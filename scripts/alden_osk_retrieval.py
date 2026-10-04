"""Disposable retrieval projection of canonical OSK notes, never graph authority."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import alden_osk as osk

SCHEMA = 1
ENCODING = 'e5-char256-stride192-weighted-unit-pool-v1'
MAX_NOTES = 4096
EMBED_BATCH = 8  # The existing pinned E5 service's supported maximum.


def _snapshot(root: Path, cancelled=lambda: False):
    home = osk._home(root)
    checkpoint = osk._read_json(home / 'sync.json')
    contract, graph, secrets, _ = osk._load_engine(root, read_only=True)
    index = graph.Index()
    if index.scan_errors or index.broken or index.dup_ids or index.dup_stems:
        raise RuntimeError('canonical_inventory_unresolved')
    managed = {v['osk_id']: (k, v) for k, v in checkpoint.get('managed', {}).items()}
    docs = []
    for title, (path, kind) in index.nodes.items():
        if cancelled():
            raise RuntimeError('canonical_retrieval_cancelled')
        if kind[0] in ('governance', 'workbench', 'archive') or graph.is_hub(path):
            continue
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise RuntimeError('canonical_note_symlink')
        data = path.read_bytes()
        note = contract.parse_bytes(path, data)
        item = managed.get(note.id)
        if contract.validate(note) or item and (not item[1].get('active') or item[0].startswith('topic:')):
            continue
        source = item[1].get('source', {}) if item else {}
        evidence = source.get('evidence') or {}
        body = secrets.filter_text(osk._reference_body(note, item[1] if item else None, checkpoint, secrets))[0]
        referenced = replace(note, body=body)
        refs = note.meta.get('derived-from') or []
        refs = refs if isinstance(refs, list) else [refs]
        links = []
        for relation, targets in [('linked', referenced.wikilinks()), ('derived-from', note.edges('derived-from')), ('conflicts', note.edges('conflicts'))]:
            for target in targets:
                if index.resolve(target)[0] == 'node':
                    hit = index.locate(target)
                    links.append((relation, contract.parse(hit[0]).id))
        stamp = datetime.strptime(str(note.meta['updated']), '%Y-%m-%d %H:%M (KST)').replace(tzinfo=ZoneInfo('Asia/Seoul')).timestamp()
        docs.append({'id': 'osk:' + note.id, 'note_id': note.id, 'path': str(path.relative_to(home / 'vault')),
                     'hash': osk.digest(data), 'title': title, 'summary': str(note.meta.get('summary', '')),
                     'body': body, 'updated_at': int(stamp), 'space': str(path.parent.relative_to(home / 'vault')),
                     'derived_from': refs, 'conflicts': note.meta.get('conflicts') or [], 'links': links, 'source_identity': str(source.get('id') or ''),
                     'room_id': str(evidence.get('chat_id') or ''), 'room_ids': evidence.get('room_ids') or [],
                     'actor_id': str(evidence.get('author_id') or ''), 'source_event_ids': evidence.get('source_event_ids') or []})
        if len(docs) > MAX_NOTES:
            raise RuntimeError('canonical_inventory_exceeds_projection_budget')
    docs.sort(key=lambda d: d['id'])
    for d in docs:
        d['revision'] = osk.digest((ENCODING + '\0' + d['path'] + '\0' + d['hash'] + '\0' + d['body']).encode())
    signature = osk.digest(json.dumps([(d['id'], d['path'], d['hash']) for d in docs], separators=(',', ':')).encode())
    return docs, signature


def _connection(root: Path):
    home = osk._safe_directory(osk._home(root))
    path = home / 'retrieval.sqlite3'
    if path.is_symlink():
        raise RuntimeError('canonical_projection_symlink')
    connection = sqlite3.connect(path, timeout=.2)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,hash TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(id UNINDEXED,title,summary,body);
        CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY,hash TEXT,model TEXT,endpoint TEXT,vector TEXT);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
    ''')
    return connection


def _project(connection, docs, signature):
    stored = dict(connection.execute('SELECT id,hash FROM documents'))
    live = {d['id']: d for d in docs}
    with connection:
        for identity in stored.keys() - live.keys():
            connection.execute('DELETE FROM documents WHERE id=?', (identity,))
            connection.execute('DELETE FROM documents_fts WHERE id=?', (identity,))
            connection.execute('DELETE FROM vectors WHERE id=?', (identity,))
        for identity, d in live.items():
            if stored.get(identity) == d['revision']:
                continue
            connection.execute('DELETE FROM documents_fts WHERE id=?', (identity,))
            connection.execute('DELETE FROM vectors WHERE id=?', (identity,))
            connection.execute('INSERT OR REPLACE INTO documents VALUES(?,?)', (identity, d['revision']))
            connection.execute('INSERT INTO documents_fts VALUES(?,?,?,?)', (identity, d['title'], d['summary'], d['body']))
        connection.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('snapshot', signature))


def index_dense(root: Path, *, cancelled=lambda: False, embed=None) -> dict:
    """Called by the existing producer; no document embedding on a user query."""
    import auto_reply_knowledge_graph as kg
    docs, signature = _snapshot(root, cancelled)
    model, endpoint = kg._active_dense_embedding_model(), kg._dense_endpoint_identity()
    with closing(_connection(root)) as connection:
        _project(connection, docs, signature)
        saved = {row[0]: row[1:] for row in connection.execute('SELECT id,hash,model,endpoint FROM vectors')}
        pending = [d for d in docs if saved.get(d['id']) != (d['revision'], model, endpoint)]
        # Keep the whole note, rather than silently losing its later conditions.
        # Short overlapping character windows stay below the pinned service's
        # token limit for ordinary text; a service rejection remains pending.
        pieces, owners = [], []
        for d in pending:
            text = d['title'] + '\n' + d['summary'] + '\n' + d['body']
            for offset in range(0, len(text), 192):
                piece = text[offset:offset + 256]
                pieces.append(piece);owners.append((d['id'], len(piece)))
        pooled = {}
        for start in range(0, len(pieces), EMBED_BATCH):
            if cancelled():
                raise RuntimeError('canonical_retrieval_cancelled')
            texts = pieces[start:start + EMBED_BATCH]
            vectors = embed(texts) if embed else kg._local_dense_embeddings(texts, input_type='passage', model_id=model, state_root=root)
            if len(vectors) != len(texts):
                raise RuntimeError('canonical_embedding_count')
            for (identity, weight), vector in zip(owners[start:start + EMBED_BATCH], vectors):
                normalized = kg._normalize_dense_vector(list(vector))
                total = pooled.setdefault(identity, [0.] * len(normalized))
                if len(total) != len(normalized):
                    raise RuntimeError('canonical_vector_dimension')
                for i, value in enumerate(normalized): total[i] += weight * value
        with connection:
            for d in pending:
                normalized = kg._normalize_dense_vector(pooled[d['id']])
                connection.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?)',
                                   (d['id'], d['revision'], model, endpoint, json.dumps(normalized)))
        _, latest = _snapshot(root, cancelled)
        return {'state': 'ready' if latest == signature else 'pending', 'notes': len(docs),
                'embedded_changed': len(pending), 'embedded_windows': len(pieces), 'snapshot': signature}


def _eligible(d, chat_id, participant_id, time_from, time_to):
    import auto_reply_knowledge_graph as kg
    # An aggregate speaker note can mix rooms; original message retrieval owns
    # scoped speaker context. Unscoped manual notes cannot invent a room binding.
    if chat_id:
        if d['source_identity'].startswith('person:') or d['room_id'] != str(chat_id):
            return False
    if participant_id and d['actor_id'] != str(participant_id):
        return False
    lower, upper = kg._message_datetime_kst(time_from), kg._message_datetime_kst(time_to)
    return not (lower and d['updated_at'] < lower.timestamp() or upper and d['updated_at'] > upper.timestamp())


def retrieve(root: Path, query: str, *, chat_id=None, participant_id=None, time_from=None, time_to=None,
             max_entities=3, max_relations=3, candidate_limit=40, max_context_chars=8000,
             rrf_k=60, rrf_weights=None, also=None, cancelled=lambda: False, query_embed=None) -> dict:
    import auto_reply_knowledge_graph as kg
    if type(max_context_chars) is not int or not 256 <= max_context_chars <= 16000:
        raise ValueError('canonical_context_budget')
    docs, signature = _snapshot(root, cancelled)
    eligible = {d['id']: d for d in docs if _eligible(d, chat_id, participant_id, time_from, time_to)}
    with closing(_connection(root)) as connection:
        _project(connection, docs, signature)
        terms = kg._fts_query_terms([query])
        lexical = connection.execute('SELECT id,bm25(documents_fts,0,5,3,1) FROM documents_fts WHERE documents_fts MATCH ? ORDER BY 2,id', (terms,)).fetchall() if terms else []
        lexical = [(i, float(s)) for i, s in lexical if i in eligible][:candidate_limit]
        dense, mode = [], 'bm25_only'
        try:
            model, endpoint = kg._active_dense_embedding_model(), kg._dense_endpoint_identity()
            saved = {i: (h, m, e, v) for i, h, m, e, v in connection.execute('SELECT id,hash,model,endpoint,vector FROM vectors')}
            if eligible and all(saved.get(i, ())[:3] == (d['revision'], model, endpoint) for i, d in eligible.items()):
                vector = query_embed(query) if query_embed else kg._local_dense_embeddings([query], input_type='query', model_id=model, state_root=root)[0]
                vector = kg._normalize_dense_vector(list(vector))
                for identity in eligible:
                    passage = kg._normalize_dense_vector(json.loads(saved[identity][3]))
                    if len(vector) != len(passage):
                        raise RuntimeError('canonical_vector_dimension')
                    dense.append((identity, kg._vector_similarity(vector, passage)))
                dense.sort(key=lambda row: (-row[1], row[0]));dense = dense[:candidate_limit];mode = 'rrf'
        except Exception:
            if cancelled():
                raise RuntimeError('canonical_retrieval_cancelled')
            dense, mode = [], 'bm25_only'
    if cancelled():
        raise RuntimeError('canonical_retrieval_cancelled')
    ranked = kg._rrf_merge(lexical, dense, k=rrf_k, weights=rrf_weights,
                           recency={i: d['updated_at'] for i, d in eligible.items()}) if mode == 'rrf' else lexical
    explicit = {str(value) for value in also or []}
    pinned = [d['id'] for d in docs if d['id'] in eligible and (d['id'] in explicit or d['source_identity'] in explicit)]
    ranked = [(identity, 0.) for identity in pinned] + [(i, s) for i, s in ranked if i not in pinned]
    selected = [eligible[i] for i, _ in ranked[:max(0, int(max_entities))]]
    facts, provenance, relations, relation_provenance = [], [], [], []
    remaining = max_context_chars
    for d in selected:
        fact = (d['title'] + ': ' + d['body'])[:remaining]
        if not fact:
            break
        remaining -= len(fact);facts.append(fact)
        provenance.append({'fact_type': 'entity', 'entity_id': d['id'], 'note_id': d['note_id'], 'note_hash': d['hash'],
                           'source_kind': 'canonical_osk_note', 'space': d['space'], 'room_id': d['room_id'],
                           'source_event_ids': d['source_event_ids'], 'derived_from': d['derived_from'], 'conflicts': d['conflicts'],
                           'updated_at': d['updated_at'], 'retracted': False, 'provenance_valid': True,
                           'fact_truncated': len(fact) < len(d['title'] + ': ' + d['body'])})
    used_docs = selected[:len(facts)]
    selected_ids = {d['note_id']: d for d in used_docs}
    for d in used_docs:
        for relation, target in d['links']:
            if target not in selected_ids or len(relations) >= max_relations or remaining <= 0:
                continue
            fact = (d['title'] + ' — ' + relation + ' → ' + selected_ids[target]['title'])[:remaining]
            remaining -= len(fact);relations.append(fact)
            relation_provenance.append({'fact_type': 'relation', 'source_id': d['id'], 'target_id': 'osk:' + target,
                                       'source_kind': 'canonical_osk_note', 'note_hash': d['hash'], 'relation': relation,
                                       'source_event_ids': [], 'retracted': False, 'provenance_valid': True})
    for d in used_docs:
        path = osk._home(root) / 'vault' / d['path']
        if cancelled():
            raise RuntimeError('canonical_retrieval_cancelled')
        if any(p.is_symlink() for p in [path, *path.parents]) or osk.digest(path.read_bytes()) != d['hash']:
            raise RuntimeError('canonical_retrieval_changed_during_query')
    return {'query': query, 'facts': facts + relations, 'fact_count': len(facts) + len(relations),
            'fact_provenance': provenance + relation_provenance, 'candidate_provenance': provenance,
            'relation_provenance': relation_provenance, 'entities_count': len(facts), 'relations_count': len(relations),
            'candidate_count': len(facts), 'retrieved_candidate_count': len(ranked), 'focus_node_id': used_docs[0]['id'] if used_docs else '',
            'focus_k': 0, 'focus_node_count': len(used_docs), 'focus_edge_count': len(relations),
            'search_mode': mode, 'index_version': 'canonical-osk-1', 'watermark': signature,
            'evidence_ids': list(dict.fromkeys(ref for d in used_docs for ref in d['derived_from'])),
            'retrieval_policy': {'rrf_k': rrf_k, 'rrf_weights': list(kg._validated_rrf_weights(rrf_weights)),
                                 'candidate_limit': candidate_limit, 'max_context_chars': max_context_chars,
                                 'time_basis': 'canonical_note_updated'}, 'authority': 'OSK canonical notes'}
