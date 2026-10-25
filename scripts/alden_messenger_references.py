"""Current explicit-reference claims; source evidence is separate from body identity."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import re
import os
import sqlite3
import time
import uuid

from alden_collection import CollectionStore, encoded, identity, require_relation_binding

KIND = 'messenger-explicit-reply-v1'
TYPE = 'replies-to'
TABLE = 'messenger_references'


def _owned_edge(db, edge, target, origin, account, room, message):
    row = db.execute('SELECT * FROM relations WHERE id=? AND target_id=?', (edge, target)).fetchone()
    if row is None: raise RuntimeError('messenger_reference_integrity')
    require_relation_binding(db, row)
    try:
        evidence = json.loads(row['evidence'])
        proof = evidence['observation']['reference']
        if (evidence['kind'] != KIND or row['type'] != TYPE
                or (proof['account_id'], proof['room_id'], proof['source_message_id']) != (account, room, message)
                or row['id'] != identity('relation', encoded([target, row['source'], TYPE, row['target'], origin]).decode())):
            raise ValueError('origin')
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError('messenger_reference_integrity') from error
    return row


def observe(source, room, message, *, cancelled=lambda: False):
    from alden_messenger_runtime import SourceBlocked
    def check():
        if cancelled(): raise RuntimeError('collection_cancelled')
    check()
    callback = getattr(source.reader, 'reply_reference', None)
    if source.platform != 'kakaotalk' or not callable(callback):
        raise SourceBlocked('messenger_reference_capability_required')
    proof = callback(room, message)
    check()
    if not isinstance(proof, dict):
        raise SourceBlocked('messenger_reference_invalid')
    if proof.get('state') in {'withheld', 'not_admitted'}:
        if not isinstance(proof.get('reason'), str) or not re.fullmatch('[a-z0-9_]{1,128}', proof['reason']):
            raise SourceBlocked('messenger_reference_invalid')
        return {'state': proof['state'], 'reason': proof['reason']}
    fields = ('platform', 'account_id', 'room_id', 'source_message_id', 'target_message_id',
              'source_content_hash', 'attachment_sha256')
    if (proof.get('state') != 'verified' or proof.get('source') != 'canonical-explicit-visible-reply-v1'
            or (proof.get('platform'), proof.get('account_id'), proof.get('room_id'), proof.get('source_message_id'))
                != (source.platform, source.account, room, message)
            or not isinstance(proof.get('target_message_id'), str)
            or not re.fullmatch('[1-9][0-9]{0,18}', proof['target_message_id'])
            or int(proof['target_message_id']) >= 2**63
            or any(not isinstance(proof.get(k), str) or not re.fullmatch('[a-f0-9]{64}', proof[k])
                   for k in ('source_content_hash', 'attachment_sha256', 'reference_revision'))):
        raise SourceBlocked('messenger_reference_invalid')
    if hashlib.sha256(encoded({k: proof[k] for k in fields})).hexdigest() != proof['reference_revision']:
        raise SourceBlocked('messenger_reference_invalid')
    proof = {k: proof[k] for k in (*fields, 'state', 'reference_revision', 'source')}
    keys = [{'room_id': room, 'message_id': value} for value in dict.fromkeys((message, proof['target_message_id']))]
    rows = source.reader.content_revisions(keys)
    check()
    if not isinstance(rows, list) or len(rows) != len(keys):
        raise SourceBlocked('messenger_reference_content_invalid')
    found = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'room_id', 'message_id', 'state', 'content_hash'}
                or {'room_id': row.get('room_id'), 'message_id': row.get('message_id')} not in keys
                or row['message_id'] in found or row['state'] not in {'available', 'withheld', 'missing', 'unavailable'}
                or row['state'] == 'available' and (not isinstance(row['content_hash'], str)
                    or not re.fullmatch('[a-f0-9]{64}', row['content_hash']))
                or row['state'] != 'available' and row['content_hash'] is not None):
            raise SourceBlocked('messenger_reference_content_invalid')
        found[row['message_id']] = row
    if found[message]['state'] != 'available' or found[message]['content_hash'] != proof['source_content_hash']:
        raise SourceBlocked('messenger_reference_source_changed')
    target = found[proof['target_message_id']]
    if target['state'] == 'unavailable':
        raise SourceBlocked('messenger_reference_target_unavailable')
    if target['state'] != 'available':
        return {'state': 'withheld', 'reason': 'reference_target_not_visible'}
    return {'state': 'verified', 'reference': proof, 'target_content_hash': target['content_hash']}


def scopes(source, *, cancelled=lambda: False):
    """Fresh metadata for known claims. No claim table is created on a read."""
    with source.store.database() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (TABLE,)).fetchone():
            return []
        from alden_messenger_production import MAX_REVISION_ORIGINS
        rows = db.execute('SELECT origin,room,message,generation,state FROM messenger_references WHERE target_id=? ORDER BY origin LIMIT ?',
                          (source.target, MAX_REVISION_ORIGINS + 1)).fetchall()
        if len(rows) > MAX_REVISION_ORIGINS:
            raise ValueError('messenger_reference_revision_budget')
    result = []
    for row in rows:
        if cancelled(): raise RuntimeError('collection_cancelled')
        try:
            current = observe(source, row['room'], row['message'], cancelled=cancelled)
        except (ValueError, RuntimeError, OSError):
            current = {'state': 'unavailable'}
        if cancelled(): raise RuntimeError('collection_cancelled')
        result.append([*tuple(row), current])
    return result


def allowed(authority, target, source_id, destination, relation_type, evidence):
    if not isinstance(evidence, dict) or set(evidence) != {'kind', 'observation', 'source_version', 'target_version'}:
        return False
    if evidence['kind'] != KIND or relation_type != TYPE:
        return False
    observed = evidence['observation']
    if not isinstance(observed, dict) or observed.get('state') != 'verified' or not isinstance(observed.get('reference'), dict):
        return False
    proof = observed['reference']
    source = authority._source(target)
    current = observe(source, proof.get('room_id'), proof.get('source_message_id'), cancelled=authority._cancelled)
    if current != observed:
        return False
    expected = [identity('graph', 'messenger-content:' + value) for value in
                (proof['source_content_hash'], current['target_content_hash'])]
    if expected[0] == expected[1] or expected != [source_id, destination]:
        return False
    with source.store.database() as db:
        for document, version in zip(expected, (evidence['source_version'], evidence['target_version'])):
            row = db.execute('''SELECT v.metadata FROM memberships m JOIN versions v ON v.id=m.current_version
                WHERE m.target_id=? AND m.document_id=? AND m.current_version=? AND m.availability='available' ''',
                (target, document, version)).fetchone()
            if row is None or not authority.access(target, document, version, json.loads(row['metadata'])):
                return False
    return True


def reconcile(root, target, room, message, *, reader=None, cancelled=lambda: False, publication_guard=None):
    """One recoverable transition: claim, edge history and refresh event commit together."""
    from alden_abort import AbortToken, ABORT_STATE_NAME
    from alden_collection_scheduler import CollectionScheduler
    from alden_messenger_production import ProductionAuthority
    from alden_messenger_runtime import SourceBlocked
    root = Path(root)
    authority = ProductionAuthority(root, reader_factory=(lambda: reader) if reader is not None else None, cancelled=cancelled)
    source = authority._source(target)
    source._allowed()
    scheduler, abort = CollectionScheduler(root), AbortToken(root / ABORT_STATE_NAME)

    def gate():
        if cancelled() or abort.is_cancelled() or scheduler._paused(target) or scheduler._voice_busy():
            raise SourceBlocked('messenger_reference_paused')
        source._allowed()

    @contextmanager
    def commit_guard():
        # The scheduled caller supplies its complete control/abort commit gate.
        # Taking these file locks again through another descriptor deadlocks.
        if publication_guard is not None:
            with publication_guard():
                gate()
                yield
        else:
            with scheduler.control_lock(), abort.commit_guard():
                gate()
                yield

    gate()
    observed = observe(source, room, message, cancelled=authority._cancelled)
    origin = identity('reference-origin', encoded([source.account, room, message]).decode())
    if observed['state'] != 'verified':
        with source.store.database() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (TABLE,)).fetchone()
            if not exists or not db.execute('SELECT 1 FROM messenger_references WHERE target_id=? AND origin=?', (target, origin)).fetchone():
                return {'state': observed['state'], 'changed': False, 'tracked': False}
    store = CollectionStore(root, source_access=authority.access, source_revision=authority.revision,
        source_stream=authority.stream_revision, source_relation_access=authority.relation_access)
    with store.database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS messenger_references(
            target_id TEXT NOT NULL,origin TEXT NOT NULL,room TEXT NOT NULL,message TEXT NOT NULL,
            relation_id TEXT,generation INTEGER NOT NULL,state TEXT NOT NULL,observation TEXT NOT NULL,
            updated REAL NOT NULL,PRIMARY KEY(target_id,origin))''')
    if store.schema == 3:
        path = store.root / ('collection.reference-schema-3-' + uuid.uuid4().hex + '.sqlite3')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(path) as backup:
            original.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('messenger_reference_backup_invalid')
    with store.database(publication_guard=commit_guard, cancelled=cancelled) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM messenger_references WHERE target_id=? AND origin=?', (target, origin)).fetchone()
        state, evidence, edge, versions = observed['state'], None, None, []
        if observed['state'] == 'verified':
            proof = observed['reference']
            documents = [identity('graph', 'messenger-content:' + value) for value in
                         (proof['source_content_hash'], observed['target_content_hash'])]
            for document in documents:
                row = db.execute('''SELECT m.current_version FROM memberships m JOIN documents d ON d.id=m.document_id
                    WHERE m.target_id=? AND m.document_id=? AND m.availability='available'
                      AND d.availability='available' AND alden_source_allowed(?,?,m.current_version)=1''',
                    (target, document, target, document)).fetchone()
                if row: versions.append(row[0])
            state = 'self_reference' if documents[0] == documents[1] else 'ready' if len(versions) == 2 else 'pending_endpoints'
            if state == 'ready':
                evidence = encoded({'kind': KIND, 'observation': observed,
                                    'source_version': versions[0], 'target_version': versions[1]}).decode()
                edge = identity('relation', encoded([target, documents[0], TYPE, documents[1], origin]).decode())
        if old is None and observed['state'] != 'verified':
            return {'state': state, 'changed': False, 'tracked': False}
        if old is not None and old['state'] == state and old['relation_id'] == edge and json.loads(old['observation']) == observed:
            unchanged = True
            if edge:
                row = _owned_edge(db, edge, target, origin, source.account, room, message)
                unchanged = bool(row['active']) and row['evidence'] == evidence
            if unchanged:
                if observe(source, room, message, cancelled=authority._cancelled) != observed:
                    raise SourceBlocked('messenger_reference_changed_during_commit')
                return {'state': state, 'changed': False, 'tracked': True}
        generation = old['generation'] + 1 if old else 1
        if old and old['relation_id']:
            _owned_edge(db, old['relation_id'], target, origin, source.account, room, message)
            db.execute('UPDATE relations SET active=0 WHERE id=?', (old['relation_id'],))
        now = time.time()
        if edge:
            version = hashlib.sha256(evidence.encode()).hexdigest()
            previous = db.execute('SELECT * FROM relations WHERE id=?', (edge,)).fetchone()
            if previous is not None:
                _owned_edge(db, edge, target, origin, source.account, room, message)
            require_relation_binding(db, {'id': edge, 'version': version, 'target_id': target,
                'source': documents[0], 'target': documents[1], 'type': TYPE, 'evidence': evidence}, allow_new=True)
            db.execute('''INSERT INTO relations VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                evidence=excluded.evidence,version=excluded.version,active=1''', (edge, target, documents[0], documents[1], TYPE, evidence, version))
            db.execute('INSERT OR IGNORE INTO relation_versions VALUES(?,?,?,?,?,?,?,?)',
                       (edge, version, target, documents[0], documents[1], TYPE, evidence, now))
        db.execute('''INSERT INTO messenger_references VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(target_id,origin) DO UPDATE SET
            relation_id=excluded.relation_id,generation=excluded.generation,state=excluded.state,
            observation=excluded.observation,updated=excluded.updated''',
            (target, origin, room, message, edge, generation, state, encoded(observed).decode(), now))
        db.execute("INSERT INTO meta VALUES('reference_protocol',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KIND,))
        db.execute("UPDATE meta SET value='4' WHERE key='schema'")
        if edge and not db.execute('SELECT alden_source_relation_allowed(?,?,?,?,?)',
                (target, documents[0], documents[1], TYPE, evidence)).fetchone()[0]:
            raise SourceBlocked('messenger_reference_not_current')
        if edge or old is not None and old['relation_id']:
            run = identity('reference-run', encoded([target, origin, generation]).decode())
            before = {key: old[key] for key in ('generation', 'state', 'relation_id', 'observation')} if old else None
            after = {'generation': generation, 'state': state, 'relation_id': edge, 'observation': observed}
            db.execute('INSERT INTO runs(id,target_id,origin,state,started_at,ended_at,cursor_before,cursor_after) VALUES(?,?,?,?,?,?,?,?)',
                       (run, target, 'messenger-reference:kakaotalk', 'complete', now, now,
                        encoded(before).decode(), encoded(after).decode()))
            store._event(db, run, target, 'stored', change='relations_changed',
                         added=int(edge is not None and (old is None or edge != old['relation_id'])),
                         removed=int(old is not None and old['relation_id'] is not None and edge != old['relation_id']),
                         revised=int(edge is not None and old is not None and edge == old['relation_id']),
                         reference_origin=origin, reference_generation=generation, reference_state=state,
                         relation_id=edge, previous_relation_id=old['relation_id'] if old else None,
                         reason='현재 원문의 명시적 답글 관계가 갱신되었습니다.')
        if observe(source, room, message, cancelled=authority._cancelled) != observed:
            raise SourceBlocked('messenger_reference_changed_during_commit')
        return {'state': state, 'changed': True, 'tracked': True, 'relation_id': edge, 'generation': generation}
