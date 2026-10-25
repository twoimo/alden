import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { parseLayoutAffinity } from '../knowledge/layout-affinity';
import { knowledgeSignature } from '../knowledge/changes';
import { affinityPayload } from './fixtures/affinity-payload';

const graph = (ids = ['a', 'b']) => parseKnowledgeGraph({ nodes: ids.map(id => ({ id, label: id, source_version: 'v1', source_target: 'target-a' })),
  edges: ids.includes('a') && ids.includes('b') ? [{ source: 'a', target: 'b', relation: 'contradicts', weight: 1 }] : [] });

const sampledPayload = (ids = ['a', 'b']): any => {
  const payload = affinityPayload(ids);
  Object.assign(payload.coverage, { pair_sampling: 'project-round-robin-v1', vector_nodes_compared: 2,
    eligible_vector_nodes: 2, comparison_min: 1, comparison_max: 1, node_pair_coverage: 1,
    possible_vector_pairs: 1, eligible_same_project_pairs: 1, pair_coverage: 1,
    pair_budget_exhausted: false, candidates_returned: 1 });
  return payload;
};

const partitionPayload = (): any => {
  const ids = Array.from({ length: 128 }, (_, i) => `n${i}`), payload = sampledPayload(ids);
  payload.state = 'bounded_partial';
  payload.budgets = { max_pairs: 4096, max_products: 25_165_824 };
  Object.assign(payload.coverage, { pair_sampling: 'sparse-partition-tournament-v1',
    vector_nodes_compared: 128, eligible_vector_nodes: 128, comparison_min: 1, comparison_max: 127,
    possible_vector_pairs: 8128, eligible_same_project_pairs: 8128, pairs_evaluated: 4096,
    node_pair_coverage: 1, pair_coverage: 4096 / 8128, pair_budget_exhausted: true,
    dimension_products: 4096 * 384, projection_products: 24576,
    total_dimension_products: 4096 * 384 + 24576, projection_trees: 4,
    projection_width: 16, projection_leaf_size: 16, coverage_seed_pairs: 64 });
  return payload;
};

