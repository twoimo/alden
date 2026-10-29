"""Generated file-set collect/capture/search integration; no live sources or models."""
from contextlib import ExitStack, contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unicodedata
import unittest
from unittest.mock import patch
import urllib.parse
import urllib.request
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import auto_reply_knowledge_graph as kg
from alden_abort import AldenCancelled
from alden_collect import capture_source, collect_target
from alden_collection import CollectionStore, encoded, identity, normalized_body
import alden_collection_retrieval as retrieval
from alden_file_set import SourceBodyError
from alden_knowledge_mcp import KnowledgeServer
import alden_original_text_index as original_index


def sha(data):
    return hashlib.sha256(data).hexdigest()


def specimen():
    head = 'headotter cafe\u0301headotter\r\n'.encode()
    boundary = b'boundarycrossover'
    before = head + (b'pad ' * 5000)[:15994 - len(head) - 1] + b' '
    assert len(before) == 15994
    return (before + boundary + b'\r\n' + b'fill ' * 1200 +
            '\r\ntailzebra\r\n청록혜성꼬리표\r\n은하표식😀우주표식\r\n'
            'cafe\u0301tailzebra\r\n'.encode())


class OriginalSearchIntegrationTests(unittest.TestCase):
    PROJECT = 'generated-original-integration'
    STRATEGIES = ('complete', 'indexed-lexical')
    QUERIES = ('headotter', 'tailzebra', 'boundarycrossover', '청록혜성꼬리표',
               '은하표식😀우주표식', 'caféheadotter', 'cafe\u0301headotter',
               'cafétailzebra', 'cafe\u0301tailzebra')

    def setUp(self):
        temporary = TemporaryDirectory(prefix='alden-original-integration-',
                                       dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.guards = []
        for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                          (socket, 'create_connection'), (urllib.request, 'urlopen'),
                          (kg, '_local_dense_embeddings'), (kg, '_active_dense_embedding_model'),
                          (kg, '_open_loopback_embedding_request')):
            self.guards.append(self.enterContext(patch.object(
                obj, name, side_effect=AssertionError('offline generated fixture only'))))
        connect, popen = sqlite3.connect, subprocess.Popen
        self.children = {}

        def private_connect(database, *args, **options):
            path = os.fspath(database)
            if path != ':memory:':
                if path.startswith('file:'):
                    path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
                if not Path(path).resolve().is_relative_to(self.root):
                    raise AssertionError('SQLite outside generated fixture')
            return connect(database, *args, **options)

        def parser_only(command, *args, **options):
            valid = (isinstance(command, (list, tuple)) and len(command) == 9
                and Path(command[0]).resolve() == Path(sys.executable).resolve()
                and list(command[1:6]) == ['-E', '-B', '-s',
                    str(Path(__file__).resolve().parents[1] / 'scripts/alden_file_set.py'), '--parse']
                and Path(command[6]).resolve().is_relative_to(self.root)
                and command[7] in ('.txt', '.md') and len(command[8]) == 64)
            cleanup = (isinstance(command, (list, tuple)) and len(command) == 5
                and list(command[:2]) == ['/bin/ps', '-g'] and command[2] in self.children
                and list(command[3:]) == ['-o', 'stat='])
            if not valid and not cleanup:
                raise AssertionError('only the owned pinned UTF-8 parser child is allowed')
            child = popen(command, *args, **options)
            if valid:
                self.children[str(child.pid)] = child
            return child

        self.enterContext(patch.object(sqlite3, 'connect', side_effect=private_connect))
        self.enterContext(patch.object(subprocess, 'Popen', side_effect=parser_only))
        # The real parser uses tempfile internally; keep its inputs task-owned too.
        self.enterContext(patch('tempfile.tempdir', str(self.root)))
        self.serial = 0

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()
        self.assertTrue(all(child.poll() is not None for child in self.children.values()))

    def build(self, name='case', documents=None, *, captured=True, project=None, root=None,
              source_id=None, target_kind='file-set'):
        project = project or self.PROJECT
        root = root or self.root / name
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        originals = root / (name + '-originals')
        originals.mkdir()
        documents = documents or {'primary': specimen()}
        declarations = []
        paths = {}
        for number, (file_id, data) in enumerate(documents.items()):
            path = originals / ('item-' + str(number) + '.txt')
            path.write_bytes(data)
            paths[file_id] = path
            declarations.append({'id': file_id, 'path': path.name, 'label': name + ' record ' + file_id})
        manifest = root / (name + '-declared.json')
        manifest.write_bytes(encoded({'schema': 1, 'files': declarations}))
        store = CollectionStore(root)
        source_id = source_id or name
        target = store.register(platform='files', original_id=source_id, kind=target_kind, label=name,
            projects=[project], config={'adapter': 'file-set', 'root': str(originals), 'path': str(manifest)})
        collected = collect_target(store, target)
        captured_result = capture_source(store, target) if captured else None
        ids = {file_id: identity('files', encoded([source_id, file_id]).decode()) for file_id in documents}
        with store.database() as db:
            versions = dict(db.execute('SELECT document_id,current_version FROM memberships WHERE target_id=?', (target,)))
        return dict(root=root, store=store, target=target, project=project, originals=originals,
            manifest=manifest, paths=paths, raw=documents, ids=ids, versions=versions,
            collected=collected, captured=captured_result)

    def arguments(self, state, file_id='primary'):
        document = state['ids'][file_id]
        return dict(projects=[state['project']], node_id=document, target_id=state['target'],
                    expected_version=state['versions'][document])

    def material(self, state, file_id='primary'):
        return state['store']._original_text_material(**self.arguments(state, file_id))

    def get(self, state, query, strategy='complete', **options):
        result = retrieval.retrieve(state['root'], query, projects=[state['project']],
            search_strategy=strategy, max_entities=10, max_relations=0, **options)
        for fact, proof in zip(result['facts'], result['fact_provenance']):
            if 'original_text_excerpt' in proof:
                self.assertEqual(proof['original_text_excerpt']['selection']['query_sha256'],
                                 sha(normalized_body(query).encode()))
                self.assertIn(normalized_body(query).casefold(), normalized_body(fact).casefold())
        return result

    def proofs(self, result):
        return [proof for proof in result['fact_provenance'] if proof['fact_type'] == 'entity']

    def ids(self, result):
        return {proof['entity_id'] for proof in self.proofs(result)}

    def mcp(self, state, query, strategy='complete'):
        server = KnowledgeServer(state['root'], [state['project']], search_strategy=strategy)
        reply = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'generated-test'}}})
        self.assertIn('result', reply)
        server.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        response = server.dispatch({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'alden_knowledge_search', 'arguments': {
                'projects': [state['project']], 'query': query, 'limit': 10}}})
        if 'result' in response and not response['result']['isError']:
            result = json.loads(response['result']['content'][0]['text'])
            for fact, proof in zip(result['facts'], result['fact_provenance']):
                if 'original_text_excerpt' in proof:
                    self.assertIn(normalized_body(query).casefold(), normalized_body(fact).casefold())
                    self.assertEqual(proof['original_text_excerpt']['selection']['query_sha256'],
                                     sha(normalized_body(query).encode()))
        return response

    def mcp_result(self, response):
        self.assertNotIn('error', response)
        self.assertFalse(response['result']['isError'], response)
        return json.loads(response['result']['content'][0]['text'])

    def assert_proof(self, state, file_id, proof, text):
        raw = state['raw'][file_id]
        material = self.material(state, file_id)
        projection = normalized_body(raw.decode())
        self.assertEqual(set(proof), {'schema', 'kind', 'binding', 'capture', 'input_bytes',
                                     'projection', 'excerpt', 'original_range', 'selection'})
        self.assertEqual(proof['schema'], 1)
        self.assertEqual(proof['kind'], 'verified-original-search-excerpt')
        self.assertEqual(proof['binding'], material['binding'])
        self.assertEqual(proof['capture'], material['capture'])
        self.assertEqual(proof['input_bytes'], len(raw))
        self.assertEqual(proof['projection'], {'policy': 'body-nfc-lines-v2',
            'sha256': sha(projection.encode()), 'characters': len(projection)})
        excerpt = proof['excerpt']
        selected = projection[excerpt['start_character']:excerpt['end_character']]
        self.assertEqual(text, selected)
        self.assertEqual(excerpt['sha256'], sha(text.encode()))
        self.assertEqual(set(proof['selection']), {'method', 'query_sha256'})
        self.assertIn(proof['selection']['method'], {'fts5-snippet', 'korean-literal', 'integer-literal'})
        self.assertLessEqual(len(text), 1200)
        self.assertEqual(excerpt['truncated'], len(text) < len(projection))
        location = proof['original_range']
        selected_raw = raw[location['start_bytes']:location['end_bytes']]
        self.assertEqual(location['sha256'], sha(selected_raw))
        if location['mapping'] == 'exact_projection_span':
            self.assertEqual(normalized_body(selected_raw.decode()), text)
        else:
            self.assertEqual(location['mapping'], 'covering_original_lines')
            self.assertIn(text, normalized_body(selected_raw.decode()))
        self.assertNotIn('_context', json.dumps(proof))

    def assert_retrieved_proof(self, state, result):
        self.assertEqual(result['projects'], [state['project']])
        self.assertEqual(result['search_mode'], 'bm25_only')
        self.assertTrue(self.proofs(result))
        by_id = {document: name for name, document in state['ids'].items()}
        for fact, proof in zip(result['facts'], result['fact_provenance']):
            if proof['fact_type'] != 'entity':
                continue
            self.assertTrue(proof['provenance_valid'])
            self.assertEqual(proof['body_source'], 'verified_retained_original_projection')
            excerpt = proof['original_text_excerpt']
            projection = normalized_body(state['raw'][by_id[proof['entity_id']]].decode())
            text = projection[excerpt['excerpt']['start_character']:excerpt['excerpt']['end_character']]
            self.assertTrue(fact.endswith(text), fact)
            self.assert_proof(state, by_id[proof['entity_id']], excerpt, text)

    @contextmanager
    def no_original_path_reads(self, state):
        originals = state['originals'].resolve()
        open_file = Path.open
        def guarded(path, *args, **options):
            if path.resolve().is_relative_to(originals) or path.resolve() == state['manifest'].resolve():
                raise AssertionError('retained search reopened declared originals')
            return open_file(path, *args, **options)
        with patch.object(Path, 'open', guarded):
            yield

    def test_real_collect_capture_automatically_indexes_complete_originals(self):
        state = self.build(documents={'primary': specimen(), 'duplicate': specimen()})
        self.assertEqual(state['collected']['records'], 2)
        receipt = state['captured']['original_text_index']
        self.assertEqual(receipt['state'], 'ready')
        self.assertEqual(receipt['eligible'], 2)
        self.assertEqual(receipt['indexed'] + receipt['reused'], 2)
        self.assertEqual(receipt['embeddings_generated'], 0)
        self.assertEqual(receipt['policy'], 'retained-original-search-v1')
        with sqlite3.connect(state['store'].root / 'retrieval.sqlite3') as db:
            rows = db.execute('SELECT document_id,target_id,version,input_sha256,body FROM file_original_text').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r[0] for r in rows}, set(state['ids'].values()))
            self.assertTrue(all(r[1] == state['target'] and r[2] == state['versions'][r[0]] for r in rows))
            self.assertEqual({r[3] for r in rows}, {sha(specimen())})
            self.assertTrue(all(r[4] == normalized_body(specimen().decode()) for r in rows))

    def test_head_tail_boundary_hangul_astral_nfd_nfc_both_retrieval_strategies(self):
        state = self.build(documents={'primary': specimen(), 'duplicate': specimen()})
        for query in self.QUERIES:
            for strategy in self.STRATEGIES:
                with self.subTest(query=query, strategy=strategy):
                    result = self.get(state, query, strategy)
                    self.assertEqual(self.ids(result), set(state['ids'].values()))
                    self.assert_retrieved_proof(state, result)
                    self.assertEqual(len(self.proofs(result)), 2)

    def test_direct_search_returns_verified_tail_excerpt_on_writer_and_reader(self):
        state = self.build()
        reader = CollectionStore.open_existing(state['root'])
        for query in self.QUERIES[1:]:
            for store in (state['store'], reader):
                with self.subTest(query=query, read_only=store.read_only):
                    rows = store.search(kg._fts_query_terms([query]), projects=[self.PROJECT])
                    self.assertEqual({row['id'] for row in rows}, set(state['ids'].values()))
                    for row in rows:
                        if 'headotter' not in query:
                            self.assertIn(normalized_body(query).casefold(), row['body'].casefold())
                            self.assert_proof(state, 'primary', row['original_text_excerpt'], row['body'])

    def test_graph_query_returns_verified_excerpt_on_writer_and_reader(self):
        state = self.build()
        reader = CollectionStore.open_existing(state['root'])
        for query in self.QUERIES:
            for store in (state['store'], reader):
                with self.subTest(query=query, read_only=store.read_only):
                    graph = store.graph_page(projects=[self.PROJECT], query=query, limit=10)
                    self.assertEqual({node['id'] for node in graph['nodes']}, set(state['ids'].values()))
                    node = graph['nodes'][0]
                    proof = node['source_metadata']['original_text_excerpt']
                    projection = normalized_body(specimen().decode())
                    text = projection[proof['excerpt']['start_character']:proof['excerpt']['end_character']]
                    self.assertIn(normalized_body(query).casefold(), text.casefold())
                    self.assertIn(text, node['description'])
                    self.assert_proof(state, 'primary', proof, text)

    def test_mcp_search_preserves_full_proof_for_both_strategies(self):
        state = self.build()
        for query in ('tailzebra', 'boundarycrossover', '청록혜성꼬리표', 'cafe\u0301tailzebra'):
            for strategy in self.STRATEGIES:
                with self.subTest(query=query, strategy=strategy):
                    result = self.mcp_result(self.mcp(state, query, strategy))
                    self.assertEqual(self.ids(result), set(state['ids'].values()))
                    self.assert_retrieved_proof(state, result)
                    self.assertNotIn('"_context"', json.dumps(result))

    def test_original_paths_disappear_without_fallback_or_cache_refresh(self):
        state = self.build()
        shutil.rmtree(state['originals'])
        state['manifest'].unlink()
        with self.no_original_path_reads(state):
            for strategy in self.STRATEGIES:
                result = self.get(state, 'caféheadotter', strategy)
                self.assert_retrieved_proof(state, result)
                self.assertEqual(self.ids(result), set(state['ids'].values()))
            for store in (state['store'], CollectionStore.open_existing(state['root'])):
                for surface in ('search', 'graph'):
                    with self.subTest(surface=surface, read_only=store.read_only):
                        rows = (store.search('tailzebra', projects=[self.PROJECT]) if surface == 'search' else
                                store.graph_page(projects=[self.PROJECT], query='tailzebra')['nodes'])
                        self.assertEqual(len(rows), 1)
            self.assert_retrieved_proof(state, self.mcp_result(self.mcp(state, 'tailzebra')))

    def test_initial_parser_coverage_stays_partial_after_complete_original_search(self):
        state = self.build()
        material = self.material(state)
        before = state['store'].graph_page(projects=[self.PROJECT], focus=state['ids']['primary'],
            details=True, expected_version=state['versions'][state['ids']['primary']])['details']
        coverage = {key: value for key, value in before['source_extraction'].items()
                    if key not in {'schema', 'kind', 'binding'}}
        self.assertEqual(coverage['status'], 'partial')
        self.assertFalse(coverage['body_complete'])
        self.assertNotIn('tailzebra', before['body'])
        for strategy in self.STRATEGIES:
            result = self.get(state, 'tailzebra', strategy)
            self.assert_retrieved_proof(state, result)
            self.assertEqual(self.proofs(result)[0]['original_body_coverage'], coverage)
        after = state['store'].graph_page(projects=[self.PROJECT], focus=state['ids']['primary'],
            details=True, expected_version=state['versions'][state['ids']['primary']])['details']
        self.assertEqual(after, before)
        self.assertEqual(material['data'], specimen())

    def test_query_only_without_original_cache_creates_nothing(self):
        state = self.build()
        cache = state['store'].root / 'retrieval.sqlite3'
        for path in state['store'].root.glob('retrieval.sqlite3*'):
            path.unlink()
        files_before = {str(p.relative_to(state['root'])): p.read_bytes() for p in state['root'].rglob('*')
                        if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
        with patch.object(CollectionStore, '_original_text_material', side_effect=AssertionError('query rebuilt material')):
            for strategy in self.STRATEGIES:
                self.assertEqual(self.ids(self.get(state, 'tailzebra', strategy)), set())
            self.assertEqual(state['store'].search('tailzebra', projects=[self.PROJECT]), [])
            self.assertEqual(state['store'].graph_page(projects=[self.PROJECT], query='tailzebra')['nodes'], [])
            self.assertEqual(self.ids(self.mcp_result(self.mcp(state, 'tailzebra'))), set())
        self.assertFalse(cache.exists())
        files_after = {str(p.relative_to(state['root'])): p.read_bytes() for p in state['root'].rglob('*')
                       if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
        self.assertEqual(files_after, files_before)

    def mutate(self, state, kind):
        store, target = state['store'], state['target']
        with store.database() as db:
            if kind == 'revoke':
                db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (target,))
            elif kind == 'config':
                config = store.target(target)['config']
                config['fixture-change'] = True
                db.execute('UPDATE targets SET config=? WHERE id=?', (encoded(config).decode(), target))
            elif kind == 'capture':
                cursor = json.loads(store.target(target)['cursor'])
                cursor.pop('source_capture')
                db.execute('UPDATE targets SET cursor=? WHERE id=?', (encoded(cursor).decode(), target))

    def test_revoked_config_changed_and_capture_removed_rows_never_admit_stale_tail(self):
        for kind in ('revoke', 'config', 'capture'):
            with self.subTest(kind=kind):
                state = self.build(name=kind)
                self.mutate(state, kind)
                for strategy in self.STRATEGIES:
                    self.assertEqual(self.ids(self.get(state, 'tailzebra', strategy)), set())
                for store in (state['store'], CollectionStore.open_existing(state['root'])):
                    with self.subTest(kind=kind, read_only=store.read_only):
                        self.assertEqual(store.search('tailzebra', projects=[self.PROJECT]), [])
                        self.assertEqual(store.graph_page(projects=[self.PROJECT], query='tailzebra')['nodes'], [])

    def test_new_version_rejects_old_candidate_and_queries_do_not_repair_index(self):
        state = self.build()
        reader = CollectionStore.open_existing(state['root'])
        candidate = original_index.scoped_original_candidates(reader, [self.PROJECT], 'tailzebra')[0]
        state['paths']['primary'].write_bytes(specimen().replace(b'tailzebra', b'newversiontail'))
        collect_target(state['store'], state['target'])
        for strategy in self.STRATEGIES:
            self.assertEqual(self.ids(self.get(state, 'tailzebra', strategy)), set())
            self.assertEqual(self.ids(self.get(state, 'newversiontail', strategy)), set())
        with self.assertRaises((SourceBodyError, original_index.OriginalTextIndexError, RuntimeError)):
            original_index.resolve_original_excerpt(state['store'], [self.PROJECT], candidate, 'tailzebra')
        captured = capture_source(state['store'], state['target'])
        self.assertEqual(captured['original_text_index']['state'], 'ready')
        for strategy in self.STRATEGIES:
            result = self.get(state, 'newversiontail', strategy)
            self.assertEqual(self.ids(result), set(state['ids'].values()))
            self.assertNotEqual(self.proofs(result)[0]['source_version'], candidate['version'])

    def test_capture_archive_and_index_projection_tamper_fail_closed(self):
        for kind in ('archive', 'projection'):
            with self.subTest(kind=kind):
                state = self.build(name=kind)
                if kind == 'archive':
                    manifest = json.loads((state['store'].root / 'source-captures' /
                                           state['captured']['manifest']).read_bytes())
                    archive = state['store'].root / 'source-captures' / manifest['source_file']
                    archive.write_bytes(archive.read_bytes() + b' ')
                else:
                    with sqlite3.connect(state['store'].root / 'retrieval.sqlite3') as db:
                        db.execute("UPDATE file_original_text SET projection_sha256=?", ('0' * 64,))
                for strategy in self.STRATEGIES:
                    with self.assertRaises((SourceBodyError, original_index.OriginalTextIndexError, RuntimeError)):
                        self.get(state, 'tailzebra', strategy)
                response = self.mcp(state, 'tailzebra')
                self.assertTrue('error' in response or response['result']['isError'], response)
                self.assertNotIn('tailzebra', json.dumps(response))
                self.assertNotIn('alden_global_abort', json.dumps(response))

    def test_graph_target_version_and_filter_before_offset(self):
        state = self.build(documents={name: specimen() for name in ('a', 'b', 'c')})
        outside = self.build(name='outside', documents={'noise': specimen()}, project='outside', root=state['root'])
        for store in (state['store'], CollectionStore.open_existing(state['root'])):
            with self.subTest(read_only=store.read_only):
                all_rows = store.graph_page(projects=[self.PROJECT], query='tailzebra', target_id=state['target'], limit=10)
                expected = [node['id'] for node in all_rows['nodes']]
                self.assertEqual(set(expected), set(state['ids'].values()))
                self.assertNotIn(outside['ids']['noise'], expected)
                for offset, document in enumerate(expected):
                    page = store.graph_page(projects=[self.PROJECT], query='tailzebra', target_id=state['target'], limit=1, offset=offset)
                    self.assertEqual([node['id'] for node in page['nodes']], [document])
                    proof = page['nodes'][0]['source_metadata']['original_text_excerpt']
                    self.assertEqual(proof['binding']['target_id'], state['target'])
                    self.assertEqual(proof['binding']['version'], state['versions'][document])
                self.assertEqual(store.graph_page(projects=[self.PROJECT], query='tailzebra', target_id=state['target'], offset=3)['nodes'], [])
                self.assertEqual(store.graph_page(projects=[self.PROJECT], query='tailzebra', platform='threads')['nodes'], [])
                self.assertEqual(store.graph_page(projects=[self.PROJECT], query='tailzebra', since=4102444800)['nodes'], [])
                self.assertEqual(store.graph_page(projects=[self.PROJECT], query='tailzebra', until=1)['nodes'], [])

    def test_same_document_other_target_version_cannot_lend_its_original_tail(self):
        state = self.build(name='old-target', source_id='shared-file-identity')
        revised = self.build(name='new-target', source_id='shared-file-identity',
            target_kind='second-file-set', root=state['root'],
            documents={'primary': specimen().replace(b'tailzebra', b'replacementtail')})
        document = state['ids']['primary']
        self.assertEqual(document, revised['ids']['primary'])
        self.assertNotEqual(state['versions'][document], revised['versions'][document])
        for store in (state['store'], CollectionStore.open_existing(state['root'])):
            with self.subTest(read_only=store.read_only):
                old_page = store.graph_page(projects=[self.PROJECT], query='tailzebra', target_id=state['target'])
                self.assertEqual([n['id'] for n in old_page['nodes']], [document])
                proof = old_page['nodes'][0]['source_metadata']['original_text_excerpt']
                self.assertEqual(proof['binding']['target_id'], state['target'])
                self.assertEqual(proof['binding']['version'], state['versions'][document])
                self.assertEqual(store.graph_page(projects=[self.PROJECT], query='tailzebra', target_id=revised['target'])['nodes'], [])
                self.assertEqual(store.graph_page(projects=[self.PROJECT], query='tailzebra')['nodes'], [])
        for strategy in self.STRATEGIES:
            self.assertEqual(self.ids(self.get(state, 'tailzebra', strategy)), set())

    def test_capture_storage_commit_survives_explicit_index_failure(self):
        state = self.build(captured=False)
        with patch.object(retrieval, 'index_original_text', side_effect=original_index.OriginalTextIndexError(
                'collection_original_index_budget')):
            receipt = capture_source(state['store'], state['target'])
        self.assertEqual(receipt['state'], 'complete')
        self.assertEqual(receipt['original_text_index'], {'state': 'failed',
            'error': 'collection_original_index_budget', 'capture_committed': True})
        self.assertEqual(self.material(state)['data'], specimen())
        cursor = json.loads(state['store'].target(state['target'])['cursor'])
        self.assertEqual(cursor['source_capture']['manifest'], receipt['manifest'])
        for strategy in self.STRATEGIES:
            self.assertEqual(self.ids(self.get(state, 'tailzebra', strategy)), set())

    def test_cancellation_and_late_original_context_guard_prevent_publication(self):
        state = self.build()
        for strategy in self.STRATEGIES:
            with self.subTest(strategy=strategy, stage='cancelled'):
                with self.assertRaises((AldenCancelled, RuntimeError)):
                    self.get(state, 'tailzebra', strategy, cancelled=lambda: True)
            original_verify = retrieval._verify_retrieval_result
            def revoke_before_publication(store, projects, authority, eligible, ranked, proofs, relations, original_contexts=()):
                self.assertTrue(original_contexts)
                self.mutate(state, 'revoke')
                return original_verify(store, projects, authority, eligible, ranked, proofs, relations, original_contexts)
            with self.subTest(strategy=strategy, stage='late-revoke'):
                with patch.object(retrieval, '_verify_retrieval_result', revoke_before_publication):
                    with self.assertRaisesRegex(RuntimeError, 'collection_source_authorization_changed'):
                        self.get(state, 'tailzebra', strategy)
                with state['store'].database() as db:
                    db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=?", (state['target'],))

    def test_late_archive_tamper_after_excerpt_is_rechecked_for_direct_and_mcp(self):
        for surface in ('retrieve', 'mcp'):
            with self.subTest(surface=surface):
                state = self.build(name='late-' + surface)
                original_verify = retrieval._verify_retrieval_result
                guarded = []
                def tamper_then_verify(store, projects, authority, eligible, ranked, proofs, relations, original_contexts=()):
                    self.assertTrue(original_contexts)
                    guarded.append(True)
                    context = original_contexts[0]
                    name = context['archives'][0][0]
                    archive = store.root / 'source-captures' / name
                    archive.write_bytes(archive.read_bytes() + b' ')
                    return original_verify(store, projects, authority, eligible, ranked, proofs, relations, original_contexts)
                with patch.object(retrieval, '_verify_retrieval_result', tamper_then_verify):
                    if surface == 'retrieve':
                        with self.assertRaises((SourceBodyError, RuntimeError)):
                            self.get(state, 'tailzebra')
                    else:
                        response = self.mcp(state, 'tailzebra')
                        self.assertTrue('error' in response or response['result']['isError'], response)
                        self.assertNotIn('tailzebra', json.dumps(response))
                        self.assertNotIn('alden_global_abort', json.dumps(response))
                self.assertEqual(guarded, [True])

    def test_cancellation_after_resolving_original_excerpt_cannot_return_facts(self):
        state = self.build()
        resolve = original_index.resolve_original_excerpt
        for strategy in self.STRATEGIES:
            stopped = [False]
            def resolve_then_cancel(*args, **options):
                result = resolve(*args, **options)
                stopped[0] = True
                return result
            with self.subTest(strategy=strategy), patch.object(original_index, 'resolve_original_excerpt', resolve_then_cancel):
                with self.assertRaises((AldenCancelled, RuntimeError)):
                    self.get(state, 'tailzebra', strategy, cancelled=lambda: stopped[0])
                self.assertTrue(stopped[0])

    def test_original_authority_accepts_partial_context_without_lookup_and_rejects_mutations(self):
        from alden_abort import AbortController
        import alden_knowledge_authority as authority

        @contextmanager
        def no_lookup():
            with ExitStack() as stack:
                guards = [stack.enter_context(patch.object(owner, name,
                    side_effect=AssertionError('authority must not search or rebuild'))) for owner, name in (
                    (retrieval, 'retrieve'), (retrieval, 'index_text'), (retrieval, 'index_original_text'),
                    (original_index, 'scoped_original_candidates'), (original_index, 'attach_original_index'),
                    (original_index, 'resolve_original_excerpt'), (kg, 'retrieve_knowledge_bundle'))]
                yield
                for guard in guards:
                    guard.assert_not_called()

        for mutation in ('none', 'capture', 'config', 'excerpt-coordinate', 'raw-coordinate', 'late-config'):
            with self.subTest(mutation=mutation):
                state = self.build(name='authority-' + mutation)
                result = self.get(state, 'tailzebra', max_original_excerpt_chars=400)
                context = {'graph_facts': result['facts'], 'graph_provenance': result['fact_provenance'],
                           'quoted_history': []}
                self.assertEqual(context['graph_provenance'][0]['original_body_coverage']['status'], 'partial')
                self.assertFalse(context['graph_provenance'][0]['original_body_coverage']['body_complete'])
                token = AbortController(state['root']).token()
                with no_lookup():
                    receipt = authority.capture_authority(state['root'], context, token)
                    self.assertIsNone(authority.verify_authority(state['root'], receipt, token))
                    if mutation in ('capture', 'config'):
                        self.mutate(state, mutation)
                        with self.assertRaises(authority.KnowledgeAuthorityError) as rejected:
                            authority.verify_authority(state['root'], receipt, token)
                        self.assertIn(str(rejected.exception), (authority.CHANGED, authority.UNAVAILABLE))
                    elif mutation in ('excerpt-coordinate', 'raw-coordinate'):
                        changed = copy.deepcopy(context)
                        proof = changed['graph_provenance'][0]['original_text_excerpt']
                        if mutation == 'excerpt-coordinate':
                            proof['excerpt']['start_character'] += 1
                        else:
                            proof['original_range']['start_bytes'] += 1
                        with self.assertRaises(authority.KnowledgeAuthorityError):
                            authority.capture_authority(state['root'], changed, token)
                    elif mutation == 'late-config':
                        original = authority._original_excerpt
                        checked = []
                        def read_then_change(*args, **options):
                            value = original(*args, **options)
                            checked.append(True)
                            self.mutate(state, 'config')
                            return value
                        with patch.object(authority, '_original_excerpt', read_then_change):
                            with self.assertRaises(authority.KnowledgeAuthorityError):
                                authority.capture_authority(state['root'], context, token)
                        self.assertEqual(checked, [True])
                    else:
                        self.assertEqual(context['graph_provenance'], result['fact_provenance'])

    def test_real_voice_reference_keeps_tail_with_400_excerpt_and_ledger_allowlist(self):
        from alden_abort import AbortController
        import alden_chat_turns as turns
        import alden_knowledge_authority as authority
        import alden_voice as voice
        state = self.build(name='voice-reference')
        query = self.PROJECT + ' 저장된 자료에서 tailzebra 찾아줘'
        token = AbortController(state['root']).token()
        selected = []
        with patch.object(kg, 'retrieve_knowledge_bundle', wraps=kg.retrieve_knowledge_bundle) as lookup:
            reference, metrics = voice._voice_knowledge_reference(query, [], state['root'], token,
                                                                  authority_observer=selected.append)
        self.assertEqual(metrics['state'], 'found')
        self.assertEqual(lookup.call_args.kwargs['max_original_excerpt_chars'], 400)
        context = json.loads(reference.split('<quoted_local_history>')[1].split('</quoted_local_history>')[0])
        self.assertEqual(len(context['graph_facts']), 1)
        self.assertIn('tailzebra', context['graph_facts'][0])
        self.assertLessEqual(len(context['graph_facts'][0]), 600)
        proof = context['graph_provenance'][0]
        self.assertEqual(proof['original_body_coverage']['status'], 'partial')
        self.assertEqual(proof['body_source'], 'verified_retained_original_projection')
        excerpt = proof['original_text_excerpt']
        self.assertLessEqual(excerpt['excerpt']['end_character'] - excerpt['excerpt']['start_character'], 400)
        projection = normalized_body(specimen().decode())
        text = projection[excerpt['excerpt']['start_character']:excerpt['excerpt']['end_character']]
        self.assertTrue(context['graph_facts'][0].endswith(text))
        self.assert_proof(state, 'primary', excerpt, text)
        self.assertEqual(metrics['graph_provenance'][0]['original_text_excerpt'], excerpt)
        self.assertEqual(selected[0]['graph_provenance'][0]['original_text_excerpt'], excerpt)
        serialized = json.loads(turns._provenance(metrics['graph_provenance']))
        self.assertEqual(serialized[0]['original_text_excerpt'], excerpt)
        with patch.object(retrieval, 'retrieve', side_effect=AssertionError('authority repeated lookup')):
            receipt = authority.capture_authority(state['root'], selected[0], token)
            self.assertIsNone(authority.verify_authority(state['root'], receipt, token))

    def test_typed_http_mock_reads_actual_tail_and_persists_original_proof(self):
        import alden_chat as chat
        import alden_voice as voice
        from alden_history import database
        from auto_reply_ondevice import QWEN38_27B_MODEL_ID
        nonce = 'nonce:' + uuid.uuid4().hex
        raw = specimen().replace(b'tailzebra', b'tailzebra ' + nonce.encode(), 1)
        state = self.build(name='typed-http', documents={'primary': raw})
        value = {'request_id': uuid.uuid4().hex, 'conversation_id': None,
                 'text': self.PROJECT + ' 저장된 자료에서 tailzebra 찾아줘', 'images': []}
        token = chat.InputToken(state['root'], str(uuid.uuid4()))
        payloads = []
        model = QWEN38_27B_MODEL_ID.removeprefix('mlx/')

        class Response(io.BytesIO):
            def __init__(self, body):
                super().__init__(json.dumps(body, ensure_ascii=False).encode())
                self.headers = {'Content-Type': 'application/json'}

        def fake_http(request, *, timeout):
            payload = json.loads(request.data)
            payloads.append(payload)
            self.assertFalse(payload['stream'])
            self.assertTrue(request.full_url.endswith('/chat/completions'))
            if len(payloads) == 1:
                self.assertNotIn(nonce, json.dumps(payload))
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'read-1', 'type': 'function', 'function': {
                        'name': 'lookup_requested_knowledge', 'arguments': '{}'}}]}
                finish = 'tool_calls'
            else:
                self.assertEqual([m['role'] for m in payload['messages'][-3:]], ['user', 'assistant', 'tool'])
                tool = payload['messages'][-1]
                self.assertEqual(tool['tool_call_id'], 'read-1')
                self.assertIn(nonce, tool['content'])
                self.assertIn('original_text_excerpt', tool['content'])
                found = re.search(r'nonce:[0-9a-f]{32}', tool['content'])
                self.assertIsNotNone(found)
                message = {'role': 'assistant', 'content': found.group()}
                finish = 'stop'
            return Response({'id': 'reply-' + str(len(payloads)), 'model': model,
                'choices': [{'index': 0, 'finish_reason': finish, 'message': message}],
                'usage': {'prompt_tokens': 17, 'completion_tokens': 9}})

        @contextmanager
        def fake_lease(root):
            self.assertEqual(Path(root), state['root'])
            yield

        with patch.object(voice, '_local_urlopen', side_effect=fake_http), \
             patch.object(voice.LocalMlxLlm, '_require_selected_model_ready', return_value=None), \
             patch('local_mlx_gateway.mlx_model_request_lease', side_effect=fake_lease):
            result = chat.process_input(state['root'], value, token,
                select_model=lambda _root: {'transport': 'local', 'model': QWEN38_27B_MODEL_ID,
                    'base_url': 'http://127.0.0.1:11234/v1', 'reasoning_effort': None})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['reply'], nonce)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(result['generation']['tool_loop']['read_count'], 1)
        self.assertEqual(result['generation']['tool_loop']['hop_count'], 2)
        with database(state['root']) as db:
            saved = db.execute('SELECT provenance FROM voice_sources WHERE session_id=? AND turn_id=?',
                (result['conversation_id'], result['turn_id'])).fetchone()
        self.assertIsNotNone(saved)
        proofs = json.loads(saved[0])
        self.assertEqual(proofs[0]['body_source'], 'verified_retained_original_projection')
        self.assertEqual(proofs[0]['original_body_coverage']['status'], 'partial')
        excerpt = proofs[0]['original_text_excerpt']
        body = normalized_body(raw.decode())
        text = body[excerpt['excerpt']['start_character']:excerpt['excerpt']['end_character']]
        self.assertIn(nonce, text)
        self.assert_proof(state, 'primary', excerpt, text)


if __name__ == '__main__':
    unittest.main()
