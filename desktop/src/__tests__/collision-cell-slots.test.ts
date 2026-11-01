import { describe, expect, it } from 'vitest';
import { boundedCollisionCellSlots } from '../knowledge/collision-cell-slots';

type Cell = { x: number; y: number; z: number; label: string; members: readonly number[] };
const capacity = 32 ** 3;
const stringKey = (x: number, y: number, z: number) => `${x},${y},${z}`;
// Public slot-address contract; the oracle below uses only coordinate strings.
const address = (x: number, y: number, z: number) => (x + 16) * 1024 + (y + 16) * 32 + z + 16;
const cell = (x: number, y: number, z: number, label = stringKey(x, y, z)): Cell =>
  Object.freeze({ x, y, z, label, members: Object.freeze([label.length, 7]) });
const mapOf = (values: readonly Cell[]): Map<number | string, Cell> =>
  new Map(values.map((value, i) => [i % 2 ? `unrelated-key-${i}` : i, value]));
const buffer = () => new Array<Cell | undefined>(capacity);
const stale = cell(4, 5, 6, 'stale sentinel');

function unchanged(cells: ReadonlyMap<number | string, Cell>, before: Array<[number | string, Cell]>): void {
  expect([...cells]).toEqual(before);
  before.forEach(([key, value]) => expect(cells.get(key)).toBe(value));
}

function checkNeighbors(cells: ReadonlyMap<number | string, Cell>, slots: Array<Cell | undefined>): void {
  const before = [...cells];
  const oracle = new Map([...cells.values()].map(value => [stringKey(value.x, value.y, value.z), value]));
  expect(oracle.size).toBe(cells.size);
  const indexed = boundedCollisionCellSlots(cells, slots);
  expect(indexed === slots, 'must return the supplied buffer identity').toBe(true);
  if (!indexed) throw new Error('valid cells must produce the supplied slots');
  expect(indexed).toHaveLength(capacity);
  for (const value of cells.values()) {
    for (const dx of [-1, 0, 1]) for (const dy of [-1, 0, 1]) for (const dz of [-1, 0, 1]) {
      const x = value.x + dx, y = value.y + dy, z = value.z + dz;
      const at = address(x, y, z);
      expect(at).toBeGreaterThanOrEqual(0);
      expect(at).toBeLessThan(capacity);
      expect(indexed[at], `neighbor ${stringKey(x, y, z)}`).toBe(oracle.get(stringKey(x, y, z)));
    }
  }
  // Check the whole buffer, including slots no queried neighborhood can reach.
  const occupied = indexed.filter(value => value !== undefined);
  expect(occupied).toHaveLength(cells.size);
  expect(new Set(occupied)).toEqual(new Set(cells.values()));
  unchanged(cells, before);
}

