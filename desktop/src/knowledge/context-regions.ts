import { ON_SCREEN_NODE_CAP, type KnowledgeGraph, type KnowledgeView } from './graph-model';
import type { Point3 } from './plasticity';

export interface ContextRegion { id: string; label: string; nodeIds: string[] }
export const CONTEXT_REGION_CAP = ON_SCREEN_NODE_CAP;

/** Visual envelopes of observed source membership, never semantic links. */
export function sourceRegions(view: KnowledgeView): ContextRegion[] {
  const regions = new Map<string, ContextRegion>();
  for (const node of view.nodes) {
    if (node.evidence.retracted) continue;
    const source = node.sourceTarget || node.space || 'memory';
    const id = 'source:' + source;
    const region = regions.get(id) ?? {id, label:source, nodeIds:[]};
    region.nodeIds.push(node.id);regions.set(id,region);
  }
  return [...regions.values()].sort((a,b)=>b.nodeIds.length-a.nodeIds.length||a.id.localeCompare(b.id)).slice(0,CONTEXT_REGION_CAP);
}

function activeHubLinks(graph: KnowledgeGraph, nowMs: number) {
  return graph.edges.filter(edge => {
    const from = Date.parse(edge.validFrom), until = Date.parse(edge.validTo);
    return (edge.relation === 'linked' || edge.relation === 'derived-from')
      && !edge.evidence.retracted && edge.source !== edge.target
      && !(Number.isFinite(from) && from > nowMs) && !(Number.isFinite(until) && until <= nowMs);
  });
}

/** Project canonical OSK directories and actual hubs without changing storage.
 * Only a note's own directory and direct Links/derived-from to an existing hub
 * confer membership. Ordinary neighbours and room metadata have no authority.
 */
export function contextRegions(view: KnowledgeView, graph: KnowledgeGraph = view, nowMs = Date.now()): ContextRegion[] {
  const stored = new Map(graph.nodes.filter(node => !node.evidence.retracted).map(node => [node.id, node]));
  const nodes = view.nodes.flatMap(node => stored.get(node.id) ?? [])
    .sort((a, b) => a.id.localeCompare(b.id)).slice(0, ON_SCREEN_NODE_CAP);
  const memberships = new Map(nodes.map(n => [n.id, new Set<string>()]));
  const labels = new Map<string, string>();
  const primary = new Set<string>();
  for (const node of nodes) {
    if (node.space?.trim()) {
      const id = 'space:' + node.space;
      memberships.get(node.id)!.add(id);
      labels.set(id, node.space.split('/').pop()!);
      primary.add(id);
    }
  }
  for (const hub of stored.values()) {
    if (!hub.isHub) continue;
    const id = 'hub:' + hub.id;
    labels.set(id, hub.label);
    memberships.get(hub.id)?.add(id);
  }
  for (const edge of activeHubLinks(graph, nowMs)) {
    for (const [hubId, memberId] of [[edge.source, edge.target], [edge.target, edge.source]]) {
      if (stored.get(hubId)?.isHub) memberships.get(memberId)?.add('hub:' + hubId);
    }
  }
  const regions = new Map<string, ContextRegion>();
  for (const node of nodes) for (const id of memberships.get(node.id)!) {
    const region = regions.get(id) ?? { id, label: labels.get(id)!, nodeIds: [] };
    region.nodeIds.push(node.id); regions.set(id, region);
  }
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
