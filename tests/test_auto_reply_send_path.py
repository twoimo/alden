"""Pins for the reply worker's send path, from a due draft to a durable result.

Every test loads its own worker with a private home, queue and decision store,
and replaces the worker's process seam with ``FakeCli``. No test can start a
process or open a socket: both are replaced with guards that fail the test.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKER = SCRIPTS / "auto-reply-worker.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CHAT_ID = 42
CHAT_NAME = "pin-room"
OWNER = "owner"
EPOCH = 7
INBOUND = "오늘 저녁 뭐 먹지?"
REPLY = "김치찌개 어때"
# Breaks the reaction rule: laughter the inbound message did not invite.
UNINVITED_LAUGHTER = "ㅋㅋㅋㅋ 그건 좀"
# The draft `due_draft` schedules, as the queue row carries it.
DRAFT = ("reply", "useful_reply", "information")

# The decision store only moves forward, and a terminal row never changes
# status. These mirror `reply_status_rank` and `can_apply_reply_status` in
# src/context/mod.rs.
_STATUS_RANK = {
    "pending": 0,
    "processing": 1,
    "projection_pending": 2,
    "scheduled": 3,
    "sending": 4,
    "failed": 4,
    "accepted_unconfirmed": 5,
    "sent": 6,
    "skipped": 6,
    "poison": 6,
    "delivery_unknown": 6,
    "reconcile_required": 6,
}
_TERMINAL_STATUSES = {"sent", "skipped", "poison", "delivery_unknown", "reconcile_required"}


def _can_apply(previous: str, incoming: str) -> bool:
    if _STATUS_RANK[incoming] < _STATUS_RANK[previous]:
        return False
    if previous == incoming:
        return True
    return previous not in _TERMINAL_STATUSES


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _next(scripted: object) -> object:
    """A list scripts one answer per call and repeats its last entry."""
    if isinstance(scripted, list):
        return scripted.pop(0) if len(scripted) > 1 else scripted[0]
    return scripted


class FakeCli:
    """The openkakao-cli commands the worker runs, over in-memory fakes.

    It owns the two things the worker reads back through other channels: the
    decision store (a real SQLite file the worker opens read-only) and the
    room transcript returned by ``local-read``. A model runner command is
    handed to ``runner``.
    """

    def __init__(self, context_db: Path):
        self.context_db = context_db
        self.calls: list[list[str]] = []
        self.options: list[dict] = []
        self.records: list[dict] = []
        self.transcript: list[dict] = []
        self.next_log_id = 9000
        # How each command answers. A test overrides one to script a failure.
        self.record_outcome = "applied"
        self.update_outcomes: dict[str, str] = {}
        self.preflight: object = "ready"
        self.send: object = "confirmed"
        self.before_send = None
        self.pre_send_tail = 0
        self.after_record = None
        self.runner = None
        connection = sqlite3.connect(context_db)
        connection.execute(
            """
            CREATE TABLE reply_decisions(
                event_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                decision TEXT,
                reason TEXT,
                category TEXT,
                reply TEXT,
                sent_at TEXT
            )
            """
        )
        connection.commit()
        connection.close()
        context_db.chmod(0o600)

    def __call__(self, command, **options):
        argv = [str(part) for part in command]
        if "--model" in argv and self.runner is not None:
            return self.runner(argv[argv.index("--model") + 1], **options)
        self.calls.append(argv)
        self.options.append(options)
        verb = argv[1]
        if verb == "context-reply-record":
            return self._record(json.loads(argv[argv.index("--record") + 1]))
        if verb == "context-reply-update":
            return self._update(argv)
        if verb == "local-read":
            count = int(argv[argv.index("--count") + 1])
            return 0, json.dumps(self.transcript[-count:]).encode(), b""
        if verb == "local-send":
            if "--preflight" in argv:
                return self._answer(_next(self.preflight), argv)
            if self.before_send is not None:
                self.before_send(argv)
            # The newest row local-send sees before it touches the composer,
            # as pre_send_tail_log_id computes it in src/commands/local_send.rs.
            source = options["env"].get("OPENKAKAO_EXPECTED_SOURCE_LOG_ID") or "0"
            self.pre_send_tail = max(
                [int(source)] + [row["log_id"] for row in self.transcript]
            )
            return self._answer(self.send, argv)
        raise AssertionError(f"unexpected CLI command: {argv[:2]}")

    def verbs(self) -> list[str]:
        """One word per call, in order, naming the preflight separately."""
        return ["preflight" if "--preflight" in argv else argv[1] for argv in self.calls]

    def sends(self) -> list[list[str]]:
        return [
            argv
            for argv in self.calls
            if argv[1] == "local-send" and "--preflight" not in argv
        ]

    def forget_calls(self) -> None:
        self.calls.clear()
        self.options.clear()
        self.records.clear()

    def decision(self, event_id: str) -> tuple | None:
        connection = sqlite3.connect(self.context_db)
        try:
            return connection.execute(
                "SELECT status, decision, reason, category, reply, sent_at "
                "FROM reply_decisions WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        finally:
            connection.close()

    def seed_decision(self, record: dict) -> None:
        """Store what the analysis phase records before a draft is scheduled."""
        outcome, self.record_outcome = self.record_outcome, "applied"
        try:
            self._record(record)
        finally:
            self.record_outcome = outcome
        self.records.clear()

    def post(self, message: str, *, is_self: bool, author_id: int = 700) -> int:
        self.next_log_id += 1
        self.transcript.append(
            {
                "chat_id": CHAT_ID,
                "log_id": self.next_log_id,
                "author_id": author_id,
                "sender_name": "최연우" if is_self else "member",
                "message": message,
                "message_type": 1,
                "sent_at": int(time.time()),
                "is_self": is_self,
            }
        )
        return self.next_log_id

    def _record(self, record: dict):
        self.records.append(record)
        if self.record_outcome == "failed":
            return 1, b"", b"context store unavailable"
        connection = sqlite3.connect(self.context_db)
        try:
            previous = connection.execute(
                "SELECT status FROM reply_decisions WHERE event_id = ?",
                (record["event_id"],),
            ).fetchone()
            applied = previous is None or _can_apply(previous[0], record["status"])
            if applied:
                skip = record["decision"] == "skip" and record["status"] == "skipped"
                connection.execute(
                    """
                    INSERT INTO reply_decisions(
                        event_id, status, decision, reason, category, reply, sent_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(event_id) DO UPDATE SET
                        status = excluded.status,
                        decision = excluded.decision,
                        reason = excluded.reason,
                        category = excluded.category,
                        reply = CASE WHEN ? THEN NULL
                                     ELSE COALESCE(excluded.reply, reply) END,
                        sent_at = CASE WHEN ? THEN NULL ELSE sent_at END
                    """,
                    (
                        record["event_id"],
                        record["status"],
                        record["decision"],
                        record["reason"],
                        record["category"],
                        record.get("reply"),
                        skip,
                        skip,
                    ),
                )
                connection.commit()
        finally:
            connection.close()
        if self.after_record is not None:
            self.after_record(record)
        if self.record_outcome == "lost_ack":
            return 1, b"", b"acknowledgement lost"
        return 0, json.dumps({"recorded": applied, "applied": applied}).encode(), b""

    def _update(self, argv: list[str]):
        event_id = argv[argv.index("--event-id") + 1]
        status = argv[argv.index("--status") + 1]
        reply = argv[argv.index("--reply") + 1] if "--reply" in argv else None
        sent_at = argv[argv.index("--sent-at") + 1] if "--sent-at" in argv else None
        outcome = self.update_outcomes.get(status, "applied")
        if outcome == "failed":
            return 1, b"", b"context store unavailable"
        connection = sqlite3.connect(self.context_db)
        try:
            previous = connection.execute(
                "SELECT status FROM reply_decisions WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            applied = previous is not None and _can_apply(previous[0], status)
            if applied:
                connection.execute(
                    """
                    UPDATE reply_decisions
                    SET status = ?, reply = COALESCE(?, reply),
                        sent_at = COALESCE(?, sent_at)
                    WHERE event_id = ?
                    """,
                    (status, reply, sent_at, event_id),
                )
                connection.commit()
        finally:
            connection.close()
        if outcome == "lost_ack":
            return 1, b"", b"acknowledgement lost"
        return 0, json.dumps({"applied": applied}).encode(), b""

    def _answer(self, scripted: object, argv: list[str]):
        if isinstance(scripted, BaseException):
            raise scripted
        if isinstance(scripted, tuple):
            return scripted
        chat_name = argv[2]
        unavailable = {
            "chat_name": chat_name,
            "status": "pre_send_unavailable",
            "mutation_started": False,
            "confirmed": False,
            "network": False,
        }
        unconfirmed = {
            "chat_name": chat_name,
            "status": "accepted_unconfirmed",
            "confirmed": False,
            "network": False,
            "pre_send_tail_log_id": self.pre_send_tail,
        }
        if scripted == "ready":
            payload = {
                "chat_name": chat_name,
                "status": "preflight_ready",
                "preflight_ready": True,
                "will_send": False,
                "network": False,
            }
        elif scripted == "composer_occupied":
            payload = {**unavailable, "reason": "composer_occupied"}
        elif scripted == "pre_send_unavailable":
            payload = unavailable
        elif scripted == "confirmed":
            payload = {
                "chat_name": chat_name,
                "status": "confirmed_local_db",
                "confirmed": True,
                "confirmation_log_id": self.post(argv[3], is_self=True, author_id=1),
                "network": False,
            }
        elif scripted == "accepted_unconfirmed":
            payload = unconfirmed
        elif scripted == "posted_but_unconfirmed":
            # The room shows the reply, but the CLI gave up waiting for it.
            self.post(argv[3], is_self=True, author_id=1)
            payload = unconfirmed
        elif scripted == "posted_twice_but_unconfirmed":
            # KakaoTalk delivered the reply twice; the CLI rejects a duplicate.
            self.post(argv[3], is_self=True, author_id=1)
            self.post(argv[3], is_self=True, author_id=1)
            payload = unconfirmed
        elif isinstance(scripted, dict):
            payload = scripted
        else:
            raise AssertionError(f"unscripted CLI answer: {scripted!r}")
        return 0, json.dumps(payload).encode(), b""


class SendPathCase(unittest.TestCase):
    """A worker with one room, one queue and fake external adapters."""

    maxDiff = None
    extra_environment: dict[str, str] = {}

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="send-path-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.root.chmod(0o700)
        home = self.root / "home"
        home.mkdir(mode=0o700)
        self.state_root = self.root / "state"
        self.room = self.state_root / "rooms" / str(CHAT_ID)
        self.room.mkdir(parents=True, mode=0o700)
        binary = self.root / "openkakao-cli"
        binary.write_bytes(b"")
        binary.chmod(0o600)
        self.db_state_path = self.root / "db-watch-state.json"
        self.environment = {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(self.root),
            "OPENKAKAO_BINARY": str(binary),
            "OPENKAKAO_TARGET_CHAT_NAME": CHAT_NAME,
            "OPENKAKAO_TARGET_CHAT_ID": str(CHAT_ID),
            "OPENKAKAO_DB_SOURCE_EPOCH": str(EPOCH),
            "OPENKAKAO_SUPERVISOR_OWNER": OWNER,
            "OPENKAKAO_DB_MODE": "database_authoritative",
            "OPENKAKAO_DB_AUTHORITATIVE": "1",
            "OPENKAKAO_AUTO_REPLY_ENABLED": "1",
            "OPENKAKAO_AUTO_REPLY_CLI": "1",
            "OPENKAKAO_REPLY_STATE": str(self.room / "reply-state.json"),
            "OPENKAKAO_CONTEXT_DB": str(self.root / "context.sqlite3"),
            "OPENKAKAO_MODEL_CIRCUIT_DB": str(self.room / "model-circuit.sqlite3"),
            "OPENKAKAO_DB_WATCH_STATE": str(self.db_state_path),
            "OPENKAKAO_SUPERVISOR_STATUS": str(self.root / "supervisor-status.json"),
            "OPENKAKAO_RERANK_DISABLED": "1",
            **self.extra_environment,
        }
        self._start(mock.patch.dict(os.environ, self.environment, clear=True))
        spec = importlib.util.spec_from_file_location(
            f"send_path_worker_{id(self)}", WORKER
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        module = self.module

        self.cli = FakeCli(module.CONTEXT_DB)
        self.fence_calls: list[dict] = []
        self.fence_answers: list[bool] = [True]

        def fence(**expected):
            self.fence_calls.append(expected)
            return (True, ("fence-token",)) if _next(self.fence_answers) else (False, None)

        for name, replacement in {
            "_run_bounded_process": self.cli,
            "send_readiness_fence": fence,
            "privacy_attestation_current": mock.Mock(return_value=True),
            "pre_ax_delivery_probe": mock.Mock(return_value={"result": "ready"}),
            "_model_endpoint_reachable": mock.Mock(return_value=False),
        }.items():
            self._start(mock.patch.object(module, name, replacement))
        for owner, name, message in (
            (subprocess, "Popen", "a send-path pin started a process"),
            (socket.socket, "connect", "a send-path pin opened a socket"),
        ):
            self._start(
                mock.patch.object(owner, name, side_effect=AssertionError(message))
            )

        module.transition_journal.open_queue(
            module.QUEUE, create=True, expected_chat_id=CHAT_ID
        ).close()
        self.connection = module._queue_connection()
        self.addCleanup(self.connection.close)

    def _start(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def inbound(self, log_id: int, message: str = INBOUND, **overrides) -> dict:
        event = {
            "envelope_version": 1,
            "event_type": "local_db_message",
            "method": "local_db",
            "direction": "incoming",
            "source": "database",
            "source_epoch": EPOCH,
            "owner_id": OWNER,
            "chat_id": CHAT_ID,
            "chat_name": CHAT_NAME,
            "log_id": log_id,
            "author_id": 700,
            "author_nickname": "member",
            "is_self": False,
            "reply_authorized": True,
            "message": message,
            "message_type": 1,
            "attachment": "",
            "sent_at": int(time.time()),
            "event_id": f"db:{CHAT_ID}:{log_id}",
            "canonical_event_id": f"db:{CHAT_ID}:{log_id}",
            "burst_tail_log_id": log_id,
            "recent_messages": [],
        }
        event.update(overrides)
        return event

    def two_part_inbound(self, log_id: int, first: str, second: str, **overrides) -> dict:
        """The tail of a two-message burst from one author."""
        event = self.inbound(log_id, second, **overrides)
        row = {
            "chat_id": CHAT_ID,
            "author_id": event["author_id"],
            "author_nickname": event["author_nickname"],
            "message_type": 1,
            "attachment": False,
        }
        event["recent_messages"] = [
            {**row, "log_id": log_id - 1, "message": first, "sent_at": event["sent_at"] - 2},
            {**row, "log_id": log_id, "message": second, "sent_at": event["sent_at"]},
        ]
        return event

    def observe_room_tail(self, log_id: int) -> None:
        """Publish the watcher's stable watermark for the room."""
        self.db_state_path.write_text(
            json.dumps(
                {
                    "schema_version": self.module.CLI_DB_STATE_SCHEMA_VERSION,
                    "target_chat_id": CHAT_ID,
                    "target_chat_name": CHAT_NAME,
                    "owner_id": OWNER,
                    "source_epoch": EPOCH,
                    "last_observed_log_id": log_id,
                }
            ),
            encoding="utf-8",
        )

    def claimed_inbound(self, event: dict) -> dict:
        """Queue `event` and claim it the way the worker loop does."""
        self.observe_room_tail(event["log_id"])
        self.assertTrue(self.module.enqueue_event(event))
        job, previous = self.module.claim_job(time.time() + 3600.0, self.connection)
        self.assertEqual(previous, "pending")
        self.cli.forget_calls()
        return job

    def due_draft(self, event: dict, reply: str, **event_fields) -> dict:
        """Queue `event`, schedule `reply` for it, and claim the due draft.

        The row goes through the worker's own transitions, so it carries the
        journal a real scheduled job has when its delay elapses.
        """
        module = self.module
        job = self.claimed_inbound(event)
        scheduled = json.loads(job["event_json"])
        scheduled.update(
            analysis_watermark_log_id=event["log_id"],
            response_window_upper_seconds=600.0,
        )
        scheduled.update(event_fields)
        decision, reason, category = DRAFT
        self.assertTrue(
            module.settle_processing_transition(
                scheduled,
                event["event_id"],
                self.connection,
                status="scheduled",
                event_json=json.dumps(scheduled, ensure_ascii=False),
                due_at=time.time(),
                decision=decision,
                reason=reason,
                category=category,
                reply=reply,
                scheduled_delay_seconds=3.0,
            )
        )
        self.cli.seed_decision(
            {
                "event_id": event["event_id"],
                "status": "scheduled",
                "decision": decision,
                "reason": reason,
                "category": category,
                "reply": reply,
            }
        )
        job, previous = module.claim_job(time.time() + 1.0, self.connection)
        self.assertEqual(previous, "scheduled")
        self.cli.forget_calls()
        return job

    def already_sent(self, log_id: int, reply: str) -> None:
        """Add an earlier job of this room whose reply was delivered just now."""
        now = time.time()
        self.connection.execute(
            """
            INSERT INTO reply_jobs(
                event_id, event_json, status, decision, reason, category,
                reply, created_at, updated_at
            ) VALUES (?, ?, 'sent', 'reply', 'useful_reply', 'information', ?, ?, ?)
            """,
            (
                f"db:{CHAT_ID}:{log_id}",
                json.dumps(self.inbound(log_id, "앞선 질문")),
                reply,
                now,
                now,
            ),
        )
        self.connection.commit()

    def change_queue_row(self, statement: str, *parameters) -> None:
        """Write to the queue as another actor would, on its own connection."""
        writer = sqlite3.connect(self.module.QUEUE)
        try:
            writer.execute(statement, parameters)
            writer.commit()
        finally:
            writer.close()

    def journal_other_messages(self, count: int) -> None:
        """Journal `count` other room messages the way db-watch does."""
        writer = sqlite3.connect(self.module.QUEUE)
        try:
            writer.executemany(
                "INSERT INTO pipeline_transitions(schema_version, event_id, "
                "attempt_no, component, from_state, to_state, code, source_epoch, "
                "occurred_at_ns) VALUES (1, ?, 0, 'ingress', 'detected', 'hooking', "
                "'candidate_persisted', ?, ?)",
                [
                    (f"db:{CHAT_ID}:{100_000 + n}", EPOCH, time.time_ns())
                    for n in range(count)
                ],
            )
            writer.commit()
        finally:
            writer.close()

    def supersede(self, event_id: str, successor_log_id: int) -> None:
        """Record that a newer message of the same burst replaced this job."""
        self.change_queue_row(
            "INSERT INTO reply_job_supersessions("
            "event_id, superseded_by_event_id, created_at) VALUES (?, ?, ?)",
            event_id,
            f"db:{CHAT_ID}:{successor_log_id}",
            time.time(),
        )

    def outcome(self, event_id: str) -> tuple:
        """The queue row's projection, in column order."""
        return tuple(
            self.connection.execute(
                "SELECT status, decision, reason, category, reply, "
                "scheduled_delay_seconds, error_class "
                "FROM reply_jobs WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        )

    def status(self, event_id: str) -> str:
        return self.outcome(event_id)[0]

    def due_at(self, event_id: str) -> float | None:
        return self.connection.execute(
            "SELECT due_at FROM reply_jobs WHERE event_id = ?", (event_id,)
        ).fetchone()["due_at"]

    def stored_event(self, event_id: str) -> dict:
        row = self.connection.execute(
            "SELECT event_json FROM reply_jobs WHERE event_id = ?", (event_id,)
        ).fetchone()
        return json.loads(row["event_json"])

    def transitions(self, event_id: str) -> list[tuple]:
        return [
            tuple(row)
            for row in self.connection.execute(
                "SELECT component, from_state, to_state, code "
                "FROM pipeline_transitions WHERE event_id = ? ORDER BY seq",
                (event_id,),
            )
        ]

    def reply_state(self) -> dict:
        return json.loads(self.module.STATE.read_text(encoding="utf-8"))

    def assert_closed_without_a_send(self, event_id: str) -> None:
        """The event is finished and nothing is on record as sent or attempted."""
        self.assertEqual(
            self.reply_state(), {"inflight_claims": {}, "last_event": event_id}
        )

    def assert_recorded_as_sent(self, event_id: str, reply: str) -> None:
        state = self.reply_state()
        self.assertEqual(
            (state["last_event"], state["last_sent_digest"], state["last_delivery_confirmation"]),
            (event_id, _digest(reply), "local_db_outgoing_row"),
        )
        self.assertNotIn("delivery_state", state)

    def assert_left_for_reconciliation(self, event_id: str, attempted_reply: str) -> None:
        state = self.reply_state()
        self.assertEqual(
            (state["last_event"], state["delivery_state"], state["last_attempted_reply_digest"]),
            (event_id, "delivery_unknown", _digest(attempted_reply)),
        )
        self.assertNotIn("last_sent_digest", state)

    def process(self, job: dict, previous_status: str = "scheduled") -> str:
        """Run one claimed job and return what the worker logged."""
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            self.module.process_job(job, previous_status, self.connection)
        return captured.getvalue()

    def send_reply(self, reply: str, event: dict):
        """Call the send boundary directly; returns (sent, holds, log)."""
        holds: list[str] = []
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            sent = self.module.send_reply(
                reply,
                event=event,
                event_id=event["event_id"],
                connection=self.connection,
                expected_target_chat_id=CHAT_ID,
                expected_owner=OWNER,
                expected_epoch=EPOCH,
                hold_out=holds,
            )
        return sent, holds, captured.getvalue()


class DueDraftDeliveryTests(SendPathCase):
    """A draft is recorded as sent only after the room confirms it."""

    def test_confirmed_send_is_persisted_after_the_room_shows_the_reply(self):
        event = self.inbound(501)
        job = self.due_draft(event, REPLY)
        seen_at_send = []
        self.cli.before_send = lambda _argv: seen_at_send.append(
            (self.status(event["event_id"]), self.cli.decision(event["event_id"])[0])
        )

        self.process(job)

        # The queue fences the job as `sending` before the composing command
        # runs, and the decision store still says nothing was sent.
        self.assertEqual(seen_at_send, [("sending", "scheduled")])
        self.assertEqual(
            self.cli.verbs(),
            [
                "local-read",
                "preflight",
                "local-send",
                "context-reply-update",
                "local-read",
                "context-reply-update",
            ],
        )
        self.assertEqual(self.outcome(event["event_id"]), ("sent", *DRAFT, REPLY, 3.0, None))
        self.assertIsNone(self.due_at(event["event_id"]))
        status, decision, _reason, _category, reply, sent_at = self.cli.decision(
            event["event_id"]
        )
        self.assertEqual((status, decision, reply), ("sent", "reply", REPLY))
        self.assertTrue(sent_at)
        self.assert_recorded_as_sent(event["event_id"], REPLY)
        self.assertEqual(
            self.transitions(event["event_id"])[-7:],
            [
                ("pre_send", "processing", "ready", "pre_send_check"),
                ("queue", "processing", "sending", "status_changed"),
                ("ax", "ready", "sending", "ax_mutation_authorized"),
                ("ax", "sending", "sending", "local_db_confirmed"),
                ("queue", "sending", "sent", "status_changed"),
                ("projection", "sending", "sent", "projection_written"),
                ("terminal", "sending", "sent", "terminal_committed"),
            ],
        )
        (receipt,) = [
            json.loads(line)
            for line in self.module.EVIDENCE_LEDGER.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            (receipt["event_id"], receipt["status"], receipt["reply"]),
            (event["event_id"], "sent", REPLY),
        )
        self.assertEqual(receipt["outgoing_log_id"], self.cli.transcript[-1]["log_id"])

    def test_reply_already_in_the_room_is_recorded_without_a_second_send(self):
        event = self.inbound(502)
        job = self.due_draft(event, REPLY)
        self.cli.post(REPLY, is_self=True, author_id=1)

        self.process(job)

        self.assertEqual(self.cli.verbs(), ["local-read"])
        self.assertEqual(self.outcome(event["event_id"]), ("sent", *DRAFT, REPLY, 3.0, None))
        self.assert_recorded_as_sent(event["event_id"], REPLY)
        # Only the queue learns about it: the decision store keeps the draft.
        self.assertEqual(self.cli.decision(event["event_id"])[0], "scheduled")
        self.assertNotIn(
            ("ax", "ready", "sending", "ax_mutation_authorized"),
            self.transitions(event["event_id"]),
        )

    def test_unconfirmed_send_becomes_delivery_unknown_and_is_never_retried(self):
        event = self.inbound(503)
        job = self.due_draft(event, REPLY)
        self.cli.send = "accepted_unconfirmed"

        self.process(job)

        self.assertEqual(
            self.cli.verbs(),
            ["local-read", "preflight", "local-send", "local-read", "context-reply-update"],
        )
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "delivery_unknown"),
        )
        self.assertEqual(self.cli.decision(event["event_id"])[0], "delivery_unknown")
        self.assert_left_for_reconciliation(event["event_id"], REPLY)
        self.assertEqual(
            self.transitions(event["event_id"])[-2:],
            [
                ("queue", "sending", "delivery_unknown", "status_changed"),
                ("terminal", "sending", "delivery_unknown", "terminal_committed"),
            ],
        )
        self.assertIsNone(self.module.claim_job(time.time() + 86400.0, self.connection))
        self.assertEqual(len(self.cli.sends()), 1)

    def test_unconfirmed_send_is_never_retried_after_the_journal_rolls_over(self):
        event = self.inbound(509)
        job = self.due_draft(event, REPLY)
        self.cli.send = "accepted_unconfirmed"
        self.process(job)
        self.assertEqual(self.status(event["event_id"]), "delivery_unknown")

        self.journal_other_messages(self.module.transition_journal.JOURNAL_MAX_ROWS)
        self.assertEqual(self.transitions(event["event_id"]), [])

        # What worker_main does while it is fenced: recover, give up, claim.
        self.cli.send = "confirmed"
        for _ in range(3):
            self.module.recover_stale_jobs(self.connection)
            self.module._auto_reconcile_give_up(self.connection, time.time())
            claimed = self.module.claim_job(time.time() + 86400.0, self.connection)
            if claimed is not None:
                self.process(*claimed)

        self.assertEqual(len(self.cli.sends()), 1)
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "delivery_unknown"),
        )
        self.assertGreater(self.module._queue_reconciliation_blockers(self.connection), 0)

    def test_send_the_cli_could_not_confirm_is_adopted_from_the_room_transcript(self):
        event = self.inbound(504)
        job = self.due_draft(event, REPLY)
        self.cli.send = "posted_but_unconfirmed"

        self.process(job)

        self.assertEqual(
            self.cli.verbs(),
            [
                "local-read",
                "preflight",
                "local-send",
                "local-read",
                "context-reply-update",
                "local-read",
                "context-reply-update",
            ],
        )
        self.assertEqual(self.status(event["event_id"]), "sent")
        self.assertEqual(self.cli.decision(event["event_id"])[0], "sent")
        self.assert_recorded_as_sent(event["event_id"], REPLY)
        self.assertIn(
            ("ax", "sending", "sending", "local_db_confirmed"),
            self.transitions(event["event_id"]),
        )

    def test_matching_row_the_account_wrote_before_the_send_is_not_its_proof(self):
        event = self.inbound(510)
        job = self.due_draft(event, REPLY)
        # The account types the same words on another device just before Return.
        self.cli.before_send = lambda _argv: self.cli.post(REPLY, is_self=True, author_id=1)
        self.cli.send = "accepted_unconfirmed"

        self.process(job)

        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "delivery_unknown"),
        )
        self.assertNotIn(
            ("ax", "sending", "sending", "local_db_confirmed"),
            self.transitions(event["event_id"]),
        )
        self.assert_left_for_reconciliation(event["event_id"], REPLY)

    def test_reply_that_landed_twice_is_not_confirmed(self):
        event = self.inbound(511)
        job = self.due_draft(event, REPLY)
        self.cli.send = "posted_twice_but_unconfirmed"

        self.process(job)

        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "delivery_unknown"),
        )
        self.assertNotIn(
            ("ax", "sending", "sending", "local_db_confirmed"),
            self.transitions(event["event_id"]),
        )
        self.assert_left_for_reconciliation(event["event_id"], REPLY)

    def test_send_refused_before_composing_is_retried_inside_the_window(self):
        event = self.inbound(505)
        job = self.due_draft(event, REPLY)
        self.cli.send = "pre_send_unavailable"
        before = time.time()

        self.process(job)

        self.assertEqual(self.cli.verbs(), ["local-read", "preflight", "local-send"])
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("scheduled", *DRAFT, REPLY, 3.0, "pre_send_unavailable"),
        )
        retry_at = self.due_at(event["event_id"])
        self.assertGreaterEqual(retry_at, before + self.module.MODEL_MIN_DEFER_SECONDS)
        self.assertLessEqual(retry_at, time.time() + self.module.MODEL_MIN_DEFER_SECONDS)
        self.assertEqual(self.cli.decision(event["event_id"])[0], "scheduled")
        self.assertFalse(self.module.STATE.exists())
        self.assertEqual(
            self.transitions(event["event_id"])[-6:],
            [
                ("queue", "processing", "sending", "status_changed"),
                ("ax", "ready", "sending", "ax_mutation_authorized"),
                ("queue", "sending", "processing", "status_changed"),
                ("pre_send", "sending", "processing", "pre_send_check"),
                ("queue", "processing", "scheduled", "status_changed"),
                ("delay", "processing", "scheduled", "delay_scheduled"),
            ],
        )
        _job, previous = self.module.claim_job(retry_at + 1.0, self.connection)
        self.assertEqual(previous, "scheduled")

    def test_sent_is_not_persisted_when_the_sending_audit_fails(self):
        event = self.inbound(506)
        job = self.due_draft(event, REPLY)
        self.cli.update_outcomes["sending"] = "failed"

        self.process(job)

        self.assertEqual(
            self.cli.verbs(),
            ["local-read", "preflight", "local-send", "context-reply-update"],
        )
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "reconcile_required"),
        )
        self.assertEqual(self.cli.decision(event["event_id"])[0], "scheduled")
        self.assert_left_for_reconciliation(event["event_id"], REPLY)

    def test_sent_audit_with_a_lost_acknowledgement_is_read_back(self):
        event = self.inbound(507)
        job = self.due_draft(event, REPLY)
        self.cli.update_outcomes["sent"] = "lost_ack"

        self.process(job)

        self.assertEqual(self.outcome(event["event_id"]), ("sent", *DRAFT, REPLY, 3.0, None))
        self.assertEqual(self.cli.decision(event["event_id"])[0], "sent")
        self.assert_recorded_as_sent(event["event_id"], REPLY)
        self.assertEqual(
            self.transitions(event["event_id"])[-4:],
            [
                ("queue", "sending", "sent", "status_changed"),
                ("recovery", "sending", "sent", "reconciled"),
                ("projection", "sending", "sent", "projection_written"),
                ("terminal", "sending", "sent", "terminal_committed"),
            ],
        )

    def test_sent_audit_that_never_lands_becomes_delivery_unknown(self):
        event = self.inbound(508)
        job = self.due_draft(event, REPLY)
        self.cli.update_outcomes["sent"] = "failed"

        self.process(job)

        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "reconcile_required"),
        )
        self.assertEqual(self.cli.decision(event["event_id"])[0], "sending")
        self.assert_left_for_reconciliation(event["event_id"], REPLY)


