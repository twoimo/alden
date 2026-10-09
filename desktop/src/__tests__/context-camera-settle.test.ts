// @vitest-environment happy-dom
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { describe, expect, it, vi } from 'vitest';
import { AnimationLoop, type FrameScheduler } from '../core/animation-loop';
import { ContextNebulae } from '../knowledge/cortex';
import { overviewGraph, parseKnowledgeGraph } from '../knowledge/graph-model';
import { KnowledgeHologram } from '../knowledge/hologram';
import { PlasticityLayout } from '../knowledge/plasticity';
import { relationAnchors } from '../knowledge/relation-layout';
import { SynapticBridges } from '../knowledge/synapses';
import { VoiceEnvelope } from '../knowledge/voice-envelope';
import { ConstellationNodes } from '../knowledge/constellation';

class Scheduler implements FrameScheduler {
  private nowMs = 0;
  private nextId = 0;
  readonly callbacks = new Map<number, FrameRequestCallback>();
  now(): number { return this.nowMs; }
  request(callback: FrameRequestCallback): number {
    const id = ++this.nextId;
    this.callbacks.set(id, callback);
    return id;
  }
  cancel(id: number): void { this.callbacks.delete(id); }
  step(): void {
    this.nowMs += 1000 / 30;
    const callbacks = [...this.callbacks.values()];
    this.callbacks.clear();
    for (const callback of callbacks) callback(this.nowMs);
  }
}

describe('context overview camera lifecycle', () => {
  it('reaches a fitted distance above 12 and stops requesting frames', () => {
    const graph = parseKnowledgeGraph({
      nodes: Array.from({ length: 24 }, (_, i) => ({
        id: 'n' + i, label: 'Node ' + i, space: '00_Scope/A',
      })),
      edges: [],
    });
    const view = overviewGraph(graph);
    const positions = new Map([...relationAnchors(view, graph)].map(([id, p]) =>
      [id, new THREE.Vector3(p.x, p.y, p.z)]));
    const camera = new THREE.PerspectiveCamera(38, 200 / 470, .1, 60);
    camera.position.set(0, 0, 3.6);
    const orbit = new OrbitControls(camera, document.createElement('canvas'));
    orbit.minDistance = 2.2;
    orbit.maxDistance = 12;
    orbit.enableDamping = false;
    const nebulae = new ContextNebulae();
    nebulae.set([]);
    nebulae.update(0, positions, true);
    const plasticity = new PlasticityLayout();
    plasticity.setGraph(view, positions, positions);
    plasticity.settle();
    const synapses = new SynapticBridges();
    const overviewSynapses=new SynapticBridges(512,false),constellationNodes=new ConstellationNodes();
    constellationNodes.set(view.nodes,view.edges);constellationNodes.update(positions);
    const voiceEnvelope = new VoiceEnvelope();
    const scene = new THREE.Scene();
    scene.add(nebulae, synapses.mesh,overviewSynapses.mesh,constellationNodes, voiceEnvelope.line);
    const scheduler = new Scheduler();
    const hologram = Object.create(KnowledgeHologram.prototype) as KnowledgeHologram;
    // Exercise the real fit/render methods without constructing a WebGL renderer
    // or starting a voice loader. Keep the real camera, controls, geometry and loop.
    const methods = hologram as unknown as {
      resetOverviewCamera(): void;
      render(dt: number): void;
    };
    const loop = new AnimationLoop(dt => methods.render(dt), scheduler);
    const renderer = { render: vi.fn() };
    Object.assign(hologram, {
      disposed: false, view, graph, anchors: positions, positions,
      displayedPositions: positions, viewport: { x: 0, y: 0, width: 200, height: 470 },
      camera, orbit, projected: new THREE.Vector3(), desiredCamera: new THREE.Vector3(), desiredLookAt: new THREE.Vector3(),
      lookAt: new THREE.Vector3(), motion: { matches: false }, paused: false,
      plasticity, synapses,overviewSynapses,constellationNodes,globalLabels:new Set(),neuronDetail:false, nebulae, nodeMeshes: new Map(), orbMeshes: new Map(),
      loop, voiceEnvelope, latestVoice: null, labels: new Map(), renderer, scene,
      orbitActive: false,
      syncPoint: (id: string, x: number, y: number, z: number) => positions.get(id)?.set(x, y, z),
    });

    try {
      methods.resetOverviewCamera();
      const target = new THREE.Vector3().fromArray(hologram.navigationTargets.camera);
      loop.start();
      for (let frame = 0; frame < 300 && loop.isRunning(); frame++) scheduler.step();

      expect(target.z).toBeGreaterThan(12);
      expect(orbit.maxDistance).toBeGreaterThanOrEqual(target.length());
      expect(camera.position.distanceTo(target)).toBeLessThan(.001);
      expect(nebulae.moving).toBe(false);
      expect(loop.isRunning()).toBe(false);
      expect(scheduler.callbacks.size).toBe(0);
      expect(loop.renderCount).toBeGreaterThan(1);
      expect(loop.renderCount).toBeLessThan(300);
      expect(renderer.render).toHaveBeenCalledTimes(loop.renderCount);
      const settledFrames = loop.renderCount;
      for (let frame = 0; frame < 30; frame++) scheduler.step();
      expect(loop.renderCount).toBe(settledFrames);

      // Check the actual sphere vertices in camera space, including their depth.
      scene.updateMatrixWorld(true);
      camera.updateMatrixWorld(true);
      const mesh = constellationNodes.children[0] as THREE.InstancedMesh;
      expect(mesh.count).toBe(24);
      expect(mesh.visible).toBe(true);
      const vertices = mesh.geometry.getAttribute('position');
      const instance = new THREE.Matrix4();
      const projected = new THREE.Vector3();
      for (let slot = 0; slot < mesh.count; slot++) {
        mesh.getMatrixAt(slot, instance);
        for (let vertex = 0; vertex < vertices.count; vertex++) {
          projected.fromBufferAttribute(vertices, vertex).applyMatrix4(instance)
            .applyMatrix4(mesh.matrixWorld).project(camera);
          expect(Math.max(Math.abs(projected.x), Math.abs(projected.y), Math.abs(projected.z)))
            .toBeLessThanOrEqual(1);
        }
      }
    } finally {
      loop.stop();
      orbit.dispose();
      synapses.dispose();
      overviewSynapses.dispose();
      voiceEnvelope.line.geometry.dispose();
      voiceEnvelope.line.material.dispose();
      const mesh = nebulae.children[0] as THREE.InstancedMesh;
      mesh.dispose();
      mesh.geometry.dispose();
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) material.dispose();
      const stars=constellationNodes.children[0] as THREE.InstancedMesh;stars.dispose();stars.geometry.dispose();(stars.material as THREE.Material).dispose();
    }
  });
});
