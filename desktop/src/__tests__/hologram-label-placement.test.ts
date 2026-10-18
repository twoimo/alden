// @vitest-environment happy-dom
import * as THREE from "three";
import { PositionTransition } from "../knowledge/position-transition";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeHologram } from "../knowledge/hologram";
import { parseKnowledgeGraph } from '../knowledge/graph-model';

// Deliberately uses synthetic geometry. It exercises the native render label
// pipeline without a WebGL context, real accounts, or a physical window.
type Box = { width: number; left: number; top: number; visible: boolean };
type Bounds = { left: number; top: number; right: number; bottom: number };
type NodeSpec = { id: string; x?: number; y?: number; z?: number };
type TestHologram = {
  render(dt: number): void;
  refreshOverviewLabels(): void;
  labelBounds: Box[];
  labels: Map<string, HTMLSpanElement>;
};

function setup(
  specs: NodeSpec[],
  opts: {
    focus?: string | null; hover?: string | null; width?: number; height?: number;
    globalIds?: string[]; ranks?: string[]; exclusions?: Bounds[];
    zoom?: number;
  } = {},
) {
  const shell = document.createElement("div");
  const layer = document.createElement("div");
  layer.className = "knowledge-node-labels";
  const accessible = document.createElement("div");
  accessible.id = "knowledge-accessible-nodes";
  const canvas = document.createElement("canvas");
  shell.append(layer, accessible, canvas);
  document.body.append(shell);

  const labels = new Map<string, HTMLSpanElement>();
  const widthReads = new Map<string, number>();
  for (const node of specs) {
    const label = document.createElement("span");
    label.textContent = "Label " + node.id.padEnd(7, "x");
    Object.defineProperty(label, "offsetWidth", { get: () => {
      widthReads.set(node.id, (widthReads.get(node.id) ?? 0) + 1);
      return 132;
    } });
    label.dataset.focused = String(node.id === opts.focus);
    layer.append(label);
    labels.set(node.id, label);
    const button = document.createElement("button");
    button.className = "knowledge-a11y-node";
    button.textContent = label.textContent;
    accessible.append(button);
  }

  const positions = new Map(specs.map((n) => [n.id, new THREE.Vector3(n.x ?? 0, n.y ?? 0, n.z ?? 0)]));
  const camera = new THREE.PerspectiveCamera(38, 1.4, .1, 60);
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  const emptyBridge = () => ({
    moving: false, mesh: { visible: false, geometry: { instanceCount: 0 } }, update: vi.fn(),
  });
  const loop = { setInteractive: vi.fn(), setVoiceActive: vi.fn(), stop: vi.fn() };
  const graphRoot = new THREE.Group();
  graphRoot.updateMatrixWorld(true);

  const fake = Object.create(KnowledgeHologram.prototype) as TestHologram;
  Object.assign(fake, {
    displayOffsets: new Map(), displayTransition: new PositionTransition(),
    displayRelaxationPending: false,
    semanticLayout: { settled: false },

    provisionalOverviewPositions: new Set<string>(),
    disposed: false,
    view: { hops: 0, focusId: opts.focus ?? null, nodes: specs.map((node) => ({ id: node.id, label: node.id })) },
    hoveredId: opts.hover ?? null,
    canvas, camera, projected: new THREE.Vector3(), graphRoot,
    desiredCamera: camera.position.clone(), desiredLookAt: new THREE.Vector3(),
    lookAt: new THREE.Vector3(), orbit: { target: new THREE.Vector3(), update: vi.fn() },
    viewport: { x: 28, y: 12, width: opts.width ?? 224, height: opts.height ?? 160 },
    positions, displayedPositions: positions,
    plasticity: { moving: false, setDisplayScale: vi.fn(), forEachPoint: vi.fn(), advance: vi.fn() },
    positionTransition: { moving: false, advance: vi.fn() },
    constellationNodes: { radius: vi.fn(() => .1), setMinimumRadius: vi.fn(), update: vi.fn(), advanceActivity: vi.fn(() => false) },
    synapses: emptyBridge(), overviewSynapses: emptyBridge(),
    nebulae: { moving: false }, nodeMeshes: new Map(), orbMeshes: new Map(), neuronMeshes: new Map(),
    globalLabels: new Set(opts.globalIds ?? specs.map((node) => node.id)),
    globalLabelRanks: new Map((opts.ranks ?? specs.map((n) => n.id)).map((id, index) => [id, index])),
    neuronDetail: false, labels, labelBounds: specs.map(() => ({ width: 0, left: 0, top: 0, visible: false })),
    excludedLabelBounds: opts.exclusions ?? [],
    reducedMotion: false, motion: { matches: true }, paused: false, orbitActive: false,
    latestVoice: null, voiceEnvelope: { needsFrame: false, displayedRms: 0 },
    loop, renderer: { render: vi.fn() },
  });

  if (opts.zoom) {
    camera.position.z = 5 / opts.zoom;
    Object.assign(fake, {desiredCamera:camera.position.clone(), labelFitDistance:5,
      labelZoom:1, zoomLabels:new Set(),
      zoomLabelCandidates:parseKnowledgeGraph({nodes:specs.map(node=>({id:node.id,
        label:'실제 자료 ' + node.id, category:opts.globalIds?.includes(node.id)?'topic':'document'})),edges:[]}).nodes});
  }

  fake.refreshOverviewLabels();
  fake.render(1 / 60);
  const boxes = new Map([...fake.labels].map(([id], index) => [id, fake.labelBounds[index]]));
  return { shell, fake, boxes, labels, accessible, widthReads };
}

