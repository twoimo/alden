"""Repeated retained text must not erase distinct, currently permitted sources."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, identity
import alden_collection_retrieval as retrieval
import alden_chat_turns as turns
import alden_history as history
from alden_entity_fixture import original_digest


class CollectionContextDuplicatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='graph', original_id='source',
            kind='source', label='source', projects=['career'])
        self.body = '청라 채용: IT기획 5명(예상). 원문의 주장으로 보존한다.'

    def add(self, name, *, body=None, raw_changes=None, metadata=None, target=None):
        text = self.body if body is None else body
        raw = {'id': name, 'code': name, 'url': 'https://example.invalid/post/' + name,
               'platform': 'threads', 'type': 'source', 'author': 'declared-author',
               'handle': '@declared-author', 'date': '2026-02-17',
               'localOriginalText': text, 'localTextOrigin': 'retained-fixture'}
        raw.update(raw_changes or {})
        row = {'original_id': name, 'label': '청라 채용', 'text': text,
               'source_node_id': name, 'raw': raw, **(metadata or {})}
        self.store.ingest(target or self.target, [row])
        return identity('graph', name)

    def get(self, **kwargs):
        return retrieval.retrieve(self.root, '청라 채용', projects=['career'],
                                  max_relations=0, **kwargs)

    def test_exact_context_is_once_with_each_original_proof_and_unchanged_store(self):
        docs = {self.add('a'), self.add('b')}
        before = original_digest(self.store)
        result = self.get()
        self.assertEqual(len(result['facts']), 1)
        proof = result['fact_provenance'][0]
        matching = proof['matching_sources']
        self.assertEqual(matching['schema'], 1)
        self.assertEqual(matching['scope'], 'context_text_only; originals_not_merged')
        sources = [proof, *matching['sources']]
        self.assertEqual({s['entity_id'] for s in sources}, docs)
        self.assertEqual(len({s['note_hash'] for s in sources}), 2)
        for source in sources:
            self.assertTrue(source['provenance_valid'])
            self.assertEqual(source['source_memberships'][0]['source_version'], source['source_version'])
            self.assertEqual(source['truth_status'], 'source_record; not independently verified')
        self.assertEqual(result['context_text_compaction']['repeated_facts_removed'], 1)
        self.assertEqual(result['context_text_compaction']['retained_sources'], 2)
        self.assertEqual(result['context_text_compaction']['saved_characters'], len(result['facts'][0]))
        self.assertEqual(len(result['candidates']), 2)
        self.assertEqual(original_digest(self.store), before)

    def test_no_group_for_changed_author_date_media_or_other_raw_semantics(self):
        changes = [{'author': 'different'}, {'date': '2026-02-18'},
                   {'remoteMediaUrls': ['https://example.invalid/image.png']},
                   {'claim_status': 'retracted'}, {'localOriginalText': self.body + '\n'},
                   {'handle': ''}, {'truncated': True}]
        for i, change in enumerate(changes):
            with self.subTest(change=change):
                with TemporaryDirectory() as directory:
                    self.root = Path(directory).resolve(); self.store = CollectionStore(self.root)
                    self.target = self.store.register(platform='graph', original_id='source',
                        kind='source', label='source', projects=['career'])
                    self.add('base-' + str(i)); self.add('changed-' + str(i), raw_changes=change)
                    result = self.get(max_entities=2)
                    self.assertEqual(len(result['facts']), 2)
                    self.assertTrue(all('matching_sources' not in p for p in result['fact_provenance']))

    def test_semantic_metadata_and_permission_contexts_remain_distinct(self):
        a = self.add('a'); b = self.add('b', metadata={'valid_from': '2026-02-18'})
        result = self.get(max_entities=2)
        self.assertEqual(len(result['facts']), 2)
        with self.store.database() as db:
            db.execute('DELETE FROM memberships WHERE document_id=?', (b,))
        other = self.store.register(platform='graph', original_id='other-target', kind='source',
                                    label='other', projects=['career'])
        self.add('b', target=other)
        result = self.get(max_entities=2)
        self.assertEqual(len(result['facts']), 2)
        self.assertEqual({p['entity_id'] for p in result['fact_provenance']}, {a, b})

    def test_explicit_source_pins_and_time_filters_disable_compaction(self):
        a = self.add('a'); b = self.add('b')
        for kwargs in ({'also': [a]}, {'time_from': 0}, {'time_to': 9999999999}):
            with self.subTest(kwargs=kwargs):
                result = self.get(**kwargs)
                self.assertEqual(len(result['facts']), 2)
                self.assertEqual({p['entity_id'] for p in result['fact_provenance']}, {a, b})
        latest = retrieval.retrieve(self.root, '청라 채용 최신', projects=['career'], max_relations=0)
        self.assertTrue(all('matching_sources' not in p for p in latest['fact_provenance']))

    def test_retained_text_spacing_and_partial_context_are_not_equivalent(self):
        self.add('a'); self.add('b', body=self.body.replace('청라 채용', '청라  채용'))
        self.assertEqual(len(self.get()['facts']), 2)
        with self.store.database() as db:
            db.execute('DELETE FROM memberships')
        long = self.body * 20
        self.add('long-a', body=long); self.add('long-b', body=long)
        result = self.get(max_context_chars=256)
        self.assertTrue(result['fact_provenance'][0]['fact_truncated'])
        self.assertNotIn('matching_sources', result['fact_provenance'][0])

    def test_verification_failure_of_matching_copy_is_not_silently_discarded(self):
        self.add('a'); self.add('b')
        original = retrieval._verified_source; checked = []
        def verify(db, store, row, projects):
            if checked:
                raise RuntimeError('collection_graph_version_changed')
            checked.append(row['id'])
            return original(db, store, row, projects)
        with patch.object(retrieval, '_verified_source', side_effect=verify):
            with self.assertRaisesRegex(RuntimeError, 'collection_graph_version_changed'):
                self.get()

    def test_cancelled_copy_check_never_returns_partial_group(self):
        self.add('a'); self.add('b')
        original = retrieval._verified_source; stopped = [False]
        def verify(*args):
            value = original(*args); stopped[0] = True
            return value
        with patch.object(retrieval, '_verified_source', side_effect=verify):
            with self.assertRaisesRegex(RuntimeError, 'collection_retrieval_cancelled'):
                self.get(cancelled=lambda: stopped[0])

    def test_chat_history_preserves_collection_version_scope_and_matching_sources(self):
        self.add('a'); self.add('b')
        result = self.get()
        proof = copy.deepcopy(result['fact_provenance'][0]); proof['credential'] = 'DO_NOT_STORE'
        # Also cover the pre-existing bug where a single collection proof lost
        # its version, original ID and permission scope when saved as a chat.
        ticket = turns.reserve_turn(self.root, 'a' * 32, None, '청라 채용 출처')
        turns.complete_turn(self.root, ticket, '보존된 원문의 채용 주장입니다.', provenance=[proof])
        page = history.read(self.root, Path('/unused'), 'voice-history-messages', chat_id=ticket['conversation_id'])
        saved = page['items'][1]['retrieval_provenance'][0]
        for key in ('source_version', 'original_id', 'source_memberships', 'scope_basis',
                    'content_evidence', 'truth_status', 'body_source', 'source_author', 'matching_sources'):
            self.assertEqual(saved[key], proof[key])
        self.assertNotIn('credential', saved)

    def test_single_collection_source_version_and_permission_survive_typed_history(self):
        self.add('a'); proof = self.get()['fact_provenance'][0]
        proof.update(observation_time='2026-02-17', observation_basis='source_published_at')
        ticket = turns.reserve_turn(self.root, 'b' * 32, None, '청라 채용')
        turns.complete_turn(self.root, ticket, '보존된 주장입니다.', provenance=[proof])
        page = history.read(self.root, Path('/unused'), 'voice-history-messages', chat_id=ticket['conversation_id'])
        saved = page['items'][1]['retrieval_provenance'][0]
        self.assertEqual(saved['source_version'], proof['source_version'])
        self.assertEqual(saved['source_memberships'], proof['source_memberships'])
        self.assertEqual(saved['observation_time'], '2026-02-17')
        self.assertEqual(saved['observation_basis'], 'source_published_at')

    def test_matching_proof_shape_is_bounded_and_does_not_save_unknown_fields(self):
        self.add('a'); self.add('b')
        proof = self.get()['fact_provenance'][0]
        self.assertIn('matching_sources', proof)
        source = proof['matching_sources']['sources'][0]
        source['credential'] = 'DO_NOT_STORE'
        saved = json.loads(turns._provenance([proof]))[0]
        self.assertNotIn('credential', saved['matching_sources']['sources'][0])
        mutations = [lambda p: p['matching_sources'].update(schema=True),
                     lambda p: p['matching_sources'].update(sources=[source] * 10),
                     lambda p: p['matching_sources'].update(sources=[source, source]),
                     lambda p: p.update(entity_id={}),
                     lambda p: p['matching_sources']['sources'][0].update(matching_sources={})]
        for change in mutations:
            value = copy.deepcopy(proof); change(value)
            with self.assertRaisesRegex(ValueError, 'turn_provenance_invalid'):
                turns._provenance([value])

    def test_matching_declared_partial_sources_and_missing_author_date_stay_separate(self):
        for change in ({'truncated': True}, {'date': ''}, {'author': ''}, {'type': 'metadata'}):
            with self.subTest(change=change), TemporaryDirectory() as directory:
                self.root = Path(directory).resolve(); self.store = CollectionStore(self.root)
                self.target = self.store.register(platform='graph', original_id='source',
                    kind='source', label='source', projects=['career'])
                self.add('a', raw_changes=change); self.add('b', raw_changes=change)
                self.assertEqual(len(self.get()['facts']), 2)


if __name__ == '__main__':
    unittest.main()
