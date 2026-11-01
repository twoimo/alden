import {describe,it,expect} from 'vitest';
import {KnowledgeDrilldown,parseKnowledgeGraph} from '../knowledge/graph-model';
import {SemanticLayoutEngine} from '../knowledge/semantic-layout';

function view(weight:number,second=false){
  return new KnowledgeDrilldown(parseKnowledgeGraph({nodes:[{id:'a',label:'Source A'},
    {id:'b',label:'Source B'},...(second?[{id:'c',label:'Source C'}]:[])],edges:[
      {source:'a',target:'b',weight},...(second?[{source:'a',target:'c',weight:1}]:[])]})).current();
}
function finite(points:ReadonlyMap<string,{x:number;y:number;z:number}>){
  for(const p of points.values())expect([p.x,p.y,p.z].every(Number.isFinite)).toBe(true);
}
describe('finite layout arithmetic for valid very small positive weights',()=>{
  it.each([Number.MIN_VALUE,1e-300,1e-100,Number.EPSILON/4])('keeps an isolated positive relation usable at %s',weight=>{
    const graph=view(weight),before=JSON.stringify(graph),engine=new SemanticLayoutEngine();
    const result=engine.layout(graph,new Map());finite(result.anchors);
    expect(result.diagnostics.relationPairs).toBe(1);
    const pair=engine.checkpoint().pairs[0][1];
    expect(pair.weight).toBeGreaterThan(0);expect(Number.isFinite(pair.weight)).toBe(true);
    expect(pair.rest).toBeCloseTo(.75,12);
    expect(JSON.stringify(graph)).toBe(before);
  });
  it('retains the source relation when its normalized force underflows beside a stronger edge',()=>{
    const graph=view(Number.MIN_VALUE,true),before=JSON.stringify(graph),engine=new SemanticLayoutEngine();
    const result=engine.layout(graph,new Map());finite(result.anchors);
    expect(result.diagnostics.relationPairs).toBe(2);
    expect(result.diagnostics.numericallyInactivePairs).toBe(1);
    expect(engine.checkpoint().pairs).toHaveLength(1);
    expect(JSON.stringify(graph)).toBe(before);
    expect([...engine.layout(graph,new Map()).anchors]).toEqual([...result.anchors]);
  });
  it('does not divide by zero when a valid semantic force is below the representable scale',()=>{
    const graph=view(0),affinities=[{source:'a',target:'b',cosine:Number.MIN_VALUE}];
    const before=JSON.stringify([graph,affinities]),engine=new SemanticLayoutEngine();
    const result=engine.layout(graph,new Map(),affinities);finite(result.anchors);
    expect(result.diagnostics.affinityPairs).toBe(1);
    expect(result.diagnostics.numericallyInactivePairs).toBe(1);
    expect(engine.checkpoint().pairs).toHaveLength(0);
    expect(JSON.stringify([graph,affinities])).toBe(before);
  });
});
