import type { KnowledgeGraph, KnowledgeNode } from './graph-model';

function text(value: unknown, limit = 1200): string {
  return typeof value === 'string' ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').trim().slice(0, limit) : '';
}
function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function element(tag: string, className: string, copy = ''): HTMLElement {
  const item = document.createElement(tag); item.className = className; item.textContent = copy; return item;
}
function date(value: unknown): string {
  const epoch = typeof value === 'number' ? value * 1000 : Date.parse(text(value));
  return Number.isFinite(epoch) && epoch > 0 ? new Date(epoch).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }) : '시점 미확인';
}

function separatedNumericTail(selected: Record<string, unknown> | null, root: Document): HTMLElement | null {
  const projection = record(selected?.text_projection);
  const tail = projection?.separated_tail;
  if (!selected || selected.basis !== 'source_record' || !projection
    || projection.policy !== 'threads-separated-numeric-tail-v1'
    || projection.status !== 'structural_tail_candidate' || projection.original_preserved !== true
    || projection.semantic_validity !== 'unverified' || typeof tail !== 'string' || tail.length === 0 || tail.length > 128
    || projection.removed_characters !== tail.length
    || !['original_body_sha256', 'body_sha256', 'separated_tail_sha256'].every(key => typeof projection[key] === 'string' && /^[0-9a-f]{64}$/.test(projection[key] as string))
    || !/^[0-9a-f]{64}$/.test(text(selected.raw_sha256))) return null;
  const note = root.createElement('details'); note.className = 'knowledge-separated-tail';
  note.append(element('summary', '', '본문에서 뺀 숫자 묶음'));
  note.append(element('p', '', '화면에서 추출된 숫자로 보이는 부분을 본문과 분리했습니다. 원문은 보존했습니다.'));
  note.append(element('pre', 'knowledge-separated-tail-text', tail));
  return note;
}

const boundPanes = new WeakSet<HTMLElement>();

function readingCandidate(selected: Record<string, unknown> | null, copy: string): {reading:string;tail:string} | null {
  const value = record(selected?.reading_projection);
  const graphTail = value?.policy === 'graph-reading-numeric-lines-v1';
  const legacyTail = value?.policy === 'legacy-threads-reading-tail-v1';
  if (selected?.basis !== 'source_record' || selected.body_format !== 'retained_original_text'
    || !/^[0-9a-f]{64}$/.test(text(selected.raw_sha256)) || !value
    || (!graphTail && !legacyTail) || value.status !== 'unverified_presentation_candidate'
    || (legacyTail && value.basis !== 'verified_retained_legacy_threads_record')
    || (graphTail && value.basis !== 'retained_graph_original_text')
    || value.original_preserved !== true
    || value.semantic_validity !== 'unverified' || typeof value.reading !== 'string' || !value.reading
    || typeof value.tail !== 'string' || !value.tail || value.tail.length > 128
    || value.reading + value.tail !== copy
    || !['body_sha256','reading_sha256','tail_sha256'].every(key => typeof value[key] === 'string' && /^[0-9a-f]{64}$/.test(value[key] as string))) return null;
  const tokens = value.tail.trim().split(/\s+/);
  if (graphTail) {
    if (tokens.length < 3 || tokens.length > 6 || tokens.some(token => !/^[0-9]{1,2}$/.test(token))) return null;
  } else if (tokens.length < 2 || tokens.length > 8 || tokens.some(token => !/^(?:[0-9]{1,4}|\/)$/.test(token))
    || tokens.filter(token => token !== '/').length < (tokens.includes('/') ? 3 : 2)) return null;
  return {reading:value.reading,tail:value.tail};
}

function bindNotePane(root: Document, pane: HTMLDialogElement): void {
  if (!boundPanes.has(pane)) {
    let composing = false, backdropPressed = false;
    const close = () => root.getElementById('knowledge-focus-close')?.click();
    const outside = (event: MouseEvent) => {
      const bounds = pane.getBoundingClientRect();
      return event.target === pane && (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom);
    };
    pane.addEventListener('compositionstart', () => { composing = true; });
    pane.addEventListener('compositionend', () => { composing = false; });
    // Use the controller's existing close action, including its retrieval fence.
    pane.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      if (event.isComposing || composing) return;
      event.stopPropagation();
      close();
    });
    pane.addEventListener('cancel', event => { event.preventDefault(); if (!composing) close(); });
    pane.addEventListener('pointerdown', event => { backdropPressed = outside(event); });
    pane.addEventListener('click', event => {
      if (backdropPressed && outside(event)) close();
      backdropPressed = false;
    });
    root.getElementById('knowledge-focus-close')?.addEventListener('click', () => {
      setNodeDetailsVisible(false, root);
      root.getElementById('knowledge-graph-canvas')?.focus({ preventScroll: true });
    }, { capture: true });
    boundPanes.add(pane);
  }
}

