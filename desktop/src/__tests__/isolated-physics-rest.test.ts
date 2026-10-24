import {describe, expect, it} from 'vitest';
import {parseKnowledgeGraph, type KnowledgeView} from '../knowledge/graph-model';
import {PlasticityLayout, type Point3} from '../knowledge/plasticity';

function scene(importance: number, count = 120) {
  const graph = parseKnowledgeGraph({nodes: Array.from({length: count}, (_, i) =>
    ({id: String(i), label: 'anonymous', importance})), edges: []});
  const view: KnowledgeView = {...graph, focusId: null, hops: 0};
  const anchors = new Map(view.nodes.map((node, i) => [node.id,
    {x: Math.sin(i) * .9, y: Math.cos(i) * .7, z: Math.sin(i * .7) * .4}]));
  const initial = new Map(view.nodes.map(node => {const a = anchors.get(node.id)!;
    return [node.id, {x: a.x + .2, y: a.y - .1, z: a.z + .05}] as const;}));
  const model = new PlasticityLayout(120, 144);
  model.setGraph(view, anchors, initial); model.setDisplayScale(.0003);
  return {model, view, anchors};
}

function bounded(model: PlasticityLayout) {
  const state = model.auditState();
  expect(state.coordinates.every(Number.isFinite)).toBe(true);
  for (let i = 0; i < state.nodeCount; i++) {
    const at = i * 3, p = state.coordinates;
    expect(Math.hypot(p[at] / 2.3, p[at + 1] / 1.55, p[at + 2] / 1.1)).toBeLessThanOrEqual(1 + 1e-10);
  }
  expect(model.diagnostics().maxSpeed).toBeLessThanOrEqual(1.4 + 1e-12);
}

function stopped(model: PlasticityLayout, beforeTolerance: number) {
  expect(model.moving).toBe(false);
  expect(model.auditState().visualRestSpeed).toBe(beforeTolerance);
  expect(model.diagnostics().maxPositionSpeed).toBeLessThan(beforeTolerance);
  const points = [...model.coordinates];
  expect(model.advance(2)).toBe(false);
  expect([...model.coordinates]).toEqual(points);
}

describe('independent anchor damping', () => {
  it.each([0, 50, 100])('settles importance %i at the original screen tolerance across frame rates', importance => {
    for (const steps of [[1 / 60], [1 / 30], [1 / 120, .04, .1, 1 / 60]]) {
      const {model, view} = scene(importance), original = JSON.stringify(view);
      const tolerance = model.auditState().visualRestSpeed;
      let elapsed = 0, frame = 0;
      while (model.moving && elapsed < 6) {
        const h = steps[frame++ % steps.length]; model.advance(h); elapsed += h;
      }
      expect(elapsed).toBeLessThan(6);
      bounded(model); stopped(model, tolerance);
      expect(JSON.stringify(view)).toBe(original);
    }
  });

  it('leaves coupled coordinates and velocities unchanged when an independent node is present', () => {
    const graph = parseKnowledgeGraph({nodes: ['a', 'b', 'single'].map(id => ({id, label: id, importance: 100})),
      edges: [{source: 'a', target: 'b', weight: 1}]});
    const full: KnowledgeView = {...graph, focusId: null, hops: 0};
    const pair: KnowledgeView = {...full, nodes: full.nodes.slice(0, 2)};
    const anchors = new Map<string, Point3>([['a', {x: -.85, y: 0, z: 0}],
      ['b', {x: .85, y: 0, z: 0}], ['single', {x: 0, y: .7, z: .2}]]);
    const initial = new Map(anchors); initial.set('single', {x: .2, y: .6, z: .25});
    const a = new PlasticityLayout(120), b = new PlasticityLayout(120);
    a.setGraph(full, anchors, initial); b.setGraph(pair, anchors, initial);
    for (let i = 0; i < 120; i++) {
      a.advance(1 / 60); b.advance(1 / 60);
      expect([...a.coordinates.slice(0, 6)]).toEqual([...b.coordinates.slice(0, 6)]);
      expect(a.auditState().velocity.slice(0, 6)).toEqual(b.auditState().velocity.slice(0, 6));
    }
  });

  it('preserves force/speed caps and ellipsoid contact under long irregular frames', () => {
    const {model, view} = scene(100, 1);
    const anchors = new Map([['0', {x: -2.3, y: 0, z: 0}]]);
    model.setGraph(view, anchors, new Map([['0', {x: 2.3, y: 0, z: 0}]]));
    const tolerance = model.auditState().visualRestSpeed;
    const steps = [1 / 120, .25, .04]; let elapsed = 0, frame = 0;
    while (model.moving && elapsed < 30) {
      const dt = steps[frame++ % steps.length]; model.advance(dt); elapsed += dt;
      bounded(model);
    }
    expect(elapsed).toBeLessThan(30); stopped(model, tolerance);
  });

  it('recomputes singleton behavior after adding and removing a real edge without a position jump', () => {
    const {model, view, anchors} = scene(100, 2);
    for (let i = 0; i < 360 && model.moving; i++) model.advance(1 / 60);
    expect(model.moving).toBe(false);
    const graph = parseKnowledgeGraph({nodes: view.nodes, edges: [{source: '0', target: '1', weight: 1}]});
    const linked: KnowledgeView = {...graph, focusId: null, hops: 0};
    const before = [...model.coordinates]; model.setGraph(linked, anchors, anchors);
    expect([...model.coordinates]).toEqual(before);
    for (let i = 0; i < 120; i++) model.advance(1 / 60);
    const linkedPositions = [...model.coordinates]; model.setGraph(view, anchors, anchors);
    expect([...model.coordinates]).toEqual(linkedPositions);
    const tolerance = model.auditState().visualRestSpeed;
    let frames = 0; while (model.moving && frames < 360) {model.advance(1 / 60); frames++;}
    expect(frames).toBeLessThan(360); bounded(model); stopped(model, tolerance);
  });

  it('settles inherited toward, away and transverse velocity with oblique contact and projected anchors', () => {
    for (const importance of [0, 100]) for (const velocity of [[5, 0, 0], [-5, 0, 0], [0, 3, -4]]) {
      const {model, view} = scene(importance, 1);
      const anchors = new Map([['0', {x: 3, y: -.8, z: .7}]]);
      model.setGraph(view, anchors, new Map([['0', {x: .9, y: .7, z: .6}]]));
      // Anonymous integrator checkpoint in a test only; no runtime import API.
      (model as unknown as {velocity: Float64Array}).velocity.set(velocity);
      const tolerance = model.auditState().visualRestSpeed;
      let elapsed = 0, frame = 0;
      while (model.moving && elapsed < 30) {
        const dt = [1 / 60, .1, .25][frame++ % 3];
        model.advance(dt); elapsed += dt; bounded(model);
      }
      expect(elapsed).toBeLessThan(30); stopped(model, tolerance);
    }
  });
});
