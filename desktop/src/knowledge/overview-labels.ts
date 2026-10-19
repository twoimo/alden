import type { KnowledgeNode } from './graph-model';

const GENERIC = /^(?:기타(?:[·\s/]+미분류)?|미분류|일반\s*동영상|전체\s*동영상|동영상|영상|unknown|untitled|snippets?|index|readme(?:\.md)?|src|dist|node_modules)$/i;
const BUCKET = /^(?:홍보(?:[·\s]+안내)?|외부\s*자료(?:[·\s]+링크)?)$/;
const PEOPLE = /^(?:author|person|creator|channel|source_group|인물|사람|대화 상대|화자)$/i;
const TOPIC = /^(?:topic|concept|subject|주제|개념)$/i;
const CLAIM = /^(?:claim|source_claim|memory|note|knowledge|지식|기억)$/i;
// Concise saved subjects used by the career graph, not inferred person names.
const CONCISE_SUBJECTS = new Set(['자소서', '면접']);
const CHANNEL_TITLE = /(?:채널|티비|tv|channel)$/i;
const MEDIA_BUCKET = /(?:^|[·\s/])(?:(?:일반|전체)\s*(?:동)?영상|(?:동)?영상\s*(?:모음|목록)|snippets?)(?:\s*\d+\s*(?:개|편|건)?)?$/i;

function descriptiveOverviewLabel(label: string): boolean {
  const terms = label.split(/[·\s/]+/).filter(term => /[A-Za-z가-힣]{2}/.test(term));
  // Ambiguous single-term labels stay on their nodes for hover/focus access.
  // This is a display heuristic, not a person or content classification.
  return terms.length >= 2 && !CHANNEL_TITLE.test(label) && !MEDIA_BUCKET.test(label);
}

/** Saved routing ids are not titles. Callers still keep the node itself. */
export function internalGraphLabel(label: string): boolean {
  const text = label.normalize('NFC').replace(/\s+/g, ' ').trim();
  return /^(?:graph|source|target|version|youtube|threads|files|memory):/i.test(text)
    || /:(?:node|series|dir|hub):/i.test(text)
    || /^[0-9a-f]{32,}$/i.test(text);
}

/** A saved bucket can remain in the graph without becoming an overview star.
 * Display geometry only. The record, label and edges stay stored. */
export function overviewRoutingStar(label: string, category?: string): boolean {
  const text = label.normalize('NFC').replace(/\s+/g, ' ').trim();
  if (GENERIC.test(text) || BUCKET.test(text)) return true;
  if ((category === 'hub' || category === 'code') && !/[가-힣]/.test(text)) return true;
  if (category !== 'source_group') return false;
  const terms = text.split(/[·\s/]+/).filter(term => /[A-Za-z가-힣]{2}/.test(term));
  return terms.length < 2;
}

/** Display choices from saved labels, not relevance/truth or identity decisions.
 * Keep every graph node and hover/focus target. Rank once when rebuilding.
 */
export function selectOverviewLabels(nodes: readonly KnowledgeNode[], cap = 8): KnowledgeNode[] {
  const limit = Math.min(8, Math.max(0, Math.floor(Number.isFinite(cap) ? cap : 0)));
  return selectSavedLabels(nodes, limit);
}

/** Ranked once per graph. Screen-space selection, not a new semantic claim. */
export function selectZoomLabelCandidates(nodes: readonly KnowledgeNode[]): KnowledgeNode[] {
  return selectSavedLabels(nodes, 2048);
}

function selectSavedLabels(nodes: readonly KnowledgeNode[], limit: number): KnowledgeNode[] {
  const candidates = nodes.flatMap(node => {
    const label = node.label.normalize('NFC').replace(/\s+/g, ' ').trim();
    if (node.evidence.retracted || (node.contentEvidence && node.contentEvidence !== 'content_candidate')
      || PEOPLE.test(node.category) || ['collection','hub','index','metadata'].includes(node.category)
      || GENERIC.test(label) || BUCKET.test(label) || !/[A-Za-z가-힣]/.test(label)
      || /^@[^\s]+$/.test(label) || internalGraphLabel(label)
      || !(descriptiveOverviewLabel(label) || TOPIC.test(node.category) && CONCISE_SUBJECTS.has(label))) return [];
    const tier = TOPIC.test(node.category) ? 0 : CLAIM.test(node.category) ? 1 : node.category === 'code' ? 3 : 2;
    if (tier >= 2 && label.length < 8) return [];
    return [{node, label, tier,
      group: node.sourceTarget || node.space || 'unscoped:' + node.id}];
  }).sort((a, b) => a.tier - b.tier
    || (b.node.degree ?? 0) - (a.node.degree ?? 0)
    || a.label.localeCompare(b.label) || a.node.id.localeCompare(b.node.id));
  const chosen: KnowledgeNode[] = [], groups = new Set<string>(), labels = new Set<string>();
  const add = (item: typeof candidates[number]): boolean => {
    // Compare NFC/whitespace keys only; keep saved text, case and punctuation.
    if (labels.has(item.label) || chosen.length >= limit) return false;
    chosen.push(item.node); labels.add(item.label); groups.add(item.group); return true;
  };
  // Leave unused slots empty when no descriptive saved labels are available.
  // Retain subject, connection and source preferences among eligible labels.
  for (const item of candidates) if (item.tier === 0) add(item);
  for (const item of candidates) if (!groups.has(item.group)) add(item);
  for (const item of candidates) add(item);
  return chosen;
}
