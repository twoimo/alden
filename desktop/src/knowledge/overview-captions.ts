/** Captions from stored subjects and explicit navigation lists, never inferred
 * knowledge. Reference-list witnesses prove visible links, not read bodies. */
import type { KnowledgeEdge, KnowledgeNode } from './graph-model';
import { internalGraphLabel, overviewRoutingStar, selectOverviewLabels, selectZoomLabelCandidates } from './overview-labels';
import { relationCurrentlyValid } from './relation-validity';

export type Caption = { node: KnowledgeNode; role:'topic'|'reference-section'|'title'; witnesses:readonly string[] };
const routing = /(?:\bindex\b|\bworkspace\b|\bcatalog\b|색인|카탈로그|보관함|작업\s*공간|과거\s*자료|전체\s*공개\s*자료|knowledge\s+graph$|통합\s*저장소|^verified\s*reviews?\s*only$)/i;
export function captionCandidates(nodes:readonly KnowledgeNode[],edges:readonly KnowledgeEdge[],now=Date.now(),rankedNodes=selectZoomLabelCandidates(nodes)):Caption[]{
  const byId=new Map(nodes.map(n=>[n.id,n]));
  const links=new Map<string,Set<string>>();
  for(const edge of edges){
    if(edge.evidence.retracted||!relationCurrentlyValid(edge,now))continue;
    for(const [from,to] of [[edge.source,edge.target],[edge.target,edge.source]]){
      const a=byId.get(from),b=byId.get(to);
      if(!a||!b||a.evidence.retracted||b.evidence.retracted||!a.sourceVersion||!b.sourceVersion
        ||!a.sourceTarget||a.sourceTarget!==b.sourceTarget)continue;
      const set=links.get(from)??new Set<string>();set.add(to);links.set(from,set);
    }
  }
  const topics=rankedNodes.filter(n=>/^(topic|concept|subject|주제|개념)$/i.test(n.category));
  const subjects:Caption[]=topics.map(node=>({node,role:'topic',witnesses:[]}));
  for(const node of nodes){
    if(!['hub','index'].includes(node.category)||node.evidence.retracted||!node.sourceVersion||!node.sourceTarget)continue;
    const label=node.label.normalize('NFC').replace(/\s+/g,' ').trim(),witnesses=[...(links.get(node.id)??[])];
    if(witnesses.length<2||[...label].length>48||routing.test(label)||internalGraphLabel(label)||overviewRoutingStar(label,node.category))continue;
    if(label.split(/[·\s/()]+/).filter(term=>/[A-Za-z가-힣]{2}/.test(term)).length<2)continue;
    subjects.push({node,role:'reference-section',witnesses});
  }
  return subjects.sort((a,b)=>(b.node.degree??0)-(a.node.degree??0)||a.node.label.localeCompare(b.node.label)||a.node.id.localeCompare(b.node.id));
}
export function selectOverviewCaptions(nodes:readonly KnowledgeNode[],edges:readonly KnowledgeEdge[],cap=8,now=Date.now()):Caption[]{
  const limit=Math.min(8,Math.max(0,Math.floor(Number.isFinite(cap)?cap:0)));
  if(!limit)return[];
  const ranked=selectZoomLabelCandidates(nodes);
  const primary=captionCandidates(nodes,edges,now,ranked);
  const titles=ranked.filter(n=>!['대화방','채팅방','room','chat_room'].includes(n.category));
  // Fallback stays visibly a title when no explicit subject/section is saved.
  if(!primary.length)return selectOverviewLabels(nodes.filter(n=>!['대화방','채팅방','room','chat_room'].includes(n.category)),limit).map(node=>({node,role:'title',witnesses:[]}));
  const groups=new Map<string,Caption[]>();
  for(const candidate of primary){const key=candidate.node.sourceTarget||candidate.node.space||candidate.node.id;const rows=groups.get(key)??[];rows.push(candidate);groups.set(key,rows);}
  const primaryIds=new Set(primary.map(c=>c.node.id));
  const eligibleTitles=titles.filter(n=>!primaryIds.has(n.id));
  const primaryGroups=new Set(primary.map(c=>c.node.sourceTarget||c.node.space||c.node.id));
  const uncovered=eligibleTitles.filter(n=>!primaryGroups.has(n.sourceTarget||n.space||n.id));
  const primaryLimit=uncovered.length?Math.min(limit,Math.ceil(limit*.75)):limit;
  const chosen:Caption[]=[],used=new Set<string>();
  for(let round=0;chosen.length<primaryLimit;round++){
    let available=false;
    for(const rows of groups.values()){
      const row=rows[round];if(!row)continue;available=true;
      const key=row.node.label.normalize('NFC').replace(/\s+/g,' ').trim();
      if(!used.has(key)&&chosen.length<primaryLimit){chosen.push(row);used.add(key);}
    }
    if(!available)break;
  }
  const represented=new Set(chosen.map(c=>c.node.sourceTarget||c.node.space||c.node.id));
  for(const node of uncovered){
    const key=node.sourceTarget||node.space||node.id,text=node.label.normalize('NFC').replace(/\s+/g,' ').trim();
    if(chosen.length>=limit)break;
    if(represented.has(key)||used.has(text))continue;
    chosen.push({node,role:'title',witnesses:[]});represented.add(key);used.add(text);
  }
  return chosen;
}

/** A heading is an existing subject or a saved list; leaf titles remain titles. */
export function overviewCaptionRole(node: Pick<KnowledgeNode,'category'>): Caption['role'] {
  if (/^(topic|concept|subject|주제|개념)$/i.test(node.category)) return 'topic';
  return ['hub','index'].includes(node.category) ? 'reference-section' : 'title';
}
