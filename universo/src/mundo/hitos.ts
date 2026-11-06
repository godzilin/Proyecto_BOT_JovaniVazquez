// Los hitos de la isla: el faro del cabo, el muelle de madera y el roque (pilar de roca).
import { BoxGeometry, BufferGeometry, Color, ConeGeometry, CylinderGeometry, IcosahedronGeometry } from "three";
import { añadirCirculo } from "./colision";
import { pintar, poner } from "./geom";
import { hash2 } from "./ruido";
import { casa } from "./pueblo";
import { mulberry32 } from "./ruido";
import { BENTAYGA, CANTERAS, FARO, MOGAN, MUELLE, NUBLO, costaRadio, sueloEn } from "./terreno";

export function crearFaro(): BufferGeometry[] {
  const y = sueloEn(FARO.x, FARO.z) - 0.3;
  const p: BufferGeometry[] = [];
  const f = (g: BufferGeometry, py: number, color: number): void => {
    p.push(pintar(poner(g.scale(1.25, 1.7, 1.25), [FARO.x, y + py * 1.7, FARO.z]), color));
  };
  f(new CylinderGeometry(1.7, 2.4, 13, 10), 6.5, 0xf6efe0);
  f(new CylinderGeometry(2.3, 2.4, 1.8, 10), 3.2, 0xc0392b);
  f(new CylinderGeometry(2.0, 2.1, 1.8, 10), 8.2, 0xc0392b);
  f(new CylinderGeometry(2.3, 2.3, 0.35, 10), 13.2, 0x3b2a22);
  f(new CylinderGeometry(1.1, 1.1, 1.9, 10), 14.3, 0xffe9a0);
  f(new ConeGeometry(1.5, 1.4, 10), 15.9, 0xc0392b);
  f(new CylinderGeometry(0.1, 0.1, 1.2, 4), 17, 0x3b2a22);
  // Casita del farero.
  const cx = FARO.x + 5;
  const cz = FARO.z + 3;
  const cy = sueloEn(cx, cz) - 0.3;
  p.push(pintar(poner(new BoxGeometry(5, 3.2, 4), [cx, cy + 1.6, cz]), 0xf6efe0));
  const t = new ConeGeometry(1, 1.6, 4);
  t.rotateY(Math.PI / 4);
  p.push(pintar(poner(t, [cx, cy + 4, cz], [0, 0, 0], [4.1, 1, 3.2]), 0xb5523a));
  p.push(pintar(poner(new BoxGeometry(0.9, 1.9, 0.2), [cx - 1, cy + 0.95, cz + 2.02]), 0x2d5d8a));
  añadirCirculo(FARO.x, FARO.z, 3.2);
  añadirCirculo(cx, cz, 2.9);
  return p;
}

export function crearMuelle(): BufferGeometry[] {
  const p: BufferGeometry[] = [];
  const yaw = Math.atan2(MUELLE.dx, MUELLE.dz);
  // Todo se construye a lo largo de +z local y se gira.
  const piezas: BufferGeometry[] = [];
  piezas.push(pintar(poner(new BoxGeometry(MUELLE.ancho, 0.25, MUELLE.largo), [0, MUELLE.alt - 0.125, MUELLE.largo / 2]), 0x8a6a44));
  for (let z = 0.6; z < MUELLE.largo; z += 1.2) piezas.push(pintar(poner(new BoxGeometry(MUELLE.ancho + 0.02, 0.04, 0.1), [0, MUELLE.alt + 0.005, z]), 0x6b4f33));
  for (let z = 1; z < MUELLE.largo; z += 3.2) {
    for (const lado of [-1, 1]) {
      piezas.push(pintar(poner(new CylinderGeometry(0.17, 0.17, 6, 6), [lado * (MUELLE.ancho / 2 - 0.15), MUELLE.alt - 3, z]), 0x5b4128));
    }
  }
  piezas.push(pintar(poner(new CylinderGeometry(0.08, 0.08, 2.4, 5), [MUELLE.ancho / 2 - 0.2, MUELLE.alt + 1.2, MUELLE.largo - 0.4]), 0x3b2a22));
  piezas.push(pintar(poner(new IcosahedronGeometry(0.28, 0), [MUELLE.ancho / 2 - 0.2, MUELLE.alt + 2.5, MUELLE.largo - 0.4]), 0xffe9a0));
  piezas.push(pintar(poner(new CylinderGeometry(0.2, 0.24, 0.5, 6), [-MUELLE.ancho / 2 + 0.3, MUELLE.alt + 0.25, MUELLE.largo - 0.5]), 0x3b2a22));
  for (const g of piezas) p.push(poner(g, [MUELLE.x0, 0, MUELLE.z0], [0, yaw, 0]));
  return p;
}

