import { LAYOUT } from "./tokens";
import type { EmergencyState, RuntimeSnapshot } from "./contracts";

export const MAIN_PANEL_CONTROLS = Object.freeze([] as const);

export function voiceErrorMessage(errorCode: string | null): string | null {
  switch (errorCode) {
    case "voice_memory_budget_low":
      return "기기 메모리 여유가 부족해 음성 처리를 멈췄습니다.";
    case "voice_memory_budget_unavailable":
      return "기기 메모리 상태를 확인할 수 없어 음성 처리를 시작하지 않았습니다.";
    case "alden_wake_model_unavailable":
      return "‘올든’을 알아듣는 기능이 준비되지 않아 음성 입력을 시작하지 않았습니다.";
    case "mic_disconnected":
      return "마이크를 사용할 수 없습니다. 연결을 확인해 주세요.";
    case "mic_unavailable":
      return "마이크를 열 수 없습니다. 연결 상태를 확인해 주세요.";
    case "mic_access_required":
      return "음성 입력을 사용하려면 마이크 접근을 허용해 주세요.";
    case "mic_audio_gap":
      return "음성 입력의 일부를 놓쳐 중단했습니다. 다시 시작해 주세요.";
    case "voice_audio_processing_unavailable":
      return "음성 입력과 재생을 연결하지 못해 중단했습니다. 다시 시작해 주세요.";
    case "stt_empty":
      return "말씀을 알아듣지 못했습니다. 다시 말씀해 주세요.";
    case "generation_error":
      return "답변을 준비하지 못했습니다. 다시 말씀해 주세요.";
    case "tts_error":
      return "답변을 소리로 들려주지 못했습니다.";
    case "global_abort":
      return "음성 요청을 중단했습니다.";
    case null:
      return null;
    default:
      return "음성 기능을 시작하지 못했습니다.";
  }
}

export const SETTINGS_IDS = Object.freeze([
  "settings-room-popup",
  "settings-add-room-select",
  "settings-add-room-button",
  "settings-sync-source",
  "settings-activity-source",
  "settings-sync-card",
  "settings-knowledge-card",
  "knowledge-graph-canvas",
  "knowledge-accessible-nodes",
  "knowledge-expand-hop",
  "knowledge-back",
  "knowledge-overview",
  "knowledge-focus-title",
  "knowledge-relations",
  "knowledge-retrieve",
] as const);

export function mainPanelMarkup(): string {
  return `<main class="alden-panel" aria-label="올든">
    <canvas class="alden-core" width="${LAYOUT.coreSize}" height="${LAYOUT.coreSize}" aria-label="올든 화면"></canvas>
  </main>`;
}

