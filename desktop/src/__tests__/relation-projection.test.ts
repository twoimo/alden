import {describe,it,expect} from 'vitest';
import {parseKnowledgeGraph} from '../knowledge/graph-model';
import {knowledgeSignature,knowledgeChangeSummary} from '../knowledge/changes';
import {unifiedGraph} from '../knowledge/unified-graph';

const payload=(count=2,revision='a'.repeat(64))=>({ok:true,nodes:[{id:'a',label:'자료 A'},{id:'b',label:'자료 B'}],
  edges:[{source:'a',target:'b',relation:'refers-to',weight:1,purpose:'reference',
    reference_group:{kind:'directed-relation-group-v1',count,truncated:count>16,revision},
    evidence:{kind:'snapshot',source_event_ids:Array.from({length:Math.min(count,16)},(_,i)=>'claim-'+i)}}]});

describe('directed source-record display groups',()=>{
  it('retains count and current revision without increasing connection force',()=>{
    const graph=parseKnowledgeGraph(payload(24));expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0].referenceGroup).toEqual({count:24,truncated:true,revision:'a'.repeat(64)});
    expect(graph.edges[0].weight).toBe(1);expect(graph.edges[0].evidence.sourceEventIds).toHaveLength(16);
  });
  it('refreshes evidence changes outside the bounded ID sample',()=>{
    const before=parseKnowledgeGraph(payload(24)),after=parseKnowledgeGraph(payload(24,'b'.repeat(64)));
    expect(before.edges[0].evidence).toEqual(after.edges[0].evidence);
    expect(knowledgeSignature(before)).not.toBe(knowledgeSignature(after));
    expect(knowledgeChangeSummary(before,after)).toBe('연결 갱신 1');
  });
  it('preserves the proof marker through the memory view union',()=>{
    const graph=parseKnowledgeGraph(unifiedGraph(null,payload(24),{}));
    expect(graph.edges[0].source).toBe('memory:a');
    expect(graph.edges[0].referenceGroup?.count).toBe(24);
  });
  it.each([{count:0},{count:1},{count:2**53},{count:'2'},{truncated:true},{revision:'bad'},{kind:'unverified'}])('rejects malformed projection metadata (%j)',bad=>{
    const raw=payload();Object.assign(raw.edges[0].reference_group,bad);
    expect(parseKnowledgeGraph(raw).edges[0].referenceGroup).toBeUndefined();
  });
  it('does not treat duplicated sample identities as multiple records',()=>{
    const raw=payload();raw.edges[0].evidence.source_event_ids=['same','same'];
    expect(parseKnowledgeGraph(raw).edges[0].referenceGroup).toBeUndefined();
  });
});
