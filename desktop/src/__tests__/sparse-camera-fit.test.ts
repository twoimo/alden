// @vitest-environment happy-dom
import * as THREE from 'three';
import { describe, expect, it, vi } from 'vitest';
import { KnowledgeHologram } from '../knowledge/hologram';
import type { LayoutResponse } from '../knowledge/semantic-layout-driver';

function fixture() {
  const value = Object.assign(Object.create(KnowledgeHologram.prototype), {
    requestedAnimation: true, disposed: false,
    view: { hops: 0, focusId: null, nodes: [{ id: 'a' }, { id: 'b' }] },
    graph: {}, anchors: new Map([
      ['a', new THREE.Vector3(-.1, 0, 0)], ['b', new THREE.Vector3(.1, 0, 0)],
    ]), positions: new Map(), layoutChanged: new Set(['a', 'b']), layoutDiagnostics: null,
    viewport: { width: 440, height: 510 }, camera: new THREE.PerspectiveCamera(38, 440 / 510, .03, 80),
    desiredCamera: new THREE.Vector3(0, 0, 3.6), desiredLookAt: new THREE.Vector3(),
    orbit: { maxDistance: 12 }, orbitActive: false, restoredWindowSize: null,
    fitAfterSemanticLayout: false, preparePositionMotion: vi.fn(),
    provisionalOverviewPositions: new Set<string>(),
    loop: { start: vi.fn() }, invalidateFrame: vi.fn(),
  });
  return value as {
    resetOverviewCamera(): void; applySemanticLayout(result: LayoutResponse): void;
    desiredCamera: THREE.Vector3; desiredLookAt: THREE.Vector3; fitAfterSemanticLayout: boolean;
    orbitActive: boolean; restoredWindowSize: { width: number; height: number } | null;
    view: { nodes: { id: string }[] }; anchors: Map<string, THREE.Vector3>;
  };
}

const anchors: LayoutResponse['anchors'] = [
  ['a', { x: 1.8, y: 1.3, z: .1 }], ['b', { x: 2.1, y: .8, z: .1 }],
];
function project(value: ReturnType<typeof fixture>, point: { x: number; y: number; z: number }) {
  const camera = new THREE.PerspectiveCamera(38, 440 / 510, .03, 80);
  camera.position.copy(value.desiredCamera);
  camera.lookAt(value.desiredLookAt); camera.updateMatrixWorld(true);
  return new THREE.Vector3(point.x, point.y, point.z).project(camera);
}

describe('sparse fit after asynchronous layout', () => {
  it('finishes a seed-point fit using later anchors and makes both points visible', () => {
    const value = fixture(); value.resetOverviewCamera();
    expect(anchors!.every(([, p]) => Math.abs(project(value, p).x) > 1)).toBe(true);
    value.applySemanticLayout({ anchors } as LayoutResponse);
    expect(value.desiredLookAt.toArray()).toEqual([1.9500000000000002, 1.05, .1]);
    for (const [, point] of anchors!) {
      const screen = project(value, point);
      expect(Math.abs(screen.x)).toBeLessThan(1);
      expect(Math.abs(screen.y)).toBeLessThan(1);
      expect(screen.z).toBeGreaterThan(-1); expect(screen.z).toBeLessThan(1);
    }
    expect(value.fitAfterSemanticLayout).toBe(false);
    const pose = value.desiredCamera.toArray();
    value.applySemanticLayout({ anchors } as LayoutResponse);
    expect(value.desiredCamera.toArray()).toEqual(pose);
  });

  it.each(['orbit', 'saved pose', 'no requested fit'] as const)('preserves %s while anchors arrive', reason => {
    const value = fixture(); value.resetOverviewCamera();
    if (reason === 'orbit') value.orbitActive = true;
    if (reason === 'saved pose') value.restoredWindowSize = { width: 640, height: 680 };
    if (reason === 'no requested fit') value.fitAfterSemanticLayout = false;
    const before = [value.desiredCamera.toArray(), value.desiredLookAt.toArray()];
    value.applySemanticLayout({ anchors } as LayoutResponse);
    expect([value.desiredCamera.toArray(), value.desiredLookAt.toArray()]).toEqual(before);
    expect(value.fitAfterSemanticLayout).toBe(false);
  });

  it('keeps large overview fits outside the sparse completion policy', () => {
    const value = fixture();
    value.view.nodes = Array.from({ length: 25 }, (_, i) => ({ id: String(i) }));
    value.anchors = new Map(value.view.nodes.map(n => [n.id, new THREE.Vector3()]));
    value.resetOverviewCamera(); expect(value.fitAfterSemanticLayout).toBe(false);
  });

  it('finishes an explicit large-graph fit made from provisional points without refitting ordinary updates', () => {
    const value=fixture(), ids=Array.from({length:25},(_,i)=>String(i));
    value.view.nodes=ids.map(id=>({id}));
    value.anchors=new Map(ids.map((id,i)=>[id,new THREE.Vector3(i%2?2.2:-2.2,0,0)]));
    Object.assign(value,{provisionalOverviewPositions:new Set(ids)});
    value.resetOverviewCamera();const far=value.desiredCamera.distanceTo(value.desiredLookAt);
    expect(value.fitAfterSemanticLayout).toBe(true);
    const compact=ids.map((id,i)=>[id,{x:(i-12)*.025,y:0,z:0}] as [string,{x:number;y:number;z:number}]);
    value.applySemanticLayout({anchors:compact} as LayoutResponse);
    expect(value.desiredCamera.distanceTo(value.desiredLookAt)).toBeLessThan(far);
    expect(value.fitAfterSemanticLayout).toBe(false);
    const pose=value.desiredCamera.toArray();
    value.applySemanticLayout({anchors:compact} as LayoutResponse);
    expect(value.desiredCamera.toArray()).toEqual(pose);
    for(const [,point] of compact)expect(Math.abs(project(value,point).x)).toBeLessThan(1);
  });
});
