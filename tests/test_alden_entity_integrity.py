"""Synthetic public-action regressions for CollectionStore v3 entity integrity.

Only this test's temporary collection may be changed.  Resolution requests go
through resolution_action; no resolution internals are mocked or invoked.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alden_collection import CollectionStore, digest, encoded, identity
import alden_entity_resolution as resolution
from tests.test_alden_entity_resolution import edge, record


class EntityIntegrityTests(unittest.TestCase):
    ORIGINAL_TABLES = (
        "meta", "targets", "target_projects", "documents", "versions",
        "memberships", "relations", "relation_versions", "runs", "events",
        "target_snapshots", "event_counts",
    )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="alden-entity-integrity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = CollectionStore(self.root)
        self.t1 = self.store.register(platform="graph", original_id="source-one", kind="source",
                                      label="source one", projects=["one"])
        self.t2 = self.store.register(platform="graph", original_id="source-two", kind="source",
                                      label="source two", projects=["one"])
        self.t3 = self.store.register(platform="graph", original_id="other-project", kind="source",
                                      label="other", projects=["two"])
        self.store.ingest(self.t1, [record("a"), record("c")])
        self.store.ingest(self.t2, [record("b")], relations=[
            edge("a", "b"), edge("b", "c"), edge("c", "a")])
        self.store.ingest(self.t3, [record("e"), record("f")])

    def member(self, target, name):
        document_id = identity("graph", name)
        with self.store.database() as db:
            version = db.execute(
                "SELECT current_version FROM memberships WHERE target_id=? AND document_id=?",
                (target, document_id),
            ).fetchone()[0]
        return {"target_id": target, "document_id": document_id, "version": version}

    def preview(self, members=None, project="one"):
        members = members if members is not None else [self.member(self.t1, "a"), self.member(self.t2, "b")]
        return resolution.resolution_action(self.root, "collection-resolution-preview", {
            "projects": [project], "members": list(reversed(members)),
            "canonical": {key: members[0][key] for key in ("target_id", "document_id")},
            "reason": "Human-reviewed synthetic members; text alone is not identity",
        })

    def apply(self, plan, request_id="integrity-apply"):
        return resolution.resolution_action(self.root, "collection-resolution-apply",
                                            {"plan": plan, "request_id": request_id},
                                            explicit_opt_in=True)

    def undo(self, applied, expected_revision, request_id="integrity-undo"):
        return resolution.resolution_action(self.root, "collection-resolution-revert", {
            "projects": [applied["project"]], "resolution_id": applied["resolution_id"],
            "expected_revision": expected_revision, "request_id": request_id,
        }, explicit_opt_in=True)

    def history(self, project="one", **options):
        return resolution.resolution_action(self.root, "collection-resolution-history",
                                            {"projects": [project], **options})

    def active(self, project="one"):
        with self.store.database() as db:
            return resolution.active_resolutions(self.store, db, [project])

    def feature_tables(self):
        with self.store.database() as db:
            return {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'resolution_%'")}

    def source_snapshot(self):
        # Source tables and blob bytes, including original journal events, must
        # remain unchanged across any resolution apply/revert/error.
        with self.store.database() as db:
            tables = {name: digest(encoded(sorted(encoded(dict(row)).decode() for row in
                       db.execute("SELECT * FROM " + name)))) for name in self.ORIGINAL_TABLES}
        blobs = {path.name: digest(path.read_bytes()) for path in self.store.blobs.iterdir() if path.is_file()}
        return tables, blobs

    def test_v3_projection_corruption_rejects_fresh_preview_and_apply_before_bootstrap(self):
        for column, replacement in (
            ('label', 'Altered label without a new version'),
            ('body', 'Altered body without a new version'),
            ('metadata', '{"source_url":"local://altered-metadata"}'),
            ('projection_sha256', '0' * 64),
            ('processing_version', 'altered-processor-without-new-version'),
        ):
            with self.subTest(column=column):
                plan = self.preview()['plan']
                version = self.member(self.t1, 'a')['version']
                with self.store.database() as db:
                    original = db.execute('SELECT ' + column + ' FROM versions WHERE id=?', (version,)).fetchone()[0]
                    db.execute('UPDATE versions SET ' + column + '=? WHERE id=?', (replacement, version))
                try:
                    with self.assertRaisesRegex(RuntimeError, '^collection_resolution_projection_integrity$'):
                        self.preview()
                    with self.assertRaisesRegex(RuntimeError, '^collection_resolution_projection_integrity$'):
                        self.apply(plan, 'invalid-projection-' + column)
                    self.assertEqual(self.feature_tables(), set())
                    self.assertEqual(list(self.store.root.glob('collection.resolution-before-v1-*.sqlite3')), [])
                finally:
                    with self.store.database() as db:
                        db.execute('UPDATE versions SET ' + column + '=? WHERE id=?', (original, version))
                self.assertEqual(self.preview()['plan'], plan)

    def test_projection_validation_is_repeated_inside_apply_transaction(self):
        plan = self.preview()['plan']
        version = self.member(self.t1, 'a')['version']
        backup = resolution._backup_before_feature
        def mutate_after_preflight(store):
            backup(store)
            with self.store.database() as db:
                db.execute('UPDATE versions SET projection_sha256=? WHERE id=?', ('0' * 64, version))
        with patch.object(resolution, '_backup_before_feature', side_effect=mutate_after_preflight):
            with self.assertRaisesRegex(RuntimeError, '^collection_resolution_projection_integrity$'):
                self.apply(plan, 'late-invalid-projection')
        self.assertEqual(self.feature_tables(), set())

    def test_active_overlay_stops_projecting_on_invalid_projection_and_recovers_without_reapply(self):
        plan = self.preview()['plan']
        applied = self.apply(plan, 'projection-current')
        version = self.member(self.t1, 'a')['version']
        with self.store.database() as db:
            projection = db.execute('SELECT projection_sha256 FROM versions WHERE id=?', (version,)).fetchone()[0]
            db.execute('UPDATE versions SET projection_sha256=? WHERE id=?', ('0' * 64, version))
        try:
            self.assertEqual(self.active(), [])
            self.assertEqual(self.history()['items'][0]['current_validity'], {
                'valid': False, 'state': 'collection_resolution_projection_integrity'})
            # A replay is the historical receipt, not a new activation or a
            # claim that the previously accepted source is still current.
            self.assertEqual(self.apply(plan, 'projection-current'), applied)
            self.assertEqual(self.active(), [])
        finally:
            with self.store.database() as db:
                db.execute('UPDATE versions SET projection_sha256=? WHERE id=?', (projection, version))
        self.assertEqual(self.active()[0]['resolution_id'], applied['resolution_id'])
        self.assertEqual(len(self.history()['items']), 1)

    def test_source_metadata_excluded_from_projection_keeps_its_provenance_guard(self):
        plan = self.preview()['plan']
        version = self.member(self.t1, 'a')['version']
        with self.store.database() as db:
            metadata = json.loads(db.execute('SELECT metadata FROM versions WHERE id=?', (version,)).fetchone()[0])
            metadata['source'] = 'changed source declaration, not a new body projection'
            db.execute('UPDATE versions SET metadata=? WHERE id=?', (encoded(metadata).decode(), version))
        refreshed = self.preview()['plan']
        self.assertNotEqual(refreshed['source_fingerprint'], plan['source_fingerprint'])
        with self.assertRaisesRegex(ValueError, '^collection_resolution_source_fingerprint_changed$'):
            self.apply(plan, 'old-source-metadata')
        self.assertEqual(self.apply(refreshed, 'new-source-metadata')['state'], 'active')

    def test_selected_original_identity_mismatch_rejected_by_preview_and_apply(self):
        for column, replacement in (("platform", "youtube"), ("original_id", "a-replaced")):
            with self.subTest(column=column):
                plan = self.preview()["plan"]
                document_id = identity("graph", "a")
                with self.store.database() as db:
                    original = db.execute("SELECT " + column + " FROM documents WHERE id=?",
                                          (document_id,)).fetchone()[0]
                    db.execute("UPDATE documents SET " + column + "=? WHERE id=?",
                               (replacement, document_id))
                try:
                    with self.assertRaisesRegex(RuntimeError,
                                                "^collection_resolution_original_identity_mismatch$"):
                        self.preview()
                    with self.assertRaisesRegex(RuntimeError,
                                                "^collection_resolution_original_identity_mismatch$"):
                        self.apply(plan, "identity-" + column)
                    self.assertEqual(self.feature_tables(), set())
                    self.assertEqual(list(self.store.root.glob("collection.resolution-before-v1-*.sqlite3")), [])
                finally:
                    with self.store.database() as db:
                        db.execute("UPDATE documents SET " + column + "=? WHERE id=?",
                                   (original, document_id))
                self.assertEqual(self.preview()["plan"], plan)

        source = self.source_snapshot()
        applied = self.apply(self.preview()["plan"], "identity-active")
        with self.store.database() as db:
            db.execute("UPDATE documents SET original_id=? WHERE id=?",
                       ("a-replaced-again", identity("graph", "a")))
        try:
            self.assertEqual(self.history()["items"][0]["current_validity"], {
                "valid": False, "state": "collection_resolution_original_identity_mismatch"})
            self.assertEqual(self.active(), [])
        finally:
            with self.store.database() as db:
                db.execute("UPDATE documents SET original_id='a' WHERE id=?",
                           (identity("graph", "a"),))
        self.assertEqual(self.active()[0]["resolution_id"], applied["resolution_id"])
        self.assertEqual(self.source_snapshot(), source)

    def test_replaced_collection_journal_invalidates_plan_and_active_review(self):
        initial = self.preview()["plan"]
        self.assertEqual(set(initial), {"schema", "project", "members", "canonical", "reason",
                                        "revision", "source_fingerprint", "plan_sha256"})
        self.assertEqual(initial["schema"], 1)
        with self.store.database() as db:
            journal = db.execute("SELECT value FROM meta WHERE key='journal_id'").fetchone()[0]
            replacement = "0" * 32 if journal != "0" * 32 else "1" * 32
            db.execute("UPDATE meta SET value=? WHERE key='journal_id'", (replacement,))
        with self.assertRaisesRegex(ValueError, "^collection_resolution_source_fingerprint_changed$"):
            self.apply(initial, "replaced-journal-before-bootstrap")
        self.assertEqual(self.feature_tables(), set())
        self.assertEqual(list(self.store.root.glob("collection.resolution-before-v1-*.sqlite3")), [])

        with self.store.database() as db:
            db.execute("UPDATE meta SET value=? WHERE key='journal_id'", (journal,))
        applied = self.apply(initial, "journal-valid-apply")
        self.assertEqual(len(self.active()), 1)
        with self.store.database() as db:
            db.execute("UPDATE meta SET value=? WHERE key='journal_id'", (replacement,))
        self.assertEqual(self.history()["items"][0]["current_validity"], {
            "valid": False, "state": "collection_resolution_source_provenance_changed"})
        self.assertEqual(self.active(), [])
        with self.store.database() as db:
            db.execute("UPDATE meta SET value=? WHERE key='journal_id'", (journal,))
        self.assertEqual(self.history()["items"][0]["current_validity"]["state"], "current")
        self.assertEqual(self.active()[0]["resolution_id"], applied["resolution_id"])

    def test_malformed_journal_has_its_own_guard_not_a_generic_stale_error(self):
        with self.store.database() as db:
            db.execute("UPDATE meta SET value=? WHERE key='journal_id'", ("not-a-32-hex-id",))
        with self.assertRaisesRegex(RuntimeError, "^collection_resolution_journal_identity_invalid$"):
            self.preview()
        self.assertEqual(self.feature_tables(), set())

    def test_same_version_member_stored_event_changes_provenance(self):
        initial = self.preview()
        self.store.ingest(self.t2, [record("b")])  # Fresh current-version journal receipt.
        self.assertEqual(self.member(self.t2, "b")["version"],
                         next(m["version"] for m in initial["plan"]["members"]
                              if m["target_id"] == self.t2))
        refreshed = self.preview()
        self.assertEqual(refreshed["records"], initial["records"])
        self.assertNotEqual(refreshed["plan"]["source_fingerprint"],
                            initial["plan"]["source_fingerprint"])
        with self.assertRaisesRegex(ValueError, "^collection_resolution_source_fingerprint_changed$"):
            self.apply(initial["plan"], "stale-stored-event")
        self.assertEqual(self.feature_tables(), set())

        applied = self.apply(refreshed["plan"], "current-stored-event")
        self.store.ingest(self.t1, [record("c")])  # Unrelated document on a selected target.
        self.assertEqual(self.history()["items"][0]["current_validity"]["state"], "current")
        self.assertEqual(self.active()[0]["resolution_id"], applied["resolution_id"])

        # Indexing receipts do not substitute for the source's stored receipt.
        with self.store.database() as db:
            db.execute("""UPDATE events SET details=? WHERE sequence=(
                SELECT MAX(sequence) FROM events WHERE document_id=? AND target_id=? AND stage='indexed')""",
                (json.dumps({"index": "synthetic-replayed-index"}), identity("graph", "a"), self.t1))
        self.assertEqual(self.history()["items"][0]["current_validity"]["state"], "current")

        self.store.ingest(self.t1, [record("a")])
        self.assertEqual(self.member(self.t1, "a")["version"],
                         next(m["version"] for m in refreshed["plan"]["members"]
                              if m["target_id"] == self.t1))
        self.assertEqual(self.history()["items"][0]["current_validity"], {
            "valid": False, "state": "collection_resolution_source_provenance_changed"})
        self.assertEqual(self.active(), [])

    def test_active_group_cap_is_per_project_atomic_and_reversible(self):
        # One collection batch, then 256 distinct reviewed groups through the
        # public preview/apply API. No synthetic resolution-table inserts.
        names = ["cap-%04d" % index for index in range(514)]
        self.store.ingest(self.t1, [record(name) for name in names])
        source = self.source_snapshot()
        applied = []
        for index in range(256):
            members = [self.member(self.t1, name) for name in names[2 * index:2 * index + 2]]
            plan = self.preview(members)["plan"]
            result = self.apply(plan, "cap-apply-%03d" % index)
            self.assertEqual(result["revision"], index + 1)
            applied.append((plan, result))

        self.assertEqual(len(self.active()), 256)
        self.assertEqual(self.history()["revision"], 256)
        with self.store.database() as db:
            before = tuple(db.execute("SELECT count(*) FROM " + name).fetchone()[0] for name in (
                "resolution_decisions", "resolution_active_groups", "resolution_events", "resolution_requests"))
        rejected = self.preview([self.member(self.t1, name) for name in names[512:514]])["plan"]
        self.assertEqual(rejected["revision"], 256)
        with self.assertRaisesRegex(ValueError, "^collection_resolution_active_group_budget_exceeded$"):
            self.apply(rejected, "cap-rejected")
        with self.store.database() as db:
            after = tuple(db.execute("SELECT count(*) FROM " + name).fetchone()[0] for name in (
                "resolution_decisions", "resolution_active_groups", "resolution_events", "resolution_requests"))
        self.assertEqual(before, after)
        self.assertEqual(self.history()["revision"], 256)
        self.assertEqual(self.apply(applied[0][0], "cap-apply-000"), applied[0][1])

        # The budget counts a project's active groups, not all projects.
        other = self.apply(self.preview([self.member(self.t3, "e"), self.member(self.t3, "f")],
                                        project="two")["plan"], "cap-other-project")
        self.assertEqual(other["revision"], 1)
        self.assertEqual(len(self.active("two")), 1)
        undone = self.undo(applied[0][1], 256, "cap-undo-first")
        self.assertEqual(undone["revision"], 257)
        self.assertEqual(len(self.active()), 255)
        self.assertEqual(self.apply(applied[0][0], "cap-apply-000"), applied[0][1])
        self.assertEqual(len(self.active()), 255)  # Historical replay does not reactivate.
        replacement = self.apply(self.preview([self.member(self.t1, name) for name in names[512:514]])["plan"],
                                 "cap-replacement")
        self.assertEqual(replacement["revision"], 258)
        self.assertEqual(len(self.active()), 256)
        self.assertEqual(self.source_snapshot(), source)
        self.assertEqual(len(list(self.store.root.glob("collection.resolution-before-v1-*.sqlite3"))), 1)

    def test_first_feature_backup_timeout_rolls_back_and_allows_retry(self):
        plan = self.preview()["plan"]
        source = self.source_snapshot()
        calls = []

        def elapsed_during_backup():
            calls.append(len(calls))
            # Entry and initial check pass; the SQLite backup progress callback
            # observes an expired deadline after the destination was created.
            return 1000.0 if len(calls) < 3 else 1011.0

        with patch.object(resolution.time, "monotonic", side_effect=elapsed_during_backup):
            with self.assertRaisesRegex(RuntimeError, "^collection_resolution_backup_time_budget$"):
                self.apply(plan, "deadline-expired")
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(self.feature_tables(), set())
        self.assertEqual(self.history()["revision"], 0)
        self.assertEqual(self.source_snapshot(), source)
        self.assertEqual(list(self.store.root.glob("collection.resolution-before-v1-*.sqlite3")), [])
        applied = self.apply(plan, "deadline-retry")
        self.assertEqual(applied["revision"], 1)
        self.assertEqual(self.history()["items"][0]["current_validity"]["state"], "current")
        self.assertEqual(self.source_snapshot(), source)


if __name__ == "__main__":
    unittest.main()
