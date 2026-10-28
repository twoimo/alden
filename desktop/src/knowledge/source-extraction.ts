import type { KnowledgeNode } from './graph-model';
import { matchesSourceReference } from './graph-model';

type ObjectValue = Record<string, unknown>;
export type SourceExtractionStatus = 'complete' | 'partial' | 'unverified';

const packetKeys = ['schema', 'kind', 'binding', 'parser', 'processing_version', 'scope_kind',
  'input_sha256', 'input_bytes', 'extracted_text_sha256', 'extracted_text_bytes', 'segment_count',
  'body_complete', 'parser_truncated', 'status'];
const bindingKeys = ['document_id', 'target_id', 'version', 'raw_sha256', 'detail_body_sha256'];
const sha = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const parser = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_.:-]{0,127}$/.test(value);
const scope = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9-]{0,63}$/.test(value);
const identity = (value: unknown): value is string => typeof value === 'string' && /^[\x21-\x7e]{1,256}$/.test(value);
const count = (value: unknown, maximum: number): boolean => Number.isSafeInteger(value) && (value as number) >= 1 && (value as number) <= maximum;

function exactObject(value: unknown, keys: string[]): ObjectValue | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return null;
  const ownKeys = Reflect.ownKeys(value);
  return ownKeys.length === keys.length && keys.every(key => Object.hasOwn(value, key))
    && ownKeys.every(key => typeof key === 'string' && 'value' in Object.getOwnPropertyDescriptor(value, key)!)
    ? value as ObjectValue : null;
}

type DataSnapshot = Readonly<Record<string, Readonly<PropertyDescriptor>>>;
function snapshotData(value: ObjectValue, keys: string[]): DataSnapshot {
  return Object.freeze(Object.fromEntries(keys.map(key =>
    [key, Object.freeze(Object.getOwnPropertyDescriptor(value, key)!)])));
}
function unchangedData(value: ObjectValue, snapshot: DataSnapshot): boolean {
  const keys = Object.keys(snapshot);
  if (!exactObject(value, keys)) return false;
  return keys.every(key => {
    const actual = Object.getOwnPropertyDescriptor(value, key)!, expected = snapshot[key];
    return Object.is(actual.value, expected.value) && actual.enumerable === expected.enumerable
      && actual.writable === expected.writable && actual.configurable === expected.configurable;
  });
}

/** Bind the bounded parser receipt to this exact delivered body, before display cleanup. */
export async function verifiedSourceExtraction(node: KnowledgeNode, selected: ObjectValue): Promise<SourceExtractionStatus | null> {
  const packet = exactObject(selected.source_extraction, packetKeys);
  const binding = exactObject(packet?.binding, bindingKeys);
  const body = selected.body;
  if (selected.basis !== 'source_record' || selected.platform !== 'files'
    || node.sourcePlatform && node.sourcePlatform !== 'files'
    || !matchesSourceReference(node, selected) || !packet || !binding
    || packet.schema !== 1 || packet.kind !== 'declared-original-file-extraction'
    || packet.processing_version !== 'document-segments-v1' || !parser(packet.parser) || !scope(packet.scope_kind)
    || !sha(packet.input_sha256) || !sha(packet.extracted_text_sha256)
    || !count(packet.input_bytes, 5242880) || !count(packet.extracted_text_bytes, 16000) || !count(packet.segment_count, 256)
    || typeof packet.body_complete !== 'boolean' || typeof packet.parser_truncated !== 'boolean'
    || packet.body_complete && packet.parser_truncated
    || typeof body !== 'string' || body.length > 24000
    || !identity(binding.document_id) || !identity(binding.target_id) || !identity(binding.version)
    || !sha(selected.raw_sha256) || !sha(binding.raw_sha256) || !sha(binding.detail_body_sha256)) return null;
  const status = packet.parser_truncated ? 'partial' : packet.body_complete ? 'complete' : 'unverified';
  if (packet.status !== status) return null;
  const documentId = binding.document_id, targetId = binding.target_id, version = binding.version;
  const archiveHash = binding.raw_sha256, bodyHash = binding.detail_body_sha256;
  const stillBound = () => selected.source_extraction === packet && selected.body === body
    && selected.basis === 'source_record' && selected.platform === 'files'
    && (!node.sourcePlatform || node.sourcePlatform === 'files') && matchesSourceReference(node, selected)
    && documentId === (node.canonicalId || node.id) && targetId === node.sourceTarget
    && version === node.sourceVersion && archiveHash === selected.raw_sha256;
  if (!stillBound()) return null;
  let characters = 0;
  for (const scalar of body) {
    if (++characters > 12000 || scalar.length === 1 && /[\ud800-\udfff]/.test(scalar)) return null;
  }
  // All fields are validated primitives except the binding object, captured
  // separately. Recheck descriptors before reading any mutated packet fields;
  // serializing the live object after await could invoke an introduced getter.
  const packetSnapshot = snapshotData(packet, packetKeys), bindingSnapshot = snapshotData(binding, bindingKeys);
  try {
    const encoder = new TextEncoder();
    if (encoder.encode(JSON.stringify(packet)).byteLength > 4096 || !globalThis.crypto?.subtle) return null;
    const digest = await globalThis.crypto.subtle.digest('SHA-256', encoder.encode(body));
    const actual = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    return unchangedData(packet, packetSnapshot) && unchangedData(binding, bindingSnapshot)
      && stillBound() && actual === bodyHash ? status : null;
  } catch { return null; }
}

