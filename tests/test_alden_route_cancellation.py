"""Cancelled media turns must preserve the last provider health observation."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from alden_abort import AbortController, AldenCancelled
from alden_model_routes import DEFAULT_MODEL, catalog as model_catalog
from alden_routed_llm import OpenCodexLlm


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeJsonHTTP(io.BytesIO):
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload, token=None):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.token = token

    def read(self, limit):
        data = super().read(limit)
        if self.token is not None:
            self.token.cancel()  # Response arrived, but the turn was aborted.
        return data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


class CancelledMediaRouteHealthTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="alden-media-cancel-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = self.root / "synthetic.png"
        self.image.write_bytes(PNG)
        self.adapter = OpenCodexLlm(DEFAULT_MODEL, "high", self.root)
        self.selected = {"models": [{"id": DEFAULT_MODEL, "selectable": True,
                                     "local": False, "efforts": ["high"]}]}
        self.completed = {"model": DEFAULT_MODEL, "status": "completed", "id": "fake-response",
                          "output": [{"type": "message", "role": "assistant",
                                      "content": [{"type": "output_text", "text": "fake answer"}]}]}

    def generate(self, token, cancel_during_read=False):
        response = FakeJsonHTTP(self.completed, token if cancel_during_read else None)
        with patch("alden_routed_llm.catalog", return_value=self.selected), \
                patch("alden_routed_llm.CancellableLocalResponse", return_value=response):
            return self.adapter.generate_messages("fake system", "fake question", token,
                                                  image_paths=(self.image,))

    def observed_route(self):
        # No real HTTP. This fixture exercises the actual catalog status logic.
        def fetcher(_url):
            return {"models": [{"id": DEFAULT_MODEL,
                                 "supported_reasoning_levels": ["high"]}], "data": []}
        return model_catalog(self.root, fetcher=fetcher)["models"][0]

    def test_cancelled_media_completion_does_not_replace_prior_route_success(self):
        self.assertEqual(self.generate(AbortController(self.root).token()), "fake answer")
        health_path = self.root / "model-route-health.json"
        before = health_path.read_bytes()
        self.assertEqual(self.observed_route()["status"], "available")

        abort_token = AbortController(self.root).token()
        with self.assertRaises(AldenCancelled):
            self.generate(abort_token, cancel_during_read=True)

        self.assertEqual(self.adapter.last_metrics["request"]["state"], "cancelled")
        self.assertEqual(health_path.read_bytes(), before,
                         "a locally cancelled media turn overwrote model health")
        self.assertEqual(self.observed_route()["status"], "available")
        self.assertTrue(self.observed_route()["automatic_candidate"])

    def test_cancelled_first_media_request_does_not_create_provider_failure(self):
        token = AbortController(self.root).token()
        with self.assertRaises(AldenCancelled):
            self.generate(token, cancel_during_read=True)
        self.assertEqual(self.adapter.last_metrics["request"]["state"], "cancelled")
        self.assertFalse((self.root / "model-route-health.json").exists(),
                         "the provider did not fail; the local user cancelled")


if __name__ == "__main__":
    unittest.main()
