export interface JobEvent {
  jobId: string;
  kind: string;
  stage: string;
  load: number;
  time: number;
  errorCode: string | null;
}

export interface CancellationToken {
  id: string;
  cancelled: boolean;
}

export interface RoomSummary {
  chatId: number;
  title: string;
  live: boolean;
  autoReply: boolean;
  openJobs: number;
}

export interface AvailableChatSummary {
  chatId: number;
  title: string;
  catalog: boolean;
  live: boolean;
}

export type BackgroundSourceState =
  | "sending"
  | "confirmed"
  | "idle"
  | "retrying"
  | "stalled"
  | "syncing"
  | "behind"
  | "ready"
  | "unknown";

export interface BackgroundSource {
  state: BackgroundSourceState;
  activity: number;
  caption: string;
}

export interface BackgroundStatus {
  activity: number;
  caption: string;
  replyLoad: number;
  geeknews: BackgroundSource;
  dbSync: BackgroundSource;
}

export interface OnDeviceStatus {
  available: boolean;
  chip: string;
  cores: number;
  memoryGb: number;
  appleSilicon: boolean;
  engine: string;
  recommendedModel: string;
  quant: string;
  verified: boolean;
  statusLabel: string;
  statusDetail: string;
}

export type PipelineStage =
  | "detect"
  | "authorize"
  | "queue"
  | "context"
  | "model"
  | "delay"
  | "send"
  | "confirm"
  | "none"
  | "unknown";

export type PipelineOutcome =
  | "none"
  | "sent"
  | "skipped"
  | "deferred"
  | "scheduled"
  | "failed"
  | "aborted"
  | "unknown";

export interface PipelineStatus {
  active: boolean;
  stage: PipelineStage;
  stageIndex: number;
  stageTotal: number;
  outcome: PipelineOutcome;
}

export interface RuntimeSnapshot {
  available: boolean;
  rooms: RoomSummary[];
  availableChats: AvailableChatSummary[];
  jobs: JobEvent[];
  recentReceipts: RecentReceipt[];
  jobLoad: number;
  background: BackgroundStatus;
  onDevice: OnDeviceStatus;
  pipeline: PipelineStatus;
  terminal: {
    sent: number;
    skipped: number;
    deliveryUnknown: number;
    burstSuperseded: number;
  };
  contextSync: {
    mode: "async";
    waited: false;
  };
  replyModelId: string | null;
  voice: VoiceStatus;
  errorCode: string | null;
}

export interface RecentReceipt {
  chatId: number;
  title: string;
  displayTime: string;
  clock: string;
  outcome: "sent" | "deferred" | "scheduled" | "skipped";
  outcomeText: string;
  reasonCode: string;
  reasonText: string;
  retrievalState: "ok" | "empty" | "skipped" | "error" | "index_not_ready" | "unrecorded";
}

export interface VoiceStatus {
  available: boolean;
  state: string;
  rms: number;
  errorCode: string | null;
  wakeSource: "stock" | "custom" | "none";
  updatedAt: number;
  wakePhrase: string;
  threshold: number;
  customModelSelected: boolean;
}

type JsonRecord = Record<string, unknown>;
const JOB_EVENT_KEYS = new Set(["jobId", "kind", "stage", "load", "time", "errorCode"]);
const RECEIPT_OUTCOMES = new Set<RecentReceipt["outcome"]>(["sent", "deferred", "scheduled", "skipped"]);
const RECEIPT_REASON_SLUG = /^[a-z][a-z0-9_]{0,63}$/;
const RETRIEVAL_STATES = new Set<RecentReceipt["retrievalState"]>([
  "ok", "empty", "skipped", "error", "index_not_ready", "unrecorded",
]);
const GEEKNEWS_BACKGROUND_STATES = new Set<BackgroundSourceState>(["sending", "confirmed", "idle", "unknown"]);
const DB_SYNC_BACKGROUND_STATES = new Set<BackgroundSourceState>(["retrying", "stalled", "syncing", "behind", "ready", "unknown"]);
const PIPELINE_STAGES = new Set<PipelineStage>([
  "detect", "authorize", "queue", "context", "model", "delay", "send", "confirm",
]);
const PIPELINE_OUTCOMES = new Set<PipelineOutcome>([
  "none", "sent", "skipped", "deferred", "scheduled", "failed", "aborted",
]);

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clamp01(value: unknown): number {
  return Math.min(1, Math.max(0, finiteNumber(value, 0)));
}

