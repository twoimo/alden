import { describe, it, expect } from 'vitest';
import { expandGraphTransport } from '../graph-transport';

describe('lossless bounded source context transport', () => {
  it('retains missing/null, future fields and prototype-named keys in record tables',()=>{
    const value:any={source_context_encoding:'display-source-context-v1',source_record_encoding:{codec:'record-keys-v1',nodes:['id','optional','future','__proto__'],edges:['id']},
      nodes:[[0,'a',1,null,2,{proof:['v1','public']},3,{injected:true}],[0,'b']],edges:[[0,'e']]};
    const decoded:any=expandGraphTransport(value);
    expect(decoded.nodes[0].optional).toBe(null);expect(Object.hasOwn(decoded.nodes[1],'optional')).toBe(false);
    expect(decoded.nodes[0].future).toEqual({proof:['v1','public']});
    expect(Object.getPrototypeOf(decoded.nodes[0])).toBe(null);expect(decoded.nodes[0].__proto__).toEqual({injected:true});
    expect(({} as any).injected).toBeUndefined();expect(decoded.edges[0].id).toBe('e');
    expect(decoded.source_record_encoding).toBeUndefined();expect(value.nodes[0]).toEqual([0,'a',1,null,2,{proof:['v1','public']},3,{injected:true}]);
  });
  it('rejects ambiguous, malformed and over-budget record tables atomically',()=>{
    for(const change of [
      (v:any)=>{v.source_record_encoding.nodes=['id','id'];},
      (v:any)=>{v.nodes=[[0,'a',0,'b']];},(v:any)=>{v.nodes=[[0]];},
      (v:any)=>{v.nodes=[[-1,'a']];},(v:any)=>{v.nodes=[[1,'a']];},
      (v:any)=>{v.nodes=Array.from({length:2049},()=>[0,'a']);},
      (v:any)=>{v.source_record_encoding.codec='unknown';},
    ]){
      const v:any={source_context_encoding:'display-source-context-v1',source_record_encoding:{codec:'record-keys-v1',nodes:['id'],edges:[]},nodes:[[0,'a']],edges:[]};
      change(v);expect(()=>expandGraphTransport(v)).toThrow();
    }
  });
  const packet = () => ({ source_context_encoding: 'display-source-context-v1', ok: true, nodes: [{
    id: 'a', label: 'Title', source_target: 't', source_version: 'v',
    source_contexts: [{ project: 'one', same_as_display: true },
      { project: 'two', target_id: 'other', version: 'older', label: 'Old title' }],
  }], edges: [{ id: 'original-edge', source: 'a', target: 'b' }] });
  it('restores exact current fields and preserves independently scoped versions and edges', () => {
    const value = packet(), decoded: any = expandGraphTransport(value);
    expect(decoded.nodes[0].source_contexts).toEqual([
      { project: 'one', target_id: 't', version: 'v', label: 'Title' },
      { project: 'two', target_id: 'other', version: 'older', label: 'Old title' },
    ]);
    expect(decoded.edges).toEqual(value.edges);
    expect(decoded.source_context_encoding).toBeUndefined();
    expect(value.source_context_encoding).toBe('display-source-context-v1');
  });
  it('never invents reference fields from missing, conflicting or unknown packets', () => {
    for (const change of [
      (v: any) => { delete v.nodes[0].source_version; },
      (v: any) => { v.nodes[0].source_contexts[0].same_as_display = 'true'; },
      (v: any) => { v.nodes[0].source_contexts[0].target_id = 'foreign'; },
      (v: any) => { v.source_context_encoding = 'unknown'; },
    ]) { const value = packet(); change(value); expect(() => expandGraphTransport(value)).toThrow(); }
  });
});
