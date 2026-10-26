"""Independent checkpoint acceptance contracts, using owned synthetic data only.

Run this module alone with the pinned Python 3.11 and PYTHONPATH=scripts:.
Missing checkpoint APIs are explicit failures, never skips or passing negatives.
The existing DeltaTests adapter supplies fake engine copying; no verifier,
exporter, publication helper, or source guard is mocked here.
"""
import inspect
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from alden_abort import AldenCancelled
from alden_collection import CollectionStore, identity
from alden_collection_export import ExportError, verify_current_export
from alden_gbrain_cache import _UnreapedWorker
from alden_gbrain_client import GbrainError
import alden_gbrain_delta as delta
import alden_gbrain_import as full
from alden_gbrain_watch import GbrainSourceWatcher
from tests import test_alden_gbrain_delta as delta_fixture


REJECTIONS = (GbrainError, ExportError, ValueError, OSError)


def _read(path):
    return json.loads(path.read_text())


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True))


def _replace_value(value, original, replacement):
    """Mutate an emitted binding without assuming its field spelling/nesting."""
    if value == original:
        return replacement, 1
    count = 0
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            result[key], changed = _replace_value(child, original, replacement)
            count += changed
        return result, count
    if isinstance(value, list):
        result = []
        for child in value:
            changed_value, changed = _replace_value(child, original, replacement)
            result.append(changed_value)
            count += changed
        return result, count
    return value, 0


class _ObservedClient:
    """Observe external engine reads/close; inject no acceptance decisions."""
    def __init__(self, case, generation, engine):
        self.case, self.generation, self.engine = case, generation, engine
        self.got, self.outgoing = set(), set()
        self.completed = False

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def __enter__(self):
        self.engine.__enter__()
        self.case.opened.append(self.generation)
        return self

    def __exit__(self, kind, value, tb):
        result = self.engine.__exit__(kind, value, tb)
        self.case.closed.append(self.generation)
        self.case.on_close(self.generation, kind)
        return result

    def get(self, slug):
        result = self.engine.get(slug)
        self.got.add(slug)
        return result

    def outgoing_links(self, slug, *, max_rows):
        yield from self.engine.outgoing_links(slug, max_rows=max_rows)
        self.outgoing.add(slug)
        expected = set(self.engine.pages)
        if not self.completed and expected and self.got == self.outgoing == expected:
            self.completed = True
            self.case.complete_reads.append((self.generation, len(expected)))
            self.case.on_readback(self.generation)


