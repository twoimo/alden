/** Owned, opt-in text/image input for the existing voice conversation reader. */
type NativeInvoke = <T = unknown>(command: string, args?: Record<string, unknown>) => Promise<T>;

type ImageInput = { name: string; mime: string; data: string };
type OwnedRequest = {
  tokenId: string;
  requestId: string;
  conversationId: string | null;
  text: string;
  textRevision: number;
  imageRevision: number;
  images: ImageInput[];
};

const MAX_CHARACTERS = 4_000;
const MAX_TEXT_BYTES = 16_000;
const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_BATCH_BYTES = 20 * 1024 * 1024;
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
const HEX_32 = /^[a-f0-9]{32}$/;
const UUID_V4 = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const NATIVE_CODE = /^(?:Error:\s*)?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?::\s.*)?$/;
const ERROR_DETAILS: Readonly<Record<string, string>> = {
  assistant_text_too_long: '메시지 크기가 허용 범위를 넘었습니다. 내용을 줄여 주세요.',
  assistant_input_too_large: '메시지 크기가 허용 범위를 넘었습니다. 내용을 줄여 주세요.',
  input_too_large: '메시지 크기가 허용 범위를 넘었습니다. 내용을 줄여 주세요.',
  assistant_image_budget: '이미지 처리 용량 한도를 넘었습니다. 이미지 개수나 크기를 줄여 주세요.',
  assistant_image_invalid: '이미지 형식이나 내용이 유효하지 않습니다. 다른 이미지를 선택해 주세요.',
  image_invalid: '이미지 형식이나 내용이 유효하지 않습니다. 다른 이미지를 선택해 주세요.',
  unsupported_image_type: '이미지 형식을 지원하지 않습니다. PNG, JPEG, WebP 또는 GIF를 선택해 주세요.',
  assistant_image_decoder_unavailable: '이미지 해독 기능을 사용할 수 없습니다. 이미지 처리 환경을 확인해 주세요.',
  assistant_image_decoder_timeout: '이미지 해독 시간이 초과됐습니다. 작은 이미지로 다시 시도해 주세요.',
  image_input_provenance_changed: '받은 이미지 내용이 바뀌었습니다. 같은 사진을 다시 첨부해 주세요.',
  assistant_image_pixel_budget: '이미지 픽셀 수 한도를 넘었습니다. 해상도를 줄여 주세요.',
  assistant_image_store_unavailable: '이미지를 저장할 수 없습니다. 저장소 상태를 확인해 주세요.',
  assistant_image_model_unsupported: '선택한 모델이 이미지 입력을 지원하지 않습니다. 모델 설정을 확인해 주세요.',
  assistant_model_unavailable: '대화 모델에 연결할 수 없습니다. 설정을 확인해 주세요.',
  model_unavailable: '대화 모델에 연결할 수 없습니다. 설정을 확인해 주세요.',
  voice_model_configuration_invalid: '대화 모델 설정이 올바르지 않습니다. 모델 설정을 확인해 주세요.',
  local_llm_model_not_ready: '선택한 로컬 모델이 준비되지 않았습니다. 준비 상태를 확인해 주세요.',
  routed_llm_incomplete_response: '모델 답변이 완결되지 않았습니다. 작성한 내용은 남아 있습니다.',
  assistant_conversation_missing: '선택한 대화를 찾지 못했습니다. 대화를 다시 선택해 주세요.',
  conversation_not_found: '선택한 대화를 찾지 못했습니다. 대화를 다시 선택해 주세요.',
  assistant_input_busy: '다른 요청을 처리하고 있습니다. 완료된 뒤 다시 시도해 주세요.',
  runtime_busy: '다른 요청을 처리하고 있습니다. 완료된 뒤 다시 시도해 주세요.',
  assistant_input_cancelled: '요청이 취소되었습니다. 작성한 내용은 남아 있습니다.',
  assistant_input_superseded: '새 요청으로 이전 요청이 종료되었습니다. 작성한 내용은 남아 있습니다.',
};
const UNCERTAIN_MESSAGE = '답변을 확인하지 못했습니다. 작성한 내용은 그대로 남아 있습니다.';

