"""Original extraction coverage versus displayed/fact excerpts; owned fixtures."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, encoded, normalized_body
from alden_collect import collect_target
import alden_file_set as files
from alden_collection_retrieval import retrieve
from alden_knowledge_mcp import KnowledgeServer
import auto_reply_knowledge_graph as kg


class SourceExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.originals = self.root / 'originals'; self.originals.mkdir()
        self.manifest = self.root / 'files.json'
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='files', original_id='originals', kind='file-set', label='Originals',
            projects=['one'], config={'adapter':'file-set','root':str(self.originals),'path':str(self.manifest)})

    def collect(self, content, name='one.txt'):
        data = content.encode() if isinstance(content, str) else content
        (self.originals / name).write_bytes(data)
        self.manifest.write_bytes(encoded({'schema':1,'files':[{'id':'one','path':name}]}))
        collect_target(self.store, self.target)
        return self.store.graph_page(projects=['one'], limit=10)['nodes'][0]

    def detail(self, node):
        return self.store.graph_page(projects=['one'], focus=node['id'], hops=0, limit=1,
            details=True, expected_version=node['source_version'])['details']

    def test_complete_body_and_response_excerpt_are_separate(self):
        node = self.collect('scopeprobe ' + 'word ' * 2900)
        detail = self.detail(node); packet = detail['source_extraction']
        self.assertTrue(detail['truncated'])
        self.assertTrue(packet['body_complete']); self.assertFalse(packet['parser_truncated'])
        self.assertEqual(packet['status'], 'complete')
        self.assertEqual(packet['binding'], {'document_id':node['id'],'target_id':self.target,
            'version':node['source_version'],'raw_sha256':detail['raw_sha256'],
            'detail_body_sha256':hashlib.sha256(detail['body'].encode()).hexdigest()})
        self.assertEqual(len(detail['body']), 12000)

    def test_parser_partial_is_disclosed_when_response_is_not_cut(self):
        node = self.collect('scopeprobe ' + '가' * 6000)
        detail = self.detail(node); packet = detail['source_extraction']
        self.assertFalse(detail['truncated'])
        self.assertTrue(packet['parser_truncated']); self.assertFalse(packet['body_complete'])
        self.assertEqual(packet['status'], 'partial')
        self.assertLessEqual(packet['extracted_text_bytes'], 16000)
        self.assertLess(len(detail['body']), 12000)

    def test_parser_and_response_can_both_be_cut(self):
        node = self.collect('scopeprobe ' + 'A' * 20000)
        detail = self.detail(node)
        self.assertTrue(detail['truncated']); self.assertTrue(detail['source_extraction']['parser_truncated'])
        self.assertFalse(detail['source_extraction']['body_complete'])

    def test_office_scope_unverified_without_a_parser_budget_cut(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>scopeprobe text</w:t></w:r></w:p></w:body></w:document>')
        detail = self.detail(self.collect(output.getvalue(), 'one.docx'))
        packet = detail['source_extraction']
        self.assertFalse(detail['truncated']); self.assertFalse(packet['parser_truncated'])
        self.assertFalse(packet['body_complete']); self.assertEqual(packet['status'], 'unverified')

    def test_unicode_original_and_normalized_display_have_distinct_hashes(self):
        data = 'scopeprobe e\u0301\r\n  한글 😀\n'.encode()
        detail = self.detail(self.collect(data)); packet = detail['source_extraction']
        self.assertEqual(detail['body'], normalized_body('scopeprobe e\u0301\n  한글 😀'))
        self.assertEqual(packet['input_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(packet['input_bytes'], len(data))
        self.assertNotEqual(packet['extracted_text_sha256'], packet['binding']['detail_body_sha256'])
        self.assertTrue(packet['body_complete'])

    def test_legacy_files_and_other_platforms_do_not_invent_coverage(self):
        for platform in ('files', 'graph'):
            with self.subTest(platform=platform):
                target = self.store.register(platform=platform, original_id='legacy-'+platform, kind='source', label='Legacy', projects=['legacy'])
                self.store.ingest(target, [{'original_id':'legacy','text':'scopeprobe body'}])
                node = self.store.graph_page(projects=['legacy'], platform=platform, limit=1)['nodes'][0]
                detail = self.store.graph_page(projects=['legacy'], platform=platform, focus=node['id'], hops=0,
                    details=True, expected_version=node['source_version'], limit=1)['details']
                self.assertNotIn('source_extraction', detail)

    def test_new_version_and_denied_scope_reject_old_detail(self):
        old = self.collect('scopeprobe first')
        self.collect('scopeprobe second')
        result = self.store.graph_page(projects=['one'], focus=old['id'], hops=0,
            details=True, expected_version=old['source_version'], limit=1)
        self.assertFalse(result['ok']); self.assertNotIn('details', result)
        self.store.register(platform='files', original_id='originals', kind='file-set', label='Denied', projects=['one'],
            permission='denied', config={'adapter':'file-set','root':str(self.originals),'path':str(self.manifest)})
        with self.assertRaisesRegex(ValueError, 'focus_not_in_scope'):
            self.detail(old)

    def record(self):
        node = self.collect('scopeprobe body')
        with self.store.database() as db:
            version = db.execute('SELECT * FROM versions WHERE id=?', (node['source_version'],)).fetchone()
        return node, version['body'], json.loads((self.store.blobs/version['raw_path']).read_bytes())

    def test_malformed_recognized_record_does_not_return_success_shaped_scope(self):
        node, body, raw = self.record()
        changes = [lambda r:r.update(schema=True),lambda r:r.update(input_bytes=True),
            lambda r:r.update(input_bytes=-1),lambda r:r.update(input_sha256='bad'),
            lambda r:r['parsed']['scope'].update(body_complete='true'),
            lambda r:r['parsed'].update(text_sha256='f'*64),
            lambda r:r['parsed']['segments'][0].update(text='forged',text_sha256=hashlib.sha256(b'forged').hexdigest()),
            lambda r:r['parsed']['scope'].update(kind='bad\nkind')]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                changed = copy.deepcopy(raw); change(changed)
                self.store.ingest(self.target, [{'original_id':encoded(['originals','one']).decode(), 'text':body,'raw':changed}],
                    processing_version='declared-original-files-v1')
                current = self.store.graph_page(projects=['one'], limit=1)['nodes'][0]
                with self.assertRaisesRegex(RuntimeError, 'collection_file_extraction_invalid'):
                    self.detail(current)

    def test_packet_is_bounded_fresh_and_rejects_forged_detail_binding(self):
        node, body, raw = self.record(); detail = self.detail(node)
        packet = detail['source_extraction']; binding = packet['binding']
        generated = files.source_extraction_packet(raw, binding=binding, stored_body=body, detail_body=body)
        self.assertLessEqual(len(encoded(generated)),4096)
        generated['binding']['document_id']='changed';self.assertEqual(binding['document_id'],node['id'])
        for changes in ({'detail_body_sha256':'a'*64},{'version':'v\n1'},{'document_id':'X'*257},{'extra':'unexpected'}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError,'collection_file_extraction_invalid'):
                    files.source_extraction_packet(raw,binding={**binding,**changes},stored_body=body,detail_body=body)
        with self.assertRaisesRegex(ValueError,'collection_file_extraction_invalid'):
            files.source_extraction_packet(raw,binding=binding,stored_body=body+'forged',detail_body=body)

    def test_retrieval_and_mcp_keep_partial_coverage_separate_from_fact_excerpt(self):
        node=self.collect('scopeprobe '+ '가'*6000)
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network')), \
                patch.object(kg,'_local_dense_embeddings',side_effect=AssertionError('embedding')):
            result=retrieve(self.root,'scopeprobe',projects=['one'],max_entities=1)
            proof=next(p for p in result['fact_provenance'] if p['fact_type']=='entity')
            self.assertEqual(proof['entity_id'],node['id'])
            self.assertEqual(proof['original_body_coverage']['status'],'partial')
            server=KnowledgeServer(self.root,['one'])
            server.dispatch({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{}}})
            server.dispatch({'jsonrpc':'2.0','method':'notifications/initialized'})
            for index,name,args in [(2,'source',{'node_id':node['id'],'expected_version':node['source_version']}),(3,'search',{'query':'scopeprobe','limit':1})]:
                reply=server.dispatch({'jsonrpc':'2.0','id':index,'method':'tools/call','params':{'name':'alden_knowledge_'+name,'arguments':{'projects':['one'],**args}}})
                self.assertFalse(reply['result']['isError'],reply)
                data=json.loads(reply['result']['content'][0]['text'])
                coverage=data['details']['source_extraction'] if name=='source' else data['fact_provenance'][0]['original_body_coverage']
                self.assertEqual(coverage['status'],'partial');self.assertFalse(coverage['body_complete'])


class SourceExtractionTypedTurnTests(unittest.TestCase):
    def test_original_coverage_reaches_actual_tool_role_model_input(self):
        from tests.test_alden_knowledge_turn import KnowledgeTurnTests
        fixture = KnowledgeTurnTests('test_actual_read_nonce_role_trace_one_commit_and_replay')
        fixture.setUp()
        try:
            originals = fixture.root / 'originals'; originals.mkdir()
            (originals/'one.txt').write_text('sourcepartial '+fixture.nonce+'\n'+'가'*6000)
            manifest=fixture.root/'files.json';manifest.write_bytes(encoded({'schema':1,'files':[{'id':'one','path':'one.txt'}]}))
            target=fixture.store.register(platform='files',original_id='typed-originals',kind='file-set',label='Typed originals',
                projects=['tool-project'],config={'adapter':'file-set','root':str(originals),'path':str(manifest)})
            collect_target(fixture.store,target)
            observed=[]
            def inspect(payload,hop):
                if hop == 2:
                    content=json.loads(payload['messages'][-1]['content'])['reference']
                    quoted=content.split('<quoted_local_history>',1)[1].split('</quoted_local_history>',1)[0]
                    context=json.loads(quoted)
                    proof=next(p for p in context['graph_provenance'] if p.get('platform')=='files')
                    observed.append(proof.get('original_body_coverage'))
                return None
            fixture.behavior=inspect
            result=fixture.run_input(value={**fixture.value,'text':'tool-project 지식에서 sourcepartial 자료를 찾아줘'})
            self.assertTrue(result['ok'],result);self.assertEqual(result['reply'],fixture.nonce)
            self.assertEqual(len(observed),1)
            self.assertIsNotNone(observed[0],'original extraction coverage was dropped before the model tool-role payload')
            self.assertEqual(observed[0]['status'],'partial');self.assertFalse(observed[0]['body_complete'])
            self.assertEqual((len(fixture.payloads),fixture.reads),(2,1))
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
