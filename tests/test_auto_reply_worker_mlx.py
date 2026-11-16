import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKER = SCRIPTS / "auto-reply-worker.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from local_mlx_gateway import mlx_model_swap_lease


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=None):
        return json.dumps(self.payload).encode("utf-8")


class AutoReplyWorkerMlxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("auto_reply_worker_mlx", WORKER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.module = module

    def setUp(self):
        self.state_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.state_dir.cleanup)
        patcher = mock.patch.object(
            self.module,
            "_operator_state_root",
            return_value=Path(self.state_dir.name),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _advertised_models():
        return {
            "data": [
                {
                    "id": "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit",
                    "owned_by": "mlx-serve",
                    "loaded": False,
                    "state": "unloaded",
                },
                {
                    "id": "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                    "owned_by": "mlx-serve",
                    "loaded": True,
                    "state": "ready",
                },
            ]
        }

    def test_mlx_generation_uses_discovered_gateway_and_advertised_id(self):
        module = self.module
        for selected_model in (
            "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
            "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
        ):
            with self.subTest(selected_model=selected_model):
                calls = []
                payloads = []

                def fake_urlopen(request, timeout=None):
                    calls.append((request.full_url, request.data, timeout))
                    if request.full_url == "http://127.0.0.1:11234/v1/models":
                        return _Response(self._advertised_models())
                    if request.full_url == "http://127.0.0.1:11234/v1/chat/completions":
                        payloads.append(json.loads(request.data.decode("utf-8")))
                        return _Response({"choices": [{"message": {"content": "ok"}}]})
                    raise AssertionError(f"unexpected URL: {request.full_url}")

                with (
                    mock.patch(
                        "auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen
                    ),
                    mock.patch("urllib.request.urlopen") as direct_urlopen,
                ):
                    code, stdout, stderr = module._run_opencodex_generation(
                        selected_model,
                        "system",
                        b'{"inbound":"hello"}',
                        timeout=90.0,
                    )

                self.assertEqual((code, stdout, stderr), (0, b"ok", b""))
                self.assertEqual(
                    payloads[0]["model"],
                    "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                )
                self.assertEqual(calls[-1][0], "http://127.0.0.1:11234/v1/chat/completions")
                self.assertTrue(all("10100" not in url for url, _data, _timeout in calls))
                self.assertTrue(all("/load" not in url for url, _data, _timeout in calls))
                direct_urlopen.assert_not_called()

    def test_mlx_generation_fails_before_gateway_when_swap_owns_exclusive_lease(self):
        module = self.module
        with mlx_model_swap_lease(Path(self.state_dir.name)):
            with mock.patch(
                "auto_reply_ondevice._local_only_urlopen",
                side_effect=AssertionError("gateway must not be called during swap"),
            ):
                result = module._run_opencodex_generation(
                    "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                    "system",
                    b"hello",
                )
        self.assertEqual(result, (1, b"", b"model_swap_in_progress"))

    def test_stale_operator_prompt_cannot_restore_ai_accusation_probe(self):
        module = self.module
        stale = (
            "If inbound accuses this account of being AI/봇, write one curious "
            "question asking which part felt off."
        )
        with mock.patch.object(
            module,
            "_load_operator_reply_prompts",
            return_value={"system": [], "instruction": [stale]},
        ):
            instructions = module._reply_decision_instructions()
        self.assertNotIn(stale, instructions)
        self.assertTrue(
            any("do not ask a follow-up or turn it into a meta-conversation" in item for item in instructions)
        )

    def _run_mlx_completion_response(self, payload):
        module = self.module

        def fake_urlopen(request, timeout=None):
            del timeout
            if request.full_url == "http://127.0.0.1:11234/v1/models":
                return _Response(self._advertised_models())
            if request.full_url == "http://127.0.0.1:11234/v1/chat/completions":
                return _Response(payload)
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with (
            mock.patch("auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen),
            mock.patch("urllib.request.urlopen") as direct_urlopen,
        ):
            result = module._run_opencodex_generation(
                "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                "system",
                b'{"inbound":"hello"}',
                timeout=90.0,
            )
        direct_urlopen.assert_not_called()
        return result

    def test_mlx_generation_accepts_matching_canonical_response_model(self):
        result = self._run_mlx_completion_response({
            "model": "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
            "choices": [{"message": {"content": "ok"}}],
        })
        self.assertEqual(result, (0, b"ok", b""))

    def test_mlx_generation_rejects_mismatched_response_model_before_choices(self):
        result = self._run_mlx_completion_response({
            "model": "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit",
        })
        self.assertEqual(result, (1, b"", b"mlx_serve_response_model_mismatch"))

    def test_mlx_generation_without_response_model_stays_fail_open(self):
        result = self._run_mlx_completion_response({
            "choices": [{"message": {"content": "ok"}}],
        })
        self.assertEqual(result, (0, b"ok", b""))

    def test_mlx_generation_rejects_oversized_completion_response(self):
        module = self.module
        oversized = mock.MagicMock()
        oversized.__enter__.return_value = oversized
        oversized.read.return_value = b"x" * (
            module.auto_reply_ondevice.MLX_GATEWAY_MAX_RESPONSE_BYTES + 1
        )

        def fake_urlopen(request, timeout=None):
            del timeout
            if request.full_url == "http://127.0.0.1:11234/v1/models":
                return _Response(self._advertised_models())
            if request.full_url == "http://127.0.0.1:11234/v1/chat/completions":
                return oversized
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with (
            mock.patch("auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen),
            mock.patch("urllib.request.urlopen") as direct_urlopen,
        ):
            result = module._run_opencodex_generation(
                "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                "system",
                b'{"inbound":"hello"}',
                timeout=90.0,
            )

        self.assertEqual(result, (1, b"", b"mlx_gateway_response_too_large"))
        oversized.read.assert_called_once_with(
            module.auto_reply_ondevice.MLX_GATEWAY_MAX_RESPONSE_BYTES + 1
        )
        direct_urlopen.assert_not_called()

    def test_missing_gateway_fails_closed_in_candidate_order(self):
        module = self.module
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, request.data, timeout))
            raise urllib.error.URLError("offline")

        with mock.patch("auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen):
            code, stdout, stderr = module._run_opencodex_generation(
                "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
                "system",
                b'{"inbound":"hello"}',
                timeout=90.0,
            )

        self.assertEqual(code, 1)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b"mlx_serve_gateway_unavailable")
        self.assertEqual(
            [url for url, _data, _timeout in calls],
            [
                "http://127.0.0.1:11234/v1/models",
            ],
        )
        self.assertTrue(all(data is None for _url, data, _timeout in calls))

    def test_flash_models_use_only_the_fixed_loopback_gateway(self):
        module = self.module
        for selected_model in (
            "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
            "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
        ):
            with self.subTest(selected_model=selected_model):
                calls = []

                def fake_urlopen(request, timeout=None):
                    calls.append((request.full_url, request.data, timeout))
                    raise urllib.error.URLError("offline")

                with mock.patch(
                    "auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen
                ):
                    code, stdout, stderr = module._run_opencodex_generation(
                        selected_model,
                        "system",
                        b'{"inbound":"hello"}',
                        timeout=90.0,
                    )

                self.assertEqual(code, 1)
                self.assertEqual(stdout, b"")
                self.assertEqual(stderr, b"mlx_serve_gateway_unavailable")
                self.assertEqual(
                    [url for url, _data, _timeout in calls],
                    ["http://127.0.0.1:11234/v1/models"],
                )
                self.assertTrue(all(data is None for _url, data, _timeout in calls))

    def test_27b_model_uses_only_the_fixed_loopback_gateway(self):
        module = self.module
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, request.data, timeout))
            raise urllib.error.URLError("offline")

        with mock.patch(
            "auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen
        ):
            code, stdout, stderr = module._run_opencodex_generation(
                "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit",
                "system",
                b'{"inbound":"hello"}',
                timeout=90.0,
            )

        self.assertEqual((code, stdout, stderr), (1, b"", b"mlx_serve_gateway_unavailable"))
        self.assertEqual(
            [url for url, _data, _timeout in calls],
            ["http://127.0.0.1:11234/v1/models"],
        )

    def test_qwen27b_image_generation_fails_closed_when_unloaded(self):
        module = self.module
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, request.data, timeout))
            if request.full_url == "http://127.0.0.1:11234/v1/models":
                return _Response(self._advertised_models())
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "photo.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            with (
                mock.patch(
                    "auto_reply_ondevice._local_only_urlopen",
                    side_effect=fake_urlopen,
                ),
                mock.patch("urllib.request.urlopen") as direct_urlopen,
            ):
                result = module._run_opencodex_generation(
                    module.QWEN38_27B_MODEL_ID,
                    "system",
                    b' {"inbound":"photo"}',
                    image_paths=[image],
                    timeout=90.0,
                )

        self.assertEqual(result, (1, b"", b"mlx_serve_vision_model_not_resident"))
        self.assertTrue(calls)
        self.assertTrue(
            all(url == "http://127.0.0.1:11234/v1/models" for url, _data, _timeout in calls)
        )
        self.assertTrue(all(data is None for _url, data, _timeout in calls))
        direct_urlopen.assert_not_called()

    def test_image_candidate_never_falls_back_to_flash_next(self):
        module = self.module
        with (
            mock.patch.object(module, "_run_opencodex_generation") as http_runner,
            mock.patch.object(module, "_run_bounded_process") as process_runner,
        ):
            result = module._run_generation_candidate(
                module.FLASH_NEXT_MODEL_ID,
                "system",
                b"{}",
                command=["gjc", "--model", module.FLASH_NEXT_MODEL_ID],
                env={},
                model_stdin_bytes=b"{}",
                image_paths=[Path("photo.png")],
                timeout=90.0,
            )

        self.assertEqual(result, (1, b"", b"local_vision_model_required"))
        http_runner.assert_not_called()
        process_runner.assert_not_called()

    def test_generation_candidate_blocks_product_cloud_fallback(self):
        module = self.module
        command = ["gjc", "--model", "primary/model"]
        mlx_model = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"

        with (
            mock.patch.object(module, "REPLY_RUNNER_KIND", "gjc"),
            mock.patch.object(
                module,
                "_run_opencodex_generation",
                return_value=(0, b"mlx", b""),
            ) as http_runner,
            mock.patch.object(
                module,
                "_run_bounded_process",
                return_value=(0, b"gjc", b""),
            ) as process_runner,
        ):
            result = module._run_generation_candidate(
                mlx_model,
                "system",
                b"{}",
                command=command,
                env={},
                model_stdin_bytes=b"{}",
                image_paths=None,
                timeout=90.0,
            )
            self.assertEqual(result, (0, b"mlx", b""))
            http_runner.assert_called_once()
            process_runner.assert_not_called()

            http_runner.reset_mock()
            process_runner.reset_mock()
            result = module._run_generation_candidate(
                module.QWEN38_27B_MODEL_ID,
                "system",
                b"{}",
                command=command,
                env={},
                model_stdin_bytes=b"{}",
                image_paths=[Path("photo.png")],
                timeout=90.0,
            )
            self.assertEqual(result, (0, b"mlx", b""))
            http_runner.assert_called_once()
            process_runner.assert_not_called()

            http_runner.reset_mock()
            process_runner.reset_mock()
            result = module._run_generation_candidate(
                "google-antigravity/gemini-3.8-flash",
                "system",
                b"{}",
                command=command,
                env={},
                model_stdin_bytes=b"{}",
                image_paths=None,
                timeout=45.0,
            )
            self.assertEqual(result, (1, b"", b"product_cloud_fallback_disabled"))
            http_runner.assert_not_called()
            process_runner.assert_not_called()

    SUSHI_MODEL_ID = "beamster/Qwen3.8-Flash-Next-Sushi-2bpw"
    SUSHI_4BPW_MODEL_ID = "beamster/Qwen3.8-Flash-Next-Sushi-4bpw"
    DDALCU_FLASH_NEXT_MODEL_ID = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
    QWEN38_27B_ROW_ID = "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
    QWEN38_27B_8BIT_ROW_ID = "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-8bit"
    QWEN36_35B_MODEL_ID = "ddalcu/Qwen3.6-35B-A3B-MLX-Serve-4bit"

    @staticmethod
    def _gateway_row(model_id, *, loaded=True):
        return {
            "id": model_id,
            "owned_by": "mlx-serve",
            "loaded": loaded,
            "state": "ready" if loaded else "unloaded",
        }

    def _run_sushi_candidate(self, model, completion, *, image_paths=None):
        return self._run_local_candidate(
            model,
            [
                self._gateway_row(self.QWEN36_35B_MODEL_ID),
                self._gateway_row(self.SUSHI_MODEL_ID),
            ],
            completion,
            image_paths=image_paths,
        )

    def _run_local_candidate(self, model, rows, completion, *, image_paths=None):
        module = self.module
        calls = []
        payloads = []

        def fake_urlopen(request, timeout=None):
            del timeout
            calls.append(request.full_url)
            if request.full_url == "http://127.0.0.1:11234/v1/models":
                return _Response({"data": rows})
            if request.full_url == "http://127.0.0.1:11234/v1/chat/completions":
                payloads.append(json.loads(request.data.decode("utf-8")))
                return _Response(completion)
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with (
            mock.patch.object(module, "REPLY_RUNNER_KIND", "opencodex"),
            mock.patch("auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen),
            mock.patch("urllib.request.urlopen") as direct_urlopen,
        ):
            result = module._run_generation_candidate(
                model,
                "system",
                b'{"inbound":"hello"}',
                command=["opencodex"],
                env={},
                model_stdin_bytes=None,
                image_paths=image_paths,
                timeout=90.0,
            )
        direct_urlopen.assert_not_called()
        return result, calls, payloads

    def test_flash_next_sushi_is_a_local_text_reply_model(self):
        module = self.module
        answered = {
            "model": self.SUSHI_MODEL_ID,
            "choices": [{"message": {"content": "ok"}}],
        }
        for selected_model in (self.SUSHI_MODEL_ID, f"mlx/{self.SUSHI_MODEL_ID}"):
            with self.subTest(selected_model=selected_model):
                result, _calls, payloads = self._run_sushi_candidate(selected_model, answered)
                self.assertEqual(result, (0, b"ok", b""))
                self.assertEqual([payload["model"] for payload in payloads], [self.SUSHI_MODEL_ID])

                result, calls, _payloads = self._run_sushi_candidate(
                    selected_model, answered, image_paths=[Path("photo.png")]
                )
                self.assertEqual(result, (1, b"", b"local_vision_model_required"))
                self.assertEqual(calls, [])

        with mock.patch.object(module, "REPLY_MODEL", self.SUSHI_MODEL_ID):
            self.assertEqual(module._active_reply_model(), self.SUSHI_MODEL_ID)

    def test_flash_next_sushi_rejects_an_answer_from_another_model(self):
        result, _calls, _payloads = self._run_sushi_candidate(
            self.SUSHI_MODEL_ID,
            {
                "model": self.QWEN36_35B_MODEL_ID,
                "choices": [{"message": {"content": "ok"}}],
            },
        )
        self.assertEqual(result, (1, b"", b"mlx_serve_response_model_mismatch"))

    def test_local_generation_sends_only_the_requested_model_among_loaded_lookalikes(self):
        cases = (
            (
                (self.DDALCU_FLASH_NEXT_MODEL_ID, self.SUSHI_MODEL_ID),
                self.SUSHI_MODEL_ID,
                self.DDALCU_FLASH_NEXT_MODEL_ID,
            ),
            (
                (self.DDALCU_FLASH_NEXT_MODEL_ID, self.SUSHI_MODEL_ID),
                self.DDALCU_FLASH_NEXT_MODEL_ID,
                self.SUSHI_MODEL_ID,
            ),
            (
                (self.SUSHI_MODEL_ID, self.DDALCU_FLASH_NEXT_MODEL_ID),
                self.SUSHI_MODEL_ID,
                self.DDALCU_FLASH_NEXT_MODEL_ID,
            ),
            (
                (self.SUSHI_MODEL_ID, self.DDALCU_FLASH_NEXT_MODEL_ID),
                self.DDALCU_FLASH_NEXT_MODEL_ID,
                self.SUSHI_MODEL_ID,
            ),
            (
                (self.SUSHI_4BPW_MODEL_ID, self.SUSHI_MODEL_ID),
                self.SUSHI_MODEL_ID,
                self.SUSHI_4BPW_MODEL_ID,
            ),
            (
                (self.QWEN38_27B_8BIT_ROW_ID, self.QWEN38_27B_ROW_ID),
                self.QWEN38_27B_ROW_ID,
                self.QWEN38_27B_8BIT_ROW_ID,
            ),
        )
        for listed, requested_row, other_row in cases:
            rows = [self._gateway_row(model_id) for model_id in listed]
            bare = requested_row.removeprefix("mlx/")
            for selected_model in (bare, f"mlx/{bare}"):
                with self.subTest(listed=listed, selected_model=selected_model):
                    result, _calls, payloads = self._run_local_candidate(
                        selected_model,
                        rows,
                        {"model": other_row, "choices": [{"message": {"content": "ok"}}]},
                    )
                    self.assertEqual(
                        (result, [payload["model"] for payload in payloads]),
                        ((1, b"", b"mlx_serve_response_model_mismatch"), [requested_row]),
                    )

                    result, _calls, payloads = self._run_local_candidate(
                        selected_model,
                        rows,
                        {"model": requested_row, "choices": [{"message": {"content": "ok"}}]},
                    )
                    self.assertEqual(
                        (result, [payload["model"] for payload in payloads]),
                        ((0, b"ok", b""), [requested_row]),
                    )

    def test_local_generation_fails_closed_when_the_requested_model_is_not_loaded(self):
        cases = (
            (
                self.SUSHI_MODEL_ID,
                self.DDALCU_FLASH_NEXT_MODEL_ID,
                [self._gateway_row(self.SUSHI_MODEL_ID, loaded=False)],
            ),
            (self.SUSHI_MODEL_ID, self.SUSHI_4BPW_MODEL_ID, []),
            (self.DDALCU_FLASH_NEXT_MODEL_ID, self.SUSHI_MODEL_ID, []),
            (self.QWEN38_27B_ROW_ID, self.QWEN38_27B_8BIT_ROW_ID, []),
        )
        for requested, loaded_lookalike, other_rows in cases:
            rows = [self._gateway_row(loaded_lookalike), *other_rows]
            with self.subTest(requested=requested, loaded=loaded_lookalike):
                result, calls, payloads = self._run_local_candidate(
                    requested,
                    rows,
                    {"model": loaded_lookalike, "choices": [{"message": {"content": "ok"}}]},
                )
                self.assertEqual(
                    (result, [payload["model"] for payload in payloads]),
                    ((1, b"", b"mlx_serve_text_model_not_advertised"), []),
                )
                self.assertNotIn("http://127.0.0.1:11234/v1/chat/completions", calls)

    def test_unlisted_local_model_stays_refused(self):
        module = self.module
        result, calls, _payloads = self._run_sushi_candidate(
            self.QWEN36_35B_MODEL_ID,
            {"choices": [{"message": {"content": "ok"}}]},
        )
        self.assertEqual(result, (1, b"", b"product_cloud_fallback_disabled"))
        self.assertEqual(calls, [])
        with mock.patch.object(module, "REPLY_MODEL", self.QWEN36_35B_MODEL_ID):
            self.assertEqual(module._active_reply_model(), module.FLASH_NEXT_MODEL_ID)

    def test_prefixless_mlx_keeps_local_generation_timeout(self):
        module = self.module
        prefixless = "ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
        prefixed = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
        self.assertEqual(module._model_generation_timeout(prefixless), 90.0)
        self.assertEqual(module._model_generation_timeout(prefixed), 90.0)
        self.assertEqual(
            module._model_generation_timeout("google-antigravity/gemini-3.8-flash"),
            45.0,
        )


if __name__ == "__main__":
    unittest.main()
