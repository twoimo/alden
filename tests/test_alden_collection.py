"""Collection crash, identity, lineage and permission contracts in private stores."""
import json
import sqlite3
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_collection import CollectionStore, TargetBusy, identity, read_action
from alden_collect import capture_source, collect_target


class CollectionTests(unittest.TestCase):
    def test_overview_density_is_explicit_bounded_and_keeps_scope_and_detail_budgets(self):
        allowed, denied = self.target(), self.target("denied", "other")
        self.store.ingest(allowed, [self.record(str(i)) for i in range(160)])
        self.store.ingest(denied, [self.record("private")])
        with self.assertRaises(ValueError):
            self.store.graph_page(projects=["one"], limit=1984)
        result = self.store.graph_page(projects=["one"], limit=1984, overview=True)
        self.assertEqual(len(result["nodes"]), 160)
        self.assertEqual(result["total_nodes"], 160)
        self.assertTrue(all(node["source_target"] == allowed for node in result["nodes"]))
        with self.assertRaises(ValueError):
            self.store.graph_page(projects=["one"], limit=1985, overview=True)
        with self.assertRaises(ValueError):
            self.store.graph_page(projects=["one"], limit=1984, overview=True, focus=result["nodes"][0]["id"])

    def test_first_target_change_after_other_target_stages_is_not_a_pruning_reset(self):
        allowed, other = self.target(), self.target("elsewhere", "other")
        self.store.ingest(other, [self.record("unrelated")])
        baseline = self.store.activity_page(projects=["one"], target_id=allowed)
        self.assertEqual(baseline["cursor"], 0)
        self.store.ingest(other, [self.record("unrelated", text="other change")])
        self.store.ingest(allowed, [self.record()])
        page = self.store.activity_page(projects=["one"], target_id=allowed,
                                        after=0, stream_id=baseline["stream_id"])
        self.assertFalse(page["reset"])
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["kind"], "added")

    def test_graph_and_activity_share_target_scoped_snapshot_checkpoint(self):
        a, b = self.target(), self.target("channel-B")
        self.store.ingest(a, [self.record()])
        self.store.ingest(b, [self.record("other")])
        for projects, options in [(["one"], {}), (["one"], {"target_id": a}), ([], {})]:
            graph = self.store.graph_page(projects=projects, **options)
            baseline = self.store.activity_page(projects=projects, **options)
            self.assertEqual(graph["activity_checkpoint"], {key: baseline[key] for key in ("cursor", "stream_id")})
        empty = self.store.graph_page(projects=["one"], target_id=a, query="nonexistent")
        self.assertIn("activity_checkpoint", empty)
        self.assertEqual(empty["nodes"], [])

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

    def test_batch_search_preserves_unrelated_rows_and_last_duplicate_body(self):
        target=self.target()
        self.store.ingest(target,[self.record('keep',text='unrelated'),self.record('edit',text='before')])
        self.store.ingest(target,[self.record('edit',text='intermediate'),self.record('new',text='inserted'),self.record('edit',text='final')])
        with self.store.database() as db:
            rows=db.execute('SELECT document_id,body FROM document_search ORDER BY document_id').fetchall()
        self.assertEqual(dict(rows),{identity('youtube','keep'):'unrelated',identity('youtube','edit'):'final',identity('youtube','new'):'inserted'})
        self.assertEqual(len(rows),3)
        self.assertEqual(self.store.search('final',projects=['one'])[0]['body'],'final')

    def test_failed_later_relation_rolls_back_batched_search_projection(self):
        target=self.target();self.store.ingest(target,[self.record(text='before')],cursor='before')
        relation={'source_platform':'youtube','source_id':'AbC','target_platform':'youtube','target_id':'missing','type':'reference'}
        with self.assertRaisesRegex(ValueError,'orphan_relation'):
            self.store.ingest(target,[self.record(text='after')],relations=[relation],cursor='after')
        self.assertEqual(json.loads(self.store.target(target)['cursor']),'before')
        self.assertEqual(self.store.search('before',projects=['one'])[0]['body'],'before')
        self.assertEqual(self.store.search('after',projects=['one']),[])

    def test_permissions_filter_graph_search_and_history(self):
        allowed = self.target()
        denied = self.target("channel-B", "two", permission="denied")
        self.store.ingest(allowed, [self.record(text="자료 검색")])
        self.store.ingest(denied, [self.record("hidden", text="비밀 검색")])
        self.assertEqual(self.store.graph(projects=["two"])["nodes"], [])
        self.assertEqual(self.store.search("검색", projects=["two"]), [])
        self.assertEqual(self.store.events(projects=["two"])["items"], [])
        self.assertEqual(len(self.store.search("검색", projects=["one"])), 1)

    def test_revision_history_uses_previous_version_from_its_own_target_only(self):
        primary=self.target()
        secondary=self.target('channel-B','one')
        self.store.ingest(primary,[self.record('shared',text='target A revision one')])
        before=self.store.graph_page(projects=['one'],target_id=primary)['nodes'][0]['source_version']
        self.store.ingest(secondary,[self.record('shared',text='target B different revision')])
        foreign=self.store.graph_page(projects=['one'],target_id=secondary)['nodes'][0]['source_version']
        self.assertNotEqual(before,foreign)
        self.store.ingest(primary,[self.record('shared',text='target A revision two')])
        after=self.store.graph_page(projects=['one'],target_id=primary)['nodes'][0]['source_version']
        history=self.store.recent_events(projects=['one'],target_id=primary,stage='stored')
        recent=next(row for row in history['items'] if row['version']==after)
        self.assertEqual(recent['details']['change'],'revised')
        self.assertEqual(recent['details']['previous_version'],before)
        self.assertNotEqual(recent['details']['previous_version'],foreign)
        secondary_history=self.store.recent_events(projects=['one'],target_id=secondary,stage='stored')
        self.assertNotIn('previous_version',secondary_history['items'][0]['details'])
        with self.store.database() as db:
            self.assertGreaterEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],3)

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

    def test_graph_filters_choose_the_selected_targets_version_and_real_degree(self):
        a, b = self.target(), self.target("channel-B", "one")
        self.store.ingest(a, [self.record("root", label="목표 A", kind="topic", source_url="https://example.test/a"),
                              self.record("leaf", kind="topic"), self.record("other", kind="video")],
                          relations=[{"source_platform": "youtube", "source_id": "root", "target_platform": "youtube", "target_id": "leaf", "type": relation} for relation in ["refers-to", "supports"]])
        self.store.ingest(b, [self.record("root", label="목표 B", source_url="https://example.test/b")])
        page = self.store.graph_page(projects=["one"], target_id=a, node_type="topic", relation="supports")
        self.assertEqual(page["total_nodes"], 2)
        self.assertEqual(page["total_edges"], 1)
        root = next(n for n in page["nodes"] if n["id"] == identity("youtube", "root"))
        self.assertEqual(root["label"], "목표 A")
        self.assertEqual(root["source_url"], "https://example.test/a")
        self.assertEqual(root["degree"], 1)
        self.assertEqual(root["space"], "one")
        self.assertEqual(self.store.graph_page(projects=["one"], platform="threads")["total_nodes"], 0)
        self.assertEqual(self.store.graph_page(projects=["one"], since=10**10)["total_nodes"], 0)
        self.assertEqual(self.store.graph_page(projects=["one"], node_type="missing")["nodes"], [])
        unfiltered = self.store.graph_page(projects=["one"], target_id=a)
        self.assertEqual(next(n for n in unfiltered["nodes"] if n["id"] == root["id"])["degree"], 1)

    def test_detail_reads_retained_original_instead_of_internal_json_for_old_graph_versions(self):
        target = self.store.register(platform="graph", original_id="retained", kind="graph", label="원본", projects=["one"])
        record = {"original_id": "source-a", "label": "", "text": "", "raw":
                  {"localOriginalText": "채용 공고의 원문\n경험을 구체적으로 적습니다.", "handle": "@original", "date": "2026-10-01"}}
        self.store.ingest(target, [record])
        node = self.store.graph_page(projects=["one"])["nodes"][0]
        detail = self.store.graph_page(projects=["one"], focus=node["id"], hops=0,
                                        details=True, expected_version=node["source_version"])["details"]
        self.assertEqual(node["label"], "source-a")
        self.assertEqual(detail["body"], record["raw"]["localOriginalText"])
        self.assertEqual(detail["body_format"], "retained_original_text")
        self.assertIn("@original", detail["display_label"])
        self.assertEqual(detail["version"], node["source_version"])

    def test_graph_details_verify_record_hash_and_fence_old_selected_versions(self):
        target = self.target()
        self.store.ingest(target, [self.record(source_url="https://example.test/source")])
        node = self.store.graph_page(projects=["one"])["nodes"][0]
        result = read_action(self.store.root.parent.parent, "collection-graph", json.dumps({"projects": ["one"],
            "focus": node["id"], "hops": 0, "details": True, "expected_version": node["source_version"]}))
        self.assertEqual(result["details"]["body"], "회의는 3층")
        self.assertEqual(result["details"]["basis"], "source_record")
        self.assertIsNone(result["details"]["capture"])
        self.store.ingest(target, [self.record(text="개정 내용")])
        stale = self.store.graph_page(projects=["one"], focus=node["id"], details=True, expected_version=node["source_version"])
        self.assertEqual(stale, {"ok": False, "error": "collection_graph_version_changed"})
        with self.store.database() as db:
            path = db.execute("SELECT raw_path FROM versions JOIN memberships ON versions.id=memberships.current_version").fetchone()[0]
        (self.store.blobs / path).write_text("corrupted")
        with self.assertRaisesRegex(RuntimeError, "collection_source_integrity"):
            self.store.graph_page(projects=["one"], focus=node["id"], details=True)

    def test_graph_filter_validation_and_denied_detail_scope(self):
        hidden = self.target(permission="denied")
        self.store.ingest(hidden, [self.record()])
        for options in [{"platform": "unknown"}, {"node_type": []}, {"since": float("nan")},
                        {"since": 2, "until": 1}, {"details": "true"}, {"hops": True}]:
            with self.assertRaises(ValueError):
                self.store.graph_page(projects=["one"], **options)
        with self.assertRaisesRegex(ValueError, "collection_graph_focus_not_in_scope"):
            self.store.graph_page(projects=["one"], focus=identity("youtube", "AbC"), details=True)

    def test_focus_bfs_handles_cycles_parallel_edges_and_exact_hop_boundaries(self):
        target = self.target()
        pairs = [("a", "b"), ("b", "c"), ("c", "a"), ("a", "d"), ("d", "e"), ("a", "a")]
        edges = [{"source_platform": "youtube", "source_id": a, "target_platform": "youtube",
                  "target_id": b, "type": "refers-to"} for a, b in pairs]
        edges.append({**edges[0], "type": "supports"})
        self.store.ingest(target, [self.record(letter) for letter in "abcde"], relations=edges)
        for hops, expected in [(0, "a"), (1, "abcd"), (2, "abcde"), (3, "abcde")]:
            page = self.store.graph_page(projects=["one"], focus=identity("youtube", "a"), hops=hops)
            self.assertEqual({n["id"] for n in page["nodes"]}, {identity("youtube", letter) for letter in expected})
            root = next(n for n in page["nodes"] if n["id"] == identity("youtube", "a"))
            self.assertEqual(root["degree"], 3)
            self.assertTrue(all(e["source"] in {n["id"] for n in page["nodes"]} and e["target"] in {n["id"] for n in page["nodes"]} for e in page["edges"]))

    def test_activity_baseline_and_committed_changes_keep_origin_and_version(self):
        target = self.target()
        self.store.ingest(target, [self.record()], origin="first-collector")
        baseline = self.store.activity_page(projects=["one"])
        self.assertTrue(baseline["reset"])
        self.assertEqual(baseline["items"], [])
        self.store.ingest(target, [self.record(text="개정 내용")], origin="revision-collector")
        page = self.store.activity_page(projects=["one"], after=baseline["cursor"], stream_id=baseline["stream_id"])
        self.assertFalse(page["reset"])
        self.assertEqual(len(page["items"]), 1)
        item = page["items"][0]
        self.assertEqual(item["document_id"], identity("youtube", "AbC"))
        self.assertEqual(item["kind"], "revised")
        self.assertEqual(item["origin"], "revision-collector")
        self.assertTrue(item["success"])
        self.assertEqual(item["version"], self.store.graph_page(projects=["one"])["nodes"][0]["source_version"])
        self.assertEqual(self.store.activity_page(projects=["one"], after=page["cursor"], stream_id=page["stream_id"])["items"], [])

    def test_first_commit_in_empty_journal_keeps_baseline_and_survives_reopen(self):
        target = self.target()
        root = self.store.root.parent.parent
        baseline = self.store.graph_page(projects=["one"])["activity_checkpoint"]
        self.assertEqual(baseline["cursor"], 0)
        self.store.ingest(target, [self.record()], origin="first-host")
        reader = CollectionStore.open_existing(root)
        page = reader.activity_page(projects=["one"], after=0, stream_id=baseline["stream_id"])
        self.assertFalse(page["reset"])
        self.assertEqual(page["stream_id"], baseline["stream_id"])
        self.assertEqual([(x["kind"], x["origin"]) for x in page["items"]], [("added", "first-host")])

    def test_legacy_journal_identity_is_backed_up_only_on_writer_open(self):
        target = self.target()
        self.store.ingest(target, [self.record()])
        root = self.store.root.parent.parent
        with self.store.database() as db:
            db.execute("DELETE FROM meta WHERE key IN ('journal_id','journal_first_event')")
        before = self.store.path.read_bytes()
        reader = CollectionStore.open_existing(root)
        baseline = reader.activity_page(projects=["one"])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.root.glob('collection.schema-*.sqlite3')), [])
        writer = CollectionStore(root)
        recovery, = writer.root.glob('collection.schema-*.sqlite3')
        self.assertEqual(recovery.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(recovery) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertIsNone(db.execute("SELECT value FROM meta WHERE key='journal_id'").fetchone())
        self.assertNotEqual(writer.activity_page(projects=["one"])["stream_id"], baseline["stream_id"])
        CollectionStore(root)
        self.assertEqual(len(list(writer.root.glob('collection.schema-*.sqlite3'))), 1)

    def test_history_counts_apply_stage_time_source_and_empty_scope_filters(self):
        target = self.target()
        self.store.ingest(target, [self.record()], run_id='first')
        self.store.ingest(target, [self.record()], run_id='repeat')
        self.store.ingest(target, [self.record(text='개정')], run_id='revision')
        self.assertEqual(self.store.recent_events(projects=['one'], stage='stored')['summary']['total'], 3)
        self.assertEqual(self.store.recent_events(projects=['one'], stage='stored')['summary']['changes'],
                         dict(added=1, revised=1, unchanged=1, removed=0, relations_changed=0))
        for options in [dict(projects=[]), dict(projects=['one'], platform='files'),
                        dict(projects=['one'], since=time.time()+60), dict(projects=['one'], query='no-match')]:
            page = self.store.recent_events(**options)
            self.assertEqual(page['items'], [])
            self.assertEqual(page['summary']['total'], 0)

    def test_journal_counts_follow_replay_rollback_edits_and_retention(self):
        target = self.target()
        self.store.ingest(target, [self.record()], run_id='once')
        before = self.store.recent_events(projects=['one'])['summary']
        self.store.ingest(target, [self.record()], run_id='once')
        self.assertEqual(self.store.recent_events(projects=['one'])['summary'], before)
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with self.store.database() as db:
                self.store._event(db, 'once', target, 'failed', reason='test-only')
                raise RuntimeError('interrupted')
        self.assertEqual(self.store.recent_events(projects=['one'])['summary'], before)
        with self.store.database() as db:
            db.execute("UPDATE events SET details=? WHERE stage='stored'", (json.dumps({'change': 'revised'}),))
            db.execute("DELETE FROM events WHERE stage='parsed'")
        result = self.store.recent_events(projects=['one'])
        self.assertEqual(result['summary']['total'], 4)
        self.assertEqual(result['summary']['changes']['added'], 0)
        self.assertEqual(result['summary']['changes']['revised'], 1)
        self.assertEqual(result['summary']['stages']['parsed'], 0)

    def test_history_replacement_resets_numeric_cursor_and_counts_only_scoped_changes(self):
        target = self.target()
        hidden = self.target("hidden", "other")
        self.store.ingest(target, [self.record()], origin="host-one", run_id="first")
        self.store.ingest(target, [self.record()], origin="host-two", run_id="repeat")
        self.store.ingest(hidden, [self.record("secret")], run_id="hidden")
        self.store.register(platform="youtube", original_id="channel-A", kind="channel",
                            label="동명이인", projects=["one", "second"])
        baseline = self.store.recent_events(projects=["one"], limit=2)
        self.assertEqual(baseline["summary"]["total"], 10)
        self.assertEqual(baseline["summary"]["changes"]["added"], 1)
        self.assertEqual(baseline["summary"]["changes"]["unchanged"], 1)
        self.assertTrue(all(x["projects"] == ["one"] for x in baseline["items"]))
        self.assertEqual({x["origin"] for x in baseline["items"]}, {"host-two"})
        page = self.store.recent_events(projects=["one"], after=baseline["cursor"],
                                        stream_id="replaced-store", limit=2)
        self.assertTrue(page["reset"])
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["summary"], baseline["summary"])

    def test_activity_pages_advance_over_skipped_stages_and_never_emit_unchanged_or_denied(self):
        target, hidden = self.target(), self.target("hidden", "one", permission="denied")
        self.store.ingest(target, [self.record()])
        baseline = self.store.activity_page(projects=["one"])
        self.store.ingest(hidden, [self.record("secret")])
        self.store.ingest(target, [self.record()])
        after = baseline["cursor"]
        count = 0
        while True:
            page = self.store.activity_page(projects=["one"], after=after, stream_id=baseline["stream_id"], limit=2)
            self.assertEqual(page["items"], [])
            self.assertFalse(page["reset"])
            self.assertGreater(page["cursor"], after)
            after = page["cursor"];count += 1
            if not page["has_more"]:break
            self.assertLess(count, 5)
        self.assertEqual(count, 3)

    def test_activity_journal_replacement_and_rewind_require_snapshot_without_old_pulses(self):
        target = self.target();self.store.ingest(target, [self.record()])
        baseline = self.store.activity_page(projects=["one"])
        for options in [{"stream_id": "different-stream", "after": baseline["cursor"]},
                        {"stream_id": baseline["stream_id"], "after": baseline["cursor"]+10}]:
            page = self.store.activity_page(projects=["one"], **options)
            self.assertTrue(page["reset"])
            self.assertEqual(page["items"], [])
        with self.store.database() as db:db.execute("DELETE FROM events WHERE sequence<=2")
        pruned = self.store.activity_page(projects=["one"], after=0, stream_id=baseline["stream_id"])
        self.assertTrue(pruned["reset"])
        self.assertEqual(pruned["items"], [])
        for invalid in [{"after": True}, {"after": -1}, {"limit": 201}, {"stream_id": []}]:
            with self.assertRaises(ValueError):self.store.activity_page(projects=["one"], **invalid)

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

    def source_target(self):
        path = self.store.root.parent.parent / "source.json"
        raw = b'{\r\n  "nodes": [ {"id": "A/B", "label": "\\uC6D0\\uBB38", "summary": "  keep  spaces "} ],\r\n  "edges": []\r\n}\r\n'
        path.write_bytes(raw)
        target = self.store.register(platform="graph", original_id="public-source", kind="graph", label="source",
                                     projects=["one"], config={"adapter": "source-graph", "path": str(path)})
        collect_target(self.store, target)
        return target, path, raw

    def test_source_capture_preserves_exact_file_bytes_and_replays_record_pointer(self):
        target, path, raw = self.source_target()
        before = self.store.graph(projects=["one"])
        result = capture_source(self.store, target)
        folder = self.store.root / "source-captures"
        manifest = json.loads((folder / result["manifest"]).read_bytes())
        self.assertEqual((folder / manifest["source_file"]).read_bytes(), raw)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(before, self.store.graph(projects=["one"]))
        self.assertEqual(manifest["records"][0]["json_pointer"], "/nodes/0")
        self.assertEqual(manifest["records"][0]["version"], before["nodes"][0]["source_version"])
        cursor = json.loads(self.store.target(target)["cursor"])
        self.assertEqual(cursor["source_capture"]["sha256"], result["sha256"])
        with self.store.database() as db:
            counts = [db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ["runs", "events", "versions"]]
        replay = capture_source(self.store, target)
        self.assertEqual(replay["state"], "unchanged")
        with self.store.database() as db:
            self.assertEqual(counts, [db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ["runs", "events", "versions"]])

    def test_changed_source_capture_does_not_overwrite_projection_checkpoint(self):
        target, path, _ = self.source_target()
        before = self.store.target(target)["cursor"]
        path.write_text('{"nodes":[],"edges":[]}')
        with self.assertRaisesRegex(RuntimeError, "collection_source_revision_changed"):
            capture_source(self.store, target)
        self.assertEqual(self.store.target(target)["cursor"], before)
        self.assertFalse((self.store.root / "source-captures").exists())

    def test_source_capture_rejects_projection_mismatch_and_cancellation(self):
        target, _, _ = self.source_target()
        cursor = json.loads(self.store.target(target)["cursor"])
        self.store.ingest(target, [{"platform": "graph", "original_id": "public-source:node:A/B",
                                   "label": "changed", "text": "unrelated content"}], cursor=cursor)
        with self.assertRaisesRegex(RuntimeError, "collection_source_projection_changed"):
            capture_source(self.store, target)
        with self.assertRaisesRegex(RuntimeError, "collection_cancelled"):
            capture_source(self.store, target, cancelled=lambda: True)
        self.assertFalse((self.store.root / "source-captures").exists())

    def test_source_capture_checks_scope_before_opening_the_file(self):
        target, path, _ = self.source_target()
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (target,))
        path.unlink()
        with self.assertRaisesRegex(ValueError, "collection_source_scope_denied"):
            capture_source(self.store, target)

    def test_same_permitted_export_reuses_receipt_despite_changed_origin_snapshot_header(self):
        target, _, raw = self.source_target()
        first = capture_source(self.store, target)
        folder = self.store.root / "source-captures"
        manifest = json.loads((folder / first["manifest"]).read_text())
        with self.store.database() as db:before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        manifest["current_origin_snapshot_sha256"] = "changed-header-observation"
        replay = self.store.retain_source_capture(target, raw, manifest, expected_cursor=self.store.target(target)["cursor"])
        self.assertEqual(replay["state"], "unchanged")
        self.assertEqual(replay["manifest"], first["manifest"])
        with self.store.database() as db:self.assertEqual(before, db.execute("SELECT COUNT(*) FROM events").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