function roundActivity(value: unknown): number {
  return Math.round(clamp01(value) * 1000) / 1000;
}

function nonNegativeInt(value: unknown): number {
  return Math.max(0, Math.trunc(finiteNumber(value, 0)));
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** The first candidate that is a string; the bridge sends some fields in two spellings. */
function firstString(...candidates: unknown[]): string | null {
  for (const candidate of candidates) {
    if (typeof candidate === "string") return candidate;
  }
  return null;
}

function member<T extends string>(allowed: ReadonlySet<T>, value: unknown): T | null {
  return typeof value === "string" && allowed.has(value as T) ? (value as T) : null;
}

function positiveChatId(value: unknown): number | null {
  const chatId = finiteNumber(value, -1);
  return Number.isInteger(chatId) && chatId > 0 ? chatId : null;
}

function parseBackgroundSource(value: unknown, allowed: ReadonlySet<BackgroundSourceState>): BackgroundSource {
  const source = record(value);
  return {
    state: member(allowed, source?.state) ?? "unknown",
    activity: roundActivity(source?.activity),
    caption: text(source?.caption).slice(0, 120),
  };
}

export function parseBackground(value: unknown): BackgroundStatus {
  const background = record(value);
  return {
    activity: roundActivity(background?.activity),
    caption: text(background?.caption).slice(0, 120),
    replyLoad: roundActivity(background?.replyLoad ?? background?.reply_load),
    geeknews: parseBackgroundSource(background?.geeknews, GEEKNEWS_BACKGROUND_STATES),
    dbSync: parseBackgroundSource(background?.dbSync ?? background?.db_sync, DB_SYNC_BACKGROUND_STATES),
  };
}

function unavailableOnDevice(): OnDeviceStatus {
  return {
    available: false,
    chip: "",
    cores: 0,
    memoryGb: 0,
    appleSilicon: false,
    engine: "",
    recommendedModel: "",
    quant: "",
    verified: false,
    statusLabel: "",
    statusDetail: "",
  };
}

export function parseOnDevice(value: unknown): OnDeviceStatus {
  const input = record(value);
  if (!input) return unavailableOnDevice();

  const hardware = record(input.hardware);
  const recommendation = record(input.recommendation);
  const verification = record(input.verification);
  const isRawSummary = hardware !== null && recommendation !== null && verification !== null;
  const isSafeSummary = typeof input.available === "boolean";
  if (!isRawSummary && !isSafeSummary) return unavailableOnDevice();

  const rawMemory = isRawSummary
    ? hardware?.memory_gb ?? hardware?.memoryGb
    : input.memoryGb ?? input.memory_gb;
  const memoryGb = Math.round(Math.min(4096, Math.max(0, finiteNumber(rawMemory, 0))) * 10) / 10;
  const rawCores = isRawSummary ? hardware?.cores : input.cores;

  return {
    available: isRawSummary ? true : input.available === true,
    chip: text(isRawSummary ? hardware?.chip : input.chip).slice(0, 64),
    cores: Math.min(1024, nonNegativeInt(rawCores)),
    memoryGb,
    appleSilicon: isRawSummary
      ? hardware?.is_apple_silicon === true || hardware?.appleSilicon === true
      : input.appleSilicon === true || input.apple_silicon === true,
    engine: text(isRawSummary ? recommendation?.primary_engine : input.engine).slice(0, 32),
    recommendedModel: text(
      isRawSummary ? recommendation?.recommended_model : input.recommendedModel ?? input.recommended_model,
    ).slice(0, 128),
    quant: text(isRawSummary ? recommendation?.recommended_quant : input.quant).slice(0, 32),
    verified: isRawSummary ? verification?.ok === true : input.verified === true,
    statusLabel: text(isRawSummary ? input.status_label ?? input.statusLabel : input.statusLabel ?? input.status_label).slice(0, 240),
    statusDetail: text(isRawSummary ? input.status_detail ?? input.statusDetail : input.statusDetail ?? input.status_detail).slice(0, 240),
  };
}

function unavailablePipeline(): PipelineStatus {
  return {
    active: false,
    stage: "none",
    stageIndex: 0,
    stageTotal: 0,
    outcome: "unknown",
  };
}

function pipelineOutcome(value: unknown): PipelineOutcome {
  return member(PIPELINE_OUTCOMES, value) ?? "unknown";
}

function pipelineStage(value: unknown): PipelineStage {
  return member(PIPELINE_STAGES, value) ?? "unknown";
}

function pipelineIndex(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value) && value >= 0
    ? value
    : null;
}

