"""Structured source bodies survive collection, upgrades and scoped retrieval."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import alden_collection as collection
from alden_collect import collect_target
from alden_collection_retrieval import index_dense, retrieve
import auto_reply_knowledge_graph as kg


class CollectionStructureTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = collection.CollectionStore(self.root)
        self.target = self.store.register(platform='graph', original_id='source',
                                          kind='source', label='source', projects=['one'])

    @staticmethod
    def records():
        return [
            {'original_id': 'structured', 'label': 'structured',
             'text': 'def calculate():\n    total = 42\n\treturn total\n\nname\tvalue\nA\t42\n'},
            {'original_id': 'plain', 'label': 'plain', 'text': 'Unchanged paragraph.'},
        ]

    def versions(self):
        with self.store.database() as db:
            return {row['id']: tuple(row) for row in db.execute('SELECT * FROM versions')}

    def seed_legacy_snapshot(self, records):
        # Reproduce the v1 body and two-element snapshot fingerprint. The
        # original completed run remains immutable when the new collector runs.
        with patch.object(collection, 'normalized_body', collection.normalized_text, create=True):
            self.store.ingest(self.target, records, run_id='legacy-seed',
                              complete_snapshot=True, source_revision='a' * 64,
                              source_order=10, processing_version='external-parser-v1')
        fingerprint = collection.digest(collection.encoded([
            [{k: v for k, v in record.items() if k != 'source'} for record in records], []]))
        with self.store.database() as db:
            db.execute('UPDATE target_snapshots SET projection_sha256=? WHERE target_id=?',
                       (fingerprint, self.target))

    def refresh(self, records, **options):
        return self.store.ingest(self.target, records, complete_snapshot=True,
                                  source_revision='a' * 64, source_order=10,
                                  processing_version='external-parser-v1', **options)

    def test_body_keeps_code_tables_markdown_and_unicode_structure(self):
        raw_text = '\r\nCafe\u0301  report\r\n\r\n```python\r\n    value = 42\r\n\treturn value\r\n```\r\nA\tB\rFirst  \rSecond\n'
        expected = '\nCafé  report\n\n```python\n    value = 42\n\treturn value\n```\nA\tB\nFirst  \nSecond\n'
        record = {'original_id': 'structured', 'label': '  Cafe\u0301\n report  ', 'text': raw_text}
        self.store.ingest(self.target, [record])
        result = self.store.search('value', projects=['one'])
        self.assertEqual(result[0]['label'], 'Café report')
        self.assertEqual(result[0]['body'], expected)
        with self.store.database() as db:
            row = db.execute('SELECT * FROM versions').fetchone()
        self.assertEqual((self.store.blobs / row['raw_path']).read_bytes(), collection.encoded(record))
        bundle = retrieve(self.root, 'value', projects=['one'])
        self.assertEqual(bundle['facts'], ['Café report: ' + expected])
        self.assertEqual(bundle['fact_provenance'][0]['source_version'], row['id'])
        self.assertTrue(bundle['fact_provenance'][0]['provenance_valid'])

    def test_unchanged_legacy_snapshot_reprocesses_once_and_preserves_history(self):
        records = self.records()
        self.seed_legacy_snapshot(records)
        before = self.versions()
        raw_before = {p.name: p.read_bytes() for p in self.store.blobs.iterdir()}
        result = self.refresh(records)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual((result['added'], result['revised'], result['unchanged']), (0, 1, 1))
        after = self.versions()
        self.assertEqual(len(after), 3)
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual({p.name: p.read_bytes() for p in self.store.blobs.iterdir()}, raw_before)
        self.assertEqual(self.store.search('calculate', projects=['one'])[0]['body'], records[0]['text'])
        with self.store.database() as db:
            event_count = db.execute('SELECT count(*) FROM events').fetchone()[0]
            self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM version_search').fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT state FROM runs WHERE id='legacy-seed'").fetchone()[0], 'complete')
        replay = self.refresh(records)
        self.assertEqual(replay['state'], 'unchanged')
        self.assertEqual(replay['run_id'], result['run_id'])
        self.assertEqual(self.versions(), after)
        with self.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], event_count)

    def test_upgraded_body_invalidates_only_changed_vector_and_keeps_project_scope(self):
        records = self.records()
        self.seed_legacy_snapshot(records)
        other = self.store.register(platform='graph', original_id='other', kind='source',
                                    label='other', projects=['two'])
        self.store.ingest(other, [{'original_id': 'private', 'label': 'private',
                                  'text': 'calculate private secret'}])
        embedded = []

        def embed(texts):
            embedded.extend(texts)
            return [[1.0, 0.0] for _ in texts]

        with patch.object(kg, '_active_dense_embedding_model', return_value='fixed-test-encoder'):
            self.assertEqual(index_dense(self.root, ['one'], embed=embed)['embedded_changed'], 2)
            self.refresh(records)
            query_calls = []
            pending = retrieve(self.root, 'calculate', projects=['one'],
                               query_embed=lambda query: query_calls.append(query) or [1.0, 0.0])
            self.assertEqual(query_calls, [])
            self.assertEqual(pending['search_mode'], 'bm25_only')
            self.assertEqual(pending['facts'], ['structured: ' + records[0]['text']])
            embedded.clear()
            report = index_dense(self.root, ['one'], embed=embed)
            self.assertEqual(report['embedded_changed'], 1)
            self.assertEqual(len(embedded), 1)
            self.assertIn('    total = 42\n\treturn total', embedded[0])
            result = retrieve(self.root, 'calculate', projects=['one'], query_embed=lambda _: [1.0, 0.0])
            self.assertEqual(result['search_mode'], 'rrf')
            self.assertIn('structured: ' + records[0]['text'], result['facts'])
            self.assertTrue(all(p['projects'] == ['one'] for p in result['fact_provenance']))
            self.assertNotIn('private secret', '\n'.join(result['facts']))
            self.assertEqual(index_dense(self.root, ['one'], embed=lambda _: self.fail('already indexed'))['embedded_changed'], 0)

    def test_declared_source_file_collector_preserves_structure_and_input_bytes(self):
        source = self.root / 'source.json'
        text = 'def calculate():\n    return 42\n\nkey\tvalue\n'
        raw = collection.encoded({'nodes': [{'id': 'code', 'title': 'Code', 'summary': text}], 'edges': []})
        source.write_bytes(raw)
        target = self.store.register(platform='graph', original_id='file-source', kind='source',
                                     label='file', projects=['one'],
                                     config={'path': str(source), 'adapter': 'source-graph'})
        result = collect_target(self.store, target)
        self.assertEqual(result['batches'][0]['added'], 1)
        self.assertEqual(self.store.search('calculate', projects=['one'])[0]['body'], text)
        self.assertEqual(source.read_bytes(), raw)
        replay = collect_target(self.store, target)
        self.assertEqual(replay['batches'][0]['state'], 'unchanged')

    def test_cancelled_upgrade_retains_old_snapshot_and_retries_cleanly(self):
        records = self.records()
        self.seed_legacy_snapshot(records)
        before = self.versions()
        with self.store.database() as db:
            snapshot = tuple(db.execute('SELECT * FROM target_snapshots').fetchone())
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls == 2

        with self.assertRaisesRegex(RuntimeError, 'collection_cancelled'):
            self.refresh(records, cancelled=cancelled)
        self.assertEqual(self.versions(), before)
        with self.store.database() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM target_snapshots').fetchone()), snapshot)
        self.assertEqual(self.refresh(records)['revised'], 1)


if __name__ == '__main__':
    unittest.main()
