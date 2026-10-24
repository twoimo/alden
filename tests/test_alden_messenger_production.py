"""Production-root behavior through owned readers; no source DB or sends."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import hashlib
import sys
import unicodedata
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, read_action
from alden_messenger_runtime import Frontier
from alden_collection_scheduler import CollectionScheduler
from alden_collection_retrieval import index_text
from alden_realtime_collection import RealtimeCollection
from alden_abort import AbortController
import alden_messenger_production as production
from tests.test_alden_messenger_frontier import Reader, item


class FixtureReader(Reader):
    account = '123'
    def catalog(self):
        return {'account_id': self.account, 'complete': True,
                'rooms': [{'id': 'a', 'title': 'Owned Fixture'}, {'id': 'b', 'title': 'Second Fixture'}]}

    def content_revisions(self, keys):
        rows = []
        for key in keys:
            values = self.pages[(key['room_id'], None)][0]
            row = next((row for row in values if row['id'] == key['message_id']), None)
            state = 'missing' if row is None else 'withheld' if not row['visible'] else 'available'
            content = hashlib.sha256(unicodedata.normalize('NFC', row['body']).encode()).hexdigest() if state == 'available' else None
            rows.append(dict(key, state=state, content_hash=content))
        return rows


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'state'
        self.root.mkdir(mode=0o700)
        self.reader = FixtureReader({('a', None): ([item(1)], False, None), ('b', None): ([item(2)], False, None)})
        self.real_canonical_reader = production.canonical_reader
        patcher = patch.object(production, 'canonical_reader', return_value=self.reader)
        patcher.start()
        self.addCleanup(patcher.stop)

    def enroll(self):
        return production.enroll(self.root, ['messenger'], reader=self.reader)

    def source(self):
        return Frontier(self.root, 'kakaotalk', self.reader.account, self.reader, production=True)

    def graph(self):
        return read_action(self.root, 'collection-graph', json.dumps({'projects': ['messenger'], 'limit': 100}))

    def test_explicit_enrollment_then_normal_graph_and_history_keep_current_source_guards(self):
        result = self.enroll()
        self.assertEqual(result['rooms'], 2)
        self.assertEqual(self.reader.reads, [])
        self.assertFalse((self.root / '.messenger-evaluation').exists())
        source = self.source()
        self.assertEqual(source.step()['state'], 'published')
        self.assertTrue(self.graph()['nodes'])
        history = read_action(self.root, 'collection-history', json.dumps({'projects': ['messenger']}))
        self.assertTrue(history['items'])
        self.assertEqual(source.status()['enrollment_mode'], 'production')

    def test_idempotent_enrollment_preserves_user_pause_interval_and_denied_permission(self):
        result = self.enroll()
        store = CollectionStore(self.root)
        with store.database() as db:
            db.execute('UPDATE targets SET enabled=0,interval_seconds=3600 WHERE id=?', (result['target_id'],))
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (result['target_id'],))
        before = (self.root / production.ENROLLMENT).read_bytes()
        repeated = self.enroll()
        self.assertFalse(repeated['enabled'])
        self.assertEqual(store.target(result['target_id'])['interval_seconds'], 3600)
        self.assertEqual(store.target(result['target_id'])['projects'][0]['permission'], 'denied')
        self.assertEqual((self.root / production.ENROLLMENT).read_bytes(), before)

    def test_account_switch_hides_existing_graph_without_reading_another_account(self):
        self.enroll()
        self.source().step()
        self.reader.account = '456'
        self.reader.reads.clear()
        self.assertEqual(self.graph()['nodes'], [])
        self.assertEqual(self.reader.reads, [])

    def test_prepared_or_tampered_scope_never_exposes_existing_body(self):
        self.enroll()
        self.source().step()
        path = self.root / production.ENROLLMENT
        original = path.read_bytes()
        for changes in [{'state': 'prepared'}, {'projects': ['different-project']}]:
            state = json.loads(original)
            state['sources'][0].update(changes)
            path.write_text(json.dumps(state))
            self.assertEqual(self.graph()['nodes'], [])
        path.write_bytes(original)
        self.assertTrue(self.graph()['nodes'])

    def test_unsafe_or_corrupt_marker_hides_messenger_and_keeps_ordinary_sources(self):
        self.enroll()
        self.source().step()
        store = CollectionStore(self.root)
        target = store.register(platform='graph', original_id='ordinary', kind='fixture', label='Ordinary', projects=['ordinary'])
        store.ingest(target, [{'platform': 'graph', 'original_id': 'ordinary-document', 'text': 'Owned ordinary evidence'}])
        path = self.root / production.ENROLLMENT
        path.write_text('{broken')
        self.assertEqual(self.graph()['nodes'], [])
        normal = read_action(self.root, 'collection-graph', json.dumps({'projects': ['ordinary']}))
        self.assertTrue(normal['nodes'])

    def test_explicit_denial_callback_is_not_overridden_by_enrollment(self):
        self.enroll()
        self.source().step()
        result = read_action(self.root, 'collection-graph', json.dumps({'projects': ['messenger']}), source_access=lambda *args: False)
        self.assertEqual(result['nodes'], [])

    def test_permission_revocation_hides_content_and_collection_disable_preserves_guarded_history(self):
        enrolled = self.enroll()
        self.source().step()
        store = CollectionStore(self.root)
        with store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (enrolled['target_id'],))
        with self.assertRaisesRegex(ValueError, 'project_scope_invalid'):
            self.graph()
        with store.database() as db:
            db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=?", (enrolled['target_id'],))
            db.execute('UPDATE targets SET enabled=0 WHERE id=?', (enrolled['target_id'],))
        self.assertTrue(self.graph()['nodes'])
        with self.assertRaisesRegex(RuntimeError, 'target_disabled_or_denied'):
            self.source().step()

    def test_failed_catalog_does_not_create_a_target_or_enrollment(self):
        with patch.object(self.reader, 'catalog', return_value={'account_id': '123', 'complete': False}):
            with self.assertRaisesRegex(ValueError, 'catalog_incomplete'):
                self.enroll()
        self.assertFalse((self.root / production.ENROLLMENT).exists())
        self.assertFalse((self.root / 'knowledge/collection/collection.sqlite3').exists())

    def test_scope_reassignment_requires_an_explicit_separate_change(self):
        self.enroll()
        with self.assertRaisesRegex(ValueError, 'scope_conflict'):
            production.enroll(self.root, ['other'], reader=self.reader)

    def test_source_publication_uses_the_scheduler_commit_guard(self):
        self.enroll()
        calls = []
        @contextmanager
        def deny():
            calls.append('commit')
            raise RuntimeError('collection_scheduler_paused')
            yield
        with self.assertRaisesRegex(RuntimeError, 'scheduler_paused'):
            production.collect(self.root, self.enroll()['target_id'], reader=self.reader, publication_guard=deny)
        self.assertTrue(calls)
        self.assertEqual(self.graph()['nodes'], [])

    def test_bounded_collect_replays_without_duplicate_knowledge_events(self):
        target = self.enroll()['target_id']
        first = production.collect(self.root, target, reader=self.reader)
        self.assertEqual(len(first['steps']), 9)
        with CollectionStore(self.root).database() as db:
            before = db.execute('SELECT count(*) FROM events').fetchone()[0]
        production.collect(self.root, target, reader=self.reader)
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], before)
        self.assertTrue(self.graph()['nodes'])

    def test_scheduler_indexes_and_updates_the_normal_graph_without_snapshot_fallback(self):
        enrolled = self.enroll()
        realtime = RealtimeCollection(self.root)
        realtime.update(True, 0)
        scheduler = CollectionScheduler(self.root)
        with patch.object(scheduler, '_voice_busy', return_value=False):
            result = scheduler.cycle(indexer=index_text, collector=lambda *a, **k: self.fail('file collector'),
                                     capturer=lambda *a, **k: self.fail('file capturer'))
            self.assertEqual(result['state'], 'complete', result)
            self.assertEqual(result['attempts'][0]['target_id'], enrolled['target_id'])
            self.assertTrue(self.graph()['nodes'])
            with CollectionStore(self.root).database() as db:
                events = db.execute('SELECT count(*) FROM events').fetchone()[0]
            again = scheduler.cycle(indexer=index_text, now=__import__('time').time() + 6)
            self.assertEqual(again['state'], 'complete', again)
            with CollectionStore(self.root).database() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], events)

    def test_realtime_off_and_pause_and_voice_do_not_read_message_pages(self):
        self.enroll()
        scheduler = CollectionScheduler(self.root)
        with patch.object(scheduler, '_voice_busy', return_value=False):
            self.assertEqual(scheduler.cycle(indexer=index_text)['state'], 'idle')
            RealtimeCollection(self.root).update(True, 0)
            scheduler.pause(True)
            self.assertEqual(scheduler.cycle(indexer=index_text)['state'], 'paused')
            scheduler.pause(False)
        with patch.object(scheduler, '_voice_busy', return_value=True):
            self.assertEqual(scheduler.cycle(indexer=index_text)['state'], 'deferred')
        self.assertEqual(self.reader.reads, [])

    def test_emergency_latch_prevents_collection_and_enrollment_without_clearing_state(self):
        self.enroll()
        RealtimeCollection(self.root).update(True, 0)
        AbortController(self.root).abort('fixture-stop')
        before = (self.root / 'alden-abort.json').read_bytes()
        with patch.object(CollectionScheduler, '_voice_busy', return_value=False):
            self.assertEqual(CollectionScheduler(self.root).cycle(indexer=index_text)['state'], 'paused')
        with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
            self.enroll()
        self.assertEqual((self.root / 'alden-abort.json').read_bytes(), before)
        self.assertEqual(self.reader.reads, [])

    def test_realtime_revocation_at_source_read_blocks_the_store_commit(self):
        self.enroll()
        realtime = RealtimeCollection(self.root)
        realtime.update(True, 0)
        scheduler = CollectionScheduler(self.root)
        page = self.reader.page
        revoked = []
        def revoke(room, cursor):
            value = page(room, cursor)
            if not revoked:
                realtime.update(False, 1)
                revoked.append(True)
            return value
        with patch.object(self.reader, 'page', side_effect=revoke), patch.object(scheduler, '_voice_busy', return_value=False):
            result = scheduler.cycle(indexer=index_text)
        self.assertEqual(result['state'], 'partial', result)
        self.assertEqual(result['attempts'][0]['state'], 'paused', result)
        self.assertEqual(self.graph()['nodes'], [])

    def test_reader_failure_uses_existing_backoff_instead_of_another_backend(self):
        self.enroll()
        RealtimeCollection(self.root).update(True, 0)
        scheduler = CollectionScheduler(self.root)
        with patch.object(production, 'canonical_reader', side_effect=RuntimeError('fixture_source_unavailable')), patch.object(scheduler, '_voice_busy', return_value=False):
            result = scheduler.cycle(indexer=index_text)
            self.assertEqual(result['attempts'][0]['state'], 'failed', result)
            again = scheduler.cycle(indexer=index_text)
            self.assertEqual(again['attempts'], [])
        self.assertEqual(self.reader.reads, [])

    def test_current_source_edit_withdrawal_and_removal_hide_stored_body_without_waiting_for_collection(self):
        self.enroll()
        self.source().step()
        original = self.reader.pages[('a', None)]
        for values in [[item(1, '다른 원문 자료 검토 회의 일정')], [item(1, visible=False)], []]:
            self.reader.pages[('a', None)] = (values, False, None)
            self.assertEqual(self.graph()['nodes'], [])
        self.reader.pages[('a', None)] = original
        self.assertTrue(self.graph()['nodes'])

    def test_missing_or_malformed_revision_capability_withholds_content(self):
        self.enroll()
        self.source().step()
        for value in [None, [], [{'room_id': 'a', 'message_id': '1', 'state': 'available', 'content_hash': 'forged'}]]:
            with patch.object(self.reader, 'content_revisions', return_value=value):
                self.assertEqual(self.graph()['nodes'], [])
        self.assertTrue(self.graph()['nodes'])

    def test_native_reader_without_new_revision_capability_is_rejected_before_enrollment(self):
        from types import SimpleNamespace
        from alden_messenger_runtime import SourceBlocked
        # Invoke the real factory function rather than the setUp fixture override.
        with patch('alden_messenger_factory.load_kakao_sdk', return_value=SimpleNamespace(snapshot=lambda **k: None)):
            with self.assertRaisesRegex(SourceBlocked, 'content_revision_capability_required'):
                self.real_canonical_reader()

    def test_revision_memory_budget_withholds_before_materializing_the_full_candidate_set(self):
        self.enroll()
        self.source().step()
        with patch.object(production, 'MAX_REVISION_TEXT_BYTES', 1), patch.object(self.reader, 'content_revisions') as revisions:
            self.assertEqual(self.graph()['nodes'], [])
            revisions.assert_not_called()

    def test_source_changes_during_a_graph_read_invalidate_the_response(self):
        self.enroll()
        self.source().step()
        original = self.reader.content_revisions
        calls = []
        def changing(keys):
            calls.append(True)
            if len(calls) == 2:
                self.reader.pages[('a', None)] = ([item(1, visible=False)], False, None)
            return original(keys)
        with patch.object(self.reader, 'content_revisions', side_effect=changing):
            with self.assertRaisesRegex(RuntimeError, 'source_authorization_changed'):
                self.graph()

    def test_enrollment_failure_between_target_enable_and_active_marker_is_resumable_and_withheld(self):
        save = production._save_enrollment
        calls = []
        def fail_second(root, state):
            calls.append(True)
            if len(calls) == 2:
                raise OSError('owned fixture disk failure')
            return save(root, state)
        with patch.object(production, '_save_enrollment', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.enroll()
        self.assertEqual(production.read_enrollment(self.root)['sources'][0]['state'], 'prepared')
        self.assertEqual(self.graph()['nodes'], [])
        self.assertEqual(self.reader.reads, [])
        self.assertEqual(self.enroll()['state'], 'enrolled')
        self.assertEqual(production.read_enrollment(self.root)['sources'][0]['state'], 'active')

    def test_source_device_probes_do_not_run_inside_the_scheduler_abort_commit_guard(self):
        target = self.enroll()['target_id']
        inside = []
        identity = self.reader.identity
        def observed_identity():
            self.assertFalse(inside, 'Source-device probe holds the abort commit guard')
            return identity()
        @contextmanager
        def guard():
            inside.append(True)
            try:
                yield
            finally:
                inside.pop()
        with patch.object(self.reader, 'identity', side_effect=observed_identity):
            production.collect(self.root, target, reader=self.reader, publication_guard=guard)


if __name__ == '__main__':
    unittest.main()
