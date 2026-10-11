from __future__ import annotations
import base64
import hashlib
from pathlib import Path
import sys
import tempfile
import json
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
        def retrieve(*args):
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
