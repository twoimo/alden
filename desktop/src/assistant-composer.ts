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
  files: ImageInput[];
};

const MAX_CHARACTERS = 4_000;
const MAX_TEXT_BYTES = 16_000;
const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_BATCH_BYTES = 20 * 1024 * 1024;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const DOCUMENT_SUFFIXES = new Set(['txt','md','csv','tsv','json','yaml','yml','log','py','js','ts','rs','toml','ini','sql','xml','html','htm','docx','xlsx','pptx','pdf','rtf','doc']);
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
const HEX_32 = /^[a-f0-9]{32}$/;
const UUID_V4 = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const NATIVE_CODE = /^(?:Error:\s*)?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?::\s.*)?$/;
const ERROR_DETAILS: Readonly<Record<string, string>> = {
  python_spawn_failed: '대화 처리기를 시작하지 못했습니다. 실행 환경을 확인해 주세요.',
  python_wait_failed: '대화 처리기의 종료를 확인하지 못했습니다. 같은 요청을 다시 확인해 주세요.',
  python_timed_out: '대화 처리 시간이 초과됐습니다. 작성한 내용은 남아 있습니다.',
  python_cancelled: '대화 처리가 취소되었습니다. 작성한 내용은 남아 있습니다.',
  python_output_too_large: '대화 처리 결과가 허용 범위를 넘었습니다. 작성한 내용은 남아 있습니다.',
  python_failed: '대화 처리기가 정상적으로 종료되지 못했습니다. 작성한 내용은 남아 있습니다.',
  python_output_empty: '대화 처리 결과를 받지 못했습니다. 같은 요청을 다시 확인해 주세요.',
  python_json_invalid: '대화 처리 결과를 해석하지 못했습니다. 같은 요청을 다시 확인해 주세요.',
  action_not_allowed: '입력 요청이나 처리 결과를 확인하지 못했습니다. 작성한 내용은 남아 있습니다.',
  state_io_failed: '대화 상태를 읽거나 저장하지 못했습니다. 저장소 상태를 확인해 주세요.',
  resource_missing: '대화 처리에 필요한 앱 파일을 찾지 못했습니다. 앱 설치를 확인해 주세요.',
  resource_unsafe: '대화 처리에 필요한 앱 파일을 검증하지 못했습니다. 앱 설치를 확인해 주세요.',
  development_resources_disabled: '대화 처리 파일의 실행 경로를 사용할 수 없습니다. 앱 설치를 확인해 주세요.',
  python_environment_missing_or_unsafe: '대화 처리 실행 환경을 검증하지 못했습니다. 실행 환경을 확인해 주세요.',
  voice_session_already_running: '음성 대화가 실행 중이라 입력을 처리하지 못했습니다. 음성 대화가 끝난 뒤 다시 시도해 주세요.',
  voice_session_process_check_failed: '음성 대화 실행 상태를 확인하지 못했습니다. 잠시 뒤 다시 시도해 주세요.',
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
  assistant_image_context_changed: '이전 사진의 대화 근거가 바뀌었습니다. 내용을 확인한 뒤 다시 요청해 주세요.',
  generation_metadata_invalid: '모델 응답의 대화·측정 근거를 확인하지 못했습니다. 작성한 내용은 남아 있습니다.',
  assistant_image_pixel_budget: '이미지 픽셀 수 한도를 넘었습니다. 해상도를 줄여 주세요.',
  assistant_image_store_unavailable: '이미지를 저장할 수 없습니다. 저장소 상태를 확인해 주세요.',
  assistant_image_model_unsupported: '선택한 모델이 이미지 입력을 지원하지 않습니다. 모델 설정을 확인해 주세요.',
  assistant_link_budget: '현재 메시지의 링크는 최대 2개까지 읽을 수 있습니다.',
  assistant_link_invalid: '링크 형식이나 길이를 확인해 주세요.',
  assistant_link_fetch_timeout: '링크를 읽는 시간이 초과됐습니다. 잠시 후 다시 요청해 주세요.',
  assistant_link_fetch_unavailable: '링크 읽기 기능을 사용할 수 없습니다.',
  assistant_link_context_changed: '링크의 저장 자료나 대화 근거가 바뀌었습니다. 내용을 확인한 뒤 다시 요청해 주세요.',
  assistant_external_context_budget: '문서와 링크의 내용 한도를 넘었습니다. 자료 수를 줄여 주세요.',
  assistant_file_invalid: '파일 이름이나 첨부 바이트를 확인하지 못했습니다. 다시 선택해 주세요.',
  assistant_file_budget: '사진과 문서를 합쳐 4개/20MiB까지, 문서는 개별 5MiB까지 보낼 수 있습니다.',
  assistant_file_store_unavailable: '첨부 파일을 저장하지 못했습니다. 저장소 상태를 확인해 주세요.',
  assistant_file_context_changed: '이전 파일의 내용이나 대화 근거가 바뀌었습니다. 다시 첨부해 주세요.',
  assistant_file_parser_timeout: '문서 해석 시간이 초과됐습니다. 작은 문서로 다시 시도해 주세요.',
  assistant_file_parser_unavailable: '문서 내용을 해석하지 못했습니다. 다른 형식으로 저장해 주세요.',
  assistant_file_parser_output_budget: '문서 해석 결과가 허용 범위를 넘었습니다.',
  document_format_unsupported: '지원하지 않는 문서 형식입니다. 텍스트, PDF, Word, Excel, PowerPoint를 선택해 주세요.',
  document_format_mismatch: '파일 내용과 형식이 일치하지 않습니다.',
  document_encoding_unavailable: '문서 인코딩을 읽지 못했습니다. UTF-8로 저장해 주세요.',
  document_has_no_text: '문서에서 읽을 수 있는 텍스트를 찾지 못했습니다.',
  document_pdf_has_no_text: 'PDF에 읽을 수 있는 텍스트가 없습니다. 스캔 문서의 문자 인식은 지원하지 않습니다.',
  document_pdf_text_unavailable: 'PDF 텍스트를 읽지 못했습니다. 잠금이나 스캔 문서 여부를 확인해 주세요.',
  document_contains_binary_text: '문서에 읽을 수 없는 바이너리 내용이 있습니다.',
  document_archive_invalid: '문서 압축 구조가 유효하지 않습니다.',
  document_archive_budget_or_shape: '문서 압축 구조나 해제 크기가 허용 범위를 넘었습니다.',
  document_xml_invalid: '문서의 텍스트 구조가 유효하지 않습니다.',
  document_xml_encoding_unsupported: '문서 구조의 문자 인코딩을 지원하지 않습니다.',
  document_xml_entities_unsupported: '문서의 외부 개체를 읽을 수 없습니다.',
  document_native_parser_failed: '시스템 문서 읽기 기능이 문서를 해석하지 못했습니다.',
  document_native_parser_unavailable: '시스템 문서 읽기 기능을 사용할 수 없습니다.',
  document_slide_relation_invalid: '슬라이드의 내부 참조를 확인하지 못했습니다.',
  document_sheet_relation_invalid: '시트의 내부 참조를 확인하지 못했습니다.',
  document_shared_string_invalid: '시트의 텍스트 참조를 확인하지 못했습니다.',
  document_byte_budget: '문서 크기가 개별 5MiB 한도를 넘었습니다.',
  assistant_model_unavailable: '대화 모델에 연결할 수 없습니다. 설정을 확인해 주세요.',
  model_unavailable: '대화 모델에 연결할 수 없습니다. 설정을 확인해 주세요.',
  voice_model_configuration_invalid: '대화 모델 설정이 올바르지 않습니다. 모델 설정을 확인해 주세요.',
  local_llm_model_not_ready: '선택한 로컬 모델이 준비되지 않았습니다. 준비 상태를 확인해 주세요.',
  local_llm_stream_incomplete: '모델 답변이 완결되지 않았습니다. 작성한 내용은 남아 있습니다.',
  local_llm_reply_incomplete: '모델 답변이 완결되지 않았습니다. 작성한 내용은 남아 있습니다.',
  local_llm_reply_truncated: '답변이 길이 한도로 중단됐습니다. 요청을 나눠 다시 보내 주세요.',
  local_llm_tool_call_unsupported: '이 대화에서는 모델의 도구 호출을 실행할 수 없습니다. 작성한 내용은 남아 있습니다.',
  routed_model_not_ready: '선택한 모델이 아직 준비되지 않았습니다. 작성한 내용은 남아 있습니다.',
  routed_llm_quota: '선택한 모델의 요청 한도로 거절됐습니다. 작성한 내용은 남아 있습니다.',
  routed_llm_authentication: '선택한 모델의 인증을 확인해 주세요. 작성한 내용은 남아 있습니다.',
  routed_llm_unavailable: '선택한 모델에 연결하지 못했습니다. 작성한 내용은 남아 있습니다.',
  routed_model_mismatch: '선택한 모델과 다른 모델의 응답이어서 답변을 저장하지 않았습니다.',
  routed_llm_generation_failed: '선택한 모델이 답변 생성을 완료하지 못했습니다. 작성한 내용은 남아 있습니다.',
  routed_llm_invalid_response: '모델 응답 형식이 올바르지 않아 답변을 확인하지 못했습니다.',
  routed_llm_response_budget: '수신한 모델 응답이 허용 크기를 넘었습니다. 작성한 내용은 남아 있습니다.',
  model_effort_not_supported: '선택한 모델이 설정된 추론 강도를 지원하지 않습니다. 모델 설정을 확인해 주세요.',
  routed_llm_incomplete_response: '모델 답변이 완결되지 않았습니다. 작성한 내용은 남아 있습니다.',
  routed_llm_reply_truncated: '답변이 길이 한도로 중단됐습니다. 요청을 나눠 다시 보내 주세요.',
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

function readImage(file: File, readers: Set<FileReader>, byteLimit = MAX_IMAGE_BYTES): Promise<ImageInput> {
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
        ? /^data:[^,]*;base64,([A-Za-z0-9+/]+={0,2})$/.exec(value) : null;
      if (!match || match[1].length % 4 !== 0) { fail(); return; }
      const data = match[1];
      const byteLength = data.length / 4 * 3 - (data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0);
      if (byteLength !== file.size || byteLength > byteLimit) { fail(); return; }
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
  const filePicker = create('input', 'assistant-composer-files');
  filePicker.type = 'file';
  filePicker.accept = [...DOCUMENT_SUFFIXES].map(suffix => `.${suffix}`).join(',');
  filePicker.multiple = true;
  filePicker.setAttribute('aria-label', '문서 첨부 선택 (사진과 합계 최대 4개)');
  filePicker.style.maxWidth = '100%';
  const imageLabel = create('label', 'assistant-composer-image-label', 'assistant-attachment-picker');
  imageLabel.textContent = '사진 추가'; imageLabel.append(imagePicker);
  const fileLabel = create('label', 'assistant-composer-file-label', 'assistant-attachment-picker');
  fileLabel.textContent = '문서 추가'; fileLabel.append(filePicker);
  const attachmentControls = create('div', 'assistant-composer-attachment-controls', 'assistant-attachment-controls');
  attachmentControls.append(imageLabel, fileLabel);

  const send = create('button', 'assistant-composer-send', 'preferences-save');
  send.type = 'submit';
  send.textContent = '보내기';
  const cancel = create('button', 'assistant-composer-cancel', 'history-older');
  cancel.type = 'button';
  cancel.textContent = '요청 취소';
  cancel.hidden = true;

  const preview = create('div', 'assistant-composer-preview', 'message-attachment');
  preview.setAttribute('role', 'list');
  preview.setAttribute('aria-label', '선택한 첨부');
  preview.style.flex = '1 1 100%';
  const limits = create('small', 'assistant-composer-limits', 'settings-field-note');
  limits.textContent = '4,000자 · 사진·문서 합계 4개/20MiB · 사진 8MiB, 문서 5MiB까지';
  const status = create('p', 'assistant-composer-status', 'settings-field-note');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.style.flex = '1 1 100%';
  form.append(label, attachmentControls, send, cancel, preview, limits, status);
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
  let files: ImageInput[] = [];
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
    && owner.text === text.value && JSON.stringify(owner.images) === JSON.stringify(images)
    && JSON.stringify(owner.files) === JSON.stringify(files);

  function updateControls(): void {
    form.hidden = !active();
    form.dataset.ownedRequest = String(Boolean(pending || uncertain) || reading || superseding);
    form.setAttribute('aria-busy', String(Boolean(pending) || reading));
    const changed = pending && (pending.text !== text.value || pending.imageRevision !== imageRevision);
    send.disabled = !active() || reading || superseding || Boolean(pending && !changed);
    send.textContent = uncertain && samePayload(uncertain) ? '결과 다시 확인' : '보내기';
    cancel.hidden = !pending || !active();
    cancel.disabled = !pending || !active();
    imagePicker.disabled = !active();
    filePicker.disabled = !active();
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

  function retirePending(message?: string, reason='request_retired'): void {
    const owned = pending;
    if (!owned) return;
    form.dataset.lastRetirement = reason;
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
    if (pending) retirePending('대화가 바뀌어 요청을 취소했습니다. 작성한 내용은 남아 있습니다.','conversation_changed');
    if (reading) abortRead();
    uncertain = null;
    selectedConversation = current;
    updateControls();
  }

  function renderImages(): void {
    preview.replaceChildren();
    const attachments = [...images.map((image,index)=>({image,index,kind:'image'})),
      ...files.map((image,index)=>({image,index,kind:'file'}))];
    for (const {index,image,kind} of attachments) {
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
        if (kind === 'image') images.splice(index, 1);
        else files.splice(index, 1);
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
    if (chosen.length + files.length > MAX_IMAGES
      || chosen.some(file => !IMAGE_TYPES.has(file.type) || !Number.isSafeInteger(file.size)
        || file.size <= 0 || file.size > MAX_IMAGE_BYTES
        || !file.name || Array.from(file.name).length > 128 || /[\\/\x00-\x1f\x7f]/.test(file.name))
      || chosen.reduce((size, file) => size + file.size, 0) + files.reduce((size,file)=>size+file.data.length*3/4,0) > MAX_BATCH_BYTES) {
      status.textContent = 'PNG, JPEG, WebP, GIF 이미지 4개까지 선택할 수 있습니다. 개별 8MiB, 합계 20MiB 이하로 해 주세요.';
      return;
    }
    retirePending('입력이 바뀌어 이전 요청을 취소했습니다.','input_changed');
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

  async function pickFiles(): Promise<void> {
    syncSelection();
    if (!active()) return;
    const chosen = Array.from(filePicker.files ?? []);
    filePicker.value = '';
    if (!chosen.length) return;
    abortRead();
    const epoch = readingEpoch;
    if (chosen.some(file => !DOCUMENT_SUFFIXES.has(file.name.split('.').at(-1)?.toLowerCase() ?? ''))) {
      status.textContent = ERROR_DETAILS.document_format_unsupported;
      return;
    }
    if (chosen.length + images.length > MAX_IMAGES
      || chosen.some(file => !DOCUMENT_SUFFIXES.has(file.name.split('.').at(-1)?.toLowerCase() ?? '')
        || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > MAX_FILE_BYTES
        || !file.name.trim() || Array.from(file.name).length > 128 || /[\\/\p{Cc}\p{Cs}]/u.test(file.name))
      || chosen.reduce((size,file)=>size+file.size,0) + images.reduce((size,image)=>size+image.data.length*3/4,0) > MAX_BATCH_BYTES) {
      status.textContent = ERROR_DETAILS.assistant_file_budget;
      return;
    }
    retirePending('첨부가 바뀌어 이전 요청을 취소했습니다.');
    uncertain = null;
    reading = true;
    status.textContent = '선택한 문서를 준비하고 있습니다…';
    updateControls();
    try {
      const copied = await Promise.all(chosen.map(file=>readImage(file,readers,MAX_FILE_BYTES)));
      syncSelection();
      if (!active() || epoch !== readingEpoch) return;
      files = copied;
      imageRevision++;
      renderImages();
      status.textContent = `${files.length}개 문서를 첨부했습니다.`;
    } catch {
      if (active() && epoch === readingEpoch) status.textContent = '문서 바이트를 읽지 못했습니다. 다시 선택해 주세요.';
    } finally {
      if (epoch === readingEpoch) { reading=false;readers.clear();updateControls(); }
    }
  }

  async function submit(): Promise<void> {
    syncSelection();
    if (!active() || superseding || reading || composing) return;
    const message = text.value;
    if (pending && pending.text === message && pending.imageRevision === imageRevision) return;
    if (!message.trim() && !images.length && !files.length) {
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
      files: files.map(file=>({...file})),
    };
    const copiedImages = images.map(image => ({ ...image }));
    pending = owned;
    delete form.dataset.lastRetirement;
    status.textContent = '답변을 요청했습니다…';
    updateControls();
    try {
      const result: unknown = await invoke('submit_assistant_input', {
        input: { request_id: requestId, conversation_id: owned.conversationId, text: message, images: copiedImages,
          ...(owned.files.length ? {files:owned.files.map(file=>({...file}))} : {}) },
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
          files = [];
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
  filePicker.addEventListener('change', () => { void pickFiles(); }, { signal: listeners.signal });
  cancel.addEventListener('click', () => retirePending('요청 취소를 보냈습니다. 작성한 내용은 남아 있습니다.','user_cancelled'), { signal: listeners.signal });
  root.defaultView?.addEventListener('pagehide', () => {
    contextEpoch++;
    retirePending(undefined,'page_closed');
    if (reading) abortRead();
    updateControls();
  }, { signal: listeners.signal });

  const controller = {
    select(next: string): void {
      if (disposed) return;
      syncSelection();
      if (selectedPage !== next) {
        contextEpoch++;
        retirePending(undefined,'page_changed');
        if (reading) abortRead();
        selectedPage = next;
      }
      updateControls();
    },
    visible(flag: boolean): void {
      if (disposed) return;
      if (!flag && shown) {
        contextEpoch++;
        retirePending(undefined,'window_hidden');
        if (reading) abortRead();
      }
      shown = flag;
      syncSelection();
      updateControls();
    },
    dispose(): void {
      if (disposed) return;
      contextEpoch++;
      retirePending(undefined,'disposed');
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
