import * as THREE from 'three';
import { describe, expect, it, vi } from 'vitest';
import { KnowledgeHologram } from '../knowledge/hologram';

describe('focused branch camera envelope',()=>{
  for(const [width,height] of [[624,570],[350,280],[1100,720]]) {
    it(`keeps the full neighbourhood visible at ${width}×${height}`,()=>{
      const root=new THREE.Vector3(.2,-.1,.3);
      const points=[root,root.clone().add(new THREE.Vector3(1.8,1.2,.6)),
        root.clone().add(new THREE.Vector3(-1.8,-1.2,-.4))];
      const graph=Object.create(KnowledgeHologram.prototype) as unknown as Record<string,any>;
      Object.assign(graph,{anchors:new Map(points.map((point,i)=>['n'+i,point])),positions:new Map(),
        view:{hops:1,nodes:points.map((_,i)=>({id:'n'+i}))},
        viewport:{width,height},camera:new THREE.PerspectiveCamera(38,width/height,.1,60),
        orbit:{minDistance:2.2,maxDistance:12},desiredLookAt:new THREE.Vector3(),
        desiredCamera:new THREE.Vector3(),invalidateFrame:vi.fn()});
      graph.focusCamera('n0');
      const camera=graph.camera as THREE.PerspectiveCamera;
      camera.position.copy(graph.desiredCamera);camera.lookAt(graph.desiredLookAt);camera.updateMatrixWorld(true);
      for(const point of points) {
        const projected=point.clone().project(camera);
        expect(Math.abs(projected.x)).toBeLessThan(1);
        expect(Math.abs(projected.y)).toBeLessThan(1);
        expect(projected.z).toBeGreaterThan(-1);expect(projected.z).toBeLessThan(1);
      }
      expect(graph.desiredLookAt.toArray()).toEqual(root.toArray());
    });
  }
});
