"""Project-scoped retrieval of retained collection versions, never graph authority."""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import struct

from alden_collection import CollectionStore, MAX_RECORD_BYTES, digest, safe_directory

ENCODING = 'e5-char256-stride192-weighted-unit-pool-v1'
TEXT_VERSION = 'retained-local-original-text-v1'
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_DOCUMENTS = 100000
BATCH = 8


def _check(cancelled):
    if cancelled():
        raise RuntimeError('collection_retrieval_cancelled')


def _scope(projects):
    if not isinstance(projects, (list, tuple)) or not 1 <= len(projects) <= 16:
        raise ValueError('collection_project_scope_required')
    if any(not isinstance(p, str) or not p or len(p) > 128 for p in projects):
        raise ValueError('collection_project_scope_invalid')
    return list(dict.fromkeys(projects))


def _rows(store, projects, cancelled, time_from=None, time_to=None, *, text_db=None, resolve_source=False):
    projects = _scope(projects)
    clauses, args = [], list(projects)
    for value, operator in [(time_from, '>='), (time_to, '<=')]:
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('collection_time_filter_invalid')
            clauses.append('v.collected_at' + operator + '?');args.append(value)
    if time_from is not None and time_to is not None and time_from > time_to:
        raise ValueError('collection_time_filter_invalid')
    owned = None
    if text_db is None and _path(store).is_file():
        owned = sqlite3.connect(_path(store).as_uri() + '?mode=ro', uri=True, timeout=.15)
        if owned.execute("SELECT 1 FROM sqlite_master WHERE name='texts'").fetchone():
            text_db = owned
    try:
        with store.database() as db:
            rows = db.execute('''SELECT d.id,d.platform,d.original_id,v.id AS version,
                v.raw_sha256,v.raw_path,v.label,v.body,v.collected_at
                FROM documents d JOIN (''' + store._scope_versions(projects) + ''') s
                ON s.document_id=d.id JOIN versions v ON v.id=s.current_version
                WHERE d.availability='available' ''' + ''.join(' AND ' + c for c in clauses) + ' ORDER BY d.id', args)
            result, budget = [], 0
            for row in rows:
                _check(cancelled)
                item = dict(row)
                base_hash = digest((item['label'] + '\n' + item['body']).encode())
                cached = text_db.execute('SELECT base_hash,raw_sha256,extraction,body,body_source FROM texts WHERE document_id=? AND version=?',
                                         (item['id'], item['version'])).fetchone() if text_db is not None else None
                ready = cached is not None and cached[:3] == (base_hash, item['raw_sha256'], TEXT_VERSION)
                if ready and (cached[4] not in {'stored_body', 'retained_record.localOriginalText'} or cached[4] == 'stored_body' and cached[3] != item['body']):
                    ready = False
                field = 'stored_body'
                if ready:
                    item['body'], field = cached[3], cached[4]
                elif resolve_source:
                    raw = store._verified_source_blob(store.blobs, item['raw_path'], MAX_RECORD_BYTES)
                    if digest(raw) != item['raw_sha256']:
                        raise RuntimeError('collection_source_integrity')
                    source = json.loads(raw)
                    if item['platform'] == 'graph' and isinstance(source, dict) and isinstance(source.get('localOriginalText'), str) and source['localOriginalText'].strip():
                        item['body'], field = source['localOriginalText'], 'retained_record.localOriginalText'
                    with text_db:
                        text_db.execute('INSERT OR REPLACE INTO texts VALUES(?,?,?,?,?,?,?,?)',
                                        (item['id'], item['version'], base_hash, item['raw_sha256'], TEXT_VERSION,
                                         item['label'], item['body'], field))
                        # A new version has no FTS row to replace. Both tables
                        # are written atomically, so avoid a full FTS scan for
                        # every newly materialized source version.
                        if cached is not None:
                            text_db.execute('DELETE FROM text_fts WHERE document_id=? AND version=?', (item['id'], item['version']))
                        text_db.execute('INSERT INTO text_fts VALUES(?,?,?,?)', (item['id'], item['version'], item['label'], item['body']))
                    ready = True
                item['text_ready'], item['body_source'] = ready, field
                text = item['label'] + '\n' + item['body']
                budget += len(text.encode())
                if budget > MAX_TEXT_BYTES or len(result) >= MAX_DOCUMENTS:
                    raise RuntimeError('collection_retrieval_budget')
                item['text_hash'] = digest(text.encode())
                result.append(item)
            return result
    finally:
        if owned is not None:
            owned.close()


def _index_rows(store, projects, cancelled, **options):
    # A union's newest visible version cannot replace an older version still
    # current in another authorized project. Persist both version identities.
    values = {}
    for project in _scope(projects):
        for row in _rows(store, [project], cancelled, **options):
            values[(row['id'], row['version'])] = row
    return [values[key] for key in sorted(values)]


