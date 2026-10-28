import type { KnowledgeNode } from './graph-model';

export type OriginalBodyRequest = Readonly<{ offset_bytes: number; max_bytes: number }>;
export interface OriginalBodyReader {
  read(request: OriginalBodyRequest): Promise<unknown>;
  /** Rechecked after verification and again immediately before a DOM commit. */
  isCurrent(): boolean;
}
type Data = Readonly<Record<string, unknown>>;
const windowKeys = ['schema', 'kind', 'binding', 'capture', 'encoding', 'offset_bytes', 'end_bytes',
  'next_offset_bytes', 'total_bytes', 'text', 'text_sha256'];
const bindingKeys = ['document_id', 'target_id', 'version', 'raw_sha256', 'input_sha256'];
const captureKeys = ['manifest_sha256', 'source_sha256'];
const extractionKeys = ['schema', 'kind', 'binding', 'parser', 'processing_version', 'scope_kind',
  'input_sha256', 'input_bytes', 'extracted_text_sha256', 'extracted_text_bytes', 'segment_count',
  'body_complete', 'parser_truncated', 'status'];
const extractionBindingKeys = ['document_id', 'target_id', 'version', 'raw_sha256', 'detail_body_sha256'];
const nodeKeys = ['id', 'canonicalId', 'sourceTarget', 'sourceVersion', 'sourcePlatform'];
const sha = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const identity = (value: unknown): value is string => typeof value === 'string' && /^[\x21-\x7e]{1,256}$/.test(value);
const integer = (value: unknown, max: number, min = 0): value is number =>
  Number.isSafeInteger(value) && (value as number) >= min && (value as number) <= max;

/** Read only own data properties. Never invoke a getter added during an await. */
function snapshot(value: unknown, keys: string[], exact = false): { data: Data; current(): boolean } | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return null;
  const object = value as Record<string, unknown>;
  const prototype = Object.getPrototypeOf(object);
  const exactShape = () => {
    const own = Reflect.ownKeys(object);
    return own.length === keys.length && own.every(key => typeof key === 'string' && keys.includes(key));
  };
  if (exact && !exactShape()) return null;
  const descriptors = keys.map(key => Object.getOwnPropertyDescriptor(object, key));
  if (descriptors.some(item => item && !('value' in item)) || exact && descriptors.some(item => !item)) return null;
  const data: Record<string, unknown> = Object.create(null);
  keys.forEach((key, index) => { data[key] = descriptors[index]?.value; });
  return { data: Object.freeze(data), current: () => Object.getPrototypeOf(object) === prototype && (!exact || exactShape()) && keys.every((key, index) => {
    const actual = Object.getOwnPropertyDescriptor(object, key), prior = descriptors[index];
    return !prior ? !actual : !!actual && 'value' in actual && Object.is(actual.value, prior.value)
      && actual.enumerable === prior.enumerable && actual.configurable === prior.configurable && actual.writable === prior.writable;
  }) };
}

function utf8(value: unknown, maximumScalars = 24000): Uint8Array | null {
  if (typeof value !== 'string' || value.length > 24000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)) return null;
  let scalars = 0;
  for (const scalar of value) if (++scalars > maximumScalars || scalar.length === 1 && /[\ud800-\udfff]/.test(scalar)) return null;
  return new TextEncoder().encode(value);
}

