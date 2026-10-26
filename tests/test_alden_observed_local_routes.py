"""Observed local routes preserve exact IDs and ports, without real HTTP.

Catalog and selection use the production implementations and temporary state.
Only discovery, HTTP responses, memory observation, and unrelated retrieval are
controlled; adapter readiness remains part of each generation attempt.
"""
import hashlib
import io
import json
from functools import partial
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import alden_model_routes as routes
import alden_voice as voice
from alden_abort import AbortController


BASE34 = "http://127.0.0.1:11234/v1"
BASE35 = "http://127.0.0.1:11235/v1"
IQ47 = "mlx/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-4.7bpw"
IQ33 = voice.FLASH_NEXT_IQ_MODEL_ID
OTHER = "mlx/fixture/observed-chat-4bit"
UNSAFE_URLS = (
    "https://127.0.0.1:11234/v1",
    "http://outside.example:11234/v1",
    "http://localhost:11234/v1",
    "http://127.0.0.2:11234/v1",
    "http://[::1]:11234/v1",
    "http://127.0.0.1:11236/v1",
    "http://127.0.0.1:10101/v1",
    "http://user:secret@127.0.0.1:11234/v1",
    "http://@127.0.0.1:11234/v1",
    BASE34 + "?redirect=outside",
    BASE34 + "#fragment",
    BASE34 + "?",
    BASE34 + "#",
    BASE34 + "/models",
    BASE34 + "/",
    " " + BASE34,
    "http://127.0.0.1:011234/v1",
    "http://127.0.0.1:11234/%76%31",
)


def ready(model, **overrides):
    return {"id": model.removeprefix("mlx/"), "loaded": True,
            "state": "ready", **overrides}


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = {"Content-Type": "application/json"}


class ObservedLocalRoutesTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="alden-observed-route-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "reply-model.json"
        self.token = AbortController(self.root).token()
        self.router_rows = [
            {"slug": model, "supported_reasoning_levels": ["high"]}
            for model in (IQ47, IQ33, OTHER, routes.DEFAULT_MODEL)
        ]
        self.observations = {BASE34: {"data": []}, BASE35: {"data": []}}
        self.discovery_calls = []
        self.memory = "ready"
        self.enterContext(patch.object(routes, "catalog", partial(
            routes.catalog, fetcher=self.fetch, memory_reader=lambda: self.memory)))
        self.enterContext(patch.object(voice, "_voice_knowledge_reference",
                                      return_value=(None, {"state": "test_no_retrieval"})))
        # Even a regression that bypasses the controlled fetcher/HTTP seam
        # cannot contact a real router, model server, or remote host.
        for target in ("urllib.request.OpenerDirector.open", "socket.create_connection",
                       "socket.socket.connect", "socket.socket.connect_ex"):
            guard = self.enterContext(patch(target, side_effect=AssertionError("live network forbidden")))
            self.addCleanup(guard.assert_not_called)

    def fetch(self, url):
        self.discovery_calls.append(url)
        if url == routes.ROUTER + "/models":
            return {"models": self.router_rows}
        for base, payload in self.observations.items():
            if url == base + "/models":
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError("unexpected discovery URL: " + url)

    def observe(self, base, *rows):
        self.observations[base] = {"data": list(rows)}

    def configure(self, model=IQ47):
        self.path.write_text(json.dumps({"schema_version": 1, "model": model,
                                        "reasoning_effort": "high", "transport": "local",
                                        "mode": "manual"}))
        return self.path.read_bytes()

    def row(self, model=IQ47):
        return routes.find_model(routes.catalog(self.root), model)

    def assert_blocked_selection(self, before):
        with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
            routes.selection(self.root)
        self.assertEqual(self.path.read_bytes(), before)

    def transport(self, readiness, calls, *, response_model=IQ47, post_error=None):
        def open_response(request, **_kwargs):
            calls.append(request)
            if request.get_method() == "GET":
                if isinstance(readiness, Exception):
                    raise readiness
                return Response(readiness)
            if post_error is not None:
                raise post_error
            return Response({"model": response_model.removeprefix("mlx/"),
                             "choices": [{"message": {"content": "synthetic answer"},
                                          "finish_reason": "stop"}]})
        return open_response

    def assert_requests(self, calls, base, *, post):
        expected = [("GET", base + "/models")]
        if post:
            expected.append(("POST", base + "/chat/completions"))
        self.assertEqual([(r.get_method(), r.full_url) for r in calls], expected)
        expected_port = 11234 if base == BASE34 else 11235
        self.assertEqual([r._alden_model_port for r in calls], [expected_port] * len(calls))

    def test_catalog_exposes_observed_iq47_port_and_keeps_exact_id(self):
        self.observe(BASE34, ready(IQ47))
        self.observations[BASE35] = OSError("synthetic endpoint unavailable")
        row = self.row()
        self.assertEqual(row["id"], IQ47)
        self.assertEqual(row.get("local_base_url"), BASE34)
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["selectable"])
        self.assertFalse(row["automatic_candidate"])
        self.assertIsNone(self.row(IQ33).get("local_base_url"))
        self.assertFalse(self.row(IQ33)["selectable"])
        self.assertEqual(set(self.discovery_calls), {
            routes.ROUTER + "/models", BASE34 + "/models", BASE35 + "/models"})

    def test_real_save_selection_and_configured_adapter_preserve_observed_route(self):
        for model, base in ((IQ47, BASE34), (OTHER, BASE35), (IQ33, BASE34)):
            with self.subTest(model=model, base=base):
                self.observations = {BASE34: {"data": []}, BASE35: {"data": []}}
                self.observe(base, ready(model))
                routes.save(self.root, {"model": model, "reasoning_effort": "high"}, fetcher=self.fetch)
                before = self.path.read_bytes()
                saved = json.loads(before)
                self.assertEqual(saved["model"], model)
                self.assertEqual(saved["transport"], "local")
                selected = routes.selection(self.root)
                self.assertEqual(selected["model"], model)
                self.assertEqual(selected["base_url"], base)
                self.assertEqual(selected["configuration_sha256"], hashlib.sha256(before).hexdigest())
                calls = []
                adapter = voice.ConfiguredLocalMlxLlm(self.root)
                with patch.object(voice, "_local_urlopen", side_effect=self.transport(
                        {"data": [ready(model)]}, calls, response_model=model)):
                    self.assertEqual(adapter.generate("fixture prompt", self.token), "synthetic answer")
                self.assert_requests(calls, base, post=True)
                self.assertEqual(json.loads(calls[1].data)["model"], model.removeprefix("mlx/"))
                self.assertEqual(adapter.last_metrics["model_selection"]["model"], model)
                self.assertEqual(self.path.read_bytes(), before)

    def test_optional_mlx_prefix_is_the_only_id_alias(self):
        for observed_id in (IQ47, IQ47.removeprefix("mlx/")):
            with self.subTest(observed_id=observed_id):
                self.observe(BASE34, ready(IQ47, id=observed_id))
                self.assertEqual(self.row().get("local_base_url"), BASE34)
        for observed_id in (IQ33, "mlx/" + IQ47, IQ47.upper(), " " + IQ47, IQ47 + "-other"):
            with self.subTest(observed_id=observed_id):
                self.observe(BASE34, ready(IQ47, id=observed_id))
                self.assertIsNone(self.row().get("local_base_url"))
                self.assertFalse(self.row()["selectable"])

    def test_same_exact_id_ready_on_both_ports_is_unavailable_even_after_success(self):
        self.observe(BASE34, ready(IQ47))
        self.observe(BASE35, ready(IQ47, id=IQ47))
        before = self.configure()
        routes.record_result(self.root, IQ47, success=True)
        row = self.row()
        self.assertEqual(row["status"], "unavailable")
        self.assertIsNone(row.get("local_base_url"))
        self.assertFalse(row["selectable"])
        self.assertFalse(row["automatic_candidate"])
        self.assert_blocked_selection(before)
        with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
            routes.save(self.root, {"model": IQ47, "reasoning_effort": "high"}, fetcher=self.fetch)
        self.assertEqual(self.path.read_bytes(), before)

    def test_ambiguous_route_reason_survives_typed_failure_and_saved_receipt(self):
        import uuid
        import alden_chat as chat
        from alden_chat_turns import get_request
        self.observe(BASE34, ready(IQ47))
        self.observe(BASE35, ready(IQ47))
        before = self.configure()
        value = {'request_id':uuid.uuid4().hex,'conversation_id':None,'text':'hello','images':[]}
        token = chat.InputToken(self.root, str(uuid.uuid4()))
        with patch.object(voice, '_local_urlopen', side_effect=AssertionError('no generation after denied route')) as transport:
            result = chat.process_input(self.root, value, token, select_model=routes.selection)
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error_code'], 'local_model_not_ready')
        saved = get_request(self.root, value['request_id'])
        self.assertEqual(saved['generation']['error_code'], 'local_model_not_ready')
        self.assertFalse(saved.get('reply'))
        self.assertEqual(self.path.read_bytes(), before)
        transport.assert_not_called()

    def test_unready_copy_on_other_port_does_not_make_ready_endpoint_ambiguous(self):
        self.observe(BASE34, ready(IQ47))
        self.observe(BASE35, ready(IQ47, loaded=False, state="unloaded"))
        self.configure()
        self.assertEqual(self.row().get("local_base_url"), BASE34)
        self.assertEqual(routes.selection(self.root)["base_url"], BASE34)

    def test_duplicate_id_on_selected_endpoint_is_unavailable_even_if_one_row_is_unloaded(self):
        before = self.configure()
        routes.record_result(self.root, IQ47, success=True)
        for duplicate in (ready(IQ47, id=IQ47), ready(IQ47, loaded=False, state="unloaded")):
            with self.subTest(duplicate=duplicate):
                self.observe(BASE34, ready(IQ47), duplicate)
                row = self.row()
                self.assertEqual(row["status"], "unavailable")
                self.assertIsNone(row["local_base_url"])
                self.assertFalse(row["selectable"])
                self.assertFalse(row["automatic_candidate"])
                self.assert_blocked_selection(before)

    def test_unloaded_malformed_and_different_models_have_no_observed_endpoint(self):
        before = self.configure()
        invalid = (None, [], {"data": None}, {"data": {}}, {"data": "bad"},
                   {"data": [None, 4, {}]},
                   {"data": [ready(IQ47, id=None)]},
                   {"data": [ready(IQ47, loaded=False)]},
                   {"data": [ready(IQ47, loaded=1)]},
                   {"data": [ready(IQ47, state="loading")]},
                   {"data": [ready(IQ33)]}, OSError("synthetic unavailable"))
        routes.record_result(self.root, IQ47, success=True)
        for payload in invalid:
            with self.subTest(payload=payload):
                self.observations[BASE34] = payload
                row = self.row()
                self.assertIsNone(row.get("local_base_url"))
                self.assertFalse(row["selectable"])
                self.assertFalse(row["automatic_candidate"])
                self.assert_blocked_selection(before)

    def test_payload_endpoint_fields_never_override_owned_discovery_port(self):
        for injected in (BASE35, "https://outside.example/v1", "file:///private/fixture"):
            with self.subTest(injected=injected):
                for row in self.router_rows:
                    row.update(local_base_url=injected, base_url=injected)
                self.observe(BASE34, ready(IQ47, local_base_url=injected, base_url=injected))
                self.configure()
                self.assertEqual(self.row().get("local_base_url"), BASE34)
                self.assertEqual(routes.selection(self.root)["base_url"], BASE34)
                self.assertIsNone(self.row(IQ33).get("local_base_url"))
                self.assertIsNone(self.row(routes.DEFAULT_MODEL).get("local_base_url"))
                self.configure(routes.DEFAULT_MODEL)
                remote = routes.selection(self.root)
                self.assertEqual(remote["base_url"], routes.ROUTER)
                self.assertEqual(remote["transport"], "opencodex")

    def test_memory_gate_is_preserved_for_an_observed_endpoint(self):
        self.observe(BASE35, ready(OTHER))
        self.memory = "memory"
        before = self.configure(OTHER)
        self.assertFalse(self.row(OTHER)["selectable"])
        self.assert_blocked_selection(before)

    def test_selection_and_save_reject_missing_or_unsafe_catalog_endpoint_without_guessing(self):
        self.observe(BASE34, ready(IQ47))
        before = self.configure()
        payload = routes.catalog(self.root)
        for endpoint in (None, "", 1, [], {}, *UNSAFE_URLS):
            with self.subTest(endpoint=endpoint):
                injected = json.loads(json.dumps(payload))
                routes.find_model(injected, IQ47)["local_base_url"] = endpoint
                with patch.object(routes, "catalog", return_value=injected):
                    self.assert_blocked_selection(before)
                    with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
                        routes.save(self.root, {"model": IQ47, "reasoning_effort": "high"}, fetcher=self.fetch)
                    self.assertEqual(self.path.read_bytes(), before)
        routes.find_model(payload, IQ47).pop("local_base_url", None)
        with patch.object(routes, "catalog", return_value=payload):
            self.assert_blocked_selection(before)
            with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
                routes.save(self.root, {"model": IQ47, "reasoning_effort": "high"}, fetcher=self.fetch)
            self.assertEqual(self.path.read_bytes(), before)

    def test_catalog_selected_validator_allows_only_literal_owned_endpoints(self):
        for model in (IQ47, IQ33, OTHER):
            for base in (BASE34, BASE35):
                with self.subTest(model=model, base=base):
                    self.assertEqual(voice._validate_local_llm_base_url(
                        base, model, catalog_selected=True), base)
                    adapter = voice.LocalMlxLlm(base_url=base, model=model,
                                                catalog_selected=True, state_root=self.root)
                    self.assertEqual(adapter.model, model)
                    self.assertEqual(adapter.base_url, base)
        for base in UNSAFE_URLS:
            with self.subTest(base=base):
                with self.assertRaisesRegex(ValueError, "^local_llm_endpoint_invalid$"):
                    voice._validate_local_llm_base_url(base, IQ47, catalog_selected=True)
                with self.assertRaisesRegex(ValueError, "^local_llm_endpoint_invalid$"):
                    voice.LocalMlxLlm(base_url=base, model=IQ47,
                                      catalog_selected=True, state_root=self.root)

    def test_catalog_selected_constructor_requires_explicit_valid_endpoint_before_http(self):
        with patch.object(voice, "_local_urlopen") as transport:
            with self.assertRaisesRegex(ValueError, "^local_llm_endpoint_invalid$"):
                voice.LocalMlxLlm(model=IQ47, catalog_selected=True, state_root=self.root)
            for endpoint in (None, "", 1, False, [], {}):
                with self.subTest(endpoint=endpoint):
                    with self.assertRaisesRegex(ValueError, "^local_llm_endpoint_invalid$"):
                        voice.LocalMlxLlm(base_url=endpoint, model=IQ47,
                                          catalog_selected=True, state_root=self.root)
            transport.assert_not_called()

    def test_legacy_default_port_validation_and_fixed_aliases_remain_strict(self):
        for model in (IQ33, voice.QWEN38_27B_MODEL_ID, voice.FLASH_NEXT_MODEL_ID, IQ47, OTHER):
            expected = BASE35 if model == IQ33 else BASE34
            wrong = BASE34 if model == IQ33 else BASE35
            with self.subTest(model=model):
                self.assertEqual(voice._validate_local_llm_base_url(expected, model), expected)
                self.assertEqual(voice.LocalMlxLlm(model=model, state_root=self.root).base_url, expected)
                with self.assertRaisesRegex(ValueError, "^local_llm_endpoint_invalid$"):
                    voice._validate_local_llm_base_url(wrong, model)
                with self.assertRaisesRegex(ValueError, "^local_llm_endpoint_invalid$"):
                    voice.LocalMlxLlm(base_url=wrong, model=model, state_root=self.root)
        alias = voice.LocalMlxLlm(model=IQ33.removeprefix("mlx/"), state_root=self.root)
        self.assertEqual((alias.model, alias.base_url), (IQ33, BASE35))

    def test_legacy_nonfixed_model_still_requires_catalog_selection_before_http(self):
        adapter = voice.LocalMlxLlm(model=IQ47, state_root=self.root)
        with patch.object(voice, "_local_urlopen") as transport:
            with self.assertRaisesRegex(RuntimeError, "^model_swap_required$"):
                adapter.generate("fixture prompt", self.token)
            transport.assert_not_called()

    def test_readiness_rejects_unloaded_malformed_duplicate_and_mismatched_ids_before_post(self):
        invalid = (b"not-json", [], {}, {"data": None}, {"data": [None]},
                   {"data": [{}]}, {"data": [ready(IQ33)]},
                   {"data": [ready(IQ47, loaded=False)]},
                   {"data": [ready(IQ47, loaded=1)]},
                   {"data": [ready(IQ47, state="loading")]},
                   {"data": [ready(IQ47), ready(IQ47, id=IQ47)]},
                   OSError("synthetic readiness unavailable"))
        for payload in invalid:
            with self.subTest(payload=payload):
                adapter = voice.LocalMlxLlm(base_url=BASE34, model=IQ47,
                                            catalog_selected=True, state_root=self.root)
                calls = []
                with patch.object(voice, "_local_urlopen", side_effect=self.transport(payload, calls)):
                    with self.assertRaisesRegex(RuntimeError, "^local_llm_model_not_ready$"):
                        adapter.generate("fixture prompt", self.token)
                self.assert_requests(calls, BASE34, post=False)

    def test_latest_readiness_rejects_changed_chat_capability_and_padded_id_before_post(self):
        rows = [ready(IQ47, capabilities=caps) for caps in
                (["audio"], "chat", [], None, ["chat", 1], ["chat", {}])]
        rows += [ready(IQ47, id=padding + IQ47.removeprefix("mlx/") + padding)
                 for padding in (" ", "\t", "\n")]
        for row in rows:
            with self.subTest(row=row):
                adapter = voice.LocalMlxLlm(base_url=BASE34, model=IQ47,
                                            catalog_selected=True, state_root=self.root)
                calls = []
                with patch.object(voice, "_local_urlopen", side_effect=self.transport({"data": [row]}, calls)):
                    with self.assertRaisesRegex(RuntimeError, "^local_llm_model_not_ready$"):
                        adapter.generate("fixture prompt", self.token)
                self.assert_requests(calls, BASE34, post=False)

    def test_readiness_port_change_does_not_fallback_to_other_ready_endpoint(self):
        for selected, moved in ((BASE34, BASE35), (BASE35, BASE34)):
            with self.subTest(selected=selected):
                self.observations = {BASE34: {"data": []}, BASE35: {"data": []}}
                self.observe(selected, ready(IQ47))
                before = self.configure()
                calls = []

                def changed_endpoint(request, **_kwargs):
                    calls.append(request)
                    if request.full_url == moved + "/models":
                        return Response({"data": [ready(IQ47)]})
                    return Response({"data": []})

                adapter = voice.ConfiguredLocalMlxLlm(self.root)
                with patch.object(voice, "_local_urlopen", side_effect=changed_endpoint):
                    with self.assertRaisesRegex(RuntimeError, "^local_llm_model_not_ready$"):
                        adapter.generate("fixture prompt", self.token)
                self.assert_requests(calls, selected, post=False)
                self.assertEqual(self.path.read_bytes(), before)

    def test_response_model_mismatch_is_not_relabelled_or_retried(self):
        self.observe(BASE34, ready(IQ47))
        before = self.configure()
        calls = []
        with patch.object(voice, "_local_urlopen", side_effect=self.transport(
                {"data": [ready(IQ47)]}, calls, response_model=IQ33)):
            with self.assertRaisesRegex(RuntimeError, "^local_llm_model_mismatch$"):
                voice.ConfiguredLocalMlxLlm(self.root).generate("fixture prompt", self.token)
        self.assert_requests(calls, BASE34, post=True)
        self.assertEqual(json.loads(calls[1].data)["model"], IQ47.removeprefix("mlx/"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_post_transport_failure_does_not_retry_another_model_or_port(self):
        self.observe(BASE35, ready(OTHER))
        before = self.configure(OTHER)
        calls = []
        with patch.object(voice, "_local_urlopen", side_effect=self.transport(
                {"data": [ready(OTHER)]}, calls, post_error=OSError("synthetic POST failure"))):
            with self.assertRaisesRegex(RuntimeError, "^local_llm_request_failed$"):
                voice.ConfiguredLocalMlxLlm(self.root).generate("fixture prompt", self.token)
        self.assert_requests(calls, BASE35, post=True)
        self.assertEqual(self.path.read_bytes(), before)


class NativeInventoryV2Tests(unittest.TestCase):
    """Native metadata is an independent inventory, never a guessed alias."""

    FLAT = "mlx/ddalcu-Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-4.7bpw"
    TTS = "mlx/mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
    CAPS = ["chat", "tool_use", "streaming", "json_schema", "vision"]
    observe = ObservedLocalRoutesTests.observe
    row = ObservedLocalRoutesTests.row
    transport = ObservedLocalRoutesTests.transport
    assert_requests = ObservedLocalRoutesTests.assert_requests

    def setUp(self):
        self.router_failure = None
        self.router_payload = None
        ObservedLocalRoutesTests.setUp(self)
        self.router_rows = []

    def fetch(self, url):
        if url == routes.ROUTER + "/models":
            if self.router_failure is not None:
                self.discovery_calls.append(url)
                raise self.router_failure
            if self.router_payload is not None:
                self.discovery_calls.append(url)
                return self.router_payload
        return ObservedLocalRoutesTests.fetch(self, url)

    def native(self, model=IQ47, **overrides):
        return ready(model, capabilities=list(self.CAPS), **overrides)

    def configure(self, model=IQ47, *, effort=None, mode="manual"):
        self.path.write_text(json.dumps({"schema_version": 1, "model": model,
                                        "reasoning_effort": effort, "transport": "local",
                                        "mode": mode}))
        return self.path.read_bytes()

    @staticmethod
    def router_row(model, **extra):
        return {"slug": model, "supported_reasoning_levels": ["low", "medium", "high"],
                "default_reasoning_level": "medium", **extra}

    def failures(self):
        import urllib.error
        url = routes.ROUTER + "/models"
        return ((urllib.error.HTTPError(url, 401, "fixture secret detail", None, None),
                 "routed_llm_authentication"),
                (urllib.error.HTTPError(url, 403, "fixture secret detail", None, None),
                 "routed_llm_authentication"),
                (urllib.error.HTTPError(url, 429, "fixture secret detail", None, None),
                 "routed_llm_quota"),
                (urllib.error.HTTPError(url, 503, "fixture secret detail", None, None),
                 "routed_llm_unavailable"),
                (urllib.error.URLError("fixture secret detail"), "routed_llm_unavailable"),
                (OSError("fixture secret detail"), "routed_llm_unavailable"))

    def typed(self):
        import uuid
        import alden_chat as chat
        from alden_chat_turns import get_request
        value = {"request_id": uuid.uuid4().hex, "conversation_id": None,
                 "text": "fixture prompt", "images": []}
        token = chat.InputToken(self.root, str(uuid.uuid4()))
        result = chat.process_input(self.root, value, token, select_model=routes.selection)
        return result, get_request(self.root, value["request_id"])

    def native_http(self, calls, model=IQ47):
        transport = self.transport({"data": [self.native(model)]}, calls, response_model=model)

        def open_response(request, **kwargs):
            if request.full_url.endswith("/metrics"):
                calls.append(request)
                return Response(b"")
            return transport(request, **kwargs)
        return open_response

    def assert_native_requests(self, calls, base, model=IQ47):
        inference = [r for r in calls if not r.full_url.endswith("/metrics")]
        self.assert_requests(inference, base, post=True)
        body = json.loads(inference[1].data)
        self.assertEqual(body["model"], model.removeprefix("mlx/"))
        self.assertNotIn("reasoning_effort", body)
        self.assertTrue(all(r.full_url.startswith(base + "/") or
                            r.full_url == base.removesuffix("/v1") + "/metrics" for r in calls))

    def test_real_unloaded_native_metadata_keeps_flattened_router_alias_distinct(self):
        self.router_rows = [self.router_row(self.FLAT)]
        self.observe(BASE34, self.native(loaded=False, state="unloaded"),
                     ready(self.TTS, loaded=False, state="unloaded", capabilities=["audio"]))
        before = self.configure()
        payload = routes.catalog(self.root)
        native = routes.find_model(payload, IQ47)
        flat = routes.find_model(payload, self.FLAT)
        self.assertNotEqual(native["id"], flat["id"])
        self.assertEqual(native["id"], IQ47)
        self.assertEqual(native["efforts"], [])
        self.assertIsNone(native["default_effort"])
        self.assertEqual(native["status"], "not_loaded")
        for row in (native, flat):
            self.assertFalse(row["selectable"])
            self.assertFalse(row["automatic_candidate"])
            self.assertIsNone(row["local_base_url"])
        self.assertNotIn(self.TTS, [r["id"] for r in payload["models"]])
        with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
            routes.selection(self.root)
        self.assertEqual(self.path.read_bytes(), before)

    def test_direct_native_discovery_accepts_only_one_optional_prefix_and_no_router_row(self):
        for raw_id in (IQ47.removeprefix("mlx/"), IQ47):
            with self.subTest(raw_id=raw_id):
                self.observe(BASE35, self.native(id=raw_id))
                payload = routes.catalog(self.root)
                self.assertEqual([r["id"] for r in payload["models"]], [IQ47])
                row = routes.find_model(payload, IQ47)
                self.assertEqual(row["local_base_url"], BASE35)
                self.assertTrue(row["selectable"])
                self.assertEqual(row["efforts"], [])
                self.assertIsNone(row["default_effort"])

    def test_native_ready_id_saves_selects_and_runs_configured_and_typed_callers_exactly(self):
        self.router_rows = [self.router_row(self.FLAT, provider_url=BASE35,
                                           upstream_model_id=IQ33, local_base_url=BASE35)]
        for base in (BASE34, BASE35):
            with self.subTest(base=base):
                self.observations = {BASE34: {"data": []}, BASE35: {"data": []}}
                self.observe(base, self.native())
                routes.save(self.root, {"model": IQ47}, fetcher=self.fetch)
                before = self.path.read_bytes()
                stored = json.loads(before)
                self.assertEqual(stored["model"], IQ47)
                self.assertIsNone(stored["reasoning_effort"])
                selected = routes.selection(self.root)
                self.assertEqual((selected["model"], selected["base_url"], selected["transport"]),
                                 (IQ47, base, "local"))
                self.assertIsNone(selected["reasoning_effort"])
                self.assertFalse(self.row(self.FLAT)["selectable"])
                calls = []
                with patch.object(voice, "_local_urlopen", side_effect=self.native_http(calls)):
                    self.assertEqual(voice.ConfiguredLocalMlxLlm(self.root).generate(
                        "fixture prompt", self.token), "synthetic answer")
                self.assert_native_requests(calls, base)
                calls = []
                with patch.object(voice, "_local_urlopen", side_effect=self.native_http(calls)):
                    result, saved = self.typed()
                self.assertTrue(result["ok"], result)
                self.assertEqual(saved["reply"], "synthetic answer")
                self.assertEqual(saved["generation"]["selection"]["model_id"], IQ47)
                self.assertIsNone(saved["generation"]["selection"]["reasoning_effort"])
                self.assert_native_requests(calls, base)
                self.assertEqual(self.path.read_bytes(), before)

    def test_native_discovery_requires_valid_list_with_literal_chat_capability(self):
        invalid = (None, "chat", {"chat": True}, [], ["audio"], ["Chat"], [" chat"],
                   ["chat", 1], ["chat", None], ["chat", {"tool": "use"}])
        before = self.configure(OTHER)
        for caps in invalid:
            with self.subTest(caps=caps):
                row = self.native()
                row["capabilities"] = caps
                self.observe(BASE34, row)
                payload = routes.catalog(self.root)
                self.assertNotIn(IQ47, [r["id"] for r in payload["models"]])
        row = self.native()
        row.pop("capabilities")
        self.observe(BASE34, row)
        self.assertNotIn(IQ47, [r["id"] for r in routes.catalog(self.root)["models"]])
        self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_native_nonchat_or_malformed_capabilities_veto_exact_router_row(self):
        self.router_rows = [self.router_row(IQ47, capabilities=["chat"])]
        before = self.configure(effort="medium")
        for caps in (None, "chat", [], ["audio"], ["Chat"], ["chat", 1], ["chat", {}]):
            with self.subTest(caps=caps):
                row = self.native()
                row["capabilities"] = caps
                self.observe(BASE34, row)
                found = self.row()
                self.assertFalse(found["selectable"])
                self.assertFalse(found["automatic_candidate"])
                with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
                    routes.selection(self.root)
                self.assertEqual(self.path.read_bytes(), before)

    def test_native_and_flattened_metadata_cannot_invent_native_reasoning_levels(self):
        self.router_rows = [self.router_row(self.FLAT)]
        self.observe(BASE34, self.native(reasoning_efforts=["high"],
                                        supported_reasoning_levels=["medium"],
                                        default_reasoning_level="medium"))
        self.assertEqual(self.row()["efforts"], [])
        self.assertIsNone(self.row()["default_effort"])
        routes.save(self.root, {"model": IQ47}, fetcher=self.fetch)
        before = self.path.read_bytes()
        self.assertIsNone(routes.selection(self.root)["reasoning_effort"])
        with self.assertRaisesRegex(ValueError, "^model_effort_not_supported$"):
            routes.save(self.root, {"model": IQ47, "reasoning_effort": "high"}, fetcher=self.fetch)
        self.assertEqual(self.path.read_bytes(), before)

    def test_only_exact_router_id_supplies_declared_reasoning_levels_and_deduplicates(self):
        self.router_rows = [self.router_row(self.FLAT), self.router_row(IQ47)]
        self.observe(BASE34, self.native())
        payload = routes.catalog(self.root)
        self.assertEqual([r["id"] for r in payload["models"]].count(IQ47), 1)
        self.assertEqual(routes.find_model(payload, IQ47)["efforts"], ["low", "medium", "high"])
        self.assertEqual(routes.find_model(payload, IQ47)["default_effort"], "medium")
        routes.save(self.root, {"model": IQ47, "reasoning_effort": "high"}, fetcher=self.fetch)
        selected = routes.selection(self.root)
        self.assertEqual(selected["model"], IQ47)
        self.assertEqual(selected["reasoning_effort"], "high")
        self.assertEqual(selected["base_url"], BASE34)

    def test_native_duplicates_within_or_across_ports_keep_single_disabled_exact_id(self):
        before = self.configure()
        routes.record_result(self.root, IQ47, success=True)
        arrangements = (([self.native(), self.native(id=IQ47)], []),
                        ([self.native()], [self.native(id=IQ47)]))
        for first, second in arrangements:
            with self.subTest(first=first, second=second):
                self.observe(BASE34, *first)
                self.observe(BASE35, *second)
                payload = routes.catalog(self.root)
                self.assertEqual([r["id"] for r in payload["models"]], [IQ47])
                row = routes.find_model(payload, IQ47)
                self.assertEqual(row["status"], "unavailable")
                self.assertFalse(row["selectable"])
                self.assertFalse(row["automatic_candidate"])
                self.assertIsNone(row["local_base_url"])
                with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
                    routes.selection(self.root)
                self.assertEqual(self.path.read_bytes(), before)

    def test_selected_port_duplicate_capability_conflict_cannot_be_hidden_by_router(self):
        self.router_rows = [self.router_row(IQ47)]
        before = self.configure(effort="medium")
        for caps in (["audio"], "chat", ["chat", None]):
            with self.subTest(caps=caps):
                conflict = self.native()
                conflict["capabilities"] = caps
                self.observe(BASE35, self.native(), conflict)
                row = self.row()
                self.assertFalse(row["selectable"])
                self.assertFalse(row["automatic_candidate"])
                self.assertIsNone(row["local_base_url"])
                with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
                    routes.selection(self.root)
                self.assertEqual(self.path.read_bytes(), before)

    def test_partial_router_errors_keep_native_inventory_and_omit_stale_remote_health(self):
        self.observe(BASE34, self.native())
        before = self.configure()
        routes.record_result(self.root, routes.DEFAULT_MODEL, success=True)
        routes.record_result(self.root, self.FLAT, success=True)
        health_before = (self.root / "model-route-health.json").read_bytes()
        for error, code in self.failures():
            with self.subTest(code=code, error=type(error).__name__):
                self.router_failure = error
                payload = routes.catalog(self.root)
                self.assertIs(payload["partial"], True)
                self.assertEqual(payload["catalog_error"], code)
                self.assertEqual([r["id"] for r in payload["models"]], [IQ47])
                self.assertEqual(payload["local_running"], 1)
                self.assertTrue(routes.find_model(payload, IQ47)["selectable"])
                self.assertIsNone(payload["automatic_model"])
                self.assertNotIn("fixture secret detail", json.dumps(payload))
                self.assertEqual(routes.selection(self.root)["model"], IQ47)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual((self.root / "model-route-health.json").read_bytes(), health_before)

    def test_malformed_router_catalog_is_partial_only_with_independent_native_chat(self):
        self.observe(BASE34, self.native())
        before = self.configure()
        for invalid in ({"models": None}, {"models": {}}, {"models": "bad"},
                        {"models": [self.router_row("remote/model-" + str(n)) for n in range(257)]}):
            with self.subTest(invalid_type=type(invalid["models"]).__name__):
                self.router_payload = invalid
                payload = routes.catalog(self.root)
                self.assertIs(payload["partial"], True)
                self.assertEqual(payload["catalog_error"], "model_catalog_invalid")
                self.assertEqual([r["id"] for r in payload["models"]], [IQ47])
                with self.assertRaisesRegex(RuntimeError, "^model_catalog_invalid$"):
                    routes.find_model(payload, routes.DEFAULT_MODEL)
                self.assertEqual(routes.selection(self.root)["model"], IQ47)
                self.assertEqual(self.path.read_bytes(), before)

    def test_partial_inventory_retains_declared_but_unloaded_native_as_disabled(self):
        self.observe(BASE34, self.native(loaded=False, state="unloaded"))
        self.router_failure, code = self.failures()[0]
        before = self.configure()
        payload = routes.catalog(self.root)
        self.assertIs(payload["partial"], True)
        self.assertEqual(payload["catalog_error"], code)
        row = routes.find_model(payload, IQ47)
        self.assertEqual(row["status"], "not_loaded")
        self.assertFalse(row["selectable"])
        self.assertFalse(row["automatic_candidate"])
        with self.assertRaisesRegex(RuntimeError, "^local_model_not_ready$"):
            routes.selection(self.root)
        self.assertEqual(self.path.read_bytes(), before)

    def test_router_failure_without_independently_declared_native_chat_retains_old_error(self):
        before = self.configure(OTHER)
        missing = ready(IQ47)
        for observation in ([], [missing], [ready(IQ47, capabilities=["audio"])],
                            [ready(IQ47, capabilities="chat")],
                            [ready(IQ47, capabilities=["chat", None])]):
            self.observe(BASE34, *observation)
            for error, code in self.failures():
                with self.subTest(observation=observation, code=code):
                    self.router_failure = error
                    with self.assertRaisesRegex(RuntimeError, "^" + code + "$"):
                        routes.catalog(self.root)
                    self.assertEqual(self.path.read_bytes(), before)

    def test_partial_find_save_and_manual_remote_selection_rethrow_original_safe_error(self):
        self.observe(BASE35, self.native())
        for error, code in self.failures():
            with self.subTest(code=code):
                self.router_failure = error
                before = self.configure(routes.DEFAULT_MODEL, effort="high")
                payload = routes.catalog(self.root)
                for requested in (routes.DEFAULT_MODEL, self.FLAT, "remote/not-in-current-inventory"):
                    with self.assertRaisesRegex(RuntimeError, "^" + code + "$"):
                        routes.find_model(payload, requested)
                    with self.assertRaisesRegex(RuntimeError, "^" + code + "$"):
                        routes.save(self.root, {"model": requested}, fetcher=self.fetch)
                with self.assertRaisesRegex(RuntimeError, "^" + code + "$"):
                    routes.selection(self.root)
                self.assertEqual(self.path.read_bytes(), before)

    def test_partial_native_manual_save_and_configured_caller_work_with_none_effort(self):
        self.observe(BASE35, self.native())
        self.router_failure, _code = self.failures()[2]
        routes.save(self.root, {"model": IQ47}, fetcher=self.fetch)
        before = self.path.read_bytes()
        calls = []
        with patch.object(voice, "_local_urlopen", side_effect=self.native_http(calls)):
            self.assertEqual(voice.ConfiguredLocalMlxLlm(self.root).generate(
                "fixture prompt", self.token), "synthetic answer")
        self.assert_native_requests(calls, BASE35)
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_typed_native_succeeds_and_missing_remote_preserves_safe_failure_receipt(self):
        self.observe(BASE34, self.native())
        self.router_failure, _code = self.failures()[0]
        routes.save(self.root, {"model": IQ47}, fetcher=self.fetch)
        before = self.path.read_bytes()
        calls = []
        with patch.object(voice, "_local_urlopen", side_effect=self.native_http(calls)):
            result, saved = self.typed()
        self.assertTrue(result["ok"], result)
        self.assertEqual(saved["generation"]["selection"]["model_id"], IQ47)
        self.assert_native_requests(calls, BASE34)
        self.assertEqual(self.path.read_bytes(), before)
        for error, code in self.failures():
            with self.subTest(code=code):
                self.router_failure = error
                before = self.configure(routes.DEFAULT_MODEL, effort="high")
                with patch.object(voice, "_local_urlopen") as http:
                    result, saved = self.typed()
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_code"], code)
                self.assertEqual(saved["generation"]["error_code"], code)
                self.assertFalse(saved.get("reply"))
                http.assert_not_called()
                self.assertEqual(self.path.read_bytes(), before)

    def test_partial_automatic_mode_cannot_fallback_even_to_a_verified_local_candidate(self):
        model = voice.QWEN38_27B_MODEL_ID
        self.observe(BASE34, self.native(model))
        routes.record_result(self.root, model, success=True)
        self.router_failure, code = self.failures()[0]
        before = self.configure(model, mode="automatic")
        payload = routes.catalog(self.root)
        self.assertIsNone(payload["automatic_model"])
        with patch.object(voice, "_local_urlopen") as http:
            with self.assertRaises(RuntimeError) as failure:
                voice.ConfiguredLocalMlxLlm(self.root).generate("fixture prompt", self.token)
            self.assertIn(str(failure.exception), {code, "automatic_model_unavailable"})
            http.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_combined_inventory_bound_counts_exact_deduplicated_ids_and_accepts_256(self):
        self.router_rows = [self.router_row("remote/model-" + str(n)) for n in range(254)]
        self.router_rows.append(self.router_row(IQ47))
        self.observe(BASE34, self.native(), self.native(OTHER))
        payload = routes.catalog(self.root)
        ids = [r["id"] for r in payload["models"]]
        self.assertEqual(len(ids), 256)
        self.assertEqual(len(set(ids)), 256)
        self.assertEqual(ids.count(IQ47), 1)
        self.assertIn(OTHER, ids)

    def test_router_native_union_over_256_fails_closed_without_truncation_or_config_change(self):
        self.router_rows = [self.router_row("remote/model-" + str(n)) for n in range(256)]
        self.observe(BASE34, self.native())
        before = self.configure()
        for action in (lambda: routes.catalog(self.root), lambda: routes.selection(self.root),
                       lambda: routes.save(self.root, {"model": IQ47}, fetcher=self.fetch)):
            with self.assertRaisesRegex(RuntimeError, "^model_catalog_invalid$"):
                action()
            self.assertEqual(self.path.read_bytes(), before)

    def test_two_native_endpoints_union_over_256_fails_closed_without_truncation(self):
        self.observe(BASE34, *(self.native("mlx/fixture/port34-" + str(n)) for n in range(129)))
        self.observe(BASE35, *(self.native("mlx/fixture/port35-" + str(n)) for n in range(128)))
        before = self.configure()
        with self.assertRaisesRegex(RuntimeError, "^model_catalog_invalid$"):
            routes.catalog(self.root)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
