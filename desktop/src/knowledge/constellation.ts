import * as THREE from 'three';
import type { KnowledgeEdge, KnowledgeNode } from './graph-model';
import type { Point3 } from './plasticity';
import { ACTIVITY_TTL_MS, type NodeActivity } from './collection-activity';

const CAPACITY = 120;
const MIN_RADIUS = .018;
const MAX_RADIUS = .055;
const SILVER = new THREE.Color('#939fac');
const ICE = new THREE.Color('#9baebe');
const NEIGHBOUR = new THREE.Color('#c2d8e8');
const CYAN = new THREE.Color('#80c9ce');
const SELECTED = new THREE.Color('#d8c19d');
const PINK = new THREE.Color('#ec83a8');
const STORED = new THREE.Color('#8ac5a6');
const READ = new THREE.Color('#8ab9e0');

/**
 * Overview stars only. The owner supplies positions, links and sphere picking;
 * selected-neuron detail belongs to the owner too. No geometry changes on set.
 * Dispose by traversing normally: mesh.dispose(), geometry.dispose(), material.dispose().
 */
export class ConstellationNodes extends THREE.Group {
  private readonly stars: THREE.InstancedMesh<THREE.SphereGeometry, THREE.MeshStandardMaterial>;
  private readonly slotIds: (string | null)[];
  private readonly slots = new Map<string, number>();
  private readonly sourceColors = new Map<string, THREE.Color>();
  private readonly adjacency = new Map<string, Set<string>>();
  private readonly radii: Float64Array;
  private readonly instanceTransform = new THREE.Matrix4();
  private readonly instancePosition = new THREE.Vector3();
  private readonly instanceOrientation = new THREE.Quaternion();
  private readonly instanceSize = new THREE.Vector3();
  private minimumRadius = 0;
  private selectedId: string | null = null;
  private readonly activity = new Map<string, NodeActivity>();
  private readonly activityColor = new THREE.Color();

  constructor(private readonly capacity = CAPACITY) {
    super();
    this.slotIds = new Array(this.capacity).fill(null);
    this.radii = new Float64Array(this.capacity);
    this.stars = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1,12,8),
      new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0x35475a, emissiveIntensity: .18, metalness: .12, roughness: .56, toneMapped: false }),
      this.capacity,
    );
    this.stars.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    // Allocate the companion color attribute once, before the first render.
    this.stars.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(this.capacity * 3), 3);
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
      if (nextIds.size === this.capacity) break;
    }
    for (let slot = 0; slot < this.capacity; slot++) {
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
    this.sourceColors.clear();
    if (this.capacity > CAPACITY) for (const node of nodes) {
      const group = node.sourceTarget || node.space || 'memory';
      let hash = 0;
      for (const letter of group) hash = (Math.imul(hash, 31) + letter.charCodeAt(0)) >>> 0;
      this.sourceColors.set(node.id, new THREE.Color(['#8daebc', '#b597a9', '#b7a674', '#a0a7b5'][hash % 4]));
    }
    for (const [id, event] of this.activity) {
      const node = nodesById.get(id);
      if (!nextIds.has(id) || node?.sourceVersion !== event.version || node.sourceTarget !== event.target_id) this.activity.delete(id);
    }
    let count = 0;
    for (let slot = 0; slot < this.capacity; slot++) {
      const id = this.slotIds[slot];
      if (id === null) continue;
      const node = nodesById.get(id);
      this.radii[slot] = node?.degreeScope === 'permitted filtered graph' && node.degree !== undefined
        ? Math.min(this.capacity > CAPACITY ? .018 : MAX_RADIUS, (this.capacity > CAPACITY ? .006 : MIN_RADIUS) + (this.capacity > CAPACITY ? .003 : .007) * Math.log1p(node.degree))
        : Math.min(this.capacity > CAPACITY ? .018 : MAX_RADIUS, (this.capacity > CAPACITY ? .006 : MIN_RADIUS) + (this.capacity > CAPACITY ? .003 : .007) * Math.sqrt(this.adjacency.get(id)!.size));
      count = slot + 1;
    }
    // Holes stay collapsed so retained IDs do not move when another ID leaves.
    this.stars.count = count;
    this.clearInstances();
    this.highlight(this.selectedId);
  }

  setMinimumRadius(radius: number): void {
    this.minimumRadius = this.capacity > CAPACITY && Number.isFinite(radius) ? Math.min(.03, Math.max(0, radius)) : 0;
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
        this.instanceSize.setScalar(Math.max(this.minimumRadius, this.radii[slot]));
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
    this.paint(Date.now(), false);
  }

  showActivity(event: NodeActivity): void {
    if (this.slots.has(event.document_id)) this.activity.set(event.document_id, event);
  }
  clearActivity(): void { this.activity.clear(); this.paint(Date.now(), false); }
  get activityCount(): number { return this.activity.size; }
  get nextActivityExpiry(): number {
    let expiry = Infinity;
    for (const event of this.activity.values()) expiry = Math.min(expiry, event.at * 1000 + ACTIVITY_TTL_MS);
    return expiry;
  }
  advanceActivity(now: number, staticColor = false): boolean {
    if (!this.activity.size) return false;
    this.paint(now, staticColor);
    return this.activity.size > 0 && !staticColor;
  }
  private paint(now: number, staticColor: boolean): void {
    for (const [id, event] of this.activity) if (now < event.at * 1000 || now - event.at * 1000 >= ACTIVITY_TTL_MS) this.activity.delete(id);
    const neighbours = this.selectedId === null ? undefined : this.adjacency.get(this.selectedId);
    for (let slot = 0; slot < this.stars.count; slot++) {
      const nodeId = this.slotIds[slot];
      let color = SILVER;
      if (nodeId !== null) {
        color = nodeId === this.selectedId ? (this.capacity > CAPACITY ? PINK : SELECTED) : neighbours?.has(nodeId) ? (this.capacity > CAPACITY ? CYAN : NEIGHBOUR)
          : this.sourceColors.get(nodeId) ?? (this.adjacency.get(nodeId)!.size > 0 ? ICE : SILVER);
      }
      const event = nodeId === null ? undefined : this.activity.get(nodeId);
      if (event && nodeId !== this.selectedId) {
        const intensity = staticColor ? .65 : .85 * Math.exp(-(now - event.at * 1000) / 1500);
        this.activityColor.copy(color).lerp(event.kind === 'read' ? READ : STORED, intensity);
        this.stars.setColorAt(slot, this.activityColor);
      } else this.stars.setColorAt(slot, color);
    }
    this.stars.instanceColor!.needsUpdate = true;
  }

  /** Base sphere radius for the owner's picker; absent/capped/retracted IDs return zero. */
  radius(id: string): number {
    const slot = this.slots.get(id);
    return slot === undefined ? 0 : Math.max(this.minimumRadius, this.radii[slot]);
  }

  private clearInstances(): void {
    this.instancePosition.set(0, 0, 0);
    this.instanceSize.setScalar(0);
    this.instanceTransform.compose(this.instancePosition, this.instanceOrientation, this.instanceSize);
    for (let slot = 0; slot < this.capacity; slot++) this.stars.setMatrixAt(slot, this.instanceTransform);
    this.stars.instanceMatrix.needsUpdate = true;
    this.stars.visible = false;
  }
}
