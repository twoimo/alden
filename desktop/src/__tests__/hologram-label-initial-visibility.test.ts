// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { KnowledgeHologram } from '../knowledge/hologram';

describe('uncommitted graph label visibility', () => {
  it('keeps a newly inserted label hidden until the renderer has placed it', () => {
    const shell = document.createElement('div');
    const canvas = document.createElement('canvas');
    const layer = document.createElement('div');
    layer.className = 'knowledge-node-labels';
    shell.append(canvas, layer);
    document.body.append(shell);
    const fake = Object.create(KnowledgeHologram.prototype) as { refreshOverviewLabels(): void };
    Object.assign(fake, {
      canvas, view: { hops: 0, focusId: null, nodes: [{ id: 'synthetic-arrival', label: 'Unplaced label' }] },
      hoveredId: null, globalLabels: new Set(['synthetic-arrival']),
      globalLabelRanks: new Map([['synthetic-arrival', 0]]), labels: new Map(), labelBounds: [],
    });
    try {
      fake.refreshOverviewLabels();
      const label = layer.querySelector<HTMLSpanElement>('.knowledge-node-label')!;
      expect(label).not.toBeNull();
      expect(label.hidden).toBe(true);
      expect(label.style.transform).toBe('');
    } finally { shell.remove(); }
  });
});
