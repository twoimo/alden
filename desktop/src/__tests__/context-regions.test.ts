import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { CONTEXT_REGION_CAP, contextRegions, regionBounds } from '../knowledge/context-regions';
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

const hubGraph = () => parseKnowledgeGraph({
  nodes: [
    { id: 'osk:hub-a', label: 'Saved hub', is_hub: true, space: '00_Scope/Index' },
    { id: 'osk:hub-b', label: 'Saved hub', is_hub: true, space: '00_Scope/Index' },
    { id: 'source:original', label: 'Original', space: '00_Scope/Notes' },
    { id: 'osk:unplaced', label: 'Unplaced' },
    { id: 'osk:second-hop', label: 'Second hop' },
    { id: 'osk:ordinary', label: 'Saved hub', category: 'collection', space: '00_Scope/Index' },
  ],
  edges: [
    { source: 'osk:hub-a', target: 'source:original', relation: 'linked', purpose: 'navigation' },
    { source: 'source:original', target: 'osk:hub-b', relation: 'derived-from', purpose: 'reference' },
    { source: 'osk:hub-a', target: 'osk:unplaced', relation: 'linked', purpose: 'navigation' },
    { source: 'osk:unplaced', target: 'osk:second-hop', relation: 'linked', purpose: 'reference' },
    { source: 'osk:ordinary', target: 'osk:second-hop', relation: 'linked', purpose: 'navigation' },
    { source: 'osk:hub-b', target: 'osk:second-hop', relation: 'discusses', purpose: 'semantic' },
  ],
});

