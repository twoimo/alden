import * as THREE from "three";

/**
 * A deterministic, static sky, independent of graph data and hit tests.
 * Owns one geometry and one material: ordinary traversal/disposal releases both.
 * No textures, shared GPU resources, update callbacks or external assets.
 */
export function createCosmosBackdrop(): THREE.Group {
  const sky = new THREE.Group();
  sky.name = "cosmos-backdrop";
  sky.renderOrder = -1000;
  const count = 1536;
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const sizes = new Float32Array(count);
  const cold = new THREE.Color("#cad8e9");
  const silver = new THREE.Color("#e7edf5");
  const warm = new THREE.Color("#e6cfac");
  let seed = 0x41d3c0de;
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 0x100000000;
  };
  for (let i = 0; i < count; i++) {
    const z = random() * 2 - 1;
    const angle = random() * Math.PI * 2;
    const depth = random();
    const radius = 18 + depth * 12;
    const ring = Math.sqrt(1 - z * z);
    positions.set([Math.cos(angle) * ring * radius, z * radius, Math.sin(angle) * ring * radius], i * 3);
    const color = i % 19 === 0 ? warm : i % 3 === 0 ? silver : cold;
    const brightness = 0.4 + random() * 0.6;
    colors.set([color.r * brightness, color.g * brightness, color.b * brightness], i * 3);
    // Small, bounded pixel sizes keep zooming from turning stars into graph nodes.
    sizes[i] = 1.5 + (1 - depth) * 1.1 + random() * 0.5;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.setAttribute("size", new THREE.BufferAttribute(sizes, 1));
  const material = new THREE.ShaderMaterial({
    name: "AldenStaticStars",
    vertexColors: true,
    transparent: true,
    blending: THREE.NormalBlending,
    depthWrite: false,
    depthTest: true,
    toneMapped: false,
    vertexShader: `
      attribute float size;
      varying vec3 starColor;
      void main() {
        starColor = color;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        // Always behind foreground geometry, including while the camera travels.
        gl_Position.z = gl_Position.w * 0.99999;
        gl_PointSize = size;
      }
    `,
    fragmentShader: `
      varying vec3 starColor;
      void main() {
        float radius = length(gl_PointCoord * 2.0 - 1.0);
        if (radius >= 1.0) discard;
        float alpha = (1.0 - smoothstep(0.0, 1.0, radius)) * 0.58;
        gl_FragColor = vec4(starColor, alpha);
        #include <colorspace_fragment>
      }
    `,
  });
  const stars = new THREE.Points(geometry, material);
  stars.name = "cosmos-stars";
  stars.renderOrder = sky.renderOrder;
  // The shader controls depth; CPU bounds cannot represent that clip-space sky.
  stars.frustumCulled = false;
  stars.raycast = () => undefined;
  sky.add(stars);
  return sky;
}
