"""Regression contract for Korean literal search in an isolated CollectionStore.

Text matches are retrieval candidates, not verified facts or person identities.
No existing collection, model, application, or external source is opened.
"""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alden_collection import CollectionStore, digest, encoded, identity


class KoreanSearchRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="alden-korean-search-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = CollectionStore(self.root)

    def target(self, name="main", *, project="one", platform="graph", permission="local-private"):
        return self.store.register(
            platform=platform, original_id=name, kind="source", label=name,
            projects=[project], permission=permission,
        )

    @staticmethod
    def record(name, text, *, platform="graph", kind="note", label="자료", raw=None):
        return {
            "platform": platform, "original_id": name, "label": label,
            "text": text, "kind": kind,
            "raw": raw if raw is not None else {"original_id": name, "original_text": text},
        }

    def search_ids(self, query, *, project="one", **filters):
        return [row["id"] for row in self.store.search(query, projects=[project], **filters)]

    def graph_ids(self, query, *, project="one", **filters):
        return [node["id"] for node in self.store.graph_page(projects=[project], query=query, **filters)["nodes"]]

    def test_attached_years_two_character_hangul_and_quoted_literals(self):
        target = self.target()
        self.store.ingest(target, [
            self.record("year-35", "이자리35년336"),
            self.record("year-49", "두번째올해49년814"),
        ])
        cases = (
            ("35년", "year-35"), ('"35년"', "year-35"),
            ("49년", "year-49"), ('"49년"', "year-49"),
            ("자리", "year-35"), ("올해", "year-49"),
        )
        for query, name in cases:
            expected = [identity("graph", name)]
            with self.subTest(query=query, api="search"):
                self.assertEqual(self.search_ids(query), expected)
            with self.subTest(query=query, api="graph_page"):
                self.assertEqual(self.graph_ids(query), expected)

    def test_original_fts_rank_precedes_fallback_and_graph_pages_are_stable(self):
        target = self.target()
        self.store.ingest(target, [
            self.record("fts-short", "35년"),
            self.record("fts-long", "이 기록은 35년 동안 보관한 자료입니다"),
            self.record("literal-a", "이자리35년336"),
            self.record("literal-b", "두번째35년814"),
        ])
        # Derive the exact FTS order independently of the proposed fallback.
        with CollectionStore.open_existing(self.root).database() as db:
            fts_ids = [row[0] for row in db.execute(
                "SELECT document_id FROM version_search WHERE version_search MATCH ? "
                "ORDER BY bm25(version_search),document_id", ("35년",)
            )]
        self.assertEqual(set(fts_ids), {identity("graph", "fts-short"), identity("graph", "fts-long")})

        expected_all = {identity("graph", name) for name in (
            "fts-short", "fts-long", "literal-a", "literal-b"
        )}
        ranked = self.search_ids("35년", limit=4)
        self.assertEqual(len(ranked), 4)
        self.assertEqual(set(ranked), expected_all)
        self.assertEqual(ranked[:len(fts_ids)], fts_ids)
        self.assertEqual(self.search_ids("35년", limit=2), fts_ids)
        self.assertEqual(self.search_ids("35년", limit=4), ranked)

        page_ids = []
        for offset in range(4):
            with self.subTest(offset=offset):
                page = self.store.graph_page(projects=["one"], query="35년", limit=1, offset=offset)
                ids = [node["id"] for node in page["nodes"]]
                self.assertEqual(len(ids), 1)
                self.assertEqual(page["next"], offset + 1 if offset < 3 else None)
                self.assertEqual(ids, self.graph_ids("35년", limit=1, offset=offset))
                page_ids.extend(ids)
        self.assertEqual(set(page_ids), expected_all)
        self.assertEqual(len(set(page_ids)), 4)
        self.assertEqual(page_ids[:len(fts_ids)], fts_ids)

    def test_boolean_or_and_support_bare_and_quoted_korean_literals(self):
        target = self.target()
        self.store.ingest(target, [
            self.record("only-35", "이자리35년336"),
            self.record("only-49", "두번째올해49년814"),
            self.record("both", "이자리35년336 그리고 두번째올해49년814"),
            self.record("unrelated", "기록이 존재합니다"),
        ])
        both = {identity("graph", "both")}
        either = both | {identity("graph", "only-35"), identity("graph", "only-49")}
        for query, expected in (
            ("35년 AND 49년", both), ('"35년" AND "49년"', both),
            ("35년 OR 49년", either), ('"35년" OR "49년"', either),
        ):
            with self.subTest(query=query, api="search"):
                self.assertEqual(set(self.search_ids(query)), expected)
            with self.subTest(query=query, api="graph_page"):
                self.assertEqual(set(self.graph_ids(query)), expected)

    def test_advanced_fts_not_column_prefix_and_near_are_not_expanded(self):
        target = self.target()
        self.store.ingest(target, [
            self.record("exact", "35년 공개"),
            self.record("excluded", "35년 제외"),
            self.record("prefix", "35년추가 공개"),
            self.record("attached", "이자리35년336 공개"),
            self.record("label-only", "본문은이자리35년336", label="35년"),
        ])
        cases = (
            ("35년 NOT 제외", {"exact", "label-only"}),
            ("body:35년", {"exact", "excluded"}),
            ("35년*", {"exact", "excluded", "prefix", "label-only"}),
            ("NEAR(35년 공개, 2)", {"exact"}),
        )
        for query, names in cases:
            with self.subTest(query=query):
                self.assertEqual(set(self.search_ids(query)), {identity("graph", name) for name in names})

    def test_literal_bounds_and_korean_requirement(self):
        target = self.target()
        self.store.ingest(target, [
            self.record("numeric", "이자리35년336"),
            self.record("length", "앞" + "가" * 65 + "뒤"),
            self.record("latin", "prefixABCtoken")
        ])
        for query in ("년", "35", "ABC", "가" * 65):
            with self.subTest(query=query, api="search"):
                self.assertEqual(self.search_ids(query), [])
            with self.subTest(query=query, api="graph_page"):
                self.assertEqual(self.graph_ids(query), [])
        expected = [identity("graph", "length")]
        self.assertEqual(self.search_ids("가" * 64), expected)
        self.assertEqual(self.graph_ids("가" * 64), expected)

    def test_denied_foreign_latest_version_and_deleted_document_are_excluded(self):
        allowed = self.target("allowed")
        foreign = self.target("foreign", project="two")
        denied = self.target("denied", permission="denied")
        removed = self.target("removed")
        self.store.ingest(allowed, [self.record("shared", "이자리35년336 공개")])
        self.store.ingest(foreign, [self.record("shared", "두번째올해49년814 다른프로젝트")])
        self.store.ingest(denied, [self.record("hidden", "이자리35년336 비공개")])
        removed_record = self.record("deleted", "이자리35년336 삭제대상")
        self.store.ingest(removed, [removed_record], complete_snapshot=True,
                          source_revision=digest(encoded([removed_record])), source_order=1)
        self.store.ingest(removed, [], complete_snapshot=True,
                          source_revision=digest(encoded([])), source_order=2)

        shared = identity("graph", "shared")
        with CollectionStore.open_existing(self.root).database() as db:
            rows = db.execute("SELECT target_id,current_version FROM memberships WHERE document_id=?",
                              (shared,)).fetchall()
            by_target = dict(rows)
            global_version = db.execute("SELECT current_version FROM documents WHERE id=?", (shared,)).fetchone()[0]
            self.assertNotEqual(by_target[allowed], by_target[foreign])
            self.assertEqual(global_version, by_target[foreign])
            self.assertEqual(db.execute("SELECT availability FROM documents WHERE id=?",
                                        (identity("graph", "deleted"),)).fetchone()[0], "deleted")

        self.assertEqual(self.search_ids("35년"), [shared])
        self.assertEqual(self.search_ids("49년"), [])
        self.assertEqual(self.graph_ids("35년"), [shared])
        self.assertEqual(self.graph_ids("49년"), [])
        self.assertEqual(self.search_ids("49년", project="two"), [shared])
        self.assertEqual(self.graph_ids("49년", project="two"), [shared])
        self.assertEqual(self.store.search("35년", projects=["one"])[0]["body"], "이자리35년336 공개")
        node = self.store.graph_page(projects=["one"], query="35년")["nodes"][0]
        self.assertEqual(node["source_target"], allowed)
        self.assertEqual(node["source_version"], by_target[allowed])
        self.assertEqual(node["description"], "이자리35년336 공개")
        self.assertEqual(self.search_ids("35년", time_from=10**10), [])

    def test_graph_literal_respects_target_platform_type_relation_and_time(self):
        main = self.target("graph-source", platform="graph")
        thread = self.target("thread-source", platform="threads")
        self.store.ingest(main, [
            self.record("anchor", "두번째올해49년814", kind="topic"),
            self.record("match", "이자리35년336", kind="topic"),
            self.record("video", "이자리35년336", kind="video"),
        ], relations=[
            {"source_platform": "graph", "source_id": "anchor", "target_platform": "graph",
             "target_id": "match", "type": "supports"},
            {"source_platform": "graph", "source_id": "match", "target_platform": "graph",
             "target_id": "video", "type": "refers-to"},
        ])
        self.store.ingest(thread, [self.record("thread-35", "이자리35년336", platform="threads", kind="topic")])

        # A relation filter chooses eligible graph nodes before text matching.
        page = self.store.graph_page(projects=["one"], query="35년", target_id=main,
                                     platform="graph", node_type="topic", relation="supports")
        self.assertEqual([n["id"] for n in page["nodes"]], [identity("graph", "match")])
        self.assertEqual((page["total_nodes"], page["total_edges"]), (2, 1))
        self.assertEqual(self.graph_ids("35년", target_id=main, node_type="video", relation="supports"), [])

        with CollectionStore.open_existing(self.root).database() as db:
            timestamp = db.execute("SELECT v.collected_at FROM versions v JOIN memberships m "
                                   "ON m.current_version=v.id WHERE m.target_id=?", (thread,)).fetchone()[0]
        self.assertEqual(self.graph_ids("35년", target_id=thread, platform="threads",
                                        since=max(0, timestamp - 1), until=timestamp + 1),
                         [identity("threads", "thread-35")])
        self.assertEqual(self.graph_ids("35년", platform="threads", until=timestamp), [])

    def persisted_source_state(self):
        with CollectionStore.open_existing(self.root).database() as db:
            state = {
                name: [tuple(row) for row in db.execute("SELECT * FROM " + name + " ORDER BY 1,2")]
                for name in ("documents", "versions", "memberships")
            }
            state["fts_counts"] = tuple(db.execute("SELECT count(*) FROM " + name).fetchone()[0]
                                        for name in ("document_search", "version_search"))
            sources = [row[0] for row in db.execute("SELECT raw_path FROM versions ORDER BY id")]
        state["raw_bytes"] = {name: (self.store.blobs / name).read_bytes() for name in sources}
        return state

    def test_queries_preserve_body_raw_bytes_ids_versions_and_fts_counts(self):
        target = self.target()
        text = "메모:\t\t이자리35년336  \n  두번째올해49년814  "
        raw = {"original_id": "UnchangedCaseID", "text": text, "spaces": "  "}
        self.store.ingest(target, [self.record("UnchangedCaseID", text, raw=raw)])
        with CollectionStore.open_existing(self.root).database() as db:
            document = db.execute("SELECT id,current_version FROM documents").fetchone()
            body = db.execute("SELECT body FROM versions").fetchone()[0]
        self.assertEqual(document[0], identity("graph", "UnchangedCaseID"))
        self.assertEqual(body, text)
        baseline = self.persisted_source_state()
        self.assertEqual(next(iter(baseline["raw_bytes"].values())), encoded(raw))

        for query in ("35년", "49년", '"35년"', "35년 OR 49년", "올해"):
            self.store.search(query, projects=["one"])
            self.store.graph_page(projects=["one"], query=query, limit=1)
        self.assertEqual(self.persisted_source_state(), baseline)


if __name__ == "__main__":
    unittest.main()
