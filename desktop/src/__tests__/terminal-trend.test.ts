import { describe, expect, it } from 'vitest';
import { readTerminalTrend, trendWindow, type TrendWindow } from '../knowledge/terminal-trend';

const now = Date.parse('2026-10-22T12:00:00Z');
const window = trendWindow({}, now)!;
const DAY = 86400;
function response(range: TrendWindow = window) {
  const days = [];
  for (let at = Math.floor(range.since / DAY) * DAY; at < range.until; at += DAY) {
    days.push({ day: new Date(at * 1000).toISOString().slice(0, 10), added: 0, revised: 0, removed: 0 });
  }
  return { ok: true, stream_id: 's1', cursor: 1000, snapshot_checkpoint: { stream_id: 's1', cursor: 1000 }, trend: {
    schema: 1, basis: 'stored_receipt_utc', window: range, stream_id: 's1', cursor: 1000,
    scan: 'complete', coverage: 'retained_only', source_coverage: 'unknown', backfill_live: 'unknown',
    relations: null, unclassified: 0, observed_at: now / 1000, days,
  } };
}

describe('f(x) exact UTC window and untrusted aggregate boundary', () => {
  it('uses 14 contiguous calendar days including the current partial day', () => {
    expect(window).toEqual({ since: Date.parse('2026-10-09T00:00:00Z') / 1000, until: Date.parse('2026-10-23T00:00:00Z') / 1000 });
    const parsed = readTerminalTrend(response(), window, now)!;
    expect(parsed.days).toHaveLength(14);
    expect(parsed.days[0].day).toBe('2026-10-09');
    expect(parsed.days.at(-1)!.day).toBe('2026-10-22');
    expect(trendWindow({}, Date.parse('2026-10-23T00:00:00Z'))!.since).toBe(window.since + DAY);
  });
  it('preserves exact partial calendar endpoints from the selected local-date scope', () => {
    const range = { since: window.since + 15 * 3600, until: window.since + DAY + 15 * 3600 };
    expect(trendWindow(range, now)).toEqual(range);
    expect(readTerminalTrend(response(range), range, now)!.days).toHaveLength(2);
  });
  it('accepts confirmed counts and an empty retained scan without claiming source coverage', () => {
    const result = response();
    result.trend.days[0].added = 101;
    result.trend.days[1].revised = 2;
    result.trend.days[2].removed = 1;
    result.trend.unclassified = 2;
    const parsed = readTerminalTrend(result, window, now)!;
    expect(parsed.days[0].added).toBe(101);
    expect(parsed.unclassified).toBe(2);
    expect(readTerminalTrend(response(), window, now)!.days.every(day => day.added === 0)).toBe(true);
  });
  it.each([
    { since: -1 }, { since: '0' }, { until: Infinity }, { until: true },
    { since: window.until }, { since: window.until - 32 * DAY },
  ])('rejects invalid or over-budget selected intervals without silent clipping: %j', query => {
    expect(trendWindow(query, now)).toBeNull();
  });
  it('binds a complete aggregate to its snapshot, not a truncated after-page cursor', () => {
    const result = response(); result.cursor = 80; result.trend.days[0].added = 101;
    expect(readTerminalTrend(result, window, now)!.days[0].added).toBe(101);
    expect(readTerminalTrend({ ...result, snapshot_checkpoint: { stream_id: 'other', cursor: 1000 } }, window, now)).toBeNull();
    expect(readTerminalTrend({ ...result, snapshot_checkpoint: { stream_id: 's1', cursor: 79 } }, window, now)).toBeNull();
    expect(readTerminalTrend({ ...result, snapshot_checkpoint: undefined }, window, now)).toBeNull();
  });
  it('rejects missing, duplicate, reversed, shifted or partial calendar coverage', () => {
    for (const mutate of [
      (days: ReturnType<typeof response>['trend']['days']) => days.slice(1),
      (days: ReturnType<typeof response>['trend']['days']) => [days[0], ...days.slice(0, -1)],
      (days: ReturnType<typeof response>['trend']['days']) => [...days].reverse(),
      (days: ReturnType<typeof response>['trend']['days']) => [...days.slice(0, -1), { ...days.at(-1)!, day: '2026-10-23' }],
    ]) {
      const result = response(); result.trend.days = mutate(result.trend.days);
      expect(readTerminalTrend(result, window, now)).toBeNull();
    }
  });
  it.each([
    { schema: 2 }, { basis: 'node_updated_at' }, { scan: 'partial' }, { coverage: 'complete' },
    { source_coverage: 'complete' }, { backfill_live: 'live' }, { relations: 0 },
    { unclassified: -1 }, { unclassified: 1.5 }, { observed_at: Infinity },
    { observed_at: now / 1000 + 61 }, { stream_id: 's2' }, { cursor: 999 },
    { window: { since: window.since + 1, until: window.until } },
  ])('rejects malformed or mismatched aggregate evidence: %j', delta => {
    const result = response();
    expect(readTerminalTrend({ ...result, trend: { ...result.trend, ...delta } }, window, now)).toBeNull();
  });
  it.each([-1, 1.5, NaN, Infinity, '1', true, Number.MAX_SAFE_INTEGER])('rejects invalid or impossible document count %j', added => {
    const result = response();
    expect(readTerminalTrend({ ...result, trend: { ...result.trend, days: result.trend.days.map((day, i) => i ? day : { ...day, added }) } }, window, now)).toBeNull();
  });
  it('bounds all classified and unclassified groups by the snapshot without safe-integer overflow', () => {
    const excessive = response(); excessive.trend.unclassified = 1001;
    expect(readTerminalTrend(excessive, window, now)).toBeNull();
    const combined = response(); combined.trend.unclassified = 999; combined.trend.days[0].added = 2;
    expect(readTerminalTrend(combined, window, now)).toBeNull();
    combined.trend.days[0].added = 1;
    expect(readTerminalTrend(combined, window, now)).not.toBeNull();
    const overflow = response(); overflow.snapshot_checkpoint.cursor = Number.MAX_SAFE_INTEGER;
    overflow.trend.cursor = Number.MAX_SAFE_INTEGER; overflow.trend.unclassified = Number.MAX_SAFE_INTEGER;
    overflow.trend.days[0].added = 1;
    expect(readTerminalTrend(overflow, window, now)).toBeNull();
  });
  it('rejects a positive receipt in a future calendar slot', () => {
    const result = response(); result.trend.observed_at = window.since;
    result.trend.days[1].added = 1;
    expect(readTerminalTrend(result, window, now)).toBeNull();
  });
  it.each([null, {}, { ok: true }, { ...response(), partial: true }, { ...response(), stale: true }, { ok: false, trend: response().trend }])('rejects unavailable aggregates %j', result => {
    expect(readTerminalTrend(result, window, now)).toBeNull();
  });
});
