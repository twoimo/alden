// @vitest-environment happy-dom
import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { originalBodyContext, verifiedOriginalBodyWindow, type OriginalBodyReader } from '../knowledge/original-body-window';
import { renderNodeDetails, setNodeDetailsVisible } from '../knowledge/node-details';
import { CollectionGraphController } from '../knowledge/collection-graph';
import { KnowledgeDrilldown, parseKnowledgeGraph, type KnowledgeGraph } from '../knowledge/graph-model';
import { settingsMarkup } from '../ui';
import type { fetchSettingsAction } from '../runtime';
import type { KnowledgeFocusEvent } from '../knowledge/hologram';
import { parseRuntimeSnapshot } from '../contracts';
import { expandGraphTransport } from '../graph-transport';

vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn(async () => null) }));
vi.mock('@tauri-apps/api/app', () => ({ getVersion: async () => '0.3.220' }));
vi.mock('../knowledge/hologram', () => ({ KnowledgeHologram: class {
  private model: KnowledgeDrilldown;
  constructor(_canvas: unknown, readonly currentGraph: KnowledgeGraph, private selected: (event: KnowledgeFocusEvent) => void,
    private closed: () => void) { this.model = new KnowledgeDrilldown(currentGraph); }
  get currentView() { return this.model.current(); }
  get canGoBack() { return false; }
  clickNode(id: string) {
    const view = this.model.openNote(id);
    this.selected({ node: this.currentGraph.nodes.find(node => node.id === id) ?? null, view,
      previousCamera: { camera: [0, 0, 1], lookAt: [0, 0, 0] } });
    return view;
  }
  reset() { this.selected({ node: null, view: this.model.reset(), previousCamera: { camera: [0, 0, 1], lookAt: [0, 0, 0] } }); }
  dispose() { this.closed(); }
} }));

type Data = Record<string, unknown>;
const sha = (value: string) => createHash('sha256').update(value, 'utf8').digest('hex');
const rawHash = 'a'.repeat(64), manifestHash = 'b'.repeat(64), sourceHash = 'c'.repeat(64);
const original = '\ufeff첫째\r\n' + '가'.repeat(5400) + 'e\u0301 <img src=x onerror="bad()">\n  ';
const retained = '수집 당시 앞부분';
const graph = () => parseKnowledgeGraph({ nodes: [{ id: 'project:one:doc', canonical_id: 'doc', label: '원본.md',
  source_version: 'v1', source_target: 'target', source_platform: 'files', category: 'document' }], edges: [] });
