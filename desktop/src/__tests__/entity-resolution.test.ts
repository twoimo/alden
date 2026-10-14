// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { wireEntityResolution } from '../entity-resolution';
import { Storage } from 'happy-dom';

const owned: ReturnType<typeof wireEntityResolution>[] = [];
const storageKey = 'alden-entity-resolution-pending-v1';
const nodes = ['one', 'two'].map(id => ({ id, label: id, source_target: 'target', source_version: id + '-v', source_platform: 'graph' }));
const sourceRecord = (node: typeof nodes[number]) => ({ target_id: 'target', document_id: node.id, version: node.source_version,
  label: node.label, body: 'Synthetic original statement for document ' + node.id, basis: 'permitted_current_stored_record',
  content_evidence: { policy: 'source-content-evidence-v5', classification: 'content_candidate' },
  source_url: 'https://example.com/' + node.id, truncated: false });
const plan = { schema: 1, project: 'a', members: nodes.map(node => ({ document_id: node.id, target_id: 'target', version: node.source_version })),
  canonical: { target_id: 'target', document_id: 'one' }, reason: '같은 지식의 원문 근거', revision: 0,
  source_fingerprint: 'a'.repeat(64), plan_sha256: 'b'.repeat(64) };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function port(extra?: (action: string, input?: { query?: string; explicitOptIn?: boolean }) => unknown) {
  return vi.fn(async (action: string, input?: { query?: string; explicitOptIn?: boolean }) => {
    const value = extra?.(action, input); if (value !== undefined) return await value as Record<string, unknown> | null;
    if (action === 'collection-projects') return { ok: true, projects: [{ project: 'a' }, { project: 'b' }] };
    if (action === 'collection-resolution-history') return { ok: true, revision: 0, items: [], request_result: null };
    if (action === 'collection-graph') return { ok: true, nodes };
    if (action === 'collection-resolution-preview') return { ok: true, plan, records: nodes.map(sourceRecord) };
    return { ok: true };
  });
}
async function setup(load = port()) {
  const view = wireEntityResolution(load); owned.push(view);
  view.select('history');
  const section = document.querySelector<HTMLDetailsElement>('.entity-resolution')!;
  section.open = true; section.dispatchEvent(new Event('toggle')); await flush();
  const project = section.querySelector<HTMLSelectElement>('select')!;
  project.value = 'a'; project.dispatchEvent(new Event('change')); await flush();
  return { view, section, load };
}
function action(section: Element, name: string) { return [...section.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === name)!; }
async function choose(section: Element) {
  action(section, '자료 찾기').click(); await flush();
  for (const box of section.querySelectorAll<HTMLInputElement>('.entity-resolution-list input')) { box.checked = true; box.dispatchEvent(new Event('change')); }
  const reason = section.querySelector<HTMLTextAreaElement>('textarea')!; reason.value = '같은 지식의 원문 근거'; reason.dispatchEvent(new Event('input'));
  action(section, '선택한 자료 비교').click(); await flush();
}
beforeEach(() => {
  // Node's experimental storage global shadows Happy DOM's getter; use the
  // installed browser Storage implementation on this document's own window.
  Object.defineProperty(document.defaultView!, 'localStorage', { configurable: true, value: new Storage() });
  document.body.innerHTML = '<section id="settings-page-history"></section>';
});
afterEach(() => { owned.splice(0).forEach(view => view.dispose()); vi.restoreAllMocks(); });

