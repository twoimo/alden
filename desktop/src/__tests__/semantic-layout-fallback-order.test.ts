import {describe,it,expect} from 'vitest';
import {KnowledgeDrilldown,parseKnowledgeGraph} from '../knowledge/graph-model';
import {SemanticLayoutEngine} from '../knowledge/semantic-layout';

function star(){
  const nodes=[{id:'hub',label:'기업 채용 과정',category:'topic'},
    ...Array.from({length:255},(_,i)=>({id:'leaf-'+i,label:'소프트웨어 검증 자료 '+i,category:'source'}))];
  return new KnowledgeDrilldown(parseKnowledgeGraph({overview_budget:2048,nodes,
    edges:nodes.slice(1).map(n=>({source:'hub',target:n.id,relation:'source-topic',purpose:'reference',weight:1}))})).current();
}
function closePairs(points:ReadonlyMap<string,{x:number;y:number;z:number}>){
  const values=[...points.values()];let count=0;
  for(let i=0;i<values.length;i++)for(let j=i+1;j<values.length;j++){
    const a=values[i],b=values[j];if(Math.hypot(a.x-b.x,a.y-b.y,a.z-b.z)<.07)count++;
  }
  return count;
}
describe('fallback geometry remains subject to the bounded collision solve',()=>{
  it('does not pack a dense hub back below half-clearance after solving',()=>{
    const view=star(),before=JSON.stringify(view),engine=new SemanticLayoutEngine();
    const result=engine.layout(view,new Map());
    expect(result.diagnostics.nodes).toBe(256);
    expect(result.diagnostics.fallbackAnchorNodes).toBe(1);
    expect(result.diagnostics.pinnedNodes).toBe(0);
    // This finite dense-hub acceptance bounds the number of close pairs; it
    // deliberately does not claim that every output pair clears .07.
    expect(closePairs(result.anchors)).toBeLessThan(view.nodes.length/2);
    expect(JSON.stringify(view)).toBe(before);
  });
  it('also applies the collision solve after an explicit fresh reflow',()=>{
    const view=star(),engine=new SemanticLayoutEngine(),first=engine.layout(view,new Map());
    const result=engine.layout(view,first.anchors,[],{reflow:true});
    expect(closePairs(result.anchors)).toBeLessThan(view.nodes.length/2);
  });
  it('does not turn an admitted semantic hub into a structural fallback anchor',()=>{
    const view=star(),affinities=[{source:'hub',target:'leaf-0',cosine:.95}];
    const before=JSON.stringify([view,affinities]);
    const result=new SemanticLayoutEngine().layout(view,new Map(),affinities);
    expect(result.diagnostics.affinityPairs).toBe(1);
    expect(result.diagnostics.fallbackAnchorNodes).toBe(0);
    expect(result.diagnostics.pinnedNodes).toBe(0);
    expect(JSON.stringify([view,affinities])).toBe(before);
  });
  it('keeps authoritative pins and a repeated unchanged snapshot exact',()=>{
    const view=star(),engine=new SemanticLayoutEngine();
    const pins=new Set(['hub','leaf-0']),previous=new Map([['hub',{x:1,y:.7,z:.2}],['leaf-0',{x:-1,y:-.6,z:-.1}]]);
    const first=engine.layout(view,previous,[],{pinnedIds:pins});
    for(const id of pins)expect(first.anchors.get(id)).toEqual(previous.get(id));
    const again=engine.layout(view,new Map(),[],{pinnedIds:pins});
    expect([...again.anchors]).toEqual([...first.anchors]);
  });
});
