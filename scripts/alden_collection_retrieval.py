"""Project-scoped retrieval of retained collection versions, never graph authority."""
from contextlib import closing
import json
import re
import math
from pathlib import Path
import sqlite3
import struct

from alden_collection import CollectionStore, MAX_RECORD_BYTES, digest, encoded, identity, safe_directory, require_relation_binding, korean_literal_plan, korean_literal_matches
from alden_content_evidence import POLICY as CONTENT_POLICY, content_evidence_class, declared_source_annotations, ANNOTATION_FIELDS
from alden_source_text import TEXT_VERSION, project_source_text

ENCODING = 'e5-char256-stride192-weighted-unit-pool-v1'
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_DOCUMENTS = 100000
BATCH = 8
MAX_SOURCE_MEMBERSHIPS = 256
MAX_BODY_EVIDENCE_CHECKS = 100
CONTEXT_TEXT_POLICY = 'retained-threads-annotation-context-v2'


def _context_text_key(text, proof, record, metadata):
    """Exact retained-text presentation only; never entity/source identity.

    Identity and capture coordinates remain in every individual proof. All
    Author/date/media/unknown fields must match. Bounded declared tag values
    may differ only with every original's declarations retained in its proof.
    Missing author/date, incomplete tags or partial content stays separate.
    """
    if (proof['content_evidence']['classification'] != 'content_candidate'
            or proof['body_source'] != 'retained_record.localOriginalText'
            or not isinstance(record, dict) or record.get('platform') != 'threads'
            or record.get('type') != 'source'
            or not all(isinstance(record.get(k), str) and record[k].strip()
                       for k in ('author', 'handle', 'date', 'localOriginalText'))
            or any(record.get(k) or metadata.get(k) for k in
                   ('truncated', 'partial', 'body_truncated', 'localTextTruncated', 'content_truncated'))):
        return None
    annotations = proof.get('source_annotations')
    if (annotations is None or any(annotations['fields'][k]['state'] not in
            ('declared', 'undeclared') for k in ANNOTATION_FIELDS)):
        return None
    other_metadata = {k: v for k, v in metadata.items()
                      if k not in ('source', 'original_id', 'source_node_id')}
    for key in ANNOTATION_FIELDS:
        # Only a literal mirror of the verified declaration may be excluded.
        # A different projection annotation remains a separate semantic field.
        if key in other_metadata and key in record and other_metadata[key] == record[key]:
            del other_metadata[key]
    contexts = [(m['target_id'], m['project'], m['permission']) for m in proof['source_memberships']]
    return digest(encoded([text, contexts, proof['truth_status'], proof['body_source'],
        proof['content_evidence'],
        [annotations['fields'][k]['state'] for k in ANNOTATION_FIELDS], other_metadata,
        {k: v for k, v in record.items() if k not in ('id', 'code', 'url', *ANNOTATION_FIELDS)}]))


def _prefer_body_literal_evidence(store, rows, ranked, plans, projects, limit, cancelled):
    """RAG grounding preference; source truth and backend scores stay separate."""
    matched = set()
    for row in rows.values():
        _check(cancelled)
        if row['body'].strip() and all(korean_literal_matches(plan, '', row['body']) for plan in plans):
            matched.add(row['id'])
    ordered = list(dict.fromkeys(identity for identity, _ in ranked if identity in matched))
    seen = set(ordered)
    # Unranked admissions have deterministic identity order. Existing backend
    # rank order remains intact within each evidence tier.
    ordered.extend(sorted(matched - seen))
    preferred, checked = set(), 0
    with store.database() as db:
        for identity in ordered[:MAX_BODY_EVIDENCE_CHECKS]:
            _check(cancelled)
            row = rows[identity]
            source, metadata, _memberships = _verified_source(db, store, row, projects)
            checked += 1
            if content_evidence_class(metadata, row['body'], source=source,
                                      body_source=row['body_source']) == 'content_candidate':
                preferred.add(identity)
    ranked_ids = {identity for identity, _ in ranked}
    candidates = ranked + [(identity, 0.) for identity in ordered if identity in preferred and identity not in ranked_ids]
    candidates.sort(key=lambda item: item[0] not in preferred)
    return candidates[:limit], preferred, {'matched':len(matched), 'verified':checked,
        'preferred':len(preferred), 'verification_limit':MAX_BODY_EVIDENCE_CHECKS,
        'truncated':len(ordered)>checked}


