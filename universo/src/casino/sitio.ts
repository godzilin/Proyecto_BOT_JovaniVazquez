// Dónde está cada tragaperras de la plaza, cómo se pasa de su marco local al mundo y las medidas del mueble.
// Marco local de una máquina: +x a la derecha de quien la mira, +y arriba, +z hacia fuera (donde se sienta el jugador).
import { Matrix4, Vector3 } from "three";
import { PLAZA, sueloEn } from "../mundo/terreno";

export const MAQUINAS = 6;
/** Radio del arco y ángulo (en el plano, desde el centro de la plaza) de la máquina central. */
export const RADIO_ARCO = 11;
const ANGULO_CENTRO = 1.6; // al sur de la plaza (+z), con el drago (oeste) a 6 m del extremo
export const PASO_ANGULO = 0.18;

// Medidas del mueble en metros (marco local).
export const ANCHO = 1.6;
export const PANTALLA = { w: 1.1, h: (1.1 * 412) / 544, y: 1.65, z: 0.345 }; // plano de la pantalla
export const CUBIERTA = { y: 1.1, z: 0.62, inclinacion: 0.3, w: 1.7, d: 0.65, grosor: 0.1 }; // botonera: se inclina hacia quien juega
export const OJOS = 1.62; // altura de los ojos sentado
/** Cuánto sobresale la cima del letrero: el arco entero cabe en 5 m de alto. */
export const ALTO_LETRERO = 4.35;

export interface Puesto {
  x: number;
  y: number;
  z: number;
  /** Giro Y: el +z local apunta al centro de la plaza. */
  yaw: number;
  angulo: number;
}

export const PUESTOS: Puesto[] = Array.from({ length: MAQUINAS }, (_, i) => {
  const angulo = ANGULO_CENTRO + (i - (MAQUINAS - 1) / 2) * PASO_ANGULO;
  const x = PLAZA.x + Math.cos(angulo) * RADIO_ARCO;
  const z = PLAZA.z + Math.sin(angulo) * RADIO_ARCO;
  return { x, y: sueloEn(x, z), z, yaw: Math.atan2(PLAZA.x - x, PLAZA.z - z), angulo };
});

/** Centro del arco por detrás (donde va el letrero del bote) y su giro. */
export const ARCO = (() => {
  const x = PLAZA.x + Math.cos(ANGULO_CENTRO) * (RADIO_ARCO + 0.7);
  const z = PLAZA.z + Math.sin(ANGULO_CENTRO) * (RADIO_ARCO + 0.7);
  return { x, y: sueloEn(x, z), z, yaw: Math.atan2(PLAZA.x - x, PLAZA.z - z) };
})();

const m = new Matrix4();
const v = new Vector3();

/** Matriz de una máquina: del marco local al mundo. */
export function matrizDe(p: { x: number; y: number; z: number; yaw: number }, salida = new Matrix4()): Matrix4 {
  return salida.makeRotationY(p.yaw).setPosition(p.x, p.y, p.z);
}

/** Un punto local de la máquina `i` en coordenadas del mundo. */
export function aMundo(i: number, lx: number, ly: number, lz: number, salida = new Vector3()): Vector3 {
  return salida.set(lx, ly, lz).applyMatrix4(matrizDe(PUESTOS[i], m));
}

/** Dirección en el mundo hacia la que mira la máquina `i` (su +z local). */
export function frenteDe(i: number): { x: number; z: number } {
  const y = PUESTOS[i].yaw;
  return { x: Math.sin(y), z: Math.cos(y) };
}

/** Dónde se sienta quien juega en `i` y hacia dónde mira; `zOjos` depende de lo ancha que sea la pantalla del jugador. */
export function asiento(i: number, zOjos: number): { pos: Vector3; yaw: number } {
  const p = PUESTOS[i];
  return { pos: aMundo(i, 0, OJOS, zOjos, v.clone()).setY(p.y + OJOS), yaw: p.yaw };
}
