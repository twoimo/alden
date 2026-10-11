// @vitest-environment happy-dom
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import {
  compareOverviewLabelPriority,
  placeOverviewLabel,
  type LabelBox,
  type LabelViewport,
} from "../knowledge/label-placement";
import { KnowledgeHologram } from "../knowledge/hologram";

// Independent, wholly synthetic label geometry. This adapter reports the
// browser's CURRENT measured dimensions, including changing font and view
// styles. No WebGL, real records, graph backend, or native application is used.
type NodeCase = { id: string; text: string; width: number; height?: number };
type SyntheticView = {
  focusId: string | null;
  hops: number;
  nodes: Array<{ id: string; label: string }>;
  edges: [];
};
type RenderHarness = {
  render(dt: number): void;
  refreshOverviewLabels(): void;
  labels: Map<string, HTMLSpanElement>;
  labelBounds: LabelBox[];
  hoveredId: string | null;
  view: SyntheticView;
  camera: THREE.PerspectiveCamera;
  desiredCamera: THREE.Vector3;
};
type MeasuredRect = { left: number; right: number; top: number; bottom: number };

function overlaps(first: MeasuredRect, second: MeasuredRect): boolean {
  return first.left < second.right && first.right > second.left &&
    first.top < second.bottom && first.bottom > second.top;
}

function mount(
  cases: NodeCase[],
  options: { width?: number; height?: number; localWidthAddition?: number } = {},
) {
  const shell = document.createElement("section");
  const layer = document.createElement("div");
  layer.className = "knowledge-node-labels";
  const keyboard = document.createElement("div");
  keyboard.id = "knowledge-accessible-nodes";
  const canvas = document.createElement("canvas");
  shell.append(layer, keyboard, canvas);
  document.body.append(shell);

  const specs = new Map(cases.map((node) => [node.id, node]));
  const spans = new Map<string, HTMLSpanElement>();
  const reads = new Map<string, number>();
  function measuredWidth(id: string): number {
    const span = spans.get(id)!;
    const spec = specs.get(id)!;
    const scale = Number.parseFloat(span.style.fontSize || "12") / 12;
    const spacing = Number.parseFloat(span.style.letterSpacing || "0") || 0;
    const local = span.dataset.view === "local" ? options.localWidthAddition ?? 0 : 0;
    return Math.round((spec.width + local) * scale + (span.textContent?.length ?? 0) * spacing);
  }
  function measuredHeight(id: string): number {
    const span = spans.get(id)!;
    return (specs.get(id)!.height ?? 22) * (Number.parseFloat(span.style.fontSize || "12") / 12);
  }

  for (const spec of cases) {
    const span = document.createElement("span");
    span.className = "knowledge-node-label";
    span.textContent = spec.text;
    span.dataset.view = "overview";
    span.dataset.focused = "false";
    span.style.fontSize = "12px";
    spans.set(spec.id, span);
    Object.defineProperty(span, "offsetWidth", {
      configurable: true,
      get: () => {
        reads.set(spec.id, (reads.get(spec.id) ?? 0) + 1);
        return measuredWidth(spec.id);
      },
    });
    Object.defineProperty(span, "offsetHeight", {
      configurable: true,
      get: () => measuredHeight(spec.id),
    });
    layer.append(span);
    const button = document.createElement("button");
    button.className = "knowledge-a11y-node";
    button.textContent = spec.text;
    keyboard.append(button);
  }

  const viewport: LabelViewport = {
    x: 28, y: 12, width: options.width ?? 450, height: options.height ?? 180,
  };
  const camera = new THREE.PerspectiveCamera(38, viewport.width / viewport.height, 0.1, 60);
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  const positions = new Map(cases.map((node) => [node.id, new THREE.Vector3(0, 0, 0)]));
  const graphRoot = new THREE.Group();
  graphRoot.updateMatrixWorld(true);
  const bridge = () => ({
    moving: false,
    mesh: { visible: false, geometry: { instanceCount: 0 } },
    update: vi.fn(), set: vi.fn(),
  });
  const view: SyntheticView = {
    focusId: null,
    hops: 0,
    nodes: cases.map((node) => ({ id: node.id, label: node.text })),
    edges: [],
  };
  const fake = Object.create(KnowledgeHologram.prototype) as RenderHarness;
  Object.assign(fake, {
    disposed: false, view, hoveredId: null,
    canvas, camera, graphRoot, projected: new THREE.Vector3(),
    desiredCamera: camera.position.clone(),
    desiredLookAt: new THREE.Vector3(), lookAt: new THREE.Vector3(),
    orbit: { target: new THREE.Vector3(), update: vi.fn() },
    viewport, positions, displayedPositions: positions,
    plasticity: { moving: false, setDisplayScale: vi.fn(), forEachPoint: vi.fn(), advance: vi.fn() },
    positionTransition: { moving: false, advance: vi.fn() },
    constellationNodes: {
      radius: vi.fn(() => 0.1), setMinimumRadius: vi.fn(),
      update: vi.fn(), advanceActivity: vi.fn(() => false), highlight: vi.fn(),
    },
    synapses: bridge(), overviewSynapses: bridge(),
    nebulae: { moving: false, update: vi.fn() },
    nodeMeshes: new Map(), orbMeshes: new Map(), neuronMeshes: new Map(),
    neuronPorts: new Map(), anchors: new Map(),
    globalLabels: new Set(cases.map((node) => node.id)),
    globalLabelRanks: new Map(cases.map((node, index) => [node.id, index])),
    neuronDetail: false, labels: spans,
    labelBounds: cases.map(() => ({ width: 0, left: 0, top: 0, visible: false })),
    excludedLabelBounds: [],
    reducedMotion: false, motion: { matches: true },
    paused: false, orbitActive: false,
    latestVoice: null, voiceEnvelope: { needsFrame: false, displayedRms: 0 },
    loop: { setInteractive: vi.fn(), setVoiceActive: vi.fn(), stop: vi.fn() },
    renderer: { render: vi.fn() },
  });

  function frame(): void { fake.render(1 / 60); }
  function box(id: string): LabelBox {
    const index = [...fake.labels.keys()].indexOf(id);
    if (index < 0) throw new Error("Synthetic label missing: " + id);
    return fake.labelBounds[index];
  }
  function rect(id: string): MeasuredRect {
    const bounds = box(id);
    return {
      left: bounds.left, top: bounds.top,
      right: bounds.left + measuredWidth(id),
      bottom: bounds.top + measuredHeight(id),
    };
  }
  function setDepth(hops: number): void {
    view.hops = hops;
    camera.position.set(0, 0, hops > 0 ? 1.8 : 5);
    fake.desiredCamera.copy(camera.position);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld(true);
  }

  fake.refreshOverviewLabels();
  frame();
  return { shell, keyboard, spans, reads, fake, frame, box, rect, measuredWidth, setDepth };
}

