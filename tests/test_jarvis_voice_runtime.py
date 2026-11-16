from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import jarvis_voice  # noqa: E402
from jarvis_abort import AbortController  # noqa: E402
from jarvis_voice import (  # noqa: E402
    VOICE_MIC_FRAME_SAMPLES,
    WHISPER_MODEL_ID,
    AudioFrameAnalysis,
    JarvisVoicePipeline,
    Qwen3TtsAdapter,
    VoiceResult,
    VoiceState,
    VoiceStatusStore,
    _MicrophoneDisconnected,
    _MicrophoneFramePoller,
    run_file_pipeline,
    run_microphone_session,
)


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeStream:
    def __init__(
        self,
        *,
        active: bool = True,
        available: int = 0,
        frame: bytes | None = None,
        overflowed: bool = False,
        **_kwargs: object,
    ) -> None:
        self.active = active
        self.available = available
        self.frame = frame if frame is not None else b"\0\0" * VOICE_MIC_FRAME_SAMPLES
        self.overflowed = overflowed
        self.read_calls = 0
        self.availability_checks = 0

    @property
    def read_available(self) -> int:
        self.availability_checks += 1
        return self.available

    def read(self, frames: int) -> tuple[bytes, bool]:
        self.read_calls += 1
        if frames != VOICE_MIC_FRAME_SAMPLES:
            raise AssertionError(f"unexpected frame request: {frames}")
        return self.frame, self.overflowed

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class MicrophoneFramePollerTests(unittest.TestCase):
    def test_idle_poll_checks_availability_without_blocking_read(self) -> None:
        clock = FakeClock(10.0)
        stream = FakeStream(available=0)
        with mock.patch("jarvis_voice.time.monotonic", side_effect=clock.monotonic):
            poller = _MicrophoneFramePoller(stream)
            self.assertIsNone(poller.poll())
            self.assertEqual(stream.read_calls, 0)

            clock.now += 0.02
            stream.available = VOICE_MIC_FRAME_SAMPLES
            frame, overflowed = poller.poll() or (b"", True)

        self.assertEqual(frame, b"\0\0" * VOICE_MIC_FRAME_SAMPLES)
        self.assertFalse(overflowed)
        self.assertEqual(stream.read_calls, 1)
        self.assertEqual(stream.availability_checks, 2)

    def test_stale_and_disconnected_streams_fail_clearly(self) -> None:
        clock = FakeClock()
        stale = FakeStream(available=0)
        with mock.patch("jarvis_voice.time.monotonic", side_effect=clock.monotonic):
            poller = _MicrophoneFramePoller(stale, stale_seconds=0.1)
            clock.now = 0.1
            with self.assertRaisesRegex(_MicrophoneDisconnected, "stalled"):
                poller.poll()
        self.assertEqual(stale.read_calls, 0)

        inactive = FakeStream(active=False)
        with mock.patch("jarvis_voice.time.monotonic", return_value=0.0):
            poller = _MicrophoneFramePoller(inactive)
            with self.assertRaisesRegex(_MicrophoneDisconnected, "inactive"):
                poller.poll()
        self.assertEqual(inactive.read_calls, 0)


class VoiceStatusStoreTests(unittest.TestCase):
    def test_state_changes_write_immediately_inside_heartbeat_window(self) -> None:
        clock = FakeClock()
        real_replace = os.replace
        with TemporaryDirectory() as temp_dir, mock.patch(
            "jarvis_voice.time.monotonic", side_effect=clock.monotonic
        ), mock.patch("jarvis_voice.time.time", return_value=1000.0), mock.patch(
            "jarvis_voice.os.replace", wraps=real_replace
        ) as replace:
            store = VoiceStatusStore(Path(temp_dir))
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.0)
            clock.now = 0.1
            store.write(state=VoiceState.USER_LISTEN, rms=0.2)
            payload = json.loads(store.path.read_text(encoding="utf-8"))

        self.assertEqual(replace.call_count, 2)
        self.assertEqual(payload["state"], "user_listen")
        self.assertEqual(payload["rms"], 0.2)

    def test_unchanged_wake_listen_writes_at_most_once_per_five_seconds(self) -> None:
        clock = FakeClock()
        wall_times = iter((1000.0, 1005.0))
        real_replace = os.replace
        with TemporaryDirectory() as temp_dir, mock.patch(
            "jarvis_voice.time.monotonic", side_effect=clock.monotonic
        ), mock.patch(
            "jarvis_voice.time.time", side_effect=lambda: next(wall_times)
        ), mock.patch("jarvis_voice.os.replace", wraps=real_replace) as replace:
            store = VoiceStatusStore(Path(temp_dir))
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.0)
            clock.now = 1.0
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.1)
            clock.now = 4.999
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.2)
            clock.now = 5.0
            store.write(state=VoiceState.WAKE_LISTEN, rms=0.3)
            payload = json.loads(store.path.read_text(encoding="utf-8"))

        self.assertEqual(replace.call_count, 2)
        self.assertEqual(payload["rms"], 0.3)
        self.assertEqual(payload["updated_at"], 1005)


