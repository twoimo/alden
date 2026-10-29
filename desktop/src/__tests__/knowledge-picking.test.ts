// @vitest-environment happy-dom
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { pickKnowledgeLabel, pickKnowledgeSphere } from "../knowledge/picking";

describe("visible knowledge label picking", () => {
  function label(left: number, top: number, width = 140, height = 24) {
    const element = document.createElement('span');
    element.getBoundingClientRect = () => new DOMRect(left, top, width, height);
    return element;
  }

  it("selects the displaced caption by its displayed client bounds", () => {
    const labels = new Map([['topic', label(488, 304)]]);
    expect(pickKnowledgeLabel(515, 315, labels)).toBe('topic');
    // The tiny sphere is elsewhere; the gap must remain an empty canvas hit.
    expect(pickKnowledgeLabel(680, 355, labels)).toBeNull();
    expect(pickKnowledgeLabel(725, 394, labels)).toBeNull();
  });

  it("ignores hidden, collapsed and out-of-bounds captions", () => {
    const hidden = label(488, 304); hidden.hidden = true;
    const labels = new Map([['hidden', hidden], ['collapsed', label(488, 304, 0)]]);
    expect(pickKnowledgeLabel(500, 315, labels)).toBeNull();
    hidden.hidden = false;
    for (const [x, y] of [[487, 315], [629, 315], [500, 303], [500, 329]]) {
      expect(pickKnowledgeLabel(x, y, labels)).toBeNull();
    }
    expect(pickKnowledgeLabel(488, 304, labels)).toBe('hidden');
  });

  it("retains label priority if displayed bounds overlap", () => {
    expect(pickKnowledgeLabel(500, 315, new Map([
      ['focused', label(488, 304)], ['other', label(488, 304)],
    ]))).toBe('focused');
  });
});

describe("knowledge sphere picking", () => {
  it("selects the same node across the equator's tiny camera perturbations", () => {
    const mesh=new THREE.Mesh(new THREE.SphereGeometry(.1195,18,12),new THREE.MeshStandardMaterial());
    mesh.position.set(1.16,0,.1803809524265535);
    for(const y of [0,3.1840816777831187e-16,-3.1840816777831187e-16,.000001]){
      const ray=new THREE.Raycaster(new THREE.Vector3(0,y,5.2),new THREE.Vector3(.22526772791684901,-5.965847904450365e-17,-.9742969007233784));
      expect(pickKnowledgeSphere(ray,[mesh])).toBe(mesh);
    }
    mesh.geometry.dispose();(mesh.material as THREE.Material).dispose();
  });
  it("honors frontmost surface, layers and distance while using current transforms", () => {
    const front=new THREE.Mesh(new THREE.SphereGeometry(.2),new THREE.MeshBasicMaterial());
    const back=front.clone();back.position.z=-1;
    const ray=new THREE.Raycaster(new THREE.Vector3(0,0,5),new THREE.Vector3(0,0,-1));
    expect(pickKnowledgeSphere(ray,[back,front])).toBe(front);
    front.layers.set(1);expect(pickKnowledgeSphere(ray,[front,back])).toBe(back);
    back.position.x=2;expect(pickKnowledgeSphere(ray,[front,back])).toBeNull();
    back.position.x=0;ray.far=4;expect(pickKnowledgeSphere(ray,[back])).toBeNull();
    front.geometry.dispose();(front.material as THREE.Material).dispose();
  });
});
