import * as THREE from "three";
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { AnimationLoop } from "../core/animation-loop";
import type { AnimationLoopDiagnostics } from "../core/animation-loop";
import { ContextNebulae } from './cortex';
import { contextRegions, sourceRegions, regionBounds } from './context-regions';
import { createNeuronGlyph, NEURON_EXTENT_RATIO } from './neuron';
import type { NeuronPort } from './neuron';
import { ConstellationNodes, minimumStarRadius, maximumOverviewStarRadius } from './constellation';
import { pickKnowledgeSphere } from "./picking";
import { overviewCameraDistance } from './camera-fit';
import { overviewDisplayPoint, overviewGlyphScale, OVERVIEW_DISPLAY_GAIN } from './overview-presentation';
import { PlasticityLayout, selectSynapses } from './plasticity';
import { relationAnchors, relationCenterId } from './relation-layout';
import { relationDepths } from './relation-depth';
import { COMMUNITY_COLORS, RELATION_DEPTH_COLORS } from './graph-palette';
import { communitySnapshot, type CommunitySnapshot } from './community-snapshot';
import { nextRelationBoundary } from './relation-validity';
import { semanticSeedPoint, type SemanticLayoutDiagnostics } from './semantic-layout';
import { SemanticLayoutDriver, type LayoutResponse } from './semantic-layout-driver';
import { PositionTransition } from './position-transition';
import { projectedLayoutMetrics, type ProjectedCircle } from './projected-layout-metrics';
import { relaxProjectedCircles, type RelaxationResult } from './projected-relaxation';
import { projectedOffsetGain } from './projected-display-offset';
import { compareOverviewLabelPriority, placeOverviewLabel, type LabelBox } from './label-placement';
import type { LayoutAffinity } from './layout-affinity';
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

const LABEL_TYPOGRAPHY = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle',
  'fontStretch', 'fontVariant', 'letterSpacing', 'lineHeight'] as const;
