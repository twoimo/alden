"""Independent, bounded pair-sampling regressions for semantic layout evidence.

All vectors and proofs are synthetic.  The exhaustive oracle enumerates the
eligible unordered graph independently of the production sampling traversal.
No collection files, embedding services, or graph mutations are involved.
"""

from array import array
from collections import Counter
from dataclasses import replace
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import alden_semantic_affinity as sa


DIMENSION = sa.E5Profile().dimension


def synthetic_loaded(specs):
    """Build a deterministic caller-ordered input, independent of store data."""
    entries = []
    for name, projects, angle in specs:
        vec = array("f", [math.cos(angle), math.sin(angle)] + [0.] * (DIMENSION - 2))
        norm = math.sqrt(sum(value * value for value in vec))
        entries.append(({"id": name, "projects": sorted(set(projects))}, vec, norm))
    return sorted(entries, key=lambda entry: entry[0]["id"])


def exhaustive_oracle(loaded, threshold, neighbors, candidate_cap):
    """Independently compute *all* allowed pairs and each node's top-k union."""
    adjacency = [[] for _ in loaded]
    eligible = {}
    for i in range(len(loaded)):
        for j in range(i + 1, len(loaded)):
            left, x, nx = loaded[i]
            right, y, ny = loaded[j]
            overlap = sorted(set(left["projects"]).intersection(right["projects"]))
            if not overlap:
                continue
            score = max(-1., min(1., math.fsum(a * b for a, b in zip(x, y)) / (nx * ny)))
            eligible[i, j] = (score, overlap)
            if score >= threshold:
                adjacency[i].append((score, j))
                adjacency[j].append((score, i))
    chosen = set()
    for owner, neighbors_for_owner in enumerate(adjacency):
        for _, peer in sorted(neighbors_for_owner, reverse=True)[:neighbors]:
            chosen.add((min(owner, peer), max(owner, peer)))
    ranked = sorted(chosen, key=lambda pair: (
        -eligible[pair][0], loaded[pair[0]][0]["id"], loaded[pair[1]][0]["id"]))
    candidates = [
        {"kind": "semantic_layout_affinity", "source": loaded[i][0]["id"],
         "target": loaded[j][0]["id"], "cosine": eligible[i, j][0],
         "projects": eligible[i, j][1]}
        for i, j in ranked[:candidate_cap]
    ]
    return eligible, ranked, candidates


