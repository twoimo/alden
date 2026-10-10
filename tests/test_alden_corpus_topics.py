"""Retired topic promotion must leave Raw evidence and retrieval untouched."""
import runpy
import sqlite3
import sys
import unittest
from contextlib import ExitStack, closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from scripts import alden_corpus, alden_corpus_topics as topics


class UntouchedStore:
    def __getattribute__(self, name):
        raise AssertionError(f'Topic indexing accessed a store: {name}')


class RawTopicTests(unittest.TestCase):
    def assert_disabled(self, result):
        self.assertEqual(result, {'topics': 0, 'written': 0, 'with_samples': 0})

    def test_existing_index_calls_do_not_access_stores(self):
        graph, source = UntouchedStore(), UntouchedStore()
        self.assert_disabled(topics.index(graph, source))
        for chat in ('', 'room1', 'AI 주식 코인'):
            for minimum in (-1, 0, 1, 20, 1000):
                with self.subTest(chat=chat, minimum=minimum):
                    self.assert_disabled(topics.index(
                        graph=graph, source=source, chat=chat, minimum=minimum))

    def test_import_does_not_depend_on_reference_classifier_or_lexicon(self):
        with mock.patch.dict(sys.modules, {'auto_reply_reference_store': None}):
            module = runpy.run_path(topics.__file__)
        self.assert_disabled(module['index'](UntouchedStore(), UntouchedStore()))

    def source(self, path):
        account = '1' * 64
        keywords = (
            'AI ＡＩ 인공지능 머신러닝 딥러닝 주식 코인 비트코인 투자 부동산 경매 '
            '연락처 손쉬운 사용 컴퓨터 도구 카톡 사업 리눅스 긱뉴스 나임'
        )
        rows = []
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''
                CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE alden_messages(
                    id INTEGER PRIMARY KEY, chat TEXT, chat_id TEXT, log_id TEXT,
                    message TEXT, author_id TEXT, user_name TEXT, date TEXT,
                    is_self INTEGER, message_type INTEGER);
                CREATE VIEW context_messages AS SELECT * FROM alden_messages;
                CREATE VIRTUAL TABLE context_messages_fts USING fts5(
                    message, user_name, chat,
                    content='alden_messages', content_rowid='id');
            ''')
            db.execute('INSERT INTO corpus_meta VALUES(?,?)', ('account', account))
            for room in ('42', '43'):
                for i in range(60):
                    row_id = len(rows) + 1
                    # Exercise incoming, outgoing and system roles; keep large
                    # IDs and full-width text exact, including whitespace.
                    author, is_self, kind = (
                        ('7', 0, 1), ('8', 1, 1), ('0', 0, 0))[i % 3]
                    rows.append((
                        row_id, f'kakao:{account}:room:{room}', room,
                        str(9007199254740997 + row_id),
                        f'  원문\n{keywords}\t<{row_id}>  ', author, '같은 이름',
                        '2026-10-02T12:00:00+09:00', is_self, kind))
            db.executemany('INSERT INTO alden_messages VALUES(?,?,?,?,?,?,?,?,?,?)', rows)
            db.execute("INSERT INTO context_messages_fts(context_messages_fts) VALUES('rebuild')")
        return account, rows

    def test_keyword_corpus_creates_no_topics_and_preserves_raw_retrieval(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'context.sqlite3'
            account, rows = self.source(path)
            before = path.read_bytes()
            # Use production search with only its graph-owned path/date helpers
            # replaced. No live corpus, vault, graph, model or config is loaded.
            dependencies = SimpleNamespace(
                _index_db_path=lambda state_root: path,
                _message_datetime_kst=datetime.fromisoformat,
            )
            with ExitStack() as connections:
                # Own every fixture reader even while the production search
                # uses SQLite's transaction-only context manager.
                fixture_sqlite = SimpleNamespace(connect=lambda *args, **kwargs:
                    connections.enter_context(closing(sqlite3.connect(*args, **kwargs))))
                connections.enter_context(mock.patch.object(alden_corpus, 'sqlite3', fixture_sqlite))
                connections.enter_context(mock.patch.dict(sys.modules, {'auto_reply_knowledge_graph': dependencies}))
                retrieved_before = alden_corpus.search(root, '원문', limit=20)
                with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(':memory:')) as graph:
                    graph.executescript('''
                        CREATE TABLE kg_entities(entity_id TEXT PRIMARY KEY);
                        CREATE TABLE kg_relations(source_id TEXT, relation TEXT, target_id TEXT);
                        CREATE TABLE kg_meta(key TEXT PRIMARY KEY, value TEXT);
                    ''')
                    accesses = []

                    def deny_access(*action):
                        accesses.append(action)
                        return sqlite3.SQLITE_DENY

                    source.set_authorizer(deny_access)
                    graph.set_authorizer(deny_access)
                    try:
                        for minimum in (0, 1, 20):
                            for chat in ('', f'kakao:{account}:room:42'):
                                self.assert_disabled(topics.index(
                                    graph, source, chat=chat, minimum=minimum))
                        self.assertEqual(accesses, [])
                        self.assertEqual(source.total_changes, 0)
                        self.assertEqual(graph.total_changes, 0)
                    finally:
                        source.set_authorizer(None)
                        graph.set_authorizer(None)
                    self.assertEqual(source.execute(
                        'SELECT * FROM alden_messages ORDER BY id').fetchall(), rows)
                    for table in ('kg_entities', 'kg_relations', 'kg_meta'):
                        self.assertEqual(graph.execute(f'SELECT * FROM {table}').fetchall(), [])
                retrieved_after = alden_corpus.search(root, '원문', limit=20)
                self.assertEqual(retrieved_after, retrieved_before)
                self.assertTrue(retrieved_after['ok'])
                self.assertEqual(len(retrieved_after['items']), 20)
                originals = {row[3]: row for row in rows}
                roles = set()
                for item in retrieved_after['items']:
                    row = originals[item['log_id']]
                    self.assertEqual(item['source_id'], f'kakao:{account}:room:{row[2]}:log:{row[3]}')
                    self.assertEqual(item['content'], row[4])
                    self.assertEqual(item['chat_id'], row[2])
                    self.assertEqual(item['author_id'], row[5])
                    self.assertEqual(item['sender'], row[6])
                    self.assertEqual(item['message_type'], row[9])
                    expected_role = ('outgoing_unclassified' if row[8] else
                                     'peer_history' if int(row[5]) > 0 else 'system_history')
                    self.assertEqual(item['source_role'], expected_role)
                    roles.add(item['source_role'])
                self.assertEqual(roles, {'peer_history', 'outgoing_unclassified', 'system_history'})
                scoped = alden_corpus.search(root, 'AI', chat_id='42', author_id='8')
                self.assertTrue(scoped['items'])
                self.assertTrue(all(item['chat_id'] == '42' and item['author_id'] == '8'
                                    for item in scoped['items']))
            self.assertEqual(path.read_bytes(), before)

    def test_index_leaves_caller_transaction_and_existing_graph_untouched(self):
        with closing(sqlite3.connect(':memory:')) as graph:
            graph.execute('CREATE TABLE kg_meta(key TEXT PRIMARY KEY, value TEXT)')
            graph.execute('INSERT INTO kg_meta VALUES(?,?)', ('caller_work', 'pending'))
            before = graph.total_changes
            self.assertTrue(graph.in_transaction)
            self.assert_disabled(topics.index(graph, UntouchedStore()))
            self.assertTrue(graph.in_transaction)
            self.assertEqual(graph.total_changes, before)
            self.assertEqual(graph.execute('SELECT * FROM kg_meta').fetchall(),
                             [('caller_work', 'pending')])
            graph.rollback()
            self.assertEqual(graph.execute('SELECT * FROM kg_meta').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
