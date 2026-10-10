"""Complete snapshots, processing revisions, target deletion and commit recovery."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from contextlib import contextmanager

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from alden_collection import CollectionStore,digest,encoded,identity
from alden_collect import collect_target


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.store=CollectionStore(self.root)
        self.a=self.target('a','one');self.b=self.target('b','two')

    def target(self, original, project):
        return self.store.register(platform='graph',original_id=original,kind='source',label=original,projects=[project])

    def record(self, original, label='current'):
        return {'platform':'graph','original_id':original,'label':label,'text':label,'raw':{'id':original}}

    def snapshot(self, target, records, order, *, processor='v1', relations=(), cancelled=lambda:False,
                 publication_guard=None):
        revision=digest(encoded([records,relations]))
        return self.store.ingest(target,records,relations=relations,complete_snapshot=True,
                                 source_revision=revision,source_order=order,processing_version=processor,
                                 cursor={'source_revision':revision,'complete':True},cancelled=cancelled,
                                 publication_guard=publication_guard)

    def persisted_state(self):
        with CollectionStore.open_existing(self.root).database() as db:
            return tuple(db.iterdump())

    def test_target_deletion_preserves_another_targets_version_and_archive(self):
        self.snapshot(self.a,[self.record('shared','A'),self.record('removed')],1)
        self.snapshot(self.b,[self.record('shared','B')],1)
        report=self.snapshot(self.a,[],2)
        self.assertEqual(report['removed'],2)
        self.assertEqual(self.store.graph_page(projects=['one'])['nodes'],[])
        visible=self.store.graph_page(projects=['two'])['nodes']
        self.assertEqual([n['label'] for n in visible],['B'])
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0],3)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_reappearing_exact_revision_reactivates_sources_and_relations_once(self):
        records=[self.record('a'),self.record('b')]
        relation={'source_platform':'graph','source_id':'a','target_platform':'graph',
                  'target_id':'b','type':'cites','evidence':{'source':'fixture'}}
        first=self.snapshot(self.a,records,1,relations=[relation])
        version=self.store.graph_page(projects=['one'])['nodes'][0]['source_version']
        self.assertEqual(len(self.store.graph_page(projects=['one'])['edges']),1)
        deleted=self.snapshot(self.a,[],2)
        self.assertEqual(deleted['removed'],2)
        self.assertEqual(self.store.graph_page(projects=['one'])['nodes'],[])
        restored=self.snapshot(self.a,records,3,relations=[relation])
        page=self.store.graph_page(projects=['one'])
        self.assertEqual(restored['state'],'complete')
        self.assertNotEqual(restored['run_id'],first['run_id'])
        self.assertEqual(restored['added'],0)
        self.assertEqual(restored['unchanged'],2)
        self.assertEqual(len(page['nodes']),2)
        self.assertEqual(len(page['edges']),1)
        self.assertEqual(page['nodes'][0]['source_version'],version)
        with self.store.database() as db:
            runs=db.execute('SELECT COUNT(*) FROM runs WHERE target_id=?',(self.a,)).fetchone()[0]
            stages=db.execute('SELECT COUNT(*) FROM events WHERE run_id=?',(restored['run_id'],)).fetchone()[0]
        self.assertEqual(runs,3)
        self.assertGreater(stages,0)
        replay=self.snapshot(self.a,records,3,relations=[relation])
        self.assertEqual(replay['state'],'unchanged')
        self.assertEqual(replay['run_id'],restored['run_id'])
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs WHERE target_id=?',(self.a,)).fetchone()[0],runs)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events WHERE run_id=?',(restored['run_id'],)).fetchone()[0],stages)

    def test_older_and_conflicting_source_revisions_cannot_replace_current(self):
        self.snapshot(self.a,[self.record('same','new')],20)
        before=self.store.target(self.a)['cursor']
        for order,reason in [(19,'stale'),(20,'conflict')]:
            with self.assertRaisesRegex(RuntimeError,reason):
                self.snapshot(self.a,[self.record('same','old')],order)
        self.assertEqual(self.store.target(self.a)['cursor'],before)
        self.assertEqual(self.store.graph_page(projects=['one'])['nodes'][0]['label'],'new')

    def test_unchanged_snapshot_preserves_latest_source_order_without_new_history(self):
        records=[self.record('same')]
        first=self.snapshot(self.a,records,10)
        self.snapshot(self.b,[self.record('other')],7)
        cursor=self.store.target(self.a)['cursor']
        tables=['versions','runs','events']
        with self.store.database() as db:
            prior=dict(db.execute('SELECT * FROM target_snapshots WHERE target_id=?',(self.a,)).fetchone())
            counts=[db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in tables]
        # A delayed replay must never lower the latest confirmed source order.
        for order in (30,20,30):
            report=self.snapshot(self.a,records,order)
            self.assertEqual(report['state'],'unchanged')
            self.assertEqual(report['run_id'],first['run_id'])
        with CollectionStore.open_existing(self.root).database() as db:
            current=dict(db.execute('SELECT * FROM target_snapshots WHERE target_id=?',(self.a,)).fetchone())
            self.assertEqual(current,{**prior,'source_order':30})
            self.assertEqual([db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in tables],counts)
            self.assertEqual(db.execute('SELECT source_order FROM target_snapshots WHERE target_id=?',(self.b,)).fetchone()[0],7)
        self.assertEqual(self.store.target(self.a)['cursor'],cursor)

    def test_unchanged_snapshot_rejects_stale_and_conflicting_source_revisions(self):
        for order,reason in [(20,'stale'),(30,'conflict')]:
            with self.subTest(order=order,reason=reason):
                target=self.target('replay-'+str(order),'one')
                original='same-'+str(order)
                records=[self.record(original,'current')]
                self.snapshot(target,records,10)
                self.snapshot(target,records,30)
                cursor=self.store.target(target)['cursor']
                doc_id=identity('graph',original)
                before=self.store.graph_page(projects=['one'],focus=doc_id)['nodes'][0]
                with self.assertRaisesRegex(RuntimeError,'collection_source_revision_'+reason):
                    self.snapshot(target,[self.record(original,'outdated')],order)
                self.assertEqual(self.store.target(target)['cursor'],cursor)
                current=self.store.graph_page(projects=['one'],focus=doc_id)['nodes'][0]
                self.assertEqual(current['source_version'],before['source_version'])
                self.assertEqual(current['label'],'current')
                self.assertEqual(self.store.search('outdated',projects=['one']),[])
                report=self.snapshot(target,[self.record(original,'newer')],31)
                self.assertEqual(report['state'],'complete')
                self.assertEqual(self.store.graph_page(projects=['one'],focus=doc_id)['nodes'][0]['label'],'newer')

    def test_processing_rule_changes_keep_same_raw_bytes_and_prior_projection(self):
        record=self.record('same','first')
        self.snapshot(self.a,[record],10,processor='v1')
        with self.store.database() as db:first=dict(db.execute('SELECT * FROM versions').fetchone())
        record['label']='second';record['text']='second'
        self.snapshot(self.a,[record],11,processor='v2')
        with self.store.database() as db:
            rows=db.execute('SELECT raw_sha256,processing_version,label FROM versions ORDER BY processing_version').fetchall()
            self.assertEqual([tuple(r) for r in rows],[(first['raw_sha256'],'v1','first'),(first['raw_sha256'],'v2','second')])
        self.assertEqual(self.store.graph_page(projects=['one'])['nodes'][0]['label'],'second')

    def test_cancelled_unchanged_snapshot_preserves_all_persisted_state(self):
        self.snapshot(self.b,[self.record('other')],7)
        for records in ([self.record('same')],[]):
            with self.subTest(empty=not records):
                target=self.target('cancelled-'+str(bool(records)),'one')
                self.snapshot(target,records,10)
                before=self.persisted_state()
                with self.assertRaisesRegex(RuntimeError,'collection_cancelled'):
                    self.snapshot(target,records,30,cancelled=lambda:True)
                self.assertEqual(self.persisted_state(),before)

    def test_abort_at_unchanged_snapshot_publication_rolls_back_confirmation(self):
        from alden_abort import AbortController, AldenCancelled
        records=[self.record('same')]
        first=self.snapshot(self.a,records,10)
        before=self.persisted_state()
        controller=AbortController(self.root);token=controller.token()
        @contextmanager
        def guard():
            controller.abort('unchanged-snapshot-test')
            with token.commit_guard():yield
        with self.assertRaises(AldenCancelled):
            self.snapshot(self.a,records,30,publication_guard=guard)
        self.assertEqual(self.persisted_state(),before)
        replay=self.snapshot(self.a,records,30)
        self.assertEqual(replay['run_id'],first['run_id'])
        self.assertEqual(replay['state'],'unchanged')

    def test_cancellation_after_publication_guard_entry_rolls_back_confirmation(self):
        records=[self.record('same')]
        self.snapshot(self.a,records,10)
        before=self.persisted_state();stopped=False
        @contextmanager
        def guard():
            nonlocal stopped
            stopped=True
            yield
        with self.assertRaisesRegex(RuntimeError,'collection_cancelled'):
            self.snapshot(self.a,records,30,cancelled=lambda:stopped,publication_guard=guard)
        self.assertTrue(stopped)
        self.assertEqual(self.persisted_state(),before)

    def test_unchanged_snapshot_confirmation_commits_once_inside_publication_guard(self):
        records=[self.record('same')]
        first=self.snapshot(self.a,records,10)
        observations=[]
        @contextmanager
        def guard():
            with CollectionStore.open_existing(self.root).database() as db:
                observations.append(db.execute('SELECT source_order FROM target_snapshots WHERE target_id=?',(self.a,)).fetchone()[0])
            yield
            with CollectionStore.open_existing(self.root).database() as db:
                observations.append(db.execute('SELECT source_order FROM target_snapshots WHERE target_id=?',(self.a,)).fetchone()[0])
        replay=self.snapshot(self.a,records,30,publication_guard=guard)
        self.assertEqual(observations,[10,30])
        self.assertEqual(replay['run_id'],first['run_id'])
        self.assertEqual(replay['state'],'unchanged')

    def test_relation_only_revision_retires_old_edge_and_retains_its_evidence(self):
        records=[self.record('x'),self.record('y')]
        edge={'source_platform':'graph','source_id':'x','target_platform':'graph','target_id':'y','type':'reference','evidence':{'quote':'original'}}
        self.snapshot(self.a,records,1,relations=[edge])
        checkpoint=self.store.graph_page(projects=['one'])['activity_checkpoint']
        self.snapshot(self.a,records,2,relations=[])
        self.assertEqual(self.store.graph_page(projects=['one'])['edges'],[])
        with self.store.database() as db:self.assertEqual(db.execute('SELECT count(*) FROM relation_versions').fetchone()[0],1)
        page=self.store.activity_page(projects=['one'],after=checkpoint['cursor'],stream_id=checkpoint['stream_id'])
        self.assertIn('relations_changed',[e['kind'] for e in page['items']])

    def test_cancelled_full_snapshot_does_not_publish_a_partial_batch_or_cursor(self):
        self.snapshot(self.a,[self.record('prior')],1)
        before=self.store.target(self.a)['cursor'];calls=0
        def cancelled():
            nonlocal calls
            calls+=1;return calls>1001
        records=[self.record(str(i)) for i in range(1100)]
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            self.snapshot(self.a,records,2,cancelled=cancelled)
        self.assertEqual(self.store.target(self.a)['cursor'],before)
        self.assertEqual(len(self.store.graph_page(projects=['one'])['nodes']),1)
        report=self.snapshot(self.a,records,2)
        self.assertEqual(report['state'],'complete');self.assertEqual(report['removed'],1)

    def test_identical_snapshot_replay_creates_no_duplicate_runs_or_stages(self):
        record=self.record('same');self.snapshot(self.a,[record],1)
        with self.store.database() as db:before=[db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['versions','runs','events']]
        self.assertEqual(self.snapshot(self.a,[record],1)['state'],'unchanged')
        with self.store.database() as db:self.assertEqual([db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['versions','runs','events']],before)

    def test_malformed_snapshot_is_not_an_empty_authoritative_source(self):
        source=self.root/'source.json';source.write_text(json.dumps({'nodes':[{'id':'x','label':'current'}],'edges':[]}))
        target=self.store.register(platform='graph',original_id='file',kind='source',label='file',projects=['one'],config={'adapter':'source-graph','path':str(source)})
        collect_target(self.store,target);before=self.store.target(target)['cursor']
        source.write_text('{}')
        with self.assertRaisesRegex(ValueError,'incomplete_source_shape'):collect_target(self.store,target)
        self.assertEqual(self.store.target(target)['cursor'],before)
        self.assertEqual(len(self.store.graph_page(projects=['one'])['nodes']),1)

    def test_abort_winning_prepared_publication_keeps_prior_snapshot_and_checkpoint(self):
        from alden_abort import AbortController
        self.snapshot(self.a,[self.record('prior')],1);before=self.store.target(self.a)['cursor']
        controller=AbortController(self.root);token=controller.token()
        @contextmanager
        def guard():
            controller.abort('snapshot-test')
            with token.commit_guard():yield
        with self.assertRaises(RuntimeError):
            self.store.ingest(self.a,[self.record('new')],cursor={'complete':True},publication_guard=guard)
        self.assertEqual(self.store.target(self.a)['cursor'],before)
        self.assertEqual([n['id'] for n in self.store.graph_page(projects=['one'])['nodes']],[identity('graph','prior')])


if __name__=='__main__':unittest.main()
