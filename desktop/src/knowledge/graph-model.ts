import type { LayoutAffinity } from './layout-affinity';

export const DEFAULT_FOCUS_HOPS = 2;
export const MAX_FOCUS_HOPS = 3;
export const FOCUS_NEIGHBOR_LIMIT = 10;
export const ON_SCREEN_NODE_CAP = 24;
export const OVERVIEW_NODE_CAP = 120;
export const OVERVIEW_LOD_CAP = 2048;
export const NAVIGATION_HISTORY_LIMIT = 32;

export interface KnowledgeEvidence {
  kind: "seed" | "ledger" | "snapshot";
  sourceEventIds: string[];
  chatId: string;
  roomIds?: string[];
  confirmedAt: string | null;
  retracted: boolean;
}

export interface KnowledgeNode {
  id: string;
  label: string;
  category: string;
  importance: number;
  updatedAt: number;
  evidence: KnowledgeEvidence;
  description?: string;
  facts?: string[];
  space?: string;
  isHub?: boolean;
  oskId?: string;
  canonicalId?: string;
  sourceVersion?: string;
  sourceTarget?: string;
  sourcePlatform?: string;
  sourceUrl?: string;
  degree?: number;
  degreeScope?: string;
}

export interface KnowledgeEdge {
  source: string;
  relation: string;
  target: string;
  context: string;
  weight: number;
  roomId: string;
  validFrom: string;
  validTo: string;
  evidenceMessageId: string;
  evidence: KnowledgeEvidence;
  purpose?: 'semantic' | 'navigation' | 'reference';
}

export interface KnowledgeGraph {
  layoutAffinity?: LayoutAffinity;
  overviewBudget?: number;
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
}

export interface KnowledgeView extends KnowledgeGraph {
  focusId: string | null;
  hops: number;
  overviewLimit?: number;
  overviewOffset?: number;
  hasMoreContexts?: boolean;
}

const EMPTY_EVIDENCE: KnowledgeEvidence = Object.freeze({
  kind: "seed",
  sourceEventIds: [],
  chatId: "",
  confirmedAt: null,
  retracted: false,
});

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function parseEvidence(value: unknown): KnowledgeEvidence {
  const item = objectValue(value);
  if (!item) return { ...EMPTY_EVIDENCE, sourceEventIds: [] };
  const ids = Array.isArray(item.source_event_ids)
    ? item.source_event_ids.filter((entry): entry is string => typeof entry === "string").slice(0, 16)
    : [];
  return {
    kind: item.kind === 'decision_ledger' ? 'ledger' : item.kind === 'local_db_snapshot' ? 'snapshot'
      : item.kind === "ledger" || item.kind === "snapshot" ? item.kind : "seed",
    sourceEventIds: ids,
    chatId: stringValue(item.chat_id),
    roomIds: Array.isArray(item.room_ids) ? [...new Set(item.room_ids.filter((entry): entry is string => typeof entry === 'string' && /^(?:\d+|kakao:[0-9a-f]{64}:room:\d+)$/.test(entry)))].slice(0, 256) : [],
    confirmedAt: typeof item.confirmed_at === "string" ? item.confirmed_at : null,
    retracted: item.retracted === true,
  };
}

export function parseKnowledgeGraph(payload: Record<string, unknown> | null, nowMs = Date.now()): KnowledgeGraph {
  const nodes = Array.isArray(payload?.nodes)
    ? payload.nodes.flatMap((raw): KnowledgeNode[] => {
      const item = objectValue(raw);
      if (!item) return [];
      const id = stringValue(item.id);
      const label = stringValue(item.label);
      if (!id || !label || id.startsWith("message:") || id.startsWith("msg:")) return [];
      const evidence = parseEvidence(item.evidence);
      if (evidence.retracted) return [];
      return [{
        id,
        label,
        category: stringValue(item.category),
        importance: Math.max(0, Math.min(100, numberValue(item.importance))),
        updatedAt: Math.max(0, numberValue(item.updated_at)),
        evidence,
        description: stringValue(item.description).slice(0, 2400),
        facts: Array.isArray(item.facts) ? item.facts.filter((entry): entry is string => typeof entry === 'string').slice(0, 6).map(entry => entry.slice(0, 600)) : [],
        space: stringValue(item.space),
        isHub: item.is_hub === true,
        oskId: stringValue(item.osk_id),
        canonicalId: stringValue(item.canonical_id),
        sourceVersion: stringValue(item.source_version).slice(0, 256),
        sourceTarget: stringValue(item.source_target).slice(0, 256),
        sourcePlatform: stringValue(item.source_platform).slice(0, 32),
        sourceUrl: stringValue(item.source_url).slice(0, 4096),
        degree: typeof item.degree === 'number' && Number.isFinite(item.degree) ? Math.max(0, Math.floor(item.degree)) : undefined,
        degreeScope: stringValue(item.degree_scope),
      }];
    })
    : [];
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = Array.isArray(payload?.edges)
    ? payload.edges.flatMap((raw): KnowledgeEdge[] => {
      const item = objectValue(raw);
      if (!item) return [];
      const source = stringValue(item.source);
      const target = stringValue(item.target);
      if (!nodeIds.has(source) || !nodeIds.has(target)) return [];
      const evidence = parseEvidence(item.evidence), validTo = stringValue(item.valid_to), validFrom = stringValue(item.valid_from);
      const until = Date.parse(validTo);
      const from = Date.parse(validFrom);
      if (evidence.retracted || (Number.isFinite(until) && until <= nowMs) || Number.isFinite(from) && from > nowMs) return [];
      return [{
        source,
        relation: stringValue(item.relation),
        target,
        context: stringValue(item.context),
        weight: Math.max(0, numberValue(item.weight)),
        roomId: stringValue(item.room_id),
        validFrom,
        validTo,
        evidenceMessageId: stringValue(item.evidence_message_id),
        evidence,
        purpose: item.purpose === 'navigation' || item.purpose === 'reference' ? item.purpose : 'semantic',
      }];
    })
    : [];
  return { nodes, edges, overviewBudget: payload?.overview_budget === OVERVIEW_LOD_CAP ? OVERVIEW_LOD_CAP : undefined };
}

