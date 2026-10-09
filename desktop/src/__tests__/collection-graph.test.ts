// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CollectionGraphController } from '../knowledge/collection-graph';
import { KnowledgeDrilldown, type KnowledgeGraph, type KnowledgeNode } from '../knowledge/graph-model';
import { settingsMarkup } from '../ui';
import type { fetchSettingsAction } from '../runtime';
import type { LayoutAffinity } from '../knowledge/layout-affinity';
import { affinityPayload } from './fixtures/affinity-payload';

const owned: CollectionGraphController[] = [];
const node = (id: string, version = 'v1') => ({ id, label: id, source_version: version, degree: 2, degree_scope: 'permitted filtered graph' });
const page = (ids = ['a', 'b'], extra = {}) => ({ ok: true, nodes: ids.map(id => node(id)), edges: [], total_nodes: 300,
  total_edges: 400, next: 120, facets: { node_types: ['topic'], relations: ['refers-to'] }, targets: [{ id: 'target-a', label: '대상 A' }], ...extra });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
class Port {
  private graph: KnowledgeGraph = { nodes: [], edges: [] };
  private model = new KnowledgeDrilldown(this.graph);
  controller!: CollectionGraphController;
  camera = { camera: [9, 8, 7], lookAt: [1, 2, 3] };
  replacements = 0;
  showNodeActivity = vi.fn();
  clearNodeActivity = vi.fn();
  setLayoutAffinity = vi.fn((value: LayoutAffinity | null) => { this.graph.layoutAffinity = value ?? undefined; });
  get currentGraph() { return this.graph; }
  get currentView() { return this.model.current(); }
  get navigationTargets() { return { camera: [...this.camera.camera], lookAt: [...this.camera.lookAt] }; }
  replaceGraph(graph: KnowledgeGraph, navigation?: { focusId: string | null; hops: number }, viewpoint?: typeof this.camera) {
    this.replacements++; this.graph = graph;
    if (navigation) this.model.restore(graph, navigation); else this.model.replaceGraph(graph);
    if (viewpoint) this.camera = viewpoint;
  }
  clickNode(id: string) {
    const previousCamera = this.navigationTargets, view = this.model.openNote(id);
    this.controller.selected({ node: this.graph.nodes.find(n => n.id === id) ?? null, view, previousCamera }); return view;
  }
}
function controller(read: (options: Record<string, unknown>) => Promise<Record<string, unknown>> = async () => page(), memory: Record<string, unknown> = { ok: true, nodes: [], edges: [] },
  affinity: (options: Record<string, unknown>) => Promise<Record<string, unknown>> = async () => ({ ok: false, reason: 'affinity_unavailable' })) {
  const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => action === 'collection-projects'
    ? { ok: true, projects: [{ project: 'one' }, { project: 'two' }] } : action === 'collection-graph'
      ? read(JSON.parse(input.query ?? '{}')) : action === 'collection-affinity' ? affinity(JSON.parse(input.query ?? '{}')) : memory);
  const port = new Port(); const c = new CollectionGraphController(load, port); port.controller = c; owned.push(c); c.start();
  return { c, port, load };
}
async function settle(ms = 0) { await Promise.resolve(); await Promise.resolve(); await vi.advanceTimersByTimeAsync(ms); }
function select(id: string, value: string) { const input = document.getElementById(id) as HTMLSelectElement; input.value = value; input.dispatchEvent(new Event('change')); }
beforeEach(() => { vi.useFakeTimers(); document.body.innerHTML = settingsMarkup(); });
afterEach(() => { for (const c of owned) c.dispose(); owned.length = 0; vi.useRealTimers(); });

