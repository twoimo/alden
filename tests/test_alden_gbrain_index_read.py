"""Bounded adversarial index-reader contracts; synthetic sources/FakeEngine only."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_alden_gbrain_import as fixture
from alden_gbrain_client import GbrainError
import alden_gbrain_import as full


class _Cursor:
    def __init__(self, raw):
        self.raw = raw
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.raw)

    def close(self):
        self.raw.close()
        self.closed = True


class _Connection:
    def __init__(self, raw, path, write):
        self.raw = raw
        self.path = Path(path)
        self.write = write
        self.closed = False
        self.cursors = []
        self.sql = []
        raw.set_trace_callback(self.sql.append)

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def execute(self, *args, **kwargs):
        cursor = _Cursor(self.raw.execute(*args, **kwargs))
        # Keep cursors alive: GC must not accidentally release a forgotten
        # point-lookup cursor before the next lazy provider callback.
        self.cursors.append(cursor)
        return cursor

    def close(self):
        self.raw.close()
        self.closed = True


class _IndexReads:
    def __init__(self, case, path):
        self.case = case
        self.path = path
        self.open_db = full._db
        self.handles = []
        self.shared = None
        self.callbacks = []

    def open(self, path, write=False):
        handle = _Connection(self.open_db(path, write), path, write)
        self.handles.append(handle)
        return handle

    def callback(self, phase, slug, count):
        live = [handle for handle in self.handles if not handle.closed]
        self.case.assertEqual(len(live), 1, 'one expected-index reader at every lazy callback')
        handle = live[0]
        self.case.assertEqual(handle.path, self.path)
        self.case.assertFalse(handle.write)
        if self.shared is None:
            self.shared = handle
        self.case.assertIs(handle, self.shared, 'outgoing sets must reuse the original reader')
        self.case.assertFalse(handle.raw.in_transaction)
        self.case.assertTrue(all(cursor.closed for cursor in handle.cursors),
                             'close every lookup cursor before resuming the provider')
        with closing(handle.raw.execute('PRAGMA query_only')) as cursor:
            self.case.assertEqual(cursor.fetchone()[0], 1)
        # in_transaction alone does not detect a SELECT cursor's read lock.
        # DELETE journal mode makes an exclusive writer a bounded lock probe.
        with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as writer:
            with closing(writer.execute('PRAGMA journal_mode')) as cursor:
                self.case.assertEqual(cursor.fetchone()[0], 'delete')
            writer.execute('BEGIN EXCLUSIVE').close()
            writer.execute('ROLLBACK').close()
        self.callbacks.append((phase, slug, count))

    def assert_closed(self):
        self.case.assertTrue(self.handles)
        for handle in self.handles:
            self.case.assertFalse(handle.write)
            self.case.assertTrue(handle.closed, 'reader leaked across verifier exit')
            with self.case.assertRaises(sqlite3.ProgrammingError):
                handle.raw.execute('SELECT 1')
            self.case.assertFalse(any(sql.lstrip().upper().startswith('BEGIN') for sql in handle.sql),
                                  'the verifier must not open an explicit read transaction')


class _VerifyProbe:
    """Observe real _verify, without replacing any validation or token guard."""
    def __init__(self, case, cache, on_stream=None, on_get=None):
        self.case = case
        self.original = cache._verify
        self.on_stream = on_stream
        self.on_get = on_get
        self.calls = 0
        self.requested = []
        self.closed_at_exit = False

    def verify(self, client, projection):
        self.calls += 1
        self.client = client
        self.projection = projection
        self.reads = _IndexReads(self.case, projection.path)
        original_outgoing = client.outgoing_links
        original_get = client.get

        def point(phase, slug, count):
            self.reads.callback(phase, slug, count)
            if self.on_stream is not None:
                self.on_stream(self, phase, slug, count)

        def outgoing(slug, *, max_rows):
            self.requested.append(slug)

            def stream():
                point('start', slug, 0)
                count = 0
                for row in original_outgoing(slug, max_rows=max_rows):
                    point('row', slug, count)
                    count += 1
                    yield row
                point('end', slug, count)

            return stream()

        def get(slug):
            page = original_get(slug)
            if self.on_get is not None:
                self.on_get(self, slug)
            return page

        with patch.object(full, '_db', side_effect=self.reads.open), \
                patch.object(client, 'outgoing_links', side_effect=outgoing), \
                patch.object(client, 'get', side_effect=get):
            try:
                return self.original(client, projection)
            finally:
                self.closed_at_exit = bool(self.reads.handles) and all(
                    handle.closed for handle in self.reads.handles)

    def assert_closed(self):
        self.case.assertEqual(self.calls, 1)
        self.case.assertTrue(self.closed_at_exit, 'close before refresh cleanup, not eventually')
        self.reads.assert_closed()


class IndexReadTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids discovering/rerunning FullImportTests' large cases.
        self.f = fixture.FullImportTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.source_before = self.f.source_bytes()

    def observe(self, **kwargs):
        probe = _VerifyProbe(self, self.f.cache, **kwargs)
        self.enterContext(patch.object(self.f.cache, '_verify', side_effect=probe.verify))
        return probe

    def assert_unpublished(self, probe):
        probe.assert_closed()
        self.assertIsNone(self.f.cache.active())
        self.assertFalse((self.f.cache.root / 'active.json').exists())
        self.assertEqual(list(self.f.cache.generations.iterdir()), [])
        with self.assertRaisesRegex(GbrainError, 'gbrain_cache_unavailable'):
            self.f.query()
        self.assertEqual(self.f.source_bytes(), self.source_before)

    def replace_index(self, path):
        original = path.read_bytes()
        before = path.stat()
        replacement = path.with_name('fixture-identical-index.sqlite3')
        replacement.write_bytes(original)
        replacement.chmod(before.st_mode & 0o777)
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, path)
        after = path.stat()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertNotEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))

    def test_full_import_reuses_one_quiescent_readonly_reader_and_closes_it(self):
        probe = self.observe()
        result = self.f.cache.refresh()
        self.assertTrue(result['ok'])
        self.assertEqual(result['state'], 'published')
        self.assertEqual({key: result[key] for key in ('source_nodes', 'origins', 'pages', 'links',
                                                      'verified_queries', 'provenance_verified_pages')},
                         {'source_nodes': 3, 'origins': 1, 'pages': 4, 'links': 5,
                          'verified_queries': 4, 'provenance_verified_pages': 4})
        starts = [slug for phase, slug, _ in probe.reads.callbacks if phase == 'start']
        ends = [slug for phase, slug, _ in probe.reads.callbacks if phase == 'end']
        self.assertEqual(len(set(starts)), 4)
        self.assertEqual(starts, probe.requested)
        self.assertEqual(ends, starts)
        self.assertEqual(sum(phase == 'row' for phase, _, _ in probe.reads.callbacks), 5)
        probe.assert_closed()
        self.assertIsNotNone(self.f.cache.active())
        self.assertEqual(self.f.source_bytes(), self.source_before)

    def test_identical_path_replacement_in_stream_stops_before_the_next_outgoing_set(self):
        attacked = []

        def attack(probe, phase, slug, count):
            # Exhaust the first nonempty set. Its rows still match the open
            # original inode, so only the whole-set pathname guard rejects it.
            if phase == 'end' and count and not attacked:
                self.replace_index(probe.projection.path)
                attacked.append((slug, len(probe.requested)))

        probe = self.observe(on_stream=attack)
        with self.assertRaisesRegex(GbrainError, 'gbrain_full_projection_changed'):
            self.f.cache.refresh()
        self.assertEqual(len(attacked), 1)
        self.assertLess(attacked[0][1], 4, 'leave another set to expose final-guard-only rejection')
        self.assertEqual(len(probe.requested), attacked[0][1])
        self.assert_unpublished(probe)

    def test_identical_path_replacement_before_set_stops_before_lazy_iteration(self):
        attacked = []

        def attack(probe, slug):
            if not attacked:
                self.replace_index(probe.projection.path)
                attacked.append(slug)

        probe = self.observe(on_get=attack)
        with self.assertRaisesRegex(GbrainError, 'gbrain_full_projection_changed'):
            self.f.cache.refresh()
        self.assertEqual(len(attacked), 1)
        self.assertEqual(probe.reads.callbacks, [], 'do not repin the token for each outgoing set')
        self.assertLessEqual(len(probe.requested), 1)
        self.assert_unpublished(probe)

    def test_committed_mutation_during_empty_set_rejects_without_holding_a_read_lock(self):
        committed = []

        def attack(probe, phase, slug, count):
            if phase != 'start' or probe.client.outgoing.get(slug) or committed:
                return
            before = probe.projection.path.read_bytes()
            with closing(sqlite3.connect(probe.projection.path, timeout=0, isolation_level=None)) as writer:
                writer.execute('BEGIN EXCLUSIVE').close()
                with closing(writer.execute('UPDATE pages SET revision=revision || ? WHERE slug=?',
                                            ('-fixture-mutation', slug))) as cursor:
                    self.assertEqual(cursor.rowcount, 1)
                writer.commit()
            self.assertNotEqual(probe.projection.path.read_bytes(), before)
            committed.append((slug, len(probe.requested)))

        probe = self.observe(on_stream=attack)
        with self.assertRaisesRegex(GbrainError, 'gbrain_full_projection_changed'):
            self.f.cache.refresh()
        self.assertEqual(len(committed), 1, 'the callback must actually acquire and commit a writer')
        self.assertEqual(len(probe.requested), committed[0][1])
        self.assertIn(('end', committed[0][0], 0), probe.reads.callbacks)
        self.assert_unpublished(probe)

    def test_cancellation_after_first_iterator_row_closes_reader_and_does_not_publish(self):
        cancelled = []
        checked = []

        def check():
            if cancelled:
                checked.append(True)
                raise RuntimeError('fixture_cancelled_during_outgoing')

        def cancel(probe, phase, slug, count):
            if phase == 'row' and count == 1 and not cancelled:
                cancelled.append((slug, len(probe.requested)))

        probe = self.observe(on_stream=cancel)
        with self.assertRaisesRegex(RuntimeError, 'fixture_cancelled_during_outgoing'):
            self.f.cache.refresh(check=check)
        self.assertEqual(len(cancelled), 1)
        self.assertTrue(checked)
        self.assertEqual(len(probe.requested), cancelled[0][1])
        self.assertNotIn(('end', cancelled[0][0], 2), probe.reads.callbacks,
                         'the per-row check must run before requesting another iterator item')
        self.assert_unpublished(probe)

    def test_provider_exception_after_first_iterator_row_closes_reader_and_does_not_publish(self):
        failed = []

        def fail(probe, phase, slug, count):
            if phase == 'row' and count == 1:
                failed.append((slug, len(probe.requested)))
                raise RuntimeError('fixture_outgoing_provider_failed')

        probe = self.observe(on_stream=fail)
        with self.assertRaisesRegex(RuntimeError, 'fixture_outgoing_provider_failed'):
            self.f.cache.refresh()
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(probe.requested), failed[0][1])
        self.assert_unpublished(probe)

    def test_public_outgoing_wrappers_each_open_and_close_their_own_readonly_reader(self):
        self.f.cache.refresh()
        projection = self.f.projection()
        engine = self.f.engines[projection.root]
        slug = next(slug for slug in sorted(engine.outgoing) if len(engine.outgoing[slug]) == 2)
        previous = None
        for name in ('verify_outgoing', 'verify_outgoing_provenance'):
            with self.subTest(wrapper=name):
                reads = _IndexReads(self, projection.path)

                def rows():
                    reads.callback('start', slug, 0)
                    count = 0
                    for row in engine.outgoing_links(slug, max_rows=3):
                        reads.callback('row', slug, count)
                        count += 1
                        yield row
                    reads.callback('end', slug, count)

                with patch.object(full, '_db', side_effect=reads.open):
                    self.assertEqual(getattr(projection, name)(slug, rows()), 2)
                self.assertEqual(len(reads.handles), 1)
                self.assertIsNot(reads.shared, previous)
                reads.assert_closed()
                previous = reads.shared
        self.assertEqual(self.f.source_bytes(), self.source_before)


if __name__ == '__main__':
    unittest.main()
