"""Independent, temporary-fixture contract tests for the pinned retained-blob reader."""

import errno
import hashlib
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import alden_graph_quality as quality


class PinnedBlobReaderTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # macOS /var can be a symlink: use the physical temporary path.
        self.root = Path(temp.name).resolve()
        self.folder = self.root / "retained" / "blobs"
        self.folder.mkdir(parents=True)

    def add_blob(self, data):
        name = hashlib.sha256(data).hexdigest() + ".json"
        (self.folder / name).write_bytes(data)
        return name

    def test_two_distinct_sources_read_from_one_pinned_directory(self):
        first = b'{"localOriginalText":"first independent source"}'
        second = b'{"localOriginalText":"second independent source"}'
        first_name = self.add_blob(first)
        second_name = self.add_blob(second)
        self.assertNotEqual(first_name, second_name)

        original_open = os.open
        leaf_flags = []

        def track_open(path, flags, *args, **kwargs):
            if path in (first_name, second_name):
                leaf_flags.append(flags)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(quality.os, "open", side_effect=track_open):
            with quality._pinned_blobs(self.folder) as read:
                self.assertEqual(read(first_name), first)
                self.assertEqual(read(second_name), second)

        self.assertEqual(len(leaf_flags), 2)
        for flags in leaf_flags:
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertTrue(flags & os.O_NONBLOCK)
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR))

    def test_leaf_symlink_cannot_be_read(self):
        target = self.add_blob(b'{"localOriginalText":"real"}')
        alias = hashlib.sha256(b"symlink alias").hexdigest() + ".json"
        (self.folder / alias).symlink_to(self.folder / target)
        with quality._pinned_blobs(self.folder) as read:
            with self.assertRaises(OSError) as failure:
                read(alias)
        self.assertEqual(failure.exception.errno, errno.ELOOP)

    def test_invalid_names_are_rejected_before_open(self):
        valid = self.add_blob(b'{"localOriginalText":"valid"}')
        bad_names = (None, b"not-text", "", "." + valid, "../" + valid,
                     valid + "/../" + valid, valid.upper(), valid[:-5] + ".txt",
                     "z" * 64 + ".json")
        with quality._pinned_blobs(self.folder) as read:
            for name in bad_names:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "quality_blob_name"):
                    read(name)
            self.assertTrue(read(valid))

    def test_corrupted_hash_is_rejected(self):
        name = self.add_blob(b'{"localOriginalText":"original"}')
        (self.folder / name).write_bytes(b'{"localOriginalText":"tampered"}')
        with quality._pinned_blobs(self.folder) as read:
            with self.assertRaisesRegex(ValueError, "quality_blob_hash"):
                read(name)

    def test_nonregular_leaf_and_byte_limit_are_enforced(self):
        directory_name = hashlib.sha256(b"directory").hexdigest() + ".json"
        (self.folder / directory_name).mkdir()
        data = b'{"localOriginalText":"longer than the budget"}'
        name = self.add_blob(data)
        with quality._pinned_blobs(self.folder) as read:
            # fdopen may reject a directory before the explicit fstat shape check.
            with self.assertRaises((IsADirectoryError, ValueError)):
                read(directory_name)
            with patch.object(quality, "MAX_RECORD_BYTES", len(data) - 1):
                with self.assertRaisesRegex(ValueError, "quality_blob_shape"):
                    read(name)

    def test_changed_leaf_fstat_is_rejected(self):
        name = self.add_blob(b'{"localOriginalText":"stable"}')
        actual_fstat = os.fstat
        leaf_stats = 0

        def changed_fstat(fd):
            nonlocal leaf_stats
            result = actual_fstat(fd)
            if stat.S_ISREG(result.st_mode):
                leaf_stats += 1
                if leaf_stats == 2:
                    return SimpleNamespace(st_ino=result.st_ino,
                                           st_size=result.st_size,
                                           st_mtime_ns=result.st_mtime_ns + 1)
            return result

        with patch.object(quality.os, "fstat", side_effect=changed_fstat):
            with quality._pinned_blobs(self.folder) as read:
                with self.assertRaisesRegex(ValueError, "quality_blob_changed"):
                    read(name)
        self.assertEqual(leaf_stats, 2)

    def test_symlink_in_parent_or_directory_is_rejected(self):
        ancestor_link = self.root / "linked-parent"
        ancestor_link.symlink_to(self.folder.parent, target_is_directory=True)
        folder_link = self.root / "linked-blobs"
        folder_link.symlink_to(self.folder, target_is_directory=True)
        for path in (ancestor_link / "blobs", folder_link):
            with self.subTest(path=path), self.assertRaises(OSError):
                with quality._pinned_blobs(path):
                    self.fail("symlink directory was accepted")

    def test_directory_replacement_during_scan_fails_on_exit(self):
        original = b'{"localOriginalText":"original pinned directory"}'
        name = self.add_blob(original)
        renamed = self.root / "retained" / "blobs-old"
        with self.assertRaisesRegex(ValueError, "quality_blob_changed"):
            with quality._pinned_blobs(self.folder) as read:
                self.folder.rename(renamed)
                self.folder.mkdir()
                (self.folder / name).write_bytes(b'{"localOriginalText":"replacement"}')
                self.assertEqual(read(name), original)

    def test_exception_closes_directory_fd_and_invalidates_reader(self):
        name = self.add_blob(b'{"localOriginalText":"untampered"}')
        (self.folder / name).write_bytes(b'{"localOriginalText":"corrupt"}')
        original_open_directory = quality._open_blob_directory
        opened_fds = []
        retained_reader = None

        def track_directory(folder):
            fd = original_open_directory(folder)
            opened_fds.append(fd)
            return fd

        with patch.object(quality, "_open_blob_directory", side_effect=track_directory):
            with self.assertRaisesRegex(ValueError, "quality_blob_hash"):
                with quality._pinned_blobs(self.folder) as read:
                    retained_reader = read
                    read(name)

        self.assertEqual(len(opened_fds), 1)
        with self.assertRaises(OSError) as failure:
            os.fstat(opened_fds[0])
        self.assertEqual(failure.exception.errno, errno.EBADF)
        with self.assertRaisesRegex(ValueError, "quality_blob_closed"):
            retained_reader(name)

    def test_initial_fstat_failure_closes_pinned_directory_fd(self):
        original_open_directory = quality._open_blob_directory
        actual_fstat = os.fstat
        opened_fds = []
        initial_checks = []

        def track_directory(folder):
            fd = original_open_directory(folder)
            opened_fds.append(fd)
            return fd

        def reject_initial_fstat(fd):
            if opened_fds and fd == opened_fds[0]:
                initial_checks.append(fd)
                raise OSError(errno.EIO, "synthetic_initial_fstat_failure")
            return actual_fstat(fd)

        with patch.object(quality, "_open_blob_directory", side_effect=track_directory):
            with patch.object(quality.os, "fstat", side_effect=reject_initial_fstat):
                with self.assertRaisesRegex(OSError, "synthetic_initial_fstat_failure"):
                    with quality._pinned_blobs(self.folder):
                        self.fail("reader yielded despite failed initial fstat")

        self.assertEqual(len(opened_fds), 1)
        self.assertEqual(initial_checks, opened_fds)
        with self.assertRaises(OSError) as failure:
            actual_fstat(opened_fds[0])
        self.assertEqual(failure.exception.errno, errno.EBADF)


if __name__ == "__main__":
    unittest.main()
