import { afterEach, describe, expect, it, vi } from "vitest";
import { KnowledgeRefresh } from "../knowledge/refresh";
afterEach(() => vi.useRealTimers());
describe("local knowledge refresh", () => {
  it("has one timer and stops all future reads when hidden", async () => {
    vi.useFakeTimers();
    const read = vi.fn(async () => ({ ok: true })), apply = vi.fn();
    const refresh = new KnowledgeRefresh(read, apply, 100);
    refresh.start(); refresh.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(read).toHaveBeenCalledTimes(1); expect(apply).toHaveBeenCalledTimes(1);
    refresh.stop(); await vi.advanceTimersByTimeAsync(1000);
    expect(read).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it("discards a response from before hide and resumes without overlapping", async () => {
    vi.useFakeTimers();
    let resolve!: (payload: Record<string, unknown>) => void;
    const read = vi.fn(() => new Promise<Record<string, unknown>>(done => { resolve = done; }));
    const apply = vi.fn(), refresh = new KnowledgeRefresh(read, apply, 100);
    refresh.start(); await vi.advanceTimersByTimeAsync(0);
    refresh.stop(); refresh.start(); await vi.advanceTimersByTimeAsync(0);
    expect(read).toHaveBeenCalledTimes(1);
    resolve({ version: "old" }); await Promise.resolve();
    expect(apply).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(100); expect(read).toHaveBeenCalledTimes(2);
    resolve({ version: "new" }); await Promise.resolve();
    expect(apply).toHaveBeenCalledWith({ version: "new" });
    refresh.stop();
  });
  it("recovers from unavailable reads without discarding the confirmed graph", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockRejectedValueOnce(new Error("unavailable")).mockResolvedValueOnce({ ok: true });
    const apply = vi.fn(), refresh = new KnowledgeRefresh(read, apply, 100);
    refresh.start(); await vi.advanceTimersByTimeAsync(0); expect(apply).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(100); expect(apply).toHaveBeenCalledWith({ ok: true });
    refresh.stop();
  });
});
