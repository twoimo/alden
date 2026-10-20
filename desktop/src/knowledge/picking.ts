import * as THREE from "three";

const sphere = new THREE.Sphere();
const point = new THREE.Vector3();

/** Captions can be displaced far from their sphere to avoid collisions.
 * Read their final displayed bounds only on a click, never in the frame loop. */
export function pickKnowledgeLabel(
  clientX: number, clientY: number, labels: Iterable<readonly [string, HTMLElement]>,
): string | null {
  for (const [id, label] of labels) {
    if (label.hidden) continue;
    const rect = label.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0
      && clientX >= rect.left && clientX <= rect.right
      && clientY >= rect.top && clientY <= rect.bottom) return id;
  }
  return null;
}

/** The rendered knowledge nodes are spheres. Pick their surface directly;
 * triangulated equator/seam boundaries can miss at floating-point precision.
 * Decorative stars are never supplied to this picker. */
export function pickKnowledgeSphere(raycaster: THREE.Raycaster, meshes: Iterable<THREE.Mesh>): THREE.Mesh | null {
  let nearest = raycaster.far;
  let selected: THREE.Mesh | null = null;
  for (const mesh of meshes) {
    if (!mesh.layers.test(raycaster.layers)) continue;
    mesh.updateWorldMatrix(true, false);
    if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
    if (!mesh.geometry.boundingSphere) continue;
    sphere.copy(mesh.geometry.boundingSphere).applyMatrix4(mesh.matrixWorld);
    if (!raycaster.ray.intersectSphere(sphere, point)) continue;
    const distance = raycaster.ray.origin.distanceTo(point);
    if (distance < raycaster.near || distance > nearest) continue;
    nearest = distance;
    selected = mesh;
  }
  return selected;
}