export function settingsMarkup(): string {
  return `<main class="settings-shell" aria-label="올든 설정" data-settings-page="memory">
    <aside class="settings-sidebar">
      <div class="settings-brand"><i data-lucide="Aperture" aria-hidden="true"></i><div><strong>Alden</strong><span>올든 설정</span></div></div>
      <nav class="settings-nav" role="tablist" aria-label="설정 영역" aria-orientation="vertical">
        <button id="settings-tab-memory" class="settings-nav-item" type="button" role="tab" data-settings-view="memory" aria-controls="settings-page-memory" aria-selected="true" tabindex="0"><i data-lucide="Network" aria-hidden="true"></i><span>지식 그래프</span></button>
        <button id="settings-tab-conversation" class="settings-nav-item" type="button" role="tab" data-settings-view="conversation" aria-controls="settings-page-conversation" aria-selected="false" tabindex="-1"><i data-lucide="MessageCircle" aria-hidden="true"></i><span>대화 · AI</span></button>
        <button id="settings-tab-voice" class="settings-nav-item" type="button" role="tab" data-settings-view="voice" aria-controls="settings-page-voice" aria-selected="false" tabindex="-1"><i data-lucide="AudioLines" aria-hidden="true"></i><span>음성 대화</span></button>
        <button id="settings-tab-history" class="settings-nav-item" type="button" role="tab" data-settings-view="history" aria-controls="settings-page-history" aria-selected="false" tabindex="-1"><i data-lucide="History" aria-hidden="true"></i><span>최근 답변</span></button>
      </nav>
      <div class="settings-sidebar-footer"><p><i data-lucide="ShieldCheck" aria-hidden="true"></i>내 컴퓨터에서 실행</p><span>긴급 중단은 ⌘⌥⇧Esc를 누르세요.</span><kbd>⌘ ⌥ ⇧ Esc</kbd></div>
    </aside>
    <div class="settings-main">
      <header class="settings-topbar"><p>Alden <span aria-hidden="true">/</span><strong id="settings-current-view">지식 그래프</strong></p><span class="settings-local-label"><i data-lucide="Laptop" aria-hidden="true"></i>로컬 AI 비서</span></header>
      <div id="emergency-controls" class="emergency-controls" hidden><span id="emergency-status" role="status" aria-live="polite"></span><button id="emergency-resume" type="button" disabled></button></div>
      <div class="settings-content">
        <section id="settings-page-conversation" class="settings-page" role="tabpanel" aria-labelledby="settings-tab-conversation" hidden>
          <header class="settings-page-heading"><p class="settings-overline">CONVERSATION</p><h1>대화를 나누는 방식</h1><p>올든이 답변할 공간과 대화의 깊이를 정합니다.</p></header>
          <section class="settings-section" aria-labelledby="rooms-title">
            <div class="setting-row"><div class="setting-copy"><h2 id="rooms-title">등록된 채팅방</h2><p>올든이 함께할 대화 공간입니다.</p></div><div class="setting-control"><label class="sr-only" for="settings-room-popup">등록된 채팅방</label><select id="settings-room-popup" aria-label="대상 채팅방"><option value="">확인 중</option></select><p id="room-summary" class="settings-field-note" role="status" aria-live="polite">등록된 채팅방을 불러오는 중입니다.</p></div></div>
            <details class="room-disclosure"><summary><i data-lucide="Plus" aria-hidden="true"></i>새 채팅방 등록<i data-lucide="ChevronDown" aria-hidden="true"></i></summary><div class="room-action-row"><label class="sr-only" for="settings-add-room-select">추가할 채팅방 선택</label><select id="settings-add-room-select" aria-label="추가할 채팅방 선택"><option value="">추가할 채팅방 선택</option></select><button id="settings-add-room-button" type="button">추가</button></div></details>
          </section>
          <section class="settings-section" aria-labelledby="model-title"><div class="section-heading"><div class="setting-copy"><h2 id="model-title">AI 답변</h2><p>질문에 맞는 답변 방식을 선택하세요.</p></div><span class="tag muted-tag">이 기기에서 실행</span></div>
            <div class="model-options"><button class="model-row selection" type="button" data-model-choice="fast" aria-pressed="true" disabled><span class="model-symbol"><i data-lucide="Zap" aria-hidden="true"></i></span><span class="model-copy"><strong>빠른 대화</strong><span class="model-desc">일상적인 질문에 빠르게 답합니다.</span><span class="tag">기본 사용</span></span></button><button class="model-row" type="button" data-model-choice="deep" aria-pressed="false" disabled><span class="model-symbol"><i data-lucide="Sparkles" aria-hidden="true"></i></span><span class="model-copy"><strong>깊은 분석</strong><span class="model-desc">어려운 질문을 차분하게 살펴봅니다.</span><span class="tag muted-tag">필요할 때 사용</span></span></button></div>
            <p id="model-status" class="model-status-highlight" role="status" aria-live="polite">AI 답변 상태를 확인하고 있습니다.</p>
          </section>
          <section id="settings-sync-card" class="settings-section settings-sync-row" aria-labelledby="sync-title"><i data-lucide="MessagesSquare" aria-hidden="true"></i><div><h2 id="sync-title">카카오톡 대화</h2><p id="settings-sync-source" role="status" aria-live="polite">대화 준비 상태를 확인하고 있습니다.</p><p id="settings-activity-source" class="settings-field-note">앱의 작업 상태를 확인하고 있습니다.</p></div></section>
        </section>
        <section id="settings-page-voice" class="settings-page" role="tabpanel" aria-labelledby="settings-tab-voice" hidden>
          <header class="settings-page-heading"><p class="settings-overline">VOICE</p><h1>목소리로 이어지는 대화</h1><p>말씀을 듣고, 답변을 소리로 전합니다.</p></header>
          <section class="settings-section voice-settings" aria-labelledby="voice-title"><div class="voice-symbol"><i data-lucide="Mic" aria-hidden="true"></i></div><div><div class="section-heading"><h2 id="voice-title">음성 대화</h2><span class="tag muted-tag">이 기기에서 처리</span></div><p id="voice-status">‘올든’을 알아듣는 기능이 준비되지 않아 음성 입력이 꺼져 있습니다.</p><div class="voice-wake-line"><span>호출어: 올든</span><button id="voice-start" type="button" disabled>마이크 켜기</button></div></div></section>
        </section>
        <section id="settings-page-memory" class="settings-page" role="tabpanel" aria-labelledby="settings-tab-memory">
          <section id="settings-knowledge-card" class="knowledge-section" aria-labelledby="knowledge-title">
            <div class="knowledge-workspace"><div class="knowledge-hologram-shell"><canvas id="knowledge-graph-canvas" width="1600" height="1200" aria-label="대화 속 이름과 주제의 연결 그림"></canvas><div class="knowledge-node-labels" aria-hidden="true"></div><div id="knowledge-accessible-nodes" class="sr-only" role="region" aria-label="대화 검색 항목 목록"></div></div></div>
            <header class="knowledge-heading"><p class="settings-overline">YOUR KNOWLEDGE</p><h1 id="knowledge-title">대화가 남기는 연결</h1><p id="knowledge-summary" role="status" aria-live="polite">지식 그래프를 불러오고 있습니다.</p><span id="knowledge-mode" class="tag">확인 중</span><p id="knowledge-sync" class="knowledge-sync" role="status" aria-live="polite">기억의 갱신 상태를 확인하고 있습니다.</p></header>
            <div class="knowledge-hologram-toolbar"><button id="knowledge-back" type="button" disabled>이전</button><button id="knowledge-overview" type="button" disabled>전체 보기</button><button id="knowledge-expand-hop" type="button" disabled>더 보기</button></div>
            <aside class="knowledge-focus-card" aria-label="선택한 지식" aria-live="polite" hidden><i data-lucide="Waypoints" aria-hidden="true"></i><strong id="knowledge-focus-title">항목을 선택하면 관련 정보를 보여드립니다.</strong><div id="knowledge-relations" class="knowledge-relations"></div><p id="knowledge-retrieve">항목을 선택하면 관련 대화를 찾아 보여드립니다.</p></aside>
          </section>
        </section>
        <section id="settings-page-history" class="settings-page" role="tabpanel" aria-labelledby="settings-tab-history" hidden>
          <header class="settings-page-heading"><p class="settings-overline">ACTIVITY</p><h1>최근에 나눈 답변</h1><p>올든이 참여한 대화의 흐름을 간결하게 확인합니다.</p></header>
          <section class="settings-section" aria-labelledby="history-title"><div class="section-heading"><h2 id="history-title">최근 답변</h2><span class="tag muted-tag">요약만 표시</span></div><p id="history-summary" class="muted" role="status" aria-live="polite">최근 답변을 확인하고 있습니다.</p><div id="history-list" class="knowledge-relations" role="list" aria-label="최근 답변 기록"></div></section>
        </section>
      </div>
    </div>
  </main>`;
}

