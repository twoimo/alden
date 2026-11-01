"""Source-export-only catch-up fallback contracts over owned synthetic fixtures.

Load against the parent's pinned scripts/tests, not the older source in this
test worktree. Compose CatchupTests through its module so unittest does not
discover or rerun the parent's suites. Source guards and archive verification
remain real; only exporter failures and inert engine boundaries are injected.
"""
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from tests import test_alden_gbrain_catchup as fixtures
from alden_abort import AldenCancelled
from alden_collection import CollectionStore, identity
from alden_collection_export import ExportError, verify_export, verify_current_export
from alden_gbrain_client import GbrainError
import alden_collection_delta as source_delta
import alden_gbrain_delta as delta
import alden_gbrain_import as full


ADVANCE_ERRORS = ('collection_export_stale', 'collection_export_source_changed')
EXPECTED_SOURCE_UPDATE = {'mode': 'full_export', 'reason': 'catch_up_source_advanced'}


class ExportFallbackTests(unittest.TestCase):
    def setUp(self):
        self.c = fixtures.CatchupTests()
        self.addCleanup(self.c.doCleanups)
        self.c.setUp()
        self.f, self.d, self.cache = self.c.f, self.c.d, self.c.cache
        self.access = {'visible': True, 'denied': False}
        self.factory = self.canonical_factory
        self.cancelled = False
        self.check_calls = 0
        self.incremental_calls, self.full_calls = [], []
        self.real_full = full.export_graph
        self.real_incremental = source_delta.export_incremental
        self.after_full = lambda output: None
        self.advance_during_full = False
        self.full_advanced = False
        # The composed fixture blocks Popen; cover the other public entrypoints
        # as well. These are hard failures, not subprocess-based test adapters.
        for name in ('run', 'call', 'check_call', 'check_output'):
            self.enterContext(patch('subprocess.' + name,
                                    side_effect=AssertionError('subprocess forbidden')))

    def canonical_factory(self):
        if self.access['denied']:
            raise PermissionError('synthetic source OS denial')
        return CollectionStore.open_existing(self.f.root,
            source_access=lambda *args: self.access['visible'],
            source_revision=lambda *args: 'a' * 64,
            source_stream=lambda *args: 'b' * 64)

    def check(self):
        self.check_calls += 1
        if self.cancelled:
            raise AldenCancelled('synthetic exporter cancellation')
        # Deliberately return None. Returning a source stamp here would make
        # SQLite's progress callback truthy and interrupt a valid operation.
        return None

    def staged_current(self, *, guarded=False):
        if guarded:
            with self.f.store.database() as db:
                db.execute("UPDATE targets SET platform='telegram'")
            old = self.c.closed_existing_guarded(self.factory)
        else:
            old = self.c.closed_existing()
        result = self.cache.stage_existing(old.root.name, store_factory=self.factory, check=self.check)
        self.c.assert_private(result, 'staged')
        verify_current_export(self.f.root, ['one'], old.archive, store_factory=self.factory)
        self.baseline_commands = list(self.f.commands)
        self.assertIsNone(self.d.engine)
        self.assertIsNone(self.check())
        return old

    def record_incremental(self, root, projects, output, kwargs):
        output = Path(output)
        self.assertEqual(Path(root), self.f.root)
        self.assertEqual(projects, ['one'])
        self.assertIs(kwargs['store_factory'], self.factory)
        self.assertFalse(output.exists())
        self.incremental_calls.append(output)
        return output

    def fail_incremental(self, code=ADVANCE_ERRORS[1], *, advance=True, action=None):
        def fail(root, projects, output, **kwargs):
            self.record_incremental(root, projects, output, kwargs)
            if advance:
                self.c.change()
            if action is not None:
                action()
            raise ExportError(code)
        return fail

    def canonical_full(self, root, projects, output, **kwargs):
        output = Path(output)
        self.full_calls.append(output)
        self.assertEqual(Path(root), self.f.root)
        self.assertEqual(projects, ['one'])
        self.assertIs(kwargs['store_factory'], self.factory)
        self.assertEqual(output, self.incremental_calls[-1])
        self.assertFalse(output.exists(), 'fallback cannot overwrite a partial archive')
        cancelled = kwargs['cancelled']
        def probe():
            value = cancelled()
            self.assertIs(value, False)
            if (self.advance_during_full and not self.full_advanced
                    and (output / 'graph.sqlite3').exists()):
                self.full_advanced = True
                self.c.change()
            return False
        receipt = self.real_full(root, projects, output, **{**kwargs, 'cancelled': probe})
        self.assertIs(receipt['complete'], True)
        self.assertEqual(verify_export(output), receipt)
        self.after_full(output)
        return receipt

    def refresh(self, **kwargs):
        return self.cache.refresh(incremental=True, catch_up=True,
                                  store_factory=self.factory, check=self.check, **kwargs)

    def assert_no_engine_work(self):
        self.assertEqual(self.f.commands, self.baseline_commands)
        self.assertIsNone(self.d.engine)

    def assert_new_complete(self, result, old):
        self.assertEqual(len(self.incremental_calls), 1)
        self.assertEqual(len(self.full_calls), 1)
        self.assertEqual(result['source_update'], EXPECTED_SOURCE_UPDATE)
        self.assertEqual(result['update_mode'], 'engine_delta')
        self.assertEqual(result['engine_writes']['pages_put'], 1)
        self.assertEqual(result['verified_queries'], result['pages'])
        self.assertEqual(result['enumerated_pages'], result['pages'])
        self.assertEqual(result['provenance_verified_pages'], result['pages'])
        active = self.cache.active()
        name = active['generation'] if active else result['generation']
        self.assertNotEqual(name, old.root.name, 'old pending retention is not new progress')
        projection = full.FullProjection.open(self.cache.generations / name)
        self.assertNotEqual(projection.fingerprint, old.fingerprint)
        receipt = verify_export(projection.archive)
        self.assertIs(receipt['complete'], True)
        self.assertEqual(receipt['source_update'], EXPECTED_SOURCE_UPDATE)
        self.assertEqual(receipt['counts'], old.receipt['counts'])
        return projection

    def test_real_incremental_cleanup_then_canonical_full_can_publish(self):
        old = self.staged_current()
        caught, advanced = [], False
        real_matches = source_delta._matches
        def moving_matches(*args, **kwargs):
            nonlocal advanced
            if not advanced:
                advanced = True
                self.c.change()
            return real_matches(*args, **kwargs)
        def moving_incremental(root, projects, output, **kwargs):
            output = self.record_incremental(root, projects, output, kwargs)
            try:
                return self.real_incremental(root, projects, output, **kwargs)
            except ExportError as error:
                caught.append(str(error))
                self.assertFalse(output.exists(), 'terminal incremental cleanup must be real')
                raise
        with patch.object(source_delta, 'export_incremental', side_effect=moving_incremental), \
                patch.object(source_delta, '_matches', side_effect=moving_matches), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            result = self.refresh()
        self.assertTrue(advanced)
        self.assertEqual(len(caught), 1)
        self.assertIn(caught[0], ADVANCE_ERRORS)
        projection = self.assert_new_complete(result, old)
        self.assertEqual(result['state'], 'published')
        verify_current_export(self.f.root, ['one'], projection.archive, store_factory=self.factory)
        self.assertTrue(self.f.query(store_factory=self.factory)['ok'])
        self.assertGreater(self.check_calls, 0)

    def test_complete_moving_full_export_makes_new_pending_then_quiet_publication(self):
        old = self.staged_current()
        self.advance_during_full = True
        with patch.object(source_delta, 'export_incremental', side_effect=self.fail_incremental()), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            result = self.refresh()
        self.assertTrue(self.full_advanced)
        self.c.assert_private(result, 'pending')
        pending = self.assert_new_complete(result, old)
        self.assertIs(pending.receipt['checkpoint_unchanged_at_completion'], False)
        with self.assertRaisesRegex(ExportError, 'stale'):
            verify_current_export(self.f.root, ['one'], pending.archive, store_factory=self.factory)
        self.assertFalse(old.root.exists())
        source_before = self.f.store.path.read_bytes()
        result = self.refresh()
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['update_mode'], 'engine_delta')
        current = self.f.projection()
        verify_current_export(self.f.root, ['one'], current.archive, store_factory=self.factory)
        self.assertFalse((self.cache.root / 'pending.json').exists())
        query = self.f.query('a', store_factory=self.factory)
        self.assertEqual(next(n['label'] for n in query['nodes'] if n['id'] == identity('graph', 'a')),
                         'Changed 2')
        self.assertEqual(source_before, self.f.store.path.read_bytes())

    def test_both_permitted_export_failures_retain_only_previous_verified_base(self):
        old = self.staged_current()
        def incomplete(root, projects, output, **kwargs):
            output = Path(output)
            self.full_calls.append(output)
            self.assertFalse(output.exists())
            output.mkdir(mode=0o700)
            (output / 'partial-only').write_bytes(b'incomplete synthetic archive, no receipt')
            self.c.change()
            raise ExportError(ADVANCE_ERRORS[0])
        with patch.object(source_delta, 'export_incremental', side_effect=self.fail_incremental()), \
                patch.object(full, 'export_graph', side_effect=incomplete):
            result = self.refresh()
        self.assertEqual(len(self.full_calls), 1, 'old retention alone must not satisfy this case')
        self.c.assert_private(result, 'pending')
        self.assertEqual(result['generation'], old.root.name)
        self.assertEqual(result['fingerprint'], old.fingerprint)
        self.assertEqual(list(self.cache.generations.iterdir()), [old.root])
        self.assert_no_engine_work()
        self.assertEqual(verify_export(old.archive), old.receipt)
        self.assertNotEqual(result.get('source_update'), EXPECTED_SOURCE_UPDATE)

    def test_dirty_incremental_output_is_rejected_without_overwriting(self):
        self.staged_current()
        dirty, removed = [], []
        real_remove = shutil.rmtree
        def fail(root, projects, output, **kwargs):
            output = self.record_incremental(root, projects, output, kwargs)
            self.c.change()
            output.mkdir(mode=0o700)
            (output / 'owner.json').write_text('{"synthetic":"unreconciled"}')
            (output / 'partial-only').write_bytes(b'must never become a full archive')
            dirty.append((output, {p.name: p.read_bytes() for p in output.iterdir()}))
            raise ExportError(ADVANCE_ERRORS[1])
        def remove(path, *args, **kwargs):
            for output, before in dirty:
                if output == Path(path) or Path(path) in output.parents:
                    self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()}, before)
                    removed.append(output)
            return real_remove(path, *args, **kwargs)
        with patch.object(source_delta, 'export_incremental', side_effect=fail), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full), \
                patch.object(shutil, 'rmtree', side_effect=remove):
            with self.assertRaisesRegex(GbrainError, '^gbrain_full_export_unreconciled$'):
                self.refresh()
        self.assertEqual(self.full_calls, [])
        self.assertEqual(removed, [dirty[0][0]], 'owned cleanup must see unchanged partial bytes')
        self.assert_no_engine_work()
        self.c.assert_purged()

    def invalidate(self, kind):
        if kind == 'authority':
            self.f.store.register(platform='graph', original_id='new-publisher', kind='source',
                                  label='Synthetic changed authority', projects=['one'])
        elif kind == 'file_identity':
            replacement = self.f.base / 'replacement.sqlite3'
            shutil.copyfile(self.f.store.path, replacement)
            replacement.replace(self.f.store.path)
        elif kind == 'stream':
            with self.f.store.database() as db:
                db.execute("UPDATE meta SET value=? WHERE key='journal_id'", ('f' * 32,))
        elif kind == 'visibility':
            self.access['visible'] = False
        elif kind == 'os_denial':
            self.access['denied'] = True
        elif kind == 'cancel':
            self.cancelled = True
        elif kind == 'runtime':
            (self.cache.root / 'runtime/gbrain').write_bytes(b'synthetic changed runtime')
        elif kind == 'permission':
            self.f.store.register(platform='graph', original_id='a', kind='source',
                                  label='Publisher', projects=['one'], permission='denied')
        else:
            raise AssertionError(kind)

    def test_dangling_archive_link_is_not_absent_output_or_fallback_authority(self):
        self.staged_current()
        links = []
        def fail(root, projects, output, **kwargs):
            output = self.record_incremental(root, projects, output, kwargs)
            self.c.change()
            target = output.parent / 'synthetic-never-created-target'
            output.symlink_to(target, target_is_directory=True)
            self.assertFalse(output.exists())
            self.assertTrue(output.is_symlink())
            links.append((output, target))
            raise ExportError(ADVANCE_ERRORS[1])
        with patch.object(source_delta, 'export_incremental', side_effect=fail), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            with self.assertRaises(GbrainError) as raised:
                self.refresh()
        causes, error = [], raised.exception
        while error is not None:
            causes.append(str(error))
            error = error.__cause__
        self.assertIn('gbrain_full_export_unreconciled', causes)
        self.assertEqual(self.full_calls, [])
        self.assertFalse(links[0][1].exists())
        self.assert_no_engine_work()
        self.assertIsNone(self.cache.active())
        self.assertFalse((self.cache.root / 'pending.json').exists())
        self.assertEqual(list(self.cache.generations.glob('*/ready.json')), [])

    def test_error_after_engine_mutation_never_retries_source_export(self):
        self.staged_current()
        mutated = []
        case = self
        class MutateThenFail:
            def __init__(self, engine):
                self.engine = engine
            def __getattr__(self, name):
                return getattr(self.engine, name)
            def __enter__(self):
                self.engine.__enter__()
                return self
            def __exit__(self, *args):
                return self.engine.__exit__(*args)
            def put(self, pages, request):
                result = self.engine.put(pages, request)
                case.assertTrue(result['terminal'])
                mutated.extend(p['slug'] for p in pages)
                # Deliberately reuse a permitted exporter code at a different
                # boundary: it must not trigger the source-export fallback.
                raise ExportError(ADVANCE_ERRORS[0])
        with patch.object(source_delta, 'export_incremental', side_effect=self.fail_incremental()), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full), \
                patch.object(delta, 'DeltaImportClient', side_effect=lambda *a, **k: MutateThenFail(self.d.engine)):
            with self.assertRaises((ExportError, GbrainError, FileNotFoundError)):
                self.refresh()
        self.assertTrue(mutated)
        self.assertEqual(len(self.incremental_calls), 1)
        self.assertEqual(len(self.full_calls), 1)
        self.c.assert_purged()


