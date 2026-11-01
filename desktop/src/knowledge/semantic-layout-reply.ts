import type { KnowledgeView } from './graph-model';
import type { Point3 } from './plasticity';
import { SEMANTIC_LAYOUT_LIMITS, type SemanticLayoutCheckpoint, type SemanticLayoutDiagnostics } from './semantic-layout';

interface AdmittedReply { id: number; anchors: Array<[string, Point3]>;
  checkpoint: SemanticLayoutCheckpoint; diagnostics: SemanticLayoutDiagnostics; wallMs?: number }

const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const point = (v: unknown): v is Point3 => record(v) && [v.x, v.y, v.z].every(n => typeof n === 'number' && Number.isFinite(n));
const copyPoint = (p: Point3): Point3 => ({ x: p.x, y: p.y, z: p.z });
const samePoint = (a: Point3, b: Point3): boolean => a.x === b.x && a.y === b.y && a.z === b.z;

export function copySemanticCheckpoint(c: SemanticLayoutCheckpoint): SemanticLayoutCheckpoint {
  return { positions: c.positions.map(([id, p]) => [id, copyPoint(p)]),
    labels: c.labels.map(([id, label]) => [id, label]), centers: c.centers.map(([id, p]) => [id, copyPoint(p)]),
    pairs: c.pairs.map(([id, p]) => [id, { ...p }]), pins: [...c.pins] };
}

function points(value: unknown, cap: number): Map<string, Point3> | null {
  if (!Array.isArray(value) || value.length > cap) return null;
  const result = new Map<string, Point3>();
  for (const row of value) {
    if (!Array.isArray(row) || row.length !== 2 || typeof row[0] !== 'string' || !row[0]
      || result.has(row[0]) || !point(row[1])) return null;
    result.set(row[0], copyPoint(row[1]));
  }
  return result;
}

/** Admit bounded geometry and coherent checkpoint membership before publication.
 * This validates transport/state invariants, not solver optimality or truth.
 * Every retained row is owned; no incoming array/object becomes the cache. */
export function admitSemanticReply(value: unknown, view: KnowledgeView, requestedPins: ReadonlySet<string>,
  previous: ReadonlyMap<string, Point3>): AdmittedReply | null {
  try {
    if (!record(value) || !Number.isSafeInteger(value.id) || !record(value.checkpoint) || !record(value.diagnostics)) return null;
    if (value.wallMs !== undefined && (typeof value.wallMs !== 'number' || !Number.isFinite(value.wallMs) || value.wallMs < 0)) return null;
    const limits = SEMANTIC_LAYOUT_LIMITS, checkpoint = value.checkpoint;
    const eligible = new Set(view.nodes.slice(0, limits.inputNodes).filter(n => n.id && !n.evidence.retracted).map(n => n.id));
    const anchors = points(value.anchors, limits.nodes), positions = points(checkpoint.positions, limits.nodes);
    if (!anchors || !positions || anchors.size !== Math.min(eligible.size, limits.nodes) || positions.size !== anchors.size) return null;
    for (const [id, p] of anchors) if (!eligible.has(id) || !positions.has(id) || !samePoint(p, positions.get(id)!)) return null;
    if (!Array.isArray(checkpoint.labels) || checkpoint.labels.length !== anchors.size) return null;
    const labels = new Map<string, string>();
    for (const row of checkpoint.labels) {
      if (!Array.isArray(row) || row.length !== 2 || typeof row[0] !== 'string' || !anchors.has(row[0])
        || labels.has(row[0]) || typeof row[1] !== 'string' || !row[1] || row[1].length > 4096) return null;
      labels.set(row[0], row[1]);
    }
    const centers = points(checkpoint.centers, limits.nodes), clusters = new Set(labels.values());
    if (!centers || centers.size !== clusters.size || [...centers.keys()].some(id => !clusters.has(id))) return null;
    if (!Array.isArray(checkpoint.pins) || checkpoint.pins.length > limits.nodes) return null;
    const pins = new Set<string>();
    for (const id of checkpoint.pins) {
      if (typeof id !== 'string' || pins.has(id) || !anchors.has(id) || !requestedPins.has(id)) return null;
      pins.add(id);
    }
    for (const [id, p] of anchors) if (requestedPins.has(id)) {
      if (!pins.has(id) || !point(previous.get(id)) || !samePoint(p, previous.get(id)!)) return null;
    }
    if (!Array.isArray(checkpoint.pairs) || checkpoint.pairs.length > limits.relationPairs + limits.affinityPairs) return null;
    const pairs: SemanticLayoutCheckpoint['pairs'] = [], keys = new Set<string>();
    for (const row of checkpoint.pairs) {
      if (!Array.isArray(row) || row.length !== 2 || typeof row[0] !== 'string' || keys.has(row[0]) || !record(row[1])) return null;
      const p = row[1];
      if (typeof p.source !== 'string' || typeof p.target !== 'string' || p.source === p.target
        || !anchors.has(p.source) || !anchors.has(p.target) || row[0] !== p.key
        || row[0] !== JSON.stringify([p.source, p.target].sort())
        || ![p.r, p.s, p.weight, p.rest].every(n => typeof n === 'number' && Number.isFinite(n))) return null;
      const r = p.r as number, s = p.s as number, weight = p.weight as number, rest = p.rest as number;
      if (r < 0 || s < 0 || weight <= 0 || weight !== r + s) return null;
      keys.add(row[0]); pairs.push([row[0], { key: row[0], source: p.source, target: p.target, r, s, weight, rest }]);
    }
    const d = value.diagnostics;
    for (const [key, n] of Object.entries({ nodes: anchors.size, cachedNodes: positions.size, clusters: centers.size,
      cachedClusters: centers.size, cachedPairs: pairs.length, pinnedNodes: pins.size })) if (d[key] !== n) return null;
    // Producer metrics are scalar snapshots. Their numerical/semantic accuracy
    // remains a separate claim; host lifecycle fields are always set last.
    let fields=0;
    for(const key in d){
      const n=d[key];
      if(!Object.hasOwn(d,key)||++fields>128||(n!==null&&typeof n!=='number'&&typeof n!=='boolean'))return null;
    }
    return { id: value.id as number, anchors: [...anchors], diagnostics: { ...d } as unknown as SemanticLayoutDiagnostics,
      checkpoint: { positions: [...positions], labels: [...labels], centers: [...centers], pairs, pins: [...pins] },
      wallMs: value.wallMs as number | undefined };
  } catch { return null; }
}