class _CatchupFixture(unittest.TestCase):
    def setUp(self):
        self.d = delta_fixture.DeltaTests()
        self.addCleanup(self.d.doCleanups)
        self.d.setUp()
        self.f, self.cache = self.d.f, self.d.cache
        self.opened, self.closed, self.complete_reads = [], [], []
        self.on_readback = lambda generation: None
        self.on_close = lambda generation, kind: None
        self.factory = lambda: CollectionStore.open_existing(self.f.root)
        self.enterContext(patch.object(full, 'GbrainImportClient', side_effect=self.client))
        self.enterContext(patch.object(full, 'GbrainReadClient', side_effect=self.client))
        self.enterContext(patch.object(delta, 'DeltaImportClient', side_effect=self.delta_client))

    def client(self, receipt, *, home, **kwargs):
        return _ObservedClient(self, Path(home).parent, self.f.engine(receipt, home=home, **kwargs))

    def delta_client(self, receipt, *, home, **kwargs):
        self.assertIsNotNone(self.d.engine, 'delta fixture must have copied its fake engine')
        return _ObservedClient(self, Path(home).parent, self.d.engine)

    def require_api(self, *, stage=True, catch_up=True):
        if stage:
            self.assertTrue(callable(getattr(self.cache, 'stage_existing', None)),
                            'BASELINE_API_ABSENT: FullGbrainCache.stage_existing')
        if catch_up:
            self.assertIn('catch_up', inspect.signature(self.cache.refresh).parameters,
                          'BASELINE_API_ABSENT: refresh(catch_up=...)')

    def clear_observations(self):
        self.opened.clear()
        self.closed.clear()
        self.complete_reads.clear()

    def closed_candidate(self):
        """Demote a real fake-engine publication to a closed unpublished fixture.

        Leave its verified operation receipt, manifest, source archive, and
        engine intact. Only publication grants are removed. This avoids
        calling private build/publication helpers to manufacture acceptance.
        """
        self.cache.refresh(incremental=True, store_factory=self.factory)
        active = self.cache.active()
        generation = self.cache.generations / active['generation']
        projection = full.FullProjection.open(generation)
        self.assertEqual(projection.policy, full.STABLE_POLICY)
        self.assertIn(generation, self.closed)
        (self.cache.root / 'active.json').unlink()
        (generation / 'ready.json').unlink()
        self.assert_unpublished()
        self.clear_observations()
        return generation, projection

    def assert_unpublished(self):
        self.assertIsNone(self.cache.active())
        self.assertFalse((self.cache.root / 'active.json').exists())
        self.assertEqual(list(self.cache.generations.glob('*/ready.json')), [])

    def assert_no_grant(self):
        self.assert_unpublished()
        self.assertFalse((self.cache.root / 'pending.json').exists())

    def assert_staged(self, result, generation=None, *, state='staged'):
        self.assertIs(result['ok'], True)
        self.assertEqual(result['state'], state)
        self.assertIs(result['published'], False)
        self.assertIs(result['current_source_verified'], False)
        self.assert_unpublished()
        pointer = _read(self.cache.root / 'pending.json')
        if generation is None:
            generation = self.cache.generations / pointer['generation']
        self.assertEqual(pointer['generation'], generation.name)
        self.assertTrue(generation.is_dir())
        return generation

    def stage(self, generation):
        return self.cache.stage_existing(generation.name, store_factory=self.factory)

    def stage_with_markers(self):
        generation, projection = self.closed_candidate()
        before = {p: p.read_bytes() for p in generation.glob('*.json')}
        self.assert_staged(self.stage(generation), generation)
        # Observe emitted per-generation JSON, without prescribing its name.
        markers = [p for p in generation.glob('*.json') if before.get(p) != p.read_bytes()]
        self.assertTrue(markers, 'stage_existing must persist a per-generation marker')
        return generation, projection, markers

    def change_content(self):
        self.f.store.ingest(self.f.target, [
            {'original_id': 'a', 'label': 'Later synthetic a', 'text': 'owned later content'}])

    def native_source(self):
        self.native = self.f.store.register(platform='kakaotalk', original_id='synthetic-room',
            kind='room', label='Owned synthetic native source', projects=['one'])
        self.f.store.ingest(self.native, [
            {'original_id': 'n', 'label': 'Synthetic native row', 'text': 'synthetic only'}])
        self.authority = {'access': True, 'revision': 'a' * 64, 'stream': 'b' * 64}
        self.factory = lambda: CollectionStore.open_existing(self.f.root,
            source_access=lambda *args: self.authority['access'],
            source_revision=lambda target: self.authority['revision'],
            source_stream=lambda target: self.authority['stream'])

    def lose_source(self, kind):
        if kind == 'authority':
            self.authority['revision'] = 'unavailable'
        elif kind == 'withheld':
            self.authority['access'] = False
        elif kind == 'access_stream':
            self.authority['stream'] = 'c' * 64
        elif kind == 'journal':
            with self.f.store.database() as db:
                db.execute("UPDATE meta SET value=? WHERE key='journal_id'", ('d' * 32,))
        elif kind == 'file_identity':
            replacement = self.f.base / 'replacement.sqlite3'
            shutil.copyfile(self.f.store.path, replacement)
            replacement.replace(self.f.store.path)
        elif kind == 'denied':
            self.f.store.register(platform='graph', original_id='a', kind='source',
                label='Publisher', projects=['one'], permission='denied')
        else:
            raise AssertionError(kind)

    def reject_or_rebuild(self, generation):
        """Invalid pending may be rejected or discarded for a fresh full build."""
        try:
            result = self.cache.refresh(incremental=True, catch_up=True, store_factory=self.factory)
        except REJECTIONS:
            self.assert_unpublished()
        else:
            self.assertEqual(result['state'], 'published')
            self.assertEqual(result['update_mode'], 'full_rebuild')
            active = self.cache.active()
            self.assertNotEqual(active['generation'], generation.name)
            current = full.FullProjection.open(self.cache.generations / active['generation'])
            verify_current_export(self.f.root, ['one'], current.archive, store_factory=self.factory)
            self.assertIsNone(self.d.engine, 'invalid pending must not become the delta base')

    def forge_pointer(self, generation, projection):
        _write(self.cache.root / 'pending.json', {
            'owner': self.cache.owner['owner'], 'generation': generation.name,
            'fingerprint': projection.fingerprint, 'state': 'staged',
            'published': False, 'current_source_verified': False})


