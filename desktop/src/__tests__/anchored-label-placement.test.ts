import {describe,it,expect} from 'vitest';
import {placeOverviewLabel,type LabelBox} from '../knowledge/label-placement';
import {outwardLabelPoint,ringLabelPoint,spreadRingCaptions} from '../knowledge/label-placement';

describe('node-adjacent overview labels',()=>{
  const viewport={x:0,y:0,width:900,height:600};
  it('moves an off-center label farther from the viewport center and leaves a central label',()=>{
    const outer=outwardLabelPoint(700,120,viewport,46);
    const center=outwardLabelPoint(450,300,viewport,46);
    expect(Math.hypot(outer.x-450,outer.y-300)).toBeGreaterThan(Math.hypot(700-450,120-300));
    expect(center).toEqual({x:450,y:300});
    const invalid=outwardLabelPoint(Number.NaN,10,viewport,46);
    expect(Number.isNaN(invalid.x)).toBe(true);
    expect(invalid.y).toBe(10);
  });
  it('moves an in-core topic label out to the ring and leaves an outer label',()=>{
    const inner=ringLabelPoint(500,340,viewport,.78);
    const center=ringLabelPoint(450,300,viewport,.78);
    const outer=ringLabelPoint(860,40,viewport,.78);
    const ring=Math.min(viewport.width,viewport.height)*.78/2;
    expect(Math.hypot(inner.x-450,inner.y-300)).toBeCloseTo(ring,5);
    expect(center).toEqual({x:450,y:300});
    expect(outer).toEqual({x:860,y:40});
    const invalid=ringLabelPoint(Number.NaN,12,viewport,.78);
    expect(Number.isNaN(invalid.x)).toBe(true);
    expect(invalid.y).toBe(12);
  });
  it('separates topic captions that share a direction and keeps a distant caption near its ray', () => {
    const viewport = {x:0,y:0,width:964,height:586};
    const spread = spreadRingCaptions([
      {id:'near-a',x:700,y:280},{id:'near-b',x:720,y:290},{id:'far',x:200,y:80},
    ], viewport);
    const a = spread.get('near-a')!, b = spread.get('near-b')!, far = spread.get('far')!;
    expect(Math.hypot(a.x-b.x, a.y-b.y)).toBeGreaterThan(80);
    const center = {x:482,y:293};
    const angle = (point:{x:number,y:number}) => Math.atan2(point.y-center.y, point.x-center.x);
    const raw = ringLabelPoint(200,80,viewport,.78);
    const difference = Math.atan2(Math.sin(angle(far)-angle(raw)), Math.cos(angle(far)-angle(raw)));
    expect(Math.abs(difference)).toBeLessThan(.5);
  });
  it('culls a crowded label instead of displacing it far from its source node',()=>{
    const earlier:LabelBox[]=[];
    for(let i=0;i<12;i++) {
      const box={left:350,top:250,width:160,height:24,visible:false};
      placeOverviewLabel(box,viewport,[],earlier,earlier.length,'anchored');
      if(box.visible) {
        expect(Math.abs(box.left-350)).toBeLessThanOrEqual(18);
        expect(Math.abs(box.top-250)).toBeLessThanOrEqual(36);
      }
      earlier.push(box);
    }
    expect(earlier.filter(box=>box.visible).length).toBeLessThanOrEqual(3);
    expect(earlier.some(box=>!box.visible)).toBe(true);
  });
  it('respects overlays and measured taller type without escaping the anchor',()=>{
    const box={left:350,top:250,width:160,height:40,visible:true};
    placeOverviewLabel(box,viewport,[{left:300,top:240,right:550,bottom:310}],[],0,'anchored');
    expect(box.visible).toBe(false);
  });
  it('keeps a label near an edge contained and never intersects prior boxes',()=>{
    const prior={left:0,top:25,width:160,height:24,visible:true};
    const box={left:0,top:55,width:160,height:24,visible:false};
    placeOverviewLabel(box,viewport,[],[prior],1,'anchored');
    expect(box.visible).toBe(true);
    expect(box.left).toBeGreaterThanOrEqual(0);
    expect(box.top).toBeGreaterThanOrEqual(prior.top+prior.height+7);
  });
});
