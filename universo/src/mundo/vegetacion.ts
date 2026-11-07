// Vegetación como datos: pinos canarios, palmeras, dragos, cardones, tabaibas y rocas.
// Cada especie es un asset compartido (piezas del formato del editor) y cada ejemplar, un objeto con su tinte y su transformación.
import { Color, DodecahedronGeometry, Euler, Quaternion } from "three";
import type { Asset, Malla, Pieza } from "../editor/formato";
import { pintar, poner } from "./geom";
import { type Aporte, aMalla, asset, icosaedro, objeto, prisma } from "./piezas";
import { hash2, mulberry32, smoothstep } from "./ruido";
import { BAND, BENTAYGA, CANTERAS, CHARCA, DUNAS, FARO, MOGAN, MUELLE, NUBLO, PICO, PLAZA, heightAt, pendienteEn, sueloEn } from "./terreno";

export const DRAGO = { x: PLAZA.x - 7, z: PLAZA.z + 4 };
const tau = Math.PI * 2;

const pino = (): Pieza[] => {
  const tiers: [number, number, number][] = [
    [2.3, 2.6, 3.4],
    [1.9, 2.4, 4.7],
    [1.45, 2.2, 5.9],
    [0.95, 2.0, 7.0],
  ];
  return [
    prisma(0.2, 0.32, 5, 6, 0x6b4a33, { pos: [0, 2.5, 0] }),
    ...tiers.map(([r, h, y], i) => prisma(0, r, h, 7, i % 2 ? 0x5a8a45 : 0x4d7a3c, { pos: [0, y, 0], rot: [0, i, 0] })),
  ];
};

const palmera = (): Pieza[] => {
  const ps: Pieza[] = [];
  for (let i = 0; i < 5; i++) {
    ps.push(prisma(0.2 - i * 0.014, 0.23 - i * 0.014, 1.4, 6, i % 2 ? 0x8a6b4a : 0x7a5d40, { pos: [i * 0.13, 0.7 + i * 1.35, 0] }));
  }
  const coronaX = 5 * 0.13;
  for (let i = 0; i < 8; i++) {
    // La hoja es un cono desplazado 1,8 hacia arriba antes de girarlo y aplastado en z.
    ps.push(
      prisma(0, 0.5, 3.6, 4, i % 2 ? 0x5c9a3f : 0x4f8c38, {
        pos: [coronaX, 6.8, 0],
        rot: [0, (i / 8) * tau, 1.25 + (i % 2) * 0.25],
        dentro: [0, 1.8, 0],
        geo: [1, 1, 0.22],
      }),
    );
  }
  ps.push(icosaedro(0.35, 0x6b4a33, { pos: [coronaX, 6.7, 0] }));
  return ps;
};

const drago = (): Pieza[] => {
  const ps = [prisma(0.5, 0.75, 2.6, 7, 0x8b7a6a, { pos: [0, 1.3, 0] })];
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * tau + 0.4;
    const bx = Math.cos(a) * 0.95;
    const bz = Math.sin(a) * 0.95;
    ps.push(prisma(0.16, 0.3, 1.9, 5, 0x8b7a6a, { pos: [0, 2.4, 0], rot: [Math.sin(a) * 0.75, 0, -Math.cos(a) * 0.75], dentro: [0, 0.95, 0] }));
    ps.push(icosaedro(1.45, i % 2 ? 0x6a9a78 : 0x5f8f6e, { pos: [bx * 1.4, 4.3, bz * 1.4], rot: [0, a, 0], escala: [1, 0.5, 1] }));
  }
  ps.push(icosaedro(1.5, 0x73a382, { pos: [0, 4.7, 0], escala: [1, 0.5, 1] }));
  return ps;
};