class MicrophoneSessionErrorTests(unittest.TestCase):
    @staticmethod
    def _run_with_stream_factory(factory: object):
        fake_sounddevice = types.ModuleType("sounddevice")
        fake_sounddevice.RawInputStream = factory  # type: ignore[attr-defined]
        with TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ, {"OPENKAKAO_VOICE_ENV": "1"}
        ), mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice}), mock.patch(
            "jarvis_voice.resolve_custom_wake_model", return_value=None
        ), mock.patch("jarvis_voice.OpenWakeVadFrontend", return_value=object()):
            return run_microphone_session(state_root=Path(temp_dir))

    def test_session_distinguishes_unavailable_from_disconnected_input(self) -> None:
        def unavailable(**_kwargs: object):
            raise OSError("no input device")

        unavailable_result = self._run_with_stream_factory(unavailable)
        disconnected_result = self._run_with_stream_factory(
            lambda **kwargs: FakeStream(active=False, **kwargs)
        )

        self.assertEqual(unavailable_result.state, VoiceState.ERROR)
        self.assertEqual(unavailable_result.error_code, "mic_unavailable")
        self.assertEqual(disconnected_result.state, VoiceState.ERROR)
        self.assertEqual(disconnected_result.error_code, "mic_disconnected")


class FixedStt:
    def transcribe(self, pcm16: bytes, sample_rate: int, token: object) -> str:
        return " 회의 일정 알려줘 "


class FixedLlm:
    def generate(self, text: str, token: object, *, history: object = ()) -> str:
        return "세 시입니다."


class FailingTts:
    def speak(self, text: str, token: object) -> None:
        raise RuntimeError("speaker offline")


class FakeWakeFrontend:
    def __init__(self, scores: list[tuple[float, float | None]]) -> None:
        self.scores = list(scores)
        self.frames: list[bytes] = []

    def analyze(self, pcm16: bytes) -> AudioFrameAnalysis:
        self.frames.append(pcm16)
        stock, custom = self.scores.pop(0) if self.scores else (0.0, None)
        return AudioFrameAnalysis(
            rms=0.2, speech=False, stock_wake_score=stock, custom_wake_score=custom
        )


class FakeTtsEngine:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, Path]] = []

    def write_wav(self, text: str, path: Path, token: object) -> dict[str, object]:
        self.calls.append((text, path))
        if self.error is not None:
            raise self.error
        return {"path": str(path), "bytes": 44, "sample_rate": 24000, "nframes": 0}


class FailedTurnResultTests(unittest.TestCase):
    def test_failed_turn_keeps_the_transcript_and_carries_no_reply(self) -> None:
        with TemporaryDirectory() as temp_dir:
            pipeline = JarvisVoicePipeline(
                stt=FixedStt(),
                llm=FixedLlm(),
                tts=FailingTts(),
                token=AbortController(Path(temp_dir)).token(),
            )
            failed = pipeline.process_utterance(b"\x01\x00" * VOICE_MIC_FRAME_SAMPLES)
            later = pipeline.mic_disconnected()

        self.assertEqual(
            failed, VoiceResult(VoiceState.ERROR, "tts_error", "회의 일정 알려줘", "")
        )
        self.assertEqual(
            later, VoiceResult(VoiceState.ERROR, "mic_disconnected", "회의 일정 알려줘", "")
        )