export function setNodeDetailsVisible(visible: boolean, root: Document = document): void {
  const pane = root.querySelector<HTMLDialogElement>('dialog#knowledge-note-pane');
  if (!pane) return;
  bindNotePane(root, pane);
  visible = visible && !pane.parentElement?.closest('[hidden]');
  root.getElementById('settings-knowledge-card')?.setAttribute('data-note-open', String(visible));
  if (visible) {
    pane.hidden = false;
    if (!pane.open) pane.showModal();
  } else {
    if (pane.open) pane.close();
    pane.hidden = true;
    delete pane.dataset.nodeId;
  }
}

function prepareNotePane(root: Document, nodeId: string, initial: boolean): void {
  const pane = root.querySelector<HTMLDialogElement>('dialog#knowledge-note-pane');
  if (!pane) return;
  bindNotePane(root, pane);
  if (initial || pane.dataset.nodeId !== nodeId) {
    pane.dataset.nodeId = nodeId;
    pane.scrollTop = 0;
    const disclosure = pane.querySelector<HTMLDetailsElement>('.knowledge-evidence-disclosure');
    if (disclosure) disclosure.open = false;
    if (!pane.hidden && pane.open) root.getElementById('knowledge-focus-close')?.focus({ preventScroll: true });
  }
}

export function nodeKind(node: KnowledgeNode): string {
  if (node.category === 'collection') return '지식 모음';
  if (/대화 상대|인물|화자|person/.test(node.category)) return '대화 상대';
  if (/대화방|room/.test(node.category)) return '채팅방';
  if (/주제|topic/.test(node.category)) return '대화 주제';
  return '저장된 기억';
}

export function nodeSummary(graph: KnowledgeGraph, node: KnowledgeNode): string {
  if (node.category === 'collection') {
    const children = new Set(graph.edges.filter(edge => edge.source === node.id && edge.purpose === 'navigation').map(edge => edge.target));
    return text(node.description) || `${node.label}의 출처 맥락입니다. 저장된 입구 ${children.size}개를 따라 탐색할 수 있습니다.`;
  }
  return text(node.description) || '저장된 항목입니다. 원문을 확인하고 있습니다.';
}

