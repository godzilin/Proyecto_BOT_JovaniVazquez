// Vegetación e instancias: pinos canarios, palmeras, dragos, cardones, tabaibas y rocas.
// Cada especie y celda es una malla instanciada con el color en los vértices; las celdas se descartan por separado.
import {
  BufferGeometry,
  Color,
  ConeGeometry,
  CylinderGeometry,
  DodecahedronGeometry,
  IcosahedronGeometry,
  InstancedMesh,
  Matrix4,
  MeshLambertMaterial,
  Object3D,
} from "three";
import { añadirCirculo } from "./colision";
import { celdaDe } from "./culling";
import { fundir, pintar, poner } from "./geom";
import { hash2, mulberry32, smoothstep } from "./ruido";
import { BAND, BENTAYGA, CANTERAS, CHARCA, DUNAS, FARO, MOGAN, MUELLE, NUBLO, PICO, PLAZA, heightAt, pendienteEn, sueloEn } from "./terreno";

export const DRAGO = { x: PLAZA.x - 7, z: PLAZA.z + 4 };
const tau = Math.PI * 2;

function pino(): BufferGeometry {
  const gs = [pintar(poner(new CylinderGeometry(0.2, 0.32, 5, 6), [0, 2.5, 0]), 0x6b4a33)];
  const tiers: [number, number, number][] = [
    [2.3, 2.6, 3.4],
    [1.9, 2.4, 4.7],
    [1.45, 2.2, 5.9],
    [0.95, 2.0, 7.0],
  ];
  tiers.forEach(([r, h, y], i) => gs.push(pintar(poner(new ConeGeometry(r, h, 7), [0, y, 0], [0, i, 0]), i % 2 ? 0x5a8a45 : 0x4d7a3c)));
  return fundir(gs);
}

function palmera(): BufferGeometry {
  const gs: BufferGeometry[] = [];
  for (let i = 0; i < 5; i++) {
    gs.push(pintar(poner(new CylinderGeometry(0.2 - i * 0.014, 0.23 - i * 0.014, 1.4, 6), [i * 0.13, 0.7 + i * 1.35, 0]), i % 2 ? 0x8a6b4a : 0x7a5d40));
  }
  const coronaX = 5 * 0.13;
  for (let i = 0; i < 8; i++) {
    const hoja = new ConeGeometry(0.5, 3.6, 4);
    hoja.translate(0, 1.8, 0);
    hoja.scale(1, 1, 0.22);
    gs.push(pintar(poner(hoja, [coronaX, 6.8, 0], [0, (i / 8) * tau, 1.25 + (i % 2) * 0.25]), i % 2 ? 0x5c9a3f : 0x4f8c38));
  }
  gs.push(pintar(poner(new IcosahedronGeometry(0.35, 0), [coronaX, 6.7, 0]), 0x6b4a33));
  return fundir(gs);
}

function drago(): BufferGeometry {
  const gs = [pintar(poner(new CylinderGeometry(0.5, 0.75, 2.6, 7), [0, 1.3, 0]), 0x8b7a6a)];
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * tau + 0.4;
    const rama = new CylinderGeometry(0.16, 0.3, 1.9, 5);
    rama.translate(0, 0.95, 0);
    const bx = Math.cos(a) * 0.95;
    const bz = Math.sin(a) * 0.95;
    gs.push(pintar(poner(rama, [0, 2.4, 0], [Math.sin(a) * 0.75, 0, -Math.cos(a) * 0.75]), 0x8b7a6a));
    gs.push(pintar(poner(new IcosahedronGeometry(1.45, 0), [bx * 1.4, 4.3, bz * 1.4], [0, a, 0], [1, 0.5, 1]), i % 2 ? 0x6a9a78 : 0x5f8f6e));
  }
  gs.push(pintar(poner(new IcosahedronGeometry(1.5, 0), [0, 4.7, 0], [0, 0, 0], [1, 0.5, 1]), 0x73a382));
  return fundir(gs);
}

function cardon(): BufferGeometry {
  const gs = [pintar(poner(new CylinderGeometry(0.34, 0.4, 3.4, 6), [0, 1.7, 0]), 0x6f9a55)];
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * tau + 0.3;
    const h = 1.0 + i * 0.35;
    const dx = Math.cos(a);
    const dz = Math.sin(a);
    gs.push(pintar(poner(new CylinderGeometry(0.22, 0.24, 0.9, 6), [dx * 0.6, h, dz * 0.6], [dz * 1.25, 0, -dx * 1.25]), 0x78a35d));
    gs.push(pintar(poner(new CylinderGeometry(0.2, 0.22, 1.6 + i * 0.3, 6), [dx * 1.0, h + 0.9 + i * 0.15, dz * 1.0]), 0x6f9a55));
  }
  return fundir(gs);
}

