"""Behavioral checks for the isolated all-room frontier and store boundaries."""
from pathlib import Path
from contextlib import contextmanager
from tempfile import TemporaryDirectory
import importlib.util
import hashlib
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, encoded, identity

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'docs/evaluation/messenger-stream-20261021'))

SPEC = importlib.util.spec_from_file_location('messenger_candidate',
    Path(__file__).resolve().parents[1] / 'docs/evaluation/messenger-stream-20261021/messenger_candidate.py')
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)
from messenger_quality import classify


def item(number, body=None, visible=True):
    return {'id': str(number), 'body': (body or '자료 검토 회의 일정 ' + str(number)) if visible else None,
            'visible': visible, 'sent_at': 1000 + number}


def journal_rows(store):
    with store.database() as db:
        return [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY sequence')]


class Reader:
    account = 'owner'
    blocked = False

    def __init__(self, pages):
        self.pages, self.reads = pages, []

    def identity(self):
        return self.account

    def authorize(self, room):
        if self.blocked:
            raise candidate.SourceBlocked('context_consent_expired')

    def page(self, room, cursor):
        self.authorize(room)
        self.reads.append((room, cursor))
        values, more, next_cursor = self.pages[(room, cursor)]
        return {'account_id': self.account, 'room_id': room, 'items': values,
                'more': more, 'next_cursor': next_cursor, 'scope': 'owned fixture'}

    def verify_prepared(self, payload):
        self.authorize(payload['room'])
        values = self.pages[(payload['room'], payload['before'])][0]
        candidate.verify_prepared_items(payload['items'], values)


class FrontierTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temp.cleanup)
        self.root = candidate.create_root(Path(temp.name) / 'candidate')

    def frontier(self, pages, rooms=('a',), platform='kakaotalk'):
        reader = Reader(pages)
        engine = candidate.Frontier(self.root, platform, 'owner', reader)
        engine.seed({'account_id': 'owner', 'complete': True,
            'rooms': [{'id': room, 'title': 'same title'} for room in rooms]})
        return engine, reader

    def test_full_catalog_is_not_limited_to_the_existing_200_target_scan(self):
        engine, _ = self.frontier({}, tuple(str(n) for n in range(707)))
        self.assertEqual(engine.status()['rooms'], 707)
        self.assertEqual(engine.status()['backfilled_rooms'], 0)

    def test_same_message_id_in_different_rooms_and_platforms_remains_separate(self):
        engine, _ = self.frontier({('a', None): ([item(1)], False, None),
                                   ('b', None): ([item(1)], False, None)}, ('a', 'b'))
        engine.step(); engine.step()
        other, _ = self.frontier({('a', None): ([item(1)], False, None)}, platform='telegram')
        other.step()
        self.assertEqual(len(engine.store.search('자료', projects=[candidate.PROJECT])), 1)
        content = classify(item(1)['body']).content_hash
        self.assertEqual(len(engine.origins(content)['items']), 2)
        self.assertEqual(len(other.origins(content)['items']), 1)

    def test_context_and_review_are_retained_without_creating_noise_nodes(self):
        values = [item(1, '안녕하세요'), item(2, '1\n/\n2\n6\n1\n3'),
                  item(3, '지금 입장하세요. 수익 보장 이벤트는 별도 검토가 필요합니다.')]
        engine, _ = self.frontier({('a', None): (values, False, None)})
        result = engine.step()
        self.assertEqual(result['triage'], {'candidate': 0, 'context': 2, 'review': 1, 'withheld': 0})
        self.assertEqual(engine.store.graph(projects=[candidate.PROJECT])['nodes'], [])
        with engine.database() as db:
            page = json.loads(db.execute('SELECT payload FROM pages').fetchone()[0])
        self.assertEqual(page['items'], values)

    def test_exact_duplicate_survives_until_its_last_visible_origin_is_withdrawn(self):
        body = '회의 일정은 2026년 10월 22일 오후 3시로 변경되었습니다.'
        engine, reader = self.frontier({('a', None): ([item(1, body)], False, None),
                                       ('b', None): ([item(2, body)], False, None)}, ('a', 'b'))
        engine.step(); engine.step()
        self.assertEqual(len(engine.store.search('회의', projects=[candidate.PROJECT])), 1)
        reader.pages[('a', None)] = ([item(1, visible=False)], False, None)
        engine.step(mode='live')
        self.assertEqual(len(engine.store.search('회의', projects=[candidate.PROJECT])), 1)
        reader.pages[('b', None)] = ([item(2, visible=False)], False, None)
        engine.step(mode='live')
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])

    def test_changed_numbers_and_direction_are_not_deduplicated_as_entities(self):
        engine, _ = self.frontier({('a', None): ([item(1, '매출이 10% 증가했습니다.'),
            item(2, '매출이 10% 감소했습니다.'), item(3, '매출이 20% 증가했습니다.')], False, None)})
        engine.step()
        self.assertEqual(len(engine.store.search('매출', projects=[candidate.PROJECT])), 3)

    def test_numeric_and_url_headers_do_not_become_graph_labels(self):
        body = '1\n/\n2\nhttps://example.com/article\n회의 일정이 다음 주 화요일 오후 3시로 변경되었습니다.\n6\n1\n3'
        engine, _ = self.frontier({('a', None): ([item(1, body)], False, None)})
        engine.step()
        result = engine.store.search('회의', projects=[candidate.PROJECT])
        self.assertEqual(result[0]['label'], '회의 일정이 다음 주 화요일 오후 3시로 변경되었습니다.')
        with engine.database() as db:
            payload = json.loads(db.execute('SELECT payload FROM pages').fetchone()[0])
        self.assertEqual(payload['items'][0]['body'], body)
        with engine.store.database() as db:
            self.assertEqual(db.execute('SELECT body FROM versions').fetchone()[0], body)

    def test_url_length_cannot_turn_decorations_or_short_context_into_knowledge(self):
        url = 'https://example.com/' + 'long-identifier-' * 12
        self.assertEqual(classify(url + ' 👀').state, 'context')
        self.assertEqual(classify(url + '\n참고').state, 'context')
        engine, _ = self.frontier({('a', None): ([item(1, url + ' 👀')], False, None)})
        engine.step()
        self.assertEqual(engine.store.graph(projects=[candidate.PROJECT])['nodes'], [])
        with engine.database() as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM pages').fetchone()[0])['items'][0]['body'], url + ' 👀')

    def test_url_prefix_does_not_hide_a_substantive_same_line_label(self):
        body = 'https://example.com/article 회의 일정은 2026년 11월 3일로 변경되었습니다.'
        engine, _ = self.frontier({('a', None): ([item(1, body)], False, None)})
        engine.step()
        result = engine.store.search('회의', projects=[candidate.PROJECT])
        self.assertEqual(result[0]['label'], '회의 일정은 2026년 11월 3일로 변경되었습니다.')

    def test_backfill_and_live_burst_keep_independent_frontiers(self):
        engine, reader = self.frontier({('a', None): ([item(5), item(4)], True, 'older'),
                                       ('a', 'older'): ([item(3), item(2)], False, None)})
        engine.step()
        reader.pages[('a', None)] = ([item(8), item(7)], True, 'live-tail')
        engine.step(mode='live')
        reader.pages[('a', 'live-tail')] = ([item(6), item(5)], True, 'past-head')
        engine.step(mode='live')
        engine.step(mode='backfill')
        self.assertEqual([cursor for _, cursor in reader.reads], [None, None, 'live-tail', 'older'])
        self.assertEqual(len(engine.store.search('자료', projects=[candidate.PROJECT])), 7)
        self.assertEqual(engine.status()['backfilled_rooms'], 1)

    def test_recovery_replays_prepared_bytes_without_another_page_fetch(self):
        engine, reader = self.frontier({('a', None): ([item(2)], False, None)})
        def crash():
            raise SystemExit(73)
        with self.assertRaises(SystemExit):
            engine.step(after_publish=crash)
        before = journal_rows(engine.store)
        engine = candidate.Frontier(self.root, 'kakaotalk', 'owner', reader)
        self.assertEqual(engine.step()['state'], 'published')
        self.assertEqual(len(reader.reads), 1)
        self.assertEqual(journal_rows(engine.store), before)
        self.assertEqual(engine.status()['prepared_pages'], 0)

    def test_authorization_is_rechecked_before_prepared_replay(self):
        engine, reader = self.frontier({('a', None): ([item(2)], False, None)})
        with self.assertRaises(SystemExit):
            engine.step(after_publish=lambda: (_ for _ in ()).throw(SystemExit(73)))
        reader.blocked = True
        self.assertEqual(engine.step()['state'], 'blocked')
        self.assertEqual(engine.status()['prepared_pages'], 1)

    def test_authorization_expiry_during_store_write_rolls_back_publication(self):
        engine, reader = self.frontier({('a', None): ([item(2)], False, None)})
        blob = engine.store._blob
        def expire_after_blob(raw):
            result = blob(raw)
            reader.blocked = True
            return result
        with patch.object(engine.store, '_blob', side_effect=expire_after_blob):
            result = engine.step()
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])
        with engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE stage='stored'").fetchone()[0], 0)
        self.assertEqual(engine.status()['prepared_pages'], 1)
        reader.blocked = False
        with engine.database() as db:
            db.execute('UPDATE rooms SET retry_at=0')
        self.assertEqual(engine.step()['state'], 'published')
        self.assertEqual(len(reader.reads), 1)
        self.assertEqual(len(engine.store.search('회의', projects=[candidate.PROJECT])), 1)

    def test_recovered_page_does_not_publish_a_now_withheld_or_edited_message(self):
        for replacement in (item(2, visible=False), item(2, '회의 자료가 수정되어 예전 내용과 다릅니다.')):
            with self.subTest(visible=replacement['visible']):
                case = candidate.create_root(self.root / ('case-' + str(replacement['visible'])))
                reader = Reader({('a', None): ([item(2)], False, None)})
                engine = candidate.Frontier(case, 'kakaotalk', 'owner', reader)
                engine.seed({'account_id':'owner','complete':True,'rooms':[{'id':'a','title':'a'}]})
                page = reader.page('a', None)
                with engine.database() as db:
                    room = db.execute('SELECT * FROM rooms').fetchone()
                engine._prepare(room, 'backfill', page)
                reader.pages[('a', None)] = ([replacement], False, None)
                self.assertEqual(engine.step()['state'], 'blocked')
                self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])
                self.assertEqual(engine.status()['prepared_pages'], 1)
                with engine.database() as db:
                    self.assertFalse(json.loads(db.execute('SELECT frontier FROM rooms').fetchone()[0])['back_done'])

    def test_changed_prepared_checkpoint_is_rejected_before_source_access(self):
        engine, reader = self.frontier({('a', None): ([item(2)], False, None)})
        with engine.database() as db:
            room = db.execute('SELECT * FROM rooms').fetchone()
        job = engine._prepare(room, 'backfill', reader.page('a', None))
        with engine.database() as db:
            next_state = json.loads(db.execute('SELECT next_frontier FROM pages').fetchone()[0])
            next_state['back_done'] = False
            db.execute('UPDATE pages SET next_frontier=?', (json.dumps(next_state),))
        with self.assertRaisesRegex(ValueError, 'messenger_prepared_integrity'):
            engine._publish(job, cancelled=lambda:False, after_publish=lambda:None)
        self.assertEqual(len(reader.reads), 1)
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])

    def test_pending_telegram_page_cannot_publish_under_a_changed_date_scope(self):
        scopes = {'a': {'since': '2026-10-20', 'until': '2026-10-21'}}
        calls = []
        def invoke(args):
            calls.append(args)
            if args == ['probe']:
                return {'status': 'ready_readonly', 'read_only': True, 'owner_id': 'owner'}
            return {'status': 'ok', 'owner_id': 'owner', 'peer_id': 'a', 'content_returned': True,
                    'more': False, 'messages': [{'id': '1', 'peer_id': 'a', 'timestamp': 1,
                        'text': '과거 범위에만 허용된 회의 자료입니다.'}]}
        reader = candidate.TelegramReader(invoke, scopes, lambda *args: None)
        engine = candidate.Frontier(self.root, 'telegram', 'owner', reader)
        engine.seed({'account_id': 'owner', 'complete': True, 'rooms': [{'id': 'a', 'title': 'a'}]})
        page = reader.page('a', None)
        with engine.database() as db:
            room = db.execute('SELECT * FROM rooms WHERE id=?', ('a',)).fetchone()
        engine._prepare(room, 'backfill', page)
        scopes['a'] = {'since': '2026-10-22', 'until': '2026-10-23'}
        result = engine.step()
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(result['reason'], 'telegram_prepared_scope_changed')
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])
        self.assertEqual(sum(args[0] == 'messages' for args in calls), 1)
        self.assertEqual(engine.status()['prepared_pages'], 1)

    def test_reconciliation_detects_old_edits_without_rewinding_live_frontier(self):
        engine, reader = self.frontier({('a', None): ([item(3), item(2)], True, 'older'),
                                       ('a', 'older'): ([item(1)], False, None)})
        engine.step(); engine.step()
        reader.pages[('a', None)] = ([item(4), item(3)], True, 'current-older')
        engine.step(mode='live')
        reader.pages[('a', 'current-older')] = ([item(2, '마감 일정이 2026년 11월 3일로 변경되었습니다.'),
                                               item(1, visible=False)], False, None)
        engine.step(mode='reconcile'); engine.step(mode='reconcile')
        self.assertEqual(len(engine.store.search('마감', projects=[candidate.PROJECT])), 1)
        self.assertEqual(engine.store.search('자료 검토 회의 일정 1', projects=[candidate.PROJECT]), [])
        with engine.database() as db:
            state = json.loads(db.execute('SELECT frontier FROM rooms WHERE id=?', ('a',)).fetchone()[0])
        self.assertEqual(state['live_head'], '4')
        self.assertTrue(state['back_done'])
        self.assertIsNone(state['check_cursor'])
        before = engine.store.events(projects=[candidate.PROJECT])['items']
        engine.step(mode='reconcile')
        with engine.database() as db:
            self.assertEqual(json.loads(db.execute('SELECT frontier FROM rooms').fetchone()[0])['check_cursor'], 'current-older')
        engine.step(mode='reconcile')
        with engine.database() as db:
            self.assertIsNone(json.loads(db.execute('SELECT frontier FROM rooms').fetchone()[0])['check_cursor'])
        self.assertEqual(engine.store.events(projects=[candidate.PROJECT])['items'], before)

    def test_repeated_unchanged_live_page_is_quiet_and_does_not_fail(self):
        engine, _ = self.frontier({('a', None): ([item(2)], False, None)})
        engine.step(); engine.step(mode='live')
        before = engine.store.events(projects=[candidate.PROJECT])['items']
        self.assertEqual(engine.step(mode='live')['state'], 'unchanged')
        self.assertEqual(engine.store.events(projects=[candidate.PROJECT])['items'], before)

    def test_visible_withheld_visible_restores_the_same_source_without_a_new_version(self):
        body = '회의 일정은 다음 주 화요일 오후 3시로 확정되었습니다.'
        engine, reader = self.frontier({('a', None): ([item(1, body)], False, None)})
        engine.step(); engine.step(mode='live')
        reader.pages[('a', None)] = ([item(1, visible=False)], False, None)
        engine.step(mode='live')
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])
        reader.pages[('a', None)] = ([item(1, body)], False, None)
        engine.step(mode='live')
        self.assertEqual(len(engine.store.search('회의', projects=[candidate.PROJECT])), 1)
        with engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)
        before = engine.store.events(projects=[candidate.PROJECT])['items']
        self.assertEqual(engine.step(mode='live')['state'], 'unchanged')
        self.assertEqual(engine.store.events(projects=[candidate.PROJECT])['items'], before)

    def test_edited_source_can_return_to_its_original_text_without_replaying_an_old_completed_page(self):
        first = '계약 금액은 10만원으로 확정되었습니다.'
        revised = '계약 금액은 20만원으로 변경되었습니다.'
        engine, reader = self.frontier({('a', None): ([item(1, first)], False, None)})
        engine.step(); engine.step(mode='live')
        reader.pages[('a', None)] = ([item(1, revised)], False, None)
        engine.step(mode='live')
        reader.pages[('a', None)] = ([item(1, first)], False, None)
        engine.step(mode='live')
        self.assertEqual(len(engine.store.search('10만원', projects=[candidate.PROJECT])), 1)
        self.assertEqual(engine.store.search('20만원', projects=[candidate.PROJECT]), [])
        with engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 2)

    def test_restoration_after_store_commit_crash_resumes_once_in_a_new_instance(self):
        original = item(1, '회의 일정은 화요일 오후 3시로 확정되었습니다.')
        engine, reader = self.frontier({('a', None): ([original], False, None)})
        engine.step(); engine.step(mode='live')
        reader.pages[('a', None)] = ([item(1, visible=False)], False, None)
        engine.step(mode='live')
        reader.pages[('a', None)] = ([original], False, None)
        with self.assertRaises(SystemExit):
            engine.step(mode='live', after_publish=lambda: (_ for _ in ()).throw(SystemExit(73)))
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])
        before = journal_rows(engine.store)
        resumed = candidate.Frontier(self.root, 'kakaotalk', 'owner', reader)
        self.assertEqual(resumed.step(mode='live')['state'], 'published')
        self.assertEqual(resumed.status()['prepared_pages'], 0)
        self.assertEqual(journal_rows(resumed.store), before)
        self.assertEqual(resumed.step(mode='live')['state'], 'unchanged')
        with resumed.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)

    def test_restored_duplicate_origin_is_retained_without_another_body_version(self):
        body = '회의 자료를 다음 주 화요일까지 검토하기로 결정했습니다.'
        engine, reader = self.frontier({('a', None): ([item(1, body)], False, None),
                                       ('b', None): ([item(2, body)], False, None)}, ('a', 'b'))
        engine.step(); engine.step()
        reader.pages[('a', None)] = ([item(1, visible=False)], False, None)
        engine.step(mode='live')
        reader.pages[('a', None)] = ([item(1, body)], False, None)
        engine.step(mode='live'); engine.step(mode='live')
        origins = engine.origins(classify(body).content_hash)['items']
        self.assertEqual([(r['room'], r['state']) for r in origins], [('a', 'candidate'), ('b', 'candidate')])
        self.assertEqual(len(engine.store.search('회의', projects=[candidate.PROJECT])), 1)
        with engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)

    def test_metadata_and_timestamp_can_return_to_original_without_body_duplication(self):
        original = {**item(1), 'original': {'senderName': 'first', 'attachment': None}}
        revised = {**original, 'sent_at': 2000, 'original': {'senderName': 'revised', 'attachment': None}}
        engine, reader = self.frontier({('a', None): ([original], False, None)})
        engine.step(); engine.step(mode='live')
        for value in (revised, original):
            reader.pages[('a', None)] = ([value], False, None)
            self.assertEqual(engine.step(mode='live')['state'], 'published')
            with engine.database() as db:
                payload = json.loads(db.execute('''SELECT p.payload FROM message_heads h
                    JOIN pages p ON p.id=h.page''').fetchone()[0])
            self.assertEqual(payload['items'], [value])
        before = engine.store.events(projects=[candidate.PROJECT])['items']
        with engine.database() as db:
            pages = db.execute('SELECT count(*) FROM pages').fetchone()[0]
        self.assertEqual(engine.step(mode='live')['state'], 'unchanged')
        self.assertEqual(engine.store.events(projects=[candidate.PROJECT])['items'], before)
        with engine.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pages').fetchone()[0], pages)
        with engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)

    def test_old_completed_observation_cannot_restore_a_later_withheld_head(self):
        engine, reader = self.frontier({('a', None): ([item(1)], False, None)})
        engine.step()
        old = engine.step(mode='live')['job']
        reader.pages[('a', None)] = ([item(1, visible=False)], False, None)
        engine.step(mode='live')
        with engine.database() as db:
            before = db.execute('SELECT frontier FROM rooms').fetchone()[0]
        with self.assertRaisesRegex(candidate.SourceBlocked, 'messenger_prepared_epoch_stale'):
            engine._publish(old, cancelled=lambda: False, after_publish=lambda: None)
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])
        with engine.database() as db:
            self.assertEqual(db.execute('SELECT frontier FROM rooms').fetchone()[0], before)

    def test_competing_prepared_observation_does_not_overwrite_committed_heads(self):
        engine, reader = self.frontier({('a', None): ([item(1)], False, None)})
        with engine.database() as db:
            room = db.execute('SELECT * FROM rooms').fetchone()
        first = engine._prepare(room, 'backfill', reader.page('a', None))
        reader.pages[('a', None)] = ([item(1, '마감 일정이 다음 달로 변경되었습니다.')], False, None)
        competing = engine._prepare(room, 'backfill', reader.page('a', None))
        engine._publish(first, cancelled=lambda: False, after_publish=lambda: None)
        with self.assertRaisesRegex(candidate.SourceBlocked, 'messenger_prepared_observation_conflict'):
            engine._publish(competing, cancelled=lambda: False, after_publish=lambda: None)
        self.assertEqual(len(engine.store.search('회의', projects=[candidate.PROJECT])), 1)
        self.assertEqual(engine.store.search('마감', projects=[candidate.PROJECT]), [])

    def test_legacy_prepared_page_migrates_without_changing_its_identity(self):
        engine, reader = self.frontier({('a', None): ([item(1)], False, None)})
        with engine.database() as db:
            room = db.execute('SELECT * FROM rooms').fetchone()
        job = engine._prepare(room, 'backfill', reader.page('a', None))
        with engine.database() as db:
            row = db.execute('SELECT * FROM pages WHERE id=?', (job,)).fetchone()
            payload, next_state = json.loads(row['payload']), json.loads(row['next_frontier'])
            payload.pop('source_epoch'); next_state.pop('source_epoch')
            legacy = hashlib.sha256(encoded([payload, next_state])).hexdigest()
            db.execute('UPDATE pages SET id=?,payload=?,next_frontier=? WHERE id=?',
                       (legacy, encoded(payload).decode(), encoded(next_state).decode(), job))
            db.execute('DROP TABLE head_fingerprints')
        resumed = candidate.Frontier(self.root, 'kakaotalk', 'owner', reader)
        self.assertEqual(resumed.step()['job'], legacy)
        self.assertEqual(resumed.status()['prepared_pages'], 0)
        resumed.step(mode='live')
        reader.pages[('a', None)] = ([item(1, visible=False)], False, None)
        resumed.step(mode='live')
        reader.pages[('a', None)] = ([item(1)], False, None)
        resumed.step(mode='live')
        self.assertEqual(len(resumed.store.search('회의', projects=[candidate.PROJECT])), 1)
        with resumed.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)

    def test_legacy_committed_heads_gain_fingerprints_without_rewriting_versions(self):
        engine, reader = self.frontier({('a', None): ([item(1)], False, None)})
        engine.step()
        with engine.database() as db:
            state = json.loads(db.execute('SELECT frontier FROM rooms').fetchone()[0])
            state.pop('source_epoch')
            db.execute('UPDATE rooms SET frontier=?', (encoded(state).decode(),))
            db.execute('DROP TABLE head_fingerprints')
        resumed = candidate.Frontier(self.root, 'kakaotalk', 'owner', reader)
        resumed.step(mode='live')
        self.assertEqual(resumed.step(mode='live')['state'], 'unchanged')
        with resumed.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)

    def test_prepared_revalidation_rejects_changed_original_metadata(self):
        original = {**item(1), 'original': {'senderName': 'first'}}
        engine, reader = self.frontier({('a', None): ([original], False, None)})
        with engine.database() as db:
            room = db.execute('SELECT * FROM rooms').fetchone()
        engine._prepare(room, 'backfill', reader.page('a', None))
        reader.pages[('a', None)] = ([{**original, 'original': {'senderName': 'changed'}}], False, None)
        self.assertEqual(engine.step()['reason'], 'messenger_prepared_source_changed')
        self.assertEqual(engine.store.search('회의', projects=[candidate.PROJECT]), [])

    def test_cancel_during_unchanged_page_read_keeps_its_checkpoint(self):
        engine, reader = self.frontier({('a', None): ([item(1)], True, 'older'),
                                       ('a', 'older'): ([item(2)], False, None)})
        engine.step(); engine.step()
        engine.step(mode='reconcile'); engine.step(mode='reconcile')
        with engine.database() as db:
            before = db.execute('SELECT frontier FROM rooms').fetchone()[0]
        events = engine.store.events(projects=[candidate.PROJECT])['items']
        stopped = False
        page = reader.page
        def cancel_after_read(*args):
            nonlocal stopped
            result = page(*args)
            stopped = True
            return result
        with patch.object(reader, 'page', side_effect=cancel_after_read):
            with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
                engine.step(mode='reconcile', cancelled=lambda: stopped)
        with engine.database() as db:
            self.assertEqual(db.execute('SELECT frontier FROM rooms').fetchone()[0], before)
        self.assertEqual(engine.store.events(projects=[candidate.PROJECT])['items'], events)

    def test_pending_duplicate_origin_recovery_orders_shared_content_but_not_other_rooms(self):
        body = '회의 자료를 다음 주 화요일까지 검토하기로 결정했습니다.'
        for migrate_claims in (False, True):
            with self.subTest(legacy_claims=migrate_claims):
                root = candidate.create_root(self.root / ('cross-room-' + str(migrate_claims)))
                reader = Reader({('a',None):([item(1,body)],False,None),
                                 ('b',None):([item(2,body)],False,None),
                                 ('c',None):([item(3,'네')],False,None)})
                engine = candidate.Frontier(root, 'kakaotalk', 'owner', reader)
                engine.seed({'account_id':'owner','complete':True,
                             'rooms':[{'id':room,'title':room} for room in ('a','b','c')]})
                engine.step(); engine.step(); engine.step()
                reader.pages[('a',None)] = ([item(1,visible=False)],False,None)
                engine.step(mode='live'); engine.step(mode='live'); engine.step(mode='live')
                reader.pages[('a',None)] = ([item(1,body)],False,None)
                with self.assertRaises(SystemExit):
                    engine.step(mode='live', after_publish=lambda: (_ for _ in ()).throw(SystemExit(73)))
                if migrate_claims:
                    with engine.database() as db:
                        db.execute('DROP TABLE IF EXISTS page_content_claims')
                    claim_page = candidate.Frontier._claim_page_content
                    def interrupt_migration(*args):
                        claim_page(*args)
                        raise RuntimeError('controlled_claim_migration_interruption')
                    with patch.object(candidate.Frontier, '_claim_page_content', side_effect=interrupt_migration, autospec=True):
                        with self.assertRaisesRegex(RuntimeError, 'controlled_claim_migration_interruption'):
                            candidate.Frontier(root, 'kakaotalk', 'owner', reader)
                    with engine.database() as db:
                        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='page_content_claims'").fetchone())
                engine = candidate.Frontier(root, 'kakaotalk', 'owner', reader)
                denied = {'a'}
                def authorize(room):
                    if room in denied:
                        raise candidate.SourceBlocked('controlled_room_authorization_expired')
                with patch.object(reader, 'authorize', side_effect=authorize):
                    self.assertEqual(engine.step(mode='live')['state'], 'blocked')
                    reader.pages[('b',None)] = ([item(2,visible=False)],False,None)
                    blocked = engine.step(mode='live')
                    self.assertEqual(blocked['state'], 'blocked')
                    self.assertEqual(blocked['reason'], 'messenger_shared_content_pending')
                    reader.pages[('c',None)] = ([item(3,'프로젝트 검토 일정은 금요일 오후 4시로 확정되었습니다.')],False,None)
                    independent = engine.step(mode='live')
                    self.assertEqual((independent['room_id'], independent['state']), ('c','published'))
                    denied.clear()
                    with engine.database() as db:
                        db.execute('UPDATE rooms SET retry_at=0')
                    self.assertEqual(engine.step(mode='live')['room_id'], 'a')
                    self.assertEqual(engine.step(mode='live')['room_id'], 'b')
                self.assertEqual(len(engine.store.search('화요일', projects=[candidate.PROJECT])), 1)
                self.assertEqual(len(engine.store.search('프로젝트', projects=[candidate.PROJECT])), 1)
                self.assertEqual(engine.status()['prepared_pages'], 0)
                with engine.store.database() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 2)

    def test_empty_room_remains_live_and_collects_its_first_arrival(self):
        engine, reader = self.frontier({('a', None): ([], False, None)})
        engine.step()
        reader.pages[('a', None)] = ([item(1)], False, None)
        self.assertEqual(engine.step(mode='live')['state'], 'published')
        self.assertEqual(len(engine.store.search('회의', projects=[candidate.PROJECT])), 1)

    def test_pending_expired_context_does_not_block_other_rooms(self):
        engine, reader = self.frontier({('a', None): ([item(1)], False, None),
                                       ('b', None): ([item(2)], False, None)}, ('a', 'b'))
        with self.assertRaises(SystemExit):
            engine.step(after_publish=lambda: (_ for _ in ()).throw(SystemExit(73)))
        reader.blocked = True
        self.assertEqual(engine.step()['state'], 'blocked')
        reader.blocked = False
        self.assertEqual(engine.step()['room_id'], 'b')

    def test_withheld_page_never_archives_unexpected_body_fields(self):
        secret = {**item(1, visible=False), 'original': {'body': 'must not be cached'}}
        engine, _ = self.frontier({('a', None): ([secret], False, None)})
        with self.assertRaisesRegex(ValueError, 'messenger_withheld_fields_invalid'):
            engine.step()
        self.assertEqual(engine.status()['prepared_pages'], 0)

    def test_blocked_source_does_not_advance_and_other_rooms_can_continue(self):
        engine, reader = self.frontier({('b', None): ([item(2)], False, None)}, ('a', 'b'))
        reader.blocked = True
        self.assertEqual(engine.step()['state'], 'blocked')
        reader.blocked = False
        self.assertEqual(engine.step()['room_id'], 'b')
        self.assertEqual(engine.status()['blocked_rooms'], 1)

    def test_malformed_page_cannot_advance_frontier_or_publish(self):
        engine, reader = self.frontier({('a', None): ([item(1), item(1)], False, None)})
        with self.assertRaisesRegex(ValueError, 'messenger_page_duplicate'):
            engine.step()
        self.assertEqual(engine.status()['backfilled_rooms'], 0)
        self.assertEqual(engine.store.search('자료', projects=[candidate.PROJECT]), [])

    def test_current_account_change_stops_publication(self):
        engine, reader = self.frontier({('a', None): ([item(1)], False, None)})
        reader.account = 'other'
        with self.assertRaisesRegex(ValueError, 'messenger_account_changed'):
            engine.step()
        with self.assertRaisesRegex(ValueError, 'messenger_account_changed'):
            candidate.Frontier(self.root, 'kakaotalk', 'other', reader)

    def test_denied_target_stops_before_body_access(self):
        engine, reader = self.frontier({('a', None): ([item(1)], False, None)})
        with engine.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (engine.target,))
        with self.assertRaisesRegex(candidate.SourceBlocked, 'messenger_target_disabled_or_denied'):
            engine.step()
        self.assertEqual(reader.reads, [])

    def test_production_or_symlink_root_is_rejected(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            candidate.Frontier(self.root.parent, 'kakaotalk', 'owner', Reader({}))
        link = self.root.parent / 'link'
        link.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'messenger_evaluation_root_required'):
            candidate.Frontier(link, 'kakaotalk', 'owner', Reader({}))


