import importlib.util
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_metrics(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "auto_reply_metrics.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class MetricsSideChannelTests(unittest.TestCase):
    def test_metric_write_failure_never_reaches_the_caller(self):
        perf = load_metrics("auto_reply_metrics_io_failure_test")

        @perf.timed("supervisor.status_write")
        def service_step():
            return {"ok": True}

        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(
                os.environ,
                {"OPENKAKAO_PERF_METRICS": "1", "OPENKAKAO_PERF_METRICS_FILE": raw},
            ):
                self.assertEqual(service_step(), {"ok": True})

    @unittest.skipUnless(hasattr(signal, "setitimer"), "needs setitimer")
    def test_shutdown_raised_during_metric_write_reaches_the_caller(self):
        perf = load_metrics("auto_reply_metrics_shutdown_test")

        @perf.timed("supervisor.status_write")
        def service_step():
            return {"ok": True}

        def request_shutdown(_signum, _frame):
            raise SystemExit(143)

        with tempfile.TemporaryDirectory() as raw:
            sink = Path(raw) / "metrics.fifo"
            os.mkfifo(sink)
            previous = signal.signal(signal.SIGALRM, request_shutdown)
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "OPENKAKAO_PERF_METRICS": "1",
                        "OPENKAKAO_PERF_METRICS_FILE": str(sink),
                    },
                ):
                    with self.assertRaises(SystemExit) as raised:
                        signal.setitimer(signal.ITIMER_REAL, 0.05)
                        service_step()
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)
        self.assertEqual(raised.exception.code, 143)


if __name__ == "__main__":
    unittest.main()
