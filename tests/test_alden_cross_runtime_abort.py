"""One shared emergency latch across live Alden voice and local browser jobs.

Runs real AldenVoicePipeline, SQLite user/assistant history, AbortController,
and AldenToolRuntime together. Only model/browser/hardware actions are faked.
"""
import asyncio
from pathlib import Path
import sys
import threading
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_abort import AbortController
from alden_browser_use import BrowserJobResult
from alden_history import database
from alden_tool_runtime import AldenToolRuntime, BrowserToolJob, ToolStatus
from alden_voice import AldenVoicePipeline, VoiceResult, VoiceState, VoiceStatusStore


class BlockingAnswer:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = 0

    def generate(self, text, token, *, history=()):
        self.started.set()
        if not self.release.wait(4):
            raise RuntimeError("test blocking answer timed out")
        token.raise_if_cancelled()
        self.completed += 1
        return "old response must not be spoken"


class TextToSpeechSpy:
    def __init__(self):
        self.calls = []

    def speak(self, text, token):
        token.raise_if_cancelled()
        self.calls.append(text)


class GatedBrowser:
    def __init__(self, *, abort_error=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.abort_error = abort_error

    async def run(self, task):
        self.started.set()
        await self.release.wait()
        if self.abort_error:
            raise RuntimeError("browser terminated after global emergency stop")
        return BrowserJobResult(True, result="confirmed browser result")


class SharedEmergencyBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_abort_between_ticket_creation_and_dispatch_returns_rejected_submission(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AbortController(root)
            class NeverInfer:
                def generate(self, *args, **kwargs):
                    self.fail("pre-dispatch abort must never call the model")
            speaker = TextToSpeechSpy()
            voice = AldenVoicePipeline(
                stt=object(), llm=NeverInfer(), tts=speaker,
                token=AbortController(root).token(), status=VoiceStatusStore(root),
                manual_listen=True,
            )
            try:
                begin = voice._begin_turn
                def abort_after_allocating_ticket(source, event_id):
                    turn = begin(source, event_id)
                    controller.abort("stop between ticket and worker queue")
                    return turn
                voice._begin_turn = abort_after_allocating_ticket
                self.assertFalse(voice.submit_text("must not reach inference", event_id="ticket-race"))
                completed = voice.poll_result()
                self.assertIsNotNone(completed)
                self.assertEqual(completed.state, VoiceState.ABORTED)
                self.assertEqual(completed.reply, "")
                self.assertEqual(voice._recent_conversation(), [])
                self.assertEqual(speaker.calls, [])
                self.assertIsNone(voice._worker)
            finally:
                voice.close()

    async def test_global_stop_fences_both_runtime_results_and_only_fresh_epoch_can_resume(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            controller = AbortController(root)
            llm = BlockingAnswer()
            voice_speaker = TextToSpeechSpy()
            voice = AldenVoicePipeline(
                stt=object(), llm=llm, tts=voice_speaker,
                token=AbortController(root).token(), status=VoiceStatusStore(root),
                manual_listen=True,
            )
            browser_runner = GatedBrowser(abort_error=True)
            browser_runtime = AldenToolRuntime(
                root, _browser_runner_factory=lambda token: browser_runner,
            )
            try:
                self.assertTrue(voice.submit_text("현재 요청", source="text", event_id="voice-event-1"))
                browser = asyncio.create_task(browser_runtime.run_browser(
                    BrowserToolJob("browser-job-1", "bounded local task")
                ))
                self.assertTrue(await asyncio.to_thread(llm.started.wait, 3))
                await asyncio.wait_for(browser_runner.started.wait(), 3)

                controller.abort("user-pressed-emergency-stop")
                llm.release.set()
                browser_runner.release.set()
                browser_answer = await asyncio.wait_for(browser, 3)

                self.assertEqual(browser_answer.status, ToolStatus.ABORTED)
                self.assertFalse(browser_answer.ok)
                self.assertEqual(browser_answer.result, "")
                self.assertEqual(browser_answer.error_code, "global_abort")

                voice_answer: VoiceResult | None = None
                for _ in range(100):
                    voice_answer = voice.poll_result()
                    if voice_answer is not None:
                        break
                    await asyncio.sleep(.01)
                self.assertIsNotNone(voice_answer)
                self.assertEqual(voice_answer.state, VoiceState.ABORTED)
                self.assertTrue(voice_answer.cancelled)
                self.assertEqual(voice_answer.reply, "")
                self.assertEqual(llm.completed, 0)
                self.assertEqual(voice_speaker.calls, [])
                with database(root) as db:
                    prior = db.execute(
                        "SELECT role,content FROM voice_messages WHERE session_id=?",
                        (voice.conversation_id,),
                    ).fetchall()
                self.assertEqual([(row["role"], row["content"]) for row in prior],
                                 [("user", "현재 요청")])

                # Resume requires an explicitly renewed global epoch. The old
                # voice token cannot be reused for a previously cancelled turn.
                controller.resume_after_human_action()
                self.assertTrue(voice.token.is_cancelled())
                prior_turn = voice.turn_id
                self.assertFalse(voice.submit_text("stale epoch ignored", event_id="old-token"))
                ignored = voice.process_text("stale direct input", event_id="old-direct")
                self.assertEqual(ignored.state, VoiceState.ABORTED)
                self.assertTrue(ignored.cancelled)
                self.assertEqual(ignored.error_code, "global_abort")
                self.assertEqual(voice.turn_id, prior_turn)
                # Begin a separate, freshly owned voice session after resume.
                new_speaker = TextToSpeechSpy()
                class ReadyAnswer:
                    def generate(self, text, token, *, history=()):
                        token.raise_if_cancelled()
                        return "fresh confirmed answer"
                renewed = AldenVoicePipeline(
                    stt=object(), llm=ReadyAnswer(), tts=new_speaker,
                    token=AbortController(root).token(), status=VoiceStatusStore(root),
                    manual_listen=True,
                )
                try:
                    result = renewed.process_text("재개한 새 요청", event_id="fresh-voice-turn")
                    self.assertEqual(result.state, VoiceState.ENDED)
                    self.assertEqual(result.reply, "fresh confirmed answer")
                    self.assertEqual(new_speaker.calls, ["fresh confirmed answer"])
                    with database(root) as db:
                        newer = db.execute(
                            "SELECT role FROM voice_messages WHERE session_id=? "
                            "ORDER BY turn_id, CASE role WHEN 'user' THEN 0 ELSE 1 END",
                            (renewed.conversation_id,),
                        ).fetchall()
                    self.assertEqual([row["role"] for row in newer], ["user", "assistant"])
                finally:
                    renewed.close()

                ready = GatedBrowser()
                ready.release.set()
                new_browser = AldenToolRuntime(root, _browser_runner_factory=lambda token: ready)
                fresh_browser_result = await new_browser.run_browser(
                    BrowserToolJob("browser-job-2", "new explicitly issued job")
                )
                self.assertEqual(fresh_browser_result.status, ToolStatus.COMPLETED)
                self.assertEqual(fresh_browser_result.result, "confirmed browser result")
                self.assertNotEqual(fresh_browser_result.job_id, browser_answer.job_id)
            finally:
                llm.release.set()
                browser_runner.release.set()
                voice.close()


if __name__ == "__main__":
    unittest.main()
