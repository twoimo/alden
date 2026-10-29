"""Journal-assisted archives must equal independently rebuilt source archives."""
from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_alden_collection_export as fixture
import alden_collection_delta as delta
from alden_collection import CollectionStore,identity
from alden_collection_export import ExportError,export_graph,verify_export,verify_current_export


class SourceDeltaTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ExportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root;self.store=self.f.store;self.base=self.f.output
        self.original=export_graph(self.root,['one'],self.base);self.output=self.f.base/'delta';self.oracle=0
    def update(self):
        return self.store.ingest(self.f.a,[{'original_id':'a','label':'New source a','text':'fixture'}])
    def compare(self,value=None):
        if value is None:value=delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertEqual(verify_export(self.output),value);verify_current_export(self.root,['one'],self.output)
        self.oracle+=1;full=export_graph(self.root,['one'],self.f.base/('full-oracle-'+str(self.oracle)))
        self.assertEqual(value['counts'],full['counts']);self.assertEqual(value['row_hashes'],full['row_hashes'])
        return value

    def test_single_revision_replaces_one_source_row_and_matches_full_export(self):
        before=(self.base/'graph.sqlite3').read_bytes();self.update();source=self.store.path.read_bytes()
        value=self.compare();self.assertEqual(value['source_update']['mode'],'journal_delta')
        self.assertEqual(value['source_update']['candidate_nodes'],1)
        self.assertEqual(value['source_update']['archive_rows'],{'targets_put':0,'nodes_put':1,'nodes_deleted':0,'edges_put':0,'edges_deleted':0})
        self.assertEqual((self.base/'graph.sqlite3').read_bytes(),before);self.assertEqual(self.store.path.read_bytes(),source)

    def test_added_record_does_not_rewrite_unchanged_records(self):
        self.store.ingest(self.f.a,[{'original_id':'d','label':'Added d','text':'fixture'}]);value=self.compare()
        self.assertEqual(value['counts']['nodes'],4);self.assertEqual(value['source_update']['archive_rows']['nodes_put'],1)

    def test_retraction_removes_document_and_incident_group(self):
        self.store.ingest(self.f.a,[],retractions=[{'platform':'graph','original_id':'c'}]);value=self.compare()
        self.assertEqual(value['counts']['nodes'],2);self.assertEqual(value['counts']['edges'],1)
        self.assertEqual(value['source_update']['archive_rows']['nodes_deleted'],1)
        self.assertEqual(value['source_update']['archive_rows']['edges_deleted'],1)

    def test_retracted_membership_keeps_other_current_origin_context(self):
        self.store.ingest(self.f.a,[],retractions=[{'platform':'graph','original_id':'a'}]);value=self.compare()
        self.assertEqual(value['counts']['nodes'],3);self.assertEqual(value['source_update']['archive_rows']['nodes_put'],1)
        with sqlite3.connect(self.output/'graph.sqlite3') as db:
            node=json.loads(db.execute('SELECT payload FROM nodes WHERE id=?',(identity('graph','a'),)).fetchone()[0])
        self.assertEqual({c['target_id'] for c in node['source_contexts']},{self.f.b})

    def test_completed_snapshot_reaggregates_all_surviving_claims_in_a_group(self):
        records=[{'original_id':k,'label':'Same label '+k,'text':'PRIVATE_BODY_'+k} for k in ('a','b','c')]
        self.store.ingest(self.f.a,records,relations=[self.f.relation('b','c')],complete_snapshot=True,
            source_revision='a'*64,source_order=1)
        value=self.compare();self.assertEqual(value['source_update']['mode'],'journal_delta')
        self.assertEqual(value['counts']['claim_records'],2)
        with sqlite3.connect(self.output/'graph.sqlite3') as db:
            group=json.loads(db.execute('SELECT payload FROM edges WHERE source=?',(identity('graph','a'),)).fetchone()[0])
        self.assertEqual(group['reference_group']['count'],1)

    def test_relation_only_run_rebuilds_complete_group_without_document_events(self):
        for i in range(24):self.store.ingest(self.f.a,[],relations=[self.f.relation('a','b',original_id='claim-'+str(i),evidence={'claim':i})])
        value=delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertEqual(value['source_update']['mode'],'journal_delta');self.compare(value)
        self.assertEqual(value['source_update']['candidate_nodes'],0)
        self.assertEqual(value['source_update']['archive_rows']['edges_put'],1)
        with sqlite3.connect(self.output/'graph.sqlite3') as db:
            group=json.loads(db.execute('SELECT payload FROM edges WHERE source=?',(identity('graph','a'),)).fetchone()[0])
        self.assertEqual(group['reference_group']['count'],26);self.assertEqual(len(group['evidence']['source_event_ids']),16)

    def test_unchanged_source_writes_no_archive_rows(self):
        value=self.compare();self.assertEqual(value['source_update']['events'],0)
        self.assertTrue(all(n==0 for n in value['source_update']['archive_rows'].values()))

    def test_outside_scope_events_do_not_expand_or_advance_this_scope(self):
        self.store.ingest(self.f.outside,[{'original_id':'extra','label':'OUTSIDE_CANARY','text':'outside'}])
        value=self.compare();self.assertEqual(value['source_update']['events'],0)
        self.assertEqual(value['as_of'],self.original['as_of'])
        with sqlite3.connect(self.output/'graph.sqlite3') as db:
            self.assertNotIn('OUTSIDE',str(db.execute('SELECT payload FROM targets UNION ALL SELECT payload FROM nodes').fetchall()))

    def test_unjournaled_record_change_is_detected_and_fully_rebuilt(self):
        with self.store.database() as db:db.execute('UPDATE versions SET label=? WHERE document_id=?',('Unjournaled label',identity('graph','a')))
        value=delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertEqual(value['source_update'],{'mode':'full_export','reason':'untracked_source_change'});self.compare(value)

    def test_unjournaled_removed_claim_is_not_left_in_the_archive(self):
        with self.store.database() as db:db.execute('DELETE FROM relations WHERE target_id=?',(self.f.a,))
        value=self.compare();self.assertEqual(value['source_update']['reason'],'untracked_source_change')

    def test_mixed_journal_and_unjournaled_changes_cannot_acknowledge_a_smaller_graph(self):
        self.update()
        with self.store.database() as db:db.execute('UPDATE versions SET label=? WHERE document_id=?',('Other unjournaled',identity('graph','b')))
        value=self.compare();self.assertEqual(value['source_update']['reason'],'untracked_source_change')

    def test_unjournaled_publisher_label_is_refreshed_without_record_rewrites(self):
        with self.store.database() as db:db.execute('UPDATE targets SET label=? WHERE id=?',('New publisher',self.f.a))
        value=self.compare();self.assertEqual(value['source_update']['mode'],'journal_delta')
        self.assertEqual(value['source_update']['archive_rows']['targets_put'],1)
        self.assertEqual(value['source_update']['archive_rows']['nodes_put'],0)

    def test_permission_change_uses_a_new_complete_baseline(self):
        self.store.register(platform='graph',original_id='a',kind='source',label='Same publisher',projects=['one'],permission='denied')
        with patch.object(delta,'_window',side_effect=AssertionError('must not reuse changed authority')):value=self.compare()
        self.assertEqual(value['source_update']['reason'],'source_baseline_changed');self.assertEqual(value['counts']['targets'],1)

    def test_full_denial_stops_without_a_fallback_reader(self):
        for k in ('a','b'):self.store.register(platform='graph',original_id=k,kind='source',label='Same publisher',projects=['one'],permission='denied')
        with patch.object(delta,'export_graph',side_effect=AssertionError('must not bypass denial')):
            with self.assertRaisesRegex(ValueError,'scope_invalid'):delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertFalse(self.output.exists())

    def test_replaced_source_database_requires_full_baseline(self):
        p=self.root/'replacement.sqlite3';shutil.copyfile(self.store.path,p);p.replace(self.store.path)
        value=self.compare();self.assertEqual(value['source_update']['reason'],'source_baseline_changed')

    def test_rewound_journal_requires_full_baseline(self):
        cursor=self.original['as_of']['checkpoint']['cursor']
        with self.store.database() as db:db.execute('DELETE FROM events WHERE sequence>?',(cursor//2,))
        value=self.compare();self.assertEqual(value['source_update']['reason'],'source_baseline_changed')

    def test_large_journal_window_rebuilds_without_truncating(self):
        self.update()
        with patch.object(delta,'MAX_EVENTS',1):value=self.compare()
        self.assertEqual(value['source_update']['reason'],'journal_window_budget')

    def test_large_candidate_set_rebuilds_without_truncating(self):
        self.update()
        with patch.object(delta,'MAX_KEYS',0):value=self.compare()
        self.assertEqual(value['source_update']['reason'],'journal_window_budget')

    def test_unreleased_activity_does_not_advance_the_archive(self):
        self.store.ingest(self.f.a,[{'original_id':'new','label':'Deferred','text':'fixture'}],defer_activity=True)
        with patch.object(delta,'export_graph',side_effect=AssertionError('deferred is not a baseline fallback')):
            with self.assertRaisesRegex(ExportError,'publication_deferred'):delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertFalse(self.output.exists())

    def test_explicit_activity_release_can_then_advance_the_exact_window(self):
        run=self.store.ingest(self.f.a,[{'original_id':'new','label':'Released','text':'fixture'}],defer_activity=True)
        self.store.release_activity(self.f.a,run['run_id'],publication_guard=nullcontext)
        value=self.compare();self.assertEqual(value['source_update']['mode'],'journal_delta')
        self.assertEqual(value['counts']['nodes'],4)

    def test_os_denial_stops_without_full_export_or_another_factory(self):
        def denied():raise PermissionError('injected source denial')
        with patch.object(delta,'export_graph',side_effect=AssertionError('no fallback')):
            with self.assertRaises(PermissionError):delta.export_incremental(self.root,['one'],self.output,previous=self.base,store_factory=denied)
        self.assertFalse(self.output.exists())

    def test_invalid_writable_factory_is_rejected(self):
        with self.assertRaisesRegex(ExportError,'readonly_source_required'):
            delta.export_incremental(self.root,['one'],self.output,previous=self.base,store_factory=lambda:self.store)

    def test_cancelled_copy_removes_only_its_new_output(self):
        before=(self.base/'graph.sqlite3').read_bytes()
        with self.assertRaisesRegex(ExportError,'cancelled'):
            delta.export_incremental(self.root,['one'],self.output,previous=self.base,cancelled=lambda:(self.output/'graph.sqlite3').exists())
        self.assertFalse(self.output.exists());self.assertEqual((self.base/'graph.sqlite3').read_bytes(),before)

    def test_uncertain_receipt_save_is_not_replayed_as_a_full_export(self):
        self.update();original=delta._write
        def uncertain(path,value):
            original(path,value)
            if Path(path).name=='receipt.json':raise OSError('injected after save')
        with patch.object(delta,'_write',side_effect=uncertain),patch.object(delta,'export_graph',side_effect=AssertionError('no replay')):
            with self.assertRaises(OSError):delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertFalse(self.output.exists());verify_export(self.base)

    def test_existing_output_is_not_overwritten(self):
        self.update();value=self.compare();before=(self.output/'receipt.json').read_bytes()
        with self.assertRaisesRegex(ExportError,'new_output_required'):delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertEqual((self.output/'receipt.json').read_bytes(),before)

    def test_wrong_source_scope_is_not_implicitly_adopted(self):
        with self.assertRaisesRegex(ExportError,'scope_mismatch'):delta.export_incremental(self.root,['two'],self.output,previous=self.base)
        self.assertFalse(self.output.exists())

    def test_malformed_checkpoint_is_rejected_before_source_cursor_use(self):
        p=self.base/'receipt.json';value=json.loads(p.read_text());value['as_of']['checkpoint']['cursor']=True;p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ExportError,'checkpoint_invalid'):delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertFalse(self.output.exists())

    def test_corrupt_base_archive_is_rejected_without_a_fallback(self):
        with sqlite3.connect(self.base/'graph.sqlite3') as db:db.execute("UPDATE nodes SET payload='{}'")
        with patch.object(delta,'export_graph',side_effect=AssertionError('corrupt base is not adopted')):
            with self.assertRaises(ExportError):delta.export_incremental(self.root,['one'],self.output,previous=self.base)

    def test_record_budget_does_not_accept_a_partial_delta(self):
        self.update()
        with self.assertRaisesRegex(ExportError,'record_budget'):delta.export_incremental(self.root,['one'],self.output,previous=self.base,max_records=1)
        self.assertFalse(self.output.exists())

    def test_bad_journal_details_are_not_silently_skipped(self):
        self.update()
        with self.store.database() as db:db.execute("UPDATE events SET details='[]' WHERE sequence=(SELECT MAX(sequence) FROM events)")
        with self.assertRaisesRegex(ExportError,'journal_invalid'):delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.assertFalse(self.output.exists())

    def test_legacy_relation_writer_without_an_event_is_rebuilt_safely(self):
        event=self.store._event
        def legacy(db,run_id,target_id,stage,**details):
            if details.get('change')=='relations_changed':return
            return event(db,run_id,target_id,stage,**details)
        with patch.object(self.store,'_event',side_effect=legacy):
            self.store.ingest(self.f.a,[],relations=[self.f.relation('a','b',original_id='legacy-extra')])
        value=self.compare();self.assertEqual(value['source_update']['reason'],'untracked_source_change')

    def test_retraction_deactivates_only_its_own_claim_and_preserves_history(self):
        with self.store.database() as db:before=db.execute('SELECT COUNT(*) FROM relation_versions').fetchone()[0]
        self.store.ingest(self.f.a,[],retractions=[{'platform':'graph','original_id':'a'}])
        with self.store.database() as db:
            rows=db.execute('SELECT target_id,active FROM relations WHERE source=?',(identity('graph','a'),)).fetchall()
            self.assertEqual({r['target_id']:r['active'] for r in rows if r['target_id'] in (self.f.a,self.f.b)},
                             {self.f.a:0,self.f.b:1})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM relation_versions').fetchone()[0],before)
        self.compare()

    def test_deferred_relation_only_event_requires_explicit_release(self):
        run=self.store.ingest(self.f.a,[],relations=[self.f.relation('a','b',original_id='deferred-extra')],defer_activity=True)
        with self.assertRaisesRegex(ExportError,'publication_deferred'):
            delta.export_incremental(self.root,['one'],self.output,previous=self.base)
        self.store.release_activity(self.f.a,run['run_id'],publication_guard=nullcontext)
        value=self.compare();self.assertEqual(value['source_update']['archive_rows']['edges_put'],1)


if __name__=='__main__':unittest.main()