const notices: Record<SourceExtractionStatus | 'unknown', string> = {
  complete: '파일 본문 추출 완료 · 수집 시 본문 전체를 추출한 것으로 확인했습니다.',
  partial: '파일 본문 일부 추출 · 원본 본문의 일부만 추출한 기록입니다.',
  unverified: '파일 본문 추출 범위 미확인 · 추출 기록에서 본문 전체가 포함됐는지 확인하지 못했습니다.',
  unknown: '파일 본문 추출 범위 알 수 없음 · 현재 자료에 연결된 추출 범위를 확인하지 못했습니다.',
};

export function renderSourceExtraction(bodyElement: HTMLElement, node: KnowledgeNode, selected: ObjectValue | null): void {
  const root = bodyElement.ownerDocument;
  root.querySelectorAll('.knowledge-source-extraction').forEach(item => item.remove());
  if (node.sourcePlatform !== 'files'
    && (node.sourcePlatform || selected?.basis !== 'source_record' || selected.platform !== 'files')) return;
  const notice = root.createElement('p'); notice.className = 'knowledge-source-extraction';
  notice.dataset.status = 'unknown'; notice.textContent = notices.unknown;
  bodyElement.before(notice);
  if (!selected) return;
  const nodeId = node.id;
  const packet = exactObject(selected.source_extraction, packetKeys);
  const binding = exactObject(packet?.binding, bindingKeys);
  const body = selected.body;
  const packetSnapshot = packet ? snapshotData(packet, packetKeys) : null;
  const bindingSnapshot = binding ? snapshotData(binding, bindingKeys) : null;
  void verifiedSourceExtraction(node, selected).then(status => {
    const pane = root.querySelector<HTMLDialogElement>('dialog#knowledge-note-pane');
    if (!notice.isConnected) return;
    if (node.id !== nodeId || pane && (pane.hidden || !pane.open || pane.dataset.nodeId !== nodeId || pane.closest('[hidden]'))) {
      notice.remove(); return;
    }
    // Verification resolves before this separate Promise reaction. Commit only
    // while that same immutable receipt still owns the current source/body.
    const current = status && packet && binding && packetSnapshot && bindingSnapshot
      && unchangedData(packet, packetSnapshot) && unchangedData(binding, bindingSnapshot)
      && selected.source_extraction === packet && selected.body === body
      && selected.basis === 'source_record' && selected.platform === 'files'
      && (!node.sourcePlatform || node.sourcePlatform === 'files') && selected.node_id === node.id
      && matchesSourceReference(node, selected) && binding.document_id === (node.canonicalId || node.id)
      && binding.target_id === node.sourceTarget && binding.target_id === selected.target_id
      && binding.version === node.sourceVersion && binding.version === selected.version
      && binding.raw_sha256 === selected.raw_sha256;
    const visible = current ? status : 'unknown';
    notice.dataset.status = visible; notice.textContent = notices[visible];
  });
}