const cardon = (): Pieza[] => {
  const ps = [prisma(0.34, 0.4, 3.4, 6, 0x6f9a55, { pos: [0, 1.7, 0] })];
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * tau + 0.3;
    const h = 1.0 + i * 0.35;
    const dx = Math.cos(a);
    const dz = Math.sin(a);
    ps.push(prisma(0.22, 0.24, 0.9, 6, 0x78a35d, { pos: [dx * 0.6, h, dz * 0.6], rot: [dz * 1.25, 0, -dx * 1.25] }));
    ps.push(prisma(0.2, 0.22, 1.6 + i * 0.3, 6, 0x6f9a55, { pos: [dx * 1.0, h + 0.9 + i * 0.15, dz * 1.0] }));
  }
  return ps;
};

const tabaiba = (): Pieza[] => [
  icosaedro(0.75, 0x8da05a, { pos: [0, 0.5, 0], escala: [1.1, 0.8, 1.1] }),
  icosaedro(0.55, 0xa3a86a, { pos: [0.6, 0.35, 0.2], rot: [0, 1, 0], escala: [1, 0.8, 1] }),
  icosaedro(0.5, 0x93a860, { pos: [-0.45, 0.35, -0.3], rot: [0, 2, 0], escala: [1, 0.8, 1] }),
];

/** La roca abollada no sale de formas básicas: es una malla con el degradado de color por vértice. */
function mallaRoca(): Malla {
  const g = new DodecahedronGeometry(1, 0);
  const p = g.getAttribute("position");
  for (let i = 0; i < p.count; i++) {
    // Mismo desplazamiento para vértices iguales: la hoja no se rompe.
    const k = 0.78 + 0.4 * hash2(Math.round(p.getX(i) * 50), Math.round(p.getY(i) * 50 + p.getZ(i) * 31));
    p.setXYZ(i, p.getX(i) * k, p.getY(i) * k * 0.75, p.getZ(i) * k);
  }
  return aMalla(pintar(poner(g, [0, 0.35, 0]), (x, y) => new Color(0x8a7f78).multiplyScalar(0.85 + y * 0.25 + x * 0.02)));
}

interface Especie {
  /** Id del asset (la roca tiene además `roca-grande`). */
  id: string;
  /** Los assets de la especie, con su distancia y su colisión (el radio del tronco a escala 1). */
  assets: () => Record<string, Asset>;
  n: number;
  /** Distancia máxima a la que se dibuja (metros, hasta el borde de su celda): lo pequeño desaparece antes. */
  distancia: number;
  /** Devuelve la escala si el punto vale, o 0. */
  vale: (x: number, z: number, h: number, pend: number, dv: number, r: () => number) => number;
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
    id: "pino",
    assets: () => ({ pino: asset("Pino canario", pino(), [[0, 0, 0.45]], { distancia: 240 }) }),
    n: 190,
    distancia: 240,
    tinte: [0.85, 1.1],
    vale: (x, z, h, p, dc) => (dc > 14 && dc < 80 && h > 9 && p < 0.8 && !enDunas(x, z) && libre(x, z) ? 0.8 + 0.7 * hash2(Math.round(x), Math.round(z)) : 0),
  },
  {
    id: "palmera",
    assets: () => ({ palmera: asset("Palmera", palmera(), [[0, 0, 0.35]], { distancia: 200 }) }),
    n: 60,
    distancia: 200,
    tinte: [0.9, 1.1],
    inclinar: 0.12,
    vale: (x, z, h, p) => {
      const charca = Math.hypot(x - CHARCA.x, z - CHARCA.z) < 20 && h > -0.2;
      const orilla = h > 0.9 && h < 3.2 && p < 0.35 && Math.hypot(x - DUNAS.x, z - DUNAS.z) < 55 && hash2(Math.round(z), Math.round(x)) < 0.4;
      return (charca || orilla) && libre(x, z) && hash2(Math.round(x), Math.round(z)) < 0.9 ? 0.85 + 0.35 * hash2(Math.round(z), Math.round(x)) : 0;
    },
  },
  {
    id: "drago",
    assets: () => ({ drago: asset("Drago", drago(), [[0, 0, 0.85]], { distancia: 260 }) }),
    n: 12,
    distancia: 260,
    tinte: [0.9, 1.1],
    vale: (x, z, h, p, dc) => (h > 3 && h < 22 && p < 0.4 && dc > 30 && !enDunas(x, z) && libre(x, z) ? 0.9 + 0.5 * hash2(Math.round(x * 2), Math.round(z)) : 0),
  },
  {
    id: "cardon",
    assets: () => ({ cardon: asset("Cardón", cardon(), [[0, 0, 0.5]], { distancia: 150 }) }),
    n: 90,
    distancia: 150,
    tinte: [0.85, 1.1],
    vale: (x, z, h, p) => (h > 1.5 && h < 24 && p < 0.55 && sur(z) > 0.4 && !enDunas(x, z) && libre(x, z) ? 0.8 + 0.7 * hash2(Math.round(x), Math.round(z * 3)) : 0),
  },
  {
    id: "tabaiba",
    assets: () => ({ tabaiba: asset("Tabaiba", tabaiba(), null, { distancia: 110 }) }),
    n: 150,
    distancia: 110,
    tinte: [0.85, 1.15],
    vale: (x, z, h, p) => (h > 1.2 && h < 26 && p < 0.7 && sur(z) > 0.2 && !enDunas(x, z) && libre(x, z) ? 0.8 + 0.8 * hash2(Math.round(z * 2), Math.round(x)) : 0),
  },
  {
    id: "roca",
    // Solo chocan las de escala mayor de 0,95: dos assets con la misma malla, uno con colisión y otro sin ella.
    assets: () => {
      const malla = mallaRoca();
      return {
        roca: asset("Roca", [], null, { malla, distancia: 110 }),
        "roca-grande": asset("Roca grande", [], [[0, 0, 0.85]], { malla, distancia: 110 }),
      };
    },
    n: 100,
    distancia: 110,
    tinte: [0.75, 1.15],
    vale: (x, z, h, _p, dc, r) => (h > -0.3 && !enDunas(x, z) && libre(x, z) ? (0.4 + 1.4 * r() * r()) * (1 + smoothstep(60, 25, dc) * 0.6) : 0),
  },
];

