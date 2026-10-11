// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { wireAssistantComposer } from '../assistant-composer';

type NativeCall = {
  args: Record<string, unknown>;
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
};
type Controller = ReturnType<typeof wireAssistantComposer>;
const controllers: Controller[] = [];
const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));

function fixture(conversationId?: string) {
  const microphone = document.querySelector<HTMLButtonElement>('#voice-start')!;
  if (conversationId) microphone.dataset.conversationId = conversationId;
  const submitted: NativeCall[] = [];
  const invokeMock = vi.fn((command: string, args?: Record<string, unknown>): Promise<unknown> => {
    if (command === 'submit_assistant_input') {
      return new Promise((resolve, reject) => submitted.push({ args: args!, resolve, reject }));
    }
    if (command === 'cancel_assistant_input') return Promise.resolve(true);
    throw new Error(`Unexpected native command: ${command}`);
  });
  const invoke = <T = unknown>(command: string, args?: Record<string, unknown>): Promise<T> =>
    invokeMock(command, args) as Promise<T>;
  const committed = vi.fn<(conversationId: string) => void>();
  const controller = wireAssistantComposer(document, invoke, committed);
  controllers.push(controller);
  const form = document.querySelector<HTMLFormElement>('#assistant-composer')!;
  const text = document.querySelector<HTMLTextAreaElement>('#assistant-composer-text')!;
  const picker = document.querySelector<HTMLInputElement>('#assistant-composer-images')!;
  const preview = document.querySelector<HTMLElement>('#assistant-composer-preview')!;
  const status = document.querySelector<HTMLElement>('#assistant-composer-status')!;
  const send = document.querySelector<HTMLButtonElement>('#assistant-composer-send')!;
  const cancel = document.querySelector<HTMLButtonElement>('#assistant-composer-cancel')!;
  controller.select('voice');
  function write(value: string) {
    text.value = value;
    text.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function submit() { form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); }
  function pick(...files: File[]) {
    Object.defineProperty(picker, 'files', { configurable: true, value: files });
    picker.dispatchEvent(new Event('change', { bubbles: true }));
  }
  return { microphone, submitted, invokeMock, committed, controller, form, text, picker, preview,
    status, send, cancel, write, submit, pick };
}

function input(call: NativeCall): Record<string, unknown> {
  return call.args.input as Record<string, unknown>;
}
function receipt(call: NativeCall, overrides: Record<string, unknown> = {}) {
  return {
    ok: true, state: 'completed', request_id: input(call).request_id,
    conversation_id: input(call).conversation_id ?? 'cccccccccccccccccccccccccccccccc',
    turn_id: 42, context_version: 7, reply: '저장된 답변', ...overrides,
  };
}

describe('uncertain input reconciliation and strict receipts', () => {
  it('reuses an uncertain request ID and payload with a fresh token after transport failure', async () => {
    const app = fixture(); app.write('원래 입력'); app.submit();
    const first = app.submitted[0]; first.reject(new Error('transport unavailable')); await tick();
    expect(app.text.value).toBe('원래 입력');
    app.submit(); const second = app.submitted[1];
    expect(input(second)).toEqual(input(first));
    expect(second.args.tokenId).not.toBe(first.args.tokenId);
    second.resolve(receipt(second)); await tick();
    expect(app.committed).toHaveBeenCalledTimes(1); expect(app.text.value).toBe('');
  });

  it('keeps a running duplicate available for same-ID reconciliation without cancelling another owner', async () => {
    const app = fixture(); app.write('한 번만'); app.submit();
    const first = app.submitted[0]; first.resolve(receipt(first,{ok:false,state:'running'})); await tick();
    expect(app.text.value).toBe('한 번만');
    expect(app.invokeMock).not.toHaveBeenCalledWith('cancel_assistant_input',expect.anything());
    app.submit(); expect(input(app.submitted[1])).toEqual(input(first));
  });

  it('treats an edited draft as a new event after an uncertain receipt', async () => {
    const app = fixture(); app.write('처음'); app.submit();
    const first = app.submitted[0]; first.resolve({ok:true}); await tick();
    app.write('새로운 요청'); app.submit();
    expect(input(app.submitted[1]).request_id).not.toBe(input(first).request_id);
    expect(input(app.submitted[1]).text).toBe('새로운 요청');
  });

  it.each([{turn_id:0},{turn_id:'42'},{context_version:0},{context_version:Number.MAX_SAFE_INTEGER+1},
    {conversation_id:'not-a-session'},{reply:''},{reply:'x'.repeat(64001)}])('rejects an invalid completed receipt %j', async invalid => {
      const app = fixture(); app.write('확인 대상'); app.submit();
      app.submitted[0].resolve(receipt(app.submitted[0],invalid)); await tick();
      expect(app.text.value).toBe('확인 대상'); expect(app.committed).not.toHaveBeenCalled();
    });
});

