import type { KnowledgeView } from './graph-model';
import type { LayoutAffinityPair } from './layout-affinity';
import type { Point3 } from './plasticity';

/** Snapshot budgets, not a larger per-frame physics capacity. */
export const SEMANTIC_LAYOUT_LIMITS = Object.freeze({
  nodes: 2048, inputNodes: 8192, inputEdges: 32768, inputAffinities: 32768,
  relationPairs: 4096, affinityPairs: 4096, affinityDegree: 4,
  communityPasses: 8, solverIterations: 32, collisionTests: 131072, cellOccupancy: 16,
});

export interface SemanticLayoutOptions {
  changedIds?: ReadonlySet<string>;
  pinnedIds?: ReadonlySet<string>;
  reflow?: boolean;
  nowMs?: number;
}
export interface SemanticLayoutDiagnostics {
  nodes: number; omittedNodes: number; removedNodes: number; invalidPreviousPositions: number;
  relationPairs: number; affinityPairs: number; ignoredRelations: number; rejectedAffinities: number;
  relationPairsDropped: number; affinityPairsDropped: number; inputTruncated: boolean;
  semanticHighPairs: number; semanticLowPairs: number;
  semanticHighMedianDistance: number | null; semanticLowMedianDistance: number | null;
  semanticCosineMin: number | null; semanticCosineMedian: number | null; semanticCosineMax: number | null;
  semanticUpperPairs: number; semanticLowerPairs: number;
  semanticUpperMedianDistance: number | null; semanticLowerMedianDistance: number | null;
  affectedNodes: number; dirtyNodes: number; pinnedNodes: number; clusters: number; depthRingNodes: number;
  communityPasses: number; communityMoves: number; communityVisits: number;
  solverIterations: number; springEvaluations: number; collisionTests: number;
  densityEvaluations: number; neighborhoodDensityEvaluations: number; crowdedCells: number; collisionBudgetExhausted: boolean; collisionCandidatesTruncated: boolean;
  cachedNodes: number; cachedPairs: number; cachedClusters: number; reflow: boolean;
}
export interface SemanticLayoutResult {
  anchors: Map<string, Point3>;
  clusterIds: Map<string, string>;
  dirtyIds: Set<string>;
  diagnostics: SemanticLayoutDiagnostics;
}
interface Pair { key: string; source: string; target: string; r: number; s: number; weight: number; rest: number }
interface CollisionCell {
  x: number; y: number; z: number; members: number[]; sample: number[];
  sumX: number; sumY: number; sumZ: number;
}
interface CollisionNeighborhood {
  samples: number[][]; ends: number[]; total: number;
  density: { x: number; y: number; z: number; pressure: number } | null;
}
function sampledMember(neighborhood: CollisionNeighborhood, offset: number): number {
  let low = 0, high = neighborhood.ends.length - 1;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (offset < neighborhood.ends[middle]) high = middle; else low = middle + 1;
  }
  return neighborhood.samples[low][offset - (low ? neighborhood.ends[low - 1] : 0)];
}
export interface SemanticLayoutCheckpoint {
  positions: Array<[string, Point3]>; labels: Array<[string, string]>;
  centers: Array<[string, Point3]>; pairs: Array<[string, Pair]>; pins: string[];
}
const order = (a: string, b: string): number => a < b ? -1 : a > b ? 1 : 0;
const pairKey = (a: string, b: string): string => JSON.stringify(a < b ? [a, b] : [b, a]);
const finite = (p: Point3 | undefined): p is Point3 => !!p && [p.x, p.y, p.z].every(Number.isFinite);
const copy = (p: Point3): Point3 => ({ x: p.x, y: p.y, z: p.z });
// Subtract first to retain close-coordinate precision; opposite finite extremes
// can overflow that subtraction, in which case divide before subtracting.
function scaledDifference(a: number, b: number, scale: number): number {
  const delta = a - b;
  if (scale === 1) return delta;
  return Number.isFinite(delta) ? delta / scale : a / scale - b / scale;
}
function addCompensated(sum: Float64Array, correction: Float64Array, at: number, value: number): void {
  const old = sum[at], next = old + value;
  correction[at] += Math.abs(old) >= Math.abs(value) ? (old - next) + value : (value - next) + old;
  sum[at] = next;
}
const SEMANTIC_HIGH_COSINE = 0.7;
const SEMANTIC_LOW_COSINE = 0.4;
function medianDistance(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = sorted.length >>> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
function hash(value: string, salt = 2166136261): number {
  for (let i = 0; i < value.length; i++) salt = Math.imul(salt ^ value.charCodeAt(i), 16777619);
  salt = Math.imul(salt ^ (salt >>> 16), 0x7feb352d);
  salt = Math.imul(salt ^ (salt >>> 15), 0x846ca68b);
  return (salt ^ (salt >>> 16)) >>> 0;
}
function seed(id: string): Point3 {
  const unit = (suffix: string) => (hash(id + suffix) + .5) / 4294967296 * 2 - 1;
  return { x: unit(':x') * 1.8, y: unit(':y') * 1.15, z: unit(':z') * .8 };
}
export function semanticSeedPoint(id: string): Point3 { return seed(id); }
function bound(p: Point3): Point3 {
  const q = { x: Math.max(-2.3, Math.min(2.3, p.x)), y: Math.max(-1.55, Math.min(1.55, p.y)), z: Math.max(-1.1, Math.min(1.1, p.z)) };
  const scale = Math.max(1, Math.hypot(q.x / 2.3, q.y / 1.55, q.z / 1.1));
  return { x: q.x / scale, y: q.y / scale, z: q.z / scale };
}
/** Keep one pinned focus fixed and give its rings the same room the origin has. */
function boundNear(p: Point3, origin: Point3): Point3 {
  const local = bound({ x: p.x - origin.x, y: p.y - origin.y, z: p.z - origin.z });
  return { x: local.x + origin.x, y: local.y + origin.y, z: local.z + origin.z };
}
function center(ids: readonly string[], points: ReadonlyMap<string, Point3>): Point3 {
  // Divide before summing, so ordinary large imported coordinates do not overflow.
  let x = 0, y = 0, z = 0;
  for (const id of ids) { const p = bound(points.get(id)!); x += p.x / ids.length; y += p.y / ids.length; z += p.z / ids.length; }
  return { x, y, z };
}
function ellipsoidRadius(p: Point3, ax = 2.05, ay = 1.35, az = .95): number {
  return Math.hypot(p.x / ax, p.y / ay, p.z / az);
}
/** Push centers of communities that actually have peers apart, then put their
 * centroid back. Singleton @id communities stay where they are: packing every
 * isolated identity into the view box only draws a shell. Rest lengths are not
 * an input here. */
function separateMultiMemberCenters(centers: Map<string, Point3>, membersByLabel: ReadonlyMap<string, readonly string[]>): void {
  const labels = [...membersByLabel.entries()].filter(([, members]) => members.length >= 2).map(([label]) => label).sort(order);
  if (labels.length < 2) return;
  const points = labels.map(label => copy(centers.get(label)!));
  const centroid = points.reduce((sum, p) => ({
    x: sum.x + p.x / points.length, y: sum.y + p.y / points.length, z: sum.z + p.z / points.length,
  }), { x: 0, y: 0, z: 0 });
  const minimum = Math.min(1.05, Math.max(.62, 1.7 / Math.cbrt(labels.length)));
  for (let pass = 0; pass < 16; pass++) {
    let moved = false;
    for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
      let dx = points[j].x - points[i].x, dy = points[j].y - points[i].y, dz = points[j].z - points[i].z;
      let length = Math.hypot(dx, dy, dz);
      if (length >= minimum) continue;
      if (length < 1e-8) {
        const v = seed(labels[i] + '\0' + labels[j]);
        dx = v.x; dy = v.y; dz = v.z; length = Math.max(1e-8, Math.hypot(dx, dy, dz));
      }
      const push = (minimum - length) / 2 / length;
      points[i].x -= dx * push; points[i].y -= dy * push; points[i].z -= dz * push;
      points[j].x += dx * push; points[j].y += dy * push; points[j].z += dz * push;
      moved = true;
    }
    const now = points.reduce((sum, p) => ({
      x: sum.x + p.x / points.length, y: sum.y + p.y / points.length, z: sum.z + p.z / points.length,
    }), { x: 0, y: 0, z: 0 });
    const sx = centroid.x - now.x, sy = centroid.y - now.y, sz = centroid.z - now.z;
    for (const p of points) { p.x += sx; p.y += sy; p.z += sz; }
    if (!moved) break;
  }
  // Clamp only the center that leaves the inset ellipsoid. A global scale
  // would drag every other community back into one ball.
  const clampToInset = (p: Point3): Point3 => {
    const dx = p.x - centroid.x, dy = p.y - centroid.y, dz = p.z - centroid.z;
    if (ellipsoidRadius(p) <= 1) return p;
    let lo = 0, hi = 1;
    for (let step = 0; step < 12; step++) {
      const mid = (lo + hi) / 2;
      const q = { x: centroid.x + dx * mid, y: centroid.y + dy * mid, z: centroid.z + dz * mid };
      if (ellipsoidRadius(q) <= 1) lo = mid; else hi = mid;
    }
    return { x: centroid.x + dx * lo, y: centroid.y + dy * lo, z: centroid.z + dz * lo };
  };
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 0; i < points.length; i++) points[i] = clampToInset(points[i]);
    const now = points.reduce((sum, p) => ({
      x: sum.x + p.x / points.length, y: sum.y + p.y / points.length, z: sum.z + p.z / points.length,
    }), { x: 0, y: 0, z: 0 });
    const sx = centroid.x - now.x, sy = centroid.y - now.y, sz = centroid.z - now.z;
    for (const p of points) { p.x += sx; p.y += sy; p.z += sz; }
  }
  // The restore above is the last word. Clamping again would move the centroid.
  labels.forEach((label, i) => centers.set(label, copy(points[i])));
}
/** A fresh unfocused overview otherwise fills the ellipsoid with unrelated
 * seeds. Put the largest equal-energy groups on widely separated rays and
 * move communities that actually connect to them onto the same ray. A label
 * is outer dust only when none of its pairs reach those groups. Membership
 * and rest lengths are unchanged. */