describe('permitted collection graph navigation', () => {
  it('shows factual data first and applies later valid affinity without moving selection/camera or creating activity', async () => {
    const pending = deferred<Record<string, unknown>>();
    const { port, load } = controller(async () => page(['a', 'b'], { nodes: ['a', 'b'].map(id => ({ ...node(id), source_target: 'target-a' })),
      edges: [{ source: 'a', target: 'b', relation: 'contradicts', weight: 1 }] }), undefined, () => pending.promise);
    await settle(); expect(port.currentGraph.nodes).toHaveLength(2);
    const replacements = port.replacements, facts = JSON.stringify(port.currentGraph.edges);
    port.clickNode('a'); port.camera = { camera: [8, 4, 2], lookAt: [1, 0, 0] };
    pending.resolve({ ok: true, layout_affinity: affinityPayload() }); await settle();
    expect(port.setLayoutAffinity).toHaveBeenCalledTimes(1); expect(port.currentGraph.layoutAffinity?.pairs).toHaveLength(1);
    expect(port.currentView.focusId).toBe('a'); expect(port.navigationTargets.camera).toEqual([8, 4, 2]);
    expect(port.replacements).toBe(replacements); expect(JSON.stringify(port.currentGraph.edges)).toBe(facts);
    expect(port.showNodeActivity).not.toHaveBeenCalled();
    expect(load.mock.calls.filter(([action]) => action === 'collection-affinity')).toHaveLength(1);
  });

  it('fences an old affinity scope even when two scopes return identical node/version/target references', async () => {
    const old = deferred<Record<string, unknown>>(), current = deferred<Record<string, unknown>>(); let calls = 0;
    const { c, port } = controller(async () => page(['a', 'b'], { nodes: ['a', 'b'].map(id => ({ ...node(id), source_target: 'target-a' })) }), undefined,
      () => ++calls === 1 ? old.promise : current.promise);
    await settle(); select('knowledge-project', 'project:two'); await settle();
    old.resolve({ ok: true, layout_affinity: affinityPayload() }); await settle();
    expect(port.setLayoutAffinity).not.toHaveBeenCalled(); expect(calls).toBe(2);
    current.resolve({ ok: true, layout_affinity: affinityPayload(['a', 'b'], 'two') }); await settle();
    expect(port.setLayoutAffinity).toHaveBeenCalledTimes(1); expect(c.readDiagnostics?.affinity).toMatchObject({ state: 'bounded_ready' });
  });

  it('discards a late hidden result and reads again on resume without overlapping affinity jobs', async () => {
    const old = deferred<Record<string, unknown>>(); let calls = 0;
    const { c, port } = controller(async () => page(['a', 'b'], { nodes: ['a', 'b'].map(id => ({ ...node(id), source_target: 'target-a' })) }), undefined,
      async () => ++calls === 1 ? old.promise : { ok: true, layout_affinity: affinityPayload() });
    await settle(); c.stop(); c.start(); await settle(); expect(calls).toBe(1);
    old.resolve({ ok: true, layout_affinity: affinityPayload() }); await settle();
    expect(calls).toBe(2); expect(port.setLayoutAffinity).toHaveBeenCalledTimes(1);
    c.stop(); await settle(60000); expect(calls).toBe(2);
  });

  it('preserves a specific read failure and never applies an old-version candidate', async () => {
    let response: Record<string, unknown> = { ok: false, reason: 'affinity_cpu_budget' };
    const { c, port } = controller(async () => page(['a', 'b'], { nodes: ['a', 'b'].map(id => ({ ...node(id), source_target: 'target-a' })) }), undefined, async () => response);
    await settle(); expect(c.readDiagnostics?.affinity).toMatchObject({ reason: 'affinity_cpu_budget' });
    response = { ok: true, layout_affinity: affinityPayload(['a', 'b'], 'one', 'old') };
    await settle(15000); expect(c.readDiagnostics?.affinity).toMatchObject({ reason: 'affinity_stale_input' });
    expect(port.currentGraph.layoutAffinity).toBeUndefined(); expect(port.setLayoutAffinity).not.toHaveBeenCalled();
  });

  it('reads a memory inside the unified view through its canonical reader without mutating source payloads', async () => {
    const { c, port, load } = controller(async () => page(), { ok: true, nodes: [{ id: 'canonical', label: '기억' }], edges: [] });
    await settle();
    const n = port.currentGraph.nodes.find(node => node.id === 'memory:canonical')!;
    const original = Object.freeze({ ok: true, details: Object.freeze({ node_id: 'canonical', body: '원문' }) });
    const legacy = vi.fn(async () => original);
    const result = await c.focus(n, legacy);
    expect(legacy).toHaveBeenCalledTimes(1);
    expect(result?.details).toMatchObject({ node_id: 'memory:canonical', body: '원문' });
    expect(original.details.node_id).toBe('canonical');
    expect(load.mock.calls.some(([action, input]) => action === 'collection-graph' && JSON.parse(input!.query!).focus === 'memory:canonical')).toBe(false);
  });

  it('loads actual project choices and distinguishes the bounded scene from the full permitted graph', async () => {
    const { port, load } = controller(); await settle();
    expect(port.currentGraph.nodes.map(n => n.id)).toEqual(['a', 'b']);
    expect(document.getElementById('knowledge-project')!.textContent).toContain('two');
    expect(document.getElementById('knowledge-scope')!.textContent).toContain('300');
    expect(document.getElementById('knowledge-scope')!.textContent).toContain('400');
    expect(document.querySelectorAll('#knowledge-result-list button')).toHaveLength(2);
    expect(load.mock.calls.filter(([action]) => action === 'collection-graph')).toHaveLength(1);
  });

  it('pages and expands real remote neighbours, then restores the preceding query and camera', async () => {
    const { c, port, load } = controller(async options => options.focus ? page(['a', 'neighbour'], {
      edges: [{ source: 'a', target: 'neighbour', relation: 'refers-to' }], next: null,
    }) : options.offset === 120 ? page(['later'], { next: null }) : page());
    await settle(); port.clickNode('a');
    c.navigate('expand'); await settle();
    expect(port.currentView.focusId).toBe('a'); expect(port.currentView.hops).toBe(1);
    expect(port.currentGraph.nodes.map(n => n.id)).toContain('neighbour');
    port.camera = { camera: [20, 0, 0], lookAt: [0, 0, 0] };
    c.navigate('back'); await settle();
    expect(port.currentView.hops).toBe(0); expect(port.navigationTargets.camera).toEqual([9, 8, 7]);
    c.navigate('back'); await settle(); expect(port.currentView.focusId).toBe(null);
    c.navigate('expand'); await settle(); expect(port.currentGraph.nodes[0].id).toBe('later');
    c.navigate('back'); await settle(); expect(port.currentGraph.nodes.map(n => n.id)).toEqual(['a', 'b']);
    expect(load.mock.calls.some(([a, input]) => a === 'collection-graph' && JSON.parse(input!.query!).focus === 'a')).toBe(true);
  });

  it('fences an old project read before accepting the next project result', async () => {
    const old = deferred<Record<string, unknown>>();
    const { port } = controller(async options => options.projects ? page(['permitted-two']) : old.promise);
    await settle(); select('knowledge-project', 'project:two');
    old.resolve(page(['old-project'])); await settle();
    expect(port.currentGraph.nodes.map(n => n.id)).not.toContain('old-project');
    await settle(1000); expect(port.currentGraph.nodes[0].id).toBe('permitted-two');
  });

  it('stops reads and discards late focus results while hidden, then resumes one poller', async () => {
    const pending = deferred<Record<string, unknown>>();
    const { c, port, load } = controller(async options => options.details ? pending.promise : page());
    await settle(); const focus = c.focus(port.currentGraph.nodes[0], async () => null);
    c.stop(); const count = load.mock.calls.length;
    pending.resolve({ ok: true, details: { node_id: 'a' } });
    expect(await focus).toEqual({ discarded: true }); await settle(60000);
    expect(load).toHaveBeenCalledTimes(count);
    c.start(); c.start(); await settle(); expect(load).toHaveBeenCalledTimes(count + 2); // One collection read and one scoped memory read.
  });

  it('sends the selected target, relation, type, source and local date boundaries to the read API', async () => {
    const { load } = controller(); await settle();
    select('knowledge-target', 'target-a'); select('knowledge-relation', 'refers-to');
    select('knowledge-type', 'topic'); select('knowledge-platform', 'youtube');
    for (const [id, value] of [['knowledge-since', '2026-10-01'], ['knowledge-until', '2026-10-07']]) {
      const input = document.getElementById(id) as HTMLInputElement; input.value = value; input.dispatchEvent(new Event('change'));
    }
    await settle(1000);
    const input = load.mock.calls.filter(([action]) => action === 'collection-graph').at(-1)![1]!;
    const query = JSON.parse(input.query!);
    expect(query).toMatchObject({ target_id: 'target-a', relation: 'refers-to', node_type: 'topic', platform: 'youtube' });
    expect(query.until - query.since).toBe(7 * 86400);
  });

  it('checks selected content versions and refreshes a real version change without moving the camera', async () => {
    let version = 'v1'; const { c, port, load } = controller(async () => page(['a'], { nodes: [node('a', version)] }));
    await settle(); const n: KnowledgeNode = port.currentGraph.nodes[0];
    await c.focus(n, async () => null);
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).toMatchObject({ expected_version: 'v1', details: true });
    port.camera = { camera: [12, 4, 8], lookAt: [3, 1, 0] }; const before = port.replacements;
    version = 'v2'; await settle(15000);
    expect(port.replacements).toBe(before + 1); expect(port.currentGraph.nodes[0].sourceVersion).toBe('v2');
    expect(port.navigationTargets.camera).toEqual([12, 4, 8]);
  });

  it('uses visible list labels as literal text and opens the identical node', async () => {
    const label = '<img src=x onerror=alert(1)>';
    const { port } = controller(async () => page(['a'], { nodes: [{ ...node('a'), label }] })); await settle();
    const button = document.querySelector<HTMLButtonElement>('#knowledge-result-list button')!;
    expect(button.textContent).toBe(label); expect(button.querySelector('img')).toBeNull();
    button.click(); expect(port.currentView.focusId).toBe('a');
  });
});
