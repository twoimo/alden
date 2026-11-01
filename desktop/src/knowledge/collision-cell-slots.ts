interface CellCoordinates { x: number; y: number; z: number }

/** Index the existing sparse cells without changing their frame or population.
 * Every occupied axis must leave a one-cell halo inside the 32^3 slot domain.
 * A null result requires the original sparse lookup; buffer contents then have
 * no meaning. The caller owns this reusable buffer for one synchronous layout. */
export function boundedCollisionCellSlots<T extends CellCoordinates>(
  cells: ReadonlyMap<number | string, T>, slots: Array<T | undefined>,
): Array<T | undefined> | null {
  for (const { x, y, z } of cells.values()) {
    if (!Number.isSafeInteger(x) || !Number.isSafeInteger(y) || !Number.isSafeInteger(z)
      || x < -15 || x > 14 || y < -15 || y > 14 || z < -15 || z > 14) return null;
  }
  slots.length = 32768;
  slots.fill(undefined);
  for (const cell of cells.values()) {
    const key = ((cell.x + 16) * 32 + cell.y + 16) * 32 + cell.z + 16;
    if (slots[key] !== undefined) return null;
    slots[key] = cell;
  }
  return slots;
}
