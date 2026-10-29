// @vitest-environment happy-dom
import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { renderNodeDetails, setNodeDetailsVisible } from '../knowledge/node-details';
import { verifiedSourceExtraction, type SourceExtractionStatus } from '../knowledge/source-extraction';

type ObjectValue = Record<string, unknown>;
type Packet = ObjectValue & { binding: ObjectValue };
const sha = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex');
const archiveHash = 'a'.repeat(64);
const body = '파일의 본문입니다.';
function graph() {
  return parseKnowledgeGraph({ nodes: [
    { id: 'project:record-a', canonical_id: 'record-a', label: '파일 A', category: 'document',
      source_version: 'v1', source_target: 'target-a', source_platform: 'files' },
    { id: 'record-b', label: '파일 B', category: 'document',
      source_version: 'v2', source_target: 'target-b', source_platform: 'files' },
  ], edges: [] });
}
function packet(copy = body, status: SourceExtractionStatus = 'complete'): Packet {
  return { schema: 1, kind: 'declared-original-file-extraction',
    binding: { document_id: 'record-a', target_id: 'target-a', version: 'v1', raw_sha256: archiveHash,
      detail_body_sha256: sha(copy) },
    parser: 'docx-xml', processing_version: 'document-segments-v1', scope_kind: 'xml-part-paragraphs',
    input_sha256: 'b'.repeat(64), input_bytes: 1234, extracted_text_sha256: sha(copy),
    extracted_text_bytes: Buffer.byteLength(copy, 'utf8'), segment_count: 3,
    body_complete: status === 'complete', parser_truncated: status === 'partial', status };
}
function detail(copy = body, status: SourceExtractionStatus = 'complete'): ObjectValue {
  return { node_id: 'project:record-a', target_id: 'target-a', version: 'v1', platform: 'files',
    basis: 'source_record', body: copy, raw_sha256: archiveHash, truncated: false,
    source_extraction: packet(copy, status) };
}
function render(value: ObjectValue = detail()) {
  const data = graph();
  renderNodeDetails(data, data.nodes[0], { ok: true, details: value });
}
function notice() { return document.querySelector<HTMLElement>('.knowledge-source-extraction'); }
async function state(status: SourceExtractionStatus | 'unknown') {
  await vi.waitFor(() => expect(notice()?.dataset.status).toBe(status));
}
function deferredDigest() {
  let resolve!: (value: ArrayBuffer) => void;
  const promise = new Promise<ArrayBuffer>(done => { resolve = done; });
  return { promise, finish(copy = body) { resolve(Uint8Array.from(Buffer.from(sha(copy), 'hex')).buffer); } };
}
async function flush() { for (let i = 0; i < 4; i++) await Promise.resolve(); }

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
  document.body.innerHTML = '<section id="settings-knowledge-card"><dialog id="knowledge-note-pane" hidden>'
    + '<button id="knowledge-focus-close">닫기</button><p id="knowledge-node-summary"></p>'
    + '<div id="knowledge-node-body" hidden></div><p id="knowledge-node-kind"></p><p id="knowledge-node-basis"></p>'
    + '<ul id="knowledge-node-facts"></ul><details class="knowledge-evidence-disclosure">'
    + '<summary id="knowledge-evidence-heading"></summary><div id="knowledge-node-evidence"></div></details>'
    + '</dialog><canvas id="knowledge-graph-canvas" tabindex="0"></canvas></section>';
  setNodeDetailsVisible(true);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('strict file extraction packet', () => {
  it.each(['complete', 'partial', 'unverified'] as const)('accepts a bound %s receipt', async status => {
    expect(await verifiedSourceExtraction(graph().nodes[0], detail(body, status))).toBe(status);
  });

  const required = Object.keys(packet());
  it.each(required)('requires packet key %s', async key => {
    const value = packet(); delete value[key];
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction: value })).toBeNull();
  });
  it.each(Object.keys(packet().binding))('requires binding key %s', async key => {
    const value = packet(); delete value.binding[key];
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction: value })).toBeNull();
  });

  it.each([
    ['schema', '1'], ['schema', true], ['schema', 2], ['kind', 'legacy-file-extraction'],
    ['processing_version', 'document-segments-v2'], ['processing_version', 1],
    ['parser', ''], ['parser', 'a'.repeat(129)], ['parser', '한글'], ['parser', '<img>'],
    ['parser', 'docx xml'], ['parser', 'Docx'], ['parser', null], ['scope_kind', ''], ['scope_kind', false],
    ['scope_kind', 'original_lines'], ['scope_kind', 'Original-lines'], ['scope_kind', 'a'.repeat(65)],
    ['scope_kind', 'original-lines\n'], ['scope_kind', '/private/original'], ['scope_kind', 'a'.repeat(129)],
    ['input_sha256', 'A'.repeat(64)], ['input_sha256', 'b'.repeat(63)], ['input_sha256', 1],
    ['extracted_text_sha256', 'g'.repeat(64)], ['extracted_text_sha256', null],
    ['input_bytes', 0], ['input_bytes', 5242881], ['input_bytes', 1.5], ['input_bytes', '2'], ['input_bytes', true],
    ['input_bytes', NaN], ['input_bytes', Infinity], ['input_bytes', -1],
    ['extracted_text_bytes', 0], ['extracted_text_bytes', 16001], ['extracted_text_bytes', 1.5],
    ['extracted_text_bytes', '2'], ['extracted_text_bytes', true],
    ['segment_count', 0], ['segment_count', 257], ['segment_count', 1.5], ['segment_count', '2'], ['segment_count', true],
    ['body_complete', 1], ['body_complete', 'true'], ['body_complete', null],
    ['parser_truncated', 0], ['parser_truncated', 'false'], ['parser_truncated', null],
    ['status', 'unknown'], ['status', 'COMPLETE'], ['status', true],
  ])('rejects malformed %s = %j', async (key, value) => {
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction: { ...packet(), [key]: value } })).toBeNull();
  });

  it.each([1, 5242880])('accepts the input-byte boundary %i', async input_bytes => {
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction: { ...packet(), input_bytes } })).toBe('complete');
  });
  it.each([1, 16000])('accepts the extracted-byte boundary %i independently of detail excerpt size', async extracted_text_bytes => {
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction: { ...packet(), extracted_text_bytes } })).toBe('complete');
  });
  it.each([1, 256])('accepts the segment boundary %i', async segment_count => {
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction: { ...packet(), segment_count } })).toBe('complete');
  });

  it('accepts the exact backend ASCII parser/scope/identity limits', async () => {
    const source_extraction = packet();
    source_extraction.parser = 'a' + '_.:-0123456789'.repeat(10).slice(0, 127);
    source_extraction.scope_kind = 'a'.repeat(64);
    const id = 'a'.repeat(256), node = { ...graph().nodes[0], canonicalId: id, sourceTarget: id, sourceVersion: id };
    source_extraction.binding.document_id = source_extraction.binding.target_id = source_extraction.binding.version = id;
    expect(await verifiedSourceExtraction(node, { ...detail(), target_id: id, version: id, source_extraction })).toBe('complete');
  });

  for (const body_complete of [false, true]) for (const parser_truncated of [false, true]) {
    it(`requires status to agree with body_complete=${body_complete}, parser_truncated=${parser_truncated}`, async () => {
      for (const status of ['complete', 'partial', 'unverified']) {
        const expected = body_complete && parser_truncated ? null : parser_truncated ? 'partial' : body_complete ? 'complete' : 'unverified';
        expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction: {
          ...packet(), body_complete, parser_truncated, status,
        } })).toBe(status === expected ? expected : null);
      }
    });
  }

  it('rejects non-records, extra keys, inherited fields, symbols, accessors and oversized UTF-8 packets', async () => {
    const accessor = packet(); Object.defineProperty(accessor, 'schema', { get: () => 1 });
    const inherited = Object.create({ ...packet() });
    const oversized = packet(); oversized.binding.document_id = '😀'.repeat(128);
    oversized.binding.target_id = '😀'.repeat(128); oversized.binding.version = '😀'.repeat(128);
    oversized.parser = 'a'.repeat(128); oversized.scope_kind = 'a'.repeat(128);
    const node = { ...graph().nodes[0], canonicalId: oversized.binding.document_id as string,
      sourceTarget: oversized.binding.target_id as string, sourceVersion: oversized.binding.version as string };
    const selected = { ...detail(), target_id: node.sourceTarget, version: node.sourceVersion, source_extraction: oversized };
    // Untrusted packets may be oversized as well as malformed.
    oversized.binding.document_id = '\u0000'.repeat(256); node.canonicalId = oversized.binding.document_id as string;
    oversized.binding.target_id = '\u0000'.repeat(256); node.sourceTarget = selected.target_id = oversized.binding.target_id as string;
    oversized.binding.version = '\u0000'.repeat(256); node.sourceVersion = selected.version = oversized.binding.version as string;
    expect(Buffer.byteLength(JSON.stringify(oversized))).toBeGreaterThan(4096);
    expect(await verifiedSourceExtraction(node, selected)).toBeNull();
    for (const source_extraction of [null, [], 'complete', 1, inherited, accessor, { ...packet(), extra: true },
      { ...packet(), [Symbol('extra')]: true }, { ...packet(), binding: { ...packet().binding, original_path: '/private/source' } },
      { ...packet(), binding: [] }]) {
      expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction })).toBeNull();
    }
  });

  it.each([
    ['document_id', 'project:record-a'], ['document_id', 'record-b'], ['target_id', 'target-b'],
    ['version', 'v2'], ['raw_sha256', 'c'.repeat(64)], ['raw_sha256', 'A'.repeat(64)],
    ['detail_body_sha256', sha('別の本文')], ['detail_body_sha256', null], ['document_id', true],
    ['target_id', 'a'.repeat(257)], ['version', ''], ['version', 'v 1'], ['version', '버전1'],
  ])('rejects a binding mismatch %s = %j', async (key, value) => {
    const source_extraction = packet(); source_extraction.binding[key] = value;
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), source_extraction })).toBeNull();
  });

  it.each([
    { node_id: 'record-a' }, { target_id: 'target-b' }, { version: 'v2' }, { basis: 'note' },
    { platform: 'graph' }, { raw_sha256: 'c'.repeat(64) }, { raw_sha256: true }, { body: null }, { body: '別の本文' },
  ])('requires the current matching file detail %j', async delta => {
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(), ...delta })).toBeNull();
  });

  it('requires graph references and uses canonical identity only for the packet binding', async () => {
    const node = graph().nodes[0];
    for (const delta of [{ sourceVersion: '' }, { sourceTarget: '' }, { canonicalId: 'record-b' }, { sourcePlatform: 'threads' }]) {
      expect(await verifiedSourceExtraction({ ...node, ...delta }, detail())).toBeNull();
    }
    const ordinary = { ...node, id: 'record-a', canonicalId: '' };
    expect(await verifiedSourceExtraction(ordinary, { ...detail(), node_id: 'record-a' })).toBe('complete');
  });

  it('hashes exact Unicode, whitespace and control characters without normalizing or trimming', async () => {
    const copy = ' \t\u1100\u1161😀\u0000\r\n';
    expect(await verifiedSourceExtraction(graph().nodes[0], detail(copy))).toBe('complete');
    for (const altered of [copy.normalize('NFC'), copy.trim(), copy.replace('\u0000', ''), copy.replace('\r\n', '\n')]) {
      const value = packet(copy); value.binding.detail_body_sha256 = sha(altered);
      expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(copy), source_extraction: value })).toBeNull();
    }
  });

  it('bounds source characters and rejects unpaired UTF-16 surrogates', async () => {
    expect(await verifiedSourceExtraction(graph().nodes[0], detail('a'.repeat(12001)))).toBeNull();
    expect(await verifiedSourceExtraction(graph().nodes[0], detail('\ud800'))).toBeNull();
    const copy = 'a'.repeat(12000), value = packet(copy);
    expect(await verifiedSourceExtraction(graph().nodes[0], { ...detail(copy), source_extraction: value })).toBe('complete');
  });

  it('fails closed without WebCrypto or when digest fails', async () => {
    vi.stubGlobal('crypto', undefined);
    expect(await verifiedSourceExtraction(graph().nodes[0], detail())).toBeNull();
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn().mockRejectedValue(new Error('digest unavailable')) } });
    expect(await verifiedSourceExtraction(graph().nodes[0], detail())).toBeNull();
  });

  it('rejects reference/body changes while hashing', async () => {
    for (const change of ['version', 'canonical', 'body', 'archive']) {
      const pending = deferredDigest();
      vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => pending.promise) } });
      const node = graph().nodes[0], selected = detail();
      const result = verifiedSourceExtraction(node, selected);
      if (change === 'version') node.sourceVersion = 'v2';
      if (change === 'canonical') node.canonicalId = 'record-b';
      if (change === 'body') selected.body = 'changed';
      if (change === 'archive') selected.raw_sha256 = 'c'.repeat(64);
      pending.finish(); expect(await result).toBeNull();
    }
  });
});

