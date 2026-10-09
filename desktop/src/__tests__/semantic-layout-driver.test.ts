import { describe, expect, it, vi } from 'vitest';
import { SemanticLayoutDriver, type LayoutRequest, type LayoutResponse, type LayoutWorkerPort } from '../knowledge/semantic-layout-driver';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { parseKnowledgeGraph, overviewGraph } from '../knowledge/graph-model';

class WorkerStub implements LayoutWorkerPort {
  onmessage: ((event: MessageEvent<LayoutResponse>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  requests: LayoutRequest[] = [];
  terminate = vi.fn();
  postMessage(request: LayoutRequest) { this.requests.push(request); }
  flush() {
    const request = this.requests.at(-1)!, engine = new SemanticLayoutEngine();
    if (request.checkpoint) engine.restore(request.checkpoint);
    const result = engine.layout(request.view, new Map(request.previous), request.pairs, { changedIds: new Set(request.changed), pinnedIds: new Set(request.pinned) });
    const response = { id: request.id, anchors: [...result.anchors], diagnostics: result.diagnostics, checkpoint: engine.checkpoint(), wallMs: 1 };
    this.onmessage?.({ data: response } as MessageEvent<LayoutResponse>);
    return response;
  }
}
const view = (ids = ['a', 'b']) => overviewGraph(parseKnowledgeGraph({ nodes: ids.map(id => ({ id, label: id })),
  edges: [{ source: 'a', target: 'b', relation: 'ref', weight: 1 }] }));

describe('owned asynchronous semantic layout', () => {
  it('returns without solving and applies only the latest job', () => {
    const workers: WorkerStub[] = [], apply = vi.fn(), driver = new SemanticLayoutDriver(apply, () => { const worker = new WorkerStub(); workers.push(worker); return worker; });
    driver.request(view(), new Map(), new Set(), new Set()); expect(apply).not.toHaveBeenCalled();
    driver.request(view(['a', 'b', 'new']), new Map(), new Set(), new Set());
    expect(workers[0].terminate).toHaveBeenCalledTimes(1);
    workers[0].flush(); expect(apply).not.toHaveBeenCalled();
    workers[1].flush(); expect(apply).toHaveBeenCalledTimes(1); expect(driver.diagnostics.state).toBe('ready');
    driver.clear(); expect(workers[1].terminate).toHaveBeenCalledTimes(1);
  });

  it('terminates hidden work and restores the last completed cache while preserving an unrelated component', () => {
    const workers: WorkerStub[] = [], apply = vi.fn(), driver = new SemanticLayoutDriver(apply, () => { const worker = new WorkerStub(); workers.push(worker); return worker; });
    driver.request(view(), new Map(), new Set(), new Set()); const first = workers[0].flush();
    const positions = new Map(first.anchors);
    driver.request(view(['a', 'b', 'cancelled']), positions, new Set(), new Set()); driver.stop();
    workers[0].flush(); expect(apply).toHaveBeenCalledTimes(1);
    driver.request(view(['a', 'b', 'new']), positions, new Set(), new Set()); const result = workers[1].flush();
    expect(result.anchors.find(([id]) => id === 'a')![1]).toEqual(positions.get('a'));
    expect(result.anchors.find(([id]) => id === 'b')![1]).toEqual(positions.get('b'));
    expect(workers[1].requests[0].checkpoint).toBeDefined(); driver.clear();
  });

  it('drops every prior-scope cache on clear and reuses an already applied snapshot without new work', () => {
    const workers: WorkerStub[] = [], driver = new SemanticLayoutDriver(vi.fn(), () => { const worker = new WorkerStub(); workers.push(worker); return worker; });
    driver.request(view(), new Map(), new Set(), new Set()); workers[0].flush();
    driver.request(view(), new Map(), new Set(), new Set()); expect(workers[0].requests).toHaveLength(1);
    driver.clear(); driver.request(view(), new Map(), new Set(), new Set());
    expect(workers[1].requests[0].checkpoint).toBeUndefined(); driver.clear();
  });
});
