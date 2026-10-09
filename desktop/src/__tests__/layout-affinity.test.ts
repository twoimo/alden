import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { parseLayoutAffinity } from '../knowledge/layout-affinity';
import { knowledgeSignature } from '../knowledge/changes';
import { affinityPayload } from './fixtures/affinity-payload';

const graph = () => parseKnowledgeGraph({ nodes: ['a', 'b'].map(id => ({ id, label: id, source_version: 'v1', source_target: 'target-a' })),
  edges: [{ source: 'a', target: 'b', relation: 'contradicts', weight: 1 }] });

describe('source-bound separate layout channel', () => {
  it('accepts compatible provenance without changing fact edges or activity signatures', () => {
    const current = graph(), signature = knowledgeSignature(current), facts = JSON.stringify(current.edges);
    const parsed = parseLayoutAffinity(affinityPayload(), current.nodes, ['one']);
    expect(parsed.reason).toBeNull(); expect(parsed.affinity?.pairs).toEqual([{ source: 'a', target: 'b', cosine: .9 }]);
    current.layoutAffinity = parsed.affinity!;
    expect(JSON.stringify(current.edges)).toBe(facts); expect(knowledgeSignature(current)).toBe(signature);
  });

  it('rejects stale version, different target, incomplete and duplicated input bindings', () => {
    for (const payload of [affinityPayload(['a', 'b'], 'one', 'old'), affinityPayload(['a', 'b'], 'one', 'v1', 'other'), affinityPayload(['a']), affinityPayload(['a', 'a'])]) {
      expect(parseLayoutAffinity(payload, graph().nodes).affinity).toBeNull();
    }
  });

  it('cannot include a denied project or turn a memory overlay into a collection node', () => {
    const payload = affinityPayload();
    expect(parseLayoutAffinity(payload, graph().nodes, ['two']).reason).toBe('affinity_invalid_scope');
    payload.nodes[0].permissions[0].permission = 'denied';
    expect(parseLayoutAffinity(payload, graph().nodes).reason).toBe('affinity_invalid_proofs');
    const current = graph(); current.nodes[0].id = 'memory:a';
    expect(parseLayoutAffinity(affinityPayload(), current.nodes).reason).toBe('affinity_stale_input');
  });

  it('rejects incompatible profiles and malformed vector/source proof instead of rendering guesses', () => {
    for (const field of ['model', 'encoding', 'endpoint', 'dimension']) {
      const payload = affinityPayload(); (payload.profile as Record<string, unknown>)[field] = 'other';
      expect(parseLayoutAffinity(payload, graph().nodes).reason).toBe('affinity_incompatible_profile');
    }
    for (const field of ['raw_path', 'vector_sha256', 'projection_sha256', 'base_text_sha256', 'body_source', 'vector_norm']) {
      const payload = affinityPayload(); (payload.nodes[0] as Record<string, unknown>)[field] = field === 'vector_norm' ? NaN : 'bad';
      expect(parseLayoutAffinity(payload, graph().nodes).reason).toBe('affinity_invalid_proofs');
    }
  });

  it('rejects invalid candidate endpoints, cross-project and nonfinite cosine', () => {
    for (const mutation of [({ target: 'outside' }), { target: 'a' }, { cosine: NaN }, { cosine: Infinity }, { cosine: 1.1 }, { projects: ['two'] }]) {
      const payload = affinityPayload(); Object.assign(payload.candidates[0], mutation);
      expect(parseLayoutAffinity(payload, graph().nodes).reason).toBe('affinity_invalid_candidates');
    }
  });

  it('reports explicit missing/budget failure and rejects inconsistent coverage', () => {
    expect(parseLayoutAffinity({ schema: 'alden-layout-affinity-v1', state: 'unavailable', reason: 'affinity_cpu_budget' }, graph().nodes).reason).toBe('affinity_cpu_budget');
    const payload = affinityPayload(); payload.coverage.usable_vector_nodes = 0;
    expect(parseLayoutAffinity(payload, graph().nodes).reason).toBe('affinity_invalid_coverage');
  });
});
