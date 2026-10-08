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

    def snapshot(self, target, records, order, *, processor='v1', relations=(), cancelled=lambda:False):
        revision=digest(encoded([records,relations]))
        return self.store.ingest(target,records,relations=relations,complete_snapshot=True,
                                 source_revision=revision,source_order=order,processing_version=processor,
                                 cursor={'source_revision':revision,'complete':True},cancelled=cancelled)

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

    def test_older_and_conflicting_source_revisions_cannot_replace_current(self):
        self.snapshot(self.a,[self.record('same','new')],20)
        before=self.store.target(self.a)['cursor']
        for order,reason in [(19,'stale'),(20,'conflict')]:
            with self.assertRaisesRegex(RuntimeError,reason):
                self.snapshot(self.a,[self.record('same','old')],order)
        self.assertEqual(self.store.target(self.a)['cursor'],before)
        self.assertEqual(self.store.graph_page(projects=['one'])['nodes'][0]['label'],'new')

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
