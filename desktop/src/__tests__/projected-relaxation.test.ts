import { describe, expect, it } from 'vitest';
import { PROJECTED_RELAXATION_LIMITS, relaxProjectedCircles } from '../knowledge/projected-relaxation';
import { projectedLayoutMetrics } from '../knowledge/projected-layout-metrics';

const viewport = { left: 0, top: 0, width: 640, height: 480 };
describe('settled projected display collision correction', () => {
  it('leaves a readable gap between tiny cores while respecting a pin', () => {
    const points = [{ x: 100, y: 100, radius: .6 }, { x: 102, y: 100, radius: .6 }];
    const before = JSON.stringify(points);
    for (const pinned of [new Set<number>(), new Set([0])]) {
      const result = relaxProjectedCircles(points, viewport, pinned);
      const a = { x: points[0].x + result.offsets[0], y: points[0].y + result.offsets[1] };
      const b = { x: points[1].x + result.offsets[2], y: points[1].y + result.offsets[3] };
      expect(Math.hypot(a.x - b.x, a.y - b.y) - points[0].radius - points[1].radius).toBeGreaterThanOrEqual(2);
      if (pinned.has(0)) expect(a).toEqual({ x: 100, y: 100 });
      expect(result.diagnostics.maxShift).toBeLessThanOrEqual(10);
      expect(result.diagnostics.pairTests).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.pairTests);
    }
    expect(JSON.stringify(points)).toBe(before);
  });

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

describe('dense viewports keep core separation ahead of optional whitespace',()=>{
  it.each([2.7,3])('does not turn a separated %s-pixel grid into overlapping cores',spacing=>{
    const area={left:0,top:0,width:439,height:332};
    const points=Array.from({length:1024},(_,i)=>({x:80+i%32*spacing,y:50+Math.floor(i/32)*spacing,radius:1.3}));
    const original=JSON.stringify(points),before=projectedLayoutMetrics(points,area);
    if(!before.available)throw Error('fixture projection unavailable');
    expect(before.overlapPairs).toBe(0);
    const {offsets,diagnostics}=relaxProjectedCircles(points,area);
    const corrected=points.map((p,i)=>({...p,x:p.x+offsets[i*2],y:p.y+offsets[i*2+1]}));
    const after=projectedLayoutMetrics(corrected,area);
    if(!after.available)throw Error('corrected projection unavailable');
    expect(after.visiblePoints).toBe(points.length);
    expect(after.overlapPairs).toBe(0);
    expect(JSON.stringify(points)).toBe(original);
    expect(diagnostics.maxShift).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.shiftCssPx+1e-9);
    expect(diagnostics.pairTests).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.pairTests);
  });
});

describe('candidate admission and fixed geometry work',()=>{
  const points=()=>Array.from({length:2048},()=>({x:320,y:240,radius:1.6}));
  class CountedPins extends Set<number>{
    calls=0;
    override has(value:number){this.calls++;return super.has(value);}
  }
  it('retains all fixed offsets without quadratically querying fixed memberships',()=>{
    const input=points(),fixed=new CountedPins(input.map((_,i)=>i)),prior=new Float64Array(input.length*2);
    prior[14]=4;prior[15]=-2;
    const r=relaxProjectedCircles(input,viewport,fixed,prior);
    expect([...r.offsets]).toEqual([...prior]);
    expect(fixed.calls).toBeLessThanOrEqual(input.length);
    expect(r.diagnostics.pairTests).toBe(0);
    expect(r.diagnostics.candidateVisits).toBe(0);
  });
  it('bounds traversal with only two movable points and preserves immutable pins',()=>{
    const input=points(),fixed=new CountedPins(Array.from({length:2046},(_,i)=>i));
    const r=relaxProjectedCircles(input,viewport,fixed);
    expect(fixed.calls).toBeLessThanOrEqual(input.length);
    expect([...r.offsets.slice(0,4092)].every(value=>value===0)).toBe(true);
    expect([...r.offsets].every(Number.isFinite)).toBe(true);
    expect(r.diagnostics.candidateVisits).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.candidateVisits);
    expect(r.diagnostics.pairTests).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.pairTests);
  });
  it('reports bounded partial work instead of treating skipped traversal as free',()=>{
    const r=relaxProjectedCircles(points(),viewport);
    expect(r.diagnostics.candidateVisits).toBeGreaterThan(0);
    expect(r.diagnostics.candidateVisits).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.candidateVisits);
    expect(r.diagnostics.pairTests).toBeLessThanOrEqual(PROJECTED_RELAXATION_LIMITS.pairTests);
    expect(r.diagnostics.budgetExhausted).toBe(true);
    expect([...r.offsets].every(Number.isFinite)).toBe(true);
  });
});
