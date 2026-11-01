"""Offline wire-contract tests; no runtime, model, source lookup or service."""

from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import alden_knowledge_tool_protocol as protocol


MODEL = "mlx-community/Qwen3.8-27B-4bit"
INVALID = "local_llm_tool_protocol_invalid"
TRUNCATED = "local_llm_reply_truncated"
INCOMPLETE = "local_llm_incomplete_response"
MISSING = object()


def completion(content="확인한 답변입니다.", finish="stop"):
    return {
        "id": "chatcmpl-fixture_1", "model": MODEL,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content}}],
    }


def tool_completion(content=None, arguments="{}"):
    body = completion(content, "tool_calls")
    body["choices"][0]["message"]["tool_calls"] = [{
        "id": "call_fixture-1", "type": "function",
        "function": {"name": "lookup_requested_knowledge", "arguments": arguments},
    }]
    return body


def message(body):
    return body["choices"][0]["message"]


def call(body):
    return message(body)["tool_calls"][0]


def assign(obj, key, value):
    if value is MISSING:
        obj.pop(key, None)
    else:
        obj[key] = value


class KnowledgeToolProtocolTests(unittest.TestCase):
    def setUp(self):
        self.guards = []
        for target in ("socket.create_connection", "socket.socket.connect",
                       "socket.socket.connect_ex", "subprocess.Popen",
                       "urllib.request.OpenerDirector.open"):
            guard = patch(target, side_effect=AssertionError("live runtime forbidden"))
            self.guards.append(guard.start())
            self.addCleanup(guard.stop)

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()

    def assert_invalid(self, body, code=INVALID, **kwargs):
        with self.assertRaises(protocol.KnowledgeToolProtocolError) as caught:
            protocol.parse_completion(body, kwargs.pop("expected_model", MODEL), **kwargs)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_schema_is_exact_fresh_and_has_no_authority_arguments(self):
        schema = protocol.tool_schema()
        self.assertEqual(protocol.TOOL_NAME, "lookup_requested_knowledge")
        self.assertEqual(schema, {
            "type": "function", "function": {
                "name": "lookup_requested_knowledge",
                "description": "Read local knowledge requested by this confirmed user turn, within its existing scope.",
                "parameters": {"type": "object", "properties": {}, "required": [],
                               "additionalProperties": False},
            },
        })
        schema["function"]["parameters"]["properties"]["query"] = {"type": "string"}
        self.assertEqual(protocol.tool_schema()["function"]["parameters"]["properties"], {})

    def test_final_preserves_content_for_parent_cleaner_and_has_no_call_id(self):
        body = completion("  **답변**\n두 번째 줄.  ")
        for allow in (True, False):
            with self.subTest(allow_tool=allow):
                result = protocol.parse_completion(body, MODEL, allow_tool=allow)
                self.assertEqual(result, {
                    "kind": "final", "response_id": body["id"], "model": MODEL,
                    "usage": {}, "text": message(body)["content"],
                    "message": {"role": "assistant", "content": message(body)["content"]},
                })

    def test_tool_normalizes_exact_empty_arguments_and_preserves_private_preface(self):
        for content in (None, "", " \n지식을 확인하겠습니다.\n "):
            with self.subTest(content=content):
                body = tool_completion(content, " \n{ \t }\r ")
                result = protocol.parse_completion(body, MODEL)
                self.assertEqual(result, {
                    "kind": "tool", "response_id": body["id"], "model": MODEL,
                    "usage": {}, "text": content or "", "call_id": "call_fixture-1",
                    "message": {"role": "assistant", "content": content, "tool_calls": [{
                        "id": "call_fixture-1", "type": "function", "function": {
                            "name": "lookup_requested_knowledge", "arguments": "{}",
                        },
                    }]},
                })

    def test_tool_can_omit_preface(self):
        body = tool_completion()
        message(body).pop("content")
        result = protocol.parse_completion(body, MODEL)
        self.assertEqual(result["text"], "")
        self.assertIsNone(result["message"]["content"])

    def test_final_accepts_absent_null_or_empty_tool_calls_only(self):
        for value in (MISSING, None, []):
            with self.subTest(tool_calls=value):
                body = completion()
                assign(message(body), "tool_calls", value)
                result = protocol.parse_completion(body, MODEL)
                self.assertEqual(result["kind"], "final")
                self.assertNotIn("tool_calls", result["message"])
        for value in ({}, "", False, 0, "[]", [None], [call(tool_completion())]):
            with self.subTest(invalid_tool_calls=value):
                body = completion()
                message(body)["tool_calls"] = value
                self.assert_invalid(body)

    def test_reasoning_is_neither_visible_content_nor_executable_call(self):
        private = 'PRIVATE_REASONING {"tool_calls":[{"name":"unknown"}]}'
        for body in (completion(), tool_completion()):
            with self.subTest(finish=body["choices"][0]["finish_reason"]):
                message(body)["reasoning_content"] = private
                result = protocol.parse_completion(body, MODEL)
                self.assertNotIn("PRIVATE_REASONING", json.dumps(result))
                self.assertNotIn("reasoning_content", result["message"])
        for content in (None, "", " \n\t"):
            with self.subTest(reasoning_only=content):
                body = completion(content)
                message(body)["reasoning_content"] = private
                self.assert_invalid(body)

    def test_literal_tool_markup_in_final_text_remains_plain_text(self):
        text = '<tool_call>{"name":"lookup_requested_knowledge"}</tool_call>'
        result = protocol.parse_completion(completion(text), MODEL, allow_tool=False)
        self.assertEqual(result["kind"], "final")
        self.assertEqual(result["text"], text)
        self.assertNotIn("tool_calls", result["message"])

    def test_missing_wrong_unsafe_or_oversize_model_is_rejected_without_aliasing(self):
        for value in (MISSING, None, False, 7, [], {}, "", "other-model", "mlx/" + MODEL,
                      MODEL + "\n", MODEL + "\x00", "m" * 129):
            with self.subTest(observed=value):
                body = completion()
                assign(body, "model", value)
                self.assert_invalid(body)
        for value in (None, False, [], {}, "", "a/../b", "a/./b", "a//b", "a/",
                      "/local/model", "https://host/model", "file:secret", "sk-secret",
                      "한글", "a\ud800", "m" * 129):
            with self.subTest(expected=value):
                body = completion()
                body["model"] = value
                self.assert_invalid(body, expected_model=value)

    def test_response_and_call_ids_require_observed_bounded_safe_strings(self):
        bad = (MISSING, None, False, 7, [], {}, "", "with space", "a/b", "id\n",
               "id\x00", "한글", "x" * 129, "sk-secret", "Bearer-token", "eyJtoken")
        for is_call in (False, True):
            for value in bad:
                with self.subTest(call_id=is_call, value=value):
                    body = tool_completion() if is_call else completion()
                    assign(call(body) if is_call else body, "id", value)
                    self.assert_invalid(body)
        body = tool_completion()
        body["id"] = "r" * 128
        call(body)["id"] = "c" * 128
        body["model"] = "m" * 128
        result = protocol.parse_completion(body, "m" * 128)
        self.assertEqual(result["response_id"], "r" * 128)
        self.assertEqual(result["call_id"], "c" * 128)

    def test_payload_and_choices_are_exact_single_nonstream_objects(self):
        for value in (None, False, 1, "{}", b"{}", [], {}):
            with self.subTest(payload=value):
                self.assert_invalid(value)
        for value in (MISSING, None, False, {}, "[]", [], [None], [False], [[]],
                      [completion()["choices"][0]] * 2):
            with self.subTest(choices=value):
                body = completion()
                assign(body, "choices", value)
                self.assert_invalid(body)
        body = completion()
        body["object"] = "chat.completion.chunk"
        self.assert_invalid(body)
        body = completion()
        body["choices"][0]["delta"] = {"content": "stream fragment"}
        self.assert_invalid(body)

    def test_choice_index_is_required_exact_integer_zero(self):
        for value in (MISSING, None, False, True, 0.0, 1, -1, "0", [], {}):
            with self.subTest(index=value):
                body = completion()
                assign(body["choices"][0], "index", value)
                self.assert_invalid(body)

    def test_message_requires_an_assistant_role(self):
        for value in (MISSING, None, False, "assistant", [], 7):
            with self.subTest(message=value):
                body = completion()
                assign(body["choices"][0], "message", value)
                self.assert_invalid(body)
        for value in (MISSING, None, False, [], {}, "user", "system", "tool", "Assistant", " assistant"):
            with self.subTest(role=value):
                body = completion()
                assign(message(body), "role", value)
                self.assert_invalid(body)

    def test_length_is_truncated_for_both_text_and_tool_responses(self):
        for body in (completion(), tool_completion()):
            body["choices"][0]["finish_reason"] = "length"
            self.assert_invalid(body, TRUNCATED)

    def test_missing_or_nonterminal_finish_is_incomplete(self):
        for value in (MISSING, None, False, 0, [], {}, "", "content_filter", "cancelled",
                      "error", "function_call", "eos", "STOP", "stop\n"):
            with self.subTest(finish=value):
                body = completion()
                assign(body["choices"][0], "finish_reason", value)
                self.assert_invalid(body, INCOMPLETE)
        body = tool_completion()
        body["choices"][0]["finish_reason"] = None
        self.assert_invalid(body, INCOMPLETE)

    def test_terminal_reason_and_tool_presence_must_agree(self):
        body = tool_completion("Do not mistake this for a final answer")
        body["choices"][0]["finish_reason"] = "stop"
        self.assert_invalid(body)
        for value in (MISSING, None, [], {}, False, "[]"):
            with self.subTest(calls=value):
                body = tool_completion("preface")
                assign(message(body), "tool_calls", value)
                self.assert_invalid(body)

    def test_exactly_one_well_formed_function_call_is_required(self):
        for value in ([call(tool_completion())] * 2, [None], [{}], [False], ["call"]):
            with self.subTest(calls=value):
                body = tool_completion()
                message(body)["tool_calls"] = value
                self.assert_invalid(body)
        for field, values in {
            "type": (MISSING, None, False, {}, "custom", "Function"),
            "function": (MISSING, None, False, "function", [], {}),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    body = tool_completion()
                    assign(call(body), field, value)
                    self.assert_invalid(body)
        for value in (MISSING, None, False, [], {}, "unknown", "lookup_requested_knowledge "):
            with self.subTest(name=value):
                body = tool_completion()
                assign(call(body)["function"], "name", value)
                self.assert_invalid(body)

    def test_arguments_must_be_a_string_containing_exact_empty_strict_json_object(self):
        for value in (MISSING, None, False, {}, [], 0, "", " ", "null", "false", "0", '"{}"',
                      "[]", "[{}]", "{", "{}{}", "{} trailing", "```json\n{}\n```",
                      "{/*comment*/}", "{,}", "\ufeff{}", "{}\x00", "\ud800",
                      "NaN", "Infinity", "-Infinity", '{"x":NaN}', '{"x":Infinity}',
                      '{"x":-Infinity}', '{"x":1e999}', '{"x":1,"x":2}',
                      '{"query":null,"query":null}', '{"x":{"a":1,"a":2}}'):
            with self.subTest(arguments=value):
                body = tool_completion()
                assign(call(body)["function"], "arguments", value)
                self.assert_invalid(body)

    def test_model_cannot_supply_query_project_path_or_authority(self):
        for key in ("query", "project", "path", "state_root", "room", "history", "model",
                    "source_id", "endpoint", "opt_in", "cancellation_id", "authority"):
            for value in (None, "forbidden", {}, [], False):
                with self.subTest(argument=key, value=value):
                    self.assert_invalid(tool_completion(arguments=json.dumps({key: value})))

    def test_legacy_and_extra_structural_tool_fields_are_never_ignored(self):
        for owner in ("body", "choice", "message", "call", "function"):
            for key in ("function_call", "tools", "tool_choice", "parallel_tool_calls",
                        "tool_call_id", "query", "authority"):
                for value in (None, {}, "ignored?", []):
                    with self.subTest(owner=owner, key=key, value=value):
                        body = tool_completion()
                        target = {"body": body, "choice": body["choices"][0],
                                  "message": message(body), "call": call(body),
                                  "function": call(body)["function"]}[owner]
                        target[key] = value
                        self.assert_invalid(body)
        for key in ("index", "arguments", "name"):
            body = tool_completion()
            call(body)[key] = 0
            self.assert_invalid(body)
        for value in (None, {}, {"name": "lookup_requested_knowledge", "arguments": "{}"}):
            body = completion()
            message(body)["function_call"] = value
            self.assert_invalid(body)

    def test_second_tool_call_is_rejected_even_with_valid_preface(self):
        for content in (None, "An apparently useful answer"):
            self.assert_invalid(tool_completion(content), allow_tool=False)
        for value in (None, 0, 1, "false", [], {}):
            with self.subTest(allow_tool=value):
                self.assert_invalid(completion(), allow_tool=value)

    def test_argument_byte_limit_is_inclusive_and_never_truncated(self):
        self.assertEqual(protocol.parse_completion(
            tool_completion(arguments=" " * 254 + "{}"), MODEL)["kind"], "tool")
        for value in (" " * 255 + "{}", "{}" + " " * 255, '{"x":"' + "한" * 84 + '"}'):
            with self.subTest(size=len(value.encode("utf-8"))):
                self.assert_invalid(tool_completion(arguments=value))

    def test_text_utf8_limit_is_bounded_and_parent_owns_aggregate_wire_cap(self):
        self.assertEqual(protocol.MAX_RESPONSE_BYTES, 65536)
        exact = "한" * 21845 + "x"
        self.assertEqual(len(exact.encode("utf-8")), 65536)
        result = protocol.parse_completion(completion(exact), MODEL)
        self.assertEqual(result["text"], exact)
        for value in (exact + "x", "x" * 65537, "한" * 21846, "bad\ud800"):
            for factory in (completion, tool_completion):
                with self.subTest(factory=factory.__name__, size=len(value)):
                    self.assert_invalid(factory(value))

    def test_content_cannot_be_multimodal_or_other_json_types(self):
        for value in (False, 1, {}, [], [{"type": "text", "text": "answer"}]):
            for factory in (completion, tool_completion):
                with self.subTest(factory=factory.__name__, content=value):
                    self.assert_invalid(factory(value))
        for value in (MISSING, None, "", " \n\t"):
            body = completion()
            assign(message(body), "content", value)
            self.assert_invalid(body)

    def test_recognized_metadata_is_validated_dropped_and_not_returned_as_authority(self):
        body = completion()
        body.update(object="chat.completion", created=0, system_fingerprint="fp-fixture", service_tier="default")
        body["choices"][0]["logprobs"] = {"content": []}
        message(body).update(reasoning_content="private", refusal=None)
        result = protocol.parse_completion(body, MODEL)
        self.assertEqual(result, protocol.parse_completion(completion(), MODEL))
        for field, value in (("object", "response"), ("created", True), ("created", -1),
                             ("created", float("inf")), ("system_fingerprint", {}),
                             ("service_tier", "x" * 129)):
            bad = completion()
            bad[field] = value
            self.assert_invalid(bad)
        for field in ("reasoning_content", "refusal"):
            for value in (False, {}, [], "x" * 65537, "\ud800"):
                bad = completion()
                message(bad)[field] = value
                self.assert_invalid(bad)
        for value in (False, "logprobs", []):
            bad = completion()
            bad["choices"][0]["logprobs"] = value
            self.assert_invalid(bad)

    def test_usage_is_fresh_safe_observed_integer_counts_only(self):
        body = completion()
        body["usage"] = {
            "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
            "input_tokens": 0, "output_tokens": 9_007_199_254_740_991,
            "prompt_tokens_details": {"cached_tokens": 3, "private": "secret"},
            "completion_tokens_details": {"reasoning_tokens": 4, "accepted_prediction_tokens": 0,
                                          "rejected_prediction_tokens": 2, "unknown": 19},
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 1},
            "secret": "not evidence", "tokens_per_second": 120.5,
        }
        result = protocol.parse_completion(body, MODEL)
        expected = deepcopy(body["usage"])
        expected.pop("secret")
        expected.pop("tokens_per_second")
        expected["prompt_tokens_details"].pop("private")
        expected["completion_tokens_details"].pop("unknown")
        self.assertEqual(result["usage"], expected)
        result["usage"]["prompt_tokens_details"]["cached_tokens"] = 999
        self.assertEqual(body["usage"]["prompt_tokens_details"]["cached_tokens"], 3)

    def test_invalid_usage_values_are_dropped_without_coercion_or_inference(self):
        for value in (None, False, True, -1, 9_007_199_254_740_992, 10**1000,
                      1.0, 1.5, float("nan"), float("inf"), float("-inf"), "7", [], {}):
            with self.subTest(value_type=type(value).__name__):
                body = completion()
                body["usage"] = {"prompt_tokens": value, "completion_tokens": 2,
                                 "completion_tokens_details": {"reasoning_tokens": value}}
                self.assertEqual(protocol.parse_completion(body, MODEL)["usage"], {"completion_tokens": 2})
        for value in (MISSING, None, False, 7, [], "tokens"):
            body = completion()
            assign(body, "usage", value)
            self.assertEqual(protocol.parse_completion(body, MODEL)["usage"], {})
        body = completion()
        body["usage"] = {"prompt_tokens_details": [], "completion_tokens_details": False}
        self.assertEqual(protocol.parse_completion(body, MODEL)["usage"], {})

    def test_parser_does_not_mutate_input_or_share_returned_history(self):
        body = tool_completion("preface", " { } ")
        before = deepcopy(body)
        result = protocol.parse_completion(body, MODEL)
        self.assertEqual(body, before)
        result["message"]["tool_calls"][0]["function"]["arguments"] = '{"query":"bad"}'
        result["message"]["tool_calls"].append({})
        self.assertEqual(body, before)
        self.assertEqual(len(protocol.parse_completion(body, MODEL)["message"]["tool_calls"]), 1)

    def test_exception_never_reflects_untrusted_input(self):
        for value in ("private credential /source/path", [], {}, None):
            error = protocol.KnowledgeToolProtocolError(value)
            self.assertEqual(str(error), INVALID)
            self.assertEqual(error.code, INVALID)
        body = tool_completion(arguments='{"secret":"NEVER_ECHO_ME"')
        with self.assertRaises(protocol.KnowledgeToolProtocolError) as caught:
            protocol.parse_completion(body, MODEL)
        self.assertEqual(str(caught.exception), INVALID)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_import_and_parsing_have_no_runtime_dependencies_or_io(self):
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name.startswith(("alden_", "auto_reply_", "mlx", "torch")):
                raise AssertionError("runtime import forbidden")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import), \
                patch("builtins.open", side_effect=AssertionError("I/O forbidden")) as opened:
            importlib.reload(protocol)
            self.assertEqual(protocol.parse_completion(tool_completion(), MODEL)["kind"], "tool")
            self.assertEqual(protocol.parse_completion(completion(), MODEL)["kind"], "final")
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
