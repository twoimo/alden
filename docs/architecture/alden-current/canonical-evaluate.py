#!/usr/bin/env python3
"""Reproduce fixed source-note checks; no tuning, training or original DB read."""
import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scripts-dir', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--judgments', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.scripts_dir.resolve(strict=True)))
    import alden_osk_retrieval as retrieval
    import auto_reply_knowledge_graph as kg
    from alden_abort import AbortController

    raw = args.judgments.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError('judgments exceed the bounded file budget')
    data = json.loads(raw)
    if not 1 <= len(data['queries']) <= 64:
        raise ValueError('query count outside 1..64')
    docs, signature = retrieval._snapshot(args.state_root)
    if signature != data['canonical_snapshot']:
        raise RuntimeError('canonical snapshot differs from fixed judgments')
    hashes = {d['id']: d['hash'] for d in docs}
    token = AbortController(args.state_root).token()
    rows = []
    for query in data['queries']:
        relevant = query['relevant']
        if not relevant or any(hashes.get(identity) != query['expected_note_hash'] for identity in relevant):
            raise RuntimeError('expected source hash is unavailable')
        started = time.perf_counter()
        with kg.embedding_abort_scope(token):
            bundle = kg.retrieve_knowledge_bundle(query['text'], state_root=args.state_root, **data['policy'])
        if bundle['search_mode'] != 'rrf':
            raise RuntimeError('local dense retrieval is not ready; RRF check incomplete')
        identities = [p['entity_id'] for p in bundle['candidate_provenance']]
        if len(bundle['facts']) != len(bundle['fact_provenance']) or any(
                p['note_hash'] != hashes[p['entity_id']] for p in bundle['candidate_provenance']):
            raise RuntimeError('returned provenance differs from canonical sources')
        metrics = {}
        for k in (1, 3, 5):
            hits = identities[:k]
            gain = sum((2 ** relevant.get(identity, 0) - 1) / math.log2(rank + 2)
                       for rank, identity in enumerate(hits))
            ideal = sum((2 ** grade - 1) / math.log2(rank + 2)
                        for rank, grade in enumerate(sorted(relevant.values(), reverse=True)[:k]))
            metrics[str(k)] = {'recall': len(set(hits) & relevant.keys()) / len(relevant), 'ndcg': gain / ideal}
        rows.append({'id': query['id'], 'split': query['split'], 'ranked_note_ids': identities,
                     'search_mode': bundle['search_mode'], 'seconds': time.perf_counter() - started, 'metrics': metrics})
    latest, after = retrieval._snapshot(args.state_root)
    if after != signature or hashes != {d['id']: d['hash'] for d in latest}:
        raise RuntimeError('canonical sources changed during evaluation')
    summary = {}
    for split in ('dev', 'heldout', 'all'):
        group = [row for row in rows if split == 'all' or row['split'] == split]
        if group:
            summary[split] = {'n': len(group), 'median_seconds': statistics.median(row['seconds'] for row in group),
                              'metrics': {str(k): {'macro_recall': statistics.mean(row['metrics'][str(k)]['recall'] for row in group),
                                                   'macro_ndcg': statistics.mean(row['metrics'][str(k)]['ndcg'] for row in group)} for k in (1, 3, 5)}}
    report = {'schema_version': 1, 'scope': data['scope'], 'judgments_sha256': hashlib.sha256(raw).hexdigest(),
              'canonical_snapshot': signature, 'canonical_notes': len(docs), 'policy': data['policy'],
              'policy_tuned': False, 'source_hash_preserved': True, 'summary': summary, 'queries': rows,
              'retrieval_module_sha256': hashlib.sha256(Path(retrieval.__file__).read_bytes()).hexdigest()}
    with args.output.open('x') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
