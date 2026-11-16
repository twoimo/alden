import * as THREE from "three";

/** Read a design token from the document root; the fallback covers a page without the stylesheet. */
export function cssColor(name: string, fallback: string): THREE.Color {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(value || fallback);
}

/** Point `index` of `count` spread evenly over a sphere along a golden-angle spiral. */
export function spherePoint(index: number, count: number, radius: number): THREE.Vector3 {
  const offset = 2 / Math.max(count, 1);
  const y = (index * offset - 1) + offset / 2;
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  const phi = index * Math.PI * (3 - Math.sqrt(5));
  return new THREE.Vector3(Math.cos(phi) * ring * radius, y * radius, Math.sin(phi) * ring * radius);
}

export function disposeObject(object: THREE.Object3D): void {
  object.traverse((child) => {
    if (child instanceof THREE.Mesh || child instanceof THREE.Points || child instanceof THREE.Line) {
      child.geometry.dispose();
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      materials.forEach((material) => material.dispose());
    }
  });
}
