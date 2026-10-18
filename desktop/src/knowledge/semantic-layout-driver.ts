import type { KnowledgeView } from './graph-model';
import type { LayoutAffinityPair } from './layout-affinity';
import type { Point3 } from './plasticity';
import type { SemanticLayoutCheckpoint, SemanticLayoutDiagnostics } from './semantic-layout';
import { relationCurrentlyValid } from './relation-validity';

export interface LayoutRequest { id: number; view: KnowledgeView; previous: Array<[string, Point3]>;
  pairs: readonly LayoutAffinityPair[]; changed: string[]; pinned: string[]; reflow?: boolean; checkpoint?: SemanticLayoutCheckpoint }
export interface LayoutResponse { id: number; anchors?: Array<[string, Point3]>;
  diagnostics?: SemanticLayoutDiagnostics; checkpoint?: SemanticLayoutCheckpoint; wallMs?: number; error?: string }
export interface LayoutWorkerPort { onmessage: ((event: MessageEvent<LayoutResponse>) => void) | null;
  onerror: ((event: Event) => void) | null; postMessage(value: LayoutRequest): void; terminate(): void }

function affinityAuthorityKey(view:KnowledgeView):string {
  const affinity=view.layoutAffinity;
  return JSON.stringify(affinity?[affinity.state,affinity.inputKey,affinity.profileKey]:null);
}

function snapshotKey(view:KnowledgeView,pinned:ReadonlySet<string>):string {
  const now=Date.now();
  return JSON.stringify([view.nodes.map(n=>[n.id,n.sourceVersion,n.sourceTarget,n.importance,n.evidence.retracted]),
    view.edges.map(e=>[e.source,e.target,e.weight,e.purpose,e.validFrom,e.validTo,e.evidence.retracted,relationCurrentlyValid(e,now)]),
    affinityAuthorityKey(view),view.layoutAffinity?.pairs??[],[...pinned].sort()]);
}

/** One latest snapshot, one owned worker; superseded/hidden jobs cannot apply.
 * The last completed bounded cache survives a cancelled job without retaining
 * authority across clear/dispose. No layout solver runs on the UI thread. */
export class SemanticLayoutDriver {
  private worker: LayoutWorkerPort | null = null;
  private checkpoint?: SemanticLayoutCheckpoint;
  private completedPositions?: SemanticLayoutCheckpoint['positions'];
  private affinityAuthority?: string;
  private generation = 0;
  private appliedKey = '';
  private requestedKey = '';
  private requestedInputs = '';
  private appliedPins = '';
  private busy = false;
  private pendingReflow = false;
  private requestedPins = '';
  private state: Record<string, unknown> = { state: 'idle' };
  private completedState:Record<string,unknown>={state:'idle'};
  constructor(private readonly apply: (result: LayoutResponse) => void,
    private readonly create: () => LayoutWorkerPort = () => new Worker(new URL('./semantic-layout-worker.ts', import.meta.url), { type: 'module' }) as unknown as LayoutWorkerPort) {}
  get diagnostics() { return { ...this.state }; }
  get ready(): boolean { return !!this.checkpoint; }
  get settled(): boolean { return !this.busy && !!this.checkpoint && this.appliedKey === this.requestedKey
    && (this.state.state==='ready'||this.state.state==='cached'); }

  /** An identical completed snapshot may restore its colour metadata on a
   * rebuild. Matching IDs alone never confer permission to reuse labels. */
  labelsFor(view:KnowledgeView,pinned:ReadonlySet<string>):ReadonlyArray<[string,string]>|undefined {
    return this.appliedKey===snapshotKey(view,pinned)?this.checkpoint?.labels:undefined;
  }

