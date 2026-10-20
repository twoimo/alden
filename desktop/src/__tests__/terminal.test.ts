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
});