export function renderEmergencyState(state: EmergencyState, root: Document = document): void {
  const controls = root.querySelector<HTMLElement>("#emergency-controls");
  const status = root.querySelector<HTMLElement>("#emergency-status");
  const button = root.querySelector<HTMLButtonElement>("#emergency-resume");
  if (!controls || !status || !button) return;
  if (!state.latched) {
    controls.hidden = true;
    status.textContent = "";
    button.textContent = "";
    button.disabled = true;
    return;
  }
  status.textContent = "일시 중지됨";
  button.textContent = "다시 시작";
  button.disabled = false;
  controls.hidden = false;
}

export function renderHistory(snapshot: RuntimeSnapshot, root: Document = document): void {
  const summary = root.getElementById("history-summary");
  const list = root.getElementById("history-list");
  if (!summary || !list) return;
  list.replaceChildren();

  if (!snapshot.available) {
    summary.textContent = "최근 답변 기록을 불러오지 못했습니다.";
    return;
  }
  if (snapshot.recentReceipts.length === 0) {
    summary.textContent = "최근 기록이 없습니다.";
    return;
  }

  summary.textContent = `최근 ${snapshot.recentReceipts.length}건 · 메시지 내용은 표시하지 않습니다.`;
  snapshot.recentReceipts.forEach((receipt) => {
    const row = root.createElement("div");
    row.className = "history-receipt-card";
    row.setAttribute("role", "listitem");

    const header = root.createElement("div");
    header.className = "history-receipt-header";

    const heading = root.createElement("strong");
    heading.className = "history-receipt-title";
    heading.textContent = `${receipt.displayTime || receipt.clock || "시간 미기록"} · ${receipt.title}`;

    const badge = root.createElement("span");
    badge.className = "history-receipt-badge";
    badge.textContent = receipt.outcomeText || receiptOutcomeLabel(receipt.outcome);

    header.append(heading, badge);

    const detail = root.createElement("span");
    detail.className = "history-receipt-detail";
    const reason = receipt.reasonText || receiptReasonLabel(receipt.reasonCode);
    detail.textContent = `${receipt.outcomeText || receiptOutcomeLabel(receipt.outcome)} · ${reason} · 대화 찾기 ${retrievalLabel(receipt.retrievalState)}`;

    row.append(header, detail);
    list.append(row);
  });
}

