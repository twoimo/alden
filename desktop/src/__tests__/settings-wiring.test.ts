// @vitest-environment happy-dom
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { parseRuntimeSnapshot, unavailableSnapshot, type CancellationToken, type RuntimeSnapshot } from "../contracts";
import type { KnowledgeDrilldown, KnowledgeGraph, KnowledgeNode, KnowledgeView } from "../knowledge/graph-model";
import type { PollTimerScheduler } from "../runtime-poller";
import { RESIDENT_MODEL_ID, SWAP_MODEL_ID } from "../tokens";
import { mainPanelMarkup } from "../ui";

type InvokeArgs = Record<string, unknown> | undefined;
type FocusHandler = (event: { node: KnowledgeNode; view: KnowledgeView }) => void;

interface FakeHologram {
  graph: KnowledgeGraph;
  renderCount: number;
  starts: number;
  stops: number;
  disposals: number;
  clickNode(nodeId: string): KnowledgeView;
}

const invoke = vi.hoisted(() => vi.fn<(command: string, args?: InvokeArgs) => Promise<unknown>>());
const stage = vi.hoisted(() => ({ holograms: [] as FakeHologram[], failConstruction: false }));

vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("../knowledge/hologram", async () => {
  const model = await import("../knowledge/graph-model");
  return {
    KnowledgeHologram: class implements FakeHologram {
      renderCount = 7;
      starts = 0;
      stops = 0;
      disposals = 0;
      private readonly drilldown: KnowledgeDrilldown;

      constructor(
        readonly canvas: HTMLCanvasElement,
        readonly graph: KnowledgeGraph,
        private readonly onFocus: FocusHandler,
      ) {
        if (stage.failConstruction) throw new Error("webgl_unavailable");
        this.drilldown = new model.KnowledgeDrilldown(graph);
        stage.holograms.push(this);
      }

      start(): void { this.starts += 1; }
      stop(): void { this.stops += 1; }
      dispose(): void { this.disposals += 1; }

      clickNode(nodeId: string): KnowledgeView {
        const node = this.graph.nodes.find((candidate) => candidate.id === nodeId);
        const view = this.drilldown.clickNode(nodeId);
        if (node) this.onFocus({ node, view });
        return view;
      }

      expandOneHop(): KnowledgeView {
        return this.drilldown.expandOneHop();
      }
    },
  };
});

let main: typeof import("../main");

beforeAll(async () => {
  document.body.innerHTML = '<div id="app"></div>';
  window.history.replaceState({}, "", "/?view=settings");
  invoke.mockResolvedValue(null);
  main = await import("../main");
  await vi.waitFor(() => expect(app().dataset.state).toBe("unavailable"));
});

beforeEach(() => {
  invoke.mockReset();
  invoke.mockResolvedValue(null);
  stage.holograms.length = 0;
  stage.failConstruction = false;
});

const CONTEXT = { mode: "async", waited: false };
const settle = (): Promise<void> => new Promise((resolve) => { setTimeout(resolve, 0); });
const app = (): HTMLElement => document.querySelector<HTMLElement>("#app")!;
const text = (id: string): string => document.getElementById(id)?.textContent ?? "";
const choice = (name: "fast" | "deep"): HTMLButtonElement =>
  document.querySelector<HTMLButtonElement>(`button[data-model-choice="${name}"]`)!;
const selection = (): Array<[string | null, boolean]> => (["fast", "deep"] as const).map((name) => [
  choice(name).getAttribute("aria-pressed"),
  choice(name).classList.contains("selection"),
]);

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function snapshotOf(overrides: Record<string, unknown> = {}): RuntimeSnapshot {
  return parseRuntimeSnapshot({
    available: true,
    rooms: [{ chat_id: 417, title: "임원방", live: true }],
    available_chats: [{ chat_id: 417, title: "임원방" }, { chat_id: 998, title: "새 방" }],
    jobs: [],
    context_sync: CONTEXT,
    ...overrides,
  });
}

