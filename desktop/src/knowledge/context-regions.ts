import { ON_SCREEN_NODE_CAP, type KnowledgeGraph, type KnowledgeView } from './graph-model';
import { connectedComponents, type Point3 } from './plasticity';

export interface ContextRegion { id: string; label: string; nodeIds: string[] }
export const CONTEXT_REGION_CAP = ON_SCREEN_NODE_CAP;

function activeEdges(graph: KnowledgeGraph, nowMs: number) {
  return graph.edges.filter(edge => {
    const from = Date.parse(edge.validFrom), until = Date.parse(edge.validTo);
    return edge.purpose !== 'navigation' && !edge.evidence.retracted && edge.source !== edge.target
      && !(Number.isFinite(from) && from > nowMs) && !(Number.isFinite(until) && until <= nowMs);
  });
}

/** A view of stored OSK spaces, not a taxonomy or a vault mutation. A memory
 * without a stored space can share its real neighbours' spaces. Only when
 * neither exists do actual semantic components supply an unnamed enclosure.
 */
export function contextRegions(view: KnowledgeView, graph: KnowledgeGraph = view, nowMs = Date.now()): ContextRegion[] {
  const nodes = [...view.nodes].filter(n => !n.evidence.retracted).sort((a, b) => a.id.localeCompare(b.id)).slice(0, ON_SCREEN_NODE_CAP);
  const visible = new Set(nodes.map(n => n.id));
  const memberships = new Map(nodes.map(n => [n.id, new Set<string>()]));
  const labels = new Map<string, string>();
  const spaces = new Map<string, string>();
  const roomSpaces = new Map<string, Set<string>>();
  for (const node of graph.nodes) {
    if (node.evidence.retracted || !node.space?.trim()) continue;
    const space = node.space.trim().replace(/\/+$/, '');
    if (!space) continue;
    const id = 'space:' + space;
    spaces.set(node.id, id);
    if (!labels.has(id)) labels.set(id, space.split('/').pop()!);
    if (node.isHub) labels.set(id, node.label);
    if (/^(?:\d+|kakao:[0-9a-f]{64}:room:\d+)$/.test(node.evidence.chatId)) {
      const candidates = roomSpaces.get(node.evidence.chatId) ?? new Set<string>();
      candidates.add(id); roomSpaces.set(node.evidence.chatId, candidates);
    }
  }
  const visibleSpaces = new Set(nodes.map(node=>spaces.get(node.id)).filter((id):id is string=>!!id));
  for (const node of nodes) {
    const space = spaces.get(node.id);
    if (space) memberships.get(node.id)!.add(space);
    // A multi-room source can legitimately cross stored OSK placements. Use
    // producer-attested room IDs and an unambiguous room-space mapping only.
    // Display memberships never move notes or collapse same-name entities.
    if ((node.evidence.roomIds?.length ?? 0) > 1) {
      const sourceSpaces = new Set<string>();
      for (const room of node.evidence.roomIds!) {
        const candidates = roomSpaces.get(room);
        if (candidates?.size !== 1) continue;
        const context = candidates.values().next().value!;
        if (visibleSpaces.has(context)) sourceSpaces.add(context);
      }
      for (const context of sourceSpaces) memberships.get(node.id)!.add(context);
      // An ancestor source entry is storage, not an extra category bubble,
      // when two visible child contexts are attested by the same original.
      if (space && sourceSpaces.size > 1 && [...sourceSpaces].every(context=>context.startsWith(space+'/'))) memberships.get(node.id)!.delete(space);
    }
  }
  // Original node-space authority only: no recursive propagation through a
  // shared topic, and no global entry/navigation hub as a fake community.
  const edges = activeEdges(graph, nowMs);
  const sharedLinks = new Map<string,Set<string>>();
  for (const edge of edges) for (const [id, neighbour] of [[edge.source, edge.target], [edge.target, edge.source]]) {
    if (!visible.has(id)) continue;
    const space = spaces.get(neighbour);
    if (!space) continue;
    if (!spaces.has(id)) memberships.get(id)!.add(space);
    else if (visibleSpaces.has(space) && space.startsWith(spaces.get(id)!+'/')) {
      const contexts=sharedLinks.get(id)??new Set<string>();contexts.add(space);sharedLinks.set(id,contexts);
    }
  }
  for(const [id,contexts] of sharedLinks) if(contexts.size>1) {
    memberships.get(id)!.delete(spaces.get(id)!);
    for(const space of contexts) memberships.get(id)!.add(space);
  }
  const unplaced = nodes.filter(n => !n.isHub && memberships.get(n.id)!.size === 0).map(n => n.id);
  const components = connectedComponents(unplaced, activeEdges(view, nowMs));
  const groups = new Map<number, string[]>();
  unplaced.forEach((id, i) => { const group = groups.get(components[i]) ?? []; group.push(id); groups.set(components[i], group); });
  for (const ids of groups.values()) {
    const key = 'links:' + JSON.stringify(ids);
    labels.set(key, ids.length === 1 ? nodes.find(n => n.id === ids[0])!.label : '연결된 기억');
    for (const id of ids) memberships.get(id)!.add(key);
  }
  const regions = new Map<string, ContextRegion>();
  for (const node of nodes) for (const id of memberships.get(node.id)!) {
    const region = regions.get(id) ?? { id, label: labels.get(id) ?? '연결된 기억', nodeIds: [] };
    region.nodeIds.push(node.id); regions.set(id, region);
  }
  const primary = new Set(nodes.map(node => spaces.get(node.id)).filter((id): id is string => !!id));
  return [...regions.values()].sort((a, b) => Number(primary.has(b.id)) - Number(primary.has(a.id)) || b.nodeIds.length - a.nodeIds.length || a.id.localeCompare(b.id)).slice(0, CONTEXT_REGION_CAP);
}

export function regionBounds(region: ContextRegion, positions: ReadonlyMap<string, Point3>): Point3 & { radius: number } {
  let x = 0, y = 0, z = 0, count = 0;
  for (const id of region.nodeIds) {
    const point = positions.get(id);
    if (!point || !Number.isFinite(point.x + point.y + point.z)) continue;
    x += point.x; y += point.y; z += point.z; count++;
  }
  if (!count) return { x: 0, y: 0, z: 0, radius: 0 };
  x /= count; y /= count; z /= count;
  let radius = .32 + .06 * Math.sqrt(count);
  for (const id of region.nodeIds) {
    const p = positions.get(id);
    if (p && Number.isFinite(p.x + p.y + p.z)) radius = Math.max(radius, Math.hypot(p.x - x, p.y - y, p.z - z) + .2);
  }
  return { x, y, z, radius };
}
