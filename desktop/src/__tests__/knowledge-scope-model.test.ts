// @vitest-environment happy-dom
import { writeFileSync } from 'node:fs';
import { expect, it, vi } from 'vitest';
import { CollectionGraphController } from '../knowledge/collection-graph';
import { KnowledgeDrilldown, type KnowledgeGraph } from '../knowledge/graph-model';
import { settingsMarkup } from '../ui';
import type { fetchSettingsAction } from '../runtime';

type Action = 'switch' | 'pause' | 'hide' | 'show' | 'dispose' | 'ok' | 'error' | 'tick';
type Domain = { active: boolean; paused: boolean; disposed: boolean; switches: number; toggles: number; visibility: number; replies: number; ticks: number };
const bounds = { depth: 5, projects: 2, switches: 2, pauseToggles: 2, visibilityChanges: 2, replies: 2, ticks: 1, tickMs: 1000 };

// This enumerates bounded input schedules, not a translation of the controller.
// Provider completions without an issued request are rejected during replay.
function schedules(): Action[][] {
  const out: Action[][] = [];
  function visit(trace: Action[], d: Domain): void {
    out.push(trace);
    if (trace.length === bounds.depth) return;
    const next: Array<[Action, Domain]> = [];
    if (d.active && !d.disposed) {
      if (d.switches < bounds.switches) next.push(['switch', { ...d, switches: d.switches + 1 }]);
      if (d.toggles < bounds.pauseToggles) next.push(['pause', { ...d, paused: !d.paused, toggles: d.toggles + 1 }]);
      if (d.visibility < bounds.visibilityChanges) next.push(['hide', { ...d, active: false, visibility: d.visibility + 1 }]);
    }
    if (!d.active && !d.disposed && d.visibility < bounds.visibilityChanges) next.push(['show', { ...d, active: true, visibility: d.visibility + 1 }]);
    if (!d.disposed) next.push(['dispose', { ...d, active: false, disposed: true }]);
    if (d.replies < bounds.replies) for (const action of ['ok', 'error'] as const) next.push([action, { ...d, replies: d.replies + 1 }]);
    if (d.ticks < bounds.ticks) next.push(['tick', { ...d, ticks: d.ticks + 1 }]);
    for (const [action, state] of next) visit([...trace, action], state);
  }
  visit([], { active: true, paused: false, disposed: false, switches: 0, toggles: 0, visibility: 0, replies: 0, ticks: 0 });
  return out;
}

type Request = { id: string; scope: string; epoch: number; delivered: boolean; processed: boolean; resolve: (value: Record<string, unknown>) => void };
type Oracle = { scope: string; epoch: number; active: boolean; paused: boolean; disposed: boolean; result: 'ready' | 'unavailable' | null };

