import { describe, expect, it, vi } from 'vitest';
import { SemanticLayoutDriver, type LayoutRequest, type LayoutResponse, type LayoutWorkerPort } from '../knowledge/semantic-layout-driver';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { parseKnowledgeGraph, overviewGraph, type KnowledgeView } from '../knowledge/graph-model';

class WorkerStub implements LayoutWorkerPort {
  constructor(private readonly engine?: SemanticLayoutEngine) {}
  onmessage: ((event: MessageEvent<LayoutResponse>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  requests: LayoutRequest[] = [];
  terminate = vi.fn();
  postMessage(request: LayoutRequest) { this.requests.push(request); }
  flush() {
    const request = this.requests.at(-1)!, engine = this.engine ?? new SemanticLayoutEngine();
    if (request.checkpoint) engine.restore(request.checkpoint);
    const result = engine.layout(request.view, new Map(request.previous), request.pairs, { changedIds: new Set(request.changed), pinnedIds: new Set(request.pinned), reflow: request.reflow === true });
    const response = { id: request.id, anchors: [...result.anchors], diagnostics: result.diagnostics, checkpoint: engine.checkpoint(), wallMs: 1 };
    this.onmessage?.({ data: response } as MessageEvent<LayoutResponse>);
    return response;
  }
}
const view = (ids = ['a', 'b']) => overviewGraph(parseKnowledgeGraph({ nodes: ids.map(id => ({ id, label: id })),
  edges: [{ source: 'a', target: 'b', relation: 'ref', weight: 1 }] }));

const withAffinity=(source:KnowledgeView):KnowledgeView=>({...source,layoutAffinity:{
  state:'bounded_ready',inputKey:'a'.repeat(64),profileKey:'b'.repeat(64),
  inputNodes:source.nodes.map(node=>({id:node.id,sourceVersion:node.sourceVersion??'synthetic-v1',sourceTarget:node.sourceTarget??'synthetic'})),
  pairs:[{source:'a',target:'b',cosine:.9}],requestedNodes:2,usableNodes:2,
  contentExcludedNodes:0,validationRejectedNodes:0,evaluatedPairs:1,truncatedCandidates:0,
}});

describe('owned asynchronous semantic layout', () => {
  it('re-solves a changed explicit pin even with an unchanged graph and pin identity',()=>{
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),source=view();
    const first=new Map([['a',{x:.3,y:.2,z:0}]]);
    driver.request(source,first,new Set(),new Set(['a']));worker.flush();
    const moved=new Map([['a',{x:.5,y:.1,z:.2}]]);
    driver.request(source,moved,new Set(),new Set(['a']));
    expect(worker.requests).toHaveLength(2);
    expect(worker.flush().anchors.find(([id])=>id==='a')![1]).toEqual(moved.get('a'));
    driver.clear();
  });

  it('runs an explicit reflow on an unchanged completed graph and returns to cached mode afterward', () => {
    const worker = new WorkerStub(), driver = new SemanticLayoutDriver(vi.fn(), () => worker), source = view();
    driver.request(source, new Map(), new Set(), new Set());
    const first = worker.flush();
    driver.request(source, new Map(first.anchors), new Set(), new Set(), new Set(), true);
    expect(worker.requests).toHaveLength(2);
    expect(worker.requests[1].reflow).toBe(true);
    expect(driver.settled).toBe(false);
    driver.request(source, new Map(first.anchors), new Set(['a']), new Set(), new Set(['b']));
    expect(worker.requests).toHaveLength(2);
    expect(worker.terminate).not.toHaveBeenCalled();
    const redone = worker.flush();
    expect(redone.diagnostics.reflow).toBe(true);
    expect(new Set(redone.anchors.map(([id]) => id))).toEqual(new Set(source.nodes.map(node => node.id)));
    driver.request(source, new Map(redone.anchors), new Set(), new Set());
    expect(worker.requests).toHaveLength(2);
    expect(driver.settled).toBe(true);
    driver.clear();
  });

  it('does not reuse a completed result after a node retracts or an explicit visible change is requested',()=>{
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),source=view();
    driver.request(source,new Map(),new Set(),new Set());worker.flush();
    driver.request(source,new Map(),new Set(['a']),new Set());expect(worker.requests).toHaveLength(2);worker.flush();
    const retracted={...source,nodes:source.nodes.map(n=>n.id==='b'?{...n,evidence:{...n.evidence,retracted:true}}:n)};
    expect(driver.labelsFor(retracted,new Set())).toBeUndefined();
    driver.request(retracted,new Map(),new Set(),new Set());
    expect(worker.flush().anchors.map(([id])=>id)).toEqual(['a']);driver.clear();
  });

  it('does not declare a failed same-key restoration settled',()=>{
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),source=view();
    driver.request(source,new Map(),new Set(),new Set());worker.flush();
    const placeholders=new Map([['a',{x:1,y:0,z:0}],['b',{x:-1,y:0,z:0}]]);
    driver.request(source,placeholders,new Set(),new Set(),new Set(['a','b']));
    worker.onerror?.(new Event('error'));
    expect(driver.diagnostics.state).toBe('failed');expect(driver.settled).toBe(false);driver.clear();
  });

