import { describe, expect, it } from 'vitest';
import { unifiedGraph } from '../knowledge/unified-graph';
import { KnowledgeDrilldown, parseKnowledgeGraph } from '../knowledge/graph-model';

const memory = { ok: true, nodes: [{ id: 'same', label: '기억', category: 'topic', updated_at: 100 },
  { id: 'other', label: '대화', category: 'person', updated_at: 200 }],
  edges: [{ source: 'same', target: 'other', relation: 'linked', evidence_message_id: 'original' }] };
const collection = { ok: true, nodes: [{ id: 'same', label: '원문', source_version: 'v1' }], edges: [], total_nodes: 42, total_edges: 7 };
describe('one permission-preserving graph view', () => {
  it('keeps colliding source identities, canonical detail keys and original relation evidence separate', () => {
    const payload = unifiedGraph(collection, memory, {}), graph = parseKnowledgeGraph(payload);
    expect(graph.nodes.map(node => node.id)).toEqual(['same', 'memory:same', 'memory:other']);
    expect(graph.nodes[1].canonicalId).toBe('same');
    expect(graph.edges[0]).toMatchObject({ source: 'memory:same', target: 'memory:other', evidenceMessageId: 'original' });
    expect(graph.edges).toHaveLength(1); expect(payload.total_nodes).toBe(44); expect(payload.total_edges).toBe(8);
  });
  it('excludes unrelated memory under project, target and platform filters', () => {
    for (const query of [{ projects: ['permitted'] }, { target_id: 'target' }, { platform: 'youtube' }]) {
      expect(parseKnowledgeGraph(unifiedGraph(collection, memory, query)).nodes.map(node => node.id)).toEqual(['same']);
    }
  });
  it('applies search, type, time and relation filters to memory and reports partial reads', () => {
    expect(parseKnowledgeGraph(unifiedGraph(null, memory, { search: '기억', since: 99, until: 101, node_type: 'topic' })).nodes.map(node => node.id)).toEqual(['memory:same']);
    expect(parseKnowledgeGraph(unifiedGraph(null, memory, { relation: 'absent' })).nodes).toEqual([]);
    expect(unifiedGraph({ ok: false }, memory, {})).toMatchObject({ ok: true, partial: true });
  });
  it('admits real overview density while keeping selected expansion bounded at 24', () => {
    const graph = parseKnowledgeGraph(unifiedGraph({ ok: true, nodes: Array.from({ length: 720 }, (_, i) => ({ id: 'n' + i, label: 'N' + i })),
      edges: Array.from({ length: 719 }, (_, i) => ({ source: 'n0', target: 'n' + (i + 1), relation: 'linked' })) }, null, {}));
    const model = new KnowledgeDrilldown(graph); expect(model.current().nodes).toHaveLength(720);
    model.clickNode('n0'); model.expandOneHop(); expect(model.current().nodes.length).toBeLessThanOrEqual(24);
    model.reset(); expect(model.current().nodes).toHaveLength(720);
  });
});
