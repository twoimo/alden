import * as THREE from "three";
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { AnimationLoop } from "../core/animation-loop";
import type { AnimationLoopDiagnostics } from "../core/animation-loop";
import { ContextNebulae } from './cortex';
import { contextRegions, sourceRegions, regionBounds } from './context-regions';
import { createNeuronGlyph, NEURON_EXTENT_RATIO } from './neuron';
import type { NeuronPort } from './neuron';
import { ConstellationNodes } from './constellation';
import { pickKnowledgeSphere } from "./picking";
import { overviewCameraDistance } from './camera-fit';
import { PlasticityLayout, selectSynapses } from './plasticity';
import { relationAnchors, relationCenterId } from './relation-layout';
import { SynapticBridges } from './synapses';
import { VoiceEnvelope } from "./voice-envelope";
import { VoiceAmplitudePoller, type VoiceStatusLoader } from "../voice-amplitude-poller";
import type { VoiceAmplitudeSource } from "../voice-amplitude";
import { fetchVoiceStatus } from "../runtime";
import type { EmergencyState, RuntimeSnapshot, VoiceStatus } from '../contracts';
import { ThinkingOrbMesh } from '../orbs/mesh';
import { runtimeOrb, QUIET_ORB, PAUSED_ORB, type OrbActivity } from '../orbs/activity';
import { orbCacheBytes, type OrbState } from '../orbs/frames';
import { ACTIVITY_TTL_MS, type NodeActivity } from './collection-activity';
import {
  KnowledgeDrilldown,
  OVERVIEW_NODE_CAP,
  OVERVIEW_LOD_CAP,
  type KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeView,
} from "./graph-model";

export interface KnowledgeFocusEvent {
  node: KnowledgeNode | null;
  view: KnowledgeView;
  previousCamera?: { camera: number[]; lookAt: number[] };
}

type FocusHandler = (event: KnowledgeFocusEvent) => void;

function cssColor(surface: Element, name: string, fallback: string): THREE.Color {
  const value = getComputedStyle(surface).getPropertyValue(name).trim();
  return new THREE.Color(value || fallback);
}

function disposeObject(object: THREE.Object3D): void {
  const geometries=new Set<THREE.BufferGeometry>(), materials=new Set<THREE.Material>();
  object.traverse((child) => {
    if (child instanceof THREE.Mesh || child instanceof THREE.Points || child instanceof THREE.LineSegments || child instanceof THREE.Line) {
      if (child instanceof THREE.InstancedMesh) child.dispose();
      if(!geometries.has(child.geometry)){geometries.add(child.geometry);child.geometry.dispose();}
      const list = Array.isArray(child.material) ? child.material : [child.material];
      for(const material of list)if(!materials.has(material)){materials.add(material);material.dispose();}
    }
  });
}

