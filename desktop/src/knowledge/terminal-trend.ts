/** Receipt aggregate IPC boundary. Graph timestamps and journal tails are not inputs. */
const DAY = 86400;
export type TrendWindow = { since: number; until: number };
export type TrendDay = { day: string; added: number; revised: number; removed: number };
export type TerminalTrend = { window: TrendWindow; days: TrendDay[]; unclassified: number; observedAt: number };
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const timestamp = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 253402214400;

/** Preserve explicit filter endpoints, including local-date endpoints from the caller. */
export function trendWindow(query: Record<string, unknown>, now = Date.now()): TrendWindow | null {
  const until = query.until ?? (Math.floor(now / 1000 / DAY) + 1) * DAY;
  if (!timestamp(until)) return null;
  const since = query.since ?? Math.max(0, (Math.ceil(until / DAY) - 14) * DAY);
  if (!timestamp(since) || !(until > since) || until - since > 31 * DAY) return null;
  return { since, until };
}

export function readTerminalTrend(result: unknown, window: TrendWindow, now = Date.now()): TerminalTrend | null {
  if (!record(result) || result.ok !== true || result.partial === true || result.stale === true || !record(result.trend)) return null;
  const raw = result.trend, snapshot = result.snapshot_checkpoint;
  if (!record(snapshot) || !count(snapshot.cursor) || snapshot.stream_id !== result.stream_id) return null;
  if (raw.schema !== 1 || raw.basis !== 'stored_receipt_utc' || raw.scan !== 'complete'
    || raw.coverage !== 'retained_only' || raw.source_coverage !== 'unknown'
    || raw.backfill_live !== 'unknown' || raw.relations !== null
    || typeof result.stream_id !== 'string' || !result.stream_id || result.stream_id.length > 128
    || raw.stream_id !== snapshot.stream_id || !count(result.cursor) || result.cursor > snapshot.cursor || raw.cursor !== snapshot.cursor
    || !record(raw.window) || raw.window.since !== window.since || raw.window.until !== window.until
    || !count(raw.unclassified) || !timestamp(raw.observed_at) || raw.observed_at > now / 1000 + 60
    || !Array.isArray(raw.days)) return null;
  const expected: string[] = [];
  for (let at = Math.floor(window.since / DAY) * DAY; at < window.until; at += DAY) expected.push(new Date(at * 1000).toISOString().slice(0, 10));
  if (raw.days.length !== expected.length) return null;
  const days: TrendDay[] = [];
  // Each classified or unclassified group consumes at least one retained sequence.
  let total = raw.unclassified;
  for (const [index, value] of raw.days.entries()) {
    if (!record(value) || value.day !== expected[index] || !count(value.added) || !count(value.revised) || !count(value.removed)) return null;
    total += value.added + value.revised + value.removed;
    if (!Number.isSafeInteger(total) || total > snapshot.cursor) return null;
    // A future calendar slot cannot contain an already stored receipt.
    if (Date.parse(String(value.day)) / 1000 > raw.observed_at && value.added + value.revised + value.removed > 0) return null;
    days.push({ day: String(value.day), added: value.added, revised: value.revised, removed: value.removed });
  }
  return { window: { ...window }, days, unclassified: raw.unclassified, observedAt: raw.observed_at };
}
