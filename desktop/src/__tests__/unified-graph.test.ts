import { describe, expect, it } from 'vitest';
import { unifiedGraph } from '../knowledge/unified-graph';
import { KnowledgeDrilldown, parseKnowledgeGraph } from '../knowledge/graph-model';

const memory = { ok: true, nodes: [{ id: 'same', label: '기억', category: 'topic', updated_at: 100 },
  { id: 'other', label: '대화', category: 'person', updated_at: 200 }],
  edges: [{ source: 'same', target: 'other', relation: 'linked', evidence_message_id: 'original' }] };
const collection = { ok: true, nodes: [{ id: 'same', label: '원문', source_version: 'v1' }], edges: [], total_nodes: 42, total_edges: 7 };
describe('one permission-preserving graph view', () => {
  it('preserves an upstream partial-source warning instead of promoting an empty union to complete', () => {
    expect(unifiedGraph({ok:true,nodes:[],partial:true},{ok:true,nodes:[]},{})).toMatchObject({ok:true,nodes:[],partial:true});
    expect(unifiedGraph({ok:true,nodes:[]},{ok:true,nodes:[],partial:true},{})).toMatchObject({partial:true});
    expect(unifiedGraph({ok:true,nodes:[]},{ok:true,nodes:[],partial:true},{projects:['one']})).toMatchObject({partial:false});
  });

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
  it('does not mark a fresh collection unverified because memory sync is old', () => {
    expect(unifiedGraph(collection, { ...memory, stale: true }, {})).toMatchObject({ stale: false, ok: true, partial: false });
    expect(unifiedGraph({ ...collection, stale: true }, memory, {})).toMatchObject({ stale: true });
    expect(unifiedGraph(null, { ...memory, stale: true }, {})).toMatchObject({ stale: true });
    expect(unifiedGraph({ ok: false, stale: false }, { ...memory, stale: true }, {})).toMatchObject({ stale: true, partial: true });
  });
});