export function parsePipeline(value: unknown): PipelineStatus {
  const input = record(value);
  if (!input) return unavailablePipeline();

  if (Array.isArray(input.stages)) {
    const stages = input.stages;
    const requestedIndex = pipelineIndex(input.active_index ?? input.activeIndex);
    let selectedIndex = requestedIndex !== null && requestedIndex < stages.length ? requestedIndex : null;
    if (selectedIndex === null) {
      const fallbackIndex = stages.findIndex((item) => record(item)?.state === "active");
      selectedIndex = fallbackIndex >= 0 ? fallbackIndex : null;
    }
    if (selectedIndex === null) {
      return {
        active: false,
        stage: "none",
        stageIndex: 0,
        stageTotal: Math.min(16, stages.length),
        outcome: pipelineOutcome(input.outcome),
      };
    }
    const selected = record(stages[selectedIndex]);
    return {
      active: true,
      stage: pipelineStage(selected?.id),
      stageIndex: Math.min(16, selectedIndex),
      stageTotal: Math.min(16, stages.length),
      outcome: pipelineOutcome(input.outcome),
    };
  }

  const hasSafeShape = typeof input.active === "boolean"
    && typeof input.stage === "string"
    && ("stageIndex" in input || "stage_index" in input)
    && ("stageTotal" in input || "stage_total" in input)
    && typeof input.outcome === "string";
  if (!hasSafeShape) return unavailablePipeline();

  const active = input.active === true;
  return {
    active,
    stage: active ? pipelineStage(input.stage) : "none",
    stageIndex: active ? Math.min(16, pipelineIndex(input.stageIndex ?? input.stage_index) ?? 0) : 0,
    stageTotal: Math.min(16, nonNegativeInt(input.stageTotal ?? input.stage_total)),
    outcome: pipelineOutcome(input.outcome),
  };
}

export function parseJobEvent(value: unknown): JobEvent | null {
  const input = record(value);
  if (!input) return null;
  if (Object.keys(input).some((key) => !JOB_EVENT_KEYS.has(key))) return null;
  if (typeof input.jobId !== "string" || typeof input.kind !== "string" || typeof input.stage !== "string") {
    return null;
  }
  if (typeof input.load !== "number" || !Number.isFinite(input.load)) return null;
  if (typeof input.time !== "number" || !Number.isFinite(input.time)) return null;
  if (input.errorCode !== null && typeof input.errorCode !== "string") return null;
  return {
    jobId: input.jobId,
    kind: input.kind,
    stage: input.stage,
    load: clamp01(input.load),
    time: input.time,
    errorCode: input.errorCode,
  };
}

export function serializeJobEvent(event: JobEvent): string {
  return JSON.stringify({
    jobId: event.jobId,
    kind: event.kind,
    stage: event.stage,
    load: event.load,
    time: event.time,
    errorCode: event.errorCode,
  });
}

export function unavailableSnapshot(errorCode: string | null = "snapshot_unavailable"): RuntimeSnapshot {
  return {
    available: false,
    rooms: [],
    availableChats: [],
    jobs: [],
    recentReceipts: [],
    jobLoad: 0,
    background: parseBackground(null),
    onDevice: unavailableOnDevice(),
    pipeline: unavailablePipeline(),
    terminal: { sent: 0, skipped: 0, deliveryUnknown: 0, burstSuperseded: 0 },
    contextSync: { mode: "async", waited: false },
    replyModelId: null,
    voice: parseVoice(null),
    errorCode,
  };
}

function parseRooms(value: unknown): RoomSummary[] {
  return list(value).flatMap((item): RoomSummary[] => {
    const room = record(item);
    const chatId = positiveChatId(room?.chat_id ?? room?.chatId);
    if (!room || chatId === null) return [];
    return [{
      chatId,
      title: text(room.title, `id:${chatId}`),
      live: room.live === true,
      autoReply: room.auto_reply === true || room.autoReply === true,
      openJobs: nonNegativeInt(room.open_jobs ?? room.openJobs),
    }];
  });
}

