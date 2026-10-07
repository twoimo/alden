"""Collection crash, identity, lineage and permission contracts in private stores."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_collection import CollectionStore, TargetBusy, identity


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=Path("/private/tmp") if Path("/private/tmp").is_dir() else None)
        self.addCleanup(temporary.cleanup)
        self.store = CollectionStore(Path(temporary.name))

    def target(self, original="channel-A", project="one", **kwargs):
        return self.store.register(platform="youtube", original_id=original, kind="channel", label="동명이인", projects=[project], **kwargs)

    @staticmethod
    def record(original="AbC", text="회의는 3층", **kwargs):
        return {"platform": "youtube", "original_id": original, "label": "제목", "text": text, "author_id": "channel-A", **kwargs}

    def test_replay_is_idempotent_and_cursor_commits_with_records(self):
        target = self.target()
        first = self.store.ingest(target, [self.record()], cursor={"after": "AbC"}, run_id="fixed")
        second = self.store.ingest(target, [self.record(text="should not replace")], cursor="bad", run_id="fixed")
        self.assertEqual(first["added"], 1)
        self.assertEqual(second["state"], "complete")
        self.assertEqual(json.loads(self.store.target(target)["cursor"]), {"after": "AbC"})
        self.assertEqual(len(self.store.graph(projects=["one"])["nodes"]), 1)
        self.assertEqual(len(self.store.events()["items"]), 5)

    def test_source_edit_keeps_original_bytes_and_prior_version(self):
        target = self.target()
        self.store.ingest(target, [self.record(text="原文  空白")])
        result = self.store.ingest(target, [self.record(text="새 원문")])
        self.assertEqual(result["revised"], 1)
        with self.store.database() as db:
            versions = db.execute("SELECT raw_path FROM versions").fetchall()
        self.assertEqual(len(versions), 2)
        raw = [json.loads((self.store.blobs / row[0]).read_text()) for row in versions]
        self.assertEqual({r["text"] for r in raw}, {"原文  空白", "새 원문"})

    def test_shared_original_has_two_target_lineages_without_merging_people(self):
        a = self.target()
        b = self.target("channel-B", "two")
        self.assertNotEqual(a, b)
        self.store.ingest(a, [self.record()])
        self.store.ingest(b, [self.record()])
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memberships").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 1)

    def test_case_sensitive_ids_and_multiple_relationship_types_survive(self):
        target = self.target()
        records = [self.record("AbC"), self.record("abc")]
        relations = [{"source_platform": "youtube", "source_id": "AbC", "target_platform": "youtube", "target_id": "abc", "type": kind, "evidence": {"source": "declared"}} for kind in ("refers-to", "corrects")]
        self.store.ingest(target, records, relations=relations)
        graph = self.store.graph(projects=["one"])
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual({e["relation"] for e in graph["edges"]}, {"refers-to", "corrects"})

    def test_failure_rolls_back_documents_index_events_and_checkpoint(self):
        target = self.target()
        self.store.ingest(target, [self.record()], cursor="before")
        with self.assertRaisesRegex(ValueError, "collection_identity_invalid"):
            self.store.ingest(target, [self.record("new"), self.record("")], cursor="after")
        self.assertEqual(json.loads(self.store.target(target)["cursor"]), "before")
        self.assertEqual(len(self.store.graph(projects=["one"])["nodes"]), 1)
        self.assertEqual([e["stage"] for e in self.store.events()["items"]][-1], "failed")
        self.assertFalse(any(e["document_id"] == identity("youtube", "new") for e in self.store.events()["items"]))

    def test_permissions_filter_graph_search_and_history(self):
        allowed = self.target()
        denied = self.target("channel-B", "two", permission="denied")
        self.store.ingest(allowed, [self.record(text="자료 검색")])
        self.store.ingest(denied, [self.record("hidden", text="비밀 검색")])
        self.assertEqual(self.store.graph(projects=["two"])["nodes"], [])
        self.assertEqual(self.store.search("검색", projects=["two"]), [])
        self.assertEqual(self.store.events(projects=["two"])["items"], [])
        self.assertEqual(len(self.store.search("검색", projects=["one"])), 1)

    def test_busy_target_does_not_block_another_target(self):
        a, b = self.target(), self.target("channel-B")
        with self.store.target_lock(a):
            with self.assertRaises(TargetBusy):
                self.store.ingest(a, [self.record()])
            self.store.ingest(b, [self.record()])

    def test_unknown_relation_endpoint_rolls_back_the_batch(self):
        target = self.target()
        with self.assertRaisesRegex(ValueError, "collection_orphan_relation"):
            self.store.ingest(target, [self.record()], relations=[{"source_platform": "youtube", "source_id": "AbC", "target_platform": "youtube", "target_id": "missing", "type": "refers-to"}])
        self.assertEqual(self.store.graph(projects=["one"])["nodes"], [])


if __name__ == "__main__":
    unittest.main()
