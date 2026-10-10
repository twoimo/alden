import json
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, digest, identity
from alden_graph_quality import audit


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.store = CollectionStore(self.root)
        self.a = self.store.register(platform='graph', original_id='a', kind='source', label='a', projects=['one'])
        self.b = self.store.register(platform='graph', original_id='b', kind='source', label='b', projects=['two'])

    def record(self, name, text='same', raw=None):
        return {'platform': 'graph', 'original_id': name, 'label': 'label', 'text': text,
                'raw': {'text': text} if raw is None else raw}

    def test_candidates_keep_distinct_ids_and_skip_denied_sources_without_writes(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')])
        self.store.ingest(self.b, [self.record('secret')])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.b,))
        before = self.store.path.read_bytes()
        report = audit(self.root, ['one', 'two'], verify_sources=True)
        self.assertEqual(report['counts']['memberships'], 2)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 1)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 1)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups_by_project'], {'one': 1})
        self.assertEqual(report['topology_distribution']['one']['isolated'], 2)
        self.assertEqual(report['findings'], {})
        self.assertNotIn(identity('graph', 'secret'), json.dumps(report))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_target_current_version_wins_over_other_project_newer_global_document(self):
        self.store.ingest(self.a, [self.record('shared', 'first'), self.record('other', 'first')])
        self.store.ingest(self.b, [self.record('shared', 'newer')])
        one = audit(self.root, ['one']); two = audit(self.root, ['two'])
        self.assertEqual(one['duplicate_candidates']['same_normalized_search_text']['groups'], 1)
        self.assertEqual(two['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(one['counts']['project_current_versions'], 2)

    def test_corrupted_source_and_normalization_are_detected_without_repairs(self):
        self.store.ingest(self.a, [self.record('x')])
        with self.store.database() as db:
            row = db.execute('SELECT * FROM versions').fetchone()
            db.execute("UPDATE versions SET body='  unnormalized  '")
        (self.store.blobs / row['raw_path']).write_text('{}')
        report = audit(self.root, ['one'], verify_sources=True)
        for kind in ('source_blob_invalid', 'normalization_mismatch', 'projection_hash_mismatch', 'version_identity_mismatch'):
            self.assertEqual(report['findings'][kind], 1)
        self.assertEqual((self.store.blobs / row['raw_path']).read_text(), '{}')

    def test_same_entity_shared_memberships_are_not_duplicate_documents(self):
        self.store.ingest(self.a, [self.record('shared')]); self.store.ingest(self.b, [self.record('shared')])
        with self.store.database() as db:
            db.execute("INSERT INTO target_projects VALUES(?, 'one', 'local-private')", (self.b,))
        report = audit(self.root, ['one'])
        self.assertEqual(report['counts']['memberships'], 2)
        self.assertEqual(report['counts']['project_current_versions'], 1)
        self.assertEqual(report['counts']['distinct_current_documents'], 1)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 0)

    def test_retained_original_is_compared_to_raw_and_nonfinite_vector_is_flagged(self):
        self.store.ingest(self.a, [self.record('x', '', {'localOriginalText': 'actual original'})])
        with self.store.database() as db:
            row = db.execute('SELECT * FROM versions').fetchone()
        with closing(sqlite3.connect(self.store.root / 'retrieval.sqlite3')) as db, db:
            db.execute('CREATE TABLE texts(document_id,version,base_hash,raw_sha256,extraction,label,body,body_source)')
            db.execute('CREATE TABLE vectors(document_id,version,text_hash,model,endpoint,encoding,vector)')
            db.execute('INSERT INTO texts VALUES(?,?,?,?,?,?,?,?)',
                       (row['document_id'], row['id'], digest(b'label\n'), row['raw_sha256'], 'retained-local-original-text-v1', 'label', 'wrong original', 'retained_record.localOriginalText'))
            db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?,?)',
                       (row['document_id'], row['id'], digest(b'label\nwrong original'), 'model', 'local', 'unit', struct.pack('<f', float('nan'))))
        report = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(report['counts']['stored_body_empty'], 1)
        self.assertEqual(report['findings']['retained_text_source_mismatch'], 1)
        self.assertEqual(report['findings']['vector_text_binding_invalid'], 1)
        self.assertEqual(report['findings']['vector_nonfinite'], 1)

    def test_empty_evidence_and_unavailable_endpoint_are_reviewable_without_deletion(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')], relations=[
            {'source_platform': 'graph', 'source_id': 'x', 'target_platform': 'graph', 'target_id': 'y', 'type': 'reference'}])
        with self.store.database() as db:
            db.execute("UPDATE memberships SET availability='deleted' WHERE document_id=?", (identity('graph', 'y'),))
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings']['relation_evidence_empty'], 1)
        self.assertEqual(report['findings']['relation_endpoint_unavailable_in_scope'], 1)
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT active FROM relations').fetchone()[0], 1)

    def test_budget_and_scope_fail_closed_without_claiming_partial_success(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')])
        for projects in ([], [''], ['one'] * 17):
            with self.assertRaises(ValueError): audit(self.root, projects)
        with self.assertRaisesRegex(RuntimeError, 'scan_budget'):
            audit(self.root, ['one'], max_rows=1)
        with self.assertRaisesRegex(RuntimeError, 'scan_budget'):
            audit(self.root, ['one'], max_bytes=1)

    def test_missing_vector_bytes_are_reported_and_samples_cover_each_project(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')])
        self.store.ingest(self.b, [self.record('z'), self.record('w')])
        with self.store.database() as db:
            row = db.execute('SELECT * FROM versions WHERE document_id=?', (identity('graph', 'x'),)).fetchone()
        with closing(sqlite3.connect(self.store.root / 'retrieval.sqlite3')) as db, db:
            db.execute('CREATE TABLE vectors(document_id,version,text_hash,model,endpoint,encoding,vector)')
            db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?,?)',
                       (row['document_id'], row['id'], digest(b'label\nsame'), 'model', 'local', 'unit', None))
        report = audit(self.root, ['one', 'two'], sample_cap=1)
        self.assertEqual(report['findings']['vector_shape_invalid'], 1)
        candidates = report['duplicate_candidates']['same_raw_bytes']
        self.assertEqual({s['project'] for s in candidates['samples']}, {'one', 'two'})
        self.assertEqual(candidates['unique_document_ids'], 4)

    def version(self, name):
        with self.store.database() as db:
            return dict(db.execute('SELECT * FROM versions WHERE document_id=?',
                                   (identity('graph', name),)).fetchone())

    def add_vector(self, row, *, model='model', endpoint='local', encoding='unit', raw=None):
        with closing(sqlite3.connect(self.store.root / 'retrieval.sqlite3')) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors(document_id,version,text_hash,model,endpoint,encoding,vector)')
            db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?,?)',
                       (row['document_id'], row['id'], digest((row['label'] + '\n' + row['body']).encode()),
                        model, endpoint, encoding, struct.pack('<f', 1) if raw is None else raw))

    def files(self):
        # SQLite readers may create shared-memory locks and an empty WAL.
        # Compare durable files and all nonempty WAL content, not reader bookkeeping.
        return {str(path.relative_to(self.root)): digest(path.read_bytes())
                for path in self.root.rglob('*') if path.is_file()
                and not path.name.endswith('-shm')
                and not (path.name.endswith('-wal') and path.stat().st_size == 0)}

    def test_malformed_version_fields_preserve_healthy_candidates_and_source_bytes(self):
        self.store.ingest(self.a, [self.record(name) for name in ('bad', 'healthy-a', 'healthy-b')])
        row = self.version('bad')
        for field, value, finding in (
                ('body', b'bad body', 'version_text_invalid'),
                ('label', b'bad label', 'version_text_invalid'),
                ('raw_path', b'bad binding', 'version_binding_invalid'),
                ('metadata', b'{}', 'metadata_invalid')):
            with self.subTest(field=field):
                with self.store.database() as db:
                    db.execute(f'UPDATE versions SET {field}=? WHERE id=?', (value, row['id']))
                try:
                    before = self.files()
                    report = audit(self.root, ['one'], verify_sources=True)
                    self.assertEqual(report['counts']['memberships'], 3)
                    self.assertEqual(report['findings'][finding], 1)
                    candidates = report['duplicate_candidates']['same_normalized_search_text']['samples']
                    self.assertTrue(any({identity('graph', 'healthy-a'), identity('graph', 'healthy-b')}
                                        <= set(group['document_ids']) for group in candidates))
                    json.dumps(report, allow_nan=False)
                    self.assertEqual(self.files(), before)
                finally:
                    with self.store.database() as db:
                        db.execute(f'UPDATE versions SET {field}=? WHERE id=?', (row[field], row['id']))

    def test_deep_metadata_does_not_discard_other_current_versions(self):
        self.store.ingest(self.a, [self.record('bad'), self.record('healthy')])
        deep = '{"nest":' + '[' * 2000 + '0' + ']' * 2000 + '}'
        with self.store.database() as db:
            db.execute('UPDATE versions SET metadata=? WHERE id=?', (deep, self.version('bad')['id']))
        before = self.files()
        report = audit(self.root, ['one'])
        self.assertEqual(report['counts']['project_current_versions'], 2)
        # Newer CPython can parse deeper JSON; it must still detect the changed projection.
        self.assertTrue(report['findings'].get('metadata_invalid') == 1
                        or report['findings'].get('projection_hash_mismatch') == 1)
        self.assertEqual(self.files(), before)

    def test_malformed_vector_profiles_keep_healthy_vectors_and_json_report(self):
        self.store.ingest(self.a, [self.record('bad'), self.record('healthy')])
        self.add_vector(self.version('healthy'))
        bad = self.version('bad')
        for field, value in (('model', b'model'), ('endpoint', float('inf')), ('encoding', b'unit')):
            with self.subTest(field=field):
                self.add_vector(bad, **{field: value})
                try:
                    before = self.files()
                    report = audit(self.root, ['one'])
                    self.assertEqual(report['counts']['current_vectors'], 2)
                    self.assertEqual(report['findings']['vector_profile_invalid'], 1)
                    self.assertEqual(sum(sum(dimensions.values()) for dimensions in report['vector_profiles'].values()), 1)
                    json.dumps(report, allow_nan=False)
                    self.assertEqual(self.files(), before)
                finally:
                    with closing(sqlite3.connect(self.store.root / 'retrieval.sqlite3')) as db, db:
                        db.execute('DELETE FROM vectors WHERE document_id=?', (bad['document_id'],))

    def test_malformed_retained_text_is_flagged_and_falls_back_to_stored_body(self):
        self.store.ingest(self.a, [self.record('x')])
        row = self.version('x')
        with closing(sqlite3.connect(self.store.root / 'retrieval.sqlite3')) as db, db:
            db.execute('CREATE TABLE texts(document_id,version,base_hash,raw_sha256,extraction,body,body_source)')
            db.execute('INSERT INTO texts VALUES(?,?,?,?,?,?,?)',
                       (row['document_id'], row['id'], digest((row['label'] + '\n' + row['body']).encode()), row['raw_sha256'],
                        'retained-local-original-text-v1', b'malformed text', 'retained_record.localOriginalText'))
        self.add_vector(row)
        before = self.files()
        report = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(report['findings'], {'retained_text_binding_invalid': 1})
        self.assertEqual(report['counts']['current_vectors'], 1)
        self.assertEqual(self.files(), before)

    def test_malformed_evidence_keeps_relation_and_healthy_endpoint_counts(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')], relations=[
            {'source_platform': 'graph', 'source_id': 'x', 'target_platform': 'graph',
             'target_id': 'y', 'type': 'reference', 'evidence': {'reason': 'reference'}}])
        deep = '{"nest":' + '[' * 2000 + '0' + ']' * 2000 + '}'
        for evidence in (b'{}', deep):
            with self.subTest(storage=type(evidence).__name__):
                with self.store.database() as db:
                    db.execute('UPDATE relations SET evidence=?,version=?',
                               (evidence, digest(evidence if isinstance(evidence, bytes) else evidence.encode())))
                before = self.files()
                report = audit(self.root, ['one'])
                self.assertEqual(report['counts']['relations'], 1)
                self.assertEqual(report['topology_distribution']['one']['documents'], 2)
                if isinstance(evidence, bytes):
                    self.assertEqual(report['findings']['relation_evidence_invalid'], 1)
                else:
                    try:
                        json.loads(evidence)
                    except RecursionError:
                        self.assertEqual(report['findings']['relation_evidence_invalid'], 1)
                self.assertEqual(self.files(), before)

    def test_deep_source_is_retained_and_does_not_abort_healthy_source_verification(self):
        self.store.ingest(self.a, [self.record('bad'), self.record('healthy')])
        deep = ('{"nest":' + '[' * 2000 + '0' + ']' * 2000 + '}').encode()
        sha = digest(deep)
        (self.store.blobs / (sha + '.json')).write_bytes(deep)
        with self.store.database() as db:
            db.execute('UPDATE versions SET raw_sha256=?,raw_path=? WHERE id=?',
                       (sha, sha + '.json', self.version('bad')['id']))
        before = self.files()
        report = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(report['counts']['project_current_versions'], 2)
        self.assertGreaterEqual(report['counts']['source_blobs_verified'], 1)
        try:
            json.loads(deep)
        except RecursionError:
            self.assertEqual(report['findings']['source_blob_invalid'], 1)
        self.assertEqual(self.files(), before)

    def test_last_vector_cannot_turn_over_budget_scan_into_success(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')])
        baseline = audit(self.root, ['one'])['counts']['bytes_examined']
        with self.store.database() as db:
            last = dict(db.execute('SELECT * FROM versions ORDER BY document_id DESC').fetchone())
        self.add_vector(last, raw=struct.pack('<1024f', 1, *([0] * 1023)))
        before = self.files()
        with self.assertRaisesRegex(RuntimeError, '^quality_scan_budget$'):
            audit(self.root, ['one'], max_bytes=baseline + 2048)
        self.assertEqual(self.files(), before)

    def test_skipped_malformed_body_still_consumes_byte_budget(self):
        self.store.ingest(self.a, [self.record('bad')])
        with self.store.database() as db:
            db.execute('UPDATE versions SET body=?', (b'x' * 4096,))
        before = self.files()
        with self.assertRaisesRegex(RuntimeError, '^quality_scan_budget$'):
            audit(self.root, ['one'], max_bytes=2048)
        self.assertEqual(self.files(), before)

    def test_source_byte_budget_and_runtime_failures_are_not_data_findings(self):
        self.store.ingest(self.a, [self.record('x', raw={'localOriginalText': 'x' * 4096})])
        baseline = audit(self.root, ['one'])['counts']['bytes_examined']
        before = self.files()
        with self.assertRaisesRegex(RuntimeError, '^quality_scan_budget$'):
            audit(self.root, ['one'], verify_sources=True, max_bytes=baseline + 1024)
        with patch('alden_graph_quality._blob', side_effect=RuntimeError('quality_fixture_cancelled')):
            with self.assertRaisesRegex(RuntimeError, '^quality_fixture_cancelled$'):
                audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(self.files(), before)

    def test_malformed_binding_fields_cannot_bypass_byte_budget(self):
        self.store.ingest(self.a, [self.record('bad')])
        row = self.version('bad')
        for field in ('raw_path', 'raw_sha256', 'processing_version', 'projection_sha256'):
            with self.subTest(field=field):
                with self.store.database() as db:
                    db.execute(f'UPDATE versions SET {field}=? WHERE id=?', (b'x' * 4096, row['id']))
                try:
                    before = self.files()
                    with self.assertRaisesRegex(RuntimeError, '^quality_scan_budget$'):
                        audit(self.root, ['one'], max_bytes=2048)
                    self.assertEqual(self.files(), before)
                finally:
                    with self.store.database() as db:
                        db.execute(f'UPDATE versions SET {field}=? WHERE id=?', (row[field], row['id']))

    def test_relation_binding_consumes_byte_budget_before_findings(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')], relations=[
            {'source_platform': 'graph', 'source_id': 'x', 'target_platform': 'graph',
             'target_id': 'y', 'type': 'reference'}])
        with self.store.database() as db:
            db.execute('UPDATE relations SET version=?', (b'x' * 4096,))
        before = self.files()
        with self.assertRaisesRegex(RuntimeError, '^quality_scan_budget$'):
            audit(self.root, ['one'], max_bytes=2048)
        self.assertEqual(self.files(), before)

    def test_denied_malformed_rows_are_not_examined_or_reported(self):
        self.store.ingest(self.a, [self.record('healthy')])
        self.store.ingest(self.b, [self.record('secret')])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.b,))
            db.execute('UPDATE versions SET body=? WHERE id=?', (b'x' * 8192, self.version('secret')['id']))
        before = self.files()
        baseline = audit(self.root, ['one'])['counts']['bytes_examined']
        report = audit(self.root, ['one', 'two'], max_bytes=baseline)
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['counts']['memberships'], 1)
        self.assertNotIn(identity('graph', 'secret'), json.dumps(report))
        self.assertEqual(self.files(), before)


if __name__ == '__main__':
    unittest.main()
