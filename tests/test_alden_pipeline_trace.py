"""Source-to-index-to-history-to-graph readback, without a live external host.

Uses the genuine collection writer, exact hashed source files, SQLite FTS,
real retrieval cache and owned read-only MCP. No LLM or network requests.
"""
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, digest, encoded, identity, read_action
from alden_collection_retrieval import index_dense, retrieve, trace_document
from alden_knowledge_mcp import KnowledgeServer
from alden_status_mcp import CallControl
import auto_reply_knowledge_graph as kg


class PipelineReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='youtube', original_id='channel-public',
                                          kind='channel', label='채널', projects=['research'])
        self.other = self.store.register(platform='youtube', original_id='channel-private',
                                         kind='channel', label='비공개', projects=['private'],
                                         permission='denied')
        self.first = identity('youtube','video-a')
        self.second = identity('youtube','video-b')
        self.a = {'platform':'youtube','original_id':'video-a','kind':'video','label':'원문 A',
                  'text':'정확한 채널 실험과 근거', 'raw':{'videoId':'video-a','text':'정확한 채널 실험과 근거'}}
        self.b = {'platform':'youtube','original_id':'video-b','kind':'video','label':'원문 B',
                  'text':'관계 있는 별도 자료', 'raw':{'videoId':'video-b','text':'관계 있는 별도 자료'}}
        self.relation = {'source_platform':'youtube','source_id':'video-a',
                         'target_platform':'youtube','target_id':'video-b',
                         'type':'cites','evidence':{'declared':'original relationship'}}

    def ingest(self, records=None, *, snapshot=False, order=1, relations=None):
        records = [self.a,self.b] if records is None else records
        relations = [self.relation] if relations is None else relations
        options = {'origin':'test-pipeline', 'relations':relations}
        if snapshot:
            options.update(complete_snapshot=True, source_revision=digest(encoded([records,relations])),
                           source_order=order, cursor={'revision':order})
        return self.store.ingest(self.target,records,**options)

    def version(self):
        page = self.store.graph_page(projects=['research'])
        return next(n['source_version'] for n in page['nodes'] if n['id']==self.first)

    def test_full_collection_mcp_ft_s_graph_and_journal_consistency(self):
        result = self.ingest(snapshot=True)
        version = self.version()
        trace = trace_document(self.root,self.first,projects=['research'],expected_version=version,
                               target_id=self.target)
        self.assertEqual(result['state'],'complete')
        self.assertEqual(trace['state'],'available')
        self.assertEqual(trace['version'],version)
        self.assertEqual(trace['run_id'],result['run_id'])
        self.assertEqual(trace['source']['state'],'hash_verified')
        self.assertEqual(trace['fts']['state'],'verified')
        self.assertEqual(trace['dense']['state'],'not_indexed')
        self.assertEqual(trace['graph']['state'],'eligible')
        self.assertEqual(trace['graph']['selected_target_relations'],1)
        self.assertTrue(all(trace['stages'].values()))
        self.assertEqual(trace['activity_checkpoint'],
                         self.store.graph_page(projects=['research'],target_id=self.target)['activity_checkpoint'])
        history=read_action(self.root,'collection-history',json.dumps({'projects':['research'],'limit':100}))
        self.assertTrue(any(row['event_id']==trace['last_stored_event']['event_id'] for row in history['items']))
        details=read_action(self.root,'collection-graph',json.dumps({
            'projects':['research'],'focus':self.first,'hops':0,'limit':120,'details':True,
            'expected_version':version,'target_id':self.target}))
        self.assertEqual(details['details']['node_id'],self.first)
        self.assertEqual(details['pipeline_trace']['last_stored_event'],trace['last_stored_event'])
        self.assertEqual(self.store.search('정확한',projects=['research'])[0]['id'],self.first)
        server=KnowledgeServer(self.root,['research'])
        call={'name':'alden_knowledge_trace','arguments':{'projects':['research'],
             'document_id':self.first,'expected_version':version,'target_id':self.target}}
        self.assertTrue(server.valid_call(call))
        saved=server.call_tool(call,CallControl(timeout=4))
        self.assertEqual(saved['source'],trace['source'])
        self.assertEqual(saved['activity_checkpoint'],trace['activity_checkpoint'])
        self.assertEqual(saved['last_stored_event'],trace['last_stored_event'])

    def test_local_dense_vector_is_separate_and_hash_bound_to_actual_source_version(self):
        self.ingest()
        initial=trace_document(self.root,self.first,projects=['research'])
        self.assertEqual(initial['dense']['state'],'not_indexed')
        # A fake profile is scoped to the test. The CI runner has no local
        # embedding service and must not access the network for the fixture.
        with patch.object(kg,'_active_dense_embedding_model',return_value='fixed-test-encoder'):
            indexed=index_dense(self.root,['research'],embed=lambda texts:[[1.0,0.0,0.0] for _ in texts])
            self.assertEqual(indexed['state'],'ready')
            trace=trace_document(self.root,self.first,projects=['research'])
            self.assertEqual(trace['dense']['state'],'stored_vector_binding_verified')
            self.assertFalse(trace['dense']['model_residency_verified'])
            answer=retrieve(self.root,'정확한 근거',projects=['research'],query_embed=lambda text:[1.,0.,0.])
        self.assertEqual(answer['search_mode'],'rrf')
        self.assertIn(self.first,answer['candidates'])
        self.assertTrue(any(proof.get('source_version')==trace['version']
                            for proof in answer['fact_provenance']))
        with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
            db.execute('UPDATE vectors SET vector=? WHERE document_id=?',(b'\x00\x01',self.first))
        degraded=trace_document(self.root,self.first,projects=['research'])
        self.assertEqual(degraded['dense']['state'],'vector_binding_invalid')
        self.assertEqual(degraded['fts']['state'],'verified')

    def test_scope_isolation_revisions_and_mismatched_selection(self):
        self.ingest(records=[self.a],relations=[])
        self.store.ingest(self.other,[{**self.a,'text':'private canary and unrelated details'}])
        allowed=trace_document(self.root,self.first,projects=['research'])
        self.assertEqual(allowed['state'],'available')
        self.assertEqual(allowed['target_id'],self.target)
        self.assertEqual(allowed['fts']['state'],'verified')
        self.assertEqual(trace_document(self.root,self.first,projects=['private'])['state'],'not_in_scope')
        self.assertEqual(trace_document(self.root,self.second,projects=['research'])['state'],'not_in_scope')
        stale=trace_document(self.root,self.first,projects=['research'],
                             expected_version=identity('version','outdated'))
        self.assertEqual(stale,{'ok':False,'state':'version_changed','document_id':self.first})
        with self.assertRaisesRegex(ValueError,'project_scope_required'):
            trace_document(self.root,self.first,projects=[])
        with self.assertRaisesRegex(ValueError,'document_id_invalid'):
            trace_document(self.root,'another_scope',projects=['research'])
        server=KnowledgeServer(self.root,['research'])
        self.assertFalse(server.valid_call({'name':'alden_knowledge_trace',
                         'arguments':{'projects':['private'],'document_id':self.first}}))
        self.assertFalse(server.valid_call({'name':'alden_knowledge_trace',
                         'arguments':{'projects':['research'],'document_id':self.first,'limit':5}}))
        self.assertNotIn('private canary',json.dumps(allowed,ensure_ascii=False))

    def test_committed_removal_retracts_node_and_edge_but_retains_previous_raw_proof(self):
        self.ingest(snapshot=True)
        version=self.version()
        baseline=self.store.activity_page(projects=['research'])
        changed=self.ingest(records=[],relations=[],snapshot=True,order=2)
        self.assertEqual(changed['removed'],2)
        trace=trace_document(self.root,self.first,projects=['research'],expected_version=version)
        self.assertEqual(trace['state'],'removed')
        self.assertEqual(trace['source']['state'],'hash_verified')
        self.assertEqual(trace['stored_change'],'removed')
        self.assertFalse(trace['stages']['indexed'])
        self.assertTrue(trace['stages']['stored'])
        self.assertEqual(trace['graph'],{'state':'retracted','selected_target_relations':0,
                                        'displayed_in_client':False})
        self.assertEqual(self.store.graph_page(projects=['research'])['nodes'],[])
        self.assertEqual(self.store.search('정확한',projects=['research']),[])
        self.assertFalse(retrieve(self.root,'정확한',projects=['research'])['facts'])
        changes=self.store.activity_page(projects=['research'],after=baseline['cursor'],
                                         stream_id=baseline['stream_id'])
        self.assertEqual({r['kind'] for r in changes['items']},{'removed','relations_changed'})
        self.assertEqual(self.store.recent_events(projects=['private'])['items'],[])
        restored=self.ingest(snapshot=True,order=3)
        back=trace_document(self.root,self.first,projects=['research'])
        self.assertEqual(restored['state'],'complete')
        self.assertEqual(back['state'],'available')
        self.assertEqual(back['graph']['selected_target_relations'],1)

    def test_corrupt_raw_does_not_produce_a_false_success_receipt(self):
        self.ingest(records=[self.a],relations=[])
        trace=trace_document(self.root,self.first,projects=['research'])
        self.assertEqual(trace['source']['state'],'hash_verified')
        with self.store.database() as db:
            file=db.execute('SELECT raw_path FROM versions WHERE id=?',(trace['version'],)).fetchone()[0]
        (self.store.blobs/file).write_text('{"tampered":true}')
        with self.assertRaisesRegex(RuntimeError,'collection_source_integrity'):
            trace_document(self.root,self.first,projects=['research'])
        with self.assertRaisesRegex(RuntimeError,'collection_source_integrity'):
            read_action(self.root,'collection-graph',json.dumps({'projects':['research'],
                'focus':self.first,'hops':0,'details':True}))


if __name__=='__main__':
    unittest.main()
