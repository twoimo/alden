import type { fetchSettingsAction } from '../runtime';
import type { KnowledgeGraph } from './graph-model';
import { terminalSummary, type TerminalSummary } from './terminal-model';

export type MapPoint = { id: string; x: number; y: number; z: number };
const panels = [
  ['ingest', 'INGEST', '최근 수집'], ['linked', 'LINKED', '선택한 연결'],
  ['trend', 'f(x)', '수정일 분포 · UTC'], ['sectors', 'SECTORS', '지식 유형'],
  ['chord', 'CHORD', '유형 간 연결'], ['tail', 'TAIL-F', '처리 이력'],
] as const;
export function terminalMarkup(): string {
  return `<section id="knowledge-terminal" class="knowledge-terminal" aria-label="실시간 지식 터미널">${panels.map(([id, name, label]) =>
    `<article class="terminal-panel terminal-${id}" aria-labelledby="terminal-${id}-heading"><header><h2 id="terminal-${id}-heading">${name}</h2><span>${label}</span>${id === 'tail' ? '<button id="terminal-follow" type="button" aria-pressed="true" title="새 기록 자동 추적">추적 켜짐</button>' : ''}</header><div id="terminal-${id}" class="terminal-content"></div></article>`).join('')}</section>`;
}
export function observationMarkup(): string {
  return `<aside class="knowledge-observations" aria-label="현재 지도의 보조 관측"><figure><figcaption><strong>SECTOR MAP</strong><span>정면 · XY</span></figcaption><canvas id="terminal-projection-xy" aria-hidden="true"></canvas></figure><figure><figcaption><strong>ORBIT MAP</strong><span>측면 · XZ</span></figcaption><canvas id="terminal-projection-xz" aria-hidden="true"></canvas></figure><div class="terminal-map-readout"><span>현재 표시 범위</span><strong id="terminal-map-count">불러오는 중</strong><p id="terminal-map-selection">지식을 선택해 연결을 확인하세요.</p><small>동일한 배치의 투영 · 연결 최대 300개</small></div></aside>`;
}

type Row = Record<string, unknown>;
const stageNames: Record<string, string> = { discovered: '발견', parsed: '내용 읽음', validated: '형식 확인', stored: '저장', indexed: '검색 반영', failed: '실패', paused: '중지' };
const colors = ['#8edcf1', '#c8b5f2', '#e6c489', '#91d1bb', '#e5aaca', '#9bbcea'];
const number = (value: number) => value.toLocaleString('ko-KR');
const typeName = (value: string): string => ({ video: '영상', document: '문서', source: '출처', source_claim: '원문 주장', topic: '주제', concept: '개념', index: '색인', collection: '지식 모음', person: '인물', claim: '주장', code: '코드' })[value] ?? value;

/** Event-driven DOM/2D views. No timers, renderer, physics or independent subscription. */
export class KnowledgeTerminal {
  private readonly controls = new AbortController();
  private readonly resize: ResizeObserver | null;
  private graph: KnowledgeGraph = { nodes: [], edges: [] };
  private summary = terminalSummary(this.graph, null);
  private points: MapPoint[] = [];
  private focus: string | null = null;
  private active = false;
  private epoch = 0;
  private scope = '';
  private busy = false;
  private follow = true;
  private history: Row[] = [];
  private historyStream: string | null = null;
  private pending: Row[] | null = null;
  private historyState = '수집 이력을 불러옵니다.';