const euler = new Euler();
const quat = new Quaternion();

/** Suma al aporte los assets de cada especie y un objeto por ejemplar, sorteados con las mismas semillas de siempre. */
export function crearVegetacion(a: Aporte): void {
  const rng = mulberry32(20261106);
  ESPECIES.forEach((esp) => {
    Object.assign(a.assets, esp.assets());
    let n = 0;
    let intentos = 0;
    while (n < esp.n && intentos++ < esp.n * 80) {
      const ang = rng() * tau;
      const d = Math.sqrt(rng()) * 150;
      const x = Math.cos(ang) * d;
      const z = Math.sin(ang) * d;
      const h = heightAt(x, z);
      const e = esp.vale(x, z, h, pendienteEn(x, z), Math.hypot(x - PICO.x, z - PICO.z), rng);
      const tono = esp.tinte[0] + rng() * (esp.tinte[1] - esp.tinte[0]);
      const giro = rng() * tau;
      const incl = esp.inclinar ? (rng() - 0.5) * esp.inclinar : 0;
      if (e <= 0) continue;
      // Antes se giraba en orden XYZ; el formato gira en YXZ.
      quat.setFromEuler(euler.set(incl, giro, incl * 0.7, "XYZ"));
      euler.setFromQuaternion(quat, "YXZ");
      const id = esp.id === "roca" && e > 0.95 ? "roca-grande" : esp.id;
      a.objetos.push(objeto(id, [x, sueloEn(x, z) - 0.15 * e, z], [euler.x, euler.y, euler.z], e, tono));
      n++;
    }
    if (esp.id === "drago") {
      // El drago grande de la plaza, con su propio radio de choque (1,5 a escala 1,9).
      a.assets["drago-plaza"] = asset("Drago de la plaza", drago(), [[0, 0, 1.5 / 1.9]], { distancia: esp.distancia });
      a.objetos.push(objeto("drago-plaza", [DRAGO.x, sueloEn(DRAGO.x, DRAGO.z), DRAGO.z], [0, 0.6, 0], 1.9));
    }
  });
}
