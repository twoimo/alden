"""Closed Korean compound suffix regressions; generated offline fixtures only.

Expected legacy prefixes are explicit examples, not a second query parser.
CollectionStore.search takes an FTS expression; retrieve and MCP take text.
"""
import json
from contextlib import closing
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
from alden_collection import (CollectionStore, identity, korean_literal_plan,
                              korean_literal_matches, korean_literal_sql)
import alden_collection_retrieval as retrieval
from alden_knowledge_mcp import KnowledgeServer


SUFFIXES = ('에서는', '에서도', '에게는', '에게도', '에서', '에게')


def expression(terms):
    return ' OR '.join('"' + term + '"' for term in terms)


class CompoundQueryTermsTests(unittest.TestCase):
    def assert_terms(self, haystacks, expected):
        self.assertEqual(kg._fts_query_terms(haystacks), expression(expected))

    def test_six_closed_suffixes_append_root_after_legacy_prefix(self):
        cases = (
            ('별무리에서는', ['별무리에서는', '별무리에서', '별무리']),
            ('별무리에서도', ['별무리에서도', '별무리에서', '별무리']),
            ('별무리에게는', ['별무리에게는', '별무리에게', '별무리']),
            ('별무리에게도', ['별무리에게도', '별무리에게', '별무리']),
            ('별무리에서', ['별무리에서', '별무리']),
            ('별무리에게', ['별무리에게', '별무리']),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                self.assert_terms([query], expected)

    def test_ai_and_mixed_script_roots_follow_the_same_closed_contract(self):
        for query, expected in (
            ('AI에서는', ['ai에서는', 'ai에서', 'ai']),
            ('AI에게도', ['ai에게도', 'ai에게', 'ai']),
            ('지식AI에서도', ['지식ai에서도', '지식ai에서', '지식ai']),
            ('AI지식에게', ['ai지식에게', 'ai지식']),
        ):
            with self.subTest(query=query):
                self.assert_terms([query], expected)

    def test_legacy_words_particles_numbers_and_negations_are_the_same_prefix(self):
        self.assert_terms(['별무리에서는 AI는 주식으로 코인도 2026 아니 않고'], [
            '별무리에서는', '별무리에서', 'ai는', 'ai', '주식으로', '주식',
            '코인도', '코인', '2026', '아니', '않고', '별무리',
        ])

    def test_new_roots_follow_all_haystacks_and_keep_first_seen_order(self):
        self.assert_terms(['AI에게는 별무리에서도', '미래에게 2026 AI에게도'], [
            'ai에게는', 'ai에게', '별무리에서도', '별무리에서', '미래에게',
            '2026', 'ai에게도', 'ai', '별무리', '미래',
        ])

    def test_existing_roots_and_repeated_variants_are_deduplicated(self):
        self.assert_terms(['별무리에서는 별무리 AI에게 ai', 'AI에게도 별무리에서도'], [
            '별무리에서는', '별무리에서', '별무리', 'ai에게', 'ai',
            'ai에게도', '별무리에서도',
        ])

    def test_nfc_nfd_casefold_and_compound_root_deduplication(self):
        query = '별무리에서는 AI에게도 지식AI에서'
        expected = ['별무리에서는', '별무리에서', 'ai에게도', 'ai에게',
                    '지식ai에서', '별무리', 'ai', '지식ai']
        self.assert_terms([unicodedata.normalize('NFD', query), query.casefold()], expected)
        self.assertEqual(kg._fts_query_terms([query]),
                         unicodedata.normalize('NFC', kg._fts_query_terms([query])))

    def test_short_roots_are_rejected_without_removing_legacy_alternatives(self):
        for query, expected in (
            ('차에서는', ['차에서는', '차에서']),
            ('차에서도', ['차에서도', '차에서']),
            ('차에게는', ['차에게는', '차에게']),
            ('차에게도', ['차에게도', '차에게']),
            ('차에서', ['차에서']),
            ('차에게', ['차에게']),
        ):
            with self.subTest(query=query):
                self.assert_terms([query], expected)

    def test_numeric_unicode_roots_and_negations_are_retained(self):
        self.assert_terms(['42에서 １２에게도 아니에게는 않고 0 1'], [
            '42에서', '１２에게도', '１２에게', '아니에게는', '아니에게',
            '않고', '42', '１２', '아니',
        ])

    def test_suffixes_outside_the_closed_set_keep_only_legacy_alternatives(self):
        self.assert_terms(['별무리한테는 별무리에게서 별무리에서만 별무리에서부터'], [
            '별무리한테는', '별무리한테', '별무리에게서', '별무리에서만',
            '별무리에서', '별무리에서부터',
        ])

    def test_one_closed_suffix_does_not_recursively_strip_another(self):
        self.assert_terms(['지식에게에서는'], ['지식에게에서는', '지식에게에서', '지식에게'])

    def test_exact_24_legacy_terms_are_unchanged(self):
        tail = ['sentinel%02d' % i for i in range(22)]
        self.assert_terms(['별무리에서는 ' + ' '.join(tail)],
                          ['별무리에서는', '별무리에서'] + tail)

    def test_over_capacity_variant_never_steals_a_later_legacy_term(self):
        tail = ['sentinel%02d' % i for i in range(25)]
        self.assert_terms(['별무리에서는', ' '.join(tail)],
                          ['별무리에서는', '별무리에서'] + tail[:22])

    def test_one_spare_slot_accepts_only_the_first_new_root(self):
        tail = ['sentinel%02d' % i for i in range(19)]
        self.assert_terms(['별무리에서는 AI에게도', ' '.join(tail)],
                          ['별무리에서는', '별무리에서', 'ai에게도', 'ai에게']
                          + tail + ['별무리'])

    def test_two_spare_slots_accept_two_roots_after_all_legacy_terms(self):
        tail = ['sentinel%02d' % i for i in range(18)]
        self.assert_terms(['별무리에서는 AI에게도 ' + ' '.join(tail)],
                          ['별무리에서는', '별무리에서', 'ai에게도', 'ai에게']
                          + tail + ['별무리', 'ai'])

    def test_deduplicated_new_root_does_not_waste_the_last_slot(self):
        tail = ['sentinel%02d' % i for i in range(18)]
        self.assert_terms(['별무리에서는 AI에게도 별무리', ' '.join(tail)],
                          ['별무리에서는', '별무리에서', 'ai에게도', 'ai에게', '별무리']
                          + tail + ['ai'])

    def test_fts_operators_quotes_and_wildcards_remain_literal(self):
        self.assert_terms(['AI에게"* OR NOT 2026 별무리에서는:NEAR(없음)'], [
            'ai에게', 'or', 'not', '2026', '별무리에서는', '별무리에서',
            'near', '없음', 'ai', '별무리',
        ])


class CompoundSearchPathTests(unittest.TestCase):
    PROJECT = 'compound-generated-fixture'
    OUTSIDE = 'compound-outside-fixture'
    # Labels and IDs intentionally contain none of the requested lexical roots.
    RECORDS = (
        ('r01', '별무리 fixture first'),
        ('r02', '별무리 fixture second'),
        ('r03', 'AI fixture third'),
        ('r04', '지식AI fixture fourth'),
        ('r05', '기록목록에서 fixture fifth'),
        ('r06', '기록목록에서는 fixture sixth'),
        ('r07', '차 fixture seventh'),
        ('r08', 'AIXylophone fixture eighth'),
        ('r09', '2026 fixture ninth'),
        ('r10', '아니 fixture tenth'),
        ('r11', '않고 fixture eleventh'),
    )

    def setUp(self):
        temporary = TemporaryDirectory(prefix='alden-compound-', dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.guards = []
        for obj, name in (
            (socket.socket, 'connect'), (socket.socket, 'connect_ex'),
            (socket, 'create_connection'), (urllib.request, 'urlopen'),
            (subprocess, 'Popen'), (kg, '_local_dense_embeddings'),
            (kg, '_local_dense_embeddings_unleased'), (kg, '_active_dense_embedding_model'),
            (kg, '_open_loopback_embedding_request'), (kg, 'mlx_model_request_lease'),
            (kg, 'refresh_dense_index'), (kg, '_start_background_reindex'),
            (kg, '_spawn_reindex_process'), (retrieval, 'index_text'),
            (retrieval, 'index_dense'), (retrieval, '_index_dense'),
            (retrieval, 'index_original_text'),
        ):
            guard = self.enterContext(patch.object(obj, name, side_effect=AssertionError('offline lookup only')))
            self.addCleanup(guard.assert_not_called)
            self.guards.append(guard)
        connect = sqlite3.connect

        def generated_connect(database, *args, **kwargs):
            path = os.fspath(database)
            if path != ':memory:':
                if path.startswith('file:'):
                    path = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
                if not Path(path).resolve().is_relative_to(self.root):
                    raise AssertionError('SQLite outside generated fixture: ' + path)
            return connect(database, *args, **kwargs)

        self.enterContext(patch.object(sqlite3, 'connect', side_effect=generated_connect))
        self.fts = sqlite3.connect(':memory:')
        self.addCleanup(self.fts.close)
        self.fts.execute("CREATE VIRTUAL TABLE kg_entities_fts USING fts5("
                         "entity_id UNINDEXED,name,aliases,description,facts,tokenize='unicode61')")
        self.store = CollectionStore(self.root)
        allowed = self.store.register(platform='graph', original_id='source-one', kind='source',
                                     label='generated source', projects=[self.PROJECT])
        self.store.ingest(allowed, [{'original_id': key, 'label': 'generated record', 'text': body}
                                   for key, body in self.RECORDS])
        for key, body in self.RECORDS:
            self.fts.execute('INSERT INTO kg_entities_fts VALUES(?,?,?,?,?)',
                             (identity('graph', key), '', '', body, ''))
        outside = self.store.register(platform='graph', original_id='source-two', kind='source',
                                      label='generated source', projects=[self.OUTSIDE])
        denied = self.store.register(platform='graph', original_id='source-three', kind='source',
                                     label='generated source', projects=[self.PROJECT])
        self.store.ingest(outside, [{'original_id': 'r90', 'label': 'generated record',
                                    'text': '별무리 AI 지식AI 숨은낱말 fixture outside'}])
        self.store.ingest(denied, [{'original_id': 'r91', 'label': 'generated record',
                                   'text': '별무리 AI 지식AI 차단낱말 fixture denied'}])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (denied,))
        # Finish fixture writes before the byte snapshot; read-only lookups must
        # not depend on incidental WAL/SHM checkpoint housekeeping.
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=DELETE').fetchone()[0], 'delete')
        self.store = CollectionStore.open_existing(self.root)
        self.server = KnowledgeServer(self.root, [self.PROJECT])
        response = self.server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {}}})
        self.assertNotIn('error', response)
        self.server.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.request_id = 1
        self.before = self.snapshot()

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def tearDown(self):
        self.assertEqual(self.before, self.snapshot(), 'lookup changed generated root bytes')
        self.assertFalse((self.store.root / 'retrieval.sqlite3').exists())
        self.assertFalse((self.root / kg.DENSE_INDEX_DB_NAME).exists())

    def ids(self, *keys):
        return {identity('graph', key) for key in keys}

    def search(self, surface, query):
        if surface == 'sqlite':
            rows = kg._bm25_candidates(self.fts, [query], limit=40)
            self.assertTrue(all(isinstance(score, float) for _, score in rows))
            result_ids = [key for key, _ in rows]
        elif surface == 'collection':
            rows = self.store.search(kg._fts_query_terms([query]), projects=[self.PROJECT], limit=40)
            self.assertTrue(all(row['label'] == 'generated record' for row in rows))
            result_ids = [row['id'] for row in rows]
        else:
            if surface == 'retrieve':
                result = retrieval.retrieve(self.root, query, projects=[self.PROJECT], max_entities=10)
            else:
                self.request_id += 1
                response = self.server.dispatch({'jsonrpc': '2.0', 'id': self.request_id,
                    'method': 'tools/call', 'params': {'name': 'alden_knowledge_search',
                        'arguments': {'projects': [self.PROJECT], 'query': query, 'limit': 10}}})
                self.assertNotIn('error', response)
                self.assertFalse(response['result']['isError'], response)
                result = json.loads(response['result']['content'][0]['text'])
            self.assertEqual(result['projects'], [self.PROJECT])
            self.assertIn(result['search_mode'], ('bm25_only', 'bm25_with_literals'))
            proofs = [p for p in result['fact_provenance'] if p['fact_type'] == 'entity']
            self.assertEqual(len(proofs), len(result['facts']))
            self.assertTrue(all(p['provenance_valid'] for p in proofs))
            result_ids = [p['entity_id'] for p in proofs]
        self.assertEqual(len(result_ids), len(set(result_ids)), 'lookup duplicated an identity')
        self.assertTrue(self.ids('r90', 'r91').isdisjoint(result_ids))
        return set(result_ids)

    def check_compound_path(self, surface):
        for root, base in (('별무리', self.ids('r01', 'r02')),
                           ('AI', self.ids('r03')), ('지식AI', self.ids('r04'))):
            for suffix in SUFFIXES:
                with self.subTest(surface=surface, root=root, suffix=suffix):
                    self.assertEqual(self.search(surface, root + suffix), base)

    def test_real_bm25_sqlite_finds_closed_suffix_body_terms(self):
        self.check_compound_path('sqlite')

    def test_collection_search_expression_finds_closed_suffix_body_terms(self):
        self.check_compound_path('collection')

    def test_retrieve_natural_query_finds_closed_suffix_body_terms(self):
        self.check_compound_path('retrieve')

    def test_mcp_natural_query_finds_closed_suffix_body_terms(self):
        self.check_compound_path('mcp')

    def test_nfd_nfc_and_casefold_reach_the_same_body_documents(self):
        for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
            for query, expected in (('별무리에게도', self.ids('r01', 'r02')),
                                    ('지식AI에서도', self.ids('r04'))):
                for form in ('NFC', 'NFD'):
                    with self.subTest(surface=surface, query=query, form=form):
                        self.assertEqual(self.search(surface, unicodedata.normalize(form, query)), expected)

    def test_scope_and_denial_exclude_body_matches_even_after_expansion(self):
        for surface in ('collection', 'retrieve', 'mcp'):
            for query in ('숨은낱말', '숨은낱말에서는', '차단낱말', '차단낱말에게도'):
                with self.subTest(surface=surface, query=query):
                    self.assertEqual(self.search(surface, query), set())
            for query, expected in (('별무리', self.ids('r01', 'r02')),
                                    ('AI', self.ids('r03'))):
                with self.subTest(surface=surface, query=query):
                    self.assertEqual(self.search(surface, query), expected)

    def test_original_partial_number_and_negation_terms_still_return_their_documents(self):
        for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
            with self.subTest(surface=surface):
                self.assertEqual(self.search(surface, '별무리에게도 기록목록에서는 2026 아니 않고'),
                                 self.ids('r01', 'r02', 'r05', 'r06', 'r09', 'r10', 'r11'))

    def test_sqlite_operators_and_wildcards_cannot_broaden_partial_roots(self):
        for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
            for query, expected in (('AIX에서*', set()),
                                    ('AI에게"*', self.ids('r03')),
                                    ('AI에게 NOT 2026', self.ids('r03', 'r09'))):
                with self.subTest(surface=surface, query=query):
                    self.assertEqual(self.search(surface, query), expected)

    def test_short_roots_do_not_retrieve_the_one_character_document(self):
        for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
            for suffix in SUFFIXES:
                with self.subTest(surface=surface, suffix=suffix):
                    self.assertEqual(self.search(surface, '차' + suffix), set())

    def test_full_term_capacity_does_not_retrieve_a_displacing_new_root(self):
        tail = ['t%02d' % i for i in range(22)]
        query = '별무리에서는 ' + ' '.join(tail)
        for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
            with self.subTest(surface=surface):
                self.assertEqual(self.search(surface, query), set())

    def test_graph_literal_filter_helpers_remain_literal(self):
        # Exercise the graph filter's real plan/matcher/SQL over generated rows.
        # Full graph_page SQL is outside this lexical regression's scope.
        self.fts.execute('CREATE TABLE literal_rows(key TEXT,label TEXT,body TEXT)')
        self.fts.executemany('INSERT INTO literal_rows VALUES(?,?,?)',
                            [(key, 'generated record', body) for key, body in self.RECORDS])
        for query, expected in (('기록목록에서는', self.ids('r06')),
                                ('별무리에서는', set()), ('AI에게', set())):
            with self.subTest(query=query):
                plan = korean_literal_plan(query)
                self.assertEqual(plan, ((query.casefold(),),))
                matches = {identity('graph', key) for key, body in self.RECORDS
                           if korean_literal_matches(plan, 'generated record', body)}
                sql, params = korean_literal_sql(plan, 'label', 'body')
                actual = {identity('graph', row[0]) for row in self.fts.execute(
                    'SELECT key FROM literal_rows WHERE ' + sql, params)}
                self.assertEqual(matches, expected)
                self.assertEqual(actual, expected)

    def test_retrieval_preserves_distinct_source_identities_with_the_same_root(self):
        for surface in ('sqlite', 'collection', 'retrieve', 'mcp'):
            with self.subTest(surface=surface):
                self.assertEqual(self.search(surface, '별무리'), self.ids('r01', 'r02'))


if __name__ == '__main__':
    unittest.main()
