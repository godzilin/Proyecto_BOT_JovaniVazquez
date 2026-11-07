// Dónde están las tragaperras y los letreros del mundo (objetos del editor con `funcion`) y las medidas de la máquina en su marco.
// Marco local de una máquina: +x a la derecha de quien la mira, +y arriba, +z hacia fuera (donde se sienta el jugador). Cualquier
// asset con la función `traga` pone la pantalla y la botonera en estos sitios de su marco, así que una máquina nueva hecha en el
// editor ha de dejarles hueco (el asset de serie, `src/bot/assets/casino_plaza.json`, es la referencia).
import { Matrix4, Vector3 } from "three";
import type { Datos, MundoDoc } from "../editor/formato";
import { MAX_TRAGAS } from "../editor/formato";
import { matriz } from "../mundo/obras";

// Medidas del mueble en metros (marco local).
export const PANTALLA = { w: 1.1, h: (1.1 * 412) / 544, y: 1.65, z: 0.345 }; // plano de la pantalla
export const CUBIERTA = { y: 1.1, z: 0.62, inclinacion: 0.3, w: 1.7, d: 0.65, grosor: 0.1 }; // botonera: se inclina hacia quien juega
export const OJOS = 1.62; // altura de los ojos sentado

/** Una tragaperras del mundo. */
export interface Puesto {
  /** Su número para el servidor (`datos.n`). */
  n: number;
  /** Del marco local al mundo (posición, giro y escala del objeto). */
  matriz: Matrix4;
  x: number;
  y: number;
  z: number;
  /** Giro Y del objeto: hacia dónde mira la máquina. */
  yaw: number;
}

/** Un letrero del mundo: su panel ya colocado (centro y medidas en el marco del objeto) y lo que enseña. */
export interface Letrero {
  matriz: Matrix4;
  ancho: number;
  alto: number;
  titulo: string;
  botes: string[];
}

/**
 * Las tragaperras y los letreros de un mundo. Una tragaperras sin número válido o con un número repetido no se puede jugar (el
 * editor lo avisa) y no sale; las demás salen ordenadas por número.
 */
export function especiales(doc: MundoDoc): { tragas: Puesto[]; letreros: Letrero[] } {
  const tragas: Puesto[] = [];
  const letreros: Letrero[] = [];
  const vistos = new Set<number>();
  for (const o of doc.objetos) {
    const f = doc.assets[o.asset]?.funcion;
    if (!f) continue;
    const d: Datos = o.datos ?? {};
    if (f.tipo === "traga") {
      const n = d.n;
      if (n === undefined || !Number.isInteger(n) || n < 1 || n > MAX_TRAGAS || vistos.has(n)) continue;
      vistos.add(n);
      tragas.push({ n, matriz: matriz(o), x: o.pos[0], y: o.pos[1], z: o.pos[2], yaw: o.rot[1] });
    } else {
      const m = matriz(o).multiply(new Matrix4().makeTranslation(...f.pos));
      letreros.push({ matriz: m, ancho: f.ancho, alto: f.alto, titulo: d.titulo ?? "", botes: d.botes ?? [] });
    }
  }
  tragas.sort((a, b) => a.n - b.n);
  return { tragas, letreros };
}

/** Un punto local de la máquina en coordenadas del mundo. */
export function aMundo(p: Puesto, lx: number, ly: number, lz: number, salida = new Vector3()): Vector3 {
  return salida.set(lx, ly, lz).applyMatrix4(p.matriz);
}

/** Dirección en el plano hacia la que mira la máquina (su +z local). */
export function frenteDe(p: Puesto): { x: number; z: number } {
  return { x: Math.sin(p.yaw), z: Math.cos(p.yaw) };
}

/** Dónde se sienta quien juega en la máquina y hacia dónde mira; `zOjos` depende de lo ancha que sea la pantalla del jugador. */
export function asiento(p: Puesto, zOjos: number): { pos: Vector3; yaw: number } {
  const f = frenteDe(p);
  return { pos: new Vector3(p.x + f.x * zOjos, p.y + OJOS, p.z + f.z * zOjos), yaw: p.yaw };
}
