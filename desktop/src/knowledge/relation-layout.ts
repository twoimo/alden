import { ON_SCREEN_NODE_CAP, type KnowledgeView } from './graph-model';
import { connectedComponents, selectSynapses, synapseRestLength, type Point3 } from './plasticity';

function seedPoint(id: string): Point3 {
  let seed = 2166136261;
  for (let i = 0; i < id.length; i++) seed = Math.imul(seed ^ id.charCodeAt(i), 16777619);
  seed = Math.imul(seed ^ (seed >>> 16), 0x7feb352d);
  seed = Math.imul(seed ^ (seed >>> 15), 0x846ca68b);
  seed ^= seed >>> 16;
  const y = ((seed >>> 0) + .5) / 4294967296 * 2 - 1;
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  const phi = (Math.imul(seed ^ 0x9e3779b9, 1664525) >>> 0) / 4294967296 * Math.PI * 2;
  return { x: Math.cos(phi) * ring * 1.3, y: y * 1.05, z: Math.sin(phi) * ring * .65 };
}

/** A bounded projection of real references, not an OSK cluster writer.
 * Entity labels/types have no spatial authority. Source navigation is a weak
 * spring; real ERE links determine proximity. Only the current 24-node view is
 * solved, once on graph/navigation changes, never on each animation frame.
 */
export function relationAnchors(view: KnowledgeView): Map<string, Point3> {
  const nodes = [...view.nodes].filter(n => !n.evidence.retracted)
    .sort((a, b) => a.id.localeCompare(b.id)).slice(0, ON_SCREEN_NODE_CAP);
  const points = nodes.map(n => seedPoint(n.id));
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const edges = selectSynapses(view.edges, new Set(index.keys()));
  const components = connectedComponents(nodes.map(n => n.id), edges);
  const members = new Map<number, string[]>();
  nodes.forEach((n, i) => { const ids = members.get(components[i]) ?? []; ids.push(n.id); members.set(components[i], ids); });
  const centers = new Map([...members].map(([group, ids]) => [group, seedPoint(ids.join('\0'))]));
  for (let i = 0; i < points.length; i++) {
    const p = points[i], center = centers.get(components[i])!;
    p.x = center.x + p.x * .32; p.y = center.y + p.y * .32; p.z = center.z + p.z * .32;
  }
  const initial = points.map(p => ({ ...p }));
  const links = edges.map(e => ({ a: index.get(e.source)!, b: index.get(e.target)!, strength: e.strength }));
  const forces = new Float64Array(nodes.length * 3);
  for (let step = 0; step < 96; step++) {
    forces.fill(0);
    for (let a = 0; a < nodes.length; a++) {
      const p = points[a], origin = initial[a];
      forces[a * 3] += .06 * (origin.x - p.x);
      forces[a * 3 + 1] += .06 * (origin.y - p.y);
      forces[a * 3 + 2] += .06 * (origin.z - p.z);
      for (let b = a + 1; b < nodes.length; b++) {
        if (components[a] !== components[b]) continue;
        const q = points[b];
        const dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
        const gain = .016 / ((dx * dx + dy * dy + dz * dz + .04) ** 1.5);
        forces[a * 3] -= gain * dx; forces[a * 3 + 1] -= gain * dy; forces[a * 3 + 2] -= gain * dz;
        forces[b * 3] += gain * dx; forces[b * 3 + 1] += gain * dy; forces[b * 3 + 2] += gain * dz;
      }
    }
    for (const { a, b, strength } of links) {
      const p = points[a], q = points[b];
      const dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
      const distance = Math.max(.001, Math.hypot(dx, dy, dz));
      const gain = strength * (distance - synapseRestLength(strength)) / distance;
      forces[a * 3] += gain * dx; forces[a * 3 + 1] += gain * dy; forces[a * 3 + 2] += gain * dz;
      forces[b * 3] -= gain * dx; forces[b * 3 + 1] -= gain * dy; forces[b * 3 + 2] -= gain * dz;
    }
    for (let i = 0; i < points.length; i++) {
      const p = points[i], at = i * 3;
      const gain = .045 / Math.max(1, Math.hypot(forces[at], forces[at + 1], forces[at + 2]));
      p.x += gain * forces[at]; p.y += gain * forces[at + 1]; p.z += gain * forces[at + 2];
      const envelope = Math.max(1, Math.hypot(p.x / 2.15, p.y / 1.4, p.z / 1));
      p.x /= envelope; p.y /= envelope; p.z /= envelope;
    }
  }
  return new Map(nodes.map((node, i) => [node.id, points[i]]));
}

export function relationCenterId(view: KnowledgeView): string {
  if (view.focusId) return view.focusId;
  const scores = new Map(view.nodes.map(n => [n.id, n.importance / 100]));
  for (const edge of selectSynapses(view.edges.filter(e => e.purpose !== 'navigation'), new Set(scores.keys()))) {
    scores.set(edge.source, (scores.get(edge.source) ?? 0) + edge.strength);
    scores.set(edge.target, (scores.get(edge.target) ?? 0) + edge.strength);
  }
  return [...scores].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? '';
}