class FilePipelineReportTests(unittest.TestCase):
    FRAME = b"\x01\x00" * VOICE_MIC_FRAME_SAMPLES

    def _run(
        self,
        scores: list[tuple[float, float | None]],
        *,
        wake_pcm: bytes | None = None,
        custom_model: Path | None = None,
        tts_error: Exception | None = None,
        abort_first: bool = False,
    ) -> tuple[dict[str, object], FakeWakeFrontend, FakeTtsEngine, dict[str, mock.Mock], dict[str, object]]:
        frontend = FakeWakeFrontend(scores)
        engine = FakeTtsEngine(tts_error)
        wake = self.FRAME * 3 if wake_pcm is None else wake_pcm
        constructors = {
            "frontend": mock.Mock(return_value=frontend),
            "stt": mock.Mock(return_value=FixedStt()),
            "llm": mock.Mock(return_value=FixedLlm()),
            "tts": mock.Mock(return_value=engine),
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            if abort_first:
                AbortController(root).abort()
            with mock.patch.dict(os.environ, {"OPENKAKAO_VOICE_ENV": "1"}), mock.patch(
                "jarvis_voice.resolve_custom_wake_model", return_value=custom_model
            ), mock.patch("jarvis_voice.OpenWakeVadFrontend", constructors["frontend"]), mock.patch(
                "jarvis_voice.MlxWhisperAdapter", constructors["stt"]
            ), mock.patch("jarvis_voice.LocalMlxLlm", constructors["llm"]), mock.patch(
                "jarvis_voice.Qwen3TtsAdapter", constructors["tts"]
            ), mock.patch(
                "jarvis_voice._load_wav_pcm16",
                side_effect=lambda path: wake if path.name == "wake.wav" else self.FRAME * 4,
            ):
                report = run_file_pipeline(
                    state_root=root,
                    wake_wav=root / "wake.wav",
                    utterance_wav=root / "utterance.wav",
                    tts_out=root / "reply.wav",
                    custom_wake_model=custom_model,
                )
            status = json.loads((root / "jarvis-voice-status.json").read_text(encoding="utf-8"))
            constructors["llm"].assert_called_once_with(state_root=root)
            constructors["frontend"].assert_called_once_with(custom_wake_model=custom_model)
            report = json.loads(json.dumps(report).replace(str(root), "<root>"))
        return report, frontend, engine, constructors, status

    def test_wake_miss_reports_the_strongest_scores(self) -> None:
        report, frontend, engine, _constructors, status = self._run(
            [(0.2, 0.3), (0.4, None)], wake_pcm=self.FRAME * 3 + b"\x00" * 100
        )

        self.assertEqual(
            list(report.items()),
            [
                ("accepted", False),
                ("state", "ended"),
                ("error_code", "wake_miss"),
                ("stock_max", 0.4),
                ("custom_max", 0.3),
                ("custom_model_selected", False),
            ],
        )
        self.assertEqual(frontend.frames, [self.FRAME] * 3)
        self.assertEqual(engine.calls, [])
        self.assertEqual((status["state"], status["error_code"]), ("ended", "wake_miss"))

    def test_abort_while_waiting_for_the_wake_word_ends_the_run(self) -> None:
        report, frontend, _engine, _constructors, _status = self._run(
            [(0.9, None)], abort_first=True
        )

        self.assertEqual(
            list(report.items()),
            [
                ("accepted", False),
                ("state", "aborted"),
                ("error_code", "global_abort"),
                ("stock_max", 0.9),
                ("custom_max", 0.0),
                ("custom_model_selected", False),
            ],
        )
        self.assertEqual(len(frontend.frames), 1)

    def test_accepted_wake_runs_one_turn_and_reports_it(self) -> None:
        custom_model = Path("/models/hey_jarvis_ko.onnx")
        report, frontend, engine, constructors, status = self._run(
            [(0.1, 0.2), (0.3, 0.8), (0.99, 0.99)], custom_model=custom_model
        )

        self.assertEqual(
            list(report.items()),
            [
                ("accepted", True),
                ("state", "ended"),
                ("error_code", ""),
                ("transcript", "회의 일정 알려줘"),
                ("reply", "세 시입니다."),
                ("stock_max", 0.3),
                ("custom_max", 0.8),
                ("custom_model_selected", True),
                ("threshold", 0.65),
                (
                    "tts",
                    {"path": "<root>/reply.wav", "bytes": 44, "sample_rate": 24000, "nframes": 0},
                ),
                # The turn re-arms the wake listener before the report is built.
                ("wake_source", "none"),
            ],
        )
        self.assertEqual(len(frontend.frames), 2)
        self.assertEqual([text for text, _path in engine.calls], ["세 시입니다."])
        constructors["stt"].assert_called_once_with(model=WHISPER_MODEL_ID)
        self.assertEqual(status["state"], "wake_listen")
        self.assertTrue(status["custom_model_selected"])

    def test_failed_speech_reports_the_error_without_a_reply(self) -> None:
        report, _frontend, _engine, _constructors, _status = self._run(
            [(0.7, None)], tts_error=RuntimeError("disk full")
        )

        self.assertEqual(
            list(report.items()),
            [
                ("accepted", True),
                ("state", "error"),
                ("error_code", "tts_error"),
                ("transcript", "회의 일정 알려줘"),
                ("reply", ""),
                ("stock_max", 0.7),
                ("custom_max", 0.0),
                ("custom_model_selected", False),
                ("threshold", 0.65),
                ("tts", {}),
                ("wake_source", "stock"),
            ],
        )

    def test_refuses_to_run_outside_the_voice_environment(self) -> None:
        with TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, clear=False) as env:
            env.pop("OPENKAKAO_VOICE_ENV", None)
            with self.assertRaisesRegex(RuntimeError, "voice_environment_not_isolated"):
                run_file_pipeline(
                    state_root=Path(temp_dir),
                    wake_wav=Path(temp_dir) / "wake.wav",
                    utterance_wav=Path(temp_dir) / "utterance.wav",
                    tts_out=Path(temp_dir) / "reply.wav",
                )


