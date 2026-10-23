"""Isolated all-room messenger frontier candidate; not an installed watcher.

Readers must use their canonical account/visibility/consent gates. No transport,
source SQL, model, source mutation, account switch or fabricated activity lives
here. A durable prepared page bridges a crash between the two SQLite commits.
"""
from contextlib import closing, contextmanager
from pathlib import Path
from types import MappingProxyType
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import stat
import re
import time

from alden_collection import CollectionStore, encoded, identity, normalized_body, overview_display_label, safe_directory
from messenger_quality import POLICY, classify, text_without_urls
from alden_platform_identity import PlatformIdentityError, platform_uuid

MARKER = b'alden-isolated-messenger-evaluation-v1\n'
PROJECT = 'messenger-evaluation'


def _message_id_key(value):
    # One total order: unsigned ASCII decimals first, then opaque IDs in binary
    # order. Keep raw IDs distinct, including zero padding; never cast to int.
    if value.isascii() and value.isdecimal():
        magnitude = value.lstrip('0') or '0'
        return (0, len(magnitude), magnitude, value)
    return (1, 0, '', value)


def _compare_message_ids(left, right):
    a, b = _message_id_key(left), _message_id_key(right)
    return (a > b) - (a < b)


class SourceBlocked(RuntimeError):
    pass


class MessengerAuthority:
    """Explicit read-side composition; never discovers/adopts a native account."""
    def __init__(self, frontiers):
        if not isinstance(frontiers, (list, tuple)) or not frontiers:
            raise ValueError('messenger_authority_sources_required')
        sources, root = {}, None
        for frontier in frontiers:
            if not isinstance(frontier, Frontier):
                raise ValueError('messenger_authority_source_invalid')
            binding = (frontier.root.absolute(), frontier.platform, frontier.account, frontier.target)
            if root is not None and root != binding[0]:
                raise ValueError('messenger_authority_roots_differ')
            if frontier.target in sources:
                raise ValueError('messenger_authority_target_ambiguous')
            root = binding[0]
            if frontier.target != identity('target-' + frontier.platform, 'local-db:' + frontier.account):
                raise ValueError('messenger_authority_binding_invalid')
            sources[frontier.target] = (frontier, binding)
        self.root, self.sources = root, MappingProxyType(sources)

    def collection_source_root(self):
        for frontier, binding in self.sources.values():
            if self.root != binding[0] or frontier.collection_source_root() != binding[0]:
                raise ValueError('messenger_authority_binding_invalid')
        return self.root

    def _source(self, target):
        if not isinstance(target, str):
            return None
        entry = self.sources.get(target)
        if entry is None:
            return None
        frontier, binding = entry
        if (frontier.root.absolute(), frontier.platform, frontier.account, frontier.target) != binding:
            return None
        return frontier

    def access(self, target, document, version, metadata):
        source = self._source(target)
        return source is not None and source._source_access(target, document, version, metadata) is True

    def revision(self, target):
        source = self._source(target)
        return None if source is None else source._source_revision(target)

    def context_window(self, target, room, message, **options):
        source = self._source(target)
        if source is None:
            raise SourceBlocked('messenger_authority_source_unavailable')
        return source.context_window(room, message, **options)

    def retrieval_context(self, proof, *, projects, source_root, cancelled=lambda: False, expected_window=None):
        """Fresh source excerpts for one selected document, never inferred replies."""
        if Path(source_root).absolute() != self.root:
            raise ValueError('messenger_context_root_mismatch')
        derived = proof.get('derived_from', {})
        content = derived.get('content_hash')
        if (derived.get('kind') != 'messenger-exact-content-provenance'
            or not isinstance(content, str) or not re.fullmatch(r'[0-9a-f]{64}', content)
            or proof.get('entity_id') != identity('graph', 'messenger-content:' + content)):
            raise ValueError('messenger_context_document_invalid')
        if expected_window is not None:
            return self._retrieval_window_current(proof, expected_window, projects, cancelled)
        targets = sorted({m['target_id'] for m in proof['source_memberships']})
        candidates = []
        for target in targets:
            source = self._source(target)
            if source is None:
                continue
            with source.database() as db:
                rows = db.execute('''SELECT room,message FROM message_heads
                    WHERE content_hash=? AND state='candidate' ORDER BY room,message LIMIT 2''', (content,)).fetchall()
            candidates.append([(source, row) for row in rows])
        # Try distinct permitted targets before a duplicate origin of one target.
        observations = [rows[index] for index in range(2) for rows in candidates if len(rows) > index][:2]
        for source, origin in observations:
            if cancelled():
                raise RuntimeError('collection_retrieval_cancelled')
            try:
                with source.store.database() as db:
                    current = db.execute('''SELECT v.metadata FROM memberships m JOIN versions v
                        ON v.id=m.current_version WHERE m.target_id=? AND m.document_id=?
                        AND m.current_version=? AND m.availability='available' ''',
                        (source.target, proof['entity_id'], proof['source_version'])).fetchone()
                if current is None or source._source_access(source.target, proof['entity_id'],
                        proof['source_version'], json.loads(current['metadata'])) is not True:
                    continue
                window = source.context_window(origin['room'], origin['message'], projects=projects, before=1, after=1)
                anchor = next(r for r in window['items'] if r['anchor'])
                if anchor['state'] != 'candidate' or anchor['content_hash'] != content:
                    raise SourceBlocked('messenger_context_anchor_changed')
                window = {key:value for key,value in window.items() if key != 'account_id'}
                return {'state':'current','source_targets':[source.target],
                    'window':{**window,'target_id':source.target,
                        'entity_id':proof['entity_id'],'source_version':proof['source_version']}}
            except SourceBlocked:
                continue
        return {'state':'unavailable','reason':'current_bounded_source_excerpt_unavailable'}

    def _retrieval_window_current(self, proof, window, projects, cancelled):
        """Final bounded metadata checkpoint; no second canonical body read."""
        if cancelled():
            raise RuntimeError('collection_retrieval_cancelled')
        source = self._source(window['target_id'])
        if source is None or window['entity_id'] != proof['entity_id'] or window['source_version'] != proof['source_version']:
            return False
        if source._source_revision(source.target) != window['authority_revision']:
            raise RuntimeError('collection_source_authorization_changed')
        if not any(p['project'] in projects and p['permission'] != 'denied'
                   for p in source.store.target(source.target)['projects']):
            raise RuntimeError('collection_source_authorization_changed')
        with source.store.database() as db:
            if db.execute('''SELECT 1 FROM memberships WHERE target_id=? AND document_id=?
                AND current_version=? AND availability='available' ''',
                (source.target,proof['entity_id'],proof['source_version'])).fetchone() is None:
                return False
        with source.database() as db:
            db.execute('BEGIN')
            room = db.execute('SELECT frontier FROM rooms WHERE id=?', (window['room_id'],)).fetchone()
            if room is None or source._epoch(json.loads(room['frontier'])) != window['source_epoch']:
                return False
            for item in window['items']:
                head = db.execute('''SELECT h.*,p.payload,p.next_frontier FROM message_heads h
                    JOIN pages p ON p.id=h.page WHERE h.room=? AND h.message=? AND p.state='complete' ''',
                    (window['room_id'], item['message_id'])).fetchone()
                if head is None or any(head[k] != item[v] for k,v in
                        (('page','source_page'),('sent_at','sent_at'),('state','state'),('content_hash','content_hash'))):
                    return False
                payload = source._current_page(head)
                current = [r for r in payload['items'] if r['id'] == item['message_id']]
                if len(current) != 1 or current[0]['body'] != item['body']:
                    return False
        with source.database() as db:
            room = db.execute('SELECT frontier FROM rooms WHERE id=?', (window['room_id'],)).fetchone()
            if room is None or source._epoch(json.loads(room['frontier'])) != window['source_epoch']:
                return False
        if source._source_revision(source.target) != window['authority_revision']:
            raise RuntimeError('collection_source_authorization_changed')
        return True


