"""Voice project scope over real private collection/corpus/OSK fixtures.

The OSK engine binds one vault per process, so the shared readback fixture runs
in a child Python process, as in test_alden_osk.py. Retrieval is never stubbed.
Only embedding configuration/model calls and network access are patched.
"""
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import unicodedata
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
ALLOWED = 'scope-allowed'
DENIED = 'scope-denied'
ALLOWED_BODY = 'ALLOWED_COLLECTION_CANARY'
DENIED_BODY = 'DENIED_COLLECTION_CANARY'
OSK_BODY = 'ALLOWED_OSK_CANARY'


def _corpus(root):
    """Small published snapshot using the existing test_alden_corpus schema."""
    account = 'a' * 64
    folder = root / 'knowledge/corpus' / account
    folder.mkdir(parents=True)
    room = 'kakao:' + account + ':room:42'
    with sqlite3.connect(folder / 'context.sqlite3') as db:
        db.executescript('''
            CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE alden_rooms(chat TEXT PRIMARY KEY,chat_id TEXT,label TEXT);
            CREATE TABLE alden_authors(author_id TEXT PRIMARY KEY,label TEXT);
            CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat TEXT,chat_id TEXT,
                log_id TEXT,author_id TEXT,user_name TEXT,message TEXT,date,
                is_self INTEGER,message_type INTEGER,source TEXT);
            CREATE INDEX corpus_by_room ON alden_messages(chat,id);
            CREATE INDEX corpus_by_actor ON alden_messages(author_id,chat,id);
            CREATE VIEW context_messages AS SELECT *,X'' AS vector FROM alden_messages;
            CREATE VIRTUAL TABLE context_messages_fts USING fts5(message,user_name,chat,
                content='alden_messages',content_rowid='id');
        ''')
        db.executemany('INSERT INTO corpus_meta VALUES(?,?)', [
            ('account', account), ('snapshot', 'scope-fixture'), ('complete', '1')])
        db.execute('INSERT INTO alden_rooms VALUES(?,?,?)', (room, '42', '범위테스트방'))
        db.execute('INSERT INTO alden_authors VALUES(?,?)', ('7', 'Fixture speaker'))
        db.execute('INSERT INTO alden_messages VALUES(?,?,?,?,?,?,?,?,?,?,?)', (
            1, room, '42', '1001', '7', 'Fixture speaker',
            'scopeprobe ALLOWED_CORPUS_CANARY', '2026-10-01', 0, 1, room))
        db.execute("INSERT INTO context_messages_fts(context_messages_fts) VALUES('rebuild')")
    (folder.parent / 'current.json').write_text(json.dumps({
        'schema_version': 1, 'account': account, 'snapshot': 'scope-fixture'}))


