// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { parseRuntimeSnapshot, unavailableSnapshot, type RuntimeSnapshot } from "../contracts";
import { renderBackground, renderHistory, renderRooms, settingsMarkup } from "../ui";

const CONTEXT = { mode: "async", waited: false };
const UNSET: Pick<RuntimeSnapshot, "background" | "onDevice" | "pipeline"> = {
  background: {
    activity: 0,
    caption: "",
    replyLoad: 0,
    geeknews: { state: "unknown", activity: 0, caption: "" },
    dbSync: { state: "unknown", activity: 0, caption: "" },
  },
  onDevice: unavailableSnapshot().onDevice,
  pipeline: { active: false, stage: "none", stageIndex: 0, stageTotal: 0, outcome: "unknown" },
};

describe("runtime snapshot boundary", () => {
  it("parses the snake_case bridge payload field by field", () => {
    const snapshot = parseRuntimeSnapshot({
      available: true,
      rooms: [
        { chat_id: 11, title: "가", live: true, auto_reply: true, open_jobs: 2.9 },
        { chat_id: 12 },
        { chat_id: 0, title: "zero" },
        { chat_id: 1.5 },
        "room",
        null,
      ],
      available_chats: [
        { chat_id: 21, title: "나", catalog: true, live: true },
        { chat_id: 22 },
        { chat_id: -1 },
        7,
      ],
      jobs: [
        { jobId: "j1", kind: "reply", stage: "model", load: 4, time: 5, errorCode: null },
        { jobId: "incomplete" },
      ],
      recent_receipts: [
        {
          chatId: 31, title: "다", displayTime: "09-21 16:00", clock: "16:00", outcome: "deferred",
          outcome_text: "나중에", reasonCode: "uncertain", reasonText: "보류", retrievalState: "empty",
        },
        {
          chatId: 32, title: "라", displayTime: "", clock: "", outcome: "sent",
          reasonCode: "Bad Code", reasonText: "", retrievalState: "weird",
        },
        { chatId: 33, title: "마", displayTime: "", clock: "", outcome: "failed", reasonCode: "x", reasonText: "" },
        { chatId: 0, title: "바", displayTime: "", clock: "", outcome: "sent", reasonCode: "x", reasonText: "" },
        { chatId: 34, title: "사", displayTime: "", clock: "", outcome: "sent", reasonCode: "x" },
      ],
      job_load: 1.7,
      terminal_counts: { sent: 3.9, skipped: -2, delivery_unknown: 4, burst_superseded: 5 },
      context_sync: CONTEXT,
      reply_model_id: "mlx/model",
      voice: {
        available: true, state: "speaking", rms: 3, error_code: "tts_error", wake_source: "custom",
        updated_at: 12.7, wake_phrase: "헤이 자비스", threshold: 0.99, custom_model_selected: true,
      },
      error_code: "runtime_note",
    });

    expect(snapshot).toEqual({
      available: true,
      rooms: [
        { chatId: 11, title: "가", live: true, autoReply: true, openJobs: 2 },
        { chatId: 12, title: "id:12", live: false, autoReply: false, openJobs: 0 },
      ],
      availableChats: [
        { chatId: 21, title: "나", catalog: true, live: true },
        { chatId: 22, title: "id:22", catalog: false, live: false },
      ],
      jobs: [{ jobId: "j1", kind: "reply", stage: "model", load: 1, time: 5, errorCode: null }],
      recentReceipts: [
        {
          chatId: 31, title: "다", displayTime: "09-21 16:00", clock: "16:00", outcome: "deferred",
          outcomeText: "나중에", reasonCode: "uncertain", reasonText: "보류", retrievalState: "empty",
        },
        {
          chatId: 32, title: "라", displayTime: "", clock: "", outcome: "sent",
          outcomeText: "", reasonCode: "unspecified", reasonText: "", retrievalState: "unrecorded",
        },
      ],
      jobLoad: 1,
      ...UNSET,
      terminal: { sent: 3, skipped: 0, deliveryUnknown: 4, burstSuperseded: 5 },
      contextSync: CONTEXT,
      replyModelId: "mlx/model",
      voice: {
        available: true, state: "speaking", rms: 1, errorCode: "tts_error", wakeSource: "custom",
        updatedAt: 12, wakePhrase: "헤이 자비스", threshold: 0.95, customModelSelected: true,
      },
      errorCode: "runtime_note",
    });
  });

  it("accepts the camelCase spelling of the same fields", () => {
    const snapshot = parseRuntimeSnapshot({
      rooms: [{ chatId: 11, title: "가", live: true, autoReply: true, openJobs: 1 }],
      availableChats: [{ chatId: 21, title: "나", catalog: true, live: false }],
      jobs: "none",
      recentReceipts: [{
        chatId: 31, title: "다", displayTime: "t", clock: "c", outcome: "skipped",
        outcomeText: "건너뜀", reasonCode: "low_information", reasonText: "r", retrievalState: "index_not_ready",
      }],
      jobLoad: -3,
      terminal: { sent: 1, skipped: 2, deliveryUnknown: 3, burstSuperseded: 4 },
      contextSync: CONTEXT,
      replyModelId: "model",
      voice: {
        available: true, state: "idle", rms: 0.25, errorCode: "mic_unavailable", wakeSource: "stock",
        updatedAt: 9, wakePhrase: "자비스", threshold: 0.1, customModelSelected: true,
      },
      errorCode: "ignored_spelling",
    });

    expect(snapshot).toEqual({
      available: true,
      rooms: [{ chatId: 11, title: "가", live: true, autoReply: true, openJobs: 1 }],
      availableChats: [{ chatId: 21, title: "나", catalog: true, live: false }],
      jobs: [],
      recentReceipts: [{
        chatId: 31, title: "다", displayTime: "t", clock: "c", outcome: "skipped",
        outcomeText: "건너뜀", reasonCode: "low_information", reasonText: "r", retrievalState: "index_not_ready",
      }],
      jobLoad: 0,
      ...UNSET,
      terminal: { sent: 1, skipped: 2, deliveryUnknown: 3, burstSuperseded: 4 },
      contextSync: CONTEXT,
      replyModelId: "model",
      voice: {
        available: true, state: "idle", rms: 0.25, errorCode: "mic_unavailable", wakeSource: "stock",
        updatedAt: 9, wakePhrase: "자비스", threshold: 0.65, customModelSelected: true,
      },
      errorCode: null,
    });
  });

  it("resolves a field given in both spellings the same way every time", () => {
    const snapshot = parseRuntimeSnapshot({
      rooms: [{ chat_id: 1, chatId: 2, open_jobs: 3, openJobs: 4 }],
      availableChats: "not-a-list",
      available_chats: [{ chat_id: 5 }],
      recent_receipts: "not-a-list",
      recentReceipts: [{
        chatId: 6, title: "t", displayTime: "", clock: "", outcome: "sent",
        outcomeText: 7, outcome_text: "snake", reasonCode: "ok", reasonText: "",
      }],
      job_load: 0.25,
      jobLoad: 0.75,
      terminal_counts: { delivery_unknown: 1, deliveryUnknown: 2 },
      terminal: { sent: 9 },
      context_sync: CONTEXT,
      contextSync: { mode: "sync", waited: true },
      reply_model_id: 7,
      replyModelId: "camel",
      voice: {
        error_code: 5, errorCode: "camel", wake_source: "custom", wakeSource: "stock",
        updated_at: 1, updatedAt: 2, wake_phrase: "snake", wakePhrase: "camel",
      },
    });

    expect(snapshot.rooms).toEqual([{ chatId: 1, title: "id:1", live: false, autoReply: false, openJobs: 3 }]);
    expect(snapshot.availableChats).toEqual([]);
    expect(snapshot.recentReceipts.map((receipt) => receipt.outcomeText)).toEqual(["snake"]);
    expect(snapshot.jobLoad).toBe(0.25);
    expect(snapshot.terminal).toEqual({ sent: 0, skipped: 0, deliveryUnknown: 1, burstSuperseded: 0 });
    expect(snapshot.available).toBe(true);
    expect(snapshot.replyModelId).toBe("camel");
    expect(snapshot.voice).toEqual({
      available: false, state: "unavailable", rms: 0, errorCode: "camel", wakeSource: "stock",
      updatedAt: 1, wakePhrase: "snake", threshold: 0.65, customModelSelected: false,
    });
  });

  it("marks a snapshot that waited on context sync as unavailable", () => {
    const snapshot = parseRuntimeSnapshot({ context_sync: { mode: "async", waited: true }, error_code: "x" });
    expect(snapshot.available).toBe(false);
    expect(snapshot.errorCode).toBe("context_sync_invalid");
    expect(parseRuntimeSnapshot([])).toEqual(unavailableSnapshot("snapshot_invalid"));
  });
});

