from __future__ import annotations
import base64
import hashlib
from pathlib import Path
import sys
import tempfile
import json
import io
import subprocess
import threading
import struct
import zlib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import alden_chat as chat
from alden_abort import AbortToken, AbortController, AldenCancelled
from alden_history import database, read
from alden_chat_turns import reserve_turn, complete_turn


def request(n=1, cid=None, text='질문 42', images=None):
    return {'request_id': f'{n:032x}', 'conversation_id': cid, 'text': text, 'images': images or []}


def png(rgb=(255,0,0)):
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',1,1,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(b'\0'+bytes(rgb)))+chunk(b'IEND',b''))


class AssistantInputTests(unittest.TestCase):
    def test_pre_adapter_selection_failure_keeps_its_exact_reason_on_replay(self):
        from alden_chat_turns import get_request
        for number,code in [(81,'routed_llm_unavailable'),(82,'routed_llm_authentication'),
                            (83,'routed_llm_quota'),(84,'model_effort_not_supported')]:
            with self.subTest(code=code):
                select=unittest.mock.Mock(side_effect=RuntimeError(code))
                generate=unittest.mock.Mock(side_effect=AssertionError('generation must not start'))
                value=request(number)
                first=chat.process_input(self.root,value,self.token,select_model=select,generate=generate)
                self.assertEqual(first['error_code'],code)
                replay=chat.process_input(self.root,value,self.token,select_model=select,generate=generate)
                self.assertEqual(replay.get('error_code'),code)
                self.assertEqual(get_request(self.root,value['request_id'])['generation']['error_code'],code)
                self.assertNotIn('response',replay['generation'])
                self.assertEqual(replay['generation']['selection'],{})
                self.assertNotIn('reply',replay)
                select.assert_called_once();generate.assert_not_called()

    def test_catalog_transport_failures_are_safe_selection_errors_with_durable_replay(self):
        import alden_model_routes as routes
        from urllib.error import HTTPError,URLError
        cases=[(TimeoutError('private endpoint timed out'),'routed_llm_unavailable'),
               (URLError('private endpoint unavailable'),'routed_llm_unavailable'),
               (HTTPError('http://user:secret@127.0.0.1/private',401,'private',{},None),'routed_llm_authentication'),
               (HTTPError('http://127.0.0.1/private',429,'private',{},None),'routed_llm_quota')]
        for number,(failure,code) in enumerate(cases,90):
            with self.subTest(code=code):
                fetch=unittest.mock.Mock(side_effect=failure)
                def select(root):return routes.catalog(root,fetcher=fetch)
                generate=unittest.mock.Mock(side_effect=AssertionError('generation must not start'))
                first=chat.process_input(self.root,request(number),self.token,select_model=select,generate=generate)
                self.assertEqual(first['error_code'],code)
                discovery_calls = [unittest.mock.call(base + '/models') for base in
                                   (routes.ROUTER, *routes.LOCAL_BASE_URLS)]
                self.assertEqual(fetch.call_args_list, discovery_calls)
                replay=chat.process_input(self.root,request(number),self.token,select_model=select,generate=generate)
                self.assertEqual(replay.get('error_code'),code)
                self.assertNotIn('private',json.dumps(first)+json.dumps(replay))
                self.assertEqual(fetch.call_args_list, discovery_calls)
                generate.assert_not_called()

    def test_adapter_catalog_rejection_keeps_its_reason_without_response_or_provider_health_write(self):
        import alden_model_routes as routes
        from urllib.error import HTTPError
        for number,status,code in [(96,401,'routed_llm_authentication'),(97,429,'routed_llm_quota'),(98,503,'routed_llm_unavailable')]:
            with self.subTest(status=status):
                def catalog(root):
                    def fetch(url):raise HTTPError(url,status,'private-provider-body',{},None)
                    return routes.catalog(root,fetcher=fetch)
                with patch('alden_routed_llm.catalog',side_effect=catalog), \
                     patch('alden_routed_llm.CancellableLocalResponse') as transport, \
                     patch('alden_routed_llm.record_result') as health:
                    value=request(number)
                    first=self.run_input(value,generate=chat.generate_input)
                    self.assertEqual(first['error_code'],code)
                    replay=self.run_input(value,generate=chat.generate_input)
                    self.assertEqual(replay.get('error_code'),code)
                    self.assertEqual(replay['generation']['request']['stage'],'selection')
                    self.assertNotIn('response',replay['generation'])
                    transport.assert_not_called();health.assert_not_called()


    def test_advertised_max_route_completes_and_replays_without_losing_effort(self):
        import alden_model_routes as routes
        from functools import partial
        model='fixture/model-with-max'
        def catalog_source(url):
            if '/v1/models' in url and ':1123' in url:
                return {'data':[]}
            return {'models':[{'slug':model,'reasoning_efforts':['high','max']}]}
        actual_catalog=routes.catalog
        class Response(io.BytesIO):
            headers={'Content-Type':'application/json'}
            def __exit__(self,*args):return False
        payload={'model':model,'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'검증 응답'}]}]}
        with patch.object(routes,'catalog',partial(actual_catalog,fetcher=catalog_source)), \
                patch('alden_routed_llm.catalog',partial(actual_catalog,fetcher=catalog_source)), \
                patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('live HTTP forbidden')) as network, \
                patch('alden_routed_llm.CancellableLocalResponse',side_effect=lambda *args,**kwargs:Response(json.dumps(payload).encode())) as transport:
            routes.save(self.root,{'model':model,'reasoning_effort':'max'},fetcher=catalog_source)
            settings=(self.root/'reply-model.json').read_bytes()
            value=request(70)
            first=chat.process_input(self.root,value,self.token)
            self.assertTrue(first['ok'],first)
            self.assertEqual(first['reply'],'검증 응답')
            self.assertEqual(first['generation']['selection']['reasoning_effort'],'max')
            self.assertEqual(first['generation']['request']['reasoning_effort'],'max')
            body=json.loads(transport.call_args.args[0].data)
            self.assertEqual(body['model'],model)
            self.assertEqual(body['reasoning'],{'effort':'max'})
            self.assertEqual(body['max_output_tokens'],2048)
            replay=chat.process_input(self.root,value,self.token)
            self.assertEqual(first,replay)
            transport.assert_called_once()
            network.assert_not_called()
            self.assertEqual((self.root/'reply-model.json').read_bytes(),settings)
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],1)

    def test_actual_transport_images_are_bound_to_selected_bytes(self):
        photo = {'name':'public.png','mime':'image/png','data':base64.b64encode(png()).decode()}
        proof = {'sha256':hashlib.sha256(png()).hexdigest(),'mime':'image/png','bytes':len(png())}
        result = self.run_input(request(images=[photo]),generate=lambda *args: ('사진 답변',{'images':[proof]}))
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['generation']['transport_images'],[proof])

    def test_different_transport_image_bytes_cannot_commit_answer(self):
        photo = {'name':'public.png','mime':'image/png','data':base64.b64encode(png()).decode()}
        proof = {'sha256':'f'*64,'mime':'image/png','bytes':len(png())}
        result = self.run_input(request(images=[photo]),generate=lambda *args: ('다른 사진 답변',{'images':[proof]}))
        self.assertEqual(result['error_code'],'generation_metadata_invalid')
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def test_failed_generation_keeps_bound_observations_without_answer(self):
        def failed(root,ticket,text,token,paths,selection):
            ticket['adapter_metrics']={'request':{**{k:ticket[k] for k in ('conversation_id','turn_id','context_version')},
                'model_id':'selected/model','transport':'opencodex','state':'failed','source':'text'},
                'elapsed_ms':12,'error_code':'routed_llm_generation_failed'}
            raise RuntimeError('routed_llm_generation_failed')
        result=self.run_input(request(),generate=failed)
        self.assertEqual(result['state'],'failed')
        from alden_chat_turns import get_request
        generation=get_request(self.root,request()['request_id'])['generation']
        self.assertEqual(generation['terminal_state'],'failed')
        self.assertEqual(generation['request']['context_binding'],'verified')
        self.assertEqual(generation['timing']['elapsed_ms'],12)
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def test_provider_failure_reaches_receipt_and_durable_evidence_without_reply(self):
        from urllib.error import HTTPError
        from alden_chat_turns import get_request
        catalog = {'models':[{'id':'selected/model','selectable':True,'local':False,'efforts':['high']}]}
        for number, status, code in [(10,429,'routed_llm_quota'),(11,401,'routed_llm_authentication'),(12,503,'routed_llm_unavailable')]:
            with self.subTest(status=status), patch('alden_routed_llm.catalog',return_value=catalog), \
                    patch('alden_routed_llm.CancellableLocalResponse',side_effect=HTTPError('http://127.0.0.1:10101/v1/responses',status,'provider rejected',{},None)) as transport:
                value=request(number)
                result=self.run_input(value,generate=chat.generate_input)
                self.assertEqual(result['error_code'],code)
                self.assertEqual(result['state'],'failed')
                self.assertEqual(result['request_id'],value['request_id'])
                self.assertEqual(get_request(self.root,value['request_id'])['generation']['error_code'],code)
                replay=self.run_input(value,generate=chat.generate_input)
                self.assertEqual(replay.get('error_code'),code)
                self.assertEqual(replay['request_id'],value['request_id'])
                transport.assert_called_once()
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def test_unrecognized_generation_failure_never_echoes_provider_payload(self):
        def fail(*args):
            raise RuntimeError('routed_llm_quota: secret-provider-payload')
        result=self.run_input(request(),generate=fail)
        self.assertEqual(result['error_code'],'assistant_input_failed')
        self.assertNotIn('secret-provider-payload',json.dumps(result))

    def test_replayed_reason_requires_a_terminal_failure_and_exact_safe_code(self):
        for state,terminal,code in [('cancelled','failed','routed_llm_quota'),
                                   ('failed','completed','routed_llm_quota'),
                                   ('failed','failed','routed_llm_quota: private payload'),
                                   ('failed','failed',{'reason':'private payload'})]:
            with self.subTest(state=state,terminal=terminal,code=code):
                result=chat.public_receipt({'state':state,'generation':{'terminal_state':terminal,'error_code':code}})
                self.assertNotIn('error_code',result)

    def test_mismatched_generation_identity_is_not_committed_as_a_reply(self):
        def wrong(root,ticket,*args):
            return '다른 맥락의 답변',{'request':{'conversation_id':'f'*32,'turn_id':ticket['turn_id'],'context_version':ticket['context_version']}}
        result=self.run_input(request(),generate=wrong)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['error_code'],'generation_metadata_invalid')
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = AbortToken(self.root / 'alden-abort.json')
        self.calls = []

    def tearDown(self): self.temp.cleanup()

    def select(self, root): return {'transport': 'opencodex', 'model': 'selected/model', 'reasoning_effort': 'high'}

    def cli(self, value, token_id='00000000-0000-4000-8000-000000000001'):
        result = subprocess.run([sys.executable,'-B',str(ROOT/'scripts/alden_chat.py'),
                                 '--state-root',str(self.root),'--token-id',token_id],
                                input=json.dumps(value),text=True,capture_output=True,timeout=5,check=True)
        return json.loads(result.stdout)

    def test_cli_invalid_media_preserves_specific_failure_and_request_identity(self):
        value=request(images=[{'name':'photo.png','mime':'image/png','data':'%%%'}])
        result=self.cli(value)
        self.assertEqual(result['error_code'],'assistant_image_invalid')
        self.assertEqual(result['request_id'],value['request_id'])
        self.assertIsNone(result['conversation_id'])
        self.assertFalse((self.root/'alden-history.sqlite3').exists())

    def test_cli_startup_cancellation_is_not_reported_as_malformed_input(self):
        token_id='00000000-0000-4000-8000-000000000001'
        folder=self.root/'assistant-input-cancel';folder.mkdir(mode=0o700)
        (folder/(token_id+'.json')).write_text('{}')
        value=request()
        result=self.cli(value,token_id)
        self.assertEqual(result['state'],'cancelled')
        self.assertEqual(result['error_code'],'assistant_input_cancelled')
        self.assertEqual(result['request_id'],value['request_id'])
        self.assertFalse((self.root/'alden-history.sqlite3').exists())

    def test_newer_turn_supersedes_late_answer_without_generic_failure(self):
        def superseded(root,ticket,text,token,paths,selection):
            reserve_turn(root,'f'*32,ticket['conversation_id'],'더 최신 질문')
            return '이전 답변',{}
        result=self.run_input(request(),generate=superseded)
        self.assertEqual(result['state'],'superseded')
        self.assertEqual(result['error_code'],'assistant_input_superseded')
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def generate(self, root, ticket, text, token, paths, selection):
        self.calls.append((ticket, text, paths, selection))
        return '확인한 답변 42', {}

    def run_input(self, value, **kwargs):
        return chat.process_input(self.root, value, kwargs.pop('token', self.token),
                                  generate=kwargs.pop('generate', self.generate), select_model=self.select, **kwargs)

    def test_text_without_voice_models_and_completed_replay_preserves_one_turn(self):
        first = self.run_input(request())
        self.assertTrue(first['ok']); self.assertEqual(first['state'], 'completed')
        self.assertNotIn('owner_id', first); self.assertNotIn('history', first)
        again = self.run_input(request())
        self.assertEqual(first, again); self.assertEqual(len(self.calls), 1)
        messages = read(self.root, None, 'voice-history-messages', chat_id=first['conversation_id'])['items']
        self.assertEqual([(x['role'], x['content']) for x in messages], [('user','질문 42'),('assistant','확인한 답변 42')])
        self.assertFalse((self.root / 'voice-status.json').exists())

    def test_followup_uses_same_confirmed_conversation_and_latest_input(self):
        first = self.run_input(request())
        second = self.run_input(request(2, first['conversation_id'], '그 수량은?'))
        self.assertTrue(second['ok']); self.assertGreater(second['turn_id'], first['turn_id'])
        ticket, text, _, selection = self.calls[-1]
        self.assertEqual(text, '그 수량은?')
        self.assertEqual([x['content'] for x in ticket['history']], ['질문 42','확인한 답변 42'])
        self.assertEqual(selection['model'], 'selected/model')

    def test_concurrent_duplicate_does_not_start_second_inference(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def blocked(*args):
            self.calls.append(args); entered.set(); self.assertTrue(release.wait(3)); return '완료', {}
        thread = threading.Thread(target=lambda: results.append(self.run_input(request(), generate=blocked)))
        thread.start(); self.assertTrue(entered.wait(3))
        try:
            duplicate = self.run_input(request())
            self.assertFalse(duplicate['ok']); self.assertEqual(duplicate['state'], 'running')
            self.assertEqual(len(self.calls), 1)
        finally: release.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertTrue(results[0]['ok'])

    def test_cancellation_after_model_result_never_commits_assistant(self):
        def cancelled(*args): self.token.cancel(); return '늦은 답변', {}
        result = self.run_input(request(), generate=cancelled)
        self.assertFalse(result['ok']); self.assertEqual(result['state'], 'cancelled')
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0], 0)

    def test_global_abort_before_input_prevents_any_turn_and_inference(self):
        controller = AbortController(self.root)
        controller.abort('global_abort')
        with self.assertRaises(AldenCancelled): self.run_input(request())
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / 'alden-history.sqlite3').exists())

    def test_bad_inputs_and_images_never_generate_or_create_history(self):
        cases = [request(text='\x00'), request(text='x'*4001), request(cid='foreign'),
                 request(images=[{'name':'../file.png','mime':'image/png','data':'abcd'}]),
                 request(images=[{'name':'file.png','mime':'image/png','data':'%%%'}])]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(chat.InputUnavailable): self.run_input(value)
        self.assertFalse((self.root / 'alden-history.sqlite3').exists()); self.assertEqual(self.calls, [])

    def test_image_bytes_bound_to_hash_and_same_conversation_followup(self):
        data = png()
        image = {'name':'fixture.png','mime':'image/png','data':base64.b64encode(data).decode()}
        first = self.run_input(request(images=[image]))
        self.assertTrue(first['ok']); path = self.calls[0][2][0]
        self.assertEqual(path.read_bytes(), data); self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn(hashlib.sha256(data).hexdigest(), path.name)
        follow = self.run_input(request(2, first['conversation_id'], '그 이미지의 의미는?'))
        self.assertTrue(follow['ok']); self.assertEqual(self.calls[-1][2], [path])
        unrelated = self.run_input(request(3))
        self.assertTrue(unrelated['ok']); self.assertEqual(self.calls[-1][2], [])

    def test_followup_rechecks_sources_after_concurrent_retraction(self):
        import alden_chat_links as links
        from alden_chat_turns import cancel_turn, get_request
        from alden_link_content import html_preview

        def fetch(root, urls, token):
            raw = b'<p>Private link quantity 73</p>'
            return [links.cache_observation(root, url, raw, html_preview(url, raw),
                    None, [{'url': url, 'status': 200}], token) for url in urls]

        for index, kind in enumerate(('image', 'file', 'link'), 1):
            with self.subTest(kind=kind), patch.object(links, 'fetch_current', side_effect=fetch):
                number = index * 10
                value = request(number, text='Source attachment')
                if kind == 'image':
                    value['images'] = [{'name': 'private.png', 'mime': 'image/png',
                                        'data': base64.b64encode(png()).decode()}]
                elif kind == 'file':
                    value['files'] = [{'name': 'private.txt', 'mime': 'text/plain',
                                       'data': base64.b64encode(b'Private file quantity 53').decode()}]
                else:
                    value['text'] = 'https://example.com/private'
                first = self.run_input(value)
                self.assertTrue(first['ok'], first)
                original = self.calls[-1][0]
                entered, release = threading.Event(), threading.Event()
                results = []

                def retrieve(text, *args, **kwargs):
                    self.assertIs(kwargs.get('voice_style'), False)
                    if text == 'Paused followup':
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError('test retrieval gate expired')
                    return '', {}

                with patch('alden_voice._voice_knowledge_reference', side_effect=retrieve), \
                     patch('alden_routed_llm.OpenCodexLlm._generate', return_value='Fixture reply') as adapter:
                    healthy = self.run_input(request(number + 1, first['conversation_id'], 'Reuse attachment'),
                                             generate=chat.generate_input)
                    self.assertTrue(healthy['ok'], healthy)
                    sent = adapter.call_args.kwargs
                    self.assertEqual((sent['turn'].conversation_id, sent['turn'].turn_id,
                                      sent['turn'].context_version, sent['turn'].source),
                                     (first['conversation_id'], 2, 3, 'text'))
                    if kind == 'image':
                        self.assertEqual([path.read_bytes() for path in sent['image_paths']], [png()])
                        self.assertEqual(sent['image_evidence'][0]['source_request_id'], original['request_id'])
                        self.assertIn('완료된 이전 턴, 대화 턴 1.', sent['system_prompt'])
                    else:
                        self.assertIn('Private ' + kind + ' quantity', sent['system_prompt'])
                        self.assertIn('"source_turn_id": 1' if kind == 'file' else '"source_turn_id":1',
                                      sent['system_prompt'])
                        self.assertIn(original['request_id'], sent['system_prompt'])
                    adapter.reset_mock()
                    pending = request(number + 2, first['conversation_id'], 'Paused followup')
                    worker = threading.Thread(target=lambda: results.append(
                        self.run_input(pending, generate=chat.generate_input)))
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        other = self.run_input(request(number + 3, text='Unrelated conversation'),
                                               generate=chat.generate_input)
                        self.assertTrue(other['ok'], other)
                        sent = adapter.call_args.kwargs
                        self.assertEqual(sent['turn'].conversation_id, other['conversation_id'])
                        self.assertEqual(sent['history'], [])
                        self.assertEqual(sent['image_paths'], [])
                        self.assertEqual(sent['image_evidence'], [])
                        self.assertNotIn('Private ', sent['system_prompt'])
                        self.assertNotIn(original['request_id'], sent['system_prompt'])
                        cancel_turn(self.root, original, reason='retracted')
                    finally:
                        release.set()
                        worker.join(5)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(len(results), 1)
                    self.assertEqual(results[0]['error_code'], f'assistant_{kind}_context_changed')
                    self.assertEqual(results[0]['conversation_id'], first['conversation_id'])
                    receipt = get_request(self.root, pending['request_id'])
                    self.assertEqual((receipt['turn_id'], receipt['context_version'], receipt['source']), (3, 5, 'text'))
                    self.assertEqual(receipt['state'], 'failed')
                    self.assertNotIn('reply', receipt)
                    self.assertEqual(adapter.call_count, 1, 'revoked evidence reached the model adapter')

    def test_mime_mismatch_and_unsafe_cache_path_do_not_reach_model(self):
        data = b'GIF89a' + b'\x00'*18
        bad = {'name':'fixture.png','mime':'image/png','data':base64.b64encode(data).decode()}
        with self.assertRaises(Exception): self.run_input(request(images=[bad]))
        self.assertEqual(self.calls, [])
        with tempfile.TemporaryDirectory() as outside:
            other = self.root/'other'; other.mkdir(mode=0o700)
            (other/'assistant-input-locks').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                chat.process_input(other,request(),AbortToken(other/'alden-abort.json'),generate=self.generate,select_model=self.select)

    def test_local_image_route_is_rejected_without_fallback(self):
        ticket = {'conversation_id':'1'*32,'turn_id':1,'context_version':1,'history':[]}
        with self.assertRaisesRegex(chat.InputUnavailable, 'assistant_image_model_unsupported'):
            chat.generate_input(self.root, ticket, '사진', self.token, [Path('/not/read')],
                                {'transport':'local','model':'chosen/local','base_url':'http://127.0.0.1:11234/v1'})

    def test_startup_cancellation_marker_blocks_before_input(self):
        token_id = '00000000-0000-4000-8000-000000000001'
        directory = self.root/'assistant-input-cancel'; directory.mkdir(mode=0o700)
        (directory/(token_id+'.json')).write_text('{}')
        token = chat.InputToken(self.root,token_id)
        with self.assertRaises(AldenCancelled): self.run_input(request(),token=token)
        self.assertFalse((self.root/'alden-history.sqlite3').exists())

    def test_isolated_input_token_respects_original_abort_during_generation(self):
        with tempfile.TemporaryDirectory() as shared:
            original=Path(shared);controller=AbortController(original)
            token=chat.InputToken(self.root,'00000000-0000-4000-8000-000000000001',original)
            def stop(*args):
                controller.abort('global_abort')
                return '취소 뒤 응답',{}
            result=self.run_input(request(),token=token,generate=stop)
            self.assertEqual(result['state'],'cancelled')
            self.assertFalse((self.root/'alden-abort.json').exists())
            with database(self.root) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def test_image_only_and_unicode_text_keep_original_content_and_safe_file_names(self):
        data = png()
        image = {'name':'한글 $(data).png','mime':'image/png','data':base64.b64encode(data).decode()}
        first = self.run_input(request(text='',images=[image]))
        self.assertTrue(first['ok'])
        rows = read(self.root,None,'voice-history-messages',chat_id=first['conversation_id'])['items']
        self.assertEqual(rows[0]['content'],'')
        self.assertEqual(rows[0]['attachments'][0]['filename'],image['name'])
        self.assertNotIn('$',self.calls[0][2][0].name)
        message = '가족 👨‍👩‍👧‍👦와 فارسی می\u200cشود'
        second = self.run_input(request(2,text=message))
        self.assertTrue(second['ok']); self.assertEqual(self.calls[-1][1],message)

    def test_changed_image_between_intake_and_transport_never_reaches_provider(self):
        from tests.test_alden_routed_llm import JsonResponse
        original=png();image={'name':'photo.png','mime':'image/png','data':base64.b64encode(original).decode()}
        def retrieve(*args, **kwargs):
            self.assertIs(kwargs.get('voice_style'), False)
            path=next((self.root/'assistant-input-images').glob('*.png'))
            path.write_bytes(png((0,0,255)))
            return '',{}
        response={'status':'completed','model':'selected/model','output':[{'type':'message','role':'assistant',
                  'content':[{'type':'output_text','text':'바뀐 사진의 답변'}]}]}
        catalog={'models':[{'id':'selected/model','selectable':True,'local':False,'efforts':['high']}]}
        with patch('alden_voice._voice_knowledge_reference',side_effect=retrieve), \
             patch('alden_routed_llm.catalog',return_value=catalog), \
             patch('alden_routed_llm.CancellableLocalResponse',return_value=JsonResponse(response)) as transport:
            result=self.run_input(request(images=[image]),generate=chat.generate_input)
            self.assertEqual(result['error_code'],'image_input_provenance_changed')
            transport.assert_not_called()
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],0)

    def test_real_loopback_responses_transport_binds_history_images_and_completed_model(self):
        from tests.test_alden_route_http import RealHttpMediaFollowupTest, IMAGE, ROUTE
        from alden_model_routes import DEFAULT_MODEL
        RealHttpMediaFollowupTest.setUpClass()
        try:
            selected = lambda root: {'transport':'opencodex','model':DEFAULT_MODEL,'reasoning_effort':'high'}
            image = {'name':'fixture.png','mime':'image/png','data':base64.b64encode(IMAGE).decode()}
            with patch('alden_routed_llm.catalog',return_value=ROUTE):
                first = chat.process_input(self.root,request(text='CASE:compact 사진 설명',images=[image]),self.token,select_model=selected)
                self.assertTrue(first['ok'])
                second = chat.process_input(self.root,request(2,first['conversation_id'],'CASE:multiline 그 사진 후속'),self.token,select_model=selected)
                self.assertTrue(second['ok'])
            sent = RealHttpMediaFollowupTest.server.requests
            self.assertEqual(sent['compact']['model'],DEFAULT_MODEL)
            self.assertEqual(sent['compact']['reasoning']['effort'],'high')
            for key in ('compact','multiline'):
                image_part=sent[key]['input'][-1]['content'][1]
                self.assertEqual(base64.b64decode(image_part['image_url'].split(',',1)[1]),IMAGE)
            self.assertEqual([x['role'] for x in sent['multiline']['input']],[ 'system','user','assistant','user'])
            self.assertEqual(sent['multiline']['input'][1]['content'],'CASE:compact 사진 설명')
            self.assertEqual(len(read(self.root,None,'voice-history-messages',chat_id=first['conversation_id'])['items']),4)
        finally:
            RealHttpMediaFollowupTest.tearDownClass()


if __name__ == '__main__': unittest.main()
