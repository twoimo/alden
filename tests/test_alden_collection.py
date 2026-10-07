"""Collection crash, identity, lineage and permission contracts in private stores."""
import json
import sqlite3
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_collection import CollectionStore, TargetBusy, identity, read_action


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

    def test_project_versions_do_not_leak_a_different_target_revision(self):
        allowed = self.target()
        restricted = self.target("channel-B", "two", permission="denied")
        self.store.ingest(allowed, [self.record(text="공개 회의", label="공개 제목")])
        self.store.ingest(restricted, [self.record(text="private_secret", label="숨은 제목")])
        page = self.store.graph_page(projects=["one"])
        self.assertEqual(page["nodes"][0]["label"], "공개 제목")
        self.assertEqual(page["nodes"][0]["description"], "공개 회의")
        self.assertEqual(self.store.graph(projects=["one"])["nodes"][0]["label"], "공개 제목")
        self.assertEqual(self.store.search("private_secret", projects=["one"]), [])
        self.assertEqual(self.store.search("회의", projects=["one"])[0]["body"], "공개 회의")
        self.assertFalse(any(row["target_id"] == restricted for row in self.store.recent_events()["items"]))
        replay = self.store.ingest(allowed, [self.record(text="공개 회의", label="공개 제목")])
        self.assertEqual(replay["unchanged"], 1)
        self.assertEqual(replay["revised"], 0)

    def test_schema_one_migration_keeps_each_target_version_and_recovery_backup(self):
        a, b = self.target(), self.target("channel-B", "two")
        self.store.ingest(a, [self.record(label="원본 A")])
        self.store.ingest(b, [self.record(label="원본 B")])
        with self.store.database() as db:
            db.execute("DROP TABLE version_search")
            db.execute("ALTER TABLE memberships DROP COLUMN current_version")
            db.execute("UPDATE meta SET value='1' WHERE key='schema'")
        with self.assertRaisesRegex(RuntimeError, "collection_schema_requires_migration"):
            CollectionStore.open_existing(self.store.root.parent.parent)
        migrated = CollectionStore(self.store.root.parent.parent)
        self.assertEqual(migrated.graph_page(projects=["one"])["nodes"][0]["label"], "원본 A")
        self.assertEqual(migrated.graph_page(projects=["two"])["nodes"][0]["label"], "원본 B")
        backups = list(migrated.root.glob("collection.schema-1-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "1")
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 2)

    def test_bounded_graph_pages_focus_and_search_preserve_scope(self):
        target = self.target()
        hidden = self.target("channel-B", "two", permission="denied")
        records = [self.record(str(i), text="검색 자료") for i in range(32)]
        relations = [{"source_platform": "youtube", "source_id": "0", "target_platform": "youtube",
                      "target_id": str(i), "type": "refers-to"} for i in range(1, 32)]
        self.store.ingest(target, records, relations=relations)
        self.store.ingest(hidden, [self.record("hidden")])
        first = self.store.graph_page(projects=["one"], limit=10)
        second = self.store.graph_page(projects=["one"], limit=10, offset=first["next"])
        self.assertEqual(first["total_nodes"], 32)
        self.assertEqual(first["total_edges"], 31)
        self.assertFalse({n["id"] for n in first["nodes"]} & {n["id"] for n in second["nodes"]})
        focused = self.store.graph_page(projects=["one"], focus=identity("youtube", "0"), hops=3)
        self.assertEqual(len(focused["nodes"]), 24)
        self.assertTrue(all(e["source"] in {n["id"] for n in focused["nodes"]} and e["target"] in {n["id"] for n in focused["nodes"]} for e in focused["edges"]))
        with self.assertRaisesRegex(ValueError, "collection_graph_focus_not_in_scope"):
            self.store.graph_page(projects=["one"], focus=identity("youtube", "hidden"))
        a = self.store.graph_page(projects=["one"], query="검색", limit=5)
        b = self.store.graph_page(projects=["one"], query="검색", limit=5, offset=a["next"])
        self.assertFalse({n["id"] for n in a["nodes"]} & {n["id"] for n in b["nodes"]})
        with self.assertRaises(ValueError):
            self.store.graph_page(projects=["one"], limit=121)

    def test_interrupted_schema_migration_rolls_back_and_can_resume(self):
        target = self.target()
        self.store.ingest(target, [self.record()])
        root = self.store.root.parent.parent
        with self.store.database() as db:
            db.execute("DROP TABLE version_search")
            db.execute("ALTER TABLE memberships DROP COLUMN current_version")
            db.execute("UPDATE meta SET value='1' WHERE key='schema'")
        original_connect = sqlite3.connect

        class Interrupted(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith("UPDATE memberships SET"):
                    raise RuntimeError("test_process_interrupted")
                return super().execute(sql, *args, **kwargs)

        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=Interrupted)

        with patch("alden_collection.sqlite3.connect", side_effect=connect):
            with self.assertRaisesRegex(RuntimeError, "test_process_interrupted"):
                CollectionStore(root)
        with original_connect(self.store.path) as db:
            self.assertNotIn("current_version", {r[1] for r in db.execute("PRAGMA table_info(memberships)")})
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "1")
        recovered = CollectionStore(root)
        self.assertEqual(recovered.search("3층", projects=["one"])[0]["body"], "회의는 3층")

    def test_history_forward_reconnect_recovers_more_than_one_page_without_duplicates(self):
        target = self.target()
        self.store.ingest(target, [self.record(str(i)) for i in range(25)])
        recent = self.store.recent_events(projects=["one"], limit=5)
        cursor = max(row["sequence"] for row in recent["items"])
        older = self.store.recent_events(projects=["one"], before=recent["next"], limit=5)
        self.assertFalse({r["sequence"] for r in recent["items"]} & {r["sequence"] for r in older["items"]})
        self.store.ingest(target, [self.record("new" + str(i)) for i in range(25)])
        observed = []
        while True:
            page = self.store.recent_events(projects=["one"], after=cursor, limit=50)
            observed += [r["sequence"] for r in page["items"]]
            cursor = page["cursor"]
            if page["next"] is None:
                break
        self.assertEqual(len(observed), 125)
        self.assertEqual(len(set(observed)), 125)
        self.assertEqual(observed, sorted(observed))

    def test_history_source_target_time_filters_and_empty_reader_do_not_write(self):
        target = self.target()
        other = self.target("channel-B", "two")
        self.store.ingest(target, [self.record(url="https://www.youtube.com/watch?v=AbC")])
        self.store.ingest(other, [self.record("other")])
        with self.store.database() as db:
            db.execute("UPDATE events SET at=100 WHERE target_id=?", (target,))
            db.execute("UPDATE events SET at=200 WHERE target_id=?", (other,))
        page = self.store.recent_events(projects=["one", "two"], since=90, until=150,
                                        target_id=target, platform="youtube", stage="stored", query="제목")
        self.assertEqual(len(page["items"]), 1)
        self.assertIn("watch?v=AbC", page["items"][0]["source_url"])
        root = self.store.root.parent.parent
        reader = CollectionStore.open_existing(root)
        with reader.database() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM events")
        missing = root / "missing"
        self.assertEqual(read_action(missing, "collection-history")["state"], "not_configured")
        self.assertFalse(missing.exists())
        with self.assertRaises(ValueError):
            read_action(root, "collection-history", '{"projects":["denied"]}')
        for options in ({"limit": True}, {"before": 0}, {"after": 0, "before": 1},
                        {"since": float("nan")}, {"since": 10, "until": 1}, {"stage": "complete"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.store.recent_events(**options)

    def test_cancelled_batch_keeps_checkpoint_and_has_a_distinct_paused_receipt(self):
        target = self.target()
        self.store.ingest(target, [self.record()], cursor="before")
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 2
        with self.assertRaisesRegex(RuntimeError, "collection_cancelled"):
            self.store.ingest(target, [self.record("new1"), self.record("new2")], cursor="after", cancelled=cancelled)
        self.assertEqual(json.loads(self.store.target(target)["cursor"]), "before")
        self.assertEqual(len(self.store.graph(projects=["one"])["nodes"]), 1)
        self.assertEqual(self.store.recent_events(projects=["one"])["items"][0]["stage"], "paused")
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT state FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()[0], "paused")


if __name__ == "__main__":
    unittest.main()
