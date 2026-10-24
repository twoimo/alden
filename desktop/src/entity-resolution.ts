import { fetchSettingsAction } from './runtime';
import type { SettingsPage } from './settings-navigation';

type Row = Record<string, unknown>;
type Member = { target_id: string; document_id: string; version: string };
type Pending = { action: 'collection-resolution-apply' | 'collection-resolution-revert'; query: Row; project: string };
const storageKey = 'alden-entity-resolution-pending-v1';
const rows = (value: unknown): Row[] => Array.isArray(value)
  ? value.filter((row): row is Row => !!row && typeof row === 'object' && !Array.isArray(row)) : [];
const text = (value: unknown): string => typeof value === 'string' ? value : '';
const object = (value: unknown): Row | null => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : null;
const nonempty = (value: unknown, maximum = 256): value is string =>
  typeof value === 'string' && !!value.trim() && value.length <= maximum;
const hexSha = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const memberKey = (member: Member) => JSON.stringify([member.target_id, member.document_id, member.version]);
function reviewableRecord(row: Row, member: Member): boolean {
  const body = text(row.body).trim();
  const evidence = object(row.content_evidence);
  return row.basis === 'permitted_current_stored_record'
    && row.target_id === member.target_id && row.document_id === member.document_id && row.version === member.version
    && row.truncated === false && !!body
    && evidence?.policy === 'source-content-evidence-v6' && evidence.classification === 'content_candidate';
}

function matchesRequestedPlan(value: Row, project: string, members: Member[], reason: string): boolean {
  const canonical = object(value.canonical);
  const planMembers = rows(value.members);
  const requested = new Set(members.map(memberKey));
  const received = planMembers.map(row => memberKey({ target_id: text(row.target_id),
    document_id: text(row.document_id), version: text(row.version) }));
  return value.schema === 1 && value.project === project && value.reason === reason
    && Number.isSafeInteger(value.revision) && Number(value.revision) >= 0
    && hexSha(value.source_fingerprint) && hexSha(value.plan_sha256)
    && canonical?.target_id === members[0].target_id && canonical.document_id === members[0].document_id
    && received.length === requested.size && new Set(received).size === requested.size
    && received.every(key => requested.has(key));
}

function validPending(value: unknown): value is Pending {
  const saved = object(value), query = object(saved?.query);
  if (!saved || !query || !nonempty(saved.project, 128) || !nonempty(query.request_id)) return false;
  if (saved.action === 'collection-resolution-apply') {
    const plan = object(query.plan);
    return !!plan && plan.project === saved.project && plan.schema === 1
      && Array.isArray(plan.members) && plan.members.length >= 2 && plan.members.length <= 16
      && !!object(plan.canonical) && nonempty(plan.reason, 1024)
      && hexSha(plan.source_fingerprint) && hexSha(plan.plan_sha256)
      && Number.isSafeInteger(plan.revision) && Number(plan.revision) >= 0;
  }
  return saved.action === 'collection-resolution-revert'
    && Array.isArray(query.projects) && query.projects.length === 1 && query.projects[0] === saved.project
    && nonempty(query.resolution_id, 128) && /^resolution:[0-9a-f]{64}$/.test(query.resolution_id)
    && Number.isSafeInteger(query.expected_revision) && Number(query.expected_revision) >= 0;
}

