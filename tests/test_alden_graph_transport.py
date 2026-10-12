"""The bounded native transport preserves every collection graph field."""
from contextlib import redirect_stdout
import io
import json
from unittest import TestCase
from unittest.mock import patch
from tests.test_auto_reply_menubar import load
from alden_graph_transport import pack_graph


class GraphTransportTests(TestCase):
    def test_references_only_exact_contexts_and_retains_original_fields(self):
        current = {'project': 'one', 'version': 'v1', 'target_id': 't', 'label': '지식'}
        foreign = {**current, 'project': 'two', 'target_id': 'other'}
        additional = {**current, 'permission': 'read'}
        value = {'nodes': [{'id': 'a', 'label': '지식', 'source_target': 't',
                           'source_version': 'v1', 'source_contexts': [current, foreign, additional]}],
                 'edges': [{'id': 'e', 'source': 'a', 'target': 'b', 'type': 'supports'}]}
        packed = pack_graph(value)
        self.assertEqual(packed['nodes'][0]['source_contexts'],
                         [{'project': 'one', 'same_as_display': True}, foreign, additional])
        self.assertEqual(value['nodes'][0]['source_contexts'], [current, foreign, additional])
        self.assertEqual(packed['edges'], value['edges'])

    def test_compact_graph_flag_round_trips_fields_without_changing_other_actions(self):
        module = load('alden_graph_transport_test')
        value = {'ok': True, 'nodes': [{'id': 'a', 'label': '지식',
                 'source_contexts': [{'project': 'one', 'version': 'v1', 'target_id': 't'}]}], 'edges': []}
        for action in ('collection-graph', 'collection-quality'):
            stream = io.StringIO()
            with patch.object(module.sys, 'argv', ['menu.py', '--action', action, '--compact-json']), redirect_stdout(stream):
                module._print_json(value)
            self.assertEqual(json.loads(stream.getvalue()), pack_graph(value) if action == 'collection-graph' else value)
            if action == 'collection-graph':
                self.assertEqual(stream.getvalue().strip(), json.dumps(pack_graph(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False))

