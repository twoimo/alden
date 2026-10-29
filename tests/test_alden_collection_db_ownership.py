"""Real private SQLite connections must close on every public exit path."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import safe_directory
from alden_collection_scheduler import CollectionScheduler, PROVIDER
from alden_realtime_collection import RealtimeCollection

CONNECT = sqlite3.connect


class ConnectionOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.scheduler = CollectionScheduler(self.root)
        self.realtime = RealtimeCollection(self.root)

    def seed(self, path, statements):
        safe_directory(path.parent)
        with closing(CONNECT(path)) as db, db:
            db.executescript(statements)
        path.chmod(0o600)

    def policy_fixture(self, *, provider=PROVIDER):
        self.seed(self.scheduler.path, '''CREATE TABLE acquisition_policy(
            target_id TEXT PRIMARY KEY,enabled INTEGER,provider TEXT,revision INTEGER);''')
        with closing(CONNECT(self.scheduler.path)) as db, db:
            db.execute('INSERT INTO acquisition_policy VALUES(?,?,?,?)', ('fixture', 1, provider, 3))

    @contextmanager
    def observed(self, *, fault=None):
        connections = []
        class Tracked(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if (fault == 'policy_read' and sql.startswith('SELECT enabled,provider,revision')
                        or fault == 'initial_query' and 'FROM sqlite_master' in sql
                        or fault == 'recovery_check' and self.is_recovery and sql == 'PRAGMA integrity_check'
                        or fault == 'read_pragma' and sql == 'PRAGMA query_only=ON'):
                    raise sqlite3.OperationalError('owned fixture injected SQL failure')
                return super().execute(sql, *args, **kwargs)

            def executescript(self, sql, *args, **kwargs):
                if fault == 'schema':
                    raise sqlite3.OperationalError('owned fixture injected schema failure')
                return super().executescript(sql, *args, **kwargs)

            def backup(self, target, *args, **kwargs):
                if fault == 'backup':
                    raise sqlite3.OperationalError('owned fixture injected backup failure')
                return super().backup(target, *args, **kwargs)

            def close(self):
                self.close_calls += 1
                self.query_only_at_close = super().execute('PRAGMA query_only').fetchone()[0]
                return super().close()

        def connect(*args, **kwargs):
            connection = CONNECT(*args, **dict(kwargs, factory=Tracked))
            connection.close_calls = 0
            connection.query_only_at_close = None
            connection.is_recovery = 'scheduler.before-' in str(args[0])
            connection.open_args = (args, kwargs)
            connections.append(connection)
            return connection
        try:
            with patch.object(sqlite3, 'connect', side_effect=connect):
                yield connections
        finally:
            # The observer keeps references only to check post-return state.
            # Failed baseline tests must also release their own connections.
            for connection in connections:
                if not connection.close_calls:
                    connection.close()

    def assert_closed(self, connections, expected):
        self.assertEqual(len(connections), expected)
        self.assertEqual([c.close_calls for c in connections], [1] * expected)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_policy_value_read_only_timeout_and_file_bytes_are_preserved(self):
        self.policy_fixture()
        before = self.scheduler.path.read_bytes()
        with self.observed() as connections:
            self.assertEqual(self.scheduler.acquisition_policy('fixture'),
                             {'enabled': True, 'provider': PROVIDER, 'revision': 3})
            self.assert_closed(connections, 1)
            self.assertEqual(connections[0].query_only_at_close, 1)
            self.assertEqual(connections[0].open_args[1], {'uri': True, 'timeout': .15})
            self.assertTrue(connections[0].open_args[0][0].endswith('?mode=ro'))
        self.assertEqual(self.scheduler.path.read_bytes(), before)

    def test_missing_policy_row_returns_default_and_closes(self):
        self.policy_fixture()
        with self.observed() as connections:
            self.assertEqual(self.scheduler.acquisition_policy('absent'),
                             {'enabled': False, 'provider': PROVIDER, 'revision': 0})
            self.assert_closed(connections, 1)

    def test_legacy_missing_policy_table_returns_default_and_closes(self):
        self.seed(self.scheduler.path, 'CREATE TABLE controls(id TEXT PRIMARY KEY,paused INTEGER);')
        before = self.scheduler.path.read_bytes()
        with self.observed() as connections:
            self.assertFalse(self.scheduler.acquisition_policy('absent')['enabled'])
            self.assert_closed(connections, 1)
        self.assertEqual(self.scheduler.path.read_bytes(), before)

    def test_absent_policy_database_is_not_created_or_connected(self):
        with self.observed() as connections:
            self.assertFalse(self.scheduler.acquisition_policy('absent')['enabled'])
            self.assertEqual(connections, [])
        self.assertFalse(self.scheduler.path.exists())

    def test_invalid_policy_still_raises_and_closes(self):
        self.policy_fixture(provider='untrusted')
        with self.observed() as connections:
            with self.assertRaisesRegex(ValueError, 'collection_acquisition_policy_invalid'):
                self.scheduler.acquisition_policy('fixture')
            self.assert_closed(connections, 1)

    def test_policy_sql_failure_closes_the_read_connection(self):
        self.policy_fixture()
        with self.observed(fault='policy_read') as connections:
            with self.assertRaises(sqlite3.OperationalError):
                self.scheduler.acquisition_policy('fixture')
            self.assert_closed(connections, 1)

    def test_scheduler_success_commits_and_closes(self):
        with self.observed() as connections:
            with self.scheduler.database() as db:
                db.execute("UPDATE controls SET paused=1 WHERE id='global'")
            self.assert_closed(connections, 1)
            self.assertEqual(connections[0].open_args[1], {'timeout': 5})
        with closing(CONNECT(self.scheduler.path)) as db:
            self.assertEqual(db.execute("SELECT paused FROM controls WHERE id='global'").fetchone()[0], 1)

    def test_scheduler_interruption_rolls_back_and_closes(self):
        with self.observed() as connections:
            with self.assertRaises(KeyboardInterrupt):
                with self.scheduler.database() as db:
                    db.execute("UPDATE controls SET paused=1 WHERE id='global'")
                    raise KeyboardInterrupt()
            self.assert_closed(connections, 1)
        with closing(CONNECT(self.scheduler.path)) as db:
            self.assertEqual(db.execute("SELECT paused FROM controls WHERE id='global'").fetchone()[0], 0)

    def test_scheduler_initial_query_failure_closes_before_yield(self):
        with self.observed(fault='initial_query') as connections:
            with self.assertRaises(sqlite3.OperationalError):
                with self.scheduler.database(): self.fail('failed initialization yielded')
            self.assert_closed(connections, 1)

    def test_scheduler_schema_failure_closes_before_yield(self):
        with self.observed(fault='schema') as connections:
            with self.assertRaises(sqlite3.OperationalError):
                with self.scheduler.database(): self.fail('failed initialization yielded')
            self.assert_closed(connections, 1)

    def legacy_scheduler(self):
        self.seed(self.scheduler.path, "CREATE TABLE controls(id TEXT PRIMARY KEY,paused INTEGER); INSERT INTO controls VALUES('global',1);")

    def test_both_legacy_recovery_connections_close_with_real_backups(self):
        self.legacy_scheduler()
        with self.observed() as connections:
            with self.scheduler.database() as db:
                self.assertEqual(db.execute("SELECT paused FROM controls WHERE id='global'").fetchone()[0], 1)
            self.assert_closed(connections, 3)
            self.assertEqual(sum(c.is_recovery for c in connections), 2)
        backups = list(self.scheduler.folder.glob('scheduler.before-*.sqlite3'))
        self.assertEqual(len(backups), 2)
        for backup in backups:
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            with closing(CONNECT(backup)) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT * FROM controls').fetchall(), [('global', 1)])
                self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='acquisition_policy'").fetchone())

    def test_backup_failure_closes_source_and_recovery(self):
        self.legacy_scheduler()
        before = self.scheduler.path.read_bytes()
        with self.observed(fault='backup') as connections:
            with self.assertRaises(sqlite3.OperationalError):
                with self.scheduler.database(): self.fail('failed backup yielded')
            self.assert_closed(connections, 2)
        self.assertEqual(self.scheduler.path.read_bytes(), before)

    def test_backup_integrity_query_failure_closes_source_and_recovery(self):
        self.legacy_scheduler()
        with self.observed(fault='recovery_check') as connections:
            with self.assertRaises(sqlite3.OperationalError):
                with self.scheduler.database(): self.fail('failed integrity check yielded')
            self.assert_closed(connections, 2)

    def test_realtime_write_commits_and_closes(self):
        with self.observed() as connections:
            with self.realtime.database(write=True) as db:
                db.execute('UPDATE policy SET enabled=1,revision=1 WHERE id=1')
            self.assert_closed(connections, 1)
            self.assertEqual(connections[0].open_args[1], {'timeout': 5})
        self.assertEqual(self.realtime.policy(), {'enabled': True, 'revision': 1})

    def test_realtime_interruption_rolls_back_and_closes(self):
        with self.observed() as connections:
            with self.assertRaises(KeyboardInterrupt):
                with self.realtime.database(write=True) as db:
                    db.execute('UPDATE policy SET enabled=1 WHERE id=1')
                    raise KeyboardInterrupt()
            self.assert_closed(connections, 1)
        self.assertEqual(self.realtime.policy(), {'enabled': False, 'revision': 0})

    def test_realtime_write_initialization_failure_closes_before_yield(self):
        with self.observed(fault='schema') as connections:
            with self.assertRaises(sqlite3.OperationalError):
                with self.realtime.database(write=True): self.fail('failed initialization yielded')
            self.assert_closed(connections, 1)

    def test_realtime_read_initialization_failure_closes_before_yield(self):
        with self.realtime.database(write=True): pass
        with self.observed(fault='read_pragma') as connections:
            with self.assertRaises(sqlite3.OperationalError):
                with self.realtime.database(): self.fail('failed initialization yielded')
            self.assert_closed(connections, 1)

    def test_realtime_read_only_timeout_and_write_rejection_are_preserved(self):
        with self.realtime.database(write=True): pass
        before = self.realtime.path.read_bytes()
        with self.observed() as connections:
            with self.assertRaises(sqlite3.OperationalError):
                with self.realtime.database() as db:
                    db.execute('UPDATE policy SET enabled=1 WHERE id=1')
            self.assert_closed(connections, 1)
            self.assertEqual(connections[0].query_only_at_close, 1)
            self.assertEqual(connections[0].open_args[1], {'uri': True, 'timeout': .2})
        self.assertEqual(self.realtime.path.read_bytes(), before)


if __name__ == '__main__': unittest.main()
