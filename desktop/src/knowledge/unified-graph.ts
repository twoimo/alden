import { parseKnowledgeGraph } from './graph-model';

/** A view union, never an identity merge or permission expansion. The memory
 * reader remains authoritative for its scope; collection filters stay remote. */
export function unifiedGraph(collection: Record<string, unknown> | null, memory: Record<string, unknown> | null,
  query: Record<string, unknown>): Record<string, unknown> {
  const stored = parseKnowledgeGraph(memory);
  const search = String(query.search ?? '').normalize('NFKC').toLocaleLowerCase();
  const collectionOnly = Boolean(query.projects || query.target_id || query.platform);
  const nodes = collectionOnly ? [] : stored.nodes.filter(node =>
    (!search || [node.label, node.description, ...(node.facts ?? [])].join(' ').normalize('NFKC').toLocaleLowerCase().includes(search))
    && (!query.node_type || node.category === query.node_type)
    && (!query.since || node.updatedAt >= Number(query.since))
    && (!query.until || node.updatedAt < Number(query.until)));
  let ids = new Set(nodes.map(node => node.id));
  const edges = stored.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)
    && (!query.relation || edge.relation === query.relation));
  if (query.relation) { ids = new Set(edges.flatMap(edge => [edge.source, edge.target])); }
  const prefix = (id: string) => 'memory:' + id;
  const visibleMemory = nodes.filter(node => ids.has(node.id));
  const collectionNodes = Array.isArray(collection?.nodes) ? collection.nodes : [];
  const shownMemory = visibleMemory.slice(0, collectionNodes.length ? 64 : 2048);
  const shownIds = new Set(shownMemory.map(node => node.id));
  const rawMemory = shownMemory.map(node => ({ ...node, id: prefix(node.id), canonical_id: node.id,
    updated_at: node.updatedAt, is_hub: node.isHub, osk_id: node.oskId,
    evidence: { ...node.evidence, source_event_ids: node.evidence.sourceEventIds, chat_id: node.evidence.chatId,
      room_ids: node.evidence.roomIds, confirmed_at: node.evidence.confirmedAt } }));
  const rawEdges = edges.filter(edge => shownIds.has(edge.source) && shownIds.has(edge.target)).map(edge => ({ ...edge, source: prefix(edge.source), target: prefix(edge.target),
    room_id: edge.roomId, valid_from: edge.validFrom, valid_to: edge.validTo, evidence_message_id: edge.evidenceMessageId,
    evidence: { ...edge.evidence, source_event_ids: edge.evidence.sourceEventIds, chat_id: edge.evidence.chatId,
      room_ids: edge.evidence.roomIds, confirmed_at: edge.evidence.confirmedAt } }));
  const collectionEdges = Array.isArray(collection?.edges) ? collection.edges : [];
  return { ...collection, ok: collection?.ok === true || memory?.ok === true,
    nodes: [...collectionNodes, ...rawMemory], edges: [...collectionEdges, ...rawEdges],
    total_nodes: Number(collection?.total_nodes ?? collectionNodes.length) + visibleMemory.length,
    total_edges: Number(collection?.total_edges ?? collectionEdges.length) + edges.length,
    overview_budget: 2048, stale: collection?.stale === true || memory?.stale === true, partial: (collection !== null && collection.ok !== true) || (memory !== null && memory.ok !== true),
    facets: collection?.facets, next: collection?.next ?? null };
}
