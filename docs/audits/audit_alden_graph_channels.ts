// Read-only channel ablation. Synthetic affinities are not encoder/human gold.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { SemanticLayoutEngine } from '../../desktop/src/knowledge/semantic-layout';
import { parseKnowledgeGraph } from '../../desktop/src/knowledge/graph-model';
import type { LayoutAffinityPair } from '../../desktop/src/knowledge/layout-affinity';

const integer = (name: string, fallback: number, maximum: number) => {
  const at = process.argv.indexOf(name), value = at < 0 ? fallback : Number(process.argv[at + 1]);
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) throw new Error('invalid ' + name);
  return value;
};
const start = integer('--seed-start', 201, 1_000_000), count = integer('--seed-count', 6, 12);
const sizesAt = process.argv.indexOf('--sizes');
const sizes = sizesAt < 0 ? [96, 192] : process.argv[sizesAt + 1]?.split(',').map(Number);
if (!sizes?.length || new Set(sizes).size !== sizes.length || sizes.some(n => !Number.isSafeInteger(n) || n < 8 || n > 2048 || n % 4)) throw new Error('invalid --sizes');
const choose2 = (n: number) => n * (n - 1) / 2;
function ari(a: string[], b: string[]): number {
  if (a.length !== b.length || a.length < 2) throw new Error('invalid partition size');
  const counts = (values: string[]) => {
    const result = new Map<string, number>();
    for (const value of values) result.set(value, (result.get(value) ?? 0) + 1);
    return [...result.values()].reduce((sum, n) => sum + choose2(n), 0);
  };
  const left = counts(a), right = counts(b), observed = counts(a.map((v, i) => JSON.stringify([v, b[i]])));
  const expected = left * right / choose2(a.length), upper = (left + right) / 2;
  return upper === expected ? 1 : (observed - expected) / (upper - expected);
}
if (Math.abs(ari(['a', 'a', 'b', 'b'], ['c', 'd', 'c', 'd']) + .5) > 1e-12) throw new Error('ARI calibration');
function random(seed: number) {
  let x = seed >>> 0;
  return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return (x >>> 0) / 4294967296; };
}
type Edge = { source: string; target: string; weight: number };
const key = (a: string, b: string) => JSON.stringify([a, b].sort());
const cases = ['aligned', 'opposed-topologies', 'hub-aligned', 'disconnected-aligned', 'sparse-aligned'] as const;
const rows = [];
for (const n of sizes) for (const scenario of cases) for (let seed = start; seed < start + count; seed++) {
  const rng = random(seed), width = n / 4;
  const ids = Array.from({ length: n }, (_, i) => 'channel:' + String(i).padStart(4, '0'));
  const structureTruth = ids.map((_, i) => Math.floor(i / width));
  const affinityTruth = ids.map((_, i) => scenario === 'opposed-topologies' ? i % 4 : structureTruth[i]);
  const nodes = ids.map(id => ({ id, label: 'Synthetic channel fixture', source_target: 'audit', source_version: 'v1' }));
  const relations: Edge[] = [];
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const chance = structureTruth[i] === structureTruth[j] ? scenario === 'sparse-aligned' ? .08 : .35
      : scenario === 'disconnected-aligned' ? 0 : .01;
    if (rng() < chance) relations.push({ source: ids[i], target: ids[j], weight: 1 });
  }
  if (scenario === 'hub-aligned') for (let i = 1; i < n; i++)
    if (!relations.some(e => e.source === ids[0] && e.target === ids[i])) relations.push({ source: ids[0], target: ids[i], weight: 1 });
  const affinities: LayoutAffinityPair[] = [], affinityKeys = new Set<string>();
  for (let group = 0; group < 4; group++) {
    const members = ids.filter((_, i) => affinityTruth[i] === group);
    for (let i = 0; i < members.length; i++) for (const step of [1, 2]) {
      const source = members[i], target = members[(i + step) % members.length], pair = key(source, target);
      if (!affinityKeys.has(pair)) { affinityKeys.add(pair); affinities.push({ source, target, cosine: .76 + .20 * rng() }); }
    }
  }
  for (const channel of ['structure', 'affinity', 'mixed']) {
    const edges = channel === 'affinity' ? [] : relations, input = channel === 'structure' ? [] : affinities;
    const view = { ...parseKnowledgeGraph({ nodes, edges }), focusId: null, hops: 0 };
    const engine = new SemanticLayoutEngine(), result = engine.layout(view, new Map(), input, { nowMs: 1000 });
    if (result.diagnostics.inputTruncated || result.diagnostics.relationPairsDropped || result.diagnostics.affinityPairsDropped
      || result.diagnostics.ignoredRelations || result.diagnostics.rejectedAffinities) throw new Error('incomplete fixture admission');
    const membership = ids.map(id => result.clusterIds.get(id)!);
    const rawDegree = new Map(ids.map(id => [id, 0]));
    for (const e of edges) for (const id of [e.source, e.target]) rawDegree.set(id, rawDegree.get(id)! + .5);
    // Reconstruct only the current documented degree gate. Pair weights and
    // channel admission come from the actual engine checkpoint, not a second
    // approximation of its normalization or semantic degree cap.
    const admitted = engine.checkpoint().pairs.map(([, pair]) => {
      const a = rawDegree.get(pair.source)!, b = rawDegree.get(pair.target)!;
      const similar = a + b === 0 ? 1 : Math.min(a, b) / Math.max(a, b);
      return { source: pair.source, target: pair.target, r: pair.r, s: pair.s,
        weight: pair.s + (similar >= .5 ? pair.r : 0) };
    }).filter(p => p.weight > 0);
    const reordered = new SemanticLayoutEngine().layout({ ...parseKnowledgeGraph({ nodes: nodes.slice().reverse(), edges: edges.slice().reverse() }), focusId: null, hops: 0 }, new Map(), input.slice().reverse(), { nowMs: 1000 });
    const permutationEqual = ids.every(id => result.clusterIds.get(id) === reordered.clusterIds.get(id)
      && JSON.stringify(result.anchors.get(id)) === JSON.stringify(reordered.anchors.get(id)));
    if (!permutationEqual) throw new Error('input array permutation changed actual result');
    const renamed = ids.map((_, i) => 'renamed:' + String((i * 37 + seed * 11) % n).padStart(4, '0'));
    const lookup = new Map(ids.map((id, i) => [id, renamed[i]]));
    const renamedResult = new SemanticLayoutEngine().layout({ ...parseKnowledgeGraph({
      nodes: nodes.map((node, i) => ({ ...node, id: renamed[i] })),
      edges: edges.map(e => ({ ...e, source: lookup.get(e.source)!, target: lookup.get(e.target)! })),
    }), focusId: null, hops: 0 }, new Map(), input.map(p => ({ ...p, source: lookup.get(p.source)!, target: lookup.get(p.target)! })), { nowMs: 1000 });
    const bridges: Edge[] = [];
    const existing = new Set(edges.map(e => key(e.source, e.target)));
    for (let group = 0; group < 3; group++) {
      let bridge: Edge | undefined;
      for (const source of ids.slice(group * width, (group + 1) * width)) {
        const target = ids.slice((group + 1) * width, (group + 2) * width).find(id => !existing.has(key(source, id)));
        if (target) { bridge = { source, target, weight: 1 }; break; }
      }
      if (!bridge) throw new Error('missing independent bridge candidate');
      bridges.push(bridge);
    }
    const changed = engine.layout({ ...parseKnowledgeGraph({ nodes, edges: [...edges, ...bridges] }), focusId: null, hops: 0 }, result.anchors, input, { nowMs: 1001, pinnedIds: new Set([ids[0]]) });
    const restored = engine.layout(view, changed.anchors, input, { nowMs: 1002, pinnedIds: new Set([ids[0]]) });
    const pinPreserved = [changed, restored].every(r => JSON.stringify(r.anchors.get(ids[0])) === JSON.stringify(result.anchors.get(ids[0])));
    if (!pinPreserved || [result, changed, restored].some(r => [...r.anchors.values()].some(p => ![p.x, p.y, p.z].every(Number.isFinite)))) throw new Error('layout invariant failed');
    rows.push({ scenario, seed, n, channel, ids, structureTruth, affinityTruth,
      rawRelations: edges, rawAffinities: input, communityEdges: admitted, membership,
      permutationEqual, renamedMembership: renamed.map(id => renamedResult.clusterIds.get(id)!),
      changedMembership: ids.map(id => changed.clusterIds.get(id)!),
      restoredMembership: ids.map(id => restored.clusterIds.get(id)!),
      relabelPartitionARI: ari(membership, renamed.map(id => renamedResult.clusterIds.get(id)!)),
      bridgeARI: ari(membership, ids.map(id => changed.clusterIds.get(id)!)),
      restoredARI: ari(membership, ids.map(id => restored.clusterIds.get(id)!)),
      addedBridges: bridges, pinPreserved, diagnostics: result.diagnostics,
      changedDiagnostics: changed.diagnostics, restoredDiagnostics: restored.diagnostics });
  }
}
const hash = (path: string) => createHash('sha256').update(readFileSync(path)).digest('hex');
console.log(JSON.stringify({ schema: 1, sourceFiles: {
  'desktop/src/knowledge/semantic-layout.ts': hash('desktop/src/knowledge/semantic-layout.ts'),
  'desktop/src/knowledge/graph-model.ts': hash('desktop/src/knowledge/graph-model.ts'),
  'docs/audits/audit_alden_graph_channels.ts': hash('docs/audits/audit_alden_graph_channels.ts'),
}, seedPolicy: { start, count, sizes, cases, channels: ['structure', 'affinity', 'mixed'] },
  scope: 'Synthetic development ablation. Two explicit conflicting generation labels; neither is real semantic truth. No models, source stores, native windows, or product policy changed.', rows }, null, 2));
