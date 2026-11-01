"""Synthetic-only cancellation of scoped MCP reads, including SQLite work."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_abort import AbortController
from alden_collection import CollectionStore, read_action
import alden_knowledge_mcp as knowledge
from alden_status_mcp import CallControl, CallStopped


class KnowledgeAbortTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='alden-mcp-abort-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = CollectionStore(self.root)
        target = self.store.register(platform='graph', original_id='synthetic', kind='source',
                                     label='Synthetic source', projects=['qa'])
        self.store.ingest(target, [{'original_id':'canary', 'label':'Synthetic item',
                                    'text':'PRIVATE_CANCEL_CANARY'}])
        self.source = self.store.graph_page(projects=['qa'], limit=1)['nodes'][0]
        self.server = knowledge.KnowledgeServer(self.root, ['qa'])
        self.server.dispatch({'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{
            'protocolVersion':'2025-11-25', 'capabilities':{}, 'clientInfo':{}}})
        self.server.dispatch({'jsonrpc':'2.0', 'method':'notifications/initialized'})
        self.abort = AbortController(self.root)

    def request(self, tool):
        args = {'projects':['qa'], 'limit':2}
        if tool == 'search': args['query'] = 'canary'
        if tool == 'source':
            args = {'projects':['qa'], 'node_id':self.source['id'],
                    'expected_version':self.source['source_version']}
        return {'jsonrpc':'2.0', 'id':2, 'method':'tools/call', 'params':{
            'name':'alden_knowledge_' + tool, 'arguments':args}}

    def assert_aborted(self, result):
        self.assertTrue(result['result']['isError'], result)
        self.assertEqual(result['result']['content'], [{'type':'text', 'text':'alden_global_abort'}])
        self.assertNotIn('PRIVATE_CANCEL_CANARY', json.dumps(result))

    def test_latched_abort_stops_all_backends_before_source_guard_or_read(self):
        self.abort.abort('PRIVATE_CANCEL_CANARY')
        with patch.object(knowledge, 'read_action') as read, patch.object(knowledge, 'retrieve') as retrieve, \
             patch.object(self.server, '_source_guards', wraps=self.server._source_guards) as guards:
            for tool in ['graph', 'history', 'search', 'source']:
                with self.subTest(tool=tool):
                    self.assert_aborted(self.server.dispatch(self.request(tool)))
            read.assert_not_called(); retrieve.assert_not_called(); guards.assert_not_called()

    def test_abort_and_resume_during_any_backend_cannot_publish_its_old_result(self):
        def late(*args, **kwargs):
            self.abort.abort('PRIVATE_CANCEL_CANARY')
            self.abort.resume_after_human_action()
            return {'nodes':['PRIVATE_CANCEL_CANARY'], 'items':['PRIVATE_CANCEL_CANARY']}
        with patch.object(knowledge, 'read_action', side_effect=late), patch.object(knowledge, 'retrieve', side_effect=late):
            for tool in ['graph', 'history', 'search', 'source']:
                with self.subTest(tool=tool):
                    self.assert_aborted(self.server.dispatch(self.request(tool)))
        self.assertIn('result', self.server.dispatch({'jsonrpc':'2.0','id':3,'method':'ping'}))

    def test_client_control_stop_retains_its_existing_transport_contract(self):
        control = CallControl(10)
        def late(*args, **kwargs):
            control.stop()
            return {'nodes':['PRIVATE_CANCEL_CANARY']}
        with patch.object(knowledge, 'read_action', side_effect=late):
            for tool in ['graph', 'source']:
                with self.subTest(tool=tool), self.assertRaises(CallStopped):
                    self.server.dispatch(self.request(tool), control)

    def test_nested_read_connection_interrupts_sqlite_before_query_finishes(self):
        polls = []; finished = []
        def cancelled():
            polls.append(None)
            return len(polls) >= 80
        def expensive(store, **kwargs):
            with store.database() as db:
                db.execute('''WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL
                    SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n''').fetchone()
                finished.append(True)
            return {'nodes':[], 'edges':[]}
        with patch.object(CollectionStore, 'graph_page', expensive):
            with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
                read_action(self.root, 'collection-graph', '{"projects":["qa"]}', cancelled=cancelled)
        self.assertFalse(finished, 'post-return cancellation alone would let the SQL finish')
        self.assertGreaterEqual(len(polls), 80)
        self.assertTrue(read_action(self.root, 'collection-graph', '{"projects":["qa"]}')['nodes'])

    def test_cancel_scope_does_not_leak_to_another_worker_or_next_call(self):
        def aborted():
            with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
                read_action(self.root, 'collection-history', '{"projects":["qa"]}', cancelled=lambda:True)
            return read_action(self.root, 'collection-history', '{"projects":["qa"]}')
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(aborted)
            second = workers.submit(read_action, self.root, 'collection-graph', '{"projects":["qa"]}')
            self.assertTrue(first.result()['items'])
            self.assertTrue(second.result()['nodes'])

    def test_source_control_interrupts_nested_sql_before_it_finishes(self):
        control = CallControl(10)
        finished = []
        def expensive(store, *args, **kwargs):
            polls = []
            def interrupt(value):
                polls.append(value)
                if len(polls) == 20:
                    control.stop()
                return value
            with store.database() as db:
                db.create_function('source_control_probe', 1, interrupt)
                db.execute('''WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL
                    SELECT x+1 FROM n WHERE x<1000000)
                    SELECT sum(source_control_probe(x)) FROM n''').fetchone()
            finished.append(True)
            return {'ok': True, 'details': {'basis': 'source_record', 'node_id': self.source['id'],
                                           'version': self.source['source_version']}}
        with patch.object(CollectionStore, '_record_details', expensive):
            with self.assertRaises(CallStopped):
                self.server.dispatch(self.request('source'), control)
        self.assertFalse(finished, 'a post-return check alone would allow the SQL to finish')

    def test_unknown_backend_error_still_cannot_export_its_message(self):
        with patch.object(knowledge, 'read_action', side_effect=RuntimeError('PRIVATE_CANCEL_CANARY')):
            for tool in ['graph', 'source']:
                result = self.server.dispatch(self.request(tool))
                self.assertTrue(result['result']['isError'])
                self.assertEqual(result['result']['content'], [{'type':'text','text':'metadata_unavailable'}])


if __name__ == '__main__': unittest.main()
