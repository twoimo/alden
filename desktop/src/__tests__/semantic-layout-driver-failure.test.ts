import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SemanticLayoutDriver, type LayoutRequest, type LayoutResponse, type LayoutWorkerPort } from '../knowledge/semantic-layout-driver';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { overviewGraph, parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import type { Point3 } from '../knowledge/plasticity';

const NOW = 1_790_000_000_000;
const PRIVATE_ERROR = 'private-worker-url /private/source/account-token';
const stages = ['create', 'post', 'runtime'] as const;
type Stage = typeof stages[number];
type SavedMessage = NonNullable<LayoutWorkerPort['onmessage']>;
type SavedError = NonNullable<LayoutWorkerPort['onerror']>;
type Delivery = { request: LayoutRequest; message: SavedMessage; error: SavedError };

// Only the transport is controlled. Every successful delivery is computed by
// the production engine, with the same structured-clone boundary as a Worker.
class Transport implements LayoutWorkerPort {
  onmessage: LayoutWorkerPort['onmessage'] = null;
  onerror: LayoutWorkerPort['onerror'] = null;
  readonly deliveries: Delivery[] = [];
  readonly engine = new SemanticLayoutEngine();
  postFailure: unknown;
  terminateHook?: () => void;
  terminate = vi.fn(() => this.terminateHook?.());
  postMessage(request: LayoutRequest) {
    this.deliveries.push({ request: structuredClone(request), message: this.onmessage!, error: this.onerror! });
    if (this.postFailure) throw this.postFailure;
  }
  compute(index = this.deliveries.length - 1): LayoutResponse {
    const { request } = this.deliveries[index];
    if (request.checkpoint) this.engine.restore(request.checkpoint);
    const result = this.engine.layout(request.view, new Map(request.previous), request.pairs, {
      changedIds: new Set(request.changed), pinnedIds: new Set(request.pinned), nowMs: request.nowMs, reflow: request.reflow === true,
    });
    return structuredClone({ id: request.id, anchors: [...result.anchors], diagnostics: result.diagnostics,
      checkpoint: this.engine.checkpoint(), wallMs: 1 });
  }
  deliver(index = this.deliveries.length - 1) {
    const response = this.compute(index);
    this.deliveries[index].message({ data: response } as MessageEvent<LayoutResponse>);
    return response;
  }
  fail(index = this.deliveries.length - 1) {
    this.deliveries[index].error(Object.assign(new Event('error'), { message: PRIVATE_ERROR }));
  }
}

function graph(ids = ['a', 'b', 'c', 'd']): KnowledgeView {
  return overviewGraph(parseKnowledgeGraph({
    nodes: ids.map((id, index) => ({ id, label: `Evidence ${id}`, importance: .3 + index / 20,
      source_version: 'fixture-v1', source_target: 'fixture-target', description: 'not dispatched', facts: ['private fact'] })),
    edges: [{ source: 'a', target: 'b', relation: 'reference', weight: .8 },
      { source: 'b', target: 'c', relation: 'supports', weight: .5 }],
  }, NOW), 2048, 0, NOW);
}

function affinity(view = graph()): KnowledgeView {
  return { ...view, layoutAffinity: { state: 'bounded_ready', inputKey: 'a'.repeat(64), profileKey: 'b'.repeat(64),
    inputNodes: view.nodes.map(node => ({ id: node.id, sourceVersion: 'fixture-v1', sourceTarget: 'fixture-target' })),
    pairs: [{ source: 'a', target: 'c', cosine: .9 }], requestedNodes: view.nodes.length, usableNodes: view.nodes.length,
    contentExcludedNodes: 0, validationRejectedNodes: 0, evaluatedPairs: 1, truncatedCandidates: 0 } };
}

function rig() {
  const workers: Transport[] = [], plans: Array<{ create?: unknown; post?: unknown }> = [];
  const apply = vi.fn(), create = vi.fn(() => {
    const plan = plans.shift();
    if (plan?.create) throw plan.create;
    const worker = new Transport(); worker.postFailure = plan?.post; workers.push(worker); return worker;
  });
  const driver = new SemanticLayoutDriver(apply, create);
  return { workers, plans, apply, create, driver };
}
type Rig = ReturnType<typeof rig>;

function planFailure(r: Rig, stage: Stage) {
  r.plans.push(stage === 'create' ? { create: new DOMException(PRIVATE_ERROR, 'SecurityError') }
    : stage === 'post' ? { post: new DOMException(PRIVATE_ERROR, 'DataCloneError') } : {});
}

function request(r: Rig, view = graph(), previous = new Map<string, Point3>(), changed = new Set<string>(),
  pins = new Set<string>(), provisional = new Set<string>(), reflow = false) {
  expect(() => r.driver.request(view, previous, changed, pins, provisional, reflow)).not.toThrow();
}

function failed(r: Rig, stage: Stage) {
  expect(r.driver.diagnostics).toMatchObject({ state: 'failed', reason: 'semantic_layout_worker_failed', failureStage: stage });
  expect(r.driver.settled).toBe(false);
  expect(JSON.stringify(r.driver.diagnostics)).not.toContain(PRIVATE_ERROR);
  expect(JSON.stringify(r.driver.diagnostics)).not.toContain('SecurityError');
  expect(JSON.stringify(r.driver.diagnostics)).not.toContain('DataCloneError');
}

function retired(worker: Transport) {
  expect(worker.onmessage).toBeNull(); expect(worker.onerror).toBeNull();
  expect(worker.terminate).toHaveBeenCalledTimes(1);
}

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('semantic layout transport failure ownership', () => {
  it.each(stages)('%s failure settles safely without an implicit solve or retry', async stage => {
    const r = rig(), engine = vi.spyOn(SemanticLayoutEngine.prototype, 'layout');
    planFailure(r, stage); request(r);
    if (stage === 'runtime') r.workers[0].fail();
    failed(r, stage); expect(r.driver.ready).toBe(false); expect(r.apply).not.toHaveBeenCalled();
    if (stage !== 'create') retired(r.workers[0]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(r.create).toHaveBeenCalledTimes(1); expect(engine).not.toHaveBeenCalled();
    expect(r.workers.flatMap(w => w.deliveries)).toHaveLength(stage === 'create' ? 0 : 1);
    r.driver.clear(); if (stage !== 'create') retired(r.workers[0]);
  });

  it.each(['unsupported', 'SecurityError'] as const)('contains the actual default Worker constructor: %s', kind => {
    const apply = vi.fn(), layout = vi.spyOn(SemanticLayoutEngine.prototype, 'layout');
    const constructor = vi.fn(function () { throw new DOMException(PRIVATE_ERROR, 'SecurityError'); });
    vi.stubGlobal('Worker', kind === 'unsupported' ? undefined : constructor);
    const driver = new SemanticLayoutDriver(apply);
    expect(() => driver.request(graph(), new Map(), new Set(), new Set())).not.toThrow();
    expect(driver.diagnostics).toMatchObject({ state: 'failed', reason: 'semantic_layout_worker_failed', failureStage: 'create' });
    expect(driver.settled).toBe(false); expect(driver.ready).toBe(false);
    expect(JSON.stringify(driver.diagnostics)).not.toContain(PRIVATE_ERROR);
    expect(layout).not.toHaveBeenCalled(); expect(apply).not.toHaveBeenCalled();
    if (kind === 'SecurityError') expect(constructor).toHaveBeenCalledTimes(1);
    driver.clear();
  });

  it.each(stages)('%s failure permits one fresh solve for the identical explicit request', stage => {
    const r = rig(), source = graph(), before = structuredClone(source);
    planFailure(r, stage); request(r, source);
    if (stage === 'runtime') r.workers[0].fail();
    request(r, source);
    const current = r.workers.at(-1)!;
    expect(r.create).toHaveBeenCalledTimes(2);
    expect(current.deliveries).toHaveLength(1); expect(current.terminate).not.toHaveBeenCalled();
    expect(r.driver.diagnostics.state).toBe('pending'); expect(r.driver.settled).toBe(false);
    const result = current.deliver();
    expect(r.apply).toHaveBeenCalledExactlyOnceWith(result);
    expect(r.driver.settled).toBe(true); expect(r.driver.ready).toBe(true);
    expect(r.driver.labelsFor(source, new Set())).toEqual(result.checkpoint!.labels);
    expect(source).toEqual(before); r.driver.clear();
  });

  it.each(['post', 'runtime'] as const)('ignores a saved message immediately after %s failure, before retry', stage => {
    const r = rig(); planFailure(r, stage); request(r);
    const old = r.workers[0]; if (stage === 'runtime') old.fail();
    const failure = structuredClone(r.driver.diagnostics);
    old.deliver(); old.fail();
    expect(r.driver.diagnostics).toEqual(failure); expect(r.driver.ready).toBe(false);
    expect(r.driver.settled).toBe(false); expect(r.apply).not.toHaveBeenCalled();
    retired(old); r.driver.clear();
  });

  it.each(['post', 'runtime'] as const)('%s saved callbacks cannot apply or retire a newer pending or completed worker', stage => {
    const r = rig(); planFailure(r, stage); request(r);
    const old = r.workers[0]; if (stage === 'runtime') old.fail();
    const next = graph(['a', 'b', 'c', 'd', 'e']); request(r, next);
    const current = r.workers.at(-1)!, pending = structuredClone(r.driver.diagnostics);
    old.deliver(); old.fail();
    expect(r.driver.diagnostics).toEqual(pending); expect(current.terminate).not.toHaveBeenCalled();
    expect(r.apply).not.toHaveBeenCalled(); expect(r.create).toHaveBeenCalledTimes(2);
    const response = current.deliver(), complete = structuredClone(r.driver.diagnostics);
    old.fail(); old.deliver();
    expect(r.driver.diagnostics).toEqual(complete); expect(r.driver.settled).toBe(true);
    expect(r.apply).toHaveBeenCalledExactlyOnceWith(response); expect(current.terminate).not.toHaveBeenCalled();
    r.driver.clear(); retired(old); retired(current);
  });

  it.each(stages)('repeated %s failures require distinct explicit attempts and remain retryable', async stage => {
    const r = rig();
    for (let attempt = 1; attempt <= 3; attempt++) {
      planFailure(r, stage); request(r); if (stage === 'runtime') r.workers.at(-1)!.fail();
      failed(r, stage); await vi.advanceTimersByTimeAsync(1000);
      expect(r.create).toHaveBeenCalledTimes(attempt); expect(r.apply).not.toHaveBeenCalled();
    }
    request(r); expect(r.create).toHaveBeenCalledTimes(4); const response = r.workers.at(-1)!.deliver();
    expect(r.apply).toHaveBeenCalledExactlyOnceWith(response); expect(r.driver.settled).toBe(true);
    r.workers.slice(0, -1).forEach(retired); r.driver.clear();
  });

  it('invalidates callbacks before terminate can synchronously reenter the driver', () => {
    const r = rig(); request(r); const worker = r.workers[0];
    worker.terminateHook = () => { worker.deliver(); worker.fail(); };
    expect(() => worker.fail()).not.toThrow();
    failed(r, 'runtime'); retired(worker); expect(r.apply).not.toHaveBeenCalled();
    request(r); expect(r.create).toHaveBeenCalledTimes(2); r.workers[1].deliver();
    expect(r.driver.settled).toBe(true); r.driver.clear(); retired(worker);
  });

  it('rejects old same-worker closures when a later request owns that transport', () => {
    const r = rig(); request(r); const worker = r.workers[0]; worker.deliver();
    request(r, graph(['a', 'b', 'c', 'd', 'e']));
    expect(r.create).toHaveBeenCalledTimes(1); expect(worker.deliveries).toHaveLength(2);
    const pending = structuredClone(r.driver.diagnostics);
    worker.fail(0); worker.deliver(0);
    expect(r.driver.diagnostics).toEqual(pending); expect(worker.terminate).not.toHaveBeenCalled();
    expect(r.apply).toHaveBeenCalledTimes(1); worker.deliver(1); expect(r.apply).toHaveBeenCalledTimes(2);
    expect(r.driver.settled).toBe(true); r.driver.clear();
  });
});

describe('last valid layout checkpoint across transport failure', () => {
  it.each(stages)('%s failure retains an eligible completed cache without forcing recovery', stage => {
    const r = rig(), source = affinity(); request(r, source);
    const first = r.workers[0].deliver(), previous = new Map(first.anchors!);
    r.driver.stop(); planFailure(r, stage); request(r, source, previous, new Set(), new Set(), new Set(), true);
    const failedWorker = stage === 'create' ? undefined : r.workers.at(-1)!;
    if (stage === 'runtime') failedWorker!.fail();
    failed(r, stage); expect(r.driver.ready).toBe(true);
    expect(r.driver.labelsFor(source, new Set())).toEqual(first.checkpoint!.labels);
    const factoryCalls = r.create.mock.calls.length;
    request(r, structuredClone(source), previous);
    expect(r.driver.diagnostics.state).toBe('cached'); expect(r.driver.settled).toBe(true);
    expect(r.create).toHaveBeenCalledTimes(factoryCalls); expect(r.apply).toHaveBeenCalledTimes(1);
    if (failedWorker) { failedWorker.deliver(); failedWorker.fail(); retired(failedWorker); }
    expect(r.driver.diagnostics.state).toBe('cached'); expect(r.apply).toHaveBeenCalledTimes(1);
    r.driver.clear();
  });

  it.each(['post', 'runtime'] as const)('%s failure on a reused worker restores only the last completed checkpoint', stage => {
    const r = rig(), source = graph(); request(r, source); const old = r.workers[0], first = old.deliver();
    const completed = structuredClone(first.checkpoint), next = graph(['a', 'b', 'c', 'd', 'e']);
    const previous = new Map(first.anchors!); previous.set('a', { x: 50, y: -30, z: 25 });
    if (stage === 'post') old.postFailure = new DOMException(PRIVATE_ERROR, 'DataCloneError');
    request(r, next, previous, new Set(['e'])); if (stage === 'runtime') old.fail();
    request(r, next, previous, new Set(['e']));
    expect(r.create).toHaveBeenCalledTimes(2);
    const current = r.workers[1], dispatched = current.deliveries[0].request;
    expect(dispatched.checkpoint).toEqual(completed); expect(dispatched.previous).toEqual(first.anchors);
    const result = current.deliver(); expect(r.apply).toHaveBeenLastCalledWith(result);
    expect(result.anchors).toHaveLength(next.nodes.length); expect(r.driver.settled).toBe(true);
    expect(first.checkpoint).toEqual(completed); retired(old); r.driver.clear();
  });

  it.each(['inputKey', 'profileKey', 'state'] as const)('a failed new %s authority cannot resurrect old membership', field => {
    const r = rig(), source = affinity(); request(r, source); const first = r.workers[0].deliver();
    const next = structuredClone(source);
    Object.assign(next.layoutAffinity!, { [field]: field === 'state' ? 'unavailable' : 'c'.repeat(64) });
    request(r, next, new Map(first.anchors!)); const failureWorker = r.workers[1]; failureWorker.fail();
    failed(r, 'runtime'); expect(r.driver.ready).toBe(false);
    expect(r.driver.labelsFor(source, new Set())).toBeUndefined(); expect(r.driver.labelsFor(next, new Set())).toBeUndefined();
    request(r, source, new Map(first.anchors!));
    expect(r.create).toHaveBeenCalledTimes(3); const fresh = r.workers[2].deliveries[0].request;
    expect(fresh.checkpoint).toBeUndefined(); expect(fresh.previous).toEqual(first.anchors);
    const pending = structuredClone(r.driver.diagnostics); failureWorker.deliver(); failureWorker.fail(); r.workers[0].deliver();
    expect(r.driver.diagnostics).toEqual(pending); expect(r.apply).toHaveBeenCalledTimes(1);
    r.workers[2].deliver(); expect(r.driver.settled).toBe(true); r.driver.clear();
  });

  it.each(['stop', 'clear'] as const)('%s after failure preserves its established cache boundary and rejects retired callbacks', action => {
    const r = rig(), source = graph(); request(r, source); const worker = r.workers[0], first = worker.deliver();
    request(r, source, new Map(first.anchors!), new Set(), new Set(), new Set(), true); worker.fail();
    r.driver[action](); const state = structuredClone(r.driver.diagnostics);
    worker.deliver(); worker.fail(); expect(r.driver.diagnostics).toEqual(state); expect(r.apply).toHaveBeenCalledTimes(1);
    expect(r.driver.ready).toBe(action === 'stop'); expect(r.driver.settled).toBe(false);
    request(r, source, new Map(first.anchors!));
    if (action === 'stop') { expect(r.driver.diagnostics.state).toBe('cached'); expect(r.create).toHaveBeenCalledTimes(1); }
    else { expect(r.create).toHaveBeenCalledTimes(2); expect(r.workers[1].deliveries[0].request.checkpoint).toBeUndefined(); r.workers[1].deliver(); }
    expect(r.driver.settled).toBe(true); retired(worker); r.driver.clear();
  });

  it('a runtime error after completion keeps the valid checkpoint but requires an explicit cache request to settle', () => {
    const r = rig(), source = graph(); request(r, source); const worker = r.workers[0], first = worker.deliver();
    worker.fail(); failed(r, 'runtime'); expect(r.driver.ready).toBe(true); retired(worker);
    const failure = structuredClone(r.driver.diagnostics); worker.deliver();
    expect(r.driver.diagnostics).toEqual(failure); expect(r.apply).toHaveBeenCalledTimes(1);
    request(r, source, new Map(first.anchors!)); expect(r.create).toHaveBeenCalledTimes(1);
    expect(r.driver.settled).toBe(true); expect(r.driver.diagnostics.state).toBe('cached'); r.driver.clear();
  });
});

describe('successful layout numerical and lifecycle contracts', () => {
  it('matches direct production engine results through initial solve, explicit pin and recovered reflow', () => {
    const r = rig(), source = affinity(), original = structuredClone(source), oracle = new SemanticLayoutEngine();
    request(r, source); const first = r.workers[0].deliver();
    const expected = oracle.layout(source, new Map(), source.layoutAffinity!.pairs, { nowMs: NOW });
    expect(first.anchors).toEqual([...expected.anchors]); expect(first.diagnostics).toEqual(expected.diagnostics);
    expect(first.checkpoint).toEqual(oracle.checkpoint());
    const previous = new Map(first.anchors!), pin = { x: .6, y: -.4, z: .25 }; previous.set('a', pin);
    const previousBefore = structuredClone(previous), pins = new Set(['a']);
    request(r, source, previous, new Set(), pins); const second = r.workers[0].deliver();
    const pinned = oracle.layout(source, previous, source.layoutAffinity!.pairs, { nowMs: NOW, pinnedIds: pins });
    expect(second.anchors).toEqual([...pinned.anchors]); expect(second.diagnostics).toEqual(pinned.diagnostics);
    expect(second.checkpoint).toEqual(oracle.checkpoint()); expect(new Map(second.anchors!).get('a')).toEqual(pin);
    request(r, source, new Map(second.anchors!), new Set(), pins, new Set(), true); r.workers[0].fail();
    request(r, source, new Map(second.anchors!), new Set(), pins, new Set(), true);
    const recovered = r.workers[1].deliver();
    const reflow = oracle.layout(source, new Map(second.anchors!), source.layoutAffinity!.pairs, { nowMs: NOW, pinnedIds: pins, reflow: true });
    expect(recovered.anchors).toEqual([...reflow.anchors]); expect(recovered.diagnostics).toEqual(reflow.diagnostics);
    expect(recovered.checkpoint).toEqual(oracle.checkpoint()); expect(r.apply).toHaveBeenCalledTimes(3);
    expect(source).toEqual(original); expect(previous).toEqual(previousBefore); r.driver.clear();
  });

  it('coalesces pending work, supersedes obsolete work, and protects an explicit reflow from an ordinary redraw', () => {
    const r = rig(), source = graph(); request(r, source); request(r, structuredClone(source));
    expect(r.create).toHaveBeenCalledTimes(1); expect(r.workers[0].deliveries).toHaveLength(1);
    const next = graph(['a', 'b', 'c', 'd', 'e']); request(r, next);
    const old = r.workers[0], current = r.workers[1], pending = structuredClone(r.driver.diagnostics);
    old.deliver(); old.fail(); expect(r.driver.diagnostics).toEqual(pending); expect(r.apply).not.toHaveBeenCalled();
    retired(old); const result = current.deliver();
    request(r, next, new Map(result.anchors!), new Set(), new Set(), new Set(), true);
    request(r, next, new Map(result.anchors!));
    expect(current.deliveries).toHaveLength(2); expect(current.terminate).not.toHaveBeenCalled();
    current.deliver(); expect(r.driver.settled).toBe(true); expect(r.apply).toHaveBeenCalledTimes(2); r.driver.clear();
  });

  it.each(['stop', 'clear'] as const)('%s cancels an initial pending solve and a later explicit solve remains possible', action => {
    const r = rig(); request(r); const old = r.workers[0]; r.driver[action]();
    const state = structuredClone(r.driver.diagnostics); old.deliver(); old.fail();
    expect(r.driver.diagnostics).toEqual(state); expect(r.apply).not.toHaveBeenCalled();
    expect(r.driver.ready).toBe(false); expect(r.driver.settled).toBe(false); retired(old);
    request(r); expect(r.create).toHaveBeenCalledTimes(2); r.workers[1].deliver();
    expect(r.driver.settled).toBe(true); expect(r.apply).toHaveBeenCalledTimes(1); r.driver.clear();
  });
});
