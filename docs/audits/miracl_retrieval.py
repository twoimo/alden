#!/usr/bin/env python3
"""Public Korean judged-passage evaluation through installed Alden, offline store.

No product writes, provider/model changes, service starts or generated gold.
Prepare freezes all inputs. Each explicit embedding shard retains the installed
64-request/8-input limits; checkpointed real vectors feed the actual producer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import sys
import time

from embedding_shadow import MODEL, URL, SCRIPTS, listener, require_warm_status, windows, dot

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = Path('/Users/twoimo/Library/Application Support/openkakao/bujamentor')
PROJECT = 'miracl-ko-dev-audit'
SOURCE_FILES = ('auto_reply_knowledge_graph.py', 'alden_collection.py',
                'alden_collection_retrieval.py', 'alden_abort.py',
                'alden_collection_scheduler.py', 'alden_local_embedding_server.py')
sha = lambda raw: hashlib.sha256(raw).hexdigest()

def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)

def modules():
    sys.path.insert(0, str(SCRIPTS))
    import auto_reply_knowledge_graph as kg
    import alden_collection_retrieval as retrieval
    from alden_collection import CollectionStore, identity
    from alden_collection_scheduler import CollectionScheduler
    from alden_abort import AbortToken, ABORT_STATE_NAME
    return kg, retrieval, CollectionStore, identity, CollectionScheduler(PRODUCT), AbortToken(PRODUCT / ABORT_STATE_NAME)

def guard(kg, scheduler, token, pid):
    token.raise_if_cancelled()
    if scheduler._voice_busy():
        raise RuntimeError('Foreground voice is active; retain checkpoints and defer audit')
    if listener() != [pid] or kg._active_dense_embedding_model() != MODEL or kg.DENSE_EMBEDDING_URL != URL:
        raise RuntimeError('Existing model/listener changed; never substitute or restart')

def prepare(out, source):
    if (out / 'protocol.json').exists():
        raise RuntimeError('Already prepared; read saved protocol rather than overwrite')
    selection = json.loads((source / 'selection.json').read_text())
    raw = (source / 'selected-corpus.jsonl').read_bytes()
    if sha(raw) != selection['selected_corpus_sha256']:
        raise RuntimeError('Selected public corpus changed')
    for name, expected in selection['source_files_sha256'].items():
        if sha((source / name).read_bytes()) != expected:
            raise RuntimeError('Public topics/qrels/tree changed')
    docs = [json.loads(line) for line in raw.splitlines()]
    assert len(docs) == selection['selected_documents'] == 2835
    kg, retrieval, Store, identity, scheduler, token = modules()
    token.raise_if_cancelled()
    state = out / 'isolated-store'
    store = Store(state)
    target = store.register(platform='graph', original_id='miracl-ko-dev', kind='source',
                            label='Public MIRACL Korean judged-passage audit', projects=[PROJECT], enabled=True)
    for start in range(0, len(docs), 128):
        store.ingest(target, [{'platform': 'graph', 'original_id': d['docid'],
                               'label': d['title'], 'text': d['text'],
                               'dataset': 'miracl/miracl-corpus', 'dataset_revision': selection['corpus_revision']}
                              for d in docs[start:start + 128]])
    with store.database() as db:
        rows = {r['original_id']: dict(r) for r in db.execute('SELECT d.id,d.original_id,v.id AS version,v.label,v.body,v.raw_sha256 FROM documents d JOIN versions v ON v.id=d.current_version')}
    assert set(rows) == {d['docid'] for d in docs}
    passages = sorted({piece for row in rows.values() for piece in windows(row['label'] + '\n' + row['body'])})
    queries = selection['queries']
    shards = [{'role': 'query', 'texts': sorted(set(queries.values()))}]
    shards += [{'role': 'passage', 'texts': passages[start:start + 512]} for start in range(0, len(passages), 512)]
    assert all(0 < len(s['texts']) <= 512 and all(t.strip() for t in s['texts']) for s in shards)
    inputs = {'queries': queries, 'qrels': selection['qrels'], 'documents': rows, 'shards': shards,
              'target_id': target, 'source_selection': selection}
    dump(out / 'inputs.json', inputs)
    protocol = {'schema': 1, 'script_sha256': sha(Path(__file__).read_bytes()),
                'helper_sha256': sha(Path(__file__).with_name('embedding_shadow.py').read_bytes()),
                'installed_sha256': {name: sha((SCRIPTS / name).read_bytes()) for name in SOURCE_FILES},
                'inputs_sha256': sha((out / 'inputs.json').read_bytes()), 'model': MODEL, 'endpoint': URL,
                'queries': len(queries), 'documents': len(rows), 'judgments': selection['judgment_count'],
                'unique_inputs': sum(len(s['texts']) for s in shards), 'shards': len(shards),
                'planned_requests': sum((len(s['texts']) + 7) // 8 for s in shards),
                'max_requests_per_shard': 64, 'max_batch_inputs': 8, 'shard_wall_seconds': 120,
                'global_request_cap': 768, 'global_input_cap': 6144,
                'methods': ['bm25_token', 'dense_pooled', 'rrf_raw', 'product_rrf_consumer'],
                'candidate_limit': 40, 'rrf_k': 60, 'rrf_weights': [1, 1], 'max_entities': 10,
                'context_chars': 16000, 'metrics_at': [1, 3, 10],
                'selection': 'All213 official Korean dev queries and all3057 labels; no tuning or score-based selection',
                'limits': ['2835 judged-passage union, not full1486752 passage retrieval',
                           'Unjudged passages are unknown; report exposure separately',
                           'No personal-source, identity/claim, generated-answer or graph-layout quality claim',
                           'Existing warm E5; no alternative encoder or matched performance comparison',
                           'No explicit relation benchmark: do not fabricate graph edges'],
                'prepared_at': time.time()}
    assert protocol['planned_requests'] <= 768 and protocol['unique_inputs'] <= 6144
    dump(out / 'protocol.json', protocol)
    print(json.dumps({k: protocol[k] for k in ('queries', 'documents', 'judgments', 'unique_inputs', 'shards', 'planned_requests')}))

def frozen(out):
    protocol = json.loads((out / 'protocol.json').read_text())
    assert sha((out / 'inputs.json').read_bytes()) == protocol['inputs_sha256']
    assert sha(Path(__file__).read_bytes()) == protocol['script_sha256'], 'Frozen script changed'
    assert sha(Path(__file__).with_name('embedding_shadow.py').read_bytes()) == protocol['helper_sha256'], 'Frozen helper changed'
    assert all(sha((SCRIPTS / name).read_bytes()) == value for name, value in protocol['installed_sha256'].items())
    return protocol, json.loads((out / 'inputs.json').read_text())

def embed(out, number):
    protocol, data = frozen(out)
    assert number is not None and 0 <= number < len(data['shards'])
    path = out / f'embedding-shard-{number:02d}.jsonl'
    assert not path.exists(), 'Preserve original shard; reconcile partial/complete checkpoint before retry'
    kg, retrieval, Store, identity, scheduler, token = modules()
    status = json.loads((out / 'manager-status.json').read_text())
    pid = status['pid']
    require_warm_status(status, listener())
    guard(kg, scheduler, token, pid)
    shard = data['shards'][number]
    budget = kg._DenseCycleBudget()
    budget.deadline = time.monotonic() + protocol['shard_wall_seconds']
    started = time.monotonic()
    with path.open('x') as handle, kg.embedding_abort_scope(token):
        path.chmod(0o600)
        handle.write(json.dumps({'header': {'shard': number, 'protocol_sha256': sha((out / 'protocol.json').read_bytes()), 'pid': pid, 'model': MODEL}}) + '\n')
        handle.flush()
        for offset in range(0, len(shard['texts']), 8):
            guard(kg, scheduler, token, pid)
            batch = shard['texts'][offset:offset + 8]
            values = kg._local_dense_embeddings(batch, input_type=shard['role'], model_id=MODEL,
                                                timeout_seconds=15, _budget=budget, state_root=out / 'isolated-store')
            assert len(values) == len(batch)
            assert all(len(v) == 384 and all(math.isfinite(x) for x in v) for v in values)
            handle.write(json.dumps({'offset': offset, 'role': shard['role'], 'input_sha256': [sha(t.encode()) for t in batch],
                                     'normalized_input_sha256': [sha(t.strip().encode()) for t in batch], 'vectors': values}) + '\n')
            handle.flush()
        guard(kg, scheduler, token, pid)
        handle.write(json.dumps({'complete': True, 'requests': budget.requests, 'inputs': len(shard['texts']), 'elapsed_seconds': time.monotonic() - started}) + '\n')
    print(json.dumps({'shard': number, 'requests': budget.requests, 'inputs': len(shard['texts']), 'complete': True}), flush=True)

def metrics(ranking, judgments, k):
    relevant = {d for d, value in judgments.items() if value == 1}
    values = [int(d in relevant) for d in ranking[:k]]
    dcg = sum(value / math.log2(i + 2) for i, value in enumerate(values))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return {'recall': sum(values) / len(relevant), 'ndcg': dcg / ideal,
            'mrr': next((1 / (i + 1) for i, value in enumerate(values) if value), 0),
            'unjudged': sum(d not in judgments for d in ranking[:k]), 'returned': len(values)}

def evaluate(out):
    assert not (out / 'results.json').exists(), 'Do not repeat completed evaluation'
    assert not (out / 'queries.jsonl').exists(), 'Existing query checkpoint; reconcile original run before replay'
    protocol, data = frozen(out)
    kg, retrieval, Store, identity, scheduler, token = modules()
    pid = json.loads((out / 'manager-status.json').read_text())['pid']
    cache = {}
    requests = 0
    for number, shard in enumerate(data['shards']):
        rows = [json.loads(l) for l in (out / f'embedding-shard-{number:02d}.jsonl').read_text().splitlines()]
        assert rows[0]['header'] == {'shard': number, 'protocol_sha256': sha((out / 'protocol.json').read_bytes()), 'pid': pid, 'model': MODEL}
        assert rows[-1]['complete'] and rows[-1]['requests'] <= 64
        requests += rows[-1]['requests']
        for batch in rows[1:-1]:
            expected = shard['texts'][batch['offset']:batch['offset'] + 8]
            assert batch['role'] == shard['role'] and batch['input_sha256'] == [sha(t.encode()) for t in expected]
            assert len(batch['vectors']) == len(expected)
            for text, vector in zip(expected, batch['vectors']):
                assert len(vector) == 384 and all(math.isfinite(x) for x in vector)
                cache[(shard['role'], text)] = vector
    assert len(cache) == protocol['unique_inputs'] and requests == protocol['planned_requests']
    guard(kg, scheduler, token, pid)
    state = out / 'isolated-store'
    store = Store.open_existing(state)
    producer = retrieval.index_dense(state, [PROJECT], embed=lambda texts: [cache[('passage', text)] for text in texts], cancelled=token.is_cancelled)
    assert producer['state'] == 'ready' and producer['documents'] == len(data['documents'])
    pooled = {}
    with sqlite3.connect(state / 'knowledge/collection/retrieval.sqlite3') as db:
        for docid, row in data['documents'].items():
            total = [0.] * 384
            for piece in windows(row['label'] + '\n' + row['body']):
                vector = kg._normalize_dense_vector(cache[('passage', piece)])
                for i, value in enumerate(vector): total[i] += len(piece) * value
            vector = kg._normalize_dense_vector(total)
            raw, = db.execute('SELECT vector FROM vectors WHERE document_id=? AND version=?', (row['id'], row['version'])).fetchone()
            assert raw == struct.pack('<384f', *vector)
            pooled[docid] = retrieval._vector(raw)
    reverse = {row['id']: docid for docid, row in data['documents'].items()}
    results = []
    started = time.monotonic()
    product_mode = {}
    for index, (qid, query) in enumerate(data['queries'].items()):
        guard(kg, scheduler, token, pid)
        terms = kg._fts_query_terms([query])
        lexical = [(row['id'], float(row['rank'])) for row in store.search(terms, projects=[PROJECT], limit=40, expand_korean=False)] if terms else []
        q = kg._normalize_dense_vector(cache[('query', query)])
        dense = sorted(((data['documents'][docid]['id'], dot(q, vector)) for docid, vector in pooled.items()), key=lambda item: (-item[1], item[0]))[:40]
        fused = kg._rrf_merge(lexical, dense, k=60, weights=[1, 1])
        product = retrieval.retrieve(state, query, projects=[PROJECT], max_entities=10, max_relations=0,
                                     candidate_limit=40, max_context_chars=16000,
                                     query_embed=lambda _: cache[('query', query)], cancelled=token.is_cancelled)
        assert product['search_mode'] == 'rrf', 'Do not label a fallback as dense/RRF success'
        product_mode[product['search_mode']] = product_mode.get(product['search_mode'], 0) + 1
        ranks = {'bm25_token': [reverse[i] for i, _ in lexical], 'dense_pooled': [reverse[i] for i, _ in dense],
                 'rrf_raw': [reverse[i] for i, _ in fused],
                 'product_rrf_consumer': [reverse[p['entity_id']] for p in product['fact_provenance'] if p['fact_type'] == 'entity']}
        assert all(len(set(ids)) == len(ids) for ids in ranks.values())
        judgments = data['qrels'][qid]
        result = {'qid': qid, 'query_sha256': sha(query.encode()), 'rankings': ranks,
                  'metrics': {method: {str(k): metrics(ids, judgments, k) for k in (1, 3, 10)} for method, ids in ranks.items()},
                  'product_candidates': [reverse[i] for i in product['candidates']],
                  'product_context_chars': product['context_chars'], 'product_eligible': product['eligible_documents'],
                  'product_ranking_policy': product.get('ranking_policy'), 'body_coverage': product.get('body_evidence_coverage'),
                  'product_projection_pending': product['pending_text_versions']}
        with (out / 'queries.jsonl').open('a') as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + '\n')
        results.append(result)
        if (index + 1) % 20 == 0: print(json.dumps({'queries_complete': index + 1}), flush=True)
    summary = {method: {str(k): {metric: sum(r['metrics'][method][str(k)][metric] for r in results) / len(results)
                                for metric in ('recall', 'ndcg', 'mrr', 'unjudged', 'returned')}
                        for k in (1, 3, 10)} for method in protocol['methods']}
    for method in protocol['methods']:
        with (out / (method + '.trec')).open('x') as handle:
            for result in results:
                for rank, docid in enumerate(result['rankings'][method][:10], 1):
                    handle.write(f"{result['qid']} Q0 {docid} {rank} {11-rank} {method}\n")
    Store(state).register(platform='graph', original_id='miracl-ko-dev', kind='source',
                   label='Public MIRACL Korean judged-passage audit', projects=[PROJECT], enabled=False)
    guard(kg, scheduler, token, pid)
    dump(out / 'results.json', {'protocol': protocol, 'summary': summary, 'queries_evaluated': len(results),
                              'producer': producer, 'producer_float32_vectors_exact_match': len(pooled),
                              'real_embedding_requests': requests, 'real_embedding_inputs': len(cache),
                              'query_embedding_replays': len(results), 'new_embedding_calls_during_evaluation': 0,
                              'warm_pid': pid, 'product_modes': product_mode, 'evaluation_seconds': time.monotonic() - started,
                              'isolated_target_paused': True, 'scope': protocol['limits']})
    print(json.dumps({'queries': len(results), 'summary_at10': {m: v['10'] for m, v in summary.items()}}), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=['prepare', 'embed', 'evaluate'])
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--dataset-dir', type=Path)
    parser.add_argument('--shard', type=int)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    assert out.is_relative_to(ROOT / 'outputs'), 'Write only to this repository audit outputs'
    out.mkdir(parents=True, mode=0o700, exist_ok=True)
    if args.phase == 'prepare':
        assert args.dataset_dir is not None
        prepare(out, args.dataset_dir.resolve())
    elif args.phase == 'embed': embed(out, args.shard)
    else: evaluate(out)

if __name__ == '__main__': main()
