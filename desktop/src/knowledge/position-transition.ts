import type { Point3 } from './plasticity';

/** Snapshot targets for nodes outside the bounded physics set. No force work,
 * source events, timers, or rendering takes place in this adapter. */
export class PositionTransition {
  private readonly targets = new Map<string, Point3>();
  get moving(): boolean { return this.targets.size > 0; }
  get pending(): number { return this.targets.size; }
  clear(): void { this.targets.clear(); }

  setTargets(anchors: ReadonlyMap<string, Point3>, positions: ReadonlyMap<string, Point3>, physics: ReadonlySet<string>): boolean {
    this.targets.clear();
    if (anchors.size > 2048 || [...anchors.values()].some(p => ![p.x, p.y, p.z].every(Number.isFinite))) return false;
    for (const [id, p] of anchors) {
      if (physics.has(id)) continue;
      const old = positions.get(id);
      if (old && Math.hypot(p.x - old.x, p.y - old.y, p.z - old.z) > 1e-7) this.targets.set(id, { ...p });
    }
    return true;
  }

  advance(positions: ReadonlyMap<string, Point3>, dt: number, immediate = false): boolean {
    if (!this.moving || (!immediate && (!Number.isFinite(dt) || dt <= 0))) return false;
    const gain = immediate ? 1 : 1 - Math.exp(-12 * Math.min(.25, dt));
    let changed = false;
    for (const [id, target] of this.targets) {
      const point = positions.get(id);
      if (!point) { this.targets.delete(id); continue; }
      point.x += (target.x - point.x) * gain;
      point.y += (target.y - point.y) * gain;
      point.z += (target.z - point.z) * gain;
      if (Math.hypot(target.x - point.x, target.y - point.y, target.z - point.z) <= 1e-7) {
        point.x = target.x; point.y = target.y; point.z = target.z; this.targets.delete(id);
      }
      changed = true;
    }
    return changed;
  }
}