def _permitted_success(code):
    def test(self):
        old = self.staged_current()
        with patch.object(source_delta, 'export_incremental', side_effect=self.fail_incremental(code)), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            result = self.refresh()
        projection = self.assert_new_complete(result, old)
        self.assertEqual(result['state'], 'published')
        verify_current_export(self.f.root, ['one'], projection.archive, store_factory=self.factory)
    return test


def _no_independent_advance(code):
    def test(self):
        self.staged_current()
        with patch.object(source_delta, 'export_incremental', side_effect=self.fail_incremental(code, advance=False)), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            with self.assertRaisesRegex(GbrainError, 'source_advance_unconfirmed'):
                self.refresh()
        self.assertEqual(self.full_calls, [])
        self.assert_no_engine_work()
        self.c.assert_purged()
    return test


def _binding_loss(kind, after_full):
    def test(self):
        self.staged_current(guarded=kind == 'visibility')
        if after_full:
            self.after_full = lambda output: self.invalidate(kind)
        injection = None if after_full else lambda: self.invalidate(kind)
        with patch.object(source_delta, 'export_incremental',
                          side_effect=self.fail_incremental(action=injection)), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            with self.assertRaises((GbrainError, ExportError, PermissionError, AldenCancelled, ValueError)):
                self.refresh()
        self.assertEqual(len(self.full_calls), int(after_full))
        self.assert_no_engine_work()
        self.c.assert_purged()
    return test


