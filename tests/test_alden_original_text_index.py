"""Generated original fixtures only; real CollectionStore authority is retained."""
import json
import os
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_original_text_index as index
from alden_abort import AldenCancelled
from alden_collect import capture_source, collect_target
from alden_collection import CollectionStore, digest, encoded, normalized_body
from alden_file_set import SourceBodyError


class ProjectionTests(unittest.TestCase):
    def assert_code(self, code, call):
        with self.assertRaises(index.OriginalTextIndexError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_error_allowlist_cannot_echo_paths(self):
        for value in ('/private/generated-fixture', 'unknown', None, [], True):
            error = index.OriginalTextIndexError(value)
            self.assertEqual(error.code, 'collection_original_index_invalid')
            self.assertEqual(str(error), error.code)

    def test_fts_snippet_centers_head_tail_and_boundary_without_chunks(self):
        body = 'HEADWORD\n' + 'ordinary text\n' * 1600 + 'crossboundary phraseword\n' + 'ordinary\n' * 2000 + 'TAILWORD\n'
        for word in ('HEADWORD', 'TAILWORD', 'crossboundary phraseword'):
            with self.subTest(word=word):
                start, end, method = index._snippet_span(body, word, index._terms(word), lambda: False)
                self.assertTrue(body[start:end].lower() in word.lower())
                self.assertEqual(method, 'fts5-snippet')
                if word == 'TAILWORD':
                    self.assertGreater(start, 16000)

    def test_fts_phrase_crosses_a_16000_byte_boundary(self):
        body = 'x ' * 7997 + 'boundaryleft boundaryright' + ' z' * 80
        self.assertLess(body.index('boundaryleft'), 16000)
        self.assertGreater(body.index('boundaryright'), 16000)
        start, end, method = index._snippet_span(body, 'boundaryleft boundaryright',
            '"boundaryleft boundaryright"', lambda: False)
        self.assertEqual(body[start:end], 'boundaryleft boundaryright')
        self.assertEqual(method, 'fts5-snippet')

    def test_unicode_query_uses_whole_nfc_projection(self):
        body = normalized_body('start\n' + '가😀 e\u0301\r\n' * 3000 + '끝의 re\u0301sume\u0301단어\r\n')
        terms = index._terms('re\u0301sume\u0301단어')
        start, end, _ = index._snippet_span(body, 're\u0301sume\u0301단어', terms, lambda: False)
        self.assertIn('résumé단어', body[start:end])

    def test_absent_match_has_no_first_body_fallback(self):
        self.assert_code('collection_original_index_stale', lambda:
            index._snippet_span('first retained body', 'absent', index._terms('absent'), lambda: False))

    def test_korean_subtoken_and_integer_literal_selection(self):
        for body, query, method in [('앞말서울시민뒷말', '서울', 'korean-literal'),
                ('price 12,345; identifier 345; other 1345', '345', 'integer-literal')]:
            with self.subTest(query=query):
                # Integer FTS often already matches; both paths must bind an
                # actual body match and grouped runs are not bare integers.
                literal = index._literal_span(body, query, index._terms(query))
                self.assertEqual(literal[2], method)
                self.assertEqual(body[literal[0]:literal[1]], query)
        self.assertIsNone(index._literal_span('12,345 1345 345.0', '345', index._terms('345')))

    def assert_range(self, original, start, end, mapping=None):
        body, raw = normalized_body(original), original.encode()
        proof = index._original_range(original, raw, start, end, body[start:end], lambda: False)
        selected = raw[proof['start_bytes']:proof['end_bytes']]
        self.assertEqual(proof['sha256'], digest(selected))
        if proof['mapping'] == 'exact_projection_span':
            self.assertEqual(normalized_body(selected.decode()), body[start:end])
        else:
            self.assertIn(body[start:end], normalized_body(selected.decode()))
        if mapping:
            self.assertEqual(proof['mapping'], mapping)
        return proof

    def test_exact_range_preserves_crlf_lone_cr_astral_and_utf8(self):
        original = '😀alpha\r\n가나다\r末尾\nlast'
        body = normalized_body(original)
        for start in range(len(body)):
            for end in range(start + 1, len(body) + 1):
                self.assert_range(original, start, end, 'exact_projection_span')

    def test_changed_nfc_endpoint_lines_are_explicit_covering_ranges(self):
        original = 'first\r\ne\u0301 query 😀\r\nlast\n'
        body = normalized_body(original)
        start = body.index('query')
        proof = self.assert_range(original, start, start + 5, 'covering_original_lines')
        self.assertEqual(original.encode()[proof['start_bytes']:proof['end_bytes']], 'e\u0301 query 😀\r\n'.encode())

    def test_changed_interior_line_keeps_unchanged_endpoint_exact(self):
        original = 'first\r\ne\u0301 interior\r\nlast\n'
        self.assert_range(original, 2, len(normalized_body(original)) - 2, 'exact_projection_span')

    def test_deterministic_generated_unicode_ranges(self):
        generator = random.Random(81601)
        for _ in range(100):
            original = ''.join(generator.choice(('가😀\r\n', 'e\u0301\n', '\u1100\u1161\r', 'alpha\n', '\tZ'))
                for _ in range(8))
            body = normalized_body(original)
            start = generator.randrange(len(body))
            end = generator.randrange(start + 1, len(body) + 1)
            self.assert_range(original, start, end)

    def test_marker_collision_rejected_in_pure_projection_validation(self):
        for marker in ('\x01', '\x02', '\x03'):
            raw = ('body' + marker).encode()
            material = {'data': raw, 'text': raw.decode(), 'input_bytes': len(raw), 'label': 'label',
                'binding': {'document_id': 'd', 'target_id': 't', 'version': 'v',
                    'raw_sha256': 'a' * 64, 'input_sha256': digest(raw)},
                'capture': {'manifest_sha256': 'b' * 64, 'source_sha256': 'c' * 64},
                'config_sha256': 'd' * 64, '_context': object()}
            self.assert_code('collection_original_index_invalid', lambda: index._material_metadata(material))

    def test_candidate_sql_is_bound_and_has_no_global_limit(self):
        terms = '"quoted" OR "값"'
        sql, parameters = index.candidate_sql('original_search', terms)
        self.assertEqual(parameters, ('body : (' + terms + ')', index.BODY_NORMALIZATION_VERSION))
        self.assertNotIn(terms, sql)
        self.assertNotIn('LIMIT', sql.upper())
        for alias in ('main', 'temp', 'other', 'original_search;DROP TABLE x'):
            self.assert_code('collection_original_index_invalid', lambda: index.candidate_sql(alias, terms))


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = SimpleNamespace(root=self.root)
        self.path = self.root / 'retrieval.sqlite3'
        self.db = sqlite3.connect(':memory:', uri=True)
        self.addCleanup(self.db.close)

    def seed(self):
        with sqlite3.connect(self.path) as db:
            for ddl in index._DDL.values():
                db.execute(ddl)
            db.executemany('INSERT INTO file_original_meta VALUES(?,?)', index._RECEIPT.items())

    def test_missing_index_lookup_creates_nothing(self):
        before = list(self.root.iterdir())
        self.assertIsNone(index.attach_original_index(self.store, self.db))
        self.assertEqual(before, list(self.root.iterdir()))

    def test_legacy_only_cache_is_not_migrated(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE texts(value TEXT)')
        before = self.path.read_bytes()
        self.assertIsNone(index.attach_original_index(self.store, self.db))
        self.assertEqual(before, self.path.read_bytes())

    def test_absent_policy_in_active_read_transaction_keeps_readonly_alias(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE texts(value TEXT)')
            db.execute('CREATE TABLE vectors(value BLOB)')
        self.db.execute('BEGIN')
        self.db.execute('SELECT * FROM sqlite_master').fetchall()
        self.assertIsNone(index.attach_original_index(self.store, self.db))
        self.assertIn('original_search', [row[1] for row in self.db.execute('PRAGMA database_list')])
        self.assertEqual(self.db.execute('SELECT count(*) FROM original_search.texts').fetchone()[0], 0)

    def test_attached_cache_is_read_only_and_metadata_functions_are_canonical(self):
        self.seed()
        self.assertEqual(index.attach_original_index(self.store, self.db), 'original_search')
        with self.assertRaises(sqlite3.OperationalError):
            self.db.execute("INSERT INTO original_search.file_original_meta VALUES('new','forbidden')")
        raw = '{"z": 2, "a": "값"}'
        self.assertEqual(self.db.execute('SELECT alden_original_config_sha(?)', (raw,)).fetchone()[0],
            digest(encoded(json.loads(raw))))
        capture = {'schema': 1, 'manifest': 'a' * 64 + '.json', 'sha256': 'b' * 64, 'byte_scope': index._BYTE_SCOPE}
        cursor = encoded({'source_capture': capture}).decode()
        self.assertEqual(self.db.execute('SELECT alden_original_capture_manifest(?),alden_original_capture_source(?)',
            (cursor, cursor)).fetchone(), ('a' * 64, 'b' * 64))
        for bad in ('not-json', 'null', '[]', '{}'):
            self.assertEqual(self.db.execute('SELECT alden_original_capture_manifest(?),alden_original_capture_source(?)',
                (bad, bad)).fetchone(), (None, None))
        for malformed in ({**capture, 'schema': True}, {**capture, 'byte_scope': 'unknown'},
                {**capture, 'extra': True}):
            raw = encoded({'source_capture': malformed}).decode()
            self.assertEqual(self.db.execute('SELECT alden_original_capture_manifest(?),alden_original_capture_source(?)',
                (raw, raw)).fetchone(), (None, None))

    def test_policy_partial_schema_and_corrupt_cache_fail_closed(self):
        for kind in ('partial', 'policy', 'corrupt'):
            with self.subTest(kind=kind):
                self.path.unlink(missing_ok=True)
                if kind == 'corrupt':
                    self.path.write_bytes(b'not a database\n')
                elif kind == 'partial':
                    with sqlite3.connect(self.path) as db:
                        db.execute(index._DDL['file_original_meta'])
                else:
                    self.seed()
                    with sqlite3.connect(self.path) as db:
                        db.execute("UPDATE file_original_meta SET value='unknown' WHERE key='policy'")
                with self.assertRaises(index.OriginalTextIndexError) as caught:
                    index.attach_original_index(self.store, self.db)
                self.assertEqual(caught.exception.code, 'collection_original_index_invalid')

    def test_index_and_sidecar_symlinks_are_rejected(self):
        external = self.root / 'other.sqlite3'
        external.write_bytes(b'generated')
        self.path.symlink_to(external)
        with self.assertRaises(index.OriginalTextIndexError):
            index.attach_original_index(self.store, self.db)
        self.path.unlink()
        self.seed()
        Path(str(self.path) + '-wal').symlink_to(external)
        with self.assertRaises(index.OriginalTextIndexError):
            index.attach_original_index(self.store, self.db)


_PARENT_READY = all(callable(getattr(CollectionStore, name, None)) for name in (
    '_original_text_material', '_original_text_session', '_original_text_inputs', '_verify_original_text_context'))


@unittest.skipUnless(_PARENT_READY, 'parent private original-text APIs not ready')
class OriginalIndexIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('ALDEN_TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = CollectionStore(self.root)
        self.collections = []

    def collection(self, texts=None, *, project='one', capture=True, labels=None):
        texts = texts or {'one': 'HEADWORD\n' + 'ordinary 가😀 text\r\n' * 1700 + 'TAILWORD\n'}
        folder = self.root / ('originals-' + str(len(self.collections))); folder.mkdir()
        entries, raw = [], {}
        for key, value in texts.items():
            name = key + '.md'
            data = value.encode() if isinstance(value, str) else value
            (folder / name).write_bytes(data)
            raw[key] = data
            entry = {'id': key, 'path': name}
            if labels and key in labels:
                entry['label'] = labels[key]
            entries.append(entry)
        manifest = folder / 'files.json'; manifest.write_bytes(encoded({'schema': 1, 'files': entries}))
        target = self.store.register(platform='files', original_id='originals-' + str(len(self.collections)),
            kind='file-set', label='Generated fixture', projects=[project],
            config={'adapter': 'file-set', 'root': str(folder), 'path': str(manifest)})
        collect_target(self.store, target)
        if capture:
            capture_source(self.store, target)
        value = dict(target=target, folder=folder, manifest=manifest, raw=raw, project=project)
        self.collections.append(value)
        return value

    def refresh(self, projects=None):
        with self.store.target_lock('collection-dense-retrieval'):
            with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
                return index.refresh_original_index(self.store, projects or ['one'], db)

    def candidates(self, query='TAILWORD', **kwargs):
        reader = CollectionStore.open_existing(self.root)
        return index.scoped_original_candidates(reader, ['one'], query, **kwargs)

    def resolve(self, candidate, query='TAILWORD', **kwargs):
        return index.resolve_original_excerpt(self.store, ['one'], candidate, query, **kwargs)

    def assert_proof(self, result, raw):
        text, proof = result['text'], result['proof']
        self.assertEqual(set(result), {'text', 'proof', '_context'})
        self.assertEqual(set(proof), {'schema', 'kind', 'binding', 'capture', 'input_bytes',
            'projection', 'excerpt', 'original_range', 'selection'})
        self.assertEqual(set(proof['binding']), set(index._BINDING))
        self.assertEqual(set(proof['capture']), set(index._CAPTURE))
        body = normalized_body(raw.decode())
        self.assertEqual(proof['projection'], {'policy': index.BODY_NORMALIZATION_VERSION,
            'sha256': digest(body.encode()), 'characters': len(body)})
        span = proof['excerpt']
        self.assertEqual(text, body[span['start_character']:span['end_character']])
        self.assertEqual(span['sha256'], digest(text.encode()))
        self.assertEqual(proof['input_bytes'], len(raw))
        self.assertEqual(proof['binding']['input_sha256'], digest(raw))
        region = proof['original_range']
        selected = raw[region['start_bytes']:region['end_bytes']]
        self.assertEqual(region['sha256'], digest(selected))
        if region['mapping'] == 'exact_projection_span':
            self.assertEqual(normalized_body(selected.decode()), text)
        else:
            self.assertIn(text, normalized_body(selected.decode()))

    def test_whole_document_head_tail_unicode_and_16000_boundary(self):
        body = 'HEADWORD\n' + 'a ' * 7980 + 'boundarytoken phraseword\r\n' + '가😀\r\n' * 4000 + 're\u0301sume\u0301단어 TAILWORD\n'
        fixture = self.collection({'one': body}); receipt = self.refresh()
        self.assertEqual(receipt['state'], 'ready')
        self.assertEqual(receipt['eligible'], 1)
        self.assertEqual(receipt['indexed'] + receipt['reused'], 1)
        self.assertEqual(receipt['embeddings_generated'], 0)
        for query in ('HEADWORD', 'boundarytoken phraseword', 're\u0301sume\u0301단어', 'TAILWORD'):
            with self.subTest(query=query):
                candidates = self.candidates(query)
                self.assertEqual(len(candidates), 1)
                result = self.resolve(candidates[0], query)
                self.assert_proof(result, fixture['raw']['one'])
                self.assertTrue(any(word.lower() in result['text'].lower()
                    for word in normalized_body(query).split()))
                if query == 'TAILWORD':
                    self.assertGreater(result['proof']['excerpt']['start_character'], 16000)

    def test_query_does_not_create_missing_cache_or_reopen_originals(self):
        fixture = self.collection()
        cache = self.store.root / 'retrieval.sqlite3'
        cache.unlink(missing_ok=True)
        self.assertEqual(self.candidates(), [])
        self.assertFalse(cache.exists())
        self.refresh()
        for path in fixture['folder'].iterdir():
            path.unlink()
        before = {p.name: digest(p.read_bytes()) for p in (self.store.root / 'source-captures').iterdir()}
        result = self.resolve(self.candidates()[0])
        self.assertIn('TAILWORD', result['text'])
        after = {p.name: digest(p.read_bytes()) for p in (self.store.root / 'source-captures').iterdir()}
        self.assertEqual(before, after)

    def test_reuse_does_not_rewrite_fts_or_existing_cache_tables(self):
        self.collection(); self.refresh()
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
            db.execute('CREATE TABLE worker_legacy_sentinel(value TEXT)')
            db.execute("INSERT INTO worker_legacy_sentinel VALUES('preserve')")
            db.commit()
            before = db.total_changes
            receipt = index.refresh_original_index(self.store, ['one'], db)
            self.assertEqual(receipt['reused'], 1)
            self.assertEqual(db.total_changes, before)
            self.assertEqual(db.execute('SELECT * FROM worker_legacy_sentinel').fetchall(), [('preserve',)])

    def test_current_project_permission_is_checked_before_limit(self):
        self.collection({'one': 'TAILWORD\n'}, project='one')
        hidden = self.collection({'two': 'TAILWORD ' * 100}, project='other')
        self.refresh(['one', 'other'])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (hidden['target'],))
        candidates = self.candidates(limit=1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['target_id'], self.collections[0]['target'])

    def test_label_only_candidates_excluded_and_ordinary_search_preserved(self):
        self.collection({'one': 'ordinary retained body\n'}, labels={'one': 'LABELONLYWORD 서울시민 345'})
        self.refresh()
        reader = CollectionStore.open_existing(self.root)
        for query in ('LABELONLYWORD', '서울', '345'):
            with self.subTest(query=query):
                self.assertEqual(self.candidates(query), [])
                self.assertEqual(len(reader.search(query, projects=['one'])), 1)

    def test_body_integer_and_korean_subtoken_candidates_resolve(self):
        fixture = self.collection({'one': 'ordinary\n앞말서울시민뒤말\nidentifier 345 value\n'})
        self.refresh()
        for query in ('서울', '345'):
            result = self.resolve(self.candidates(query)[0], query)
            self.assertIn(query, result['text'])
            self.assert_proof(result, fixture['raw']['one'])

    def test_candidate_sql_optional_query_includes_body_literal_without_duplicates(self):
        self.collection({'one': '앞말서울시민뒤말 345\n'})
        self.refresh()
        reader = CollectionStore.open_existing(self.root)
        with reader.database() as db:
            alias = index.attach_original_index(reader, db)
            for query in ('서울', '345'):
                sql, args = index.candidate_sql(alias, index._terms(query), query=query)
                self.assertEqual(len(db.execute(sql, args).fetchall()), 1)

    def test_duplicate_cached_ids_do_not_consume_current_limit(self):
        self.collection(); self.refresh()
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
            columns = index._COLUMNS[1:]
            saved = list(db.execute('SELECT ' + ','.join(columns) + ' FROM file_original_text').fetchone())
            for number in range(150):
                row = saved[:]
                row[1] = 'historical-target-' + str(number)
                rowid = db.execute('INSERT INTO file_original_text(' + ','.join(columns) + ') VALUES('
                    + ','.join('?' for _ in row) + ')', row).lastrowid
                db.execute('INSERT INTO file_original_fts(rowid,label,body) VALUES(?,?,?)', (rowid, row[-4], row[-1]))
        self.assertEqual(len(self.candidates(limit=1)), 1)
        self.assertEqual(self.candidates(limit=1)[0]['target_id'], self.collections[0]['target'])

    def test_current_config_capture_and_raw_hash_reject_stale_slots_before_limit(self):
        valid = self.collection({'one': 'TAILWORD\n'})
        stale = self.collection({'two': 'TAILWORD ' * 100})
        self.refresh()
        for field in ('config_sha256', 'capture_manifest_sha256', 'capture_source_sha256', 'raw_sha256'):
            with self.subTest(field=field):
                with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
                    old = db.execute('SELECT ' + field + ' FROM file_original_text WHERE target_id=?', (stale['target'],)).fetchone()[0]
                    db.execute('UPDATE file_original_text SET ' + field + '=? WHERE target_id=?', ('0' * 64, stale['target']))
                self.assertEqual(self.candidates(limit=1)[0]['target_id'], valid['target'])
                with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
                    db.execute('UPDATE file_original_text SET ' + field + '=? WHERE target_id=?', (old, stale['target']))

    def test_actual_current_config_and_capture_swap_reject_selected_candidate(self):
        fixture = self.collection(); self.refresh(); candidate = self.candidates()[0]
        with self.store.database() as db:
            config, cursor = tuple(db.execute('SELECT config,cursor FROM targets WHERE id=?', (fixture['target'],)).fetchone())
        for field, changed in (('config', encoded({**json.loads(config), 'changed': True}).decode()),
                ('cursor', '{}')):
            with self.subTest(field=field):
                with self.store.database() as db:
                    db.execute('UPDATE targets SET ' + field + '=? WHERE id=?', (changed, fixture['target']))
                self.assertEqual(self.candidates(), [])
                with self.assertRaises((SourceBodyError, index.OriginalTextIndexError)):
                    self.resolve(candidate)
                with self.store.database() as db:
                    db.execute('UPDATE targets SET config=?,cursor=? WHERE id=?', (config, cursor, fixture['target']))

    def test_revoked_material_and_late_final_context_guard_fail(self):
        fixture = self.collection(); self.refresh()
        candidate = self.candidates()[0]
        result = self.resolve(candidate)
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (fixture['target'],))
        self.assertEqual(self.candidates(), [])
        with self.assertRaises((SourceBodyError, RuntimeError)):
            self.store._verify_original_text_context(result['_context'], ['one'])
        with self.assertRaises((SourceBodyError, index.OriginalTextIndexError)):
            self.resolve(candidate)

    def test_refresh_rechecks_authority_inside_atomic_row_transaction(self):
        fixture = self.collection()
        (self.store.root / 'retrieval.sqlite3').unlink(missing_ok=True)
        # A wrapper injects a real revocation, then invokes the real guard.
        # No authority decision or verifier return value is mocked away.
        guard = self.store._verify_original_text_context
        calls = 0
        def revoke_before_commit(context, projects):
            nonlocal calls
            calls += 1
            if calls == 2:
                with self.store.database() as source:
                    source.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (fixture['target'],))
            return guard(context, projects)
        with patch.object(self.store, '_verify_original_text_context', side_effect=revoke_before_commit):
            with self.assertRaises((SourceBodyError, index.OriginalTextIndexError)):
                self.refresh()
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM file_original_text').fetchone()[0], 0)

    def test_projection_hash_and_absent_actual_query_never_use_cached_body(self):
        self.collection(); self.refresh(); candidate = self.candidates()[0]
        wrong = {**candidate, 'projection_sha256': '0' * 64}
        with self.assertRaises(index.OriginalTextIndexError) as caught:
            self.resolve(wrong)
        self.assertEqual(caught.exception.code, 'collection_original_index_stale')
        with self.assertRaises(index.OriginalTextIndexError) as caught:
            self.resolve(candidate, 'thereisnosuchword')
        self.assertEqual(caught.exception.code, 'collection_original_index_stale')

    def test_final_guard_and_session_reuse_detect_capture_archive_change(self):
        fixture = self.collection(); self.refresh(); candidate = self.candidates()[0]
        session = self.store._original_text_session()
        result = self.resolve(candidate, _session=session)
        cursor = json.loads(self.store.target(fixture['target'])['cursor'])
        packet = self.store.root / 'source-captures' / (cursor['source_capture']['sha256'] + '.json')
        packet.write_bytes(packet.read_bytes() + b' ')
        with self.assertRaises(SourceBodyError) as caught:
            self.store._verify_original_text_context(result['_context'], ['one'])
        self.assertEqual(caught.exception.code, 'alden_source_body_integrity')
        with self.assertRaises(SourceBodyError):
            self.resolve(candidate, _session=session)

    def test_resolve_maximum_and_remaining_context_1_through_1200(self):
        self.collection(); self.refresh(); candidate = self.candidates()[0]
        for size in (1, 2, 7, 48, 1199, 1200):
            result = self.resolve(candidate, max_characters=size)
            self.assertEqual(len(result['text']), min(size, result['proof']['projection']['characters']))
            self.assertEqual(result['proof']['excerpt']['sha256'], digest(result['text'].encode()))
        for size in (0, 1201, True, 1.5):
            with self.assertRaises(index.OriginalTextIndexError):
                self.resolve(candidate, max_characters=size)

    def test_nfc_changed_lines_disclose_covering_original_range(self):
        fixture = self.collection({'one': 'start\r\n' + 'e\u0301 TAILWORD 😀\r\n' + 'last\n'})
        self.refresh(); result = self.resolve(self.candidates()[0], max_characters=10)
        self.assert_proof(result, fixture['raw']['one'])
        self.assertEqual(result['proof']['original_range']['mapping'], 'covering_original_lines')

    def test_current_time_filters_precede_limit(self):
        self.collection(); self.refresh()
        candidate = self.candidates()[0]
        with self.store.database() as db:
            timestamp = db.execute('SELECT collected_at FROM versions WHERE id=?', (candidate['version'],)).fetchone()[0]
        self.assertEqual(len(self.candidates(time_from=timestamp, time_to=timestamp)), 1)
        self.assertEqual(self.candidates(time_from=timestamp + 1), [])
        for kwargs in ({'time_from': float('nan')}, {'time_from': 2, 'time_to': 1}, {'time_to': True}):
            with self.assertRaises(index.OriginalTextIndexError):
                self.candidates(**kwargs)

    def test_missing_capture_partial_build_does_not_prune_old_rows(self):
        fixture = self.collection(); self.refresh()
        with self.store.database() as db:
            db.execute("UPDATE targets SET cursor='{}' WHERE id=?", (fixture['target'],))
        receipt = self.refresh()
        self.assertEqual(receipt['state'], 'partial')
        self.assertEqual(receipt['missing_capture'], 1)
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM file_original_text').fetchone()[0], 1)
        self.assertEqual(self.candidates(), [])

    def test_strict_encoding_unsupported_is_explicit_partial_coverage(self):
        self.collection({'one': 'ordinary TAILWORD\n'.encode('utf-16')})
        receipt = self.refresh()
        self.assertEqual(receipt['state'], 'partial')
        self.assertEqual(receipt['unsupported'], 1)
        self.assertEqual(receipt['indexed'] + receipt['reused'], 0)
        self.assertEqual(self.candidates(), [])

    def test_refresh_replaces_external_fts_after_new_capture(self):
        fixture = self.collection({'one': 'OLDTAILWORD\n'})
        self.refresh()
        (fixture['folder'] / 'one.md').write_text('NEWTAILWORD\r\n')
        collect_target(self.store, fixture['target'])
        capture_source(self.store, fixture['target'])
        self.refresh()
        self.assertEqual(self.candidates('OLDTAILWORD'), [])
        result = self.resolve(self.candidates('NEWTAILWORD')[0], 'NEWTAILWORD')
        self.assert_proof(result, b'NEWTAILWORD\r\n')

    def test_cancel_and_budget_are_failures_with_no_ready_receipt(self):
        self.collection()
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
            with self.assertRaises(AldenCancelled):
                index.refresh_original_index(self.store, ['one'], db, cancelled=lambda: True)
            with patch.object(index, 'MAX_TEXT_BYTES', 20):
                with self.assertRaises(index.OriginalTextIndexError) as caught:
                    index.refresh_original_index(self.store, ['one'], db)
            self.assertEqual(caught.exception.code, 'collection_original_index_budget')
        self.refresh()
        with self.assertRaises(AldenCancelled):
            self.candidates(cancelled=lambda: True)
        with self.assertRaises(AldenCancelled):
            self.resolve(self.candidates()[0], cancelled=lambda: True)

    def test_lookup_keeps_scoped_document_and_text_budgets(self):
        self.collection({'one': 'TAILWORD\n', 'two': 'TAILWORD second\n'})
        self.refresh()
        for name, value in (('MAX_DOCUMENTS', 1), ('MAX_TEXT_BYTES', 1)):
            with self.subTest(budget=name), patch.object(index, name, value):
                with self.assertRaises(index.OriginalTextIndexError) as caught:
                    self.candidates()
                self.assertEqual(caught.exception.code, 'collection_original_index_budget')

    def test_cancellation_propagates_during_real_material_io_and_context_resets(self):
        self.collection()
        inherited = index._READ_CANCELLATION.get()
        checks = 0
        def cancelled():
            nonlocal checks
            checks += 1
            return checks >= 8
        with sqlite3.connect(self.store.root / 'retrieval.sqlite3') as db:
            with self.assertRaises(AldenCancelled):
                index.refresh_original_index(self.store, ['one'], db, cancelled=cancelled)
        self.assertEqual(index._READ_CANCELLATION.get(), inherited)

    def test_source_record_integrity_failure_is_not_success(self):
        self.collection(); self.refresh(); candidate = self.candidates()[0]
        with self.store.database() as db:
            name = db.execute('SELECT raw_path FROM versions WHERE id=?', (candidate['version'],)).fetchone()[0]
        (self.store.blobs / name).write_bytes(b'{}')
        with self.assertRaises(SourceBodyError) as caught:
            self.resolve(candidate)
        self.assertEqual(caught.exception.code, 'alden_source_body_integrity')

    def test_new_current_version_cannot_admit_old_candidate(self):
        fixture = self.collection(); self.refresh(); candidate = self.candidates()[0]
        (fixture['folder'] / 'one.md').write_text('replacement TAILWORD\n')
        collect_target(self.store, fixture['target'])
        self.assertEqual(self.candidates(), [])
        with self.assertRaises((index.OriginalTextIndexError, SourceBodyError)):
            self.resolve(candidate)


if __name__ == '__main__':
    unittest.main()
