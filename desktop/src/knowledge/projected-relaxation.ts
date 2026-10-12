import type { ProjectedCircle, ProjectionViewport } from './projected-layout-metrics';

export const PROJECTED_RELAXATION_LIMITS = Object.freeze({ nodes: 2048, passes: 8, pairTests: 131072, shiftCssPx: 10, gapCssPx: .7 });
export interface RelaxationResult {
  offsets: Float64Array;
  diagnostics: { nodes: number; passes: number; pairTests: number; moved: number; maxShift: number; budgetExhausted: boolean; inputTruncated: boolean };
}

/** Bounded display-only offsets. No identities, edges, source coordinates or
 * semantic weights are created or modified. Called on settled snapshots. */
export function relaxProjectedCircles(input: readonly ProjectedCircle[], viewport: ProjectionViewport,
  pinned: ReadonlySet<number> = new Set()): RelaxationResult {
  const limits = PROJECTED_RELAXATION_LIMITS, count = Math.min(input.length, limits.nodes);
  const offsets = new Float64Array(count * 2), x = new Float64Array(count), y = new Float64Array(count);
  const active = new Uint8Array(count), dx = new Float64Array(count), dy = new Float64Array(count);
  const diagnostics = { nodes: count, passes: 0, pairTests: 0, moved: 0, maxShift: 0,
    budgetExhausted: false, inputTruncated: input.length > count };
  const { left, top, width, height } = viewport;
  if (![left,top,width,height].every(Number.isFinite) || width <= 0 || height <= 0) return { offsets, diagnostics };
  let maxRadius = 0;
  for (let i = 0; i < count; i++) {
    const p = input[i]; x[i] = p.x; y[i] = p.y;
    if (![p.x,p.y,p.radius].every(Number.isFinite) || p.radius <= 0 || p.inClipVolume === false
      || p.x < left || p.x > left + width || p.y < top || p.y > top + height
      || p.radius * 2 > width || p.radius * 2 > height) continue;
    active[i] = 1; maxRadius = Math.max(maxRadius, p.radius);
  }
  const cellSize = Math.max(1, maxRadius * 2 + limits.gapCssPx);
  for (let pass = 0; pass < limits.passes; pass++) {
    const grid = new Map<string, number[]>();
    for (let i = 0; i < count; i++) if (active[i]) {
      const key = Math.floor(x[i] / cellSize) + ':' + Math.floor(y[i] / cellSize);
      const cell = grid.get(key); if (cell) cell.push(i); else grid.set(key, [i]);
    }
    dx.fill(0); dy.fill(0);
    const budget = Math.floor((limits.pairTests - diagnostics.pairTests) / (limits.passes - pass));
    let tested = 0, contacts = 0;
    outer: for (let slot = 0; slot < count; slot++) {
      const a = (slot + pass * 257) % count;
      if (!active[a]) continue;
      const cx = Math.floor(x[a] / cellSize), cy = Math.floor(y[a] / cellSize);
      for (let gx = cx - 1; gx <= cx + 1; gx++) for (let gy = cy - 1; gy <= cy + 1; gy++)
        for (const b of grid.get(gx + ':' + gy) ?? []) {
          if (b <= a || pinned.has(a) && pinned.has(b)) continue;
          if (tested >= budget) { diagnostics.budgetExhausted = true; break outer; }
          tested++; diagnostics.pairTests++;
          let vx = x[a] - x[b], vy = y[a] - y[b], distance = Math.hypot(vx, vy);
          const separation = input[a].radius + input[b].radius + limits.gapCssPx;
          if (distance >= separation) continue;
          contacts++;
          if (distance < 1e-9) {
            const angle = ((Math.imul(a + 1, 2654435761) ^ Math.imul(b + 1, 1597334677)) >>> 0) / 4294967296 * Math.PI * 2;
            vx = Math.cos(angle); vy = Math.sin(angle); distance = 1;
          } else { vx /= distance; vy /= distance; }
          const amount = Math.max(.1, separation - Math.hypot(x[a]-x[b],y[a]-y[b])) * .55;
          const share = pinned.has(a) || pinned.has(b) ? 1 : .5;
          if (!pinned.has(a)) { dx[a] += vx * amount * share; dy[a] += vy * amount * share; }
          if (!pinned.has(b)) { dx[b] -= vx * amount * share; dy[b] -= vy * amount * share; }
        }
    }
    diagnostics.passes++;
    if (!contacts) break;
    for (let i = 0; i < count; i++) if (active[i] && !pinned.has(i)) {
      const step = Math.hypot(dx[i],dy[i]), gain = step > 2 ? 2 / step : 1;
      let ox = x[i] + dx[i] * gain - input[i].x, oy = y[i] + dy[i] * gain - input[i].y;
      const total = Math.hypot(ox,oy);
      if (total > limits.shiftCssPx) { ox *= limits.shiftCssPx / total; oy *= limits.shiftCssPx / total; }
      x[i] = Math.max(left + input[i].radius,Math.min(left + width - input[i].radius,input[i].x + ox));
      y[i] = Math.max(top + input[i].radius,Math.min(top + height - input[i].radius,input[i].y + oy));
    }
  }
  for (let i = 0; i < count; i++) if (active[i] && !pinned.has(i)) {
    let ox = x[i] - input[i].x, oy = y[i] - input[i].y, size = Math.hypot(ox,oy);
    // Clipping correction must obey the same displacement limit.
    if (size > limits.shiftCssPx) { ox *= limits.shiftCssPx / size; oy *= limits.shiftCssPx / size; size = limits.shiftCssPx; }
    offsets[i*2] = ox; offsets[i*2+1] = oy;
    if (size > 1e-9) diagnostics.moved++;
    diagnostics.maxShift = Math.max(diagnostics.maxShift,size);
  }
  return { offsets, diagnostics };
}
