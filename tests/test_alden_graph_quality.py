import json
from pathlib import Path
import sqlite3
import struct
import sys
from tempfile import TemporaryDirectory
import unittest

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
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
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
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
            db.execute('CREATE TABLE vectors(document_id,version,text_hash,model,endpoint,encoding,vector)')
            db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?,?)',
                       (row['document_id'], row['id'], digest(b'label\nsame'), 'model', 'local', 'unit', None))
        report = audit(self.root, ['one', 'two'], sample_cap=1)
        self.assertEqual(report['findings']['vector_shape_invalid'], 1)
        candidates = report['duplicate_candidates']['same_raw_bytes']
        self.assertEqual({s['project'] for s in candidates['samples']}, {'one', 'two'})
        self.assertEqual(candidates['unique_document_ids'], 4)


if __name__ == '__main__':
    unittest.main()
