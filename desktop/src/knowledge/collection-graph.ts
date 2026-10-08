import type { fetchSettingsAction } from '../runtime';
import type { KnowledgeFocusEvent, KnowledgeHologram } from './hologram';
import type { KnowledgeNode } from './graph-model';
import { parseKnowledgeGraph, MAX_FOCUS_HOPS, NAVIGATION_HISTORY_LIMIT } from './graph-model';
import { knowledgeSignature } from './changes';
import { KnowledgeRefresh } from './refresh';
import { unifiedGraph } from './unified-graph';
import { CollectionActivity } from './collection-activity';

type GraphPort = Pick<KnowledgeHologram, 'replaceGraph' | 'currentGraph' | 'currentView' | 'navigationTargets' | 'clickNode' | 'showNodeActivity' | 'clearNodeActivity'>;
type Navigation = { offset: number; focusId: string | null; hops: number };
type Viewpoint = { camera: number[]; lookAt: number[] };
export type GraphAction = 'expand' | 'back' | 'reset' | 'clear';

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((entry): entry is Record<string, unknown> => !!entry && typeof entry === 'object' && !Array.isArray(entry)) : [];
}

/** One bounded scene per request. Scope changes and hiding fence all old reads;
 * navigation history restores the query and camera, then reads current data. */
export class CollectionGraphController {
  private readonly controls = new AbortController();
  private readonly refresh: KnowledgeRefresh;
  private readonly journal: CollectionActivity;
  private readSequence = 0;
  private lastRead: Record<string, unknown> | null = null;
  private active = false;
  private disposed = false;
  private epoch = 0;
  private initialization: Promise<void> | null = null;
  private userSelected = false;
  private state: Navigation = { offset: 0, focusId: null, hops: 0 };
  private history: Array<{ state: Navigation; camera: Viewpoint }> = [];
  private next: number | null = null;
  private navigationChanged = false;
  private pendingCamera: Viewpoint | undefined;
  private applying = false;
  private signature = '';
  private listSignature = '';
  private debounce: ReturnType<typeof setTimeout> | null = null;
  private totals = { nodes: 0, edges: 0 };
  private readonly source: HTMLSelectElement;

  constructor(private readonly load: typeof fetchSettingsAction, private readonly graph: GraphPort,
    private readonly root: Document = document, revision?: () => Promise<string | null>) {
    this.source = root.getElementById('knowledge-project') as HTMLSelectElement;
    this.refresh = new KnowledgeRefresh(() => this.read(), payload => this.apply(payload), 15000,
      () => this.collection ? Promise.resolve(null) : revision?.() ?? Promise.resolve(null));
    this.journal = new CollectionActivity(options => {
      const query = this.query();
      return this.load('collection-graph', { query: JSON.stringify({ ...options,
        projects: query.projects, target_id: query.target_id, platform: query.platform }) });
    }, () => this.refresh.invalidate(), event => this.graph.showNodeActivity(event), () => this.graph.clearNodeActivity());
    const signal = this.controls.signal;
    this.source.addEventListener('change', () => {
      this.userSelected = true;
      const target = this.root.getElementById('knowledge-target') as HTMLSelectElement;
      target.value = '';
      for (const id of ['knowledge-type', 'knowledge-relation']) (this.root.getElementById(id) as HTMLSelectElement).value = '';
      this.changeScope();
    }, { signal });
    root.getElementById('knowledge-filter-form')?.addEventListener('submit', event => { event.preventDefault(); this.changeScope(); }, { signal });
    for (const id of ['knowledge-platform', 'knowledge-target', 'knowledge-type', 'knowledge-relation', 'knowledge-since', 'knowledge-until']) {
      root.getElementById(id)?.addEventListener('change', () => this.changeScope(), { signal });
    }
    root.getElementById('knowledge-search')?.addEventListener('input', () => {
      if (this.debounce !== null) clearTimeout(this.debounce);
      // Fence the old query immediately, before the bounded debounce elapses.
      this.epoch++; this.refresh.stop(); this.journal.reset();
      this.debounce = setTimeout(() => { this.debounce = null; this.changeScope(); }, 250);
    }, { signal });
    root.getElementById('knowledge-result-list')?.addEventListener('click', event => {
      const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>('button[data-graph-node]') : null;
      if (button?.dataset.graphNode) this.graph.clickNode(button.dataset.graphNode);
    }, { signal });
    this.setFilterAvailability();
  }

