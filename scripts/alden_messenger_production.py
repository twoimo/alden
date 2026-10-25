"""Explicit local enrollment and current-source authority for the shared store.

Enrollment contains no reader credentials. Only the canonical current-account
reader can authorize a source. A malformed/missing binding withholds messenger
content; ordinary collection targets keep their existing access contract.
"""
from contextlib import nullcontext
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
import uuid

from alden_collection import CollectionStore, encoded, identity, normalized_body
from alden_abort import AbortToken, ABORT_STATE_NAME

ENROLLMENT = 'knowledge/collection/production-messengers.json'
ADAPTER = 'messenger-db'
MAX_REVISION_ORIGINS = 100000
MAX_REVISION_TEXT_BYTES = 32 * 1024 * 1024


def _private_root(root):
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('messenger_production_root_unsafe')
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('messenger_production_root_unsafe')
    return root


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('messenger_enrollment_duplicate_key')
        result[key] = value
    return result


def read_enrollment(root):
    root = _private_root(root)
    if AbortToken(root / ABORT_STATE_NAME).is_cancelled():
        raise RuntimeError('collection_cancelled')
    path = root / ENROLLMENT
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('messenger_enrollment_unsafe')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('messenger_enrollment_unsafe')
        raw = handle.read(16385)
    value = json.loads(raw, object_pairs_hook=_pairs)
    if (not isinstance(value, dict) or set(value) != {'schema', 'revision', 'sources'}
            or type(value['schema']) is not int or value['schema'] != 1
            or type(value['revision']) is not int or not 1 <= value['revision'] <= 9007199254740991
            or not isinstance(value['sources'], list) or len(value['sources']) > 16):
        raise ValueError('messenger_enrollment_invalid')
    seen = set()
    for source in value['sources']:
        if not isinstance(source, dict) or set(source) != {'platform', 'account', 'projects', 'state'}:
            raise ValueError('messenger_enrollment_invalid')
        account, projects = source['account'], source['projects']
        if (source['platform'] != 'kakaotalk' or not isinstance(account, str)
                or not account.isascii() or not account.isdecimal() or not 0 < int(account) < 2**63
                or not isinstance(projects, list) or not 1 <= len(projects) <= 16
                or any(not isinstance(p, str) or not p or len(p) > 128 for p in projects)
                or projects != sorted(set(projects)) or source['state'] not in ('prepared', 'active')):
            raise ValueError('messenger_enrollment_invalid')
        key = source['platform'], account
        if key in seen:
            raise ValueError('messenger_enrollment_duplicate_source')
        seen.add(key)
    return value


def binding(root, platform, account, *, active=True):
    state = read_enrollment(root)
    matches = [s for s in state['sources'] if (s['platform'], s['account']) == (platform, account)]
    if len(matches) != 1 or active and matches[0]['state'] != 'active':
        raise ValueError('messenger_production_binding_unavailable')
    source = matches[0]
    path = Path(root) / 'knowledge/collection/collection.sqlite3'
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('messenger_production_store_unsafe')
    target = identity('target-' + platform, 'local-db:' + account)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
        row = db.execute('SELECT platform,original_id,kind,config FROM targets WHERE id=?', (target,)).fetchone()
        projects = [r[0] for r in db.execute('SELECT project FROM target_projects WHERE target_id=? ORDER BY project', (target,))]
    if (row is None or row[:3] != (platform, account, 'local-db')
            or json.loads(row[3]) != {'adapter': ADAPTER, 'account': account}
            or projects != source['projects']):
        raise ValueError('messenger_production_binding_changed')
    return state, source


def frontier_folder(root, platform, account):
    return Path(root) / 'knowledge/collection/frontiers' / platform / hashlib.sha256(account.encode()).hexdigest()


