"""A newer snapshot may restore old content without replaying its old run."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, digest, encoded


class SnapshotReturnTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = CollectionStore(Path(temporary.name).resolve())
        self.target = self.store.register(platform='graph', original_id='return-test',
            kind='source', label='return test', projects=['one'])

    @staticmethod
    def records(text='alpha'):
        return [{'original_id': 'x', 'label': 'x', 'text': text},
                {'original_id': 'y', 'label': 'y', 'text': 'anchor'}]

    def snapshot(self, records, order, *, relations=(), processor='v1', **options):
        revision = digest(encoded([records, list(relations)]))
        return self.store.ingest(self.target, records, relations=relations,
            complete_snapshot=True, source_revision=revision, source_order=order,
            processing_version=processor,
            cursor={'source_revision': revision, 'source_order': order}, **options)

    def checkpoint(self):
        with self.store.database() as db:
            return dict(db.execute('SELECT * FROM target_snapshots WHERE target_id=?',
                (self.target,)).fetchone())

    def rows(self, table):
        # Only static test-owned table names are supplied by this module.
        with self.store.database() as db:
            return {tuple(row) for row in db.execute('SELECT * FROM ' + table)}

    def test_repeated_returns_restore_body_without_rewriting_versions_or_runs(self):
        completed = set()
        run_ids = []
        for order, text in [(10, 'alpha'), (20, 'beta'), (30, 'alpha'),
                            (40, 'beta'), (50, 'alpha')]:
            report = self.snapshot(self.records(text), order)
            self.assertEqual([row['body'] for row in self.store.search(text,
                projects=['one'])], [text])
            self.assertEqual(self.checkpoint()['source_order'], order)
            self.assertEqual(self.checkpoint()['run_id'], report['run_id'])
            self.assertTrue(completed <= self.rows('runs'))
            completed = self.rows('runs')
            run_ids.append(report['run_id'])
        self.assertEqual(len(set(run_ids)), 5)
        self.assertEqual(len(self.rows('versions')), 3)
        before = {table: self.rows(table) for table in ['versions', 'runs', 'events']}
        replay = self.snapshot(self.records('alpha'), 60)
        self.assertEqual(replay['state'], 'unchanged')
        self.assertEqual(replay['run_id'], run_ids[-1])
        self.assertEqual(self.checkpoint()['source_order'], 60)
        self.assertEqual({table: self.rows(table) for table in before}, before)

    def test_return_reactivates_relation_and_retains_original_evidence(self):
        records = self.records()
        edge = {'source_platform': 'graph', 'source_id': 'x',
                'target_platform': 'graph', 'target_id': 'y',
                'type': 'reference', 'evidence': {'quote': 'declared relation'}}
        first = self.snapshot(records, 10, relations=[edge])
        evidence = self.rows('relation_versions')
        self.snapshot(records, 20, relations=[])
        self.assertEqual(self.store.graph_page(projects=['one'])['edges'], [])
        restored = self.snapshot(records, 30, relations=[edge])
        self.assertNotEqual(first['run_id'], restored['run_id'])
        self.assertEqual(len(self.store.graph_page(projects=['one'])['edges']), 1)
        self.assertEqual(self.rows('relation_versions'), evidence)
        with self.store.database() as db:
            changes = db.execute("SELECT details FROM events WHERE run_id=? "
                "AND details LIKE '%relations_changed%'", (restored['run_id'],)).fetchall()
        self.assertEqual(len(changes), 1)

    def test_return_restores_deleted_members_and_keeps_their_versions(self):
        records = self.records()
        self.snapshot(records, 10)
        versions = self.rows('versions')
        self.snapshot([], 20)
        self.assertEqual(self.store.graph_page(projects=['one'])['nodes'], [])
        self.snapshot(records, 30)
        self.assertEqual({node['label'] for node in self.store.graph_page(
            projects=['one'])['nodes']}, {'x', 'y'})
        self.assertEqual(self.rows('versions'), versions)
        self.assertEqual(self.checkpoint()['source_order'], 30)

    def test_parser_policy_can_return_at_the_same_source_order(self):
        run_ids = []
        for processor in ['v1', 'v2', 'v1']:
            report = self.snapshot(self.records(), 10, processor=processor)
            run_ids.append(report['run_id'])
            with self.store.database() as db:
                current = {row[0] for row in db.execute(
                    'SELECT v.processing_version FROM memberships m JOIN versions v '
                    'ON v.id=m.current_version WHERE m.target_id=?', (self.target,))}
            self.assertEqual(current, {processor})
            self.assertEqual(self.checkpoint()['processing_version'], processor)
        self.assertEqual(len(set(run_ids)), 3)
        self.assertEqual(len(self.rows('versions')), 4)

    def test_cancelled_return_keeps_current_snapshot_and_retries_the_same_run(self):
        self.snapshot(self.records('alpha'), 10)
        self.snapshot(self.records('beta'), 20)
        checkpoint = self.checkpoint()
        versions = self.rows('versions')
        completed = self.rows('runs')
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls == 2

        with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
            self.snapshot(self.records('alpha'), 30, cancelled=cancelled)
        self.assertEqual(self.checkpoint(), checkpoint)
        self.assertEqual(self.rows('versions'), versions)
        self.assertTrue(completed <= self.rows('runs'))
        with self.store.database() as db:
            failed = db.execute("SELECT id FROM runs WHERE state='paused'").fetchall()
        self.assertEqual(len(failed), 1)
        retry = self.snapshot(self.records('alpha'), 30)
        self.assertEqual(retry['run_id'], failed[0]['id'])
        self.assertEqual(retry['state'], 'complete')
        self.assertEqual(self.checkpoint()['source_order'], 30)
        self.assertTrue(completed <= self.rows('runs'))

    def test_explicit_completed_request_id_remains_an_immutable_replay(self):
        first = self.snapshot(self.records('alpha'), 10, run_id='explicit-request')
        self.snapshot(self.records('beta'), 20)
        before = {table: self.rows(table) for table in
                  ['versions', 'runs', 'events', 'target_snapshots', 'memberships']}
        replay = self.snapshot(self.records('alpha'), 30, run_id='explicit-request')
        self.assertEqual(replay['id'], first['run_id'])
        self.assertEqual({table: self.rows(table) for table in before}, before)
        self.assertEqual([r['body'] for r in self.store.search('beta', projects=['one'])], ['beta'])

    def test_stale_return_is_rejected_and_other_project_keeps_its_version(self):
        self.snapshot(self.records('alpha'), 10)
        self.snapshot(self.records('beta'), 20)
        other = self.store.register(platform='graph', original_id='other',
            kind='source', label='other', projects=['two'])
        self.store.ingest(other, [{'original_id': 'x', 'label': 'private', 'text': 'secret'}])
        with self.store.database() as db:
            other_before = tuple(db.execute('SELECT * FROM memberships WHERE target_id=?',
                (other,)).fetchone())
        for order, reason in [(19, 'stale'), (20, 'conflict')]:
            with self.assertRaisesRegex(RuntimeError, 'collection_source_revision_' + reason):
                self.snapshot(self.records('alpha'), order)
        self.snapshot(self.records('alpha'), 30)
        self.assertEqual([r['body'] for r in self.store.search('alpha', projects=['one'])], ['alpha'])
        self.assertEqual(self.store.search('secret', projects=['one']), [])
        self.assertEqual([r['body'] for r in self.store.search('secret', projects=['two'])], ['secret'])
        with self.store.database() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM memberships WHERE target_id=?',
                (other,)).fetchone()), other_before)


if __name__ == '__main__':
    unittest.main()