function spreadFreshEnergy(
  clusterIds: ReadonlyMap<string, string>,
  membersByLabel: ReadonlyMap<string, readonly string[]>,
  centers: Map<string, Point3>,
  pairs: readonly Pair[],
): void {
  const multi = [...membersByLabel.entries()]
    .filter(([, members]) => members.length >= 2)
    .sort((a, b) => b[1].length - a[1].length || order(a[0], b[0]));
  const ringCount = Math.min(multi.length, 8);
  const ringLabels = new Set<string>();
  const ray = new Map<string, Point3>();
  for (let i = 0; i < ringCount; i++) {
    const angle = -Math.PI / 2 + (i / ringCount) * Math.PI * 2;
    const label = multi[i][0];
    const raw = { x: Math.cos(angle), y: Math.sin(angle) * .72, z: ((i % 3) - 1) * .08 };
    const rawLength = Math.hypot(raw.x, raw.y, raw.z) || 1;
    const direction = { x: raw.x / rawLength, y: raw.y / rawLength, z: raw.z / rawLength };
    ringLabels.add(label); ray.set(label, direction);
    centers.set(label, { x: direction.x * 1.25, y: direction.y * 1.25, z: direction.z * 1.25 });
  }
  const pull = new Map<string, { x: number; y: number; z: number; w: number }>();
  for (const pair of pairs) {
    const left = clusterIds.get(pair.source), right = clusterIds.get(pair.target);
    if (!left || !right || left === right) continue;
    const leftHub = ringLabels.has(left), rightHub = ringLabels.has(right);
    if (leftHub === rightHub) continue;
    const spoke = leftHub ? right : left;
    const hub = ray.get(leftHub ? left : right);
    if (!hub) continue;
    const acc = pull.get(spoke) ?? { x: 0, y: 0, z: 0, w: 0 };
    acc.x += hub.x * pair.weight; acc.y += hub.y * pair.weight; acc.z += hub.z * pair.weight; acc.w += pair.weight;
    pull.set(spoke, acc);
  }
  for (const [label, acc] of pull) {
    if (ringLabels.has(label)) continue;
    const length = Math.hypot(acc.x, acc.y, acc.z);
    if (!(length > 1e-8)) continue;
    const dist = (membersByLabel.get(label)?.length ?? 1) >= 2 ? 1.45 : 1.6;
    const jitter = seed(label);
    centers.set(label, {
      x: acc.x / length * dist + jitter.x * .02,
      y: acc.y / length * dist + jitter.y * .02,
      z: acc.z / length * dist + jitter.z * .015,
    });
  }
  for (const [label, members] of membersByLabel) {
    if (ringLabels.has(label) || pull.has(label)) continue;
    const dir = seed(members[0] ?? label);
    const length = Math.hypot(dir.x, dir.y, dir.z) || 1;
    centers.set(label, bound({ x: dir.x / length * 1.7, y: dir.y / length * 1.7, z: dir.z / length * 1.7 }));
  }
}
function placeOverviewStars(
  ids: readonly string[],
  anchors: Map<string, Point3>,
  edges: ReadonlyArray<readonly [string, { source: string; target: string }]>,
  pinned: ReadonlySet<string>,
): Set<string> {
  const owned = new Set<string>();
  if (ids.length < 256 || pinned.size === 1) return owned;
  const degree = new Map<string, number>();
  for (const [, edge] of edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const hubs = [...degree.entries()].filter(([, count]) => count >= 24)
    .sort((a, b) => b[1] - a[1] || order(a[0], b[0]));
  if (!hubs.length) return owned;
  const hubDegree = new Map(hubs);
  const owner = new Map<string, string>();
  for (const [hub] of hubs) owner.set(hub, hub);
  for (const [, edge] of edges) {
    const left = degree.get(edge.source) ?? 0, right = degree.get(edge.target) ?? 0;
    const leaf = left >= 24 && right < 24 ? edge.target : right >= 24 && left < 24 ? edge.source : '';
    const hub = leaf === edge.target ? edge.source : leaf === edge.source ? edge.target : '';
    if (!leaf || !hub) continue;
    const current = owner.get(leaf);
    if (!current || (hubDegree.get(current) ?? 0) < (hubDegree.get(hub) ?? 0)) owner.set(leaf, hub);
  }
  const members = new Map<string, string[]>();
  for (const [id, hub] of owner) {
    const group = members.get(hub) ?? [];
    group.push(id); members.set(hub, group);
  }
  const ringCount = Math.min(8, hubs.length);
  hubs.forEach(([hub], index) => {
    let dest: Point3;
    if (index < ringCount) {
      const angle = -Math.PI / 2 + (index / ringCount) * Math.PI * 2;
      dest = bound({ x: Math.cos(angle) * 1.55, y: Math.sin(angle) * 1.05, z: ((index % 3) - 1) * .12 });
    } else {
      const dir = seed(hub), length = Math.hypot(dir.x, dir.y, dir.z) || 1;
      dest = bound({ x: dir.x / length * 1.7, y: dir.y / length * 1.7, z: dir.z / length * 1.7 });
    }
    const group = members.get(hub) ?? [hub];
    const disk = Math.min(.48, .035 * Math.sqrt(group.length));
    for (const id of group) {
      if (pinned.has(id)) continue;
      if (id === hub) { anchors.set(id, dest); owned.add(id); continue; }
      const offset = seed(id), span = Math.hypot(offset.x, offset.y, offset.z) || 1;
      const reach = .45 + .55 * ((hash(id) % 1000) / 1000);
      anchors.set(id, bound({
        x: dest.x + offset.x / span * disk * reach,
        y: dest.y + offset.y / span * disk * reach,
        z: dest.z + offset.z / span * disk * reach * .35,
      }));
      owned.add(id);
    }
  });
  return owned;
}

function placeDuplicateLabels(
  nodes: readonly { id?: string; label?: string }[],
  anchors: Map<string, Point3>,
  owned: ReadonlySet<string>,
  pinned: ReadonlySet<string>,
): void {
  if (nodes.length < 256 || pinned.size === 1) return;
  const groups = new Map<string, string[]>();
  for (const node of nodes) {
    if (!node.id || owned.has(node.id) || pinned.has(node.id) || typeof node.label !== 'string') continue;
    const label = node.label.normalize('NFC').replace(/\s+/g, ' ').trim().toLowerCase();
    if (label.length < 2 || label.length > 80) continue;
    const list = groups.get(label) ?? [];
    list.push(node.id); groups.set(label, list);
  }
  const usable = [...groups.entries()].filter(([, ids]) => ids.length >= 2 && ids.length <= 120 && ids.length / nodes.length < .25)
    .sort((a, b) => b[1].length - a[1].length || order(a[0], b[0])).slice(0, 8);
  usable.forEach(([, ids], index) => {
    const angle = -Math.PI / 2 + ((index + .5) / Math.max(8, usable.length)) * Math.PI * 2;
    const dest = bound({ x: Math.cos(angle) * 1.15, y: Math.sin(angle) * .78, z: ((index % 3) - 1) * .08 });
    const disk = Math.min(.42, .03 * Math.sqrt(ids.length));
    for (const id of ids) {
      if (pinned.has(id)) continue;
      const offset = seed(id), span = Math.hypot(offset.x, offset.y, offset.z) || 1;
      const reach = id === ids[0] ? 0 : .4 + .6 * ((hash(id) % 1000) / 1000);
      anchors.set(id, bound({
        x: dest.x + offset.x / span * disk * reach,
        y: dest.y + offset.y / span * disk * reach,
        z: dest.z + offset.z / span * disk * reach * .3,
      }));
    }
  });
}

function unionFind(count: number) {
  const parent = Int32Array.from({ length: count }, (_, i) => i);
  const find = (i: number): number => {
    let root = i;
    while (parent[root] !== root) root = parent[root];
    while (parent[i] !== i) { const next = parent[i]; parent[i] = root; i = next; }
    return root;
  };
  return { find, join(a: number, b: number) { a = find(a); b = find(b); if (a !== b) parent[Math.max(a, b)] = Math.min(a, b); } };
}

/** R/S are geometry inputs only. The caller owns permission/version/profile
 * validation and must clear this bounded instance when its authority changes.
 * Reuse the instance across snapshots; never call it from an animation frame. */
/** Preserve the bounded numeric-grid kernel for the common unpinned view.
 * Extreme pinned contacts use the separately guarded sparse/world-frame pass. */
function denseCollisionPass(ids: readonly string[], points: Point3[], movable: boolean[],
  forces: Float64Array, d: SemanticLayoutDiagnostics, movableCount: number, iteration: number,
  sampleSlots: Array<CollisionCell | undefined>, neighborSlots: Array<CollisionNeighborhood | undefined>,
  collisionSeeds: readonly number[]): void {
  const limits = SEMANTIC_LAYOUT_LIMITS, neighborhoodCrowdThreshold = 24;
  if (d.collisionTests < limits.collisionTests) {
    // Numeric cells and one candidate list per occupied cell avoid repeating
    // 27 string lookups for every node. Rotate crowded representatives and
    // reserve a share of the snapshot budget for every pass and movable ID.
    const cellKey = (x: number, y: number, z: number) => ((x + 16) * 32 + y + 16) * 32 + z + 16;
    const cells = new Map<number, CollisionCell>();
    const nodeCells = new Int32Array(ids.length);
    const shiftX = iteration * .061 % .16 - .08, shiftY = iteration * .043 % .16 - .08, shiftZ = iteration * .097 % .16 - .08;
    points.forEach((p, i) => {
      const x = Math.floor((p.x + shiftX) / .16), y = Math.floor((p.y + shiftY) / .16), z = Math.floor((p.z + shiftZ) / .16), key = cellKey(x, y, z);
      const cell = cells.get(key) ?? { x, y, z, members: [], sample: [], sumX: 0, sumY: 0, sumZ: 0 };
      cell.members.push(i); cell.sumX += p.x; cell.sumY += p.y; cell.sumZ += p.z; cells.set(key, cell); nodeCells[i] = key;
    });
    // One coarse density evaluation per crowded movable ID, not an
    // unreported all-pairs search. Shift the grid each pass so cell planes
    // do not become artificial packing boundaries. This is geometry only.
    for (const cell of cells.values()) {
      const count = cell.members.length;
      if (count < 2) continue;
      const x = cell.sumX / count, y = cell.sumY / count, z = cell.sumZ / count;
      const radius = .14 * Math.cbrt(count);
      for (const a of cell.members) {
        if (!movable[a]) continue;
        d.densityEvaluations++;
        const p = points[a]; let dx = p.x - x, dy = p.y - y, dz = p.z - z;
        const separation = Math.sqrt(dx * dx + dy * dy + dz * dz); let length = separation;
        if (length < 1e-9) {
          const v = seed(ids[a]); dx = v.x; dy = v.y; dz = v.z;
          length = Math.max(1e-9, Math.hypot(dx, dy, dz));
        }
        const gain = 3 * Math.max(0, radius - separation) / length;
        forces[a * 3] += gain * dx; forces[a * 3 + 1] += gain * dy; forces[a * 3 + 2] += gain * dz;
      }
    }
    sampleSlots.fill(undefined); neighborSlots.fill(undefined);
    for (const [key, cell] of cells) {
      const members = cell.members, sample: number[] = [];
      if (members.length > limits.cellOccupancy) {
        d.crowdedCells += members.length - limits.cellOccupancy; d.collisionCandidatesTruncated = true;
      }
      const start = iteration * limits.cellOccupancy % members.length;
      for (let i = 0; i < Math.min(members.length, limits.cellOccupancy); i++) sample.push(members[(start + i) % members.length]);
      cell.sample = sample;
      sampleSlots[key] = cell;
    }
    for (const [key, cell] of cells) {
      // Keep at most 27 sample references and cumulative lengths. Copying
      // all sampled IDs into every neighboring cell dominated snapshot CPU;
      // binary lookup retains the exact flattened order and rotated quota.
      const samples: number[][] = [], ends: number[] = [];
      let total = 0;
      let count = 0, sumX = 0, sumY = 0, sumZ = 0;
      for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
        const neighborKey = cellKey(cell.x + x, cell.y + y, cell.z + z);
        const neighbor = sampleSlots[neighborKey];
        if (neighbor) {
          samples.push(neighbor.sample); total += neighbor.sample.length; ends.push(total);
          count += neighbor.members.length;
          sumX += neighbor.sumX; sumY += neighbor.sumY; sumZ += neighbor.sumZ;
        }
      }
      // A locally crowded neighborhood can straddle many lightly filled
      // cells. Pressure its centroid only above 24 nodes in the 3x3x3
      // neighborhood, without changing the cloud's global scale or pins.
      neighborSlots[key] = { samples, ends, total, density: count > neighborhoodCrowdThreshold
        ? { x: sumX / count, y: sumY / count, z: sumZ / count,
          pressure: .4 * Math.min(1, (count - neighborhoodCrowdThreshold) / neighborhoodCrowdThreshold) }
        : null };
    }
    const passBudget = Math.floor((limits.collisionTests - d.collisionTests) / (limits.solverIterations - iteration));
    const perNode = Math.max(1, Math.floor(passBudget / movableCount));
    let passTests = 0;
    for (let a = 0; a < ids.length; a++) {
      if (!movable[a]) continue;
      const neighborhood = neighborSlots[nodeCells[a]]!;
      const total = neighborhood.total, p = points[a];
      const density = neighborhood.density;
      if (density) {
        d.neighborhoodDensityEvaluations++;
        let dx = p.x - density.x, dy = p.y - density.y, dz = p.z - density.z;
        let length = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (length < 1e-9) {
          const v = seed(ids[a]), scale = .14 / Math.max(1e-9, Math.hypot(v.x, v.y, v.z));
          dx = v.x * scale; dy = v.y * scale; dz = v.z * scale; length = .14;
        }
        const gain = density.pressure / Math.max(.14, length);
        forces[a * 3] += gain * dx; forces[a * 3 + 1] += gain * dy; forces[a * 3 + 2] += gain * dz;
      }
      if (total > perNode) d.collisionCandidatesTruncated = true;
      const start = (collisionSeeds[a] + iteration * 17) % total;
      let tests = 0;
      // A directed sample updates only this movable node. Every movable ID
      // owns its quota; a popular early representative cannot absorb it.
      for (let trial = 0; trial < Math.min(total, perNode + 1) && tests < perNode && passTests < passBudget; trial++) {
        const b = sampledMember(neighborhood, (start + trial) % total);
        if (a === b) continue;
        tests++; passTests++; d.collisionTests++;
        const q = points[b];
        let dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
        const square = dx * dx + dy * dy + dz * dz;
        if (square >= .0196) continue;
        const separation = Math.sqrt(square);
        let length = separation;
        if (length < 1e-9) {
          const v = seed(ids[a]); dx = -v.x; dy = -v.y; dz = -v.z;
          length = Math.max(1e-9, Math.hypot(dx, dy, dz));
        }
        const densityScale = Math.min(4, Math.max(1, total / perNode));
        const gain = 1.5 * densityScale * (.14 - separation) / length;
        forces[a * 3] -= gain * dx; forces[a * 3 + 1] -= gain * dy; forces[a * 3 + 2] -= gain * dz;
      }
    }
  } else d.collisionBudgetExhausted = true;
}

