import type { KnowledgeNode } from './graph-model';

export interface LayoutAffinityPair { source: string; target: string; cosine: number }
export interface LayoutAffinity {
  state: 'bounded_ready' | 'bounded_partial';
  inputKey: string;
  profileKey: string;
  inputNodes: Array<{ id: string; sourceVersion: string; sourceTarget: string }>;
  pairs: LayoutAffinityPair[];
  requestedNodes: number;
  usableNodes: number;
  evaluatedPairs: number;
  truncatedCandidates: number;
}
const MODEL = 'mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0';
const ENDPOINT = '5956b9388ba26ec18062d7a95c02b4d02eb01a467e383f7ddd505ecba2508844';
const ENCODING = 'e5-char256-stride192-weighted-unit-pool-v1';
const PROFILE = '9657dd14d382be6d66db63edf3e8d328011c623d77829b03fb4d01dfeabf51f3';
const digest = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const object = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const count = (value: unknown, cap: number): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= cap;
const ref = (value: unknown): { id: string; sourceVersion: string; sourceTarget: string } | null => {
  const row = object(value);
  if (!row || !['id', 'source_version', 'source_target'].every(key => typeof row[key] === 'string' && (row[key] as string).length > 0 && (row[key] as string).length <= 256)) return null;
  return { id: row.id as string, sourceVersion: row.source_version as string, sourceTarget: row.source_target as string };
};

/** The collection snapshot is authoritative; a memory overlay never gains
 * collection authority from a coincident name, ID or inherited metadata. */
