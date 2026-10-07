"""Request-bound telemetry through a synthetic local transport; no live models."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import alden_voice as voice
from alden_abort import AbortController


class Response(io.BytesIO):
    def __init__(self, raw, content_type="text/plain"):
        super().__init__(raw)
        self.headers = {"Content-Type": content_type}


def stream(ids=("chatcmpl-fixed", "chatcmpl-fixed")):
    chunks = [
        {"id": ids[0], "model": voice.QWEN38_27B_MODEL_ID.removeprefix("mlx/"),
         "choices": [{"delta": {"content": "확인"}, "finish_reason": None}]},
        {"id": ids[1], "choices": [{"delta": {"content": "했습니다."}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 42, "completion_tokens": 3}},
    ]
    return b"".join(b"data: " + json.dumps(c, ensure_ascii=False).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


class VoiceMetricsTests(unittest.TestCase):
    def run_generation(self, *, unavailable=False, ids=("chatcmpl-fixed", "chatcmpl-fixed")):
        observed = []
        metric_reads = []
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = AbortController(root).token()
            turn = voice.VoiceTurn("b" * 32, 4, 8, "text", voice.VoiceTurnToken(token))
            adapter = voice.LocalMlxLlm(state_root=root)

            def transport(request, **kwargs):
                observed.append(request)
                if request.full_url.endswith("/metrics"):
                    if unavailable:
                        raise urllib.error.HTTPError(request.full_url, 503, "disabled", {}, None)
                    metric_reads.append(1)
                    return Response(("vllm:num_requests_running 0\n"
                                     f"vllm:request_success_total {10 + len(metric_reads)}\n"
                                     "mlx_serve:mlx_active_bytes 123456\n").encode())
                if request.full_url.endswith("/models"):
                    return Response(json.dumps({"data": [{"id": adapter.model.removeprefix("mlx/"), "loaded": True, "state": "ready"}]}).encode(), "application/json")
                return Response(stream(ids), "text/event-stream")

            with mock.patch.object(voice, "_local_urlopen", side_effect=transport):
                result = adapter.generate_for_turn("현재 발화", turn.token, turn=turn)
            metrics = json.loads(json.dumps(adapter.last_metrics))
            # A direct later request must not inherit this conversation binding.
            self.assertIsNone(adapter._turn_context.get())
            return result, metrics, observed

    def test_actual_response_id_and_engine_observations_keep_input_turn_identity(self):
        result, metrics, requests = self.run_generation()
        self.assertEqual(result, "확인했습니다.")
        trace = metrics["request"]
        self.assertEqual((trace["conversation_id"], trace["turn_id"], trace["context_version"]), ("b" * 32, 4, 8))
        self.assertEqual(trace["state"], "completed")
        self.assertFalse(trace["cancelled"])
        self.assertGreaterEqual(trace["completed_at_unix"], trace["requested_at_unix"])
        self.assertEqual(metrics["backend_request_id"], "chatcmpl-fixed")
        self.assertEqual(metrics["usage"]["completion_tokens"], 3)
        self.assertEqual(metrics["engine_before"]["state"], "connected")
        self.assertEqual(metrics["engine_after"]["values"]["vllm:request_success_total"], 12)
        self.assertIn("engine_aggregate", metrics["engine_after"]["scope"])
        post = next(r for r in requests if r.get_method() == "POST")
        headers = {k.lower(): v for k, v in post.header_items()}
        self.assertEqual(headers["x-alden-conversation-id"], "b" * 32)
        self.assertEqual(headers["x-alden-request-id"], trace["local_request_id"])

    def test_disconnected_metrics_remain_unknown_and_do_not_block_valid_inference(self):
        result, metrics, _ = self.run_generation(unavailable=True)
        self.assertEqual(result, "확인했습니다.")
        for phase in ("engine_before", "engine_after"):
            self.assertEqual(metrics[phase]["state"], "unavailable")
            self.assertIsNone(metrics[phase]["values"])

    def test_mixed_request_ids_in_one_stream_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "local_llm_stream_request_mismatch"):
            self.run_generation(ids=("chatcmpl-old", "chatcmpl-new"))

    def test_read_only_metrics_use_the_actual_cancellable_transport(self):
        with TemporaryDirectory() as tmp:
            token = AbortController(Path(tmp)).token()
            request = voice.urllib.request.Request("http://127.0.0.1:11234/metrics")
            request._alden_abort_token = token
            with mock.patch("alden_local_http.http.client.HTTPConnection") as connection:
                connection.return_value.getresponse.return_value.status = 200
                with voice._local_urlopen(request, timeout=.35):
                    pass
            self.assertEqual(connection.call_args.args, ("127.0.0.1", 11234))
            self.assertEqual(connection.return_value.request.call_args.args[:2], ("GET", "/metrics"))

    def test_metrics_cannot_write_or_escape_the_fixed_inference_service(self):
        with TemporaryDirectory() as tmp:
            token = AbortController(Path(tmp)).token()
            cases = [
                ("http://127.0.0.1:11234/metrics", b"write"),
                ("http://127.0.0.1:11236/metrics", None),
                ("https://outside.example/metrics", None),
                ("http://127.0.0.1:11234/metrics?target=other", None),
            ]
            for url, data in cases:
                with self.subTest(url=url), mock.patch("alden_local_http.http.client.HTTPConnection") as connection:
                    request = voice.urllib.request.Request(url, data=data)
                    request._alden_abort_token = token
                    with self.assertRaisesRegex(ValueError, "local_llm_endpoint_invalid"):
                        with voice._local_urlopen(request, timeout=.35):
                            pass
                    connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
