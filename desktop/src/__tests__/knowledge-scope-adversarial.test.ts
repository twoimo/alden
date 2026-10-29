// @vitest-environment happy-dom
// Bounded ST15 adversarial schedules. Real controller/refresh/parser/DOM writers;
// only transport and the renderer port are synthetic. Never call private apply.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CollectionGraphController } from '../knowledge/collection-graph';
import { KnowledgeDrilldown, type KnowledgeGraph } from '../knowledge/graph-model';
import type { LayoutAffinity } from '../knowledge/layout-affinity';
import type { fetchSettingsAction } from '../runtime';
import { settingsMarkup } from '../ui';
import { affinityPayload } from './fixtures/affinity-payload';

type Payload = Record<string, unknown>;
type Reply = Payload | null;
type Read = (query: Payload, ordinal: number) => Reply | Promise<Reply>;
type Kind = 'projects' | 'graph' | 'memory' | 'scheduler' | 'details' | 'affinity' | 'history' | 'activity';
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const page = (id: string, extra: Payload = {}): Payload => ({
  ok: true, nodes: [{ id, label: id, source_version: 'v1', source_target: 'target-a' }],
  edges: [], total_nodes: 1, total_edges: 0, ...extra,
});
const projects = { ok: true, projects: [{ project: 'one' }, { project: 'two' }] };

class GraphFixture {
  private value: KnowledgeGraph = { nodes: [], edges: [] };
  private drilldown = new KnowledgeDrilldown(this.value);
  controller!: CollectionGraphController;
  writes: string[][] = [];
  showNodeActivity = vi.fn();
  clearNodeActivity = vi.fn();
  setLayoutAffinity = vi.fn((value: LayoutAffinity | null) => { this.value.layoutAffinity = value ?? undefined; });
  get currentGraph() { return this.value; }
  get currentView() { return this.drilldown.current(); }
  get navigationTargets() { return { camera: [0, 0, 9], lookAt: [0, 0, 0] }; }
  replaceGraph(value: KnowledgeGraph, navigation?: { focusId: string | null; hops: number }) {
    this.value = value;
    this.writes.push(value.nodes.map(node => node.id));
    if (navigation) this.drilldown.restore(value, navigation);
    else this.drilldown.replaceGraph(value);
  }
  clickNode(id: string) {
    const view = this.drilldown.openNote(id);
    this.controller.selected({ node: this.value.nodes.find(node => node.id === id) ?? null,
      view, previousCamera: this.navigationTargets });
    return view;
  }
}