describe('source-bound separate layout channel', () => {
  it('admits partition evidence with independently checked arithmetic and preserves graph facts', () => {
    const payload = partitionPayload(), current = graph(payload.input_nodes.map((n: any) => n.id));
    const facts = JSON.stringify(current.edges);
    const parsed = parseLayoutAffinity(payload, current.nodes);
    expect(parsed.reason).toBeNull();
    expect(parsed.affinity).toMatchObject({ pairSampling: 'sparse-partition-tournament-v1',
      evaluatedPairs: 4096, projectionProducts: 24576, totalProducts: 4096 * 384 + 24576 });
    expect(JSON.stringify(current.edges)).toBe(facts);
  });

  it('rejects partial, impossible and over-budget partition work claims', () => {
    const mutations = [
      (p: any) => { delete p.coverage.projection_products; },
      (p: any) => { p.coverage.total_dimension_products--; },
      (p: any) => { p.coverage.dimension_products++; },
      (p: any) => { p.coverage.projection_trees = 3; },
      (p: any) => { p.coverage.projection_products = 1; p.coverage.total_dimension_products = 4096 * 384 + 1; },
      (p: any) => { p.coverage.coverage_seed_pairs = 0; },
      (p: any) => { p.coverage.pair_sampling = 'project-round-robin-v1'; },
      (p: any) => { p.budgets.max_products = 1; },
      (p: any) => { p.budgets.max_pairs = 4095; },
      (p: any) => { delete p.budgets; },
    ];
    for (const change of mutations) {
      const payload = partitionPayload(); change(payload);
      expect(parseLayoutAffinity(payload, graph(payload.input_nodes.map((n: any) => n.id)).nodes).reason).toBe('affinity_invalid_coverage');
    }
  });

  it('validates measured zero projection work and leaves legacy absence unreported', () => {
    const payload = sampledPayload(); payload.budgets = { max_pairs: 65536, max_products: 25165824 };
    Object.assign(payload.coverage, { dimension_products: 384, projection_products: 0, total_dimension_products: 384,
      projection_trees: 0, projection_width: 0, projection_leaf_size: 0, coverage_seed_pairs: 0 });
    expect(parseLayoutAffinity(payload, graph().nodes).affinity).toMatchObject({ projectionProducts: 0, totalProducts: 384 });
    expect(parseLayoutAffinity(sampledPayload(), graph().nodes).affinity).toMatchObject({ projectionProducts: null, totalProducts: null });
  });
  it('rejects old affinities predating reference-only or empty-body exclusion',()=>{
    for (const policy of ['source-content-evidence-v4', 'source-content-evidence-v5']) {
      const payload=affinityPayload();payload.content_evidence_policy=policy;
      expect(parseLayoutAffinity(payload,graph().nodes).reason).toBe('affinity_unverified_content_evidence');
    }
  });
  it('rejects old or unbound content policy instead of reusing filename affinity', () => {
    for (const key of ['content_evidence_policy', 'binding']) {
      const payload: any = affinityPayload(); delete payload[key];
      expect(parseLayoutAffinity(payload, graph().nodes).reason).toBe('affinity_unverified_content_evidence');
    }
  });

  it('retains structural facts while accepting a content-excluded partial result', () => {
    const current = graph(), before = JSON.stringify(current.edges), payload = affinityPayload();
    payload.state = 'bounded_partial'; payload.nodes.pop(); payload.candidates = [];
    payload.coverage.usable_vector_nodes = 1; payload.coverage.content_excluded_nodes = 1;
    const parsed = parseLayoutAffinity(payload, current.nodes);
    expect(parsed.affinity?.pairs).toEqual([]);
    expect(parsed.affinity?.contentExcludedNodes).toBe(1);
    expect(JSON.stringify(current.edges)).toBe(before);
    payload.coverage.validation_rejected_nodes = 1;
    expect(parseLayoutAffinity(payload, current.nodes).reason).toBe('affinity_invalid_coverage');
  });

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

  it('exposes measured round-robin statistics while preserving factual edges and activity', () => {
    const current = graph(), facts = JSON.stringify(current.edges), signature = knowledgeSignature(current);
    const parsed = parseLayoutAffinity(sampledPayload(), current.nodes, ['one']);
    expect(parsed.reason).toBeNull();
    expect(parsed.affinity).toMatchObject({
      state: 'bounded_ready', pairSampling: 'project-round-robin-v1', vectorNodesCompared: 2,
      eligibleVectorNodes: 2, comparisonMin: 1, comparisonMax: 1, nodePairCoverage: 1, pairCoverage: 1,
    });
    current.layoutAffinity = parsed.affinity!;
    expect(JSON.stringify(current.edges)).toBe(facts);
    expect(knowledgeSignature(current)).toBe(signature);
  });

  it('keeps historical and explicitly null sampling statistics unreported', () => {
    const legacy = affinityPayload();
    for (const payload of [legacy, { ...legacy, coverage: { ...legacy.coverage,
      pair_sampling: null, vector_nodes_compared: null, eligible_vector_nodes: null,
      comparison_min: null, comparison_max: null, node_pair_coverage: null } }]) {
      const parsed = parseLayoutAffinity(payload, graph().nodes);
      expect(parsed.reason).toBeNull();
      expect(parsed.affinity).toMatchObject({
        pairSampling: null, vectorNodesCompared: null, eligibleVectorNodes: null,
        comparisonMin: null, comparisonMax: null, nodePairCoverage: null, pairCoverage: null,
      });
    }
  });

  it('requires bounded_partial for truncated pairs or candidates even with every node compared', () => {
    const ids = ['a', 'b', 'c'], current = graph(ids);
    const pairsLimited = sampledPayload(ids);
    pairsLimited.state = 'bounded_partial';
    Object.assign(pairsLimited.coverage, { possible_vector_pairs: 3, eligible_same_project_pairs: 3,
      pairs_evaluated: 2, pair_budget_exhausted: true, pair_coverage: 2 / 3,
      vector_nodes_compared: 3, eligible_vector_nodes: 3, comparison_min: 1, comparison_max: 2,
      node_pair_coverage: 1 });
    const parsed = parseLayoutAffinity(pairsLimited, current.nodes);
    expect(parsed.reason).toBeNull();
    expect(parsed.affinity).toMatchObject({ state: 'bounded_partial', nodePairCoverage: 1, pairCoverage: 2 / 3 });
    pairsLimited.state = 'bounded_ready';
    expect(parseLayoutAffinity(pairsLimited, current.nodes).reason).toBe('affinity_invalid_coverage');

    const candidatesLimited = sampledPayload(ids);
    candidatesLimited.state = 'bounded_partial';
    Object.assign(candidatesLimited.coverage, { possible_vector_pairs: 3, eligible_same_project_pairs: 3,
      pairs_evaluated: 3, pair_coverage: 1, vector_nodes_compared: 3, eligible_vector_nodes: 3,
      comparison_min: 2, comparison_max: 2, candidates_truncated: 2 });
    expect(parseLayoutAffinity(candidatesLimited, current.nodes).affinity?.state).toBe('bounded_partial');
    candidatesLimited.state = 'bounded_ready';
    expect(parseLayoutAffinity(candidatesLimited, current.nodes).reason).toBe('affinity_invalid_coverage');
  });

  it('distinguishes sampled-node reach from eligible pair coverage and handles no eligible pairs', () => {
    const ids = ['a', 'b', 'c'], partial = sampledPayload(ids);
    partial.state = 'bounded_partial';
    Object.assign(partial.coverage, { possible_vector_pairs: 3, eligible_same_project_pairs: 3,
      pair_budget_exhausted: true, pair_coverage: 1 / 3,
      eligible_vector_nodes: 3, vector_nodes_compared: 2, comparison_min: 0, node_pair_coverage: 2 / 3 });
    expect(parseLayoutAffinity(partial, graph(ids).nodes).affinity).toMatchObject({
      state: 'bounded_partial', nodePairCoverage: 2 / 3, pairCoverage: 1 / 3, comparisonMin: 0,
    });
    const alone = sampledPayload(['a']);
    alone.candidates = [];
    Object.assign(alone.coverage, { pairs_evaluated: 0, possible_vector_pairs: 0,
      eligible_same_project_pairs: 0, vector_nodes_compared: 0, eligible_vector_nodes: 0,
      comparison_min: 0, comparison_max: 0, node_pair_coverage: null, pair_coverage: null,
      candidates_returned: 0 });
    expect(parseLayoutAffinity(alone, graph(['a']).nodes).affinity).toMatchObject({
      state: 'bounded_ready', vectorNodesCompared: 0, eligibleVectorNodes: 0, nodePairCoverage: null,
    });
    alone.coverage.eligible_vector_nodes = 1;
    alone.coverage.node_pair_coverage = 0;
    expect(parseLayoutAffinity(alone, graph(['a']).nodes).reason).toBe('affinity_invalid_coverage');
  });

  it('rejects malformed, partially present and contradictory sampling diagnostics', () => {
    const mutations = [
      { pair_sampling: 'row-first' }, { pair_sampling: null }, { vector_nodes_compared: 1 },
      { vector_nodes_compared: 3 }, { vector_nodes_compared: NaN }, { eligible_vector_nodes: 1 },
      { comparison_min: -1 }, { comparison_min: 2 }, { comparison_max: 0 },
      { comparison_max: null }, { node_pair_coverage: 0.5 }, { node_pair_coverage: Infinity },
      { pair_coverage: 0 }, { eligible_same_project_pairs: 0 }, { possible_vector_pairs: 0 },
      { pair_budget_exhausted: true }, { candidates_returned: 0 },
    ];
    for (const mutation of mutations) {
      const payload = sampledPayload();
      Object.assign(payload.coverage, mutation);
      expect(parseLayoutAffinity(payload, graph().nodes).reason, JSON.stringify(mutation)).toBe('affinity_invalid_coverage');
    }
    const omitted = sampledPayload();
    delete omitted.coverage.node_pair_coverage;
    expect(parseLayoutAffinity(omitted, graph().nodes).reason).toBe('affinity_invalid_coverage');
  });
});
