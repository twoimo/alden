/** One in-flight local read; visibility epochs discard late hidden responses. */
export class KnowledgeRefresh {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private active = false;
  private busy = false;
  private epoch = 0;
  constructor(private readonly read: () => Promise<Record<string, unknown> | null>, private readonly apply: (payload: Record<string, unknown>) => void, private readonly interval = 15000) {}
  start(): void {
    if (this.active) return;
    this.active = true;
    this.epoch += 1;
    this.schedule(0);
  }
  stop(): void {
    this.active = false;
    this.epoch += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
  private schedule(delay: number): void {
    if (!this.active || this.timer !== null) return;
    this.timer = setTimeout(() => { this.timer = null; void this.tick(); }, delay);
  }
  private async tick(): Promise<void> {
    if (!this.active) return;
    if (this.busy) { this.schedule(this.interval); return; }
    this.busy = true;
    const epoch = this.epoch;
    try {
      const payload = await this.read();
      if (payload && this.active && epoch === this.epoch) this.apply(payload);
    } catch { /* Preserve the last confirmed graph; next bounded read can recover. */ }
    finally { this.busy = false; this.schedule(this.interval); }
  }
}
