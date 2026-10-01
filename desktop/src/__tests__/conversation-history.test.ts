// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { wireConversationViews } from '../conversations';
import { VirtualList } from '../virtual-list';
import type { fetchSettingsAction } from '../runtime';
beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe('complete local histories', () => {
  it('discards a late room response and reads the new room with string IDs', async () => {
    let release!: (data: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action === 'history-rooms') return { ok: true, rooms: [{ chat_id: '9007199254740997', chat_name: 'A' }, { chat_id: '2', chat_name: 'B' }] };
      if (action === 'history-messages' && input.chatId !== '2') return new Promise(resolve => { release = resolve; });
      return { ok: true, anchor_log_id: '99', next_before: null, total: 1, messages: [{ id: '99', text: '<script>bad</script>\n' + '긴 원문'.repeat(10000), sender: 'B' }] };
    });
    const views = wireConversationViews(load); views.select('conversation'); await settle();
    document.querySelector<HTMLButtonElement>('#chat-room-list button')!.click();
    document.querySelectorAll<HTMLButtonElement>('#chat-room-list button')[1].click();
    release({ ok: true, anchor_log_id: '1', total: 1, messages: [{ id: '1', text: 'OLD_ROOM' }] }); await settle();
    expect(document.getElementById('chat-message-list')!.textContent).not.toContain('OLD_ROOM');
    expect(document.querySelector('.message-text')!.textContent!.length).toBeGreaterThan(40000);
    expect(document.querySelector('#chat-message-list script')).toBeNull();
    expect(load.mock.calls.find(([action]) => action === 'history-messages')?.[1]?.chatId).toBe('9007199254740997'); views.dispose();
  });
  it('retains older DB receipts across current-state polling and stops when hidden', async () => {
    vi.useFakeTimers();
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action !== 'db-sync-history') return null;
      const older = JSON.parse(input.query!).before;
      return { ok: true, current: { phase: 'pending' }, next: older ? null : 2, items: [{ id: older ? 1 : 3, phase: older ? 'collecting' : 'pending', details: older ? { messages: 2034253 } : {} }] };
    });
    const views = wireConversationViews(load); views.select('history'); await vi.advanceTimersByTimeAsync(0);
    document.getElementById('db-history-older')!.click(); await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5000); expect(document.querySelectorAll('.db-cycle-row')).toHaveLength(2);
    expect(document.getElementById('db-cycle-list')!.textContent).toContain('2,034,253');
    expect(document.getElementById('db-cycle-list')!.textContent).toContain('대기');
    views.visible(false); const calls = load.mock.calls.length; await vi.advanceTimersByTimeAsync(20000); expect(load).toHaveBeenCalledTimes(calls);
    views.dispose(); vi.useRealTimers();
  });
  it('keeps a bounded DOM while all loaded message text remains addressable', () => {
    const host = document.createElement('div'); document.body.append(host);
    const list = new VirtualList<{id:number;text:string}>(host, row => String(row.id), row => { const node = document.createElement('p'); node.textContent = row.text; return node; });
    const messages = Array.from({ length: 10000 }, (_, id) => ({ id, text: `message ${id}` }));
    list.set(messages); expect(host.querySelectorAll('p').length).toBeLessThanOrEqual(80);
    host.scrollTop = 9999 * 78; host.dispatchEvent(new Event('scroll')); list.set(messages, { end: true });
    expect(host.textContent).toContain('message 9999'); expect(host.querySelectorAll('p').length).toBeLessThanOrEqual(80); list.dispose();
  });
});
