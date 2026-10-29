import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { contextRegions } from '../knowledge/context-regions';
import { relationAnchors } from '../knowledge/relation-layout';
import type { Point3 } from '../knowledge/plasticity';

/** Synthetic 24-node reference fan: 5 branches, 15 leaves, 3 grandchildren. */
function denseReferenceFan(): { view: KnowledgeView; branchOf: Map<string, number>; links: [string, string][] } {
  const branchOf = new Map<string, number>();
  const ids = ['focus'];
  const links: [string, string][] = [];
  for (let branch = 0; branch < 5; branch++) {
    const head = `branch-${branch}`;
    ids.push(head);
    branchOf.set(head, branch);
    links.push(['focus', head]);
    for (let leaf = 0; leaf < 3; leaf++) {
      const id = `${head}-leaf-${leaf}`;
      ids.push(id);
      branchOf.set(id, branch);
      links.push([head, id]);
      if (branch < 3 && leaf === 0) {
        const grandchild = `${id}-grandchild`;
        ids.push(grandchild);
        branchOf.set(grandchild, branch);
        links.push([id, grandchild]);
      }
    }
  }
  const graph = parseKnowledgeGraph({
    nodes: ids.map(id => ({ id, label: id, category: 'topic', importance: 50 })),
    edges: links.map(([source, target]) => ({ source, target, weight: 8, purpose: 'reference' })),
  });
  return { view: { ...graph, focusId: 'focus', hops: 3 }, branchOf, links };
}

function xyDistance(a: Point3, b: Point3): number { return Math.hypot(a.x - b.x, a.y - b.y); }
function xyCrosses(a: Point3, b: Point3, c: Point3, d: Point3): boolean {
  const cross = (p: Point3, q: Point3, r: Point3) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  return cross(a, b, c) * cross(a, b, d) < -1e-10 && cross(c, d, a) * cross(c, d, b) < -1e-10;
}

function fanMetrics(
  points: ReadonlyMap<string, Point3>, branchOf: ReadonlyMap<string, number>, links: readonly [string, string][],
): { crossBranchCloseXY: number; crossingEdgesXY: number; rootXY: number; minBranchHeadAngle: number } {
  const members = [...branchOf];
  let crossBranchCloseXY = 0;
  for (let i = 0; i < members.length; i++) for (let j = i + 1; j < members.length; j++) {
    if (members[i][1] !== members[j][1] && xyDistance(points.get(members[i][0])!, points.get(members[j][0])!) < .25) crossBranchCloseXY++;
  }
  let crossingEdgesXY = 0;
  for (let i = 0; i < links.length; i++) for (let j = i + 1; j < links.length; j++) {
    const [a, b] = links[i], [c, d] = links[j];
    if (a !== c && a !== d && b !== c && b !== d && xyCrosses(points.get(a)!, points.get(b)!, points.get(c)!, points.get(d)!)) crossingEdgesXY++;
  }
  const root = points.get('focus')!;
  const angles = Array.from({ length: 5 }, (_, i) => {
    const p = points.get(`branch-${i}`)!;
    return (Math.atan2(p.y - root.y, p.x - root.x) + 2 * Math.PI) % (2 * Math.PI);
  }).sort((a, b) => a - b);
  const gaps = angles.map((value, i) => (angles[(i + 1) % angles.length] - value + 2 * Math.PI) % (2 * Math.PI));
  return { crossBranchCloseXY, crossingEdgesXY, rootXY: Math.hypot(root.x, root.y), minBranchHeadAngle: Math.min(...gaps) };
}

