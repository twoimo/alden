// @vitest-environment happy-dom
import * as THREE from 'three';
import { describe, expect, it, vi } from 'vitest';
import { KnowledgeHologram } from '../knowledge/hologram';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { ConstellationNodes } from '../knowledge/constellation';
import { SynapticBridges } from '../knowledge/synapses';
import { PositionTransition } from '../knowledge/position-transition';
import { overviewDisplayPoint } from '../knowledge/overview-presentation';
import { pickKnowledgeSphere } from '../knowledge/picking';

// Real frame, projection, instance buffers, bridges and picker; only the WebGL
// draw and upstream settled worker are replaced. Synthetic records are not live
// source evidence. Distinct source/display maps detect accidental source edits.
function fixture(reducedMotion: boolean) {
  const graph = parseKnowledgeGraph({nodes: Array.from({length:121}, (_, i) => ({id:'n'+i,label:'Node '+i})),edges:[]});
  const view = {...graph,hops:0,focusId:'n0'};
  const positions = new Map(graph.nodes.map((node,i) => [node.id, i<2
    ? new THREE.Vector3(i*.00001,0,0)
    : new THREE.Vector3(Math.cos(i*2*Math.PI/119),Math.sin(i*2*Math.PI/119),0)]));
  const anchors = new Map([...positions].map(([id,p])=>[id,p.clone()]));
  const displayedPositions = new Map([...positions].map(([id,p])=>[id,p.clone()]));
  const camera = new THREE.PerspectiveCamera(38,900/600,.1,60);
  camera.position.set(0,0,8);camera.lookAt(0,0,0);camera.updateMatrixWorld(true);
  const graphRoot = new THREE.Group(), scene = new THREE.Scene();scene.add(graphRoot);
  const constellationNodes = new ConstellationNodes(2048);constellationNodes.set(graph.nodes,[]);
  const synapses = new SynapticBridges(), overviewSynapses = new SynapticBridges(4096,false);
  overviewSynapses.set([{key:'n0-n1',source:'n0',target:'n1',strength:.5}],null,true);
  graphRoot.add(constellationNodes,synapses.mesh,overviewSynapses.mesh);
  const geometry = new THREE.SphereGeometry(1,8,6), material = new THREE.MeshBasicMaterial();
  const nodeMeshes = new Map(graph.nodes.map(node => {
    const mesh = new THREE.Mesh(geometry,material);mesh.userData.nodeId=node.id;return [node.id,mesh];
  }));
  const shell=document.createElement('div'),canvas=document.createElement('canvas'),label=document.createElement('span');
  label.textContent='Node 1';shell.append(canvas,label);document.body.append(shell);
  Object.defineProperty(label,'offsetWidth',{get:()=>80});Object.defineProperty(label,'offsetHeight',{get:()=>22});
  const h=Object.create(KnowledgeHologram.prototype) as Record<string,any>;
  Object.assign(h,{
    graph,view,positions,anchors,displayedPositions,displayOffsets:new Map(),displayTransition:new PositionTransition(),
    displayClipMatrix:new THREE.Matrix4(),displayRelaxationPending:false,semanticLayout:{settled:true},positionTransition:new PositionTransition(),
    plasticity:{moving:false,setDisplayScale:vi.fn(),advance:vi.fn(),forEachPoint:vi.fn()},
    camera,desiredCamera:camera.position.clone(),lookAt:new THREE.Vector3(),desiredLookAt:new THREE.Vector3(),
    graphRoot,scene,canvas,viewport:{x:0,y:0,width:900,height:600},projected:new THREE.Vector3(),
    constellationNodes,synapses,overviewSynapses,nodeMeshes,orbMeshes:new Map(),neuronMeshes:new Map(),
    nebulae:{moving:false,update:vi.fn()},motion:{matches:false},reducedMotion,paused:false,disposed:false,
    orbit:{target:new THREE.Vector3(),update:vi.fn()},orbitActive:false,neuronDetail:false,
    provisionalOverviewPositions:new Set(),globalLabels:new Set(['n1']),labels:new Map([['n1',label]]),
    labelBounds:[{width:0,left:0,top:0,visible:false}],excludedLabelBounds:[],
    voiceEnvelope:{needsFrame:false,displayedRms:0},latestVoice:null,
    loop:{setInteractive:vi.fn(),setVoiceActive:vi.fn(),stop:vi.fn()},
    renderer:{render:()=>{scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);}},
    renderedFrames:0,
  });
  return {h,positions,anchors,displayedPositions,nodeMeshes,graphRoot,camera,constellationNodes,overviewSynapses,label,
    dispose:()=>{synapses.dispose();overviewSynapses.dispose();geometry.dispose();material.dispose();
      constellationNodes.traverse(child=>{if(child instanceof THREE.InstancedMesh){child.dispose();child.geometry.dispose();
        (child.material as THREE.Material).dispose();}});shell.remove();}};
}

