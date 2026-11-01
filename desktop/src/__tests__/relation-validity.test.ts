// @vitest-environment happy-dom
import { afterEach,describe,expect,it,vi } from 'vitest';
import { parseKnowledgeGraph,overviewGraph } from '../knowledge/graph-model';
import { KnowledgeHologram } from '../knowledge/hologram';
import { relationCurrentlyValid,nextRelationBoundary } from '../knowledge/relation-validity';
afterEach(()=>vi.useRealTimers());
describe('owned relation time boundary',()=>{
  it('uses inclusive starts and exclusive ends, including an exact expiry with unchanged source fields',()=>{
    const edge={validFrom:'1970-01-01T00:00:01.000Z',validTo:'1970-01-01T00:00:02.000Z'};
    expect(relationCurrentlyValid(edge,999)).toBe(false);expect(relationCurrentlyValid(edge,1000)).toBe(true);
    expect(relationCurrentlyValid(edge,1999)).toBe(true);expect(relationCurrentlyValid(edge,2000)).toBe(false);
    expect(relationCurrentlyValid({validFrom:'',validTo:''},2000)).toBe(true);
  });
  it('owns a single visible timer, rebuilds once at expiry and cancels it while stopped',()=>{
    vi.useFakeTimers();vi.setSystemTime(1000);
    const graph=overviewGraph(parseKnowledgeGraph({nodes:[{id:'a',label:'a'},{id:'b',label:'b'}],edges:[{source:'a',target:'b',weight:1}]}));
    graph.edges=graph.edges.map(e=>({...e,validTo:'1970-01-01T00:00:02.000Z'}));
    const value=Object.assign(Object.create(KnowledgeHologram.prototype),{requestedAnimation:true,disposed:false,view:graph,
      relationBoundaryTimer:null,rebuildGraph:vi.fn(),invalidateFrame:vi.fn()});
    value.scheduleRelationBoundary();value.scheduleRelationBoundary();expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(999);expect(value.rebuildGraph).not.toHaveBeenCalled();vi.advanceTimersByTime(1);
    expect(value.rebuildGraph).toHaveBeenCalledTimes(1);expect(value.invalidateFrame).toHaveBeenCalledTimes(1);
    value.scheduleRelationBoundary();expect(vi.getTimerCount()).toBe(0);
    graph.edges=graph.edges.map(e=>({...e,validTo:'1970-01-01T00:00:03.000Z'}));value.scheduleRelationBoundary();expect(vi.getTimerCount()).toBe(1);
    value.requestedAnimation=false;value.scheduleRelationBoundary();expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(5000);expect(value.rebuildGraph).toHaveBeenCalledTimes(1);
    expect(graph.edges[0].validTo).toBe('1970-01-01T00:00:03.000Z');
  });
  it('does not schedule events for retired/navigation rows or repeatedly poll elapsed boundaries',()=>{
    const graph=overviewGraph(parseKnowledgeGraph({nodes:[{id:'a',label:'a'},{id:'b',label:'b'}],edges:[{source:'a',target:'b',weight:1}]}));
    const edge={...graph.edges[0],validTo:'1970-01-01T00:00:02.000Z'};
    expect(nextRelationBoundary([edge],1000)).toBe(2000);expect(nextRelationBoundary([edge],2000)).toBeNull();
    expect(nextRelationBoundary([{...edge,purpose:'navigation'}],1000)).toBeNull();
    expect(nextRelationBoundary([{...edge,evidence:{...edge.evidence,retracted:true}}],1000)).toBeNull();
  });
});
