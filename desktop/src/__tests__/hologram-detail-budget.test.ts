// @vitest-environment happy-dom
import * as THREE from 'three';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeHologram } from '../knowledge/hologram';
import { ConstellationNodes } from '../knowledge/constellation';
import { SynapticBridges } from '../knowledge/synapses';
import type { KnowledgeNode, KnowledgeView } from '../knowledge/graph-model';

// Exercise production rebuilding without a WebGL context or source/provider calls.
const owned: Array<() => void> = [];
afterEach(() => { for (const dispose of owned.splice(0)) dispose(); });

function fixture() {
  const nodes: KnowledgeNode[] = Array.from({length:24}, (_, i) => ({
    id:`n${i}`, label:`n${i}`, category:'fixture', importance:25, updatedAt:0,
    evidence:{kind:'snapshot',sourceEventIds:[],chatId:'',confirmedAt:null,retracted:false},
  }));
  const view: KnowledgeView = {nodes,edges:[],hops:1,focusId:'n0'};
  const positions = new Map(nodes.map((node,i) => [node.id,new THREE.Vector3(i*.2,0,0)]));
  const constellation = new ConstellationNodes(2048);
  const synapses = new SynapticBridges(), overview = new SynapticBridges(4096,false);
  const root = new THREE.Group();
  root.add(constellation,synapses.mesh,overview.mesh);
  const canvas=document.createElement('canvas');
  const shell=document.createElement('div');
  shell.innerHTML='<div class="knowledge-depth-legend" hidden></div>';
  shell.append(canvas); document.body.append(shell);
  const value=Object.create(KnowledgeHologram.prototype) as KnowledgeHologram;
  const fake = value as unknown as Record<string, any>;
  Object.assign(fake, {
    view,graph:view,positions,anchors:positions,displayedPositions:new Map(),
    graphRoot:root,canvas,constellationNodes:constellation,synapses,overviewSynapses:overview,
    nodeMeshes:new Map(),neuronMeshes:new Map(),neuronPorts:new Map(),orbMeshes:new Map(),
    labels:new Map(),labelBounds:[],globalLabels:new Set(),globalLabelRanks:new Map(),
    pickGeometry:null,pickMaterial:null,orbit:{mouseButtons:{}},hoveredId:null,
    nebulae:{set:vi.fn(),update:vi.fn()},motion:{matches:true},reducedMotion:true,
    layoutPositions:vi.fn(),preparePositionMotion:vi.fn(),cancelHover:vi.fn(),
    viewport:{height:400},renderer:{getPixelRatio:()=>1},
  });
  owned.push(() => {
    const geometries=new Set<THREE.BufferGeometry>(),materials=new Set<THREE.Material>();
    root.traverse(child => {
      if(child instanceof THREE.Mesh) {
        if(child instanceof THREE.InstancedMesh) child.dispose();
        geometries.add(child.geometry);
        for(const material of Array.isArray(child.material)?child.material:[child.material]) materials.add(material);
      }
    });
    for(const geometry of geometries)geometry.dispose();
    for(const material of materials)material.dispose();
    fake.pickGeometry?.dispose();fake.pickMaterial?.dispose();shell.remove();
  });
  return fake;
}

describe('detail geometry admission', () => {
  it('keeps a 24-node relation view as one dot batch until actual detail zoom', () => {
    const graph=fixture();
    graph.rebuildGraph();
    expect(graph.neuronMeshes.size).toBe(0);
    expect(graph.orbMeshes.size).toBe(0);
    expect(new Set([...graph.nodeMeshes.values()].map((mesh:any)=>mesh.geometry)).size).toBe(1);
    expect([...graph.nodeMeshes.values()].every((mesh:any)=>mesh.parent===null)).toBe(true);
    const before=graph.graphRoot.children.length;
    graph.applyDetailMode(true);
    expect(graph.neuronMeshes.size).toBe(24);
    expect(graph.graphRoot.children.length).toBe(before+24);
    const retained=[...graph.neuronMeshes.values()];
    graph.applyDetailMode(false);graph.applyDetailMode(true);
    expect([...graph.neuronMeshes.values()]).toEqual(retained);
    for(const mesh of graph.nodeMeshes.values()) {
      expect(mesh.scale.x).toBeCloseTo(mesh.userData.glyph.radius);
    }
  });

  it('retires old glyphs on graph rebuild and does not allocate for global selection', () => {
    const graph=fixture();graph.rebuildGraph();graph.applyDetailMode(true);
    const disposed=vi.spyOn([...graph.neuronMeshes.values()][0].children[0].geometry,'dispose');
    graph.view={...graph.view,hops:0};
    graph.rebuildGraph();
    expect(disposed).toHaveBeenCalledTimes(1);
    graph.ensureDetailGlyphs();
    expect(graph.neuronMeshes.size).toBe(0);
    expect(graph.orbMeshes.size).toBe(0);
  });
});
