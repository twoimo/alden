#!/usr/bin/env python3
"""Trace frozen public MIRACL ranks through installed retrieval, without encoding.

Read-only inputs and collection databases; wrappers observe this process only.
Existing results are evidence to reconcile, never overwritten or used for tuning.
"""
import argparse
import json
from pathlib import Path
import socket
import sys

import miracl_retrieval as baseline


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fingerprint(root):
    return {str(p.relative_to(root)): baseline.sha(p.read_bytes())
            for p in sorted(root.rglob('*')) if p.is_file()}


def run(source, output):
    source, output = source.resolve(), output.resolve()
    require(source.is_relative_to(baseline.ROOT / 'outputs'), 'Baseline outside audit outputs')
    require(output.is_relative_to(baseline.ROOT / 'outputs'), 'Output outside audit outputs')
    require(not output.is_relative_to(source), 'Keep trace outside frozen baseline')
    require(not output.exists(), 'Preserve existing output; use a new directory')
    require(sys.dont_write_bytecode, 'Run with -B before importing signed Resources')
    protocol = json.loads((source / 'protocol.json').read_text())
    data = json.loads((source / 'inputs.json').read_text())
    require(baseline.sha((source / 'inputs.json').read_bytes()) == protocol['inputs_sha256'], 'Inputs changed')
    for name, digest in protocol['installed_sha256'].items():
        require(baseline.sha((baseline.SCRIPTS / name).read_bytes()) == digest, 'Installed source changed: ' + name)
    previous = [json.loads(line) for line in (source / 'queries.jsonl').read_text().splitlines()]
    require(len(previous) == len(data['queries']) == 213, 'Incomplete original query receipts')
    require([r['qid'] for r in previous] == list(data['queries']), 'Duplicate or reordered query receipts')
    batches = [json.loads(line) for line in (source / 'embedding-shard-00.jsonl').read_text().splitlines()]
    require(batches[0]['header']['protocol_sha256'] == baseline.sha((source / 'protocol.json').read_bytes()), 'Vector protocol changed')
    require(batches[0]['header']['model'] == protocol['model'] and batches[-1]['complete'], 'Incomplete query vectors')
    texts = data['shards'][0]['texts']
    cache = {}
    for batch in batches[1:-1]:
        selected = texts[batch['offset']:batch['offset'] + 8]
        require(batch['role'] == 'query' and batch['input_sha256'] == [baseline.sha(t.encode()) for t in selected], 'Query vector binding changed')
        require(len(selected) == len(batch['vectors']), 'Vector count changed')
        cache.update(zip(selected, batch['vectors']))
    require(len(cache) == len(texts), 'Missing query vectors')
    sys.path.insert(0, str(baseline.SCRIPTS))
    import auto_reply_knowledge_graph as kg
    import alden_collection_retrieval as retrieval
    observed_model = kg._active_dense_embedding_model()
    require(observed_model == protocol['model'] and kg.DENSE_EMBEDDING_URL == protocol['endpoint'], 'Live model identity differs; never substitute')

    state = source / 'isolated-store'
    before = fingerprint(source)
    reverse = {row['id']: docid for docid, row in data['documents'].items()}
    convert = lambda ranked: [reverse[i] for i, _ in ranked]
    ids = {docid: row['id'] for docid, row in data['documents'].items()}
    original_merge, original_body = kg._rrf_merge, retrieval._prefer_body_literal_evidence
    original_connect, original_embed = socket.socket.connect, kg._local_dense_embeddings
    original_model = kg._active_dense_embedding_model
    current = {}
    denied_calls = []

    def denied(*_args, **_kwargs):
        denied_calls.append(True)
        raise RuntimeError('Offline audit forbids new network/embedding calls')

    def merge(lexical, dense, **kwargs):
        require('lexical' not in current, 'Unexpected duplicate fusion')
        result = original_merge(lexical, dense, **kwargs)
        current.update(lexical=list(lexical), dense=list(dense), fusion=result,
                       recency=dict(kwargs.get('recency') or {}), kwargs=kwargs)
        return result

    def body(*args, **kwargs):
        require('body' not in current, 'Unexpected duplicate body selection')
        result = original_body(*args, **kwargs)
        current['body'] = result[0]
        current['body_coverage'] = result[2]
        return result

    output.mkdir(mode=0o700)
    baseline.dump(output / 'protocol.json', {
        'schema': 1, 'baseline_protocol_sha256': baseline.sha((source / 'protocol.json').read_bytes()),
        'baseline_query_receipts_sha256': baseline.sha((source / 'queries.jsonl').read_bytes()),
        'script_sha256': baseline.sha(Path(__file__).read_bytes()),
        'baseline_files_sha256': before, 'installed_sha256': protocol['installed_sha256'],
        'initial_model_discovery': observed_model,
        'model_metadata_replay': 'Verified live identity frozen locally; no readiness claim during offline replay',
        'policy': 'Observe original installed pipeline; alternate tie orders are audit-only counterfactuals',
        'limits': protocol['limits'], 'new_embedding_request_budget': 0})
    results = []
    try:
        kg._rrf_merge, retrieval._prefer_body_literal_evidence = merge, body
        socket.socket.connect, kg._local_dense_embeddings = denied, denied
        kg._active_dense_embedding_model = lambda: observed_model
        with (output / 'queries.jsonl').open('x') as handle:
            for old in previous:
                current.clear()
                qid, query = old['qid'], data['queries'][old['qid']]
                require(old['query_sha256'] == baseline.sha(query.encode()), 'Original query changed')
                product = retrieval.retrieve(state, query, projects=[baseline.PROJECT],
                    max_entities=10, max_relations=0, candidate_limit=40,
                    max_context_chars=16000, query_embed=lambda text: cache[text])
                require(product['search_mode'] == 'rrf' and 'fusion' in current, 'Do not count a fallback as fusion')
                token = [(ids[i], 0.) for i in old['rankings']['bm25_token']]
                dense = current['dense']
                ranks = {
                    'bm25_token': old['rankings']['bm25_token'],
                    'dense_pooled': convert(dense),
                    'rrf_raw': convert(original_merge(token, dense, k=60, weights=[1, 1])),
                    'rrf_token_collection_tie': convert(original_merge(token, dense, k=60, weights=[1, 1], recency=current['recency'])),
                    'lexical_product': convert(current['lexical']),
                    'rrf_product_id_tie': convert(original_merge(current['lexical'], dense, k=60, weights=[1, 1])),
                    'rrf_product_collection_tie': convert(current['fusion']),
                    'post_body': convert(current.get('body', current['fusion'])),
                    'product_rrf_consumer': [reverse[p['entity_id']] for p in product['fact_provenance'] if p['fact_type'] == 'entity']}
                for method in protocol['methods']:
                    require(ranks[method] == old['rankings'][method], 'Frozen baseline diverged: ' + qid + '/' + method)
                require(ranks['post_body'][:10] == ranks['product_rrf_consumer'], 'Untraced consumer ordering: ' + qid)
                groups = {}
                for identity, score in current['fusion']:
                    groups.setdefault(score, []).append(reverse[identity])
                groups = [members for members in groups.values() if len(members) > 1]
                tied_positions = [i + 1 for i, (_identity, score) in enumerate(current['fusion'])
                                  if sum(value == score for _, value in current['fusion']) > 1]
                item = {'qid': qid, 'query_sha256': old['query_sha256'], 'rankings': ranks,
                    'ranking_sha256': {k: baseline.sha(json.dumps(v).encode()) for k, v in ranks.items()},
                    'metrics_at10': {k: baseline.metrics(v, data['qrels'][qid], 10) for k, v in ranks.items()},
                    'body_coverage': current.get('body_coverage'),
                    'exact_fusion_ties': groups, 'tied_positions': tied_positions,
                    'collection_time_is_ingestion_time': True,
                    'source_versions': {docid: data['documents'][docid]['version'] for docid in set(sum(ranks.values(), []))}}
                handle.write(json.dumps(item, ensure_ascii=False) + '\n')
                handle.flush()
                results.append(item)
                if len(results) % 25 == 0:
                    print(json.dumps({'queries_traced': len(results)}), flush=True)
    finally:
        kg._rrf_merge, retrieval._prefer_body_literal_evidence = original_merge, original_body
        socket.socket.connect, kg._local_dense_embeddings = original_connect, original_embed
        kg._active_dense_embedding_model = original_model
    require(not denied_calls, 'Unexpected attempted network/embedding call')
    require(fingerprint(source) == before, 'Frozen source artifacts changed')
    methods = list(results[0]['rankings'])
    summary = {m: {metric: sum(r['metrics_at10'][m][metric] for r in results) / len(results)
                   for metric in ('ndcg', 'recall', 'mrr', 'unjudged', 'returned')} for m in methods}
    adjacent = [('rrf_raw', 'rrf_token_collection_tie'), ('rrf_token_collection_tie', 'rrf_product_collection_tie'),
                ('rrf_product_id_tie', 'rrf_product_collection_tie'), ('rrf_product_collection_tie', 'post_body'),
                ('post_body', 'product_rrf_consumer')]
    deltas = []
    for a, b in adjacent:
        changed = [r['qid'] for r in results if r['rankings'][a][:10] != r['rankings'][b][:10]]
        deltas.append({'before': a, 'after': b, 'changed_top10_qids': changed,
                       'mean_ndcg_delta': summary[b]['ndcg'] - summary[a]['ndcg']})
    baseline.dump(output / 'results.json', {'queries': len(results), 'summary_at10': summary,
        'deltas': deltas, 'body_invocations': sum(r['body_coverage'] is not None for r in results),
        'body_reordered_qids': [r['qid'] for r in results if r['rankings']['rrf_product_collection_tie'] != r['rankings']['post_body']],
        'network_or_embedding_attempts': len(denied_calls), 'source_artifacts_unchanged': True,
        'all_four_original_rankings_exact': True,
        'scope': 'Post-hoc frozen-regression diagnosis; no new gold, tuning, runtime policy or speed claim'})
    print(json.dumps({'queries': len(results), 'summary_at10': summary, 'deltas': deltas}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    run(args.baseline_dir, args.output_dir)
