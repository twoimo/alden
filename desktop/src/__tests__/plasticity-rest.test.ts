import { describe, expect, it } from 'vitest';
import fixture from './fixtures/constrained-rest-20261009.json';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { PlasticityLayout } from '../knowledge/plasticity';

function scene() {
  const graph = parseKnowledgeGraph({ nodes: fixture.nodes, edges: fixture.edges });
  const view = { ...graph, focusId: null, hops: 0 };
  const anchors = new Map(fixture.nodes.map(node => [node.id, node.anchor]));
  const layout = new PlasticityLayout(120, 512);
  layout.setGraph(view, anchors, anchors);
  return { layout, view, anchors };
}

describe('visual rest at constrained equilibrium', () => {
  it('releases interactive motion for a numerically stationary real scene without shifting its final pose', () => {
    const { layout } = scene();
    let frames = 0;
    while (layout.moving && frames < 6000) { layout.advance(1 / 60); frames++; }
    expect(layout.moving).toBe(false);
    expect(frames).toBeLessThan(1200);
    for (let i = 0; i < layout.coordinates.length; i++) {
      expect(Math.abs(layout.coordinates[i] - fixture.oldAt100[i])).toBeLessThan(0.000002);
    }
    const positions = [...layout.coordinates];
    expect(layout.advance(1 / 60)).toBe(false);
    expect([...layout.coordinates]).toEqual(positions);
  });

  it('wakes for a changed target and retains the prior coordinates until the next step', () => {
    const { layout, view, anchors } = scene();
    for (let i = 0; i < 1200; i++) layout.advance(1 / 60);
    const positions = [...layout.coordinates];
    const id = view.nodes[0].id, old = anchors.get(id)!;
    anchors.set(id, { ...old, x: old.x + 0.2 });
    layout.setGraph(view, anchors, anchors);
    expect(layout.moving).toBe(true);
    // Re-projecting a point on the ellipsoid can change its last binary digit.
    for (let i = 0; i < positions.length; i++) expect(Math.abs(layout.coordinates[i] - positions[i])).toBeLessThan(1e-12);
    layout.advance(1 / 60);
    expect([...layout.coordinates]).not.toEqual(positions);
  });
});