class VoiceCommandLineTests(unittest.TestCase):
    def _main(self, argv: list[str], **patches: object) -> tuple[int, str]:
        import io
        from contextlib import redirect_stdout

        stdout = io.StringIO()
        patches.setdefault("run_microphone_session", mock.Mock(side_effect=AssertionError))
        with mock.patch.multiple("jarvis_voice", **patches), redirect_stdout(stdout):
            code = jarvis_voice.main(argv)
        return code, stdout.getvalue()

    def test_microphone_session_exit_code_follows_the_final_state(self) -> None:
        cases = [
            (VoiceResult(VoiceState.ENDED, "silence_timeout"), 0),
            (VoiceResult(VoiceState.ABORTED, "global_abort"), 0),
            (VoiceResult(VoiceState.ERROR, "mic_unavailable"), 1),
        ]
        for result, expected in cases:
            with self.subTest(state=result.state):
                session = mock.Mock(return_value=result)
                code, printed = self._main(
                    ["--state-root", "/state", "--custom-wake-model", "/models/ko.onnx"],
                    run_microphone_session=session,
                )
                self.assertEqual(code, expected)
                self.assertEqual(
                    printed,
                    json.dumps({"state": result.state.value, "error_code": result.error_code}) + "\n",
                )
                session.assert_called_once_with(
                    state_root=Path("/state"), custom_wake_model=Path("/models/ko.onnx")
                )

    def test_default_state_root_honours_the_override(self) -> None:
        session = mock.Mock(return_value=VoiceResult(VoiceState.ENDED))
        with mock.patch.dict(os.environ, {"OPENKAKAO_STATE_ROOT": "/override/state"}):
            self._main([], run_microphone_session=session)
        session.assert_called_once_with(
            state_root=Path("/override/state"), custom_wake_model=None
        )

    def test_file_pipeline_needs_all_three_paths(self) -> None:
        for argv in (["--file-wake", "w.wav"], ["--tts-out", "o.wav", "--file-utterance", "u.wav"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as raised:
                    self._main(["--state-root", "/state", *argv])
                self.assertEqual(raised.exception.code, "file_pipeline_args_incomplete")

    def test_file_pipeline_exit_code_needs_an_accepted_ended_turn(self) -> None:
        argv = [
            "--state-root", "/state", "--file-wake", "w.wav",
            "--file-utterance", "u.wav", "--tts-out", "o.wav",
        ]
        cases = [
            ({"accepted": True, "state": "ended", "reply": "예"}, 0),
            ({"accepted": True, "state": "error"}, 1),
            ({"accepted": False, "state": "ended"}, 1),
        ]
        for report, expected in cases:
            with self.subTest(report=report):
                pipeline = mock.Mock(return_value=report)
                code, printed = self._main(argv, run_file_pipeline=pipeline)
                self.assertEqual(code, expected)
                self.assertEqual(printed, json.dumps(report, ensure_ascii=False) + "\n")
                pipeline.assert_called_once_with(
                    state_root=Path("/state"),
                    wake_wav=Path("w.wav"),
                    utterance_wav=Path("u.wav"),
                    tts_out=Path("o.wav"),
                    custom_wake_model=None,
                )


class WavWriterWithoutNumpyTests(unittest.TestCase):
    def test_peak_normalised_pcm16_is_written_when_numpy_is_absent(self) -> None:
        import wave
        from array import array

        engine = types.SimpleNamespace(
            get_supported_speakers=lambda: [],
            generate_custom_voice=mock.Mock(return_value=([[0.0, [0.5, -2.0]]], 8000)),
        )
        adapter = Qwen3TtsAdapter()
        adapter._engine = engine
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "nested" / "reply.wav"
            with mock.patch.dict(sys.modules, {"numpy": None}):
                meta = adapter.write_wav("안녕하세요", output, AbortController(root).token())
            with wave.open(str(output), "rb") as handle:
                written = (
                    handle.getnchannels(),
                    handle.getsampwidth(),
                    handle.getframerate(),
                    handle.readframes(handle.getnframes()),
                )
            size = output.stat().st_size

        self.assertEqual(written, (1, 2, 8000, array("h", [0, 8192, -32767]).tobytes()))
        self.assertEqual(
            meta, {"path": str(output), "bytes": size, "sample_rate": 8000, "nframes": 3}
        )
        engine.generate_custom_voice.assert_called_once_with(
            text="안녕하세요", speaker="ryan", language="Korean"
        )


if __name__ == "__main__":
    unittest.main()