  request(view: KnowledgeView, previous: ReadonlyMap<string, Point3>, changed: ReadonlySet<string>, pinned: ReadonlySet<string>,
    provisional: ReadonlySet<string> = new Set(), reflow = false): void {
    const authority=affinityAuthorityKey(view);
    if(this.affinityAuthority!==undefined&&this.affinityAuthority!==authority){
      // Workers retain their engines even without a restore checkpoint. Drop
      // that engine and all membership metadata before any cache/pin shortcut.
      // Completed coordinates remain only a geometric prior, never labels.
      const positions=this.completedPositions;
      this.clear();this.completedPositions=positions;
    }
    this.affinityAuthority=authority;
    const pairs = view.layoutAffinity?.pairs ?? [];
    const visibleIds=new Set(view.nodes.filter(node=>!node.evidence.retracted).map(node=>node.id));
    const compact = { ...view, nodes: view.nodes.map(n => ({ ...n, label: n.id, description: '', facts: [] })),
      edges: view.edges.map(e => ({ ...e, context: '', evidenceMessageId: '', evidence: { ...e.evidence, sourceEventIds: [] } })) };
    const key = snapshotKey(view,pinned);
    const pinCoordinates=()=>[...pinned].filter(id=>visibleIds.has(id)).sort().map(id=>{
      const p=previous.get(id);return [id,p?.x??null,p?.y??null,p?.z??null];
    });
    if(pinCoordinates().some(row=>!row.slice(1).every(value=>typeof value==='number'&&Number.isFinite(value)))){
      this.stop();this.requestedKey=key;this.state={state:'failed',reason:'semantic_layout_pin_unavailable'};return;
    }
    const pinKey=JSON.stringify(pinCoordinates());
    const changedVisible=[...changed].filter(id=>visibleIds.has(id)).sort();
    const inputs=JSON.stringify([key,pinKey,changedVisible,[...provisional].filter(id=>visibleIds.has(id)).sort(),reflow]);
    const restore = [...previous.keys()].some(id => visibleIds.has(id)&&provisional.has(id));
    // An ordinary redraw cannot cancel a user-requested reflow of the same
    // content and pins while its worker is still solving.
    if (!reflow && this.busy && this.pendingReflow && key === this.requestedKey
      && pinKey === this.requestedPins) return;
    if(!reflow&&key===this.appliedKey&&!restore&&!changedVisible.length&&pinKey===this.appliedPins){
      if(this.busy)this.stop();
      this.requestedKey=key;this.state={...this.completedState,state:'cached',generation:this.generation};return;
    }
    if(this.busy&&inputs===this.requestedInputs)return;
    // Cancel obsolete work immediately; restore only the last completed cache.
    if (this.busy) this.stop();
    const worker = this.worker ?? this.create(); this.worker = worker;
    const id = ++this.generation; this.busy = true; this.pendingReflow = reflow; this.requestedPins = pinKey;
    this.requestedKey = key; this.requestedInputs=inputs; this.state = { state: 'pending', generation: id };
    worker.onmessage = event => {
      if (this.worker !== worker || event.data.id !== id || id !== this.generation) return;
      this.busy = false; this.pendingReflow = false;
      // A boundary can pass while the main thread is unable to run its timer.
      // Discard that reply before publishing any position or membership state.
      if(snapshotKey(view,pinned)!==key||JSON.stringify(pinCoordinates())!==pinKey){
        this.request(view,previous,changed,pinned,provisional,reflow);return;
      }
      if (event.data.error || !event.data.anchors || !event.data.diagnostics || !event.data.checkpoint) {
        this.state = { state: 'failed', reason: event.data.error ?? 'semantic_layout_invalid_reply' }; return;
      }
      this.appliedKey = key; this.appliedPins=pinKey; this.checkpoint = event.data.checkpoint;
      this.completedPositions=this.checkpoint.positions;
      this.state = { state: 'ready', generation: id, wallMs: event.data.wallMs, ...event.data.diagnostics, ...priorBasis };
      this.completedState={...this.state};
      this.apply(event.data);
    };
    worker.onerror = () => {
      if (this.worker !== worker || id !== this.generation) return;
      this.busy = false; this.pendingReflow = false; this.state = { state: 'failed', reason: 'semantic_layout_worker_failed' };
    };
    // A new identity's ID seed is provisional even after another page solved.
    // Retain completed identities and explicit pins; unrelated cached IDs do
    // not make new seeds an authoritative prior community center.
    const solved = new Map(this.completedPositions ?? []);
    // Animation/physics coordinates depend on when an asynchronous affinity
    // reply arrives. Preserve the completed solver snapshot as the next solve's
    // prior; only an explicit pin takes the currently shown source position.
    // With no authority-valid checkpoint, explicitly send known solver points
    // for provisional identities; their current presentation seeds stay out.
    const retained = [...previous].filter(([id]) => visibleIds.has(id)
      && (pinned.has(id) || solved.has(id) && (!provisional.has(id) || !this.checkpoint)))
      .map(([id,p]) => [id,pinned.has(id)?p:solved.get(id)!] as [string,Point3]);
    const priorBasis={priorSource:'completed_solver_or_explicit_pin',retainedPriorNodes:retained.length,
      explicitPinPriorNodes:retained.filter(([key])=>pinned.has(key)).length,
      restoredProvisionalNodes:[...visibleIds].filter(key=>provisional.has(key)&&solved.has(key)&&!pinned.has(key)).length};
    this.state={...this.state,...priorBasis};
    worker.postMessage({ id, view: compact, previous: retained.map(([key, p]) => [key, { x: p.x, y: p.y, z: p.z }]),
      pairs, changed: [...changed], pinned: [...pinned], reflow, checkpoint: this.checkpoint });
  }

  stop(): void { this.generation++; this.worker?.terminate(); this.worker = null; this.busy = false; this.pendingReflow = false; this.requestedPins = ''; this.requestedKey = ''; this.requestedInputs=''; }
  clear(): void { this.stop(); this.checkpoint = undefined; this.completedPositions=undefined; this.affinityAuthority=undefined; this.appliedKey = ''; this.appliedPins=''; this.state = { state: 'idle' };this.completedState={state:'idle'}; }
}