  it('fails a requested pin with no current position instead of inventing a checkpoint position',()=>{
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),source=view();
    driver.request(source,new Map(),new Set(),new Set());worker.flush();
    driver.request(source,new Map(),new Set(),new Set(['a']));
    expect(driver.diagnostics.state).toBe('failed');expect(driver.diagnostics.reason).toBe('semantic_layout_pin_unavailable');
    expect(driver.settled).toBe(false);expect(worker.requests).toHaveLength(1);driver.clear();
  });

  it('uses identical completed solver coordinates when affinities arrive during or after presentation settling',()=>{
    const run=(provisional:boolean)=>{
      const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),firstView=view();
      driver.request(firstView,new Map(),new Set(),new Set());const completed=worker.flush();
      const shown=new Map(completed.anchors.map(([id,p],i)=>[id,{x:p.x+.007*(i+1),y:p.y-.006,z:p.z+.002}]));
      const before=JSON.stringify([...shown]);
      const next=withAffinity(firstView);
      driver.request(next,shown,new Set(),new Set(),new Set(provisional?shown.keys():[]));
      const response=worker.flush();expect(JSON.stringify([...shown])).toBe(before);
      driver.clear();return response.anchors;
    };
    expect(run(false)).toEqual(run(true));
  });

  it('preserves the current explicit pin while excluding unrelated cached coordinates from a request',()=>{
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),firstView=view();
    driver.request(firstView,new Map(),new Set(),new Set());const completed=worker.flush();
    const shown=new Map(completed.anchors.map(([id,p])=>[id,{...p,x:p.x+.013}]));
    shown.set('outside',{x:9,y:9,z:9});
    const next=withAffinity(firstView);
    driver.request(next,shown,new Set(),new Set(['a']));
    const request=worker.requests.at(-1)!;
    expect(request.previous.find(([id])=>id==='a')![1]).toEqual(shown.get('a'));
    expect(request.previous.find(([id])=>id==='b')![1]).toEqual(new Map(completed.anchors).get('b'));
    expect(request.previous.some(([id])=>id==='outside')).toBe(false);
    driver.clear();
  });

  it('does not restart a completed current scope because a hidden identity remains provisional',()=>{
    const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker);
    driver.request(view(),new Map(),new Set(),new Set());const completed=worker.flush();
    const shown=new Map([...completed.anchors,['outside',{x:1,y:1,z:1}]]);
    driver.request(view(),shown,new Set(),new Set(),new Set(['outside']));
    expect(worker.requests).toHaveLength(1);expect(driver.settled).toBe(true);driver.clear();
  });

  it('cancels obsolete work when the requested scope returns to its completed cache',()=>{
    const workers:WorkerStub[]=[],apply=vi.fn(),driver=new SemanticLayoutDriver(apply,()=>{const w=new WorkerStub();workers.push(w);return w;});
    const a=view(),b={...a,edges:[]};driver.request(a,new Map(),new Set(),new Set());workers[0].flush();
    driver.request(b,new Map(),new Set(),new Set());const old=workers[0].onmessage;
    driver.request(a,new Map(),new Set(),new Set());expect(driver.settled).toBe(true);expect(driver.diagnostics.state).toBe('cached');
    expect(workers[0].terminate).toHaveBeenCalled();old?.({data:{id:workers[0].requests.at(-1)!.id}} as MessageEvent<LayoutResponse>);
    expect(apply).toHaveBeenCalledTimes(1);expect(driver.labelsFor(a,new Set())).toBeDefined();driver.clear();
  });
  it('rejects an active-relation result delivered after its expiry before the main-thread timer runs',()=>{
    vi.useFakeTimers();vi.setSystemTime(1000);
    try {
      const worker=new WorkerStub(),apply=vi.fn(),driver=new SemanticLayoutDriver(apply,()=>worker),a=view();
      a.edges=a.edges.map(e=>({...e,validTo:'1970-01-01T00:00:02.000Z'}));
      driver.request(a,new Map(),new Set(),new Set());
      const request=worker.requests[0],engine=new SemanticLayoutEngine(),result=engine.layout(request.view,new Map());
      const response={id:request.id,anchors:[...result.anchors],diagnostics:result.diagnostics,checkpoint:engine.checkpoint(),wallMs:1};
      vi.setSystemTime(2000);worker.onmessage?.({data:response} as MessageEvent<LayoutResponse>);
      expect(apply).not.toHaveBeenCalled();expect(driver.labelsFor(a,new Set())).toBeUndefined();
      expect(worker.requests).toHaveLength(2);worker.flush();expect(apply).toHaveBeenCalledTimes(1);driver.clear();
    } finally {vi.useRealTimers();}
  });
  it('invalidates same-ID cached communities when an unchanged link reaches its valid-to boundary',()=>{
    vi.useFakeTimers();vi.setSystemTime(1000);
    try {
      const worker=new WorkerStub(),driver=new SemanticLayoutDriver(vi.fn(),()=>worker),a=view();
      a.edges=a.edges.map(e=>({...e,validTo:'1970-01-01T00:00:02.000Z'}));
      driver.request(a,new Map(),new Set(),new Set());worker.flush();
      expect(driver.labelsFor(a,new Set())).toBeDefined();vi.setSystemTime(2000);
      expect(driver.labelsFor(a,new Set())).toBeUndefined();
      driver.request(a,new Map(),new Set(),new Set());expect(worker.requests).toHaveLength(2);
      const expired=worker.flush();expect(new Set(expired.checkpoint.labels.map(([,label])=>label)).size).toBe(2);driver.clear();
    } finally {vi.useRealTimers();}
  });
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

