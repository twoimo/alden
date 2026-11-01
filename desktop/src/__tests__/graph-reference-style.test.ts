import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConstellationNodes } from '../knowledge/constellation';
import { SynapticBridges } from '../knowledge/synapses';
import { RELATION_DEPTH_COLORS } from '../knowledge/graph-palette';
import type { KnowledgeNode, KnowledgeEdge } from '../knowledge/graph-model';
import type { NodeActivity } from '../knowledge/collection-activity';

const evidence={kind:'snapshot' as const,sourceEventIds:[],chatId:'',confirmedAt:null,retracted:false};
const nodes: KnowledgeNode[]=Array.from({length:7},(_,i)=>({
  id:`n${i}`,label:`n${i}`,category:'fixture',importance:0,updatedAt:0,evidence,
  sourceVersion:'v1',sourceTarget:'fixture',
}));
const edges: KnowledgeEdge[]=Array.from({length:5},(_,i)=>({
  source:`n${i}`,target:`n${i+1}`,relation:'related',context:'',weight:1,roomId:'',
  validFrom:'',validTo:'',evidenceMessageId:'',evidence,purpose:'semantic',
}));
const cleanup:Array<()=>void>=[];
afterEach(()=>{vi.useRealTimers();for(const fn of cleanup.splice(0))fn();});
function fixture() {
  const group=new ConstellationNodes(2048);
  group.set(nodes,edges);
  const mesh=group.children[0] as THREE.InstancedMesh;
  cleanup.push(()=>{mesh.dispose();mesh.geometry.dispose();(mesh.material as THREE.Material).dispose();});
  return {group,mesh};
}
function color(mesh:THREE.InstancedMesh,slot:number):string {
  const result=new THREE.Color();mesh.getColorAt(slot,result);return result.getHexString();
}

describe('reference graph meaning',()=>{
  it('uses distance colours with a 4+ cap and leaves an unreachable node distinct',()=>{
    const {group,mesh}=fixture();
    const matrices=mesh.instanceMatrix.array.slice();
    group.highlight('n0');
    group.setRelationDepths(new Map(nodes.slice(0,6).map((node,i)=>[node.id,i])));
    for(let i=0;i<6;i++)expect(color(mesh,i)).toBe(RELATION_DEPTH_COLORS[Math.min(4,i)].slice(1));
    expect(RELATION_DEPTH_COLORS.map(c=>c.slice(1))).not.toContain(color(mesh,6));
    expect(mesh.instanceMatrix.array).toEqual(matrices);
    expect(group.activityCount).toBe(0);
  });
  it('rejects invalid distances and restores the exact topology colours after observed activity expires',()=>{
    vi.useFakeTimers();vi.setSystemTime(10000);
    const {group,mesh}=fixture();
    group.highlight('n0');
    group.setRelationDepths(new Map([['n0',0],['n1',1],['n2',-1],['n3',NaN],['absent',2]]));
    const original=color(mesh,1);
    const event:NodeActivity={event_id:'stored-test',sequence:1,document_id:'n1',version:'v1',
      target_id:'fixture',run_id:'test',origin:'fixture',at:10,kind:'revised',success:true};
    group.showActivity(event);group.advanceActivity(10000);
    expect(color(mesh,1)).not.toBe(original);
    expect(color(mesh,0)).toBe(RELATION_DEPTH_COLORS[0].slice(1));
    group.advanceActivity(20000);
    expect(group.activityCount).toBe(0);
    expect(color(mesh,1)).toBe(original);
    expect(color(mesh,2)).toBe(color(mesh,3));
  });
  it('repaints batched actual bridges by relation distance without changing endpoints or strengths',()=>{
    const bridges=new SynapticBridges(8,false);
    cleanup.push(()=>bridges.dispose());
    bridges.set([{key:'0-1',source:'n0',target:'n1',strength:.5,purpose:'semantic'}],'n0',true);
    const positions=new Map([['n0',{x:0,y:0,z:0}],['n1',{x:1,y:0,z:0}]]);
    bridges.update(0,positions,true);
    const geometry=bridges.mesh.geometry;
    const before=['aStart','aEnd','aStrength'].map(name=>geometry.getAttribute(name).array.slice());
    bridges.setRelationDepths(new Map([['n0',0],['n1',1]]));bridges.update(0,positions,true,false);
    const rgb=geometry.getAttribute('aColor');
    expect(new THREE.Color().setRGB(rgb.getX(0),rgb.getY(0),rgb.getZ(0)).getHexString()).toBe(RELATION_DEPTH_COLORS[1].slice(1));
    for(let i=0;i<before.length;i++)expect(geometry.getAttribute(['aStart','aEnd','aStrength'][i]).array).toEqual(before[i]);
    expect(bridges.diagnostics().active).toBe(1);
  });
});
