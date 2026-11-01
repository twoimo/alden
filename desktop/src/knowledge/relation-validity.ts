import type { KnowledgeEdge } from './graph-model';

export function relationCurrentlyValid(edge:Pick<KnowledgeEdge,'validFrom'|'validTo'>,now=Date.now()):boolean {
  const from=Date.parse(edge.validFrom),until=Date.parse(edge.validTo);
  return !(Number.isFinite(from)&&from>now||Number.isFinite(until)&&until<=now);
}
/** The next boundary among the already-authorized visible relations only. */
export function nextRelationBoundary(edges:readonly KnowledgeEdge[],now=Date.now()):number|null {
  let next=Infinity;
  for(const edge of edges.slice(0,32768)){
    if(edge.evidence.retracted||edge.purpose==='navigation')continue;
    for(const time of [Date.parse(edge.validFrom),Date.parse(edge.validTo)])if(Number.isFinite(time)&&time>now)next=Math.min(next,time);
  }
  return Number.isFinite(next)?next:null;
}
