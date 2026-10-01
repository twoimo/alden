import { invoke } from "@tauri-apps/api/core";
import "./styles.css";
import {
  createCancellationToken,
  unavailableSnapshot,
  type CancellationToken,
  type EmergencyState,
  type RuntimeSnapshot,
} from "./contracts";
import type { SourceLoads } from "./core/load-mapping";
import type { AnimationLoopDiagnostics } from "./core/animation-loop";
import { RenderLifecycle } from "./core/lifecycle";
import {
  tauriVisibilitySubscriber,
  tauriVisibilityReader,
  wireRenderLifecycle,
  type VisibilityReader,
  type VisibilitySubscriber,
} from "./core/lifecycle-wiring";
import type { KnowledgeHologram } from "./knowledge/hologram";
import {
  MAX_FOCUS_HOPS,
  ON_SCREEN_NODE_CAP,
  parseKnowledgeGraph,
  type KnowledgeEdge,
  type KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeView,
} from "./knowledge/graph-model";
import {
  cancelRuntimeRequest,
  cancelModelSwap,
  fetchEmergencyState,
  fetchRuntimeSnapshot,
  fetchSettingsAction,
  normalizeLocalModelId,
  operatorResume,
  selectResidentModel,
  setSwapModel,
  subscribeEmergencyState,
  swapToLargeModel,
  type EmergencyStateSubscriber,
  type SettingsInvoke,
} from "./runtime";
import {
  RuntimeSnapshotPoller,
  browserPollScheduler,
  type PollTimerScheduler,
  type SnapshotCanceller,
  type SnapshotLoader,
} from "./runtime-poller";
import { mainPanelMarkup, renderBackground, renderEmergencyState, renderHistory, renderRooms, settingsMarkup, voiceErrorMessage } from "./ui";
import { RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "./tokens";
import { wireVoiceStart } from "./voice-controls";

export { RuntimeSnapshotPoller, type PollTimerScheduler } from "./runtime-poller";

const app = document.querySelector<HTMLDivElement>("#app")!;
if (!app) throw new Error("app_root_missing");

const isSettings = new URLSearchParams(window.location.search).get("view") === "settings";
document.body.classList.add(isSettings ? "settings-view" : "panel-view");

interface AldenCoreControl {
  readonly renderCount: number;
  start(): void;
  stop(): void;
  diagnostics(): AnimationLoopDiagnostics;
  setSignals(jobLoad: number, voiceRms: number, sources?: SourceLoads): void;
  dispose(): void;
}

function setText(id: string, value: string): void {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function showPanelUnavailable(message: string): void {
  app.dataset.state = "unavailable";
  const panel = document.querySelector<HTMLElement>(".alden-panel");
  if (!panel) return;
  let status = document.getElementById("panel-status");
  if (!status) {
    status = document.createElement("p");
    status.id = "panel-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.style.cssText = "position:absolute;left:12px;right:12px;bottom:9px;margin:0;text-align:center;font-size:10px;line-height:1.35;color:var(--muted);pointer-events:none";
    panel.append(status);
  }
  status.textContent = message;
}

function renderPanelUnavailable(): void {
  app.innerHTML = mainPanelMarkup();
  document.querySelector<HTMLCanvasElement>(".alden-core")
    ?.setAttribute("aria-label", "Alden core 확인 불가");
  showPanelUnavailable("Alden 상태를 확인할 수 없습니다.");
}

function wireRoomAdd(loadSnapshot: SnapshotLoader, loadAction: typeof fetchSettingsAction): void {
  const addBtn = document.querySelector<HTMLButtonElement>("#settings-add-room-button");
  const addSelect = document.querySelector<HTMLSelectElement>("#settings-add-room-select");
  if (!addBtn || !addSelect) return;
  addBtn.addEventListener("click", async () => {
    const selectedId = addSelect.value;
    if (!selectedId) return;
    const selectedTitle = addSelect.selectedOptions[0]?.textContent || "";
    addBtn.disabled = true;
    setText("room-summary", "채팅방을 등록하는 중...");
    try {
      const res = await loadAction("room-upsert", {
        chatId: selectedId,
        query: selectedTitle,
      });
      if (res && res.ok === true) {
        setText("room-summary", `채팅방이 등록되었습니다: ${selectedTitle}`);
        const token = createCancellationToken();
        const updatedSnapshot = await loadSnapshot(token);
        renderRooms(updatedSnapshot);
      } else {
        setText("room-summary", "채팅방을 등록하지 못했습니다. 확인 후 다시 시도해 주세요.");
      }
    } catch {
      setText("room-summary", "채팅방 등록 중 오류가 발생했습니다.");
    } finally {
      addBtn.disabled = false;
    }
  });
}

function modelButton(modelId: string): HTMLButtonElement | null {
  const choice = modelId === RESIDENT_MODEL_ID ? "fast" : "deep";
  return document.querySelector<HTMLButtonElement>(`button[data-model-choice="${choice}"]`);
}

function setModelSelection(modelId: string | null): void {
  for (const candidate of [RESIDENT_MODEL_ID, SWAP_MODEL_ID]) {
    const button = modelButton(candidate);
    const selected = modelId === candidate;
    button?.classList.toggle("selection", selected);
    button?.setAttribute("aria-pressed", String(selected));
  }
}

function setModelBusy(busy: boolean): void {
  for (const candidate of [RESIDENT_MODEL_ID, SWAP_MODEL_ID]) {
    const button = modelButton(candidate);
    if (button) button.disabled = busy;
  }
}

function clearModelFailures(): void {
  modelButton(RESIDENT_MODEL_ID)?.classList.remove("model-failed");
  modelButton(SWAP_MODEL_ID)?.classList.remove("model-failed");
}

function renderModels(snapshot: RuntimeSnapshot): void {
  const current = snapshot.replyModelId;
  const normalized = normalizeLocalModelId(current);
  const selected = current === null ? RESIDENT_MODEL_ID : normalized;
  setModelSelection(selected);
  setText("model-status", selected === RESIDENT_MODEL_ID
    ? "빠른 대화가 선택되어 있습니다."
    : selected === SWAP_MODEL_ID
      ? "깊은 분석이 선택되어 있습니다."
      : "AI 답변 설정을 확인할 수 없습니다.");
}

export function modelSwapFailureText(reason: string): string {
  if (reason === "model_residency_uncertain") {
    return "AI 설정 변경을 확인하지 못했습니다. 현재 설정을 유지했습니다.";
  }
  if (reason === "model_swap_busy") return "다른 설정 변경이 끝난 뒤 다시 시도해 주세요.";
  if (reason === "cancelled") return "요청을 취소했습니다. 기존 설정을 유지했습니다.";
  if (reason === "insufficient_free_memory" || reason === "memory_budget_unavailable") {
    return "안전하게 사용할 수 있는 메모리가 부족해 설정을 바꾸지 않았습니다.";
  }
  if (reason === "model_owner_unmanaged") return "현재 사용 중인 AI와 안전하게 바꿀 수 없어 기존 설정을 유지했습니다.";
  if (reason === "model_owner_unknown" || reason === "model_owner_state_invalid" || reason === "model_owner_state_stale") {
    return "AI 실행 상태를 확인하지 못해 기존 설정을 유지했습니다.";
  }
  if (reason.includes("rollback_failed")) {
    return "AI 설정을 바꾸지 못했습니다. 현재 상태를 확인한 뒤 다시 시도해 주세요.";
  }
  if (reason === "load_failed" || reason === "probe_failed" || reason === "unload_failed") {
    return "깊은 분석을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.";
  }
  return "깊은 분석을 준비하지 못했습니다. 기존 설정을 유지했습니다.";
}

function wireModelSelection(invokeFn: SettingsInvoke): void {
  const resident = modelButton(RESIDENT_MODEL_ID);
  const swap = modelButton(SWAP_MODEL_ID);
  if (!resident || !swap) return;
  let activeSwap: CancellationToken | null = null;
  window.addEventListener("pagehide", () => {
    const token = activeSwap;
    activeSwap = null;
    if (token) void cancelModelSwap(token, invokeFn);
  }, { once: true });
  setModelBusy(false);

  resident.addEventListener("click", () => {
    void (async () => {
      clearModelFailures();
      setModelBusy(true);
      resident.setAttribute("aria-busy", "true");
      setText("model-status", "빠른 대화를 선택하고 있습니다…");
      const result = await selectResidentModel(invokeFn, (phase) => {
        setText("model-status", phase === "launching"
          ? "빠른 대화를 준비하고 있습니다…"
          : "빠른 대화를 적용하고 있습니다…");
      });
      resident.removeAttribute("aria-busy");
      setModelBusy(false);
      if (!result.ok) {
        resident.classList.add("model-failed");
        setText("model-status", result.outcome === "launch_failed"
          ? "빠른 대화를 준비하지 못했습니다. 기존 설정을 유지했습니다."
          : result.outcome === "retry_failed"
            ? "빠른 대화를 준비했지만 선택을 완료하지 못했습니다. 기존 설정을 유지했습니다."
            : "빠른 대화를 선택하지 못했습니다. 기존 설정을 유지했습니다.");
        return;
      }
      setModelSelection(RESIDENT_MODEL_ID);
      setText("model-status", "빠른 대화가 선택되어 있습니다.");
    })();
  });

  swap.addEventListener("click", () => {
    void (async () => {
      clearModelFailures();
      setModelBusy(true);
      swap.setAttribute("aria-busy", "true");
      setText("model-status", "깊은 분석을 선택하고 있습니다…");
      const direct = await setSwapModel(invokeFn);
      if (direct.ok) {
        swap.removeAttribute("aria-busy");
        setModelBusy(false);
        setModelSelection(SWAP_MODEL_ID);
        setText("model-status", "깊은 분석을 사용할 준비가 되었습니다.");
        return;
      }
      if (!direct.needsPrepare) {
        swap.removeAttribute("aria-busy");
        setModelBusy(false);
        swap.classList.add("model-failed");
        setText("model-status", "깊은 분석을 선택하지 못했습니다. 기존 설정을 유지했습니다.");
        return;
      }

      setText("model-status", "깊은 분석을 준비하고 있습니다…");
      const token = createCancellationToken();
      activeSwap = token;
      const result = await swapToLargeModel(token, invokeFn);
      if (activeSwap === token) activeSwap = null;
      swap.removeAttribute("aria-busy");
      setModelBusy(false);
      if (!result.ok) {
        swap.classList.add("model-failed");
        setText("model-status", modelSwapFailureText(result.reason));
        return;
      }
      setModelSelection(SWAP_MODEL_ID);
      setText("model-status", "깊은 분석을 사용할 준비가 되었습니다.");
    })();
  });
}

function renderVoice(snapshot: RuntimeSnapshot): void {
  const voice = snapshot.voice;
  const startButton = document.getElementById("voice-start") as HTMLButtonElement | null;
  if (startButton) startButton.disabled = true;
  if (!voice.customModelSelected) {
    setText("voice-status", "‘올든’을 알아듣는 기능이 준비되지 않아 음성 입력이 꺼져 있습니다.");
    return;
  }
  if (!voice.available) {
    setText("voice-status", "음성 상태를 확인할 수 없습니다.");
    return;
  }
  const labels: Record<string, string> = {
    idle: "마이크가 대기 중입니다.",
    wake_listen: "호출어를 기다리고 있습니다.",
    user_listen: "말씀을 듣고 있습니다.",
    transcribing: "말씀을 정리하고 있습니다.",
    generating: "답변을 준비하고 있습니다.",
    speaking: "답변을 말하고 있습니다.",
    ended: "음성 대화가 끝났습니다.",
    aborted: "음성 대화가 끝났습니다.",
    error: "음성 기능을 사용할 수 없습니다.",
  };
  setText("voice-status", voiceErrorMessage(voice.errorCode) ?? labels[voice.state] ?? "음성 상태를 확인하고 있습니다.");
}

function renderSettingsUnavailable(): void {
  app.innerHTML = settingsMarkup();
  app.dataset.state = "unavailable";
  const snapshot = unavailableSnapshot("desktop_boot_failed");
  renderRooms(snapshot);
  renderModels(snapshot);
  renderVoice(snapshot);
  renderHistory(snapshot);
  renderBackground(snapshot);
  setText("model-status", "AI 답변 설정을 확인할 수 없습니다.");
  setText("settings-sync-source", "대화 준비 상태를 확인할 수 없습니다.");
  setText("knowledge-summary", "대화에서 찾은 연결 정보를 확인할 수 없습니다.");
  setText("knowledge-mode", "확인 필요");
  document.querySelectorAll<HTMLButtonElement>("button").forEach((button) => {
    button.disabled = true;
  });
}

type EmergencyResumeAction = (explicitOptIn: boolean) => Promise<EmergencyState | null>;

export function wireEmergencyResume(
  resumeAction: EmergencyResumeAction,
  root: Document = document,
): { update: (state: EmergencyState) => void } {
  const button = root.querySelector<HTMLButtonElement>("#emergency-resume");
  let current: EmergencyState | null = null;
  let busy = false;

  const renderCurrent = (): void => {
    if (current) renderEmergencyState(current, root);
    if (button) button.disabled = busy || current?.latched !== true;
  };
  const update = (state: EmergencyState): void => {
    if (current && state.epoch <= current.epoch) {
      renderCurrent();
      return;
    }
    current = state;
    renderCurrent();
  };

  button?.addEventListener("click", async () => {
    const before = current;
    if (!before?.latched || busy) return;
    busy = true;
    renderCurrent();
    let resumed: EmergencyState | null = null;
    try {
      resumed = await resumeAction(true);
    } catch {
      resumed = null;
    }
    busy = false;
    if (
      resumed
      && resumed.epoch > before.epoch
      && !resumed.latched
      && resumed.reason === "human_resume"
    ) {
      update(resumed);
      return;
    }
    renderCurrent();
  });

  return { update };
}

function relationLabel(relation: string): string {
  switch (relation.trim().toUpperCase().replace(/[\s-]+/g, "_")) {
    case "MENTIONS": return "언급";
    case "PARTICIPATES":
    case "PARTICIPATES_IN": return "참여";
    case "MEMBER_OF": return "소속";
    case "OCCURRED_IN": return "발생";
    case "RELATED_TO":
    case "ABOUT": return "관련";
    default: return "연결";
  }
}

function relationMeta(edge: KnowledgeEdge, snapshot: RuntimeSnapshot): string {
  const roomId = edge.roomId || edge.evidence.chatId;
  const room = snapshot.rooms.find((candidate) => String(candidate.chatId) === roomId)?.title;
  return room ? `대화방 · ${room}` : "대화에서 확인한 관계";
}

function renderKnowledgeRelations(
  graph: KnowledgeGraph,
  node: KnowledgeNode,
  view: KnowledgeView,
  snapshot: RuntimeSnapshot,
): void {
  const container = document.getElementById("knowledge-relations");
  if (!container) return;
  container.replaceChildren();
  const labels = new Map(graph.nodes.map((candidate) => [candidate.id, candidate.label]));
  const related = view.edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .sort((left, right) => right.weight - left.weight)
    .slice(0, 6);

  if (related.length === 0) {
    const empty = document.createElement("p");
    empty.className = "knowledge-empty";
    empty.textContent = "아직 연결된 항목이 없습니다.";
    container.append(empty);
    return;
  }

  related.forEach((edge) => {
    const row = document.createElement("div");
    row.className = "knowledge-relation-row";
    const triple = document.createElement("strong");
    triple.textContent = `${labels.get(edge.source) ?? "항목"} —${relationLabel(edge.relation)}→ ${labels.get(edge.target) ?? "항목"}`;
    const meta = document.createElement("span");
    meta.textContent = relationMeta(edge, snapshot);
    row.append(triple, meta);
    container.append(row);
  });
}

export async function setupKnowledgeGraph(
  payload: Record<string, unknown> | null,
  snapshot: RuntimeSnapshot,
  loadAction: typeof fetchSettingsAction = fetchSettingsAction,
): Promise<KnowledgeHologram | null> {
  const graph = parseKnowledgeGraph(payload);
  const canvas = document.querySelector<HTMLCanvasElement>("#knowledge-graph-canvas");
  const expand = document.querySelector<HTMLButtonElement>("#knowledge-expand-hop");
  const back = document.querySelector<HTMLButtonElement>("#knowledge-back");
  const overview = document.querySelector<HTMLButtonElement>("#knowledge-overview");
  if (!canvas || !expand || graph.nodes.length === 0) {
    setText("knowledge-summary", "아직 연결된 대화가 없습니다.");
    setText("knowledge-mode", "준비 중");
    return null;
  }

  setText(
    "knowledge-summary",
    `대화에서 찾은 연결 항목 ${graph.nodes.length}개`,
  );
  setText("knowledge-mode", payload?.stale === true ? "자료 확인 필요" : "연결된 주제");

  const a11yContainer = document.querySelector<HTMLDivElement>("#knowledge-accessible-nodes");

  let selectionEpoch = 0;
  let disposed = false;
  const controls = new AbortController();
  const { KnowledgeHologram } = await import("./knowledge/hologram");
  const hologram = new KnowledgeHologram(canvas, graph, ({ node, view }) => {
    if (disposed) return;
    const epoch = ++selectionEpoch;
    if (back) back.disabled = !hologram.canGoBack;
    if (overview) overview.disabled = view.focusId === null;
    expand.disabled = view.focusId === null || view.hops >= MAX_FOCUS_HOPS;
    if (!node) {
      setText("knowledge-focus-title", "항목을 선택하면 관련 정보를 보여드립니다.");
      document.getElementById("knowledge-relations")?.replaceChildren();
      setText("knowledge-retrieve", "항목을 선택하면 관련 대화를 찾아 보여드립니다.");
      return;
    }
    setText("knowledge-focus-title", node.label);
    renderKnowledgeRelations(graph, node, view, snapshot);
    setText("knowledge-retrieve", "관련 대화를 찾고 있습니다…");

    const localRoom = view.edges.find((edge) => edge.source === node.id || edge.target === node.id)?.roomId
      || node.evidence.chatId
      || String(snapshot.rooms[0]?.chatId ?? "");
    void loadAction("knowledge-graph-focus", {
      query: node.label,
      nodeId: node.id,
      chatId: localRoom,
    }).then((focus) => {
      // A → B → A and overview/back are different selections even when the
      // node ID repeats. Earlier retrievals cannot overwrite the current one.
      if (selectionEpoch !== epoch) return;
      if (!focus || focus.ok !== true) {
        setText("knowledge-retrieve", "관련 대화를 찾지 못했습니다.");
        return;
      }
      const facts = Array.isArray(focus.facts) ? focus.facts.filter((item): item is string => typeof item === "string") : [];
      const firstFact = facts[0]?.slice(0, 180);
      setText("knowledge-retrieve", facts.length
        ? `관련 정보 ${facts.length}건을 찾았습니다.${firstFact ? ` ${firstFact}` : ""}`
        : "관련 대화를 찾지 못했습니다.");
    }).catch(() => {
      if (selectionEpoch === epoch) setText("knowledge-retrieve", "관련 대화를 찾지 못했습니다.");
    });
  }, () => {
    disposed = true;
    selectionEpoch += 1;
    controls.abort();
  });

  expand.addEventListener("click", () => {
    hologram.expandOneHop();
  }, { signal: controls.signal });
  back?.addEventListener("click", () => hologram.back(), { signal: controls.signal });
  overview?.addEventListener("click", () => hologram.reset(), { signal: controls.signal });

  if (a11yContainer) {
    a11yContainer.replaceChildren();
    graph.nodes.slice(0, ON_SCREEN_NODE_CAP).forEach((node) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "knowledge-a11y-node";
      btn.dataset.nodeId = node.id;
      btn.textContent = node.label;
      btn.setAttribute("aria-label", `${node.label} 선택`);
      btn.addEventListener("click", () => {
        hologram.clickNode(node.id);
      }, { signal: controls.signal });
      a11yContainer.append(btn);
    });
  }

  return hologram;
}

interface SettingsBootDependencies {
  loadSnapshot: SnapshotLoader;
  loadAction: typeof fetchSettingsAction;
  loadEmergency: typeof fetchEmergencyState;
  resumeEmergency: EmergencyResumeAction;
  subscribeEmergency: EmergencyStateSubscriber | null;
  wireVoice: typeof wireVoiceStart;
  invokeCommand: typeof invoke;
  subscribeVisibility: VisibilitySubscriber | null;
  /** Boot handshake: the shell's current visibility decides the first state. */
  readVisibility: VisibilityReader | null;
}

export async function bootSettings(
  overrides: Partial<SettingsBootDependencies> = {},
): Promise<void> {
  const dependencies: SettingsBootDependencies = {
    loadSnapshot: fetchRuntimeSnapshot,
    loadAction: fetchSettingsAction,
    loadEmergency: fetchEmergencyState,
    resumeEmergency: operatorResume,
    subscribeEmergency: subscribeEmergencyState,
    wireVoice: wireVoiceStart,
    invokeCommand: invoke,
    subscribeVisibility: tauriVisibilitySubscriber,
    readVisibility: tauriVisibilityReader,
    ...overrides,
  };
  app.innerHTML = settingsMarkup();
  app.dataset.state = "loading";
  let hologram: KnowledgeHologram | null = null;
  try {
    const emergency = wireEmergencyResume(dependencies.resumeEmergency);
    if (dependencies.subscribeEmergency) {
      try {
        const unsubscribe = await dependencies.subscribeEmergency(emergency.update);
        window.addEventListener("pagehide", unsubscribe, { once: true });
      } catch {
        // Initial readback below remains authoritative when event subscription is unavailable.
      }
    }
    const token = createCancellationToken();
    const results = await Promise.allSettled([
      dependencies.loadSnapshot(token),
      dependencies.loadAction("knowledge-graph-status"),
      dependencies.loadAction("knowledge-graph"),
      dependencies.loadEmergency(),
    ] as const);
    const [snapshotResult, knowledgeResult, graphResult, emergencyResult] = results;
    const snapshot = snapshotResult.status === "fulfilled"
      ? snapshotResult.value
      : unavailableSnapshot("settings_snapshot_unavailable");
    const knowledge = knowledgeResult.status === "fulfilled" ? knowledgeResult.value : null;
    const graphPayload = graphResult.status === "fulfilled" ? graphResult.value : null;
    const degraded = [snapshotResult, knowledgeResult, graphResult]
      .some((result) => result.status === "rejected") || !snapshot.available;

    if (emergencyResult.status === "fulfilled" && emergencyResult.value) {
      emergency.update(emergencyResult.value);
    }

    renderRooms(snapshot);
    wireRoomAdd(dependencies.loadSnapshot, dependencies.loadAction);
    renderModels(snapshot);
    wireModelSelection(dependencies.invokeCommand);
    renderVoice(snapshot);
    renderHistory(snapshot);
    renderBackground(snapshot);
    dependencies.wireVoice(document, dependencies.invokeCommand);

    if (knowledge) {
      setText("settings-sync-source", knowledge.stale === true
        ? "대화 자료를 새로 확인해야 합니다."
        : "대화 자료가 준비되었습니다.");
    } else {
      setText("settings-sync-source", "대화 준비 상태를 확인하지 못했습니다.");
    }

    hologram = await setupKnowledgeGraph(graphPayload, snapshot, dependencies.loadAction);
    app.dataset.state = degraded ? "unavailable" : "ready";
    if (hologram) {
      const graph = hologram;
      const focusSlots = new Map(parseKnowledgeGraph(graphPayload).nodes.map((node, index) => [node.id, index]));
      Object.defineProperty(window, "__knowledgeRenderCount", { configurable: true, get: () => graph.renderCount });
      const lifecycle = new RenderLifecycle(graph, () => undefined, () => undefined);
      Object.defineProperty(window, "__knowledgeRenderDiagnostics", { configurable: true, get: () => ({
        ...graph.diagnostics(), nodeCount: graph.currentView.nodes.length, edgeCount: graph.currentView.edges.length,
        hops: graph.currentView.hops, focused: graph.currentView.focusId !== null,
        focusSlot: graph.currentView.focusId === null ? -1 : focusSlots.get(graph.currentView.focusId) ?? -1,
        canGoBack: graph.canGoBack,
        targets: graph.navigationTargets,
      }) });
      Object.defineProperty(window, "__knowledgeRenderPause", { configurable: true, get: () => lifecycle.lastPauseMeasurement() });
      wireRenderLifecycle(lifecycle, {
        subscribeVisibility: dependencies.subscribeVisibility,
        readVisibility: dependencies.readVisibility,
        onClosed: () => {
          try {
            graph.dispose();
          } catch {
            // The page is closing; disposal remains fail-closed and idempotent here.
          }
        },
      });
    }
  } catch {
    try {
      hologram?.dispose();
    } catch {
      // Rendering the fixed unavailable state takes precedence over cleanup errors.
    }
    renderSettingsUnavailable();
  }
}

type CommandInvoker = (command: string) => Promise<unknown>;

interface PanelBootDependencies {
  createCore: (canvas: HTMLCanvasElement) => AldenCoreControl | Promise<AldenCoreControl>;
  loadSnapshot: SnapshotLoader;
  cancelSnapshot: SnapshotCanceller;
  makeToken: () => CancellationToken;
  pollScheduler: PollTimerScheduler;
  invokeCommand: CommandInvoker;
  subscribeVisibility: VisibilitySubscriber | null;
  /** Boot handshake: the shell's current visibility decides the first state. */
  readVisibility: VisibilityReader | null;
}

export async function bootPanel(
  overrides: Partial<PanelBootDependencies> = {},
): Promise<void> {
  const dependencies: PanelBootDependencies = {
    createCore: async (canvas) => {
      const { AldenCore } = await import("./core/alden-core");
      return new AldenCore(canvas);
    },
    loadSnapshot: fetchRuntimeSnapshot,
    cancelSnapshot: cancelRuntimeRequest,
    makeToken: createCancellationToken,
    pollScheduler: browserPollScheduler,
    invokeCommand: invoke,
    subscribeVisibility: tauriVisibilitySubscriber,
    readVisibility: tauriVisibilityReader,
    ...overrides,
  };
  app.innerHTML = mainPanelMarkup();
  app.dataset.state = "loading";
  const canvas = document.querySelector<HTMLCanvasElement>(".alden-core");
  if (!canvas) {
    renderPanelUnavailable();
    return;
  }

  let core: AldenCoreControl | null = null;
  let closePanel: (() => void) | null = null;
  try {
    core = await dependencies.createCore(canvas);
    const activeCore = core;
    Object.defineProperty(window, "__aldenRenderCount", { configurable: true, get: () => activeCore.renderCount });
    Object.defineProperty(window, "__aldenRenderDiagnostics", { configurable: true, get: () => activeCore.diagnostics() });
    const polling = new RuntimeSnapshotPoller(
      activeCore,
      dependencies.loadSnapshot,
      dependencies.cancelSnapshot,
      dependencies.makeToken,
      dependencies.pollScheduler,
    );
    const lifecycle = new RenderLifecycle(activeCore, () => polling.stop(), () => polling.start());
    Object.defineProperty(window, "__aldenRenderPause", {
      configurable: true,
      get: () => lifecycle.lastPauseMeasurement(),
    });
    let disposed = false;
    let detachLifecycle: (() => void) | null = null;

    const close = (): void => {
      if (disposed) return;
      disposed = true;
      detachLifecycle?.();
      // Stopping is idempotent, so this also stops the loop and the poller on
      // the teardown path that never reaches the pagehide handler.
      try {
        lifecycle.transition("closed");
      } catch {
        polling.stop();
      }
    };
    closePanel = close;

    // One wiring owns blur/focus/visibilitychange/pagehide and the Rust
    // window-visibility event, so hiding the panel stops the 3D loop and the
    // snapshot poller even when the webview reports no DOM signal of its own.
    detachLifecycle = wireRenderLifecycle(lifecycle, {
      subscribeVisibility: dependencies.subscribeVisibility,
      readVisibility: dependencies.readVisibility,
      onClosed: () => {
        close();
        try {
          activeCore.dispose();
        } catch {
          // WebGL teardown must never surface an exception during page shutdown.
        }
      },
    });

    app.dataset.state = "ready";
  } catch {
    if (closePanel) closePanel();
    else {
      try {
        core?.dispose();
      } catch {
        // A partially constructed renderer is best-effort cleanup only.
      }
    }
    renderPanelUnavailable();
  }
}

interface DesktopBootDependencies {
  panel: () => Promise<void>;
  settings: () => Promise<void>;
}

export async function startDesktopApp(
  settingsView = isSettings,
  booters: DesktopBootDependencies = { panel: bootPanel, settings: bootSettings },
): Promise<void> {
  try {
    if (settingsView) await booters.settings();
    else await booters.panel();
  } catch {
    if (settingsView) renderSettingsUnavailable();
    else renderPanelUnavailable();
  }
}

void startDesktopApp().catch(() => undefined);
