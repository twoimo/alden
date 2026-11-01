import { describe, expect, it } from 'vitest';
import { overviewCameraDistance } from '../knowledge/camera-fit';

describe('graph overview camera', () => {
  it('uses the compact display space for dense stars while retaining every enclosing corner', () => {
    const points=[{x:-1.5,y:.8,z:.6},{x:1.5,y:-.8,z:.6},{x:0,y:0,z:-1.1}];
    for(const [width,height] of [[439,510],[510,439],[964,586]]) {
      const legacy=overviewCameraDistance(points,width,height,38,.08);
      const dense=overviewCameraDistance(points,width,height,38,.08,true);
      expect(dense).toBeLessThanOrEqual(legacy);
      if(width===439)expect(dense).toBeLessThan(legacy-.4);
      const tanY=Math.tan(38*Math.PI/360),tanX=tanY*width/height;
      for(const p of points)for(const dx of [-.08,.08])for(const dy of [-.08,.08])for(const dz of [-.08,.08]) {
        const depth=dense-p.z-dz;
        expect(depth).toBeGreaterThan(0);
        expect(Math.abs((p.x+dx)/(depth*tanX))).toBeLessThanOrEqual(1-32/width+1e-12);
        expect(Math.abs((p.y+dy)/(depth*tanY))).toBeLessThanOrEqual(1-40/height+1e-12);
      }
    }
  });

  it('keeps every enclosing corner inside a narrow viewport and its label margin', () => {
    const points = [{ x: -1.84, y: 0.78, z: 0.44 }, { x: 1.84, y: -0.78, z: 0.44 }];
    const width = 439, height = 470;
    const distance = overviewCameraDistance(points, width, height);
    const tanY = Math.tan(38 * Math.PI / 360), tanX = tanY * width / height;
    for (const point of points) for (const x of [-0.32, 0.32]) for (const y of [-0.32, 0.32]) for (const z of [-0.32, 0.32]) {
      const depth = distance - point.z - z;
      expect(Math.abs((point.x + x) / (depth * tanX))).toBeLessThanOrEqual(1 - 96 / width + 1e-12);
      expect(Math.abs((point.y + y) / (depth * tanY))).toBeLessThanOrEqual(1 - 40 / height + 1e-12);
    }
    expect(distance).toBeGreaterThan(5.2);
    expect(distance).toBeLessThan(12);
  });
  it('preserves the normal camera distance when the graph already fits', () => {
    expect(overviewCameraDistance([{ x: 0, y: 0, z: 0 }], 1200, 632)).toBe(3.6);
    expect(overviewCameraDistance([], 640, 470)).toBe(3.6);
  });
});
