const hash = 'a'.repeat(64);
const profileHash = '9657dd14d382be6d66db63edf3e8d328011c623d77829b03fb4d01dfeabf51f3';
export function affinityPayload(ids = ['a', 'b'], project = 'one', version = 'v1', target = 'target-a') {
  const input_nodes = ids.map(id => ({ id, source_version: version, source_target: target }));
  return { schema: 'alden-layout-affinity-v1', state: 'bounded_ready', input_nodes, input_sha256: hash,
    input_projects: [project], profile_sha256: profileHash,
    profile: { model: 'mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0',
      endpoint: '5956b9388ba26ec18062d7a95c02b4d02eb01a467e383f7ddd505ecba2508844', encoding: 'e5-char256-stride192-weighted-unit-pool-v1', dimension: 384 },
    nodes: input_nodes.map(node => ({ ...node, projects: [project], permissions: [{ project, permission: 'local-private' }],
      profile_sha256: profileHash, dimension: 384, text_sha256: hash, raw_sha256: hash, raw_path: hash + '.json', vector_sha256: hash,
      projection_sha256: hash, base_text_sha256: hash, vector_norm: 1, body_source: 'stored_body' })),
    candidates: ids.length >= 2 ? [{ kind: 'semantic_layout_affinity', source: ids[0], target: ids[1], cosine: .9, projects: [project] }] : [],
    coverage: { requested_nodes: ids.length, usable_vector_nodes: ids.length, pairs_evaluated: ids.length >= 2 ? 1 : 0, candidates_truncated: 0 } };
}
