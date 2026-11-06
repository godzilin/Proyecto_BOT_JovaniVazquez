// El pueblo: casas blancas con ventanas y balcones de madera, una iglesia pequeña y un quiosco alrededor de una plaza.
// La plaza (radio ~12 m) queda libre a propósito: ahí irán el casino y el banco.
import { BoxGeometry, BufferGeometry, ConeGeometry, CylinderGeometry } from "three";
import { añadirCirculo } from "./colision";
import { pintar, poner } from "./geom";
import { mulberry32 } from "./ruido";
import { PLAZA, sueloEn } from "./terreno";

const PAREDES = [0xf4ecda, 0xefe3cb, 0xf7f1e4, 0xead9bd, 0xe9d0b0, 0xe8b7a0, 0xb7d3c6, 0xf0d27a, 0xa8c4e0];
const PUERTAS = [0x5b3a29, 0x2f6b57, 0x2d5d8a, 0x7a2f2a];
const TEJAS = [0xb5523a, 0xc4633f, 0xa8492f];
const MADERA = 0x7a4a2a;

type P = BufferGeometry[];
const caja = (p: P, w: number, h: number, d: number, x: number, y: number, z: number, color: number): void => {
  p.push(pintar(poner(new BoxGeometry(w, h, d), [x, y, z]), color));
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
    const t = new ConeGeometry(1, 1.8, 4);
    t.rotateY(Math.PI / 4);
    p.push(pintar(poner(t, [0, alto + 0.9, 0], [0, 0, 0], [(w + 0.9) / Math.SQRT2, 1, (d + 0.9) / Math.SQRT2]), TEJAS[Math.floor(rng() * TEJAS.length)]));
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
  const t = new ConeGeometry(1, 2.4, 4);
  t.rotateY(Math.PI / 4);
  p.push(pintar(poner(t, [0, 6.8, 0], [0, 0, 0], [5.2, 1, 7.7]), 0xb5523a));
  for (const sx of [-2.3, 2.3]) {
    caja(p, 2.4, 11, 3.2, sx, 4.8, 6.2, 0xf6efe0);
    caja(p, 1, 1.8, 0.2, sx, 10.2, 7.85, 0x2a3b4d);
    const cupula = new ConeGeometry(1.9, 3, 4);
    cupula.rotateY(Math.PI / 4);
    p.push(pintar(poner(cupula, [sx, 12.8, 6.2]), 0xb5523a));
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
  p.push(pintar(poner(new CylinderGeometry(2.3, 2.3, 0.3, 8), [0, 0.1, 0]), 0xcdbd9a));
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    p.push(pintar(poner(new CylinderGeometry(0.1, 0.1, 2.8, 5), [Math.cos(a) * 1.9, 1.5, Math.sin(a) * 1.9]), i % 2 ? 0xf4ecda : 0xb5523a));
  }
  p.push(pintar(poner(new CylinderGeometry(0.1, 2.7, 1.5, 8), [0, 3.65, 0]), 0xc4633f));
  p.push(pintar(poner(new CylinderGeometry(0.4, 0.4, 1.0, 8), [0, 0.8, 0]), 0xe7d6b0));
  return p;
}

/** Cada pieza se coloca con una rotación Y y se hunde un poco para que no flote en cuestas suaves. */
function colocar(piezas: P, x: number, z: number, giro: number, k = 1): BufferGeometry[] {
  const y = sueloEn(x, z);
  return piezas.map((g) => poner(g, [x, y, z], [0, giro, 0], k));
}

export function crearPueblo(): BufferGeometry[] {
  const rng = mulberry32(4242);
  const salida: BufferGeometry[] = [];
  const mirarPlaza = (x: number, z: number): number => Math.atan2(PLAZA.x - x, PLAZA.z - z);

  // Dos anillos de casas. Huecos libres hacia el sureste (camino a la playa) y el oeste (futuro banco/casino).
  const anillos: [number, number, number, number[]][] = [
    [21, 11, 0.2, [0.6, 3.14]], // radio, casas, desfase, ángulos que se dejan libres (rad, ±0.4)
    [32, 8, 0.1, [0.6]],
  ];
  for (const [radio, n, off, libres] of anillos) {
    for (let i = 0; i < n; i++) {
      const a = off + (i / n) * Math.PI * 2;
      if (radio === 21 && Math.abs(a - Math.PI * 1.5) < 0.35) continue; // aquí va la iglesia
      if (libres.some((l) => Math.abs(((a - l + Math.PI * 3) % (Math.PI * 2)) - Math.PI) < 0.4)) continue;
      const r = radio + (rng() - 0.5) * 3;
      const x = PLAZA.x + Math.cos(a) * r;
      const z = PLAZA.z + Math.sin(a) * r;
      const w = 5.2 + rng() * 2.2;
      const d = 4.6 + rng() * 1.6;
      salida.push(...colocar(casa(w, d, rng() < 0.45 ? 2 : 1, rng), x, z, mirarPlaza(x, z)));
      const rc = Math.hypot(w, d) * 0.5 * 0.86;
      añadirCirculo(x, z, rc);
    }
  }

  const ix = PLAZA.x;
  const iz = PLAZA.z - 21;
  salida.push(...colocar(iglesia(), ix, iz, 0, 1.35)); // la Basílica del Pino, más grande que una iglesia normal
  añadirCirculo(ix, iz - 2, 5.6);
  añadirCirculo(ix, iz + 3, 5.6);
  añadirCirculo(ix - 3.1, iz + 8.4, 3.2);
  añadirCirculo(ix + 3.1, iz + 8.4, 3.2);

  const kx = PLAZA.x + 8;
  const kz = PLAZA.z - 5;
  salida.push(...colocar(quiosco(), kx, kz, 0));
  añadirCirculo(kx, kz, 2.4);
  return salida;
}