function withBackground(geeknews: string, dbSync: string): RuntimeSnapshot {
  return parseRuntimeSnapshot({
    context_sync: CONTEXT,
    background: {
      activity: 0.5,
      replyLoad: 0.6,
      geeknews: { state: geeknews, activity: 0.5 },
      db_sync: { state: dbSync, activity: 0.5 },
    },
  });
}

describe("settings labels", () => {
  it("names every background source state the bridge can report", () => {
    document.body.innerHTML = settingsMarkup();
    const label = (geeknews: string, dbSync: string): string => {
      renderBackground(withBackground(geeknews, dbSync));
      return document.getElementById("settings-activity-source")?.textContent ?? "";
    };

    expect(label("sending", "retrying")).toBe("작업 현황 · 답변 대기 2건 · 긱뉴스 전송 중 · 대화 준비 확인 중");
    expect(label("confirmed", "stalled")).toBe("작업 현황 · 답변 대기 2건 · 긱뉴스 확인 중 · 대화 준비 확인 중");
    expect(label("idle", "syncing")).toBe("작업 현황 · 답변 대기 2건 · 긱뉴스 확인 중 · 대화 준비 확인 중");
    expect(label("unknown", "behind")).toBe("작업 현황 · 답변 대기 2건 · 긱뉴스 확인 중 · 대화 준비 새로 확인 필요");
    expect(label("sending", "ready")).toBe("작업 현황 · 답변 대기 2건 · 긱뉴스 전송 중 · 대화 준비 완료");
    expect(label("ready", "sending")).toBe("작업 현황 · 답변 대기 2건 · 긱뉴스 확인 중 · 대화 준비 확인 중");
  });

  it("falls back to its own labels for each receipt outcome, reason and lookup state", () => {
    document.body.innerHTML = settingsMarkup();
    const rows: Array<[string, string, string, string, string]> = [
      ["sent", "direct_question", "ok", "답변 완료", "답변 완료 · 질문에 답변 · 대화 찾기 자료 확인됨"],
      ["deferred", "uncertain", "empty", "나중에 처리", "나중에 처리 · 판단을 보류한 대화 · 대화 찾기 관련 자료 없음"],
      ["scheduled", "low_information", "skipped", "예약됨", "예약됨 · 답변할 정보가 부족한 대화 · 대화 찾기 확인하지 않음"],
      ["skipped", "already_commented", "error", "건너뜀", "건너뜀 · 이미 답변한 대화 · 대화 찾기 자료를 확인하지 못함"],
      ["sent", "social_reply", "index_not_ready", "답변 완료", "답변 완료 · 사유가 기록되지 않았습니다 · 대화 찾기 자료 준비 중"],
      ["sent", "ok", "unrecorded", "답변 완료", "답변 완료 · 사유가 기록되지 않았습니다 · 대화 찾기 상태 확인 중"],
    ];
    renderHistory(parseRuntimeSnapshot({
      context_sync: CONTEXT,
      recent_receipts: rows.map(([outcome, reasonCode, retrievalState], index) => ({
        chatId: index + 1, title: "방", displayTime: index === 0 ? "09-21" : "", clock: index === 1 ? "16:00" : "",
        outcome, reasonCode, reasonText: "", retrievalState,
      })),
    }));

    const cards = [...document.querySelectorAll("#history-list .history-receipt-card")];
    expect(cards.map((card) => card.getAttribute("role"))).toEqual(rows.map(() => "listitem"));
    expect(cards.map((card) => card.querySelector(".history-receipt-badge")?.textContent)).toEqual(rows.map((row) => row[3]));
    expect(cards.map((card) => card.querySelector(".history-receipt-detail")?.textContent)).toEqual(rows.map((row) => row[4]));
    expect(cards.map((card) => card.querySelector(".history-receipt-title")?.textContent)).toEqual([
      "09-21 · 방", "16:00 · 방", "시간 미기록 · 방", "시간 미기록 · 방", "시간 미기록 · 방", "시간 미기록 · 방",
    ]);
    expect(document.getElementById("history-summary")?.textContent).toBe("최근 6건 · 메시지 내용은 표시하지 않습니다.");
  });

  it("explains an empty room list differently when the runtime is unreachable", () => {
    document.body.innerHTML = settingsMarkup();
    const texts = (): string[] => [
      document.querySelector("#settings-room-popup option")?.textContent ?? "",
      document.getElementById("room-summary")?.textContent ?? "",
      document.querySelector("#settings-add-room-select option")?.textContent ?? "",
    ];

    renderRooms(parseRuntimeSnapshot({ context_sync: CONTEXT }));
    expect(texts()).toEqual(["등록된 채팅방이 없습니다.", "등록된 채팅방이 없습니다.", "추가 가능한 새 채팅방 없음"]);

    renderRooms(unavailableSnapshot());
    expect(texts()).toEqual([
      "채팅방 목록을 불러오지 못했습니다.",
      "채팅방 목록을 불러오지 못했습니다. 다시 확인해 주세요.",
      "추가 가능한 새 채팅방 없음",
    ]);

    renderRooms(parseRuntimeSnapshot({
      context_sync: CONTEXT,
      rooms: [{ chat_id: 1, title: "가", live: true }, { chat_id: 2, title: "나" }],
      available_chats: [{ chat_id: 2, title: "나" }, { chat_id: 3, title: "다" }],
    }));
    expect(texts()).toEqual(["가", "등록된 채팅방 2개 · 연결됨 1개", "추가할 채팅방 선택"]);
    expect([...document.querySelectorAll<HTMLOptionElement>("#settings-add-room-select option")].map((option) => option.value))
      .toEqual(["", "3"]);
  });
});