const GRAPH = {
  nodes: [
    { id: "person:a", label: "연우", importance: 90 },
    { id: "term:b", label: "알쿠", importance: 70 },
    { id: "org:c", label: "클라우드", importance: 60 },
    { id: "doc:d", label: "정책", importance: 50 },
    { id: "lone:e", label: "외톨이", importance: 10, evidence: { chat_id: "555" } },
  ],
  edges: [
    { source: "person:a", relation: "MENTIONS", target: "term:b", weight: 3, room_id: "417" },
    { source: "term:b", relation: "member-of", target: "org:c", weight: 2 },
    { source: "org:c", relation: "related to", target: "doc:d", weight: 1, room_id: "000" },
    { source: "doc:d", relation: "HAS_POLICY", target: "person:a", weight: 0.5, evidence: { chat_id: "417" } },
  ],
};

interface BootOptions {
  snapshot?: RuntimeSnapshot | Error;
  status?: Record<string, unknown> | null | Error;
  graph?: Record<string, unknown> | null | Error;
  roomUpsert?: () => Promise<Record<string, unknown> | null>;
  wireVoice?: (root: ParentNode, invokeFn: unknown) => void;
}

async function boot(options: BootOptions = {}) {
  const answer = <T,>(value: T | Error): Promise<T> =>
    value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
  const loadSnapshot = vi.fn((_token: CancellationToken) => answer(options.snapshot ?? snapshotOf()));
  const loadAction = vi.fn((action: string, _input?: Record<string, unknown>) => {
    if (action === "knowledge-graph-status") return answer(options.status === undefined ? { stale: false } : options.status);
    if (action === "knowledge-graph") return answer(options.graph === undefined ? null : options.graph);
    if (action === "room-upsert" && options.roomUpsert) return options.roomUpsert();
    return Promise.resolve(null);
  });
  const wireVoice = vi.fn(options.wireVoice ?? (() => undefined));
  const invokeCommand = vi.fn();
  await main.bootSettings({
    loadSnapshot,
    loadAction: loadAction as never,
    wireVoice,
    invokeCommand: invokeCommand as never,
    subscribeVisibility: null,
    readVisibility: null,
  });
  return { loadSnapshot, loadAction, wireVoice, invokeCommand };
}