describe('source-reviewed duplicate decisions', () => {
  it('does not run reads while the panel is closed or the history is hidden', async () => {
    const load = port(), view = wireEntityResolution(load); owned.push(view);
    view.select('history'); await flush(); expect(load).not.toHaveBeenCalled();
    view.visible(false);
    const section = document.querySelector<HTMLDetailsElement>('details')!; section.open = true; section.dispatchEvent(new Event('toggle')); await flush();
    expect(load).not.toHaveBeenCalled();
  });
  it('requires a fresh source preview and explicit same-identity confirmation', async () => {
    const { section, load } = await setup(); await choose(section);
    const apply = action(section, '같은 지식으로 묶기'); expect(apply.disabled).toBe(true); apply.click();
    expect(load.mock.calls.some(([name]) => name === 'collection-resolution-apply')).toBe(false);
    const box = section.querySelector<HTMLInputElement>('.entity-resolution-approval input')!;
    box.checked = true; box.dispatchEvent(new Event('change')); apply.click(); await flush();
    const call = load.mock.calls.find(([name]) => name === 'collection-resolution-apply')!;
    expect(call[1]?.explicitOptIn).toBe(true);
    expect(JSON.parse(call[1]!.query!).plan).toEqual(plan);
    expect(document.defaultView!.localStorage.getItem(storageKey)).toBeNull();
  });
  it('rejects a late preview after project selection changes', async () => {
    let finish!: (value: Record<string, unknown>) => void;
    const load = port(name => name === 'collection-resolution-preview' ? new Promise(resolve => { finish = resolve; }) : undefined);
    const { section } = await setup(load); await choose(section);
    const project = section.querySelector<HTMLSelectElement>('select')!; project.value = 'b'; project.dispatchEvent(new Event('change'));
    finish({ ok: true, plan, records: [{ label: 'old-project', body: 'must not render' }] }); await flush();
    expect(section.textContent).not.toContain('must not render');
    expect(action(section, '같은 지식으로 묶기').disabled).toBe(true);
  });
  it('renders original text as text and rejects executable source URLs', async () => {
    const load = port(name => name === 'collection-resolution-preview' ? { ok: true, plan, records: [
      { ...sourceRecord(nodes[0]), label: '<img src=x>', body: '<script>throw Error()</script>', source_url: 'javascript:alert(1)' },
      sourceRecord(nodes[1]),
    ] } : undefined);
    const { section } = await setup(load); await choose(section);
    expect(section.querySelector('script')).toBeNull();
    expect(section.querySelector('.entity-resolution-comparison')?.textContent).toContain('<script>');
    expect(section.querySelector('.entity-resolution-comparison article:first-child a')).toBeNull();
  });
  it('keeps an uncertain write and retries with exactly the original request ID', async () => {
    let attempts = 0;
    const load = port(name => name === 'collection-resolution-apply' ? (++attempts === 1 ? null : { ok: true }) : undefined);
    const { section } = await setup(load); await choose(section);
    const box = section.querySelector<HTMLInputElement>('.entity-resolution-approval input')!; box.checked = true; box.dispatchEvent(new Event('change'));
    action(section, '같은 지식으로 묶기').click(); await flush();
    const saved = JSON.parse(document.defaultView!.localStorage.getItem(storageKey)!);
    expect(saved.query.request_id).toBeTruthy(); expect(action(section, '자료 찾기').disabled).toBe(true);
    action(section, '대기 요청 결과 확인').click(); await flush();
    const writes = load.mock.calls.filter(([name]) => name === 'collection-resolution-apply');
    expect(writes).toHaveLength(2); expect(writes[0][1]).toEqual(writes[1][1]);
    expect(JSON.parse(writes[1][1]!.query!).request_id).toBe(saved.query.request_id);
  });
  it('reconciles a recovered completed request without starting another write', async () => {
    document.defaultView!.localStorage.setItem(storageKey, JSON.stringify({ action: 'collection-resolution-apply', project: 'a', query: { plan, request_id: 'old-request-0000000001' } }));
    const load = port((name, input) => name === 'collection-resolution-history' && JSON.parse(input!.query!).request_id
      ? { ok: true, revision: 1, items: [], request_result: { ok: true, resolution_id: 'recorded' } } : undefined);
    const { section } = await setup(load); action(section, '대기 요청 결과 확인').click(); await flush();
    expect(load.mock.calls.some(([name]) => name === 'collection-resolution-apply')).toBe(false);
    expect(document.defaultView!.localStorage.getItem(storageKey)).toBeNull();
  });
  it('refreshes graph listeners when a recovered successful request is confirmed', async () => {
    document.defaultView!.localStorage.setItem(storageKey, JSON.stringify({ action: 'collection-resolution-apply', project: 'a', query: { plan, request_id: 'saved-request-000000001' } }));
    const changed = vi.fn();
    document.defaultView!.addEventListener('alden://entity-resolution-changed', changed, { once: true });
    const load = port((name, input) => name === 'collection-resolution-history' && JSON.parse(input!.query!).request_id
      ? { ok: true, revision: 1, items: [], request_result: { ok: true, resolution_id: 'recorded' } } : undefined);
    const { section } = await setup(load); action(section, '대기 요청 결과 확인').click(); await flush();
    document.defaultView!.removeEventListener('alden://entity-resolution-changed', changed);
    expect(changed).toHaveBeenCalledTimes(1);
    expect(load.mock.calls.some(([name]) => name === 'collection-resolution-apply')).toBe(false);
  });
  it('shows a recovered rejection reason without reporting a graph change', async () => {
    document.defaultView!.localStorage.setItem(storageKey, JSON.stringify({ action: 'collection-resolution-apply', project: 'a', query: { plan, request_id: 'rejected-request-000001' } }));
    const changed = vi.fn();
    document.defaultView!.addEventListener('alden://entity-resolution-changed', changed, { once: true });
    const load = port((name, input) => name === 'collection-resolution-history' && JSON.parse(input!.query!).request_id
      ? { ok: true, revision: 1, items: [], request_result: { ok: false, reason: 'permission_changed' } } : undefined);
    const { section } = await setup(load); action(section, '대기 요청 결과 확인').click(); await flush();
    expect(section.querySelector('[role="status"]')!.textContent).toContain('permission_changed');
    expect(changed).not.toHaveBeenCalled();
    document.defaultView!.removeEventListener('alden://entity-resolution-changed', changed);
    expect(load.mock.calls.some(([name]) => name === 'collection-resolution-apply')).toBe(false);
  });
  it('ignores an in-flight preview after the reviewer edits its reason', async () => {
    let finish!: (value: Record<string, unknown>) => void;
    const load = port(name => name === 'collection-resolution-preview'
      ? new Promise(resolve => { finish = resolve; }) : undefined);
    const { section } = await setup(load); await choose(section);
    const reason = section.querySelector<HTMLTextAreaElement>('textarea')!;
    reason.value = 'Changed after preview began'; reason.dispatchEvent(new Event('input'));
    finish({ ok: true, plan, records: nodes.map(sourceRecord) }); await flush();
    expect(section.querySelector('.entity-resolution-comparison')!.children).toHaveLength(0);
    expect(section.querySelector<HTMLInputElement>('.entity-resolution-approval input')!.disabled).toBe(true);
  });
  it('keeps a declared content JSON record reviewable and renders every admitted preview character', async () => {
    const body = JSON.stringify({ title: 'A factual JSON document', source: 'Text '.repeat(2000) });
    const load = port(name => name === 'collection-resolution-preview' ? { ok: true, plan,
      records: [sourceRecord(nodes[0]), { ...sourceRecord(nodes[1]), body }] } : undefined);
    const { section } = await setup(load); await choose(section);
    expect(section.querySelector<HTMLInputElement>('.entity-resolution-approval input')!.disabled).toBe(false);
    expect([...section.querySelectorAll('pre')].some(pre => pre.textContent === body)).toBe(true);
  });
  it.each([
    ['missing original body', { body: ' ' }],
    ['declared metadata-only body', { body: 'two', content_evidence: { policy: 'source-content-evidence-v5', classification: 'metadata_only' } }],
    ['archive-only noncontent declaration', { content_evidence: { policy: 'source-content-evidence-v5', classification: 'source_metadata_only' } }],
    ['generic source-file description', { body: '현재 체크아웃의 소스 파일', content_evidence: { policy: 'source-content-evidence-v5', classification: 'metadata_description' } }],
    ['unread whole-body notice', { body: '경로만 색인한 파일. 본문은 읽지 않았습니다.', content_evidence: { policy: 'source-content-evidence-v5', classification: 'unread_body_marker' } }],
    ['bare link without destination content', { body:'https://example.invalid/apply',content_evidence:{policy:'source-content-evidence-v5',classification:'reference_only'} }],
    ['policy predating reference exclusion', { content_evidence:{policy:'source-content-evidence-v4',classification:'content_candidate'} }],
    ['missing evidence policy', { content_evidence: null }],
    ['old ambiguous evidence policy', { content_evidence: { policy: 'source-content-evidence-v1', classification: 'content_candidate' } }],
    ['stale policy before derived-summary exclusion', { content_evidence: { policy: 'source-content-evidence-v3', classification: 'content_candidate' } }],
    ['metadata-only basis', { basis: 'source_metadata' }],
    ['truncated original text', { truncated: true }],
  ])('blocks confirmation when a preview contains %s', async (_description, patch) => {
    const load = port(name => name === 'collection-resolution-preview' ? { ok: true, plan,
      records: [sourceRecord(nodes[0]), { ...sourceRecord(nodes[1]), ...patch }] } : undefined);
    const { section } = await setup(load); await choose(section);
    expect(section.querySelector<HTMLInputElement>('.entity-resolution-approval input')!.disabled).toBe(true);
    expect(action(section, '같은 지식으로 묶기').disabled).toBe(true);
    expect(section.querySelector('[role="status"]')!.textContent).toContain('원문');
  });
  it.each([
    ['another project', { project: 'b' }],
    ['another canonical document', { canonical: { target_id: 'target', document_id: 'two' } }],
    ['different members', { members: [plan.members[0]] }],
    ['different reason', { reason: 'Backend returned another decision' }],
  ])('does not confirm a preview plan for %s', async (_description, change) => {
    const load = port(name => name === 'collection-resolution-preview' ? { ok: true, plan: { ...plan, ...change }, records: nodes.map(sourceRecord) } : undefined);
    const { section } = await setup(load); await choose(section);
    expect(section.querySelector<HTMLInputElement>('.entity-resolution-approval input')!.disabled).toBe(true);
    expect(section.querySelector('.entity-resolution-comparison')!.children).toHaveLength(0);
    expect(action(section, '같은 지식으로 묶기').disabled).toBe(true);
  });
  it('does not replay a stored change without a recoverable request ID', async () => {
    document.defaultView!.localStorage.setItem(storageKey, JSON.stringify({ action: 'collection-resolution-apply', project: 'a', query: { plan } }));
    const load = port(), { section } = await setup(load);
    action(section, '대기 요청 결과 확인')?.click(); await flush();
    expect(load.mock.calls.some(([name]) => name === 'collection-resolution-apply')).toBe(false);
    expect(action(section, '자료 찾기').disabled).toBe(true);
  });
  it('asks for separate confirmation before reversing an existing decision', async () => {
    const load = port(name => name === 'collection-resolution-history' ? { ok: true, revision: 2, items: [
      { state: 'active', resolution_id: 'resolution:' + 'c'.repeat(64), reason: 'Previously reviewed', members: plan.members, current_validity: { valid: true } },
    ] } : undefined);
    const { section } = await setup(load);
    action(section, '이 결정 되돌리기').click(); await flush();
    expect(load.mock.calls.some(([name]) => name === 'collection-resolution-revert')).toBe(false);
    action(section, '되돌리기 확정').click(); await flush();
    expect(load.mock.calls.filter(([name]) => name === 'collection-resolution-revert')).toHaveLength(1);
  });
  it('recovers from a rejected find request without leaving controls busy', async () => {
    const load = port(name => name === 'collection-graph' ? Promise.reject(new Error('synthetic read failure')) : undefined);
    const { section } = await setup(load);
    action(section, '자료 찾기').click(); await flush();
    expect(action(section, '자료 찾기').disabled).toBe(false);
    expect(section.querySelector('[role="status"]')!.textContent).toContain('확인하지 못했습니다');
  });
  it('submits an explicitly approved decision at most once on rapid double click', async () => {
    let finish!: (value: Record<string, unknown>) => void;
    const load = port(name => name === 'collection-resolution-apply' ? new Promise(resolve => { finish = resolve; }) : undefined);
    const { section } = await setup(load); await choose(section);
    const checkbox = section.querySelector<HTMLInputElement>('.entity-resolution-approval input')!;
    checkbox.checked = true; checkbox.dispatchEvent(new Event('change'));
    const submit = action(section, '같은 지식으로 묶기'); submit.click(); submit.click(); await flush();
    expect(load.mock.calls.filter(([name]) => name === 'collection-resolution-apply')).toHaveLength(1);
    finish({ ok: true }); await flush();
    expect(document.defaultView!.localStorage.getItem(storageKey)).toBeNull();
  });
});
