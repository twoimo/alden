import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_graph_quality import _quality_read_cache
from alden_graph_quality_action import read_action


class QualityReadConnectionsTests(unittest.TestCase):
    def test_unavailable_or_capped_mapping_keeps_previous_private_cache(self):
        for schema, expected in [('main', 65536), ('quality_cache', 32768)]:
            for admitted in [None, (0,), (134217728,), ('268435456',), (True,)]:
                with self.subTest(schema=schema, admitted=admitted):
                    db = Mock()
                    db.execute.return_value.fetchone.return_value = admitted
                    _quality_read_cache(db, schema)
                    self.assertEqual(db.execute.call_args_list[-1].args,
                                     (f'PRAGMA {schema}.cache_size=-{expected}',))

    def test_read_configuration_error_is_not_hidden_by_a_cache_fallback(self):
        db = Mock()
        db.execute.side_effect = sqlite3.OperationalError('read failure')
        with self.assertRaises(sqlite3.OperationalError):
            _quality_read_cache(db, 'main')
        self.assertEqual(db.execute.call_count, 1)
        db.reset_mock()
        with self.assertRaises(KeyError):
            _quality_read_cache(db, 'other; VACUUM')
        db.execute.assert_not_called()

    def test_actual_wal_snapshots_and_connection_settings_remain_local(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ['collection.sqlite3', 'retrieval.sqlite3']]
            writers = [sqlite3.connect(path) for path in paths]
            try:
                for writer in writers:
                    self.assertEqual(writer.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
                    writer.execute('CREATE TABLE items(value INTEGER)')
                    writer.execute('INSERT INTO items VALUES(1)')
                    writer.commit()
                defaults = [(writer.execute('PRAGMA mmap_size').fetchone()[0],
                             writer.execute('PRAGMA cache_size').fetchone()[0]) for writer in writers]
                with closing(sqlite3.connect(paths[0].as_uri() + '?mode=ro', uri=True)) as reader:
                    reader.execute('PRAGMA query_only=ON')
                    reader.execute('ATTACH DATABASE ? AS quality_cache',
                                   (paths[1].as_uri() + '?mode=ro',))
                    reader.execute('BEGIN')
                    for schema in ['main', 'quality_cache']:
                        _quality_read_cache(reader, schema)
                        self.assertEqual(reader.execute(f'SELECT * FROM {schema}.items').fetchall(), [(1,)])
                    for writer in writers:
                        writer.execute('INSERT INTO items VALUES(2)')
                        writer.commit()
                    for schema in ['main', 'quality_cache']:
                        self.assertEqual(reader.execute(f'SELECT * FROM {schema}.items').fetchall(), [(1,)])
                        with self.assertRaises(sqlite3.OperationalError):
                            reader.execute(f'INSERT INTO {schema}.items VALUES(3)')
                    reader.rollback()
                for path, default in zip(paths, defaults):
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as fresh:
                        self.assertEqual(fresh.execute('SELECT * FROM items').fetchall(), [(1,), (2,)])
                        self.assertEqual(fresh.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
                        self.assertEqual(fresh.execute('PRAGMA mmap_size').fetchone()[0], default[0])
                        self.assertEqual(fresh.execute('PRAGMA cache_size').fetchone()[0], default[1])
            finally:
                for writer in writers:
                    writer.close()

    def test_quality_response_rejects_retrieval_replacement_removal_or_appearance(self):
        for mutation in ['unchanged', 'replace', 'remove', 'appear']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                main = root / 'collection.sqlite3'
                main.write_bytes(b'owned collection adapter')
                cache = root / 'retrieval.sqlite3'
                if mutation != 'appear':
                    cache.write_bytes(b'owned cache adapter')
                store = SimpleNamespace(root=root, path=main, schema=3)
                def during_audit(*args, **kwargs):
                    if mutation == 'replace':
                        replacement = root / 'replacement.sqlite3'
                        replacement.write_bytes(cache.read_bytes())
                        replacement.replace(cache)
                    elif mutation == 'remove':
                        cache.unlink()
                    elif mutation == 'appear':
                        cache.write_bytes(b'new cache adapter')
                    return {}
                with patch('alden_graph_quality_action.CollectionStore.open_existing', return_value=store), \
                     patch('alden_graph_quality_action._projects', return_value=['one']), \
                     patch('alden_graph_quality_action._bindings', return_value=[]), \
                     patch('alden_graph_quality_action.audit', side_effect=during_audit), \
                     patch('alden_graph_quality_action._summary', return_value={}):
                    response = read_action(root, '{"scope":"all"}')
                if mutation == 'unchanged':
                    self.assertIs(response['ok'], True)
                else:
                    self.assertIs(response['ok'], False)
                    self.assertEqual(response['reason'], 'quality_scope_unavailable')


if __name__ == '__main__':
    unittest.main()
