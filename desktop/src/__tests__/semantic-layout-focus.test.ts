// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { KnowledgeHologram } from '../knowledge/hologram';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { SemanticLayoutDriver, type LayoutRequest, type LayoutResponse, type LayoutWorkerPort } from '../knowledge/semantic-layout-driver';
import type { Point3 } from '../knowledge/plasticity';

class LayoutWorker implements LayoutWorkerPort {
  onmessage: ((event: MessageEvent<LayoutResponse>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  requests: LayoutRequest[] = [];
  terminate = vi.fn();
  postMessage(request: LayoutRequest) { this.requests.push(request); }
  flush() {
    const request = this.requests.at(-1)!, engine = new SemanticLayoutEngine();
    if (request.checkpoint) engine.restore(request.checkpoint);
    const result = engine.layout(request.view, new Map(request.previous), request.pairs, {
      changedIds: new Set(request.changed), pinnedIds: new Set(request.pinned), nowMs: request.nowMs,
    });
    const response = { id: request.id, anchors: [...result.anchors], diagnostics: result.diagnostics, checkpoint: engine.checkpoint() };
    this.onmessage?.({ data: response } as MessageEvent<LayoutResponse>);
    return response;
  }
}

it('publishes bounded anchors through Hologram focus/unfocus requests and restored worker checkpoints', () => {
  const graph = parseKnowledgeGraph({ nodes: ['pin', 'related', 'unrelated'].map(id => ({ id, label: id })),
    edges: [{ source: 'pin', target: 'related', weight: 1 }] });
  const worker = new LayoutWorker();
  const state = {
    requestedAnimation: true, disposed: false, view: { ...graph, focusId: 'pin' as string | null, hops: 0 },
    positions: new Map<string, Point3>([['pin', { x: 6, y: 0, z: 0 }]]), anchors: new Map<string, Point3>(),
    layoutChanged: new Set<string>(), provisionalOverviewPositions: new Set<string>(),
    applyCommunityLabels: vi.fn(), preparePositionMotion: vi.fn(), fitAfterSemanticLayout: false,
    loop: { start: vi.fn() }, invalidateFrame: vi.fn(),
  };
  const hologram = Object.assign(Object.create(KnowledgeHologram.prototype), state) as typeof state & {
    queueSemanticLayout(): void; applySemanticLayout(response: LayoutResponse): void; semanticLayout: SemanticLayoutDriver;
  };
  const driver = new SemanticLayoutDriver(response => hologram.applySemanticLayout(response), () => worker);
  hologram.semanticLayout = driver;
  try {
    hologram.queueSemanticLayout();
    expect(worker.requests[0].pinned).toEqual(['pin']);
    const focused = worker.flush();
    expect(hologram.anchors.get('pin')).toMatchObject({ x: 6, y: 0, z: 0 });
    expect(hologram.anchors.get('unrelated')!.x).toBeGreaterThan(2.3);
    hologram.positions = new Map(focused.anchors);
    hologram.view = { ...hologram.view, focusId: null };
    hologram.queueSemanticLayout();
    expect(worker.requests[1].pinned).toEqual([]);
    expect(worker.requests[1].checkpoint).toBeDefined();
    const released = worker.flush();
    expect(released.diagnostics.affectedNodes).toBe(2);
    const referenceEngine = new SemanticLayoutEngine(), request = worker.requests[1];
    referenceEngine.restore(request.checkpoint!);
    const reflow = referenceEngine.layout(request.view, new Map(request.previous), request.pairs, { reflow: true });
    expect([...hologram.anchors.keys()]).toEqual([...reflow.anchors.keys()]);
    expect(reflow.diagnostics.pinnedNodes).toBe(0);
    // Reflow reseeds coordinates. Compare the global-bound invariant without
    // replacing incremental cache preservation with a global force solve.
    for (const point of reflow.anchors.values())
      expect(Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1)).toBeLessThanOrEqual(1 + 1e-12);
    for (const point of hologram.anchors.values())
      expect(Math.hypot(point.x / 2.3, point.y / 1.55, point.z / 1.1)).toBeLessThanOrEqual(1 + 1e-12);
    expect(hologram.anchors.size).toBe(3);
    hologram.positions = new Map(released.anchors);
    hologram.queueSemanticLayout();
    expect(worker.requests).toHaveLength(2);
    expect(driver.settled).toBe(true);
  } finally { driver.clear(); }
});