def _path(store):
    path = store.root / 'retrieval.sqlite3'
    if path.is_symlink():
        raise ValueError('collection_projection_symlink')
    return path


def _vector(raw):
    if len(raw) % 4 or not 4 <= len(raw) <= 8192:
        raise RuntimeError('collection_vector_invalid')
    import auto_reply_knowledge_graph as kg
    return kg._normalize_dense_vector(list(struct.unpack('<' + 'f' * (len(raw) // 4), raw)))


def index_dense(root, projects, *, cancelled=lambda: False, embed=None, progress=None):
    store = CollectionStore.open_existing(Path(root))
    if store is None:
        return {'state': 'empty', 'documents': 0}
    with store.target_lock('collection-dense-retrieval'):
        return _index_dense(root, projects, cancelled=cancelled, embed=embed, progress=progress)


def _index_dense(root, projects, *, cancelled, embed, progress):
    """Explicit producer operation; persist complete document vectors incrementally."""
    import auto_reply_knowledge_graph as kg
    store = CollectionStore.open_existing(Path(root))
    if store is None:
        return {'state': 'empty', 'documents': 0}
    model, endpoint = kg._active_dense_embedding_model(), kg._dense_endpoint_identity()
    safe_directory(store.root)
    with closing(sqlite3.connect(_path(store), timeout=.15)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS vectors(
            document_id TEXT,version TEXT,text_hash TEXT,model TEXT,endpoint TEXT,
            encoding TEXT,vector BLOB,PRIMARY KEY(document_id,version))''')
        db.execute('''CREATE TABLE IF NOT EXISTS texts(document_id TEXT,version TEXT,
            base_hash TEXT,raw_sha256 TEXT,extraction TEXT,label TEXT,body TEXT,body_source TEXT,
            PRIMARY KEY(document_id,version))''')
        db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(document_id UNINDEXED,version UNINDEXED,label,body)')
        db.commit()
        source_rows = _index_rows(store, projects, cancelled, text_db=db, resolve_source=True)
        rows = [r for r in source_rows if (r['label'] + '\n' + r['body']).strip()]
        saved = {(r[0], r[1]): r[2:] for r in db.execute(
            'SELECT document_id,version,text_hash,model,endpoint,encoding FROM vectors')}
        changed, windows, reused = 0, 0, 0
        reused_rows = []
        def flush_reused():
            if not reused_rows:return
            _check(cancelled)
            with db:db.executemany('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?)',reused_rows)
            reused_rows.clear()
        pieces, owners, totals = [], [], {}
        def flush():
            nonlocal changed, windows
            if not pieces:
                return
            _check(cancelled)
            vectors = embed(pieces) if embed else kg._local_dense_embeddings(
                pieces, input_type='passage', model_id=model, state_root=Path(root))
            if len(vectors) != len(pieces):
                raise RuntimeError('collection_embedding_count')
            complete = []
            for (row, weight, final), vector in zip(owners, vectors):
                normalized = kg._normalize_dense_vector(list(vector))
                key = (row['id'], row['version'])
                total = totals.setdefault(key, [0.] * len(normalized))
                if len(total) != len(normalized):
                    raise RuntimeError('collection_vector_dimension')
                for i, value in enumerate(normalized):
                    total[i] += weight * value
                if final:
                    values = kg._normalize_dense_vector(totals.pop(key))
                    complete.append((row['id'], row['version'], row['text_hash'], model, endpoint,
                                     ENCODING, struct.pack('<' + 'f' * len(values), *values)))
            _check(cancelled)
            with db:
                db.executemany('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?)', complete)
            changed += len(complete);windows += len(pieces)
            if progress is not None and changed and changed % 128 < len(complete):
                progress({'completed_versions': changed, 'eligible_versions': len(rows)})
            pieces.clear();owners.clear()
        for row in rows:
            _check(cancelled)
            stamp = (row['text_hash'], model, endpoint, ENCODING)
            if saved.get((row['id'], row['version'])) == stamp:
                continue
            raw = store._verified_source_blob(store.blobs, row['raw_path'], MAX_RECORD_BYTES)
            if digest(raw) != row['raw_sha256']:
                raise RuntimeError('collection_source_integrity')
            prior=db.execute('SELECT vector FROM vectors WHERE document_id=? AND text_hash=? AND model=? AND endpoint=? AND encoding=? LIMIT 1',
                             (row['id'],row['text_hash'],model,endpoint,ENCODING)).fetchone()
            if prior:
                # Only the same document and exact encoder input/profile can
                # reuse a vector. Source/version authority was checked above.
                _vector(prior[0])
                reused_rows.append((row['id'],row['version'],row['text_hash'],model,endpoint,ENCODING,prior[0]));reused+=1
                if len(reused_rows)>=64:flush_reused()
                continue
            text = row['label'] + '\n' + row['body']
            for offset in range(0, len(text), 192):
                piece = text[offset:offset + 256]
                pieces.append(piece);owners.append((row, len(piece), offset + 192 >= len(text)))
                if len(pieces) == BATCH:
                    flush()
        flush()
        flush_reused()
        latest = _index_rows(store, projects, cancelled, text_db=db)
    signature = lambda values: [(r['id'], r['version'], r['text_hash']) for r in values]
    return {'state': 'ready' if signature(source_rows) == signature(latest) else 'pending',
            'projects': _scope(projects), 'documents': len(rows), 'embedded_changed': changed,
            'source_versions': len(source_rows), 'unsearchable_versions': len(source_rows) - len(rows),
            'embedded_windows': windows, 'reused_versions':reused, 'model': model, 'encoding': ENCODING}


def retrieve(root, query, *, projects, max_entities=3, max_relations=3,
             candidate_limit=40, max_context_chars=8000, rrf_k=60, rrf_weights=None,
             cancelled=lambda: False, query_embed=None, time_from=None, time_to=None, also=None):
    import auto_reply_knowledge_graph as kg
    projects = _scope(projects)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1024:
        raise ValueError('collection_query_invalid')
    if type(max_context_chars) is not int or not 256 <= max_context_chars <= 16000:
        raise ValueError('collection_context_budget')
    if type(candidate_limit) is not int or not 1 <= candidate_limit <= 100:
        raise ValueError('collection_candidate_budget')
    store = CollectionStore.open_existing(Path(root))
    if store is None:
        return {'facts': [], 'fact_provenance': [], 'search_mode': 'bm25_only', 'projects': projects}
    source_rows = _rows(store, projects, cancelled, time_from, time_to)
    rows = [r for r in source_rows if (r['label'] + '\n' + r['body']).strip()]
    eligible = {row['id']: row for row in rows}
    terms = kg._fts_query_terms([query])
    lexical = [(r['id'], float(r['rank'])) for r in store.search(terms, projects=projects,
                limit=candidate_limit, time_from=time_from, time_to=time_to)] if terms else []
    dense, mode = [], 'bm25_only'
    path = _path(store)
    if terms and path.is_file():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.15)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='text_fts'").fetchone():
                raw_lexical = db.execute('SELECT document_id,version,bm25(text_fts) FROM text_fts WHERE text_fts MATCH ? ORDER BY 3,document_id', (terms,))
                projected_lexical = []
                for identity, version, score in raw_lexical:
                    if identity in eligible and eligible[identity]['version'] == version and eligible[identity]['text_ready']:
                        projected_lexical.append((identity, float(score)))
                        if len(projected_lexical) == candidate_limit:
                            break
                if all(r['text_ready'] for r in source_rows):
                    lexical = projected_lexical
                else:
                    seen = {i for i, _ in lexical}
                    lexical = (lexical + [(i, s) for i, s in projected_lexical if i not in seen])[:candidate_limit]
    _check(cancelled)
    try:
        if path.is_file() and eligible:
            model, endpoint = kg._active_dense_embedding_model(), kg._dense_endpoint_identity()
            saved = {}
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.15)) as scoped:
                for start in range(0, len(rows), 200):
                    chunk = rows[start:start + 200]
                    clauses = ' OR '.join('(document_id=? AND version=?)' for _ in chunk)
                    values = [value for r in chunk for value in (r['id'], r['version'])]
                    saved.update({(r[0], r[1]): r[2:] for r in scoped.execute(
                        'SELECT document_id,version,text_hash,model,endpoint,encoding,vector FROM vectors WHERE ' + clauses, values)})
            if all(r['text_ready'] for r in source_rows) and all(saved.get((r['id'], r['version']), ())[:4] ==
                   (r['text_hash'], model, endpoint, ENCODING) for r in rows):
                values = query_embed(query) if query_embed else kg._local_dense_embeddings(
                    [query], input_type='query', model_id=model, state_root=Path(root))[0]
                vector = kg._normalize_dense_vector(list(values))
                for row in rows:
                    _check(cancelled)
                    passage = _vector(saved[(row['id'], row['version'])][4])
                    if len(vector) != len(passage):
                        raise RuntimeError('collection_vector_dimension')
                    dense.append((row['id'], kg._vector_similarity(vector, passage)))
                dense.sort(key=lambda r: (-r[1], r[0]))
                dense, mode = dense[:candidate_limit], 'rrf'
    except Exception:
        _check(cancelled)
        dense, mode = [], 'bm25_only'
    _check(cancelled)
    ranked = kg._rrf_merge(lexical, dense, k=rrf_k, weights=rrf_weights,
                          recency={r['id']: r['collected_at'] for r in rows}) if mode == 'rrf' else lexical
    if also is not None:
        if not isinstance(also, (list, tuple)) or len(also) > 40 or any(not isinstance(i, str) for i in also):
            raise ValueError('collection_explicit_ids_invalid')
        pinned = list(dict.fromkeys(i for i in also if i in eligible))
        ranked = [(i, 0.) for i in pinned] + [(i, s) for i, s in ranked if i not in pinned]
    facts, provenance, used = [], [], 0
    def append(text, proof):
        nonlocal used
        remaining = max_context_chars - used
        if remaining <= 0:
            return
        facts.append(text[:remaining]);proof['fact_truncated'] = len(text) > remaining
        provenance.append(proof);used += len(facts[-1])

    with store.database() as db:
        chosen = []
        for document_id, _score in ranked[:max(0, min(int(max_entities), 10))]:
            row = eligible[document_id]
            current = db.execute("SELECT s.current_version FROM (" + store._scope_versions(projects) +
                                 ") s JOIN documents d ON d.id=s.document_id WHERE d.id=? AND d.availability='available'", (*projects, document_id)).fetchone()
            if current is None or current[0] != row['version']:
                raise RuntimeError('collection_graph_version_changed')
            raw = store._verified_source_blob(store.blobs, row['raw_path'], MAX_RECORD_BYTES)
            if digest(raw) != row['raw_sha256']:
                raise RuntimeError('collection_source_integrity')
            record = json.loads(raw)
            if row['body_source'] == 'retained_record.localOriginalText' and (
                    not isinstance(record, dict) or record.get('localOriginalText') != row['body']):
                raise RuntimeError('collection_source_integrity')
            metadata = json.loads(db.execute('SELECT metadata FROM versions WHERE id=?', (row['version'],)).fetchone()[0])
            proof = {'fact_type': 'entity', 'entity_id': document_id, 'note_id': document_id,
                     'source_kind': 'collected_source', 'source_version': row['version'],
                     'note_hash': row['raw_sha256'], 'original_id': row['original_id'],
                     'platform': row['platform'], 'projects': projects, 'updated_at': row['collected_at'],
                     'body_source': row['body_source'],
                     'derived_from': metadata.get('source', {}), 'truth_status': 'source_record; not independently verified',
                     'provenance_valid': True, 'retracted': False}
            if isinstance(record, dict):
                proof['source_author'] = {key: record[key] for key in ['author', 'handle', 'date', 'url', 'localTextOrigin'] if isinstance(record.get(key), str)}
            append((row['label'] + ': ' if row['label'] and row['body'] else row['label']) + row['body'], proof)
            chosen.append(document_id)
        if chosen and max_relations > 0:
            marks = ','.join('?' for _ in projects)
            edges = db.execute('''SELECT r.*,sm.current_version AS source_version,
                tm.current_version AS target_version FROM relations r
                JOIN memberships sm ON sm.target_id=r.target_id AND sm.document_id=r.source
                JOIN memberships tm ON tm.target_id=r.target_id AND tm.document_id=r.target
                WHERE r.active=1 AND r.source=?
                AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id
                AND p.permission!='denied' AND p.project IN (''' + marks + ')) ORDER BY r.id',
                (chosen[0], *projects))
            for edge in edges:
                _check(cancelled)
                if edge['target'] not in eligible:
                    continue
                target = eligible[edge['target']]
                if edge['source_version'] != eligible[edge['source']]['version'] or edge['target_version'] != target['version']:
                    continue
                raw = store._verified_source_blob(store.blobs, target['raw_path'], MAX_RECORD_BYTES)
                if digest(raw) != target['raw_sha256']:
                    raise RuntimeError('collection_source_integrity')
                append(eligible[edge['source']]['label'] + ' — ' + edge['type'] + ' → ' + target['label'],
                       {'fact_type': 'relation', 'source_kind': 'collected_source', 'relation': edge['type'],
                        'source_id': edge['source'], 'target_id': edge['target'], 'relation_id': edge['id'],
                        'source_version': eligible[edge['source']]['version'], 'target_version': target['version'],
                        'projects': projects, 'source_evidence': json.loads(edge['evidence']),
                        'provenance_valid': True, 'retracted': False})
                if sum(p['fact_type'] == 'relation' for p in provenance) >= min(int(max_relations), 10):
                    break
    _check(cancelled)
    return {'facts': facts, 'fact_provenance': provenance, 'search_mode': mode,
            'projects': projects, 'eligible_documents': len(rows), 'context_chars': used,
            'unsearchable_documents': len(source_rows) - len(rows),
            'pending_text_versions': sum(not r['text_ready'] for r in source_rows),
            'time_basis': 'collection_time',
            'candidates': [row[0] for row in ranked], 'canonical': 'independent originals; derived collection projection'}