class ProvenSkipTests(SendPathCase):
    """A job is closed as skipped only after its no-send audit is durable.

    Each closing site keeps its own queue projection, so the row left behind
    by one site is not the row left behind by another.
    """

    def audit(self) -> tuple:
        """What the single skip record told the decision store."""
        (record,) = self.cli.records
        return (
            record["event_id"],
            record["status"],
            record["decision"],
            record["reason"],
            record["category"],
            record["reply"],
            record["message"],
        )

    def test_empty_draft_is_audited_then_skipped(self):
        event = self.inbound(601)
        job = self.due_draft(event, "")
        reason = "empty_scheduled_reply"

        self.process(job)

        self.assertEqual(self.cli.verbs(), ["context-reply-record"])
        self.assertEqual(
            self.audit(),
            (event["event_id"], "skipped", "skip", reason, "uncertain", None, INBOUND),
        )
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("skipped", "skip", reason, "uncertain", None, None, reason),
        )
        self.assertEqual(
            self.cli.decision(event["event_id"]),
            ("skipped", "skip", reason, "uncertain", None, None),
        )
        # The stored event is left as it was scheduled.
        self.assertEqual(
            self.stored_event(event["event_id"])["analysis_watermark_log_id"], 601
        )
        self.assert_closed_without_a_send(event["event_id"])
        self.assertEqual(
            self.transitions(event["event_id"])[-3:],
            [
                ("queue", "processing", "skipped", "status_changed"),
                ("projection", "processing", "skipped", "projection_written"),
                ("terminal", "processing", "skipped", "terminal_committed"),
            ],
        )

    def test_skip_is_accepted_when_only_the_audit_acknowledgement_is_lost(self):
        event = self.inbound(602)
        job = self.due_draft(event, "")
        self.cli.record_outcome = "lost_ack"

        self.process(job)

        self.assertEqual(self.status(event["event_id"]), "skipped")
        self.assert_closed_without_a_send(event["event_id"])

    def test_skip_without_a_durable_audit_becomes_delivery_unknown(self):
        event = self.inbound(603)
        job = self.due_draft(event, "")
        self.cli.record_outcome = "failed"
        reason = "empty_scheduled_reply"

        self.process(job)

        self.assertEqual(self.cli.verbs(), ["context-reply-record", "context-reply-update"])
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", "skip", reason, "uncertain", None, None, "reconcile_required"),
        )
        self.assertEqual(self.cli.decision(event["event_id"])[0], "delivery_unknown")
        self.assert_left_for_reconciliation(event["event_id"], "")
        self.assertEqual(
            self.transitions(event["event_id"])[-2:],
            [
                ("queue", "processing", "delivery_unknown", "status_changed"),
                ("terminal", "processing", "delivery_unknown", "terminal_committed"),
            ],
        )

    def test_skip_without_a_durable_audit_stays_unknown_without_a_queue_connection(self):
        event = self.inbound(605)
        job = self.due_draft(event, "")
        self.cli.record_outcome = "failed"

        # A caller without its own queue connection writes the row unguarded.
        with contextlib.redirect_stderr(io.StringIO()):
            self.module.process_job(job, "scheduled", None)

        self.assertEqual(self.status(event["event_id"]), "delivery_unknown")
        self.assert_left_for_reconciliation(event["event_id"], "")

    def test_skip_that_loses_its_queue_row_is_left_for_reconciliation(self):
        event = self.inbound(604)
        job = self.due_draft(event, "")
        # Another actor moves the row while the audit is being written.
        self.cli.after_record = lambda _record: self.change_queue_row(
            "UPDATE reply_jobs SET status = 'sending' WHERE event_id = ?",
            event["event_id"],
        )

        self.process(job)

        self.assertEqual(self.status(event["event_id"]), "delivery_unknown")
        self.assert_left_for_reconciliation(event["event_id"], "")

    def test_draft_that_fails_policy_is_skipped_with_the_rejecting_rule(self):
        for log_id, draft, rule in (
            (611, "가" * 230, "policy_too_long"),
            (612, UNINVITED_LAUGHTER, "policy_reaction_mismatch"),
        ):
            with self.subTest(rule=rule):
                event = self.inbound(log_id)
                job = self.due_draft(event, draft)

                self.process(job)

                self.assertEqual(self.cli.verbs(), ["local-read", "context-reply-record"])
                self.assertEqual(
                    self.outcome(event["event_id"]),
                    ("skipped", "skip", rule, "policy", None, None, rule),
                )
                self.assertEqual(
                    self.cli.decision(event["event_id"]),
                    ("skipped", "skip", rule, "policy", None, None),
                )
                self.assert_closed_without_a_send(event["event_id"])

    def test_lenient_fallback_draft_is_held_to_the_light_rules_only(self):
        event = self.inbound(613)
        job = self.due_draft(event, "가" * 230, rerank_fallback="lenient_policy")
        rule = "policy_lenient_violation"

        self.process(job)

        self.assertEqual(
            self.outcome(event["event_id"]),
            ("skipped", "skip", rule, "policy", None, None, rule),
        )
        self.assert_closed_without_a_send(event["event_id"])

    def test_lenient_fallback_draft_still_faces_the_send_boundary_rules(self):
        event = self.inbound(614)
        job = self.due_draft(event, UNINVITED_LAUGHTER, rerank_fallback="lenient_policy")

        self.process(job)

        # The light rules let it through, the send boundary refuses it before
        # any command, and the draft is put back to wait.
        self.assertEqual(self.cli.verbs(), ["local-read"])
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("scheduled", *DRAFT, UNINVITED_LAUGHTER, 3.0, "pre_send_unavailable"),
        )
        self.assertFalse(self.module.STATE.exists())

    def test_second_reply_to_one_burst_is_skipped_at_the_send_boundary(self):
        event = self.inbound(621, sent_at=int(time.time()) - 30)
        job = self.due_draft(event, REPLY)
        self.already_sent(620, "먼저 보낸 답")
        reason = "already_commented"

        self.process(job)

        self.assertEqual(self.cli.verbs(), ["local-read", "context-reply-record"])
        # This site keeps the draft and its delay on the skipped row.
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("skipped", "skip", reason, "policy", REPLY, 3.0, reason),
        )
        self.assertEqual(
            self.cli.decision(event["event_id"]),
            ("skipped", "skip", reason, "policy", None, None),
        )
        self.assert_closed_without_a_send(event["event_id"])

    def test_repeat_hold_without_a_durable_audit_keeps_the_draft_unprojected(self):
        event = self.inbound(622, sent_at=int(time.time()) - 30)
        job = self.due_draft(event, REPLY)
        self.already_sent(620, "먼저 보낸 답")
        self.cli.record_outcome = "failed"

        self.process(job)

        # Unlike the other sites, the queue row is not projected as a skip.
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "reconcile_required"),
        )
        self.assert_left_for_reconciliation(event["event_id"], "")

    def _hold_from_send(self, reason: str):
        def refuse(_reply, *, hold_out, **_boundary):
            hold_out.append(reason)
            return False

        return mock.patch.object(self.module, "send_reply", side_effect=refuse)

    def test_hold_reported_by_the_send_boundary_closes_the_job_by_category(self):
        for log_id, reason, category in (
            (631, "similar_recent_self", "policy"),
            (632, "send_allowlist_rejected", "configuration"),
        ):
            with self.subTest(reason=reason):
                event = self.inbound(log_id)
                job = self.due_draft(event, REPLY)

                with self._hold_from_send(reason):
                    self.process(job)

                self.assertEqual(
                    self.outcome(event["event_id"]),
                    ("skipped", "skip", reason, category, None, None, reason),
                )
                self.assertEqual(
                    self.cli.decision(event["event_id"]),
                    ("skipped", "skip", reason, category, None, None),
                )
                self.assert_closed_without_a_send(event["event_id"])

    def test_send_hold_without_a_durable_audit_keeps_the_draft_for_reconciliation(self):
        event = self.inbound(633)
        job = self.due_draft(event, REPLY)
        self.cli.record_outcome = "failed"

        with self._hold_from_send("send_allowlist_rejected"):
            self.process(job)

        self.assertEqual(
            self.outcome(event["event_id"]),
            ("delivery_unknown", *DRAFT, REPLY, 3.0, "send_allowlist_rejected"),
        )
        self.assert_left_for_reconciliation(event["event_id"], REPLY)

    def test_context_change_reported_by_the_send_boundary_requeues_for_analysis(self):
        event = self.inbound(634)
        job = self.due_draft(event, REPLY)
        reason = "conversation_context_changed"

        with self._hold_from_send(reason):
            self.process(job)

        self.assertEqual(self.cli.verbs(), ["local-read"])
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("pending", None, reason, "uncertain", None, None, reason),
        )
        stored = self.stored_event(event["event_id"])
        self.assertTrue(stored["context_refresh_required"])
        self.assertNotIn("analysis_watermark_log_id", stored)
        self.assertFalse(self.module.STATE.exists())

    def test_proactive_post_from_a_closed_slot_is_skipped(self):
        event = self.inbound(
            640,
            "긱뉴스 소식",
            proactive=True,
            proactive_query="geeknews-rss",
            proactive_source_log_id=640,
            event_id=f"db:{CHAT_ID}:1794758000",
            canonical_event_id=f"db:{CHAT_ID}:1794758000",
        )
        job = self.claimed_inbound(event)

        with mock.patch.object(
            self.module, "_proactive_unknown_is_closed_slot", return_value=True
        ):
            self.process(job, "pending")

        self.assertEqual(self.cli.verbs(), ["context-reply-record"])
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("skipped", "skip", "stale_backlog", "policy", None, None, "stale_backlog"),
        )
        self.assert_closed_without_a_send(event["event_id"])

    def test_expired_draft_is_skipped_and_its_stored_event_is_scrubbed(self):
        event = self.two_part_inbound(
            651, "첫 줄", "둘째 줄", sent_at=int(time.time()) - 5000
        )
        job = self.due_draft(event, REPLY, media_marker="/tmp/left-over-marker")

        self.process(job)

        self.assertEqual(self.cli.verbs(), ["context-reply-record"])
        # The audit names the whole burst, not only its last message.
        self.assertEqual(
            self.audit(),
            (event["event_id"], "skipped", "skip", "stale_backlog", "policy", None, "첫 줄\n둘째 줄"),
        )
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("skipped", "skip", "stale_backlog", "policy", None, None, None),
        )
        stored = self.stored_event(event["event_id"])
        self.assertEqual(
            (stored["image_path"], stored["image_paths"], stored["media_marker"]),
            ("", [], ""),
        )
        self.assertIsNone(stored["media_manifest"])
        self.assert_closed_without_a_send(event["event_id"])

    def _held_turn(self, log_id: int) -> tuple[dict, dict]:
        """A claimed two-part burst whose author is not allowed to be answered."""
        event = self.two_part_inbound(log_id, "첫 줄", "둘째 줄")
        job = self.claimed_inbound(event)
        held = json.loads(job["event_json"])
        # The whole burst changes author, so it still reads as one burst.
        for row in (held, *held["recent_messages"]):
            row["author_nickname"] = "공지봇"
        held["media_marker"] = "/tmp/left-over-marker"
        self.assertEqual(
            self.module._coalesced_burst_event(held)["message"], "첫 줄\n둘째 줄"
        )
        return held, {**job, "event_json": json.dumps(held, ensure_ascii=False)}

    def test_turn_hold_is_audited_as_the_last_message_and_scrubs_the_stored_event(self):
        held, job = self._held_turn(661)
        reason = "author_not_allowlisted"

        log = self.process(job, "pending")

        self.assertEqual(self.cli.verbs(), ["context-reply-record"])
        self.assertEqual(
            self.audit(),
            (held["event_id"], "skipped", "skip", reason, "policy", None, "둘째 줄"),
        )
        self.assertEqual(
            self.outcome(held["event_id"]),
            ("skipped", "skip", reason, "policy", None, None, None),
        )
        stored = self.stored_event(held["event_id"])
        self.assertEqual((stored["author_nickname"], stored["media_marker"]), ("공지봇", ""))
        self.assertIn(f"[reply-turn] skip={reason} chat_id={CHAT_ID} log_id=661", log)
        self.assert_closed_without_a_send(held["event_id"])

    def test_turn_hold_without_a_durable_audit_projects_the_skip_onto_delivery_unknown(self):
        held, job = self._held_turn(662)
        self.cli.record_outcome = "failed"
        reason = "author_not_allowlisted"

        self.process(job, "pending")

        self.assertEqual(
            self.outcome(held["event_id"]),
            ("delivery_unknown", "skip", reason, "policy", None, None, "reconcile_required"),
        )
        self.assertEqual(self.stored_event(held["event_id"])["media_marker"], "")
        self.assertIsNone(self.cli.decision(held["event_id"]))
        self.assert_left_for_reconciliation(held["event_id"], "")

    def test_conversation_advanced_skip_audits_the_whole_burst(self):
        event = self.two_part_inbound(671, "첫 줄", "둘째 줄")
        job = self.claimed_inbound(event)
        queued = json.loads(job["event_json"])
        reason = "conversation_advanced"

        self.module.finish_turn_policy_skip(
            queued, event["event_id"], self.connection, reason
        )

        self.assertEqual(
            self.audit(),
            (event["event_id"], "skipped", "skip", reason, "policy", None, "첫 줄\n둘째 줄"),
        )
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("skipped", "skip", reason, "policy", None, None, None),
        )
        self.assert_closed_without_a_send(event["event_id"])

    def test_job_superseded_while_it_is_skipped_is_closed_as_superseded(self):
        event = self.inbound(681)
        job = self.due_draft(event, "")
        self.cli.after_record = lambda record: (
            self.supersede(event["event_id"], 682)
            if record["reason"] == "empty_scheduled_reply"
            else None
        )

        self.process(job)

        self.assertEqual(
            [record["reason"] for record in self.cli.records],
            ["empty_scheduled_reply", "burst_superseded"],
        )
        self.assertEqual(
            self.outcome(event["event_id"]),
            ("skipped", "skip", "burst_superseded", "duplicate", None, None, None),
        )
        self.assertEqual(
            self.cli.decision(event["event_id"]),
            ("skipped", "skip", "burst_superseded", "duplicate", None, None),
        )
        self.assert_closed_without_a_send(event["event_id"])


