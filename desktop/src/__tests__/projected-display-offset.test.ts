import * as THREE from 'three';
import { describe,expect,it } from 'vitest';
import { projectedOffsetGain } from '../knowledge/projected-display-offset';

describe('retained offsets under current perspective',()=>{
  it('bounds zoom, viewport, orbit and depth-changing offsets by independent projection',()=>{
    const base=new THREE.Vector3(.1,-.1,.2),offset=new THREE.Vector3(.4,.3,.5);
    const before=JSON.stringify({base,offset});
    for(const [x,y,z,width,height] of [[0,0,8,900,600],[0,0,4,900,600],[0,0,8,1800,1200],[5,-2,4,640,900]]){
      const camera=new THREE.PerspectiveCamera(38,width/height,.1,60);
      camera.position.set(x,y,z);camera.lookAt(0,0,0);camera.updateMatrixWorld(true);
      const root=new THREE.Matrix4().makeRotationY(.3);
      const clip=new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse).multiply(root);
      const gain=projectedOffsetGain(base,offset,clip.elements,width,height);
      const a=base.clone().applyMatrix4(root).project(camera);
      const b=base.clone().addScaledVector(offset,gain).applyMatrix4(root).project(camera);
      expect(gain).toBeGreaterThan(0);expect(gain).toBeLessThan(1);
      expect(Math.hypot((b.x-a.x)*width/2,(b.y-a.y)*height/2)).toBeCloseTo(10,8);
    }
    expect(JSON.stringify({base,offset})).toBe(before);
  });
  it('preserves small offsets and rejects invalid or behind-camera projections',()=>{
    const camera=new THREE.PerspectiveCamera(38,1,.1,60);
    camera.position.z=8;camera.lookAt(0,0,0);camera.updateMatrixWorld(true);
    const clip=new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse);
    const base=new THREE.Vector3(),offset=new THREE.Vector3(.0001,0,0);
    expect(projectedOffsetGain(base,offset,clip.elements,600,600)).toBe(1);
    expect(projectedOffsetGain(base,new THREE.Vector3(0,0,9),clip.elements,600,600)).toBe(0);
    expect(projectedOffsetGain(base,offset,clip.elements,0,600)).toBe(0);
    expect(projectedOffsetGain(base,new THREE.Vector3(NaN,0,0),clip.elements,600,600)).toBe(0);
  });
});
