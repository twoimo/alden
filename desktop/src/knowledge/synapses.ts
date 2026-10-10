import * as THREE from 'three';
import { SYNAPSE_CAP, type Point3, type Synapse } from './plasticity';

interface Bridge extends Synapse { goal: number; growth: number; active: boolean; color: THREE.Color; ax: number; ay: number; az: number; bx: number; by: number; bz: number;
  portA: number; portB: number; dockAx: number; dockAy: number; dockAz: number; dockBx: number; dockBy: number; dockBz: number }

/** Choose a real tip once, not a free-floating point or a per-frame switch. */
function facingPort(ports: readonly Point3[] | undefined, a: Point3 | undefined, b: Point3 | undefined): number {
  if (!ports?.length || !a || !b) return -1;
  const dx=b.x-a.x,dy=b.y-a.y,dz=b.z-a.z;
  let selected=-1, best=-Infinity;
  for(let i=0;i<ports.length;i++) {
    const p=ports[i], length=Math.hypot(p.x,p.y,p.z);
    if(!Number.isFinite(length) || length<=0) continue;
    const alignment=(p.x*dx+p.y*dy+p.z*dz)/length;
    if(Number.isFinite(alignment) && alignment>best) {best=alignment;selected=i;}
  }
  return selected;
}

/** One persistent GPU ribbon batch; withdrawn bridges retract without becoming pick targets. */
export class SynapticBridges {
  readonly mesh: THREE.Mesh;
  private readonly geometry = new THREE.InstancedBufferGeometry();
  private readonly starts: Float32Array;
  private readonly ends: Float32Array;
  private readonly strengths: Float32Array;
  private readonly growths: Float32Array;
  private readonly opacities: Float32Array;
  private readonly semantic: Float32Array;
  private readonly colors: Float32Array;
  private readonly attributes: THREE.InstancedBufferAttribute[];
  private readonly records: Bridge[] = [];
  private readonly index = new Map<string, Bridge>();
  private readonly neutral = new THREE.Color('#90a9bc');
  private readonly selected = new THREE.Color('#d5bf81');
  moving = false;
  private dirty = true;
  private disposed = false;

