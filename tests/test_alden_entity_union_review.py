"""Independent synthetic regressions at the CollectionStore application read boundary.

No projection functions are mocked or called directly: reviewed overlays reach
the displayed graph only through the same read_action used by the app. Every
database, source blob and resolution decision belongs to a temporary fixture.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alden_collection import CollectionStore, digest, encoded, identity, read_action
from alden_entity_resolution import resolution_action


class EntityUnionReadBoundaryTests(unittest.TestCase):
    PROJECTS = ["union-one", "union-two"]
    SOURCE_TABLES = ("meta", "targets", "target_projects", "documents", "versions",
                     "memberships", "relations", "relation_versions", "runs", "events",
                     "target_snapshots", "event_counts")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="alden-union-synthetic-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.store = CollectionStore(self.root)
        self.target = self.register("shared", self.PROJECTS)
        self.records = [self.record("alpha", "Alpha original", "alphakey"),
                        self.record("beta", "Beta original", "betakey"),
                        self.record("neighbor", "Neighbor original", "neighborkey")]
        self.store.ingest(self.target, self.records, relations=[
            self.relation("alpha", "beta", "quotes"),
            self.relation("beta", "neighbor", "supports"),
            self.relation("neighbor", "beta", "opposes")])
        self.alpha, self.beta, self.neighbor = [identity("graph", "synthetic-union-" + name)
                                                 for name in ("alpha", "beta", "neighbor")]
        self.request_sequence = 0

    @staticmethod
    def record(name, label, text):
        return {"platform": "graph", "original_id": "synthetic-union-" + name,
                "label": label, "text": text, "source_url": "local://synthetic-union/" + name}

    @staticmethod
    def relation(source, target, kind):
        return {"source_platform": "graph", "source_id": "synthetic-union-" + source,
                "target_platform": "graph", "target_id": "synthetic-union-" + target,
                "type": kind, "evidence": {"fixture": "isolated-union", "direction": source + ">" + target}}

    def register(self, name, projects, permission="local-private"):
        return self.store.register(platform="graph", original_id="synthetic-union-target-" + name,
                                   kind="source", label="Synthetic " + name,
                                   projects=projects, permission=permission)

    def member(self, target, name):
        document = identity("graph", "synthetic-union-" + name)
        with self.store.database() as db:
            version = db.execute("SELECT current_version FROM memberships WHERE target_id=? AND document_id=?",
                                 (target, document)).fetchone()[0]
        return {"target_id": target, "document_id": document, "version": version}

    def decide(self, project, canonical="alpha", target=None):
        target = self.target if target is None else target
        members = [self.member(target, name) for name in ("alpha", "beta")]
        reason = "Explicit synthetic review for " + project + "; not semantic truth"
        preview = resolution_action(self.root, "collection-resolution-preview", {
            "projects": [project], "members": members,
            "canonical": {"target_id": target,
                          "document_id": identity("graph", "synthetic-union-" + canonical)},
            "reason": reason})
        self.request_sequence += 1
        result = resolution_action(self.root, "collection-resolution-apply", {
            "plan": preview["plan"], "request_id": "union-apply-" + str(self.request_sequence)},
            explicit_opt_in=True)
        self.assertTrue(result["ok"])
        return {"project": project, "resolution_id": result["resolution_id"],
                "reason": reason, "members": members, "result": result}

    def agree(self):
        return [self.decide(project) for project in self.PROJECTS]

    def undo(self, decision):
        history = resolution_action(self.root, "collection-resolution-history", {
            "projects": [decision["project"]]})
        self.request_sequence += 1
        return resolution_action(self.root, "collection-resolution-revert", {
            "projects": [decision["project"]], "resolution_id": decision["resolution_id"],
            "expected_revision": history["revision"],
            "request_id": "union-undo-" + str(self.request_sequence)}, explicit_opt_in=True)

    def page(self, projects=None, **options):
        # JSON serialization exercises the real application query boundary.
        query = {"projects": self.PROJECTS if projects is None else projects, "limit": 100, **options}
        result = read_action(self.root, "collection-graph", json.dumps(query))
        self.assertTrue(result["ok"])
        return result

    def raw(self, projects=None, **options):
        return self.store.graph_page(projects=self.PROJECTS if projects is None else projects,
                                     limit=options.pop("limit", 100), **options)

    @staticmethod
    def node_map(page):
        return {node["id"]: node for node in page["nodes"]}

    def source_fingerprint(self):
        # All reads here refer exclusively to this test's generated database.
        with self.store.database() as db:
            tables = {table: sorted(encoded(dict(row)).decode()
                                    for row in db.execute("SELECT * FROM " + table))
                      for table in self.SOURCE_TABLES}
        tables["synthetic_blobs"] = sorted((blob.name, digest(blob.read_bytes()))
                                            for blob in self.store.blobs.iterdir())
        return digest(encoded(tables))

    def expected_sources(self, document, projects):
        marks = ",".join("?" for _ in projects)
        with self.store.database() as db:
            return [dict(row) for row in db.execute("""SELECT p.project,m.target_id,
                 m.current_version AS version,v.label FROM memberships m
                 JOIN versions v ON v.id=m.current_version AND v.document_id=m.document_id
                 JOIN target_projects p ON p.target_id=m.target_id
                 WHERE m.document_id=? AND m.availability='available'
                   AND p.permission!='denied' AND p.project IN (""" + marks + ")",
                 (document, *projects))]

    def assert_sources(self, page, projects):
        for node in page["nodes"]:
            with self.subTest(node=node["id"], field="source_contexts"):
                self.assertIn("source_contexts", node)
                contexts = node["source_contexts"]
                self.assertIsInstance(contexts, list)
                self.assertEqual(sorted(encoded(item) for item in contexts),
                                 sorted(encoded(item) for item in self.expected_sources(node["id"], projects)))

    def assert_reviews(self, page, decisions, *, consolidated, matches):
        if decisions:
            projection = page["resolution_projection"]
            self.assertEqual(projection["groups"], 1 if consolidated else 0)
            self.assertEqual(projection["display_nodes"], len(page["nodes"]))
            self.assertEqual(projection["source_edges"], len(page["edges"]))
            self.assertEqual(projection["source_nodes"], len(projection["original_nodes"]))
            self.assertEqual(projection["hidden_sources"], 1 if consolidated else 0)
        for node in page["nodes"]:
            expected = [d for d in decisions if any(m["document_id"] == node["id"] for m in d["members"])]
            contexts = node.get("resolution_contexts", [])
            self.assertIsInstance(contexts, list)
            self.assertEqual({context["resolution_id"] for context in contexts},
                             {d["resolution_id"] for d in expected}, node["id"])
            self.assertEqual(len(contexts), len(expected), "Do not duplicate project review metadata")
            for context in contexts:
                decision = next(d for d in expected if d["resolution_id"] == context["resolution_id"])
                self.assertEqual(context["project"], decision["project"])
                self.assertEqual(context["reason"], decision["reason"])
                self.assertEqual(context["kind"], "explicit_user_decision")
                self.assertIs(context["consolidated"], consolidated)
                self.assertIsInstance(context["display_matches"], bool)
                if matches is not None:
                    self.assertIs(context["display_matches"], matches)
                readable = []
                with self.store.database() as db:
                    for member in decision["members"]:
                        label = db.execute("SELECT label FROM versions WHERE id=?", (member["version"],)).fetchone()[0]
                        readable.append({"id": member["document_id"], "label": label,
                                         "source_version": member["version"], "source_target": member["target_id"]})
                self.assertEqual(sorted(encoded(item) for item in context["members"]),
                                 sorted(encoded(item) for item in readable))

    def assert_edges(self, before, after, mapping=None):
        mapping = {} if mapping is None else mapping
        original = {edge["id"]: edge for edge in before["edges"]}
        displayed = {edge["id"]: edge for edge in after["edges"]}
        self.assertEqual(set(displayed), set(original))
        self.assertEqual(len(after["edges"]), len(before["edges"]))
        for edge_id, edge in displayed.items():
            prior = original[edge_id]
            self.assertEqual(edge["source"], mapping.get(prior["source"], prior["source"]))
            self.assertEqual(edge["target"], mapping.get(prior["target"], prior["target"]))
            self.assertEqual(edge.get("original_source", edge["source"]), prior["source"])
            self.assertEqual(edge.get("original_target", edge["target"]), prior["target"])
            for key, value in prior.items():
                if key not in {"source", "target", "original_source", "original_target"}:
                    self.assertEqual(edge[key], value, (edge_id, key))
        self.assertEqual(after["total_nodes"], before["total_nodes"])
        self.assertEqual(after["total_edges"], before["total_edges"])

    def test_graph_page_exposes_every_current_permitted_source_context(self):
        page = self.raw()
        self.assertEqual(set(self.node_map(page)), {self.alpha, self.beta, self.neighbor})
        self.assert_sources(page, self.PROJECTS)
        self.assert_sources(self.raw([self.PROJECTS[0]]), [self.PROJECTS[0]])
        self.assert_reviews(self.page(), [], consolidated=False, matches=None)

    def test_complete_agreement_consolidates_then_one_undo_restores_originals(self):
        source_before = self.source_fingerprint()
        before = self.page()
        first = self.decide(self.PROJECTS[0])
        one_review = self.page()
        self.assertEqual(set(self.node_map(one_review)), {self.alpha, self.beta, self.neighbor})
        self.assert_reviews(one_review, [first], consolidated=False, matches=True)
        second = self.decide(self.PROJECTS[1])
        agreed = self.page()
        self.assertEqual(set(self.node_map(agreed)), {self.alpha, self.neighbor})
        self.assert_reviews(agreed, [first, second], consolidated=True, matches=True)
        self.assert_edges(before, agreed, {self.beta: self.alpha})
        internal = [e for e in agreed["edges"] if e["source"] == e["target"]]
        self.assertEqual(len(internal), 1)
        self.assertEqual(internal[0]["relation"], "quotes")
        self.assertEqual(set(self.node_map(self.raw())), {self.alpha, self.beta, self.neighbor})
        self.assert_sources(self.raw(), self.PROJECTS)
        self.assertEqual(self.source_fingerprint(), source_before)
        self.assertEqual(self.undo(second)["state"], "reverted")
        restored = self.page()
        self.assertEqual(set(self.node_map(restored)), {self.alpha, self.beta, self.neighbor})
        self.assert_reviews(restored, [first], consolidated=False, matches=True)
        self.assert_edges(before, restored)
        self.undo(first)
        self.assert_reviews(self.page(), [], consolidated=False, matches=None)
        self.assertEqual(self.source_fingerprint(), source_before)

    def test_single_project_consolidation_and_project_order_remain_consistent(self):
        decisions = self.agree()
        for project, decision in zip(self.PROJECTS, decisions):
            with self.subTest(project=project):
                raw = self.raw([project])
                page = self.page([project])
                self.assertEqual(set(self.node_map(page)), {self.alpha, self.neighbor})
                legacy_review = self.node_map(page)[self.alpha]["resolution"]
                self.assertEqual(legacy_review["project"], project)
                self.assertEqual(legacy_review["resolution_id"], decision["resolution_id"])
                self.assertEqual(legacy_review["reason"], decision["reason"])
                self.assertEqual(legacy_review["kind"], "explicit_user_decision")
                self.assertEqual({m["id"] for m in legacy_review["members"]}, {self.alpha, self.beta})
                self.assertEqual(sorted(encoded(m) for m in legacy_review["members"]),
                                 sorted(encoded(self.node_map(raw)[m["document_id"]])
                                        for m in decision["members"]))
                contexts = self.node_map(page)[self.alpha]["resolution_contexts"]
                self.assertEqual(len(contexts), 1)
                context = contexts[0]
                for field in ("resolution_id", "project", "reason", "kind"):
                    self.assertEqual(context[field], legacy_review[field])
                self.assertIs(context["consolidated"], True)
                self.assertIs(context["display_matches"], True)
                member_fields = ("id", "label", "source_version", "source_target")
                self.assertEqual(sorted(encoded({key: m[key] for key in member_fields})
                                        for m in context["members"]),
                                 sorted(encoded({key: m[key] for key in member_fields})
                                        for m in legacy_review["members"]))
                self.assert_edges(raw, page, {self.beta: self.alpha})
        normal = self.page()
        reversed_scope = self.page(list(reversed(self.PROJECTS)))
        self.assertEqual(set(self.node_map(normal)), set(self.node_map(reversed_scope)))
        self.assert_reviews(reversed_scope, decisions, consolidated=True, matches=True)
        self.assert_edges(self.raw(), reversed_scope, {self.beta: self.alpha})

    def test_unreviewed_target_with_identical_versions_prevents_union_consolidation(self):
        decisions = self.agree()
        extra = self.register("unreviewed-same-bytes", [self.PROJECTS[1]])
        self.store.ingest(extra, self.records[:2])
        self.assertEqual(self.member(extra, "beta")["version"], self.member(self.target, "beta")["version"])
        raw, page = self.raw(), self.page()
        self.assert_sources(raw, self.PROJECTS)
        self.assertEqual(set(self.node_map(page)), {self.alpha, self.beta, self.neighbor})
        self.assert_reviews(page, decisions, consolidated=False, matches=None)
        self.assert_edges(raw, page)

    def test_shared_id_with_other_current_target_version_keeps_selected_original(self):
        decisions = self.agree()
        extra = self.register("unreviewed-new-version", [self.PROJECTS[1]])
        revised = self.record("beta", "Other target beta", "different-current-context")
        self.store.ingest(extra, [revised])
        raw, page = self.raw(), self.page()
        self.assert_sources(raw, self.PROJECTS)
        self.assertEqual(set(self.node_map(page)), {self.alpha, self.beta, self.neighbor})
        selected = self.node_map(page)[self.beta]
        self.assertEqual((selected["source_target"], selected["label"]), (extra, revised["label"]))
        self.assertNotEqual(selected["source_version"], decisions[0]["members"][1]["version"])
        self.assert_reviews(page, decisions, consolidated=False, matches=False)
        self.assert_edges(raw, page)

    def test_denied_extra_target_cannot_supply_context_or_block_valid_consensus(self):
        decisions = self.agree()
        denied = self.register("denied", [self.PROJECTS[1]], permission="denied")
        marker = "DENIED-SYNTHETIC-UNION-CONTENT"
        self.store.ingest(denied, [self.record("beta", marker, marker)])
        raw, page = self.raw(), self.page()
        self.assert_sources(raw, self.PROJECTS)
        self.assertEqual(set(self.node_map(page)), {self.alpha, self.neighbor})
        self.assert_reviews(page, decisions, consolidated=True, matches=True)
        self.assertNotIn(marker, json.dumps(page))
        self.assertNotIn(denied, json.dumps(page))
        self.assert_edges(raw, page, {self.beta: self.alpha})

    def test_permission_revocation_removes_only_revoked_project_review_context(self):
        # Keep the second project readable through an unrelated permitted source.
        anchor = self.register("two-anchor", [self.PROJECTS[1]])
        self.store.ingest(anchor, [self.record("anchor", "Unrelated anchor", "anchorkey")])
        anchor_id = identity("graph", "synthetic-union-anchor")
        decisions = self.agree()
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=? AND project=?",
                       (self.target, self.PROJECTS[1]))
        raw, page = self.raw(), self.page()
        self.assert_sources(raw, self.PROJECTS)
        self.assertEqual(set(self.node_map(page)), {self.alpha, self.neighbor, anchor_id})
        self.assert_reviews(page, [decisions[0]], consolidated=True, matches=True)
        self.assertNotIn(decisions[1]["resolution_id"], json.dumps(page))
        self.assertNotIn(decisions[1]["reason"], json.dumps(page))
        self.assert_edges(raw, page, {self.beta: self.alpha})
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=? AND project=?",
                       (self.target, self.PROJECTS[1]))
        self.assert_reviews(self.page(), decisions, consolidated=True, matches=True)

    def test_partial_search_and_bounded_focus_do_not_fabricate_missing_members(self):
        decisions = self.agree()
        searched = self.page(search="alphakey")
        raw_searched = self.raw(query="alphakey")
        self.assertEqual(set(self.node_map(searched)), {self.alpha})
        self.assertEqual(len(searched["edges"]), 0)
        # graph_page counts shown/permitted_edges, while FTS limits picked and
        # selected nodes. Search does not replace source-scope totals with 1/0.
        self.assertEqual((raw_searched["total_nodes"], raw_searched["total_edges"]), (3, 3))
        self.assertEqual((searched["total_nodes"], searched["total_edges"]), (3, 3))
        self.assert_edges(raw_searched, searched)
        self.assertEqual(searched.get("resolution_projection", {}).get("groups", 0), 0)
        self.assert_sources(raw_searched, self.PROJECTS)
        for options in ({"limit": 1}, {"focus": self.beta, "hops": 0}):
            with self.subTest(options=options):
                raw = self.raw(**options)
                page = self.page(**options)
                self.assertEqual(set(self.node_map(page)), set(self.node_map(raw)))
                self.assertEqual(len(page["nodes"]), 1)
                self.assertEqual(page.get("resolution_projection", {}).get("groups", 0), 0)
                # Partial pages need not carry complete-group review metadata.
                # Any supplied notes must remain references, never a merge.
                for node in page["nodes"]:
                    for context in node.get("resolution_contexts", []):
                        self.assertIn(context["resolution_id"], {d["resolution_id"] for d in decisions})
                        self.assertFalse(context["consolidated"])
                        self.assertFalse(context["display_matches"])
                self.assert_edges(raw, page)

    def test_conflicting_representatives_preserve_original_nodes_and_both_reviews(self):
        decisions = [self.decide(self.PROJECTS[0], "alpha"), self.decide(self.PROJECTS[1], "beta")]
        raw, page = self.raw(), self.page()
        self.assertEqual(set(self.node_map(page)), {self.alpha, self.beta, self.neighbor})
        self.assert_reviews(page, decisions, consolidated=False, matches=True)
        self.assert_edges(raw, page)
        self.undo(decisions[1])
        restored = self.page()
        self.assertEqual(set(self.node_map(restored)), {self.alpha, self.beta, self.neighbor})
        self.assert_reviews(restored, decisions[:1], consolidated=False, matches=True)

    def test_source_revision_invalidates_review_metadata_and_restores_originals(self):
        decisions = self.agree()
        self.assertEqual(set(self.node_map(self.page())), {self.alpha, self.neighbor})
        revised = self.record("beta", "Revised original beta", "revised-betakey")
        self.store.ingest(self.target, [revised])
        raw, page = self.raw(), self.page()
        self.assertEqual(set(self.node_map(page)), {self.alpha, self.beta, self.neighbor})
        self.assert_sources(raw, self.PROJECTS)
        self.assertEqual(self.node_map(page)[self.beta]["label"], revised["label"])
        self.assert_reviews(page, [], consolidated=False, matches=None)
        for decision in decisions:
            self.assertNotIn(decision["resolution_id"], json.dumps(page))
        self.assert_edges(raw, page)

    def test_all_projects_do_not_apply_subset_decisions_to_third_project_shared_ids(self):
        decisions = self.agree()
        other_project = "union-three"
        other = self.register("unreviewed-third-project", [other_project])
        marker = "THIRD-PROJECT-SYNTHETIC-CONTEXT"
        self.store.ingest(other, [self.record("alpha", marker + " alpha", marker),
                                  self.record("beta", marker + " beta", marker)])
        scoped = self.page()
        self.assertEqual(set(self.node_map(scoped)), {self.alpha, self.neighbor})
        self.assert_reviews(scoped, decisions, consolidated=True, matches=True)
        self.assertNotIn(marker, json.dumps(scoped))
        all_projects = read_action(self.root, "collection-graph", json.dumps({"limit": 100}))
        self.assertTrue(all_projects["ok"])
        self.assertEqual(set(self.node_map(all_projects)), {self.alpha, self.beta, self.neighbor})
        self.assert_sources(self.raw([*self.PROJECTS, other_project]), [*self.PROJECTS, other_project])
        self.assert_reviews(all_projects, decisions, consolidated=False, matches=False)
        self.assert_edges(self.raw([*self.PROJECTS, other_project]), all_projects)


if __name__ == "__main__":
    unittest.main()
