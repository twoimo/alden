"""Production-root behavior through owned readers; no source DB or sends."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
import json
import hashlib
import sys
import unicodedata
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, read_action
from alden_messenger_runtime import Frontier
from alden_collection_scheduler import CollectionScheduler, settings_action
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

    def test_recent_catalog_room_is_primed_before_older_rooms_without_narrowing_enrollment(self):
        target = self.enroll()['target_id']
        catalog = self.reader.catalog()
        catalog['rooms'].reverse()
        catalog['room_order'] = 'recent_activity_desc'
        with patch.object(self.reader, 'catalog', return_value=catalog):
            result = production.collect(self.root, target, reader=self.reader)
        self.assertEqual(self.reader.reads[0], ('b', None))
        self.assertEqual(result['status']['rooms'], 2)
        self.assertEqual(len(self.graph()['nodes']), 2)

    def test_unknown_catalog_order_retains_the_existing_full_fair_schedule(self):
        target = self.enroll()['target_id']
        catalog = self.reader.catalog()
        catalog['rooms'].reverse()
        catalog['room_order'] = 'unknown-order'
        with patch.object(self.reader, 'catalog', return_value=catalog):
            result = production.collect(self.root, target, reader=self.reader)
        self.assertEqual(self.reader.reads[0], ('a', None))
        self.assertEqual(result['schedule']['policy'], 'fair-sweeps')
        self.assertEqual(result['status']['rooms'], 2)

    @contextmanager
    def missing_room_adapter(self, missing=('a',)):
        from alden_messenger_runtime import KakaoReader
        adapter = KakaoReader(SimpleNamespace(snapshot=lambda **options: {
            'status': 'room_not_found', 'db_readonly_verified': True, 'schema_decryption_verified': True}))
        adapter.identity = self.reader.identity
        original = self.reader.page
        original_verify = self.reader.verify_prepared
        original_revisions = self.reader.content_revisions
        def page(room, cursor):
            return adapter.page(room, cursor) if room in missing else original(room, cursor)
        def verify(payload):
            return adapter.page(payload['room'], None) if payload['room'] in missing else original_verify(payload)
        def revisions(keys):
            return [dict(row, state='missing', content_hash=None) if row['room_id'] in missing else row
                    for row in original_revisions(keys)]
        with patch.object(self.reader, 'page', side_effect=page), patch.object(self.reader, 'verify_prepared', side_effect=verify), \
                patch.object(self.reader, 'content_revisions', side_effect=revisions):
            yield

    def test_missing_room_does_not_delay_other_room_publication_or_index(self):
        enrolled = self.enroll()
        RealtimeCollection(self.root).update(True, 0)
        scheduler = CollectionScheduler(self.root)
        with self.missing_room_adapter(), patch.object(scheduler, '_voice_busy', return_value=False):
            result = scheduler.cycle(indexer=index_text)
        self.assertEqual(result['state'], 'complete', result)
        self.assertEqual(result['attempts'][0]['stage'], 'index')
        self.assertEqual(len(self.graph()['nodes']), 1)
        with scheduler.database() as db:
            attempt = json.loads(db.execute('SELECT result FROM attempts').fetchone()[0])
            state = db.execute('SELECT failures,blocked FROM target_state WHERE target_id=?', (enrolled['target_id'],)).fetchone()
        self.assertEqual(tuple(state), (0, 0))
        self.assertEqual(attempt['collection']['state'], 'partial')
        self.assertEqual(attempt['collection']['status']['blocked_rooms'], 1)
        self.assertEqual(attempt['index']['state'], 'ready')
        status = settings_action(self.root, 'collection-scheduler-status')
        self.assertEqual(status['targets'][0]['collection_status'], 'partial')
        self.assertEqual(status['targets'][0]['collection_blocked_rooms'], 1)

    def test_all_missing_rooms_remain_explicitly_partial_and_publish_nothing(self):
        target = self.enroll()['target_id']
        with self.missing_room_adapter(('a', 'b')):
            result = production.collect(self.root, target, reader=self.reader)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['status']['blocked_rooms'], 2)
        self.assertEqual(self.graph()['nodes'], [])
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 0)

    def test_missing_room_rejoins_after_its_own_retry_without_global_backoff(self):
        self.enroll()
        RealtimeCollection(self.root).update(True, 0)
        scheduler = CollectionScheduler(self.root)
        with self.missing_room_adapter(), patch.object(scheduler, '_voice_busy', return_value=False):
            first = scheduler.cycle(indexer=index_text)
        self.assertEqual(first['state'], 'complete', first)
        with self.source().database() as db:
            db.execute("UPDATE rooms SET retry_at=0 WHERE id='a'")
        with patch.object(scheduler, '_voice_busy', return_value=False):
            second = scheduler.cycle(indexer=index_text, now=__import__('time').time() + 6)
        self.assertEqual(second['state'], 'complete', second)
        self.assertEqual(len(self.graph()['nodes']), 2)
        self.assertEqual(self.source().status()['blocked_rooms'], 0)

    def test_readonly_failure_still_stops_the_whole_collection_before_other_rooms(self):
        from alden_messenger_runtime import KakaoReader
        self.enroll()
        RealtimeCollection(self.root).update(True, 0)
        scheduler = CollectionScheduler(self.root)
        adapter = KakaoReader(SimpleNamespace(snapshot=lambda **options: {'status': 'ok', 'db_readonly_verified': False}))
        adapter.identity = self.reader.identity
        with patch.object(self.reader, 'page', side_effect=adapter.page), patch.object(scheduler, '_voice_busy', return_value=False):
            result = scheduler.cycle(indexer=lambda *args, **options: self.fail('index after readonly failure'))
        self.assertEqual(result['state'], 'partial', result)
        self.assertEqual(result['attempts'][0]['stage'], 'collection')
        self.assertEqual(result['attempts'][0]['error'], 'kakao_readonly_or_schema_unconfirmed')
        self.assertEqual(self.graph()['nodes'], [])

    def test_missing_room_during_activity_recovery_does_not_block_other_rooms_or_release_stale_activity(self):
        target = self.enroll()['target_id']
        source = self.source()
        with patch.object(source, '_release_activity', side_effect=SystemExit('after frontier commit')):
            with self.assertRaisesRegex(SystemExit, 'after frontier commit'):
                source.step()
        with self.missing_room_adapter():
            result = production.collect(self.root, target, reader=self.reader)
            self.assertEqual(result['state'], 'partial')
            self.assertEqual(result['status']['blocked_rooms'], 1)
            self.assertEqual(result['status']['pending_activity'], 1)
            self.assertEqual(len(self.graph()['nodes']), 1)
            self.assertEqual(read_action(self.root, 'collection-graph', json.dumps({'projects': ['messenger'], 'activity': True}))['items'], [])

    def test_unverified_missing_room_or_changed_account_never_gets_room_only_scope(self):
        from alden_messenger_runtime import KakaoReader, RoomUnavailable, SourceBlocked
        for changes, identities in [({'db_readonly_verified': False}, ['123', '123']),
                                    ({'schema_decryption_verified': False}, ['123', '123']),
                                    ({'reason': 'current_account_identity_unavailable'}, ['123', '123']),
                                    ({}, ['123', '456'])]:
            with self.subTest(changes=changes, identities=identities):
                response = {'status': 'room_not_found', 'db_readonly_verified': True, 'schema_decryption_verified': True, **changes}
                adapter = KakaoReader(SimpleNamespace(snapshot=lambda **options: response))
                with patch.object(adapter, 'identity', side_effect=identities), self.assertRaises(SourceBlocked) as failure:
                    adapter.page('a', None)
                self.assertNotIsInstance(failure.exception, RoomUnavailable)

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

    def test_native_poll_owner_is_not_treated_as_an_unsupported_file_input(self):
        self.enroll()
        realtime = RealtimeCollection(self.root)
        realtime.update(True, 0)
        scheduler = CollectionScheduler(self.root)
        with patch.object(RealtimeCollection, 'signature', wraps=RealtimeCollection.signature) as signature, \
                patch.object(scheduler, '_voice_busy', return_value=False):
            realtime.scan(CollectionStore(self.root))
            self.assertEqual(signature.call_count, 0)
            result = scheduler.cycle(indexer=index_text)
            self.assertEqual(result['state'], 'complete', result)
            self.assertEqual(signature.call_count, 0)
        self.assertEqual(realtime.status()['inputs'], [])
        self.assertTrue(self.graph()['nodes'])

    def test_new_live_content_keeps_stream_identity_and_delivers_its_committed_receipt(self):
        self.enroll()
        source = self.source()
        source.step()
        source.step()
        query = {'projects': ['messenger'], 'activity': True}
        baseline = read_action(self.root, 'collection-graph', json.dumps(query))
        self.reader.pages[('a', None)] = ([item(3, '새로운 회의 일정과 계약 검토 자료가 도착했습니다.'), item(1)], False, None)
        self.assertEqual(source.step(mode='live')['state'], 'published')
        page = read_action(self.root, 'collection-graph', json.dumps({**query,
            'after': baseline['cursor'], 'stream_id': baseline['stream_id']}))
        self.assertFalse(page['reset'], page)
        self.assertEqual(page['stream_id'], baseline['stream_id'])
        self.assertEqual([event['kind'] for event in page['items']], ['added'])
        self.assertEqual(page['items'][0]['origin'], 'messenger-db:kakaotalk:live')
        self.assertNotEqual(page['content_revision'], baseline['content_revision'])

    def test_source_edit_without_collection_refreshes_content_but_not_the_journal_namespace(self):
        self.enroll()
        source = self.source();source.step();source.step()
        query = {'projects': ['messenger'], 'activity': True}
        before = read_action(self.root, 'collection-graph', json.dumps(query))
        self.reader.pages[('a', None)] = ([item(1, '수정되어 이전 저장 본문과 달라진 계약 자료입니다.')], False, None)
        after = read_action(self.root, 'collection-graph', json.dumps({**query,
            'after': before['cursor'], 'stream_id': before['stream_id']}))
        self.assertFalse(after['reset'])
        self.assertEqual(after['stream_id'], before['stream_id'])
        self.assertNotEqual(after['content_revision'], before['content_revision'])
        self.assertEqual(after['items'], [])
        self.assertEqual(len(self.graph()['nodes']), 1)

    def test_changed_account_still_replaces_activity_namespace_and_withholds_receipts(self):
        self.enroll();self.source().step()
        query = {'projects': ['messenger'], 'activity': True}
        before = read_action(self.root, 'collection-graph', json.dumps(query))
        self.reader.account = '456';self.reader.reads.clear()
        after = read_action(self.root, 'collection-graph', json.dumps({**query,
            'after': before['cursor'], 'stream_id': before['stream_id']}))
        self.assertTrue(after['reset'])
        self.assertNotEqual(after['stream_id'], before['stream_id'])
        self.assertEqual(after['items'], [])
        self.assertEqual(self.reader.reads, [])

    def test_invalid_or_foreign_stream_authority_does_not_bypass_current_source_guards(self):
        self.enroll();self.source().step()
        store = CollectionStore.open_existing(self.root, source_access=lambda *args: True,
            source_revision=lambda target: 'a' * 64, source_stream=lambda target: 'invalid')
        self.assertEqual(store.graph_page(projects=['messenger'])['nodes'], [])
        root = self.root
        class Foreign:
            def collection_source_root(self):return root / 'other'
            def revision(self, target):return 'a' * 64
        with self.assertRaisesRegex(ValueError, 'collection_source_root_mismatch'):
            CollectionStore.open_existing(self.root, source_stream=Foreign().revision)

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
        catalog = self.reader.catalog()
        catalog['rooms'].reverse()
        catalog['room_order'] = 'recent_activity_desc'
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
        with patch.object(self.reader, 'catalog', return_value=catalog), patch.object(self.reader, 'page', side_effect=revoke), patch.object(scheduler, '_voice_busy', return_value=False):
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
