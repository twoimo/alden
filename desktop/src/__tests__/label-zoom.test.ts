import {describe,it,expect} from 'vitest';
import {labelZoomLevel,labelZoomStyle} from '../knowledge/label-zoom';
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
    expect(labelZoomStyle(1,1000,600).topicSize).toBe(13);
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
