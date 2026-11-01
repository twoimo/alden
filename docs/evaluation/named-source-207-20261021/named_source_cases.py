"""Behavioral candidate fixtures; no live source writes or model inference."""
import unittest
from unittest.mock import patch
import alden_collection_retrieval as retrieval
from alden_collection import identity
from test_alden_collection_retrieval import CollectionRetrievalTests as Base


class NamedSources(Base):
    query = '제한시간 25분 초대왕 돈까스'

    def named(self, original='named-source', **extra):
        record = {'original_id': original, 'label': self.query,
                  'text': '보존된 실제 자막 본문입니다. ' * 80, **extra}
        self.store.ingest(self.allowed, [record])
        return identity('graph', original)

    def test_named_source_not_crowded_out_by_partial_title_references(self):
        self.title_matches(15, '돈까스')
        document = self.named()
        result = retrieval.retrieve(self.root, self.query, projects=['one'], candidate_limit=3,
                                    max_entities=1, max_relations=0)
        proof = result['fact_provenance'][0]
        self.assertEqual(proof['entity_id'], document)
        self.assertEqual(proof['content_evidence']['classification'], 'content_candidate')
        self.assertEqual(proof['selection_basis'],
                         'matching_current_version_bound_title; source lookup, not body grounding')
        self.assertEqual(proof['truth_status'], 'source_record; not independently verified')
        self.assertEqual(result['source_title_evidence_coverage']['preferred'], 1)

    def test_named_title_never_displaces_complete_body_grounding(self):
        named = self.named()
        body = self.add(self.allowed, 'body-proof', self.query + '를 설명하는 보존 원문입니다.')
        result = retrieval.retrieve(self.root, self.query, projects=['one'], max_relations=0)
        self.assertEqual(result['candidates'][:2], [body, named])
        self.assertIn('bound_body', result['fact_provenance'][0]['selection_basis'])

    def test_named_source_requires_all_title_words_and_actual_content(self):
        self.named('split-title-body', label='제한시간 25분', text='초대왕 돈까스')
        self.named('metadata', raw={'kind': 'metadata', 'content_indexed': False})
        self.named('link', text='https://example.invalid/자료')
        self.named('title-only', text='')
        result = retrieval.retrieve(self.root, self.query, projects=['one'], max_relations=0)
        self.assertTrue(all('bound_title' not in proof.get('selection_basis', '')
                            for proof in result['fact_provenance']))
        self.assertEqual(result['source_title_evidence_coverage']['preferred'], 0)

    def test_named_source_shares_total_verification_budget_with_body_checks(self):
        self.store.ingest(self.allowed, [{'original_id': 'body-' + str(i), 'label': '',
            'text': self.query + '의 보존 본문'} for i in range(105)])
        self.named()
        result = retrieval.retrieve(self.root, self.query, projects=['one'], candidate_limit=3,
                                    max_entities=1, max_relations=0)
        body, title = result['body_evidence_coverage'], result['source_title_evidence_coverage']
        self.assertEqual(body['verified'] + title['verified'], 100)
        self.assertEqual(title['verification_limit'], 0)
        self.assertTrue(title['truncated'])

    def test_named_source_keeps_explicit_pins_and_dense_only_policy(self):
        titles = self.title_matches(5, '돈까스')
        named = self.named()
        with self.profile():
            retrieval.index_dense(self.root, ['one'], embed=lambda texts:
                [[0., 1.] if '자막' in text else [1., 0.] for text in texts])
            options = {'projects': ['one'], 'max_entities': 1, 'max_relations': 0,
                       'query_embed': lambda _: [1., 0.]}
            dense = retrieval.retrieve(self.root, self.query, rrf_weights=[0., 1.], **options)
            self.assertIn(dense['fact_provenance'][0]['entity_id'], titles)
            self.assertNotIn('source_title_policy', dense)
            pinned = retrieval.retrieve(self.root, self.query, also=[titles[0]], **options)
            self.assertEqual(pinned['fact_provenance'][0]['entity_id'], titles[0])
            normal = retrieval.retrieve(self.root, self.query, **options)
            self.assertEqual(normal['fact_provenance'][0]['entity_id'], named)

    def test_named_source_checks_remain_current_and_cancellable(self):
        self.named()
        original = retrieval._verified_source
        changed = [False]
        def revise(*args):
            if not changed[0]:
                changed[0] = True
                self.named(text='개정된 다른 본문')
            return original(*args)
        with patch.object(retrieval, '_verified_source', side_effect=revise):
            with self.assertRaisesRegex(RuntimeError, 'collection_graph_version_changed'):
                retrieval.retrieve(self.root, self.query, projects=['one'])
        stopped = [False]
        def cancel(*args):
            result = original(*args); stopped[0] = True; return result
        with patch.object(retrieval, '_verified_source', side_effect=cancel):
            with self.assertRaisesRegex(RuntimeError, 'collection_retrieval_cancelled'):
                retrieval.retrieve(self.root, self.query, projects=['one'], cancelled=lambda: stopped[0])

    def test_named_source_keeps_numeric_boundaries_and_denied_projects(self):
        self.named('wrong-number', label=self.query.replace('25분', '125분'))
        denied = self.store.register(platform='graph', original_id='denied', kind='source',
                                     label='denied', projects=['one'], permission='denied')
        self.store.ingest(denied, [{'original_id': 'forbidden', 'label': self.query, 'text': '비공개 본문'}])
        document = self.named()
        result = retrieval.retrieve(self.root, self.query, projects=['one'], max_relations=0)
        self.assertEqual(result['source_title_evidence_coverage']['matched'], 1)
        self.assertEqual(result['fact_provenance'][0]['entity_id'], document)
        self.assertNotIn(identity('graph', 'forbidden'), result['candidates'])

    def test_named_source_rechecks_revoked_permission_and_cached_body(self):
        self.named()
        original = retrieval._verified_source
        def revoke(*args):
            with self.store.database() as db:
                db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.allowed,))
            return original(*args)
        with patch.object(retrieval, '_verified_source', side_effect=revoke):
            with self.assertRaisesRegex(RuntimeError, 'collection_graph_version_changed'):
                retrieval.retrieve(self.root, self.query, projects=['one'])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=?", (self.allowed,))
        with self.profile():
            retrieval.index_dense(self.root, ['one'], embed=self.embed)
            import sqlite3
            with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
                db.execute('UPDATE texts SET body=?', ('다른 위조된 캐시 본문',))
            result = retrieval.retrieve(self.root, self.query, projects=['one'], query_embed=lambda _: [1., 0.])
            self.assertNotIn('위조된', '\n'.join(result['facts']))
            self.named(raw={'localOriginalText': '보존된 원래 자막 본문입니다.'})
            retrieval.index_dense(self.root, ['one'], embed=self.embed)
            version = self.store.graph_page(projects=['one'])['nodes'][0]['source_version']
            with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
                self.assertEqual(db.execute('SELECT body_source FROM texts WHERE version=?', (version,)).fetchone()[0],
                                 'retained_record.localOriginalText')
                db.execute('UPDATE texts SET body=? WHERE version=?', ('다른 위조된 캐시 본문', version))
            with self.assertRaisesRegex(RuntimeError, 'collection_source_integrity'):
                retrieval.retrieve(self.root, self.query, projects=['one'], query_embed=lambda _: [1., 0.])

    def test_named_source_does_not_call_truncated_query_a_complete_title_match(self):
        words = ['주제' + str(index) for index in range(25)]
        self.named(label=' '.join(words[:24]))
        result = retrieval.retrieve(self.root, ' '.join(words), projects=['one'], max_relations=0)
        self.assertNotIn('source_title_policy', result)

    def test_named_source_keeps_explicit_latest_publication_selection(self):
        self.named('old-source', label='최신 약관', published_at='2020-01-01T00:00:00Z')
        self.named('new-reference', label='최신 약관', text='', published_at='2026-09-01T00:00:00Z')
        result = retrieval.retrieve(self.root, '최신 약관', projects=['one'], max_entities=1, max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'], identity('graph', 'new-reference'))
        self.assertNotIn('source_title_policy', result)


if __name__ == '__main__':
    unittest.main()
