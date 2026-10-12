// @vitest-environment happy-dom
import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConstellationNodes, minimumStarRadius } from '../knowledge/constellation';
import { KnowledgeHologram } from '../knowledge/hologram';
import type { KnowledgeEdge, KnowledgeNode } from '../knowledge/graph-model';
import { pickKnowledgeSphere } from '../knowledge/picking';

const owned: ConstellationNodes[] = [];
const evidence = () => ({
  kind: 'snapshot' as const, sourceEventIds: [], chatId: '', confirmedAt: null, retracted: false,
});
const node = (id: string, degree = 0): KnowledgeNode => ({
  id, label: id, category: 'fixture', importance: 0, updatedAt: 0, evidence: evidence(),
  degree, degreeScope: 'permitted filtered graph',
});
const edge = (source: string, target: string): KnowledgeEdge => ({
  source, target, relation: 'related', context: '', weight: 1, roomId: '',
  validFrom: '', validTo: '', evidenceMessageId: '', evidence: evidence(),
});
const group = (capacity = 2048): ConstellationNodes => {
  const result = new ConstellationNodes(capacity);
  owned.push(result);
  return result;
};
const stars = (value: ConstellationNodes): THREE.InstancedMesh => value.children[0] as THREE.InstancedMesh;
const matrix = (mesh: THREE.InstancedMesh, index: number): THREE.Matrix4 => {
  const result = new THREE.Matrix4();
  mesh.getMatrixAt(index, result);
  return result;
};
const collapsed = (mesh: THREE.InstancedMesh, index: number): void => {
  expect(matrix(mesh, index).elements).toEqual([
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
  ]);
};

// Check the public helper against an independent Three.js perspective projection.
// CSS size, rather than the drawing-buffer size, determines the desired radius.
function projectedCssRadius(radius: number, distance: number, fov: number, width: number, height: number): number {
  const camera = new THREE.PerspectiveCamera(fov, width / height, .01, 1000);
  camera.position.set(0, 0, distance);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  const center = new THREE.Vector3().project(camera);
  const rim = new THREE.Vector3(radius, 0, 0).project(camera);
  return (rim.x - center.x) * width / 2;
}

afterEach(() => {
  vi.restoreAllMocks();
  for (const value of owned.splice(0)) {
    const mesh = stars(value);
    mesh.dispose();
    mesh.geometry.dispose();
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) material.dispose();
  }
});

