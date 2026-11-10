// El mundo en trozos (formato 2): `aTrozos` parte el `MundoDoc` plano con el que trabajan la página y el editor, `deTrozos` lo junta.
// `aTrozos` es el par de `split_world` de `bot/services/universo_editor.py` (mismas claves, mismos objetos); el terreno ya va por
// celdas en el `MundoDoc`. Se comprueba con `docs/comparar_trozos.py`.
import { CELDA, type Asset, type MundoDoc, type Objeto, type Teleporte, type Terreno, type Zona } from "./formato";

/** Clave de la celda donde cae el punto (x, z) del mundo (`cell_key`). */
export const claveCelda = (x: number, z: number): string => `c/${Math.floor(x / CELDA)}/${Math.floor(z / CELDA)}`;

// La celda como un número (sin crear textos) para recorrer muchos objetos: (cx + B) · 2B + (cz + B), lejos de cualquier tope.
const B = 1 << 20;
/** La celda de (x, z) como número; `claveDeNumero` la devuelve a su clave. */
export const numeroCelda = (x: number, z: number): number => (Math.floor(x / CELDA) + B) * 2 * B + Math.floor(z / CELDA) + B;
const cxDe = (n: number): number => Math.floor(n / (2 * B)) - B;
const czDe = (n: number): number => (n % (2 * B)) - B;
export const claveDeNumero = (n: number): string => `c/${cxDe(n)}/${czDe(n)}`;
const CLAVE_CELDA = /^c\/(-?\d+)\/(-?\d+)$/;
/** La celda (cx, cz) de una clave `c/<cx>/<cz>`, o `null` si no es de celda. */
export function coordsDeClave(clave: string): [number, number] | null {
  const m = CLAVE_CELDA.exec(clave);
  return m ? [Number(m[1]), Number(m[2])] : null;
}
/** El número de una clave `c/<cx>/<cz>`, o `null` si no es de celda. */
export function numeroDeClave(clave: string): number | null {
  const c = coordsDeClave(clave);
  return c ? (c[0] + B) * 2 * B + c[1] + B : null;
}

interface Celda {
  objetos: Objeto[];
  terreno?: Terreno;
}

// El terreno cambia pocas veces y cada commit parte el documento: con el mismo objeto de terreno, el mismo `Map`.
const cortes = new WeakMap<Record<string, Terreno>, Map<string, Terreno>>();

/** El terreno del documento como `Map` (clave de celda → terreno). Con el mismo objeto, el mismo `Map`. */
export function partirTerreno(t: Record<string, Terreno>): Map<string, Terreno> {
  let m = cortes.get(t);
  if (!m) cortes.set(t, (m = new Map(Object.entries(t))));
  return m;
}

/** Lo que trae la celda aparte de sus objetos, su terreno y `gen` (ver `MundoDoc.celdas`); `undefined` si nada. */
export function extraCelda(c: object): Record<string, unknown> | undefined {
  const resto: Record<string, unknown> = { ...c };
  delete resto.objetos;
  delete resto.terreno;
  delete resto.gen;
  return Object.keys(resto).length ? resto : undefined;
}

/** Si dos terrenos de celda son el mismo (alturas, colores y base). */
export const mismoTerreno = (a?: Terreno, b?: Terreno): boolean => a?.alturas === b?.alturas && a?.colores === b?.colores && a?.base === b?.base;

/** Los trozos `zonas` y `teleportes` (`null` el que no sale). Una lista de teleportes vacía sí sale: «ninguno», no «los de serie». */
export function trozosSueltos(doc: MundoDoc): [string, string | null][] {
  return [
    ["zonas", Object.keys(doc.zonas).length > 0 ? JSON.stringify({ zonas: doc.zonas }) : null],
    ["teleportes", doc.teleportes ? JSON.stringify({ teleportes: doc.teleportes }) : null],
  ];
}

/** El JSON de una celda con esos objetos (en su orden), ese terreno y lo demás que traía; `null` si no lleva nada. */
export function trozoCelda(objetos: Objeto[], terreno?: Terreno, extra?: Record<string, unknown>): string | null {
  if (extra) return JSON.stringify({ objetos, terreno, ...extra });
  if (terreno) return JSON.stringify({ objetos, terreno });
  return objetos.length ? JSON.stringify({ objetos }) : null;
}

