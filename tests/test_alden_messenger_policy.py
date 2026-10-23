"""Exact content identity and recoverable, unactivated policy snapshots."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import sys
import unicodedata
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'docs/evaluation/messenger-stream-20261021'))
import messenger_candidate as candidate
from messenger_policy_migration import PolicyMigration
from messenger_quality import classify

OLD_POLICY = 'messenger-triage-nfc-v2'
CODE = "result = '회의 자료의 원문과 코드 들여쓰기를 그대로 보존합니다. 검토 후에 공유합니다.'\nprint(result)"


def legacy_classify(body):
    return classify(unicodedata.normalize('NFC', body).strip() if isinstance(body, str) else body)


@contextmanager
def legacy_policy():
    with patch.object(candidate, 'POLICY', OLD_POLICY), patch.object(candidate, 'classify', legacy_classify):
        yield


def item(number, text=CODE):
    return {'id': str(number), 'body': text, 'visible': text is not None, 'sent_at': 1000 + number}


class CurrentReader:
    account = 'owner'
    blocked = False

    def __init__(self, pages):
        self.pages, self.refreshes = pages, []

    def identity(self):
        return self.account

    def authorize(self, room):
        if self.blocked:
            raise candidate.SourceBlocked('context_consent_expired')

    def page(self, room, cursor):
        self.authorize(room)
        items, more, following = self.pages[(room, cursor)]
        return {'account_id': self.account, 'room_id': room, 'items': items,
                'more': more, 'next_cursor': following, 'scope': 'controlled current canonical-reader fixture'}

    def refresh_prepared(self, payload):
        self.authorize(payload['room'])
        cursor = payload.get('reader_before', payload['before'])
        self.refreshes.append((payload['room'], cursor))
        page = self.page(payload['room'], cursor)
        wanted = {r['id'] for r in payload['items']}
        rows = [r for r in page['items'] if r['id'] in wanted]
        if len(rows) != len(wanted):
            raise candidate.SourceBlocked('messenger_prepared_window_incomplete')
        return {**page, 'items': rows, 'more': False, 'next_cursor': None}

    def verify_prepared(self, payload):
        candidate.verify_prepared_items(payload['items'], self.refresh_prepared(payload)['items'])


class MessengerPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = candidate.create_root(self.folder / 'original')

    def frontier(self, pages, platform='kakaotalk'):
        reader = CurrentReader(pages)
        frontier = candidate.Frontier(self.root, platform, 'owner', reader)
        frontier.seed({'account_id': 'owner', 'complete': True,
                       'rooms': [{'id': r, 'title': r} for r in sorted({r for r, _ in pages})]})
        return frontier, reader

    def snapshot(self, frontier):
        with frontier.database() as db:
            frontier_rows = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY rowid')]
                             for t in ('rooms', 'pages', 'message_heads', 'head_fingerprints')}
        with frontier.store.database() as db:
            store_rows = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY rowid')]
                          for t in ('versions', 'events', 'runs')}
        return frontier_rows, store_rows

    def finish(self, migration):
        for _ in range(30):
            result = migration.step()
            if result['state'] == 'ready':
                return result
        self.fail('migration did not reach its bounded completion')

    def legacy_pair(self):
        frontier, reader = self.frontier({('a', None): ([item(1), item(2, '  ' + CODE)], False, None)})
        with legacy_policy():
            frontier.step()
            self.assertEqual(len(frontier.store.graph(projects=[candidate.PROJECT])['nodes']), 1)
        return frontier, reader

    def test_exact_identity_preserves_code_semantics_and_all_whitespace(self):
        compile(CODE, '<original>', 'exec')
        with self.assertRaises(IndentationError):
            compile('  ' + CODE, '<indented>', 'exec')
        bodies = [CODE, '  ' + CODE, CODE + ' ', CODE + '\n', CODE.replace('\n', '\r\n')]
        decisions = [classify(s) for s in bodies]
        self.assertEqual(len({d.content_hash for d in decisions}), len(bodies))
        self.assertEqual([d.text for d in decisions], bodies)
        frontier, _ = self.frontier({('a', None): ([item(i + 1, s) for i, s in enumerate(bodies[:4])], False, None)})
        frontier.step()
        self.assertEqual(len(frontier.store.graph(projects=[candidate.PROJECT])['nodes']), 4)
        with frontier.store.database() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT body FROM versions')}, set(bodies[:4]))

    def test_nfc_equivalents_still_share_content_and_retain_both_originals(self):
        decomposed = unicodedata.normalize('NFD', CODE)
        self.assertEqual(classify(CODE).content_hash, classify(decomposed).content_hash)
        frontier, _ = self.frontier({('a', None): ([item(1), item(2, decomposed)], False, None)})
        frontier.step()
        self.assertEqual(len(frontier.store.graph(projects=[candidate.PROJECT])['nodes']), 1)
        self.assertEqual(len(frontier.origins(classify(CODE).content_hash)['items']), 2)
        with frontier.database() as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM pages').fetchone()[0])['items'][1]['body'], decomposed)

    def test_triage_does_not_erase_numeric_link_ack_or_advertising_source(self):
        for body, state in [('  1\n/\n2\n6\n1\n3  ', 'context'), (' 네!\n', 'context'),
                            (' https://example.com/read\n', 'context'), ('원금 보장 지금 입장하세요.', 'review')]:
            with self.subTest(body=body):
                result = classify(body)
                self.assertEqual((result.state, result.text), (state, body))

    def test_staged_split_retains_original_root_and_discovery_cursors(self):
        frontier, _ = self.legacy_pair()
        original = self.snapshot(frontier)
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        with self.assertRaisesRegex(candidate.SourceBlocked, 'not_ready'):
            migration.read_action('collection-graph')
        self.finish(migration)
        self.assertEqual(self.snapshot(frontier), original)
        staged = migration._frontiers[frontier.target]
        self.assertEqual(len(migration.read_action('collection-graph')['nodes']), 2)
        with staged.database() as db:
            state = json.loads(db.execute('SELECT frontier FROM rooms').fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM pages').fetchone()[0], 3)
        old_state = json.loads(original[0]['rooms'][0][2])
        for key in ('back_cursor', 'back_done', 'live_head', 'live_cursor', 'live_new_head', 'check_cursor'):
            self.assertEqual(state.get(key), old_state.get(key))
        self.assertEqual(migration.identity_map()['mapping'], {
            hashlib.sha256(CODE.encode()).hexdigest(): sorted(classify(s).content_hash for s in (CODE, '  ' + CODE))})
        self.assertFalse(migration.status()['production_activated'])

    def test_crash_after_store_commit_reopens_same_job_without_duplicate_events(self):
        frontier, reader = self.legacy_pair()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        def crash():
            raise RuntimeError('controlled interruption after store commit')
        with self.assertRaisesRegex(RuntimeError, 'controlled interruption'):
            migration.step(after_publish=crash)
        staged = migration._frontiers[frontier.target]
        with staged.store.database() as db:
            events = [tuple(r) for r in db.execute('SELECT * FROM events ORDER BY sequence')]
        reopened = PolicyMigration(migration.root, [candidate.Frontier(migration.root, frontier.platform, frontier.account, reader)])
        self.assertEqual(reopened.step()['state'], 'processed')
        from tests.test_alden_messenger_frontier import assert_readiness_extension
        assert_readiness_extension(self, staged.store, events)
        self.finish(reopened)
        self.assertEqual(len(reopened.read_action('collection-graph')['nodes']), 2)

    def test_old_prepared_page_is_retained_but_never_replayed_as_current_policy(self):
        frontier, _ = self.frontier({('a', None): ([item(1), item(2, '  ' + CODE)], False, None)})
        with legacy_policy(), frontier.database() as db:
            room = dict(db.execute('SELECT * FROM rooms').fetchone())
        with legacy_policy():
            job = frontier._prepare(room, 'backfill', frontier.reader.page('a', None))
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        self.finish(migration)
        self.assertEqual(len(migration.read_action('collection-graph')['nodes']), 2)
        with migration._frontiers[frontier.target].database() as db:
            self.assertEqual(db.execute('SELECT state FROM pages WHERE id=?', (job,)).fetchone()[0], 'prepared')
            self.assertIsNotNone(db.execute('SELECT reason FROM retired_policy_pages WHERE page=?', (job,)).fetchone())

    def test_legacy_store_commit_without_head_commit_is_not_lost_or_replayed(self):
        frontier, _ = self.frontier({('a', None): ([item(1), item(2, '  ' + CODE)], False, None)})
        def crash():
            raise RuntimeError('legacy store committed')
        with legacy_policy(), self.assertRaisesRegex(RuntimeError, 'legacy store committed'):
            frontier.step(after_publish=crash)
        original = self.snapshot(frontier)
        self.assertEqual(len(original[0]['message_heads']), 0)
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        self.finish(migration)
        self.assertEqual(len(migration.read_action('collection-graph')['nodes']), 2)
        self.assertEqual(self.snapshot(frontier), original)

    def test_revoked_prepared_job_is_retired_before_current_observation_retry(self):
        frontier, reader = self.legacy_pair()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        def revoke(payload):
            reader.blocked = True
            raise candidate.SourceBlocked('context_consent_expired')
        with patch.object(reader, 'verify_prepared', side_effect=revoke):
            self.assertEqual(migration.step()['state'], 'blocked')
        reader.blocked = False
        reader.pages[('a', None)] = ([item(1, CODE + ' # new'), item(2, '  ' + CODE)], False, None)
        migration.retry_blocked()
        self.finish(migration)
        self.assertEqual(migration.status()['tasks'], {'complete': 2})
        self.assertEqual(len(migration.read_action('collection-graph')['nodes']), 2)

    def test_telegram_refresh_preserves_old_window_and_blocks_missing_or_duplicate_rows(self):
        reader = candidate.TelegramReader(lambda _: None, {}, lambda *args: None)
        payload = {'room': 'a', 'before': None, 'reader_before': 'older', 'items': [item(1)]}
        page = {'items': [item(3), item(1)], 'more': True, 'next_cursor': 'earlier'}
        with patch.object(reader, 'page', return_value=page) as observed:
            reader.verify_prepared(payload)
            observed.assert_called_once_with('a', 'older')
        for rows, reason in [([item(3)], 'window_incomplete'), ([item(1), item(1)], 'duplicate')]:
            with self.subTest(reason=reason), patch.object(reader, 'page', return_value={**page, 'items': rows}):
                with self.assertRaisesRegex(candidate.SourceBlocked, reason):
                    reader.refresh_prepared(payload)

    def test_kakao_refresh_reads_exact_ids_and_rejects_account_change(self):
        reader = candidate.KakaoReader(None)
        payload = {'room': 'a', 'before': 'ignored', 'items': [item(1)]}
        page = {'items': [item(1)], 'scope': 'canonical current exact-id fixture'}
        with (patch.object(reader, 'identity', return_value='owner'),
              patch.object(reader, 'page', return_value=page) as observed):
            reader.verify_prepared(payload)
            observed.assert_called_once_with('a', None, message_id='1')
        with (patch.object(reader, 'identity', side_effect=['owner', 'other']),
              patch.object(reader, 'page', return_value=page)):
            with self.assertRaisesRegex(candidate.SourceBlocked, 'account_changed'):
                reader.refresh_prepared(payload)

    def test_consent_expiry_blocks_before_body_refresh_and_retry_rechecks_current_source(self):
        frontier, reader = self.legacy_pair()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        reader.blocked = True
        self.finish(migration)
        self.assertEqual(migration.status()['tasks'], {'blocked': 2})
        self.assertEqual(reader.refreshes, [])
        self.assertEqual(migration.read_action('collection-graph')['nodes'], [])
        reader.blocked = False
        migration.retry_blocked()
        self.assertEqual(migration.status()['phase'], 'prepared')
        self.finish(migration)
        self.assertEqual(len(migration.read_action('collection-graph')['nodes']), 2)

    def test_account_change_and_missing_window_are_blocked_without_inferred_deletion(self):
        frontier, reader = self.legacy_pair()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        reader.account = 'different-owner'
        self.assertEqual(migration.step()['state'], 'blocked')
        self.assertEqual(reader.refreshes, [])
        reader.account = 'owner'
        reader.pages[('a', None)] = ([], False, None)
        self.finish(migration)
        self.assertEqual(migration.status()['tasks'], {'blocked': 2})
        with migration._frontiers[frontier.target].database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM message_heads').fetchone()[0], 2)
        self.assertEqual(migration.read_action('collection-graph')['nodes'], [])

    def test_current_withheld_observation_does_not_become_a_knowledge_node(self):
        frontier, reader = self.legacy_pair()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        reader.pages[('a', None)] = ([item(1, None), item(2, None)], False, None)
        self.finish(migration)
        self.assertEqual(migration.read_action('collection-graph')['nodes'], [])
        self.assertEqual(migration.status()['tasks'], {'complete': 2})

    def test_reprocessing_refreshes_graph_without_combat_but_new_live_message_emits_receipt(self):
        frontier, reader = self.legacy_pair()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        staged = migration._frontiers[frontier.target]
        baseline = staged.store.activity_page(projects=[candidate.PROJECT])
        self.finish(migration)
        page = staged.store.activity_page(projects=[candidate.PROJECT], after=baseline['cursor'], stream_id=baseline['stream_id'])
        self.assertEqual(page['items'], [])
        self.assertTrue(page['refresh_required'])
        self.assertGreater(page['cursor'], baseline['cursor'])
        baseline = staged.store.activity_page(projects=[candidate.PROJECT])
        reader.pages[('a', None)] = ([item(3, '운영 정책 변경 공지는 다음 주 월요일에 확인하고 계약 일정에 반영합니다.'), item(1)], False, None)
        staged.step(mode='live')
        page = staged.store.activity_page(projects=[candidate.PROJECT], after=baseline['cursor'], stream_id=baseline['stream_id'])
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(page['items'][0]['origin'], 'messenger-db:kakaotalk:live')

    def test_older_window_revalidation_keeps_reader_cursor_independent_of_discovery(self):
        frontier, reader = self.frontier({('a', None): ([item(3)], True, 'older'),
                                          ('a', 'older'): ([item(1)], False, None)})
        with legacy_policy():
            frontier.step(); frontier.step()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        self.finish(migration)
        self.assertEqual(migration.status()['tasks'], {'complete': 2})
        self.assertIn(('a', 'older'), reader.refreshes)
        # A snapshot of an already staged policy must keep the same actual
        # reader window, independent of its policy bookkeeping cursor.
        second = PolicyMigration.create(list(migration._frontiers.values()), self.folder / 'second-staged')
        self.finish(second)
        self.assertEqual(second.status()['tasks'], {'complete': 2})

    def test_cancelled_step_does_not_advance_any_task(self):
        frontier, _ = self.legacy_pair()
        migration = PolicyMigration.create([frontier], self.folder / 'staged')
        self.assertEqual(migration.step(cancelled=lambda: True), {'state': 'cancelled'})
        self.assertEqual(migration.status()['tasks'], {'pending': 2})

    def test_concurrent_source_write_aborts_copy_without_ready_snapshot(self):
        frontier, _ = self.legacy_pair()
        original = self.snapshot(frontier)
        def concurrent_write():
            with frontier.database() as db:
                db.execute("UPDATE rooms SET checked=checked+1")
        destination = self.folder / 'staged'
        with self.assertRaisesRegex(RuntimeError, 'changed_during_snapshot'):
            PolicyMigration.create([frontier], destination, after_copy=concurrent_write)
        with self.assertRaisesRegex(candidate.SourceBlocked, 'not_ready'):
            PolicyMigration(destination, [candidate.Frontier(destination, frontier.platform, frontier.account, frontier.reader)]).read_action('collection-graph')
        self.assertEqual(self.snapshot(frontier)[1], original[1])

    def test_destination_cannot_overwrite_existing_or_source_directory(self):
        frontier, _ = self.legacy_pair()
        for destination in (self.root, self.root / 'nested', self.folder):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, 'destination_required'):
                PolicyMigration.create([frontier], destination)


if __name__ == '__main__':
    unittest.main()
