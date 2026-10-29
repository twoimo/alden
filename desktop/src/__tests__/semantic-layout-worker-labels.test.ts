import { afterEach, describe, expect, it, vi } from 'vitest';
import { SemanticLayoutDriver, type LayoutRequest, type LayoutResponse, type LayoutWorkerPort } from '../knowledge/semantic-layout-driver';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { parseKnowledgeGraph, overviewGraph, type KnowledgeView } from '../knowledge/graph-model';

// Structured-clone transport with the actual worker entry/retained engine.
// Delayed flush can model an old reply arriving after termination.
async function worker() {
  vi.resetModules();
  const scope = { onmessage: null as ((event: MessageEvent<LayoutRequest>) => void) | null,
    postMessage(value: LayoutResponse) { port.onmessage?.({ data: structuredClone(value) } as MessageEvent<LayoutResponse>); } };
  const port: LayoutWorkerPort & { requests: LayoutRequest[]; flush(): void } = {
    onmessage: null, onerror: null, requests: [], terminate: vi.fn(),
    postMessage(value) { this.requests.push(structuredClone(value)); },
    flush() { scope.onmessage!({ data: this.requests.at(-1)! } as MessageEvent<LayoutRequest>); },
  };
  vi.stubGlobal('self', scope);
  await import('../knowledge/semantic-layout-worker');
  return port;
}

function fixture(bucket = '미분류', duplicates = false): KnowledgeView {
  return overviewGraph(parseKnowledgeGraph({
    nodes: Array.from({ length: 256 }, (_, i) => ({ id: 'node-' + String(i).padStart(3, '0'),
      label: i === 0 ? bucket : duplicates && i >= 40 && i < 48 ? '지원 준비 자료' : '자료 ' + i,
      category: 'note', source_version: 'v1', source_target: i % 2 ? 'target-a' : 'target-b',
      description: 'body excluded from worker', facts: ['fact excluded from worker'] })),
    edges: Array.from({ length: 30 }, (_, i) => ({ source: 'node-000',
      target: 'node-' + String(i + 1).padStart(3, '0'), relation: 'ref', weight: 1 })),
  }), 256);
}
afterEach(() => vi.unstubAllGlobals());

describe('layout label semantics through the actual worker entry', () => {
  it('returns a completed finite graph through the driver when valid relation weights underflow after normalization',async()=>{
    const port=await worker(),apply=vi.fn(),driver=new SemanticLayoutDriver(apply,()=>port);
    const base=fixture(),source={...base,edges:base.edges.map(edge=>({...edge,weight:Number.MIN_VALUE}))};
    const before=structuredClone(source);
    driver.request(source,new Map(),new Set(),new Set());port.flush();
    expect(apply).toHaveBeenCalledTimes(1);expect(driver.settled).toBe(true);
    const response=apply.mock.calls[0][0]as LayoutResponse;
    expect(response.error).toBeUndefined();expect(response.anchors).toHaveLength(256);
    expect(response.diagnostics!.numericallyInactivePairs).toBeGreaterThan(0);
    expect(response.anchors!.every(([,p])=>[p.x,p.y,p.z].every(Number.isFinite))).toBe(true);
    expect(source).toEqual(before);driver.clear();
  });
  it.each(['미분류', '홍보 안내', '면접 준비'])('matches direct layout for the saved %s label without source mutation', async bucket => {
    const port = await worker(), apply = vi.fn();
    const driver = new SemanticLayoutDriver(apply, () => port), source = fixture(bucket, true);
    const before = structuredClone(source);
    driver.request(source, new Map(), new Set(), new Set());
    const request = port.requests[0];
    const direct = new SemanticLayoutEngine().layout(source, new Map(), [], { nowMs: request.nowMs });
    port.flush();
    expect(apply).toHaveBeenCalledTimes(1);
    expect(new Map(apply.mock.calls[0][0].anchors)).toEqual(direct.anchors);
    expect(request.view.nodes.map(node => [node.id, node.label, node.sourceTarget]))
      .toEqual(source.nodes.map(node => [node.id, node.label, node.sourceTarget]));
    expect(request.view.nodes.every(node => node.description === '' && !node.facts?.length)).toBe(true);
    expect(new Set(apply.mock.calls[0][0].anchors.map(([id]: [string, unknown]) => id)).size).toBe(256);
    expect(source).toEqual(before); driver.clear();
  });

  it('invalidates completed cache and membership after a saved title changes without an ID/version change', async () => {
    const port = await worker(), apply = vi.fn();
    const driver = new SemanticLayoutDriver(apply, () => port), source = fixture();
    driver.request(source, new Map(), new Set(), new Set()); port.flush();
    const next = { ...source, nodes: source.nodes.map(node => node.id === 'node-000' ? { ...node, label: '면접 준비' } : node) };
    expect(driver.labelsFor(next, new Set())).toBeUndefined();
    driver.request(next, new Map(), new Set(), new Set());
    expect(port.requests).toHaveLength(2); expect(driver.settled).toBe(false);
    port.flush(); expect(apply).toHaveBeenCalledTimes(2);
    expect(driver.labelsFor(source, new Set())).toBeUndefined();
    expect(driver.labelsFor(next, new Set())).toBeDefined();
    driver.request(next, new Map(), new Set(), new Set());
    expect(port.requests).toHaveLength(2); expect(driver.diagnostics.state).toBe('cached'); driver.clear();
  });

  it('rejects the old worker reply when only the saved title changes in flight', async () => {
    const first = await worker(), second = await worker(), ports = [first, second], apply = vi.fn();
    const driver = new SemanticLayoutDriver(apply, () => ports.shift()!), source = fixture();
    driver.request(source, new Map(), new Set(), new Set());
    const next = { ...source, nodes: source.nodes.map(node => node.id === 'node-000' ? { ...node, label: '면접 준비' } : node) };
    driver.request(next, new Map(), new Set(), new Set());
    expect(first.terminate).toHaveBeenCalledTimes(1); expect(second.requests).toHaveLength(1);
    first.flush(); expect(apply).not.toHaveBeenCalled();
    second.flush(); expect(apply).toHaveBeenCalledTimes(1); expect(driver.settled).toBe(true); driver.clear();
  });
});
