import { describe, expect, it } from 'vitest';
import { PositionTransition } from '../knowledge/position-transition';

describe('nonphysical visible node targets', () => {
  it('updates a node after the 120-node physics window while preserving other nodes and rests naturally', () => {
    const positions = new Map<string, { x: number; y: number; z: number }>(Array.from({ length: 2048 }, (_, i) => ['n' + i, { x: 0, y: 0, z: 0 }]));
    const targets = new Map([...positions].map(([id, point]) => [id, { ...point }]));
    targets.set('n2047', { x: .6, y: -.5, z: .2 }); targets.set('n0', { x: 1, y: 0, z: 0 });
    const transition = new PositionTransition();
    expect(transition.setTargets(targets, positions, new Set(Array.from({ length: 120 }, (_, i) => 'n' + i)))).toBe(true);
    expect(transition.pending).toBe(1);
    transition.advance(positions, 1 / 60);
    expect(positions.get('n2047')!.x).toBeGreaterThan(0);
    expect(positions.get('n2047')!.x).toBeLessThan(.6);
    expect(positions.get('n0')).toEqual({ x: 0, y: 0, z: 0 });
    for (let i = 0; i < 180; i++) transition.advance(positions, 1 / 60);
    expect(transition.moving).toBe(false);
    expect(positions.get('n2047')).toEqual(targets.get('n2047'));
    const settled = JSON.stringify([...positions]);
    expect(transition.advance(positions, 1 / 60)).toBe(false);
    expect(JSON.stringify([...positions])).toBe(settled);
  });

  it('replaces pending targets without resetting the current position and rejects malformed snapshots', () => {
    const positions = new Map([['a', { x: 0, y: 0, z: 0 }]]), transition = new PositionTransition();
    transition.setTargets(new Map([['a', { x: 1, y: 0, z: 0 }]]), positions, new Set());
    transition.advance(positions, .1); const current = { ...positions.get('a')! };
    transition.setTargets(new Map([['a', { x: -.5, y: 0, z: 0 }]]), positions, new Set());
    expect(positions.get('a')).toEqual(current);
    transition.advance(positions, .1);
    expect(positions.get('a')!.x).toBeLessThan(current.x);
    expect(transition.setTargets(new Map([['a', { x: NaN, y: 0, z: 0 }]]), positions, new Set())).toBe(false);
    expect(transition.moving).toBe(false);
  });

  it('supports reduced motion, invalid elapsed time and removal without creating nodes', () => {
    const positions = new Map([['a', { x: 0, y: 0, z: 0 }]]), transition = new PositionTransition();
    transition.setTargets(new Map([['a', { x: 1, y: 0, z: 0 }], ['absent', { x: 2, y: 0, z: 0 }]]), positions, new Set());
    expect(transition.pending).toBe(1);
    for (const dt of [NaN, Infinity, -1, 0]) expect(transition.advance(positions, dt)).toBe(false);
    transition.advance(positions, 0, true); expect(positions.get('a')!.x).toBe(1); expect(transition.moving).toBe(false);
    transition.setTargets(new Map([['a', { x: -1, y: 0, z: 0 }]]), positions, new Set());
    positions.delete('a'); transition.advance(positions, .1); expect(transition.moving).toBe(false); expect(positions.size).toBe(0);
  });
});
