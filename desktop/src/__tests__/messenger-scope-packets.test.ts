// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import packets from './fixtures/messenger-scope-packets.json';
import { wireCollectionHistory } from '../collection-history';
import { CollectionActivity } from '../knowledge/collection-activity';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { KnowledgeTerminal, terminalMarkup } from '../knowledge/terminal';
import type { fetchSettingsAction } from '../runtime';

const cleanup: Array<() => void> = [];
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => {
  cleanup.splice(0).forEach(dispose => dispose());
  document.body.replaceChildren(); vi.useRealTimers();
});

// These packets came from candidate Python + isolated SQLite with controlled
// sources, not handwritten API responses or a live private messenger database.
describe('messenger source authority across backend packets and UI consumers', () => {
  it('discards expanded cached history when the actual backend scope is revoked', async () => {
    const data = packets.revocation;
    document.body.innerHTML = '<div id="settings-page-history"></div>';
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(data.before_history).mockResolvedValue(data.after_history);
    const history = wireCollectionHistory(load); cleanup.push(() => history.dispose()); history.select('history');
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelectorAll('.collection-event-row').length).toBeGreaterThan(0);
    const detail = document.querySelector<HTMLDetailsElement>('.collection-event-detail')!;
    detail.open = true; detail.dispatchEvent(new Event('toggle'));
    await vi.advanceTimersByTimeAsync(2500);
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).toMatchObject({
      after: data.before_history.cursor, stream_id: data.before_history.stream_id,
    });
    expect(document.querySelectorAll('.collection-event-row')).toHaveLength(0);
    expect(document.querySelectorAll('.collection-event-detail')).toHaveLength(0);
    expect(document.querySelector('.collection-summary')!.textContent).toContain('0개 단계 기록');
  });

  it('clears paused terminal history and pending follow rows on scope change', async () => {
    const data = packets.revocation;
    document.body.innerHTML = terminalMarkup();
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(data.before_history).mockResolvedValueOnce(data.before_history)
      .mockResolvedValue(data.after_history);
    const select = vi.fn(), terminal = new KnowledgeTerminal(document, load, select);
    cleanup.push(() => terminal.dispose()); terminal.visible(true);
    terminal.update(parseKnowledgeGraph(data.before_graph), null);
    await terminal.refreshHistory({}, true);
    expect(document.querySelectorAll('#terminal-tail .terminal-row').length).toBeGreaterThan(0);
    document.getElementById('terminal-follow')!.click();
    await terminal.refreshHistory({}, true); // A paused reader now has pending rows.
    expect(document.getElementById('terminal-follow')!.textContent).toBe('추적 꺼짐');
    await terminal.refreshHistory({}, true);
    expect(document.querySelectorAll('#terminal-tail .terminal-row')).toHaveLength(0);
    document.getElementById('terminal-follow')!.click();
    expect(document.querySelectorAll('#terminal-tail .terminal-row')).toHaveLength(0);
    expect(select).not.toHaveBeenCalled();
  });

  it('refreshes revoked graph activity without replaying old knowledge as combat', async () => {
    const data = packets.revocation, refresh = vi.fn(), show = vi.fn(), clear = vi.fn();
    const read = vi.fn(async () => data.after_activity);
    const activity = new CollectionActivity(read, refresh, show, clear);
    cleanup.push(() => activity.stop()); activity.start();
    activity.snapshot(data.before_graph.activity_checkpoint, parseKnowledgeGraph(data.before_graph).nodes);
    clear.mockClear(); await vi.advanceTimersByTimeAsync(2500);
    expect(refresh).toHaveBeenCalledTimes(1); expect(clear).toHaveBeenCalledTimes(1);
    expect(show).not.toHaveBeenCalled(); expect(activity.diagnostics().pending).toBe(0);
    activity.snapshot(data.after_graph.activity_checkpoint, parseKnowledgeGraph(data.after_graph).nodes);
    expect(show).not.toHaveBeenCalled();
  });

  it('plays a real new-knowledge receipt once, only after the matching graph version arrives', async () => {
    const data = packets.new_knowledge, event = data.after_activity.items[0];
    expect(event).toBeDefined(); expect(event.kind).toBe('added');
    expect(data.after_activity.reset).toBe(false);
    expect(data.after_activity.stream_id).toBe(data.before_activity.stream_id);
    vi.setSystemTime(event.at * 1000 - 1);
    const refresh = vi.fn(), show = vi.fn(), clear = vi.fn();
    const activity = new CollectionActivity(vi.fn(async () => data.after_activity), refresh, show, clear);
    cleanup.push(() => activity.stop()); activity.start();
    activity.snapshot(data.before_graph.activity_checkpoint, parseKnowledgeGraph(data.before_graph).nodes);
    await vi.advanceTimersByTimeAsync(2500);
    expect(refresh).toHaveBeenCalledTimes(1); expect(show).not.toHaveBeenCalled();
    expect(activity.diagnostics().pending).toBe(1);
    activity.snapshot(data.after_graph.activity_checkpoint, parseKnowledgeGraph(data.after_graph).nodes);
    expect(show).toHaveBeenCalledExactlyOnceWith(event);
    activity.snapshot(data.after_graph.activity_checkpoint, parseKnowledgeGraph(data.after_graph).nodes);
    expect(show).toHaveBeenCalledTimes(1);
  });
});
