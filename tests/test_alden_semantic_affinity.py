"""Isolation, source bindings and hard resource guards for read-only affinity."""
from dataclasses import replace
import io
import json
from pathlib import Path
import sqlite3
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_collection import CollectionStore, digest, identity
import alden_semantic_affinity as sa


class SemanticAffinityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="alden-affinity-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.a = self.store.register(platform="graph", original_id="a", kind="source", label="a", projects=["one"])
        self.b = self.store.register(platform="graph", original_id="b", kind="source", label="b", projects=["two"])
        # Keep this synthetic WAL open, so production preflight does not have
        # to initialize sidecars. This connection belongs only to the fixture.
        self.holder = sqlite3.connect(self.store.path)
        self.holder.execute("SELECT * FROM meta").fetchall()
        self.addCleanup(self.holder.close)
        self.cache = self.store.root / "retrieval.sqlite3"
        with sqlite3.connect(self.cache) as db:
            db.execute("""CREATE TABLE vectors(document_id TEXT,version TEXT,text_hash TEXT,
                model TEXT,endpoint TEXT,encoding TEXT,vector BLOB,PRIMARY KEY(document_id,version))""")
            db.execute("""CREATE TABLE texts(document_id TEXT,version TEXT,base_hash TEXT,
                raw_sha256 TEXT,extraction TEXT,label TEXT,body TEXT,body_source TEXT,
                PRIMARY KEY(document_id,version))""")

    def make(self, name, *, target=None, text="body", raw=None, vector=(1., 0.)):
        target = self.a if target is None else target
        record = {"platform": "graph", "original_id": name, "label": "topic", "text": text}
        if raw is not None:
            record["raw"] = raw
        self.store.ingest(target, [record])
        node = self.ref(name, target)
        self.install(node, vector)
        return node

    def ref(self, name, target):
        doc = identity("graph", name)
        with self.store.database() as db:
            version = db.execute("SELECT current_version FROM memberships WHERE document_id=? AND target_id=?", (doc, target)).fetchone()[0]
        return {"id": doc, "source_version": version, "source_target": target}

    def install(self, node, vector):
        with self.store.database() as db:
            row = db.execute("SELECT * FROM versions WHERE id=?", (node["source_version"],)).fetchone()
        source = json.loads((self.store.blobs / row["raw_path"]).read_bytes())
        body, field = row["body"], "stored_body"
        if isinstance(source, dict) and isinstance(source.get("localOriginalText"), str) and source["localOriginalText"].strip():
            body, field = source["localOriginalText"], "retained_record.localOriginalText"
        values = list(vector) + [0.] * (384 - len(vector))
        raw = struct.pack("<384f", *values)
        with sqlite3.connect(self.cache) as db:
            db.execute("INSERT OR REPLACE INTO texts VALUES(?,?,?,?,?,?,?,?)", (node["id"], node["source_version"],
                digest((row["label"] + "\n" + row["body"]).encode()), row["raw_sha256"], sa.TEXT_VERSION, row["label"], body, field))
            db.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?)", (node["id"], node["source_version"],
                digest((row["label"] + "\n" + body).encode()), sa.MODEL, sa.ENDPOINT, sa.ENCODING, raw))

    def run_affinity(self, nodes, **options):
        return sa.affinity(self.root, ["one"], nodes, **options)

    def reason(self, report, kind, count=1):
        self.assertEqual(report["coverage"]["reasons"].get(kind), count)
        self.assertEqual(report["state"], "bounded_partial")

    def test_read_only_cosine_and_provenance_without_network_or_store_initialization(self):
        a = self.make("a", vector=(1., 0.))
        b = self.make("b", vector=(.8, .6))
        files = [self.store.path, self.cache, *self.store.blobs.glob("*.json")]
        before = {str(p): digest(p.read_bytes()) for p in files}
        with patch.object(CollectionStore, "__init__", side_effect=AssertionError("store write")), patch("socket.socket", side_effect=AssertionError("network")):
            report = self.run_affinity([a, b])
        self.assertEqual(report["state"], "bounded_ready")
        self.assertAlmostEqual(report["candidates"][0]["cosine"], .8, places=6)
        self.assertEqual(report["candidates"][0]["kind"], "semantic_layout_affinity")
        self.assertFalse(report["identity_merge"])
        self.assertFalse(report["factual_relationship"])
        self.assertEqual(report["coverage"]["pairs_evaluated"], 1)
        self.assertEqual(report["coverage"]["node_coverage"], 1)
        for proof in report["nodes"]:
            self.assertEqual(proof["dimension"], 384)
            self.assertEqual(proof["profile_sha256"], report["profile_sha256"])
            self.assertEqual(proof["raw_path"], proof["raw_sha256"] + ".json")
            self.assertEqual(proof["permissions"], [{"project": "one", "permission": "local-private"}])
            self.assertEqual(len(proof["vector_sha256"]), 64)
        self.assertEqual(before, {str(p): digest(p.read_bytes()) for p in files})
        self.assertNotIn('"body":', json.dumps(report))

    def test_union_scope_never_creates_cross_project_candidates(self):
        a = self.make("a")
        b = self.make("b", target=self.b)
        report = sa.affinity(self.root, ["one", "two"], [a, b])
        self.assertEqual(report["coverage"]["usable_vector_nodes"], 2)
        self.assertEqual(report["coverage"]["cross_project_pairs_excluded"], 1)
        self.assertEqual(report["coverage"]["pairs_evaluated"], 0)
        self.assertEqual(report["candidates"], [])
        self.assertIsNone(report["coverage"]["pair_coverage"])

    def test_denied_deleted_missing_and_outside_scope_have_no_provenance_leak(self):
        a = self.make("allowed")
        outside = self.make("outside", target=self.b)
        denied_target = self.store.register(platform="graph", original_id="denied", kind="source", label="denied", projects=["one"])
        denied = self.make("denied", target=denied_target)
        deleted = self.make("deleted")
        deleted_doc = self.make("deleted-document")
        missing = {"id": "graph:missing", "source_target": self.a, "source_version": "version:missing"}
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (denied_target,))
            db.execute("UPDATE memberships SET availability='deleted' WHERE document_id=?", (deleted["id"],))
            db.execute("UPDATE documents SET availability='deleted' WHERE id=?", (deleted_doc["id"],))
        report = self.run_affinity([a, outside, denied, deleted, deleted_doc, missing])
        self.reason(report, "not_current_or_permitted", 5)
        self.assertEqual(report["coverage"]["usable_vector_nodes"], 1)
        for node in (outside, denied, deleted, deleted_doc, missing):
            self.assertNotIn(node["id"], json.dumps(report))

    def test_current_target_version_survives_newer_other_project_global_version(self):
        a = self.make("shared", text="authorized original")
        b = self.make("neighbor", vector=(.8, .6))
        other = self.make("shared", target=self.b, text="new private revision", vector=(0., 1.))
        report = self.run_affinity([a, b])
        proof = next(n for n in report["nodes"] if n["id"] == a["id"])
        self.assertEqual(proof["source_version"], a["source_version"])
        self.assertNotEqual(proof["source_version"], other["source_version"])
        self.assertEqual(proof["projects"], ["one"])
        wrong_target = self.run_affinity([other])
        self.reason(wrong_target, "not_current_or_permitted")

    def test_stale_graph_and_missing_new_vector_never_reuse_archived_vector(self):
        old = self.make("changed", text="old")
        self.store.ingest(self.a, [{"original_id": "changed", "label": "topic", "text": "new"}])
        current = self.ref("changed", self.a)
        self.reason(self.run_affinity([old]), "stale_graph_version")
        self.reason(self.run_affinity([current]), "missing_current_vector")
        self.assertNotEqual(current["source_version"], old["source_version"])

    def test_stale_base_raw_extraction_label_and_vector_text_hash_are_rejected(self):
        for field in ("base_hash", "raw_sha256", "extraction", "label", "body_source"):
            with self.subTest(field=field):
                node = self.make("text-" + field)
                with sqlite3.connect(self.cache) as db:
                    db.execute("UPDATE texts SET " + field + "='wrong' WHERE document_id=?", (node["id"],))
                self.reason(self.run_affinity([node]), "stale_text_binding")
        node = self.make("vector-hash")
        with sqlite3.connect(self.cache) as db:
            db.execute("UPDATE vectors SET text_hash=? WHERE document_id=?", ("0" * 64, node["id"]))
        self.reason(self.run_affinity([node]), "stale_vector_text_hash")

    def test_retained_original_matches_actual_raw_blob_not_only_cache_hashes(self):
        node = self.make("retained", text="", raw={"localOriginalText": "actual original\nfull text"})
        report = self.run_affinity([node])
        self.assertEqual(report["nodes"][0]["body_source"], "retained_record.localOriginalText")
        with sqlite3.connect(self.cache) as db:
            db.execute("UPDATE texts SET body='forged' WHERE document_id=?", (node["id"],))
            db.execute("UPDATE vectors SET text_hash=? WHERE document_id=?", (digest(b"topic\nforged"), node["id"]))
        self.reason(self.run_affinity([node]), "retained_text_source_mismatch")

    def test_source_corruption_and_current_projection_tamper_are_not_used(self):
        node = self.make("corrupt")
        with self.store.database() as db:
            blob = db.execute("SELECT raw_path FROM versions WHERE id=?", (node["source_version"],)).fetchone()[0]
        (self.store.blobs / blob).write_bytes(b"{}")
        self.reason(self.run_affinity([node]), "malformed_source")
        other = self.make("projection")
        with self.store.database() as db:
            db.execute("UPDATE versions SET body='tampered' WHERE id=?", (other["source_version"],))
        self.reason(self.run_affinity([other]), "current_version_hash_mismatch")

    def test_deep_metadata_rejects_only_affected_node_and_preserves_valid_neighbors(self):
        nodes = [self.make(name) for name in ("deep-metadata", "healthy-a", "healthy-b")]
        nested = '{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}'
        with self.store.database() as db:
            db.execute("UPDATE versions SET metadata=? WHERE id=?", (nested, nodes[0]["source_version"]))
            before = list(db.iterdump())
        with patch("socket.socket", side_effect=AssertionError("network")):
            report = self.run_affinity(nodes)
        # Newer Python parsers can accept this depth; its changed projection
        # must still be excluded. The pinned product runtime rejects nesting.
        reasons = report["coverage"]["reasons"]
        self.assertEqual(sum(reasons.values()), 1)
        self.assertTrue(set(reasons) <= {"malformed_current_version", "current_version_hash_mismatch"})
        self.assertEqual(report["state"], "bounded_partial")
        self.assertEqual(report["coverage"]["usable_vector_nodes"], 2)
        self.assertEqual({p["id"] for p in report["nodes"]}, {n["id"] for n in nodes[1:]})
        self.assertEqual(len(report["candidates"]), 1)
        with self.store.database() as db:
            self.assertEqual(list(db.iterdump()), before)

    def test_deep_canonical_source_is_a_data_error_without_changing_source(self):
        raw = b'{"nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'
        name = digest(raw) + ".json"
        path = self.store.blobs / name
        path.write_bytes(raw)
        guard = sa._Guard(sa.Budgets(), lambda: False)
        try:
            json.loads(raw)
        except RecursionError:
            with self.assertRaisesRegex(ValueError, "affinity_json_nesting"):
                sa._source(self.store.blobs, name, guard)
        else:
            self.assertIsInstance(sa._source(self.store.blobs, name, guard), dict)
        self.assertEqual(path.read_bytes(), raw)

    def test_source_budget_stop_is_not_reclassified_as_malformed_json(self):
        raw = b'{"nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'
        name = digest(raw) + ".json"
        (self.store.blobs / name).write_bytes(raw)
        guard = sa._Guard(replace(sa.Budgets(), max_source_bytes=1), lambda: False)
        with self.assertRaisesRegex(sa.BudgetExceeded, "affinity_source_bytes_budget"):
            sa._source(self.store.blobs, name, guard)

    def test_mixed_profile_dimension_malformed_nonfinite_and_zero_vectors_are_excluded(self):
        cases = [("model", "other", "different_profile"), ("endpoint", "0" * 64, "different_profile"),
                 ("encoding", "other", "different_profile"),
                 ("vector", struct.pack("<383f", *([0.] * 383)), "different_dimension"),
                 ("vector", b"odd", "malformed_vector"), ("vector", None, "malformed_vector"),
                 ("vector", "not bytes", "malformed_vector"),
                 ("vector", struct.pack("<384f", float("nan"), *([0.] * 383)), "nonfinite_vector"),
                 ("vector", struct.pack("<384f", float("inf"), *([0.] * 383)), "nonfinite_vector"),
                 ("vector", struct.pack("<384f", *([0.] * 384)), "nonunit_vector"),
                 ("vector", struct.pack("<384f", 2., *([0.] * 383)), "nonunit_vector")]
        for index, (field, value, reason) in enumerate(cases):
            with self.subTest(reason=reason, index=index):
                node = self.make("bad-" + str(index))
                with sqlite3.connect(self.cache) as db:
                    db.execute("UPDATE vectors SET " + field + "=? WHERE document_id=?", (value, node["id"]))
                report = self.run_affinity([node])
                self.reason(report, reason)
                self.assertEqual(report["candidates"], [])

    def test_vector_byte_budget_stops_before_reading_next_vector(self):
        nodes = [self.make("a"), self.make("b")]
        with self.assertRaisesRegex(sa.BudgetExceeded, "affinity_vector_bytes_budget"):
            self.run_affinity(nodes, budgets=replace(sa.Budgets(), max_vector_bytes=1536))
        with self.assertRaisesRegex(sa.BudgetExceeded, "affinity_vector_bytes_budget"):
            self.run_affinity(nodes[:1], budgets=replace(sa.Budgets(), max_vector_bytes=1535))
        report = self.run_affinity(nodes, budgets=replace(sa.Budgets(), max_vector_bytes=3072))
        self.assertEqual(report["metrics"]["vector_bytes"], 3072)

    def test_text_and_source_budgets_stop_instead_of_returning_completed_candidates(self):
        node = self.make("a")
        for key in ("max_text_bytes", "max_source_bytes"):
            with self.subTest(key=key), self.assertRaises(sa.BudgetExceeded):
                self.run_affinity([node], budgets=replace(sa.Budgets(), **{key: 1}))

    def test_pair_product_and_candidate_caps_report_partial_coverage(self):
        nodes = [self.make(name) for name in ("a", "b", "c", "d")]
        for budgets in (replace(sa.Budgets(), max_pairs=2), replace(sa.Budgets(), max_products=768)):
            report = self.run_affinity(nodes, budgets=budgets)
            self.assertEqual(report["state"], "bounded_partial")
            self.assertEqual(report["coverage"]["pairs_evaluated"], 2)
            self.assertEqual(report["coverage"]["eligible_same_project_pairs"], 6)
            self.assertAlmostEqual(report["coverage"]["pair_coverage"], 1 / 3)
            self.assertTrue(report["coverage"]["pair_budget_exhausted"])
        capped = self.run_affinity(nodes, budgets=replace(sa.Budgets(), max_candidates=1))
        self.assertEqual(len(capped["candidates"]), 1)
        self.assertEqual(capped["coverage"]["candidates_truncated"], 5)
        self.assertEqual(capped["state"], "bounded_partial")

    def test_zero_product_capacity_is_explicit_partial_and_order_is_deterministic(self):
        nodes = [self.make("a"), self.make("b"), self.make("c")]
        empty = self.run_affinity(nodes, budgets=replace(sa.Budgets(), max_products=383))
        self.assertEqual(empty["coverage"]["pairs_evaluated"], 0)
        self.assertTrue(empty["coverage"]["pair_budget_exhausted"])
        report = self.run_affinity(nodes)
        reversed_report = self.run_affinity(list(reversed(nodes)))
        self.assertEqual(report["candidates"], reversed_report["candidates"])
        self.assertEqual(report["input_sha256"], reversed_report["input_sha256"])

    def test_scope_node_profile_threshold_and_budget_validation(self):
        node = self.make("a")
        for projects in ([], [""], ["one"] * 17, "one"):
            with self.subTest(projects=projects), self.assertRaises(ValueError):
                sa.affinity(self.root, projects, [node])
        for nodes in ([node] * 2049, [node, node], [{"id": node["id"]}]):
            with self.assertRaises(ValueError):
                self.run_affinity(nodes)
        for threshold in (True, float("nan"), 1.1):
            with self.assertRaises(ValueError):
                self.run_affinity([node], min_cosine=threshold)
        for budget in (replace(sa.Budgets(), max_nodes=2049), replace(sa.Budgets(), max_pairs=True),
                       replace(sa.Budgets(), wall_seconds=float("inf")), replace(sa.Budgets(), cpu_seconds=21)):
            with self.assertRaises(ValueError):
                self.run_affinity([node], budgets=budget)
        with self.assertRaisesRegex(ValueError, "local_e5_profile_required"):
            self.run_affinity([node], profile=replace(sa.E5Profile(), dimension=768))

    def test_2048_node_ceiling_is_accepted_with_explicit_missing_coverage(self):
        nodes = [{"id": "graph:missing-" + str(i), "source_version": "version:absent", "source_target": self.a} for i in range(2048)]
        report = self.run_affinity(nodes)
        self.assertEqual(report["coverage"]["requested_nodes"], 2048)
        self.assertEqual(report["coverage"]["usable_vector_nodes"], 0)
        self.reason(report, "not_current_or_permitted", 2048)

    def test_empty_input_coverage_is_null_and_never_whole_graph_coverage(self):
        report = self.run_affinity([])
        self.assertIsNone(report["coverage"]["node_coverage"])
        self.assertIsNone(report["coverage"]["pair_coverage"])
        self.assertIn("only supplied graph nodes", report["limitations"][0])

    def test_nontext_current_body_and_oversized_cache_text_fail_closed(self):
        node = self.make("body")
        with self.store.database() as db:
            db.execute("UPDATE versions SET body=? WHERE id=?", (b"blob", node["source_version"]))
        self.reason(self.run_affinity([node]), "malformed_current_version")
        other = self.make("oversized")
        with sqlite3.connect(self.cache) as db:
            db.execute("UPDATE texts SET body=? WHERE document_id=?", ("x" * (2 * 1024 * 1024 + 1), other["id"]))
        self.reason(self.run_affinity([other]), "malformed_bound_text")

    def test_symlink_source_and_retrieval_are_rejected(self):
        node = self.make("source")
        with self.store.database() as db:
            name = db.execute("SELECT raw_path FROM versions WHERE id=?", (node["source_version"],)).fetchone()[0]
        source = self.store.blobs / name
        retained = self.root / "retained.json"
        source.rename(retained)
        source.symlink_to(retained)
        self.reason(self.run_affinity([node]), "malformed_source")
        moved = self.root / "retrieval.sqlite3"
        self.cache.rename(moved)
        self.cache.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "affinity_symlink"):
            self.run_affinity([node])

    def test_cpu_wall_rss_and_cancellation_report_precise_stops(self):
        guard = sa._Guard(sa.Budgets(), lambda: False)
        for clock, value, message in (("time.monotonic", guard.started + 21, "affinity_wall_budget"),
                                      ("time.process_time", guard.cpu_started + 9, "affinity_cpu_budget"),
                                      ("_rss", 129 * 1024 * 1024, "affinity_rss_budget")):
            target = "alden_semantic_affinity." + clock
            with self.subTest(message=message), patch(target, return_value=value), self.assertRaisesRegex(sa.BudgetExceeded, message):
                guard.check()
        with self.assertRaisesRegex(RuntimeError, "affinity_cancelled"):
            self.run_affinity([], cancelled=lambda: True)

    def test_sql_progress_interruption_preserves_cpu_budget_error(self):
        guard = sa._Guard(sa.Budgets(), lambda: False)
        with sqlite3.connect(":memory:") as db:
            db.set_progress_handler(guard.progress, 1)
            with patch.object(guard, "check", side_effect=sa.BudgetExceeded("affinity_cpu_budget")):
                with self.assertRaisesRegex(sa.BudgetExceeded, "affinity_cpu_budget"), sa._sql_errors(guard):
                    db.execute("SELECT 1").fetchone()

    def test_closed_wal_is_refused_before_sql_connection_or_sidecar_creation(self):
        node = self.make("closed")
        with sqlite3.connect(self.cache) as db:
            db.execute("PRAGMA journal_mode=WAL")
        # Close this owned fixture explicitly; sqlite3 context exits a
        # transaction but is not a connection lifetime context manager.
        db.close()
        wal, shm = Path(str(self.cache) + "-wal"), Path(str(self.cache) + "-shm")
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())
        before = digest(self.cache.read_bytes())
        with patch("sqlite3.connect", side_effect=AssertionError("must refuse before SQLite open")):
            with self.assertRaisesRegex(ValueError, "affinity_wal_sidecars_required:retrieval.sqlite3"):
                self.run_affinity([node])
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())
        self.assertEqual(digest(self.cache.read_bytes()), before)

    def test_existing_wal_reader_does_not_checkpoint_or_ignore_current_frames(self):
        node = self.make("live-wal")
        writer = sqlite3.connect(self.cache)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE vectors SET text_hash=? WHERE document_id=?", ("0" * 64, node["id"]))
        writer.commit()
        before = {str(p): digest(p.read_bytes()) for p in (self.cache, Path(str(self.cache) + "-wal"))}
        # The stale hash is in live WAL frames, so immutable/main-only readers
        # would incorrectly admit this vector. The reader must see the WAL.
        self.reason(self.run_affinity([node]), "stale_vector_text_hash")
        self.assertEqual(before, {p: digest(Path(p).read_bytes()) for p in before})

    def test_shared_evaluator_reuses_verified_owner_without_open_commit_or_close(self):
        from alden_layout_affinity import read_transactions
        node = self.make("borrowed")
        with read_transactions(self.root) as owner:
            with patch("sqlite3.connect", side_effect=AssertionError("evaluator must borrow")):
                report = sa._affinity_on_reads(owner.store, owner.collection, owner.cache, ["one"], [node], guard=owner.guard)
            self.assertEqual(report["state"], "bounded_ready")
            self.assertTrue(owner.collection.in_transaction)
            self.assertTrue(owner.cache.in_transaction)

    def test_shared_evaluator_refuses_writer_or_unbegun_reader(self):
        from alden_layout_affinity import read_transactions
        node = self.make("writer")
        with read_transactions(self.root) as owner:
            with self.assertRaisesRegex(ValueError, "affinity_verified_read_transactions_required"):
                sa._affinity_on_reads(self.store, owner.collection, owner.cache, ["one"], [node], guard=owner.guard)
            owner.cache.rollback()
            with self.assertRaisesRegex(ValueError, "affinity_verified_read_transactions_required"):
                sa._affinity_on_reads(owner.store, owner.collection, owner.cache, ["one"], [node], guard=owner.guard)

    def test_cli_success_and_failure_are_machine_readable_and_emit_no_partial_on_error(self):
        node = self.make("a")
        path = self.root / "nodes.json"
        path.write_text(json.dumps([node]))
        args = ["--state-root", str(self.root), "--project", "one", "--nodes-json", str(path)]
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(sa.main(args), 0)
            self.assertEqual(json.loads(out.getvalue())["state"], "bounded_ready")
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(sa.main(args + ["--max-pairs", "0"]), 2)
            failure = json.loads(out.getvalue())
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["state"], "failed")
        self.assertNotIn("candidates", failure)


if __name__ == "__main__":
    unittest.main()