describe('in-flight packet mutations', () => {
  const mutations: [string, (value: Packet, getter: () => unknown) => void][] = [
    ['binding version', value => { value.binding.version = 'v2'; }],
    ['body completeness', value => { value.body_complete = false; }],
    ['parser truncation', value => { value.parser_truncated = true; }],
    ['status', value => { value.status = 'unverified'; }],
    ['consistent partial state', value => {
      value.body_complete = false; value.parser_truncated = true; value.status = 'partial';
    }],
    ['binding accessor', (value, getter) => { Object.defineProperty(value.binding, 'version', { get: getter }); }],
    ['packet accessor', (value, getter) => { Object.defineProperty(value, 'status', { get: getter }); }],
    ['data descriptor flags', value => { Object.defineProperty(value, 'status', { enumerable: false }); }],
    ['extra packet field', value => { value.extra = true; }],
    ['nested serialization getter', (value, getter) => {
      value.parser = Object.defineProperty({}, 'toJSON', { get: getter });
    }],
  ];
  it.each(mutations)('rejects a changed %s without invoking introduced getters', async (_name, mutate) => {
    const pending = deferredDigest(), getter = vi.fn(() => { throw new Error('introduced getter must stay inert'); });
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => pending.promise) } });
    const selected = detail(), value = selected.source_extraction as Packet;
    const result = verifiedSourceExtraction(graph().nodes[0], selected);
    mutate(value, getter);
    pending.finish();
    const actual = await result;
    expect(getter).not.toHaveBeenCalled();
    expect(actual).toBeNull();
  });

  it.each(['binding', 'status'])('keeps the reader unknown after an in-place %s mutation', async field => {
    const pending = deferredDigest();
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => pending.promise) } });
    const selected = detail(), value = selected.source_extraction as Packet;
    render(selected);
    if (field === 'binding') value.binding.version = 'v2';
    else { value.body_complete = false; value.parser_truncated = true; value.status = 'partial'; }
    pending.finish(); await flush();
    expect(notice()?.dataset.status).toBe('unknown');
    expect(notice()?.textContent).not.toContain('추출 완료');
  });
});

