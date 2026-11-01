import type { Point3 } from './plasticity';
import { PROJECTED_RELAXATION_LIMITS } from './projected-relaxation';

/** Limit a retained world offset in the current camera, including during zoom
 * and resize. Homogeneous perspective gives the exact interpolation fraction;
 * no source coordinate, stored offset or identity is mutated. */
export function projectedOffsetGain(base: Point3, offset: Point3, clip: ArrayLike<number>,
  width: number, height: number): number {
  if (!(width>0&&height>0)) return 0;
  const w=clip[3]*base.x+clip[7]*base.y+clip[11]*base.z+clip[15];
  const dw=clip[3]*offset.x+clip[7]*offset.y+clip[11]*offset.z;
  if (!(w>0&&w+dw>0)) return 0;
  const x=clip[0]*base.x+clip[4]*base.y+clip[8]*base.z+clip[12];
  const y=clip[1]*base.x+clip[5]*base.y+clip[9]*base.z+clip[13];
  const dx=clip[0]*offset.x+clip[4]*offset.y+clip[8]*offset.z;
  const dy=clip[1]*offset.x+clip[5]*offset.y+clip[9]*offset.z;
  const distance=Math.hypot(((x+dx)/(w+dw)-x/w)*width/2,((y+dy)/(w+dw)-y/w)*height/2);
  if (!Number.isFinite(distance)) return 0;
  const cap=PROJECTED_RELAXATION_LIMITS.shiftCssPx;
  return distance<=cap ? 1 : cap/(distance*(1+dw/w)-cap*dw/w);
}
