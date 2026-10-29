import { describe, expect, it } from 'vitest';
import { terminalSummary } from '../knowledge/terminal-model';
import { parseKnowledgeGraph } from '../knowledge/graph-model';

const now = Date.parse('2026-10-20T12:00:00Z');
describe('permitted terminal display-slice aggregates', () => {
  it('retains real connections within a single saved type', () => {
    const graph = parseKnowledgeGraph({ nodes: [{ id: 'a', label: '지원 준비', category: 'topic' },
      { id: 'b', label: '면접', category: 'topic' }], edges: [{ source: 'a', target: 'b', relation: 'related-to' }] }, now);
    const data = terminalSummary(graph, null, now);
    expect(data.edges).toHaveLength(1);
    expect(data.routes).toEqual([{ source: 'topic', target: 'topic', count: 1 }]);
  });
  it('deduplicates exact transport repeats without merging direction, relation or evidence', () => {
    const raw = { source: 'a', target: 'b', relation: 'supports', evidence_message_id: 'proof-1' };
    const graph = parseKnowledgeGraph({ nodes: [{ id: 'a', label: 'A', category: '개념' }, { id: 'a', label: 'A copy' },
      { id: 'b', label: 'B', category: '자료' }], edges: [raw, { ...raw }, { ...raw, source: 'b', target: 'a' },
      { ...raw, relation: 'contradicts' }, { ...raw, evidence_message_id: 'proof-2' }] }, now);
    const data = terminalSummary(graph, 'a', now);
    expect(data.nodes.map(node => node.label)).toEqual(['A', 'B']);
    expect(data.edges).toHaveLength(4);
    expect(data.links.map(link => link.direction)).toEqual(['out', 'in', 'out', 'out']);
    expect(data.routes).toEqual([{ source: '개념', target: '자료', count: 3 }, { source: '자료', target: '개념', count: 1 }]);
    expect(graph.nodes).toHaveLength(3);
  });
  it('rechecks time and retraction after a graph snapshot was parsed', () => {
    const graph = parseKnowledgeGraph({ nodes: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }],
      edges: [{ source: 'a', target: 'b', relation: 'expires', valid_to: '2026-10-20T11:59:00Z' },
        { source: 'b', target: 'a', relation: 'starts', valid_from: '2026-10-21T00:00:00Z' }] }, now - 3600000);
    graph.edges.push({ ...graph.edges[0], target: 'missing', validTo: '' });
    expect(terminalSummary(graph, 'a', now).edges).toEqual([]);
    graph.nodes[1].evidence.retracted = true;
    expect(terminalSummary(graph, 'a', now).nodes.map(node => node.id)).toEqual(['a']);
  });
  it('does not derive intake counts from graph update timestamps', () => {
    const graph = parseKnowledgeGraph({ nodes: [
      { id: 'a', label: 'A', category: '자료', updated_at: Date.parse('2026-10-19T23:59:00Z') / 1000 },
      { id: 'b', label: 'B', category: '자료', updated_at: 0 },
      { id: 'c', label: 'C', category: '', updated_at: (now + 1000) / 1000 },
    ], edges: [] }, now);
    const data = terminalSummary(graph, null, now);
    expect(data).not.toHaveProperty('days');
    expect(data).not.toHaveProperty('undated');
    expect(data.sectors).toEqual([{ name: '자료', count: 2 }, { name: '분류 미정', count: 1 }]);
    expect(data.links).toEqual([]);
  });
});
