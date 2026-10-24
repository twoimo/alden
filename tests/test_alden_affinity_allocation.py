"""Incidence allocation preserves admitted candidates and consumer bounds."""
import itertools
import math
from array import array
from collections import Counter
from dataclasses import replace
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_semantic_affinity as affinity


class Guard:
    def __init__(self, fail_at=None):
        self.calls, self.fail_at = 0, fail_at

    def check(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError('affinity_cancelled')


def entry(name, project='P', axis=0):
    values = array('f', [0.] * affinity.E5Profile().dimension)
    values[axis] = 1.
    return ({'id': name, 'projects': [project]}, values, math.sqrt(sum(v * v for v in values)))


class CandidateAllocationTests(unittest.TestCase):
    def select(self, ranked, cap):
        return affinity._select_candidate_pairs(ranked, cap, Guard())

    def test_disjoint_candidates_represent_more_nodes_than_a_high_score_prefix(self):
        ranked = [(0, 1), (0, 2), (2, 3)]
        selected = self.select(ranked, 2)
        self.assertEqual(selected, [(0, 1), (2, 3)])
        self.assertEqual(len({v for p in selected for v in p}), 4)
        self.assertEqual(len({v for p in ranked[:2] for v in p}), 3)

    def test_three_phases_restore_original_rank_without_duplicate_selection(self):
        ranked = [(0, 1), (0, 2), (1, 2), (3, 4), (0, 3)]
        self.assertEqual(self.select(ranked, 4), ranked[:4])

    def test_star_reports_real_endpoint_capacity_instead_of_inventing_neighbors(self):
        ranked = [(0, i) for i in range(1, 10)]
        selected = self.select(ranked, 8)
        self.assertEqual(selected, ranked[:4])
        self.assertEqual(len({v for p in selected for v in p}), 5)
        self.assertLess(len(selected), min(8, len(ranked)))

    def test_empty_small_and_untruncated_inputs_keep_existing_order(self):
        ranked = [(0, 1), (2, 3)]
        self.assertEqual(self.select([], 1), [])
        self.assertEqual(self.select(ranked, 1), ranked[:1])
        self.assertEqual(self.select(ranked, 2), ranked)
        self.assertEqual(self.select(ranked, 3), ranked)

    def test_greedy_selection_does_not_claim_global_maximum_coverage(self):
        ranked = [(1, 2), (0, 1), (2, 3)]
        selected = self.select(ranked, 2)
        self.assertEqual(selected, ranked[:2])
        self.assertEqual(len({v for p in selected for v in p}), 3)
        self.assertEqual(len({v for p in ranked[1:] for v in p}), 4)

    def test_all_small_subgraphs_preserve_subset_order_caps_and_monotonic_sets(self):
        edges = list(itertools.combinations(range(4), 2))
        for mask in range(1 << len(edges)):
            ranked = [pair for i, pair in enumerate(edges) if mask & (1 << i)]
            previous = set()
            for cap in range(1, len(edges) + 1):
                selected = self.select(ranked, cap)
                self.assertEqual(selected, [pair for pair in ranked if pair in set(selected)])
                self.assertEqual(len(selected), min(cap, len(ranked)))
                self.assertEqual(len(set(selected)), len(selected))
                self.assertTrue(previous <= set(selected))
                self.assertLessEqual(max(Counter(v for p in selected for v in p).values(), default=0), 4)
                previous = set(selected)

    def test_cancel_during_a_later_block_propagates_without_returning_partial_pairs(self):
        guard = Guard(fail_at=2)
        with self.assertRaisesRegex(RuntimeError, 'affinity_cancelled'):
            affinity._select_candidate_pairs([(0, i) for i in range(1, 200)], 1024, guard)
        self.assertEqual(guard.calls, 2)

    def test_guard_work_is_bounded_by_four_linear_scans(self):
        ranked = list(itertools.combinations(range(64), 2))
        guard = Guard()
        self.assertLessEqual(len(affinity._select_candidate_pairs(ranked, 1024, guard)), 1024)
        self.assertLessEqual(guard.calls, 4 * math.ceil(len(ranked) / 64) + 1)

    def test_cancel_during_return_construction_is_checked_after_the_chosen_prefix(self):
        guard = Guard(fail_at=3)
        with self.assertRaisesRegex(RuntimeError, 'affinity_cancelled'):
            affinity._select_candidate_pairs([(0, i) for i in range(1, 200)], 1, guard)
        self.assertEqual(guard.calls, 3)

    def test_actual_candidate_pipeline_is_stable_under_loaded_order_and_score_ties(self):
        loaded = [entry('P:' + name) for name in 'abcd']
        budgets = replace(affinity.Budgets(), max_candidates=2)
        expected = affinity._candidates(loaded, budgets, .65, Guard())
        self.assertEqual([(c['source'], c['target']) for c in expected[0]], [('P:a', 'P:b'), ('P:c', 'P:d')])
        for order in itertools.permutations(loaded):
            self.assertEqual(affinity._candidates(order, budgets, .65, Guard()), expected)
        counts = expected[1]
        self.assertEqual(counts['candidate_selection'], 'incidence-degree-v1')
        self.assertEqual(counts['candidate_nodes_available'], 4)
        self.assertEqual(counts['candidate_nodes_selected'], 4)
        self.assertEqual(counts['candidate_nodes_omitted'], 0)
        self.assertEqual(counts['candidate_max_degree'], 1)
        self.assertEqual(counts['candidates_truncated'], 4)

    def test_project_permissions_and_threshold_precede_coverage_allocation(self):
        loaded = [entry('P:a'), entry('P:b'), entry('P:orthogonal', axis=1),
                  entry('Q:a', 'Q'), entry('Q:b', 'Q')]
        candidates, counts = affinity._candidates(loaded, affinity.Budgets(), .65, Guard())
        self.assertEqual({(c['source'], c['target']) for c in candidates}, {('P:a', 'P:b'), ('Q:a', 'Q:b')})
        self.assertTrue(all(c['projects'] in (['P'], ['Q']) and c['cosine'] == 1 for c in candidates))
        self.assertEqual(counts['pairs_above_threshold'], 2)
        self.assertEqual(counts['candidate_nodes_available'], 4)
        self.assertEqual(counts['candidate_nodes_selected'], 4)

    def test_endpoint_limit_matches_the_actual_typescript_consumer_contract(self):
        source = (Path(__file__).resolve().parents[1] / 'desktop/src/knowledge/semantic-layout.ts').read_text()
        limit = re.search(r'affinityDegree:\s*(\d+)', source)
        self.assertIsNotNone(limit)
        self.assertEqual(affinity.CANDIDATE_DEGREE_LIMIT, int(limit.group(1)))


if __name__ == '__main__':
    unittest.main()
