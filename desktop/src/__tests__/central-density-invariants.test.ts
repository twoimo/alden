import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { SEMANTIC_LAYOUT_LIMITS, SemanticLayoutEngine } from '../knowledge/semantic-layout';
import type { LayoutAffinityPair } from '../knowledge/layout-affinity';
import type { Point3 } from '../knowledge/plasticity';

// Deliberately invented identities and topology. Never load user records or
// recorded positions to exercise this geometry-only contract.
const id = (community: number, member: number): string =>
  `synthetic-${String(community).padStart(2, '0')}-${String(member).padStart(3, '0')}`;

function denseCommunities(communities = 4, members = 42): KnowledgeView {
  const nodes: Array<{ id: string; label: string; category: string; importance: number }> = [];
  const edges: Array<{ source: string; target: string; weight: number }> = [];
  for (let community = 0; community < communities; community++) {
    for (let member = 0; member < members; member++) {
      nodes.push({ id: id(community, member), label: 'synthetic node', category: 'topic', importance: 50 });
      for (let other = 0; other < member; other++) {
        // A directed source fact; the layout may treat it as an undirected
        // geometric spring but must not change its stored direction.
        edges.push({ source: id(community, member), target: id(community, other), weight: 2 });
      }
    }
  }
  return { ...parseKnowledgeGraph({ nodes, edges }), focusId: null, hops: 0 };
}

const ZERO: Point3 = { x: 0, y: 0, z: 0 };
function coincidentInputs(view: KnowledgeView): Map<string, Point3> {
  return new Map(view.nodes.map(node => [node.id, { ...ZERO }]));
}

function pinnedInputs(view: KnowledgeView): { positions: Map<string, Point3>; pins: Set<string> } {
  const positions = coincidentInputs(view);
  const pins = new Set([id(0, 0), id(1, 0), id(2, 0), id(3, 0)]);
  const fixed: Point3[] = [
    { x: 0, y: 0, z: 0 }, { x: .65, y: 0, z: 0 },
    { x: -.55, y: .2, z: 0 }, { x: 0, y: -.6, z: .2 },
  ];
  [...pins].forEach((key, i) => positions.set(key, fixed[i]));
  return { positions, pins };
}

function distance(a: Point3, b: Point3): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}

// A geometric readout independent of the solver's grids, forces or heuristics.
// The thresholds represent a small visually crowded ball and pair separation;
// they are used for paired before/after comparisons, not claimed target scores.
function density(positions: ReadonlyMap<string, Point3>, ids: readonly string[]) {
  const points = ids.map(key => positions.get(key)!);
  let closePairs = 0;
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      if (distance(points[i], points[j]) < .1) closePairs++;
    }
  }
  const radii = points.map(p => distance(p, ZERO)).sort((a, b) => a - b);
  return {
    nodes: points.length,
    centerR025: radii.filter(radius => radius < .25).length,
    closePairsR010: closePairs,
    medianRadius: radii[Math.floor(radii.length / 2)],
    p90Radius: radii[Math.floor(.9 * (radii.length - 1))],
  };
}

function expectFiniteBounded(points: ReadonlyMap<string, Point3>): void {
  for (const point of points.values()) {
    expect([point.x, point.y, point.z].every(Number.isFinite)).toBe(true);
    expect(Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1)).toBeLessThanOrEqual(1 + 1e-9);
  }
}