function receiptOutcomeLabel(outcome: string): string {
  switch (outcome) {
    case "sent": return "답변 완료";
    case "deferred": return "나중에 처리";
    case "scheduled": return "예약됨";
    case "skipped": return "건너뜀";
    default: return "기록됨";
  }
}

function receiptReasonLabel(reasonCode: string): string {
  switch (reasonCode) {
    case "already_commented": return "이미 답변한 대화";
    case "low_information": return "답변할 정보가 부족한 대화";
    case "uncertain": return "판단을 보류한 대화";
    case "direct_question": return "질문에 답변";
    default: return "사유가 기록되지 않았습니다";
  }
}

function retrievalLabel(state: string): string {
  switch (state) {
    case "ok": return "자료 확인됨";
    case "empty": return "관련 자료 없음";
    case "skipped": return "확인하지 않음";
    case "error": return "자료를 확인하지 못함";
    case "index_not_ready": return "자료 준비 중";
    default: return "상태 확인 중";
  }
}

function activityStateLabel(value: unknown): string {
  const state = safeDisplayString(value, "").toLowerCase();
  if (["active", "running", "in_progress", "processing"].includes(state)) return "진행 중";
  if (["ready", "complete", "completed", "done", "ok", "success"].includes(state)) return "완료";
  if (["queued", "pending", "waiting"].includes(state)) return "대기 중";
  if (["sending", "send", "publishing"].includes(state)) return "전송 중";
  if (["behind", "stale"].includes(state)) return "새로 확인 필요";
  if (["error", "failed", "unavailable", "blocked"].includes(state)) return "확인 필요";
  return "확인 중";
}

