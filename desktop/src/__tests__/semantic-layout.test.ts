import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import type { LayoutAffinityPair } from '../knowledge/layout-affinity';
import type { Point3 } from '../knowledge/plasticity';
import { SemanticLayoutEngine, SEMANTIC_LAYOUT_LIMITS as limits } from '../knowledge/semantic-layout';

const empty = () => new Map<string, Point3>();
function scene(count = 6, edges: Array<Record<string, unknown>> = []): KnowledgeView {
  return { ...parseKnowledgeGraph({ nodes: Array.from({ length: count }, (_, i) => ({
    id: `n${String(i).padStart(4, '0')}`, label: 'same template, distinct entity',
    source_target: `source-${i % 3}`, space: 'same source', source_version: 'v1', importance: 20,
  })), edges }), focusId: null, hops: 0 };
}
const R = (a: number, b: number, weight = 1, extra = {}) => ({
  source: `n${String(a).padStart(4, '0')}`, target: `n${String(b).padStart(4, '0')}`, weight, ...extra,
});
const S = (a: number, b: number, cosine = .9): LayoutAffinityPair => ({ ...R(a, b), cosine });
function distance(p: ReadonlyMap<string, Point3>, a: string, b: string): number {
  const x = p.get(a)!, y = p.get(b)!; return Math.hypot(x.x - y.x, x.y - y.y, x.z - y.z);
}
function changed(a: ReadonlyMap<string, Point3>, b: ReadonlyMap<string, Point3>, ids = [...a.keys()]): string[] {
  return ids.filter(id => JSON.stringify(a.get(id)) !== JSON.stringify(b.get(id)));
}

