"""Independent public contract tests for the read-only collection quality action.

All collection records, callbacks and database mutations here are synthetic and
confined to temporary state roots.  These tests never use a user's source store.
"""

import copy
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import alden_graph_quality as quality
import alden_graph_quality_action as action
from alden_collection import CollectionStore, digest, identity
from alden_normalization_evidence import validate_declared_summary


COUNTS = {
    "memberships", "project_current_versions", "relations",
    "distinct_current_documents", "bytes_examined",
    "noncontent_current_versions", "metadata_only_current_versions", "unread_body_marker_current_versions", "metadata_description_current_versions", "reference_only_current_versions", "empty_body_current_versions",
}
CANDIDATES = {
    "same_raw_bytes": "hash_references_only",
    "same_normalized_search_text": "review_candidate",
    "cross_context_search_text": "review_candidate",
}
PRIVATE_MARKERS = (
    "private-label-sentinel", "private-body-sentinel",
    "private-author-sentinel", "private-token-sentinel",
    "https://private.example.invalid/", "denied-record-sentinel",
)


class QualityActionContractTests(unittest.TestCase):
    def test_empty_body_is_counted_without_hiding_title_or_mutating_source(self):
        self.store.ingest(self.target(), [
            {'platform': 'graph', 'original_id': 'only-title', 'label': '찾을 수 있는 영상 제목', 'text': ''},
            {'platform': 'graph', 'original_id': 'body', 'label': '자료', 'text': '실제로 보존된 짧은 본문'},
        ])
        before = self.store.path.read_bytes()
        result = action.read_action(self.root, self.project_query('one'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['counts']['project_current_versions'], 2)
        self.assertEqual(result['counts']['empty_body_current_versions'], 1)
        self.assertEqual(result['counts']['noncontent_current_versions'], 1)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_annotation_inspection_is_explicit_and_keeps_hash_summary_separate(self):
        body='같은 보존 원문입니다. 채용 일정을 별도로 확인합니다.'
        rows=[{'platform':'graph','original_id':name,'label':'원문','text':body,
            'raw':{'localOriginalText':body,'topics':[tag],'entityTags':[]}}
            for name,tag in [('annotation-a','채용'),('annotation-b','AI')]]
        self.store.ingest(self.target(),rows)
        before=self.store.path.read_bytes()
        ordinary=action.read_action(self.root,self.project_query('one'))
        self.assertTrue(ordinary['ok']);self.assertNotIn('source_annotation_review',ordinary)
        query=json.dumps({'scope':'project','project':'one','include_annotation_review':True})
        with patch.object(action,'audit',wraps=quality.audit) as watched, patch.object(
                action,'audit_annotations',wraps=quality.audit_annotations) as annotations:
            result=action.read_action(self.root,query)
        self.assertTrue(result['ok'],result)
        self.assertEqual(watched.call_count,0)
        self.assertEqual(annotations.call_count,1)
        review=result['source_annotation_review']
        self.assertEqual(review['counts']['same_projected_body_groups_with_tag_differences'],1)
        self.assertEqual(review['semantic_status'],'unverified')
        self.assertEqual(result['scope']['source_verification'],'retained_canonical_json')
        self.assertEqual(result['kind'],'source_annotation_review')
        self.assertNotIn('samples',review)
        self.assertFalse(any(review['binding_findings'].values()))
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_annotation_inspection_rejects_partial_or_semantic_verdict_reports(self):
        self.store.ingest(self.target(),[{'platform':'graph','original_id':'annotation','label':'원문','text':'원문 내용',
            'raw':{'localOriginalText':'원문 내용','topics':['AI']}}])
        query=json.dumps({'scope':'project','project':'one','include_annotation_review':True})
        for failure in ['semantic_status','coverage','missing_field']:
            def broken(*args,**options):
                report=quality.audit_annotations(*args,**options)
                if failure=='semantic_status':report['source_annotation_review']['semantic_status']='verified'
                elif failure=='coverage':report['coverage']['status']='partial'
                else:del report['source_annotation_review']['fields']['topics']['declared']
                return report
            with self.subTest(failure=failure),patch.object(action,'audit_annotations',side_effect=broken):
                self.assert_error(action.read_action(self.root,query),'quality_report_unavailable',mode='project',project='one')

    def test_annotation_inspection_exposes_source_byte_failures(self):
        self.store.ingest(self.target(),[{'platform':'graph','original_id':'annotation','label':'원문','text':'원문 내용',
            'raw':{'localOriginalText':'원문 내용','topics':['AI']}}])
        with self.store.database() as db:raw=db.execute('SELECT raw_path FROM versions WHERE document_id=?',
            (identity('graph','annotation'),)).fetchone()[0]
        (self.store.blobs/raw).write_text('{}')
        result=action.read_action(self.root,json.dumps({'scope':'project','project':'one','include_annotation_review':True}))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['source_annotation_review']['binding_findings']['source_blob_invalid'],1)
        self.assertEqual(result['source_annotation_review']['counts']['source_versions_inspected'],0)

    def test_annotation_flag_rejects_wrong_type(self):
        for value in ['true',1,None,[]]:
            with self.subTest(value=value):self.assert_error(action.read_action(self.root,
                json.dumps({'scope':'project','project':'one','include_annotation_review':value})), 'quality_query_invalid')

    def comparison_query(self, project=None):
        value={'scope':'project','project':project} if project else {'scope':'all'}
        return json.dumps({**value,'include_samples':True})

    def test_comparison_is_explicit_source_bound_and_read_only(self):
        target=self.target();self.store.ingest(target,[self.record('a'),self.record('b')])
        before=self.durable_files()
        ordinary=action.read_action(self.root,self.union_query())
        self.assertNotIn('comparison_samples',ordinary)
        result=action.read_action(self.root,self.comparison_query())
        self.assertTrue(result['ok'],result)
        samples=result['comparison_samples'];self.assertEqual(samples['coverage'],'bounded_sample')
        self.assertEqual([r['kind'] for r in samples['groups']],['canonical_record'])
        for group in samples['groups']:
            self.assertEqual(len(group['records']),2)
            self.assertEqual({r['document_id'] for r in group['records']},{identity('graph','a'),identity('graph','b')})
            self.assertEqual(group['status'],'review_only_not_confirmed_duplicate')
            for record in group['records']:
                self.assertEqual(record['body'],'private-body-sentinel')
                self.assertNotIn('source_url',record)
                self.assertNotIn('private-token-sentinel',json.dumps(record))
                self.assertEqual(record['basis'],'permitted_current_stored_record')
        self.assertEqual(self.durable_files(),before)

    def test_matching_summary_does_not_claim_original_equivalence(self):
        target=self.target()
        self.store.ingest(target,[self.record('a',raw={'localOriginalText':'original A'}),
                                  self.record('b',raw={'localOriginalText':'original B'})])
        result=action.read_action(self.root,self.comparison_query());self.assertTrue(result['ok'],result)
        group=result['comparison_samples']['groups'][0];self.assertEqual(group['kind'],'stored_body')
        self.assertEqual({r['body'] for r in group['records']},{'original A','original B'})
        self.assertEqual({r['matched_body'] for r in group['records']},{'private-body-sentinel'})
        self.assertEqual(group['status'],'review_only_not_confirmed_duplicate')

    def test_source_tags_are_individually_bound_unverified_and_never_copy_metadata(self):
        target=self.target()
        self.store.ingest(target,[self.record('a',raw={'topics':['medical'],'entityTags':['school'],
            'credential':'DO_NOT_DISPLAY'}),self.record('b',raw={'topics':['construction'],'entityTags':['public_corp']})])
        before=self.durable_files()
        ordinary=action.read_action(self.root,self.union_query())
        self.assertNotIn('source_annotations',json.dumps(ordinary))
        result=action.read_action(self.root,self.comparison_query())
        self.assertTrue(result['ok'],result)
        records=result['comparison_samples']['groups'][0]['records']
        values={r['original_id']:r['source_annotations'] for r in records}
        self.assertEqual(values['a']['fields']['topics'],{'state':'declared','values':['medical']})
        self.assertEqual(values['b']['fields']['topics'],{'state':'declared','values':['construction']})
        for r in records:
            self.assertEqual(r['source_annotations']['basis'],'verified_retained_record')
            self.assertEqual(r['source_annotations']['semantic_status'],'unverified')
        self.assertNotIn('DO_NOT_DISPLAY',json.dumps(result))
        self.assertEqual(self.durable_files(),before)

    def test_undeclared_empty_malformed_and_budgeted_tags_are_distinct(self):
        from alden_entity_resolution import _declared_source_annotations
        for source,state in [({},'undeclared'),({'topics':[]},'declared'),
            ({'topics':None},'unsupported'),({'topics':['x']*33},'over_budget'),
            ({'topics':['x'*129]},'over_budget'),({'topics':[True]},'unsupported')]:
            with self.subTest(source=source):
                result=_declared_source_annotations(source)['fields']['topics']
                self.assertEqual(result,{'state':state,'values':[]})
        result=_declared_source_annotations({'topics':['A','A','a','A B']})
        self.assertEqual(result['fields']['topics']['values'],['A','A','a','A B'])

    def test_tag_preview_cannot_claim_verified_source_without_blob_validation(self):
        from alden_entity_resolution import _source_rows
        with self.store.database() as db:
            with self.assertRaisesRegex(ValueError,'annotations_require_verified_source'):
                _source_rows(db,self.store,'one',[],read_blobs=False,show_records=True,include_annotations=True)

    def test_corrupt_pair_source_fails_without_disclosing_partial_records(self):
        target=self.target();self.store.ingest(target,[self.record('a'),self.record('b')])
        blob=next(self.store.blobs.iterdir());blob.write_text('{"changed":true}')
        result=action.read_action(self.root,self.comparison_query())
        self.assert_error(result,'quality_comparison_unavailable',mode='all')
        self.assertNotIn('private-body-sentinel',json.dumps(result))

    def test_revoked_permission_after_audit_does_not_expose_pair(self):
        target=self.target();self.store.ingest(target,[self.record('a'),self.record('b')])
        original=action.audit
        def revoke(*args,**kwargs):
            result=original(*args,**kwargs)
            with sqlite3.connect(self.store.path) as db:
                db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?",(target,))
            return result
        with patch.object(action,'audit',side_effect=revoke):
            result=action.read_action(self.root,self.comparison_query())
        self.assert_error(result,'quality_comparison_unavailable',mode='all')

    def test_changed_pair_after_audit_requires_new_comparison(self):
        target=self.target();self.store.ingest(target,[self.record('a'),self.record('b')])
        original=action.audit
        def change(*args,**kwargs):
            result=original(*args,**kwargs)
            self.store.ingest(target,[self.record('a',text='different source body')])
            return result
        with patch.object(action,'audit',side_effect=change):
            self.assert_error(action.read_action(self.root,self.comparison_query()),
                'quality_comparison_unavailable',mode='all')

    def test_ambiguous_current_versions_are_not_chosen_arbitrarily(self):
        a,b=self.target(name='a'),self.target(name='b')
        self.store.ingest(a,[self.record('x',raw={'revision':1,'id':'x'}),self.record('y',raw={'revision':1,'id':'y'})])
        self.store.ingest(b,[self.record('x',raw={'revision':2,'id':'x'}),self.record('y',raw={'revision':2,'id':'y'})])
        self.assert_error(action.read_action(self.root,self.comparison_query()),
            'quality_comparison_unavailable',mode='all')

    def test_pair_preview_clips_unicode_without_mutating_source(self):
        target=self.target();body='🦊'*5000
        self.store.ingest(target,[self.record('a',text=body),self.record('b',text=body)])
        before=self.durable_files();result=action.read_action(self.root,self.comparison_query())
        self.assertTrue(result['ok'],result)
        for group in result['comparison_samples']['groups']:
            for record in group['records']:
                self.assertEqual(record['body'],'🦊'*4000)
                self.assertTrue(record['truncated']);self.assertTrue(record['matched_body_truncated'])
        self.assertEqual(self.durable_files(),before)

    def test_pair_samples_cover_projects_without_exceeding_budget(self):
        for i in range(7):
            target=self.target(project=f'p{i}',name=f't{i}')
            self.store.ingest(target,[self.record(f'a{i}'),self.record(f'b{i}')])
        result=action.read_action(self.root,self.comparison_query());self.assertTrue(result['ok'],result)
        groups=result['comparison_samples']['groups']
        self.assertEqual(len(groups),6);self.assertEqual(len({g['project'] for g in groups}),6)

    def test_noncontent_raw_matches_do_not_crowd_out_substantive_comparisons(self):
        for i in range(4):
            target=self.target(project=f'p{i}',name=f't{i}')
            rows=[]
            for j in range(2):
                raw={'content_indexed':False,'kind':'file','marker':j}
                rows.extend([self.record(f'm{i}-{j}-a',text='index notice',raw=raw),
                             self.record(f'm{i}-{j}-b',text='index notice',raw=raw)])
            rows.extend([self.record(f'a{i}',text='shared substantive summary',raw={'localOriginalText':'original A'}),
                         self.record(f'b{i}',text='shared substantive summary',raw={'localOriginalText':'original B'})])
            self.store.ingest(target,rows)
        result=action.read_action(self.root,self.comparison_query());self.assertTrue(result['ok'],result)
        samples=result['comparison_samples'];self.assertEqual({g['project'] for g in samples['groups']},{'p0','p1','p2','p3'})
        self.assertGreater(samples['skipped_noncontent_groups'],6)
        self.assertEqual(samples['examined_pairs'],len(samples['groups'])+samples['skipped_noncontent_groups'])
        self.assertLessEqual(samples['examined_pairs'],20)

    def test_pair_preview_cancellation_does_not_publish_partial_records(self):
        import alden_entity_resolution as resolution
        target=self.target();self.store.ingest(target,[self.record('a'),self.record('b')])
        state={'cancelled':False};original=resolution._source_rows
        def stop(*args,**kwargs):
            result=original(*args,**kwargs);state['cancelled']=True;return result
        with patch.object(resolution,'_source_rows',side_effect=stop):
            self.assert_error(action.read_action(self.root,self.comparison_query(),
                cancelled=lambda:state['cancelled']),'quality_cancelled',mode='all')

    def test_declared_dates_and_quantities_are_syntax_observations_and_never_merge(self):
        target = self.target()
        rows = [dict(self.record('a'), published_at='2026-02-30', quantity={'value':'1000','unit':'g'}),
                dict(self.record('b'), published_at='2026-10-13T10:00:00', quantity={'value':'1','unit':'kg'})]
        self.store.ingest(target, rows)
        before = self.durable_files()
        result = action.read_action(self.root, self.union_query())
        self.assert_success(result, mode='all', project=None, projects=['one'])
        declared = result['normalization']['declared_fields']
        self.assertEqual(declared['time']['invalid'], 1)
        self.assertEqual(declared['time']['ambiguous'], 1)
        self.assertEqual(declared['quantity']['normalized'], 2)
        self.assertEqual(result['counts']['distinct_current_documents'], 2)
        self.assertEqual(declared['truth_status'], 'syntax_conversion_not_semantic_verdict')
        self.assertEqual(self.durable_files(), before)

    def test_declared_summary_cannot_hide_missing_invalid_or_overbudget_fields(self):
        target = self.target(); self.store.ingest(target,[dict(self.record('a'),published_at='2026-10-13')])
        report = quality.audit(self.root,['one'],verify_sources=False,sample_cap=0)
        mutations = [lambda r:r.pop('declared_field_normalization'),
            lambda r:r['declared_field_normalization'].update(truth_status='verified'),
            lambda r:r['declared_field_normalization']['time'].update(normalized=2),
            lambda r:r['declared_field_normalization']['quantity'].update(omitted=1)]
        for mutate in mutations:
            altered = copy.deepcopy(report); mutate(altered)
            with patch.object(action,'audit',return_value=altered):
                self.assertEqual(action.read_action(self.root,self.union_query())['reason'],'quality_report_unavailable')

    def test_untitled_body_and_reference_matches_are_counted_without_private_samples(self):
        target = self.target()
        body = 'private-body-sentinel: 별도 출처에 반복된 안내문입니다. 내용 일치만 관찰합니다.'
        link = 'https://private.example.invalid/Path?ID=ABC'
        self.store.ingest(target, [dict(self.record(name, text=text), label='')
            for name, text in [('body-a', body), ('body-b', body), ('link-a', link), ('link-b', link)]])
        before = self.durable_files()
        result = action.read_action(self.root, self.union_query())
        self.assert_success(result, mode='all', project=None, projects=['one'])
        self.assertEqual(result['body_matches'], {
            'policy': 'stored-body-match-v1',
            'basis': 'current_stored_body_projection_not_original_equivalence',
            'counts': {'eligible_current_versions': 2, 'without_substantive_label': 2,
                       'reference_only_current_versions': 2},
            'stored_body': {'groups': 1, 'members': 2, 'status': 'text_match_review_only'},
            'reference_text': {'groups': 1, 'members': 2, 'status': 'text_match_review_only'},
        })
        self.assertEqual(result['duplicate_candidates']['same_normalized_search_text']['groups'], 0)
        self.assertEqual(self.durable_files(), before)

    def test_missing_impossible_or_confirmed_body_coverage_cannot_become_success(self):
        target = self.target()
        self.store.ingest(target, [self.record('healthy')])
        base = quality.audit(self.root, ['one'], verify_sources=False, sample_cap=0)
        def missing(report): del report['body_matches']
        mutations = [missing,
            lambda r: r['body_matches']['counts'].update(eligible_current_versions=True),
            lambda r: r['body_matches']['counts'].update(without_substantive_label=2),
            lambda r: r['body_matches']['counts'].update(reference_only_current_versions=2),
            lambda r: r['body_matches']['stored_body'].update(status='confirmed'),
            lambda r: r['body_matches']['stored_body'].update(groups=1, distinct_document_members=1),
            lambda r: r['body_matches']['reference_text'].update(groups=0, distinct_document_members=1),
            lambda r: r['body_matches'].update(basis='verified_original_equivalence'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                report = copy.deepcopy(base)
                mutate(report)
                with patch.object(action, 'audit', return_value=report):
                    self.assert_error(action.read_action(self.root, self.union_query()),
                        'quality_report_unavailable', mode='all')

    def test_union_project_added_during_scan_cannot_return_partial_all_scope(self):
        target=self.target();self.store.ingest(target,[self.record('healthy')])
        real_audit=action.audit
        def concurrent_registration(*args,**kwargs):
            report=real_audit(*args,**kwargs)
            self.target('new-project',name='new-target')
            return report
        with patch.object(action,'audit',side_effect=concurrent_registration):
            self.assert_error(action.read_action(self.root,self.union_query()),
                'quality_scope_unavailable',mode='all')

    def test_impossible_or_boolean_coverage_cannot_become_completed_observation(self):
        target=self.target();self.store.ingest(target,[self.record('healthy')])
        base=quality.audit(self.root,['one'],verify_sources=False,sample_cap=0)
        for mode in ('over_budget','boolean'):
            report=copy.deepcopy(base)
            if mode=='over_budget':
                report['counts']['memberships']=100001
                report['coverage']['memberships_scanned']=100001
            else:
                report['coverage']['memberships_scanned']=True
            with patch.object(action,'audit',return_value=report):
                self.assert_error(action.read_action(self.root,self.project_query('one')),
                    'quality_report_unavailable',mode='project',project='one')

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = CollectionStore(self.root)

    def target(self, project="one", *, name="target", permission="local-private"):
        return self.store.register(
            platform="graph", original_id=name, kind="source",
            label="private-label-sentinel", projects=[project],
            permission=permission,
        )

    @staticmethod
    def record(name, *, text="private-body-sentinel", raw=None):
        return {
            "platform": "graph", "original_id": name,
            "label": "private-label-sentinel", "text": text,
            "raw": {"text": text, "token": "private-token-sentinel"} if raw is None else raw,
            "source": {"author": "private-author-sentinel",
                       "url": "https://private.example.invalid/"},
        }

    @staticmethod
    def project_query(project):
        return json.dumps({"scope": "project", "project": project}, ensure_ascii=False)

    @staticmethod
    def union_query():
        return '{"scope":"all"}'

    def durable_files(self):
        # SQLite readers can create ephemeral -shm files and empty WALs.
        return {
            str(path.relative_to(self.root)): digest(path.read_bytes())
            for path in self.root.rglob("*")
            if path.is_file() and not path.name.endswith("-shm")
            and not (path.name.endswith("-wal") and path.stat().st_size == 0)
        }

    def assert_error(self, result, reason, *, mode=None, project=None):
        self.assertEqual(result, {
            "ok": False, "schema": 3, "reason": reason,
            "scope": {"requested_scope": mode, "requested_project": project},
        })
        json.dumps(result, allow_nan=False)

    def assert_success(self, result, *, mode, project, projects):
        self.assertEqual(set(result), {
            "ok", "schema", "scope", "checked_at", "elapsed_ms", "counts",
            "findings", "duplicate_candidates", "numeric_review_candidates",
            "normalization", "metadata_matches", "content_evidence", "body_matches",
        })
        self.assertIs(result["ok"], True)
        self.assertEqual(result["schema"], 3)
        self.assertEqual(result["scope"], {
            "requested_scope": mode, "requested_project": project,
            "projects": projects,
            "coverage": "permitted_current_collection_memberships",
            "filters": "project_only",
            "source_verification": "hash_references_only",
        })
        for key in ("checked_at", "elapsed_ms"):
            self.assertIsInstance(result[key], (int, float))
            self.assertTrue(math.isfinite(result[key]))
            self.assertGreaterEqual(result[key], 0)
        self.assertGreater(result["checked_at"], 0)
        self.assertEqual(set(result["counts"]), COUNTS)
        self.assertIsInstance(result["findings"], dict)
        for metric in (*result["counts"].values(), *result["findings"].values()):
            self.assertIs(type(metric), int)
            self.assertGreaterEqual(metric, 0)
            self.assertLessEqual(metric, 9_007_199_254_740_991)
        self.assertEqual(set(result["duplicate_candidates"]), set(CANDIDATES))
        for name, status in CANDIDATES.items():
            candidate = result["duplicate_candidates"][name]
            self.assertEqual(set(candidate), {"groups", "status"})
            self.assertEqual(candidate["status"], status)
            self.assertIs(type(candidate["groups"]), int)
            self.assertGreaterEqual(candidate["groups"], 0)
        self.assertEqual(
            set(result["numeric_review_candidates"]), {"mixed_vector_dimensions"})
        mixed = result["numeric_review_candidates"]["mixed_vector_dimensions"]
        self.assertEqual(set(mixed), {"profiles", "status"})
        self.assertIs(type(mixed["profiles"]), int)
        self.assertGreaterEqual(mixed["profiles"], 0)
        self.assertEqual(mixed["status"], "review_candidate")
        self.assertEqual({key: result['normalization'][key] for key in ('method', 'semantic_validity')}, {
            "method": "NFC_line_endings_preserving_body_spacing",
            "semantic_validity": "unverified",
        })
        self.assertEqual(set(result['normalization']), {'method', 'semantic_validity', 'declared_fields'})
        declared = result['normalization']['declared_fields']
        self.assertEqual(validate_declared_summary(declared, result['counts']['project_current_versions']), declared)

        # Neither audit samples nor source/target identifiers, raw material,
        # auth or URLs are allowed in the public response.
        def keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)

        self.assertFalse({
            "samples", "sample", "body", "label", "target_id", "document_id",
            "document_ids", "version_ids", "url", "auth", "source_blob",
            "sha256", "provenance_sha256", "model", "author",
        } & set(keys(result)))
        output = json.dumps(result, ensure_ascii=False, allow_nan=False)
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, output)

    def test_actual_classifications_counts_and_bounded_read_only_audit(self):
        first = self.target(name="first")
        second = self.target(name="second")
        denied = self.target(name="denied", permission="denied")
        raw = {"text": "matching canonical bytes", "token": "private-token-sentinel"}
        self.store.ingest(first, [
            self.record("first-a", text="matching text", raw=raw),
            self.record("first-b", text="matching text", raw=raw),
        ], relations=[{
            "source_platform": "graph", "source_id": "first-a",
            "target_platform": "graph", "target_id": "first-b",
            "type": "references", "evidence": {"basis": "synthetic"},
        }])
        self.store.ingest(second, [self.record(
            "other-context", text="matching text", raw={"different": True})])
        self.store.ingest(denied, [self.record("denied-record-sentinel")])

        with self.store.database() as db:
            versions = [dict(row) for row in db.execute(
                "SELECT * FROM versions WHERE document_id IN (?,?) ORDER BY document_id",
                (identity("graph", "first-a"), identity("graph", "first-b")),
            )]
        # One vector profile with two observed dimensions must be a numeric
        # review candidate, never a semantic outlier assertion.
        with sqlite3.connect(self.store.root / "retrieval.sqlite3") as db:
            db.execute("CREATE TABLE vectors(document_id,version,text_hash,model,endpoint,encoding,vector)")
            for row, vector in zip(versions, (
                    struct.pack("<f", 1.0), struct.pack("<ff", 0.0, 1.0))):
                db.execute("INSERT INTO vectors VALUES(?,?,?,?,?,?,?)", (
                    row["document_id"], row["id"],
                    digest((row["label"] + "\n" + row["body"]).encode()),
                    "synthetic-model", "local", "unit", vector,
                ))

        before = self.durable_files()
        with patch.object(quality, "_blob", side_effect=AssertionError("raw blob read")), \
             patch.object(action, "audit", wraps=quality.audit) as watched:
            result = action.read_action(self.root, self.project_query("one"))
        self.assert_success(result, mode="project", project="one", projects=["one"])
        self.assertEqual(result["counts"]["memberships"], 3)
        self.assertEqual(result["counts"]["project_current_versions"], 3)
        self.assertEqual(result["counts"]["distinct_current_documents"], 3)
        self.assertEqual(result["counts"]["relations"], 1)
        self.assertGreater(result["counts"]["bytes_examined"], 0)
        self.assertEqual(result["findings"], {})
        self.assertEqual({name: value["groups"] for name, value in
                          result["duplicate_candidates"].items()}, {
            "same_raw_bytes": 1,
            "same_normalized_search_text": 1,
            "cross_context_search_text": 1,
        })
        self.assertEqual(result["numeric_review_candidates"]
                         ["mixed_vector_dimensions"]["profiles"], 1)
        watched.assert_called_once()
        args, options = watched.call_args
        self.assertEqual(args, (self.root, ["one"]))
        self.assertEqual(options, {
            "verify_sources": False, "sample_cap": 0, "timeout": 10,
            "max_rows": 100000, "max_bytes": 512 * 1024 * 1024,
            "cancelled": None,
        })
        self.assertEqual(self.durable_files(), before)

    def test_literal_project_all_never_broadens_to_union(self):
        for project in ("one", "all", "two"):
            target = self.target(project, name="target-" + project)
            self.store.ingest(target, [self.record("document-" + project)])
        denied = self.target("private", name="denied-only", permission="denied")
        self.store.ingest(denied, [self.record("denied-record-sentinel")])
        before = self.durable_files()
        literal = action.read_action(self.root, self.project_query("all"))
        union = action.read_action(self.root, self.union_query())
        self.assert_success(literal, mode="project", project="all", projects=["all"])
        self.assert_success(union, mode="all", project=None, projects=["all", "one", "two"])
        self.assertEqual(literal["counts"]["memberships"], 1)
        self.assertEqual(union["counts"]["memberships"], 3)
        self.assertEqual(self.durable_files(), before)

    def test_denied_target_and_denied_project_are_not_scanned(self):
        permitted = self.target(name="permitted")
        denied_same = self.target(name="denied-same", permission="denied")
        denied_only = self.target("private", name="denied-private", permission="denied")
        self.store.ingest(permitted, [self.record("healthy")])
        self.store.ingest(denied_same, [self.record("denied-record-sentinel")])
        self.store.ingest(denied_only, [self.record("private-denied")])
        before = self.durable_files()
        one = action.read_action(self.root, self.project_query("one"))
        union = action.read_action(self.root, self.union_query())
        self.assert_success(one, mode="project", project="one", projects=["one"])
        self.assert_success(union, mode="all", project=None, projects=["one"])
        self.assertEqual(one["counts"]["memberships"], 1)
        self.assertEqual(union["counts"]["memberships"], 1)
        self.assert_error(action.read_action(self.root, self.project_query("private")),
                          "quality_scope_unavailable", mode="project", project="private")
        self.assert_error(action.read_action(self.root, self.project_query("unknown")),
                          "quality_scope_unavailable", mode="project", project="unknown")
        self.assertEqual(self.durable_files(), before)

    def test_empty_permitted_collection_is_still_a_complete_read(self):
        self.target(name="empty")
        result = action.read_action(self.root, self.project_query("one"))
        self.assert_success(result, mode="project", project="one", projects=["one"])
        self.assertEqual(result["counts"], {metric: 0 for metric in COUNTS})
        self.assertEqual(result["findings"], {})

    def test_all_exceeding_16_fails_without_truncation_but_one_remains_readable(self):
        for n in range(17):
            self.target(f"p{n:02d}", name=f"target-{n:02d}")
        before = self.durable_files()
        self.assert_error(action.read_action(self.root, self.union_query()),
                          "quality_scope_unavailable", mode="all")
        one = action.read_action(self.root, self.project_query("p16"))
        self.assert_success(one, mode="project", project="p16", projects=["p16"])
        self.assertEqual(one["counts"]["memberships"], 0)
        self.assertEqual(self.durable_files(), before)

    def test_project_selection_is_exact_and_case_sensitive(self):
        project = "One/Case:한글"
        target = self.target(project, name="exact")
        self.store.ingest(target, [self.record("exact-document")])
        self.assert_success(action.read_action(self.root, self.project_query(project)),
                            mode="project", project=project, projects=[project])
        self.assert_error(action.read_action(self.root, self.project_query(project.lower())),
                          "quality_scope_unavailable", mode="project",
                          project=project.lower())

    def test_strict_query_json_duplicate_keys_nonfinite_and_deep_input(self):
        self.target(name="valid")
        deeply_nested = '{"scope":"project","project":"one","extra":' + \
            "[" * 1100 + "0" + "]" * 1100 + "}"
        invalid = (
            None, {}, b'{"scope":"all"}', "", "not JSON", "[]", "null", "2",
            '{"scope":"all"} trailing', "{", '{"scope":"all","project":"all"}',
            '{"scope":"project"}', '{"project":"one"}',
            '{"scope":"anything","project":"one"}',
            '{"scope":"ALL"}', '{"scope":true}',
            '{"scope":"project","project":null}',
            '{"scope":"project","project":0}',
            '{"scope":"project","project":""}',
            '{"scope":"project","project":" one"}',
            '{"scope":"project","project":"one "}',
            '{"scope":"project","project":"one\\n"}',
            '{"scope":"project","project":"one","filter":"x"}',
            '{"scope":"all","scope":"all"}',
            '{"scope":"all","sc\\u006fpe":"project"}',
            '{"scope":"project","project":"one","project":"two"}',
            '{"scope":"all","extra":{"x":1,"x":2}}',
            '{"scope":"all","extra":NaN}',
            '{"scope":"all","extra":Infinity}',
            '{"scope":"all","extra":1e400}',
            deeply_nested, self.project_query("x" * 129),
            '{"scope":"all"}' + " " * 4096,
        )
        with patch.object(action, "audit", side_effect=AssertionError("invalid query reached audit")) as watched:
            for raw in invalid:
                with self.subTest(raw=repr(raw)[:90]):
                    self.assert_error(action.read_action(self.root, raw),
                                      "quality_query_invalid")
        watched.assert_not_called()

    def test_absent_state_never_creates_a_collection(self):
        missing = self.root / "missing-state-root"
        self.assertFalse(missing.exists())
        with patch.object(action, "audit", side_effect=AssertionError("unexpected audit")) as watched:
            self.assert_error(action.read_action(missing, self.union_query()),
                              "quality_scope_unavailable", mode="all")
        watched.assert_not_called()
        self.assertFalse(missing.exists())

    def test_only_denied_scope_cannot_produce_green_union(self):
        denied = self.target("secret", name="denied", permission="denied")
        self.store.ingest(denied, [self.record("denied-record-sentinel")])
        self.assert_error(action.read_action(self.root, self.union_query()),
                          "quality_scope_unavailable", mode="all")
        self.assert_error(action.read_action(self.root, self.project_query("secret")),
                          "quality_scope_unavailable", mode="project", project="secret")

    def test_budget_time_cancel_and_backend_failures_are_not_partial_success(self):
        target = self.target(name="active")
        self.store.ingest(target, [self.record("example")])
        before = self.durable_files()
        failures = (
            (RuntimeError("quality_scan_budget"), "quality_scan_budget"),
            (RuntimeError("quality_time_budget"), "quality_time_budget"),
            (RuntimeError("quality_cancelled"), "quality_cancelled"),
            (RuntimeError("unknown_backend_error"), "quality_report_unavailable"),
            (ValueError("quality_existing_v3_store_required"), "quality_report_unavailable"),
            (sqlite3.OperationalError("database is locked"), "quality_report_unavailable"),
            (OSError("disconnected"), "quality_report_unavailable"),
            (TypeError("malformed rows"), "quality_report_unavailable"),
        )
        for error, expected in failures:
            with self.subTest(error=repr(error)), patch.object(action, "audit", side_effect=error):
                self.assert_error(action.read_action(self.root, self.project_query("one")),
                                  expected, mode="project", project="one")
        self.assertEqual(self.durable_files(), before)

    def test_preflight_cancellation_skips_audit_and_postflight_cancellation_fails_closed(self):
        self.target(name="active")
        raw = self.project_query("one")
        with patch.object(action, "audit", side_effect=AssertionError("cancelled ran audit")) as watched:
            self.assert_error(action.read_action(self.root, raw, cancelled=lambda: True),
                              "quality_cancelled", mode="project", project="one")
        watched.assert_not_called()

        complete_report = quality.audit(self.root, ["one"], sample_cap=0)
        calls = 0

        def cancel_after_audit():
            nonlocal calls
            calls += 1
            return calls == 2

        with patch.object(action, "audit", return_value=complete_report) as watched:
            self.assert_error(action.read_action(self.root, raw, cancelled=cancel_after_audit),
                              "quality_cancelled", mode="project", project="one")
        self.assertEqual(calls, 2)
        self.assertIs(watched.call_args.kwargs["cancelled"], cancel_after_audit)

    def test_row_cancellation_propagates_without_partial_report(self):
        target = self.target(name="active")
        self.store.ingest(target, [self.record(f"document-{n}") for n in range(8)])
        ticks = 0.0
        callback_calls = 0

        def clock():
            nonlocal ticks
            ticks += .06
            return ticks

        def stop_in_scan():
            nonlocal callback_calls
            callback_calls += 1
            return callback_calls >= 3  # action preflight + two audited checks

        with patch.object(quality, "time", SimpleNamespace(monotonic=clock)):
            result = action.read_action(self.root, self.project_query("one"), cancelled=stop_in_scan)
        self.assert_error(result, "quality_cancelled", mode="project", project="one")
        self.assertEqual(callback_calls, 3)

    def test_scan_callback_is_throttled_to_at_most_one_per_50ms(self):
        target = self.target(name="active")
        self.store.ingest(target, [self.record(f"document-{n}") for n in range(32)])
        ticks = 0.0
        invoked_at = []

        def clock():
            nonlocal ticks
            ticks += .01
            return ticks

        def observe():
            invoked_at.append(ticks)
            return False

        with patch.object(quality, "time", SimpleNamespace(monotonic=clock)):
            report = quality.audit(self.root, ["one"], sample_cap=0, timeout=10,
                                   cancelled=observe)
        self.assertEqual(report["coverage"]["status"], "complete")
        self.assertGreater(len(invoked_at), 1)
        self.assertLess(len(invoked_at), 32)
        for earlier, later in zip(invoked_at, invoked_at[1:]):
            self.assertGreaterEqual(later - earlier, .05 - 1e-8)

    def test_actual_timeout_becomes_quality_time_budget(self):
        target = self.target(name="active")
        self.store.ingest(target, [self.record("example")])
        ticks = 0.0

        def over_budget_clock():
            nonlocal ticks
            ticks += 11.0
            return ticks

        with patch.object(quality, "time", SimpleNamespace(monotonic=over_budget_clock)):
            result = action.read_action(self.root, self.project_query("one"))
        self.assert_error(result, "quality_time_budget", mode="project", project="one")

    def test_malformed_nested_audit_reports_never_turn_green(self):
        target = self.target(name="active")
        self.store.ingest(target, [self.record("example")])
        valid = quality.audit(self.root, ["one"], verify_sources=False, sample_cap=0)
        before = self.durable_files()

        def set_field(path, value):
            def mutate(report):
                obj = report
                for name in path[:-1]:
                    obj = obj[name]
                obj[path[-1]] = value
            return mutate

        bad_reports = (
            ("absent report", lambda r: None),
            ("wrong schema", set_field(("schema",), 1)),
            ("old ambiguous text candidates", set_field(("schema",), 3)),
            ("missing content policy", set_field(("content_evidence",), None)),
            ("unknown content policy", set_field(("content_evidence", "policy"), "future-policy")),
            ("impossible exclusion partition", set_field(("counts", "noncontent_current_versions"), 1)),
            ("missing metadata matches", set_field(("metadata_matches",), None)),
            ("metadata matches claimed content", set_field(("metadata_matches", "within_context", "status"), "review_candidate")),
            ("wrong projects", set_field(("projects",), ["two"])),
            ("verified sources", set_field(("source_verification_requested",), True)),
            ("partial", set_field(("coverage", "status"), "partial")),
            ("wrong source claim", set_field(("coverage", "source_verification"), "retained_canonical_json")),
            ("wrong covered count", set_field(("coverage", "memberships_scanned"), -1)),
            ("missing counts", set_field(("counts",), None)),
            ("negative count", set_field(("counts", "memberships"), -1)),
            ("boolean count", set_field(("counts", "memberships"), True)),
            ("fractional count", set_field(("counts", "memberships"), 1.5)),
            ("nonfinite count", set_field(("counts", "memberships"), float("nan"))),
            ("unsafe count precision", set_field(("counts", "memberships"), 2**53)),
            ("source blob verified", set_field(("counts", "source_blobs_verified"), 1)),
            ("invalid finding", set_field(("findings",), {"private body": 1})),
            ("nonfinite finding", set_field(("findings",), {"bad": float("inf")})),
            ("bad duplicate tree", set_field(("duplicate_candidates",), [])),
            ("invalid raw claim", set_field(
                ("duplicate_candidates", "same_raw_bytes", "classification"), "verified")),
            ("bad duplicate group", set_field(
                ("duplicate_candidates", "same_normalized_search_text", "groups"), -1)),
            ("bad duplicate status", set_field(
                ("duplicate_candidates", "cross_context_search_text", "status"), "verified")),
            ("bad mixed dimensions", set_field(
                ("numeric_review_candidates", "mixed_vector_dimensions"), [])),
            ("nonfinite profiles", set_field(
                ("numeric_review_candidates", "mixed_vector_dimensions", "profiles"), float("inf"))),
        )
        for name, mutation in bad_reports:
            with self.subTest(report=name):
                broken = copy.deepcopy(valid)
                mutated = mutation(broken)
                if name == "absent report":
                    broken = mutated
                with patch.object(action, "audit", return_value=broken):
                    self.assert_error(
                        action.read_action(self.root, self.project_query("one")),
                        "quality_report_unavailable", mode="project", project="one")
        self.assertEqual(self.durable_files(), before)

    def test_permission_revocation_or_change_during_scan_fails_closed(self):
        for new_permission in ("denied", "review"):
            with self.subTest(new_permission=new_permission):
                target = self.target(name="changing")
                self.store.ingest(target, [self.record("example")])

                def race(*args, **kwargs):
                    report = quality.audit(*args, **kwargs)
                    with self.store.database() as db:
                        db.execute("UPDATE target_projects SET permission=? WHERE target_id=?",
                                   (new_permission, target))
                    return report

                with patch.object(action, "audit", side_effect=race):
                    self.assert_error(action.read_action(self.root, self.project_query("one")),
                                      "quality_scope_unavailable", mode="project", project="one")
                with self.store.database() as db:
                    db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=?",
                               (target,))

    def test_collection_database_inode_replacement_during_scan_fails_closed(self):
        target = self.target(name="active")
        self.store.ingest(target, [self.record("example")])
        first_inode = self.store.path.stat().st_ino

        def replace_after_audit(*args, **kwargs):
            report = quality.audit(*args, **kwargs)
            replacement = self.root / "synthetic-replacement.sqlite3"
            replacement.write_bytes(self.store.path.read_bytes())
            os.replace(replacement, self.store.path)
            return report

        with patch.object(action, "audit", side_effect=replace_after_audit):
            self.assert_error(action.read_action(self.root, self.project_query("one")),
                              "quality_scope_unavailable", mode="project", project="one")
        self.assertNotEqual(self.store.path.stat().st_ino, first_inode)

    def test_malformed_existing_sqlite_returns_report_unavailable(self):
        corrupt_root = self.root / "corrupt-synthetic-state"
        directory = corrupt_root / "knowledge" / "collection"
        directory.mkdir(parents=True)
        path = directory / "collection.sqlite3"
        path.write_bytes(b"synthetic invalid sqlite")
        self.assert_error(action.read_action(corrupt_root, self.project_query("one")),
                          "quality_report_unavailable", mode="project", project="one")
        self.assertEqual(path.read_bytes(), b"synthetic invalid sqlite")


if __name__ == "__main__":
    unittest.main()