function renderBody(container: HTMLElement, body: string, graph: KnowledgeGraph, allowLabelLinks = true): void {
  container.replaceChildren();
  // Parse only saved node links and explicit HTTP(S) URLs. Everything else is
  // literal text, including HTML and unsupported Markdown schemes.
  const pattern = /\[\[([^\]\n]+)\]\]|\[([^\]\n]+)\]\((https?:\/\/[^\s<>"']+)\)|(https?:\/\/[^\s<>\[\]"'`)]+)/g;
  let offset = 0;
  for (const match of body.matchAll(pattern)) {
    const at = match.index ?? 0;
    container.append(document.createTextNode(body.slice(offset, at)));
    if (match[1]) {
      const [target, alias] = match[1].split('|', 2);
      const identities = graph.nodes.filter(item => item.id === target || item.id === `osk:${target}` || item.oskId === target);
      const labels = graph.nodes.filter(item => item.label === target);
      const node = identities.length === 1 ? identities[0]
        : allowLabelLinks && identities.length === 0 && labels.length === 1 ? labels[0] : null;
      if (node) {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'knowledge-note-link'; button.dataset.knowledgeNode = node.id;
        button.textContent = alias || node.label; container.append(button);
      } else container.append(document.createTextNode(match[0]));
    } else {
      const link = document.createElement('a'); link.href = match[3] || match[4];
      link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = match[2] || match[4]; container.append(link);
    }
    offset = at + match[0].length;
  }
  container.append(document.createTextNode(body.slice(offset)));
}

export function renderNodeDetails(graph: KnowledgeGraph, node: KnowledgeNode, payload: Record<string, unknown> | null = null, root: Document = document): void {
  const details = record(payload?.details);
  const selected = details && details.node_id === node.id
    && (details.basis !== 'source_record' || details.version === node.sourceVersion) ? details : null;
  const summary = root.getElementById('knowledge-node-summary');
  if (summary) summary.textContent = text(selected?.summary) || nodeSummary(graph, node);
  const body = root.getElementById('knowledge-node-body');
  const copy = (selected?.basis === 'note' || selected?.basis === 'source_record') && node.category !== 'collection' ? text(selected.body, 12000) : '';
  // Source-specific projection is already bound to retained bytes by the
  // backend. Numeric shape alone cannot distinguish chrome from source data.
  const candidate = readingCandidate(selected, copy);
  const reading = candidate?.reading ?? copy;
  if (body) { renderBody(body, reading, graph, selected?.basis !== 'source_record'); body.hidden = !reading; }
  root.querySelectorAll('.knowledge-separated-tail').forEach(item => item.remove());
  if (body && candidate) {
    const note = root.createElement('details'); note.className = 'knowledge-separated-tail knowledge-reading-tail';
    note.append(element('summary', '', '원문의 숫자 부분 보기 · 의미 미확인'));
    note.append(element('p', '', '수집 형식상 별도 숫자 묶음일 수 있어 접었습니다. 본문인지 화면 수치인지는 미확인입니다. 원문은 보존됩니다.'));
    note.append(element('pre', 'knowledge-separated-tail-text', candidate.tail));
    body.after(note);
  }
  root.querySelectorAll('.knowledge-resolution-note').forEach(note => note.remove());
  const resolutions = node.resolutionContexts?.length
    ? node.resolutionContexts.map(context => ({ resolution: context, context }))
    : node.resolution ? [{ resolution: node.resolution, context: null }] : [];
  let resolutionAnchor = body;
  for (const { resolution, context } of resolutions) {
    if (!resolutionAnchor) break;
    const note = root.createElement('section'); note.className = 'knowledge-resolution-note';
    note.dataset.resolutionId = resolution.id; note.dataset.project = resolution.project;
    const title = root.createElement('h3'); title.textContent = context
      ? `${text(resolution.project, 128)} · 검토 결정 · 자료 ${resolution.members.length}개`
      : '검토로 묶은 자료 ' + resolution.members.length + '개';
    const reason = root.createElement('p'); reason.textContent = text(resolution.reason, 1024);
    const basis = root.createElement('p'); basis.textContent = context
      ? '명시적인 사용자 검토 결정입니다. 각 원문과 관계는 보존됩니다.'
      : '현재 화면의 검토 결정입니다. 각 원문과 관계는 보존됩니다.';
    note.append(title, reason, basis);
    if (context) {
      const display = root.createElement('p'); display.textContent = context.consolidated
        ? '현재 통합 화면에서 이 프로젝트의 검토 결정으로 묶어 표시합니다.'
        : '현재 통합 화면에서는 이 프로젝트의 묶음을 적용하지 않고 원문을 유지합니다.';
      const version = root.createElement('p'); version.textContent = context.displayMatches
        ? '현재 표시 버전이 이 결정의 검토 버전과 일치합니다.'
        : '현재 표시 버전이 이 결정의 검토 버전과 다릅니다. 현재 본문을 검토된 내용으로 간주할 수 없습니다.';
      note.append(display, version);
    }
    const sources = root.createElement('ul');
    for (const member of resolution.members) {
      const item = root.createElement('li'); item.textContent = text(member.label, 1024) || '제목 없는 자료'; sources.append(item);
    }
    note.append(sources); resolutionAnchor.after(note); resolutionAnchor = note;
  }
  if (summary) summary.hidden = Boolean(body && reading);
  const kind = root.getElementById('knowledge-node-kind'); if (kind) kind.textContent = nodeKind(node);
  const facts = root.getElementById('knowledge-node-facts'); facts?.replaceChildren();
  const values = selected?.key_facts;
  const factKeys = new Set<string>();
  if (Array.isArray(values)) for (const value of values) {
    const fact = text(value, 600);
    if (!fact) continue;
    const key = fact.normalize('NFC').replace(/\s+/g, ' ');
    if (factKeys.has(key)) continue;
    factKeys.add(key);
    facts?.append(element('li', '', fact));
    if (factKeys.size === 6) break;
  }
  if (facts) facts.hidden = facts.children.length === 0;
  const evidence = root.getElementById('knowledge-node-evidence'); evidence?.replaceChildren();
  const seen = new Set<string>();
  const identity = node.id.match(/^(person|chat):kakao:([0-9a-f]{64}):(actor|room):([0-9]+)$/);
  const rawSources = selected && Array.isArray(payload?.sources) ? payload.sources : [];
  for (const raw of rawSources.slice(0, 12)) {
    const source = record(raw); if (!source) continue;
    const id = text(source.source_id, 200), quote = text(source.content, 1000);
    const sourceId = id.match(/^kakao:([0-9a-f]{64}):room:([0-9]+):log:([0-9]+)$/);
    if (!quote || !sourceId || source.source_kind !== 'local_db_snapshot' || seen.has(id)
      || selected?.scope_room_id && String(selected.scope_room_id) !== sourceId[2]
      || identity && (identity[2] !== sourceId[1] || identity[1] === 'chat' && identity[4] !== sourceId[2]
        || identity[1] === 'person' && identity[4] !== String(source.author_id))) continue;
    seen.add(id);
    const entry = element('blockquote', 'knowledge-source');
    entry.dataset.sourceId = id;
    const author = source.source_role === 'outgoing_unclassified' ? '발신자 구분 미확인' : source.source_role === 'system_history' ? '시스템 기록' : text(source.sender, 128) || '이름 미확인';
    entry.append(element('p', 'knowledge-source-text', quote));
    const room = text(source.room_title, 128);
    const citation = element('cite', '', `${room ? room + ' · ' : ''}${author} · ${date(source.date)}${source.truncated === true ? ' · 일부 발췌' : ''}`);
    const titleBasis: Record<string, string> = {catalog_history:'저장된 과거 이름',activity_alias:'원문 참여자로 붙인 표시 이름',
      topic_alias:'반복된 주제로 붙인 표시 이름',unresolved:'보관 기록의 식별 이름',observed_title:'원본에서 확인한 방 제목',
      observed_display:'원본에서 확인한 표시 이름',snapshot:'수집된 이름',catalog:'등록한 표시 이름',saved_graph:'저장된 기억의 이름'};
    citation.title = titleBasis[text(source.room_title_source)] ?? '';
    entry.append(citation);
    evidence?.append(entry);
    if (seen.size === 6) break;
  }
  const heading = root.getElementById('knowledge-evidence-heading');
  const disclosure = root.querySelector<HTMLElement>('.knowledge-evidence-disclosure');
  if (disclosure) disclosure.hidden = Boolean(copy && seen.size === 0);
  if (heading) {
    heading.hidden = false;
    heading.textContent = seen.size ? `원문 근거 · ${seen.size}건` : '원문 근거';
  }
  const basis = root.getElementById('knowledge-node-basis');
  if (selected?.basis === 'source_record' && selected.version === node.sourceVersion && /^[0-9a-f]{64}$/.test(text(selected.raw_sha256))) {
    const receipt = element('div', 'knowledge-source');
    receipt.append(element('p', '', `수집 기록 · ${text(selected.platform, 32)} · ${date(selected.collected_at)}${selected.truncated === true ? ' · 일부 발췌' : ''}`));
    receipt.append(element('p', '', `기록 버전: ${text(selected.version, 256)}`));
    receipt.append(element('p', '', `보존 기록 SHA-256: ${text(selected.raw_sha256, 64)}`));
    const capture = record(selected.capture);
    if (capture?.status === 'manifest_verified') {
      receipt.append(element('p', '', `보존 파일: ${text(capture.byte_scope, 160)} · ${text(capture.json_pointer, 256)}`));
      receipt.append(element('p', '', `보존 파일 SHA-256: ${text(capture.source_sha256, 64)}`));
    }
    const rawUrl = text(selected.source_url, 4096);
    try {
      const url = new URL(rawUrl);
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) {
        const link = document.createElement('a'); link.href = url.href; link.textContent = '출처 열기'; link.target = '_blank'; link.rel = 'noopener noreferrer'; receipt.append(link);
      }
    } catch { /* Records without a public URL retain local evidence. */ }
    evidence?.append(receipt);
    const tailNote = separatedNumericTail(selected, root);
    if (tailNote) evidence?.append(tailNote);
    if (disclosure) disclosure.hidden = false;
    if (heading) heading.textContent = '보존 근거';
    if (basis) basis.textContent = '수집된 기록과 해시를 확인했습니다. 내용의 사실 여부를 검증한 것은 아닙니다.';
    prepareNotePane(root, node.id, payload === null);
    return;
  }
  if (basis) basis.textContent = seen.size ? `원문 ${seen.size}건 · 최근 기록의 일부입니다.`
    : node.category === 'collection' ? '그래프에 실제 등록된 구조를 기준으로 설명합니다.'
    : selected?.basis === 'note' ? '저장된 노트의 설명입니다. 대화 원문으로 확인된 사실과 구분합니다.'
    : payload ? '원문을 확인하지 못했습니다. 저장된 설명만 표시합니다.' : '저장된 설명 · 원문을 확인하고 있습니다.';
  prepareNotePane(root, node.id, payload === null);
}
