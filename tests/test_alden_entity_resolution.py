"""Isolated CollectionStore v3 resolution contracts and source preservation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alden_collection import CollectionStore, identity
import alden_entity_resolution as resolution


def record(name, text=None):
    return {"platform": "graph", "original_id": name, "label": "Same label" if name in {"a", "b"} else name,
            "text": text if text is not None else "Original text for " + name,
            "source_url": "local://synthetic/" + name}


def edge(source, destination, kind="mentions"):
    return {"source_platform": "graph", "source_id": source,
            "target_platform": "graph", "target_id": destination,
            "type": kind, "evidence": {"source": "synthetic", "direction": source + ">" + destination}}


class EmptyStoreTest(unittest.TestCase):
    def test_preview_and_history_do_not_create_database_or_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory)
            with self.assertRaisesRegex(ValueError, "collection_resolution_store_not_configured"):
                resolution.resolution_action(state_root, "collection-resolution-preview", {
                    "projects": ["one"], "members": [
                        {"target_id": "one", "document_id": "a", "version": "v1"},
                        {"target_id": "one", "document_id": "b", "version": "v2"}],
                    "canonical": {"target_id": "one", "document_id": "a"}, "reason": "human reviewed"})
            self.assertEqual(resolution.resolution_action(state_root, "collection-resolution-history",
                               {"projects": ["one"]})["state"], "not_configured")
            self.assertEqual(list(state_root.iterdir()), [])


class ResolutionTest(unittest.TestCase):
    ORIGINAL_TABLES = ("meta", "targets", "target_projects", "documents", "versions",
                       "memberships", "relations", "relation_versions", "runs", "events",
                       "target_snapshots", "event_counts")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.t1 = self.store.register(platform="graph", original_id="source-one", kind="source",
                                      label="source one", projects=["one"])
        self.t2 = self.store.register(platform="graph", original_id="source-two", kind="source",
                                      label="source two", projects=["one"])
        self.t3 = self.store.register(platform="graph", original_id="other-project", kind="source",
                                      label="other", projects=["two"])
        self.denied = self.store.register(platform="graph", original_id="restricted", kind="source",
                                          label="restricted", projects=["one"], permission="denied")
        self.store.ingest(self.t1, [record("a"), record("c")])
        self.store.ingest(self.t2, [record("b")], relations=[edge("a", "b"), edge("b", "c"), edge("c", "a")])
        # Globally identical document ID, but a different target-local version.
        self.store.ingest(self.t3, [record("a", "OTHER PROJECT PRIVATE BODY"), record("d")])
        self.store.ingest(self.denied, [record("secret")], relations=[edge("secret", "a")])

    def member(self, target, name):
        doc_id = identity("graph", name)
        with CollectionStore.open_existing(self.root).database() as db:
            version = db.execute("SELECT current_version FROM memberships WHERE target_id=? AND document_id=?",
                                 (target, doc_id)).fetchone()[0]
        return {"target_id": target, "document_id": doc_id, "version": version}

    def preview(self, names=("a", "b"), *, project="one", reason="Reviewed matching identity"):
        mapping = {"a": self.t1 if project == "one" else self.t3, "b": self.t2,
                   "c": self.t1, "d": self.t3}
        members = [self.member(mapping[name], name) for name in names]
        return resolution.resolution_action(self.root, "collection-resolution-preview", {
            "projects": [project], "members": list(reversed(members)),
            "canonical": {key: members[0][key] for key in ("target_id", "document_id")},
            "reason": reason})

    def apply(self, plan, request_id="apply-1", *, opt_in=True):
        return resolution.resolution_action(self.root, "collection-resolution-apply",
                                            {"plan": plan, "request_id": request_id},
                                            explicit_opt_in=opt_in)

    def revert(self, applied, request_id="revert-1", *, revision=None):
        return resolution.resolution_action(self.root, "collection-resolution-revert", {
            "projects": [applied["project"]], "resolution_id": applied["resolution_id"],
            "expected_revision": applied["revision"] if revision is None else revision,
            "request_id": request_id}, explicit_opt_in=True)

    def history(self, project="one", **options):
        return resolution.resolution_action(self.root, "collection-resolution-history",
                                            {"projects": [project], **options})

    def source_snapshot(self):
        with CollectionStore.open_existing(self.root).database() as db:
            tables = {table: sorted(json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
                                    for row in db.execute("SELECT * FROM " + table))
                      for table in self.ORIGINAL_TABLES}
        blobs = {path.name: path.read_bytes() for path in self.store.blobs.iterdir() if path.is_file()}
        return tables, blobs

    def feature_tables(self):
        with CollectionStore.open_existing(self.root).database() as db:
            return {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'resolution_%'")}

    def scoped_active(self, projects):
        store = CollectionStore.open_existing(self.root)
        with store.database() as db:
            return resolution.active_resolutions(store, db, projects)

    def test_preview_is_deterministic_read_only_and_preserves_directed_relations(self):
        before = self.source_snapshot()
        self.assertEqual(self.feature_tables(), set())
        first, second = self.preview(), self.preview()
        self.assertEqual(first, second)
        self.assertEqual(self.source_snapshot(), before)
        self.assertEqual(self.feature_tables(), set())
        self.assertEqual(len(first["records"]), 2)
        self.assertTrue(all("body" in rec and "raw_sha256" in rec for rec in first["records"]))
        self.assertEqual({rec["label"] for rec in first["records"]}, {"Same label"})
        self.assertTrue(all("OTHER PROJECT PRIVATE BODY" not in rec["body"] for rec in first["records"]))
        self.assertEqual({(r["source"], r["target"]) for r in first["relations"]}, {
            (identity("graph", "a"), identity("graph", "b")),
            (identity("graph", "b"), identity("graph", "c")),
            (identity("graph", "c"), identity("graph", "a"))})
        self.assertFalse(any("secret" in str(rel) for rel in first["relations"]))
        plan = first["plan"]
        self.assertEqual(set(plan), resolution._PLAN_KEYS)
        self.assertEqual(plan["revision"], 0)
        self.assertNotIn("body", json.dumps(plan))
        self.assertNotIn("Same label", json.dumps(plan))
        self.assertLessEqual(len(resolution.encoded(plan)), 32768)

    def test_opt_in_apply_backup_history_revert_replay_and_source_immutability(self):
        before = self.source_snapshot()
        plan = self.preview()["plan"]
        with self.assertRaisesRegex(ValueError, "explicit_opt_in_required"):
            self.apply(plan, opt_in=False)
        self.assertEqual(self.feature_tables(), set())
        applied = self.apply(plan)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["state"], "active")
        self.assertEqual(applied["revision"], 1)
        self.assertTrue(self.feature_tables())
        backups = list(self.store.root.glob("collection.resolution-before-v1-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(os.stat(backups[0]).st_mode & 0o777, 0o600)
        with sqlite3.connect(backups[0]) as recovery:
            self.assertEqual(recovery.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertIsNone(recovery.execute(
                "SELECT 1 FROM sqlite_master WHERE name='resolution_feature_meta'").fetchone())
            self.assertEqual(recovery.execute("SELECT count(*) FROM relations").fetchone()[0], 4)
        self.assertEqual(self.source_snapshot(), before)
        active = self.scoped_active(["one"])
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["resolution_id"], applied["resolution_id"])
        self.assertEqual(self.scoped_active(["one", "two"]), [])
        self.assertEqual(self.scoped_active(["two"]), [])
        current = self.history(request_id="apply-1")
        self.assertEqual(current["request_result"], applied)
        self.assertEqual(current["revision"], 1)
        self.assertEqual(current["items"][0]["state"], "active")
        self.assertEqual(current["items"][0]["current_validity"], {"valid": True, "state": "current"})
        self.assertEqual(self.apply(plan), applied)
        self.assertEqual(self.history()["revision"], 1)
        reverted = self.revert(applied)
        self.assertEqual(reverted["state"], "reverted")
        self.assertEqual(reverted["revision"], 2)
        self.assertEqual(self.scoped_active(["one"]), [])
        self.assertEqual(self.history(request_id="revert-1")["request_result"], reverted)
        self.assertEqual(self.history()["items"][0]["state"], "reverted")
        self.assertEqual(self.apply(plan), applied)  # historical replay never reactivates
        self.assertEqual(self.revert(applied), reverted)
        self.assertEqual(self.history()["revision"], 2)
        self.assertEqual(self.source_snapshot(), before)
        with self.assertRaisesRegex(ValueError, "revision_changed"):
            self.apply(plan, request_id="new-apply-after-revert")

    def test_request_conflict_overlap_and_revision_protection(self):
        first = self.preview()["plan"]
        applied = self.apply(first)
        altered = {**first, "reason": "changed request payload"}
        altered["plan_sha256"] = resolution.digest(resolution.encoded({
            key: val for key, val in altered.items() if key != "plan_sha256"}))
        with self.assertRaisesRegex(ValueError, "request_id_conflict"):
            self.apply(altered, request_id="apply-1")
        with self.assertRaisesRegex(ValueError, "overlapping_active_group"):
            self.preview(("b", "c"))
        with self.assertRaisesRegex(ValueError, "revision_changed"):
            self.revert(applied, request_id="bad-revision", revision=0)
        self.assertEqual(self.history()["revision"], 1)
        self.revert(applied)
        with self.assertRaisesRegex(ValueError, "already_reverted"):
            self.revert(applied, request_id="different-revert", revision=2)
        self.assertEqual(self.history()["revision"], 2)

    def test_stale_versions_and_relations_rejected_before_feature_creation(self):
        plan = self.preview()["plan"]
        self.store.ingest(self.t2, [record("b", "new source version")])
        with self.assertRaisesRegex(ValueError, "member_version_changed"):
            self.apply(plan)
        self.assertEqual(self.feature_tables(), set())
        self.assertEqual(list(self.store.root.glob("collection.resolution-before-v1-*.sqlite3")), [])
        plan = self.preview()["plan"]
        self.store.ingest(self.t1, [], relations=[edge("a", "c", "corrects")])
        with self.assertRaisesRegex(ValueError, "source_fingerprint_changed"):
            self.apply(plan)
        self.assertEqual(self.feature_tables(), set())

    def test_history_distinguishes_later_revision_and_relation_drift(self):
        applied = self.apply(self.preview()["plan"])
        self.store.ingest(self.t2, [record("b", "changed after approval")])
        status = self.history()["items"][0]
        self.assertEqual(status["state"], "active")
        self.assertEqual(status["current_validity"]["state"], "collection_resolution_member_version_changed")
        self.assertEqual(self.scoped_active(["one"]), [])
        self.revert(applied)
        self.assertEqual(self.history()["items"][0]["state"], "reverted")
        second = self.apply(self.preview()["plan"], "apply-2")
        self.assertEqual(second["revision"], 3)
        self.store.ingest(self.t1, [], relations=[edge("a", "c", "corrects")])
        state = self.history()["items"][0]
        self.assertEqual(state["current_validity"]["state"], "collection_resolution_relations_changed")

    def test_permission_revocation_redacts_history_and_reconciled_request(self):
        plan = self.preview()["plan"]
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=? AND project='one'",
                       (self.t2,))
        with self.assertRaisesRegex(ValueError, "member_permission_revoked"):
            self.apply(plan)
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=? AND project='one'",
                       (self.t2,))
        applied = self.apply(plan)
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=? AND project='one'",
                       (self.t2,))
        result = self.history(request_id="apply-1")
        self.assertIsNone(result["request_result"])
        self.assertEqual(result["items"][0]["state"], "active")
        self.assertFalse(result["items"][0]["current_validity"]["valid"])
        self.assertTrue(result["items"][0]["redacted"])
        self.assertEqual(result["items"][0]["members"], [])
        self.assertEqual(self.scoped_active(["one"]), [])
        with self.assertRaisesRegex(ValueError, "request_scope_denied"):
            self.apply(plan)
        # Reverting an invalid overlay is safe even if one source was revoked.
        self.assertEqual(self.revert(applied)["state"], "reverted")
        self.assertEqual(self.history()["revision"], 2)

    def test_project_isolation_and_no_cross_project_request_leak(self):
        one = self.apply(self.preview()["plan"])
        two = self.apply(self.preview(("a", "d"), project="two")["plan"], "apply-two")
        self.assertEqual(self.history("one")["revision"], 1)
        self.assertEqual(self.history("two")["revision"], 1)
        self.assertEqual(self.history("two", request_id="apply-1")["request_result"], None)
        self.assertEqual(self.history("one", request_id="apply-two")["request_result"], None)
        self.assertEqual([r["resolution_id"] for r in self.scoped_active(["one"])], [one["resolution_id"]])
        self.assertEqual([r["resolution_id"] for r in self.scoped_active(["two"])], [two["resolution_id"]])
        self.assertEqual(self.scoped_active(["one", "two"]), [])
        self.assertEqual(self.history("two")["items"][0]["members"][0]["target_id"], self.t3)

    def test_invalid_plan_members_budget_and_bounded_history(self):
        plan = self.preview()["plan"]
        with self.assertRaisesRegex(ValueError, "plan_hash_invalid"):
            self.apply({**plan, "reason": "undisclosed mutation"})
        with self.assertRaisesRegex(ValueError, "plan_too_large"):
            self.apply({**plan, "extra": "X" * 32768})
        with self.assertRaisesRegex(ValueError, "history_limit_invalid"):
            self.history(limit=101)
        with self.assertRaisesRegex(ValueError, "project_scope_invalid"):
            resolution.resolution_action(self.root, "collection-resolution-history", {"projects": ["one", "two"]})
        with self.assertRaisesRegex(ValueError, "distinct_documents_required"):
            resolution.resolution_action(self.root, "collection-resolution-preview", {
                "projects": ["one"], "members": [self.member(self.t1, "a"), self.member(self.t1, "a")],
                "canonical": {"target_id": self.t1, "document_id": identity("graph", "a")},
                "reason": "No duplicate membership"})
        with self.assertRaisesRegex(ValueError, "canonical_not_member"):
            resolution.resolution_action(self.root, "collection-resolution-preview", {
                "projects": ["one"], "members": [self.member(self.t1, "a"), self.member(self.t2, "b")],
                "canonical": {"target_id": self.t1, "document_id": identity("graph", "c")},
                "reason": "Other member"})

    def test_schema_bootstrap_is_rolled_back_if_decision_fails(self):
        original = self.source_snapshot()
        plan = self.preview()["plan"]
        original_creator = resolution._new_feature_tables

        def interrupt_after_creation(db):
            original_creator(db)
            raise RuntimeError("synthetic_abort_after_schema")

        with patch.object(resolution, "_new_feature_tables", side_effect=interrupt_after_creation):
            with self.assertRaisesRegex(RuntimeError, "synthetic_abort_after_schema"):
                self.apply(plan)
        self.assertEqual(self.feature_tables(), set())
        self.assertEqual(self.source_snapshot(), original)
        self.assertEqual(self.history()["revision"], 0)
        self.assertEqual(self.apply(plan)["revision"], 1)

    def test_competing_requests_have_one_winner(self):
        plan = self.preview()["plan"]

        def attempt(request_id):
            try:
                return self.apply(plan, request_id)["resolution_id"]
            except ValueError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(attempt, ["concurrent-1", "concurrent-2"]))
        self.assertEqual(sum(item.startswith("resolution:") for item in results), 1)
        self.assertIn("collection_resolution_revision_changed", results)
        self.assertEqual(self.history()["revision"], 1)
        self.assertEqual(len(self.scoped_active(["one"])), 1)


if __name__ == "__main__":
    unittest.main()
