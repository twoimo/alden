"""Strict parsing and evidence/profile isolation for bounded quality scans."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import struct
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, digest, encoded
from alden_content_evidence import DERIVED_SUMMARY_COVERAGE
import alden_graph_quality as quality
from alden_graph_quality import _json, audit


class StrictQualityJsonTests(unittest.TestCase):
    def test_text_bytes_encodings_and_results_remain_independent(self):
        value = {'한글': ['café', -0.25, {'value': 1e300}], 'empty': {}}
        text = json.dumps(value, ensure_ascii=False)
        inputs = [text, text.encode(), bytearray(text.encode())]
        inputs.extend(text.encode(encoding) for encoding in
                      ('utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32'))
        for raw in inputs:
            with self.subTest(type=type(raw).__name__):
                decoded = _json(raw)
                self.assertEqual(decoded, value)
                decoded['한글'][2]['value'] = 'changed'
                self.assertEqual(_json(raw), value)

    def test_invalid_records_do_not_poison_the_next_parse(self):
        cases = ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"nested":{"a":1,"a":2}}',
                 '[NaN]', '{"x":Infinity}', '-Infinity', '{"x":[1e400]}',
                 '1e400', 'true false', '"unterminated', '\ufeff{}', '{"x":"\x00"}']
        for text in cases:
            for raw in (text, text.encode()):
                with self.subTest(raw=repr(raw)):
                    # A byte BOM remains valid under json.loads encoding detection.
                    if text.startswith('\ufeff') and isinstance(raw, bytes):
                        self.assertEqual(_json(raw), {})
                    else:
                        with self.assertRaises(ValueError):
                            _json(raw)
                    self.assertEqual(_json('{"ok":[1,2.5]}'), {'ok': [1, 2.5]})
        for raw in (None, 1, [], {}):
            with self.assertRaises(TypeError):
                _json(raw)

    def test_concurrent_records_keep_nested_objects_and_failures_isolated(self):
        def parse(index):
            if index % 7 == 0:
                with self.assertRaisesRegex(ValueError, 'duplicate_json_key'):
                    _json('{"same":1,"same":2}')
            value = {'index': index, 'nested': {'same': [index, '한글']}}
            decoded = _json(json.dumps(value, ensure_ascii=False))
            self.assertEqual(decoded, value)
            return decoded
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(parse, range(400)))
        rows[0]['nested']['same'].append('only first')
        self.assertEqual(rows[1]['nested']['same'], [1, '한글'])

    def test_overlapping_threads_own_distinct_decoders(self):
        barrier = Barrier(8)
        def parse(index):
            self.assertEqual(_json('"NaN"'), 'NaN')
            decoder = quality._JSON_DECODERS.decoder
            barrier.wait(timeout=5)
            self.assertEqual(_json(str(index)), index)
            return decoder
        with ThreadPoolExecutor(max_workers=8) as pool:
            decoders = list(pool.map(parse, range(8)))
        self.assertEqual(len({id(decoder) for decoder in decoders}), 8)


class QualityEvidenceReuseTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='graph', original_id='quality-cpu',
            kind='source', label='quality', projects=['one'])

    def records(self, body, original, **metadata):
        self.store.ingest(self.target, [dict(platform='graph', original_id=str(index),
            label='저장된 제목', text=body, raw={'localOriginalText': original}, **metadata)
            for index in range(2)])
        with self.store.database() as db:
            versions = [dict(row) for row in db.execute('SELECT * FROM versions')]
        with closing(sqlite3.connect(self.store.root / 'retrieval.sqlite3')) as db, db:
            db.execute('CREATE TABLE texts(document_id,version,base_hash,raw_sha256,extraction,body,body_source)')
            db.executemany('INSERT INTO texts VALUES(?,?,?,?,?,?,?)', [
                (row['document_id'], row['id'], digest((row['label']+'\n'+body).encode()),
                 row['raw_sha256'], 'retained-local-original-text-v1', original,
                 'retained_record.localOriginalText') for row in versions])

    def test_equal_body_different_verified_provenance_keeps_stored_class(self):
        body = '실제 저장된 내용을 그대로 보존합니다.'
        self.records(body, body, coverage=DERIVED_SUMMARY_COVERAGE)
        report = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['counts']['noncontent_current_versions'], 0)
        self.assertEqual(report['body_matches']['counts']['eligible_current_versions'], 0)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 1)

    def test_different_cached_body_does_not_upgrade_stored_reference(self):
        self.records('https://example.invalid/OriginalCase?id=A', '별도로 보존된 실제 본문입니다.')
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['body_matches']['counts']['reference_only_current_versions'], 2)
        self.assertEqual(report['body_matches']['counts']['eligible_current_versions'], 0)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 1)

    def test_more_than_64_profiles_keep_each_vector_dimension_and_exact_descriptor(self):
        self.store.ingest(self.target, [dict(platform='graph', original_id=str(index),
            label='제목', text='같은 본문', raw={'text': '같은 본문'}) for index in range(132)])
        with self.store.database() as db:
            versions = [dict(row) for row in db.execute('SELECT * FROM versions ORDER BY rowid')]
        expected = {}
        with closing(sqlite3.connect(self.store.root / 'retrieval.sqlite3')) as db, db:
            db.execute('CREATE TABLE vectors(document_id,version,text_hash,model,endpoint,encoding,vector)')
            for index, row in enumerate(versions):
                profile = ('Model' + str(index // 2), 'local/Case', 'unit')
                dimension = index % 2 + 1
                raw = struct.pack('<f', 1.) if dimension == 1 else struct.pack('<ff', 1., 0.)
                db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?,?)', (row['document_id'], row['id'],
                    digest((row['label']+'\n'+row['body']).encode()), *profile, raw))
                expected.setdefault(digest(encoded(profile)), {})[dimension] = 1
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['counts']['current_vectors'], 132)
        self.assertEqual(report['counts']['vector_profiles_mixed_dimensions'], 66)
        self.assertEqual(report['vector_profiles'], expected)


if __name__ == '__main__':
    unittest.main()
