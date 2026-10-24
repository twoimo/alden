// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeHologram } from '../knowledge/hologram';
import { parseKnowledgeGraph } from '../knowledge/graph-model';

// Keep the real constructor/resize/overlay discovery path; no GPU claim.
vi.mock('three', async importOriginal => ({
  ...await importOriginal<typeof import('three')>(),
  WebGLRenderer: class {
    setPixelRatio() {} setClearColor() {} setSize() {} setViewport() {}
    getPixelRatio() { return 1; } dispose() {} render() {}
  },
}));

type Bounds = { left: number; top: number; right: number; bottom: number };
type OverlayGeometry = {
  labelOverlays: HTMLElement[];
  excludedLabelBounds: Bounds[];
  resize(resetCamera?: boolean): void;
};
const observed = new Set<Element>();

afterEach(() => {
  document.body.replaceChildren();
  observed.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('knowledge-map status caption exclusion', () => {
  it('reserves the actual status HUD and refreshes its measured bounds on layout changes', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    vi.stubGlobal('ResizeObserver', class {
      observe(element: Element) { observed.add(element); }
      disconnect() {} unobserve() {}
    });
    const workspace = document.createElement('section');
    workspace.className = 'knowledge-section';
    workspace.innerHTML = '<div class="knowledge-command-stage"><div class="knowledge-map-caption">ALDEN / KNOWLEDGE COMMAND <time>확인 19시 7분 51초</time></div><div class="knowledge-hologram-shell"><canvas></canvas><div class="knowledge-node-labels"></div><div id="knowledge-accessible-nodes"></div></div></div>';
    document.body.append(workspace);
    const canvas = workspace.querySelector('canvas')!;
    const shell = workspace.querySelector<HTMLElement>('.knowledge-hologram-shell')!;
    const caption = workspace.querySelector<HTMLElement>('.knowledge-map-caption')!;
    const rect = (left: number, top: number, width: number, height: number) =>
      ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON() {} });
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(rect(144, 51, 495, 360));
    vi.spyOn(shell, 'getBoundingClientRect').mockReturnValue(rect(144, 51, 495, 360));
    const captionRect = vi.spyOn(caption, 'getBoundingClientRect').mockReturnValue(rect(154, 380, 304, 13));
    const hologram = new KnowledgeHologram(canvas, parseKnowledgeGraph({ nodes: [], edges: [] }), () => {}, undefined, 'workspace', null);
    const geometry = hologram as unknown as OverlayGeometry;
    try {
      expect(geometry.labelOverlays).toContain(caption);
      expect(observed).toContain(caption);
      const index = geometry.labelOverlays.indexOf(caption);
      expect(geometry.excludedLabelBounds[index]).toEqual({ left: 4, top: 323, right: 320, bottom: 348 });
      captionRect.mockReturnValue(rect(154, 360, 360, 26));
      geometry.resize(false);
      expect(geometry.excludedLabelBounds[index]).toEqual({ left: 4, top: 303, right: 376, bottom: 341 });
      caption.hidden = true;
      geometry.resize(false);
      expect(geometry.excludedLabelBounds[index].right).toBe(geometry.excludedLabelBounds[index].left);
      caption.hidden = false;
      geometry.resize(false);
      expect(geometry.excludedLabelBounds[index]).toEqual({ left: 4, top: 303, right: 376, bottom: 341 });
    } finally { hologram.dispose(); }
  });
});
