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
  it('binds cached labels to current R/S, source versions, pins and authority clear',()=>{
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),a=view();
    driver.request(a,new Map(),new Set(),new Set());const response=worker.flush();
    expect(driver.labelsFor(a,new Set())).toEqual(response.checkpoint.labels);
    expect(driver.labelsFor({...a,edges:[]},new Set())).toBeUndefined();
    expect(driver.labelsFor({...a,layoutAffinity:{pairs:[{source:'a',target:'b',cosine:.9}]} as any},new Set())).toBeUndefined();
    expect(driver.labelsFor({...a,nodes:a.nodes.map(n=>({...n,sourceVersion:'different-current-version'}))},new Set())).toBeUndefined();
    expect(driver.labelsFor(a,new Set(['a']))).toBeUndefined();driver.clear();expect(driver.labelsFor(a,new Set())).toBeUndefined();
  });
  it('never restores labels from superseded same-ID R input or a cancelled result',()=>{
    const workers:WorkerStub[]=[],apply=vi.fn(),driver=new SemanticLayoutDriver(apply,()=>{const worker=new WorkerStub();workers.push(worker);return worker;});
    const a=view(),b={...a,edges:[]};driver.request(a,new Map(),new Set(),new Set());const old=workers[0].onmessage;
    driver.request(b,new Map(),new Set(),new Set());const current=workers[1].flush();
    old?.({data:{...current,id:workers[0].requests[0].id}} as MessageEvent<LayoutResponse>);
    expect(apply).toHaveBeenCalledTimes(1);expect(driver.labelsFor(a,new Set())).toBeUndefined();
    expect(driver.labelsFor(b,new Set())).toEqual(current.checkpoint.labels);driver.clear();
    workers[1].flush();expect(apply).toHaveBeenCalledTimes(1);expect(driver.labelsFor(b,new Set())).toBeUndefined();
  });
  it('does not treat provisional ID seeds as solved cold cluster centers while retaining a fixed selection', () => {
    const worker = new WorkerStub(), driver = new SemanticLayoutDriver(vi.fn(), () => worker);
    const initial = new Map([['a', { x: 1, y: 0, z: 0 }], ['b', { x: -1, y: 0, z: 0 }]]);
    driver.request(view(), initial, new Set(), new Set(['a']));
    expect(worker.requests[0].previous).toEqual([['a', initial.get('a')!]]);
    const response = worker.flush(); expect(response.anchors.find(([id]) => id === 'a')![1]).toEqual(initial.get('a')); driver.clear();
  });
  it('returns without solving and applies only the latest job', () => {
    const workers: WorkerStub[] = [], apply = vi.fn(), driver = new SemanticLayoutDriver(apply, () => { const worker = new WorkerStub(); workers.push(worker); return worker; });
    driver.request(view(), new Map(), new Set(), new Set()); expect(apply).not.toHaveBeenCalled();
    driver.request(view(['a', 'b', 'new']), new Map(), new Set(), new Set());
    expect(workers[0].terminate).toHaveBeenCalledTimes(1);
    workers[0].flush(); expect(apply).not.toHaveBeenCalled();
    workers[1].flush(); expect(apply).toHaveBeenCalledTimes(1); expect(driver.diagnostics.state).toBe('ready');
    driver.clear(); expect(workers[1].terminate).toHaveBeenCalledTimes(1);
  });

  it('retains only solved identities or explicit pins when a later batch contains provisional seeds', () => {
    const worker = new WorkerStub(), driver = new SemanticLayoutDriver(vi.fn(), () => worker);
    driver.request(view(), new Map(), new Set(), new Set());
    const first = worker.flush(), solved = new Map(first.anchors);
    const initialNew = { x: .8, y: -.4, z: .2 };
    const pinnedNew = { x: -.6, y: .3, z: -.1 };
    const previous = new Map([...solved, ['new', initialNew], ['pinned', pinnedNew]]);
    driver.request(view(['a', 'b', 'new', 'pinned']), previous, new Set(), new Set(['pinned']));
    expect(worker.requests[1].previous).toEqual([...solved, ['pinned', pinnedNew]]);
    const response = worker.flush();
    expect(response.anchors.find(([id]) => id === 'a')![1]).toEqual(solved.get('a'));
    expect(response.anchors.find(([id]) => id === 'b')![1]).toEqual(solved.get('b'));
    expect(response.anchors.find(([id]) => id === 'pinned')![1]).toEqual(pinnedNew);
    driver.clear();
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

  it('restores known identities whose current coordinates are provisional even for an unchanged view key', () => {
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker);
    driver.request(view(),new Map(),new Set(),new Set());const first=worker.flush();
    expect(driver.settled).toBe(true);
    const placeholders=new Map([['a',{x:1.2,y:.7,z:.4}],['b',{x:-1.3,y:-.6,z:-.3}]]);
    driver.request(view(),placeholders,new Set(),new Set(),new Set(['a','b']));
    expect(worker.requests).toHaveLength(2);
    expect(worker.requests[1].previous).toEqual([]);
    expect(driver.settled).toBe(false);
    expect(worker.flush().anchors).toEqual(first.anchors);
    expect(driver.settled).toBe(true);driver.clear();
  });

  it('drops every prior-scope cache on clear and reuses an already applied snapshot without new work', () => {
    const workers: WorkerStub[] = [], driver = new SemanticLayoutDriver(vi.fn(), () => { const worker = new WorkerStub(); workers.push(worker); return worker; });
    driver.request(view(), new Map(), new Set(), new Set()); workers[0].flush();
    driver.request(view(), new Map(), new Set(), new Set()); expect(workers[0].requests).toHaveLength(1);
    driver.clear(); driver.request(view(), new Map(), new Set(), new Set());
    expect(workers[1].requests[0].checkpoint).toBeUndefined(); driver.clear();
  });
});
