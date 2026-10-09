import type { KnowledgeView } from './graph-model';
import type { LayoutAffinityPair } from './layout-affinity';
import type { Point3 } from './plasticity';
import type { SemanticLayoutCheckpoint, SemanticLayoutDiagnostics } from './semantic-layout';

export interface LayoutRequest { id: number; view: KnowledgeView; previous: Array<[string, Point3]>;
  pairs: readonly LayoutAffinityPair[]; changed: string[]; pinned: string[]; checkpoint?: SemanticLayoutCheckpoint }
export interface LayoutResponse { id: number; anchors?: Array<[string, Point3]>;
  diagnostics?: SemanticLayoutDiagnostics; checkpoint?: SemanticLayoutCheckpoint; wallMs?: number; error?: string }
export interface LayoutWorkerPort { onmessage: ((event: MessageEvent<LayoutResponse>) => void) | null;
  onerror: ((event: Event) => void) | null; postMessage(value: LayoutRequest): void; terminate(): void }

/** One latest snapshot, one owned worker; superseded/hidden jobs cannot apply.
 * The last completed bounded cache survives a cancelled job without retaining
 * authority across clear/dispose. No layout solver runs on the UI thread. */
export class SemanticLayoutDriver {
  private worker: LayoutWorkerPort | null = null;
  private checkpoint?: SemanticLayoutCheckpoint;
  private generation = 0;
  private appliedKey = '';
  private requestedKey = '';
  private busy = false;
  private state: Record<string, unknown> = { state: 'idle' };
  constructor(private readonly apply: (result: LayoutResponse) => void,
    private readonly create: () => LayoutWorkerPort = () => new Worker(new URL('./semantic-layout-worker.ts', import.meta.url), { type: 'module' }) as unknown as LayoutWorkerPort) {}
  get diagnostics() { return { ...this.state }; }
  get ready(): boolean { return !!this.checkpoint; }

  request(view: KnowledgeView, previous: ReadonlyMap<string, Point3>, changed: ReadonlySet<string>, pinned: ReadonlySet<string>): void {
    const pairs = view.layoutAffinity?.pairs ?? [];
    const compact = { ...view, nodes: view.nodes.map(n => ({ ...n, label: n.id, description: '', facts: [] })),
      edges: view.edges.map(e => ({ ...e, context: '', evidenceMessageId: '', evidence: { ...e.evidence, sourceEventIds: [] } })) };
    const key = JSON.stringify([compact.nodes.map(n => [n.id, n.sourceVersion, n.sourceTarget, n.importance]),
      compact.edges.map(e => [e.source, e.target, e.weight, e.purpose, e.validFrom, e.validTo, e.evidence.retracted]), pairs, [...pinned].sort()]);
    if (key === this.appliedKey || this.busy && key === this.requestedKey) return;
    // Cancel obsolete work immediately; restore only the last completed cache.
    if (this.busy) this.stop();
    const worker = this.worker ?? this.create(); this.worker = worker;
    const id = ++this.generation; this.busy = true; this.requestedKey = key; this.state = { state: 'pending', generation: id };
    worker.onmessage = event => {
      if (this.worker !== worker || event.data.id !== id || id !== this.generation) return;
      this.busy = false;
      if (event.data.error || !event.data.anchors || !event.data.diagnostics || !event.data.checkpoint) {
        this.state = { state: 'failed', reason: event.data.error ?? 'semantic_layout_invalid_reply' }; return;
      }
      this.appliedKey = key; this.checkpoint = event.data.checkpoint;
      this.state = { state: 'ready', generation: id, wallMs: event.data.wallMs, ...event.data.diagnostics };
      this.apply(event.data);
    };
    worker.onerror = () => {
      if (this.worker !== worker || id !== this.generation) return;
      this.busy = false; this.state = { state: 'failed', reason: 'semantic_layout_worker_failed' };
    };
    // Initial ID seeds are a pending display, not a prior solved geometry.
    // Averaging that random volume collapses unrelated cold communities.
    const retained = this.checkpoint ? [...previous] : [...previous].filter(([id]) => pinned.has(id));
    worker.postMessage({ id, view: compact, previous: retained.map(([key, p]) => [key, { x: p.x, y: p.y, z: p.z }]),
      pairs, changed: [...changed], pinned: [...pinned], checkpoint: this.checkpoint });
  }

  stop(): void { this.generation++; this.worker?.terminate(); this.worker = null; this.busy = false; this.requestedKey = ''; }
  clear(): void { this.stop(); this.checkpoint = undefined; this.appliedKey = ''; this.state = { state: 'idle' }; }
}
