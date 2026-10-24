"""Isolated, real-socket Responses/SSE regression for Alden media follow-ups.

No external calls, installed service changes, Kakao adapters, or model usage.
The socket target is a private ephemeral loopback HTTP test server; only the
test process redirects the fixed 10101 router connection to that test port.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SELECTED_SCRIPTS = Path(os.environ.get("ALDEN_TEST_PRODUCT_SCRIPTS", str(REPO / "scripts")))
sys.path.insert(0, str(SELECTED_SCRIPTS))

import alden_local_http
from alden_abort import AbortController
from alden_file_content import extract_document, validated_context
from alden_model_routes import DEFAULT_MODEL
from alden_routed_llm import OpenCodexLlm

IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
ROUTE = {"models": [{"id": DEFAULT_MODEL, "selectable": True, "local": False, "efforts": ["high"]}]}
REAL_CONNECTION = http.client.HTTPConnection


class LocalRouter(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def do_POST(self):
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        user_parts = request["input"][-1]["content"]
        text = user_parts[0]["text"]
        case = "multiline" if "CASE:multiline" in text else "compact"
        with self.server.capture_lock:
            self.server.requests[case] = request

        response = {"status": "completed", "model": DEFAULT_MODEL,
                    "id": "synthetic-loopback-response", "usage": {"output_tokens": 3},
                    "output": [{"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": "로컬 파일·사진 후속 답변"}]}]}
        events = [
            {"type": "response.output_text.delta", "delta": "검증 중"},
            {"type": "response.completed", "response": response},
        ]
        frames = []
        for event in events:
            encoded = json.dumps(event, ensure_ascii=False, indent=2 if case == "multiline" else None)
            frames.append(b"".join(b"data: " + line.encode("utf-8") + b"\n"
                                   for line in encoded.splitlines()) + b"\n")
        body = b"".join(frames)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        # Break SSE field lines across real HTTP chunk boundaries.
        for offset in range(0, len(body), 13):
            chunk = body[offset:offset + 13]
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class RealHttpMediaFollowupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalRouter)
        cls.server.capture_lock = threading.Lock()
        cls.server.requests = {}
        cls.runner = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.runner.start()

        def redirect_fixed_router(host, port=None, *args, **kwargs):
            if host != "127.0.0.1" or port != 10101:
                raise AssertionError("test attempted to contact a non-test endpoint")
            return REAL_CONNECTION("127.0.0.1", cls.server.server_port, *args, **kwargs)

        cls.socket_redirect = patch.object(
            alden_local_http.http.client, "HTTPConnection", side_effect=redirect_fixed_router)
        cls.socket_redirect.start()

    @classmethod
    def tearDownClass(cls):
        cls.socket_redirect.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.runner.join(timeout=2)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="alden-http-media-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.picture = self.root / "illustration.jpg"
        self.picture.write_bytes(IMAGE)
        file_bytes = "검증할 파일: 사진의 항목을 42개로 기록함.".encode()
        extracted = extract_document(file_bytes, "records.txt")
        file_digest = hashlib.sha256(file_bytes).hexdigest()
        self.context = {**extracted, "evidence_id": "file:" + file_digest,
                        "bytes_sha256": file_digest, "filename": "records.txt",
                        "chat_id": 42, "log_id": 99, "author_id": 7,
                        "attachment_sha256": file_digest}
        self.assertIsNotNone(validated_context(self.context))

    def request(self, case):
        adapter = OpenCodexLlm(DEFAULT_MODEL, "high", self.root)
        system = "답변용 검증 파일 컨텍스트: " + json.dumps(self.context, ensure_ascii=False)
        user = f"CASE:{case} 아까 첨부한 파일 내용과 사진을 함께 설명해줘."
        token = AbortController(self.root).token()
        try:
            with patch("alden_routed_llm.catalog", return_value=ROUTE):
                result = adapter.generate_messages(system, user, token,
                                                   image_paths=(self.picture,), timeout=5)
        except Exception:
            print("FAILURE_METRICS", json.dumps(adapter.last_metrics, ensure_ascii=False),
                  file=sys.stderr)
            health = self.root / "model-route-health.json"
            print("ROUTE_HEALTH", health.read_text() if health.exists() else "absent",
                  file=sys.stderr)
            raise
        self.assertEqual(result, "로컬 파일·사진 후속 답변")
        self.assertEqual(adapter.last_metrics["request"]["state"], "completed")
        self.assertEqual(adapter.last_metrics["images"][0]["sha256"], hashlib.sha256(IMAGE).hexdigest())
        request = self.server.requests[case]
        self.assertEqual(request["model"], DEFAULT_MODEL)
        self.assertEqual(request["reasoning"], {"effort": "high"})
        self.assertTrue(request["stream"])
        self.assertEqual(request["input"][0]["role"], "system")
        self.assertIn(self.context["evidence_id"], request["input"][0]["content"])
        parts = request["input"][-1]["content"]
        self.assertEqual(parts[0]["type"], "input_text")
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(parts[1]["image_url"].split(",", 1)[1]), IMAGE)
        health = json.loads((self.root / "model-route-health.json").read_text())
        self.assertTrue(health[DEFAULT_MODEL]["success"])

    def test_compact_sse_over_real_chunked_http(self):
        self.request("compact")

    def test_multiline_sse_over_real_chunked_http(self):
        self.request("multiline")


if __name__ == "__main__":
    unittest.main(verbosity=2)
