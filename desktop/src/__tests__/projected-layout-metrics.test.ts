import {describe,it,expect} from 'vitest';
import {projectedLayoutMetrics as measure} from '../knowledge/projected-layout-metrics';
const viewport={left:0,top:0,width:100,height:100};
describe('on-demand projected core geometry',()=>{
  it('counts exact overlap participation, coincidence, tangency and nearest distances',()=>{
    const result=measure([{x:10,y:10,radius:1},{x:12,y:10,radius:1},{x:10,y:10,radius:1}],viewport);
    expect(result).toMatchObject({available:true,visiblePoints:3,overlapPairs:1,overlappingPoints:2,pairTests:3,
      nearestMedianCssPx:0,nearestP95CssPx:2});
  });
  it('reports invalids and clip-volume/viewport exclusions without changing input',()=>{
    const input=[{x:50,y:50,radius:1},{x:101,y:30,radius:1},{x:10,y:20,radius:1,inClipVolume:false},
      {x:NaN,y:20,radius:1},{x:30,y:20,radius:-1}];
    const original=input.map(p=>({...p}));
    expect(measure(input,viewport)).toMatchObject({invalidPoints:2,validPoints:3,visiblePoints:1,clippedCenters:2,
      centralQuarterFraction:1,nearestMedianCssPx:null,nearestP95CssPx:null});
    expect(input).toEqual(original);
  });
  it('keeps empty denominators null and marks invalid viewports unavailable',()=>{
    expect(measure([],viewport)).toMatchObject({visiblePoints:0,pairTests:0,nearestMedianCssPx:null,centralQuarterFraction:null,boundingCentersAreaFraction:null});
    expect(measure([],{...viewport,width:0})).toMatchObject({available:false,reason:'invalid_viewport'});
  });
  it('preserves fractions under translation and scale while scaling distances',()=>{
    const points=[{x:0,y:0,radius:1},{x:50,y:50,radius:1},{x:100,y:100,radius:1}];
    const a=measure(points,viewport),b=measure(points.map(p=>({x:p.x*2+7,y:p.y*2-9,radius:p.radius*2})),
      {left:7,top:-9,width:200,height:200});
    if(!a.available||!b.available)throw Error('valid viewport unexpectedly unavailable');
    expect(a).toMatchObject({centralQuarterFraction:1/3,boundingCentersAreaFraction:1,occupiedCells:3});
    expect(b).toMatchObject({centralQuarterFraction:a.centralQuarterFraction,boundingCentersAreaFraction:a.boundingCentersAreaFraction,
      overlapPairs:a.overlapPairs,occupiedCells:a.occupiedCells});
    expect(b.nearestMedianCssPx).toBe((a.nearestMedianCssPx??0)*2);
  });
  it('caps exact work honestly at 2048 points',()=>{
    const result=measure(Array.from({length:2049},()=>({x:50,y:50,radius:1})),viewport);
    expect(result).toMatchObject({totalPoints:2049,sampledPoints:2048,truncated:true,visiblePoints:2048,
      pairTests:2096128,overlapPairs:2096128,overlappingPoints:2048});
  });
});
