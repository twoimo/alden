"""Isolated, real SQLite receipts for Alden's typed assistant input."""

from __future__ import annotations

import concurrent.futures
import multiprocessing
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import alden_chat_turns as turns
import alden_history as history


def _child_reserve(args):
    directory, request_id, conversation_id, text = args
    return turns.reserve_turn(Path(directory), request_id, conversation_id, text)


def _image(name='sample.png', digest='a' * 64):
    return {'filename': name, 'mime': 'image/png', 'sha256': digest, 'size': 2048}


class TypedTurnLedgerTests(unittest.TestCase):
    def test_duplicate_initial_id_is_durable_and_completed_replay_is_original(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            request_id = 'a' * 32
            first = turns.reserve_turn(root, request_id, None, '\t첫 질문\n',
                                       attachments=[_image()])
            self.assertEqual(first['state'], 'running')
            self.assertEqual((first['turn_id'], first['context_version']), (1, 1))
            self.assertEqual(first['history'], [])
            self.assertEqual(first['history_attachments'], [])
            self.assertEqual(first['source'], 'text')
            self.assertEqual(len(first['conversation_id']), 32)
            self.assertEqual(turns.reserve_turn(root, request_id, None, '\t첫 질문\n',
                                                attachments=[_image()]), first)
            status = turns.get_request(root, request_id)
            self.assertEqual(status['state'], 'running')
            self.assertNotIn('owner_id', status)

            saved = turns.complete_turn(root, first, '첫 답변', provenance=[
                {'note_id': 'local-1', 'note_hash': 'b' * 64, 'credential': 'DO_NOT_STORE'}])
            self.assertEqual(saved['state'], 'completed')
            self.assertEqual(saved['reply'], '첫 답변')
            self.assertEqual(saved['answer_context_version'], 2)
            replay = turns.complete_turn(root, first, '바뀐 답변', provenance=[
                {'note_id': 'substitute'}])
            self.assertEqual(replay, saved)
            self.assertEqual(turns.reserve_turn(root, request_id, None, '\t첫 질문\n',
                                                attachments=[_image()]), saved)
            self.assertEqual(turns.get_request(root, request_id)['reply'], '첫 답변')
            page = history.read(root, Path('/unused'), 'voice-history-messages',
                                chat_id=first['conversation_id'])
            self.assertEqual([(item['role'], item['content']) for item in page['items']], [
                ('user', '\t첫 질문\n'), ('assistant', '첫 답변')])
            self.assertEqual(page['items'][1]['retrieval_provenance'],
                             [{'note_id': 'local-1', 'note_hash': 'b' * 64}])
            with history.database(root) as db:
                row = db.execute('SELECT source FROM voice_sessions WHERE id=?',
                                 (first['conversation_id'],)).fetchone()
                self.assertEqual(row['source'], 'text')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM alden_chat_requests').fetchone()[0], 1)
            self.assertEqual((root / 'alden-history.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_followup_uses_selected_voice_history_and_increments_counters(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            conversation_id = '1' * 32
            history.record_voice(root, conversation_id, 3, 'user', '원래 음성', 7,
                                 source='microphone')
            history.record_voice(root, conversation_id, 3, 'assistant', '음성 답변', 8,
                                 provenance=[{'source_id': 'voice-proof'}])
            ticket = turns.reserve_turn(root, '2' * 32, conversation_id, '텍스트 질문')
            self.assertEqual((ticket['turn_id'], ticket['context_version']), (4, 9))
            self.assertEqual(ticket['history'], [
                {'role': 'user', 'content': '원래 음성'},
                {'role': 'assistant', 'content': '음성 답변'},
            ])
            turns.complete_turn(root, ticket, '텍스트 답변')
            followup = turns.reserve_turn(root, '3' * 32, conversation_id, '후속 질문')
            self.assertEqual((followup['turn_id'], followup['context_version']), (5, 11))
            self.assertEqual([row['content'] for row in followup['history']],
                             ['원래 음성', '음성 답변', '텍스트 질문', '텍스트 답변'])
            page = history.read(root, Path('/unused'), 'voice-history-messages',
                                chat_id=conversation_id)
            self.assertEqual(page['items'][1]['retrieval_provenance'],
                             [{'source_id': 'voice-proof'}])
            with history.database(root) as db:
                self.assertEqual(db.execute('SELECT source FROM voice_sessions WHERE id=?',
                                            (conversation_id,)).fetchone()[0], 'microphone')

    def test_history_last_eight_confirmed_records_truncate_each_to_600(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            conversation_id = '4' * 32
            for turn in range(1, 9):
                history.record_voice(root, conversation_id, turn, 'user',
                                     'U' * 650 + str(turn), 2 * turn - 1)
                history.record_voice(root, conversation_id, turn, 'assistant',
                                     'A' * 700 + str(turn), 2 * turn)
            receipt = turns.reserve_turn(root, '5' * 32, conversation_id, '현재 질문')
            self.assertEqual(len(receipt['history']), 8)
            self.assertEqual([entry['role'] for entry in receipt['history']],
                             ['user', 'assistant'] * 4)
            self.assertEqual([len(entry['content']) for entry in receipt['history']], [600] * 8)
            self.assertTrue(receipt['history'][0]['content'].startswith('U'))
            self.assertEqual(receipt['turn_id'], 9)
            self.assertNotIn('현재 질문', str(receipt['history']))

    def test_supersession_keeps_confirmed_input_but_rejects_old_completion_and_images(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = turns.reserve_turn(root, 'a' * 32, None, '오래 걸리는 질문',
                                     attachments=[_image('old.png')])
            current = turns.reserve_turn(root, 'b' * 32, old['conversation_id'], '새 질문')
            self.assertEqual(turns.get_request(root, old['request_id'])['state'], 'superseded')
            self.assertEqual(current['turn_id'], 2)
            self.assertEqual(current['context_version'], 2)
            self.assertEqual(current['history'], [{'role': 'user', 'content': '오래 걸리는 질문'}])
            with self.assertRaisesRegex(turns.StaleTurn, 'turn_superseded'):
                turns.complete_turn(root, old, '늦은 답변')
            turns.complete_turn(root, current, '새 답변')
            next_turn = turns.reserve_turn(root, 'c' * 32, old['conversation_id'], '확인')
            self.assertEqual(next_turn['history_attachments'], [])
            page = history.read(root, Path('/unused'), 'voice-history-messages',
                                chat_id=old['conversation_id'])
            self.assertEqual([(item['turn_id'], item['role']) for item in page['items']],
                             [(1, 'user'), (2, 'user'), (2, 'assistant'), (3, 'user')])

    def test_cancel_retract_and_cross_conversation_attachments(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cancelled = turns.reserve_turn(root, '1' * 32, None, '취소 요청',
                                           attachments=[_image('cancelled.png')])
            self.assertEqual(turns.cancel_turn(root, cancelled, reason='retracted')['state'], 'cancelled')
            with self.assertRaises(turns.StaleTurn):
                turns.complete_turn(root, cancelled, '취소된 답변')
            complete = turns.reserve_turn(root, '2' * 32, cancelled['conversation_id'],
                                          '사진으로 설명', attachments=[_image('completed.png')])
            turns.complete_turn(root, complete, '사진 답변')
            next_turn = turns.reserve_turn(root, '3' * 32, complete['conversation_id'], '후속')
            self.assertEqual(next_turn['history_attachments'], [
                {**_image('completed.png'), 'source_request_id': complete['request_id'],
                 'source_turn_id': complete['turn_id']}])
            self.assertEqual(turns.cancel_turn(root, complete)['state'], 'completed')
            retracted = turns.cancel_turn(root, complete, reason='retracted')
            self.assertEqual(retracted['state'], 'superseded')
            self.assertEqual(retracted['reason'], 'retracted')
            self.assertNotIn('reply', retracted)
            replay = turns.complete_turn(root, complete, '대체 답변')
            self.assertEqual(replay, retracted)
            self.assertNotIn('reply', replay)
            duplicate = turns.reserve_turn(root, complete['request_id'],
                                           cancelled['conversation_id'], '사진으로 설명',
                                           attachments=[_image('completed.png')])
            self.assertEqual(duplicate, retracted)
            self.assertNotIn('reply', duplicate)
            after_retraction = turns.reserve_turn(root, '4' * 32, complete['conversation_id'], '다음 질문')
            self.assertEqual(after_retraction['history_attachments'], [])
            self.assertNotIn('사진 답변', str(after_retraction['history']))
            self.assertNotIn('사진으로 설명', str(after_retraction['history']))
            other = turns.reserve_turn(root, '5' * 32, None, '다른 대화')
            self.assertEqual(other['history_attachments'], [])
            readback = turns.get_request(root, complete['request_id'])
            self.assertEqual(readback['state'], 'superseded')
            self.assertNotIn('reply', readback)
            self.assertNotIn('answer_context_version', readback)

    def test_public_running_failed_and_cancelled_mapping_preserves_sql_cas(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            failed = turns.reserve_turn(root, '0' * 32, None, '실패 예정 요청')
            self.assertEqual(failed['state'], 'running')
            self.assertEqual(turns.get_request(root, failed['request_id'])['state'], 'running')
            self.assertEqual(turns.reserve_turn(root, failed['request_id'], None, '실패 예정 요청'), failed)
            with history.database(root) as db:
                self.assertEqual(db.execute('SELECT state FROM alden_chat_requests WHERE request_id=?',
                                            (failed['request_id'],)).fetchone()[0], 'pending')
            terminal = turns.cancel_turn(root, failed, reason='failed')
            self.assertEqual((terminal['state'], terminal['reason']), ('failed', 'failed'))
            self.assertEqual(turns.get_request(root, failed['request_id'])['state'], 'failed')
            self.assertEqual(turns.reserve_turn(root, failed['request_id'], None, '실패 예정 요청'), terminal)
            self.assertEqual(turns.cancel_turn(root, failed)['state'], 'failed')
            with history.database(root) as db:
                self.assertEqual(db.execute('SELECT state FROM alden_chat_requests WHERE request_id=?',
                                            (failed['request_id'],)).fetchone()[0], 'cancelled')
            with self.assertRaises(turns.StaleTurn):
                turns.complete_turn(root, failed, '늦은 답변')
            cancelled = turns.reserve_turn(root, '1' * 32, failed['conversation_id'], '취소 요청')
            self.assertEqual(turns.cancel_turn(root, cancelled)['state'], 'cancelled')
            self.assertEqual(turns.get_request(root, cancelled['request_id'])['state'], 'cancelled')
            running = turns.reserve_turn(root, '2' * 32, failed['conversation_id'], '진행 요청')
            turns.reserve_turn(root, '3' * 32, failed['conversation_id'], '최신 요청')
            self.assertEqual(turns.get_request(root, running['request_id'])['state'], 'superseded')
            self.assertEqual(turns.get_request(root, '3' * 32)['state'], 'running')

    def test_history_attachments_latest_completed_typed_only_up_to_four_images(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batch = [_image(f'one-{index}.png', str(index) * 64) for index in range(3)]
            batch.append({'filename': 'notes.pdf', 'mime': 'application/pdf',
                          'sha256': 'e' * 64, 'size': 10})
            first = turns.reserve_turn(root, '5' * 32, None, '첫 파일', attachments=batch)
            turns.complete_turn(root, first, '완료1')
            second = turns.reserve_turn(root, '6' * 32, first['conversation_id'], '두번째 파일',
                                        attachments=[_image('two.png', 'f' * 64),
                                                     _image('three.png', 'a' * 64)])
            turns.complete_turn(root, second, '완료2')
            last = turns.reserve_turn(root, '7' * 32, first['conversation_id'], '파일 이어서')
            sources = last['history_attachments']
            self.assertEqual(len(sources), 4)
            self.assertEqual([row['filename'] for row in sources],
                             ['two.png', 'three.png', 'one-0.png', 'one-1.png'])
            self.assertEqual([row['source_request_id'] for row in sources],
                             [second['request_id']] * 2 + [first['request_id']] * 2)
            self.assertFalse(any('/' in row['filename'] for row in sources))
            other = turns.reserve_turn(root, '8' * 32, None, '분리 대화')
            self.assertEqual(other['history_attachments'], [])

    def test_changed_payload_or_requested_target_and_stolen_ticket_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = turns.reserve_turn(root, '9' * 32, None, '같은 질문')
            for target, text, attachments in [
                (None, '다른 질문', None),
                (None, '같은 질문', [_image()]),
                (first['conversation_id'], '같은 질문', None),
            ]:
                with self.assertRaises(turns.TurnConflict):
                    turns.reserve_turn(root, first['request_id'], target, text,
                                       attachments=attachments)
            with self.assertRaisesRegex(ValueError, 'conversation_not_found'):
                turns.reserve_turn(root, 'a' * 32, 'f' * 32, '없는 대화')
            second = turns.reserve_turn(root, 'b' * 32, None, '별도 대화')
            for mutation in [
                {'owner_id': second['owner_id']},
                {'conversation_id': second['conversation_id']},
                {'turn_id': second['turn_id'] + 1},
                {'context_version': second['context_version'] + 1},
            ]:
                with self.assertRaises(turns.TurnConflict):
                    turns.complete_turn(root, {**first, **mutation}, '가짜 답변')
            with self.assertRaisesRegex(ValueError, 'turn_ticket_invalid'):
                turns.complete_turn(root, {**first, 'source': 'microphone'}, '가짜 답변')
            self.assertEqual(turns.get_request(root, first['request_id'])['state'], 'running')

    def test_external_voice_advance_or_modified_source_refuses_stale_commit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = turns.reserve_turn(root, 'c' * 32, None, '사용자 텍스트')
            history.record_voice(root, receipt['conversation_id'], 2, 'user',
                                 '외부 음성 이벤트', 2)
            with self.assertRaisesRegex(turns.StaleTurn, 'turn_not_current'):
                turns.complete_turn(root, receipt, '쓰면 안 되는 답변')
            with history.database(root) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0], 0)
            second = turns.reserve_turn(root, 'd' * 32, receipt['conversation_id'], '확인 질문')
            with history.database(root, write=True) as db:
                db.execute("UPDATE voice_messages SET content='변조됨' WHERE session_id=? AND turn_id=? AND role='user'",
                           (second['conversation_id'], second['turn_id']))
            with self.assertRaisesRegex(turns.StaleTurn, 'turn_not_current'):
                turns.complete_turn(root, second, '출처 없는 답변')

    def test_validation_rejects_controls_oversize_paths_commands_and_extra_fields(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_texts = ['', '  \n\t  ', '\x00', 'ok\x85', 'x' * 4001, '😀' * 4001]
            for index, text in enumerate(invalid_texts):
                with self.subTest(text=index), self.assertRaises(ValueError):
                    turns.reserve_turn(root, 'a' * 32, None, text)
            with self.assertRaises(ValueError):
                turns.reserve_turn(root, 'NOTHEX' * 5, None, '잘못된 ID')
            for index, attachment in enumerate([
                {**_image(), 'path': '/private/secret'},
                _image('../../user.jpg'),
                _image('bad\x00.jpg'),
                {**_image(), 'sha256': 'not-a-hash'},
                {**_image(), 'size': False},
                {**_image(), 'mime': 'image/png;bash'},
            ]):
                with self.subTest(attachment=index), self.assertRaises(ValueError):
                    turns.reserve_turn(root, 'b' * 32, None, '질문', attachments=[attachment])
            self.assertFalse((root / 'alden-history.sqlite3').exists(),
                             'invalid input must not create storage')
            valid = turns.reserve_turn(root, 'c' * 32, None, '😀' * 4000)
            self.assertEqual(len(valid['conversation_id']), 32)
            with self.assertRaises(ValueError):
                turns.complete_turn(root, valid, 'a' * (turns.MAX_REPLY_CHARS + 1))
            with self.assertRaises(ValueError):
                turns.cancel_turn(root, valid, reason='private path /secrets')

    def test_rollback_does_not_supersede_or_partially_commit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = turns.reserve_turn(root, 'e' * 32, None, '원래 보관')
            with history.database(root, write=True) as db:
                db.execute('''CREATE TRIGGER refuse_user BEFORE INSERT ON voice_messages
                    WHEN NEW.role='user' AND NEW.content='트리거 실패'
                    BEGIN SELECT RAISE(ABORT, 'fixture_user_insert_failure'); END''')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture_user_insert_failure'):
                turns.reserve_turn(root, 'f' * 32, old['conversation_id'], '트리거 실패')
            self.assertIsNone(turns.get_request(root, 'f' * 32))
            self.assertEqual(turns.get_request(root, old['request_id'])['state'], 'running')

            with history.database(root, write=True) as db:
                db.execute('''CREATE TRIGGER refuse_assistant BEFORE INSERT ON voice_messages
                    WHEN NEW.role='assistant'
                    BEGIN SELECT RAISE(ABORT, 'fixture_assistant_insert_failure'); END''')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture_assistant_insert_failure'):
                turns.complete_turn(root, old, '답변')
            self.assertEqual(turns.get_request(root, old['request_id'])['state'], 'running')
            with history.database(root) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM voice_messages WHERE role='assistant'").fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_sources').fetchone()[0], 0)
            with history.database(root, write=True) as db:
                db.execute('DROP TRIGGER refuse_assistant')
            self.assertEqual(turns.complete_turn(root, old, '재시도 답변')['reply'], '재시도 답변')

    def test_cross_process_reservations_allocate_unique_monotonic_counters(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed = turns.reserve_turn(root, 'a' * 32, None, '초기 요청')
            turns.complete_turn(root, seed, '초기 답변')
            args = [(directory, f'{index:032x}', seed['conversation_id'], f'요청 {index}')
                    for index in range(1, 9)]
            ctx = multiprocessing.get_context('spawn')
            with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=ctx) as executor:
                tickets = list(executor.map(_child_reserve, args))
            self.assertEqual(sorted(ticket['turn_id'] for ticket in tickets), list(range(2, 10)))
            self.assertEqual(sorted(ticket['context_version'] for ticket in tickets), list(range(3, 11)))
            states = [turns.get_request(root, ticket['request_id'])['state'] for ticket in tickets]
            self.assertEqual(states.count('running'), 1)
            self.assertEqual(states.count('superseded'), 7)
            with history.database(root) as db:
                rows = db.execute('''SELECT turn_id,role FROM voice_messages
                                     WHERE session_id=? ORDER BY turn_id,role''',
                                  (seed['conversation_id'],)).fetchall()
                self.assertEqual(len(rows), 10)
                self.assertEqual(len({row['turn_id'] for row in rows}), 9)
            winner = max(tickets, key=lambda ticket: ticket['turn_id'])
            self.assertEqual(turns.complete_turn(root, winner, '최신 답변')['state'], 'completed')
            for loser in tickets:
                if loser != winner:
                    with self.assertRaises(turns.StaleTurn):
                        turns.complete_turn(root, loser, '늦은 답변')

    def test_simultaneous_duplicate_initial_request_owns_one_conversation(self):
        with TemporaryDirectory() as directory:
            args = [(directory, 'd' * 32, None, '같은 최초 요청')] * 4
            ctx = multiprocessing.get_context('spawn')
            with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=ctx) as executor:
                receipts = list(executor.map(_child_reserve, args))
            self.assertTrue(all(receipt == receipts[0] for receipt in receipts[1:]))
            root = Path(directory)
            with history.database(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_sessions').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM voice_messages').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM alden_chat_requests').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
