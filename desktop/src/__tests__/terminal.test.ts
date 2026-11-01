// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeTerminal, terminalMarkup, observationMarkup } from '../knowledge/terminal';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import type { fetchSettingsAction } from '../runtime';
import { trendWindow } from '../knowledge/terminal-trend';

let terminal: KnowledgeTerminal;
const event = (sequence: number, label = '원문 자료') => ({ sequence, document_id: 'a', document_label: label, stage: 'stored', at: 1792450800, details: { change: 'added' } });
function setup(load: typeof fetchSettingsAction) {
  const select = vi.fn(); terminal = new KnowledgeTerminal(document, load, select); terminal.visible(true);
  terminal.update(parseKnowledgeGraph({ nodes: [{ id: 'a', label: '선택 지식', category: '주제' }, { id: 'b', label: '연결 지식', category: '자료' }],
    edges: [{ source: 'a', target: 'b', relation: 'supports' }] }), 'a');
  return select;
}
beforeEach(() => { document.body.innerHTML = '<button id="knowledge-terminal-toggle" aria-expanded="true"></button>' + observationMarkup() + terminalMarkup(); });
afterEach(() => terminal?.dispose());
describe('knowledge terminal lifecycle and evidence', () => {
  it.each(['resolve','reject'])('ends a stopped initial history request without accepting its late %s result', async outcome => {
    let finish!: (value:Record<string,unknown>)=>void, fail!: (reason:Error)=>void;
    const load=vi.fn<typeof fetchSettingsAction>(()=>new Promise((resolve,reject)=>{finish=resolve;fail=reject;}));
    setup(load);const pending=terminal.refreshHistory({},true);
    expect(document.getElementById('terminal-ingest')!.textContent).toContain('불러옵니다');
    terminal.visible(false);
    expect(document.getElementById('terminal-ingest')!.textContent).toContain('중지되어 있습니다');
    if(outcome==='resolve')finish({ok:true,items:[event(1,'늦은 자료')]});else fail(new Error('late'));
    await pending;
    expect(document.getElementById('terminal-ingest')!.textContent).not.toContain('불러옵니다');
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('늦은 자료');
    load.mockResolvedValueOnce({ok:true,items:[event(2,'재개 후 자료')]});
    terminal.visible(true);await terminal.refreshHistory({},true);
    expect(document.getElementById('terminal-ingest')!.textContent).toContain('재개 후 자료');
  });

  it('only claims loading while a history request is actually in flight', async () => {
    let resolve!: (value: Record<string, unknown>) => void;
    setup(vi.fn<typeof fetchSettingsAction>(() => new Promise(r => { resolve = r; })));
    expect(document.getElementById('terminal-ingest')!.textContent).toContain('아직 확인하지 않았습니다');
    const pending = terminal.refreshHistory({}, true);
    expect(document.getElementById('terminal-ingest')!.textContent).toContain('불러옵니다');
    resolve({ ok:false }); await pending;
    expect(document.getElementById('terminal-ingest')!.textContent).toContain('확인할 수 없습니다');
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('불러옵니다');
  });

  it('labels a readiness receipt without calling it another stored document', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => ({ ok: true, items: [{ ...event(1), stage: 'published' }] }));
    setup(load); await terminal.refreshHistory({}, true);
    expect(document.getElementById('terminal-tail')!.textContent).toContain('갱신 준비');
  });
  it('opens a history row only through the same displayed document/version/target', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => ({ ok: true, items: [{ ...event(1), version: 'v1', target_id: 'target-a' }] }));
    const select = setup(load);
    terminal.update(parseKnowledgeGraph({ nodes: [{ id: 'a', label: '현재 원문', source_version: 'v1', source_target: 'target-a' }], edges: [] }), null);
    await terminal.refreshHistory({}, true);
    for (const id of ['terminal-ingest', 'terminal-tail']) {
      const button = document.querySelector<HTMLButtonElement>(`#${id} button[data-terminal-node="a"]`)!;
      expect(button).not.toBeNull(); button.click();
    }
    expect(select.mock.calls).toEqual([['a'], ['a']]);
    terminal.update(parseKnowledgeGraph({ nodes: [{ id: 'a', label: '변경된 원문', source_version: 'v2', source_target: 'target-a' }], edges: [] }), null);
    expect(document.querySelectorAll('#terminal-ingest button, #terminal-tail button')).toHaveLength(0);
    expect(document.getElementById('terminal-tail')!.textContent).toContain('원문 자료');
    expect(select).toHaveBeenCalledTimes(2);
  });
  it.each([{ version: 'v2' }, { target_id: 'target-b' }, { version: undefined }, { target_id: undefined }, { document_id: 'outside' }])('keeps unresolved history visible without offering a misleading current-source action: %j', async delta => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => ({ ok: true, items: [{ ...event(1), version: 'v1', target_id: 'target-a', ...delta }] }));
    const select = setup(load);
    terminal.update(parseKnowledgeGraph({ nodes: [{ id: 'a', label: '현재 원문', source_version: 'v1', source_target: 'target-a' }], edges: [] }), null);
    await terminal.refreshHistory({}, true);
    expect(document.querySelectorAll('#terminal-ingest button, #terminal-tail button')).toHaveLength(0);
    expect(document.getElementById('terminal-tail')!.textContent).toContain('원문 자료');
    document.querySelector<HTMLElement>('#terminal-tail .terminal-row')!.click();
    expect(select).not.toHaveBeenCalled();
  });
  it('draws actual within-type connections instead of an empty chord panel', () => {
    setup(vi.fn());
    terminal.update(parseKnowledgeGraph({ nodes: [{ id: 'a', label: '지원 준비', category: 'topic' },
      { id: 'b', label: '면접', category: 'topic' }], edges: [{ source: 'a', target: 'b', relation: 'related-to' }] }), null);
    expect(document.querySelector('#terminal-chord path[data-route="within-type"]')).not.toBeNull();
    expect(document.getElementById('terminal-chord')!.textContent).toContain('주제 내부');
    expect(document.getElementById('terminal-chord')!.textContent).not.toContain('연결이 없습니다');
  });
  it('renders source labels as text and opens only an actually displayed linked node', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => ({ ok: true, items: [event(1, '<img src=x onerror=alert(1)>')] }));
    const select = setup(load); await terminal.refreshHistory({}, true);
    expect(document.querySelector('img')).toBeNull();
    expect(document.getElementById('terminal-tail')!.textContent).toContain('<img src=x');
    document.querySelector<HTMLButtonElement>('#terminal-linked button')!.click();
    expect(select).toHaveBeenCalledWith('b');
    expect(document.getElementById('terminal-map-count')!.textContent).toContain('2 지식 / 1 연결');
    expect(load).toHaveBeenCalledTimes(1);
  });
  it('fences hidden requests and owns no background timer', async () => {
    vi.useFakeTimers();
    let resolve!: (value: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>(() => new Promise(r => { resolve = r; }));
    setup(load); const pending = terminal.refreshHistory({}, true);
    terminal.visible(false); resolve({ ok: true, items: [event(1)] }); await pending;
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('원문 자료');
    await vi.advanceTimersByTimeAsync(60000); expect(load).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0); vi.useRealTimers();
  });
  it('freezes the reader until follow is resumed, deduplicates records and respects a node-type slice', async () => {
    let next = 1;
    const load = vi.fn<typeof fetchSettingsAction>(async () => ({ ok: true, items: [event(next, '자료 ' + next), event(next),
      { ...event(next + 50, '다른 범위'), document_id: 'outside' }] }));
    setup(load); await terminal.refreshHistory({ node_type: '주제' }, true);
    document.getElementById('terminal-follow')!.click(); next = 2;
    await terminal.refreshHistory({ node_type: '주제' }, true);
    expect(document.getElementById('terminal-tail')!.textContent).toContain('자료 1');
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('자료 2');
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('다른 범위');
    expect(document.getElementById('terminal-follow')!.textContent).toBe('새 기록 1');
    document.getElementById('terminal-follow')!.click();
    expect(document.querySelectorAll('#terminal-tail .terminal-row')).toHaveLength(1);
    expect(document.getElementById('terminal-tail')!.textContent).toContain('자료 2');
    document.getElementById('knowledge-terminal-toggle')!.click();
    expect(document.getElementById('knowledge-terminal')!.hidden).toBe(true);
  });
  it.each(['denied', 'malformed', 'rejected'] as const)('does not restore pre-failure pending records after %s history', async failure => {
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce({ ok: true, stream_id: 's1', items: [event(1, '기존 기록')] })
      .mockResolvedValueOnce({ ok: true, stream_id: 's1', items: [event(2, '실패 전 대기 기록')] });
    if (failure === 'rejected') load.mockRejectedValueOnce(new Error('unavailable'));
    else load.mockResolvedValueOnce(failure === 'denied' ? { ok: false } : { ok: true, items: null });
    load.mockResolvedValueOnce({ ok: true, stream_id: 's2', items: [event(3, '새로 확인한 기록')] });
    setup(load); await terminal.refreshHistory({}, true);
    document.getElementById('terminal-follow')!.click();
    await terminal.refreshHistory({}, true);
    expect(document.getElementById('terminal-follow')!.textContent).toBe('새 기록 1');
    await terminal.refreshHistory({}, true);
    expect(document.getElementById('terminal-follow')!.textContent).toBe('추적 꺼짐');
    document.getElementById('terminal-follow')!.click();
    for (const id of ['terminal-ingest', 'terminal-tail']) {
      const host = document.getElementById(id)!;
      expect(host.textContent).toContain('수집 이력을 확인할 수 없습니다.');
      expect(host.querySelectorAll('.terminal-row')).toHaveLength(0);
      expect(host.textContent).not.toContain('실패 전 대기 기록');
    }
    await terminal.refreshHistory({}, true);
    expect(document.getElementById('terminal-tail')!.textContent).toContain('새로 확인한 기록');
    expect(document.getElementById('terminal-tail')!.textContent).not.toContain('확인할 수 없습니다');
  });
  it('fences a collection reply after switching to conversation memory with the same query', async () => {
    let resolve!: (value: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>(() => new Promise(r => { resolve = r; }));
    setup(load); const pending = terminal.refreshHistory({}, true);
    await terminal.refreshHistory({}, false);
    resolve({ ok: true, stream_id: 's1', items: [event(1, '이전 수집 범위의 기록')] });
    await pending;
    document.getElementById('terminal-follow')!.click();
    document.getElementById('terminal-follow')!.click();
    for (const id of ['terminal-ingest', 'terminal-tail']) {
      const host = document.getElementById(id)!;
      expect(host.textContent).toContain('대화 기억에는 수집 처리 이력이 없습니다.');
      expect(host.querySelectorAll('.terminal-row')).toHaveLength(0);
    }
  });
});

