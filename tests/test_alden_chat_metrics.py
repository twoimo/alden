"""Pure metric sanitization and fake-transport evidence checks; no live model."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_abort import AbortController, AldenCancelled
from alden_chat_metrics import MAX_GENERATION_JSON_BYTES, sanitize_generation
from alden_model_routes import DEFAULT_MODEL
from alden_routed_llm import OpenCodexLlm


CID = "a" * 32


def ticket():
    return {
        "conversation_id": CID, "turn_id": 3, "context_version": 5,
        "request_id": "b" * 32, "text": "private user prompt",
        "history": [{"role": "user", "content": "private history"}],
    }


def selection():
    return {
        "transport": "opencodex", "model": "chatgpt-web/gpt-6-sol",
        "reasoning_effort": "xhigh", "base_url": "https://token.invalid/key",
        "api_key": "sk-PRIVATE-AUTH",
    }


def request(**changes):
    return {
        "conversation_id": CID, "turn_id": 3, "context_version": 5,
        "model_id": "chatgpt-web/gpt-6-sol", "reasoning_effort": "xhigh",
        "transport": "opencodex", "state": "completed", "cancelled": False,
        **changes,
    }


def json_bytes(result):
    return json.dumps(
        result, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


class SanitizationTests(unittest.TestCase):
    def test_request_source_route_and_model_conflicts_are_rejected(self):
        for change in [{'source':'microphone'},{'transport':'local'},{'model_id':'other/model'}]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError,'generation_metadata_invalid'):
                sanitize_generation(selection(),{'request':request(**change)},ticket())

    def test_routed_seconds_field_is_not_labeled_as_local_elapsed(self):
        result=sanitize_generation(selection(),{'elapsed_seconds':1},ticket())
        self.assertNotIn('timing',result)

    def test_requested_adapter_and_observed_models_remain_distinct(self):
        metrics = {
            "request": request(local_request_id="sk-NOT-AN-ID", source="text"),
            "response_model_id": "gpt-6-sol",
            "backend_request_id": "resp_ABC123",
            "usage": {
                "input_tokens": 11, "output_tokens": 13, "total_tokens": 24,
                "input_tokens_details": {"cached_tokens": 7, "user_text": "private"},
                "output_tokens_details": {"reasoning_tokens": 5},
                "secret": "prompt and image bytes",
            },
            "elapsed_ms": 120.123456,
            "first_output_ms": 70.1255,
            "first_output_scope": "stream_output_text_delta",
            "retrieval": {"prompt": "private user prompt", "url": "https://secret.invalid"},
            "images": [{"image_url": "data:image/png;base64,private"}],
        }
        result = sanitize_generation(selection(), metrics, ticket())
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["selection"], {
            "transport": "opencodex", "model_id": "chatgpt-web/gpt-6-sol",
            "reasoning_effort": "xhigh",
        })
        self.assertEqual(result["request"], {
            "context_binding": "verified", "conversation_id": CID,
            "turn_id": 3, "context_version": 5,
            "model_id": "chatgpt-web/gpt-6-sol",
            "reasoning_effort": "xhigh", "transport": "opencodex",
            "state": "completed", "cancelled": False,
        })
        self.assertEqual(result["response"], {
            "model_id": "gpt-6-sol", "id": "resp_ABC123",
            "usage": {
                "input_tokens": 11, "output_tokens": 13, "total_tokens": 24,
                "input_tokens_details": {"cached_tokens": 7},
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        })
        self.assertEqual(result["timing"], {
            "elapsed_ms": 120.123, "elapsed_scope": "routed_generation_call",
            "first_output_ms": 70.126,
            "first_output_scope": "stream_output_text_delta",
        })
        serialized = json_bytes(result)
        self.assertLessEqual(len(serialized), MAX_GENERATION_JSON_BYTES)
        for private in (b"PRIVATE", b"private", b"https:", b"image_url", b"retrieval",
                        b"local_request_id", b"sk-NOT-AN-ID", b"api_key", b"base_url"):
            self.assertNotIn(private, serialized)

    def test_missing_observations_are_omitted_not_filled_in(self):
        result = sanitize_generation(selection(), {}, ticket())
        self.assertEqual(result["request"], {"context_binding": "unmeasured"})
        self.assertNotIn("response", result)
        self.assertNotIn("timing", result)
        self.assertNotIn("error_code", result)
        self.assertEqual(result["selection"]["reasoning_effort"], "xhigh")
        self.assertEqual(
            sanitize_generation(None, None, ticket()),
            {"schema_version": 1, "selection": {},
             "request": {"context_binding": "unmeasured"}},
        )

    def test_context_mismatch_or_partial_context_is_rejected_with_fixed_code(self):
        bad_requests = [
            request(conversation_id="c" * 32),
            request(turn_id=4),
            request(context_version=6),
            request(context_version=True),
            request(turn_id=3.0),
            {"conversation_id": CID, "turn_id": 3},
            {"context_version": 5},
        ]
        for bad in bad_requests:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "^generation_metadata_invalid$"):
                    sanitize_generation(selection(), {"request": bad}, ticket())
        with self.assertRaisesRegex(ValueError, "^generation_metadata_invalid$"):
            sanitize_generation(selection(), {}, {**ticket(), "turn_id": True})

    def test_invalid_selection_is_never_reflected_or_serialized(self):
        for bad in (
            {"transport": "https://secret.invalid", "model": "any"},
            {"transport": ["opencodex"]},
            {"model": "/Users/secret/model"},
            {"model": "https://secret.invalid/selected"},
            {"model": "sk-PRIVATE-AUTH"},
            {"reasoning_effort": {"secret": "password"}},
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "^generation_metadata_invalid$"):
                    sanitize_generation(bad, {}, ticket())

    def test_nonfinite_wrong_unit_and_unscoped_ttft_do_not_claim_latency(self):
        cases = (math.nan, math.inf, -math.inf, -1, True, 10**600)
        for invalid in cases:
            with self.subTest(invalid=repr(invalid)[:50]):
                result = sanitize_generation(
                    selection(),
                    {"elapsed_ms": invalid, "elapsed_seconds": invalid,
                     "ttft_ms": 8.5, "first_output_ms": 7.0,
                     "first_output_scope": "nonstream_json_completed"},
                    ticket(),
                )
                self.assertNotIn("timing", result)
        result = sanitize_generation(selection(), {
            "elapsed_ms": 10.0, "ttft_ms": 1.0, "first_output_ms": 12.0,
            "first_output_scope": "stream_output_text_delta",
        }, ticket())
        self.assertEqual(result["timing"], {
            "elapsed_ms": 10.0, "elapsed_scope": "routed_generation_call",
        })

    def test_untrusted_metadata_cannot_expand_allowlist_or_usage(self):
        metrics = {
            "request": request(
                state={"token": "sk-secret"}, transport={"url": "http://bad"},
                reasoning_effort=["xhigh"], cancelled="false", model_id="data:image/png;base64,private",
            ),
            "stage": ["response"],
            "response_model_id": "https://secret.invalid/model",
            "backend_request_id": "Bearer SECRET AUTHORIZATION",
            "usage": {
                "input_tokens": True, "output_tokens": -3,
                "total_tokens": 10**600, "prompt_tokens": 2,
                "input_tokens_details": {"cached_tokens": False, "raw": "SECRET"},
                "completion_tokens_details": {"reasoning_tokens": 4.0},
            },
            "error_code": {"authorization": "SECRET"},
            "elapsed_ms": "5", "first_output_ms": False,
            "first_output_scope": "stream_output_text_delta",
            "credentials": {"token": "SECRET"},
        }
        result = sanitize_generation(selection(), metrics, ticket())
        self.assertEqual(result["request"], {
            "context_binding": "verified", "conversation_id": CID,
            "turn_id": 3, "context_version": 5,
        })
        self.assertEqual(result["response"], {"usage": {"prompt_tokens": 2}})
        self.assertNotIn("timing", result)
        self.assertNotIn("error_code", result)
        self.assertNotIn(b"SECRET", json_bytes(result))

    def test_cancellation_and_failure_preserve_only_safe_partial_evidence(self):
        failed = sanitize_generation(selection(), {
            "request": request(state="failed", cancelled=False),
            "stage": "response", "error_code": "routed_llm_generation_failed",
            "elapsed_ms": 35.2, "ttft_ms": 12.0,
        }, ticket())
        self.assertEqual(failed["request"]["state"], "failed")
        self.assertEqual(failed["error_code"], "routed_llm_generation_failed")
        self.assertNotIn("first_output_ms", failed["timing"])
        self.assertNotIn("response", failed)
        cancelled = sanitize_generation(selection(), {
            "request": request(state="cancelled", cancelled=True),
            "stage": "transport", "error_code": "cancelled",
            "elapsed_ms": 0.0,
        }, ticket())
        self.assertEqual(cancelled["request"]["state"], "cancelled")
        self.assertTrue(cancelled["request"]["cancelled"])
        self.assertEqual(cancelled["timing"]["elapsed_ms"], 0.0)

    def test_local_stream_time_has_its_own_scope_without_inferred_model(self):
        selected = {"transport": "local", "model": "mlx/Qwen3.5-27B"}
        result = sanitize_generation(selected, {
            "request": {
                "conversation_id": CID, "turn_id": 3, "context_version": 5,
                "model_id": "mlx/Qwen3.5-27B", "state": "completed",
            },
            "backend_request_id": "chatcmpl-1",
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            "elapsed_seconds": 1.50123, "first_visible_token_seconds": 0.123456,
        }, ticket())
        self.assertEqual(result["response"], {
            "id": "chatcmpl-1", "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        })
        self.assertEqual(result["timing"], {
            "elapsed_ms": 1501.23, "elapsed_scope": "local_call_to_stream_end",
            "first_output_ms": 123.456, "first_output_scope": "local_stream_visible_content",
        })
        self.assertNotIn("tokens_per_second", json_bytes(result).decode())
        self.assertNotIn("model_id", result["response"])

    def test_largest_allowlisted_values_fit_four_kib_even_in_ascii_json(self):
        long_model = "model/" + "x" * 122
        raw = {
            "request": request(model_id=long_model),
            "response_model_id": long_model,
            "backend_request_id": "r" * 128,
            "usage": {
                "input_tokens": 9_007_199_254_740_991,
                "output_tokens": 9_007_199_254_740_991,
                "total_tokens": 9_007_199_254_740_991,
                "prompt_tokens": 9_007_199_254_740_991,
                "completion_tokens": 9_007_199_254_740_991,
                "input_tokens_details": {"cached_tokens": 9_007_199_254_740_991},
                "output_tokens_details": {"reasoning_tokens": 9_007_199_254_740_991},
                "prompt_tokens_details": {"cached_tokens": 9_007_199_254_740_991},
                "completion_tokens_details": {
                    "reasoning_tokens": 9_007_199_254_740_991,
                    "accepted_prediction_tokens": 9_007_199_254_740_991,
                    "rejected_prediction_tokens": 9_007_199_254_740_991,
                },
            },
            "elapsed_ms": 86_400_000, "first_output_ms": 86_400_000,
            "first_output_scope": "stream_output_text_delta",
        }
        result = sanitize_generation({**selection(), "model": long_model}, raw, ticket())
        self.assertLessEqual(len(json_bytes(result)), MAX_GENERATION_JSON_BYTES)


class FakeResponse(io.BytesIO):
    def __init__(self, raw, mime):
        super().__init__(raw)
        self.headers = {"Content-Type": mime}


def terminal(model, **changes):
    return {
        "status": "completed", "model": model, "id": "resp_test123",
        "usage": {"input_tokens": 3, "output_tokens": 1},
        "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "확인"}]}],
        **changes,
    }


class RoutedFakeAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.adapter = OpenCodexLlm(DEFAULT_MODEL, "high", self.root)
        self.token = AbortController(self.root).token()
        self.turn = SimpleNamespace(conversation_id=CID, turn_id=3, context_version=5,
                                    source="text")
        self.catalog = {
            "models": [{"id": DEFAULT_MODEL, "selectable": True,
                        "local": False, "efforts": ["high"]}],
        }
        self.selected = {"transport": "opencodex", "model": DEFAULT_MODEL,
                         "reasoning_effort": "high"}

    def invoke(self, response):
        with patch("alden_routed_llm.catalog", return_value=self.catalog), \
                patch("alden_routed_llm.CancellableLocalResponse", return_value=response):
            return self.adapter._generate(
                "PRIVATE PROMPT", self.token, history=(),
                turn=self.turn, system_prompt="PRIVATE SYSTEM",
            )

    def test_completed_nonstream_json_has_observed_model_but_no_first_token_latency(self):
        payload = terminal(DEFAULT_MODEL)
        self.assertEqual(self.invoke(FakeResponse(json.dumps(payload).encode(),
                                                   "application/json")), "확인")
        actual = self.adapter.last_metrics
        self.assertEqual(actual["response_model_id"], DEFAULT_MODEL)
        self.assertNotIn("ttft_ms", actual)
        self.assertNotIn("first_output_ms", actual)
        result = sanitize_generation(self.selected, actual, ticket())
        self.assertEqual(result["response"]["model_id"], DEFAULT_MODEL)
        self.assertEqual(result["request"]["context_binding"], "verified")
        self.assertNotIn("first_output_ms", result["timing"])

    def test_stream_delta_has_explicit_scope_and_observed_terminal_model(self):
        events = [
            {"type": "response.output_text.delta", "delta": ""},
            {"type": "response.output_text.delta", "delta": "확"},
            {"type": "response.output_text.delta", "delta": "인"},
            {"type": "response.completed", "response": terminal(DEFAULT_MODEL)},
        ]
        raw = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n"
                       for event in events)
        self.assertEqual(self.invoke(FakeResponse(raw, "text/event-stream")), "확인")
        generation = sanitize_generation(self.selected, self.adapter.last_metrics, ticket())
        timing = generation["timing"]
        self.assertEqual(timing["first_output_scope"], "stream_output_text_delta")
        self.assertGreaterEqual(timing["elapsed_ms"], timing["first_output_ms"])
        self.assertEqual(generation["response"]["usage"]["output_tokens"], 1)

    def test_failed_wrong_model_preserves_observation_without_answer_or_secrets(self):
        payload = terminal("gpt-6-astra")
        with self.assertRaisesRegex(RuntimeError, "^routed_model_mismatch$"):
            self.invoke(FakeResponse(json.dumps(payload).encode(), "application/json"))
        sanitized = sanitize_generation(self.selected, self.adapter.last_metrics, ticket())
        self.assertEqual(sanitized["request"]["state"], "failed")
        self.assertEqual(sanitized["response"]["model_id"], "gpt-6-astra")
        self.assertEqual(sanitized["selection"]["model_id"], DEFAULT_MODEL)
        self.assertEqual(sanitized["error_code"], "routed_model_mismatch")
        self.assertNotIn("id", sanitized["response"])

    def test_failed_partial_stream_keeps_measured_delta_but_not_answer(self):
        events = [
            {"type": "response.output_text.delta", "delta": "PRIVATE PARTIAL"},
            {"type": "response.failed", "response": {
                "model": DEFAULT_MODEL, "error": {"message": "PRIVATE ERROR"},
            }},
        ]
        raw = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n"
                       for event in events)
        with self.assertRaisesRegex(RuntimeError, "^routed_llm_generation_failed$"):
            self.invoke(FakeResponse(raw, "text/event-stream"))
        sanitized = sanitize_generation(self.selected, self.adapter.last_metrics, ticket())
        self.assertEqual(sanitized["request"]["state"], "failed")
        self.assertEqual(sanitized["error_code"], "routed_llm_generation_failed")
        self.assertEqual(sanitized["timing"]["first_output_scope"], "stream_output_text_delta")
        self.assertNotIn("response", sanitized)
        self.assertNotIn(b"PRIVATE", json_bytes(sanitized))

    def test_cancelled_adapter_can_be_sanitized_after_exception(self):
        self.token.cancel()
        with patch("alden_routed_llm.CancellableLocalResponse") as transport:
            with self.assertRaises(AldenCancelled):
                self.adapter._generate(
                    "PRIVATE PROMPT", self.token, history=(), turn=self.turn,
                    system_prompt="PRIVATE SYSTEM",
                )
            transport.assert_not_called()
        result = sanitize_generation(self.selected, self.adapter.last_metrics, ticket())
        self.assertEqual(result["request"]["state"], "cancelled")
        self.assertEqual(result["request"]["context_binding"], "verified")
        self.assertIn("elapsed_ms", result["timing"])
        self.assertNotIn("response", result)


if __name__ == "__main__":
    unittest.main()
