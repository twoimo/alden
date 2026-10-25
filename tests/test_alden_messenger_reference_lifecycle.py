"""Actual owned stores and controlled sources for reference publication/withdrawal."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, encoded, read_action
from alden_collection_scheduler import CollectionScheduler
from alden_messenger_runtime import Frontier, SourceBlocked
from alden_messenger_references import reconcile
import alden_messenger_production as production
from tests.test_alden_messenger_production import FixtureReader
from tests.test_alden_messenger_frontier import item


class ReferenceReader(FixtureReader):
    reply = '7'
    unavailable = False
    reference_reads = 0

    def catalog(self):
        return {'account_id': self.account, 'complete': True, 'rooms': [{'id': '123', 'title': 'Owned reference fixture'}]}

    def reply_reference(self, room, message):
        self.reference_reads += 1
        if self.unavailable: raise SourceBlocked('fixture_source_unavailable')
        rows = {r['id']: r for r in self.pages[(room, None)][0]}
        if not rows[message]['visible']: return {'state': 'withheld', 'reason': 'reply_body_not_visible'}
        if self.reply is None: return {'state': 'not_admitted', 'reason': 'no_verified_reply_metadata'}
        if self.reply not in rows or not rows[self.reply]['visible']:
            return {'state': 'withheld', 'reason': 'quoted_content_withheld'}
        proof = {'platform': 'kakaotalk', 'account_id': self.account, 'room_id': room,
                 'source_message_id': message, 'target_message_id': self.reply,
                 'source_content_hash': hashlib.sha256(rows[message]['body'].encode()).hexdigest(),
                 'attachment_sha256': hashlib.sha256(self.reply.encode()).hexdigest()}
        return {'state': 'verified', **proof, 'reference_revision': hashlib.sha256(encoded(proof)).hexdigest(),
                'source': 'canonical-explicit-visible-reply-v1'}


class ReferenceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'state'; self.root.mkdir(mode=0o700)
        self.reader = ReferenceReader({('123', None): ([item(n, f'계약 검토 자료 {n}과 운영 회의 일정에 대한 구체적인 결과입니다.')
                                                       for n in (9, 8, 7)], False, None)})
        for target, attribute, value in [(production, 'canonical_reader', self.reader), (CollectionScheduler, '_voice_busy', False)]:
            p = patch.object(target, attribute, return_value=value); p.start(); self.addCleanup(p.stop)
        self.target = production.enroll(self.root, ['messenger'], reader=self.reader)['target_id']
        self.source = Frontier(self.root, 'kakaotalk', self.reader.account, self.reader, production=True)
        self.source.step()

    def graph(self):
        return read_action(self.root, 'collection-graph', json.dumps({'projects': ['messenger'], 'overview': True, 'limit': 1984}))

    def update(self, **options):
        return reconcile(self.root, self.target, '123', '9', reader=self.reader, **options)

    def activity(self, baseline):
        return read_action(self.root, 'collection-graph', json.dumps({'projects': ['messenger'], 'activity': True,
            'stream_id': baseline['stream_id'], 'after': baseline['cursor']}))

    def test_publish_then_replay_keeps_one_edge_and_one_structural_refresh(self):
        before = self.graph(); result = self.update(); self.assertEqual(result['state'], 'ready')
        graph = self.graph(); self.assertEqual(len(graph['edges']), 1); self.assertEqual(len(graph['nodes']), 3)
        self.assertEqual(graph['edges'][0]['relation'], 'replies-to')
        events = self.activity(before['activity_checkpoint'])
        self.assertTrue(events['refresh_required']); self.assertEqual(events['items'], [])
        self.assertFalse(events['reset'])
        self.assertFalse(self.update()['changed'])
        self.assertEqual(self.activity(events)['cursor'], events['cursor'])

    def test_same_body_changed_reference_hides_old_edge_before_writer_then_replaces_it(self):
        self.update(); before = self.graph(); old_edge = before['edges'][0]
        self.reader.reply = '8'
        stale = self.graph(); self.assertEqual(stale['edges'], [])
        self.assertEqual(stale['activity_checkpoint']['stream_id'], before['activity_checkpoint']['stream_id'])
        self.assertNotEqual(stale['activity_checkpoint']['content_revision'], before['activity_checkpoint']['content_revision'])
        self.update(); after = self.graph(); self.assertEqual(len(after['edges']), 1)
        self.assertNotEqual(after['edges'][0]['target'], old_edge['target'])
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM relation_versions').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 3)

    def test_withdrawn_reference_is_withheld_and_retirement_preserves_history(self):
        self.update(); self.reader.reply = None
        self.assertEqual(self.graph()['edges'], [])
        self.assertEqual(self.update()['state'], 'not_admitted')
        self.assertFalse(self.update()['changed'])
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM relations WHERE active=1').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM relation_versions').fetchone()[0], 1)

    def test_transient_reference_failure_keeps_claim_and_recovers_without_new_event(self):
        self.update(); before = self.graph()['activity_checkpoint']
        self.reader.unavailable = True
        self.assertEqual(self.graph()['edges'], [])
        with self.assertRaisesRegex(SourceBlocked, 'fixture_source_unavailable'): self.update()
        self.reader.unavailable = False
        self.assertEqual(len(self.graph()['edges']), 1)
        self.assertFalse(self.update()['changed'])
        self.assertEqual(self.activity(before)['cursor'], before['cursor'])

    def test_pending_endpoint_does_not_leave_old_edge_visible(self):
        self.update()
        self.reader.pages[('123', None)][0].append(item(6, '기존에 아직 수집하지 않은 계약 검토 자료와 운영 일정 설명입니다.'))
        self.reader.reply = '6'
        self.assertEqual(self.update()['state'], 'pending_endpoints')
        self.assertEqual(self.graph()['edges'], [])
        self.source.step(mode='reconcile')
        self.assertEqual(self.update()['state'], 'ready')
        self.assertEqual(len(self.graph()['edges']), 1)

    def test_commit_failure_rolls_back_claim_edge_and_refresh_then_retry_publishes_once(self):
        baseline = self.graph()['activity_checkpoint']
        @contextmanager
        def fail():
            raise OSError('owned commit gate failure')
            yield
        with self.assertRaisesRegex(OSError, 'owned commit gate failure'): self.update(publication_guard=fail)
        self.assertEqual(self.graph()['edges'], [])
        self.assertEqual(self.activity(baseline)['cursor'], baseline['cursor'])
        self.update(); self.assertEqual(len(self.graph()['edges']), 1)

    def test_pause_and_cancellation_stop_reference_reads(self):
        CollectionScheduler(self.root).pause(True)
        before = self.reader.reference_reads
        with self.assertRaisesRegex(SourceBlocked, 'paused'): self.update()
        self.assertEqual(self.reader.reference_reads, before)
        CollectionScheduler(self.root).pause(False)
        with self.assertRaises((SourceBlocked, RuntimeError)): self.update(cancelled=lambda: True)
        self.assertEqual(self.reader.reference_reads, before)

    def test_reference_change_during_publication_rolls_back(self):
        original = self.reader.reply_reference
        def change(room, message):
            result = original(room, message); self.reader.reply = '8'; return result
        with patch.object(self.reader, 'reply_reference', side_effect=change):
            with self.assertRaises(SourceBlocked): self.update()
        self.assertEqual(self.graph()['edges'], [])

    def test_reference_schema_is_opt_in_and_invalid_protocol_never_exposes_an_edge(self):
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], '3')
        self.update()
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], '4')
            db.execute("UPDATE meta SET value='invalid' WHERE key='reference_protocol'")
        with self.assertRaisesRegex(RuntimeError, 'reference_schema_invalid'):
            self.graph()

    def test_missing_reference_authority_cannot_fall_back_to_endpoint_only_checks(self):
        self.update()
        graph = read_action(self.root, 'collection-graph', json.dumps({'projects': ['messenger']}),
                            source_access=lambda *args: True, source_revision=lambda target: 'a' * 64)
        self.assertEqual(graph['edges'], [])

    def test_no_reference_observation_creates_no_claim_schema_or_backup(self):
        self.reader.reply = None
        self.assertFalse(self.update()['changed'])
        self.assertFalse(list((self.root / 'knowledge/collection').glob('collection.reference-schema-3-*')))
        with CollectionStore(self.root).database() as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], '3')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='messenger_references'").fetchone())

    def test_changed_stored_version_rebinds_reference_without_adding_another_logical_edge(self):
        self.update(); before = self.graph()['edges'][0]
        from alden_messenger_quality import POLICY
        body = self.reader.pages[('123', None)][0][0]['body']
        content = hashlib.sha256(body.encode()).hexdigest()
        CollectionStore(self.root).ingest(self.target, [{'platform': 'graph', 'original_id': 'messenger-content:' + content,
            'kind': 'knowledge-candidate', 'text': body, 'raw': {'text': body, 'policy': POLICY},
            'source': {'kind': 'messenger-exact-content-provenance', 'content_hash': content}}],
            processing_version='owned-new-projection', origin='messenger-policy:owned-fixture')
        self.assertEqual(self.graph()['edges'], [])
        self.assertTrue(self.update()['changed'])
        self.assertEqual(self.graph()['edges'][0]['id'], before['id'])
        self.assertEqual(len(self.graph()['edges']), 1)
        with CollectionStore(self.root).database() as db:
            event = db.execute("SELECT details FROM events WHERE stage='stored' AND json_extract(details,'$.change')='relations_changed' ORDER BY sequence DESC LIMIT 1").fetchone()
            self.assertEqual(json.loads(event[0])['revised'], 1)

    def test_relation_callback_root_mismatch_is_rejected(self):
        root = self.root
        class Foreign:
            def collection_source_root(self): return root / 'other'
            def check(self, *args): return True
        with self.assertRaisesRegex(ValueError, 'root_mismatch'):
            CollectionStore.open_existing(root, source_relation_access=Foreign().check)


if __name__ == '__main__': unittest.main()
