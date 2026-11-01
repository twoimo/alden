"""Actual SQLite store/frontier commit boundaries with controlled readers."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import sys
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'scripts'), str(BASE / 'docs/evaluation/messenger-stream-20261021')]
import messenger_candidate as candidate
from alden_collection import encoded, identity
from tests.test_alden_messenger_frontier import Reader as FixtureReader, item


class Reader(FixtureReader):
    def authorize(self, room):
        if self.blocked or room in getattr(self, 'denied', set()):
            raise candidate.SourceBlocked('context_consent_expired')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir='/private/tmp')
        self.addCleanup(temp.cleanup)
        self.root = candidate.create_root(Path(temp.name) / 'state')
        self.text = '회의 자료 검토 일정은 화요일 오후 3시로 결정되었습니다.'
        self.reader = Reader({('a', None): ([item(1, self.text)], False, None)})
        self.engine = candidate.Frontier(self.root, 'kakaotalk', 'owner', self.reader)
        self.engine.seed({'account_id': 'owner', 'complete': True,
                          'rooms': [{'id': 'a', 'title': 'controlled source'}]})
        self.projects = [candidate.PROJECT]
        self.start = self.engine.store.activity_page(projects=self.projects)
        self.document = identity('graph', 'messenger-content:' + hashlib.sha256(self.text.encode()).hexdigest())

    def read_after(self, page):
        return self.engine.store.activity_page(projects=self.projects,
            after=page['cursor'], stream_id=page['stream_id'])

    def current(self):
        with self.engine.store.database() as db:
            row = db.execute('SELECT v.* FROM memberships m JOIN versions v ON v.id=m.current_version '
                'WHERE m.target_id=? AND m.document_id=?', (self.engine.target, self.document)).fetchone()
        return row['id'], json.loads(row['metadata'])

    def interrupt_after_store(self):
        def crash(): raise SystemExit('controlled interruption after store commit')
        with self.assertRaises(SystemExit): self.engine.step(after_publish=crash)
        return self.current()

    def test_activity_read_between_commits_does_not_permanently_lose_the_receipt(self):
        between = []
        self.engine.step(after_publish=lambda: between.append(self.read_after(self.start)))
        final = self.read_after(between[0])
        delivered = between[0]['items'] + final['items']
        self.assertEqual([r['kind'] for r in delivered], ['added'])
        self.assertEqual(delivered[0]['document_id'], self.document)
        self.assertFalse(between[0]['reset']); self.assertFalse(final['reset'])
        self.assertEqual(self.read_after(final)['items'], [])

    def test_completed_store_with_prepared_frontier_survives_crash_and_replay(self):
        def crash(): raise SystemExit('controlled interruption after store commit')
        with self.assertRaises(SystemExit): self.engine.step(after_publish=crash)
        self.assertEqual(self.engine.status()['prepared_pages'], 1)
        between = self.read_after(self.start)
        self.engine = candidate.Frontier(self.root, 'kakaotalk', 'owner', self.reader)
        self.engine.step()
        final = self.read_after(between)
        self.assertEqual(len(between['items'] + final['items']), 1)
        self.assertFalse(final['reset'])
        self.assertEqual(self.engine.status()['prepared_pages'], 0)

    def test_authority_requires_the_current_membership_version(self):
        self.engine.step()
        version, metadata = self.current()
        self.assertTrue(self.engine._source_access(self.engine.target, self.document, version, metadata))
        self.assertFalse(self.engine._source_access(self.engine.target, self.document,
                                                    'version:not-current', metadata))

    def test_merely_prepared_page_and_an_unrelated_complete_run_do_not_authorize(self):
        with self.engine.database() as db:
            room = dict(db.execute('SELECT * FROM rooms WHERE id=?', ('a',)).fetchone())
        self.engine._prepare(room, 'backfill', self.reader.page('a', None))
        digest = hashlib.sha256(self.text.encode()).hexdigest()
        record = {'platform': 'graph', 'original_id': 'messenger-content:' + digest,
                  'text': self.text, 'source': {'kind': 'messenger-exact-content-provenance',
                                              'content_hash': digest}}
        self.engine.store.ingest(self.engine.target, [record], run_id='unrelated-complete',
                                 processing_version=candidate.POLICY)
        version, metadata = self.current()
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))
        self.assertEqual(self.read_after(self.start)['items'], [])

    def test_interrupted_prepared_origin_still_rechecks_account_and_room_grant(self):
        version, metadata = self.interrupt_after_store()
        self.reader.blocked = True
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))
        self.reader.blocked = False; self.reader.account = 'different-current-account'
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))
        self.reader.account = 'owner'
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))
        self.engine.step()
        self.assertTrue(self.engine._source_access(self.engine.target, self.document, version, metadata))

    def test_stale_prepared_frontier_epoch_is_not_receipt_authority(self):
        version, metadata = self.interrupt_after_store()
        with self.engine.database() as db:
            state = json.loads(db.execute('SELECT frontier FROM rooms WHERE id=?', ('a',)).fetchone()[0])
            state['source_epoch'] = 3
            db.execute('UPDATE rooms SET frontier=? WHERE id=?', (encoded(state).decode(), 'a'))
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))

    def test_prepared_checksum_mismatch_and_current_projection_tampering_stay_withheld(self):
        version, metadata = self.interrupt_after_store()
        with self.engine.store.database() as db:
            db.execute('UPDATE versions SET body=? WHERE id=?', ('different source body', version))
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))
        with self.engine.store.database() as db:
            db.execute('UPDATE versions SET body=? WHERE id=?', (self.text, version))
        with self.engine.database() as db:
            payload = json.loads(db.execute("SELECT payload FROM pages WHERE state='prepared'").fetchone()[0])
            payload['items'][0]['body'] += ' changed'
            db.execute("UPDATE pages SET payload=? WHERE state='prepared'", (encoded(payload).decode(),))
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))

    def test_prepared_reads_do_not_create_events_or_authorize_a_withdrawn_membership(self):
        version, metadata = self.interrupt_after_store()
        with self.engine.store.database() as db:
            before = [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY sequence')]
        for _ in range(5):
            self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))
        with self.engine.store.database() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY sequence')])
            db.execute("UPDATE memberships SET availability='removed' WHERE target_id=? AND document_id=?",
                       (self.engine.target, self.document))
        self.assertFalse(self.engine._source_access(self.engine.target, self.document, version, metadata))

    def test_ordinary_next_live_commit_preserves_stream_generation(self):
        self.engine.step()
        first = self.read_after(self.start)
        self.reader.pages[('a', None)] = ([item(2, self.text + ' 이후 변경된 자료를 공유합니다.'),
                                          item(1, self.text)], False, None)
        between = []
        self.engine.step(mode='live', after_publish=lambda: between.append(self.read_after(first)))
        last = self.read_after(between[0])
        self.assertEqual(len(between[0]['items'] + last['items']), 1)
        self.assertFalse(between[0]['reset']); self.assertFalse(last['reset'])
        self.assertEqual(first['stream_id'], last['stream_id'])

    def test_crash_after_frontier_commit_releases_the_durable_outbox_after_restart(self):
        with patch.object(self.engine, '_release_activity', side_effect=SystemExit('before readiness receipt')):
            with self.assertRaises(SystemExit): self.engine.step()
        with self.engine.database() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE state='complete'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM activity_releases WHERE state='pending'").fetchone()[0], 1)
        before = self.read_after(self.start)
        self.assertEqual(before['items'], [])
        self.engine = candidate.Frontier(self.root, 'kakaotalk', 'owner', self.reader)
        self.assertEqual(self.engine.step()['state'], 'activity_released')
        after = self.read_after(before)
        self.assertEqual([r['kind'] for r in after['items']], ['added'])
        self.assertFalse(after['reset'])

    def test_crash_after_readiness_commit_does_not_duplicate_the_receipt_on_retry(self):
        original = self.engine.store.release_activity
        def committed(*args, **options):
            original(*args, **options)
            raise SystemExit('after readiness commit before outbox settle')
        with patch.object(self.engine.store, 'release_activity', side_effect=committed):
            with self.assertRaises(SystemExit): self.engine.step()
        before = self.read_after(self.start)
        self.assertEqual(len(before['items']), 1)
        self.engine = candidate.Frontier(self.root, 'kakaotalk', 'owner', self.reader)
        self.assertEqual(self.engine.step()['state'], 'activity_released')
        self.assertEqual(self.read_after(before)['items'], [])

    def test_readiness_cancellation_preserves_the_pending_outbox(self):
        with patch.object(self.engine, '_release_activity', side_effect=SystemExit('after frontier commit')):
            with self.assertRaises(SystemExit): self.engine.step()
        with self.engine.database() as db:
            job = db.execute("SELECT page FROM activity_releases WHERE state='pending'").fetchone()[0]
        with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
            self.engine._release_activity(job, cancelled=lambda: True)
        self.assertEqual(self.read_after(self.start)['items'], [])
        self.assertEqual(self.engine.status()['pending_activity'], 1)
        self.engine.step()
        self.assertEqual(len(self.read_after(self.start)['items']), 1)

    def test_blocked_outbox_backoff_leaves_another_room_collectable(self):
        with patch.object(self.engine, '_release_activity', side_effect=SystemExit('after frontier commit')):
            with self.assertRaises(SystemExit): self.engine.step()
        self.reader.denied = {'a'}
        self.reader.pages[('b', None)] = ([item(2, self.text + ' 별도 자료입니다.')], False, None)
        self.engine.seed({'account_id': 'owner', 'complete': True,
                          'rooms': [{'id': r, 'title': r} for r in ('a', 'b')]})
        self.assertEqual(self.engine.step()['state'], 'blocked')
        self.assertEqual(self.engine.status()['blocked_activity'], 1)
        self.assertEqual(self.engine.step()['room_id'], 'b')

    def test_unchanged_live_read_does_not_add_another_readiness_receipt(self):
        self.engine.step()
        baseline = self.read_after(self.start)
        self.assertEqual(len(baseline['items']), 1)
        self.assertEqual(self.engine.step(mode='live')['state'], 'unchanged')
        self.assertEqual(self.read_after(baseline)['items'], [])
        self.assertEqual(self.engine.status()['pending_activity'], 0)

    def test_exact_crlf_identity_and_lf_search_projection_retain_current_rag_provenance(self):
        from alden_collection_retrieval import retrieve
        text = self.text + '\r\n회의 자료의 원문 줄바꿈을 그대로 보존합니다.\r\n'
        self.reader.pages[('a', None)] = ([item(1, text)], False, None)
        self.engine.step()
        document = identity('graph', 'messenger-content:' + hashlib.sha256(text.encode()).hexdigest())
        authority = candidate.MessengerAuthority([self.engine])
        result = retrieve(self.root, '회의', projects=self.projects, max_entities=1, max_relations=0,
                          source_access=authority.access, source_revision=authority.revision)
        self.assertTrue(result['facts'])
        self.assertEqual(result['fact_provenance'][0]['entity_id'], document)
        with self.engine.store.database() as db:
            row = db.execute('SELECT v.body FROM memberships m JOIN versions v ON v.id=m.current_version '
                             'WHERE m.document_id=?', (document,)).fetchone()
        self.assertEqual(row['body'], text.replace('\r\n', '\n'))
        with self.engine.database() as db:
            raw = json.loads(db.execute('SELECT payload FROM pages').fetchone()[0])['items'][0]['body']
        self.assertEqual(raw, text)


if __name__ == '__main__': unittest.main()
