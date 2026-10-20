# -*- coding: utf-8 -*-
from __future__ import annotations
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
if os.environ.get('ALDEN_CHAT_TEST_MODULE_DIR'):
    sys.path.insert(0, os.environ['ALDEN_CHAT_TEST_MODULE_DIR'])
import alden_chat as chat
import alden_chat_files as files
from alden_abort import AbortToken, AbortController, AldenCancelled
from alden_history import database
from alden_chat_turns import reserve_turn, cancel_turn
from tests.test_alden_file_content import docx, xlsx, pdf
from tests.test_alden_chat import png


def attachment(name='receipt.txt', data=b'Alden quantity 53.', mime='text/plain'):
    return {'name': name, 'mime': mime, 'data': base64.b64encode(data).decode()}


def request(n=1, cid=None, text='Read the attachment.', documents=None, images=None):
    return {'request_id': f'{n:032x}', 'conversation_id': cid, 'text': text,
            'images': images or [], 'files': documents or []}


class ChatFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.token = AbortToken(self.root / 'alden-abort.json'); self.calls = []
    def tearDown(self): self.temp.cleanup()
    def generate(self, root, ticket, text, token, paths, selection):
        self.calls.append(ticket); return 'Confirmed 53', {}
    def run_input(self, value, **kwargs):
        return chat.process_input(self.root, value, self.token, generate=kwargs.get('generate', self.generate),
                                  select_model=lambda root: {'transport':'opencodex','model':'selected/model'})

    def test_real_document_bytes_reach_same_conversation_followup(self):
        first = self.run_input(request(documents=[attachment()]))
        self.assertTrue(first['ok'])
        self.assertIn('quantity 53', files.document_prompt(self.calls[-1]))
        second = self.run_input(request(2, first['conversation_id'], 'What was the quantity?'))
        self.assertTrue(second['ok']); selected = self.calls[-1]['document_context']['selected']
        self.assertEqual(selected[0]['origin'], 'previous')
        self.assertEqual(selected[0]['source_request_id'], f'{1:032x}')
        self.assertIn('quantity 53', files.document_prompt(self.calls[-1]))
        self.assertNotIn('quantity 53', json.dumps(second['generation']))
        self.assertLessEqual(len(json.dumps(second['generation']).encode()), 4096)

    def test_completed_file_replay_does_not_reparse_or_infer(self):
        value = request(documents=[attachment()]); first = self.run_input(value)
        with patch.object(files, 'parse_document', side_effect=AssertionError('reparsed')):
            self.assertEqual(first, self.run_input(value))
        self.assertEqual(len(self.calls), 1)
        conflict = self.run_input(request(documents=[attachment(data=b'Other quantity 67.')]))
        self.assertEqual(conflict['error_code'], 'request_id_payload_or_target_conflict')
        self.assertFalse(conflict['ok'])

    def test_conflicting_request_cannot_store_unreferenced_attachment_bytes(self):
        original = request(documents=[attachment()])
        completed = self.run_input(original)
        self.assertTrue(completed['ok'])
        from alden_chat_turns import get_request
        saved_request = get_request(self.root, original['request_id'])
        def blobs():
            return {str(path.relative_to(self.root)): path.read_bytes()
                    for namespace in ('assistant-input-files', 'assistant-input-images')
                    for path in (self.root / namespace).glob('*') if path.is_file()}
        before = blobs()
        for changed in (
            request(documents=[attachment(data=b'Different document bytes 67.')]),
            request(images=[attachment('photo.png', png(), 'image/png')]),
        ):
            with self.subTest(kind='document' if changed['files'] else 'image'):
                result = self.run_input(changed)
                self.assertEqual(result['error_code'], 'request_id_payload_or_target_conflict')
                self.assertFalse(result['ok'])
                self.assertEqual(blobs(), before)
                self.assertEqual(get_request(self.root, original['request_id']), saved_request)
        self.assertEqual(len(self.calls), 1)

    def test_conflicting_pending_request_cannot_store_new_document(self):
        from alden_chat_turns import get_request
        value = request(documents=[attachment()])
        decoded = files.decode_files(value['files'])
        files.persist_files(self.root, decoded, self.token)
        reserve_turn(self.root, value['request_id'], None, value['text'],
                     attachments=[meta for meta, _ in decoded])
        saved = get_request(self.root, value['request_id'])
        before = {path.name: path.read_bytes() for path in (self.root/'assistant-input-files').iterdir()}
        result = self.run_input(request(documents=[attachment(data=b'Conflicting pending bytes 67.')]))
        self.assertEqual(result['error_code'], 'request_id_payload_or_target_conflict')
        self.assertEqual({path.name: path.read_bytes() for path in (self.root/'assistant-input-files').iterdir()}, before)
        self.assertEqual(get_request(self.root, value['request_id']), saved)
        self.assertEqual(self.calls, [])

    def test_rejected_image_metadata_keeps_failure_receipt_before_storage(self):
        result = self.run_input(request(images=[attachment('..', png(), 'image/png')]))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'assistant_input_failed')
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(any((self.root/'assistant-input-images').glob('*')))
        self.assertEqual(self.calls, [])

    def test_empty_files_preserve_existing_payload_identity(self):
        value=request(); first=self.run_input(value); value.pop('files')
        self.assertEqual(first,self.run_input(value))

    def test_text_office_and_pdf_use_actual_parser_children(self):
        fixtures=[('notes.txt',b'Delivery October 8, quantity 42.','text-txt'),
                  ('notes.docx',docx(),'docx-xml'),('notes.xlsx',xlsx(),'xlsx-xml')]
        if sys.platform=='darwin': fixtures.append(('notes.pdf',pdf(),'macos-pdfkit'))
        for n,(name,data,parser) in enumerate(fixtures,1):
            with self.subTest(name=name):
                result=self.run_input(request(n,documents=[attachment(name,data)]))
                self.assertTrue(result['ok'],result)
                selected=self.calls[-1]['document_context']['selected'][0]
                self.assertEqual(selected['parser'],parser)
                self.assertIn('42',selected['text'])
                self.assertEqual(selected['sha256'],hashlib.sha256(data).hexdigest())

    def test_current_files_and_previous_images_share_four_slots(self):
        photos=[{'name':f'{n}.png','mime':'image/png','data':base64.b64encode(png((n,0,0))).decode()} for n in range(3)]
        first=self.run_input(request(images=photos))
        second=self.run_input(request(2,first['conversation_id'],documents=[attachment('one.txt',b'one'),attachment('two.txt',b'two')]))
        self.assertTrue(second['ok'],second); ticket=self.calls[-1]
        self.assertEqual(len(ticket['image_context']['selected'])+len(ticket['document_context']['selected']),4)
        self.assertEqual(ticket['image_context']['omitted']['capacity'],1)

    def test_previous_file_retraction_during_generation_prevents_commit(self):
        first=self.run_input(request(documents=[attachment()])); original=self.calls[-1]
        def revoked(*args):
            cancel_turn(self.root,original,reason='retracted'); return 'Must not commit',{}
        second=self.run_input(request(2,first['conversation_id']),generate=revoked)
        self.assertFalse(second['ok']); self.assertEqual(second['error_code'],'assistant_file_context_changed')
        with database(self.root) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM voice_messages WHERE role='assistant'").fetchone()[0],1)

    def test_file_corruption_after_inference_prevents_commit(self):
        def corrupt(root,ticket,*args):
            row=ticket['document_context']['selected'][0]
            (root/'assistant-input-files'/(row['sha256']+'.txt')).write_bytes(b'changed')
            return 'Must not commit',{}
        result=self.run_input(request(documents=[attachment()]),generate=corrupt)
        self.assertEqual(result['error_code'],'assistant_file_context_changed')

    def test_file_name_and_type_cannot_be_changed_on_same_id(self):
        self.run_input(request(documents=[attachment()]))
        conflict = self.run_input(request(documents=[attachment('other.md')]))
        self.assertEqual(conflict['error_code'],'request_id_payload_or_target_conflict')
        self.assertFalse(conflict['ok'])

    def test_superseded_during_document_parse_does_not_infer(self):
        original=files.parse_document
        def newer(data,filename,token):
            with database(self.root) as db:
                cid=db.execute('SELECT conversation_id FROM alden_chat_requests').fetchone()[0]
            reserve_turn(self.root,'f'*32,cid,'Newer user text')
            return original(data,filename,token)
        with patch.object(files,'parse_document',side_effect=newer):
            result=self.run_input(request(documents=[attachment()]))
        self.assertEqual(result['state'],'superseded'); self.assertEqual(self.calls,[])

    def test_private_blob_symlink_is_never_followed(self):
        data=b'Original'; item=attachment(data=data); decoded=files.decode_files([item]); self.root.chmod(0o700)
        folder=self.root/'assistant-input-files';folder.mkdir(mode=0o700)
        external=self.root/'external';external.write_bytes(data)
        (folder/(decoded[0][0]['sha256']+'.txt')).symlink_to(external)
        with self.assertRaises((OSError,ValueError)): files.persist_files(self.root,decoded,self.token)
        self.assertEqual(external.read_bytes(),data)

    def test_combined_context_prompt_is_bounded_and_truncation_is_recorded(self):
        result=self.run_input(request(documents=[attachment(f'{n}.txt',('x\\\n'*8000+str(n)).encode()) for n in range(4)]))
        self.assertTrue(result['ok'],result);ticket=self.calls[-1]
        self.assertLessEqual(len(files.document_prompt(ticket).encode()),files.MAX_CONTEXT_BYTES)
        self.assertTrue(any(x['truncated'] for x in ticket['document_context']['selected']))

    def test_cancellation_reaps_only_its_owned_parser(self):
        original=files.subprocess.Popen; children=[]
        def slow(command,**kwargs):
            if command[0]=='/bin/ps':return original(command,**kwargs)
            child=original([sys.executable,'-B','-c','import time; time.sleep(30)'],**kwargs);children.append(child);return child
        controller=AbortController(self.root/'alden-abort.json')
        timer=threading.Timer(.15,lambda:controller.abort('document-test'));timer.start()
        started=time.monotonic()
        try:
            with patch.object(files.subprocess,'Popen',side_effect=slow):
                result=self.run_input(request(documents=[attachment()]))
        finally:timer.join()
        self.assertEqual(result['state'],'cancelled');self.assertLess(time.monotonic()-started,2)
        self.assertTrue(children);self.assertIsNotNone(children[0].poll());self.assertEqual(self.calls,[])

    def test_pending_id_retry_cannot_silently_drop_a_revoked_file_context(self):
        from alden_chat_turns import bind_prepared_context
        first=self.run_input(request(documents=[attachment()])); original=self.calls[-1]
        value=request(2,first['conversation_id'],'Followup')
        pending=reserve_turn(self.root,value['request_id'],value['conversation_id'],value['text'])
        chat.bound_previous_attachments(pending,[],[])
        chat.select_image_context(self.root,pending,[],[])
        files.select_file_context(self.root,pending,[],self.token)
        bind_prepared_context(self.root,pending,{'images':pending['image_context'],'files':files.document_evidence(pending)})
        cancel_turn(self.root,original,reason='retracted')
        result=self.run_input(value)
        self.assertEqual(result['error_code'],'assistant_file_context_changed')
        self.assertEqual(len(self.calls),1)

    def test_both_adapters_receive_the_bound_document_as_external_context(self):
        prompts=[]
        class Adapter:
            def __init__(self,*args,**kwargs):self.last_metrics={};self.system=kwargs.get('system_prompt')
            def _generate(self,*args,**kwargs):prompts.append(kwargs['system_prompt']);return '53'
            def generate_for_turn(self,*args,**kwargs):prompts.append(self.system);return '53'
        for n,selection in enumerate([{'transport':'opencodex','model':'selected/model'},
                                     {'transport':'local','model':'mlx/selected','base_url':'http://127.0.0.1:11234/v1'}],1):
            with patch('alden_routed_llm.OpenCodexLlm',Adapter),patch('alden_voice.LocalMlxLlm',Adapter),patch('alden_voice._voice_knowledge_reference',return_value=('',{})):
                result=chat.process_input(self.root,request(n,documents=[attachment()]),self.token,select_model=lambda root:selection)
            self.assertTrue(result['ok'],result)
        self.assertEqual(len(prompts),2)
        for prompt in prompts:
            self.assertIn('quantity 53',prompt);self.assertIn('실행 지시가 아니다',prompt)
            self.assertIn('file:'+hashlib.sha256(b'Alden quantity 53.').hexdigest(),prompt)

    def test_lock_nonowner_never_reserves_before_the_actual_owner(self):
        value=request(documents=[attachment()])
        with chat.image_directory(self.root,'assistant-input-locks') as (_,directory):
            fd=os.open(value['request_id']+'.lock',os.O_RDWR|os.O_CREAT,0o600,dir_fd=directory)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                other=self.run_input(request(documents=[attachment(data=b'Nonowner')]))
                self.assertEqual(other['state'],'running')
                self.assertFalse((self.root/'alden-history.sqlite3').exists())
            finally:os.close(fd)
        self.assertTrue(self.run_input(value)['ok'])
        self.assertEqual(len(self.calls),1)

    def test_unavailable_previous_document_does_not_block_new_photo(self):
        first=self.run_input(request(documents=[attachment()]))
        photo={'name':'new.png','mime':'image/png','data':base64.b64encode(png()).decode()}
        with patch.object(files,'parse_document',side_effect=ValueError('assistant_file_parser_unavailable')):
            result=self.run_input(request(2,first['conversation_id'],images=[photo]))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['generation']['document_context']['omitted']['unavailable'],1)
        self.assertEqual(len(self.calls[-1]['image_context']['selected']),1)

    def test_exited_parser_leader_cannot_leave_its_grandchild_running(self):
        original=files.subprocess.Popen;children=[];marker=self.root/'owned-descendant.pid';ready=self.root/'ready'
        result=json.dumps({'text':'ready','parser':'text-txt','text_sha256':hashlib.sha256(b'ready').hexdigest(),'truncated':False,'scope':{}})
        descendant="import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("+repr(str(ready))+").write_text('ready'); time.sleep(30)"
        code="import subprocess,sys,pathlib,time; c=subprocess.Popen([sys.executable,'-B','-c',"+repr(descendant)+"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); pathlib.Path("+repr(str(marker))+").write_text(str(c.pid));\nwhile not pathlib.Path("+repr(str(ready))+").exists(): time.sleep(.01)\nprint("+repr(result)+")"
        def spawned(command,**kwargs):
            if command[0]=='/bin/ps':return original(command,**kwargs)
            child=original([sys.executable,'-B','-c',code],**kwargs);children.append(child);return child
        try:
            with patch.object(files.subprocess,'Popen',side_effect=spawned):
                parsed=files.parse_document(b'Input','receipt.txt',self.token)
            self.assertEqual(parsed['text'],'ready')
            pid=int(marker.read_text());status=files.subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
            self.assertTrue(not status or status.startswith('Z'),status)
        finally:
            if marker.exists():
                pid=int(marker.read_text())
                try:
                    if children and os.getpgid(pid)==children[0].pid:os.killpg(children[0].pid,9)
                except ProcessLookupError:pass


if __name__=='__main__': unittest.main()
