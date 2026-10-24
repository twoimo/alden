import {describe,expect,it} from 'vitest';
import {parseKnowledgeGraph} from '../knowledge/graph-model';
import {captionCandidates,overviewCaptionRole,selectOverviewCaptions} from '../knowledge/overview-captions';
import {labelReadingFrame} from '../knowledge/label-zoom';

const node=(id:string,label:string,category='topic',target='career',degree=1)=>({id,label,category,degree,source_target:target,source_version:'v:'+id});
function fixture(){
  return parseKnowledgeGraph({nodes:[node('a','지원 전략'),node('b','면접'),
    {...node('s','스토어 운영 자료','index','files',10),source_metadata:{content_evidence:'metadata_description'}},
    {...node('x','수집한 파일 이름','document','files'),source_metadata:{content_evidence:'metadata_description'}},
    {...node('y','다른 파일 이름','document','files'),source_metadata:{content_evidence:'reference_only'}}],
    edges:[{source:'s',target:'x',relation:'contains',purpose:'navigation'},{source:'s',target:'y',relation:'contains',purpose:'navigation'}]});
}
describe('typed source-backed overview captions',()=>{
  it('uses a saved reference list with real scoped links without promoting its metadata to content',()=>{
    const graph=fixture(),before=JSON.stringify(graph),captions=selectOverviewCaptions(graph.nodes,graph.edges);
    const section=captions.find(c=>c.node.id==='s')!;
    expect(section.role).toBe('reference-section');expect(new Set(section.witnesses)).toEqual(new Set(['x','y']));
    expect(section.node).toBe(graph.nodes[2]);expect(section.node.contentEvidence).toBe('metadata_description');
    expect(captions.some(c=>c.role==='topic'&&c.node.id==='a')).toBe(true);
    expect(JSON.stringify(graph)).toBe(before);expect(graph.nodes).toHaveLength(5);expect(graph.edges).toHaveLength(2);
  });
  it('keeps most heading slots for subjects/lists while retaining other source titles',()=>{
    const topics=Array.from({length:9},(_,i)=>node('t'+i,'지원 전략 '+i,'topic','career',100-i));
    const graph=fixture();graph.nodes.push(...parseKnowledgeGraph({nodes:[...topics,
      node('leaf1','다른 출처에서 저장한 실제 문서','document','one'),node('leaf2','별도 출처의 확인할 원문','document','two')]}).nodes);
    const result=selectOverviewCaptions(graph.nodes,graph.edges);
    expect(result).toHaveLength(8);expect(result.filter(c=>c.role==='title')).toHaveLength(2);
    expect(result.filter(c=>c.role!=='title')).toHaveLength(6);
    expect(new Set(result.map(c=>c.node.sourceTarget))).toEqual(new Set(['career','files','one','two']));
    expect(result.map(c=>c.node.id).slice(0,4)).toContain('s');
  });
  it.each(['missing','unversioned','foreign','retracted-node','retracted-edge','expired','future','duplicate'])('does not manufacture a two-link list proof from %s references',kind=>{
    const graph=fixture();
    if(kind==='missing')graph.nodes.pop();
    if(kind==='unversioned')graph.nodes[4].sourceVersion=undefined;
    if(kind==='foreign')graph.nodes[4].sourceTarget='denied';
    if(kind==='retracted-node')graph.nodes[4].evidence.retracted=true;
    if(kind==='retracted-edge')graph.edges[1].evidence.retracted=true;
    if(kind==='expired')graph.edges[1].validTo=new Date(9999).toISOString();
    if(kind==='future')graph.edges[1].validFrom=new Date(11000).toISOString();
    if(kind==='duplicate')graph.edges[1].target='x';
    expect(captionCandidates(graph.nodes,graph.edges,10000).some(c=>c.node.id==='s')).toBe(false);
  });
  it('keeps routing indexes, unbound lists and generic buckets out of the primary headings',()=>{
    const graph=fixture();
    for(const label of ['일반 동영상','키워드 역색인','graph:internal','과거 자료','전체 공개 자료']){
      graph.nodes[2].label=label;expect(captionCandidates(graph.nodes,graph.edges).some(c=>c.node.id==='s')).toBe(false);
    }
    graph.nodes[2].label='스토어 운영 자료';graph.nodes[2].sourceVersion=undefined;
    expect(captionCandidates(graph.nodes,graph.edges).some(c=>c.node.id==='s')).toBe(false);
  });
  it('does not use a chat-room name as a heading fallback or erase its node',()=>{
    const graph=parseKnowledgeGraph({nodes:[node('room','대화 자료 1번방','대화방','memory',1000),
      node('doc','실제 문서에 남은 상세 제목','document','files'),node('doc2','다른 출처의 상세 제목','document','other')]});
    const result=selectOverviewCaptions(graph.nodes,[]);
    expect(result.map(c=>c.node.id)).toEqual(['doc2','doc']);expect(result.every(c=>c.role==='title')).toBe(true);
    expect(graph.nodes).toHaveLength(3);
  });
  it('deduplicates Unicode/space display text only, caps slots, and leaves original IDs intact',()=>{
    const graph=fixture();graph.nodes.push({...graph.nodes[0],id:'duplicate',label:'지원   전략',sourceTarget:'other'});
    const all=selectOverviewCaptions(graph.nodes,graph.edges);
    expect(all.filter(c=>c.node.label.replace(/\s+/g,' ')==='지원 전략')).toHaveLength(1);
    expect(graph.nodes.some(n=>n.id==='duplicate')).toBe(true);
    expect(selectOverviewCaptions(graph.nodes,graph.edges,0)).toEqual([]);
    expect(selectOverviewCaptions(graph.nodes,graph.edges,100).length).toBeLessThanOrEqual(8);
  });
  it('gives lists an explicit reading role and badge width rather than styling leaf titles as subjects',()=>{
    expect(overviewCaptionRole({category:'index'})).toBe('reference-section');
    expect(overviewCaptionRole({category:'topic'})).toBe('topic');expect(overviewCaptionRole({category:'video'})).toBe('title');
    const section=labelReadingFrame('reference-section',1,'스토어 자료',14),subject=labelReadingFrame('topic',1,'스토어 자료',14);
    expect(section.width).toBeGreaterThan(subject.width);expect(section.lines).toBe(1);expect(section.maxWidth).toBe(220);
  });
});
