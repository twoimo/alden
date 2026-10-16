import { describe, expect, it } from 'vitest';
import { overviewDisplayPoint, overviewGlyphScale, OVERVIEW_DISPLAY_GAIN } from '../knowledge/overview-presentation';
import { ConstellationNodes, maximumOverviewStarRadius } from '../knowledge/constellation';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { overviewCameraDistance } from '../knowledge/camera-fit';
import { projectedLayoutMetrics } from '../knowledge/projected-layout-metrics';

describe('dense overview display projection', () => {
  it('opens the inner quarter of a dense cloud without replacing its source coordinates', () => {
    const source={x:2.3*.25,y:0,z:0},saved={...source};
    const point=overviewDisplayPoint(source,964,586,true,{x:0,y:0,z:0});
    expect(Math.hypot(point.x/2.3,point.y/1.55,point.z/1.1)).toBeGreaterThanOrEqual(.5);
    expect(source).toEqual(saved);
  });

  it('keeps finite differences below the motion bound across clamp seams and mixed axes', () => {
    const output=(p:{x:number;y:number;z:number},w:number)=>overviewDisplayPoint(p,w,586,true,{x:0,y:0,z:0});
    for(const width of [439,964])for(const r of [0,.099999,.1,.100001,.5,.999999,1,1.000001]){
      const point={x:2.3*r/Math.SQRT2,y:0,z:1.1*r/Math.SQRT2};
      const axes=[{x:1e-7,y:0,z:0},{x:0,y:1e-7,z:0},{x:0,y:0,z:1e-7},{x:1e-7,y:0,z:-1e-7}];
      for(const delta of axes){
        const a=output(point,width),b=output({x:point.x+delta.x,y:point.y+delta.y,z:point.z+delta.z},width);
        expect([a.x,a.y,a.z,b.x,b.y,b.z].every(Number.isFinite)).toBe(true);
        expect(Math.hypot(b.x-a.x,b.y-a.y,b.z-a.z)).toBeLessThanOrEqual(OVERVIEW_DISPLAY_GAIN*Math.hypot(delta.x,delta.y,delta.z)+1e-12);
      }
    }
    // Estimate the largest singular value of the actual mixed-axis Jacobian,
    // independently of the map's exponent/formula. This guards the rest bound.
    const point={x:2.3*.1001/Math.SQRT2,y:0,z:1.1*.1001/Math.SQRT2},step=1e-7;
    const base=output(point,964),columns=['x','y','z'].map(key=>{
      const p={...point};p[key as keyof typeof p]+=step;const next=output(p,964);
      return [(next.x-base.x)/step,(next.y-base.y)/step,(next.z-base.z)/step];
    });
    let v=[1,1,1];
    const multiply=(value:number[])=>[0,1,2].map(row=>columns.reduce((sum,col,index)=>sum+col[row]*value[index],0));
    for(let i=0;i<24;i++){
      const jv=multiply(v),next=columns.map(col=>col.reduce((sum,x,row)=>sum+x*jv[row],0));
      const norm=Math.hypot(...next);v=next.map(x=>x/norm);
    }
    expect(Math.hypot(...multiply(v))).toBeLessThanOrEqual(OVERVIEW_DISPLAY_GAIN+1e-5);
  });

  it('bounds dense glyphs in CSS space and gives the picker the exact GPU radius', () => {
    const {nodes,edges}=parseKnowledgeGraph({nodes:[{id:'hub',label:'synthetic',degree:900,degree_scope:'permitted filtered graph'}],edges:[]});
    const stars=new ConstellationNodes(2048);stars.set(nodes,edges);
    const point=new Map([['hub',{x:.1,y:.2,z:0}]]);
    const radiusBefore=stars.radius('hub'),cap=maximumOverviewStarRadius(4.7,38,510);
    stars.setMinimumRadius(.003,1,cap);stars.update(point);
    const radius=stars.radius('hub');
    expect(radius).toBeLessThan(radiusBefore);expect(radius).toBeLessThanOrEqual(cap);
    expect(radius*510/(2*4.7*Math.tan(38*Math.PI/360))).toBeCloseTo(2.4);
    const batch=stars.children[0] as import('three').InstancedMesh;
    expect(batch.instanceMatrix.array[0]).toBeCloseTo(radius,7);
    expect(batch.instanceMatrix.array[5]).toBeCloseTo(radius,7);
    stars.setMinimumRadius(.003);stars.update(point);
    expect(stars.radius('hub')).toBe(radiusBefore);
    batch.dispose();batch.geometry.dispose();(batch.material as import('three').Material).dispose();
  });

  it('retains a visible floor and rejects invalid ceiling inputs', () => {
    expect(maximumOverviewStarRadius(0,38,510)).toBe(Infinity);
    expect(maximumOverviewStarRadius(4.7,NaN,510)).toBe(Infinity);
    expect(maximumOverviewStarRadius(4.7,38,0)).toBe(Infinity);
    const {nodes,edges}=parseKnowledgeGraph({nodes:[{id:'dot',label:'synthetic'}],edges:[]});
    const stars=new ConstellationNodes(2048);stars.set(nodes,edges);
    stars.setMinimumRadius(.01,1,.001);
    expect(stars.radius('dot')).toBe(.01);
    const batch=stars.children[0] as import('three').InstancedMesh;
    batch.dispose();batch.geometry.dispose();(batch.material as import('three').Material).dispose();
  });

  it('uses matching glyph radii for the GPU and picker, retaining a visible floor after compression', () => {
    const {nodes,edges}=parseKnowledgeGraph({nodes:[{id:'synthetic-dot',label:'synthetic'}],edges:[]});
    const stars=new ConstellationNodes(2048);stars.set(nodes,edges);
    const position=new Map([['synthetic-dot',{x:.2,y:0,z:0}]]);
    const before=stars.radius('synthetic-dot'),scale=overviewGlyphScale(439,510,true);
    stars.setMinimumRadius(.004,scale);stars.update(position);
    const radius=stars.radius('synthetic-dot');
    expect(radius).toBeLessThan(before);expect(radius).toBeGreaterThanOrEqual(.004);
    const batch=stars.children[0] as import('three').InstancedMesh;
    expect(batch.instanceMatrix.array[0]).toBeCloseTo(radius,7);
    expect(batch.instanceMatrix.array[5]).toBeCloseTo(radius,7);
    stars.setMinimumRadius(.004,1);stars.update(position);
    expect(stars.radius('synthetic-dot')).toBe(before);
    batch.dispose();batch.geometry.dispose();
    (batch.material as import('three').Material).dispose();
  });
  it('preserves sparse/local coordinates, source values and caller-owned output', () => {
    const input={x:.2,y:-.3,z:.1}, saved={...input}, output={x:0,y:0,z:0};
    expect(overviewDisplayPoint(input,440,510,false,output)).toBe(output);
    expect(output).toEqual(input);
    overviewDisplayPoint(input,440,510,true,output);
    expect(input).toEqual(saved);
    expect(overviewDisplayPoint({x:0,y:0,z:0},440,510,true,output)).toEqual({x:0,y:0,z:0});
  });

  it('keeps a synthetic central cloud readable in a portrait viewport without dropping its outliers', () => {
    const points=Array.from({length:256},(_,i)=>{
      const angle=i*2.399963229728653,radius=.24+.45*(i%16)/15;
      return {x:Math.cos(angle)*radius,y:Math.sin(angle)*radius,z:0};
    });
    points.push({x:2.1,y:0,z:0},{x:-2.1,y:0,z:0},{x:0,y:1.4,z:0},{x:0,y:-1.4,z:0});
    function project(dense:boolean){
      const transformed=points.map(p=>overviewDisplayPoint(p,439,510,dense,{x:0,y:0,z:0}));
      const distance=overviewCameraDistance(transformed,439,510,38,.08),fy=510/(2*Math.tan(38*Math.PI/360));
      return projectedLayoutMetrics(transformed.map(p=>({x:219.5+p.x*fy/distance,y:255-p.y*fy/distance,radius:.45})),
        {left:0,top:0,width:439,height:510});
    }
    const baseline=project(false), candidate=project(true);
    expect(baseline.available&&candidate.available).toBe(true);
    if(!baseline.available||!candidate.available)return;
    expect(candidate.visiblePoints).toBe(points.length);
    expect(candidate.centralQuarterFraction!).toBeLessThan(baseline.centralQuarterFraction!);
    expect(candidate.nearestMedianCssPx!).toBeGreaterThan(baseline.nearestMedianCssPx!);
    expect(candidate.occupiedCells).toBeGreaterThan(baseline.occupiedCells);
  });

  it('keeps radial order and the bounded ellipsoid while applying the same mapping to every identity', () => {
    let previous=0;
    for(let i=0;i<=100;i++){
      const source={x:2.3*i/100,y:0,z:0},target=overviewDisplayPoint(source,964,586,true,{x:0,y:0,z:0});
      expect(target.x).toBeGreaterThanOrEqual(previous);
      expect(Math.hypot(target.x/2.3,target.y/1.55,target.z/1.1)).toBeLessThanOrEqual(1+1e-9);
      previous=target.x;
    }
  });
});