const owned: CollectionGraphController[] = [];
const states: Array<string | null> = [];
let observer: MutationObserver;
function harness(reads: Partial<Record<Kind, Read>> = {}, port = new GraphFixture(),
  revision?: () => Promise<string | null>, start = true) {
  const calls: Record<Kind, Payload[]> = {
    projects: [], graph: [], memory: [], scheduler: [], details: [], affinity: [], history: [], activity: [],
  };
  const defaults: Record<Kind, Reply> = {
    projects, graph: page('current'), memory: { ok: true, nodes: [], edges: [] }, scheduler: { ok: false },
    details: { ok: false }, affinity: { ok: false, reason: 'affinity_unavailable' },
    history: { ok: true, items: [] }, activity: { ok: false },
  };
  const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
    const query = JSON.parse(input.query ?? '{}') as Payload;
    const kind: Kind | undefined = action === 'collection-graph'
      ? query.activity ? 'activity' : query.details ? 'details' : 'graph'
      : ({ 'collection-projects': 'projects', 'knowledge-graph': 'memory',
        'collection-scheduler-status': 'scheduler', 'collection-affinity': 'affinity',
        'collection-history': 'history' } as Record<string, Kind>)[action];
    if (!kind) throw new Error(`Unexpected fixture action: ${action}`);
    calls[kind].push(query);
    return reads[kind] ? await reads[kind]!(query, calls[kind].length) : defaults[kind];
  });
  const controller = new CollectionGraphController(load, port, document, revision);
  port.controller = controller; owned.push(controller);
  if (start) controller.start();
  return { controller, port, calls, load };
}
async function drain(ms = 0) {
  await Promise.resolve(); await Promise.resolve();
  await vi.advanceTimersByTimeAsync(ms);
  await Promise.resolve();
}
function select(value: string) {
  const input = document.getElementById('knowledge-project') as HTMLSelectElement;
  input.value = value; input.dispatchEvent(new Event('change'));
}
function search(value: string) {
  const input = document.getElementById('knowledge-search') as HTMLInputElement;
  input.value = value; input.dispatchEvent(new Event('input'));
}
function toggle() { document.getElementById('knowledge-live-toggle')!.click(); }
function status() { return document.getElementById('knowledge-map-status')!.dataset.state; }
function ids(port: GraphFixture) { return port.currentGraph.nodes.map(node => node.id); }
function observation(port: GraphFixture) {
  return { ids: ids(port), writes: port.writes.map(row => [...row]), status: status(),
    statusHidden: document.getElementById('knowledge-map-status')!.hidden,
    summary: document.getElementById('knowledge-summary')!.textContent,
    mode: document.getElementById('knowledge-mode')!.textContent,
    busy: document.getElementById('knowledge-filter-form')!.getAttribute('aria-busy'),
    collector: document.getElementById('knowledge-mode')!.dataset.collectorState };
}
function forbidOldWrites(port: GraphFixture, cut: number, forbidden: string[]) {
  for (const written of port.writes.slice(cut)) for (const id of forbidden) expect(written).not.toContain(id);
}
function forbidClaim(cut: number) {
  // Mutation old-values preserve intermediate states even if a subsequent writer
  // repairs the final DOM in the same microtask checkpoint.
  expect(states.slice(cut).filter(state => ['ready', 'empty', 'partial', 'stale', 'unavailable'].includes(state ?? ''))).toEqual([]);
}
async function currentWitness(port: GraphFixture, pending: ReturnType<typeof deferred<Reply>>, id = 'current') {
  pending.resolve(page(id)); await drain(1000);
  expect(ids(port)).toEqual([id]); expect(status()).toBe('ready');
  expect(document.getElementById('knowledge-map-status')!.hidden).toBe(true);
}

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-27T00:00:00Z'));
  document.body.innerHTML = settingsMarkup(); states.length = 0;
  observer = new MutationObserver(records => {
    const changes = records.filter(row => row.attributeName === 'data-state');
    for (let index = 0; index < changes.length; index++) {
      states.push(index + 1 < changes.length ? changes[index + 1].oldValue
        : (changes[index].target as HTMLElement).getAttribute('data-state'));
    }
  });
  observer.observe(document.getElementById('knowledge-map-status')!, { attributes: true, attributeOldValue: true, attributeFilter: ['data-state'] });
});
afterEach(() => {
  for (const controller of owned) controller.dispose(); owned.length = 0;
  observer.disconnect(); vi.clearAllTimers(); vi.useRealTimers();
});