  get collection(): boolean { return this.source.value !== 'legacy'; }
  get canGoBack(): boolean { return this.history.length > 0; }
  get offset(): number { return this.state.offset; }
  get hasMore(): boolean { return this.next !== null; }
  get readDiagnostics() { return this.lastRead && { ...this.lastRead, navigation: { ...this.state }, navigationPending: this.navigationChanged }; }
  get activityDiagnostics() { return this.journal.diagnostics(); }

  start(): void {
    if (this.active || this.disposed) return;
    this.active = true; this.epoch++;
    if (this.collection) this.journal.start();
    this.root.getElementById('knowledge-filter-form')?.setAttribute('aria-busy', 'true');
    void this.initialize().then(() => { if (this.active && !this.disposed) this.refresh.start(); });
  }

  stop(): void {
    this.active = false; this.epoch++; this.refresh.stop(); this.journal.stop();
    if (this.debounce !== null) clearTimeout(this.debounce);
    this.debounce = null;
    this.root.getElementById('knowledge-filter-form')?.removeAttribute('aria-busy');
  }

  dispose(): void { this.stop(); this.disposed = true; this.controls.abort(); }

  private initialize(): Promise<void> {
    if (this.initialization) return this.initialization;
    this.initialization = this.load('collection-projects').then(result => {
      const projects = records(result?.projects).filter(row => typeof row.project === 'string');
      if (this.disposed) return;
      this.options(this.source, [{ value: 'all', label: '전체 지식' }, { value: 'legacy', label: '기억 · 대화' },
        ...projects.map(row => ({ value: 'project:' + row.project, label: String(row.project) }))]);
      if (!this.userSelected) { this.source.value = 'all'; this.changeScope(); }
    }).catch(() => { /* Existing memory remains usable if the collection is absent. */ });
    return this.initialization;
  }

  private input(id: string): string {
    return (this.root.getElementById(id) as HTMLInputElement | HTMLSelectElement | null)?.value ?? '';
  }

  private query(): Record<string, unknown> {
    const options: Record<string, unknown> = { limit: this.state.hops > 0 ? 120 : 1984, overview: this.state.hops === 0, offset: this.state.offset, search: this.input('knowledge-search').trim() };
    if (this.source.value.startsWith('project:')) options.projects = [this.source.value.slice(8)];
    for (const [id, key] of [['knowledge-platform', 'platform'], ['knowledge-target', 'target_id'],
      ['knowledge-type', 'node_type'], ['knowledge-relation', 'relation']] as const) {
      const value = this.input(id); if (value) options[key] = value;
    }
    for (const [id, key] of [['knowledge-since', 'since'], ['knowledge-until', 'until']] as const) {
      const value = this.input(id);
      if (!value) continue;
      const date = new Date(value + 'T00:00:00');
      if (key === 'until') date.setDate(date.getDate() + 1);
      if (Number.isFinite(date.getTime())) options[key] = date.getTime() / 1000;
    }
    if (this.state.focusId && !this.state.focusId.startsWith('memory:') && this.state.hops > 0) { options.focus = this.state.focusId; options.hops = this.state.hops; }
    return options;
  }

  private changeScope(): void {
    if (this.disposed) return;
    this.epoch++; this.refresh.stop(); this.journal.reset(); this.history = [];
    this.state = { offset: 0, focusId: null, hops: 0 }; this.next = null;
    this.navigationChanged = true; this.pendingCamera = undefined; this.signature = '';
    this.applying = true;
    this.graph.replaceGraph({ nodes: [], edges: [] }, this.state);
    this.applying = false;
    this.setFilterAvailability(); this.updateTools(); this.renderList();
    this.setText('knowledge-summary', '불러오는 중'); this.setText('knowledge-scope', '');
    if (this.active) { if (this.collection) this.journal.start(); this.refresh.start(); }
  }

  private setFilterAvailability(): void {
    const key = this.root.querySelector<HTMLElement>('.knowledge-activity-key'); if (key) key.hidden = !this.collection;
    for (const id of ['knowledge-search', 'knowledge-platform', 'knowledge-target', 'knowledge-type', 'knowledge-relation', 'knowledge-since', 'knowledge-until']) {
      const input = this.root.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
      if (input) input.disabled = false;
    }
  }

