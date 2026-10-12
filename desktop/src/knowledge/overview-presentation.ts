import type { Point3 } from './plasticity';

// Display geometry only. R/S solving, identities and saved source coordinates
// retain their original authority. Local and sparse views use exact positions.
// Conservative Euclidean derivative bound of the ellipsoidal radial mapping.
// Include it in the existing pixel-motion stop criterion.
export const OVERVIEW_DISPLAY_GAIN = Math.pow(.1, -.35) * (1 + .35 * 2.3 / 1.1);
export function overviewDisplayPoint(point: Point3, width: number, height: number,
  dense: boolean, output: Point3): Point3 {
  if (!dense) { output.x=point.x;output.y=point.y;output.z=point.z;return output; }
  const radius=Math.hypot(point.x/2.3,point.y/1.55,point.z/1.1);
  const gain=Math.pow(Math.max(.1,Math.min(1,radius)),-.35);
  const aspect=Number.isFinite(width)&&Number.isFinite(height)&&width>0&&height>0
    ? Math.min(1,Math.max(.5,width/height/(2.3/1.55))) : 1;
  output.x=point.x*gain*aspect;output.y=point.y*gain;output.z=point.z*gain;
  return output;
}