def _strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError('collection_json_duplicate_key')
            result[key] = item
        return result
    def constant(_value):
        raise ValueError('collection_json_nonfinite')
    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def _verified_source(db, store, row, projects):
    """Bind selected source bytes to the current permitted projection snapshot."""
    current = db.execute('''SELECT v.*,d.platform,d.original_id FROM (''' +
        store._scope_versions(projects) + ''') s JOIN documents d ON d.id=s.document_id
        JOIN versions v ON v.id=s.current_version
        WHERE d.id=? AND d.availability='available' ''', (*projects, row['id'])).fetchone()
    if current is None or current['id'] != row['version']:
        raise RuntimeError('collection_graph_version_changed')
    try:
        metadata = _strict_json(current['metadata'])
        if (not isinstance(metadata, dict) or not isinstance(current['metadata'], str)
                or any(not isinstance(current[k], str) for k in
                       ('document_id','platform','original_id','label','body',
                        'raw_sha256','raw_path','processing_version','projection_sha256'))):
            raise ValueError('version_fields')
        projection = digest(encoded([current['label'], current['body'],
            {k:v for k,v in metadata.items() if k != 'source'}]))
        expected = identity('version', encoded([row['id'], current['raw_sha256'],
                            current['processing_version'], projection]).decode())
        if (current['document_id'] != row['id']
                or identity(current['platform'], current['original_id']) != row['id']
                or projection != current['projection_sha256'] or expected != current['id']
                or any(current[k] != row[k] for k in ('label','platform','original_id','raw_sha256','raw_path','collected_at'))):
            raise ValueError('version_binding')
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise RuntimeError('collection_version_integrity') from error
    raw = store._verified_source_blob(store.blobs, current['raw_path'], MAX_RECORD_BYTES)
    if digest(raw) != current['raw_sha256']:
        raise RuntimeError('collection_source_integrity')
    try:
        record = _strict_json(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise RuntimeError('collection_source_integrity') from error
    if (row['body_source'] == 'retained_record.localOriginalText'
            and (not isinstance(record, dict) or not isinstance(record.get('localOriginalText'), str)
                 or project_source_text(record['localOriginalText'], record)['body'] != row['body'])
            or row['body_source'] == 'stored_body' and current['body'] != row['body']):
        raise RuntimeError('collection_source_integrity')
    original = record.get('localOriginalText') if isinstance(record, dict) else None
    if row['platform'] == 'graph' and isinstance(original, str) and original.strip():
        projected = project_source_text(original, record)
        # Legacy fallback is admitted only after its original version binding.
        if row['body_source'] == 'stored_body' and current['body'] == original:
            row['body'], row['body_source'] = projected['body'], 'retained_record.localOriginalText'
        row['text_projection'] = projected['projection']
    marks = ','.join('?' for _ in projects)
    memberships = [dict(r) for r in db.execute('''SELECT m.target_id,p.project,p.permission,
        m.current_version AS source_version FROM memberships m
        JOIN target_projects p ON p.target_id=m.target_id
        WHERE m.document_id=? AND m.current_version=? AND m.availability='available'
        AND p.permission!='denied' AND p.project IN (''' + marks + ''')
        ORDER BY p.project,m.target_id LIMIT 257''', (row['id'], row['version'], *projects))]
    if len(memberships) > MAX_SOURCE_MEMBERSHIPS:
        raise RuntimeError('collection_retrieval_budget')
    if not memberships:
        raise RuntimeError('collection_graph_version_changed')
    if any(m['permission'] not in ('public','local-private') for m in memberships):
        raise RuntimeError('collection_source_scope_integrity')
    return record, metadata, memberships


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
            source_query = ' FROM documents d JOIN (' + store._scope_versions(projects) + ''') s
                ON s.document_id=d.id JOIN versions v ON v.id=s.current_version
                WHERE d.availability='available' ''' + ''.join(' AND ' + c for c in clauses)
            fts_ids = {}
            if resolve_source:
                keys = db.execute('SELECT d.id,v.id' + source_query + ' LIMIT ?', (*args, MAX_DOCUMENTS+1)).fetchall()
                if len(keys)>MAX_DOCUMENTS:raise RuntimeError('collection_retrieval_budget')
                with text_db:
                    text_db.execute('DROP TABLE IF EXISTS temp.source_text_scope')
                    text_db.execute('CREATE TEMP TABLE source_text_scope(document_id TEXT,version TEXT,PRIMARY KEY(document_id,version))')
                    text_db.executemany('INSERT INTO source_text_scope VALUES(?,?)', keys)
                # One bounded scope scan. Deleting by the unindexed FTS fields
                # for each migrated record otherwise rescans every old version.
                for rowid, doc, version in text_db.execute('''SELECT rowid,document_id,version FROM text_fts
                    WHERE EXISTS(SELECT 1 FROM source_text_scope s WHERE s.document_id=text_fts.document_id AND s.version=text_fts.version)'''):
                    fts_ids.setdefault((doc,version), []).append(rowid)
            pending_text, pending_delete, pending_fts = [], [], []
            def flush():
                if not pending_text and not pending_fts:return
                _check(cancelled)
                with text_db:
                    text_db.executemany('INSERT OR REPLACE INTO texts VALUES(?,?,?,?,?,?,?,?)', pending_text)
                    text_db.executemany('DELETE FROM text_fts WHERE rowid=?', pending_delete)
                    text_db.executemany('INSERT INTO text_fts VALUES(?,?,?,?)', pending_fts)
                pending_text.clear();pending_delete.clear();pending_fts.clear()
            rows = db.execute('''SELECT d.id,d.platform,d.original_id,v.id AS version,
                v.raw_sha256,v.raw_path,v.label,v.body,v.collected_at''' + source_query + ' ORDER BY d.id', args)
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
                        item['body'], field = project_source_text(source['localOriginalText'], source)['body'], 'retained_record.localOriginalText'
                    pending_text.append((item['id'], item['version'], base_hash, item['raw_sha256'], TEXT_VERSION,
                                         item['label'], item['body'], field))
                    ready = True
                if resolve_source:
                    rowids = fts_ids.get((item['id'],item['version']), [])
                    prior = text_db.execute('SELECT label,body FROM text_fts WHERE rowid=?', (rowids[0],)).fetchone() if len(rowids)==1 else None
                    if prior != (item['label'],item['body']):
                        pending_delete.extend((rowid,) for rowid in rowids)
                        pending_fts.append((item['id'],item['version'],item['label'],item['body']))
                    if len(pending_text)>=256 or len(pending_fts)>=256:flush()
                item['text_ready'], item['body_source'] = ready, field
                text = item['label'] + '\n' + item['body']
                budget += len(text.encode())
                if budget > MAX_TEXT_BYTES or len(result) >= MAX_DOCUMENTS:
                    raise RuntimeError('collection_retrieval_budget')
                item['text_hash'] = digest(text.encode())
                result.append(item)
            flush()
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


def index_text(root, projects, *, cancelled=lambda: False):
    """Explicit reversible text-cache refresh; no model loading or embedding."""
    store = CollectionStore.open_existing(Path(root))
    if store is None:
        return {'state': 'empty', 'documents': 0}
    projects = _scope(projects)
    with store.target_lock('collection-dense-retrieval'):
        safe_directory(store.root)
        with closing(sqlite3.connect(_path(store), timeout=.15)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS texts(document_id TEXT,version TEXT,
                base_hash TEXT,raw_sha256 TEXT,extraction TEXT,label TEXT,body TEXT,body_source TEXT,
                PRIMARY KEY(document_id,version))''')
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(document_id UNINDEXED,version UNINDEXED,label,body)')
            db.commit()
            rows = _index_rows(store, projects, cancelled, text_db=db, resolve_source=True)
            return {'state': 'ready', 'documents': len(rows), 'projects': projects,
                    'text_version': TEXT_VERSION, 'embeddings_generated': 0}


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
        # _index_rows(resolve_source=True) already admitted this bounded scope.
        # Probe its current versions by the existing primary key instead of
        # loading metadata for every historical or unrelated project vector.
        saved = {(r[0], r[1]): r[2:] for r in db.execute('''
            SELECT v.document_id,v.version,v.text_hash,v.model,v.endpoint,v.encoding
            FROM temp.source_text_scope s CROSS JOIN vectors v
            ON v.document_id=s.document_id AND v.version=s.version''')}
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
    if also is not None and (not isinstance(also,(list,tuple)) or len(also)>40 or any(not isinstance(i,str) for i in also)):
        raise ValueError('collection_explicit_ids_invalid')
    store = CollectionStore.open_existing(Path(root))
    if store is None:
        return {'facts': [], 'fact_provenance': [], 'search_mode': 'bm25_only', 'projects': projects}
    source_rows = _rows(store, projects, cancelled, time_from, time_to)
    rows = [r for r in source_rows if (r['label'] + '\n' + r['body']).strip()]
    eligible = {row['id']: row for row in rows}
    terms = kg._fts_query_terms([query])
    lexical = [(r['id'], float(r['rank'])) for r in store.search(terms, projects=projects,
                limit=candidate_limit, time_from=time_from, time_to=time_to, expand_korean=False)] if terms else []
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
    token_ids = {document_id for document_id, _ in lexical}
    literal_ids = set()
    plan = korean_literal_plan(terms)
    if plan:
        seen = {identity for identity, _ in lexical}
        for row in rows:
            _check(cancelled)
            if len(lexical) >= candidate_limit:
                break
            if row['id'] not in seen and korean_literal_matches(plan, row['label'], row['body']):
                lexical.append((row['id'], 0.0)); seen.add(row['id']); literal_ids.add(row['id'])
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
    from alden_retrieval_time import select_latest, latest_requested, observation_note, collection_time_evidence
    latest_query = latest_requested(query,time_from,time_to)
    body_preferred, body_coverage = set(), None
    # Explicit latest requests keep their publication selection and admission
    # intact; an archived body must not displace a newer title before that step.
    if plan and not latest_query and (mode != 'rrf' or kg._validated_rrf_weights(rrf_weights)[0] > 0):
        # Recall still uses OR, while grounding requires every original query
        # word in this body. Particle variants are alternatives within a word.
        words = list(dict.fromkeys(re.findall(r'[0-9a-z가-힣]+', query.casefold())))[:24]
        body_plans = [korean_literal_plan(kg._fts_query_terms([word])) for word in words]
        if body_plans and all(body_plans):
            ranked, body_preferred, body_coverage = _prefer_body_literal_evidence(
                store, eligible, ranked, body_plans, projects,
                candidate_limit * (2 if mode == 'rrf' else 1), cancelled)
    temporal_selection={'mode':'all','basis':'relevance'}
    temporal_excluded=set()
    if ranked and latest_query:
        temporal_records=[]
        with store.database() as source:
            for identity,_score in ranked:
                row=eligible[identity]
                metadata=json.loads(source.execute('SELECT metadata FROM versions WHERE id=?',(row['version'],)).fetchone()[0])
                aliases=metadata.get('aliases',[]);aliases=aliases if isinstance(aliases,list) else []
                marks=','.join('?' for _ in projects)
                publisher_scopes={}
                for target_id,label in source.execute('''SELECT DISTINCT t.id,t.label FROM targets t JOIN memberships m ON m.target_id=t.id
                    WHERE m.document_id=? AND m.availability='available' AND EXISTS(SELECT 1 FROM target_projects p
                    WHERE p.target_id=t.id AND p.permission!='denied' AND p.project IN ('''+marks+'))',(identity,*projects)):
                    publisher_scopes.setdefault(label,[]).append(target_id)
                conflict=source.execute('''SELECT r.source,r.target FROM relations r WHERE r.active=1 AND r.type IN ('conflicts','contradicts')
                    AND (r.source=? OR r.target=?) AND EXISTS(SELECT 1 FROM target_projects p
                    WHERE p.target_id=r.target_id AND p.permission!='denied' AND p.project IN ('''+marks+'))',(identity,identity,*projects))
                temporal_records.append({'id':identity,'name':row['label'],'aliases':aliases+list(publisher_scopes),'mention_scopes':publisher_scopes,
                    'has_conflict':any(a in eligible and b in eligible for a,b in conflict),
                    'observed_at':metadata.get('published_at'),'time_basis':'source_published_at',
                    'pinned':identity in (also or [])})
        ordered,temporal_selection=select_latest(query,temporal_records,needles=kg.query_haystacks(query,None),time_from=time_from,time_to=time_to)
        temporal_excluded={identity for identity,_score in ranked}-set(ordered)
        scores=dict(ranked);ranked=[(identity,scores[identity]) for identity in ordered]
    if also is not None:
        if not isinstance(also, (list, tuple)) or len(also) > 40 or any(not isinstance(i, str) for i in also):
            raise ValueError('collection_explicit_ids_invalid')
        pinned = list(dict.fromkeys(i for i in also if i in eligible))
        ranked = [(i, 0.) for i in pinned] + [(i, s) for i, s in ranked if i not in pinned]
    facts, provenance, used = [], [], 0
    matching_text, repeated_facts, saved_characters = {}, 0, 0
    compact_text = not also and not latest_query and time_from is None and time_to is None
    def append(text, proof):
        nonlocal used
        remaining = max_context_chars - used
        if remaining <= 0:
            return False
        facts.append(text[:remaining]);proof['fact_truncated'] = len(text) > remaining
        provenance.append(proof);used += len(facts[-1])
        return True

    with store.database() as db:
        chosen = []
        for document_id, _score in ranked[:max(0, min(int(max_entities), 10))]:
            _check(cancelled)
            row = eligible[document_id]
            record, metadata, memberships = _verified_source(db, store, row, projects)
            source_projects = list(dict.fromkeys(m['project'] for m in memberships))
            proof = {'fact_type': 'entity', 'entity_id': document_id, 'note_id': document_id,
                     'source_kind': 'collected_source', 'source_version': row['version'],
                     'note_hash': row['raw_sha256'], 'original_id': row['original_id'],
                     'platform': row['platform'], 'projects': source_projects,
                     'requested_projects': projects, 'source_memberships': memberships,
                     'scope_basis': 'current_permitted_version_memberships_at_selected_snapshot',
                     'updated_at': row['collected_at'],
                     'body_source': row['body_source'],
                     'lexical_match': ('korean_literal_substring' if document_id in literal_ids else
                                       'bm25_token' if document_id in token_ids else None),
                     'derived_from': metadata.get('source', {}), 'truth_status': 'source_record; not independently verified',
                     'provenance_valid': True, 'retracted': False}
            evidence_class = content_evidence_class(metadata, row['body'], source=record,
                                                   body_source=row['body_source'])
            proof['content_evidence'] = {'policy': CONTENT_POLICY, 'classification': evidence_class}
            if row.get('text_projection') is not None:
                proof['text_projection'] = {k:v for k,v in row['text_projection'].items() if k != 'separated_tail'}
            if evidence_class == 'reference_only':
                proof['truth_status'] = 'link_reference; referenced destination body unverified'
            elif evidence_class == 'empty_body':
                proof['truth_status'] = 'title_or_label_reference; original body unavailable'
            elif evidence_class != 'content_candidate':
                proof['truth_status'] = 'source_description; original body unverified'
            if document_id in body_preferred:
                proof['selection_basis'] = 'matching_current_version_bound_body; source truth not independently verified'
            if isinstance(record, dict):
                proof['source_author'] = {key: record[key] for key in ['author', 'handle', 'date', 'url', 'localTextOrigin'] if isinstance(record.get(key), str)}
                if record.get('platform') == 'threads' and record.get('type') == 'source':
                    proof['source_annotations'] = declared_source_annotations(record, max_field_bytes=900)
            try:
                proof['time_evidence'] = collection_time_evidence(row['collected_at'], proof.get('source_author', {}).get('date'))
            except ValueError as error:
                raise RuntimeError('collection_version_integrity') from error
            text=(row['label'] + ': ' if row['label'] and row['body'] else row['label']) + row['body']
            if evidence_class == 'reference_only':
                text='[링크 참조 · 연결된 페이지 본문 미확인] '+text
            elif evidence_class == 'empty_body':
                text='[제목·이름 참조 · 본문 없음] '+text
            elif evidence_class != 'content_candidate':
                text='[자료 설명 · 원래 본문 미확인] '+text
            if temporal_selection['mode']=='latest_observation':
                proof['observation_time']=metadata.get('published_at');proof['observation_basis']='source_published_at'
                text=observation_note(metadata.get('published_at'),'source_published_at')+text
            match = _context_text_key(text, proof, record, metadata) if compact_text else None
            if match is not None and match in matching_text:
                representative = provenance[matching_text[match]]
                group = representative.setdefault('matching_sources', {
                    'schema': 1, 'policy': CONTEXT_TEXT_POLICY,
                    'scope': 'context_text_only; originals_not_merged', 'sources': [],
                    'annotation_differences': []})
                proof['fact_truncated'] = False
                group['sources'].append(proof)
                group['annotation_differences'] = [key for key in ANNOTATION_FIELDS
                    if any(representative['source_annotations']['fields'][key] !=
                           source['source_annotations']['fields'][key] for source in group['sources'])]
                repeated_facts += 1; saved_characters += len(text)
            elif append(text, proof) and match is not None and not proof['fact_truncated']:
                matching_text[match] = len(provenance) - 1
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
                if edge['target'] not in eligible or edge['target'] in temporal_excluded:
                    continue
                target = eligible[edge['target']]
                if edge['source_version'] != eligible[edge['source']]['version'] or edge['target_version'] != target['version']:
                    continue
                _verified_source(db, store, target, projects)
                require_relation_binding(db, edge)
                try:
                    declared_evidence = _strict_json(edge['evidence'])
                except (ValueError, TypeError, UnicodeError, RecursionError) as error:
                    raise RuntimeError('collection_relation_integrity') from error
                relation_memberships = [dict(r) for r in db.execute('''SELECT target_id,project,permission
                    FROM target_projects WHERE target_id=? AND permission!='denied'
                    AND project IN (''' + marks + ') ORDER BY project', (edge['target_id'], *projects))]
                if not relation_memberships or any(m['permission'] not in ('public','local-private') for m in relation_memberships):
                    raise RuntimeError('collection_source_scope_integrity')
                append(eligible[edge['source']]['label'] + ' — ' + edge['type'] + ' → ' + target['label'],
                       {'fact_type': 'relation', 'source_kind': 'collected_source', 'relation': edge['type'],
                        'source_id': edge['source'], 'target_id': edge['target'], 'relation_id': edge['id'],
                        'source_version': eligible[edge['source']]['version'], 'target_version': target['version'],
                        'projects': [m['project'] for m in relation_memberships],
                        'requested_projects': projects, 'source_memberships': [
                            {**m,'source_version':edge['source_version'],'target_version':edge['target_version']}
                            for m in relation_memberships],
                        'scope_basis': 'current_permitted_relation_memberships_at_selected_snapshot',
                        'source_evidence': declared_evidence,
                        'evidence_version': edge['version'],
                        'truth_status': 'declared stored relation; not independently verified',
                        'provenance_valid': True, 'retracted': False})
                if sum(p['fact_type'] == 'relation' for p in provenance) >= min(int(max_relations), 10):
                    break
    _check(cancelled)
    return {'facts': facts, 'fact_provenance': provenance,
            'search_mode': 'bm25_with_literals' if mode == 'bm25_only' and literal_ids else mode,
            'lexical_policy': 'token_then_korean_literal_substring_v1',
            'projects': projects, 'eligible_documents': len(rows), 'context_chars': used,
            'unsearchable_documents': len(source_rows) - len(rows),
            'pending_text_versions': sum(not r['text_ready'] for r in source_rows),
            'context_text_compaction': {'policy': CONTEXT_TEXT_POLICY, 'enabled': compact_text,
                'repeated_facts_removed': repeated_facts, 'saved_characters': saved_characters,
                'retained_sources': sum(p['fact_type'] == 'entity' for p in provenance) + repeated_facts,
                'additional_candidates_scanned': 0,
                'scope': 'selected_sources_only; original_ids_and_rank_order_unchanged'},
            'time_basis': 'collection_time',
            **({'ranking_policy':'current_version_bound_body_literal_first_v1','body_evidence_coverage':body_coverage}
               if body_coverage and body_coverage['matched'] else {}),
            'temporal_selection':temporal_selection,
            'candidates': [row[0] for row in ranked], 'canonical': 'independent originals; derived collection projection'}