describe('bounded semantic overview layout', () => {
  it('uses real R links to cluster and move a cross-source pair, without mutating facts', () => {
    const view = scene(6, [R(0, 1)]), original = JSON.stringify(view);
    const plain = new SemanticLayoutEngine().layout(scene(), empty());
    const linked = new SemanticLayoutEngine().layout(view, empty());
    expect(linked.clusterIds.get('n0000')).toBe(linked.clusterIds.get('n0001'));
    expect(changed(plain.anchors, linked.anchors, ['n0000', 'n0001'])).toHaveLength(2);
    expect(distance(linked.anchors, 'n0000', 'n0001')).toBeLessThan(distance(plain.anchors, 'n0000', 'n0001'));
    expect(JSON.stringify(view)).toBe(original);
  });

  it('uses S alone, and R+S keeps the channels separate', () => {
    const view = scene(), original = JSON.stringify(view);
    const s = new SemanticLayoutEngine().layout(view, empty(), [S(0, 1)]);
    const rs = new SemanticLayoutEngine().layout(scene(6, [R(2, 3)]), empty(), [S(0, 1)]);
    expect(s.clusterIds.get('n0000')).toBe(s.clusterIds.get('n0001'));
    expect(rs.clusterIds.get('n0000')).toBe(rs.clusterIds.get('n0001'));
    expect(rs.clusterIds.get('n0002')).toBe(rs.clusterIds.get('n0003'));
    expect(rs.clusterIds.get('n0000')).not.toBe(rs.clusterIds.get('n0002'));
    expect(rs.diagnostics.relationPairs).toBe(1); expect(rs.diagnostics.affinityPairs).toBe(1);
    expect(JSON.stringify(view)).toBe(original);
  });

  it('is deterministic under node, R and S display-order changes', () => {
    const view = scene(40, [R(0, 1), R(1, 2), R(3, 4)]), affinity = [S(8, 9), S(9, 10)];
    const a = new SemanticLayoutEngine().layout(view, empty(), affinity, { nowMs: 1000 });
    const b = new SemanticLayoutEngine().layout({ ...view, nodes: [...view.nodes].reverse(), edges: [...view.edges].reverse() }, empty(), [...affinity].reverse(), { nowMs: 1000 });
    expect(b.anchors).toEqual(a.anchors); expect(b.clusterIds).toEqual(a.clusterIds);
  });

  it('preserves all previous anchors across 120 to 121 nodes', () => {
    const engine = new SemanticLayoutEngine(), view = scene(120, [R(0, 1), R(3, 4)]);
    const a = engine.layout(view, empty());
    const b = engine.layout(scene(121, [R(0, 1), R(3, 4)]), a.anchors);
    expect(changed(a.anchors, b.anchors)).toEqual([]);
    for (const [id, label] of a.clusterIds) expect(b.clusterIds.get(id)).toBe(label);
    expect(b.diagnostics.affectedNodes).toBe(1);
    expect(b.dirtyIds.has('n0120')).toBe(true);
  });

  it('adding a new source or changing source/space/labels has no spatial authority', () => {
    const engine = new SemanticLayoutEngine(), view = scene(121, [R(0, 1)]);
    const a = engine.layout(view, empty());
    const next = scene(122, [R(0, 1)]);
    next.nodes = next.nodes.map(n => ({ ...n, sourceTarget: 'new-source:' + n.id, space: 'renamed', label: 'unrelated new wording' }));
    const b = engine.layout(next, a.anchors);
    expect(changed(a.anchors, b.anchors)).toEqual([]);
  });

  it('changing an unrelated ID never reallocates existing regions', () => {
    const engine = new SemanticLayoutEngine(), view = scene(8, [R(0, 1)]), a = engine.layout(view, empty());
    const next = { ...view, nodes: view.nodes.map(n => n.id === 'n0007' ? { ...n, id: 'new-id' } : n) };
    const b = engine.layout(next, a.anchors);
    expect(changed(a.anchors, b.anchors, [...a.anchors.keys()].filter(id => id !== 'n0007'))).toEqual([]);
    expect(b.anchors.has('n0007')).toBe(false); expect(b.diagnostics.cachedNodes).toBe(8);
  });

  it('detects real-link addition, weight change and withdrawal without changedIds hints', () => {
    const engine = new SemanticLayoutEngine(), a = engine.layout(scene(8, [R(4, 5)]), empty());
    const b = engine.layout(scene(8, [R(4, 5), R(0, 1)]), a.anchors);
    expect(changed(a.anchors, b.anchors, ['n0000', 'n0001'])).toHaveLength(2);
    expect(b.anchors.get('n0004')).toEqual(a.anchors.get('n0004'));
    const c = engine.layout(scene(8, [R(4, 5), R(0, 1, 8)]), b.anchors);
    expect(changed(b.anchors, c.anchors, ['n0000', 'n0001'])).toHaveLength(2);
    const removed = engine.layout(scene(8, [R(4, 5)]), c.anchors);
    expect(changed(c.anchors, removed.anchors, ['n0000', 'n0001']).length).toBeGreaterThan(0);
    expect(removed.clusterIds.get('n0000')).not.toBe(removed.clusterIds.get('n0001'));
  });

  it('detects S cosine changes and removed S while preserving an unrelated R component', () => {
    const engine = new SemanticLayoutEngine(), view = scene(8, [R(4, 5)]);
    const a = engine.layout(view, empty(), [S(0, 1, .8)]);
    const b = engine.layout(view, a.anchors, [S(0, 1, .99)]);
    expect(changed(a.anchors, b.anchors, ['n0000', 'n0001'])).toHaveLength(2);
    expect(b.anchors.get('n0004')).toEqual(a.anchors.get('n0004'));
    const c = engine.layout(view, b.anchors, []);
    expect(changed(b.anchors, c.anchors, ['n0000', 'n0001']).length).toBeGreaterThan(0);
    expect(c.diagnostics.affinityPairs).toBe(0);
  });

  it('zero weights, navigation and nonpositive cosine never contribute to communities or springs', () => {
    const plain = new SemanticLayoutEngine().layout(scene(), empty());
    const result = new SemanticLayoutEngine().layout(scene(6, [R(0, 1, 0), R(2, 3, 100, { purpose: 'navigation' })]), empty(), [S(0, 1, 0), S(2, 3, -.5)]);
    expect(result.anchors).toEqual(plain.anchors); expect(result.clusterIds).toEqual(plain.clusterIds);
    expect(result.diagnostics.relationPairs).toBe(0); expect(result.diagnostics.affinityPairs).toBe(0);
    expect(result.diagnostics.springEvaluations).toBe(0);
  });

  it('retains raw cosine sensitivity when high-degree normalization cancels uniform scaling', () => {
    const engine = new SemanticLayoutEngine(), view = scene(6);
    const pairs = [S(0, 1), S(1, 2), S(0, 2)];
    const a = engine.layout(view, empty(), pairs);
    const b = engine.layout(view, a.anchors, pairs.map(p => ({ ...p, cosine: p.cosine * .8 })));
    expect(changed(a.anchors, b.anchors, ['n0000', 'n0001', 'n0002'])).toHaveLength(3);
    expect(b.anchors.get('n0005')).toEqual(a.anchors.get('n0005'));
  });

  it('retains R strength sensitivity at high degree, not just normalized relative weights', () => {
    const engine = new SemanticLayoutEngine(), edges = [R(0, 1, 10), R(1, 2, 10), R(0, 2, 10)];
    const a = engine.layout(scene(6, edges), empty());
    const b = engine.layout(scene(6, edges.map(p => ({ ...p, weight: 20 }))), a.anchors);
    expect(changed(a.anchors, b.anchors, ['n0000', 'n0001', 'n0002'])).toHaveLength(3);
    expect(b.anchors.get('n0005')).toEqual(a.anchors.get('n0005'));
  });

  it('breaks coincident zero-force ties without merging unrelated identities', () => {
    const view = scene(4), positions = new Map(view.nodes.map(n => [n.id, { x: 0, y: 0, z: 0 }]));
    const a = new SemanticLayoutEngine().layout(view, positions);
    const b = new SemanticLayoutEngine().layout(view, positions);
    expect(a.anchors).toEqual(b.anchors);
    expect(distance(a.anchors, 'n0000', 'n0001')).toBeGreaterThan(.01);
    expect(new Set(a.clusterIds.values()).size).toBe(4);
    expect(a.diagnostics.collisionTests).toBeGreaterThan(0);
    expect(a.diagnostics.relationPairs + a.diagnostics.affinityPairs).toBe(0);
  });

  it('keeps exact pins during R/S changes and explicit reflow', () => {
    const engine = new SemanticLayoutEngine(), view = scene(8, [R(0, 1)]);
    const a = engine.layout(view, empty()), positions = new Map(a.anchors);
    positions.set('n0000', { x: 3, y: .25, z: .5 });
    const b = engine.layout(scene(8, [R(0, 1, 10)]), positions, [S(0, 2)], { pinnedIds: new Set(['n0000']), reflow: true });
    expect(b.anchors.get('n0000')).toEqual(positions.get('n0000'));
    expect(b.dirtyIds.has('n0000')).toBe(false);
    expect(changed(a.anchors, b.anchors, ['n0002', 'n0003']).length).toBeGreaterThan(0);
  });

  it('is a no-op on identical snapshots and supports explicit local invalidation', () => {
    const engine = new SemanticLayoutEngine(), view = scene(12, [R(0, 1)]);
    const a = engine.layout(view, empty()), b = engine.layout(view, a.anchors);
    expect(b.anchors).toEqual(a.anchors); expect(b.dirtyIds.size).toBe(0); expect(b.diagnostics.solverIterations).toBe(0);
    const c = engine.layout(view, b.anchors, [], { changedIds: new Set(['n0000']) });
    expect(c.diagnostics.affectedNodes).toBe(2);
    expect(c.anchors.get('n0009')).toEqual(b.anchors.get('n0009'));
  });

  it('preserves cluster identity when its original representative is removed and evicts retired caches', () => {
    const engine = new SemanticLayoutEngine(), view = scene(2, [R(0, 1)]), a = engine.layout(view, empty());
    const b = engine.layout({ ...view, nodes: [view.nodes[1]], edges: [] }, a.anchors);
    expect(b.clusterIds.get('n0001')).toBe(a.clusterIds.get('n0001'));
    expect(b.anchors.has('n0000')).toBe(false); expect(b.diagnostics.cachedNodes).toBe(1);
    expect(b.diagnostics.cachedPairs).toBe(0); expect(b.diagnostics.cachedClusters).toBe(1);
    const cleared = engine.layout({ ...view, nodes: [], edges: [] }, a.anchors);
    expect(cleared.diagnostics.cachedNodes + cleared.diagnostics.cachedPairs + cleared.diagnostics.cachedClusters).toBe(0);
  });

  it('never merges entity identities even with identical labels and cosine one', () => {
    const view = scene(2), result = new SemanticLayoutEngine().layout(view, empty(), [S(0, 1, 1)]);
    expect(result.anchors.size).toBe(2); expect(distance(result.anchors, 'n0000', 'n0001')).toBeGreaterThan(.05);
    expect(view.nodes).toHaveLength(2); expect(view.edges).toHaveLength(0);
  });

  it('rejects bad/unknown/self S and filters retracted or expired real edges', () => {
    const view = scene(6, [R(0, 1), R(2, 3), R(4, 5)]);
    view.edges[0].validTo = '2000-01-01';
    view.edges[1].evidence.retracted = true;
    const result = new SemanticLayoutEngine().layout(view, empty(), [S(0, 0), S(0, 9), S(0, 1, NaN), S(0, 1, Infinity), S(0, 1, 1.01)]);
    expect(result.diagnostics.relationPairs).toBe(1); expect(result.diagnostics.rejectedAffinities).toBe(5);
    expect(result.diagnostics.affinityPairs).toBe(0);
  });

  it('bounds hub S degree and pair/iteration budgets on a full 2048-node snapshot', () => {
    const view = scene(2048, Array.from({ length: 2047 }, (_, i) => R(i, i + 1)));
    const result = new SemanticLayoutEngine().layout(view, empty(), Array.from({ length: 2047 }, (_, i) => S(0, i + 1)));
    const d = result.diagnostics;
    expect(result.anchors.size).toBe(2048); expect(d.affinityPairs).toBe(limits.affinityDegree);
    expect(d.affinityPairsDropped).toBe(2047 - limits.affinityDegree);
    expect(d.communityPasses).toBeLessThanOrEqual(limits.communityPasses);
    expect(d.solverIterations).toBeLessThanOrEqual(limits.solverIterations);
    expect(d.collisionTests).toBeLessThanOrEqual(limits.collisionTests);
    expect(d.communityVisits).toBeLessThanOrEqual(2 * limits.communityPasses * (d.relationPairs + d.affinityPairs));
    expect(d.springEvaluations).toBeLessThanOrEqual(limits.solverIterations * (d.relationPairs + d.affinityPairs));
    for (const p of result.anchors.values()) expect([p.x, p.y, p.z].every(Number.isFinite)).toBe(true);
  });

  it('keeps cached survivors at node capacity and reports omitted arrivals', () => {
    const engine = new SemanticLayoutEngine(), a = engine.layout(scene(2048), empty());
    const next = scene(2049); next.nodes[2048].id = 'aaa-arrival';
    const b = engine.layout(next, a.anchors);
    expect(b.anchors).toEqual(a.anchors); expect(b.diagnostics.omittedNodes).toBe(1);
    expect(b.diagnostics.cachedNodes).toBe(2048); expect(b.dirtyIds.size).toBe(0);
  });

  it('bounds pathological spatial collisions and reports unresolved work honestly', () => {
    const view = scene(128), previous = new Map(view.nodes.map(n => [n.id, { x: 0, y: 0, z: 0 }]));
    const result = new SemanticLayoutEngine().layout(view, previous);
    expect(result.diagnostics.collisionTests).toBeLessThanOrEqual(limits.collisionTests);
    expect(result.diagnostics.collisionCandidatesTruncated).toBe(true);
    expect(result.diagnostics.crowdedCells).toBeGreaterThan(0);
    expect(result.anchors.size).toBe(128);
  });

  it('does not alias caller positions or returned cache maps, and clear releases all scope state', () => {
    const engine = new SemanticLayoutEngine(), view = scene(3), a = engine.layout(view, empty());
    const b = engine.layout(view, a.anchors); b.anchors.get('n0000')!.x = 99;
    const c = engine.layout(view, empty()); expect(c.anchors.get('n0000')!.x).not.toBe(99);
    engine.clear(); const d = engine.layout(scene(1), empty());
    expect(d.diagnostics.cachedNodes).toBe(1); expect(d.diagnostics.removedNodes).toBe(0);
  });

  it('takes the parent validated channel by default without changing fact edges', () => {
    const view = scene(2);
    view.layoutAffinity = { state: 'bounded_ready', inputKey: 'validated', profileKey: 'existing', inputNodes: [],
      pairs: [S(0, 1)], requestedNodes: 2, usableNodes: 2, evaluatedPairs: 1, truncatedCandidates: 0 };
    const result = new SemanticLayoutEngine().layout(view, empty());
    expect(result.diagnostics.affinityPairs).toBe(1); expect(view.edges).toEqual([]);
  });
});
