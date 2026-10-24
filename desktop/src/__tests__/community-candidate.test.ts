import { describe, expect, it } from 'vitest';
import { CONNECTED_AGGREGATE_PASSES, refineConnectedCommunities } from '../../../docs/audits/connected_aggregate_candidate';

function graph(n: number, edges: Array<[number, number, number]>) {
  const adjacency = Array.from({ length: n }, () => [] as Array<{ to: number; weight: number }>);
  const components = Array.from({ length: n }, (_, i) => i);
  for (const [a, b, weight] of edges) {
    adjacency[a].push({ to: b, weight }); adjacency[b].push({ to: a, weight });
    const from = components[b], to = components[a];
    for (let i = 0; i < n; i++) if (components[i] === from) components[i] = to;
  }
  return { adjacency, components };
}
function componentModularity(g: ReturnType<typeof graph>, labels: Array<string | number>) {
  let q = 0;
  // Deliberately direct O(n²) definition, independent of candidate merge gains.
  for (const component of new Set(g.components)) {
    const ids = g.components.flatMap((value, i) => value === component ? [i] : []);
    const degrees = g.adjacency.map(list => list.reduce((sum, p) => sum + p.weight, 0));
    const volume = ids.reduce((sum, i) => sum + degrees[i], 0);
    if (!volume) continue;
    for (const i of ids) for (const j of ids) if (labels[i] === labels[j])
      q += ((g.adjacency[i].find(p => p.to === j)?.weight ?? 0) - degrees[i] * degrees[j] / volume) / volume;
  }
  return q;
}

describe('connected aggregate audit candidate', () => {
  it('joins blocks that need a community move, retaining a weak bridge', () => {
    const edges: Array<[number, number, number]> = [];
    for (const offset of [0, 4]) for (let i = offset; i < offset + 4; i++) for (let j = i + 1; j < offset + 4; j++) edges.push([i, j, 1]);
    edges.push([3, 4, .01]);
    const g = graph(8, edges), labels = Array.from({ length: 8 }, (_, i) => 'pair' + (i >> 1));
    const result = refineConnectedCommunities(g.adjacency, labels, labels.map(() => true), g.components);
    expect(result.roots).toEqual([0, 0, 0, 0, 4, 4, 4, 4]);
    expect(result.merges).toBe(2);
    expect(componentModularity(g, result.roots)).toBeGreaterThan(componentModularity(g, labels));
    expect(componentModularity(g, result.roots) - componentModularity(g, labels)).toBeCloseTo(result.mergeObjectiveGain, 12);
    expect(result.termination).toBe('stable');
  });

  it('splits a disconnected inherited label, without joining unrelated components or isolates', () => {
    const g = graph(5, [[0, 1, 1], [2, 3, 1]]);
    const result = refineConnectedCommunities(g.adjacency, Array(5).fill('one'), Array(5).fill(true), g.components);
    expect(result.roots).toEqual([0, 0, 2, 2, 4]);
    expect(result.merges).toBe(0);
  });

  it('splits disconnected labels within the same component and accepts only positive objective moves', () => {
    const g = graph(4, [[0, 1, 1], [1, 2, 1], [2, 3, 1]]), labels = ['a', 'b', 'a', 'b'];
    const result = refineConnectedCommunities(g.adjacency, labels, Array(4).fill(true), g.components);
    expect(result.connectivitySplits).toBe(2);
    expect(componentModularity(g, result.roots)).toBeGreaterThanOrEqual(componentModularity(g, labels));
    for (const label of new Set(result.roots)) {
      const members = result.roots.flatMap((root, i) => root === label ? [i] : []);
      const reached = new Set([members[0]]), queue = [members[0]];
      for (let at = 0; at < queue.length; at++) for (const p of g.adjacency[queue[at]])
        if (result.roots[p.to] === label && !reached.has(p.to)) { reached.add(p.to); queue.push(p.to); }
      expect(reached.size).toBe(members.length);
    }
  });

  it('leaves inactive components outside candidate ownership and preserves unrelated-component invariance', () => {
    const base = graph(4, [[0, 1, 1], [1, 2, 1], [2, 3, 1], [3, 0, 1]]);
    const extra = graph(7, [[0, 1, 1], [1, 2, 1], [2, 3, 1], [3, 0, 1], [4, 5, 100], [5, 6, 100], [4, 6, 100]]);
    const a = refineConnectedCommunities(base.adjacency, ['a', 'b', 'c', 'd'], Array(4).fill(true), base.components);
    const b = refineConnectedCommunities(extra.adjacency, ['a', 'b', 'c', 'd', 'e', 'f', 'g'], [true, true, true, true, false, false, false], extra.components);
    expect(b.roots.slice(0, 4)).toEqual(a.roots);
    expect(b.roots.slice(4)).toEqual([-1, -1, -1]);
  });

  it('bounds scans on a 2048-node ring at the admitted capacity', () => {
    const n = 2048, edges: Array<[number, number, number]> = Array.from({ length: n }, (_, i) => [i, (i + 1) % n, 1]);
    const g = graph(n, edges);
    const result = refineConnectedCommunities(g.adjacency, Array.from({ length: n }, (_, i) => String(i)), Array(n).fill(true), g.components);
    expect(result.passes).toBeLessThanOrEqual(CONNECTED_AGGREGATE_PASSES);
    expect(result.visits).toBeLessThanOrEqual(2 * edges.length * (CONNECTED_AGGREGATE_PASSES + 1));
    expect(result.roots.every(root => root >= 0 && root < n)).toBe(true);
    expect(result.mergeObjectiveGain).toBeGreaterThan(0);
  });

  it('reports budget exhaustion rather than claiming convergence on a large star', () => {
    const n = 2048, edges: Array<[number, number, number]> = Array.from({ length: n - 1 }, (_, i) => [0, i + 1, 1]);
    const g = graph(n, edges);
    const result = refineConnectedCommunities(g.adjacency, Array.from({ length: n }, (_, i) => String(i)), Array(n).fill(true), g.components);
    expect(result.passes).toBe(CONNECTED_AGGREGATE_PASSES);
    expect(result.termination).toBe('budget');
    expect(result.merges).toBe(CONNECTED_AGGREGATE_PASSES);
    expect(result.visits).toBeLessThanOrEqual(2 * edges.length * (CONNECTED_AGGREGATE_PASSES + 1));
  });

  it('does not reduce the directly computed objective on any simple five-node graph', () => {
    const pairs: Array<[number, number]> = [];
    for (let i = 0; i < 5; i++) for (let j = i + 1; j < 5; j++) pairs.push([i, j]);
    for (let mask = 0; mask < 1 << pairs.length; mask++) {
      const edges: Array<[number, number, number]> = pairs.flatMap(([a, b], bit) => mask & 1 << bit ? [[a, b, 1] as [number, number, number]] : []);
      const g = graph(5, edges), labels = ['a', 'b', 'a', 'b', 'a'];
      const result = refineConnectedCommunities(g.adjacency, labels, Array(5).fill(true), g.components);
      expect(componentModularity(g, result.roots) + 1e-12, 'graph mask ' + mask).toBeGreaterThanOrEqual(componentModularity(g, labels));
    }
  });
});
