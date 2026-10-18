import {describe,it,expect} from 'vitest';
import {labelZoomLevel,labelZoomStyle,selectProjectedLabels} from '../knowledge/label-zoom';
import {selectZoomLabelCandidates} from '../knowledge/overview-labels';
import {parseKnowledgeGraph} from '../knowledge/graph-model';

describe('screen-space graph reading levels',()=>{
  it('moves through four levels in either direction and preserves a dead band',()=>{
    expect([.5,1,1.7,2.5].map(r=>labelZoomLevel(r))).toEqual([0,1,2,3]);
    expect(labelZoomLevel(1.41,1)).toBe(1);
    expect(labelZoomLevel(1.39,2)).toBe(2);
    expect(labelZoomLevel(.5,3)).toBe(0);
    expect(labelZoomLevel(2.5,0)).toBe(3);
    for(const r of [NaN,Infinity,0,-1])expect(labelZoomLevel(r)).toBe(1);
  });
  it('limits density by actual viewport without making text illegibly small',()=>{
    expect(labelZoomStyle(3,1000,600).budget).toBe(24);
    expect(labelZoomStyle(3,300,400).budget).toBe(7);
    expect(labelZoomStyle(0,1000,600).budget).toBe(4);
    expect(labelZoomStyle(1,1000,600).topicSize).toBe(14.25);
  });
  it('keeps type continuous across content thresholds, readable and bounded at extreme magnification',()=>{
    for(const ratio of [.72,1.4,2.1]) {
      const before=labelZoomStyle(labelZoomLevel(ratio-.01,0),1000,600,ratio-.01);
      const after=labelZoomStyle(labelZoomLevel(ratio+.01,3),1000,600,ratio+.01);
      expect(Math.abs(before.topicSize-after.topicSize)).toBeLessThanOrEqual(.25);
    }
    for(const ratio of [.01,.55,1,1.7,2.5,100,NaN,Infinity]) {
      const style=labelZoomStyle(3,1000,600,ratio);
      expect(style.topicSize).toBeGreaterThanOrEqual(13);
      expect(style.topicSize).toBeLessThanOrEqual(16);
      expect(style.detailSize).toBeGreaterThanOrEqual(12);
    }
  });
  it('leaves room for visible titles beyond a dense high-rank topic cluster',()=>{
    const candidates=[...Array.from({length:8},(_,rank)=>({id:'t'+rank,x:200,y:200,topic:true,rank})),
      {id:'a',x:400,y:100,topic:false,rank:8},{id:'b',x:600,y:300,topic:false,rank:9}];
    const before=JSON.stringify(candidates);
    expect([...selectProjectedLabels(candidates,8,new Set())]).toEqual(['t0','a','b']);
    expect(JSON.stringify(candidates)).toBe(before);
    expect(selectProjectedLabels(candidates,0,new Set()).size).toBe(0);
  });
  it('retains a nearby already visible title without letting titles outrank subjects',()=>{
    const candidates=[{id:'topic',x:0,y:0,topic:true,rank:0},
      {id:'a',x:400,y:100,topic:false,rank:9},{id:'b',x:401,y:100,topic:false,rank:10}];
    expect([...selectProjectedLabels(candidates,24,new Set(['b']))]).toEqual(['topic','b']);
    expect(selectProjectedLabels([...candidates,{id:'bad',x:NaN,y:20,topic:true,rank:0}],100,new Set()).size).toBe(2);
  });
  it('keeps useful off-overview titles eligible without generic or duplicate labels or source mutation',()=>{
    const g=parseKnowledgeGraph({nodes:[
      ...Array.from({length:8},(_,i)=>({id:'t'+i,label:'저장 주제 '+i,category:'topic',degree:100})),
      {id:'doc',label:'면접 질문을 준비하는 방법',category:'document'},
      {id:'dupe',label:'면접  질문을 준비하는 방법',category:'document'},
      {id:'generic',label:'일반 동영상',category:'topic'},
      {id:'internal',label:'source:secret',category:'document'},
      {id:'unread',label:'읽지 않은 자료 제목',category:'document',source_metadata:{content_evidence:'reference_only'}},
    ],edges:[]});
    const before=JSON.stringify(g);
    expect(selectZoomLabelCandidates(g.nodes).map(n=>n.id)).toEqual([...Array.from({length:8},(_,i)=>'t'+i),'doc']);
    expect(JSON.stringify(g)).toBe(before);
  });
});