def verify_prepared_items(prepared, observed):
    """Current visible bytes must agree; an absent window proves no deletion."""
    current = {item['id']: item for item in observed}
    if len(current) != len(observed):
        raise SourceBlocked('messenger_revalidation_duplicate')
    for before in prepared:
        after = current.get(before['id'])
        if after is None:
            raise SourceBlocked('messenger_prepared_window_incomplete')
        if before != after:
            raise SourceBlocked('messenger_prepared_source_changed')


class KakaoReader:
    """Adapter over the canonical reader; never SQL or a copied decoder."""
    def __init__(self, canonical):
        self.canonical = canonical

    def identity(self):
        try:
            device = platform_uuid()
        except PlatformIdentityError as error:
            raise SourceBlocked('kakao_current_identity_unavailable') from error
        return self.canonical.resolve_identity(device)[0]

    def authorize(self, room):
        # snapshot() independently verifies the current owner before/after its
        # read-only transaction and withholds hidden source fields at SQL read.
        self.identity()

    def authorization_scope(self):
        return {'account': self.identity(), 'policy': 'canonical-current-account'}

    def catalog(self):
        account = self.identity()
        result = self.canonical.snapshot(catalog_only=True)
        if result.get('status') != 'ok' or result.get('catalog_truncated') is not False or self.identity() != account:
            raise SourceBlocked('kakao_catalog_unavailable_or_changed')
        return {'account_id': account, 'complete': True,
                'rooms': [{'id': r['room_id'], 'title': r['title']} for r in result['rooms']]}

    def page(self, room, cursor, *, message_id=None):
        account = self.identity()
        result = self.canonical.snapshot(chat_id=room, show_text=True,
            limit=1 if message_id is not None else 50, cursor=cursor,
            **({'message_id': message_id} if message_id is not None else {}))
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

    def verify_prepared(self, payload):
        verify_prepared_items(payload['items'], self.refresh_prepared(payload)['items'])

    def refresh_prepared(self, payload):
        # Exact-ID lookup avoids advancing or replacing the retained page when
        # newer arrivals have moved its rows out of the latest window.
        items, scope, account = [], None, self.identity()
        for item in payload['items']:
            page = self.page(payload['room'], None, message_id=item['id'])
            current = [r for r in page['items'] if r['id'] == item['id']]
            if len(current) != 1 or len(page['items']) != 1:
                raise SourceBlocked('messenger_prepared_window_incomplete')
            items.extend(current); scope = page['scope']
        if self.identity() != account:
            raise SourceBlocked('kakao_account_changed_during_refresh')
        return {'account_id': account, 'room_id': payload['room'], 'items': items,
                'more': False, 'next_cursor': None, 'scope': scope}


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
        authorization = self.authorizer(self.identity(), room, scope['since'], scope['until'])
        if isinstance(authorization, dict) and authorization:
            scope['authorization_sha256'] = hashlib.sha256(encoded(authorization)).hexdigest()
        return scope

    def authorization_scope(self):
        account, rooms = self.identity(), []
        for room in sorted(self.scopes):
            try:
                scope = self.authorize(room)
            except SourceBlocked:
                scope = {'state': 'withheld'}
            rooms.append([room, scope])
        if self.identity() != account:
            raise SourceBlocked('telegram_account_changed_during_authorization')
        return {'account': account, 'rooms': rooms}

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
                          'decode_errors': result.get('decode_errors'),
                          **({'authorization_sha256': scope['authorization_sha256']}
                             if 'authorization_sha256' in scope else {})}}

    def verify_prepared(self, payload):
        page = self.refresh_prepared(payload)
        # The canonical CLI currently has no exact-ID lookup. A moved or
        # missing window blocks recovery, never implies source withdrawal.
        verify_prepared_items(payload['items'], page['items'])

    def refresh_prepared(self, payload):
        page = self.page(payload['room'], payload.get('reader_before', payload['before']))
        if len({r['id'] for r in page['items']}) != len(page['items']):
            raise SourceBlocked('messenger_revalidation_duplicate')
        wanted = {item['id'] for item in payload['items']}
        selected = [item for item in page['items'] if item['id'] in wanted]
        if len(selected) != len(wanted) or len({r['id'] for r in selected}) != len(wanted):
            raise SourceBlocked('messenger_prepared_window_incomplete')
        return {**page, 'items': selected, 'more': False, 'next_cursor': None}


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
        self._source_root = root.absolute()
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
            db.execute('CREATE INDEX IF NOT EXISTS message_content_origin ON message_heads(content_hash,state,room,message)')
            db.execute('''CREATE TABLE IF NOT EXISTS head_fingerprints(
                id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL)''')
            binding = db.execute('SELECT account FROM binding').fetchone()
            if binding and binding[0] != account:
                raise ValueError('messenger_account_changed')
            if not binding:
                db.execute('INSERT INTO binding VALUES(?)', (account,))
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            claims_exist = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='page_content_claims'").fetchone()
            db.execute('''CREATE TABLE IF NOT EXISTS page_content_claims(
                page TEXT NOT NULL,content_hash TEXT NOT NULL,PRIMARY KEY(page,content_hash))''')
            db.execute('CREATE INDEX IF NOT EXISTS content_claim_pages ON page_content_claims(content_hash,page)')
            db.execute('CREATE TABLE IF NOT EXISTS retired_policy_pages(page TEXT PRIMARY KEY,reason TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS activity_releases(
                page TEXT PRIMARY KEY,state TEXT NOT NULL CHECK(state IN ('pending','settled','retired')),
                error TEXT,retry_at REAL NOT NULL DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS room_mode_checks(
                room TEXT NOT NULL,mode TEXT NOT NULL,position INTEGER NOT NULL CHECK(position>0),
                PRIMARY KEY(room,mode))''')
            db.execute('CREATE INDEX IF NOT EXISTS mode_check_order ON room_mode_checks(mode,position,room)')
            if not claims_exist:
                for pending in db.execute("SELECT id,room,payload FROM pages WHERE state='prepared'"):
                    self._claim_page_content(db, pending['id'], pending['room'], json.loads(pending['payload'])['items'])
            if 'sent_at' not in {r['name'] for r in db.execute('PRAGMA table_info(message_heads)')}:
                db.execute('ALTER TABLE message_heads ADD COLUMN sent_at REAL')
                pages = {}
                for head in db.execute('''SELECT h.id,h.room,h.message,h.page,p.payload,p.next_frontier
                    FROM message_heads h JOIN pages p ON p.id=h.page WHERE p.state='complete' '''):
                    if head['page'] not in pages:
                        try:
                            pages[head['page']] = {item['id']: item for item in self._retained_page(head)['items']}
                        except SourceBlocked:
                            pages[head['page']] = None  # Old policies require fresh canonical observations.
                    if pages[head['page']] is None:
                        continue
                    item = pages[head['page']].get(head['message'])
                    if item is None or type(item.get('sent_at')) not in (int, float) or not math.isfinite(item['sent_at']) or item['sent_at'] < 0:
                        raise ValueError('messenger_context_timestamp_missing')
                    db.execute('UPDATE message_heads SET sent_at=? WHERE id=?', (item['sent_at'], head['id']))
            db.execute('CREATE INDEX IF NOT EXISTS message_context_time ON message_heads(room,sent_at,message)')
        self.store = CollectionStore(root, source_access=self._source_access, source_revision=self._source_revision)
        self.target = identity('target-' + platform, 'local-db:' + account)
        with self.store.database() as db:
            present = db.execute('SELECT 1 FROM targets WHERE id=?', (self.target,)).fetchone()
        if not present:
            self.store.register(platform=platform, original_id=account, kind='local-db', label=platform,
                projects=[PROJECT], config={'adapter': 'messenger-evaluation', 'account': account})

    def collection_source_root(self):
        root = self._source_root
        if (self.root.absolute() != root
            or self.path.parent.parent != root
            or hasattr(self, 'store') and self.store.path != root / 'knowledge/collection/collection.sqlite3'):
            raise ValueError('messenger_frontier_binding_invalid')
        return root

    @contextmanager
    def database(self):
        if self.path.is_symlink():
            raise ValueError('messenger_frontier_unsafe')
        with closing(sqlite3.connect(self.path, timeout=.5)) as db:
            db.row_factory = sqlite3.Row
            db.create_collation('alden_message_id', _compare_message_ids)
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

    @staticmethod
    def _epoch(state):
        epoch = state.get('source_epoch', 0)
        if type(epoch) is not int or epoch < 0:
            raise ValueError('messenger_source_epoch_invalid')
        return epoch

    def _observation(self, room, item):
        original = encoded([self.account, room, item['id']]).decode()
        fingerprint = hashlib.sha256(encoded([POLICY, item])).hexdigest()
        return original, fingerprint, classify(item['body'])

    def _heads_match(self, db, room, items):
        for item in items:
            original, fingerprint, triage = self._observation(room, item)
            head = db.execute('''SELECT h.content_hash,h.state,h.reason,f.fingerprint
                FROM message_heads h LEFT JOIN head_fingerprints f ON f.id=h.id
                WHERE h.id=?''', (original,)).fetchone()
            if head is None or tuple(head) != (triage.content_hash, triage.state, triage.reason, fingerprint):
                return False
        return True

    def _claim_page_content(self, db, job, room, items):
        affected = set()
        for item in items:
            original, _, triage = self._observation(room, item)
            previous = db.execute('SELECT content_hash,state FROM message_heads WHERE id=?', (original,)).fetchone()
            if previous and previous['state'] == 'candidate':
                affected.add(previous['content_hash'])
            if triage.state == 'candidate':
                affected.add(triage.content_hash)
        db.executemany('INSERT OR IGNORE INTO page_content_claims VALUES(?,?)',
                       [(job, content_hash) for content_hash in sorted(affected)])

    def _prepare(self, room, mode, page, *, policy_reprocess=False, observation_cursor=None):
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
        epoch = self._epoch(state)
        cursor_key = {'backfill': 'back_cursor', 'live': 'live_cursor', 'reconcile': 'check_cursor', 'policy': 'policy_cursor'}[mode]
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
        elif mode == 'policy':
            state['policy_cursor'] = None
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
        with self.database() as db:
            current = db.execute('SELECT frontier FROM rooms WHERE id=?', (room['id'],)).fetchone()
            if current is None or current['frontier'] != room['frontier']:
                raise SourceBlocked('messenger_prepare_checkpoint_changed')
            # A -> B -> A is a new observation, even though A's content and
            # cursor match an old completed page. Stable observations reuse it.
            changed = not self._heads_match(db, room['id'], selected)
            state['source_epoch'] = epoch + 1 if changed else epoch
            payload = {'account': self.account, 'platform': self.platform, 'room': room['id'],
                       'items': selected, 'mode': mode, 'before': before, 'reader_scope': page.get('scope'),
                       'quality_policy': POLICY, 'source_epoch': state['source_epoch'],
                       'activity_protocol': 'frontier-outbox-v1'}
            if policy_reprocess:
                payload['policy_reprocess'] = True
                payload['reader_before'] = observation_cursor
            job = hashlib.sha256(encoded([payload, state])).hexdigest()
            db.execute('INSERT OR IGNORE INTO pages VALUES(?,?,?,?,?)',
                       (job, room['id'], encoded(payload).decode(), encoded(state).decode(), 'prepared'))
            if changed:
                self._claim_page_content(db, job, room['id'], selected)
        return job

    def _publish(self, job, *, cancelled, after_publish, revalidate=False):
        with self.database() as db:
            row = db.execute('SELECT * FROM pages WHERE id=?', (job,)).fetchone()
        if row is None:
            raise ValueError('messenger_prepared_page_missing')
        payload = json.loads(row['payload'])
        if hashlib.sha256(encoded([payload, json.loads(row['next_frontier'])])).hexdigest() != job:
            raise ValueError('messenger_prepared_integrity')
        if payload.get('quality_policy') != POLICY:
            raise ValueError('messenger_prepared_policy_changed')
        next_state = json.loads(row['next_frontier'])
        with self.database() as db:
            current = db.execute('SELECT frontier FROM rooms WHERE id=?', (row['room'],)).fetchone()
            if current is None:
                raise ValueError('messenger_prepared_room_missing')
            current_state = json.loads(current['frontier'])
            epoch = self._epoch(current_state)
            following = self._epoch(next_state)
            legacy = 'source_epoch' not in payload and 'source_epoch' not in next_state
            if not legacy and ('source_epoch' not in payload or 'source_epoch' not in next_state
                               or self._epoch(payload) != following):
                raise ValueError('messenger_prepared_epoch_mismatch')
            if legacy:
                if epoch != 0:
                    raise SourceBlocked('messenger_prepared_epoch_stale')
            elif following not in (epoch, epoch + 1) or row['state'] == 'complete' and following != epoch:
                raise SourceBlocked('messenger_prepared_epoch_stale')
            unchanged = row['state'] == 'complete' or not legacy and following == epoch
            if unchanged and not self._heads_match(db, row['room'], payload['items']):
                raise SourceBlocked('messenger_prepared_observation_conflict')
            if not unchanged and db.execute('''SELECT 1 FROM page_content_claims mine
                JOIN page_content_claims earlier ON earlier.content_hash=mine.content_hash
                JOIN pages p ON p.id=earlier.page
                WHERE mine.page=? AND p.state='prepared'
                  AND NOT EXISTS(SELECT 1 FROM retired_policy_pages x WHERE x.page=p.id)
                  AND p.rowid<(SELECT rowid FROM pages WHERE id=?) LIMIT 1''', (job, job)).fetchone():
                # A pending store commit must reach its origin-head commit
                # before another room can invalidate that same content's run.
                raise SourceBlocked('messenger_shared_content_pending')
        self._authorize_publication(payload)
        if revalidate and row['state'] == 'prepared':
            self.reader.verify_prepared(payload)
            self._authorize_publication(payload)
        if cancelled():
            raise RuntimeError('collection_cancelled')
        if unchanged:
            fields = {'backfill': ('back_cursor', 'back_done', 'live_head'),
                      'live': ('live_cursor', 'live_new_head', 'live_head'),
                      'reconcile': ('check_cursor',), 'policy': ('policy_cursor',)}[payload['mode']]
            if current_state.get(fields[0]) != payload['before']:
                raise SourceBlocked('messenger_prepared_cursor_changed')
            # A repeated reconcile sweep may revisit a completed page. Advance
            # only its own cursor; never rewind another mode's later progress.
            current_state.update({key: next_state[key] for key in fields})
            with self.database() as db:
                if row['state'] == 'prepared':
                    db.executemany('UPDATE message_heads SET page=? WHERE id=?',
                        [(job, self._observation(row['room'], item)[0]) for item in payload['items']])
                db.execute('UPDATE rooms SET frontier=?,checked=?,error=NULL,retry_at=0 WHERE id=?',
                           (encoded(current_state).decode(), time.time(), row['room']))
                self._checked(db, row['room'], payload['mode'])
                db.execute("UPDATE pages SET state='complete' WHERE id=?", (job,))
            if payload.get('activity_protocol') == 'frontier-outbox-v1':
                self._release_activity(job, cancelled=cancelled)
            return {'state': 'unchanged', 'room_id': row['room'], 'job': job}
        records, retractions, updates, fingerprints, affected = [], [], [], [], set()
        for item in payload['items']:
            original, fingerprint, triage = self._observation(payload['room'], item)
            fingerprints.append((original, fingerprint))
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
            run_id='messenger-page:' + job,
            origin=('messenger-policy:' if payload.get('policy_reprocess') is True else 'messenger-db:') + self.platform + ':' + payload['mode'],
            processing_version=POLICY, cancelled=cancelled,
            publication_guard=lambda: self._publication_guard(payload),
            defer_activity=payload.get('activity_protocol') == 'frontier-outbox-v1',
            cursor={'page': job, 'scope': 'one observed page; not complete DB coverage'})
        after_publish()
        with self.database() as db:
            db.executemany('''INSERT INTO message_heads(id,room,message,content_hash,state,reason,body,page)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                content_hash=excluded.content_hash,state=excluded.state,reason=excluded.reason,body=excluded.body,page=excluded.page''', updates)
            db.executemany('''INSERT INTO head_fingerprints VALUES(?,?) ON CONFLICT(id) DO UPDATE SET
                fingerprint=excluded.fingerprint''', fingerprints)
            db.executemany('UPDATE message_heads SET sent_at=? WHERE id=?',
                [(item['sent_at'], self._observation(row['room'], item)[0]) for item in payload['items']])
            db.execute('UPDATE rooms SET frontier=?,checked=?,error=NULL,retry_at=0 WHERE id=?',
                       (row['next_frontier'], time.time(), row['room']))
            self._checked(db, row['room'], payload['mode'])
            db.execute("UPDATE pages SET state='complete' WHERE id=?", (job,))
            if payload.get('activity_protocol') == 'frontier-outbox-v1':
                db.execute("INSERT OR IGNORE INTO activity_releases(page,state) VALUES(?,'pending')", (job,))
        if payload.get('activity_protocol') == 'frontier-outbox-v1':
            self._release_activity(job, cancelled=cancelled)
        return {'state': 'published', 'room_id': row['room'], 'job': job,
                'input_rows': len(payload['items']), 'text_records': len(records),
                'withheld': sum(not item['visible'] for item in payload['items']),
                'triage': {state: sum(r[4] == state for r in updates) for state in ('candidate', 'context', 'review', 'withheld')},
                'collection': result}

    def _release_activity(self, job, *, cancelled, revalidate=False):
        with self.database() as db:
            row = db.execute('''SELECT p.id AS page,p.room,p.payload,p.next_frontier,p.state
                FROM pages p JOIN activity_releases a ON a.page=p.id
                WHERE p.id=? AND a.state='pending' ''', (job,)).fetchone()
        if row is None:
            return {'state': 'activity_settled', 'job': job}
        if row['state'] != 'complete':
            raise ValueError('messenger_activity_frontier_incomplete')
        payload = self._current_page(row)
        if payload.get('activity_protocol') != 'frontier-outbox-v1':
            raise ValueError('messenger_activity_protocol_invalid')
        if revalidate:
            self.reader.verify_prepared(payload)
        self._authorize_publication(payload)
        self.store.release_activity(self.target, 'messenger-page:' + job, cancelled=cancelled,
                                    publication_guard=lambda: self._publication_guard(payload))
        with self.database() as db:
            db.execute("UPDATE activity_releases SET state='settled',error=NULL,retry_at=0 WHERE page=?", (job,))
        return {'state': 'activity_released', 'room_id': row['room'], 'job': job}

    def _authorize_publication(self, payload):
        if self.reader.identity() != self.account:
            raise ValueError('messenger_account_changed')
        self._allowed()
        authorization = self.reader.authorize(payload['room'])
        if self.platform == 'telegram' and authorization is not None:
            old_scope = payload.get('reader_scope')
            if not isinstance(old_scope, dict) or any(old_scope.get(k) != authorization[k] for k in ('since', 'until')):
                raise SourceBlocked('telegram_prepared_scope_changed')
            if old_scope.get('authorization_sha256') != authorization.get('authorization_sha256'):
                raise SourceBlocked('telegram_prepared_authorization_changed')

    def _source_revision(self, target):
        if target != self.target:
            return None
        account = self.reader.identity()
        if account != self.account:
            return hashlib.sha256(encoded(['account_changed', self.account, POLICY])).hexdigest()
        scope = getattr(self.reader, 'authorization_scope', None)
        if callable(scope):
            state = scope()
        else:
            # Custom evaluation readers have no batch capability. Native
            # readers provide metadata-only scope snapshots above.
            with self.database() as db:
                rooms = [row[0] for row in db.execute('SELECT id FROM rooms ORDER BY id')]
            state = []
            for room in rooms:
                try:
                    authorization = self.reader.authorize(room)
                except SourceBlocked:
                    authorization = {'state': 'withheld'}
                state.append([room, authorization])
        if self.reader.identity() != account:
            raise SourceBlocked('messenger_account_changed_during_authorization')
        projects = sorted((p['project'], p['permission']) for p in self.store.target(target)['projects'])
        return hashlib.sha256(encoded([self.platform, account, POLICY, state, projects])).hexdigest()

    def _source_access(self, target, document, version, metadata):
        """Current origin authority is required even when collection is stopped."""
        if not isinstance(metadata, dict):
            return False
        source = metadata.get('source', {})
        if (target != self.target or not isinstance(source, dict)
            or source.get('kind') != 'messenger-exact-content-provenance'):
            return False
        content_hash = source.get('content_hash')
        if (not isinstance(content_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', content_hash)
            or document != identity('graph', 'messenger-content:' + content_hash)
            or self.reader.identity() != self.account):
            return False
        with self.store.database() as stored:
            current = stored.execute('''SELECT v.body,v.metadata FROM memberships m
                JOIN versions v ON v.id=m.current_version JOIN documents d ON d.id=m.document_id
                WHERE m.target_id=? AND m.document_id=? AND m.current_version=?
                  AND m.availability='available' AND d.availability='available' ''',
                (target, document, version)).fetchone()
        if current is None or json.loads(current['metadata']).get('source', {}).get('content_hash') != content_hash:
            return False
        def matches(payload, message=None):
            for item in payload['items']:
                if not item['visible'] or message is not None and item['id'] != message:
                    continue
                triage = classify(item['body'])
                if (triage.state == 'candidate' and triage.content_hash == content_hash
                    and normalized_body(triage.text) == current['body']):
                    return True
            return False
        with self.database() as db:
            origins = db.execute('''SELECT h.room,h.message,h.page,p.payload,p.next_frontier
                FROM message_heads h JOIN pages p ON p.id=h.page
                WHERE h.content_hash=? AND h.state='candidate' AND p.state='complete'
                ORDER BY h.room,h.message''', (content_hash,))
            for origin in origins:
                try:
                    payload = self._current_page(origin)
                except SourceBlocked:
                    continue
                except ValueError:
                    return False
                if matches(payload, origin['message']):
                    return True
        return False

    def _retained_page(self, origin):
        payload = json.loads(origin['payload'])
        if (hashlib.sha256(encoded([payload, json.loads(origin['next_frontier'])])).hexdigest() != origin['page']
            or payload.get('account') != self.account or payload.get('platform') != self.platform
            or payload.get('room') != origin['room']):
            raise ValueError('messenger_origin_integrity')
        if payload.get('quality_policy') != POLICY:
            raise SourceBlocked('messenger_origin_policy_changed')
        return payload

    def _current_page(self, origin):
        payload = self._retained_page(origin)
        authorization = self.reader.authorize(origin['room'])
        if self.platform == 'telegram':
            scope = payload.get('reader_scope')
            if (not isinstance(authorization, dict) or not isinstance(scope, dict)
                or not authorization.get('authorization_sha256')
                or any(scope.get(k) != authorization.get(k) for k in ('since', 'until', 'authorization_sha256'))):
                raise SourceBlocked('messenger_origin_authorization_changed')
        return payload

    def context_window(self, room, message, *, projects, before=3, after=3, max_gap=900):
        """Current, bounded source transcript; proximity is not a reply/fact edge."""
        if (not isinstance(room, str) or not room or not isinstance(message, str) or not message
            or type(before) is not int or not 0 <= before <= 5
            or type(after) is not int or not 0 <= after <= 5
            or type(max_gap) is not int or not 0 <= max_gap <= 3600
            or not isinstance(projects, list) or not projects
            or any(not isinstance(p, str) or not p for p in projects)):
            raise ValueError('messenger_context_query_invalid')
        def permission():
            if not any(p['project'] in projects and p['permission'] != 'denied'
                       for p in self.store.target(self.target)['projects']):
                raise SourceBlocked('messenger_context_project_denied')
        permission()
        if self.reader.identity() != self.account:
            raise SourceBlocked('messenger_context_account_changed')
        revision = self._source_revision(self.target)
        with self.database() as db:
            db.execute('BEGIN')
            frontier = db.execute('SELECT frontier FROM rooms WHERE id=?', (room,)).fetchone()
            anchor = db.execute('SELECT * FROM message_heads WHERE room=? AND message=?', (room, message)).fetchone()
            if frontier is None or anchor is None or anchor['sent_at'] is None:
                raise SourceBlocked('messenger_context_anchor_unavailable')
            unplaced = bool(db.execute('SELECT 1 FROM message_heads WHERE room=? AND sent_at IS NULL LIMIT 1', (room,)).fetchone())
            at = anchor['sent_at']
            older = db.execute('''SELECT * FROM message_heads WHERE room=? AND sent_at>=? AND sent_at<=?
                AND (sent_at<? OR sent_at=? AND message COLLATE alden_message_id<?)
                ORDER BY sent_at DESC,message COLLATE alden_message_id DESC LIMIT ?''',
                (room, at-max_gap, at, at, at, message, before)).fetchall()
            newer = db.execute('''SELECT * FROM message_heads WHERE room=? AND sent_at<=? AND sent_at>=?
                AND (sent_at>? OR sent_at=? AND message COLLATE alden_message_id>?)
                ORDER BY sent_at,message COLLATE alden_message_id LIMIT ?''',
                (room, at+max_gap, at, at, at, message, after)).fetchall()
            heads = [*reversed(older), anchor, *newer]
            pages, items = {}, []
            for head in heads:
                origin = db.execute('''SELECT h.room,h.page,p.payload,p.next_frontier FROM message_heads h
                    JOIN pages p ON p.id=h.page WHERE h.id=? AND p.state='complete' ''', (head['id'],)).fetchone()
                if origin is None:
                    raise SourceBlocked('messenger_context_page_unavailable')
                if head['page'] not in pages:
                    pages[head['page']] = [self._current_page(origin), []]
                payload, selected = pages[head['page']]
                matches = [item for item in payload['items'] if item['id'] == head['message']]
                fingerprint = db.execute('SELECT fingerprint FROM head_fingerprints WHERE id=?', (head['id'],)).fetchone()
                if len(matches) != 1 or fingerprint is None:
                    raise SourceBlocked('messenger_context_observation_missing')
                item = matches[0]
                _, observed, triage = self._observation(room, item)
                if (observed != fingerprint[0] or item['sent_at'] != head['sent_at']
                    or triage.content_hash != head['content_hash'] or triage.state != head['state']
                    or triage.reason != head['reason']
                    or (triage.text if triage.state == 'candidate' else None) != head['body']):
                    raise SourceBlocked('messenger_context_observation_changed')
                selected.append(item)
                items.append({'message_id': item['id'], 'sent_at': item['sent_at'], 'body': item['body'],
                    'state': triage.state, 'reason': triage.reason, 'source_page': head['page'],
                    'content_hash': triage.content_hash, 'anchor': item['id'] == message})
            if len(encoded(items)) > 65536:
                raise SourceBlocked('messenger_context_budget')
        # Source reads retain each adapter's current visibility/consent guard.
        # Check only this bounded span, never reacquire the entire room here.
        for payload, selected in pages.values():
            self.reader.verify_prepared({**payload, 'items': selected})
        permission()
        if self.reader.identity() != self.account or self._source_revision(self.target) != revision:
            raise SourceBlocked('messenger_context_authorization_changed')
        with self.database() as db:
            current = db.execute('SELECT frontier FROM rooms WHERE id=?', (room,)).fetchone()
            if current is None or self._epoch(json.loads(current['frontier'])) != self._epoch(json.loads(frontier['frontier'])):
                raise SourceBlocked('messenger_context_frontier_changed')
            for head in heads:
                current = db.execute('SELECT * FROM message_heads WHERE id=?', (head['id'],)).fetchone()
                if current is None or dict(current) != dict(head):
                    raise SourceBlocked('messenger_context_frontier_changed')
        window_id = hashlib.sha256(encoded([self.platform, self.account, room, message, revision,
            [(r['message_id'], r['source_page']) for r in items]])).hexdigest()
        return {'platform': self.platform, 'account_id': self.account, 'room_id': room,
            'anchor_message': message, 'items': items, 'authority_revision': revision,
            'window_id': window_id, 'quality_policy': POLICY,
            'source_epoch': self._epoch(json.loads(frontier['frontier'])),
            'has_unplaced_rows': unplaced, 'coverage': 'committed observed rows only; not full conversation history',
            'scope': 'bounded cached conversation; time proximity is not a reply, identity, agreement or established fact',
            'order': 'sent_at then unsigned ASCII decimal IDs by magnitude/raw ID, then opaque IDs in binary order; '
                     'equal-time order is not conversational causality'}
    @contextmanager
    def _publication_guard(self, payload):
        self._authorize_publication(payload)
        yield

    def _allowed(self):
        target = self.store.target(self.target)
        if not target['enabled'] or not any(p['permission'] != 'denied' for p in target['projects']):
            raise SourceBlocked('messenger_target_disabled_or_denied')

    @staticmethod
    def _checked(db, room, mode):
        if mode not in {'backfill', 'live', 'reconcile'}:
            return
        position = db.execute('SELECT coalesce(max(position),0)+1 FROM room_mode_checks WHERE mode=?', (mode,)).fetchone()[0]
        db.execute('''INSERT INTO room_mode_checks VALUES(?,?,?) ON CONFLICT(room,mode)
            DO UPDATE SET position=excluded.position''', (room,mode,position))

    def _blocked(self, room, error, mode):
        with self.database() as db:
            db.execute('UPDATE rooms SET checked=?,error=?,retry_at=? WHERE id=?',
                       (time.time(), str(error)[:160], time.time() + 60, room))
            self._checked(db, room, mode)
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
                release = db.execute('''SELECT a.page FROM activity_releases a JOIN pages p ON p.id=a.page
                    WHERE a.state='pending' AND a.retry_at<=? ORDER BY p.rowid LIMIT 1''', (time.time(),)).fetchone()
                pending = db.execute("SELECT p.id,p.room,json_extract(p.payload,'$.mode') AS mode FROM pages p JOIN rooms r ON r.id=p.room WHERE p.state='prepared' AND NOT EXISTS(SELECT 1 FROM retired_policy_pages x WHERE x.page=p.id) AND r.retry_at<=? ORDER BY p.rowid LIMIT 1", (time.time(),)).fetchone()
                eligibility = {'backfill': "json_extract(r.frontier,'$.back_done')=0",
                    'live': "(json_extract(r.frontier,'$.live_head') IS NOT NULL OR json_extract(r.frontier,'$.back_done')=1)",
                    'reconcile': '1'}[mode]
                rooms = db.execute("""SELECT r.* FROM rooms r LEFT JOIN room_mode_checks c
                    ON c.room=r.id AND c.mode=? WHERE r.retry_at<=? AND """ + eligibility + """
                    AND NOT EXISTS(SELECT 1 FROM pages p WHERE p.room=r.id AND p.state='prepared'
                      AND NOT EXISTS(SELECT 1 FROM retired_policy_pages x WHERE x.page=p.id))
                    ORDER BY coalesce(c.position,0),r.id LIMIT 1""", (mode,time.time())).fetchall()
            if release:
                try:
                    return self._release_activity(release['page'], cancelled=cancelled, revalidate=True)
                except SourceBlocked as error:
                    with self.database() as db:
                        db.execute('UPDATE activity_releases SET error=?,retry_at=? WHERE page=?',
                                   (str(error)[:160], time.time() + 60, release['page']))
                    return {'state':'blocked', 'reason':str(error), 'job':release['page']}
            if pending:
                try:
                    return self._publish(pending['id'], cancelled=cancelled, after_publish=after_publish, revalidate=True)
                except SourceBlocked as error:
                    return self._blocked(pending['room'], error, pending['mode'])
            for room in rooms:
                state = json.loads(room['frontier'])
                try:
                    cursor = state.get({'backfill': 'back_cursor', 'live': 'live_cursor', 'reconcile': 'check_cursor'}[mode])
                    page = self.reader.page(room['id'], cursor)
                    job = self._prepare(room, mode, page)
                    return self._publish(job, cancelled=cancelled, after_publish=after_publish)
                except SourceBlocked as error:
                    return self._blocked(room['id'], error, mode)
            return {'state': 'idle'}
        finally:
            os.close(descriptor)

    def status(self):
        with self.database() as db:
            rooms = db.execute('SELECT frontier,error FROM rooms').fetchall()
            pending = db.execute("SELECT count(*) FROM pages p WHERE state='prepared' AND NOT EXISTS(SELECT 1 FROM retired_policy_pages x WHERE x.page=p.id)").fetchone()[0]
            triage = dict(db.execute('SELECT state,count(*) FROM message_heads GROUP BY state'))
            unique = db.execute("SELECT count(DISTINCT content_hash) FROM message_heads WHERE state='candidate'").fetchone()[0]
            pending_activity = db.execute("SELECT count(*) FROM activity_releases WHERE state='pending'").fetchone()[0]
            blocked_activity = db.execute("SELECT count(*) FROM activity_releases WHERE state='pending' AND error IS NOT NULL").fetchone()[0]
        return {'rooms': len(rooms), 'backfilled_rooms': sum(json.loads(r['frontier'])['back_done'] for r in rooms),
                'blocked_rooms': sum(r['error'] is not None for r in rooms), 'prepared_pages': pending,
                'pending_activity': pending_activity, 'blocked_activity': blocked_activity,
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