describe('independent central-density geometry invariants', () => {
  it('disperses coincident dense communities without uniformly scaling the source or inventing links', () => {
    const view = denseCommunities();
    const { positions, pins } = pinnedInputs(view);
    const original = JSON.stringify(view);
    const sourceEdges = view.edges.map(edge => [edge.source, edge.target]);
    const affinity: LayoutAffinityPair[] = [
      { source: id(0, 1), target: id(0, 2), cosine: .91 },
      { source: id(3, 1), target: id(3, 2), cosine: .88 },
    ];
    const result = new SemanticLayoutEngine().layout(view, positions, affinity, { pinnedIds: pins, nowMs: 1000 });
    const ids = view.nodes.map(node => node.id);
    const before = density(positions, ids), after = density(result.anchors, ids);

    expect(result.anchors.size).toBe(ids.length);
    expect(new Set(result.anchors.keys())).toEqual(new Set(ids));
    expect(after.centerR025).toBeLessThan(before.centerR025);
    expect(after.closePairsR010).toBeLessThan(before.closePairsR010);
    // All movable input points start at exactly the same position. A uniform
    // scale would leave them coincident, whatever the global zoom factor.
    const formerlyCoincident = ids.filter(key => !pins.has(key));
    expect(new Set(formerlyCoincident.map(key => JSON.stringify(result.anchors.get(key)))).size).toBeGreaterThan(1);
    for (const key of pins) {
      expect(result.anchors.get(key)).toEqual(positions.get(key));
      expect(result.dirtyIds.has(key)).toBe(false);
    }
    for (let group = 1; group < 4; group++) {
      expect(result.clusterIds.get(id(0, 0))).not.toBe(result.clusterIds.get(id(group, 0)));
    }
    expect(result.diagnostics.relationPairs).toBe(view.edges.length);
    expect(result.diagnostics.affinityPairs).toBe(affinity.length);
    expect(view.edges.map(edge => [edge.source, edge.target])).toEqual(sourceEdges);
    expect(JSON.stringify(view)).toBe(original);
    expectFiniteBounded(result.anchors);

    const d = result.diagnostics, limits = SEMANTIC_LAYOUT_LIMITS;
    expect(d.communityPasses).toBeLessThanOrEqual(limits.communityPasses);
    expect(d.solverIterations).toBeLessThanOrEqual(limits.solverIterations);
    expect(d.collisionTests).toBeLessThanOrEqual(limits.collisionTests);
    expect(d.densityEvaluations).toBeLessThanOrEqual(d.nodes * d.solverIterations);
    expect(d.neighborhoodDensityEvaluations).toBeGreaterThan(0);
    expect(d.neighborhoodDensityEvaluations).toBeLessThanOrEqual(d.nodes * d.solverIterations);
    expect(d.springEvaluations).toBeLessThanOrEqual(d.solverIterations * (d.relationPairs + d.affinityPairs));
    expect(d.cachedNodes).toBe(ids.length);

    if (process.env.CENTRAL_DENSITY_METRICS === '1') {
      console.info('CENTRAL_DENSITY_METRICS', JSON.stringify({ before, after, diagnostics: d }));
    }
  });

  it('is order-independent in a pathological source graph and keeps exact pins during reflow', () => {
    const view = denseCommunities(4, 24);
    const { positions, pins } = pinnedInputs(view);
    const options = { pinnedIds: pins, nowMs: 1000, reflow: true };
    const normal = new SemanticLayoutEngine().layout(view, positions, [], options);
    const reversed = new SemanticLayoutEngine().layout({
      ...view, nodes: [...view.nodes].reverse(), edges: [...view.edges].reverse(),
    }, positions, [], options);
    expect(reversed.anchors).toEqual(normal.anchors);
    expect(reversed.clusterIds).toEqual(normal.clusterIds);
    expect(reversed.diagnostics).toEqual(normal.diagnostics);
    for (const key of pins) expect(normal.anchors.get(key)).toEqual(positions.get(key));
    expectFiniteBounded(normal.anchors);
  });

  it('localizes a small real-edge change and leaves unrelated dense components stationary', () => {
    const view = denseCommunities(4, 24), { positions, pins } = pinnedInputs(view);
    const engine = new SemanticLayoutEngine();
    const first = engine.layout(view, positions, [], { pinnedIds: pins, nowMs: 1000 });
    const nextView: KnowledgeView = {
      ...view,
      edges: view.edges.map(edge => edge.source === id(0, 1) && edge.target === id(0, 0)
        ? { ...edge, weight: 20 } : edge),
    };
    const next = engine.layout(nextView, first.anchors, [], { pinnedIds: pins, nowMs: 1000 });
    let changedInAffected = 0;
    for (const node of view.nodes) {
      if (node.id.startsWith('synthetic-00-')) {
        if (distance(first.anchors.get(node.id)!, next.anchors.get(node.id)!) > 1e-9) changedInAffected++;
      } else {
        expect(next.anchors.get(node.id)).toEqual(first.anchors.get(node.id));
        expect(next.clusterIds.get(node.id)).toBe(first.clusterIds.get(node.id));
        expect(next.dirtyIds.has(node.id)).toBe(false);
      }
    }
    expect(changedInAffected).toBeGreaterThan(0);
    for (const key of pins) expect(next.anchors.get(key)).toEqual(positions.get(key));
    expect(next.diagnostics.affectedNodes).toBeLessThan(view.nodes.length);
    expect(view.edges.some(edge => edge.weight === 20)).toBe(false);

    const arrival = { ...view.nodes[0], id: 'synthetic-unrelated-arrival' };
    const extended = engine.layout({ ...nextView, nodes: [...nextView.nodes, arrival] }, next.anchors, [], { pinnedIds: pins, nowMs: 1000 });
    for (const [key, position] of next.anchors) expect(extended.anchors.get(key)).toEqual(position);
    expect(extended.anchors.has(arrival.id)).toBe(true);
    expectFiniteBounded(extended.anchors);
  });

  it('caps pathological excess relations in geometry while preserving every input source fact', () => {
    const view = denseCommunities(1, 96);
    const originalEdges = view.edges.map(edge => ({ source: edge.source, target: edge.target, weight: edge.weight }));
    expect(originalEdges.length).toBeGreaterThan(SEMANTIC_LAYOUT_LIMITS.relationPairs);
    const result = new SemanticLayoutEngine().layout(view, coincidentInputs(view), [], { nowMs: 1000 });
    expect(result.anchors.size).toBe(view.nodes.length);
    expect(result.diagnostics.relationPairs).toBe(SEMANTIC_LAYOUT_LIMITS.relationPairs);
    expect(result.diagnostics.relationPairsDropped).toBe(originalEdges.length - SEMANTIC_LAYOUT_LIMITS.relationPairs);
    expect(view.edges.map(edge => ({ source: edge.source, target: edge.target, weight: edge.weight }))).toEqual(originalEdges);
    expectFiniteBounded(result.anchors);
    expect(result.diagnostics.collisionTests).toBeLessThanOrEqual(SEMANTIC_LAYOUT_LIMITS.collisionTests);
  });
});