export function renderBackground(snapshot: RuntimeSnapshot, root: Document = document): void {
  const target = root.getElementById("settings-activity-source");
  if (!target) return;
  if (!snapshot.available) {
    target.textContent = "앱의 작업 상태를 불러오지 못했습니다.";
    return;
  }

  const background = snapshot.background;
  const empty = background.activity === 0
    && background.replyLoad === 0
    && background.geeknews.activity === 0
    && background.dbSync.activity === 0
    && background.geeknews.state === "unknown"
    && background.dbSync.state === "unknown"
    && background.caption.length === 0
    && !snapshot.pipeline.active
    && snapshot.jobs.length === 0;
  if (empty) {
    target.textContent = "백그라운드 활동이 없습니다.";
    return;
  }

  const pendingReplies = Math.round(Math.min(1, Math.max(0, background.replyLoad)) * 4);
  const pipeline = snapshot.pipeline.active ? " · 답변 준비 중" : "";
  const jobs = snapshot.jobs.length > 0 ? ` · 다른 작업 ${snapshot.jobs.length}건 진행 중` : "";
  target.textContent = `작업 현황 · 답변 대기 ${pendingReplies}건 · 긱뉴스 ${activityStateLabel(background.geeknews.state)} · 대화 준비 ${activityStateLabel(background.dbSync.state)}${pipeline}${jobs}`;
}

function safeDisplayString(value: unknown, fallback: string): string {
  if (value === undefined || value === null) return fallback;
  try {
    return String(value);
  } catch {
    return fallback;
  }
}

function syncRoomOptions(
  select: HTMLSelectElement,
  items: ReadonlyArray<{ value: string; label: string }>,
): void {
  // Stable snapshots must not rebuild a native menu the user is interacting with.
  if (select.options.length === items.length && items.every((item, index) => {
    const option = select.options[index];
    return option.value === item.value && option.textContent === item.label;
  })) return;

  const selected = select.value;
  const options = items.map((item) => {
    const option = select.ownerDocument.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    return option;
  });
  select.replaceChildren(...options);
  if (items.some((item) => item.value === selected)) select.value = selected;
}

export function renderRooms(snapshot: RuntimeSnapshot, root: Document = document): void {
  const rooms = snapshot.available ? snapshot.rooms : [];
  const popup = root.querySelector<HTMLSelectElement>("#settings-room-popup");
  if (popup) {
    syncRoomOptions(popup, rooms.length > 0
      ? rooms.map((room) => ({ value: String(room.chatId), label: room.title }))
      : [{ value: "", label: snapshot.available
        ? "등록된 채팅방이 없습니다." : "채팅방 목록을 불러오지 못했습니다." }]);
  }

  const summary = root.getElementById("room-summary");
  if (summary) {
    let message: string;
    if (!snapshot.available) {
      message = "채팅방 목록을 불러오지 못했습니다. 다시 확인해 주세요.";
    } else if (rooms.length === 0) {
      message = "등록된 채팅방이 없습니다.";
    } else {
      const enabled = rooms.filter((room) => room.autoReply);
      const ready = enabled.filter((room) => room.live && room.replyReadiness === "ready").length;
      const attention = enabled.length - ready;
      const off = rooms.length - enabled.length;
      const parts = [`등록된 채팅방 ${rooms.length}개`, `답변 가능 ${ready}개`];
      if (attention > 0) parts.push(`확인 필요 ${attention}개`);
      if (off > 0) parts.push(`자동 답변 꺼짐 ${off}개`);
      message = parts.join(" · ");
    }
    if (summary.textContent !== message) summary.textContent = message;
  }

  const addSelect = root.querySelector<HTMLSelectElement>("#settings-add-room-select");
  if (addSelect) {
    const enrolledIds = new Set(rooms.map((room) => room.chatId));
    const candidates = snapshot.available
      ? (snapshot.availableChats || []).filter((chat) => !enrolledIds.has(chat.chatId)) : [];
    syncRoomOptions(addSelect, [
      { value: "", label: !snapshot.available ? "채팅방 목록을 불러오지 못했습니다."
        : candidates.length > 0 ? "추가할 채팅방 선택" : "추가 가능한 새 채팅방 없음" },
      ...candidates.map((chat) => ({ value: String(chat.chatId), label: chat.title })),
    ]);
  }
}
