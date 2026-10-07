import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConstellationNodes } from '../knowledge/constellation';
import type { KnowledgeEdge, KnowledgeEvidence, KnowledgeNode } from '../knowledge/graph-model';
import type { Point3 } from '../knowledge/plasticity';
import { pickKnowledgeSphere } from '../knowledge/picking';
import type { NodeActivity } from '../knowledge/collection-activity';

type Stars = THREE.InstancedMesh<THREE.SphereGeometry, THREE.MeshStandardMaterial>;
const owned: ConstellationNodes[] = [];
const distance = (a: THREE.Color, b: THREE.Color) => Math.hypot(a.r-b.r, a.g-b.g, a.b-b.b);
const evidence = (): KnowledgeEvidence => ({
  kind: 'snapshot', sourceEventIds: [], chatId: '', confirmedAt: null, retracted: false,
});
const node = (id: string, extra: Partial<KnowledgeNode> = {}): KnowledgeNode => ({
  id, label: id, category: 'fixture', importance: 0, updatedAt: 0, evidence: evidence(), ...extra,
});
const edge = (source: string, target: string, extra: Partial<KnowledgeEdge> = {}): KnowledgeEdge => ({
  source, target, relation: 'related', context: '', weight: 1, roomId: '', validFrom: '', validTo: '',
  evidenceMessageId: '', evidence: evidence(), ...extra,
});
const points = (...ids: string[]): Map<string, Point3> => new Map(ids.map((id, i) => [id, { x: i + 1, y: 2, z: -3 }]));
const stars = (group: ConstellationNodes): Stars => group.children[0] as Stars;
const transform = (mesh: Stars, slot: number): THREE.Matrix4 => {
  const result = new THREE.Matrix4(); mesh.getMatrixAt(slot, result); return result;
};
const color = (mesh: Stars, slot: number): THREE.Color => {
  const result = new THREE.Color(); mesh.getColorAt(slot, result); return result;
};
const collapsed = (mesh: Stars, slot: number): void => {
  expect(transform(mesh, slot).elements).toEqual([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]);
};
function batch(nodes: readonly KnowledgeNode[] = [], edges: readonly KnowledgeEdge[] = []): ConstellationNodes {
  const group = new ConstellationNodes(); owned.push(group); group.set(nodes, edges); return group;
}
function dispose(group: ConstellationNodes): void {
  // The owning hologram's ordinary traversal also releases instancing buffers.
  group.traverse(child => {
    if (!(child instanceof THREE.Mesh)) return;
    if (child instanceof THREE.InstancedMesh) child.dispose();
    child.geometry.dispose();
    for (const material of Array.isArray(child.material) ? child.material : [child.material]) material.dispose();
  });
}
afterEach(() => {
  vi.restoreAllMocks(); vi.useRealTimers();
  for (const group of owned) dispose(group);
  owned.length = 0;
});

