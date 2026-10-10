import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { knowledgeSignature, knowledgeChangeSummary } from '../knowledge/changes';

const source = () => parseKnowledgeGraph({ nodes: [{ id: 'a', label: 'A', updated_at: 1 }, { id: 'b', label: 'B', updated_at: 1 }],
  edges: [{ source: 'a', target: 'b', relation: 'discusses', weight: 2, evidence: { source_event_ids: ['1'] } }] });
describe('meaningful source changes', () => {
  it('ignores materialization clocks and ordering without manufacturing graph activity', () => {
    const a = source(), b = source(); b.nodes.reverse().forEach(n => n.updatedAt = 999);
    b.edges.reverse(); expect(knowledgeSignature(a)).toBe(knowledgeSignature(b)); expect(knowledgeChangeSummary(a, b)).toBe('');
  });
  it('announces changed evidence, stronger references and withdrawals', () => {
    const a = source(), b = source(); b.nodes[0].evidence.sourceEventIds.push('new-source'); b.edges[0].weight = 6;
    expect(knowledgeChangeSummary(a, b)).toBe('기억 갱신 1 · 연결 갱신 1');
    b.edges = []; expect(knowledgeChangeSummary(a, b)).toContain('연결 −1');
    b.nodes.push({ ...b.nodes[0], id: 'c' }); expect(knowledgeChangeSummary(a, b)).toContain('기억 +1');
  });
  it('preserves multiple evidence instances with the same triple independently of ordering', () => {
    const a = source(); a.edges.push({ ...a.edges[0], context: 'second-observation' });
    const b = { ...a, edges: [...a.edges].reverse() };
    expect(knowledgeSignature(a)).toBe(knowledgeSignature(b));
    expect(knowledgeSignature(a)).not.toBe(knowledgeSignature({ ...a, edges: a.edges.slice(0, 1) }));
  });
  it('invalidates real room membership changes while ignoring source room order', () => {
    const a=source(), b=source(); a.nodes[0].evidence.roomIds=['101','202']; b.nodes[0].evidence.roomIds=['202','101'];
    expect(knowledgeSignature(a)).toBe(knowledgeSignature(b));
    b.nodes[0].evidence.roomIds=['101','303'];
    expect(knowledgeSignature(a)).not.toBe(knowledgeSignature(b));
  });
});