  constructor(private readonly root: Document, private readonly load: typeof fetchSettingsAction,
    private readonly select: (id: string) => void) {
    const signal = this.controls.signal;
    root.getElementById('knowledge-terminal')?.addEventListener('click', event => {
      const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>('button[data-terminal-node]') : null;
      if (button?.dataset.terminalNode && this.summary.nodes.some(node => node.id === button.dataset.terminalNode)) this.select(button.dataset.terminalNode);
    }, { signal });
    root.getElementById('terminal-follow')?.addEventListener('click', () => {
      this.follow = !this.follow;
      if (this.follow && this.pending) { this.history = this.pending; this.pending = null; this.renderHistory(); }
      this.followLabel();
    }, { signal });
    root.getElementById('knowledge-terminal-toggle')?.addEventListener('click', () => {
      const dock = root.getElementById('knowledge-terminal'); if (!dock) return;
      dock.hidden = !dock.hidden;
      root.getElementById('knowledge-terminal-toggle')?.setAttribute('aria-expanded', String(!dock.hidden));
    }, { signal });
    this.resize = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => { if (this.active) this.drawMaps(); }) : null;
    for (const id of ['terminal-projection-xy', 'terminal-projection-xz']) {
      const canvas = root.getElementById(id); if (canvas) this.resize?.observe(canvas);
    }
    this.render(); this.renderHistory();
  }
  visible(active: boolean): void { this.active = active; this.epoch++; if (active) this.drawMaps(); }
  reset(): void {
    this.epoch++; this.scope = ''; this.history = []; this.pending = null; this.historyStream = null;
    this.historyState = '수집 이력을 불러옵니다.'; this.renderHistory(); this.followLabel();
  }
  dispose(): void { this.visible(false); this.controls.abort(); this.resize?.disconnect(); }
  update(graph: KnowledgeGraph, focus: string | null, points: MapPoint[] = []): void {
    this.graph = graph; this.focus = focus; this.points = points;
    this.summary = terminalSummary(graph, focus);
    this.render();
  }
  async refreshHistory(query: Row, collection: boolean): Promise<void> {
    if (!this.active) return;
    const options: Row = { limit: 80 };
    for (const key of ['projects', 'target_id', 'platform', 'search', 'since', 'until']) if (query[key]) options[key] = query[key];
    const key = JSON.stringify([options, query.node_type, query.relation]);
    if (this.scope !== key) { this.reset(); this.scope = key; }
    if (!collection) { this.history = []; this.historyState = '대화 기억에는 수집 처리 이력이 없습니다.'; this.renderHistory(); return; }
    if (this.busy) return;
    this.busy = true; const epoch = this.epoch;
    try {
      const result = await this.load('collection-history', { query: JSON.stringify(options) });
      if (!this.active || epoch !== this.epoch) return;
      if (result?.ok !== true || !Array.isArray(result.items)) {
        this.historyState = '수집 이력을 확인할 수 없습니다.'; this.history = []; this.renderHistory(); return;
      }
      const stream = typeof result.stream_id === 'string' ? result.stream_id : null;
      if (result.reset === true || stream !== null && this.historyStream !== null && stream !== this.historyStream) {
        this.history = []; this.pending = null;
      }
      this.historyStream = stream;
      const known = new Set<string>();
      const ids = new Set(this.summary.nodes.map(node => node.id));
      const items = result.items.filter((raw): raw is Row => !!raw && typeof raw === 'object' && !Array.isArray(raw))
        .filter(row => {
          if (!Number.isSafeInteger(row.sequence) || Number(row.sequence) <= 0 || known.has(String(row.sequence))) return false;
          if (!stageNames[String(row.stage)] || typeof row.document_label !== 'string') return false;
          if ((query.node_type || query.relation) && !ids.has(String(row.document_id))) return false;
          known.add(String(row.sequence)); return true;
        }).sort((a, b) => Number(b.sequence) - Number(a.sequence)).slice(0, 32);
      this.historyState = '수집 범위 · 최근 기록';
      if (this.follow || !this.history.length) { this.history = items; this.pending = null; this.renderHistory(); }
      else { this.pending = items; }
      this.followLabel();
    } catch {
      if (this.active && epoch === this.epoch) { this.historyState = '수집 이력을 확인할 수 없습니다.'; this.history = []; this.renderHistory(); }
    } finally { this.busy = false; }
  }
  synced(): void {
    const time = this.root.getElementById('terminal-synced');
    if (time) time.textContent = '확인 ' + new Date().toLocaleTimeString('ko-KR', { hour12: false });
  }
  projection(points: MapPoint[]): void { this.points = points; if (this.active) this.drawMaps(); }
  private make<K extends keyof HTMLElementTagNameMap>(tag: K, text = '', className = ''): HTMLElementTagNameMap[K] {
    const node = this.root.createElement(tag); node.textContent = text; node.className = className; return node;
  }
  private host(id: string) { return this.root.getElementById(`terminal-${id}`); }
  private set(id: string, content: Node[]): void { this.host(id)?.replaceChildren(...content); }
  private empty(text: string) { return this.make('p', text, 'terminal-empty'); }
  private row(label: string, value: string, id?: string): HTMLElement {
    const row = this.make(id ? 'button' : 'div', '', 'terminal-row');
    if (row instanceof HTMLButtonElement) { row.type = 'button'; row.dataset.terminalNode = id; }
    row.title = label;
    row.append(this.make('span', label), this.make('small', value)); return row;
  }
  private render(): void {
    const data = this.summary, selected = data.nodes.find(node => node.id === this.focus);
    this.set('linked', selected ? [this.make('p', selected.label, 'terminal-selection'),
      ...data.links.map(link => this.row(link.node.label, `${link.direction === 'out' ? '→' : '←'} ${link.edge.relation || '관계'}`, link.node.id)),
      ...(!data.links.length ? [this.empty('표시 범위에 연결이 없습니다.')] : [])] : [this.empty('지도에서 지식을 선택하세요.')]);
    this.set('sectors', data.sectors.length ? data.sectors.slice(0, 8).map((sector, i) => {
      const row = this.row(typeName(sector.name), number(sector.count)); row.style.setProperty('--sector-color', colors[i % colors.length]);
      row.style.setProperty('--sector-share', `${sector.count / data.nodes.length * 100}%`); row.classList.add('terminal-sector'); return row;
    }) : [this.empty('표시할 지식이 없습니다.')]);
    this.renderTrend(data); this.renderRoutes(data);
    const count = this.root.getElementById('terminal-map-count');
    if (count) count.textContent = `${number(data.nodes.length)} 지식 / ${number(data.edges.length)} 연결`;
    const selection = this.root.getElementById('terminal-map-selection');
    if (selection) selection.textContent = selected ? selected.label : '지식을 선택해 연결을 확인하세요.';
    if (this.active) this.drawMaps();
  }
  private renderTrend(data: TerminalSummary): void {
    const days = data.days.slice(-14), peak = Math.max(1, ...days.map(day => day.count));
    const chart = this.make('div', '', 'terminal-bars'); chart.setAttribute('role', 'img'); chart.setAttribute('aria-label', '표시한 지식의 최근 14개 수정 날짜별 개수. 빈 날짜는 포함하지 않습니다.');
    for (const day of days) {
      const bar = this.make('span'); bar.style.height = `${Math.max(2, day.count / peak * 100)}%`;
      bar.title = `${day.day} UTC · ${number(day.count)}개`; chart.append(bar);
    }
    this.set('trend', days.length ? [chart, this.row(days[0].day.slice(5), days.at(-1)!.day.slice(5)),
      this.make('p', `표시 범위 · 날짜 미상 ${number(data.undated)}개`, 'terminal-footnote')]
      : [this.empty('확인된 수정 날짜가 없습니다.')]);
  }
  private renderRoutes(data: TerminalSummary): void {
    const list = data.routes.slice(0, 6);
    if (!list.length) { this.set('chord', [this.empty('표시 범위에 영역 간 연결이 없습니다.')]); return; }
    const svg = this.root.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 140 90'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('terminal-chord');
    const sectors = [...new Set(list.flatMap(route => [route.source, route.target]))];
    const point = (index: number) => { const angle = index / sectors.length * Math.PI * 2 - Math.PI / 2; return [70 + Math.cos(angle) * 37, 45 + Math.sin(angle) * 37]; };
    for (const route of list) {
      const a = point(sectors.indexOf(route.source)), b = point(sectors.indexOf(route.target));
      const path = this.root.createElementNS(svg.namespaceURI, 'path');
      path.setAttribute('d', `M${a.join(',')} Q70,45 ${b.join(',')}`); path.setAttribute('stroke', colors[sectors.indexOf(route.source) % colors.length]);
      path.setAttribute('stroke-width', String(Math.min(3, .5 + Math.log1p(route.count) / 2))); path.setAttribute('fill', 'none'); svg.append(path);
    }
    sectors.forEach((_, i) => { const [x, y] = point(i), dot = this.root.createElementNS(svg.namespaceURI, 'circle'); dot.setAttribute('cx', String(x)); dot.setAttribute('cy', String(y)); dot.setAttribute('r', '3'); dot.setAttribute('fill', colors[i % colors.length]); svg.append(dot); });
    this.set('chord', [svg, ...list.slice(0, 3).map(route => this.row(`${typeName(route.source)} → ${typeName(route.target)}`, number(route.count)))]);
  }
  private renderHistory(): void {
    const latest = new Map<string, Row>();
    for (const row of this.history) { const id = typeof row.document_id === 'string' ? row.document_id : ''; if (id && !latest.has(id)) latest.set(id, row); }
    const display = (row: Row, ingestion: boolean) => {
      const stage = stageNames[String(row.stage)], detail = row.details as Row | undefined;
      const label = row.stage === 'stored' && detail?.change === 'unchanged' ? '변경 없음' : stage;
      const at = Number(row.at), time = Number.isFinite(at) && at > 0 ? new Date(at * 1000).toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit' }) : '시각 미상';
      const line = this.row(String(row.document_label), ingestion ? label : time);
      const badge = this.make('b', label, 'terminal-stage'); badge.dataset.stage = String(row.stage); line.prepend(badge); return line;
    };
    this.set('ingest', this.history.length ? [this.make('p', '수집 범위 · 최근 자료', 'terminal-footnote'), ...[...latest.values()].slice(0, 8).map(row => display(row, true))]
      : [this.empty(this.historyState)]);
    this.set('tail', this.history.length ? [this.make('p', this.historyState, 'terminal-footnote'), ...this.history.slice(0, 12).map(row => display(row, false))] : [this.empty(this.historyState)]);
  }
  private followLabel(): void {
    const button = this.root.getElementById('terminal-follow'); if (!button) return;
    button.setAttribute('aria-pressed', String(this.follow));
    const unseen = this.pending?.filter(row => Number(row.sequence) > Number(this.history[0]?.sequence ?? 0)).length ?? 0;
    button.textContent = this.follow ? '추적 켜짐' : unseen ? `새 기록 ${unseen}` : '추적 꺼짐';
  }
  private drawMaps(): void {
    const known = new Map(this.points.filter(point => [point.x, point.y, point.z].every(Number.isFinite)).map(point => [point.id, point]));
    for (const axis of ['xy', 'xz'] as const) {
      const canvas = this.root.getElementById(`terminal-projection-${axis}`) as HTMLCanvasElement | null;
      if (!canvas) continue;
      const width = canvas.clientWidth, height = canvas.clientHeight;
      if (width <= 0 || height <= 0) continue;
      const ratio = Math.min(this.root.defaultView?.devicePixelRatio ?? 1, 2);
      canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
      const context = canvas.getContext('2d'); if (!context) continue;
      context.scale(ratio, ratio);
      const points = [...known.values()], radius = Math.max(.01, ...points.map(point => Math.hypot(point.x, point.y, point.z)));
      const scale = Math.min(width, height) * .43 / radius;
      const pos = (point: MapPoint) => [width / 2 + point.x * scale, height / 2 - (axis === 'xy' ? point.y : point.z) * scale];
      context.strokeStyle = '#78b4d416'; context.lineWidth = .55;
      context.beginPath();
      for (const edge of this.summary.edges.slice(0, 300)) {
        const a = known.get(edge.source), b = known.get(edge.target); if (!a || !b) continue;
        const [ax, ay] = pos(a), [bx, by] = pos(b); context.moveTo(ax, ay); context.lineTo(bx, by);
      }
      context.stroke();
      for (const point of points) {
        const [x, y] = pos(point), selected = point.id === this.focus;
        context.fillStyle = selected ? '#f1d296' : '#a5cfe0b0';
        context.beginPath(); context.arc(x, y, selected ? 3 : .7, 0, Math.PI * 2); context.fill();
      }
    }
  }
}
