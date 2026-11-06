// El mar: malla en anillos (densa cerca del jugador, rala a lo lejos) con un shader barato:
// olas en el vértice, fresnel, brillo del sol y espuma donde el mapa de profundidad dice que hay poca agua.
import { BufferAttribute, BufferGeometry, Color, DoubleSide, Mesh, ShaderMaterial, UniformsLib, UniformsUtils, type Texture } from "three";
import { COLOR_CENIT, COLOR_HORIZONTE, COLOR_SOL, DIR_SOL } from "./luz";
import { MAPA_LADO } from "./terreno";

function crearAnillos(): BufferGeometry {
  const radiosN = 44;
  const seg = 72;
  const rMax = 760;
  const rMin = 1.5;
  const pos: number[] = [0, 0, 0];
  for (let i = 0; i < radiosN; i++) {
    const r = rMin * Math.pow(rMax / rMin, i / (radiosN - 1));
    for (let j = 0; j < seg; j++) {
      const a = (j / seg) * Math.PI * 2;
      pos.push(Math.cos(a) * r, 0, Math.sin(a) * r);
    }
  }
  const idx: number[] = [];
  for (let j = 0; j < seg; j++) idx.push(0, 1 + ((j + 1) % seg), 1 + j);
  for (let i = 0; i < radiosN - 1; i++) {
    for (let j = 0; j < seg; j++) {
      const a = 1 + i * seg + j;
      const b = 1 + i * seg + ((j + 1) % seg);
      const c = a + seg;
      const d = b + seg;
      idx.push(a, b, c, b, d, c);
    }
  }
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(new Float32Array(pos), 3));
  g.setIndex(idx);
  return g;
}

const VERTICE = /* glsl */ `
uniform float uTime;
varying vec3 vW;
#include <fog_pars_vertex>
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  wp.y += sin(wp.x * 0.21 + uTime * 1.1) * 0.12 + sin(wp.z * 0.17 - uTime * 0.9) * 0.1 + sin((wp.x + wp.z) * 0.35 + uTime * 1.7) * 0.05;
  vW = wp.xyz;
  vec4 mvPosition = viewMatrix * wp;
  gl_Position = projectionMatrix * mvPosition;
  #include <fog_vertex>
}`;

const FRAGMENTO = /* glsl */ `
uniform float uTime;
uniform sampler2D uProf;
uniform vec3 uSol;
uniform vec3 uColSol;
uniform vec3 uHor;
uniform vec3 uCenit;
uniform vec3 uProfundo;
uniform vec3 uSomero;
varying vec3 vW;
#include <fog_pars_fragment>
void main() {
  float t = uTime;
  float gx = cos(vW.x * 0.21 + t * 1.1) * 0.0252 + cos((vW.x + vW.z) * 0.35 + t * 1.7) * 0.0175 + cos(vW.x * 1.3 + t * 2.1) * 0.016;
  float gz = cos(vW.z * 0.17 - t * 0.9) * 0.017 + cos((vW.x + vW.z) * 0.35 + t * 1.7) * 0.0175 + cos(vW.z * 1.1 - t * 1.8) * 0.014;
  vec3 n = normalize(vec3(-gx * 6.0, 1.0, -gz * 6.0));
  vec3 v = normalize(cameraPosition - vW);
  float fres = 0.04 + 0.96 * pow(1.0 - max(dot(n, v), 0.0), 4.0);
  float prof = -(texture2D(uProf, (vW.xz + ${MAPA_LADO / 2}.0) / ${MAPA_LADO}.0).r * 12.0 - 6.0);
  vec3 cuerpo = mix(uSomero, uProfundo, smoothstep(0.0, 5.0, prof));
  vec3 cielo = mix(uHor, uCenit, 0.35);
  vec3 col = mix(cuerpo, cielo, clamp(fres, 0.0, 0.85));
  float brillo = pow(max(dot(reflect(-v, n), uSol), 0.0), 120.0);
  col += uColSol * brillo * 1.6;
  float f = 1.0 - smoothstep(0.0, 0.4, prof + 0.18 * sin(t * 1.4 + vW.x * 0.9) + 0.12 * sin(t * 1.1 + vW.z * 1.1));
  float rizo = smoothstep(0.2, 0.6, 0.5 + 0.5 * sin(vW.x * 2.7 + vW.z * 2.1 + t * 0.8 + 3.0 * sin(vW.x * 0.9 - vW.z * 1.3 + t * 0.3)));
  float espuma = clamp(f * (0.55 + 0.45 * rizo), 0.0, 1.0);
  col = mix(col, vec3(1.0, 0.97, 0.9), espuma * 0.9);
  float alfa = mix(0.35, 0.97, smoothstep(0.0, 3.0, prof));
  alfa = max(alfa, espuma);
  gl_FragColor = vec4(col, alfa);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  #include <fog_fragment>
}`;

export interface Mar {
  malla: Mesh;
  actualizar(t: number, x: number, z: number): void;
}

export function crearMar(profundidad: Texture): Mar {
  const uniforms = UniformsUtils.merge([
    UniformsLib.fog,
    {
      uTime: { value: 0 },
      uProf: { value: profundidad },
      uSol: { value: DIR_SOL },
      uColSol: { value: COLOR_SOL },
      uHor: { value: COLOR_HORIZONTE },
      uCenit: { value: COLOR_CENIT },
      uProfundo: { value: new Color(0x0e5a7a) },
      uSomero: { value: new Color(0x45c8b8) },
    },
  ]);
  const material = new ShaderMaterial({
    uniforms,
    vertexShader: VERTICE,
    fragmentShader: FRAGMENTO,
    transparent: true,
    depthWrite: false,
    fog: true,
    side: DoubleSide,
  });
  const malla = new Mesh(crearAnillos(), material);
  malla.frustumCulled = false;
  malla.renderOrder = 1;
  return {
    malla,
    actualizar(t, x, z) {
      uniforms.uTime.value = t;
      malla.position.set(x, 0, z);
    },
  };
}
