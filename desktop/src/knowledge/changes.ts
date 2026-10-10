import type { KnowledgeGraph } from './graph-model';

function memories(graph: KnowledgeGraph): Map<string, string> {
  return new Map(graph.nodes.map(n => [n.id, JSON.stringify([n.label, n.category, n.importance, n.description, n.facts,
    n.space, n.isHub, n.canonicalId, n.sourceVersion, n.sourceTarget, n.sourcePlatform, n.sourceUrl, n.degree, n.degreeScope,
    n.evidence.kind, n.evidence.chatId, [...(n.evidence.roomIds ?? [])].sort(), [...n.evidence.sourceEventIds].sort(), n.evidence.confirmedAt, n.evidence.retracted])]));
}
function relations(graph: KnowledgeGraph): Map<string, string> {
  const groups = new Map<string, string[]>();
  for (const e of graph.edges) {
    const key = JSON.stringify([e.source, e.target, e.relation, e.roomId, e.purpose]);
    const values = groups.get(key) ?? [];
    values.push(JSON.stringify([e.weight, e.context, e.validFrom, e.validTo, e.evidenceMessageId, e.evidence.kind,
      e.evidence.chatId, [...e.evidence.sourceEventIds].sort(), e.evidence.confirmedAt, e.evidence.retracted]));
    groups.set(key, values);
  }
  return new Map([...groups].map(([key, values]) => [key, JSON.stringify(values.sort())]));
}
export function knowledgeSignature(graph: KnowledgeGraph): string {
  const sort = (a: [string, string], b: [string, string]) => a[0].localeCompare(b[0]);
  return JSON.stringify([[...memories(graph)].sort(sort), [...relations(graph)].sort(sort)]);
}
export function knowledgeChangeSummary(before: KnowledgeGraph, after: KnowledgeGraph): string {
  const oldNodes = memories(before), newNodes = memories(after), oldEdges = relations(before), newEdges = relations(after);
  const delta = (old: Map<string, string>, next: Map<string, string>) => ({
    added: [...next.keys()].filter(k => !old.has(k)).length,
    removed: [...old.keys()].filter(k => !next.has(k)).length,
    updated: [...next].filter(([k, v]) => old.has(k) && old.get(k) !== v).length,
  });
  const nodes = delta(oldNodes, newNodes), edges = delta(oldEdges, newEdges), labels: string[] = [];
  for (const [name, d] of [['기억', nodes], ['연결', edges]] as const) {
    if (d.added) labels.push(`${name} +${d.added}`);
    if (d.updated) labels.push(`${name} 갱신 ${d.updated}`);
    if (d.removed) labels.push(`${name} −${d.removed}`);
  }
  return labels.join(' · ');
}
