import { SemanticLayoutEngine } from './semantic-layout';
import type { LayoutRequest } from './semantic-layout-driver';

const engine = new SemanticLayoutEngine();
const scope = self as unknown as { onmessage: ((event: MessageEvent<LayoutRequest>) => void) | null; postMessage(value: unknown): void };
scope.onmessage = event => {
  const request = event.data;
  try {
    if (!Number.isFinite(request.nowMs)) throw new Error('semantic_layout_clock_invalid');
    if (request.checkpoint) engine.restore(request.checkpoint);
    const started = performance.now();
    const result = engine.layout(request.view, new Map(request.previous), request.pairs,
      { changedIds: new Set(request.changed), pinnedIds: new Set(request.pinned), nowMs: request.nowMs, reflow: request.reflow === true });
    scope.postMessage({ id: request.id, anchors: [...result.anchors], diagnostics: result.diagnostics,
      checkpoint: engine.checkpoint(), wallMs: performance.now() - started });
  } catch { scope.postMessage({ id: request.id, error: 'semantic_layout_failed' }); }
};
