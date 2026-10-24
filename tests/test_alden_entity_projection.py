import copy
import unittest
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_collection import CollectionStore, identity, read_action
from alden_entity_resolution import resolution_action

from scripts.alden_entity_projection import project_groups, project_context_groups


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.graph = {"ok": True, "scope": ["a"], "total_nodes": 100, "total_edges": 200,
                      "nodes": [{"id": i, "source_target": "target", "source_version": i + "v", "label": i}
                                for i in ["a", "b", "c"]],
                      "edges": [{"id": "one", "source": "b", "target": "c", "relation": "mentions", "evidence": {"source_event_ids": ["one"]}},
                                {"id": "two", "source": "c", "target": "b", "relation": "opposes", "evidence": {"source_event_ids": ["two"]}},
                                {"id": "inside", "source": "a", "target": "b", "relation": "quotes", "evidence": {"source_event_ids": ["inside"]}}]}
        self.group = {"resolution_id": "decision", "project": "a", "state": "active", "reason": "source review",
                      "canonical": {"document_id": "a", "target_id": "target"},
                      "members": [{"document_id": i, "target_id": "target", "version": i + "v"} for i in ["a", "b"]]}

    def test_keeps_original_direction_ids_evidence_and_internal_relations(self):
        before = copy.deepcopy(self.graph)
        result = project_groups(self.graph, [self.group], "a")
        self.assertEqual(self.graph, before)
        self.assertEqual([n["id"] for n in result["nodes"]], ["a", "c"])
        self.assertEqual([(e["id"], e["source"], e["target"]) for e in result["edges"]],
                         [("one", "a", "c"), ("two", "c", "a"), ("inside", "a", "a")])
        self.assertEqual([(e["original_source"], e["original_target"]) for e in result["edges"]],
                         [("b", "c"), ("c", "b"), ("a", "b")])
        self.assertEqual([e["evidence"] for e in result["edges"]], [e["evidence"] for e in before["edges"]])
        self.assertEqual(result["resolution_projection"]["internal_relations"][0]["id"], "inside")
        self.assertEqual(result["resolution_projection"]["original_nodes"], before["nodes"])
        self.assertEqual((result["total_nodes"], result["total_edges"]), (100, 200))

    def test_partial_filtered_stale_or_wrong_project_preserves_all_originals(self):
        for change in ["partial", "stale", "scope", "undone", "target"]:
            graph, group = copy.deepcopy(self.graph), copy.deepcopy(self.group)
            if change == "partial": graph["nodes"] = graph["nodes"][1:]
            if change == "stale": graph["nodes"][1]["source_version"] = "next"
            if change == "scope": group["project"] = "b"
            if change == "undone": group["state"] = "reverted"
            if change == "target": graph["nodes"][1]["source_target"] = "private-other"
            self.assertEqual(project_groups(graph, [group], "a"), graph)

    def test_undo_restores_exact_display_without_rewriting_the_source_graph(self):
        merged = project_groups(self.graph, [self.group], "a")
        self.assertEqual(len(merged["nodes"]), 2)
        self.group["state"] = "reverted"
        self.assertEqual(project_groups(self.graph, [self.group], "a"), self.graph)

    def test_overlapping_decisions_do_not_invent_a_transitive_merge(self):
        other = copy.deepcopy(self.group); other["resolution_id"] = "other"
        other["members"] = [{"document_id": i, "target_id": "target", "version": i + "v"} for i in ["b", "c"]]
        other["canonical"]["document_id"] = "b"
        result = project_groups(self.graph, [self.group, other], "a")
        self.assertEqual(result["resolution_projection"]["groups"], 1)
        self.assertEqual([n["id"] for n in result["nodes"]], ["a", "c"])


class ApplicationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform="graph", original_id="original", kind="source",
                                          label="Synthetic source", projects=["one"])
        records = [{"platform": "graph", "original_id": name, "label": "Stored " + name, "text": "Evidence " + name} for name in ["a", "b", "c"]]
        relations = [{"source_platform": "graph", "source_id": a, "target_platform": "graph", "target_id": b,
                      "type": kind, "evidence": {"fixture": True}} for a, b, kind in
                     [("a", "b", "quotes"), ("b", "c", "mentions"), ("c", "b", "opposes")]]
        self.store.ingest(self.target, records, relations=relations)
        self.members = []
        with self.store.database() as db:
            for name in ["a", "b"]:
                document = identity("graph", name)
                version = db.execute("SELECT current_version FROM memberships WHERE target_id=? AND document_id=?",
                                     (self.target, document)).fetchone()[0]
                self.members.append({"document_id": document, "target_id": self.target, "version": version})
        self.plan = resolution_action(self.root, "collection-resolution-preview", {
            "projects": ["one"], "members": self.members,
            "canonical": {"target_id": self.target, "document_id": self.members[0]["document_id"]},
            "reason": "Explicit synthetic review, no semantic gold"})["plan"]

    def page(self, **options):
        return read_action(self.root, "collection-graph", json.dumps({"projects": ["one"], "limit": 100, **options}))

    def apply(self):
        return resolution_action(self.root, "collection-resolution-apply",
                                 {"plan": self.plan, "request_id": "application-apply-00000001"}, explicit_opt_in=True)

    def test_actual_read_boundary_consolidates_and_revert_restores_exact_source_page(self):
        before = self.page()
        applied = self.apply()
        grouped = self.page()
        self.assertEqual((len(before["nodes"]), len(grouped["nodes"])), (3, 2))
        self.assertEqual(grouped["total_nodes"], before["total_nodes"])
        self.assertEqual([e["id"] for e in grouped["edges"]], [e["id"] for e in before["edges"]])
        self.assertEqual(grouped["resolution_projection"]["internal_relations"][0]["relation"], "quotes")
        resolution_action(self.root, "collection-resolution-revert",
                          {"projects": ["one"], "resolution_id": applied["resolution_id"],
                           "expected_revision": applied["revision"], "request_id": "application-revert-0000001"},
                          explicit_opt_in=True)
        self.assertEqual(self.page(), before)

    def test_changed_member_and_partial_search_are_never_silently_consolidated(self):
        self.apply()
        filtered = self.page(search="Stored a")
        self.assertNotIn("resolution_projection", filtered)
        self.store.ingest(self.target, [{"platform": "graph", "original_id": "b", "label": "Stored b", "text": "Revised evidence"}])
        changed = self.page()
        self.assertEqual(len(changed["nodes"]), 3)
        self.assertNotIn("resolution_projection", changed)

    def test_combined_project_scope_retains_shared_original_ids(self):
        self.apply()
        other = self.store.register(platform="graph", original_id="other", kind="source", label="Other source", projects=["two"])
        self.store.ingest(other, [{"platform": "graph", "original_id": "b", "label": "Separate context", "text": "Different project source"}])
        combined = read_action(self.root, "collection-graph", json.dumps({"projects": ["one", "two"], "limit": 100}))
        self.assertEqual(len(combined["nodes"]), 3)
        self.assertEqual(combined["resolution_projection"]["groups"], 0)
        self.assertEqual(combined["resolution_projection"]["hidden_sources"], 0)
        notes = [note for node in combined["nodes"] for note in node.get("resolution_contexts", [])]
        self.assertEqual({note["project"] for note in notes}, {"one"})
        self.assertTrue(all(not note["consolidated"] and not note["display_matches"] for note in notes))


