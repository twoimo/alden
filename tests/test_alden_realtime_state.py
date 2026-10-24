"""Bounded generated traces of the actual watch loop, not a whole-system proof."""
from itertools import product
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_realtime_collection import POLL_SECONDS, watch


class WatchStateTests(unittest.TestCase):
    def run_trace(self, phase, code, control='ready', enabled=True):
        events = []; tick = [0]; fired = []
        stopped = threading.Event()
        def boundary(name):
            events.append((tick[0], name))
            if name == phase and not fired:
                fired.append(tick[0])
                events.append((tick[0], 'error'))
                error = sqlite3.OperationalError('controlled SQLite boundary failure')
                error.sqlite_errorcode = code
                raise error
        class Live:
            def __init__(self, _root): pass
            def policy(self):
                boundary('policy'); return {'enabled': enabled}
            def heartbeat(self, state):
                boundary('heartbeat'); events.append((tick[0], 'state:' + state))
            def scan(self, *_a, **_kw): boundary('scan')
        def existing(_root):
            boundary('store'); return object()
        def paused():
            boundary('paused'); return control == 'paused'
        def abort():
            boundary('abort'); return control == 'aborted'
        def voice():
            boundary('voice'); return control == 'voice'
        def cycle(**options):
            boundary('cycle')
            options['on_progress']()
            return {'state': 'paused' if control in {'paused', 'aborted'} else
                    'deferred' if control == 'voice' else 'complete'}
        scheduler = SimpleNamespace(state_root=Path('/controlled/no-files'),
            _paused=paused, _abort_latched=abort, _voice_busy=voice, cycle=cycle)
        def wait(seconds):
            self.assertEqual(seconds, POLL_SECONDS)
            events.append((tick[0], 'wait')); tick[0] += 1
            if tick[0] == 2: stopped.set()
        with patch('alden_realtime_collection.RealtimeCollection', Live), \
             patch('alden_collection.CollectionStore.open_existing', side_effect=existing):
            watch(scheduler, stopped, wait=wait, now=lambda: 100 + tick[0] * POLL_SECONDS)
        return events, fired

    def test_busy_interleavings_retry_and_recheck_guards_at_the_next_tick(self):
        cases = 0
        phases = ('policy', 'store', 'paused', 'abort', 'voice', 'scan', 'cycle', 'heartbeat')
        codes = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY | (2 << 8))
        for phase, code, control in product(phases, codes, ('ready', 'paused', 'aborted', 'voice')):
            if phase == 'abort' and control == 'paused': continue
            if phase == 'scan' and control != 'ready': continue
            with self.subTest(phase=phase, code=code, control=control):
                events, fired = self.run_trace(phase, code, control)
                self.assertEqual(fired, [0])
                self.assertEqual(sum(name == 'wait' for _, name in events), 2)
                self.assertIn((1, 'policy'), events)
                self.assertIn((1, 'store'), events)
                if control != 'ready':
                    self.assertNotIn((1, 'scan'), events)
                if phase != 'heartbeat':
                    failure = events.index((0, 'error'))
                    end = events.index((0, 'wait'))
                    self.assertTrue(all(name == 'heartbeat' or name.startswith('state:')
                                        for _, name in events[failure + 1:end]))
                cases += 1
        self.assertEqual(cases, 84)

    def test_busy_periodic_cycle_retries_without_advancing_its_success_clock(self):
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            events, fired = self.run_trace('cycle', code, enabled=False)
            self.assertEqual(fired, [0])
            self.assertIn((1, 'cycle'), events)

    def test_non_contention_database_errors_remain_fatal(self):
        for phase in ('policy', 'store', 'paused', 'abort', 'voice', 'scan', 'cycle', 'heartbeat'):
            with self.subTest(phase=phase):
                with self.assertRaises(sqlite3.OperationalError):
                    self.run_trace(phase, sqlite3.SQLITE_ERROR)


if __name__ == '__main__': unittest.main()
