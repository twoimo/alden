import contextlib
import errno
import importlib.util
import io
import itertools
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_counter = itertools.count()


def load_supervisor():
    name = f"auto_reply_supervisor_lifecycle_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "auto-reply-supervisor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeChild:
    _pids = itertools.count(41000)

    def __init__(self, argv, env):
        self.argv = list(argv)
        self.env = dict(env or {})
        self.pid = next(self._pids)
        self.returncode = None
        self.stdout = None
        self.terminated = False

    def __repr__(self):
        scripts = [Path(arg).name for arg in self.argv if str(arg).endswith(".py")]
        return f"FakeChild({' '.join(scripts)})"

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -signal.SIGTERM

    def kill(self):
        self.terminated = True
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        return self.returncode


class SupervisorMainHarness:
    """Run the real supervisor main() against fake children and a fake DB probe."""

    def __init__(self, test, root, *, db_ready, env=None, ticks=3, spawn_error=None):
        self.test = test
        self.state_root = root / "home" / "state" / "rooms" / "42"
        self.module = load_supervisor()
        self.spawned = []
        self.db_ready = db_ready
        self.env = env or {}
        self.ticks = ticks
        self.spawn_error = spawn_error
        self.sleeps = 0
        self.patches = {}

    def _popen(self, argv, **kwargs):
        if self.spawn_error is not None and self.spawn_error in " ".join(argv):
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
        child = FakeChild(argv, kwargs.get("env"))
        self.spawned.append(child)
        return child

    def _sleep(self, _seconds):
        self.sleeps += 1
        if self.sleeps >= self.ticks:
            self.module._signal_requested = signal.SIGTERM

    def scripts_started(self):
        return [
            Path(arg).name
            for child in self.spawned
            for arg in child.argv
            if str(arg).endswith(".py")
        ]

    def status(self):
        return json.loads((self.state_root / "supervisor-status.json").read_text("utf-8"))

    def run(self):
        raised = self.run_capturing()
        self.test.assertIsInstance(raised, SystemExit)
        return raised.code

    def run_capturing(self):
        module = self.module
        config = {
            "safety": {"allow_auto_reply": True},
            "model": {"privacy_mode": "local"},
        }
        argv = [
            "auto-reply-supervisor.py",
            "--state-root",
            str(self.state_root),
            "--target-chat-id",
            "42",
            "--self-nickname",
            "me",
            "--interval",
            "0.2",
        ]
        environment = {
            "OPENKAKAO_REPLY_AUTHORS": "friend",
            "OPENKAKAO_GEEKNEWS_ONLY": "",
            **self.env,
        }
        with mock.patch.dict(os.environ, environment), mock.patch.object(
            sys, "argv", argv
        ), mock.patch.object(
            module, "read_attested_config", return_value=("digest", config)
        ), mock.patch.object(
            module, "db_ready", side_effect=self.db_ready
        ), mock.patch.object(
            module.subprocess, "Popen", side_effect=self._popen
        ), mock.patch.object(
            module.signal, "signal"
        ), mock.patch.object(
            module.time, "sleep", side_effect=self._sleep
        ), contextlib.ExitStack() as stack:
            for name, value in self.patches.items():
                stack.enter_context(mock.patch.object(module, name, value))
            try:
                module.main()
            except BaseException as exc:
                return exc
        self.test.fail("supervisor main() returned instead of exiting")


class GeekNewsOnlyRoomTests(unittest.TestCase):
    def assert_never_started_db_watch(self, harness):
        self.assertNotIn("auto-reply-db-watch.py", harness.scripts_started())
        for child in harness.spawned:
            self.assertEqual(child.env.get("OPENKAKAO_DB_READY"), "0", child.argv)
            self.assertEqual(
                child.env.get("OPENKAKAO_AUTO_REPLY_ENABLED"), "0", child.argv
            )
        status = harness.status()
        self.assertFalse(status["database_started"])
        self.assertEqual(status["database_reason"], "geeknews_only")
        self.assertFalse(status["auto_reply_enabled"])

    def test_geeknews_only_room_never_starts_db_watch_while_db_is_ready(self):
        with tempfile.TemporaryDirectory() as raw:
            harness = SupervisorMainHarness(
                self,
                Path(raw),
                db_ready=lambda: True,
                env={"OPENKAKAO_GEEKNEWS_ONLY": "1"},
            )
            self.assertEqual(harness.run(), 0)
            self.assertEqual(
                harness.scripts_started(),
                ["auto-reply-apple-watch.py", "auto-reply-worker.py"],
            )
            self.assert_never_started_db_watch(harness)

    def test_geeknews_only_room_never_starts_db_watch_when_db_becomes_ready(self):
        answers = iter([False])
        with tempfile.TemporaryDirectory() as raw:
            harness = SupervisorMainHarness(
                self,
                Path(raw),
                db_ready=lambda: next(answers, True),
                env={"OPENKAKAO_GEEKNEWS_ONLY": "1"},
            )
            self.assertEqual(harness.run(), 0)
            self.assert_never_started_db_watch(harness)

    def test_reply_room_starts_db_watch_once_the_db_becomes_ready(self):
        answers = iter([False])
        with tempfile.TemporaryDirectory() as raw:
            harness = SupervisorMainHarness(
                self,
                Path(raw),
                db_ready=lambda: next(answers, True),
            )
            self.assertEqual(harness.run(), 0)
            self.assertEqual(
                harness.scripts_started(),
                [
                    "auto-reply-apple-watch.py",
                    "auto-reply-worker.py",
                    "auto-reply-db-watch.py",
                ],
            )
            self.assertTrue(harness.status()["database_started"])


class SupervisorFailureCleanupTests(unittest.TestCase):
    def test_status_write_failure_in_the_loop_stops_every_child(self):
        with tempfile.TemporaryDirectory() as raw:
            harness = SupervisorMainHarness(self, Path(raw), db_ready=lambda: True)
            real_write_status = harness.module.write_status

            def disk_full_after_first_tick(*args, **kwargs):
                if harness.sleeps >= 1:
                    raise OSError(errno.ENOSPC, "No space left on device")
                return real_write_status(*args, **kwargs)

            harness.patches["write_status"] = disk_full_after_first_tick
            with contextlib.redirect_stderr(io.StringIO()):
                raised = harness.run_capturing()

            self.assertEqual(len(harness.spawned), 3)
            self.assertEqual(
                [child for child in harness.spawned if not child.terminated], []
            )
            self.assertIsInstance(raised, SystemExit)
            self.assertEqual(raised.code, 1)

    def test_failed_worker_start_stops_the_children_already_started(self):
        with tempfile.TemporaryDirectory() as raw:
            harness = SupervisorMainHarness(
                self,
                Path(raw),
                db_ready=lambda: True,
                spawn_error="auto-reply-worker.py",
            )
            with contextlib.redirect_stderr(io.StringIO()):
                raised = harness.run_capturing()

            self.assertEqual(
                harness.scripts_started(),
                ["auto-reply-apple-watch.py", "auto-reply-db-watch.py"],
            )
            self.assertEqual(
                [child for child in harness.spawned if not child.terminated], []
            )
            self.assertIsInstance(raised, SystemExit)
            self.assertEqual(raised.code, 1)


if __name__ == "__main__":
    unittest.main()
