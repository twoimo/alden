"""Selected-result fences against real disposable stores, never private data."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from alden_abort import AbortController, AldenCancelled
from alden_collection import CollectionStore, identity
import alden_collection_retrieval as retrieval
import alden_corpus as corpus
import alden_knowledge_authority as authority


def context(result):
    return {'graph_facts': result['facts'], 'graph_provenance': result['fact_provenance'], 'quoted_history': []}


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.token = AbortController(self.root).token()

    def collection(self):
        self.store = CollectionStore(self.root)
        self.target = self.store.register(platform='graph', original_id='test', kind='source', label='test', projects=['one'])
        self.store.ingest(self.target, [{'platform': 'graph', 'original_id': 'a', 'label': 'needle', 'text': 'selected original'}])
        return self.lookup()

    def lookup(self, **options):
        return context(retrieval.retrieve(self.root, 'needle', projects=['one'], max_relations=0, **options))

    def capture(self, value):
        return authority.capture_authority(self.root, value, self.token)

    def verify(self, receipt):
        return authority.verify_authority(self.root, receipt, self.token)

    def changed(self, receipt):
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, '^' + authority.CHANGED + '$'):
            self.verify(receipt)

    def test_success_is_read_only_opaque_and_does_not_repeat_lookup(self):
        value = self.collection(); before = self.store.path.read_bytes()
        with patch.object(retrieval, 'retrieve', side_effect=AssertionError('second retrieval')), \
             patch.object(corpus, 'search', side_effect=AssertionError('second retrieval')):
            receipt = self.capture(value)
            for _ in range(3):
                self.assertIsNone(self.verify(receipt))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertNotIn('selected original', repr(receipt))
        with self.assertRaises(TypeError): pickle.dumps(receipt)

    def test_permission_revoked_after_lookup_before_capture(self):
        value = self.collection()
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.target,))
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, '^' + authority.CHANGED + '$'):
            self.capture(value)

    def test_permission_revoked_after_capture(self):
        receipt = self.capture(self.collection())
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (self.target,))
        self.changed(receipt)

    def test_membership_withdrawn(self):
        receipt = self.capture(self.collection())
        with self.store.database() as db:
            db.execute("UPDATE memberships SET availability='unavailable' WHERE target_id=?", (self.target,))
        self.changed(receipt)

    def test_document_withdrawn(self):
        receipt = self.capture(self.collection())
        with self.store.database() as db:
            db.execute("UPDATE documents SET availability='unavailable'")
        self.changed(receipt)

    def test_native_source_revision_and_access_are_rechecked_by_owner(self):
        current = {'revision': 'a' * 64, 'allowed': True}
        callbacks = {'source_access': lambda *args: current['allowed'],
                     'source_revision': lambda *args: current['revision'],
                     'source_stream': None, 'source_context': None, 'source_relation_access': None}
        with patch('alden_collection.source_callbacks', return_value=callbacks):
            store = CollectionStore(self.root)
            target = store.register(platform='telegram', original_id='fixture', kind='source', label='fixture', projects=['one'])
            store.ingest(target, [{'platform': 'graph', 'original_id': 'bound', 'label': 'needle', 'text': 'permission bound'}])
            value = context(retrieval.retrieve(self.root, 'needle', projects=['one'], max_relations=0))
            self.assertEqual(len(value['graph_facts']), 1)
            receipt = self.capture(value); self.verify(receipt)
            current['revision'] = 'b' * 64
            self.changed(receipt)
            current['revision'] = 'a' * 64; current['allowed'] = False
            self.changed(receipt)

    def test_version_advance_does_not_adopt_new_result(self):
        value = self.collection(); receipt = self.capture(value)
        self.store.ingest(self.target, [{'platform': 'graph', 'original_id': 'a', 'label': 'needle', 'text': 'new unselected bytes'}])
        self.changed(receipt)
        self.assertEqual(value['graph_facts'], ['needle: selected original'])

    def test_raw_blob_corruption(self):
        value = self.collection(); receipt = self.capture(value)
        proof = value['graph_provenance'][0]
        (self.store.blobs / (proof['note_hash'] + '.json')).write_text('{"changed":true}')
        self.changed(receipt)

    def test_blob_fifo_cannot_block_read_or_supply_authority(self):
        value = self.collection(); receipt = self.capture(value)
        path = self.store.blobs / (value['graph_provenance'][0]['note_hash'] + '.json')
        path.unlink(); os.mkfifo(path)
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.UNAVAILABLE):
            self.verify(receipt)

    def test_metadata_tampering_without_version_advance(self):
        receipt = self.capture(self.collection())
        with self.store.database() as db:
            db.execute("UPDATE versions SET metadata='{}'")
        self.changed(receipt)

    def test_unselected_document_change_does_not_invalidate_selected_receipt(self):
        receipt = self.capture(self.collection())
        self.store.ingest(self.target, [{'platform': 'graph', 'original_id': 'unselected', 'label': 'other', 'text': 'unrelated'}])
        self.verify(receipt)

    def test_compacted_matching_source_revocation_is_not_lost(self):
        self.collection()
        for name in ('b', 'c'):
            self.store.ingest(self.target, [{'original_id': name, 'label': 'needle', 'text': 'matching body',
                'raw': {'platform': 'threads', 'type': 'source', 'id': name,
                        'author': 'fixture', 'handle': '@fixture', 'date': '2026-10-02',
                        'localOriginalText': 'matching body'}}])
        value = self.lookup(max_entities=3)
        matching = next(p['matching_sources']['sources'][0] for p in value['graph_provenance'] if 'matching_sources' in p)
        receipt = self.capture(value)
        with self.store.database() as db:
            db.execute("UPDATE memberships SET availability='unavailable' WHERE document_id=?", (matching['entity_id'],))
        self.changed(receipt)

    def relation(self):
        self.collection()
        self.store.ingest(self.target, [
            {'platform': 'graph', 'original_id': 'left', 'label': 'needle left', 'text': 'left'},
            {'platform': 'graph', 'original_id': 'right', 'label': 'needle right', 'text': 'right'}], relations=[
            {'source_platform': 'graph', 'source_id': 'left', 'target_platform': 'graph', 'target_id': 'right',
             'type': 'mentions', 'evidence': {'declared': 'fixture'}}])
        result = retrieval.retrieve(self.root, 'needle', projects=['one'], max_entities=1, max_relations=1,
                                    also=[identity('graph', 'left')])
        self.assertEqual(result['fact_provenance'][-1]['fact_type'], 'relation')
        return context(result)

    def test_relation_retraction(self):
        receipt = self.capture(self.relation())
        with self.store.database() as db: db.execute('UPDATE relations SET active=0')
        self.changed(receipt)

    def test_relation_binding_corruption(self):
        receipt = self.capture(self.relation())
        with self.store.database() as db: db.execute("UPDATE relations SET evidence='{}'")
        self.changed(receipt)

    def test_relation_unselected_endpoint_version_advance(self):
        receipt = self.capture(self.relation())
        self.store.ingest(self.target, [{'platform': 'graph', 'original_id': 'right', 'label': 'needle right', 'text': 'changed endpoint'}])
        self.changed(receipt)

    def test_selected_text_mismatch_fails_at_capture(self):
        value = self.collection(); value['graph_facts'][0] = 'invented fact'
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.CHANGED): self.capture(value)

    def test_unknown_legacy_and_overbudget_proofs_fail_closed(self):
        value = self.collection()
        variants = []
        for key in ('note_hash', 'source_version', 'source_memberships', 'fact_truncated'):
            variant = copy.deepcopy(value); variant['graph_provenance'][0].pop(key); variants.append(variant)
        unknown = copy.deepcopy(value); unknown['graph_provenance'][0]['source_kind'] = 'legacy_graph'; variants.append(unknown)
        variants.append({'graph_facts': ['x'] * 8, 'graph_provenance': [{}] * 8})
        variants.append({'quoted_history': [{}] * 5})
        for variant in variants:
            with self.subTest(variant=list(variant)), self.assertRaisesRegex(authority.KnowledgeAuthorityError, '^local_knowledge_source_(unavailable|changed)$'):
                self.capture(variant)
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.UNAVAILABLE): self.verify(object())

    def test_root_token_and_inode_are_bound_even_for_empty_context(self):
        receipt = self.capture({})
        self.verify(receipt)
        other = self.root / 'other'; other.mkdir()
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.CHANGED):
            authority.verify_authority(other, receipt, self.token)
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.CHANGED):
            authority.verify_authority(self.root, receipt, AbortController(self.root).token())

    def test_root_replacement_is_detected(self):
        root = self.root / 'bound'; root.mkdir(mode=0o700)
        token = AbortController(root).token()
        receipt = authority.capture_authority(root, {}, token)
        root.rename(self.root / 'retired'); root.mkdir(mode=0o700)
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.CHANGED):
            authority.verify_authority(root, receipt, token)

    def test_context_mutation_cannot_replace_captured_sources(self):
        value = self.collection(); receipt = self.capture(value)
        value['graph_facts'].clear(); value['graph_provenance'].clear()
        with self.store.database() as db: db.execute("UPDATE target_projects SET permission='denied'")
        self.changed(receipt)

    def test_cancellation_before_and_during_collection_read(self):
        value = self.collection(); original = retrieval._verified_source
        def cancel(*args):
            result = original(*args); self.token.cancel(); return result
        with patch.object(retrieval, '_verified_source', side_effect=cancel), self.assertRaises(AldenCancelled):
            self.capture(value)
        with self.assertRaises(AldenCancelled): self.capture({})

    def test_abort_epoch_invalidates_receipt_after_resume(self):
        receipt = self.capture(self.collection()); control = AbortController(self.root)
        control.abort(); control.resume_after_human_action()
        with self.assertRaises(AldenCancelled): self.verify(receipt)

    def corpus(self, text='needle original', *, kind=1, actor='7', is_self=0):
        account = 'a' * 64
        folder = self.root / 'knowledge/corpus' / account; folder.mkdir(parents=True)
        self.corpus_path = folder / 'context.sqlite3'
        with sqlite3.connect(self.corpus_path) as db:
            db.executescript('''CREATE TABLE corpus_meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE alden_messages(id INTEGER PRIMARY KEY,chat TEXT,chat_id TEXT,log_id TEXT,
                author_id TEXT,user_name TEXT,message TEXT,date TEXT,is_self INTEGER,message_type INTEGER,
                source TEXT,UNIQUE(chat_id,log_id));
                CREATE VIRTUAL TABLE context_messages_fts USING fts5(message,user_name,chat,
                content='alden_messages',content_rowid='id');''')
            db.executemany('INSERT INTO corpus_meta VALUES(?,?)', [('account', account), ('snapshot', 'fixture'), ('complete', '1')])
            key = f'kakao:{account}:room:42'
            db.execute('INSERT INTO alden_messages VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (1, key, '42', str(2**60), actor, 'fixture', text, '2026-10-02', is_self, kind, key))
        (folder.parent / 'current.json').write_text(json.dumps({'schema_version': 1, 'account': account, 'snapshot': 'fixture'}))
        rows = corpus.search(self.root, '', chat_id='42', limit=4)['items']
        return {'quoted_history': rows}

    def test_corpus_success_and_edit_beyond_truncated_quote(self):
        value = self.corpus('x' * 1100); receipt = self.capture(value)
        self.verify(receipt); self.assertTrue(value['quoted_history'][0]['truncated'])
        with sqlite3.connect(self.corpus_path) as db:
            db.execute('UPDATE alden_messages SET message=?', ('x' * 1050 + 'changed',))
        self.changed(receipt)

    def test_corpus_feed_role_is_checked_from_actual_current_message(self):
        value = self.corpus('{"feedType":2,"member":{"id":7}}', kind=0)
        self.assertEqual(value['quoted_history'][0]['source_role'], 'system_history')
        receipt = self.capture(value); self.verify(receipt)
        with sqlite3.connect(self.corpus_path) as db: db.execute('UPDATE alden_messages SET message_type=1')
        self.changed(receipt)

    def test_corpus_date_author_room_role_and_account_changes(self):
        value = self.corpus(); receipt = self.capture(value)
        for column, changed in [('date', '2026-10-03'), ('author_id', '8'), ('chat', 'different'), ('is_self', 1)]:
            with sqlite3.connect(self.corpus_path) as db:
                prior = db.execute('SELECT ' + column + ' FROM alden_messages').fetchone()[0]
                db.execute('UPDATE alden_messages SET ' + column + '=?', (changed,))
            self.changed(receipt)
            with sqlite3.connect(self.corpus_path) as db: db.execute('UPDATE alden_messages SET ' + column + '=?', (prior,))
        with sqlite3.connect(self.corpus_path) as db: db.execute("UPDATE corpus_meta SET value=? WHERE key='account'", ('b' * 64,))
        self.changed(receipt)

    def test_corpus_edited_after_search_before_capture(self):
        value = self.corpus()
        with sqlite3.connect(self.corpus_path) as db: db.execute("UPDATE alden_messages SET message='new bytes'")
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.CHANGED): self.capture(value)

    def test_corpus_absent_publication_never_uses_legacy_database(self):
        value = self.corpus(); (self.root / 'knowledge/corpus/current.json').unlink()
        with self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.UNAVAILABLE): self.capture(value)

    def test_corpus_publication_switch_during_read_is_rejected(self):
        value = self.corpus(); original = corpus._published_rooms
        @contextmanager
        def changing(*args, **kwargs):
            with original(*args, **kwargs) as publication: yield publication
            pointer = self.root / 'knowledge/corpus/current.json'
            manifest = json.loads(pointer.read_text()); manifest['snapshot'] = 'replaced'
            pointer.write_text(json.dumps(manifest))
        with patch.object(corpus, '_published_rooms', changing), self.assertRaisesRegex(authority.KnowledgeAuthorityError, authority.CHANGED):
            self.capture(value)

    def test_sqlite_progress_cancellation_is_preserved(self):
        value = self.corpus(); original = corpus._published_rooms
        @contextmanager
        def progressing(*args, **kwargs):
            with original(*args, **kwargs) as (db, account):
                class DB:
                    def set_progress_handler(inner, callback, steps):
                        db.set_progress_handler(callback, steps)
                        if callback is not None:
                            self.token.cancel()
                            db.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n').fetchone()
                    def execute(inner, *a, **k): return db.execute(*a, **k)
                yield DB(), account
        with patch.object(corpus, '_published_rooms', progressing), self.assertRaises(AldenCancelled):
            self.capture(value)


def _osk_scenario(case):
    """Each child owns one real pinned SDK root, matching its process contract."""
    import alden_osk as osk
    import alden_osk_retrieval as oskr
    with TemporaryDirectory() as temporary:
        root = Path(temporary).resolve(); token = AbortController(root).token()
        source = {'ok': True, 'indexed_at': 42, 'stale': False, 'nodes': [
            {'id': 'memory:one', 'label': 'needle one', 'category': 'memory', 'description': 'original premise',
             'facts': ['selected fixture'], 'evidence': {'kind': 'fixture', 'source_event_ids': ['fixture:1']}},
            {'id': 'memory:two', 'label': 'needle two', 'category': 'memory', 'description': 'other premise',
             'facts': [], 'evidence': {'kind': 'fixture'}}], 'edges': []}
        osk.synchronize(root, source)
        home = osk._home(root); cp = osk._read_json(home / 'sync.json')
        contract, graph, secrets, writer = osk._load_engine(root)
        one, two = cp['managed']['memory:one'], cp['managed']['memory:two']
        path = home / 'vault' / one['space'] / (one['title'] + '.md')
        if case in ('relation', 'title_relation'):
            writer.update_node(one['osk_id'], add_edges={'derived-from': [two['osk_id']]}, expect_hash=one['written_hash'])
            if case == 'relation':
                # The SDK accepts both stored stable IDs and title references.
                # Its writer may choose the latter; exercise the supported real
                # canonical ID representation without inventing a proof.
                path.write_text(path.read_text().replace(two['title'], two['osk_id']))
            one['written_hash'] = osk.digest(path.read_bytes()); osk._save(home / 'sync.json', cp)
        if case == 'alias_relation':
            writer.update_node(one['osk_id'], body='Premise [[' + two['title'] + '|shown alias]]', expect_hash=one['written_hash'])
            one['written_hash'] = osk.digest(path.read_bytes()); osk._save(home / 'sync.json', cp)
        if case == 'unmanaged':
            created = writer.create_node('unmanaged needle', 'human canonical fixture', 'unmanaged selected body', 'agent', space=osk.SOURCE_SPACE)
            assert all(item['osk_id'] != created['id'] for item in cp['managed'].values())
        result = oskr.retrieve(root, 'needle', max_entities=3, max_relations=2)
        value = context(result)
        assert value['graph_facts']
        if case == 'unmanaged':
            assert any(p.get('note_id') == created['id'] for p in value['graph_provenance'])
        with patch.object(oskr, '_snapshot', wraps=oskr._snapshot) as snapshots, \
             patch.object(oskr, 'retrieve', side_effect=AssertionError('no second retrieval')):
            receipt = authority.capture_authority(root, value, token)
            authority.verify_authority(root, receipt, token)
            assert snapshots.call_count == 2
            if case in ('success', 'unmanaged'): return
            if case == 'retraction':
                one['active'] = False; osk._save(home / 'sync.json', cp)
            elif case == 'edit':
                path.write_text(path.read_text().replace('original premise', 'changed canonical bytes'))
            elif case == 'source':
                one['source']['evidence']['source_event_ids'] = ['replacement-event']; osk._save(home / 'sync.json', cp)
            elif case in ('relation', 'title_relation', 'alias_relation'):
                assert any(p['fact_type'] == 'relation' for p in value['graph_provenance'])
                name = two['osk_id'] if case == 'relation' else two['title']
                path.write_bytes(path.read_bytes().replace(name.encode(), b'withdrawn-reference'))
            elif case in ('duplicate_id', 'duplicate_stem'):
                duplicate = path.with_name('duplicate needle.md')
                data = path.read_bytes()
                if case == 'duplicate_stem':
                    duplicate = path.parent / 'copies' / path.name
                    duplicate.parent.mkdir()
                    two_path = home / 'vault' / two['space'] / (two['title'] + '.md')
                    data = two_path.read_bytes()
                duplicate.write_bytes(data)
            elif case == 'cancel':
                original = authority.os.read
                def cancel_read(*args):
                    data = original(*args); token.cancel(); return data
                with patch.object(authority.os, 'read', side_effect=cancel_read):
                    try: authority.verify_authority(root, receipt, token)
                    except AldenCancelled: return
                    raise AssertionError('file cancellation was swallowed')
            else: raise AssertionError(case)
            try: authority.verify_authority(root, receipt, token)
            except authority.KnowledgeAuthorityError as error:
                assert str(error) == (authority.UNAVAILABLE if case.startswith('duplicate_') else authority.CHANGED), str(error)
            else: raise AssertionError('source mutation accepted')


class OskAuthorityTests(unittest.TestCase):
    def scenario(self, name):
        completed = subprocess.run([sys.executable, '-E', '-B', '-s', '-c',
            'from tests.test_alden_knowledge_authority import _osk_scenario; _osk_scenario(' + repr(name) + ')'],
            cwd=SCRIPTS.parent, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_real_canonical_success_uses_owner_snapshot_without_retrieval(self): self.scenario('success')
    def test_unmanaged_canonical_note_remains_supported(self): self.scenario('unmanaged')
    def test_canonical_retraction(self): self.scenario('retraction')
    def test_canonical_edit(self): self.scenario('edit')
    def test_canonical_source_binding_change(self): self.scenario('source')
    def test_canonical_relation_retraction(self): self.scenario('relation')
    def test_canonical_title_relation_success_and_retraction(self): self.scenario('title_relation')
    def test_canonical_link_display_alias_success_and_retraction(self): self.scenario('alias_relation')
    def test_duplicate_canonical_id_is_rejected_by_owner(self): self.scenario('duplicate_id')
    def test_duplicate_canonical_stem_is_rejected_by_owner(self): self.scenario('duplicate_stem')
    def test_canonical_bounded_file_cancellation(self): self.scenario('cancel')


if __name__ == '__main__':
    unittest.main()