  private async read(): Promise<Record<string, unknown> | null> {
    const epoch = this.epoch;
    if (!this.active || this.disposed) return null;
    const form = this.root.getElementById('knowledge-filter-form'); form?.setAttribute('aria-busy', 'true');
    try {
      const query = this.query();
      const includeMemory = this.source.value === 'legacy' || (this.source.value === 'all' && !query.focus && this.state.offset === 0);
      const reads = await Promise.allSettled([
        this.collection ? this.load('collection-graph', { query: JSON.stringify(query) }) : Promise.resolve(null),
        includeMemory ? this.load('knowledge-graph') : Promise.resolve(null),
      ]);
      const values = reads.map(read => read.status === 'fulfilled' ? read.value : { ok: false });
      const result = unifiedGraph(values[0], values[1], query);
      if (!this.active || this.disposed || epoch !== this.epoch) return null;
      this.lastRead = { query, epoch, nodes: Array.isArray(result.nodes) ? result.nodes.length : 0 };
      return result ?? { ok: false };
    } finally { if (epoch === this.epoch) form?.removeAttribute('aria-busy'); }
  }

  async focus(node: KnowledgeNode, legacy: () => Promise<Record<string, unknown> | null>): Promise<Record<string, unknown> | null> {
    const epoch = this.epoch;
    let result = node.sourceVersion ? await this.load('collection-graph', { query: JSON.stringify({ ...this.query(),
      focus: node.id, hops: 0, limit: 120, overview: false, details: true, expected_version: node.sourceVersion }) }) : await legacy();
    if (!this.active || epoch !== this.epoch || this.disposed) return { discarded: true };
    if (node.canonicalId && result?.details && typeof result.details === 'object') {
      const original = result.details as Record<string, unknown>;
      if (original.node_id === node.canonicalId) result = { ...result, details: { ...original, node_id: node.id } };
    }
    const detail = result?.details as Record<string, unknown> | undefined;
    if (this.collection && this.graph.currentView.focusId === node.id && result?.ok === true && detail?.node_id === node.id && detail.version === node.sourceVersion && detail.target_id === node.sourceTarget) {
      this.graph.showNodeActivity({ event_id: `read:${++this.readSequence}`, sequence: 0, document_id: node.id,
        version: node.sourceVersion!, target_id: node.sourceTarget!, run_id: 'visible-detail', origin: 'alden-detail',
        at: Date.now() / 1000, kind: 'read', success: true });
    }
    return result;
  }

  selected(event: KnowledgeFocusEvent): void {
    if (this.applying || !this.collection) return;
    if (this.state.focusId !== event.view.focusId || this.state.hops !== event.view.hops) {
      this.remember(event.previousCamera); this.state = { ...this.state, focusId: event.view.focusId, hops: event.view.hops };
    }
    this.updateTools(); this.describeScope(); this.renderList();
  }

  navigate(action: GraphAction): boolean {
    if (!this.collection || (action === 'expand' && this.state.focusId?.startsWith('memory:'))) return false;
    if (!this.active || this.disposed) return true;
    if (action === 'back') {
      const previous = this.history.pop(); if (!previous) return true;
      this.state = previous.state; this.pendingCamera = previous.camera;
    } else {
      const next = action === 'expand' ? this.state.focusId
        ? { ...this.state, hops: Math.min(MAX_FOCUS_HOPS, this.state.hops + 1) }
        : this.next !== null ? { offset: this.next, focusId: null, hops: 0 } : this.state
        : { offset: action === 'clear' ? this.state.offset : 0, focusId: null, hops: 0 };
      if (JSON.stringify(next) === JSON.stringify(this.state)) return true;
      this.remember(); this.state = next; this.pendingCamera = undefined;
    }
    this.navigationChanged = true; this.epoch++; this.refresh.stop(); this.refresh.start(); this.updateTools();
    this.root.getElementById('knowledge-filter-form')?.setAttribute('aria-busy', 'true');
    return true;
  }

  private remember(camera = this.graph.navigationTargets): void {
    this.history.push({ state: { ...this.state }, camera });
    if (this.history.length > NAVIGATION_HISTORY_LIMIT) this.history.shift();
  }