/** Pilar de roca sobre una peana (Roque Nublo, Roque Bentayga) con escala k. */
export function crearRoque(c: { x: number; z: number }, k: number): BufferGeometry[] {
  const y = sueloEn(c.x, c.z) - 1;
  const cuerpo = new CylinderGeometry(3.3, 6.2, 24, 8, 5);
  const pos = cuerpo.getAttribute("position");
  for (let i = 0; i < pos.count; i++) {
    const f = 0.82 + 0.36 * hash2(Math.round(pos.getX(i) * 40), Math.round(pos.getY(i) * 7 + pos.getZ(i) * 29));
    pos.setX(i, pos.getX(i) * f);
    pos.setZ(i, pos.getZ(i) * f);
  }
  const roca = (yy: number): Color => new Color(0x6c5a4c).lerp(new Color(0x9a8470), Math.min(1, Math.max(0, (yy + 12) / 24)));
  const l: BufferGeometry[] = [pintar(poner(cuerpo, [0, 12, 0]), (_x, py) => roca(py - 12))];
  l.push(pintar(poner(new IcosahedronGeometry(3.4, 0), [0, 24, 0], [0, 0.5, 0], [1, 0.9, 1]), 0x9a8470));
  l.push(pintar(poner(new IcosahedronGeometry(3, 0), [5.2, 2.5, 1.5], [0.3, 1, 0], [1, 1.2, 1]), 0x7a6656));
  l.push(pintar(poner(new IcosahedronGeometry(2.4, 0), [-4.5, 1.6, -3.5], [0.5, 2, 0], [1.1, 1, 1]), 0x726050));
  añadirCirculo(c.x, c.z, 5.2 * k);
  añadirCirculo(c.x + 5.2 * k, c.z + 1.5 * k, 2.8 * k);
  añadirCirculo(c.x - 4.5 * k, c.z - 3.5 * k, 2.2 * k);
  return l.map((g) => poner(g, [c.x, y, c.z], [0, 0, 0], k));
}

export const crearRoques = (): BufferGeometry[] => [...crearRoque(NUBLO, 1), ...crearRoque(BENTAYGA, 0.55)];

/** Las Canteras: unos pocos edificios bajos de colores pastel detrás de la playa urbana. */
export function crearCanteras(): BufferGeometry[] {
  const rng = mulberry32(31337);
  const p: BufferGeometry[] = [];
  const colores = [0xf1dcc0, 0xe6c9b0, 0xd9e2e8, 0xf4ecda, 0xe8b7a0];
  for (let i = 0; i < 7; i++) {
    const a = ((62 + (i - 3) * 4.2) * Math.PI) / 180;
    const ux = Math.cos(a);
    const uz = Math.sin(a);
    const r = costaRadio(ux, uz) - 24 - (i % 2) * 5;
    const x = ux * r;
    const z = uz * r;
    const w = 8 + rng() * 4;
    const h = 5 + rng() * 5;
    const y = sueloEn(x, z);
    const g: BufferGeometry[] = [pintar(poner(new BoxGeometry(w, h + 2, 7), [0, h / 2 - 1, 0]), colores[i % colores.length])];
    for (let f = 0; f < Math.floor(h / 2.6); f++) {
      g.push(pintar(poner(new BoxGeometry(w - 1.2, 0.9, 0.12), [0, 1.4 + f * 2.5, 3.52]), 0x2a3b4d));
    }
    g.push(pintar(poner(new BoxGeometry(w + 0.3, 0.3, 7.3), [0, h + 0.15, 0]), 0xf2ead8));
    const giro = Math.atan2(ux, uz);
    for (const q of g) p.push(poner(q, [x, y, z], [0, giro, 0]));
    añadirCirculo(x, z, w * 0.5);
  }
  return p;
}

/** Puerto de Mogán: casitas blancas con marcos de colores frente al mar y unas barquitas junto al muelle. */
export function crearMogan(): BufferGeometry[] {
  const rng = mulberry32(5150);
  const p: BufferGeometry[] = [];
  const centro = Math.atan2(MOGAN.z, MOGAN.x);
  for (let i = 0; i < 12; i++) {
    const a = centro + ((i - 5.5) * 3.6 * Math.PI) / 180;
    const ux = Math.cos(a);
    const uz = Math.sin(a);
    const r = costaRadio(ux, uz) - 13 - (i % 2) * 9;
    const x = ux * r;
    const z = uz * r;
    const y = sueloEn(x, z);
    const giro = Math.atan2(ux, uz);
    for (const g of casa(4.6 + rng() * 1.6, 4 + rng(), rng() < 0.3 ? 2 : 1, rng, [0xf7f1e4, 0xf4ecda])) p.push(poner(g, [x, y, z], [0, giro, 0]));
    añadirCirculo(x, z, 3);
  }
  // Barquitas fondeadas a los lados del muelle.
  const cols = [0xc0392b, 0x2d5d8a, 0xf4ecda, 0xd9a53a];
  for (let i = 0; i < 4; i++) {
    const lado = i % 2 ? 1 : -1;
    const a = 6 + i * 4;
    const bx = MUELLE.x0 + MUELLE.dx * a - MUELLE.dz * lado * 5.5;
    const bz = MUELLE.z0 + MUELLE.dz * a + MUELLE.dx * lado * 5.5;
    const giro = Math.atan2(MUELLE.dx, MUELLE.dz) + (rng() - 0.5) * 0.3;
    const b = [
      pintar(poner(new BoxGeometry(1.6, 0.7, 4), [0, 0.1, 0]), cols[i]),
      pintar(poner(new BoxGeometry(1.7, 0.12, 4.1), [0, 0.5, 0]), 0xf4ecda),
      pintar(poner(new BoxGeometry(1, 0.9, 1.3), [0, 0.95, -0.4]), 0xf4ecda),
    ];
    for (const q of b) p.push(poner(q, [bx, 0.1, bz], [0, giro, 0]));
  }
  return p;
}
