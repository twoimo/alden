import {describe,expect,it} from 'vitest';
import fixture from './fixtures/physics-tzuyang-boundary-20261013.json';
import {PlasticityLayout} from '../knowledge/plasticity';

// Restore the anonymous-index captured integration checkpoint in the test
// only. Production exposes no import path, source identifiers or body data.
function captured() {
  const model=new PlasticityLayout(fixture.nodeCap,fixture.synapseCap),state=model as any;
  state.ids=Array.from({length:fixture.nodeCount},(_,i)=>'index-'+i);state.edgeCount=fixture.edgeCount;
  for(const key of ['coordinates','velocity','anchors','inverseMass','degree','components','edgeA','edgeB','edgeStrength','restLength'] as const)state[key].set(fixture[key]);
  for(const key of ['moving','quietSteps','visualRestSpeed','visualRestForce','simulatedSeconds'] as const)state[key]=fixture[key];
  return model;
}
describe('captured ellipsoid contact rest',()=>{
  it('settles actual contact drift within fifteen seconds using the original strict screen tolerance',()=>{
    const model=captured();let frames=0;
    while(model.moving&&frames<450){model.advance(1/30);frames++;}
    expect(model.moving).toBe(false);expect(frames).toBeLessThan(450);
    expect(model.diagnostics().maxPositionSpeed).toBeLessThan(fixture.visualRestSpeed);
    expect(model.auditState().visualRestSpeed).toBe(fixture.visualRestSpeed);
    expect(model.auditState().visualRestForce).toBe(fixture.visualRestForce);
    for(let i=0;i<fixture.nodeCount;i++){
      const at=i*3,p=model.coordinates;
      expect(Math.hypot(p[at]/2.3,p[at+1]/1.55,p[at+2]/1.1)).toBeLessThanOrEqual(1+1e-10);
      expect(Math.hypot(...[0,1,2].map(axis=>p[at+axis]-fixture.coordinates[at+axis]))).toBeLessThan(.01);
    }
    const before=Array.from(model.coordinates);expect(model.advance(2)).toBe(false);expect(Array.from(model.coordinates)).toEqual(before);
  });

  it('keeps the audit checkpoint immutable and excludes source identities or content',()=>{
    const model=captured(),snapshot=model.auditState(),before=Array.from(model.coordinates);
    snapshot.coordinates.fill(99);snapshot.velocity.fill(99);snapshot.anchors.fill(99);
    expect(Array.from(model.coordinates)).toEqual(before);
    for(const key of ['ids','nodes','labels','source','body','document_id'])expect(Object.hasOwn(snapshot,key)).toBe(false);
  });
});
