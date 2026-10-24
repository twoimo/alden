import type { ProjectedCircle, ProjectionViewport } from './projected-layout-metrics';

export const PROJECTED_RELAXATION_LIMITS = Object.freeze({ nodes: 2048, passes: 16, pairTests: 131072, shiftCssPx: 10, gapCssPx: 4.5 });
export interface RelaxationResult {
  offsets: Float64Array;
  diagnostics: { nodes: number; passes: number; effectiveGapCssPx:number; pairTests: number; corePairTests:number; gapPairTests:number; coreContacts:number; moved: number; maxShift: number; budgetExhausted: boolean; inputTruncated: boolean };
}

/** Bounded display-only offsets. No identities, edges, source coordinates or
 * semantic weights are created or modified. Called on settled snapshots. */
export function relaxProjectedCircles(input: readonly ProjectedCircle[], viewport: ProjectionViewport,
  pinned: ReadonlySet<number> = new Set(), initialOffsets?: Float64Array): RelaxationResult {
  const limits = PROJECTED_RELAXATION_LIMITS, count = Math.min(input.length, limits.nodes);
  const offsets = new Float64Array(count * 2), x = new Float64Array(count), y = new Float64Array(count);
  const active = new Uint8Array(count), dx = new Float64Array(count), dy = new Float64Array(count);
  const diagnostics = { nodes: count, passes: 0, effectiveGapCssPx:0, pairTests: 0, corePairTests:0, gapPairTests:0, coreContacts:0, moved: 0, maxShift: 0,
    budgetExhausted: false, inputTruncated: input.length > count };
  const { left, top, width, height } = viewport;
  if (![left,top,width,height].every(Number.isFinite) || width <= 0 || height <= 0) return { offsets, diagnostics };
  let maxRadius = 0, retainedCorrection = false;
  for (let i = 0; i < count; i++) {
    const p = input[i]; x[i] = p.x; y[i] = p.y;
    if (![p.x,p.y,p.radius].every(Number.isFinite) || p.radius <= 0 || p.inClipVolume === false
      || p.x < left || p.x > left + width || p.y < top || p.y > top + height
      || p.radius * 2 > width || p.radius * 2 > height) continue;
    active[i] = 1; maxRadius = Math.max(maxRadius, p.radius);
    if (initialOffsets?.length === count * 2) {
      const ox = initialOffsets[i*2], oy = initialOffsets[i*2+1], size = Math.hypot(ox,oy);
      if (Number.isFinite(size)) {
        if (size > 0) retainedCorrection = true;
        const gain = size > limits.shiftCssPx ? limits.shiftCssPx / size : 1;
        x[i] = pinned.has(i) ? p.x + ox * gain : Math.max(left + p.radius,Math.min(left + width - p.radius,p.x + ox * gain));
        y[i] = pinned.has(i) ? p.y + oy * gain : Math.max(top + p.radius,Math.min(top + height - p.radius,p.y + oy * gain));
      }
    }
  }
  const visibleCount = active.reduce((sum,value)=>sum+value,0);
  // Optional whitespace follows the visible average pitch; it is not a
  // source/semantic distance or a guarantee that every packing is feasible.
  const gap = Math.min(limits.gapCssPx,Math.sqrt(width*height/Math.max(1,visibleCount))*.1);
  diagnostics.effectiveGapCssPx = gap;
  for (let pass = 0; pass < limits.passes; pass++) {
    dx.fill(0); dy.fill(0);
    const budget = Math.floor((limits.pairTests - diagnostics.pairTests) / (limits.passes - pass));
    let tested = 0, contacts = 0, coreContacts = 0;
    const accumulate = (gap:number, core:boolean) => {
      const cellSize = Math.max(1, maxRadius * 2 + gap);
      const columns = Math.ceil(width / cellSize) + 3;
      if (!Number.isSafeInteger(columns * (Math.ceil(height / cellSize) + 3))) return;
      const grid = new Map<number, number[]>();
      for (let i = 0; i < count; i++) if (active[i]) {
        const key = Math.floor((y[i] - top) / cellSize) * columns + Math.floor((x[i] - left) / cellSize);
        const cell = grid.get(key); if (cell) cell.push(i); else grid.set(key, [i]);
      }
      outer: for (let slot = 0; slot < count; slot++) {
        const a = (slot + pass * 257) % count;
        if (!active[a]) continue;
        const cx = Math.floor((x[a] - left) / cellSize), cy = Math.floor((y[a] - top) / cellSize);
        for (let gx = cx - 1; gx <= cx + 1; gx++) for (let gy = cy - 1; gy <= cy + 1; gy++)
          for (const b of grid.get(gy * columns + gx) ?? []) {
            if (b <= a || pinned.has(a) && pinned.has(b)) continue;
            if (tested >= budget) { diagnostics.budgetExhausted = true; break outer; }
            tested++; diagnostics.pairTests++;
            if(core)diagnostics.corePairTests++;else diagnostics.gapPairTests++;
            let vx = x[a] - x[b], vy = y[a] - y[b], distance = Math.hypot(vx, vy);
            const radii = input[a].radius + input[b].radius;
            const separation = radii + (core ? .5 : gap);
            if (distance >= (core ? radii : separation)) continue;
            contacts++;if(core){coreContacts++;diagnostics.coreContacts++;}
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
    };
    // Spend each pass on actual overlapping cores before optional whitespace.
    accumulate(0,true);
    // A retained correction is already a settled display choice. Reprojection
    // repairs new core contacts without repeatedly spending optional whitespace.
    // Extra convergence rounds are for cores only, not repeated whitespace.
    if (!retainedCorrection && !coreContacts && tested < budget && pass < Math.floor(limits.passes/2)) accumulate(gap,false);
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
  for (let i = 0; i < count; i++) if (active[i]) {
    let ox = x[i] - input[i].x, oy = y[i] - input[i].y, size = Math.hypot(ox,oy);
    // Clipping correction must obey the same displacement limit.
    if (size > limits.shiftCssPx) { ox *= limits.shiftCssPx / size; oy *= limits.shiftCssPx / size; size = limits.shiftCssPx; }
    offsets[i*2] = ox; offsets[i*2+1] = oy;
    if (size > 1e-9) diagnostics.moved++;
    diagnostics.maxShift = Math.max(diagnostics.maxShift,size);
  }
  return { offsets, diagnostics };
}
