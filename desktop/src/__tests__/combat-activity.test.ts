import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CombatActivity } from '../knowledge/combat-activity';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import type { NodeActivity } from '../knowledge/collection-activity';

const owned: CombatActivity[]=[];
afterEach(()=>{for(const item of owned)item.dispose();owned.length=0;});
const receipt=(extra:Partial<NodeActivity>={}):NodeActivity=>({event_id:'read:1',sequence:0,document_id:'a',version:'v1',target_id:'t',run_id:'visible-detail',origin:'alden-detail',at:10,kind:'read',success:true,...extra});
function setup(neighbours=1){
  const graph=parseKnowledgeGraph({nodes:[{id:'a',label:'source',source_version:'v1',source_target:'t'},
    ...Array.from({length:neighbours},(_,i)=>({id:'b'+i,label:'peer'}))],
    edges:Array.from({length:neighbours},(_,i)=>({source:'a',target:'b'+i,relation:'linked'}))});
  const effect=new CombatActivity();owned.push(effect);effect.setGraph(graph.nodes,graph.edges);
  const points=new Map(graph.nodes.map((node,i)=>[node.id,{x:i,y:0,z:0}]));
  return {graph,effect,points};
}
describe('actual-activity space combat',()=>{
  it('is completely quiet without a receipt and never changes the graph',()=>{
    const {graph,effect,points}=setup();const before=JSON.stringify(graph);
    expect(effect.advance(10000,points,800,false)).toBe(false);
    expect(effect.diagnostics()).toMatchObject({exchanges:0,shots:0,impacts:0,ambient:false});
    effect.show(receipt(),10000);effect.advance(10350,points,800,false);
    expect(JSON.stringify(graph)).toBe(before);expect(effect.children).toHaveLength(2);
  });
  it('shows opposing shots, then a collision shield, then expires without ambient refiring',()=>{
    const {effect,points}=setup();expect(effect.show(receipt(),10000)).toBe(true);
    expect(effect.advance(10350,points,800,false)).toBe(true);
    expect(effect.diagnostics()).toMatchObject({shots:2,impacts:0});
    effect.advance(10800,points,800,false);expect(effect.diagnostics()).toMatchObject({shots:0,impacts:1});
    expect(effect.advance(12700,points,800,false)).toBe(false);
    expect(effect.diagnostics()).toMatchObject({exchanges:0,shots:0,impacts:0});
    expect(effect.advance(40000,points,800,false)).toBe(false);
  });
  it('rejects wrong scope/version, future, stale, failed and duplicate events',()=>{
    const {effect}=setup();
    for(const event of [receipt({version:'other'}),receipt({target_id:'other'}),receipt({document_id:'hidden'}),
      receipt({at:11}),receipt({at:0}),receipt({success:false} as never)])expect(effect.show(event,10000)).toBe(false);
    expect(effect.show(receipt(),10000)).toBe(true);expect(effect.show(receipt(),10000)).toBe(false);
  });
  it('never shoots along navigation, expired, future or retracted edges',()=>{
    const {effect,graph,points}=setup(4);
    graph.edges[0].purpose='navigation';graph.edges[1].validTo=new Date(9999).toISOString();
    graph.edges[2].validFrom=new Date(11000).toISOString();graph.edges[3].evidence.retracted=true;
    effect.setGraph(graph.nodes,graph.edges);effect.show(receipt(),10000);effect.advance(10350,points,800,false);
    expect(effect.diagnostics()).toMatchObject({exchanges:1,shots:0,impacts:1});
    const mesh=effect.children[1] as THREE.Mesh<THREE.InstancedBufferGeometry>;
    expect(Array.from(mesh.geometry.getAttribute('aStart').array).slice(0,3)).toEqual([0,0,0]);
    expect(Array.from(mesh.geometry.getAttribute('aEnd').array).slice(0,3)).toEqual([0,0,0]);
  });
  it('withdraws an active path at a relation boundary or source retraction',()=>{
    const {effect,graph,points}=setup();graph.edges[0].validTo=new Date(10500).toISOString();
    effect.show(receipt(),10000);effect.advance(10350,points,800,false);expect(effect.diagnostics().shots).toBe(2);
    effect.advance(10500,points,800,false);expect(effect.diagnostics().exchanges).toBe(0);
    graph.edges[0].validTo='';effect.show(receipt({event_id:'read:2'}),10500);
    graph.nodes[0].evidence.retracted=true;effect.advance(10600,points,800,false);expect(effect.diagnostics().exchanges).toBe(0);
  });
  it('bounds simultaneous paths, reuses GPU arrays and clears on hidden/static/dispose',()=>{
    const {effect,points}=setup(10);
    const mesh=effect.children[0] as THREE.Mesh<THREE.InstancedBufferGeometry,THREE.ShaderMaterial>;
    const arrays=Object.values(mesh.geometry.attributes).map(a=>a.array);
    for(let i=0;i<100;i++)effect.show(receipt({event_id:'read:'+i}),10000);
    effect.advance(10650,points,800,false);
    expect(effect.diagnostics().exchanges).toBeLessThanOrEqual(48);
    expect(effect.diagnostics().shots).toBeLessThanOrEqual(96);
    expect(effect.diagnostics().bufferBytes).toBe(5760);
    Object.values(mesh.geometry.attributes).forEach((a,i)=>expect(a.array).toBe(arrays[i]));
    expect(effect.advance(10700,points,800,true)).toBe(false);expect(effect.diagnostics().exchanges).toBe(0);
    effect.show(receipt({event_id:'read:101'}),10800);effect.clearActivity();expect(effect.diagnostics().shots).toBe(0);
    const geometry=vi.spyOn(mesh.geometry,'dispose'),material=vi.spyOn(mesh.material,'dispose');
    effect.dispose();effect.dispose();expect(geometry).toHaveBeenCalledTimes(1);expect(material).toHaveBeenCalledTimes(1);
    expect(effect.show(receipt({event_id:'read:102'}),11000)).toBe(false);
  });
  it('removes paths immediately when their current graph scope disappears',()=>{
    const {effect,points}=setup();effect.show(receipt(),10000);effect.advance(10350,points,800,false);
    effect.setGraph([],[]);expect(effect.diagnostics()).toMatchObject({exchanges:0,shots:0,impacts:0});
    expect(effect.advance(10400,points,800,false)).toBe(false);
  });
});