function subgraph(graph: KnowledgeGraph, ids: string[], focusId: string | null, hops: number): KnowledgeView {
  const keep = new Set(ids.slice(0, focusId === null ? OVERVIEW_LOD_CAP : ON_SCREEN_NODE_CAP));
  return {
    layoutAffinity: graph.layoutAffinity,
    nodes: graph.nodes.filter((node) => keep.has(node.id)),
    edges: graph.edges.filter((edge) => keep.has(edge.source) && keep.has(edge.target)),
    focusId,
    hops,
  };
}

/** Cosmic global view: every displayed dot is a real stored memory. Pages
 * bound the scene independently of the smaller local-neighbourhood budget.
 * Space/category does not choose global positions or hide disconnected notes.
 */
export function overviewGraph(graph: KnowledgeGraph, nodeLimit=OVERVIEW_NODE_CAP, nodeOffset=0, nowMs=Date.now()): KnowledgeView {
  const memories=(graph.overviewBudget ? graph.nodes : graph.nodes.filter(node=>!node.isHub&&!node.evidence.retracted));
  const candidates=memories.length?memories:graph.nodes.filter(node=>!node.evidence.retracted);
  const degree=new Map<string,Set<string>>();
  for(const edge of graph.edges) {
    const from=Date.parse(edge.validFrom),until=Date.parse(edge.validTo);
    if(edge.purpose==='navigation'||edge.evidence.retracted||edge.source===edge.target
      ||Number.isFinite(from)&&from>nowMs||Number.isFinite(until)&&until<=nowMs)continue;
    for(const [id,other] of [[edge.source,edge.target],[edge.target,edge.source]]) {
      const neighbours=degree.get(id)??new Set<string>();neighbours.add(other);degree.set(id,neighbours);
    }
  }
  const ordered=graph.overviewBudget ? candidates : [...candidates].sort((a,b)=>(degree.get(b.id)?.size??0)-(degree.get(a.id)?.size??0)||b.importance-a.importance||a.id.localeCompare(b.id));
  const limit=Number.isFinite(nodeLimit)?Math.min(OVERVIEW_LOD_CAP,Math.max(1,Math.floor(nodeLimit))):OVERVIEW_NODE_CAP;
  const offset=Number.isFinite(nodeOffset)?Math.min(Math.max(0,Math.floor(nodeOffset)),Math.max(0,Math.floor((ordered.length-1)/limit)*limit)):0;
  return {...subgraph(graph,ordered.slice(offset,offset+limit).map(n=>n.id),null,0),overviewLimit:limit,overviewOffset:offset,hasMoreContexts:offset+limit<ordered.length};
}

export function kHopNodeIds(
  graph: KnowledgeGraph,
  rootId: string,
  hops: number,
  neighborLimit = FOCUS_NEIGHBOR_LIMIT,
  nodeCap = ON_SCREEN_NODE_CAP,
): string[] {
  if (!graph.nodes.some((node) => node.id === rootId)) return [];
  const boundedHops = Math.min(MAX_FOCUS_HOPS, Math.max(0, Math.floor(hops)));
  const cap = Math.max(1, Math.floor(nodeCap));
  const ordered = [rootId];
  const visited = new Set(ordered);
  let frontier = new Set(ordered);

  for (let hop = 0; hop < boundedHops && ordered.length < cap; hop += 1) {
    const strongest = new Map<string, number>();
    for (const edge of graph.edges) {
      let other = "";
      if (frontier.has(edge.source)) other = edge.target;
      else if (frontier.has(edge.target)) other = edge.source;
      if (!other || visited.has(other)) continue;
      strongest.set(other, Math.max(strongest.get(other) ?? 0, edge.weight));
    }
    const next = [...strongest.entries()]
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, Math.max(1, neighborLimit))
      .map(([id]) => id)
      .slice(0, cap - ordered.length);
    if (next.length === 0) break;
    next.forEach((id) => {
      visited.add(id);
      ordered.push(id);
    });
    frontier = new Set(next);
  }
  return ordered;
}

