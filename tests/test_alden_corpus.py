"""Published-source selection, identity and quotes; no account/model/send."""
import json
import sqlite3
import time
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_auto_reply_knowledge_graph import KG
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import alden_corpus

class CorpusTests(unittest.TestCase):
    def fixture(self,base):
        root=base/'state';root.mkdir()
        account='a'*64;folder=root/'knowledge/corpus'/account;folder.mkdir(parents=True)
        source=folder/'context.sqlite3';c=sqlite3.connect(source)
        c.executescript('''CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE context_message_topics(message_id INTEGER,topic TEXT,PRIMARY KEY(message_id,topic));CREATE TABLE context_topic_stats(chat TEXT,topic TEXT,message_count INTEGER);CREATE TABLE alden_rooms(chat TEXT PRIMARY KEY,chat_id TEXT,label TEXT);CREATE TABLE alden_authors(author_id TEXT PRIMARY KEY,label TEXT);
          CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat TEXT,chat_id TEXT,log_id TEXT,author_id TEXT,user_name TEXT,message TEXT,date,is_self INTEGER,message_type INTEGER,source TEXT,UNIQUE(chat_id,log_id));
          CREATE INDEX corpus_by_room ON alden_messages(chat,id);CREATE INDEX corpus_by_actor ON alden_messages(author_id,chat,id);
          CREATE VIEW context_messages AS SELECT *,X'' AS vector FROM alden_messages;
          CREATE VIRTUAL TABLE context_messages_fts USING fts5(message,user_name,chat,content='alden_messages',content_rowid='id');''')
        c.execute('INSERT INTO corpus_meta VALUES(?,?)',('account',account));c.execute('INSERT INTO corpus_meta VALUES(?,?)',('snapshot','fixed'))
        c.execute('INSERT INTO corpus_meta VALUES(?,?)',('complete','1'))
        c.executemany('INSERT INTO alden_authors VALUES(?,?)',[('7','같은 이름'),('8','같은 이름')])
        for room in ('42','84'):
            key=f'kakao:{account}:room:{room}';c.execute('INSERT INTO alden_rooms VALUES(?,?,?)',(key,room,'같은 방 이름'))
            for actor in ('7','8'):
                for i in range(60):
                    c.execute('INSERT INTO alden_messages(chat,chat_id,log_id,author_id,user_name,message,date,is_self,message_type,source) VALUES(?,?,?,?,?,?,?,?,?,?)',(key,room,str(9007199254740997+i+int(actor)*1000),actor,'같은 이름',f'원문 행 {room} {actor} {i} <script>','2026-10-02',actor=='7',1,key))
        c.execute("INSERT INTO context_messages_fts(context_messages_fts) VALUES('rebuild')");c.commit();c.close()
        (folder.parent/'current.json').write_text(json.dumps({'schema_version':1,'account':account,'snapshot':'fixed'}))
        return root,account,source

    def test_same_names_do_not_merge_numeric_authors_or_rooms(self):
        with TemporaryDirectory() as td:
            root,account,source=self.fixture(Path(td));graph=KG._connect_kg(root/'graph.sqlite3')
            try:
                KG.index_chat_entities(graph,root);KG.index_person_entities(graph,root);KG.index_membership_relations(graph,root)
                people=graph.execute("SELECT entity_id,name FROM kg_entities WHERE category='대화 상대'").fetchall()
                self.assertEqual(set(people),{(f'person:kakao:{account}:actor:7','같은 이름'),(f'person:kakao:{account}:actor:8','같은 이름')})
                rooms=graph.execute("SELECT entity_id,name,aliases_json FROM kg_entities WHERE category='대화방'").fetchall()
                self.assertEqual(len(rooms),2);self.assertEqual(len({r[1] for r in rooms}),2)
                self.assertTrue(all(r[1].startswith('같은 방 이름 · #') and '같은 방 이름' in json.loads(r[2]) for r in rooms))
                self.assertEqual(graph.execute("SELECT count(*) FROM kg_relations WHERE relation='TALKED_IN'").fetchone()[0],4)
            finally:graph.close()

    def test_scope_and_quote_roles_are_preserved_and_accounts_do_not_cross(self):
        with TemporaryDirectory() as td:
            root,account,_=self.fixture(Path(td))
            result=alden_corpus.search(root,'원문',chat_id='42',author_id='7')
            self.assertTrue(result['ok']);self.assertTrue(result['items'])
            self.assertTrue(all(r['chat_id']=='42' and r['author_id']=='7' and r['source_role']=='outgoing_unclassified' for r in result['items']))
            self.assertIn('<script>',result['items'][0]['content'])
            self.assertEqual(alden_corpus.search(root,'원문',expected_account='b'*64)['items'],[])
            with self.assertRaises(ValueError):alden_corpus.search(root,'원문',chat_id='42 OR 1=1')

    def test_equal_room_names_require_disambiguation(self):
        with TemporaryDirectory() as td:
            root,_,_=self.fixture(Path(td))
            self.assertEqual(alden_corpus.resolve_room(root,'카카오톡 같은 방 이름 대화')['state'],'ambiguous')
            self.assertEqual(alden_corpus.resolve_room(root,'없는 이름')['state'],'none')

    def test_room_displays_read_unselected_corpus_rooms_and_keep_numeric_identity(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.executemany('INSERT INTO alden_rooms VALUES(?,?,?)',
                    [(f'kakao:{account}:room:{i}', str(i), '추가 방') for i in range(100, 150)])
            result = alden_corpus.room_displays(root, ['00149', 42, '84', '999'], account)
            self.assertEqual(set(result), {'149', '42', '84'})
            self.assertEqual(result['149'], {'label': '추가 방', 'label_source': 'snapshot'})
            self.assertNotEqual(result['42']['label'], result['84']['label'])
            self.assertTrue(all(row['label_source'] == 'snapshot' for row in result.values()))
            self.assertFalse((root / KG.KNOWLEDGE_GRAPH_DB_NAME).exists())

    def test_room_displays_restore_saved_title_before_participant_alias(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            key = f'kakao:{account}:room:42'
            alias = '기록된 사람 대화 · #' + KG._short_identity(key)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='' WHERE chat_id='42'")
            (root / 'menubar-room-catalog.json').write_text(json.dumps({'rooms': [
                {'chat_id': '42', 'title': alias}, {'chat_id': '84', 'title': '낡은 제목'}]}))
            history = root / 'catalog-history'; history.mkdir()
            (history / 'saved.json').write_text(json.dumps({'rooms': [{'chat_id': 42, 'title': '부자멘토멘티'}]}))
            before = path.read_bytes()
            result = alden_corpus.room_displays(root, ['42', '84'], account)
            self.assertEqual(result['42'], {'label': '부자멘토멘티', 'label_source': 'catalog_history'})
            self.assertEqual(result['84'], {'label': '같은 방 이름', 'label_source': 'snapshot'})
            self.assertEqual(alden_corpus.resolve_room(root, '부자멘토멘티 기록')['chat_id'], '42')
            graph = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_chat_entities(graph, root)
                row = graph.execute('SELECT name,description FROM kg_entities WHERE entity_id=?', ('chat:' + key,)).fetchone()
                self.assertEqual(row[0], '부자멘토멘티')
                self.assertIn('과거 제목', row[1])
            finally:
                graph.close()
            self.assertEqual(path.read_bytes(), before)

    def test_room_displays_catalog_precedence_and_account_scoping(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='' WHERE chat_id='42'")
            catalog = root / 'menubar-room-catalog.json'
            catalog.write_text(json.dumps({'account': account, 'rooms': [{'chat_id': 42, 'title': '현재 저장 제목'}]}))
            history = root / 'catalog-history'; history.mkdir()
            (history / 'past.json').write_text(json.dumps({'rooms': [{'chat_id': 42, 'title': '과거 저장 제목'}]}))
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42'],
                             {'label': '현재 저장 제목', 'label_source': 'catalog'})
            catalog.write_text(json.dumps({'account': 'b' * 64, 'rooms': [{'chat_id': 42, 'title': '다른 계정'}]}))
            with self.assertRaisesRegex(RuntimeError, 'corpus_catalog_account_mismatch'):
                alden_corpus.room_displays(root, [42], account)
            catalog.unlink()
            (history / 'past.json').write_text(json.dumps({'account': 'b' * 64, 'rooms': [{'chat_id': 42, 'title': '다른 계정'}]}))
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'activity_alias')

    def test_participant_aliases_are_grounded_in_requested_room_rows(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='제목 미확인'")
                db.execute("UPDATE alden_authors SET label='전역 명부의 다른 이름'")
                db.execute("UPDATE alden_messages SET user_name=CASE WHEN is_self=1 THEN '나' WHEN chat_id='42' THEN '이 방 참여자' ELSE '다른 방 참여자' END")
            result = alden_corpus.room_displays(root, [42, 84], account)
            for number, peer in [('42', '이 방 참여자'), ('84', '다른 방 참여자')]:
                self.assertEqual(result[number]['label_source'], 'activity_alias')
                self.assertTrue(result[number]['label'].startswith(peer + ' 대화 · #'))
                self.assertNotIn('전역', result[number]['label'])

    def test_unresolved_is_honest_when_recent_rows_only_have_self_system_or_unknown_names(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='' WHERE chat_id='42'")
                db.execute("UPDATE alden_messages SET user_name='Unknown' WHERE chat_id='42'")
                db.execute("UPDATE alden_messages SET user_name='시스템',author_id='0' WHERE chat_id='42' AND id % 2=0")
            result = alden_corpus.room_displays(root, [42], account)['42']
            self.assertEqual(result['label_source'], 'unresolved')
            self.assertTrue(result['label'].startswith('보관 대화 · #'))

    def test_room_displays_require_complete_matching_publication(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with self.assertRaisesRegex(RuntimeError, 'corpus_account_mismatch'):
                alden_corpus.room_displays(root, [42], 'b' * 64)
            for key, bad, good, reason in [('account', 'b' * 64, account, 'corpus_account_mismatch'),
                                          ('snapshot', 'unpublished', 'fixed', 'corpus_not_ready'),
                                          ('complete', '0', '1', 'corpus_not_ready')]:
                with self.subTest(key=key):
                    with sqlite3.connect(path) as db:
                        db.execute('UPDATE corpus_meta SET value=? WHERE key=?', (bad, key))
                    with self.assertRaisesRegex(RuntimeError, reason):
                        alden_corpus.room_displays(root, [42], account)
                    with sqlite3.connect(path) as db:
                        db.execute('UPDATE corpus_meta SET value=? WHERE key=?', (good, key))
            (path.parent.parent / 'current.json').unlink()
            # Unpublished/legacy files must not supply names.
            self.assertEqual(alden_corpus.room_displays(root, [42], account), {})

    def test_room_display_inputs_are_bounded_and_canonical(self):
        with TemporaryDirectory() as td:
            root, account, _ = self.fixture(Path(td))
            for ids in ['42', ['42 OR 1=1'], [True], [42.0], ['４２'], ['0'], [-1], [2**63], ['1'] * 257]:
                with self.subTest(ids=str(ids)[:30]), self.assertRaises(ValueError):
                    alden_corpus.room_displays(root, ids, account)
            self.assertEqual(alden_corpus.room_displays(root, []), {})

    def test_room_displays_reject_mismatched_room_identity_and_unbound_expected_account(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with self.assertRaisesRegex(ValueError, 'corpus_account_invalid'):
                alden_corpus.room_displays(root, [42], '../other')
            with self.assertRaisesRegex(RuntimeError, 'corpus_account_mismatch'):
                alden_corpus.resolve_room(root, '같은 방 이름', expected_account='b' * 64)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET chat_id='84' WHERE chat_id='42'")
            with self.assertRaisesRegex(RuntimeError, 'corpus_room_identity_mismatch'):
                alden_corpus.room_displays(root, [42], account)

    def test_catalog_history_limits_and_unsafe_files_do_not_become_titles(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='' WHERE chat_id='42'")
            catalog = root / 'menubar-room-catalog.json'
            history = root / 'catalog-history'; history.mkdir()
            saved = history / 'saved.json'
            saved.write_text(json.dumps({'rooms': [{'chat_id': 42, 'title': '저장 제목'}]}))
            catalog.symlink_to(saved)
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'catalog_history')
            catalog.unlink()
            catalog.write_text(' ' * (KG.ROOM_CATALOG_MAX_BYTES + 1))
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label'], '저장 제목')
            for index in range(KG.ROOM_CATALOG_HISTORY_LIMIT):
                (history / f'{index}.json').write_text('{}')
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'activity_alias')

    def test_catalog_conflicts_and_generated_aliases_are_not_official_titles(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='' WHERE chat_id='42'")
            catalog = root / 'menubar-room-catalog.json'
            variants = [
                [{'chat_id': 42, 'title': '첫 제목'}, {'chat_id': 42, 'title': '충돌 제목'}],
                [{'chat_id': 42, 'title': '저장한 참여자 별칭', 'label_source': 'activity_alias'}],
                [{'chat_id': 42, 'title': '코인 관련 대화', 'label_source': 'topic_alias'}],
                [{'chat_id': 42, 'title': {'invalid': 'not a title'}}],
            ]
            for rooms in variants:
                with self.subTest(rooms=rooms):
                    catalog.write_text(json.dumps({'rooms': rooms}))
                    self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'activity_alias')

    def test_room_displays_bound_reads_before_filtering_and_do_not_read_bodies(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='' WHERE chat_id='42'")
                # The old room-wide aggregate exceeds the instruction budget.
                db.executemany('INSERT INTO alden_messages(chat,chat_id,log_id,author_id,user_name,message,is_self) VALUES(?,?,?,?,?,?,?)',
                    [(f'kakao:{account}:room:{room}', room, str(i), '7', '나', '비공개 원문', 1)
                     for room in ('42', '84') for i in range(5000)])
            connect = sqlite3.connect
            statements, steps, handles = [], [], []
            def bounded_connect(*args, **kwargs):
                db = connect(*args, **kwargs); handles.append(db)
                def authorize(action, table, column, *_):
                    if action == sqlite3.SQLITE_READ and (table == 'alden_authors' or column in ('message', 'attachment')):
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                db.set_trace_callback(statements.append)
                def progress():
                    steps.append(1)
                    return int(len(steps) > 100)
                db.set_progress_handler(progress, 100)
                return db
            with mock.patch.object(alden_corpus.sqlite3, 'connect', side_effect=bounded_connect):
                result = alden_corpus.room_displays(root, ['42'], account)
            self.assertEqual(result['42']['label_source'], 'unresolved')
            message_queries = [sql for sql in statements if 'FROM alden_messages ' in sql]
            self.assertEqual(len(message_queries), 2)
            self.assertIn('LIMIT 256', message_queries[0])
            self.assertIn('LIMIT 512', message_queries[1])
            for sql in message_queries:
                self.assertIn('INDEXED BY corpus_by_room', sql)
                self.assertIn(f'kakao:{account}:room:42', sql)
                self.assertNotIn(f'kakao:{account}:room:84', sql)
            self.assertFalse(any('GROUP BY' in sql for sql in statements))
            for handle in handles:
                with self.assertRaises(sqlite3.ProgrammingError):
                    handle.execute('SELECT 1')

    def topic_fixture(self, base):
        root, account, path = self.fixture(base)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE alden_rooms SET label='' WHERE chat_id='42'")
            db.execute("UPDATE alden_messages SET user_name='' WHERE chat_id='42'")
            ids = [r[0] for r in db.execute("SELECT id FROM alden_messages WHERE chat_id='42' AND is_self=0 ORDER BY id")]
        return root, account, path, ids

    def test_topic_alias_minimum_dominance_and_ambiguity_rules(self):
        cases = [
            ([('coins', 20), ('stocks', 10)], 'topic_alias'),
            ([('coins', 19)], 'unresolved'),
            ([('coins', 20), ('stocks', 11)], 'unresolved'),
            ([('coins', 20), ('stocks', 20)], 'unresolved'),
            ([('coins', 20), ('stocks', 10), ('news', 10)], 'unresolved'),
            ([('identity', 20)], 'unresolved'),
            ([('unknown_topic', 20)], 'unresolved'),
        ]
        for counts, source in cases:
            with self.subTest(counts=counts), TemporaryDirectory() as td:
                root, account, path, ids = self.topic_fixture(Path(td))
                with sqlite3.connect(path) as db:
                    offset = 0
                    for topic, count in counts:
                        db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, topic) for i in ids[offset:offset+count]])
                        offset += count
                result = alden_corpus.room_displays(root, [42], account)['42']
                self.assertEqual(result['label_source'], source)
                self.assertTrue(result['label'].startswith('코인 관련 대화 · #' if source == 'topic_alias' else '보관 대화 · #'))

    def test_topic_alias_requires_sample_coverage_and_excludes_self_system_rows(self):
        for mode in ('sparse', 'self', 'system', 'non_text', 'other_room'):
            with self.subTest(mode=mode), TemporaryDirectory() as td:
                root, account, path, ids = self.topic_fixture(Path(td))
                with sqlite3.connect(path) as db:
                    db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'coins') for i in ids[:20]])
                    if mode == 'sparse':
                        db.execute("UPDATE alden_messages SET is_self=0 WHERE chat_id='42'")
                    elif mode == 'self':
                        db.execute("UPDATE alden_messages SET is_self=1 WHERE chat_id='42'")
                    elif mode == 'system':
                        db.execute("UPDATE alden_messages SET author_id='0' WHERE chat_id='42'")
                    elif mode == 'non_text':
                        db.execute("UPDATE alden_messages SET message_type=2 WHERE chat_id='42'")
                    else:
                        db.execute('UPDATE context_message_topics SET message_id=message_id+120')
                self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'unresolved')

    def test_topic_alias_never_overrides_existing_title_or_participant_name(self):
        for source in ('snapshot', 'catalog', 'catalog_history', 'activity_alias'):
            with self.subTest(source=source), TemporaryDirectory() as td:
                root, account, path, ids = self.topic_fixture(Path(td))
                with sqlite3.connect(path) as db:
                    db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'coins') for i in ids])
                    if source == 'snapshot':
                        db.execute("UPDATE alden_rooms SET label='알려진 이름' WHERE chat_id='42'")
                    elif source == 'activity_alias':
                        db.execute("UPDATE alden_messages SET user_name='기록된 참여자' WHERE chat_id='42'")
                if source in ('catalog', 'catalog_history'):
                    catalog = root / 'menubar-room-catalog.json' if source == 'catalog' else root / 'catalog-history/past.json'
                    catalog.parent.mkdir(exist_ok=True)
                    catalog.write_text(json.dumps({'rooms': [{'chat_id': 42, 'title': '알려진 이름'}]}))
                with mock.patch('auto_reply_knowledge_graph._corpus_topic_aliases', wraps=KG._corpus_topic_aliases) as topics:
                    result = alden_corpus.room_displays(root, [42], account)['42']
                self.assertEqual(result['label_source'], source)
                self.assertTrue(result['label'].startswith('기록된 참여자 대화' if source == 'activity_alias' else '알려진 이름'))
                self.assertEqual(set(topics.call_args.args[1]), set())

    def test_graph_producer_and_room_displays_share_topic_and_archive_policy(self):
        with TemporaryDirectory() as td:
            root, account, path, ids = self.topic_fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label=''")
                db.execute("UPDATE alden_messages SET user_name=''")
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'coins') for i in ids[:30]])
            expected = alden_corpus.room_displays(root, [42, 84], account)
            graph = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_chat_entities(graph, root)
                for room in ('42', '84'):
                    name, description, raw = graph.execute('SELECT name,description,evidence_json FROM kg_entities WHERE entity_id=?',
                        (f'chat:kakao:{account}:room:{room}',)).fetchone()
                    evidence = json.loads(raw)
                    self.assertEqual(name, expected[room]['label'])
                    self.assertEqual(evidence['label_source'], expected[room]['label_source'])
                    self.assertIsNone(evidence['confirmed_at'])
                    self.assertIn('공식 제목은 확인되지 않았습니다', description)
            finally:
                graph.close()
            report = KG.collect_knowledge_graph(root / 'context.sqlite3', state_root=root, read_only=True)
            self.assertEqual({node['label_source'] for node in report['nodes']}, {'topic_alias', 'unresolved'})

    def test_duplicate_topic_names_keep_separate_ids_and_archive_cannot_become_title(self):
        with TemporaryDirectory() as td:
            root, account, path, ids = self.topic_fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label=''")
                db.execute("UPDATE alden_messages SET user_name=''")
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i + offset, 'coins') for offset in (0, 120) for i in ids[:30]])
            result = alden_corpus.room_displays(root, [42, 84], account)
            self.assertTrue(all(r['label_source'] == 'topic_alias' for r in result.values()))
            self.assertNotEqual(result['42']['label'], result['84']['label'])
            for title in [result['42']['label'], '보관 대화 · #' + KG._short_identity(f'kakao:{account}:room:42')]:
                (root / 'menubar-room-catalog.json').write_text(json.dumps({'rooms': [{'chat_id': 42, 'title': title}]}))
                self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'topic_alias')

    def test_topic_lookup_is_indexed_bounded_and_never_reads_stats_view_or_text(self):
        with TemporaryDirectory() as td:
            root, account, path, ids = self.topic_fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute('DROP TABLE context_topic_stats')
                db.execute('CREATE VIEW context_topic_stats AS SELECT message FROM alden_messages')
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'coins') for i in ids[:30]])
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'stocks') for i in range(1000, 11000)])
            connect = sqlite3.connect
            statements = []; steps = []
            def guarded_connect(*args, **kwargs):
                db = connect(*args, **kwargs)
                def authorize(action, table, column, *_):
                    if action == sqlite3.SQLITE_READ and (table == 'context_topic_stats' or column in ('message', 'attachment')):
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                db.set_trace_callback(statements.append)
                def progress():
                    steps.append(1)
                    return int(len(steps) > 100)
                db.set_progress_handler(progress, 100)
                return db
            with mock.patch.object(alden_corpus.sqlite3, 'connect', side_effect=guarded_connect):
                result = alden_corpus.room_displays(root, [42], account)
            self.assertEqual(result['42']['label_source'], 'topic_alias')
            topic_queries = [q for q in statements if 'FROM context_message_topics ' in q]
            self.assertEqual(len(topic_queries), 1)
            self.assertIn('INDEXED BY', topic_queries[0])
            self.assertIn('LIMIT 8193', topic_queries[0])
            self.assertFalse(any('GROUP BY' in q or 'FROM context_topic_stats' in q for q in statements))

    def test_topic_metadata_without_index_fails_closed_and_duplicates_do_not_inflate_support(self):
        with TemporaryDirectory() as td:
            root, account, path, ids = self.topic_fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute('DROP TABLE context_message_topics')
                db.execute('CREATE TABLE context_message_topics(message_id INTEGER,topic TEXT)')
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'coins') for i in ids[:30]])
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'unresolved')
            with sqlite3.connect(path) as db:
                db.execute('CREATE INDEX topic_message_lookup ON context_message_topics(message_id)')
                db.execute('DELETE FROM context_message_topics')
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(ids[0], 'coins')] * 30)
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'unresolved')

    def test_existing_literal_archive_title_remains_a_snapshot_name(self):
        with TemporaryDirectory() as td:
            root, account, path, ids = self.topic_fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='보관 대화' WHERE chat_id='42'")
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'coins') for i in ids])
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42'],
                             {'label': '보관 대화', 'label_source': 'snapshot'})

    def test_generated_topic_alias_does_not_change_an_equal_known_title(self):
        with TemporaryDirectory() as td:
            root, account, path, ids = self.topic_fixture(Path(td))
            with sqlite3.connect(path) as db:
                db.execute("UPDATE alden_rooms SET label='코인 관련 대화' WHERE chat_id='84'")
                db.executemany('INSERT INTO context_message_topics VALUES(?,?)', [(i, 'coins') for i in ids])
            result = alden_corpus.room_displays(root, [42, 84], account)
            self.assertEqual(result['84'], {'label': '코인 관련 대화', 'label_source': 'snapshot'})
            self.assertEqual(result['42']['label_source'], 'topic_alias')
            self.assertTrue(result['42']['label'].startswith('코인 관련 대화 · #'))
            self.assertNotEqual(result['42']['label'], result['84']['label'])

    def observation_cache(self, path, account, rooms, **overrides):
        cache = path.parent / 'room-observations.json'
        payload = {'schema_version': 1, 'account': account, 'corpus_snapshot': 'fixed',
                   'observed_at': time.time(), 'rooms': rooms}
        payload.update(overrides)
        cache.write_text(json.dumps(payload))
        return cache

    def test_verified_observation_overrides_snapshot_and_distinguishes_display_name(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            self.observation_cache(path, account, [
                {'chat_id': '42', 'label': '관측한 제목', 'label_kind': 'room_title', 'source': 'ax'},
                {'chat_id': '84', 'label': '관측한 상대 표시', 'label_kind': 'display_name', 'source': 'local_history_rooms'},
            ])
            result = alden_corpus.room_displays(root, [42, 84], account)
            self.assertEqual(result['42'], {'label': '관측한 제목', 'label_source': 'observed_title'})
            self.assertEqual(result['84'], {'label': '관측한 상대 표시', 'label_source': 'observed_display'})
            self.assertEqual(alden_corpus.resolve_room(root, '관측한 제목 기록', expected_account=account)['chat_id'], '42')
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT label FROM alden_rooms WHERE chat_id='42'").fetchone()[0], '같은 방 이름')

    def test_observation_cannot_bind_an_account_or_room_by_matching_names(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            before = alden_corpus.room_displays(root, [42, 84], account)
            self.observation_cache(path, account, [
                {'chat_id': '999', 'label': '같은 방 이름', 'label_kind': 'room_title', 'source': 'local_chats'}])
            self.assertEqual(alden_corpus.room_displays(root, [42, 84, 999], account), before)
            self.observation_cache(path, 'b' * 64, [
                {'chat_id': '42', 'label': '같은 방 이름', 'label_kind': 'room_title', 'source': 'local_chats'}])
            with self.assertRaisesRegex(RuntimeError, 'corpus_observation_account_mismatch'):
                alden_corpus.room_displays(root, [42], account)

    def test_old_snapshot_stale_or_unbound_observations_preserve_existing_names(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            before = alden_corpus.room_displays(root, [42, 84], account)
            row = {'chat_id': '42', 'label': '새 관측', 'label_kind': 'room_title', 'source': 'local_chats'}
            for overrides in ({'corpus_snapshot': 'old'}, {'corpus_snapshot': ''},
                              {'observed_at': time.time() - 86401}, {'observed_at': time.time() + 3600},
                              {'observed_at': True}, {'observed_at': 10**200}, {'observed_at': None}):
                with self.subTest(overrides=overrides):
                    self.observation_cache(path, account, [row], **overrides)
                    self.assertEqual(alden_corpus.room_displays(root, [42, 84], account), before)
            cache = path.parent / 'room-observations.json'
            cache.write_text(json.dumps([{'chat_id': 42, 'chat_name': '새 관측', 'display_name': ''}]))
            self.assertEqual(alden_corpus.room_displays(root, [42, 84], account), before)

    def test_partial_conflicting_or_inexact_observation_rows_do_not_overwrite(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            before = alden_corpus.room_displays(root, [42, 84], account)
            row = {'chat_id': '42', 'label': '새 관측', 'label_kind': 'room_title', 'source': 'ax'}
            variants = [[], [dict(row, label='')], [dict(row, chat_id=42)], [dict(row, chat_id='042')],
                        [dict(row, source='name_match')], [dict(row, label_kind='inferred')],
                        [row, dict(row, label='충돌하는 관측')]]
            for rooms in variants:
                with self.subTest(rooms=rooms):
                    self.observation_cache(path, account, rooms)
                    self.assertEqual(alden_corpus.room_displays(root, [42, 84], account), before)

    def test_observation_reader_rechecks_pointer_against_pinned_corpus(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            self.observation_cache(path, account, [
                {'chat_id': '42', 'label': '새 관측', 'label_kind': 'room_title', 'source': 'local_history_rooms'}])
            pointer = path.parent.parent / 'current.json'
            with sqlite3.connect(path) as db:
                labels = dict(db.execute('SELECT chat,label FROM alden_rooms'))
                pointer.write_text(json.dumps({'schema_version': 1, 'account': 'b' * 64, 'snapshot': 'fixed'}))
                with self.assertRaisesRegex(RuntimeError, 'corpus_account_mismatch'):
                    KG._room_observation_displays(db, root, labels, expected_account=account)
                pointer.write_text(json.dumps({'schema_version': 1, 'account': account, 'snapshot': 'next'}))
                self.assertEqual(KG._room_observation_displays(db, root, labels, expected_account=account), {})

    def test_observations_are_bounded_and_unsafe_files_are_ignored(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            before = alden_corpus.room_displays(root, [42, 84], account)
            row = {'chat_id': '42', 'label': '새 관측', 'label_kind': 'room_title', 'source': 'ax'}
            cache = self.observation_cache(path, account, [row] * (KG.ROOM_OBSERVATIONS_MAX_ROOMS + 1))
            self.assertEqual(alden_corpus.room_displays(root, [42, 84], account), before)
            cache.write_text(' ' * (KG.ROOM_OBSERVATIONS_MAX_BYTES + 1))
            self.assertEqual(alden_corpus.room_displays(root, [42, 84], account), before)
            cache.unlink()
            unrelated = path.parent / 'unrelated.json'
            unrelated.write_text('{}')
            cache.symlink_to(unrelated)
            self.assertEqual(alden_corpus.room_displays(root, [42, 84], account), before)

    def test_graph_and_reader_preserve_distinct_ids_with_equal_observed_names(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            self.observation_cache(path, account, [
                {'chat_id': room, 'label': '같은 관측 이름', 'label_kind': 'room_title', 'source': 'ax'} for room in ('42', '84')])
            result = alden_corpus.room_displays(root, [42, 84], account)
            self.assertEqual(len({r['label'] for r in result.values()}), 2)
            self.assertTrue(all(r['label_source'] == 'observed_title' for r in result.values()))
            graph = KG._connect_kg(root / KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_chat_entities(graph, root)
                rows = graph.execute("SELECT entity_id,name,evidence_json FROM kg_entities WHERE category='대화방'").fetchall()
                self.assertEqual(len(rows), 2)
                for identifier, label, raw in rows:
                    self.assertEqual(label, result[identifier.rsplit(':', 1)[-1]]['label'])
                    self.assertEqual(json.loads(raw)['label_source'], 'observed_title')
            finally:
                graph.close()

    def test_merged_observation_rows_keep_their_original_expiration(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            observed = 1800000000
            rows = [
                {'chat_id': '42', 'label': '보존한 관측', 'label_kind': 'room_title', 'source': 'local_chats', 'observed_at': observed},
                {'chat_id': '84', 'label': '최신 관측', 'label_kind': 'display_name', 'source': 'local_chats', 'observed_at': observed + 3600},
            ]
            self.observation_cache(path, account, rows, observed_at=observed + 3600)
            with mock.patch('auto_reply_knowledge_graph.time.time', return_value=observed + 3600):
                result = alden_corpus.room_displays(root, [42, 84], account)
            self.assertEqual(result['42']['label_source'], 'observed_title')
            self.assertEqual(result['84']['label_source'], 'observed_display')
            later = observed + 86401
            rows[1]['observed_at'] = later
            self.observation_cache(path, account, rows, observed_at=later)
            with mock.patch('auto_reply_knowledge_graph.time.time', return_value=later):
                result = alden_corpus.room_displays(root, [42, 84], account)
            self.assertEqual(result['42']['label_source'], 'snapshot')
            self.assertEqual(result['84']['label_source'], 'observed_display')

    def test_invalid_row_times_cannot_borrow_fresh_envelope_time(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            now = 1800000000
            row = {'chat_id': '42', 'label': '검증 대상', 'label_kind': 'room_title', 'source': 'local_chats'}
            for stamp in (None, True, '1800000000', float('nan'), now - 86401, now + 61):
                with self.subTest(stamp=stamp):
                    self.observation_cache(path, account, [dict(row, observed_at=stamp)], observed_at=now)
                    with mock.patch('auto_reply_knowledge_graph.time.time', return_value=now):
                        self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'snapshot')
            self.observation_cache(path, account, [dict(row, observed_at=now)], observed_at=now - 3600)
            with mock.patch('auto_reply_knowledge_graph.time.time', return_value=now):
                self.assertEqual(alden_corpus.room_displays(root, [42], account)['42']['label_source'], 'snapshot')

    def test_observation_name_basis_is_audit_metadata_not_a_title_promotion(self):
        with TemporaryDirectory() as td:
            root, account, path = self.fixture(Path(td))
            self.observation_cache(path, account, [
                {'chat_id': '42', 'label': '관측 별명', 'label_kind': 'display_name', 'source': 'local_chats',
                 'name_basis': 'NTUser.friendNickName', 'observed_at': time.time()},
            ])
            self.assertEqual(alden_corpus.room_displays(root, [42], account)['42'],
                             {'label': '관측 별명', 'label_source': 'observed_display'})

    def test_missing_titles_restore_exact_catalog_identity_and_remain_distinct(self):
        from unittest import mock
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td))
            with sqlite3.connect(path) as db:db.execute("UPDATE alden_rooms SET label=CASE chat_id WHEN '42' THEN '' ELSE char(8203) END")
            (root/'menubar-room-catalog.json').write_text(json.dumps({'schema_version':1,'rooms':[{'chat_id':'42','title':'ＡＩ　 연구방'}]}))
            graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                with mock.patch.object(KG,'_copy_consistent_sqlite_replica',wraps=KG._copy_consistent_sqlite_replica) as copy:
                    KG.index_chat_entities(graph,root)
                self.assertEqual(copy.call_count,1)
                rows=dict(graph.execute("SELECT entity_id,name FROM kg_entities WHERE category='대화방'"))
                self.assertEqual(rows[f'chat:kakao:{account}:room:42'],'AI 연구방')
                self.assertTrue(rows[f'chat:kakao:{account}:room:84'].startswith('같은 이름 대화 · #'))
                self.assertNotEqual(rows[f'chat:kakao:{account}:room:84'],rows[f'chat:kakao:{account}:room:42'])
                # Display aliases are grounded in the roster, not written back
                # into the raw room title or used to merge numeric identities.
                with sqlite3.connect(path) as raw:
                    self.assertEqual(raw.execute("SELECT label FROM alden_rooms WHERE chat_id='84'").fetchone()[0],chr(8203))
                self.assertEqual(alden_corpus.resolve_room(root,'카카오톡 ai연구방 내용')['chat_id'],'42')
                KG.index_chat_entities(graph,root)
                self.assertEqual(rows,dict(graph.execute("SELECT entity_id,name FROM kg_entities WHERE category='대화방'")))
            finally:graph.close()

    def test_unicode_variants_are_search_aliases_not_merged_identities(self):
        with TemporaryDirectory() as td:
            root,_,path=self.fixture(Path(td))
            with sqlite3.connect(path) as db:db.execute("UPDATE alden_rooms SET label=CASE chat_id WHEN '42' THEN 'ＡＩ　연구' ELSE 'AI연구' END")
            self.assertEqual(alden_corpus.resolve_room(root,'ai 연구 기록')['state'],'ambiguous')
            graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_chat_entities(graph,root)
                rooms=graph.execute("SELECT name FROM kg_entities WHERE category='대화방'").fetchall()
                self.assertEqual(len(set(rooms)),2);self.assertTrue(all(' · #' in r[0] for r in rooms))
                self.assertEqual(alden_corpus.resolve_room(root,rooms[0][0])['state'],'resolved')
            finally:graph.close()

    def test_people_samples_match_global_latest_ids_without_other_room_mix(self):
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td));graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_person_entities(graph,root)
                with sqlite3.connect(path) as source:
                    for actor in ('7','8'):
                        expected=source.execute('SELECT chat_id,log_id FROM alden_messages WHERE author_id=? ORDER BY id DESC LIMIT 3',(actor,)).fetchall()
                        evidence=json.loads(graph.execute('SELECT evidence_json FROM kg_entities WHERE entity_id=?',(f'person:kakao:{account}:actor:{actor}',)).fetchone()[0])
                        self.assertEqual(evidence['source_event_ids'],[f'kakao:{account}:room:{room}:log:{log}' for room,log in expected])
            finally:graph.close()

    def test_time_scope_canonicalizes_real_timestamps_and_keeps_source(self):
        with TemporaryDirectory() as td:
            root,_,path=self.fixture(Path(td));stamp=1758708000.25
            with sqlite3.connect(path) as db:db.execute('UPDATE alden_messages SET date=? WHERE id=1',(stamp,))
            day=KG._message_datetime_kst(stamp).date().isoformat()
            result=alden_corpus.search(root,'원문',chat_id='42',author_id='7',time_from=day+'T00:00:00+09:00',time_to=day+'T23:59:59+09:00')
            self.assertEqual(len(result['items']),1)
            self.assertEqual(result['items'][0]['source_date'],stamp)
            self.assertEqual(result['items'][0]['date'],KG._message_datetime_kst(stamp).isoformat())
            for start,end in [('invalid',None),('2026-10-02','2026-10-01')]:
                with self.assertRaisesRegex(ValueError,'corpus_time_scope_invalid'):alden_corpus.search(root,'원문',time_from=start,time_to=end)

    def test_new_publication_invalidates_a_recent_graph_cache(self):
        from unittest import mock
        import time
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td));graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            KG.ensure_seeded(graph);KG.write_meta(graph,'last_indexed_at',str(int(time.time())));graph.close()
            with mock.patch.object(KG,'refresh_dense_index'):
                result=KG.collect_knowledge_graph(root/'context.sqlite3',state_root=root,wait_for_reindex=True)
            self.assertFalse(result['stale']);self.assertEqual(result['reindex']['mode'],'inline')
            graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            self.assertEqual(KG.read_meta(graph,'source_corpus_snapshot'),'fixed');graph.close()

    def test_only_explicitly_published_account_path_is_selected(self):
        with TemporaryDirectory() as td:
            root,account,path=self.fixture(Path(td));self.assertEqual(KG._index_db_path(root),path)
            pointer=path.parent.parent/'current.json';pointer.write_text('{"schema_version":1,"account":"../escape"}')
            with self.assertRaises(RuntimeError):KG._index_db_path(root)

    def test_snapshot_candidates_keep_valid_provenance_and_numeric_room_scope(self):
        with TemporaryDirectory() as td:
            root,account,_=self.fixture(Path(td));graph=KG._connect_kg(root/KG.KNOWLEDGE_GRAPH_DB_NAME)
            try:
                KG.index_chat_entities(graph,root);KG.index_person_entities(graph,root);KG.index_membership_relations(graph,root)
                for identifier,evidence,updated in graph.execute("SELECT entity_id,evidence_json,updated_at FROM kg_entities"):
                    self.assertTrue(KG._candidate_provenance(identifier,evidence,updated)['provenance_valid'])
            finally:graph.close()
            from unittest import mock
            with mock.patch.object(KG,'_local_dense_embeddings',side_effect=RuntimeError('no model')):
                found=KG._query_knowledge_ranked('같은 이름',state_root=root,chat_id='42')
                forbidden=KG._query_knowledge_ranked('같은 이름',state_root=root,chat_id='999')
            self.assertTrue(f'person:kakao:{account}:actor:7' in found['candidates'])
            self.assertFalse(any(row.startswith(('person:kakao:','chat:kakao:')) for row in forbidden['candidates']))
            self.assertFalse(any('原文 행 84' in fact or '원문 행 84' in fact for fact in found['entity_facts']))
            self.assertTrue(all(':room:84:' not in eid for p in found['candidate_provenance'] for eid in p['source_event_ids']))

if __name__=='__main__':unittest.main()
