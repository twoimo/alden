import { describe, expect, it, vi } from 'vitest';
import { parseKnowledgeGraph, type KnowledgeEdge, type KnowledgeView } from '../knowledge/graph-model';
import { connectedComponents, PlasticityLayout, selectSynapses, synapseRestLength, type Point3 } from '../knowledge/plasticity';

const NOW = Date.parse('2026-10-04T00:00:00.000Z');
const id = (i: number) => `node-${String(i).padStart(3, '0')}`;

function fixture(nodeCount = 120, edgeCount = 512): { view: KnowledgeView; anchors: Map<string, Point3> } {
  const edges: Array<{ source: string; target: string; weight: number }> = [];
  for (let offset = 1; offset < nodeCount / 2 && edges.length < edgeCount; offset++) {
    for (let i = 0; i < nodeCount && edges.length < edgeCount; i++) {
      edges.push({ source: id(i), target: id((i + offset) % nodeCount), weight: 1 + i % 7 });
    }
  }
  const graph = parseKnowledgeGraph({
    nodes: Array.from({ length: nodeCount }, (_, i) => ({ id: id(i), label: `Node ${i}`, importance: i % 101 })),
    edges,
  }, NOW);
  const anchors = new Map(graph.nodes.map((node, i) => [node.id, {
    x: (i % 5 - 2) * .35,
    y: (Math.floor(i / 5) % 6 - 2.5) * .2,
    z: (Math.floor(i / 30) - 1.5) * .2,
  }]));
  return { view: { ...graph, focusId: null, hops: 0 }, anchors };
}

function pointIds(layout: PlasticityLayout): string[] {
  const ids: string[] = [];
  layout.forEachPoint(nodeId => ids.push(nodeId));
  return ids;
}

function expectFinite(layout: PlasticityLayout): void {
  expect([...layout.coordinates].every(Number.isFinite)).toBe(true);
  expect(Number.isFinite(layout.diagnostics().maxSpeed)).toBe(true);
  expect(Number.isFinite(layout.diagnostics().maxForce)).toBe(true);
  expect(layout.diagnostics().maxSpeed).toBeLessThanOrEqual(1.4 + 1e-12);
  layout.forEachPoint((_id, x, y, z) => {
    expect(Math.hypot(x / 2.3, y / 1.55, z / 1.1)).toBeLessThanOrEqual(1 + 1e-12);
  });
}