export function originalBodyContext(node: KnowledgeNode, selected: Record<string, unknown> | null) {
  const n = snapshot(node, nodeKeys);
  const s = snapshot(selected, ['node_id', 'target_id', 'version', 'basis', 'platform', 'raw_sha256', 'body', 'source_extraction', 'capture']);
  const e = snapshot(s?.data.source_extraction, extractionKeys, true);
  const b = snapshot(e?.data.binding, extractionBindingKeys, true);
  if (!n || !s || !e || !b) return null;
  const nd = n.data, sd = s.data, ed = e.data, bd = b.data;
  const documentId = nd.canonicalId || nd.id;
  if (!identity(documentId) || !identity(nd.sourceVersion) || !identity(nd.sourceTarget)
    || nd.sourcePlatform !== 'files' || sd.basis !== 'source_record' || sd.platform !== 'files'
    || sd.node_id !== nd.id || sd.version !== nd.sourceVersion || sd.target_id !== nd.sourceTarget
    || !sha(sd.raw_sha256) || bd.document_id !== documentId || bd.version !== sd.version
    || bd.target_id !== sd.target_id || bd.raw_sha256 !== sd.raw_sha256 || !sha(bd.detail_body_sha256)
    || ed.schema !== 1 || ed.kind !== 'declared-original-file-extraction'
    || !['text-txt', 'text-md'].includes(ed.parser as string)
    || ed.processing_version !== 'document-segments-v1' || ed.scope_kind !== 'original-lines'
    || !sha(ed.input_sha256) || !integer(ed.input_bytes, 5242880, 1)
    || !sha(ed.extracted_text_sha256) || !integer(ed.extracted_text_bytes, 16000, 1)
    || !integer(ed.segment_count, 256, 1) || typeof ed.body_complete !== 'boolean' || typeof ed.parser_truncated !== 'boolean'
    || ed.body_complete && ed.parser_truncated
    || ed.status !== (ed.parser_truncated ? 'partial' : ed.body_complete ? 'complete' : 'unverified')) return null;
  const body = utf8(sd.body, 12000);
  if (!body) return null;
  const capture = sd.capture === undefined ? null : snapshot(sd.capture, ['status', ...captureKeys]);
  if (sd.capture !== undefined && !capture) return null;
  return { documentId, targetId: nd.sourceTarget, version: nd.sourceVersion, rawHash: sd.raw_sha256,
    inputHash: ed.input_sha256, totalBytes: ed.input_bytes, body, bodyHash: bd.detail_body_sha256,
    capture: capture?.data,
    current: () => n.current() && s.current() && e.current() && b.current() && (!capture || capture.current()) };
}
type BodyContext = NonNullable<ReturnType<typeof originalBodyContext>>;
export type OriginalBodyWindow = Readonly<{
  offset_bytes: number; end_bytes: number; next_offset_bytes: number | null; total_bytes: number;
  text: string; manifest_sha256: string; source_sha256: string;
}>;
type OriginalBodyCursor = Omit<OriginalBodyWindow, 'text'>;

async function digest(bytes: Uint8Array): Promise<string> {
  const result = await globalThis.crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>);
  return Array.from(new Uint8Array(result), byte => byte.toString(16).padStart(2, '0')).join('');
}

/** Returns copied primitives plus a live guard for the separate DOM reaction. */
export async function verifiedOriginalBodyWindow(response: unknown, context: BodyContext, request: OriginalBodyRequest,
  previous: OriginalBodyCursor | null = null): Promise<{ window: OriginalBodyWindow; current(): boolean } | null> {
  const r = snapshot(response, ['ok', 'body_window'], true);
  const w = snapshot(r?.data.body_window, windowKeys, true);
  const b = snapshot(w?.data.binding, bindingKeys, true);
  const c = snapshot(w?.data.capture, captureKeys, true);
  if (!r || !w || !b || !c || r.data.ok !== true || !context.current()) return null;
  const wd = w.data, bd = b.data, cd = c.data;
  const offset = request.offset_bytes, maximum = request.max_bytes;
  if (!integer(offset, 5242880) || !integer(maximum, 16000, 4)
    || wd.schema !== 1 || wd.kind !== 'declared-original-utf8-window' || wd.encoding !== 'utf-8'
    || !bindingKeys.slice(0, 3).every(key => identity(bd[key])) || !bindingKeys.slice(3).every(key => sha(bd[key]))
    || bd.document_id !== context.documentId || bd.target_id !== context.targetId || bd.version !== context.version
    || bd.raw_sha256 !== context.rawHash || bd.input_sha256 !== context.inputHash
    || !captureKeys.every(key => sha(cd[key])) || !sha(wd.text_sha256)
    || !integer(wd.offset_bytes, 5242880) || wd.offset_bytes !== offset
    || !integer(wd.end_bytes, 5242880) || !integer(wd.total_bytes, 5242880)
    || wd.total_bytes !== context.totalBytes || wd.end_bytes < offset || wd.end_bytes > wd.total_bytes
    || wd.end_bytes - offset > maximum
    || (wd.end_bytes === wd.total_bytes ? wd.next_offset_bytes !== null
      : wd.next_offset_bytes !== wd.end_bytes || wd.end_bytes <= offset)
    || previous && (previous.total_bytes !== wd.total_bytes || previous.manifest_sha256 !== cd.manifest_sha256
      || previous.source_sha256 !== cd.source_sha256)
    || context.capture?.source_sha256 !== undefined && context.capture.source_sha256 !== cd.source_sha256
    || context.capture?.manifest_sha256 !== undefined && context.capture.manifest_sha256 !== cd.manifest_sha256) return null;
  const bytes = utf8(wd.text);
  if (!bytes || bytes.byteLength !== wd.end_bytes - offset || bytes.byteLength > maximum) return null;
  const current = () => r.current() && w.current() && b.current() && c.current() && context.current();
  try {
    const [textHash, bodyHash] = await Promise.all([digest(bytes), digest(context.body)]);
    if (!current() || textHash !== wd.text_sha256 || bodyHash !== context.bodyHash
      || offset === 0 && wd.end_bytes === wd.total_bytes && textHash !== context.inputHash) return null;
    return { window: Object.freeze({ offset_bytes: offset, end_bytes: wd.end_bytes, next_offset_bytes: wd.next_offset_bytes as number | null,
      total_bytes: wd.total_bytes, text: wd.text as string, manifest_sha256: cd.manifest_sha256 as string,
      source_sha256: cd.source_sha256 as string }), current };
  } catch { return null; }
}

