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
  affectedNodes: number; dirtyNodes: number; pinnedNodes: number; clusters: number;
  communityPasses: number; communityMoves: number; communityVisits: number;
  solverIterations: number; springEvaluations: number; collisionTests: number;
  crowdedCells: number; collisionBudgetExhausted: boolean; collisionCandidatesTruncated: boolean;
  cachedNodes: number; cachedPairs: number; cachedClusters: number; reflow: boolean;
}
export interface SemanticLayoutResult {
  anchors: Map<string, Point3>;
  clusterIds: Map<string, string>;
  dirtyIds: Set<string>;
  diagnostics: SemanticLayoutDiagnostics;
}
interface Pair { key: string; source: string; target: string; r: number; s: number; weight: number; rest: number }
export interface SemanticLayoutCheckpoint {
  positions: Array<[string, Point3]>; labels: Array<[string, string]>;
  centers: Array<[string, Point3]>; pairs: Array<[string, Pair]>; pins: string[];
}
const order = (a: string, b: string): number => a < b ? -1 : a > b ? 1 : 0;
const pairKey = (a: string, b: string): string => JSON.stringify(a < b ? [a, b] : [b, a]);
const finite = (p: Point3 | undefined): p is Point3 => !!p && [p.x, p.y, p.z].every(Number.isFinite);
const copy = (p: Point3): Point3 => ({ x: p.x, y: p.y, z: p.z });
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
function center(ids: readonly string[], points: ReadonlyMap<string, Point3>): Point3 {
  // Divide before summing, so ordinary large imported coordinates do not overflow.
  let x = 0, y = 0, z = 0;
  for (const id of ids) { const p = bound(points.get(id)!); x += p.x / ids.length; y += p.y / ids.length; z += p.z / ids.length; }
  return { x, y, z };
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
      inputTruncated: view.nodes.length > limits.inputNodes || view.edges.length > limits.inputEdges || validatedPairs.length > limits.inputAffinities,
      affectedNodes: 0, dirtyNodes: 0, pinnedNodes: 0, clusters: 0,
      communityPasses: 0, communityMoves: 0, communityVisits: 0, solverIterations: 0,
      springEvaluations: 0, collisionTests: 0, crowdedCells: 0,
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
        p.weight = p.r + p.s; nextPairs.set(key, p);
      }
    // Normalize stiffness, but retain raw strength in rest lengths. Otherwise
    // uniform cosine/weight changes at high degree cancel out of normalization.
    for (const p of nextPairs.values()) {
      const realRest = .3 + .45 * (1 - (r.get(p.key)?.value ?? 0));
      const semanticRest = .9 - .25 * (s.get(p.key)?.value ?? 0);
      p.rest = (p.r * realRest + p.s * semanticRest) / p.weight;
    }
    const pairs = [...nextPairs.values()].sort((a, b) => order(a.key, b.key));
    const adjacency: Array<Array<{ to: number; weight: number }>> = ids.map(() => []);
    const current = unionFind(ids.length), impact = unionFind(ids.length);
    for (const p of pairs) {
      const a = index.get(p.source)!, b = index.get(p.target)!;
      adjacency[a].push({ to: b, weight: p.weight }); adjacency[b].push({ to: a, weight: p.weight });
      current.join(a, b); impact.join(a, b);
    }
    for (const p of this.pairs.values()) if (index.has(p.source) && index.has(p.target)) impact.join(index.get(p.source)!, index.get(p.target)!);
    const touched = new Set<number>();
    const touch = (id: string) => { const at = index.get(id); if (at !== undefined) touched.add(at); };
    for (const id of ids) if (!this.positions.has(id) || options.changedIds?.has(id) || pinned.has(id) !== this.pins.has(id)) touch(id);
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
    d.clusters = centers.size;
    const anchors = new Map<string, Point3>();
    for (const id of ids) {
      const c = centers.get(clusterIds.get(id)!)!, offset = seed(id), prior = finite(previousPositions.get(id)) || this.positions.has(id);
      anchors.set(id, prior ? copy(base.get(id)!) : bound({ x: c.x + offset.x * .2, y: c.y + offset.y * .2, z: c.z + offset.z * .2 }));
    }
    const movable = ids.map((id, i) => active[i] && !pinned.has(id));
    const forces = new Float64Array(ids.length * 3);
    if (movable.some(Boolean)) for (let iteration = 0; iteration < limits.solverIterations; iteration++) {
      d.solverIterations++; forces.fill(0);
      ids.forEach((id, i) => {
        if (!movable[i]) return;
        const p = bound(anchors.get(id)!), origin = bound(base.get(id)!), c = centers.get(clusterIds.get(id)!)!;
        forces[i * 3] = .12 * (c.x - p.x) + .08 * (origin.x - p.x);
        forces[i * 3 + 1] = .12 * (c.y - p.y) + .08 * (origin.y - p.y);
        forces[i * 3 + 2] = .12 * (c.z - p.z) + .08 * (origin.z - p.z);
      });
      for (const pair of pairs) {
        const a = index.get(pair.source)!, b = index.get(pair.target)!;
        if (!movable[a] && !movable[b]) continue;
        d.springEvaluations++;
        const p = bound(anchors.get(pair.source)!), q = bound(anchors.get(pair.target)!);
        let dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
        if (Math.hypot(dx, dy, dz) < 1e-9) { const v = seed(pair.key); dx = v.x * .001; dy = v.y * .001; dz = v.z * .001; }
        const length = Math.max(1e-9, Math.hypot(dx, dy, dz));
        const gain = 2 * pair.weight * (length - pair.rest) / length;
        for (const [at, sign] of [[a, 1], [b, -1]]) if (movable[at]) {
          forces[at * 3] += sign * gain * dx; forces[at * 3 + 1] += sign * gain * dy; forces[at * 3 + 2] += sign * gain * dz;
        }
      }
      if (d.collisionTests < limits.collisionTests) {
        const cells = new Map<string, number[]>(), cellAt = (p: Point3) => [Math.floor(p.x / .16), Math.floor(p.y / .16), Math.floor(p.z / .16)];
        ids.forEach((id, i) => {
          const key = cellAt(bound(anchors.get(id)!)).join(','), bucket = cells.get(key) ?? [];
          if (bucket.length < limits.cellOccupancy) bucket.push(i);
          else { d.crowdedCells++; d.collisionCandidatesTruncated = true; }
          cells.set(key, bucket);
        });
        collisionLoop: for (let a = 0; a < ids.length; a++) {
          if (!movable[a]) continue;
          const p = bound(anchors.get(ids[a])!), cell = cellAt(p);
          for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
            for (const b of cells.get([cell[0] + x, cell[1] + y, cell[2] + z].join(',')) ?? []) {
              if (a === b || movable[b] && a > b) continue;
              if (d.collisionTests >= limits.collisionTests) { d.collisionBudgetExhausted = true; break collisionLoop; }
              d.collisionTests++;
              const q = bound(anchors.get(ids[b])!);
              let dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
              let length = Math.hypot(dx, dy, dz);
              if (length >= .14) continue;
              if (length < 1e-9) { const v = seed(pairKey(ids[a], ids[b])); dx = v.x; dy = v.y; dz = v.z; length = Math.max(1e-9, Math.hypot(dx, dy, dz)); }
              const gain = 1.5 * (.14 - Math.min(.14, Math.hypot(q.x - p.x, q.y - p.y, q.z - p.z))) / length;
              forces[a * 3] -= gain * dx; forces[a * 3 + 1] -= gain * dy; forces[a * 3 + 2] -= gain * dz;
              if (movable[b]) { forces[b * 3] += gain * dx; forces[b * 3 + 1] += gain * dy; forces[b * 3 + 2] += gain * dz; }
            }
          }
        }
      } else d.collisionBudgetExhausted = true;
      ids.forEach((id, i) => {
        if (!movable[i]) return;
        const p = bound(anchors.get(id)!), at = i * 3;
        const gain = .08 / Math.max(1, Math.hypot(forces[at], forces[at + 1], forces[at + 2]));
        anchors.set(id, bound({ x: p.x + gain * forces[at], y: p.y + gain * forces[at + 1], z: p.z + gain * forces[at + 2] }));
      });
    }
    const dirtyIds = new Set<string>();
    for (const id of ids) {
      const prior = previousPositions.get(id), p = anchors.get(id)!;
      if (!finite(prior) || Math.hypot(p.x - prior.x, p.y - prior.y, p.z - prior.z) > 1e-9) dirtyIds.add(id);
    }
    // No retired positions, pair endpoints or unused centers survive this call.
    this.positions = new Map([...anchors].map(([id, p]) => [id, copy(p)]));
    this.labels = new Map(clusterIds); this.centers = centers; this.pairs = nextPairs; this.pins = pinned;
    d.dirtyNodes = dirtyIds.size; d.cachedNodes = this.positions.size; d.cachedPairs = this.pairs.size; d.cachedClusters = centers.size;
    return { anchors, clusterIds, dirtyIds, diagnostics: d };
  }
}