class BaselineControls(_CatchupFixture):
    """Valid baseline negatives; these do not depend on the new API."""
    def test_closed_fixture_is_complete_and_source_factory_is_readonly(self):
        self.native_source()
        before = self.f.store.path.read_bytes()
        generation, projection = self.closed_candidate()
        receipt = verify_current_export(self.f.root, ['one'], projection.archive, store_factory=self.factory)
        self.assertEqual(receipt['counts']['nodes'], 4)
        self.assertEqual(projection.counts()['pages'], 6)
        self.assertEqual(_read(generation / 'operation.json')['state'], 'verified')
        with self.factory().database() as db:
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
        self.assertEqual(before, self.f.store.path.read_bytes())

    def test_normal_reader_does_not_adopt_forged_pending(self):
        generation, projection = self.closed_candidate()
        self.forge_pointer(generation, projection)
        before = (self.cache.root / 'pending.json').read_bytes()
        with self.assertRaisesRegex(GbrainError, 'unavailable'):
            self.f.query(store_factory=self.factory)
        self.assertEqual(self.opened, [])
        self.assertEqual(before, (self.cache.root / 'pending.json').read_bytes())
        self.assert_unpublished()

    def test_normal_refresh_rebuilds_without_adopting_forged_pending(self):
        generation, projection = self.closed_candidate()
        self.forge_pointer(generation, projection)
        self.f.engines[generation].pages.clear()
        result = self.cache.refresh(incremental=True, store_factory=self.factory)
        self.assertEqual(result['update_mode'], 'full_rebuild')
        self.assertNotEqual(self.cache.active()['generation'], generation.name)
        self.assertIsNone(self.d.engine)
        self.assertTrue(self.f.query(store_factory=self.factory)['ok'])

    def test_default_watcher_acknowledges_only_new_publication(self):
        generation, projection = self.closed_candidate()
        self.forge_pointer(generation, projection)
        watcher = GbrainSourceWatcher(self.cache.root, collection_root=self.f.root,
            projects=['one'], store_factory=self.factory)
        watcher.cache = self.cache
        with watcher:
            result = watcher.tick()
        self.assertEqual(result['state'], 'ready')
        self.assertNotEqual(result['accepted']['generation'], generation.name)
        self.assertEqual(result['accepted']['generation'], self.cache.active()['generation'])
        self.assertIsNone(self.d.engine)

    def test_normal_refresh_content_advance_after_readback_is_not_published(self):
        self.on_readback = lambda generation: self.change_content()
        with self.assertRaises(ExportError):
            self.cache.refresh(incremental=True, store_factory=self.factory)
        self.assertTrue(self.complete_reads)
        self.assert_no_grant()


