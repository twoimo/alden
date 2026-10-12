import { describe, expect, it } from 'vitest';
import { PROJECTED_RELAXATION_LIMITS, relaxProjectedCircles } from '../knowledge/projected-relaxation';
import { projectedLayoutMetrics } from '../knowledge/projected-layout-metrics';

const viewport = { left: 0, top: 0, width: 640, height: 480 };
describe('settled projected display collision correction', () => {
  it('retains settled offsets for unaffected nodes without accumulating displacement', () => {
    const points=[{x:100,y:100,radius:1},{x:400,y:400,radius:1}];
    const seeded=relaxProjectedCircles(points,viewport,new Set(),new Float64Array([4,-2,0,0]));
    expect([...seeded.offsets]).toEqual([4,-2,0,0]);
    expect([...relaxProjectedCircles(points,viewport,new Set(),seeded.offsets).offsets]).toEqual([4,-2,0,0]);
    expect([...relaxProjectedCircles(points,viewport,new Set([0]),seeded.offsets).offsets]).toEqual([4,-2,0,0]);
  });
  it('separates coincident cores without changing source circles or point count', () => {
    const points = Object.freeze(Array.from({length:16},()=>Object.freeze({x:320,y:240,radius:1.6})));
    const before = JSON.stringify(points), result = relaxProjectedCircles(points,viewport);
    const after = points.map((p,i)=>({...p,x:p.x+result.offsets[i*2],y:p.y+result.offsets[i*2+1]}));
    expect(JSON.stringify(points)).toBe(before);
    expect(after).toHaveLength(points.length);
    const originalMetrics=projectedLayoutMetrics(points,viewport), correctedMetrics=projectedLayoutMetrics(after,viewport);
    if(!originalMetrics.available||!correctedMetrics.available)throw Error('valid projection unavailable');
    expect(correctedMetrics.overlapPairs).toBeLessThan(originalMetrics.overlapPairs);
    expect(result.diagnostics.maxShift).toBeLessThanOrEqual(10+1e-9);
    expect([...result.offsets]).toEqual([...relaxProjectedCircles(points,viewport).offsets]);
  });
  it('preserves pins, absent clip-volume points and invalid inputs', () => {
    const points = [{x:320,y:240,radius:1.6},{x:320,y:240,radius:1.6},
      {x:320,y:240,radius:1.6,inClipVolume:false},{x:NaN,y:240,radius:1.6}];
    const {offsets}=relaxProjectedCircles(points,viewport,new Set([0]));
    expect([...offsets.slice(0,2)]).toEqual([0,0]);
    expect([...offsets.slice(4)]).toEqual([0,0,0,0]);
    expect([...offsets]).toSatisfy(a=>a.every(Number.isFinite));
    expect(Math.hypot(offsets[2],offsets[3])).toBeGreaterThan(0);
  });
  it('keeps visible cores in the viewport and bounds adversarial work', () => {
    const points=Array.from({length:2048},(_,i)=>({x:2+(i%4)*.01,y:2+Math.floor(i/4)*.001,radius:1.6}));
    const {offsets,diagnostics}=relaxProjectedCircles(points,viewport);
    expect(diagnostics.pairTests).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.pairTests);
    expect(diagnostics.budgetExhausted).toBe(true);
    points.forEach((p,i)=>{
      expect(Math.hypot(offsets[i*2],offsets[i*2+1])).toBeLessThanOrEqual(10+1e-9);
      expect(p.x+offsets[i*2]).toBeGreaterThanOrEqual(p.radius);
      expect(p.y+offsets[i*2+1]).toBeGreaterThanOrEqual(p.radius);
    });
  });
  it('does no movement for separated points and rejects an invalid viewport', () => {
    const points=[{x:10,y:10,radius:1},{x:30,y:30,radius:1}];
    expect(relaxProjectedCircles(points,viewport).diagnostics.moved).toBe(0);
    expect([...relaxProjectedCircles(points,{...viewport,width:0}).offsets]).toEqual([0,0,0,0]);
  });
});
