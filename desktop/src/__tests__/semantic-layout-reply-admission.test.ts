import { expect, it, vi } from 'vitest';
import { SemanticLayoutDriver, type LayoutRequest, type LayoutResponse, type LayoutWorkerPort } from '../knowledge/semantic-layout-driver';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { overviewGraph, parseKnowledgeGraph } from '../knowledge/graph-model';

class Port implements LayoutWorkerPort {
  onmessage: LayoutWorkerPort['onmessage'] = null;
  onerror: LayoutWorkerPort['onerror'] = null;
  requests: LayoutRequest[] = [];
  terminate = vi.fn();
  postMessage(request: LayoutRequest) { this.requests.push(request); }
  reply() {
    const request = this.requests.at(-1)!, engine = new SemanticLayoutEngine();
    if (request.checkpoint) engine.restore(request.checkpoint);
    const solved = engine.layout(request.view, new Map(request.previous), request.pairs, { nowMs: request.nowMs });
    return structuredClone({ id: request.id, anchors: [...solved.anchors], diagnostics: solved.diagnostics,
      checkpoint: engine.checkpoint(), wallMs: 1 });
  }
  emit(value: unknown) { this.onmessage?.({ data: value } as MessageEvent<LayoutResponse>); }
}
const source = () => overviewGraph(parseKnowledgeGraph({ nodes: [
  { id: 'a', label: '저장된 첫 주제', category: 'topic' }, { id: 'b', label: '저장된 둘째 주제', category: 'topic' },
], edges: [{ source: 'a', target: 'b', relation: 'ref' }] }));

const mutations = {
  nanAnchor: (r: ReturnType<Port['reply']>) => { r.anchors[0][1].x = NaN; },
  duplicateAnchor: (r: ReturnType<Port['reply']>) => { r.anchors[1][0] = r.anchors[0][0]; },
  missingAnchor: (r: ReturnType<Port['reply']>) => { r.anchors.pop(); },
  foreignAnchor: (r: ReturnType<Port['reply']>) => { r.anchors[0][0] = 'foreign'; },
  nanCheckpoint: (r: ReturnType<Port['reply']>) => { r.checkpoint.positions[0][1].x = NaN; },
  inconsistentCheckpoint: (r: ReturnType<Port['reply']>) => { r.checkpoint.positions[0][1].x += .1; },
  duplicateLabels: (r: ReturnType<Port['reply']>) => { r.checkpoint.labels[1][0] = r.checkpoint.labels[0][0]; },
  missingLabels: (r: ReturnType<Port['reply']>) => { r.checkpoint.labels.pop(); },
  orphanCenter: (r: ReturnType<Port['reply']>) => { r.checkpoint.centers.push(['orphan', { x: 0, y: 0, z: 0 }]); },
  foreignPin: (r: ReturnType<Port['reply']>) => { r.checkpoint.pins.push('foreign'); },
};

it.each(Object.entries(mutations))('rejects %s before publishing a completed cache', (_name, mutate) => {
  const port = new Port(), apply = vi.fn(), driver = new SemanticLayoutDriver(apply, () => port), view = source();
  driver.request(view, new Map(), new Set(), new Set());
  const reply = port.reply(); mutate(reply);
  expect(() => port.emit(reply)).not.toThrow();
  expect(apply).not.toHaveBeenCalled();
  expect(driver.ready).toBe(false);
  expect(driver.labelsFor(view, new Set())).toBeUndefined();
  expect(driver.diagnostics.state).toBe('failed');
  driver.clear();
});

it('rejects an empty current-worker payload without throwing from the message handler', () => {
  const port = new Port(), driver = new SemanticLayoutDriver(vi.fn(), () => port);
  driver.request(source(), new Map(), new Set(), new Set());
  expect(() => port.emit(null)).not.toThrow();
  expect(driver.ready).toBe(false); expect(driver.diagnostics.state).toBe('failed'); driver.clear();
});

it('owns published checkpoint and returned labels independently from callback and message objects', () => {
  const port = new Port(), apply = vi.fn(), driver = new SemanticLayoutDriver(apply, () => port), view = source();
  driver.request(view, new Map(), new Set(), new Set()); const reply = port.reply(); port.emit(reply);
  const expected = structuredClone(reply.checkpoint.labels);
  reply.checkpoint.labels[0][1] = 'message mutation';
  (apply.mock.calls[0][0] as LayoutResponse).checkpoint!.labels[0][1] = 'callback mutation';
  expect(driver.labelsFor(view, new Set())).toEqual(expected);
  const returned = driver.labelsFor(view, new Set())!; returned[0][1] = 'reader mutation';
  expect(driver.labelsFor(view, new Set())).toEqual(expected); driver.clear();
});