type CachedLabelBox = LabelBox & { typography?: string[] };

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
  private readonly provisionalOverviewPositions = new Set<string>();
  private readonly displayedPositions = new Map<string, THREE.Vector3>();
  private readonly displayOffsets = new Map<string, THREE.Vector3>();
  private readonly displayTransition = new PositionTransition();
  private readonly displayClipMatrix = new THREE.Matrix4();
  private displayRelaxationPending = true;
  private projectedRelaxation: (RelaxationResult['diagnostics'] & {state:'applied'|'rejected';reason?:string}) | null = null;
  private readonly nebulae = new ContextNebulae();
  private readonly anchors = new Map<string, THREE.Vector3>();
  private readonly plasticity = new PlasticityLayout(OVERVIEW_NODE_CAP,512);
  private readonly semanticLayout = new SemanticLayoutDriver(result => this.applySemanticLayout(result));
  private communityState:CommunitySnapshot|null=null;
  private communitySlots=new Map<string,number>();
  private readonly layoutChanged = new Set<string>();
  private readonly positionTransition = new PositionTransition();
  private readonly layoutFingerprints = new Map<string, string>();
  private layoutDiagnostics: SemanticLayoutDiagnostics | null = null;
  private readonly synapses = new SynapticBridges();
  private readonly overviewSynapses = new SynapticBridges(4096,false);
  private readonly constellationNodes = new ConstellationNodes(OVERVIEW_LOD_CAP);
  private readonly globalLabels = new Set<string>();
  private readonly globalLabelRanks = new Map<string, number>();
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
  private readonly labelBounds: CachedLabelBox[] = [];
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
  private renderedFrames = 0;
  private renderedNodeCount = 0;
  private renderedVisibleNodeCount: number | null = null;
  private fitAfterSemanticLayout = false;
  private activityExpiry: ReturnType<typeof setTimeout> | null = null;
  private relationBoundaryTimer:ReturnType<typeof setTimeout>|null=null;
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
    this.orbit.addEventListener('start',()=>{this.orbitActive=true;this.fitAfterSemanticLayout=false;});
    this.orbit.addEventListener('end',()=>{this.orbitActive=false;this.displayRelaxationPending=true;this.invalidateFrame();});
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
    for (const selector of ['.knowledge-heading, .mini-graph-heading', '.knowledge-focus-card', '.knowledge-hologram-toolbar, .mini-graph-title', '.knowledge-depth-legend']) {
      const overlay = workspace?.querySelector<HTMLElement>(selector);
      if (overlay) { this.labelOverlays.push(overlay); this.excludedLabelBounds.push({left:0,top:0,right:0,bottom:0}); this.resizeObserver?.observe(overlay); }
    }
    this.resize();
  }

  start(): void {
    if (this.disposed) return;
    this.requestedAnimation = true;
    this.queueSemanticLayout();
    this.scheduleRelationBoundary();
    this.updateOrbActivity();
    if (this.view.nodes.length > 0) { this.loop.start(); this.voicePoller?.start(); }
  }

  stop(): void {
    if(this.relationBoundaryTimer!==null){clearTimeout(this.relationBoundaryTimer);this.relationBoundaryTimer=null;}
    this.semanticLayout.stop();
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
    if (!graph.nodes.length) {
      this.semanticLayout.clear(); this.layoutFingerprints.clear(); this.positionTransition.clear();
      this.provisionalOverviewPositions.clear();
      this.communityState=null;this.communitySlots.clear();
    }
    this.graph = graph;
    const sorted = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id));
    const ids = new Set(sorted.map(node => node.id));
    for (const id of this.positions.keys()) if (!ids.has(id)) this.positions.delete(id);
    for (const id of this.provisionalOverviewPositions) if (!ids.has(id)) this.provisionalOverviewPositions.delete(id);
    for (const id of this.displayedPositions.keys()) if (!ids.has(id)) this.displayedPositions.delete(id);
    this.view = navigation ? this.drilldown.restore(graph, navigation) : this.drilldown.replaceGraph(graph);
    this.rebuildGraph();
    if (navigation || focus && !ids.has(focus)) this.resetOverviewCamera();
    if (navigation && this.view.focusId && this.view.hops > 0) this.focusCamera(this.view.focusId);
    if (viewpoint && [viewpoint.camera, viewpoint.lookAt].every(values => values.length === 3 && values.every(Number.isFinite))) {
      const camera = new THREE.Vector3().fromArray(viewpoint.camera);
      const lookAt = new THREE.Vector3().fromArray(viewpoint.lookAt);
      const distance = camera.distanceTo(lookAt);
      // The saved pose has its own radius. The current graph fit may have
      // lowered the controls limit, making this otherwise valid pose
      // unreachable and keeping the idle render loop alive forever.
      if (Number.isFinite(distance) && distance >= this.orbit.minDistance) {
        this.desiredCamera.copy(camera); this.desiredLookAt.copy(lookAt);
        this.orbit.maxDistance = Math.max(12, distance + .5);
        this.restoredWindowSize={width:window.innerWidth,height:window.innerHeight};
      }
    }
    this.canvas.hidden = graph.nodes.length === 0;
    this.resize(false);
    if (this.requestedAnimation && graph.nodes.length > 0) { this.loop.start(); this.voicePoller?.start(); }
    else { this.loop.stop(); this.voicePoller?.stop(); this.voiceEnvelope.clear(); this.voiceSource = "none"; }
    this.notifyView();
  }

  /** A geometry-only enrichment. It never rewrites edges, triggers a read
   * activity event, replaces selection, or refits the user's camera. */
  setLayoutAffinity(value: LayoutAffinity | null): void {
    if (this.disposed) return;
    this.graph.layoutAffinity = value ?? undefined;
    this.view.layoutAffinity = value ?? undefined;
    if (this.view.hops !== 0) return;
    this.applyCommunityLabels(this.semanticLayout.labelsFor(this.view,new Set(this.view.focusId?[this.view.focusId]:[])));
    this.layoutPositions(); this.preparePositionMotion();
    if (this.requestedAnimation) this.loop.start(); this.invalidateFrame();
  }

  get semanticDiagnostics() { return { worker: this.semanticLayout.diagnostics, layout: this.layoutDiagnostics, pendingTargets: this.positionTransition.pending,
    projectedCorrection:this.projectedRelaxation,
    communities:this.communityState?{state:'computed',nodes:this.communityState.nodes,clusters:this.communityState.clusters,
      multiNodeClusters:this.communityState.multiNodeClusters,majorGroups:this.communityState.groups.length,
      majorNodes:this.communityState.nodes-this.communityState.otherNodes,otherNodes:this.communityState.otherNodes}:null,
    affinity: this.graph.layoutAffinity ? { state: this.graph.layoutAffinity.state, pairs: this.graph.layoutAffinity.pairs.length,
      usable: this.graph.layoutAffinity.usableNodes, requested: this.graph.layoutAffinity.requestedNodes,
      pairSampling: this.graph.layoutAffinity.pairSampling ?? null,
      comparedNodes: this.graph.layoutAffinity.vectorNodesCompared ?? null,
      eligibleNodes: this.graph.layoutAffinity.eligibleVectorNodes ?? null,
      comparisonMin: this.graph.layoutAffinity.comparisonMin ?? null,
      comparisonMax: this.graph.layoutAffinity.comparisonMax ?? null,
      nodePairCoverage: this.graph.layoutAffinity.nodePairCoverage ?? null,
      pairCoverage: this.graph.layoutAffinity.pairCoverage ?? null } : null }; }

  /** Constant-cost readiness geometry; no projection or pair audit. */
  auditViewport() {
    if (this.disposed || this.neuronDetail) return null;
    const rect=this.canvas.getBoundingClientRect();
    return {left:rect.left+this.viewport.x,top:rect.top+this.viewport.y,
      width:this.viewport.width,height:this.viewport.height};
  }

  /** Explicit audit snapshot only; no labels, identities or per-frame pair work. */
  auditProjectedLayout(includeCircles = false) {
    const points:ProjectedCircle[]=[],viewport=this.auditViewport();
    if (!viewport) return { available:false,reason:'overview_unavailable' };
    const projected=new THREE.Vector3(), eye=new THREE.Vector3();
    const factor=viewport.height/(2*Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2)));
    for(const node of this.view.nodes.slice(0,2048)){
      const point=this.displayedPositions.get(node.id);
      if(!point){points.push({x:NaN,y:NaN,radius:0});continue;}
      eye.copy(point).applyMatrix4(this.graphRoot.matrixWorld).applyMatrix4(this.camera.matrixWorldInverse);
      projected.copy(point).applyMatrix4(this.graphRoot.matrixWorld).project(this.camera);
      // Dense billboard radius extends twice the world radius; reference core
      // alpha reaches zero at shader r=.38. This is core geometry, not glow.
      points.push({x:viewport.left+(projected.x+1)*viewport.width/2,
        y:viewport.top+(1-projected.y)*viewport.height/2,
        radius:.76*this.constellationNodes.radius(node.id)*factor/Math.max(this.camera.near,-eye.z),
        inClipVolume:projected.z>=-1&&projected.z<=1&&eye.z<0,
        ...(includeCircles&&this.anchors.has(node.id)?{solverTarget:this.anchors.get(node.id)!.toArray() as [number,number,number]}:{})});
    }
    return {scope:'on-demand projected reference core circles; no pixel, glow, label or semantic-quality proof',
      renderedFrames:this.renderedFrames,renderedNodes:this.renderedNodeCount,viewNodes:this.view.nodes.length,
      inputTruncated:this.view.nodes.length>2048,viewport,displayCorrection:this.projectedRelaxation,
      camera:this.camera.position.toArray(),lookAt:this.lookAt.toArray(),
      metrics:projectedLayoutMetrics(points,viewport), ...(includeCircles ? { circles: points } : {})};
  }

  auditPhysicsState() { return this.plasticity.auditState(); }

  get lifecycleDiagnostics() {
    return { animationRequested:this.requestedAnimation, emergencyPaused:this.paused, disposed:this.disposed };
  }

  get motionDiagnostics() {
    return {
      camera: this.camera.position.distanceToSquared(this.desiredCamera) > .000001
        || this.lookAt.distanceToSquared(this.desiredLookAt) > .000001,
      requestedCameraDistance: this.desiredCamera.distanceTo(this.desiredLookAt),
      cameraMinDistance: this.orbit.minDistance, cameraMaxDistance: this.orbit.maxDistance,
      physics: this.plasticity.moving, transition: this.positionTransition.moving || this.displayTransition.moving,
      bridges: this.synapses.moving, overviewBridges: this.overviewSynapses.moving,
      regions: this.nebulae.moving, orbit: this.orbitActive,
      voice: this.voiceEnvelope.needsFrame,
    };
  }

  diagnostics(): AnimationLoopDiagnostics & { renderedFrames:number; renderedNodeCount:number; renderedVisibleNodeCount:number|null; regions: ReturnType<ContextNebulae['diagnostics']>; motion:KnowledgeHologram['motionDiagnostics']; ambientMotion:boolean; visibleTime:number; voiceRms: number; voiceSource: VoiceAmplitudeSource; displayedRms: number; audioVertices: number; orbCount: number; orbState: OrbState; orbActive: boolean; orbCacheBytes: number; physics: ReturnType<PlasticityLayout['diagnostics']>; synapses: ReturnType<SynapticBridges['diagnostics']>; drawCalls:number; geometries:number; neuronGlyphs:number; detailMode:boolean } {
    return { ...this.loop.diagnostics(), renderedFrames:this.renderedFrames, renderedNodeCount:this.renderedNodeCount, renderedVisibleNodeCount:this.renderedVisibleNodeCount, regions:this.nebulae.diagnostics(), motion:this.motionDiagnostics, voiceRms: this.voiceEnvelope.targetRms, voiceSource: this.voiceSource,
      displayedRms: this.voiceEnvelope.displayedRms, audioVertices: this.voiceEnvelope.line.geometry.getAttribute("position").count,
      orbCount: this.orbMeshes.size, orbState: this.activity.state, orbActive: this.orbMeshes.get(this.rootNodeId)?.active === true, orbCacheBytes: orbCacheBytes(), physics:this.plasticity.diagnostics(), synapses:(this.neuronDetail?this.synapses:this.overviewSynapses).diagnostics(), drawCalls:this.renderer.info.render.calls, geometries:this.renderer.info.memory.geometries, neuronGlyphs:this.neuronMeshes.size, detailMode:this.neuronDetail, ambientMotion:false, visibleTime:0 };
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
    this.semanticLayout.clear(); this.positionTransition.clear(); this.layoutChanged.clear(); this.layoutFingerprints.clear();
    this.displayOffsets.clear();this.displayTransition.clear();
    this.communityState=null;this.communitySlots.clear();
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
    let anchors;
    if (this.view.hops === 0) {
      for (const node of this.view.nodes) {
        const signature = JSON.stringify([node.sourceVersion, node.sourceTarget, node.importance]);
        if (this.layoutFingerprints.get(node.id) !== signature) this.layoutChanged.add(node.id);
        this.layoutFingerprints.set(node.id, signature);
      }
      const ids = new Set(this.view.nodes.map(node => node.id));
      for (const id of this.layoutFingerprints.keys()) if (!ids.has(id)) this.layoutFingerprints.delete(id);
      // Seed only new real nodes cheaply while the worker computes R/S.
      // Existing visible coordinates remain intact until a verified result.
      anchors = new Map(this.view.nodes.map(node => [node.id, this.positions.get(node.id) ?? semanticSeedPoint(node.id)]));
    } else {
      this.semanticLayout.stop(); anchors = relationAnchors(this.view, this.graph, 24);
      for(const node of this.view.nodes)this.provisionalOverviewPositions.add(node.id);
    }
    for (const [id, p] of anchors) {
      const point = new THREE.Vector3(p.x, p.y, p.z);
      this.anchors.set(id, point);
      if (!this.positions.has(id)) {
        this.positions.set(id, point.clone());this.provisionalOverviewPositions.add(id);
      }
    }
    this.queueSemanticLayout();
  }

  private queueSemanticLayout(): void {
    if (!this.requestedAnimation || this.disposed || this.view.hops !== 0 || !this.view.nodes.length) return;
    this.semanticLayout.request(this.view, this.positions, this.layoutChanged, new Set(this.view.focusId ? [this.view.focusId] : []),
      this.provisionalOverviewPositions);
  }

  private applySemanticLayout(result: LayoutResponse): void {
    if (!this.requestedAnimation || this.disposed || this.view.hops !== 0 || !result.anchors) return;
    const ids = new Set(this.view.nodes.map(node => node.id));
    if (result.anchors.length !== ids.size || result.anchors.length > 2048 || new Set(result.anchors.map(([id]) => id)).size !== ids.size
      || result.anchors.some(([id, p]) => !ids.has(id) || ![p.x, p.y, p.z].every(Number.isFinite))) return;
    this.anchors.clear();
    for (const [id, p] of result.anchors) this.anchors.set(id, new THREE.Vector3(p.x, p.y, p.z));
    this.layoutChanged.clear(); this.layoutDiagnostics = result.diagnostics ?? null;
    this.applyCommunityLabels(result.checkpoint?.labels);
    this.preparePositionMotion();
    // A fit made from temporary seed points may miss the later worker anchors.
    // Finish that explicit sparse fit once; never override an orbit or saved pose.
    if (this.fitAfterSemanticLayout && !this.orbitActive && !this.restoredWindowSize) this.resetOverviewCamera();
    this.fitAfterSemanticLayout = false;
    this.loop.start(); this.invalidateFrame();
  }

  private preparePositionMotion(): void {
    this.displayRelaxationPending = true;
    // Selected overview nodes stay fixed while their surrounding geometry
    // updates. All other displayed nodes receive targets, including late IDs.
    const view = this.view.hops === 0 && this.view.focusId
      ? { ...this.view, nodes: this.view.nodes.filter(node => node.id !== this.view.focusId) } : this.view;
    this.plasticity.setGraph(view, this.anchors, this.positions);
    const physics = new Set<string>(); this.plasticity.forEachPoint(id => physics.add(id));
    this.positionTransition.setTargets(this.anchors, this.positions, physics);
    if (this.reducedMotionEnabled) { this.plasticity.settle(); this.positionTransition.advance(this.positions, 0, true); }
    this.plasticity.forEachPoint(this.syncPoint);
  }

  private rebuildGraph(): void {
    this.displayRelaxationPending = true;
    const visibleIds = new Set(this.view.nodes.map(node=>node.id));
    for(const id of this.displayOffsets.keys())if(!visibleIds.has(id))this.displayOffsets.delete(id);
    if(this.view.hops!==0||this.view.nodes.length<=120){this.displayOffsets.clear();this.displayTransition.clear();this.projectedRelaxation=null;}
    this.scheduleRelationBoundary();
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
    this.hoveredId=null;this.globalLabels.clear();this.globalLabelRanks.clear();
    this.constellationNodes.set(this.view.nodes,this.view.edges);
    this.constellationNodes.highlight(focusId);
    const depths = relationDepths(this.view);
    this.constellationNodes.setRelationDepths(depths);
    this.updateDepthLegend(depths);
    if(global)this.applyCommunityLabels(this.semanticLayout.labelsFor(this.view,new Set(focusId?[focusId]:[])));
    else this.communityState=null;
    this.constellationNodes.visible=true;this.neuronDetail=false;
    this.orbit.enableRotate=true;this.orbit.mouseButtons.LEFT=THREE.MOUSE.ROTATE;
    const labelledGroups = new Set<string>();
    for (const node of [...this.view.nodes].sort((a,b)=>this.constellationNodes.radius(b.id)-this.constellationNodes.radius(a.id)||a.id.localeCompare(b.id))) {
      const group = node.sourceTarget || node.space || node.category;
      if (!labelledGroups.has(group)) { this.globalLabels.add(node.id); this.globalLabelRanks.set(node.id,this.globalLabelRanks.size); labelledGroups.add(group); }
      if (this.globalLabels.size >= 8) break;
    }
    this.rootNodeId = relationCenterId(this.view);

    this.preparePositionMotion();
    const regions = global?sourceRegions(this.view):contextRegions(this.view, this.graph);
    this.nebulae.set(regions);
    this.canvas.setAttribute('aria-label', global?`${this.view.nodes.length}개 기억의 연결 성도`:`${this.view.nodes.length}개 기억, ${regions.length}개 맥락의 연결 그림`);
    this.canvas.title = global ? '' : regions.map(region => region.label).join(' · ');
    this.nebulae.update(0, this.positions, this.reducedMotionEnabled);

    const sharedPickGeometry=new THREE.SphereGeometry(1,8,6);
    const sharedPickMaterial=new THREE.MeshBasicMaterial({visible:false});
    this.pickGeometry=sharedPickGeometry;this.pickMaterial=sharedPickMaterial;
    for (const node of this.view.nodes) {
      const position = this.positions.get(node.id);
      if (!position) continue;
      if(!this.displayedPositions.has(node.id))this.displayedPositions.set(node.id,position.clone());
      const focused = node.id === focusId;
      const kind=node.category==='collection'?node.label:node.category;
      const token=/인물|사람|대화 상대|화자/.test(kind)?'--cosmos-person':/대화방/.test(kind)?'--cosmos-room':/주제/.test(kind)?'--cosmos-topic':'--accent';
      const depth = depths.get(node.id);
      const color=depth !== undefined ? new THREE.Color(RELATION_DEPTH_COLORS[Math.min(4,depth)])
        : global?new THREE.Color('#cad8e9'):cssColor(this.canvas,token,"#dde7ef");
      const isOrb = !global&&(node.isHub || node.category === 'collection');
      const diameter = node.id === this.rootNodeId ? 0.64 : node.category === 'collection' ? 0.4 : 0.3;
      const radius = global?this.constellationNodes.radius(node.id):isOrb ? diameter * 0.44 : (.04 + (node.importance / 100) * .025) * (focused ? 1.2 : 1);
      const mesh = new THREE.Mesh(sharedPickGeometry, sharedPickMaterial);
      mesh.scale.setScalar(this.constellationNodes.radius(node.id));
      mesh.userData.glyph = { radius, diameter, color, isOrb, kind };
      mesh.position.copy(position);
      mesh.userData.nodeId = node.id;
      this.nodeMeshes.set(node.id, mesh);
      // Global pick spheres are CPU targets. Keeping thousands of invisible
      // objects in the rendered scene needlessly traverses them each frame.
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
        label.hidden = true;
        label.textContent = node.label;
        label.title = node.label;
        label.dataset.focused = String(focused);
        label.dataset.view='overview';
        labelLayer.append(label);
        this.labels.set(node.id, label);
        this.labelBounds.push({ width: 0, left: 0, top: 0, visible: false });
      }
    }
    this.prioritizeOverviewLabels();
    const activeIds=new Set(this.view.nodes.map(node=>node.id));
    this.synapses.set(global?[]:selectSynapses(this.view.edges,activeIds),focusId,true,this.neuronPorts,this.anchors);
    this.synapses.update(0,this.positions,this.reducedMotionEnabled);
    this.overviewSynapses.set(selectSynapses(this.view.edges,activeIds,Date.now(),global?4096:144,global),focusId,this.reducedMotionEnabled);
    this.overviewSynapses.setRelationDepths(depths);
    this.overviewSynapses.update(0,this.positions,this.reducedMotionEnabled);
    this.constellationNodes.update(this.positions);
    this.applyDetailMode(false,true);
  }

  private updateDepthLegend(depths: ReadonlyMap<string, number>): void {
    const legend = this.canvas.parentElement?.querySelector<HTMLElement>('.knowledge-depth-legend');
    if (!legend) return;
    legend.dataset.kind='relation-depth';legend.removeAttribute('title');legend.setAttribute('aria-label','현재 자료의 연결 거리');
    legend.hidden = depths.size === 0;
    legend.replaceChildren();
    if (legend.hidden) return;
    const title = document.createElement('span');
    title.textContent = '연결 거리';
    legend.append(title);
    const present = new Set([...depths.values()].map(depth => Math.min(4, depth)));
    for (let depth = 0; depth < RELATION_DEPTH_COLORS.length; depth++) {
      if (!present.has(depth)) continue;
      const entry = document.createElement('span');
      const marker = document.createElement('i');
      marker.style.backgroundColor = RELATION_DEPTH_COLORS[depth];
      marker.setAttribute('aria-hidden', 'true');
      entry.append(marker, depth === 0 ? '선택' : depth === 4 ? '4단계 이상' : `${depth}단계`);
      legend.append(entry);
    }
  }

  private applyCommunityLabels(labels:unknown):void {
    const state=communitySnapshot(this.view.nodes.map(n=>n.id),labels,this.communitySlots);
    this.communityState=state;
    if(state)this.communitySlots=state.slots;
    this.constellationNodes.setCommunityColors(state?.colors??new Map());
    if(this.view.hops!==0||this.view.focusId)return;
    const legend=this.canvas.parentElement?.querySelector<HTMLElement>('.knowledge-depth-legend');
    if(!legend)return;
    legend.replaceChildren();legend.hidden=!state;
    legend.dataset.kind='communities';
    legend.removeAttribute('title');legend.removeAttribute('aria-label');
    if(!state)return;
    legend.setAttribute('aria-label','현재 화면의 주요 연결 모임과 자료 수. 내용 비교는 부분 결과입니다.');
    legend.title='현재 표시한 자료의 관계와 사용 가능한 내용 비교로 계산한 모임입니다. 내용 비교는 부분 결과입니다. 기타에는 여러 모임과 독립 자료가 포함됩니다. 원본 자료와 사람은 각각 유지됩니다.';
    const title=document.createElement('span');title.textContent=state.groups.length?'주요 모임 · 일부':'계산된 모임 없음';legend.append(title);
    for(const group of state.groups){
      const entry=document.createElement('span'),marker=document.createElement('i');
      marker.style.backgroundColor=COMMUNITY_COLORS[group.slot];marker.setAttribute('aria-hidden','true');
      entry.setAttribute('aria-label',`모임 ${group.slot}, ${group.count}개 자료`);
      entry.append(marker,`${group.slot} · ${group.count.toLocaleString()}`);legend.append(entry);
    }
    if(state.otherNodes){
      const entry=document.createElement('span'),marker=document.createElement('i');
      marker.style.backgroundColor=COMMUNITY_COLORS[0];marker.setAttribute('aria-hidden','true');
      entry.append(marker,`${state.groups.length?'기타':'독립'} ${state.otherNodes.toLocaleString()}`);legend.append(entry);
    }
  }

  /** One owned timer; no frame polling, reads, voice or activity receipts.
   * Hidden/stopped views resume from current time through start(). */
  private scheduleRelationBoundary():void {
    if(this.relationBoundaryTimer!==null)clearTimeout(this.relationBoundaryTimer);
    this.relationBoundaryTimer=null;
    if(!this.requestedAnimation||this.disposed)return;
    const now=Date.now(),boundary=nextRelationBoundary(this.view.edges,now);
    if(boundary===null)return;
    this.relationBoundaryTimer=setTimeout(()=>{
      this.relationBoundaryTimer=null;
      if(!this.requestedAnimation||this.disposed)return;
      this.rebuildGraph();this.invalidateFrame();
    },Math.min(2147483647,Math.max(1,boundary-now)));
  }

  /** Detail is paid for once, only after zooming into the real local view. */
  private ensureDetailGlyphs(): void {
    if (this.view.hops === 0) return;
    for (const node of this.view.nodes) {
      if (this.neuronMeshes.has(node.id) || this.orbMeshes.has(node.id)) continue;
      const mesh = this.nodeMeshes.get(node.id), position = this.positions.get(node.id);
      if (!mesh || !position) continue;
      const glyph = mesh.userData.glyph as { radius:number; diameter:number; color:THREE.Color; isOrb:boolean; kind:string };
      if (glyph.isOrb) {
        const state: OrbState = /인물|사람|대화 상대|화자/.test(glyph.kind) ? 'listening' : /대화방/.test(glyph.kind) ? 'connecting' : /주제/.test(glyph.kind) ? 'composing' : 'solving';
        const orb = new ThinkingOrbMesh(state, glyph.diameter, glyph.color);
        orb.position.copy(position);
        orb.setViewportHeight(this.viewport.height * this.renderer.getPixelRatio());
        this.orbMeshes.set(node.id, orb); this.graphRoot.add(orb);
      } else {
        const neuron = createNeuronGlyph(glyph.radius, glyph.color, node.id);
        neuron.position.copy(position);
        this.neuronMeshes.set(node.id, neuron); this.neuronPorts.set(node.id, neuron.ports);
        this.graphRoot.add(neuron);
      }
    }
  }

  private focusCamera(nodeId: string): void {
    const position = this.anchors.get(nodeId) ?? this.positions.get(nodeId);
    if (!position) return;
    this.desiredLookAt.copy(position);
    const relative = this.view.nodes.flatMap(node => {
      const point = this.anchors.get(node.id) ?? this.positions.get(node.id);
      return point ? [{x:point.x-position.x,y:point.y-position.y,z:point.z-position.z}] : [];
    });
    const distance = Math.max(this.orbit.minDistance,
      overviewCameraDistance(relative,this.viewport.width,this.viewport.height,this.camera.fov,.16));
    this.desiredCamera.set(position.x, position.y, position.z + distance);
    this.orbit.maxDistance = Math.max(12,distance+.5);
    this.invalidateFrame();
  }

  private readonly rememberPress=(event:PointerEvent):void=>{this.press.set(event.clientX,event.clientY);};
  private cancelHover(): void {
    if(typeof this.hoverFrame==='number')cancelAnimationFrame(this.hoverFrame);
    this.hoverFrame=null;
  }
  // Refresh the collision order only on graph rebuild or hover change.
  // Selection stays ahead of hover; representative ranks follow node radius.
  private prioritizeOverviewLabels(): void {
    if (this.view.hops !== 0) return;
    const ordered = [...this.labels].sort(([first], [second]) =>
      compareOverviewLabelPriority(first, second, this.view.focusId, this.hoveredId, this.globalLabelRanks));
    this.labels.clear();
    for (const [id, label] of ordered) this.labels.set(id, label);
    this.labelBounds.length = 0;
    for (const _ of this.labels) this.labelBounds.push({width:0,left:0,top:0,visible:false});
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
      label.hidden=true;
      label.textContent=node.label;label.title=node.label;label.dataset.view='overview';
      label.dataset.focused=String(id===this.view.focusId);layer.append(label);this.labels.set(id,label);
    }
    this.prioritizeOverviewLabels();
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
    if(detail)this.ensureDetailGlyphs();
    this.neuronDetail=detail;
    this.constellationNodes.visible=!detail;this.nebulae.visible=detail;
    for(const [id,mesh] of this.nodeMeshes){
      const radius=mesh.geometry instanceof THREE.SphereGeometry?mesh.geometry.parameters.radius:1;
      mesh.scale.setScalar(detail ? (mesh.userData.glyph?.radius ?? radius) / radius : this.constellationNodes.radius(id)/radius);
    }
    for(const neuron of this.neuronMeshes.values())neuron.visible=detail;
    for(const orb of this.orbMeshes.values())orb.visible=detail;
    for(const label of this.labels.values())label.dataset.view=detail?'local':'overview';
    // This switches CSS padding as well as visual mode. The next frame must
    // measure the new box, even when its text and projected anchor are unchanged.
    for(const box of this.labelBounds)box.width=0;
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
    this.displayRelaxationPending = true;
    const points = this.view.nodes.flatMap(node => {
      const point = this.anchors.get(node.id) ?? this.positions.get(node.id);
      return point ? [overviewDisplayPoint(point,this.viewport.width,this.viewport.height,
        this.view.hops===0&&this.view.nodes.length>120,new THREE.Vector3())] : [];
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
    const distance=overviewCameraDistance(relative,this.viewport.width,this.viewport.height,this.camera.fov,
      this.view.hops===0?.08:.32,this.view.hops===0&&this.view.nodes.length>120);
    this.desiredCamera.set(x,y,z+distance);this.desiredLookAt.set(x,y,z);
    this.fitAfterSemanticLayout = this.view.hops === 0 && this.view.nodes.length > 0
      && (this.view.nodes.length <= 24 || this.provisionalOverviewPositions.size > 0);
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
    if(previousWidth!==this.viewport.width||previousHeight!==this.viewport.height)this.displayRelaxationPending=true;
    this.renderer.setViewport(this.viewport.x, height - this.viewport.y - this.viewport.height, this.viewport.width, this.viewport.height);
    this.overviewSynapses.setViewportHeight(this.viewport.height);
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
    // Finalize the camera before testing correction readiness or projecting its
    // display cap. Reduced motion must finish in this same requested frame.
    const cameraProjectionChanged=this.camera.position.distanceToSquared(this.desiredCamera)>0
      ||this.lookAt.distanceToSquared(this.desiredLookAt)>0||this.orbitActive;
    const alpha=this.reducedMotionEnabled||this.paused?1:1-Math.exp(-12*elapsed);
    this.camera.position.lerp(this.desiredCamera,alpha);this.lookAt.lerp(this.desiredLookAt,alpha);
    this.camera.lookAt(this.lookAt);this.orbit.target.copy(this.lookAt);this.orbit.update();
    this.camera.updateMatrixWorld(true);

    // Physics points stay within the 2.3-unit envelope. Use a larger bound
    // to underestimate pixel size for the nearest possible point, including
    // orbit/zoom, so a visible drift cannot be hidden by the rest criterion.
    this.camera.getWorldDirection(this.projected);
    const nearestDepth = Math.max(this.camera.near, -this.camera.position.dot(this.projected) - 3);
    const halfFovTangent = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    // Include perspective motion from depth changes at the viewport corners.
    const perspectiveBound = Math.hypot(1, halfFovTangent * Math.hypot(1, this.camera.aspect));
    const denseOverview=this.view.hops===0&&this.view.nodes.length>120;
    const cameraMoving=this.camera.position.distanceToSquared(this.desiredCamera)>0.000001||this.lookAt.distanceToSquared(this.desiredLookAt)>0.000001;
    this.plasticity.setDisplayScale(2 * nearestDepth * halfFovTangent / (Math.max(1, this.viewport.height) * perspectiveBound
      * (denseOverview?OVERVIEW_DISPLAY_GAIN:1)));
    const pointsMoving = this.plasticity.moving || this.positionTransition.moving;
    if (this.reducedMotionEnabled && this.plasticity.moving) this.plasticity.settle();
    else if (!this.paused) this.plasticity.advance(elapsed);
    if (!this.paused) this.positionTransition.advance(this.positions, elapsed, this.reducedMotionEnabled);
    this.plasticity.forEachPoint(this.syncPoint);
    this.constellationNodes.setMinimumRadius(minimumStarRadius(this.camera.position.distanceTo(this.lookAt),
      this.camera.fov, this.viewport.height, this.view.nodes.length),
      overviewGlyphScale(this.viewport.width,this.viewport.height,denseOverview),
      denseOverview?maximumOverviewStarRadius(this.camera.position.distanceTo(this.lookAt),this.camera.fov,this.viewport.height):Infinity);
    // The source advance above can settle on this very frame. Testing its
    // pre-advance state would stop RAF before the first correction can run.
    if(denseOverview&&this.displayRelaxationPending&&!this.plasticity.moving
      &&!this.positionTransition.moving&&!cameraMoving&&!this.orbitActive&&this.semanticLayout.settled)
      this.prepareDisplayCorrection();
    this.displayTransition.freeze(this.view.focusId);
    const displayMoving=this.displayTransition.moving;
    this.displayTransition.advance(this.displayOffsets,elapsed,this.reducedMotionEnabled||this.paused);
    if (!this.neuronDetail) for (const [id, mesh] of this.nodeMeshes) mesh.scale.setScalar(this.constellationNodes.radius(id));
    if(denseOverview&&this.displayOffsets.size){
      this.graphRoot.updateMatrixWorld(true);
      this.displayClipMatrix.multiplyMatrices(this.camera.projectionMatrix,this.camera.matrixWorldInverse).multiply(this.graphRoot.matrixWorld);
    }
    for (const [id,mesh] of this.nodeMeshes) {
      const base=this.positions.get(id),position=this.displayedPositions.get(id);
      if(base&&position){
        overviewDisplayPoint(base,this.viewport.width,this.viewport.height,denseOverview,position);
        if(denseOverview){const offset=this.displayOffsets.get(id);
          if(offset&&offset.lengthSq()>0)position.addScaledVector(offset,
            projectedOffsetGain(position,offset,this.displayClipMatrix.elements,this.viewport.width,this.viewport.height));}
        mesh.position.copy(position);this.orbMeshes.get(id)?.position.copy(position);this.neuronMeshes.get(id)?.position.copy(position);
      }
    }
    this.synapses.update(elapsed,this.displayedPositions,this.reducedMotionEnabled||this.paused,pointsMoving||displayMoving||denseOverview&&cameraProjectionChanged);
    this.overviewSynapses.update(elapsed,this.displayedPositions,this.reducedMotionEnabled||this.paused,pointsMoving||displayMoving||denseOverview&&cameraProjectionChanged);
    this.constellationNodes.update(this.displayedPositions);
    const nodeActivityMoving = this.constellationNodes.advanceActivity(Date.now(), this.reducedMotionEnabled || this.paused);
    if(pointsMoving || displayMoving || this.nebulae.moving || denseOverview&&cameraProjectionChanged) this.nebulae.update(elapsed,this.displayedPositions,this.reducedMotionEnabled||this.paused);
    this.loop.setInteractive(this.plasticity.moving||this.positionTransition.moving||this.displayTransition.moving||this.synapses.moving||this.overviewSynapses.moving||this.nebulae.moving||this.orbitActive||cameraMoving);
    this.applyDetailMode(this.view.hops>0&&this.camera.position.distanceTo(this.lookAt)<2.35);
    this.synapses.mesh.visible=this.neuronDetail&&(this.synapses.mesh.geometry as THREE.InstancedBufferGeometry).instanceCount>0;
    this.overviewSynapses.mesh.visible=!this.neuronDetail&&(this.overviewSynapses.mesh.geometry as THREE.InstancedBufferGeometry).instanceCount>0;
    if (this.voiceEnvelope.needsFrame) this.voiceEnvelope.advance(dt);
    if (this.latestVoice && Date.now() / 1000 - this.latestVoice.updatedAt > 3) { this.latestVoice = null; this.updateOrbActivity(); }
    const orbActive = this.orbMeshes.get(this.rootNodeId)?.active === true;
    for (const orb of this.orbMeshes.values()) orb.advance(dt);
    this.loop.setVoiceActive(this.voiceEnvelope.displayedRms > 0 || orbActive);
    this.renderer.render(this.scene, this.camera);
    if(this.provisionalOverviewPositions.size&&this.view.hops===0&&!this.plasticity.moving
      &&!this.positionTransition.moving&&this.semanticLayout.settled)
      for(const node of this.view.nodes)this.provisionalOverviewPositions.delete(node.id);
    this.renderedFrames++;
    this.renderedNodeCount = this.view.nodes.length;
    // Only sparse fixtures need a clip-volume certificate. Dense rendering
    // retains its existing frame cost; this count does not attest pixel opacity.
    this.renderedVisibleNodeCount = null;
    if (this.view.nodes.length <= 24) {
      let visible = 0;
      for (const node of this.view.nodes) {
        const position = this.displayedPositions.get(node.id);
        if (!position) continue;
        this.projected.copy(position).applyMatrix4(this.graphRoot.matrixWorld).project(this.camera);
        if (Math.abs(this.projected.x) <= 1 && Math.abs(this.projected.y) <= 1
          && this.projected.z >= -1 && this.projected.z <= 1) visible++;
      }
      this.renderedVisibleNodeCount = visible;
    }
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
      // Inline typography reads do not request layout. Re-measure only after
      // rebuild/resize/mode or an explicit typography change; transforms never
      // invalidate the metric cache. Keep at most 24 measured collision boxes.
      let metricsChanged = !box.width || !box.typography;
      if (!metricsChanged) for (let key = 0; key < LABEL_TYPOGRAPHY.length; key++) {
        if (box.typography![key] !== label.style[LABEL_TYPOGRAPHY[key]]) { metricsChanged = true; break; }
      }
      if (metricsChanged) {
        label.hidden = false;
        box.width = label.offsetWidth || Math.min(156, label.textContent!.length * 11 + 12);
        box.height = label.offsetHeight || 24;
        box.typography = LABEL_TYPOGRAPHY.map(key => label.style[key]);
      }
      const labelHeight = box.height ?? 24;
      box.left = Math.max(x, Math.min(x + width - box.width, x + (this.projected.x + 1) * width / 2 - box.width / 2));
      const mesh = this.nodeMeshes.get(id);
      const radius = mesh?.geometry instanceof THREE.SphereGeometry ? mesh.geometry.parameters.radius*mesh.scale.x : 0;
      const extent = this.neuronDetail&&this.neuronMeshes.has(id) ? radius * NEURON_EXTENT_RATIO : radius;
      const offset = Math.min(54, extent * height / (2 * Math.tan(this.camera.fov * Math.PI / 360) * Math.max(.1, this.camera.position.distanceTo(position))) + 8);
      box.top = Math.min(y + height - labelHeight, y + (1 - this.projected.y) * height / 2 + offset);
      if (this.view.hops === 0) {
        placeOverviewLabel(box, this.viewport, this.excludedLabelBounds, this.labelBounds, index - 1);
      } else {
        // Keep local/drilldown collision behavior unchanged.
        let collision = box.width > width || labelHeight > height;
        for (const overlay of this.excludedLabelBounds) {
          if (overlay.right > overlay.left && box.left < overlay.right && box.left + box.width > overlay.left && box.top < overlay.bottom && box.top + labelHeight > overlay.top) { collision = true; break; }
        }
        for (let previous = 0; previous < index - 1; previous++) {
          const other = this.labelBounds[previous];
          if (other.visible && box.left < other.left + other.width + 5 && box.left + box.width + 5 > other.left && box.top < other.top + (other.height ?? 24) && box.top + labelHeight > other.top) {
            collision = true;
            break;
          }
        }
        box.visible = !collision;
      }
      if (label.hidden === box.visible) label.hidden = !box.visible;
      if (box.visible && (!wasVisible || previousLeft !== box.left || previousTop !== box.top)) label.style.transform = `translate(${box.left}px, ${box.top}px)`;
    }
    if (this.paused || this.reducedMotionEnabled || (!nodeActivityMoving && !this.plasticity.moving && !this.positionTransition.moving && !this.displayTransition.moving && !this.synapses.moving && !this.overviewSynapses.moving && !this.nebulae.moving && !orbActive && !this.voiceEnvelope.needsFrame && !this.orbitActive && !cameraMoving)) this.loop.stop();
  }
  private prepareDisplayCorrection(): void {
    const {width,height}=this.viewport;
    if(width<=0||height<=0)return;
    this.camera.updateMatrixWorld(true);this.graphRoot.updateMatrixWorld(true);
    const inverseRoot=this.graphRoot.matrixWorld.clone().invert(),factor=height/(2*Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2)));
    const circles:ProjectedCircle[]=[],bases:THREE.Vector3[]=[],depths:number[]=[],initial=new Float64Array(this.view.nodes.length*2);
    const pinned=new Set<number>(),projected=new THREE.Vector3(),eye=new THREE.Vector3(),shifted=new THREE.Vector3();
    for(const [index,node] of this.view.nodes.entries()){
      const source=this.positions.get(node.id);
      if(!source)return;
      const base=new THREE.Vector3();overviewDisplayPoint(source,width,height,true,base);bases.push(base);
      projected.copy(base).applyMatrix4(this.graphRoot.matrixWorld).project(this.camera);depths.push(projected.z);
      eye.copy(base).applyMatrix4(this.graphRoot.matrixWorld).applyMatrix4(this.camera.matrixWorldInverse);
      circles.push({x:(projected.x+1)*width/2,y:(1-projected.y)*height/2,
        radius:.76*this.constellationNodes.radius(node.id)*factor/Math.max(this.camera.near,-eye.z),
        inClipVolume:projected.z>=-1&&projected.z<=1&&eye.z<0});
      const offset=this.displayOffsets.get(node.id);
      if(offset){
        shifted.copy(base).add(offset).applyMatrix4(this.graphRoot.matrixWorld).project(this.camera);
        initial[index*2]=(shifted.x-projected.x)*width/2;initial[index*2+1]=(projected.y-shifted.y)*height/2;
      }else this.displayOffsets.set(node.id,new THREE.Vector3());
      if(node.id===this.view.focusId)pinned.add(index);
    }
    const result=relaxProjectedCircles(circles,{left:0,top:0,width,height},pinned,initial),targets=new Map<string,THREE.Vector3>();
    this.view.nodes.forEach((node,index)=>{
      if(node.id===this.view.focusId){targets.set(node.id,this.displayOffsets.get(node.id)!.clone());return;}
      const dx=result.offsets[index*2],dy=result.offsets[index*2+1];
      if(!dx&&!dy){targets.set(node.id,new THREE.Vector3());return;}
      const target=new THREE.Vector3((circles[index].x+dx)/width*2-1,1-(circles[index].y+dy)/height*2,depths[index])
        .unproject(this.camera).applyMatrix4(inverseRoot).sub(bases[index]);
      targets.set(node.id,target);
    });
    const accepted=this.displayTransition.setTargets(targets,this.displayOffsets,new Set(this.view.focusId?[this.view.focusId]:[]));
    this.projectedRelaxation={...result.diagnostics,state:accepted?'applied':'rejected',
      ...(!accepted?{reason:'display_correction_invalid_targets'}:{})};
    // A rejected snapshot waits for the next real source/camera change. It
    // never reports application success or keeps RAF busy retrying bad input.
    this.displayRelaxationPending=false;
  }
  private readonly syncPoint = (id:string,x:number,y:number,z:number):void => {this.positions.get(id)?.set(x,y,z);};
}
