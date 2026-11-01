import type { Point3 } from './plasticity';

/** A pass-local broad phase. Indices retain ascending pair order. The caller
 * owns points and must report both endpoints after each push. */
export function centerCandidates(points: readonly Point3[], minimum: number) {
  if (points.length < 128 || points.length > 1024 || minimum < .62 || minimum > 1.05
    || !Number.isFinite(minimum) || points.some(p => [p.x, p.y, p.z].some(v => !Number.isFinite(v) || Math.abs(v) > 64))) return null;
  // Padding covers cell-boundary rounding in this bounded center solver.
  // Unusual checkpoints use the caller's original exact all-pairs path.
  const width = minimum * (1 + 1e-6), words = Math.ceil(points.length / 32);
  type Bin = { mask: Uint32Array; count: number };
  const bins = [new Map<number, Bin>(), new Map<number, Bin>(), new Map<number, Bin>()];
  const coordinates = new Int32Array(points.length * 3); coordinates.fill(0x7fffffff);
  const empty = new Uint32Array(words);
  const available: Uint32Array[] = [], retired: Uint32Array[] = [];
  let disabled = false;
  const updateAxis = (i: number, axis: number, next: number) => {
    const offset = i * 3 + axis, old = coordinates[offset];
    if (next === old) return false;
    const word = i >>> 5, bit = 1 << (i & 31), previous = bins[axis].get(old);
    if (previous) {
      previous.mask[word] &= ~bit;
      if (--previous.count === 0) { bins[axis].delete(old); retired.push(previous.mask); }
    }
    let bin = bins[axis].get(next);
    if (!bin) { bin = { mask: available.pop() ?? new Uint32Array(words), count: 0 }; bins[axis].set(next, bin); }
    bin.mask[word] |= bit; bin.count++; coordinates[offset] = next;
    return true;
  };
  const update = (i: number) => {
    const p = points[i], x = Math.floor(p.x / width), y = Math.floor(p.y / width), z = Math.floor(p.z / width);
    if (!Number.isFinite(x + y + z) || Math.abs(x) > 8192 || Math.abs(y) > 8192 || Math.abs(z) > 8192) { disabled = true; return false; }
    const movedX = updateAxis(i, 0, x), movedY = updateAxis(i, 1, y), movedZ = updateAxis(i, 2, z);
    return movedX || movedY || movedZ;
  };
  for (let i = 0; i < points.length; i++) update(i);
  const neighborMasks = (i: number) => {
    const x = coordinates[i * 3], y = coordinates[i * 3 + 1], z = coordinates[i * 3 + 2];
    return [bins[0].get(x - 1)?.mask ?? empty, bins[0].get(x)?.mask ?? empty, bins[0].get(x + 1)?.mask ?? empty,
      bins[1].get(y - 1)?.mask ?? empty, bins[1].get(y)?.mask ?? empty, bins[1].get(y + 1)?.mask ?? empty,
      bins[2].get(z - 1)?.mask ?? empty, bins[2].get(z)?.mask ?? empty, bins[2].get(z + 1)?.mask ?? empty];
  };
  const wordMask = (masks: Uint32Array[], word: number) =>
    (masks[0][word] | masks[1][word] | masks[2][word])
    & (masks[3][word] | masks[4][word] | masks[5][word])
    & (masks[6][word] | masks[7][word] | masks[8][word]);
  // A dense pass cannot skip enough pairs to pay for index maintenance. Sample
  // eight deterministic rows; this only selects an exact execution path.
  let sampled = 0, possible = 0;
  for (let sample = 0; sample < 8; sample++) {
    const i = Math.floor(sample * points.length / 8), neighbors = neighborMasks(i);
    possible += points.length - i - 1;
    for (let word = (i + 1) >>> 5; word < words; word++) {
      let bits = wordMask(neighbors, word) & (word === ((i + 1) >>> 5) ? -1 << ((i + 1) & 31) : -1);
      bits -= (bits >>> 1) & 0x55555555;
      bits = (bits & 0x33333333) + ((bits >>> 2) & 0x33333333);
      sampled += Math.imul((bits + (bits >>> 4)) & 0x0f0f0f0f, 0x01010101) >>> 24;
    }
  }
  if (sampled > possible * .45) return null;
  let masks: Uint32Array[] = [], currentWord = -1, mask = 0, row = -1;
  return {
    row(i: number) {
      if (disabled) return;
      // Empty masks may still be referenced by the current row's neighbor
      // cache. Recycle them only after that row has finished.
      for (const mask of retired) available.push(mask);
      retired.length = 0;
      row = i; masks = neighborMasks(i); currentWord = -1;
    },
    next(atLeast: number): number {
      if (disabled) return atLeast;
      for (let word = atLeast >>> 5; word < words; word++) {
        if (word !== currentWord) { mask = wordMask(masks, word); currentWord = word; }
        mask &= -1 << (atLeast & 31);
        if (mask) {
          const next = (word << 5) + 31 - Math.clz32(mask & -mask);
          mask &= mask - 1;
          return next;
        }
        atLeast = (word + 1) << 5;
      }
      return points.length;
    },
    moved(i: number, j: number) {
      const crossed = update(i); update(j);
      if (disabled) return;
      if (crossed) { masks = neighborMasks(row); currentWord = -1; }
    },
  };
}
