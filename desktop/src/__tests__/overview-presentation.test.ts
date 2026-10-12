import { describe, expect, it } from 'vitest';
import { overviewDisplayPoint, overviewGlyphScale } from '../knowledge/overview-presentation';
import { ConstellationNodes } from '../knowledge/constellation';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { overviewCameraDistance } from '../knowledge/camera-fit';
import { projectedLayoutMetrics } from '../knowledge/projected-layout-metrics';

describe('dense overview display projection', () => {
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
