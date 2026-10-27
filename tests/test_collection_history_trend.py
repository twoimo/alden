"""ST03: receipt-window aggregation in isolated temporary stores; no source reads."""
import json
import os
import sqlite3
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, read_action

DAY = 86400
START = 1791417600  # 2026-10-08T00:00:00Z


class CollectionHistoryTrendTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='youtube', original_id='channel', kind='channel',
                                          label='Allowed channel', projects=['one'])
        self.window = {'since': START, 'until': START + 14 * DAY}

    def ingest(self, at, records, **kwargs):
        with patch('alden_collection.time.time', return_value=at):
            return self.store.ingest(self.target, records, **kwargs)

    @staticmethod
    def record(key='a', text='First version'):
        return {'platform': 'youtube', 'original_id': key, 'label': 'Document ' + key, 'text': text}

    def read(self, **options):
        return read_action(self.root, 'collection-history', json.dumps(
            {'projects': ['one'], 'limit': 1, 'trend': self.window, **options}))

    def total(self, result, kind='added'):
        return sum(row[kind] for row in result['trend']['days'])

    def test_complete_calendar_window_is_independent_of_recent_tail_and_graph_slice(self):
        self.ingest(START + 60, [self.record(str(i)) for i in range(101)])
        result = self.read()
        self.assertEqual(len(result['items']), 1)
        self.assertIsNotNone(result['next'])
        self.assertEqual(self.total(result), 101)
        self.assertEqual(len(result['trend']['days']), 14)
        self.assertEqual(result['trend']['days'][0]['day'], '2026-10-08')
        self.assertEqual(result['trend']['days'][-1]['day'], '2026-10-21')
        self.assertEqual(result['trend']['scan'], 'complete')
        self.assertEqual(result['trend']['source_coverage'], 'unknown')
        self.assertEqual(result['trend']['window'], self.window)
        self.assertEqual(result['trend']['cursor'], result['cursor'])
        self.assertEqual(result['trend']['stream_id'], result['stream_id'])

    def test_after_page_progress_is_separate_from_complete_aggregate_snapshot(self):
        self.ingest(START + 1, [self.record(str(i)) for i in range(101)])
        first = self.read(after=0, limit=80)
        self.assertEqual(len(first['items']), 80)
        self.assertIsNotNone(first['next'])
        self.assertEqual(first['cursor'], first['items'][-1]['sequence'])
        self.assertGreater(first['snapshot_checkpoint']['cursor'], first['cursor'])
        self.assertEqual(first['trend']['cursor'], first['snapshot_checkpoint']['cursor'])
        self.assertEqual(self.total(first), 101)
        second = self.read(after=first['cursor'], limit=80, stream_id=first['stream_id'])
        self.assertEqual(second['snapshot_checkpoint'], first['snapshot_checkpoint'])
        self.assertEqual(second['trend']['days'], first['trend']['days'])
        self.assertEqual(self.total(second), 101)

    def test_unchanged_processing_index_and_published_do_not_multiply_documents(self):
        run = self.ingest(START + 1, [self.record()])
        self.ingest(START + 2, [self.record()])
        self.ingest(START + 3, [self.record(text='Revised')])
        with self.store.database() as db:
            row = db.execute("SELECT * FROM events WHERE stage='stored' AND run_id=?", (run['run_id'],)).fetchone()
            with patch('alden_collection.time.time', return_value=START + 5):
                self.store._event(db, run['run_id'], self.target, 'published', document_id=row['document_id'], version=row['version'], stored_event_id=row['event_id'])
        result = self.read()
        self.assertEqual(self.total(result), 1)
        self.assertEqual(self.total(result, 'revised'), 1)
        self.assertEqual(result['trend']['unclassified'], 0)

    def test_duplicate_receipts_and_out_of_order_arrival_use_one_canonical_transition(self):
        run = self.ingest(START + DAY - 1, [self.record()])
        self.ingest(START + 10, [self.record('older-arrives-later')])
        with self.store.database() as db:
            db.execute("""INSERT INTO events(event_id,run_id,target_id,stage,at,document_id,version,details)
                SELECT 'transport-copy',run_id,target_id,stage,?,document_id,version,details FROM events
                WHERE run_id=? AND stage='stored'""", (START + DAY + 1, run['run_id']))
        result = self.read()
        self.assertEqual(result['trend']['days'][0]['added'], 2)
        self.assertEqual(result['trend']['days'][1]['added'], 0)
        self.window = {'since': START + DAY, 'until': START + 2 * DAY}
        self.assertEqual(self.total(self.read(since=START + DAY)), 0)

    def test_exact_half_open_boundaries_midnight_and_non_utc_filter_endpoints(self):
        for i, at in enumerate((START - 1, START, START + DAY - 1, START + DAY, START + 14 * DAY)):
            self.ingest(at, [self.record(str(i))])
        result = self.read()
        self.assertEqual(self.total(result), 3)
        self.assertEqual(result['trend']['days'][0]['added'], 2)
        self.assertEqual(result['trend']['days'][1]['added'], 1)
        self.window = {'since': START + 15 * 3600, 'until': START + DAY + 15 * 3600}
        result = self.read(since=self.window['since'], until=self.window['until'])
        self.assertEqual(len(result['trend']['days']), 2)
        self.assertEqual(self.total(result), 2)

    def test_empty_and_missing_store_do_not_claim_source_coverage(self):
        result = self.read()
        self.assertEqual(self.total(result), 0)
        self.assertEqual(result['trend']['coverage'], 'retained_only')
        self.assertEqual(result['trend']['source_coverage'], 'unknown')
        self.assertEqual(result['trend']['relations'], None)
        missing = read_action(self.root / 'missing', 'collection-history', json.dumps({'trend': self.window}))
        self.assertEqual(missing['state'], 'not_configured')
        self.assertNotIn('trend', missing)
        self.assertFalse((self.root / 'missing').exists())

    def test_permission_project_target_platform_and_search_scope(self):
        self.ingest(START + 1, [self.record()])
        other = self.store.register(platform='youtube', original_id='private', kind='channel', label='Private', projects=['two'])
        with patch('alden_collection.time.time', return_value=START + 2):
            self.store.ingest(other, [self.record('private')])
        self.assertEqual(self.total(self.read()), 1)
        for options in ({'projects': []}, {'target_id': other}, {'platform': 'files'}, {'search': 'absent'}):
            with self.subTest(options=options):
                self.assertEqual(self.total(self.read(**options)), 0)
        self.assertEqual(self.total(self.read(search='Document a')), 1)
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.target,))
        with self.assertRaisesRegex(ValueError, 'collection_project_scope_invalid'):
            self.read()
        self.assertEqual(self.total(self.read(projects=[])), 0)

    def test_source_authority_guards_filter_receipts_without_visible_node_inference(self):
        self.ingest(START + 1, [self.record()])
        # Install exactly the SQL authority boundary used by recent_events.
        with self.store.database() as db:
            db.create_function('alden_source_allowed', 3, lambda target, doc, version: 0)
            trend = self.store._event_trend(db, 'alden_source_allowed(e.target_id,e.document_id,e.version)=1', [],
                                          {'stream_id': 'test', 'cursor': 100}, self.window)
        self.assertEqual(sum(day['added'] for day in trend['days']), 0)

    def test_protected_source_permission_is_rechecked_by_existing_reader_boundary(self):
        self.ingest(START + 1, [self.record()])
        with self.store.database() as db:
            db.execute("UPDATE targets SET platform='telegram' WHERE id=?", (self.target,))
        query = json.dumps({'projects': ['one'], 'trend': self.window})
        allowed = read_action(self.root, 'collection-history', query, source_access=lambda *args: True)
        denied = read_action(self.root, 'collection-history', query, source_access=lambda *args: False)
        self.assertEqual(self.total(allowed), 1)
        self.assertEqual(self.total(denied), 0)
        self.assertEqual(denied['items'], [])
        self.assertEqual(denied['trend']['source_coverage'], 'unknown')

    def test_malformed_persisted_receipt_fails_instead_of_returning_success_shaped_zero(self):
        self.ingest(START + 1, [self.record()])
        with self.store.database() as db:
            # Existing count triggers parse details; drop only in this isolated malformed fixture.
            triggers = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
            for name in triggers:
                db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
            db.execute("UPDATE events SET details='not json' WHERE stage='stored'")
        with self.assertRaises((sqlite3.OperationalError, ValueError)):
            self.read()

    def test_backfill_uses_storage_time_never_original_timestamp_or_origin_guess(self):
        record = {**self.record(), 'created_at': 123, 'updated_at': 456}
        self.ingest(START + 3 * DAY, [record], origin='historical-import')
        result = self.read()
        self.assertEqual(result['trend']['days'][3]['added'], 1)
        self.assertEqual(result['trend']['backfill_live'], 'unknown')

    def test_deletion_is_counted_only_from_explicit_receipt_relations_stay_unknown(self):
        self.ingest(START + 1, [self.record()], complete_snapshot=True, source_revision='a' * 64, source_order=1)
        self.ingest(START + 2, [], complete_snapshot=True, source_revision='b' * 64, source_order=2)
        result = self.read()
        self.assertEqual(self.total(result, 'removed'), 1)
        self.assertIsNone(result['trend']['relations'])
        with self.store.database() as db:
            db.execute("UPDATE events SET details=? WHERE stage='stored'", ('{"change":"relations_changed","added":999}',))
        result = self.read()
        self.assertEqual(self.total(result), 0)
        self.assertEqual(result['trend']['unclassified'], 2)

    def test_unfinished_or_deferred_change_is_not_reported_as_confirmed(self):
        run = self.ingest(START + 1, [self.record()])
        with self.store.database() as db:
            db.execute("UPDATE events SET details=? WHERE stage='stored'", ('{"change":"added","activity_deferred":true}',))
        result = self.read()
        self.assertEqual(self.total(result), 0)
        self.assertEqual(result['trend']['unclassified'], 1)
        with self.store.database() as db:
            row = db.execute("SELECT * FROM events WHERE stage='stored'").fetchone()
            with patch('alden_collection.time.time', return_value=START + DAY):
                self.store._event(db, run['run_id'], self.target, 'published', document_id=row['document_id'], version=row['version'], stored_event_id=row['event_id'])
        self.assertEqual(self.total(self.read()), 1)
        with self.store.database() as db:
            db.execute("""INSERT INTO events(event_id,run_id,target_id,stage,at,document_id,version,details)
                SELECT 'deferred-copy',run_id,target_id,stage,at,document_id,version,details FROM events WHERE stage='stored'""")
        self.assertEqual(self.total(self.read()), 1)
        with self.store.database() as db:
            db.execute("UPDATE runs SET state='failed' WHERE id=?", (run['run_id'],))
        self.assertEqual(self.total(self.read()), 0)

    def test_reset_pruning_rebuilds_retained_counts_without_claiming_full_coverage(self):
        self.ingest(START + 1, [self.record()])
        before = self.read()
        with self.store.database() as db:
            db.execute('DELETE FROM events')
        after = self.read(stream_id=before['stream_id'])
        self.assertTrue(after['reset'])
        self.assertNotEqual(after['stream_id'], before['stream_id'])
        self.assertEqual(self.total(after), 0)
        self.assertEqual(after['trend']['source_coverage'], 'unknown')

    def test_malformed_or_unbounded_window_fails_closed(self):
        for window in ({}, {'since': True, 'until': START}, {'since': START, 'until': START},
                       {'since': START, 'until': START + 32 * DAY}, {'since': -1, 'until': START},
                       {'since': START, 'until': float('inf')}, {'since': '0', 'until': START}):
            with self.subTest(window=window), self.assertRaisesRegex(ValueError, 'collection_trend_window_invalid'):
                self.read(trend=window)
        with self.assertRaisesRegex(ValueError, 'collection_trend_window_invalid'):
            self.read(since=START + DAY)


    def test_trend_rejects_incompatible_stage_without_changing_plain_history(self):
        self.ingest(START + 1, [self.record()])
        for stage in ('discovered', 'parsed', 'validated', 'published', 'indexed', 'failed', 'paused'):
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, 'collection_trend_stage_invalid'):
                self.read(stage=stage)
        self.assertEqual(self.total(self.read(stage='stored')), 1)
        plain = read_action(self.root, 'collection-history', json.dumps(
            {'projects': ['one'], 'stage': 'parsed', 'limit': 200}))
        self.assertTrue(plain['items'])
        self.assertTrue(all(row['stage'] == 'parsed' for row in plain['items']))
        self.assertNotIn('trend', plain)


if __name__ == '__main__':
    unittest.main()