export class SemanticLayoutEngine {
  private positions = new Map<string, Point3>();
  private labels = new Map<string, string>();
  private centers = new Map<string, Point3>();
  private pairs = new Map<string, Pair>();
  private pins = new Set<string>();

  clear(): void { this.positions.clear(); this.labels.clear(); this.centers.clear(); this.pairs.clear(); this.pins.clear(); }

  checkpoint(): SemanticLayoutCheckpoint {
    return { positions: [...this.positions], labels: [...this.labels], centers: [...this.centers], pairs: [...this.pairs], pins: [...this.pins] };
  }

  restore(value: SemanticLayoutCheckpoint): void {
    if (value.positions.length > 2048 || value.labels.length > 2048 || value.centers.length > 2048 || value.pairs.length > 8192 || value.pins.length > 2048
      || value.positions.some(([, p]) => !finite(p)) || value.centers.some(([, p]) => !finite(p))) throw new Error('semantic_checkpoint_invalid');
    this.positions = new Map(value.positions); this.labels = new Map(value.labels); this.centers = new Map(value.centers); this.pairs = new Map(value.pairs); this.pins = new Set(value.pins);
  }

  layout(view: KnowledgeView, previousPositions: ReadonlyMap<string, Point3>,
    validatedPairs: readonly LayoutAffinityPair[] = view.layoutAffinity?.pairs ?? [],
    options: SemanticLayoutOptions = {}): SemanticLayoutResult {
    const limits = SEMANTIC_LAYOUT_LIMITS;
    const d: SemanticLayoutDiagnostics = {
      nodes: 0, omittedNodes: 0, removedNodes: 0, invalidPreviousPositions: 0,
      relationPairs: 0, affinityPairs: 0, ignoredRelations: 0, rejectedAffinities: 0,
      relationPairsDropped: 0, affinityPairsDropped: 0,
      semanticHighPairs: 0, semanticLowPairs: 0,
      semanticHighMedianDistance: null, semanticLowMedianDistance: null,
      semanticCosineMin: null, semanticCosineMedian: null, semanticCosineMax: null,
      semanticUpperPairs: 0, semanticLowerPairs: 0,
      semanticUpperMedianDistance: null, semanticLowerMedianDistance: null,
      inputTruncated: view.nodes.length > limits.inputNodes || view.edges.length > limits.inputEdges || validatedPairs.length > limits.inputAffinities,
      affectedNodes: 0, dirtyNodes: 0, pinnedNodes: 0, clusters: 0, depthRingNodes: 0,
      communityPasses: 0, communityMoves: 0, communityVisits: 0, solverIterations: 0,
      springEvaluations: 0, collisionTests: 0, densityEvaluations: 0, neighborhoodDensityEvaluations: 0, crowdedCells: 0,
      collisionBudgetExhausted: false, collisionCandidatesTruncated: false,
      cachedNodes: 0, cachedPairs: 0, cachedClusters: 0, reflow: options.reflow === true,
    };
    const unique = new Set(view.nodes.slice(0, limits.inputNodes).filter(n => n.id && !n.evidence.retracted).map(n => n.id));
    // At capacity, keep surviving cached IDs before accepting unrelated arrivals.
    const ids = [...unique].sort((a, b) => Number(this.positions.has(b)) - Number(this.positions.has(a)) || order(a, b))
      .slice(0, limits.nodes).sort(order);
    const index = new Map(ids.map((id, i) => [id, i]));
    const pinned = new Set(ids.filter(id => options.pinnedIds?.has(id)));
    d.nodes = ids.length; d.omittedNodes = Math.max(0, unique.size - ids.length) + Math.max(0, view.nodes.length - limits.inputNodes);
    d.removedNodes = [...this.positions.keys()].filter(id => !index.has(id)).length;
    d.pinnedNodes = pinned.size;
    const r = new Map<string, { source: string; target: string; value: number }>();
    const s = new Map<string, { source: string; target: string; value: number }>();
    const now = Number.isFinite(options.nowMs) ? options.nowMs! : Date.now();
    for (const edge of view.edges.slice(0, limits.inputEdges)) {
      const from = Date.parse(edge.validFrom), until = Date.parse(edge.validTo);
      if (!index.has(edge.source) || !index.has(edge.target) || edge.source === edge.target
        || edge.evidence.retracted || edge.purpose === 'navigation' || !Number.isFinite(edge.weight) || edge.weight <= 0
        || Number.isFinite(from) && from > now || Number.isFinite(until) && until <= now) { d.ignoredRelations++; continue; }
      const key = pairKey(edge.source, edge.target), [source, target] = JSON.parse(key) as [string, string];
      const value = 1 - 1 / (1 + edge.weight);
      if (!r.has(key) || r.get(key)!.value < value) r.set(key, { source, target, value });
    }
    for (const pair of validatedPairs.slice(0, limits.inputAffinities)) {
      if (!index.has(pair.source) || !index.has(pair.target) || pair.source === pair.target
        || !Number.isFinite(pair.cosine) || pair.cosine <= 0 || pair.cosine > 1) { d.rejectedAffinities++; continue; }
      const key = pairKey(pair.source, pair.target), [source, target] = JSON.parse(key) as [string, string];
      if (!s.has(key) || s.get(key)!.value < pair.cosine) s.set(key, { source, target, value: pair.cosine });
    }
    const ranking = (a: [string, { value: number }], b: [string, { value: number }]) => b[1].value - a[1].value || order(a[0], b[0]);
    const real = [...r].sort(ranking).slice(0, limits.relationPairs);
    d.relationPairsDropped = r.size - real.length;
    const semantic: typeof real = [], semanticDegree = new Uint8Array(ids.length);
    for (const entry of [...s].sort(ranking)) {
      const a = index.get(entry[1].source)!, b = index.get(entry[1].target)!;
      if (semantic.length >= limits.affinityPairs || semanticDegree[a] >= limits.affinityDegree || semanticDegree[b] >= limits.affinityDegree) continue;
      semantic.push(entry); semanticDegree[a]++; semanticDegree[b]++;
    }
    d.affinityPairsDropped = s.size - semantic.length;
    d.relationPairs = real.length; d.affinityPairs = semantic.length;
    const rDegree = new Float64Array(ids.length), sDegree = new Float64Array(ids.length);
    for (const [entries, degree] of [[real, rDegree], [semantic, sDegree]] as const)
      for (const [, p] of entries) { degree[index.get(p.source)!] += p.value; degree[index.get(p.target)!] += p.value; }
    const nextPairs = new Map<string, Pair>();
    for (const [entries, degree, kind, scale] of [[real, rDegree, 'r', 1], [semantic, sDegree, 's', .25]] as const)
      for (const [key, raw] of entries) {
        const a = index.get(raw.source)!, b = index.get(raw.target)!;
        const p = nextPairs.get(key) ?? { key, source: raw.source, target: raw.target, r: 0, s: 0, weight: 0, rest: 0 };
        p[kind] = scale * raw.value / Math.sqrt(Math.max(1, degree[a]) * Math.max(1, degree[b]));
        // Unequal endpoint energy is a hub spoke. Keep the link, but do not
        // let it outrank a pair whose force magnitude and direction agree.
        if (kind === 'r') {
          const similar = Math.min(degree[a], degree[b]) / Math.max(degree[a], degree[b]);
          p.r *= 0.35 + 0.65 * similar;
        }
        p.weight = p.r + p.s; nextPairs.set(key, p);
      }
    // Normalize stiffness, but retain raw strength in rest lengths. Otherwise
    // uniform cosine/weight changes at high degree cancel out of normalization.
    for (const p of nextPairs.values()) {
      const realRest = .3 + .45 * (1 - (r.get(p.key)?.value ?? 0));
      const semanticRest = .62 - .35 * (s.get(p.key)?.value ?? 0);
      p.rest = (p.r * realRest + p.s * semanticRest) / p.weight;
    }
    const pairs = [...nextPairs.values()].sort((a, b) => order(a.key, b.key));
    const adjacency: Array<Array<{ to: number; weight: number }>> = ids.map(() => []);
    const current = unionFind(ids.length), impact = unionFind(ids.length);
    for (const p of pairs) {
      const a = index.get(p.source)!, b = index.get(p.target)!;
      const similar = rDegree[a] + rDegree[b] === 0 ? 1 : Math.min(rDegree[a], rDegree[b]) / Math.max(rDegree[a], rDegree[b]);
      // Structural degree imbalance identifies hub spokes, not dissimilar
      // content. A validated semantic pair remains independent evidence for
      // layout even when one endpoint has many more stored relations.
      const communityWeight = p.s + (similar >= 0.5 ? p.r : 0);
      if (communityWeight > 0) {
        adjacency[a].push({ to: b, weight: communityWeight }); adjacency[b].push({ to: a, weight: communityWeight });
        current.join(a, b);
      }
      impact.join(a, b);
    }
    for (const p of this.pairs.values()) if (index.has(p.source) && index.has(p.target)) impact.join(index.get(p.source)!, index.get(p.target)!);
    const touched = new Set<number>();
    const touch = (id: string) => { const at = index.get(id); if (at !== undefined) touched.add(at); };
    for (const id of ids) {
      const supplied = previousPositions.get(id), cached = this.positions.get(id);
      const pinMoved = pinned.has(id) && finite(supplied) && finite(cached)
        && (supplied.x !== cached.x || supplied.y !== cached.y || supplied.z !== cached.z);
      if (!cached || options.changedIds?.has(id) || pinned.has(id) !== this.pins.has(id) || pinMoved) touch(id);
    }
    for (const [key, p] of nextPairs) { const old = this.pairs.get(key); if (!old || p.r !== old.r || p.s !== old.s || p.rest !== old.rest) { touch(p.source); touch(p.target); } }
    for (const [key, p] of this.pairs) if (!nextPairs.has(key)) { touch(p.source); touch(p.target); }
    const roots = new Set([...touched].map(i => impact.find(i)));
    const active = ids.map((_, i) => options.reflow === true || roots.has(impact.find(i)));
    d.affectedNodes = active.filter(Boolean).length;
    const base = new Map<string, Point3>();
    for (const id of ids) {
      const supplied = previousPositions.get(id);
      if (supplied && !finite(supplied)) d.invalidPreviousPositions++;
      base.set(id, copy(finite(supplied) ? supplied : this.positions.get(id) ?? seed(id)));
    }

    // Per-component modularity prevents an unrelated component's edge mass
    // from changing the resolution of an existing community.
    const labels = ids.map(id => options.reflow ? '@' + id : this.labels.get(id) ?? '@' + id);
    const degrees = adjacency.map(list => list.reduce((sum, p) => sum + p.weight, 0));
    const componentVolume = new Map<number, number>(), volumes = new Map<string, number>();
    const volumeKey = (i: number, label: string) => JSON.stringify([current.find(i), label]);
    ids.forEach((id, i) => {
      if (!degrees[i]) labels[i] = '@' + id;
      const root = current.find(i); componentVolume.set(root, (componentVolume.get(root) ?? 0) + degrees[i]);
      const key = volumeKey(i, labels[i]); volumes.set(key, (volumes.get(key) ?? 0) + degrees[i]);
    });
    if (d.affectedNodes) for (let pass = 0; pass < limits.communityPasses; pass++) {
      let moves = 0; d.communityPasses++;
      for (let i = 0; i < ids.length; i++) {
        if (!active[i] || !degrees[i]) continue;
        const weights = new Map<string, number>();
        for (const p of adjacency[i]) { d.communityVisits++; weights.set(labels[p.to], (weights.get(labels[p.to]) ?? 0) + p.weight); }
        weights.set('@' + ids[i], weights.get('@' + ids[i]) ?? 0);
        const old = labels[i], oldKey = volumeKey(i, old), own = degrees[i];
        volumes.set(oldKey, (volumes.get(oldKey) ?? 0) - own);
        const score = (label: string) => (weights.get(label) ?? 0) - own * (volumes.get(volumeKey(i, label)) ?? 0) / componentVolume.get(current.find(i))!;
        let best = old, bestScore = score(old);
        for (const candidate of [...weights.keys()].sort(order)) {
          const value = score(candidate);
          if (value > bestScore + 1e-12) { best = candidate; bestScore = value; }
        }
        labels[i] = best; const nextKey = volumeKey(i, best); volumes.set(nextKey, (volumes.get(nextKey) ?? 0) + own);
        if (best !== old) moves++;
      }
      d.communityMoves += moves;
      if (!moves) break;
    }
    const groups = new Map<string, string[]>();
    ids.forEach((id, i) => { const key = volumeKey(i, labels[i]), members = groups.get(key) ?? []; members.push(id); groups.set(key, members); });
    const memberships = [...groups.values()].sort((a, b) => order(a[0], b[0]));
    const candidates: Array<{ group: number; old: string; overlap: number }> = [];
    memberships.forEach((members, group) => {
      const overlaps = new Map<string, number>();
      for (const id of members) { const old = this.labels.get(id); if (old) overlaps.set(old, (overlaps.get(old) ?? 0) + 1); }
      for (const [old, overlap] of overlaps) candidates.push({ group, old, overlap });
    });
    candidates.sort((a, b) => b.overlap - a.overlap || order(a.old, b.old) || order(memberships[a.group][0], memberships[b.group][0]));
    const assigned = new Map<number, string>(), used = new Set<string>();
    for (const c of candidates) if (!assigned.has(c.group) && !used.has(c.old)) { assigned.set(c.group, c.old); used.add(c.old); }
    const clusterIds = new Map<string, string>(), centers = new Map<string, Point3>();
    memberships.forEach((members, group) => {
      let label = assigned.get(group);
      if (!label) {
        const key = members[0]; label = `c:${hash(key).toString(16)}:${hash(key, 314159265).toString(16)}`;
        while (used.has(label)) label += ':'; used.add(label);
      }
      for (const id of members) clusterIds.set(id, label);
      const oldCenter = this.centers.get(label);
      const hasPrior = members.some(id => finite(previousPositions.get(id)) || this.positions.has(id));
      centers.set(label, copy(options.reflow ? seed(label) : oldCenter ?? (hasPrior ? center(members, base) : seed(label))));
    });
    const membersByLabel = new Map<string, string[]>();
    for (const [id, label] of clusterIds) {
      const members = membersByLabel.get(label) ?? [];
      members.push(id); membersByLabel.set(label, members);
    }
    separateMultiMemberCenters(centers, membersByLabel);
    const freshOverview = ids.length >= 256
      && (options.pinnedIds?.size ?? 0) !== 1
      && (options.reflow === true || ids.every(id => !finite(previousPositions.get(id)) && !this.positions.has(id)));
    if (freshOverview) spreadFreshEnergy(clusterIds, membersByLabel, centers, pairs);
    // A few hundred members on the inner ring become one ball at the camera
    // center. Keep small test cliques where they are and move only a large
    // fresh community farther along the ray it already occupies.
    if (freshOverview) {
      for (const [label, members] of membersByLabel) {
        if (members.length < 24) continue;
        const current = centers.get(label)!;
        const length = Math.hypot(current.x, current.y, current.z) || 1;
        const dist = Math.min(1.85, 1.2 + .018 * Math.sqrt(members.length));
        centers.set(label, bound({ x: current.x / length * dist, y: current.y / length * dist, z: current.z / length * dist }));
      }
    }
    d.clusters = centers.size;
    const anchors = new Map<string, Point3>();
    for (const id of ids) {
      const c = centers.get(clusterIds.get(id)!)!, offset = seed(id);
      const group = membersByLabel.get(clusterIds.get(id)!) ?? [];
      const span = Math.hypot(offset.x, offset.y, offset.z) || 1;
      const prior = (pinned.has(id) || !options.reflow) && (finite(previousPositions.get(id)) || this.positions.has(id));
      const radialJitter = !freshOverview ? .2
        : group.length < 24 ? .05
        : Math.min(.42, .04 * Math.sqrt(group.length)) / span;
      anchors.set(id, prior ? copy(base.get(id)!) : bound({ x: c.x + offset.x * radialJitter, y: c.y + offset.y * radialJitter, z: c.z + offset.z * radialJitter }));
    }
    const movable = ids.map((id, i) => active[i] && !pinned.has(id));
    // Imported coincident coordinates carry no ordering authority. Give each
    // affected movable identity a deterministic direction before solving; pins
    // and unchanged components remain exact.
    const coincident = new Map<string, number>();
    for (const id of ids) { const p = anchors.get(id)!, key = `${p.x},${p.y},${p.z}`; coincident.set(key, (coincident.get(key) ?? 0) + 1); }
    ids.forEach((id, i) => {
      const p = anchors.get(id)!, key = `${p.x},${p.y},${p.z}`;
      if (!movable[i] || (coincident.get(key) ?? 0) < 2) return;
      const v = seed(id), gain = .14 / Math.max(1e-9, Math.hypot(v.x, v.y, v.z));
      anchors.set(id, bound({ x: p.x + v.x * gain, y: p.y + v.y * gain, z: p.z + v.z * gain }));
    });
    const movableCount = movable.filter(Boolean).length, collisionSeeds = ids.map(id => hash(id));
    const points = ids.map(id => copy(anchors.get(id)!));
    const origins = ids.map(id => copy(base.get(id)!));
    // A fresh ID seed is a provisional starting point, not a prior placement.
    // Holding a whole new community toward those unrelated seeds pulls its
    // center inward. Preserve positions only after a solve or an explicit prior.
    const hasPrior = ids.map(id => (pinned.has(id) || !options.reflow)
      && (finite(previousPositions.get(id)) || this.positions.has(id)));
    const clusterCenters = ids.map(id => centers.get(clusterIds.get(id)!)!);
    const indexedPairs = pairs.map(pair => ({ ...pair, a: index.get(pair.source)!, b: index.get(pair.target)! }));
    const relationDepth = new Int16Array(ids.length).fill(-1);
    let depthRoot = -1;
    if (pinned.size === 1) {
      depthRoot = index.get([...pinned][0])!;
      relationDepth[depthRoot] = 0;
      const relationAdjacency: number[][] = ids.map(() => []);
      for (const pair of indexedPairs) if (pair.r > 0) {
        relationAdjacency[pair.a].push(pair.b); relationAdjacency[pair.b].push(pair.a);
      }
      const queue = [depthRoot];
      for (let head = 0; head < queue.length; head++) {
        const at = queue[head];
        if (relationDepth[at] >= 4) continue;
        for (const next of relationAdjacency[at]) if (relationDepth[next] < 0) {
          relationDepth[next] = relationDepth[at] + 1; queue.push(next);
        }
      }
      d.depthRingNodes = relationDepth.reduce((count, depth) => count + (depth > 0 ? 1 : 0), 0);
    }
    const depthRadius = [0, .42, .86, 1.28, 1.66];
    const pinOrigin = depthRoot >= 0 ? copy(points[depthRoot]) : null;
    for (let i = 0; i < points.length; i++) {
      if (pinned.has(ids[i])) continue;
      points[i] = pinOrigin ? boundNear(points[i], pinOrigin) : bound(points[i]);
      origins[i] = pinOrigin ? boundNear(origins[i], pinOrigin) : bound(origins[i]);
    }
    // Keep authoritative pins in world coordinates. Scale ALL force terms in
    // an extreme component together, including rest lengths and collisions;
    // per-pin clipping would lose relative attraction (e.g. +MAX vs -MAX/2).
    // Ordinary components retain the original arithmetic. With at most 8192
    // admitted pairs, 1e100 also leaves ample headroom for squares and sums.
    const componentScales = new Map<number, number>();
    if (pinned.size) for (let i = 0; i < points.length; i++) {
      const p = points[i], o = origins[i], c = clusterCenters[i];
      const magnitude = Math.max(Math.abs(p.x), Math.abs(p.y), Math.abs(p.z),
        Math.abs(o.x), Math.abs(o.y), Math.abs(o.z), Math.abs(c.x), Math.abs(c.y), Math.abs(c.z));
      if (magnitude > 1e100) {
        const root = impact.find(i);
        componentScales.set(root, Math.max(componentScales.get(root) ?? 1, magnitude));
      }
    }
    const forceScales = ids.map((_, i) => componentScales.get(impact.find(i)) ?? 1);
    const forces = new Float64Array(ids.length * 3);
    const corrections = componentScales.size ? new Float64Array(forces.length) : null;
    // Bounded 32^3 cell keys. Reuse direct slots during this snapshot rather
    // than hashing 27 neighbor keys per occupied cell on every solver pass in
    // the unchanged no-pin path. Pins use at most one sparse cell per node.
    const denseGrid = pinned.size === 0;
    const sampleSlots: Array<CollisionCell | undefined> = movableCount && denseGrid ? new Array(32768) : [];
    const neighborSlots: Array<CollisionNeighborhood | undefined> = movableCount && denseGrid ? new Array(32768) : [];
    const neighborhoodCrowdThreshold = 24;
    if (movable.some(Boolean)) for (let iteration = 0; iteration < limits.solverIterations; iteration++) {
      d.solverIterations++; forces.fill(0); corrections?.fill(0);
      ids.forEach((_, i) => {
        if (!movable[i]) return;
        const p = points[i], origin = origins[i], c = clusterCenters[i], scale = forceScales[i];
        // A reached hop already has a ring target. Community-center pull collapses those radii.
        const onRing = depthRoot >= 0 && relationDepth[i] > 0;
        const groupSize = membersByLabel.get(clusterIds.get(ids[i])!)?.length ?? 1;
        const clusterGain = onRing ? 0 : freshOverview && groupSize >= 24 ? .08 : .28;
        forces[i * 3] = clusterGain * scaledDifference(c.x, p.x, scale) + (hasPrior[i] ? .08 * scaledDifference(origin.x, p.x, scale) : 0);
        forces[i * 3 + 1] = clusterGain * scaledDifference(c.y, p.y, scale) + (hasPrior[i] ? .08 * scaledDifference(origin.y, p.y, scale) : 0);
        forces[i * 3 + 2] = clusterGain * scaledDifference(c.z, p.z, scale) + (hasPrior[i] ? .08 * scaledDifference(origin.z, p.z, scale) : 0);
        if (depthRoot >= 0 && relationDepth[i] > 0) {
          const root = points[depthRoot];
          let dx = p.x - root.x, dy = p.y - root.y, dz = p.z - root.z;
          let length = Math.hypot(dx, dy, dz);
          if (length < 1e-6) {
            const v = seed(ids[i]); dx = v.x; dy = v.y; dz = v.z;
            length = Math.max(1e-9, Math.hypot(dx, dy, dz));
          }
          const gain = .55 * (depthRadius[relationDepth[i]] - length) / length / scale;
          forces[i * 3] += gain * dx; forces[i * 3 + 1] += gain * dy; forces[i * 3 + 2] += gain * dz;
        }
      });
      for (const pair of indexedPairs) {
        const { a, b } = pair;
        if (!movable[a] && !movable[b]) continue;
        d.springEvaluations++;
        const p = points[a], q = points[b], scale = forceScales[a], minimum = 1e-9 / scale;
        let dx = scaledDifference(q.x, p.x, scale), dy = scaledDifference(q.y, p.y, scale), dz = scaledDifference(q.z, p.z, scale);
        // Scaling bounds large deltas. Hypot also retains tiny movable-movable
        // deltas within an extreme component instead of underflowing squares.
        let length = scale === 1 ? Math.sqrt(dx * dx + dy * dy + dz * dz) : Math.hypot(dx, dy, dz);
        const tied = length < minimum;
        if (tied) {
          const v = seed(pair.key); dx = v.x * .001 / scale; dy = v.y * .001 / scale; dz = v.z * .001 / scale;
          length = scale === 1 ? Math.sqrt(dx * dx + dy * dy + dz * dz) : Math.hypot(dx, dy, dz);
        }
        length = Math.max(minimum, length);
        if (scale !== 1) {
          // Expand k * (q - p - rest * direction) before accumulating. Forming
          // q-p or length-rest first loses the small restoring term next to an
          // extreme pin. Compensation keeps it when opposite pins cancel.
          const stiffness = 2 * pair.weight;
          for (const [axis, coordinate, delta] of [[0, 'x', dx], [1, 'y', dy], [2, 'z', dz]] as const) {
            const terms = tied ? [stiffness * delta] : [stiffness * (q[coordinate] / scale), -stiffness * (p[coordinate] / scale)];
            terms.push(-stiffness * (pair.rest / scale) * (delta / length));
            for (const term of terms) {
              if (movable[a]) addCompensated(forces, corrections!, a * 3 + axis, term);
              if (movable[b]) addCompensated(forces, corrections!, b * 3 + axis, -term);
            }
          }
          continue;
        }
        const gain = 2 * pair.weight * (length - pair.rest / scale) / length;
        if (movable[a]) { forces[a * 3] += gain * dx; forces[a * 3 + 1] += gain * dy; forces[a * 3 + 2] += gain * dz; }
        if (movable[b]) { forces[b * 3] -= gain * dx; forces[b * 3 + 1] -= gain * dy; forces[b * 3 + 2] -= gain * dz; }
      }
      if (corrections) for (let i = 0; i < ids.length; i++) if (forceScales[i] !== 1) {
        forces[i * 3] += corrections[i * 3];
        forces[i * 3 + 1] += corrections[i * 3 + 1];
        forces[i * 3 + 2] += corrections[i * 3 + 2];
      }
      if (denseGrid) denseCollisionPass(ids, points, movable, forces, d, movableCount, iteration, sampleSlots, neighborSlots, collisionSeeds);
      else if (d.collisionTests < limits.collisionTests) {
        // Numeric cells and one candidate list per occupied cell avoid repeating
        // 27 string lookups for every node. Rotate crowded representatives and
        // reserve a share of the snapshot budget for every pass and movable ID.
        const cellKey = (x: number, y: number, z: number): number | string => denseGrid
          ? ((x + 16) * 32 + y + 16) * 32 + z + 16 : `${x},${y},${z}`;
        const cells = new Map<number | string, CollisionCell>();
        const nodeCells: Array<number | string> = new Array(ids.length);
        const neighborhoods = new Map<number | string, CollisionNeighborhood>();
        // Translate collision arithmetic around a single focus, including cell
        // centroids. Summing its world coordinates can overflow even when every
        // member is finite. Multi-pin movables retain the existing origin bound.
        const collisionPoints = pinOrigin ? points.map(p => ({
          x: p.x - pinOrigin.x, y: p.y - pinOrigin.y, z: p.z - pinOrigin.z,
        })) : points;
        let minX = Infinity, minY = Infinity, minZ = Infinity;
        let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
        if (!denseGrid) for (let i = 0; i < points.length; i++) if (movable[i]) {
          const p = collisionPoints[i];
          minX = Math.min(minX, p.x); minY = Math.min(minY, p.y); minZ = Math.min(minZ, p.z);
          maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y); maxZ = Math.max(maxZ, p.z);
        }
        const shiftX = iteration * .061 % .16 - .08, shiftY = iteration * .043 % .16 - .08, shiftZ = iteration * .097 % .16 - .08;
        collisionPoints.forEach((p, i) => {
          // A point more than two cell widths outside the movable envelope
          // cannot enter any queried 3x3x3 neighborhood, at any grid shift.
          // Reject it before division/indexing, never clamp it into a contact.
          // It remains an authoritative endpoint in the spring pass above.
          if (!denseGrid && pinned.has(ids[i]) && (p.x < minX - .32 || p.x > maxX + .32
            || p.y < minY - .32 || p.y > maxY + .32 || p.z < minZ - .32 || p.z > maxZ + .32)) return;
          const x = Math.floor((p.x + shiftX) / .16), y = Math.floor((p.y + shiftY) / .16), z = Math.floor((p.z + shiftZ) / .16), key = cellKey(x, y, z);
          const cell = cells.get(key) ?? { x, y, z, members: [], sample: [], sumX: 0, sumY: 0, sumZ: 0 };
          cell.members.push(i); cell.sumX += p.x; cell.sumY += p.y; cell.sumZ += p.z; cells.set(key, cell); nodeCells[i] = key;
        });
        // One coarse density evaluation per crowded movable ID, not an
        // unreported all-pairs search. Shift the grid each pass so cell planes
        // do not become artificial packing boundaries. This is geometry only.
        for (const cell of cells.values()) {
          const count = cell.members.length;
          if (count < 2) continue;
          const x = cell.sumX / count, y = cell.sumY / count, z = cell.sumZ / count;
          const radius = .14 * Math.cbrt(count);
          for (const a of cell.members) {
            if (!movable[a]) continue;
            d.densityEvaluations++;
            const p = collisionPoints[a]; let dx = p.x - x, dy = p.y - y, dz = p.z - z;
            const separation = Math.sqrt(dx * dx + dy * dy + dz * dz); let length = separation;
            if (length < 1e-9) {
              const v = seed(ids[a]); dx = v.x; dy = v.y; dz = v.z;
              length = Math.max(1e-9, Math.hypot(dx, dy, dz));
            }
            const gain = 3 * Math.max(0, radius - separation) / length / forceScales[a];
            forces[a * 3] += gain * dx; forces[a * 3 + 1] += gain * dy; forces[a * 3 + 2] += gain * dz;
          }
        }
        sampleSlots.fill(undefined); neighborSlots.fill(undefined);
        for (const [key, cell] of cells) {
          const members = cell.members, sample: number[] = [];
          if (members.length > limits.cellOccupancy) {
            d.crowdedCells += members.length - limits.cellOccupancy; d.collisionCandidatesTruncated = true;
          }
          const start = iteration * limits.cellOccupancy % members.length;
          for (let i = 0; i < Math.min(members.length, limits.cellOccupancy); i++) sample.push(members[(start + i) % members.length]);
          cell.sample = sample;
          if (denseGrid) sampleSlots[key as number] = cell;
        }
        for (const [key, cell] of cells) {
          // Keep at most 27 sample references and cumulative lengths. Copying
          // all sampled IDs into every neighboring cell dominated snapshot CPU;
          // binary lookup retains the exact flattened order and rotated quota.
          const samples: number[][] = [], ends: number[] = [];
          let total = 0;
          let count = 0, sumX = 0, sumY = 0, sumZ = 0;
          for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
            const neighborKey = cellKey(cell.x + x, cell.y + y, cell.z + z);
            const neighbor = denseGrid ? sampleSlots[neighborKey as number] : cells.get(neighborKey);
            if (neighbor) {
              samples.push(neighbor.sample); total += neighbor.sample.length; ends.push(total);
              count += neighbor.members.length;
              sumX += neighbor.sumX; sumY += neighbor.sumY; sumZ += neighbor.sumZ;
            }
          }
          // A locally crowded neighborhood can straddle many lightly filled
          // cells. Pressure its centroid only above 24 nodes in the 3x3x3
          // neighborhood, without changing the cloud's global scale or pins.
          const neighborhood = { samples, ends, total, density: count > neighborhoodCrowdThreshold
            ? { x: sumX / count, y: sumY / count, z: sumZ / count,
              pressure: .4 * Math.min(1, (count - neighborhoodCrowdThreshold) / neighborhoodCrowdThreshold) }
            : null };
          if (denseGrid) neighborSlots[key as number] = neighborhood;
          else neighborhoods.set(key, neighborhood);
        }
        const passBudget = Math.floor((limits.collisionTests - d.collisionTests) / (limits.solverIterations - iteration));
        const perNode = Math.max(1, Math.floor(passBudget / movableCount));
        let passTests = 0;
        for (let a = 0; a < ids.length; a++) {
          if (!movable[a]) continue;
          const neighborhood = denseGrid ? neighborSlots[nodeCells[a] as number]! : neighborhoods.get(nodeCells[a])!;
          const total = neighborhood.total, p = collisionPoints[a];
          const density = neighborhood.density;
          if (density) {
            d.neighborhoodDensityEvaluations++;
            let dx = p.x - density.x, dy = p.y - density.y, dz = p.z - density.z;
            let length = Math.sqrt(dx * dx + dy * dy + dz * dz);
            if (length < 1e-9) {
              const v = seed(ids[a]), scale = .14 / Math.max(1e-9, Math.hypot(v.x, v.y, v.z));
              dx = v.x * scale; dy = v.y * scale; dz = v.z * scale; length = .14;
            }
            const gain = density.pressure / Math.max(.14, length) / forceScales[a];
            forces[a * 3] += gain * dx; forces[a * 3 + 1] += gain * dy; forces[a * 3 + 2] += gain * dz;
          }
          if (total > perNode) d.collisionCandidatesTruncated = true;
          const start = (collisionSeeds[a] + iteration * 17) % total;
          let tests = 0;
          // A directed sample updates only this movable node. Every movable ID
          // owns its quota; a popular early representative cannot absorb it.
          for (let trial = 0; trial < Math.min(total, perNode + 1) && tests < perNode && passTests < passBudget; trial++) {
            const b = sampledMember(neighborhood, (start + trial) % total);
            if (a === b) continue;
            tests++; passTests++; d.collisionTests++;
            const q = collisionPoints[b];
            let dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
            const square = dx * dx + dy * dy + dz * dz;
            if (square >= .0196) continue;
            const separation = Math.sqrt(square);
            let length = separation;
            if (length < 1e-9) {
              const v = seed(ids[a]); dx = -v.x; dy = -v.y; dz = -v.z;
              length = Math.max(1e-9, Math.hypot(dx, dy, dz));
            }
            const densityScale = Math.min(4, Math.max(1, total / perNode));
            const gain = 1.5 * densityScale * (.14 - separation) / length / forceScales[a];
            forces[a * 3] -= gain * dx; forces[a * 3 + 1] -= gain * dy; forces[a * 3 + 2] -= gain * dz;
          }
        }
      } else d.collisionBudgetExhausted = true;
      ids.forEach((id, i) => {
        if (!movable[i]) return;
        const p = points[i], at = i * 3;
        const gain = .08 / Math.max(1, Math.hypot(forces[at], forces[at + 1], forces[at + 2]));
        const scale = forceScales[i];
        // Do not form .08 / a subnormal denominator: that reciprocal can
        // overflow. Divide each scaled force first; the ratio is at most one.
        const divisor = scale === 1 ? 1 : Math.max(1 / scale, Math.hypot(forces[at], forces[at + 1], forces[at + 2]));
        const step = scale === 1
          ? { x: p.x + gain * forces[at], y: p.y + gain * forces[at + 1], z: p.z + gain * forces[at + 2] }
          : { x: p.x + .08 * (forces[at] / divisor), y: p.y + .08 * (forces[at + 1] / divisor), z: p.z + .08 * (forces[at + 2] / divisor) };
        const next = pinOrigin ? boundNear(step, pinOrigin) : bound(step);
        points[i] = next; anchors.set(id, next);
      });
    }
    // Approximate crowded samples can cancel at an imported exact coincidence.
    // Keep a half-clearance exclusion around that shared origin, without moving
    // a pin or giving coincident identities any semantic/merge authority.
    ids.forEach((id, i) => {
      if (!movable[i]) return;
      const origin = base.get(id)!, key = `${origin.x},${origin.y},${origin.z}`;
      if ((coincident.get(key) ?? 0) < 2) return;
      const p = anchors.get(id)!;
      if (Math.hypot(p.x - origin.x, p.y - origin.y, p.z - origin.z) >= .07) return;
      const v = seed(id), gain = .14 / Math.max(1e-9, Math.hypot(v.x, v.y, v.z));
      const separated = {x:origin.x+v.x*gain,y:origin.y+v.y*gain,z:origin.z+v.z*gain};
      anchors.set(id, pinOrigin ? boundNear(separated, pinOrigin) : bound(separated));
    });
    const starOwned = placeOverviewStars(ids, anchors, real, pinned);
    placeDuplicateLabels(view.nodes, anchors, starOwned, pinned);
    const dirtyIds = new Set<string>();
    for (const id of ids) {
      const prior = previousPositions.get(id), p = anchors.get(id)!;
      if (!finite(prior) || Math.hypot(p.x - prior.x, p.y - prior.y, p.z - prior.z) > 1e-9) dirtyIds.add(id);
    }
    // No retired positions, pair endpoints or unused centers survive this call.
    this.positions = new Map([...anchors].map(([id, p]) => [id, copy(p)]));
    this.labels = new Map(clusterIds); this.centers = centers; this.pairs = nextPairs; this.pins = pinned;
    d.dirtyNodes = dirtyIds.size; d.cachedNodes = this.positions.size; d.cachedPairs = this.pairs.size; d.cachedClusters = centers.size;
    const highDistances: number[] = [], lowDistances: number[] = [];
    for (const [, raw] of semantic) {
      const p = anchors.get(raw.source), q = anchors.get(raw.target);
      if (!p || !q) continue;
      const distance = Math.hypot(q.x - p.x, q.y - p.y, q.z - p.z);
      if (raw.value >= SEMANTIC_HIGH_COSINE) highDistances.push(distance);
      else if (raw.value <= SEMANTIC_LOW_COSINE) lowDistances.push(distance);
    }
    d.semanticHighPairs = highDistances.length;
    d.semanticLowPairs = lowDistances.length;
    d.semanticHighMedianDistance = medianDistance(highDistances);
    d.semanticLowMedianDistance = medianDistance(lowDistances);
    const admitted: Array<{ cosine: number; distance: number }> = [];
    for (const [, raw] of semantic) {
      const p = anchors.get(raw.source), q = anchors.get(raw.target);
      if (!p || !q) continue;
      admitted.push({ cosine: raw.value, distance: Math.hypot(q.x - p.x, q.y - p.y, q.z - p.z) });
    }
    if (admitted.length) {
      const cosines = admitted.map(pair => pair.cosine);
      const cosineMedian = medianDistance(cosines)!;
      const upper = admitted.filter(pair => pair.cosine >= cosineMedian).map(pair => pair.distance);
      const lower = admitted.filter(pair => pair.cosine < cosineMedian).map(pair => pair.distance);
      d.semanticCosineMin = Math.min(...cosines);
      d.semanticCosineMedian = cosineMedian;
      d.semanticCosineMax = Math.max(...cosines);
      d.semanticUpperPairs = upper.length;
      d.semanticLowerPairs = lower.length;
      d.semanticUpperMedianDistance = medianDistance(upper);
      d.semanticLowerMedianDistance = medianDistance(lower);
    }
    return { anchors, clusterIds, dirtyIds, diagnostics: d };
  }
}
