import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, identity, read_action
import alden_gbrain_client as gbrain_client
from alden_gbrain_client import GbrainError, GbrainReadClient, _Stdio, validate_runtime
from alden_gbrain_projection import GbrainProjection
from alden_gbrain_bridge import GbrainGraphReader
from alden_knowledge_mcp import KnowledgeServer


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.a = self.store.register(platform='graph', original_id='publisher-a', kind='source',
                                     label='Same publisher label', projects=['one'])
        self.b = self.store.register(platform='graph', original_id='publisher-b', kind='source',
                                     label='Same publisher label', projects=['one'])
        self.store.ingest(self.a, [{'original_id':'a1','label':'Same title','text':'PRIVATE BODY A'},
                                   {'original_id':'a2','label':'Same title','text':'PRIVATE BODY B'}])
        self.store.ingest(self.b, [{'original_id':'b1','label':'Same title','text':'PRIVATE BODY C'}])
        self.packet = self.current()
        self.projection = GbrainProjection(self.packet, ['one'])

    def current(self):
        return read_action(self.root, 'collection-graph', json.dumps({'projects':['one'], 'limit':50}))

    def rows(self, target):
        return [{**{k:link[k] for k in ('context','link_type')}, 'from_slug':link['from'],
                 'to_slug':link['to'], 'from_source_id':'default', 'to_source_id':'default',
                 'depth':1 if self.projection.by_slug[link['from']]['id']==identity('graph','a1') else 2}
                for link in self.projection.links()
                if link['link_type']=='sourced_from' and self.projection.by_slug[link['to']]['source_target']==target]

    def test_metadata_only_pages_preserve_same_named_records_and_origins(self):
        before = copy.deepcopy(self.packet)
        pages = self.projection.pages()
        self.assertEqual(len(pages), 5)
        self.assertEqual(len({p['slug'] for p in pages}), 5)
        self.assertEqual(sum(n['label']=='Same title' for n in self.projection.nodes.values()), 3)
        self.assertNotIn('PRIVATE BODY', json.dumps(pages))
        self.assertNotIn('effective_date', json.dumps(pages))
        self.assertEqual(self.packet, before)

    def test_current_source_context_paths_bind_back_to_original_ids_and_versions(self):
        result = self.projection.bind(self.rows(self.a), focus=identity('graph','a1'),
                                      current_packet=self.current(), projects=['one'])
        self.assertEqual({n['id'] for n in result['nodes'] if 'source_version' in n},
                         {identity('graph','a1'), identity('graph','a2')})
        self.assertEqual(len(result['nodes']), 3)
        self.assertTrue(all(n['source_target']==self.a for n in result['nodes']))
        self.assertEqual([e['relation'] for e in result['edges']], ['sourced_from']*2)

    def test_real_source_version_change_invalidates_every_generation_slug(self):
        self.store.ingest(self.a, [{'original_id':'a1','label':'Same title','text':'Changed retained body'}])
        current = GbrainProjection(self.current(), ['one'])
        self.assertFalse(set(current.slugs.values()) & set(self.projection.slugs.values()))
        with self.assertRaisesRegex(GbrainError, 'gbrain_projection_stale'):
            self.projection.bind(self.rows(self.a), focus=identity('graph','a1'),
                                 current_packet=self.current(), projects=['one'])

    def test_real_permission_revocation_rejects_previously_cached_paths(self):
        self.store.register(platform='graph',original_id='publisher-a',kind='source',
                            label='Same publisher label',projects=['one'],permission='denied')
        with self.assertRaisesRegex(GbrainError, 'gbrain_projection_stale'):
            self.projection.bind(self.rows(self.a), focus=identity('graph','a1'),
                                 current_packet=self.current(), projects=['one'])

    def test_another_project_and_out_of_scope_context_are_rejected(self):
        with self.assertRaisesRegex(GbrainError, 'gbrain_snapshot_scope_mismatch'):
            GbrainProjection(self.packet, ['two'])
        packet = copy.deepcopy(self.packet)
        packet['nodes'][0]['source_contexts'][0]['project'] = 'two'
        with self.assertRaisesRegex(GbrainError, 'gbrain_snapshot_context_invalid'):
            GbrainProjection(packet, ['one'])

    def test_matching_original_id_does_not_import_another_memberships_old_version(self):
        self.store.ingest(self.a, [{'original_id':'shared','label':'Shared','text':'Earlier revision'}])
        self.store.ingest(self.b, [{'original_id':'shared','label':'Shared','text':'Selected revision'}])
        projection = GbrainProjection(self.current(), ['one'])
        origin_edges = [e for e in projection.edges if e['source']==identity('graph','shared')]
        self.assertEqual(len(origin_edges), 1)
        self.assertEqual(projection.nodes[origin_edges[0]['target']]['source_target'], self.b)

    def test_foreign_or_forged_graph_evidence_is_rejected(self):
        for field,value in [('from_source_id','other'),('context','{}'),('from_slug','sources/foreign'),('depth',True)]:
            rows = self.rows(self.a); rows[0][field] = value
            with self.subTest(field=field), self.assertRaises(GbrainError):
                self.projection.bind(rows, focus=identity('graph','a1'),
                                     current_packet=self.current(), projects=['one'])

    def test_valid_but_disconnected_evidence_is_not_a_path_from_the_focus(self):
        with self.assertRaisesRegex(GbrainError, 'gbrain_projection_path_unbound'):
            self.projection.bind(self.rows(self.b), focus=identity('graph','a1'),
                                 current_packet=self.current(), projects=['one'])

    def test_duplicate_edges_and_a_claim_beyond_the_requested_depth_are_rejected(self):
        rows = self.rows(self.a)
        for bad in (rows + rows, rows):
            with self.subTest(rows=len(bad)), self.assertRaises(GbrainError):
                self.projection.bind(bad, focus=identity('graph','a1'), depth=1,
                                     current_packet=self.current(), projects=['one'])

    def test_missing_edges_empty_result_and_wrong_in_range_depth_are_rejected(self):
        rows = self.rows(self.a)
        wrong_depth = copy.deepcopy(rows); wrong_depth[0]['depth'] = 2
        for bad in ([], rows[:1], wrong_depth):
            with self.subTest(rows=bad), self.assertRaisesRegex(GbrainError, 'gbrain_projection_incomplete'):
                self.projection.bind(bad, focus=identity('graph','a1'),
                                     current_packet=self.current(), projects=['one'])

    def test_direction_depth_and_explicit_typed_relation_are_checked(self):
        self.store.ingest(self.a, [], relations=[{'source_platform':'graph','source_id':'a1',
            'target_platform':'graph','target_id':'a2','type':'hyperlink','evidence':{'basis':'fixture'}}])
        self.packet = self.current(); self.projection = GbrainProjection(self.packet, ['one'])
        rows = self.rows(self.a)
        links = [link for link in self.projection.links() if link['link_type'] != 'sourced_from']
        self.assertEqual(len(links), 1)
        link = links[0]
        typed = {'context':link['context'],'link_type':link['link_type'], 'from_slug':link['from'],
                 'to_slug':link['to'], 'from_source_id':'default','to_source_id':'default','depth':1}
        options = {'focus':identity('graph','a1'),'current_packet':self.current(),'projects':['one']}
        result = self.projection.bind(rows+[typed], **options)
        self.assertEqual(len(result['edges']), 3)
        self.assertEqual(sum(e['relation']=='hyperlink' for e in result['edges']), 1)
        self.assertEqual(len(self.projection.bind([], direction='in', **options)['nodes']), 1)
        outgoing = [r for r in rows if r['depth']==1]+[typed]
        self.assertEqual(len(self.projection.bind(outgoing, direction='out', depth=1, **options)['edges']), 2)
        self.assertEqual(len(self.projection.bind([typed], link_type=link['link_type'], **options)['edges']), 1)
        self.assertEqual(self.projection.schema_pack()['link_types'], [{'name':link['link_type']}])
        self.assertNotIn('description:', self.projection.schema_yaml().split('link_types:')[1])


