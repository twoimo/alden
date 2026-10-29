"""Owned retained-original graph reads through strict affinity transactions."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tests import test_alden_original_search_integration as integration
from alden_collection import CollectionStore, digest, encoded
import alden_collection_retrieval as retrieval
import alden_layout_affinity as layout
import alden_original_text_index as original_index
import alden_semantic_affinity as semantic


class OriginalLayoutAffinityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = integration.OriginalSearchIntegrationTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def build(self, name='affinity', *, legacy=False):
        raw = integration.specimen().replace(b'tailzebra', b'affinitytailneedle')
        state = self.fixture.build(name=name, captured=not legacy, documents={
            'alpha': raw, 'beta': raw.replace(b'affinitytailneedle', b'decoytailneedle')})
        cache = state['store'].root / 'retrieval.sqlite3'
        if not legacy:
            receipt = retrieval.index_text(state['root'], [state['project']])
            self.assertEqual(receipt['state'], 'ready')
            self.assertEqual(receipt['original_text_index']['state'], 'ready')
        profile = semantic.E5Profile()
        with state['store'].database() as source:
            rows = source.execute('''SELECT m.document_id,v.id,v.raw_sha256,v.label,v.body
                FROM memberships m JOIN versions v ON v.id=m.current_version
                WHERE m.target_id=? ORDER BY m.document_id''', (state['target'],)).fetchall()
        with sqlite3.connect(cache) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS texts(document_id TEXT,version TEXT,base_hash TEXT,
                raw_sha256 TEXT,extraction TEXT,label TEXT,body TEXT,body_source TEXT,
                PRIMARY KEY(document_id,version))''')
            if legacy:
                db.execute('CREATE VIRTUAL TABLE text_fts USING fts5(document_id UNINDEXED,version UNINDEXED,label,body)')
            db.execute('''CREATE TABLE vectors(document_id TEXT,version TEXT,text_hash TEXT,
                model TEXT,endpoint TEXT,encoding TEXT,vector BLOB,PRIMARY KEY(document_id,version))''')
            for document, version, raw_sha, label, body in rows:
                text_hash = digest((label + '\n' + body).encode())
                if legacy:
                    db.execute('INSERT INTO texts VALUES(?,?,?,?,?,?,?,?)',
                        (document, version, text_hash, raw_sha, retrieval.TEXT_VERSION, label, body, 'stored_body'))
                    db.execute('INSERT INTO text_fts VALUES(?,?,?,?)', (document, version, label, body))
                # Generated unit vectors describe only the initial parser projection.
                # No embedding/model function is invoked by this fixture.
                db.execute('INSERT INTO vectors VALUES(?,?,?,?,?,?,?)',
                    (document, version, text_hash, profile.model, profile.endpoint, profile.encoding,
                     struct.pack('<384f', 1.0, *([0.0] * 383))))
        state['cache'] = cache
        return state

    def snapshot(self, state):
        return {str(p.relative_to(state['root'])): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in state['root'].rglob('*') if p.is_file()
                and not p.name.endswith(('-wal', '-shm'))}

    def request(self, state, *, owner=None, search='affinitytailneedle', **options):
        return layout.read_action(state['root'], json.dumps({
            'projects': [state['project']], 'search': search, 'limit': 10}), transactions=owner, **options)

    def refs(self, state, search):
        page = CollectionStore.open_existing(state['root']).graph_page(
            projects=[state['project']], query=search, limit=10)
        self.assertTrue(page['ok'])
        return [{key: node[key] for key in ('id', 'source_version', 'source_target')} for node in page['nodes']]

    def assert_alive(self, owner, collection, cache):
        self.assertIs(owner.collection, collection)
        self.assertIs(owner.cache, cache)
        self.assertTrue(collection.in_transaction)
        self.assertTrue(cache.in_transaction)
        self.assertEqual(collection.execute('SELECT 1').fetchone()[0], 1)
        self.assertEqual(cache.execute('SELECT 1').fetchone()[0], 1)

    def assert_rejected(self, result, *, code=None):
        self.assertFalse(result['ok'], result)
        self.assertIsInstance(result.get('error'), str)
        if code is not None:
            self.assertEqual(result['error'], code)
        self.assertNotIn('layout_affinity', result, 'failed graph read must not publish stale refs/candidates')

    def test_legacy_cache_without_original_tables_keeps_empty_and_filtered_queries(self):
        state = self.build(legacy=True)
        with sqlite3.connect(state['cache']) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'file_original_%'").fetchall(), [])
        before = self.snapshot(state)
        expected = {query: self.refs(state, query) for query in ('', 'unmatched-probe', 'beta')}
        with layout.read_transactions(state['root']) as owner:
            collection, cache = owner.collection, owner.cache
            denied = owner.metadata['denied_write_attempts']
            for db in (collection, cache):
                self.assertTrue({row[1] for row in db.execute('PRAGMA database_list')} <= {'main', 'temp'})
            self.assertIsNone(original_index.attach_original_index(owner.store, collection))
            self.assertIsNone(semantic._validate_reads(owner.store, collection, cache))
            raw = semantic._affinity_on_reads(owner.store, collection, cache,
                [state['project']], expected[''], budgets=owner.guard.budgets, guard=owner.guard)
            self.assertEqual(raw['coverage']['usable_vector_nodes'], len(expected['']))
            self.assertEqual({node['id'] for node in raw['nodes']}, {node['id'] for node in expected['']})
            with self.assertRaises(original_index.OriginalTextIndexError):
                original_index._bind_original_index(owner.store, collection)
            for query in expected:
                with self.subTest(query=query):
                    result = self.request(state, owner=owner, search=query)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['layout_affinity']['input_nodes'], expected[query])
                    self.assert_alive(owner, collection, cache)
                    owner.validate()
            for db in (collection, cache):
                self.assertTrue({row[1] for row in db.execute('PRAGMA database_list')} <= {'main', 'temp'})
            self.assertEqual(owner.metadata['denied_write_attempts'], denied,
                             'sealed legacy absence must not attempt ATTACH/DETACH after initialization')
        self.assertEqual(self.snapshot(state), before)
        with sqlite3.connect(state['cache']) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'file_original_%'").fetchall(), [])

    def test_real_tail_refs_match_regular_graph_and_reused_owner_stays_alive(self):
        state = self.build()
        expected = self.refs(state, 'affinitytailneedle')
        self.assertEqual([ref['id'] for ref in expected], [state['ids']['alpha']])
        before = self.snapshot(state)
        ordinary = self.request(state)
        self.assertTrue(ordinary['ok'], ordinary)
        self.assertEqual(ordinary['layout_affinity']['input_nodes'], expected)
        with layout.read_transactions(state['root']) as owner:
            collection, cache = owner.collection, owner.cache
            denied = owner.metadata['denied_write_attempts']
            for _ in range(2):
                result = self.request(state, owner=owner)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['layout_affinity']['input_nodes'], expected)
                self.assertNotEqual(result['layout_affinity'].get('reason'), 'collection_original_index_invalid')
                self.assert_alive(owner, collection, cache)
                owner.validate()
            self.assertEqual(owner.metadata['denied_write_attempts'], denied,
                             'known original alias reuse must not attempt another ATTACH')
        self.assertFalse(owner.active)
        self.assertEqual(self.snapshot(state), before)

    def test_only_exact_attested_readonly_alias_is_reused_and_sql_guards_remain(self):
        state = self.build()
        before = self.snapshot(state)
        with layout.read_transactions(state['root']) as owner:
            databases = {row[1]: Path(row[2]).resolve() for row in owner.collection.execute('PRAGMA database_list')
                         if row[1] != 'temp'}
            self.assertEqual(databases, {'main': state['store'].path.resolve(),
                                       'original_search': state['cache'].resolve()})
            with self.assertRaises(original_index.OriginalTextIndexError):
                original_index._bind_original_index(owner.store, owner.collection)
            self.assertEqual(original_index.attach_original_index(owner.store, owner.collection), 'original_search')
            plain = CollectionStore.open_existing(state['root'])
            with self.assertRaises(original_index.OriginalTextIndexError):
                original_index.attach_original_index(plain, owner.collection)
            with sqlite3.connect(state['store'].path.as_uri() + '?mode=ro', uri=True) as other:
                other.execute('ATTACH DATABASE ? AS original_search', (state['cache'].as_uri() + '?mode=ro',))
                with self.assertRaises(original_index.OriginalTextIndexError):
                    original_index.attach_original_index(owner.store, other)
            for db in (owner.collection, owner.cache):
                statements = ["ATTACH ':memory:' AS arbitrary", 'DETACH original_search',
                    "INSERT INTO file_original_meta(key,value) VALUES('forbidden','write')",
                    'PRAGMA query_only=OFF', 'PRAGMA journal_mode=DELETE', 'PRAGMA wal_checkpoint']
                if db is owner.collection:
                    statements[2] = "INSERT INTO original_search.file_original_meta(key,value) VALUES('forbidden','write')"
                    statements.append("ATTACH '" + state['cache'].as_uri() + "?mode=ro' AS another_alias")
                for sql in statements:
                    with self.subTest(connection='collection' if db is owner.collection else 'cache', sql=sql):
                        with self.assertRaises(sqlite3.Error):
                            db.execute(sql)
            owner.validate()
            self.assertTrue(self.request(state, owner=owner)['ok'])
        self.assertEqual(self.snapshot(state), before)

    def test_material_source_and_text_budgets_stop_at_graph_boundary_without_writes(self):
        for field in ('max_source_bytes', 'max_text_bytes'):
            with self.subTest(budget=field):
                state = self.build(name='budget-' + field)
                before = self.snapshot(state)
                budgets = replace(semantic.Budgets(), **{field: 1})
                with layout.read_transactions(state['root'], budgets=budgets) as owner:
                    collection, cache = owner.collection, owner.cache
                    protected = {(p.stat().st_dev, p.stat().st_ino)
                        for folder in (state['store'].blobs, state['store'].root / 'source-captures')
                        for p in folder.iterdir() if p.is_file()}
                    data_reads = []
                    actual_fdopen, actual_path_open = os.fdopen, Path.open
                    class ReadProbe:
                        def __init__(probe, handle):
                            probe.handle = handle
                        def read(probe, *args, **options):
                            data_reads.append(True)
                            return probe.handle.read(*args, **options)
                        def __enter__(probe):
                            probe.handle.__enter__()
                            return probe
                        def __exit__(probe, *args):
                            return probe.handle.__exit__(*args)
                        def __getattr__(probe, key):
                            return getattr(probe.handle, key)
                    def track(handle):
                        info = os.fstat(handle.fileno())
                        return ReadProbe(handle) if (info.st_dev, info.st_ino) in protected else handle
                    def fdopen(fd, *args, **options):
                        return track(actual_fdopen(fd, *args, **options))
                    def path_open(path, *args, **options):
                        return track(actual_path_open(path, *args, **options))
                    with patch.object(os, 'fdopen', fdopen), patch.object(Path, 'open', path_open):
                        result = self.request(state, owner=owner, budgets=budgets)
                    self.assert_rejected(result, code='affinity_' + field.removeprefix('max_') + '_budget')
                    if field == 'max_source_bytes':
                        self.assertEqual(data_reads, [], 'source budget must reject before archive/blob data read')
                    self.assert_alive(owner, collection, cache)
                self.assertEqual(self.snapshot(state), before)

    def test_real_material_wall_cpu_rss_and_cancel_failures_keep_exact_stop_reason(self):
        actual = CollectionStore._original_text_material
        for kind in ('wall', 'cpu', 'rss', 'cancel'):
            with self.subTest(kind=kind):
                state = self.build(name='stop-' + kind)
                before = self.snapshot(state)
                stopped = [False]
                touched = []
                with layout.read_transactions(state['root'], cancelled=lambda: stopped[0]) as owner:
                    started, cpu_started = owner.guard.started, owner.guard.cpu_started
                    rss_patch = None
                    def stop_after_real_read(store, *args, **options):
                        nonlocal rss_patch
                        value = actual(store, *args, **options)
                        if not touched:
                            touched.append(True)
                            if kind == 'wall':
                                owner.guard.started -= owner.guard.budgets.wall_seconds + 1
                            elif kind == 'cpu':
                                owner.guard.cpu_started -= owner.guard.budgets.cpu_seconds + 1
                            elif kind == 'rss':
                                rss_patch = patch.object(semantic, '_rss', return_value=owner.guard.budgets.max_rss_bytes + 1)
                                rss_patch.start()
                            else:
                                stopped[0] = True
                        return value
                    try:
                        with patch.object(CollectionStore, '_original_text_material', stop_after_real_read):
                            result = self.request(state, owner=owner)
                        expected = 'affinity_cancelled' if kind == 'cancel' else 'affinity_' + kind + '_budget'
                        self.assert_rejected(result, code=expected)
                        self.assertEqual(touched, [True], 'exercise actual retained material before stopping')
                    finally:
                        stopped[0] = False
                        owner.guard.started, owner.guard.cpu_started = started, cpu_started
                        if rss_patch is not None:
                            rss_patch.stop()
                self.assertEqual(self.snapshot(state), before)

    def test_pinned_owner_snapshot_cannot_hide_current_config_capture_or_revoke(self):
        for kind in ('config', 'capture', 'revoke'):
            with self.subTest(kind=kind):
                state = self.build(name='snapshot-' + kind)
                self.assertEqual(len(self.refs(state, 'affinitytailneedle')), 1)
                with layout.read_transactions(state['root']) as owner:
                    collection, cache = owner.collection, owner.cache
                    self.assertTrue(self.request(state, owner=owner)['ok'],
                                    'freshness rejection requires a working unchanged-owner control')
                    self.fixture.mutate(state, kind)
                    after_mutation = self.snapshot(state)
                    result = self.request(state, owner=owner)
                    self.assert_rejected(result)
                    self.assert_alive(owner, collection, cache)
                self.assertEqual(self.snapshot(state), after_mutation)

    def test_strict_semantic_validation_rejects_product_alias_without_exact_owner(self):
        state = self.build(name='strict-semantic-owner')
        before = self.snapshot(state)
        with layout.read_transactions(state['root']) as owner:
            self.assertIn('original_search', {row[1] for row in owner.collection.execute('PRAGMA database_list')})
            with self.assertRaisesRegex(ValueError, '^affinity_read_transaction_path_mismatch$'):
                semantic._validate_reads(owner.store, owner.collection, owner.cache)
            with patch.object(owner, 'validate', wraps=owner.validate) as validate:
                self.assertIsNone(semantic._validate_reads(owner.store, owner.collection, owner.cache, owner=owner))
                validate.assert_called_once()
            fake = SimpleNamespace(store=owner.store, collection=owner.collection,
                                   cache=owner.cache, validate=owner.validate)
            with self.assertRaisesRegex(ValueError, '^affinity_verified_read_transactions_required$'):
                semantic._validate_reads(owner.store, owner.collection, owner.cache, owner=fake)
            self.assert_alive(owner, owner.collection, owner.cache)
        self.assertEqual(self.snapshot(state), before)

    def test_late_mutation_after_verified_material_is_freshly_rechecked_outside_snapshot(self):
        actual = CollectionStore._original_text_material
        for kind in ('config', 'capture', 'revoke'):
            with self.subTest(kind=kind):
                state = self.build(name='late-' + kind)
                changed = []
                with layout.read_transactions(state['root']) as owner:
                    collection, cache = owner.collection, owner.cache
                    def mutate_after_read(store, *args, **options):
                        value = actual(store, *args, **options)
                        if not changed:
                            self.fixture.mutate(state, kind)
                            changed.append(True)
                        return value
                    with patch.object(CollectionStore, '_original_text_material', mutate_after_read):
                        result = self.request(state, owner=owner)
                    self.assertEqual(changed, [True], 'must reach genuine material before late mutation')
                    self.assert_rejected(result)
                    self.assert_alive(owner, collection, cache)


if __name__ == '__main__':
    unittest.main()