const mounted = new WeakMap<Document, ReturnType<typeof wireAssistantComposer>>();

function receivedCode(error: unknown, depth = 0): string | null {
  if (depth > 2) return null;
  if (typeof error === 'string') {
    const code = NATIVE_CODE.exec(error.trim())?.[1];
    return code && Object.hasOwn(ERROR_DETAILS, code) ? code : null;
  }
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>;
    return receivedCode(record.error_code,depth+1) ?? receivedCode(record.code,depth+1)
      ?? (error instanceof Error ? receivedCode(error.message,depth+1) : receivedCode(record.message,depth+1));
  }
  return null;
}

function safeFailure(error: unknown): string {
  const code = receivedCode(error);
  if (!code) return UNCERTAIN_MESSAGE;
  return `${ERROR_DETAILS[code] ?? UNCERTAIN_MESSAGE} (오류 코드: ${code})`;
}

function isReceipt(value: unknown, owner: OwnedRequest): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const result = value as Record<string, unknown>;
  const state = result.state;
  if (typeof result.ok !== 'boolean'
    || !['completed', 'cancelled', 'failed', 'superseded', 'running'].includes(String(state))
    || !HEX_32.test(String(result.request_id))
    || result.request_id !== owner.requestId) return false;
  if (result.conversation_id !== null && (typeof result.conversation_id !== 'string'
    || !HEX_32.test(result.conversation_id))) return false;
  if (owner.conversationId !== null && result.conversation_id !== owner.conversationId) return false;
  return true;
}

function completedReceipt(value: Record<string, unknown>): value is Record<string, unknown> & { conversation_id: string } {
  if (value.ok !== true || value.state !== 'completed'
    || typeof value.conversation_id !== 'string' || !HEX_32.test(value.conversation_id)) return false;
  if (typeof value.reply !== 'string' || !value.reply.trim()
    || new TextEncoder().encode(value.reply).byteLength > 64_000) return false;
  const positiveSafeInteger = (number: unknown) => typeof number === 'number'
    && Number.isSafeInteger(number) && number > 0;
  return positiveSafeInteger(value.turn_id) && positiveSafeInteger(value.context_version);
}

function readImage(file: File, readers: Set<FileReader>): Promise<ImageInput> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    readers.add(reader);
    const fail = () => { readers.delete(reader); reject(new Error('image_read_failed')); };
    reader.addEventListener('error', fail, { once: true });
    reader.addEventListener('abort', fail, { once: true });
    reader.addEventListener('load', () => {
      readers.delete(reader);
      const value = reader.result;
      const match = typeof value === 'string'
        ? /^data:[^,;]*;base64,([A-Za-z0-9+/]+={0,2})$/.exec(value) : null;
      if (!match || match[1].length % 4 !== 0) { fail(); return; }
      const data = match[1];
      const byteLength = data.length / 4 * 3 - (data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0);
      if (byteLength !== file.size || byteLength > MAX_IMAGE_BYTES) { fail(); return; }
      resolve({ name: file.name, mime: file.type, data });
    }, { once: true });
    try { reader.readAsDataURL(file); } catch { fail(); }
  });
}

