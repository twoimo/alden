"""Durable change detection for existing, permitted collection inputs.

No content inference or source discovery. CollectionScheduler remains the only
publication owner. Signatures are observation hints, never source identities.
"""
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from alden_collection import safe_directory

POLL_SECONDS = 5
SETTLE_SECONDS = 2
REMOTE_SECONDS = 60
ADAPTERS = {'source-graph', 'spark-index', 'youtube-graph', 'threads-snapshot', 'youtube-video', 'threads-post', 'file-set'}


class RealtimeCollection:
    def __init__(self, state_root):
        self.root = Path(state_root) / 'knowledge' / 'collection'
        self.path = self.root / 'realtime.sqlite3'
        self.last_heartbeat = 0.
        self.last_state = None

    @contextmanager
    def database(self, *, write=False):
        if any(p.is_symlink() for p in [self.path, *self.path.parents]):
            raise ValueError('collection_realtime_state_unsafe')
        if write:
            safe_directory(self.root)
            if not self.path.exists():
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
        with closing(sqlite3.connect(self.path, timeout=5) if write else
                     sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            if write:
                db.executescript('''CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,revision INTEGER NOT NULL);
                  INSERT OR IGNORE INTO policy VALUES(1,0,0);
                  CREATE TABLE IF NOT EXISTS inputs(target_id TEXT PRIMARY KEY,seen TEXT,stable_since REAL,queued TEXT,queued_at REAL,
                    processed TEXT,last_collected REAL,error TEXT);
                  CREATE TABLE IF NOT EXISTS heartbeat(id INTEGER PRIMARY KEY CHECK(id=1),pid INTEGER NOT NULL,updated REAL NOT NULL,state TEXT NOT NULL);''')
            else:
                db.execute('PRAGMA query_only=ON')
            db.row_factory = sqlite3.Row
            try:
                yield db
                if write: db.commit()
            except BaseException:
                if write: db.rollback()
                raise

    def policy(self):
        if not self.path.exists(): return {'enabled': False, 'revision': 0}
        with self.database() as db:
            row = db.execute('SELECT enabled,revision FROM policy WHERE id=1').fetchone()
        if row is None or row['enabled'] not in (0, 1) or type(row['revision']) is not int or not 0 <= row['revision'] <= 9007199254740991:
            raise ValueError('collection_realtime_policy_invalid')
        return {'enabled': bool(row['enabled']), 'revision': row['revision']}

    def update(self, enabled, expected_revision):
        if type(enabled) is not bool or type(expected_revision) is not int or not 0 <= expected_revision < 9007199254740991:
            raise ValueError('collection_realtime_control_invalid')
        # Caller holds the existing scheduler control lock across CAS/readback.
        prior = self.policy()
        if prior['revision'] != expected_revision: raise ValueError('collection_realtime_policy_changed')
        if prior['enabled'] == enabled: return prior
        raw = (json.dumps(prior, sort_keys=True) + '\n').encode()
        folder = safe_directory(self.root / 'realtime-policy-backups')
        backup = folder / (hashlib.sha256(raw).hexdigest() + '.json')
        if not backup.exists():
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as handle: handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        if backup.is_symlink() or backup.read_bytes() != raw: raise RuntimeError('collection_realtime_backup_invalid')
        with self.database(write=True) as db:
            changed = db.execute('UPDATE policy SET enabled=?,revision=revision+1 WHERE id=1 AND revision=?', (int(enabled), expected_revision)).rowcount
            if changed != 1: raise ValueError('collection_realtime_policy_changed')
        return self.policy()

    @staticmethod
    def signature(target):
        config = target['config']
        if config.get('adapter') not in ADAPTERS: raise ValueError('collection_realtime_input_unsupported')
        if config['adapter'] == 'file-set':
            from alden_file_set import file_set_signature
            try:
                return file_set_signature(target)
            except RuntimeError as error:
                raise ValueError('collection_realtime_input_changed') from error
        path = Path(config['path'])
        if not path.is_absolute() or any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError('collection_realtime_input_unsafe')
        paths = [path, Path(str(path) + '-wal')] if config['adapter'] == 'spark-index' else [path]
        stamps = []
        for i, item in enumerate(paths):
            try: info = item.stat(follow_symlinks=False)
            except FileNotFoundError:
                if i: stamps.append(None); continue
                raise ValueError('collection_realtime_input_missing') from None
            if not stat.S_ISREG(info.st_mode): raise ValueError('collection_realtime_input_unsafe')
            stamps.append([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns])
        return hashlib.sha256(json.dumps([str(path), stamps], separators=(',', ':')).encode()).hexdigest()

    def scan(self, store, *, paused=lambda _id: False, now=None):
        policy = self.policy()
        if not policy['enabled']: return 0
        now = time.time() if now is None else now
        after, scanned = '', 0
        while True:
            # The page size bounds temporary IDs, not the supported target set.
            # Release the source read before observing files or writing hints.
            with store.database() as source:
                ids = [r[0] for r in source.execute('''SELECT t.id FROM targets t WHERE t.id>? AND t.enabled=1 AND
                  EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=t.id AND p.permission!='denied')
                  ORDER BY t.id LIMIT 200''', (after,))]
            if not ids or self.policy() != policy: return scanned
            with self.database(write=True) as db:
                for target_id in ids:
                    if paused(target_id): continue
                    try:
                        target = store.target(target_id)
                        if target['config'].get('adapter') == 'messenger-db':
                            # The existing scheduler owns canonical source polls;
                            # there is no file path or file hint for this input.
                            db.execute('DELETE FROM inputs WHERE target_id=?', (target_id,))
                            continue
                        signature, error = self.signature(target), None
                    except (OSError, KeyError, ValueError) as exc:
                        signature, error = None, str(exc) if str(exc).startswith('collection_realtime_') else 'collection_realtime_input_unavailable'
                    row = db.execute('SELECT * FROM inputs WHERE target_id=?', (target_id,)).fetchone()
                    if row is None:
                        db.execute('INSERT INTO inputs VALUES(?,?,?,NULL,NULL,NULL,NULL,?)', (target_id, signature, now, error))
                    elif error:
                        if row['error'] != error: db.execute('UPDATE inputs SET error=? WHERE target_id=?', (error, target_id))
                    elif row['seen'] != signature or row['error']:
                        db.execute('UPDATE inputs SET seen=?,stable_since=?,error=NULL WHERE target_id=?', (signature, now, target_id))
                    elif now - row['stable_since'] >= SETTLE_SECONDS and signature != row['processed'] and signature != row['queued']:
                        db.execute('UPDATE inputs SET queued=?,queued_at=COALESCE(queued_at,?) WHERE target_id=?', (signature, now, target_id))
            after, scanned = ids[-1], scanned + len(ids)

    def pending(self):
        if not self.policy()['enabled']: return {}
        with self.database() as db:
            return {r['target_id']: dict(r) for r in db.execute('SELECT target_id,queued,queued_at FROM inputs WHERE queued IS NOT NULL AND error IS NULL ORDER BY queued_at,target_id')}

    def acknowledge(self, target, signature, *, observed_queued=None, now=None):
        if not signature:return False
        try:
            if self.signature(target) != signature:return False
        except (OSError,ValueError,KeyError):return False
        with self.database(write=True) as db:
            # Acquisition can replace the queued input before publication.
            # Consume that original hint too, preserving a newer queued hint.
            db.execute('''UPDATE inputs SET processed=?,last_collected=?,queued=CASE WHEN queued IN (?,?) THEN NULL ELSE queued END,
              queued_at=CASE WHEN queued IN (?,?) THEN NULL ELSE queued_at END WHERE target_id=?''',
              (signature, time.time() if now is None else now, signature, observed_queued,
               signature, observed_queued, target['id']))
        return True

    def heartbeat(self, state, *, now=None):
        now = time.time() if now is None else now
        if state == self.last_state and now - self.last_heartbeat < 10: return
        if not self.path.exists(): return
        with self.database(write=True) as db:
            db.execute('INSERT OR REPLACE INTO heartbeat VALUES(1,?,?,?)', (os.getpid(), now, state))
        self.last_heartbeat, self.last_state = now, state

    def status(self, schedule=None, *, now=None):
        policy = self.policy()
        result = {**policy, 'poll_seconds': POLL_SECONDS, 'remote_seconds': REMOTE_SECONDS, 'state': 'disabled', 'inputs': []}
        if not self.path.exists(): return result
        with self.database() as db:
            row = db.execute('SELECT * FROM heartbeat WHERE id=1').fetchone()
            result['inputs'] = [dict(r) for r in db.execute('SELECT target_id,last_collected,error,queued IS NOT NULL AS pending FROM inputs ORDER BY target_id LIMIT 200')]
        if policy['enabled']:
            age = (time.time() if now is None else now) - row['updated'] if row else None
            confirmed = row and schedule and schedule.get('state') == 'running' and schedule.get('pid') == row['pid'] and 0 <= age <= 20
            result['state'] = row['state'] if confirmed and row['state'] in {'watching', 'processing', 'paused', 'voice_deferred'} else 'reconnecting'
            result['heartbeat_at'] = row['updated'] if row else None
        return result


def watch(scheduler, stopped, *, wait=None, now=time.time):
    """One app-owned process. A stop cancels its current bounded cycle first."""
    wait = stopped.wait if wait is None else wait
    from alden_collection import CollectionStore
    realtime = RealtimeCollection(scheduler.state_root)
    last_scheduled = float('-inf')
    def storage_busy(error):
        code = getattr(error, 'sqlite_errorcode', None)
        return type(code) is int and code & 255 in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    def heartbeat(state):
        try:
            realtime.heartbeat(state)
        except sqlite3.OperationalError as error:
            if not storage_busy(error): raise
    try:
        while not stopped.is_set():
            try:
                stamp = now(); enabled = realtime.policy()['enabled']
                if enabled:
                    store = CollectionStore.open_existing(scheduler.state_root)
                    blocked = scheduler._paused() or scheduler._abort_latched()
                    voice = scheduler._voice_busy()
                    heartbeat('paused' if blocked else 'voice_deferred' if voice else 'watching')
                    if store and not blocked and not voice: realtime.scan(store, paused=scheduler._paused, now=stamp)
                if enabled or stamp - last_scheduled >= 60:
                    result=scheduler.cycle(max_targets=1, max_seconds=180, stopping=stopped.is_set,
                        on_progress=lambda: heartbeat('processing'))
                    last_scheduled = stamp
                    if enabled: heartbeat('paused' if result['state']=='paused' else 'voice_deferred' if result['state']=='deferred' else 'watching')
            except sqlite3.OperationalError as error:
                if not storage_busy(error): raise
                # A failed read is not permission to collect or an empty source.
                # The next bounded tick rereads all gates and durable queues.
                heartbeat('storage_busy')
            wait(POLL_SECONDS)
    finally:
        heartbeat('stopped')
