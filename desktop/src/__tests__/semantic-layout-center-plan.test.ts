import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';

const edge = (a: number, b: number) => ({ source: `n${a}`, target: `n${b}`, weight: 1 });
function view(count: number, edges: ReturnType<typeof edge>[]): KnowledgeView {
  return { ...parseKnowledgeGraph({ nodes: Array.from({ length: count }, (_, i) => ({ id: `n${i}`, label: `Fixture ${i}` })), edges }), focusId: null, hops: 0 };
}

describe('fresh energy center plans', () => {
  it('preserves an unplaced spoke when equal opposing hub pulls cancel', () => {
    const edges = [];
    for (let group = 0; group < 9; group++) {
      const start = group * 30, end = group < 8 ? start + 30 : start + 24;
      for (let a = start; a < end; a++) for (let b = a + 1; b < end; b++) edges.push(edge(a, b));
    }
    const first = new SemanticLayoutEngine().layout(view(264, edges), new Map());
    const hubs = Array.from({ length: 8 }, (_, i) => ({ node: i * 30, label: first.clusterIds.get(`n${i * 30}`)! }))
      .sort((a, b) => a.label < b.label ? -1 : 1);
    edges.push(edge(hubs[2].node, 240), edge(hubs[6].node, 241));
    const engine = new SemanticLayoutEngine(), result = engine.layout(view(264, edges), new Map());
    expect(result.diagnostics.clusters).toBe(9);
    const spoke = new Map(engine.checkpoint().centers).get(result.clusterIds.get('n240')!)!;
    // Actual pre-optimization source207e518 result. An incomplete plan must
    // retain sequential separation, rather than silently leaving a seed here.
    expect(spoke.x).toBeCloseTo(-1.1975226817917424, 12);
    expect(spoke.y).toBeCloseTo(-0.16182464678250105, 12);
    expect(spoke.z).toBeCloseTo(0.44627807930089786, 12);
  });

  it('keeps the complete cold chain geometry and checkpoint from source207e518', () => {
    const engine = new SemanticLayoutEngine();
    const result = engine.layout(view(512, Array.from({ length: 511 }, (_, i) => edge(i, i + 1))), new Map());
    const snapshot = JSON.stringify({ anchors: [...result.anchors], clusters: [...result.clusterIds], checkpoint: engine.checkpoint() });
    expect(createHash('sha256').update(snapshot).digest('hex')).toBe('86b5ddfd24def28a6fab9875454b0063074797a16f9a5cc4cb81d8fc78f9bbe4');
  });
});