class UnionProjectionTests(ProjectionTests):
    def setUp(self):
        super().setUp()
        self.graph["scope"] = ["a", "b"]
        for node in self.graph["nodes"]:
            node["source_contexts"] = [
                {"project": "a", "target_id": "target", "version": node["id"] + "v", "label": "a:" + node["id"]},
                {"project": "b", "target_id": "other", "version": node["id"] + "v2", "label": "b:" + node["id"]}]
        self.other = copy.deepcopy(self.group)
        self.other.update(project="b", resolution_id="other-decision")
        self.other["canonical"]["target_id"] = "other"
        for member in self.other["members"]:
            member["target_id"] = "other"; member["version"] = member["document_id"] + "v2"

    def union(self, *groups):
        return project_context_groups(self.graph, list(groups), ["a", "b"])

    def test_one_project_review_is_visible_without_erasing_another_context(self):
        before = copy.deepcopy(self.graph)
        result = self.union(self.group)
        self.assertEqual(result["edges"], before["edges"])
        self.assertEqual([n["id"] for n in result["nodes"]], ["a", "b", "c"])
        self.assertEqual(result["resolution_projection"]["groups"], 0)
        self.assertEqual(result["nodes"][0]["resolution_contexts"][0]["members"][1]["label"], "a:b")
        self.assertFalse(result["nodes"][0]["resolution_contexts"][0]["consolidated"])
        self.assertEqual(self.graph, before)

    def test_all_current_contexts_agree_before_consolidation_and_direction_is_preserved(self):
        result = self.union(self.group, self.other)
        self.assertEqual([n["id"] for n in result["nodes"]], ["a", "c"])
        self.assertEqual([(e["id"], e["source"], e["target"]) for e in result["edges"]],
                         [("one", "a", "c"), ("two", "c", "a"), ("inside", "a", "a")])
        self.assertEqual([e["evidence"] for e in result["edges"]], [e["evidence"] for e in self.graph["edges"]])
        notes = result["nodes"][0]["resolution_contexts"]
        self.assertEqual({n["project"] for n in notes}, {"a", "b"})
        self.assertTrue(all(n["consolidated"] for n in notes))
        self.assertEqual([n["display_matches"] for n in notes], [True, False])
        self.assertEqual((result["total_nodes"], result["total_edges"]), (100, 200))
        self.assertEqual(result["resolution_projection"]["original_nodes"], self.graph["nodes"])

    def test_conflicting_representatives_or_unreviewed_memberships_keep_originals(self):
        for variant in ("canonical", "additional-membership", "stale", "ambiguous"):
            with self.subTest(variant=variant):
                graph = copy.deepcopy(self.graph); group = copy.deepcopy(self.other)
                groups = [self.group, group]
                if variant == "canonical": group["canonical"]["document_id"] = "b"
                if variant == "additional-membership":
                    graph["nodes"][0]["source_contexts"].append({"project": "b", "target_id": "third", "version": "av3", "label": "third"})
                if variant == "stale": group["members"][0]["version"] = "old"
                if variant == "ambiguous":
                    conflict = copy.deepcopy(self.group); conflict["resolution_id"] = "conflict"; conflict["canonical"]["document_id"] = "b"; groups.append(conflict)
                result = project_context_groups(graph, groups, ["a", "b"])
                self.assertEqual(result["edges"], graph["edges"])
                self.assertEqual([n["id"] for n in result["nodes"]], ["a", "b", "c"])
                self.assertEqual(result["resolution_projection"]["groups"], 0)

    def test_partial_or_missing_current_context_does_not_invent_a_review(self):
        for variant in ("partial", "missing", "racing-version"):
            with self.subTest(variant=variant):
                graph = copy.deepcopy(self.graph)
                if variant == "partial": graph["nodes"] = graph["nodes"][1:]
                if variant == "missing": del graph["nodes"][0]["source_contexts"]
                if variant == "racing-version": graph["nodes"][0]["source_version"] = "no-longer-current"
                self.assertEqual(project_context_groups(graph, [self.group, self.other], ["a", "b"]), graph)


if __name__ == "__main__":
    unittest.main()