describe("overview labels in the native hologram projection loop", () => {
  it('reveals saved titles only after zooming in, with readable topic hierarchy and bounded boxes', () => {
    const specs=Array.from({length:20},(_,i)=>({id:'n'+i,x:(i%5-2)*.25,y:(Math.floor(i/5)-1.5)*.25}));
    const topics=specs.slice(0,8).map(n=>n.id);
    for(const zoom of [.55,1,1.7,2.5]) {
      const r=setup(specs,{globalIds:topics,ranks:specs.map(n=>n.id),zoom,width:1000,height:600});
      try {
        const labels=[...r.fake.labels.values()];
        expect(labels.length).toBeLessThanOrEqual(zoom<.7?4:zoom<1.4?8:24);
        const titles=labels.filter(l=>l.dataset.role==='title');
        expect(titles.length>0).toBe(zoom>1.4);
        for(const label of labels) {
          expect(parseFloat(label.style.fontSize)).toBeGreaterThanOrEqual(11.5);
          if(label.dataset.role==='topic') expect(parseFloat(label.style.fontSize)).toBeGreaterThanOrEqual(12);
        }
        const boxes=r.fake.labelBounds.filter(b=>b.visible);
        for(let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) {
          const a=boxes[i],b=boxes[j];
          expect(a.left<b.left+b.width&&a.left+a.width>b.left&&a.top<b.top+24&&a.top+24>b.top).toBe(false);
        }
        expect(r.accessible.querySelectorAll('button')).toHaveLength(20);
      } finally {r.shell.remove();}
    }
  });
  it("promotes selected labels ahead of earlier general candidates", () => {
    const r = setup([{ id: "ordinary" }, { id: "selected" }], {
      focus: "selected", ranks: ["ordinary", "selected"], height: 24,
    });
    try {
      expect(r.boxes.get("selected")?.visible).toBe(true);
      expect(r.boxes.get("ordinary")?.visible).toBe(false);
      expect(r.labels.get("selected")?.hidden).toBe(false);
      expect(r.accessible.querySelectorAll("button")).toHaveLength(2);
    } finally { r.shell.remove(); }
  });

  it("promotes the hover label ahead of ordinary labels even if appended last", () => {
    const r = setup([{ id: "ordinary" }, { id: "hover" }], { hover: "hover", height: 24 });
    try {
      expect(r.boxes.get("hover")?.visible).toBe(true);
      expect(r.boxes.get("ordinary")?.visible).toBe(false);
      expect(r.labels.get("hover")?.hidden).toBe(false);
    } finally { r.shell.remove(); }
  });

  it("uses selected, hovered, and representative ranks in that order on identical anchors", () => {
    const r = setup([{ id: "low" }, { id: "hover" }, { id: "high" }, { id: "focus" }], {
      focus: "focus", hover: "hover", ranks: ["high", "low"],
    });
    try {
      expect([...r.fake.labels.keys()]).toEqual(["focus", "hover", "high", "low"]);
      expect(r.boxes.get("focus")?.visible).toBe(true);
      expect(r.boxes.get("hover")?.visible).toBe(true);
    } finally { r.shell.remove(); }
  });

  it("uses radius-derived representative rank instead of graph insertion order", () => {
    const r = setup([{ id: "low" }, { id: "high" }], {
      ranks: ["high", "low"], height: 24,
    });
    try {
      expect([...r.fake.labels.keys()]).toEqual(["high", "low"]);
      expect(r.boxes.get("high")?.visible).toBe(true);
      expect(r.boxes.get("low")?.visible).toBe(false);
    } finally { r.shell.remove(); }
  });

  it("spreads crowded central labels inside a narrow viewport without overlapping", () => {
    const r = setup(Array.from({ length: 7 }, (_, n) => ({ id: "n" + n })), {
      width: 180, height: 180,
    });
    try {
      const visible = [...r.boxes.values()].filter((box) => box.visible);
      expect(visible.length).toBeGreaterThanOrEqual(3);
      for (const box of visible) {
        expect(box.left).toBeGreaterThanOrEqual(28);
        expect(box.left + box.width).toBeLessThanOrEqual(208);
        expect(box.top).toBeGreaterThanOrEqual(12);
        expect(box.top + 24).toBeLessThanOrEqual(192);
      }
      for (let i = 0; i < visible.length; i++)
        for (let j = i + 1; j < visible.length; j++) {
          const a = visible[i], b = visible[j];
          expect(
            a.left < b.left + b.width + 7 && a.left + a.width + 7 > b.left &&
            a.top < b.top + 24 + 7 && a.top + 24 + 7 > b.top,
          ).toBe(false);
        }
    } finally { r.shell.remove(); }
  });

  it("avoids cached exclusion bounds by using a bounded adjacent slot", () => {
    const exclusion = { left: 28, right: 260, top: 95, bottom: 122 };
    const r = setup([{ id: "selected" }], {
      focus: "selected", width: 232, height: 160, exclusions: [exclusion],
    });
    try {
      const label = r.boxes.get("selected")!;
      expect(label.visible).toBe(true);
      expect(label.top + 24 <= exclusion.top || label.top >= exclusion.bottom).toBe(true);
    } finally { r.shell.remove(); }
  });

  it("reuses cached DOM widths across frames without extra measurements", () => {
    const r = setup([{ id: "first" }, { id: "second" }]);
    try {
      const firstFrameReads = [...r.widthReads.values()];
      expect(firstFrameReads).toEqual([1, 1]);
      r.fake.render(1 / 60);
      expect([...r.widthReads.values()]).toEqual(firstFrameReads);
    } finally { r.shell.remove(); }
  });

  it("continues to hide out-of-frustum visual labels while keeping keyboard buttons", () => {
    const r = setup([{ id: "front" }, { id: "behind", z: 6 }], { focus: "behind" });
    try {
      expect(r.boxes.get("behind")?.visible).toBe(false);
      expect(r.labels.get("behind")?.hidden).toBe(true);
      expect(r.accessible.querySelectorAll("button")).toHaveLength(2);
    } finally { r.shell.remove(); }
  });
});
