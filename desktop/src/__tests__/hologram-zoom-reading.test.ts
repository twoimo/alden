// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { KnowledgeHologram } from '../knowledge/hologram';
import { parseKnowledgeGraph } from '../knowledge/graph-model';

function fixture() {
  const nodes = parseKnowledgeGraph({nodes: [
    {id:'salary', label:'연봉·복리후생', category:'topic'},
    {id:'essay', label:'자소서', category:'topic'},
    {id:'jobs', label:'채용공고·지원시기', category:'topic'},
    {id:'list', label:'Shopify 운영·결제 자료 검토', category:'hub'},
  ], edges:[]}).nodes;
  const shell = document.createElement('div');
  shell.innerHTML = '<canvas></canvas><div class="knowledge-node-labels"></div>';
  document.body.append(shell);
  const camera = new THREE.PerspectiveCamera(50, 1.5, .01, 100);
  camera.position.z = 10; camera.updateMatrixWorld(true);
  const graph = Object.create(KnowledgeHologram.prototype) as any;
  Object.assign(graph, {
    canvas:shell.querySelector('canvas'), camera, lookAt:new THREE.Vector3(), graphRoot:new THREE.Group(),
    projected:new THREE.Vector3(), viewport:{width:1200,height:800,x:0,y:0},
    view:{nodes,hops:0,focusId:null}, hoveredId:null,
    positions:new Map(nodes.map((node,index)=>[node.id,new THREE.Vector3((index-1.5)*1.1,0,0)])),
    displayedPositions:new Map(), labels:new Map(), labelBounds:[],
    globalLabels:new Set(nodes.map(node=>node.id)), globalLabelRanks:new Map(nodes.map((node,i)=>[node.id,i])),
    zoomLabelCandidates:nodes, zoomLabels:new Set(), labelZoom:1, labelFitDistance:10,
  });
  return graph;
}

afterEach(()=>{document.body.replaceChildren();vi.restoreAllMocks();});

describe('zoom-dependent passive graph captions',()=>{
  it('keeps topics and saved lists out of the fitted overview and reveals them on zoom',()=>{
    const graph=fixture(), before=JSON.stringify(graph.view.nodes);
    graph.updateZoomLabels();
    expect([...graph.zoomLabels]).toEqual([]);
    expect(graph.labels.size).toBe(0);
    graph.camera.position.z=5;graph.camera.updateMatrixWorld(true);graph.updateZoomLabels();
    expect(graph.zoomLabels.has('salary')).toBe(true);
    expect(graph.zoomLabels.has('essay')).toBe(true);
    expect(graph.zoomLabels.has('jobs')).toBe(true);
    expect(Number(graph.labels.get('salary').style.opacity)).toBe(1);
    expect(JSON.stringify(graph.view.nodes)).toBe(before);
    graph.camera.position.z=10;graph.camera.updateMatrixWorld(true);graph.updateZoomLabels();
    expect(graph.labels.size).toBe(0);
  });

  it('retains intentional hover and selection access at overview scale',()=>{
    const graph=fixture();graph.hoveredId='salary';graph.view.focusId='essay';
    graph.updateZoomLabels();
    expect([...graph.labels.keys()]).toEqual(['essay','salary']);
    expect([...graph.labels.values()].every((label:any)=>label.style.opacity==='1')).toBe(true);
  });

  it('does not project candidates again during stationary activity frames; invalidates changed inputs',()=>{
    const graph=fixture();graph.camera.position.z=5;graph.camera.updateMatrixWorld(true);
    const project=vi.spyOn(graph.projected,'project');
    graph.updateZoomLabels(false);expect(project).toHaveBeenCalled();project.mockClear();
    for(let frame=0;frame<30;frame++)graph.updateZoomLabels(false);
    expect(project).not.toHaveBeenCalled();
    graph.updateZoomLabels(true);expect(project).toHaveBeenCalled();project.mockClear();
    graph.viewport.width=1000;graph.updateZoomLabels(false);expect(project).toHaveBeenCalled();project.mockClear();
    graph.hoveredId='salary';graph.updateZoomLabels(false);expect(project).toHaveBeenCalled();project.mockClear();
    graph.zoomLabelCandidates=[...graph.zoomLabelCandidates];graph.updateZoomLabels(false);expect(project).toHaveBeenCalled();
  });
});
