"""Current numeric/short conversation evidence at real retrieval/MCP boundaries."""
import copy
import hashlib
import json
import socket
import unittest
from unittest.mock import patch
from tests import test_alden_messenger_context as context_tests
from tests.test_alden_messenger_frontier import item, journal_rows
from alden_collection import identity
from alden_collection_retrieval import retrieve
from alden_knowledge_mcp import KnowledgeServer
import auto_reply_knowledge_graph as kg

candidate = context_tests.candidate


class MessengerRagContextTests(unittest.TestCase):
    def setUp(self):
        self.case = context_tests.MessengerContextTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.root, self.engine, self.reader = self.case.root, self.case.engine, self.case.reader
        self.projects = [candidate.PROJECT]
        self.authority = candidate.MessengerAuthority([self.engine])
        self.guards = {'source_access': self.authority.access, 'source_revision': self.authority.revision}
        self.embed = self.enterContext(patch.object(kg, '_local_dense_embeddings', side_effect=AssertionError('unexpected embedding')))
        self.network = self.enterContext(patch.object(socket.socket, 'connect', side_effect=AssertionError('unexpected network')))

    def tearDown(self):
        self.embed.assert_not_called(); self.network.assert_not_called()

    def lookup(self, **options):
        options.setdefault('source_context', self.authority.retrieval_context)
        return retrieve(self.root, '회의 일정', projects=self.projects, **self.guards, **options)

    def excerpts(self, result):
        return result['conversation_context']['items']

    def bodies(self, result):
        return [item['body'] for window in self.excerpts(result) for item in window['items']]

    def test_numeric_reply_is_source_context_and_does_not_create_a_fact_or_node(self):
        events = journal_rows(self.engine.store)
        with self.engine.store.database() as db:
            versions = db.execute('SELECT count(*) FROM versions').fetchone()[0]
        result = self.lookup()
        self.assertIn('3시요', self.bodies(result))
        self.assertTrue(all('3시요' not in fact for fact in result['facts']))
        self.assertEqual([p['fact_type'] for p in result['fact_provenance']], ['entity'])
        self.assertIn('not facts/replies/agreements', result['conversation_context']['scope'])
        self.assertNotIn('account_id', self.excerpts(result)[0])
        self.assertEqual(journal_rows(self.engine.store), events)
        with self.engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], versions)
        self.assertEqual(len(self.engine.store.graph(projects=self.projects)['nodes']), 1)

    def test_default_retrieval_does_not_adopt_native_context_or_read_more_source_bodies(self):
        with patch.object(self.reader, 'verify_prepared', wraps=self.reader.verify_prepared) as verify:
            result = self.lookup(source_context=None)
            verify.assert_not_called()
        self.assertNotIn('conversation_context', result)
        self.assertTrue(result['facts'])

    def test_context_callback_requires_explicit_source_authority(self):
        with self.assertRaisesRegex(ValueError, 'authority_required'):
            retrieve(self.root, '회의', projects=self.projects, source_context=self.authority.retrieval_context)
        with self.assertRaisesRegex(ValueError, 'authority_required'):
            KnowledgeServer(self.root, self.projects, source_context=self.authority.retrieval_context)

    def test_context_from_another_root_cannot_reuse_the_same_account_document_identity(self):
        other_root = candidate.create_root(self.root.parent / 'other-private-root')
        other_reader = context_tests.Reader({('a', None): ([item(2, '7시요'), self.case.values[1]], False, None)})
        other = candidate.Frontier(other_root, 'kakaotalk', 'owner', other_reader)
        other.seed({'account_id':'owner','complete':True,'rooms':[{'id':'a','title':'other scope'}]})
        other.step()
        other_authority = candidate.MessengerAuthority([other])
        with self.assertRaisesRegex(ValueError, 'root_mismatch'):
            self.lookup(source_context=other_authority.retrieval_context)

    def test_serialized_mcp_search_includes_the_same_context_and_rejects_json_authority(self):
        server = KnowledgeServer(self.root, self.projects, **self.guards, source_context=self.authority.retrieval_context)
        server.dispatch({'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{}}})
        server.dispatch({'jsonrpc':'2.0','method':'notifications/initialized'})
        response = server.dispatch({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{
            'name':'alden_knowledge_search','arguments':{'projects':self.projects,'query':'회의 일정'}}})
        self.assertFalse(response['result']['isError'], response)
        result = json.loads(response['result']['content'][0]['text'])
        self.assertIn('3시요', self.bodies(result))
        self.assertFalse(server.valid_call({'name':'alden_knowledge_search','arguments':{
            'projects':self.projects,'query':'회의','source_context':True}}))

    def test_current_edit_blocks_stale_context_and_primary_fact_until_reconciliation(self):
        self.reader.pages[('a', None)] = ([item(2, '4시요'), self.case.values[1]], False, None)
        result = self.lookup()
        self.assertEqual(result['facts'], [])
        self.assertEqual(self.excerpts(result), [])
        self.assertEqual(result['conversation_context']['withheld_entities'], 1)
        self.engine.step(mode='reconcile')
        result = self.lookup()
        self.assertIn('4시요', self.bodies(result))
        self.assertNotIn('3시요', json.dumps(result, ensure_ascii=False))

    def test_expired_authority_is_withheld_before_source_body_revalidation(self):
        self.reader.blocked = True
        with patch.object(self.reader, 'verify_prepared', wraps=self.reader.verify_prepared) as verify:
            result = self.lookup()
            verify.assert_not_called()
        self.assertEqual(result['facts'], [])
        self.assertEqual(self.excerpts(result), [])

    def test_explicit_hidden_neighbor_is_an_observable_gap_without_its_old_body(self):
        self.reader.pages[('a', None)] = ([item(2, visible=False), self.case.values[1]], False, None)
        self.engine.step(mode='reconcile')
        result = self.lookup()
        self.assertTrue(result['facts'])
        self.assertEqual(self.bodies(result)[1], None)
        self.assertNotIn('3시요', json.dumps(result, ensure_ascii=False))

    def test_changed_account_or_project_permission_cannot_publish_context(self):
        self.reader.account = 'other'
        self.assertEqual(self.lookup()['facts'], [])
        self.reader.account = 'owner'
        with self.engine.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied'")
        self.assertEqual(self.lookup()['facts'], [])

    def test_revocation_during_context_read_prevents_returning_the_selected_body(self):
        def revoke(payload):
            with self.engine.store.database() as db:
                db.execute("UPDATE target_projects SET permission='denied'")
        with patch.object(self.reader, 'verify_prepared', side_effect=revoke):
            with self.assertRaisesRegex(RuntimeError, 'authorization_changed'):
                self.lookup()

    def test_permission_revoked_after_context_return_invalidates_the_selected_response(self):
        def context(proof, **options):
            value = self.authority.retrieval_context(proof, **options)
            with self.engine.store.database() as db:
                db.execute("UPDATE target_projects SET permission='denied'")
            return value
        with self.assertRaisesRegex(RuntimeError, 'authorization_changed'):
            self.lookup(source_context=context)

    def test_neighbor_changes_after_context_read_cannot_publish_the_old_numeric_answer(self):
        def context(proof, **options):
            value = self.authority.retrieval_context(proof, **options)
            if 'expected_window' not in options:
                self.reader.pages[('a', None)] = ([item(2, '4시요'), self.case.values[1]], False, None)
                self.engine.step(mode='reconcile')
            return value
        with self.assertRaisesRegex(RuntimeError, 'context_changed'):
            self.lookup(source_context=context)

    def test_other_room_and_distant_context_do_not_join_the_selected_excerpt(self):
        self.reader.pages[('a', None)] = ([item(10000, '이전 방의 멀리 떨어진 확인 메시지'), *self.case.values], False, None)
        self.engine.step(mode='reconcile')
        self.engine.seed({'account_id':'owner','complete':True,'rooms':[{'id':'b','title':'other'}]})
        self.reader.pages[('b', None)] = ([item(3, '다른 방의 비공개 짧은 답변')], False, None)
        self.engine.step()
        result = self.lookup()
        self.assertEqual(self.bodies(result), [self.case.values[1]['body'], '3시요'])

    def test_context_respects_shared_character_budget_without_partial_source_claims(self):
        result = self.lookup(max_context_chars=256)
        self.assertTrue(result['facts'])
        self.assertEqual(self.excerpts(result), [])
        self.assertEqual(result['conversation_context']['omitted_for_budget'], 1)
        self.assertLessEqual(result['context_chars'], 256)

    def test_context_window_and_anchor_bindings_reject_malformed_callback_output(self):
        def changed(mutator):
            def callback(proof, **options):
                value = self.authority.retrieval_context(proof, **options)
                mutator(value)
                return value
            return callback
        mutations = [lambda v:v.update(source_targets=['foreign']),
            lambda v:v['window'].update(source_version='other'),
            lambda v:v['window']['items'][0].update(body='위조된 원문'),
            lambda v:v['window']['items'].append(copy.deepcopy(v['window']['items'][0])),
            lambda v:v['window'].update(account_id='unrequested-account')]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, 'collection_source_context_'):
                self.lookup(source_context=changed(mutate))

    def test_cancelled_context_does_not_escape_as_a_successful_search(self):
        cancel = False
        def context(proof, **options):
            nonlocal cancel
            value = self.authority.retrieval_context(proof, **options)
            cancel = True
            return value
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.lookup(source_context=context, cancelled=lambda: cancel)

    def test_current_duplicate_origin_survives_without_exposing_blocked_room_context(self):
        self.engine.seed({'account_id':'owner','complete':True,'rooms':[{'id':'b','title':'duplicate'}]})
        self.reader.pages[('b', None)] = ([item(4, '5시요'), item(3, self.case.values[1]['body'])], False, None)
        self.engine.step()
        authorize = self.reader.authorize
        def scoped(room):
            if room == 'a':
                raise candidate.SourceBlocked('room_denied')
            return authorize(room)
        with patch.object(self.reader, 'authorize', side_effect=scoped):
            result = self.lookup()
        self.assertEqual(self.excerpts(result)[0]['room_id'], 'b')
        self.assertIn('5시요', self.bodies(result))
        self.assertNotIn('3시요', json.dumps(result, ensure_ascii=False))

    def test_live_context_is_bounded_per_search_and_reports_unchecked_anchors(self):
        self.reader.pages[('a', None)] = ([item(5, '회의 일정과 배송 계약의 확인 방법을 다음 주에 공유합니다.'),
            item(4, '회의 일정 변경 공지는 내일 오전에 전달하고 운영 정책에도 반영합니다.'), *self.case.values], False, None)
        self.engine.step(mode='reconcile')
        with patch.object(self.reader, 'verify_prepared', wraps=self.reader.verify_prepared) as observed:
            result = self.lookup(max_entities=3)
        self.assertEqual(observed.call_count, 2)
        self.assertEqual(result['conversation_context']['checked_entities'], 2)
        self.assertEqual(result['conversation_context']['withheld_entities'], 1)
        self.assertEqual(len(result['facts']), 2)
        self.assertTrue(all(len(w['items']) <= 3 for w in self.excerpts(result)))

    def test_new_context_does_not_turn_a_review_message_into_a_graph_fact(self):
        promotion = '원금 보장 지금 입장하세요.'
        self.reader.pages[('a', None)] = ([item(3, promotion), *self.case.values], False, None)
        self.engine.step(mode='reconcile')
        result = self.lookup()
        self.assertTrue(all(promotion not in fact for fact in result['facts']))
        self.assertEqual(len(self.engine.store.graph(projects=self.projects)['nodes']), 1)

    def test_permission_revision_changes_when_grants_change_but_not_on_collection_pause(self):
        before = self.authority.revision(self.engine.target)
        with self.engine.store.database() as db:
            db.execute('UPDATE targets SET enabled=0')
        self.assertEqual(self.authority.revision(self.engine.target), before)
        with self.engine.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied'")
        self.assertNotEqual(self.authority.revision(self.engine.target), before)

    def test_mutating_callback_input_cannot_rebind_the_selected_entity_version(self):
        def context(proof, **options):
            value = self.authority.retrieval_context(proof, **options)
            proof['source_version'] = 'forged'
            value['window']['source_version'] = 'forged'
            return value
        with self.assertRaisesRegex(ValueError, 'context_binding'):
            self.lookup(source_context=context)

    def test_one_stale_room_does_not_hide_independent_current_source_or_reveal_stale_relation(self):
        independent = '회의 일정은 별도 계약 검토와 배송 정책 변경을 확인한 다음에 공유합니다.'
        self.engine.seed({'account_id':'owner','complete':True,'rooms':[{'id':'b','title':'independent'}]})
        self.reader.pages[('b', None)] = ([item(3, independent)], False, None)
        self.engine.step()
        first = 'messenger-content:' + hashlib.sha256(independent.encode()).hexdigest()
        second = 'messenger-content:' + hashlib.sha256(self.case.values[1]['body'].encode()).hexdigest()
        self.engine.store.ingest(self.engine.target, [], relations=[{'source_platform':'graph',
            'source_id':first,'target_platform':'graph','target_id':second,'type':'reference',
            'evidence':{'basis':'controlled explicit stored relation'}}])
        narrow = self.lookup(also=[identity('graph', first)], max_entities=1)
        self.assertEqual([p['fact_type'] for p in narrow['fact_provenance']], ['entity'])
        self.assertEqual(narrow['conversation_context']['unverified_relation_endpoints'], 1)
        self.reader.pages[('a', None)] = ([item(2, '4시요'), self.case.values[1]], False, None)
        result = self.lookup(also=[identity('graph', first), identity('graph', second)])
        self.assertEqual([p['entity_id'] for p in result['fact_provenance']], [identity('graph', first)])
        self.assertEqual([w['room_id'] for w in self.excerpts(result)], ['b'])
        self.assertNotIn('3시요', json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
