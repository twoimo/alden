// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeTerminal, terminalMarkup, observationMarkup } from '../knowledge/terminal';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import type { fetchSettingsAction } from '../runtime';

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