describe("answer model choice", () => {
  it.each([
    [null, [["true", true], ["false", false]], "빠른 대화가 선택되어 있습니다."],
    [`mlx/${SWAP_MODEL_ID}`, [["false", false], ["true", true]], "깊은 분석이 선택되어 있습니다."],
    ["someone/else", [["false", false], ["false", false]], "AI 답변 설정을 확인할 수 없습니다."],
  ])("shows the model the runtime reports (%s)", async (replyModelId, expected, status) => {
    await boot({ snapshot: snapshotOf({ reply_model_id: replyModelId }) });

    expect(selection()).toEqual(expected);
    expect(text("model-status")).toBe(status);
    expect([choice("fast").disabled, choice("deep").disabled]).toEqual([false, false]);
  });

  it("locks both choices while the fast model is being saved, then selects it", async () => {
    await boot({ snapshot: snapshotOf({ reply_model_id: SWAP_MODEL_ID }) });
    const saved = deferred<unknown>();
    invoke.mockReturnValueOnce(saved.promise);

    choice("fast").click();

    expect(invoke.mock.calls).toEqual([["fetch_settings_action", { action: "model-set", model: RESIDENT_MODEL_ID }]]);
    expect([choice("fast").disabled, choice("deep").disabled]).toEqual([true, true]);
    expect(choice("fast").getAttribute("aria-busy")).toBe("true");
    expect(text("model-status")).toBe("빠른 대화를 선택하고 있습니다…");

    saved.resolve({ ok: true, action: "model-set", model: RESIDENT_MODEL_ID, stored: true });
    await settle();

    expect([choice("fast").disabled, choice("deep").disabled]).toEqual([false, false]);
    expect(choice("fast").hasAttribute("aria-busy")).toBe(false);
    expect(selection()).toEqual([["true", true], ["false", false]]);
    expect(text("model-status")).toBe("빠른 대화가 선택되어 있습니다.");
  });

  it("keeps the previous choice and marks the row when the fast model cannot be saved", async () => {
    await boot({ snapshot: snapshotOf({ reply_model_id: SWAP_MODEL_ID }) });
    invoke.mockResolvedValueOnce({ ok: false });

    choice("fast").click();
    await settle();

    expect(selection()).toEqual([["false", false], ["true", true]]);
    expect(choice("fast").classList.contains("model-failed")).toBe(true);
    expect(text("model-status")).toBe("빠른 대화를 선택하지 못했습니다. 기존 설정을 유지했습니다.");

    invoke.mockResolvedValueOnce({ ok: true, action: "model-set", model: RESIDENT_MODEL_ID, stored: true });
    choice("fast").click();
    await settle();
    expect(choice("fast").classList.contains("model-failed")).toBe(false);
  });

  it("asks for the deep model with an explicit opt-in and selects it once ready", async () => {
    await boot();
    const swapped = deferred<unknown>();
    invoke.mockReturnValueOnce(swapped.promise);

    choice("deep").click();

    const [command, args] = invoke.mock.calls[0];
    expect(command).toBe("fetch_settings_action");
    expect(args).toEqual({ action: "model-swap", model: SWAP_MODEL_ID, explicitOptIn: true, tokenId: expect.any(String) });
    expect([choice("fast").disabled, choice("deep").disabled]).toEqual([true, true]);
    expect(choice("deep").getAttribute("aria-busy")).toBe("true");
    expect(text("model-status")).toBe("깊은 분석을 준비하고 있습니다…");

    swapped.resolve({
      ok: true, action: "model-swap", model: SWAP_MODEL_ID, stage: "ready", reason: "ready", stored: true, prepared: true,
    });
    await settle();

    expect([choice("fast").disabled, choice("deep").disabled]).toEqual([false, false]);
    expect(choice("deep").hasAttribute("aria-busy")).toBe(false);
    expect(selection()).toEqual([["false", false], ["true", true]]);
    expect(text("model-status")).toBe("깊은 분석을 사용할 준비가 되었습니다.");
  });

  it("explains a refused deep-model change and leaves the choice alone", async () => {
    await boot();
    invoke.mockResolvedValueOnce({
      ok: false, action: "model-swap", model: SWAP_MODEL_ID, stage: "failed", reason: "model_swap_busy",
    });

    choice("deep").click();
    await settle();

    expect(selection()).toEqual([["true", true], ["false", false]]);
    expect(choice("deep").classList.contains("model-failed")).toBe(true);
    expect(text("model-status")).toBe("다른 설정 변경이 끝난 뒤 다시 시도해 주세요.");
  });

  it("cancels an unfinished deep-model change when the window closes", async () => {
    await boot();
    const swapped = deferred<unknown>();
    invoke.mockReturnValueOnce(swapped.promise);
    choice("deep").click();
    const tokenId = invoke.mock.calls[0][1]?.tokenId;

    window.dispatchEvent(new Event("pagehide"));
    await settle();

    expect(invoke.mock.calls.filter(([command]) => command === "cancel_model_swap")).toEqual([
      ["cancel_model_swap", { tokenId }],
    ]);
    swapped.resolve(null);
    await settle();
  });

  it.each([
    ["model_residency_uncertain", "AI 설정 변경을 확인하지 못했습니다. 현재 설정을 유지했습니다."],
    ["model_swap_busy", "다른 설정 변경이 끝난 뒤 다시 시도해 주세요."],
    ["cancelled", "요청을 취소했습니다. 기존 설정을 유지했습니다."],
    ["memory_budget_unavailable", "안전하게 사용할 수 있는 메모리가 부족해 설정을 바꾸지 않았습니다."],
    ["model_owner_state_invalid", "AI 실행 상태를 확인하지 못해 기존 설정을 유지했습니다."],
    ["model_owner_state_stale", "AI 실행 상태를 확인하지 못해 기존 설정을 유지했습니다."],
    ["load_failed_rollback_failed", "AI 설정을 바꾸지 못했습니다. 현재 상태를 확인한 뒤 다시 시도해 주세요."],
    ["insufficient_free_memory_rollback_failed", "AI 설정을 바꾸지 못했습니다. 현재 상태를 확인한 뒤 다시 시도해 주세요."],
    ["load_failed", "깊은 분석을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요."],
    ["probe_failed", "깊은 분석을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요."],
    ["unload_failed", "깊은 분석을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요."],
    ["drain_timeout", "깊은 분석을 준비하지 못했습니다. 기존 설정을 유지했습니다."],
  ])("words the %s refusal for the user", (reason, message) => {
    expect(main.modelSwapFailureText(reason)).toBe(message);
  });
});

