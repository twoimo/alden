"""Relations must agree with their immutable revision before display or replay."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from alden_collection import CollectionStore, identity
from alden_graph_quality import audit
import alden_entity_resolution as resolution


class RelationBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='graph', original_id='binding-fixture',
            kind='source', label='binding fixture', projects=['one'])
        self.records = [{'platform':'graph','original_id':name,'label':name,
            'text':'explicit fixture source','raw':{'name':name}} for name in ('x','y')]
        self.edge = {'source_platform':'graph','source_id':'x','target_platform':'graph',
            'target_id':'y','type':'reference','evidence':{'reason':'explicit fixture edge'}}
        self.store.ingest(self.target, self.records, relations=[self.edge])

    def rows(self, table):
        with self.store.database() as db:
            return [tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]

    def test_quality_detects_changed_direction_without_changing_records(self):
        with self.store.database() as db:
            db.execute('UPDATE relations SET source=target,target=source')
        before = self.rows('relations'), self.rows('relation_versions')
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings'].get('relation_history_binding_mismatch'), 1)
        self.assertEqual((self.rows('relations'), self.rows('relation_versions')), before)

    def test_quality_distinguishes_missing_history(self):
        with self.store.database() as db:
            db.execute('DELETE FROM relation_versions')
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings'].get('relation_history_missing'), 1)
        self.assertNotIn('relation_history_binding_mismatch', report['findings'])

    def test_both_graph_paths_reject_type_changed_under_same_version(self):
        with self.store.database() as db:
            db.execute("UPDATE relations SET type='contradicts'")
        for read in (lambda:self.store.graph(projects=['one']),
                     lambda:self.store.graph_page(projects=['one'], overview=True),
                     lambda:self.store.graph_page(projects=['one'], focus=identity('graph','x'))):
            with self.subTest(reader=read):
                with self.assertRaisesRegex(RuntimeError, 'collection_relation_integrity'):
                    read()

    def test_graph_paths_reject_evidence_changed_without_new_version(self):
        with self.store.database() as db:
            db.execute('UPDATE relations SET evidence=?', (json.dumps({'reason':'altered'}),))
        for read in (lambda:self.store.graph(projects=['one']),
                     lambda:self.store.graph_page(projects=['one'], overview=True)):
            with self.assertRaisesRegex(RuntimeError, 'collection_relation_integrity'):
                read()

    def test_writer_rejects_existing_revision_collision_without_repairing_history(self):
        with self.store.database() as db:
            db.execute("UPDATE relation_versions SET type='contradicts'")
        before = self.rows('relations'), self.rows('relation_versions')
        with self.assertRaisesRegex(RuntimeError, 'collection_relation_integrity'):
            self.store.ingest(self.target, self.records, relations=[self.edge])
        self.assertEqual((self.rows('relations'), self.rows('relation_versions')), before)

    def test_matching_rows_with_wrong_evidence_digest_are_still_rejected(self):
        with self.store.database() as db:
            for table in ('relations', 'relation_versions'):
                db.execute('UPDATE '+table+' SET evidence=?', (json.dumps({'reason':'altered'}),))
        with self.assertRaisesRegex(RuntimeError, 'collection_relation_integrity'):
            self.store.graph_page(projects=['one'], overview=True)

    def test_resolution_preview_cannot_use_a_changed_relation_revision(self):
        with self.store.database() as db:
            members=[dict(row) for row in db.execute('SELECT target_id,document_id,current_version AS version FROM memberships ORDER BY document_id')]
            db.execute("UPDATE relations SET type='contradicts'")
        before=self.rows('relations'),self.rows('relation_versions')
        with self.assertRaisesRegex(RuntimeError, 'collection_resolution_relation_integrity'):
            resolution.resolution_action(self.root,'collection-resolution-preview',{
                'projects':['one'],'members':members,
                'canonical':{k:members[0][k] for k in ('target_id','document_id')},
                'reason':'Synthetic integrity fixture only; no identity decision'})
        self.assertEqual((self.rows('relations'),self.rows('relation_versions')),before)

    def test_legitimate_reciprocal_edges_revisions_and_replay_remain_valid(self):
        reverse = {**self.edge,'source_id':'y','target_id':'x'}
        self.store.ingest(self.target, self.records, relations=[self.edge,reverse])
        revised = {**self.edge,'evidence':{'reason':'new explicit observation'}}
        self.store.ingest(self.target, self.records, relations=[revised,reverse])
        before = self.rows('relation_versions')
        self.store.ingest(self.target, self.records, relations=[revised,reverse])
        self.assertEqual(self.rows('relation_versions'), before)
        self.assertEqual(len(before), 3)
        self.assertEqual(audit(self.root, ['one'])['findings'], {})
        self.assertEqual(len(self.store.graph(projects=['one'])['edges']), 2)
        self.assertEqual(len(self.store.graph_page(projects=['one'], overview=True)['edges']), 2)

    def test_unrelated_or_filtered_target_corruption_does_not_block_requested_scope(self):
        other=self.store.register(platform='graph',original_id='other-binding',kind='source',
            label='other binding',projects=['one','two'])
        self.store.ingest(other,self.records,relations=[self.edge])
        with self.store.database() as db:
            db.execute("UPDATE relations SET type='contradicts' WHERE target_id=?",(other,))
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=? AND project='one'",(other,))
        self.assertEqual(len(self.store.graph(projects=['one'])['edges']),1)
        self.assertEqual(audit(self.root,['one'])['findings'],{})
        self.assertEqual(len(self.store.graph_page(projects=['one'],overview=True)['edges']),1)
        self.assertEqual(len(self.store.graph_page(projects=['one','two'],target_id=self.target,overview=True)['edges']),1)
        with self.assertRaisesRegex(RuntimeError,'collection_relation_integrity'):
            self.store.graph_page(projects=['two'],overview=True)


if __name__ == '__main__': unittest.main()
