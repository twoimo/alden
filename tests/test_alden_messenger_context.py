"""Bounded current conversation spans preserve context without inventing facts."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest
from unittest.mock import patch
from tests.test_alden_messenger_frontier import Reader, candidate, item, journal_rows
from alden_collection import encoded


class MessengerContextTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir='/private/tmp'); self.addCleanup(temporary.cleanup)
        self.root = candidate.create_root(Path(temporary.name) / 'candidate')
        self.values = [item(2, '3시요'), item(1, '회의 일정은 몇 시로 변경되나요?')]
        self.reader = Reader({('a', None): (self.values, False, None)})
        self.engine = candidate.Frontier(self.root, 'kakaotalk', 'owner', self.reader)
        self.engine.seed({'account_id': 'owner', 'complete': True, 'rooms': [{'id': 'a', 'title': 'fixture'}]})
        self.engine.step()

    def context(self, message='1', **options):
        return self.engine.context_window('a', message, projects=[candidate.PROJECT], **options)

    def drop_timestamp(self):
        with self.engine.database() as db:
            db.execute('DROP INDEX message_context_time')
            db.execute('ALTER TABLE message_heads DROP COLUMN sent_at')

    def test_question_and_short_answer_are_exact_transcript_not_a_fact_or_fake_activity(self):
        before = journal_rows(self.engine.store)
        with self.engine.store.database() as db:
            versions = db.execute('SELECT count(*) FROM versions').fetchone()[0]
        result = self.context()
        self.assertEqual([r['body'] for r in result['items']], [self.values[1]['body'], '3시요'])
        self.assertEqual([r['state'] for r in result['items']], ['candidate', 'context'])
        self.assertEqual([r['anchor'] for r in result['items']], [True, False])
        self.assertIn('not a reply', result['scope'])
        self.assertEqual(journal_rows(self.engine.store), before)
        with self.engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], versions)
        # Numeric answer is retained, never promoted as an isolated graph node.
        self.assertEqual(len(self.engine.store.graph(projects=[candidate.PROJECT])['nodes']), 1)

    def test_a_context_message_can_be_the_anchor_without_becoming_a_graph_node(self):
        result = self.context('2')
        self.assertEqual([r['message_id'] for r in result['items']], ['1', '2'])
        self.assertTrue(result['items'][1]['anchor'])

    def test_cross_page_neighbors_and_time_gaps_are_bounded_to_the_same_room(self):
        self.reader.pages[('a', None)] = ([item(3, '확인했습니다')], True, 'older')
        self.engine.step(mode='live')
        self.reader.pages[('a', 'older')] = (self.values, False, None)
        # A finite reader window cannot prove moved old rows still exist.
        with self.assertRaisesRegex(candidate.SourceBlocked, 'window_incomplete'):
            self.context('2', before=1, after=1)
        # Exact-ID source capability can independently confirm both pages.
        current = [item(3, '확인했습니다'), *self.values]
        self.reader.verify_prepared = lambda payload: candidate.verify_prepared_items(payload['items'], current)
        result = self.context('2', before=1, after=1)
        self.assertEqual([r['message_id'] for r in result['items']], ['1', '2', '3'])
        self.assertEqual([r['message_id'] for r in self.context('2', max_gap=0)['items']], ['2'])
        self.engine.seed({'account_id': 'owner', 'complete': True, 'rooms': [{'id': 'b', 'title': 'fixture'}]})
        self.reader.pages[('b', None)] = ([item(4, '다른 방의 비공개 맥락')], False, None)
        self.engine.step()
        self.assertNotIn('4', [r['message_id'] for r in self.context('2')['items']])

    def test_collection_pause_keeps_authorized_context_read_only_and_repeatable(self):
        with self.engine.store.database() as db:
            db.execute('UPDATE targets SET enabled=0')
        before = journal_rows(self.engine.store)
        first, second = self.context(), self.context()
        self.assertEqual(first['window_id'], second['window_id'])
        self.assertEqual(journal_rows(self.engine.store), before)

    def test_changed_context_gets_a_new_window_identity_without_promoting_a_numeric_node(self):
        first = self.context()
        self.reader.pages[('a', None)] = ([item(2, '4시요'), self.values[1]], False, None)
        self.engine.step(mode='reconcile')
        second = self.context()
        self.assertNotEqual(first['window_id'], second['window_id'])
        self.assertEqual(second['items'][1]['body'], '4시요')
        self.assertEqual(len(self.engine.store.graph(projects=[candidate.PROJECT])['nodes']), 1)

    def test_current_canonical_visibility_or_edits_block_old_cached_context(self):
        self.reader.pages[('a', None)] = ([item(2, visible=False), self.values[1]], False, None)
        with self.assertRaisesRegex(candidate.SourceBlocked, 'messenger_prepared_source_changed'):
            self.context()
        self.reader.pages[('a', None)] = ([item(2, '4시요'), self.values[1]], False, None)
        with self.assertRaisesRegex(candidate.SourceBlocked, 'messenger_prepared_source_changed'):
            self.context()

    def test_authorization_denial_precedes_source_body_revalidation(self):
        self.reader.blocked = True
        with patch.object(self.reader, 'verify_prepared', wraps=self.reader.verify_prepared) as verify:
            with self.assertRaises(candidate.SourceBlocked): self.context()
            verify.assert_not_called()

    def test_permission_and_account_changes_are_rechecked(self):
        self.reader.account = 'other'
        with self.assertRaisesRegex(candidate.SourceBlocked, 'account_changed'): self.context()
        self.reader.account = 'owner'
        with self.engine.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied'")
        with self.assertRaisesRegex(candidate.SourceBlocked, 'project_denied'): self.context()

    def test_permission_lost_during_revalidation_prevents_returning_selected_bodies(self):
        def revoke(payload):
            with self.engine.store.database() as db:
                db.execute("UPDATE target_projects SET permission='denied'")
        with patch.object(self.reader, 'verify_prepared', side_effect=revoke):
            with self.assertRaisesRegex(candidate.SourceBlocked, 'project_denied'): self.context()

    def test_concurrent_frontier_change_blocks_stale_context(self):
        def change(payload):
            with self.engine.database() as db:
                r = db.execute("SELECT frontier FROM rooms WHERE id='a'").fetchone()
                state = json.loads(r[0]); state['source_epoch'] += 1
                db.execute("UPDATE rooms SET frontier=? WHERE id='a'", (encoded(state).decode(),))
        with patch.object(self.reader, 'verify_prepared', side_effect=change):
            with self.assertRaisesRegex(candidate.SourceBlocked, 'frontier_changed'): self.context()

    def test_quiet_checkpoint_progress_does_not_block_the_same_current_source_span(self):
        verify = self.reader.verify_prepared
        def advance(payload):
            verify(payload)
            with self.engine.database() as db:
                state = json.loads(db.execute("SELECT frontier FROM rooms WHERE id='a'").fetchone()[0])
                state['check_cursor'] = 'next-metadata-page'
                db.execute("UPDATE rooms SET frontier=? WHERE id='a'", (encoded(state).decode(),))
        before = self.context()['window_id']
        with patch.object(self.reader, 'verify_prepared', side_effect=advance):
            self.assertEqual(self.context()['window_id'], before)

    def test_head_tampering_cannot_supply_new_context_body_or_timestamp(self):
        with self.engine.database() as db:
            db.execute("UPDATE message_heads SET sent_at=1001 WHERE message='2'")
        with self.assertRaisesRegex(candidate.SourceBlocked, 'observation_changed'): self.context()

    def test_hidden_rows_remain_explicit_gaps_and_are_not_joined_into_an_agreement(self):
        self.reader.pages[('a', None)] = ([item(3, '확인했습니다'), item(2, visible=False), self.values[1]], False, None)
        self.engine.step(mode='reconcile')
        result = self.context()
        self.assertEqual([r['message_id'] for r in result['items']], ['1', '2', '3'])
        self.assertIsNone(result['items'][1]['body'])
        self.assertEqual(result['items'][1]['state'], 'withheld')
        self.assertNotIn('relations', result)

    def test_legacy_timestamp_migration_is_atomic_and_recovers_from_interruption(self):
        self.drop_timestamp()
        with patch.object(candidate.Frontier, '_retained_page', side_effect=RuntimeError('migration interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'migration interrupted'):
                candidate.Frontier(self.root, 'kakaotalk', 'owner', self.reader)
        with sqlite3.connect(self.engine.path) as db:
            self.assertNotIn('sent_at', {r[1] for r in db.execute('PRAGMA table_info(message_heads)')})
        self.engine = candidate.Frontier(self.root, 'kakaotalk', 'owner', self.reader)
        self.assertEqual([r['body'] for r in self.context()['items']], [self.values[1]['body'], '3시요'])

    def test_query_bounds_and_body_budget_fail_explicitly(self):
        for options in ({'before': True}, {'after': 6}, {'max_gap': 3601}):
            with self.subTest(options=options), self.assertRaises(ValueError): self.context(**options)
        huge = item(3, '가' * 30000)
        self.reader.pages[('a', None)] = ([huge, *self.values], False, None)
        self.engine.step(mode='reconcile')
        with patch.object(self.reader, 'verify_prepared', wraps=self.reader.verify_prepared) as verify:
            with self.assertRaisesRegex(candidate.SourceBlocked, 'context_budget'): self.context()
            verify.assert_not_called()

    def test_canonical_kakao_adapter_uses_exact_ids_for_current_context_readback(self):
        class Canonical:
            def __init__(self):
                self.rows = [{'chatId': 'a', 'logId': r['id'], 'sentAt': r['sent_at'],
                    'message': r['body'], 'message_state': None} for r in self_outer.values]
                self.calls = []

            def snapshot(self, **options):
                self.calls.append(options)
                rows = [r for r in self.rows if options.get('message_id') in (None, r['logId'])]
                return {'status': 'ok', 'db_readonly_verified': True, 'schema_decryption_verified': True,
                    'chat': {'chatId': 'a'}, 'messages': rows, 'messages_truncated': False, 'next_cursor': None}
        self_outer = self
        canonical = Canonical(); reader = candidate.KakaoReader(canonical)
        reader.identity = lambda: 'owner'  # Fake host identity only; real adapter/page validation.
        engine = candidate.Frontier(candidate.create_root(self.root / 'adapter'), 'kakaotalk', 'owner', reader)
        engine.seed({'account_id': 'owner', 'complete': True, 'rooms': [{'id': 'a', 'title': 'fixture'}]})
        engine.step(); canonical.calls.clear()
        result = engine.context_window('a', '1', projects=[candidate.PROJECT])
        self.assertEqual([r['body'] for r in result['items']], [self.values[1]['body'], '3시요'])
        self.assertEqual([r['message_id'] for r in canonical.calls], ['1', '2'])
        self.assertTrue(all(r['limit'] == 1 for r in canonical.calls))
        canonical.rows[0]['message_state'] = 'hidden'
        canonical.rows[0]['message'] = None
        with self.assertRaisesRegex(candidate.SourceBlocked, 'source_changed'):
            engine.context_window('a', '1', projects=[candidate.PROJECT])

    def test_telegram_date_grant_and_midread_expiry_guard_the_whole_span(self):
        from tests.test_alden_messenger_access import FrontierAccessTests
        case = FrontierAccessTests(); case.setUp(); self.addCleanup(case.doCleanups)
        read = lambda: case.engine.context_window('a', 'a', projects=[candidate.PROJECT])
        self.assertEqual(read()['items'][0]['body'], case.text)
        count = len(case.body_calls); case.denied.add('a')
        with self.assertRaises(candidate.SourceBlocked): read()
        self.assertEqual(len(case.body_calls), count)
        case.denied.clear(); case.scopes['a']['since'] = '2026-10-21'
        with self.assertRaisesRegex(candidate.SourceBlocked, 'authorization_changed'): read()
        self.assertEqual(len(case.body_calls), count)
        case.scopes['a']['since'] = '2026-10-20'
        verify = case.reader.verify_prepared
        def expire(payload):
            verify(payload); case.grants['a'] = {'controlled_authorization_revision': 'fixture-v2'}
        with patch.object(case.reader, 'verify_prepared', side_effect=expire):
            with self.assertRaisesRegex(candidate.SourceBlocked, 'authorization_changed'): read()
