"""NR-1/NR-2 regressions using composed, generated-only integration fixtures."""
from contextlib import contextmanager
import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tests import test_alden_original_search_integration as integration
from alden_abort import AldenCancelled
from alden_collection import CollectionStore, _READ_CANCELLATION, identity
import alden_collection_retrieval as retrieval
import alden_original_text_index as original_index
import auto_reply_knowledge_graph as kg


class OriginalSearchRegressionTests(unittest.TestCase):
    STRATEGIES = ('complete', 'indexed-lexical')

    def setUp(self):
        # Composition deliberately avoids discovering/running the helper's 20 tests.
        self.fixture = integration.OriginalSearchIntegrationTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def indexed(self, state):
        receipt = retrieval.index_text(state['root'], [state['project']])
        self.assertEqual(receipt['state'], 'ready')
        self.assertEqual(receipt['embeddings_generated'], 0)
        self.assertEqual(receipt['original_text_index']['state'], 'ready')

    def lookup(self, state, query, strategy, limit, *, also=None, cancelled=lambda: False):
        return retrieval.retrieve(state['root'], query, projects=[state['project']],
            search_strategy=strategy, candidate_limit=limit, max_entities=10, max_relations=0,
            also=also, cancelled=cancelled)

    def ids(self, result):
        return [proof['entity_id'] for proof in result['fact_provenance'] if proof['fact_type'] == 'entity']

    @contextmanager
    def observed_rows(self):
        actual = retrieval._rows
        observations = []
        def read(*args, **options):
            rows = actual(*args, **options)
            candidates = options.get('candidate_ids')
            # Legacy source admission also calls _rows in independent batches.
            # Observe the retrieve owner's merged admission, not those probes.
            caller = inspect.currentframe().f_back
            while caller is not None and caller.f_code.co_filename != retrieval.__file__:
                caller = caller.f_back
            if caller is not None and caller.f_code.co_name == 'retrieve':
                observations.append({'candidate_ids': None if candidates is None else list(candidates),
                                     'returned_ids': [row['id'] for row in rows]})
            return rows
        with patch.object(retrieval, '_rows', read):
            yield observations

    def saturated(self, *, legacy_count=2, query='reviewtailnonce'):
        state = self.fixture.build(name='saturation', documents={
            'primary': b'headcontrol\n' + b'padding ' * 3000 + b'\n' + query.encode() + b'\n'})
        store = state['store']
        target = store.register(platform='graph', original_id='legacy-heads', kind='source',
            label='generated controls', projects=[state['project']])
        records = [{'original_id': 'head-' + str(i), 'label': 'legacy control',
                    'text': query + ' legacy head'} for i in range(legacy_count)]
        store.ingest(target, records)
        state['legacy_ids'] = [identity('graph', row['original_id']) for row in records]
        self.indexed(state)
        return state

    def test_saturated_legacy_heads_keep_original_before_rows_in_both_budgets(self):
        state = self.saturated()
        original = state['ids']['primary']
        legacy = state['store'].search('reviewtailnonce', projects=[state['project']],
                                      limit=10, _include_original=False)
        self.assertEqual({row['id'] for row in legacy}, set(state['legacy_ids']))
        for strategy in self.STRATEGIES:
            for limit in (1, 2):
                with self.subTest(strategy=strategy, candidate_limit=limit):
                    with self.observed_rows() as observed:
                        result = self.lookup(state, 'reviewtailnonce', strategy, limit)
                    self.assertIn(original, self.ids(result))
                    self.assertLessEqual(len(result['candidates']), limit)
                    self.assertLessEqual(len(self.ids(result)), limit)
                    self.assertTrue(observed)
                    # Complete retains its authorized full-vector scan scope.
                    if strategy == 'indexed-lexical':
                        self.assertLessEqual(result['eligible_documents'], limit)
                        for admission in observed:
                            self.assertIsNotNone(admission['candidate_ids'])
                            self.assertIn(original, admission['candidate_ids'])
                            self.assertLessEqual(len(admission['candidate_ids']), limit)
                            self.assertLessEqual(len(admission['returned_ids']), limit)
                    proof = next(p for p in result['fact_provenance'] if p.get('entity_id') == original)
                    self.assertIn('original_text_excerpt', proof)
                    self.assertTrue(any('reviewtailnonce' in fact for fact in result['facts']))

    def test_whole_original_rank_one_beats_parser_head_rank_at_every_limit_one_surface(self):
        documents = {'winner': b'commonhead\n' + b'padding ' * 4000 + b'\nraretail\n'}
        documents.update({'distractor-' + str(i): b'commonhead ' * 50 + b'\n' + b'padding ' * 4000
                          for i in range(9)})
        state = self.fixture.build(name='natural-rank', documents=documents)
        self.indexed(state)
        expected = state['ids']['winner']
        query = 'commonhead raretail'
        terms = kg._fts_query_terms([query])
        parser = state['store'].search(terms, projects=[state['project']], limit=10, _include_original=False)
        self.assertEqual(len(parser), 10)
        self.assertNotEqual(parser[0]['id'], expected, 'fixture must favor another parser head')
        self.assertGreater(documents['winner'].index(b'raretail'), 16000)
        reader = CollectionStore.open_existing(state['root'])
        originals = original_index.scoped_original_candidates(reader, [state['project']], query, limit=10)
        self.assertEqual(len(originals), 10)
        self.assertEqual(originals[0]['document_id'], expected, 'fixture must have a full-original rank-one winner')
        for strategy in self.STRATEGIES:
            with self.subTest(surface='retrieve', strategy=strategy):
                result = retrieval.retrieve(state['root'], query, projects=[state['project']],
                    search_strategy=strategy, max_entities=1, max_relations=0, candidate_limit=1)
                self.assertEqual(self.ids(result), [expected])
                self.assertIn('raretail', result['facts'][0])
                self.assertEqual(result['candidates'], [expected])
                if strategy == 'indexed-lexical':
                    self.assertLessEqual(result['eligible_documents'], 1)
                self.assertIn('original_text_excerpt', result['fact_provenance'][0])
        for store in (state['store'], reader):
            with self.subTest(surface='search', read_only=store.read_only):
                rows = store.search(terms, projects=[state['project']], limit=1)
                self.assertEqual([row['id'] for row in rows], [expected])
                self.assertIn('raretail', rows[0]['body'])
                self.assertIn('original_text_excerpt', rows[0])
            with self.subTest(surface='graph', read_only=store.read_only):
                nodes = store.graph_page(projects=[state['project']], query=query, limit=1)['nodes']
                self.assertEqual([node['id'] for node in nodes], [expected])
                self.assertIn('raretail', nodes[0]['description'])
                self.assertIn('original_text_excerpt', nodes[0]['source_metadata'])

    def test_repeated_source_hits_deduplicate_without_merging_distinct_originals(self):
        first, second, old = (identity('graph', name) for name in ('first', 'second', 'old'))
        merged = original_index.merge_original_ranks(
            [(first, -999), (old, -998), (old, -997), (second, -996)],
            [(first, -2), (first, -20), (second, -1), (second, -10)], limit=140)
        self.assertEqual(merged, [(first, -2.0), (old, -998.0), (second, -1.0)])
        self.assertEqual(original_index.merge_original_ranks([(old, -999)], [(first, -1)], limit=1),
                         [(first, -1.0)])
        for limit in (0, 141, True):
            with self.subTest(invalid_limit=limit):
                with self.assertRaises(original_index.OriginalTextIndexError):
                    original_index.merge_original_ranks([(old, -1)], [(first, -1)], limit=limit)
        state = self.fixture.build(name='duplicate-originals', documents={
            'primary': integration.specimen(), 'separate-id': integration.specimen()})
        self.indexed(state)
        originals = set(state['ids'].values())
        self.assertEqual(len(originals), 2)
        for strategy in self.STRATEGIES:
            for limit in (1, 2):
                with self.subTest(strategy=strategy, candidate_limit=limit):
                    result = self.lookup(state, 'headotter', strategy, limit)
                    ids = self.ids(result)
                    self.assertEqual(len(ids), limit)
                    self.assertEqual(len(ids), len(set(ids)))
                    self.assertTrue(set(ids) <= originals)
                    self.assertEqual(len(result['candidates']), len(set(result['candidates'])))
                    self.assertLessEqual(len(result['candidates']), limit)
                    if limit == 2:
                        self.assertEqual(set(ids), originals)

    def test_explicit_pins_preserve_order_and_deduplicate_within_140_admission(self):
        state = self.saturated(legacy_count=160, query='pinneedle')
        original = state['ids']['primary']
        reader = CollectionStore.open_existing(state['root'])
        original_rows = original_index.scoped_original_candidates(reader, [state['project']], 'pinneedle', limit=100)
        legacy = state['store'].search('pinneedle', projects=[state['project']], limit=100, _include_original=False)
        merged = original_index.merge_original_ranks([(r['id'], r['rank']) for r in legacy],
            [(r['document_id'], r['rank']) for r in original_rows], limit=100)
        merged_ids = {row[0] for row in merged}
        distinct_pins = [document for document in state['legacy_ids'] if document not in merged_ids][:40]
        self.assertEqual(len(distinct_pins), 40)
        for strategy in self.STRATEGIES:
            for pins in (distinct_pins, [distinct_pins[0]] * 40):
                with self.subTest(strategy=strategy, unique_pins=len(set(pins))):
                    with self.observed_rows() as observations:
                        result = self.lookup(state, 'pinneedle', strategy, 100, also=pins)
                    expected_pins = list(dict.fromkeys(pins))
                    self.assertEqual(result['candidates'][:len(expected_pins)], expected_pins)
                    self.assertIn(original, result['candidates'])
                    self.assertEqual(len(result['candidates']), len(set(result['candidates'])))
                    self.assertLessEqual(len(result['candidates']), 140)
                    self.assertTrue(observations)
                    if strategy == 'indexed-lexical':
                        self.assertLessEqual(result['eligible_documents'], 140)
                        for admission in observations:
                            self.assertIsNotNone(admission['candidate_ids'])
                            self.assertEqual(len(admission['candidate_ids']), len(set(admission['candidate_ids'])))
                            self.assertLessEqual(len(admission['candidate_ids']), 140)
                            self.assertLessEqual(len(admission['returned_ids']), 140)
                            self.assertTrue(set(expected_pins) <= set(admission['candidate_ids']))
                    self.assertLessEqual(len(self.ids(result)), 10)

    def test_final_actual_original_context_guard_receives_callback_and_rejects_late_cancel(self):
        state = self.saturated()
        actual = CollectionStore._verify_original_text_context
        for strategy in self.STRATEGIES:
            stopped = [False]
            final_calls = []
            def cancel_at_final_guard(store, context, projects):
                caller = inspect.currentframe().f_back
                if caller.f_code.co_name == '_verify_retrieval_result':
                    inherited = _READ_CANCELLATION.get()
                    stopped[0] = True
                    final_calls.append({'callback_present': callable(inherited),
                        'callback_sees_cancel': callable(inherited) and inherited(),
                        'real_guard_entered': True})
                return actual(store, context, projects)
            with self.subTest(strategy=strategy):
                with patch.object(CollectionStore, '_verify_original_text_context', cancel_at_final_guard):
                    with self.assertRaises(AldenCancelled):
                        self.lookup(state, 'reviewtailnonce', strategy, 2, cancelled=lambda: stopped[0])
                self.assertEqual(final_calls, [{'callback_present': True,
                    'callback_sees_cancel': True, 'real_guard_entered': True}])
                self.assertTrue(stopped[0])
                self.assertIsNone(_READ_CANCELLATION.get(), 'request scope must be restored after cancellation')


if __name__ == '__main__':
    unittest.main()
