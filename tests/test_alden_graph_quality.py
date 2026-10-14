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
from alden_graph_quality import audit, audit_annotations


class QualityTests(unittest.TestCase):
    def test_annotation_cancellation_poll_is_throttled_and_still_observes_later_cancellation(self):
        self.store.ingest(self.a,[self.annotated(str(i),['AI']) for i in range(100)])
        with patch('alden_graph_quality.time.monotonic',side_effect=[i*.001 for i in range(2000)]):
            calls=0
            def cancelled():
                nonlocal calls
                calls+=1
                return calls>=2
            with self.assertRaisesRegex(RuntimeError,'quality_cancelled'):
                audit_annotations(self.root,['one'],cancelled=cancelled)
            self.assertEqual(calls,2)
        calls=0
        def available():
            nonlocal calls
            calls+=1
            return False
        audit_annotations(self.root,['one'],cancelled=available)
        self.assertLess(calls,100)

    def test_annotation_scan_matches_verified_review_without_opening_retrieval_or_relations(self):
        self.store.ingest(self.a,[self.annotated('a',['AI']),self.annotated('b',['채용'])])
        full=audit(self.root,['one'],verify_sources=True,sample_cap=0)['source_annotation_review']
        (self.store.root/'retrieval.sqlite3').write_bytes(b'not a retrieval database')
        before=self.files();focused=audit_annotations(self.root,['one'])
        self.assertEqual(focused['source_annotation_review'],full)
        self.assertEqual(focused['coverage']['versions_scanned'],2)
        self.assertEqual(self.files(),before)
        with self.assertRaisesRegex(RuntimeError,'quality_cancelled'):
            audit_annotations(self.root,['one'],cancelled=lambda:True)
        with self.assertRaisesRegex(RuntimeError,'quality_scan_budget'):
            audit_annotations(self.root,['one'],max_rows=1)

    def annotated(self, name, topics=None, entities=None, body='동일한 원문 내용입니다. 날짜 2026-02-20, 단위와 띄어쓰기를 보존합니다.'):
        raw = {'localOriginalText': body}
        if topics is not None:
            raw['topics'] = topics
        if entities is not None:
            raw['entityTags'] = entities
        return self.record(name, body, raw=raw)

    def test_verified_annotation_review_distinguishes_sets_from_semantic_truth(self):
        self.store.ingest(self.a, [self.annotated('a', ['채용', 'AI'], ['하나저축은행']),
            self.annotated('b', ['AI', '채용'], ['신한은행']),
            self.annotated('c', ['공공기관'], ['하나저축은행'])])
        before = self.files()
        review = audit(self.root, ['one'], verify_sources=True)['source_annotation_review']
        self.assertEqual(review['counts'], {'source_versions_inspected': 3,
            'records_with_repeated_values': 0, 'same_projected_body_groups_compared': 1,
            'same_projected_body_groups_with_tag_differences': 1,
            'same_body_group_members_with_tag_differences': 3})
        self.assertEqual(review['status'], 'verified_retained_declarations')
        self.assertEqual(review['semantic_status'], 'unverified')
        self.assertEqual(review['samples'][0]['different_fields'], ['topics', 'entityTags'])
        self.assertEqual(review['fields']['topics']['body_groups_with_different_values'], 1)
        self.assertEqual(review['fields']['entityTags']['body_groups_with_different_values'], 1)
        self.assertNotIn('하나저축은행', json.dumps(review, ensure_ascii=False))
        self.assertEqual(self.files(), before)

    def test_annotation_review_preserves_case_spacing_and_counts_nfc_repetition(self):
        self.store.ingest(self.a, [self.annotated('a', ['cafe\u0301', 'café'], ['A', 'A']),
            self.annotated('b', ['café'], ['A']), self.annotated('c', ['Café'], ['A '])])
        review = audit(self.root, ['one'], verify_sources=True)['source_annotation_review']
        self.assertEqual(review['counts']['records_with_repeated_values'], 1)
        self.assertEqual(review['fields']['topics']['repeated_values'], 1)
        self.assertEqual(review['fields']['entityTags']['repeated_values'], 1)
        self.assertEqual(review['fields']['topics']['body_groups_with_different_values'], 1)
        self.assertEqual(review['fields']['entityTags']['body_groups_with_different_values'], 1)
        self.store.ingest(self.a, [self.annotated('c', ['café'], ['A'])])
        review = audit(self.root, ['one'], verify_sources=True)['source_annotation_review']
        self.assertEqual(review['counts']['same_projected_body_groups_with_tag_differences'], 0)

    def test_annotation_review_missing_unsupported_budget_and_scope_do_not_become_differences(self):
        self.store.ingest(self.a, [self.annotated('a', ['AI']), self.annotated('missing'),
            self.annotated('invalid', 'AI'), self.annotated('budget', ['x'] * 33),
            dict(self.annotated('metadata', ['다른 태그']), kind='metadata')])
        self.store.ingest(self.b, [self.annotated('other-project', ['다른 태그'])])
        denied = self.store.register(platform='graph', original_id='denied', kind='source',
                                     label='denied', projects=['one'])
        self.store.ingest(denied, [self.annotated('denied-record', ['다른 태그'])])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (denied,))
        before = self.files()
        review = audit(self.root, ['one'], verify_sources=True)['source_annotation_review']
        self.assertEqual(review['counts']['source_versions_inspected'], 4)
        self.assertEqual(review['counts']['same_projected_body_groups_compared'], 0)
        self.assertEqual(review['counts']['same_projected_body_groups_with_tag_differences'], 0)
        self.assertEqual({k: review['fields']['topics'][k] for k in
            ('declared', 'undeclared', 'unsupported', 'over_budget')},
            {'declared': 1, 'undeclared': 1, 'unsupported': 1, 'over_budget': 1})
        self.assertEqual(review['fields']['entityTags']['undeclared'], 4)
        self.assertEqual(self.files(), before)

    def test_annotation_review_hash_only_and_tampered_sources_are_not_inspected(self):
        self.store.ingest(self.a, [self.annotated('a', ['AI']), self.annotated('b', ['채용'])])
        review = audit(self.root, ['one'])['source_annotation_review']
        self.assertEqual(review['status'], 'not_inspected')
        self.assertEqual(review['counts']['source_versions_inspected'], 0)
        with self.store.database() as db:
            raw = db.execute('SELECT raw_path FROM versions WHERE document_id=?', (identity('graph', 'a'),)).fetchone()[0]
        (self.store.blobs / raw).write_text('{}')
        report = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(report['findings']['source_blob_invalid'], 1)
        self.assertEqual(report['source_annotation_review']['counts']['source_versions_inspected'], 1)
        self.assertEqual(report['source_annotation_review']['counts']['same_projected_body_groups_with_tag_differences'], 0)

    def test_annotation_review_sample_cap_does_not_change_total_or_cross_projects(self):
        for target, prefix in [(self.a, 'one'), (self.b, 'two')]:
            self.store.ingest(target, [self.annotated(prefix+'a', ['AI']),
                                     self.annotated(prefix+'b', ['채용'])])
        review = audit(self.root, ['one', 'two'], verify_sources=True, sample_cap=0)['source_annotation_review']
        self.assertEqual(review['counts']['source_versions_inspected'], 4)
        self.assertEqual(review['counts']['same_projected_body_groups_with_tag_differences'], 2)
        self.assertEqual(review['counts']['same_body_group_members_with_tag_differences'], 4)
        self.assertEqual(review['samples'], [])

    def test_float32_extremes_keep_nonfinite_and_unit_tolerance_findings(self):
        values={'nan':float('nan'),'inf':float('inf'),'largest':3.4e38,
                'inside':1.0005,'outside':1.0015,'zero':0.0}
        self.store.ingest(self.a,[self.record(name,'content') for name in values])
        for name,value in values.items():self.add_vector(self.version(name),raw=struct.pack('<f',value))
        before=self.files();report=audit(self.root,['one'],sample_cap=10)
        self.assertEqual(report['findings']['vector_nonfinite'],2)
        self.assertEqual(report['findings']['vector_not_unit'],3)
        self.assertEqual(report['findings'].get('vector_shape_invalid',0),0)
        self.assertEqual(self.files(),before)

    def test_body_matching_preserves_spacing_scope_and_source_exclusions(self):
        rows = [dict(self.record(name, body), label='') for name, body in
            [('a', 'cafe\u0301\r\n  날짜 2026-10-13'), ('b', 'café\n  날짜 2026-10-13'),
             ('spacing', 'café\n 날짜 2026-10-13')]]
        rows.append(dict(self.record('description', 'café\n  날짜 2026-10-13'),
            label='', coverage='existing privacy-filtered derived metadata; original bytes not opened'))
        self.store.ingest(self.a, rows)
        self.store.ingest(self.b, [dict(self.record('foreign', 'café\n  날짜 2026-10-13'), label='')])
        before = self.store.path.read_bytes()
        report = audit(self.root, ['one'], verify_sources=True, sample_cap=10)
        matches = report['body_matches']
        self.assertEqual(matches['counts']['eligible_current_versions'], 3)
        self.assertEqual(matches['stored_body']['groups'], 1)
        self.assertEqual(matches['stored_body']['distinct_document_members'], 2)
        self.assertEqual(set(matches['stored_body']['samples'][0]['document_ids']),
                         {identity('graph', name) for name in ('a', 'b')})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_stored_body_matches_do_not_require_a_title(self):
        body='서로 별도 기록인 원래 저장 본문입니다. 날짜와 단위를 그대로 유지합니다.\n  원래 들여쓰기'
        self.store.ingest(self.a,[{**self.record(name,body),'label':''} for name in ('body-a','body-b')])
        report=audit(self.root,['one'])
        self.assertEqual(report['body_matches']['stored_body']['groups'],1)
        self.assertEqual(report['body_matches']['counts']['without_substantive_label'],2)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'],0)

    def test_reference_body_matches_are_separate_from_content_and_keep_url_case(self):
        rows=[{**self.record(name,body),'label':''} for name,body in
            [('link-a','https://example.test/Path?ID=ABC'),('link-b','https://example.test/Path?ID=ABC'),
             ('different','https://example.test/path?ID=abc')]]
        self.store.ingest(self.a,rows)
        report=audit(self.root,['one'])
        self.assertEqual(report['body_matches']['reference_text']['groups'],1)
        self.assertEqual(report['body_matches']['stored_body']['groups'],0)
    def test_unread_metadata_notice_is_not_a_content_duplicate(self):
        notice = '경로만 색인한 파일. 본문은 읽지 않았습니다.'
        records = [dict(self.record(name, notice), kind='metadata',
                        label='shared.js', source_path=name + '/shared.js')
                   for name in ('first', 'second')]
        self.store.ingest(self.a, records)
        before = self.store.path.read_bytes()
        report = audit(self.root, ['one'])
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(report['metadata_matches']['within_context']['groups'], 1)
        self.assertEqual(report['counts']['metadata_only_current_versions'], 2)
        self.assertEqual(report['body_matches']['counts']['eligible_current_versions'], 0)
        self.assertEqual(report['body_matches']['stored_body']['groups'], 0)
        self.assertEqual(report['counts']['project_current_versions'], 2)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_unread_body_marker_is_excluded_without_a_kind_declaration(self):
        notice = '경로만 색인한 파일. 본문은 읽지 않았습니다.'
        self.store.ingest(self.a, [self.record('first', notice), self.record('second', notice)])
        report = audit(self.root, ['one'])
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(report['metadata_matches']['within_context']['groups'], 1)
        self.assertEqual(report['counts']['unread_body_marker_current_versions'], 2)

    def test_metadata_and_content_cannot_form_a_cross_context_content_pair(self):
        other = self.store.register(platform='graph', original_id='other', kind='source',
                                    label='other', projects=['one'])
        self.store.ingest(self.a, [dict(self.record('metadata', 'same text'), kind='metadata')])
        self.store.ingest(other, [self.record('content', 'same text')])
        report = audit(self.root, ['one'])
        self.assertEqual(report['duplicate_candidates']['cross_context_search_text']['groups'], 0)
        self.assertEqual(report['metadata_matches']['cross_context']['groups'], 0)
        self.assertEqual(report['counts']['noncontent_current_versions'], 1)

    def test_quoted_notice_inside_a_real_body_remains_a_content_candidate(self):
        text = '오류 메시지 분석: 경로만 색인한 파일. 본문은 읽지 않았습니다. 이후 본문 접근을 확인한다.'
        self.store.ingest(self.a, [self.record('first', text), self.record('second', text)])
        report = audit(self.root, ['one'])
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 1)
        self.assertEqual(report['metadata_matches']['within_context']['groups'], 0)
        self.assertEqual(report['counts']['noncontent_current_versions'], 0)

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
            # NFC and platform line endings are required; indentation is allowed.
            db.execute("UPDATE versions SET body=?", ('cafe\u0301\r\nline',))
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

    def test_ambiguous_or_overflowed_source_json_is_not_counted_as_verified(self):
        self.store.ingest(self.a, [self.record('bad'), self.record('healthy')])
        bad = self.version('bad')
        for payload in (
                b'{"text":"first","text":"second"}',
                b'{"nested":{"name":"first","na\\u006de":"second"}}',
                b'{"score":1e400}',
                b'{"nested":[{"score":-1e400}]}'):
            with self.subTest(payload=payload):
                sha = digest(payload)
                (self.store.blobs / (sha + '.json')).write_bytes(payload)
                with self.store.database() as db:
                    db.execute('UPDATE versions SET raw_sha256=?,raw_path=? WHERE id=?',
                               (sha, sha + '.json', bad['id']))
                before = self.files()
                report = audit(self.root, ['one'], verify_sources=True)
                self.assertEqual(report['findings'].get('source_blob_invalid'), 1)
                self.assertEqual(report['counts']['source_blobs_verified'], 1)
                self.assertEqual(report['counts']['project_current_versions'], 2)
                self.assertEqual(self.files(), before)

    def test_ambiguous_or_overflowed_relation_json_is_reported_without_deletion(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')], relations=[
            {'source_platform': 'graph', 'source_id': 'x', 'target_platform': 'graph',
             'target_id': 'y', 'type': 'reference', 'evidence': {'reason': 'reference'}}])
        for evidence in ('{"reason":"first","reason":"second"}',
                         '{"nested":[{"confidence":1e400}]}'):
            with self.subTest(evidence=evidence):
                with self.store.database() as db:
                    db.execute('UPDATE relations SET evidence=?,version=?',
                               (evidence, digest(evidence.encode())))
                before = self.files()
                report = audit(self.root, ['one'])
                self.assertEqual(report['findings'].get('relation_evidence_invalid'), 1)
                self.assertEqual(report['counts']['relations'], 1)
                self.assertEqual(self.files(), before)

    def test_duplicate_metadata_keys_are_reported_even_when_hashes_match_last_value(self):
        self.store.ingest(self.a, [self.record('x')])
        row = self.version('x')
        metadata = json.loads(row['metadata'])
        self.assertTrue(metadata)
        name = next(iter(metadata))
        ambiguous = '{' + json.dumps(name) + ':"discarded",' + row['metadata'][1:]
        self.assertEqual(json.loads(ambiguous), metadata)
        with self.store.database() as db:
            db.execute('UPDATE versions SET metadata=? WHERE id=?', (ambiguous, row['id']))
        before = self.files()
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings'].get('metadata_invalid'), 1)
        self.assertEqual(self.files(), before)

    def test_finite_exponents_and_repeated_keys_in_separate_objects_remain_valid(self):
        evidence = {'parts': [{'name': 'first', 'score': 1e300},
                              {'name': 'second', 'score': -1e-300}]}
        self.store.ingest(self.a, [self.record('x', raw=evidence), self.record('y')], relations=[
            {'source_platform': 'graph', 'source_id': 'x', 'target_platform': 'graph',
             'target_id': 'y', 'type': 'reference', 'evidence': evidence}])
        before = self.files()
        report = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['counts']['source_blobs_verified'], 2)
        self.assertEqual(report['counts']['relations'], 1)
        self.assertEqual(self.files(), before)


    def test_indentation_tabs_and_blank_lines_are_valid_distinct_content(self):
        originals = ['  item: 1 kg\n\tunit: box\n\nend',
                     ' item: 1 kg\n\tunit: box\n\nend',
                     '  item: 1 kg\n  unit: box\n\nend']
        self.store.ingest(self.a, [self.record(str(i), body) for i, body in enumerate(originals)])
        before = self.files()
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(report['counts']['project_current_versions'], 3)
        self.assertEqual(self.files(), before)

    def test_blank_or_incomplete_fields_do_not_establish_semantic_duplicates(self):
        records = [dict(self.record(name, ''), label='Untitled') for name in ('empty1', 'empty2')]
        records += [dict(self.record(name, '5 kg'), label='') for name in ('no-label1', 'no-label2')]
        self.store.ingest(self.a, records)
        report = audit(self.root, ['one'])
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(report['counts']['duplicate_text_ineligible'], 4)
        # Identical retained serialized input still counts as byte-only evidence.
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 2)
        self.assertEqual(report['findings'], {})

    def test_nfc_line_endings_equivalence_preserves_dates_units_and_indentation(self):
        self.store.ingest(self.a, [
            self.record('a', 'caf\u00e9\n2026-10-01: 5 kg'),
            self.record('b', 'cafe\u0301\r\n2026-10-01: 5 kg'),
            self.record('date', 'caf\u00e9\n2026-10-02: 5 kg'),
            self.record('unit', 'caf\u00e9\n2026-10-01: 5 g'),
            self.record('space', 'caf\u00e9\n2026-10-01: 5kg')])
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 1)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['distinct_document_members'], 2)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 0)

    def test_cross_target_or_provenance_matches_remain_separate_review_class(self):
        other = self.store.register(platform='graph', original_id='independent',
            kind='source', label='independent', projects=['one'], permission='review')
        self.store.ingest(self.a, [
            dict(self.record('same-a'), source={'author': 'A', 'revision': 1}),
            dict(self.record('same-b'), source={'author': 'A', 'revision': 1})])
        self.store.ingest(other, [
            dict(self.record('separate'), source={'author': 'B', 'revision': 2})])
        report = audit(self.root, ['one'])
        direct = report['duplicate_candidates']['same_normalized_search_text']
        across = report['duplicate_candidates']['cross_context_search_text']
        self.assertEqual(direct['groups'], 1)
        self.assertEqual(direct['distinct_document_members'], 2)
        self.assertEqual(across['groups'], 1)
        self.assertEqual(across['distinct_document_members'], 3)
        self.assertEqual(across['samples'][0]['context_count'], 2)
        self.assertEqual(report['findings'], {})

    def test_same_document_current_versions_are_scope_revisions_not_duplicates(self):
        other = self.store.register(platform='graph', original_id='independent',
            kind='source', label='independent', projects=['one'])
        self.store.ingest(self.a, [self.record('shared', 'revision A')])
        self.store.ingest(other, [self.record('shared', 'revision B')])
        report = audit(self.root, ['one'])
        self.assertEqual(report['counts']['project_current_versions'], 2)
        self.assertEqual(report['counts']['distinct_current_documents'], 1)
        self.assertEqual(report['scoped_revision_variants']['groups'], 1)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(report['duplicate_candidates']['cross_context_search_text']['groups'], 0)
        self.assertEqual(report['findings'], {})

    def test_wrong_raw_reference_is_structural_anomaly_not_byte_proof(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')])
        with self.store.database() as db:
            db.execute("UPDATE versions SET raw_path='0000.json' WHERE document_id=?",
                       (identity('graph', 'x'),))
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings']['source_reference_invalid'], 1)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 0)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 1)

    def test_relation_direction_is_independent_and_invalid_type_is_detected(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')], relations=[
            {'source_platform':'graph','source_id':'x','target_platform':'graph',
             'target_id':'y','type':'references','evidence':{'v':'x-to-y'}},
            {'source_platform':'graph','source_id':'y','target_platform':'graph',
             'target_id':'x','type':'references','evidence':{'v':'y-to-x'}}])
        good = audit(self.root, ['one'])
        self.assertEqual(good['counts']['relations'], 2)
        self.assertEqual(good['counts']['reciprocal_relation_pairs'], 1)
        self.assertEqual(good['findings'], {})
        with self.store.database() as db:
            db.execute("UPDATE relations SET type='' WHERE source=?", (identity('graph', 'x'),))
        bad = audit(self.root, ['one'])
        self.assertEqual(bad['findings']['relation_type_invalid'], 1)
        self.assertEqual(bad['counts']['reciprocal_relation_pairs'], 0)

    def test_vector_profile_mixed_dimensions_are_numeric_review_candidates(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')])
        self.add_vector(self.version('x'), raw=struct.pack('<f', 1.0))
        self.add_vector(self.version('y'), raw=struct.pack('<ff', 0.0, 1.0))
        report = audit(self.root, ['one'])
        self.assertEqual(report['counts']['vector_profiles_mixed_dimensions'], 1)
        self.assertEqual(report['findings'], {})
        self.assertEqual(report['coverage']['status'], 'complete')
        self.assertEqual(report['coverage']['memberships_scanned'], 2)
        self.assertEqual(report['coverage']['max_rows'], 100000)

    def test_raw_byte_groups_report_verified_vs_hash_only_evidence(self):
        self.store.ingest(self.a, [self.record('a'), self.record('b')])
        fast = audit(self.root, ['one'])
        verified = audit(self.root, ['one'], verify_sources=True)
        raw = fast['duplicate_candidates']['same_raw_bytes']
        self.assertEqual(raw['groups'], 1)
        self.assertEqual(raw['verified_groups'], 0)
        self.assertEqual(raw['hash_only_groups'], 1)
        self.assertEqual(raw['classification'], 'identical_canonical_record_hash_only')
        self.assertEqual(verified['duplicate_candidates']['same_raw_bytes']['classification'],
                         'identical_canonical_record_bytes_verified')
        self.assertEqual(verified['duplicate_candidates']['same_raw_bytes']['verified_groups'], 1)
        self.assertEqual(verified['duplicate_candidates']['same_raw_bytes']['hash_only_groups'], 0)
        self.assertEqual(verified['findings'], {})



    def test_distinct_authors_and_revisions_share_text_but_not_identity(self):
        self.store.ingest(self.a, [
            dict(self.record('author-1'), source={'author': 'writer 1', 'revision': 1}),
            dict(self.record('author-2'), source={'author': 'writer 2', 'revision': 2})])
        report = audit(self.root, ['one'])
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(report['duplicate_candidates']['cross_context_search_text']['groups'], 1)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 1)
        self.assertEqual(report['findings'], {})

    def test_bad_permission_storage_is_reported_without_collapsing_contexts(self):
        self.store.ingest(self.a, [self.record('x'), self.record('y')])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission=? WHERE target_id=?", (b'review', self.a))
        report = audit(self.root, ['one'])
        self.assertEqual(report['counts']['memberships'], 2)
        self.assertEqual(report['findings']['membership_context_invalid'], 2)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)


    def test_corrupted_identical_raw_blob_drops_verified_duplicate_candidate(self):
        self.store.ingest(self.a, [self.record('a'), self.record('b')])
        raw_name = self.version('a')['raw_path']
        (self.store.blobs / raw_name).write_bytes(b'invalid source replacement')
        before = self.files()
        unchecked = audit(self.root, ['one'])
        verified = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(unchecked['duplicate_candidates']['same_raw_bytes']['groups'], 1)
        self.assertEqual(unchecked['duplicate_candidates']['same_raw_bytes']['hash_only_groups'], 1)
        self.assertEqual(verified['duplicate_candidates']['same_raw_bytes']['groups'], 0)
        self.assertEqual(verified['findings']['source_blob_invalid'], 1)
        self.assertEqual(self.files(), before)

    def test_nonpositive_collection_time_is_numeric_integrity_finding(self):
        self.store.ingest(self.a, [self.record('timestamp')])
        with self.store.database() as db:
            db.execute("UPDATE versions SET collected_at=-1")
        report = audit(self.root, ['one'])
        self.assertEqual(report['findings']['collection_time_invalid'], 1)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 0)


    def test_placeholder_titles_and_bodies_do_not_form_meaning_candidates(self):
        incomplete = [
            dict(self.record('u1', 'same detail'), label='Untitled'),
            dict(self.record('u2', 'same detail'), label='Untitled'),
            dict(self.record('ellipsis1', '...'), label='named item'),
            dict(self.record('ellipsis2', '...'), label='named item'),
            dict(self.record('unavailable1', 'N/A'), label='named item'),
            dict(self.record('unavailable2', 'N/A'), label='named item')]
        self.store.ingest(self.a, incomplete)
        report = audit(self.root, ['one'])
        self.assertEqual(report['counts']['duplicate_text_ineligible'], 6)
        self.assertEqual(report['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(report['duplicate_candidates']['same_raw_bytes']['groups'], 3)
        self.assertEqual(report['findings'], {})


if __name__ == '__main__':
    unittest.main()
