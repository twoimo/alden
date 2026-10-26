"""Ephemeral, selected-source fences for the local knowledge tool loop.

Only the host's final trimmed context may enter capture_authority. No search,
model, network, index refresh or write is performed here. A receipt is bound to
one root inode, token object and immutable selected result; never serialize it,
send it to a model, or include it in telemetry. Empty receipts attest no sources.

Each call closes its read transactions before returning. Independent collection,
OSK and corpus reads are NOT an atomic snapshot or an authorization lease. A
writer can change a source after its last read, including before the turn CAS.
The caller must fence before hop 2, after generation and immediately before CAS;
this reduces, but cannot eliminate, that residual TOCTOU. Hashes bind bytes and
identities, never the semantic truth of a stored claim.

OSK reuses the domain-owned canonical namespace snapshot (MAX_NOTES admitted
notes, full SDK identity/ambiguity checks), including unmanaged notes and title
or alias relations. This is an authority rebuild, not a new query/retrieval. Its
cost includes reading the canonical namespace on every fence. The owner checks
cancellation between files; it does not promise a hard bound on one file read.
Only selected documents/sync bindings enter the receipt fingerprint. A future
selected-index optimization must preserve these identity rules and be measured.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from alden_abort import AldenCancelled

CHANGED = 'local_knowledge_source_changed'
UNAVAILABLE = 'local_knowledge_source_unavailable'
MAX_CONTEXT_BYTES = 128 * 1024  # Full proofs, unlike the compact model context.
MAX_PROOFS = 32               # Includes compacted matching_sources.
MAX_FILE_BYTES = 2 * 1024 * 1024
_SEAL = object()


class KnowledgeAuthorityError(RuntimeError):
    """Only a fixed, content-free error crosses the host boundary."""


def _fail(code=UNAVAILABLE):
    raise KnowledgeAuthorityError(code)


def _check(token):
    token.raise_if_cancelled()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(value).digest()


def _safe_path(path):
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        _fail()
    return path


def _root(root):
    path = _safe_path(root)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        _fail()
    return str(path), info.st_dev, info.st_ino


def _read(path, limit, token):
    """Bounded, cancellable regular-file reads; never follow a leaf symlink."""
    _check(token)
    path = _safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            _fail()
        chunks, size = [], 0
        while True:
            _check(token)
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                _fail()
            chunks.append(chunk)
        _check(token)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _selected(context):
    # Bound before serialization, including recursion and aggregate string work.
    remaining = [MAX_CONTEXT_BYTES, 12000]
    def bounded(value, depth=0):
        remaining[1] -= 1
        if depth > 16 or remaining[1] < 0:
            _fail()
        if type(value) is str:
            remaining[0] -= len(value.encode('utf-8'))
            if remaining[0] < 0:
                _fail()
        elif type(value) is dict:
            for k, v in value.items():
                if type(k) is not str:
                    _fail()
                bounded(k, depth + 1); bounded(v, depth + 1)
        elif type(value) is list:
            for v in value:
                bounded(v, depth + 1)
        elif value is not None and type(value) not in (bool, int, float):
            _fail()
    if type(context) is not dict:
        _fail()
    value = {k: context.get(k, []) for k in ('graph_facts', 'graph_provenance', 'quoted_history')}
    bounded(value)
    facts, proofs, rows = value.values()
    if (any(type(v) is not list for v in (facts, proofs, rows))
            or len(facts) > 7 or len(rows) > 4 or len(facts) != len(proofs)
            or any(type(f) is not str or not f or len(f) > 8000 for f in facts)):
        _fail()
    payload = _json(value)
    if len(payload) > MAX_CONTEXT_BYTES:
        _fail()
    return payload


@dataclass(frozen=True, repr=False, eq=False)
class _Receipt:
    root: tuple
    token: object
    selected: bytes
    state: bytes
    seal: object

    def __repr__(self):
        return '<local knowledge authority receipt>'

    def __reduce__(self):
        raise TypeError('local knowledge authority receipts are in-process only')


def _proof(proof):
    if (type(proof) is not dict or proof.get('provenance_valid') is not True
            or proof.get('retracted') is not False
            or type(proof.get('fact_truncated')) is not bool
            or proof.get('fact_type') not in ('entity', 'relation')):
        _fail()


def _fact(fact, text, proof):
    if not (text.startswith(fact) if proof['fact_truncated'] else text == fact):
        _fail(CHANGED)


def _collection(root, selected, token):
    from alden_collection import (CollectionStore, _read_cancellation,
                                  require_relation_binding, MAX_RECORD_BYTES)
    from alden_collection_retrieval import (_verified_source, _retrieval_relations,
        _strict_json, _scope, CONTENT_POLICY, content_evidence_class,
        declared_source_annotations, project_source_text)
    from alden_retrieval_time import collection_time_evidence, observation_note
    states = []
    with _read_cancellation(token.is_cancelled):
        store = CollectionStore.open_existing(root)
        if store is None or store.schema < 3:
            _fail()
        # The owner's leaf blob reader uses a single blocking open/read. Keep
        # its exact filename/digest contract while adding cancellable chunks and
        # regular-file/O_NOFOLLOW checks; _verified_source still owns all source
        # permissions, version, metadata and body-projection decisions.
        def blob(folder, name, budget):
            if not isinstance(name, str) or not re.fullmatch('[0-9a-f]{64}\\.json', name):
                _fail()
            result = _read(folder / name, budget, token)
            if hashlib.sha256(result).hexdigest() + '.json' != name:
                _fail(CHANGED)
            return result
        store._verified_source_blob = blob
        with store.database(cancelled=token.is_cancelled) as db:
            def document(identity, version, projects, body_source='stored_body'):
                _check(token)
                sizes = db.execute('SELECT length(CAST(body AS BLOB)),length(CAST(metadata AS BLOB)) '
                                   'FROM versions WHERE document_id=? AND id=?', (identity, version)).fetchone()
                if sizes is None:
                    _fail(CHANGED)
                if any(size is None or size > MAX_FILE_BYTES for size in sizes):
                    _fail()
                row = db.execute('''SELECT d.id,d.platform,d.original_id,v.id AS version,
                    v.label,v.body,v.metadata,v.collected_at,v.raw_sha256,v.raw_path,
                    v.processing_version FROM documents d JOIN versions v ON v.document_id=d.id
                    WHERE d.id=? AND v.id=?''', (identity, version)).fetchone()
                if row is None:
                    _fail(CHANGED)
                row = dict(row)
                if row['processing_version'] == 'legacy':
                    _fail()
                if body_source not in ('stored_body', 'retained_record.localOriginalText'):
                    _fail()
                row['body_source'] = body_source
                if body_source == 'retained_record.localOriginalText':
                    record = _strict_json(blob(store.blobs, row['raw_path'], MAX_RECORD_BYTES))
                    row['body'] = project_source_text(record['localOriginalText'], record)['body']
                record, metadata, memberships = _verified_source(db, store, row, projects)
                _check(token)
                return row, record, metadata, memberships

            def scopes(memberships):
                values = []
                for target in sorted({m['target_id'] for m in memberships}):
                    _check(token)
                    value = tuple(db.execute('SELECT alden_source_scope(?),alden_source_stream(?)',
                                             (target, target)).fetchone())
                    if any(v is not None and not re.fullmatch('[0-9a-f]{64}', v) for v in value):
                        _fail()
                    values.append((target, value))
                return values

            for fact, proof in selected:
                _check(token)
                projects = _scope(proof['requested_projects'])
                members = proof['source_memberships']
                if type(members) is not list or not 1 <= len(members) <= 256:
                    _fail()
                if proof['fact_type'] == 'entity':
                    if proof.get('scope_basis') not in ('current_permitted_version_memberships_at_selected_snapshot',
                                                        'current_canonical_excerpt_at_selected_version'):
                        _fail()
                    row, record, metadata, current = document(proof['entity_id'], proof['source_version'],
                                                             projects, proof['body_source'])
                    expected = {'note_id': row['id'], 'note_hash': row['raw_sha256'],
                        'original_id': row['original_id'], 'platform': row['platform'],
                        'updated_at': row['collected_at'], 'body_source': row['body_source'],
                        'projects': list(dict.fromkeys(m['project'] for m in members))}
                    if any(proof.get(k) != v for k, v in expected.items()) or any(m not in current for m in members):
                        _fail(CHANGED)
                    derived = metadata.get('source', {})
                    if isinstance(derived, dict) and derived.get('kind') == 'messenger-exact-content-provenance':
                        derived = {'kind': derived['kind'], 'content_hash': derived.get('content_hash'),
                            'current_source_targets': sorted({m['target_id'] for m in members}),
                            'scope': 'current_verified_source_memberships; historical first-publisher metadata retained privately'}
                    if proof.get('derived_from') != derived:
                        _fail(CHANGED)
                    projection = row.get('text_projection')
                    if proof.get('text_projection') != ({k: v for k, v in projection.items() if k != 'separated_tail'}
                                                        if projection is not None else None):
                        _fail(CHANGED)
                    author = {k: record[k] for k in ('author','handle','date','url','localTextOrigin')
                              if isinstance(record, dict) and isinstance(record.get(k), str)}
                    classification = content_evidence_class(metadata, row['body'], source=record,
                                                            body_source=row['body_source'])
                    if (proof.get('source_author', {}) != author
                            or proof.get('time_evidence') != collection_time_evidence(row['collected_at'], author.get('date'))
                            or proof.get('content_evidence') != {'policy': CONTENT_POLICY, 'classification': classification}):
                        _fail(CHANGED)
                    if isinstance(record, dict) and record.get('platform') == 'threads' and record.get('type') == 'source':
                        if proof.get('source_annotations') != declared_source_annotations(record, max_field_bytes=900):
                            _fail(CHANGED)
                    text = (row['label'] + ': ' if row['label'] and row['body'] else row['label']) + row['body']
                    if classification != 'content_candidate':
                        text = {'reference_only': '[링크 참조 · 연결된 페이지 본문 미확인] ',
                                'empty_body': '[제목·이름 참조 · 본문 없음] '}.get(
                                    classification, '[자료 설명 · 원래 본문 미확인] ') + text
                    if 'observation_time' in proof:
                        if proof['observation_time'] != metadata.get('published_at') or proof.get('observation_basis') != 'source_published_at':
                            _fail(CHANGED)
                        text = observation_note(proof['observation_time'], 'source_published_at') + text
                    _fact(fact, text, proof)
                    states.append((row, members, scopes(members)))
                else:
                    if proof.get('scope_basis') != 'current_permitted_relation_memberships_at_selected_snapshot':
                        _fail()
                    edges = list(_retrieval_relations(db, store, projects, [proof['source_id']], [proof['relation_id']]))
                    if len(edges) != 1:
                        _fail(CHANGED)
                    edge = dict(edges[0]); require_relation_binding(db, edge)
                    expected = {'source_id': edge['source'], 'target_id': edge['target'],
                        'source_version': edge['source_version'], 'target_version': edge['target_version'],
                        'relation': edge['type'], 'evidence_version': edge['version'],
                        'source_evidence': _strict_json(edge['evidence'])}
                    if any(proof.get(k) != v for k, v in expected.items()):
                        _fail(CHANGED)
                    marks = ','.join('?' for _ in projects)
                    current = [{**dict(m), 'source_version': edge['source_version'], 'target_version': edge['target_version']}
                        for m in db.execute('SELECT target_id,project,permission FROM target_projects WHERE target_id=? '
                            "AND permission!='denied' AND project IN (" + marks + ') ORDER BY project', (edge['target_id'], *projects))]
                    if members != current or proof.get('projects') != [m['project'] for m in current]:
                        _fail(CHANGED)
                    left = document(edge['source'], edge['source_version'], projects)
                    right = document(edge['target'], edge['target_version'], projects)
                    _fact(fact, left[0]['label'] + ' — ' + edge['type'] + ' → ' + right[0]['label'], proof)
                    states.append((edge, left[0], right[0], current, scopes(current)))
    return states


def _corpus(root, rows, token):
    from alden_corpus import _published_rooms, message_source_role
    from auto_reply_knowledge_graph import _message_datetime_kst
    _check(token)
    pointer = root / 'knowledge/corpus/current.json'
    before = _read(pointer, 8192, token)
    states = []
    with _published_rooms(root) as (db, account):
        if db is None:
            _fail()
        db.set_progress_handler(lambda: int(token.is_cancelled()), 256)
        try:
            # Require the real publication's indexed exact-identity contract.
            sql = '''SELECT chat,chat_id,log_id,author_id,user_name,message,date,is_self,message_type
                     FROM alden_messages WHERE chat_id=? AND log_id=? LIMIT 2'''
            plan = list(db.execute('EXPLAIN QUERY PLAN ' + sql, ('0', '0')))
            if not any('SEARCH alden_messages' in r[3] for r in plan):
                _fail()
            for proof in rows:
                _check(token)
                if type(proof) is not dict or proof.get('source_kind') != 'local_db_snapshot':
                    _fail()
                room, log = proof['chat_id'], proof['log_id']
                if (type(room) is not str or type(log) is not str
                        or not re.fullmatch('[0-9]{1,19}', room) or not re.fullmatch('[0-9]{1,19}', log)
                        or proof['source_id'] != f'kakao:{account}:room:{room}:log:{log}'):
                    _fail(CHANGED)
                size = db.execute('SELECT length(CAST(message AS BLOB)) FROM alden_messages WHERE chat_id=? AND log_id=?', (room, log)).fetchone()
                if size is None:
                    _fail(CHANGED)
                if size[0] is None or size[0] > MAX_FILE_BYTES:
                    _fail()
                matches = db.execute(sql, (room, log)).fetchall()
                if len(matches) != 1:
                    _fail(CHANGED)
                values = matches[0]
                row = dict(zip(('chat','chat_id','log_id','author_id','user_name','message','date','is_self','message_type'), values))
                text = row['message']; stamp = _message_datetime_kst(row['date'])
                content = proof['content']
                if type(text) is not str or type(content) is not str or not 1 <= len(content) <= 1000:
                    _fail()
                expected = {'author_id': row['author_id'], 'sender': row['user_name'],
                    'source_date': row['date'], 'date': stamp.isoformat() if stamp else str(row['date']),
                    'message_type': row['message_type'], 'source_role': message_source_role(row),
                    'truncated': len(content) < len(text)}
                if (row['chat'] != f'kakao:{account}:room:{room}' or not text.startswith(content)
                        or any(proof.get(k) != v for k, v in expected.items())):
                    _fail(CHANGED)
                states.append(row)  # Full selected row detects edits past a quoted prefix.
            _check(token)
        finally:
            db.set_progress_handler(None, 0)
    if _read(pointer, 8192, token) != before:
        _fail(CHANGED)
    return states


def _osk(root, selected, token):
    import alden_osk as osk
    from alden_osk_retrieval import _snapshot
    from alden_collection_retrieval import _strict_json
    from alden_retrieval_time import observation_note
    home = osk._home(root)
    checkpoint_path = home / 'sync.json'
    def checkpoint_bytes():
        if not checkpoint_path.exists() and not checkpoint_path.is_symlink():
            return b'{}'
        return _read(checkpoint_path, 16 * 1024 * 1024, token)
    before = checkpoint_bytes()
    checkpoint = _strict_json(before)
    # This owner alone resolves canonical IDs, titles/aliases, duplicate IDs,
    # duplicate stems, retractions, source bindings and filtered reference bodies.
    # Do not substitute its disposable search projection or build a guessed path.
    _check(token)
    docs, _signature = _snapshot(root, cancelled=token.is_cancelled)
    _check(token)
    if checkpoint_bytes() != before:
        _fail(CHANGED)
    by_id = {}
    for doc in docs:
        _check(token)
        if doc['note_id'] in by_id:
            _fail()
        by_id[doc['note_id']] = doc
    loaded = {}
    def note(identity, expected_hash):
        _check(token)
        doc = by_id.get(identity)
        if doc is None or doc['hash'] != expected_hash:
            _fail(CHANGED)
        loaded[identity] = doc
        return doc
    for fact, proof in selected:
        if proof['fact_type'] == 'entity':
            doc = note(proof['note_id'], proof['note_hash'])
            expected = {'entity_id': doc['id'], 'space': doc['space'], 'room_id': doc['room_id'],
                        'source_account': doc['source_account'], 'source_event_ids': doc['source_event_ids'],
                        'derived_from': doc['derived_from'], 'conflicts': doc['conflicts'],
                        'updated_at': doc['updated_at']}
            if any(proof.get(k) != v for k, v in expected.items()):
                _fail(CHANGED)
            text = doc['title'] + ': ' + doc['body']
            # OSK retrieval currently adds this prefix without a proof field.
            prefix = observation_note(doc['updated_at'], 'note_revision_at')
            if fact.startswith(prefix):
                text = prefix + text
            _fact(fact, text, proof)
        else:
            left = note(proof['source_note_id'], proof['note_hash'])
            right = note(proof['target_note_id'], proof['target_note_hash'])
            relation = proof['relation']
            if (proof['source_id'] != left['id'] or proof['target_id'] != right['id']
                    or (relation, right['note_id']) not in left['links']):
                _fail(CHANGED)
            _fact(fact, left['title'] + ' — ' + relation + ' → ' + right['title'], proof)
    # Re-read selected files after the owner's inventory/parse work, as the
    # existing retrieval does. Use its exact returned paths; do not guess them.
    for doc in loaded.values():
        _check(token)
        path = _safe_path(home / 'vault' / doc['path'])
        # Retain the owner's admitted file sizes, including large human notes.
        # Limit each read to its observed size, checking cancellation in chunks.
        if osk.digest(_read(path, path.stat().st_size, token)) != doc['hash']:
            _fail(CHANGED)
    if checkpoint_bytes() != before:
        _fail(CHANGED)
    bindings = {identity: [] for identity in loaded}
    for key, item in checkpoint.get('managed', {}).items():
        _check(token)
        if item.get('osk_id') in bindings:
            bindings[item['osk_id']].append((key, item))
    # Unmanaged canonical notes intentionally have no sync binding. Multiple
    # metadata claims are retained, never reduced to an invented first match.
    return [(identity, doc, bindings[identity]) for identity, doc in sorted(loaded.items())]


def _state(root, payload, token):
    context = json.loads(payload)
    collection, canonical = [], []
    count = 0
    for fact, proof in zip(context['graph_facts'], context['graph_provenance']):
        _check(token); _proof(proof)
        matching = proof.get('matching_sources')
        extra = []
        if matching is not None:
            from alden_collection_retrieval import CONTEXT_TEXT_POLICY
            if (type(matching) is not dict or matching.get('schema') != 1
                    or matching.get('policy') != CONTEXT_TEXT_POLICY
                    or matching.get('scope') != 'context_text_only; originals_not_merged'
                    or type(matching.get('sources')) is not list):
                _fail()
            extra = matching['sources']
        for p in [proof, *extra]:
            _proof(p); count += 1
            if count > MAX_PROOFS or p is not proof and ('matching_sources' in p or p['fact_type'] != 'entity'):
                _fail()
            if p['source_kind'] == 'collected_source':
                collection.append((fact, p))
            elif p['source_kind'] == 'canonical_osk_note' and not matching:
                canonical.append((fact, p))
            else:
                _fail()
    state = []
    if collection:
        state.append(_collection(root, collection, token))
    if canonical:
        state.append(_osk(root, canonical, token))
    if context['quoted_history']:
        state.append(_corpus(root, context['quoted_history'], token))
    _check(token)
    return _hash(_json(state))


def _run(token, operation):
    try:
        _check(token)
        result = operation()
        _check(token)
        return result
    except AldenCancelled:
        raise
    except KnowledgeAuthorityError:
        _check(token)
        raise
    except Exception as error:
        _check(token)  # SQLite callback errors must preserve cancel/deadline.
        # Missing/unknown proof/schema is unavailable; recognized domain changes
        # (including integrity and authorization failures) are fixed-code changes.
        code = CHANGED if str(error).startswith(('collection_graph_', 'collection_source_',
            'collection_version_integrity', 'collection_relation_', 'corpus_account_mismatch')) else UNAVAILABLE
        raise KnowledgeAuthorityError(code) from None


def capture_authority(root: Path, context: dict, token) -> object:
    """Capture only this final selected result; cancellation is never swallowed."""
    def capture():
        bound = _root(root); payload = _selected(context)
        state = _state(Path(bound[0]), payload, token)
        if _root(root) != bound:
            _fail(CHANGED)
        return _Receipt(bound, token, payload, state, _SEAL)
    return _run(token, capture)


def verify_authority(root: Path, receipt: object, token) -> None:
    """Freshly check the same sources; no retry, replacement or broader lookup."""
    def verify():
        if type(receipt) is not _Receipt or receipt.seal is not _SEAL:
            _fail()
        if receipt.token is not token or _root(root) != receipt.root:
            _fail(CHANGED)
        if _state(Path(receipt.root[0]), receipt.selected, token) != receipt.state:
            _fail(CHANGED)
        if _root(root) != receipt.root:
            _fail(CHANGED)
    return _run(token, verify)
