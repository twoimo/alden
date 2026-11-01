import type { KnowledgeView } from './graph-model';

/** Minimum undirected hops over visible, currently valid reference/semantic
 * edges. The depth is display topology only: it conveys no inferred relation,
 * truth, source hierarchy, or path through a hidden node. O(V + E) time/space.
 */
export function relationDepths(view: KnowledgeView, nowMs = Date.now()): Map<string, number> {
  if (!view.focusId) return new Map();

  const neighbours = new Map<string, string[]>();
  for (const node of view.nodes) {
    if (!node.evidence.retracted) neighbours.set(node.id, []);
  }
  if (!neighbours.has(view.focusId)) return new Map();

  for (const edge of view.edges) {
    if (edge.source === edge.target || edge.purpose === 'navigation' || edge.evidence.retracted) continue;
    const source = neighbours.get(edge.source), target = neighbours.get(edge.target);
    if (!source || !target) continue;
    const from = Date.parse(edge.validFrom), until = Date.parse(edge.validTo);
    if (Number.isFinite(from) && from > nowMs || Number.isFinite(until) && until <= nowMs) continue;
    source.push(edge.target);
    target.push(edge.source);
  }

  const depths = new Map<string, number>([[view.focusId, 0]]);
  const queue = [view.focusId];
  for (let head = 0; head < queue.length; head++) {
    const id = queue[head], nextDepth = depths.get(id)! + 1;
    for (const neighbour of neighbours.get(id)!) {
      if (depths.has(neighbour)) continue;
      depths.set(neighbour, nextDepth);
      queue.push(neighbour);
    }
  }
  return depths;
}
