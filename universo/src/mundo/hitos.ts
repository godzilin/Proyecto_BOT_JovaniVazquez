// Los hitos de la isla, como datos: el faro del cabo, el muelle de madera, el roque (pilar de roca), Las Canteras y Puerto de Mogán.
import { Color, CylinderGeometry } from "three";
import type { Asset, Pieza } from "../editor/formato";
import { pintar, poner } from "./geom";
import { type Aporte, aMalla, asset, caja, icosaedro, objeto, prisma } from "./piezas";
import { casa } from "./pueblo";
import { hash2, mulberry32 } from "./ruido";
import { BENTAYGA, FARO, MOGAN, MUELLE, NUBLO, costaRadio, sueloEn } from "./terreno";

export function crearFaro(a: Aporte): void {
  const f = (rT: number, rB: number, h: number, lados: number, py: number, color: number): Pieza =>
    prisma(rT, rB, h, lados, color, { pos: [0, py * 1.7, 0], geo: [1.25, 1.7, 1.25] });
  const torre = [
    f(1.7, 2.4, 13, 10, 6.5, 0xf6efe0),
    f(2.3, 2.4, 1.8, 10, 3.2, 0xc0392b),
    f(2.0, 2.1, 1.8, 10, 8.2, 0xc0392b),
    f(2.3, 2.3, 0.35, 10, 13.2, 0x3b2a22),
    f(1.1, 1.1, 1.9, 10, 14.3, 0xffe9a0),
    f(0, 1.5, 1.4, 10, 15.9, 0xc0392b),
    f(0.1, 0.1, 1.2, 4, 17, 0x3b2a22),
  ];
  a.assets.faro = asset("Faro", torre, [[0, 0, 3.2]]);
  a.objetos.push(objeto("faro", [FARO.x, sueloEn(FARO.x, FARO.z) - 0.3, FARO.z]));
  // Casita del farero.
  const cx = FARO.x + 5;
  const cz = FARO.z + 3;
  const casita = [
    caja(5, 3.2, 4, 0, 1.6, 0, 0xf6efe0),
    prisma(0, 1, 1.6, 4, 0xb5523a, { pos: [0, 4, 0], escala: [4.1, 1, 3.2] }, Math.PI / 4),
    caja(0.9, 1.9, 0.2, -1, 0.95, 2.02, 0x2d5d8a),
  ];
  a.assets["casa-farero"] = asset("Casita del farero", casita, [[0, 0, 2.9]]);
  a.objetos.push(objeto("casa-farero", [cx, sueloEn(cx, cz) - 0.3, cz]));
}

export function crearMuelle(a: Aporte): void {
  // Todo se construye a lo largo de +z local y se gira.
  const yaw = Math.atan2(MUELLE.dx, MUELLE.dz);
  const p: Pieza[] = [];
  p.push(caja(MUELLE.ancho, 0.25, MUELLE.largo, 0, MUELLE.alt - 0.125, MUELLE.largo / 2, 0x8a6a44));
  for (let z = 0.6; z < MUELLE.largo; z += 1.2) p.push(caja(MUELLE.ancho + 0.02, 0.04, 0.1, 0, MUELLE.alt + 0.005, z, 0x6b4f33));
  for (let z = 1; z < MUELLE.largo; z += 3.2) {
    for (const lado of [-1, 1]) p.push(prisma(0.17, 0.17, 6, 6, 0x5b4128, { pos: [lado * (MUELLE.ancho / 2 - 0.15), MUELLE.alt - 3, z] }));
  }
  p.push(prisma(0.08, 0.08, 2.4, 5, 0x3b2a22, { pos: [MUELLE.ancho / 2 - 0.2, MUELLE.alt + 1.2, MUELLE.largo - 0.4] }));
  p.push(icosaedro(0.28, 0xffe9a0, { pos: [MUELLE.ancho / 2 - 0.2, MUELLE.alt + 2.5, MUELLE.largo - 0.4] }));
  p.push(prisma(0.2, 0.24, 0.5, 6, 0x3b2a22, { pos: [-MUELLE.ancho / 2 + 0.3, MUELLE.alt + 0.25, MUELLE.largo - 0.5] }));
  a.assets.muelle = asset("Muelle", p, null);
  a.objetos.push(objeto("muelle", [MUELLE.x0, 0, MUELLE.z0], [0, yaw, 0]));
}

