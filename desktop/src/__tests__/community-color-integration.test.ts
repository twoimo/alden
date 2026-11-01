// @vitest-environment happy-dom
import * as THREE from 'three';
import { describe, expect, it, vi } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { SemanticLayoutEngine } from '../knowledge/semantic-layout';
import { ConstellationNodes } from '../knowledge/constellation';
import { KnowledgeHologram } from '../knowledge/hologram';

describe('actual solver communities in the dot batch',()=>{
  it('gives four unrelated source-link groups distinct colors without changing their facts or identities',()=>{
    const graph=parseKnowledgeGraph({nodes:Array.from({length:12},(_,i)=>({id:`synthetic-${i}`,label:'same label',source_target:`target-${i%3}`})),
      edges:Array.from({length:8},(_,i)=>({source:`synthetic-${Math.floor(i/2)*3}`,target:`synthetic-${Math.floor(i/2)*3+i%2+1}`,weight:1}))});
    const view={...graph,hops:0,focusId:null},facts=JSON.stringify(view),engine=new SemanticLayoutEngine();
    const result=engine.layout(view,new Map(),[],{nowMs:1000}),checkpoint=engine.checkpoint();
    const stars=new ConstellationNodes(2048);stars.set(view.nodes,view.edges);stars.setRelationDepths(new Map());
    const canvas=document.createElement('canvas'),shell=document.createElement('div');
    shell.innerHTML='<div class="knowledge-depth-legend" hidden></div>';shell.append(canvas);document.body.append(shell);
    const hologram=Object.assign(Object.create(KnowledgeHologram.prototype),{requestedAnimation:true,disposed:false,view,graph,
      anchors:new Map(),positions:new Map(),layoutChanged:new Set(),layoutDiagnostics:null,constellationNodes:stars,canvas,
      fitAfterSemanticLayout:false,preparePositionMotion:vi.fn(),orbitActive:false,restoredWindowSize:null,
      loop:{start:vi.fn()},invalidateFrame:vi.fn()});
    hologram.applySemanticLayout({id:1,anchors:[...result.anchors],diagnostics:result.diagnostics,checkpoint});
    const batch=stars.children[0] as THREE.InstancedMesh,colors=[];
    for(const slot of [0,3,6,9]){const color=new THREE.Color();batch.getColorAt(slot,color);colors.push(color.getHexString());}
    expect(new Set(colors).size).toBe(4);
    expect(stars.radius('synthetic-11')).toBeGreaterThan(0);
    expect(JSON.stringify(view)).toBe(facts);
    const legend=shell.querySelector<HTMLElement>('.knowledge-depth-legend')!;
    expect(legend.hidden).toBe(false);expect(legend.dataset.kind).toBe('communities');
    expect(legend.textContent).toContain('주요 모임');
    stars.setRelationDepths(new Map([['synthetic-0',0],['synthetic-1',1]]));
    stars.setCommunityColors(new Map(view.nodes.map(n=>[n.id,4])));
    const selected=new THREE.Color();batch.getColorAt(0,selected);expect(selected.getHexString()).toBe('f4f2f7');
    stars.setRelationDepths(new Map());
    const invalid={...checkpoint,labels:checkpoint.labels.slice(1)};
    const updated=[...result.anchors].map(([id,p])=>[id,{...p,x:p.x+.01}] as [string,{x:number;y:number;z:number}]);
    hologram.applySemanticLayout({id:2,anchors:updated,diagnostics:result.diagnostics,checkpoint:invalid});
    expect(legend.hidden).toBe(true);expect(hologram.anchors.get(updated[0][0]).x).toBe(updated[0][1].x);
    const cleared=[];for(const slot of [0,3,6,9]){const c=new THREE.Color();batch.getColorAt(slot,c);cleared.push(c.getHexString());}
    expect(new Set(cleared).size).toBe(1);
    shell.remove();batch.dispose();batch.geometry.dispose();(batch.material as THREE.Material).dispose();
  });
});
