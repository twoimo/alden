import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { createCosmosBackdrop } from "../knowledge/cosmos";

function visitResources(
  root: THREE.Object3D,
  visit: (resource: THREE.BufferGeometry | THREE.Material) => void,
): void {
  root.traverse((object) => {
    if (object instanceof THREE.Points || object instanceof THREE.Line || object instanceof THREE.Mesh) {
      visit(object.geometry);
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        visit(material);
      }
    }
  });
}

function dispose(root: THREE.Object3D): void {
  visitResources(root, (resource) => resource.dispose());
}

function getStars(sky: THREE.Group): THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial> {
  expect(sky).toBeInstanceOf(THREE.Group);
  expect(sky.children).toHaveLength(1);
  const stars = sky.children[0];
  expect(stars).toBeInstanceOf(THREE.Points);
  const points = stars as THREE.Points;
  expect(points.geometry).toBeInstanceOf(THREE.BufferGeometry);
  expect(points.material).toBeInstanceOf(THREE.ShaderMaterial);
  return points as THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>;
}

function withoutComments(shader: string): string {
  return shader.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, " ");
}

describe("createCosmosBackdrop", () => {
  it("owns one point cloud and releases its two resources through ordinary traversal", () => {
    const sky = createCosmosBackdrop();
    const events: ReturnType<typeof vi.fn>[] = [];
    try {
      const stars = getStars(sky);
      const objects: THREE.Object3D[] = [];
      sky.traverse((object) => objects.push(object));
      expect(objects).toEqual([sky, stars]);

      const resources: (THREE.BufferGeometry | THREE.Material)[] = [];
      visitResources(sky, (resource) => resources.push(resource));
      expect(resources).toEqual([stars.geometry, stars.material]);
      expect(new Set(resources).size).toBe(2);
      expect(Object.keys(stars.material.uniforms)).toEqual([]);
      expect(stars.material.uniformsGroups).toEqual([]);
      expect(Object.values(stars.material).some((value) => value instanceof THREE.Texture)).toBe(false);

      visitResources(sky, (resource) => {
        const onDispose = vi.fn();
        resource.addEventListener("dispose", onDispose);
        events.push(onDispose);
      });
      expect(events).toHaveLength(2);
      for (const event of events) expect(event).not.toHaveBeenCalled();
    } finally {
      dispose(sky);
    }
    for (const event of events) expect(event).toHaveBeenCalledTimes(1);
  });

  it("stores finite deep-shell stars with bounded sizes, muted blue/silver colors and sparse warmth", () => {
    const sky = createCosmosBackdrop();
    try {
      const { geometry } = getStars(sky);
      const position = geometry.getAttribute("position");
      const color = geometry.getAttribute("color");
      const size = geometry.getAttribute("size");
      expect(position.count).toBeGreaterThan(0);
      expect(position.count).toBeLessThanOrEqual(1536);
      for (const [attribute, itemSize] of [[position, 3], [color, 3], [size, 1]] as const) {
        expect(attribute).toBeInstanceOf(THREE.BufferAttribute);
        expect(attribute.array).toBeInstanceOf(Float32Array);
        expect(attribute.itemSize).toBe(itemSize);
        expect(attribute.count).toBe(position.count);
        expect(attribute.array.length).toBe(position.count * itemSize);
        expect(Array.from(attribute.array).every(Number.isFinite)).toBe(true);
      }

      const point = new THREE.Vector3();
      for (let index = 0; index < position.count; index++) {
        const radius = point.fromBufferAttribute(position, index).length();
        // Float32 storage can round a point just beyond the mathematical shell.
        expect(radius).toBeGreaterThanOrEqual(18 - 1e-5);
        expect(radius).toBeLessThanOrEqual(30 + 1e-5);
        expect(size.getX(index)).toBeGreaterThanOrEqual(1.5 - 1e-6);
        expect(size.getX(index)).toBeLessThanOrEqual(3.1 + 1e-6);
      }
      let blue = 0;
      let silver = 0;
      let warm = 0;
      for (let index = 0; index < color.count; index++) {
        const [r, g, b] = [color.getX(index), color.getY(index), color.getZ(index)];
        for (const channel of [r, g, b]) {
          expect(channel).toBeGreaterThan(0);
          expect(channel).toBeLessThanOrEqual(1);
        }
        if (r > b) {
          warm++;
          expect(r).toBeGreaterThanOrEqual(g);
          expect(g).toBeGreaterThanOrEqual(b);
        } else {
          expect(b).toBeGreaterThanOrEqual(g);
          expect(g).toBeGreaterThanOrEqual(r);
          // Compare channel ratios so brightness variation does not change hue classification.
          expect(r / b).toBeGreaterThanOrEqual(0.6);
          if (r / b >= 0.8) silver++;
          else blue++;
        }
      }
      expect(blue).toBeGreaterThan(0);
      expect(silver).toBeGreaterThan(0);
      expect(warm).toBeGreaterThan(0);
      expect(warm / color.count).toBeLessThan(0.1);
    } finally {
      dispose(sky);
    }
  });

  it("reproduces every buffer while keeping creations independently owned", () => {
    const first = createCosmosBackdrop();
    let second: THREE.Group | undefined;
    try {
      second = createCosmosBackdrop();
      const a = getStars(first);
      const b = getStars(second);
      expect(a).not.toBe(b);
      expect(a.geometry).not.toBe(b.geometry);
      expect(a.material).not.toBe(b.material);
      expect(Object.keys(a.geometry.attributes).sort()).toEqual(Object.keys(b.geometry.attributes).sort());
      for (const name of Object.keys(a.geometry.attributes)) {
        const left = a.geometry.getAttribute(name);
        const right = b.geometry.getAttribute(name);
        expect(left).not.toBe(right);
        expect(left.array.buffer).not.toBe(right.array.buffer);
        expect(left.itemSize).toBe(right.itemSize);
        expect(left.array).toEqual(right.array);
      }

      const originalX = b.geometry.getAttribute("position").getX(0);
      a.geometry.getAttribute("position").setX(0, originalX + 1);
      expect(b.geometry.getAttribute("position").getX(0)).toBe(originalX);
    } finally {
      dispose(first);
      if (second) dispose(second);
    }
  });

  it("remains static and renders soft round points behind foreground graph geometry", () => {
    const sky = createCosmosBackdrop();
    try {
      const stars = getStars(sky);
      const { material } = stars;
      expect(sky.renderOrder).toBeLessThan(0);
      expect(stars.renderOrder).toBeLessThan(0);
      expect(stars.frustumCulled).toBe(false);
      expect(material.transparent).toBe(true);
      expect(material.blending).toBe(THREE.NormalBlending);
      expect(material.depthWrite).toBe(false);
      expect(material.depthTest).toBe(true);
      sky.traverse((object) => {
        expect(object.animations).toEqual([]);
        expect(object.onBeforeRender).toBe(THREE.Object3D.prototype.onBeforeRender);
        expect(object.onAfterRender).toBe(THREE.Object3D.prototype.onAfterRender);
        expect(object).not.toHaveProperty("update");
        expect(Object.values(object.userData).some((value) => typeof value === "function")).toBe(false);
      });
      for (const attribute of Object.values(stars.geometry.attributes)) {
        expect((attribute as THREE.BufferAttribute).usage).toBe(THREE.StaticDrawUsage);
      }

      // Check shader operations without depending on indentation, comments or whole-source snapshots.
      const vertex = withoutComments(material.vertexShader);
      const depth = vertex.match(/\bgl_Position\s*\.\s*z\s*=\s*gl_Position\s*\.\s*w(?:\s*\*\s*(\d*\.?\d+(?:[eE][+-]?\d+)?))?\s*;/);
      expect(depth).not.toBeNull();
      const clipDepth = Number(depth?.[1] ?? 1);
      expect(clipDepth).toBeGreaterThanOrEqual(0.9999);
      expect(clipDepth).toBeLessThanOrEqual(1);
      expect(vertex).toMatch(/\bgl_PointSize\s*=\s*size\s*;/);
      const fragment = withoutComments(material.fragmentShader);
      expect(fragment).toMatch(/\bgl_PointCoord\b/);
      expect(fragment).toMatch(/\b(?:length|distance|dot)\s*\(/);
      expect(fragment).toMatch(/\b(?:smoothstep|exp|pow)\s*\(/);
    } finally {
      dispose(sky);
    }
  });

  it("cannot be picked recursively even by a ray that otherwise hits a known star", () => {
    const sky = createCosmosBackdrop();
    try {
      const stars = getStars(sky);
      sky.updateMatrixWorld(true);
      const target = new THREE.Vector3()
        .fromBufferAttribute(stars.geometry.getAttribute("position"), 0)
        .applyMatrix4(stars.matrixWorld);
      const raycaster = new THREE.Raycaster(new THREE.Vector3(), target.clone().normalize(), 0, target.length() + 1);
      raycaster.params.Points.threshold = 1e-4;
      expect(raycaster.layers.test(stars.layers)).toBe(true);

      const control: THREE.Intersection[] = [];
      THREE.Points.prototype.raycast.call(stars, raycaster, control);
      const knownHit = control.find((hit) => hit.object === stars && hit.index === 0);
      expect(knownHit).toBeDefined();
      expect(knownHit!.point.distanceTo(target)).toBeLessThan(1e-4);
      expect(raycaster.intersectObject(sky, true)).toEqual([]);

      // A true no-op also preserves intersections already collected for other objects.
      const existingHits = [knownHit!];
      stars.raycast(raycaster, existingHits);
      expect(existingHits).toEqual([knownHit]);
    } finally {
      dispose(sky);
    }
  });
});