class BridgeTests(unittest.TestCase):
    setUp, current, rows = ProjectionTests.setUp, ProjectionTests.current, ProjectionTests.rows

    def reader(self):
        path = self.root/'projection.json'
        path.write_text(json.dumps(self.projection.manifest()))
        self.manifest_path = path
        return GbrainGraphReader(owned_root=self.root, receipt=self.root/'runtime.json',
                                home=self.root/'gbrain-home', manifest=path)

    def client(self, rows, after=lambda: None):
        class Client:
            def __enter__(client): return client
            def __exit__(client, *args): self.closed = True
            def graph(client, *args, **kwargs):
                after(); return rows
        return patch('alden_gbrain_bridge.GbrainReadClient', return_value=Client())

    def test_roundtrip_manifest_retains_no_body_and_validates_current_scope(self):
        reader = self.reader()
        self.assertNotIn('PRIVATE BODY', self.manifest_path.read_text())
        before = self.store.path.read_bytes()
        with self.client(self.rows(self.a)):
            result = reader.query(projects=['one'], focus=identity('graph','a1'), current=self.current)
        self.assertTrue(result['basis']['complete_within_snapshot'])
        self.assertEqual(result['basis']['checked_edges'], 2)
        self.assertTrue(self.closed)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_manifest_scope_tamper_and_symlink_fail_before_starting_runtime(self):
        reader = self.reader()
        for mutation in ('fingerprint','scope','symlink'):
            path = self.manifest_path
            if path.is_symlink(): path.unlink()
            document = self.projection.manifest()
            if mutation=='fingerprint': document['fingerprint']='forged'
            if mutation=='scope': document['query']['projects']=['other']
            path.write_text(json.dumps(document))
            if mutation=='symlink':
                other = self.root/'other.json'; path.rename(other); path.symlink_to(other)
            with self.subTest(mutation=mutation), patch('alden_gbrain_bridge.GbrainReadClient') as client:
                with self.assertRaises(GbrainError):
                    reader.query(projects=['one'], focus=identity('graph','a1'), current=self.current)
                client.assert_not_called()

    def test_permission_revoked_before_or_during_query_is_not_returned(self):
        for during in (False, True):
            self.projection = GbrainProjection(self.current(), ['one'])
            reader = self.reader()
            def revoke():
                self.store.register(platform='graph',original_id='publisher-a',kind='source',
                    label='Same publisher label',projects=['one'],permission='denied')
            if not during: revoke()
            with self.subTest(during=during), self.client(self.rows(self.a), revoke if during else lambda: None) as client:
                with self.assertRaisesRegex(GbrainError, 'gbrain_projection_stale'):
                    reader.query(projects=['one'], focus=identity('graph','a1'), current=self.current)
                self.assertEqual(client.call_count, 1 if during else 0)
            self.store.register(platform='graph',original_id='publisher-a',kind='source',
                label='Same publisher label',projects=['one'],permission='local-private')

    def test_partial_result_is_error_and_cancellation_reaps_client(self):
        reader = self.reader()
        with self.client([]), self.assertRaisesRegex(GbrainError, 'gbrain_projection_incomplete'):
            reader.query(projects=['one'], focus=identity('graph','a1'), current=self.current)
        stopped = [False]
        def check():
            if stopped[0]: raise RuntimeError('fixture cancellation')
        with self.client(self.rows(self.a), lambda: stopped.__setitem__(0, True)):
            with self.assertRaisesRegex(RuntimeError, 'fixture cancellation'):
                reader.query(projects=['one'], focus=identity('graph','a1'), current=self.current, check=check)
        self.assertTrue(self.closed)

    def test_mcp_feature_is_opt_in_and_checks_tool_scope_and_options(self):
        default = KnowledgeServer(self.root, ['one'])
        name = 'alden_knowledge_paths'
        self.assertNotIn(name, [t['name'] for t in default.tool_catalog()])
        self.assertFalse(default.valid_call({'name':name,'arguments':{'projects':['one'],'focus':'x'}}))
        server = KnowledgeServer(self.root, ['one'], gbrain=self.reader())
        self.assertIn(name, [t['name'] for t in server.tool_catalog()])
        args = {'projects':['one'],'focus':identity('graph','a1')}
        for extra in ({'depth':True},{'depth':4},{'direction':'sideways'},{'limit':50},{'projects':['other']}):
            self.assertFalse(server.valid_call({'name':name,'arguments':{**args, **extra}}))
        self.assertTrue(server.valid_call({'name':name,'arguments':args}))
        with self.client(self.rows(self.a)):
            result = server.call_tool({'name':name,'arguments':args}, None)
        self.assertEqual(len(result['edges']), 2)
        self.assertEqual(server.tool_failure_code(GbrainError('gbrain_projection_incomplete')), 'alden_gbrain_refresh_required')


