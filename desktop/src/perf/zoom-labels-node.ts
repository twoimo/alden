import * as THREE from 'three';
import { performance } from 'node:perf_hooks';
import { KnowledgeHologram } from '../knowledge/hologram';
import { parseKnowledgeGraph, type KnowledgeView } from '../knowledge/graph-model';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';

function graphData(count: number) {
  return parseKnowledgeGraph({nodes:Array.from({length:count},(_,i)=>({id:`n${i}`,label:`저장 주제 ${i}`,category:'topic'})),
    edges:Array.from({length:count-1},(_,i)=>({source:`n${i}`,target:`n${i+1}`,kind:'related',weight:1}))});
}

export function labelWorkload(count: number) {
  const nodes=graphData(count).nodes;
  const camera=new THREE.PerspectiveCamera(50,1.5,.01,100);
  camera.position.z=5;camera.updateMatrixWorld(true);
  const graph=Object.create(KnowledgeHologram.prototype);
  Object.assign(graph, {camera,lookAt:new THREE.Vector3(),graphRoot:new THREE.Group(),projected:new THREE.Vector3(),
    viewport:{width:1200,height:800},view:{nodes,hops:0,focusId:null},hoveredId:null,
    positions:new Map(nodes.map((node,i)=>[node.id,new THREE.Vector3((i%32-16)*.09,(Math.floor(i/32)-16)*.05,0)])),
    displayedPositions:new Map(),labels:new Map(),labelBounds:[],
    globalLabels:new Set(nodes.slice(0,8).map(node=>node.id)),
    globalLabelRanks:new Map(nodes.map((node,i)=>[node.id,i])),zoomLabelCandidates:nodes,
    zoomLabels:new Set(),labelZoom:1,labelFitDistance:10,
    // Isolate actual projection/selection. DOM placement, WebGL and source reads
    // are deliberately outside this CPU workload.
    refreshOverviewLabels() {},
  });
  graph.updateZoomLabels(true);
  return (moving:boolean)=>{graph.updateZoomLabels(moving);return graph.zoomLabels.size as number;};
}

export function layoutWorkload(count:number) {
  const graph=graphData(count),view={nodes:graph.nodes,edges:graph.edges,hops:0,focusId:null} as KnowledgeView;
  const start=performance.now();
  const result=new SemanticLayoutEngine().layout(view,new Map());
  const ms=performance.now()-start;
  return {ms,diagnostics:result.diagnostics,snapshot:JSON.stringify({anchors:[...result.anchors],clusters:[...result.clusterIds]})};
}