class SendBoundaryTests(SendPathCase):
    """`send_reply` composes only for the enrolled room, once, and reports
    success only for a reply the local database shows."""

    def test_draft_that_breaks_reply_policy_is_refused_before_any_command(self):
        event = self.inbound(701)
        self.due_draft(event, REPLY)

        sent, holds, _log = self.send_reply(UNINVITED_LAUGHTER, event)

        self.assertFalse(sent)
        self.assertEqual((holds, self.cli.calls, self.fence_calls), ([], [], []))
        self.assertEqual(self.status(event["event_id"]), "processing")

    def test_author_outside_the_allowlist_is_refused_before_any_command(self):
        event = self.inbound(702)
        self.due_draft(event, REPLY)
        for label, changed in (
            ("the account owner", {"is_self": True}),
            ("a bot", {"author_nickname": "공지봇"}),
            ("an unverifiable author", {"author_id": None}),
        ):
            with self.subTest(author=label):
                sent, holds, _log = self.send_reply(REPLY, {**event, **changed})

                self.assertFalse(sent)
                self.assertEqual((holds, self.cli.calls, self.fence_calls), ([], [], []))

    def test_closed_readiness_fence_is_refused_before_any_command(self):
        event = self.inbound(703)
        self.due_draft(event, REPLY)
        self.fence_answers = [False]

        sent, holds, _log = self.send_reply(REPLY, event)

        self.assertFalse(sent)
        self.assertEqual((holds, self.cli.calls), ([], []))
        self.assertEqual(
            self.fence_calls,
            [
                {
                    "expected_target_chat_id": CHAT_ID,
                    "expected_owner": OWNER,
                    "expected_epoch": EPOCH,
                }
            ],
        )

    def test_fence_that_changes_during_the_preflight_stops_the_send(self):
        event = self.inbound(704)
        self.due_draft(event, REPLY)
        self.fence_answers = [True, False]

        sent, holds, _log = self.send_reply(REPLY, event)

        self.assertFalse(sent)
        self.assertEqual(holds, [])
        self.assertEqual(self.cli.verbs(), ["preflight"])
        # The second read must present the token the first read returned.
        self.assertEqual(self.fence_calls[1]["expected_token"], ("fence-token",))
        self.assertEqual(self.status(event["event_id"]), "processing")

    def test_dry_run_reports_success_without_running_a_command(self):
        event = self.inbound(705)
        self.due_draft(event, REPLY)

        with mock.patch.dict(os.environ, {"OPENKAKAO_HOOK_DRY_RUN": "1"}):
            sent, holds, _log = self.send_reply(REPLY, event)

        self.assertTrue(sent)
        self.assertEqual((holds, self.cli.calls, self.fence_calls), ([], [], []))
        self.assertEqual(self.status(event["event_id"]), "processing")

    def test_commands_target_the_enrolled_room_and_carry_the_source_identity(self):
        event = self.inbound(706)
        job = self.due_draft(event, REPLY)

        sent, holds, _log = self.send_reply(REPLY, json.loads(job["event_json"]))

        self.assertTrue(sent)
        self.assertEqual(holds, [])
        binary = str(self.module.BIN)
        common = ["--yes", "--json", "--no-prefix"]
        self.assertEqual(
            self.cli.calls,
            [
                [
                    binary,
                    "local-send",
                    CHAT_NAME,
                    "openkakao-read-only-preflight",
                    *common,
                    "--preflight",
                ],
                [binary, "local-send", CHAT_NAME, REPLY, *common, "--reply-to", INBOUND],
            ],
        )
        preflight, send = self.cli.options
        self.assertEqual((preflight["timeout"], send["timeout"]), (20.0, 60.0))
        self.assertEqual(preflight["cwd"], self.module.ROOT)
        self.assertIs(preflight["env"], send["env"])
        self.assertEqual(
            send["env"],
            {
                "HOME": self.environment["HOME"],
                "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
                "OPENKAKAO_AUTO_REPLY_WORKER": "1",
                "OPENKAKAO_AUTO_REPLY_CLI": "1",
                "OPENKAKAO_ENROLLMENT_PATH": "",
                "OPENKAKAO_ENROLLMENT_SHA256": "",
                "OPENKAKAO_DB_AUTHORITATIVE": "1",
                "OPENKAKAO_AUTO_REPLY_ENABLED": "1",
                "OPENKAKAO_DB_MODE": "database_authoritative",
                "OPENKAKAO_DB_READY": "",
                "OPENKAKAO_SUPERVISOR_OWNER": OWNER,
                "OPENKAKAO_DB_SOURCE_EPOCH": str(EPOCH),
                "OPENKAKAO_TARGET_CHAT_ID": str(CHAT_ID),
                "OPENKAKAO_TARGET_CHAT_NAME": CHAT_NAME,
                "OPENKAKAO_EXPECTED_SOURCE_LOG_ID": "706",
                "OPENKAKAO_EXPECTED_SOURCE_AUTHOR_ID": "700",
                "OPENKAKAO_EXPECTED_SOURCE_AUTHOR_NICKNAME": "member",
                "OPENKAKAO_PROACTIVE_SEND": "",
                "OPENKAKAO_SUPERVISOR_STATUS": self.environment["OPENKAKAO_SUPERVISOR_STATUS"],
                "OPENKAKAO_DB_WATCH_STATE": self.environment["OPENKAKAO_DB_WATCH_STATE"],
                "OPENKAKAO_AUTO_REPLY_LOCK": "",
                "OPENKAKAO_AUTO_REPLY_GENERATION_LOCK": "",
                "OPENKAKAO_AUTO_REPLY_SEND_LOCK": "",
                "OPENKAKAO_BINARY": self.environment["OPENKAKAO_BINARY"],
                "OPENKAKAO_CONFIG": "",
                "OPENKAKAO_PRIVACY_ATTESTATION": "",
                "OPENKAKAO_COMPOSER_ALLOWLIST": str(self.room / "composer-allowlist.json"),
            },
        )

    def test_job_that_is_already_sending_is_never_composed_again(self):
        event = self.inbound(707)
        self.due_draft(event, REPLY)
        self.change_queue_row(
            "UPDATE reply_jobs SET status = 'sending' WHERE event_id = ?",
            event["event_id"],
        )

        sent, holds, _log = self.send_reply(REPLY, event)

        self.assertFalse(sent)
        self.assertEqual(holds, [])
        self.assertEqual(self.cli.verbs(), ["preflight"])
        self.assertEqual(self.status(event["event_id"]), "sending")

    def test_superseded_job_is_held_before_it_is_composed(self):
        event = self.inbound(708)
        self.due_draft(event, REPLY)
        self.supersede(event["event_id"], 709)

        sent, holds, _log = self.send_reply(REPLY, event)

        self.assertFalse(sent)
        self.assertEqual(holds, ["burst_superseded"])
        self.assertEqual(self.cli.verbs(), ["preflight"])
        self.assertEqual(self.status(event["event_id"]), "processing")

    def test_draft_left_in_the_composer_is_sent_only_before_a_confirmation(self):
        event = self.inbound(710)
        self.due_draft(event, REPLY)
        self.cli.preflight = "composer_occupied"

        sent, _holds, _log = self.send_reply(REPLY, event)

        # No confirmation is on record, so the draft in the composer may be
        # this job's own unsent text and the send goes ahead.
        self.assertTrue(sent)
        self.assertEqual(self.cli.verbs(), ["preflight", "local-send"])

        confirmed = self.inbound(711)
        self.due_draft(confirmed, REPLY)
        self.module._journal_checkpoint(
            self.connection,
            confirmed["event_id"],
            component="ax",
            from_state="sending",
            to_state="sending",
            code="local_db_confirmed",
            source_epoch=EPOCH,
        )
        self.cli.forget_calls()

        sent, holds, log = self.send_reply(REPLY, confirmed)

        self.assertFalse(sent)
        self.assertEqual(holds, [])
        self.assertEqual(self.cli.verbs(), ["preflight"])
        self.assertTrue(confirmed["_composer_occupied"])
        self.assertIn("[reply-send] composer_occupied rc=0", log)
        self.assertEqual(self.status(confirmed["event_id"]), "processing")

    def test_only_a_newer_local_database_row_counts_as_sent(self):
        accepted = {
            "chat_name": CHAT_NAME,
            "status": "confirmed_local_db",
            "confirmed": True,
            "confirmation_log_id": 9999,
            "network": False,
        }
        cases = (
            ("confirmation is the inbound row itself", {**accepted, "confirmation_log_id": 720}),
            ("delivery went over the network", {**accepted, "network": True}),
            ("status without the confirmed flag", {**accepted, "confirmed": False}),
            ("confirmation is not a log id", {**accepted, "confirmation_log_id": True}),
            ("accepted but unconfirmed", "accepted_unconfirmed"),
            ("command failed", (1, json.dumps(accepted).encode(), b"")),
            ("output is not JSON", (0, b"sent", b"")),
            ("command timed out", subprocess.TimeoutExpired("local-send", 60.0)),
        )
        for log_id, (label, answer) in enumerate(cases, start=720):
            with self.subTest(result=label):
                event = self.inbound(log_id)
                self.due_draft(event, REPLY)
                self.cli.send = answer

                sent, holds, _log = self.send_reply(REPLY, event)

                self.assertFalse(sent)
                self.assertEqual(holds, [])
                self.assertEqual(len(self.cli.sends()), 1)
                # The job stays fenced as sending: it may have reached the room.
                self.assertEqual(self.status(event["event_id"]), "sending")
                self.assertNotIn(
                    ("ax", "sending", "sending", "local_db_confirmed"),
                    self.transitions(event["event_id"]),
                )

    def test_recent_replies_stay_in_the_composer_allowlist(self):
        allowlist = self.room / "composer-allowlist.json"
        for index in range(10):
            event = self.inbound(740 + index)
            self.due_draft(event, f"답 {index}")
            sent, _holds, _log = self.send_reply(f"답 {index}", event)
            self.assertTrue(sent)

        # The eight most recent drafts, newest first.
        self.assertEqual(
            json.loads(allowlist.read_text(encoding="utf-8")),
            {"allowed": [f"답 {index}" for index in range(9, 1, -1)]},
        )

    def test_only_a_reply_about_to_be_typed_enters_the_composer_allowlist(self):
        allowlist = self.room / "composer-allowlist.json"
        unavailable = (1, b"", b"KakaoTalk window is not open")

        blocked = self.inbound(770)
        self.due_draft(blocked, "막힌 답")
        self.cli.preflight = unavailable
        with mock.patch.object(self.module, "PRE_SEND_PREFLIGHT_RETRY_SECONDS", 0.0):
            self.assertEqual(self.send_reply("막힌 답", blocked)[:2], (False, []))

        held = self.inbound(771)
        scheduled = json.loads(self.due_draft(held, "보류된 답")["event_json"])
        self.cli.preflight = "ready"
        self.observe_room_tail(772)
        self.assertEqual(
            self.send_reply("보류된 답", scheduled)[:2],
            (False, ["conversation_context_changed"]),
        )
        self.assertEqual(self.cli.sends(), [])
        self.assertFalse(allowlist.exists())

        sent = self.inbound(773)
        self.due_draft(sent, REPLY)
        at_send = []
        self.cli.before_send = lambda _argv: at_send.append(
            json.loads(allowlist.read_text(encoding="utf-8"))
        )
        self.assertTrue(self.send_reply(REPLY, sent)[0])
        self.assertEqual(at_send, [{"allowed": [REPLY]}])

    def test_preflight_is_retried_a_bounded_number_of_times(self):
        event = self.inbound(760)
        self.due_draft(event, REPLY)
        unavailable = (1, b"", b"KakaoTalk window is not open")
        self.cli.preflight = [unavailable, unavailable, "ready"]

        with mock.patch.object(self.module, "PRE_SEND_PREFLIGHT_RETRY_SECONDS", 0.0):
            sent, _holds, _log = self.send_reply(REPLY, event)

            self.assertTrue(sent)
            self.assertEqual(
                self.cli.verbs(), ["preflight", "preflight", "preflight", "local-send"]
            )

            blocked = self.inbound(761)
            self.due_draft(blocked, REPLY)
            self.cli.preflight = unavailable

            sent, holds, log = self.send_reply(REPLY, blocked)

        self.assertFalse(sent)
        self.assertEqual(holds, [])
        self.assertEqual(
            self.cli.verbs(), ["preflight"] * self.module.PRE_SEND_PREFLIGHT_ATTEMPTS
        )
        self.assertIn("[reply-send] preflight_unavailable rc=1", log)
        self.assertEqual(self.status(blocked["event_id"]), "processing")

    def test_room_outside_the_send_allowlist_is_reported_as_a_named_hold(self):
        event = self.inbound(762)
        self.due_draft(event, REPLY)
        self.cli.preflight = (
            1,
            b"",
            f'Error: chat "{CHAT_NAME}" is not in the local-send allowlist.'.encode(),
        )

        with mock.patch.object(self.module, "PRE_SEND_PREFLIGHT_RETRY_SECONDS", 0.0):
            sent, holds, log = self.send_reply(REPLY, event)

        self.assertFalse(sent)
        self.assertEqual(holds, ["send_allowlist_rejected"])
        self.assertEqual(self.cli.sends(), [])
        self.assertIn("[reply-send] send_allowlist_rejected rc=1", log)
        self.assertEqual(self.status(event["event_id"]), "processing")


