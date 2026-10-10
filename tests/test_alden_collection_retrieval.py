"""Collection source/version scopes, genuine hybrid mode and bounded MCP contracts."""
import io
import json
from pathlib import Path
import sys
import select
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, identity
from alden_collection_retrieval import index_dense, retrieve
from alden_knowledge_mcp import KnowledgeServer, CallToken
from alden_status_mcp import CallControl, StdioServer
import auto_reply_knowledge_graph as kg


class CollectionRetrievalTests(unittest.TestCase):
    def test_latest_source_uses_publication_not_collection_time_and_keeps_history(self):
        self.store.ingest(self.allowed,[{'original_id':'old','label':'shared term','text':'older terms','published_at':'2026-08-01T00:00:00Z'}])
        self.store.ingest(self.allowed,[{'original_id':'new','label':'shared term','text':'recent terms','published_at':'2026-09-01T00:00:00Z'}])
        result=retrieve(self.root,'shared term latest',projects=['one'])
        self.assertEqual([p['entity_id'] for p in result['fact_provenance']],[identity('graph','new')])
        self.assertEqual(result['temporal_selection']['observation_basis'],['source_published_at'])
        self.assertIn('효력의 증명은 아님',result['facts'][0])
        history=retrieve(self.root,'shared term 이전 기록',projects=['one'])
        self.assertEqual(len(history['fact_provenance']),2)
        with self.store.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],2)
    def setUp(self):
        self.temp = TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve();self.store = CollectionStore(self.root)
        self.allowed = self.store.register(platform='graph', original_id='allowed', kind='source', label='allowed', projects=['one'])
        self.other = self.store.register(platform='graph', original_id='other', kind='source', label='other', projects=['two'])

    def add(self, target, original, text):
        self.store.ingest(target, [{'platform': 'graph', 'original_id': original, 'label': 'shared term', 'text': text}])
        return identity('graph', original)

    def embed(self, texts):
        return [[1., 0.] if 'original' in text else [0., 1.] for text in texts]

    def profile(self):
        return patch.object(kg, '_active_dense_embedding_model', return_value='fixed-test-encoder')

    def test_exact_target_graph_rag_fences_revisions_relations_and_mcp_scope(self):
        # Two authorized sources may own the same original ID and versions
        # inside one project. Filtering happens before selecting one version.
        second=self.store.register(platform='graph',original_id='another-source',
                                   kind='source',label='another',projects=['one'])
        shared=self.add(self.allowed,'same','first source facts only')
        self.add(second,'same','second source facts only')
        self.store.ingest(second,[{'platform':'graph','original_id':'second-only',
                                  'label':'unique adjacency','text':'other document'}])
        relations=[{'source_platform':'graph','source_id':'same',
                    'target_platform':'graph','target_id':'second-only',
                    'type':'cites','evidence':{'explicit':True}}]
        self.store.ingest(second,[],relations=relations)
        base=retrieve(self.root,'shared',projects=['one'],target_id=self.allowed)
        selected=retrieve(self.root,'shared',projects=['one'],target_id=second)
        self.assertEqual(base['target_id'],self.allowed)
        self.assertEqual(base['facts'],['shared term: first source facts only'])
        self.assertEqual(base['eligible_documents'],1)
        self.assertEqual(base['fact_provenance'][0]['source_target'],self.allowed)
        self.assertEqual(selected['fact_provenance'][0]['source_target'],second)
        self.assertIn('second source facts only',selected['facts'][0])
        self.assertEqual(len([p for p in base['fact_provenance'] if p['fact_type']=='relation']),0)
        self.assertEqual(len([p for p in selected['fact_provenance'] if p['fact_type']=='relation']),1)
        self.assertNotEqual(base['fact_provenance'][0]['source_version'],
                            selected['fact_provenance'][0]['source_version'])
        self.assertEqual(self.store.search('shared',projects=['one'],
                                           target_id=self.allowed)[0]['body'],'first source facts only')
        self.assertEqual(self.store.search('shared',projects=['one'],
                                           target_id=second)[0]['body'],'second source facts only')
        out=retrieve(self.root,'shared',projects=['one'],target_id=self.other)
        self.assertEqual(out['facts'],[])
        self.assertEqual(out['eligible_documents'],0)
        with self.assertRaisesRegex(ValueError,'target_scope_invalid'):
            retrieve(self.root,'shared',projects=['one'],target_id=True)
        mcp=KnowledgeServer(self.root,['one'])
        selection={'name':'alden_knowledge_search','arguments':
                   {'projects':['one'],'query':'shared','target_id':self.allowed}}
        self.assertTrue(mcp.valid_call(selection))
        observed=mcp.call_tool(selection,CallControl(timeout=5))
        self.assertEqual(observed['fact_provenance'][0]['source_target'],self.allowed)
        for invalid in (None,7,'','x'*257):
            if invalid is None:
                continue
            self.assertFalse(mcp.valid_call({'name':'alden_knowledge_search','arguments':
                             {'projects':['one'],'query':'shared','target_id':invalid}}))

    def test_visible_version_is_project_specific_and_hash_verified(self):
        self.add(self.allowed, 'same', 'original body');self.add(self.other, 'same', 'private other body')
        result = retrieve(self.root, 'shared', projects=['one'])
        self.assertEqual(result['facts'], ['shared term: original body'])
        proof = result['fact_provenance'][0]
        self.assertEqual(proof['projects'], ['one']);self.assertTrue(proof['provenance_valid'])
        with self.store.database() as db:
            path = db.execute('SELECT raw_path FROM versions WHERE id=?', (proof['source_version'],)).fetchone()[0]
        (self.store.blobs / path).write_text('corrupt')
        with self.assertRaisesRegex(RuntimeError, 'source_integrity'):
            retrieve(self.root, 'shared', projects=['one'])

    def test_full_scope_vectors_required_for_rrf_and_old_revision_is_never_reused(self):
        self.add(self.allowed, 'a', 'original');self.add(self.allowed, 'b', 'another')
        with self.profile():
            result = index_dense(self.root, ['one'], embed=self.embed)
            self.assertEqual(result['documents'], 2);self.assertEqual(result['embedded_changed'], 2)
            self.assertEqual(index_dense(self.root, ['one'], embed=self.embed)['embedded_changed'], 0)
            ranked = retrieve(self.root, 'semantic only', projects=['one'], query_embed=lambda _: [1.,0.])
            self.assertEqual(ranked['search_mode'], 'rrf');self.assertIn('original', ranked['facts'][0])
            self.add(self.allowed, 'a', 'new revision')
            pending = retrieve(self.root, 'shared', projects=['one'], query_embed=lambda _: self.fail('stale vectors must not embed the query'))
            self.assertEqual(pending['search_mode'], 'bm25_only');self.assertIn('new revision', '\n'.join(pending['facts']))

    def test_index_batches_across_documents_and_cancelled_partial_work_resumes(self):
        for n in range(17):self.add(self.allowed, str(n), 'original')
        calls=[]
        def embed(texts):calls.append(len(texts));return self.embed(texts)
        with self.profile():
            report=index_dense(self.root,['one'],embed=embed)
        self.assertEqual(calls,[8,8,1]);self.assertEqual(report['embedded_changed'],17)
        self.add(self.allowed, 'new', 'original')
        with self.profile(), self.assertRaisesRegex(RuntimeError,'cancelled'):
            index_dense(self.root,['one'],embed=embed,cancelled=lambda:True)
        with self.profile():self.assertEqual(index_dense(self.root,['one'],embed=embed)['embedded_changed'],1)

    def test_processing_revision_reuses_only_exact_verified_text_and_encoder(self):
        records=[{'platform':'graph','original_id':'a','label':'shared term','text':'original'}]
        self.store.ingest(self.allowed,records,processing_version='parser-one')
        with self.profile():
            self.assertEqual(index_dense(self.root,['one'],embed=self.embed)['embedded_changed'],1)
            self.store.ingest(self.allowed,records,processing_version='parser-two')
            report=index_dense(self.root,['one'],embed=lambda _:self.fail('unchanged input must reuse its vector'))
            self.assertEqual(report['reused_versions'],1);self.assertEqual(report['embedded_changed'],0)
            result=retrieve(self.root,'shared',projects=['one'],query_embed=lambda _:[1.,0.])
            self.assertEqual(result['search_mode'],'rrf');self.assertIn('original',result['facts'][0])
            self.store.ingest(self.allowed,[{**records[0],'text':'changed content'}],processing_version='parser-three')
            self.assertEqual(index_dense(self.root,['one'],embed=self.embed)['embedded_changed'],1)
        with patch.object(kg,'_active_dense_embedding_model',return_value='different-encoder'):
            self.assertEqual(index_dense(self.root,['one'],embed=self.embed)['embedded_changed'],1)

    def test_mcp_scope_and_unknown_arguments_cannot_expand_startup_authority(self):
        self.add(self.allowed,'a','original');server=KnowledgeServer(self.root,['one'])
        for args in [{'projects':['two'],'query':'shared'}, {'projects':[],'query':'shared'},
                     {'projects':['one'],'query':'shared','state_root':'/outside'},
                     {'projects':['one'],'query':'shared','limit':True}]:
            self.assertFalse(server.valid_call({'name':'alden_knowledge_search','arguments':args}))
        self.assertFalse(server.valid_call({'name':[],'arguments':{}}))
        frames=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{}}},
                {'jsonrpc':'2.0','method':'notifications/initialized'},
                {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'alden_knowledge_search','arguments':{'projects':['one'],'query':'shared'}}}]
        script = Path(__file__).resolve().parents[1] / 'scripts/alden_knowledge_mcp.py'
        child = subprocess.Popen([sys.executable, '-B', str(script), '--state-root', str(self.root), '--allow-project', 'one'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            values = []
            for frame in frames:
                child.stdin.write(json.dumps(frame).encode() + b'\n');child.stdin.flush()
                if 'id' in frame:
                    self.assertTrue(select.select([child.stdout], [], [], 4)[0], 'owned MCP child did not reply')
                    values.append(json.loads(child.stdout.readline()))
            reply = next(x for x in values if x.get('id') == 2)
        finally:
            child.stdin.close()
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:child.kill();child.wait()
            child.stdout.close();child.stderr.close()
        self.assertFalse(reply['result']['isError']);data=json.loads(reply['result']['content'][0]['text'])
        self.assertEqual(data['facts'],['shared term: original'])

    def test_status_default_catalog_still_exposes_no_knowledge(self):
        self.assertEqual([t['name'] for t in StdioServer(None).tool_catalog()],['alden_status'])
        self.assertFalse(StdioServer(None).valid_call({'name':'alden_knowledge_search','arguments':{}}))

    def test_individual_mcp_cancellation_does_not_latch_global_abort(self):
        control=CallControl();token=CallToken(self.root/'alden-abort.json',control)
        self.assertFalse(token.is_cancelled());control.stop();self.assertTrue(token.is_cancelled())
        self.assertFalse((self.root/'alden-abort.json').exists())

    def test_explicit_project_bundle_never_accepts_a_kakao_room_scope(self):
        with self.assertRaisesRegex(ValueError,'local_project_scope_only'):
            kg.retrieve_knowledge_bundle('shared',state_root=self.root,projects=['one'],chat_id='123')

    def test_voice_project_request_keeps_collection_version_proof(self):
        self.add(self.allowed, 'a', 'original')
        from alden_abort import AbortController
        from alden_voice import _voice_knowledge_reference
        reference, metrics = _voice_knowledge_reference('one 지식 shared 찾아줘', [], self.root, AbortController(self.root).token())
        self.assertEqual(metrics['state'], 'found')
        proof = metrics['graph_provenance'][0]
        self.assertEqual(proof['projects'], ['one']);self.assertTrue(proof['source_version'])
        self.assertIn('original', reference)

    def test_index_of_multiple_projects_retains_each_authorized_version(self):
        self.add(self.allowed, 'same', 'original');self.add(self.other, 'same', 'private other')
        with self.profile():
            result=index_dense(self.root,['one','two'],embed=self.embed)
            self.assertEqual(result['documents'],2)
            first=retrieve(self.root,'shared',projects=['one'],query_embed=lambda _: [1.,0.])
            second=retrieve(self.root,'shared',projects=['two'],query_embed=lambda _: [0.,1.])
        self.assertEqual(first['search_mode'],'rrf');self.assertEqual(second['search_mode'],'rrf')
        self.assertIn('original',first['facts'][0]);self.assertIn('private other',second['facts'][0])

    def test_collection_time_filter_applies_before_candidate_limit(self):
        self.add(self.allowed,'old','original');self.add(self.allowed,'new','newer')
        with self.store.database() as db:
            db.execute('UPDATE versions SET collected_at=CASE WHEN document_id=? THEN 10 ELSE 20 END', (identity('graph','old'),))
        result=retrieve(self.root,'shared',projects=['one'],time_from=0,time_to=15,candidate_limit=1)
        self.assertEqual(result['facts'],['shared term: original']);self.assertEqual(result['time_basis'],'collection_time')
        with self.assertRaisesRegex(ValueError,'time_filter_invalid'):
            retrieve(self.root,'shared',projects=['one'],time_from=float('nan'))

    def test_empty_source_text_is_retained_but_never_invented_for_embedding(self):
        self.store.ingest(self.allowed,[{'original_id':'blank','label':'','text':''}])
        with self.profile():
            result=index_dense(self.root,['one'],embed=lambda _:self.fail('empty source must not be embedded'))
        self.assertEqual(result['source_versions'],1);self.assertEqual(result['unsearchable_versions'],1)
        self.assertEqual(result['documents'],0)
        self.assertEqual(retrieve(self.root,'anything',projects=['one'])['facts'],[])
        with self.store.database() as db:self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0],1)

    def test_retained_thread_text_is_searchable_without_rewriting_source_versions(self):
        record={'platform':'graph','original_id':'post','label':'','text':'',
                'raw':{'localOriginalText':'retained source text','author':'actual source author'}}
        self.store.ingest(self.allowed,[record])
        with self.store.database() as db:before=tuple(db.execute('SELECT id,raw_sha256,label,body FROM versions').fetchone())
        with self.profile():
            report=index_dense(self.root,['one'],embed=self.embed)
            result=retrieve(self.root,'retained',projects=['one'],query_embed=lambda _: [0.,1.])
        self.assertEqual(report['documents'],1);self.assertEqual(report['unsearchable_versions'],0)
        self.assertEqual(result['facts'],['retained source text'])
        proof=result['fact_provenance'][0]
        self.assertEqual(proof['source_version'],before[0]);self.assertEqual(proof['note_hash'],before[1])
        self.assertEqual(proof['body_source'],'retained_record.localOriginalText')
        self.assertEqual(proof['source_author']['author'],'actual source author')
        with self.store.database() as db:self.assertEqual(tuple(db.execute('SELECT id,raw_sha256,label,body FROM versions').fetchone()),before)


if __name__=='__main__':unittest.main()