/** Parte el mundo en trozos (clave → JSON). Los vacíos no salen: sin zonas, sin objetos ni terreno en una celda. */
export function aTrozos(doc: MundoDoc): Map<string, string> {
  const trozos = new Map<string, string>();
  for (const [k, v] of trozosSueltos(doc)) if (v !== null) trozos.set(k, v);
  for (const [id, asset] of Object.entries(doc.assets)) trozos.set(`a/${id}`, JSON.stringify(asset));
  const celdas = new Map<string, Objeto[]>();
  for (const o of doc.objetos) {
    const clave = claveCelda(o.pos[0], o.pos[2]);
    const l = celdas.get(clave);
    if (l) l.push(o);
    else celdas.set(clave, [o]);
  }
  const terreno = doc.terreno ? partirTerreno(doc.terreno) : new Map<string, Terreno>();
  for (const clave of terreno.keys()) if (!celdas.has(clave)) celdas.set(clave, []);
  for (const clave of Object.keys(doc.celdas ?? {})) if (!celdas.has(clave)) celdas.set(clave, []);
  for (const [clave, objetos] of celdas) trozos.set(clave, trozoCelda(objetos, terreno.get(clave), doc.celdas?.[clave]) as string);
  return trozos;
}

/**
 * El mundo `doc` con esos trozos cambiados (clave → documento, o `null` si ya no está), sin tocar `doc` ni volver a juntar lo
 * demás: los objetos de las demás celdas y los assets que no cambian son los mismos. Da lo mismo que `deTrozos` de todos los
 * trozos con esos cambiados, salvo el orden de los objetos (las celdas cambiadas van al final).
 */
export function parchear(doc: MundoDoc, cambios: Record<string, unknown>): MundoDoc {
  const nuevo: MundoDoc = { ...doc };
  const celdas = new Map<number, Celda | null>();
  for (const [clave, t] of Object.entries(cambios)) {
    if (clave === "zonas") nuevo.zonas = t ? (t as { zonas: Record<string, Zona> }).zonas : {};
    else if (clave === "teleportes") {
      if (t) nuevo.teleportes = (t as { teleportes: Teleporte[] }).teleportes;
      else delete nuevo.teleportes;
    } else if (clave.startsWith("a/")) {
      if (nuevo.assets === doc.assets) nuevo.assets = { ...doc.assets };
      if (t) nuevo.assets[clave.slice(2)] = t as Asset;
      else delete nuevo.assets[clave.slice(2)];
    } else {
      const n = numeroDeClave(clave);
      if (n !== null) celdas.set(n, (t as Celda | null) ?? null);
    }
  }
  if (!celdas.size) return nuevo;
  nuevo.objetos = doc.objetos.filter((o) => !celdas.has(numeroCelda(o.pos[0], o.pos[2])));
  const extras = { ...doc.celdas };
  for (const [n, c] of celdas) {
    const e = c && extraCelda(c);
    if (e) extras[claveDeNumero(n)] = e;
    else delete extras[claveDeNumero(n)];
  }
  if (Object.keys(extras).length) nuevo.celdas = extras;
  else delete nuevo.celdas;
  const terreno = new Map(doc.terreno ? partirTerreno(doc.terreno) : []);
  let otroTerreno = false;
  for (const [n, c] of celdas) {
    if (c) for (const o of c.objetos) nuevo.objetos.push(o);
    const clave = claveDeNumero(n);
    const viejo = terreno.get(clave);
    if (c?.terreno) {
      if (mismoTerreno(viejo, c.terreno)) continue;
      terreno.set(clave, c.terreno);
    } else if (!terreno.delete(clave)) continue;
    otroTerreno = true;
  }
  if (otroTerreno) {
    if (terreno.size) nuevo.terreno = Object.fromEntries(terreno);
    else delete nuevo.terreno;
  }
  return nuevo;
}

/**
 * Junta los trozos (clave → documento) en el mundo plano. Los objetos salen por celdas, no en su orden de antes, y el terreno de
 * cada celda que lo trae, por su clave (sin ninguna, `terreno` queda sin definir). Las claves desconocidas y los trozos `null` se
 * ignoran.
 */
export function deTrozos(trozos: Record<string, unknown>): MundoDoc {
  const doc: MundoDoc = { v: 2, assets: {}, objetos: [], zonas: {} };
  const terreno: Record<string, Terreno> = {};
  let conTerreno = false;
  for (const [clave, t] of Object.entries(trozos)) {
    if (!t) continue;
    if (clave === "zonas") doc.zonas = (t as { zonas: Record<string, Zona> }).zonas;
    else if (clave === "teleportes") doc.teleportes = (t as { teleportes: Teleporte[] }).teleportes;
    else if (clave.startsWith("a/")) doc.assets[clave.slice(2)] = t as Asset;
    else {
      if (!CLAVE_CELDA.test(clave)) continue;
      const c = t as Celda;
      for (const o of c.objetos) doc.objetos.push(o);
      if (c.terreno) [terreno[clave], conTerreno] = [c.terreno, true];
      const e = extraCelda(c);
      if (e) (doc.celdas ??= {})[clave] = e;
    }
  }
  if (conTerreno) doc.terreno = terreno;
  return doc;
}
