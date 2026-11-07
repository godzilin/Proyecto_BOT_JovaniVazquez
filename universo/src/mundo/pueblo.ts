// El pueblo, como datos: casas blancas con ventanas y balcones de madera, una iglesia pequeña y un quiosco alrededor de una plaza.
// La plaza (radio ~12 m) queda libre a propósito: ahí irán el casino y el banco. Cada casa es un asset con sus piezas.
import type { Pieza } from "../editor/formato";
import { type Aporte, asset, caja as cajaPieza, objeto, prisma } from "./piezas";
import { mulberry32 } from "./ruido";
import { PLAZA, sueloEn } from "./terreno";

const PAREDES = [0xf4ecda, 0xefe3cb, 0xf7f1e4, 0xead9bd, 0xe9d0b0, 0xe8b7a0, 0xb7d3c6, 0xf0d27a, 0xa8c4e0];
const PUERTAS = [0x5b3a29, 0x2f6b57, 0x2d5d8a, 0x7a2f2a];
const TEJAS = [0xb5523a, 0xc4633f, 0xa8492f];
const MADERA = 0x7a4a2a;

type P = Pieza[];
const caja = (p: P, w: number, h: number, d: number, x: number, y: number, z: number, color: number): void => {
  p.push(cajaPieza(w, h, d, x, y, z, color));
};

/** Casa en coordenadas locales: el frente mira a +z y el suelo está en y = 0. */
export function casa(w: number, d: number, pisos: number, rng: () => number, paredes: number[] = PAREDES): P {
  const p: P = [];
  const alto = pisos === 2 ? 6 : 3.3;
  const pared = paredes[Math.floor(rng() * paredes.length)];
  const puerta = PUERTAS[Math.floor(rng() * PUERTAS.length)];
  const postigo = PUERTAS[Math.floor(rng() * PUERTAS.length)];
  caja(p, w, alto + 1.4, d, 0, alto / 2 - 0.7, 0, pared);
  if (rng() < 0.5) {
    // Tejado a cuatro aguas.
    p.push(prisma(0, 1, 1.8, 4, TEJAS[Math.floor(rng() * TEJAS.length)], { pos: [0, alto + 0.9, 0], escala: [(w + 0.9) / Math.SQRT2, 1, (d + 0.9) / Math.SQRT2] }, Math.PI / 4));
  } else {
    caja(p, w + 0.3, 0.3, d + 0.3, 0, alto + 0.15, 0, 0xf2ead8);
  }
  caja(p, 1.1, 2.1, 0.2, (rng() - 0.5) * (w - 3), 1.05, d / 2 + 0.02, puerta);
  const huecos = Math.max(1, Math.floor(w / 2.6));
  for (let piso = 0; piso < pisos; piso++) {
    const y = 1.9 + piso * 2.9;
    for (let i = 0; i < huecos; i++) {
      const x = -w / 2 + ((i + 0.5) * w) / huecos;
      if (piso === 0 && Math.abs(x) < 1.1 && i === 0) continue;
      caja(p, 0.95, 1.2, 0.12, x, y, d / 2 + 0.02, 0x2a3b4d);
      caja(p, 0.4, 1.2, 0.14, x - 0.68, y, d / 2 + 0.03, postigo);
      caja(p, 0.4, 1.2, 0.14, x + 0.68, y, d / 2 + 0.03, postigo);
    }
  }
  if (pisos === 2) {
    // Balcón canario de madera en el piso alto.
    const bw = w * 0.7;
    caja(p, bw, 0.16, 1.2, 0, 3.7, d / 2 + 0.6, MADERA);
    caja(p, bw, 0.08, 0.08, 0, 4.7, d / 2 + 1.15, MADERA);
    caja(p, bw, 0.08, 0.08, 0, 4.2, d / 2 + 1.15, MADERA);
    for (let i = 0; i <= 4; i++) caja(p, 0.1, 1.1, 0.1, -bw / 2 + (i * bw) / 4, 4.25, d / 2 + 1.15, MADERA);
    caja(p, 0.14, 2.6, 0.14, -bw / 2, 4.9, d / 2 + 1.15, MADERA);
    caja(p, 0.14, 2.6, 0.14, bw / 2, 4.9, d / 2 + 1.15, MADERA);
    caja(p, bw + 0.4, 0.12, 1.6, 0, 6.25, d / 2 + 0.8, 0x9c5a3a);
  }
  return p;
}

