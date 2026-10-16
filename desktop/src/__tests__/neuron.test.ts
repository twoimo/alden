import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createNeuronGlyph, NEURON_EXTENT_RATIO, NEURON_GEOMETRY_BUDGET, type NeuronGlyph } from '../knowledge/neuron';

type NeuronMesh = THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
type Shell = { vertices: number[]; triangles: number[][] };
const owned: THREE.Group[] = [];

function dispose(group: THREE.Group): void {
  group.traverse(child => {
    if (!(child instanceof THREE.Mesh)) return;
    child.geometry.dispose();
    for (const material of Array.isArray(child.material) ? child.material : [child.material]) material.dispose();
  });
}

function glyph(id = 'fixture-neuron', radius = 1, color = new THREE.Color('#a2c3df')): NeuronGlyph {
  const group = createNeuronGlyph(radius, color, id);
  owned.push(group);
  return group;
}

function mesh(group: THREE.Group): NeuronMesh { return group.children[0] as NeuronMesh; }
function point(geometry: THREE.BufferGeometry, index: number): THREE.Vector3 {
  return new THREE.Vector3().fromBufferAttribute(geometry.getAttribute('position'), index);
}

// Recover closed surfaces from index connectivity rather than stored diagnostics.
function shells(geometry: THREE.BufferGeometry): Shell[] {
  const index = geometry.index!;
  const parent = Array.from({ length: geometry.getAttribute('position').count }, (_, i) => i);
  const root = (i: number): number => {
    while (i !== parent[i]) { parent[i] = parent[parent[i]]; i = parent[i]; }
    return i;
  };
  for (let i = 0; i < index.count; i += 3) {
    const a = root(index.getX(i));
    parent[root(index.getX(i + 1))] = a;
    parent[root(index.getX(i + 2))] = a;
  }
  const groups = new Map<number, Shell>();
  for (let i = 0; i < parent.length; i++) {
    const key = root(i);
    if (!groups.has(key)) groups.set(key, { vertices: [], triangles: [] });
    groups.get(key)!.vertices.push(i);
  }
  for (let i = 0; i < index.count; i += 3) {
    groups.get(root(index.getX(i)))!.triangles.push([index.getX(i), index.getX(i + 1), index.getX(i + 2)]);
  }
  return [...groups.values()];
}

function inside(geometry: THREE.BufferGeometry, shell: Shell, p: THREE.Vector3): boolean {
  const ray = new THREE.Ray(p, new THREE.Vector3(.79, .37, .49).normalize());
  const hit = new THREE.Vector3();
  const distances: number[] = [];
  for (const [a, b, c] of shell.triangles) {
    if (ray.intersectTriangle(point(geometry, a), point(geometry, b), point(geometry, c), false, hit)) {
      const distance = hit.distanceTo(p);
      if (distance > 1e-8 && !distances.some(value => Math.abs(value - distance) < 1e-7)) distances.push(distance);
    }
  }
  return distances.length % 2 === 1;
}

function ring(geometry: THREE.BufferGeometry, shell: Shell, offset: number): { center: THREE.Vector3; width: number } {
  const points = shell.vertices.slice(offset, offset + 8).map(index => point(geometry, index));
  const center = points.reduce((sum, p) => sum.add(p), new THREE.Vector3()).divideScalar(points.length);
  return { center, width: points[0].distanceTo(center) };
}

afterEach(() => { for (const group of owned.splice(0)) dispose(group); vi.restoreAllMocks(); });

