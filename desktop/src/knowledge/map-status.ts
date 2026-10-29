type Payload = Record<string, unknown> | null | undefined;
export type MapStatus = 'ready' | 'empty' | 'partial' | 'stale' | 'unavailable';

export function knowledgeMapStatus(payload: Payload, visibleNodes: number): MapStatus {
  if (!payload || payload.ok !== true || !Array.isArray(payload.nodes)
    || !Number.isSafeInteger(visibleNodes) || visibleNodes < 0
    || (payload.partial !== undefined && typeof payload.partial !== 'boolean')
    || (payload.stale !== undefined && typeof payload.stale !== 'boolean')) return 'unavailable';
  if (payload.partial === true) return 'partial';
  if (payload.stale === true) return 'stale';
  if (!visibleNodes) return payload.nodes.length ? 'unavailable' : 'empty';
  return payload.nodes.length ? 'ready' : 'unavailable';
}

type RequestState = 'loading' | 'unverified' | 'paused';
const copy: Record<Exclude<MapStatus, 'ready'> | RequestState, [string, string]> = {
  loading: ['지식 지도를 불러오는 중입니다', '현재 범위의 자료를 확인하고 있습니다.'],
  unverified: ['이 범위의 자료를 아직 확인하지 않았습니다', '현재 범위의 확인 결과가 도착하면 지도를 표시합니다.'],
  paused: ['이 범위의 갱신이 중지되어 있습니다', '갱신을 재개하면 현재 범위의 자료를 확인합니다.'],
  empty: ['이 범위에 표시할 지식이 없습니다', '검색어나 프로젝트·출처 필터를 조정해 보세요. 저장된 원문은 그대로 유지됩니다.'],
  partial: ['일부 출처를 확인하지 못했습니다', '현재 확인할 수 있는 자료의 범위입니다. 연결 상태를 확인한 뒤 다시 살펴보세요.'],
  stale: ['자료를 다시 확인해야 합니다', '현재 원본과의 일치를 확인하지 못했습니다. 확인 전까지 지도의 범위를 살펴보세요.'],
  unavailable: ['지도를 불러올 수 없습니다', '자료가 없는 상태와는 다릅니다. 설정에서 연결 상태를 확인해 주세요.'],
};

export function renderKnowledgeMapStatus(root: Document, payload: Payload, visibleNodes: number): void {
  renderState(root, knowledgeMapStatus(payload, visibleNodes), visibleNodes);
}

export function renderKnowledgeMapRequestState(root: Document, state: RequestState, visibleNodes = 0): void {
  renderState(root, state, visibleNodes);
}

function renderState(root: Document, state: MapStatus | RequestState, visibleNodes: number): void {
  const panel = root.getElementById('knowledge-map-status');
  if (!panel) return;
  panel.dataset.state = state;
  panel.dataset.presentation = visibleNodes > 0 ? 'notice' : 'empty';
  panel.hidden = state === 'ready';
  if (state === 'ready') return;
  const [title, description] = copy[state];
  const heading = root.getElementById('knowledge-map-status-title');
  const message = root.getElementById('knowledge-map-status-message');
  if (heading && heading.textContent !== title) heading.textContent = title;
  if (message && message.textContent !== description) message.textContent = description;
}