describe('file extraction notices in the reader', () => {
  it.each([
    ['complete', '파일 본문 추출 완료'], ['partial', '파일 본문 일부 추출'], ['unverified', '파일 본문 추출 범위 미확인'],
  ] as const)('shows a single fixed %s notice without raw metadata or backup claims', async (status, copy) => {
    render(detail(body, status)); expect(notice()?.dataset.status).toBe('unknown');
    await state(status);
    expect(document.querySelectorAll('.knowledge-source-extraction')).toHaveLength(1);
    expect(notice()?.textContent).toContain(copy);
    for (const raw of ['docx-xml', 'xml-part-paragraphs', 'document-segments-v1', archiveHash, '1234', 'SHA', '백업', '보존']) {
      expect(notice()?.textContent).not.toContain(raw);
    }
  });

  it.each(['missing', 'malformed', 'legacy', 'unknown-schema'])('keeps %s file extraction unknown', async kind => {
    const selected = detail();
    if (kind === 'missing') delete selected.source_extraction;
    if (kind === 'malformed') selected.source_extraction = { ...packet(), parser_truncated: 'false' };
    if (kind === 'legacy') selected.source_extraction = { scope: 'entire original', body_complete: true };
    if (kind === 'unknown-schema') selected.source_extraction = { ...packet(), schema: 2 };
    render(selected); await flush();
    expect(notice()?.dataset.status).toBe('unknown');
    expect(notice()?.textContent).toContain('알 수 없음');
    expect(notice()?.textContent).not.toContain('entire original');
  });

  it('shows unknown for loading, unavailable, discarded and version-mismatched file details', async () => {
    const data = graph();
    for (const payload of [null, { ok: false, details: detail() }, { ok: true, discarded: true, details: detail() },
      { ok: true, details: { ...detail(), version: 'v2' } }]) {
      renderNodeDetails(data, data.nodes[0], payload); await flush();
      expect(notice()?.dataset.status).toBe('unknown');
    }
  });

  it('keeps parser coverage independent from the response excerpt flag', async () => {
    render({ ...detail(body, 'partial'), truncated: false }); await state('partial');
    expect(document.querySelector('.knowledge-body-limit')).toBeNull();
    render({ ...detail(body, 'complete'), truncated: true }); await state('complete');
    expect(document.querySelector('.knowledge-body-limit')?.textContent).toContain('일부 발췌');
    const sourceBody = document.getElementById('knowledge-node-body')!;
    expect(document.querySelector('.knowledge-body-limit')!.compareDocumentPosition(sourceBody) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(notice()!.compareDocumentPosition(sourceBody) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    render({ ...detail(body, 'unverified'), truncated: true }); await state('unverified');
    expect(document.querySelector('.knowledge-body-limit')).not.toBeNull();
  });

  it('retains frontend clipping disclosure when an oversized body cannot verify', async () => {
    render(detail('원'.repeat(12001))); await state('unknown');
    expect(document.getElementById('knowledge-node-body')?.textContent).toHaveLength(12000);
    expect(document.querySelector('.knowledge-body-limit')?.textContent).toContain('일부 발췌');
  });

  it('keeps HTML and raw scope text inert', async () => {
    const copy = '<img src=x onerror="alert(1)"><script>unsafe()</script> [열기](javascript:unsafe())';
    render(detail(copy)); await state('complete');
    const content = document.getElementById('knowledge-node-body')!;
    expect(content.textContent).toBe(copy);
    expect(content.querySelector('img, script, iframe, [onerror], a')).toBeNull();
    render({ ...detail(), source_extraction: { ...packet(), scope_kind: copy } }); await state('unknown');
    expect(notice()?.textContent).not.toContain(copy);
    expect(document.querySelector('img, script, iframe, [onerror]')).toBeNull();
  });

  it('leaves non-file sources and saved notes unchanged, even with a file-shaped packet', async () => {
    render(); await state('complete');
    const data = graph(), node = { ...data.nodes[0], sourcePlatform: 'threads' };
    renderNodeDetails(data, node, { details: { ...detail(), platform: 'threads' } }); await flush();
    expect(notice()).toBeNull();
    expect(document.getElementById('knowledge-node-body')?.textContent).toBe(body);
    const notes = parseKnowledgeGraph({ nodes: [{ id: 'note', label: '노트' }], edges: [] });
    renderNodeDetails(notes, notes.nodes[0], { details: { node_id: 'note', basis: 'note', body, source_extraction: packet() } });
    expect(notice()).toBeNull();
    expect(document.getElementById('knowledge-node-body')?.textContent).toBe(body);
  });

  it('recognizes selected legacy file details when the graph has no platform field', async () => {
    const data = graph(), node = { ...data.nodes[0], sourcePlatform: '' };
    renderNodeDetails(data, node, { details: { ...detail(), source_extraction: undefined } }); await flush();
    expect(notice()?.dataset.status).toBe('unknown');
  });

  it('does not apply an old digest after A → B → A, or after a newer payload for the same node', async () => {
    const old = deferredDigest();
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => old.promise) } });
    render(); const oldNotice = notice();
    const data = graph(); renderNodeDetails(data, data.nodes[1]);
    render({ ...detail(), source_extraction: undefined });
    old.finish(); await flush();
    expect(oldNotice?.isConnected).toBe(false);
    expect(notice()?.dataset.status).toBe('unknown');
    render(); const secondNotice = notice();
    render({ ...detail(), source_extraction: { ...packet(), binding: { ...packet().binding, version: 'v2' } } });
    await flush();
    expect(secondNotice?.isConnected).toBe(false);
    expect(notice()?.dataset.status).toBe('unknown');
  });

  it('keeps a newer partial result when an older complete digest finishes later', async () => {
    const old = deferredDigest(), current = deferredDigest();
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn().mockImplementationOnce(() => old.promise).mockImplementationOnce(() => current.promise) } });
    render(); const oldNotice = notice();
    render(detail(body, 'partial')); current.finish(); await flush();
    expect(notice()?.dataset.status).toBe('partial');
    old.finish(); await flush();
    expect(oldNotice?.isConnected).toBe(false);
    expect(notice()?.dataset.status).toBe('partial');
  });

  it.each([{ ok: false }, { ok: true, discarded: true }])('invalidates a pending digest on a failed/discarded response %j', async envelope => {
    const pending = deferredDigest();
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => pending.promise) } });
    render(); const oldNotice = notice();
    const data = graph(); renderNodeDetails(data, data.nodes[0], { ...envelope, details: detail() });
    pending.finish(); await flush();
    expect(oldNotice?.isConnected).toBe(false);
    expect(notice()?.dataset.status).toBe('unknown');
  });

  it.each(['close-action', 'native-close', 'hidden-ancestor'])('suppresses digest completion with %s', async closeMode => {
    const pending = deferredDigest();
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => pending.promise) } });
    render(); const oldNotice = notice();
    if (closeMode === 'close-action') document.getElementById('knowledge-focus-close')!.click();
    if (closeMode === 'native-close') (document.getElementById('knowledge-note-pane') as HTMLDialogElement).close();
    if (closeMode === 'hidden-ancestor') document.getElementById('settings-knowledge-card')!.hidden = true;
    pending.finish(); await flush();
    expect(oldNotice?.isConnected).toBe(false);
    expect(notice()).toBeNull();
  });

  it('does not resurrect a closed receipt when the same node is reopened before hashing completes', async () => {
    const pending = deferredDigest();
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn(() => pending.promise) } });
    render(); const oldNotice = notice();
    setNodeDetailsVisible(false); setNodeDetailsVisible(true);
    render({ ...detail(), source_extraction: undefined });
    pending.finish(); await flush();
    expect(oldNotice?.isConnected).toBe(false);
    expect(notice()?.dataset.status).toBe('unknown');
  });

  it('keeps unknown visible when hashing fails', async () => {
    vi.stubGlobal('crypto', { subtle: { digest: vi.fn().mockRejectedValue(new Error('unavailable')) } });
    render(); await flush(); expect(notice()?.dataset.status).toBe('unknown');
  });
});