describe("voice status line", () => {
  it.each([
    ["idle", "마이크가 대기 중입니다."],
    ["wake_listen", "호출어를 기다리고 있습니다."],
    ["user_listen", "말씀을 듣고 있습니다."],
    ["transcribing", "말씀을 정리하고 있습니다."],
    ["generating", "답변을 준비하고 있습니다."],
    ["speaking", "답변을 말하고 있습니다."],
    ["ended", "음성 대화가 끝났습니다."],
    ["aborted", "음성 대화가 끝났습니다."],
    ["error", "음성 기능을 사용할 수 없습니다."],
    ["listening", "음성 상태를 확인하고 있습니다."],
  ])("describes the %s state", async (state, message) => {
    await boot({ snapshot: snapshotOf({ voice: { available: true, state } }) });
    expect(text("voice-status")).toBe(message);
  });

  it("prefers the error explanation and admits when voice is unreachable", async () => {
    await boot({ snapshot: snapshotOf({ voice: { available: true, state: "speaking", error_code: "tts_error" } }) });
    expect(text("voice-status")).toBe("답변을 소리로 들려주지 못했습니다.");

    await boot({ snapshot: snapshotOf({ voice: { available: false, state: "speaking" } }) });
    expect(text("voice-status")).toBe("음성 상태를 확인할 수 없습니다.");
  });
});

describe("adding a chat room", () => {
  const addButton = (): HTMLButtonElement => document.querySelector<HTMLButtonElement>("#settings-add-room-button")!;
  const pick = (value: string): void => {
    document.querySelector<HTMLSelectElement>("#settings-add-room-select")!.value = value;
  };

  it("does nothing until a room is picked", async () => {
    const { loadAction } = await boot();
    addButton().click();
    await settle();
    expect(loadAction.mock.calls.map(([action]) => action)).toEqual(["knowledge-graph-status", "knowledge-graph"]);
    expect(text("room-summary")).toBe("등록된 채팅방 1개 · 연결됨 1개");
  });

  it("registers the picked room and redraws the list from a fresh snapshot", async () => {
    const upsert = deferred<Record<string, unknown> | null>();
    const { loadAction, loadSnapshot } = await boot({ roomUpsert: () => upsert.promise });
    loadSnapshot.mockResolvedValueOnce(snapshotOf({
      rooms: [{ chat_id: 417, title: "임원방", live: true }, { chat_id: 998, title: "새 방" }],
    }));
    pick("998");

    addButton().click();

    expect(loadAction.mock.calls.at(-1)).toEqual(["room-upsert", { chatId: "998", query: "새 방" }]);
    expect(addButton().disabled).toBe(true);
    expect(text("room-summary")).toBe("채팅방을 등록하는 중...");

    upsert.resolve({ ok: true });
    await settle();

    expect(loadSnapshot).toHaveBeenCalledTimes(2);
    expect(addButton().disabled).toBe(false);
    expect(text("room-summary")).toBe("등록된 채팅방 2개 · 연결됨 1개");
    expect([...document.querySelectorAll<HTMLOptionElement>("#settings-add-room-select option")].map((option) => option.value))
      .toEqual([""]);
  });

  it("reports a refused or failed registration without reloading", async () => {
    const refused = await boot({ roomUpsert: async () => ({ ok: false }) });
    pick("998");
    addButton().click();
    await settle();
    expect(text("room-summary")).toBe("채팅방을 등록하지 못했습니다. 확인 후 다시 시도해 주세요.");
    expect(refused.loadSnapshot).toHaveBeenCalledTimes(1);
    expect(addButton().disabled).toBe(false);

    await boot({ roomUpsert: () => Promise.reject(new Error("bridge")) });
    pick("998");
    addButton().click();
    await settle();
    expect(text("room-summary")).toBe("채팅방 등록 중 오류가 발생했습니다.");
    expect(addButton().disabled).toBe(false);
  });
});

