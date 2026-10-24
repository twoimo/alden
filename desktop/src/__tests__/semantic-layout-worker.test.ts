import {describe,expect,it,vi} from 'vitest';
import {parseKnowledgeGraph,overviewGraph} from '../knowledge/graph-model';
import type {LayoutRequest,LayoutResponse} from '../knowledge/semantic-layout-driver';

describe('semantic worker snapshot clock',()=>{
  it('uses the dispatched validity time rather than its later wall clock',async()=>{
    const scope={onmessage:null as null|((event:MessageEvent<LayoutRequest>)=>void),postMessage:vi.fn()};
    vi.stubGlobal('self',scope);vi.spyOn(Date,'now').mockReturnValue(2000);
    try{
      await import('../knowledge/semantic-layout-worker');
      const source=overviewGraph(parseKnowledgeGraph({nodes:[{id:'a',label:'a'},{id:'b',label:'b'}],
        edges:[{source:'a',target:'b',relation:'ref',weight:1,valid_to:'1970-01-01T00:00:01.500Z'}]},1000),2048,0,1000);
      const request:LayoutRequest={id:1,view:source,previous:[],pairs:[],changed:[],pinned:[],nowMs:1000};
      scope.onmessage!({data:request} as MessageEvent<LayoutRequest>);
      const first=scope.postMessage.mock.calls[0][0] as LayoutResponse;
      expect(first.error).toBeUndefined();expect(first.diagnostics?.relationPairs).toBe(1);
      scope.onmessage!({data:{...request,id:2,nowMs:2000}} as MessageEvent<LayoutRequest>);
      const second=scope.postMessage.mock.calls[1][0] as LayoutResponse;
      expect(second.error).toBeUndefined();expect(second.diagnostics?.relationPairs).toBe(0);
      for(const nowMs of [undefined,NaN,Infinity]){
        scope.onmessage!({data:{...request,id:3,nowMs}} as MessageEvent<LayoutRequest>);
        const invalid=scope.postMessage.mock.calls.at(-1)![0] as LayoutResponse;
        expect(invalid.error).toBe('semantic_layout_failed');expect(invalid.anchors).toBeUndefined();
      }
    }finally{vi.restoreAllMocks();vi.unstubAllGlobals();}
  });
});