// ST03: graph timestamps do not attest intake/change events.
describe('f(x) authoritative history contract', () => {
  it('never fabricates an intake graph from the displayed node timestamp', () => {
    setup(vi.fn());
    terminal.update(parseKnowledgeGraph({ nodes: [{ id: 'a', label: 'Visible slice', updated_at: Date.now() / 1000 }], edges: [] }), null);
    expect(document.querySelector('#terminal-trend .terminal-bars')).toBeNull();
    expect(document.getElementById('terminal-trend')!.textContent).toContain('미집계');
  });
});

function aggregate(count: number, range = trendWindow({})!, stream = 's1', extra: Record<string, unknown> = {}) {
  const days = [];
  for (let at = Math.floor(range.since / 86400) * 86400; at < range.until; at += 86400) {
    days.push({ day: new Date(at * 1000).toISOString().slice(0, 10), added: days.length ? 0 : count, revised: 0, removed: 0 });
  }
  return { ok: true, stream_id: stream, cursor: 1000, snapshot_checkpoint: { stream_id: stream, cursor: 1000 }, items: [event(count + 1)], trend: {
    schema: 1, basis: 'stored_receipt_utc', window: range, stream_id: stream, cursor: 1000,
    scan: 'complete', coverage: 'retained_only', source_coverage: 'unknown', backfill_live: 'unknown',
    relations: null, unclassified: 0, observed_at: Date.now() / 1000, days,
  }, ...extra };
}
const trendText = () => document.getElementById('terminal-trend')!.textContent!;
describe('f(x) scope, follow and lifecycle', () => {
  it('renders full receipt-window counts independently of 80-row tail and visible graph updates', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => aggregate(101));
    setup(load); await terminal.refreshHistory({ projects: ['one'], since: trendWindow({})!.since }, true);
    expect(trendText()).toContain('저장 영수증 101건');
    expect(document.querySelectorAll('#terminal-trend .terminal-bars > span')).toHaveLength(14);
    expect(load.mock.calls).toHaveLength(1);
    const query = JSON.parse(load.mock.calls[0][1]!.query!);
    expect(query.limit).toBe(80); expect(query.projects).toEqual(['one']); expect(query.trend).toEqual(trendWindow({}));
    terminal.update(parseKnowledgeGraph({ nodes: [], edges: [] }), null);
    expect(trendText()).toContain('저장 영수증 101건');
    expect(trendText()).toContain('보존된 이력만'); expect(trendText()).toContain('과거 일괄 수집/실시간 구분 미지원');
    expect(trendText()).toContain('확인 시각 이후 미집계');
  });
  it('shows missing/empty history as unaggregated without a fabricated zero chart', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(aggregate(0)).mockResolvedValueOnce({ ok: true, items: [] });
    setup(load); await terminal.refreshHistory({}, true);
    expect(trendText()).toContain('실제 유입·변경량 미집계');
    expect(document.querySelector('#terminal-trend .terminal-bars')).toBeNull();
    await terminal.refreshHistory({}, true);
    expect(trendText()).toContain('미집계'); expect(trendText()).not.toContain('저장 영수증 0건');
  });
  it.each(['reject', 'denied', 'malformed', 'partial'])('clears previous and pending totals on %s', async outcome => {
    let next = aggregate(7);
    const load = vi.fn<typeof fetchSettingsAction>(async () => next);
    setup(load); await terminal.refreshHistory({}, true);
    document.getElementById('terminal-follow')!.click(); next = aggregate(9); await terminal.refreshHistory({}, true);
    if (outcome === 'reject') load.mockRejectedValueOnce(new Error('offline'));
    else if (outcome === 'denied') load.mockResolvedValueOnce({ ok: false });
    else if (outcome === 'malformed') load.mockResolvedValueOnce({ ...next, trend: { ...next.trend, days: [] } });
    else load.mockResolvedValueOnce({ ...next, trend: { ...next.trend, scan: 'partial' } });
    await terminal.refreshHistory({}, true); document.getElementById('terminal-follow')!.click();
    expect(trendText()).toContain('미집계'); expect(trendText()).not.toMatch(/저장 영수증 (7|9)건/);
  });
  it('preserves follow freeze, replaces snapshots on reconnect, and clears reset totals', async () => {
    let next = aggregate(7);
    const load = vi.fn<typeof fetchSettingsAction>(async () => next);
    setup(load); await terminal.refreshHistory({}, true);
    document.getElementById('terminal-follow')!.click(); next = aggregate(9); await terminal.refreshHistory({}, true);
    expect(trendText()).toContain('추적 중지'); expect(trendText()).toContain('저장 영수증 7건');
    document.getElementById('terminal-follow')!.click(); expect(trendText()).toContain('저장 영수증 9건');
    await terminal.refreshHistory({}, true); expect(trendText()).toContain('저장 영수증 9건');
    document.getElementById('terminal-follow')!.click(); next = aggregate(2, trendWindow({})!, 's2', { reset: true });
    await terminal.refreshHistory({}, true); expect(trendText()).toContain('저장 영수증 2건');
    expect(trendText()).not.toContain('저장 영수증 9건');
    terminal.reset(); expect(trendText()).toContain('미집계');
  });
  it('dispatches a new scope while old work is unresolved and rejects its late reply', async () => {
    let old!: (value: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(aggregate(7))
      .mockImplementationOnce(() => new Promise(resolve => { old = resolve; })).mockResolvedValueOnce(aggregate(2));
    setup(load); await terminal.refreshHistory({ projects: ['one'] }, true);
    const pending = terminal.refreshHistory({ projects: ['one'] }, true);
    await terminal.refreshHistory({ projects: ['two'] }, true);
    expect(trendText()).toContain('저장 영수증 2건'); expect(load).toHaveBeenCalledTimes(3);
    old(aggregate(99)); await pending;
    expect(trendText()).toContain('저장 영수증 2건'); expect(trendText()).not.toContain('99건');
  });
  it('deduplicates same-scope requests and fences paused and disposed responses', async () => {
    let finish!: (value: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(aggregate(7))
      .mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    setup(load); await terminal.refreshHistory({}, true);
    const pending = terminal.refreshHistory({}, true); await terminal.refreshHistory({}, true);
    expect(load).toHaveBeenCalledTimes(2);
    terminal.visible(false); expect(trendText()).toContain('갱신 중지'); finish(aggregate(9)); await pending;
    expect(trendText()).toContain('저장 영수증 7건');
    terminal.visible(true); const last = terminal.refreshHistory({}, true); terminal.dispose();
    finish(aggregate(99)); await last; expect(trendText()).not.toContain('99건');
  });
  it('clears aggregate scope for conversation memory and unsupported graph-only filters', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async () => aggregate(7));
    setup(load); await terminal.refreshHistory({}, true);
    await terminal.refreshHistory({}, false); expect(trendText()).toContain('미연결');
    await terminal.refreshHistory({ node_type: 'topic' }, true); expect(trendText()).toContain('미집계');
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).not.toHaveProperty('trend');
    await terminal.refreshHistory({ relation: 'supports' }, true); expect(trendText()).not.toContain('7건');
  });
  it('refreshes the whole UTC window across midnight with no timer or subscription', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      vi.setSystemTime('2026-10-22T23:59:59Z');
      const load = vi.fn<typeof fetchSettingsAction>(async (_action, options) => aggregate(7, JSON.parse(options!.query!).trend));
      setup(load); await terminal.refreshHistory({}, true);
      const first = JSON.parse(load.mock.calls[0][1]!.query!).trend;
      vi.setSystemTime('2026-10-23T00:00:00Z'); await terminal.refreshHistory({}, true);
      const second = JSON.parse(load.mock.calls[1][1]!.query!).trend;
      expect(second.since - first.since).toBe(86400); expect(second.until - first.until).toBe(86400);
      expect(document.querySelectorAll('#terminal-trend .terminal-bars > span')).toHaveLength(14);
    } finally { vi.useRealTimers(); }
  });
});