export function renderOriginalBodyWindow(bodyElement: HTMLElement, node: KnowledgeNode,
  selected: Record<string, unknown> | null, reader?: OriginalBodyReader): void {
  const root = bodyElement.ownerDocument;
  root.querySelectorAll('.knowledge-original-body-window').forEach(item => item.remove());
  const context = originalBodyContext(node, selected);
  if (!reader || !context || !reader.isCurrent()) return;
  const section = root.createElement('section'); section.className = 'knowledge-original-body-window';
  const title = root.createElement('h3'); title.textContent = '보존 원문 읽기';
  const note = root.createElement('p'); note.textContent = '한 부분씩 표시합니다. 수집 당시의 추출 범위는 위 안내를 따릅니다.';
  const status = root.createElement('p'); status.setAttribute('role', 'status'); status.textContent = '읽기를 누르면 첫 부분을 불러옵니다.';
  const content = root.createElement('pre'); content.className = 'knowledge-original-body-text'; content.hidden = true;
  const actions = root.createElement('div'); actions.className = 'knowledge-original-body-actions';
  const next = root.createElement('button'); next.type = 'button'; next.textContent = '첫 부분 읽기';
  const reset = root.createElement('button'); reset.type = 'button'; reset.textContent = '처음부터 읽기'; reset.hidden = true;
  actions.append(next, reset); section.append(title, note, status, content, actions); bodyElement.before(section);
  let page: OriginalBodyCursor | null = null, busy = false;
  const clearContent = () => { content.textContent = ''; content.hidden = true; };
  const current = () => section.isConnected && context.current() && reader.isCurrent() && (() => {
    const pane = root.querySelector<HTMLDialogElement>('dialog#knowledge-note-pane');
    return !pane || pane.open && !pane.hidden && pane.dataset.nodeId === node.id && !pane.closest('[hidden]');
  })();
  const load = async (offset: number) => {
    if (busy || !current()) return;
    // Clear while this selection still owns the UI, before any asynchronous
    // invalidation can prevent a later failure/status commit.
    clearContent();
    busy = true; next.disabled = true; reset.disabled = true; section.setAttribute('aria-busy', 'true');
    section.dataset.state = 'loading'; status.textContent = '원문을 불러오는 중입니다…';
    const request = Object.freeze({ offset_bytes: offset, max_bytes: 16000 });
    try {
      const response = await reader.read(request);
      if (!current()) return;
      const verified = await verifiedOriginalBodyWindow(response, context, request, page);
      // Hash verification and this continuation are separate Promise reactions.
      if (!current()) return;
      if (!verified || !verified.current()) throw new Error('unavailable');
      const { text, ...cursor } = verified.window;
      page = Object.freeze(cursor);
      content.textContent = text; content.hidden = false; content.scrollTop = 0;
      section.dataset.state = page.next_offset_bytes === null ? 'last' : 'part';
      status.textContent = page.next_offset_bytes === null ? '원문의 마지막 부분입니다.' : '원문의 일부입니다. 다음 부분을 이어서 읽을 수 있습니다.';
      next.textContent = '다음 부분 읽기'; reset.hidden = false;
    } catch {
      if (current()) { clearContent(); section.dataset.state = 'failure'; status.textContent = '원문을 불러오지 못했습니다. 다시 시도해 주세요.'; }
    } finally {
      busy = false;
      if (current()) { section.removeAttribute('aria-busy'); next.disabled = page?.next_offset_bytes === null; reset.disabled = false; }
    }
  };
  next.addEventListener('click', () => { if (!page || page.next_offset_bytes !== null) void load(page?.next_offset_bytes ?? 0); });
  reset.addEventListener('click', () => { void load(0); });
}
