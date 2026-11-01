"""Reversible isolated policy snapshots; never activates an installed runtime.

The original root is retained. Current canonical reads rebuild a separate staged
projection, preserving old pages, versions and discovery cursors for recovery.
"""
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
import fcntl
import os
import shutil
import sqlite3

from alden_collection import encoded, read_action, safe_directory
from messenger_candidate import Frontier, MessengerAuthority, SourceBlocked, create_root
from messenger_quality import POLICY


@contextmanager
def writer_lock(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


class PolicyMigration:
    @classmethod
    def create(cls, frontiers, destination, *, after_copy=lambda: None):
        frontiers = tuple(frontiers)
        source = MessengerAuthority(frontiers)
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink() or destination.is_relative_to(source.root):
            raise ValueError('messenger_policy_destination_required')
        collection = source.root / 'knowledge' / 'collection'
        databases = sorted({*collection.rglob('*.sqlite3'), *(f.path for f in frontiers)})
        with ExitStack() as stack:
            for frontier in sorted(frontiers, key=lambda f: f.platform):
                stack.enter_context(writer_lock(frontier.folder / 'writer.lock'))
            watches = {}
            for path in databases:
                if any(p.is_symlink() for p in (path, *path.parents)):
                    raise ValueError('messenger_policy_source_symlink')
                db = stack.enter_context(closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)))
                db.execute('PRAGMA query_only=ON')
                watches[path] = (db, db.execute('PRAGMA data_version').fetchone()[0])
            create_root(destination)
            for path in sorted(collection.rglob('*')):
                if path.is_symlink():
                    raise ValueError('messenger_policy_source_symlink')
                relative = path.relative_to(source.root)
                if 'locks' in relative.parts or path.name.endswith(('-wal', '-shm')):
                    continue
                target = destination / relative
                if path.is_dir():
                    safe_directory(target)
                elif path not in watches:
                    safe_directory(target.parent); shutil.copy2(path, target); target.chmod(0o600)
            for path, (db, _) in watches.items():
                target = destination / path.relative_to(source.root)
                safe_directory(target.parent)
                with closing(sqlite3.connect(target)) as saved:
                    db.backup(saved)
                target.chmod(0o600)
            after_copy()
            if (set(collection.rglob('*.sqlite3')) | {f.path for f in frontiers} != set(watches)
                or any(db.execute('PRAGMA data_version').fetchone()[0] != version for db, version in watches.values())):
                raise RuntimeError('messenger_policy_source_changed_during_snapshot')
        staged = [Frontier(destination, f.platform, f.account, f.reader) for f in frontiers]
        result = cls(destination, staged)
        with result.database() as db:
            db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE tasks(target TEXT NOT NULL,room TEXT NOT NULL,message TEXT NOT NULL,
                old_hash TEXT,page TEXT NOT NULL,before_cursor TEXT,job TEXT,
                state TEXT NOT NULL,error TEXT,new_hash TEXT,PRIMARY KEY(target,room,message));
              CREATE INDEX task_state ON tasks(state,target,room,message);
              CREATE INDEX task_lineage ON tasks(old_hash,state,new_hash);''')
            db.executemany('INSERT INTO meta VALUES(?,?)', [('phase','prepared'),('policy',POLICY),('original_root',str(source.root))])
            for frontier in staged:
                with frontier.database() as original:
                    for row in original.execute('''SELECT h.room,h.message,h.content_hash,h.page,
                        coalesce(json_extract(p.payload,'$.reader_before'),json_extract(p.payload,'$.before')) AS before_cursor
                        FROM message_heads h JOIN pages p ON p.id=h.page'''):
                        db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,NULL,'pending',NULL,?)",
                            (frontier.target, row['room'], row['message'], row['content_hash'], row['page'], row['before_cursor'], None))
                    for page in original.execute("""SELECT id,room,
                        json_extract(payload,'$.quality_policy') AS policy,
                        coalesce(json_extract(payload,'$.reader_before'),json_extract(payload,'$.before')) AS before_cursor
                        FROM pages WHERE state='prepared'"""):
                        # This metadata-only selection does not release retained bodies.
                        ids = [r[0] for r in original.execute("""SELECT json_extract(value,'$.id')
                            FROM pages,json_each(pages.payload,'$.items') WHERE pages.id=?""", (page['id'],))]
                        for message in ids:
                            db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,NULL,?,?,NULL,'pending',NULL,NULL)",
                                (frontier.target,page['room'],message,page['id'],page['before_cursor']))
                        if page['policy'] != POLICY:
                            original.execute('INSERT OR IGNORE INTO retired_policy_pages VALUES(?,?)',
                                (page['id'],'retained old-policy job; canonical refresh queued in staged policy tasks'))
        return result

    def __init__(self, root, frontiers):
        self.root = Path(root)
        self._authority = MessengerAuthority(frontiers)
        if self.root.absolute() != self._authority.root:
            raise ValueError('messenger_policy_root_mismatch')
        self.path = self.root / 'policy-migration.sqlite3'
        self._frontiers = {f.target:f for f in frontiers}

    @contextmanager
    def database(self):
        if self.path.is_symlink():
            raise ValueError('messenger_policy_ledger_symlink')
        with closing(sqlite3.connect(self.path, timeout=.5)) as db:
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback(); raise
        self.path.chmod(0o600)

    def _metadata(self):
        if not self.path.is_file():
            raise SourceBlocked('messenger_policy_snapshot_not_ready')
        with self.database() as db:
            meta = dict(db.execute('SELECT key,value FROM meta'))
        if meta.get('policy') != POLICY:
            raise SourceBlocked('messenger_policy_snapshot_version_changed')
        return meta

    def status(self):
        meta = self._metadata()
        with self.database() as db:
            states = dict(db.execute('SELECT state,count(*) FROM tasks GROUP BY state'))
        return {'phase':meta['phase'],'tasks':states,'policy':POLICY,'original_root':meta['original_root'],
            'root':str(self.root),'production_activated':False}

    def retry_blocked(self):
        with writer_lock(self.root / 'policy-writer.lock'):
            self._metadata()
            with self.database() as db:
                tasks = db.execute("SELECT target,job FROM tasks WHERE state='blocked'").fetchall()
            for task in tasks:
                frontier = self._frontiers[task['target']]
                with writer_lock(frontier.folder / 'writer.lock'), frontier.database() as db:
                    if task['job']:
                        db.execute('INSERT OR IGNORE INTO retired_policy_pages VALUES(?,?)',
                                   (task['job'],'blocked staged observation; new canonical refresh required'))
            with self.database() as db:
                changed = db.execute("UPDATE tasks SET state='pending',job=NULL,error=NULL WHERE state='blocked'").rowcount
                if changed:
                    db.execute("UPDATE meta SET value='prepared' WHERE key='phase'")

    def step(self, *, cancelled=lambda: False, after_publish=lambda: None):
        with writer_lock(self.root / 'policy-writer.lock'):
            self._metadata()
            if cancelled():
                return {'state':'cancelled'}
            with self.database() as db:
                task = db.execute("SELECT * FROM tasks WHERE state='pending' ORDER BY target,room,message LIMIT 1").fetchone()
                if task is None:
                    db.execute("UPDATE meta SET value='ready' WHERE key='phase'")
                    return {'state':'ready','coverage':'completed current sources plus explicitly blocked/withheld tasks; not full server history'}
            frontier = self._frontiers[task['target']]
            try:
                with writer_lock(frontier.folder / 'writer.lock'):
                    if frontier.reader.identity() != frontier.account:
                        raise SourceBlocked('messenger_policy_account_changed')
                    frontier._allowed()
                    job = task['job']
                    if job is None:
                        refresh = getattr(frontier.reader, 'refresh_prepared', None)
                        if not callable(refresh):
                            raise SourceBlocked('messenger_policy_canonical_refresh_unavailable')
                        page = refresh({'room':task['room'],'before':task['before_cursor'],'items':[{'id':task['message']}]})
                        with frontier.database() as db:
                            room = db.execute('SELECT * FROM rooms WHERE id=?', (task['room'],)).fetchone()
                        if room is None:
                            raise SourceBlocked('messenger_policy_room_unavailable')
                        job = frontier._prepare(room, 'policy', page, policy_reprocess=True,
                                                observation_cursor=task['before_cursor'])
                        with self.database() as db:
                            db.execute('UPDATE tasks SET job=? WHERE target=? AND room=? AND message=?',
                                (job, task['target'], task['room'], task['message']))
                    result = frontier._publish(job, cancelled=cancelled, after_publish=after_publish, revalidate=True)
                    with frontier.database() as heads:
                        head = heads.execute('SELECT content_hash FROM message_heads WHERE room=? AND message=?',
                                            (task['room'],task['message'])).fetchone()
                    with self.database() as db:
                        db.execute("UPDATE tasks SET state='complete',error=NULL,new_hash=? WHERE target=? AND room=? AND message=?",
                            (head[0] if head else None,task['target'],task['room'],task['message']))
                return {'state':'processed','job':job,'result':result}
            except SourceBlocked as error:
                with self.database() as db:
                    db.execute("UPDATE tasks SET state='blocked',error=? WHERE target=? AND room=? AND message=?",
                        (str(error),task['target'],task['room'],task['message']))
                return {'state':'blocked','reason':str(error)}

    def identity_map(self):
        # Original source identities remain the primary lineage. One old hash
        # can split into several exact hashes; never guess a pin/entity merge.
        with self.database() as db:
            rows = db.execute("""SELECT DISTINCT old_hash,new_hash FROM tasks
                WHERE old_hash IS NOT NULL AND new_hash IS NOT NULL AND state='complete'
                ORDER BY old_hash,new_hash""").fetchall()
        mapping = {}
        for row in rows:
            mapping.setdefault(row['old_hash'],[]).append(row['new_hash'])
        return {'policy':POLICY,'mapping':mapping,
                'meaning':'content lineage only; ambiguous splits require explicit UI/pin review'}

    def read_action(self, action, query=None):
        if self._metadata()['phase'] != 'ready':
            raise SourceBlocked('messenger_policy_snapshot_not_ready')
        return read_action(self.root, action, query, source_access=self._authority.access,
                           source_revision=self._authority.revision)
