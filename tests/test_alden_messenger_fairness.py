"""Mixed history/live/reconcile traversal must revisit every eligible room."""
import unittest
import json
from unittest.mock import patch
from tests import test_alden_messenger_frontier as fixtures

candidate = fixtures.candidate


class MessengerFairnessTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FrontierTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.root = self.case.root

    def busy_rooms(self):
        pages = {}
        for index, room in enumerate(('a', 'b', 'c')):
            pages[(room, None)] = ([fixtures.item(100 + index, '회의 일정과 계약 자료를 검토하는 방 ' + room)], True, 'older-1')
            for page in range(1, 8):
                pages[(room, 'older-' + str(page))] = (
                    [fixtures.item(1000 + index*100 + page, '과거 회의 자료와 배송 정책 변경 기록 ' + room + str(page))],
                    True, 'older-' + str(page+1))
        return self.case.frontier(pages, rooms=('a','b','c'))

    def test_interleaving_modes_cannot_partition_rooms_into_permanently_unchecked_groups(self):
        engine, _ = self.busy_rooms()
        for _ in range(3):
            engine.step(mode='backfill')
        seen = {mode:[] for mode in ('live','backfill','reconcile')}
        for _ in range(3):
            for mode in seen:
                result = engine.step(mode=mode)
                seen[mode].append(result.get('room_id'))
        self.assertEqual({mode:set(rooms) for mode,rooms in seen.items()},
                         {mode:{'a','b','c'} for mode in seen})

    def test_fixed_and_rewound_wall_clock_cannot_reselect_the_same_room_forever(self):
        engine, _ = self.busy_rooms()
        for _ in range(3): engine.step()
        for clock in (100, 50):
            with patch.object(candidate.time, 'time', return_value=clock):
                seen = [engine.step(mode='live')['room_id'] for _ in range(3)]
            self.assertEqual(set(seen), {'a','b','c'})

    def test_mode_order_survives_reopen_without_moving_other_mode_cursors(self):
        engine, reader = self.busy_rooms()
        for _ in range(3): engine.step()
        with engine.database() as db:
            before = {r['id']:json.loads(r['frontier'])['back_cursor'] for r in db.execute('SELECT * FROM rooms')}
        self.assertEqual(engine.step(mode='live')['room_id'], 'a')
        reopened = candidate.Frontier(self.root, 'kakaotalk', 'owner', reader)
        self.assertEqual(reopened.step(mode='live')['room_id'], 'b')
        self.assertEqual(reopened.step(mode='reconcile')['room_id'], 'a')
        with engine.database() as db:
            self.assertEqual({r['id']:json.loads(r['frontier'])['back_cursor'] for r in db.execute('SELECT * FROM rooms')}, before)

    def test_unprimed_room_is_not_read_live_and_empty_primed_room_remains_eligible(self):
        engine, reader = self.case.frontier({('a',None):([],False,None),
            ('b',None):([fixtures.item(1)],False,None)}, rooms=('a','b'))
        self.assertEqual(engine.step(mode='live')['state'], 'idle')
        self.assertEqual(reader.reads, [])
        self.assertEqual(engine.step()['room_id'], 'a')
        self.assertEqual(engine.step(mode='live')['room_id'], 'a')
        self.assertEqual(engine.step()['room_id'], 'b')
        self.assertEqual(engine.step(mode='live')['room_id'], 'b')

    def test_blocked_room_backoff_preserves_other_room_progress(self):
        engine, reader = self.busy_rooms()
        authorize = reader.authorize
        def blocked(room):
            if room == 'a':raise candidate.SourceBlocked('fixture_room_denied')
            return authorize(room)
        with patch.object(reader, 'authorize', side_effect=blocked):
            self.assertEqual(engine.step()['state'], 'blocked')
            self.assertEqual([engine.step()['room_id'] for _ in range(2)], ['b','c'])
            self.assertEqual({engine.step(mode='live')['room_id'] for _ in range(2)}, {'b','c'})
        with engine.database() as db:db.execute("UPDATE rooms SET retry_at=0 WHERE id='a'")
        self.assertEqual(engine.step()['room_id'], 'a')

    def test_pending_commit_is_charged_to_its_actual_mode_during_cross_mode_recovery(self):
        engine, _ = self.busy_rooms()
        def crash():raise RuntimeError('after durable store commit')
        with self.assertRaisesRegex(RuntimeError, 'durable store commit'):engine.step(after_publish=crash)
        events = fixtures.journal_rows(engine.store)
        self.assertEqual(engine.step(mode='live')['room_id'], 'a')
        self.assertEqual(fixtures.journal_rows(engine.store), events)
        with engine.database() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT room,mode,position FROM room_mode_checks')], [('a','backfill',1)])
        self.assertEqual(engine.step()['room_id'], 'b')

    def test_new_fairness_metadata_preserves_legacy_pages_heads_cursors_and_journal(self):
        engine, reader = self.busy_rooms()
        for _ in range(3):engine.step()
        with engine.database() as db:
            before = {name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
                      for name in ('rooms','pages','message_heads','head_fingerprints')}
            db.execute('DROP TABLE room_mode_checks')
        events = fixtures.journal_rows(engine.store)
        reopened = candidate.Frontier(self.root, 'kakaotalk', 'owner', reader)
        with reopened.database() as db:
            self.assertEqual({name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')] for name in before}, before)
        self.assertEqual(fixtures.journal_rows(engine.store), events)
        self.assertEqual({reopened.step(mode='live')['room_id'] for _ in range(3)}, {'a','b','c'})

    def test_two_complete_sweeps_cover_more_than_the_existing_200_target_scan(self):
        rooms = tuple(str(n).zfill(4) for n in range(707))
        engine, _ = self.case.frontier({(r,None):([],False,None) for r in rooms}, rooms=rooms)
        self.assertEqual({engine.step()['room_id'] for _ in rooms}, set(rooms))
        self.assertEqual({engine.step(mode='live')['room_id'] for _ in rooms}, set(rooms))
        self.assertEqual(engine.store.graph(projects=[candidate.PROJECT])['nodes'], [])


if __name__ == '__main__':
    unittest.main()