def _fixture_readback():
    sys.path.insert(0, str(SCRIPTS))
    import alden_corpus
    import alden_voice as voice
    import alden_osk as osk
    import alden_osk_retrieval as canonical
    import alden_collection_retrieval as collection
    import auto_reply_knowledge_graph as kg
    from alden_abort import AbortController
    from alden_collection import CollectionStore, identity

    with TemporaryDirectory(prefix='alden-request-scope-') as temporary, \
            patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')) as network, \
            patch.object(kg, '_local_dense_embeddings', side_effect=AssertionError('embedding forbidden')) as embedding, \
            patch.object(kg, '_active_dense_embedding_model', return_value='fixture-encoder'), \
            patch.object(kg, '_dense_endpoint_identity', return_value='fixture://offline'):
        root = Path(temporary).resolve()
        store = CollectionStore(root)
        for project, label, body in [
                (ALLOWED, '허용분야 지식 그래프', ALLOWED_BODY),
                (DENIED, '차단분야 지식 그래프', DENIED_BODY)]:
            options = dict(platform='graph', original_id=project, kind='source',
                           label=label, projects=[project])
            target = store.register(**options)
            store.ingest(target, [{'original_id': project + '-source',
                                  'label': 'scopeprobe original', 'text': 'scopeprobe ' + body}])
            if project == DENIED:
                # Retained source, then permission revocation through the store API.
                store.register(**options, permission='denied')
        for original, label, project, permission in [
                ('shared-allowed', '공통분야 지식 그래프', ALLOWED, 'local-private'),
                ('shared-denied', '공통분야 지식 그래프', DENIED, 'denied'),
                ('decomposed-denied', unicodedata.normalize('NFD', '분해분야 지식 그래프'), DENIED, 'denied'),
                ('wide-denied', 'Ａ 지식 그래프', DENIED, 'denied')]:
            store.register(platform='graph', original_id=original, kind='source',
                           label=label, projects=[project], permission=permission)
        synchronized = osk.synchronize(root, {
            'ok': True, 'indexed_at': 42, 'stale': False, 'nodes': [], 'edges': []})
        assert synchronized['ok'] and not synchronized['pending'] and not synchronized['conflicts'], synchronized
        _, _, _, writer = osk._load_engine(root)
        note = writer.create_node('scopeprobe canonical note', 'scopeprobe unrelated permitted note',
                                  'scopeprobe ' + OSK_BODY, 'agent', space=osk.SOURCE_SPACE)
        _corpus(root)

        def snapshot():
            paths = [store.path, *store.blobs.rglob('*'),
                     *(root / 'knowledge/corpus').rglob('*'),
                     *(osk._home(root) / 'vault').rglob('*'), osk._home(root) / 'sync.json']
            return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in paths if p.is_file()}

        before = snapshot()
        observations = {}
        requests = {
            'denied_id': (DENIED + ' scopeprobe 자료 찾아줘', []),
            'denied_alias': ('차단분야 scopeprobe 자료 찾아줘', []),
            'denied_label': ('차단분야 지식 그래프 scopeprobe 자료 찾아줘', []),
            'mixed': ('차단분야 허용분야 scopeprobe 자료 찾아줘', []),
            'shared_alias': ('공통분야 scopeprobe 자료 찾아줘', []),
            'denied_nfc': ('분해분야 scopeprobe 자료 찾아줘', []),
            'denied_nfd': (unicodedata.normalize('NFD', '분해분야 scopeprobe 자료 찾아줘'), []),
            'wide_alias': ('Ａ scopeprobe 자료 찾아줘', []),
            'ascii_unmatched': ('A scopeprobe 자료 찾아줘', []),
            'allowed': ('허용분야 scopeprobe 자료 찾아줘', []),
            'default': ('scopeprobe 자료 찾아줘', []),
            'unregistered': ('미등록분야 scopeprobe 자료 찾아줘', []),
            'no_search': ('scopeprobe 자료 검색하지 마',
                          [{'role': 'user', 'content': '허용분야 scopeprobe 자료 찾아줘'}]),
            'storage': ('지금 말한 scopeprobe 자료 저장해 줘',
                        [{'role': 'user', 'content': '차단분야 scopeprobe 자료 찾아줘'}]),
            'allowed_followup': ('그럼 scopeprobe 날짜는?',
                                 [{'role': 'user', 'content': '허용분야 scopeprobe 자료 찾아줘'}]),
            'denied_followup': ('그럼 scopeprobe 날짜는?',
                                [{'role': 'user', 'content': '차단분야 scopeprobe 자료 찾아줘'}]),
        }
        for name, (query, history) in requests.items():
            history_bytes = json.dumps(history, ensure_ascii=False)
            with patch.object(kg, 'retrieve_knowledge_bundle', wraps=kg.retrieve_knowledge_bundle) as bundle, \
                    patch.object(collection, 'retrieve', wraps=collection.retrieve) as collected, \
                    patch.object(canonical, 'retrieve', wraps=canonical.retrieve) as canon, \
                    patch.object(alden_corpus, 'search', wraps=alden_corpus.search) as corpus:
                reference, metrics = voice._voice_knowledge_reference(
                    query, history, root, AbortController(root).token())
            context = (json.loads(reference.split('<quoted_local_history>')[1]
                                  .split('</quoted_local_history>')[0])
                       if '<quoted_local_history>' in reference else {})
            observations[name] = {
                'query': query, 'reference': reference, 'metrics': metrics, 'context': context,
                'history_unchanged': history_bytes == json.dumps(history, ensure_ascii=False),
                'bundle_calls': [{'query': c.args[0], 'state_root': str(c.kwargs['state_root']),
                                  **{k: c.kwargs[k] for k in (
                                      'projects', 'chat_id', 'max_entities', 'max_relations') if k in c.kwargs}}
                                 for c in bundle.call_args_list],
                'collection_calls': [{'query': c.args[1], 'projects': c.kwargs['projects']}
                                     for c in collected.call_args_list],
                'osk_calls': canon.call_count, 'corpus_calls': corpus.call_count,
            }
        lower = {}
        for name, projects in [('denied', [DENIED]), ('mixed', [ALLOWED, DENIED])]:
            result = kg.retrieve_knowledge_bundle('scopeprobe 자료 찾아줘', state_root=root,
                                                  projects=projects)
            lower[name] = {k: result[k] for k in (
                'projects', 'facts', 'fact_provenance', 'eligible_documents', 'search_mode')}
        # Validate the published corpus rather than relying on a pointer placeholder.
        corpus_probe = alden_corpus.search(root, 'scopeprobe', chat_id='42')
        return {'requests': observations, 'lower': lower, 'corpus_probe': corpus_probe,
                'source_hashes_before': before, 'source_hashes_after': snapshot(),
                'network_calls': network.call_count, 'embedding_calls': embedding.call_count,
                'ids': {'allowed': identity('graph', ALLOWED + '-source'),
                        'denied': identity('graph', DENIED + '-source'), 'osk': 'osk:' + note['id']}}


class KnowledgeRequestScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--fixture'],
                                text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise AssertionError('private fixture failed:\n' + result.stdout + result.stderr)
        cls.readback = json.loads(result.stdout)

    def request(self, name):
        return self.readback['requests'][name]

    def assert_collection_scope(self, result, projects):
        self.assertEqual(len(result['bundle_calls']), 1, result)
        self.assertEqual(result['bundle_calls'][0].get('projects'), projects, result['bundle_calls'])
        self.assertEqual(result['collection_calls'], [
            {'query': result['bundle_calls'][0]['query'], 'projects': projects}])
        self.assertEqual(result['osk_calls'], 0)
        self.assertEqual(result['corpus_calls'], 0)

    def assert_empty_denied(self, name):
        result = self.request(name)
        self.assertEqual(result['metrics']['state'], 'empty', result)
        self.assertEqual(result['metrics']['sources'], 0)
        self.assertEqual(result['reference'], '')
        self.assertFalse(result['context'].get('graph_facts'))
        self.assertFalse(result['context'].get('graph_provenance'))
        self.assert_collection_scope(result, [DENIED])

    def test_denied_known_project_cannot_fall_back_to_unrelated_osk(self):
        self.assert_empty_denied('denied_id')

    def test_denied_registered_label_and_existing_short_alias_keep_scope(self):
        for name in ('denied_alias', 'denied_label'):
            with self.subTest(name=name):
                self.assert_empty_denied(name)

    def test_mixed_request_keeps_both_projects_but_only_permitted_source(self):
        result = self.request('mixed')
        self.assert_collection_scope(result, [ALLOWED, DENIED])
        self.assertEqual(result['metrics']['state'], 'found')
        self.assertIn(ALLOWED_BODY, result['reference'])
        self.assertNotIn(DENIED_BODY, result['reference'])
        proof, = result['metrics']['graph_provenance']
        self.assertEqual(proof['entity_id'], self.readback['ids']['allowed'])
        self.assertEqual(proof['requested_projects'], [ALLOWED, DENIED])
        self.assertEqual(proof['projects'], [ALLOWED])
        self.assertTrue(all(p['permission'] != 'denied' for p in proof['source_memberships']))

    def test_permitted_project_still_returns_its_original_provenance(self):
        result = self.request('allowed')
        self.assert_collection_scope(result, [ALLOWED])
        self.assertEqual(result['metrics']['state'], 'found')
        self.assertIn(ALLOWED_BODY, result['reference'])
        proof, = result['context']['graph_provenance']
        self.assertEqual(proof['entity_id'], self.readback['ids']['allowed'])
        self.assertEqual(proof['requested_projects'], [ALLOWED])
        self.assertNotIn(OSK_BODY, result['reference'])

    def test_default_and_unregistered_words_preserve_legacy_unscoped_lookup(self):
        for name in ('default', 'unregistered'):
            with self.subTest(name=name):
                result = self.request(name)
                self.assertEqual(result['metrics']['state'], 'found')
                self.assertIn(OSK_BODY, result['reference'])
                self.assertNotIn('projects', result['bundle_calls'][0])
                self.assertEqual(result['collection_calls'], [])
                self.assertEqual(result['osk_calls'], 1)
                proof, = result['metrics']['graph_provenance']
                self.assertEqual(proof['entity_id'], self.readback['ids']['osk'])

    def test_shared_label_retains_mixed_scope_with_only_allowed_material(self):
        result = self.request('shared_alias')
        self.assert_collection_scope(result, [ALLOWED, DENIED])
        self.assertEqual(result['metrics']['state'], 'found')
        self.assertIn(ALLOWED_BODY, result['reference'])
        self.assertNotIn(DENIED_BODY, result['reference'])
        self.assertNotIn(OSK_BODY, result['reference'])
        proof, = result['metrics']['graph_provenance']
        self.assertEqual(proof['requested_projects'], [ALLOWED, DENIED])
        self.assertEqual(proof['projects'], [ALLOWED])

    def test_denied_alias_keeps_canonical_forms_and_fullwidth_distinction(self):
        for name in ('denied_nfc', 'denied_nfd', 'wide_alias'):
            with self.subTest(name=name):
                self.assert_empty_denied(name)
        unmatched = self.request('ascii_unmatched')
        self.assertEqual(unmatched['metrics']['state'], 'found')
        self.assertIn(OSK_BODY, unmatched['reference'])
        self.assertNotIn('projects', unmatched['bundle_calls'][0])

    def test_current_no_search_and_storage_override_earlier_lookup(self):
        for name in ('no_search', 'storage'):
            with self.subTest(name=name):
                result = self.request(name)
                self.assertEqual(result['metrics']['state'], 'not_requested')
                self.assertEqual(result['reference'], '')
                self.assertEqual(result['bundle_calls'], [])
                self.assertEqual(result['collection_calls'], [])
                self.assertEqual((result['osk_calls'], result['corpus_calls']), (0, 0))

    def test_followup_keeps_permitted_scope_and_original_history(self):
        result = self.request('allowed_followup')
        self.assert_collection_scope(result, [ALLOWED])
        self.assertEqual(result['metrics']['state'], 'found')
        self.assertIn(ALLOWED_BODY, result['reference'])
        self.assertEqual(result['bundle_calls'][0]['query'],
                         '그럼 scopeprobe 날짜는? 허용분야 scopeprobe 자료 찾아줘')
        self.assertTrue(result['history_unchanged'])

    def test_followup_cannot_drop_earlier_denied_project(self):
        self.assert_empty_denied('denied_followup')
        result = self.request('denied_followup')
        self.assertEqual(result['bundle_calls'][0]['query'],
                         '그럼 scopeprobe 날짜는? 차단분야 scopeprobe 자료 찾아줘')
        self.assertTrue(result['history_unchanged'])

    def test_real_lower_layer_already_filters_denied_source_without_mutation(self):
        denied = self.readback['lower']['denied']
        self.assertEqual(denied['projects'], [DENIED])
        self.assertEqual(denied['eligible_documents'], 0)
        self.assertEqual((denied['facts'], denied['fact_provenance']), ([], []))
        mixed = self.readback['lower']['mixed']
        proof, = mixed['fact_provenance']
        self.assertEqual(proof['requested_projects'], [ALLOWED, DENIED])
        self.assertEqual(proof['projects'], [ALLOWED])
        self.assertNotIn(DENIED_BODY, json.dumps(self.readback['requests']))
        self.assertEqual(self.readback['source_hashes_before'], self.readback['source_hashes_after'])
        self.assertEqual((self.readback['network_calls'], self.readback['embedding_calls']), (0, 0))
        self.assertTrue(self.readback['corpus_probe']['ok'])
        item, = self.readback['corpus_probe']['items']
        self.assertEqual(item['content'], 'scopeprobe ALLOWED_CORPUS_CANARY')


if __name__ == '__main__':
    if sys.argv[1:] == ['--fixture']:
        print(json.dumps(_fixture_readback(), ensure_ascii=False))
    else:
        unittest.main()