describe('ST15 independent public-control adversarial schedules', () => {
  it.each(['memory', 'scheduler'] as const)('fences all -> project -> all while the old %s sibling holds allSettled', async sibling => {
    const oldSibling = deferred<Reply>(), current = deferred<Reply>();
    const h = harness({ graph: (_q, n) => n === 1 ? page('old-collection') : current.promise,
      [sibling]: (_q: Payload, n: number) => n === 1 ? oldSibling.promise
        : sibling === 'memory' ? { ok: true, nodes: [], edges: [] } : { ok: false } });
    await drain(); expect(h.calls.graph).toHaveLength(1); expect(h.calls[sibling]).toHaveLength(1);
    select('project:two'); select('all'); await drain();
    const writes = h.port.writes.length, claim = states.length;
    oldSibling.resolve(sibling === 'memory' ? page('old-memory')
      : { ok: true, realtime: { enabled: true, state: 'processing' } });
    await drain(1000);
    expect(h.calls.graph).toHaveLength(2);
    forbidOldWrites(h.port, writes, ['old-collection', 'memory:old-memory']); forbidClaim(claim);
    expect(h.controller.readDiagnostics).toBeNull();
    expect(document.getElementById('knowledge-mode')!.dataset.collectorState).toBeUndefined();
    await currentWitness(h.port, current);
  });

  it.each([
    ['ready', page('old')], ['empty', { ok: true, nodes: [], edges: [] }],
    ['partial', page('old', { partial: true })], ['stale', page('old', { stale: true })], ['failure', { ok: false }],
  ] as const)('fences old same-scope %s replies across stop/start', async (label, payload) => {
    const old = deferred<Reply>(), current = deferred<Reply>();
    const h = harness({ graph: (_q, n) => n === 1 ? old.promise : current.promise,
      memory: (_q, n) => n === 1 && label === 'failure' ? { ok: false } : { ok: true, nodes: [], edges: [] } });
    await drain(); expect(h.calls.graph).toHaveLength(1);
    h.controller.stop(); h.controller.start(); h.controller.start(); await drain();
    const writes = h.port.writes.length, claim = states.length;
    old.resolve(payload); await drain(1000);
    forbidOldWrites(h.port, writes, ['old']); forbidClaim(claim);
    expect(h.calls.graph).toHaveLength(2); expect(h.calls.projects).toHaveLength(1);
    await currentWitness(h.port, current);
  });

  it('does not let a disposed predecessor clear the replacement owner busy/status or graph', async () => {
    const oldReply = deferred<Reply>(), newReply = deferred<Reply>();
    const old = harness({ graph: () => oldReply.promise }); await drain();
    old.controller.dispose();
    const next = harness({ graph: () => newReply.promise }, old.port); await drain();
    expect(next.calls.graph).toHaveLength(1);
    const waiting = observation(next.port);
    oldReply.resolve(page('disposed-owner')); await drain();
    expect(observation(next.port)).toEqual(waiting);
    await currentWitness(next.port, newReply, 'replacement-owner');
    const complete = observation(next.port);
    old.controller.start(); await drain(5000);
    expect(observation(next.port)).toEqual(complete); expect(old.calls.graph).toHaveLength(1);
  });

  it('discards a disposed owner initialization after its replacement is already current', async () => {
    const initialization = deferred<Reply>();
    const old = harness({ projects: () => initialization.promise }); await drain();
    expect(old.calls.graph).toHaveLength(0); old.controller.dispose();
    const next = harness({}, old.port); await drain();
    expect(status()).toBe('ready'); const before = observation(next.port);
    const options = (document.getElementById('knowledge-project') as HTMLSelectElement).innerHTML;
    initialization.resolve({ ok: true, projects: [{ project: 'disposed-project' }] }); await drain(1000);
    expect(observation(next.port)).toEqual(before);
    expect((document.getElementById('knowledge-project') as HTMLSelectElement).innerHTML).toBe(options);
    expect(old.calls.graph).toHaveLength(0);
  });

  it('preserves an explicit project selection made before initialization finishes', async () => {
    const initialization = deferred<Reply>(), current = deferred<Reply>();
    const h = harness({ projects: () => initialization.promise, graph: () => current.promise });
    // A pre-populated selector can be used before its asynchronous options refresh.
    const control = document.getElementById('knowledge-project') as HTMLSelectElement;
    const option = document.createElement('option'); option.value = 'project:one'; control.append(option);
    select('project:one'); await drain();
    initialization.resolve(projects); await drain();
    expect(control.value).toBe('project:one');
    expect(h.calls.graph.every(query => JSON.stringify(query.projects) === '["one"]')).toBe(true);
    expect(h.calls.memory).toHaveLength(0);
    await currentWitness(h.port, current);
  });

  it('fences the revision await before an obsolete legacy read can even start', async () => {
    const revision = deferred<string | null>(), current = deferred<Reply>();
    const probe = vi.fn(() => revision.promise);
    const h = harness({ graph: () => current.promise }, undefined, probe, false);
    select('legacy'); h.controller.start(); await drain();
    expect(probe).toHaveBeenCalledTimes(1); expect(h.calls.memory).toHaveLength(0);
    select('project:one'); revision.resolve('obsolete-revision'); await drain(1000);
    expect(h.calls.memory).toHaveLength(0); expect(h.calls.graph).toHaveLength(1);
    expect(h.calls.graph[0].projects).toEqual(['one']);
    await currentWitness(h.port, current);
  });

  it('fences a search ABA immediately, including the interval before debounce fires', async () => {
    const old = deferred<Reply>(), current = deferred<Reply>();
    const h = harness({ graph: (_q, n) => n === 1 ? old.promise : current.promise }); await drain();
    search('temporary'); search(''); await drain();
    const cut = h.port.writes.length, claim = states.length;
    old.resolve(page('before-search')); await drain(249);
    expect(h.calls.graph).toHaveLength(1); forbidOldWrites(h.port, cut, ['before-search']); forbidClaim(claim);
    await drain(1001); expect(h.calls.graph).toHaveLength(2); expect(h.calls.graph[1].search).toBe('');
    forbidClaim(claim); await currentWitness(h.port, current);
  });

  it('fences a rejected old read after entity resolution changes the same scope epoch', async () => {
    const old = deferred<Reply>(), current = deferred<Reply>();
    const h = harness({ graph: (_q, n) => n === 1 ? old.promise : current.promise }); await drain();
    window.dispatchEvent(new Event('alden://entity-resolution-changed')); await drain();
    const claim = states.length;
    old.reject(new Error('synthetic old transport failure')); await drain(1000);
    expect(h.calls.graph).toHaveLength(2); forbidClaim(claim);
    expect(h.controller.readDiagnostics).toBeNull(); await currentWitness(h.port, current);
  });

  it('proves the temporal boundary path is reachable before the regular 15-second read', async () => {
    const current = deferred<Reply>();
    const h = harness({ graph: (_q, n) => n === 1
      ? page('confirmed', { next_transition_at: Date.now() / 1000 + 3 }) : current.promise });
    await drain(); expect(ids(h.port)).toEqual(['confirmed']); expect(h.calls.graph).toHaveLength(1);
    await drain(2999); expect(h.calls.graph).toHaveLength(1);
    await drain(2); expect(h.calls.graph).toHaveLength(2);
    await currentWitness(h.port, current, 'after-boundary');
  });

  it('cannot replay a confirmed old-scope payload at its temporal boundary', async () => {
    const current = deferred<Reply>();
    const h = harness({ graph: (_q, n) => n === 1
      ? page('old-confirmed', { next_transition_at: Date.now() / 1000 + 5 }) : current.promise });
    await drain(); expect(ids(h.port)).toEqual(['old-confirmed']);
    select('project:two'); await drain(); const cut = h.port.writes.length, claim = states.length;
    await drain(5001);
    forbidOldWrites(h.port, cut, ['old-confirmed']); forbidClaim(claim);
    expect(ids(h.port)).toEqual([]); expect(status()).toBe('loading');
    await currentWitness(h.port, current);
  });

  it('cannot restore ready via an old temporal replay after restart revalidation fails', async () => {
    const h = harness({ graph: (_q, n) => n === 1
      ? page('last-confirmed', { next_transition_at: Date.now() / 1000 + 5 }) : { ok: false },
      memory: (_q, n) => n === 1 ? { ok: true, nodes: [], edges: [] } : { ok: false } });
    await drain(); expect(status()).toBe('ready');
    h.controller.stop(); h.controller.start(); await drain();
    expect(status()).toBe('unavailable'); const claim = states.length, writes = h.port.writes.length;
    await drain(5001);
    expect(states.slice(claim)).not.toContain('ready'); expect(status()).toBe('unavailable');
    expect(h.port.writes).toHaveLength(writes);
  });

  it.each(['success', 'rejection'] as const)('fences old identical-input affinity %s across stop/start', async outcome => {
    const old = deferred<Reply>(), current = deferred<Reply>();
    const h = harness({ graph: () => page('a', { nodes: ['a', 'b'].map(id => ({
      id, label: id, source_version: 'v1', source_target: 'target-a' })) }),
      affinity: (_q, n) => n === 1 ? old.promise : current.promise });
    await drain(); expect(h.calls.affinity).toHaveLength(1);
    h.controller.stop(); h.controller.start(); await drain();
    const before = observation(h.port);
    if (outcome === 'success') old.resolve({ ok: true, layout_affinity: affinityPayload() });
    else old.reject(new Error('synthetic old affinity failure'));
    await drain();
    expect(observation(h.port)).toEqual(before); expect(h.port.setLayoutAffinity).not.toHaveBeenCalled();
    expect(h.calls.affinity).toHaveLength(2);
    current.resolve({ ok: true, layout_affinity: affinityPayload() }); await drain();
    expect(h.port.setLayoutAffinity).toHaveBeenCalledTimes(1);
    expect(h.port.currentGraph.layoutAffinity?.state).toBe('bounded_ready');
  });

  it('returns discarded for an old detail even when pause/resume preserves the same node reference', async () => {
    const old = deferred<Reply>();
    const detail = { ok: true, details: { node_id: 'current', basis: 'source_record', version: 'v1', target_id: 'target-a' } };
    const h = harness({ details: (_q, n) => n === 1 ? old.promise : detail }); await drain();
    h.port.clickNode('current'); const node = h.port.currentGraph.nodes[0];
    const result = h.controller.focus(node, async () => { throw new Error('Legacy fallback forbidden'); });
    toggle(); toggle(); await drain();
    old.resolve(detail); expect(await result).toEqual({ discarded: true });
    expect(h.port.showNodeActivity).not.toHaveBeenCalled();
    expect(await h.controller.focus(node, async () => null)).toEqual(detail);
    expect(h.port.showNodeActivity).toHaveBeenCalledTimes(1);
  });

  it('fences an in-flight expansion through reset, despite returning to the identical overview query', async () => {
    const expansion = deferred<Reply>(), current = deferred<Reply>();
    const h = harness({ graph: (query, n) => n === 1 ? page('anchor') : query.focus ? expansion.promise : current.promise });
    await drain(); h.port.clickNode('anchor'); expect(h.controller.navigate('expand')).toBe(true); await drain();
    expect(h.calls.graph[1]).toMatchObject({ focus: 'anchor', hops: 1 });
    expect(h.controller.navigate('reset')).toBe(true); await drain(); const cut = h.port.writes.length;
    expansion.resolve(page('obsolete-neighbour')); await drain(1000);
    forbidOldWrites(h.port, cut, ['obsolete-neighbour']);
    expect(h.calls.graph).toHaveLength(3); expect(h.calls.graph[2].focus).toBeUndefined();
    await currentWitness(h.port, current, 'reset-current');
  });

  it('does not publish old history after same-scope pause/resume, then accepts current history', async () => {
    const old = deferred<Reply>();
    const history = (label: string) => ({ ok: true, stream_id: label,
      items: [{ sequence: 1, stage: 'stored', document_id: 'current', document_label: label }] });
    const h = harness({ history: (_q, n) => n === 1 ? old.promise : history('CURRENT_HISTORY') });
    await drain(); expect(h.calls.history).toHaveLength(1);
    toggle(); toggle(); await drain(); old.resolve(history('OBSOLETE_HISTORY')); await drain();
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('OBSOLETE_HISTORY');
    document.getElementById('knowledge-filter-form')!.dispatchEvent(new Event('submit', { cancelable: true }));
    await drain(1000);
    expect(document.getElementById('terminal-tail')!.textContent).toContain('CURRENT_HISTORY');
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('OBSOLETE_HISTORY');
    expect(status()).toBe('ready');
  });
});
