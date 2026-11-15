import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
WORKER = SCRIPTS / "auto-reply-worker.py"


def load_worker(name: str):
    spec = importlib.util.spec_from_file_location(name, WORKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _process_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# worker_main with every collaborator faked except one real isolated model
# runner. The runner and its descendant ignore SIGTERM, so only the worker's
# own group cleanup (TERM, then KILL) can stop them.
SIGTERM_HARNESS = r"""
import importlib.util, os, sys
from pathlib import Path
from unittest import mock

worker_path, pid_path, closed_path = sys.argv[1:4]
sys.path.insert(0, str(Path(worker_path).parent))
spec = importlib.util.spec_from_file_location("auto_reply_sigterm_harness", worker_path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

RUNNER = (
    "import os, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "child = subprocess.Popen([sys.executable, '-c', "
    "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
    "tmp = sys.argv[1] + '.tmp'\n"
    "open(tmp, 'w').write(f'{os.getpid()} {child.pid}')\n"
    "os.replace(tmp, sys.argv[1])\n"
    "time.sleep(60)\n"
)


class Sampler:
    def start(self):
        pass

    def tick(self):
        pass

    def take(self, now=None):
        return None

    def close(self):
        Path(closed_path).write_text("closed")


def process_job(job, previous_status, connection):
    worker._run_bounded_process(
        [sys.executable, "-c", RUNNER, pid_path],
        cwd=Path("/tmp"),
        env=dict(os.environ),
        timeout=60.0,
        stdout_cap=1024,
        stderr_cap=1024,
        isolate_group=True,
    )


worker._IdleContextSampler = Sampler
worker._queue_connection = lambda: mock.MagicMock()
worker._model_circuit_connection = lambda: mock.MagicMock()
worker._WorkerHealth = lambda: mock.MagicMock()
worker._rerank_client = lambda: mock.MagicMock()
worker._refresh_model_status_from_circuit = lambda *a, **k: {}
worker._rearm_model_call_in_flight_jobs = lambda *a, **k: None
worker.apply_operator_request = lambda *a, **k: None
worker.recover_stale_jobs = lambda *a, **k: None
worker._auto_reconcile_give_up = lambda *a, **k: None
worker._queue_reconciliation_blockers = lambda *a, **k: 0
worker.archive_terminal_jobs = lambda *a, **k: 0
worker.claim_job = lambda *a, **k: ({"event_id": "db:42:1"}, "pending")
worker.process_job = process_job
raise SystemExit(worker.worker_main())
"""


class WorkerShutdownTests(unittest.TestCase):
    @unittest.expectedFailure
    def test_sigterm_runs_cleanup_and_stops_the_isolated_model_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "harness.py"
            harness.write_text(SIGTERM_HARNESS, encoding="utf-8")
            pid_path = root / "runner.pids"
            closed_path = root / "sampler.closed"
            process = subprocess.Popen(
                [sys.executable, str(harness), str(WORKER), str(pid_path), str(closed_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            leader = descendant = None
            try:
                self.assertTrue(
                    _wait_until(pid_path.is_file, 20.0), "fake model runner never started"
                )
                leader, descendant = (int(item) for item in pid_path.read_text().split())
                # The Rust sweep signals the whole group, then the Python
                # supervisor's stop() terminates the worker again.
                process.send_signal(signal.SIGTERM)
                time.sleep(0.15)
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    _stdout, stderr = process.communicate(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    _stdout, stderr = process.communicate()
                    self.fail("worker did not exit within the supervisor's grace period")
                self.assertTrue(
                    _wait_until(lambda: _process_gone(leader), 2.0),
                    "isolated model runner survived worker shutdown",
                )
                self.assertTrue(
                    _wait_until(lambda: _process_gone(descendant), 2.0),
                    "isolated model runner descendant survived worker shutdown",
                )
                self.assertTrue(closed_path.is_file(), "worker shutdown cleanup did not run")
                self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr.decode())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if leader is not None:
                    try:
                        os.killpg(leader, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

    @unittest.expectedFailure
    def test_shutdown_during_a_link_attempt_is_not_retried_on_the_next_address(self):
        worker = load_worker("auto_reply_link_shutdown_test")
        attempts: list[str] = []

        class Connection:
            def __init__(self, ip, *args, **kwargs):
                self.ip = ip
                attempts.append(ip)

            def request(self, *args, **kwargs):
                if self.ip == "192.0.2.1":
                    raise SystemExit(128 + signal.SIGTERM)

            def getresponse(self):
                return object()

            def close(self):
                pass

        addresses = [
            (None, None, None, "", ("192.0.2.1", 80)),
            (None, None, None, "", ("192.0.2.2", 80)),
        ]
        with mock.patch.object(worker.http.client, "HTTPConnection", Connection):
            with self.assertRaises(SystemExit):
                worker._open_pinned_link(
                    urllib.parse.urlparse("http://example.test/feed"),
                    "example.test",
                    80,
                    addresses,
                    time.monotonic() + 5.0,
                )
        self.assertEqual(attempts, ["192.0.2.1"])


class LaughterSelectionTests(unittest.TestCase):
    def setUp(self):
        self.worker = load_worker("auto_reply_laughter_selection_test")

    def sanitize(self, draft: str) -> str:
        return self.worker._sanitize_outbound_reaction(
            draft, "그건 좀 세네", laughter_allowed=True
        )

    def test_allowed_four_run_reply_passes_through_unchanged(self):
        for _ in range(20):
            self.assertEqual(self.sanitize("ㅋㅋㅋㅋ 그건 좀"), "ㅋㅋㅋㅋ 그건 좀")

    def test_same_draft_always_sanitizes_to_the_same_reply(self):
        outputs = {self.sanitize("좋네 ㅋㅋㅋ") for _ in range(20)}
        self.assertEqual(len(outputs), 1, outputs)

    def test_exact_triple_laughter_still_varies_across_drafts(self):
        drafts = [f"{word} ㅋㅋㅋ" for word in (
            "좋네", "그렇지", "맞아", "대박", "역시", "아 진짜",
            "그니까", "오호", "인정", "웃기네", "그럴 줄", "어쩐지",
        )]
        lengths = set()
        for draft in drafts:
            reply = self.sanitize(draft)
            run = reply.rsplit(" ", 1)[-1]
            self.assertRegex(run, r"\Aㅋ{3,}\Z")
            lengths.add(len(run))
        self.assertGreater(len(lengths), 1, "every draft kept exactly three ㅋ")


if __name__ == "__main__":
    unittest.main()
