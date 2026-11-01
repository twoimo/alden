// CPU buffer update benchmark; no WebGL, device latency or whole-app claim.
import * as THREE from 'three';
import { performance } from 'node:perf_hooks';
import { ConstellationNodes } from '../knowledge/constellation';
import { SynapticBridges } from '../knowledge/synapses';
import type { KnowledgeNode } from '../knowledge/graph-model';

const count = 2048;
const nodes: KnowledgeNode[] = Array.from({length:count}, (_,i) => ({
  id:String(i),label:String(i),category:'fixture',importance:0,updatedAt:0,
  sourceTarget:String(i%4),evidence:{kind:'snapshot',sourceEventIds:[],chatId:'',confirmedAt:null,retracted:false},
}));
const positions = new Map(nodes.map((node,i)=>[node.id,new THREE.Vector3(Math.sin(i)*2,Math.cos(i)*2,i%9*.01)]));
const stars = new ConstellationNodes(count);
stars.set(nodes,[]);stars.update(positions);
const bridges = new SynapticBridges(4096,false);
bridges.set(Array.from({length:4096},(_,i)=>({key:String(i),source:String(i%count),target:String((i+1)%count),strength:.5})),null,true);
bridges.update(0,positions,true);
function measure(moving:boolean) {
  const samples:number[]=[];
  for(let i=0;i<1100;i++) {
    if(moving) positions.get('0')!.x+=.00001;
    const start=performance.now();stars.update(positions);bridges.update(1/30,positions,true,moving);
    if(i>=100)samples.push(performance.now()-start);
  }
  samples.sort((a,b)=>a-b);
  return {samples:samples.length,p50Ms:samples[499],p95Ms:samples[949]};
}
const mesh=stars.children[0] as THREE.InstancedMesh;
console.log(JSON.stringify({scope:'Node CPU buffer updates, synthetic 2048 nodes/4096 links; no GPU or UI latency claim',
  runtime:process.version,stationary:measure(false),changing:measure(true),
  starTriangles:mesh.geometry.index!.count/3*count,linkTriangles:bridges.mesh.geometry.index!.count/3*4096}));
bridges.dispose();mesh.dispose();mesh.geometry.dispose();(mesh.material as THREE.Material).dispose();
