"""Portable production regression contracts for retained collection trends.

Fixtures contain synthetic rows only. ALDEN_TEST_TMPDIR optionally selects the
fixture root; no saved review artifacts, live state or baseline checkout needed.
"""
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore

START = 1791417600
WINDOW = {'since': START, 'until': START + 86400}


class CollectionHistoryTrendOptimizationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='trend-review-', dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(Path(self.temp.name) / 'snapshot.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.addCleanup(self.db.close)
        # Exact production events columns/affinities and its three event indexes.
        # Only columns consulted by _event_trend are needed in the joined tables.
        self.db.executescript("""
            CREATE TABLE targets(id TEXT PRIMARY KEY);
            CREATE TABLE versions(id TEXT PRIMARY KEY);
            CREATE TABLE runs(id TEXT PRIMARY KEY,target_id TEXT,state TEXT);
            CREATE TABLE events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,target_id TEXT NOT NULL,stage TEXT NOT NULL,
                at REAL NOT NULL,document_id TEXT,version TEXT,details TEXT NOT NULL);
            CREATE INDEX event_run ON events(run_id,sequence);
            CREATE INDEX event_document ON events(document_id,target_id,stage,sequence DESC);
            CREATE INDEX event_target_sequence ON events(target_id,sequence);
            INSERT INTO targets VALUES('target'),('other');
            INSERT INTO runs VALUES('run','target','complete'),('other-run','other','complete');
        """)
        self.serial = 0

    def reset(self):
        self.db.execute('DELETE FROM events')
        self.db.execute('DELETE FROM sqlite_sequence')

    def event(self, event_id='receipt', *, stage='stored', details=None, target='target', run='run', at=START + 1):
        self.serial += 1
        if details is None:
            details = '{"change":"added","activity_deferred":true}'
        self.db.execute('INSERT INTO events(event_id,run_id,target_id,stage,at,document_id,version,details) VALUES(?,?,?,?,?,?,?,?)',
                        (event_id, run, target, stage, at, 'doc', 'version', details))

    def publication(self, details='{"stored_event_id":"receipt"}', **kwargs):
        self.event('proof-' + str(self.serial), stage='published', details=details, **kwargs)

    def read_trend(self, checkpoint=None):
        self.db.commit()
        self.db.execute('BEGIN')
        self.db.execute('PRAGMA query_only=ON')
        try:
            if checkpoint is None:
                checkpoint = self.db.execute('SELECT COALESCE(MAX(sequence),0) FROM events').fetchone()[0]
            return CollectionStore._event_trend(self.db, 'e.target_id=?', ['target'],
                                              {'stream_id': 'isolated-review', 'cursor': checkpoint}, dict(WINDOW))
        finally:
            self.db.rollback()
            self.db.execute('PRAGMA query_only=OFF')

    def assert_counts(self, expected, checkpoint=None):
        trend = self.read_trend(checkpoint)
        self.assertEqual(tuple(sum(day[key] for day in trend['days'])
                               for key in ('added', 'revised', 'removed')) + (trend['unclassified'],), expected)
        self.assertEqual([day['day'] for day in trend['days']], ['2026-10-08'])
        for key, value in {'schema': 1, 'basis': 'stored_receipt_utc', 'scan': 'complete',
                           'coverage': 'retained_only', 'source_coverage': 'unknown',
                           'backfill_live': 'unknown', 'relations': None}.items():
            self.assertEqual(trend[key], value, key)

    @unittest.skipIf(sqlite3.sqlite_version_info < (3, 42, 0), 'SQLite JSON5 requires 3.42+')
    def test_json5_publications_keep_json_extract_acceptance(self):
        forms = [('{stored_event_id:"receipt"}', 'receipt'),
                 ("{'stored_event_id':'receipt'}", 'receipt'),
                 ('{"stored_event_id":"receipt",}', 'receipt'),
                 ('{/* note */"stored_event_id":"receipt"}', 'receipt'),
                 ('{"stored_event_id":// note\n"receipt"}', 'receipt'),
                 ('{"stored_event_id":0x10}', '16'),
                 ('{"stored_event_id":+1}', '1'),
                 ('{"stored_event_id":.5}', '0.5'),
                 ('{"stored_event_id":1.}', '1.0'),
                 ('{"stored_event_id":Infinity}', 'Inf')]
        for details, receipt in forms:
            with self.subTest(details=details):
                self.reset()
                self.event(receipt)
                self.publication(details)
                self.assert_counts((1, 0, 0, 0))

    @unittest.skipIf(sqlite3.sqlite_version_info < (3, 42, 0), 'SQLite JSON5 requires 3.42+')
    def test_nonmatching_json5_before_strict_matching_proof(self):
        self.event()
        self.publication('{stored_event_id:"someone-else"}')
        self.publication()
        self.assert_counts((1, 0, 0, 0))

    def test_scalar_proofs_follow_text_affinity_not_python_str(self):
        # SQLite changed REAL-to-TEXT precision across versions. Obtain this
        # spelling from the engine, while keeping exact integer/nonmatch cases.
        overflow_text = self.db.execute("SELECT CAST(json_extract(?, '$') AS TEXT)",
                                        ('9223372036854775808',)).fetchone()[0]
        cases = [('true','1',True),('false','0',True),('true','true',False),
                 ('1','1',True),('1','01',False),('1','1.0',False),('1.0','1.0',True),('1.0','1',False),
                 ('-0.0','0.0',True),('-0.0','-0.0',False),('0','0',True),('-1','-1',True),
                 ('1e20','1.0e+20',True),('1e20','1e+20',False),('1e-7','1.0e-07',True),
                 ('9223372036854775807','9223372036854775807',True),
                 ('9223372036854775808',overflow_text,True),
                 ('9223372036854775808','9223372036854775808',False),
                 ('1e999','Inf',True),('-1e999','-Inf',True),
                 ('null','null',False),('null','',False),('""','',True),
                 ('"01"','01',True),('"01"','1',False),('"1\\u0000x"','1\x00x',True),
                 ('"한글"','한글',True),('"é"','e\u0301',False),
                 ('[ 1, 2 ]','[1,2]',True),('{ "id": 1 }','{"id":1}',True)]
        for raw, receipt, matches in cases:
            with self.subTest(raw=raw, receipt=receipt):
                self.reset()
                self.event(receipt)
                self.publication('{"stored_event_id":' + raw + '}')
                self.assert_counts((int(matches), 0, 0, int(not matches)))

    def test_malformed_proof_shortcircuit_follows_connection_scan_direction(self):
        for reverse in (False, True):
            self.db.execute('PRAGMA reverse_unordered_selects=' + ('ON' if reverse else 'OFF'))
            for proof_first in (False, True):
                with self.subTest(reverse=reverse, proof_first=proof_first):
                    self.reset()
                    self.event()
                    if proof_first:
                        self.publication()
                    self.publication('not-json')
                    if not proof_first:
                        self.publication()
                    if reverse == proof_first:
                        with self.assertRaisesRegex(sqlite3.OperationalError, 'malformed JSON'):
                            self.read_trend()
                    else:
                        self.assert_counts((1, 0, 0, 0))

    @unittest.skipIf(sqlite3.sqlite_version_info < (3, 42, 0), 'SQLite JSON5 requires 3.42+')
    def test_shortcircuit_ignores_later_json5_after_strict_proof(self):
        self.event()
        self.publication()
        self.publication('{stored_event_id:"someone-else"}')
        self.assert_counts((1, 0, 0, 0))

    def test_unrelated_or_future_malformed_proof_is_not_scanned(self):
        for scope in ('other-target', 'other-run', 'future'):
            with self.subTest(scope=scope):
                self.reset()
                self.event()
                self.publication()
                cutoff = self.db.execute('SELECT MAX(sequence) FROM events').fetchone()[0]
                options = {'target': 'other'} if scope == 'other-target' else {'run': 'other-run'} if scope == 'other-run' else {}
                self.publication('not-json', **options)
                self.assert_counts((1, 0, 0, 0), cutoff if scope == 'future' else None)

    def test_nondeferred_receipt_avoids_invalid_publications(self):
        self.event(details='{"change":"added","activity_deferred":false}')
        self.publication('not-json')
        self.assert_counts((1, 0, 0, 0))

    def test_text_and_blob_timestamps_keep_sql_window_comparison(self):
        for at in ('not-a-timestamp', sqlite3.Binary(b'not-a-timestamp')):
            with self.subTest(storage=type(at).__name__):
                self.reset()
                self.event(at=at, details='{"change":"added"}')
                self.assert_counts((0, 0, 0, 0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
