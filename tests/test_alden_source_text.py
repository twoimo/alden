"""Legacy social-export footer boundaries and source-bound projection regressions."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_source_text import project_source_text, reading_tail_presentation, TEXT_VERSION
from alden_collection import CollectionStore, identity
from alden_collection_retrieval import index_text, retrieve
from alden_graph_quality import audit
from alden_collect import source_graph

BODY = ('서류에서 높은 점수를 받는 지원자 특징 5가지.\n'
        '1. 채용공고의 JD를 그대로 옮겨 쓰지 않는다.\n'
        '2. 경험 나열보다 왜를 제대로 쓴다.\n'
        '3. 본인의 역할과 행동 그리고 결과 및 KPI가 보여야 한다.\n'
        '4. 회사 및 직무에 제대로 Align 한다.\n'
        '5. 첫 문장부터 핵심이 보여야 하고 끝까지 쉽게 읽혀야 한다.')
TAIL = '\u00a0\n1\n/\n2\n6\n1\n3'


def source(body):
    return {'id': 'source:kim_manager:Dd5U_zkmRLT', 'type': 'source',
            'platform': 'threads', 'code': 'Dd5U_zkmRLT',
            'url': 'https://www.threads.com/@allpass_everything/post/Dd5U_zkmRLT',
            'localTextOrigin': 'local_corpus_json', 'localOriginalText': body}


class SourceTextTests(unittest.TestCase):

    def test_graph_note_folds_only_a_long_one_or_two_digit_line_tail(self):
        body = BODY + '\n1\n2\n6\n1\n3'
        record = {'platform': 'graph', 'localOriginalText': body}
        result = reading_tail_presentation(body, record)
        self.assertEqual(result['policy'], 'graph-reading-numeric-lines-v1')
        self.assertEqual(result['reading'] + result['tail'], body)
        self.assertTrue(result['original_preserved'])
        self.assertEqual(result['semantic_validity'], 'unverified')
        self.assertIsNone(project_source_text(body, record)['projection'])
        for unchanged in (BODY + '\n125\n180\n240', BODY + '\n2026', '짧은 메모\n1\n2\n3',
                          BODY + '\n1\n2\n6\n1\n3\n4\n5\n6'):
            self.assertIsNone(reading_tail_presentation(unchanged, {'platform': 'graph', 'localOriginalText': unchanged}))
        self.assertIsNone(reading_tail_presentation(body, {'platform': 'graph', 'localOriginalText': body + 'x'}))

    def test_reading_candidate_preserves_exact_body_and_unverified_status(self):
        for suffix in ('\n11\n2', ' 1 / 2 6 1 3', '\n1\n/\n2\n9\n2'):
            body = BODY + suffix
            result = reading_tail_presentation(body, source(body))
            self.assertIsNotNone(result)
            self.assertEqual(result['reading'] + result['tail'], body)
            self.assertEqual(result['body_sha256'], hashlib.sha256(body.encode()).hexdigest())
            self.assertTrue(result['original_preserved'])
            self.assertEqual(result['semantic_validity'], 'unverified')
            self.assertIsNone(project_source_text(body, source(body))['projection'])

    def test_reading_candidate_requires_verified_legacy_source_shape(self):
        body = BODY + '\n11\n2'
        for change in ({'platform':'files'}, {'type':'note'}, {'localTextOrigin':'unknown'},
                       {'localOriginalText':body+'x'}, {'code':'different'}, {'url':'https://example.com/post/a'}):
            self.assertIsNone(reading_tail_presentation(body, {**source(body), **change}))
        self.assertIsNone(reading_tail_presentation(body, None))

    def test_single_year_ratio_code_and_unbounded_numeric_data_stay_unfolded(self):
        for body in (BODY+'\n2026', BODY+'\n1\n/\n2', BODY+'\n```\n11\n2',
                     BODY+'\n'+'1\n'*9+'2', BODY+'\n123456\n2', '본문\n11\n2',
                     '본문'*6001+'\n11\n2'):
            self.assertIsNone(reading_tail_presentation(body, source(body)))
    def test_future_source_graph_import_preserves_raw_and_declares_projection(self):
        raw=source(BODY+TAIL)
        records,edges=source_graph({'original_id':'career'}, {'nodes':[raw],'edges':[]}, {'sha256':'1'*64})
        self.assertEqual(records[0]['text'],BODY)
        self.assertEqual(records[0]['raw']['localOriginalText'],BODY+TAIL)
        self.assertEqual(records[0]['text_projection']['semantic_validity'],'unverified')
        self.assertEqual(records[0]['original_id'],'career:node:'+raw['id'])
        self.assertEqual(edges,[])
    def test_numeric_footer_is_reversible_and_numbered_post_is_exact(self):
        original = BODY + TAIL; raw = source(original)
        result = project_source_text(original, raw)
        self.assertEqual(result['body'], BODY)
        receipt = result['projection']
        self.assertEqual(result['body'] + receipt['separated_tail'], original)
        self.assertEqual(raw['localOriginalText'], original)
        self.assertEqual(receipt['original_body_sha256'], hashlib.sha256(original.encode()).hexdigest())
        self.assertEqual(receipt['semantic_validity'], 'unverified')
        # Another retained exporter puts the same marker on a separate line.
        separate = BODY + '\n' + TAIL
        projected = project_source_text(separate, source(separate))
        self.assertEqual(projected['body'], BODY)
        self.assertEqual(projected['body'] + projected['projection']['separated_tail'], separate)

    def test_body_patterns_dates_quantities_and_normal_lists_stay_exact(self):
        for body in (BODY, BODY + TAIL + '\n그 다음 설명입니다.',
                     BODY + '\n2026/10/13\n6개\n13명',
                     BODY + '\n\u00a0\n1/2\n6개\n1\n3',
                     BODY + '\n\u00a0\n6. 또 다른 항목\n7. 마지막 항목',
                     BODY + TAIL.replace('\u00a0', ''),
                     BODY + '\n```\n숫자 자료\n```' + TAIL,
                     BODY + '\n~~~\n표\n~~~' + TAIL):
            with self.subTest(body=body[-60:]):
                self.assertEqual(project_source_text(body, source(body)), {'body': body, 'projection': None})

    def test_source_declarations_url_and_shortcode_must_all_match(self):
        body = BODY + TAIL
        for change in ({'platform': 'youtube'}, {'localTextOrigin': 'ocr'}, {'type': 'claim'},
                       {'code': 'another'}, {'url': 'https://www.threads.com.evil.test/@x/post/Dd5U_zkmRLT'},
                       {'url': 'https://www.threads.com/@x/post/Dd5U_zkmRLT?content=1'},
                       {'url': 'https://user@www.threads.com/@x/post/Dd5U_zkmRLT'},
                       {'url': 'https://www.threads.com:443/@x/post/Dd5U_zkmRLT'},
                       {'localOriginalText': BODY}):
            with self.subTest(change=change):
                self.assertIsNone(project_source_text(body, {**source(body), **change})['projection'])

    def test_unbounded_or_impossible_counter_tails_and_short_text_remain(self):
        for tail in (TAIL.replace('1\n/\n2', '3\n/\n2'),
                     TAIL.replace('1\n/\n2', '1\n/\n1001'),
                     TAIL.replace('\n6\n', '\n-6\n'), TAIL + '\n4\n5',
                     TAIL.replace('\n6\n', '\n12345678\n'), TAIL + '\n\n'):
            body = BODY + tail
            self.assertIsNone(project_source_text(body, source(body))['projection'])
        body = '숫자 수업' + TAIL
        self.assertIsNone(project_source_text(body, source(body))['projection'])

    def test_number_and_uncertainty_differences_are_not_normalized_away(self):
        bodies = [BODY+'\n약 3년의 경력입니다.', BODY+'\n약 5년의 경력입니다.',
                  BODY+'\n가능성은 아직 미확인입니다.', BODY+'\n확인했습니다.']
        results = [project_source_text(body+TAIL, source(body+TAIL))['body'] for body in bodies]
        self.assertEqual(results, bodies); self.assertEqual(len(set(results)), 4)


class SourceProjectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir='/private/tmp'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='graph', original_id='career', kind='graph', label='취업', projects=['one'])
        self.record = {'platform':'graph','original_id':'post1','label':'','text':BODY+TAIL,'raw':source(BODY+TAIL)}
        self.store.ingest(self.target, [self.record])
        self.doc = identity('graph', 'post1')

    def test_detail_and_search_share_projection_without_source_or_version_mutation(self):
        before = self.store.path.read_bytes()
        raw_path = next(self.store.blobs.iterdir()); original = raw_path.read_bytes()
        detail = self.store.graph_page(projects=['one'], focus=self.doc, hops=0, details=True)['details']
        self.assertEqual(detail['body'], BODY)
        self.assertEqual(detail['text_projection']['separated_tail'], TAIL)
        with patch('auto_reply_knowledge_graph._local_dense_embeddings', side_effect=AssertionError('must not embed')):
            receipt = index_text(self.root, ['one'])
            result = retrieve(self.root, '채용공고', projects=['one'], max_entities=1, max_relations=0,
                              query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('disabled')))
        self.assertEqual(receipt['embeddings_generated'], 0)
        self.assertEqual(result['facts'], [BODY])
        self.assertEqual(result['fact_provenance'][0]['text_projection']['original_body_sha256'], detail['text_projection']['original_body_sha256'])
        self.assertNotIn('separated_tail', result['fact_provenance'][0]['text_projection'])
        self.assertEqual(self.store.path.read_bytes(), before); self.assertEqual(raw_path.read_bytes(), original)

    def test_reading_candidate_changes_neither_detail_body_nor_search_extraction(self):
        body = BODY+'\n11\n2'
        self.store.ingest(self.target,[{**self.record,'text':body,'raw':source(body)}])
        detail = self.store.graph_page(projects=['one'],focus=self.doc,details=True)['details']
        self.assertEqual(detail['body'],body)
        self.assertIsNone(detail['text_projection'])
        self.assertEqual(detail['reading_projection']['reading']+detail['reading_projection']['tail'],body)
        index_text(self.root,['one'])
        result=retrieve(self.root,'채용공고',projects=['one'],max_relations=0,
                        query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('disabled')))
        self.assertIn(body,result['facts'])

    def test_corrupt_clean_cache_cannot_pass_source_hash_binding(self):
        index_text(self.root, ['one'])
        with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
            db.execute('UPDATE texts SET body=? WHERE document_id=?', ('채용공고 가짜 본문', self.doc))
            db.execute('UPDATE text_fts SET body=? WHERE document_id=?', ('채용공고 가짜 본문', self.doc))
        with self.assertRaisesRegex(RuntimeError, 'collection_source_integrity'):
            retrieve(self.root, '채용공고', projects=['one'], max_relations=0,
                     query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('disabled')))

    def test_text_refresh_is_idempotent_and_excludes_denied_scope(self):
        denied = self.store.register(platform='graph', original_id='denied', kind='graph', label='denied', projects=['other'])
        self.store.ingest(denied, [{**self.record, 'original_id':'post2'}])
        self.assertEqual(index_text(self.root, ['one'])['documents'], 1)
        with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
            rows = db.execute('SELECT * FROM texts').fetchall()
        index_text(self.root, ['one'])
        with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
            self.assertEqual(rows, db.execute('SELECT * FROM texts').fetchall())
        with self.assertRaises(ValueError): index_text(self.root, [])

    def test_legacy_cache_stamp_and_vector_cannot_silently_be_reused(self):
        index_text(self.root,['one'])
        with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
            db.execute('UPDATE texts SET extraction=?,body=?', ('retained-local-original-text-v1', BODY+TAIL))
        result=retrieve(self.root,'채용공고',projects=['one'],max_relations=0,
                       query_embed=lambda _: (_ for _ in ()).throw(AssertionError('must not infer')))
        self.assertEqual(result['pending_text_versions'],1)
        self.assertNotEqual(result['search_mode'],'rrf')
        self.assertEqual(result['facts'],[BODY])
        index_text(self.root,['one'])
        with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
            self.assertEqual(db.execute('SELECT extraction,body FROM texts').fetchone(),(TEXT_VERSION,BODY))

    def test_unchanged_migration_keeps_fts_rowids_and_repairs_duplicate_current_rows(self):
        index_text(self.root,['one'])
        path=self.store.root/'retrieval.sqlite3'
        with sqlite3.connect(path) as db:
            original=db.execute('SELECT rowid,* FROM text_fts').fetchone()
            db.execute('UPDATE texts SET extraction=?', ('retained-local-original-text-v1',))
        index_text(self.root,['one'])
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT rowid,* FROM text_fts').fetchone(),original)
            db.execute('INSERT INTO text_fts(document_id,version,label,body) SELECT document_id,version,label,body FROM text_fts')
        index_text(self.root,['one'])
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM text_fts').fetchone(),(1,))
            self.assertEqual(db.execute('SELECT body FROM text_fts').fetchone(),(BODY,))

    def test_clean_body_matches_are_candidates_without_merging_distinct_posts(self):
        self.store.ingest(self.target, [{**self.record, 'original_id':'post2','text':BODY,'raw':source(BODY)}])
        index_text(self.root, ['one'])
        report = audit(self.root, ['one'], verify_sources=True)
        self.assertEqual(report['source_text_projection']['numeric_tail_candidates'], 1)
        self.assertEqual(report['source_text_projection']['same_projected_body_groups'], 1)
        self.assertEqual(report['counts']['distinct_current_documents'], 2)
        self.assertEqual(report['findings'], {})
        with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
            self.assertEqual(db.execute('SELECT DISTINCT extraction FROM texts').fetchall(), [(TEXT_VERSION,)])


if __name__ == '__main__': unittest.main()
