import { KnowledgeHologram } from '../knowledge/hologram';
import { parseKnowledgeGraph } from '../knowledge/graph-model';

/** Actual refresh method with a synthetic DOM; no WebGL or account access. */
export function labelRefreshWorkload(count: number) {
  let sourceIdReads = 0;
  const nodes = parseKnowledgeGraph({ nodes: Array.from({ length: count }, (_, index) => ({
    id: `n${index}`, label: `저장 자료 ${index}`, category: 'topic',
  })), edges: [] }).nodes;
  for (const node of nodes) {
    const id = node.id;
    Object.defineProperty(node, 'id', { get: () => { sourceIdReads++; return id; } });
  }
  const ids = Array.from({ length: Math.min(24, count) }, (_, index) => `n${count - 1 - index}`);
  const shell = document.createElement('div');
  const canvas = document.createElement('canvas');
  const layer = document.createElement('div');
  layer.className = 'knowledge-node-labels'; shell.append(canvas, layer);
  const graph = Object.create(KnowledgeHologram.prototype);
  Object.assign(graph, {
    canvas, view: { nodes, hops: 0, focusId: null }, hoveredId: null,
    labels: new Map(), labelBounds: [], globalLabels: new Set(ids),
    globalLabelRanks: new Map(ids.map((id, index) => [id, index])),
  });
  return () => {
    graph.labels.clear(); graph.labelBounds.length = 0; layer.replaceChildren();
    sourceIdReads = 0;
    graph.refreshOverviewLabels();
    return { sourceIdReads, labels: [...graph.labels].map(([id, label]: [string, HTMLSpanElement]) =>
      ({ id, text: label.textContent, title: label.title, role: label.dataset.captionRole, hidden: label.hidden })) };
  };
}
