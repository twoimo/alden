"""Bootstrap publication boundaries over temporary sources and fake engines only."""
from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch
import uuid

from tests import test_alden_gbrain_import as fixture
from alden_collection import identity
import alden_collection_export as archive
from alden_gbrain_client import GbrainError
import alden_gbrain_import as full


class BootstrapContractTests(unittest.TestCase):
    @contextmanager
    def synthetic_source(self):
        # Reuse the inert runtime receipt and FakeEngine, never a host runtime.
        with patch('subprocess.Popen', side_effect=AssertionError('synthetic engines only')):
            f = fixture.FullImportTests()
            try:
                f.setUp()
                yield f
            finally:
                f.doCleanups()

    @contextmanager
    def during_initial_rows(self, change):
        """Advance the synthetic writer after one version has been exported."""
        original = archive._records
        emitted = []

        def records(*args, **kwargs):
            expected, rows = original(*args, **kwargs)

            def changing_rows():
                for table, value in rows:
                    yield table, value
                    if not emitted and table == 'nodes' and value['id'] == identity('graph', 'a'):
                        emitted.append(dict(value))
                        change()

            return expected, changing_rows()

        with patch.object(archive, '_records', side_effect=records):
            yield emitted

    def assert_unpublished(self, f):
        self.assertIsNone(f.cache.active())
        self.assertFalse((f.cache.root / 'active.json').exists())
        self.assertEqual(list(f.cache.generations.iterdir()), [])
        with self.assertRaisesRegex(GbrainError, '^gbrain_cache_unavailable$'):
            f.query()

    def deny_publisher(self, f):
        f.store.register(platform='graph', original_id='a', kind='source',
                         label='Publisher', projects=['one'], permission='denied')

    def test_version_advancement_during_initial_export_cannot_bootstrap(self):
        for incremental in (False, True):
            with self.subTest(incremental=incremental), self.synthetic_source() as f:
                receipts = []
                export = full.export_graph

                def capture(*args, **kwargs):
                    receipt = export(*args, **kwargs)
                    receipts.append(receipt)
                    return receipt

                def change():
                    f.store.ingest(f.target, [{'original_id': 'a', 'label': 'Changed during export',
                                               'text': 'new synthetic version'}])

                with self.during_initial_rows(change) as emitted, \
                        patch.object(full, 'export_graph', side_effect=capture):
                    with self.assertRaisesRegex(archive.ExportError, '^collection_export_stale$'):
                        f.cache.refresh(incremental=incremental)
                self.assertEqual(len(emitted), 1)
                self.assertEqual(len(receipts), 1)
                self.assertTrue(receipts[0]['complete'])
                self.assertFalse(receipts[0]['checkpoint_unchanged_at_completion'])
                self.assertNotEqual(receipts[0]['as_of']['checkpoint'],
                                    receipts[0]['observed_at_completion']['checkpoint'])
                self.assert_unpublished(f)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.engines, {})

                accepted = f.cache.refresh(incremental=incremental)
                self.assertEqual(accepted['state'], 'published')
                projection = f.projection()
                current = next(json.loads(p['payload']) for p in projection.pages()
                               if p['id'] == identity('graph', 'a'))
                self.assertNotEqual(current['source_version'], emitted[0]['source_version'])
                self.assertEqual(current['label'], 'Changed during export')
                self.assertEqual(next(n['label'] for n in f.query('a')['nodes']
                                      if n['id'] == identity('graph', 'a')), 'Changed during export')
                self.assertTrue(projection.receipt['checkpoint_unchanged_at_completion'])

    def test_full_permission_withdrawal_during_initial_export_cannot_bootstrap(self):
        for incremental in (False, True):
            with self.subTest(incremental=incremental), self.synthetic_source() as f:
                with self.during_initial_rows(lambda: self.deny_publisher(f)) as emitted:
                    with self.assertRaisesRegex(ValueError, '^collection_project_scope_invalid$'):
                        f.cache.refresh(incremental=incremental)
                self.assertEqual(len(emitted), 1)
                self.assert_unpublished(f)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.engines, {})
                # Retrying the same denied authority must not resurrect the archive.
                with self.assertRaisesRegex(ValueError, '^collection_project_scope_invalid$'):
                    f.cache.refresh(incremental=incremental)
                self.assert_unpublished(f)

    def test_partial_permission_withdrawal_during_initial_export_rebuilds_survivors(self):
        for incremental in (False, True):
            with self.subTest(incremental=incremental), self.synthetic_source() as f:
                other = f.store.register(platform='graph', original_id='other', kind='source',
                                         label='Other', projects=['one'])
                f.store.ingest(other, [{'original_id': 'd', 'label': 'Surviving record',
                                        'text': 'synthetic surviving source'}])
                with self.during_initial_rows(lambda: self.deny_publisher(f)) as emitted:
                    with self.assertRaisesRegex(archive.ExportError, '^collection_export_source_changed$'):
                        f.cache.refresh(incremental=incremental)
                self.assertEqual(len(emitted), 1)
                self.assert_unpublished(f)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.engines, {})

                accepted = f.cache.refresh(incremental=incremental)
                self.assertEqual(accepted['state'], 'published')
                self.assertEqual((accepted['source_nodes'], accepted['origins'], accepted['pages'],
                                  accepted['links'], accepted['verified_queries']), (1, 1, 2, 1, 2))
                projection = f.projection()
                self.assertEqual({p['id'] for p in projection.pages()},
                                 {identity('graph', 'd'), projection.origin(other)})
                self.assertEqual({n['id'] for n in f.query('d')['nodes']},
                                 {identity('graph', 'd'), projection.origin(other)})
                with self.assertRaisesRegex(GbrainError, '^gbrain_focus_unavailable$'):
                    f.query('a')

    def stage_verified_generation(self, f, incremental):
        """Produce real archive/index/readback evidence, withholding publication."""
        generation = f.cache.generations / ('g' + uuid.uuid4().hex)
        with f.cache.lease(exclusive=True) as fd:
            generation.mkdir(mode=0o700)
            full._write(generation / 'generation.json', {
                'kind': f.cache.KIND, 'owner': f.cache.owner['owner'], 'generation': generation.name})
            receipt = full.export_graph(f.root, ['one'], generation / 'source-archive')
            projection = full.FullProjection.prepare(
                generation, policy=full.STABLE_POLICY if incremental else full.POLICY)
            acceptance = f.cache._build(generation, projection, fd=fd, check=lambda: None)
            full._write(generation / 'projection.json', projection.manifest())
        self.assertTrue(receipt['complete'])
        self.assertTrue(receipt['checkpoint_unchanged_at_completion'])
        self.assertEqual(archive.verify_current_export(f.root, ['one'], projection.archive), receipt)
        self.assertEqual(acceptance['verified_queries'], 4)
        self.assertEqual(json.loads((generation / 'operation.json').read_text())['state'], 'verified')
        self.assertEqual(full.FullProjection.open(generation).fingerprint, projection.fingerprint)
        self.assertFalse((generation / 'ready.json').exists())
        self.assertFalse((f.cache.root / 'active.json').exists())
        return projection

    def check_unpublished_generation(self, *, advance_source):
        for incremental in (False, True):
            with self.subTest(incremental=incremental), self.synthetic_source() as f:
                source_before = f.source_bytes()
                staged = self.stage_verified_generation(f, incremental)
                if advance_source:
                    f.store.ingest(f.target, [{'original_id': 'a', 'label': 'Changed after verification',
                                               'text': 'newer synthetic version'}])
                    with self.assertRaisesRegex(archive.ExportError, '^collection_export_stale$'):
                        archive.verify_current_export(f.root, ['one'], staged.archive)
                self.assertIsNone(f.cache.active())
                with patch.object(full, 'GbrainReadClient', side_effect=AssertionError('unpublished engine read')):
                    with self.assertRaisesRegex(GbrainError, '^gbrain_cache_unavailable$'):
                        f.query('a')
                self.assertTrue(staged.root.exists())
                self.assertFalse((staged.root / 'ready.json').exists())

                accepted = f.cache.refresh(incremental=incremental)
                self.assertEqual(accepted['state'], 'published')
                self.assertEqual(accepted['removed_generations'], 1)
                if incremental:
                    self.assertEqual(accepted['update_mode'], 'full_rebuild')
                active = f.cache.active()
                projection = f.projection()
                self.assertNotEqual(projection.root, staged.root)
                self.assertFalse(staged.root.exists())
                self.assertEqual(list(f.cache.generations.iterdir()), [projection.root])
                self.assertEqual(active['fingerprint'], projection.fingerprint)
                ready = json.loads((projection.root / 'ready.json').read_text())
                self.assertEqual(ready['fingerprint'], projection.fingerprint)
                self.assertEqual(ready['manifest_sha256'],
                                 full._sha((projection.root / 'projection.json').read_bytes()))
                self.assertEqual(ready['verified_queries'], 4)
                result = f.query('a')
                self.assertTrue(result['ok'])
                label = next(n['label'] for n in result['nodes'] if n['id'] == identity('graph', 'a'))
                self.assertEqual(label, 'Changed after verification' if advance_source else 'Title a')
                if advance_source:
                    self.assertNotEqual(projection.fingerprint, staged.fingerprint)
                else:
                    self.assertEqual(projection.fingerprint, staged.fingerprint)
                    self.assertEqual(source_before, f.source_bytes())

    def test_verified_as_of_generation_without_active_or_ready_is_rebuilt(self):
        self.check_unpublished_generation(advance_source=False)

    def test_verified_as_of_generation_cannot_hide_a_later_source_version(self):
        self.check_unpublished_generation(advance_source=True)


if __name__ == '__main__':
    unittest.main()
