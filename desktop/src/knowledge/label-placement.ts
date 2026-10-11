/**
 * Overview label-only placement in canvas-local screen pixels.
 * Nodes, edges, semantic positions and keyboard targets are never filtered.
 * The caller projects/measures labels once and invokes this with cached widths.
 */
export type LabelBox = { width: number; left: number; top: number; visible: boolean };
export type LabelViewport = { x: number; y: number; width: number; height: number };
export type LabelExclusion = { left: number; top: number; right: number; bottom: number };

const LABEL_HEIGHT = 24;
const LABEL_GAP = 7;
const VERTICAL_STEP = 32;
// At most 15 nearby slots, with no state or allocations per rendered label.
const OFFSETS = [
  [0, 0], [0, -1], [0, 1], [0, -2], [0, 2], [0, -3], [0, 3],
  [-1, 0], [1, 0], [-1, -1], [1, -1], [-1, 1], [1, 1],
  [-1, -2], [1, -2],
] as const;

export function compareOverviewLabelPriority(
  first: string,
  second: string,
  focusedId: string | null,
  hoveredId: string | null,
  representativeRanks: ReadonlyMap<string, number>,
): number {
  if (first === second) return 0;
  const rank = (id: string): number => id === focusedId ? -2
    : id === hoveredId ? -1
      : representativeRanks.get(id) ?? Number.MAX_SAFE_INTEGER;
  const difference = rank(first) - rank(second);
  return difference || first.localeCompare(second);
}

/** Mutates the supplied measured box; earlierBoxes[0..earlierCount) are placed already. */
export function placeOverviewLabel(
  box: LabelBox,
  viewport: LabelViewport,
  exclusions: readonly LabelExclusion[],
  earlierBoxes: readonly LabelBox[],
  earlierCount: number,
): void {
  box.visible = false;
  if (!(box.width > 0) || !Number.isFinite(box.left) || !Number.isFinite(box.top) ||
      !(viewport.width > 0) || viewport.height < LABEL_HEIGHT) return;

  const preferredLeft = box.left, preferredTop = box.top;
  const maxLeft = viewport.x + Math.max(0, viewport.width - box.width);
  const maxTop = viewport.y + viewport.height - LABEL_HEIGHT;

  for (const [horizontal, row] of OFFSETS) {
    const left = Math.max(viewport.x, Math.min(maxLeft, preferredLeft + horizontal * (box.width + 10)));
    const top = Math.max(viewport.y, Math.min(maxTop, preferredTop + row * VERTICAL_STEP));
    let blocked = false;
    for (const exclusion of exclusions) {
      if (exclusion.right > exclusion.left &&
          left < exclusion.right && left + box.width > exclusion.left &&
          top < exclusion.bottom && top + LABEL_HEIGHT > exclusion.top) {
        blocked = true;
        break;
      }
    }
    if (blocked) continue;
    for (let index = 0; index < earlierCount; index++) {
      const other = earlierBoxes[index];
      if (other.visible &&
          left < other.left + other.width + LABEL_GAP &&
          left + box.width + LABEL_GAP > other.left &&
          top < other.top + LABEL_HEIGHT + LABEL_GAP &&
          top + LABEL_HEIGHT + LABEL_GAP > other.top) {
        blocked = true;
        break;
      }
    }
    if (blocked) continue;
    box.left = left;
    box.top = top;
    box.visible = true;
    return;
  }
}
