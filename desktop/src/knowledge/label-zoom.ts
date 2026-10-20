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

export type ProjectedLabelCandidate = { id: string; x: number; y: number; topic: boolean; rank: number; width?: number; height?: number };

export type LabelReadingRole = 'topic' | 'reference-section' | 'title' | 'active';

/** How much of a saved label fits on screen. The stored string is unchanged. */
export function labelReadingFrame(role: LabelReadingRole, level: LabelZoom, text: string, fontSize: number): { maxWidth: number; lines: 1 | 2; width: number; height: number } {
  const size = Number.isFinite(fontSize) && fontSize > 0 ? fontSize : 13;
  const glyphs = [...text.normalize('NFC').replace(/\s+/g, ' ').trim()].length;
  const natural = 8 + glyphs * size * .9 + (role==='reference-section'?32:0);
  const maxWidth = role === 'topic' || role==='reference-section' ? 220 : role === 'active' ? 260 : level >= 2 ? 280 : 240;
  const lines: 1 | 2 = role === 'title' && level >= 2 && natural > maxWidth ? 2 : 1;
  const line = Math.ceil(size * 1.45 + 4);
  return { maxWidth, lines, width: Math.min(maxWidth, natural), height: line * lines };
}

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
    if (chosen.some(other => {
      const vertical = other.topic && candidate.topic ? 22
        : other.height && candidate.height ? (other.height + candidate.height) / 2 + 8 : 38;
      return other.id === candidate.id
        || ((other.x - candidate.x) / (((other.width ?? 162) + (candidate.width ?? 162)) / 2 + 10)) ** 2
          + ((other.y - candidate.y) / vertical) ** 2 < 1;
    })) continue;
    chosen.push(candidate);
  }
  return new Set(chosen.map(candidate => candidate.id));
}
