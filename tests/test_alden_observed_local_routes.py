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


if __name__ == "__main__":
    unittest.main()