describe('sparse-visibility world-space minimum', () => {
  it('uses 1.5 CSS-pixel radius for 1 through 24 nodes and the existing 0.6 for 25+', () => {
    for (const [count, expected] of [[1, 1.5], [12, 1.5], [24, 1.5], [25, .6], [120, .6], [2048, .6]] as const) {
      const radius = minimumStarRadius(4, 50, 240, count);
      expect(radius).toBeGreaterThan(0);
      expect(projectedCssRadius(radius, 4, 50, 480, 240)).toBeCloseTo(expected, 9);
    }
    expect(minimumStarRadius(4, 50, 240, 0)).toBe(0);
    expect(minimumStarRadius(4, 50, 240, 24)).toBeGreaterThan(minimumStarRadius(4, 50, 240, 25));
  });

  it('tracks perspective FOV, zoom and CSS viewport resizing without changing the pixel target', () => {
    const scenes = [
      { distance: 2.3, fov: 35, width: 960, height: 540 },
      { distance: 8, fov: 65, width: 640, height: 360 },
      { distance: 4, fov: 50, width: 480, height: 240 },
      { distance: 4, fov: 50, width: 1440, height: 720 },
    ];
    for (const scene of scenes) {
      for (const [count, pixels] of [[1, 1.5], [24, 1.5], [25, .6]] as const) {
        const radius = minimumStarRadius(scene.distance, scene.fov, scene.height, count);
        expect(projectedCssRadius(radius, scene.distance, scene.fov, scene.width, scene.height))
          .toBeCloseTo(pixels, 9);
      }
    }
    expect(minimumStarRadius(8, 50, 360, 24)).toBeCloseTo(2 * minimumStarRadius(4, 50, 360, 24), 12);
    expect(minimumStarRadius(4, 50, 720, 24)).toBeCloseTo(minimumStarRadius(4, 50, 360, 24) / 2, 12);
  });

  it('returns zero for an empty graph or invalid physical inputs', () => {
    const invalid: ReadonlyArray<readonly [number, number, number, number]> = [
      [4, 50, 240, 0], [4, 50, 240, -1], [4, 50, 240, 1.5],
      [4, 50, 240, NaN], [4, 50, 240, Infinity],
      [0, 50, 240, 1], [-4, 50, 240, 1], [NaN, 50, 240, 1], [Infinity, 50, 240, 1],
      [4, 0, 240, 1], [4, -10, 240, 1], [4, 180, 240, 1], [4, 181, 240, 1],
      [4, NaN, 240, 1], [4, Infinity, 240, 1],
      [4, 50, 0, 1], [4, 50, -1, 1], [4, 50, NaN, 1], [4, 50, Infinity, 1],
    ];
    for (const values of invalid) expect(minimumStarRadius(...values)).toBe(0);
  });

  it('uses the current view node count and CSS height in the actual hologram frame, regardless of DPR', () => {
    const nodes = Array.from({ length: 25 }, (_, index) => node(`n${index}`));
    const positions = new Map(nodes.map((value, index) => [value.id, new THREE.Vector3(index / 100, 0, 0)]));
    const measured: number[] = [];

    for (const dpr of [1, 2, 3]) {
      const value = group();
      value.set(nodes.slice(0, 24), []);
      const camera = new THREE.PerspectiveCamera(50, 2, .1, 60);
      camera.position.set(0, 0, 4);
      camera.lookAt(0, 0, 0);
      camera.updateMatrixWorld(true);
      const lookAt = new THREE.Vector3();
      const view = { nodes: nodes.slice(0, 24), edges: [], hops: 0, focusId: null };
      const noop = vi.fn();
      const bridge = () => ({ moving: false, update: noop, mesh: { visible: false, geometry: { instanceCount: 0 } } });
      const frame = Object.create(KnowledgeHologram.prototype) as { render(dt: number): void; renderedFrames: number; renderedNodeCount: number; disposed: boolean };
      Object.assign(frame, {
        disposed: false, paused: true, reducedMotion: true, motion: { matches: true },
        renderedFrames: 0, renderedNodeCount: 0,
        camera, lookAt, desiredCamera: camera.position.clone(), desiredLookAt: lookAt.clone(),
        projected: new THREE.Vector3(), viewport: { x: 0, y: 0, width: 480, height: 240 },
        plasticity: { moving: false, forEachPoint: noop, setDisplayScale: noop },
        positionTransition: { moving: false },
        positions, displayedPositions: positions, constellationNodes: value, view,
        neuronDetail: false, nodeMeshes: new Map(), orbMeshes: new Map(),
        synapses: bridge(), overviewSynapses: bridge(), nebulae: { moving: false },
        orbit: { target: lookAt.clone(), update: noop }, orbitActive: false,
        loop: { setInteractive: noop, setVoiceActive: noop, stop: noop },
        latestVoice: null, voiceEnvelope: { needsFrame: false, displayedRms: 0 },
        renderer: { getPixelRatio: () => dpr, render: noop }, scene: new THREE.Scene(),
        labels: new Map(), labelBounds: [], graphRoot: new THREE.Group(),
      });

      frame.render(1 / 60);
      expect(frame.renderedFrames).toBe(1);
      expect(frame.renderedNodeCount).toBe(24);
      expect(projectedCssRadius(value.radius('n0'), 4, 50, 480, 240)).toBeCloseTo(1.5, 5);
      measured.push(value.radius('n0'));

      view.nodes = nodes;
      value.set(nodes, []);
      frame.render(1 / 60);
      expect(frame.renderedFrames).toBe(2);
      expect(frame.renderedNodeCount).toBe(25);
      expect(projectedCssRadius(value.radius('n0'), 4, 50, 480, 240)).toBeCloseTo(.6, 5);
      view.nodes = nodes.slice(0, 24);
      Object.assign(frame, { renderer: { render: () => { throw new Error('synthetic render failure'); } } });
      expect(() => frame.render(1 / 60)).toThrow('synthetic render failure');
      expect(frame.renderedFrames).toBe(2);
      expect(frame.renderedNodeCount).toBe(25);
      frame.disposed = true;
      expect(() => frame.render(1 / 60)).not.toThrow();
      expect(frame.renderedFrames).toBe(2);
    }
    expect(measured[1]).toBeCloseTo(measured[0], 12);
    expect(measured[2]).toBeCloseTo(measured[0], 12);
  });

  it('preserves graph data and positions, synchronizes the CPU picker, and caps the world floor at .03', () => {
    const value = group(), mesh = stars(value);
    const nodes = [node('small'), node('degree-32', 32)];
    const edges = [edge('small', 'degree-32')];
    const positions = new Map([
      ['small', { x: .5, y: -.75, z: -2 }],
      ['degree-32', { x: 1.25, y: .25, z: -3 }],
    ]);
    const snapshot = JSON.stringify({ nodes, edges, positions: [...positions] });
    value.set(nodes, edges);
    const physical = new Map(nodes.map(value => [value.id, groupRadius(value.id)]));
    function groupRadius(id: string): number { return value.radius(id); }

    const floor = minimumStarRadius(4, 50, 360, nodes.length);
    value.setMinimumRadius(floor);
    value.update(positions);
    expect(value.radius('small')).toBeCloseTo(floor, 12);
    expect(value.radius('degree-32')).toBeCloseTo(physical.get('degree-32')!, 12);
    for (const [slot, nodeId] of nodes.map((n, i) => [i, n.id] as const)) {
      const m = matrix(mesh, slot).elements;
      expect(m[0]).toBeCloseTo(value.radius(nodeId), 7);
      expect(m[5]).toBeCloseTo(value.radius(nodeId), 7);
      expect(m.slice(12, 15)).toEqual(Object.values(positions.get(nodeId)!));
    }

    const proxy = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshBasicMaterial());
    try {
      proxy.scale.setScalar(value.radius('small'));
      proxy.position.set(.5, -.75, -2);
      const ray = new THREE.Raycaster(new THREE.Vector3(.5 + value.radius('small') * .8, -.75, 5),
        new THREE.Vector3(0, 0, -1));
      expect(pickKnowledgeSphere(ray, [proxy])).toBe(proxy);
      ray.ray.origin.x = .5 + value.radius('small') * 1.2;
      expect(pickKnowledgeSphere(ray, [proxy])).toBeNull();
    } finally { proxy.geometry.dispose(); proxy.material.dispose(); }

    value.setMinimumRadius(minimumStarRadius(20, 65, 120, 2));
    value.update(positions);
    expect(value.radius('small')).toBe(.03);
    expect(matrix(mesh, 0).elements[0]).toBeCloseTo(.03, 7);
    value.setMinimumRadius(0);
    value.update(positions);
    for (const id of physical.keys()) expect(value.radius(id)).toBeCloseTo(physical.get(id)!, 12);
    expect(JSON.stringify({ nodes, edges, positions: [...positions] })).toBe(snapshot);

    value.setMinimumRadius(floor);
    value.update(new Map([['small', { x: NaN, y: 0, z: 0 }]]));
    collapsed(mesh, 0);
    collapsed(mesh, 1);
    expect(mesh.visible).toBe(false);
    expect([...mesh.instanceMatrix.array].every(Number.isFinite)).toBe(true);
    expect(value.radius('missing')).toBe(0);
  });

  it('keeps one bounded GPU batch while nodes appear, retire, and disappear', () => {
    const value = group(), mesh = stars(value);
    const geometry = mesh.geometry, material = mesh.material;
    const matrices = mesh.instanceMatrix, matrixBuffer = matrices.array;
    const colors = mesh.instanceColor!, colorBuffer = colors.array;
    expect(geometry).toBeInstanceOf(THREE.PlaneGeometry);
    expect(geometry.getAttribute('position').count).toBe(4);
    expect(geometry.index!.count).toBe(6);
    expect(matrices.count).toBe(2048);
    expect(colors.count).toBe(2048);

    for (const count of [1, 24, 25, 120, 2050, 1, 0]) {
      const nodes = Array.from({ length: count }, (_, index) => node(`n${index}`));
      value.set(nodes, []);
      value.setMinimumRadius(minimumStarRadius(4, 50, 240, count));
      value.update(new Map(nodes.map((n, index) => [n.id, { x: index / 100, y: 0, z: -2 }])));
      expect(mesh.count).toBe(Math.min(2048, count));
      expect(mesh.visible).toBe(count > 0);
      expect(mesh.children).toHaveLength(0);
      expect(value.children).toEqual([mesh]);
      expect(value.getObjectByProperty('type', 'Points')).toBeUndefined();
      expect(mesh.geometry).toBe(geometry);
      expect(mesh.material).toBe(material);
      expect(mesh.instanceMatrix).toBe(matrices);
      expect(mesh.instanceMatrix.array).toBe(matrixBuffer);
      expect(mesh.instanceColor).toBe(colors);
      expect(mesh.instanceColor!.array).toBe(colorBuffer);
    }
    expect(value.radius('n2047')).toBe(0);
    expect(value.radius('n0')).toBe(0);
    expect(mesh.count).toBe(0);
    expect(mesh.visible).toBe(false);
    for (const slot of [0, 1, 24, 119, 2047]) collapsed(mesh, slot);
  });
});
