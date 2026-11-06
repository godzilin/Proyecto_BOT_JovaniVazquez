// El cielo: cúpula con degradado y sol, y unas pocas nubes de bajo poligonaje que derivan despacio.
import { BackSide, Color, Group, IcosahedronGeometry, Mesh, MeshBasicMaterial, ShaderMaterial, SphereGeometry } from "three";
import { fundir, pintar, poner } from "./geom";
import { COLOR_CENIT, COLOR_HORIZONTE, COLOR_SOL, DIR_SOL } from "./luz";
import { mulberry32 } from "./ruido";

const VERTICE = /* glsl */ `
varying vec3 vDir;
void main() {
  vDir = position;
  gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
}`;

const FRAGMENTO = /* glsl */ `
uniform vec3 uHor;
uniform vec3 uCenit;
uniform vec3 uSol;
uniform vec3 uColSol;
varying vec3 vDir;
void main() {
  vec3 d = normalize(vDir);
  vec3 col = mix(uHor, uCenit, pow(clamp(d.y, 0.0, 1.0), 0.5));
  float s = max(dot(d, uSol), 0.0);
  col += uColSol * (pow(s, 8.0) * 0.28 + pow(s, 64.0) * 0.35);
  col = mix(col, vec3(1.0, 0.96, 0.85), smoothstep(0.9993, 0.9998, s));
  gl_FragColor = vec4(col, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

export interface Cielo {
  objetos: [Mesh, Group];
  actualizar(dt: number, x: number, y: number, z: number): void;
}

export function crearCielo(): Cielo {
  const domo = new Mesh(
    new SphereGeometry(900, 24, 12),
    new ShaderMaterial({
      uniforms: { uHor: { value: COLOR_HORIZONTE }, uCenit: { value: COLOR_CENIT }, uSol: { value: DIR_SOL }, uColSol: { value: COLOR_SOL } },
      vertexShader: VERTICE,
      fragmentShader: FRAGMENTO,
      side: BackSide,
      depthWrite: false,
      fog: false,
    }),
  );
  domo.frustumCulled = false;
  domo.renderOrder = -1;

  const rng = mulberry32(777);
  const blobs = [];
  for (let i = 0; i < 9; i++) {
    const a = rng() * Math.PI * 2;
    const d = 220 + rng() * 380;
    const cx = Math.cos(a) * d;
    const cz = Math.sin(a) * d;
    const cy = 120 + rng() * 60;
    const piezas = 3 + Math.floor(rng() * 3);
    for (let k = 0; k < piezas; k++) {
      const s = 22 + rng() * 22;
      blobs.push(
        pintar(poner(new IcosahedronGeometry(1, 1), [cx + (k - piezas / 2) * 26, cy + rng() * 5, cz + (rng() - 0.5) * 18], [0, 0, 0], [s * 1.3, s * 0.42, s * 0.9]), (_x, y) =>
          new Color(0xfff1de).lerp(new Color(0xf0b9a0), y < cy ? 0.55 : 0),
        ),
      );
    }
  }
  const nubes = new Group();
  nubes.add(new Mesh(fundir(blobs), new MeshBasicMaterial({ vertexColors: true, fog: true })));

  return {
    objetos: [domo, nubes],
    actualizar(dt, x, y, z) {
      domo.position.set(x, y, z);
      nubes.position.set(x, 0, z);
      nubes.rotation.y += dt * 0.004;
    },
  };
}
