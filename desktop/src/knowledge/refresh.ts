/** One in-flight local read; visibility epochs discard late hidden responses. */
export class KnowledgeRefresh {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private boundaryTimer: ReturnType<typeof setTimeout> | null = null;
  private confirmedPayload: Record<string, unknown> | null = null;
  private active = false;
  private busy = false;
  private epoch = 0;
  private confirmedRevision: string | null = null;
  private lastRead = -Infinity;
  private retryAfter = 0;
  private needsRead = true;
  constructor(private readonly read: () => Promise<Record<string, unknown> | null>, private readonly apply: (payload: Record<string, unknown>) => void, private readonly interval = 15000,
    private readonly revision?: () => Promise<string | null>, private readonly probeInterval = 1000) {}
  start(): void {
    if (this.active) return;
    this.active = true;
    this.epoch += 1;
    this.needsRead = true;
    this.schedule(0);
  }
  stop(): void {
    this.active = false;
    this.epoch += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    if (this.boundaryTimer !== null) clearTimeout(this.boundaryTimer);
    this.boundaryTimer = null;
  }
  private schedule(delay: number): void {
    if (!this.active || this.timer !== null) return;
    this.timer = setTimeout(() => { this.timer = null; void this.tick(); }, delay);
  }
  private scheduleBoundary(payload: Record<string, unknown>): void {
    if (this.boundaryTimer !== null) clearTimeout(this.boundaryTimer);
    this.boundaryTimer = null;
    if (!this.active) return;
    const now = Date.now(), boundaries: number[] = [];
    if (typeof payload.next_transition_at === 'number' && Number.isFinite(payload.next_transition_at)) boundaries.push(payload.next_transition_at * 1000);
    if (Array.isArray(payload.edges)) for (const raw of payload.edges) {
      if (!raw || typeof raw !== 'object') continue;
      const edge = raw as Record<string, unknown>;
      if ((edge.evidence as { retracted?: boolean } | null)?.retracted) continue;
      for (const key of ['valid_from', 'valid_to']) if (typeof edge[key] === 'string') boundaries.push(Date.parse(edge[key]));
    }
    const next = Math.min(...boundaries.filter(at => Number.isFinite(at) && at > now));
    if (!Number.isFinite(next)) return;
    this.boundaryTimer = setTimeout(() => {
      this.boundaryTimer = null;
      if (!this.active || this.confirmedPayload !== payload) return;
      this.apply(payload); // Expiry is visible even if the next source read fails.
      this.needsRead = true; this.retryAfter = 0;
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null; this.schedule(0);
      this.scheduleBoundary(payload);
    }, Math.min(2147483647, next - now));
  }
  private async tick(): Promise<void> {
    if (!this.active) return;
    const cadence = this.revision ? this.probeInterval : this.interval;
    if (this.busy) { this.schedule(cadence); return; }
    this.busy = true;
    const epoch = this.epoch;
    try {
      let revision: string | null = null;
      try { revision = await this.revision?.() ?? null; } catch { /* Fall back to the full read interval. */ }
      if (!this.active || epoch !== this.epoch) return;
      const now = Date.now();
      const changed = revision !== null && revision !== this.confirmedRevision;
      if (this.revision && !this.needsRead && !changed && now - this.lastRead < this.interval) return;
      if (now < this.retryAfter) return;
      this.retryAfter = now + Math.min(2000, this.interval);
      const payload = await this.read();
      if (payload && this.active && epoch === this.epoch) {
        this.apply(payload);
        if (payload.ok !== false) {
          this.confirmedRevision = revision; this.lastRead = Date.now(); this.needsRead = false; this.retryAfter = 0;
          this.confirmedPayload = payload; this.scheduleBoundary(payload);
        }
      }
    } catch { /* Preserve the last confirmed graph; next bounded read can recover. */ }
    finally { this.busy = false; this.schedule(cadence); }
  }
}