function iglesia(): P {
  const p: P = [];
  caja(p, 7, 5.2, 11, 0, 2.6 - 0.7, 0, 0xf6efe0);
  p.push(prisma(0, 1, 2.4, 4, 0xb5523a, { pos: [0, 6.8, 0], escala: [5.2, 1, 7.7] }, Math.PI / 4));
  for (const sx of [-2.3, 2.3]) {
    caja(p, 2.4, 11, 3.2, sx, 4.8, 6.2, 0xf6efe0);
    caja(p, 1, 1.8, 0.2, sx, 10.2, 7.85, 0x2a3b4d);
    p.push(prisma(0, 1.9, 3, 4, 0xb5523a, { pos: [sx, 12.8, 6.2] }, Math.PI / 4));
    caja(p, 0.2, 1.4, 0.2, sx, 14.9, 6.2, 0x3b2a22);
    caja(p, 0.8, 0.2, 0.2, sx, 14.9, 6.2, 0x3b2a22);
  }
  caja(p, 1.8, 2.8, 0.25, 0, 1.4, 7.82, 0x5b3a29);
  caja(p, 1, 1.6, 0.2, -2.2, 3.4, 5.6, 0x3a5a8c);
  for (const x of [-3.55, 3.55]) for (const z of [-3, 0, 3]) caja(p, 0.2, 1.6, 0.9, x, 3, z, 0x3a5a8c);
  return p;
}

function quiosco(): P {
  const p: P = [];
  p.push(prisma(2.3, 2.3, 0.3, 8, 0xcdbd9a, { pos: [0, 0.1, 0] }));
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    p.push(prisma(0.1, 0.1, 2.8, 5, i % 2 ? 0xf4ecda : 0xb5523a, { pos: [Math.cos(a) * 1.9, 1.5, Math.sin(a) * 1.9] }));
  }
  p.push(prisma(0.1, 2.7, 1.5, 8, 0xc4633f, { pos: [0, 3.65, 0] }));
  p.push(prisma(0.4, 0.4, 1.0, 8, 0xe7d6b0, { pos: [0, 0.8, 0] }));
  return p;
}

/** Suma al aporte las casas de los dos anillos, la Basílica y el quiosco, cada uno con su asset y su objeto. */
export function crearPueblo(a: Aporte): void {
  const rng = mulberry32(4242);
  const mirarPlaza = (x: number, z: number): number => Math.atan2(PLAZA.x - x, PLAZA.z - z);

  // Dos anillos de casas. Huecos libres hacia el sureste (camino a la playa) y el oeste (futuro banco/casino).
  const anillos: [number, number, number, number[]][] = [
    [21, 11, 0.2, [0.6, 3.14]], // radio, casas, desfase, ángulos que se dejan libres (rad, ±0.4)
    [32, 8, 0.1, [0.6]],
  ];
  let n = 0;
  for (const [radio, cuantas, off, libres] of anillos) {
    for (let i = 0; i < cuantas; i++) {
      const ang = off + (i / cuantas) * Math.PI * 2;
      if (radio === 21 && Math.abs(ang - Math.PI * 1.5) < 0.35) continue; // aquí va la iglesia
      if (libres.some((l) => Math.abs(((ang - l + Math.PI * 3) % (Math.PI * 2)) - Math.PI) < 0.4)) continue;
      const r = radio + (rng() - 0.5) * 3;
      const x = PLAZA.x + Math.cos(ang) * r;
      const z = PLAZA.z + Math.sin(ang) * r;
      const w = 5.2 + rng() * 2.2;
      const d = 4.6 + rng() * 1.6;
      const piezas = casa(w, d, rng() < 0.45 ? 2 : 1, rng);
      const id = `casa-${++n}`;
      a.assets[id] = asset(`Casa ${n}`, piezas, [[0, 0, Math.hypot(w, d) * 0.5 * 0.86]]);
      a.objetos.push(objeto(id, [x, sueloEn(x, z), z], [0, mirarPlaza(x, z), 0]));
    }
  }

  // La Basílica del Pino, más grande que una iglesia normal (escala 1,35): sus círculos eran del mundo y aquí van en el asset.
  const k = 1.35;
  const ix = PLAZA.x;
  const iz = PLAZA.z - 21;
  const c = (dx: number, dz: number, rad: number): [number, number, number] => [dx / k, dz / k, rad / k];
  a.assets.iglesia = asset("Basílica", iglesia(), [c(0, -2, 5.6), c(0, 3, 5.6), c(-3.1, 8.4, 3.2), c(3.1, 8.4, 3.2)]);
  a.objetos.push(objeto("iglesia", [ix, sueloEn(ix, iz), iz], [0, 0, 0], k));

  const kx = PLAZA.x + 8;
  const kz = PLAZA.z - 5;
  a.assets.quiosco = asset("Quiosco", quiosco(), [[0, 0, 2.4]]);
  a.objetos.push(objeto("quiosco", [kx, sueloEn(kx, kz), kz]));
}
