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
function expectFiniteSolve(result: ReturnType<SemanticLayoutEngine['layout']>, pins: ReadonlyMap<string, Point3>): void {
  for (const [id, point] of result.anchors) {
    expect(Object.values(point).every(Number.isFinite), id).toBe(true);
    if (pins.has(id)) {
      expect(point).toEqual(pins.get(id));
      expect(result.dirtyIds.has(id)).toBe(false);
    }
  }
  const d = result.diagnostics;
  expect(d.solverIterations).toBeLessThanOrEqual(limits.solverIterations);
  expect(d.springEvaluations).toBeLessThanOrEqual((d.relationPairs + d.affinityPairs) * limits.solverIterations);
  expect(d.collisionTests).toBeLessThanOrEqual(limits.collisionTests);
  expect(d.densityEvaluations).toBeLessThanOrEqual((d.nodes - d.pinnedNodes) * limits.solverIterations);
  expect(d.neighborhoodDensityEvaluations).toBeLessThanOrEqual((d.nodes - d.pinnedNodes) * limits.solverIterations);
}

describe('bounded semantic overview layout', () => {
  it('ignores unknown pin IDs when choosing a fresh dense layout', () => {
    const view = scene(256);
    for (const pins of [[], ['n0000']]) {
      const previous = new Map([['n0000', { x: 3, y: 0, z: 0 }]]);
      const solve = (ids: string[]) => new SemanticLayoutEngine().layout(view, pins.length ? previous : empty(), [], {
        nowMs: 1000, pinnedIds: new Set(ids), reflow: pins.length > 0,
      });
      const expected = solve(pins), actual = solve([...pins, 'not-in-view']);
      expect(actual.diagnostics.pinnedNodes).toBe(pins.length);
      expect(actual.anchors).toEqual(expected.anchors);
    }
  });

  it('bounds a cached disconnected anchor after releasing an off-center pin without reflowing it', () => {
    const view = scene(3, [R(0, 1)]), engine = new SemanticLayoutEngine();
    const previous = new Map([
      ['n0000', { x: 3, y: 0, z: 0 }], ['n0001', { x: 3.4, y: 0, z: 0 }],
      ['n0002', { x: 4.5, y: 0, z: 0 }],
    ]);
    const focused = engine.layout(view, previous, [], { nowMs: 1000, pinnedIds: new Set(['n0000']) });
    expect(focused.anchors.get('n0000')).toEqual(previous.get('n0000'));
    expect(focused.anchors.get('n0002')!.x).toBeGreaterThan(2.3);
    const released = engine.layout(view, focused.anchors, [], { nowMs: 1000 });
    expect(released.diagnostics.affectedNodes).toBe(2);
    for (const p of released.anchors.values()) expect(Math.hypot(p.x / 2.3, p.y / 1.55, p.z / 1.1)).toBeLessThanOrEqual(1 + 1e-12);
    const cached = engine.layout(view, released.anchors, [], { nowMs: 1000 });
    expect(cached.anchors).toEqual(released.anchors);
    expect(cached.diagnostics.solverIterations).toBe(0);
    expect(engine.checkpoint().positions).toEqual([...cached.anchors]);
  });

  it('ALD-PIN-FRAME-003 uses distinct authoritative multi-pin spring endpoints at x=2.4 and x=6', () => {
    const view = scene(3, [R(0, 2)]), pinnedIds = new Set(['n0000', 'n0001']);
    const solve = (x: number) => {
      const previous = new Map([
        ['n0000', { x, y: 0, z: 0 }], ['n0001', { x: -3, y: 0, z: 0 }],
        ['n0002', { x: 2, y: 0, z: 0 }],
      ]);
      const engine = new SemanticLayoutEngine();
      const result = engine.layout(view, previous, [], { pinnedIds, nowMs: 1000 });
      for (const id of pinnedIds) {
        expect(result.anchors.get(id)).toEqual(previous.get(id));
        expect(new Map(engine.checkpoint().positions).get(id)).toEqual(previous.get(id));
        expect(result.dirtyIds.has(id)).toBe(false);
      }
      return result.anchors.get('n0002')!;
    };
    expect(solve(6).x - solve(2.4).x).toBeGreaterThan(.05);
  });

  it('ALD-PIN-FRAME-003 preserves relative attraction from unequal extreme finite pins', () => {
    const view = scene(3, [R(0, 2), R(1, 2)]);
    for (const magnitude of [1e155, Number.MAX_VALUE]) {
      const previous = new Map([
        ['n0000', { x: magnitude, y: 0, z: 0 }],
        ['n0001', { x: -magnitude / 2, y: 0, z: 0 }],
        ['n0002', { x: 0, y: 0, z: 0 }],
      ]);
      const result = new SemanticLayoutEngine().layout(view, previous, [], {
        pinnedIds: new Set(['n0000', 'n0001']), nowMs: 1000,
      });
      expect(result.anchors.get('n0002')!.x).toBeGreaterThan(.5);
      for (const point of result.anchors.values()) expect(Object.values(point).every(Number.isFinite)).toBe(true);
    }
  });

  it('ALD-PIN-FRAME-003 keeps a single extreme focus finite through collision handling', () => {
    const view = scene(4, [R(0, 1), R(1, 2), R(2, 3)]);
    for (const magnitude of [3, -3, 1e15, 2 ** 54, -(2 ** 54), 2 ** 55, 1e155, -1e155, Number.MAX_VALUE, -Number.MAX_VALUE]) {
      const pin = { x: magnitude, y: magnitude, z: magnitude };
      const engine = new SemanticLayoutEngine();
      const result = engine.layout(view, new Map([['n0000', pin]]), [], {
        pinnedIds: new Set(['n0000']), nowMs: 1000,
      });
      expect(result.anchors.get('n0000')).toEqual(pin);
      expect(result.diagnostics.depthRingNodes).toBe(3);
      for (const point of result.anchors.values()) expect(Object.values(point).every(Number.isFinite)).toBe(true);
      expect(() => new SemanticLayoutEngine().restore(engine.checkpoint())).not.toThrow();
    }
  });

  it('ALD-PIN-FRAME-003 retains local restoring forces when opposite extreme pin forces cancel', () => {
    const view = scene(3, [R(0, 2), R(1, 2)]);
    for (const magnitude of [1e155, Number.MAX_VALUE]) {
      const previous = new Map([
        ['n0000', { x: magnitude, y: 0, z: 0 }],
        ['n0001', { x: -magnitude, y: 0, z: 0 }],
        ['n0002', { x: 1, y: 0, z: 0 }],
      ]);
      const result = new SemanticLayoutEngine().layout(view, previous, [], {
        pinnedIds: new Set(['n0000', 'n0001']), nowMs: 1000,
      });
      // The two large translations cancel, but each spring still restores the
      // movable endpoint toward their midpoint. It must not freeze at x=1.
      expect(result.anchors.get('n0002')!.x).toBeLessThan(.5);
    }
  });

  it.each([Number.MIN_VALUE, 1e-300, 1e15, 2 ** 54, 2 ** 55, 1e99, 1e100, 1e101, 1e154, 1e155, 1e200, Number.MAX_VALUE])(
    'ALD-PIN-FRAME-003 handles mixed pins and movable imports on all axes at %s', magnitude => {
      const view = scene(6, [R(0, 3), R(1, 3), R(2, 4), R(3, 4)]), affinities = [S(4, 5)];
      const previous = new Map([
        ['n0000', { x: magnitude, y: magnitude, z: -magnitude }],
        ['n0001', { x: -magnitude, y: magnitude, z: -magnitude }],
        ['n0002', { x: magnitude / 2, y: -magnitude, z: magnitude }],
        ['n0003', { x: -magnitude / 2, y: magnitude / 2, z: 0 }],
        ['n0004', { x: 0, y: 0, z: 0 }],
        ['n0005', { x: magnitude / 4, y: -magnitude / 4, z: magnitude / 4 }],
      ]);
      const pins = new Map([...previous].slice(0, 3)), pinnedIds = new Set(pins.keys());
      const sourceBefore = structuredClone([view, affinities, previous]);
      const engine = new SemanticLayoutEngine();
      const first = engine.layout(view, previous, affinities, { pinnedIds, nowMs: 1000 });
      expectFiniteSolve(first, pins);
      expect(first.diagnostics.invalidPreviousPositions).toBe(0);
      for (const [id, point] of first.anchors) if (!pins.has(id)) {
        expect(Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1)).toBeLessThanOrEqual(1 + 1e-14);
      }
      const checkpoint = structuredClone(engine.checkpoint()), restored = new SemanticLayoutEngine();
      restored.restore(checkpoint);
      const cached = restored.layout(view, first.anchors, affinities, { pinnedIds, nowMs: 1000 });
      expect(cached.anchors).toEqual(first.anchors);
      expect(cached.diagnostics.solverIterations).toBe(0);
      expect(cached.dirtyIds.size).toBe(0);
      const reflowed = restored.layout(view, cached.anchors, affinities, { pinnedIds, reflow: true, nowMs: 1000 });
      expectFiniteSolve(reflowed, pins);
      for (const [id, point] of pins) expect(new Map(restored.checkpoint().positions).get(id)).toEqual(point);
      expect([view, affinities, previous]).toEqual(sourceBefore);
      expect(checkpoint.positions).toEqual([...first.anchors]);
    },
  );

  it('ALD-PIN-FRAME-003 retains nearby pin collisions and rejects unreachable pins without phantom contacts', () => {
    const view = scene(3), pinnedIds = new Set(['n0000', 'n0001']);
    const solve = (x: number) => new SemanticLayoutEngine().layout(view, new Map([
      ['n0000', { x, y: 0, z: 0 }], ['n0001', { x: -1e155, y: 0, z: 0 }],
      ['n0002', { x: 2.3, y: 0, z: 0 }],
    ]), [], { pinnedIds, nowMs: 1000 });
    const near = solve(2.35), far = solve(6), extreme = solve(Number.MAX_VALUE);
    expect(near.diagnostics.collisionTests).toBeGreaterThan(0);
    expect(near.anchors.get('n0002')!.x).toBeLessThan(2.29);
    expect(near.anchors.get('n0000')!.x).toBe(2.35);
    expect(far.anchors.get('n0002')).toEqual(extreme.anchors.get('n0002'));
    expect(far.anchors.get('n0002')).toEqual({ x: 2.3, y: 0, z: 0 });
    expect(far.diagnostics.collisionTests).toBe(0);
    expect(extreme.diagnostics.collisionTests).toBe(0);
  });

  it('ALD-PIN-FRAME-003 does not rescale an unrelated active component because another component has an extreme pin', () => {
    const view = scene(6, [R(0, 2), R(3, 4), R(4, 5)]), pinnedIds = new Set(['n0000', 'n0001']);
    const solve = (x: number) => new SemanticLayoutEngine().layout(view, new Map([
      ['n0000', { x, y: 0, z: 0 }], ['n0001', { x: -6, y: 0, z: 0 }],
      ['n0002', { x: 2, y: 0, z: 0 }], ['n0003', { x: -1, y: 1, z: 0 }],
      ['n0004', { x: -1.2, y: 1, z: 0 }], ['n0005', { x: -1.4, y: 1, z: 0 }],
    ]), [], { pinnedIds, nowMs: 1000 });
    const ordinary = solve(6), extreme = solve(Number.MAX_VALUE);
    for (const id of ['n0003', 'n0004', 'n0005']) expect(extreme.anchors.get(id)).toEqual(ordinary.anchors.get(id));
  });

  it('ALD-PIN-FRAME-003 preserves F01, unrelated cached components and checkpoints across incremental changes and reflow', () => {
    const edges = [R(0, 2), R(2, 3), R(4, 5)], view = scene(8, edges);
    const pinnedIds = new Set(['n0000', 'n0001']);
    const pins = new Map([['n0000', { x: 2.4, y: 0, z: 0 }], ['n0001', { x: -3, y: 0, z: 0 }]]);
    const engine = new SemanticLayoutEngine(), first = engine.layout(view, pins, [], { pinnedIds, nowMs: 1000 });
    const checkpoint = structuredClone(engine.checkpoint()), prior = new Map(first.anchors);
    pins.set('n0000', { x: 6, y: 0, z: 0 }); prior.set('n0000', pins.get('n0000')!);
    const implicit = new SemanticLayoutEngine(), explicit = new SemanticLayoutEngine();
    implicit.restore(checkpoint); explicit.restore(checkpoint);
    const moved = implicit.layout(view, prior, [], { pinnedIds, nowMs: 1000 });
    const control = explicit.layout(view, prior, [], { pinnedIds, changedIds: new Set(['n0000']), nowMs: 1000 });
    expect(moved.anchors).toEqual(control.anchors);
    expect(moved.diagnostics.affectedNodes).toBe(3);
    expect(changed(first.anchors, moved.anchors, ['n0002', 'n0003']).length).toBeGreaterThan(0);
    for (const id of ['n0001', 'n0004', 'n0005', 'n0006', 'n0007']) expect(moved.anchors.get(id)).toEqual(first.anchors.get(id));
    const grown = scene(9, [...edges, R(3, 8)]);
    const added = implicit.layout(grown, moved.anchors, [S(2, 3, .8)], { pinnedIds, nowMs: 1000 });
    expect(added.anchors.has('n0008')).toBe(true);
    expectFiniteSolve(added, pins);
    expect(added.anchors.get('n0005')).toEqual(first.anchors.get('n0005'));
    const removed = implicit.layout(view, added.anchors, [], { pinnedIds, nowMs: 1000 });
    expect(removed.anchors.has('n0008')).toBe(false);
    expect(new Map(implicit.checkpoint().positions).has('n0008')).toBe(false);
    expectFiniteSolve(removed, pins);
    const reflowed = implicit.layout(view, removed.anchors, [S(2, 3, .9)], { pinnedIds, reflow: true, nowMs: 1000 });
    expect(reflowed.diagnostics.affectedNodes).toBe(8);
    expectFiniteSolve(reflowed, pins);
    const restored = new SemanticLayoutEngine(); restored.restore(structuredClone(implicit.checkpoint()));
    const unchanged = restored.layout(view, reflowed.anchors, [S(2, 3, .9)], { pinnedIds, nowMs: 1000 });
    expect(unchanged.anchors).toEqual(reflowed.anchors);
    expect(unchanged.diagnostics.solverIterations).toBe(0);
    expect(engine.checkpoint()).toEqual(checkpoint);
  });

  it('ALD-PIN-FRAME-003 honors authoritative geometry for semantic-only springs without changing source facts', () => {
    const view = scene(3), affinities = [S(0, 2, .9)], sourceBefore = structuredClone([view, affinities]);
    const solve = (x: number) => new SemanticLayoutEngine().layout(view, new Map([
      ['n0000', { x, y: 0, z: 0 }], ['n0001', { x: -3, y: 0, z: 0 }],
      ['n0002', { x: 2, y: 0, z: 0 }],
    ]), affinities, { pinnedIds: new Set(['n0000', 'n0001']), nowMs: 1000 });
    const near = solve(2.4), far = solve(6);
    expect(far.anchors.get('n0002')!.x - near.anchors.get('n0002')!.x).toBeGreaterThan(.05);
    expect(far.diagnostics.relationPairs).toBe(0);
    expect(far.diagnostics.affinityPairs).toBe(1);
    expect([view, affinities]).toEqual(sourceBefore);
  });

  it('ALD-PIN-FRAME-003 keeps all-pinned extremes exact without inventing movable or collision work', () => {
    const view = scene(3, [R(0, 1), R(1, 2)]);
    const pins = new Map([
      ['n0000', { x: Number.MAX_VALUE, y: -Number.MAX_VALUE, z: Number.MIN_VALUE }],
      ['n0001', { x: -Number.MAX_VALUE, y: Number.MAX_VALUE, z: -Number.MIN_VALUE }],
      ['n0002', { x: 0, y: -0, z: 0 }],
    ]);
    const engine = new SemanticLayoutEngine();
    const result = engine.layout(view, pins, [], { pinnedIds: new Set(pins.keys()), reflow: true, nowMs: 1000 });
    expectFiniteSolve(result, pins);
    expect(result.anchors).toEqual(pins);
    expect(result.diagnostics.solverIterations).toBe(0);
    expect(result.diagnostics.collisionTests).toBe(0);
    expect(() => new SemanticLayoutEngine().restore(engine.checkpoint())).not.toThrow();
  });

  it('ALD-PIN-FRAME-003 keeps a full mixed extreme/crowded snapshot within existing budgets', () => {
    const view = scene(limits.nodes, Array.from({ length: limits.nodes - 1 }, (_, i) => R(i, i + 1)));
    const previous = new Map(view.nodes.map((node, i) => [node.id,
      i < 16 ? { x: i % 2 ? -Number.MAX_VALUE : Number.MAX_VALUE, y: 0, z: 0 } : { x: 0, y: 0, z: 0 },
    ]));
    const pins = new Map([...previous].slice(0, 32)), engine = new SemanticLayoutEngine();
    const result = engine.layout(view, previous, [], { pinnedIds: new Set(pins.keys()), nowMs: 1000 });
    expectFiniteSolve(result, pins);
    expect(result.anchors.size).toBe(limits.nodes);
    expect(result.diagnostics.collisionTests).toBeGreaterThan(0);
    expect(result.diagnostics.collisionCandidatesTruncated).toBe(true);
    expect(changed(previous, result.anchors, ['n2046', 'n2047'])).toHaveLength(2);
    expect(() => new SemanticLayoutEngine().restore(engine.checkpoint())).not.toThrow();
  });

  it('reactivates the affected component when an existing pin moves without a changed-ID hint',()=>{
    const view=scene(6,[R(0,1),R(1,2),R(3,4),R(4,5)]),pins=new Set(['n0000']);
    const initial=new SemanticLayoutEngine();
    const a=initial.layout(view,empty(),[],{pinnedIds:pins,nowMs:1000});
    const positions=new Map(a.anchors),old=positions.get('n0000')!;
    positions.set('n0000',{x:old.x+.35,y:old.y-.2,z:old.z+.1});
    const implicit=new SemanticLayoutEngine(),explicit=new SemanticLayoutEngine();
    implicit.restore(initial.checkpoint());explicit.restore(initial.checkpoint());
    const actual=implicit.layout(view,positions,[],{pinnedIds:pins,nowMs:1001});
    const control=explicit.layout(view,positions,[],{pinnedIds:pins,changedIds:new Set(['n0000']),nowMs:1001});
    expect(actual.diagnostics.affectedNodes).toBe(3);
    expect(actual.anchors).toEqual(control.anchors);
    expect(actual.anchors.get('n0000')).toEqual(positions.get('n0000'));
    for(const id of ['n0003','n0004','n0005'])expect(actual.anchors.get(id)).toEqual(a.anchors.get(id));
  });
  it('does not reintroduce suppressed structural community weight through an admitted semantic pair',()=>{
    const view=scene(4,[R(0,1),R(0,2),R(0,3),R(1,2)]),before=JSON.stringify(view);
    // The 0–3 structural degree ratio is 1/3. Its validated S channel is
    // independent; it must not also enable the excluded R weight in modularity.
    const result=new SemanticLayoutEngine().layout(view,empty(),[S(0,3,.65)],{nowMs:1000});
    expect(new Set(result.clusterIds.values()).size).toBe(1);
    expect(result.diagnostics.affinityPairs).toBe(1);
    expect(JSON.stringify(view)).toBe(before);
  });
  it('retains semantic affinity when structural hub degrees differ', () => {
    const view = scene(40, Array.from({ length: 30 }, (_, i) => R(0, i + 1)));
    const original = JSON.stringify(view);
    const plain = new SemanticLayoutEngine().layout(view, empty(), [], { nowMs: 1000 });
    const semantic = new SemanticLayoutEngine().layout(view, empty(), [S(0, 1, .95)], { nowMs: 1000 });
    expect(plain.clusterIds.get('n0000')).not.toBe(plain.clusterIds.get('n0001'));
    expect(semantic.clusterIds.get('n0000')).toBe(semantic.clusterIds.get('n0001'));
    expect(semantic.diagnostics.affinityPairs).toBe(1);
    expect(JSON.stringify(view)).toBe(original);
  });
  it('does not pull a fresh dense community toward arbitrary provisional ID seeds', () => {
    const count = 64;
    const edges = Array.from({ length: count }, (_, a) =>
      Array.from({ length: count - a - 1 }, (_, offset) => R(a, a + offset + 1))).flat();
    const engine = new SemanticLayoutEngine(), view = scene(count, edges);
    const original = JSON.stringify(view), result = engine.layout(view, empty());
    expect(new Set(result.clusterIds.values()).size).toBe(1);
    const target = new Map(engine.checkpoint().centers).get(result.clusterIds.get('n0000')!)!;
    const mean = [...result.anchors.values()].reduce((sum, point) => ({
      x: sum.x + point.x / count, y: sum.y + point.y / count, z: sum.z + point.z / count,
    }), { x: 0, y: 0, z: 0 });
    // With no prior placement to preserve, the group should stay near its own
    // chosen center rather than drift toward the unrelated per-ID seed mean.
    expect(Math.hypot(mean.x - target.x, mean.y - target.y, mean.z - target.z)).toBeLessThan(.08);
    expect(JSON.stringify(view)).toBe(original);
  });

  it('keeps arrival cohorts together while protecting pins and unrelated communities', () => {
    const id = (i: number) => `n${String(i).padStart(4, '0')}`;
    const old = Array.from({ length: 16 }, (_, i) => i);
    const fresh = Array.from({ length: 16 }, (_, i) => i + 16);
    const other = Array.from({ length: 32 }, (_, i) => i + 32);
    const make = (ids: number[]) => {
      const edges = ids.flatMap((a, at) => ids.slice(at + 1)
        .filter(b => (a < 32) === (b < 32)).map(b => R(a, b)));
      const view = scene(64, edges), selected = new Set(ids.map(id));
      return { ...view, nodes: view.nodes.filter(node => selected.has(node.id)) };
    };
    for (const batches of [[16], [4, 8, 12, 16]]) {
      const engine = new SemanticLayoutEngine();
      let result = engine.layout(make([...old, ...other]), empty());
      const protectedPoints = new Map([0, ...other].map(i => [id(i), result.anchors.get(id(i))!]));
      for (const count of batches) result = engine.layout(
        make([...old, ...fresh.slice(0, count), ...other]), result.anchors, [], { pinnedIds: new Set([id(0)]) });
      const mean = (members: number[]) => members.reduce((sum, i) => {
        const p = result.anchors.get(id(i))!, depth = 4.75 - p.z;
        return { x: sum.x + p.x / depth / members.length, y: sum.y + p.y / depth / members.length };
      }, { x: 0, y: 0 });
      const a = mean(old), n = mean(fresh), b = mean(other);
      const cohortDistance = Math.hypot(a.x - n.x, a.y - n.y);
      const communityDistance = Math.hypot((a.x + n.x) / 2 - b.x, (a.y + n.y) / 2 - b.y);
      expect(cohortDistance).toBeLessThan(communityDistance);
      expect(result.clusterIds.get(id(0))).toBe(result.clusterIds.get(id(16)));
      expect(result.clusterIds.get(id(0))).not.toBe(result.clusterIds.get(id(32)));
      for (const [key, point] of protectedPoints) expect(result.anchors.get(key)).toEqual(point);
    }
  });

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

  it('keeps a saved map within one clearance on ordinary affinity updates and preserves an exact repeat', () => {
    const view = scene(64, Array.from({ length: 63 }, (_, i) => R(0, i + 1)));
    const pairs = Array.from({ length: 31 }, (_, i) => S(i * 2 + 1, i * 2 + 2));
    const engine = new SemanticLayoutEngine(), first = engine.layout(view, empty(), pairs);
    const prior = structuredClone([...first.anchors]);
    const next = engine.layout(view, first.anchors, pairs.slice(1));
    expect(next.diagnostics.affinityPairs).toBe(pairs.length - 1);
    expect(next.dirtyIds.size).toBeGreaterThan(0);
    for (const [id, p] of next.anchors) {
      const old = first.anchors.get(id)!;
      expect(Math.hypot(p.x - old.x, p.y - old.y, p.z - old.z), id).toBeLessThanOrEqual(.140000001);
    }
    expect([...first.anchors]).toEqual(prior);
    const repeat = engine.layout(view, next.anchors, pairs.slice(1));
    expect(repeat.anchors).toEqual(next.anchors);
    expect(repeat.diagnostics.solverIterations).toBe(0);
  });

  it.each([0, 3])('keeps distant cached spokes fixed across repeated affinity-only changes with %i pins', pinCount => {
    const view = scene(64, Array.from({ length: 63 }, (_, i) => R(0, i + 1)));
    const pairs = [S(1, 2), S(2, 3), S(3, 4), S(24, 25)];
    const original = JSON.stringify([view, pairs]);
    const engine = new SemanticLayoutEngine(), first = engine.layout(view, empty(), pairs);
    const pins = new Set(['n0000', 'n0020', 'n0030'].slice(0, pinCount));
    const settled = engine.layout(view, first.anchors, pairs, { pinnedIds: pins });
    const distant = view.nodes.filter(node => Number(node.id.slice(1)) >= 8).map(node => node.id);
    let prior = settled.anchors;
    for (let step = 0; step < 24; step++) {
      const nextPairs = step % 2 ? pairs : pairs.slice(1);
      const before = structuredClone(prior);
      const next = engine.layout(view, prior, nextPairs, { pinnedIds: pins });
      expect(next.anchors.size).toBe(64);
      expect(next.diagnostics.relationPairs).toBe(63);
      expect(next.diagnostics.affinityPairs).toBe(nextPairs.length);
      expect(changed(settled.anchors, next.anchors, distant)).toEqual([]);
      for (const id of pins) expect(next.anchors.get(id)).toEqual(settled.anchors.get(id));
      for (const [id, p] of next.anchors) {
        const old = prior.get(id)!;
        expect(Math.hypot(p.x - old.x, p.y - old.y, p.z - old.z)).toBeLessThanOrEqual(.140000001);
      }
      expect(prior).toEqual(before);
      const unchanged = engine.layout(view, next.anchors, nextPairs, { pinnedIds: pins });
      expect(unchanged.anchors).toEqual(next.anchors);
      expect(unchanged.diagnostics.solverIterations).toBe(0);
      prior = next.anchors;
    }
    expect(JSON.stringify([view, pairs])).toBe(original);
  });

  it('readmits a distant out-of-frame checkpoint even during an affinity-only update', () => {
    const view = scene(64, Array.from({ length: 63 }, (_, i) => R(0, i + 1)));
    const engine = new SemanticLayoutEngine(), pairs = [S(1, 2), S(2, 3), S(3, 4)];
    engine.layout(view, empty(), pairs);
    const checkpoint = structuredClone(engine.checkpoint());
    checkpoint.positions = checkpoint.positions.map(([id, p]) =>
      [id, id === 'n0060' ? { x: Number.MAX_VALUE, y: 0, z: 0 } : p]);
    const prior = new Map(checkpoint.positions), restored = new SemanticLayoutEngine();
    restored.restore(checkpoint);
    const next = restored.layout(view, prior, pairs.slice(1));
    const point = next.anchors.get('n0060')!;
    expect(Object.values(point).every(Number.isFinite)).toBe(true);
    expect(Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1)).toBeLessThanOrEqual(1.000000001);
    expect(prior.get('n0060')!.x).toBe(Number.MAX_VALUE);
  });

  it('restores the incremental bound from a checkpoint while explicit reflow can rebuild the map', () => {
    const view = scene(32, Array.from({ length: 31 }, (_, i) => R(0, i + 1)));
    const engine = new SemanticLayoutEngine(), first = engine.layout(view, empty(), [S(1, 2)]);
    const restored = new SemanticLayoutEngine(); restored.restore(structuredClone(engine.checkpoint()));
    const next = restored.layout(view, first.anchors, [S(3, 4)]);
    for (const [id, p] of next.anchors) {
      const old = first.anchors.get(id)!;
      expect(Math.hypot(p.x - old.x, p.y - old.y, p.z - old.z)).toBeLessThanOrEqual(.140000001);
    }
    const reflow = restored.layout(view, next.anchors, [S(3, 4)], { reflow: true });
    expect([...reflow.anchors].some(([id, p]) => {
      const old = next.anchors.get(id)!;
      return Math.hypot(p.x - old.x, p.y - old.y, p.z - old.z) > .14;
    })).toBe(true);
    expect(reflow.diagnostics.reflow).toBe(true);
  });

  it('readmits an out-of-frame restored prior before applying temporal constraints', () => {
    const view = scene(6, [R(0, 1)]), engine = new SemanticLayoutEngine();
    engine.layout(view, empty(), [S(1, 2)]);
    const checkpoint = structuredClone(engine.checkpoint());
    checkpoint.positions = checkpoint.positions.map(([id, p]) =>
      [id, id === 'n0001' ? { x: Number.MAX_VALUE, y: 0, z: 0 } : p]);
    const prior = new Map(checkpoint.positions), restored = new SemanticLayoutEngine();
    restored.restore(checkpoint);
    const result = restored.layout(view, prior, [S(1, 3)]);
    const point = result.anchors.get('n0001')!;
    expect(Object.values(point).every(Number.isFinite)).toBe(true);
    expect(Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1)).toBeLessThanOrEqual(1.000000001);
    expect(prior.get('n0001')!.x).toBe(Number.MAX_VALUE);
  });

  it('retains the temporal bound for a prior only a rounding step outside the ellipsoid', () => {
    const view = scene(6, [R(0, 1)]), engine = new SemanticLayoutEngine();
    engine.layout(view, empty(), [S(1, 2)]);
    const checkpoint = structuredClone(engine.checkpoint());
    const nearBoundary = { x: 2.3 * (1 + Number.EPSILON), y: 0, z: 0 };
    checkpoint.positions = checkpoint.positions.map(([id, p]) => [id, id === 'n0001' ? nearBoundary : p]);
    const prior = new Map(checkpoint.positions), restored = new SemanticLayoutEngine();
    restored.restore(checkpoint);
    const result = restored.layout(view, prior, [S(1, 3)]), p = result.anchors.get('n0001')!;
    expect(Math.hypot(p.x - nearBoundary.x, p.y, p.z)).toBeLessThanOrEqual(.140000001);
    expect(Math.hypot(p.x / 2.3, p.y / 1.55, p.z / 1.1)).toBeLessThanOrEqual(1.000000001);
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

  it('explicit overview reflow forgets dense legacy coordinates but preserves every identity', () => {
    const view = scene(512), original = JSON.stringify(view);
    const crowded = new Map(view.nodes.map(node => [node.id, { x: 0, y: 0, z: 0 }]));
    const engine = new SemanticLayoutEngine();
    const persisted = engine.layout(view, crowded);
    const reflowed = engine.layout(view, persisted.anchors, [], { reflow: true });
    const coldReflow = new SemanticLayoutEngine().layout(view, empty(), [], { reflow: true });
    expect(reflowed.anchors).toEqual(coldReflow.anchors);
    expect(reflowed.anchors.size).toBe(512);
    expect(new Set(reflowed.clusterIds.values()).size).toBe(512);
    const nearCore = [...reflowed.anchors.values()].filter(point => Math.hypot(point.x, point.y) < .25).length;
    expect(nearCore).toBeLessThan(view.nodes.length * .2);
    expect(reflowed.diagnostics.reflow).toBe(true);
    expect(JSON.stringify(view)).toBe(original);
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
    expect(d.densityEvaluations).toBeLessThanOrEqual(limits.nodes * limits.solverIterations);
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

  it('separates late IDs in a crowded coincident snapshot without dropping their identities', () => {
    const view = scene(2048), previous = new Map(view.nodes.map(n => [n.id, {x:0,y:0,z:0}]));
    const original=JSON.stringify(view), result=new SemanticLayoutEngine().layout(view,previous);
    for(const node of view.nodes.slice(1024)) {
      const p=result.anchors.get(node.id)!;
      expect(Math.hypot(p.x,p.y,p.z)).toBeGreaterThan(.015);
    }
    expect(result.anchors.size).toBe(2048);
    expect(new Set(result.clusterIds.values()).size).toBe(2048);
    expect(JSON.stringify(view)).toBe(original);
    expect(result.diagnostics.collisionTests).toBeLessThanOrEqual(limits.collisionTests);
  });

  it('keeps crowded pins exact while giving late movable nodes separation work', () => {
    const view=scene(2048), previous=new Map(view.nodes.map(n=>[n.id,{x:0,y:0,z:0}]));
    const pinned=new Set(view.nodes.slice(1024,1040).map(n=>n.id));
    const result=new SemanticLayoutEngine().layout(view,previous,[],{pinnedIds:pinned});
    for(const id of pinned) expect(result.anchors.get(id)).toEqual(previous.get(id));
    for(const node of view.nodes.slice(1040)) {
      const p=result.anchors.get(node.id)!;
      expect(Math.hypot(p.x,p.y,p.z)).toBeGreaterThan(.015);
    }
  });

  it('takes the parent validated channel by default without changing fact edges', () => {
    const view = scene(2);
    view.layoutAffinity = { state: 'bounded_ready', inputKey: 'validated', profileKey: 'existing', inputNodes: [],
      pairs: [S(0, 1)], requestedNodes: 2, usableNodes: 2, contentExcludedNodes: 0, validationRejectedNodes: 0,
      evaluatedPairs: 1, truncatedCandidates: 0 };
    const result = new SemanticLayoutEngine().layout(view, empty());
    expect(result.diagnostics.affinityPairs).toBe(1); expect(view.edges).toEqual([]);
  });

  it('pulls equal-energy nodes that share a direction closer than a hub spoke', () => {
    const edges = [R(0, 1), R(0, 2), R(0, 3), R(0, 4)];
    const hub = new SemanticLayoutEngine().layout(scene(5, edges), empty());
    const aligned = new SemanticLayoutEngine().layout(scene(5, edges), empty(), [S(1, 2, .96)]);
    expect(distance(aligned.anchors, 'n0001', 'n0002')).toBeLessThan(distance(hub.anchors, 'n0001', 'n0002'));
    expect(distance(aligned.anchors, 'n0001', 'n0002')).toBeLessThan(distance(aligned.anchors, 'n0001', 'n0003'));
    expect(distance(aligned.anchors, 'n0001', 'n0002')).toBeGreaterThan(.05);
    expect(aligned.anchors.size).toBe(5);
  });

  it('keeps unequal hub spokes out of the hub community and farther apart than an equal pair', () => {
    const edges = [R(0, 1), R(0, 2), R(0, 3), R(0, 4), R(0, 5)];
    const result = new SemanticLayoutEngine().layout(scene(6, edges), empty(), [S(1, 2, .96)]);
    const hub = result.clusterIds.get('n0000');
    expect(result.clusterIds.get('n0001')).toBe(result.clusterIds.get('n0002'));
    expect(result.clusterIds.get('n0001')).not.toBe(hub);
    for (const id of ['n0003', 'n0004', 'n0005']) expect(result.clusterIds.get(id)).not.toBe(hub);
    expect(distance(result.anchors, 'n0001', 'n0002')).toBeLessThan(distance(result.anchors, 'n0000', 'n0003'));
    expect(distance(result.anchors, 'n0000', 'n0003')).toBeGreaterThan(.2);
  });

  it('separates two cliques joined by one unequal spoke farther than an intra-clique pair', () => {
    const edges = [];
    for (let a = 0; a < 8; a++) for (let b = a + 1; b < 8; b++) edges.push(R(a, b));
    for (let a = 8; a < 11; a++) for (let b = a + 1; b < 11; b++) edges.push(R(a, b));
    edges.push(R(0, 8));
    const result = new SemanticLayoutEngine().layout(scene(11, edges), empty());
    expect(result.clusterIds.get('n0000')).toBe(result.clusterIds.get('n0001'));
    expect(result.clusterIds.get('n0008')).toBe(result.clusterIds.get('n0009'));
    expect(result.clusterIds.get('n0000')).not.toBe(result.clusterIds.get('n0008'));
    const mean = (ids: string[]) => ids.reduce((sum, id) => {
      const point = result.anchors.get(id)!;
      return { x: sum.x + point.x / ids.length, y: sum.y + point.y / ids.length, z: sum.z + point.z / ids.length };
    }, { x: 0, y: 0, z: 0 });
    const left = mean(['n0000', 'n0001', 'n0002', 'n0003', 'n0004', 'n0005', 'n0006', 'n0007']);
    const right = mean(['n0008', 'n0009', 'n0010']);
    const gap = Math.hypot(left.x - right.x, left.y - right.y, left.z - right.z);
    expect(gap).toBeGreaterThan(distance(result.anchors, 'n0000', 'n0001'));
    expect(gap).toBeGreaterThan(distance(result.anchors, 'n0008', 'n0009'));
    expect(result.diagnostics.relationPairs).toBe(edges.length);
  });

  it('preserves the centroid while separating outer multi-member centers', () => {
    const edges = [];
    for (let a = 0; a < 8; a++) for (let b = a + 1; b < 8; b++) edges.push(R(a, b));
    for (let a = 8; a < 11; a++) for (let b = a + 1; b < 11; b++) edges.push(R(a, b));
    edges.push(R(0, 8));
    const engine = new SemanticLayoutEngine();
    const view = scene(11, edges);
    const first = engine.layout(view, empty());
    const left = first.clusterIds.get('n0000')!;
    const right = first.clusterIds.get('n0008')!;
    expect(left).not.toBe(right);
    const checkpoint = engine.checkpoint();
    checkpoint.centers = checkpoint.centers.map(([label, point]) => {
      if (label === left) return [label, { x: 1.8, y: 0, z: 0 }] as [string, Point3];
      if (label === right) return [label, { x: 1.9, y: 0, z: 0 }] as [string, Point3];
      return [label, point] as [string, Point3];
    });
    engine.restore(checkpoint);
    engine.layout(view, first.anchors);
    const next = new Map(engine.checkpoint().centers);
    const a = next.get(left)!, b = next.get(right)!;
    expect(Math.abs((a.x + b.x) / 2 - 1.85)).toBeLessThan(1e-6);
    expect(Math.abs(a.y + b.y)).toBeLessThan(1e-6);
    expect(Math.abs(a.z + b.z)).toBeLessThan(1e-6);
  });

  it('keeps later relation hops farther from one pinned focus', () => {
    const edges = [R(0, 1), R(1, 2), R(2, 3)];
    const result = new SemanticLayoutEngine().layout(scene(4, edges), empty(), [], { pinnedIds: new Set(['n0000']) });
    const root = result.anchors.get('n0000')!;
    const radius = (id: string) => {
      const point = result.anchors.get(id)!;
      return Math.hypot(point.x - root.x, point.y - root.y, point.z - root.z);
    };
    expect(result.diagnostics.depthRingNodes).toBe(3);
    expect(radius('n0001')).toBeGreaterThan(.12);
    expect(radius('n0002')).toBeGreaterThan(radius('n0001'));
    expect(radius('n0003')).toBeGreaterThan(radius('n0002'));
  });

  it('keeps an off-center pin fixed and does not clamp its outer hop back into the origin box', () => {
    const edges = [R(0, 1), R(1, 2), R(2, 3)];
    const view = scene(4, edges);
    const pin = { x: 3, y: 0, z: 0 };
    const positions = new Map<string, { x: number; y: number; z: number }>([
      ['n0000', pin],
      ['n0001', { x: 3.42, y: 0, z: 0 }],
      ['n0002', { x: 3.86, y: 0, z: 0 }],
      ['n0003', { x: 4.28, y: 0, z: 0 }],
    ]);
    const result = new SemanticLayoutEngine().layout(view, positions, [], { pinnedIds: new Set(['n0000']) });
    expect(result.anchors.get('n0000')).toEqual(pin);
    expect(result.diagnostics.pinnedNodes).toBe(1);
    expect(result.diagnostics.depthRingNodes).toBe(3);
    const radius = (id: string) => {
      const point = result.anchors.get(id)!;
      return Math.hypot(point.x - pin.x, point.y - pin.y, point.z - pin.z);
    };
    expect(radius('n0002')).toBeGreaterThan(radius('n0001'));
    expect(radius('n0003')).toBeGreaterThan(radius('n0002'));
    expect(result.anchors.get('n0003')!.x).toBeGreaterThan(2.4);
  });

  it('reports final median distance of high-cosine pairs below low-cosine pairs', () => {
    const pairs: LayoutAffinityPair[] = [];
    for (let a = 0; a < 8; a++) for (let b = a + 1; b < 8; b++) {
      const same = a < 4 === b < 4;
      pairs.push(S(a, b, same ? .95 : .2));
    }
    const result = new SemanticLayoutEngine().layout(scene(8), empty(), pairs);
    const d = result.diagnostics;
    expect(d.semanticHighPairs).toBeGreaterThan(0);
    expect(d.semanticLowPairs).toBeGreaterThan(0);
    expect(d.semanticHighMedianDistance).not.toBeNull();
    expect(d.semanticLowMedianDistance).not.toBeNull();
    expect(d.semanticHighMedianDistance!).toBeLessThan(d.semanticLowMedianDistance!);
    expect(d.semanticHighMedianDistance!).toBeGreaterThan(.05);
    expect(d.semanticUpperPairs).toBeGreaterThan(0);
    expect(d.semanticLowerPairs).toBeGreaterThan(0);
    expect(d.semanticUpperMedianDistance!).toBeLessThan(d.semanticLowerMedianDistance!);
    expect(d.semanticCosineMax).toBeGreaterThan(d.semanticCosineMin!);
  });

  it('keeps stronger semantic pairs closer while one pinned focus forms rings', () => {
    const pairs: LayoutAffinityPair[] = [];
    for (let a = 1; a < 8; a++) for (let b = a + 1; b < 8; b++) {
      const same = a < 4 === b < 4;
      pairs.push(S(a, b, same ? .95 : .2));
    }
    const result = new SemanticLayoutEngine().layout(scene(8, [R(0, 1), R(1, 2)]), empty(), pairs, { pinnedIds: new Set(['n0000']) });
    const d = result.diagnostics;
    expect(d.pinnedNodes).toBe(1);
    expect(d.depthRingNodes).toBeGreaterThan(0);
    expect(d.semanticHighMedianDistance).not.toBeNull();
    expect(d.semanticHighMedianDistance!).toBeLessThan(d.semanticLowMedianDistance!);
  });

  it('places a fresh dense overview on energy rays instead of one filled ellipsoid', () => {
    const edges = [];
    for (let a = 0; a < 8; a++) for (let b = a + 1; b < 8; b++) edges.push(R(a, b));
    for (let a = 8; a < 16; a++) for (let b = a + 1; b < 16; b++) edges.push(R(a, b));
    for (let spoke = 16; spoke < 20; spoke++) edges.push(R(0, spoke));
    const result = new SemanticLayoutEngine().layout(scene(256, edges), empty());
    const id = (n: number) => `n${String(n).padStart(4, '0')}`;
    const mean = (nums: number[]) => nums.reduce((sum, n) => {
      const point = result.anchors.get(id(n))!;
      return { x: sum.x + point.x / nums.length, y: sum.y + point.y / nums.length, z: sum.z + point.z / nums.length };
    }, { x: 0, y: 0, z: 0 });
    const unit = (point: { x: number; y: number; z: number }) => {
      const length = Math.hypot(point.x, point.y, point.z) || 1;
      return { x: point.x / length, y: point.y / length, z: point.z / length, radius: length };
    };
    const ellip = (n: number) => {
      const point = result.anchors.get(id(n))!;
      return Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1);
    };
    const left = unit(mean([1, 2, 3, 4, 5, 6, 7]));
    const right = unit(mean([8, 9, 10, 11, 12, 13, 14, 15]));
    const spokes = unit(mean([16, 17, 18, 19]));
    const dot = (a: { x: number; y: number; z: number }, b: { x: number; y: number; z: number }) => a.x * b.x + a.y * b.y + a.z * b.z;
    expect(result.clusterIds.get(id(1))).toBe(result.clusterIds.get(id(2)));
    expect(result.clusterIds.get(id(1))).not.toBe(result.clusterIds.get(id(8)));
    expect(result.clusterIds.get(id(16))).not.toBe(result.clusterIds.get(id(0)));
    expect(dot(left, right)).toBeLessThan(.35);
    expect(dot(spokes, left)).toBeGreaterThan(.75);
    expect(dot(spokes, right)).toBeLessThan(dot(spokes, left));
    const cliqueRadius = [1, 2, 3, 4, 5, 6, 7].map(ellip).sort((a, b) => a - b)[3];
    const isolateRadius = Array.from({ length: 80 }, (_, n) => ellip(40 + n)).sort((a, b) => a - b)[40];
    expect(isolateRadius).toBeGreaterThan(cliqueRadius + .15);
    expect(distance(result.anchors, id(1), id(2))).toBeLessThan(distance(result.anchors, id(1), id(8)));
  });

  it('keeps two large fresh stars apart and open instead of one central point', () => {
    const edges = [];
    for (let member = 1; member <= 40; member++) edges.push(R(0, member));
    for (let member = 42; member <= 81; member++) edges.push(R(41, member));
    const result = new SemanticLayoutEngine().layout(scene(256, edges), empty());
    const id = (n: number) => `n${String(n).padStart(4, '0')}`;
    const mean = (nums: number[]) => nums.reduce((sum, n) => {
      const point = result.anchors.get(id(n))!;
      return { x: sum.x + point.x / nums.length, y: sum.y + point.y / nums.length, z: sum.z + point.z / nums.length };
    }, { x: 0, y: 0, z: 0 });
    const left = mean([1, 2, 3, 4, 5, 6, 7, 8]);
    const right = mean([42, 43, 44, 45, 46, 47, 48, 49]);
    const dot = left.x * right.x + left.y * right.y + left.z * right.z;
    const leftLength = Math.hypot(left.x, left.y, left.z) || 1;
    const rightLength = Math.hypot(right.x, right.y, right.z) || 1;
    expect(result.clusterIds.get(id(1))).not.toBe(result.clusterIds.get(id(42)));
    expect(dot / (leftLength * rightLength)).toBeLessThan(.35);
    const spread = [1, 2, 3, 4, 5, 6, 7, 8].map(n => distance(result.anchors, id(0), id(n)));
    spread.sort((a, b) => a - b);
    expect(spread[4]).toBeGreaterThan(.15);
    const rim = (n: number) => {
      const point = result.anchors.get(id(n))!;
      return Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1);
    };
    expect(rim(0)).toBeGreaterThan(.72);
    expect(rim(41)).toBeGreaterThan(.72);
    expectFiniteSolve(result, empty());
  });

  it('keeps a generic bucket off the rim star reserved for a saved subject', () => {
    const edges = [];
    for (let member = 1; member <= 40; member++) edges.push(R(0, member));
    for (let member = 42; member <= 71; member++) edges.push(R(41, member));
    const view = scene(256, edges);
    view.nodes = view.nodes.map(node => node.id === 'n0000'
      ? { ...node, label: '기타·미분류', category: 'topic' }
      : node.id === 'n0041'
        ? { ...node, label: '채용공고·지원시기', category: 'topic' }
        : node);
    const result = new SemanticLayoutEngine().layout(view, empty());
    const ellip = (id: string) => {
      const point = result.anchors.get(id)!;
      return Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1);
    };
    expect(ellip('n0041')).toBeGreaterThan(.72);
    expect(ellip('n0000')).toBeLessThan(ellip('n0041') - .15);
    expectFiniteSolve(result, empty());
  });

  it('pulls a repeated saved label together without collapsing a uniform fixture', () => {
    const nodes = Array.from({ length: 256 }, (_, i) => ({
      id: `n${String(i).padStart(4, '0')}`,
      label: i < 12 ? 'repeated topic' : `distinct ${i}`,
      source_target: 'source-a', space: 'same source', source_version: 'v1', importance: 20,
    }));
    const view = { ...parseKnowledgeGraph({ nodes, edges: [] }), focusId: null, hops: 0 };
    const result = new SemanticLayoutEngine().layout(view, empty());
    const id = (n: number) => `n${String(n).padStart(4, '0')}`;
    expect(distance(result.anchors, id(0), id(1))).toBeLessThan(distance(result.anchors, id(0), id(40)));
    expect(distance(result.anchors, id(0), id(11))).toBeLessThan(.5);
    expectFiniteSolve(result, empty());
  });

  it('does not gather ascii code hubs while repeated topic labels stay together', () => {
    const nodes = Array.from({ length: 256 }, (_, i) => ({
      id: `n${String(i).padStart(4, '0')}`,
      label: i < 12 ? 'overlay' : i < 24 ? '반복 주제' : `distinct ${i}`,
      category: i < 12 ? 'hub' : 'document',
      source_target: 'source-a', space: 'same source', source_version: 'v1', importance: 20,
    }));
    const view = { ...parseKnowledgeGraph({ nodes, edges: [] }), focusId: null, hops: 0 };
    const result = new SemanticLayoutEngine().layout(view, empty());
    const id = (n: number) => `n${String(n).padStart(4, '0')}`;
    expect(distance(result.anchors, id(12), id(13))).toBeLessThan(.5);
    expect(distance(result.anchors, id(0), id(1))).toBeGreaterThan(distance(result.anchors, id(12), id(13)));
    expectFiniteSolve(result, empty());
  });
});
