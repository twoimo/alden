"""Conversation selection races with generation; no live models, mic or sends."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import threading
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_abort import AbortController, AldenCancelled
from alden_history import read, record_voice
from alden_voice import AldenVoicePipeline, VoiceState, VoiceStatusStore


class Speech:
    def __init__(self):
        self.spoken = []

    def speak(self, text, token):
        token.raise_if_cancelled()
        self.spoken.append(text)


class ImmediateModel:
    def generate(self, text, token, **kwargs):
        token.raise_if_cancelled()
        return "응답: " + text


class ConversationSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.controller = AbortController(self.root)
        self.destination = "b" * 32
        record_voice(self.root, self.destination, 4, "user", "회의는 3층", 7)
        record_voice(self.root, self.destination, 4, "assistant", "확인했습니다", 8)

    def pipeline(self, llm=None):
        pipeline = AldenVoicePipeline(
            stt=object(), llm=llm or ImmediateModel(), tts=Speech(),
            token=self.controller.token(), status=VoiceStatusStore(self.root),
            manual_listen=True,
        )
        self.addCleanup(pipeline.close)
        return pipeline

    def messages(self, session):
        return read(self.root, Path("/unused"), "voice-history-messages", chat_id=session)["items"]

    def test_switch_discards_late_answer_and_keeps_its_original_session_id(self):
        entered, release = threading.Event(), threading.Event()

        class Model:
            def generate(self, text, token, **kwargs):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test gate expired")
                # Deliberately ignores cancellation, like an uninterruptible kernel.
                return "이전 대화의 늦은 응답"

        pipeline = self.pipeline(Model())
        original = pipeline.conversation_id
        result = []
        worker = threading.Thread(target=lambda: result.append(pipeline.process_text("이전 요청")))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            pipeline.restore_selected_conversation(self.destination)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0].state, VoiceState.ABORTED)
        self.assertTrue(result[0].cancelled)
        self.assertEqual(result[0].conversation_id, original)
        self.assertEqual(pipeline.tts.spoken, [])
        self.assertEqual(len(self.messages(self.destination)), 2)
        self.assertEqual([(r["role"], r["content"]) for r in self.messages(original)], [("user", "이전 요청")])
        self.assertEqual(pipeline.state, VoiceState.USER_LISTEN)

    def test_switch_cancels_active_and_pending_input_without_resurrecting_poll_result(self):
        entered, release = threading.Event(), threading.Event()

        class Model:
            def generate(self, text, token, **kwargs):
                entered.set()
                release.wait(3)
                return "오래된 응답"

        pipeline = self.pipeline(Model())
        pipeline.submit_text("첫 요청")
        try:
            self.assertTrue(entered.wait(2))
            pipeline.submit_text("대기 요청")
            pipeline.restore_selected_conversation(self.destination)
            self.assertIsNone(pipeline._active_turn)
            self.assertIsNone(pipeline._pending_turn)
            self.assertIsNone(pipeline.poll_result())
        finally:
            release.set()
            pipeline.close()
            if pipeline._worker is not None:
                pipeline._worker.join(3)
        self.assertEqual(pipeline.tts.spoken, [])
        self.assertEqual(len(self.messages(self.destination)), 2)

    def test_selected_history_resets_transient_text_and_accepts_its_own_event_ids(self):
        pipeline = self.pipeline()
        pipeline.process_text("이전 요청", event_id="one")
        pipeline.restore_selected_conversation(self.destination)
        self.assertEqual(pipeline._transcript, "")
        self.assertEqual(pipeline._reply, "")
        self.assertEqual(pipeline.turn_id, 4)
        self.assertEqual(pipeline.context_version, 8)
        self.assertEqual(pipeline._recent_conversation(), [
            {"role": "user", "content": "회의는 3층"},
            {"role": "assistant", "content": "확인했습니다"},
        ])
        result = pipeline.process_text("그럼 어디로 가죠?", event_id="one")
        self.assertEqual(result.state, VoiceState.ENDED)
        self.assertEqual(result.turn_id, 5)
        self.assertEqual(result.conversation_id, self.destination)

    def test_closed_pipeline_cannot_restore_a_conversation(self):
        pipeline = self.pipeline()
        original = pipeline.conversation_id
        pipeline.close()
        with self.assertRaisesRegex(RuntimeError, "voice_conversation_unavailable"):
            pipeline.restore_selected_conversation(self.destination)
        self.assertEqual(pipeline.conversation_id, original)

    def test_global_abort_cannot_be_resumed_by_selecting_history(self):
        pipeline = self.pipeline()
        original = pipeline.conversation_id
        self.controller.abort()
        with self.assertRaises(AldenCancelled):
            pipeline.restore_selected_conversation(self.destination)
        self.assertEqual(pipeline.conversation_id, original)

    def test_invalid_selection_preserves_current_conversation(self):
        pipeline = self.pipeline()
        pipeline.process_text("원래 요청")
        before = (pipeline.conversation_id, pipeline.turn_id, pipeline.context_version, pipeline._recent_conversation())
        with self.assertRaisesRegex(RuntimeError, "voice_conversation_unavailable"):
            pipeline.restore_selected_conversation("f" * 32)
        self.assertEqual((pipeline.conversation_id, pipeline.turn_id, pipeline.context_version, pipeline._recent_conversation()), before)

    def test_restore_before_microphone_start_stays_idle(self):
        pipeline = self.pipeline()
        pipeline.restore_selected_conversation(self.destination)
        self.assertEqual(pipeline.state, VoiceState.IDLE)

    def test_restoring_same_session_does_not_bypass_event_deduplication(self):
        pipeline = self.pipeline()
        original = pipeline.conversation_id
        pipeline.process_text("一度の入力", event_id="one")
        pipeline.restore_selected_conversation(original)
        ignored = pipeline.process_text("一度の入力", event_id="one")
        self.assertEqual(ignored.error_code, "input_ignored")
        self.assertEqual(len(self.messages(original)), 2)

    def test_input_during_history_load_belongs_to_selected_conversation(self):
        import alden_history
        pipeline = self.pipeline()
        original = pipeline.conversation_id
        entered, release, typed = threading.Event(), threading.Event(), threading.Event()
        actual_database = alden_history.database
        errors, results = [], []

        class PausedRead:
            def __init__(self, db):
                self.db = db

            def execute(self, sql, *args):
                if sql.startswith("SELECT role,content FROM voice_messages"):
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("history read gate expired")
                return self.db.execute(sql, *args)

        @contextmanager
        def database(*args, **kwargs):
            with actual_database(*args, **kwargs) as db:
                yield PausedRead(db) if db is not None else None

        def restore():
            try:
                pipeline.restore_selected_conversation(self.destination)
            except Exception as error:
                errors.append(error)

        def type_input():
            try:
                results.append(pipeline.process_text("선택한 대화의 새 질문"))
            finally:
                typed.set()

        with mock.patch.object(alden_history, "database", database):
            restoring = threading.Thread(target=restore)
            restoring.start()
            self.assertTrue(entered.wait(2))
            incoming = threading.Thread(target=type_input)
            incoming.start()
            try:
                self.assertFalse(typed.wait(.05), "input must wait for history selection")
            finally:
                release.set()
                restoring.join(3)
                incoming.join(3)
        self.assertEqual(errors, [])
        self.assertFalse(restoring.is_alive())
        self.assertFalse(incoming.is_alive())
        self.assertEqual(results[0].conversation_id, self.destination)
        self.assertEqual(results[0].state, VoiceState.ENDED)
        self.assertEqual(len(self.messages(self.destination)), 4)
        self.assertEqual(self.messages(original), [])


if __name__ == "__main__":
    unittest.main()