it('checks bounded actual-code schedules against graph scope ownership', async () => {
  vi.useFakeTimers();
  const report = { kind: 'bounded generated schedules against actual code; not a theorem or refinement proof', bounds,
    words: 0, completedSchedules: 0, prunedCompletionsBeforeIssuance: 0, observations: 0, issued: 0,
    currentCompletions: 0, currentSuccessfulCompletions: 0, staleCompletions: 0, acceptedGraphs: 0, maxIssuedPerSchedule: 0, acceptedProjects: [] as string[],
    status: 'running', failureTrace: [] as Action[], failures: [] as string[] };
  const projects = new Set<string>();
  const all = schedules(); report.words = all.length;
  let currentTrace: Action[] = [];
  try {
    for (const trace of all) {
      currentTrace = trace; vi.clearAllTimers(); vi.setSystemTime(0);
      document.body.innerHTML = settingsMarkup();
      const oracle: Oracle = { scope: 'A', epoch: 0, active: true, paused: false, disposed: false, result: null };
      const requests: Request[] = [], failures: string[] = [];
      let graph: KnowledgeGraph = { nodes: [], edges: [] };
      const drilldown = new KnowledgeDrilldown(graph);
      const port = {
        get currentGraph() { return graph; }, get currentView() { return drilldown.current(); },
        get navigationTargets() { return { camera: [0, 0, 5], lookAt: [0, 0, 0] }; },
        replaceGraph(next: KnowledgeGraph) {
          for (const node of next.nodes) {
            const request = requests.find(item => item.id === node.id);
            // Do not throw here: the product intentionally catches renderer/read
            // errors. Assert the recorded violation outside that catch boundary.
            if (!request || request.scope !== oracle.scope || request.epoch !== oracle.epoch
              || !oracle.active || oracle.paused || oracle.disposed) failures.push('scope ownership violated by graph replacement');
          }
          if (next.nodes.length) { report.acceptedGraphs++; projects.add(oracle.scope); }
          graph = next; drilldown.replaceGraph(next);
        },
        clickNode() { return drilldown.current(); }, showNodeActivity() {}, clearNodeActivity() {},
      };
      const load: typeof fetchSettingsAction = async (action, input = {}) => {
        if (action === 'collection-projects') return { ok: true, projects: [{ project: 'A' }, { project: 'B' }] };
        if (action === 'knowledge-graph') return { ok: true, nodes: [], edges: [] };
        if (action !== 'collection-graph') return { ok: false };
        const query = JSON.parse(input.query ?? '{}') as Record<string, unknown>;
        if (query.events) return { ok: false };
        const scope = Array.isArray(query.projects) ? String(query.projects[0]) : 'all';
        if (scope !== oracle.scope || !oracle.active || oracle.paused || oracle.disposed) failures.push('scope ownership violated by request issuance');
        report.issued++;
        return new Promise<Record<string, unknown>>(resolve => requests.push({ id: `request-${requests.length + 1}`, scope,
          epoch: oracle.epoch, delivered: false, processed: false, resolve }));
      };
      const controller = new CollectionGraphController(load, port);
      const microtasks = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };
      const pump = async (ms = 0) => { await microtasks(); await vi.advanceTimersByTimeAsync(ms); await microtasks(); };
      const select = () => {
        const source = document.getElementById('knowledge-project') as HTMLSelectElement;
        source.value = 'project:' + oracle.scope; source.dispatchEvent(new Event('change'));
      };
      const check = () => {
        report.observations++;
        for (const node of port.currentView.nodes) {
          if (requests.find(item => item.id === node.id)?.scope !== oracle.scope) failures.push('scope ownership violated by visible node');
        }
        const state = document.getElementById('knowledge-map-status')!.dataset.state;
        const terminal = ['ready', 'empty', 'partial', 'stale', 'unavailable'].includes(state ?? '');
        if (terminal && oracle.result === null) failures.push('scope ownership violated by an unconfirmed result state');
        if (state === 'ready' && !port.currentView.nodes.length) failures.push('scope ownership violated by ready without graph');
        if (state === 'loading' && (!oracle.active || oracle.paused || oracle.disposed
          || !requests.some(item => item.epoch === oracle.epoch && !item.processed))) failures.push('scope ownership violated by loading without a current read');
        expect(failures, `scope ownership violated; trace=${JSON.stringify(trace)}`).toEqual([]);
      };
      let complete = true;
      try {
        controller.start(); await microtasks(); select(); await pump();
        expect(requests.length, 'non-vacuity: the real controller must issue the first project read').toBe(1);
        check();
        for (const action of trace) {
          let delivered: Request | undefined;
          if (action === 'switch') {
            oracle.scope = oracle.scope === 'A' ? 'B' : 'A'; oracle.epoch++; oracle.result = null; select();
          } else if (action === 'pause') {
            oracle.paused = !oracle.paused; oracle.epoch++; document.getElementById('knowledge-live-toggle')!.click();
          } else if (action === 'hide') {
            oracle.active = false; oracle.epoch++; controller.stop();
          } else if (action === 'show') {
            oracle.active = true; oracle.epoch++; controller.start();
          } else if (action === 'dispose') {
            oracle.disposed = true; oracle.active = false; oracle.epoch++; controller.dispose();
          } else if (action === 'ok' || action === 'error') {
            delivered = requests.find(item => !item.delivered);
            if (!delivered) { report.prunedCompletionsBeforeIssuance++; complete = false; break; }
            delivered.delivered = true;
            const current = delivered.epoch === oracle.epoch && delivered.scope === oracle.scope
              && oracle.active && !oracle.paused && !oracle.disposed;
            if (current) { report.currentCompletions++; if (action === 'ok') report.currentSuccessfulCompletions++; oracle.result = action === 'ok' ? 'ready' : 'unavailable'; }
            else report.staleCompletions++;
            delivered.resolve(action === 'error' ? { ok: false } : { ok: true,
              nodes: [{ id: delivered.id, label: delivered.id, source_version: 'v-' + delivered.id, source_target: 'target-' + delivered.scope }], edges: [] });
          }
          check(); // Event-handler return, before scheduled reads/microtasks.
          await pump(action === 'tick' ? bounds.tickMs : 0);
          if (delivered) delivered.processed = true;
          check();
        }
        if (complete) report.completedSchedules++;
        report.maxIssuedPerSchedule = Math.max(report.maxIssuedPerSchedule, requests.length);
      } finally { controller.dispose(); vi.clearAllTimers(); }
    }
    expect(report.acceptedGraphs).toBeGreaterThan(0);
    expect(report.currentCompletions).toBeGreaterThan(0);
    expect(report.staleCompletions).toBeGreaterThan(0);
    expect(report.acceptedGraphs, 'non-vacuity: current successful replies must be visibly applied').toBe(report.currentSuccessfulCompletions);
    expect([...projects].sort()).toEqual(['A', 'B']);
    report.status = 'pass';
  } catch (error) {
    report.status = 'fail'; report.failureTrace = currentTrace;
    report.failures.push(error instanceof Error ? error.message : String(error)); throw error;
  } finally {
    report.acceptedProjects = [...projects].sort();
    if (process.env.ALDEN_SCOPE_REPORT) writeFileSync(process.env.ALDEN_SCOPE_REPORT, JSON.stringify(report, null, 2) + '\n');
    vi.useRealTimers();
  }
}, 120000);