class ToolPayloadTests(unittest.TestCase):
    def client(self, result):
        class Transport:
            graph_truncated = False
            def request(self, method, params):
                self.last_request = (method, params)
                return result
        client = object.__new__(GbrainReadClient)
        client.transport, client.source_id = Transport(), 'default'
        client.check, client.notice_codes = lambda: None, set()
        return client

    def test_data_and_structured_setup_notice_are_separate_and_inert(self):
        notice = {'code':'first_run_decisions','kind':'ask','why':'setup pending',
                  'fix':{'argv':['DO_NOT_EXECUTE']}}
        result = {'content':[{'type':'text','text':'[]'},
            {'type':'text','text':'[gbrain notice first_run_decisions kind=ask]\nwhy: setup pending'}],
            '_meta':{'gbrain_notices':[notice]}}
        client = self.client(result)
        self.assertEqual(client.graph('sources/example'), [])
        self.assertEqual(client.notice_codes, {'first_run_decisions'})
        self.assertEqual(client.transport.last_request[1]['name'], 'traverse_graph')

    def test_unpaired_unreviewed_or_error_payloads_are_not_success(self):
        variants = [
            {'content':[{'type':'text','text':'[]'},{'type':'text','text':'[]'}]},
            {'content':[{'type':'text','text':'[]'},{'type':'text','text':'[gbrain notice degraded kind=degraded]\n'}],
             '_meta':{'gbrain_notices':[{'code':'degraded','kind':'degraded'}]}},
            {'isError':True,'content':[{'type':'text','text':'SECRET_ERROR_TEXT'}]},
            {'content':[{'type':'text','text':'{"x":1,"x":2}'}]},
            {'content':[{'type':'text','text':'{}'}]},
            {'content':[{'type':'text','text':'[{"value":1e999}]'}]},
        ]
        for result in variants:
            with self.subTest(result=result):
                with self.assertRaises(GbrainError) as caught:
                    self.client(result).graph('sources/example')
                self.assertNotIn('SECRET', str(caught.exception))

    def test_all_sources_and_invalid_graph_options_never_reach_the_transport(self):
        client = self.client({'content':[{'type':'text','text':'[]'}]})
        for kwargs in ({'depth':True},{'depth':4},{'direction':'sideways'},{'link_type':['sourced_from']}):
            with self.subTest(kwargs=kwargs), self.assertRaises(GbrainError):
                client.graph('sources/example', **kwargs)
        self.assertFalse(hasattr(client.transport, 'last_request'))

    def test_upstream_truncation_is_not_a_complete_graph(self):
        client = self.client({'content':[{'type':'text','text':'[]'}]})
        original = client.transport.request
        def truncated(method, params):
            result = original(method, params)
            client.transport.graph_truncated = True
            return result
        client.transport.request = truncated
        with self.assertRaisesRegex(GbrainError, 'gbrain_graph_truncated'):
            client.graph('sources/example')