describe('saved activity color buffers', () => {
  it('decays a saved receipt on its real node without replacing geometry, slots or buffers', () => {
    vi.useFakeTimers(); vi.setSystemTime(10000);
    const group = batch([node('a', { sourceVersion: 'v2', sourceTarget: 't' }), node('b')]);
    const mesh = stars(group), attribute = mesh.instanceColor, geometry = mesh.geometry;
    const initial = color(mesh, 0);
    const receipt: NodeActivity = { event_id: 'e', sequence: 1, document_id: 'a', version: 'v2', target_id: 't',
      run_id: 'r', origin: 'import', at: 10, kind: 'revised', success: true };
    group.showActivity(receipt); expect(group.advanceActivity(10000)).toBe(true);
    const peak = color(mesh, 0); expect(distance(peak, initial)).toBeGreaterThan(.1);
    group.advanceActivity(13000); expect(distance(color(mesh, 0), initial)).toBeLessThan(distance(peak, initial));
    group.highlight('a'); expect(color(mesh, 0).getHexString()).toBe('d8c19d');
    group.highlight(null); expect(group.advanceActivity(20000)).toBe(false);
    expect(distance(color(mesh, 0), initial)).toBeLessThan(.00001);
    expect(mesh.instanceColor).toBe(attribute); expect(mesh.geometry).toBe(geometry); expect(group.activityCount).toBe(0);
  });
  it('uses one static reduced-motion color and drops receipts when their source version or scope leaves', () => {
    vi.useFakeTimers(); vi.setSystemTime(10000);
    const a = node('a', { sourceVersion: 'v1', sourceTarget: 't' }), group = batch([a]);
    group.showActivity({ event_id: 'read', sequence: 0, document_id: 'a', version: 'v1', target_id: 't',
      run_id: 'detail', origin: 'detail', at: 10, kind: 'read', success: true });
    expect(group.advanceActivity(10000, true)).toBe(false); const staticColor = color(stars(group), 0);
    group.advanceActivity(13000, true); expect(distance(color(stars(group), 0), staticColor)).toBeLessThan(.00001);
    group.set([{ ...a, sourceVersion: 'v2' }], []); expect(group.activityCount).toBe(0);
    group.set([], []); expect(group.nextActivityExpiry).toBe(Infinity);
  });
});

