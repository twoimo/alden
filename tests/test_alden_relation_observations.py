"""Re-observation is distinct from a relationship/content revision."""
import importlib.util
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_collection as current
if os.environ.get('ALDEN_RELATION_BASELINE_FILE'):
    spec=importlib.util.spec_from_file_location('relation_baseline',os.environ['ALDEN_RELATION_BASELINE_FILE'])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
else:module=current


class RelationObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(dir='/private/tmp');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=module.CollectionStore(self.root)
        self.target=self.store.register(platform='graph',original_id='declared',kind='source',label='declared',projects=['one'])
        self.revision='1'*64

    def snapshot(self, at, *, body='initial', processor='v1', revision=None, source_extra=None, raw_extra=None, quote='same', publication_guard=None):
        revision=revision or self.revision
        source={'sha256':revision,'observed_at':at,'path':'/declared/export.json','mtime_ns':10}
        source.update(source_extra or {})
        evidence={'source':source,'raw':{'id':'edge','kind':'reference',**(raw_extra or {})},'quote':quote}
        records=[{'platform':'graph','original_id':key,'label':key,'text':body if key=='x' else 'unchanged','raw':{'id':key}}
                 for key in ('x','y')]
        return self.store.ingest(self.target,records,relations=[{'source_platform':'graph','source_id':'x',
            'target_platform':'graph','target_id':'y','type':'reference','original_id':'edge','evidence':evidence}],
            cursor={'complete':True,'source_observation':{'source_revision':revision,'observed_at':at}},
            complete_snapshot=True,source_revision=revision,source_order=1 if revision==self.revision else 2,
            processing_version=processor,publication_guard=publication_guard)

    def state(self):
        with self.store.database() as db:
            row=dict(db.execute('SELECT * FROM relations').fetchone())
            count=db.execute('SELECT COUNT(*) FROM relation_versions').fetchone()[0]
            changes=[json.loads(r[0]).get('change') for r in db.execute('SELECT details FROM events')]
        return row,count,changes

    def test_parser_or_body_revision_does_not_version_unchanged_file_relationships(self):
        self.snapshot(10);before=self.state();self.snapshot(20,body='revised projection',processor='v2')
        after=self.state();self.assertEqual(after[0],before[0]);self.assertEqual(after[1],1)
        self.assertNotIn('relations_changed',after[2][len(before[2]):])
        self.assertEqual(json.loads(self.store.target(self.target)['cursor'])['source_observation']['observed_at'],20)
        with self.store.database() as db:
            cursor=json.loads(db.execute('SELECT cursor_after FROM runs WHERE state=? ORDER BY started_at DESC LIMIT 1',('complete',)).fetchone()[0])
        self.assertEqual(cursor['source_observation']['observed_at'],20)

    def test_unchanged_confirmation_keeps_capture_pointer_and_records_fresh_observation(self):
        self.snapshot(10)
        with self.store.database() as db:
            cursor=json.loads(db.execute('SELECT cursor FROM targets').fetchone()[0]);cursor['source_capture']={'manifest':'previous','sha256':self.revision}
            db.execute('UPDATE targets SET cursor=?',(current.encoded(cursor).decode(),))
            counts=[db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('runs','events','versions','relation_versions')]
        result=self.snapshot(20);self.assertEqual(result['state'],'unchanged')
        target=json.loads(self.store.target(self.target)['cursor']);self.assertEqual(target['source_capture'],cursor['source_capture'])
        self.assertEqual(target['source_observation']['observed_at'],20)
        self.snapshot(15)
        self.assertEqual(json.loads(self.store.target(self.target)['cursor'])['source_observation']['observed_at'],20)
        with self.store.database() as db:
            self.assertEqual(counts,[db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('runs','events','versions','relation_versions')])
            observations=[tuple(r) for r in db.execute('SELECT observed_at,basis_run_id FROM source_observations ORDER BY observed_at')]
        self.assertEqual([r[0] for r in observations],[10,15,20])
        self.snapshot(20)
        self.snapshot(20.0)
        with self.store.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM source_observations').fetchone()[0],3)

    def test_relation_set_changes_retract_only_missing_edges_from_same_file(self):
        self.snapshot(10)
        records=[{'platform':'graph','original_id':key,'label':key,'text':'same','raw':{'id':key}} for key in ('x','y')]
        self.store.ingest(self.target,records,relations=[],complete_snapshot=True,
                         source_revision=self.revision,source_order=1,processing_version='v2',
                         cursor={'source_observation':{'source_revision':self.revision,'observed_at':20}})
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT active FROM relations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM relation_versions').fetchone()[0],1)

    def test_file_revision_or_relationship_claim_changes_retain_new_history(self):
        for index,change in enumerate(({'revision':'2'*64},{'raw_extra':{'observed_at':99}}, {'quote':'corrected claim'},
                       {'source_extra':{'path':'/another/export.json'}}, {'source_extra':{'author':'new author'}})):
            with self.subTest(change=change):
                temporary=module.CollectionStore(self.root/('case'+str(index)))
                original_store,original_target=self.store,self.target
                self.store=temporary;self.target=temporary.register(platform='graph',original_id='declared',kind='source',label='declared',projects=['one'])
                self.snapshot(10);self.snapshot(20,processor='v2',**change)
                self.assertEqual(self.state()[1],2)
                self.store,self.target=original_store,original_target

    def test_non_snapshot_timestamps_are_not_equated(self):
        self.snapshot(10);row,count,_=self.state()
        evidence=json.loads(row['evidence']);evidence['source']['observed_at']=20
        self.store.ingest(self.target,[],relations=[{'source_platform':'graph','source_id':'x',
            'target_platform':'graph','target_id':'y','type':'reference','original_id':'edge','evidence':evidence}])
        self.assertEqual(self.state()[1],count+1)

    def test_invalid_observation_is_rejected_without_publishing(self):
        self.snapshot(10);before=self.store.path.read_bytes()
        for value in (False,'20',float('inf'),-1,None,10**100):
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                self.snapshot(value,processor='v2')
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_old_relation_integrity_failure_is_not_silently_reused(self):
        self.snapshot(10)
        with self.store.database() as db:db.execute("UPDATE relations SET evidence='corrupt'")
        with self.assertRaisesRegex(RuntimeError,'collection_relation_integrity'):
            self.snapshot(20,processor='v2')

    def test_confirmation_receipt_cursor_and_last_success_roll_back_together(self):
        self.snapshot(10);before=self.store.target(self.target)
        @contextmanager
        def rejected():
            raise RuntimeError('publication rejected')
            yield
        with self.assertRaisesRegex(RuntimeError,'publication rejected'):
            self.snapshot(20,publication_guard=rejected)
        after=self.store.target(self.target)
        self.assertEqual(after['cursor'],before['cursor']);self.assertEqual(after['last_success'],before['last_success'])
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM source_observations').fetchone()[0],1)


if __name__=='__main__':unittest.main()
