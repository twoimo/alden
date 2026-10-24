// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { wireConversationViews } from '../conversations';
import { settingsMarkup } from '../ui';
import { wireVoiceStart } from '../voice-controls';
import type { fetchSettingsAction } from '../runtime';

type Reply = Record<string, unknown> | null;
const oldId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const newId = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const flush = () => new Promise<void>(resolve => setTimeout(resolve, 0));
const sessions = (...items: Array<{ id: string; title: string }>): Reply => ({ ok: true, items });
const turns = (content: string): Reply => ({
  ok: true, next: null, items: [{ turn_id: 1, role: 'user', content }],
});

function deferred() {
  let resolve!: (value: Reply) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Reply>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const button = () => document.getElementById('voice-start') as HTMLButtonElement;
const voiceText = () => document.getElementById('voice-message-list')!.textContent ?? '';
const voiceTitle = () => document.getElementById('voice-history-title')!.textContent;

beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); });

describe('independent conversation target races using the real view wiring', () => {
  it.each(['resolve', 'reject'] as const)(
    'ignores a %s from the old message request after the selected session disappears',
    async outcome => {
      const pending = deferred();
      let catalogReads = 0;
      const load = vi.fn<typeof fetchSettingsAction>(async (action, args) => {
        if (action === 'voice-history-sessions') {
          return ++catalogReads === 1 ? sessions({ id: oldId, title: 'OLD SESSION' })
            : sessions({ id: newId, title: 'NEW SESSION' });
        }
        if (action === 'voice-history-messages') {
          return args?.chatId === oldId ? pending.promise : turns('NEW OWNED MESSAGE');
        }
        throw new Error(`Unexpected action: ${action}`);
      });
      const invoke = vi.fn().mockResolvedValue(undefined);
      const views = wireConversationViews(load);
      wireVoiceStart(document, invoke);
      try {
        views.select('voice');
        await flush();
        expect(load.mock.calls.some(([action, args]) => action === 'voice-history-messages'
          && args?.chatId === oldId)).toBe(true);

        views.select('voice');
        await flush();
        expect(button().dataset.conversationId).toBe(newId);
        expect(voiceTitle()).toBe('NEW SESSION');

        if (outcome === 'resolve') pending.resolve(turns('STALE OLD MESSAGE'));
        else pending.reject(new Error('old message reader disconnected'));
        await flush();
        await flush();

        expect(voiceText()).toContain('NEW OWNED MESSAGE');
        expect(voiceText()).not.toContain('STALE OLD MESSAGE');
        expect(button().dataset.conversationId).toBe(newId);
        expect(load.mock.calls.filter(([action, args]) => action === 'voice-history-messages'
          && args?.chatId === newId)).toHaveLength(1);
        button().click();
        await flush();
        expect(invoke).toHaveBeenCalledWith('start_manual_voice_session', { conversationId: newId });
      } finally { views.dispose(); }
    },
  );

  it('retires a selected target immediately on a complete empty catalog, even with messages pending', async () => {
    const pending = deferred();
    let catalogReads = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'voice-history-sessions') {
        return ++catalogReads === 1 ? sessions({ id: oldId, title: 'OLD SESSION' }) : sessions();
      }
      if (action === 'voice-history-messages') return pending.promise;
      throw new Error(`Unexpected action: ${action}`);
    });
    const invoke = vi.fn().mockResolvedValue(undefined);
    const views = wireConversationViews(load);
    wireVoiceStart(document, invoke);
    try {
      views.select('voice');
      await flush();
      views.select('voice');
      await flush();

      expect(button().dataset.conversationId).toBeUndefined();
      expect(voiceTitle()).not.toBe('OLD SESSION');
      expect(voiceText()).not.toContain('STALE OLD MESSAGE');
      pending.resolve(turns('STALE OLD MESSAGE'));
      await flush();
      expect(voiceText()).not.toContain('STALE OLD MESSAGE');
      expect(load.mock.calls.filter(([action]) => action === 'voice-history-messages')).toHaveLength(1);
      button().click();
      await flush();
      expect(invoke).toHaveBeenCalledWith('start_manual_voice_session');
    } finally { views.dispose(); }
  });

  it.each([
    ['failed response', { ok: false, items: [] }],
    ['null response', null],
    ['missing items', { ok: true }],
    ['object items', { ok: true, items: {} }],
    ['missing item ID', { ok: true, items: [{}] }],
    ['numeric item ID', { ok: true, items: [{ id: 7 }] }],
    ['paginated empty', { ok: true, items: [], next: 'cursor' }],
    ['partial empty', { ok: true, items: [], partial: true }],
    ['truncated empty', { ok: true, items: [], truncated: true }],
  ] satisfies Array<[string, Reply]>)('preserves the input owner on a %s catalog', async (_label, nextReply) => {
    let catalogReads = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'voice-history-sessions') {
        return ++catalogReads === 1 ? sessions({ id: oldId, title: 'CONFIRMED ORIGINAL' }) : nextReply;
      }
      if (action === 'voice-history-messages') return turns('ORIGINAL CONTENT');
      throw new Error(`Unexpected action: ${action}`);
    });
    const invoke = vi.fn().mockResolvedValue(undefined);
    const views = wireConversationViews(load);
    wireVoiceStart(document, invoke);
    try {
      views.select('voice');
      await flush();
      expect(voiceText()).toContain('ORIGINAL CONTENT');
      views.select('voice');
      await flush();
      expect(button().dataset.conversationId).toBe(oldId);
      expect(voiceTitle()).toBe('CONFIRMED ORIGINAL');
      expect(voiceText()).toContain('ORIGINAL CONTENT');
      button().click();
      await flush();
      expect(invoke).toHaveBeenCalledWith('start_manual_voice_session', { conversationId: oldId });
    } finally { views.dispose(); }
  });

  it('keeps a selected owner when a partial catalog only contains a different session', async () => {
    let catalogs = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'voice-history-sessions') {
        return ++catalogs === 1 ? sessions({ id: oldId, title: 'OWNED' })
          : { ok: true, items: [{ id: newId, title: 'PARTIAL ONLY' }], next: 42 };
      }
      if (action === 'voice-history-messages') return turns('ORIGINAL OWNER MESSAGE');
      throw new Error(`Unexpected action: ${action}`);
    });
    const views = wireConversationViews(load);
    try {
      views.select('voice');
      await flush();
      views.select('voice');
      await flush();
      expect(button().dataset.conversationId).toBe(oldId);
      expect(voiceTitle()).toBe('OWNED');
      expect(voiceText()).toContain('ORIGINAL OWNER MESSAGE');
      expect(document.getElementById('voice-session-list')!.textContent).toContain('PARTIAL ONLY');
    } finally { views.dispose(); }
  });

  it.each([
    ['next', { next: 'more' }],
    ['partial', { partial: true }],
    ['truncated', { truncated: true }],
  ] as const)('preserves visible history and rail when a %s catalog is empty but incomplete', async (_flag, incomplete) => {
    let catalogs = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'voice-history-sessions') {
        return ++catalogs === 1 ? sessions({ id: oldId, title: 'KNOWN HISTORY' })
          : { ok: true, items: [], ...incomplete };
      }
      if (action === 'voice-history-messages') return turns('KNOWN CONTENT');
      throw new Error(`Unexpected action: ${action}`);
    });
    const views = wireConversationViews(load);
    try {
      views.select('voice');
      await flush();
      views.select('voice');
      await flush();
      expect(button().dataset.conversationId).toBe(oldId);
      expect(voiceTitle()).toBe('KNOWN HISTORY');
      expect(voiceText()).toContain('KNOWN CONTENT');
      expect(document.getElementById('voice-history-status')!.textContent)
        .toBe('대화 목록을 모두 불러오지 못했습니다.');
      expect(document.getElementById('voice-session-list')!.textContent).toContain('KNOWN HISTORY');
      expect(document.getElementById('voice-placeholder')!.hidden).toBe(true);
      expect(document.getElementById('voice-message-list')!.hidden).toBe(false);
    } finally { views.dispose(); }
  });

  it.each(['late success', 'late failure'] as const)(
    'keeps the newest catalog and input owner after an out-of-order %s', async outcome => {
      const olderCatalog = deferred();
      let catalogs = 0;
      const load = vi.fn<typeof fetchSettingsAction>(async action => {
        if (action === 'voice-history-sessions') {
          if (++catalogs === 1) return sessions({ id: oldId, title: 'OLD' });
          if (catalogs === 2) return olderCatalog.promise;
          return sessions({ id: newId, title: 'NEW' });
        }
        if (action === 'voice-history-messages') return turns('VISIBLE CURRENT');
        throw new Error(`Unexpected action: ${action}`);
      });
      const views = wireConversationViews(load);
      try {
        views.select('voice');
        await flush();
        views.select('voice');
        views.select('voice');
        await flush();
        expect(button().dataset.conversationId).toBe(newId);
        expect(voiceTitle()).toBe('NEW');

        if (outcome === 'late success') olderCatalog.resolve(sessions({ id: oldId, title: 'OBSOLETE' }));
        else olderCatalog.reject(new Error('obsolete catalog failure'));
        await flush();
        expect(button().dataset.conversationId).toBe(newId);
        expect(voiceTitle()).toBe('NEW');
        expect(document.getElementById('voice-session-list')!.textContent).not.toContain('OBSOLETE');
        expect(document.getElementById('voice-placeholder')!.hidden).toBe(true);
        expect(voiceText()).toContain('VISIBLE CURRENT');
      } finally { views.dispose(); }
    },
  );

  it('drops a hidden old response and reads the correct target after page changes and restore', async () => {
    const pending = deferred();
    let catalogs = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async (action, args) => {
      if (action === 'voice-history-sessions') {
        return ++catalogs === 1 ? sessions({ id: oldId, title: 'OLD' })
          : sessions({ id: newId, title: 'RESTORED' });
      }
      if (action === 'voice-history-messages') {
        return args?.chatId === oldId ? pending.promise : turns('RESTORED MESSAGE');
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const views = wireConversationViews(load);
    try {
      views.select('voice');
      await flush();
      views.visible(false);
      views.select('settings');
      pending.resolve(turns('STALE HIDDEN MESSAGE'));
      await flush();
      expect(voiceText()).not.toContain('STALE HIDDEN MESSAGE');
      const beforeRestore = load.mock.calls.length;
      views.visible(true);
      await flush();
      expect(load).toHaveBeenCalledTimes(beforeRestore);
      views.select('voice');
      await flush();
      expect(button().dataset.conversationId).toBe(newId);
      expect(voiceTitle()).toBe('RESTORED');
      expect(voiceText()).toContain('RESTORED MESSAGE');
      expect(voiceText()).not.toContain('STALE HIDDEN MESSAGE');
    } finally { views.dispose(); }
  });

  it('does not use a catalog resolved while hidden to claim a session on page restoration', async () => {
    const staleCatalog = deferred();
    let catalogs = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'voice-history-sessions') {
        return ++catalogs === 1 ? staleCatalog.promise : sessions({ id: newId, title: 'REOPENED' });
      }
      if (action === 'voice-history-messages') return turns('REOPENED MESSAGE');
      throw new Error(`Unexpected action: ${action}`);
    });
    const views = wireConversationViews(load);
    try {
      views.select('voice');
      views.visible(false);
      views.select('settings');
      staleCatalog.resolve(sessions({ id: oldId, title: 'HIDDEN STALE' }));
      await flush();
      expect(button().dataset.conversationId).toBeUndefined();
      views.visible(true);
      views.select('voice');
      await flush();
      expect(button().dataset.conversationId).toBe(newId);
      expect(voiceTitle()).toBe('REOPENED');
      expect(voiceText()).toContain('REOPENED MESSAGE');
      expect(document.getElementById('voice-session-list')!.textContent).not.toContain('HIDDEN STALE');
    } finally { views.dispose(); }
  });

  it('retiring history preserves the active voice control state and its stop owner', async () => {
    let catalogs = 0;
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'voice-history-sessions') {
        return ++catalogs === 1 ? sessions({ id: oldId, title: 'OLD' }) : sessions();
      }
      if (action === 'voice-history-messages') return turns('OLD MESSAGE');
      throw new Error(`Unexpected action: ${action}`);
    });
    const invoke = vi.fn().mockResolvedValue(undefined);
    const views = wireConversationViews(load);
    wireVoiceStart(document, invoke);
    try {
      views.select('voice');
      await flush();
      button().click();
      await flush();
      expect(invoke).toHaveBeenCalledWith('start_manual_voice_session', { conversationId: oldId });
      expect(button().dataset.voiceActive).toBe('true');
      views.select('voice');
      await flush();
      expect(button().dataset.conversationId).toBeUndefined();
      expect(button().dataset.voiceActive).toBe('true');
      button().click();
      await flush();
      expect(invoke).toHaveBeenLastCalledWith('stop_manual_voice_session');
    } finally { views.dispose(); }
  });

  it.each(['catalog', 'messages'] as const)('does not apply a pending %s result after disposal', async pendingKind => {
    const pending = deferred();
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === 'voice-history-sessions') {
        return pendingKind === 'catalog' ? pending.promise : sessions({ id: oldId, title: 'SELECTED' });
      }
      if (action === 'voice-history-messages') return pending.promise;
      throw new Error(`Unexpected action: ${action}`);
    });
    const views = wireConversationViews(load);
    views.select('voice');
    await flush();
    const oldTitle = voiceTitle();
    views.dispose();
    const callsAtDispose = load.mock.calls.length;
    pending.resolve(pendingKind === 'catalog' ? sessions({ id: newId, title: 'TOO LATE' })
      : turns('TOO LATE MESSAGE'));
    await flush();
    expect(voiceTitle()).toBe(oldTitle);
    expect(voiceText()).not.toContain('TOO LATE');
    expect(load).toHaveBeenCalledTimes(callsAtDispose);
  });

  it('ignores a delayed chat room message after switching to a different room', async () => {
    const pending = deferred();
    const load = vi.fn<typeof fetchSettingsAction>(async (action, args) => {
      if (action === 'history-rooms') return { ok: true, rooms: [
        { chat_id: oldId, chat_name: 'ROOM OLD' }, { chat_id: newId, chat_name: 'ROOM NEW' },
      ] };
      if (action === 'history-messages') return args?.chatId === oldId ? pending.promise
        : { ok: true, anchor_log_id: '2', next_before: null, total: 1,
            messages: [{ id: '2', text: 'CURRENT CHAT' }] };
      throw new Error(`Unexpected action: ${action}`);
    });
    const views = wireConversationViews(load);
    try {
      views.select('conversation');
      await flush();
      const roomButtons = document.querySelectorAll<HTMLButtonElement>('#chat-room-list button');
      expect(roomButtons).toHaveLength(2);
      roomButtons[1].click();
      pending.resolve({ ok: true, anchor_log_id: '1', next_before: null, total: 1,
        messages: [{ id: '1', text: 'STALE CHAT' }] });
      await flush();
      await flush();
      expect(document.getElementById('conversation-history-title')!.textContent).toBe('ROOM NEW');
      expect(document.getElementById('chat-message-list')!.textContent).toContain('CURRENT CHAT');
      expect(document.getElementById('chat-message-list')!.textContent).not.toContain('STALE CHAT');
    } finally { views.dispose(); }
  });
});
