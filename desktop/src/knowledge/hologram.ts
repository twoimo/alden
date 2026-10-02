import * as THREE from "three";
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { AnimationLoop } from "../core/animation-loop";
import type { AnimationLoopDiagnostics } from "../core/animation-loop";
import { createCosmosBackdrop } from "./cosmos";
import { pickKnowledgeSphere } from "./picking";
import {
  KnowledgeDrilldown,
  type KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeView,
} from "./graph-model";

export interface KnowledgeFocusEvent {
  node: KnowledgeNode | null;
  view: KnowledgeView;
}

type FocusHandler = (event: KnowledgeFocusEvent) => void;

function cssColor(surface: Element, name: string, fallback: string): THREE.Color {
  const value = getComputedStyle(surface).getPropertyValue(name).trim();
  return new THREE.Color(value || fallback);
}

function spherePoint(index: number, count: number, radius: number): THREE.Vector3 {
  const offset = 2 / Math.max(count, 1);
  const y = (index * offset - 1) + offset / 2;
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  const phi = index * Math.PI * (3 - Math.sqrt(5));
  return new THREE.Vector3(Math.cos(phi) * ring * radius, y * radius, Math.sin(phi) * ring * radius);
}

function disposeObject(object: THREE.Object3D): void {
  object.traverse((child) => {
    if (child instanceof THREE.Mesh || child instanceof THREE.Points || child instanceof THREE.LineSegments || child instanceof THREE.Line) {
      child.geometry.dispose();
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      materials.forEach((material) => material.dispose());
    }
  });
}

export class KnowledgeHologram {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(38, 2, 0.1, 60);
  private readonly graphRoot = new THREE.Group();
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly loop: AnimationLoop;
  private readonly orbit: OrbitControls;
  private orbitActive=false;
  private readonly press=new THREE.Vector2();
  private drilldown: KnowledgeDrilldown;
  private readonly positions = new Map<string, THREE.Vector3>();
  private readonly nodeMeshes = new Map<string, THREE.Mesh>();
  private readonly labels = new Map<string, HTMLSpanElement>();
  private readonly labelBounds: Array<{ width: number; left: number; top: number; visible: boolean }> = [];
  private readonly projected = new THREE.Vector3();
  private readonly desiredCamera = new THREE.Vector3(0, 0, 5.2);
  private readonly desiredLookAt = new THREE.Vector3();
  private readonly lookAt = new THREE.Vector3();
  private readonly resizeObserver: ResizeObserver | null;
  private view: KnowledgeView;
  private disposed = false;
  private requestedAnimation = false;
  private viewport = { x: 0, y: 0, width: 1, height: 1 };

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private graph: KnowledgeGraph,
    private readonly onFocus: FocusHandler,
    private readonly onDispose: () => void = () => undefined,
    private readonly layoutMode: 'workspace'|'popover' = 'workspace',
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

    const ambient = new THREE.AmbientLight(0xd7e1f3, 1.75);
    const key = new THREE.DirectionalLight(0xffedcc, 2.1);
    key.position.set(2.5, 3.5, 5);
    this.scene.add(ambient, key);
    this.scene.add(createCosmosBackdrop());

    this.layoutPositions();

    this.drilldown = new KnowledgeDrilldown(graph);
    this.view = this.drilldown.current();
    this.rebuildGraph();
    this.loop = new AnimationLoop((dt) => this.render(dt));
    this.canvas.addEventListener("pointerdown", this.rememberPress);
    this.canvas.addEventListener("pointerup", this.handlePointerDown);

