"""Contract regressions for paged, source-verified annotation candidates.

Only synthetic CollectionStore records are used.  The candidate module is
intentionally imported as the production implementation: these tests must
not provide a replacement for a backend that has not been implemented yet.
"""

import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from alden_collection import CollectionStore, digest, identity
from alden_graph_quality import audit_annotations
from alden_annotation_candidates import create_review, read_review
import alden_annotation_candidates as candidates


class AnnotationCandidateContractTests(unittest.TestCase):
    PAGE_SIZE = 5
    PROJECT_SCOPE = {"scope": "project", "project": "one"}

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="alden-annotation-candidates-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.one = self.store.register(
            platform="graph", original_id="fixture-one", kind="source",
            label="fixture one", projects=["one"],
        )
        self.two = self.store.register(
            platform="graph", original_id="fixture-two", kind="source",
            label="fixture two", projects=["two"],
        )
        self.expected = {}

    @staticmethod
    def _record(name, body, topic, entity="기관공통"):
        return {
            "platform": "graph", "original_id": name,
            "label": "공통 제목", "text": body,
            "raw": {
                "localOriginalText": body,
                "topics": [topic],
                "entityTags": [entity],
                "fixtureMarker": "원본에만있는비공개표식",
            },
        }

    def _seed_groups(self, count=12, *, first_members=11):
        records = []
        for index in range(count):
            body = (
                f"비공개원문{index:02d}: 동일 투영의 증거를 확인합니다. "
                f"2026-10-14의 문장 {index}와 공백을 그대로 보존합니다."
            )
            names = sorted(
                (f"group-{index:02d}-source-{i:02d}"
                 for i in range(first_members if index == 0 else 2)),
                key=lambda name: identity("graph", name),
            )
            for position, name in enumerate(names):
                # In document-ID order the first TWO declare the same values.
                # The third (and subsequent) sources retain a different tag.
                same = position < (2 if index == 0 else 1)
                topic = f"비공개공통태그{index:02d}" if same else f"비공개다른태그{index:02d}"
                entity = "기관공통" if index != 0 or same else "기관다름"
                record = self._record(name, body, topic, entity)
                records.append(record)
                self.expected[identity("graph", name)] = {
                    "name": name, "body": body, "topic": topic, "entity": entity,
                }
        self.store.ingest(self.one, records)

    def _seed_other_project(self):
        body = "다른 프로젝트의 비공개원문: 현재 프로젝트에서 보이면 안 됩니다."
        self.store.ingest(self.two, [
            self._record("other-a", body, "다른범위A"),
            self._record("other-b", body, "다른범위B"),
        ])

    def _scan(self, projects=("one",)):
        return audit_annotations(
            self.root, list(projects), collect_candidates=True, sample_cap=0,
            cancelled=lambda: False,
        )

    def _review(self, scope=None, projects=("one",)):
        scope = dict(self.PROJECT_SCOPE if scope is None else scope)
        report = self._scan(projects)
        review_id = create_review(self.root, scope, report)
        self.assertIsInstance(review_id, str)
        self.assertTrue(review_id)
        return scope, report, review_id

    def test_concurrent_review_publications_respect_the_shared_file_budget(self):
        self._seed_groups(1, first_members=3)
        report = self._scan()
        existing = {create_review(self.root, self.PROJECT_SCOPE, report)
                    for _ in range(candidates.MAX_CACHE_FILES - 1)}
        self.assertEqual(len(existing), candidates.MAX_CACHE_FILES - 1)
        folder = self.store.root / 'annotation-reviews'
        previous = {identity: hashlib.sha256((folder / (identity + '.json')).read_bytes()).hexdigest()
                    for identity in existing}
        report_path = self.root / 'owned-candidate-report.json'
        report_path.write_text(json.dumps(report))
        paused, release = threading.Event(), threading.Event()
        original_mkstemp = candidates.tempfile.mkstemp
        published, failures = [], []

        def pause_before_publication(*args, **kwargs):
            paused.set()
            if not release.wait(10):
                raise AssertionError('owned publisher was not released')
            return original_mkstemp(*args, **kwargs)

        def publish():
            try:
                published.append(create_review(self.root, self.PROJECT_SCOPE, report))
            except Exception as error:
                failures.append(error)

        program = """
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from alden_annotation_candidates import create_review
try:
    review=create_review(Path(sys.argv[2]),{'scope':'project','project':'one'},
                         json.loads(Path(sys.argv[3]).read_text()))
    print(json.dumps({'review_id':review}))
except ValueError as error:
    print(json.dumps({'error':str(error)}))
"""
        with patch('alden_annotation_candidates.tempfile.mkstemp', side_effect=pause_before_publication):
            owner = threading.Thread(target=publish)
            owner.start()
            try:
                self.assertTrue(paused.wait(3), 'first publisher did not reach the real write boundary')
                contender = subprocess.run(
                    [sys.executable, '-B', '-c', program,
                     str(Path(__file__).resolve().parents[1] / 'scripts'),
                     str(self.root), str(report_path)],
                    capture_output=True, text=True, timeout=5,
                )
            finally:
                release.set()
                owner.join(5)
        self.assertFalse(owner.is_alive(), 'owned publisher leaked')
        self.assertEqual(failures, [])
        self.assertEqual(len(published), 1)
        self.assertLessEqual(len(list(folder.glob('*.json'))), candidates.MAX_CACHE_FILES)
        self.assertEqual(contender.returncode, 0, contender.stderr)
        self.assertEqual(json.loads(contender.stdout), {'error': 'annotation_review_cache_busy'})
        self.assertTrue(all((folder / (identity + '.json')).is_file() for identity in existing))
        self.assertEqual({identity: hashlib.sha256((folder / (identity + '.json')).read_bytes()).hexdigest()
                          for identity in existing}, previous)
        # The owner released its guard. An ordinary full-cache error must not
        # leave subsequent publishers permanently busy.
        with self.assertRaisesRegex(ValueError, '^annotation_review_cache_budget$'):
            create_review(self.root, self.PROJECT_SCOPE, report)

    def test_process_exit_releases_publication_ownership_without_removing_the_lock_inode(self):
        self._seed_groups(1, first_members=3)
        report = self._scan()
        folder = self.store.root / 'annotation-reviews'
        folder.mkdir(mode=0o700)
        program = """
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from alden_annotation_candidates import _publication_lock
with _publication_lock(Path(sys.argv[2])):
    os._exit(0)
"""
        owner = subprocess.run(
            [sys.executable, '-B', '-c', program,
             str(Path(__file__).resolve().parents[1] / 'scripts'), str(folder)],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(owner.returncode, 0, owner.stderr)
        lock = folder / '.publication.lock'
        identity = (lock.stat().st_dev, lock.stat().st_ino)
        review_id = create_review(self.root, self.PROJECT_SCOPE, report)
        self.assertTrue((folder / (review_id + '.json')).is_file())
        self.assertEqual((lock.stat().st_dev, lock.stat().st_ino), identity)
        self.assertEqual(lock.read_bytes(), b'')
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_unsafe_publication_lock_objects_never_publish_or_modify_their_targets(self):
        self._seed_groups(1, first_members=3)
        report = self._scan()
        folder = self.store.root / 'annotation-reviews'
        folder.mkdir(mode=0o700)
        lock = folder / '.publication.lock'
        outside = self.root / 'untouched-lock-target'
        outside.write_bytes(b'')
        outside.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'directory', 'nonempty', 'shared_permissions'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    lock.symlink_to(outside)
                elif kind == 'hardlink':
                    lock.hardlink_to(outside)
                elif kind == 'directory':
                    lock.mkdir(mode=0o700)
                else:
                    lock.write_bytes(b'not-a-lock' if kind == 'nonempty' else b'')
                    lock.chmod(0o644 if kind == 'shared_permissions' else 0o600)
                try:
                    with self.assertRaises((OSError, ValueError)):
                        create_review(self.root, self.PROJECT_SCOPE, report)
                    self.assertEqual(list(folder.glob('*.json')), [])
                    self.assertEqual(outside.read_bytes(), b'')
                    self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)
                finally:
                    if lock.is_dir() and not lock.is_symlink():
                        lock.rmdir()
                    else:
                        lock.unlink()

    def test_failed_publication_cleans_staging_and_releases_ownership(self):
        self._seed_groups(1, first_members=3)
        report = self._scan()
        with patch('alden_annotation_candidates.os.replace', side_effect=OSError('owned write failure')):
            with self.assertRaisesRegex(OSError, 'owned write failure'):
                create_review(self.root, self.PROJECT_SCOPE, report)
        folder = self.store.root / 'annotation-reviews'
        self.assertEqual(list(folder.glob('*.json')), [])
        self.assertEqual(list(folder.glob('.review-*')), [])
        review_id = create_review(self.root, self.PROJECT_SCOPE, report)
        self.assertTrue((folder / (review_id + '.json')).is_file())

    @staticmethod
    def _query(scope, review_id, *, offset=0, group=None):
        query = {**scope, "review_id": review_id, "offset": offset}
        if group is not None:
            query["group"] = group
        return query

    def _walk_pages(self, scope, review_id, *, kind, group=None):
        offset, seen_offsets, collected, total = 0, set(), [], None
        while True:
            self.assertNotIn(offset, seen_offsets, "pagination loop")
            seen_offsets.add(offset)
            page = read_review(
                self.root, self._query(scope, review_id, offset=offset, group=group),
                cancelled=lambda: False,
            )
            self.assertIs(page.get("ok"), True, page)
            self.assertEqual(page.get("schema"), 3)
            self.assertEqual(page.get("kind"), kind)
            self.assertEqual(page.get("semantic_status"), "unverified")
            self.assertTrue(page.get("checked_at"))
            self.assertEqual(page.get("offset"), offset)
            self.assertIs(type(page.get("total")), int)
            self.assertIn("next_offset", page)
            if total is None:
                total = page["total"]
            self.assertEqual(page["total"], total)
            items = page.get("items")
            self.assertIsInstance(items, list)
            self.assertLessEqual(len(items), self.PAGE_SIZE)
            if offset < total:
                self.assertTrue(items, "premature empty page")
            collected.extend(items)
            next_offset = page["next_offset"]
            if next_offset is None or next_offset == total:
                self.assertEqual(len(collected), total)
                break
            self.assertIs(type(next_offset), int)
            self.assertEqual(next_offset, offset + len(items))
            self.assertLess(next_offset, total)
            offset = next_offset
        return collected

    @staticmethod
    def _file_set(root):
        return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}

    def _original_hashes(self):
        paths = [self.store.path, *sorted(self.store.blobs.glob("*.json"))]
        return {
            str(path.relative_to(self.root)):
                hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        }

    def _version_rows(self):
        with self.store.database() as db:
            return {
                row["document_id"]: dict(row)
                for row in db.execute(
                    "SELECT document_id,id,raw_sha256 FROM versions"
                )
            }

    def _assert_rejected(self, result):
        self.assertIsInstance(result, dict)
        self.assertIs(
            result.get("ok"), False,
            f"expected rejection, got ok={result.get('ok')!r}, "
            f"kind={result.get('kind')!r}, reason={result.get('reason')!r}",
        )
        self.assertFalse(result.get("items"), "denied read returned partial records")
        serialized = json.dumps(result, ensure_ascii=False)
        for entry in self.expected.values():
            self.assertNotIn(entry["body"], serialized)
            self.assertNotIn(entry["topic"], serialized)

    def test_reference_pages_qualify_full_body_before_display_truncation(self):
        link='https://example.invalid/apply?revision=2'
        long_body=('https://example.invalid/apply ' * 160)+'뒤에 실제 본문 설명이 있습니다.'
        self.store.ingest(self.one,[self._record('link-a',link,'A'),self._record('link-b',link,'B'),
                                    self._record('long-a',long_body,'A'),self._record('long-b',long_body,'B')])
        scope,report,review_id=self._review()
        before=self._original_hashes()
        by_hash={g['projected_body_sha256']:i for i,g in enumerate(report['candidate_groups'])}
        for body,expected in ((link,'reference_only'),(long_body,'content_candidate')):
            page=read_review(self.root,self._query(scope,review_id,group=by_hash[digest(body.encode())]))
            self.assertTrue(page['ok'],page)
            self.assertEqual(len(page['items']),2)
            for row in page['items']:
                self.assertEqual(row['content_evidence'],{'policy':'source-content-evidence-v6','classification':expected})
                self.assertEqual(row['truncated'],len(body)>4000)
                self.assertEqual(row['body'],body[:4000])
        self.assertEqual(self._original_hashes(),before)

    def test_scan_exports_every_group_and_complete_version_membership(self):
        self._seed_groups()
        before = self._original_hashes()
        report = self._scan()
        self.assertRegex(report["scope_digest"], r"\A[0-9a-f]{64}\Z")
        self.assertEqual(report["source_annotation_review"]["samples"], [])
        groups = report["candidate_groups"]
        self.assertIsInstance(groups, list)
        self.assertEqual(len(groups), 12)
        versions = self._version_rows()
        all_document_ids = set()
        for group in groups:
            self.assertEqual(group["project"], "one")
            self.assertRegex(group["projected_body_sha256"], r"\A[0-9a-f]{64}\Z")
            self.assertTrue(group["different_fields"])
            self.assertTrue(set(group["different_fields"]) <= {"topics", "entityTags"})
            self.assertGreaterEqual(group["members"], 2)
            self.assertEqual(len(group["source_versions"]), group["members"])
            document_ids = [source["document_id"] for source in group["source_versions"]]
            self.assertEqual(len(set(document_ids)), group["members"])
            for source in group["source_versions"]:
                doc_id = source["document_id"]
                self.assertIn(doc_id, self.expected)
                self.assertEqual(source["version"], versions[doc_id]["id"])
                self.assertNotIn(doc_id, all_document_ids)
                all_document_ids.add(doc_id)
        self.assertEqual(all_document_ids, set(self.expected))
        self.assertEqual(self._original_hashes(), before)

    def test_group_pages_are_complete_unique_and_cache_is_metadata_only(self):
        self._seed_groups()
        before_originals = self._original_hashes()
        before_files = self._file_set(self.root)
        scope, report, review_id = self._review()
        new_files = sorted(self._file_set(self.root) - before_files)
        self.assertTrue(new_files, "review must persist a private cache in state_root")
        cache_bytes = {}
        for name in new_files:
            path = self.root / name
            data = path.read_bytes()
            cache_bytes[name] = data
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
            for marker in (
                "비공개원문", "비공개공통태그", "비공개다른태그",
                "원본에만있는비공개표식", "기관공통", "기관다름",
            ):
                self.assertNotIn(marker.encode("utf-8"), data)
        pages = self._walk_pages(
            scope, review_id, kind="annotation_candidate_groups"
        )
        self.assertEqual(len(pages), len(report["candidate_groups"]))
        key = lambda item: (item["project"], item["projected_body_sha256"])
        self.assertEqual(len({key(item) for item in pages}), len(pages))
        self.assertEqual(
            [item["group"] for item in pages], list(range(len(pages))),
        )
        # Full version membership is checked on the scan report; the public
        # group index can expose only the metadata needed to select a group.
        self.assertEqual(
            {
                key(item): (
                    item["members"], set(item["different_fields"]),
                )
                for item in pages
            },
            {
                key(item): (
                    item["members"], set(item["different_fields"]),
                )
                for item in report["candidate_groups"]
            },
        )
        for name, data in cache_bytes.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual(self._original_hashes(), before_originals)

    def test_record_pages_complete_with_third_distinct_tag(self):
        self._seed_groups()
        before = self._original_hashes()
        scope, report, review_id = self._review()
        group_index = next(
            i for i, group in enumerate(report["candidate_groups"])
            if group["members"] == 11
        )
        group = report["candidate_groups"][group_index]
        records = self._walk_pages(
            scope, review_id, kind="annotation_candidate_records",
            group=group_index,
        )
        self.assertEqual(len(records), 11)
        self.assertEqual(len({row["document_id"] for row in records}), 11)
        self.assertEqual(
            {row["document_id"] for row in records},
            {row["document_id"] for row in group["source_versions"]},
        )
        versions = self._version_rows()
        topic_counts = {"비공개공통태그00": 0, "비공개다른태그00": 0}
        for row in records:
            doc_id = row["document_id"]
            original = self.expected[doc_id]
            self.assertEqual(row["version"], versions[doc_id]["id"])
            self.assertEqual(row["raw_sha256"], versions[doc_id]["raw_sha256"])
            self.assertRegex(row["raw_sha256"], r"\A[0-9a-f]{64}\Z")
            rendered = json.dumps(row, ensure_ascii=False)
            self.assertIn(original["body"], rendered)
            self.assertIn(original["topic"], rendered)
            self.assertIn(original["entity"], rendered)
            topic_counts[original["topic"]] += 1
        self.assertEqual(topic_counts, {
            "비공개공통태그00": 2,
            "비공개다른태그00": 9,
        })
        self.assertEqual(
            [self.expected[row["document_id"]]["topic"] for row in records[:3]],
            ["비공개공통태그00", "비공개공통태그00", "비공개다른태그00"],
            "a third distinct tag must be visible even after two equal tags",
        )
        self.assertEqual(self._original_hashes(), before)

    def test_different_scope_and_copied_root_are_rejected(self):
        self._seed_groups(count=2, first_members=3)
        self._seed_other_project()
        scope, report, review_id = self._review()
        all_report = self._scan(("one", "two"))
        self.assertNotEqual(report["scope_digest"], all_report["scope_digest"])
        self.assertEqual(len(all_report["candidate_groups"]), 3)
        all_scope, _, all_review_id = self._review(
            scope={"scope": "all"}, projects=("one", "two"),
        )
        all_pages = self._walk_pages(
            all_scope, all_review_id, kind="annotation_candidate_groups",
        )
        self.assertEqual(len(all_pages), 3)
        self.assertEqual({group["project"] for group in all_pages}, {"one", "two"})
        for wrong_scope in (
            {"scope": "all"}, {"scope": "project", "project": "two"},
        ):
            with self.subTest(scope=wrong_scope):
                self._assert_rejected(read_review(
                    self.root, self._query(wrong_scope, review_id),
                ))
        with TemporaryDirectory(prefix="alden-candidate-copied-root-") as temp:
            copied = Path(temp) / "copied"
            shutil.copytree(self.root, copied)
            # /var is a symlink on macOS.  Resolve it so the rejection cannot
            # pass solely because CollectionStore forbids symlink ancestors.
            copied = copied.resolve()
            cloned_report = audit_annotations(
                copied, ["one"], collect_candidates=True, sample_cap=0,
                cancelled=lambda: False,
            )
            self.assertEqual(
                cloned_report["candidate_groups"], report["candidate_groups"],
                "copied collection must be a valid, equivalent scan",
            )
            self._assert_rejected(read_review(
                copied, self._query(scope, review_id, group=0),
            ))

    def test_revoked_permission_rejects_all_record_content(self):
        self._seed_groups(count=1, first_members=3)
        scope, _, review_id = self._review()
        self.assertTrue(read_review(
            self.root, self._query(scope, review_id, group=0),
        )["ok"])
        with self.store.database() as db:
            db.execute(
                "UPDATE target_projects SET permission='denied' WHERE target_id=?",
                (self.one,),
            )
        for group in (None, 0):
            with self.subTest(group=group):
                self._assert_rejected(read_review(
                    self.root, self._query(scope, review_id, group=group),
                ))

    def test_changed_current_version_rejects_cached_records(self):
        self._seed_groups(count=1, first_members=3)
        scope, _, review_id = self._review()
        doc_id, original = next(iter(self.expected.items()))
        before_version = self._version_rows()[doc_id]["id"]
        self.store.ingest(self.one, [
            self._record(
                original["name"], original["body"] + " 개정본",
                original["topic"], original["entity"],
            )
        ])
        self.assertNotEqual(self._version_rows()[doc_id]["id"], before_version)
        for group in (None, 0):
            with self.subTest(group=group):
                self._assert_rejected(read_review(
                    self.root, self._query(scope, review_id, group=group),
                ))

    def test_corrupted_private_cache_is_rejected(self):
        self._seed_groups(count=1, first_members=3)
        before = self._file_set(self.root)
        scope, _, review_id = self._review()
        new_files = self._file_set(self.root) - before
        self.assertTrue(new_files)
        for name in new_files:
            (self.root / name).write_bytes(b"tampered-candidate-cache")
        self._assert_rejected(read_review(
            self.root, self._query(scope, review_id, group=0),
        ))

    def test_expired_cache_is_rejected(self):
        self._seed_groups(count=1, first_members=3)
        scope, _, review_id = self._review()
        future_wall = time.time() + 20 * 366 * 86400
        future_monotonic = time.monotonic() + 20 * 366 * 86400
        with patch("time.time", return_value=future_wall), patch(
            "time.monotonic", return_value=future_monotonic
        ):
            self._assert_rejected(read_review(
                self.root, self._query(scope, review_id, group=0),
            ))

    def test_cancelled_read_never_returns_partial_originals(self):
        self._seed_groups(count=1, first_members=11)
        scope, _, review_id = self._review()
        for group in (None, 0):
            with self.subTest(group=group):
                calls = []
                def cancelled():
                    calls.append(True)
                    return True
                try:
                    result = read_review(
                        self.root, self._query(scope, review_id, group=group),
                        cancelled=cancelled,
                    )
                except (RuntimeError, ValueError) as exc:
                    self.assertRegex(str(exc).lower(), "cancel")
                else:
                    self._assert_rejected(result)
                self.assertTrue(calls, "cancel callback was not checked")


if __name__ == "__main__":
    unittest.main()