class CheckpointAdversarialTests(_CatchupFixture):
    def test_stage_reverifies_closed_engine_without_granting_reader_authority(self):
        self.require_api(catch_up=False)
        generation, projection = self.closed_candidate()
        before = self.f.store.path.read_bytes()
        self.assert_staged(self.stage(generation), generation)
        self.assertIn((generation, projection.counts()['pages']), self.complete_reads)
        self.assertIn(generation, self.closed)
        with self.assertRaisesRegex(GbrainError, 'unavailable'):
            self.f.query(store_factory=self.factory)
        self.assertEqual(before, self.f.store.path.read_bytes())

    def test_catch_up_requires_incremental_without_engine_work_or_grants(self):
        self.require_api(stage=False)
        before = self.f.store.path.read_bytes()
        with self.assertRaises((GbrainError, ValueError)):
            self.cache.refresh(catch_up=True, incremental=False, store_factory=self.factory)
        self.assertEqual(before, self.f.store.path.read_bytes())
        self.assert_no_grant()
        self.assertEqual(list(self.cache.generations.iterdir()), [])
        self.assertEqual(self.opened, [])
        self.assertEqual(self.f.commands, [])

    def test_stage_rejects_changed_engine_page(self):
        self.require_api(catch_up=False)
        generation, projection = self.closed_candidate()
        slug = projection.slug(identity('graph', 'b'))
        self.f.engines[generation].pages[slug]['frontmatter']['alden_record_sha256'] = '0' * 64
        with self.assertRaises(GbrainError):
            self.stage(generation)
        self.assert_no_grant()

    def test_stage_rejects_missing_engine_page(self):
        self.require_api(catch_up=False)
        generation, projection = self.closed_candidate()
        del self.f.engines[generation].pages[projection.slug(identity('graph', 'b'))]
        with self.assertRaises(GbrainError):
            self.stage(generation)
        self.assert_no_grant()

    def test_stage_reverifies_data_instead_of_trusting_an_old_intent_receipt(self):
        self.require_api(catch_up=False)
        generation, _ = self.closed_candidate()
        _write(generation / 'operation.json', {'state': 'intent', 'request_id': 'synthetic-uncertain'})
        result=self.stage(generation)
        self.assert_staged(result,generation)
        self.assertIn((generation,result['pages']),self.complete_reads)
        self.assertEqual(result['verified_queries'],result['pages'])
        self.assertEqual(_read(generation/'operation.json')['state'],'intent')

    def test_stage_recomputes_incomplete_receipt_counts_from_full_readback(self):
        self.require_api(catch_up=False)
        generation, _ = self.closed_candidate()
        value = _read(generation / 'operation.json')
        value.pop('verified_queries')
        _write(generation / 'operation.json', value)
        result=self.stage(generation)
        self.assert_staged(result,generation)
        self.assertIn((generation,result['pages']),self.complete_reads)
        self.assertEqual(result['verified_queries'],result['pages'])
        self.assertNotIn('verified_queries',_read(generation/'operation.json'))

    def test_partial_per_generation_marker_cannot_authorize_catch_up(self):
        self.require_api()
        generation, _, markers = self.stage_with_markers()
        for path in markers:
            _write(path, {})
        self.reject_or_rebuild(generation)

    def test_normal_refresh_does_not_adopt_valid_staged_base(self):
        self.require_api(catch_up=False)
        generation, _, _ = self.stage_with_markers()
        result = self.cache.refresh(incremental=True, store_factory=self.factory)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['update_mode'], 'full_rebuild')
        self.assertNotEqual(self.cache.active()['generation'], generation.name)
        self.assertIsNone(self.d.engine)
        self.assertTrue(self.f.query(store_factory=self.factory)['ok'])

    def test_default_watcher_does_not_adopt_valid_staged_base(self):
        self.require_api(catch_up=False)
        generation, _, _ = self.stage_with_markers()
        watcher = GbrainSourceWatcher(self.cache.root, collection_root=self.f.root,
            projects=['one'], store_factory=self.factory, incremental=True)
        watcher.cache = self.cache
        with watcher:
            result = watcher.tick()
        self.assertEqual(result['state'], 'ready')
        self.assertNotEqual(result['accepted']['generation'], generation.name)
        self.assertEqual(result['accepted']['generation'], self.cache.active()['generation'])
        self.assertIsNone(self.d.engine)

    def test_changed_manifest_cannot_authorize_pending_delta(self):
        self.require_api()
        generation, _, _ = self.stage_with_markers()
        manifest = generation / 'projection.json'
        value = _read(manifest)
        value['fingerprint'] = 'f' * 64
        _write(manifest, value)
        self.reject_or_rebuild(generation)

    def test_changed_runtime_receipt_cannot_authorize_pending_delta(self):
        self.require_api()
        generation, _, _ = self.stage_with_markers()
        path = self.cache.root / 'runtime-receipt.json'
        receipt = _read(path)
        receipt['sha256'] = '0' * 64
        _write(path, receipt)
        self.reject_or_rebuild(generation)

    def test_pending_marker_cannot_regrant_revoked_original_authority(self):
        self.require_api()
        self.native_source()
        generation, _, _ = self.stage_with_markers()
        self.lose_source('authority')
        with self.assertRaises(REJECTIONS):
            self.cache.refresh(incremental=True, catch_up=True, store_factory=self.factory)
        self.assert_no_grant()

    def test_read_client_unreaped_preserves_candidate_without_pending(self):
        self.require_api(catch_up=False)
        generation, _ = self.closed_candidate()
        def unreaped(path, kind):
            raise _UnreapedWorker('gbrain_cache_worker_not_reaped')
        self.on_close = unreaped
        with self.assertRaises(_UnreapedWorker):
            self.stage(generation)
        self.assertTrue(self.complete_reads)
        self.assertTrue(generation.is_dir())
        self.assert_no_grant()

    def test_cancel_during_stage_full_readback_never_grants_pending(self):
        self.require_api(catch_up=False)
        generation, _ = self.closed_candidate()
        def cancel(path):
            raise AldenCancelled('synthetic checkpoint CANCEL')
        self.on_readback = cancel
        with self.assertRaises(AldenCancelled):
            self.stage(generation)
        self.assertTrue(self.complete_reads)
        self.assert_no_grant()

    def test_cancel_and_unreaped_reader_retains_recovery_precedence(self):
        self.require_api(catch_up=False)
        generation, _ = self.closed_candidate()
        self.cancel_with_unreaped_close()
        with self.assertRaises(_UnreapedWorker):
            self.stage(generation)
        self.assertTrue(generation.is_dir())
        self.assert_no_grant()

    def cancel_with_unreaped_close(self):
        def cancel(path):
            raise AldenCancelled('synthetic checkpoint CANCEL')
        def unreaped(path, kind):
            self.assertIs(kind, AldenCancelled)
            raise _UnreapedWorker('gbrain_cache_worker_not_reaped')
        self.on_readback, self.on_close = cancel, unreaped

    def test_cancel_and_unreaped_writer_retains_recovery_precedence(self):
        self.require_api(stage=False)
        self.cancel_with_unreaped_close()
        with self.assertRaises(_UnreapedWorker):
            self.cache.refresh(incremental=True, catch_up=True, store_factory=self.factory)
        self.assertEqual(len(self.complete_reads), 1)
        generation, _ = self.complete_reads[0]
        self.assertTrue(generation.is_dir())
        self.assertTrue((generation / 'brain.pglite').is_dir())
        self.assert_no_grant()

    def test_content_only_pending_then_quiet_source_publishes_through_delta(self):
        self.require_api()
        before = self.f.store.path.read_bytes()
        self.on_readback = lambda generation: self.change_content()
        result = self.cache.refresh(incremental=True, catch_up=True, store_factory=self.factory)
        generation = self.assert_staged(result,state='pending')
        projection = full.FullProjection.open(generation)
        self.assertIn((generation, projection.counts()['pages']), self.complete_reads)
        self.assertNotEqual(before, self.f.store.path.read_bytes(), 'fault injection must advance content')
        with self.assertRaisesRegex(ExportError, 'stale'):
            verify_current_export(self.f.root, ['one'], projection.archive, store_factory=self.factory)
        with self.assertRaisesRegex(GbrainError, 'unavailable'):
            self.f.query(store_factory=self.factory)
        self.on_readback = lambda generation: None
        stable_source = self.f.store.path.read_bytes()
        result = self.cache.refresh(incremental=True, catch_up=True, store_factory=self.factory)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['update_mode'], 'engine_delta')
        self.assertEqual(result['engine_writes']['pages_put'], 1)
        active = self.cache.active()
        self.assertNotEqual(active['generation'], generation.name)
        current = full.FullProjection.open(self.cache.generations / active['generation'])
        verify_current_export(self.f.root, ['one'], current.archive, store_factory=self.factory)
        self.assertIn((current.root, current.counts()['pages']), self.complete_reads)
        self.assertFalse((self.cache.root / 'pending.json').exists())
        query = self.f.query('a', store_factory=self.factory)
        self.assertEqual(next(n['label'] for n in query['nodes'] if n['id'] == identity('graph', 'a')),
                         'Later synthetic a')
        self.assertEqual(stable_source, self.f.store.path.read_bytes())