// The real worker retains its engine, and postMessage snapshots its inputs.
class AuthorityWorkerStub extends WorkerStub {
  constructor() { super(new SemanticLayoutEngine()); }
  override postMessage(request: LayoutRequest) { super.postMessage(structuredClone(request)); }
}

const authorityChanges = [
  ['state', { state: 'bounded_partial' }],
  ['profileKey', { profileKey: 'c'.repeat(64) }],
  ['inputKey', { inputKey: 'd'.repeat(64) }],
] as const;
const authorityFixture = () => {
  const workers: AuthorityWorkerStub[] = [], apply = vi.fn();
  const driver = new SemanticLayoutDriver(apply, () => {
    const worker = new AuthorityWorkerStub(); workers.push(worker); return worker;
  });
  return { workers, apply, driver };
};

describe('affinity worker authority boundary ALD-WORKER-SCOPE-004', () => {
  it.each(authorityChanges)('rejects completed labels when only %s changes', (_field, delta) => {
    const { driver, workers } = authorityFixture(), source = withAffinity(view());
    driver.request(source, new Map(), new Set(), new Set());
    const first = workers[0].flush();
    const next = { ...source, layoutAffinity: { ...source.layoutAffinity!, ...delta } };
    expect(next.nodes).toBe(source.nodes); expect(next.edges).toBe(source.edges);
    expect(next.layoutAffinity.pairs).toBe(source.layoutAffinity!.pairs);
    expect(driver.labelsFor(next, new Set())).toBeUndefined();
    expect(driver.labelsFor(source, new Set())).toEqual(first.checkpoint.labels);
    driver.clear();
  });

  it.each(authorityChanges)('restarts an identical completed graph when only %s changes', (_field, delta) => {
    const { driver, workers, apply } = authorityFixture(), source = withAffinity(view());
    const pinned = new Set(['a']), initial = new Map([['a', { x: .3, y: .2, z: 0 }]]);
    driver.request(source, initial, new Set(), pinned);
    const first = workers[0].flush(), previous = new Map(first.anchors);
    previous.set('b', { x: .7, y: .6, z: .5 });
    previous.set('outside', { x: 9, y: 9, z: 9 });
    const next = { ...source, layoutAffinity: { ...source.layoutAffinity!, ...delta } };
    const before = structuredClone({ source, next, previous });
    driver.request(next, previous, new Set(), pinned);
    expect(driver.diagnostics.state).toBe('pending');
    expect(driver.ready).toBe(false); expect(driver.settled).toBe(false);
    expect(workers).toHaveLength(2); expect(workers[0].terminate).toHaveBeenCalledTimes(1);
    const request = workers[1].requests[0];
    expect(request.checkpoint).toBeUndefined();
    expect(request.previous).toEqual(first.anchors);
    expect(request.pairs).toEqual(source.layoutAffinity!.pairs);
    expect(request.pinned).toEqual(['a']); expect(request.view.nodes).toHaveLength(source.nodes.length);
    expect(request.view.nodes.every(node => node.label === node.id && node.description === '' && node.facts?.length === 0)).toBe(true);
    expect(request.view.edges.every(edge => edge.context === '' && edge.evidenceMessageId === '' && !edge.evidence.sourceEventIds.length)).toBe(true);
    workers[0].flush(); expect(apply).toHaveBeenCalledTimes(1);
    const current = workers[1].flush();
    expect(current.anchors.find(([id]) => id === 'a')![1]).toEqual(initial.get('a'));
    expect(driver.labelsFor(next, pinned)).toEqual(current.checkpoint.labels);
    expect(driver.labelsFor(source, pinned)).toBeUndefined();
    expect({ source, next, previous }).toEqual(before);
    driver.clear();
  });

  it.each(authorityChanges)('drops the checkpoint and worker engine on a forced solve after %s changes', (_field, delta) => {
    const { driver, workers } = authorityFixture(), source = withAffinity(view());
    driver.request(source, new Map(), new Set(), new Set());
    const first = workers[0].flush();
    const next = { ...source, layoutAffinity: { ...source.layoutAffinity!, ...delta } };
    driver.request(next, new Map(first.anchors), new Set(), new Set(), new Set(), true);
    expect(workers.flatMap(worker => worker.requests).at(-1)!.checkpoint).toBeUndefined();
    expect(workers).toHaveLength(2); expect(workers[0].terminate).toHaveBeenCalledTimes(1);
    expect(workers[1].requests[0].reflow).toBe(true);
    workers[1].flush(); expect(driver.settled).toBe(true); driver.clear();
  });

  it.each(authorityChanges)('supersedes an in-flight reflow when only %s changes', (_field, delta) => {
    const { driver, workers, apply } = authorityFixture(), source = withAffinity(view());
    driver.request(source, new Map(), new Set(), new Set(), new Set(), true);
    source.layoutAffinity = { ...source.layoutAffinity!, ...delta };
    driver.request(source, new Map(), new Set(), new Set());
    expect(workers).toHaveLength(2); expect(workers[0].terminate).toHaveBeenCalledTimes(1);
    expect(workers[1].requests[0].id).toBeGreaterThan(workers[0].requests[0].id);
    expect(workers[1].requests[0].checkpoint).toBeUndefined();
    workers[0].flush(); expect(apply).not.toHaveBeenCalled();
    const current = workers[1].flush(); expect(apply).toHaveBeenCalledTimes(1);
    expect(driver.labelsFor(source, new Set())).toEqual(current.checkpoint.labels); driver.clear();
  });

  it.each(authorityChanges)('rejects a reply if %s mutates before the caller queues its replacement', (_field, delta) => {
    const { driver, workers, apply } = authorityFixture(), source = withAffinity(view());
    driver.request(source, new Map(), new Set(), new Set());
    Object.assign(source.layoutAffinity!, delta);
    workers[0].flush();
    expect(apply).not.toHaveBeenCalled();
    expect(driver.labelsFor(source, new Set())).toBeUndefined();
    expect(workers).toHaveLength(2); expect(workers[0].terminate).toHaveBeenCalledTimes(1);
    expect(workers[1].requests[0].checkpoint).toBeUndefined();
    workers[1].flush(); expect(apply).toHaveBeenCalledTimes(1); driver.clear();
  });

  it.each([false, true])('invalidates empty-pair authority when affinity presence changes (initially present: %s)', present => {
    const plain = view(), affinity = withAffinity(plain);
    affinity.layoutAffinity!.pairs = [];
    const source = present ? affinity : plain, next = present ? plain : affinity;
    const { driver, workers } = authorityFixture();
    driver.request(source, new Map(), new Set(), new Set());
    const first = workers[0].flush();
    expect(driver.labelsFor(next, new Set())).toBeUndefined();
    driver.request(next, new Map(first.anchors), new Set(), new Set());
    expect(workers).toHaveLength(2); expect(workers[1].requests[0].checkpoint).toBeUndefined();
    workers[1].flush(); expect(driver.settled).toBe(true); driver.clear();
  });

  it('retains same-authority caching, in-flight coalescing and completed checkpoints', () => {
    const { driver, workers, apply } = authorityFixture(), source = withAffinity(view());
    driver.request(source, new Map(), new Set(), new Set());
    driver.request(structuredClone(source), new Map(), new Set(), new Set());
    expect(workers[0].requests).toHaveLength(1);
    const first = workers[0].flush(), previous = new Map(first.anchors);
    const same = { ...source, layoutAffinity: { ...source.layoutAffinity!, evaluatedPairs: 2 } };
    driver.request(same, previous, new Set(), new Set());
    expect(driver.diagnostics.state).toBe('cached'); expect(driver.settled).toBe(true);
    expect(driver.labelsFor(same, new Set())).toEqual(first.checkpoint.labels);
    driver.request(same, previous, new Set(), new Set(), new Set(), true);
    expect(workers[0].requests[1].checkpoint).toEqual(first.checkpoint);
    driver.request(same, previous, new Set(), new Set());
    expect(workers[0].requests).toHaveLength(2); expect(workers[0].terminate).not.toHaveBeenCalled();
    workers[0].flush(); expect(apply).toHaveBeenCalledTimes(2); driver.clear();
  });

  it('does not resurrect completed authority after cancelling its replacement', () => {
    const { driver, workers, apply } = authorityFixture(), source = withAffinity(view());
    driver.request(source, new Map(), new Set(), new Set());
    const first = workers[0].flush(), previous = new Map(first.anchors);
    const next = { ...source, layoutAffinity: { ...source.layoutAffinity!, profileKey: 'c'.repeat(64) } };
    driver.request(next, previous, new Set(), new Set()); driver.stop();
    expect(driver.ready).toBe(false); expect(driver.labelsFor(source, new Set())).toBeUndefined();
    driver.request(source, previous, new Set(), new Set());
    expect(workers).toHaveLength(3); expect(workers[2].requests[0].checkpoint).toBeUndefined();
    expect(workers[2].requests[0].previous).toEqual(first.anchors);
    workers[0].flush(); workers[1].flush(); expect(apply).toHaveBeenCalledTimes(1);
    workers[2].flush(); expect(apply).toHaveBeenCalledTimes(2); driver.clear();
  });

  it('revokes the old checkpoint even when the new authority has an unavailable pin', () => {
    const { driver, workers } = authorityFixture(), source = withAffinity(view()), pinned = new Set(['a']);
    driver.request(source, new Map([['a', { x: .3, y: .2, z: 0 }]]), new Set(), pinned);
    const first = workers[0].flush();
    const next = { ...source, layoutAffinity: { ...source.layoutAffinity!, inputKey: 'd'.repeat(64) } };
    driver.request(next, new Map(), new Set(), pinned);
    expect(driver.diagnostics).toMatchObject({ state: 'failed', reason: 'semantic_layout_pin_unavailable' });
    expect(driver.ready).toBe(false); expect(driver.labelsFor(source, pinned)).toBeUndefined();
    driver.request(next, new Map(first.anchors), new Set(), pinned);
    expect(workers[1].requests[0].checkpoint).toBeUndefined();
    workers[1].flush(); expect(driver.settled).toBe(true); driver.clear();
  });
});
