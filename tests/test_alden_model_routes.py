import json
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