describe('canonical OSK context envelopes', () => {
  it('uses only each note’s stored directory, never its neighbours’ directories', () => {
    const graph = view(), before = JSON.stringify(graph);
    expect(contextRegions(graph)).toEqual(['a', 'b', 'c'].map(id => ({
      id: 'space:source/' + id, label: id, nodeIds: [id],
    })));
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
  it('does not turn ordinary Links or derived-from into transitive directory membership', () => {
    const graph = view();
    graph.edges[0].relation = 'linked'; graph.edges[0].purpose = 'navigation';
    graph.edges[1].relation = 'derived-from'; graph.edges[1].purpose = 'reference';
    graph.nodes.push({ ...graph.nodes[3], id: 'second' });
    graph.edges.push({ ...graph.edges[0], source: 'shared', target: 'second' });
    expect(contextRegions(graph).flatMap(region => region.nodeIds)).toEqual(['a', 'b', 'c']);
  });
  it('leaves unplaced notes ungrouped even when they share types, titles and connected components', () => {
    const graph = view(); graph.nodes.forEach(n => { n.space = ''; });
    graph.nodes.forEach(n => { n.category = 'collection'; n.label = 'Confirmed topic'; });
    expect(contextRegions(graph)).toEqual([]);
    graph.edges = [];
    expect(contextRegions(graph)).toEqual([]);
    expect(graph.nodes.map(n => n.id)).toEqual(['a', 'b', 'c', 'shared']);
  });
  it('keeps canonical memberships deterministic and inside the existing node and region budgets', () => {
    const graph = view();
    graph.nodes = Array.from({ length: 80 }, (_, i) => ({ ...graph.nodes[0], id: String(i), space: 'source/' + i, isHub: true }));
    graph.edges = [];
    const regions = contextRegions(graph);
    expect(regions).toHaveLength(CONTEXT_REGION_CAP);
    expect(regions.every(region => region.id.startsWith('space:'))).toBe(true);
    expect(new Set(regions.flatMap(region => region.nodeIds)).size).toBe(24);
    expect(contextRegions({ ...graph, nodes: [...graph.nodes].reverse() })).toEqual(regions);
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
  it('ignores both unambiguous and ambiguous room lists and preserves the source directory', () => {
    const graph = view(); graph.nodes[0].evidence.chatId = '101'; graph.nodes[1].evidence.chatId = '202';
    graph.nodes[3].space = 'source'; graph.nodes[3].evidence.roomIds = ['101','202'];
    const regions = contextRegions(graph);
    expect(regions.filter(region => region.nodeIds.includes('shared'))).toEqual([
      { id: 'space:source', label: 'source', nodeIds: ['shared'] },
    ]);
    graph.nodes.push({...graph.nodes[2],id:'ambiguous',space:'other/room',evidence:{...graph.nodes[2].evidence,chatId:'101'}});
    expect(contextRegions(graph).filter(region => region.nodeIds.includes('shared'))).toEqual([
      { id: 'space:source', label: 'source', nodeIds: ['shared'] },
    ]);
    graph.nodes[3].space = '';
    expect(contextRegions(graph).some(region => region.nodeIds.includes('shared'))).toBe(false);
  });
  it('keeps producer room IDs as strings and does not accept guessed or malformed identities', () => {
    const scoped='kakao:'+('a'.repeat(64))+':room:101';
    const graph = parseKnowledgeGraph({nodes:[{id:'a',label:'a',evidence:{room_ids:['101','202','101',scoped,'name:room',202,null]}}]});
    expect(graph.nodes[0].evidence.roomIds).toEqual(['101','202',scoped]);
  });
  it('does not use account-scoped room identities as display membership', () => {
    const graph=view(), room='kakao:'+('a'.repeat(64))+':room:101', other='kakao:'+('b'.repeat(64))+':room:101';
    graph.nodes[0].evidence.chatId=room;graph.nodes[1].evidence.chatId=other;
    graph.nodes[3].space='source/shared';graph.nodes[3].evidence.roomIds=[room,'kakao:'+('a'.repeat(64))+':room:202'];
    expect(contextRegions(graph).find(r=>r.id==='space:source/a')!.nodeIds).not.toContain('shared');
    expect(contextRegions(graph).find(r=>r.id==='space:source/b')!.nodeIds).not.toContain('shared');
  });
  it('does not change stored directory membership when unrelated edges exceed the bridge drawing budget', () => {
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
  it('cannot infer membership from a hidden neighbour or a newly visible unrelated space', () => {
    const graph=view(),memory={...graph.nodes[3],id:'memory'},hidden=graph.nodes[0],unrelated=graph.nodes[1];
    const full={nodes:[memory,hidden,unrelated],edges:[{...graph.edges[0],source:'memory',target:hidden.id}]};
    const initial={...graph,nodes:[memory],edges:[]};
    expect(contextRegions(initial,full)).toEqual([]);
    expect(contextRegions({...initial,nodes:[memory,unrelated]},full)).toEqual([
      { id: 'space:source/b', label: 'b', nodeIds: ['b'] },
    ]);
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

describe('stored hub membership readback', () => {
  it('uses only direct explicit hub links, preserving IDs, directories and same-title hubs', () => {
    const graph = hubGraph(), before = JSON.stringify(graph);
    const regions = contextRegions({ ...graph, focusId: null, hops: 0 });
    expect(regions.find(region => region.id === 'space:00_Scope/Index')).toEqual({
      id: 'space:00_Scope/Index', label: 'Index', nodeIds: ['osk:hub-a', 'osk:hub-b', 'osk:ordinary'],
    });
    expect(regions.filter(region => region.id.startsWith('hub:'))).toEqual([
      { id: 'hub:osk:hub-a', label: 'Saved hub', nodeIds: ['osk:hub-a', 'osk:unplaced', 'source:original'] },
      { id: 'hub:osk:hub-b', label: 'Saved hub', nodeIds: ['osk:hub-b', 'source:original'] },
    ]);
    expect(regions.find(region => region.id === 'space:00_Scope/Notes')!.nodeIds).toEqual(['source:original']);
    expect(regions.some(region => region.nodeIds.includes('osk:second-hop'))).toBe(false);
    expect(JSON.stringify(graph)).toBe(before);
  });

  it('reads links to hidden actual hubs from the full graph without inheriting their directories', () => {
    const graph = hubGraph();
    const visible = { ...graph, nodes: graph.nodes.filter(node => node.id === 'osk:unplaced'), edges: [], focusId: null, hops: 0 };
    const expected = [{ id: 'hub:osk:hub-a', label: 'Saved hub', nodeIds: ['osk:unplaced'] }];
    expect(contextRegions(visible, graph)).toEqual(expected);
    const extra = parseKnowledgeGraph({
      nodes: Array.from({ length: 290 }, (_, i) => ({ id: 'hidden-' + i, label: 'hidden' })),
      edges: Array.from({ length: 145 }, (_, i) => ({ source: 'hidden-' + i * 2, target: 'hidden-' + (i * 2 + 1), weight: 100 })),
    });
    expect(contextRegions(visible, { nodes: [...graph.nodes, ...extra.nodes], edges: [...extra.edges, ...graph.edges] })).toEqual(expected);
  });

  it('reads current paths by source ID even when the selected view still has old paths', () => {
    const graph = hubGraph(), selected = { ...graph, focusId: 'source:original', hops: 2 };
    const current = hubGraph();
    current.nodes.find(node => node.id === 'source:original')!.space = '00_Scope/Moved/Notes';
    current.nodes.find(node => node.id === 'osk:hub-a')!.space = '00_Scope/Moved/Index';
    const before = JSON.stringify({ selected, current });
    const regions = contextRegions(selected, current);
    expect(regions.find(region => region.id === 'space:00_Scope/Notes')).toBeUndefined();
    expect(regions.find(region => region.id === 'space:00_Scope/Moved/Notes')!.nodeIds).toEqual(['source:original']);
    expect(regions.find(region => region.id === 'space:00_Scope/Moved/Index')!.nodeIds).toEqual(['osk:hub-a']);
    expect(regions.find(region => region.id === 'space:00_Scope/Index')!.nodeIds).toEqual(['osk:hub-b', 'osk:ordinary']);
    expect(regions.find(region => region.id === 'hub:osk:hub-a')!.nodeIds).toContain('source:original');
    expect(JSON.stringify({ selected, current })).toBe(before);
  });

  it('reads hub renames, explicit-link removal and hub removal without stale memberships', () => {
    const graph = hubGraph(), selected = { ...hubGraph(), focusId: null, hops: 0 };
    graph.nodes.find(node => node.id === 'osk:hub-a')!.label = 'Renamed on disk';
    graph.edges = graph.edges.filter(edge => edge.target !== 'osk:unplaced');
    let regions = contextRegions(selected, graph);
    expect(regions.find(region => region.id === 'hub:osk:hub-a')).toEqual({
      id: 'hub:osk:hub-a', label: 'Renamed on disk', nodeIds: ['osk:hub-a', 'source:original'],
    });
    expect(regions.some(region => region.nodeIds.includes('osk:unplaced'))).toBe(false);
    graph.nodes.find(node => node.id === 'osk:hub-a')!.isHub = false;
    expect(contextRegions(selected, graph).some(region => region.id === 'hub:osk:hub-a')).toBe(false);
    graph.nodes = graph.nodes.filter(node => node.id !== 'osk:hub-b');
    regions = contextRegions(selected, graph);
    expect(regions.some(region => region.id.startsWith('hub:') || region.nodeIds.includes('osk:hub-b'))).toBe(false);
    expect(regions.find(region => region.id === 'space:00_Scope/Notes')!.nodeIds).toEqual(['source:original']);
  });

  it('does not keep retracted hubs or notes through dangling explicit links', () => {
    const graph = hubGraph(), selected = { ...hubGraph(), focusId: null, hops: 0 };
    graph.nodes.find(node => node.id === 'osk:hub-a')!.evidence.retracted = true;
    graph.nodes.find(node => node.id === 'source:original')!.evidence.retracted = true;
    const regions = contextRegions(selected, graph);
    expect(regions.some(region => region.id === 'hub:osk:hub-a')).toBe(false);
    expect(regions.flatMap(region => region.nodeIds)).not.toEqual(expect.arrayContaining(['source:original']));
    expect(regions.flatMap(region => region.nodeIds)).not.toEqual(expect.arrayContaining(['osk:hub-a']));
    expect(regions.some(region => region.nodeIds.includes('osk:unplaced'))).toBe(false);
  });

  it('honours link validity boundaries and retractions without dropping a note’s own directory', () => {
    const graph = hubGraph(), visible = { ...graph, nodes: graph.nodes.filter(node => node.id === 'source:original'), edges: [], focusId: null, hops: 0 };
    graph.edges = [graph.edges[0]];
    graph.edges[0].validFrom = '2026-10-01T00:00:00Z';
    graph.edges[0].validTo = '2026-10-02T00:00:00Z';
    const from = Date.parse(graph.edges[0].validFrom), until = Date.parse(graph.edges[0].validTo);
    const directory = { id: 'space:00_Scope/Notes', label: 'Notes', nodeIds: ['source:original'] };
    const hub = { id: 'hub:osk:hub-a', label: 'Saved hub', nodeIds: ['source:original'] };
    expect(contextRegions(visible, graph, from - 1)).toEqual([directory]);
    expect(contextRegions(visible, graph, from)).toEqual([directory, hub]);
    expect(contextRegions(visible, graph, until - 1)).toEqual([directory, hub]);
    expect(contextRegions(visible, graph, until)).toEqual([directory]);
    graph.edges[0].evidence.retracted = true;
    expect(contextRegions(visible, graph, from)).toEqual([directory]);
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