describe('display collision correction in the production frame',()=>{
  it('freezes a newly selected displayed node while the source worker is pending',()=>{
    const f=fixture(false),{h}=f;
    try {
      h.displayOffsets.set('n1',new THREE.Vector3(.01,0,0));
      h.displayTransition.setTargets(new Map([['n1',new THREE.Vector3(.03,0,0)]]),h.displayOffsets,new Set());
      h.view={...h.view,focusId:'n1'};h.semanticLayout.settled=false;h.displayRelaxationPending=true;
      h.render(1/60);
      expect(h.displayOffsets.get('n1').toArray()).toEqual([.01,0,0]);
      expect(h.displayTransition.moving).toBe(false);
    }finally{f.dispose();}
  });
  it('reports a rejected oversized correction without retrying it every frame',()=>{
    const f=fixture(false),{h}=f;
    try {
      const extra=Array.from({length:2049-h.view.nodes.length},(_,i)=>({...h.view.nodes[0],id:'overflow'+i}));
      h.view={...h.view,nodes:[...h.view.nodes,...extra]};
      extra.forEach(node=>h.positions.set(node.id,new THREE.Vector3(0,0,0)));
      h.prepareDisplayCorrection();
      expect(h.projectedRelaxation.state).toBe('rejected');
      expect(h.projectedRelaxation.reason).toBe('display_correction_invalid_targets');
      expect(h.projectedRelaxation.inputTruncated).toBe(true);
      expect(h.displayRelaxationPending).toBe(false);
      expect(h.displayTransition.moving).toBe(false);
    }finally{f.dispose();}
  });

  it('completes reduced-motion camera fit before applying pending correction',()=>{
    const f=fixture(true),{h}=f;
    try {
      h.displayRelaxationPending=true;h.desiredCamera.set(0,0,6);
      h.render(1/60);
      expect(h.camera.position.toArray()).toEqual([0,0,6]);
      expect(h.displayRelaxationPending).toBe(false);
      expect(h.projectedRelaxation.moved).toBeGreaterThan(0);
      expect(h.auditProjectedLayout().metrics.overlapPairs).toBe(0);
    }finally{f.dispose();}
  });
  it.each(['resize','zoom'])('bounds retained display shifts during active %s',change=>{
    const f=fixture(false),{h}=f;
    try {
      h.displayOffsets.set('n1',new THREE.Vector3(.08,0,0));h.orbitActive=true;
      if(change==='resize'){h.viewport.height=1200;h.camera.aspect=900/1200;h.camera.updateProjectionMatrix();}
      else h.desiredCamera.set(0,0,4);
      h.reducedMotion=true;h.render(1/60);
      const base=new THREE.Vector3();overviewDisplayPoint(f.positions.get('n1')!,h.viewport.width,h.viewport.height,true,base);
      const original=base.project(h.camera),shown=f.displayedPositions.get('n1')!.clone().project(h.camera);
      expect(Math.hypot((shown.x-original.x)*h.viewport.width/2,(shown.y-original.y)*h.viewport.height/2)).toBeLessThanOrEqual(10+1e-8);
      expect(h.displayOffsets.get('n1').toArray()).toEqual([.08,0,0]);
    }finally{f.dispose();}
  });
  it('keeps a newly focused overview node at its settled displayed point',()=>{
    const f=fixture(false),{h}=f;
    try {
      h.displayRelaxationPending=true;
      for(let i=0;i<180;i++){h.render(1/60);if(!h.displayTransition.moving)break;}
      const shown=f.displayedPositions.get('n1')!.clone(),offset=h.displayOffsets.get('n1').clone();
      h.view={...h.view,focusId:'n1'};h.displayRelaxationPending=true;h.render(1/60);
      expect(h.displayOffsets.get('n1').toArray()).toEqual(offset.toArray());
      expect(f.displayedPositions.get('n1')!.distanceTo(shown)).toBeLessThan(1e-12);
    }finally{f.dispose();}
  });

  it.each([false,true])('applies correction on the final source-settling frame (reduced=%s)',reduced=>{
    const f=fixture(reduced),{h}=f;
    try {
      h.displayRelaxationPending=true;h.plasticity.moving=true;
      h.plasticity.advance=()=>{h.plasticity.moving=false;};
      h.plasticity.settle=()=>{h.plasticity.moving=false;};
      h.render(1/60);
      expect(h.displayRelaxationPending).toBe(false);
      expect(h.projectedRelaxation.moved).toBeGreaterThan(0);
      if(reduced)expect(h.auditProjectedLayout().metrics.overlapPairs).toBe(0);
      else expect(h.displayTransition.moving).toBe(true);
    }finally{f.dispose();}
  });
  it.each([false,true])('preserves sources and pins; synchronizes GPU, labels, picking and bridges (reduced=%s)', reduced=>{
    const f=fixture(reduced),{h,positions,anchors,displayedPositions}=f;
    const snapshot=()=>JSON.stringify({positions:[...positions],anchors:[...anchors],graph:h.graph});
    const before=snapshot();
    try {
      h.render(1/60);const initial=h.auditProjectedLayout();
      expect(initial.metrics.overlapPairs).toBeGreaterThan(0);
      h.displayRelaxationPending=true;
      for(let i=0;i<180;i++){h.render(1/60);if(!h.displayTransition.moving)break;}
      expect(h.displayTransition.moving).toBe(false);
      expect(h.loop.stop).toHaveBeenCalled();
      expect(h.auditProjectedLayout().metrics.overlapPairs).toBeLessThan(initial.metrics.overlapPairs);
      expect(h.auditProjectedLayout().metrics.visiblePoints).toBe(121);
      expect(snapshot()).toBe(before);
      const audited=h.auditProjectedLayout(true);
      expect(audited.circles.map((circle:any)=>circle.solverTarget)).toEqual(h.view.nodes.map((node:any)=>anchors.get(node.id)!.toArray()));
      expect(h.auditProjectedLayout().circles).toBeUndefined();
      expect(h.displayOffsets.get('n0').length()).toBe(0);
      expect(h.displayOffsets.get('n1').length()).toBeGreaterThan(0);
      const shown=displayedPositions.get('n1')!;
      expect(f.nodeMeshes.get('n1')!.position.toArray()).toEqual(shown.toArray());
      const instances=f.constellationNodes.children[0] as THREE.InstancedMesh,matrix=new THREE.Matrix4();
      instances.getMatrixAt(1,matrix);
      const gpu=new THREE.Vector3().setFromMatrixPosition(matrix);
      expect(gpu.distanceTo(shown)).toBeLessThan(1e-6);
      const end=f.overviewSynapses.mesh.geometry.getAttribute('aEnd');
      expect(new THREE.Vector3(end.getX(0),end.getY(0),end.getZ(0)).distanceTo(shown)).toBeLessThan(1e-6);
      const projected=shown.clone().project(f.camera),ray=new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2(projected.x,projected.y),f.camera);
      expect(pickKnowledgeSphere(ray,[f.nodeMeshes.get('n1')!])?.userData.nodeId).toBe('n1');
      expect(f.label.hidden).toBe(false);
      expect(h.labelBounds[0].left + h.labelBounds[0].width/2).toBeCloseTo((projected.x+1)*450,6);
      // A camera re-projection retains its bounded correction rather than
      // adding repeated displacement, and local exploration uses exact points.
      const settled=shown.clone();h.displayRelaxationPending=true;h.render(1/60);
      expect(displayedPositions.get('n1')!.distanceTo(settled)).toBeLessThan(1e-6);
      h.view={...h.view,hops:1};h.render(1/60);
      expect(displayedPositions.get('n1')!.toArray()).toEqual(positions.get('n1')!.toArray());
      expect(snapshot()).toBe(before);
      expect(overviewDisplayPoint(positions.get('n1')!,900,600,false,new THREE.Vector3())).toEqual(positions.get('n1'));
    } finally {f.dispose();}
  });
});
