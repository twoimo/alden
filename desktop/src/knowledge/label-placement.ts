/**
 * Overview label-only placement in canvas-local screen pixels.
 * Nodes, edges, semantic positions and keyboard targets are never filtered.
 * The caller projects/measures labels once and invokes this with cached widths.
 */
export type LabelBox = { width: number; height?: number; left: number; top: number; visible: boolean };
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
const ANCHORED_OFFSETS = [[0, 0], [0, -1], [0, 1], [-1, 0], [1, 0],
  [-1, -1], [1, -1], [-1, 1], [1, 1]] as const;
export type LabelPlacement = 'legacy' | 'anchored' | 'active';

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
  placement: LabelPlacement = 'legacy',
): void {
  box.visible = false;
  const height = box.height ?? LABEL_HEIGHT;
  if (!Number.isFinite(box.width) || !Number.isFinite(height) || !Number.isFinite(box.left) || !Number.isFinite(box.top) ||
      !Number.isFinite(viewport.x) || !Number.isFinite(viewport.y) || !Number.isFinite(viewport.width) || !Number.isFinite(viewport.height) ||
      !(box.width > 0) || !(height > 0) || box.width > viewport.width || height > viewport.height) return;

  const preferredLeft = box.left, preferredTop = box.top;
  const maxLeft = viewport.x + Math.max(0, viewport.width - box.width);
  const maxTop = viewport.y + viewport.height - height;
  const verticalStep = Math.max(VERTICAL_STEP, height + LABEL_GAP + 1);

  const anchored = placement !== 'legacy';
  const offsets = anchored ? ANCHORED_OFFSETS : OFFSETS;
  for (const [horizontal, row] of offsets) {
    const left = Math.max(viewport.x, Math.min(maxLeft, preferredLeft + horizontal * (anchored ? 18 : box.width + 10)));
    const top = Math.max(viewport.y, Math.min(maxTop, preferredTop + row * verticalStep));
    // A topic must remain beside its own projected node. Cull crowded labels
    // instead of manufacturing a detached list up to three rows away.
    if (anchored && (Math.abs(left - preferredLeft) > 18 || Math.abs(top - preferredTop) >
      (placement === 'active' ? 54 : 36))) continue;
    let blocked = false;
    for (const exclusion of exclusions) {
      if (exclusion.right > exclusion.left &&
          left < exclusion.right && left + box.width > exclusion.left &&
          top < exclusion.bottom && top + height > exclusion.top) {
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
          top < other.top + (other.height ?? LABEL_HEIGHT) + LABEL_GAP &&
          top + height + LABEL_GAP > other.top) {
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

/** Shift a screen point away from the viewport center. Near the center, stay put.
 * Display placement only; stored graph coordinates are untouched. */
export function outwardLabelPoint(
  screenX: number, screenY: number, viewport: LabelViewport, distance: number,
): { x: number; y: number } {
  if (!Number.isFinite(screenX) || !Number.isFinite(screenY) || !Number.isFinite(distance) || distance <= 0
      || !Number.isFinite(viewport.width) || !Number.isFinite(viewport.height) || viewport.width <= 0 || viewport.height <= 0) {
    return { x: screenX, y: screenY };
  }
  const centerX = viewport.x + viewport.width / 2, centerY = viewport.y + viewport.height / 2;
  let dx = screenX - centerX, dy = screenY - centerY;
  const length = Math.hypot(dx, dy);
  if (!(length > 64)) return { x: screenX, y: screenY };
  const step = Math.min(distance, length * .22);
  return { x: screenX + dx / length * step, y: screenY + dy / length * step };
}