describe('constellation overview stars', () => {
  it('owns one fixed low-poly draw with 120 matrix/color slots and no decorative objects or animation', () => {
    const group = batch(), mesh = stars(group);
    expect(group).toBeInstanceOf(THREE.Group);
    expect(group.children).toEqual([mesh]);
    expect(mesh).toBeInstanceOf(THREE.InstancedMesh);
    expect(mesh.children).toEqual([]);
    expect(mesh.geometry).toBeInstanceOf(THREE.SphereGeometry);
    expect(mesh.geometry.index!.count/3).toBeLessThanOrEqual(256);
    expect(mesh.geometry.groups).toEqual([]);
    expect(mesh.material).toBeInstanceOf(THREE.MeshStandardMaterial);
    expect(mesh.material.map).toBeNull();
    expect(mesh.material.alphaMap).toBeNull();
    expect(mesh.material.envMap).toBeNull();
    expect(mesh.material.transparent).toBe(false);
    expect(mesh.material).not.toHaveProperty('uniforms');
    expect(mesh.morphTexture).toBeNull();
    expect(mesh.instanceMatrix.count).toBe(120);
    expect(mesh.instanceMatrix.array.byteLength).toBe(7680);
    expect(mesh.instanceColor!.count).toBe(120);
    expect(mesh.instanceColor!.array.byteLength).toBe(1440);
    expect(mesh.instanceMatrix.usage).toBe(THREE.DynamicDrawUsage);
    expect(mesh.instanceColor!.usage).toBe(THREE.DynamicDrawUsage);
    expect(mesh.count).toBe(0);
    expect(mesh.visible).toBe(false);
    expect(mesh.frustumCulled).toBe(false);
    group.traverse(child => {
      expect(child.userData).toEqual({});
      expect(child.animations).toEqual([]);
      expect(child.onBeforeRender).toBe(THREE.Object3D.prototype.onBeforeRender);
      expect(child.onAfterRender).toBe(THREE.Object3D.prototype.onAfterRender);
    });
    for (let slot = 0; slot < 120; slot++) collapsed(mesh, slot);
  });

  it('sizes by unique undirected real degree, ignoring scaffolding, duplicates, loops and absent endpoints', () => {
    const group = batch(['a', 'b', 'c', 'd'].map(id => node(id)), [
      edge('a', 'b'), edge('b', 'a', { weight: 1000 }), edge('a', 'b', { relation: 'duplicate' }),
      edge('c', 'a', { purpose: 'reference' }), edge('a', 'd', { purpose: 'navigation' }),
      edge('a', 'a'), edge('a', 'unknown'), edge('unknown', 'a'),
    ]);
    expect(group.radius('d')).toBe(.018);
    expect(group.radius('b')).toBeGreaterThan(group.radius('d'));
    expect(group.radius('b')).toBe(group.radius('c'));
    expect(group.radius('a')).toBeGreaterThan(group.radius('b'));
    expect(group.radius('a')).toBeLessThan(.055);
    expect(group.radius('unknown')).toBe(0);

    const many = Array.from({ length: 120 }, (_, i) => node(`n${i}`));
    group.set(many, many.slice(1).map(n => edge('n0', n.id)));
    expect(group.radius('n0')).toBe(.055);
    for (const n of many) {
      expect(group.radius(n.id)).toBeGreaterThanOrEqual(.018);
      expect(group.radius(n.id)).toBeLessThanOrEqual(.055);
    }
  });

  it('excludes withdrawn nodes and retracted, expired or future relations from size and highlighting', () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-04T00:00:00Z'));
    const group = batch([
      ...['a', 'b', 'c', 'd', 'e', 'f'].map(id => node(id)),
      node('withdrawn', { evidence: { ...evidence(), retracted: true } }),
    ], [
      edge('a', 'b', { evidence: { ...evidence(), retracted: true } }),
      edge('a', 'c', { validTo: '2026-10-04T00:00:00Z' }),
      edge('a', 'd', { validFrom: '2026-10-05T00:00:00Z' }),
      edge('a', 'withdrawn'),
      edge('a', 'e', { validFrom: '2026-10-04T00:00:00Z', validTo: '2026-10-05T00:00:00Z' }),
      edge('f', 'e'),
    ]);
    const mesh = stars(group), before = mesh.instanceColor!.array.slice();
    expect(group.radius('withdrawn')).toBe(0);
    expect(mesh.count).toBe(6);
    expect(group.radius('a')).toBe(group.radius('f'));
    for (const id of ['b', 'c', 'd']) expect(group.radius(id)).toBe(.018);
    group.highlight('a');
    for (const slot of [1, 2, 3, 5]) {
      expect(mesh.instanceColor!.array.slice(slot * 3, slot * 3 + 3)).toEqual(before.slice(slot * 3, slot * 3 + 3));
    }
    expect(mesh.instanceColor!.array.slice(12, 15)).not.toEqual(before.slice(12, 15));
  });

  it('does not use categories, spaces, importance, hub flags or decorative ID patterns for appearance', () => {
    const group = batch([
      node('a', { category: 'people', importance: 100, space: 'room/one', isHub: true }),
      node('b', { category: 'project', importance: 0, space: 'room/two' }),
      node('a-different-id', { category: 'unrelated', importance: 65 }),
    ], [edge('a', 'b'), edge('b', 'a-different-id'), edge('a-different-id', 'a')]);
    const mesh = stars(group), positions = points('a', 'b', 'a-different-id');
    group.update(positions);
    expect(group.radius('a')).toBe(group.radius('b'));
    expect(group.radius('b')).toBe(group.radius('a-different-id'));
    expect(color(mesh, 0)).toEqual(color(mesh, 1));
    expect(color(mesh, 1)).toEqual(color(mesh, 2));
    for (let slot = 0; slot < 3; slot++) {
      const m = transform(mesh, slot);
      expect(m.elements.slice(12, 15)).toEqual([slot + 1, 2, -3]);
      expect(m.elements[0]).toBeCloseTo(group.radius('a'), 8);
    }
  });

  it('highlights just the selected star and its direct incident neighbours, then restores exact base colors', () => {
    const group = batch(['a', 'b', 'c', 'two-hop', 'navigation', 'isolated'].map(id => node(id)), [
      edge('a', 'b'), edge('c', 'a'), edge('b', 'two-hop'), edge('a', 'navigation', { purpose: 'navigation' }),
    ]);
    const mesh = stars(group), before = mesh.instanceColor!.array.slice();
    group.update(points('a', 'b', 'c', 'two-hop', 'navigation', 'isolated'));
    const matrices = mesh.instanceMatrix.array.slice(), radiusBefore = group.radius('a');
    group.highlight('a');
    expect(color(mesh, 0).r).toBeGreaterThan(color(mesh, 0).b); // Sole warm mark.
    expect(color(mesh, 1)).toEqual(color(mesh, 2));
    expect(color(mesh, 1).b).toBeGreaterThan(color(mesh, 1).r);
    for (const slot of [0, 1, 2]) {
      expect(mesh.instanceColor!.array.slice(slot * 3, slot * 3 + 3)).not.toEqual(before.slice(slot * 3, slot * 3 + 3));
    }
    for (const slot of [3, 4, 5]) {
      expect(mesh.instanceColor!.array.slice(slot * 3, slot * 3 + 3)).toEqual(before.slice(slot * 3, slot * 3 + 3));
    }
    expect(mesh.instanceMatrix.array).toEqual(matrices);
    expect(group.radius('a')).toBe(radiusBefore);
    group.highlight('b');
    expect(mesh.instanceColor!.array.slice(6, 9)).toEqual(before.slice(6, 9));
    group.highlight(null);
    expect(mesh.instanceColor!.array).toEqual(before);
    group.highlight('a'); group.highlight('unknown');
    expect(mesh.instanceColor!.array).toEqual(before);
  });

  it('keeps slots stable across reordering, reuses holes and clears stale positions on set', () => {
    const group = batch(['a', 'b', 'c'].map(id => node(id)), [edge('a', 'b')]), mesh = stars(group);
    const positions = points('a', 'b', 'c', 'd');
    group.update(positions);
    const before = mesh.instanceMatrix.array.slice();
    group.set(['c', 'b', 'a'].map(id => node(id)), [edge('b', 'a')]);
    expect(mesh.visible).toBe(false);
    for (let slot = 0; slot < 3; slot++) collapsed(mesh, slot);
    group.update(positions);
    expect(mesh.instanceMatrix.array).toEqual(before);
    group.set(['c', 'a'].map(id => node(id)), []);
    group.update(positions);
    expect(mesh.count).toBe(3);
    expect(group.radius('b')).toBe(0);
    collapsed(mesh, 1);
    expect(transform(mesh, 2).elements[12]).toBe(3);
    group.set(['d', 'c', 'a'].map(id => node(id)), []);
    group.update(positions);
    expect(transform(mesh, 1).elements[12]).toBe(4);
    expect(transform(mesh, 0).elements[12]).toBe(1);
    expect(transform(mesh, 2).elements[12]).toBe(3);
    group.set([node('a')], []); group.update(positions);
    expect(mesh.count).toBe(1);
    collapsed(mesh, 1); collapsed(mesh, 2);
  });

  it('deduplicates IDs, caps at 120 and keeps allocation identities through growth, replacement and empty sets', () => {
    const group = batch(), mesh = stars(group);
    const geometry = mesh.geometry, material = mesh.material;
    const matrixAttribute = mesh.instanceMatrix, matrices = matrixAttribute.array;
    const colorAttribute = mesh.instanceColor!, colors = colorAttribute.array;
    const nodes = Array.from({ length: 125 }, (_, i) => node(`n${i}`));
    group.set([nodes[0], nodes[0], ...nodes], []);
    group.update(points(...nodes.map(n => n.id)));
    expect(mesh.count).toBe(120);
    expect(group.radius('n119')).toBe(.018);
    expect(group.radius('n120')).toBe(0);
    expect(transform(mesh, 119).elements[12]).toBe(120);
    group.set([node('replacement')], []);
    expect(mesh.count).toBe(1);
    expect(mesh.visible).toBe(false);
    for (let slot = 0; slot < 120; slot++) collapsed(mesh, slot);
    group.update(points('replacement'));
    group.set([], []); group.update(points('replacement'));
    expect(mesh.count).toBe(0);
    expect(mesh.visible).toBe(false);
    expect(group.radius('replacement')).toBe(0);
    expect(group.radius('n0')).toBe(0);
    expect(mesh.geometry).toBe(geometry); expect(mesh.material).toBe(material);
    expect(mesh.instanceMatrix).toBe(matrixAttribute); expect(mesh.instanceMatrix.array).toBe(matrices);
    expect(mesh.instanceColor).toBe(colorAttribute); expect(mesh.instanceColor!.array).toBe(colors);
  });

  it('hides missing and invalid positions without ghosting and restores only positions present in the next update', () => {
    const group = batch(['a', 'b', 'c', 'd', 'e'].map(id => node(id))), mesh = stars(group);
    group.update(points('a', 'b', 'c', 'd', 'e'));
    expect(mesh.visible).toBe(true);
    const next = points('a');
    next.set('c', { x: NaN, y: 0, z: 0 });
    next.set('d', { x: 0, y: Infinity, z: 0 });
    next.set('e', { x: 0, y: 0, z: Number.MAX_VALUE });
    group.update(next);
    expect(transform(mesh, 0).elements[0]).toBeGreaterThan(0);
    for (let slot = 1; slot < 5; slot++) collapsed(mesh, slot);
    expect(mesh.instanceMatrix.array.every(Number.isFinite)).toBe(true);
    group.highlight('c');
    collapsed(mesh, 2);
    group.update(new Map());
    expect(mesh.visible).toBe(false);
    for (let slot = 0; slot < 5; slot++) collapsed(mesh, slot);
    group.update(new Map([['b', { x: 0, y: 0, z: 0 }]]));
    expect(mesh.visible).toBe(true);
    expect(transform(mesh, 1).elements[0]).toBeCloseTo(group.radius('b'), 8);
    for (const slot of [0, 2, 3, 4]) collapsed(mesh, slot);
  });

  it('snapshots IDs and adjacency on set and refreshes an existing selection when relationships change', () => {
    const nodes = ['a', 'b', 'c'].map(id => node(id)), edges = [edge('a', 'b')];
    const group = batch(nodes, edges), mesh = stars(group), originalRadius = group.radius('a');
    group.highlight('a');
    const selected = mesh.instanceColor!.array.slice();
    nodes[0].id = 'mutated'; edges[0].target = 'c';
    group.update(points('a', 'b', 'c')); group.highlight('a');
    expect(group.radius('a')).toBe(originalRadius);
    expect(group.radius('mutated')).toBe(0);
    expect(mesh.instanceColor!.array).toEqual(selected);
    group.set(['a', 'b', 'c'].map(id => node(id)), [edge('a', 'c')]);
    expect(color(mesh, 0).r).toBeGreaterThan(color(mesh, 0).b);
    expect(color(mesh, 2).toArray()).toEqual(Array.from(selected.slice(3, 6)));
    expect(color(mesh, 1).toArray()).toEqual(Array.from(selected.slice(6, 9)));
    group.set([node('b'), node('c')], []);
    expect(color(mesh, 1)).toEqual(color(mesh, 2));
    expect(color(mesh, 1).b).toBeGreaterThan(color(mesh, 1).r);
  });

  it('updates in place using the same scratch transforms without moving Object3D properties or idle star positions', () => {
    const group = batch(['a', 'b'].map(id => node(id))), mesh = stars(group);
    const positions: ReadonlyMap<string, Point3> = points('a', 'b');
    group.position.set(7, 8, 9); group.rotation.set(.1, .2, .3); group.scale.set(2, 2, 2);
    group.updateMatrixWorld(true);
    const groupMatrix = group.matrix, groupRotation = group.rotation, groupScale = group.scale;
    const originalMatrix = group.matrix.clone(), originalRotation = group.rotation.clone(), originalScale = group.scale.clone();
    const matrixBuffer = mesh.instanceMatrix.array, colorBuffer = mesh.instanceColor!.array;
    const geometryPositions = mesh.geometry.getAttribute('position').array;
    const geometryBefore = geometryPositions.slice(), colorVersion = mesh.instanceColor!.version;
    const compose = vi.spyOn(THREE.Matrix4.prototype, 'compose');
    group.update(positions);
    const before = matrixBuffer.slice();
    for (let frame = 0; frame < 12; frame++) group.update(positions);
    expect(mesh.instanceMatrix.array).toBe(matrixBuffer);
    expect(matrixBuffer).toEqual(before);
    expect(mesh.instanceColor!.array).toBe(colorBuffer);
    expect(mesh.instanceColor!.version).toBe(colorVersion);
    expect(mesh.geometry.getAttribute('position').array).toBe(geometryPositions);
    expect(geometryPositions).toEqual(geometryBefore);
    expect(compose).toHaveBeenCalledTimes(26);
    const [position, orientation, size] = compose.mock.calls[0];
    for (const call of compose.mock.calls) {
      expect(call[0]).toBe(position); expect(call[1]).toBe(orientation); expect(call[2]).toBe(size);
    }
    for (const context of compose.mock.contexts) expect(context).toBe(compose.mock.contexts[0]);
    expect(compose.mock.contexts[0]).not.toBe(groupMatrix);
    expect(position).not.toBe(group.position); expect(orientation).not.toBe(group.quaternion); expect(size).not.toBe(groupScale);
    expect(group.matrix).toBe(groupMatrix); expect(group.matrix).toEqual(originalMatrix);
    expect(group.rotation).toBe(groupRotation); expect(group.rotation.equals(originalRotation)).toBe(true);
    expect(group.scale).toBe(groupScale); expect(group.scale).toEqual(originalScale);
  });

  it('leaves raycasting to the owning sphere picker using the advertised radius', () => {
    const group = batch([node('a')]); group.update(new Map([['a', { x: 0, y: 0, z: 0 }]]));
    group.updateMatrixWorld(true);
    const ray = new THREE.Raycaster(new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 0, -1));
    expect(ray.intersectObject(group, true)).toEqual([]);
    const proxy = new THREE.Mesh(new THREE.SphereGeometry(group.radius('a'), 8, 6), new THREE.MeshBasicMaterial());
    expect(pickKnowledgeSphere(ray, [proxy])).toBe(proxy);
    ray.ray.origin.x = group.radius('a') * 1.1;
    expect(pickKnowledgeSphere(ray, [proxy])).toBeNull();
    proxy.geometry.dispose(); proxy.material.dispose();
  });

  it('releases its mesh, geometry and material through ordinary owner traversal with no shared GPU resources', () => {
    const first = batch([node('a')]), second = batch([node('a')]);
    const a = stars(first), b = stars(second);
    expect(a.geometry).not.toBe(b.geometry); expect(a.material).not.toBe(b.material);
    expect(a.instanceMatrix.array).not.toBe(b.instanceMatrix.array);
    expect(a.instanceColor!.array).not.toBe(b.instanceColor!.array);
    const meshDisposed = vi.fn(), geometryDisposed = vi.fn(), materialDisposed = vi.fn(), otherDisposed = vi.fn();
    a.addEventListener('dispose', meshDisposed);
    a.geometry.addEventListener('dispose', geometryDisposed); a.material.addEventListener('dispose', materialDisposed);
    b.addEventListener('dispose', otherDisposed);
    b.geometry.addEventListener('dispose', otherDisposed); b.material.addEventListener('dispose', otherDisposed);
    dispose(first); owned.splice(owned.indexOf(first), 1);
    expect(meshDisposed).toHaveBeenCalledTimes(1);
    expect(geometryDisposed).toHaveBeenCalledTimes(1); expect(materialDisposed).toHaveBeenCalledTimes(1);
    expect(otherDisposed).not.toHaveBeenCalled();
  });
});
