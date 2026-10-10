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

const boundPanes = new WeakSet<HTMLElement>();

function prepareNotePane(root: Document, nodeId: string, initial: boolean): void {
  const pane = root.getElementById('knowledge-note-pane');
  if (!pane) return;
  if (!boundPanes.has(pane)) {
    // Use the controller's existing close action, including its retrieval fence.
    pane.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.isComposing) return;
      event.preventDefault();
      event.stopPropagation();
      root.getElementById('knowledge-focus-close')?.click();
    });
    root.getElementById('knowledge-focus-close')?.addEventListener('click', () => {
      delete pane.dataset.nodeId;
      root.getElementById('knowledge-graph-canvas')?.focus({ preventScroll: true });
    }, { capture: true });
    boundPanes.add(pane);
  }
  if (initial || pane.dataset.nodeId !== nodeId) {
    pane.dataset.nodeId = nodeId;
    pane.scrollTop = 0;
    const disclosure = pane.querySelector<HTMLDetailsElement>('.knowledge-evidence-disclosure');
    if (disclosure) disclosure.open = false;
    if (!pane.hidden) pane.focus({ preventScroll: true });
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

function renderBody(container: HTMLElement, body: string, graph: KnowledgeGraph): void {
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
      const node = identities.length === 1 ? identities[0] : identities.length === 0 && labels.length === 1 ? labels[0] : null;
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
  if (body) { renderBody(body, copy, graph); body.hidden = !copy; }
  if (summary) summary.hidden = Boolean(body && copy);
  const kind = root.getElementById('knowledge-node-kind'); if (kind) kind.textContent = nodeKind(node);
  const facts = root.getElementById('knowledge-node-facts'); facts?.replaceChildren();
  const values = selected?.key_facts;
  if (Array.isArray(values)) for (const value of [...new Set(values.filter(value => typeof value === 'string'))].slice(0, 6)) {
    if (text(value, 600)) facts?.append(element('li', '', text(value, 600)));
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
    // A trace belongs to this exact selected source version and target only.
    // It reports stored proof; it never claims the GPU rendered this node or
    // that a local model ran because a derived vector was found.
    const trace = record(payload?.pipeline_trace);
    const proof = trace?.ok === true && trace.state === 'available' && trace.document_id === node.id
      && trace.version === node.sourceVersion && trace.target_id === node.sourceTarget ? trace : null;
    if (proof) {
      const stages = record(proof.stages), source = record(proof.source), fts = record(proof.fts), dense = record(proof.dense);
      const stored = stages?.stored === true && source?.state === 'hash_verified';
      const indexed = stored && stages?.indexed === true && fts?.state === 'verified';
      const vector = dense?.state === 'stored_vector_binding_verified';
      const status = stored ? '저장·원본 해시 확인' : '저장 상태 재확인 필요';
      receipt.append(element('p', '', `처리 경로 · ${status} · FTS ${indexed ? '확인' : '미확인'} · 로컬 Dense ${vector ? '저장 바인딩 확인' : '미확인/대기'}`));
      receipt.append(element('p', '', '검증 범위: 저장 기록과 파생 색인 · 실제 모델 로딩 및 화면 렌더링은 별도 확인'));
    }
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