describe('focused real-reference branch readability', () => {
  it('separates connected branches in the bounded 24-node fan with finite positions and no synthetic changes', () => {
    const { view, branchOf, links } = denseReferenceFan();
    const original = JSON.stringify(view);
    const points = relationAnchors(view);
    expect(points.size).toBe(24);
    const metrics = fanMetrics(points, branchOf, links);
    console.log('synthetic focused 24-node XY geometry:', JSON.stringify(metrics));
    expect(metrics.crossBranchCloseXY).toBe(0);
    expect(metrics.crossingEdgesXY).toBe(0);
    expect(metrics.minBranchHeadAngle).toBeGreaterThan(.5);
    expect(metrics.rootXY).toBe(0);
    for (const point of points.values()) {
      expect([point.x, point.y, point.z].every(Number.isFinite)).toBe(true);
      expect(Math.hypot(point.x / 2.15, point.y / 1.4, point.z)).toBeLessThanOrEqual(1 + 1e-9);
    }
    expect(JSON.stringify(view)).toBe(original);
    expect(relationAnchors({ ...view, nodes: [...view.nodes].reverse(), edges: [...view.edges].reverse() })).toEqual(points);
    expect(relationAnchors({ ...view, edges: view.edges.map(edge => ({ ...edge, source: edge.target, target: edge.source })) })).toEqual(points);
  });

  it('preserves real source memberships and deterministic source-region influence', () => {
    const { view } = denseReferenceFan();
    view.nodes.forEach(node => { node.space = node.id === 'focus' ? 'sources/root' : `sources/${node.id.split('-')[1]}`; });
    const memberships = contextRegions(view);
    expect(memberships.filter(region => region.id.startsWith('space:')).length).toBe(6);
    const baseline = relationAnchors(view);
    expect(relationAnchors({ ...view, nodes: [...view.nodes].reverse(), edges: [...view.edges].reverse() })).toEqual(baseline);
    const withoutSpaces = relationAnchors({ ...view, nodes: view.nodes.map(node => ({ ...node, space: '' })) });
    const point = baseline.get('branch-0-leaf-0')!, ungrouped = withoutSpaces.get('branch-0-leaf-0')!;
    expect(Math.hypot(point.x - ungrouped.x, point.y - ungrouped.y, point.z - ungrouped.z)).toBeGreaterThan(.01);
    expect(view.nodes.find(node => node.id === 'branch-0-leaf-0')!.space).toBe('sources/0');
  });

  it('measures a high-degree real star under the same 24-node projection budget', () => {
    const ids = Array.from({ length: 23 }, (_, i) => `leaf-${i}`);
    const graph = parseKnowledgeGraph({
      nodes: ['focus', ...ids].map(id => ({ id, label: id, importance: 40 })),
      edges: ids.map(id => ({ source: id, target: 'focus', purpose: 'reference', weight: 8 })),
    });
    const points = relationAnchors({ ...graph, focusId: 'focus', hops: 1 });
    let closePairsXY = 0;
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
      if (xyDistance(points.get(ids[i])!, points.get(ids[j])!) < .25) closePairsXY++;
    }
    console.log('synthetic focused 23-neighbor star:', JSON.stringify({ closePairsXY }));
    expect(points.size).toBe(24);
    expect(closePairsXY).toBeLessThanOrEqual(2);
    for (const p of points.values()) {
      expect(Math.hypot(p.x / 2.15, p.y / 1.4, p.z)).toBeLessThanOrEqual(1 + 1e-9);
    }
  });

  it('leaves disconnected visible notes outside the focused branch fan', () => {
    const parsed = parseKnowledgeGraph({
      nodes: ['focus', 'linked', 'loner', 'another'].map(id => ({ id, label: id, importance: 50 })),
      edges: [{ source: 'focus', target: 'linked', weight: 8, purpose: 'reference' }],
    });
    const view: KnowledgeView = { ...parsed, focusId: 'focus', hops: 2 };
    const original = relationAnchors(view);
    view.nodes.push({ ...view.nodes[0], id: 'unrelated' });
    const expanded = relationAnchors(view);
    expect(expanded.get('focus')).toEqual(original.get('focus'));
    expect(expanded.get('linked')).toEqual(original.get('linked'));
    expect(expanded.get('loner')).toEqual(original.get('loner'));
  });

  it('does not assign branch positions through invalid links or weak navigation', () => {
    const graph = parseKnowledgeGraph({
      nodes: ['focus', 'real', 'navigational', 'expired', 'retracted'].map(id => ({ id, label: id })),
      edges: [{ source: 'focus', target: 'real', purpose: 'reference', weight: 5 },
        { source: 'focus', target: 'navigational', purpose: 'navigation' }],
    });
    const view: KnowledgeView = { ...graph, focusId: 'focus', hops: 2 };
    const base = relationAnchors(view);
    const reference = graph.edges[0];
    view.edges = [...view.edges,
      { ...reference, target: 'expired', validTo: '2000-01-01T00:00:00Z' },
      { ...reference, target: 'retracted', evidence: { ...reference.evidence, retracted: true } },
      { ...reference, target: 'hidden-node' },
    ];
    expect(relationAnchors(view)).toEqual(base);
    expect(base.get('focus')).toEqual({ x: 0, y: 0, z: 0 });
  });
});
