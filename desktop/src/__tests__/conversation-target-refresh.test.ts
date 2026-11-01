// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { wireConversationViews } from '../conversations';
import { wireVoiceStart } from '../voice-controls';
import type { fetchSettingsAction } from '../runtime';

const first = '11111111111111111111111111111111';
const second = '22222222222222222222222222222222';
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
afterEach(() => { document.body.replaceChildren(); });

describe('confirmed voice catalog and input target ownership', () => {
  it('retries an incomplete initial catalog without claiming no conversations exist', async () => {
    let reads = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async action => action === 'voice-history-sessions'
      ? ++reads === 1 ? { ok: true, items: [], partial: true }
        : { ok: true, items: [{ id: second, title: 'RETRIED' }] }
      : { ok: true, items: [{ turn_id: 1, role: 'user', content: 'RETRIED CONTENT' }], next: null });
    const views = wireConversationViews(load);
    try {
      views.select('voice'); await flush();
      expect(document.getElementById('voice-history-status')!.textContent).toContain('모두 불러오지 못했습니다');
      expect(document.getElementById('voice-retry')!.hidden).toBe(false);
      document.getElementById('voice-retry')!.click(); await flush(); await flush();
      expect(document.getElementById('voice-start')!.dataset.conversationId).toBe(second);
      expect(document.getElementById('voice-history-title')!.textContent).toBe('RETRIED');
      expect(document.getElementById('voice-message-list')!.textContent).toContain('RETRIED CONTENT');
    } finally { views.dispose(); }
  });

  it('replaces a missing selection with a current catalog session and does not restart its old input context', async () => {
    let current = first;
    const load = vi.fn<typeof fetchSettingsAction>(async (action, args) => action === 'voice-history-sessions'
      ? { ok: true, items: [{ id: current, title: current === first ? 'OLD' : 'NEW' }] }
      : { ok: true, items: [{ turn_id: 1, role: 'user', content: args?.chatId === first ? 'OLD TEXT' : 'NEW TEXT' }], next: null });
    const views = wireConversationViews(load);
    const invoke = vi.fn().mockResolvedValue(undefined);
    wireVoiceStart(document, invoke);
    try {
      views.select('voice'); await flush(); await flush();
      expect(document.getElementById('voice-message-list')!.textContent).toContain('OLD TEXT');
      current = second; views.select('voice'); await flush(); await flush();
      const button = document.getElementById('voice-start') as HTMLButtonElement;
      expect(button.dataset.conversationId).toBe(second);
      expect(document.getElementById('voice-history-title')!.textContent).toBe('NEW');
      expect(document.getElementById('voice-message-list')!.textContent).toContain('NEW TEXT');
      expect(document.getElementById('voice-message-list')!.textContent).not.toContain('OLD TEXT');
      button.click(); await flush();
      expect(invoke).toHaveBeenCalledWith('start_manual_voice_session', { conversationId: second });
    } finally { views.dispose(); }
  });

  it('clears a removed input target on a confirmed empty catalog and creates a fresh session on start', async () => {
    let empty = false;
    const load = vi.fn<typeof fetchSettingsAction>(async action => action === 'voice-history-sessions'
      ? { ok: true, items: empty ? [] : [{ id: first, title: 'OLD' }] }
      : { ok: true, items: [{ turn_id: 1, role: 'user', content: 'OLD TEXT' }], next: null });
    const views = wireConversationViews(load);
    const invoke = vi.fn().mockResolvedValue(undefined);
    wireVoiceStart(document, invoke);
    try {
      views.select('voice'); await flush(); await flush();
      empty = true; views.select('voice'); await flush();
      const button = document.getElementById('voice-start') as HTMLButtonElement;
      expect(button.dataset.conversationId).toBeUndefined();
      expect(document.getElementById('voice-message-list')!.textContent).not.toContain('OLD TEXT');
      expect(document.getElementById('voice-history-title')!.textContent).not.toBe('OLD');
      button.click(); await flush();
      expect(invoke).toHaveBeenCalledWith('start_manual_voice_session');
    } finally { views.dispose(); }
  });
});
