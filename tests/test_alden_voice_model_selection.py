"""Configured voice model identity, fixed routes and per-turn snapshots."""
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_voice as voice
import alden_model_routes as routes
from alden_abort import AbortController

class Response(io.BytesIO):
    def __init__(self, raw, content_type='application/json'):
        super().__init__(raw); self.headers = {'Content-Type': content_type}

class VoiceModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        self.path=self.root/'reply-model.json'; self.token=AbortController(self.root).token()

    def save(self, model):
        self.path.write_text(json.dumps({'model': model}))

    def test_documented_default_is_used_only_when_config_is_absent(self):
        result=voice.configured_voice_model(self.root)
        self.assertEqual(result['model'],routes.DEFAULT_MODEL)
        self.assertEqual(result['reasoning_effort'],'high')
        self.assertEqual(result['mode'],'manual')
        self.assertEqual(result['transport'],'opencodex')
        self.assertEqual(result['base_url'],routes.ROUTER)
        self.assertEqual(result['source'],'documented_default')
        self.assertIsNone(result['configuration_sha256'])
        self.assertFalse(self.path.exists())

    @contextmanager
    def remote_response(self):
        requests = []
        catalog = {'models': [{'id': routes.DEFAULT_MODEL, 'local': False,
                              'selectable': True, 'efforts': ['high']}]}
        def transport(request, *_args, **_kwargs):
            requests.append(request)
            return Response(json.dumps({
                'id': 'fixture-response', 'model': routes.DEFAULT_MODEL, 'status': 'completed',
                'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': '모의 응답'}]}],
            }).encode())
        with patch('alden_routed_llm.catalog', return_value=catalog), \
             patch('alden_routed_llm.CancellableLocalResponse', side_effect=transport), \
             patch('alden_voice.LocalMlxLlm', side_effect=AssertionError('unexpected local adapter')):
            yield requests

    def assert_default_request(self, request):
        self.assertEqual(request.full_url, routes.ROUTER + '/responses')
        body = json.loads(request.data)
        self.assertEqual(body['model'], routes.DEFAULT_MODEL)
        self.assertEqual(body['reasoning'], {'effort': 'high'})

    def test_absent_config_routes_actual_voice_caller_to_flash_high(self):
        llm = voice.ConfiguredLocalMlxLlm(self.root)
        turn = voice.VoiceTurn('c' * 32, 1, 1, 'microphone', voice.VoiceTurnToken(self.token))
        with self.remote_response() as requests:
            self.assertEqual(llm.generate_for_turn('질문', self.token, turn=turn), '모의 응답')
        self.assertEqual(len(requests), 1)
        self.assert_default_request(requests[0])
        self.assertEqual(llm.last_metrics['model_selection']['mode'], 'manual')
        self.assertFalse(self.path.exists())

    def test_absent_config_routes_actual_text_caller_to_flash_high(self):
        from alden_chat import process_input
        value = {'request_id': 'd' * 32, 'conversation_id': None, 'text': '질문', 'images': []}
        with self.remote_response() as requests:
            receipt = process_input(self.root, value, self.token)
        self.assertEqual(receipt['state'], 'completed', receipt)
        self.assertEqual(receipt['reply'], '모의 응답')
        self.assertEqual(len(requests), 1)
        self.assert_default_request(requests[0])
        self.assertFalse(self.path.exists())

    def test_nonexistent_state_root_gets_explicit_defaults_without_creating_state(self):
        root = self.root / 'new-state'
        result = voice.configured_voice_model(root)
        self.assertEqual((result['model'], result.get('reasoning_effort'), result.get('mode')),
                         (routes.DEFAULT_MODEL, 'high', 'manual'))
        self.assertEqual(result.get('transport'), 'opencodex')
        self.assertFalse(root.exists())

    def test_absent_and_saved_manual_defaults_have_the_same_route(self):
        absent = voice.configured_voice_model(self.root)
        self.path.write_text(json.dumps({'model': routes.DEFAULT_MODEL, 'reasoning_effort': 'high',
                                        'mode': 'manual', 'transport': 'opencodex'}))
        catalog = {'models': [{'id': routes.DEFAULT_MODEL, 'local': False,
                              'selectable': True, 'efforts': ['high']}]}
        with patch('alden_model_routes.catalog', return_value=catalog):
            saved = voice.configured_voice_model(self.root)
        keys = ('model', 'reasoning_effort', 'mode', 'transport', 'base_url')
        self.assertEqual({k: absent.get(k) for k in keys}, {k: saved[k] for k in keys})
        self.assertEqual(saved['source'], 'saved_configuration')
        self.assertIsNotNone(saved['configuration_sha256'])

    def test_direct_local_adapter_keeps_its_27b_default(self):
        adapter = voice.LocalMlxLlm()
        self.assertEqual(adapter.model, voice.QWEN38_27B_MODEL_ID)
        self.assertEqual(adapter.base_url, voice.LOCAL_LLM_BASE_URL)

    def test_each_fixed_choice_and_prefixless_alias_has_exact_service(self):
        for model in voice.LOCAL_LLM_ALLOWED_MODEL_IDS:
            for selected in (model,model.removeprefix('mlx/')):
                with self.subTest(selected=selected):
                    self.save(selected); result=voice.configured_voice_model(self.root)
                    self.assertEqual(result['model'],model)
                    expected=11235 if model==voice.FLASH_NEXT_IQ_MODEL_ID else 11234
                    adapter=voice.LocalMlxLlm(model=selected)
                    self.assertIn(str(expected),adapter.base_url)
                    self.assertEqual(adapter.base_url,result['base_url'])
                    wrong=f'http://127.0.0.1:{11234 if expected==11235 else 11235}/v1'
                    with self.assertRaises(ValueError):voice.LocalMlxLlm(base_url=wrong,model=selected)

    def test_invalid_or_remote_choice_is_not_repaired_or_sent_to_default(self):
        adapter=voice.ConfiguredLocalMlxLlm(self.root)
        for text in ('bad-json','[]','{}','{"model":7}',json.dumps({'model':'cloud/remote'}),'x'*8193):
            with self.subTest(text=text[:40]):
                self.path.write_text(text)
                expected = ('voice_model_selection_unavailable' if text == json.dumps({'model':'cloud/remote'})
                            else 'voice_model_configuration_invalid')
                with patch('alden_voice._local_urlopen') as network, \
                     patch('alden_model_routes.selection', side_effect=AssertionError('invalid configuration must not use defaults')):
                    with self.assertRaisesRegex(RuntimeError, '^' + expected + '$'):
                        adapter.generate('request',self.token)
                    network.assert_not_called()
                self.assertEqual(self.path.read_text(),text)
                self.assertEqual(adapter.last_metrics['request']['state'],'failed')
        self.path.unlink(); target=self.root/'another.json'; target.write_text(json.dumps({'model':voice.QWEN38_27B_MODEL_ID}));self.path.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError,'configuration_invalid'):voice.configured_voice_model(self.root)

    def test_in_flight_request_keeps_old_choice_and_next_turn_reads_new_choice(self):
        iq=voice.FLASH_NEXT_IQ_MODEL_ID; old=voice.QWEN38_27B_MODEL_ID;self.save(iq)
        requests=[]; changed=False
        def transport(request, **_kwargs):
            nonlocal changed
            requests.append(request)
            model=iq if ':11235/' in request.full_url else old
            if request.full_url.endswith('/models'):
                if not changed:self.save(old);changed=True
                return Response(json.dumps({'data':[{'id':model.removeprefix('mlx/'),'loaded':True,'state':'ready'}]}).encode())
            if request.full_url.endswith('/metrics'):return Response(b'vllm:num_requests_running 0\n','text/plain')
            payload=json.loads(request.data);self.assertEqual(payload['model'],model.removeprefix('mlx/'))
            return Response(json.dumps({'id':'request-a','model':model.removeprefix('mlx/'),'choices':[{'message':{'content':'confirmed'},'finish_reason':'stop'}]}).encode())
        llm=voice.ConfiguredLocalMlxLlm(self.root)
        turn=voice.VoiceTurn('c'*32,1,2,'text',voice.VoiceTurnToken(self.token))
        with patch('alden_voice._local_urlopen',side_effect=transport):
            self.assertEqual(llm.generate_for_turn('first',self.token,turn=turn),'confirmed')
            self.assertEqual(llm.last_metrics['request']['model_id'],iq)
            self.assertEqual(llm.last_metrics['request']['conversation_id'],turn.conversation_id)
            self.assertEqual(llm.last_metrics['model_selection']['model'],iq)
            self.assertEqual(llm.generate('next',self.token),'confirmed')
            self.assertEqual(llm.last_metrics['model_selection']['model'],old)
        posts=[r for r in requests if r.data]
        self.assertEqual([r.full_url for r in posts],['http://127.0.0.1:11235/v1/chat/completions','http://127.0.0.1:11234/v1/chat/completions'])

    def test_unprepared_iq_is_not_sent_to_a_ready_27b(self):
        self.save(voice.FLASH_NEXT_IQ_MODEL_ID);llm=voice.ConfiguredLocalMlxLlm(self.root);calls=[]
        def transport(request,**_kwargs):
            calls.append(request)
            return Response(json.dumps({'data':[{'id':voice.QWEN38_27B_MODEL_ID.removeprefix('mlx/'),'loaded':True,'state':'ready'}]}).encode())
        with patch('alden_voice._local_urlopen',side_effect=transport):
            with self.assertRaisesRegex(RuntimeError,'local_llm_model_not_ready'):llm.generate('request',self.token)
        self.assertTrue(calls);self.assertTrue(all(':11235/' in r.full_url and not r.data for r in calls))

    def test_cancellable_transport_adds_only_the_exact_iq_port(self):
        for url, port, valid in [('http://127.0.0.1:11235/metrics',11235,True),
                                 ('http://127.0.0.1:11234/metrics',11235,False),
                                 ('http://127.0.0.1:11236/metrics',11235,False),
                                 ('https://outside.example/metrics',11235,False)]:
            with self.subTest(url=url), patch('alden_local_http.http.client.HTTPConnection') as connection:
                connection.return_value.getresponse.return_value.status = 200
                request=voice.urllib.request.Request(url);request._alden_abort_token=self.token;request._alden_model_port=port
                if valid:
                    with voice._local_urlopen(request,timeout=.35):pass
                    self.assertEqual(connection.call_args.args,('127.0.0.1',11235))
                else:
                    with self.assertRaises(ValueError):
                        with voice._local_urlopen(request,timeout=.35):pass
                    connection.assert_not_called()

    def test_nonregular_configuration_is_rejected_without_waiting_for_a_writer(self):
        os.mkfifo(self.path)
        with self.assertRaisesRegex(RuntimeError, 'configuration_invalid'):
            voice.configured_voice_model(self.root)

    def test_cancelled_next_turn_does_not_report_previous_success(self):
        llm = voice.ConfiguredLocalMlxLlm(self.root)
        llm.last_metrics = {'request': {'state': 'completed', 'turn_id': 1}}
        self.token.cancel()
        turn = voice.VoiceTurn('c' * 32, 2, 3, 'text', voice.VoiceTurnToken(self.token))
        with patch('alden_voice._local_urlopen') as network:
            with self.assertRaises(voice.AldenCancelled):
                llm.generate_for_turn('cancelled', self.token, turn=turn)
            network.assert_not_called()
        self.assertEqual(llm.last_metrics['request']['turn_id'], 2)
        self.assertEqual(llm.last_metrics['request']['state'], 'cancelled')

    def test_pipeline_retains_confirmed_input_but_never_speaks_or_saves_a_fallback(self):
        self.save('remote/unsupported')
        tts = Mock()
        pipeline = voice.AldenVoicePipeline(
            stt=Mock(), llm=voice.ConfiguredLocalMlxLlm(self.root),
            tts=tts, token=self.token, status=voice.VoiceStatusStore(self.root),
        )
        try:
            with patch('alden_voice._local_urlopen') as network:
                result = pipeline.process_text('confirmed input')
                network.assert_not_called()
            self.assertEqual(result.state, voice.VoiceState.ERROR)
            self.assertEqual(result.error_code, 'voice_model_selection_unavailable')
            tts.speak.assert_not_called()
            self.assertEqual(list(pipeline._conversation), [{'role': 'user', 'content': 'confirmed input'}])
            from alden_history import read
            page = read(self.root, Path('/unused'), 'voice-history-messages', chat_id=result.conversation_id)
            self.assertEqual([row['role'] for row in page['items']], ['user'])
        finally:
            pipeline.close()

if __name__=='__main__':unittest.main()