def _pointer_test(binding):
    def test(self):
        self.require_api()
        generation, projection, _ = self.stage_with_markers()
        original = {'owner': self.cache.owner['owner'], 'generation': generation.name,
                    'fingerprint': projection.fingerprint}[binding]
        replacement = {'owner': '00000000-0000-0000-0000-000000000000',
                       'generation': 'g' + 'f' * 32, 'fingerprint': '0' * 64}[binding]
        path = self.cache.root / 'pending.json'
        value, count = _replace_value(_read(path), original, replacement)
        self.assertGreater(count, 0, 'pending pointer lacks the required ' + binding + ' binding')
        _write(path, value)
        self.reject_or_rebuild(generation)
    return test


def _source_loss_test(kind, *, stage):
    def test(self):
        self.require_api(stage=stage, catch_up=not stage)
        if kind in ('authority', 'withheld', 'access_stream'):
            self.native_source()
        generation = self.closed_candidate()[0] if stage else None
        self.on_readback = lambda path: self.lose_source(kind)
        try:
            if stage:
                result = self.stage(generation)
            else:
                result = self.cache.refresh(incremental=True, catch_up=True, store_factory=self.factory)
        except REJECTIONS:
            self.assert_no_grant()
        else:
            # A retry which fully rebuilds and verifies current source is safe;
            # converting a changed-authority candidate into pending is not.
            self.assertFalse(stage, 'stage_existing cannot accept source authority/identity loss')
            self.assertEqual(result['state'], 'published')
            self.assertEqual(result['update_mode'], 'full_rebuild')
            active = self.cache.active()
            self.assertNotEqual(active['generation'], self.complete_reads[0][0].name)
            projection = full.FullProjection.open(self.cache.generations / active['generation'])
            verify_current_export(self.f.root, ['one'], projection.archive, store_factory=self.factory)
            self.assertFalse((self.cache.root / 'pending.json').exists())
        self.assertTrue(self.complete_reads, 'fault must occur during complete engine readback')
    return test