  private readonly activeCap:number;
  private readonly capacity:number;
  private focus: string | null = null;
  constructor(cap=SYNAPSE_CAP, private readonly detail=true) {
    this.activeCap=Number.isFinite(cap)?Math.min(4096,Math.max(1,Math.floor(cap))):SYNAPSE_CAP;
    this.capacity=this.activeCap*2;
    this.starts=new Float32Array(this.capacity * 3);
    this.ends=new Float32Array(this.capacity * 3);
    this.strengths=new Float32Array(this.capacity);
    this.growths=new Float32Array(this.capacity);
    this.opacities=new Float32Array(this.capacity);
    this.semantic=new Float32Array(this.capacity);
    this.colors=new Float32Array(this.capacity * 3);

    const positions: number[] = [], indices: number[] = [];
    for(const branch of detail?[0,-1,1,-2,2]:[0]) {
      const offset=positions.length/3, steps=branch===0?(detail?32:1):8;
      for(let i=0;i<=steps;i++) positions.push(-1,i/steps,branch,1,i/steps,branch);
      for(let i=0;i<steps;i++){const at=offset+i*2;indices.push(at,at+1,at+2,at+1,at+3,at+2);}
    }
    this.geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    this.geometry.setIndex(indices);
    this.attributes = [new THREE.InstancedBufferAttribute(this.starts, 3), new THREE.InstancedBufferAttribute(this.ends, 3),
      new THREE.InstancedBufferAttribute(this.strengths, 1), new THREE.InstancedBufferAttribute(this.growths, 1),
      new THREE.InstancedBufferAttribute(this.opacities, 1), new THREE.InstancedBufferAttribute(this.colors, 3),new THREE.InstancedBufferAttribute(this.semantic,1)];
    ['aStart', 'aEnd', 'aStrength', 'aGrowth', 'aOpacity', 'aColor','aSemantic'].forEach((name, i) => {
      this.attributes[i].setUsage(THREE.DynamicDrawUsage); this.geometry.setAttribute(name, this.attributes[i]);
    });
    this.geometry.instanceCount = 0;
    this.mesh = new THREE.Mesh(this.geometry, new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, side: THREE.DoubleSide, forceSinglePass:true,
      uniforms:{uDetail:{value:detail?1:0}},
      vertexShader: `
        uniform float uDetail;
        attribute vec3 aStart, aEnd, aColor;
        attribute float aStrength, aGrowth, aOpacity, aSemantic;
        varying float vAcross, vAlong, vGrowth, vOpacity, vSemantic, vBranch, vGap;
        varying vec3 vColor;
        void main() {
          float branch=position.z;
          float localT=position.y;
          float t=localT;
          float front=.5*aGrowth;
          if(abs(branch)>.5) t=abs(branch)<1.5 ? mix(max(0.,front-.045),front,localT) : mix(min(1.,1.-front+.045),1.-front,localT);
          vec3 delta = aEnd - aStart;
          float distance = max(.0001, length(delta));
          vec3 direction = delta / distance;
          vec3 bend = cross(direction, vec3(0.,0.,1.));
          if(length(bend)<.01) bend = cross(direction,vec3(0.,1.,0.));
          if(length(bend)<.0001) bend=vec3(0.,1.,0.);
          bend = normalize(bend) * min(.20,.045+.06*distance)*uDetail;
          vec3 center = mix(aStart,aEnd,t) + bend*sin(3.14159265*t);
          float fan=smoothstep(0.,.15,aGrowth)*(1.-smoothstep(.8,1.,aGrowth));
          if(abs(branch)>.5) center+=normalize(bend)*sign(branch)*min(.10,.12*distance)*localT*localT*fan;
          vec3 tangent = delta + bend*3.14159265*cos(3.14159265*t);
          vec4 eye = modelViewMatrix*vec4(center,1.);
          vec2 projected = (modelViewMatrix*vec4(tangent,0.)).xy;
          vec2 normal = length(projected)>.0001 ? normalize(vec2(-projected.y,projected.x)) : vec2(1.,0.);
          float terminal=exp(-2200.*pow(t-.5*aGrowth,2.))+exp(-2200.*pow(t-1.+.5*aGrowth,2.));
          float taper=abs(branch)>.5 ? mix(.45,.06,localT) : (.65+.35*sin(3.14159265*t))*smoothstep(0.,.035,min(t,1.-t));
          float radius=(.003+.006*aStrength)*taper;
          if(abs(branch)<.5) radius*=1.+1.5*terminal*aSemantic;
          if(aSemantic<.5 || uDetail<.5) radius=.0012;
          eye.xy += normal*position.x*radius;
          gl_Position = projectionMatrix*eye;
          vAcross=position.x; vAlong=t; vGrowth=aGrowth; vOpacity=aOpacity; vColor=aColor;
          vSemantic=aSemantic*uDetail;vBranch=abs(branch);vGap=min(.015,.006/distance);
        }`,
      fragmentShader: `
        varying float vAcross, vAlong, vGrowth, vOpacity, vSemantic, vBranch, vGap;
        varying vec3 vColor;
        void main() {
          if(vAlong>vGrowth*.5 && vAlong<1.-vGrowth*.5) discard;
          if(vSemantic<.5 && vBranch>.5) discard;
          if(vBranch>.5 && vGrowth>=.999) discard;
          if(vSemantic>.5 && abs(vAlong-.5)<vGap) discard;
          float rounded=sqrt(max(0.,1.-vAcross*vAcross));
          float alpha=vOpacity*smoothstep(0.,.25,rounded)*(vBranch>.5?.65:1.);
          gl_FragColor=vec4(vColor*(.58+.42*rounded),alpha);
          #include <tonemapping_fragment>
          #include <colorspace_fragment>
        }`,
    }));
    this.mesh.frustumCulled = false;
    this.mesh.name = 'source-synaptic-bridges';
    this.mesh.visible = false;
  }

  set(synapses: readonly Synapse[], focus: string | null, reducedMotion: boolean,
    ports?: ReadonlyMap<string, readonly Point3[]>, centers?: ReadonlyMap<string, Point3>): void {
    if (this.disposed) return;
    this.focus = focus;
    const incoming = new Set(synapses.slice(0, this.activeCap).map(edge => edge.key));
    this.records.forEach(record => { record.active = incoming.has(record.key); });
    for (const edge of synapses.slice(0, this.activeCap)) {
      let record = this.index.get(edge.key);
      if (!record) {
        if (this.records.length >= this.capacity) this.remove(this.records.findIndex(candidate => !candidate.active));
        record = { ...edge, goal: edge.strength, growth: reducedMotion ? 1 : 0, active: true, color: this.neutral.clone(), ax:0,ay:0,az:0,bx:0,by:0,bz:0,
          portA:-1,portB:-1,dockAx:0,dockAy:0,dockAz:0,dockBx:0,dockBy:0,dockBz:0 };
        this.records.push(record); this.index.set(edge.key, record);
      }
      record.goal = edge.strength; record.purpose = edge.purpose; record.active = true;
      const a=centers?.get(edge.source),b=centers?.get(edge.target);
      const aPorts=edge.purpose==='navigation'?undefined:ports?.get(edge.source),bPorts=edge.purpose==='navigation'?undefined:ports?.get(edge.target);
      if(!aPorts?.[record.portA]) record.portA=facingPort(aPorts,a,b);
      if(!bPorts?.[record.portB]) record.portB=facingPort(bPorts,b,a);
      const pa=aPorts?.[record.portA],pb=bPorts?.[record.portB];
      record.dockAx=pa?.x??0;record.dockAy=pa?.y??0;record.dockAz=pa?.z??0;
      record.dockBx=pb?.x??0;record.dockBy=pb?.y??0;record.dockBz=pb?.z??0;
      record.color.copy(edge.source === focus || edge.target === focus ? this.selected : this.neutral);
      if (reducedMotion) { record.strength = record.goal; record.growth = 1; }
    }
    if (reducedMotion) for (let i = this.records.length - 1; i >= 0; i--) if (!this.records[i].active) this.remove(i);
    this.dirty = true; this.moving = !reducedMotion && this.records.some(record => !record.active || record.growth < 1 || Math.abs(record.strength - record.goal) > .001);
    this.geometry.instanceCount = this.records.length; this.mesh.visible = this.records.length > 0;
  }

  update(dt: number, points: ReadonlyMap<string, Point3>, reducedMotion: boolean, positionsChanged = true): boolean {
    if (this.disposed) return false;
    if (!this.dirty && !this.moving && !positionsChanged) return false;
    let changed = this.dirty; this.moving = false;
    const elapsed = Number.isFinite(dt) ? Math.min(.25, Math.max(0, dt)) : 0;
    const alpha = 1 - Math.exp(-elapsed / .24);
    for (let i = this.records.length - 1; i >= 0; i--) {
      const record = this.records[i], target = record.active ? 1 : 0;
      if (reducedMotion) record.growth = target;
      else record.growth += (target - record.growth) * alpha;
      if (Math.abs(record.growth - target) < .003) record.growth = target;
      if (!record.active && record.growth === 0) { this.remove(i); changed = true; continue; }
      record.strength += (record.goal - record.strength) * (reducedMotion ? 1 : alpha);
      if (Math.abs(record.goal - record.strength) < .001) record.strength = record.goal;
      if (record.growth !== target || record.strength !== record.goal) this.moving = true;
    }
    for (let i = 0; i < this.records.length; i++) {
      const record = this.records[i];
      const a = points.get(record.source), b = points.get(record.target), at = i * 3;
      if (a) {record.ax=a.x+record.dockAx; record.ay=a.y+record.dockAy; record.az=a.z+record.dockAz;}
      if (b) {record.bx=b.x+record.dockBx; record.by=b.y+record.dockBy; record.bz=b.z+record.dockBz;}
      if (this.starts[at] !== Math.fround(record.ax) || this.starts[at+1] !== Math.fround(record.ay) || this.starts[at+2] !== Math.fround(record.az)
        || this.ends[at] !== Math.fround(record.bx) || this.ends[at+1] !== Math.fround(record.by) || this.ends[at+2] !== Math.fround(record.bz)) changed=true;
      this.starts[at]=record.ax; this.starts[at+1]=record.ay; this.starts[at+2]=record.az;
      this.ends[at]=record.bx; this.ends[at+1]=record.by; this.ends[at+2]=record.bz;
      if (this.growths[i] !== Math.fround(record.growth) || this.strengths[i] !== Math.fround(record.strength)) changed = true;
      this.strengths[i] = record.strength; this.growths[i] = record.growth;
      const selected = this.focus !== null && (record.source === this.focus || record.target === this.focus);
      this.opacities[i] = (this.detail ? (record.purpose === 'navigation' ? .12 : record.active ? .42 + .22 * record.strength : .28)
        : selected ? .52 : record.purpose === 'navigation' ? .035 : .065 + .055 * record.strength) * record.growth;
      this.semantic[i] = record.purpose === 'navigation' ? 0 : 1;
      this.colors[at] = record.color.r; this.colors[at + 1] = record.color.g; this.colors[at + 2] = record.color.b;
    }
    if (changed) for (let i=0;i<this.attributes.length;i++) this.attributes[i].needsUpdate = true;
    this.geometry.instanceCount = this.records.length; this.mesh.visible = this.records.length > 0;
    this.dirty = false; return changed;
  }

  highlight(id:string|null):void {
    if(this.disposed)return;
    this.focus = id;
    for(const record of this.records)record.color.copy(id&&(record.source===id||record.target===id)?this.selected:this.neutral);
    this.dirty=true;
  }

  diagnostics(): { active: number; retiring: number; forming: number; formed: number; dockedEndpoints: number; capacity: number; bufferBytes: number; moving: boolean } {
    const active = this.records.filter(record => record.active).length;
    return { active, retiring: this.records.length - active, forming:this.records.filter(r=>r.active&&r.growth<1).length,
      formed:this.records.filter(r=>r.active&&r.growth===1).length,
      dockedEndpoints:this.records.reduce((sum,r)=>sum+Number(r.portA>=0)+Number(r.portB>=0),0),capacity: this.capacity, moving: this.moving,
      bufferBytes: this.starts.byteLength + this.ends.byteLength + this.strengths.byteLength + this.growths.byteLength + this.opacities.byteLength + this.colors.byteLength + this.semantic.byteLength };
  }

  private remove(at: number): void {
    if (at < 0) return;
    this.index.delete(this.records[at].key);
    const last = this.records.pop()!;
    if (at < this.records.length) this.records[at] = last;
    this.dirty = true;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true; this.geometry.dispose(); (this.mesh.material as THREE.Material).dispose();
    this.records.length = 0; this.index.clear();
  }
}
