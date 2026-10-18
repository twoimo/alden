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
export function labelZoomStyle(level: LabelZoom, width: number, height: number, ratio = [.55, 1, 1.7, 2.5][level]) {
  const capacity = Math.max(4, Math.min(24, Math.floor(width * height / 16000)));
  // Font size follows magnification, not the discrete content threshold. Quarter
  // pixels bound remeasurement during a gesture while retaining readable type.
  const magnification = Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
  const progress = Math.max(0, Math.min(1, Math.log2(magnification / .55) / Math.log2(2.5 / .55)));
  const gradualBudget = Math.max(4, Math.round(8 * magnification ** 1.2));
  return { budget: Math.min([4, 8, 16, 24][level], capacity, gradualBudget),
    topicSize: Math.round((13 + progress * 3) * 4) / 4,
    detailSize: Math.round((12 + progress) * 4) / 4,
    detail: level >= 2, opacity: [.88, .96, 1, 1][level] };
}

export type ProjectedLabelCandidate = { id: string; x: number; y: number; topic: boolean; rank: number };

/** Screen-space decluttering only: never changes graph membership or meaning.
 * A small retention preference avoids swapping equally ranked titles on pan.
 * Actual measured rectangles are checked by the renderer afterwards.
 */
export function selectProjectedLabels(
  candidates: readonly ProjectedLabelCandidate[], budget: number,
  previous: ReadonlySet<string>,
): Set<string> {
  const limit = Math.max(0, Math.min(24, Math.floor(Number.isFinite(budget) ? budget : 0)));
  const ordered = candidates.filter(c => Number.isFinite(c.x) && Number.isFinite(c.y))
    .sort((a, b) => Number(b.topic) - Number(a.topic)
      || (a.rank - (previous.has(a.id) ? 2 : 0)) - (b.rank - (previous.has(b.id) ? 2 : 0))
      || a.id.localeCompare(b.id));
  const chosen: ProjectedLabelCandidate[] = [];
  for (const candidate of ordered) {
    if (chosen.length >= limit) break;
    if (chosen.some(other => other.id === candidate.id
      || ((other.x - candidate.x) / 172) ** 2 + ((other.y - candidate.y) / 42) ** 2 < 1)) continue;
    chosen.push(candidate);
  }
  return new Set(chosen.map(candidate => candidate.id));
}
