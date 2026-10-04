import {describe,expect,it} from 'vitest';
import {parseKnowledgeGraph} from '../knowledge/graph-model';
import {SynapticBridges} from '../knowledge/synapses';
import * as THREE from 'three';
import {createNeuronGlyph} from '../knowledge/neuron';

describe('source synaptic bridges',()=>{
  it('grows and retracts inside one retained geometry without inventing relationships',()=>{
    const bridges=new SynapticBridges(),geometry=bridges.mesh.geometry;
    const points=new Map([['a',{x:0,y:0,z:0}],['b',{x:1,y:0,z:0}]]);
    bridges.set([{key:'a-b',source:'a',target:'b',strength:.8}],null,false);
    bridges.update(.1,points,false);
    expect(geometry.getAttribute('aGrowth').getX(0)).toBeGreaterThan(0);
    expect(geometry.getAttribute('aGrowth').getX(0)).toBeLessThan(1);
    for(let i=0;i<20;i++)bridges.update(.1,points,false);
    expect(bridges.diagnostics().active).toBe(1);expect(bridges.moving).toBe(false);
    bridges.set([],null,false);expect(bridges.diagnostics().retiring).toBe(1);
    for(let i=0;i<20;i++)bridges.update(.1,points,false);
    expect(bridges.mesh.geometry).toBe(geometry);expect(bridges.mesh.visible).toBe(false);
    expect(bridges.diagnostics().retiring).toBe(0);bridges.dispose();
  });
  it('settles real links and has no perpetual flow without a new source change',()=>{
    const bridges=new SynapticBridges();
    const edge={key:'a-b',source:'a',target:'b',strength:.8};
    bridges.set([edge],'a',true);bridges.update(0,new Map(),true);
    expect(bridges.mesh.geometry.getAttribute('aGrowth').getX(0)).toBe(1);
    expect(bridges.update(3600,new Map(),true)).toBe(false);
    expect(edge.strength).toBe(.8);expect(bridges.moving).toBe(false);bridges.dispose();
  });
  it('keeps source-navigation quieter than semantic bridges',()=>{
    const bridges=new SynapticBridges();
    bridges.set([{key:'a-b',source:'a',target:'b',strength:.8,purpose:'semantic'},
      {key:'b-c',source:'b',target:'c',strength:.08,purpose:'navigation'}],null,true);
    bridges.update(0,new Map(),true);
    expect(bridges.mesh.geometry.getAttribute('aOpacity').getX(1)).toBeLessThan(bridges.mesh.geometry.getAttribute('aOpacity').getX(0));
    bridges.dispose();
  });
  it('projects only current evidence and keeps the original payload intact',()=>{
    const payload={nodes:[{id:'a',label:'A'},{id:'b',label:'B'},{id:'retired',label:'Retired',evidence:{retracted:true}}],
      edges:[{source:'a',target:'b',valid_to:'2026-01-01T00:00:00Z'},{source:'a',target:'b',evidence:{retracted:true}},
        {source:'a',target:'retired'},{source:'a',target:'b',weight:2}]};
    const before=JSON.stringify(payload),graph=parseKnowledgeGraph(payload,Date.parse('2026-10-03T00:00:00Z'));
    expect(graph.nodes).toHaveLength(2);expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0].weight).toBe(2);expect(JSON.stringify(payload)).toBe(before);
  });
  it('grows a single synapse from the exact rendered dendrite tips and keeps its attachment while cells move',()=>{
    const a=createNeuronGlyph(.05,new THREE.Color('#dde7ef'),'a'),b=createNeuronGlyph(.05,new THREE.Color('#dde7ef'),'b');
    const bridges=new SynapticBridges(),points=new Map([['a',{x:0,y:0,z:0}],['b',{x:1,y:0,z:0}]]),ports=new Map([['a',a.ports],['b',b.ports]]);
    const edge={key:'a-b',source:'a',target:'b',strength:.8,purpose:'semantic' as const};
    try {
      bridges.set([edge],null,false,ports,points);bridges.update(.1,points,false);
      const start=bridges.mesh.geometry.getAttribute('aStart'),end=bridges.mesh.geometry.getAttribute('aEnd');
      const pa=a.ports.find(p=>Math.hypot(start.getX(0)-p.x,start.getY(0)-p.y,start.getZ(0)-p.z)<1e-6)!;
      const pb=b.ports.find(p=>Math.hypot(end.getX(0)-1-p.x,end.getY(0)-p.y,end.getZ(0)-p.z)<1e-6)!;
      expect(pa).toBeDefined();expect(pb).toBeDefined();expect(Math.hypot(start.getX(0),start.getY(0),start.getZ(0))).toBeGreaterThan(.05);
      expect(bridges.diagnostics()).toMatchObject({forming:1,formed:0,dockedEndpoints:2});
      points.get('a')!.x=.2;points.get('b')!.y=.2;bridges.update(.1,points,false);
      expect(start.getX(0)).toBeCloseTo(.2+pa.x,6);expect(start.getY(0)).toBeCloseTo(pa.y,6);expect(end.getY(0)).toBeCloseTo(.2+pb.y,6);
      for(let i=0;i<30;i++)bridges.update(.1,points,false);
      expect(bridges.diagnostics()).toMatchObject({forming:0,formed:1,moving:false});
      const retained=bridges.mesh.geometry;bridges.set([],null,false,ports,points);bridges.update(.1,points,false);
      expect(bridges.diagnostics()).toMatchObject({active:0,retiring:1});
      points.get('a')!.x+=.25;points.get('b')!.y+=.3;bridges.update(.1,points,false);
      expect(start.getX(0)).toBeCloseTo(.45+pa.x,6);expect(end.getY(0)).toBeCloseTo(.5+pb.y,6);
      for(let i=0;i<30;i++)bridges.update(.1,points,false);
      expect(bridges.mesh.geometry).toBe(retained);expect(bridges.mesh.visible).toBe(false);
    } finally {
      bridges.dispose();for(const group of [a,b])group.traverse(child=>{if(child instanceof THREE.Mesh){child.geometry.dispose();for(const m of Array.isArray(child.material)?child.material:[child.material])m.dispose();}});
    }
  });
  it('retains the chosen physical branch across focus rebuilds and keeps navigation outside synaptic contacts',()=>{
    const bridges=new SynapticBridges(),points=new Map([['a',{x:0,y:0,z:0}],['b',{x:1,y:0,z:0}]]);
    const ports=new Map([['a',[{x:.2,y:0,z:0},{x:0,y:.2,z:0}]],['b',[{x:-.2,y:0,z:0}]]]);
    const edge={key:'a-b',source:'a',target:'b',strength:.8,purpose:'semantic' as const};
    bridges.set([edge],null,true,ports,points);bridges.update(0,points,true);
    points.get('b')!.x=0;points.get('b')!.y=1;
    bridges.set([edge],'a',true,ports,points);bridges.update(0,points,true);
    expect(bridges.mesh.geometry.getAttribute('aStart').getX(0)).toBeCloseTo(.2);
    bridges.set([{...edge,purpose:'navigation'}],null,true,ports,points);bridges.update(0,points,true);
    expect(bridges.mesh.geometry.getAttribute('aStart').getX(0)).toBe(0);
    expect(bridges.diagnostics().dockedEndpoints).toBe(0);bridges.dispose();
  });
});
