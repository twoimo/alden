"""Owned vision lifecycle contracts, using only fake services and scratch files."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts import alden_local_vision as VISION  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class IsolatedVisionTestCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.global_root = self.home / "Library/Caches/Alden/local-vision"
        self.stack.enter_context(mock.patch.object(Path, "home", return_value=self.home))
        # Fail loudly if a fixture misses a real process, socket, HTTP, or memory seam.
        for owner, name in (
            (VISION.subprocess, "Popen"),
            (VISION.subprocess, "run"),
            (VISION.socket, "socket"),
            (VISION.http.client, "HTTPConnection"),
            (VISION.os, "killpg"),
            (VISION.ondevice, "_local_only_urlopen"),
            (VISION.ondevice, "detect_memory_budget"),
            (VISION, "validate_executable"),
        ):
            self.stack.enter_context(mock.patch.object(
                owner, name, side_effect=AssertionError(f"unconfigured adapter: {name}")
            ))

    @contextmanager
    def runtime(self):
        """A ready server whose deadline monitor is explicitly driven by each test."""
        model_dir = self.home / ".mlx-serve/models" / VISION.MODEL_ID
        model_dir.mkdir(parents=True, exist_ok=True)
        process = mock.Mock(
            pid=4000,
            stdin=io.BytesIO(), stdout=io.BytesIO(b"4242\n"),
            poll=mock.Mock(return_value=None), wait=mock.Mock(return_value=0),
        )
        clock = FakeClock()
        event = mock.Mock(wait=mock.Mock(return_value=True))
        watcher = mock.Mock()
        row = {"id": VISION.MODEL_ID, "loaded": True, "capabilities": ["text", "vision"]}
        with ExitStack() as stack:
            def patch(owner, name, **kwargs):
                return stack.enter_context(mock.patch.object(owner, name, **kwargs))

            patch(VISION.sys, "platform", new="darwin")
            patch(VISION, "time", new=clock)
            patch(VISION, "threading", new=SimpleNamespace(
                Event=mock.Mock(return_value=event), Lock=threading.Lock,
                Thread=mock.Mock(return_value=watcher),
            ))
            sandbox = patch(Path, "is_file", return_value=True)
            executable = patch(VISION, "validate_executable", return_value=None)
            identity = {"revision": VISION.REVISION, "files": {"synthetic": [1, 2, 3]}}
            verify = patch(VISION, "_verify_checkpoint", return_value=({"files": []}, identity))
            checkpoint_identity = patch(VISION, "_checkpoint_identity", return_value=identity)
            memory = patch(VISION.ondevice, "detect_memory_budget", return_value=
                           VISION.ondevice.MemoryBudget(free_bytes=40 * 1024**3))
            socket_factory = patch(VISION.socket, "socket")
            probe = socket_factory.return_value.__enter__.return_value
            spawn = patch(VISION.subprocess, "Popen", return_value=process)
            reap = patch(VISION, "_reap_stranded_group", return_value=True)
            listener = patch(VISION.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout="4242\n",
            ))
            http = mock.Mock(side_effect=lambda *_a, **_k:
                             io.BytesIO(json.dumps({"data": [row]}).encode()))
            transport_factory = patch(VISION, "_bound_transport", return_value=http)
            patch(VISION.select, "select", side_effect=lambda readers, *_args: (readers, [], []))
            patch(VISION.secrets, "token_urlsafe", return_value="test-request-key")
            stack.enter_context(mock.patch.dict(VISION.os.environ, {}, clear=True))
            yield SimpleNamespace(
                clock=clock, process=process, event=event, watcher=watcher, row=row,
                sandbox=sandbox, executable=executable, memory=memory, probe=probe,
                spawn=spawn, listener=listener, http=http, model_dir=model_dir,
                transport_factory=transport_factory,
                verify=verify, checkpoint_identity=checkpoint_identity,
                reap=reap,
                monitor=lambda: VISION.threading.Thread.call_args.kwargs["target"](),
            )

    def assert_unavailable(self, code, *, timeout=1.0, check_cancelled=lambda: None):
        with self.assertRaises(VISION.VisionUnavailable) as raised:
            with VISION.owned_vision_endpoint(
                self.state, timeout=timeout, check_cancelled=check_cancelled,
            ):
                self.fail("an unavailable server must never yield an endpoint")
        self.assertEqual(raised.exception.code, code)

    def assert_cleaned(self, runtime, *, joined=True, escalated=False):
        self.assertTrue(runtime.process.stdin.closed)
        self.assertTrue(runtime.process.stdout.closed)
        if not escalated:
            runtime.process.wait.assert_called_once_with(timeout=8.0)
        runtime.event.set.assert_called_once_with()
        if joined:
            runtime.watcher.join.assert_called_once_with(timeout=0.2)
        else:
            runtime.watcher.join.assert_not_called()
        log_path = Path(runtime.spawn.call_args.args[0][4])
        self.assertFalse(log_path.parent.exists())
        with VISION._vision_lock(self.state), VISION._vision_lock(self.global_root):
            pass  # Both normal and exceptional cleanup must release both locks.


class VisionGuardTests(IsolatedVisionTestCase):
    def test_platform_and_invalid_deadlines_never_spawn(self):
        with self.runtime() as runtime:
            with mock.patch.object(VISION.sys, "platform", "linux"):
                self.assert_unavailable("local_vision_platform_unavailable")
            for timeout in (0, -1, float("nan"), float("inf"), -float("inf"), "1", None):
                with self.subTest(timeout=timeout):
                    self.assert_unavailable("local_vision_deadline_invalid", timeout=timeout)
            runtime.spawn.assert_not_called()
            runtime.memory.assert_not_called()
            self.assertFalse(self.state.exists())

    def test_busy_port_is_never_adopted_or_probed_over_http(self):
        with self.runtime() as runtime:
            runtime.probe.bind.side_effect = OSError("address already in use")
            self.assert_unavailable("local_vision_port_in_use")
            runtime.probe.bind.assert_called_once_with(("127.0.0.1", 11237))
            runtime.executable.assert_not_called()
            runtime.spawn.assert_not_called()
            runtime.listener.assert_not_called()
            runtime.http.assert_not_called()

    def test_port_taken_during_hashing_is_rechecked_before_spawn(self):
        with self.runtime() as runtime:
            def checkpoint_verified(*_args):
                runtime.probe.bind.side_effect = OSError("port taken during hashing")
                return runtime.verify.return_value

            runtime.verify.side_effect = checkpoint_verified
            self.assert_unavailable("local_vision_port_in_use")
            runtime.verify.assert_called_once()
            runtime.spawn.assert_not_called()
            runtime.http.assert_not_called()

    def test_unverified_executable_and_missing_sandbox_fail_before_memory_or_spawn(self):
        with self.runtime() as runtime:
            runtime.executable.return_value = "signature_invalid"
            self.assert_unavailable("local_vision_executable_unverified")
            runtime.executable.return_value = None
            runtime.sandbox.return_value = False
            self.assert_unavailable("local_vision_sandbox_unavailable")
            runtime.memory.assert_not_called()
            runtime.spawn.assert_not_called()

    def test_missing_or_symlinked_checkpoint_never_spawns(self):
        with self.runtime() as runtime:
            runtime.model_dir.rmdir()
            self.assert_unavailable("local_vision_checkpoint_unavailable")
            scratch_model = self.root / "scratch-model"
            scratch_model.mkdir()
            runtime.model_dir.symlink_to(scratch_model, target_is_directory=True)
            self.assert_unavailable("local_vision_checkpoint_unavailable")
            runtime.memory.assert_not_called()
            runtime.spawn.assert_not_called()

    def test_memory_guard_uses_usable_40_gib_and_fails_closed(self):
        with self.runtime() as runtime:
            # Free bytes alone pass 40 GiB; reserved bytes make usable memory fail.
            runtime.memory.return_value = VISION.ondevice.MemoryBudget(
                free_bytes=41 * 1024**3, other_resident_bytes=1024**3 + 1,
            )
            self.assert_unavailable("local_vision_memory_insufficient")
            runtime.memory.side_effect = OSError("memory measurement failed")
            self.assert_unavailable("local_vision_memory_unavailable")
            runtime.spawn.assert_not_called()
            runtime.http.assert_not_called()

    def test_preflight_consuming_deadline_never_spawns(self):
        with self.runtime() as runtime:
            def slow_memory():
                runtime.clock.sleep(2.0)
                return VISION.ondevice.MemoryBudget(free_bytes=40 * 1024**3)

            runtime.memory.side_effect = slow_memory
            self.assert_unavailable("local_vision_timeout", timeout=1.0)
            runtime.spawn.assert_not_called()

    def test_checkpoint_failure_or_hashing_deadline_prevents_launch(self):
        with self.runtime() as runtime:
            runtime.verify.side_effect = OSError("checkpoint missing")
            self.assert_unavailable("local_vision_checkpoint_mismatch")

            def slow_verification(_model_dir, _state_root, check_budget):
                runtime.clock.sleep(2)
                check_budget()
                self.fail("hashing continued beyond deadline")

            runtime.verify.side_effect = slow_verification
            self.assert_unavailable("local_vision_timeout")
            runtime.spawn.assert_not_called()

    def test_memory_is_rechecked_after_hashing_before_spawning(self):
        for outcome, code in (
            (VISION.ondevice.MemoryBudget(free_bytes=40 * 1024**3 - 1), "local_vision_memory_insufficient"),
            (OSError("memory probe failed"), "local_vision_memory_unavailable"),
        ):
            with self.subTest(code=code), self.runtime() as runtime:
                runtime.memory.side_effect = [VISION.ondevice.MemoryBudget(free_bytes=40 * 1024**3), outcome]
                self.assert_unavailable(code)
                runtime.verify.assert_called_once()
                self.assertEqual(runtime.memory.call_count, 2)
                runtime.spawn.assert_not_called()

    def test_cancellation_before_launch_propagates_without_spawning(self):
        for cancel_call in (1, 2):
            with self.subTest(cancel_call=cancel_call), self.runtime() as runtime:
                cancelled = InterruptedError("cancelled")
                callback = mock.Mock(side_effect=[None] * (cancel_call - 1) + [cancelled])
                with self.assertRaises(InterruptedError) as raised:
                    with VISION.owned_vision_endpoint(
                        self.state, timeout=1, check_cancelled=callback,
                    ):
                        self.fail("cancelled request yielded")
                self.assertIs(raised.exception, cancelled)
                runtime.spawn.assert_not_called()


class VisionEndpointTests(IsolatedVisionTestCase):
    def test_exact_memory_boundary_yields_authenticated_same_model_and_cleans_up(self):
        with self.runtime() as runtime:
            proxies = {name: "http://proxy.invalid:9999" for name in (
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
            )}
            with mock.patch.dict(VISION.os.environ, {**proxies, "KEEP_TEST_ENV": "kept"}):
                with VISION.owned_vision_endpoint(self.state, timeout=200) as endpoint:
                    self.assertEqual(endpoint[:5], (
                        "http://127.0.0.1:11237/v1", VISION.MODEL_ID,
                        ["text", "vision"], "test-request-key", 90.0,
                    ))
                    self.assertEqual(len(endpoint), 6)
                    self.assertIs(endpoint[5], runtime.http)
                    runtime.transport_factory.assert_called_once()
                    self.assertEqual(runtime.transport_factory.call_args.args[:2], (4242, runtime.process))
                    argv = runtime.spawn.call_args.args[0]
                    options = runtime.spawn.call_args.kwargs
                    self.assertEqual(float(argv[3]), 1090.0)
                    command = json.loads(argv[5])
                    for flag, value in (
                        ("--model", str(runtime.model_dir)), ("--host", "127.0.0.1"),
                        ("--port", "11237"), ("--api-key-env", VISION.KEY_ENV),
                        ("--max-concurrent", "1"), ("--max-resident-models", "1"),
                        ("--ctx-size", "32768"), ("--max-resident-mem", "24GB"),
                        ("--max-tokens", "1024"),
                        ("--prefix-cache-disk", "off"),
                    ):
                        self.assertEqual(command[command.index(flag) + 1], value)
                    self.assertIn("--api-key-strict", command)
                    self.assertNotIn("test-request-key", " ".join(argv))
                    self.assertEqual(options["env"][VISION.KEY_ENV], "test-request-key")
                    self.assertEqual(options["env"]["KEEP_TEST_ENV"], "kept")
                    self.assertTrue(set(proxies).isdisjoint(options["env"]))
                    self.assertTrue(options["close_fds"])
                    self.assertTrue(options["start_new_session"])
                    self.assertEqual(options["stdin"], subprocess.PIPE)
                    request = runtime.http.call_args.args[0]
                    self.assertEqual(request.full_url, endpoint[0] + "/models")
                    self.assertEqual(request.get_header("Authorization"), "Bearer test-request-key")
                    self.assertLessEqual(runtime.http.call_args.kwargs["timeout"], 1.0)
                    runtime.listener.assert_called_once_with(
                        ["/usr/sbin/lsof", "-nP", "-a", "-p", "4242", "-iTCP:11237", "-sTCP:LISTEN", "-t"],
                        capture_output=True, text=True, timeout=2.0, check=False,
                    )
                    log = Path(argv[4])
                    log.write_text("scratch server log", encoding="utf-8")
                    self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)
            self.assert_cleaned(runtime)

    def test_slow_catalog_cannot_yield_after_deadline_without_monitor_tick(self):
        with self.runtime() as runtime:
            def slow_catalog(*_args, **_kwargs):
                runtime.clock.sleep(1)
                return io.BytesIO(json.dumps({"data": [runtime.row]}).encode())

            runtime.http.side_effect = slow_catalog
            self.assert_unavailable("local_vision_timeout", timeout=1)
            self.assert_cleaned(runtime)

    def test_consumer_deadline_is_checked_even_without_monitor_tick(self):
        with self.runtime() as runtime:
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                with VISION.owned_vision_endpoint(self.state, timeout=1):
                    runtime.clock.sleep(1)
                    self.assertFalse(runtime.process.stdin.closed)
            self.assertEqual(raised.exception.code, "local_vision_timeout")
            self.assert_cleaned(runtime)

    def test_global_lock_serializes_requests_from_different_operator_roots(self):
        with self.runtime() as runtime:
            other_root = self.root / "other-operator"
            with VISION.owned_vision_endpoint(self.state, timeout=1):
                with self.assertRaises(VISION.VisionUnavailable) as raised:
                    with VISION.owned_vision_endpoint(other_root, timeout=1):
                        self.fail("second operator root acquired the shared server")
                self.assertEqual(raised.exception.code, "local_vision_busy")
                runtime.spawn.assert_called_once()
                self.assertEqual(stat.S_IMODE(self.global_root.stat().st_mode), 0o700)
            self.assert_cleaned(runtime)
            with VISION._vision_lock(other_root):
                pass

    def test_transport_receives_request_deadline_guard_not_only_cancellation(self):
        with self.runtime() as runtime:
            with VISION.owned_vision_endpoint(self.state, timeout=1):
                guard = runtime.transport_factory.call_args.args[2]
                guard()
                runtime.clock.sleep(1)
                with self.assertRaises(VISION.VisionUnavailable) as raised:
                    guard()
                self.assertEqual(raised.exception.code, "local_vision_timeout")
                # Restore the fake clock so this test isolates the transport guard.
                runtime.clock.now -= 1
            self.assert_cleaned(runtime)

    def test_only_loaded_same_27b_vision_aliases_are_accepted(self):
        for model_id in (VISION.MODEL_ID, "mlx/" + VISION.MODEL_ID, VISION.MODEL_ID.rsplit("/", 1)[1]):
            with self.subTest(model_id=model_id), self.runtime() as runtime:
                runtime.row["id"] = model_id
                with VISION.owned_vision_endpoint(self.state, timeout=1) as endpoint:
                    self.assertEqual(endpoint[1], model_id)
                self.assert_cleaned(runtime)

    def test_wrong_model_unloaded_or_text_only_catalog_times_out_and_reaps(self):
        for changes in (
            {"id": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"},
            {"id": "other-publisher/Qwen3.8-27B-MLX-Serve-4bit"},
            {"loaded": False}, {"loaded": 1}, {"capabilities": ["text"]},
        ):
            with self.subTest(changes=changes), self.runtime() as runtime:
                runtime.row.update(changes)
                self.assert_unavailable("local_vision_timeout", timeout=0.25)
                runtime.http.assert_called()
                self.assert_cleaned(runtime)

    def test_foreign_listener_cannot_supply_even_a_matching_catalog(self):
        for returncode, stdout in ((0, "42420\n"), (0, "9999\n"), (1, "4242\n")):
            with self.subTest(returncode=returncode, stdout=stdout), self.runtime() as runtime:
                runtime.listener.return_value = SimpleNamespace(returncode=returncode, stdout=stdout)
                self.assert_unavailable("local_vision_timeout", timeout=0.15)
                runtime.http.assert_not_called()
                runtime.process.terminate.assert_not_called()
                runtime.process.kill.assert_not_called()
                self.assert_cleaned(runtime)

    def test_transient_catalog_errors_retry_with_remaining_budget(self):
        with self.runtime() as runtime:
            runtime.http.side_effect = [
                OSError("starting"), io.BytesIO(b"not json"), io.BytesIO(b"{}"),
                io.BytesIO(json.dumps({"data": [None, {}, runtime.row]}).encode()),
            ]
            with VISION.owned_vision_endpoint(self.state, timeout=0.5) as endpoint:
                self.assertAlmostEqual(endpoint[4], 0.2)
                self.assertEqual(runtime.http.call_count, 4)
                self.assertAlmostEqual(runtime.http.call_args.kwargs["timeout"], endpoint[4])
            self.assert_cleaned(runtime)

    def test_oversized_catalog_is_bounded_and_cleanup_still_runs(self):
        with self.runtime() as runtime:
            response = mock.MagicMock()
            response.__enter__.return_value = response
            limit = VISION.ondevice.MLX_GATEWAY_MAX_RESPONSE_BYTES
            response.read.return_value = b"x" * (limit + 1)
            runtime.http.side_effect = None
            runtime.http.return_value = response
            self.assert_unavailable("local_vision_catalog_invalid")
            response.read.assert_called_once_with(limit + 1)
            response.__exit__.assert_called_once()
            self.assert_cleaned(runtime)

    def test_invalid_child_pid_or_exited_supervisor_never_reaches_http(self):
        for line in (b"", b"not-a-pid\n", b"0\n", b"1\n", b"-2\n", b"4242\n"):
            with self.subTest(line=line), self.runtime() as runtime:
                runtime.process.stdout = io.BytesIO(line)
                if line == b"4242\n":
                    runtime.process.poll.return_value = 1
                self.assert_unavailable("local_vision_launch_failed")
                runtime.http.assert_not_called()
                self.assert_cleaned(runtime)

    def test_handshake_timeout_closes_pipe_and_reaps(self):
        with self.runtime() as runtime, mock.patch.object(
            VISION.select, "select", return_value=([], [], []),
        ):
            self.assert_unavailable("local_vision_timeout")
            runtime.http.assert_not_called()
            self.assert_cleaned(runtime)

    def test_consumer_failure_preserves_exception_and_cleans_up(self):
        with self.runtime() as runtime:
            failure = RuntimeError("inference failed")
            with self.assertRaises(RuntimeError) as raised:
                with VISION.owned_vision_endpoint(self.state, timeout=1):
                    raise failure
            self.assertIs(raised.exception, failure)
            self.assert_cleaned(runtime)

    def test_checkpoint_change_during_request_rejects_result_and_cleans_up(self):
        with self.runtime() as runtime:
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                with VISION.owned_vision_endpoint(self.state, timeout=1):
                    runtime.checkpoint_identity.return_value = {"files": "changed"}
            self.assertEqual(raised.exception.code, "local_vision_checkpoint_changed")
            self.assert_cleaned(runtime)

    def test_monitor_closes_pipe_at_deadline_while_consumer_is_active(self):
        with self.runtime() as runtime:
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                with VISION.owned_vision_endpoint(self.state, timeout=200):
                    runtime.clock.sleep(90)
                    runtime.event.wait.side_effect = [False, True]
                    runtime.monitor()
                    self.assertTrue(runtime.process.stdin.closed)
                    runtime.process.wait.assert_not_called()
            self.assertEqual(raised.exception.code, "local_vision_timeout")
            self.assert_cleaned(runtime)

    def test_monitor_cancellation_is_not_lost_when_callback_later_recovers(self):
        with self.runtime() as runtime:
            callback = mock.Mock(return_value=None)
            cancelled = InterruptedError("request cancelled")
            with self.assertRaises(InterruptedError) as raised:
                with VISION.owned_vision_endpoint(self.state, timeout=1, check_cancelled=callback):
                    callback.side_effect = cancelled
                    runtime.event.wait.side_effect = [False, True]
                    runtime.monitor()
                    self.assertTrue(runtime.process.stdin.closed)
                    callback.side_effect = None
            self.assertIs(raised.exception, cancelled)
            self.assert_cleaned(runtime)

    def test_watcher_start_failure_still_reaps_supervisor_and_releases_lock(self):
        with self.runtime() as runtime:
            runtime.watcher.ident = None
            runtime.watcher.start.side_effect = RuntimeError("thread start failed")
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                with VISION.owned_vision_endpoint(self.state, timeout=1):
                    self.fail("watcher did not start")
            self.assert_cleaned(runtime, joined=False)

    def test_cleanup_requires_successful_supervisor_exit(self):
        with self.runtime() as runtime:
            runtime.process.wait.return_value = 1
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                with VISION.owned_vision_endpoint(self.state, timeout=1):
                    pass
            self.assertEqual(raised.exception.code, "local_vision_cleanup_failed")
            command = tuple(json.loads(runtime.spawn.call_args.args[0][5]))
            runtime.reap.assert_called_once_with(4000, command)
            self.assertFalse((self.global_root / "mlx-vision-cleanup-required").exists())
            self.assert_cleaned(runtime)

    def test_unknown_cleanup_ownership_fences_future_allocations(self):
        for failure in (False, OSError("process lookup failed"), subprocess.TimeoutExpired("fake-ps", 1)):
            with self.subTest(failure=failure), self.runtime() as runtime:
                fence = self.global_root / "mlx-vision-cleanup-required"
                try:
                    runtime.process.wait.return_value = 1
                    runtime.reap.side_effect = failure if isinstance(failure, Exception) else None
                    runtime.reap.return_value = False
                    with self.assertRaises(VISION.VisionUnavailable) as raised:
                        with VISION.owned_vision_endpoint(self.state, timeout=1):
                            pass
                    self.assertEqual(raised.exception.code, "local_vision_cleanup_failed")
                    self.assert_cleaned(runtime)
                    self.assertEqual(stat.S_IMODE(fence.stat().st_mode), 0o600)
                    runtime.spawn.reset_mock()
                    runtime.memory.reset_mock()
                    runtime.probe.bind.reset_mock()
                    other_root = self.root / "other-operator"
                    with self.assertRaises(VISION.VisionUnavailable) as raised:
                        with VISION.owned_vision_endpoint(other_root, timeout=1):
                            self.fail("a different operator root bypassed the cleanup fence")
                    self.assertEqual(raised.exception.code, "local_vision_cleanup_required")
                    runtime.spawn.assert_not_called()
                    runtime.memory.assert_not_called()
                    runtime.probe.bind.assert_not_called()
                finally:
                    fence.unlink(missing_ok=True)

    def test_cleanup_timeout_escalates_only_owned_supervisor_and_group(self):
        for kill_group in (False, True):
            with self.subTest(kill_group=kill_group), self.runtime() as runtime, mock.patch.object(
                VISION.os, "killpg",
            ) as killpg:
                timeout = subprocess.TimeoutExpired("fake-supervisor", 8)
                runtime.process.wait.side_effect = [timeout, timeout, -9] if kill_group else [timeout, 0]
                if kill_group:
                    with self.assertRaises(VISION.VisionUnavailable) as raised:
                        with VISION.owned_vision_endpoint(self.state, timeout=1):
                            pass
                    self.assertEqual(raised.exception.code, "local_vision_cleanup_failed")
                    killpg.assert_called_once_with(4000, signal.SIGKILL)
                else:
                    with VISION.owned_vision_endpoint(self.state, timeout=1):
                        pass
                    killpg.assert_not_called()
                runtime.process.terminate.assert_called_once_with()
                expected = [mock.call(timeout=8.0), mock.call(timeout=8.0)]
                if kill_group:
                    expected.append(mock.call(timeout=3.0))
                self.assertEqual(runtime.process.wait.call_args_list, expected)
                self.assert_cleaned(runtime, escalated=True)

    def test_deadline_error_survives_nonzero_cleanup_status(self):
        with self.runtime() as runtime:
            runtime.process.wait.return_value = 1
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                with VISION.owned_vision_endpoint(self.state, timeout=1):
                    runtime.clock.sleep(1)
                    runtime.event.wait.side_effect = [False, True]
                    runtime.monitor()
            self.assertEqual(raised.exception.code, "local_vision_timeout")
            self.assert_cleaned(runtime)


class VisionInputBudgetTests(IsolatedVisionTestCase):
    def test_text_budget_counts_utf8_bytes_and_reserves_output_and_framing(self):
        # 32,768 context minus 1,024 output and 512 framing leaves 31,232 bytes.
        prompt = b"x" * (31232 - len("한".encode("utf-8")))
        self.assertEqual(VISION.input_byte_budget("한", []), 31229)
        VISION.check_input_budget("한", prompt, [])
        with self.assertRaises(VISION.VisionUnavailable) as raised:
            VISION.check_input_budget("한", prompt + b"x", [])
        self.assertEqual(raised.exception.code, "local_vision_input_budget_exceeded")
        VISION.subprocess.run.assert_not_called()

    def test_image_patch_budget_includes_rounding_and_pixel_scaling(self):
        image = self.root / "synthetic image.png"
        # Fixed expected token bounds: tiny images upscale to 256x256; 8192
        # squares downscale to 4096x4096. Nonmultiples need rounded patch counts.
        for width, height, image_tokens in ((1, 1, 145), (256, 256, 145), (257, 257, 164), (8192, 8192, 16705)):
            with self.subTest(width=width, height=height), mock.patch.object(
                VISION.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=f"pixelWidth: {width}\npixelHeight: {height}\n",
                ),
            ) as sips:
                prompt = b"x" * (31232 - image_tokens)
                self.assertEqual(VISION.input_byte_budget("", [image]), len(prompt))
                VISION.check_input_budget("", prompt, [image])
                with self.assertRaises(VISION.VisionUnavailable) as raised:
                    VISION.check_input_budget("", prompt + b"x", [image])
                self.assertEqual(raised.exception.code, "local_vision_input_budget_exceeded")
                expected = mock.call(
                    ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(image)],
                    capture_output=True, text=True, timeout=2, check=False,
                )
                self.assertEqual(sips.call_args_list, [expected, expected, expected])
                self.assertFalse(image.exists())  # Metadata came only from the fake adapter.

    def test_all_images_share_one_input_budget(self):
        with mock.patch.object(VISION.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="pixelWidth: 256\npixelHeight: 256\n",
        )):
            images = [self.root / "first.png", self.root / "second.png"]
            VISION.check_input_budget("", b"x" * (31232 - 290), images)
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                VISION.check_input_budget("", b"x" * (31232 - 289), images)
            self.assertEqual(raised.exception.code, "local_vision_input_budget_exceeded")

    def test_unknown_or_unsafe_dimensions_fail_closed(self):
        for returncode, stdout in (
            (1, "pixelWidth: 256\npixelHeight: 256"),
            (0, "pixelWidth: 256"), (0, "unreadable image"),
            (0, "pixelWidth: 0\npixelHeight: 256"),
            (0, "pixelWidth: -1\npixelHeight: 256"),
            (0, "pixelWidth: 201\npixelHeight: 1"),
        ):
            with self.subTest(returncode=returncode, stdout=stdout), mock.patch.object(
                VISION.subprocess, "run", return_value=SimpleNamespace(returncode=returncode, stdout=stdout),
            ), self.assertRaises(VISION.VisionUnavailable) as raised:
                VISION.check_input_budget("", b"", [self.root / "fake.png"])
            self.assertEqual(raised.exception.code, "local_vision_input_budget_exceeded")

    def test_exhausted_system_or_image_budget_cannot_return_a_negative_allowance(self):
        self.assertEqual(VISION.input_byte_budget("x" * 31232, []), 0)
        VISION.check_input_budget("x" * 31232, b"", [])
        with self.assertRaises(VISION.VisionUnavailable) as raised:
            VISION.input_byte_budget("x" * 31233, [])
        self.assertEqual(raised.exception.code, "local_vision_input_budget_exceeded")
        with mock.patch.object(VISION.subprocess, "run", return_value=SimpleNamespace(
            returncode=0, stdout="pixelWidth: 8192\npixelHeight: 8192\n",
        )), self.assertRaises(VISION.VisionUnavailable) as raised:
            VISION.input_byte_budget("", [self.root / "first.png", self.root / "second.png"])
        self.assertEqual(raised.exception.code, "local_vision_input_budget_exceeded")


class VisionStrandedGroupTests(IsolatedVisionTestCase):
    COMMAND = ("/fake/mlx-serve", "--serve", "--ctx-size", "32768")

    @contextmanager
    def group(self, results):
        clock = FakeClock()
        with (
            mock.patch.object(VISION, "time", clock),
            mock.patch.object(VISION.subprocess, "run", side_effect=[
                SimpleNamespace(returncode=code, stdout=stdout) for code, stdout in results
            ]) as probe,
            mock.patch.object(VISION.os, "killpg") as killpg,
        ):
            yield probe, killpg, clock

    def test_exact_command_and_group_are_verified_before_killing_live_orphan(self):
        with self.group([
            (0, "4242\n"), (0, "4000 S " + " ".join(self.COMMAND)),
            (0, "S\n"), (1, ""),
        ]) as (probe, killpg, clock):
            def kill_verified(group, sig):
                self.assertEqual(probe.call_count, 2)
                self.assertEqual((group, sig), (4000, signal.SIGKILL))

            killpg.side_effect = kill_verified
            self.assertTrue(VISION._reap_stranded_group(4000, self.COMMAND))
            killpg.assert_called_once_with(4000, signal.SIGKILL)
            self.assertEqual([call.args[0] for call in probe.call_args_list[:2]], [
                ["/usr/bin/pgrep", "-g", "4000"],
                ["/bin/ps", "-ww", "-p", "4242", "-o", "pgid=,stat=,args="],
            ])
            self.assertGreater(clock.now, 1000)

    def test_absent_or_zombie_group_needs_no_signal(self):
        for results in (
            [(1, "")],
            [(0, "4242"), (0, "4000 Z defunct"), (0, "Z")],
        ):
            with self.subTest(results=results), self.group(results) as (_probe, killpg, _clock):
                self.assertTrue(VISION._reap_stranded_group(4000, self.COMMAND))
                killpg.assert_not_called()

    def test_unknown_or_mixed_group_ownership_never_receives_signal(self):
        expected = " ".join(self.COMMAND)
        for results in (
            [(2, "")], [(0, "")], [(0, "not-a-pid")],
            [(0, "4242"), (1, "")],
            [(0, "4242"), (0, "4001 S " + expected)],
            [(0, "4242"), (0, "4000 S " + expected + " --foreign")],
            [(0, "4242"), (0, "malformed")],
            [(0, "4242\n4243"), (0, "4000 S " + expected + "\n4000 S /foreign/server")],
        ):
            with self.subTest(results=results), self.group(results) as (_probe, killpg, _clock):
                self.assertFalse(VISION._reap_stranded_group(4000, self.COMMAND))
                killpg.assert_not_called()

    def test_kill_without_observed_exit_does_not_confirm_cleanup(self):
        with self.group([
            (0, "4242"), (0, "4000 S " + " ".join(self.COMMAND)),
        ]) as (probe, killpg, clock):
            initial = probe.side_effect

            def still_running(*_args, **_kwargs):
                return next(initial, SimpleNamespace(returncode=0, stdout="S"))

            probe.side_effect = still_running
            self.assertFalse(VISION._reap_stranded_group(4000, self.COMMAND))
            killpg.assert_called_once_with(4000, signal.SIGKILL)
            self.assertGreaterEqual(clock.now, 1003)


class VisionAdmissionTests(IsolatedVisionTestCase):
    def test_admission_refusals_return_without_penalizing_shared_fallback_circuit(self):
        # Import the actual worker with user-path discovery and all I/O disabled.
        spec = importlib.util.spec_from_file_location("vision_admission_worker", ROOT / "scripts/auto-reply-worker.py")
        worker = importlib.util.module_from_spec(spec)
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch("shutil.which", return_value=None),
            mock.patch("sqlite3.connect", side_effect=AssertionError("unexpected database access")),
            mock.patch.object(VISION.urllib.request, "urlopen", side_effect=AssertionError("unexpected HTTP")),
        ):
            spec.loader.exec_module(worker)
            with (
                mock.patch.object(worker, "_reply_fallback_candidates", return_value=["test-vision", "test-next"]),
                mock.patch.object(worker, "_raise_if_job_aborted"),
                mock.patch.object(worker, "_open_fallback_lease", return_value={"lease_token": "test-lease", "retry_at": 1001}),
                mock.patch.object(worker, "_close_model_lease") as penalize,
                mock.patch.object(worker, "_classify_model_failure") as classify,
            ):
                self.assertIn("local_vision_input_budget_exceeded", VISION.ADMISSION_CODES)
                self.assertIn("local_vision_memory_insufficient", VISION.ADMISSION_CODES)
                self.assertNotIn("local_vision_cleanup_failed", VISION.ADMISSION_CODES)
                for code in sorted(VISION.ADMISSION_CODES):
                    with self.subTest(code=code):
                        generate = mock.Mock(return_value=(1, b"", code.encode("ascii")))
                        result = worker._model_fallback_chain("test-primary", generate)
                        self.assertEqual(result["stderr"], code.encode("ascii"))
                        self.assertEqual(result["lease_token"], "test-lease")
                        generate.assert_called_once_with("test-vision")
                classify.assert_not_called()
                penalize.assert_not_called()


class VisionCheckpointTests(IsolatedVisionTestCase):
    def test_verified_cache_skips_hashing_until_same_size_checkpoint_is_replaced(self):
        model_dir = self.root / "synthetic-checkpoint"
        model_dir.mkdir()
        self.state.mkdir(mode=0o700)
        core = model_dir / "core.bin"
        core.write_bytes(b"good")
        manifest = {
            "schema_version": 1, "repository": VISION.MODEL_ID, "revision": VISION.REVISION,
            "files": [{"path": "core.bin", "bytes": 4, "algorithm": "sha256",
                       "digest": hashlib.sha256(b"good").hexdigest()}],
        }
        callback = mock.Mock()
        with (
            mock.patch.object(VISION, "load_manifest", return_value=manifest),
            mock.patch.object(VISION, "verify_model", wraps=VISION.verify_model) as verify,
        ):
            _, identity = VISION._verify_checkpoint(model_dir, self.state, callback)
            verify.assert_called_once_with(model_dir, manifest, check_cancelled=callback)
            callback.assert_called()
            cache = self.state / "mlx-vision-verified-core.json"
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
            self.assertEqual(json.loads(cache.read_text()), identity)
            self.assertEqual(VISION._verify_checkpoint(model_dir, self.state, callback), (manifest, identity))
            self.assertEqual(verify.call_count, 1)

            # Preserve size and mtime: replacing the inode must still invalidate
            # the cache and rehash before accepting this synthetic checkpoint.
            original = core.stat()
            replacement = model_dir / "replacement.bin"
            replacement.write_bytes(b"evil")
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            replacement.replace(core)
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                VISION._verify_checkpoint(model_dir, self.state, callback)
            self.assertEqual(raised.exception.code, "local_vision_checkpoint_mismatch")
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(json.loads(cache.read_text()), identity)
            self.assertEqual(list(self.state.iterdir()), [cache])


class VisionTransportTests(IsolatedVisionTestCase):
    @contextmanager
    def connection(self):
        clock = FakeClock()
        response = mock.Mock(status=200, headers={"Content-Type": "application/json"}, reason="OK")
        response.read.return_value = b"synthetic response"
        connection = mock.Mock(getresponse=mock.Mock(return_value=response))
        connection.sock.getsockname.return_value = ("127.0.0.1", 51000)
        supervisor = mock.Mock(poll=mock.Mock(return_value=None))
        with (
            mock.patch.object(VISION, "time", clock),
            mock.patch.object(VISION.http.client, "HTTPConnection", return_value=connection) as factory,
            mock.patch.object(VISION.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout="p4242\nn127.0.0.1:11237->127.0.0.1:51000\n",
            )) as peer,
        ):
            yield SimpleNamespace(
                clock=clock, response=response, connection=connection,
                supervisor=supervisor, factory=factory, peer=peer,
            )

    def request(self, path="/v1/chat/completions"):
        return VISION.urllib.request.Request(
            "http://127.0.0.1:11237" + path,
            data=b'{"image":"synthetic test pixels"}' if path.endswith("completions") else None,
            headers={"Authorization": "Bearer synthetic-key"},
        )

    def test_connected_socket_is_attested_before_any_key_or_pixels_are_written(self):
        for path in ("/v1/models", "/v1/chat/completions"):
            with self.subTest(path=path), self.connection() as runtime:
                order = []
                runtime.connection.connect.side_effect = lambda: order.append("connect")
                peer_result = runtime.peer.return_value

                def attest(*_args, **_kwargs):
                    order.append("attest")
                    runtime.connection.request.assert_not_called()
                    return peer_result

                runtime.peer.side_effect = attest
                runtime.connection.request.side_effect = lambda *_a, **_k: order.append("send")
                transport = VISION._bound_transport(4242, runtime.supervisor, lambda: None)
                request = self.request(path)
                with transport(request, timeout=0.5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(17), b"synthetic response")
                    runtime.response.read.assert_called_once_with(17)
                    runtime.connection.close.assert_not_called()
                self.assertEqual(order, ["connect", "attest", "send"])
                runtime.factory.assert_called_once_with("127.0.0.1", 11237, timeout=0.5)
                runtime.peer.assert_called_once_with(
                    ["/usr/sbin/lsof", "-nP", "-a", "-p", "4242", "-iTCP:11237", "-sTCP:ESTABLISHED", "-Fn"],
                    capture_output=True, text=True, timeout=1.0, check=False,
                )
                runtime.connection.request.assert_called_once_with(
                    request.get_method(), path, body=request.data,
                    headers={"Authorization": "Bearer synthetic-key"},
                )
                runtime.response.close.assert_called_once_with()
                runtime.connection.close.assert_called_once_with()

    def test_each_request_reattests_its_new_client_socket(self):
        with self.connection() as runtime:
            transport = VISION._bound_transport(4242, runtime.supervisor, lambda: None)
            with transport(self.request("/v1/models"), timeout=0.03):
                pass
            runtime.connection.request.reset_mock()
            # The second connection has a different ephemeral port. Proof for the
            # first connection must never authorize pixels on this replacement.
            runtime.connection.sock.getsockname.return_value = ("127.0.0.1", 51001)
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                transport(self.request(), timeout=0.03)
            self.assertEqual(raised.exception.code, "local_vision_connection_unattested")
            self.assertEqual(runtime.factory.call_count, 2)
            runtime.connection.request.assert_not_called()
            self.assertEqual(runtime.connection.close.call_count, 2)

    def test_near_matching_or_failed_peer_identity_never_sends_credentials(self):
        for returncode, stdout in (
            (0, "n127.0.0.1:11237->127.0.0.1:510000\n"),
            (0, "n127.0.0.1:51000->127.0.0.1:11237\n"),
            (0, "n127.0.0.1:11234->127.0.0.1:51000\n"),
            (1, "n127.0.0.1:11237->127.0.0.1:51000\n"),
        ):
            with self.subTest(returncode=returncode, stdout=stdout), self.connection() as runtime:
                runtime.peer.return_value = SimpleNamespace(returncode=returncode, stdout=stdout)
                transport = VISION._bound_transport(4242, runtime.supervisor, lambda: None)
                with self.assertRaises(VISION.VisionUnavailable) as raised:
                    transport(self.request(), timeout=0.03)
                self.assertEqual(raised.exception.code, "local_vision_connection_unattested")
                runtime.connection.request.assert_not_called()
                runtime.connection.close.assert_called_once_with()
                self.assertLess(runtime.clock.now, 1000.05)

    def test_endpoint_allowlist_rejects_redirect_targets_before_connecting(self):
        urls = (
            "https://127.0.0.1:11237/v1/models",
            "http://localhost:11237/v1/models",
            "http://127.0.0.1:11234/v1/models",
            "http://user@127.0.0.1:11237/v1/models",
            "http://example.invalid/v1/models",
            "http://127.0.0.1:11237/v1/models?redirect=1",
            "http://127.0.0.1:11237/v1/models#fragment",
            "http://127.0.0.1:11237/v1/other",
        )
        with self.connection() as runtime:
            transport = VISION._bound_transport(4242, runtime.supervisor, lambda: None)
            for url in urls:
                with self.subTest(url=url), self.assertRaises(VISION.VisionUnavailable) as raised:
                    transport(VISION.urllib.request.Request(url), timeout=0.5)
                self.assertEqual(raised.exception.code, "local_vision_endpoint_invalid")
            runtime.factory.assert_not_called()

    def test_owner_exit_and_cancellation_after_attestation_close_without_sending(self):
        with self.connection() as runtime:
            runtime.supervisor.poll.return_value = 0
            transport = VISION._bound_transport(4242, runtime.supervisor, lambda: None)
            with self.assertRaises(VISION.VisionUnavailable) as raised:
                transport(self.request(), timeout=1)
            self.assertEqual(raised.exception.code, "local_vision_owner_gone")
            runtime.connection.connect.assert_not_called()
            runtime.connection.request.assert_not_called()
            runtime.connection.close.assert_called_once_with()
        with self.connection() as runtime:
            cancelled = InterruptedError("cancelled after attestation")
            callback = mock.Mock(side_effect=[None, cancelled])
            transport = VISION._bound_transport(4242, runtime.supervisor, callback)
            with self.assertRaises(InterruptedError) as raised:
                transport(self.request(), timeout=1)
            self.assertIs(raised.exception, cancelled)
            runtime.peer.assert_called_once()
            runtime.connection.request.assert_not_called()
            runtime.connection.close.assert_called_once_with()

    def test_attestation_process_failure_closes_without_sending(self):
        with self.connection() as runtime:
            runtime.peer.side_effect = subprocess.TimeoutExpired("fake-lsof", 1)
            transport = VISION._bound_transport(4242, runtime.supervisor, lambda: None)
            with self.assertRaises(subprocess.TimeoutExpired):
                transport(self.request(), timeout=1)
            runtime.connection.request.assert_not_called()
            runtime.connection.close.assert_called_once_with()

    def test_http_errors_are_returned_without_following_redirects_and_are_closeable(self):
        for status in (302, 401, 500):
            with self.subTest(status=status), self.connection() as runtime:
                runtime.response.status = status
                runtime.response.headers["Location"] = "http://example.invalid/collect"
                transport = VISION._bound_transport(4242, runtime.supervisor, lambda: None)
                with self.assertRaises(VISION.urllib.error.HTTPError) as raised:
                    transport(self.request(), timeout=1)
                self.assertEqual(raised.exception.code, status)
                raised.exception.close()
                runtime.factory.assert_called_once()
                runtime.connection.request.assert_called_once()
                runtime.response.close.assert_called_once_with()
                runtime.connection.close.assert_called_once_with()


class VisionLockTests(IsolatedVisionTestCase):
    def test_lock_is_private_exclusive_and_released_on_exception(self):
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            with VISION._vision_lock(self.state):
                lock = self.state / "mlx-vision-request.lock"
                self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
                with self.assertRaises(VISION.VisionUnavailable) as raised:
                    with VISION._vision_lock(self.state):
                        self.fail("concurrent lock acquired")
                self.assertEqual(raised.exception.code, "local_vision_busy")
                raise RuntimeError("request failed")
        lock.chmod(0o644)
        with VISION._vision_lock(self.state):
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_symlinked_root_and_symlinked_or_hardlinked_lock_are_rejected(self):
        target = self.root / "target"
        target.mkdir()
        self.state.symlink_to(target, target_is_directory=True)
        with self.assertRaises(VISION.VisionUnavailable) as raised:
            with VISION._vision_lock(self.state):
                self.fail("symlinked state acquired")
        self.assertEqual(raised.exception.code, "local_vision_state_unsafe")
        self.assertEqual(list(target.iterdir()), [])
        self.state.unlink()
        self.state.mkdir()
        lock = self.state / "mlx-vision-request.lock"
        victim = target / "sentinel"
        victim.write_bytes(b"unchanged")
        victim.chmod(0o644)
        for link in (lambda: lock.symlink_to(victim), lambda: os.link(victim, lock)):
            with self.subTest(link=link):
                link()
                with self.assertRaises(VISION.VisionUnavailable) as raised:
                    with VISION._vision_lock(self.state):
                        self.fail("linked lock acquired")
                self.assertEqual(raised.exception.code, "local_vision_state_unsafe")
                self.assertEqual(victim.read_bytes(), b"unchanged")
                self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o644)
                lock.unlink()

    def test_foreign_owner_or_nonregular_lock_closes_descriptor(self):
        for mode, uid in ((stat.S_IFREG | 0o600, os.getuid() + 1), (stat.S_IFIFO | 0o600, os.getuid())):
            with self.subTest(mode=mode, uid=uid), mock.patch.object(
                VISION.os, "fstat", return_value=SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=1),
            ), mock.patch.object(VISION.os, "close", wraps=os.close) as close:
                with self.assertRaises(VISION.VisionUnavailable) as raised:
                    with VISION._vision_lock(self.state):
                        self.fail("unsafe lock acquired")
                self.assertEqual(raised.exception.code, "local_vision_state_unsafe")
                close.assert_called_once()
                descriptor = close.call_args.args[0]
            with self.assertRaises(OSError):
                os.fstat(descriptor)


class VisionSupervisorTests(IsolatedVisionTestCase):
    @contextmanager
    def supervisor(self):
        child = mock.Mock(pid=4242, poll=mock.Mock(return_value=None), wait=mock.Mock(return_value=0))
        clock = FakeClock()
        stdin = mock.Mock(buffer=io.BytesIO(), fileno=mock.Mock(return_value=91))
        stdout = io.StringIO()
        handlers = {}
        with ExitStack() as stack:
            def patch(owner, name, **kwargs):
                return stack.enter_context(mock.patch.object(owner, name, **kwargs))

            patch(VISION, "time", new=clock)
            patch(VISION.signal, "signal", side_effect=lambda signum, callback: handlers.update({signum: callback}))
            patch(VISION.sys, "stdin", new=stdin)
            patch(VISION.sys, "stdout", new=stdout)
            spawn = patch(VISION.subprocess, "Popen", return_value=child)
            select = patch(VISION.select, "select", side_effect=lambda _r, _w, _e, timeout:
                           ([], [], []) if timeout == 0 else ([stdin.buffer], [], []))
            read = patch(VISION.os, "read", return_value=b"")
            yield SimpleNamespace(
                child=child, clock=clock, handlers=handlers, spawn=spawn, select=select,
                read=read, stdout=stdout, log=self.root / "server.log",
            )

    def test_pipe_eof_terminates_and_reaps_only_its_own_sandboxed_child(self):
        with self.supervisor() as runtime:
            command = ["/fake/mlx-serve", "--serve"]
            self.assertEqual(VISION._supervise(1010, runtime.log, command), 0)
            self.assertEqual(runtime.stdout.getvalue(), "4242\n")
            runtime.read.assert_called_once_with(91, 1)
            runtime.child.terminate.assert_called_once_with()
            runtime.child.wait.assert_called_once_with(timeout=3.0)
            runtime.child.kill.assert_not_called()
            args, options = runtime.spawn.call_args
            self.assertEqual(args[0], ["/usr/bin/sandbox-exec", "-p", VISION.SANDBOX, *command])
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["stderr"], subprocess.STDOUT)
            self.assertTrue(options["close_fds"])
            self.assertTrue(options["stdout"].closed)

    def test_deadline_terminates_even_when_parent_pipe_remains_open(self):
        with self.supervisor() as runtime:
            def no_eof(_read, _write, _error, timeout):
                runtime.clock.sleep(timeout)
                return [], [], []

            runtime.select.side_effect = no_eof
            self.assertEqual(VISION._supervise(1000.25, runtime.log, ["/fake/mlx"]), 0)
            runtime.read.assert_not_called()
            runtime.child.terminate.assert_called_once_with()
            runtime.child.wait.assert_called_once_with(timeout=3.0)

    def test_unresponsive_child_is_killed_and_reaped_after_grace_period(self):
        with self.supervisor() as runtime:
            runtime.child.wait.side_effect = [subprocess.TimeoutExpired("fake-mlx", 3), 0]
            self.assertEqual(VISION._supervise(1010, runtime.log, ["/fake/mlx"]), 0)
            self.assertEqual(runtime.child.method_calls, [
                mock.call.poll(), mock.call.poll(), mock.call.terminate(),
                mock.call.wait(timeout=3.0), mock.call.kill(), mock.call.wait(timeout=3.0),
            ])

    def test_term_and_interrupt_handlers_reap_child(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signum=signum), self.supervisor() as runtime:
                def interrupt(_r, _w, _e, timeout):
                    if timeout == 0:
                        return [], [], []
                    runtime.handlers[signum](signum, None)

                runtime.select.side_effect = interrupt
                def cleanup_ignores_signals():
                    self.assertEqual(runtime.handlers[signal.SIGTERM], signal.SIG_IGN)
                    self.assertEqual(runtime.handlers[signal.SIGINT], signal.SIG_IGN)

                runtime.child.terminate.side_effect = cleanup_ignores_signals
                self.assertEqual(VISION._supervise(1010, runtime.log, ["/fake/mlx"]), 1)
                runtime.child.terminate.assert_called_once_with()
                runtime.child.wait.assert_called_once_with(timeout=3.0)
            runtime.log.unlink()

    def test_exited_child_is_not_signalled(self):
        with self.supervisor() as runtime:
            runtime.child.poll.return_value = 0
            self.assertEqual(VISION._supervise(1010, runtime.log, ["/fake/mlx"]), 0)
            self.assertEqual(runtime.select.call_count, 1)  # Only the pre-spawn EOF check.
            runtime.child.terminate.assert_not_called()
            runtime.child.kill.assert_not_called()

    def test_spawn_failure_closes_log_without_attempting_child_cleanup(self):
        with self.supervisor() as runtime:
            runtime.spawn.side_effect = OSError("spawn failed")
            self.assertEqual(VISION._supervise(1010, runtime.log, ["/fake/mlx"]), 1)
            self.assertTrue(runtime.spawn.call_args.kwargs["stdout"].closed)
            runtime.child.terminate.assert_not_called()
            runtime.child.kill.assert_not_called()

    def test_expired_deadline_or_existing_pipe_eof_never_spawns(self):
        for expired in (False, True):
            with self.subTest(expired=expired), self.supervisor() as runtime:
                runtime.select.side_effect = None
                runtime.select.return_value = ([io.BytesIO()], [], [])
                deadline = runtime.clock.now if expired else runtime.clock.now + 1
                self.assertEqual(VISION._supervise(deadline, runtime.log, ["/fake/mlx"]), 0)
                runtime.spawn.assert_not_called()
                self.assertFalse(runtime.log.exists())
                runtime.child.terminate.assert_not_called()
                runtime.child.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
