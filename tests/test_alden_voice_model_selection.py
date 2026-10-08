"""Configured voice model identity, fixed routes and per-turn snapshots."""
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_voice as voice
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
        self.assertEqual(result['model'],voice.QWEN38_27B_MODEL_ID)
        self.assertEqual(result['source'],'documented_default')
        self.assertIsNone(result['configuration_sha256'])

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
                with patch('alden_voice._local_urlopen') as network:
                    with self.assertRaises(RuntimeError):adapter.generate('request',self.token)
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