    this.resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => this.resize());
    this.resizeObserver?.observe(canvas);
    this.resize();
  }

  start(): void {
    if (this.disposed) return;
    this.requestedAnimation = true;
    if (this.view.nodes.length > 0) this.loop.start();
  }

  stop(): void {
    this.requestedAnimation = false;
    this.loop.stop();
  }

  get renderCount(): number {
    return this.loop.renderCount;
  }

  setSignals(load:number,_voiceRms:number):void {
    const bounded=Number.isFinite(load)?Math.max(0,Math.min(1,load)):0;
    this.loop.setLoad(bounded);
    const scale=1+bounded*.006;
    if(Math.abs(this.graphRoot.scale.x-scale)>0.0001){this.graphRoot.scale.setScalar(scale);this.invalidateFrame();}
  }

  private invalidateFrame(): void {
    if (!this.disposed && this.requestedAnimation && this.view.nodes.length > 0) this.loop.start();
  }

  get currentView(): KnowledgeView {
    return this.view;
  }

  get canGoBack(): boolean { return this.drilldown.canGoBack; }

  replaceGraph(graph: KnowledgeGraph): void {
    if (this.disposed) return;
    const focus = this.view.focusId;
    this.graph = graph;
    const sorted = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id));
    this.layoutPositions();
    const ids = new Set(sorted.map(node => node.id));
    for (const id of this.positions.keys()) if (!ids.has(id)) this.positions.delete(id);
    this.view = this.drilldown.replaceGraph(graph);
    if (!focus || !ids.has(focus)) { this.desiredCamera.set(0, 0, 5.2); this.desiredLookAt.set(0, 0, 0); }
    this.rebuildGraph();
    if (focus && ids.has(focus)) this.focusCamera(focus);
    this.canvas.hidden = graph.nodes.length === 0;
    this.resize();
    if (this.requestedAnimation && graph.nodes.length > 0) this.loop.start();
    else this.loop.stop();
    this.notifyView();
  }

  diagnostics(): AnimationLoopDiagnostics { return this.loop.diagnostics(); }
  get navigationTargets(): { camera: number[]; lookAt: number[] } {
    return { camera: this.desiredCamera.toArray(), lookAt: this.desiredLookAt.toArray() };
  }

  clickNode(nodeId: string): KnowledgeView {
    if (this.disposed) return this.view;
    const node = this.graph.nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return this.view;
    this.view = this.drilldown.clickNode(nodeId);
    this.rebuildGraph();
    this.focusCamera(nodeId);
    this.onFocus({ node, view: this.view });
    return this.view;
  }

  expandOneHop(): KnowledgeView {
    if (this.disposed) return this.view;
    this.view = this.drilldown.expandOneHop();
    this.rebuildGraph();
    if (this.view.focusId) this.focusCamera(this.view.focusId);
    this.notifyView();
    return this.view;
  }

  reset(): KnowledgeView {
    if (this.disposed) return this.view;
    this.view = this.drilldown.reset();
    this.rebuildGraph();
    this.desiredCamera.set(0, 0, 5.2);
    this.desiredLookAt.set(0, 0, 0);
    this.invalidateFrame();
    this.notifyView();
    return this.view;
  }

  back(): KnowledgeView {
    if (this.disposed) return this.view;
    this.view = this.drilldown.back();
    this.rebuildGraph();
    if (this.view.focusId) this.focusCamera(this.view.focusId);
    else {
      this.desiredCamera.set(0, 0, 5.2);
      this.desiredLookAt.set(0, 0, 0);
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
    this.orbit.dispose();
    this.resizeObserver?.disconnect();
    disposeObject(this.graphRoot);
    this.scene.children
      .filter((child) => child !== this.graphRoot)
      .forEach((child) => disposeObject(child));
    this.renderer.dispose();
    this.labels.clear();
    this.labelBounds.length = 0;
    this.canvas.parentElement?.querySelector(".knowledge-node-labels")?.replaceChildren();
    this.canvas.parentElement?.querySelector("#knowledge-accessible-nodes")?.replaceChildren();
  }

  private layoutPositions():void {
    const sorted=[...this.graph.nodes].sort((a,b)=>a.id.localeCompare(b.id));
    const groups=sorted.filter(n=>n.category==='collection');
    if(!groups.length){sorted.forEach((n,i)=>this.positions.set(n.id,spherePoint(i,sorted.length,1.42)));return;}
    const root=groups.find(n=>n.label==='카카오톡')??groups[0];this.positions.set(root.id,new THREE.Vector3(0,.1,0));
    const children=groups.filter(n=>n!==root);children.forEach((n,i)=>{const angle=i*Math.PI*2/Math.max(1,children.length);this.positions.set(n.id,new THREE.Vector3(Math.cos(angle)*1.16,Math.sin(angle)*.78,Math.sin(angle+.7)*.28));});
    const parents=new Map(this.graph.edges.filter(e=>e.relation==='contains').map(e=>[e.target,e.source]));
    const members=new Map<string,KnowledgeNode[]>();
    for(const node of sorted){if(node.category==='collection')continue;const parent=parents.get(node.id)??root.id;const bucket=members.get(parent)??[];bucket.push(node);members.set(parent,bucket);}
    for(const [parent,nodes] of members){const center=this.positions.get(parent)??new THREE.Vector3();nodes.forEach((node,index)=>{
      const point=spherePoint(index,nodes.length,.68);point.y*=.85;point.z*=.65;point.add(center);this.positions.set(node.id,point);
    });}
  }

  private rebuildGraph(): void {
    while (this.graphRoot.children.length > 0) {
      const child = this.graphRoot.children.pop();
      if (child) disposeObject(child);
    }
    this.nodeMeshes.clear();
    this.labels.clear();
    this.labelBounds.length = 0;
    const labelLayer = this.canvas.parentElement?.querySelector(".knowledge-node-labels");
    labelLayer?.replaceChildren();
    const accessible = this.canvas.parentElement?.querySelector("#knowledge-accessible-nodes");
    accessible?.replaceChildren();
    const accent = cssColor(this.canvas, "--accent", "#dde7ef");
    const muted = cssColor(this.canvas, "--cosmos-link", "#7894b1");
    const focusId = this.view.focusId;

    for (const edge of this.view.edges) {
      const start = this.positions.get(edge.source);
      const end = this.positions.get(edge.target);
      if (!start || !end) continue;
      const geometry = new THREE.BufferGeometry().setFromPoints([start, end]);
      const touchesFocus = focusId !== null && (edge.source === focusId || edge.target === focusId);
      const material = new THREE.LineBasicMaterial({
        color: touchesFocus ? accent : muted,
        transparent: true,
        opacity: touchesFocus ? 0.68 : edge.relation==='contains' ? 0.24 : 0.42,
        depthWrite: false,
      });
      this.graphRoot.add(new THREE.Line(geometry, material));
    }

    for (const node of this.view.nodes) {
      const position = this.positions.get(node.id);
      if (!position) continue;
      const focused = node.id === focusId;
      const kind=node.category==='collection'?node.label:node.category;
      const token=/인물|사람|대화 상대|화자/.test(kind)?'--cosmos-person':/대화방/.test(kind)?'--cosmos-room':/주제/.test(kind)?'--cosmos-topic':'--accent';
      const color=cssColor(this.canvas,token,"#dde7ef");
      const radius = (node.category==='collection'?.095:.04) + (node.importance / 100) * 0.025 + (focused ? 0.02 : 0);
      const geometry = new THREE.SphereGeometry(radius, 18, 12);
      const material = new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: focused ? 0.28 : 0.12,
        roughness: 0.55,
        metalness: 0.12,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(position);
      mesh.userData.nodeId = node.id;
      this.nodeMeshes.set(node.id, mesh);
      this.graphRoot.add(mesh);
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
      if (labelLayer) {
        const label = document.createElement("span");
        label.className = "knowledge-node-label";
        label.textContent = node.label;
        label.title = node.label;
        label.dataset.focused = String(focused);
        labelLayer.append(label);
        this.labels.set(node.id, label);
        this.labelBounds.push({ width: 0, left: 0, top: 0, visible: false });
      }
    }
  }

  private focusCamera(nodeId: string): void {
    const position = this.positions.get(nodeId);
    if (!position) return;
    this.desiredLookAt.copy(position);
    this.desiredCamera.set(position.x * 0.58, position.y * 0.58, position.z + 2.55);
    this.invalidateFrame();
  }

  private readonly rememberPress=(event:PointerEvent):void=>{this.press.set(event.clientX,event.clientY);};
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

  private resize(): void {
    if (this.disposed) return;
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.floor(rect.width || 640));
    const height = Math.max(1, Math.floor(rect.height || 320));
    this.renderer.setSize(width, height, false);
    const top = this.layoutMode==='popover'?58:width < 650 ? 246 : 84;
    const bottom=this.layoutMode==='popover'?42:80;
    this.viewport = { x: 28, y: Math.min(top, height / 2), width: Math.max(1, width - 56), height: Math.max(1, height - Math.min(top, height / 2) - bottom) };
    this.renderer.setViewport(this.viewport.x, height - this.viewport.y - this.viewport.height, this.viewport.width, this.viewport.height);
    this.camera.aspect = this.viewport.width / this.viewport.height;
    this.camera.updateProjectionMatrix();
    for (const box of this.labelBounds) box.width = 0;
    this.invalidateFrame();
  }

  private render(dt: number): void {
    if (this.disposed) return;
    const alpha = 1 - Math.exp(-6.2 * Math.min(dt, 0.05));
    this.camera.position.lerp(this.desiredCamera, alpha);
    this.lookAt.lerp(this.desiredLookAt, alpha);
    this.camera.lookAt(this.lookAt);
    this.orbit.target.copy(this.lookAt);this.orbit.update();
    this.renderer.render(this.scene, this.camera);
    const { x, y, width, height } = this.viewport;
    let index = 0;
    for (const [id, label] of this.labels) {
      const box = this.labelBounds[index++];
      box.visible = false;
      const position = this.positions.get(id);
      if (!position) continue;
      this.projected.copy(position).project(this.camera);
      label.hidden = Math.abs(this.projected.x) > 1 || Math.abs(this.projected.y) > 1 || this.projected.z > 1 || this.projected.z < -1;
      if (label.hidden) continue;
      // Measure only after a rebuild/resize. Reuse at most 24 collision boxes.
      if (!box.width) box.width = label.offsetWidth || Math.min(156, label.textContent!.length * 11 + 12);
      box.left = Math.max(x, Math.min(x + width - box.width, x + (this.projected.x + 1) * width / 2 - box.width / 2));
      box.top = Math.min(y + height - 24, y + (1 - this.projected.y) * height / 2 + 10);
      for (let previous = 0; previous < index - 1; previous++) {
        const other = this.labelBounds[previous];
        if (other.visible && box.left < other.left + other.width + 5 && box.left + box.width + 5 > other.left && box.top < other.top + 24 && box.top + 24 > other.top) {
          label.hidden = true;
          break;
        }
      }
      box.visible = !label.hidden;
      if (box.visible) label.style.transform = `translate(${box.left}px, ${box.top}px)`;
    }
    // Static sky and settled knowledge do not need another GPU frame. Real
    // navigation, source replacement, resize and load changes invalidate it.
    if (!this.orbitActive && this.camera.position.distanceToSquared(this.desiredCamera) < 0.000001 && this.lookAt.distanceToSquared(this.desiredLookAt) < 0.000001) this.loop.stop();
  }
}
