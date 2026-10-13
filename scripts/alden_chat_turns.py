"""Durable, bounded typed-turn admission and completion on Alden's history DB.

The parent serializes native admission and holds the global AbortToken
commit_guard during completion/cancellation. No inference or file parsing is
performed here. This ledger records confirmed text and metadata, never files.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import unicodedata
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from alden_history import database


MAX_TEXT_CHARS = 4000
MAX_TEXT_BYTES = 16000
MAX_REPLY_CHARS = 16000
MAX_REPLY_BYTES = 65536
MAX_ATTACHMENTS = 4
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024
MAX_HISTORY_ITEMS = 8
MAX_HISTORY_CHARS = 600
MAX_HISTORY_ATTACHMENTS = 4

_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MIME = re.compile(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*\Z")
_REASON = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_PROOF_KEYS = frozenset({
    'fact_type', 'entity_id', 'source_id', 'target_id', 'relation',
    'source_note_id', 'target_note_id', 'target_note_hash', 'source_kind',
    'source_account', 'room_id', 'retracted', 'provenance_valid',
    'source_event_ids', 'confirmed_at', 'updated_at', 'valid_from', 'valid_to',
    'note_id', 'note_hash', 'space', 'derived_from', 'conflicts',
    'retrieval_role', 'fact_truncated',
})


class TurnConflict(ValueError):
    """A request ID or ownership ticket was used for a different input."""


class StaleTurn(RuntimeError):
    """A cancelled, superseded or externally advanced turn cannot commit."""


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f'{name}_invalid')
    return value


def _text(value: object, name: str, chars: int, byte_limit: int, *, allow_empty: bool = False) -> str:
    if (not isinstance(value, str) or (not value.strip() and not allow_empty)
            or len(value) > chars
            or any(char not in '\n\t' and unicodedata.category(char) in {'Cc','Cs'}
                   for char in value)):
        raise ValueError(f'{name}_invalid')
    if len(value.encode('utf-8')) > byte_limit:
        raise ValueError(f'{name}_too_large')
    return value


def _attachments(value: list[dict] | None) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ATTACHMENTS:
        raise ValueError('turn_attachments_invalid')
    items = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'filename', 'mime', 'sha256', 'size'}:
            raise ValueError('turn_attachment_fields_invalid')
        filename, mime, digest, size = (item[key] for key in ('filename', 'mime', 'sha256', 'size'))
        if (not isinstance(filename, str) or not filename.strip()
                or len(filename) > 128 or len(filename.encode('utf-8')) > 512
                or filename in ('.', '..')
                or any(char in '/\\\n\r\t' or unicodedata.category(char) in {'Cc','Cs'}
                       for char in filename)):
            raise ValueError('turn_attachment_filename_invalid')
        if not isinstance(mime, str) or len(mime) > 100 or _MIME.fullmatch(mime) is None:
            raise ValueError('turn_attachment_mime_invalid')
        if not isinstance(digest, str) or _HASH.fullmatch(digest) is None:
            raise ValueError('turn_attachment_hash_invalid')
        if type(size) is not int or not 0 < size <= MAX_ATTACHMENT_BYTES:
            raise ValueError('turn_attachment_size_invalid')
        items.append({'filename': filename, 'mime': mime, 'sha256': digest, 'size': size})
    return items


def _provenance(value: list[dict] | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 7 or not all(isinstance(row, dict) for row in value):
        raise ValueError('turn_provenance_invalid')
    rows = [{key: item for key, item in row.items() if key in _PROOF_KEYS} for row in value]
    try:
        encoded = json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise ValueError('turn_provenance_invalid') from exc
    if len(encoded.encode('utf-8')) > 64 * 1024:
        raise ValueError('turn_provenance_too_large')
    return encoded if rows else None


def _schema(db: sqlite3.Connection) -> None:
    # DDL executes inside BEGIN IMMEDIATE. No existing history table is altered.
    db.execute('''CREATE TABLE IF NOT EXISTS alden_chat_requests (
        request_id TEXT PRIMARY KEY,
        requested_conversation_id TEXT,
        conversation_id TEXT NOT NULL,
        turn_id INTEGER NOT NULL,
        context_version INTEGER NOT NULL,
        text_sha256 TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        attachments_json TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source = 'text'),
        state TEXT NOT NULL CHECK(state IN ('pending','completed','cancelled','superseded','retracted')),
        reason TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(conversation_id, turn_id)
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS alden_chat_by_conversation
                  ON alden_chat_requests(conversation_id, state, turn_id DESC)''')
    db.execute('''CREATE TABLE IF NOT EXISTS alden_chat_generations (
        request_id TEXT PRIMARY KEY REFERENCES alden_chat_requests(request_id),
        metadata_json TEXT NOT NULL CHECK(length(metadata_json)<=4096),
        recorded_at REAL NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS alden_chat_input_contexts (
        request_id TEXT PRIMARY KEY REFERENCES alden_chat_requests(request_id),
        context_sha256 TEXT NOT NULL,
        bound_at REAL NOT NULL
    )''')


def _generation(value: dict | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('generation_metadata_invalid')
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > 4096:
            raise ValueError('generation_metadata_invalid')
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError('generation_metadata_invalid') from error
    return encoded


@contextmanager
def _write(state_root: Path):
    with ExitStack() as stack:
        try:
            db = stack.enter_context(database(state_root, write=True))
        except sqlite3.OperationalError as exc:
            if str(exc) != 'duplicate column name: pid_start':
                raise
            # The existing history helper checks then adds this column on
            # first use. Another process may finish that migration first.
            # Retry that already-changed state once; no schema rewrite here.
            db = stack.enter_context(database(state_root, write=True))
        # The existing helper owns the DB path, permissions, WAL and old schema.
        db.execute('PRAGMA busy_timeout=1500')
        db.execute('BEGIN IMMEDIATE')
        _schema(db)
        yield db


def _history(db: sqlite3.Connection, conversation_id: str, before: int) -> list[dict]:
    rows = db.execute('''SELECT role, SUBSTR(content, 1, 600) AS content FROM voice_messages
        WHERE session_id=? AND turn_id<? AND role IN ('user','assistant')
          AND state='confirmed'
          AND NOT EXISTS (SELECT 1 FROM alden_chat_requests AS r
              WHERE r.conversation_id=voice_messages.session_id
                AND r.turn_id=voice_messages.turn_id AND r.state='retracted')
        ORDER BY turn_id DESC, CASE role WHEN 'assistant' THEN 0 ELSE 1 END
        LIMIT ?''', (conversation_id, before, MAX_HISTORY_ITEMS)).fetchall()
    return [{'role': row['role'], 'content': row['content'][:MAX_HISTORY_CHARS]}
            for row in reversed(rows)]


def _history_attachments(db: sqlite3.Connection, conversation_id: str, before: int, *, files=False) -> list[dict]:
    # Ignore all non-completed or retracted requests. Each stored value is at
    # most four metadata objects; search results and return size are bounded.
    rows = db.execute('''SELECT r.request_id, r.turn_id, r.attachments_json
        FROM alden_chat_requests AS r
        WHERE r.conversation_id=? AND r.turn_id<? AND r.source='text'
          AND r.state='completed' AND r.attachments_json!='[]'
          AND EXISTS (SELECT 1 FROM voice_messages AS u
              WHERE u.session_id=r.conversation_id AND u.turn_id=r.turn_id
                AND u.role='user' AND u.state='confirmed')
          AND EXISTS (SELECT 1 FROM voice_messages AS a
              WHERE a.session_id=r.conversation_id AND a.turn_id=r.turn_id
                AND a.role='assistant' AND a.state='confirmed')
        ORDER BY r.turn_id DESC LIMIT 64''', (conversation_id, before)).fetchall()
    found, seen = [], set()
    for row in rows:
        for item in json.loads(row['attachments_json']):
            if item['mime'].startswith('image/') == files:
                continue
            key = (item['sha256'], item['mime'])
            if key in seen:
                continue
            seen.add(key)
            found.append({**item, 'source_request_id': row['request_id'],
                          'source_turn_id': row['turn_id']})
            if len(found) == MAX_HISTORY_ATTACHMENTS:
                return found
    return found


def _verify_history_image_sources(db: sqlite3.Connection, ticket: dict) -> None:
    for image in ticket.get('image_context', {}).get('selected', []):
        if image['origin'] != 'previous':
            continue
        row = db.execute('''SELECT r.* FROM alden_chat_requests AS r
            WHERE request_id=? AND conversation_id=? AND turn_id=? AND turn_id<? AND state='completed'
              AND (SELECT count(*) FROM voice_messages AS m WHERE m.session_id=r.conversation_id
                   AND m.turn_id=r.turn_id AND m.role IN ('user','assistant') AND m.state='confirmed')=2''',
            (image['source_request_id'],ticket['conversation_id'],image['source_turn_id'],ticket['turn_id'])).fetchone()
        if row is None or not any(all(item.get(k)==image[k] for k in ('sha256','mime','size'))
                                  for item in json.loads(row['attachments_json'])):
            raise ValueError('assistant_image_context_changed')


def verify_history_image_sources(state_root: Path, ticket: dict) -> None:
    with database(state_root) as db:
        if db is None:
            raise ValueError('assistant_image_context_changed')
        _verify_history_image_sources(db, ticket)


def _verify_history_file_sources(db: sqlite3.Connection, ticket: dict) -> None:
    for item in ticket.get('document_context', {}).get('selected', []):
        if item['origin'] != 'previous':
            continue
        row = db.execute('''SELECT r.attachments_json FROM alden_chat_requests AS r
            WHERE request_id=? AND conversation_id=? AND turn_id=? AND turn_id<? AND state='completed'
              AND (SELECT count(*) FROM voice_messages AS m WHERE m.session_id=r.conversation_id
                   AND m.turn_id=r.turn_id AND m.role IN ('user','assistant') AND m.state='confirmed')=2''',
            (item['source_request_id'], ticket['conversation_id'], item['source_turn_id'], ticket['turn_id'])).fetchone()
        if row is None or not any(all(meta.get(key) == item[key] for key in ('filename', 'sha256', 'mime', 'size'))
                                 for meta in json.loads(row['attachments_json'])):
            raise ValueError('assistant_file_context_changed')


def verify_history_file_sources(state_root: Path, ticket: dict) -> None:
    if not ticket.get('document_context', {}).get('selected'):
        return
    with database(state_root) as db:
        if db is None:
            raise ValueError('assistant_file_context_changed')
        _verify_history_file_sources(db, ticket)


def _receipt(db: sqlite3.Connection, row: sqlite3.Row, *, include_owner: bool) -> dict:
    # Stable public/native states. The persisted SQL state remains suitable for
    # pending-turn CAS and old databases: pending -> running, cancelled with a
    # failure reason -> failed, retracted -> superseded (no answer replay).
    state = row['state']
    if state == 'pending':
        state = 'running'
    elif state == 'cancelled' and row['reason'] == 'failed':
        state = 'failed'
    elif state == 'retracted':
        state = 'superseded'
    result = {
        'conversation_id': row['conversation_id'],
        'turn_id': row['turn_id'],
        'context_version': row['context_version'],
        'request_id': row['request_id'],
        'state': state,
        'source': 'text',
        'history': _history(db, row['conversation_id'], row['turn_id']),
        'history_attachments': _history_attachments(db, row['conversation_id'], row['turn_id']),
        'history_files': _history_attachments(db, row['conversation_id'], row['turn_id'], files=True),
    }
    if include_owner:
        result['owner_id'] = row['owner_id']
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alden_chat_generations'").fetchone():
        generation = db.execute('SELECT metadata_json FROM alden_chat_generations WHERE request_id=?', (row['request_id'],)).fetchone()
        if generation is not None:
            result['generation'] = json.loads(generation['metadata_json'])
    if row['reason'] is not None:
        result['reason'] = row['reason']
    if row['state'] == 'completed':
        answer = db.execute('''SELECT content, context_version FROM voice_messages
            WHERE session_id=? AND turn_id=? AND role='assistant' AND state='confirmed' ''',
            (row['conversation_id'], row['turn_id'])).fetchone()
        if answer is None:
            raise RuntimeError('turn_answer_missing')
        result['reply'] = answer['content']
        result['answer_context_version'] = answer['context_version']
    return result


def _row(db: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
    return db.execute('SELECT * FROM alden_chat_requests WHERE request_id=?',
                      (request_id,)).fetchone()


def reserve_turn(state_root: Path, request_id: str, conversation_id: str | None,
                 text: str, *, attachments: list[dict] | None = None) -> dict:
    """Atomically accept a real user text event and return its durable ticket.

    Repeating the same request and *originally requested* conversation returns
    its original turn, even if it has since completed or been superseded.
    """
    _identifier(request_id, 'request_id')
    if conversation_id is not None:
        _identifier(conversation_id, 'conversation_id')
    metadata = _attachments(attachments)
    _text(text, 'turn_text', MAX_TEXT_CHARS, MAX_TEXT_BYTES, allow_empty=bool(metadata))
    payload = json.dumps({'text': text, 'attachments': metadata},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()
    text_digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    attachment_json = json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))
    with _write(state_root) as db:
        previous = _row(db, request_id)
        if previous is not None:
            if (previous['requested_conversation_id'] != conversation_id
                    or previous['payload_sha256'] != digest or previous['source'] != 'text'):
                raise TurnConflict('request_id_payload_or_target_conflict')
            return _receipt(db, previous, include_owner=True)

        now = time.time()
        selected = conversation_id
        if selected is None:
            selected = uuid.uuid4().hex
            db.execute('INSERT INTO voice_sessions(id, started_at, title, source) VALUES(?,?,?,?)',
                       (selected, now, text[:80] or '첨부 대화', 'text'))
        elif db.execute('SELECT 1 FROM voice_sessions WHERE id=?', (selected,)).fetchone() is None:
            raise ValueError('conversation_not_found')

        counters = db.execute('''SELECT COALESCE(MAX(turn_id),0),
                                      COALESCE(MAX(context_version),0)
                               FROM voice_messages WHERE session_id=?''', (selected,)).fetchone()
        turn_id, prior_version = map(int, counters)
        if turn_id >= 9_007_199_254_740_991 or prior_version >= 9_007_199_254_740_990:
            raise OverflowError('conversation_counter_overflow')
        turn_id += 1
        context_version = prior_version + 1
        db.execute('''UPDATE alden_chat_requests
                      SET state='superseded', reason='superseded', updated_at=?
                      WHERE conversation_id=? AND state='pending' ''', (now, selected))
        db.execute('''INSERT INTO voice_messages
                      (session_id,turn_id,role,content,created_at,context_version,state)
                      VALUES(?,?,?,?,?,?,?)''',
                   (selected, turn_id, 'user', text, now, context_version, 'confirmed'))
        db.execute('''INSERT INTO alden_chat_requests
            (request_id,requested_conversation_id,conversation_id,turn_id,context_version,
             text_sha256,payload_sha256,attachments_json,owner_id,source,state,reason,
             created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,'text','pending',NULL,?,?)''',
            (request_id, conversation_id, selected, turn_id, context_version,
             text_digest, digest, attachment_json, secrets.token_hex(16), now, now))
        return _receipt(db, _row(db, request_id), include_owner=True)


def _ticket(db: sqlite3.Connection, ticket: dict) -> sqlite3.Row:
    if not isinstance(ticket, dict):
        raise ValueError('turn_ticket_invalid')
    request_id = _identifier(ticket.get('request_id'), 'request_id')
    _identifier(ticket.get('conversation_id'), 'conversation_id')
    _identifier(ticket.get('owner_id'), 'owner_id')
    if (type(ticket.get('turn_id')) is not int or ticket['turn_id'] < 1
            or type(ticket.get('context_version')) is not int
            or ticket['context_version'] < 1 or ticket.get('source') != 'text'):
        raise ValueError('turn_ticket_invalid')
    row = _row(db, request_id)
    if row is None or any((ticket[field] != row[field] for field in (
            'request_id', 'conversation_id', 'turn_id', 'context_version', 'owner_id', 'source'))):
        raise TurnConflict('turn_ticket_ownership_conflict')
    return row


def require_current_turn(state_root: Path, ticket: dict) -> None:
    with database(state_root) as db:
        if db is None:
            raise StaleTurn('turn_not_current')
        row = _ticket(db, ticket)
        latest = db.execute('SELECT COALESCE(MAX(turn_id),0),COALESCE(MAX(context_version),0) FROM voice_messages WHERE session_id=?',
                            (row['conversation_id'],)).fetchone()
        if row['state'] != 'pending' or tuple(latest) != (row['turn_id'], row['context_version']):
            raise StaleTurn('turn_not_current')


def bind_prepared_context(state_root: Path, ticket: dict, context: dict) -> None:
    # Only digests of prepared metadata and history: no document body cache.
    payload = json.dumps({'references': context, 'history': ticket['history']}, ensure_ascii=False,
                         sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()
    with _write(state_root) as db:
        row = _ticket(db, ticket)
        if row['state'] != 'pending':
            raise StaleTurn('turn_not_current')
        latest = db.execute('SELECT COALESCE(MAX(turn_id),0),COALESCE(MAX(context_version),0) FROM voice_messages WHERE session_id=?',
                            (row['conversation_id'],)).fetchone()
        if tuple(latest) != (row['turn_id'], row['context_version']):
            raise StaleTurn('turn_not_current')
        previous = db.execute('SELECT context_sha256 FROM alden_chat_input_contexts WHERE request_id=?', (row['request_id'],)).fetchone()
        if previous and previous[0] != digest:
            raise ValueError('assistant_file_context_changed')
        db.execute('INSERT OR IGNORE INTO alden_chat_input_contexts VALUES(?,?,?)', (row['request_id'], digest, time.time()))
    ticket['prepared_context_sha256'] = digest


def complete_turn(state_root: Path, ticket: dict, reply: str, *,
                  provenance: list[dict] | None = None, generation: dict | None = None) -> dict:
    """Persist one assistant answer with compare-and-swap turn/source checks.

    Caller must hold its existing global abort commit guard. This function
    never generates text, reads attachment bytes, or acquires that guard.
    """
    _text(reply, 'turn_reply', MAX_REPLY_CHARS, MAX_REPLY_BYTES)
    proof = _provenance(provenance)
    observation = _generation(generation)
    with _write(state_root) as db:
        row = _ticket(db, ticket)
        if row['state'] in ('completed', 'retracted'):
            return _receipt(db, row, include_owner=True)
        if row['state'] != 'pending':
            raise StaleTurn(f"turn_{row['state']}")
        expected = ticket.get('prepared_context_sha256')
        if expected is not None:
            bound = db.execute('SELECT context_sha256 FROM alden_chat_input_contexts WHERE request_id=?', (row['request_id'],)).fetchone()
            if bound is None or bound[0] != expected:
                raise StaleTurn('turn_context_changed')
        _verify_history_image_sources(db, ticket)
        _verify_history_file_sources(db, ticket)
        user = db.execute('''SELECT content FROM voice_messages
            WHERE session_id=? AND turn_id=? AND role='user' AND state='confirmed' ''',
            (row['conversation_id'], row['turn_id'])).fetchone()
        latest = db.execute('''SELECT COALESCE(MAX(turn_id),0),
                                    COALESCE(MAX(context_version),0)
                             FROM voice_messages WHERE session_id=?''',
                            (row['conversation_id'],)).fetchone()
        if (user is None or hashlib.sha256(user['content'].encode('utf-8')).hexdigest()
                != row['text_sha256'] or tuple(map(int, latest)) !=
                (row['turn_id'], row['context_version'])):
            raise StaleTurn('turn_not_current')
        changed = db.execute('''UPDATE alden_chat_requests SET state='completed',updated_at=?
            WHERE request_id=? AND state='pending' AND source='text'
              AND conversation_id=? AND turn_id=? AND context_version=? AND owner_id=?''',
            (time.time(), row['request_id'], row['conversation_id'], row['turn_id'],
             row['context_version'], row['owner_id']))
        if changed.rowcount != 1:
            raise StaleTurn('turn_not_current')
        db.execute('''INSERT INTO voice_messages
                      (session_id,turn_id,role,content,created_at,context_version,state)
                      VALUES(?,?,?,?,?,?,?)''',
                   (row['conversation_id'], row['turn_id'], 'assistant', reply,
                    time.time(), row['context_version'] + 1, 'confirmed'))
        if proof is not None:
            db.execute('INSERT INTO voice_sources(session_id,turn_id,provenance) VALUES(?,?,?)',
                       (row['conversation_id'], row['turn_id'], proof))
        if observation is not None:
            db.execute('INSERT INTO alden_chat_generations(request_id,metadata_json,recorded_at) VALUES(?,?,?)',
                       (row['request_id'], observation, time.time()))
        return _receipt(db, _row(db, row['request_id']), include_owner=True)


def cancel_turn(state_root: Path, ticket: dict, *, reason: str = 'cancelled', generation: dict | None = None) -> dict:
    """Cancel pending work; 'retracted' also hides a completed turn's images."""
    if not isinstance(reason, str) or _REASON.fullmatch(reason) is None:
        raise ValueError('turn_cancel_reason_invalid')
    observation = _generation(generation)
    with _write(state_root) as db:
        row = _ticket(db, ticket)
        if row['state'] == 'pending':
            state = 'superseded' if reason == 'superseded' else 'cancelled'
            db.execute('''UPDATE alden_chat_requests SET state=?,reason=?,updated_at=?
                          WHERE request_id=? AND state='pending' AND owner_id=?''',
                       (state, reason, time.time(), row['request_id'], row['owner_id']))
            if observation is not None:
                db.execute('INSERT INTO alden_chat_generations(request_id,metadata_json,recorded_at) VALUES(?,?,?)',
                           (row['request_id'],observation,time.time()))
        elif row['state'] == 'completed' and reason == 'retracted':
            db.execute('''UPDATE alden_chat_requests SET state='retracted',reason=?,updated_at=?
                          WHERE request_id=? AND state='completed' AND owner_id=?''',
                       (reason, time.time(), row['request_id'], row['owner_id']))
        return _receipt(db, _row(db, row['request_id']), include_owner=True)


def get_request(state_root: Path, request_id: str) -> dict | None:
    """Read status and the persisted reply; do not disclose the owner ticket."""
    _identifier(request_id, 'request_id')
    with database(state_root) as db:
        if db is None or db.execute('''SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='alden_chat_requests' ''').fetchone() is None:
            return None
        row = _row(db, request_id)
        return _receipt(db, row, include_owner=False) if row is not None else None


def inspect_replay(state_root: Path, request_id: str, conversation_id: str | None,
                   text: str, attachments: list[dict]) -> dict | None:
    """A non-owner can inspect a reservation; it never creates one."""
    _identifier(request_id, 'request_id')
    metadata = _attachments(attachments)
    payload = json.dumps({'text': text, 'attachments': metadata}, ensure_ascii=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    with database(state_root) as db:
        if db is None or db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alden_chat_requests'").fetchone() is None:
            return None
        row = _row(db, request_id)
        if row is None:
            return None
        if (row['requested_conversation_id'] != conversation_id
                or row['payload_sha256'] != hashlib.sha256(payload).hexdigest() or row['source'] != 'text'):
            raise TurnConflict('request_id_payload_or_target_conflict')
        return _receipt(db, row, include_owner=False)
