"""Independent receipt semantics for aggregate rewrites, using isolated stores.

Set ALDEN_TEST_TMPDIR to place every temporary fixture in an owned output root.
The oracle reads plain rows and uses Python grouping, not aggregate/candidate SQL.
Random coverage is finite: three seeds, 40 groups/seed, at most 240 events/seed.
"""
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_abort import AldenCancelled
from alden_collection import CollectionStore, read_action


DAY = 86400
START = 1791417600
KINDS = ('added', 'revised', 'removed')
MISSING = object()


def publication_text(value):
    """JSON scalar comparison to a TEXT receipt ID (fixture numeric domain only).

    JSON booleans become SQLite integers; null never equals an event ID. Float
    fixtures are small exactly representable values with unambiguous spelling.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def rowwise_oracle(db, checkpoint, window, *, projects=('one',), target_id=None,
                   platform=None, search='', allowed=lambda event: True):
    """Interpret retained rows independently of SQL joins and aggregation.

    Search fixtures use literal ASCII substrings, without SQL LIKE wildcards.
    Malformed JSON short-circuit cases have separate explicit characterization
    tests; this oracle only receives valid JSON evidence in its selected scope.
    """
    events = [dict(row) for row in db.execute('SELECT * FROM events ORDER BY sequence')]
    targets = {row['id']: dict(row) for row in db.execute('SELECT * FROM targets')}
    versions = {row['id']: dict(row) for row in db.execute('SELECT * FROM versions')}
    runs = {row['id']: row['state'] for row in db.execute('SELECT * FROM runs')}
    permitted = {row['target_id'] for row in db.execute('SELECT * FROM target_projects')
                 if row['project'] in projects and row['permission'] != 'denied'}
    retained = [event for event in events if event['sequence'] <= checkpoint]
    groups = defaultdict(list)
    for event in retained:
        target = targets.get(event['target_id'])
        if (event['stage'] != 'stored' or target is None or event['target_id'] not in permitted
            or target_id is not None and event['target_id'] != target_id
            or platform is not None and target['platform'] != platform or not allowed(event)):
            continue
        labels = (target['label'], versions.get(event['version'], {}).get('label', ''))
        if search and not any(search.lower() in label.lower() for label in labels):
            continue
        details = json.loads(event['details'])
        change = details.get('change')
        identity = (event['run_id'], event['target_id'], event['document_id'], event['version'], change)
        confirmed_member = details.get('activity_deferred') != 1
        if not confirmed_member:
            for proof in retained:
                if (proof['stage'] == 'published' and proof['run_id'] == event['run_id']
                    and proof['target_id'] == event['target_id']
                    and publication_text(json.loads(proof['details']).get('stored_event_id')) == event['event_id']):
                    confirmed_member = True
                    break
        groups[identity].append((event['at'], confirmed_member))
    days = {}
    for at in range(math.floor(window['since'] / DAY) * DAY, math.ceil(window['until']), DAY):
        day = datetime.fromtimestamp(at, timezone.utc).date().isoformat()
        days[day] = {'day': day, **dict.fromkeys(KINDS, 0)}
    unclassified = 0
    for (run, target, document, version, change), members in groups.items():
        first = sorted(at for at, confirmed in members)[0]
        if not window['since'] <= first < window['until'] or change == 'unchanged':
            continue
        if (change not in KINDS or not document or not version or runs.get(run) != 'complete'
            or not any(confirmed for at, confirmed in members)):
            unclassified += 1
        else:
            day = datetime.fromtimestamp(first, timezone.utc).date().isoformat()
            days[day][change] += 1
    return {'days': list(days.values()), 'unclassified': unclassified}


class CollectionHistoryTrendOptimizationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.target = self.target_fixture('alpha', 'Alpha channel')
        self.other = self.target_fixture('beta', 'Beta channel', platform='files')
        self.denied = self.target_fixture('denied', 'Denied channel', projects=['two', 'one'])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=? AND project='one'", (self.denied,))
        self.window = {'since': START, 'until': START + 3 * DAY}
        self.serial = 0

    def target_fixture(self, original_id, label, *, platform='youtube', projects=None):
        return self.store.register(platform=platform, original_id=original_id, kind='channel',
                                   label=label, projects=['one'] if projects is None else projects)

    def run_fixture(self, run='run', *, target=None, state='complete'):
        with self.store.database() as db:
            db.execute('INSERT INTO runs(id,target_id,origin,state,started_at) VALUES(?,?,?,?,?)',
                       (run, target or self.target, 'fixture', state, START))
        return run

    def version_fixture(self, document, version, label='Needle document'):
        with self.store.database() as db:
            db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?)',
                       (document, 'youtube', document, version, 'available'))
            db.execute('''INSERT INTO versions(id,document_id,raw_sha256,raw_path,label,body,metadata,collected_at)
                          VALUES(?,?,?,?,?,?,?,?)''',
                       (version, document, version, str(self.root / 'unused-fixture-blob'), label, '', '{}', START))

    def event(self, *, run='run', target=None, at=None, document='doc', version='version',
              stage='stored', change='added', deferred=MISSING, stored_id=MISSING,
              event_id=None, raw_details=None):
        self.serial += 1
        details = {}
        if change is not MISSING:
            details['change'] = change
        if deferred is not MISSING:
            details['activity_deferred'] = deferred
        if stored_id is not MISSING:
            details['stored_event_id'] = stored_id
        with self.store.database() as db:
            row = db.execute('''INSERT INTO events(event_id,run_id,target_id,stage,at,document_id,version,details)
                                VALUES(?,?,?,?,?,?,?,?) RETURNING *''',
                             (str(self.serial) if event_id is None else event_id, run,
                              self.target if target is None else target, stage, START + 1 if at is None else at,
                              document, version, json.dumps(details) if raw_details is None else raw_details)).fetchone()
        return dict(row)

    def publish(self, receipt, **overrides):
        return self.event(**{'run': receipt['run_id'], 'target': receipt['target_id'],
                             'document': receipt['document_id'], 'version': receipt['version'],
                             'stage': 'published', 'change': MISSING,
                             'stored_id': receipt['event_id'], **overrides})

    def aggregate(self, *, checkpoint=None, target=None, window=None):
        with self.store.database() as db:
            if checkpoint is None:
                checkpoint = db.execute('SELECT COALESCE(MAX(sequence),0) FROM events').fetchone()[0]
            return self.store._event_trend(db, 'e.target_id=?', [target or self.target],
                                          {'stream_id': 'fixture', 'cursor': checkpoint}, window or self.window)

    def read(self, **options):
        return read_action(self.root, 'collection-history', json.dumps(
            {'projects': ['one'], 'limit': 1, 'trend': self.window, **options}))

    def assert_oracle(self, trend, **scope):
        with self.store.database() as db:
            expected = rowwise_oracle(db, trend['cursor'], trend['window'], **scope)
        self.assertEqual(trend['days'], expected['days'])
        self.assertEqual(trend['unclassified'], expected['unclassified'])
        for key, value in {'schema': 1, 'basis': 'stored_receipt_utc', 'scan': 'complete',
                           'coverage': 'retained_only', 'source_coverage': 'unknown',
                           'backfill_live': 'unknown', 'relations': None}.items():
            self.assertEqual(trend[key], value, key)

    def assert_counts(self, trend, *, added=0, revised=0, removed=0, unclassified=0):
        self.assertEqual([sum(day[kind] for day in trend['days']) for kind in KINDS],
                         [added, revised, removed])
        self.assertEqual(trend['unclassified'], unclassified)

    def test_semantic_identity_includes_every_component_and_excludes_transport_id(self):
        self.run_fixture()
        self.run_fixture('later')
        base = self.event()
        self.event(at=START + DAY)  # Same identity, different receipt/timestamp.
        self.event(run='later')
        self.event(document='other-document')
        self.event(version='other-version')
        self.event(change='revised')
        self.event(target=self.other)
        self.publish(base)
        result = self.read()
        self.assert_counts(result['trend'], added=5, revised=1)
        self.assert_oracle(result['trend'])

    def test_earliest_retained_receipt_precedes_window_even_when_later_arrival_is_earlier(self):
        self.run_fixture()
        late = self.event(at=START + DAY)
        early = self.event(at=START - 1)
        self.event(at=START + 2 * DAY)
        self.event(document='at-start', at=START)
        self.event(document='at-end', at=self.window['until'])
        self.assert_counts(self.aggregate(checkpoint=late['sequence']), added=1)
        result = self.read(since=START, until=self.window['until'], after=0)
        self.assert_counts(result['trend'], added=1)
        self.assert_oracle(result['trend'])
        with self.store.database() as db:
            db.execute('DELETE FROM events WHERE event_id=?', (early['event_id'],))
        retained = self.read()['trend']
        self.assert_counts(retained, added=2)
        self.assert_oracle(retained)

    def test_duplicate_group_needs_only_one_nondeferred_or_one_published_member(self):
        self.run_fixture()
        self.event(document='mixed', deferred=True)
        self.event(document='mixed', deferred=False, at=START + DAY)
        self.event(document='published-copy', deferred=1)
        published_copy = self.event(document='published-copy', deferred=True, at=START + DAY)
        before = self.aggregate()
        self.assert_counts(before, added=1, unclassified=1)
        self.publish(published_copy, at=START + 99 * DAY)
        after = self.aggregate()
        self.assert_counts(after, added=2)
        self.assertEqual(after['days'][0]['added'], 2)
        self.assertEqual(after['days'][1]['added'], 0)
        self.assert_oracle(after, target_id=self.target)

    def test_exact_publication_proof_and_checkpoint_without_extra_document_version_conditions(self):
        self.run_fixture()
        self.run_fixture('other-run')
        receipt = self.event(deferred=True)
        self.publish(receipt, run='other-run')
        self.publish(receipt, target=self.denied)
        self.publish(receipt, target=self.other)
        self.publish(receipt, stored_id='different-receipt')
        self.publish(receipt, stage='indexed')
        before = self.aggregate()
        self.assert_counts(before, unclassified=1)
        proof = self.publish(receipt, document='different-document', version=None, at=START - DAY)
        self.assert_counts(self.aggregate(checkpoint=proof['sequence'] - 1), unclassified=1)
        after = self.aggregate(checkpoint=proof['sequence'])
        self.assert_counts(after, added=1)
        self.assert_oracle(after, target_id=self.target)

    def test_publication_scalar_identity_obeys_text_affinity_and_null_never_matches(self):
        cases = [('1', 1, True), ('1', True, True), ('0', False, True), ('1.0', 1.0, True),
                 ('0.5', 0.5, True), ('1', 1.0, False), ('01', 1, False), ('true', True, False),
                 ('1', '1', True), ('01', '01', True), ('1', '01', False),
                 ('null', None, False), ('', None, False), ('1', MISSING, False)]
        for index, (event_id, identity, matches) in enumerate(cases):
            with self.subTest(event_id=event_id, identity=repr(identity)):
                # Event IDs are globally unique; reuse each value only after
                # removing this isolated case, while preserving sequence order.
                with self.store.database() as db:
                    db.execute('DELETE FROM events')
                run = self.run_fixture('scalar-' + str(index))
                receipt = self.event(run=run, event_id=event_id, deferred=True)
                self.publish(receipt, event_id='proof-' + str(index), stored_id=identity)
                trend = self.aggregate()
                self.assert_counts(trend, added=int(matches), unclassified=int(not matches))
                self.assert_oracle(trend, target_id=self.target)

    def test_only_numeric_one_or_true_is_deferred(self):
        self.run_fixture()
        for index, value in enumerate((MISSING, None, False, 0, '1', 'true', 2, True, 1, 1.0)):
            self.event(document='doc-' + str(index), deferred=value)
        trend = self.aggregate()
        self.assert_counts(trend, added=7, unclassified=3)
        self.assert_oracle(trend, target_id=self.target)

    def test_run_completion_null_unknown_and_unchanged_do_not_claim_success(self):
        for state in ('complete', 'failed', 'paused', 'running'):
            run = self.run_fixture(state, state=state)
            self.publish(self.event(run=run, document=state, deferred=True))
        self.event(run='missing-run', document='missing')
        for index, change in enumerate((MISSING, None, '', 'relations_changed', 'novel', 1, True)):
            self.event(run='complete', document='unknown-' + str(index), change=change)
        self.event(run='complete', document=None)
        self.event(run='complete', version=None)
        self.event(run='complete', document='')
        self.event(run='complete', version='')
        self.event(run='missing-run', document=None, version=None, change='unchanged', deferred=True)
        trend = self.aggregate()
        self.assert_counts(trend, added=1, unclassified=15)
        self.assert_oracle(trend, target_id=self.target)

    def test_later_true_transition_back_to_same_version_remains_distinct_run(self):
        record = {'platform': 'youtube', 'original_id': 'real', 'label': 'Real', 'text': 'First'}
        for index, text in enumerate(('First', 'Second', 'First')):
            with patch('alden_collection.time.time', return_value=START + index * DAY + 1):
                self.store.ingest(self.target, [{**record, 'text': text}])
        with self.store.database() as db:
            receipts = db.execute("SELECT run_id,version FROM events WHERE stage='stored' ORDER BY sequence").fetchall()
        self.assertEqual(len(receipts), 3)
        self.assertEqual(receipts[0]['version'], receipts[2]['version'])
        self.assertNotEqual(receipts[0]['run_id'], receipts[2]['run_id'])
        trend = self.read()['trend']
        self.assert_counts(trend, added=1, revised=2)
        self.assert_oracle(trend)

    def test_project_target_platform_search_and_page_share_complete_scoped_aggregate(self):
        self.run_fixture()
        self.version_fixture('doc', 'version')
        self.version_fixture('other-doc', 'other-version', label='Haystack')
        for target in (self.target, self.other, self.denied):
            self.event(target=target)
            self.event(target=target, document='other-doc', version='other-version')
        cases = [({}, {}, 4), ({'projects': []}, {'projects': []}, 0),
                 ({'projects': ['two']}, {'projects': ['two']}, 2),
                 ({'target_id': self.target}, {'target_id': self.target}, 2),
                 ({'platform': 'files'}, {'platform': 'files'}, 2),
                 ({'search': 'needle'}, {'search': 'needle'}, 2),
                 ({'search': 'ALPHA'}, {'search': 'ALPHA'}, 2),
                 ({'search': 'absent'}, {'search': 'absent'}, 0),
                 ({'target_id': self.denied}, {'target_id': self.denied}, 0)]
        for options, scope, count in cases:
            with self.subTest(options=options):
                result = self.read(**options, after=0)
                self.assertTrue(result['ok'])
                self.assert_counts(result['trend'], added=count)
                self.assertEqual(result['trend']['cursor'], result['snapshot_checkpoint']['cursor'])
                self.assertEqual(result['trend']['stream_id'], result['snapshot_checkpoint']['stream_id'])
                self.assert_oracle(result['trend'], **scope)

    def test_source_authority_filters_stored_members_before_canonicalization(self):
        self.run_fixture()
        self.version_fixture('allowed', 'allowed-version')
        self.version_fixture('denied', 'denied-version')
        visible = self.event(document='allowed', version='allowed-version', deferred=True)
        self.publish(visible, document='denied', version='denied-version')
        self.event(document='denied', version='denied-version')
        with self.store.database() as db:
            db.execute("UPDATE targets SET platform='telegram' WHERE id=?", (self.target,))
        result = read_action(self.root, 'collection-history', json.dumps(
            {'projects': ['one'], 'limit': 1, 'trend': self.window}),
            source_access=lambda target, document, version, metadata: document == 'allowed')
        self.assert_counts(result['trend'], added=1)
        self.assert_oracle(result['trend'], allowed=lambda event: event['document_id'] == 'allowed')
        denied = read_action(self.root, 'collection-history', json.dumps(
            {'projects': ['one'], 'trend': self.window}), source_access=lambda *args: False)
        self.assert_counts(denied['trend'])
        self.assertEqual(denied['items'], [])

    def test_unrelated_or_future_malformed_publications_are_not_evidence(self):
        self.run_fixture()
        receipt = self.event(deferred=True)
        self.publish(receipt, run='unrelated-run', raw_details='not json')
        self.publish(receipt, target=self.denied, raw_details='not json')
        self.publish(receipt, target=self.other, raw_details='not json')
        checkpoint = self.aggregate()['cursor']
        self.assert_counts(self.aggregate(), unclassified=1)
        self.publish(receipt, raw_details='not json')
        self.assert_counts(self.aggregate(checkpoint=checkpoint), unclassified=1)

    def test_valid_publication_before_later_malformed_related_row_preserves_baseline_success(self):
        self.run_fixture()
        receipt = self.event(deferred=True)
        self.publish(receipt)
        self.publish(receipt, raw_details='not json')
        # Characterize the existing persisted-data boundary: finding valid proof
        # need not parse later malformed rows. Do not broaden failures eagerly.
        self.assert_counts(self.aggregate(), added=1)

    def test_malformed_needed_publication_raises_instead_of_success_shaped_zero(self):
        self.run_fixture()
        receipt = self.event(deferred=True)
        self.publish(receipt, raw_details='not json')
        with self.assertRaisesRegex(sqlite3.OperationalError, 'malformed JSON'):
            self.aggregate()

    def test_nondeferred_receipt_does_not_require_parsing_malformed_publication(self):
        self.run_fixture()
        receipt = self.event(deferred=False)
        self.publish(receipt, raw_details='not json')
        self.assert_counts(self.aggregate(), added=1)

    def test_out_of_window_corrupt_proof_still_raises_like_original_scan(self):
        self.run_fixture()
        # The canonical day precedes the window, so the original HAVING clause
        # dropped this group, yet its aggregate arguments were still evaluated
        # and the needed corrupt publication still aborted the scan.
        receipt = self.event(at=START - DAY, deferred=True)
        self.publish(receipt, raw_details='not json')
        with self.assertRaisesRegex(sqlite3.OperationalError, 'malformed JSON'):
            self.aggregate()

    def test_out_of_window_group_with_valid_proof_is_excluded_without_error(self):
        self.run_fixture()
        receipt = self.event(at=START - DAY, deferred=True)
        self.publish(receipt)
        self.assert_counts(self.aggregate())
        self.assert_oracle(self.aggregate())

    def test_sql_interrupt_and_read_denial_never_return_success_then_recover(self):
        self.run_fixture()
        self.event()
        with self.store.database() as db:
            checkpoint = {'stream_id': 'fixture', 'cursor': 1}
            db.set_progress_handler(lambda: 1, 1)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'interrupted'):
                    self.store._event_trend(db, 'e.target_id=?', [self.target], checkpoint, self.window)
            finally:
                db.set_progress_handler(None, 0)
            db.set_authorizer(lambda action, table, column, database, trigger:
                              sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'events'
                              else sqlite3.SQLITE_OK)
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    self.store._event_trend(db, 'e.target_id=?', [self.target], checkpoint, self.window)
            finally:
                db.set_authorizer(None)
        self.assert_counts(self.aggregate(), added=1)

    def test_public_boundary_cancellation_before_and_during_authority_check(self):
        self.run_fixture()
        self.version_fixture('doc', 'version')
        self.event()
        options = json.dumps({'projects': ['one'], 'trend': self.window})
        with self.assertRaises((AldenCancelled, RuntimeError)):
            read_action(self.root, 'collection-history', options, cancelled=lambda: True)
        with self.store.database() as db:
            db.execute("UPDATE targets SET platform='telegram' WHERE id=?", (self.target,))
        cancelled = False

        def revoke_during_source_check(*args):
            nonlocal cancelled
            cancelled = True
            return True

        with self.assertRaisesRegex((AldenCancelled, RuntimeError), 'collection_cancelled'):
            read_action(self.root, 'collection-history', options,
                        source_access=revoke_during_source_check, cancelled=lambda: cancelled)
        self.assertTrue(cancelled)

    def random_case(self, seed):
        rng = random.Random(seed)
        for index, state in enumerate(('complete', 'complete', 'paused', 'failed')):
            self.run_fixture('random-' + str(index), state=state)
        for index in range(5):
            self.version_fixture('doc-' + str(index), 'v-' + str(index),
                                 label='Needle' if index % 2 else 'Haystack')
        for group in range(40):
            run = rng.choice(['random-0', 'random-1', 'random-2', 'random-3', 'missing'])
            target = rng.choice([self.target, self.other, self.denied])
            document = rng.choice(['doc-' + str(i) for i in range(5)] + [None])
            version = rng.choice(['v-' + str(i) for i in range(5)] + [None])
            change = rng.choice([*KINDS, 'unchanged', 'relations_changed', None])
            for copy in range(rng.randint(1, 3)):
                receipt = self.event(run=run, target=target, document=document, version=version, change=change,
                                     at=START + rng.choice([-1, 0, 1, DAY - 1, DAY, 2 * DAY, 3 * DAY]),
                                     deferred=rng.choice([True, False, None, '1']))
                if rng.random() < .55:
                    self.publish(receipt, run=run if rng.random() < .8 else 'unrelated',
                                 target=target if rng.random() < .8 else self.denied,
                                 stored_id=receipt['event_id'] if rng.random() < .8 else 'missing')
        with self.store.database() as db:
            count = db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.assertLessEqual(count, 240)
        windows = [self.window, {'since': START + .5, 'until': START + DAY + .5},
                   {'since': START + DAY, 'until': START + 3 * DAY}]
        checkpoints = sorted({0, count // 3, 2 * count // 3, count})
        for checkpoint in checkpoints:
            for window in windows:
                for target in (self.target, self.other):
                    with self.subTest(seed=seed, checkpoint=checkpoint, window=window, target=target):
                        trend = self.aggregate(checkpoint=checkpoint, window=window, target=target)
                        self.assert_oracle(trend, target_id=target)
        for options, scope in [({}, {}), ({'search': 'needle'}, {'search': 'needle'}),
                               ({'projects': ['two']}, {'projects': ['two']})]:
            self.assert_oracle(self.read(**options)['trend'], **scope)

    def test_seeded_rowwise_differential_seed_11(self):
        self.random_case(11)

    def test_seeded_rowwise_differential_seed_37(self):
        self.random_case(37)

    def test_seeded_rowwise_differential_seed_83(self):
        self.random_case(83)


if __name__ == '__main__':
    unittest.main()