def _marker_scope_test(binding):
    def test(self):
        self.require_api()
        generation, projection, markers = self.stage_with_markers()
        candidates = ([['one']] if binding == 'projects' else
                      [str(self.f.root), projection.receipt['source_root_sha256']])
        changed = 0
        for path in markers:
            value = _read(path)
            for original in candidates:
                replacement = ['foreign-project'] if binding == 'projects' else '0' * 64
                value, count = _replace_value(value, original, replacement)
                changed += count
            _write(path, value)
        self.assertGreater(changed, 0, 'stage marker lacks an explicit ' + binding + ' binding')
        self.reject_or_rebuild(generation)
    return test


for _binding in ('owner', 'generation', 'fingerprint'):
    setattr(CheckpointAdversarialTests, 'test_pending_pointer_' + _binding + '_cannot_authorize_delta',
            _pointer_test(_binding))

for _binding in ('source_root', 'projects'):
    setattr(CheckpointAdversarialTests, 'test_stage_marker_' + _binding + '_cannot_authorize_delta',
            _marker_scope_test(_binding))

for _kind in ('authority', 'withheld', 'access_stream', 'journal', 'file_identity', 'denied'):
    for _stage in (True, False):
        setattr(CheckpointAdversarialTests,
                'test_' + ('stage' if _stage else 'catch_up') + '_source_loss_during_readback_' + _kind,
                _source_loss_test(_kind, stage=_stage))


if __name__ == '__main__':
    unittest.main()
