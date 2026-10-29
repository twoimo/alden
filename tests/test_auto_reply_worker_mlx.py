import importlib.util
from contextlib import contextmanager
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
    LEGACY_GATEWAY = "http://127.0.0.1:11234/v1"
    IQ_GATEWAY = "http://127.0.0.1:11235/v1"
    MIXED_FLASH = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
    IQ_FLASH = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw"
    QWEN_27B = "mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit"

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

    @classmethod
    def _ready_catalog(
        cls,
        order=("mixed", "iq"),
        *,
        iq_loaded=True,
        include_iq=True,
        iq_model_id=None,
    ):
        rows = {
            "mixed": {
                "id": cls.MIXED_FLASH.removeprefix("mlx/"),
                "owned_by": "mlx-serve",
                "loaded": True,
                "state": "ready",
            },
            "iq": {
                "id": iq_model_id or cls.IQ_FLASH,
                "owned_by": "mlx-serve",
                "loaded": iq_loaded,
                "state": "ready" if iq_loaded else "unloaded",
            },
        }
        return {"data": [rows[name] for name in order if name != "iq" or include_iq]}

    def _run_with_catalog(self, selected_model, catalog, *, gateway_base_url=None):
        module = self.module
        calls = []
        payloads = []
        gateway_base_url = gateway_base_url or self.LEGACY_GATEWAY

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, request.data, timeout))
            if request.full_url == f"{gateway_base_url}/models":
                return _Response(catalog)
            if request.full_url == f"{gateway_base_url}/chat/completions":
                payloads.append(json.loads(request.data.decode("utf-8")))
                return _Response({"choices": [{"message": {"content": "ok"}}]})
            raise AssertionError(f"unexpected URL: {request.full_url}")

        with (
            mock.patch("auto_reply_ondevice._local_only_urlopen", side_effect=fake_urlopen),
            mock.patch("urllib.request.urlopen") as direct_urlopen,
        ):
            result = module._run_opencodex_generation(
                selected_model,
                "system",
                b'{"inbound":"hello"}',
                timeout=90.0,
            )
        direct_urlopen.assert_not_called()
        return result, calls, payloads

    def test_flash_pack_selection_is_exact_in_either_catalog_order(self):
        for order in (("mixed", "iq"), ("iq", "mixed")):
            catalog = self._ready_catalog(order)
            for selected_model, expected_advertised, gateway_base_url in (
                (
                    self.MIXED_FLASH,
                    self.MIXED_FLASH.removeprefix("mlx/"),
                    self.LEGACY_GATEWAY,
                ),
                (
                    self.IQ_FLASH.removeprefix("mlx/"),
                    self.IQ_FLASH,
                    self.IQ_GATEWAY,
                ),
            ):
                with self.subTest(order=order, selected_model=selected_model):
                    result, calls, payloads = self._run_with_catalog(
                        selected_model,
                        catalog,
                        gateway_base_url=gateway_base_url,
                    )
                    self.assertEqual(result, (0, b"ok", b""))
                    self.assertEqual(payloads[0]["model"], expected_advertised)
                    self.assertEqual(
                        calls[-1][0], f"{gateway_base_url}/chat/completions"
                    )

    def test_iq_request_fails_closed_when_exact_pack_absent_wrong_or_unloaded(self):
        for label, catalog in (
            ("absent", self._ready_catalog(include_iq=False)),
            (
                "wrong",
                self._ready_catalog(
                    iq_model_id="mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-4.0bpw"
                ),
            ),
            ("unloaded", self._ready_catalog(iq_loaded=False)),
        ):
            with self.subTest(label=label):
                result, calls, payloads = self._run_with_catalog(
                    self.IQ_FLASH,
                    catalog,
                    gateway_base_url=self.IQ_GATEWAY,
                )
                self.assertEqual(result, (1, b"", b"mlx_serve_text_model_not_advertised"))
                self.assertEqual(payloads, [])
                self.assertFalse(
                    any(url.endswith("/chat/completions") for url, _data, _timeout in calls)
                )
                self.assertEqual(
                    [url for url, _data, _timeout in calls],
                    [f"{self.IQ_GATEWAY}/models"],
                )
                self.assertTrue(all("11234" not in url for url, _data, _timeout in calls))

    def test_unknown_or_cloudish_flash_ids_do_not_match_local_pack(self):
        catalog = self._ready_catalog()
        for selected_model in (
            "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-unknown-pack",
            "cloud/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw",
        ):
            with self.subTest(selected_model=selected_model):
                result, calls, payloads = self._run_with_catalog(selected_model, catalog)
                self.assertEqual(result, (1, b"", b"mlx_serve_text_model_not_advertised"))
                self.assertEqual(payloads, [])
                self.assertFalse(
                    any(url.endswith("/chat/completions") for url, _data, _timeout in calls)
                )

    def test_27b_selection_requires_exact_ready_catalog_id(self):
        catalog = self._ready_catalog()
        catalog["data"].insert(
            1,
            {
                "id": self.QWEN_27B.removeprefix("mlx/"),
                "owned_by": "mlx-serve",
                "loaded": True,
                "state": "ready",
            },
        )
        result, calls, payloads = self._run_with_catalog(self.QWEN_27B, catalog)
        self.assertEqual(result, (0, b"ok", b""))
        self.assertEqual(payloads[0]["model"], self.QWEN_27B.removeprefix("mlx/"))
        self.assertEqual(calls[-1][0], "http://127.0.0.1:11234/v1/chat/completions")

        result, calls, payloads = self._run_with_catalog(
            self.QWEN_27B.replace("-4bit", "-8bit"), catalog
        )
        self.assertEqual(result, (1, b"", b"mlx_serve_text_model_not_advertised"))
        self.assertEqual(payloads, [])
        self.assertFalse(any(url.endswith("/chat/completions") for url, _data, _ in calls))

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
        for selected_model, gateway_base_url in (
            (self.MIXED_FLASH, self.LEGACY_GATEWAY),
            (self.MIXED_FLASH.removeprefix("mlx/"), self.LEGACY_GATEWAY),
            (self.IQ_FLASH, self.IQ_GATEWAY),
            (self.IQ_FLASH.removeprefix("mlx/"), self.IQ_GATEWAY),
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
                    [f"{gateway_base_url}/models"],
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
                mock.patch.object(module.alden_local_vision, "owned_vision_endpoint", side_effect=module.alden_local_vision.VisionUnavailable("local_vision_memory_insufficient")),
                mock.patch.object(module.alden_local_vision, "check_input_budget"),
            ):
                result = module._run_opencodex_generation(
                    module.QWEN38_27B_MODEL_ID,
                    "system",
                    b' {"inbound":"photo"}',
                    image_paths=[image],
                    timeout=90.0,
                )

        self.assertEqual(result, (1, b"", b"local_vision_memory_insufficient"))
        self.assertTrue(calls)
        self.assertTrue(
            all(url == "http://127.0.0.1:11234/v1/models" for url, _data, _timeout in calls)
        )
        self.assertTrue(all(data is None for _url, data, _timeout in calls))
        direct_urlopen.assert_not_called()

    def test_resident_image_model_requires_advertised_vision_capability(self):
        module = self.module
        model = module.QWEN38_27B_MODEL_ID.removeprefix("mlx/")
        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "photo.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            for capabilities in (["chat"], ["chat", "vision"]):
                with self.subTest(capabilities=capabilities):
                    catalog = [{"id": model, "loaded": True, "state": "ready",
                                "capabilities": capabilities}]
                    with (
                        mock.patch.object(module, "discover_mlx_gateway", return_value=("http://127.0.0.1:11234/v1", catalog)),
                        mock.patch.object(module, "detect_mlx_gateway_models", return_value=catalog),
                        mock.patch("auto_reply_ondevice._local_only_urlopen", return_value=_Response({
                            "choices": [{"message": {"content": "grounded"}}],
                        })) as request,
                        mock.patch("urllib.request.urlopen", side_effect=AssertionError("unmocked transport")),
                    ):
                        result = module._run_opencodex_generation_unleased(
                            module.QWEN38_27B_MODEL_ID, "system", b"photo",
                            image_paths=[image], timeout=5.0,
                        )
                    if "vision" not in capabilities:
                        self.assertEqual(result, (1, b"", b"mlx_serve_vision_capability_unavailable"))
                        request.assert_not_called()
                    else:
                        self.assertEqual(result[0], 0)
                        payload = json.loads(request.call_args.args[0].data)
                        content = payload["messages"][1]["content"]
                        self.assertTrue(any(part.get("image_url", {}).get("url", "").startswith("data:image/png;") for part in content))

    def test_owned_vision_fallback_keeps_pixels_auth_identity_and_lease(self):
        module = self.module
        owned_calls = []
        payloads = []
        @contextmanager
        def endpoint(*args, **kwargs):
            with self.assertRaises(Exception):
                with mlx_model_swap_lease(Path(self.state_dir.name)):
                    pass
            owned_calls.append(kwargs)
            yield module.alden_local_vision.BASE_URL, self.QWEN_27B.removeprefix("mlx/"), ["chat", "vision"], "fixture-key", 3.0, respond
        def respond(request, timeout=None):
            self.assertEqual(request.full_url, "http://127.0.0.1:11237/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer fixture-key")
            payloads.append(json.loads(request.data))
            return _Response({"model": self.QWEN_27B.removeprefix("mlx/"), "choices": [{"message": {"content": "seen"}}]})
        catalog = [{"id": self.QWEN_27B, "loaded": True, "state": "ready", "capabilities": ["chat"]}]
        image = Path(self.state_dir.name) / "photo.png"
        image.write_bytes(b"image-fixture")
        with (
            mock.patch.object(module.alden_local_vision, "check_input_budget"),
            mock.patch.object(module, "discover_mlx_gateway", return_value=(self.LEGACY_GATEWAY, catalog)),
            mock.patch.object(module, "detect_mlx_gateway_models", return_value=catalog),
            mock.patch.object(module.alden_local_vision, "owned_vision_endpoint", side_effect=endpoint),
            mock.patch("auto_reply_ondevice._local_only_urlopen", side_effect=respond),
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("cloud")),
        ):
            result = module._run_opencodex_generation(self.QWEN_27B, "system", b"what is shown", image_paths=[image], timeout=5)
        self.assertEqual(result, (0, b"seen", b""))
        self.assertEqual(len(owned_calls), 1)
        self.assertEqual(payloads[0]["max_tokens"], 1024)
        self.assertEqual(payloads[0]["temperature"], 0)
        self.assertEqual(len(payloads[0]["messages"][1]["content"]), 2)
        self.assertTrue(payloads[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:"))

    def test_unreadable_image_never_launches_owned_vision(self):
        module = self.module
        catalog = [{"id": self.QWEN_27B, "loaded": True, "state": "ready", "capabilities": ["chat"]}]
        with (
            mock.patch.object(module, "discover_mlx_gateway", return_value=(self.LEGACY_GATEWAY, catalog)),
            mock.patch.object(module, "detect_mlx_gateway_models", return_value=catalog),
            mock.patch.object(module.alden_local_vision, "owned_vision_endpoint") as launch,
        ):
            result = module._run_opencodex_generation(self.QWEN_27B, "system", b"photo", image_paths=[Path(self.state_dir.name)/"missing.png"])
        self.assertEqual(result, (1, b"", b"image_input_unavailable"))
        launch.assert_not_called()

    def test_owned_vision_missing_or_wrong_response_identity_is_rejected(self):
        module = self.module
        for identity in (None, self.IQ_FLASH, "other/Qwen3.8-27B-MLX-Serve-4bit"):
            with self.subTest(identity=identity), mock.patch("auto_reply_ondevice._local_only_urlopen", return_value=_Response({"model": identity, "choices": [{"message": {"content": "untrusted"}}]})):
                result = module._run_opencodex_generation_unleased(self.QWEN_27B, "system", b"photo", _owned_vision=(module.alden_local_vision.BASE_URL,self.QWEN_27B, ["vision"], "fixture-key", module.auto_reply_ondevice._local_only_urlopen))
            self.assertEqual(result, (1, b"", b"mlx_serve_response_model_mismatch"))

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
