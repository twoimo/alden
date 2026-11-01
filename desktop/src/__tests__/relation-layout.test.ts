import { describe, expect, it } from 'vitest';
import { overviewGraph, parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { relationAnchors, relationCenterId } from '../knowledge/relation-layout';

const view = (): KnowledgeView => ({ ...parseKnowledgeGraph({
  nodes: ['a', 'b', 'c', 'd'].map((id, i) => ({ id, label: i < 2 ? '인물' : '주제', category: i < 2 ? 'people' : 'topic', importance: 50 })),
  edges: [{ source: 'a', target: 'c', weight: 10 }, { source: 'b', target: 'd', weight: 10 }],
}), focusId: null, hops: 0 });

describe('real-reference graph projection', () => {
  it('shows the memories first while keeping real hub navigation in the full graph', () => {
    const graph = view(); graph.nodes.push({ ...graph.nodes[0], id: 'hub', isHub: true });
    const original = JSON.stringify(graph);
    expect(overviewGraph(graph).nodes.map(n => n.id)).not.toContain('hub');
    expect(JSON.stringify(graph)).toBe(original);
    expect(overviewGraph({ nodes: [graph.nodes[4]], edges: [] }).nodes[0].id).toBe('hub');
  });
  it('ignores labels and entity-type relabeling, input order, and does not mutate OSK data', () => {
    const graph = view(), original = JSON.stringify(graph), anchors = relationAnchors(graph);
    const renamed = { ...graph, nodes: [...graph.nodes].reverse().map(n => ({ ...n, label: '카카오톡', category: 'collection' })), edges: [...graph.edges].reverse() };
    expect(relationAnchors(renamed)).toEqual(anchors);
    expect(JSON.stringify(graph)).toBe(original);
    expect(relationCenterId(renamed)).toBe(relationCenterId(graph));
  });
  it('pulls cross-type source-linked pairs together and separates unrelated same-type nodes', () => {
    const p = relationAnchors(view());
    const distance = (a: string, b: string) => { const x = p.get(a)!, y = p.get(b)!; return Math.hypot(x.x - y.x, x.y - y.y, x.z - y.z); };
    expect(distance('a', 'c')).toBeLessThan(distance('a', 'b'));
    expect(distance('b', 'd')).toBeLessThan(distance('c', 'd'));
  });
  it('keeps an existing component anchored when an unrelated memory is added', () => {
    const a = view(), b = view(); b.nodes.push({ ...b.nodes[0], id: 'unrelated' });
    const before = relationAnchors(a), after = relationAnchors(b);
    for (const [id, xyz] of before) expect(after.get(id)).toEqual(xyz);
  });
  it('keeps targets finite and bounded, removes expired references and observes the view budget', () => {
    const graph = view(); graph.nodes = Array.from({ length: 80 }, (_, i) => ({ ...graph.nodes[0], id: 'node-' + i }));
    const p = relationAnchors(graph); expect(p.size).toBe(24);
    for (const xyz of p.values()) expect(Math.hypot(xyz.x / 2.15, xyz.y / 1.4, xyz.z)).toBeLessThanOrEqual(1.000001);
    const expired = view(); expired.edges[0].validTo = '2000-01-01T00:00:00Z';
    const removed = { ...expired, edges: expired.edges.slice(1) };
    expect(relationAnchors(expired)).toEqual(relationAnchors(removed));
  });
});
