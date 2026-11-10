// El suelo y las paredes cuando se está dentro de un interior (`interiores/`): una caja con el suelo plano a la altura de la
// puerta, sin el terreno de fuera. El jugador y la cámara preguntan aquí el suelo (en vez de a `terreno.ts`) y se encierran aquí
// (en vez de en `borde.ts`): fuera de un interior, lo de siempre; dentro, la habitación. Las colisiones de los muebles van
// aparte (`soloEstos` de `colision.ts`). Sin nada más que eso, para no tocar la física ni el terreno.
import { encerrar as encerrarMundo } from "./borde";
import * as T from "./terreno";

/** La habitación en coordenadas del mundo: su planta (sin el grosor del jugador), la altura del suelo y la del techo. */
export interface Caja {
  x0: number;
  x1: number;
  z0: number;
  z1: number;
  y: number;
  techo: number;
}

let caja: Caja | null = null;

/** Entra (`c`) o sale (`null`) de un interior. */
export function fijarCaja(c: Caja | null): void {
  caja = c;
}

/** Si se está dentro de un interior (la sala lo dice al volver a conectarse). */
export const dentro = (): boolean => caja !== null;

export const terrenoEn = (x: number, z: number): number => (caja ? caja.y : T.terrenoEn(x, z));
export const sueloEn = (x: number, z: number): number => (caja ? caja.y : T.sueloEn(x, z));
export const pendienteEn = (x: number, z: number): number => (caja ? 0 : T.pendienteEn(x, z));
export const enMuelle = (x: number, z: number): boolean => (caja ? false : T.enMuelle(x, z));

/** Deja (x, y, z) dentro del borde del mundo o, en un interior, de la habitación. Dice si estaba fuera. */
export function encerrar(p: { x: number; y: number; z: number }): boolean {
  if (!caja) return encerrarMundo(p);
  const x = Math.min(Math.max(p.x, caja.x0), caja.x1);
  const y = Math.min(Math.max(p.y, caja.y), caja.techo);
  const z = Math.min(Math.max(p.z, caja.z0), caja.z1);
  const fuera = x !== p.x || y !== p.y || z !== p.z;
  p.x = x;
  p.y = y;
  p.z = z;
  return fuera;
}