export function affinityInput(nodes: readonly KnowledgeNode[]) {
  return nodes.filter(node => !node.id.startsWith('memory:') && node.sourceVersion && node.sourceTarget && !node.evidence.retracted)
    .map(node => ({ id: node.id, sourceVersion: node.sourceVersion!, sourceTarget: node.sourceTarget! }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

export function affinityMatches(affinity: LayoutAffinity, nodes: readonly KnowledgeNode[]): boolean {
  return JSON.stringify(affinity.inputNodes) === JSON.stringify(affinityInput(nodes));
}

/** Accept a separate layout channel, never fabricate KnowledgeEdge records.
 * Malformed, stale and incompatible replies fail as a whole before geometry. */
export function parseLayoutAffinity(raw: unknown, nodes: readonly KnowledgeNode[], requestedProjects?: readonly string[]): { affinity: LayoutAffinity | null; reason: string | null } {
  const fail = (reason: string) => ({ affinity: null, reason });
  const value = object(raw);
  if (!value || value.schema !== 'alden-layout-affinity-v1') return fail('affinity_invalid_contract');
  if (value.state === 'unavailable') {
    const reason = typeof value.reason === 'string' && /^[a-z0-9_:.-]{1,96}$/i.test(value.reason) ? value.reason : 'affinity_unavailable';
    return fail(reason);
  }
  if (value.state !== 'bounded_ready' && value.state !== 'bounded_partial') return fail('affinity_invalid_state');
  const profile = object(value.profile);
  if (!profile || profile.model !== MODEL || profile.endpoint !== ENDPOINT || profile.encoding !== ENCODING || profile.dimension !== 384 || value.profile_sha256 !== PROFILE) return fail('affinity_incompatible_profile');
  const inputHash = value.input_sha256 ?? object(value.binding)?.input_sha256;
  if (!Array.isArray(value.input_nodes) || value.input_nodes.length > 2048 || !digest(inputHash)) return fail('affinity_invalid_input');
  const inputNodes = value.input_nodes.map(ref);
  if (inputNodes.some(node => !node) || new Set(inputNodes.map(node => node!.id)).size !== inputNodes.length) return fail('affinity_invalid_input');
  const references = inputNodes as NonNullable<ReturnType<typeof ref>>[];
  references.sort((a, b) => a.id.localeCompare(b.id));
  if (JSON.stringify(references) !== JSON.stringify(affinityInput(nodes))) return fail('affinity_stale_input');
  const projects = value.input_projects;
  if (!Array.isArray(projects) || projects.length > 16 || !projects.length || projects.some(p => typeof p !== 'string' || p.length < 1 || p.length > 128)) return fail('affinity_invalid_scope');
  if (requestedProjects && projects.some(p => !requestedProjects.includes(p))) return fail('affinity_invalid_scope');
  const allowedProjects = new Set(projects as string[]);
  if (!Array.isArray(value.nodes) || value.nodes.length > references.length || !Array.isArray(value.candidates) || value.candidates.length > 4096) return fail('affinity_invalid_proofs');
  const byId = new Map(references.map(node => [node.id, node]));
  const proofs = new Map<string, Set<string>>();
  for (const rawProof of value.nodes) {
    const row = object(rawProof), node = ref(rawProof), expected = node && byId.get(node.id);
    if (!row || !node || !expected || proofs.has(node.id) || node.sourceVersion !== expected.sourceVersion || node.sourceTarget !== expected.sourceTarget
      || row.dimension !== 384 || row.profile_sha256 !== PROFILE || !digest(row.text_sha256) || !digest(row.raw_sha256) || !digest(row.vector_sha256)
      || row.raw_path !== row.raw_sha256 + '.json' || !digest(row.projection_sha256) || !digest(row.base_text_sha256)
      || typeof row.vector_norm !== 'number' || !Number.isFinite(row.vector_norm) || Math.abs(row.vector_norm - 1) > .001
      || !['stored_body', 'retained_record.localOriginalText'].includes(String(row.body_source))
      || !Array.isArray(row.projects) || !row.projects.length || row.projects.some(p => typeof p !== 'string' || !allowedProjects.has(p))) return fail('affinity_invalid_proofs');
    const proofProjects = new Set(row.projects as string[]);
    if (!Array.isArray(row.permissions) || row.permissions.length !== row.projects.length || row.permissions.some(p => {
      const permission = object(p);
      return !permission || typeof permission.project !== 'string' || !proofProjects.has(permission.project) || typeof permission.permission !== 'string' || !permission.permission || permission.permission === 'denied';
    }) || new Set(row.permissions.map(p => object(p)!.project)).size !== row.projects.length) return fail('affinity_invalid_proofs');
    proofs.set(node.id, proofProjects);
  }
  const pairs = new Map<string, LayoutAffinityPair>();
  for (const rawPair of value.candidates) {
    const row = object(rawPair);
    if (!row || (row.kind !== undefined && row.kind !== 'semantic_layout_affinity') || typeof row.source !== 'string' || typeof row.target !== 'string'
      || row.source === row.target || !proofs.has(row.source) || !proofs.has(row.target) || typeof row.cosine !== 'number' || !Number.isFinite(row.cosine) || row.cosine < -1 || row.cosine > 1
      || !Array.isArray(row.projects) || !row.projects.length || row.projects.some(p => typeof p !== 'string' || !proofs.get(row.source as string)!.has(p) || !proofs.get(row.target as string)!.has(p))) return fail('affinity_invalid_candidates');
    const [source, target] = [row.source, row.target].sort();
    const key = JSON.stringify([source, target]);
    if (!pairs.has(key) || pairs.get(key)!.cosine < row.cosine) pairs.set(key, { source, target, cosine: row.cosine });
  }
  const coverage = object(value.coverage);
  if (!coverage || coverage.requested_nodes !== references.length || coverage.usable_vector_nodes !== proofs.size
    || !count(coverage.pairs_evaluated, 131072) || !count(coverage.candidates_truncated, 16384)) return fail('affinity_invalid_coverage');
  return { affinity: { state: value.state, inputKey: inputHash, profileKey: PROFILE, inputNodes: references,
    pairs: [...pairs.values()].sort((a, b) => a.source.localeCompare(b.source) || a.target.localeCompare(b.target)),
    requestedNodes: references.length, usableNodes: proofs.size, evaluatedPairs: coverage.pairs_evaluated, truncatedCandidates: coverage.candidates_truncated }, reason: null };
}
