"""Independent, synthetic regression checks for the quality audit's source evidence.

The retained canonical record is the only authority for a source declaration
when verification is requested. Matching text never proves original-file or
human identity, and an unread-body notice never supplies document content.
"""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alden_collection import CollectionStore, digest, encoded, identity
import alden_graph_quality as quality


UNREAD_NOTICE = "경로만 색인한 파일. 본문은 읽지 않았습니다."


class VerifiedSourceQualityTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="alden-source-quality-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.target = self.store.register(
            platform="graph", original_id="private-source-fixture",
            kind="source", label="private", projects=["one"],
        )

    def record(self, name, body, *, raw, label="shared-name.ts"):
        return {
            "platform": "graph", "original_id": name,
            "label": label, "text": body, "raw": raw,
        }

    def report(self, *, verify=True):
        return quality.audit(self.root, ["one"], verify_sources=verify)

    def version(self, name):
        with self.store.database() as db:
            return dict(db.execute(
                "SELECT * FROM versions WHERE document_id=?",
                (identity("graph", name),),
            ).fetchone())

    def test_verified_source_only_exclusions_survive_projection_omission(self):
        # All three declarations live ONLY in canonical JSON, never metadata.
        declarations = (
            {"content_indexed": False},
            {"content_indexed": 0},
            {"kind": "metadata"},
        )
        records = []
        for index, declaration in enumerate(declarations):
            for member in ("a", "b"):
                name = f"source-only-{index}-{member}"
                records.append(self.record(
                    name, f"same projected body for exclusion {index}",
                    raw={**declaration, "original_path": name},
                ))
        self.store.ingest(self.target, records)
        for record in records:
            version = self.version(record["original_id"])
            self.assertNotIn('"content_indexed"', version["metadata"])
            self.assertNotIn('"kind"', version["metadata"])

        report = self.report()
        self.assertEqual(report["findings"], {})
        self.assertEqual(report["counts"]["source_blobs_verified"], 6)
        self.assertEqual(report["counts"]["project_current_versions"], 6)
        self.assertEqual(report["counts"]["noncontent_current_versions"], 6)
        self.assertEqual(report["counts"]["source_metadata_only_current_versions"], 6)
        self.assertEqual(report["duplicate_candidates"]["same_normalized_search_text"]["groups"], 0)
        self.assertEqual(report["duplicate_candidates"]["cross_context_search_text"]["groups"], 0)
        names = report["metadata_matches"]["within_context"]
        self.assertEqual(names["groups"], 3)
        self.assertEqual(names["distinct_document_members"], 6)
        self.assertEqual(names["status"], "metadata_match_not_content")

    def test_identical_substantive_text_is_only_a_review_candidate(self):
        body = "Invoice item: 10 units\nRecorded on 2026-10-12."
        names = ("independent-person-a", "independent-person-b")
        self.store.ingest(self.target, [
            self.record(name, body, raw={"content_indexed": True, "original_path": name})
            for name in names
        ])

        report = self.report()
        pair = report["duplicate_candidates"]["same_normalized_search_text"]
        self.assertEqual(report["findings"], {})
        self.assertEqual(report["counts"]["source_blobs_verified"], 2)
        self.assertEqual(report["counts"]["noncontent_current_versions"], 0)
        self.assertEqual(pair["groups"], 1)
        self.assertEqual(pair["distinct_document_members"], 2)
        self.assertEqual(pair["unique_document_ids"], 2)
        self.assertEqual(pair["status"], "review_candidates_not_confirmed_entities")
        self.assertEqual(
            set(pair["samples"][0]["document_ids"]),
            {identity("graph", name) for name in names},
        )
        # Each independent original has different canonical record bytes.
        self.assertEqual(report["duplicate_candidates"]["same_raw_bytes"]["groups"], 0)
        self.assertEqual(report["metadata_matches"]["within_context"]["groups"], 0)

    def test_shared_words_across_authors_do_not_prove_one_person(self):
        body = "Identical wording in separately attributed local notes."
        records = []
        for index in (1, 2):
            name = f"attributed-person-{index}"
            record = self.record(name, body, raw={"original_path": name})
            record["source"] = {"author_id": f"person-{index}", "revision": index}
            records.append(record)
        self.store.ingest(self.target, records)

        report = self.report()
        self.assertEqual(report["counts"]["source_blobs_verified"], 2)
        self.assertEqual(report["duplicate_candidates"]["same_normalized_search_text"]["groups"], 0)
        across = report["duplicate_candidates"]["cross_context_search_text"]
        self.assertEqual(across["groups"], 1)
        self.assertEqual(across["samples"][0]["context_count"], 2)
        self.assertEqual(across["status"], "review_candidates_not_confirmed_entities")
        self.assertEqual(report["duplicate_candidates"]["same_raw_bytes"]["groups"], 0)
        self.assertEqual(report["findings"], {})

    def test_exact_unread_notice_is_not_the_same_as_a_quoted_notice(self):
        quotation = f"Audit log quotes: {UNREAD_NOTICE} The log continues with details."
        self.store.ingest(self.target, [
            self.record(f"unread-{i}", UNREAD_NOTICE, raw={"original_path": f"unread-{i}"})
            for i in range(2)
        ] + [
            self.record(f"quoted-{i}", quotation, raw={"original_path": f"quoted-{i}"})
            for i in range(2)
        ])

        report = self.report()
        self.assertEqual(report["counts"]["source_blobs_verified"], 4)
        self.assertEqual(report["counts"]["unread_body_marker_current_versions"], 2)
        self.assertEqual(report["counts"]["noncontent_current_versions"], 2)
        self.assertEqual(report["metadata_matches"]["within_context"]["groups"], 1)
        pair = report["duplicate_candidates"]["same_normalized_search_text"]
        self.assertEqual(pair["groups"], 1)
        self.assertEqual(
            set(pair["samples"][0]["document_ids"]),
            {identity("graph", "quoted-0"), identity("graph", "quoted-1")},
        )
        self.assertEqual(report["findings"], {})

    def test_corrupt_hash_excludes_unverified_text_from_verified_candidates(self):
        body = "same plausible retained content"
        names = ("corrupt-hash", "healthy-hash")
        self.store.ingest(self.target, [
            self.record(name, body, raw={"original_path": name}) for name in names
        ])
        damaged = self.store.blobs / self.version(names[0])["raw_path"]
        damaged.write_bytes(b"replaced canonical bytes with a mismatching hash")

        report = self.report()
        self.assertEqual(report["findings"].get("source_blob_invalid"), 1)
        self.assertEqual(report["counts"]["source_blobs_verified"], 1)
        self.assertEqual(report["counts"]["project_current_versions"], 2)
        self.assertEqual(report["duplicate_candidates"]["same_normalized_search_text"]["groups"], 0)
        self.assertEqual(report["duplicate_candidates"]["cross_context_search_text"]["groups"], 0)
        self.assertEqual(report["duplicate_candidates"]["same_raw_bytes"]["groups"], 0)

    def test_invalid_json_with_matching_hash_is_not_verified_content(self):
        body = "same plausible retained content"
        names = ("invalid-json", "healthy-json")
        self.store.ingest(self.target, [
            self.record(name, body, raw={"original_path": name}) for name in names
        ])
        old = self.version(names[0])
        # Bind a synthetic malformed JSON blob to its CORRECT SHA/version, so
        # JSON validation is isolated from raw-hash or projection failures.
        invalid = b'{"content_indexed":false,"kind":"metadata",'
        sha = digest(invalid)
        (self.store.blobs / (sha + ".json")).write_bytes(invalid)
        new_id = identity("version", encoded([
            old["document_id"], sha, old["processing_version"],
            old["projection_sha256"],
        ]).decode())
        with self.store.database() as db:
            db.execute(
                "UPDATE versions SET id=?,raw_sha256=?,raw_path=? WHERE id=?",
                (new_id, sha, sha + ".json", old["id"]),
            )
            db.execute(
                "UPDATE memberships SET current_version=? WHERE current_version=?",
                (new_id, old["id"]),
            )
            db.execute(
                "UPDATE documents SET current_version=? WHERE id=?",
                (new_id, old["document_id"]),
            )

        report = self.report()
        self.assertEqual(report["findings"].get("source_blob_invalid"), 1)
        self.assertNotIn("version_identity_mismatch", report["findings"])
        self.assertNotIn("projection_hash_mismatch", report["findings"])
        self.assertEqual(report["counts"]["source_blobs_verified"], 1)
        self.assertEqual(report["duplicate_candidates"]["same_normalized_search_text"]["groups"], 0)

    def test_manual_unverified_audit_never_reads_canonical_source(self):
        body = "shared projected text, with unknown canonical declaration"
        raw = {"content_indexed": False, "kind": "metadata"}
        self.store.ingest(self.target, [
            self.record(f"unverified-{i}", body, raw=raw) for i in range(2)
        ])

        with patch.object(quality, "_blob", side_effect=AssertionError("source reread")) as read:
            report = self.report(verify=False)
        read.assert_not_called()
        self.assertFalse(report["source_verification_requested"])
        self.assertEqual(report["coverage"]["source_verification"], "hash_references_only")
        self.assertEqual(report["counts"]["source_blobs_verified"], 0)
        self.assertEqual(report["duplicate_candidates"]["same_raw_bytes"]["verified_groups"], 0)
        self.assertEqual(report["duplicate_candidates"]["same_raw_bytes"]["hash_only_groups"], 1)
        # Only projected evidence is available to this manual, non-verifying path.
        self.assertEqual(report["duplicate_candidates"]["same_normalized_search_text"]["groups"], 1)


if __name__ == "__main__":
    unittest.main()