# "opencode" in the id keeps the default runner's quota branch reachable while
# the model stays a local one, which is all product generation allows.
PRIMARY = "mlx/opencode-pin-primary"
FALLBACK_A = "mlx/pin-fallback-a"
FALLBACK_B = "mlx/pin-fallback-b"
RUNNER_KINDS = ("gjc", "opencodex")
NOW = 1_800_000_000.0
NO_REPLY = (
    0,
    json.dumps(
        {
            "should_reply": False,
            "reply": "",
            "reason": "low_information",
            "category": "uncertain",
            "evidence_ids": [],
        }
    ).encode(),
    b"",
)
# The provider names its own reset: 2h39m22s is 9562 seconds.
QUOTA = (429, b"", b"Quota exhausted. Resets in 2h39m22s.")
QUOTA_RESET_SECONDS = 9562.0
BROKEN = (1, b"", b"boom")


class _Cooling:
    """Equal to any positive number of seconds."""

    def __eq__(self, other):
        return isinstance(other, (int, float)) and other > 0

    def __repr__(self):
        return "<some positive number of seconds>"


COOLING = _Cooling()


class GenerationFallbackTests(SendPathCase):
    """Every model call owns the lease of the model that answered."""

    extra_environment = {"OPENKAKAO_REPLY_MODEL": PRIMARY}

    def setUp(self):
        super().setUp()
        module = self.module
        (self.state_root / module.REPLY_MODEL_FALLBACKS_NAME).write_text(
            json.dumps({"schema_version": 1, "models": [FALLBACK_A, FALLBACK_B]}),
            encoding="utf-8",
        )
        self.lease_seconds = module.MODEL_CALL_LEASE_SECONDS
        self.model_calls: list[str] = []
        self.answers: dict[str, object] = {}

        def runner(model, *_prompt, **_options):
            self.model_calls.append(model)
            answer = self.answers.get(model, NO_REPLY)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        self.cli.runner = runner
        self.statuses = mock.Mock()
        for owner, name, replacement in (
            (module, "_run_opencodex_generation", runner),
            (module, "runner_is_trusted", mock.Mock(return_value=True)),
            (module, "_publish_model_status", self.statuses),
            (module.time, "time", mock.Mock(return_value=NOW)),
            (module.time, "monotonic", mock.Mock(return_value=1000.0)),
            (module.random, "random", mock.Mock(return_value=0.0)),
        ):
            self._start(mock.patch.object(owner, name, replacement))
        self._start(
            mock.patch(
                "auto_reply_knowledge_graph.retrieve_knowledge_bundle", return_value={}
            )
        )

    @contextlib.contextmanager
    def runner_kind(self, kind: str):
        """One runner kind on an empty breaker, reported as its own subtest."""
        module = self.module
        self.model_calls.clear()
        self.answers.clear()
        self.statuses.reset_mock()
        connection = module._model_circuit_connection()
        try:
            connection.execute("DELETE FROM model_circuit_breaker")
            connection.commit()
        finally:
            connection.close()
        with self.subTest(runner=kind), mock.patch.object(module, "REPLY_RUNNER_KIND", kind):
            yield

    def generate(self, **arguments) -> tuple[dict, str]:
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            result = self.module.generate_reply("확인했어요", [], [], [], [], **arguments)
        return result, captured.getvalue()

    def circuit(self) -> dict[str, tuple]:
        """Each model's breaker row: (state, class, failures, seconds left, leased)."""
        module = self.module
        names = {
            module._model_circuit_key(model): model
            for model in (PRIMARY, FALLBACK_A, FALLBACK_B)
        }
        connection = module._model_circuit_connection()
        try:
            return {
                names[row["model_key"]]: (
                    row["state"],
                    row["failure_class"],
                    row["consecutive_failures"],
                    row["open_until"] - NOW,
                    row["lease_token"] is not None,
                )
                for row in connection.execute("SELECT * FROM model_circuit_breaker")
            }
        finally:
            connection.close()

    def cool_down_primary(self) -> None:
        """Leave the primary rate limited, with a reset 600 seconds away."""
        module = self.module
        slot = module._acquire_model_call_slot(model=PRIMARY)
        module._finish_model_call_failure(
            slot["lease_token"], "rate_limit", model=PRIMARY, retry_after_seconds=600.0
        )

    def answered(self) -> list:
        return [
            mock.call("in_flight", retry_at=NOW + self.lease_seconds),
            mock.call("available"),
        ]

    def test_primary_answer_releases_its_lease(self):
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                result, _log = self.generate()

                self.assertEqual(self.model_calls, [PRIMARY])
                self.assertEqual(
                    (result["model"], result["reason"]), (PRIMARY, "low_information")
                )
                self.assertEqual(self.circuit(), {})
                self.assertEqual(self.statuses.mock_calls, self.answered())

    def test_cooled_down_primary_is_answered_by_a_fallback_and_not_called(self):
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                self.cool_down_primary()

                result, log = self.generate()

                self.assertEqual(self.model_calls, [FALLBACK_A])
                self.assertEqual(
                    (result["model"], result["reason"]), (FALLBACK_A, "low_information")
                )
                self.assertTrue(result["prompt_sha256"])
                # The fallback's lease is released; the primary keeps its reset.
                self.assertEqual(
                    self.circuit(), {PRIMARY: ("open", "rate_limit", 1, 600.0, False)}
                )
                self.assertEqual(self.statuses.mock_calls, self.answered())
                self.assertIn(f"[reply-gen] cooldown fallback answered on {FALLBACK_A}", log)

    def test_cooled_down_primary_with_no_working_fallback_defers_to_its_reset(self):
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                self.cool_down_primary()
                self.answers.update({FALLBACK_A: BROKEN, FALLBACK_B: BROKEN})

                result, _log = self.generate()

                self.assertEqual(self.model_calls, [FALLBACK_A, FALLBACK_B])
                self.assertEqual(
                    (
                        result["should_reply"],
                        result["reason"],
                        result["model_failure_class"],
                        result["model_defer_until"],
                        result["model_invoked"],
                    ),
                    (False, "model_rate_limited", "rate_limit", NOW + 600.0, False),
                )
                self.assertNotIn("prompt_sha256", result)
                self.assertEqual(
                    self.circuit(),
                    {
                        PRIMARY: ("open", "rate_limit", 1, 600.0, False),
                        FALLBACK_A: ("open", "runner_failed", 1, COOLING, False),
                        FALLBACK_B: ("open", "runner_failed", 1, COOLING, False),
                    },
                )
                self.assertEqual(
                    self.statuses.mock_calls,
                    [mock.call("cooldown", failure_class="rate_limit", retry_at=NOW + 600.0)],
                )

    def test_primary_call_in_flight_elsewhere_defers_without_a_fallback(self):
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                self.module._acquire_model_call_slot(model=PRIMARY)
                lease_ends = NOW + self.lease_seconds

                result, _log = self.generate()

                self.assertEqual(self.model_calls, [])
                self.assertEqual(
                    (
                        result["reason"],
                        result["model_failure_class"],
                        result["model_defer_until"],
                        result["model_invoked"],
                    ),
                    ("model_call_in_flight", "call_in_flight", lease_ends, False),
                )
                self.assertEqual(
                    self.circuit(), {PRIMARY: ("in_flight", "", 0, self.lease_seconds, True)}
                )
                self.assertEqual(
                    self.statuses.mock_calls,
                    [mock.call("in_flight", failure_class="call_in_flight", retry_at=lease_ends)],
                )

    def test_limited_primary_is_answered_by_a_fallback_and_keeps_its_reset(self):
        labels = {"gjc": "opencode_go_quota_fallback", "opencodex": "fallback"}
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                self.answers[PRIMARY] = QUOTA

                result, log = self.generate()

                self.assertEqual(self.model_calls, [PRIMARY, FALLBACK_A])
                self.assertEqual(
                    (result["model"], result["reason"]), (FALLBACK_A, "low_information")
                )
                self.assertEqual(
                    self.circuit(),
                    {PRIMARY: ("open", "quota_exhausted", 1, QUOTA_RESET_SECONDS, False)},
                )
                self.assertEqual(self.statuses.mock_calls, self.answered())
                self.assertIn(f"[reply-gen] {labels[kind]} answered on {FALLBACK_A}", log)

    def test_limited_primary_with_no_working_fallback_opens_its_circuit(self):
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                self.answers.update({PRIMARY: QUOTA, FALLBACK_A: BROKEN, FALLBACK_B: BROKEN})
                reset_at = NOW + QUOTA_RESET_SECONDS

                result, _log = self.generate()

                self.assertEqual(self.model_calls, [PRIMARY, FALLBACK_A, FALLBACK_B])
                self.assertEqual(
                    (
                        result["model"],
                        result["reason"],
                        result["model_failure_class"],
                        result["model_defer_until"],
                        result["model_invoked"],
                    ),
                    (PRIMARY, "model_usage_limited", "quota_exhausted", reset_at, True),
                )
                self.assertEqual(
                    self.circuit(),
                    {
                        PRIMARY: ("open", "quota_exhausted", 1, QUOTA_RESET_SECONDS, False),
                        FALLBACK_A: ("open", "runner_failed", 1, COOLING, False),
                        FALLBACK_B: ("open", "runner_failed", 1, COOLING, False),
                    },
                )
                self.assertEqual(
                    self.statuses.mock_calls,
                    [
                        mock.call("in_flight", retry_at=NOW + self.lease_seconds),
                        mock.call(
                            "cooldown", failure_class="quota_exhausted", retry_at=reset_at
                        ),
                    ],
                )

    def test_timed_out_primary_is_answered_by_a_fallback_on_the_default_runner(self):
        self.answers[PRIMARY] = subprocess.TimeoutExpired("runner", 90.0)

        result, log = self.generate()

        self.assertEqual(self.model_calls, [PRIMARY, FALLBACK_A])
        self.assertEqual((result["model"], result["reason"]), (FALLBACK_A, "low_information"))
        self.assertIsNone(result["generation_seconds"])
        self.assertEqual(
            self.circuit(), {PRIMARY: ("open", "runner_timeout", 1, COOLING, False)}
        )
        self.assertEqual(self.statuses.mock_calls, self.answered())
        self.assertIn(f"[reply-gen] timeout_fallback answered on {FALLBACK_A}", log)

    def test_timed_out_primary_with_no_working_fallback_opens_its_circuit(self):
        self.answers.update(
            {
                PRIMARY: subprocess.TimeoutExpired("runner", 90.0),
                FALLBACK_A: BROKEN,
                FALLBACK_B: BROKEN,
            }
        )

        result, _log = self.generate()

        self.assertEqual(self.model_calls, [PRIMARY, FALLBACK_A, FALLBACK_B])
        self.assertEqual(
            (result["model"], result["reason"], result["model_failure_class"]),
            (PRIMARY, "model_temporarily_unavailable", "runner_timeout"),
        )
        self.assertEqual(
            self.circuit(),
            {
                PRIMARY: ("open", "runner_timeout", 1, COOLING, False),
                FALLBACK_A: ("open", "runner_failed", 1, COOLING, False),
                FALLBACK_B: ("open", "runner_failed", 1, COOLING, False),
            },
        )
        self.assertEqual(
            result["model_defer_until"] - NOW, self.circuit()[PRIMARY][3]
        )

    def test_runner_fault_with_no_fallback_route_opens_the_primary_circuit(self):
        module = self.module
        for kind, fault, failure in (
            ("gjc", OSError("runner vanished"), "runner_io_failure"),
            ("gjc", module._CaptureOverflow("too much output"), "runner_output_overflow"),
            ("opencodex", subprocess.TimeoutExpired("runner", 90.0), "runner_timeout"),
        ):
            with self.runner_kind(kind), self.subTest(failure=failure):
                self.answers[PRIMARY] = fault

                result, _log = self.generate()

                self.assertEqual(self.model_calls, [PRIMARY])
                self.assertEqual(
                    (result["model"], result["model_failure_class"], result["model_invoked"]),
                    (PRIMARY, failure, True),
                )
                self.assertEqual(
                    self.circuit(), {PRIMARY: ("open", failure, 1, COOLING, False)}
                )

    def test_spent_generation_budget_calls_no_model(self):
        for kind in RUNNER_KINDS:
            for cooled in (False, True):
                with self.runner_kind(kind), self.subTest(cooled=cooled), mock.patch.object(
                    self.module, "MODEL_GENERATION_BUDGET_SECONDS", 0.0
                ):
                    if cooled:
                        self.cool_down_primary()

                    result, _log = self.generate()

                    self.assertEqual(self.model_calls, [])
                    self.assertEqual(
                        result["model_failure_class"],
                        "rate_limit" if cooled else "runner_timeout",
                    )
                    self.assertEqual(set(self.circuit()), {PRIMARY})

    def test_turn_held_after_the_model_answered_releases_the_lease(self):
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                guard = mock.Mock(side_effect=[None, None, "burst_superseded"])

                result, _log = self.generate(turn_guard=guard)

                self.assertEqual(self.model_calls, [PRIMARY])
                self.assertEqual(
                    (result["should_reply"], result["reason"], result["category"]),
                    (False, "burst_superseded", "policy"),
                )
                self.assertEqual(result["model"], PRIMARY)
                self.assertEqual(self.circuit(), {})

    def test_turn_held_during_a_cooldown_stops_the_fallback_chain(self):
        for kind in RUNNER_KINDS:
            with self.runner_kind(kind):
                self.cool_down_primary()
                guard = mock.Mock(side_effect=[None, None, None, "burst_superseded"])

                result, _log = self.generate(turn_guard=guard)

                self.assertEqual(self.model_calls, [])
                self.assertEqual(
                    (result["reason"], result["model_failure_class"], result["model_invoked"]),
                    ("model_rate_limited", "rate_limit", False),
                )
                self.assertEqual(
                    self.circuit(), {PRIMARY: ("open", "rate_limit", 1, 600.0, False)}
                )


if __name__ == "__main__":
    unittest.main()
