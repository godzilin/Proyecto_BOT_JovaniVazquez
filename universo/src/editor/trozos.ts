// El mundo en trozos (formato 2): `aTrozos` parte el `MundoDoc` plano con el que trabajan la página y el editor, `deTrozos` lo junta.
// `aTrozos` es el par de `split_world` de `bot/services/universo_editor.py` (mismas claves, mismos objetos, mismo base64 de terreno);
// se comprueba con `docs/comparar_trozos.py`. Vive aparte de `formato.ts` porque rellenar el terreno que falta necesita `heightAt`
// (`mundo/terreno.ts`), que a su vez importa las constantes de `formato.ts`: un ciclo.
import { CELDA, TERRENO_CELDAS, TERRENO_LADO, type Asset, type MundoDoc, type Objeto, type Terreno, type Zona } from "./formato";
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

interface Celda {
  objetos: Objeto[];
  terreno?: Terreno;
}

// El terreno cambia pocas veces y cada commit parte el documento: se reusa el último corte si no ha cambiado.
let corte: { alturas: string; colores: string; celdas: Map<string, Terreno> } | undefined;

/** Parte el terreno de la isla en las 16 celdas de la -2 a la 1 (`_split_terrain`). */
function partirTerreno(t: Terreno): Map<string, Terreno> {
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

/** Parte el mundo en trozos (clave → JSON). Los vacíos no salen: sin zonas, sin objetos ni terreno en una celda. */
export function aTrozos(doc: MundoDoc): Map<string, string> {
  const trozos = new Map<string, string>();
  if (Object.keys(doc.zonas).length > 0) trozos.set("zonas", JSON.stringify({ zonas: doc.zonas }));
  for (const [id, asset] of Object.entries(doc.assets)) trozos.set(`a/${id}`, JSON.stringify(asset));
  const celdas = new Map<string, Celda>();
  const celda = (clave: string): Celda => {
    let c = celdas.get(clave);
    if (!c) celdas.set(clave, (c = { objetos: [] }));
    return c;
  };
  for (const o of doc.objetos) celda(claveCelda(o.pos[0], o.pos[2])).objetos.push(o);
  if (doc.terreno) for (const [clave, t] of partirTerreno(doc.terreno)) celda(clave).terreno = t;
  for (const [clave, c] of celdas) trozos.set(clave, JSON.stringify(c));
  return trozos;
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
    else if (clave.startsWith("a/")) doc.assets[clave.slice(2)] = t as Asset;
    else {
      const m = /^c\/(-?\d+)\/(-?\d+)$/.exec(clave);
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