describe("conversation graph", () => {
  const relations = (): string[][] => [...document.querySelectorAll("#knowledge-relations .knowledge-relation-row")]
    .map((row) => [row.querySelector("strong")?.textContent ?? "", row.querySelector("span")?.textContent ?? ""]);
  const nodeButtons = (): HTMLButtonElement[] =>
    [...document.querySelectorAll<HTMLButtonElement>("#knowledge-accessible-nodes .knowledge-a11y-node")];
  const expand = (): HTMLButtonElement => document.querySelector<HTMLButtonElement>("#knowledge-expand-hop")!;
  const focusCalls = (): unknown[] => invoke.mock.calls.filter(([, args]) => args?.action === "knowledge-graph-focus");

  it("summarises the graph and lists its nodes for assistive technology", async () => {
    await boot({ graph: GRAPH });

    expect(app().dataset.state).toBe("ready");
    expect(text("knowledge-summary")).toBe("대화에서 찾은 연결 항목 5개");
    expect(text("knowledge-mode")).toBe("연결된 주제");
    expect(text("settings-sync-source")).toBe("대화 자료가 준비되었습니다.");
    expect(nodeButtons().map((button) => [button.type, button.dataset.nodeId, button.textContent, button.getAttribute("aria-label")]))
      .toEqual(GRAPH.nodes.map((node) => ["button", node.id, node.label, `${node.label} 선택`]));
    expect(stage.holograms).toHaveLength(1);
    expect(stage.holograms[0].starts).toBe(1);
    expect((window as unknown as { __knowledgeRenderCount: number }).__knowledgeRenderCount).toBe(7);
    expect(expand().disabled).toBe(true);
  });

  it("flags stale material and an unreadable status separately", async () => {
    await boot({ graph: { ...GRAPH, stale: true }, status: { stale: true } });
    expect(text("knowledge-mode")).toBe("자료 확인 필요");
    expect(text("settings-sync-source")).toBe("대화 자료를 새로 확인해야 합니다.");
    expect(app().dataset.state).toBe("ready");

    await boot({ graph: GRAPH, status: null });
    expect(text("settings-sync-source")).toBe("대화 준비 상태를 확인하지 못했습니다.");
    expect(app().dataset.state).toBe("ready");

    await boot({ graph: GRAPH, status: new Error("bridge") });
    expect(text("settings-sync-source")).toBe("대화 준비 상태를 확인하지 못했습니다.");
    expect(app().dataset.state).toBe("unavailable");
  });

  it("says so when there is nothing to draw", async () => {
    await boot({ graph: { nodes: [], edges: [] } });
    expect(text("knowledge-summary")).toBe("아직 연결된 대화가 없습니다.");
    expect(text("knowledge-mode")).toBe("준비 중");
    expect(stage.holograms).toHaveLength(0);
    expect(nodeButtons()).toHaveLength(0);
    expect(app().dataset.state).toBe("ready");
  });

  it("focuses a node, lists its strongest relations and looks up related talk", async () => {
    await boot({ graph: GRAPH });
    const lookup = deferred<unknown>();
    invoke.mockReturnValueOnce(lookup.promise);

    nodeButtons()[0].click();

    expect(text("knowledge-focus-title")).toBe("연우");
    expect(expand().disabled).toBe(false);
    expect(relations()).toEqual([
      ["연우 —언급→ 알쿠", "대화방 · 임원방"],
      ["정책 —연결→ 연우", "대화방 · 임원방"],
    ]);
    expect(text("knowledge-retrieve")).toBe("관련 대화를 찾고 있습니다…");
    expect(invoke.mock.calls).toEqual([["fetch_settings_action", {
      action: "knowledge-graph-focus",
      query: "연우",
      nodeId: "person:a",
      chatId: "417",
      model: undefined,
      explicitOptIn: undefined,
    }]]);

    lookup.resolve({ ok: true, facts: ["첫 사실".padEnd(200, "!"), 7, "둘째"] });
    await settle();
    expect(text("knowledge-retrieve")).toBe(`관련 정보 2건을 찾았습니다. ${"첫 사실".padEnd(180, "!")}`);
  });

  it("names relation kinds and falls back when the room is not enrolled", async () => {
    await boot({ graph: GRAPH });

    nodeButtons()[2].click();
    expect(relations()).toEqual([
      ["알쿠 —소속→ 클라우드", "대화에서 확인한 관계"],
      ["클라우드 —관련→ 정책", "대화에서 확인한 관계"],
    ]);

    nodeButtons()[4].click();
    expect(text("knowledge-focus-title")).toBe("외톨이");
    expect(document.querySelector("#knowledge-relations .knowledge-empty")?.textContent).toBe("아직 연결된 항목이 없습니다.");
    expect(focusCalls().map((call) => (call as [string, { chatId: string }])[1].chatId)).toEqual(["417", "555"]);
  });

  it.each([
    ["PARTICIPATES", "참여"],
    ["participates in", "참여"],
    ["MEMBER_OF", "소속"],
    ["occurred-in", "발생"],
    ["ABOUT", "관련"],
    ["", "연결"],
  ])("labels the %s relation", async (relation, label) => {
    await boot({
      graph: {
        nodes: [{ id: "a", label: "가" }, { id: "b", label: "나" }],
        edges: [{ source: "a", relation, target: "b", weight: 1 }],
      },
    });
    nodeButtons()[0].click();
    expect(relations()).toEqual([[`가 —${label}→ 나`, "대화에서 확인한 관계"]]);
  });

  it("reports an empty or failed lookup and ignores an answer for a node no longer in focus", async () => {
    await boot({ graph: GRAPH });
    const first = deferred<unknown>();
    invoke.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ ok: true, facts: [] });

    nodeButtons()[0].click();
    nodeButtons()[1].click();
    await settle();
    expect(text("knowledge-retrieve")).toBe("관련 대화를 찾지 못했습니다.");

    first.resolve({ ok: true, facts: ["늦은 답"] });
    await settle();
    expect(text("knowledge-focus-title")).toBe("알쿠");
    expect(text("knowledge-retrieve")).toBe("관련 대화를 찾지 못했습니다.");

    invoke.mockResolvedValueOnce({ ok: false, facts: ["숨김"] });
    nodeButtons()[3].click();
    await settle();
    expect(text("knowledge-retrieve")).toBe("관련 대화를 찾지 못했습니다.");

    invoke.mockRejectedValueOnce(new Error("bridge"));
    nodeButtons()[0].click();
    await settle();
    expect(text("knowledge-retrieve")).toBe("관련 대화를 찾지 못했습니다.");
  });

  it("widens the focus one step and stops offering more at the limit", async () => {
    const chain = {
      nodes: ["a", "b", "c", "d", "e"].map((id) => ({ id, label: id.toUpperCase() })),
      edges: [["a", "b"], ["b", "c"], ["c", "d"], ["d", "e"]]
        .map(([source, target], index) => ({ source, target, relation: "ABOUT", weight: 4 - index })),
    };
    await boot({ graph: chain });

    expand().click();
    expect(relations()).toEqual([]);

    nodeButtons()[1].click();
    expect(relations()).toEqual([
      ["A —관련→ B", "대화에서 확인한 관계"],
      ["B —관련→ C", "대화에서 확인한 관계"],
    ]);
    expect(expand().disabled).toBe(false);

    document.getElementById("knowledge-relations")?.replaceChildren();
    expand().click();
    expect(expand().disabled).toBe(true);
    expect(relations()).toHaveLength(2);
    expect(focusCalls()).toHaveLength(1);
  });

  it("stops and releases the graph when the window closes", async () => {
    await boot({ graph: GRAPH });
    const hologram = stage.holograms[0];

    window.dispatchEvent(new Event("pagehide"));

    expect([hologram.stops, hologram.disposals]).toEqual([1, 1]);
    window.dispatchEvent(new Event("focus"));
    expect(hologram.starts).toBe(1);
  });
});

