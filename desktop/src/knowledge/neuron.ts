import * as THREE from 'three';

export type NeuronPort = Readonly<{ x: number; y: number; z: number }>;
export type NeuronGlyph = THREE.Group & { readonly ports: readonly NeuronPort[] };

/** Reserve this many soma radii in every direction for camera/label margins. */
export const NEURON_EXTENT_RATIO = 3.6;

const SOMA_SIDES = 16;
const SOMA_ROWS = 12;
const DENDRITES = 5;
const TUBE_SIDES = 8;
const STEM_STEPS = 10;
const BRANCH_STEPS = 7;
const ROOT_WIDTH = .24;

/** One opaque draw, one owned geometry/material, no textures or retained cache. */
export const NEURON_GEOMETRY_BUDGET = Object.freeze({
  vertices: 1168,
  triangles: 2272,
  bufferBytes: 41664,
  drawCalls: 1,
});

function seededRandom(stableId: string): () => number {
  let state = 2166136261;
  for (let i = 0; i < stableId.length; i++) state = Math.imul(state ^ stableId.charCodeAt(i), 16777619);
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function taper(width: number, t: number): number {
  return width * Math.pow(1 - t, 1.25);
}

// Decorative morphology must never intercept the original sphere's picking.
const ignoreRaycast: THREE.Mesh['raycast'] = () => undefined;

/** Static visual metaphor, not biological learning or a relationship generator.
 * `radius` is the nominal soma radius in scene units. Translate the returned group
 * to the node position; keep its original sphere as the sole picking target.
 * The enclosing radius is <= NEURON_EXTENT_RATIO * radius (before group scaling).
 * `ports` contains all 15 rendered dendrite tips in group-local coordinates,
 * ordered by stem, then its two branches. Port order is stable for a stable ID;
 * callers transform the chosen port to world space when docking a real bridge.
 * Ordinary Mesh geometry/material disposal during traversal releases all owned
 * GPU resources. No update method, clocks, animation hooks or external inputs.
 */
export function createNeuronGlyph(radius: number, color: THREE.Color, stableId: string): NeuronGlyph {
  if (!Number.isFinite(radius) || radius <= 0) throw new RangeError('Neuron radius must be finite and positive');

  const random = seededRandom(stableId);
  const phase = random() * Math.PI * 2;
  const directions = Array.from({ length: DENDRITES }, (_, i) => {
    const angle = phase + i * Math.PI * 2 / DENDRITES + (random() - .5) * .22;
    return new THREE.Vector3(Math.cos(angle), Math.sin(angle), (random() - .5) * .9).normalize();
  });
  const positions: number[] = [];
  const indices: number[] = [];
  const tipIndices: number[] = [];
  const vertex = (point: THREE.Vector3): number => {
    const index = positions.length / 3;
    positions.push(point.x, point.y, point.z);
    return index;
  };

  // A welded latitude mesh: no seam/pole duplication or degenerate triangles.
  // Low-frequency irregularity and small root lobes keep the soma rounded.
  const somaVertex = (x: number, y: number, z: number): number => {
    const direction = new THREE.Vector3(x, y, z);
    let shape = 1 + .03 * Math.sin(3 * x + phase) * Math.cos(2 * y - phase)
      + .025 * Math.sin(4 * z + phase);
    for (const root of directions) shape += .075 * Math.pow(Math.max(0, direction.dot(root)), 16);
    return vertex(direction.multiplyScalar(shape));
  };
  const north = somaVertex(0, 0, 1);
  for (let row = 1; row < SOMA_ROWS; row++) {
    const theta = row * Math.PI / SOMA_ROWS;
    for (let side = 0; side < SOMA_SIDES; side++) {
      const phi = side * Math.PI * 2 / SOMA_SIDES;
      somaVertex(Math.sin(theta) * Math.cos(phi), Math.sin(theta) * Math.sin(phi), Math.cos(theta));
    }
  }
  const south = somaVertex(0, 0, -1);
  for (let side = 0; side < SOMA_SIDES; side++) {
    const next = (side + 1) % SOMA_SIDES;
    indices.push(north, 1 + side, 1 + next);
    for (let row = 0; row < SOMA_ROWS - 2; row++) {
      const a = 1 + row * SOMA_SIDES + side, b = 1 + row * SOMA_SIDES + next;
      const c = a + SOMA_SIDES, d = b + SOMA_SIDES;
      indices.push(a, c, b, b, c, d);
    }
    const last = 1 + (SOMA_ROWS - 2) * SOMA_SIDES;
    indices.push(last + side, south, last + next);
  }

  // Append swept rings directly into one indexed buffer, without TubeGeometry
  // objects. Each root is buried inside its parent surface; overlapping closed
  // shells make a continuous silhouette without adding junction meshes/draws.
  const appendDendrite = (curve: THREE.CubicBezierCurve3, width: number, steps: number): void => {
    const start = positions.length / 3;
    const center = new THREE.Vector3(), tangent = new THREE.Vector3();
    const normal = new THREE.Vector3(), binormal = new THREE.Vector3(), point = new THREE.Vector3();
    curve.getTangent(0, tangent);
    normal.set(0, Math.abs(tangent.z) < .8 ? 0 : 1, Math.abs(tangent.z) < .8 ? 1 : 0);
    for (let step = 0; step < steps; step++) {
      const t = step / steps;
      curve.getPoint(t, center); curve.getTangent(t, tangent);
      // Transport the previous ring frame instead of independently twisting it.
      normal.addScaledVector(tangent, -normal.dot(tangent)).normalize();
      binormal.crossVectors(tangent, normal).normalize();
      const thickness = taper(width, t);
      for (let side = 0; side < TUBE_SIDES; side++) {
        const angle = side * Math.PI * 2 / TUBE_SIDES;
        vertex(point.copy(center).addScaledVector(normal, Math.cos(angle) * thickness)
          .addScaledVector(binormal, Math.sin(angle) * thickness));
      }
      if (step === 0) continue;
      for (let side = 0; side < TUBE_SIDES; side++) {
        const next = (side + 1) % TUBE_SIDES;
        const a = start + (step - 1) * TUBE_SIDES + side, b = start + (step - 1) * TUBE_SIDES + next;
        indices.push(a, b, a + TUBE_SIDES, b, b + TUBE_SIDES, a + TUBE_SIDES);
      }
    }
    const root = vertex(curve.v0), tip = vertex(curve.v3);
    tipIndices.push(tip);
    const last = start + (steps - 1) * TUBE_SIDES;
    for (let side = 0; side < TUBE_SIDES; side++) {
      const next = (side + 1) % TUBE_SIDES;
      indices.push(root, start + next, start + side, last + side, last + next, tip);
    }
  };

  for (const direction of directions) {
    const lateral = new THREE.Vector3(-direction.y, direction.x, 0).normalize();
    const bend = (random() < .5 ? -1 : 1) * (.22 + random() * .2);
    const length = 2.7 + random() * .35;
    const stem = new THREE.CubicBezierCurve3(
      direction.clone().multiplyScalar(.62),
      direction.clone().multiplyScalar(1.3).addScaledVector(lateral, bend * .3),
      direction.clone().multiplyScalar(length * .76).addScaledVector(lateral, bend),
      direction.clone().multiplyScalar(length).addScaledVector(lateral, bend * .75),
    );
    appendDendrite(stem, ROOT_WIDTH, STEM_STEPS);

    for (let branch = 0; branch < 2; branch++) {
      const t = branch === 0 ? .4 : .62;
      const root = stem.getPoint(t), tangent = stem.getTangent(t);
      const side = lateral.clone().multiplyScalar(branch === 0 ? -1 : 1);
      side.z += (random() - .5) * .6;
      side.addScaledVector(tangent, -side.dot(tangent)).normalize();
      const reach = 1.05 + random() * .2;
      // Bezier convex hull <= 3.3; tube radius <= .24, hence extent < 3.6.
      const child = new THREE.CubicBezierCurve3(
        root,
        root.clone().addScaledVector(tangent, reach * .34),
        root.clone().addScaledVector(tangent, reach * .72).addScaledVector(side, .42).clampLength(0, 3.3),
        root.clone().addScaledVector(tangent, reach).addScaledVector(side, .68 + random() * .18).clampLength(0, 3.3),
      );
      appendDendrite(child, taper(ROOT_WIDTH, t) * .65, BRANCH_STEPS);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.name = 'neuron-morphology';
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(new THREE.Uint16BufferAttribute(indices, 1));
  geometry.computeVertexNormals();
  geometry.scale(radius, radius, radius);
  geometry.computeBoundingBox();
  // Origin-centered bounds also give integration a meaningful node extent.
  const attribute = geometry.getAttribute('position');
  let extent = 0;
  for (let i = 0; i < attribute.count; i++) {
    extent = Math.max(extent, Math.hypot(attribute.getX(i), attribute.getY(i), attribute.getZ(i)));
  }
  geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(), extent);

  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color('#dde7ef').lerp(new THREE.Color('#ece6d8'), .28).lerp(color, .22),
    roughness: .58,
    metalness: .16,
  });
  material.name = 'neuron-silver-ivory';
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = 'neuron-morphology';
  mesh.raycast = ignoreRaycast;
  // Read the finished Float32 positions, after every geometry transform, rather
  // than rescaling curve endpoints: ports must match rendered vertices exactly.
  const ports = Object.freeze(tipIndices.map(index => Object.freeze({
    x: attribute.getX(index), y: attribute.getY(index), z: attribute.getZ(index),
  })));
  const group = Object.defineProperty(new THREE.Group(), 'ports', {
    value: ports, enumerable: true,
  }) as NeuronGlyph;
  group.name = 'knowledge-neuron';
  group.add(mesh);
  return group;
}