describe('static neuron glyph', () => {
  it('reproduces morphology by stable ID independently of call order and color', () => {
    const first = mesh(glyph('same-id')).geometry;
    const other = mesh(glyph('other-id')).geometry;
    const repeated = mesh(glyph('same-id', 1, new THREE.Color('#e6c797'))).geometry;
    expect(repeated.getAttribute('position').array).toEqual(first.getAttribute('position').array);
    expect(repeated.getAttribute('normal').array).toEqual(first.getAttribute('normal').array);
    expect(repeated.index!.array).toEqual(first.index!.array);
    expect(other.getAttribute('position').array).not.toEqual(first.getAttribute('position').array);
    // Bodies, not only branch orientation, vary slightly with identity.
    expect(other.getAttribute('position').array.slice(0, 100)).not.toEqual(first.getAttribute('position').array.slice(0, 100));
  });

  it('keeps geometry, buffers, triangles and material bounded to one opaque draw', () => {
    for (const id of ['', '가나다/뉴런', 'fixture-0', 'fixture-1', 'fixture-2']) {
      const group = glyph(id), body = mesh(group), geometry = body.geometry;
      expect(group.children).toHaveLength(1);
      expect(geometry.getAttribute('position').count).toBe(NEURON_GEOMETRY_BUDGET.vertices);
      expect(geometry.index!.count / 3).toBe(NEURON_GEOMETRY_BUDGET.triangles);
      expect(geometry.index!.array).toBeInstanceOf(Uint16Array);
      const attributes = Object.values(geometry.attributes) as THREE.BufferAttribute[];
      expect(attributes.reduce((sum, attribute) => sum + attribute.array.byteLength, geometry.index!.array.byteLength))
        .toBe(NEURON_GEOMETRY_BUDGET.bufferBytes);
      expect(attributes.every(attribute => attribute.usage === THREE.StaticDrawUsage)).toBe(true);
      expect(geometry.groups).toHaveLength(0);
      expect(NEURON_GEOMETRY_BUDGET.drawCalls).toBe(1);
      expect(Array.isArray(body.material)).toBe(false);
      expect(body.material.transparent).toBe(false);
      expect(body.material.side).toBe(THREE.FrontSide);
      expect(Object.values(body.material).some(value => value instanceof THREE.Texture)).toBe(false);
    }
  });

  it('has a rounded irregular soma, five embedded stems and two attached branches per stem', () => {
    for (const id of ['anatomy-a', 'anatomy-b', '기억-가지']) {
      const geometry = mesh(glyph(id)).geometry;
      const parts = shells(geometry), soma = parts[0], dendrites = parts.slice(1);
      expect(parts).toHaveLength(16);
      const somaRadii = soma.vertices.map(index => point(geometry, index).length());
      expect(Math.min(...somaRadii)).toBeGreaterThan(.92);
      expect(Math.max(...somaRadii)).toBeLessThan(1.16);
      expect(Math.max(...somaRadii) - Math.min(...somaRadii)).toBeGreaterThan(.04);

      // Every root ring is physically inside its parent, including its full
      // cross-section. This catches detached branches despite a merged buffer.
      const stems = dendrites.filter(part => part.vertices.slice(0, 8).every(index => inside(geometry, soma, point(geometry, index))));
      expect(stems).toHaveLength(5);
      const children = dendrites.filter(part => !stems.includes(part));
      expect(children).toHaveLength(10);
      const parentCounts = stems.map(() => 0);
      for (const child of children) {
        const parentIndex = stems.findIndex(stem => child.vertices.slice(0, 8).every(index => inside(geometry, stem, point(geometry, index))));
        expect(parentIndex).toBeGreaterThanOrEqual(0);
        parentCounts[parentIndex]++;
      }
      expect(parentCounts).toEqual([2, 2, 2, 2, 2]);

      for (const dendrite of dendrites) {
        const count = (dendrite.vertices.length - 2) / 8;
        const first = ring(geometry, dendrite, 0);
        const tip = point(geometry, dendrite.vertices.at(-1)!);
        const axis = tip.clone().sub(first.center).normalize();
        let previousWidth = first.width, maximumBend = 0;
        for (let i = 1; i < count; i++) {
          const section = ring(geometry, dendrite, i * 8);
          expect(section.width).toBeLessThan(previousWidth);
          previousWidth = section.width;
          const offset = section.center.clone().sub(first.center);
          maximumBend = Math.max(maximumBend, offset.addScaledVector(axis, -offset.dot(axis)).length());
        }
        expect(previousWidth).toBeLessThan(first.width * .12);
        expect(maximumBend).toBeGreaterThan(.015);
      }
    }
  });

  it('uses closed outward-wound nondegenerate surfaces and finite unit normals', () => {
    const geometry = mesh(glyph()).geometry;
    const positions = geometry.getAttribute('position'), normals = geometry.getAttribute('normal');
    expect(Array.from(positions.array).every(Number.isFinite)).toBe(true);
    for (let i = 0; i < normals.count; i++) {
      expect(new THREE.Vector3().fromBufferAttribute(normals, i).length()).toBeCloseTo(1, 5);
    }
    for (const part of shells(geometry)) {
      const edges = new Map<string, number>();
      let volume = 0;
      for (const [ia, ib, ic] of part.triangles) {
        const a = point(geometry, ia), b = point(geometry, ib), c = point(geometry, ic);
        expect(b.clone().sub(a).cross(c.clone().sub(a)).length()).toBeGreaterThan(1e-8);
        volume += a.dot(b.clone().cross(c)) / 6;
        for (const [u, v] of [[ia, ib], [ib, ic], [ic, ia]]) {
          const key = `${Math.min(u, v)}:${Math.max(u, v)}`;
          edges.set(key, (edges.get(key) ?? 0) + 1);
        }
      }
      expect([...edges.values()].every(count => count === 2)).toBe(true);
      expect(volume).toBeGreaterThan(0);
    }
  });

  it('scales the entire glyph with radius and encloses every vertex in the advertised margins', () => {
    for (let id = 0; id < 16; id++) {
      const base = mesh(glyph(`extent-${id}`)).geometry.getAttribute('position');
      for (const radius of [.015, .2816, 3]) {
        const geometry = mesh(glyph(`extent-${id}`, radius)).geometry;
        const positions = geometry.getAttribute('position');
        const sphere = geometry.boundingSphere!;
        expect(sphere.center.toArray()).toEqual([0, 0, 0]);
        expect(sphere.radius).toBeLessThanOrEqual(NEURON_EXTENT_RATIO * radius);
        expect(sphere.radius).toBeGreaterThan(2.7 * radius);
        for (let i = 0; i < positions.count; i++) {
          const p = point(geometry, i);
          expect(p.distanceTo(new THREE.Vector3().fromBufferAttribute(base, i).multiplyScalar(radius))).toBeLessThan(1e-6);
          expect(p.length()).toBeLessThanOrEqual(sphere.radius + 1e-12);
          expect(geometry.boundingBox!.containsPoint(p)).toBe(true);
        }
      }
    }
  });

  it('exports every docking port from its exact final rendered tip vertex across radii and IDs', () => {
    for (const id of ['', 'docking-a', 'docking-b', '기억-가지']) {
      for (const radius of [.00001, .015, .2816, .123456789, 1, 3, 10000]) {
        const group = glyph(id, radius), geometry = mesh(group).geometry;
        const positions = geometry.getAttribute('position');
        const dendrites = shells(geometry).slice(1);
        expect(group.ports).toHaveLength(15);
        expect(group.ports).toHaveLength(dendrites.length);
        for (let i = 0; i < dendrites.length; i++) {
          const tipIndex = dendrites[i].vertices.at(-1)!;
          const port = group.ports[i];
          // Exact equality catches both unscaled endpoints and Float32 rounding
          // differences from recomputing a scaled mathematical curve endpoint.
          expect(port.x).toBe(positions.getX(tipIndex));
          expect(port.y).toBe(positions.getY(tipIndex));
          expect(port.z).toBe(positions.getZ(tipIndex));
          expect(dendrites[i].triangles.filter(triangle => triangle.includes(tipIndex))).toHaveLength(8);
        }
      }
    }
  });

  it('keeps docking ports finite and inside the final geometry and advertised extent bounds', () => {
    for (let id = 0; id < 16; id++) {
      for (const radius of [.015, .2816, 1, 3]) {
        const group = glyph(`port-bounds-${id}`, radius), geometry = mesh(group).geometry;
        for (const port of group.ports) {
          expect([port.x, port.y, port.z].every(Number.isFinite)).toBe(true);
          const position = new THREE.Vector3(port.x, port.y, port.z);
          expect(geometry.boundingBox!.containsPoint(position)).toBe(true);
          expect(Math.hypot(port.x, port.y, port.z)).toBeLessThanOrEqual(geometry.boundingSphere!.radius);
          expect(position.length()).toBeLessThanOrEqual(NEURON_EXTENT_RATIO * radius);
          expect(position.length()).toBeGreaterThan(radius);
        }
      }
    }
  });

  it('retains stable immutable local ports independently of group transforms and other glyphs', () => {
    const group = glyph('stable-docking', .2816), ports = group.ports;
    const before = ports.map(port => ({ ...port }));
    glyph('unrelated-docking');
    const other = glyph('stable-docking', .2816, new THREE.Color('#e6c797'));
    expect(other.ports).toEqual(ports);
    expect(other.ports).not.toBe(ports);
    expect(other.ports[0]).not.toBe(ports[0]);
    expect(Object.isFrozen(ports)).toBe(true);
    expect(ports.every(Object.isFrozen)).toBe(true);
    expect(Object.getOwnPropertyDescriptor(group, 'ports')!.writable).toBe(false);
    expect(Object.getOwnPropertyDescriptor(group, 'ports')!.configurable).toBe(false);

    group.position.set(11, -7, 4);
    group.rotation.set(.7, -.4, 1.2);
    group.scale.set(2, 3, .5);
    const parent = new THREE.Group();
    parent.position.set(-3, 8, 2);
    parent.add(group); parent.updateMatrixWorld(true);
    expect(group.ports).toBe(ports);
    expect(group.ports).toEqual(before);
    const geometry = mesh(group).geometry, dendrites = shells(geometry).slice(1);
    for (let i = 0; i < ports.length; i++) {
      const port = ports[i], tip = point(geometry, dendrites[i].vertices.at(-1)!);
      expect(group.localToWorld(new THREE.Vector3(port.x, port.y, port.z)).toArray())
        .toEqual(mesh(group).localToWorld(tip).toArray());
    }
  });

  it('leaves color inputs intact and owns independently disposable resources', () => {
    const color = new THREE.Color('#e6c797'), original = color.clone();
    const first = glyph('shared-id', 1, color), second = glyph('shared-id', 1, color);
    const a = mesh(first), b = mesh(second);
    expect(color.equals(original)).toBe(true);
    expect(a.material.color).not.toBe(color);
    expect(a.material.color.equals(b.material.color)).toBe(true);
    expect(a.geometry).not.toBe(b.geometry);
    expect(a.geometry.getAttribute('position').array).not.toBe(b.geometry.getAttribute('position').array);
    expect(a.material).not.toBe(b.material);
    const geometryDisposed = vi.fn(), materialDisposed = vi.fn(), otherDisposed = vi.fn();
    a.geometry.addEventListener('dispose', geometryDisposed);
    a.material.addEventListener('dispose', materialDisposed);
    b.geometry.addEventListener('dispose', otherDisposed);
    b.material.addEventListener('dispose', otherDisposed);
    dispose(first);
    owned.splice(owned.indexOf(first), 1);
    expect(geometryDisposed).toHaveBeenCalledTimes(1);
    expect(materialDisposed).toHaveBeenCalledTimes(1);
    expect(otherDisposed).not.toHaveBeenCalled();
  });

  it('provides no picking target, animation, shader clock or render-time update callback', () => {
    const group = glyph(), body = mesh(group);
    const baseline = body.geometry.getAttribute('position').array.slice();
    group.updateMatrixWorld(true);
    const ray = new THREE.Raycaster(new THREE.Vector3(0, 0, 10), new THREE.Vector3(0, 0, -1));
    expect(ray.intersectObject(group, true)).toEqual([]);
    group.traverse(child => {
      expect(child.userData).toEqual({});
      expect(child.animations).toEqual([]);
      expect(child.onBeforeRender).toBe(THREE.Object3D.prototype.onBeforeRender);
      expect(child.onAfterRender).toBe(THREE.Object3D.prototype.onAfterRender);
    });
    expect(body.material).toBeInstanceOf(THREE.MeshStandardMaterial);
    expect(body.material).not.toHaveProperty('uniforms');
    expect(body.material.emissive.getHex()).toBe(0);
    expect(body.geometry.morphAttributes).toEqual({});
    expect(body.geometry.getAttribute('position').array).toEqual(baseline);
  });

  it.each([0, -1, NaN, Infinity, -Infinity])('rejects invalid radius %s before allocating resources', radius => {
    expect(() => createNeuronGlyph(radius, new THREE.Color(), 'invalid')).toThrow(RangeError);
  });
});