describe("settings window that cannot start", () => {
  const unavailableTexts = (): Record<string, string> => Object.fromEntries([
    "room-summary", "model-status", "voice-status", "settings-sync-source", "settings-activity-source",
    "knowledge-summary", "knowledge-mode", "history-summary",
  ].map((id) => [id, text(id)]));
  const FIXED = {
    "room-summary": "채팅방 목록을 불러오지 못했습니다. 다시 확인해 주세요.",
    "model-status": "AI 답변 설정을 확인할 수 없습니다.",
    "voice-status": "음성 상태를 확인할 수 없습니다.",
    "settings-sync-source": "대화 준비 상태를 확인할 수 없습니다.",
    "settings-activity-source": "앱의 작업 상태를 불러오지 못했습니다.",
    "knowledge-summary": "대화에서 찾은 연결 정보를 확인할 수 없습니다.",
    "knowledge-mode": "확인 필요",
    "history-summary": "최근 답변 기록을 불러오지 못했습니다.",
  };
  const allDisabled = (): boolean[] => [...document.querySelectorAll<HTMLButtonElement>("button")].map((button) => button.disabled);

  it("keeps working with what loaded when only the snapshot is missing", async () => {
    await boot({ snapshot: new Error("bridge"), graph: GRAPH });

    expect(app().dataset.state).toBe("unavailable");
    expect(text("room-summary")).toBe("채팅방 목록을 불러오지 못했습니다. 다시 확인해 주세요.");
    expect(text("model-status")).toBe("빠른 대화가 선택되어 있습니다.");
    expect(text("knowledge-summary")).toBe("대화에서 찾은 연결 항목 5개");
    expect(choice("fast").disabled).toBe(false);
  });

  it("shows the fixed unavailable screen when wiring fails", async () => {
    await boot({ graph: GRAPH, wireVoice: () => { throw new Error("no voice bridge"); } });

    expect(app().dataset.state).toBe("unavailable");
    expect(unavailableTexts()).toEqual(FIXED);
    expect(allDisabled()).toEqual([true, true, true, true, true]);
    expect(selection()).toEqual([["true", true], ["false", false]]);
  });

  it("shows the same screen when the graph renderer cannot be created", async () => {
    stage.failConstruction = true;
    const { wireVoice, invokeCommand } = await boot({ graph: GRAPH });

    expect(wireVoice).toHaveBeenCalledWith(document, invokeCommand);
    expect(app().dataset.state).toBe("unavailable");
    expect(unavailableTexts()).toEqual(FIXED);
    expect(allDisabled()).toEqual([true, true, true, true, true]);
  });

  it("falls back to the unavailable screen of the view that failed to boot", async () => {
    const failing = { panel: () => Promise.reject(new Error("panel")), settings: () => Promise.reject(new Error("settings")) };

    await main.startDesktopApp(true, failing);
    expect(app().dataset.state).toBe("unavailable");
    expect(unavailableTexts()).toEqual(FIXED);

    await main.startDesktopApp(false, failing);
    expect(app().dataset.state).toBe("unavailable");
    expect(text("panel-status")).toBe("Jarvis 상태를 확인할 수 없습니다.");
    expect(document.querySelector(".settings-shell")).toBeNull();
  });
});

