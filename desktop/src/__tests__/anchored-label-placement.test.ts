import {describe,it,expect} from 'vitest';
import {placeOverviewLabel,type LabelBox} from '../knowledge/label-placement';

describe('node-adjacent overview labels',()=>{
  const viewport={x:0,y:0,width:900,height:600};
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