/** Explicit source review. No similarity score grants identity or write authority. */
export function wireEntityResolution(load: typeof fetchSettingsAction = fetchSettingsAction, root: Document = document) {
  const host = root.getElementById('settings-page-history');
  if (!host) return { select(_page: SettingsPage) {}, visible(_flag: boolean) {}, dispose() {} };
  const make = <K extends keyof HTMLElementTagNameMap>(tag: K, label = '') => {
    const node = root.createElement(tag); node.textContent = label; return node;
  };
  const button = (label: string) => { const node = make('button', label); node.type = 'button'; return node; };
  const section = make('details'); section.className = 'entity-resolution';
  section.append(make('summary', '중복 지식 검토'));
  const introduction = make('p', '저장된 자료를 비교해 같은 지식으로 묶을 수 있습니다. 원래 자료와 연결은 보존되며 결정은 되돌릴 수 있습니다.');
  const project = make('select'); project.setAttribute('aria-label', '중복 검토 프로젝트');
  const emptyProject = make('option', '프로젝트 선택'); emptyProject.value = ''; project.append(emptyProject);
  const search = make('input'); search.type = 'search'; search.placeholder = '비교할 자료 검색'; search.setAttribute('aria-label', '비교할 자료 검색');
  const find = button('자료 찾기'), preview = button('선택한 자료 비교'), apply = button('같은 지식으로 묶기'), retry = button('대기 요청 결과 확인');
  const status = make('p'); status.className = 'collection-status'; status.setAttribute('role', 'status');
  const list = make('div'); list.className = 'entity-resolution-list';
  const comparison = make('div'); comparison.className = 'entity-resolution-comparison';
  const reason = make('textarea'); reason.rows = 2; reason.maxLength = 500; reason.placeholder = '같은 지식으로 판단한 근거'; reason.setAttribute('aria-label', '병합 판단 근거');
  const confirmation = make('input'); confirmation.type = 'checkbox';
  const confirmationLabel = make('label'); confirmationLabel.append(confirmation, root.createTextNode('원문에서 같은 대상·지식임을 확인했습니다.'));
  const history = make('div'); history.className = 'entity-resolution-history';
  const toolbar = make('div'); toolbar.className = 'entity-resolution-toolbar'; toolbar.append(project, search, find, preview);
  const approval = make('div'); approval.className = 'entity-resolution-approval'; approval.append(reason, confirmationLabel, apply);
  section.append(introduction, toolbar, status, list, comparison, approval, retry, make('h3', '검토 결정 이력'), history);
  host.append(section);
  const controls = new AbortController(), signal = controls.signal;
  let page: SettingsPage = 'memory', visible = true, dead = false, epoch = 0, reviewEpoch = 0, busy = false;
  let initialized = false, plan: Row | null = null, revision = 0;
  const selected = new Map<string, Member>();
  let pending: Pending | null = null, blockedStorage = false;
  try {
    const saved = root.defaultView!.localStorage.getItem(storageKey);
    if (saved !== null) {
      const parsed: unknown = JSON.parse(saved);
      if (validPending(parsed)) pending = parsed;
      else blockedStorage = true;
    }
  } catch { blockedStorage = true; }
  if (blockedStorage) status.textContent = '보관된 요청 ID 또는 내용이 올바르지 않습니다. 저장 상태 확인 전에는 변경할 수 없습니다.';
  const active = () => !dead && visible && page === 'history' && section.open;
  function update() {
    const locked = busy || !!pending || blockedStorage;
    project.disabled = locked;
    search.disabled = locked;
    find.disabled = locked || !project.value;
    preview.disabled = locked || selected.size < 2 || selected.size > 16 || !reason.value.trim();
    apply.disabled = locked || !plan || !confirmation.checked;
    reason.disabled = locked;
    confirmation.disabled = locked || !plan;
    retry.hidden = !pending; retry.disabled = busy || blockedStorage;
    for (const input of list.querySelectorAll<HTMLInputElement>('input')) input.disabled = locked;
    for (const input of history.querySelectorAll<HTMLButtonElement>('button')) input.disabled = locked;
  }
  function clearReview() {
    reviewEpoch++; plan = null; confirmation.checked = false; comparison.replaceChildren();
  }
  function invalidate() {
    epoch++; clearReview(); selected.clear(); list.replaceChildren(); update();
  }
  const queryProject = () => ({ projects: [project.value] });
  function notifyGraphChange(result: Row) {
    if (result.ok === true) root.defaultView?.dispatchEvent(new Event('alden://entity-resolution-changed'));
  }
  async function readHistory(ticket = epoch) {
    let response: Row | null = null;
    try { response = await load('collection-resolution-history', { query: JSON.stringify(queryProject()) }); }
    catch { /* A failed history read never starts a write. */ }
    if (!active() || ticket !== epoch) return;
    if (response?.ok !== true) { status.textContent = '결정 이력을 확인하지 못했습니다.'; return; }
    revision = Number.isSafeInteger(response.revision) ? Number(response.revision) : 0;
    history.replaceChildren(...rows(response.items).map(row => {
      const item = make('div'); item.className = 'entity-resolution-decision';
      const count = Number.isSafeInteger(row.member_count) ? Number(row.member_count) : rows(row.members).length;
      const validity = row.current_validity as Row | undefined;
      const label = row.state !== 'active' ? '되돌린 결정' : validity?.valid === true ? '현재 유효한 묶음' : '원문 변경·권한 확인 필요';
      item.append(make('p', label + ' · 자료 ' + count + '개'), make('p', text(row.reason)));
      if (row.state === 'active' && typeof row.resolution_id === 'string') {
        const undo = button('이 결정 되돌리기'); undo.disabled = busy || !!pending || blockedStorage;
        undo.addEventListener('click', () => {
          if (undo.disabled || !active()) return;
          undo.hidden = true;
          const confirmUndo = button('되돌리기 확정'), cancelUndo = button('취소');
          const scope = project.value, expectedRevision = revision;
          const stop = () => { confirmUndo.remove(); cancelUndo.remove(); undo.hidden = false; };
          cancelUndo.addEventListener('click', stop, { signal });
          confirmUndo.addEventListener('click', () => {
            if (confirmUndo.disabled || !active() || project.value !== scope) return;
            void mutate({ action: 'collection-resolution-revert', project: scope,
              query: { projects: [scope], resolution_id: row.resolution_id, expected_revision: expectedRevision, request_id: crypto.randomUUID() } });
          }, { signal });
          item.append(confirmUndo, cancelUndo); update();
        }, { signal }); item.append(undo);
      }
      return item;
    }));
  }
  async function initialize() {
    if (initialized || !active()) return;
    const ticket = epoch;
    let response: Row | null = null;
    try { response = await load('collection-projects'); }
    catch { /* The panel can be reopened or refreshed later. */ }
    if (!active() || ticket !== epoch) return;
    if (response?.ok !== true) { status.textContent = '프로젝트를 확인하지 못했습니다.'; return; }
    project.replaceChildren(emptyProject, ...rows(response.projects).filter(row => typeof row.project === 'string').map(row => {
      const option = make('option', text(row.project)); option.value = text(row.project); return option;
    }));
    initialized = true;
    if (pending) {
      if (![...project.options].some(option => option.value === pending!.project)) {
        const option = make('option', pending.project); option.value = pending.project; project.append(option);
      }
      project.value = pending.project; status.textContent = '이전 요청의 결과 확인이 필요합니다.';
      void readHistory();
    }
    update();
  }
  function clearSavedRequest(request: Pending): boolean {
    try {
      const storage = root.defaultView!.localStorage;
      if (storage.getItem(storageKey) === JSON.stringify(request)) {
        storage.removeItem(storageKey); return true;
      }
    } catch { /* The pending request could not be safely removed. */ }
    blockedStorage = true;
    return false;
  }
  async function mutate(request: Pending) {
    if (busy || blockedStorage || !active() || pending && JSON.stringify(request) !== JSON.stringify(pending)) return;
    if (!validPending(request)) { status.textContent = '요청 ID 또는 내용이 올바르지 않아 변경을 시작하지 않았습니다.'; return; }
    try {
      const storage = root.defaultView!.localStorage, encoded = JSON.stringify(request);
      const existing = storage.getItem(storageKey);
      if (existing !== null && existing !== encoded) {
        blockedStorage = true; status.textContent = '다른 대기 요청이 있습니다. 새 변경을 시작하지 않았습니다.';
        update(); return;
      }
      storage.setItem(storageKey, encoded);
    } catch { status.textContent = '요청 ID를 보존하지 못해 변경을 시작하지 않았습니다.'; return; }
    pending = request; busy = true; update();
    let response: Row | null = null;
    try { response = await load(request.action, { query: JSON.stringify(request.query), explicitOptIn: true }); }
    catch { /* Keep the exact request for reconciliation and idempotent retry. */ }
    busy = false;
    if (response && typeof response.ok === 'boolean') {
      pending = null; const recovered = clearSavedRequest(request);
      invalidate();
      if (active()) {
        status.textContent = !recovered ? '저장된 대기 요청이 달라 확인이 필요합니다.'
          : response.ok ? '결정을 저장했습니다. 원래 자료와 연결은 보존됩니다.' : '변경하지 못했습니다. ' + text(response.reason);
        void readHistory();
      }
      notifyGraphChange(response);
    } else if (active()) status.textContent = '결과가 확인되지 않았습니다. 같은 요청 ID로 결과를 확인하세요.';
    update();
  }
  async function reconcile() {
    if (!pending || busy || !active()) return;
    const request = pending, ticket = epoch;
    busy = true; update();
    let response: Row | null = null;
    try { response = await load('collection-resolution-history', { query: JSON.stringify({ projects: [request.project], request_id: request.query.request_id }) }); }
    catch { /* No new ID or automatic retry. */ }
    busy = false;
    if (!active() || ticket !== epoch) { update(); return; }
    const completed = response?.request_result;
    if (completed && typeof completed === 'object' && typeof (completed as Row).ok === 'boolean') {
      pending = null; const recovered = clearSavedRequest(request);
      const result = completed as Row;
      invalidate();
      status.textContent = !recovered ? '저장된 대기 요청이 달라 확인이 필요합니다.'
        : result.ok ? '이전 요청이 저장된 것을 확인했습니다.' : '이전 요청이 거절되었습니다. ' + text(result.reason);
      notifyGraphChange(result);
      void readHistory(); update();
    } else if (response?.ok === true) await mutate(request);
    else { status.textContent = '이전 요청 결과를 확인하지 못했습니다. 새 변경은 시작하지 않습니다.'; update(); }
  }
  find.addEventListener('click', async () => {
    if (busy || pending || blockedStorage || !active() || !project.value) return;
    invalidate(); const ticket = epoch; busy = true; update();
    let response: Row | null = null;
    try { response = await load('collection-graph', { query: JSON.stringify({
      ...queryProject(), search: search.value.trim().slice(0, 256), limit: 100, overview: true,
    }) }); } catch { /* Recover controls after a failed read. */ }
    busy = false;
    if (!active() || ticket !== epoch) { update(); return; }
    if (response?.ok !== true) { status.textContent = '자료를 확인하지 못했습니다.'; update(); return; }
    const candidates = rows(response.nodes).filter(row => typeof row.id === 'string' && typeof row.source_target === 'string' && typeof row.source_version === 'string');
    list.replaceChildren(...candidates.map(row => {
      const label = make('label'); const box = make('input'); box.type = 'checkbox';
      const member = { target_id: text(row.source_target), document_id: text(row.id), version: text(row.source_version) };
      box.addEventListener('change', () => {
        clearReview();
        if (box.checked && selected.size < 16) selected.set(text(row.id), member);
        else { selected.delete(text(row.id)); box.checked = false; }
        update();
      }, { signal }); label.append(box, root.createTextNode(text(row.label)), make('small', text(row.source_platform))); return label;
    }));
    status.textContent = candidates.length + '개 표시 · 같은 지식으로 의심되는 자료를 선택하세요.'; update();
  }, { signal });
  preview.addEventListener('click', async () => {
    if (preview.disabled || !active()) return;
    const ticket = epoch, members = [...selected.values()], projectName = project.value, givenReason = reason.value.trim();
    clearReview(); const reviewTicket = reviewEpoch;
    busy = true; update();
    let response: Row | null = null;
    try { response = await load('collection-resolution-preview', { query: JSON.stringify({
      projects: [projectName], members,
      canonical: { target_id: members[0].target_id, document_id: members[0].document_id }, reason: givenReason,
    }) }); } catch { /* Keep review disabled on a failed preview. */ }
    busy = false;
    if (!active() || ticket !== epoch || reviewTicket !== reviewEpoch) { update(); return; }
    if (response?.ok !== true) {
      status.textContent = '비교하지 못했습니다. ' + text(response?.reason); update(); return;
    }
    const candidate = object(response.plan);
    if (!candidate || !matchesRequestedPlan(candidate, projectName, members, givenReason)) {
      status.textContent = '비교 결과가 현재 선택·근거와 일치하지 않습니다. 다시 비교하세요.'; update(); return;
    }
    const records = rows(response.records);
    const recordIds = new Set(records.map(row => memberKey({ target_id: text(row.target_id),
      document_id: text(row.document_id), version: text(row.version) })));
    const sourceVerified = records.length === members.length && recordIds.size === members.length
      && members.every(member => records.some(row => reviewableRecord(row, member)));
    comparison.replaceChildren(...records.map(row => {
      const article = make('article'); article.append(make('h4', text(row.label) || text(row.original_id)), make('pre', text(row.body)));
      const evidence = row.content_evidence as Row | undefined;
      const classification = text(evidence?.classification);
      const description = ['metadata_only', 'source_metadata_only', 'metadata_description'].includes(classification)
        ? '이름·자료 설명입니다. 실제 내용의 동일성은 확인되지 않았습니다.'
        : classification === 'unread_body_marker' ? '본문 미열람 안내입니다.'
        : row.body_source === 'retained_record.localOriginalText' ? '보관된 본문입니다.'
        : '저장된 본문·설명입니다. 원래 파일 전체의 확인과 구분됩니다.';
      article.append(make('p', description));
      if (row.truncated === true) article.append(make('p', '보관된 본문 일부를 표시했습니다. 전체 내용을 확인한 뒤 판단하세요.'));
      const url = text(row.source_url);
      try {
        const parsed = new URL(url);
        if (['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password) {
          const link = make('a', '원문 출처'); link.href = parsed.href; link.rel = 'noopener noreferrer'; link.target = '_blank'; article.append(link);
        }
      } catch { /* Missing or invalid source URLs are not fabricated. */ }
      return article;
    }));
    if (!sourceVerified) {
      status.textContent = '원문이 비어 있거나 메타데이터·일부 내용만 있어 병합을 확인할 수 없습니다.';
      update(); return;
    }
    plan = candidate;
    status.textContent = '처음 선택한 자료를 대표로 묶습니다. 날짜·작성자·대상과 원문이 같은 지식인지 확인하세요.'; update();
  }, { signal });
  apply.addEventListener('click', () => {
    if (!apply.disabled && plan) void mutate({ action: 'collection-resolution-apply', project: project.value, query: { plan, request_id: crypto.randomUUID() } });
  }, { signal });
  retry.addEventListener('click', () => void reconcile(), { signal });
  project.addEventListener('change', () => { invalidate(); history.replaceChildren(); if (project.value) void readHistory(); }, { signal });
  search.addEventListener('input', invalidate, { signal });
  reason.addEventListener('input', () => { clearReview(); update(); }, { signal });
  confirmation.addEventListener('change', update, { signal });
  const refreshVisible = () => { if (active()) { if (!initialized) void initialize(); else if (project.value) void readHistory(); } };
  section.addEventListener('toggle', () => { epoch++; clearReview(); update(); refreshVisible(); }, { signal });
  update();
  return {
    select(next: SettingsPage) { if (page === next) return; page = next; epoch++; clearReview(); update(); refreshVisible(); },
    visible(flag: boolean) { if (visible === flag) return; visible = flag; epoch++; clearReview(); update(); refreshVisible(); },
    dispose() { dead = true; epoch++; controls.abort(); section.remove(); },
  };
}