class SemanticPairSamplingTests(unittest.TestCase):
    def sample(self, loaded, *, max_pairs=100, max_products=50331648,
               threshold=-1., neighbors=8, max_candidates=100, cancelled=lambda: False):
        budgets = replace(sa.Budgets(), max_pairs=max_pairs, max_products=max_products,
                          neighbors_per_node=neighbors, max_candidates=max_candidates)
        return sa._candidates(loaded, budgets, threshold, sa._Guard(budgets, cancelled))

    def assert_participation(self, coverage, *, eligible, compared, minimum, maximum):
        self.assertEqual(coverage["pair_sampling"], "project-round-robin-v1")
        self.assertEqual(coverage["eligible_vector_nodes"], eligible)
        self.assertEqual(coverage["vector_nodes_compared"], compared)
        self.assertEqual(coverage["comparison_min"], minimum)
        self.assertEqual(coverage["comparison_max"], maximum)
        if eligible:
            self.assertAlmostEqual(coverage["node_pair_coverage"], compared / eligible)
        else:
            self.assertIn(coverage["node_pair_coverage"], (None, 0))

    def assert_candidates_match_oracle(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for candidate, oracle in zip(actual, expected):
            self.assertEqual({key: value for key, value in candidate.items() if key != "cosine"},
                             {key: value for key, value in oracle.items() if key != "cosine"})
            self.assertAlmostEqual(candidate["cosine"], oracle["cosine"], places=6)

    def test_one_project_tight_budget_covers_each_even_group_node_once(self):
        loaded = synthetic_loaded((f"n{i}", ["one"], 0.) for i in range(8))
        candidates, coverage = self.sample(loaded, max_pairs=4)
        degree = Counter(node for pair in candidates for node in (pair["source"], pair["target"]))
        self.assertEqual(dict(degree), {entry[0]["id"]: 1 for entry in loaded})
        self.assertEqual(coverage["eligible_same_project_pairs"], 28)
        self.assertEqual(coverage["pairs_evaluated"], 4)
        self.assertAlmostEqual(coverage["pair_coverage"], 4 / 28)
        self.assertTrue(coverage["pair_budget_exhausted"])
        self.assert_participation(coverage, eligible=8, compared=8, minimum=1, maximum=1)
        self.assertLess(coverage["pair_coverage"], coverage["node_pair_coverage"])

    def test_odd_group_first_matching_has_one_bye_not_an_id_prefix_star(self):
        loaded = synthetic_loaded((f"n{i}", ["odd"], 0.) for i in range(5))
        candidates, coverage = self.sample(loaded, max_pairs=2)
        degree = Counter(node for pair in candidates for node in (pair["source"], pair["target"]))
        self.assertEqual(len(degree), 4)
        self.assertTrue(all(count == 1 for count in degree.values()))
        self.assertEqual(coverage["eligible_same_project_pairs"], 10)
        self.assertEqual(coverage["pairs_evaluated"], 2)
        self.assert_participation(coverage, eligible=5, compared=4, minimum=0, maximum=1)

    def test_project_matchings_interleave_and_rotate_with_partial_next_round(self):
        loaded = synthetic_loaded(
            [(f"a{i}", ["alpha"], 0.) for i in range(4)] +
            [(f"z{i}", ["zeta"], 0.) for i in range(4)])
        candidates, coverage = self.sample(loaded, max_pairs=6)
        by_project = Counter(candidate["projects"][0] for candidate in candidates)
        self.assertEqual(by_project, {"alpha": 2, "zeta": 4})
        self.assertEqual(len({(c["source"], c["target"]) for c in candidates}), 6)
        self.assertEqual(coverage["eligible_same_project_pairs"], 12)
        self.assertEqual(coverage["cross_project_pairs_excluded"] if "cross_project_pairs_excluded" in coverage
                         else coverage["possible_vector_pairs"] - coverage["eligible_same_project_pairs"], 16)
        self.assert_participation(coverage, eligible=8, compared=8, minimum=1, maximum=2)

    def test_even_and_odd_full_rounds_exhaust_unique_unordered_pairs(self):
        for size in (5, 6):
            with self.subTest(size=size):
                loaded = synthetic_loaded((f"n{i}", ["shared"], 0.) for i in range(size))
                candidates, coverage = self.sample(loaded)
                all_pairs = {(loaded[i][0]["id"], loaded[j][0]["id"])
                             for i in range(size) for j in range(i + 1, size)}
                selected = [(item["source"], item["target"]) for item in candidates]
                self.assertEqual(set(selected), all_pairs)
                self.assertEqual(len(selected), len(all_pairs))
                self.assertEqual(coverage["pairs_evaluated"], len(all_pairs))
                self.assertEqual(coverage["eligible_same_project_pairs"], len(all_pairs))
                self.assertEqual(coverage["pair_coverage"], 1)
                self.assertFalse(coverage["pair_budget_exhausted"])
                self.assert_participation(coverage, eligible=size, compared=size,
                                          minimum=size - 1, maximum=size - 1)

    def test_overlap_is_one_pair_with_all_shared_project_labels(self):
        loaded = synthetic_loaded([
            ("a", ["alpha", "beta"], 0.), ("b", ["alpha"], 0.),
            ("c", ["beta"], 0.), ("d", ["alpha", "beta"], 0.),
            ("e", ["beta"], 0.), ("isolated", ["gamma"], 0.),
        ])
        eligible, _, oracle = exhaustive_oracle(loaded, -1., 8, 100)
        candidates, coverage = self.sample(loaded)
        self.assertEqual(len(eligible), 8)
        self.assertEqual(len(candidates), len(eligible))
        self.assertEqual(coverage["pairs_evaluated"], len(eligible))
        self.assertEqual(coverage["eligible_same_project_pairs"], len(eligible))
        self.assert_candidates_match_oracle(candidates, oracle)
        both = next(c for c in candidates if (c["source"], c["target"]) == ("a", "d"))
        self.assertEqual(both["projects"], ["alpha", "beta"])
        self.assertFalse(any("isolated" in (c["source"], c["target"]) for c in candidates))
        self.assert_participation(coverage, eligible=5, compared=5, minimum=2, maximum=4)

    def test_singletons_and_disjoint_scopes_never_create_cross_project_pairs(self):
        loaded = synthetic_loaded([
            ("solo-a", ["a"], 0.), ("solo-b", ["b"], 0.),
            ("paired-a", ["c"], 0.), ("paired-b", ["c"], 0.),
        ])
        candidates, coverage = self.sample(loaded)
        self.assertEqual([(c["source"], c["target"], c["projects"]) for c in candidates],
                         [("paired-a", "paired-b", ["c"])])
        self.assertEqual(coverage["eligible_same_project_pairs"], 1)
        self.assertEqual(coverage["pairs_evaluated"], 1)
        self.assert_participation(coverage, eligible=2, compared=2, minimum=1, maximum=1)
        only_singletons = synthetic_loaded([("x", ["a"], 0.), ("y", ["b"], 0.)])
        empty, no_pairs = self.sample(only_singletons)
        self.assertEqual(empty, [])
        self.assertEqual(no_pairs["eligible_same_project_pairs"], 0)
        self.assertEqual(no_pairs["pairs_evaluated"], 0)
        self.assertIsNone(no_pairs["pair_coverage"])
        self.assertFalse(no_pairs["pair_budget_exhausted"])
        self.assert_participation(no_pairs, eligible=0, compared=0, minimum=0, maximum=0)

    def test_complete_cosines_threshold_heaps_and_candidate_cap_match_exhaustive_oracle(self):
        angles = (.02, .34, 1.01, 1.63, 2.45, 2.91)
        loaded = synthetic_loaded((f"n{i}", ["one"], angle) for i, angle in enumerate(angles))
        threshold, neighbors, cap = .15, 2, 3
        eligible, ranked, oracle = exhaustive_oracle(loaded, threshold, neighbors, cap)
        candidates, coverage = self.sample(loaded, threshold=threshold, neighbors=neighbors,
                                            max_candidates=cap)
        self.assertEqual(coverage["pairs_evaluated"], len(eligible))
        self.assertEqual(coverage["pairs_above_threshold"],
                         sum(score >= threshold for score, _ in eligible.values()))
        self.assertEqual(coverage["neighbor_union_candidates"], len(ranked))
        self.assertEqual(coverage["candidates_truncated"], max(0, len(ranked) - cap))
        self.assert_candidates_match_oracle(candidates, oracle)
        self.assertEqual(coverage["pair_coverage"], 1)
        self.assert_participation(coverage, eligible=6, compared=6, minimum=5, maximum=5)

    def test_project_sorted_input_and_repeat_execution_are_deterministic(self):
        specs = [(f"n{i}", (["alpha", "beta"] if i in (1, 4) else
                           ["alpha"] if i < 4 else ["beta"]), i * .29)
                 for i in range(7)]
        original = synthetic_loaded(specs)
        by_id = {entry[0]["id"]: entry for entry in original}
        refs = [{"id": proof["id"], "source_target": "synthetic",
                 "source_version": "synthetic-v1"} for proof, _, _ in original]
        reports = []
        for requested_projects, requested_nodes in ((["beta", "alpha"], refs[::-1]),
                                                     (["alpha", "beta"], refs)):
            projects, nodes = sa._inputs(requested_projects, requested_nodes, sa.Budgets())
            self.assertEqual(projects, ["alpha", "beta"])
            loaded = [by_id[node["id"]] for node in nodes]
            reports.append(self.sample(loaded, max_pairs=5, neighbors=3))
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[0], self.sample(original, max_pairs=5, neighbors=3))
        # The sampler also canonicalizes direct, unsorted loaded input without
        # mutating the caller's list or its graph reference order.
        unsorted = list(reversed(original))
        ids_before = [entry[0]["id"] for entry in unsorted]
        self.assertEqual(self.sample(unsorted, max_pairs=5, neighbors=3), reports[0])
        self.assertEqual([entry[0]["id"] for entry in unsorted], ids_before)
        self.assertEqual([node["id"] for node in refs[::-1]],
                         [proof["id"] for proof, _, _ in unsorted])

    def test_pair_and_dimension_product_ceilings_stop_at_exact_charge(self):
        loaded = synthetic_loaded((f"n{i}", ["one"], 0.) for i in range(6))
        for pair_cap, product_cap, expected in ((2, 50331648, 2), (100, DIMENSION * 2, 2),
                                                 (100, DIMENSION - 1, 0), (3, DIMENSION, 1)):
            with self.subTest(pair_cap=pair_cap, product_cap=product_cap):
                candidates, coverage = self.sample(loaded, max_pairs=pair_cap,
                                                    max_products=product_cap)
                self.assertEqual(len(candidates), expected)
                self.assertEqual(coverage["pairs_evaluated"], expected)
                self.assertEqual(coverage["dimension_products"], expected * DIMENSION)
                self.assertLessEqual(coverage["dimension_products"], product_cap)
                self.assertTrue(coverage["pair_budget_exhausted"])
                self.assertAlmostEqual(coverage["pair_coverage"], expected / 15)
        _, zero = self.sample(loaded, max_products=DIMENSION - 1)
        self.assert_participation(zero, eligible=6, compared=0, minimum=0, maximum=0)

    def test_cancellation_and_deadline_abort_without_publishing_partial_pairs(self):
        loaded = synthetic_loaded((f"n{i}", ["one"], 0.) for i in range(12))
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 6

        with self.assertRaisesRegex(RuntimeError, "affinity_cancelled"):
            self.sample(loaded, max_pairs=60, cancelled=cancelled)
        self.assertGreaterEqual(calls, 6)

        budget = sa.Budgets()
        guard = sa._Guard(budget, lambda: False)
        checks = 0
        original_check = guard.check

        def timeout_after_progress():
            nonlocal checks
            checks += 1
            if checks == 6:
                raise sa.BudgetExceeded("affinity_wall_budget")
            original_check()

        with patch.object(guard, "check", side_effect=timeout_after_progress):
            with self.assertRaisesRegex(sa.BudgetExceeded, "affinity_wall_budget"):
                sa._candidates(loaded, budget, -1., guard)
        self.assertEqual(checks, 6)

    def test_report_state_remains_partial_for_pair_and_candidate_truncation(self):
        loaded = synthetic_loaded((f"n{i}", ["one"], 0.) for i in range(4))
        entries = {entry[0]["id"]: entry for entry in loaded}
        refs = [{"id": name, "source_version": "synthetic-v1", "source_target": "synthetic"}
                for name in entries]
        cache, db = Mock(), Mock()
        cache.execute.return_value = [("vectors",), ("texts",)]
        db.execute.return_value.fetchone.return_value = ("synthetic-journal",)

        def load_stub(_db, _cache, _store, node, _projects, _profile, _guard):
            return entries[node["id"]], None

        with patch.object(sa, "_validate_reads"), patch.object(sa, "_load", side_effect=load_stub):
            for limits, state, pairs, candidate_cap in (
                ({"max_pairs": 2}, "bounded_partial", 2, 2),
                ({"max_candidates": 1}, "bounded_partial", 6, 1),
                ({}, "bounded_ready", 6, 6),
            ):
                with self.subTest(limits=limits):
                    budgets = replace(sa.Budgets(), neighbors_per_node=8, **limits)
                    report = sa._affinity_on_reads(None, db, cache, ["one"], refs,
                                                   budgets=budgets, min_cosine=-1.)
                    self.assertEqual(report["state"], state)
                    self.assertFalse(report["identity_merge"])
                    self.assertFalse(report["factual_relationship"])
                    self.assertEqual(report["coverage"]["pairs_evaluated"], pairs)
                    self.assertEqual(report["coverage"]["candidates_returned"], candidate_cap)
                    self.assert_participation(report["coverage"], eligible=4, compared=4,
                                              minimum=1 if pairs == 2 else 3,
                                              maximum=1 if pairs == 2 else 3)
                    self.assertEqual(report["coverage"]["pair_coverage"], pairs / 6)
                    self.assertEqual(report["schema"], "alden-semantic-affinity-v1")


if __name__ == "__main__":
    unittest.main()
