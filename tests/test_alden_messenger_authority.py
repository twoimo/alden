"""Explicit multi-source authority at real read-only API/retrieval/MCP boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import socket
import shutil
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_alden_messenger_access as access_tests
from tests.test_alden_messenger_frontier import candidate, item, journal_rows
from alden_collection import CollectionStore, identity, read_action
from alden_collection_retrieval import retrieve
from alden_knowledge_mcp import KnowledgeServer
import auto_reply_knowledge_graph as kg


class MessengerAuthorityTests(unittest.TestCase):
    def test_wrong_root_writer_does_not_create_a_directory(self):
        other = self.root.parent / 'uncreated-store'
        self.assertFalse(other.exists())
        with self.assertRaisesRegex(ValueError, 'root_mismatch'):
            CollectionStore(other, **self.guards)
        self.assertFalse(other.exists())

    def test_existing_store_rechecks_an_authority_root_change(self):
        store = CollectionStore.open_existing(self.root, **self.guards)
        original = self.authority.root
        self.authority.root = self.root.parent / 'wrong-root'
        try:
            with self.assertRaisesRegex(ValueError, 'root_mismatch'):
                store.graph_page(projects=self.projects)
        finally:
            self.authority.root = original
        self.assertTrue(store.graph_page(projects=self.projects)['nodes'])

    def test_mcp_rejects_a_context_callback_owned_by_a_different_root(self):
        other_root = candidate.create_root(self.root.parent / 'context-root')
        other = candidate.Frontier(other_root, 'kakaotalk', 'owner', self.reader)
        authority = candidate.MessengerAuthority([other])
        with self.assertRaisesRegex(ValueError, 'root_mismatch'):
            KnowledgeServer(self.root, self.projects, **self.guards, source_context=authority.retrieval_context)

    def test_bound_source_authority_cannot_read_a_cloned_second_store(self):
        temporary = TemporaryDirectory(dir='/private/tmp'); self.addCleanup(temporary.cleanup)
        other = candidate.create_root(Path(temporary.name) / 'other')
        shutil.copytree(self.root / 'knowledge', other / 'knowledge')
        for action in ('graph', 'search', 'mcp', 'writer'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'root_mismatch'):
                if action == 'graph':
                    read_action(other, 'collection-graph', json.dumps({'projects': self.projects}), **self.guards)
                elif action == 'search':
                    retrieve(other, '회의', projects=self.projects, **self.guards)
                elif action == 'mcp':
                    KnowledgeServer(other, self.projects, **self.guards)
                else:
                    CollectionStore(other, **self.guards)
        with self.assertRaisesRegex(ValueError, 'root_mismatch'):
            CollectionStore.open_existing(other, source_access=self.kk._source_access)

    def setUp(self):
        self.case = access_tests.FrontierAccessTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.reader, self.kk = self.case.add_kakao_origin()
        self.tg, self.root = self.case.engine, self.case.root
        self.authority = candidate.MessengerAuthority([self.kk, self.tg])
        self.guards = {'source_access': self.authority.access, 'source_revision': self.authority.revision}
        self.projects = [candidate.PROJECT]
        self.embed = self.enterContext(patch.object(kg, '_local_dense_embeddings', side_effect=AssertionError('unexpected model')))
        self.network = self.enterContext(patch.object(socket.socket, 'connect', side_effect=AssertionError('unexpected network')))

    def tearDown(self):
        self.embed.assert_not_called(); self.network.assert_not_called()

    def graph(self, **options):
        return read_action(self.root, 'collection-graph', json.dumps({'projects': self.projects, **options}), **self.guards)

    def lookup(self, **options):
        return retrieve(self.root, '회의', projects=self.projects, **self.guards, **options)

    def unique_kakao(self):
        self.reader.pages[('k', None)] = ([item(1, '계약 조건과 배송 일정은 다음 주 별도 승인 후에 확정합니다.')], False, None)
        self.kk.step(mode='reconcile')

    def server(self, guarded=True):
        server = KnowledgeServer(self.root, self.projects, **(self.guards if guarded else {}))
        server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {}}})
        server.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return server

    def call(self, server, tool, **options):
        response = server.dispatch({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'alden_knowledge_' + tool, 'arguments': {'projects': self.projects, **options}}})
        self.assertNotIn('error', response)
        self.assertFalse(response['result']['isError'], response)
        return json.loads(response['result']['content'][0]['text'])

    def test_existing_graph_boundary_combines_sources_and_defaults_remain_closed(self):
        self.unique_kakao()
        self.assertEqual(len(self.graph()['nodes']), 2)
        self.assertEqual(read_action(self.root, 'collection-graph')['nodes'], [])
        self.case.denied.update(('a', 'b'))
        self.assertEqual(len(self.graph()['nodes']), 1)
        self.reader.blocked = True
        self.assertEqual(self.graph()['nodes'], [])

    def test_shared_content_remains_one_version_with_separate_allowed_origins(self):
        node = self.graph()['nodes'][0]
        self.assertEqual({c['target_id'] for c in node['source_contexts']}, {self.kk.target, self.tg.target})
        with self.kk.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)

    def test_one_unavailable_provider_does_not_hide_a_permitted_independent_origin(self):
        with patch.object(self.tg.reader, 'identity', side_effect=candidate.SourceBlocked('provider_unavailable')):
            self.assertEqual(len(self.graph()['nodes']), 1)
            result = self.lookup(); self.assertTrue(result['facts'])
            self.assertNotIn(self.tg.target, json.dumps(result))

    def test_retrieval_provenance_excludes_a_revoked_duplicate_origin(self):
        self.case.denied.update(('a', 'b'))
        result = self.lookup()
        self.assertTrue(result['facts'])
        targets = {m['target_id'] for p in result['fact_provenance'] for m in p['source_memberships']}
        self.assertEqual(targets, {self.kk.target})

    def test_first_publisher_authorization_metadata_is_not_current_selected_source_evidence(self):
        self.case.denied.update(('a', 'b'))
        result = self.lookup()
        self.assertTrue(result['facts'])
        self.assertNotIn('authorization_sha256', json.dumps(result))
        self.assertEqual(result['fact_provenance'][0]['derived_from']['current_source_targets'], [self.kk.target])
        with self.kk.store.database() as db:
            stored = json.loads(db.execute('SELECT metadata FROM versions').fetchone()[0])
        self.assertEqual(stored['source']['platform'], 'telegram')

    def test_lexical_search_does_not_export_revoked_first_publisher_capture_metadata(self):
        self.case.denied.update(('a', 'b'))
        store = CollectionStore.open_existing(self.root, **self.guards)
        with store.database() as db:
            before = [tuple(row) for row in db.execute('SELECT id,raw_sha256,metadata FROM versions ORDER BY id')]
            events = db.execute('SELECT count(*) FROM events').fetchone()[0]
        for expand in (False, True):
            with self.subTest(expand_korean=expand):
                result = store.search('회의', projects=self.projects, expand_korean=expand)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['body'], self.case.text)
                self.assertEqual(result[0]['metadata']['source']['content_hash'], hashlib.sha256(self.case.text.encode()).hexdigest())
                self.assertNotIn('authorization_sha256', json.dumps(result))
                self.assertNotIn('account_id', json.dumps(result))
                self.assertNotIn('platform', result[0]['metadata']['source'])
                self.assertNotIn('since', json.dumps(result))
                self.assertNotIn('until', json.dumps(result))
        with store.database() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,raw_sha256,metadata FROM versions ORDER BY id')], before)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], events)
        self.reader.blocked = True
        self.assertEqual(store.search('회의', projects=self.projects), [])

    def test_lexical_search_metadata_is_content_identity_even_before_revocation(self):
        result = CollectionStore.open_existing(self.root, **self.guards).search('회의', projects=self.projects)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['metadata']['source']['kind'], 'messenger-exact-content-provenance')
        self.assertNotIn('authorization_sha256', json.dumps(result))
        self.assertNotIn('account_id', json.dumps(result))

    def test_retrieval_does_not_reveal_a_relation_owned_by_a_revoked_source(self):
        second = '업무 자료 검토는 계약 조건의 변경 이후 다음 주에 진행합니다.'
        self.reader.pages[('k', None)] = ([item(2, second), item(1, self.case.text)], False, None)
        self.kk.step(mode='reconcile')
        first = self.case.text; self.case.text = second; self.tg.step(mode='reconcile')
        keys = ['messenger-content:' + hashlib.sha256(text.encode()).hexdigest() for text in (first, second)]
        relation = {'source_platform': 'graph', 'source_id': keys[0], 'target_platform': 'graph',
            'target_id': keys[1], 'type': 'reference', 'evidence': {'basis': 'private-relation-canary'}}
        self.tg.store.ingest(self.tg.target, [], relations=[relation])
        first_id = identity('graph', keys[0])
        active = self.lookup(also=[first_id], max_entities=1)
        self.assertTrue(any(p['fact_type'] == 'relation' for p in active['fact_provenance']))
        self.case.denied.update(('a', 'b'))
        result = self.lookup(also=[first_id], max_entities=1)
        self.assertTrue(result['facts'])
        self.assertNotIn('private-relation-canary', json.dumps(result))
        self.assertFalse(any(p['fact_type'] == 'relation' for p in result['fact_provenance']))

    def test_history_activity_and_context_recheck_current_source_authority(self):
        before = self.graph(activity=True)
        span = self.authority.context_window(self.kk.target, 'k', '1', projects=self.projects)
        self.assertTrue(span['items'])
        self.reader.blocked = True; self.case.denied.update(('a', 'b'))
        current = self.graph(activity=True, after=before['cursor'], stream_id=before['stream_id'])
        self.assertTrue(current['reset']); self.assertEqual(current['items'], [])
        history = read_action(self.root, 'collection-history', json.dumps({'projects': self.projects}), **self.guards)
        self.assertEqual(history['items'], [])
        with self.assertRaises(candidate.SourceBlocked):
            self.authority.context_window(self.kk.target, 'k', '1', projects=self.projects)

    def test_read_only_mcp_uses_the_same_authority_for_graph_history_and_search(self):
        self.unique_kakao(); server = self.server()
        self.assertEqual(len(self.call(server, 'graph')['nodes']), 2)
        self.assertTrue(self.call(server, 'search', query='회의')['facts'])
        self.assertTrue(self.call(server, 'history')['items'])
        self.case.denied.update(('a', 'b')); self.reader.blocked = True
        self.assertEqual(self.call(server, 'graph')['nodes'], [])
        self.assertEqual(self.call(server, 'search', query='회의')['facts'], [])
        self.assertEqual(self.call(server, 'history')['items'], [])
        self.assertEqual(self.call(self.server(False), 'graph')['nodes'], [])

    def test_mcp_serialization_keeps_shared_knowledge_without_revoked_origin_receipts(self):
        self.case.denied.update(('a', 'b'))
        result = self.call(self.server(), 'search', query='회의')
        self.assertTrue(result['facts'])
        serialized = json.dumps(result)
        self.assertNotIn(self.tg.target, serialized)
        self.assertNotIn('authorization_sha256', serialized)

    def test_latest_selection_does_not_use_withheld_publisher_labels(self):
        import alden_retrieval_time as temporal
        with self.tg.store.database() as db:
            db.execute('UPDATE targets SET label=? WHERE id=?', ('private-publisher-canary', self.tg.target))
        self.case.denied.update(('a', 'b'))
        with patch.object(temporal, 'select_latest', wraps=temporal.select_latest) as select:
            retrieve(self.root, '최신 회의', projects=self.projects, **self.guards)
        select.assert_called_once()
        self.assertNotIn('private-publisher-canary', json.dumps(select.call_args.args[1]))

    def test_query_json_cannot_supply_or_broaden_native_authority(self):
        response = read_action(self.root, 'collection-graph', json.dumps({'source_access': True,
            'source_revision': 'allow', 'native_accounts': ['all']}))
        self.assertEqual(response['nodes'], [])
        reply = self.server().dispatch({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
            'name': 'alden_knowledge_graph', 'arguments': {'projects': self.projects, 'source_access': True}}})
        self.assertIn('error', reply)

    def test_read_composition_does_not_write_or_request_more_native_bodies(self):
        before = journal_rows(self.kk.store); calls = len(self.case.body_calls)
        store = CollectionStore.open_existing(self.root, **self.guards)
        self.graph(); self.lookup(); self.call(self.server(), 'history')
        self.assertEqual(journal_rows(self.kk.store), before); self.assertEqual(len(self.case.body_calls), calls)
        with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
            store.register(platform='graph', original_id='no', kind='source', label='no', projects=self.projects)
        self.assertFalse((store.root / 'retrieval.sqlite3').exists())

    def test_registry_rejects_ambiguous_or_cross_root_source_ownership(self):
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            candidate.MessengerAuthority([self.kk, self.kk])
        temporary = TemporaryDirectory(dir='/private/tmp'); self.addCleanup(temporary.cleanup)
        other = candidate.Frontier(candidate.create_root(Path(temporary.name) / 'other'), 'kakaotalk', 'owner', self.reader)
        with self.assertRaisesRegex(ValueError, 'roots_differ'):
            candidate.MessengerAuthority([self.kk, other])

    def test_changed_binding_and_unregistered_sources_withhold_instead_of_adopting(self):
        partial = candidate.MessengerAuthority([self.kk])
        store = CollectionStore.open_existing(self.root, source_access=partial.access, source_revision=partial.revision)
        self.unique_kakao(); self.assertEqual(len(store.graph_page(projects=self.projects)['nodes']), 1)
        self.kk.account = 'changed-owner'
        self.assertEqual(store.graph_page(projects=self.projects)['nodes'], [])
        with self.assertRaises(candidate.SourceBlocked):
            partial.context_window(self.tg.target, 'a', 'a', projects=self.projects)
        for target in (None, [], {}):
            with self.subTest(target=target), self.assertRaises(candidate.SourceBlocked):
                partial.context_window(target, 'a', 'a', projects=self.projects)