describe('bounded collision cell slots', () => {
  it('matches all 27 string-Map neighbors at every face, edge and corner', () => {
    const values: Cell[] = [];
    for (const x of [-15, -14, -1, 0, 1, 13, 14])
      for (const y of [-15, -14, -1, 0, 1, 13, 14])
        for (const z of [-15, -14, -1, 0, 1, 13, 14]) values.push(cell(x, y, z));
    const slots = buffer().fill(stale);
    checkNeighbors(mapOf(values), slots);
    expect(address(-16, -16, -16)).toBe(0);
    expect(address(15, 15, 15)).toBe(capacity - 1);
    expect(slots[0]).toBeUndefined();
    expect(slots[capacity - 1]).toBeUndefined();
    expect(slots[address(0, 0, 0)]).toBe(values.find(value => value.label === '0,0,0'));
  });

  it('agrees with the string oracle for bounded seeded frames and changed insertion orders', () => {
    let seed = 0x51a7c0de;
    const random = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0);
    const slots = buffer();
    for (let frame = 0; frame < 12; frame++) {
      const unique = new Map<string, Cell>();
      // Local clusters provide positive neighbor matches as well as empty slots.
      const center = [random() % 28 - 14, random() % 28 - 14, random() % 28 - 14];
      for (const dx of [-1, 0, 1]) for (const dy of [-1, 0, 1]) for (const dz of [-1, 0, 1]) {
        const value = cell(center[0] + dx, center[1] + dy, center[2] + dz);
        unique.set(value.label, value);
      }
      for (let draw = 0; draw < 160; draw++) {
        const value = cell(random() % 30 - 15, random() % 30 - 15, random() % 30 - 15);
        unique.set(value.label, value);
      }
      const values = [...unique.values()];
      if (frame % 2) values.reverse();
      checkNeighbors(mapOf(values), slots);
    }
  });

  const rejectedNumbers = [
    -16, 15, -17, 16, -32, 32, -0.5, 0.5, -15.000001, 14.000001,
    Number.MIN_VALUE, -Number.MIN_VALUE, NaN, Infinity, -Infinity,
    Number.MAX_SAFE_INTEGER, Number.MIN_SAFE_INTEGER, 2 ** 53, -(2 ** 53),
    2 ** 32, -(2 ** 32), Number.MAX_VALUE, -Number.MAX_VALUE,
  ];
  for (const axis of ['x', 'y', 'z'] as const) {
    it.each(rejectedNumbers)(`rejects invalid ${axis}=%s without changing cells`, value => {
      const bad = Object.freeze({ ...cell(0, 0, 0, 'invalid'), [axis]: value });
      for (const values of [[cell(1, 2, 3), bad], [bad, cell(1, 2, 3)]]) {
        const cells = mapOf(values), before = [...cells];
        expect(boundedCollisionCellSlots(cells, buffer().fill(stale))).toBeNull();
        // A null buffer is intentionally not inspected or used for lookups.
        unchanged(cells, before);
      }
    });

    it.each(['0', null, undefined, true, [], new Number(0)])(
      `requires an actual number on ${axis}, without coercion (%s)`, value => {
        const bad = Object.freeze({ ...cell(0, 0, 0, 'coercible'), [axis]: value }) as Cell;
        const cells = mapOf([bad]), before = [...cells];
        expect(boundedCollisionCellSlots(cells, buffer())).toBeNull();
        unchanged(cells, before);
      },
    );
  }

  it('uses cell coordinates rather than unrelated numeric or coordinate-looking Map keys', () => {
    const a = cell(-15, 14, 0), b = cell(1, 2, 3), c = cell(14, -15, -1), d = cell(0, 0, 0);
    const cells = new Map<number | string, Cell>([
      [address(1, 2, 3), a], ['-15,14,0', b], ['0,0,0', c], [0, d],
    ]);
    checkNeighbors(cells, buffer());
  });

  it.each([
    [1, -32, 0], [-1, 32, 0], [0, 1, -32], [0, -1, 32],
    [0.5, -16, 0], [0, 0.5, -16],
  ])('rejects axis carries/aliases at (%s,%s,%s) even when the flattened address is valid', (x, y, z) => {
    expect(address(x, y, z)).toBe(address(0, 0, 0));
    for (const values of [[cell(x, y, z)], [cell(0, 0, 0), cell(x, y, z)]]) {
      const cells = mapOf(values), before = [...cells];
      expect(boundedCollisionCellSlots(cells, buffer())).toBeNull();
      unchanged(cells, before);
    }
  });

  it('rejects duplicate coordinates with distinct keys and distinct cell references', () => {
    const cells = new Map<number | string, Cell>([
      [1, cell(-15, 14, 2, 'first')], ['1', cell(-15, 14, 2, 'second')],
    ]), before = [...cells];
    expect(boundedCollisionCellSlots(cells, buffer())).toBeNull();
    unchanged(cells, before);
  });

  it('rejects the same cell reference under two keys', () => {
    const value = cell(2, 3, 4), cells = new Map<number | string, Cell>([[0, value], ['other', value]]);
    expect(boundedCollisionCellSlots(cells, buffer())).toBeNull();
    unchanged(cells, [[0, value], ['other', value]]);
  });

  it('handles negative zero like the canonical string Map while preserving its sign', () => {
    const value = cell(-0, -0, -0);
    const slots = buffer();
    checkNeighbors(mapOf([value, cell(-1, 0, 1)]), slots);
    expect(slots[address(0, 0, 0)]).toBe(value);
    expect(Object.is(value.x, -0) && Object.is(value.y, -0) && Object.is(value.z, -0)).toBe(true);
    expect(boundedCollisionCellSlots(mapOf([value, cell(0, 0, 0)]), slots)).toBeNull();
  });

  it('reuses the exact buffer and clears all stale slots across successive valid frames', () => {
    const slots = buffer().fill(stale);
    checkNeighbors(mapOf([cell(-15, 14, 2), cell(2, -15, 14), cell(14, 2, -15)]), slots);
    checkNeighbors(mapOf([cell(0, 1, -1)]), slots);
    checkNeighbors(mapOf([cell(0, 1, -1, 'replacement reference')]), slots);
    checkNeighbors(new Map(), slots);
    expect(slots.every(value => value === undefined)).toBe(true);
  });

  it('cleans the whole buffer after a rejected guard and a duplicate-coordinate fallback', () => {
    const slots = buffer();
    checkNeighbors(mapOf([cell(12, 13, 14)]), slots);
    expect(boundedCollisionCellSlots(mapOf([cell(0, 0, 0), cell(15, 0, 0)]), slots)).toBeNull();
    checkNeighbors(mapOf([cell(-15, -14, -13)]), slots);
    expect(boundedCollisionCellSlots(mapOf([cell(3, 4, 5), cell(3, 4, 5, 'duplicate')]), slots)).toBeNull();
    checkNeighbors(mapOf([cell(8, 9, 10)]), slots);
    expect(boundedCollisionCellSlots(mapOf([cell(3, 4, 5), cell(3, 4, 5, 'duplicate')]), slots)).toBeNull();
    checkNeighbors(new Map(), slots);
  });

  it('keeps independently owned buffers isolated', () => {
    const a = buffer(), b = buffer(), first = cell(1, 2, 3);
    checkNeighbors(mapOf([first]), a);
    checkNeighbors(mapOf([cell(-1, -2, -3)]), b);
    expect(a[address(1, 2, 3)]).toBe(first);
    expect(b[address(1, 2, 3)]).toBeUndefined();
    checkNeighbors(new Map(), b);
    expect(a[address(1, 2, 3)]).toBe(first);
  });

  it('accepts an empty map and clears even a fully dirty supplied buffer', () => {
    checkNeighbors(new Map(), buffer().fill(stale));
  });
});
