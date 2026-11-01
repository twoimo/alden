"""Reference selection must match the full view, including its failure fences."""
import sqlite3
import unittest

from tests import test_alden_collection as fixtures
from alden_collection import identity


class GraphReferenceTests(unittest.TestCase):
    setUp = fixtures.CollectionTests.setUp
    target = fixtures.CollectionTests.target
    record = staticmethod(fixtures.CollectionTests.record)

    def populate(self):
        first, second, denied = self.target(), self.target('other', 'two'), self.target('denied')
        self.store.ingest(first, [self.record('shared', label='자소서 안내', kind='topic'),
            self.record('neighbor', label='면접 안내'), self.record('third')], relations=[
            {'source_platform': 'youtube', 'source_id': 'shared', 'target_platform': 'youtube',
             'target_id': 'neighbor', 'type': 'supports', 'evidence': {'reason': 'reference'}}])
        self.store.ingest(second, [self.record('shared', label='다른 프로젝트 개정본'), self.record('other')])
        self.store.ingest(denied, [self.record('secret', label='not-permitted')])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?", (denied,))
        return first, second

    def test_reference_selection_preserves_order_versions_scope_counts_pages_and_search(self):
        first, _ = self.populate()
        before = self.store.path.read_bytes()
        for projects, options in [
            (['one'], {}), (['one', 'two'], {}), ([], {}),
            (['one'], {'target_id': first}), (['one'], {'limit': 2}),
            (['one'], {'limit': 2, 'offset': 2}), (['one'], {'overview': True, 'limit': 1984}),
            (['one'], {'query': '자소서'}), (['one'], {'query': 'nothing-matches'}),
            (['one'], {'node_type': 'topic'}), (['one'], {'relation': 'supports'}),
            (['one'], {'platform': 'threads'}), (['one'], {'since': 0, 'until': 1}),
            *[(['one'], {'focus': identity('youtube', 'shared'), 'hops': hops}) for hops in (0, 1, 3)],
        ]:
            with self.subTest(projects=projects, options=options):
                full = self.store.graph_page(projects=projects, **options)
                compact = self.store.graph_references(projects=projects, **options)
                expected = [{key: node[key] for key in ('id', 'source_version', 'source_target')}
                            for node in full['nodes']]
                self.assertEqual(compact['nodes'], expected)
                self.assertEqual(compact['projection'], 'current_graph_references')
                self.assertNotIn('edges', compact)
                self.assertNotIn('targets', compact)
                for key in ('ok', 'total_nodes', 'total_edges', 'next'):
                    self.assertEqual(compact[key], full[key])
                self.assertEqual(compact['revision'], full.get('revision', 0))
                self.assertNotIn(identity('youtube', 'secret'), {node['id'] for node in compact['nodes']})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_json_scalars_cannot_enable_the_private_projection(self):
        self.populate()
        for value in (True, None, 0, 1, 'true', {}, []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'collection_graph_budget_invalid'):
                self.store.graph_page(projects=['one'], _references_only=value)
        with self.assertRaisesRegex(ValueError, 'collection_graph_budget_invalid'):
            self.store.graph_references(projects=['one'], details=True)

    def test_relation_integrity_is_checked_even_without_returning_edges(self):
        self.populate()
        with self.store.database() as db:
            db.execute('DELETE FROM relation_versions')
        for read in (self.store.graph_page, self.store.graph_references):
            with self.subTest(read=read.__name__), self.assertRaisesRegex(RuntimeError, 'collection_relation_integrity'):
                read(projects=['one'])

    def test_sql_edge_json_validation_is_not_skipped_with_the_python_edge_payload(self):
        self.populate()
        with self.store.database() as db:
            # Preserve the history binding while introducing a value JSON1
            # cannot serialize. Omitting Python edge objects must not hide it.
            db.execute('UPDATE relations SET type=?', (b'not-text',))
            db.execute('UPDATE relation_versions SET type=?', (b'not-text',))
        failures = []
        for read in (self.store.graph_page, self.store.graph_references):
            with self.assertRaises(sqlite3.Error) as caught:
                read(projects=['one'])
            failures.append(str(caught.exception))
        self.assertEqual(failures[0], failures[1])

    def test_unselected_permitted_context_keeps_filter_validation_errors(self):
        _, second = self.populate()
        shared = identity('youtube', 'shared')
        with self.store.database() as db:
            version = db.execute('SELECT current_version FROM memberships WHERE target_id=? AND document_id=?',
                                 (second, shared)).fetchone()[0]
            # The first target remains selected, but a filtered context query
            # also inspects this older permitted membership's metadata.
            db.execute('UPDATE versions SET collected_at=1,metadata=? WHERE id=?', ('{', version))
        for read in (self.store.graph_page, self.store.graph_references):
            with self.subTest(read=read.__name__), self.assertRaises(sqlite3.Error):
                read(projects=['one', 'two'], node_type='topic')

    def test_unselected_target_configuration_keeps_the_existing_selection_behavior(self):
        first, _ = self.populate()
        with self.store.database() as db:
            db.execute('UPDATE targets SET config=? WHERE id=?', ('{', first))
        # The full graph does not parse scheduler configuration. The compact
        # selection must neither invent validation nor omit an existing one.
        full = self.store.graph_page(projects=['one'])
        compact = self.store.graph_references(projects=['one'])
        self.assertTrue(full['ok'])
        self.assertTrue(compact['ok'])
        self.assertEqual(compact['nodes'], [{key: node[key] for key in
                         ('id', 'source_version', 'source_target')} for node in full['nodes']])

    def test_malformed_selected_records_keep_the_same_failure(self):
        first, _ = self.populate()
        node = identity('youtube', 'shared')
        with self.store.database() as db:
            version = db.execute('SELECT current_version FROM memberships WHERE target_id=? AND document_id=?',
                                 (first, node)).fetchone()[0]
            saved = dict(db.execute('SELECT body,metadata FROM versions WHERE id=?', (version,)).fetchone())
        for field, value in [('body', b'not-text'), ('metadata', '{')]:
            with self.subTest(field=field):
                with self.store.database() as db:
                    db.execute(f'UPDATE versions SET {field}=? WHERE id=?', (value, version))
                failures = []
                try:
                    for read in (self.store.graph_page, self.store.graph_references):
                        try:
                            read(projects=['one'])
                        except (sqlite3.Error, ValueError, TypeError, AttributeError) as error:
                            failures.append((type(error), str(error)))
                        else:
                            self.fail('Malformed full-view input was silently accepted')
                    self.assertEqual(failures[0], failures[1])
                finally:
                    with self.store.database() as db:
                        db.execute(f'UPDATE versions SET {field}=? WHERE id=?', (saved[field], version))
