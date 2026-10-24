"""Uninstalled channel-discovery candidate; isolated marked stores only.

Feed discovery is an additive frontier, never a replacement channel snapshot.
Callbacks are bounded host adapters. Existing product collection/capture/index
consumes the newly registered video inputs; this module does not infer edges.
"""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import xml.etree.ElementTree as ET

from alden_abort import ABORT_STATE_NAME, AbortToken, AldenCancelled
from alden_collection import CollectionStore, encoded, identity, safe_directory
from alden_collect import json_snapshot, youtube_video_snapshot
from alden_collection_scheduler import CollectionScheduler
from alden_source_acquisition import _decode, MAX_OUTPUT_BYTES

NS = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
MAX_FEED_BYTES = 1024 * 1024
ROOT_MARKER = {'schema': 1, 'purpose': 'isolated-channel-discovery-evaluation'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parent_stamp(target):
    fields = ('id', 'platform', 'kind', 'original_id', 'config', 'enabled', 'interval_seconds', 'projects')
    return sha(encoded({k: target[k] for k in fields}))


def parse_feed(raw, channel_id):
    """Require exact full channel identity beyond the observed root suffix."""
    if not isinstance(channel_id, str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id):
        raise ValueError('discovery_channel_identity_invalid')
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FEED_BYTES:
        raise ValueError('discovery_feed_budget')
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or b'\x00' in raw:
        raise ValueError('discovery_feed_declarations_forbidden')
    try:
        tree = ET.fromstring(raw.decode('utf-8'))
    except (UnicodeError, ET.ParseError) as error:
        raise ValueError('discovery_feed_invalid') from error
    author = 'https://www.youtube.com/channel/' + channel_id
    if (tree.tag != '{' + NS['a'] + '}feed'
            or tree.findtext('yt:channelId', namespaces=NS) not in (channel_id, channel_id[2:])
            or tree.findtext('a:author/a:uri', namespaces=NS) != author
            or not any(e.get('rel') == 'alternate' and e.get('href') == author for e in tree.findall('a:link', NS))):
        raise ValueError('discovery_feed_channel_mismatch')
    entries = tree.findall('a:entry', NS)
    if len(entries) > 100:
        raise ValueError('discovery_feed_entry_budget')
    items = []
    seen = set()
    for entry in entries:
        video = entry.findtext('yt:videoId', namespaces=NS)
        if not isinstance(video, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video) or video in seen:
            raise ValueError('discovery_feed_video_identity_invalid')
        url = 'https://www.youtube.com/watch?v=' + video
        links = [e.get('href') for e in entry.findall('a:link', NS) if e.get('rel') == 'alternate']
        if (entry.findtext('yt:channelId', namespaces=NS) != channel_id
                or entry.findtext('a:id', namespaces=NS) != 'yt:video:' + video
                or entry.findtext('a:author/a:uri', namespaces=NS) != author
                or len(links) != 1 or links[0] not in (url, 'https://www.youtube.com/shorts/' + video)):
            raise ValueError('discovery_feed_video_channel_mismatch')
        title = entry.findtext('a:title', namespaces=NS)
        if not isinstance(title, str) or not title.strip() or len(title) > 2000:
            raise ValueError('discovery_feed_title_invalid')
        stamps = {}
        for key in ('published', 'updated'):
            stamp = entry.findtext('a:' + key, namespaces=NS)
            try:
                parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError()
            except (AttributeError, ValueError) as error:
                raise ValueError('discovery_feed_timestamp_invalid') from error
            stamps[key] = stamp
        seen.add(video)
        items.append({'video_id': video, 'url': links[0], 'canonical_url': url, 'title': title, 'author_uri': author, **stamps})
    return items


def _save_once(path, raw):
    safe_directory(path.parent)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if _retained_native(path, limit=len(raw)) != raw:
            raise RuntimeError('discovery_retained_bytes_conflict')
        return
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _retained_native(path, *, limit=MAX_OUTPUT_BYTES):
    # Native stdout may have the documented CLI timing trailer; it is not a
    # plain JSON snapshot. Read a stable bounded byte sequence before decoding.
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('discovery_native_symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError('discovery_native_budget')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(path.stat(follow_symlinks=False)):
        raise RuntimeError('discovery_native_changed_during_read')
    return raw


class DiscoveryCandidate:
    def __init__(self, state_root):
        self.root = Path(state_root).absolute()
        live = Path.home() / 'Library/Application Support/openkakao/bujamentor'
        if self.root.resolve() == live.resolve() or any(p.is_symlink() for p in [self.root, *self.root.parents]):
            raise ValueError('discovery_candidate_live_root_forbidden')
        info = self.root.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('discovery_candidate_private_store_required')
        marker, _ = json_snapshot(self.root / 'candidate-channel-discovery.json')
        if marker != ROOT_MARKER:
            raise ValueError('discovery_candidate_marked_store_required')
        if CollectionStore.open_existing(self.root) is None:
            raise ValueError('discovery_candidate_existing_store_required')
        self.store = CollectionStore(self.root)
        self.scheduler = CollectionScheduler(self.root)
        self.token = AbortToken(self.root / ABORT_STATE_NAME)
        self.folder = safe_directory(self.root / 'knowledge/channel-discovery-candidate')
        self.path = self.folder / 'frontier.sqlite3'

    @contextmanager
    def db(self):
        if self.path.is_symlink():
            raise ValueError('discovery_frontier_unsafe')
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=.15)
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS channels(
          parent TEXT PRIMARY KEY,stamp TEXT NOT NULL,next_poll REAL NOT NULL,hold TEXT);
          CREATE TABLE IF NOT EXISTS feeds(parent TEXT,sha TEXT,observed REAL,entries INTEGER,
            PRIMARY KEY(parent,sha));
          CREATE TABLE IF NOT EXISTS frontier(parent TEXT,video TEXT,feed_sha TEXT,item TEXT,
            state TEXT,attempts INTEGER DEFAULT 0,retry_at REAL DEFAULT 0,native_sha TEXT,
            input_sha TEXT,error TEXT,PRIMARY KEY(parent,video));
          CREATE TABLE IF NOT EXISTS provider_state(key TEXT PRIMARY KEY,value TEXT);''')
        if 'publication_failures' not in {row[1] for row in db.execute('PRAGMA table_info(frontier)')}:
            db.execute('ALTER TABLE frontier ADD COLUMN publication_failures INTEGER NOT NULL DEFAULT 0')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def gate(self, parent, expected):
        self.token.raise_if_cancelled()
        if self.scheduler._paused(parent) or self.scheduler._voice_busy():
            raise AldenCancelled('discovery_paused_or_voice_active')
        target = self.store.target(parent)
        if parent_stamp(target) != expected:
            raise ValueError('discovery_parent_changed')
        if (target['platform'] != 'youtube' or target['kind'] != 'channel'
                or not target['enabled'] or target['config'].get('adapter') != 'youtube-graph'
                or not any(p['permission'] != 'denied' for p in target['projects'])):
            raise ValueError('discovery_parent_scope_denied')
        return target

    def _known(self, video):
        with self.store.database() as db:
            return bool(db.execute('SELECT 1 FROM documents WHERE id=?', (identity('youtube', video),)).fetchone()
                        or db.execute('SELECT 1 FROM targets WHERE id=?', (identity('target-youtube', 'video:' + video),)).fetchone())

    def _publish(self, target, expected, job, raw, cancelled):
        feed_raw = _retained_native(self.folder / 'feeds' / (job['feed_sha'] + '.xml'), limit=MAX_FEED_BYTES)
        if (sha(feed_raw) != job['feed_sha'] or json.loads(job['item']) not in parse_feed(feed_raw, target['original_id'])):
            raise RuntimeError('discovery_feed_recovery_hash_changed')
        value = _decode(raw)
        if value.get('ok') is not True:
            status = value.get('httpStatus')
            raise RuntimeError('discovery_native_http_' + str(status) if type(status) is int else 'discovery_native_unavailable')
        value.pop('ok')
        if value.get('acquisition') != {'provider': 'aside-native-youtube', 'host': 'local', 'account': 'u0', 'nativeReads': 3}:
            raise ValueError('discovery_native_provenance_invalid')
        for key in ('observedAt', 'captionObservedAt'):
            try:
                stamp = datetime.fromisoformat(value[key].replace('Z', '+00:00'))
                if stamp.tzinfo is None or not 0 < stamp.timestamp() <= time.time() + 300:
                    raise ValueError()
            except (KeyError, AttributeError, ValueError) as error:
                raise ValueError('discovery_native_observation_invalid') from error
        video = job['video']
        records, relations = youtube_video_snapshot({'platform': 'youtube', 'kind': 'video', 'original_id': video}, value, {})
        assert len(records) == 1 and not relations
        captions = value['videos'][0].get('captions', {})
        if captions.get('availability') == 'access_failed' and captions.get('httpStatus') in (401, 429):
            raise RuntimeError('discovery_native_http_' + str(captions['httpStatus']))
        # The feed establishes declared context, not native uploader identity.
        value['videos'][0]['declared_context'] = {
            'channel_id': target['original_id'], 'channel_target_id': target['id'],
            'feed_sha256': job['feed_sha'], 'feed_item': json.loads(job['item']),
            'basis': 'retained public feed; native uploader ID not established'}
        input_raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
        retained = self.folder / 'inputs' / (sha(input_raw) + '.json')
        _save_once(retained, input_raw)
        child = identity('target-youtube', 'video:' + video)
        destination = self.root / 'knowledge/acquisition-inputs/channel-discovery-candidate' / child / 'current.json'
        metadata = value['videos'][0]['metadata']
        config = encoded({'adapter': 'youtube-video', 'path': str(destination)}).decode()
        projects = sorted((p['project'], p['permission']) for p in target['projects'] if p['permission'] != 'denied')
        # A single insert-only transaction protects existing targets and scope.
        # Control/parent locks remain held until the canonical DB commits.
        with self.scheduler.control_lock(), self.token.commit_guard(), self.store.target_lock(target['id']), self.store.target_lock(child):
            if cancelled():
                raise AldenCancelled('discovery_cancelled')
            self.gate(target['id'], expected)
            with self.store.database() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT * FROM targets WHERE id=?', (child,)).fetchone()
                if old is not None:
                    actual_projects = [tuple(r) for r in db.execute('SELECT project,permission FROM target_projects WHERE target_id=? ORDER BY project', (child,))]
                    if (old['config'] != config or old['interval_seconds'] != target['interval_seconds']
                            or not old['enabled'] or actual_projects != projects):
                        raise RuntimeError('discovery_existing_target_conflict')
                elif db.execute('SELECT 1 FROM documents WHERE id=?', (identity('youtube', video),)).fetchone():
                    raise RuntimeError('discovery_existing_document_conflict')
                _save_once(destination, input_raw)
                if old is None:
                    db.execute('''INSERT INTO targets(id,platform,original_id,kind,label,url,config,interval_seconds,enabled,next_run)
                      VALUES(?,?,?,?,?,?,?,?,?,?)''', (child, 'youtube', video, 'video', metadata['title'][:120],
                        metadata['url'], config, target['interval_seconds'], 1, time.time()))
                    db.executemany('INSERT INTO target_projects VALUES(?,?,?)', [(child, *p) for p in projects])
                self.gate(target['id'], expected)
                if cancelled():
                    raise AldenCancelled('discovery_cancelled')
        return sha(input_raw), child

    @staticmethod
    def _hold(db, reason):
        db.execute("INSERT OR REPLACE INTO provider_state VALUES('hold',?)", (reason,))

    def step(self, parent, expected, *, feed_reader, video_reader, max_videos=3, max_seconds=300,
             now=None, stopped=lambda: False, after_stage=lambda stage: None):
        if (type(max_videos) is not int or not 1 <= max_videos <= 16 or type(max_seconds) is not int
                or not 1 <= max_seconds <= 3600 or not re.fullmatch('[0-9a-f]{64}', expected)):
            raise ValueError('discovery_budget_or_stamp_invalid')
        at = time.time() if now is None else now
        began = time.monotonic()
        def cancelled():
            if stopped() or time.monotonic() - began >= max_seconds:
                return True
            try:
                self.gate(parent, expected)
            except AldenCancelled:
                return True
            return False
        target = self.gate(parent, expected)
        fd = os.open(self.folder / 'cycle.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'state': 'busy'}
            with self.db() as db:
                db.execute('INSERT OR IGNORE INTO channels VALUES(?,?,0,NULL)', (parent, expected))
                channel = dict(db.execute('SELECT * FROM channels WHERE parent=?', (parent,)).fetchone())
                held = db.execute("SELECT value FROM provider_state WHERE key='hold'").fetchone()
            if channel['stamp'] != expected:
                raise ValueError('discovery_parent_checkpoint_changed')
            if held or channel['hold']:
                return {'state': 'blocked', 'reason': held[0] if held else channel['hold']}
            if at >= channel['next_poll']:
                if cancelled():
                    raise AldenCancelled('discovery_cancelled')
                try:
                    raw = feed_reader(target['original_id'], cancelled=cancelled, timeout=min(20, max_seconds))
                    items = parse_feed(raw, target['original_id'])
                except AldenCancelled:
                    raise
                except Exception as error:
                    message = str(error)[:160]
                    with self.db() as db:
                        db.execute('UPDATE channels SET next_poll=? WHERE parent=?', (at + 60, parent))
                        if message in ('discovery_native_http_401', 'discovery_native_http_429'):
                            self._hold(db, message)
                    return {'state': 'feed_failed', 'reason': message}
                self.gate(parent, expected)
                _save_once(self.folder / 'feeds' / (sha(raw) + '.xml'), raw)
                with self.db() as db:
                    db.execute('INSERT OR IGNORE INTO feeds VALUES(?,?,?,?)', (parent, sha(raw), at, len(items)))
                    for item in items:
                        state = 'existing' if self._known(item['video_id']) else 'queued'
                        db.execute('INSERT OR IGNORE INTO frontier(parent,video,feed_sha,item,state) VALUES(?,?,?,?,?)',
                                   (parent, item['video_id'], sha(raw), encoded(item).decode(), state))
                    db.execute('UPDATE channels SET next_poll=? WHERE parent=?', (at + 60, parent))
            with self.db() as db:
                jobs = [dict(r) for r in db.execute("SELECT * FROM frontier WHERE parent=? AND state IN ('queued','reading','acquired','failed') AND retry_at<=? ORDER BY rowid LIMIT ?", (parent, at, max_videos))]
            results = []
            for job in jobs:
                if cancelled():
                    raise AldenCancelled('discovery_cancelled')
                try:
                    if job['state'] not in ('reading', 'acquired'):
                        job['attempts'] += 1
                        job['native_sha'] = None
                        with self.db() as db:
                            db.execute("UPDATE frontier SET state='reading',attempts=?,native_sha=NULL WHERE parent=? AND video=?", (job['attempts'], parent, job['video']))
                    raw_path = self.folder / 'native' / parent / job['video'] / (str(job['attempts']) + '.json')
                    if raw_path.exists() or raw_path.is_symlink():
                        raw = _retained_native(raw_path)
                    else:
                        raw = video_reader(job['video'], cancelled=cancelled, timeout=min(110, max(1, max_seconds - (time.monotonic() - began))))
                        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_OUTPUT_BYTES:
                            raise ValueError('discovery_native_budget')
                        _decode(raw)
                        _save_once(raw_path, raw)
                    if job['native_sha'] is not None and sha(raw) != job['native_sha']:
                        raise RuntimeError('discovery_native_recovery_hash_changed')
                    with self.db() as db:
                        db.execute("UPDATE frontier SET state='acquired',native_sha=? WHERE parent=? AND video=?", (sha(raw), parent, job['video']))
                    after_stage('acquired')
                    if cancelled():
                        raise AldenCancelled('discovery_cancelled')
                    input_sha, child = self._publish(target, expected, job, raw, cancelled)
                    after_stage('published')
                    with self.db() as db:
                        db.execute("UPDATE frontier SET state='registered',input_sha=?,error=NULL,publication_failures=0 WHERE parent=? AND video=?", (input_sha, parent, job['video']))
                    results.append({'state': 'registered', 'target': child, 'input_sha': input_sha})
                except AldenCancelled:
                    raise
                except Exception as error:
                    message = str(error)[:160]
                    delay = min(21600, 60 * 2 ** min(job['attempts'], 8))
                    with self.db() as db:
                        state = 'blocked' if job['attempts'] >= 5 or message in ('discovery_existing_target_conflict', 'discovery_existing_document_conflict', 'discovery_native_recovery_hash_changed', 'discovery_feed_recovery_hash_changed') else 'failed'
                        publication_failures = job['publication_failures']
                        if isinstance(error, sqlite3.OperationalError):
                            publication_failures += 1
                            # Keep the durable response and deterministic input
                            # after a rolled-back canonical transaction. A new
                            # source read could change its observation time and
                            # conflict with the retained orphan input file.
                            state = 'blocked' if publication_failures >= 5 else 'acquired'
                            delay = min(21600, 60 * 2 ** min(publication_failures, 8))
                        db.execute('UPDATE frontier SET state=?,retry_at=?,error=?,publication_failures=? WHERE parent=? AND video=?',
                                   (state, at + delay, message, publication_failures, parent, job['video']))
                        if message in ('discovery_native_http_401', 'discovery_native_http_429'):
                            self._hold(db, message)
                        if message == 'discovery_native_http_403':
                            row = db.execute("SELECT value FROM provider_state WHERE key='denials'").fetchone()
                            count = (int(row[0]) if row else 0) + 1
                            db.execute("INSERT OR REPLACE INTO provider_state VALUES('denials',?)", (str(count),))
                            if count >= 2:
                                self._hold(db, 'discovery_repeated_native_403')
                        held = db.execute("SELECT value FROM provider_state WHERE key='hold'").fetchone()
                    results.append({'state': 'failed', 'reason': message})
                    if held:
                        break
            with self.db() as db:
                counts = {r[0]: r[1] for r in db.execute('SELECT state,count(*) FROM frontier WHERE parent=? GROUP BY state', (parent,))}
            remaining = sum(n for state, n in counts.items() if state not in ('existing', 'registered'))
            state = 'partial' if any(r['state'] != 'registered' for r in results) else 'pending' if remaining else 'complete'
            return {'state': state, 'results': results, 'frontier': counts,
                    'scope': 'isolated candidate; additive latest-feed registration, existing collection/index required; no full-channel completeness claim'}
        finally:
            os.close(fd)
