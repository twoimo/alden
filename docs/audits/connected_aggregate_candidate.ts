/** Reversible W-A03 candidate, used only by the audit build. This is neither
 * Leiden nor a semantic/identity merge. The product does not import it. */
export const CONNECTED_AGGREGATE_PASSES = 8;
export interface CommunityNeighbor { to: number; weight: number }
export interface ConnectedAggregateResult {
  roots: number[];
  passes: number;
  merges: number;
  connectivitySplits: number;
  visits: number;
  mergeObjectiveGain: number;
  termination: 'stable' | 'budget';
}

/** Input is the engine's admitted, positive, symmetric community adjacency.
 * Affected components have already rerun the existing local mover from
 * singletons. Unaffected labels and all layout coordinates remain owned by
 * the engine. Per-component volume keeps unrelated components independent. */
export function refineConnectedCommunities(
  adjacency: readonly (readonly CommunityNeighbor[])[],
  labels: readonly string[],
  active: readonly boolean[],
  components: readonly number[],
): ConnectedAggregateResult {
  const n = adjacency.length;
  if ([labels.length, active.length, components.length].some(size => size !== n)) throw new Error('community_candidate_shape');
  const roots = Array.from({ length: n }, () => -1);
  const degrees = adjacency.map(list => list.reduce((sum, p) => sum + p.weight, 0));
  const componentVolumes = new Map<number, number>();
  for (let i = 0; i < n; i++) componentVolumes.set(components[i], (componentVolumes.get(components[i]) ?? 0) + degrees[i]);
  let visits = 0, connectivitySplits = 0, merges = 0, passes = 0, mergeObjectiveGain = 0;
  const seen = new Map<number, Set<string>>();
  // A local move can disconnect the community it leaves. Split only along
  // missing admitted edges, never by source, title, generated truth or size.
  for (let i = 0; i < n; i++) {
    if (!active[i] || roots[i] >= 0) continue;
    const existing = seen.get(components[i]) ?? new Set<string>();
    if (existing.has(labels[i])) connectivitySplits++;
    existing.add(labels[i]); seen.set(components[i], existing);
    const queue = [i]; roots[i] = i;
    for (let at = 0; at < queue.length; at++) for (const p of adjacency[queue[at]]) {
      visits++;
      if (active[p.to] && roots[p.to] < 0 && labels[p.to] === labels[i]) { roots[p.to] = i; queue.push(p.to); }
    }
  }
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (value: number): number => {
    let root = value;
    while (parent[root] !== root) root = parent[root];
    while (parent[value] !== value) { const next = parent[value]; parent[value] = root; value = next; }
    return root;
  };
  for (; passes < CONNECTED_AGGREGATE_PASSES;) {
    passes++;
    const volumes = new Map<number, number>();
    for (let i = 0; i < n; i++) if (active[i]) {
      roots[i] = find(roots[i]);
      volumes.set(roots[i], (volumes.get(roots[i]) ?? 0) + degrees[i]);
    }
    const between = new Map<number, Map<number, number>>();
    for (let i = 0; i < n; i++) if (active[i]) for (const p of adjacency[i]) {
      visits++;
      if (i >= p.to || !active[p.to] || roots[i] === roots[p.to]) continue;
      const a = Math.min(roots[i], roots[p.to]), b = Math.max(roots[i], roots[p.to]);
      const neighbors = between.get(a) ?? new Map<number, number>();
      neighbors.set(b, (neighbors.get(b) ?? 0) + p.weight); between.set(a, neighbors);
    }
    const candidates: Array<{ a: number; b: number; gain: number; volume: number }> = [];
    for (const [a, neighbors] of between) for (const [b, weight] of neighbors) {
      const volume = componentVolumes.get(components[a])!;
      // Delta Q = 2 * (w_AB - k_A*k_B / componentVolume) / componentVolume.
      const gain = weight - volumes.get(a)! * volumes.get(b)! / volume;
      if (gain > 1e-12) candidates.push({ a, b, gain, volume });
    }
    candidates.sort((a, b) => b.gain / b.volume - a.gain / a.volume || a.a - b.a || a.b - b.b);
    if (!candidates.length) return { roots, passes, merges, connectivitySplits, visits, mergeObjectiveGain, termination: 'stable' };
    const used = new Set<number>();
    // Disjoint merges make every stored gain valid for this round. Rebuild
    // weights next round rather than using stale gains after adjacent merges.
    for (const candidate of candidates) {
      if (used.has(candidate.a) || used.has(candidate.b)) continue;
      parent[candidate.b] = candidate.a;
      used.add(candidate.a); used.add(candidate.b); merges++;
      mergeObjectiveGain += 2 * candidate.gain / candidate.volume;
    }
  }
  for (let i = 0; i < n; i++) if (active[i]) roots[i] = find(roots[i]);
  return { roots, passes, merges, connectivitySplits, visits, mergeObjectiveGain, termination: 'budget' };
}
