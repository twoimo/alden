"""Protected source reads require current authority, independent of collecting."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, identity, read_action


class MessengerAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'state'
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='telegram', original_id='controlled-owner',
            kind='local-db', label='controlled messenger', projects=['project'])
        self.record = {'platform':'graph','original_id':'controlled-secret',
            'label':'revocable knowledge','text':'revocable evidence retained from a controlled source',
            'raw':{'text':'revocable evidence retained from a controlled source'}}
        self.store.ingest(self.target, [self.record])
        self.document = identity('graph', self.record['original_id'])

    def assert_hidden(self, store):
        self.assertEqual(store.search('revocable', projects=['project']), [])
        self.assertEqual(store.graph(projects=['project'])['nodes'], [])
        self.assertEqual(store.graph_page(projects=['project'])['nodes'], [])
        self.assertEqual(store.graph_references(projects=['project'])['nodes'], [])
        with self.assertRaisesRegex(ValueError, 'collection_graph_focus_not_in_scope'):
            store.graph_page(projects=['project'], focus=self.document, details=True)
        self.assertEqual(store.events(projects=['project'])['items'], [])
        history = store.recent_events(projects=['project'])
        self.assertEqual(history['items'], [])
        self.assertEqual(history['summary']['total'], 0)
        self.assertEqual(store.activity_page(projects=['project'], after=0,
            stream_id=history['stream_id'])['items'], [])

    def test_generic_and_readonly_paths_default_to_withholding_protected_sources(self):
        self.assert_hidden(self.store)
        self.assert_hidden(CollectionStore.open_existing(self.root))
        response = read_action(self.root, 'collection-graph', json.dumps({'projects':['project']}))
        self.assertEqual(response['nodes'], [])
        self.assertEqual(len(self.store.projects()), 1)  # Metadata remains discoverable.

    def test_current_authority_controls_reads_even_while_the_collector_is_paused(self):
        authorized = True
        calls = []
        def access(target, document, version, metadata):
            calls.append((target, document, version))
            return authorized
        self.store.source_access = access
        self.assertEqual(len(self.store.search('revocable', projects=['project'])), 1)
        self.assertEqual(len(self.store.graph_page(projects=['project'])['nodes']), 1)
        with self.store.database() as db:
            db.execute('UPDATE targets SET enabled=0 WHERE id=?', (self.target,))
            versions = db.execute('SELECT count(*) FROM versions').fetchone()[0]
        authorized = False
        self.assert_hidden(self.store)
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], versions)
        self.assertTrue(calls)

    def test_a_different_permitted_membership_survives_with_no_hidden_source_context(self):
        other = self.store.register(platform='graph', original_id='independent-source', kind='sqlite',
                                    label='independent', projects=['project'])
        self.store.ingest(other, [self.record])
        result = self.store.graph_page(projects=['project'])
        self.assertEqual(len(result['nodes']), 1)
        self.assertEqual(len(self.store.search('revocable', projects=['project'])), 1)
        contexts = result['nodes'][0]['source_contexts']
        self.assertEqual({value['target_id'] for value in contexts}, {other})
        self.assertIn(self.target, {value['id'] for value in result['targets']})

    def test_authority_lost_during_a_read_prevents_returning_the_already_selected_body(self):
        calls = 0
        def access(*args):
            nonlocal calls
            calls += 1
            return calls == 1
        self.store.source_access = access
        with self.assertRaisesRegex(RuntimeError, 'collection_source_authorization_changed'):
            self.store.search('revocable', projects=['project'])

    def test_non_boolean_or_failing_authorizer_is_not_an_allow_decision(self):
        for callback in (lambda *args: None, lambda *args: 1,
                         lambda *args: (_ for _ in ()).throw(RuntimeError('source unavailable'))):
            with self.subTest(callback=callback):
                self.store.source_access = callback
                self.assertEqual(self.store.search('revocable', projects=['project']), [])

    def test_invalid_configured_source_revision_fails_closed(self):
        self.store.source_access = lambda *args: True
        for revision in (True, lambda *args: None, lambda *args: 'not-a-digest',
                         lambda *args: (_ for _ in ()).throw(RuntimeError('scope unavailable'))):
            with self.subTest(revision=revision):
                self.store.source_revision = revision
                self.assertEqual(self.store.search('revocable', projects=['project']), [])

    def test_scope_change_during_a_metadata_only_read_prevents_returning_a_stale_checkpoint(self):
        calls = 0
        def revision(target):
            nonlocal calls
            calls += 1
            return ('a' if calls == 1 else 'b') * 64
        self.store.source_revision = revision
        with self.assertRaisesRegex(RuntimeError, 'collection_source_authorization_changed'):
            self.store.activity_page(projects=['project'])

    def test_withholding_retains_versions_raw_bytes_and_current_memberships(self):
        with self.store.database() as db:
            before = [tuple(row) for row in db.execute('SELECT * FROM memberships')]
        blobs = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.store.blobs.iterdir()}
        self.assert_hidden(self.store)
        with self.store.database() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM memberships')], before)
        self.assertEqual({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.store.blobs.iterdir()}, blobs)

    def test_denied_details_never_open_the_retained_source_blob(self):
        with patch.object(self.store, '_verified_source_blob', side_effect=AssertionError('must not open source')):
            with self.assertRaisesRegex(ValueError, 'collection_graph_focus_not_in_scope'):
                self.store.graph_page(projects=['project'], focus=self.document, details=True)

    def test_borrowed_layout_snapshot_preserves_the_same_default_withholding(self):
        import alden_layout_affinity as layout
        with layout.read_transactions(self.root) as owner:
            self.assertEqual(owner.store.graph_page(projects=['project'])['nodes'], [])
            self.assertEqual(owner.store.graph_references(projects=['project'])['nodes'], [])
            self.assertEqual(owner.metadata['denied_write_attempts'], 0)

    def test_messenger_target_kind_cannot_turn_off_the_source_gate(self):
        with self.store.database() as db:
            db.execute("UPDATE targets SET kind='source' WHERE id=?", (self.target,))
        self.assert_hidden(self.store)

    def test_hidden_relation_evidence_stays_hidden_when_nodes_have_other_memberships(self):
        second = {**self.record, 'original_id':'second', 'raw':{'text':'second source'}}
        edge = {'source_platform':'graph','source_id':self.record['original_id'],
                'target_platform':'graph','target_id':'second','type':'reference',
                'evidence':{'reason':'private controlled relation'}}
        self.store.ingest(self.target, [self.record,second], relations=[edge])
        public = self.store.register(platform='graph', original_id='independent-source', kind='source',
                                     label='independent', projects=['project'])
        self.store.ingest(public, [self.record,second])
        for read in (self.store.graph, self.store.graph_page):
            result = read(projects=['project'])
            self.assertEqual(len(result['nodes']), 2)
            self.assertEqual(result['edges'], [])
            self.assertNotIn('private controlled relation', json.dumps(result))
        self.store.source_access = lambda *args: True
        self.assertEqual(len(self.store.graph_page(projects=['project'])['edges']), 1)


class FrontierAccessTests(unittest.TestCase):
    def add_kakao_origin(self):
        from tests.test_alden_messenger_frontier import Reader, item
        text = '회의 자료 검토 일정은 화요일 오후 3시로 결정되었습니다.'
        reader = Reader({('k',None):([item(1,text)],False,None)})
        reader.account = 'controlled-kakao-owner'
        engine = self.candidate.Frontier(self.root, 'kakaotalk', reader.account, reader)
        engine.seed({'account_id':reader.account,'complete':True,'rooms':[{'id':'k','title':'controlled'}]})
        engine.step()
        return reader, engine

    def setUp(self):
        from tests.test_alden_messenger_frontier import candidate
        self.candidate = candidate
        temporary = TemporaryDirectory(dir='/private/tmp'); self.addCleanup(temporary.cleanup)
        self.root = candidate.create_root(Path(temporary.name) / 'frontier')
        self.denied, self.body_calls = set(), []
        self.scopes = {room:{'since':'2026-10-20','until':'2026-10-23'} for room in ('a','b')}
        self.account = 'owner'
        self.text = '회의 자료 검토 일정은 화요일 오후 3시로 결정되었습니다.'
        self.grants = {room:{'controlled_authorization_revision':'fixture-v1'} for room in ('a','b')}
        def invoke(args):
            if args == ['probe']:
                return {'status':'ready_readonly','read_only':True,'owner_id':self.account}
            self.body_calls.append(args)
            room = args[args.index('--peer-id')+1]
            return {'status':'ok','owner_id':self.account,'peer_id':room,'content_returned':True,
                    'more':False,'messages':[{'id':room,'peer_id':room,'timestamp':1792551600,
                    'text':self.text}]}
        def authorize(owner, room, since, until):
            if room in self.denied:
                raise candidate.SourceBlocked('telegram_context_consent_expired')
            return self.grants[room]
        self.reader = candidate.TelegramReader(invoke, self.scopes, authorize)
        self.engine = candidate.Frontier(self.root, 'telegram', self.account, self.reader)
        self.engine.seed({'account_id':self.account,'complete':True,
                          'rooms':[{'id':room,'title':room} for room in ('a','b')]})
        self.engine.step(); self.engine.step()

    def test_expiry_hides_every_body_path_without_a_collector_tick_or_another_source_read(self):
        self.assertEqual(len(self.engine.store.search('회의', projects=[self.candidate.PROJECT])), 1)
        before = list(self.body_calls)
        self.denied.update(('a','b'))
        self.assertEqual(self.engine.store.search('회의', projects=[self.candidate.PROJECT]), [])
        self.assertEqual(self.engine.store.graph_page(projects=[self.candidate.PROJECT])['nodes'], [])
        self.assertEqual(self.engine.store.recent_events(projects=[self.candidate.PROJECT])['items'], [])
        reader = CollectionStore.open_existing(self.root, source_access=self.engine._source_access)
        self.assertEqual(reader.graph(projects=[self.candidate.PROJECT])['nodes'], [])
        self.assertEqual(self.body_calls, before)

    def test_one_expired_origin_does_not_withhold_another_currently_authorized_origin(self):
        self.denied.add('a')
        self.assertEqual(len(self.engine.store.search('회의', projects=[self.candidate.PROJECT])), 1)
        self.denied.add('b')
        self.assertEqual(self.engine.store.search('회의', projects=[self.candidate.PROJECT]), [])
        with self.engine.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM message_heads WHERE state='candidate'").fetchone()[0], 2)

    def test_revocation_changes_history_and_activity_scope_without_fake_events(self):
        store, projects = self.engine.store, [self.candidate.PROJECT]
        history = store.recent_events(projects=projects)
        checkpoint = store.activity_page(projects=projects)
        self.assertTrue(history['items'])
        with store.database() as db:
            count = db.execute('SELECT count(*) FROM events').fetchone()[0]
        before = len(self.body_calls)
        self.denied.update(('a','b'))
        current = store.recent_events(projects=projects, after=history['cursor'], stream_id=history['stream_id'])
        self.assertTrue(current['reset'])
        self.assertNotEqual(current['stream_id'], history['stream_id'])
        self.assertEqual(current['items'], [])
        self.assertEqual(current['summary']['total'], 0)
        activity = store.activity_page(projects=projects, after=checkpoint['cursor'], stream_id=checkpoint['stream_id'])
        self.assertTrue(activity['reset'])
        self.assertEqual(activity['items'], [])
        self.assertEqual(store.graph_page(projects=projects)['activity_checkpoint']['stream_id'], current['stream_id'])
        self.assertEqual(len(self.body_calls), before)
        with store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], count)

    def test_new_knowledge_keeps_authority_scope_and_delivers_real_activity(self):
        store, projects = self.engine.store, [self.candidate.PROJECT]
        checkpoint = store.activity_page(projects=projects)
        self.text = '회의 운영 원칙은 금요일부터 새로운 일정으로 변경되었습니다.'
        self.assertEqual(self.engine.step(mode='live')['state'], 'published')
        page = store.activity_page(projects=projects, after=checkpoint['cursor'], stream_id=checkpoint['stream_id'])
        self.assertFalse(page['reset'])
        self.assertEqual(page['stream_id'], checkpoint['stream_id'])
        self.assertEqual([item['kind'] for item in page['items']], ['added'])
        self.assertTrue(page['items'][0]['origin'].endswith(':live'))

    def test_withheld_source_retirement_refreshes_caches_without_exposing_a_receipt(self):
        from tests.test_alden_messenger_frontier import item
        reader, engine = self.add_kakao_origin()
        projects = [self.candidate.PROJECT]
        before = engine.store.recent_events(projects=projects, target_id=engine.target)
        self.assertTrue(before['items'])
        reader.pages[('k',None)] = ([item(1,visible=False)],False,None)
        engine.step(mode='live')
        current = engine.store.recent_events(projects=projects, target_id=engine.target,
            after=before['cursor'], stream_id=before['stream_id'])
        self.assertTrue(current['reset'])
        self.assertEqual(current['items'], [])
        activity = engine.store.activity_page(projects=projects, target_id=engine.target,
            after=before['cursor'], stream_id=before['stream_id'])
        self.assertFalse(activity['reset'])
        self.assertEqual(activity['items'], [])
        self.assertTrue(activity['refresh_required'])
        self.assertGreater(activity['cursor'], before['cursor'])

    def test_current_account_and_date_scope_changes_cannot_reuse_old_origin_authority(self):
        for room in self.scopes:
            self.scopes[room] = {'since':'2026-10-24','until':'2026-10-25'}
        self.assertEqual(self.engine.store.search('회의', projects=[self.candidate.PROJECT]), [])
        self.scopes.update({room:{'since':'2026-10-20','until':'2026-10-23'} for room in ('a','b')})
        self.account = 'other'
        self.assertEqual(self.engine.store.graph(projects=[self.candidate.PROJECT])['nodes'], [])

    def test_renewed_grant_requires_fresh_canonical_observation_without_new_body_versions(self):
        self.denied.update(('a','b'))
        self.assertEqual(self.engine.store.search('회의', projects=[self.candidate.PROJECT]), [])
        self.denied.clear()
        self.grants = {room:{'controlled_authorization_revision':'fixture-v2'} for room in ('a','b')}
        before_calls = len(self.body_calls)
        self.assertEqual(self.engine.store.search('회의', projects=[self.candidate.PROJECT]), [])
        self.assertEqual(len(self.body_calls), before_calls)
        with self.engine.store.database() as db:
            versions = db.execute('SELECT count(*) FROM versions').fetchone()[0]
            events = db.execute('SELECT count(*) FROM events').fetchone()[0]
        self.assertEqual(self.engine.step(mode='live')['state'], 'unchanged')
        self.assertEqual(len(self.body_calls), before_calls+1)
        self.assertEqual(len(self.engine.store.search('회의', projects=[self.candidate.PROJECT])), 1)
        with self.engine.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], versions)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], events)

    def test_shared_version_uses_each_current_target_origin_instead_of_first_publisher_account(self):
        reader, other = self.add_kakao_origin()
        self.denied.update(('a','b'))
        def access(*args):
            return self.engine._source_access(*args) or other._source_access(*args)
        store = CollectionStore.open_existing(self.root, source_access=access)
        result = store.search('회의', projects=[self.candidate.PROJECT])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['metadata']['source']['platform'], 'telegram')
        graph = store.graph_page(projects=[self.candidate.PROJECT])
        self.assertEqual(graph['nodes'][0]['source_target'], other.target)
        self.assertEqual({c['target_id'] for c in graph['nodes'][0]['source_contexts']}, {other.target})
        with store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 1)
        reader.blocked = True
        self.assertEqual(store.search('회의', projects=[self.candidate.PROJECT]), [])

    def test_a_valid_page_checksum_does_not_authorize_another_account_payload(self):
        import hashlib
        from alden_collection import encoded
        _, other = self.add_kakao_origin()
        self.assertEqual(len(other.store.search('회의', projects=[self.candidate.PROJECT])), 1)
        with other.database() as db:
            row = db.execute('SELECT p.* FROM pages p JOIN message_heads h ON h.page=p.id').fetchone()
            payload = json.loads(row['payload']);payload['account'] = 'different-account'
            job = hashlib.sha256(encoded([payload,json.loads(row['next_frontier'])])).hexdigest()
            db.execute('INSERT INTO pages VALUES(?,?,?,?,?)',
                       (job,row['room'],encoded(payload).decode(),row['next_frontier'],'complete'))
            db.execute('UPDATE message_heads SET page=?', (job,))
        self.assertEqual(other.store.search('회의', projects=[self.candidate.PROJECT]), [])


if __name__ == '__main__':
    unittest.main()
