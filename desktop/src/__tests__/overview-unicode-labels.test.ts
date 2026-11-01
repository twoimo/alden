import { expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { overviewRoutingStar, selectOverviewLabels, selectZoomLabelCandidates } from '../knowledge/overview-labels';

const titles = ['採用 条件', '面接 準備', '転職・相談', 'Условия работы', 'شروط العمل', 'नौकरी तैयारी', 'Εργασία συνθήκες', 'תנאי עבודה'];
it.each(titles)('keeps the saved multi-term title %s eligible without altering its record', label => {
  const nodes = parseKnowledgeGraph({ nodes: [{ id: 'saved', label, category: 'topic' }], edges: [] }).nodes;
  const original = JSON.stringify(nodes);
  expect(selectOverviewLabels(nodes)).toEqual([nodes[0]]);
  expect(selectZoomLabelCandidates(nodes)).toEqual([nodes[0]]);
  expect(selectOverviewLabels(nodes)[0]).toBe(nodes[0]);
  expect(JSON.stringify(nodes)).toBe(original);
  expect(overviewRoutingStar(label, 'source_group')).toBe(false);
});

it.each(['中文', 'ひらがな', '123 456', '😀 😃', 'अि बि', 'graph:資料 条件', 'source:資料 条件', 'readme.md'])('keeps ambiguous or internal title %s out of overview captions', label => {
  const nodes = parseKnowledgeGraph({ nodes: [{ id: 'saved', label, category: 'topic' }], edges: [] }).nodes;
  expect(selectOverviewLabels(nodes)).toEqual([]);
});

it('keeps source evidence, person exclusion and normalized duplicate rules for Unicode titles', () => {
  const nodes = parseKnowledgeGraph({ nodes: [
    { id: 'person', label: '採用 条件', category: 'person', degree: 10000 },
    { id: 'reference', label: '面接 準備', category: 'topic', source_metadata: { content_evidence: 'reference_only' } },
    { id: 'saved', label: 'شروط العمل', category: 'topic', degree: 2 },
    { id: 'duplicate', label: 'شروط  العمل', category: 'topic', degree: 1 },
  ], edges: [] }).nodes;
  const before = JSON.stringify(nodes);
  expect(selectOverviewLabels(nodes).map(node => node.id)).toEqual(['saved']);
  expect(selectOverviewLabels([...nodes].reverse()).map(node => node.id)).toEqual(['saved']);
  expect(JSON.stringify(nodes)).toBe(before);
});
