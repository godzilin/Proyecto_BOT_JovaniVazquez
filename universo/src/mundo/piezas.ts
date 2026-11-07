// Ayudas para describir el mundo de serie como datos (`MundoDoc`): piezas del formato del editor en lugar de geometría.
// Cada ayuda guarda la misma transformación que antes aplicaban `poner` y los translate/scale de la geometría vieja:
// M = T(pos) · R(rot, YXZ) · S(escala) · S(geo) · T(dentro) · forma de 1 m, descompuesta en pos, rot y esc de la pieza.
import { Color, type BufferGeometry, Euler, Vector3 } from "three";
import type { Asset, Circulo, Forma, Malla, MundoDoc, Objeto, Pieza, Vec3 } from "../editor/formato";

/** Redondea a `d` decimales: el documento es de personas (el editor enseña estos números) y pesa menos. */
export const r = (n: number, d = 4): number => {
  const k = 10 ** d;
  return Math.round(n * k) / k || 0; // `|| 0` quita el -0
};
const vec = (v: Vec3, d = 4): Vec3 => [r(v[0], d), r(v[1], d), r(v[2], d)];
const trio = (s: number | Vec3): Vec3 => (typeof s === "number" ? [s, s, s] : s);

export interface Opciones {
  /** Traslación de `poner`. */
  pos?: Vec3;
  /** Giro de `poner` (radianes, YXZ). */
  rot?: Vec3;
  /** Escala de `poner`. */
  escala?: number | Vec3;
  /** Escala aplicada a la geometría antes de `poner` (`g.scale(...)`), después del `dentro`. */
  geo?: Vec3;
  /** Traslación aplicada a la geometría antes de girarla (`g.translate(...)`). */
  dentro?: Vec3;
}

const e = new Euler(0, 0, 0, "YXZ");
const v = new Vector3();

function colocar(forma: Forma, tam: Vec3, color: number, o: Opciones, extra: Partial<Pieza> = {}): Pieza {
  const sp = trio(o.escala ?? 1);
  const sg = o.geo ?? [1, 1, 1];
  const pos: Vec3 = [...(o.pos ?? [0, 0, 0])];
  if (o.dentro) {
    // La traslación interior se escala y se gira con el resto.
    v.set(o.dentro[0] * sp[0] * sg[0], o.dentro[1] * sp[1] * sg[1], o.dentro[2] * sp[2] * sg[2]).applyEuler(e.set(...(o.rot ?? [0, 0, 0]), "YXZ"));
    pos[0] += v.x;
    pos[1] += v.y;
    pos[2] += v.z;
  }
  const esc: Vec3 = [tam[0] * sp[0] * sg[0], tam[1] * sp[1] * sg[1], tam[2] * sp[2] * sg[2]];
  const p: Pieza = { forma, pos: vec(pos), rot: vec(o.rot ?? [0, 0, 0], 5), esc: vec(esc), color, ...extra };
  if (p.fase !== undefined) p.fase = r(p.fase, 5);
  return p;
}

/** `BoxGeometry(w, h, d)` en (x, y, z). */
export const caja = (w: number, h: number, d: number, x: number, y: number, z: number, color: number): Pieza =>
  colocar("caja", [w, h, d], color, { pos: [x, y, z] });

/**
 * `CylinderGeometry(arriba, abajo, alto, lados)` (o `ConeGeometry(abajo, alto, lados)` con `arriba` 0) como prisma:
 * radios normalizados a 0,5 y la escala con el doble del mayor. `fase` es el `rotateY` previo de la geometría.
 */
export function prisma(arriba: number, abajo: number, alto: number, lados: number, color: number, o: Opciones = {}, fase = 0): Pieza {
  const R = Math.max(arriba, abajo);
  const extra: Partial<Pieza> = { lados, arriba: r(arriba / R), abajo: r(abajo / R) };
  if (fase) extra.fase = fase;
  return colocar("prisma", [2 * R, alto, 2 * R], color, o, extra);
}

/** `IcosahedronGeometry(radio, 0)`. */
export const icosaedro = (radio: number, color: number, o: Opciones = {}): Pieza => colocar("icosaedro", [2 * radio, 2 * radio, 2 * radio], color, o);

/** Un asset de piezas sin malla ni distancia propia. */
export function asset(nombre: string, piezas: Pieza[], colision: Circulo[] | null, extra: { malla?: Malla; distancia?: number } = {}): Asset {
  const a: Asset = { nombre, tipo: "piezas", piezas, colision: colision && colision.map(([x, z, rad]) => [r(x), r(z), r(rad)] as Circulo) };
  if (extra.malla) a.malla = extra.malla;
  if (extra.distancia !== undefined) a.distancia = extra.distancia;
  return a;
}

/** Un objeto colocado; `tinte` solo si no es 1. */
export function objeto(id: string, pos: Vec3, rot: Vec3 = [0, 0, 0], esc: number | Vec3 = 1, tinte?: number): Objeto {
  const o: Objeto = { asset: id, pos: vec(pos), rot: vec(rot, 5), esc: vec(trio(esc)) };
  if (tinte !== undefined && r(tinte) !== 1) o.tinte = r(tinte);
  return o;
}

/** Geometría ya hecha (no indexada, con atributo `color` lineal) como `Malla`: milímetros enteros y un hex por vértice. */
export function aMalla(g: BufferGeometry): Malla {
  const p = g.getAttribute("position");
  const c = g.getAttribute("color");
  const col = new Color();
  const pos: number[] = [];
  const color: number[] = [];
  for (let i = 0; i < p.count; i++) {
    pos.push(Math.round(p.getX(i) * 1000), Math.round(p.getY(i) * 1000), Math.round(p.getZ(i) * 1000));
    color.push(col.setRGB(c.getX(i), c.getY(i), c.getZ(i)).getHex());
  }
  return { pos, color };
}

/** Lo que va sumando cada generador del mundo de serie. */
export interface Aporte {
  assets: Record<string, Asset>;
  objetos: Objeto[];
}

export const vacio = (): Aporte => ({ assets: {}, objetos: [] });

export type { MundoDoc };