function parseAvailableChats(value: unknown): AvailableChatSummary[] {
  return list(value).flatMap((item): AvailableChatSummary[] => {
    const chat = record(item);
    const chatId = positiveChatId(chat?.chat_id ?? chat?.chatId);
    if (!chat || chatId === null) return [];
    return [{
      chatId,
      title: text(chat.title, `id:${chatId}`),
      catalog: chat.catalog === true,
      live: chat.live === true,
    }];
  });
}

function parseRecentReceipt(value: unknown): RecentReceipt | null {
  const input = record(value);
  if (!input) return null;
  const chatId = positiveChatId(input.chatId);
  const outcome = member(RECEIPT_OUTCOMES, input.outcome);
  if (chatId === null || outcome === null) return null;
  if (typeof input.title !== "string" || typeof input.displayTime !== "string" || typeof input.clock !== "string") return null;
  if (typeof input.reasonText !== "string") return null;
  const reasonCode = text(input.reasonCode);
  return {
    chatId,
    title: input.title.slice(0, 120),
    displayTime: input.displayTime.slice(0, 32),
    clock: input.clock.slice(0, 8),
    outcome,
    outcomeText: (firstString(input.outcomeText, input.outcome_text) ?? "").slice(0, 32),
    reasonCode: RECEIPT_REASON_SLUG.test(reasonCode) ? reasonCode : "unspecified",
    reasonText: input.reasonText.slice(0, 64),
    retrievalState: member(RETRIEVAL_STATES, input.retrievalState) ?? "unrecorded",
  };
}

function parseVoice(value: unknown): VoiceStatus {
  const voice = record(value);
  const wakeSources = [voice?.wake_source, voice?.wakeSource];
  return {
    available: voice?.available === true,
    state: text(voice?.state, "unavailable"),
    rms: clamp01(voice?.rms),
    errorCode: firstString(voice?.error_code, voice?.errorCode),
    wakeSource: wakeSources.includes("stock") ? "stock" : wakeSources.includes("custom") ? "custom" : "none",
    updatedAt: nonNegativeInt(voice?.updated_at ?? voice?.updatedAt),
    wakePhrase: text(voice?.wake_phrase ?? voice?.wakePhrase),
    threshold: Math.min(0.95, Math.max(0.65, finiteNumber(voice?.threshold, 0.65))),
    customModelSelected: voice?.custom_model_selected === true || voice?.customModelSelected === true,
  };
}

export function parseRuntimeSnapshot(value: unknown): RuntimeSnapshot {
  const input = record(value);
  if (!input) return unavailableSnapshot("snapshot_invalid");

  const receipts = Array.isArray(input.recent_receipts) ? input.recent_receipts : list(input.recentReceipts);
  const terminal = record(input.terminal_counts ?? input.terminal) ?? {};
  const contextSync = record(input.context_sync ?? input.contextSync);
  const contextValid = contextSync?.mode === "async" && contextSync.waited === false;

  return {
    available: input.available !== false && contextValid,
    rooms: parseRooms(input.rooms),
    availableChats: parseAvailableChats(input.availableChats ?? input.available_chats),
    jobs: list(input.jobs).map(parseJobEvent).filter((item): item is JobEvent => item !== null),
    recentReceipts: receipts
      .map(parseRecentReceipt)
      .filter((item): item is RecentReceipt => item !== null)
      .slice(0, 12),
    jobLoad: clamp01(input.job_load ?? input.jobLoad),
    background: parseBackground(input.background),
    onDevice: parseOnDevice(input.onDevice ?? input.ondevice_hardware),
    pipeline: parsePipeline(input.pipeline),
    terminal: {
      sent: nonNegativeInt(terminal.sent),
      skipped: nonNegativeInt(terminal.skipped),
      deliveryUnknown: nonNegativeInt(terminal.delivery_unknown ?? terminal.deliveryUnknown),
      burstSuperseded: nonNegativeInt(terminal.burst_superseded ?? terminal.burstSuperseded),
    },
    contextSync: { mode: "async", waited: false },
    replyModelId: firstString(input.reply_model_id, input.replyModelId),
    voice: parseVoice(input.voice),
    errorCode: contextValid ? firstString(input.error_code) : "context_sync_invalid",
  };
}

export function parseRuntimeSnapshotJson(json: string): RuntimeSnapshot {
  try {
    return parseRuntimeSnapshot(JSON.parse(json) as unknown);
  } catch {
    return unavailableSnapshot("snapshot_json_invalid");
  }
}

export function createCancellationToken(): CancellationToken {
  return { id: crypto.randomUUID(), cancelled: false };
}
