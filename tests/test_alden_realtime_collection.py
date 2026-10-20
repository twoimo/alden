"""Actual local input/collector publication; isolated roots and fake dense index."""
from pathlib import Path
import json
import os
import sys
import tempfile
import time
import unittest
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_abort import AbortController
from alden_collect import collect_target, capture_source
from alden_collection import CollectionStore
from alden_collection_scheduler import CollectionScheduler, settings_action
from alden_realtime_collection import RealtimeCollection, watch


class RealtimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').is_dir() else None)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.source = self.root / 'input.json'
        self.write('원문 하나')
        self.target = self.store.register(platform='graph', original_id='live-fixture', kind='graph_source', label='fixture',
            projects=['allowed'], config={'adapter': 'source-graph', 'path': str(self.source)}, interval_seconds=21600)
        self.scheduler = CollectionScheduler(self.root)
        self.live = RealtimeCollection(self.root)

    def write(self, text):
        self.source.write_text(json.dumps({'nodes': [{'id': 'one', 'label': '지식', 'summary': text}], 'edges': []}, ensure_ascii=False))

    def enable(self):
        with self.scheduler.control_lock(): return self.live.update(True, self.live.policy()['revision'])

    def queue(self, stamp=100):
        self.live.scan(self.store, paused=self.scheduler._paused, now=stamp)
        self.live.scan(self.store, paused=self.scheduler._paused, now=stamp + 3)

    def cycle(self, **changes):
        return self.scheduler.cycle(collector=collect_target, capturer=capture_source,
            indexer=changes.pop('indexer', lambda *a, **kw: {'state': 'ready'}), **changes)

    def test_default_read_is_side_effect_free_and_policy_cas_is_recoverable(self):
        self.assertFalse(self.live.policy()['enabled'])
        self.assertFalse(self.live.path.exists())
        self.assertEqual(self.enable(), {'enabled': True, 'revision': 1})
        with self.assertRaisesRegex(ValueError, 'policy_changed'):
            with self.scheduler.control_lock(): self.live.update(False, 0)
        backup, = (self.live.root / 'realtime-policy-backups').glob('*.json')
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(backup.read_text()), {'enabled': False, 'revision': 0})

    def test_settled_changes_survive_restart_coalesce_and_unchanged_polls_do_not_requeue(self):
        self.enable()
        self.live.scan(self.store, now=100)
        self.assertEqual(self.live.pending(), {})
        self.live.scan(self.store, now=103)
        first = self.live.pending()[self.target]
        self.assertEqual(RealtimeCollection(self.root).pending(), self.live.pending())
        self.live.scan(self.store, now=200)
        self.assertEqual(self.live.pending()[self.target], first)
        self.assertTrue(self.live.acknowledge(self.store.target(self.target), first['queued']))
        self.live.scan(self.store, now=203)
        self.assertEqual(self.live.pending(), {})
        self.write('새 원문')
        self.queue(300)
        self.assertNotEqual(self.live.pending()[self.target]['queued'], first['queued'])

    def test_actual_collect_capture_and_search_publish_before_ack_without_changing_interval(self):
        self.cycle()
        before = self.store.target(self.target)
        self.enable(); self.queue()
        result = self.cycle()
        self.assertEqual(result['attempts'][0]['state'], 'complete')
        self.assertEqual(self.live.pending(), {})
        with self.scheduler.database() as db:
            attempt = db.execute('SELECT result FROM attempts ORDER BY started DESC LIMIT 1').fetchone()
            self.assertEqual(json.loads(attempt[0])['trigger'], 'input_change')
        self.assertEqual(self.store.target(self.target)['interval_seconds'], before['interval_seconds'])
        self.assertEqual(self.scheduler.status()['manual_pending'], [])
        self.write('새롭게 수집된 지식')
        self.queue(300)
        self.assertEqual(self.cycle()['attempts'][0]['state'], 'complete')
        self.assertEqual(self.live.pending(), {})
        with self.store.database() as db:
            self.assertGreaterEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0], 2)

    def test_acquired_replacement_consumes_its_original_hint_without_requeueing(self):
        self.enable(); self.queue()
        queued=self.live.pending()[self.target]['queued']
        self.write('원본 확인 중 갱신된 파일')
        target=self.store.target(self.target);current=self.live.signature(target)
        self.assertNotEqual(queued,current)
        self.assertTrue(self.live.acknowledge(target,current,observed_queued=queued))
        self.assertEqual(self.live.pending(),{})
        self.queue(300)
        self.assertEqual(self.live.pending(),{})
        self.write('다음에 도착한 변경');self.queue(400)
        newer=self.live.pending()[self.target]['queued']
        self.assertFalse(self.live.acknowledge(target,current,observed_queued=queued))
        self.assertEqual(self.live.pending()[self.target]['queued'],newer)

    def test_index_pending_retains_durable_input_then_retry_completes(self):
        self.enable(); self.queue()
        first = self.cycle(indexer=lambda *a, **kw: {'state': 'warming'})
        self.assertEqual(first['attempts'][0]['state'], 'pending')
        pending = self.live.pending()
        self.assertIn(self.target, pending)
        self.assertEqual(RealtimeCollection(self.root).pending(), pending)
        with self.scheduler.database() as db: db.execute('UPDATE target_state SET retry_at=0')
        self.assertEqual(self.cycle()['attempts'][0]['state'], 'complete')
        self.assertEqual(self.live.pending(), {})

    def test_change_during_index_is_not_acknowledged_as_the_newer_source(self):
        self.enable(); self.queue()
        def newer(*args, **kwargs):
            self.write('두 번째 수신은 별도 수집해야 한다')
            return {'state': 'ready'}
        self.assertEqual(self.cycle(indexer=newer)['attempts'][0]['state'], 'complete')
        self.assertIn(self.target, self.live.pending())
        self.queue(200)
        self.assertEqual(self.cycle()['attempts'][0]['state'], 'complete')
        self.assertEqual(self.live.pending(), {})

    def test_interruption_retains_input_and_reuses_existing_recovery_path(self):
        self.enable(); self.queue()
        def stop(*args, **kwargs): raise SystemExit('fixture interruption')
        with self.assertRaises(SystemExit): self.cycle(indexer=stop)
        self.assertIn(self.target, RealtimeCollection(self.root).pending())
        self.assertEqual(self.cycle()['attempts'][0]['state'], 'complete')
        self.assertEqual(self.live.pending(), {})

    def test_pause_voice_abort_and_permission_do_not_consume_pending_input(self):
        self.enable(); self.queue()
        self.scheduler.pause(True)
        self.assertEqual(self.cycle()['state'], 'paused')
        self.scheduler.pause(False)
        voice = self.root / 'alden-voice-status.json'
        voice.write_text(json.dumps({'state': 'speaking', 'updated_at': time.time()}))
        self.assertEqual(self.cycle()['state'], 'deferred')
        voice.unlink()
        with self.store.database() as db: db.execute("UPDATE target_projects SET permission='denied'")
        self.assertEqual(self.cycle()['attempts'], [])
        with self.store.database() as db: db.execute("UPDATE target_projects SET permission='allowed'")
        AbortController(self.root).abort('fixture')
        self.assertEqual(self.cycle()['state'], 'paused')
        self.assertIn(self.target, self.live.pending())

    def test_disabling_live_mode_during_collection_fences_publication_and_keeps_queue(self):
        self.enable(); self.queue()
        def stop_live(store, target, **options):
            with self.scheduler.control_lock(): self.live.update(False, 1)
            with options['publication_guard']():
                raise AssertionError('publication must be fenced')
        result = self.scheduler.cycle(collector=stop_live, capturer=capture_source, indexer=lambda *a, **kw: {'state': 'ready'})
        self.assertEqual(result['attempts'][0]['state'], 'paused')
        self.assertFalse(self.live.policy()['enabled'])
        with self.live.database() as db: self.assertIsNotNone(db.execute('SELECT queued FROM inputs WHERE target_id=?', (self.target,)).fetchone()[0])

    def test_missing_and_symlink_inputs_are_errors_not_empty_sources(self):
        self.enable()
        raw = self.source.read_bytes()
        self.source.unlink(); self.queue()
        self.assertEqual(self.live.pending(), {})
        with self.live.database() as db: self.assertEqual(db.execute('SELECT error FROM inputs').fetchone()[0], 'collection_realtime_input_missing')
        other = self.root / 'other.json'; other.write_bytes(raw); self.source.symlink_to(other)
        self.queue(200)
        with self.live.database() as db: self.assertEqual(db.execute('SELECT error FROM inputs').fetchone()[0], 'collection_realtime_input_unsafe')
        self.assertEqual(other.read_bytes(), raw)

    def test_atomic_replacement_and_sqlite_wal_change_signatures(self):
        target = self.store.target(self.target)
        first = self.live.signature(target)
        replacement = self.root / 'replacement.json'; replacement.write_bytes(self.source.read_bytes())
        os.replace(replacement, self.source)
        self.assertNotEqual(self.live.signature(target), first)
        target['config']['adapter'] = 'spark-index'
        before = self.live.signature(target)
        Path(str(self.source) + '-wal').write_bytes(b'fixture WAL')
        self.assertNotEqual(self.live.signature(target), before)

    def test_heartbeat_needs_exact_running_owned_service_pid_and_fresh_timestamp(self):
        self.enable(); self.live.heartbeat('watching', now=100)
        self.assertEqual(self.live.status({'state': 'running', 'pid': os.getpid()}, now=105)['state'], 'watching')
        for service, stamp in [({'state': 'waiting', 'pid': os.getpid()},105),({'state': 'running', 'pid': 1},105),({'state': 'running', 'pid': os.getpid()},125)]:
            self.assertEqual(self.live.status(service, now=stamp)['state'], 'reconnecting')

    def test_global_live_control_uses_opt_in_and_preserves_emergency_latch(self):
        AbortController(self.root).abort('fixture')
        before = (self.root / 'alden-abort.json').read_bytes()
        query = json.dumps({'operation': 'realtime', 'target_id': None, 'enabled': True, 'expected_revision': 0})
        with self.assertRaises(ValueError): settings_action(self.root, 'collection-scheduler-control', query)
        with patch('alden_collection_scheduler.install_schedule', return_value={'state': 'scheduled'}), \
             patch('alden_collection_scheduler.read_schedule', return_value={'state': 'waiting'}):
            result = settings_action(self.root, 'collection-scheduler-control', query, explicit_opt_in=True)
        self.assertTrue(result['realtime']['enabled'])
        self.assertTrue(result['abort_latched'])
        self.assertEqual((self.root / 'alden-abort.json').read_bytes(), before)

    def test_watch_loop_receives_a_later_source_change_without_a_manual_request(self):
        self.enable()
        stopped=threading.Event();clock=[100];ticks=[0]
        original=self.scheduler.cycle
        def cycle(**options):
            return original(indexer=lambda *a,**kw:{'state':'ready'},**options)
        def advance(_seconds):
            ticks[0]+=1;clock[0]+=5
            if ticks[0]==2:self.write('감시 중 들어온 새 자료')
            if ticks[0]==4:stopped.set()
        with patch.object(self.scheduler,'cycle',side_effect=cycle):
            watch(self.scheduler,stopped,wait=advance,now=lambda:clock[0])
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM versions').fetchone()[0],2)
        self.assertEqual(self.live.pending(),{})
        self.assertEqual(self.scheduler.status()['manual_pending'],[])


if __name__ == '__main__': unittest.main()
