"""Unicode lexical regressions over generated, private SQLite fixtures only."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unicodedata
import unittest
from unittest.mock import patch
import urllib.parse
import urllib.request

import auto_reply_knowledge_graph as kg
from alden_collection import CollectionStore, identity
from alden_collection_retrieval import retrieve
from alden_knowledge_mcp import KnowledgeServer


class UnicodeQueryTermsTests(unittest.TestCase):
    def test_ascii_and_hangul_characterization(self):
        cases = [
            ([], ''),
            (['A I 1 가 나'], ''),
            (['Alpha_BETA,foo-bar 42'], '"alpha" OR "beta" OR "foo" OR "bar" OR "42"'),
            (['AI는 주식으로 코인도 면접 준비'],
             '"ai는" OR "ai" OR "주식으로" OR "주식" OR "코인도" OR "코인" OR "면접" OR "준비"'),
            (['TEST test', '테스트는 테스트'], '"test" OR "테스트는" OR "테스트"'),
        ]
        for haystacks, expected in cases:
            with self.subTest(haystacks=haystacks):
                self.assertEqual(kg._fts_query_terms(haystacks), expected)

    def test_all_existing_particle_suffixes(self):
        for suffix in ('으로', '부터', '까지', '처럼', '보다', '은', '는', '이', '가',
                       '을', '를', '도', '에', '과', '와', '의', '로', '만'):
            with self.subTest(suffix=suffix):
                self.assertEqual(kg._fts_query_terms(['주식' + suffix]),
                                 '"주식' + suffix + '" OR "주식"')
        self.assertEqual(kg._fts_query_terms(['차는']), '"차는"')

    def test_deduplication_order_and_24_term_cap(self):
        words = ['word' + str(i) for i in range(30)]
        self.assertEqual(kg._fts_query_terms([' '.join(words), 'WORD0']),
                         ' OR '.join('"' + word + '"' for word in words[:24]))
        # Particle alternatives count toward the same cap as full words.
        self.assertEqual(len(kg._fts_query_terms([' '.join(words[:23]), '주식은']).split(' OR ')), 24)
        self.assertTrue(kg._fts_query_terms([' '.join(words[:23]), '주식은']).endswith('"주식은"'))

    def test_unicode_letters_numbers_and_continuing_marks(self):
        self.assertEqual(kg._fts_query_terms(['caféheadotter ΆΛΦΑ İSTANBUL 東京１２']),
                         '"caféheadotter" OR "άλφα" OR "i\u0307stanbul" OR "東京１２"')
        # Mn, Mc and Me continue words; unattached marks never start a term.
        self.assertEqual(kg._fts_query_terms(['\u0301aq\u0307cd \u093ek\u093ex n\u20ddz']),
                         '"aq\u0307cd" OR "k\u093ex" OR "n\u20ddz"')
        self.assertEqual(kg._fts_query_terms(['\u0301\u093e\u20dd a 7']), '')

    def test_nfc_nfd_casefold_and_canonical_deduplication(self):
        for word in ('caféheadotter', 'cafétailzebra', 'ΆΛΦΑ', 'ΐχνος', 'İZMİR', '면접'):
            expected = unicodedata.normalize('NFC', unicodedata.normalize('NFC', word).casefold())
            with self.subTest(word=word):
                terms = kg._fts_query_terms([unicodedata.normalize('NFD', word), word])
                self.assertEqual(terms, '"' + expected + '"')
                self.assertEqual(unicodedata.normalize('NFC', terms), terms)
        self.assertNotEqual(kg._fts_query_terms(['άλφα']), kg._fts_query_terms(['αλφα']))

    def test_punctuation_underscore_and_emoji_are_separators(self):
        self.assertEqual(kg._fts_query_terms(['café_άλφα—İZMİR😀東京/alpha\u200dbeta']),
                         '"café" OR "άλφα" OR "i\u0307zmi\u0307r" OR "東京" OR "alpha" OR "beta"')
        self.assertEqual(kg._fts_query_terms(['😀\ufe0f\u0301___"*:-()']), '')


class UnicodeSearchPathTests(unittest.TestCase):
    PROJECT = 'unicode-generated-fixture'
    WORDS = (
        ('accent-head', 'caféheadotter'),
        ('accent-tail', 'cafétailzebra'),
        ('greek', 'ΆΛΦΑΔΟΚΙΜΉ'),
        ('greek-fold', 'ΐχνος'),
        ('turkish', 'İSTANBUL'),
        ('turkish-fold', 'İZMİR'),
    )

    def setUp(self):
        temporary = TemporaryDirectory(prefix='alden-unicode-', dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.guards = []
        for obj, name in ((socket.socket, 'connect'), (socket, 'create_connection'),
                          (urllib.request, 'urlopen'), (subprocess, 'Popen'),
                          (kg, '_local_dense_embeddings'), (kg, '_active_dense_embedding_model'),
                          (kg, '_open_loopback_embedding_request')):
            guard = self.enterContext(patch.object(obj, name, side_effect=AssertionError('offline fixture only')))
            self.guards.append(guard)
        connect = sqlite3.connect

        def private_connect(database, *args, **kwargs):
            path = os.fspath(database)
            if path != ':memory:':
                if path.startswith('file:'):
                    path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
                if not Path(path).resolve().is_relative_to(self.root):
                    raise AssertionError('SQLite outside generated fixture: ' + path)
            return connect(database, *args, **kwargs)

        self.enterContext(patch.object(sqlite3, 'connect', side_effect=private_connect))
        self.fts = sqlite3.connect(':memory:')
        self.addCleanup(self.fts.close)
        self.fts.execute("CREATE VIRTUAL TABLE kg_entities_fts USING fts5("
                         "entity_id UNINDEXED,name,aliases,description,facts,tokenize='unicode61')")
        self.store = CollectionStore(self.root)
        target = self.store.register(platform='graph', original_id='unicode-source', kind='source',
                                     label='generated source', projects=[self.PROJECT], permission='local-private')
        self.store.ingest(target, [{'original_id': name, 'label': 'generated record',
                                   'text': ' '.join((word, 'fixture body'))} for name, word in self.WORDS])
        for name, word in self.WORDS:
            self.fts.execute('INSERT INTO kg_entities_fts VALUES(?,?,?,?,?)',
                             (identity('graph', name), '', '', word, ''))
        self.server = KnowledgeServer(self.root, [self.PROJECT])
        self.server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                              'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {}}})
        self.server.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.request_id = 1
        self.before = self.snapshot()

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and not p.name.endswith(('-wal', '-shm'))}

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()
        self.assertEqual(self.before, self.snapshot())
        self.assertFalse((self.store.root / 'retrieval.sqlite3').exists())

    def search(self, surface, query):
        if surface == 'sqlite':
            return [row[0] for row in kg._bm25_candidates(self.fts, [query])]
        if surface == 'collection':
            # CollectionStore.search accepts an FTS expression; retrieve/MCP accept text.
            return [row['id'] for row in self.store.search(kg._fts_query_terms([query]), projects=[self.PROJECT])]
        if surface == 'retrieve':
            result = retrieve(self.root, query, projects=[self.PROJECT], max_entities=10)
        else:
            self.request_id += 1
            response = self.server.dispatch({'jsonrpc': '2.0', 'id': self.request_id,
                'method': 'tools/call', 'params': {'name': 'alden_knowledge_search',
                    'arguments': {'projects': [self.PROJECT], 'query': query, 'limit': 10}}})
            self.assertNotIn('error', response)
            self.assertFalse(response['result']['isError'], response)
            result = json.loads(response['result']['content'][0]['text'])
        self.assertEqual(result['projects'], [self.PROJECT])
        self.assertEqual(result['search_mode'], 'bm25_only')
        proofs = [p for p in result['fact_provenance'] if p['fact_type'] == 'entity']
        self.assertTrue(all(p['provenance_valid'] for p in proofs))
        self.assertEqual(len(proofs), len(result['facts']))
        return [p['entity_id'] for p in proofs]

    def test_accented_head_and_tail_nfc_nfd_across_real_search_paths(self):
        for name, word in self.WORDS[:2]:
            for form in ('NFC', 'NFD'):
                for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
                    with self.subTest(name=name, form=form, surface=surface):
                        self.assertEqual(self.search(surface, unicodedata.normalize(form, word)),
                                         [identity('graph', name)])

    def test_greek_and_turkish_casefold_marks_across_real_search_paths(self):
        for name, word in self.WORDS[2:]:
            for form in ('NFC', 'NFD'):
                for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
                    with self.subTest(name=name, form=form, surface=surface):
                        self.assertEqual(self.search(surface, unicodedata.normalize(form, word)),
                                         [identity('graph', name)])

    def test_sqlite_matches_nfd_and_nfc_accented_source(self):
        self.fts.execute('DELETE FROM kg_entities_fts')
        for name, word in self.WORDS[:2]:
            for source_form in ('NFC', 'NFD'):
                self.fts.execute('INSERT INTO kg_entities_fts VALUES(?,?,?,?,?)',
                                 (name + source_form, '', '', unicodedata.normalize(source_form, word), ''))
            for query_form in ('NFC', 'NFD'):
                with self.subTest(name=name, query_form=query_form):
                    self.assertEqual(set(self.search('sqlite', unicodedata.normalize(query_form, word))),
                                     {name + 'NFC', name + 'NFD'})

    def test_fts_syntax_is_literal_and_unknown_queries_stay_empty(self):
        for query in ('absent"*', 'absent NEAR(absent)', 'absent OR absent',
                      'absent"; DROP TABLE versions; --', 'άλφα*', 'absent:absent'):
            with self.subTest(query=query):
                expression = kg._fts_query_terms([query])
                self.assertTrue(all(term.startswith('"') and term.endswith('"')
                                    for term in expression.split(' OR ')))
                for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
                    self.assertEqual(self.search(surface, query), [])
        # A trailing wildcard is discarded; it cannot broaden a partial word.
        self.assertEqual(self.search('sqlite', 'caféhead*'), [])
        self.assertEqual(self.search('sqlite', 'caféheadotter"*'), [identity('graph', 'accent-head')])

    def test_empty_term_queries_stay_empty_in_text_apis(self):
        for query in ('😀_"*', 'a I 가 1'):
            for surface in ('sqlite', 'retrieve', 'mcp'):
                with self.subTest(query=query, surface=surface):
                    self.assertEqual(self.search(surface, query), [])


if __name__ == '__main__':
    unittest.main()