/** Pilar de roca sobre una peana (Roque Nublo, Roque Bentayga): el cuerpo con degradado es una malla y las peñas, icosaedros. */
function roque(): Asset {
  const cuerpo = new CylinderGeometry(3.3, 6.2, 24, 8, 5);
  const pos = cuerpo.getAttribute("position");
  for (let i = 0; i < pos.count; i++) {
    const f = 0.82 + 0.36 * hash2(Math.round(pos.getX(i) * 40), Math.round(pos.getY(i) * 7 + pos.getZ(i) * 29));
    pos.setX(i, pos.getX(i) * f);
    pos.setZ(i, pos.getZ(i) * f);
  }
  const roca = (yy: number): Color => new Color(0x6c5a4c).lerp(new Color(0x9a8470), Math.min(1, Math.max(0, (yy + 12) / 24)));
  const malla = aMalla(pintar(poner(cuerpo, [0, 12, 0]), (_x, py) => roca(py - 12)));
  const peñas = [
    icosaedro(3.4, 0x9a8470, { pos: [0, 24, 0], rot: [0, 0.5, 0], escala: [1, 0.9, 1] }),
    icosaedro(3, 0x7a6656, { pos: [5.2, 2.5, 1.5], rot: [0.3, 1, 0], escala: [1, 1.2, 1] }),
    icosaedro(2.4, 0x726050, { pos: [-4.5, 1.6, -3.5], rot: [0.5, 2, 0], escala: [1.1, 1, 1] }),
  ];
  return asset("Roque", peñas, [[0, 0, 5.2], [5.2, 1.5, 2.8], [-4.5, -3.5, 2.2]], { malla });
}

export function crearRoques(a: Aporte): void {
  a.assets.roque = roque();
  // El Bentayga es el mismo roque a escala 0,55.
  for (const [c, k] of [[NUBLO, 1], [BENTAYGA, 0.55]] as const) a.objetos.push(objeto("roque", [c.x, sueloEn(c.x, c.z) - 1, c.z], [0, 0, 0], k));
}

/** Las Canteras: unos pocos edificios bajos de colores pastel detrás de la playa urbana. */
export function crearCanteras(a: Aporte): void {
  const rng = mulberry32(31337);
  const colores = [0xf1dcc0, 0xe6c9b0, 0xd9e2e8, 0xf4ecda, 0xe8b7a0];
  for (let i = 0; i < 7; i++) {
    const ang = ((62 + (i - 3) * 4.2) * Math.PI) / 180;
    const ux = Math.cos(ang);
    const uz = Math.sin(ang);
    const r = costaRadio(ux, uz) - 24 - (i % 2) * 5;
    const x = ux * r;
    const z = uz * r;
    const w = 8 + rng() * 4;
    const h = 5 + rng() * 5;
    const p: Pieza[] = [caja(w, h + 2, 7, 0, h / 2 - 1, 0, colores[i % colores.length])];
    for (let f = 0; f < Math.floor(h / 2.6); f++) p.push(caja(w - 1.2, 0.9, 0.12, 0, 1.4 + f * 2.5, 3.52, 0x2a3b4d));
    p.push(caja(w + 0.3, 0.3, 7.3, 0, h + 0.15, 0, 0xf2ead8));
    const id = `canteras-${i + 1}`;
    a.assets[id] = asset(`Las Canteras ${i + 1}`, p, [[0, 0, w * 0.5]]);
    a.objetos.push(objeto(id, [x, sueloEn(x, z), z], [0, Math.atan2(ux, uz), 0]));
  }
}

/** Puerto de Mogán: casitas blancas con marcos de colores frente al mar y unas barquitas junto al muelle. */
export function crearMogan(a: Aporte): void {
  const rng = mulberry32(5150);
  const centro = Math.atan2(MOGAN.z, MOGAN.x);
  for (let i = 0; i < 12; i++) {
    const ang = centro + ((i - 5.5) * 3.6 * Math.PI) / 180;
    const ux = Math.cos(ang);
    const uz = Math.sin(ang);
    const r = costaRadio(ux, uz) - 13 - (i % 2) * 9;
    const x = ux * r;
    const z = uz * r;
    const id = `mogan-${i + 1}`;
    a.assets[id] = asset(`Casa de Mogán ${i + 1}`, casa(4.6 + rng() * 1.6, 4 + rng(), rng() < 0.3 ? 2 : 1, rng, [0xf7f1e4, 0xf4ecda]), [[0, 0, 3]]);
    a.objetos.push(objeto(id, [x, sueloEn(x, z), z], [0, Math.atan2(ux, uz), 0]));
  }
  // Barquitas fondeadas a los lados del muelle.
  const cols = [0xc0392b, 0x2d5d8a, 0xf4ecda, 0xd9a53a];
  for (let i = 0; i < 4; i++) {
    const lado = i % 2 ? 1 : -1;
    const dist = 6 + i * 4;
    const bx = MUELLE.x0 + MUELLE.dx * dist - MUELLE.dz * lado * 5.5;
    const bz = MUELLE.z0 + MUELLE.dz * dist + MUELLE.dx * lado * 5.5;
    const giro = Math.atan2(MUELLE.dx, MUELLE.dz) + (rng() - 0.5) * 0.3;
    const id = `barca-${i + 1}`;
    a.assets[id] = asset(`Barca ${i + 1}`, [caja(1.6, 0.7, 4, 0, 0.1, 0, cols[i]), caja(1.7, 0.12, 4.1, 0, 0.5, 0, 0xf4ecda), caja(1, 0.9, 1.3, 0, 0.95, -0.4, 0xf4ecda)], null);
    a.objetos.push(objeto(id, [bx, 0.1, bz], [0, giro, 0]));
  }
}
