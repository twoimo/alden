"""Real voice-turn publication fences around mocked local MLX wire responses.

The production AldenVoicePipeline, LocalMlxLlm, AbortToken and SQLite voice
history run unmodified. Only the loopback HTTP model and TTS hardware are
deterministic fixtures. No actual microphone, GPU, external model or sends.
"""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_abort import AbortController
from alden_history import database
from alden_voice import (
    AldenVoicePipeline, LocalMlxLlm, QWEN38_27B_MODEL_ID,
    VoiceState, VoiceStatusStore,
)
import alden_voice as voice


MODEL = QWEN38_27B_MODEL_ID.removeprefix("mlx/")


class WireReply(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


def response(content, *, finish="stop", role=None, extra=None):
    message = {"content": content}
    if role is not None:
        message["role"] = role
    if extra:
        message.update(extra)
    return {
        "id": "chatcmpl-validated",
        "model": MODEL,
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def sse(chunks, *, done=True):
    wire = b"".join(
        b"data: " + json.dumps(chunk, ensure_ascii=False, allow_nan=False).encode() + b"\n\n"
        for chunk in chunks
    )
    return wire + (b"data: [DONE]\n\n" if done else b"")


def chunk(*, content=None, finish=None, model=MODEL, role=None, tool_calls=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if role is not None:
        delta["role"] = role
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-validated",
        "model": model,
        "choices": [{"delta": delta, "finish_reason": finish}],
    }


class HardwareTts:
    def __init__(self):
        self.spoken = []

    def speak(self, text, token):
        token.raise_if_cancelled()
        self.spoken.append(text)


class ModelCompletionFences(unittest.TestCase):
    def test_exact_stop_publishes_one_assistant_only_and_retains_turn_identity(self):
        body = sse([
            chunk(content="최종"),
            chunk(content=" 답변", finish="stop"),
            {"id": "chatcmpl-validated", "choices": [], "usage": {"completion_tokens": 3}},
        ])
        result, speech, messages, metrics, calls = self.run_turn(body, "text/event-stream")
        self.assertEqual(result.state, VoiceState.ENDED)
        self.assertEqual(result.reply, "최종 답변")
        self.assertEqual(speech, ["최종 답변"])
        self.assertEqual([row["role"] for row in messages], ["user", "assistant"])
        self.assertEqual(metrics["finish_reason"], "stop")
        self.assertEqual(metrics["usage"]["completion_tokens"], 3)
        self.assertEqual(metrics["request"]["turn_id"], result.turn_id)
        self.assertEqual(metrics["request"]["context_version"], 1)
        self.assertEqual(metrics["request"]["conversation_id"], result.conversation_id)
        self.assertEqual(metrics["request"]["state"], "completed")
        posts = [r for r in calls if r.get_method() == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(dict(posts[0].header_items())["X-alden-conversation-id"], result.conversation_id)

    def test_nonterminal_json_and_tool_calls_never_speak_or_persist_model_answer(self):
        for finish in (None, "content_filter", "tool_calls", "function_call", "failed", "", 2):
            with self.subTest(finish=finish):
                self.assert_rejected(json.dumps(response("부분 발화", finish=finish)).encode())
        for extra in ({"tool_calls": [{"id": "call-incomplete"}]}, {"function_call": {"name": "send"}}):
            with self.subTest(extra=extra):
                self.assert_rejected(json.dumps(response("보내면 안 됨", extra=extra)).encode())

    def test_explicit_failed_response_cannot_sneak_in_with_successful_choice(self):
        for fields in ({"error": {"message": "private provider error"}},
                       {"status": "failed"}, {"status": "incomplete"},
                       {"status": "cancelled"}):
            with self.subTest(fields=fields):
                forged = response("위장된 완료", finish="stop")
                forged.update(fields)
                self.assert_rejected(json.dumps(forged).encode())
                forged_sse = chunk(content="위장된 완료", finish="stop")
                forged_sse.update(fields)
                self.assert_rejected(sse([forged_sse]), "text/event-stream",
                                     "local_llm_generation_failed")

    def test_nonterminal_sse_and_poststop_injection_never_speak(self):
        cases = [
            (sse([chunk(content="끝나지 않은 발화")]), "local_llm_stream_incomplete"),
            (sse([chunk(content="부적절한 중단", finish="content_filter")]), "local_llm_reply_unconfirmed"),
            (sse([chunk(content="도구 호출", finish="tool_calls")]), "local_llm_reply_unconfirmed"),
            (sse([chunk(content="끝", finish="stop"), chunk(content="이후 오염")]), "local_llm_stream_post_completion"),
            (sse([chunk(content="끝", finish="stop"), chunk(finish="stop")]), "local_llm_stream_post_completion"),
            (sse([chunk(content="도구", tool_calls=[{"id": "call"}], finish="stop")]), "local_llm_reply_unconfirmed"),
            (sse([chunk(content="사용자 전용", role="user", finish="stop")]), "local_llm_response_invalid"),
            (sse([chunk(content="절단", finish="length")]), "local_llm_reply_truncated"),
            (sse([chunk(content="완성")], done=False), "local_llm_stream_incomplete"),
        ]
        for body, reason in cases:
            with self.subTest(reason=reason, data=body[:55]):
                self.assert_rejected(body, "text/event-stream", reason)

    def test_ambiguous_and_nonfinite_json_fail_closed_on_local_transport(self):
        with self.assertRaisesRegex(RuntimeError, "local_llm_response_invalid"):
            LocalMlxLlm._decode_completion_payload(b'{"choices":[],"choices":[]}')
        with self.assertRaisesRegex(RuntimeError, "local_llm_response_invalid"):
            LocalMlxLlm._decode_completion_payload(b'{"usage":{"tokens":NaN}}')
        with self.assertRaisesRegex(RuntimeError, "local_llm_response_invalid"):
            LocalMlxLlm._decode_completion_payload(b'{"usage":{"tokens":1e400}}')
        self.assert_rejected(b'{"choices":[{"message":{"content":"unsafe"},"finish_reason":"stop"}],'
                             b'"choices":[{"message":{"content":"spoof"},"finish_reason":"stop"}]}')
        self.assert_rejected(sse([chunk(content="어색한 종료", finish="stop")]).replace(
            b'"choices": [', b'"usage": NaN, "choices": ['), "text/event-stream", "local_llm_response_invalid")

    def test_rejected_reply_preserves_user_input_and_next_valid_turn_can_complete(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = LocalMlxLlm(state_root=root)
            tts = HardwareTts()
            token = AbortController(root).token()
            pipe = AldenVoicePipeline(stt=object(), llm=model, tts=tts, token=token,
                                      status=VoiceStatusStore(root), manual_listen=True)
            attempts = [
                WireReply(json.dumps(response("조각", finish="content_filter")).encode()),
                WireReply(json.dumps(response("검증된 최종 답변", finish="stop")).encode()),
            ]

            def opener(request, **_):
                if request.full_url.endswith("/models"):
                    return WireReply(json.dumps({"data": [{"id": MODEL, "loaded": True, "state": "ready"}]}).encode())
                if request.full_url.endswith("/metrics"):
                    return WireReply(b"vllm:num_requests_running 0\n", "text/plain")
                return attempts.pop(0)

            try:
                with patch.object(voice, "_local_urlopen", side_effect=opener):
                    fail = pipe.process_text("첫 질문", event_id="turn-1")
                    self.assertEqual(fail.state, VoiceState.ERROR)
                    self.assertFalse(fail.reply)
                    self.assertEqual(tts.spoken, [])
                    duplicate = pipe.process_text("첫 질문", event_id="turn-1")
                    self.assertEqual(duplicate.error_code, "input_ignored")
                    self.assertEqual(duplicate.turn_id, fail.turn_id)
                    passed = pipe.process_text("이어지는 질문", event_id="turn-2")
                self.assertEqual(passed.state, VoiceState.ENDED)
                self.assertEqual(passed.turn_id, fail.turn_id+1)
                self.assertEqual(tts.spoken, ["검증된 최종 답변"])
                with database(root) as db:
                    rows = db.execute("SELECT role, content, turn_id FROM voice_messages "
                                      "WHERE session_id=? ORDER BY turn_id, "
                                      "CASE role WHEN 'user' THEN 0 ELSE 1 END",
                                      (pipe.conversation_id,)).fetchall()
                self.assertEqual([(row["role"], row["turn_id"]) for row in rows],
                                 [("user", 1), ("user", 2), ("assistant", 2)])
                self.assertEqual(rows[-1]["content"], "검증된 최종 답변")
            finally:
                pipe.close()

    def assert_rejected(self, wire, content_type="application/json", reason=None):
        result, spoken, history, metrics, calls = self.run_turn(wire, content_type)
        self.assertEqual(result.state, VoiceState.ERROR)
        self.assertEqual(result.error_code, "generation_error")
        self.assertEqual(result.reply, "")
        self.assertEqual(spoken, [])
        self.assertEqual([row["role"] for row in history], ["user"])
        self.assertEqual(metrics["request"]["state"], "failed")
        self.assertEqual(len([request for request in calls if request.get_method() == "POST"]), 1)
        if reason:
            # The pipeline intentionally redacts the model's private error;
            # test the model boundary separately rather than expose it to UI.
            with TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = LocalMlxLlm(state_root=root)
                def local_opener(request, **_):
                    if request.full_url.endswith("/models"):
                        return WireReply(json.dumps({"data": [{"id": MODEL, "loaded": True, "state": "ready"}]}).encode())
                    return WireReply(wire, content_type)
                with patch.object(voice, "_local_urlopen", side_effect=local_opener):
                    with self.assertRaisesRegex(RuntimeError, reason):
                        adapter.generate("확인", AbortController(root).token())

    def run_turn(self, wire, content_type="application/json"):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = LocalMlxLlm(state_root=root)
            tts = HardwareTts()
            calls = []
            pipe = AldenVoicePipeline(stt=object(), llm=model, tts=tts, token=AbortController(root).token(),
                                      status=VoiceStatusStore(root), manual_listen=True)
            def opener(request, **_):
                calls.append(request)
                if request.full_url.endswith("/models"):
                    return WireReply(json.dumps({"data": [{"id": MODEL, "loaded": True, "state": "ready"}]}).encode())
                if request.full_url.endswith("/metrics"):
                    return WireReply(b"vllm:num_requests_running 0\n", "text/plain")
                return WireReply(wire, content_type)
            try:
                with patch.object(voice, "_local_urlopen", side_effect=opener):
                    result = pipe.process_text("현재 질문", source="text", event_id="live-event-id")
                with database(root) as db:
                    rows = db.execute("SELECT role, content FROM voice_messages "
                                      "WHERE session_id=? ORDER BY turn_id, "
                                      "CASE role WHEN 'user' THEN 0 ELSE 1 END",
                                      (pipe.conversation_id,)).fetchall()
                return result, list(tts.spoken), [dict(row) for row in rows], json.loads(json.dumps(model.last_metrics)), calls
            finally:
                pipe.close()


if __name__ == "__main__":
    unittest.main()