describe('plasticity capacities for the global graph', () => {
  it('preserves the default 24-node, 144-synapse layout and 5184-byte budget', () => {
    const { view, anchors } = fixture(24, 200);
    const layout = new PlasticityLayout();
    layout.setGraph(view, anchors, anchors);
    expect([layout.nodeCap, layout.synapseCap, layout.coordinates.length]).toEqual([24, 144, 72]);
    expect(layout.diagnostics()).toMatchObject({ nodes: 24, synapses: 144, bufferBytes: 5184 });
    expect(selectSynapses(view.edges)).toHaveLength(144);
  });

  it.each([
    [200, 900, 120, 512],
    [32.9, 17.9, 32, 17],
    [0, 0, 0, 0],
    [-1, -2, 0, 0],
    [NaN, NaN, 24, 144],
    [Infinity, -Infinity, 24, 144],
  ])('bounds capacities (%s, %s) before allocating buffers', (nodes, edges, nodeCap, synapseCap) => {
    const { view, anchors } = fixture(130, 700);
    const layout = new PlasticityLayout(nodes, edges);
    layout.setGraph(view, anchors, anchors);
    expect([layout.nodeCap, layout.synapseCap]).toEqual([nodeCap, synapseCap]);
    expect(layout.coordinates.length).toBe(nodeCap * 3);
    expect(layout.diagnostics().bufferBytes).toBe(nodeCap * 108 + synapseCap * 18);
    expect(pointIds(layout)).toEqual(view.nodes.slice(0, nodeCap).map(node => node.id));
    expect(layout.diagnostics().synapses).toBeLessThanOrEqual(synapseCap);
    layout.settle();
    expectFinite(layout);
    expect(layout.moving).toBe(false);
  });

  it('keeps deterministic synapse ranking and bundling for a caller cap bounded at 512', () => {
    const { view } = fixture(120, 700);
    const edge = view.edges[0];
    view.edges.push({ ...edge, source: edge.target, target: edge.source, weight: 100 });
    const original = JSON.stringify(view);
    const selected = selectSynapses(view.edges, undefined, NOW, 512);
    expect(selected).toHaveLength(512);
    expect(selected[0].strength).toBe(100 / 101);
    expect(selectSynapses(view.edges, undefined, NOW, 9999)).toEqual(selected);
    expect(selectSynapses(view.edges, undefined, NOW)).toEqual(selected.slice(0, 144));
    expect(selectSynapses(view.edges, undefined, NOW, 17.9)).toEqual(selected.slice(0, 17));
    expect(selectSynapses(view.edges, undefined, NOW, NaN)).toEqual(selected.slice(0, 144));
    expect(selectSynapses(view.edges, undefined, NOW, 0)).toEqual([]);
    expect(selectSynapses(view.edges, undefined, NOW, -1)).toEqual([]);
    expect(JSON.stringify(view)).toBe(original);
  });

  it('applies evidence, active endpoint and exact temporal gates before the instance edge cap', () => {
    const { view, anchors } = fixture(26, 0);
    const base = fixture().view.edges[0];
    const edge = (source: number, target: number, patch: Partial<KnowledgeEdge> = {}): KnowledgeEdge => ({
      ...base, source: id(source), target: id(target), weight: 1, ...patch,
    });
    view.nodes[0].evidence.retracted = true;
    view.edges = [
      edge(0, 1, { weight: 100 }),
      edge(1, 25, { weight: 100 }),
      edge(1, 2, { weight: 100, evidence: { ...base.evidence, retracted: true } }),
      edge(2, 3, { weight: 100, validTo: new Date(NOW).toISOString() }),
      edge(3, 4, { weight: 100, validFrom: new Date(NOW + 1).toISOString() }),
      edge(4, 4, { weight: 100 }),
      edge(4, 5, { source: 'missing', weight: 100 }),
      edge(20, 21, { validFrom: new Date(NOW).toISOString() }),
      edge(21, 22, { validTo: new Date(NOW + 1).toISOString() }),
      edge(22, 23, { validFrom: 'invalid', validTo: 'invalid' }),
      edge(23, 24, { validFrom: '', validTo: '' }),
    ];
    const active = new Set(view.nodes.slice(1, 25).map(node => node.id));
    const selected = selectSynapses(view.edges, active, NOW, 512);
    expect(selected.map(link => [link.source, link.target])).toEqual([
      [id(20), id(21)], [id(21), id(22)], [id(22), id(23)], [id(23), id(24)],
    ]);
    const clock = vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const original = JSON.stringify(view);
    try {
      const layout = new PlasticityLayout(24, 4);
      layout.setGraph(view, anchors, anchors);
      expect(pointIds(layout)).toEqual([...active]);
      expect(layout.diagnostics()).toMatchObject({ nodes: 24, synapses: 4 });
      const capped = new PlasticityLayout(24, 2);
      capped.setGraph(view, anchors, anchors);
      expect(capped.diagnostics().synapses).toBe(2);
      clock.mockReturnValue(NOW + 1);
      layout.setGraph(view, anchors, anchors);
      expect(layout.diagnostics().synapses).toBe(4);
      view.edges[4].evidence = { ...base.evidence, retracted: true };
      layout.setGraph(view, anchors, anchors);
      expect(layout.diagnostics().synapses).toBe(3);
      view.edges[4].evidence = base.evidence;
      expect(JSON.stringify(view)).toBe(original);
    } finally {
      clock.mockRestore();
    }
  });

  it('retains all 120 nodes and 512 synapses with finite mechanics and an immediate settle path', () => {
    const { view, anchors } = fixture();
    const initial = new Map(view.nodes.map(node => [node.id, { x: 0, y: 0, z: 0 }]));
    const layout = new PlasticityLayout(120, 512);
    layout.setGraph(view, anchors, initial);
    expect(pointIds(layout)).toEqual(view.nodes.map(node => node.id));
    expect(layout.diagnostics()).toMatchObject({ nodes: 120, synapses: 512, bufferBytes: 22176 });
    for (let frame = 0; frame < 480; frame++) layout.advance(1 / 60);
    expectFinite(layout);
    layout.settle();
    expect(layout.moving).toBe(false);
    expectFinite(layout);
    const settled = [...layout.coordinates];
    layout.setGraph(view, anchors, initial);
    expect(layout.advance(1 / 60)).toBe(false);
    expect([...layout.coordinates]).toEqual(settled);

    const immediate = new PlasticityLayout(120, 512);
    immediate.setGraph(view, anchors, initial);
    immediate.settle();
    expect(immediate.moving).toBe(false);
    expectFinite(immediate);
    expect(pointIds(immediate)).toEqual(pointIds(layout));
  });

  it('allocates each typed array once and reuses its storage across frames and graph replacements', () => {
    const { view, anchors } = fixture();
    const smaller = fixture(12, 30);
    const empty: KnowledgeView = { nodes: [], edges: [], focusId: null, hops: 0 };
    const allocations: Array<{ kind: string; length: number }> = [];
    for (const [kind, constructor] of Object.entries({ Float64Array, Uint16Array, Uint8Array })) {
      vi.stubGlobal(kind, new Proxy(constructor, {
        construct(target, args) {
          const array = Reflect.construct(target, args);
          allocations.push({ kind, length: array.length });
          return array;
        },
      }));
    }
    let layout: PlasticityLayout;
    let buffers: ArrayBufferView[];
    let allocatedAtConstruction: number;
    const apply = () => {};
    try {
      layout = new PlasticityLayout(120, 512);
      allocatedAtConstruction = allocations.length;
      buffers = Object.values(layout).filter(ArrayBuffer.isView);
      layout.setGraph(view, anchors, anchors);
      for (let frame = 0; frame < 120; frame++) {
        layout.advance(1 / 60);
        layout.forEachPoint(apply);
      }
      layout.setGraph(smaller.view, smaller.anchors, smaller.anchors);
      layout.setGraph(empty, anchors, anchors);
      layout.setGraph(view, anchors, anchors);
      layout.settle();
    } finally {
      vi.unstubAllGlobals();
    }
    expect(allocatedAtConstruction).toBe(11);
    expect(allocations).toHaveLength(allocatedAtConstruction);
    expect(allocations.filter(array => array.kind === 'Uint8Array')).toEqual([
      { kind: 'Uint8Array', length: 512 }, { kind: 'Uint8Array', length: 512 },
    ]);
    const after = Object.values(layout).filter(ArrayBuffer.isView);
    expect(after).toHaveLength(buffers.length);
    after.forEach((array, i) => {
      expect(array).toBe(buffers[i]);
      expect(array.buffer).toBe(buffers[i].buffer);
    });
    expect(layout.diagnostics()).toMatchObject({ nodes: 120, synapses: 512, bufferBytes: 22176 });
  });

  it('connects endpoint indices 118 and 119 while leaving disconnected nodes independent', () => {
    const { view } = fixture(120, 0);
    const anchors = new Map(view.nodes.map(node => [node.id, { x: 0, y: 0, z: 0 }]));
    anchors.set(id(118), { x: -.85, y: 0, z: 0 });
    anchors.set(id(119), { x: .85, y: 0, z: 0 });
    view.edges.push({ ...fixture().view.edges[0], source: id(118), target: id(119), weight: 10 });
    const layout = new PlasticityLayout(120, 512);
    layout.setGraph(view, anchors, anchors);
    layout.settle();
    expect(layout.coordinates[119 * 3] - layout.coordinates[118 * 3]).toBeLessThan(1.7);
    expect([...layout.coordinates.slice(0, 118 * 3)]).toEqual(Array(118 * 3).fill(0));
    view.edges = [];
    layout.setGraph(view, anchors, anchors);
    layout.settle();
    expect(layout.coordinates[119 * 3] - layout.coordinates[118 * 3]).toBeCloseTo(1.7, 2);
  });

  it('preserves component root and strength-to-rest-length semantics', () => {
    const ids = ['a', 'b', 'c', 'd', 'e'];
    expect([...connectedComponents(ids, [
      { source: 'b', target: 'a' }, { source: 'd', target: 'c' },
      { source: 'a', target: 'c' }, { source: 'missing', target: 'e' },
    ])]).toEqual([1, 1, 1, 1, 4]);
    expect(connectedComponents([], [])).toEqual(new Uint16Array());
    expect(synapseRestLength(0)).toBe(.75);
    expect(synapseRestLength(.5)).toBe(.525);
    expect(synapseRestLength(1)).toBe(.3);
  });
});
