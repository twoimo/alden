// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { wireConversationViews } from '../conversations';
import { wireAssistantComposer } from '../assistant-composer';
import { VirtualList } from '../virtual-list';
import type { fetchSettingsAction } from '../runtime';
import { wireRenderLifecycle, type VisibilityHandler } from '../core/lifecycle-wiring';
beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe('complete local histories', () => {
  it('keeps explicit user selection authoritative during a pending first reply',async()=>{
    const id='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';let sessions:any[]=[];let resolveReply!:(value:unknown)=>void;
    const native=vi.fn((command:string):Promise<any>=>command==='submit_assistant_input'?new Promise(resolve=>{resolveReply=resolve;}):Promise.resolve(true));
    const load=vi.fn<typeof fetchSettingsAction>(async action=>action==='voice-history-sessions'?{ok:true,items:sessions}:{ok:true,next:null,items:[]});
    const views=wireConversationViews(load);const committed=vi.fn(conversation=>views.refreshConversation(conversation));
    const composer=wireAssistantComposer(document,native,committed);
    try{
      composer.select('voice');views.select('voice');await settle();
      const text=document.querySelector<HTMLTextAreaElement>('#assistant-composer-text')!;text.value='Pending draft';text.dispatchEvent(new Event('input'));
      document.querySelector<HTMLFormElement>('#assistant-composer')!.requestSubmit();
      sessions=[{id,title:'Other conversation'}];views.select('voice');await settle();
      document.querySelector<HTMLButtonElement>('.history-room')!.click();await settle();
      expect(native).toHaveBeenCalledWith('cancel_assistant_input',expect.anything());
      resolveReply({ok:true,state:'completed',request_id:(native.mock.calls[0] as any)[1].input.request_id,conversation_id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',turn_id:1,context_version:1,reply:'Old response'});await settle();
      expect(committed).not.toHaveBeenCalled();expect(text.value).toBe('Pending draft');
      expect(document.getElementById('voice-start')!.dataset.conversationId).toBe(id);
    }finally{composer.dispose();views.dispose();}
  });
  it('does not turn a pending first reply catalog refresh into a user conversation switch',async()=>{
    const id='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';let sessions:any[]=[];let resolveReply!:(value:unknown)=>void;
    const native=vi.fn((command:string):Promise<any>=>command==='submit_assistant_input'
      ?new Promise(resolve=>{resolveReply=resolve;}):Promise.resolve(true));
    const load=vi.fn<typeof fetchSettingsAction>(async action=>action==='voice-history-sessions'
      ?{ok:true,items:sessions}:{ok:true,next:null,items:[]});
    const views=wireConversationViews(load);const composer=wireAssistantComposer(document,native,conversation=>views.refreshConversation(conversation));
    try{
      composer.select('voice');views.select('voice');await settle();
      const text=document.querySelector<HTMLTextAreaElement>('#assistant-composer-text')!;text.value='Read the link';text.dispatchEvent(new Event('input'));
      document.querySelector<HTMLFormElement>('#assistant-composer')!.requestSubmit();
      sessions=[{id,title:'Pending first question'}];views.select('voice');await settle();
      expect(document.getElementById('voice-start')!.dataset.conversationId).toBeUndefined();
      expect(native).not.toHaveBeenCalledWith('cancel_assistant_input',expect.anything());
      resolveReply({ok:true,state:'completed',request_id:(native.mock.calls[0] as any)[1].input.request_id,
        conversation_id:id,turn_id:1,context_version:1,answer_context_version:2,reply:'8'});await settle();
      expect(document.getElementById('voice-start')!.dataset.conversationId).toBe(id);
      expect(text.value).toBe('');
    }finally{composer.dispose();views.dispose();}
  });
  it('distinguishes typed document and image attachments in confirmed history', async () => {
    const load=vi.fn<typeof fetchSettingsAction>(async action=>action==='voice-history-sessions'
      ? {ok:true,items:[{id:'1',title:'첨부'}]}
      : {ok:true,next:null,items:[{id:'1',turn_id:1,role:'user',content:'첨부 확인',attachments:[
        {filename:'receipt.txt',mime:'text/plain'},{filename:'photo.png',mime:'image/png'},{filename:'unknown'}]}]});
    const views=wireConversationViews(load);
    try {
      views.select('voice');await settle();
      const labels=[...document.querySelectorAll('#voice-message-list .message-attachment')].map(x=>x.textContent);
      expect(labels).toEqual(['문서 · receipt.txt','이미지 · photo.png','첨부 · unknown']);
    } finally {views.dispose();}
  });
  it('shows persisted generation evidence only on assistant replies in collapsed details', async () => {
    const generation = { schema_version: 1, response: { model_id: 'observed/model', usage: { total_tokens: 0 } },
      image_context: { selected: [{ origin: 'current' }, { origin: 'previous' }] } };
    const load = vi.fn<typeof fetchSettingsAction>(async action => action === 'voice-history-sessions'
      ? { ok: true, items: [{ id: '1', title: '사진 비교' }] }
      : { ok: true, next: null, items: [
        { id: '1', turn_id: 1, role: 'user', content: '사진 질문', generation },
        { id: '2', turn_id: 1, role: 'assistant', content: '사진 답변', generation },
        { id: '3', turn_id: 2, role: 'assistant', content: '기존 답변' },
      ] });
    const views = wireConversationViews(load);
    try {
      views.select('voice');
      await settle();
      const rows = document.querySelectorAll('#voice-message-list .message-row');
      expect(rows).toHaveLength(3);
      expect(rows[0].querySelector('details')).toBeNull();
      const details = rows[1].querySelector('details')!;
      expect(details.open).toBe(false);
      expect(details.textContent).toContain('observed/model');
      expect(details.textContent).toContain('0 토큰');
      expect(details.textContent).toContain('이번 1장 · 이전 1장');
      expect(rows[2].querySelector('details')).toBeNull();
    } finally { views.dispose(); }
  });
  it('loads an unfocused visible window and still obeys native hide and restore', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async action => action === 'history-rooms'
      ? { ok: true, rooms: [{ chat_id: '1', chat_name: '확인된 방' }] }
      : { ok: true, anchor_log_id: '1', next_before: null, total: 1,
          messages: [{ id: '1', text: '확인된 원문' }] });
    const views = wireConversationViews(load);
    let signal!: VisibilityHandler;
    const detach = wireRenderLifecycle({ transition: state => views.visible(state === 'visible') }, {
      pauseOnBlur: false,
      subscribeVisibility: async handler => { signal = handler; return () => {}; },
      readVisibility: async () => true,
    });
    try {
      await settle();
      window.dispatchEvent(new Event('blur'));
      views.select('conversation');
      await settle();
      expect(document.getElementById('conversation-placeholder')!.hidden).toBe(true);
      expect(document.getElementById('chat-message-list')!.textContent).toContain('확인된 원문');
      signal(false);
      load.mockClear();
      views.select('voice');
      await settle();
      expect(load).not.toHaveBeenCalled();
      signal(true);
      await settle();
      expect(load.mock.calls.some(([action]) => action === 'voice-history-sessions')).toBe(true);
    } finally { detach(); views.dispose(); }
  });

  it('accepts confirmed history after focus leaves a still visible window', async () => {
    let release!: (value: Record<string, unknown>) => void;
    const load = vi.fn<typeof fetchSettingsAction>(async action => action === 'history-rooms'
      ? new Promise(resolve => { release = resolve; })
      : { ok: true, anchor_log_id: '1', next_before: null, total: 1,
          messages: [{ id: '1', text: '확인된 원문' }] });
    const views = wireConversationViews(load);
    const detach = wireRenderLifecycle({ transition: state => views.visible(state === 'visible') }, {
      pauseOnBlur: false,
      subscribeVisibility: async () => () => {},
      readVisibility: async () => true,
    });
    try {
      await settle();
      views.select('conversation');
      window.dispatchEvent(new Event('blur'));
      release({ ok: true, rooms: [{ chat_id: '1', chat_name: '확인된 방' }] });
      await settle();
      expect(document.getElementById('conversation-placeholder')!.hidden).toBe(true);
      expect(document.getElementById('chat-message-list')!.textContent).toContain('확인된 원문');
    } finally { detach(); views.dispose(); }
  });

  it.each(['conversation', 'voice'] as const)('ignores a late rejected %s catalog after a newer view has loaded', async kind => {
    let rejectOld!: (error: Error) => void;
    let catalogCalls = 0;
    const catalog = kind === 'conversation' ? 'history-rooms' : 'voice-history-sessions';
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === catalog) {
        if (++catalogCalls === 1) return new Promise((_, reject) => { rejectOld = reject; });
        return kind === 'conversation'
          ? { ok: true, rooms: [{ chat_id: 'current', chat_name: '현재 대화' }] }
          : { ok: true, items: [{ id: 'current', title: '현재 음성' }] };
      }
      return kind === 'conversation'
        ? { ok: true, anchor_log_id: '1', next_before: null, total: 1, messages: [{ id: '1', text: '현재 원문' }] }
        : { ok: true, next: null, items: [{ turn_id: 1, role: 'user', content: '현재 원문' }] };
    });
    const views = wireConversationViews(load);
    try {
      views.select(kind);
      await settle();
      views.select('settings');
      views.select(kind);
      await settle();
      const placeholder = document.getElementById(`${kind}-placeholder`)!;
      const messages = document.getElementById(kind === 'conversation' ? 'chat-message-list' : 'voice-message-list')!;
      expect(placeholder.hidden).toBe(true);
      expect(messages.textContent).toContain('현재 원문');
      rejectOld(new Error('old request disconnected'));
      await settle();
      expect(placeholder.hidden).toBe(true);
      expect(messages.hidden).toBe(false);
      expect(messages.textContent).toContain('현재 원문');
      expect(catalogCalls).toBe(2);
    } finally {
      views.dispose();
    }
  });

  it.each(['conversation', 'voice'] as const)('keeps the latest %s catalog when same-view responses arrive out of order', async kind => {
    let releaseOld!: (data: Record<string, unknown>) => void;
    let catalogCalls = 0;
    const catalog = kind === 'conversation' ? 'history-rooms' : 'voice-history-sessions';
    const catalogData = (name: string) => kind === 'conversation'
      ? { ok: true, rooms: [{ chat_id: name, chat_name: name }] }
      : { ok: true, items: [{ id: name, title: name }] };
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === catalog) {
        if (++catalogCalls === 1) return new Promise(resolve => { releaseOld = resolve; });
        return catalogData('CURRENT');
      }
      return kind === 'conversation'
        ? { ok: true, anchor_log_id: '1', next_before: null, total: 1, messages: [{ id: '1', text: '현재 원문' }] }
        : { ok: true, next: null, items: [{ turn_id: 1, role: 'user', content: '현재 원문' }] };
    });
    const views = wireConversationViews(load);
    try {
      views.select(kind);
      await settle();
      views.select(kind);
      await settle();
      releaseOld(catalogData('STALE'));
      await settle();
      const rail = document.getElementById(kind === 'conversation' ? 'chat-room-list' : 'voice-session-list')!;
      expect(rail.textContent).toContain('CURRENT');
      expect(rail.textContent).not.toContain('STALE');
      expect(load.mock.calls.filter(([action]) => action !== catalog)).toHaveLength(1);
    } finally {
      views.dispose();
    }
  });

  it.each(['conversation', 'voice'] as const)('shows a current rejected %s catalog and permits retry', async kind => {
    let catalogCalls = 0;
    const catalog = kind === 'conversation' ? 'history-rooms' : 'voice-history-sessions';
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action === catalog) {
        if (++catalogCalls === 1) throw new Error('current reader unavailable');
        return kind === 'conversation'
          ? { ok: true, rooms: [{ chat_id: '1', chat_name: '현재 대화' }] }
          : { ok: true, items: [{ id: '1', title: '현재 음성' }] };
      }
      return kind === 'conversation'
        ? { ok: true, anchor_log_id: '1', next_before: null, total: 1, messages: [{ id: '1', text: '다시 읽은 원문' }] }
        : { ok: true, next: null, items: [{ turn_id: 1, role: 'user', content: '다시 읽은 원문' }] };
    });
    const views = wireConversationViews(load);
    try {
      views.select(kind);
      await settle();
      const placeholder = document.getElementById(`${kind}-placeholder`)!;
      const retry = document.getElementById(`${kind}-retry`)!;
      expect(placeholder.hidden).toBe(false);
      expect(placeholder.textContent).toContain('불러오지 못했습니다');
      expect(retry.hidden).toBe(false);
      retry.click();
      await settle();
      expect(placeholder.hidden).toBe(true);
      expect(document.getElementById(kind === 'conversation' ? 'chat-message-list' : 'voice-message-list')!.textContent).toContain('다시 읽은 원문');
      expect(catalogCalls).toBe(2);
    } finally {
      views.dispose();
    }
  });

  it.each(['conversation', 'voice'] as const)('clears the failure status when a rejected %s catalog retries to an empty result', async kind => {
    let catalogCalls = 0;
    const catalog = kind === 'conversation' ? 'history-rooms' : 'voice-history-sessions';
    const heading = kind === 'conversation' ? '저장된 대화가 없습니다' : '지난 음성 대화가 없습니다';
    const load = vi.fn<typeof fetchSettingsAction>(async action => {
      if (action !== catalog) throw new Error('An empty catalog must not request messages');
      if (++catalogCalls === 1) throw new Error('current reader unavailable');
      return kind === 'conversation' ? { ok: true, rooms: [] } : { ok: true, items: [] };
    });
    const views = wireConversationViews(load);
    try {
      views.select(kind);
      await settle();
      const status = document.getElementById(`${kind}-history-status`)!;
      const placeholder = document.getElementById(`${kind}-placeholder`)!;
      const retry = document.getElementById(`${kind}-retry`)!;
      expect(status.textContent).toContain('불러오지 못했습니다');
      expect(retry.hidden).toBe(false);
      retry.click();
      await settle();
      expect(status.textContent).toBe(heading);
      expect(placeholder.hidden).toBe(false);
      expect(placeholder.querySelector('strong')!.textContent).toBe(heading);
      expect(retry.hidden).toBe(true);
      expect(load).toHaveBeenCalledTimes(2);
      expect(load.mock.calls.every(([action]) => action === catalog)).toBe(true);
    } finally {
      views.dispose();
    }
  });

  it('collapses system metadata while preserving its original and a user JSON message', async () => {
    const text='{"feedType":2,"member":{"nickName":"예시"}}';
    const load=vi.fn<typeof fetchSettingsAction>(async action=>action==='history-rooms'
      ? {ok:true,rooms:[{chat_id:'1',chat_name:'기록'}]}
      : {ok:true,anchor_log_id:'2',total:2,next_before:null,messages:[{id:'1',type:0,text},{id:'2',type:1,text}]});
    const views=wireConversationViews(load);views.select('conversation');await settle();
    const rows=document.querySelectorAll('.message-row');
    expect(rows[0].querySelector('details')?.open).toBe(false);
    expect(rows[0].querySelector('.message-text')?.textContent).toBe(text);
    expect(rows[0].querySelector('.message-author')?.textContent).toBe('대화방');
    expect(rows[1].querySelector('details')).toBeNull();
    expect(rows[1].querySelector('.message-text')?.textContent).toBe(text);views.dispose();
  });
  it('opens the latest room and keeps the last message visible when pagination appears', async () => {
    const load=vi.fn<typeof fetchSettingsAction>(async(action)=>action==='history-rooms'
      ? {ok:true,rooms:[{chat_id:'1',chat_name:'최근 대화'}]}
      : {ok:true,anchor_log_id:'9',next_before:'8',total:9,messages:[{id:'9',text:'최신 원문'}]});
    const views=wireConversationViews(load);views.select('conversation');await settle();
    expect(load.mock.calls.some(([action,args])=>action==='history-messages'&&args?.chatId==='1')).toBe(true);
    expect(document.getElementById('conversation-placeholder')!.hidden).toBe(true);
    expect(document.getElementById('chat-message-list')!.textContent).toContain('최신 원문');
    expect(document.getElementById('conversation-history-older')!.hidden).toBe(false);views.dispose();
  });
  it('shows a failed empty reader and retries once instead of retaining a loading screen', async () => {
    let available=false;
    const load=vi.fn<typeof fetchSettingsAction>(async(action)=>action==='history-rooms'
      ? available?{ok:true,rooms:[{chat_id:'1',chat_name:'방'}]}:{ok:false}
      : {ok:true,anchor_log_id:'1',next_before:null,total:1,messages:[{id:'1',text:'복구한 기록'}]});
    const views=wireConversationViews(load);views.select('conversation');await settle();
    expect(document.getElementById('conversation-placeholder')!.textContent).toContain('불러오지 못했습니다');
    expect(document.getElementById('conversation-retry')!.hidden).toBe(false);
    available=true;document.getElementById('conversation-retry')!.click();await settle();
    expect(load.mock.calls.filter(([action])=>action==='history-rooms')).toHaveLength(2);
    expect(document.getElementById('conversation-placeholder')!.hidden).toBe(true);views.dispose();
  });
  it('refreshes a revisited voice session without duplicating old turns or losing the older cursor', async () => {
    let revision=1;
    const load=vi.fn<typeof fetchSettingsAction>(async(action)=>action==='voice-history-sessions'
      ? {ok:true,items:[{id:'session',title:'음성 기록'}]}
      : {ok:true,next:revision===1?5:10,items:[{turn_id:1,role:'user',content:'첫 발화'},...(revision===2?[{turn_id:2,role:'assistant',content:'새 답변'}]:[])]});
    const views=wireConversationViews(load);views.select('voice');await settle();
    revision=2;views.select('settings');views.select('voice');await settle();
    expect(document.getElementById('voice-message-list')!.textContent?.match(/첫 발화/g)).toHaveLength(1);
    expect(document.getElementById('voice-message-list')!.textContent).toContain('새 답변');
    document.getElementById('voice-history-older')!.click();await settle();
    const calls=load.mock.calls.filter(([action])=>action==='voice-history-messages');
    expect(JSON.parse(calls.at(-1)![1]!.query!).before).toBe(5);views.dispose();
  });
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
  it('reopens a missing middle range after more than one page arrives while hidden', async () => {
    vi.useFakeTimers();
    let total = 10;
    const cursors: Array<number | null> = [];
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action !== 'db-sync-history') return null;
      const before = JSON.parse(input.query!).before as number | null;
      cursors.push(before);
      const high = before === null ? total : Math.min(total, before - 1);
      const low = Math.max(1, high - 49);
      return { ok: true, current: { phase: 'complete' }, next: low > 1 ? low : null,
        items: Array.from({ length: high - low + 1 }, (_, i) => ({ id: high - i, phase: 'complete', details: { messages: high - i } })) };
    });
    const views = wireConversationViews(load); views.select('history'); await vi.advanceTimersByTimeAsync(0);
    expect(document.getElementById('db-history-older')!.hidden).toBe(true);
    views.visible(false); total = 130; views.visible(true); await vi.advanceTimersByTimeAsync(0);
    expect(document.getElementById('db-history-older')!.hidden).toBe(false);
    document.getElementById('db-history-older')!.click(); await vi.advanceTimersByTimeAsync(0);
    expect(cursors.at(-1)).toBe(81);
    document.getElementById('db-history-older')!.click(); await vi.advanceTimersByTimeAsync(0);
    expect(cursors.at(-1)).toBe(31);
    expect(document.getElementById('db-history-older')!.hidden).toBe(true);
    expect(document.querySelectorAll('.db-cycle-row').length).toBeLessThanOrEqual(80);
    views.dispose(); vi.useRealTimers();
  });
});