export class KnowledgeHologram {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(38, 2, 0.1, 60);
  private readonly graphRoot = new THREE.Group();
  private readonly voiceEnvelope = new VoiceEnvelope();
  private readonly voicePoller: VoiceAmplitudePoller | null;
  private voiceSource: VoiceAmplitudeSource = "none";
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly loop: AnimationLoop;
  private readonly orbit: OrbitControls;
  private orbitActive=false;
  private readonly press=new THREE.Vector2();
  private drilldown: KnowledgeDrilldown;
  private readonly positions = new Map<string, THREE.Vector3>();
  private readonly displayedPositions = new Map<string, THREE.Vector3>();
  private readonly nebulae = new ContextNebulae();
  private readonly anchors = new Map<string, THREE.Vector3>();
  private readonly plasticity = new PlasticityLayout(OVERVIEW_NODE_CAP,512);
  private readonly synapses = new SynapticBridges();
  private readonly overviewSynapses = new SynapticBridges(4096,false);
  private readonly constellationNodes = new ConstellationNodes(OVERVIEW_LOD_CAP);
  private readonly globalLabels = new Set<string>();
  private hoveredId:string|null=null;
  private hoverFrame: number | null = null;
  private hoverX = 0;
  private hoverY = 0;
  private pickGeometry: THREE.SphereGeometry | null = null;
  private pickMaterial: THREE.MeshBasicMaterial | null = null;
  private neuronDetail=false;
  private readonly nodeMeshes = new Map<string, THREE.Mesh>();
  private readonly orbMeshes = new Map<string, ThinkingOrbMesh>();
  private readonly neuronMeshes = new Map<string, THREE.Group>();
  private readonly neuronPorts = new Map<string, readonly NeuronPort[]>();
  private readonly motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  private snapshot: RuntimeSnapshot | null = null;
  private latestVoice: VoiceStatus | null = null;
  private paused = false;
  private reducedMotion = false;
  private activity: OrbActivity = QUIET_ORB;
  private rootNodeId = '';
  private readonly labels = new Map<string, HTMLSpanElement>();
  private readonly labelBounds: Array<{ width: number; left: number; top: number; visible: boolean }> = [];
  private readonly labelOverlays: HTMLElement[] = [];
  private readonly excludedLabelBounds: Array<{ left:number; top:number; right:number; bottom:number }> = [];
  private readonly projected = new THREE.Vector3();
  private readonly desiredCamera = new THREE.Vector3(0, 0, 5.2);
  private readonly desiredLookAt = new THREE.Vector3();
  private restoredWindowSize: {width:number; height:number} | null = null;
  private readonly lookAt = new THREE.Vector3();
  private readonly resizeObserver: ResizeObserver | null;
  private view: KnowledgeView;
  private disposed = false;
  private requestedAnimation = false;
  private activityExpiry: ReturnType<typeof setTimeout> | null = null;
  private activityAccepted = 0;
  private lastNodeActivity: NodeActivity | null = null;
  private viewport = { x: 0, y: 0, width: 1, height: 1 };

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private graph: KnowledgeGraph,
    private readonly onFocus: FocusHandler,
    private readonly onDispose: () => void = () => undefined,
    private readonly layoutMode: 'workspace'|'popover' = 'workspace',
    voiceLoader: VoiceStatusLoader | null = fetchVoiceStatus,
    private readonly onVoice: (voice: VoiceStatus | null) => void = () => undefined,
  ) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.camera.position.copy(this.desiredCamera);
    this.orbit=new OrbitControls(this.camera,canvas);
    this.orbit.enableDamping=false;this.orbit.minDistance=2.2;this.orbit.maxDistance=12;this.orbit.rotateSpeed=.65;this.orbit.zoomSpeed=.7;
    this.orbit.addEventListener('start',()=>{this.orbitActive=true;});
    this.orbit.addEventListener('end',()=>{this.orbitActive=false;});
    this.orbit.addEventListener('change',()=>{if(this.orbitActive){this.desiredCamera.copy(this.camera.position);this.desiredLookAt.copy(this.orbit.target);this.lookAt.copy(this.orbit.target);}this.invalidateFrame();});
    this.scene.add(this.graphRoot);
    this.graphRoot.add(this.synapses.mesh);
    this.graphRoot.add(this.overviewSynapses.mesh,this.constellationNodes);

    const ambient = new THREE.AmbientLight(0xd7e1f3, 1.75);
    const key = new THREE.DirectionalLight(0xffedcc, 2.1);
    key.position.set(2.5, 3.5, 5);
    this.scene.add(ambient, key);
    this.scene.add(this.nebulae);
    this.scene.add(this.voiceEnvelope.line);

    this.drilldown = new KnowledgeDrilldown(graph);
    this.view = this.drilldown.current();
    this.rebuildGraph();
    this.loop = new AnimationLoop((dt) => this.render(dt));
    this.voicePoller = voiceLoader ? new VoiceAmplitudePoller((rms, source) => {
      if (!this.requestedAnimation || this.disposed) return;
      this.voiceSource = source;
      this.applyVoiceRms(this.paused ? 0 : rms);
    }, voiceLoader, undefined, voice => {
      if (!this.requestedAnimation || this.disposed) return;
      this.latestVoice = voice; this.updateOrbActivity(); this.onVoice(voice);
    }) : null;
    this.canvas.addEventListener("pointerdown", this.rememberPress);
    this.canvas.addEventListener("pointerup", this.handlePointerDown);
    this.canvas.addEventListener('pointermove',this.handleHover);
    this.canvas.addEventListener('pointerleave',this.clearHover);
    this.motion.addEventListener('change', this.handleMotionChange);

    this.resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => this.resize());
    this.resizeObserver?.observe(canvas);
    const workspace = canvas.closest('.knowledge-section, .alden-panel');
    for (const selector of ['.knowledge-heading, .mini-graph-heading', '.knowledge-focus-card', '.knowledge-hologram-toolbar, .mini-graph-title']) {
      const overlay = workspace?.querySelector<HTMLElement>(selector);
      if (overlay) { this.labelOverlays.push(overlay); this.excludedLabelBounds.push({left:0,top:0,right:0,bottom:0}); this.resizeObserver?.observe(overlay); }
    }
    this.resize();
  }

  start(): void {
    if (this.disposed) return;
    this.requestedAnimation = true;
    this.updateOrbActivity();
    if (this.view.nodes.length > 0) { this.loop.start(); this.voicePoller?.start(); }
  }

  stop(): void {
    this.requestedAnimation = false;
    this.cancelHover();
    this.clearNodeActivity();
    try { this.voicePoller?.stop(); } finally {
      try { this.loop.stop(); } finally {
        this.loop.setVoiceActive(false);
        this.voiceEnvelope.clear();
        this.voiceSource = "none";
        this.latestVoice = null;
      }
    }
  }

  get renderCount(): number {
    return this.loop.renderCount;
  }

  setSignals(load:number,voiceRms:number):void {
    if (this.disposed) return;
    const bounded=Number.isFinite(load)?Math.max(0,Math.min(1,load)):0;
    this.loop.setLoad(bounded);
    // The narrow poller owns current input/output; the slow snapshot supplies load.
    if (!this.voicePoller) this.applyVoiceRms(voiceRms);
  }

  private applyVoiceRms(rms: number): void {
    this.loop.setVoiceActive(rms > 0 || this.voiceEnvelope.displayedRms > 0 || this.orbMeshes.get(this.rootNodeId)?.active === true);
    if (this.voiceEnvelope.setRms(rms)) this.invalidateFrame();
  }

  setActivity(snapshot: RuntimeSnapshot | null): void { this.snapshot = snapshot; this.updateOrbActivity(); }
  setEmergency(state: EmergencyState): void {
    if (this.disposed) return;
    this.paused = state.latched;
    if (this.paused) { this.voiceEnvelope.clear(); this.voiceSource = 'none'; }
    this.updateOrbActivity();
    this.invalidateFrame();
  }
  private readonly updateOrbActivity = (): void => {
    if (this.disposed) return;
    this.activity = this.paused ? PAUSED_ORB : runtimeOrb(this.snapshot, this.latestVoice ?? this.snapshot?.voice ?? null);
    // Runtime work belongs to Alden's sidebar orb, not an arbitrary memory.
    // Graph-node motion comes only from its real references and user navigation.
  };
  private readonly handleMotionChange = (): void => { this.updateOrbActivity(); this.invalidateFrame(); };
  get reducedMotionEnabled(): boolean { return this.reducedMotion || this.motion.matches; }
  setReducedMotion(reduced: boolean): void {
    this.reducedMotion = reduced;
    if (this.reducedMotionEnabled) this.plasticity.settle();
    this.handleMotionChange();
  }
  resetCamera(): void {
    this.restoredWindowSize=null;
    if (this.view.focusId && this.view.hops > 0) this.focusCamera(this.view.focusId);
    else this.resetOverviewCamera();
    this.invalidateFrame();
  }

  private invalidateFrame(): void {
    if (!this.disposed && this.requestedAnimation && this.view.nodes.length > 0) this.loop.start();
  }

  get currentView(): KnowledgeView {
    return this.view;
  }
  get currentGraph(): KnowledgeGraph { return this.graph; }
  get nodeActivityDiagnostics() { return { active: this.constellationNodes.activityCount, accepted: this.activityAccepted, last: this.lastNodeActivity }; }

  showNodeActivity(event: NodeActivity): void {
    const now = Date.now(), at = event.at * 1000;
    if (this.disposed || !this.requestedAnimation || event.success !== true || !Number.isFinite(at)
      || at > now || now - at >= ACTIVITY_TTL_MS || !this.view.nodes.some(node => node.id === event.document_id
        && node.sourceVersion === event.version && node.sourceTarget === event.target_id && !node.evidence.retracted)) return;
    this.constellationNodes.showActivity(event); this.activityAccepted++; this.lastNodeActivity = event;
    this.constellationNodes.advanceActivity(now, this.reducedMotionEnabled);
    this.scheduleActivityExpiry(); this.invalidateFrame();
  }
  clearNodeActivity(): void {
    if (this.activityExpiry !== null) clearTimeout(this.activityExpiry);
    this.activityExpiry = null; this.lastNodeActivity = null; this.constellationNodes.clearActivity(); this.invalidateFrame();
  }
  private scheduleActivityExpiry(): void {
    if (this.activityExpiry !== null) clearTimeout(this.activityExpiry);
    this.activityExpiry = null;
    const expiry = this.constellationNodes.nextActivityExpiry;
    if (!this.requestedAnimation || !Number.isFinite(expiry)) return;
    this.activityExpiry = setTimeout(() => {
      this.activityExpiry = null;
      this.constellationNodes.advanceActivity(Date.now(), this.reducedMotionEnabled);
      this.invalidateFrame(); this.scheduleActivityExpiry();
    }, Math.max(1, expiry - Date.now()));
  }

  get canGoBack(): boolean { return this.drilldown.canGoBack; }

  replaceGraph(graph: KnowledgeGraph, navigation?: { focusId: string | null; hops: number }, viewpoint?: { camera: number[]; lookAt: number[] }): void {
    if (this.disposed) return;
    this.restoredWindowSize=null;
    const focus = this.view.focusId;
    this.graph = graph;
    const sorted = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id));
    const ids = new Set(sorted.map(node => node.id));
    for (const id of this.positions.keys()) if (!ids.has(id)) this.positions.delete(id);
    for (const id of this.displayedPositions.keys()) if (!ids.has(id)) this.displayedPositions.delete(id);
    this.view = navigation ? this.drilldown.restore(graph, navigation) : this.drilldown.replaceGraph(graph);
    this.rebuildGraph();
    if (navigation || focus && !ids.has(focus)) this.resetOverviewCamera();
    if (navigation && this.view.focusId && this.view.hops > 0) this.focusCamera(this.view.focusId);
    if (viewpoint && [viewpoint.camera, viewpoint.lookAt].every(values => values.length === 3 && values.every(Number.isFinite))) {
      this.desiredCamera.fromArray(viewpoint.camera); this.desiredLookAt.fromArray(viewpoint.lookAt);
      this.restoredWindowSize={width:window.innerWidth,height:window.innerHeight};
    }
    this.canvas.hidden = graph.nodes.length === 0;
    this.resize(false);
    if (this.requestedAnimation && graph.nodes.length > 0) { this.loop.start(); this.voicePoller?.start(); }
    else { this.loop.stop(); this.voicePoller?.stop(); this.voiceEnvelope.clear(); this.voiceSource = "none"; }
    this.notifyView();
  }

  diagnostics(): AnimationLoopDiagnostics & { regions: ReturnType<ContextNebulae['diagnostics']>; ambientMotion:boolean; visibleTime:number; voiceRms: number; voiceSource: VoiceAmplitudeSource; displayedRms: number; audioVertices: number; orbCount: number; orbState: OrbState; orbActive: boolean; orbCacheBytes: number; physics: ReturnType<PlasticityLayout['diagnostics']>; synapses: ReturnType<SynapticBridges['diagnostics']>; drawCalls:number; geometries:number } {
    return { ...this.loop.diagnostics(), regions:this.nebulae.diagnostics(), voiceRms: this.voiceEnvelope.targetRms, voiceSource: this.voiceSource,
      displayedRms: this.voiceEnvelope.displayedRms, audioVertices: this.voiceEnvelope.line.geometry.getAttribute("position").count,
      orbCount: this.orbMeshes.size, orbState: this.activity.state, orbActive: this.orbMeshes.get(this.rootNodeId)?.active === true, orbCacheBytes: orbCacheBytes(), physics:this.plasticity.diagnostics(), synapses:(this.neuronDetail?this.synapses:this.overviewSynapses).diagnostics(), drawCalls:this.renderer.info.render.calls, geometries:this.renderer.info.memory.geometries, ambientMotion:false, visibleTime:0 };
  }
  get navigationTargets(): { camera: number[]; lookAt: number[] } {
    return { camera: this.desiredCamera.toArray(), lookAt: this.desiredLookAt.toArray() };
  }

  clickNode(nodeId: string): KnowledgeView {
    if (this.disposed) return this.view;
    this.restoredWindowSize=null;
    const node = this.graph.nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return this.view;
    const previousCamera = this.navigationTargets;
    this.view = this.drilldown.openNote(nodeId);
    this.rebuildGraph();
    this.resetOverviewCamera();this.invalidateFrame();
    this.onFocus({ node, view: this.view, previousCamera });
    return this.view;
  }

  expandOneHop(): KnowledgeView {
    if (this.disposed) return this.view;
    this.restoredWindowSize=null;
    this.view = this.drilldown.expandOneHop();
    this.rebuildGraph();
    if (this.view.focusId&&this.view.hops>0) this.focusCamera(this.view.focusId);
    else {this.resetOverviewCamera();this.invalidateFrame();}
    this.notifyView();
    return this.view;
  }

  reset(): KnowledgeView {
    if (this.disposed) return this.view;
    this.restoredWindowSize=null;
    this.view = this.drilldown.reset();
    this.rebuildGraph();
    this.resetOverviewCamera();
    this.invalidateFrame();
    this.notifyView();
    return this.view;
  }

  back(): KnowledgeView {
    if (this.disposed) return this.view;
    this.restoredWindowSize=null;
    this.view = this.drilldown.back();
    this.rebuildGraph();
    if (this.view.focusId&&this.view.hops>0) this.focusCamera(this.view.focusId);
    else {
      this.resetOverviewCamera();
      this.invalidateFrame();
    }
    this.notifyView();
    return this.view;
  }

  private notifyView(): void {
    const node = this.graph.nodes.find((candidate) => candidate.id === this.view.focusId) ?? null;
    this.onFocus({ node, view: this.view });
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.onDispose();
    this.stop();
    this.canvas.removeEventListener("pointerdown", this.rememberPress);
    this.canvas.removeEventListener("pointerup", this.handlePointerDown);
    this.canvas.removeEventListener('pointermove',this.handleHover);
    this.canvas.removeEventListener('pointerleave',this.clearHover);
    this.motion?.removeEventListener('change', this.handleMotionChange);
    this.orbit.dispose();
    this.resizeObserver?.disconnect();
    this.synapses.mesh.removeFromParent();
    this.synapses.dispose();
    this.overviewSynapses.mesh.removeFromParent();this.overviewSynapses.dispose();
    disposeObject(this.graphRoot);
    this.scene.children
      .filter((child) => child !== this.graphRoot)
      .forEach((child) => disposeObject(child));
    this.renderer.dispose();
    this.pickGeometry?.dispose();this.pickMaterial?.dispose();
    this.labels.clear();
    this.labelBounds.length = 0;
    this.canvas.parentElement?.querySelector(".knowledge-node-labels")?.replaceChildren();
    this.canvas.parentElement?.querySelector("#knowledge-accessible-nodes")?.replaceChildren();
  }

  private layoutPositions():void {
    this.anchors.clear();
    for (const [id, p] of relationAnchors(this.view, this.graph,this.view.hops===0?OVERVIEW_LOD_CAP:24)) {
      const point = new THREE.Vector3(p.x, p.y, p.z);
      this.anchors.set(id, point);
      if (!this.positions.has(id)) this.positions.set(id, point.clone());
    }
  }

  private rebuildGraph(): void {
    this.cancelHover();
    this.layoutPositions();
    const retired=new THREE.Group();
    for (const child of [...this.graphRoot.children]) {
      if (child === this.synapses.mesh||child===this.overviewSynapses.mesh||child===this.constellationNodes) continue;
      this.graphRoot.remove(child);
      retired.add(child);
    }
    disposeObject(retired);
    this.pickGeometry?.dispose();this.pickMaterial?.dispose();
    this.pickGeometry=null;this.pickMaterial=null;
    this.nodeMeshes.clear();
    this.orbMeshes.clear();
    this.neuronMeshes.clear();
    this.neuronPorts.clear();
    this.labels.clear();
    this.labelBounds.length = 0;
    const labelLayer = this.canvas.parentElement?.querySelector(".knowledge-node-labels");
    labelLayer?.replaceChildren();
    const accessible = this.canvas.parentElement?.querySelector("#knowledge-accessible-nodes");
    accessible?.replaceChildren();
    const focusId = this.view.focusId;
    const global=this.view.hops===0;
    this.hoveredId=null;this.globalLabels.clear();
    this.constellationNodes.set(this.view.nodes,this.view.edges);
    this.constellationNodes.highlight(focusId);
    this.constellationNodes.visible=true;this.neuronDetail=false;
    this.orbit.enableRotate=true;this.orbit.mouseButtons.LEFT=THREE.MOUSE.ROTATE;
    const labelledGroups = new Set<string>();
    for (const node of [...this.view.nodes].sort((a,b)=>this.constellationNodes.radius(b.id)-this.constellationNodes.radius(a.id)||a.id.localeCompare(b.id))) {
      const group = node.sourceTarget || node.space || node.category;
      if (!labelledGroups.has(group)) { this.globalLabels.add(node.id); labelledGroups.add(group); }
      if (this.globalLabels.size >= 8) break;
    }
    this.rootNodeId = relationCenterId(this.view);

    this.plasticity.setGraph(this.view, this.anchors, this.positions);
    if (this.reducedMotionEnabled) this.plasticity.settle();
    this.plasticity.forEachPoint(this.syncPoint);
    const regions = global?sourceRegions(this.view):contextRegions(this.view, this.graph);
    this.nebulae.set(regions);
    this.canvas.setAttribute('aria-label', global?`${this.view.nodes.length}개 기억의 연결 성도`:`${this.view.nodes.length}개 기억, ${regions.length}개 맥락의 연결 그림`);
    this.canvas.title = global ? '' : regions.map(region => region.label).join(' · ');
    this.nebulae.update(0, this.positions, this.reducedMotionEnabled);

    const sharedPickGeometry=global?new THREE.SphereGeometry(1,8,6):null;
    const sharedPickMaterial=global?new THREE.MeshBasicMaterial({visible:false}):null;
    this.pickGeometry=sharedPickGeometry;this.pickMaterial=sharedPickMaterial;
    for (const node of this.view.nodes) {
      const position = this.positions.get(node.id);
      if (!position) continue;
      if(!this.displayedPositions.has(node.id))this.displayedPositions.set(node.id,position.clone());
      const focused = node.id === focusId;
      const kind=node.category==='collection'?node.label:node.category;
      const token=/인물|사람|대화 상대|화자/.test(kind)?'--cosmos-person':/대화방/.test(kind)?'--cosmos-room':/주제/.test(kind)?'--cosmos-topic':'--accent';
      const color=global?new THREE.Color('#cad8e9'):cssColor(this.canvas,token,"#dde7ef");
      const isOrb = !global&&(node.isHub || node.category === 'collection');
      const diameter = node.id === this.rootNodeId ? 0.64 : node.category === 'collection' ? 0.4 : 0.3;
      const radius = global?this.constellationNodes.radius(node.id):isOrb ? diameter * 0.44 : (.04 + (node.importance / 100) * .025) * (focused ? 1.2 : 1);
      const geometry = sharedPickGeometry??new THREE.SphereGeometry(radius, 18, 12);
      const material = sharedPickMaterial??new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: focused ? 0.28 : 0.12,
        roughness: 0.55,
        metalness: 0.12,
      });
      const mesh = new THREE.Mesh(geometry, material);
      if(global) mesh.scale.setScalar(radius);
      else if (isOrb) {
        material.visible = false;
        const state: OrbState = /인물|사람|대화 상대|화자/.test(kind) ? 'listening' : /대화방/.test(kind) ? 'connecting' : /주제/.test(kind) ? 'composing' : 'solving';
        const orb = new ThinkingOrbMesh(state, diameter, color);
        orb.position.copy(position);
        orb.setViewportHeight(this.viewport.height * this.renderer.getPixelRatio());
        this.orbMeshes.set(node.id, orb);
        this.graphRoot.add(orb);
      } else {
        material.visible = false;
        const neuron = createNeuronGlyph(radius, color, node.id);
        neuron.position.copy(position); this.neuronMeshes.set(node.id, neuron); this.graphRoot.add(neuron);
        this.neuronPorts.set(node.id, neuron.ports);
      }
      mesh.position.copy(position);
      mesh.userData.nodeId = node.id;
      this.nodeMeshes.set(node.id, mesh);
      // Global pick spheres are CPU targets. Keeping thousands of invisible
      // objects in the rendered scene needlessly traverses them each frame.
      if (!global) this.graphRoot.add(mesh);
      if (accessible) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "knowledge-a11y-node";
        button.dataset.nodeId = node.id;
        button.textContent = node.label;
        button.setAttribute("aria-label", `${node.label} 선택`);
        button.onclick = () => this.clickNode(node.id);
        accessible.append(button);
      }
      if (labelLayer && (!global || this.globalLabels.has(node.id) || focused)) {
        const label = document.createElement("span");
        label.className = "knowledge-node-label";
        label.textContent = node.label;
        label.title = node.label;
        label.dataset.focused = String(focused);
        label.dataset.view='overview';
        labelLayer.append(label);
        this.labels.set(node.id, label);
        this.labelBounds.push({ width: 0, left: 0, top: 0, visible: false });
      }
    }
    const activeIds=new Set(this.view.nodes.map(node=>node.id));
    this.synapses.set(global?[]:selectSynapses(this.view.edges,activeIds),focusId,true,this.neuronPorts,this.anchors);
    this.synapses.update(0,this.positions,this.reducedMotionEnabled);
    this.overviewSynapses.set(selectSynapses(this.view.edges,activeIds,Date.now(),global?4096:144,global),focusId,this.reducedMotionEnabled);
    this.overviewSynapses.update(0,this.positions,this.reducedMotionEnabled);
    this.constellationNodes.update(this.positions);
    this.applyDetailMode(false,true);
  }

  private focusCamera(nodeId: string): void {
    const position = this.anchors.get(nodeId) ?? this.positions.get(nodeId);
    if (!position) return;
    this.desiredLookAt.copy(position);
    this.desiredCamera.set(position.x * 0.58, position.y * 0.58, position.z + 2.55);
    this.invalidateFrame();
  }

  private readonly rememberPress=(event:PointerEvent):void=>{this.press.set(event.clientX,event.clientY);};
  private cancelHover(): void {
    if(typeof this.hoverFrame==='number')cancelAnimationFrame(this.hoverFrame);
    this.hoverFrame=null;
  }
  private refreshOverviewLabels(): void {
    if(this.view.hops!==0)return;
    const wanted=new Set(this.globalLabels);
    if(this.hoveredId)wanted.add(this.hoveredId);
    if(this.view.focusId)wanted.add(this.view.focusId);
    for(const [id,label] of this.labels)if(!wanted.has(id)){label.remove();this.labels.delete(id);}
    const layer=this.canvas.parentElement?.querySelector('.knowledge-node-labels');
    for(const id of wanted){
      if(this.labels.has(id)||!layer)continue;
      const node=this.view.nodes.find(node=>node.id===id);if(!node)continue;
      const label=document.createElement('span');label.className='knowledge-node-label';
      label.textContent=node.label;label.title=node.label;label.dataset.view='overview';
      label.dataset.focused=String(id===this.view.focusId);layer.append(label);this.labels.set(id,label);
    }
    this.labelBounds.length=0;
    for(const _ of this.labels)this.labelBounds.push({width:0,left:0,top:0,visible:false});
  }
  private readonly clearHover=():void=>{
    this.cancelHover();
    if(this.hoveredId===null)return;
    this.hoveredId=null;this.refreshOverviewLabels();this.constellationNodes.highlight(this.view.focusId);this.overviewSynapses.highlight(this.view.focusId);this.invalidateFrame();
  };
  private readonly handleHover=(event:PointerEvent):void=>{
    if(this.disposed||!this.requestedAnimation||this.neuronDetail||this.orbitActive)return;
    this.hoverX=event.clientX;this.hoverY=event.clientY;
    if(this.hoverFrame===null)this.hoverFrame=requestAnimationFrame(()=>{this.hoverFrame=null;this.pickHover();});
  };
  private pickHover(): void {
    if(this.disposed||!this.requestedAnimation||this.neuronDetail||this.orbitActive)return;
    const rect=this.canvas.getBoundingClientRect();
    const x=this.hoverX-rect.left-this.viewport.x,y=this.hoverY-rect.top-this.viewport.y;
    if(x<0||y<0||x>this.viewport.width||y>this.viewport.height){this.clearHover();return;}
    this.pointer.set(x/this.viewport.width*2-1,-y/this.viewport.height*2+1);
    this.scene.updateMatrixWorld(true);this.camera.updateMatrixWorld(true);this.raycaster.setFromCamera(this.pointer,this.camera);
    const hit=pickKnowledgeSphere(this.raycaster,this.nodeMeshes.values());
    const id=typeof hit?.userData.nodeId==='string'?hit.userData.nodeId:null;
    if(id===this.hoveredId)return;
    this.hoveredId=id;this.refreshOverviewLabels();this.constellationNodes.highlight(id??this.view.focusId);this.overviewSynapses.highlight(id??this.view.focusId);this.invalidateFrame();
  }
  private applyDetailMode(detail:boolean,force=false):void {
    if(!force&&detail===this.neuronDetail)return;
    this.neuronDetail=detail;
    this.constellationNodes.visible=!detail;this.nebulae.visible=detail;
    for(const [id,mesh] of this.nodeMeshes){
      const radius=mesh.geometry instanceof THREE.SphereGeometry?mesh.geometry.parameters.radius:1;
      mesh.scale.setScalar(detail?1:this.constellationNodes.radius(id)/radius);
    }
    for(const neuron of this.neuronMeshes.values())neuron.visible=detail;
    for(const orb of this.orbMeshes.values())orb.visible=detail;
    for(const label of this.labels.values())label.dataset.view=detail?'local':'overview';
    if(detail){
      this.synapses.set([],null,true);
      this.synapses.set(selectSynapses(this.view.edges,new Set(this.view.nodes.map(n=>n.id))),this.view.focusId,this.reducedMotionEnabled,this.neuronPorts,this.anchors);
    } else if(this.view.hops>0) {
      this.synapses.set(selectSynapses(this.view.edges,new Set(this.view.nodes.map(n=>n.id))),this.view.focusId,true,this.neuronPorts,this.anchors);
    }
  }
  private readonly handlePointerDown = (event: PointerEvent): void => {
    if(Math.hypot(event.clientX-this.press.x,event.clientY-this.press.y)>5)return;
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const x = event.clientX - rect.left - this.viewport.x;
    const y = event.clientY - rect.top - this.viewport.y;
    if (x < 0 || y < 0 || x > this.viewport.width || y > this.viewport.height) return;
    this.pointer.x = (x / this.viewport.width) * 2 - 1;
    this.pointer.y = -(y / this.viewport.height) * 2 + 1;
    // An idle scene may have changed since its last draw. Picking must use
    // the current transforms even before the invalidated frame is delivered.
    this.scene.updateMatrixWorld(true);
    this.camera.updateMatrixWorld(true);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = pickKnowledgeSphere(this.raycaster, this.nodeMeshes.values());
    const nodeId = typeof hit?.userData.nodeId === "string" ? hit.userData.nodeId : "";
    if (nodeId) this.clickNode(nodeId);
    else this.reset();
  };

  private resetOverviewCamera(): void {
    const points = this.view.nodes.flatMap(node => {
      const point = this.anchors.get(node.id) ?? this.positions.get(node.id);
      return point ? [point] : [];
    });
    for (const region of this.view.hops===0?[]:contextRegions(this.view, this.graph)) {
      const bounds = regionBounds(region, this.anchors);
      if (!bounds.radius) continue;
      points.push(new THREE.Vector3(bounds.x - bounds.radius, bounds.y - bounds.radius, bounds.z),
        new THREE.Vector3(bounds.x + bounds.radius, bounds.y + bounds.radius, bounds.z));
    }
    let x=0,y=0,z=0;
    if(this.view.hops===0&&points.length){
      x=(Math.min(...points.map(p=>p.x))+Math.max(...points.map(p=>p.x)))/2;
      y=(Math.min(...points.map(p=>p.y))+Math.max(...points.map(p=>p.y)))/2;
      z=(Math.min(...points.map(p=>p.z))+Math.max(...points.map(p=>p.z)))/2;
    }
    const relative=points.map(p=>({x:p.x-x,y:p.y-y,z:p.z-z}));
    const distance=overviewCameraDistance(relative,this.viewport.width,this.viewport.height,this.camera.fov,this.view.hops===0?.08:.32);
    this.desiredCamera.set(x,y,z+distance);this.desiredLookAt.set(x,y,z);
    // Orbit controls must admit the fitted overview; otherwise every frame
    // pulls toward an unreachable target and the idle loop never settles.
    this.orbit.maxDistance = Math.max(12,distance+.5);
  }

  private resize(resetCamera = true): void {
    if (this.disposed) return;
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width || 640));
    const height = Math.max(1, Math.floor(rect.height || 320));
    this.renderer.setSize(width, height, false);
    const previousWidth = this.viewport.width, previousHeight = this.viewport.height;
    const top = this.layoutMode==='popover'?58:12;
    const bottom=this.layoutMode==='popover'?42:16;
    this.viewport = { x: 28, y: Math.min(top, height / 2), width: Math.max(1, width - 56), height: Math.max(1, height - Math.min(top, height / 2) - bottom) };
    this.renderer.setViewport(this.viewport.x, height - this.viewport.y - this.viewport.height, this.viewport.width, this.viewport.height);
    this.camera.aspect = this.viewport.width / this.viewport.height;
    this.camera.updateProjectionMatrix();
    if(this.restoredWindowSize && (this.restoredWindowSize.width!==window.innerWidth || this.restoredWindowSize.height!==window.innerHeight))this.restoredWindowSize=null;
    // Opening/closing the note pane can notify ResizeObserver after a history
    // restore. Preserve its saved pose; a real window resize still refits.
    if (resetCamera && !this.restoredWindowSize && this.view.hops===0 && (previousWidth !== this.viewport.width || previousHeight !== this.viewport.height)) this.resetOverviewCamera();
    for (const orb of this.orbMeshes.values()) orb.setViewportHeight(this.viewport.height * this.renderer.getPixelRatio());
    for (const box of this.labelBounds) box.width = 0;
    // Overlay geometry is read only on resize/selection layout changes.
    // Labels remain available through the keyboard list when visually covered.
    const parent = this.canvas.parentElement?.getBoundingClientRect();
    if (parent) for (let overlay = 0; overlay < this.labelOverlays.length; overlay++) {
      const element = this.labelOverlays[overlay], bounds = this.excludedLabelBounds[overlay];
      if (element.hidden) { bounds.right = bounds.left = 0; continue; }
      const rect = element.getBoundingClientRect();
      bounds.left = rect.left - parent.left - 6; bounds.top = rect.top - parent.top - 6;
      bounds.right = rect.right - parent.left + 6; bounds.bottom = rect.bottom - parent.top + 6;
    }
    this.invalidateFrame();
  }

  private render(dt: number): void {
    if (this.disposed) return;
    const elapsed = Math.min(.25, Math.max(0, dt));
    // Physics points stay within the 2.3-unit envelope. Use a larger bound
    // to underestimate pixel size for the nearest possible point, including
    // orbit/zoom, so a visible drift cannot be hidden by the rest criterion.
    this.camera.getWorldDirection(this.projected);
    const nearestDepth = Math.max(this.camera.near, -this.camera.position.dot(this.projected) - 3);
    const halfFovTangent = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    // Include perspective motion from depth changes at the viewport corners.
    const perspectiveBound = Math.hypot(1, halfFovTangent * Math.hypot(1, this.camera.aspect));
    this.plasticity.setDisplayScale(2 * nearestDepth * halfFovTangent / (Math.max(1, this.viewport.height) * perspectiveBound));
    const pointsMoving = this.plasticity.moving;
    if (this.reducedMotionEnabled && this.plasticity.moving) this.plasticity.settle();
    else if (!this.paused) this.plasticity.advance(elapsed);
    this.plasticity.forEachPoint(this.syncPoint);
    this.constellationNodes.setMinimumRadius(this.camera.position.distanceTo(this.lookAt)
      * 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) / Math.max(1, this.viewport.height) * .6);
    if (this.view.hops === 0) for (const [id, mesh] of this.nodeMeshes) mesh.scale.setScalar(this.constellationNodes.radius(id));
    for (const [id,mesh] of this.nodeMeshes) {
      const base=this.positions.get(id),position=this.displayedPositions.get(id);
      if(base&&position){
        position.copy(base);
        mesh.position.copy(position);this.orbMeshes.get(id)?.position.copy(position);this.neuronMeshes.get(id)?.position.copy(position);
      }
    }
    this.synapses.update(elapsed,this.displayedPositions,this.reducedMotionEnabled||this.paused,pointsMoving);
    this.overviewSynapses.update(elapsed,this.displayedPositions,this.reducedMotionEnabled||this.paused,pointsMoving);
    this.constellationNodes.update(this.displayedPositions);
    const nodeActivityMoving = this.constellationNodes.advanceActivity(Date.now(), this.reducedMotionEnabled || this.paused);
    if(pointsMoving || this.nebulae.moving) this.nebulae.update(elapsed,this.displayedPositions,this.reducedMotionEnabled||this.paused);
    const cameraMoving=this.camera.position.distanceToSquared(this.desiredCamera)>0.000001||this.lookAt.distanceToSquared(this.desiredLookAt)>0.000001;
    this.loop.setInteractive(this.plasticity.moving||this.synapses.moving||this.overviewSynapses.moving||this.nebulae.moving||this.orbitActive||cameraMoving);
    const alpha = this.reducedMotionEnabled||this.paused ? 1 : 1 - Math.exp(-12 * elapsed);
    this.camera.position.lerp(this.desiredCamera, alpha);
    this.lookAt.lerp(this.desiredLookAt, alpha);
    this.camera.lookAt(this.lookAt);
    this.orbit.target.copy(this.lookAt);this.orbit.update();
    this.applyDetailMode(this.view.hops>0&&this.camera.position.distanceTo(this.lookAt)<2.35);
    this.synapses.mesh.visible=this.neuronDetail&&(this.synapses.mesh.geometry as THREE.InstancedBufferGeometry).instanceCount>0;
    this.overviewSynapses.mesh.visible=!this.neuronDetail&&(this.overviewSynapses.mesh.geometry as THREE.InstancedBufferGeometry).instanceCount>0;
    if (this.voiceEnvelope.needsFrame) this.voiceEnvelope.advance(dt);
    if (this.latestVoice && Date.now() / 1000 - this.latestVoice.updatedAt > 3) { this.latestVoice = null; this.updateOrbActivity(); }
    const orbActive = this.orbMeshes.get(this.rootNodeId)?.active === true;
    for (const orb of this.orbMeshes.values()) orb.advance(dt);
    this.loop.setVoiceActive(this.voiceEnvelope.displayedRms > 0 || orbActive);
    this.renderer.render(this.scene, this.camera);
    const { x, y, width, height } = this.viewport;
    let index = 0;
    for (const [id, label] of this.labels) {
      const box = this.labelBounds[index++];
      const wasVisible = box.visible, previousLeft = box.left, previousTop = box.top;
      box.visible = false;
      if(!this.neuronDetail&&!this.globalLabels.has(id)&&id!==this.hoveredId&&id!==this.view.focusId){label.hidden=true;continue;}
      const position = this.displayedPositions.get(id) ?? this.positions.get(id);
      if (!position) continue;
      this.projected.copy(position).applyMatrix4(this.graphRoot.matrixWorld).project(this.camera);
      const outside = Math.abs(this.projected.x) > 1 || Math.abs(this.projected.y) > 1 || this.projected.z > 1 || this.projected.z < -1;
      if (outside) { if (!label.hidden) label.hidden = true; continue; }
      // Measure only after a rebuild/resize. Reuse at most 24 collision boxes.
      if (!box.width) { label.hidden = false; box.width = label.offsetWidth || Math.min(156, label.textContent!.length * 11 + 12); }
      box.left = Math.max(x, Math.min(x + width - box.width, x + (this.projected.x + 1) * width / 2 - box.width / 2));
      const mesh = this.nodeMeshes.get(id);
      const radius = mesh?.geometry instanceof THREE.SphereGeometry ? mesh.geometry.parameters.radius*mesh.scale.x : 0;
      const extent = this.neuronDetail&&this.neuronMeshes.has(id) ? radius * NEURON_EXTENT_RATIO : radius;
      const offset = Math.min(54, extent * height / (2 * Math.tan(this.camera.fov * Math.PI / 360) * Math.max(.1, this.camera.position.distanceTo(position))) + 8);
      box.top = Math.min(y + height - 24, y + (1 - this.projected.y) * height / 2 + offset);
      let collision = false;
      for (const overlay of this.excludedLabelBounds) {
        if (overlay.right > overlay.left && box.left < overlay.right && box.left + box.width > overlay.left && box.top < overlay.bottom && box.top + 24 > overlay.top) { collision = true; break; }
      }
      for (let previous = 0; previous < index - 1; previous++) {
        const other = this.labelBounds[previous];
        if (other.visible && box.left < other.left + other.width + 5 && box.left + box.width + 5 > other.left && box.top < other.top + 24 && box.top + 24 > other.top) {
          collision = true;
          break;
        }
      }
      box.visible = !collision;
      if (label.hidden === box.visible) label.hidden = !box.visible;
      if (box.visible && (!wasVisible || previousLeft !== box.left || previousTop !== box.top)) label.style.transform = `translate(${box.left}px, ${box.top}px)`;
    }
    if (this.paused || this.reducedMotionEnabled || (!nodeActivityMoving && !this.plasticity.moving && !this.synapses.moving && !this.overviewSynapses.moving && !this.nebulae.moving && !orbActive && !this.voiceEnvelope.needsFrame && !this.orbitActive && !cameraMoving)) this.loop.stop();
  }
  private readonly syncPoint = (id:string,x:number,y:number,z:number):void => {this.positions.get(id)?.set(x,y,z);};
}
