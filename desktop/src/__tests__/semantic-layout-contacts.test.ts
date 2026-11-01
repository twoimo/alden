import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { refineLayoutContacts, SemanticLayoutEngine, SEMANTIC_LAYOUT_LIMITS as limits } from '../knowledge/semantic-layout';

const diagnostics = () => new SemanticLayoutEngine().layout({
  ...parseKnowledgeGraph({ nodes: [], edges: [] }), focusId: null, hops: 0,
}, new Map()).diagnostics;

describe('bounded final layout contacts', () => {
  it('separates a simple movable contact while keeping its fixed endpoint exact', () => {
    const fixed = { x: 0, y: 0, z: 0 }, points = [fixed, { x: .01, y: 0, z: 0 }], d = diagnostics();
    refineLayoutContacts(['fixed', 'moving'], points, [false, true], d, 16, (_, p) => p);
    expect(points[0]).toBe(fixed);
    expect(points[1].x).toBeGreaterThanOrEqual(.14 - 1e-9);
    expect(d.finalContactMovedNodes).toBe(1);
    expect(d.finalContactTests).toBe(d.collisionTests);
    expect(d.finalContactPasses).toBeLessThanOrEqual(limits.contactPasses);
  });

  it('advances past repeated samples in a seventeen-member neighborhood', () => {
    const ids = Array.from({ length: 17 }, (_, i) => `n${i}`);
    const points = ids.map((_, i) => i === 0 ? { x: 0, y: 0, z: 0 }
      : i === 12 ? { x: .01, y: 0, z: 0 } : { x: .13, y: .13, z: .13 });
    const fixed = structuredClone(points.slice(1)), d = diagnostics();
    refineLayoutContacts(ids, points, ids.map((_, i) => i === 0), d, 8, (_, p) => p);
    expect(Math.hypot(points[0].x - .01, points[0].y, points[0].z)).toBeGreaterThan(.04);
    expect(points.slice(1)).toEqual(fixed);
    expect(d.finalContactTests).toBeLessThanOrEqual(8);
    expect(d.finalContactCandidatesTruncated).toBe(true);
  });

  it('shares the existing distance-test cap instead of adding an unreported budget', () => {
    const d = diagnostics(), points = [{ x: 0, y: 0, z: 0 }, { x: .01, y: 0, z: 0 }];
    d.collisionTests = limits.collisionTests - 2;
    refineLayoutContacts(['a', 'b'], points, [true, true], d, 1024, (_, p) => p);
    expect(d.collisionTests).toBe(limits.collisionTests);
    expect(d.finalContactTests).toBe(2);
    expect(d.collisionBudgetExhausted).toBe(true);
    expect(d.finalContactCandidatesTruncated).toBe(true);
  });

  it('keeps crowded coincidences finite, bounded and explicitly partial', () => {
    const ids = Array.from({ length: 128 }, (_, i) => `same-${i}`);
    const points = ids.map(() => ({ x: 0, y: 0, z: 0 })), before = structuredClone(ids), d = diagnostics();
    refineLayoutContacts(ids, points, ids.map(() => true), d, 1024, (_, p) => p);
    expect(ids).toEqual(before);
    for (const p of points) {
      expect(Object.values(p).every(Number.isFinite)).toBe(true);
      expect(Math.hypot(p.x, p.y, p.z)).toBeLessThanOrEqual(.140000001);
    }
    expect(d.finalContactTests).toBeLessThanOrEqual(1024);
    expect(d.finalContactCandidatesTruncated).toBe(true);
  });

  it('respects the caller constraint even when it leaves a contact unresolved', () => {
    const origin = { x: 0, y: 0, z: 0 }, points = [origin, { x: .01, y: 0, z: 0 }], d = diagnostics();
    refineLayoutContacts(['a', 'b'], points, [true, false], d, 8, () => origin);
    expect(points[0]).toEqual(origin);
    expect(points[1]).toEqual({ x: .01, y: 0, z: 0 });
    expect(d.finalContactTests).toBeGreaterThan(0);
    expect(d.finalContactMovedNodes).toBe(0);
  });

  it('reports unsafe extreme grid coordinates without moving them or inventing tests', () => {
    const points = [{ x: Number.MAX_VALUE, y: 0, z: 0 }, { x: -Number.MAX_VALUE, y: 0, z: 0 }];
    const before = structuredClone(points), d = diagnostics();
    refineLayoutContacts(['a', 'b'], points, [true, true], d, 8, (_, p) => p);
    expect(points).toEqual(before);
    expect(d.finalContactSkippedNodes).toBe(2);
    expect(d.finalContactCandidatesTruncated).toBe(true);
    expect(d.finalContactTests).toBe(0);
  });

  it('resolves the same boundary contact through dense and sparse coordinate frames', () => {
    for (const origin of [30.2 * .14, 31.2 * .14]) {
      const fixed = { x: origin, y: origin, z: origin };
      const points = [fixed, { ...fixed, x: origin + .01 }], d = diagnostics();
      refineLayoutContacts(['fixed', 'moving'], points, [false, true], d, 8, (_, p) => p);
      expect(points[0]).toBe(fixed);
      expect(points[1].x - fixed.x).toBeCloseTo(.14, 10);
      expect(d.finalContactSkippedNodes).toBe(0);
      expect(d.finalContactTests).toBeLessThanOrEqual(8);
    }
  });
});
