// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CollectionGraphController } from '../knowledge/collection-graph';
import { KnowledgeDrilldown, type KnowledgeGraph, type KnowledgeNode } from '../knowledge/graph-model';
import { settingsMarkup } from '../ui';
import type { fetchSettingsAction } from '../runtime';

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
function controller(read: (options: Record<string, unknown>) => Promise<Record<string, unknown>> = async () => page()) {
  const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => action === 'collection-projects'
    ? { ok: true, projects: [{ project: 'one' }, { project: 'two' }] } : action === 'collection-graph'
      ? read(JSON.parse(input.query ?? '{}')) : { ok: true, nodes: [], edges: [] });
  const port = new Port(); const c = new CollectionGraphController(load, port); port.controller = c; owned.push(c); c.start();
  return { c, port, load };
}
async function settle(ms = 0) { await Promise.resolve(); await Promise.resolve(); await vi.advanceTimersByTimeAsync(ms); }
function select(id: string, value: string) { const input = document.getElementById(id) as HTMLSelectElement; input.value = value; input.dispatchEvent(new Event('change')); }
beforeEach(() => { vi.useFakeTimers(); document.body.innerHTML = settingsMarkup(); });
afterEach(() => { for (const c of owned) c.dispose(); owned.length = 0; vi.useRealTimers(); });

describe('permitted collection graph navigation', () => {
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
    c.start(); c.start(); await settle(); expect(load).toHaveBeenCalledTimes(count + 1);
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
