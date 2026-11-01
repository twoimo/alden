import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { relationDepths } from '../knowledge/relation-depth';

const NOW = Date.parse('2026-10-12T00:00:00Z');
function graph(ids: readonly string[], links: Array<[string, string, 'reference' | 'semantic' | 'navigation']>): KnowledgeView {
  const parsed = parseKnowledgeGraph({
    nodes: ids.map(id => ({ id, label: id })),
    edges: links.map(([source, target, purpose]) => ({ source, target, purpose, weight: 2 })),
  }, NOW);
  return { ...parsed, focusId: 'root', hops: 3 };
}
function ordered(depths: ReadonlyMap<string, number>): [string, number][] {
  return [...depths].sort(([a], [b]) => a.localeCompare(b));
}

describe('visible real relation depth', () => {
  it('computes minimum hops across directed, cyclic and parallel facts without mutating them', () => {
    const view = graph(['root', 'a', 'b', 'c', 'd', 'unlinked'], [
      ['a', 'root', 'reference'], ['a', 'b', 'semantic'], ['c', 'b', 'reference'],
      ['root', 'b', 'reference'], ['b', 'root', 'semantic'], ['c', 'a', 'reference'],
      ['d', 'c', 'semantic'], ['d', 'd', 'reference'],
    ]);
    const initial = JSON.stringify(view);
    expect(ordered(relationDepths(view, NOW))).toEqual([['a', 1], ['b', 1], ['c', 2], ['d', 3], ['root', 0]]);
    expect(ordered(relationDepths({ ...view, nodes: [...view.nodes].reverse(), edges: [...view.edges].reverse() }, NOW)))
      .toEqual(ordered(relationDepths(view, NOW)));
    expect(JSON.stringify(view)).toBe(initial);
  });

  it('excludes navigation, retracted, hidden and time-invalid routes at exact bounds', () => {
    const view = graph(['root', 'old', 'from', 'until', 'gone', 'hiddenPath', 'end', 'nav', 'retracted'], [
      ['root', 'old', 'reference'], ['root', 'from', 'reference'], ['root', 'until', 'semantic'],
      ['root', 'gone', 'reference'], ['root', 'hiddenPath', 'reference'],
      ['hiddenPath', 'end', 'semantic'], ['root', 'nav', 'navigation'], ['root', 'retracted', 'reference'],
    ]);
    const edge = (target: string) => view.edges.find(e => e.target === target)!;
    edge('old').validTo = new Date(NOW - 1).toISOString();
    edge('from').validFrom = new Date(NOW).toISOString();
    edge('until').validTo = new Date(NOW).toISOString();
    edge('gone').evidence.retracted = true;
    edge('retracted').validFrom = new Date(NOW + 1).toISOString();
    view.nodes.find(n => n.id === 'hiddenPath')!.evidence.retracted = true;
    expect(ordered(relationDepths(view, NOW))).toEqual([['from', 1], ['root', 0]]);
    view.nodes.find(n => n.id === 'hiddenPath')!.evidence.retracted = false;
    view.nodes = view.nodes.filter(n => n.id !== 'hiddenPath');
    expect(ordered(relationDepths(view, NOW))).toEqual([['from', 1], ['root', 0]]);
  });

  it('respects a missing or retracted focus and does not attribute depth to disconnected nodes', () => {
    const view = graph(['root', 'a', 'b'], [['a', 'b', 'semantic']]);
    expect(ordered(relationDepths(view, NOW))).toEqual([['root', 0]]);
    expect(relationDepths({ ...view, focusId: null }, NOW).size).toBe(0);
    expect(relationDepths({ ...view, focusId: 'missing' }, NOW).size).toBe(0);
    view.nodes[0].evidence.retracted = true;
    expect(relationDepths(view, NOW).size).toBe(0);
  });

  it('traverses a long visible chain iteratively without an arbitrary hop ceiling', () => {
    const count = 4096;
    const ids = Array.from({ length: count }, (_, i) => i === 0 ? 'root' : `node-${i}`);
    const links = ids.slice(1).map((id, i): [string, string, 'reference'] => [id, ids[i], 'reference']);
    const view = graph(ids, links);
    const depths = relationDepths(view, NOW);
    expect(depths.size).toBe(count);
    expect(depths.get(ids.at(-1)!)).toBe(count - 1);
  });
});
