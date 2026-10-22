"""Isolated all-room messenger frontier candidate; not an installed watcher.

Readers must use their canonical account/visibility/consent gates. No transport,
source SQL, model, source mutation, account switch or fabricated activity lives
here. A durable prepared page bridges a crash between the two SQLite commits.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import stat
import re
import subprocess
import time

from alden_collection import CollectionStore, encoded, identity, overview_display_label, safe_directory
from messenger_quality import POLICY, classify, text_without_urls

MARKER = b'alden-isolated-messenger-evaluation-v1\n'
PROJECT = 'messenger-evaluation'


class SourceBlocked(RuntimeError):
    pass


class KakaoReader:
    """Adapter over the canonical reader; never SQL or a copied decoder."""
    def __init__(self, canonical):
        self.canonical = canonical

    def identity(self):
        raw = subprocess.check_output(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], text=True)
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"', raw)
        if not match:
            raise SourceBlocked('kakao_current_identity_unavailable')
        return self.canonical.resolve_identity(match[1])[0]

    def authorize(self, room):
        # snapshot() independently verifies the current owner before/after its
        # read-only transaction and withholds hidden source fields at SQL read.
        self.identity()

    def catalog(self):
        account = self.identity()
        result = self.canonical.snapshot(catalog_only=True)
        if result.get('status') != 'ok' or result.get('catalog_truncated') is not False or self.identity() != account:
            raise SourceBlocked('kakao_catalog_unavailable_or_changed')
        return {'account_id': account, 'complete': True,
                'rooms': [{'id': r['room_id'], 'title': r['title']} for r in result['rooms']]}

    def page(self, room, cursor):
        account = self.identity()
        result = self.canonical.snapshot(chat_id=room, show_text=True, limit=50, cursor=cursor)
        if result.get('status') != 'ok':
            reason = result.get('reason') or result.get('status') or 'unavailable'
            raise SourceBlocked('kakao_reader_' + (reason if isinstance(reason, str) and re.fullmatch(r'[a-z0-9_]{1,96}', reason) else 'unavailable'))
        if result.get('db_readonly_verified') is not True or result.get('schema_decryption_verified') is not True:
            raise SourceBlocked('kakao_readonly_or_schema_unconfirmed')
        if self.identity() != account:
            raise SourceBlocked('kakao_account_changed_during_page')
        if result.get('chat', {}).get('chatId') != room:
            raise SourceBlocked('kakao_page_room_scope_mismatch')
        values = []
        for row in result.get('messages', []):
            if row.get('chatId') != room:
                raise ValueError('kakao_message_scope_mismatch')
            visible = not row.get('message_state') and isinstance(row.get('message'), str)
            values.append({'id': row['logId'], 'body': row['message'] if visible else None,
                'visible': visible, 'sent_at': row['sentAt'],
                **({'original': row} if visible else {'withheld_reason': row.get('message_state', 'unsupported_text')})})
        return {'account_id': account, 'room_id': room, 'items': values,
                'more': result['messages_truncated'], 'next_cursor': result.get('next_cursor'),
                'scope': 'canonical current-account visible local SQLCipher rows; live WAL included'}


class TelegramReader:
    """Canonical CLI plus exact, already-obtained date/peer consent contexts.

    invoke must run telegram.py under its existing pinned account, never export
    a session or replay a network API. authorizer rechecks the live registry.
    """
    def __init__(self, invoke, scopes, authorizer):
        self.invoke, self.scopes, self.authorizer = invoke, scopes, authorizer

    def identity(self):
        result = self.invoke(['probe'])
        if result.get('status') != 'ready_readonly' or result.get('read_only') is not True:
            raise SourceBlocked('telegram_current_identity_unavailable')
        return result['owner_id']

    def catalog(self):
        account, rooms, offset, expected = self.identity(), [], 0, None
        while True:
            result = self.invoke(['chats', '--limit', '100', '--offset', str(offset)])
            if result.get('status') != 'ok' or result.get('owner_id') != account or result.get('unsupported_peers') != 0:
                raise SourceBlocked('telegram_catalog_unavailable_or_changed')
            total = result.get('total_matching')
            if type(total) is not int or not 0 <= total <= 100000 or expected is not None and expected != total:
                raise SourceBlocked('telegram_catalog_changed')
            expected = total
            rooms.extend({'id': r['db_peer_id'], 'title': r['title'],
                          'body_blocked': None if r['db_peer_id'] in self.scopes else 'telegram_context_consent_required'} for r in result['chats'])
            if result.get('more') is False:
                if len(rooms) != total or len({r['id'] for r in rooms}) != total:
                    raise SourceBlocked('telegram_catalog_incomplete')
                return {'account_id': account, 'complete': True, 'rooms': rooms}
            following = result.get('next_offset')
            if type(following) is not int or following <= offset or following != len(rooms):
                raise SourceBlocked('telegram_catalog_stalled')
            offset = following

    def authorize(self, room):
        scope = self.scopes.get(room)
        if not scope:
            raise SourceBlocked('telegram_context_consent_required')
        scope = {'since': scope['since'], 'until': scope['until']}
        self.authorizer(self.identity(), room, scope['since'], scope['until'])
        return scope

    def page(self, room, cursor):
        scope = self.authorize(room)
        account = self.identity()
        result = None
        for limit in (50, 1):
            args = ['messages', '--peer-id', room, '--since', scope['since'], '--until', scope['until'],
                    '--show-text', '--limit', str(limit)]
            if cursor is not None:
                args.extend(['--cursor', cursor])
            result = self.invoke(args)
            if result.get('status') not in {'ok', 'partial'}:
                raise SourceBlocked('telegram_content_unavailable')
            if not any(r.get('body_withheld') == 'output_character_budget' for r in result.get('messages', [])):
                break
        if result.get('owner_id') != account or result.get('peer_id') != room or result.get('content_returned') is not True:
            raise SourceBlocked('telegram_page_scope_mismatch')
        if any(r.get('body_withheld') == 'output_character_budget' for r in result.get('messages', [])):
            raise SourceBlocked('telegram_single_message_output_budget')
        values = []
        for row in result.get('messages', []):
            if row.get('peer_id') != room:
                raise ValueError('telegram_message_scope_mismatch')
            visible = not row.get('body_withheld') and isinstance(row.get('text'), str)
            values.append({'id': row['id'], 'body': row['text'] if visible else None,
                'visible': visible, 'sent_at': row['timestamp'],
                **({'original': row} if visible else {'withheld_reason': row.get('body_withheld', 'unsupported_text')})})
        return {'account_id': account, 'room_id': room, 'items': values,
                'more': result['more'], 'next_cursor': result.get('next_cursor'),
                'scope': {'source': 'canonical local Postbox', 'since': scope['since'], 'until': scope['until'],
                          'complete': result.get('complete'), 'withheld': result.get('withheld'),
                          'decode_errors': result.get('decode_errors')}}


def create_root(path):
    path = safe_directory(Path(path))
    marker = path / '.messenger-evaluation'
    fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(MARKER)
    return path


class Frontier:
    def __init__(self, root, platform, account, reader):
        root = Path(root)
        if platform not in {'kakaotalk', 'telegram'} or not isinstance(account, str) or not account:
            raise ValueError('messenger_identity_invalid')
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError('messenger_evaluation_root_required')
        info = root.stat()
        marker = root / '.messenger-evaluation'
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or marker.is_symlink() or marker.read_bytes() != MARKER:
            raise ValueError('messenger_evaluation_root_required')
        self.root, self.platform, self.account, self.reader = root, platform, account, reader
        self.folder = safe_directory(root / ('frontier-' + platform))
        self.path = self.folder / 'frontier.sqlite3'
        with self.database() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS rooms(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,frontier TEXT NOT NULL,
                checked REAL NOT NULL DEFAULT 0,error TEXT,retry_at REAL NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS pages(
                id TEXT PRIMARY KEY,room TEXT NOT NULL,payload TEXT NOT NULL,
                next_frontier TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('prepared','complete')));
              CREATE TABLE IF NOT EXISTS binding(account TEXT NOT NULL);''')
            db.execute('''CREATE TABLE IF NOT EXISTS message_heads(
                id TEXT PRIMARY KEY,room TEXT NOT NULL,message TEXT NOT NULL,
                content_hash TEXT,state TEXT NOT NULL,reason TEXT NOT NULL,body TEXT,page TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS message_content ON message_heads(content_hash,state)')
            binding = db.execute('SELECT account FROM binding').fetchone()
            if binding and binding[0] != account:
                raise ValueError('messenger_account_changed')
            if not binding:
                db.execute('INSERT INTO binding VALUES(?)', (account,))
        self.store = CollectionStore(root)
        self.target = identity('target-' + platform, 'local-db:' + account)
        with self.store.database() as db:
            present = db.execute('SELECT 1 FROM targets WHERE id=?', (self.target,)).fetchone()
        if not present:
            self.store.register(platform=platform, original_id=account, kind='local-db', label=platform,
                projects=[PROJECT], config={'adapter': 'messenger-evaluation', 'account': account})

    @contextmanager
    def database(self):
        if self.path.is_symlink():
            raise ValueError('messenger_frontier_unsafe')
        with closing(sqlite3.connect(self.path, timeout=.5)) as db:
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def seed(self, catalog):
        if catalog.get('account_id') != self.account or catalog.get('complete') is not True:
            raise ValueError('messenger_catalog_incomplete_or_wrong_account')
        rooms = catalog.get('rooms')
        if not isinstance(rooms, list) or len(rooms) > 100000:
            raise ValueError('messenger_catalog_invalid')
        ids = []
        for room in rooms:
            if not isinstance(room, dict) or not isinstance(room.get('id'), str) or not room['id'] or not isinstance(room.get('title'), str):
                raise ValueError('messenger_catalog_invalid')
            if room.get('body_blocked') is not None and (not isinstance(room['body_blocked'], str) or not room['body_blocked'] or len(room['body_blocked']) > 160):
                raise ValueError('messenger_catalog_invalid')
            ids.append(room['id'])
        if len(ids) != len(set(ids)):
            raise ValueError('messenger_catalog_duplicate')
        state = encoded({'back_cursor': None, 'back_done': False, 'check_cursor': None, 'live_head': None,
                         'live_cursor': None, 'live_new_head': None}).decode()
        with self.database() as db:
            for room in rooms:
                block = room.get('body_blocked')
                db.execute('''INSERT INTO rooms(id,title,frontier,error,retry_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,error=CASE WHEN excluded.error IS NOT NULL THEN excluded.error
                        WHEN rooms.error='telegram_context_consent_required' THEN NULL ELSE rooms.error END,
                    retry_at=CASE WHEN excluded.error IS NOT NULL THEN excluded.retry_at
                        WHEN rooms.error='telegram_context_consent_required' THEN 0 ELSE rooms.retry_at END''',
                    (room['id'], room['title'], state, block, time.time() + 60 if block else 0))
        return len(rooms)

    def _prepare(self, room, mode, page):
        if page.get('account_id') != self.account or page.get('room_id') != room['id']:
            raise ValueError('messenger_page_scope_mismatch')
        items = page.get('items')
        more, cursor = page.get('more'), page.get('next_cursor')
        if not isinstance(items, list) or len(items) > 50 or type(more) is not bool or more and (not isinstance(cursor, str) or not cursor):
            raise ValueError('messenger_page_invalid')
        if more and not items:
            raise ValueError('messenger_page_invalid')
        if len(encoded(page)) > 4 * 1024 * 1024:
            raise ValueError('messenger_page_budget')
        state = json.loads(room['frontier'])
        cursor_key = {'backfill': 'back_cursor', 'live': 'live_cursor', 'reconcile': 'check_cursor'}[mode]
        before = state.get(cursor_key)
        if more and cursor == before:
            raise ValueError('messenger_page_stalled')
        ids = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or type(item.get('visible')) is not bool:
                raise ValueError('messenger_item_invalid')
            if item['visible'] and not isinstance(item.get('body'), str) or not item['visible'] and item.get('body') is not None:
                raise ValueError('messenger_visibility_invalid')
            if not item['visible'] and set(item) - {'id', 'body', 'visible', 'sent_at', 'withheld_reason'}:
                raise ValueError('messenger_withheld_fields_invalid')
            if type(item.get('sent_at')) not in (int, float) or not math.isfinite(item['sent_at']) or item['sent_at'] < 0:
                raise ValueError('messenger_timestamp_invalid')
            ids.append(item['id'])
        if len(ids) != len(set(ids)):
            raise ValueError('messenger_page_duplicate')
        selected = items
        if mode == 'backfill':
            if not state['live_head'] and ids:
                state['live_head'] = ids[0]
            state.update(back_cursor=cursor if more else None, back_done=not more)
        elif mode == 'reconcile':
            # Revisit acquired rows independently of live-head discovery.
            # Absence alone is not evidence of source deletion.
            state['check_cursor'] = cursor if more else None
        else:
            if not state['live_new_head'] and ids:
                state['live_new_head'] = ids[0]
            reached = state['live_head'] in ids if state['live_head'] else False
            if reached:
                # Revisit the boundary item too: a changed/withdrawn newest
                # message must not remain visible just because its ID matches.
                selected = items[:ids.index(state['live_head']) + 1]
            if reached or not more:
                state.update(live_head=state['live_new_head'] or state['live_head'], live_cursor=None, live_new_head=None)
            else:
                state['live_cursor'] = cursor
        payload = {'account': self.account, 'platform': self.platform, 'room': room['id'],
                   'items': selected, 'mode': mode, 'before': before, 'reader_scope': page.get('scope'), 'quality_policy': POLICY}
        job = hashlib.sha256(encoded([payload, state])).hexdigest()
        with self.database() as db:
            db.execute('INSERT OR IGNORE INTO pages VALUES(?,?,?,?,?)',
                       (job, room['id'], encoded(payload).decode(), encoded(state).decode(), 'prepared'))
        return job

    def _publish(self, job, *, cancelled, after_publish):
        with self.database() as db:
            row = db.execute('SELECT * FROM pages WHERE id=?', (job,)).fetchone()
        if row is None:
            raise ValueError('messenger_prepared_page_missing')
        if self.reader.identity() != self.account:
            raise ValueError('messenger_account_changed')
        self._allowed()
        payload = json.loads(row['payload'])
        if payload.get('quality_policy') != POLICY:
            raise ValueError('messenger_prepared_policy_changed')
        authorization = self.reader.authorize(payload['room'])
        if self.platform == 'telegram' and authorization is not None:
            old_scope = payload.get('reader_scope')
            if not isinstance(old_scope, dict) or any(old_scope.get(k) != authorization[k] for k in ('since', 'until')):
                raise SourceBlocked('telegram_prepared_scope_changed')
        if row['state'] == 'complete':
            with self.database() as db:
                db.execute('UPDATE rooms SET frontier=?,checked=?,error=NULL,retry_at=0 WHERE id=?',
                           (row['next_frontier'], time.time(), row['room']))
            return {'state': 'unchanged', 'room_id': row['room'], 'job': job}
        records, retractions, updates, affected = [], [], [], set()
        for item in payload['items']:
            original = encoded([self.account, payload['room'], item['id']]).decode()
            triage = classify(item['body'])
            with self.database() as db:
                previous = db.execute('SELECT content_hash,state FROM message_heads WHERE id=?', (original,)).fetchone()
            if previous and previous['state'] == 'candidate':
                affected.add(previous['content_hash'])
            if triage.state == 'candidate':
                affected.add(triage.content_hash)
            updates.append((original, payload['room'], item['id'], triage.content_hash,
                            triage.state, triage.reason, triage.text if triage.state == 'candidate' else None, job))
        overrides = {value[0]: value for value in updates}
        for content_hash in sorted(affected):
            with self.database() as db:
                remaining = db.execute("SELECT 1 FROM message_heads WHERE content_hash=? AND state='candidate' AND id NOT IN ("
                    + ','.join('?' for _ in overrides) + ') LIMIT 1', (content_hash, *overrides)).fetchone()
            incoming = [r for r in updates if r[3] == content_hash and r[4] == 'candidate']
            key = {'platform': 'graph', 'original_id': 'messenger-content:' + content_hash}
            if not remaining and not incoming:
                retractions.append(key)
                continue
            if not incoming:
                continue
            body = incoming[0][6]
            title_text = text_without_urls(body)
            records.append({**key, 'kind': 'knowledge-candidate',
                'label': overview_display_label('', title_text) or ' '.join(title_text.split())[:80],
                'text': body, 'raw': {'text': body, 'policy': POLICY}, 'quality': 'unverified_candidate',
                'source': {'kind': 'messenger-exact-content-provenance', 'platform': self.platform,
                           'content_hash': content_hash, 'account_id': self.account,
                           'scope': payload['reader_scope'], 'provenance': 'query Frontier.origins; source pages retained'}})
        result = self.store.ingest(self.target, records, retractions=retractions,
            run_id='messenger-page:' + job, origin='messenger-db:' + self.platform + ':' + payload['mode'],
            processing_version=POLICY, cancelled=cancelled,
            cursor={'page': job, 'scope': 'one observed page; not complete DB coverage'})
        after_publish()
        with self.database() as db:
            db.executemany('''INSERT INTO message_heads VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                content_hash=excluded.content_hash,state=excluded.state,reason=excluded.reason,body=excluded.body,page=excluded.page''', updates)
            db.execute('UPDATE rooms SET frontier=?,checked=?,error=NULL,retry_at=0 WHERE id=?',
                       (row['next_frontier'], time.time(), row['room']))
            db.execute("UPDATE pages SET state='complete' WHERE id=?", (job,))
        return {'state': 'published', 'room_id': row['room'], 'job': job,
                'input_rows': len(payload['items']), 'text_records': len(records),
                'withheld': sum(not item['visible'] for item in payload['items']),
                'triage': {state: sum(r[4] == state for r in updates) for state in ('candidate', 'context', 'review', 'withheld')},
                'collection': result}

    def _allowed(self):
        target = self.store.target(self.target)
        if not target['enabled'] or not any(p['permission'] != 'denied' for p in target['projects']):
            raise SourceBlocked('messenger_target_disabled_or_denied')

    def _blocked(self, room, error):
        with self.database() as db:
            db.execute('UPDATE rooms SET checked=?,error=?,retry_at=? WHERE id=?',
                       (time.time(), str(error)[:160], time.time() + 60, room))
        return {'state': 'blocked', 'room_id': room, 'reason': str(error)}

    def step(self, *, mode='backfill', cancelled=lambda: False, after_publish=lambda: None):
        if mode not in {'backfill', 'live', 'reconcile'}:
            raise ValueError('messenger_mode_invalid')
        descriptor = os.open(self.folder / 'writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'state': 'busy'}
            if cancelled():
                return {'state': 'cancelled'}
            if self.reader.identity() != self.account:
                raise ValueError('messenger_account_changed')
            self._allowed()
            with self.database() as db:
                pending = db.execute("SELECT p.id,p.room FROM pages p JOIN rooms r ON r.id=p.room WHERE p.state='prepared' AND r.retry_at<=? ORDER BY p.rowid LIMIT 1", (time.time(),)).fetchone()
                rooms = db.execute("SELECT * FROM rooms r WHERE retry_at<=? AND NOT EXISTS(SELECT 1 FROM pages p WHERE p.room=r.id AND p.state='prepared') ORDER BY checked,id", (time.time(),)).fetchall()
            if pending:
                try:
                    return self._publish(pending['id'], cancelled=cancelled, after_publish=after_publish)
                except SourceBlocked as error:
                    return self._blocked(pending['room'], error)
            for room in rooms:
                state = json.loads(room['frontier'])
                if mode == 'backfill' and state['back_done'] or mode == 'live' and state['live_head'] is None:
                    continue
                try:
                    cursor = state.get({'backfill': 'back_cursor', 'live': 'live_cursor', 'reconcile': 'check_cursor'}[mode])
                    page = self.reader.page(room['id'], cursor)
                    job = self._prepare(room, mode, page)
                    return self._publish(job, cancelled=cancelled, after_publish=after_publish)
                except SourceBlocked as error:
                    return self._blocked(room['id'], error)
            return {'state': 'idle'}
        finally:
            os.close(descriptor)

    def status(self):
        with self.database() as db:
            rooms = db.execute('SELECT frontier,error FROM rooms').fetchall()
            pending = db.execute("SELECT count(*) FROM pages WHERE state='prepared'").fetchone()[0]
            triage = dict(db.execute('SELECT state,count(*) FROM message_heads GROUP BY state'))
            unique = db.execute("SELECT count(DISTINCT content_hash) FROM message_heads WHERE state='candidate'").fetchone()[0]
        return {'rooms': len(rooms), 'backfilled_rooms': sum(json.loads(r['frontier'])['back_done'] for r in rooms),
                'blocked_rooms': sum(r['error'] is not None for r in rooms), 'prepared_pages': pending,
                'triage': triage, 'unique_candidate_contents': unique, 'production_activated': False}

    def origins(self, content_hash, *, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('messenger_provenance_page_invalid')
        with self.database() as db:
            rows = db.execute('SELECT room,message,state,reason,page FROM message_heads WHERE content_hash=? ORDER BY room,message LIMIT ? OFFSET ?',
                              (content_hash, limit + 1, offset)).fetchall()
        return {'items': [dict(r) for r in rows[:limit]], 'has_more': len(rows) > limit,
                'next_offset': offset + limit if len(rows) > limit else None,
                'scope': 'this pinned platform/account; exact text match is not entity identity'}
