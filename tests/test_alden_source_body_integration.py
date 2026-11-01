"""Retained-original windows through real capture, authority and MCP boundaries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_abort import AldenCancelled
from alden_collection import CollectionStore, digest, encoded, identity, read_action, read_source_body
from alden_collect import capture_source, collect_target
import alden_file_set as files
from alden_knowledge_mcp import KnowledgeServer


class OriginalBodyIntegrationTests(unittest.TestCase):
    def fixture(self, data=None, *, capture=True, name='one.md'):
        temp = tempfile.TemporaryDirectory(dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.originals = self.root / 'originals'; self.originals.mkdir()
        self.data = data if data is not None else ('\ufeffstart e\u0301\r\n' + '가😀 text\r\n' * 2000 + 'end \t\n').encode()
        self.original = self.originals / name; self.original.write_bytes(self.data)
        self.manifest = self.root / 'files.json'
        self.manifest.write_bytes(encoded({'schema': 1, 'files': [{'id': 'one', 'path': name}]}))
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='files', original_id='body-originals', kind='file-set',
            label='Originals', projects=['one'], config={'adapter': 'file-set',
                'root': str(self.originals), 'path': str(self.manifest)})
        collect_target(self.store, self.target)
        self.node = self.store.graph_page(projects=['one'], limit=1)['nodes'][0]
        if capture:
            capture_source(self.store, self.target)
        self.args = {'projects': ['one'], 'node_id': self.node['id'], 'target_id': self.target,
                     'expected_version': self.node['source_version']}
        return self

    def read(self, **kwargs):
        return read_source_body(self.root, **{**self.args, **kwargs})

    def detail(self):
        return self.store.graph_page(projects=['one'], focus=self.node['id'], hops=0,
            details=True, expected_version=self.node['source_version'], limit=1)['details']

    def capture_objects(self):
        cursor = json.loads(self.store.target(self.target)['cursor'])
        folder = self.store.root / 'source-captures'
        receipt = json.loads((folder / cursor['source_capture']['manifest']).read_bytes())
        packet = json.loads((folder / receipt['source_file']).read_bytes())
        return folder, cursor, receipt, packet

    def alter_capture(self, *, receipt_change=None, packet_change=None, packet_raw=None):
        folder, cursor, receipt, packet = self.capture_objects()
        if packet_change is not None:
            packet_change(packet)
        if packet_change is not None or packet_raw is not None:
            raw = encoded(packet) if packet_raw is None else packet_raw
            sha = digest(raw); (folder / (sha + '.json')).write_bytes(raw)
            receipt.update(source_file=sha + '.json', source_sha256=sha, source_bytes=len(raw))
            cursor['source_capture']['sha256'] = sha
        if receipt_change is not None:
            receipt_change(receipt)
        raw = encoded(receipt); sha = digest(raw)
        (folder / (sha + '.json')).write_bytes(raw)
        cursor['source_capture']['manifest'] = sha + '.json'
        with self.store.database() as db:
            db.execute('UPDATE targets SET cursor=? WHERE id=?', (encoded(cursor).decode(), self.target))

    def assert_code(self, code, **kwargs):
        with self.assertRaises(files.SourceBodyError) as caught:
            self.read(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def durable_snapshot(self):
        # SQLite may create transient WAL/SHM bookkeeping on a cold readonly
        # open. Assert persisted content and logical rows separately.
        with CollectionStore.open_existing(self.root).database() as db:
            tables = ('targets', 'target_projects', 'documents', 'versions', 'memberships', 'runs', 'events')
            logical = {name: sorted(tuple(row) for row in db.execute('SELECT * FROM ' + name)) for name in tables}
        transient = {str(Path(str(self.store.path) + suffix).relative_to(self.root)) for suffix in ('-wal', '-shm')}
        durable = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
            if p.is_file() and str(p.relative_to(self.root)) not in transient}
        return durable, logical

    def test_exact_reconstruction_after_original_disappears_without_reparse_or_network(self):
        self.fixture(); before_detail = self.detail()
        self.assertEqual(before_detail['source_extraction']['status'], 'partial')
        self.original.unlink(); self.manifest.unlink()
        before = self.durable_snapshot()
        chunks, offset = [], 0
        with patch.object(files, '_manifest', side_effect=AssertionError('original manifest reopened')), \
             patch.object(files, 'file_set_snapshot', side_effect=AssertionError('originals reopened')), \
             patch.object(files, 'parse_file', side_effect=AssertionError('parser called')), \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('network')):
            while True:
                value = self.read(offset_bytes=offset)['body_window']
                self.assertEqual(set(value), {'schema', 'kind', 'binding', 'capture', 'encoding', 'offset_bytes',
                    'end_bytes', 'next_offset_bytes', 'total_bytes', 'text', 'text_sha256'})
                raw = value['text'].encode()
                self.assertEqual(raw, self.data[offset:value['end_bytes']])
                self.assertEqual(value['text_sha256'], digest(raw))
                self.assertEqual(value['binding']['input_sha256'], digest(self.data))
                self.assertEqual(value['binding']['document_id'], self.node['id'])
                self.assertEqual(value['binding']['target_id'], self.target)
                self.assertEqual(value['binding']['version'], self.node['source_version'])
                self.assertLessEqual(len(raw), 16000); chunks.append(raw)
                if value['next_offset_bytes'] is None:
                    break
                self.assertGreater(value['next_offset_bytes'], offset)
                offset = value['next_offset_bytes']
        self.assertEqual(b''.join(chunks), self.data)
        after = self.durable_snapshot()
        self.assertEqual(before, after)
        self.assertEqual(self.detail()['source_extraction'], before_detail['source_extraction'])
        self.assertEqual(self.read(offset_bytes=len(self.data))['body_window']['text'], '')

    def test_request_rejection_precedes_store_open(self):
        base = {'projects': ['one'], 'node_id': 'files:one', 'target_id': 'files:target', 'expected_version': 'version:one'}
        invalid = [{'projects': []}, {'projects': ['one', 'one']}, {'node_id': ''}, {'target_id': ' padded'},
            {'expected_version': 'x' * 257}, {'offset_bytes': True}, {'offset_bytes': -1}, {'offset_bytes': 1.0},
            {'offset_bytes': '0'}, {'max_bytes': 3}, {'max_bytes': 16001}, {'max_bytes': False}]
        with patch.object(CollectionStore, 'open_existing', side_effect=AssertionError('opened')):
            for change in invalid:
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, '^collection_source_body_request_invalid$'):
                    read_source_body(Path('/unused'), **{**base, **change})

    def test_missing_store_and_capture_never_fall_back(self):
        self.fixture(capture=False)
        self.assert_code('alden_source_body_capture_unavailable')
        missing = self.root / 'absent'
        with self.assertRaises(files.SourceBodyError) as caught:
            read_source_body(missing, **self.args)
        self.assertEqual(caught.exception.code, 'alden_source_body_unavailable')
        self.assertFalse(missing.exists())

    def test_exact_target_and_stale_version_reject_before_archive(self):
        self.fixture()
        old = dict(self.args)
        self.original.write_text('new version')
        collect_target(self.store, self.target)
        with patch.object(CollectionStore, '_source_body_archive', side_effect=AssertionError('archive read')):
            with self.assertRaisesRegex(RuntimeError, '^collection_graph_version_changed$'):
                read_source_body(self.root, **old)
            for options in ({'target_id': 'files:unknown'}, {'projects': ['outside']}, {'node_id': 'files:unknown'}):
                self.assert_code('alden_source_body_unavailable', **options)

    def test_paused_retained_target_is_readable_but_denied_or_retracted_is_not(self):
        self.fixture(b'paused retained text')
        with self.store.database() as db:
            db.execute('UPDATE targets SET enabled=0 WHERE id=?', (self.target,))
        self.assertEqual(self.read()['body_window']['text'].encode(), self.data)
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.target,))
        self.assert_code('alden_source_body_unavailable')
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=?", (self.target,))
            db.execute("UPDATE memberships SET availability='unavailable' WHERE target_id=?", (self.target,))
        self.assert_code('alden_source_body_unavailable')

    def test_whole_capture_packet_and_record_receipt_must_validate(self):
        mutations = [
            ('source length', {'receipt_change': lambda r: r.update(source_bytes=r['source_bytes'] + 1)}),
            ('cross target', {'receipt_change': lambda r: r.update(target_id='files:other')}),
            ('duplicate ref', {'receipt_change': lambda r: r['records'].append(copy.deepcopy(r['records'][0]))}),
            ('wrong record hash', {'receipt_change': lambda r: r['records'][0].update(record_sha256='0' * 64)}),
            ('bad pointer', {'receipt_change': lambda r: r['records'][0].update(json_pointer='/files/00/record')}),
            ('duplicate JSON key', {'packet_raw': b'{"schema":1,"schema":1}'}),
            ('nonfinite JSON', {'packet_raw': b'{"schema":NaN}'}),
            ('wrong blob', {'packet_change': lambda p: p['blobs_base64'].update({next(iter(p['blobs_base64'])): 'eA=='})}),
        ]
        for label, options in mutations:
            with self.subTest(label=label):
                self.fixture(b'capture integrity body')
                self.alter_capture(**options)
                self.assert_code('alden_source_body_integrity')

    def test_missing_or_symlink_capture_and_corrupted_record(self):
        for case in ('missing', 'symlink', 'raw'):
            with self.subTest(case=case):
                self.fixture(b'archive fixture')
                folder, _, receipt, _ = self.capture_objects()
                if case == 'raw':
                    with self.store.database() as db:
                        path = self.store.blobs / db.execute('SELECT raw_path FROM versions WHERE id=?',
                            (self.args['expected_version'],)).fetchone()[0]
                    path.write_bytes(b'{}')
                else:
                    path = folder / receipt['source_file']; saved = path.read_bytes(); path.unlink()
                    if case == 'symlink':
                        alternate = folder / 'alternate'; alternate.write_bytes(saved); path.symlink_to(alternate)
                self.assert_code('alden_source_body_capture_unavailable' if case == 'missing' else 'alden_source_body_integrity')

    def test_capture_numeric_metadata_rejects_exponent_overflow(self):
        for token in (b'1e999', b'-1e999', b'NaN', b'Infinity', b'-Infinity', b'1e3'):
            with self.subTest(token=token):
                self.fixture(b'finite numeric metadata fixture')
                _, _, _, packet = self.capture_objects()
                raw = encoded(packet)[:-1] + b',"extra_numeric_metadata":' + token + b'}'
                self.alter_capture(packet_raw=raw)
                if token == b'1e3':
                    self.assertEqual(self.read()['body_window']['text'].encode(), self.data)
                else:
                    self.assert_code('alden_source_body_integrity')

    def test_equal_bytes_preserve_distinct_file_identities(self):
        self.fixture(b'equal original bytes')
        (self.originals / 'two.md').write_bytes(self.data)
        self.manifest.write_bytes(encoded({'schema': 1, 'files': [
            {'id': 'one', 'path': 'one.md'}, {'id': 'two', 'path': 'two.md'}]}))
        collect_target(self.store, self.target); capture_source(self.store, self.target)
        nodes = self.store.graph_page(projects=['one'], limit=10)['nodes']
        self.assertEqual(len(nodes), 2)
        windows = [self.read(node_id=n['id'], expected_version=n['source_version'])['body_window'] for n in nodes]
        self.assertEqual(len({w['binding']['document_id'] for w in windows}), 2)
        self.assertEqual({w['binding']['input_sha256'] for w in windows}, {digest(self.data)})
        self.assertTrue(all(w['text'].encode() == self.data for w in windows))
        _, _, _, packet = self.capture_objects()
        self.assertEqual(len(packet['blobs_base64']), 1)

    def test_unrelated_target_revision_does_not_revoke_current_selected_capture(self):
        self.fixture(b'selected current original')
        (self.originals / 'two.md').write_bytes(b'other file first')
        self.manifest.write_bytes(encoded({'schema': 1, 'files': [
            {'id': 'one', 'path': 'one.md'}, {'id': 'two', 'path': 'two.md'}]}))
        collect_target(self.store, self.target); capture_source(self.store, self.target)
        cursor = json.loads(self.store.target(self.target)['cursor']); retained = cursor['source_capture']
        self.assertEqual(self.read()['body_window']['text'].encode(), self.data)
        (self.originals / 'two.md').write_bytes(b'other file second')
        collect_target(self.store, self.target)
        changed = json.loads(self.store.target(self.target)['cursor'])
        self.assertNotEqual(cursor['source_revision'], changed['source_revision'])
        # Fixture explicitly carries a still-valid selected-record receipt.
        # This does not assert automatic archive-reference retention by intake.
        changed['source_capture'] = retained
        with self.store.database() as db:
            db.execute('UPDATE targets SET cursor=? WHERE id=?', (encoded(changed).decode(), self.target))
        self.assertEqual(self.read()['body_window']['text'].encode(), self.data)
        other = identity('files', encoded(['body-originals', 'two']).decode())
        current = next(n for n in self.store.graph_page(projects=['one'], limit=10)['nodes'] if n['id'] == other)
        self.assert_code('alden_source_body_capture_unavailable', node_id=other, expected_version=current['source_version'])

    def test_utf16_and_non_text_formats_do_not_inherit_utf8_completeness(self):
        for data, name, code in ((('scopeprobe UTF16 한글').encode('utf-16'), 'one.txt',
                                  'alden_source_body_encoding_unsupported'),
                                 (b'name,value\none,two\n', 'one.csv', 'alden_source_body_format_unsupported')):
            with self.subTest(name=name):
                self.fixture(data, name=name)
                self.assert_code(code)

    def test_final_fresh_authority_rejects_concurrent_changes(self):
        real = files.original_utf8_window
        for case in ('permission', 'membership', 'document', 'version', 'config', 'capture'):
            with self.subTest(case=case):
                self.fixture(b'fresh authority fixture')
                def mutate(*args, **kwargs):
                    result = real(*args, **kwargs)
                    if case == 'version':
                        self.original.write_text('new committed version'); collect_target(self.store, self.target)
                    else:
                        with self.store.database() as db:
                            if case == 'permission':
                                db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.target,))
                            elif case == 'membership':
                                db.execute("UPDATE memberships SET availability='unavailable' WHERE target_id=?", (self.target,))
                            elif case == 'document':
                                db.execute("UPDATE documents SET availability='unavailable' WHERE id=?", (self.node['id'],))
                            elif case == 'config':
                                config = self.store.target(self.target)['config']; config['root'] += '-changed'
                                db.execute('UPDATE targets SET config=? WHERE id=?', (encoded(config).decode(), self.target))
                            else:
                                cursor = json.loads(self.store.target(self.target)['cursor']); cursor.pop('source_capture')
                                db.execute('UPDATE targets SET cursor=? WHERE id=?', (encoded(cursor).decode(), self.target))
                    return result
                with patch.object(files, 'original_utf8_window', side_effect=mutate):
                    if case == 'version':
                        with self.assertRaisesRegex(RuntimeError, '^collection_graph_version_changed$'):
                            self.read()
                    else:
                        self.assert_code('alden_source_body_unavailable')

    def test_cancellation_after_verified_decode_discards_result(self):
        self.fixture(b'cancel fixture'); cancelled = [False]; real = files.original_utf8_window
        def cancel(*args, **kwargs):
            result = real(*args, **kwargs); cancelled[0] = True; return result
        with patch.object(files, 'original_utf8_window', side_effect=cancel), self.assertRaises(AldenCancelled):
            self.read(cancelled=lambda: cancelled[0])

    def test_graph_opt_in_is_exact_and_old_detail_unchanged(self):
        self.fixture(b'graph transport body'); detail = self.detail()
        query = {'projects': ['one'], 'focus': self.node['id'], 'target_id': self.target,
                 'expected_version': self.args['expected_version'], 'body_window': {}}
        self.assertEqual(read_action(self.root, 'collection-graph', json.dumps(query)), self.read())
        from alden_graph_transport import pack_graph
        self.assertEqual(pack_graph(self.read()), self.read())
        for bad in ({**query, 'details': True}, {**query, 'body_window': {'path': '/unused'}}, {**query, 'projects': []}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, '^collection_source_body_request_invalid$'):
                read_action(self.root, 'collection-graph', json.dumps(bad))
        self.assertEqual(detail, self.detail())

    def test_mcp_catalog_validation_success_and_safe_errors(self):
        self.fixture(b'MCP body fixture')
        server = KnowledgeServer(self.root, ['one'])
        server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {}}})
        server.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        def call(args):
            return server.dispatch({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                'params': {'name': 'alden_knowledge_source_body', 'arguments': args}})
        catalog = {t['name']: t for t in server.tool_catalog()}
        self.assertEqual(len(catalog), 5)
        schema = catalog['alden_knowledge_source_body']['inputSchema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['required'], ['projects', 'node_id', 'target_id', 'expected_version'])
        response = call(self.args)
        self.assertFalse(response['result']['isError'], response)
        self.assertEqual(json.loads(response['result']['content'][0]['text']), self.read())
        for change in ({'max_bytes': True}, {'projects': ['outside']}, {'limit': 1}, {'target_id': ''}):
            with self.subTest(change=change):
                self.assertEqual(call({**self.args, **change})['error']['code'], -32602)
        for change, code in (({'offset_bytes': len(self.data) + 1}, 'alden_source_body_cursor_invalid'),
                             ({'target_id': 'files:absent'}, 'alden_source_body_unavailable')):
            response = call({**self.args, **change})
            self.assertTrue(response['result']['isError'])
            self.assertIn(code, json.dumps(response)); self.assertNotIn('body_window', json.dumps(response))
        self.original.write_text('updated'); collect_target(self.store, self.target)
        response = call(self.args)
        self.assertTrue(response['result']['isError'])
        self.assertIn('alden_source_version_changed', json.dumps(response))
        self.assertNotIn('updated', json.dumps(response))


if __name__ == '__main__':
    unittest.main()
