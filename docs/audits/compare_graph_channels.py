"""Compare actual channel checkpoints using the previously frozen CPM setting.

No new parameter selection, production state, models or UI. Both conflicting
planted labels are reported; neither is an independently judged semantic gold.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import statistics

import igraph as ig
from compare_graph_communities import adjusted_rand, evaluate, partition, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    selection = json.loads(args.selection.read_text())
    versions = {name: importlib.metadata.version(name) for name in ('igraph', 'leidenalg')}
    reference = Path(__file__).with_name('compare_graph_communities.py')
    assert selection['versions'] == versions and selection['script_sha256'] == sha(reference)
    assert selection['phase'] == 'dev' and selection['gamma'] > 0
    expected = {(n, case, channel, seed)
                for n in data['seedPolicy']['sizes'] for case in data['seedPolicy']['cases']
                for channel in data['seedPolicy']['channels']
                for seed in range(data['seedPolicy']['start'], data['seedPolicy']['start'] + data['seedPolicy']['count'])}
    assert len(data['rows']) == len(expected)
    assert {(r['n'], r['scenario'], r['channel'], r['seed']) for r in data['rows']} == expected
    results = []
    fingerprints = []
    for row in data['rows']:
        # Several ablations intentionally reuse one input graph. Count those
        # executions separately, without treating them as independent samples.
        fingerprint = hashlib.sha256(json.dumps({'n': row['n'], 'relations': row['rawRelations'],
            'affinities': row['rawAffinities']}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        fingerprints.append(fingerprint)
        ids = row['ids']; index = {id: i for i, id in enumerate(ids)}
        assert len(ids) == row['n'] == len(index)
        for field, metric in (('renamedMembership', 'relabelPartitionARI'),
                              ('changedMembership', 'bridgeARI'), ('restoredMembership', 'restoredARI')):
            assert len(row[field]) == len(ids)
            assert abs(adjusted_rand(row['membership'], row[field]) - row[metric]) < 1e-12
        pairs = row['communityEdges']
        endpoints = [(index[p['source']], index[p['target']]) for p in pairs]
        assert all(a != b for a, b in endpoints)
        assert len({tuple(sorted(pair)) for pair in endpoints}) == len(pairs)
        graph = ig.Graph(n=len(ids), edges=endpoints, directed=False)
        graph.es['weight'] = [p['weight'] for p in pairs]
        for method in ('current', 'components', 'leiden-global-modularity', 'leiden-component-modularity', 'leiden-cpm'):
            labels = row['membership'] if method == 'current' else partition(graph, method, selection['gamma'] if method == 'leiden-cpm' else None)
            results.append({'n': row['n'], 'scenario': row['scenario'], 'channel': row['channel'], 'seed': row['seed'], 'method': method,
                            'input_fingerprint': fingerprint,
                            'structure': evaluate(graph, row['structureTruth'], labels),
                            'affinity': evaluate(graph, row['affinityTruth'], labels),
                            'membership': labels})
    summary = []
    for n, case, channel, method in sorted({(r['n'], r['scenario'], r['channel'], r['method']) for r in results}):
        sample = [r for r in results if (r['n'], r['scenario'], r['channel'], r['method']) == (n, case, channel, method)]
        native = [r for r in data['rows'] if (r['n'], r['scenario'], r['channel']) == (n, case, channel)]
        summary.append({'n': n, 'scenario': case, 'channel': channel, 'method': method, 'samples': len(sample),
                        'structure_ari_median': statistics.median(r['structure']['ari'] for r in sample),
                        'affinity_ari_median': statistics.median(r['affinity']['ari'] for r in sample),
                        'disconnected': sum(r['structure']['disconnected'] for r in sample),
                        'communities_median': statistics.median(r['structure']['communities'] for r in sample),
                        'native_relabel_ari_median': statistics.median(r['relabelPartitionARI'] for r in native) if method == 'current' else None,
                        'native_bridge_ari_median': statistics.median(r['bridgeARI'] for r in native) if method == 'current' else None,
                        'native_restored_ari_median': statistics.median(r['restoredARI'] for r in native) if method == 'current' else None})
    report = {'schema': 1, 'input_sha256': sha(args.input), 'script_sha256': sha(__file__),
              'reference_script_sha256': sha(reference), 'frozen_selection_sha256': sha(args.selection),
              'frozen_selection': selection, 'versions': versions, 'sourceFiles': data['sourceFiles'],
              'executions': len(data['rows']), 'unique_input_graphs': len(set(fingerprints)),
              'generation_size_seed_families': len({(r['n'], r['seed']) for r in data['rows']}),
              'scope': data['scope'], 'limits': ['Synthetic affinities, no actual encoder or semantic human gold.',
                  'Repeated channel ablations share inputs; execution counts are not independent sample counts.',
                  'The three-edge perturbation introduces structural edges even into affinity-only baselines.',
                  'Previously structural-dev CPM gamma is transferred without retuning; this is not a new holdout claim.',
                  'Exact admitted pair weights; degree gate reconstructed for complete uniform positive relation fixtures.',
                  'Different objectives and compute budgets; no product algorithm promotion or performance comparison.'],
              'summary': summary, 'results': results}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'fixtures': len(data['rows']), 'comparisons': len(results), 'frozen_gamma': selection['gamma'],
                      'array_permutations': all(r['permutationEqual'] for r in data['rows']),
                      'pinned_coordinates': all(r['pinPreserved'] for r in data['rows'])}))


if __name__ == '__main__':
    main()