  private apply(payload: Record<string, unknown>): void {
    if (!this.active || this.disposed) return;
    if (payload.ok !== true) {
      this.setText('knowledge-sync', '자료를 불러오지 못했습니다.');
      if (!this.graph.currentView.nodes.length) this.setText('knowledge-summary', '자료를 불러오지 못했습니다.');
      return;
    }
    const next = parseKnowledgeGraph(payload), signature = knowledgeSignature(next);
    this.applying = true;
    if (signature !== this.signature || this.navigationChanged) {
      this.signature = signature;
      this.graph.replaceGraph(next, this.navigationChanged ? this.state : undefined, this.pendingCamera);
      this.state.focusId = this.graph.currentView.focusId; this.state.hops = this.graph.currentView.hops;
    }
    this.applying = false; this.navigationChanged = false; this.pendingCamera = undefined;
    this.next = typeof payload.next === 'number' ? payload.next : null;
    this.totals = { nodes: typeof payload.total_nodes === 'number' ? payload.total_nodes : next.nodes.length,
      edges: typeof payload.total_edges === 'number' ? payload.total_edges : next.edges.length };
    this.setText('knowledge-summary', `${this.graph.currentView.nodes.length.toLocaleString('ko-KR')}개 표시`);
    this.setText('knowledge-sync', payload.partial ? '일부 출처를 불러오지 못했습니다.' : payload.stale ? '자료 확인 필요' : this.collection ? '저장된 기록' : payload.stale ? '자료 확인 필요' : '저장된 기억');
    this.setText('knowledge-mode', this.collection ? '수집 기록' : payload.stale ? '자료 확인 필요' : '저장된 기억');
    this.updateFacets(payload); this.updateTools(); this.describeScope(); this.renderList();
    if (this.collection) this.journal.snapshot(payload.activity_checkpoint, this.graph.currentView.nodes);
  }

  private describeScope(): void {
    const view = this.graph.currentView;
    this.setText('knowledge-scope', this.collection
      ? `표시 ${view.nodes.length}개 · 연결 ${view.edges.length}개 / 허용한 필터 범위 ${this.totals.nodes.toLocaleString('ko-KR')}개 · 연결 ${this.totals.edges.toLocaleString('ko-KR')}개. 크기는 해당 범위의 이웃 수이며 신뢰도를 뜻하지 않습니다.`
      : '현재 표시한 기억과 저장된 관계입니다.');
  }

  private updateTools(): void {
    if (!this.collection) return;
    const back = this.root.getElementById('knowledge-back') as HTMLButtonElement;
    const overview = this.root.getElementById('knowledge-overview') as HTMLButtonElement;
    const expand = this.root.getElementById('knowledge-expand-hop') as HTMLButtonElement;
    back.disabled = !this.history.length;
    overview.disabled = this.state.offset === 0 && this.state.focusId === null;
    expand.disabled = this.state.focusId ? this.state.hops >= MAX_FOCUS_HOPS : this.next === null;
    expand.textContent = this.state.focusId ? '주변 연결 더 보기' : '다음 보기';
  }

  private updateFacets(payload: Record<string, unknown>): void {
    if (!this.collection) return;
    const facets = payload.facets as Record<string, unknown> | undefined;
    for (const [id, values, label] of [['knowledge-target', records(payload.targets).map(row => ({ value: String(row.id), label: String(row.label) })), '모든 대상'],
      ['knowledge-type', Array.isArray(facets?.node_types) ? facets.node_types.filter(value => typeof value === 'string').map(value => ({ value: String(value), label: String(value) })) : [], '모든 유형'],
      ['knowledge-relation', Array.isArray(facets?.relations) ? facets.relations.filter(value => typeof value === 'string').map(value => ({ value: String(value), label: String(value) })) : [], '모든 관계']] as const) {
      const select = this.root.getElementById(id) as HTMLSelectElement;
      this.options(select, [{ value: '', label }, ...values]);
    }
  }

  private options(select: HTMLSelectElement, options: Array<{ value: string; label: string }>): void {
    const current = select.value;
    if (current && !options.some(option => option.value === current)) options.push({ value: current, label: current });
    select.replaceChildren(...options.map(option => {
      const item = this.root.createElement('option'); item.value = option.value; item.textContent = option.label; return item;
    }));
    if (options.some(option => option.value === current)) select.value = current;
  }

  private renderList(): void {
    const list = this.root.getElementById('knowledge-result-list'); if (!list) return;
    const signature = JSON.stringify([this.graph.currentView.focusId, this.graph.currentView.nodes.map(node => [node.id, node.label, node.category, node.sourcePlatform])]);
    if (signature === this.listSignature) return;
    this.listSignature = signature;
    list.replaceChildren(...this.graph.currentView.nodes.map(node => {
      const row = this.root.createElement('div'); row.setAttribute('role', 'listitem');
      const button = this.root.createElement('button'); button.type = 'button'; button.dataset.graphNode = node.id;
      button.textContent = node.label; button.setAttribute('aria-pressed', String(node.id === this.graph.currentView.focusId));
      const meta = this.root.createElement('small'); meta.textContent = [node.category, node.sourcePlatform].filter(Boolean).join(' · ');
      row.append(button, meta); return row;
    }));
  }

  private setText(id: string, value: string): void { const element = this.root.getElementById(id); if (element) element.textContent = value; }
}