describe('coverage commit after hash verification', () => {
  it('places extraction coverage before the source text', async () => {
    render(); await state('complete');
    expect(document.getElementById('knowledge-node-body')?.previousElementSibling).toBe(notice());
  });
  it.each(['packet', 'binding', 'node-version', 'selected-body'])('discards %s mutation before the DOM write', async kind => {
    let pending: Promise<ArrayBuffer> | null = null;
    vi.stubGlobal('crypto', { subtle: { digest: (algorithm: string, input: ArrayBuffer) => {
      pending = webcrypto.subtle.digest(algorithm, input) as Promise<ArrayBuffer>;
      return pending;
    } } });
    const data = graph(), selected = detail();
    renderNodeDetails(data, data.nodes[0], { ok: true, details: selected });
    expect(pending).not.toBeNull();
    await pending!.then(() => {
      const value = selected.source_extraction as Packet;
      if (kind === 'packet') Object.assign(value, { body_complete: false, parser_truncated: true, status: 'partial' });
      if (kind === 'binding') value.binding.version = 'changed-version';
      if (kind === 'node-version') data.nodes[0].sourceVersion = 'changed-version';
      if (kind === 'selected-body') selected.body = 'changed body';
    });
    await flush();
    expect(notice()?.dataset.status).toBe('unknown');
    expect(notice()?.textContent).not.toContain('추출 완료');
  });
});