export class KnowledgeDrilldown {
  private focusId: string | null = null;
  private hops = 0;
  private contextLimit=OVERVIEW_NODE_CAP;
  private contextOffset=0;
  private readonly history: Array<{ focusId: string | null; hops: number; contextLimit:number; contextOffset:number }> = [];

  constructor(private graph: KnowledgeGraph) { this.contextLimit = graph.overviewBudget ?? OVERVIEW_NODE_CAP; }

  replaceGraph(graph: KnowledgeGraph): KnowledgeView {
    this.graph = graph;
    this.contextLimit = graph.overviewBudget ?? OVERVIEW_NODE_CAP;
    const ids = new Set(graph.nodes.map(node => node.id));
    this.history.splice(0, this.history.length, ...this.history.filter(entry => entry.focusId === null || ids.has(entry.focusId)));
    if (this.focusId && !ids.has(this.focusId)) { this.focusId = null; this.hops = 0; }
    return this.current();
  }

  restore(graph: KnowledgeGraph, state: { focusId: string | null; hops: number }): KnowledgeView {
    this.graph = graph;
    this.contextLimit = graph.overviewBudget ?? OVERVIEW_NODE_CAP;
    this.focusId = graph.nodes.some(node => node.id === state.focusId) ? state.focusId : null;
    this.hops = this.focusId ? Math.min(MAX_FOCUS_HOPS, Math.max(0, state.hops)) : 0;
    this.contextOffset = 0;
    this.contextLimit = graph.overviewBudget ?? OVERVIEW_NODE_CAP;
    this.history.length = 0;
    return this.current();
  }

  current(): KnowledgeView {
    if (!this.focusId) return overviewGraph(this.graph,this.contextLimit,this.contextOffset);
    if(this.hops===0){
      const overview=overviewGraph(this.graph,this.contextLimit,this.contextOffset);
      if(!overview.nodes.some(n=>n.id===this.focusId)){
        const ids=overview.nodes.slice(0,this.contextLimit-1).map(n=>n.id);ids.push(this.focusId);
        return {...subgraph(this.graph,ids,null,0),focusId:this.focusId,overviewLimit:overview.overviewLimit,overviewOffset:overview.overviewOffset,hasMoreContexts:overview.hasMoreContexts};
      }
      return {...overview,focusId:this.focusId};
    }
    return subgraph(
      this.graph,
      kHopNodeIds(this.graph, this.focusId, this.hops),
      this.focusId,
      this.hops,
    );
  }

  clickNode(nodeId: string): KnowledgeView {
    if (!this.graph.nodes.some((node) => node.id === nodeId)) return this.current();
    this.remember(nodeId, DEFAULT_FOCUS_HOPS);
    this.focusId = nodeId;
    this.hops = DEFAULT_FOCUS_HOPS;
    return this.current();
  }

  openNote(nodeId:string):KnowledgeView {
    if(!this.graph.nodes.some(n=>n.id===nodeId))return this.current();
    this.remember(nodeId,0);this.focusId=nodeId;this.hops=0;return this.current();
  }

  expandOneHop(): KnowledgeView {
    if (this.focusId) {
      const next = Math.min(MAX_FOCUS_HOPS, Math.max(DEFAULT_FOCUS_HOPS, this.hops + 1));
      this.remember(this.focusId, next);
      this.hops = next;
    } else if(this.current().hasMoreContexts) {
      const nextLimit=OVERVIEW_NODE_CAP;
      const nextOffset=this.contextOffset+OVERVIEW_NODE_CAP;
      this.remember(null,0,nextLimit,nextOffset);this.contextLimit=nextLimit;this.contextOffset=nextOffset;
    }
    return this.current();
  }

  reset(): KnowledgeView {
    this.remember(null, 0,OVERVIEW_NODE_CAP,0);
    this.focusId = null;
    this.hops = 0;
    this.contextLimit=this.graph.overviewBudget??OVERVIEW_NODE_CAP;this.contextOffset=0;
    return this.current();
  }

  get canGoBack(): boolean { return this.history.length > 0; }

  back(): KnowledgeView {
    const previous = this.history.pop();
    if (previous) {
      this.focusId = previous.focusId;
      this.hops = previous.hops;
      this.contextLimit=previous.contextLimit;this.contextOffset=previous.contextOffset;
    }
    return this.current();
  }

  private remember(focusId: string | null, hops: number,contextLimit=this.contextLimit,contextOffset=this.contextOffset): void {
    if (this.focusId === focusId && this.hops === hops && this.contextLimit===contextLimit && this.contextOffset===contextOffset) return;
    this.history.push({ focusId: this.focusId, hops: this.hops,contextLimit:this.contextLimit,contextOffset:this.contextOffset });
    if (this.history.length > NAVIGATION_HISTORY_LIMIT) this.history.shift();
  }
}
