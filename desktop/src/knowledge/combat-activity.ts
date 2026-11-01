import * as THREE from 'three';
import { ACTIVITY_TTL_MS, type NodeActivity } from './collection-activity';
import type { KnowledgeEdge, KnowledgeNode } from './graph-model';
import { relationCurrentlyValid } from './relation-validity';
import type { Point3 } from './plasticity';

const CAPACITY = 48, PATHS_PER_EVENT = 4, DURATION = 2700;
type Exchange = { event: NodeActivity; peer: string; edge: KnowledgeEdge | null; began: number; delay: number };
type Batch = { mesh: THREE.Mesh<THREE.InstancedBufferGeometry, THREE.ShaderMaterial>;
  starts: Float32Array; ends: Float32Array; phases: Float32Array; colors: Float32Array };
const colors = { read: new THREE.Color('#7edcf3'), added: new THREE.Color('#8ce7cb'), revised: new THREE.Color('#ffc18d') };

function batch(capacity: number, impact: boolean): Batch {
  const geometry = new THREE.InstancedBufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([-1,-1,0, 1,-1,0, 1,1,0, -1,-1,0, 1,1,0, -1,1,0], 3));
  const starts = new Float32Array(capacity * 3), ends = new Float32Array(capacity * 3);
  const phases = new Float32Array(capacity), values = new Float32Array(capacity * 3);
  for (const [name, array, size] of [['aStart',starts,3],['aEnd',ends,3],['aPhase',phases,1],['aColor',values,3]] as const) {
    geometry.setAttribute(name, new THREE.InstancedBufferAttribute(array, size).setUsage(THREE.DynamicDrawUsage));
  }
  geometry.instanceCount = 0;
  const material = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending,
    uniforms: { uHeight: { value: 1 } },
    vertexShader: `
      uniform float uHeight;
      attribute vec3 aStart, aEnd, aColor;
      attribute float aPhase;
      varying vec2 vUv;
      varying vec3 vColor;
      varying float vPhase;
      void main() {
        vUv=position.xy; vColor=aColor; vPhase=aPhase;
        ${impact ? `
          vec4 eye=modelViewMatrix*vec4((aStart+aEnd)*.5,1.);
          float pixel=2.*abs(eye.z)/(projectionMatrix[1][1]*max(1.,uHeight));
          eye.xy+=position.xy*mix(9.,34.,aPhase)*pixel;
        ` : `
          float tail=max(0.,aPhase-.22);
          float along=mix(tail,aPhase,(position.x+1.)*.5)*.5;
          vec4 eye=modelViewMatrix*vec4(mix(aStart,aEnd,along),1.);
          vec2 delta=(modelViewMatrix*vec4(aEnd-aStart,0.)).xy;
          vec2 normal=length(delta)>.00001?normalize(vec2(-delta.y,delta.x)):vec2(1.,0.);
          float pixel=2.*abs(eye.z)/(projectionMatrix[1][1]*max(1.,uHeight));
          eye.xy+=normal*position.y*2.8*pixel;
        `}
        gl_Position=projectionMatrix*eye;
      }`,
    fragmentShader: `
      varying vec2 vUv;
      varying vec3 vColor;
      varying float vPhase;
      void main() {
        ${impact ? `
          float r=length(vUv);
          float ring=exp(-pow((r-.72)/.035,2.));
          float shield=exp(-pow((r-.52)/.09,2.))*.32;
          float core=exp(-r*r*24.)*(1.-vPhase);
          float rays=pow(max(0.,1.-abs(vUv.x*vUv.y)*28.),3.)*exp(-r*r*3.)*.24;
          float alpha=(ring+shield+core+rays)*(1.-vPhase)*.9;
        ` : `
          float alpha=exp(-vUv.y*vUv.y*4.)*pow((vUv.x+1.)*.5,1.4)*.95;
        `}
        if(alpha<.008) discard;
        gl_FragColor=vec4(vColor*1.35,alpha);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.frustumCulled = false; mesh.renderOrder = 7;
  mesh.name = impact ? 'knowledge-activity-shields' : 'knowledge-activity-crossfire';
  return { mesh, starts, ends, phases, colors: values };
}

/** Finite visual responses to admitted real receipts, never ambient battles.
 * Crossfire is a visual metaphor; it does not assign hostility or direction to
 * knowledge. No decorative nodes, relations, events or counters are created. */
export class CombatActivity extends THREE.Group {
  private readonly shots = batch(CAPACITY * 2, false);
  private readonly impacts = batch(CAPACITY, true);
  private exchanges: Exchange[] = [];
  private readonly seen = new Set<string>();
  private nodes = new Map<string, KnowledgeNode>();
  private edges: readonly KnowledgeEdge[] = [];
  private disposed = false;

  constructor() { super(); this.name = 'real-knowledge-activity-combat'; this.add(this.shots.mesh, this.impacts.mesh); }

  setGraph(nodes: readonly KnowledgeNode[], edges: readonly KnowledgeEdge[]): void {
    this.nodes = new Map(nodes.filter(node => !node.evidence.retracted).map(node => [node.id,node]));
    this.edges = edges;
    this.exchanges = this.exchanges.filter(exchange => this.admitted(exchange.event)
      && this.nodes.has(exchange.peer) && (!exchange.edge || edges.includes(exchange.edge) && !exchange.edge.evidence.retracted));
    this.shots.mesh.geometry.instanceCount = this.impacts.mesh.geometry.instanceCount = 0;
  }
  private admitted(event: NodeActivity): boolean {
    const node = this.nodes.get(event.document_id);
    return event.success === true && Object.hasOwn(colors, event.kind) && !!node && !node.evidence.retracted
      && node.sourceVersion === event.version && node.sourceTarget === event.target_id;
  }
  show(event: NodeActivity, now: number): boolean {
    const at = event.at * 1000;
    if (this.disposed || !Number.isFinite(now) || !this.admitted(event) || !Number.isFinite(at) || at > now || now-at >= ACTIVITY_TTL_MS || this.seen.has(event.event_id)) return false;
    this.seen.add(event.event_id);
    if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value!);
    const peers = new Map<string, KnowledgeEdge>();
    for (const edge of this.edges) {
      if (edge.evidence.retracted || edge.purpose === 'navigation' || !relationCurrentlyValid(edge, now)) continue;
      const peer = edge.source === event.document_id ? edge.target : edge.target === event.document_id ? edge.source : null;
      if (peer && peer !== event.document_id && this.nodes.has(peer) && !peers.has(peer)) peers.set(peer, edge);
      if (peers.size >= PATHS_PER_EVENT) break;
    }
    const paths: Array<[string,KnowledgeEdge|null]> = peers.size ? [...peers] : [[event.document_id,null]];
    paths.forEach(([peer,edge], index) => this.exchanges.push({event,peer,edge,began:now,delay:index*110}));
    if (this.exchanges.length > CAPACITY) this.exchanges.splice(0,this.exchanges.length-CAPACITY);
    return true;
  }
  advance(now: number, points: ReadonlyMap<string, Point3>, height: number, staticMode: boolean): boolean {
    if (this.disposed) return false;
    if (staticMode) { this.clearActivity(); return false; }
    let shotCount = 0, impactCount = 0;
    this.exchanges = this.exchanges.filter(exchange => now >= exchange.began && now-exchange.began < DURATION
      && now-exchange.event.at*1000 < ACTIVITY_TTL_MS && this.admitted(exchange.event)
      && !this.nodes.get(exchange.peer)?.evidence.retracted
      && (!exchange.edge || !exchange.edge.evidence.retracted && relationCurrentlyValid(exchange.edge,now)));
    const put = (target: Batch, slot: number, a: Point3, b: Point3, phase: number, color: THREE.Color) => {
      const i=slot*3;
      target.starts[i]=a.x;target.starts[i+1]=a.y;target.starts[i+2]=a.z;
      target.ends[i]=b.x;target.ends[i+1]=b.y;target.ends[i+2]=b.z;
      target.phases[slot]=phase;target.colors[i]=color.r;target.colors[i+1]=color.g;target.colors[i+2]=color.b;
    };
    for (const exchange of this.exchanges) {
      const a=points.get(exchange.event.document_id),b=points.get(exchange.peer);
      if (!a || !b || ![a.x,a.y,a.z,b.x,b.y,b.z].every(Number.isFinite)) continue;
      const age=now-exchange.began-exchange.delay;
      if (age<0 || age>=2300) continue;
      const phase=age%1150,color=colors[exchange.event.kind];
      if (exchange.edge && phase<720) {
        put(this.shots,shotCount++,a,b,phase/720,color);
        put(this.shots,shotCount++,b,a,phase/720,color);
      } else if (phase>=720 || !exchange.edge) {
        put(this.impacts,impactCount++,a,b,exchange.edge?(phase-720)/430:phase/1150,color);
      }
    }
    for (const [target,count] of [[this.shots,shotCount],[this.impacts,impactCount]] as const) {
      target.mesh.material.uniforms.uHeight.value=Math.max(1,height);
      target.mesh.geometry.instanceCount=count;
      if(count)for(const name of ['aStart','aEnd','aPhase','aColor'])target.mesh.geometry.getAttribute(name).needsUpdate=true;
    }
    return this.exchanges.length>0;
  }
  diagnostics() { return { exchanges:this.exchanges.length,shots:this.shots.mesh.geometry.instanceCount,
    impacts:this.impacts.mesh.geometry.instanceCount,capacity:CAPACITY,ambient:false,
    bufferBytes:[this.shots,this.impacts].reduce((sum,b)=>sum+b.starts.byteLength+b.ends.byteLength+b.phases.byteLength+b.colors.byteLength,0) }; }
  clearActivity(): void { this.exchanges=[];this.seen.clear();this.shots.mesh.geometry.instanceCount=this.impacts.mesh.geometry.instanceCount=0; }
  dispose(): void { if(this.disposed)return;this.clearActivity();this.disposed=true;this.removeFromParent();for(const b of [this.shots,this.impacts]){b.mesh.geometry.dispose();b.mesh.material.dispose();} }
}
