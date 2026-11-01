"""Local text completion contracts through fake HTTP and temporary chat state.

Exercise the real LocalMlxLlm and process_input caller, never model inference,
listeners, downloads, or user state. These fixtures describe wire responses;
they do not reimplement the production parser.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import alden_chat as chat
import alden_voice as voice
from alden_abort import AbortController
from alden_chat_turns import get_request
from alden_history import database
from tests.test_alden_chat import request as chat_request
from tests.test_alden_unit3 import FakeTts, ready_model_catalog
from tests.test_alden_voice_metrics import Response


MODEL = voice.QWEN38_27B_MODEL_ID
REPLY = "확인했습니다."
PREFACE = "도구를 확인하겠습니다."
TOOL_ERROR = "local_llm_tool_call_unsupported"
INCOMPLETE_ERROR = "local_llm_reply_incomplete"
MISSING = object()


def tool_call():
    return [{"id": "call_synthetic", "type": "function",
             "function": {"name": "fixture_lookup", "arguments": "{}"}}]


def function_call():
    return {"name": "fixture_lookup", "arguments": "{}"}


def completion(content=REPLY, finish="stop", **message_fields):
    choice = {"message": {"role": "assistant", "content": content, **message_fields}}
    if finish is not MISSING:
        choice["finish_reason"] = finish
    return {"id": "chatcmpl-synthetic", "model": MODEL.removeprefix("mlx/"),
            "choices": [choice]}


def chunk(delta, finish=None):
    return {"id": "chatcmpl-synthetic", "model": MODEL.removeprefix("mlx/"),
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def stream(events, *, done=True):
    raw = b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8")
                   + b"\n\n" for event in events)
    return raw + (b"data: [DONE]\n\n" if done else b"")


class LocalCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="alden-local-completion-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.token = AbortController(self.root).token()
        self.adapter = voice.LocalMlxLlm(state_root=self.root)
        self.raw = b""
        self.content_type = "application/json"
        self.posts = []
        self.network_guards = []
        # Guard the real transports as well as replacing the established seam.
        for target in ("urllib.request.OpenerDirector.open", "socket.create_connection",
                       "socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("live runtime forbidden"))
            self.network_guards.append(guard.start())
            self.addCleanup(guard.stop)
        retrieval = patch.object(voice, "_voice_knowledge_reference",
                                 return_value=("", {"state": "empty", "sources": 0}))
        retrieval.start()
        self.addCleanup(retrieval.stop)
        transport = patch.object(voice, "_local_urlopen", side_effect=self.transport)
        transport.start()
        self.addCleanup(transport.stop)

    def tearDown(self):
        for guard in self.network_guards:
            guard.assert_not_called()

    def transport(self, request, **kwargs):
        if request.full_url == self.adapter.base_url + "/models":
            self.assertEqual(request.get_method(), "GET")
            return Response(json.dumps(ready_model_catalog(MODEL)).encode(), "application/json")
        if request.full_url == self.adapter.base_url.removesuffix("/v1") + "/metrics":
            return Response(b"vllm:num_requests_running 0\n")
        self.assertEqual(request.full_url, self.adapter.base_url + "/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.posts.append(json.loads(request.data))
        return Response(self.raw, self.content_type)

    def use_json(self, body):
        self.raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.content_type = "application/json"

    def use_stream(self, events, *, done=True):
        self.raw = stream(events, done=done)
        self.content_type = "text/event-stream"

    def generate(self):
        return self.adapter.generate("합성 입력", self.token)

    def assert_rejected(self, code):
        with self.assertRaisesRegex(RuntimeError, "^" + code + "$"):
            self.generate()
        self.assertEqual(self.adapter.last_metrics["request"]["state"], "failed")

    def test_json_stop_preserves_visible_text_and_response_metadata(self):
        body = completion("  " + REPLY + "  ")
        body["usage"] = {"prompt_tokens": 4, "completion_tokens": 2}
        self.use_json(body)
        self.assertEqual(self.generate(), REPLY)
        self.assertEqual(self.adapter.last_metrics["usage"], body["usage"])
        self.assertEqual(self.adapter.last_metrics["backend_request_id"], body["id"])
        self.assertEqual(self.adapter.last_metrics["request"]["state"], "completed")
        self.assertTrue(self.posts[0]["stream"])

    def test_json_legacy_missing_or_null_finish_is_accepted(self):
        for finish in (MISSING, None):
            with self.subTest(finish="missing" if finish is MISSING else "null"):
                self.use_json(completion(finish=finish))
                self.assertEqual(self.generate(), REPLY)

    def test_json_null_or_empty_tool_list_is_no_call(self):
        for value in (None, []):
            with self.subTest(tool_calls=value):
                self.use_json(completion(tool_calls=value, function_call=None))
                self.assertEqual(self.generate(), REPLY)

    def test_sse_role_reasoning_and_usage_footer_preserve_visible_answer(self):
        usage = {"prompt_tokens": 4, "completion_tokens": 2}
        self.use_stream([
            chunk({"role": "assistant", "content": "", "tool_calls": [], "function_call": None}),
            chunk({"reasoning_content": "합성 비공개 추론"}),
            chunk({"content": "  확인"}), chunk({"content": "했습니다.  "}),
            chunk({}, "stop"), {"choices": [], "usage": usage},
        ])
        self.assertEqual(self.generate(), REPLY)
        self.assertEqual(self.adapter.last_metrics["finish_reason"], "stop")
        self.assertEqual(self.adapter.last_metrics["usage"], usage)
        self.assertIn("first_visible_token_seconds", self.adapter.last_metrics)

    def test_sse_stop_on_content_chunk_is_complete(self):
        self.use_stream([chunk({"content": REPLY}, "stop")])
        self.assertEqual(self.generate(), REPLY)

    def test_sse_terminal_cannot_be_replaced_or_followed_by_content(self):
        for terminal in ("stop", "length", "content_filter", "tool_calls"):
            with self.subTest(terminal=terminal):
                self.use_stream([chunk({"content": PREFACE}, terminal),
                                 chunk({"content": REPLY}, "stop")])
                self.assert_rejected("local_llm_response_invalid")

    def test_malformed_terminal_reason_is_not_completion(self):
        for wire in ("json", "sse"):
            for reason in (False, 0, [], {}):
                with self.subTest(wire=wire, reason=reason):
                    if wire == "json":
                        self.use_json(completion(PREFACE, reason))
                    else:
                        self.use_stream([chunk({"content": PREFACE}, reason)])
                    self.assert_rejected(INCOMPLETE_ERROR)

    def test_json_message_must_be_an_object(self):
        for message in (None, "invalid", []):
            with self.subTest(message=message):
                body = completion()
                body["choices"][0]["message"] = message
                self.use_json(body)
                self.assert_rejected("local_llm_response_invalid")

    def test_json_multiple_choices_cannot_hide_an_unsupported_call(self):
        body = completion()
        body["choices"].append(completion(PREFACE, tool_calls=tool_call())["choices"][0])
        self.use_json(body)
        self.assert_rejected("local_llm_response_invalid")

    def test_sse_multiple_choices_cannot_hide_an_unsupported_call(self):
        event = chunk({"content": REPLY}, "stop")
        event["choices"].append({"index": 1, "delta": {"tool_calls": tool_call()}})
        self.use_stream([event])
        self.assert_rejected("local_llm_response_invalid")

    def test_response_choice_index_must_match_the_single_requested_answer(self):
        for wire in ("json", "sse"):
            for index in (1, -1, False, None, "0"):
                with self.subTest(wire=wire, index=index):
                    if wire == "json":
                        body = completion()
                        body["choices"][0]["index"] = index
                        self.use_json(body)
                    else:
                        event = chunk({"content": REPLY}, "stop")
                        event["choices"][0]["index"] = index
                        self.use_stream([event])
                    self.assert_rejected("local_llm_response_invalid")

    def test_voice_does_not_speak_or_commit_unsupported_tool_preface(self):
        self.use_json(completion(PREFACE, "tool_calls", tool_calls=tool_call()))
        tts = FakeTts()
        pipeline = voice.AldenVoicePipeline(
            stt=object(), llm=self.adapter, tts=tts, token=self.token,
            status=voice.VoiceStatusStore(self.root))
        try:
            result = pipeline.process_text("합성 입력")
            self.assertEqual(result.state, voice.VoiceState.ERROR)
            self.assertEqual(result.error_code, "generation_error")
            self.assertEqual(result.reply, "")
            self.assertEqual(tts.calls, 0)
            with database(self.root) as db:
                roles = [row[0] for row in db.execute("SELECT role FROM voice_messages")]
            self.assertEqual(roles, ["user"])
        finally:
            pipeline.close()

    def test_literal_tool_call_markup_is_ordinary_content(self):
        literal = '문서의 "<tool_call>{\"name\":\"example\"}</tool_call>"는 문자열입니다.'
        for wire in ("json", "sse"):
            with self.subTest(wire=wire):
                if wire == "json":
                    self.use_json(completion(literal))
                else:
                    self.use_stream([chunk({"content": literal}, "stop")])
                self.assertEqual(self.generate(), literal)

    def test_json_length_rejects_visible_partial_reply(self):
        self.use_json(completion(PREFACE, "length"))
        self.assert_rejected("local_llm_reply_truncated")

    def test_sse_length_rejects_visible_partial_reply(self):
        self.use_stream([chunk({"content": PREFACE}), chunk({}, "length")])
        self.assert_rejected("local_llm_reply_truncated")

    def test_json_nonstop_terminal_rejects_visible_preface(self):
        for finish in ("content_filter", "vendor_unknown", ""):
            with self.subTest(finish=finish):
                self.use_json(completion(PREFACE, finish))
                self.assert_rejected(INCOMPLETE_ERROR)

    def test_sse_nonstop_terminal_rejects_visible_preface(self):
        for finish in ("content_filter", "vendor_unknown", ""):
            with self.subTest(finish=finish):
                self.use_stream([chunk({"content": PREFACE}), chunk({}, finish)])
                self.assert_rejected(INCOMPLETE_ERROR)

    def test_json_tool_finish_without_metadata_is_unsupported(self):
        for finish in ("tool_calls", "function_call"):
            with self.subTest(finish=finish):
                self.use_json(completion(PREFACE, finish))
                self.assert_rejected(TOOL_ERROR)

    def test_sse_tool_finish_without_metadata_is_unsupported(self):
        for finish in ("tool_calls", "function_call"):
            with self.subTest(finish=finish):
                self.use_stream([chunk({"content": PREFACE}), chunk({}, finish)])
                self.assert_rejected(TOOL_ERROR)

    def test_json_structured_tool_call_rejects_preface_even_with_stop(self):
        self.use_json(completion(PREFACE, tool_calls=tool_call()))
        self.assert_rejected(TOOL_ERROR)

    def test_json_legacy_function_call_rejects_preface_even_with_stop(self):
        self.use_json(completion(PREFACE, function_call=function_call()))
        self.assert_rejected(TOOL_ERROR)

    def test_json_tool_metadata_rejects_legacy_missing_or_null_finish(self):
        for field, value in (("tool_calls", tool_call()), ("function_call", function_call())):
            for finish in (MISSING, None):
                with self.subTest(field=field, finish="missing" if finish is MISSING else "null"):
                    self.use_json(completion(PREFACE, finish, **{field: value}))
                    self.assert_rejected(TOOL_ERROR)

    def test_sse_structured_tool_delta_after_visible_preface_is_unsupported(self):
        self.use_stream([chunk({"content": PREFACE}),
                         chunk({"tool_calls": [{"index": 0, **tool_call()[0]}]}),
                         chunk({}, "stop")])
        self.assert_rejected(TOOL_ERROR)

    def test_sse_legacy_function_delta_after_visible_preface_is_unsupported(self):
        self.use_stream([chunk({"content": PREFACE}),
                         chunk({"function_call": function_call()}), chunk({}, "stop")])
        self.assert_rejected(TOOL_ERROR)

    def test_sse_fragmented_tool_arguments_are_unsupported(self):
        self.use_stream([chunk({"content": PREFACE}),
                         chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{"}}]}),
                         chunk({"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}),
                         chunk({}, "tool_calls")])
        self.assert_rejected(TOOL_ERROR)

    def test_malformed_tool_metadata_rejects_visible_preface(self):
        for wire in ("json", "sse"):
            for field, values in (("tool_calls", ["bad", "", False, 0, {}, [None]]),
                                  ("function_call", ["bad", "", False, 0, [], ["bad"]])):
                for value in values:
                    with self.subTest(wire=wire, field=field, value=value):
                        if wire == "json":
                            self.use_json(completion(PREFACE, **{field: value}))
                        else:
                            self.use_stream([chunk({"content": PREFACE, field: value}, "stop")])
                        self.assert_rejected(TOOL_ERROR)

    def test_sse_done_without_terminal_finish_rejects_visible_preface(self):
        self.use_stream([chunk({"content": PREFACE})])
        self.assert_rejected("local_llm_stream_incomplete")

    def test_sse_usage_footer_is_not_a_terminal_finish(self):
        self.use_stream([chunk({"content": PREFACE}),
                         {"choices": [], "usage": {"completion_tokens": 3}}])
        self.assert_rejected("local_llm_stream_incomplete")

    def test_sse_eof_without_terminal_finish_rejects_visible_preface(self):
        self.use_stream([chunk({"content": PREFACE})], done=False)
        self.assert_rejected("local_llm_stream_incomplete")

    def test_json_reasoning_only_or_empty_remains_reply_empty(self):
        for content in (None, "", " \n"):
            with self.subTest(content=content):
                self.use_json(completion(content, reasoning_content="합성 비공개 추론"))
                self.assert_rejected("local_llm_reply_empty")

    def test_sse_reasoning_only_with_or_without_stop_remains_reply_empty(self):
        for finish in (None, "stop"):
            with self.subTest(finish=finish):
                self.use_stream([chunk({"reasoning_content": "합성 비공개 추론"}, finish)])
                self.assert_rejected("local_llm_reply_empty")

    def test_tool_metadata_without_visible_content_is_still_unsupported(self):
        for wire in ("json", "sse"):
            with self.subTest(wire=wire):
                if wire == "json":
                    self.use_json(completion(None, "tool_calls", tool_calls=tool_call()))
                else:
                    self.use_stream([chunk({"tool_calls": tool_call()}, "tool_calls")])
                self.assert_rejected(TOOL_ERROR)

    def process_input(self, value):
        selection = {"transport": "local", "model": MODEL,
                     "base_url": self.adapter.base_url, "reasoning_effort": None}
        # Keep generate_input, LocalMlxLlm, and the durable turn path real.
        return chat.process_input(self.root, value, self.token,
                                  select_model=lambda root: selection)

    def assert_persisted_failure(self, code):
        value = chat_request(text="합성 입력")
        first = self.process_input(value)
        stored = get_request(self.root, value["request_id"])
        posts_before_replay = len(self.posts)
        replay = self.process_input(value)
        with database(self.root) as db:
            roles = [row[0] for row in db.execute("SELECT role FROM voice_messages")]
        # Subtests expose persistence and commit failures even if the public
        # receipt is already wrong on the unfixed baseline.
        with self.subTest(check="public_failure"):
            self.assertFalse(first["ok"], first)
            self.assertEqual(first["state"], "failed")
            self.assertEqual(first["error_code"], code)
            self.assertNotIn("reply", first)
        with self.subTest(check="persisted_failure"):
            self.assertEqual(stored["state"], "failed")
            self.assertEqual(stored["generation"]["terminal_state"], "failed")
            self.assertEqual(stored["generation"]["error_code"], code)
            self.assertEqual(stored["generation"]["request"]["context_binding"], "verified")
        with self.subTest(check="no_assistant_commit"):
            self.assertEqual(roles.count("assistant"), 0)
            self.assertEqual(roles.count("user"), 1)
        with self.subTest(check="replay_preserves_failure_without_generation"):
            self.assertEqual(posts_before_replay, 1)
            self.assertEqual(len(self.posts), posts_before_replay)
            self.assertFalse(replay["ok"], replay)
            self.assertEqual(replay["state"], "failed")
            self.assertEqual(replay["error_code"], code)
            self.assertNotIn("reply", replay)

    def test_process_input_tool_preface_persists_failure_without_assistant_commit(self):
        self.use_stream([chunk({"content": PREFACE}),
                         chunk({"tool_calls": tool_call()}, "tool_calls")])
        self.assert_persisted_failure(TOOL_ERROR)

    def test_process_input_function_preface_persists_failure_without_assistant_commit(self):
        self.use_json(completion(PREFACE, "function_call", function_call=function_call()))
        self.assert_persisted_failure(TOOL_ERROR)

    def test_process_input_malformed_tool_persists_failure_without_assistant_commit(self):
        self.use_json(completion(PREFACE, tool_calls="malformed"))
        self.assert_persisted_failure(TOOL_ERROR)

    def test_process_input_missing_terminal_persists_failure_without_assistant_commit(self):
        self.use_stream([chunk({"content": PREFACE})])
        self.assert_persisted_failure("local_llm_stream_incomplete")

    def test_process_input_content_filter_persists_failure_without_assistant_commit(self):
        self.use_stream([chunk({"content": PREFACE}), chunk({}, "content_filter")])
        self.assert_persisted_failure(INCOMPLETE_ERROR)

    def test_process_input_length_persists_failure_without_assistant_commit(self):
        self.use_stream([chunk({"content": PREFACE}), chunk({}, "length")])
        self.assert_persisted_failure("local_llm_reply_truncated")

    def test_process_input_reasoning_only_persists_failure_without_assistant_commit(self):
        self.use_stream([chunk({"reasoning_content": "합성 비공개 추론"}, "stop")])
        self.assert_persisted_failure("local_llm_reply_empty")

    def test_process_input_stop_commits_one_answer_and_replays_without_generation(self):
        self.use_stream([chunk({"content": REPLY}), chunk({}, "stop"),
                         {"choices": [], "usage": {"completion_tokens": 2}}])
        value = chat_request(text="합성 입력")
        first = self.process_input(value)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["reply"], REPLY)
        self.assertEqual(first["generation"]["terminal_state"], "completed")
        self.assertEqual(self.process_input(value), first)
        self.assertEqual(len(self.posts), 1)
        with database(self.root) as db:
            replies = [row[0] for row in db.execute(
                "SELECT content FROM voice_messages WHERE role='assistant'")]
        self.assertEqual(replies, [REPLY])


if __name__ == "__main__":
    unittest.main()
