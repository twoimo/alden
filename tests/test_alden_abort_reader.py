from __future__ import annotations

import json
import os
import stat
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import alden_abort as abort


def write_state(path, *, latched=False, epoch=3):
    path.write_text(json.dumps({"schema_version": 1, "epoch": epoch,
        "latched": latched, "reason": "stop" if latched else "human_resume"}))
    path.chmod(0o600)


class ScopedAbortReaderTests(unittest.TestCase):
    def assert_stopped(self, state):
        self.assertTrue(state.latched)
        self.assertTrue(state.is_error)

    def test_parsing_reused_but_file_open_validation_and_bytes_are_fresh(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / abort.ABORT_STATE_NAME
            write_state(path)
            with mock.patch.object(abort, "_strict_json_loads", wraps=abort._strict_json_loads) as parse, \
                 mock.patch.object(abort, "_validate_private_file", wraps=abort._validate_private_file) as validate, \
                 mock.patch.object(abort, "_read_bounded", wraps=abort._read_bounded) as read_bytes, \
                 mock.patch.object(abort.os, "open", wraps=os.open) as opened:
                with abort.abort_state_reader(path) as read:
                    for _ in range(3):
                        self.assertFalse(read().latched)
                self.assertEqual(parse.call_count, 1)
                self.assertEqual(validate.call_count, 3)
                self.assertEqual(read_bytes.call_count, 3)
                self.assertEqual(sum(c.args[0] == path.name for c in opened.call_args_list), 3)
                self.assertEqual(sum(c.args[0] == path.parent for c in opened.call_args_list), 1)

    def test_inplace_and_atomic_changes_are_visible_on_the_next_call(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / abort.ABORT_STATE_NAME
            write_state(path)
            with abort.abort_state_reader(path) as read:
                self.assertFalse(read().latched)
                before = path.stat()
                write_state(path, latched=True, epoch=4)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertTrue(read().latched)
                replacement = path.with_name("replacement")
                write_state(replacement, epoch=5)
                os.replace(replacement, path)
                self.assertEqual((read().latched, read().epoch), (False, 5))

    def test_same_bytes_on_new_inode_still_revalidate_permissions(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / abort.ABORT_STATE_NAME
            write_state(path)
            with abort.abort_state_reader(path) as read:
                self.assertFalse(read().latched)
                replacement = path.with_name("replacement")
                replacement.write_bytes(path.read_bytes())
                replacement.chmod(0o644)
                os.replace(replacement, path)
                self.assert_stopped(read())
                path.chmod(0o600)
                self.assertFalse(read().latched)

    def test_missing_root_and_missing_file_are_not_cached(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "initially-absent" / abort.ABORT_STATE_NAME
            with abort.abort_state_reader(path) as read:
                self.assertFalse(read().latched)
                path.parent.mkdir(mode=0o700)
                self.assertFalse(read().latched)
                write_state(path, latched=True)
                self.assertTrue(read().latched)

    def test_pinned_root_replacement_cannot_be_adopted_or_reset(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir(mode=0o700)
            path = root / abort.ABORT_STATE_NAME
            write_state(path)
            with abort.abort_state_reader(path) as read:
                self.assertFalse(read().latched)
                parked = root.with_name("parked")
                root.rename(parked)
                root.mkdir(mode=0o700)
                write_state(path)
                self.assert_stopped(read())
                path.unlink()
                root.rmdir()
                parked.rename(root)
                self.assert_stopped(read())

    def test_root_mode_owner_symlink_and_disappearance_fail_closed(self):
        for kind in ("mode", "owner", "symlink", "missing"):
            with self.subTest(kind=kind), TemporaryDirectory() as temp:
                root = Path(temp) / "root"
                root.mkdir(mode=0o700)
                path = root / abort.ABORT_STATE_NAME
                write_state(path)
                with abort.abort_state_reader(path) as read:
                    self.assertFalse(read().latched)
                    if kind == "mode":
                        root.chmod(0o755)
                    elif kind == "symlink":
                        parked = root.with_name("parked")
                        root.rename(parked)
                        root.symlink_to(parked, target_is_directory=True)
                    elif kind == "missing":
                        path.unlink()
                        root.rmdir()
                    if kind == "owner":
                        with mock.patch.object(abort.os, "geteuid", return_value=os.geteuid() + 1):
                            self.assert_stopped(read())
                    else:
                        self.assert_stopped(read())

    def test_unsafe_files_fail_closed_after_a_valid_cached_read(self):
        for kind in ("mode", "owner", "hardlink", "fifo", "symlink", "directory", "oversize", "utf8", "duplicate", "malformed"):
            with self.subTest(kind=kind), TemporaryDirectory() as temp:
                path = Path(temp) / abort.ABORT_STATE_NAME
                write_state(path)
                with abort.abort_state_reader(path) as read:
                    self.assertFalse(read().latched)
                    if kind == "owner":
                        original = os.fstat
                        def foreign_file(fd):
                            info = original(fd)
                            if stat.S_ISREG(info.st_mode):
                                fields = list(info)
                                fields[4] = info.st_uid + 1
                                return os.stat_result(fields)
                            return info
                        with mock.patch.object(abort.os, "fstat", side_effect=foreign_file):
                            self.assert_stopped(read())
                        continue
                    if kind == "mode":
                        path.chmod(0o644)
                    elif kind == "hardlink":
                        os.link(path, path.with_name("extra-link"))
                    elif kind in {"fifo", "symlink", "directory"}:
                        path.unlink()
                        if kind == "fifo":
                            os.mkfifo(path, 0o600)
                        elif kind == "directory":
                            path.mkdir(mode=0o700)
                        else:
                            target = path.with_name("target")
                            write_state(target)
                            path.symlink_to(target)
                    else:
                        raw = {"oversize": b"x" * 4097, "utf8": b"\xff",
                            "duplicate": b'{"schema_version":1,"epoch":0,"latched":true,"latched":false,"reason":""}',
                            "malformed": b"{"}[kind]
                        path.write_bytes(raw)
                    self.assert_stopped(read())

    def test_root_swap_during_file_read_is_rejected_before_return(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir(mode=0o700)
            path = root / abort.ABORT_STATE_NAME
            write_state(path)
            original = abort._read_bounded
            def swap(fd):
                raw = original(fd)
                root.rename(root.with_name("parked"))
                root.mkdir(mode=0o700)
                write_state(path)
                return raw
            with abort.abort_state_reader(path) as read:
                with mock.patch.object(abort, "_read_bounded", side_effect=swap):
                    self.assert_stopped(read())

    def test_exception_exit_closes_all_descriptors_and_stale_callback_stops(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / abort.ABORT_STATE_NAME
            write_state(path)
            with mock.patch.object(abort.os, "open", wraps=os.open) as opened, \
                 mock.patch.object(abort.os, "close", wraps=os.close) as closed:
                with self.assertRaisesRegex(RuntimeError, "test exit"):
                    with abort.abort_state_reader(path) as read:
                        self.assertFalse(read().latched)
                        raise RuntimeError("test exit")
                self.assertEqual(opened.call_count, closed.call_count)
                for call in closed.call_args_list:
                    with self.assertRaises(OSError):
                        os.fstat(call.args[0])
                self.assert_stopped(read())

    def test_root_validation_exception_does_not_leak_the_unclaimed_fd(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / abort.ABORT_STATE_NAME
            original_open = os.open
            descriptors = []
            def tracked_open(*args, **kwargs):
                fd = original_open(*args, **kwargs)
                descriptors.append(fd)
                return fd
            with mock.patch.object(abort.os, "open", side_effect=tracked_open), \
                 mock.patch.object(abort, "_validate_root_fd", side_effect=OSError("validation interrupted")):
                with self.assertRaisesRegex(OSError, "validation interrupted"):
                    with abort.abort_state_reader(path) as read:
                        read()
            self.assertEqual(len(descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])

    def test_close_waits_for_inflight_read_and_no_post_close_unlatched_result(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / abort.ABORT_STATE_NAME
            write_state(path)
            entered, release, closed = threading.Event(), threading.Event(), threading.Event()
            original = abort._read_bounded
            results = []
            def blocked(fd):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test release timed out")
                return original(fd)
            context = abort.abort_state_reader(path)
            read = context.__enter__()
            worker = threading.Thread(target=lambda: results.append(read()))
            closer = threading.Thread(target=lambda: (context.__exit__(None, None, None), closed.set()))
            try:
                with mock.patch.object(abort, "_read_bounded", side_effect=blocked):
                    worker.start()
                    self.assertTrue(entered.wait(2))
                    closer.start()
                    self.assertFalse(closed.wait(0.02))
                    release.set()
                    worker.join(2)
                    closer.join(2)
                self.assertFalse(worker.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertEqual(len(results), 1)
                self.assertFalse(results[0].latched)
                self.assert_stopped(read())
            finally:
                release.set()
                worker.join(2)
                if closer.ident is not None:
                    closer.join(2)
                read.close()


if __name__ == "__main__":
    unittest.main()
