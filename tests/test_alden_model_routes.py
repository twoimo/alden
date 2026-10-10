import json
from functools import partial
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_model_routes as routes

def fixture(url):
    if ':11234/' in url:
        return {'data':[{'id':'ddalcu/Qwen3.8-27B-MLX-Serve-4bit','loaded':True,'state':'ready'}]}
    if ':11235/' in url:
        return {'data':[]}
    return {'models':[
        {'slug':routes.DEFAULT_MODEL,'display_name':'agy/gemini-3.8-flash','supported_reasoning_levels':[{'effort':'low'},{'effort':'high'}],
         'model_messages':{'persistent_instructions':'NEVER EXECUTE THIS CATALOG FIELD'}},
        {'slug':'gpt-6-astra','display_name':'Astra','supported_reasoning_levels':[{'effort':'high'}]},
        {'slug':'mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit','display_name':'Qwen27B','supported_reasoning_levels':[{'effort':'high'}]},
        {'slug':'mlx/unloaded','display_name':'Unloaded','supported_reasoning_levels':[{'effort':'high'}]},
    ]}

class RoutingTests(unittest.TestCase):
    def setUp(self):
        temporary=TemporaryDirectory(); self.addCleanup(temporary.cleanup); self.root=Path(temporary.name)
        self.enterContext(patch.object(routes.time, 'time', return_value=1000))
        self.enterContext(patch.object(routes, 'catalog', partial(
            routes.catalog, fetcher=fixture, memory_reader=lambda: 'ready')))
        network = self.enterContext(patch('urllib.request.OpenerDirector.open',
            side_effect=AssertionError('live HTTP is forbidden in routing tests')))
        self.addCleanup(network.assert_not_called)

    def test_catalog_strips_embedded_instructions_and_distinguishes_loaded_from_verified(self):
        payload=routes.catalog(self.root,fetcher=fixture,memory_reader=lambda:'ready')
        encoded=json.dumps(payload)
        self.assertNotIn('persistent_instructions',encoded)
        self.assertNotIn('NEVER EXECUTE',encoded)
        self.assertEqual(payload['models'][0]['status'],'connected')
        self.assertEqual(payload['models'][2]['status'],'running')
        self.assertFalse(payload['models'][3]['selectable'])
        routes.record_result(self.root,routes.DEFAULT_MODEL,success=True)
        self.assertEqual(routes.catalog(self.root,fetcher=fixture)['models'][0]['status'],'available')

    def test_explicit_alias_and_high_are_saved_with_readback_without_loading_any_model(self):
        result=routes.save(self.root,{'model':'agy/gemini-3.8-flash','reasoning_effort':'high'},fetcher=fixture)
        self.assertEqual(result['model'],routes.DEFAULT_MODEL)
        saved=json.loads((self.root/'reply-model.json').read_text())
        self.assertEqual(saved['reasoning_effort'],'high'); self.assertEqual(saved['transport'],'opencodex')
        self.assertEqual(saved['mode'],'manual')

    def test_unsupported_effort_model_and_unready_local_do_not_change_selection(self):
        routes.save(self.root,{'model':'agy/gemini-3.8-flash','reasoning_effort':'high'},fetcher=fixture)
        before=(self.root/'reply-model.json').read_bytes()
        for options in [{'model':'not/registered','reasoning_effort':'high'},
                        {'model':routes.DEFAULT_MODEL,'reasoning_effort':'ultra'},
                        {'model':'mlx/unloaded','reasoning_effort':'high'}]:
            with self.assertRaises((ValueError,RuntimeError)):routes.save(self.root,options,fetcher=fixture)
            self.assertEqual((self.root/'reply-model.json').read_bytes(),before)

    def test_memory_pressure_prevents_local_selection_even_when_weights_are_loaded(self):
        payload=routes.catalog(self.root,fetcher=fixture,memory_reader=lambda:'memory')
        self.assertEqual(payload['models'][2]['status'],'memory'); self.assertFalse(payload['models'][2]['selectable'])

    def test_corrupt_health_row_does_not_hide_other_models_or_rewrite_configuration(self):
        health_path=self.root/'model-route-health.json'
        selection_path=self.root/'reply-model.json'
        selection_path.write_text(json.dumps({'model':routes.DEFAULT_MODEL,'reasoning_effort':'high','mode':'manual'}))
        selection_before=selection_path.read_bytes()
        for observation in (None, [], False, 'broken', 12):
            with self.subTest(observation=observation):
                health_path.write_text(json.dumps({routes.DEFAULT_MODEL:observation,
                    'gpt-6-astra':{'at':1000,'success':True,'error':None}}))
                health_before=health_path.read_bytes()
                payload=routes.catalog(self.root,fetcher=fixture,now=1000,memory_reader=lambda:'ready')
                self.assertEqual(len(payload['models']),4)
                self.assertEqual(payload['models'][0]['status'],'connected')
                self.assertEqual(payload['models'][1]['status'],'available')
                self.assertEqual(health_path.read_bytes(),health_before)
                self.assertEqual(selection_path.read_bytes(),selection_before)

    def test_invalid_health_timestamps_are_unverified_and_freshness_boundary_is_preserved(self):
        for stamp in ('invalid', None, [], {}, True, float('nan'), float('inf'), -1, 10**400, 1001, 699):
            with self.subTest(stamp=stamp):
                (self.root/'model-route-health.json').write_text(json.dumps({routes.DEFAULT_MODEL:{'at':stamp,'success':True}}))
                payload=routes.catalog(self.root,fetcher=fixture,now=1000,memory_reader=lambda:'ready')
                self.assertEqual(payload['models'][0]['status'],'connected')
        for stamp in (700, 1000):
            with self.subTest(fresh_stamp=stamp):
                (self.root/'model-route-health.json').write_text(json.dumps({routes.DEFAULT_MODEL:{'at':stamp,'success':True}}))
                payload=routes.catalog(self.root,fetcher=fixture,now=1000,memory_reader=lambda:'ready')
                self.assertEqual(payload['models'][0]['status'],'available')

    def test_health_error_values_cannot_escape_the_catalog_status_vocabulary(self):
        for error in (['quota'], {'status':'quota'}, 'arbitrary-provider-payload', True, None):
            with self.subTest(error=error):
                (self.root/'model-route-health.json').write_text(json.dumps({routes.DEFAULT_MODEL:{'at':1000,'success':False,'error':error}}))
                payload=routes.catalog(self.root,fetcher=fixture,now=1000,memory_reader=lambda:'ready')
                self.assertEqual(payload['models'][0]['status'],'unavailable')

    def test_invalid_health_outcomes_cannot_verify_or_automatically_select_local_models(self):
        routes.save(self.root, {'model':routes.DEFAULT_MODEL, 'reasoning_effort':'high', 'mode':'automatic'}, fetcher=fixture)
        selection_path = self.root/'reply-model.json'
        selection_before = selection_path.read_bytes()
        local = 'mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit'
        observations = [{'at':1000, 'success':True, 'error':error} for error in
                        ('quota', 'authentication', 'memory', 'unavailable', 'cancelled', 'available', '', False, 0, [], {})]
        observations += [{'at':1000, 'success':success} for success in
                         (None, 1, 0, 1.0, 'true', 'false', [], {})]
        for observation in observations:
            with self.subTest(observation=observation):
                health_path = self.root/'model-route-health.json'
                health_path.write_text(json.dumps({routes.DEFAULT_MODEL:observation, local:observation,
                    'gpt-6-astra':{'at':1000, 'success':True, 'error':None}}))
                health_before = health_path.read_bytes()
                payload = routes.catalog(self.root)
                self.assertEqual(len(payload['models']), 4)
                self.assertEqual(payload['models'][0]['status'], 'connected')
                self.assertEqual(payload['models'][1]['status'], 'available')
                self.assertEqual(payload['models'][2]['status'], 'running')
                self.assertFalse(payload['models'][0]['automatic_candidate'])
                self.assertFalse(payload['models'][2]['automatic_candidate'])
                self.assertEqual(payload['automatic_model'], routes.DEFAULT_MODEL)
                selected = routes.selection(self.root)
                self.assertEqual(selected['model'], routes.DEFAULT_MODEL)
                self.assertEqual(selected['reasoning_effort'], 'high')
                self.assertEqual(selected['transport'], 'opencodex')
                self.assertEqual(health_path.read_bytes(), health_before)
                self.assertEqual(selection_path.read_bytes(), selection_before)

    def test_invalid_health_times_neither_promote_nor_suppress_automatic_routes(self):
        routes.save(self.root, {'model':routes.DEFAULT_MODEL, 'reasoning_effort':'high', 'mode':'automatic'}, fetcher=fixture)
        local = 'mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit'
        for stamp in ('invalid', None, [], {}, True, float('nan'), float('inf'), float('-inf'), -1, 10**400, 1001, 699):
            for success in (True, False):
                with self.subTest(stamp=stamp, success=success):
                    observation = {'at':stamp, 'success':success, 'error':None if success else 'quota'}
                    (self.root/'model-route-health.json').write_text(json.dumps({routes.DEFAULT_MODEL:observation, local:observation}))
                    payload = routes.catalog(self.root)
                    self.assertEqual(payload['models'][0]['status'], 'connected')
                    self.assertEqual(payload['models'][2]['status'], 'running')
                    self.assertFalse(payload['models'][2]['automatic_candidate'])
                    self.assertEqual(routes.selection(self.root)['model'], routes.DEFAULT_MODEL)

    def test_unparseable_health_cannot_hide_models_or_block_default_manual_selection(self):
        depth = sys.getrecursionlimit() + 20
        nested = ('{"unused":' + '[' * depth + '0' + ']' * depth + '}').encode()
        self.assertLess(len(nested), 128 * 1024)
        for raw in (b'{', b'\xff', b'[]', b'null', b' ' * (128 * 1024 + 1), nested):
            with self.subTest(size=len(raw), prefix=raw[:16]):
                health_path = self.root/'model-route-health.json'
                health_path.write_bytes(raw)
                payload = routes.catalog(self.root)
                self.assertEqual(len(payload['models']), 4)
                self.assertEqual(payload['models'][0]['status'], 'connected')
                self.assertEqual(payload['models'][2]['status'], 'running')
                selected = routes.selection(self.root)
                self.assertEqual(selected['model'], routes.DEFAULT_MODEL)
                self.assertEqual(selected['reasoning_effort'], 'high')
                self.assertEqual(selected['mode'], 'manual')
                self.assertIsNone(selected['configuration_sha256'])
                self.assertFalse((self.root/'reply-model.json').exists())
                self.assertEqual(health_path.read_bytes(), raw)

    def test_health_decode_recursion_does_not_discard_a_confirmed_result(self):
        health_path = self.root/'model-route-health.json'
        health_path.write_text('{}')
        with patch.object(routes.json, 'loads', side_effect=RecursionError('health JSON nesting')):
            self.assertTrue(routes.record_result(self.root, routes.DEFAULT_MODEL, success=True))
        payload = routes.catalog(self.root)
        self.assertEqual(payload['models'][0]['status'], 'available')
        self.assertFalse((self.root/'reply-model.json').exists())

    def test_successful_health_never_bypasses_local_readiness_or_ram_admission(self):
        routes.save(self.root, {'model':routes.DEFAULT_MODEL, 'reasoning_effort':'high', 'mode':'automatic'}, fetcher=fixture)
        selection_path = self.root/'reply-model.json'
        automatic_state = selection_path.read_bytes()
        local = 'mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit'
        (self.root/'model-route-health.json').write_text(json.dumps({local:{'at':1000, 'success':True, 'error':None}}))
        for loaded, state, workspace, expected in ((False, 'ready', 'ready', 'not_loaded'),
                (True, 'loading', 'ready', 'not_loaded'), (True, 'ready', 'memory', 'memory'),
                (True, 'ready', 'memory_unknown', 'memory_unknown')):
            with self.subTest(loaded=loaded, state=state, workspace=workspace):
                def fetcher(url):
                    if ':11234/' in url:
                        return {'data':[{'id':local.removeprefix('mlx/'), 'loaded':loaded, 'state':state}]}
                    return fixture(url)
                selection_path.write_bytes(automatic_state)
                with patch.object(routes, 'catalog', partial(routes.catalog, fetcher=fetcher, memory_reader=lambda:workspace)):
                    payload = routes.catalog(self.root)
                    self.assertEqual(payload['models'][2]['status'], expected)
                    self.assertFalse(payload['models'][2]['selectable'])
                    self.assertFalse(payload['models'][2]['automatic_candidate'])
                    self.assertEqual(routes.selection(self.root)['model'], routes.DEFAULT_MODEL)
                    self.assertEqual(selection_path.read_bytes(), automatic_state)
                    selection_path.write_text(json.dumps({'model':local, 'reasoning_effort':'high', 'mode':'manual'}))
                    manual_state = selection_path.read_bytes()
                    with self.assertRaisesRegex(RuntimeError, '^local_model_not_ready$'):
                        routes.selection(self.root)
                    self.assertEqual(selection_path.read_bytes(), manual_state)

    def test_automatic_uses_only_verified_local_or_flash_and_never_escalates_to_astra(self):
        routes.save(self.root,{'model':routes.DEFAULT_MODEL,'reasoning_effort':'high','mode':'automatic'},fetcher=fixture)
        routes.record_result(self.root,'gpt-6-astra',success=True)
        payload=routes.catalog(self.root,fetcher=fixture,memory_reader=lambda:'ready')
        with patch.object(routes,'catalog',return_value=payload):
            selected=routes.selection(self.root)
        self.assertEqual(selected['model'],routes.DEFAULT_MODEL)
        self.assertEqual(selected['reasoning_effort'],'high')
        routes.record_result(self.root,'mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit',success=True)
        payload=routes.catalog(self.root,fetcher=fixture,memory_reader=lambda:'ready')
        with patch.object(routes,'catalog',return_value=payload):selected=routes.selection(self.root)
        self.assertEqual(selected['transport'],'local'); self.assertIn(':11234/',selected['base_url'])

    def test_automatic_does_not_repeatedly_choose_a_known_quota_failure(self):
        routes.save(self.root,{'model':routes.DEFAULT_MODEL,'reasoning_effort':'high','mode':'automatic'},fetcher=fixture)
        routes.record_result(self.root,routes.DEFAULT_MODEL,success=False,error='quota')
        payload=routes.catalog(self.root,fetcher=fixture,memory_reader=lambda:'ready')
        with patch.object(routes,'catalog',return_value=payload):
            with self.assertRaisesRegex(RuntimeError,'automatic_model_unavailable'):routes.selection(self.root)

if __name__=='__main__':unittest.main()
