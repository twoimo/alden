import {describe,expect,it} from 'vitest';
import {parseKnowledgeGraph,type KnowledgeView} from '../knowledge/graph-model';
import {SemanticLayoutEngine} from '../knowledge/semantic-layout';
import type {LayoutAffinityPair} from '../knowledge/layout-affinity';

function scene(star:boolean,duplicateTitles=false):KnowledgeView {
  return {...parseKnowledgeGraph({nodes:Array.from({length:256},(_,i)=>({
    id:`n${i.toString().padStart(3,'0')}`,label:duplicateTitles&&(i===1||i===128)?'Same saved title':`Saved topic ${i}`,category:'topic',
  })),edges:star?Array.from({length:255},(_,i)=>({source:'n000',target:`n${(i+1).toString().padStart(3,'0')}`,weight:1})):[]}),hops:0,focusId:null};
}
const affinity=(target:string):LayoutAffinityPair[]=>[{source:'n001',target,cosine:.95}];
const solve=(view:KnowledgeView,pairs:LayoutAffinityPair[])=>new SemanticLayoutEngine().layout(view,new Map(),pairs,{nowMs:1000});

describe('preserve admitted semantic geometry through overview styling',()=>{
  it('lets a different semantic neighbor change the final coordinates of a hub leaf',()=>{
    const view=scene(true),source=JSON.stringify(view);
    const first=solve(view,affinity('n128')),second=solve(view,affinity('n129'));
    expect(first.diagnostics.affinityPairs).toBe(1);expect(second.diagnostics.affinityPairs).toBe(1);
    expect(first.clusterIds.get('n001')).toBe(first.clusterIds.get('n128'));
    expect(second.clusterIds.get('n001')).toBe(second.clusterIds.get('n129'));
    expect(first.anchors.get('n001')).not.toEqual(second.anchors.get('n001'));
    expect(first.anchors.get('n128')).not.toEqual(second.anchors.get('n128'));
    expect(JSON.stringify(view)).toBe(source);
  });

  it('does not replace admitted semantic positions with a duplicate-title disk',()=>{
    const view=scene(false,true),source=JSON.stringify(view);
    const first=solve(view,affinity('n128')),second=solve(view,affinity('n129'));
    expect(first.diagnostics.affinityPairs).toBe(1);
    expect(first.anchors.get('n001')).not.toEqual(second.anchors.get('n001'));
    expect(JSON.stringify(view)).toBe(source);
  });

  it('keeps structural fallback unchanged for rejected affinity input',()=>{
    const view=scene(true),fallback=solve(view,[]);
    for(const pair of [{source:'n001',target:'outside',cosine:.95},
                       {source:'n001',target:'n128',cosine:NaN},
                       {source:'n001',target:'n128',cosine:0}]){
      const rejected=solve(view,[pair]);
      expect(rejected.diagnostics.affinityPairs).toBe(0);
      expect(rejected.anchors).toEqual(fallback.anchors);
    }
  });
});
