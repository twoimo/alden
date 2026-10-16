import { describe,expect,it } from 'vitest';
import { communitySnapshot } from '../knowledge/community-snapshot';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { parseKnowledgeGraph,overviewGraph } from '../knowledge/graph-model';

const rows=Array.from({length:17},(_,i)=>[`synthetic-${i}`,`c:${Math.floor(i/3).toString(16)}:a`] as [string,string]);
const ids=rows.map(([id])=>id);
describe('computed community metadata boundary',()=>{
  it('covers every identity, gives at most eight major groups colors and retains the aggregate remainder',()=>{
    const result=communitySnapshot(ids,rows)!;
    expect(result.groups).toHaveLength(6);expect(new Set(result.groups.map(g=>g.slot)).size).toBe(6);
    expect(result.colors.size).toBe(17);expect(result.otherNodes).toBe(0);
    expect(result.multiNodeClusters).toBe(6);
    const before=JSON.stringify(rows);
    const reversed=communitySnapshot([...ids].reverse(),[...rows].reverse())!;
    expect(reversed.groups).toEqual(result.groups);expect(reversed.colors).toEqual(result.colors);
    expect(JSON.stringify(rows)).toBe(before);
  });
  it.each(['missing','unknown','duplicate','invalid cluster'] as const)('rejects an entire %s batch',kind=>{
    const changed=rows.map(row=>[...row]);
    if(kind==='missing')changed.pop();
    if(kind==='unknown')changed[0][0]='foreign-scope';
    if(kind==='duplicate')changed[0]=[...changed[1]];
    if(kind==='invalid cluster')changed[0][1]='<script>same labels</script>';
    expect(communitySnapshot(ids,changed)).toBeNull();
  });
  it('keeps bounded surviving palette slots without retaining old memberships',()=>{
    const first=communitySnapshot(ids,rows)!;
    const changed=rows.map(([id,cluster],i)=>[id,i>=12?'c:9:b':cluster] as [string,string]);
    const next=communitySnapshot(ids,changed,first.slots)!;
    for(const g of next.groups)if(first.slots.has(g.id))expect(g.slot).toBe(first.slots.get(g.id));
    expect(next.slots.size).toBeLessThanOrEqual(8);expect(next.colors.size).toBe(ids.length);
  });
  it('uses actual S-only solver memberships without introducing source facts or same-entity claims',()=>{
    const graph=overviewGraph(parseKnowledgeGraph({nodes:[{id:'a',label:'one'},{id:'b',label:'another'}],edges:[]}));
    const engine=new SemanticLayoutEngine(),result=engine.layout(graph,new Map(),[{source:'a',target:'b',cosine:.9}],{nowMs:1000});
    const snapshot=communitySnapshot(graph.nodes.map(n=>n.id),engine.checkpoint().labels)!;
    expect(snapshot.colors.get('a')).toBe(snapshot.colors.get('b'));expect(snapshot.groups[0].count).toBe(2);
    expect(graph.edges).toEqual([]);expect(result.anchors.size).toBe(2);expect(snapshot.colors.has('a')).toBe(true);
  });
  it('rejects empty, duplicate and excess visible identities before accepting labels',()=>{
    expect(communitySnapshot([],[])).toBeNull();expect(communitySnapshot(['a','a'],[['a','c:1:2'],['a','c:1:2']])).toBeNull();
    const excess=Array.from({length:2049},(_,i)=>`n${i}`);expect(communitySnapshot(excess,excess.map(id=>[id,'c:1:2']))).toBeNull();
  });
});