function tabaiba(): BufferGeometry {
  return fundir([
    pintar(poner(new IcosahedronGeometry(0.75, 0), [0, 0.5, 0], [0, 0, 0], [1.1, 0.8, 1.1]), 0x8da05a),
    pintar(poner(new IcosahedronGeometry(0.55, 0), [0.6, 0.35, 0.2], [0, 1, 0], [1, 0.8, 1]), 0xa3a86a),
    pintar(poner(new IcosahedronGeometry(0.5, 0), [-0.45, 0.35, -0.3], [0, 2, 0], [1, 0.8, 1]), 0x93a860),
  ]);
}

function roca(): BufferGeometry {
  const g = new DodecahedronGeometry(1, 0);
  const p = g.getAttribute("position");
  for (let i = 0; i < p.count; i++) {
    // Mismo desplazamiento para vértices iguales: la hoja no se rompe.
    const k = 0.78 + 0.4 * hash2(Math.round(p.getX(i) * 50), Math.round(p.getY(i) * 50 + p.getZ(i) * 31));
    p.setXYZ(i, p.getX(i) * k, p.getY(i) * k * 0.75, p.getZ(i) * k);
  }
  return pintar(poner(g, [0, 0.35, 0]), (x, y) => new Color(0x8a7f78).multiplyScalar(0.85 + y * 0.25 + x * 0.02));
}

interface Especie {
  geo: BufferGeometry;
  n: number;
  /** Distancia máxima a la que se dibuja (metros, hasta el borde de su celda): lo pequeño desaparece antes. */
  distancia: number;
  /** Devuelve la escala si el punto vale, o 0. */
  vale: (x: number, z: number, h: number, pend: number, dv: number, r: () => number) => number;
  radioTronco: number;
  tinte: [number, number];
  inclinar?: number;
}

const lejos = (x: number, z: number, cx: number, cz: number, r: number): boolean => Math.hypot(x - cx, z - cz) > r;

/** Terreno libre de construcciones y sitios reservados. */
function libre(x: number, z: number): boolean {
  const mx = x - MUELLE.cx;
  const mz = z - MUELLE.cz;
  return (
    lejos(x, z, PLAZA.x, PLAZA.z, 38) &&
    lejos(x, z, FARO.x, FARO.z, 9) &&
    lejos(x, z, NUBLO.x, NUBLO.z, 13) &&
    lejos(x, z, BENTAYGA.x, BENTAYGA.z, 6) &&
    lejos(x, z, CANTERAS.x, CANTERAS.z, 32) &&
    lejos(x, z, MOGAN.x, MOGAN.z, 24) &&
    lejos(x, z, BAND.x, BAND.z, BAND.radio + 8) &&
    mx * mx + mz * mz > 22 * 22
  );
}
const enDunas = (x: number, z: number): boolean => Math.hypot(x - DUNAS.x, z - DUNAS.z) < 38;
const sur = (z: number): number => smoothstep(18, -20, z); // 1 en el sur seco, 0 en el norte

const ESPECIES: Especie[] = [
  {
    geo: pino(),
    n: 190,
    distancia: 240,
    radioTronco: 0.45,
    tinte: [0.85, 1.1],
    vale: (x, z, h, p, dc) => (dc > 14 && dc < 80 && h > 9 && p < 0.8 && !enDunas(x, z) && libre(x, z) ? 0.8 + 0.7 * hash2(Math.round(x), Math.round(z)) : 0),
  },
  {
    geo: palmera(),
    n: 60,
    distancia: 200,
    radioTronco: 0.35,
    tinte: [0.9, 1.1],
    inclinar: 0.12,
    vale: (x, z, h, p) => {
      const charca = Math.hypot(x - CHARCA.x, z - CHARCA.z) < 20 && h > -0.2;
      const orilla = h > 0.9 && h < 3.2 && p < 0.35 && Math.hypot(x - DUNAS.x, z - DUNAS.z) < 55 && hash2(Math.round(z), Math.round(x)) < 0.4;
      return (charca || orilla) && libre(x, z) && hash2(Math.round(x), Math.round(z)) < 0.9 ? 0.85 + 0.35 * hash2(Math.round(z), Math.round(x)) : 0;
    },
  },
  {
    geo: drago(),
    n: 12,
    distancia: 260,
    radioTronco: 0.85,
    tinte: [0.9, 1.1],
    vale: (x, z, h, p, dc) => (h > 3 && h < 22 && p < 0.4 && dc > 30 && !enDunas(x, z) && libre(x, z) ? 0.9 + 0.5 * hash2(Math.round(x * 2), Math.round(z)) : 0),
  },
  {
    geo: cardon(),
    n: 90,
    distancia: 150,
    radioTronco: 0.5,
    tinte: [0.85, 1.1],
    vale: (x, z, h, p) => (h > 1.5 && h < 24 && p < 0.55 && sur(z) > 0.4 && !enDunas(x, z) && libre(x, z) ? 0.8 + 0.7 * hash2(Math.round(x), Math.round(z * 3)) : 0),
  },
  {
    geo: tabaiba(),
    n: 150,
    distancia: 110,
    radioTronco: 0,
    tinte: [0.85, 1.15],
    vale: (x, z, h, p) => (h > 1.2 && h < 26 && p < 0.7 && sur(z) > 0.2 && !enDunas(x, z) && libre(x, z) ? 0.8 + 0.8 * hash2(Math.round(z * 2), Math.round(x)) : 0),
  },
  {
    geo: roca(),
    n: 100,
    distancia: 110,
    radioTronco: 0,
    tinte: [0.75, 1.15],
    vale: (x, z, h, _p, dc, r) => (h > -0.3 && !enDunas(x, z) && libre(x, z) ? (0.4 + 1.4 * r() * r()) * (1 + smoothstep(60, 25, dc) * 0.6) : 0),
  },
];