class CanonicalAdapterTests(unittest.TestCase):
    def test_kakao_recovery_uses_exact_ids_instead_of_a_shifted_latest_window(self):
        calls = []
        def snapshot(**options):
            calls.append(options)
            return {'status':'ok','db_readonly_verified':True,'schema_decryption_verified':True,
                'chat':{'chatId':'a'},'messages_truncated':False,'next_cursor':None,
                'messages':[{'chatId':'a','logId':options['message_id'],'sentAt':1,'message':'회의 자료입니다.'}]}
        reader = candidate.KakaoReader(SimpleNamespace(snapshot=snapshot))
        payload = {'room':'a','before':'old-page-cursor','items':[
            {'id':str(n),'visible':True,'body':'회의 자료입니다.','sent_at':1,
             'original':{'chatId':'a','logId':str(n),'sentAt':1,'message':'회의 자료입니다.'}} for n in (1,2)]}
        with patch.object(reader,'identity',return_value='owner'):
            reader.verify_prepared(payload)
        self.assertEqual([c['message_id'] for c in calls], ['1','2'])
        self.assertTrue(all(c['limit']==1 and c['cursor'] is None for c in calls))

    def test_telegram_recovery_missing_window_blocks_without_inferred_deletion(self):
        calls = []
        def invoke(args):
            calls.append(args)
            if args == ['probe']:
                return {'status':'ready_readonly','read_only':True,'owner_id':'owner'}
            return {'status':'ok','owner_id':'owner','peer_id':'a','content_returned':True,
                'messages':[],'more':True,'next_cursor':'following'}
        reader = candidate.TelegramReader(invoke,{'a':{'since':'2026-10-20','until':'2026-10-21'}},lambda *args:None)
        with self.assertRaisesRegex(candidate.SourceBlocked,'messenger_prepared_window_incomplete'):
            reader.verify_prepared({'room':'a','before':'saved','items':[item(1)]})
        requests = [c for c in calls if c[0]=='messages']
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0][-2:],['--cursor','saved'])

    def test_kakao_withheld_original_is_never_copied_into_a_prepared_page(self):
        response = {'status':'ok','db_readonly_verified':True,'schema_decryption_verified':True,
            'chat':{'chatId':'a'},'messages_truncated':False,'next_cursor':None,
            'messages':[{'chatId':'a','logId':'1','sentAt':1,'message_state':'deleted','message':'placeholder'}]}
        reader = candidate.KakaoReader(SimpleNamespace(snapshot=lambda **_:response))
        with patch.object(reader,'identity',return_value='owner'):
            item = reader.page('a',None)['items'][0]
        self.assertFalse(item['visible']); self.assertIsNone(item['body']); self.assertNotIn('original',item)

    def test_kakao_preserves_specific_canonical_failure_and_never_substitutes_a_reader(self):
        reader = candidate.KakaoReader(SimpleNamespace(snapshot=lambda **_:{'status':'unavailable','reason':'current_account_identity_unavailable'}))
        with patch.object(reader,'identity',return_value='owner'), self.assertRaisesRegex(candidate.SourceBlocked,'^kakao_reader_current_account_identity_unavailable$'):
            reader.page('a',None)

    def test_telegram_missing_context_makes_zero_body_or_metadata_requests(self):
        calls=[]
        reader=candidate.TelegramReader(lambda args:calls.append(args),{},lambda *args:None)
        with self.assertRaisesRegex(candidate.SourceBlocked,'telegram_context_consent_required'):
            reader.page('a',None)
        self.assertEqual(calls,[])

    def test_telegram_partial_byte_budget_does_not_become_a_visibility_withdrawal(self):
        calls=[]
        def invoke(args):
            calls.append(args)
            if args[0]=='probe':return {'status':'ready_readonly','read_only':True,'owner_id':'owner'}
            return {'status':'partial','owner_id':'owner','peer_id':'a','content_returned':True,
                'messages':[{'id':'1','peer_id':'a','timestamp':1,'body_withheld':'output_character_budget'}]}
        reader=candidate.TelegramReader(invoke,{'a':{'since':'2026-10-20','until':'2026-10-21'}},lambda *args:None)
        with self.assertRaisesRegex(candidate.SourceBlocked,'telegram_single_message_output_budget'):
            reader.page('a',None)
        body_calls=[args for args in calls if args[0]=='messages']
        self.assertEqual([args[-1] for args in body_calls],['50','1'])

    def test_short_contexts_stay_reversible_and_factual_numbers_are_preserved(self):
        for text in ('네','3층','2026-10-21','1\n/\n2\n6\n1\n3','https://example.com/document'):
            with self.subTest(text=text):
                self.assertEqual(classify(text).state,'context')
                self.assertEqual(classify(text).text,text)
        for text in ('회의는 내일 오후 3시에 시작합니다.','가격은 72,000원으로 변경되었습니다.'):
            with self.subTest(text=text):
                self.assertEqual(classify(text).state,'candidate')
                self.assertEqual(classify(text).text,text)


class MessengerStoreTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temp.cleanup)
        # These checks isolate store transactions with controlled authority.
        self.store = CollectionStore(Path(temp.name), source_access=lambda *args: True)
        self.target = self.store.register(platform='kakaotalk', original_id='owner', kind='local-db',
            label='local messages', projects=['one'])
        self.record = {'platform': 'kakaotalk', 'original_id': 'room:message',
                       'label': 'withdrawable', 'text': 'withdrawable phrase', 'raw': {'body': 'original'}}
        self.key = {k: self.record[k] for k in ('platform', 'original_id')}

    def test_both_platforms_support_graph_history_and_activity_filters(self):
        for platform in ('kakaotalk', 'telegram'):
            target = self.store.register(platform=platform, original_id='owner', kind='local-db', label='db', projects=['one'])
            checkpoint = self.store.activity_page(projects=['one'], platform=platform)
            self.store.ingest(target, [{**self.record, 'platform': platform}])
            self.assertTrue(self.store.graph_page(projects=['one'], platform=platform)['ok'])
            self.assertTrue(self.store.recent_events(projects=['one'], platform=platform)['ok'])
            page = self.store.activity_page(projects=['one'], platform=platform,
                after=checkpoint['cursor'], stream_id=checkpoint['stream_id'])
            self.assertEqual(len(page['items']), 1)

    def test_withdrawal_and_new_text_publish_together_and_duplicate_is_quiet(self):
        self.store.ingest(self.target, [self.record])
        result = self.store.ingest(self.target, [{**self.record, 'original_id': 'new', 'label': 'fresh', 'text': 'fresh'}],
            retractions=[self.key], run_id='page-2', cursor={'page': 2})
        self.assertEqual(result['removed'], 1)
        self.assertEqual(self.store.search('withdrawable', projects=['one']), [])
        self.assertEqual(len(self.store.search('fresh', projects=['one'])), 1)
        before = self.store.events(projects=['one'])['items']
        self.store.ingest(self.target, [], retractions=[self.key], run_id='page-2')
        self.assertEqual(self.store.events(projects=['one'])['items'], before)
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 2)

    def test_conflicting_visible_record_and_retraction_are_rejected_before_any_write(self):
        self.store.ingest(self.target, [self.record])
        with self.assertRaisesRegex(ValueError, 'collection_retraction_conflicts_with_record'):
            self.store.ingest(self.target, [self.record], retractions=[self.key])
        self.assertEqual(len(self.store.search('withdrawable', projects=['one'])), 1)

    def test_cancellation_rolls_back_text_and_withdrawal_and_cursor(self):
        self.store.ingest(self.target, [self.record], cursor={'page': 1})
        @contextmanager
        def cancelled_at_commit():
            raise RuntimeError('collection_cancelled')
            yield
        with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
            self.store.ingest(self.target, [{**self.record, 'original_id': 'new', 'text': 'uncommitted'}],
                retractions=[self.key], publication_guard=cancelled_at_commit, cursor={'page': 2})
        self.assertEqual(json.loads(self.store.target(self.target)['cursor']), {'page': 1})
        self.assertEqual(len(self.store.search('withdrawable', projects=['one'])), 1)
        self.assertEqual(self.store.search('uncommitted', projects=['one']), [])

    def test_visibility_restoration_emits_a_real_change_without_rewriting_version(self):
        self.store.ingest(self.target, [self.record])
        self.store.ingest(self.target, [], retractions=[self.key])
        checkpoint = self.store.activity_page(projects=['one'])
        result = self.store.ingest(self.target, [self.record])
        self.assertEqual(result['revised'], 1)
        page = self.store.activity_page(projects=['one'], after=checkpoint['cursor'], stream_id=checkpoint['stream_id'])
        self.assertEqual(page['items'][0]['kind'], 'revised')
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)

    def test_target_withdrawal_does_not_revoke_another_available_membership(self):
        other = self.store.register(platform='kakaotalk', original_id='second-scope', kind='local-db', label='second', projects=['two'])
        self.store.ingest(self.target, [self.record]); self.store.ingest(other, [self.record])
        self.store.ingest(self.target, [], retractions=[self.key])
        self.assertEqual(self.store.search('withdrawable', projects=['one']), [])
        self.assertEqual(len(self.store.search('withdrawable', projects=['two'])), 1)

    def test_unknown_platform_and_denied_retraction_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'collection_platform_invalid'):
            self.store.register(platform='unknown', original_id='x', kind='local-db', label='x', projects=['one'])
        denied = self.store.register(platform='telegram', original_id='denied', kind='local-db', label='d', projects=['one'], permission='denied')
        with self.assertRaisesRegex(ValueError, 'collection_source_scope_denied'):
            self.store.ingest(denied, [], retractions=[self.key])