class ControlledReader {
  static instances: ControlledReader[] = [];
  result: string | null = null;
  aborted = false;
  private events = new Map<string, Array<() => void>>();
  constructor() { ControlledReader.instances.push(this); }
  addEventListener(name: string, listener: () => void): void {
    this.events.set(name, [...(this.events.get(name) ?? []), listener]);
  }
  readAsDataURL(_file: File): void { /* Resolves only through an explicit test action. */ }
  abort(): void { this.aborted = true; this.fire('abort'); }
  load(data: string): void { this.result = data; this.fire('load'); }
  error(): void { this.fire('error'); }
  private fire(event: string): void { for (const handler of this.events.get(event) ?? []) handler(); }
}

beforeEach(() => { document.body.innerHTML = settingsMarkup(); });
afterEach(() => {
  for (const controller of controllers.splice(0)) controller.dispose();
  vi.unstubAllGlobals();
  ControlledReader.instances = [];
  document.body.replaceChildren();
});

describe('assistant composer native ownership', () => {
  it('mounts beneath the existing voice history and leaves microphone controls untouched', () => {
    const mic = document.querySelector('#voice-start')!;
    const micLabel = mic.textContent;
    const app = fixture('existing');
    expect(app.form.parentElement).toBe(document.querySelector('#settings-page-voice .history-reader'));
    expect(app.form.previousElementSibling?.id).toBe('voice-history-older');
    expect(app.text.getAttribute('aria-describedby')).toContain('assistant-composer-status');
    expect(app.picker.multiple).toBe(true);
    expect(app.picker.accept).toBe('image/png,image/jpeg,image/webp,image/gif');
    expect(mic.textContent).toBe(micLabel);
    expect(app.status.getAttribute('role')).toBe('status');
    expect(wireAssistantComposer(document, async <T = unknown>() => undefined as T, vi.fn())).toBe(app.controller);
    expect(document.querySelectorAll('#assistant-composer')).toHaveLength(1);
  });

  it('sends a new token/request pair to the selected conversation and commits only its validated receipt', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('안녕하세요, 올든!');
    app.submit();
    expect(app.submitted).toHaveLength(1);
    const call = app.submitted[0];
    expect(app.invokeMock).toHaveBeenCalledWith('submit_assistant_input', {
      input: { request_id: expect.stringMatching(/^[0-9a-f]{32}$/),
        conversation_id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', text: '안녕하세요, 올든!', images: [] },
      tokenId: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/),
    });
    expect(call.args.tokenId).not.toBe(input(call).request_id);
    expect(app.send.disabled).toBe(true);
    expect(app.cancel.hidden).toBe(false);
    call.resolve(receipt(call));
    await tick();
    expect(app.committed).toHaveBeenCalledExactlyOnceWith('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    expect(app.text.value).toBe('');
    expect(app.status.textContent).toBe('답변이 저장되었습니다.');
    expect(app.send.disabled).toBe(false);
  });

  it('allows a new conversation with null input ID and requires a named completed conversation', async () => {
    const app = fixture();
    app.write('새 대화');
    app.submit();
    expect(input(app.submitted[0]).conversation_id).toBeNull();
    app.submitted[0].resolve(receipt(app.submitted[0], { conversation_id: null }));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.text.value).toBe('새 대화');
    app.submit();
    app.submitted[1].resolve(receipt(app.submitted[1]));
    await tick();
    expect(app.committed).toHaveBeenCalledExactlyOnceWith('cccccccccccccccccccccccccccccccc');
  });

  it('suppresses duplicate submit events and issues fresh IDs for the next event', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('첫 요청');
    app.submit();
    app.submit();
    app.send.click();
    expect(app.submitted).toHaveLength(1);
    const first = app.submitted[0];
    first.resolve(receipt(first));
    await tick();
    app.write('다음 요청');
    app.submit();
    expect(app.submitted).toHaveLength(2);
    expect(input(app.submitted[1]).request_id).not.toBe(input(first).request_id);
    expect(app.submitted[1].args.tokenId).not.toBe(first.args.tokenId);
  });

  it('cancels on page switch and rejects late success and failure from the retired owner', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('보존할 초안');
    app.submit();
    const stale = app.submitted[0];
    app.controller.select('settings');
    expect(app.form.hidden).toBe(true);
    expect(app.invokeMock).toHaveBeenCalledWith('cancel_assistant_input', { tokenId: stale.args.tokenId });
    stale.resolve(receipt(stale));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.text.value).toBe('보존할 초안');
    app.controller.select('voice');
    app.submit();
    const newer = app.submitted[1];
    app.controller.select('history');
    newer.reject(new Error('/Users/private/newer'));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.status.textContent).not.toContain('/Users/private');
  });

  it('retires a request when the voice history selection changes within the same page', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('이전 방 초안');
    app.submit();
    const stale = app.submitted[0];
    app.microphone.dataset.conversationId = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    await tick();
    expect(app.invokeMock).toHaveBeenCalledWith('cancel_assistant_input', { tokenId: stale.args.tokenId });
    stale.resolve(receipt(stale));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.text.value).toBe('이전 방 초안');
    app.submit();
    expect(input(app.submitted[1]).conversation_id).toBe('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');
  });

  it('keeps an observed session switch retired even when the user returns to the original session', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('초안');
    app.submit();
    const stale = app.submitted[0];
    app.microphone.dataset.conversationId = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    await tick();
    app.microphone.dataset.conversationId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    stale.resolve(receipt(stale));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.invokeMock).toHaveBeenCalledWith('cancel_assistant_input', { tokenId: stale.args.tokenId });
  });

  it('cancels on native hide and preserves the draft across restore', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('다시 쓸 수 있는 초안');
    app.submit();
    const stale = app.submitted[0];
    app.controller.visible(false);
    expect(app.form.hidden).toBe(true);
    expect(app.invokeMock).toHaveBeenCalledWith('cancel_assistant_input', { tokenId: stale.args.tokenId });
    stale.resolve(receipt(stale));
    await tick();
    app.controller.visible(true);
    expect(app.form.hidden).toBe(false);
    expect(app.text.value).toBe('다시 쓸 수 있는 초안');
    expect(app.committed).not.toHaveBeenCalled();
  });

  it('cancels explicitly and disposes idempotently without updating an unrelated reader', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('취소할 요청');
    app.submit();
    const first = app.submitted[0];
    app.cancel.click();
    expect(app.invokeMock).toHaveBeenCalledWith('cancel_assistant_input', { tokenId: first.args.tokenId });
    expect(app.text.value).toBe('취소할 요청');
    app.submit();
    const second = app.submitted[1];
    app.controller.dispose();
    app.controller.dispose();
    expect(app.invokeMock).toHaveBeenCalledWith('cancel_assistant_input', { tokenId: second.args.tokenId });
    expect(document.querySelector('#assistant-composer')).toBeNull();
    first.resolve(receipt(first));
    second.resolve(receipt(second));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
  });

  it('honors IME composition, Shift+Enter, and Enter keyboard semantics', () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('한국어');
    app.text.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    app.text.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(app.submitted).toHaveLength(0);
    app.text.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
    app.text.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }));
    expect(app.submitted).toHaveLength(0);
    app.text.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
    expect(app.submitted).toHaveLength(0);
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    app.text.dispatchEvent(enter);
    expect(enter.defaultPrevented).toBe(true);
    expect(app.submitted).toHaveLength(1);
    app.text.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(app.submitted).toHaveLength(1);
  });

  it('keeps newer edits, including identical text typed again, after a valid older receipt', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('첫 초안');
    app.submit();
    app.write('바꾼 초안');
    app.write('첫 초안');
    app.submitted[0].resolve(receipt(app.submitted[0]));
    await tick();
    expect(app.committed).toHaveBeenCalledTimes(1);
    expect(app.text.value).toBe('첫 초안');
  });

  it.each([
    { request_id: 'f'.repeat(32) },
    { conversation_id: 'another-session' },
    { turn_id: null },
    { context_version: null },
    { state: 'completed', ok: false },
    { state: 'unknown' },
  ])('rejects malformed or mismatched receipts without committing (%j)', async override => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('안전한 초안');
    app.submit();
    app.submitted[0].resolve(receipt(app.submitted[0], override));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.text.value).toBe('안전한 초안');
    expect(app.send.disabled).toBe(false);
    expect(app.invokeMock).toHaveBeenCalledWith('cancel_assistant_input', { tokenId: app.submitted[0].args.tokenId });
  });

  it('rejects primitives and partial success without treating them as a native receipt', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('초안');
    app.submit();
    app.submitted[0].resolve(true);
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    app.submit();
    app.submitted[1].resolve({ ok: true, state: 'completed' });
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.text.value).toBe('초안');
  });

  it.each([
    { state: 'failed', ok: false, error_code: 'assistant_image_invalid', status: '이미지를 처리할 수 없습니다.' },
    { state: 'cancelled', ok: false, status: '요청이 취소되었습니다.' },
    { state: 'superseded', ok: false, status: '새 요청으로 이전 요청이 종료되었습니다.' },
    { state: 'running', ok: true, status: '답변을 확인하지 못했습니다.' },
  ])('keeps the draft for noncompleted responses (%j)', async outcome => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('초안 유지');
    app.submit();
    app.submitted[0].resolve(receipt(app.submitted[0], outcome));
    await tick();
    expect(app.committed).not.toHaveBeenCalled();
    expect(app.text.value).toBe('초안 유지');
    expect(app.status.textContent).toContain(outcome.status);
  });

  it('shows only safe Korean text for rejected native calls and private errors', async () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('<img src=x onerror=alert(1)>');
    app.submit();
    app.submitted[0].reject(new Error('provider crashed at /Users/private/token'));
    await tick();
    expect(app.status.textContent).toBe('답변을 확인하지 못했습니다. 작성한 내용은 그대로 남아 있습니다.');
    expect(document.querySelector('#assistant-composer img')).toBeNull();
    expect(app.text.value).toContain('<img');
    expect(app.committed).not.toHaveBeenCalled();
  });

  it('enforces the character boundary and permits 4,000 emoji within 16,000 UTF-8 bytes', () => {
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.write('한'.repeat(4_001));
    app.submit();
    expect(app.submitted).toHaveLength(0);
    expect(app.status.textContent).toContain('4,000자');
    app.write('😀'.repeat(4_000));
    app.submit();
    expect(app.submitted).toHaveLength(1);
    expect(input(app.submitted[0]).text).toBe('😀'.repeat(4_000));
  });

  it('blocks empty content and cannot submit after hide or dispose', () => {
    const app = fixture();
    app.write('   ');
    app.submit();
    expect(app.submitted).toHaveLength(0);
    app.controller.select('settings');
    app.write('가려진 초안');
    app.submit();
    app.controller.select('voice');
    app.controller.dispose();
    app.submit();
    expect(app.submitted).toHaveLength(0);
  });

  it('checks image types, per-file size, total batch size, count and filename before reading', () => {
    vi.stubGlobal('FileReader', ControlledReader);
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    const file = (name: string, mime = 'image/png', size = 1) => {
      const image = new File(['A'], name, { type: mime });
      Object.defineProperty(image, 'size', { configurable: true, value: size });
      return image;
    };
    app.pick(file('not.txt', 'text/plain'));
    app.pick(file('oversized.png', 'image/png', 8 * 1024 * 1024 + 1));
    app.pick(file('a.png', 'image/png', 7 * 1024 * 1024),
      file('b.png', 'image/png', 7 * 1024 * 1024), file('c.png', 'image/png', 7 * 1024 * 1024));
    app.pick(...Array.from({ length: 5 }, (_, n) => file(`${n}.png`)));
    app.pick(file('../path.png'));
    expect(ControlledReader.instances).toHaveLength(0);
    expect(app.preview.children).toHaveLength(0);
    expect(app.status.textContent).toContain('20MiB');
    app.submit();
    expect(app.submitted).toHaveLength(0);
  });

  it('copies picker images as base64 only, displays safe names and clears committed attachments', async () => {
    vi.stubGlobal('FileReader', ControlledReader);
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    const file = new File(['A'], '<photo>.png', { type: 'image/png' });
    app.pick(file);
    expect(app.send.disabled).toBe(true);
    expect(ControlledReader.instances).toHaveLength(1);
    ControlledReader.instances[0].load('data:image/png;base64,QQ==');
    await tick();
    expect(app.preview.textContent).toContain('<photo>.png');
    expect(app.preview.querySelector('photo')).toBeNull();
    app.submit();
    expect(input(app.submitted[0]).images).toEqual([{ name: '<photo>.png', mime: 'image/png', data: 'QQ==' }]);
    app.submitted[0].resolve(receipt(app.submitted[0]));
    await tick();
    expect(app.preview.children).toHaveLength(0);
    expect(app.committed).toHaveBeenCalledTimes(1);
  });

  it('only the latest picker read can change attachments and removes individual previews', async () => {
    vi.stubGlobal('FileReader', ControlledReader);
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.pick(new File(['A'], 'old.png', { type: 'image/png' }));
    const old = ControlledReader.instances[0];
    app.pick(new File(['B'], 'new.png', { type: 'image/png' }));
    expect(old.aborted).toBe(true);
    old.load('data:image/png;base64,QQ==');
    ControlledReader.instances[1].load('data:image/png;base64,Qg==');
    await tick();
    expect(app.preview.textContent).toContain('new.png');
    expect(app.preview.textContent).not.toContain('old.png');
    app.preview.querySelector<HTMLButtonElement>('button')!.click();
    expect(app.preview.children).toHaveLength(0);
    app.submit();
    expect(app.submitted).toHaveLength(0);
  });

  it('preserves newly selected images when an older pending turn commits', async () => {
    vi.stubGlobal('FileReader', ControlledReader);
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.pick(new File(['A'], 'first.png', { type: 'image/png' }));
    ControlledReader.instances[0].load('data:image/png;base64,QQ==');
    await tick();
    app.write('첫 메시지');
    app.submit();
    const call = app.submitted[0];
    app.pick(new File(['B'], 'second.png', { type: 'image/png' }));
    ControlledReader.instances[1].load('data:image/png;base64,Qg==');
    await tick();
    call.resolve(receipt(call));
    await tick();
    expect(app.committed).toHaveBeenCalledTimes(1);
    expect(app.preview.textContent).toContain('second.png');
    expect(app.preview.textContent).not.toContain('first.png');
  });

  it('fences image read completion after a session switch or native hide', async () => {
    vi.stubGlobal('FileReader', ControlledReader);
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.pick(new File(['A'], 'stale.png', { type: 'image/png' }));
    const stale = ControlledReader.instances[0];
    app.microphone.dataset.conversationId = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    await tick();
    expect(stale.aborted).toBe(true);
    stale.load('data:image/png;base64,QQ==');
    await tick();
    expect(app.preview.children).toHaveLength(0);
    app.pick(new File(['B'], 'hidden.png', { type: 'image/png' }));
    const hidden = ControlledReader.instances[1];
    app.controller.visible(false);
    expect(hidden.aborted).toBe(true);
    hidden.load('data:image/png;base64,Qg==');
    await tick();
    expect(app.preview.children).toHaveLength(0);
    expect(app.committed).not.toHaveBeenCalled();
  });

  it('does not bind an invalid or truncated base64 image', async () => {
    vi.stubGlobal('FileReader', ControlledReader);
    const app = fixture('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    app.pick(new File(['ABCDE'], 'bad.png', { type: 'image/png' }));
    ControlledReader.instances[0].load('data:image/png;base64,QQ==');
    await tick();
    expect(app.status.textContent).toBe('이미지를 읽지 못했습니다. 다시 선택해 주세요.');
    expect(app.preview.children).toHaveLength(0);
    app.submit();
    expect(app.submitted).toHaveLength(0);
  });
});
