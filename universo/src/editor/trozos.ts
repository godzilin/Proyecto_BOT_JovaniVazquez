// El mundo en trozos (formato 2): `aTrozos` parte el `MundoDoc` plano con el que trabajan la página y el editor, `deTrozos` lo junta.
// `aTrozos` es el par de `split_world` de `bot/services/universo_editor.py` (mismas claves, mismos objetos, mismo base64 de terreno);
// se comprueba con `docs/comparar_trozos.py`. Vive aparte de `formato.ts` porque rellenar el terreno que falta necesita `heightAt`
// (`mundo/terreno.ts`), que a su vez importa las constantes de `formato.ts`: un ciclo.
import { CELDA, TERRENO_CELDAS, TERRENO_LADO, type Asset, type MundoDoc, type Objeto, type Teleporte, type Terreno, type Zona } from "./formato";
import { heightAt, MAX_ALTURA, MIN_ALTURA } from "../mundo/terreno";

const PASO = TERRENO_LADO / TERRENO_CELDAS;
/** Vértices por lado de la isla y de una celda (los bordes se repiten en la celda de al lado). */
const LADO_ISLA = TERRENO_CELDAS + 1;
const LADO_CELDA = CELDA / PASO + 1;
/** Primera celda de la isla (-2) y vértices que avanza cada celda. */
const PRIMERA = -TERRENO_LADO / 2 / CELDA;
const POR_CELDA = CELDA / PASO;

const aBytes = (b64: string): Uint8Array => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
function aBase64(b: Uint8Array): string {
  let s = "";
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000));
  return btoa(s);
}

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

// El terreno cambia pocas veces y cada commit parte el documento: se reusa el último corte si no ha cambiado.
let corte: { alturas: string; colores: string; celdas: Map<string, Terreno> } | undefined;

/** Parte el terreno de la isla en las 16 celdas de la -2 a la 1 (`_split_terrain`). Con el mismo terreno, el mismo `Map`. */
export function partirTerreno(t: Terreno): Map<string, Terreno> {
  if (corte && corte.alturas === t.alturas && corte.colores === t.colores) return corte.celdas;
  const h = aBytes(t.alturas);
  const c = aBytes(t.colores);
  if (h.length !== 2 * LADO_ISLA * LADO_ISLA || c.length !== LADO_ISLA * LADO_ISLA) throw new Error("El terreno no cuadra con la rejilla de la isla.");
  const celdas = new Map<string, Terreno>();
  for (let cz = PRIMERA; cz < -PRIMERA; cz++) {
    for (let cx = PRIMERA; cx < -PRIMERA; cx++) {
      const hh = new Uint8Array(2 * LADO_CELDA * LADO_CELDA);
      const cc = new Uint8Array(LADO_CELDA * LADO_CELDA);
      for (let k = 0; k < LADO_CELDA; k++) {
        const ini = ((cz - PRIMERA) * POR_CELDA + k) * LADO_ISLA + (cx - PRIMERA) * POR_CELDA;
        hh.set(h.subarray(2 * ini, 2 * (ini + LADO_CELDA)), 2 * k * LADO_CELDA);
        cc.set(c.subarray(ini, ini + LADO_CELDA), k * LADO_CELDA);
      }
      celdas.set(`c/${cx}/${cz}`, { alturas: aBase64(hh), colores: aBase64(cc) });
    }
  }
  corte = { alturas: t.alturas, colores: t.colores, celdas };
  return celdas;
}

/** Los trozos `zonas` y `teleportes` (`null` el que no sale). Una lista de teleportes vacía sí sale: «ninguno», no «los de serie». */
export function trozosSueltos(doc: MundoDoc): [string, string | null][] {
  return [
    ["zonas", Object.keys(doc.zonas).length > 0 ? JSON.stringify({ zonas: doc.zonas }) : null],
    ["teleportes", doc.teleportes ? JSON.stringify({ teleportes: doc.teleportes }) : null],
  ];
}

/** El JSON de una celda con esos objetos (en su orden) y ese terreno; `null` si no lleva nada. */
export function trozoCelda(objetos: Objeto[], terreno?: Terreno): string | null {
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
  for (const [clave, objetos] of celdas) trozos.set(clave, trozoCelda(objetos, terreno.get(clave)) as string);
  return trozos;
}

