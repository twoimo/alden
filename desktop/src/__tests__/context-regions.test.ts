import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { contextRegions, regionBounds } from '../knowledge/context-regions';
import { ContextNebulae } from '../knowledge/cortex';
import { KnowledgeDrilldown, overviewGraph, parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { relationAnchors } from '../knowledge/relation-layout';

const view = (): KnowledgeView => ({ ...parseKnowledgeGraph({
  nodes: [
    ...['a', 'b', 'c'].map(id => ({ id, label: id, space: 'source/'+id, importance: 50 })),
    { id: 'shared', label: '공유 기억', importance: 50 },
  ],
  edges: ['a', 'b', 'c'].map(source => ({ source, target: 'shared', weight: 5, purpose: 'semantic' })),
}), focusId: null, hops: 0 });

describe('source context envelopes', () => {
  it('shows three actual spaces and places a shared memory in its three real contexts without mutating evidence', () => {
    const graph = view(), before = JSON.stringify(graph);
    const regions = contextRegions(graph);
    expect(regions).toHaveLength(3);
    for (const region of regions) expect(region.nodeIds).toContain('shared');
    expect(JSON.stringify(graph)).toBe(before);
  });
  it('adds and removes contexts with their actual members, rather than fixing three or deriving types', () => {
    const graph = view(); graph.nodes.push({ ...graph.nodes[0], id: 'd', space: 'source/d' });
    expect(contextRegions(graph)).toHaveLength(4);
    graph.nodes = graph.nodes.filter(n => n.id !== 'b');
    expect(contextRegions(graph).map(r => r.id)).not.toContain('space:source/b');
    graph.nodes.forEach(n => { n.category = '인물'; n.label = '같은 제목'; });
    expect(contextRegions(graph)).toHaveLength(3);
  });
  it('does not propagate through navigation, retracted links, expired links or a second shared hop', () => {
    const graph = view(); graph.edges[1].purpose = 'navigation'; graph.edges[2].validTo = '2000-01-01';
    graph.nodes.push({ ...graph.nodes[3], id: 'second' });
    graph.edges.push({ ...graph.edges[0], source: 'shared', target: 'second' });
    expect(contextRegions(graph).find(r => r.id === 'space:source/a')!.nodeIds).toEqual(['a', 'shared']);
    expect(contextRegions(graph).find(r => r.id === 'space:source/b')!.nodeIds).toEqual(['b']);
    graph.edges[0].evidence.retracted = true;
    expect(contextRegions(graph).find(r => r.id === 'space:source/a')!.nodeIds).toEqual(['a']);
  });
  it('uses actual semantic components when no space exists, and keeps the visible budget', () => {
    const graph = view(); graph.nodes.forEach(n => { n.space = ''; });
    expect(contextRegions(graph)).toHaveLength(1);
    graph.edges = []; graph.nodes = Array.from({length:80}, (_,i) => ({...graph.nodes[0],id:String(i)}));
    expect(contextRegions(graph)).toHaveLength(24);
  });
  it('keeps existing component anchors unchanged when an unrelated context appears', () => {
    const graph = view(), before = relationAnchors(graph);
    graph.nodes.push({ ...graph.nodes[0], id: 'independent', space: 'other/source' });
    const after = relationAnchors(graph);
    for (const [id, point] of before) expect(after.get(id)).toEqual(point);
  });
  it('fits envelopes from real finite member coordinates', () => {
    const region = {id:'source',label:'source',nodeIds:['a','b','bad']};
    const bounds = regionBounds(region,new Map([['a',{x:-1,y:0,z:0}],['b',{x:1,y:0,z:0}],['bad',{x:NaN,y:0,z:0}]]));
    expect(bounds).toEqual({x:0,y:0,z:0,radius:1.2});
  });
  it('preserves attested multi-room memberships while refusing ambiguous room-space mappings', () => {
    const graph = view(); graph.nodes[0].evidence.chatId = '101'; graph.nodes[1].evidence.chatId = '202';
    graph.nodes[3].space = 'source/shared'; graph.nodes[3].evidence.roomIds = ['101','202'];
    const regions = contextRegions(graph);
    expect(regions.find(r=>r.id==='space:source/a')!.nodeIds).toContain('shared');
    expect(regions.find(r=>r.id==='space:source/b')!.nodeIds).toContain('shared');
    graph.nodes.push({...graph.nodes[2],id:'ambiguous',space:'other/room',evidence:{...graph.nodes[2].evidence,chatId:'101'}});
    expect(contextRegions(graph).find(r=>r.id==='space:source/a')!.nodeIds).not.toContain('shared');
    expect(graph.nodes[3].space).toBe('source/shared');
  });
  it('keeps producer room IDs as strings and does not accept guessed or malformed identities', () => {
    const scoped='kakao:'+('a'.repeat(64))+':room:101';
    const graph = parseKnowledgeGraph({nodes:[{id:'a',label:'a',evidence:{room_ids:['101','202','101',scoped,'name:room',202,null]}}]});
    expect(graph.nodes[0].evidence.roomIds).toEqual(['101','202',scoped]);
  });
  it('matches complete account-scoped room identities without mixing equal numbers across accounts', () => {
    const graph=view(), room='kakao:'+('a'.repeat(64))+':room:101', other='kakao:'+('b'.repeat(64))+':room:101';
    graph.nodes[0].evidence.chatId=room;graph.nodes[1].evidence.chatId=other;
    graph.nodes[3].space='source/shared';graph.nodes[3].evidence.roomIds=[room,'kakao:'+('a'.repeat(64))+':room:202'];
    expect(contextRegions(graph).find(r=>r.id==='space:source/a')!.nodeIds).toContain('shared');
    expect(contextRegions(graph).find(r=>r.id==='space:source/b')!.nodeIds).not.toContain('shared');
  });
  it('does not lose semantic membership when unrelated edges exceed the bridge drawing budget', () => {
    const graph=view(), before=contextRegions(graph);
    const extra = parseKnowledgeGraph({nodes:Array.from({length:290},(_,i)=>({id:'hidden-'+i,label:'hidden'})),edges:Array.from({length:145},(_,i)=>({source:'hidden-'+(i*2),target:'hidden-'+(i*2+1),weight:100}))});
    expect(contextRegions(graph,{nodes:[...graph.nodes,...extra.nodes],edges:[...graph.edges,...extra.edges]})).toEqual(before);
  });
  it('contains all members rather than clipping a wide context to an arbitrary radius', () => {
    const positions=new Map([['left',{x:-3.2,y:0,z:0}],['right',{x:3.2,y:0,z:0}]]);
    const region={id:'space:test',label:'test',nodeIds:['left','right']};
    expect(regionBounds(region,positions).radius).toBeCloseTo(3.4);
    const nebulae=new ContextNebulae();nebulae.set([region]);nebulae.update(0,positions,true);
    const matrix=new THREE.Matrix4();(nebulae.children[0] as THREE.InstancedMesh).getMatrixAt(0,matrix);
    expect(matrix.elements[0]).toBeCloseTo(3.4,5);
  });
  it('shows every small-graph memory in the cosmic overview without choosing fixed spaces or types', () => {
    const graph=parseKnowledgeGraph({nodes:Array.from({length:40},(_,i)=>({id:String(i),label:'같은 제목',category:i%2?'인물':'주제',space:'source/'+Math.floor(i/10),importance:100-i})),edges:[{source:'0',target:'39',weight:5}]});
    const before=JSON.stringify(graph), overview=overviewGraph(graph);
    expect(new Set(overview.nodes.map(n=>n.space)).size).toBe(4);
    expect(overview.nodes).toHaveLength(40);
    expect(JSON.stringify(graph)).toBe(before);expect(graph.nodes.some(n=>n.id==='39')).toBe(true);
  });
  it('balances three spaces even when a high-importance space has thirty memories', () => {
    const graph=parseKnowledgeGraph({nodes:[...Array.from({length:30},(_,i)=>({id:'a'+i,label:'A',space:'scope/a',importance:100})),{id:'b',label:'B',space:'scope/b'},{id:'c',label:'C',space:'scope/c'}]});
    const overview=overviewGraph(graph);expect(overview.nodes.some(n=>n.id==='b')).toBe(true);expect(overview.nodes.some(n=>n.id==='c')).toBe(true);
  });
  it('does not let expired or future edges choose a different overview', () => {
    const graph=parseKnowledgeGraph({nodes:['a','b','c','d','shared'].map(id=>({id,label:id,space:id==='shared'?'scope':'scope/'+id})),edges:[{source:'a',target:'b',weight:1}]});
    const before=overviewGraph(graph);graph.edges.push({...graph.edges[0],source:'d',target:'shared',weight:100,validTo:'2000-01-01'}, {...graph.edges[0],source:'d',target:'shared',weight:100,validFrom:'2999-01-01'});
    expect(overviewGraph(graph).nodes.map(n=>n.id)).toEqual(before.nodes.map(n=>n.id));
  });
  it('keeps a direct hidden-neighbour membership when another unrelated space becomes visible', () => {
    const graph=view(),memory={...graph.nodes[3],id:'memory'},hidden=graph.nodes[0],unrelated=graph.nodes[1];
    const full={nodes:[memory,hidden,unrelated],edges:[{...graph.edges[0],source:'memory',target:hidden.id}]};
    const initial={...graph,nodes:[memory],edges:[]};
    expect(contextRegions(initial,full).find(r=>r.id==='space:source/a')!.nodeIds).toContain('memory');
    expect(contextRegions({...initial,nodes:[memory,unrelated]},full).find(r=>r.id==='space:source/a')!.nodeIds).toContain('memory');
  });
  it('makes all disconnected spaces reachable through bounded More pages and restores prior overviews', () => {
    const graph=parseKnowledgeGraph({nodes:Array.from({length:245},(_,i)=>({id:String(i),label:String(i),space:'source/'+String(i).padStart(3,'0')}))});
    const model=new KnowledgeDrilldown(graph),seen=new Set<string>();const first=model.current().nodes.map(n=>n.id);
    model.expandOneHop();expect(model.current().nodes.length).toBe(120);model.back();expect(model.current().nodes.map(n=>n.id)).toEqual(first);
    for(let step=0;step<40;step++) {
      const view=model.current();expect(view.nodes.length).toBeLessThanOrEqual(120);view.nodes.forEach(n=>seen.add(n.id));
      if(!view.hasMoreContexts) break;model.expandOneHop();
    }
    expect(seen.size).toBe(245);expect(model.current().hasMoreContexts).toBe(false);
    model.reset();expect(model.current().nodes.map(n=>n.id)).toEqual(first);
  });
  it('opens a note without removing its global context, then expands links only on request', () => {
    const graph=view(),model=new KnowledgeDrilldown(graph),ids=model.current().nodes.map(n=>n.id);
    model.openNote('a');expect(model.current().hops).toBe(0);expect(model.current().focusId).toBe('a');expect(model.current().nodes.map(n=>n.id)).toEqual(ids);
    model.expandOneHop();expect(model.current().hops).toBe(2);expect(model.current().nodes.length).toBeLessThanOrEqual(24);
    model.back();expect(model.current().hops).toBe(0);expect(model.current().nodes.map(n=>n.id)).toEqual(ids);
    model.back();expect(model.current().focusId).toBe(null);
  });
});

describe('finite nebula lifecycle', () => {
  it('grows and retracts to an idle frame with one retained geometry, material and instance buffer', () => {
    const nebulae = new ContextNebulae(), graph = view(), regions = contextRegions(graph), positions = relationAnchors(graph);
    const mesh = nebulae.children[0] as THREE.InstancedMesh;
    const geometry = mesh.geometry, material = mesh.material, matrix = mesh.instanceMatrix.array;
    const advance = () => { for(let i=0;i<180;i++) nebulae.update(1/60,positions); };
    for(let cycle=0;cycle<8;cycle++) {
      nebulae.set(regions); advance();
      expect(nebulae.diagnostics()).toMatchObject({active:3,moving:false,retiring:0}); expect(mesh.count).toBe(3);
      nebulae.set([]); advance();
      expect(nebulae.diagnostics()).toMatchObject({active:0,moving:false,retiring:0}); expect(mesh.visible).toBe(false);
    }
    expect(mesh.geometry).toBe(geometry);expect(mesh.material).toBe(material);expect(mesh.instanceMatrix.array).toBe(matrix);
    expect(nebulae.diagnostics().capacity).toBe(24);
  });
  it('responds to member movement, freezes identical state, and respects immediate reduced motion', () => {
    const nebulae = new ContextNebulae(), graph = view(), regions = contextRegions(graph), positions = relationAnchors(graph);
    nebulae.set(regions); nebulae.update(0,positions,true); expect(nebulae.moving).toBe(false);
    const mesh = nebulae.children[0] as THREE.InstancedMesh, before = Array.from(mesh.instanceMatrix.array);
    for(let i=0;i<60;i++) nebulae.update(1/60,positions);
    expect(Array.from(mesh.instanceMatrix.array)).toEqual(before);expect(nebulae.moving).toBe(false);
    positions.get('a')!.x += .2;nebulae.update(1/60,positions);expect(nebulae.moving).toBe(true);
    nebulae.update(0,positions,true);expect(nebulae.moving).toBe(false);
    nebulae.set([]);nebulae.update(0,positions,true);expect(mesh.visible).toBe(false);
  });
  it('replaces a full capacity without leaking slots or leaving ghost instances', () => {
    const nebulae = new ContextNebulae(), positions = new Map(Array.from({length:48},(_,i)=>[String(i),{x:i*.02,y:0,z:0}]));
    const regions=(offset:number)=>Array.from({length:24},(_,i)=>({id:String(i+offset),label:String(i+offset),nodeIds:[String(i+offset)]}));
    nebulae.set(regions(0));nebulae.update(0,positions,true);
    nebulae.set(regions(24));nebulae.update(0,positions,true);
    expect(nebulae.diagnostics()).toMatchObject({active:24,retiring:0,moving:false});
    nebulae.set([]);nebulae.update(0,positions,true);expect((nebulae.children[0] as THREE.InstancedMesh).count).toBe(0);
  });
});
