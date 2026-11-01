"""Durable reference work through the normal collector; owned stores, no sends."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import fcntl
import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, read_action
from alden_collection_scheduler import CollectionScheduler, settings_action
from alden_messenger_runtime import Frontier, SourceBlocked
import alden_messenger_production as production
from tests.test_alden_messenger_reference_lifecycle import ReferenceReader
from tests.test_alden_messenger_frontier import item


class JobReader(ReferenceReader):
    def __init__(self):
        super().__init__({('123', None): ([item(n, f'계약 검토 자료 {n}과 운영 회의 일정에 대한 구체적인 결과입니다.')
                                         for n in (9, 8, 7)], False, None)})
        self.attempted = []
        self.failures = set()

    def reply_reference(self, room, message):
        self.attempted.append(message)
        if message in self.failures:
            raise SourceBlocked('fixture_reference_temporarily_unavailable')
        if not any(row['id'] == message for row in self.pages[(room, None)][0]):
            return {'state': 'withheld', 'reason': 'reply_body_not_visible'}
        if message != '9':
            return {'state': 'not_admitted', 'reason': 'no_verified_reply_metadata'}
        return super().reply_reference(room, message)


class ReferenceJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'state'; self.root.mkdir(mode=0o700)
        self.reader = JobReader()
        for target, attribute, value in [(production, 'canonical_reader', self.reader), (CollectionScheduler, '_voice_busy', False)]:
            p = patch.object(target, attribute, return_value=value); p.start(); self.addCleanup(p.stop)
        self.target = production.enroll(self.root, ['messenger'], reader=self.reader)['target_id']
        self.source = Frontier(self.root, 'kakaotalk', self.reader.account, self.reader, production=True)

    def graph(self):
        return read_action(self.root, 'collection-graph', json.dumps({'projects': ['messenger'], 'limit': 100}))

    def jobs(self):
        with self.source.database() as db:
            return [dict(r) for r in db.execute('SELECT * FROM reference_jobs ORDER BY message DESC')]

    def due(self):
        with self.source.database() as db:
            db.execute("UPDATE reference_jobs SET retry_at=0 WHERE state='pending'")

    def prefer(self, message):
        with self.source.database() as db:
            db.execute('UPDATE reference_jobs SET retry_at=CASE WHEN message=? THEN 0 ELSE 1 END', (message,))

    def events(self):
        with CollectionStore(self.root).database() as db:
            return db.execute('SELECT count(*) FROM events').fetchone()[0]

    def test_normal_collect_publishes_reply_without_replacing_any_source_lane(self):
        result = production.collect(self.root, self.target, reader=self.reader)
        self.assertEqual(len(self.graph()['edges']), 1)
        self.assertEqual(len(result['steps']), 9)
        self.assertEqual(len(result['reference_steps']), 3)
        self.assertEqual({s['state'] for s in result['reference_steps']}, {'ready', 'not_admitted'})
        self.assertEqual(result['state'], 'complete')

    def test_frontier_failure_registers_no_job_and_recovery_registers_with_heads(self):
        def interrupted(): raise RuntimeError('fixture_store_frontier_gap')
        with self.assertRaisesRegex(RuntimeError, 'store_frontier_gap'):
            self.source.step(after_publish=interrupted)
        self.assertEqual(self.jobs(), [])
        self.source.step()
        self.assertEqual({j['message'] for j in self.jobs()}, {'7', '8', '9'})
        with self.source.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='prepared'").fetchone()[0], 0)

    def test_interruption_after_relation_commit_replays_same_origin_without_extra_event(self):
        self.source.step()
        self.prefer('9')
        real_database = self.source.database
        @contextmanager
        def fail_ack():
            with real_database() as db:
                yield db
                if db.execute("SELECT count(*) FROM reference_jobs WHERE state='settled'").fetchone()[0]:
                    raise RuntimeError('fixture_queue_ack_interrupted')
        with patch.object(self.source, 'database', fail_ack), self.assertRaisesRegex(RuntimeError, 'ack_interrupted'):
            self.source.reference_step()
        self.assertEqual(len(self.graph()['edges']), 1)
        self.assertTrue(all(j['state'] == 'pending' for j in self.jobs()))
        count = self.events()
        self.source = Frontier(self.root, 'kakaotalk', self.reader.account, self.reader, production=True)
        self.due(); self.prefer('9')
        self.assertEqual(self.source.reference_step()['state'], 'ready')
        self.assertEqual(self.events(), count)

    def test_failed_reference_does_not_starve_another_job_or_source_acquisition(self):
        self.reader.failures.add('9')
        result = production.collect(self.root, self.target, reader=self.reader)
        self.assertEqual(len(result['steps']), 9)
        self.assertEqual(len(self.graph()['nodes']), 3)
        self.assertEqual(result['state'], 'partial')
        self.assertIn('8', self.reader.attempted); self.assertIn('7', self.reader.attempted)
        failed = next(j for j in self.jobs() if j['message'] == '9')
        self.assertEqual(failed['state'], 'pending'); self.assertIsNotNone(failed['error'])
        self.reader.failures.clear(); self.due()
        self.assertEqual(self.source.reference_step()['state'], 'ready')

    def test_pending_endpoint_is_retried_after_later_source_backfill(self):
        self.source.step()
        self.prefer('9')
        with self.source.database() as db:
            db.execute("DELETE FROM message_heads WHERE message='7'")
        result = self.source.reference_step()
        self.assertEqual(result['state'], 'pending_endpoints')
        self.assertEqual(next(j for j in self.jobs() if j['message'] == '9')['state'], 'pending')
        self.source.step(mode='reconcile'); self.due()
        for _ in range(3): self.source.reference_step()
        self.assertEqual(len(self.graph()['edges']), 1)

    def test_unchanged_body_reconcile_requeues_changed_reply_metadata_and_withdrawal(self):
        production.collect(self.root, self.target, reader=self.reader)
        old = self.graph()['edges'][0]
        self.reader.reply = '8'; self.source.step(mode='reconcile')
        for _ in range(3): self.source.reference_step()
        new = self.graph()['edges'][0]
        self.assertNotEqual(new['target'], old['target'])
        self.reader.reply = None; self.source.step(mode='reconcile')
        for _ in range(3): self.source.reference_step()
        self.assertEqual(self.graph()['edges'], [])

    def test_same_source_writer_lock_excludes_reference_worker(self):
        self.source.step()
        fd = os.open(self.source.folder / 'writer.lock', os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.source.reference_step()['state'], 'busy')
            self.assertEqual(self.reader.attempted, [])
        finally: os.close(fd)

    def test_cancel_pause_and_permission_revocation_do_not_consume_jobs(self):
        self.source.step(); before = self.jobs()
        self.assertEqual(self.source.reference_step(cancelled=lambda: True)['state'], 'cancelled')
        self.assertEqual(self.jobs(), before)
        with patch.object(CollectionScheduler, '_voice_busy', return_value=True):
            self.assertEqual(self.source.reference_step()['state'], 'blocked')
        self.assertEqual(self.reader.attempted, [])
        with CollectionStore(self.root).database() as db:
            db.execute('UPDATE targets SET enabled=0 WHERE id=?', (self.target,))
        with self.assertRaisesRegex(SourceBlocked, 'disabled_or_denied'):
            self.source.reference_step()
        self.assertTrue(all(j['state'] == 'pending' for j in self.jobs()))

    def test_non_candidate_context_does_not_create_reference_work_or_graph_nodes(self):
        self.reader.pages[('123', None)] = ([item(9, '네'), item(8, '1'), item(7, 'ㅋㅋ')], False, None)
        self.source.step()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.source.reference_step()['state'], 'idle')
        self.assertEqual(self.graph()['nodes'], [])

    def test_new_generation_during_work_is_not_acknowledged_by_old_completion(self):
        from alden_messenger_references import reconcile
        self.source.step(); self.prefer('9')
        def requeue(*args, **kwargs):
            result = reconcile(*args, **kwargs)
            with self.source.database() as db:
                db.execute("UPDATE reference_jobs SET generation=generation+1,state='pending' WHERE message='9'")
            return result
        with patch('alden_messenger_references.reconcile', side_effect=requeue):
            result = self.source.reference_step()
        self.assertEqual(result['queue_state'], 'superseded')
        self.assertEqual(next(j for j in self.jobs() if j['message'] == '9')['state'], 'pending')

    def test_frontier_commit_failure_rolls_back_jobs_and_heads_together(self):
        with self.source.database() as db:
            db.execute("CREATE TRIGGER fixture_reject BEFORE UPDATE ON rooms BEGIN SELECT RAISE(ABORT,'fixture_frontier_commit_failed'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'frontier_commit_failed'):
            self.source.step()
        self.assertEqual(self.jobs(), [])
        with self.source.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM message_heads').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='prepared'").fetchone()[0], 1)
            db.execute('DROP TRIGGER fixture_reject')
        self.source.step()
        self.assertEqual(len(self.jobs()), 3)

    def test_waiting_failure_keeps_backoff_and_healthy_jobs_continue(self):
        self.source.step(); self.prefer('9'); self.reader.failures.add('9')
        self.assertEqual(self.source.reference_step()['state'], 'blocked')
        failed = next(j for j in self.jobs() if j['message'] == '9')
        self.source.step(mode='reconcile')
        current = next(j for j in self.jobs() if j['message'] == '9')
        self.assertEqual(current['retry_at'], failed['retry_at'])
        for _ in range(2): self.assertEqual(self.source.reference_step()['state'], 'not_admitted')
        self.assertEqual(self.source.reference_step()['state'], 'idle')

    def test_watched_origin_is_retired_even_after_it_disappears_from_later_pages(self):
        production.collect(self.root, self.target, reader=self.reader)
        self.assertEqual(len(self.graph()['edges']), 1)
        self.reader.pages[('123', None)][0].pop(0)
        self.source.step(mode='reconcile'); self.prefer('9')
        self.assertEqual(self.source.reference_step()['state'], 'withheld')
        self.assertEqual(self.graph()['edges'], [])
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM relations WHERE active=1').fetchone()[0], 0)
        self.assertEqual(next(j for j in self.jobs() if j['message'] == '9')['watch'], 1)

    def test_periodic_unchanged_reference_checks_emit_no_new_activity(self):
        production.collect(self.root, self.target, reader=self.reader)
        before = self.events(); self.prefer('9')
        result = self.source.reference_step()
        self.assertEqual(result['state'], 'ready'); self.assertFalse(result['changed'])
        self.assertEqual(self.events(), before)

    def test_normal_scheduler_indexes_the_automatically_linked_format_four_store(self):
        from alden_collection_retrieval import index_text
        from alden_realtime_collection import RealtimeCollection
        RealtimeCollection(self.root).update(True, 0)
        scheduler = CollectionScheduler(self.root)
        result = scheduler.cycle(indexer=index_text)
        self.assertEqual(result['state'], 'complete', result)
        with scheduler.database() as db:
            saved = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
        self.assertEqual(saved['collection']['state'], 'complete')
        self.assertEqual(saved['index']['state'], 'ready')
        self.assertEqual(len(self.graph()['edges']), 1)

    def test_scheduler_status_exposes_reference_backlog_without_claiming_room_failure(self):
        from alden_collection_retrieval import index_text
        from alden_realtime_collection import RealtimeCollection
        RealtimeCollection(self.root).update(True, 0)
        self.reader.failures.add('9')
        result = CollectionScheduler(self.root).cycle(indexer=index_text)
        self.assertEqual(result['state'], 'complete', result)
        status = settings_action(self.root, 'collection-scheduler-status')['targets'][0]
        self.assertEqual(status['collection_status'], 'partial')
        self.assertEqual(status['collection_pending_references'], 1)
        self.assertIsNone(status['collection_blocked_rooms'])

    def test_filtered_endpoint_settles_without_noise_node_and_can_become_ready_after_edit(self):
        self.reader.pages[('123', None)][0][-1] = item(7, '네')
        result = production.collect(self.root, self.target, reader=self.reader)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(len(self.graph()['nodes']), 2); self.assertEqual(self.graph()['edges'], [])
        job = next(j for j in self.jobs() if j['message'] == '9')
        self.assertEqual(json.loads(job['result'])['state'], 'retained_context')
        self.assertEqual(job['state'], 'settled'); self.assertEqual(job['watch'], 1)
        self.reader.pages[('123', None)][0][-1] = item(7, '계약 검토 자료 7과 운영 회의 일정에 대한 구체적인 결과입니다.')
        self.source.step(mode='reconcile')
        for _ in range(3): self.source.reference_step()
        self.assertEqual(len(self.graph()['nodes']), 3); self.assertEqual(len(self.graph()['edges']), 1)

    def test_review_endpoint_remains_reversible_review_without_a_permanent_pending_job(self):
        self.reader.pages[('123', None)][0][-1] = item(7, '수익 보장 입장 안내와 자세한 가입 방법을 전달합니다.')
        result = production.collect(self.root, self.target, reader=self.reader)
        self.assertEqual(result['state'], 'complete')
        job = next(j for j in self.jobs() if j['message'] == '9')
        self.assertEqual(json.loads(job['result'])['state'], 'retained_review')
        self.assertEqual(job['state'], 'settled'); self.assertEqual(job['watch'], 1)
        self.assertEqual(self.graph()['edges'], [])


if __name__ == '__main__': unittest.main()
