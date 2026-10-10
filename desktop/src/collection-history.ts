import { fetchSettingsAction } from './runtime';
import { VirtualList } from './virtual-list';
import type { SettingsPage } from './settings-navigation';

type Row = Record<string, unknown>;
const stages: Record<string, string> = {
  discovered: '자료 발견', parsed: '내용 읽음', validated: '형식 확인',
  stored: '저장 완료', indexed: '검색 반영', failed: '실패', paused: '중지',
};
const rows = (value: unknown): Row[] => Array.isArray(value)
  ? value.filter(v => v && typeof v === 'object' && !Array.isArray(v)) as Row[] : [];
const validEvents = (value: unknown): Row[] => rows(value).filter(row =>
  Number.isSafeInteger(row.sequence) && Number(row.sequence) > 0);
const WINDOW_LIMIT = 1000;
const changes = ['added', 'revised', 'unchanged', 'removed', 'relations_changed'] as const;
type Counts = { total: number; stages: Record<string, number>; changes: Record<string, number> };
function counts(value: unknown): Counts | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Row, stagesRow = row.stages as Row | undefined, changesRow = row.changes as Row | undefined;
  const valid = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
  if (!valid(row.total) || !stagesRow || !changesRow
    || !Object.keys(stages).every(key => valid(stagesRow[key])) || !changes.every(key => valid(changesRow[key]))) return null;
  const result = { total: row.total, stages: { ...stagesRow }, changes: { ...changesRow } } as Counts;
  return Object.keys(stages).reduce((sum, key) => sum + result.stages[key], 0) === result.total ? result : null;
}