export interface Vegetacion {
  malla: InstancedMesh;
  distancia: number;
}

/** La vegetación de la isla, en mallas instanciadas por especie y por celda de `celda` metros de lado. */
export function crearVegetacion(celda: number): Vegetacion[] {
  const rng = mulberry32(20261106);
  const material = new MeshLambertMaterial({ vertexColors: true, flatShading: true });
  const dummy = new Object3D();
  const out: Vegetacion[] = [];

  ESPECIES.forEach((esp, idx) => {
    const lista: { m: Matrix4; t: number }[] = [];
    const grande = idx === 2; // el drago de la plaza se añade aparte
    let intentos = 0;
    while (lista.length < esp.n && intentos++ < esp.n * 80) {
      const a = rng() * tau;
      const d = Math.sqrt(rng()) * 150;
      const x = Math.cos(a) * d;
      const z = Math.sin(a) * d;
      const h = heightAt(x, z);
      const e = esp.vale(x, z, h, pendienteEn(x, z), Math.hypot(x - PICO.x, z - PICO.z), rng);
      const tono = esp.tinte[0] + rng() * (esp.tinte[1] - esp.tinte[0]);
      const giro = rng() * tau;
      const incl = esp.inclinar ? (rng() - 0.5) * esp.inclinar : 0;
      if (e <= 0) continue;
      dummy.position.set(x, sueloEn(x, z) - 0.15 * e, z);
      dummy.rotation.set(incl, giro, incl * 0.7);
      dummy.scale.setScalar(e);
      dummy.updateMatrix();
      lista.push({ m: dummy.matrix.clone(), t: tono });
      if (esp.radioTronco > 0) añadirCirculo(x, z, esp.radioTronco * e);
      else if (idx === 5 && e > 0.95) añadirCirculo(x, z, e * 0.85);
    }
    if (grande) {
      const y = sueloEn(DRAGO.x, DRAGO.z);
      dummy.position.set(DRAGO.x, y, DRAGO.z);
      dummy.rotation.set(0, 0.6, 0);
      dummy.scale.setScalar(1.9);
      dummy.updateMatrix();
      lista.push({ m: dummy.matrix.clone(), t: 1 });
      añadirCirculo(DRAGO.x, DRAGO.z, 1.5);
    }
    // Una malla por celda: la esfera de sus instancias es la que descarta three, en la cámara y en la del sol.
    const celdas = new Map<string, { m: Matrix4; t: number }[]>();
    for (const it of lista) {
      const k = celdaDe(it.m.elements[12], it.m.elements[14], celda);
      const c = celdas.get(k);
      if (c) c.push(it);
      else celdas.set(k, [it]);
    }
    const color = new Color();
    for (const items of celdas.values()) {
      const mesh = new InstancedMesh(esp.geo, material, items.length);
      items.forEach((it, i) => {
        mesh.setMatrixAt(i, it.m);
        mesh.setColorAt(i, color.setScalar(it.t));
      });
      mesh.instanceMatrix.needsUpdate = true;
      mesh.castShadow = true;
      mesh.receiveShadow = idx !== 4;
      mesh.computeBoundingSphere();
      out.push({ malla: mesh, distancia: esp.distancia });
    }
  });
  return out;
}
