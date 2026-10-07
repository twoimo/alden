import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CollectionActivity, type NodeActivity } from '../knowledge/collection-activity';
import { parseKnowledgeGraph } from '../knowledge/graph-model';

const owned: CollectionActivity[] = [];
const point = (cursor: number, stream_id = 'stream-a') => ({ cursor, stream_id });
const nodes = (version = 'v2', target = 'target-a', id = 'a') => parseKnowledgeGraph({ nodes: [
  { id, label: id, source_version: version, source_target: target },
], edges: [] }).nodes;
const event = (sequence = 14, extra: Partial<NodeActivity> = {}): NodeActivity => ({
  event_id: 'event-' + sequence, sequence, document_id: 'a', version: 'v2', target_id: 'target-a',
  run_id: 'run-a', origin: 'source-import', at: 11, kind: 'revised', success: true, ...extra,
});
const page = (cursor: number, items: unknown[] = [], extra = {}) => ({ ok: true, ...point(cursor), items,
  latest: cursor, reset: false, has_more: false, ...extra });
function setup(read = vi.fn(async (_: Record<string, unknown>) => page(15, [event()]))) {
  const refresh = vi.fn(), show = vi.fn(), clear = vi.fn();
  const consumer = new CollectionActivity(read, refresh, show, clear);
  owned.push(consumer); consumer.start(); consumer.snapshot(point(10), nodes('v1'));
  return { consumer, read, refresh, show, clear };
}
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(10000); });
afterEach(() => { owned.forEach(c => c.stop()); owned.length = 0; vi.useRealTimers(); });

describe('committed graph activity consumption', () => {
  it('baselines without pulses, waits for the saved visible version, and never skips unread pages on refresh', async () => {
    const { consumer, read, refresh, show } = setup();
    consumer.snapshot(point(12), nodes('v1'));
    expect(show).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2500);
    expect(read).toHaveBeenCalledWith({ activity: true, after: 10, stream_id: 'stream-a', limit: 200 });
    expect(refresh).toHaveBeenCalledTimes(1); expect(show).not.toHaveBeenCalled();
    consumer.snapshot(point(15), nodes());
    expect(show).toHaveBeenCalledWith(event()); expect(consumer.diagnostics().pending).toBe(0);
    await vi.advanceTimersByTimeAsync(2500);
    expect(show).toHaveBeenCalledTimes(1); expect(refresh).toHaveBeenCalledTimes(1);
  });
  it('an older in-flight snapshot cannot rewind the committed cursor or discard a pending receipt', async () => {
    const { consumer, show } = setup();
    await vi.advanceTimersByTimeAsync(2500);
    consumer.snapshot(point(11), nodes('v1'));
    expect(consumer.diagnostics().cursor).toBe(15); expect(consumer.diagnostics().pending).toBe(1);
    consumer.snapshot(point(15), nodes()); expect(show).toHaveBeenCalledTimes(1);
  });
  it('does not pulse wrong target/version, hidden nodes, future/old timestamps, failures, or reversed sequences', async () => {
    const entries = [event(11, { at: 0 }), event(12, { at: 20 }), event(13, { success: false } as never),
      event(14, { target_id: 'denied' }), event(15, { document_id: 'hidden' }), event(16, { version: 'old' }), event(14)];
    const { consumer, show } = setup(vi.fn(async () => page(16, entries)));
    await vi.advanceTimersByTimeAsync(2500); consumer.snapshot(point(16), nodes());
    expect(show).not.toHaveBeenCalled(); expect(consumer.diagnostics().pending).toBe(0);
  });
  it('fences hidden replies and resumes the old cursor without lighting hidden history', async () => {
    let resolve!: (value: ReturnType<typeof page>) => void;
    const read = vi.fn((_options: Record<string, unknown>) => new Promise<ReturnType<typeof page>>(done => { resolve = done; }));
    const { consumer, show, refresh } = setup(read);
    await vi.advanceTimersByTimeAsync(2500); consumer.stop(); resolve(page(15, [event()]));
    await Promise.resolve(); await Promise.resolve();
    expect(consumer.diagnostics().cursor).toBe(10); expect(refresh).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0);
    vi.setSystemTime(20000); read.mockImplementation(async () => page(15, [event()]));
    consumer.start(); consumer.snapshot(point(15), nodes()); await vi.advanceTimersByTimeAsync(2500);
    expect(read.mock.calls.at(-1)![0].after).toBe(10); expect(show).not.toHaveBeenCalled();
    expect(consumer.diagnostics().cursor).toBe(15); expect(refresh).not.toHaveBeenCalled();
  });
  it('requires a fresh snapshot after journal reset, without trusting reset-page candidates', async () => {
    const { consumer, read, refresh, show } = setup(vi.fn(async () => page(5, [event()], { stream_id: 'replacement', reset: true })));
    await vi.advanceTimersByTimeAsync(2500); expect(refresh).toHaveBeenCalledTimes(1); expect(show).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(10000); expect(read).toHaveBeenCalledTimes(1);
    consumer.snapshot(point(5, 'replacement'), nodes()); expect(show).not.toHaveBeenCalled();
    expect(consumer.diagnostics().cursor).toBe(5);
  });
  it('bounds pages, pending nodes and event IDs, and catches up one page at a time', async () => {
    const entries = Array.from({ length: 200 }, (_, index) => event(index + 11, { document_id: 'n' + index }));
    const { consumer, read, show } = setup(vi.fn(async () => page(210, entries, { latest: 300, has_more: true })));
    await vi.advanceTimersByTimeAsync(2500);
    expect(consumer.diagnostics().pending).toBe(120); expect(consumer.diagnostics().seen).toBe(200);
    const visible = entries.slice(80).flatMap(e => nodes(e.version, e.target_id, e.document_id));
    consumer.snapshot(point(210), visible); expect(show).toHaveBeenCalledTimes(120);
    await vi.advanceTimersByTimeAsync(499); expect(read).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(read).toHaveBeenCalledTimes(2); expect(show).toHaveBeenCalledTimes(120);
  });
  it('advances across skipped stages without refresh, rejects over-budget pages and non-progress loops', async () => {
    const read = vi.fn(async () => page(12));
    const { consumer, refresh } = setup(read);
    await vi.advanceTimersByTimeAsync(2500); expect(consumer.diagnostics().cursor).toBe(12); expect(refresh).not.toHaveBeenCalled();
    read.mockImplementation(async () => page(300, Array.from({ length: 201 }, (_, i) => event(i + 20))));
    await vi.advanceTimersByTimeAsync(2500); expect(consumer.diagnostics().cursor).toBe(12);
    read.mockImplementation(async () => page(12, [], { has_more: true, latest: 14 }));
    await vi.advanceTimersByTimeAsync(2500); expect(consumer.diagnostics().cursor).toBe(12);
  });
  it('scope reset forgets all receipts and stops every scheduled read', async () => {
    const { consumer, read, show } = setup();
    await vi.advanceTimersByTimeAsync(2500); consumer.reset(); consumer.start();
    await vi.advanceTimersByTimeAsync(10000);
    expect(read).toHaveBeenCalledTimes(1); expect(show).not.toHaveBeenCalled();
    expect(consumer.diagnostics()).toEqual({ cursor: null, pending: 0, seen: 0, active: true });
  });
});
