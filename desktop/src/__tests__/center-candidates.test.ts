import { describe, expect, it } from 'vitest';
import { centerCandidates } from '../knowledge/center-candidates';

const points = () => Array.from({ length: 128 }, (_, i) => ({ x: 20 + (i % 8) * 2, y: 20 + (Math.floor(i / 8) % 8) * 2, z: 20 + Math.floor(i / 64) * 2 }));
function collect(index: NonNullable<ReturnType<typeof centerCandidates>>, count: number, first: number) {
  const found: number[] = [];
  for (let next = first; next < count; next++) {
    next = index.next(next); if (next >= count) break;
    found.push(next);
  }
  return found;
}

describe('ordered center candidates', () => {
  it('keeps sparse hits across sign bits and word boundaries in order', () => {
    const p = points(); p[0] = { x: 0, y: 0, z: 0 };
    for (const i of [31, 32, 63, 64, 127]) p[i] = { x: .2, y: 0, z: 0 };
    const index = centerCandidates(p, .62)!; index.row(0);
    expect(collect(index, p.length, 1)).toEqual([31, 32, 63, 64, 127]);
  });

  it('refreshes the moving row and retains the other moved endpoint for later rows', () => {
    const p = points(); p[0] = { x: 0, y: 0, z: 0 }; p[31] = { x: .2, y: 0, z: 0 };
    p[32] = { x: 3.1, y: 0, z: 0 }; p[63] = { x: 3.2, y: 0, z: 0 }; p[100] = { x: 10.1, y: 0, z: 0 };
    const index = centerCandidates(p, .62)!; index.row(0);
    expect(index.next(1)).toBe(31);
    p[0].x = 3; p[31].x = 10; index.moved(0, 31);
    expect(collect(index, p.length, 32)).toEqual([32, 63]);
    index.row(31);
    expect(collect(index, p.length, 32)).toEqual([100]);
  });

  it('keeps same-word new hits and never revisits already processed indices', () => {
    const p = points(); p[0] = { x: 0, y: 0, z: 0 }; p[7] = { x: .1, y: 0, z: 0 };
    p[2] = { x: 3.1, y: 0, z: 0 }; p[8] = { x: 3.2, y: 0, z: 0 };
    const index = centerCandidates(p, .62)!; index.row(0);
    expect(index.next(1)).toBe(7);
    p[0].x = 3; index.moved(0, 7);
    expect(collect(index, p.length, 8)).toEqual([8]);
  });

  it('never omits a box contact near positive and negative cell boundaries', () => {
    for (const minimum of [.62, .8, 1.05]) {
      const p = Array.from({ length: 128 }, (_, i) => ({ x: (i % 8 - 4) * minimum + (i % 3 - 1) * Number.EPSILON,
        y: (Math.floor(i / 8) % 4 - 2) * minimum, z: (Math.floor(i / 32) - 2) * minimum }));
      const index = centerCandidates(p, minimum)!;
      for (let i = 0; i < p.length; i++) {
        index.row(i); const found = collect(index, p.length, i + 1);
        expect(found).toEqual([...new Set(found)].sort((a, b) => a - b));
        for (let j = i + 1; j < p.length; j++) if (Math.abs(p[j].x - p[i].x) < minimum
          && Math.abs(p[j].y - p[i].y) < minimum && Math.abs(p[j].z - p[i].z) < minimum) expect(found).toContain(j);
      }
    }
  });

  it('leaves small or unusual checkpoints on the original all-pairs path', () => {
    expect(centerCandidates(points().slice(1), .62)).toBeNull();
    expect(centerCandidates(Array.from({ length: 1025 }, () => ({ x: 0, y: 0, z: 0 })), .62)).toBeNull();
    for (const x of [65, Infinity, NaN]) {
      const p = points(); p[0].x = x; expect(centerCandidates(p, .62)).toBeNull();
    }
    for (const minimum of [.61, 1.06, NaN]) expect(centerCandidates(points(), minimum)).toBeNull();
    expect(centerCandidates(points().map(() => ({ x: 0, y: 0, z: 0 })), .62)).toBeNull();
  });

  it('continues with every remaining pair after an out-of-range reported move', () => {
    for (const x of [1e20, Infinity, NaN]) {
      const p = points(), index = centerCandidates(p, .62)!; index.row(0);
      p[0].x = x; index.moved(0, 7);
      expect(collect(index, p.length, 8)).toEqual(Array.from({ length: 120 }, (_, i) => i + 8));
      index.row(31); expect(index.next(32)).toBe(32);
    }
  });

  it('reuses empty bins across rows without retaining an earlier endpoint bit', () => {
    const p = points(); p[0] = { x: 0, y: 0, z: 0 }; p[31] = { x: .2, y: 0, z: 0 };
    p[32] = { x: 3.1, y: 0, z: 0 }; p[100] = { x: 10.1, y: 0, z: 0 }; p[110] = { x: 13.1, y: 0, z: 0 };
    const index = centerCandidates(p, .62)!; index.row(0);
    expect(index.next(1)).toBe(31);
    p[0].x = 3; p[31].x = 10; index.moved(0, 31);
    expect(collect(index, p.length, 32)).toEqual([32]);
    index.row(31); expect(index.next(32)).toBe(100);
    p[31].x = 13; p[100].x = -10; index.moved(31, 100);
    expect(collect(index, p.length, 101)).toEqual([110]);
    index.row(32); expect(collect(index, p.length, 33)).toEqual([]);
  });
});