describe("independent overview label geometry invariants", () => {
  it("uses measured text widths for differently sized labels and prevents visual rectangle overlap", () => {
    const fx = mount([
      { id: "synthetic-short", text: "A", width: 84 },
      { id: "synthetic-long", text: "Very different label length", width: 148 },
    ]);
    try {
      expect(fx.box("synthetic-short").width).toBe(fx.measuredWidth("synthetic-short"));
      expect(fx.box("synthetic-long").width).toBe(fx.measuredWidth("synthetic-long"));
      expect(fx.box("synthetic-short").visible).toBe(true);
      expect(fx.box("synthetic-long").visible).toBe(true);
      expect(overlaps(fx.rect("synthetic-short"), fx.rect("synthetic-long"))).toBe(false);
    } finally { fx.shell.remove(); }
  });

  it("never exposes a clipped label that cannot fit within the viewport width", () => {
    const viewport = { x: 19, y: 9, width: 100, height: 130 };
    const label: LabelBox = { width: 148, left: 35, top: 70, visible: false };
    placeOverviewLabel(label, viewport, [], [], 0);
    expect(label.visible).toBe(false);
  });

  it("clamps near-edge anchors inside a viewport with a nonzero origin", () => {
    const viewport = { x: 29, y: 17, width: 240, height: 170 };
    for (const [left, top] of [[-500, -500], [500, 500], [-500, 500], [500, -500]]) {
      const label: LabelBox = { width: 96, left, top, visible: false };
      placeOverviewLabel(label, viewport, [], [], 0);
      expect(label.visible).toBe(true);
      expect(label.left).toBeGreaterThanOrEqual(viewport.x);
      expect(label.left + label.width).toBeLessThanOrEqual(viewport.x + viewport.width);
      expect(label.top).toBeGreaterThanOrEqual(viewport.y);
      expect(label.top).toBeLessThan(viewport.y + viewport.height);
    }
  });

  it("rejects non-finite anchors and non-positive measured widths", () => {
    const viewport = { x: 0, y: 0, width: 210, height: 150 };
    for (const label of [
      { width: 0, left: 12, top: 18, visible: true },
      { width: -25, left: 12, top: 18, visible: true },
      { width: 90, left: Number.NaN, top: 18, visible: true },
      { width: 90, left: 12, top: Number.POSITIVE_INFINITY, visible: true },
    ]) {
      placeOverviewLabel(label, viewport, [], [], 0);
      expect(label.visible).toBe(false);
    }
  });

  it("prioritizes focus, then hover, then representative order over insertion order", () => {
    const candidates = ["synthetic-low", "synthetic-hover", "synthetic-high", "synthetic-focus"];
    const representatives = ["synthetic-high", "synthetic-low"];
    const ranks = new Map(representatives.map((id, index) => [id, index]));
    const ordered = [...candidates].sort((first, second) =>
      compareOverviewLabelPriority(first, second, "synthetic-focus", "synthetic-hover", ranks));
    expect(ordered).toEqual(["synthetic-focus", "synthetic-hover", ...representatives]);

    const fx = mount(candidates.map((id) => ({ id, text: id, width: 90 })), { height: 24 });
    try {
      fx.fake.view.focusId = "synthetic-focus";
      fx.fake.hoveredId = "synthetic-hover";
      fx.fake.refreshOverviewLabels();
      fx.frame();
      expect([...fx.fake.labels.keys()].slice(0, 2)).toEqual(["synthetic-focus", "synthetic-hover"]);
      expect(fx.box("synthetic-focus").visible).toBe(true);
      expect(fx.keyboard.querySelectorAll("button")).toHaveLength(candidates.length);
    } finally { fx.shell.remove(); }
  });

  it("preserves distinct overview and local placement while retaining keyboard access", () => {
    const fx = mount([
      { id: "synthetic-one", text: "Nearby first", width: 116 },
      { id: "synthetic-two", text: "Nearby second", width: 116 },
    ]);
    try {
      expect(fx.box("synthetic-one").visible).toBe(true);
      expect(fx.box("synthetic-two").visible).toBe(true);
      expect(overlaps(fx.rect("synthetic-one"), fx.rect("synthetic-two"))).toBe(false);

      fx.setDepth(1);
      fx.frame();
      expect([...fx.spans.values()].map((span) => span.dataset.view)).toEqual(["local", "local"]);
      expect([fx.box("synthetic-one"), fx.box("synthetic-two")].filter((b) => b.visible)).toHaveLength(1);
      expect(fx.keyboard.querySelectorAll("button")).toHaveLength(2);

      fx.setDepth(0);
      fx.frame();
      expect([...fx.spans.values()].map((span) => span.dataset.view)).toEqual(["overview", "overview"]);
      expect(fx.box("synthetic-one").visible).toBe(true);
      expect(fx.box("synthetic-two").visible).toBe(true);
      expect(fx.keyboard.querySelectorAll("button")).toHaveLength(2);
    } finally { fx.shell.remove(); }
  });

  it("reuses the width measurements on unchanged render frames", () => {
    const fx = mount([
      { id: "synthetic-red", text: "Red", width: 84 },
      { id: "synthetic-blue", text: "Blue", width: 140 },
    ]);
    try {
      const initialReads = [...fx.reads];
      expect(initialReads.map(([, count]) => count)).toEqual([1, 1]);
      fx.frame();
      fx.frame();
      expect([...fx.reads]).toEqual(initialReads);
    } finally { fx.shell.remove(); }
  });

  it("re-measures after a live font-size change instead of retaining a stale collision box", () => {
    const fx = mount([{ id: "synthetic-type", text: "Dynamic typography", width: 112 }]);
    try {
      const before = fx.box("synthetic-type").width;
      fx.spans.get("synthetic-type")!.style.fontSize = "20px";
      expect(fx.measuredWidth("synthetic-type")).toBeGreaterThan(before);
      fx.frame();
      expect(fx.box("synthetic-type").width).toBe(fx.measuredWidth("synthetic-type"));
    } finally { fx.shell.remove(); }
  });

  it("re-measures when letter spacing changes the rendered width", () => {
    const fx = mount([{ id: "synthetic-spacing", text: "Wide track", width: 98 }]);
    try {
      const before = fx.box("synthetic-spacing").width;
      fx.spans.get("synthetic-spacing")!.style.letterSpacing = "2px";
      expect(fx.measuredWidth("synthetic-spacing")).toBeGreaterThan(before);
      fx.frame();
      expect(fx.box("synthetic-spacing").width).toBe(fx.measuredWidth("synthetic-spacing"));
    } finally { fx.shell.remove(); }
  });

  it("re-measures current DOM geometry across overview -> local -> overview style transitions", () => {
    // A fake browser's offsetWidth includes local-mode horizontal padding and
    // border; the overview CSS removes that horizontal chrome. No CSS layout
    // or WebGL implementation is mocked into the placement algorithm itself.
    const id = "synthetic-switch";
    const fx = mount([{ id, text: "Contextual label", width: 104 }], {
      localWidthAddition: 2 * (6 + 1),
    });
    try {
      const label = fx.spans.get(id)!;
      expect(label.dataset.view).toBe("overview");
      const overviewWidth = fx.measuredWidth(id);
      expect(fx.box(id).width).toBe(overviewWidth);

      fx.setDepth(1);
      fx.frame();
      expect(label.dataset.view).toBe("local");
      const localWidth = fx.measuredWidth(id);
      expect(localWidth).toBeGreaterThan(overviewWidth);
      expect(fx.box(id).width).toBe(localWidth);
      expect(fx.box(id).left + fx.box(id).width).toBe(fx.rect(id).right);

      fx.setDepth(0);
      fx.frame();
      expect(label.dataset.view).toBe("overview");
      expect(fx.measuredWidth(id)).toBe(overviewWidth);
      expect(fx.box(id).width).toBe(overviewWidth);
    } finally { fx.shell.remove(); }
  });

  it("avoids overlap using actual tall label rectangles, including changed line height", () => {
    const fx = mount([
      { id: "synthetic-tall-a", text: "Tall text A", width: 125, height: 44 },
      { id: "synthetic-tall-b", text: "Tall text B", width: 125, height: 44 },
    ], { height: 210 });
    try {
      const visible = ["synthetic-tall-a", "synthetic-tall-b"].filter((id) => fx.box(id).visible);
      for (let i = 0; i < visible.length; i++)
        for (let j = i + 1; j < visible.length; j++)
          expect(overlaps(fx.rect(visible[i]), fx.rect(visible[j]))).toBe(false);
    } finally { fx.shell.remove(); }
  });

  it("short-circuits a viewport-wide exclusion before scanning prior labels", () => {
    const viewport = { x: 25, y: 8, width: 160, height: 90 };
    const forbidden = [{
      left: viewport.x, top: viewport.y,
      right: viewport.x + viewport.width, bottom: viewport.y + viewport.height,
    }];
    const prior = new Proxy<LabelBox[]>([], {
      get: () => { throw new Error("Occupied/excluded layout must not scan earlier labels"); },
    });
    const label: LabelBox = { width: 88, left: 60, top: 30, visible: false };
    placeOverviewLabel(label, viewport, forbidden, prior, 10000);
    expect(label.visible).toBe(false);
  });

  it("bounds dense collision work when the first earlier label blocks every available slot", () => {
    const viewport = { x: 25, y: 8, width: 160, height: 24 };
    const covering: LabelBox = { width: viewport.width, left: viewport.x, top: viewport.y, visible: true };
    function probes(candidateCount: number) {
      const readIndexes: number[] = [];
      const prior = new Proxy(Array.from({ length: candidateCount }, (_, index) =>
        index === 0 ? covering : { width: 1, left: -100, top: -100, visible: false }), {
        get(target, property, receiver) {
          if (typeof property === "string" && /^\d+$/.test(property))
            readIndexes.push(Number(property));
          return Reflect.get(target, property, receiver);
        },
      });
      const label: LabelBox = { width: 90, left: 55, top: viewport.y, visible: false };
      placeOverviewLabel(label, viewport, [], prior, candidateCount);
      return { label, readIndexes };
    }
    const one = probes(1);
    const many = probes(4000);
    expect(one.label.visible).toBe(false);
    expect(many.label.visible).toBe(false);
    expect(one.readIndexes.length).toBeGreaterThan(0);
    expect(many.readIndexes).toEqual(one.readIndexes);
    expect(many.readIndexes.every((index) => index === 0)).toBe(true);
  });
});
