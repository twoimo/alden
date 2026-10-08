import type { fetchSettingsAction } from './runtime';

type Model = { id: string; label: string; provider: string; local: boolean; efforts: string[];
  default_effort: string | null; status: string; selectable: boolean };
const FLASH = 'google-antigravity/gemini-3.8-flash';
const states: Record<string, string> = { available:'사용 가능', running:'실행 중', connected:'연결됨', not_loaded:'준비되지 않음',
  memory:'메모리 부족', memory_unknown:'메모리 확인 필요', quota:'쿼터 제한', authentication:'로그인 필요', unavailable:'연결 확인 필요', not_chat:'음성 모델' };

/** Catalog/health reads only on opening or resuming this control. No timer or
 * per-model background generation; selecting is an explicit preference write. */
export class RoutedModels {
  private readonly events = new AbortController();
  private models: Model[] = [];
  private model = '';
  private effort: string | null = null;
  private mode = 'manual';
  private automaticModel: string | null = null;
  private busy = false;
  private epoch = 0;
  private disposed = false;

  constructor(private readonly load: typeof fetchSettingsAction, private readonly root: Document = document) {
    const signal = this.events.signal;
    root.getElementById('routed-model-picker')?.addEventListener('toggle', () => {
      if ((root.getElementById('routed-model-picker') as HTMLDetailsElement).open) {
        const picker = root.getElementById('routed-model-picker')!;
        const bounds = picker.getBoundingClientRect(), below = (root.defaultView?.innerHeight ?? 800) - bounds.bottom;
        const above = below < 260 && bounds.top > below;
        picker.dataset.placement = above ? 'above' : 'below';
        const menu = picker.querySelector<HTMLElement>('.routed-model-menu');
        if (menu) menu.style.maxHeight = Math.max(90, Math.min(430, (above ? bounds.top : below) - 12)) + 'px';
        void this.refresh();
      }
    }, { signal });
    root.getElementById('routed-model-search')?.addEventListener('input', () => this.renderList(), { signal });
    root.getElementById('routed-model-list')?.addEventListener('click', event => {
      const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>('button[data-route-model]') : null;
      const model = this.models.find(row => row.id === button?.dataset.routeModel);
      if (model) void this.save(model.id, model.id === FLASH && model.efforts.includes('high') ? 'high' : model.default_effort, 'manual');
    }, { signal });
    root.getElementById('routed-model-auto')?.addEventListener('click', () => void this.save(FLASH, 'high', 'automatic'), { signal });
    root.getElementById('routed-model-effort')?.addEventListener('change', () => {
      const effort = (root.getElementById('routed-model-effort') as HTMLSelectElement).value;
      void this.save(this.model, effort || null, this.mode);
    }, { signal });
    root.getElementById('routed-model-refresh')?.addEventListener('click', () => void this.refresh(), { signal });
    root.addEventListener('keydown', event => {
      if (event.key === 'Escape') (root.getElementById('routed-model-picker') as HTMLDetailsElement).open = false;
    }, { signal });
  }

  dispose(): void { this.disposed = true; this.epoch++; this.events.abort(); }

  async refresh(): Promise<void> {
    if (this.disposed || this.busy) return;
    const epoch = ++this.epoch;
    try {
      const payload = await this.load('routed-models');
      if (this.disposed || epoch !== this.epoch) return;
      if (payload?.ok !== true || !Array.isArray(payload.models)) throw Error('catalog');
      this.models = payload.models.flatMap((value): Model[] => {
        if (!value || typeof value !== 'object') return [];
        const row = value as Record<string, unknown>;
        if (typeof row.id !== 'string' || typeof row.label !== 'string' || typeof row.provider !== 'string') return [];
        return [{ id:row.id, label:row.label, provider:row.provider, local:row.local === true,
          efforts:Array.isArray(row.efforts) ? row.efforts.filter((item): item is string => typeof item === 'string') : [],
          default_effort:typeof row.default_effort === 'string' ? row.default_effort : null,
          status:typeof row.status === 'string' ? row.status : 'unavailable', selectable:row.selectable === true }];
      });
      this.model = typeof payload.model === 'string' ? payload.model : FLASH;
      if (!this.models.some(row => row.id === this.model) && this.models.some(row => row.id === 'mlx/' + this.model)) this.model = 'mlx/' + this.model;
      this.effort = typeof payload.reasoning_effort === 'string' ? payload.reasoning_effort : null;
      this.mode = payload.mode === 'automatic' ? 'automatic' : 'manual';
      this.automaticModel = typeof payload.automatic_model === 'string' ? payload.automatic_model : null;
      this.render();
      this.text('routed-model-status', '다음 응답부터 선택한 모델을 사용합니다.');
    } catch {
      if (!this.disposed && epoch === this.epoch) this.text('routed-model-status', 'OpenCodex 연결을 확인해 주세요. 현재 선택은 유지됩니다.');
    }
  }