class StdioBoundaryTests(unittest.TestCase):
    def transport(self, source, *, timeout=.2, check=lambda: None):
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        peer = _Stdio([sys.executable,'-B','-u','-c',source], cwd=directory.name,
                      env={'PATH':os.environ.get('PATH','')}, check=check, timeout=timeout)
        self.addCleanup(peer.close)
        return peer

    def test_real_pipe_accepts_a_split_utf8_response(self):
        code = """import json,os,sys,time
r=json.loads(sys.stdin.readline());raw=(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'label':'한글'}},ensure_ascii=False)+'\\n').encode()
i=raw.index('한'.encode())+1;os.write(1,raw[:i]);time.sleep(.02);os.write(1,raw[i:])
"""
        self.assertEqual(self.transport(code,timeout=2).request('ping',{}), {'label':'한글'})

    def test_boolean_id_duplicate_keys_and_private_rpc_error_are_rejected(self):
        frames = [
            '{"jsonrpc":"2.0","id":true,"result":{}}',
            '{"jsonrpc":"2.0","id":1,"result":{"x":1,"x":2}}',
            '{"jsonrpc":"2.0","id":1,"error":{"message":"SECRET"}}',
        ]
        for frame in frames:
            code = 'import sys;sys.stdin.readline();print('+repr(frame)+',flush=True)'
            with self.subTest(frame=frame), self.assertRaises(GbrainError) as caught:
                self.transport(code,timeout=2).request('ping',{})
            self.assertNotIn('SECRET', str(caught.exception))

    def test_oversized_actual_pipe_frame_is_bounded(self):
        peer = self.transport("import sys;sys.stdin.readline();sys.stdout.write('x'*5000+'\\n');sys.stdout.flush()",timeout=2)
        with patch('alden_gbrain_client.MAX_FRAME',4096), self.assertRaisesRegex(GbrainError,'gbrain_response_budget'):
            peer.request('ping',{})

    def test_a_peer_that_does_not_read_stdin_cannot_block_the_request_deadline(self):
        peer = self.transport('import time;time.sleep(.7)',timeout=.05)
        with self.assertRaisesRegex(GbrainError,'gbrain_deadline'):
            peer.send({'body':'x'*1024*1024})
        peer.close()
        self.assertIsNotNone(peer.proc.poll())

    def test_cancellation_propagates_and_owned_peer_is_reaped(self):
        class Cancelled(Exception): pass
        stop = time.monotonic()+.05
        def check():
            if time.monotonic() >= stop: raise Cancelled()
        peer = self.transport('import time;time.sleep(.7)',timeout=2,check=check)
        with self.assertRaises(Cancelled):
            peer.request('ping',{})
        peer.close()
        self.assertIsNotNone(peer.proc.poll())


class RuntimeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root/'home'; (self.home/'.gbrain').mkdir(parents=True)
        self.database = self.root/'brain'; self.database.mkdir()
        self.binary = self.root/'inert-binary'; self.binary.write_bytes(b'not executed')
        self.config_path = self.home/'.gbrain/config.json'
        self.config = {'engine':'pglite','database_path':str(self.database),'embedding_disabled':True,
                       'schema_pack':'gbrain-base-v2','self_upgrade':{'mode':'notify'}}
        self.config_path.write_text(json.dumps(self.config))
        self.receipt = self.root/'runtime.json'
        self.receipt.write_text(json.dumps({'repository':'garrytan/gbrain',
            'source_commit':gbrain_client.SOURCE_COMMIT,'tag':'v'+gbrain_client.VERSION,
            'asset_name':gbrain_client.ASSET,'bytes':len(b'not executed'),
            'sha256':hashlib.sha256(b'not executed').hexdigest(),
            'github_asset_digest_matches':True,'runtime_path':str(self.binary)}))
        for name,value in [('ASSET_BYTES',len(b'not executed')),
                           ('ASSET_SHA256',hashlib.sha256(b'not executed').hexdigest())]:
            patcher = patch.object(gbrain_client,name,value); patcher.start(); self.addCleanup(patcher.stop)

    def admit(self):
        return validate_runtime(self.receipt,self.root,self.home)

    def test_owned_keyless_configuration_is_read_without_executing_the_file(self):
        self.assertEqual(self.admit(), (self.binary,self.root,self.home))
        self.assertEqual(self.binary.read_bytes(), b'not executed')

    def test_changed_publisher_bytes_are_rejected(self):
        self.binary.write_bytes(b'now modified')
        with self.assertRaisesRegex(GbrainError,'gbrain_runtime_bytes_changed'):
            self.admit()

    def test_provider_configuration_or_automatic_upgrade_is_not_admitted(self):
        for update in ({'embedding_disabled':False},{'engine':'postgres'},
                       {'provider_keys':{'openai':'SECRET'}},{'self_upgrade':{'mode':'auto'}}):
            self.config_path.write_text(json.dumps({**self.config,**update}))
            with self.subTest(update=update), self.assertRaisesRegex(GbrainError,'gbrain_local_keyless_store_required'):
                self.admit()

    def test_a_symlink_or_database_outside_the_owned_root_is_rejected(self):
        self.binary.rename(self.root/'original-binary')
        self.binary.symlink_to(self.root/'original-binary')
        with self.assertRaisesRegex(GbrainError,'gbrain_path_invalid'):
            self.admit()
        self.binary.unlink(); (self.root/'original-binary').rename(self.binary)
        self.config_path.write_text(json.dumps({**self.config,'database_path':str(self.root.parent)}))
        with self.assertRaisesRegex(GbrainError,'gbrain_database_root_mismatch'):
            self.admit()


if __name__ == '__main__':
    unittest.main()