export function wireAssistantComposer(
  root: Document,
  invoke: NativeInvoke,
  onCommitted: (conversationId: string) => void,
): { dispose(): void; select(page: string): void; visible(flag: boolean): void } {
  const existing = mounted.get(root);
  if (existing) return existing;

  const page = root.getElementById('settings-page-voice');
  const reader = page?.querySelector<HTMLElement>('.history-reader');
  const voiceStart = root.getElementById('voice-start');
  if (!reader || !voiceStart) throw new Error('assistant_composer_voice_reader_missing');

  const create = <K extends keyof HTMLElementTagNameMap>(tag: K, id: string, className = '') => {
    const node = root.createElement(tag);
    node.id = id;
    node.className = className;
    return node;
  };

  const form = create('form', 'assistant-composer', 'voice-reader-controls');
  form.setAttribute('aria-label', '올든에게 메시지 보내기');
  form.noValidate = true;
  // Parent owns the stylesheet; the existing reader classes provide the shared surface.
  form.style.flexWrap = 'wrap';
  const label = create('label', 'assistant-composer-label', 'preference-field');
  label.htmlFor = 'assistant-composer-text';
  label.textContent = '올든에게 메시지';
  label.style.flex = '1 1 100%';
  const text = create('textarea', 'assistant-composer-text', 'message-text');
  text.rows = 3;
  // Astral characters occupy two UTF-16 units in maxlength, but one code point.
  text.maxLength = MAX_CHARACTERS * 2;
  text.placeholder = '메시지를 입력하세요. Shift+Enter로 줄을 바꿉니다.';
  text.setAttribute('aria-describedby', 'assistant-composer-status assistant-composer-limits');
  text.style.display = 'block';
  text.style.width = '100%';
  text.style.minWidth = '0';
  text.style.minHeight = '68px';
  text.style.resize = 'vertical';
  text.style.padding = '10px 12px';
  text.style.font = 'inherit';
  text.style.color = 'var(--text)';
  text.style.background = 'var(--surface)';
  text.style.border = '1px solid var(--border)';
  text.style.borderRadius = '8px';
  label.append(text);

  const imagePicker = create('input', 'assistant-composer-images');
  imagePicker.type = 'file';
  imagePicker.accept = 'image/png,image/jpeg,image/webp,image/gif';
  imagePicker.multiple = true;
  imagePicker.setAttribute('aria-label', '이미지 첨부 선택 (최대 4개)');
  imagePicker.style.maxWidth = '100%';

  const send = create('button', 'assistant-composer-send', 'preferences-save');
  send.type = 'submit';
  send.textContent = '보내기';
  const cancel = create('button', 'assistant-composer-cancel', 'history-older');
  cancel.type = 'button';
  cancel.textContent = '요청 취소';
  cancel.hidden = true;

  const preview = create('div', 'assistant-composer-preview', 'message-attachment');
  preview.setAttribute('role', 'list');
  preview.setAttribute('aria-label', '선택한 이미지');
  preview.style.flex = '1 1 100%';
  const limits = create('small', 'assistant-composer-limits', 'settings-field-note');
  limits.textContent = '4,000자 · 이미지 4개까지 (개별 8MiB, 합계 20MiB)';
  const status = create('p', 'assistant-composer-status', 'settings-field-note');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.style.flex = '1 1 100%';
  form.append(label, imagePicker, send, cancel, preview, limits, status);
  reader.append(form);

  let disposed = false;
  let selectedPage = '';
  let shown = true;
  let composing = false;
  let finishingComposition = false;
  let pending: OwnedRequest | null = null;
  let uncertain: OwnedRequest | null = null;
  let superseding = false;
  let contextEpoch = 0;
  let images: ImageInput[] = [];
  let textRevision = 0;
  let imageRevision = 0;
  let readingEpoch = 0;
  let reading = false;
  const readers = new Set<FileReader>();
  const listeners = new AbortController();
  const conversationId = (): string | null => voiceStart.dataset.conversationId || null;
  let selectedConversation = conversationId();
  const active = () => !disposed && shown && selectedPage === 'voice';
  const samePayload = (owner: OwnedRequest) => owner.conversationId === conversationId()
    && owner.textRevision === textRevision && owner.imageRevision === imageRevision
    && owner.text === text.value && JSON.stringify(owner.images) === JSON.stringify(images);

  function updateControls(): void {
    form.hidden = !active();
    form.setAttribute('aria-busy', String(Boolean(pending) || reading));
    const changed = pending && (pending.text !== text.value || pending.imageRevision !== imageRevision);
    send.disabled = !active() || reading || superseding || Boolean(pending && !changed);
    send.textContent = uncertain && samePayload(uncertain) ? '결과 다시 확인' : '보내기';
    cancel.hidden = !pending || !active();
    cancel.disabled = !pending || !active();
    imagePicker.disabled = !active();
    text.disabled = !active();
  }

  function abortRead(): void {
    readingEpoch++;
    reading = false;
    for (const reader of readers) {
      try { reader.abort(); } catch { /* Already settled. */ }
    }
    readers.clear();
  }

  function retirePending(message?: string): void {
    const owned = pending;
    if (!owned) return;
    pending = null; // Fence even a completion that settles before native cancellation.
    if (message && active()) status.textContent = message;
    updateControls();
    try {
      void invoke('cancel_assistant_input', { tokenId: owned.tokenId }).catch(() => {
        // Late errors from a retired request never change the current conversation.
      });
    } catch { /* The owner's result is already fenced. */ }
  }

  function cancelUncertain(tokenId: string): void {
    try { void invoke('cancel_assistant_input', { tokenId }).catch(() => {}); }
    catch { /* The result was already rejected by the receipt validator. */ }
  }

  const observer = new MutationObserver(() => syncSelection());
  observer.observe(voiceStart, { attributes: true, attributeFilter: ['data-conversation-id'] });

  function syncSelection(): void {
    const changes = observer.takeRecords();
    const current = conversationId();
    if (current === selectedConversation && changes.length === 0) return;
    contextEpoch++;
    if (pending) retirePending('대화가 바뀌어 요청을 취소했습니다. 작성한 내용은 남아 있습니다.');
    if (reading) abortRead();
    uncertain = null;
    selectedConversation = current;
    updateControls();
  }

  function renderImages(): void {
    preview.replaceChildren();
    for (const [index, image] of images.entries()) {
      const item = root.createElement('div');
      item.setAttribute('role', 'listitem');
      const name = root.createElement('span');
      name.textContent = `${image.name} · ${Math.ceil(image.data.length * 3 / 4 / 1024)}KiB`;
      const remove = root.createElement('button');
      remove.type = 'button';
      remove.className = 'history-older';
      remove.textContent = '제거';
      remove.setAttribute('aria-label', `${image.name} 첨부 제거`);
      remove.addEventListener('click', () => {
        if (!active()) return;
        retirePending('입력이 바뀌어 이전 요청을 취소했습니다.');
        uncertain = null;
        images.splice(index, 1);
        imageRevision++;
        renderImages();
        updateControls();
      }, { signal: listeners.signal });
      item.append(name, remove);
      preview.append(item);
    }
  }

  async function pickImages(): Promise<void> {
    syncSelection();
    if (!active()) return;
    const chosen = Array.from(imagePicker.files ?? []);
    imagePicker.value = ''; // Re-selecting the same file is an explicit new picker event.
    if (!chosen.length) return;
    abortRead();
    // The read epoch fences older picker completions without changing a submitted draft.
    const epoch = readingEpoch;
    if (chosen.length > MAX_IMAGES
      || chosen.some(file => !IMAGE_TYPES.has(file.type) || !Number.isSafeInteger(file.size)
        || file.size <= 0 || file.size > MAX_IMAGE_BYTES
        || !file.name || Array.from(file.name).length > 128 || /[\\/\x00-\x1f\x7f]/.test(file.name))
      || chosen.reduce((size, file) => size + file.size, 0) > MAX_BATCH_BYTES) {
      status.textContent = 'PNG, JPEG, WebP, GIF 이미지 4개까지 선택할 수 있습니다. 개별 8MiB, 합계 20MiB 이하로 해 주세요.';
      return;
    }
    retirePending('입력이 바뀌어 이전 요청을 취소했습니다.');
    uncertain = null;
    reading = true;
    status.textContent = '선택한 이미지를 준비하고 있습니다…';
    updateControls();
    try {
      const copied = await Promise.all(chosen.map(file => readImage(file, readers)));
      syncSelection();
      if (!active() || epoch !== readingEpoch) return;
      images = copied;
      imageRevision++;
      renderImages();
      status.textContent = `${images.length}개 이미지를 첨부했습니다.`;
    } catch {
      if (active() && epoch === readingEpoch) status.textContent = '이미지를 읽지 못했습니다. 다시 선택해 주세요.';
    } finally {
      if (epoch === readingEpoch) {
        reading = false;
        readers.clear();
        updateControls();
      }
    }
  }

  async function submit(): Promise<void> {
    syncSelection();
    if (!active() || superseding || reading || composing) return;
    const message = text.value;
    if (pending && pending.text === message && pending.imageRevision === imageRevision) return;
    if (!message.trim() && !images.length) {
      status.textContent = '메시지를 입력해 주세요. 이미지는 메시지와 함께 보낼 수 있습니다.';
      return;
    }
    if (Array.from(message).length > MAX_CHARACTERS
      || new TextEncoder().encode(message).byteLength > MAX_TEXT_BYTES) {
      status.textContent = '메시지는 4,000자 및 UTF-8 16,000바이트 이하여야 합니다.';
      return;
    }
    if (Array.from(message).some(character => character < ' ' && character !== '\n' && character !== '\t')) {
      status.textContent = '메시지에 사용할 수 없는 문자가 포함되어 있습니다.';
      return;
    }
    if (pending) {
      const previous = pending;
      const epoch = contextEpoch;
      pending = null; // Fence the old completion before invoking cooperative cancellation.
      superseding = true;
      status.textContent = '이전 요청의 취소를 확인하고 있습니다…';
      updateControls();
      let cancelled = false;
      try { cancelled = await invoke<boolean>('cancel_assistant_input', { tokenId: previous.tokenId }) === true; }
      catch { /* A missing cancellation receipt is not an acknowledged cancellation. */ }
      superseding = false;
      syncSelection();
      if (!active() || contextEpoch !== epoch || selectedConversation !== previous.conversationId) {
        updateControls();
        return;
      }
      if (!cancelled) {
        status.textContent = '이전 요청의 취소를 확인하지 못했습니다. 작성한 내용은 남아 있습니다.';
        updateControls();
        return;
      }
      // Use the latest edited text and attachments after acknowledgement.
      await submit();
      return;
    }
    let tokenId: string;
    let requestId: string;
    try {
      tokenId = crypto.randomUUID();
      requestId = uncertain && samePayload(uncertain) ? uncertain.requestId : crypto.randomUUID().replace(/-/g, '');
    } catch {
      status.textContent = '안전한 요청 번호를 만들지 못했습니다. 다시 시도해 주세요.';
      return;
    }
    if (!UUID_V4.test(tokenId) || !HEX_32.test(requestId)) {
      status.textContent = '안전한 요청 번호를 만들지 못했습니다. 다시 시도해 주세요.';
      return;
    }
    const owned: OwnedRequest = {
      tokenId, requestId, conversationId: selectedConversation, text: message, textRevision, imageRevision,
      images: images.map(image=>({...image})),
    };
    const copiedImages = images.map(image => ({ ...image }));
    pending = owned;
    status.textContent = '답변을 요청했습니다…';
    updateControls();
    try {
      const result: unknown = await invoke('submit_assistant_input', {
        input: { request_id: requestId, conversation_id: owned.conversationId, text: message, images: copiedImages },
        tokenId,
      });
      syncSelection();
      if (pending !== owned || !active() || selectedConversation !== owned.conversationId
        || textRevision !== owned.textRevision || imageRevision !== owned.imageRevision) return;
      pending = null;
      updateControls();
      if (!isReceipt(result, owned)) {
        uncertain = owned;
        status.textContent = safeFailure(null);
        // Malformed or mismatched native results may still conceal an active turn.
        cancelUncertain(tokenId);
        updateControls();
        return;
      }
      if (completedReceipt(result)) {
        uncertain = null;
        if (textRevision === owned.textRevision && text.value === owned.text) {
          text.value = '';
          textRevision++;
        }
        if (imageRevision === owned.imageRevision) {
          images = [];
          imageRevision++;
          renderImages();
        }
        status.textContent = '답변이 저장되었습니다.';
        try { onCommitted(result.conversation_id); }
        catch { status.textContent = '답변은 저장됐지만 대화 기록을 새로 고치지 못했습니다.'; }
      } else if (result.state === 'completed') {
        uncertain = owned;
        // A success-shaped response without a valid durable receipt is uncertain.
        status.textContent = safeFailure(null);
        cancelUncertain(tokenId);
      } else if (result.state === 'cancelled') {
        uncertain = null;
        status.textContent = safeFailure('assistant_input_cancelled');
      } else if (result.state === 'superseded') {
        uncertain = null;
        status.textContent = safeFailure('assistant_input_superseded');
      } else {
        status.textContent = safeFailure(result.error_code);
        if (result.state === 'running') {
          uncertain = owned;
        } else {
          uncertain = null;
        }
      }
      updateControls();
    } catch (error) {
      if (pending !== owned || !active()) return;
      pending = null;
      uncertain = owned;
      updateControls();
      status.textContent = safeFailure(error);
    }
  }

  form.addEventListener('submit', event => { event.preventDefault(); void submit(); }, { signal: listeners.signal });
  text.addEventListener('input', () => {
    retirePending('입력이 바뀌어 이전 요청을 취소했습니다.');
    uncertain = null;
    textRevision++;
    updateControls();
  }, { signal: listeners.signal });
  text.addEventListener('compositionstart', () => { composing = true; }, { signal: listeners.signal });
  text.addEventListener('compositionend', () => { composing = false; finishingComposition = true; }, { signal: listeners.signal });
  text.addEventListener('keydown', event => {
    if (event.key !== 'Enter') finishingComposition = false;
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing || event.keyCode === 229) return;
    event.preventDefault();
    if (finishingComposition) return;
    void submit();
  }, { signal: listeners.signal });
  text.addEventListener('keyup', () => { finishingComposition = false; }, { signal: listeners.signal });
  text.addEventListener('pointerup', () => { finishingComposition = false; }, { signal: listeners.signal });
  imagePicker.addEventListener('change', () => { void pickImages(); }, { signal: listeners.signal });
  cancel.addEventListener('click', () => retirePending('요청 취소를 보냈습니다. 작성한 내용은 남아 있습니다.'), { signal: listeners.signal });
  root.defaultView?.addEventListener('pagehide', () => {
    contextEpoch++;
    retirePending();
    if (reading) abortRead();
    updateControls();
  }, { signal: listeners.signal });

  const controller = {
    select(next: string): void {
      if (disposed) return;
      syncSelection();
      if (selectedPage !== next) {
        contextEpoch++;
        retirePending();
        if (reading) abortRead();
        selectedPage = next;
      }
      updateControls();
    },
    visible(flag: boolean): void {
      if (disposed) return;
      if (!flag && shown) {
        contextEpoch++;
        retirePending();
        if (reading) abortRead();
      }
      shown = flag;
      syncSelection();
      updateControls();
    },
    dispose(): void {
      if (disposed) return;
      contextEpoch++;
      retirePending();
      abortRead();
      disposed = true;
      listeners.abort();
      observer.disconnect();
      form.remove();
      mounted.delete(root);
    },
  };
  mounted.set(root, controller);
  updateControls();
  return controller;
}