function details(copy = original): Data {
  return { node_id: 'project:one:doc', target_id: 'target', version: 'v1', basis: 'source_record', platform: 'files',
    raw_sha256: rawHash, body: retained, truncated: true,
    capture: { status: 'manifest_verified', source_sha256: sourceHash },
    source_extraction: { schema: 1, kind: 'declared-original-file-extraction',
      binding: { document_id: 'doc', target_id: 'target', version: 'v1', raw_sha256: rawHash, detail_body_sha256: sha(retained) },
      parser: 'text-md', processing_version: 'document-segments-v1', scope_kind: 'original-lines',
      input_sha256: sha(copy), input_bytes: Buffer.byteLength(copy), extracted_text_sha256: sha(retained),
      extracted_text_bytes: Buffer.byteLength(retained), segment_count: 1, body_complete: false, parser_truncated: true, status: 'partial' } };
}
function response(copy = original, offset = 0, maximum = 16000) {
  const all = Buffer.from(copy);
  let end = Math.min(all.length, offset + maximum);
  while (end < all.length && (all[end] & 0xc0) === 0x80) end--;
  const text = all.subarray(offset, end).toString('utf8');
  return { ok: true, body_window: { schema: 1, kind: 'declared-original-utf8-window',
    binding: { document_id: 'doc', target_id: 'target', version: 'v1', raw_sha256: rawHash, input_sha256: sha(copy) },
    capture: { manifest_sha256: manifestHash, source_sha256: sourceHash }, encoding: 'utf-8',
    offset_bytes: offset, end_bytes: end, next_offset_bytes: end === all.length ? null : end,
    total_bytes: all.length, text, text_sha256: sha(text) } };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
const owned: CollectionGraphController[] = [];
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
  document.body.innerHTML = '<section id="settings-knowledge-card"><dialog id="knowledge-note-pane" hidden>'
    + '<button id="knowledge-focus-close">닫기</button><p id="knowledge-node-summary"></p><div id="knowledge-node-body"></div>'
    + '<p id="knowledge-node-kind"></p><p id="knowledge-node-basis"></p><ul id="knowledge-node-facts"></ul>'
    + '<details class="knowledge-evidence-disclosure"><summary id="knowledge-evidence-heading"></summary>'
    + '<div id="knowledge-node-evidence"></div></details></dialog><canvas id="knowledge-graph-canvas"></canvas></section>';
  setNodeDetailsVisible(true);
});
afterEach(() => { owned.splice(0).forEach(c => c.dispose()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const section = () => document.querySelector<HTMLElement>('.knowledge-original-body-window')!;
const textArea = () => document.querySelector<HTMLElement>('.knowledge-original-body-text')!;
const buttons = () => section().querySelectorAll('button');
async function waitState(state: string) { await vi.waitFor(() => expect(section()?.dataset.state).toBe(state)); }
function render(selected = details(), reader: OriginalBodyReader = { read: async r => response(original, r.offset_bytes, r.max_bytes), isCurrent: () => true }) {
  const data = graph(); renderNodeDetails(data, data.nodes[0], { ok: true, details: selected }, document, reader); return { data, selected, reader };
}

describe('retained original UTF8 window validation', () => {
  it('validates exact bytes, Unicode, EOF (including empty EOF) without changing the extraction receipt', async () => {
    for (const copy of ['\ufeffe\u0301\r\n \t😀 ', 'a']) {
      const selected = details(copy), context = originalBodyContext(graph().nodes[0], selected)!;
      const first = await verifiedOriginalBodyWindow(response(copy), context, { offset_bytes: 0, max_bytes: 16000 });
      expect(first?.window.text).toBe(copy); expect(first?.current()).toBe(true);
      const end = Buffer.byteLength(copy);
      const eof = await verifiedOriginalBodyWindow(response(copy, end), context, { offset_bytes: end, max_bytes: 4 }, first!.window);
      expect(eof?.window.text).toBe(''); expect(eof?.window.next_offset_bytes).toBeNull();
      expect((selected.source_extraction as Data).status).toBe('partial');
    }
  });

  it('uses the existing unencoded body carrier and never copies menubar failure reasons', async () => {
    const value = response(); expect(expandGraphTransport(value)).toBe(value);
    const failure = { ok: false, items: [], reason: 'collection_graph_version_changed' };
    expect(expandGraphTransport(failure)).toBe(failure);
    render(details(), { read: async () => failure, isCurrent: () => true });
    buttons()[0].click(); await waitState('failure');
    expect(section().textContent).not.toContain(failure.reason);
  });

  const invalid: Array<[string, (value: ReturnType<typeof response>) => void]> = [
    ['extra envelope key', value => Object.assign(value, { extra: true })],
    ['extra inner key', value => Object.assign(value.body_window, { extra: true })],
    ['missing inner key', value => { delete (value.body_window as Data).text_sha256; }],
    ['extra binding key', value => Object.assign(value.body_window.binding, { extra: true })],
    ['missing binding key', value => { delete (value.body_window.binding as Data).input_sha256; }],
    ['extra capture key', value => Object.assign(value.body_window.capture, { extra: true })],
    ['missing capture key', value => { delete (value.body_window.capture as Data).manifest_sha256; }],
    ['schema', value => { value.body_window.schema = 2; }],
    ['kind', value => { value.body_window.kind = 'other'; }],
    ['encoding', value => { value.body_window.encoding = 'UTF-8'; }],
    ['document', value => { value.body_window.binding.document_id = 'other'; }],
    ['target', value => { value.body_window.binding.target_id = 'other'; }],
    ['version', value => { value.body_window.binding.version = 'v2'; }],
    ['non ASCII ID', value => { value.body_window.binding.document_id = '한글'; }],
    ['raw hash', value => { value.body_window.binding.raw_sha256 = 'd'.repeat(64); }],
    ['input hash', value => { value.body_window.binding.input_sha256 = 'e'.repeat(64); }],
    ['capture source mismatch', value => { value.body_window.capture.source_sha256 = 'f'.repeat(64); }],
    ['capture manifest format', value => { value.body_window.capture.manifest_sha256 = 'X'.repeat(64); }],
    ['offset mismatch', value => { value.body_window.offset_bytes = 1; }],
    ['fraction', value => { value.body_window.end_bytes += 0.5; }],
    ['boolean range', value => { (value.body_window as Data).end_bytes = true; }],
    ['oversized', value => { value.body_window.end_bytes = 16001; }],
    ['total mismatch', value => { value.body_window.total_bytes++; }],
    ['next mismatch', value => { value.body_window.next_offset_bytes!++; }],
    ['premature EOF', value => { value.body_window.next_offset_bytes = null; }],
    ['text hash', value => { value.body_window.text_sha256 = 'd'.repeat(64); }],
    ['text byte length', value => { value.body_window.text += 'x'; }],
    ['unpaired scalar', value => { value.body_window.text = '\ud800' + value.body_window.text.slice(1); }],
    ['control', value => { value.body_window.text = '\u0000' + value.body_window.text.slice(1); }],
    ['symbol key', value => Object.assign(value.body_window, { [Symbol('extra')]: 1 })],
  ];
  it.each(invalid)('rejects %s', async (_name, mutate) => {
    const value = response(); mutate(value);
    expect(await verifiedOriginalBodyWindow(value, originalBodyContext(graph().nodes[0], details())!, { offset_bytes: 0, max_bytes: 16000 })).toBeNull();
  });

  it.each([{ offset_bytes: -1, max_bytes: 16000 }, { offset_bytes: 0, max_bytes: 3 },
    { offset_bytes: 0, max_bytes: 16001 }, { offset_bytes: 0.5, max_bytes: 16000 }])('rejects invalid request %j', async request => {
    expect(await verifiedOriginalBodyWindow(response(), originalBodyContext(graph().nodes[0], details())!, request)).toBeNull();
  });

  it('rejects a validly hashed whole page with a different declared input hash', async () => {
    const selected = details('text'), value = response('text');
    (selected.source_extraction as Data).input_sha256 = value.body_window.binding.input_sha256 = 'd'.repeat(64);
    expect(await verifiedOriginalBodyWindow(value, originalBodyContext(graph().nodes[0], selected)!, { offset_bytes: 0, max_bytes: 16000 })).toBeNull();
  });

  it('freshly hashes the delivered detail body against its receipt for every read', async () => {
    const selected = details(); selected.body = 'same version but changed delivered text';
    const context = originalBodyContext(graph().nodes[0], selected)!;
    expect(context).not.toBeNull();
    expect(await verifiedOriginalBodyWindow(response(), context, { offset_bytes: 0, max_bytes: 16000 })).toBeNull();
  });

  it.each([['input_bytes', 5242880], ['extracted_text_bytes', 16000], ['segment_count', 256]] as const)(
    'requires %s within the retained receipt range 1..%i', (key, maximum) => {
      for (const count of [0, -1, 0.5, true, maximum + 1]) {
        const selected = details(); (selected.source_extraction as Data)[key] = count;
        expect(originalBodyContext(graph().nodes[0], selected)).toBeNull();
      }
      for (const count of [1, maximum]) {
        const selected = details(); (selected.source_extraction as Data)[key] = count;
        expect(originalBodyContext(graph().nodes[0], selected)).not.toBeNull();
      }
    });

  it.each([
    ['12000 BMP scalars', 'a'.repeat(12000), true],
    ['12001 BMP scalars', 'a'.repeat(12001), false],
    ['12000 supplementary scalars', '😀'.repeat(12000), true],
    ['12001 supplementary scalars', '😀'.repeat(12001), false],
    ['12000 mixed scalars', '😀'.repeat(11999) + 'x', true],
    ['12001 mixed scalars in 24000 code units', '😀'.repeat(11999) + 'xy', false],
    ['12000 decomposed scalars', 'e\u0301'.repeat(6000), true],
    ['12001 decomposed scalars', 'e\u0301'.repeat(6000) + 'x', false],
    ['lone high surrogate within limit', 'a'.repeat(11999) + '\ud800', false],
    ['lone low surrogate within limit', '\udfff' + 'a'.repeat(11999), false],
  ] as const)('bounds the delivered detail body by %s', (_label, body, valid) => {
    const selected = details(); selected.body = body;
    ((selected.source_extraction as Data).binding as Data).detail_body_sha256 = sha(body);
    expect(originalBodyContext(graph().nodes[0], selected) !== null).toBe(valid);
  });

  it.each(['packet', 'binding', 'capture', 'selected', 'node'] as const)('rejects mutation of %s during digest without invoking introduced getters', async target => {
    const value = response(), selected = details(), node = graph().nodes[0];
    const context = originalBodyContext(node, selected)!, textHash = value.body_window.text_sha256;
    const gate = deferred<ArrayBuffer>(), getter = vi.fn(() => { throw new Error('getter called'); });
    const actualDigest = webcrypto.subtle.digest.bind(webcrypto.subtle);
    let calls = 0;
    vi.stubGlobal('crypto', { subtle: { digest: (...args: Parameters<typeof actualDigest>) => ++calls === 1 ? gate.promise : actualDigest(...args) } });
    const promise = verifiedOriginalBodyWindow(value, context, { offset_bytes: 0, max_bytes: 16000 });
    const [object, key] = target === 'packet' ? [value.body_window, 'text']
      : target === 'binding' ? [value.body_window.binding, 'version']
        : target === 'capture' ? [value.body_window.capture, 'source_sha256']
          : target === 'selected' ? [selected, 'source_extraction'] : [node, 'sourceVersion'];
    Object.defineProperty(object, key, { configurable: true, enumerable: true, get: getter });
    gate.resolve(Uint8Array.from(Buffer.from(textHash, 'hex')).buffer);
    expect(await promise).toBeNull(); expect(getter).not.toHaveBeenCalled();
  });

  it('keeps the guard active between verification resolution and its consumer reaction', async () => {
    const value = response(), context = originalBodyContext(graph().nodes[0], details())!;
    const promise = verifiedOriginalBodyWindow(value, context, { offset_bytes: 0, max_bytes: 16000 });
    const getter = vi.fn();
    promise.then(() => Object.defineProperty(value.body_window.binding, 'version', { enumerable: true, configurable: true, get: getter }));
    const verified = await promise;
    expect(verified).not.toBeNull(); expect(verified?.current()).toBe(false); expect(getter).not.toHaveBeenCalled();
  });
});

describe('actual detail reader controls', () => {
  it('places first-read controls after unchanged notices and before a long parser excerpt', async () => {
    const selected = details(), copy = 'long excerpt line\n'.repeat(650);
    selected.body = copy;
    const packet = selected.source_extraction as Data;
    (packet.binding as Data).detail_body_sha256 = packet.extracted_text_sha256 = sha(copy);
    packet.extracted_text_bytes = Buffer.byteLength(copy);
    render(selected);
    await vi.waitFor(() => expect(document.querySelector<HTMLElement>('.knowledge-source-extraction')?.dataset.status).toBe('partial'));
    const coverage = document.querySelector('.knowledge-source-extraction')!;
    const excerpt = document.querySelector('.knowledge-body-limit')!;
    const body = document.getElementById('knowledge-node-body')!;
    expect(coverage.textContent).toBe('파일 본문 일부 추출 · 원본 본문의 일부만 추출한 기록입니다.');
    expect(excerpt.textContent).toBe('일부 발췌 · 현재 화면에는 본문의 앞부분만 표시합니다.');
    for (const [before, after] of [[coverage, excerpt], [excerpt, section()], [buttons()[0], body]]) {
      expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(body.textContent).toBe(copy.trim()); expect(textArea().hidden).toBe(true);
    expect(buttons()[0].textContent).toBe('첫 부분 읽기');
  });

  it('loads only on click, replaces the current bounded part, resets and leaves coverage partial at EOF', async () => {
    const read = vi.fn(async (r: { offset_bytes: number; max_bytes: number }) => response(original, r.offset_bytes, r.max_bytes));
    const { selected } = render(details(), { read, isCurrent: () => true });
    expect(read).not.toHaveBeenCalled(); expect(textArea().hidden).toBe(true);
    buttons()[0].click(); expect(section().dataset.state).toBe('loading'); expect(buttons()[0].disabled).toBe(false); expect(buttons()[0].getAttribute('aria-disabled')).toBe('true');
    await waitState('part'); const first = textArea().textContent!;
    expect(Buffer.byteLength(first)).toBeLessThanOrEqual(16000); expect(first).toBe(response().body_window.text);
    buttons()[0].click(); await waitState('last');
    expect(textArea().textContent).toBe(response(original, Buffer.byteLength(first)).body_window.text);
    expect(section().querySelector('img')).toBeNull(); expect(first + textArea().textContent).toBe(original);
    expect(buttons()[0].disabled).toBe(true); expect(buttons()[1].hidden).toBe(false);
    expect((selected.source_extraction as Data).status).toBe('partial');
    expect(document.querySelector('.knowledge-body-limit')).not.toBeNull();
    await vi.waitFor(() => expect(document.querySelector<HTMLElement>('.knowledge-source-extraction')?.dataset.status).toBe('partial'));
    buttons()[1].click(); await waitState('part'); expect(textArea().textContent).toBe(first);
    expect(read.mock.calls.map(([r]) => r.offset_bytes)).toEqual([0, Buffer.byteLength(first), 0]);
    expect(document.querySelectorAll('.knowledge-original-body-text')).toHaveLength(1);
  });

  it.each(['text-txt', 'text-md'])('allows %s original-lines and preserves callers without a callback', parser => {
    const selected = details(); (selected.source_extraction as Data).parser = parser;
    const data = graph(); renderNodeDetails(data, data.nodes[0], { ok: true, details: selected });
    expect(section()).toBeNull(); render(selected); expect(section()).not.toBeNull();
  });

  it.each(['pdf-pypdf', 'docx-xml', 'csv', 'other'])('does not expose unsupported parser %s', parser => {
    const selected = details(); (selected.source_extraction as Data).parser = parser; render(selected); expect(section()).toBeNull();
  });

  it('requires the declared original-lines scope and a source record bound to the current selection', () => {
    for (const mutate of [(selected: Data) => { (selected.source_extraction as Data).scope_kind = 'segments'; },
      (selected: Data) => { selected.version = 'old'; }, (selected: Data) => { selected.basis = 'note'; }]) {
      const selected = details(); mutate(selected); render(selected); expect(section()).toBeNull();
    }
  });

  it('shows fixed failure copy and clears the old page while allowing a failed next read to retry', async () => {
    let calls = 0;
    render(details(), { read: async r => ++calls === 2 ? { ok: false, error: '<secret raw path>' } : response(original, r.offset_bytes), isCurrent: () => true });
    buttons()[0].click(); await waitState('part');
    buttons()[0].click(); await waitState('failure'); expect(textArea().textContent).toBe(''); expect(textArea().hidden).toBe(true);
    expect(section().textContent).toContain('원문을 불러오지 못했습니다.'); expect(section().textContent).not.toContain('secret');
    buttons()[0].click(); await waitState('last');
  });

  it.each(['close', 'selection', 'guard', 'version', 'packet', 'body'] as const)('suppresses a late transport result after %s', async action => {
    const pending = deferred<unknown>(); let active = true;
    const { selected, data } = render(details(), { read: () => pending.promise, isCurrent: () => active });
    const old = section(); buttons()[0].click();
    if (action === 'close') setNodeDetailsVisible(false);
    if (action === 'selection') renderNodeDetails(data, { ...data.nodes[0], id: 'other' });
    if (action === 'guard') active = false;
    if (action === 'version') data.nodes[0].sourceVersion = 'v2';
    if (action === 'packet') (selected.source_extraction as Data).input_sha256 = 'd'.repeat(64);
    if (action === 'body') selected.body = 'changed retained body';
    pending.resolve(response()); for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(old.querySelector('pre')?.textContent).toBe('');
  });

  it('rejects capture changes on next/reset and clears the previously accepted page', async () => {
    let calls = 0;
    render(details(), { read: async r => { const value = response(original, r.offset_bytes); if (++calls > 1) value.body_window.capture.manifest_sha256 = 'e'.repeat(64); return value; }, isCurrent: () => true });
    buttons()[0].click(); await waitState('part');
    buttons()[0].click(); await waitState('failure'); expect(textArea().textContent).toBe(''); expect(textArea().hidden).toBe(true);
  });

  it.each(['next', 'reset'] as const)('hides a loaded page through failed %s reads and retries with the retained cursor', async action => {
    for (const failure of ['denied', 'stale', 'malformed', 'transport']) {
      const pending = deferred<unknown>(); let armed = false;
      const read = vi.fn(async (request: { offset_bytes: number; max_bytes: number }) => {
        if (!armed) return response(original, request.offset_bytes);
        const value = await pending.promise;
        if (failure === 'transport') throw new Error('private transport detail');
        return value;
      });
      render(details(), { read, isCurrent: () => true });
      buttons()[0].click(); await waitState('part');
      const nextOffset = Buffer.byteLength(textArea().textContent!);
      if (action === 'reset') { buttons()[0].click(); await waitState('last'); }
      expect(textArea().hidden).toBe(false); expect(textArea().textContent).not.toBe('');
      armed = true;
      buttons()[action === 'next' ? 0 : 1].click();
      expect(section().dataset.state).toBe('loading'); expect(textArea().hidden).toBe(true); expect(textArea().textContent).toBe('');
      const malformed = response(original, action === 'next' ? nextOffset : 0); malformed.body_window.binding.version = 'revoked';
      pending.resolve(failure === 'malformed' ? malformed : { ok: false, items: [],
        reason: failure === 'stale' ? 'collection_graph_version_changed' : 'collection_source_authorization_required' });
      await waitState('failure'); expect(textArea().hidden).toBe(true); expect(textArea().textContent).toBe('');
      expect(section().textContent).not.toContain('collection_');
      armed = false;
      buttons()[action === 'next' ? 0 : 1].click(); await waitState(action === 'next' ? 'last' : 'part');
      expect(read.mock.calls.at(-1)![0].offset_bytes).toBe(action === 'next' ? nextOffset : 0);
      expect(textArea().hidden).toBe(false); expect(textArea().textContent).not.toBe('');
    }
  });

  it('clears the loaded page before an async selection guard can invalidate the next read', async () => {
    let active = true, calls = 0; const pending = deferred<unknown>();
    render(details(), { read: async r => ++calls === 1 ? response(original, r.offset_bytes) : pending.promise, isCurrent: () => active });
    buttons()[0].click(); await waitState('part');
    buttons()[0].click(); expect(textArea().hidden).toBe(true); expect(textArea().textContent).toBe('');
    active = false; pending.resolve(response());
    for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(textArea().hidden).toBe(true); expect(textArea().textContent).toBe('');
  });

  it.each(['packet', 'binding', 'capture', 'selected', 'version'] as const)('fences %s changes in the actual DOM reaction after successful verification', async target => {
    const value = response(), getter = vi.fn(() => { throw new Error('introduced getter'); });
    const { selected, data } = render(details(), { read: async () => value, isCurrent: () => true });
    const aggregate = Promise.all.bind(Promise);
    let mutationScheduled = false;
    const spy = vi.spyOn(Promise, 'all').mockImplementation(values => {
      const result = aggregate(values);
      // This reaction runs just before the verifier continuation; its queued
      // microtask runs after verification, before the renderer continuation.
      void result.then(() => queueMicrotask(() => {
        mutationScheduled = true;
        const [object, key] = target === 'packet' ? [value.body_window, 'text']
          : target === 'binding' ? [value.body_window.binding, 'version']
            : target === 'capture' ? [value.body_window.capture, 'source_sha256']
              : target === 'selected' ? [selected, 'source_extraction'] : [data.nodes[0], 'sourceVersion'];
        Object.defineProperty(object, key, { configurable: true, enumerable: true, get: getter });
      }));
      return result;
    });
    buttons()[0].click();
    await vi.waitFor(() => expect(mutationScheduled).toBe(true));
    spy.mockRestore();
    for (let i = 0; i < 6; i++) await Promise.resolve();
    expect(textArea().textContent).toBe(''); expect(getter).not.toHaveBeenCalled();
    if (target === 'packet' || target === 'binding' || target === 'capture') expect(section().dataset.state).toBe('failure');
  });

  it('fails closed when hashing is unavailable and can retry after a transport failure', async () => {
    const read = vi.fn().mockRejectedValueOnce(new Error('private path')).mockResolvedValue(response());
    render(details(), { read, isCurrent: () => true });
    buttons()[0].click(); await waitState('failure'); expect(textArea().hidden).toBe(true);
    vi.stubGlobal('crypto', undefined);
    buttons()[0].click(); await waitState('failure'); expect(textArea().textContent).toBe('');
  });
});

describe('existing collection controller transport', () => {
  async function setup(project = 'one') {
    document.body.innerHTML = settingsMarkup();
    let current: KnowledgeGraph = { nodes: [], edges: [] }, model = new KnowledgeDrilldown(current);
    const pending = deferred<Record<string, unknown>>();
    const load = vi.fn<typeof fetchSettingsAction>(async (action, input = {}) => {
      if (action === 'collection-projects') return { ok: true, projects: [{ project }, { project: 'two' }] };
      if (action === 'collection-graph') {
        const query = JSON.parse(input.query ?? '{}');
        if (query.body_window) return pending.promise;
        return { ok: true, nodes: [{ id: 'project:one:doc', canonical_id: 'doc', label: '原文.md', category: 'document',
          source_platform: 'files', source_version: 'v1', source_target: 'target' }], edges: [], details: { ...details(), node_id: 'doc' } };
      }
      return { ok: true, nodes: [], edges: [] };
    });
    const port = { get currentGraph() { return current; }, get currentView() { return model.current(); },
      get navigationTargets() { return { camera: [1, 2, 3], lookAt: [0, 0, 0] }; },
      replaceGraph(next: KnowledgeGraph, navigation?: { focusId: string | null; hops: number }) { current = next; model = new KnowledgeDrilldown(next); if (navigation) model.restore(next, navigation); },
      clickNode() { return model.current(); }, showNodeActivity() {}, clearNodeActivity() {} };
    const controller = new CollectionGraphController(load, port); owned.push(controller); controller.start();
    await vi.waitFor(() => expect(current.nodes).toHaveLength(1));
    const source = document.getElementById('knowledge-project') as HTMLSelectElement;
    source.value = 'project:' + project; source.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(current.nodes).toHaveLength(1));
    const node = current.nodes[0], previousCamera = port.navigationTargets;
    const view = model.openNote(node.id); controller.selected({ node, previousCamera, view });
    await controller.focus(node, async () => null);
    return { controller, node, load, pending, source, port, model };
  }

  it('sends only the five settled query keys using current explicit projects and the canonical ID', async () => {
    const { controller, node, load, pending } = await setup();
    const reader = controller.originalBodyReader(node)!; expect(reader.isCurrent()).toBe(true);
    const promise = reader.read({ offset_bytes: 0, max_bytes: 16000 });
    const query = JSON.parse(load.mock.calls.at(-1)![1]!.query!);
    expect(query).toEqual({ projects: ['one'], focus: 'doc', target_id: 'target', expected_version: 'v1', body_window: { offset_bytes: 0, max_bytes: 16000 } });
    pending.resolve(response()); expect(await promise).toEqual(response());
  });

  it.each([
    ['취업', '취업'], ['장사의신', '장사의신'], ['exact spaces', ' 취업 '],
    ['128 Korean scalars', '가'.repeat(128)], ['128 supplementary scalars', '😀'.repeat(128)],
  ])('preserves the current explicit Unicode project %s in the carrier', async (_label, project) => {
    const { controller, node, load, pending } = await setup(project);
    const reader = controller.originalBodyReader(node)!; expect(reader).toBeDefined();
    const promise = reader.read({ offset_bytes: 0, max_bytes: 16000 });
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).toEqual({ projects: [project], focus: 'doc',
      target_id: 'target', expected_version: 'v1', body_window: { offset_bytes: 0, max_bytes: 16000 } });
    pending.resolve(response()); expect(await promise).toEqual(response());
  });

  it.each([['empty', ''], ['129 Korean scalars', '가'.repeat(129)], ['129 supplementary scalars', '😀'.repeat(129)]])(
    'rejects a project outside the registered name bound: %s', async (_label, project) => {
      const { controller, node, load } = await setup(project), count = load.mock.calls.length;
      expect(controller.originalBodyReader(node)).toBeUndefined(); expect(load.mock.calls).toHaveLength(count);
    });

  it.each(['filter', 'silent-filter', 'pause', 'stop', 'dispose', 'focus', 'selection', 'version', 'canonical', 'clear'] as const)('fences in-flight results after %s', async action => {
    const { controller, node, pending, source, model, port } = await setup();
    const reader = controller.originalBodyReader(node)!, promise = reader.read({ offset_bytes: 0, max_bytes: 16000 });
    if (action === 'filter' || action === 'silent-filter') { source.value = 'project:two'; if (action === 'filter') source.dispatchEvent(new Event('change')); }
    if (action === 'pause') document.getElementById('knowledge-live-toggle')!.click();
    if (action === 'stop') controller.stop();
    if (action === 'dispose') controller.dispose();
    if (action === 'focus') await controller.focus(node, async () => null);
    if (action === 'selection') { const view = model.reset(); controller.selected({ node: null, view, previousCamera: port.navigationTargets }); }
    if (action === 'version') node.sourceVersion = 'v2';
    if (action === 'canonical') node.canonicalId = 'different';
    if (action === 'clear') controller.navigate('clear');
    pending.resolve(response()); expect(await promise).toBeNull(); expect(reader.isCurrent()).toBe(false);
  });

  it('never infers projects for all/legacy or reads after pause', async () => {
    const { controller, node, source } = await setup();
    source.value = 'all'; expect(controller.originalBodyReader(node)).toBeUndefined();
    source.value = 'legacy'; expect(controller.originalBodyReader(node)).toBeUndefined();
    source.value = 'project:one'; document.getElementById('knowledge-live-toggle')!.click();
    expect(controller.originalBodyReader(node)).toBeUndefined();
  });

  it('rejects invalid cursor bounds before invoking the carrier', async () => {
    const { controller, node, load } = await setup();
    const reader = controller.originalBodyReader(node)!, count = load.mock.calls.length;
    expect(await reader.read({ offset_bytes: -1, max_bytes: 16000 })).toBeNull();
    expect(await reader.read({ offset_bytes: 0, max_bytes: 16001 })).toBeNull();
    expect(load.mock.calls).toHaveLength(count);
  });
});

describe('desktop main callback wiring', () => {
  let setup: typeof import('../main').setupKnowledgeGraph;
  beforeAll(async () => {
    document.body.innerHTML = '<div id="app"></div>';
    window.history.replaceState({}, '', '/?view=settings');
    setup = (await import('../main')).setupKnowledgeGraph;
    await vi.waitFor(() => expect(document.querySelector<HTMLElement>('#app')?.dataset.state).not.toBe('loading'));
    window.dispatchEvent(new Event('pagehide'));
  });

  it.each(['read', 'close', 'dispose', 'reselect'] as const)('connects the actual optional renderer callback and fences %s', async action => {
    document.body.innerHTML = settingsMarkup();
    document.getElementById('settings-knowledge-card')!.closest<HTMLElement>('[hidden]')?.removeAttribute('hidden');
    const pending = deferred<unknown>(), selected = details();
    const read = vi.fn(() => pending.promise), reader = { read, isCurrent: () => true };
    const callback = vi.fn(() => reader);
    const payload = { ok: true, nodes: [{ id: 'project:one:doc', canonical_id: 'doc', label: '원문.md',
      category: 'document', source_version: 'v1', source_target: 'target', source_platform: 'files' }], edges: [] };
    const hologram = await setup(payload, parseRuntimeSnapshot({ available: false, rooms: [], jobs: [] }), async () => null,
      false, () => undefined, { selected() {}, navigate: () => false,
        focus: async () => ({ ok: true, details: selected }), originalBodyReader: callback });
    hologram!.clickNode('project:one:doc');
    await vi.waitFor(() => expect(section()).not.toBeNull());
    expect(callback).toHaveBeenCalledOnce(); expect(read).not.toHaveBeenCalled();
    const old = section(); buttons()[0].click(); expect(read).toHaveBeenCalledWith({ offset_bytes: 0, max_bytes: 16000 });
    if (action === 'close') document.getElementById('knowledge-focus-close')!.click();
    if (action === 'dispose') hologram!.dispose();
    if (action === 'reselect') hologram!.clickNode('project:one:doc');
    pending.resolve(response());
    if (action === 'read') { await waitState('part'); expect(textArea().textContent).toBe(response().body_window.text); }
    else { for (let i = 0; i < 12; i++) await Promise.resolve(); expect(old.querySelector('pre')?.textContent).toBe(''); }
    hologram!.dispose();
  });
});

describe('original reader keyboard focus contract', () => {
  it.each(['next', 'reset'] as const)('keeps pending %s focusable, blocks repeated activation and preserves focus on a non-EOF result', async action => {
    const copy = original.repeat(2), pending = deferred<unknown>();
    let armed = false;
    const read = vi.fn(async (request: { offset_bytes: number; max_bytes: number }) =>
      armed ? pending.promise : response(copy, request.offset_bytes, request.max_bytes));
    render(details(copy), { read, isCurrent: () => true });
    buttons()[0].click(); await waitState('part');
    if (action === 'reset') {
      buttons()[0].click(); await waitState('part');
      buttons()[0].click(); await waitState('last');
    }
    const [next, reset] = buttons(), target = action === 'next' ? next : reset;
    const offset = action === 'next' ? Buffer.byteLength(textArea().textContent!) : 0;
    const readsBeforePending = read.mock.calls.length;
    target.focus(); expect(document.activeElement).toBe(target);
    const focus = vi.spyOn(target, 'focus');
    armed = true; target.click();
    expect(section().dataset.state).toBe('loading');
    expect(next.disabled).toBe(action === 'reset'); // EOF next stays natively disabled while reset loads.
    expect(reset.disabled).toBe(false);
    for (const button of [next, reset]) expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(target);
    target.click(); next.click(); reset.click();
    expect(read).toHaveBeenCalledTimes(readsBeforePending + 1);
    expect(read.mock.calls.at(-1)![0]).toEqual({ offset_bytes: offset, max_bytes: 16000 });
    pending.resolve(response(copy, offset)); await waitState('part');
    expect(document.activeElement).toBe(target); expect(focus).not.toHaveBeenCalled();
    for (const button of [next, reset]) {
      expect(button.disabled).toBe(false);
      expect(button.getAttribute('aria-disabled')).toBeNull();
    }
    expect(section().hasAttribute('aria-busy')).toBe(false);
    expect(textArea().textContent).toBe(response(copy, offset).body_window.text);
    expect(read).toHaveBeenCalledTimes(readsBeforePending + 1);
  });

  it('moves focused next to visible reset once before disabling next at EOF', async () => {
    const pending = deferred<unknown>(); let armed = false;
    render(details(), { read: async request => armed ? pending.promise : response(original, request.offset_bytes),
      isCurrent: () => true });
    buttons()[0].click(); await waitState('part');
    const offset = Buffer.byteLength(textArea().textContent!), [next, reset] = buttons();
    next.focus(); expect(document.activeElement).toBe(next);
    const nativeFocus = reset.focus.bind(reset);
    const focus = vi.spyOn(reset, 'focus').mockImplementation(options => {
      expect(reset.hidden).toBe(false); expect(reset.disabled).toBe(false);
      expect(next.disabled).toBe(false); nativeFocus(options);
    });
    armed = true; next.click();
    pending.resolve(response(original, offset)); await waitState('last');
    expect(focus).toHaveBeenCalledOnce();
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(document.activeElement).toBe(reset);
    expect(next.disabled).toBe(true); expect(reset.disabled).toBe(false);
    expect(next.getAttribute('aria-disabled')).toBeNull(); expect(reset.getAttribute('aria-disabled')).toBeNull();
    expect(textArea().textContent).toBe(response(original, offset).body_window.text);
  });

  it.each(['next', 'reset'] as const)('retains %s focus after failure and retries the same cursor without stealing focus', async action => {
    const copy = original.repeat(2), pending = deferred<unknown>(); let calls = 0;
    const read = vi.fn(async (request: { offset_bytes: number; max_bytes: number }) =>
      ++calls === 2 ? pending.promise : response(copy, request.offset_bytes, request.max_bytes));
    render(details(copy), { read, isCurrent: () => true });
    buttons()[0].click(); await waitState('part');
    const [next, reset] = buttons(), target = action === 'next' ? next : reset;
    const offset = action === 'next' ? Buffer.byteLength(textArea().textContent!) : 0;
    target.focus(); expect(document.activeElement).toBe(target);
    const focus = vi.spyOn(target, 'focus');
    target.click(); pending.resolve({ ok: false }); await waitState('failure');
    expect(document.activeElement).toBe(target); expect(focus).not.toHaveBeenCalled();
    expect(target.disabled).toBe(false); expect(target.getAttribute('aria-disabled')).toBeNull();
    expect(textArea().hidden).toBe(true); expect(textArea().textContent).toBe('');
    target.click(); await waitState('part');
    expect(document.activeElement).toBe(target); expect(focus).not.toHaveBeenCalled();
    expect(read.mock.calls.map(([request]) => request.offset_bytes)).toEqual([0, offset, offset]);
    expect(textArea().textContent).toBe(response(copy, offset).body_window.text);
  });

  it.each(['close', 'another control'] as const)('does not steal focus moved to %s while an EOF read is pending', async destination => {
    const pending = deferred<unknown>(); let armed = false;
    render(details(), { read: async request => armed ? pending.promise : response(original, request.offset_bytes),
      isCurrent: () => true });
    buttons()[0].click(); await waitState('part');
    const offset = Buffer.byteLength(textArea().textContent!), [next, reset] = buttons();
    const elsewhere = destination === 'close' ? document.getElementById('knowledge-focus-close')!
      : document.body.appendChild(document.createElement('button'));
    if (destination === 'another control') elsewhere.textContent = 'generated unrelated control';
    next.focus(); armed = true; next.click();
    elsewhere.focus(); expect(document.activeElement).toBe(elsewhere);
    const focusReset = vi.spyOn(reset, 'focus'), focusNext = vi.spyOn(next, 'focus');
    pending.resolve(response(original, offset)); await waitState('last');
    expect(document.activeElement).toBe(elsewhere);
    expect(focusReset).not.toHaveBeenCalled(); expect(focusNext).not.toHaveBeenCalled();
    expect(next.disabled).toBe(true); expect(reset.hidden).toBe(false);
    expect(textArea().textContent).toBe(response(original, offset).body_window.text);
  });

  it.each(['closed', 'stale'] as const)('does not focus or commit an EOF reply after the reader becomes %s', async ownership => {
    const pending = deferred<unknown>(); let armed = false, active = true;
    render(details(), { read: async request => armed ? pending.promise : response(original, request.offset_bytes),
      isCurrent: () => active });
    buttons()[0].click(); await waitState('part');
    const offset = Buffer.byteLength(textArea().textContent!), old = section(), content = textArea();
    const [next, reset] = buttons();
    next.focus(); armed = true; next.click();
    if (ownership === 'closed') setNodeDetailsVisible(false);
    else active = false;
    const activeAfterInvalidation = document.activeElement;
    const focusReset = vi.spyOn(reset, 'focus'), focusNext = vi.spyOn(next, 'focus');
    pending.resolve(response(original, offset));
    for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(old.dataset.state).toBe('loading');
    expect(content.textContent).toBe(''); expect(content.hidden).toBe(true);
    expect(document.activeElement).toBe(activeAfterInvalidation);
    expect(focusReset).not.toHaveBeenCalled(); expect(focusNext).not.toHaveBeenCalled();
  });
});