export function wireCollectionHistory(load: typeof fetchSettingsAction = fetchSettingsAction) {
  const host = document.getElementById('settings-page-history');
  if (!host) return { select(_page: SettingsPage) {}, visible(_flag: boolean) {}, dispose() {} };
  const make = (tag: string, className: string, text: string) => {
    const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
  };
  const select = (label: string, all: string) => {
    const node = document.createElement('select'); node.setAttribute('aria-label', label);
    const option = document.createElement('option'); option.value = ''; option.textContent = all;
    node.append(option); return node;
  };
  const date = (label: string) => {
    const wrap = document.createElement('label'); wrap.textContent = label;
    const input = document.createElement('input'); input.type = 'date'; input.setAttribute('aria-label', label);
    wrap.append(input); return { wrap, input };
  };
  const section = make('section', 'collection-history', '');
  section.setAttribute('aria-label', '여러 출처의 기억 정리');
  const heading = make('h2', '', '자료가 기억으로 이어지는 과정');
  const toolbar = make('div', 'collection-filters', '');
  const project = select('프로젝트', '모든 프로젝트');
  const target = select('수집 대상', '모든 대상');
  const stage = select('처리 단계', '모든 단계');
  for (const [key, label] of Object.entries(stages)) {
    const option = document.createElement('option'); option.value = key; option.textContent = label; stage.append(option);
  }
  const search = document.createElement('input'); search.type = 'search';
  search.placeholder = '대상 또는 자료 검색'; search.setAttribute('aria-label', '수집 자료 검색');
  toolbar.append(project, target, stage, search);
  const extra = document.createElement('details'); extra.className = 'collection-extra-filters';
  const summary = document.createElement('summary'); summary.textContent = '출처와 기간'; extra.append(summary);
  const source = select('자료 출처', '모든 출처');
  for (const [key, label] of [['youtube', 'YouTube'], ['threads', 'Threads'], ['files', '파일'], ['graph', '기존 그래프']]) {
    const option = document.createElement('option'); option.value = key; option.textContent = label; source.append(option);
  }
  const start = date('시작일'), end = date('종료일');
  const extraFields = make('div', 'collection-filter-dates', '');
  extraFields.append(source, start.wrap, end.wrap); extra.append(extraFields);
  const status = make('p', 'collection-status', ''); status.setAttribute('aria-live', 'polite');
  const totals = make('p', 'collection-summary', ''); totals.hidden = true;
  const list = make('div', 'collection-event-list', '');
  list.setAttribute('role', 'list'); list.setAttribute('aria-label', '수집·저장·검색 반영 이력');
  const older = document.createElement('button'); older.type = 'button';
  older.className = 'history-older'; older.textContent = '이전 처리 더 보기'; older.hidden = true;
  const latest = document.createElement('button'); latest.type = 'button';
  latest.className = 'history-latest'; latest.textContent = '최신 이력 보기'; latest.hidden = true;
  section.append(heading, toolbar, extra, status, totals, list, older, latest);
  host.querySelector('.settings-page-heading')?.after(section); if (!section.parentElement) host.prepend(section);

  let page: SettingsPage = 'memory', visible = true, dead = false, busy = false, epoch = 0;
  let before: number | null = null, newest: number | null = null, items: Row[] = [];
  let stream: string | null = null, aggregate: Counts | null = null;
  let historical = false, newCount = 0;
  const expanded = new Set<number>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  const listeners = new AbortController();
  const virtual = new VirtualList<Row>(list as HTMLElement, row => String(row.sequence), row => {
    const article = make('article', 'collection-event-row', ''); article.setAttribute('role', 'listitem');
    const at = Number(row.at);
    const time = make('time', '', Number.isFinite(at) ? new Date(at * 1000).toLocaleString('ko-KR', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }) : '시각 미확인');
    const copy = make('div', 'collection-event-copy', '');
    copy.append(make('strong', '', String(row.target_label ?? '대상')), make('span', '', String(row.document_label ?? '처리 기록')));
    const details = row.details && typeof row.details === 'object' ? row.details as Row : {};
    const unchanged = details.change === 'unchanged';
    const label = unchanged && row.stage === 'stored' ? '기존 자료 유지' : stages[String(row.stage)] ?? '처리 기록';
    const badge = make('span', 'collection-stage', label); badge.dataset.stage = String(row.stage);
    const more = document.createElement('details'); more.className = 'collection-event-detail';
    more.dataset.sequence = String(row.sequence); more.open = expanded.has(Number(row.sequence));
    const detailTitle = document.createElement('summary'); detailTitle.textContent = '근거와 처리 내역'; more.append(detailTitle);
    more.append(make('p', '', '프로젝트: ' + (Array.isArray(row.projects) ? row.projects.join(', ') : '미확인')));
    if (typeof row.origin === 'string' && row.origin) more.append(make('p', '', '수집 경로: ' + row.origin));
    const explanation = row.stage === 'validated' ? '원본 ID·내용 해시·자료 형식을 확인했습니다. 내용의 사실 여부를 검증한 단계는 아닙니다.'
      : row.stage === 'indexed' ? '텍스트 검색에 반영했습니다. 임베딩 검색 반영은 아직 확인하지 않았습니다.'
      : unchanged ? '이미 저장된 내용과 같습니다. 새 지식이 추가된 기록으로 세지 않습니다.'
      : String(details.reason ?? '원문 ID와 저장 버전을 연결한 기록입니다.');
    more.append(make('p', '', explanation));
    const identifiers = document.createElement('dl');
    for (const [label, value] of [['실행', row.run_id], ['원문 항목', row.document_id], ['저장 버전', row.version]]) {
      if (typeof value === 'string' && value) identifiers.append(make('dt', '', String(label)), make('dd', '', value));
    }
    more.append(identifiers);
    if (typeof row.source_url === 'string') {
      try {
        const url = new URL(row.source_url);
        if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password) {
          const link = document.createElement('a'); link.href = url.href; link.textContent = '원문 열기';
          link.target = '_blank'; link.rel = 'noopener noreferrer'; more.append(link);
        }
      } catch { /* Invalid URLs remain unlinked. */ }
    }
    article.append(time, copy, badge, more); return article;
  }, 82);
  list.addEventListener('toggle', event => {
    const detail = event.target;
    if (!(detail instanceof HTMLDetailsElement) || !list.contains(detail)) return;
    const sequence = Number(detail.dataset.sequence);
    if (detail.open) expanded.add(sequence); else expanded.delete(sequence);
  }, { capture: true, signal: listeners.signal });
  virtual.setVisible(false);
  const stopTimer = () => { if (timer) clearTimeout(timer); timer = null; };
  const populate = (node: HTMLSelectElement, value: unknown, key: string, label: string) => {
    const selected = node.value; const first = node.options[0]; node.replaceChildren(first);
    for (const row of rows(value)) {
      if (typeof row[key] !== 'string') continue;
      const option = document.createElement('option'); option.value = row[key] as string;
      option.textContent = String(row[label] ?? row[key]); node.append(option);
    }
    node.value = selected; if (node.selectedIndex < 0) node.value = '';
  };
  const filters = () => {
    const options: Row = { search: search.value };
    if (project.value) options.projects = [project.value];
    if (target.value) options.target_id = target.value;
    if (stage.value) options.stage = stage.value;
    if (source.value) options.platform = source.value;
    if (start.input.value) options.since = new Date(start.input.value + 'T00:00:00').getTime() / 1000;
    if (end.input.value) {
      const boundary = new Date(end.input.value + 'T00:00:00'); boundary.setDate(boundary.getDate() + 1);
      options.until = boundary.getTime() / 1000;
    }
    return options;
  };
  async function refresh(olderPage = false): Promise<void> {
    if (dead || !visible || page !== 'history' || busy) return;
    stopTimer(); busy = true; const ticket = epoch;
    section.setAttribute('aria-busy', 'true');
    if (!items.length) status.textContent = '수집 이력을 불러옵니다.';
    const forward = !olderPage && newest !== null;
    let delay = 2500;
    try {
      const options = { ...filters(), limit: forward ? 200 : 50,
        ...(stream ? { stream_id: stream } : {}),
        ...(olderPage ? { before } : forward ? { after: newest } : {}) };
      const result = await load('collection-history', { query: JSON.stringify(options) });
      if (dead || ticket !== epoch || !visible || page !== 'history') return;
      if (result?.ok !== true) { section.dataset.state = 'error'; status.textContent = '수집 이력을 불러오지 못했습니다.'; delay = 5000; return; }
      section.dataset.state = result.state === 'not_configured' ? 'not_configured' : 'ready';
      populate(project, result.projects, 'project', 'project'); populate(target, result.targets, 'id', 'label');
      const nextStream = typeof result.stream_id === 'string' && result.stream_id.length <= 128 ? result.stream_id : null;
      const restarting = result.reset === true || nextStream !== null && stream !== null && stream !== nextStream;
      if (restarting) {
        items = []; newest = before = null; historical = false; newCount = 0; expanded.clear(); aggregate = null;
      }
      if (nextStream) stream = nextStream;
      const append = forward && !restarting;
      const incoming = validEvents(result.items).filter(row => !append || Number(row.sequence) > (newest ?? 0));
      const snapshotCounts = counts(result.summary);
      if (snapshotCounts) aggregate = snapshotCounts;
      else if (append && aggregate) for (const row of incoming) {
        aggregate.total++;
        if (String(row.stage) in aggregate.stages) aggregate.stages[String(row.stage)]++;
        const detail = row.details as Row | undefined;
        if (row.stage === 'stored' && detail && String(detail.change) in aggregate.changes) aggregate.changes[String(detail.change)]++;
      }
      if (incoming.length) newest = Math.max(newest ?? 0, ...incoming.map(row => Number(row.sequence)));
      if ((!olderPage || restarting) && Number.isSafeInteger(result.cursor) && Number(result.cursor) >= (newest ?? 0)) newest = Number(result.cursor);
      if (append && result.next !== null && result.next !== undefined) delay = 500;
      if (append && historical) newCount += incoming.length;
      else {
        const known = new Map(items.map(row => [Number(row.sequence), row]));
        for (const row of incoming) known.set(Number(row.sequence), row);
        items = [...known.values()].sort((a, b) => Number(b.sequence) - Number(a.sequence));
        if (items.length > WINDOW_LIMIT) items = olderPage ? items.slice(-WINDOW_LIMIT) : items.slice(0, WINDOW_LIMIT);
        const retained = new Set(items.map(row => Number(row.sequence)));
        for (const sequence of expanded) if (!retained.has(sequence)) expanded.delete(sequence);
        if (!append || incoming.length) virtual.set(items, { preserve: !olderPage && !restarting });
      }
      if (olderPage || !append) before = typeof result.next === 'number' ? result.next : null;
      if (olderPage && !restarting) historical = true;
      older.hidden = before === null; latest.hidden = !historical;
      totals.hidden = aggregate === null;
      if (aggregate) totals.textContent = '선택한 범위 · ' + aggregate.total.toLocaleString('ko-KR') + '개 단계 기록'
        + ' · 추가 ' + aggregate.changes.added.toLocaleString('ko-KR') + ' · 수정 ' + aggregate.changes.revised.toLocaleString('ko-KR')
        + ' · 유지 ' + aggregate.changes.unchanged.toLocaleString('ko-KR') + ' · 실패 ' + aggregate.stages.failed.toLocaleString('ko-KR')
        + ' · 중지 ' + aggregate.stages.paused.toLocaleString('ko-KR');
      status.textContent = result.state === 'not_configured' ? '아직 수집 대상을 연결하지 않았습니다.'
        : newCount ? '새 처리 이력 ' + newCount + '개 · 최신 이력 보기로 돌아갈 수 있습니다.'
        : items.length ? '현재 목록 ' + items.length + '개 · 각 단계는 확인된 작업을 표시합니다.'
        : '선택한 범위의 처리 이력이 없습니다.';
      if (result.state === 'not_configured') delay = 15000;
    } catch { if (!dead && ticket === epoch) { section.dataset.state = 'error'; status.textContent = '수집 이력을 불러오지 못했습니다.'; } delay = 5000; }
    finally {
      busy = false;
      section.setAttribute('aria-busy', 'false');
      if (!dead && visible && page === 'history') {
        if (ticket !== epoch) void refresh();
        else timer = setTimeout(() => void refresh(), delay);
      }
    }
  }
  const reset = () => {
    epoch++; items = []; before = null; newest = null; stream = null; aggregate = null; totals.hidden = true;
    historical = false; newCount = 0; expanded.clear();
    older.hidden = true; latest.hidden = true; virtual.set([]); stopTimer(); void refresh();
  };
  project.addEventListener('change', () => { target.value = ''; reset(); }, { signal: listeners.signal });
  for (const input of [target, stage, source, search, start.input, end.input]) input.addEventListener('change', reset, { signal: listeners.signal });
  older.addEventListener('click', () => void refresh(true), { signal: listeners.signal });
  latest.addEventListener('click', reset, { signal: listeners.signal });
  return {
    select(next: SettingsPage) {
      if (page === next) return; page = next; epoch++; stopTimer();
      virtual.setVisible(visible && page === 'history'); if (page === 'history') void refresh();
    },
    visible(flag: boolean) {
      if (visible === flag) return; visible = flag; epoch++; stopTimer();
      virtual.setVisible(flag && page === 'history'); if (flag && page === 'history') void refresh();
    },
    dispose() { dead = true; epoch++; listeners.abort(); stopTimer(); virtual.dispose(); section.remove(); },
  };
}