  private async save(model: string, effort: string | null, mode: string): Promise<void> {
    if (this.busy || this.disposed) return;
    this.busy = true; this.epoch++;
    this.root.getElementById('routed-model-controls')?.setAttribute('aria-busy', 'true');
    this.text('routed-model-status', '선택을 저장하고 있습니다…');
    try {
      const result = await this.load('routed-model-set', {query:JSON.stringify({model, reasoning_effort:effort, mode})});
      if (this.disposed) return;
      if (result?.ok !== true || result.stored !== true) {
        this.text('routed-model-status', result?.reason === 'local_model_not_ready' ? '로컬 모델이 준비되지 않았습니다. 현재 선택은 유지됩니다.' : '선택을 저장하지 못했습니다. 현재 선택은 유지됩니다.');
        return;
      }
      this.model = String(result.model); this.effort = typeof result.reasoning_effort === 'string' ? result.reasoning_effort : null;
      this.mode = result.mode === 'automatic' ? 'automatic' : 'manual'; this.render();
      (this.root.getElementById('routed-model-picker') as HTMLDetailsElement).open = false;
      this.text('routed-model-status', this.mode === 'automatic' ? '응답 시작 시 확인된 상태로 고릅니다. Astra는 직접 선택합니다.' : '다음 응답부터 선택한 모델을 사용합니다.');
    } catch {
      this.text('routed-model-status', '선택을 저장하지 못했습니다. 현재 선택은 유지됩니다.');
    } finally {
      this.busy = false; this.root.getElementById('routed-model-controls')?.removeAttribute('aria-busy');
    }
  }

  private render(): void {
    const chosen = this.models.find(row => row.id === (this.mode === 'automatic' && this.automaticModel ? this.automaticModel : this.model));
    this.text('routed-model-name', this.mode === 'automatic' ? '자동 · ' + (chosen?.label ?? this.model) : chosen?.label ?? this.model);
    const indicator = this.root.getElementById('routed-model-indicator');
    if (indicator) { indicator.dataset.state = chosen?.status ?? 'unavailable'; indicator.textContent = states[chosen?.status ?? 'unavailable'] ?? '확인 필요'; }
    const local = this.models.filter(row => row.local && ['available','running'].includes(row.status));
    const shortage = this.models.some(row => row.local && row.status === 'memory');
    this.text('routed-local-status', local.length ? `로컬 · ${local.length}개 실행 중` : shortage ? '로컬 · 메모리 부족' : '로컬 · 준비된 모델 없음');
    this.root.getElementById('routed-local-status')?.setAttribute('data-state', local.length ? 'running' : 'not_loaded');
    const select = this.root.getElementById('routed-model-effort') as HTMLSelectElement;
    const fallback = this.root.createElement('option'); fallback.value = ''; fallback.textContent = '기본';
    select.replaceChildren(fallback, ...(chosen?.efforts ?? []).map(effort => {
      const option = this.root.createElement('option'); option.value = effort; option.textContent = effort; return option;
    }));
    select.disabled = !chosen?.efforts.length;
    select.value = this.effort ?? '';
    this.root.getElementById('routed-model-auto')?.setAttribute('aria-pressed', String(this.mode === 'automatic'));
    this.renderList();
  }

  private renderList(): void {
    const list = this.root.getElementById('routed-model-list'); if (!list) return;
    const query = (this.root.getElementById('routed-model-search') as HTMLInputElement)?.value.toLocaleLowerCase() ?? '';
    list.replaceChildren();
    let provider = '';
    for (const row of this.models.filter(row => (row.label + ' ' + row.id).toLocaleLowerCase().includes(query))) {
      if (row.provider !== provider) {
        provider = row.provider; const heading = this.root.createElement('p'); heading.className = 'routed-provider'; heading.textContent = provider; list.append(heading);
      }
      const button = this.root.createElement('button'); button.type = 'button'; button.dataset.routeModel = row.id;
      button.className = 'routed-model-row'; button.disabled = !row.selectable; button.setAttribute('aria-pressed', String(row.id === this.model && this.mode === 'manual'));
      const label = this.root.createElement('span'); label.textContent = row.label;
      const status = this.root.createElement('span'); status.className = 'model-availability'; status.dataset.state = row.status;
      status.textContent = states[row.status] ?? '확인 필요';
      status.title = row.status === 'connected' ? '라우터와 모델 목록을 확인했습니다. 실제 응답은 요청할 때 확인합니다.' : row.status === 'available' ? '최근 5분 안에 실제 응답을 확인했습니다.' : row.status === 'running' ? '가중치가 준비된 실행 상태를 확인했습니다.' : status.textContent;
      button.append(label, status); list.append(button);
    }
  }

  private text(id: string, value: string): void { const element = this.root.getElementById(id); if (element) element.textContent = value; }
}
