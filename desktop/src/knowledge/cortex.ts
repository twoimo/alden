import * as THREE from 'three';
import { CONTEXT_REGION_CAP, type ContextRegion } from './context-regions';
import type { Point3 } from './plasticity';

/** Envelopes follow their real members. One retained instanced mesh, finite
 * changes, no clock, inferred activity, graph IDs or picking targets. */
export class ContextNebulae extends THREE.Group {
  private readonly geometry = new THREE.PlaneGeometry(2, 2);
  private readonly opacity = new THREE.InstancedBufferAttribute(new Float32Array(CONTEXT_REGION_CAP), 1);
  private readonly colors = new THREE.InstancedBufferAttribute(new Float32Array(CONTEXT_REGION_CAP * 3), 3);
  private readonly material = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    vertexShader: `attribute float regionOpacity; attribute vec3 regionTint;
      varying vec2 vLocal;
      varying float vOpacity; varying vec3 vTint;
      void main(){
        vec4 world = instanceMatrix * vec4(0.,0.,0.,1.);
        vec4 view = modelViewMatrix * world;
        view.xy += position.xy * length(instanceMatrix[0].xyz);
        vLocal = position.xy;
        vOpacity = regionOpacity; vTint = regionTint;
        gl_Position = projectionMatrix * view;
      }`,
    fragmentShader: `varying vec2 vLocal;
      varying float vOpacity; varying vec3 vTint;
      void main(){
        float r = length(vLocal);
        float mist = exp(-4.5*r*r)*(1.-smoothstep(.65,1.,r));
        gl_FragColor = vec4(vTint, .10*mist*vOpacity);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
  private readonly mesh = new THREE.InstancedMesh(this.geometry, this.material, CONTEXT_REGION_CAP);
  private readonly current = new Float64Array(CONTEXT_REGION_CAP * 5);
  private readonly slots: Array<ContextRegion | null> = Array(CONTEXT_REGION_CAP).fill(null);
  private readonly live = new Uint8Array(CONTEXT_REGION_CAP);
  private readonly matrixScratch = new THREE.Matrix4();
  private readonly center = new THREE.Vector3();
  private readonly scaleScratch = new THREE.Vector3();
  private readonly orientation = new THREE.Quaternion();
  moving = false;

  constructor() {
    super(); this.name = 'context-nebulae';
    this.geometry.setAttribute('regionOpacity', this.opacity);
    this.geometry.setAttribute('regionTint', this.colors);
    this.mesh.frustumCulled = false; this.mesh.renderOrder = -2; this.mesh.visible = false;
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.add(this.mesh);
  }

  set(regions: readonly ContextRegion[]): void {
    this.live.fill(0);
    for (const region of regions.slice(0, CONTEXT_REGION_CAP)) {
      let slot = this.slots.findIndex(r => r?.id === region.id);
      if (slot < 0) slot = this.slots.findIndex((r, i) => r === null || !regions.some(candidate => candidate.id === r.id) && this.current[i * 5 + 4] <= .001);
      if (slot < 0) slot = this.slots.findIndex((r, i) => !this.live[i] && !regions.some(candidate => candidate.id === r?.id));
      if (slot < 0) continue;
      if (this.slots[slot]?.id !== region.id) {
        this.current.fill(0, slot * 5, slot * 5 + 5);
        const key = region.id.replace(/^source:/,'');
        let seed = 0; for (let i = 0; i < key.length; i++) seed = (Math.imul(seed,31)+key.charCodeAt(i))>>>0;
        const palette = ['#8daebc', '#b597a9', '#b7a674', '#a0a7b5'];
        const tint = new THREE.Color(palette[(seed >>> 0) % palette.length]);
        this.colors.setXYZ(slot, tint.r, tint.g, tint.b);
      }
      this.slots[slot] = region; this.live[slot] = 1;
    }
    this.colors.needsUpdate = true; this.moving = true;
  }

  private smooth(at: number, target: number, alpha: number, immediate: boolean): void {
    const delta = target - this.current[at];
    if (Math.abs(delta) < .0001 || immediate) this.current[at] = target;
    else { this.current[at] += delta * alpha; this.moving = true; }
  }

  update(dt: number, positions: ReadonlyMap<string, Point3>, immediate = false): void {
    const alpha = immediate ? 1 : 1 - Math.exp(-12 * Math.max(0, Math.min(.25, dt)));
    this.moving = false; let visible = 0, lastSlot = -1;
    for (let slot = 0; slot < CONTEXT_REGION_CAP; slot++) {
      const region = this.slots[slot]; if (!region) continue;
      const at = slot * 5; let x = 0, y = 0, z = 0, count = 0;
      if (this.live[slot]) for (const id of region.nodeIds) {
        const p = positions.get(id); if (!p || !Number.isFinite(p.x + p.y + p.z)) continue;
        x += p.x; y += p.y; z += p.z; count++;
      }
      let radius = this.current[at + 3];
      if (count) {
        x /= count; y /= count; z /= count; radius = .32 + .06 * Math.sqrt(Math.min(24,count));
        for (const id of region.nodeIds) {
          const p = positions.get(id);
          if (p && Number.isFinite(p.x + p.y + p.z)) radius = Math.max(radius, Math.hypot(p.x - x, p.y - y, p.z - z) + .2);
        }
        if (!this.current[at + 3]) { this.current[at] = x; this.current[at + 1] = y; this.current[at + 2] = z; }
      } else { x = this.current[at]; y = this.current[at + 1]; z = this.current[at + 2]; }
      this.smooth(at, x, alpha, immediate); this.smooth(at + 1, y, alpha, immediate); this.smooth(at + 2, z, alpha, immediate);
      this.smooth(at + 3, radius, alpha, immediate); this.smooth(at + 4, count ? 1 : 0, alpha, immediate);
      if (this.current[at + 4] <= .001 && !this.live[slot]) { this.slots[slot] = null; this.current[at + 4] = 0; }
      else lastSlot = slot;
      this.opacity.setX(slot, this.current[at + 4]);
      this.center.set(this.current[at], this.current[at + 1], this.current[at + 2]);
      this.scaleScratch.setScalar(this.current[at + 3]);
      this.matrixScratch.compose(this.center, this.orientation, this.scaleScratch); this.mesh.setMatrixAt(slot, this.matrixScratch);
      if (this.current[at + 4] > .001) visible++;
    }
    this.mesh.count = lastSlot + 1; this.mesh.visible = visible > 0;
    this.mesh.instanceMatrix.needsUpdate = true; this.opacity.needsUpdate = true;
  }

  diagnostics(): { active: number; retiring: number; moving: boolean; capacity: number; stateBytes: number } {
    return { active: this.live.reduce((sum, n) => sum + n, 0), retiring: this.slots.filter((r, i) => r && !this.live[i]).length,
      moving: this.moving, capacity: CONTEXT_REGION_CAP, stateBytes: this.current.byteLength + this.live.byteLength + this.opacity.array.byteLength + this.colors.array.byteLength };
  }
}
