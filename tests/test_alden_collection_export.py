"""Complete source-record export, independent of display caps and aliases."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from alden_collection import CollectionStore,identity,read_action,_RelationViewProof
import alden_collection_export as export
from alden_collection_export import ExportError,export_graph,verify_export,verify_current_export


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=self.base/'source';self.root.mkdir(mode=0o700)
        self.store=CollectionStore(self.root)
        self.a=self.store.register(platform='graph',original_id='a',kind='source',label='Same publisher',projects=['one'])
        self.b=self.store.register(platform='graph',original_id='b',kind='source',label='Same publisher',projects=['one'])
        self.outside=self.store.register(platform='graph',original_id='outside',kind='source',label='OUTSIDE_CANARY',projects=['two'])
        self.store.ingest(self.a,[{'original_id':k,'label':'Same label '+k,'text':'PRIVATE_BODY_'+k} for k in ('a','b','c')],
            relations=[self.relation('a','b'),self.relation('b','c')])
        self.store.ingest(self.b,[{'original_id':k,'label':'Same label '+k,'text':'PRIVATE_BODY_'+k} for k in ('a','b')],
                          relations=[self.relation('a','b')])
        self.store.ingest(self.outside,[{'original_id':'secret','label':'OUTSIDE_LABEL','text':'OUTSIDE_BODY'}],
                          relations=[self.relation('a','b')])
        self.output=self.base/'archive'

    @staticmethod
    def relation(source,target,**extra):
        return {'source_platform':'graph','source_id':source,'target_platform':'graph','target_id':target,
                'type':'reference','evidence':{'kind':'synthetic export fixture'},**extra}

    def records(self,table):
        with sqlite3.connect(self.output/'graph.sqlite3') as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM '+table+' ORDER BY id')]

    def test_complete_source_identity_context_and_group_proof_match_the_canonical_view(self):
        before=self.store.path.read_bytes();receipt=export_graph(self.root,['one'],self.output)
        self.assertEqual(receipt['counts'],{'targets':2,'nodes':3,'edges':2,'claim_records':3})
        self.assertTrue(receipt['checkpoint_unchanged_at_completion'])
        nodes,edges=self.records('nodes'),self.records('edges')
        graph=read_action(self.root,'collection-graph',json.dumps({'projects':['one'],'limit':50}))
        for node in nodes:
            expected=next(n for n in graph['nodes'] if n['id']==node['id'])
            self.assertEqual(node,{k:expected[k] for k in node})
        for edge in edges:
            expected=next(e for e in graph['edges'] if e['id']==edge['id'])
            self.assertEqual(edge,{k:expected[k] for k in edge})
        self.assertEqual(verify_export(self.output),receipt)
        self.assertEqual(verify_current_export(self.root,['one'],self.output),receipt)
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertNotIn('PRIVATE_BODY',json.dumps(nodes+edges))
        self.assertNotIn('OUTSIDE',json.dumps(self.records('targets')+nodes+edges))

    def test_export_crosses_visual_node_and_edge_limits_without_dropping_cross_page_edges(self):
        records=[{'original_id':'big-'+str(i),'label':'Record '+str(i),'text':'fixture body'} for i in range(600)]
        links=[self.relation('big-'+str(i%600),'big-'+str((i+1+i//600)%600)) for i in range(700)]
        self.store.ingest(self.a,records,relations=links)
        receipt=export_graph(self.root,['one'],self.output)
        self.assertEqual(receipt['counts'],{'targets':2,'nodes':603,'edges':702,'claim_records':703})
        self.assertEqual({n['id'] for n in self.records('nodes')},
                         {identity('graph',k) for k in ['a','b','c']+['big-'+str(i) for i in range(600)]})
        actual={(e['source'],e['target']) for e in self.records('edges')}
        self.assertTrue({(identity('graph',e['source_id']),identity('graph',e['target_id'])) for e in links}<=actual)
        self.assertEqual(len(read_action(self.root,'collection-graph',json.dumps({'projects':['one'],'limit':120}))['nodes']),120)
        verify_current_export(self.root,['one'],self.output)

    def test_many_claims_keep_all_evidence_in_revision_with_a_bounded_sample(self):
        for i in range(24):self.store.ingest(self.a,[],relations=[self.relation('a','b',original_id='claim-'+str(i),evidence={'claim':i})])
        export_graph(self.root,['one'],self.output)
        edge=next(e for e in self.records('edges') if e['source']==identity('graph','a'))
        proof=_RelationViewProof()
        with self.store.database() as db:
            claims=db.execute('SELECT id,version FROM relations WHERE active=1 AND source=? AND target=? AND target_id IN (?,?)',
                              (identity('graph','a'),identity('graph','b'),self.a,self.b)).fetchall()
        for row in reversed(claims):proof.step(row['id'],row['version'])
        expected=json.loads(proof.finalize())
        self.assertEqual(edge['reference_group']['revision'],expected['revision'])
        self.assertEqual(edge['reference_group']['count'],len(claims))
        self.assertEqual(edge['evidence']['source_event_ids'],expected['ids'])
        self.assertEqual(len(expected['ids']),16)

    def test_corrupt_relation_history_fails_instead_of_exporting_a_smaller_graph(self):
        with self.store.database() as db:db.execute('UPDATE relations SET evidence=? WHERE target_id=?',('{"tampered":true}',self.a))
        before=self.store.path.read_bytes()
        with self.assertRaisesRegex(ExportError,'relation_integrity'):export_graph(self.root,['one'],self.output)
        self.assertFalse(self.output.exists());self.assertEqual(self.store.path.read_bytes(),before)

    def test_budget_failure_never_publishes_a_partial_archive(self):
        with self.assertRaisesRegex(ExportError,'record_budget'):
            export_graph(self.root,['one'],self.output,max_records=5)
        self.assertFalse(self.output.exists())
        with patch.object(export,'MAX_ROW_BYTES',128), self.assertRaisesRegex(ExportError,'row_budget'):
            export_graph(self.root,['one'],self.output)
        self.assertFalse(self.output.exists())

    def test_cancelled_scan_and_bad_factory_never_create_accepted_output(self):
        calls=[0]
        def cancelled():calls[0]+=1;return calls[0]>25
        with self.assertRaises(Exception):export_graph(self.root,['one'],self.output,cancelled=cancelled)
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ExportError,'readonly_source_required'):
            export_graph(self.root,['one'],self.output,store_factory=lambda:self.store)
        self.assertFalse(self.output.exists())

    def test_existing_output_and_foreign_scope_are_not_overwritten(self):
        receipt=export_graph(self.root,['one'],self.output)
        with self.assertRaisesRegex(ExportError,'new_output_required'):export_graph(self.root,['one'],self.output)
        self.assertEqual(verify_export(self.output),receipt)
        with self.assertRaisesRegex(ExportError,'scope_mismatch'):verify_current_export(self.root,['two'],self.output)
        with self.assertRaisesRegex(ValueError,'project_scope_invalid'):
            export_graph(self.root,['missing'],self.base/'wrong')
        self.assertFalse((self.base/'wrong').exists())

    def test_scope_revocation_during_export_removes_unpublished_output(self):
        original=export._records
        def changing(db,store,projects,check):
            expected,rows=original(db,store,projects,check)
            def iterate():
                changed=False
                for table,value in rows:
                    yield table,value
                    if table=='nodes' and not changed:
                        changed=True
                        self.store.register(platform='graph',original_id='a',kind='source',label='Same publisher',projects=['one'],permission='denied')
            return expected,iterate()
        with patch.object(export,'_records',side_effect=changing):
            with self.assertRaisesRegex(ExportError,'source_changed'):export_graph(self.root,['one'],self.output)
        self.assertFalse(self.output.exists())

    def test_content_update_is_a_consistent_as_of_snapshot_not_latest_state(self):
        original=export._records
        def changing(db,store,projects,check):
            expected,rows=original(db,store,projects,check)
            def iterate():
                changed=False
                for table,value in rows:
                    yield table,value
                    if table=='nodes' and not changed:
                        changed=True;self.store.ingest(self.a,[{'original_id':'late','label':'Late record','text':'later'}])
            return expected,iterate()
        with patch.object(export,'_records',side_effect=changing):receipt=export_graph(self.root,['one'],self.output)
        self.assertEqual(receipt['counts']['nodes'],3)
        self.assertNotIn(identity('graph','late'),{n['id'] for n in self.records('nodes')})
        self.assertFalse(receipt['checkpoint_unchanged_at_completion'])
        with self.assertRaisesRegex(ExportError,'stale'):verify_current_export(self.root,['one'],self.output)
        self.assertEqual(verify_export(self.output),receipt)

    def test_native_missing_authority_or_withheld_records_are_not_an_empty_complete_snapshot(self):
        native=self.store.register(platform='kakaotalk',original_id='native-fixture',kind='room',label='Native fixture',projects=['native'])
        self.store.ingest(native,[{'original_id':'1','label':'Scoped source','text':'source fixture'}])
        with self.assertRaisesRegex(ExportError,'authority_unavailable'):export_graph(self.root,['native'],self.output)
        factory=lambda:CollectionStore.open_existing(self.root,source_access=lambda *args:False,source_revision=lambda _: 'a'*64)
        with self.assertRaisesRegex(ExportError,'source_withheld'):
            export_graph(self.root,['native'],self.output,store_factory=factory)
        self.assertFalse(self.output.exists())

    def test_permitted_empty_project_is_a_verified_empty_archive(self):
        self.store.register(platform='graph',original_id='empty',kind='source',label='Empty',projects=['empty'])
        receipt=export_graph(self.root,['empty'],self.output)
        self.assertEqual(receipt['counts'],{'targets':1,'nodes':0,'edges':0,'claim_records':0})
        self.assertEqual(verify_current_export(self.root,['empty'],self.output),receipt)

    def test_withheld_relation_is_not_a_complete_graph_with_missing_edges(self):
        native=self.store.register(platform='kakaotalk',original_id='native-relation',kind='room',label='Native',projects=['native'])
        self.store.ingest(native,[{'original_id':k,'label':k,'text':'native fixture'} for k in ('n1','n2')],
            relations=[{'source_platform':'kakaotalk','source_id':'n1','target_platform':'kakaotalk',
                        'target_id':'n2','type':'reference','evidence':{'kind':'fixture'}}])
        factory=lambda:CollectionStore.open_existing(self.root,source_access=lambda *args:True,
            source_revision=lambda _: 'c'*64,source_relation_access=lambda *args:False)
        with self.assertRaisesRegex(ExportError,'source_withheld'):
            export_graph(self.root,['native'],self.output,store_factory=factory)
        self.assertFalse(self.output.exists())

    def test_source_callbacks_do_not_touch_native_targets_outside_requested_projects(self):
        native=self.store.register(platform='kakaotalk',original_id='outside-native',kind='room',label='Outside native',projects=['two'])
        self.store.ingest(native,[{'original_id':'outside-native','label':'Outside native message','text':'outside'}])
        seen=[]
        def access(target,*args):seen.append(target);return True
        factory=lambda:CollectionStore.open_existing(self.root,source_access=access,source_revision=lambda _: 'a'*64)
        export_graph(self.root,['one'],self.output,store_factory=factory)
        self.assertNotIn(native,seen)

    def test_native_consent_change_mid_snapshot_cannot_publish_old_labels(self):
        native=self.store.register(platform='kakaotalk',original_id='native',kind='room',label='Native',projects=['native'])
        self.store.ingest(native,[{'original_id':'n','label':'Native fixture','text':'native body'}])
        allowed=[True];original=export._records
        factory=lambda:CollectionStore.open_existing(self.root,source_access=lambda *args:allowed[0],source_revision=lambda _: 'b'*64)
        def changing(*args):
            expected,rows=original(*args)
            def iterate():
                for table,value in rows:
                    yield table,value
                    if table=='nodes':allowed[0]=False
            return expected,iterate()
        with patch.object(export,'_records',side_effect=changing):
            with self.assertRaisesRegex(RuntimeError,'authorization_changed'):
                export_graph(self.root,['native'],self.output,store_factory=factory)
        self.assertFalse(self.output.exists())

    def test_persisted_corruption_and_incomplete_receipt_are_rejected(self):
        export_graph(self.root,['one'],self.output)
        path=self.output/'graph.sqlite3'
        with path.open('r+b') as stream:stream.seek(-1,2);stream.write(b'x')
        with self.assertRaisesRegex(ExportError,'digest_mismatch'):verify_export(self.output)
        (self.output/'receipt.json').unlink()
        with self.assertRaises(FileNotFoundError):verify_export(self.output)

    def test_authority_is_rechecked_after_persisted_archive_verification(self):
        original=export._verify_data
        def revoked(*args):
            original(*args)
            self.store.register(platform='graph',original_id='a',kind='source',label='Same publisher',
                                projects=['one'],permission='denied')
        with patch.object(export,'_verify_data',side_effect=revoked):
            with self.assertRaisesRegex(ExportError,'source_changed'):
                export_graph(self.root,['one'],self.output)
        self.assertFalse(self.output.exists())

    def test_failed_persisted_readback_never_publishes_a_receipt(self):
        with patch.object(export,'_verify_data',side_effect=ExportError('collection_export_digest_mismatch')):
            with self.assertRaisesRegex(ExportError,'digest_mismatch'):
                export_graph(self.root,['one'],self.output)
        self.assertFalse(self.output.exists())

    def test_archive_changes_between_readback_and_hashing_are_not_published(self):
        original=export._verify_data
        def changed(path,*args):
            original(path,*args)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE nodes SET payload=json_set(payload,'$.label','changed')")
        with patch.object(export,'_verify_data',side_effect=changed):
            with self.assertRaisesRegex(ExportError,'collection_export_changed'):
                export_graph(self.root,['one'],self.output)
        self.assertFalse(self.output.exists())

    def test_archive_byte_budget_discards_an_otherwise_valid_full_graph(self):
        self.store.ingest(self.a,[{'original_id':'bytes-'+str(i),'label':'Record '+str(i),
                                   'text':'fixture'} for i in range(600)])
        with self.assertRaisesRegex(ExportError,'byte_budget'):
            export_graph(self.root,['one'],self.output,max_bytes=65536)
        self.assertFalse(self.output.exists())

    def test_failure_cleanup_does_not_delete_a_replacement_directory(self):
        moved=self.base/'moved-owned-output'
        def replaced(*args):
            self.output.rename(moved);self.output.mkdir(mode=0o700)
            (self.output/'keep.txt').write_text('foreign replacement')
            raise RuntimeError('fixture replacement')
        with patch.object(export,'_records',side_effect=replaced):
            with self.assertRaisesRegex(ExportError,'owner_changed'):export_graph(self.root,['one'],self.output)
        self.assertEqual((self.output/'keep.txt').read_text(),'foreign replacement')
        self.assertTrue(moved.exists())


if __name__=='__main__':unittest.main()
