import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CollectionActivity, type NodeActivity } from '../knowledge/collection-activity';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { KnowledgeRefresh } from '../knowledge/refresh';

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
  it('preserves a new receipt across content refresh and waits for the matching source snapshot', async () => {
    const a='a'.repeat(64),b='b'.repeat(64);
    const read=vi.fn(async()=>page(15,[event()],{content_revision:b}));
    const {consumer,refresh,show}=setup(read);
    consumer.snapshot({...point(10),content_revision:a},nodes('v1'));
    await vi.advanceTimersByTimeAsync(2500);
    expect(consumer.diagnostics()).toMatchObject({cursor:15,pending:1});
    expect(refresh).toHaveBeenCalledTimes(1);expect(show).not.toHaveBeenCalled();
    consumer.snapshot({...point(15),content_revision:a},nodes());
    expect(show).not.toHaveBeenCalled();
    consumer.snapshot({...point(15),content_revision:b},nodes());
    expect(show).toHaveBeenCalledExactlyOnceWith(event());
  });
  it('refreshes changed content with no journal event without inventing a pulse', async () => {
    const a='a'.repeat(64),b='b'.repeat(64);
    const {consumer,refresh,show,clear}=setup(vi.fn(async()=>page(10,[],{content_revision:b})));
    consumer.snapshot({...point(10),content_revision:a},nodes('v1'));clear.mockClear();
    await vi.advanceTimersByTimeAsync(2500);
    expect(refresh).toHaveBeenCalledTimes(1);expect(clear).toHaveBeenCalledTimes(1);
    expect(show).not.toHaveBeenCalled();
    consumer.snapshot({...point(10),content_revision:b},[]);
    expect(show).not.toHaveBeenCalled();
  });
  it('keeps pending receipts while a content refresh is delayed across polls', async () => {
    const a='a'.repeat(64),b='b'.repeat(64);
    const read=vi.fn().mockResolvedValueOnce(page(15,[event()],{content_revision:b}))
      .mockResolvedValue(page(15,[],{content_revision:b}));
    const {consumer,show}=setup(read);consumer.snapshot({...point(10),content_revision:a},nodes('v1'));
    await vi.advanceTimersByTimeAsync(5000);
    expect(consumer.diagnostics().pending).toBe(1);expect(show).not.toHaveBeenCalled();
    consumer.snapshot({...point(15),content_revision:b},nodes());
    expect(show).toHaveBeenCalledExactlyOnceWith(event());
  });
  it.each([undefined,'bad',42])('does not drop declared content protection on a malformed page (%s)',async(value)=>{
    const a='a'.repeat(64);
    const {consumer,show}=setup(vi.fn(async()=>page(15,[event()],{content_revision:value})));
    consumer.snapshot({...point(10),content_revision:a},nodes('v1'));
    await vi.advanceTimersByTimeAsync(2500);
    expect(consumer.diagnostics()).toMatchObject({cursor:10,pending:0});expect(show).not.toHaveBeenCalled();
  });
  it('fences an older reply when a snapshot first declares content protection',async()=>{
    let resolve!:(value:ReturnType<typeof page>)=>void;
    const read=vi.fn(()=>new Promise<ReturnType<typeof page>>(done=>{resolve=done;}));
    const {consumer,show}=setup(read);
    await vi.advanceTimersByTimeAsync(2500);
    consumer.snapshot({...point(10),content_revision:'a'.repeat(64)},nodes('v1'));
    resolve(page(15,[event()]));await vi.advanceTimersByTimeAsync(0);
    expect(consumer.diagnostics()).toMatchObject({cursor:10,pending:0});expect(show).not.toHaveBeenCalled();
  });
  it('coalesces content invalidation while a slower graph read remains in flight',async()=>{
    const a='a'.repeat(64),b='b'.repeat(64);
    let resolve!:(value:Record<string,unknown>)=>void;
    const read=vi.fn().mockResolvedValueOnce(page(15,[event()],{content_revision:b}))
      .mockResolvedValue(page(15,[],{content_revision:b}));
    const show=vi.fn(),clear=vi.fn();
    let consumer!:CollectionActivity;
    const graph=new KnowledgeRefresh(()=>new Promise<Record<string,unknown>>(done=>{resolve=done;}),
      payload=>consumer.snapshot(payload.activity_checkpoint,nodes()));
    consumer=new CollectionActivity(read,()=>{graph.start();graph.invalidate();},show,clear);
    owned.push(consumer);consumer.start();consumer.snapshot({...point(10),content_revision:a},nodes('v1'));
    try{
      await vi.advanceTimersByTimeAsync(5000);
      expect(consumer.diagnostics().pending).toBe(1);expect(show).not.toHaveBeenCalled();
      resolve({ok:true,activity_checkpoint:{...point(15),content_revision:b}});
      await vi.advanceTimersByTimeAsync(0);
      expect(show).toHaveBeenCalledExactlyOnceWith(event());
    }finally{graph.stop();}
  });
  it('discards a pending source receipt when a bodyless retirement invalidates its snapshot', async () => {
    const read = vi.fn().mockResolvedValueOnce(page(15, [event()]))
      .mockResolvedValue(page(20, [], { refresh_required: true }));
    const { consumer, show } = setup(read);
    await vi.advanceTimersByTimeAsync(2500);
    expect(consumer.diagnostics().pending).toBe(1);
    await vi.advanceTimersByTimeAsync(2500);
    expect(consumer.diagnostics().pending).toBe(0);
    consumer.snapshot(point(20), nodes());
    expect(show).not.toHaveBeenCalled();
  });
  it('refreshes a withheld retirement without revealing a receipt or playing a pulse', async () => {
    const { consumer, refresh, show, clear } = setup(vi.fn(async () => page(15, [], { refresh_required: true })));
    clear.mockClear();
    await vi.advanceTimersByTimeAsync(2500);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(clear).toHaveBeenCalledTimes(1);
    expect(show).not.toHaveBeenCalled();
    expect(consumer.diagnostics().cursor).toBe(15);
    consumer.snapshot(point(15), []);
    expect(show).not.toHaveBeenCalled();
  });
  it('admits the first saved event from an empty baseline only after its exact source version is visible', async () => {
    const read = vi.fn(async () => page(5, [event(4, { kind: 'added' })]));
    const refresh = vi.fn(), show = vi.fn(), clear = vi.fn();
    const consumer = new CollectionActivity(read, refresh, show, clear);
    owned.push(consumer); consumer.start(); consumer.snapshot(point(0), []);
    await vi.advanceTimersByTimeAsync(2500);
    expect(read).toHaveBeenCalledWith({ activity: true, after: 0, stream_id: 'stream-a', limit: 200 });
    expect(refresh).toHaveBeenCalledTimes(1); expect(show).not.toHaveBeenCalled();
    consumer.snapshot(point(5), nodes());
    expect(show).toHaveBeenCalledExactlyOnceWith(event(4, { kind: 'added' }));
    consumer.snapshot(point(5), nodes()); expect(show).toHaveBeenCalledTimes(1);
  });
  it('refreshes committed removal and relationship changes without inventing a node pulse', async () => {
    const changes = [
      { ...event(13), kind: 'removed' },
      { ...event(14), kind: 'relations_changed', document_id: null, version: null },
    ];
    const { consumer, refresh, show } = setup(vi.fn(async () => page(14, changes)));
    await vi.advanceTimersByTimeAsync(2500);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(show).not.toHaveBeenCalled();
    expect(consumer.diagnostics().pending).toBe(0);
    consumer.snapshot(point(14), []);
    expect(show).not.toHaveBeenCalled();
  });
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
  it('discards an old journal reply after a replacement snapshot even when node identities match', async () => {
    let resolve!: (value: ReturnType<typeof page>) => void;
    const read = vi.fn((_options: Record<string, unknown>) => new Promise<ReturnType<typeof page>>(done => { resolve = done; }));
    const { consumer, show } = setup(read);
    await vi.advanceTimersByTimeAsync(2500);
    consumer.snapshot(point(100, 'stream-b'), nodes());
    resolve(page(15, [event()]));
    await Promise.resolve(); await Promise.resolve();
    expect(consumer.diagnostics()).toMatchObject({ cursor: 100, pending: 0, seen: 0 });
    expect(show).not.toHaveBeenCalled();
    read.mockImplementation(async () => page(101, [event(101, { at: 14 })], { stream_id: 'stream-b' }));
    await vi.advanceTimersByTimeAsync(2500);
    expect(read.mock.calls.at(-1)![0]).toEqual({ activity: true, after: 100, stream_id: 'stream-b', limit: 200 });
    consumer.snapshot(point(101, 'stream-b'), nodes());
    expect(show).toHaveBeenCalledExactlyOnceWith(event(101, { at: 14 }));
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
