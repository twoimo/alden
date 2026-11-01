"""Verify the reversible candidate against the same actual engine inputs.

No tuning, product migration, model call or source-store access. iGraph computes
component modularity independently; Fraction ARI checks the TypeScript metrics.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

import igraph as ig
from compare_graph_communities import adjusted_rand, evaluate, sha


def verify_build(path, expected, root):
    receipt = json.loads((path.parent / 'build.json').read_text())
    assert receipt['phase'] == 'frozen-before-evaluation' and receipt['mode'] == expected
    assert sha(path.parent / 'channel-audit.mjs') == receipt['bundleSha256']
    for source, digest in receipt['sourceFiles'].items():
        assert sha(root / source) == digest, source
    assert receipt['originalEngineSha256'] == sha(root / 'desktop/src/knowledge/semantic-layout.ts')
    if expected == 'candidate':
        assert receipt['executedEngineSha256'] == sha(path.parent / 'candidate-semantic-layout.ts')
    else:
        assert receipt['executedEngineSha256'] == receipt['originalEngineSha256']
    return receipt


def component_modularity(graph, labels):
    total = 0.
    for vertices in graph.connected_components():
        sub = graph.induced_subgraph(vertices)
        if not sub.ecount():
            continue
        mapping = {label: i for i, label in enumerate(dict.fromkeys(labels[v] for v in vertices))}
        total += sub.modularity([mapping[labels[v]] for v in vertices], weights='weight')
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert not args.output.exists(), 'Never overwrite a reviewed result'
    root = Path(__file__).resolve().parents[2]
    builds = [verify_build(path, name, root) for path, name in [(args.baseline, 'baseline'), (args.candidate, 'candidate')]]
    assert builds[0]['sourceFiles'] == builds[1]['sourceFiles']
    baseline, candidate = [json.loads(path.read_text()) for path in [args.baseline, args.candidate]]
    assert baseline['seedPolicy'] == candidate['seedPolicy'] and baseline['sourceFiles'] == candidate['sourceFiles']
    policy = baseline['seedPolicy']
    expected = {(n, case, channel, seed) for n in policy['sizes'] for case in policy['cases']
                for channel in policy['channels'] for seed in range(policy['start'], policy['start'] + policy['count'])}
    identity = lambda row: (row['n'], row['scenario'], row['channel'], row['seed'])
    left, right = [{identity(row): row for row in data['rows']} for data in [baseline, candidate]]
    assert len(baseline['rows']) == len(candidate['rows']) == len(expected) == len(left) == len(right)
    assert set(left) == set(right) == expected
    results = []
    fingerprints = set()
    ari_checks = 0
    for key in sorted(expected):
        a, b = left[key], right[key]
        for field in ['ids', 'rawRelations', 'rawAffinities', 'addedBridges', 'communityEdges', 'structureTruth', 'affinityTruth']:
            assert a[field] == b[field], (key, field)
        ids = a['ids']; index = {id: i for i, id in enumerate(ids)}
        assert len(index) == len(ids) == a['n']
        edges = a['communityEdges']
        endpoints = [(index[p['source']], index[p['target']]) for p in edges]
        assert len({tuple(sorted(edge)) for edge in endpoints}) == len(endpoints)
        assert all(i != j for i, j in endpoints) and all(math.isfinite(p['weight']) and p['weight'] > 0 for p in edges)
        graph = ig.Graph(n=len(ids), edges=endpoints, directed=False)
        graph.es['weight'] = [p['weight'] for p in edges]
        fingerprint = hashlib.sha256(json.dumps({'n': a['n'], 'relations': a['rawRelations'], 'affinities': a['rawAffinities']}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        fingerprints.add(fingerprint)
        metrics = {}
        for name, row in [('baseline', a), ('candidate', b)]:
            assert row['permutationEqual'] and row['pinPreserved']
            d = row['diagnostics']
            assert not any(d[field] for field in ['inputTruncated', 'relationPairsDropped', 'affinityPairsDropped', 'ignoredRelations', 'rejectedAffinities'])
            for field, metric in [('renamedMembership', 'relabelPartitionARI'), ('changedMembership', 'bridgeARI'), ('restoredMembership', 'restoredARI')]:
                assert len(row[field]) == len(ids) and abs(adjusted_rand(row['membership'], row[field]) - row[metric]) < 1e-12
                ari_checks += 1
            metrics[name] = {'structure': evaluate(graph, row['structureTruth'], row['membership']),
                             'affinity': evaluate(graph, row['affinityTruth'], row['membership']),
                             'component_modularity': component_modularity(graph, row['membership']),
                             'rename_ari': row['relabelPartitionARI'], 'bridge_ari': row['bridgeARI'], 'restore_ari': row['restoredARI']}
        assert metrics['candidate']['structure']['disconnected'] == 0
        assert metrics['candidate']['component_modularity'] >= metrics['baseline']['component_modularity'] - 1e-10
        assert abs(metrics['candidate']['restore_ari'] - 1) < 1e-12
        for field in ['diagnostics', 'changedDiagnostics', 'restoredDiagnostics']:
            stats = b[field]['communityRefinement']
            assert 1 <= stats['passes'] <= 8 and stats['termination'] in ['stable', 'budget']
            assert stats['mergeObjectiveGain'] >= 0
            admitted_pairs = b[field]['relationPairs'] + b[field]['affinityPairs']
            assert stats['visits'] <= 2 * admitted_pairs * 9
        results.append({'n': key[0], 'scenario': key[1], 'channel': key[2], 'seed': key[3], 'input_fingerprint': fingerprint,
                        **metrics, 'refinement': b['diagnostics']['communityRefinement']})
    summary = []
    for n, case, channel in sorted({key[:3] for key in expected}):
        sample = [row for row in results if (row['n'], row['scenario'], row['channel']) == (n, case, channel)]
        summary.append({'n': n, 'scenario': case, 'channel': channel, 'samples': len(sample), **{name: {
            'structure_ari_median': statistics.median(row[name]['structure']['ari'] for row in sample),
            'affinity_ari_median': statistics.median(row[name]['affinity']['ari'] for row in sample),
            'communities_median': statistics.median(row[name]['structure']['communities'] for row in sample),
            'component_modularity_median': statistics.median(row[name]['component_modularity'] for row in sample),
            'rename_ari_median': statistics.median(row[name]['rename_ari'] for row in sample),
            'restore_ari_median': statistics.median(row[name]['restore_ari'] for row in sample),
        } for name in ['baseline', 'candidate']}})
    result = {'schema': 1, 'scope': 'New sizes/seeds of the same synthetic generator, not independent human semantic gold. No retuning or product promotion.',
              'input_hashes': {str(path): sha(path) for path in [args.baseline, args.candidate]}, 'builds': builds,
              'evaluator_sha256': sha(__file__), 'ari_helper_sha256': sha(Path(__file__).with_name('compare_graph_communities.py')),
              'igraph_version': ig.__version__, 'seed_policy': policy, 'executions_per_variant': len(results),
              'unique_input_graphs': len(fingerprints), 'size_seed_families': len({(key[0], key[3]) for key in expected}),
              'ari_crosschecks': ari_checks, 'candidate_disconnected': 0,
              'restored_partition_exact': True, 'component_objective_nondecrease': True,
              'budget_terminations': sum(row['refinement']['termination'] == 'budget' for row in results),
              'limits': ['No semantic correctness guarantee, encoder, source store or native/GPU measurement.',
                  'Node IDs still choose deterministic tie order; partition identity under renaming is measured, not guaranteed.',
                  'Fresh affected-component partitions remove cached membership hysteresis but can change visible layout when inputs change.',
                  'Eight disjoint aggregate passes may stop with positive merges remaining; no optimum/Leiden guarantee.',
                  'Component-local resolution retains the previous disconnected-network resolution tradeoff.',
                  'Baseline/candidate timings are not a matched performance benchmark.'],
              'summary': summary, 'results': results}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ['executions_per_variant', 'unique_input_graphs', 'size_seed_families', 'ari_crosschecks', 'candidate_disconnected', 'budget_terminations', 'restored_partition_exact', 'component_objective_nondecrease']}))


if __name__ == '__main__':
    main()
