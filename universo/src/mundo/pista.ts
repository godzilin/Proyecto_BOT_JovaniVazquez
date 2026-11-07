// La pista del extraplano de pruebas: rampas de varias pendientes, escalones, un muro y columnas de varios radios. Un solo
// sitio define cada bloque: de aquí salen la altura del suelo (`alturaPista`, la que pisa el jugador) y su dibujo (`pistaDoc`,
// datos que pinta `Obras` como cualquier mundo). Coordenadas absolutas: el casino y la salida siguen en la plaza (-8, 60).
import type { MundoDoc, Pieza } from "../editor/formato";

interface Bloque {
  x0: number;
  x1: number;
  z0: number;
  z1: number;
  alto: number;
  /** Rampa: alta en z0 y a ras de suelo en z1 (se sube yendo hacia -z). */
  rampa?: boolean;
  color: number;
}

/** Pendientes de las rampas, en grados. El jugador no sube de ~56° (pendiente 1,5): la de 60 no se sube. */
export const RAMPAS = [15, 30, 45, 55, 60];
const ALTO_RAMPA = 4; // con menos, la de 60° mide menos de 2 m y la pendiente (diferencia a ±1 m) no la ve entera
/** Alturas de los escalones, en metros. */
export const ESCALONES = [0.25, 0.5, 0.75, 1, 1.5, 2];
/** Radios de las columnas, en metros (colisión de círculo, como los troncos y las casas). */
export const COLUMNAS = [0.3, 0.6, 1, 2];

const BLOQUES: Bloque[] = [];
RAMPAS.forEach((g, i) => {
  const x0 = -44 + i * 6;
  const largo = ALTO_RAMPA / Math.tan((g * Math.PI) / 180);
  const tono = [0x5fb35f, 0x9fc24a, 0xe0b040, 0xe07a3a, 0xd04a3a][i];
  BLOQUES.push({ x0, x1: x0 + 4, z0: 44 - largo, z1: 44, alto: ALTO_RAMPA, rampa: true, color: tono });
  BLOQUES.push({ x0, x1: x0 + 4, z0: 40 - largo, z1: 44 - largo, alto: ALTO_RAMPA, color: 0xb8bec4 });
});
ESCALONES.forEach((h, i) => BLOQUES.push({ x0: 4 + i * 3.5, x1: 6 + i * 3.5, z0: 40, z1: 42, alto: h, color: 0x7f9fc8 }));
BLOQUES.push({ x0: 28, x1: 40, z0: 35, z1: 35.6, alto: 4, color: 0x8a7a6a }); // muro: más empinado que lo que se sube

/** Altura del suelo del extraplano en (x, z): 0 salvo encima de un bloque. */
export function alturaPista(x: number, z: number): number {
  let h = 0;
  for (const b of BLOQUES) {
    if (x < b.x0 || x > b.x1 || z < b.z0 || z > b.z1) continue;
    h = Math.max(h, b.rampa ? (b.alto * (b.z1 - z)) / (b.z1 - b.z0) : b.alto);
  }
  return h;
}

/** Los sitios de la pista, para teletransportarse (x, z). */
export const SITIOS_PISTA: Record<string, [number, number]> = {
  plaza: [-4, 66],
  casino: [-8.3, 64], // irA lo encuadra como en la isla
  rampas: [-32, 50],
  escalones: [12, 46],
  columnas: [6, 30],
  muro: [34, 40],
};

/** El dibujo de la pista como documento del mundo (sin zonas: el extraplano no da logros). */
export function pistaDoc(): MundoDoc {
  const piezas: Pieza[] = BLOQUES.map((b) => ({
    forma: b.rampa ? "cuna" : "caja",
    pos: [(b.x0 + b.x1) / 2, b.alto / 2, (b.z0 + b.z1) / 2],
    rot: [0, 0, 0],
    esc: [b.x1 - b.x0, b.alto, b.z1 - b.z0],
    color: b.color,
  }));
  return {
    v: 2,
    assets: {
      pista: { tipo: "piezas", nombre: "Pista de pruebas", colision: null, piezas, distancia: 600 },
      columna: {
        tipo: "piezas",
        nombre: "Columna de pruebas",
        colision: [[0, 0, 0.5]],
        piezas: [{ forma: "cilindro", pos: [0, 1.5, 0], rot: [0, 0, 0], esc: [1, 3, 1], color: 0xc8a0d8 }],
      },
    },
    objetos: [
      { asset: "pista", pos: [0, 0, 0], rot: [0, 0, 0], esc: [1, 1, 1] },
      ...COLUMNAS.map((r, i) => ({ asset: "columna", pos: [i * 6, 0, 30] as [number, number, number], rot: [0, 0, 0] as [number, number, number], esc: [2 * r, 1, 2 * r] as [number, number, number] })),
    ],
    zonas: {},
  };
}
