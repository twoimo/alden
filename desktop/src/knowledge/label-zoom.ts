/** Relative to this graph's fitted overview, independent of world-space scale. */
export type LabelZoom = 0 | 1 | 2 | 3;
const BOUNDARIES = [.72, 1.4, 2.1];
export function labelZoomLevel(ratio: number, previous: LabelZoom = 1): LabelZoom {
  if (!Number.isFinite(ratio) || ratio <= 0) return 1;
  let level = previous;
  // A five percent dead band prevents trackpad jitter from swapping labels.
  while (level < 3 && ratio > BOUNDARIES[level] * 1.05) level = (level + 1) as LabelZoom;
  while (level > 0 && ratio < BOUNDARIES[level - 1] * .95) level = (level - 1) as LabelZoom;
  return level;
}
export function labelZoomStyle(level: LabelZoom, width: number, height: number) {
  const capacity = Math.max(4, Math.min(24, Math.floor(width * height / 16000)));
  return { budget: Math.min([4, 8, 16, 24][level], capacity),
    topicSize: [12, 13, 14, 15][level], detailSize: level === 3 ? 12 : 11.5,
    detail: level >= 2, opacity: [.8, .96, 1, 1][level] };
}
