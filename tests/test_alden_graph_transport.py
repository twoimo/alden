"""The bounded native transport preserves every collection graph field."""
from contextlib import redirect_stdout
import io
import json
from unittest import TestCase
from unittest.mock import patch
from tests.test_auto_reply_menubar import load
from alden_graph_transport import pack_graph, _record_table


class GraphTransportTests(TestCase):
    def test_record_tables_preserve_present_null_missing_and_future_keys_without_mutation(self):
        rows=[{'id':'a','optional':None,'future':{'proof':['v1','public']}},{'id':'b'}]
        original=json.loads(json.dumps(rows));keys,packed=_record_table(rows)
        self.assertEqual(rows,original)
        self.assertEqual(keys,['future','id','optional'])
        self.assertEqual(packed,[[1,'a',2,None,0,{'proof':['v1','public']}],[1,'b']])
        for invalid in [[{'':1}],[{None:1}],[{str(i):i for i in range(257)}],[['invalid']]]:
            with self.assertRaises(ValueError):_record_table(invalid)

    def test_large_packets_use_the_lossless_record_codec_and_fence_reserved_metadata(self):
        value={'ok':True,'nodes':[{'id':'a','future':'x'*(3*1024*1024)}],'edges':[{'source':'a','target':'b','future':None}]}
        packed=pack_graph(value)
        self.assertEqual(packed['source_record_encoding'],{'codec':'record-keys-v1','nodes':['future','id'],'edges':['future','source','target']})
        self.assertEqual(value['nodes'][0]['id'],'a');self.assertIsNone(value['edges'][0]['future'])
        self.assertLess(len(json.dumps(packed,separators=(',',':'))),4*1024*1024)
        with self.assertRaises(ValueError):pack_graph({**value,'source_record_encoding':{'codec':'other'}})

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

    def test_v2_retains_nested_proofs_null_missing_and_future_fields_without_mutation(self):
        proof={'kind':'snapshot','source_event_ids':['original-id'],'confirmed_at':None,'future':{'claim':'original'}}
        nodes=[{'id':str(i),'description':'x'*400000,'evidence':proof}for i in range(8)]
        nodes.extend([{'id':'absent'},{'id':'null','evidence':None}])
        value={'ok':True,'nodes':nodes,'edges':[{'id':str(i),'evidence':proof,
          'reference_group':{'kind':'directed-relation-group-v1','count':2,'truncated':False,'revision':'v1'}}for i in range(32)]}
        original=json.loads(json.dumps(value));packed=pack_graph(value)
        self.assertEqual(packed['source_record_encoding']['codec'],'record-keys-v2')
        self.assertEqual(value,original)
        encoding=packed['source_record_encoding']
        def restore(row,keys):
            return dict(zip(keys,row[1:]))if row and row[0]==-1 else {keys[row[i]]:row[i+1]for i in range(0,len(row),2)}
        for kind in ('nodes','edges'):
            rows=[restore(row,encoding[kind])for row in packed[kind]]
            for row in rows:
                for field,keys in encoding.get('objects',{}).get(kind,{}).items():
                    if field in row and row[field] is not None:row[field]=restore(row[field],keys)
            self.assertEqual(rows,original[kind])

    def test_v2_dense_marker_requires_all_keys_and_does_not_fill_absent_values(self):
        keys,rows=_record_table([{'id':'a','optional':None},{'id':'b'}],dense=True)
        self.assertEqual(keys,['id','optional']);self.assertEqual(rows,[[-1,'a',None],[0,'b']])

    def test_large_packets_keep_unencodable_nested_values_verbatim(self):
        for evidence in ('text',['array'],{str(i):i for i in range(257)}):
            value={'nodes':[{'id':'a','future':'x'*(3*1024*1024),'evidence':evidence}],'edges':[]}
            packed=pack_graph(value)
            self.assertEqual(packed['source_record_encoding']['codec'],'record-keys-v1')
            keys=packed['source_record_encoding']['nodes'];row=packed['nodes'][0]
            self.assertEqual(row[row.index(keys.index('evidence'))+1],evidence)

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