def _nonadvance_error(code):
    def test(self):
        self.staged_current()
        with patch.object(source_delta, 'export_incremental', side_effect=self.fail_incremental(code)), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            with self.assertRaisesRegex(ExportError, '^' + code + '$'):
                self.refresh()
        self.assertEqual(self.full_calls, [])
        self.assert_no_engine_work()
        self.c.assert_purged()
    return test


def _default_refresh(code):
    def test(self):
        self.d.initial()
        self.baseline_commands = list(self.f.commands)
        with patch.object(source_delta, 'export_incremental', side_effect=self.fail_incremental(code)), \
                patch.object(full, 'export_graph', side_effect=self.canonical_full):
            with self.assertRaisesRegex(ExportError, '^' + code + '$'):
                self.cache.refresh(incremental=True, store_factory=self.factory, check=self.check)
        self.assertEqual(self.full_calls, [])
        self.assert_no_engine_work()
        self.c.assert_purged()
    return test


for _code in ADVANCE_ERRORS:
    _suffix = _code.removeprefix('collection_export_')
    setattr(ExportFallbackTests, 'test_permitted_' + _suffix + '_with_real_advance_can_publish',
            _permitted_success(_code))
    setattr(ExportFallbackTests, 'test_' + _suffix + '_without_independent_advance_never_falls_back',
            _no_independent_advance(_code))
    setattr(ExportFallbackTests, 'test_default_refresh_' + _suffix + '_never_falls_back',
            _default_refresh(_code))

for _kind in ('authority', 'file_identity', 'stream', 'visibility', 'os_denial', 'cancel', 'runtime', 'permission'):
    for _after in (False, True):
        setattr(ExportFallbackTests,
                'test_' + _kind + '_' + ('after_complete_full_export' if _after else 'before_fallback') + '_cannot_grant',
                _binding_loss(_kind, _after))

for _code in ('collection_export_record_budget', 'collection_export_source_withheld',
              'collection_export_authority_unavailable', 'collection_export_cancelled',
              'collection_export_changed', 'collection_export_owner_changed',
              'collection_export_scope_mismatch', 'collection_export_readonly_source_required'):
    setattr(ExportFallbackTests, 'test_nonadvance_' + _code.removeprefix('collection_export_') + '_never_falls_back',
            _nonadvance_error(_code))


if __name__ == '__main__':
    unittest.main()
