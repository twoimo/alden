import type { KnowledgeEdge, KnowledgeGraph, KnowledgeNode } from './graph-model';

export interface TerminalLink { node: KnowledgeNode; edge: KnowledgeEdge; direction: 'in' | 'out' }
export interface TerminalSummary {
  nodes: KnowledgeNode[]; edges: KnowledgeEdge[]; recent: KnowledgeNode[]; links: TerminalLink[];
  sectors: Array<{ name: string; count: number }>;
  routes: Array<{ source: string; target: string; count: number }>;
}

/** A projection of the supplied, permitted display slice. Never an identity merge. */
export function terminalSummary(graph: KnowledgeGraph, focus: string | null, now = Date.now()): TerminalSummary {
  const known = new Map<string, KnowledgeNode>();
  for (const node of graph.nodes) if (!node.evidence.retracted && !known.has(node.id)) known.set(node.id, node);
  const nodes = [...known.values()], seen = new Set<string>();
  const edges = graph.edges.filter(edge => {
    if (edge.evidence.retracted || !known.has(edge.source) || !known.has(edge.target)) return false;
    const from = Date.parse(edge.validFrom), until = Date.parse(edge.validTo);
    if (Number.isFinite(from) && from > now || Number.isFinite(until) && until <= now) return false;
    const key = JSON.stringify([edge.source, edge.target, edge.relation, edge.purpose, edge.context, edge.roomId,
      edge.validFrom, edge.validTo, edge.evidenceMessageId, edge.originalSource, edge.originalTarget, edge.weight, [...(edge.evidence.roomIds ?? [])].sort(),
      [...edge.evidence.sourceEventIds].sort(), edge.evidence.chatId, edge.evidence.kind, edge.evidence.confirmedAt]);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
  const sectors = new Map<string, number>(), routes = new Map<string, { source: string; target: string; count: number }>();
  for (const node of nodes) {
    const sector = node.category.trim() || '분류 미정';
    sectors.set(sector, (sectors.get(sector) ?? 0) + 1);

  }
  for (const edge of edges) {
    const source = known.get(edge.source)!.category.trim() || '분류 미정';
    const target = known.get(edge.target)!.category.trim() || '분류 미정';
    const key = JSON.stringify([source, target]);
    const previous = routes.get(key);
    if (previous) previous.count++;
    else routes.set(key, { source, target, count: 1 });
  }
  return { nodes, edges,
    recent: [...nodes].sort((a, b) => b.updatedAt - a.updatedAt || a.id.localeCompare(b.id)).slice(0, 12),
    links: edges.flatMap((edge): TerminalLink[] => edge.source === focus
      ? [{ node: known.get(edge.target)!, edge, direction: 'out' }]
      : edge.target === focus ? [{ node: known.get(edge.source)!, edge, direction: 'in' }] : []).slice(0, 12),
    sectors: [...sectors].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
    routes: [...routes.values()].sort((a, b) => b.count - a.count || a.source.localeCompare(b.source) || a.target.localeCompare(b.target)) };
}
