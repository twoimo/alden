"""Candidate regressions for complete title words and retained body coordinates."""
import hashlib
import unicodedata
import unittest
from unittest.mock import patch

import alden_collection_retrieval as retrieval
from alden_collection import identity
from test_alden_collection_retrieval import CollectionRetrievalTests as Base


class MatchedContext(Base):
    def source(self, original, label, body):
        self.store.ingest(self.allowed, [{'original_id': original, 'label': label, 'text': body}])
        return identity('graph', original)

    def test_context_title_requires_short_hangul_and_numeric_latin_words(self):
        query = '제 삶 1m 실험'
        body = '보존된 자막으로 확인할 수 있는 원본 내용입니다. ' * 60
        correct = self.source('correct', query, body)
        for i, label in enumerate(['삶 1m 실험', '제 1m 실험', '제 삶 11m 실험']):
            self.source('wrong-' + str(i), label, body)
        result = retrieval.retrieve(self.root, query, projects=['one'], max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'], correct)
        self.assertEqual(result['source_title_evidence_coverage']['matched'], 1)
        self.assertIn('not body grounding', result['fact_provenance'][0]['selection_basis'])

    def test_context_single_hangul_title_has_no_fts_minimum_length_requirement(self):
        correct = self.source('short', '삶', '확인 가능한 원래 자막 내용입니다. ' * 50)
        result = retrieval.retrieve(self.root, '삶', projects=['one'], max_entities=1, max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'], correct)
        self.assertEqual(result['source_title_evidence_coverage']['preferred'], 1)

    def test_context_long_first_source_does_not_starve_second_and_emits_late_matches(self):
        query = '검색어 하나 둘'
        bodies = ['일반 설명입니다. ' * 1500 + query + '를 확인한 구간입니다.' for _ in range(2)]
        expected = {self.source(str(i), '자료 ' + str(i), body) for i, body in enumerate(bodies)}
        result = retrieval.retrieve(self.root, query, projects=['one'], max_entities=2,
                                    max_relations=0, max_context_chars=800)
        self.assertEqual({p['entity_id'] for p in result['fact_provenance']}, expected)
        self.assertLessEqual(result['context_chars'], 800)
        self.assertEqual(result['context_chars'], sum(map(len, result['facts'])))
        for text, proof in zip(result['facts'], result['fact_provenance']):
            self.assertIn(query, text)
            self.assertTrue(proof['fact_truncated'])
            excerpt = proof['body_excerpt']
            body = bodies[int(proof['original_id'])]
            pieces = [body[r['start']:r['end']] for r in excerpt['ranges']]
            self.assertEqual(excerpt['body_sha256'], hashlib.sha256(body.encode()).hexdigest())
            self.assertEqual(excerpt['body_chars'], len(body))
            self.assertEqual(text, '자료 ' + proof['original_id'] + ': ' + '\n[…생략…]\n'.join(pieces))
            self.assertTrue(proof['provenance_valid'])

    def test_context_separated_matching_spans_keep_explicit_discontinuity(self):
        body = '첫머리. ' * 1000 + '검색어' + ' 중간 설명 ' * 1000 + '단서' + ' 끝 설명 ' * 1000
        text, ranges = retrieval._retained_excerpt(body, retrieval._original_word_plans('검색어 단서'), 300, lambda: False)
        self.assertIn('검색어', text)
        self.assertIn('단서', text)
        self.assertEqual(len(ranges), 2)
        self.assertIn('\n[…생략…]\n', text)
        self.assertLessEqual(len(text), 300)
        self.assertEqual(text, '\n[…생략…]\n'.join(body[start:end] for start, end in ranges))

    def test_context_unicode_coordinates_do_not_follow_expanding_casefold(self):
        body = 'İß ﬃ ① 😀 ' * 400 + '선명한 단서' + ' 마지막 설명 ' * 400
        query = unicodedata.normalize('NFD', '선명한 단서')
        self.source('unicode', '원문', body)
        result = retrieval.retrieve(self.root, query, projects=['one'], max_entities=1,
                                    max_relations=0, max_context_chars=256)
        proof = result['fact_provenance'][0]
        self.assertIn('선명한 단서', result['facts'][0])
        pieces = [body[r['start']:r['end']] for r in proof['body_excerpt']['ranges']]
        self.assertEqual(result['facts'][0], '원문: ' + '\n[…생략…]\n'.join(pieces))

    def test_context_insufficient_budget_reports_partial_and_stays_bounded(self):
        body = '가나다라마바사아자차카타파하' * 400 + '다른검색어'
        plans = retrieval._original_word_plans('가나다라마바사아자차카타파하 다른검색어')
        text, ranges = retrieval._retained_excerpt(body, plans, 3, lambda: False)
        self.assertLessEqual(len(text), 3)
        self.assertNotIn('다른검색어', text)
        self.assertTrue(all(0 <= start < end <= len(body) for start, end in ranges))

    def test_context_numeric_passage_boundaries_do_not_match_125_as_25(self):
        body = '일반 설명 ' * 500 + '125분' + ' 일반 설명 ' * 1000 + '25분' + ' 끝 설명 ' * 500
        text, ranges = retrieval._retained_excerpt(body, retrieval._original_word_plans('25분'), 120, lambda: False)
        self.assertIn('25분', text)
        self.assertNotIn('125분', text)
        self.assertGreater(ranges[0][0], body.index('125분'))

    def test_context_cancelled_scans_remain_cancellable(self):
        with self.assertRaisesRegex(RuntimeError, 'collection_retrieval_cancelled'):
            retrieval._retained_excerpt('검색어 ' * 5000,
                retrieval._original_word_plans('검색어'), 256, lambda: True)

    def test_context_explicit_pin_keeps_original_prefix_contract(self):
        body = '앞부분 ' * 2000 + '검색어 단서'
        document = self.source('pin', '문서', body)
        result = retrieval.retrieve(self.root, '검색어 단서', projects=['one'], also=[document],
                                    max_entities=1, max_relations=0, max_context_chars=256)
        self.assertEqual(result['facts'], [('문서: ' + body)[:256]])
        self.assertNotIn('body_excerpt', result['fact_provenance'][0])

    def test_context_zero_entities_does_not_divide_by_zero_or_emit(self):
        self.source('zero', '검색어', '검색어 원문 ' * 1000)
        result = retrieval.retrieve(self.root, '검색어', projects=['one'], max_entities=0)
        self.assertEqual(result['facts'], [])


if __name__ == '__main__':
    unittest.main()
