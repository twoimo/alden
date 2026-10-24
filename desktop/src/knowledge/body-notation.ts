import type { KnowledgeNode } from './graph-model';

type ObjectValue = Record<string, unknown>;
type Notation = { raw: string; start: number; end: number; status: string; value: string };
const object = (value: unknown): ObjectValue | null => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : null;
const sha = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const decimal = (value: unknown): value is string => typeof value === 'string' && value.length <= 256 && /^-?[0-9]+(?:\.[0-9]+)?$/.test(value);
const units: Record<string, string> = { mass: 'g', length: 'm', duration: 's', data: 'bit',
  money_won_unspecified: '원', money_KRW: 'KRW', money_USD: 'USD', money_EUR: 'EUR', money_JPY: 'JPY',
  person_count: '명', item_count: '개', case_count: '건', occurrence_count: '회', percent: '%', percentage_point: '%p' };
const statuses: Record<string, string> = { normalized: '구문 변환', ambiguous: '의미 미확정', unsupported: '지원 범위 밖', invalid: '형식 확인 필요' };

function quantity(value: ObjectValue | null): string | null {
  if (!value || value.status !== 'normalized' || !decimal(value.canonical_value)
    || typeof value.dimension !== 'string' || !Object.hasOwn(units, value.dimension)
    || value.canonical_unit !== units[value.dimension]) return null;
  // Keep exact decimal strings, including integers beyond Number's precision.
  return `${value.canonical_value} ${units[value.dimension]}`;
}

function display(value: ObjectValue): string | null {
  if (typeof value.status !== 'string' || !Object.hasOwn(statuses, value.status)
    || !['time', 'quantity', 'quantity_range', 'quantity_relation', 'untyped_number'].includes(String(value.kind))) return null;
  if (value.status !== 'normalized') return statuses[value.status];
  if (value.kind === 'time') return typeof value.canonical === 'string' && value.canonical.length <= 64
    && /^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,9})?)?Z)?$/.test(value.canonical) ? value.canonical : null;
  if (value.kind === 'quantity') return quantity(value);
  if (value.kind === 'quantity_range') {
    const first = object(value.first), last = object(value.last);
    const left = quantity(first), right = quantity(last);
    return left && right && first?.dimension === last?.dimension && first?.canonical_unit === last?.canonical_unit
      && ['~', '～', '–', '—'].includes(String(value.range_marker)) ? `${left} ~ ${right} · 원문 순서` : null;
  }
  return null;
}

/** Verify only an annotation packet for this exact source excerpt; never infer facts. */
export async function verifiedBodyNotation(node: KnowledgeNode, selected: ObjectValue): Promise<{ items: Notation[]; excerpt: boolean; limited: boolean } | null> {
  const note = object(selected.body_notation), binding = object(note?.binding), analysis = object(note?.analysis);
  const body = selected.body;
  if (selected.basis !== 'source_record' || selected.node_id !== node.id || typeof body !== 'string' || body.length > 24000 || !binding || !analysis
    || binding.basis !== 'verified_source_record_detail' || binding.document_id !== (node.canonicalId || node.id)
    || binding.target_id !== node.sourceTarget || binding.target_id !== selected.target_id
    || binding.version !== node.sourceVersion || binding.version !== selected.version
    || !sha(selected.raw_sha256) || binding.raw_sha256 !== selected.raw_sha256
    || !sha(binding.body_sha256) || binding.body_sha256 !== analysis.body_sha256
    || binding.body_scope !== (selected.truncated === true ? 'detail_body_excerpt' : 'detail_body')
    || analysis.policy !== 'body-notation-candidates-v1' || analysis.semantic_validity !== 'not_assessed'
    || analysis.offset_basis !== 'exact_supplied_unicode_codepoints' || analysis.client_offset_basis !== 'exact_supplied_utf16_code_units'
    || analysis.source_authorization !== 'caller_must_bind_permitted_current_source_version'
    || typeof analysis.truncated !== 'boolean'
    || analysis.state !== (analysis.truncated ? 'bounded_candidates' : 'supported_notation_scan_complete')
    || !Array.isArray(analysis.candidates) || analysis.candidates.length > 64) return null;
  const boundaries = [0]; let utf16 = 0;
  for (const scalar of body) {
    if (scalar.length === 1 && /[\ud800-\udfff]/.test(scalar)) return null;
    utf16 += scalar.length; boundaries.push(utf16);
  }
  if (boundaries.length - 1 > 12000 || analysis.body_characters !== boundaries.length - 1) return null;
  const items: Notation[] = []; let previousEnd = 0;
  for (const candidate of analysis.candidates) {
    const value = object(candidate);
    if (!value || !Number.isInteger(value.start) || !Number.isInteger(value.end)) return null;
    const start = value.start as number, end = value.end as number;
    if (start < previousEnd || end <= start || end >= boundaries.length
      || value.utf16_start !== boundaries[start] || value.utf16_end !== boundaries[end]
      || typeof value.raw !== 'string' || value.raw.length > 512 || value.raw !== body.slice(boundaries[start], boundaries[end])) return null;
    const copy = display(value); if (!copy) return null;
    items.push({ raw: value.raw, start: boundaries[start], end: boundaries[end], status: statuses[String(value.status)], value: copy });
    previousEnd = end;
  }
  // Backend checked the archive/current version; this independently binds client
  // offsets to the delivered body. Missing WebCrypto leaves the source readable.
  if (!globalThis.crypto?.subtle) return null;
  const expectedHash = binding.body_sha256;
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
  const actual = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
  return actual === expectedHash ? { items, excerpt: selected.truncated === true, limited: analysis.truncated } : null;
}

export function renderBodyNotation(bodyElement: HTMLElement, node: KnowledgeNode, selected: ObjectValue | null): void {
  const root = bodyElement.ownerDocument;
  root.querySelectorAll('.knowledge-body-notation').forEach(item => item.remove());
  if (!selected?.body_notation) return;
  const body = selected.body as string;
  const section = root.createElement('details'); section.className = 'knowledge-body-notation'; section.hidden = true;
  bodyElement.after(section);
  void verifiedBodyNotation(node, selected).then(result => {
    const pane = root.getElementById('knowledge-note-pane');
    if (!section.isConnected) return; // Another render/close invalidated this request.
    if (!result?.items.length || pane && pane.hidden) { section.remove(); return; }
    const summary = root.createElement('summary'); summary.textContent = `날짜·수량 표기 · ${result.items.length}개`;
    const explanation = root.createElement('p');
    explanation.textContent = '원문에 적힌 표기만 해석합니다. 실제 사실·현재 유효성·채용 조건은 확인하지 않았습니다.'
      + (result.excerpt ? ' 본문 앞부분의 발췌 범위입니다.' : '') + (result.limited ? ' 처리 한도에 도달해 일부만 표시합니다.' : '');
    const list = root.createElement('ul');
    for (const item of result.items) {
      const row = root.createElement('li'), quote = root.createElement('q'), mark = root.createElement('mark'), resultText = root.createElement('span');
      mark.textContent = item.raw;
      quote.append(root.createTextNode(body.slice(Math.max(0, item.start - 36), item.start)), mark,
        root.createTextNode(body.slice(item.end, item.end + 36)));
      resultText.textContent = item.status === item.value ? item.status : `${item.status} · ${item.value}`;
      row.append(quote, resultText); list.append(row);
    }
    section.append(summary, explanation, list); section.hidden = false;
  }).catch(() => section.remove());
}
