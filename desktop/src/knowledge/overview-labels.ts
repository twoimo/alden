import type { KnowledgeNode } from './graph-model';

const GENERIC = /^(?:기타(?:[·\s/]+미분류)?|미분류|일반\s*동영상|전체\s*동영상|동영상|영상|unknown|untitled|snippets?|index|readme(?:\.md)?|src|dist|node_modules)$/i;
const BUCKET = /^(?:홍보(?:[·\s]+안내)?|외부\s*자료(?:[·\s]+링크)?)$/;
const PEOPLE = /^(?:author|person|creator|channel|source_group|인물|사람|대화 상대|화자)$/i;
const TOPIC = /^(?:topic|concept|subject|주제|개념)$/i;
const CLAIM = /^(?:claim|source_claim|memory|note|knowledge|지식|기억)$/i;

/** Display choices from saved labels, not relevance/truth or identity decisions.
 * Keep every graph node and hover/focus target. Rank once when rebuilding.
 */
export function selectOverviewLabels(nodes: readonly KnowledgeNode[], cap = 8): KnowledgeNode[] {
  const limit = Math.min(8, Math.max(0, Math.floor(Number.isFinite(cap) ? cap : 0)));
  const candidates = nodes.flatMap(node => {
    const label = node.label.normalize('NFC').replace(/\s+/g, ' ').trim();
    if (node.evidence.retracted || (node.contentEvidence && node.contentEvidence !== 'content_candidate')
      || PEOPLE.test(node.category) || ['collection','hub','index','metadata'].includes(node.category)
      || GENERIC.test(label) || BUCKET.test(label) || !/[A-Za-z가-힣]/.test(label)
      || /^@[^\s]+$/.test(label) || /^(?:graph|source|target|version|youtube|threads|files|memory):/i.test(label)
      || /:(?:node|series|dir|hub):/i.test(label)
      || /^[0-9a-f]{32,}$/i.test(label) || label.length < 2) return [];
    const tier = TOPIC.test(node.category) ? 0 : CLAIM.test(node.category) ? 1 : node.category === 'code' ? 3 : 2;
    if (tier >= 2 && label.length < 8) return [];
    return [{node, label, tier, group: node.sourceTarget || node.space || 'unscoped:' + node.id}];
  }).sort((a, b) => a.tier - b.tier
    || (b.node.degree ?? 0) - (a.node.degree ?? 0)
    || a.label.localeCompare(b.label) || a.node.id.localeCompare(b.node.id));
  const chosen: KnowledgeNode[] = [], groups = new Set<string>(), labels = new Set<string>();
  const add = (item: typeof candidates[number]): boolean => {
    if (labels.has(item.label) || chosen.length >= limit) return false;
    chosen.push(item.node); labels.add(item.label); groups.add(item.group); return true;
  };
  // Saved subjects fill the overview by their actual connections. Another
  // source gets a slot only after those subjects, then remaining titles.
  for (const item of candidates) if (item.tier === 0) add(item);
  for (const item of candidates) if (!groups.has(item.group)) add(item);
  for (const item of candidates) add(item);
  return chosen;
}