/**
 * El mundo `doc` con esos trozos cambiados (clave → documento, o `null` si ya no está), sin tocar `doc` ni volver a juntar lo
 * demás: los objetos de las demás celdas y los assets que no cambian son los mismos. Da lo mismo que `deTrozos` de todos los
 * trozos con esos cambiados, salvo el orden de los objetos (las celdas cambiadas van al final) y, si se quita el terreno de
 * todas las celdas que lo traían y alguna no lo traía, que la isla se queda con la fórmula en vez de quedarse sin terreno.
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
  const terreno = new Map(doc.terreno ? partirTerreno(doc.terreno) : []);
  let otroTerreno = false;
  for (const [n, c] of celdas) {
    if (c) for (const o of c.objetos) nuevo.objetos.push(o);
    const clave = claveDeNumero(n);
    const viejo = terreno.get(clave);
    if (c?.terreno) {
      if (viejo?.alturas === c.terreno.alturas && viejo.colores === c.terreno.colores) continue;
      terreno.set(clave, c.terreno);
    } else if (!terreno.delete(clave)) continue;
    otroTerreno = true;
  }
  if (otroTerreno) {
    const t = juntarTerreno(
      [...terreno].map(([clave, t]): [number, number, Terreno] => {
        const n = numeroDeClave(clave) as number;
        return [cxDe(n), czDe(n), t];
      }),
    );
    if (t) nuevo.terreno = t;
    else delete nuevo.terreno;
  }
  return nuevo;
}

/**
 * Junta los trozos (clave → documento) en el mundo plano. Los objetos salen por celdas, no en su orden de antes. Si alguna de las
 * 16 celdas de la isla trae terreno, se monta la rejilla entera con él y los vértices que ninguna cubre salen de la fórmula de
 * la isla (color 0); si ninguna lo trae, `terreno` queda sin definir. Las claves desconocidas y los trozos `null` se ignoran.
 */
export function deTrozos(trozos: Record<string, unknown>): MundoDoc {
  const doc: MundoDoc = { v: 2, assets: {}, objetos: [], zonas: {} };
  const conTerreno: [number, number, Terreno][] = [];
  for (const [clave, t] of Object.entries(trozos)) {
    if (!t) continue;
    if (clave === "zonas") doc.zonas = (t as { zonas: Record<string, Zona> }).zonas;
    else if (clave === "teleportes") doc.teleportes = (t as { teleportes: Teleporte[] }).teleportes;
    else if (clave.startsWith("a/")) doc.assets[clave.slice(2)] = t as Asset;
    else {
      const m = CLAVE_CELDA.exec(clave);
      if (!m) continue;
      const c = t as Celda;
      for (const o of c.objetos) doc.objetos.push(o);
      if (c.terreno) conTerreno.push([Number(m[1]), Number(m[2]), c.terreno]);
    }
  }
  const t = juntarTerreno(conTerreno);
  if (t) doc.terreno = t;
  return doc;
}

function juntarTerreno(celdas: [number, number, Terreno][]): Terreno | undefined {
  const alturas = new Uint8Array(2 * LADO_ISLA * LADO_ISLA);
  const colores = new Uint8Array(LADO_ISLA * LADO_ISLA);
  const cubierto = new Uint8Array(LADO_ISLA * LADO_ISLA);
  let alguna = false;
  for (const [cx, cz, t] of celdas) {
    if (cx < PRIMERA || cx >= -PRIMERA || cz < PRIMERA || cz >= -PRIMERA) continue; // fuera de la isla esculpible
    const h = aBytes(t.alturas);
    const c = aBytes(t.colores);
    if (h.length !== 2 * LADO_CELDA * LADO_CELDA || c.length !== LADO_CELDA * LADO_CELDA) continue; // el servidor ya lo valida
    alguna = true;
    for (let k = 0; k < LADO_CELDA; k++) {
      const ini = ((cz - PRIMERA) * POR_CELDA + k) * LADO_ISLA + (cx - PRIMERA) * POR_CELDA;
      alturas.set(h.subarray(2 * k * LADO_CELDA, 2 * (k + 1) * LADO_CELDA), 2 * ini);
      colores.set(c.subarray(k * LADO_CELDA, (k + 1) * LADO_CELDA), ini);
      cubierto.fill(1, ini, ini + LADO_CELDA);
    }
  }
  if (!alguna) return undefined;
  const v = new DataView(alturas.buffer);
  const mitad = TERRENO_LADO / 2;
  for (let i = 0; i < cubierto.length; i++) {
    if (cubierto[i]) continue;
    const x = (i % LADO_ISLA) * PASO - mitad;
    const z = Math.floor(i / LADO_ISLA) * PASO - mitad;
    // Float32 como la rejilla de `terreno.ts`, para dar los mismos centímetros que `terrenoDoc`.
    const m = Math.fround(heightAt(x, z));
    v.setInt16(i * 2, Math.round(Math.min(Math.max(m, MIN_ALTURA), MAX_ALTURA) * 100), true);
  }
  return { alturas: aBase64(alturas), colores: aBase64(colores) };
}