def _save_enrollment(root, state):
    path = Path(root) / ENROLLMENT
    raw = encoded(state)
    if path.exists():
        previous = path.read_bytes()
        backup = path.with_name('production-messengers.before-' + uuid.uuid4().hex + '.json')
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(previous)
    fd, name = tempfile.mkstemp(prefix='.messenger-enrollment-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    if read_enrollment(root) != state:
        raise ValueError('messenger_enrollment_readback_failed')


def canonical_reader(cancelled=lambda: False):
    from alden_messenger_factory import load_kakao_sdk, _CanonicalRead
    from alden_messenger_runtime import KakaoReader, SourceBlocked
    def check():
        if cancelled():
            raise SourceBlocked('messenger_request_cancelled')
    module = load_kakao_sdk()
    if getattr(module.snapshot, 'supports_content_revisions', False) is not True:
        raise SourceBlocked('kakao_content_revision_capability_required')
    return KakaoReader(_CanonicalRead(module, check))


def enroll(root, projects, *, reader=None):
    """Explicit all-room enrollment. No body reads, discovery sends or watcher."""
    from alden_messenger_runtime import Frontier
    root = _private_root(root)
    if AbortToken(root / ABORT_STATE_NAME).is_cancelled():
        raise RuntimeError('collection_cancelled')
    if (not isinstance(projects, list) or not 1 <= len(projects) <= 16
            or any(not isinstance(p, str) or not p or len(p) > 128 for p in projects)):
        raise ValueError('messenger_enrollment_projects_invalid')
    projects = sorted(set(projects))
    reader = reader if reader is not None else canonical_reader()
    catalog = reader.catalog()
    account = reader.identity()
    if catalog.get('account_id') != account or catalog.get('complete') is not True:
        raise ValueError('messenger_catalog_incomplete_or_wrong_account')
    store = CollectionStore(root)
    path = root / ENROLLMENT
    lock = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('messenger_enrollment_lock_unsafe')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = read_enrollment(root) if path.exists() or path.is_symlink() else {'schema': 1, 'revision': 1, 'sources': []}
        source = next((s for s in state['sources'] if (s['platform'], s['account']) == ('kakaotalk', account)), None)
        if source is not None and source['projects'] != projects:
            raise ValueError('messenger_enrollment_scope_conflict')
        target = identity('target-kakaotalk', 'local-db:' + account)
        with store.database() as db:
            existing = db.execute('SELECT config FROM targets WHERE id=?', (target,)).fetchone()
        if existing is None:
            store.register(platform='kakaotalk', original_id=account, kind='local-db',
                label='카카오톡 · 로컬 대화', projects=projects, enabled=False, interval_seconds=60,
                config={'adapter': ADAPTER, 'account': account})
        if source is None:
            source = {'platform': 'kakaotalk', 'account': account, 'projects': projects, 'state': 'prepared'}
            state['sources'].append(source)
            state['revision'] += 1
            _save_enrollment(root, state)
        binding(root, 'kakaotalk', account, active=False)
        frontier = Frontier(root, 'kakaotalk', account, reader, production=True)
        rooms = frontier.seed(catalog)
        if source['state'] == 'prepared':
            if reader.identity() != account:
                raise ValueError('messenger_account_changed')
            with store.target_lock(target), store.database() as db:
                db.execute('UPDATE targets SET enabled=1 WHERE id=?', (target,))
            source['state'] = 'active'
            state['revision'] += 1
            _save_enrollment(root, state)
        binding(root, 'kakaotalk', account)
        return {'target_id': target, 'rooms': rooms, 'projects': projects,
                'enabled': bool(store.target(target)['enabled']), 'state': 'enrolled', 'message_rows_read': 0}
    finally:
        os.close(lock)


class ProductionAuthority:
    def __init__(self, root, *, reader_factory=None, cancelled=lambda: False):
        self.root = Path(root)
        self.cancelled = cancelled
        self.abort = AbortToken(self.root / ABORT_STATE_NAME)
        self.reader_factory = reader_factory or (lambda: canonical_reader(self._cancelled))
        self.sources = {}
        self.current = {}

    def _cancelled(self):
        return self.cancelled() or self.abort.is_cancelled()

    def collection_source_root(self):
        return self.root

    def _source(self, target):
        from alden_messenger_runtime import Frontier, SourceBlocked
        if self._cancelled():
            raise SourceBlocked('messenger_request_cancelled')
        state = read_enrollment(self.root)
        entry = next((s for s in state['sources'] if identity('target-' + s['platform'], 'local-db:' + s['account']) == target), None)
        if entry is None:
            raise SourceBlocked('messenger_production_binding_unavailable')
        binding(self.root, entry['platform'], entry['account'])
        if target not in self.sources:
            reader = self.reader_factory()
            self.sources[target] = Frontier.open_existing(self.root, entry['platform'], entry['account'], reader, production=True)
        return self.sources[target]

    def access(self, target, document, version, metadata):
        try:
            if target not in self.current:
                self.revision(target)
            source, bodies = self.current[target]
            claimed = metadata.get('source', {}) if isinstance(metadata, dict) else {}
            if not isinstance(claimed, dict):
                return False
            content = claimed.get('content_hash')
            if (not isinstance(content, str) or claimed.get('kind') != 'messenger-exact-content-provenance'
                    or content not in bodies or document != identity('graph', 'messenger-content:' + content)):
                return False
            with source.store.database() as db:
                row = db.execute('''SELECT v.body,v.metadata FROM memberships m JOIN versions v
                    ON v.id=m.current_version JOIN documents d ON d.id=m.document_id
                    WHERE m.target_id=? AND m.document_id=? AND m.current_version=?
                      AND m.availability='available' AND d.availability='available' ''',
                    (target, document, version)).fetchone()
            return (row is not None and row['body'] == normalized_body(bodies[content])
                    and json.loads(row['metadata']).get('source', {}).get('content_hash') == content)
        except (ValueError, OSError, RuntimeError):
            return False

    def stream_revision(self, target):
        source = self._source(target)
        state, _ = binding(self.root, source.platform, source.account)
        authorization = source._source_revision(target)
        if read_enrollment(self.root)['revision'] != state['revision']:
            raise ValueError('messenger_enrollment_changed_during_read')
        return hashlib.sha256(encoded([state['revision'], authorization])).hexdigest()

    def relation_access(self, target, source, destination, relation_type, evidence):
        from alden_messenger_references import allowed
        try:
            return allowed(self, target, source, destination, relation_type, evidence)
        except (ValueError, OSError, RuntimeError, TypeError, KeyError):
            return False

    def revision(self, target):
        source = self._source(target)
        state, _ = binding(self.root, source.platform, source.account)
        revision = source._source_revision(target)
        with source.database() as db:
            count, size = db.execute("SELECT count(*),coalesce(sum(length(CAST(body AS BLOB))),0) FROM message_heads WHERE state='candidate'").fetchone()
            if count > MAX_REVISION_ORIGINS or size > MAX_REVISION_TEXT_BYTES:
                raise ValueError('messenger_content_revision_budget')
            heads = [dict(row) for row in db.execute("SELECT room,message,content_hash,body,page FROM message_heads WHERE state='candidate' ORDER BY room,message")]
        if not callable(getattr(source.reader, 'content_revisions', None)):
            raise ValueError('messenger_content_revision_capability_required')
        keys = [{'room_id': head['room'], 'message_id': head['message']} for head in heads]
        observed = {}
        for offset in range(0, len(keys), 8192):
            if self._cancelled():
                raise RuntimeError('messenger_request_cancelled')
            requested = keys[offset:offset + 8192]
            rows = source.reader.content_revisions(requested)
            expected = {(key['room_id'], key['message_id']) for key in requested}
            if not isinstance(rows, list) or len(rows) != len(expected):
                raise ValueError('messenger_content_revision_invalid')
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {'room_id', 'message_id', 'state', 'content_hash'}:
                    raise ValueError('messenger_content_revision_invalid')
                key = row['room_id'], row['message_id']
                if (not all(isinstance(value, str) for value in key) or key not in expected or key in seen
                        or row['state'] not in ('available', 'withheld', 'missing', 'unavailable')):
                    raise ValueError('messenger_content_revision_invalid')
                if row['state'] == 'available':
                    if not isinstance(row['content_hash'], str) or not re.fullmatch('[a-f0-9]{64}', row['content_hash']):
                        raise ValueError('messenger_content_revision_invalid')
                elif row['content_hash'] is not None:
                    raise ValueError('messenger_content_revision_invalid')
                observed[key] = (row['state'], row['content_hash'])
                seen.add(key)
        bodies = {}
        for head in heads:
            if (isinstance(head['body'], str) and hashlib.sha256(head['body'].encode()).hexdigest() == head['content_hash']
                    and observed[(head['room'], head['message'])] == ('available', head['content_hash'])):
                bodies[head['content_hash']] = head['body']
        if read_enrollment(self.root)['revision'] != state['revision']:
            raise ValueError('messenger_enrollment_changed_during_read')
        self.current[target] = source, bodies
        from alden_messenger_references import scopes
        reference_scopes = scopes(source, cancelled=self._cancelled)
        return hashlib.sha256(encoded([state['revision'], revision, heads,
            [[room, message, *observed[(room, message)]] for room, message in sorted(observed)], reference_scopes])).hexdigest()

    def retrieval_context(self, proof, **options):
        from alden_messenger_runtime import MessengerAuthority
        targets = sorted({m['target_id'] for m in proof['source_memberships']})
        registry = MessengerAuthority([self._source(target) for target in targets])
        return registry.retrieval_context(proof, **options)


def collect(root, target, *, cancelled=lambda: False, publication_guard=None, reader=None):
    from alden_messenger_runtime import Frontier
    authority = ProductionAuthority(root, reader_factory=(lambda: reader) if reader is not None else None, cancelled=cancelled)
    existing = authority._source(target)
    source = Frontier(root, existing.platform, existing.account, existing.reader, production=True)
    source.external_publication_guard = publication_guard
    catalog = source.reader.catalog()
    source.seed(catalog)
    recent = tuple(room['id'] for room in catalog['rooms'][:16]) if catalog.get('room_order') == 'recent_activity_desc' else ()
    if recent:
        plan = [('backfill', recent), ('live', recent), ('backfill', ()), ('live', ()),
                ('backfill', recent), ('live', recent), ('backfill', ()), ('live', ()), ('reconcile', ())]
    else:
        plan = [(mode, ()) for mode in ['backfill'] * 4 + ['live'] * 4 + ['reconcile']]
    results = []
    for mode, preferred in plan:
        if cancelled():
            raise RuntimeError('collection_cancelled')
        result = source.step(mode=mode, preferred_rooms=preferred, cancelled=cancelled)
        results.append(result)
        if result['state'] in ('busy', 'cancelled') or result['state'] == 'blocked' and result.get('scope') != 'room':
            raise RuntimeError(result.get('reason', 'messenger_' + result['state']))
    # Source acquisition keeps all nine fair-sweep slots. References have a
    # separate bounded lane, so a deferred origin cannot consume a room's slot.
    reference_results = []
    if source.reference_supported():
        for _ in range(3):
            result = source.reference_step(cancelled=cancelled)
            reference_results.append(result)
            if result['state'] == 'cancelled':
                raise RuntimeError('collection_cancelled')
            if result['state'] in {'busy', 'idle'}:
                break
    with (publication_guard() if publication_guard else nullcontext()), source.store.database() as db:
        if cancelled():
            raise RuntimeError('collection_cancelled')
        db.execute('UPDATE targets SET last_success=?,next_run=?+interval_seconds,last_error=NULL WHERE id=?', (time.time(), time.time(), target))
    status = source.status()
    return {'target_id': target, 'steps': results, 'reference_steps': reference_results, 'status': status,
            'state': 'partial' if (status['blocked_rooms'] or status['reference_jobs'].get('pending', 0)
                                   or any(r['state'] == 'busy' for r in reference_results)) else 'complete',
            'schedule': {'policy': 'recent-with-fair-sweeps' if recent else 'fair-sweeps',
                         'preferred_room_count': len(recent), 'total_rooms': len(catalog['rooms']),
                         'reference_slot_budget': 3 if source.reference_supported() else 0},
            'coverage': 'bounded round-robin local backfill/live/reconciliation; original source server coverage unverified'}
