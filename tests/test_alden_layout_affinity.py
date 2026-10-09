"""Product boundary, same-view bindings and caller-owned derived read scopes."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_collection import CollectionStore, digest, encoded, identity
import alden_semantic_affinity as semantic
import alden_layout_affinity as layout
from tests import test_alden_semantic_affinity as fixtures


class LayoutAffinityTests(unittest.TestCase):
    setUp = fixtures.SemanticAffinityTests.setUp
    make = fixtures.SemanticAffinityTests.make
    ref = fixtures.SemanticAffinityTests.ref
    install = fixtures.SemanticAffinityTests.install

    def request(self, query=None, **kwargs):
        return layout.read_action(self.root, json.dumps({"projects": ["one"], **(query or {})}), **kwargs)

    def current_refs(self, options):
        page = CollectionStore.open_existing(self.root).graph_page(projects=options.get("projects", ["one"]),
            limit=options.get("limit", 120), offset=options.get("offset", 0), focus=options.get("focus"),
            hops=options.get("hops", 1), query=options.get("search", ""), target_id=options.get("target_id"),
            platform=options.get("platform"), node_type=options.get("node_type"), relation=options.get("relation"),
            since=options.get("since"), until=options.get("until"), overview=options.get("overview", False))
        return [{k: n[k] for k in ("id", "source_version", "source_target")} for n in page["nodes"]], page.get("revision", 0)

    def test_endpoint_is_bound_to_server_graph_refs_and_compact_proofs(self):
        self.make("a")
        self.make("b", vector=(.8, .6))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch("subprocess.Popen", side_effect=AssertionError("external execution forbidden")):
            result = self.request()
        self.assertTrue(result["ok"])
        affinity = result["layout_affinity"]
        self.assertEqual(affinity["schema"], "alden-layout-affinity-v1")
        self.assertEqual(affinity["state"], "bounded_ready")
        refs, revision = self.current_refs({})
        self.assertEqual(affinity["input_nodes"], refs)
        self.assertEqual(result["revision"], revision)
        self.assertEqual(affinity["binding"]["graph_revision"], revision)
        self.assertEqual(affinity["input_projects"], ["one"])
        binding = {"input_nodes": refs, "input_projects": ["one"], "profile_sha256": affinity["profile_sha256"]}
        self.assertEqual(affinity["binding"]["input_sha256"], digest(encoded(binding)))
        self.assertEqual(len(affinity["candidates"]), 1)
        self.assertAlmostEqual(affinity["candidates"][0]["cosine"], .8, places=6)
        self.assertEqual(set(affinity["candidates"][0]), {"source", "target", "cosine", "projects"})
        self.assertEqual(affinity["binding"]["admitted_provenance_sha256"], digest(encoded(affinity["nodes"])))
        for proof in affinity["nodes"]:
            self.assertEqual(proof["raw_path"], proof["raw_sha256"] + ".json")
            self.assertEqual(proof["dimension"], 384)
            self.assertEqual(proof["profile_sha256"], affinity["profile_sha256"])
            self.assertAlmostEqual(proof["vector_norm"], 1, places=6)
            self.assertEqual(proof["permissions"], [{"project": "one", "permission": "local-private"}])
        payload = encoded(result).decode()
        for key in ('"body":', '"original_id":', '"description":', '"edges":', '"label":'):
            self.assertNotIn(key, payload)
        self.assertLess(len(encoded(result)), layout.MAX_RESPONSE_BYTES)

    def test_existing_graph_filters_paging_search_focus_time_type_relation_are_preserved(self):
        records = [{"original_id": "a", "label": "alpha", "text": "body", "kind": "person"},
                   {"original_id": "b", "label": "beta", "text": "body", "kind": "company"},
                   {"original_id": "c", "label": "gamma", "text": "body", "kind": "person"}]
        relation = {"source_platform": "graph", "source_id": "a", "target_platform": "graph", "target_id": "b",
                    "type": "reference", "evidence": {"quote": "explicit source"}}
        self.store.ingest(self.a, records, relations=[relation])
        for i, name in enumerate(("a", "b", "c")):
            node = self.ref(name, self.a)
            self.install(node, (1., 0.))
            with self.store.database() as db:
                db.execute("UPDATE versions SET collected_at=? WHERE id=?", (10 + i * 10, node["source_version"]))
        self.make("other", target=self.b)
        cases = [{"overview": True, "limit": 2}, {"limit": 1, "offset": 1}, {"search": "beta"},
                 {"target_id": self.a}, {"platform": "graph"}, {"node_type": "person"},
                 {"relation": "reference"}, {"since": 10, "until": 25},
                 {"focus": identity("graph", "a"), "hops": 0},
                 {"focus": identity("graph", "a"), "hops": 1}]
        for query in cases:
            with self.subTest(query=query):
                refs, revision = self.current_refs(query)
                result = self.request(query)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["layout_affinity"]["input_nodes"], refs)
                self.assertEqual(result["revision"], revision)
                allowed = {n["id"] for n in refs}
                for candidate in result["layout_affinity"]["candidates"]:
                    self.assertIn(candidate["source"], allowed)
                    self.assertIn(candidate["target"], allowed)

    def test_only_two_derived_connections_and_nested_graph_queries_reuse_snapshot(self):
        self.make("a")
        original = sqlite3.connect
        opened = []
        def connect(*args, **kwargs):
            opened.append((args[0], kwargs))
            return original(*args, **kwargs)
        with patch("sqlite3.connect", connect), patch.object(CollectionStore, "__init__", side_effect=AssertionError("store initialization")), patch.object(CollectionStore, "open_existing", side_effect=AssertionError("extra source connection")):
            result = self.request()
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(opened), 2)
        for uri, options in opened:
            self.assertTrue(options["uri"])
            self.assertTrue(uri.endswith("?mode=ro"))
            self.assertNotIn("immutable", uri)
            self.assertIn(str(self.store.root), uri)
        self.assertEqual(result["layout_affinity"]["sqlite_read_metadata"]["denied_write_attempts"], 0)

    def test_caller_owned_reads_remain_active_and_immutable_under_nested_use(self):
        self.make("a")
        with layout.read_transactions(self.root) as owner:
            self.assertTrue(owner.collection.in_transaction)
            self.assertTrue(owner.cache.in_transaction)
            result = self.request(transactions=owner)
            self.assertTrue(result["ok"], result)
            self.assertTrue(owner.collection.in_transaction)
            self.assertTrue(owner.cache.in_transaction)
            for sql in ("UPDATE documents SET availability='deleted'", "PRAGMA journal_mode=DELETE",
                        "PRAGMA wal_checkpoint", "PRAGMA query_only=OFF", "ATTACH ':memory:' AS extra"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.Error):
                    owner.collection.execute(sql)
            self.assertEqual(owner.metadata["denied_write_attempts"], 5)
        self.assertFalse(owner.active)
        self.assertIsNotNone(owner.metadata["after"])
        self.assertFalse(self.request(transactions=owner)["ok"])

    def test_wrong_owner_root_and_raw_connections_are_rejected(self):
        self.make("a")
        with layout.read_transactions(self.root) as owner:
            wrong = layout.read_action(self.root / "other", '{}', transactions=owner)
            self.assertFalse(wrong["ok"])
            self.assertEqual(wrong["error"], "layout_verified_read_owner_required")
        with sqlite3.connect(self.cache) as db:
            self.assertFalse(self.request(transactions=db)["ok"])

    def test_product_closed_wal_metadata_policy_does_not_relax_developer_cli(self):
        node = self.make("a")
        db = sqlite3.connect(self.cache)
        db.execute("PRAGMA journal_mode=WAL")
        db.close()
        main_hash = digest(self.cache.read_bytes())
        with self.assertRaisesRegex(ValueError, "affinity_wal_sidecars_required"):
            semantic.affinity(self.root, ["one"], [node])
        result = self.request()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["layout_affinity"]["state"], "bounded_ready")
        metadata = result["layout_affinity"]["sqlite_read_metadata"]
        self.assertFalse(metadata["before"]["retrieval.sqlite3"]["-wal"]["exists"])
        self.assertTrue(metadata["during_read"]["retrieval.sqlite3"]["-wal"]["exists"])
        self.assertFalse(metadata["shm_byte_identity_measured"])
        self.assertEqual(main_hash, digest(self.cache.read_bytes()))

    def test_query_bytes_shapes_and_client_nodes_are_rejected_before_open(self):
        cases = ['x' * 4097, json.dumps({"search": "한" * 1400}, ensure_ascii=False), "[]", '{"nodes":[]}',
                 '{"input_nodes":[]}', '{"details":true}', '{"activity":true}', '{"profile":{}}', '{"search":NaN}']
        for raw in cases:
            with self.subTest(raw=raw[:60]), patch("sqlite3.connect", side_effect=AssertionError("invalid request must not open SQLite")):
                self.assertFalse(layout.read_action(self.root, raw)["ok"])
        self.assertFalse(layout.read_action(self.root, {})["ok"])

    def test_project_permission_and_target_isolation_match_current_graph(self):
        self.make("a")
        private = self.make("private", target=self.b)
        result = self.request({"target_id": self.b})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["layout_affinity"]["input_nodes"], [])
        self.assertNotIn(private["id"], encoded(result).decode())
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.b,))
        denied = self.request({"projects": ["one", "two"]})
        self.assertFalse(denied["ok"])
        self.assertNotIn(private["id"], encoded(denied).decode())

    def test_union_projects_do_not_gain_cross_project_semantic_links(self):
        self.make("one")
        self.make("two", target=self.b)
        result = self.request({"projects": ["one", "two"]})
        self.assertTrue(result["ok"])
        affinity = result["layout_affinity"]
        self.assertEqual(affinity["coverage"]["usable_vector_nodes"], 2)
        self.assertEqual(affinity["coverage"]["cross_project_pairs_excluded"], 1)
        self.assertEqual(affinity["candidates"], [])

    def test_deleted_membership_and_new_current_without_vector_never_use_archived_vector(self):
        old = self.make("shared", text="old")
        self.store.ingest(self.a, [{"original_id": "shared", "label": "topic", "text": "new"}])
        deleted = self.make("deleted")
        with self.store.database() as db:
            db.execute("UPDATE memberships SET availability='deleted' WHERE document_id=?", (deleted["id"],))
        result = self.request()
        affinity = result["layout_affinity"]
        self.assertEqual(affinity["state"], "unavailable")
        self.assertNotIn(old, affinity["input_nodes"])
        self.assertNotIn(deleted["id"], encoded(result).decode())
        self.assertEqual(affinity["coverage"]["reasons"], {"missing_current_vector": 1})

    def test_expected_focus_version_mismatch_is_graph_failure(self):
        node = self.make("a")
        result = self.request({"focus": node["id"], "hops": 0, "expected_version": "version:stale"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "collection_graph_version_changed")

    def test_missing_cache_and_tables_are_unavailable_with_actual_input_binding(self):
        self.make("a")
        self.cache.unlink()
        result = self.request()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["layout_affinity"]["state"], "unavailable")
        self.assertEqual(len(result["layout_affinity"]["input_nodes"]), 1)
        self.assertEqual(result["layout_affinity"]["reason"], "layout_existing_retrieval_required")
        self.assertFalse(self.cache.exists())
        with sqlite3.connect(self.cache) as db:
            db.execute("CREATE TABLE unrelated(x)")
        result = self.request()
        self.assertTrue(result["ok"])
        self.assertEqual(result["layout_affinity"]["reason"], "affinity_existing_vector_text_tables_required")

    def test_stale_cache_provenance_profile_and_malformed_values_cannot_enter_candidates(self):
        good = self.make("good")
        bad = self.make("bad")
        cases = [("vectors", "text_hash", "0" * 64, "stale_vector_text_hash"),
                 ("vectors", "model", "other-profile", "different_profile"),
                 ("vectors", "vector", b"bad", "malformed_vector"),
                 ("texts", "raw_sha256", "0" * 64, "stale_text_binding")]
        for table, field, value, reason in cases:
            with self.subTest(reason=reason):
                self.install(bad, (1., 0.))
                with sqlite3.connect(self.cache) as db:
                    db.execute("UPDATE " + table + " SET " + field + "=? WHERE document_id=?", (value, bad["id"]))
                result = self.request()
                affinity = result["layout_affinity"]
                self.assertEqual(affinity["state"], "bounded_partial")
                self.assertEqual(affinity["coverage"]["reasons"], {reason: 1})
                self.assertEqual(affinity["nodes"][0]["id"], good["id"])
                self.assertEqual(affinity["candidates"], [])

    def test_retained_source_provenance_is_bound_without_original_source_access(self):
        node = self.make("retained", text="", raw={"localOriginalText": "actual retained text"})
        with sqlite3.connect(self.cache) as db:
            db.execute("UPDATE texts SET body='forged' WHERE document_id=?", (node["id"],))
            db.execute("UPDATE vectors SET text_hash=? WHERE document_id=?", (digest(b"topic\nforged"), node["id"]))
        result = self.request()
        self.assertEqual(result["layout_affinity"]["coverage"]["reasons"], {"retained_text_source_mismatch": 1})

    def test_pair_and_candidate_caps_stay_partial_with_actual_selected_coverage(self):
        for name in ("a", "b", "c", "d"):
            self.make(name)
        limited = self.request(budgets=replace(semantic.Budgets(), max_pairs=2))
        self.assertEqual(limited["layout_affinity"]["coverage"]["pairs_evaluated"], 2)
        self.assertTrue(limited["layout_affinity"]["coverage"]["pair_budget_exhausted"])
        self.assertEqual(limited["layout_affinity"]["state"], "bounded_partial")
        capped = self.request(budgets=replace(semantic.Budgets(), max_candidates=1))
        self.assertEqual(capped["layout_affinity"]["coverage"]["candidates_returned"], 1)
        self.assertEqual(capped["layout_affinity"]["coverage"]["candidates_truncated"], 5)

    def test_hard_budget_and_mid_compute_cancel_publish_no_partial_candidates(self):
        self.make("a")
        self.make("b")
        result = self.request(budgets=replace(semantic.Budgets(), max_vector_bytes=1535))
        self.assertEqual(result["layout_affinity"]["state"], "unavailable")
        self.assertEqual(result["layout_affinity"]["reason"], "affinity_vector_bytes_budget")
        for error in (semantic.BudgetExceeded("affinity_cpu_budget"), semantic.BudgetExceeded("affinity_wall_budget"),
                      semantic.BudgetExceeded("affinity_rss_budget"), RuntimeError("affinity_cancelled")):
            with self.subTest(error=str(error)), patch.object(semantic, "_candidates", side_effect=error):
                result = self.request()
                self.assertTrue(result["ok"])
                self.assertEqual(result["layout_affinity"]["state"], "unavailable")
                self.assertEqual(result["layout_affinity"]["reason"], str(error))
                self.assertEqual(result["layout_affinity"]["nodes"], [])
                self.assertEqual(result["layout_affinity"]["candidates"], [])

    def test_pre_cancel_and_reused_owner_cancel_do_not_open_or_mutate(self):
        with patch("sqlite3.connect", side_effect=AssertionError("pre-cancel must not open")):
            result = self.request(cancelled=lambda: True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "affinity_cancelled")
        self.make("a")
        with layout.read_transactions(self.root) as owner:
            original = owner.guard.cancelled
            result = self.request(transactions=owner, cancelled=lambda: True)
            self.assertFalse(result["ok"])
            self.assertIs(owner.guard.cancelled, original)
            self.assertTrue(owner.collection.in_transaction)

    def test_response_limit_and_collection_page_limit_do_not_expand_menu_budget(self):
        self.make("a")
        with patch.object(layout, "MAX_RESPONSE_BYTES", 100):
            result = self.request()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "layout_response_byte_budget")
            self.assertLess(len(encoded(result)), 100)
        self.assertFalse(self.request({"overview": True, "limit": 1985})["ok"])
        self.assertFalse(self.request({"limit": 121})["ok"])
        self.assertTrue(self.request({"overview": True, "limit": 1984})["ok"])

    def test_empty_projects_or_no_matching_nodes_is_explicit_empty_view(self):
        self.make("a")
        for query in ({"projects": []}, {"search": "absent"}):
            result = self.request(query)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["layout_affinity"]["state"], "bounded_ready")
            self.assertEqual(result["layout_affinity"]["input_nodes"], [])
            self.assertIsNone(result["layout_affinity"]["coverage"]["node_coverage"])

    def test_borrowed_collection_cannot_publish_and_memory_overlay_is_rejected(self):
        self.make("a")
        with layout.read_transactions(self.root) as owner:
            with self.assertRaisesRegex(ValueError, "layout_read_publication_guard_forbidden"):
                with owner.store.database(publication_guard=lambda: None):
                    pass
        page = {"ok": True, "nodes": [{"id": "memory:overlay", "source_version": "version:x", "source_target": self.a}]}
        with patch.object(layout._BorrowedCollection, "graph_page", return_value=page):
            result = self.request()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "layout_non_collection_node")

    def test_graph_selection_and_affinity_share_permission_and_version_snapshot(self):
        old = self.make("shared", text="before")
        self.make("neighbor")
        with layout.read_transactions(self.root) as owner:
            # The owner pinned its collection snapshot before this independent
            # fixture writer revised the membership and revoked permission.
            self.store.ingest(self.a, [{"original_id": "shared", "label": "topic", "text": "after"}])
            with self.store.database() as db:
                db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.a,))
            result = self.request(transactions=owner)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["layout_affinity"]["state"], "bounded_ready")
            self.assertIn(old, result["layout_affinity"]["input_nodes"])
            proof = next(n for n in result["layout_affinity"]["nodes"] if n["id"] == old["id"])
            self.assertEqual(proof["source_version"], old["source_version"])
        # Fresh requests see the revocation; prior-snapshot evidence is not a
        # claim that permission stayed current after the response was produced.
        current = self.request()
        self.assertFalse(current["ok"])
        self.assertEqual(current["error"], "collection_project_scope_invalid")

    def test_live_wal_cache_snapshot_and_new_request_staleness_are_distinct(self):
        node = self.make("wal")
        writer = sqlite3.connect(self.cache)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        with layout.read_transactions(self.root) as owner:
            writer.execute("UPDATE vectors SET text_hash=? WHERE document_id=?", ("0" * 64, node["id"]))
            writer.commit()
            old_snapshot = self.request(transactions=owner)
            self.assertEqual(old_snapshot["layout_affinity"]["state"], "bounded_ready")
        fresh = self.request()
        self.assertTrue(fresh["ok"])
        self.assertEqual(fresh["layout_affinity"]["state"], "unavailable")
        self.assertEqual(fresh["layout_affinity"]["coverage"]["reasons"], {"stale_vector_text_hash": 1})


if __name__ == "__main__":
    unittest.main()