describe("menu-bar panel boot", () => {
  class ManualTimers implements PollTimerScheduler {
    readonly pending = new Map<number, () => void>();
    private nextId = 1;
    setTimeout(callback: () => void): number {
      this.pending.set(this.nextId, callback);
      return this.nextId++;
    }
    clearTimeout(timerId: number): void { this.pending.delete(timerId); }
  }

  function fakeCore() {
    return { renderCount: 3, start: vi.fn(), stop: vi.fn(), setSignals: vi.fn(), dispose: vi.fn() };
  }

  it("draws only the core, starts it and feeds it the polled load", async () => {
    const core = fakeCore();
    const timers = new ManualTimers();
    const loadSnapshot = vi.fn(async () => snapshotOf({ job_load: 0.4, voice: { available: true, rms: 0.2 } }));
    const cancelSnapshot = vi.fn(async () => undefined);
    const canvases: HTMLCanvasElement[] = [];

    await main.bootPanel({
      createCore: (canvas) => { canvases.push(canvas); return core; },
      loadSnapshot,
      cancelSnapshot,
      makeToken: () => ({ id: "token-1", cancelled: false }),
      pollScheduler: timers,
      subscribeVisibility: null,
      readVisibility: null,
    });
    await settle();

    expect(app().innerHTML).toBe(mainPanelMarkup());
    expect(app().dataset.state).toBe("ready");
    expect(canvases).toEqual([document.querySelector(".jarvis-core")]);
    expect((window as unknown as { __jarvisRenderCount: number }).__jarvisRenderCount).toBe(3);
    expect(core.start).toHaveBeenCalledTimes(1);
    expect(loadSnapshot).toHaveBeenCalledTimes(1);
    expect(core.setSignals.mock.calls).toEqual([[0.4, 0.2]]);
    expect(timers.pending.size).toBe(1);

    window.dispatchEvent(new Event("pagehide"));

    expect(core.stop).toHaveBeenCalled();
    expect(core.dispose).toHaveBeenCalledTimes(1);
    expect(timers.pending.size).toBe(0);
    window.dispatchEvent(new Event("focus"));
    expect(core.start).toHaveBeenCalledTimes(1);
    expect(cancelSnapshot).not.toHaveBeenCalled();
  });

  it("explains itself in place when the renderer cannot be created", async () => {
    await main.bootPanel({
      createCore: () => Promise.reject(new Error("webgl_unavailable")),
      loadSnapshot: async () => unavailableSnapshot(),
      subscribeVisibility: null,
      readVisibility: null,
    });

    expect(app().dataset.state).toBe("unavailable");
    expect(document.querySelector(".jarvis-core")?.getAttribute("aria-label")).toBe("Jarvis core 확인 불가");
    const status = document.getElementById("panel-status");
    expect(status?.textContent).toBe("Jarvis 상태를 확인할 수 없습니다.");
    expect([status?.tagName, status?.getAttribute("role"), status?.getAttribute("aria-live")]).toEqual(["P", "status", "polite"]);
    expect(status?.parentElement?.className).toBe("jarvis-panel");
    expect(status?.getAttribute("style")).toBe(
      "position: absolute; left: 12px; right: 12px; bottom: 9px; margin: 0px; text-align: center; font-size: 10px; line-height: 1.35; color: var(--muted); pointer-events: none;",
    );
  });
});
