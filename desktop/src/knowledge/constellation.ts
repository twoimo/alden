import * as THREE from 'three';
import type { KnowledgeEdge, KnowledgeNode } from './graph-model';
import type { Point3 } from './plasticity';

const CAPACITY = 120;
const MIN_RADIUS = .018;
const MAX_RADIUS = .055;
const SILVER = new THREE.Color('#939fac');
const ICE = new THREE.Color('#9baebe');
const NEIGHBOUR = new THREE.Color('#c2d8e8');
const SELECTED = new THREE.Color('#d8c19d');

/**
 * Overview stars only. The owner supplies positions, links and sphere picking;
 * selected-neuron detail belongs to the owner too. No geometry changes on set.
 * Dispose by traversing normally: mesh.dispose(), geometry.dispose(), material.dispose().
 */
export class ConstellationNodes extends THREE.Group {
  private readonly stars: THREE.InstancedMesh<THREE.SphereGeometry, THREE.MeshStandardMaterial>;
  private readonly slotIds: (string | null)[] = new Array(CAPACITY).fill(null);
  private readonly slots = new Map<string, number>();
  private readonly adjacency = new Map<string, Set<string>>();
  private readonly radii = new Float64Array(CAPACITY);
  private readonly instanceTransform = new THREE.Matrix4();
  private readonly instancePosition = new THREE.Vector3();
  private readonly instanceOrientation = new THREE.Quaternion();
  private readonly instanceSize = new THREE.Vector3();
  private selectedId: string | null = null;

  constructor() {
    super();
    this.stars = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1,12,8),
      new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0x35475a, emissiveIntensity: .18, metalness: .12, roughness: .56, toneMapped: false }),
      CAPACITY,
    );
    this.stars.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    // Allocate the companion color attribute once, before the first render.
    this.stars.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(CAPACITY * 3), 3);
    this.stars.instanceColor.setUsage(THREE.DynamicDrawUsage);
    this.stars.count = 0;
    // Avoid stale instanced bounds after a position update; this is one small batch.
    this.stars.frustumCulled = false;
    this.stars.raycast = () => {};
    this.add(this.stars);
    this.clearInstances();
  }

  /** Keep surviving IDs in their slots; first 120 unique, unretracted nodes win. */
  set(nodes: readonly KnowledgeNode[], edges: readonly KnowledgeEdge[]): void {
    const nextIds = new Set<string>();
    for (const node of nodes) {
      if (!node.evidence.retracted) nextIds.add(node.id);
      if (nextIds.size === CAPACITY) break;
    }
    for (let slot = 0; slot < CAPACITY; slot++) {
      const id = this.slotIds[slot];
      if (id !== null && !nextIds.has(id)) {
        this.slots.delete(id);
        this.slotIds[slot] = null;
      }
    }
    let freeSlot = 0;
    this.adjacency.clear();
    for (const id of nextIds) {
      if (!this.slots.has(id)) {
        while (this.slotIds[freeSlot] !== null) freeSlot++;
        this.slotIds[freeSlot] = id;
        this.slots.set(id, freeSlot);
      }
      this.adjacency.set(id, new Set());
    }

    // Undirected, unique real neighbours in this batch. Neither category,
    // importance, weight nor navigation scaffolding is a degree signal.
    const now = Date.now();
    for (const edge of edges) {
      if (edge.purpose === 'navigation' || edge.source === edge.target || edge.evidence.retracted) continue;
      if (Date.parse(edge.validFrom) > now || Date.parse(edge.validTo) <= now) continue;
      const source = this.adjacency.get(edge.source), target = this.adjacency.get(edge.target);
      if (!source || !target) continue;
      source.add(edge.target);
      target.add(edge.source);
    }

    this.radii.fill(0);
    const nodesById = new Map(nodes.map(node => [node.id, node]));
    let count = 0;
    for (let slot = 0; slot < CAPACITY; slot++) {
      const id = this.slotIds[slot];
      if (id === null) continue;
      const node = nodesById.get(id);
      this.radii[slot] = node?.degreeScope === 'permitted filtered graph' && node.degree !== undefined
        ? Math.min(MAX_RADIUS, MIN_RADIUS + .007 * Math.log1p(node.degree))
        : Math.min(MAX_RADIUS, MIN_RADIUS + .007 * Math.sqrt(this.adjacency.get(id)!.size));
      count = slot + 1;
    }
    // Holes stay collapsed so retained IDs do not move when another ID leaves.
    this.stars.count = count;
    this.clearInstances();
    this.highlight(this.selectedId);
  }

  /** Missing/non-finite positions collapse immediately. No per-update allocations. */
  update(positions: ReadonlyMap<string, Point3>): void {
    let visible = false;
    for (let slot = 0; slot < this.stars.count; slot++) {
      const id = this.slotIds[slot];
      const point = id === null ? undefined : positions.get(id);
      // Matrices are Float32: a finite JS number can still overflow their buffer.
      if (point && Number.isFinite(Math.fround(point.x))
        && Number.isFinite(Math.fround(point.y)) && Number.isFinite(Math.fround(point.z))) {
        this.instancePosition.set(point.x, point.y, point.z);
        this.instanceSize.setScalar(this.radii[slot]);
        visible = true;
      } else {
        this.instancePosition.set(0, 0, 0);
        this.instanceSize.setScalar(0);
      }
      this.instanceTransform.compose(this.instancePosition, this.instanceOrientation, this.instanceSize);
      this.stars.setMatrixAt(slot, this.instanceTransform);
    }
    this.stars.visible = visible;
    this.stars.instanceMatrix.needsUpdate = true;
  }

  /** One warm selection and ice-blue direct neighbours; unrelated stars keep their base color. */
  highlight(id: string | null): void {
    this.selectedId = id !== null && this.slots.has(id) ? id : null;
    const neighbours = this.selectedId === null ? undefined : this.adjacency.get(this.selectedId);
    for (let slot = 0; slot < this.stars.count; slot++) {
      const nodeId = this.slotIds[slot];
      let color = SILVER;
      if (nodeId !== null) {
        color = nodeId === this.selectedId ? SELECTED : neighbours?.has(nodeId) ? NEIGHBOUR
          : this.adjacency.get(nodeId)!.size > 0 ? ICE : SILVER;
      }
      this.stars.setColorAt(slot, color);
    }
    this.stars.instanceColor!.needsUpdate = true;
  }

  /** Base sphere radius for the owner's picker; absent/capped/retracted IDs return zero. */
  radius(id: string): number {
    const slot = this.slots.get(id);
    return slot === undefined ? 0 : this.radii[slot];
  }

  private clearInstances(): void {
    this.instancePosition.set(0, 0, 0);
    this.instanceSize.setScalar(0);
    this.instanceTransform.compose(this.instancePosition, this.instanceOrientation, this.instanceSize);
    for (let slot = 0; slot < CAPACITY; slot++) this.stars.setMatrixAt(slot, this.instanceTransform);
    this.stars.instanceMatrix.needsUpdate = true;
    this.stars.visible = false;
  }
}
