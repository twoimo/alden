"""MCP response scope over private SQLite fixtures; no host or model required."""
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from alden_collection import CollectionStore, identity, read_action
from alden_collection_retrieval import retrieve
from alden_knowledge_mcp import KnowledgeServer
import auto_reply_knowledge_graph as kg


class KnowledgeResponseScopeTests(unittest.TestCase):
    FIRST = 'mcp-allowed-a'
    SECOND = 'mcp-allowed-b'
    OUTSIDE = 'mcp-external-project-canary'

    def setUp(self):
        temporary = TemporaryDirectory(prefix='alden-mcp-scope-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = CollectionStore(self.root)
        self.first = self.target('first', [self.FIRST], ['alpha', 'related'])
        self.second = self.target('second', [self.SECOND], ['beta'])
        self.shared = self.target('shared', [self.FIRST, self.SECOND, self.OUTSIDE], ['shared'])
        for index in range(3):
            self.target('external-target-canary-' + str(index), [self.OUTSIDE],
                        ['external-document-canary-' + str(index)])
        self.server = KnowledgeServer(self.root, [self.FIRST, self.SECOND])
        self.server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                              'params': {'protocolVersion': '2025-11-25',
                                         'capabilities': {}, 'clientInfo': {}}})
        self.server.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.request_id = 1
        self.embed = self.enterContext(patch.object(
            kg, '_local_dense_embeddings', side_effect=AssertionError('unexpected embedding')))
        self.connect = self.enterContext(patch.object(
            socket.socket, 'connect', side_effect=AssertionError('unexpected network')))
        self.before = self.snapshot()

    def target(self, name, projects, originals):
        target = self.store.register(platform='graph', original_id=name, kind='source',
                                     label=name, projects=projects, permission='local-private')
        relations = []
        if len(originals) == 2:
            relations.append({'source_platform': 'graph', 'source_id': originals[0],
                              'target_platform': 'graph', 'target_id': originals[1],
                              'type': 'reference', 'evidence': {'basis': 'fixture'}})
        self.store.ingest(target, [{'original_id': original, 'label': 'scopeprobe ' + original,
                                    'text': 'retained body for ' + original}
                                   for original in originals], relations=relations)
        return target

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()
                and not path.name.endswith(('-wal', '-shm'))}

    def tearDown(self):
        self.embed.assert_not_called()
        self.connect.assert_not_called()
        self.assertFalse((self.store.root / 'retrieval.sqlite3').exists())
        self.assertEqual(self.before, self.snapshot())

    def call(self, tool, projects, **options):
        self.request_id += 1
        reply = self.server.dispatch({'jsonrpc': '2.0', 'id': self.request_id,
            'method': 'tools/call', 'params': {'name': 'alden_knowledge_' + tool,
                                             'arguments': {'projects': projects, **options}}})
        self.assertNotIn('error', reply)
        self.assertFalse(reply['result']['isError'], reply)
        return json.loads(reply['result']['content'][0]['text']), json.dumps(reply)

    def history_expected(self, projects, **options):
        result = read_action(self.root, 'collection-history',
                             json.dumps({'projects': projects, **options}))
        return {**result, 'projects': [row for row in result['projects']
                                      if row['project'] in projects]}

    def test_history_serialization_excludes_external_project_and_target_count(self):
        global_result = read_action(self.root, 'collection-history',
                                    json.dumps({'projects': [self.FIRST], 'limit': 2}))
        self.assertIn({'project': self.OUTSIDE, 'targets': 4}, global_result['projects'])
        result, wire = self.call('history', [self.FIRST], limit=2)
        self.assertNotIn(self.OUTSIDE, wire)
        self.assertNotIn(self.SECOND, wire)
        self.assertNotIn('external-target-canary', wire)
        self.assertEqual(result, self.history_expected([self.FIRST], limit=2))
        self.assertEqual(result['projects'], [{'project': self.FIRST, 'targets': 2}])
        # The UI retains its global catalog, including legitimate target counts.
        self.assertEqual(read_action(self.root, 'collection-projects')['projects'],
                         global_result['projects'])

    def test_history_each_request_keeps_its_legitimate_project_metadata(self):
        for projects in ([self.SECOND], [self.FIRST, self.SECOND]):
            with self.subTest(projects=projects):
                result, wire = self.call('history', projects, limit=50)
                self.assertEqual(result, self.history_expected(projects, limit=50))
                self.assertEqual(result['projects'], [{'project': project, 'targets': 2}
                                                     for project in projects])
                self.assertTrue(result['items'])
                self.assertNotIn(self.OUTSIDE, wire)

    def test_history_preserves_events_summary_limit_and_cursor_pages(self):
        projects = [self.FIRST]
        first, _ = self.call('history', projects, limit=2, after=0)
        self.assertEqual(first, self.history_expected(projects, limit=2, after=0))
        self.assertEqual(len(first['items']), 2)
        self.assertIsNotNone(first['next'])
        following, _ = self.call('history', projects, limit=2, after=first['cursor'])
        self.assertEqual(following, self.history_expected(projects, limit=2, after=first['cursor']))
        self.assertEqual(first['stream_id'], following['stream_id'])
        self.assertTrue({item['sequence'] for item in first['items']}.isdisjoint(
                        item['sequence'] for item in following['items']))
        latest, _ = self.call('history', projects, limit=1)
        self.assertEqual(latest, self.history_expected(projects, limit=1))
        self.assertGreater(latest['summary']['total'], len(latest['items']))

    def test_search_preserves_scoped_provenance_and_relations(self):
        for projects in ([self.FIRST], [self.SECOND], [self.FIRST, self.SECOND]):
            with self.subTest(projects=projects):
                result, wire = self.call('search', projects, query='scopeprobe', limit=10)
                self.assertEqual(result, retrieve(self.root, 'scopeprobe', projects=projects,
                                                  max_entities=10))
                self.assertNotIn(self.OUTSIDE, wire)
                self.assertNotIn('external-document-canary', wire)
                self.assertEqual(result['projects'], projects)
                entities = [proof for proof in result['fact_provenance']
                            if proof['fact_type'] == 'entity']
                shared = next(proof for proof in entities if proof['original_id'] == 'shared')
                self.assertEqual(shared['projects'], projects)
                self.assertEqual({m['project'] for m in shared['source_memberships']}, set(projects))
                self.assertTrue(all(proof['provenance_valid'] for proof in entities))
                if len(projects) == 1:
                    other = self.SECOND if projects == [self.FIRST] else self.FIRST
                    self.assertNotIn(other, wire)
        limited, _ = self.call('search', [self.FIRST], query='scopeprobe', limit=1)
        self.assertEqual(sum(p['fact_type'] == 'entity' for p in limited['fact_provenance']), 1)

    def test_graph_preserves_request_scoped_contexts_counts_and_edges(self):
        for projects in ([self.FIRST], [self.SECOND], [self.FIRST, self.SECOND]):
            with self.subTest(projects=projects):
                result, wire = self.call('graph', projects, limit=50)
                self.assertEqual(result, read_action(self.root, 'collection-graph',
                                                    json.dumps({'projects': projects, 'limit': 50})))
                self.assertNotIn(self.OUTSIDE, wire)
                self.assertNotIn('external-target-canary', wire)
                self.assertNotIn('external-document-canary', wire)
                self.assertEqual(result['scope'], projects)
                shared = next(node for node in result['nodes'] if node['id'] == identity('graph', 'shared'))
                self.assertEqual({c['project'] for c in shared['source_contexts']}, set(projects))
                self.assertEqual(result['total_nodes'], 1 + 2 * (self.FIRST in projects)
                                 + (self.SECOND in projects))
                self.assertEqual(result['total_edges'], int(self.FIRST in projects))
                if len(projects) == 1:
                    other = self.SECOND if projects == [self.FIRST] else self.FIRST
                    self.assertNotIn(other, wire)
        focused, _ = self.call('graph', [self.FIRST], focus=identity('graph', 'alpha'), limit=1)
        self.assertEqual(focused, read_action(self.root, 'collection-graph', json.dumps(
            {'projects': [self.FIRST], 'focus': identity('graph', 'alpha'), 'hops': 1, 'limit': 1})))

    def test_authorized_original_text_is_not_redacted_by_project_name(self):
        text = 'scopeprobe quoted project name: ' + self.OUTSIDE
        self.store.ingest(self.first, [{'original_id': 'quoted', 'label': 'quote', 'text': text}])
        self.before = self.snapshot()
        result, _ = self.call('search', [self.FIRST], query='quoted', limit=1)
        self.assertEqual(result['facts'], ['quote: ' + text])
        graph, _ = self.call('graph', [self.FIRST], focus=identity('graph', 'quoted'), limit=1)
        self.assertEqual(graph['nodes'][0]['description'], text)

    def test_missing_store_and_rejected_scope_do_not_create_state(self):
        absent = self.root / 'not-configured'
        missing = KnowledgeServer(absent, [self.FIRST])
        result = missing.call_tool({'name': 'alden_knowledge_history',
                                    'arguments': {'projects': [self.FIRST]}}, None)
        self.assertEqual(result['state'], 'not_configured')
        self.assertEqual(result['projects'], [])
        self.assertFalse(absent.exists())
        for tool in ('history', 'graph', 'search'):
            arguments = {'projects': [self.OUTSIDE]}
            if tool == 'search':
                arguments['query'] = 'scopeprobe'
            reply = self.server.dispatch({'jsonrpc': '2.0', 'id': 100,
                'method': 'tools/call', 'params': {'name': 'alden_knowledge_' + tool,
                                                 'arguments': arguments}})
            self.assertEqual(reply['error']['code'], -32602)
            self.assertNotIn(self.OUTSIDE, json.dumps(reply))


if __name__ == '__main__':
    unittest.main()
