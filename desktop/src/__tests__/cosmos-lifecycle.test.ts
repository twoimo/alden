import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { createCosmosBackdrop } from "../knowledge/cosmos";
import { KnowledgeHologram } from "../knowledge/hologram";

describe("decorative sky ownership", () => {
  it("releases the sky's retained GPU buffers exactly once with its graph", () => {
    const sky=createCosmosBackdrop();
    const disposals: ReturnType<typeof vi.spyOn>[]=[];
    sky.traverse(child=>{
      if(child instanceof THREE.Points || child instanceof THREE.Line){
        disposals.push(vi.spyOn(child.geometry,"dispose"));
        for(const material of Array.isArray(child.material)?child.material:[child.material]) disposals.push(vi.spyOn(material,"dispose"));
      }
    });
    const graphRoot=new THREE.Group();const scene=new THREE.Scene();scene.add(graphRoot,sky);
    const rendererDispose=vi.fn();const stopped=vi.fn();const started=vi.fn();
    const graph=Object.create(KnowledgeHologram.prototype) as KnowledgeHologram;
    Object.defineProperties(graph,{
      disposed:{value:false,writable:true},onDispose:{value:vi.fn()},
      canvas:{value:{removeEventListener:vi.fn(),parentElement:null}},
      loop:{value:{stop:stopped,start:started}},orbit:{value:{dispose:vi.fn()}},
      resizeObserver:{value:null},graphRoot:{value:graphRoot},scene:{value:scene},
      renderer:{value:{dispose:rendererDispose}},labels:{value:new Map()},labelBounds:{value:[]},
    });
    graph.dispose();graph.dispose();graph.start();
    expect(disposals.length).toBeGreaterThan(0);
    for(const dispose of disposals)expect(dispose).toHaveBeenCalledTimes(1);
    expect(rendererDispose).toHaveBeenCalledTimes(1);expect(stopped).toHaveBeenCalledTimes(1);expect(started).not.toHaveBeenCalled();
  });
});
