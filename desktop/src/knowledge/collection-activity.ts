import type { KnowledgeNode } from './graph-model';

export const ACTIVITY_TTL_MS = 10000;
export type NodeActivity = {
  event_id: string; sequence: number; document_id: string; version: string;
  target_id: string; run_id: string; origin: string; at: number;
  kind: 'added' | 'revised' | 'read'; success: true;
};
type Checkpoint = { stream_id: string; cursor: number };
type StructuralActivity = Omit<NodeActivity, 'kind' | 'document_id' | 'version'> & {
  kind: 'removed' | 'relations_changed'; document_id: string | null; version: string | null;
};
const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
function checkpoint(value: unknown): Checkpoint | null {
  const row = object(value);
  return row && typeof row.stream_id === 'string' && row.stream_id.length > 0 && row.stream_id.length <= 128 && integer(row.cursor)
    ? { stream_id: row.stream_id, cursor: row.cursor } : null;
}
function receipt(value: unknown): NodeActivity | StructuralActivity | null {
  const row = object(value);
  if (!row || !integer(row.sequence) || row.success !== true || !['added', 'revised', 'removed', 'relations_changed'].includes(String(row.kind))
    || typeof row.at !== 'number' || !Number.isFinite(row.at) || row.at < 0) return null;
  for (const key of ['event_id', 'target_id', 'run_id', 'origin']) {
    if (typeof row[key] !== 'string' || !row[key] || (row[key] as string).length > 256) return null;
  }
  if (row.kind === 'relations_changed') {
    if (row.document_id !== null || row.version !== null) return null;
    return row as StructuralActivity;
  }
  for (const key of ['document_id', 'version']) {
    if (typeof row[key] !== 'string' || !row[key] || (row[key] as string).length > 256) return null;
  }
  return row as NodeActivity | StructuralActivity;
}

/** One visible-only, bounded journal consumer. Snapshots establish the first
 * cursor; ordinary refreshes never skip unread journal pages. No history pulse. */
export class CollectionActivity {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private active = false;
  private busy = false;
  private epoch = 0;
  private visibleSince = 0;
  private committed: Checkpoint | null = null;
  private waitingSnapshot = false;
  private readonly pending = new Map<string, NodeActivity>();
  private readonly seen = new Set<string>();
  private nodes: readonly KnowledgeNode[] = [];
  private snapshotCursor = 0;
  constructor(private readonly read: (options: Record<string, unknown>) => Promise<Record<string, unknown> | null>,
    private readonly refresh: () => void, private readonly show: (receipt: NodeActivity) => void,
    private readonly clear: () => void, private readonly now = Date.now) {}

  start(): void {
    if (this.active) return;
    this.active = true; this.epoch++; this.visibleSince = this.now();
    if (this.committed && !this.waitingSnapshot) this.schedule(2500);
  }
  stop(): void {
    this.active = false; this.epoch++;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null; this.pending.clear(); this.clear();
  }
  reset(): void {
    this.stop(); this.committed = null; this.waitingSnapshot = false; this.seen.clear(); this.nodes = [];
  }
  snapshot(value: unknown, nodes: readonly KnowledgeNode[]): void {
    if (!this.active) return;
    const next = checkpoint(value);
    if (!next) return;
    this.nodes = nodes; this.snapshotCursor = next.cursor;
    if (!this.committed || this.waitingSnapshot || next.stream_id !== this.committed.stream_id) {
      this.committed = next; this.waitingSnapshot = false; this.pending.clear(); this.seen.clear(); this.clear();
    }
    this.flush(); this.schedule(2500);
  }
  diagnostics(): { cursor: number | null; pending: number; seen: number; active: boolean } {
    return { cursor: this.committed?.cursor ?? null, pending: this.pending.size, seen: this.seen.size, active: this.active };
  }
  private flush(): void {
    const now = this.now();
    for (const [id, event] of this.pending) {
      if (event.at * 1000 < this.visibleSince || event.at * 1000 > now || now - event.at * 1000 >= ACTIVITY_TTL_MS) {
        this.pending.delete(id); continue;
      }
      if (event.sequence > this.snapshotCursor) continue;
      const node = this.nodes.find(node => node.id === id && node.sourceVersion === event.version && node.sourceTarget === event.target_id && !node.evidence.retracted);
      this.pending.delete(id);
      if (node) this.show(event);
    }
  }
  private schedule(delay: number): void {
    if (!this.active || this.waitingSnapshot || !this.committed || this.timer !== null) return;
    this.timer = setTimeout(() => { this.timer = null; void this.tick(); }, delay);
  }
  private async tick(): Promise<void> {
    if (!this.active || !this.committed || this.waitingSnapshot) return;
    if (this.busy) { this.schedule(2500); return; }
    this.busy = true;
    const epoch = this.epoch, before = this.committed;
    let delay = 2500;
    try {
      const page = await this.read({ activity: true, after: before.cursor, stream_id: before.stream_id, limit: 200 });
      if (!this.active || epoch !== this.epoch || !page || page.ok !== true) return;
      const next = checkpoint(page);
      if (!next) return;
      if (page.reset === true || next.stream_id !== before.stream_id || next.cursor < before.cursor) {
        this.pending.clear(); this.seen.clear(); this.clear(); this.waitingSnapshot = true; this.refresh(); return;
      }
      if (page.reset !== false || !integer(page.latest) || next.cursor > page.latest || !Array.isArray(page.items)
        || page.items.length > 200 || typeof page.has_more !== 'boolean' || page.has_more && next.cursor === before.cursor) return;
      // Validate the entire ordered page before consuming any receipt or
      // moving the journal cursor. Otherwise a reversed/corrupt row can be
      // silently skipped while a later cursor makes that loss permanent.
      const staged: Array<NodeActivity | StructuralActivity> = [];
      const pageIds = new Set<string>();
      let previous = before.cursor;
      for (const raw of page.items) {
        const event = receipt(raw);
        if (!event || event.sequence <= previous || event.sequence > next.cursor
          || pageIds.has(event.event_id) || this.seen.has(event.event_id)) return;
        staged.push(event); pageIds.add(event.event_id); previous = event.sequence;
      }
      let needsSnapshot = false;
      for (const event of staged) {
        needsSnapshot ||= event.sequence > this.snapshotCursor; this.seen.add(event.event_id);
        if (this.seen.size > 400) this.seen.delete(this.seen.values().next().value!);
        if (event.kind === 'removed' || event.kind === 'relations_changed') continue;
        const now = this.now(), at = event.at * 1000;
        if (at < this.visibleSince || at > now || now - at >= ACTIVITY_TTL_MS) continue;
        this.pending.set(event.document_id as string, event as NodeActivity);
        if (this.pending.size > 120) this.pending.delete(this.pending.keys().next().value!);
      }
      this.committed = next;
      if (needsSnapshot) this.refresh();
      this.flush();
      if (page.has_more) delay = 500;
    } catch { /* Keep the cursor; retry the same page with a bounded delay. */ }
    finally { this.busy = false; this.schedule(delay); }
  }
}
