// Guiones del borrador del editor por celdas (`universo/src/editor/borrador.ts`) para `test_universo_editor_celdas.py`: se compilan
// con Vite (`docs/volcar_mundo_serie.py`) y se ejecutan con Node contra un servidor falso del borrador (`fetch` de mentira con las
// rutas `/api/editor/indice`, `trozos`, `usos` y el `PUT` de `borrador`), sin navegador.
import { Borrador, inicio } from "../../universo/src/editor/borrador";
import type { Asset, Objeto } from "../../universo/src/editor/formato";
import { escribirPaso } from "../../universo/src/editor/pincel";
import { claveCelda } from "../../universo/src/editor/trozos";
import { NV, rejillaEditable, rejillas, terrenoDe, numeroDe } from "../../universo/src/mundo/terreno";

const CELDA = 80;
const caja: Asset = { nombre: "Caja", tipo: "piezas", piezas: [{ forma: "caja", pos: [0, 0.5, 0], rot: [0, 0, 0], esc: [1, 1, 1], color: 0x888888 }], colision: null };
const objeto = (asset: string, x: number, z: number): Objeto => ({ asset, pos: [x, 1, z], rot: [0, 0, 0], esc: [1, 1, 1] });
const sesion = { session: "s", guildId: "1" };

/** Un borrador de `lado` × `lado` celdas centrado en 0, dos objetos por celda, en un servidor falso que apunta lo que le piden. */
function servidor(lado: number) {
  const trozos = new Map<string, { doc: unknown; rev: number }>();
  for (let cx = -lado / 2; cx < lado / 2; cx++) {
    for (let cz = -lado / 2; cz < lado / 2; cz++) {
      const a = `a${Math.abs(cx + cz) % 3}`;
      trozos.set(`c/${cx}/${cz}`, { doc: { objetos: [objeto(a, cx * CELDA + 10, cz * CELDA + 10), objeto(a, cx * CELDA + 50, cz * CELDA + 50)] }, rev: 1 });
    }
  }
  for (let i = 0; i < 3; i++) trozos.set(`a/a${i}`, { doc: caja, rev: 1 });
  trozos.set("zonas", { doc: { zonas: { plaza: { x: 0, z: 0, r: 10 } } }, rev: 1 });
  const pedidas: string[] = [];
  const guardados: Record<string, { rev: number; doc: unknown }>[] = [];
  let bytes = 0;
  const responder = (status: number, datos: unknown) => {
    const texto = JSON.stringify(datos);
    bytes += texto.length;
    return { ok: status < 400, status, json: async () => JSON.parse(texto) };
  };
  (globalThis as { fetch: unknown }).fetch = async (url: string, o: { method: string; body?: string }) => {
    const u = new URL(url, "http://x");
    const ruta = u.pathname.replace("/api/editor/", "");
    if (ruta === "indice") {
      const vivos: Record<string, number> = {};
      const borrados: Record<string, number> = {};
      const docs: Record<string, unknown> = {};
      for (const [k, t] of trozos) {
        (t.doc ? vivos : borrados)[k] = t.rev;
        if (t.doc && !k.startsWith("c/")) docs[k] = t.doc;
      }
      return responder(200, { version: 1, trozos: vivos, borrados, docs });
    }
    if (ruta === "trozos") {
      const claves = (u.searchParams.get("t") ?? "").split(",");
      if (claves.length > 64) return responder(400, { error: "lote" });
      pedidas.push(...claves);
      return responder(200, { trozos: Object.fromEntries(claves.map((k) => [k, trozos.get(k) ?? { doc: null, rev: 0 }])) });
    }
    if (ruta === "usos") {
      const id = u.searchParams.get("asset");
      const celdas = [...trozos].filter(([k, t]) => k.startsWith("c/") && (t.doc as { objetos?: Objeto[] } | null)?.objetos?.some((x) => x.asset === id)).map(([k]) => k);
      return responder(200, { celdas });
    }
    if (ruta === "borrador" && o.method === "PUT") {
      const pedido = JSON.parse(o.body as string).trozos as Record<string, { rev: number; doc: unknown }>;
      for (const [k, t] of Object.entries(pedido)) if ((trozos.get(k)?.rev ?? 0) !== t.rev) return responder(409, { error: "Otra persona ha guardado." });
      guardados.push(pedido);
      const revs: Record<string, number> = {};
      for (const [k, t] of Object.entries(pedido)) {
        revs[k] = t.rev + 1;
        trozos.set(k, { doc: t.doc, rev: revs[k] });
      }
      return responder(200, { revs });
    }
    return responder(404, { error: ruta });
  };
  return { trozos, pedidas, guardados, leer: () => bytes, cero: () => (bytes = 0) };
}

const nada = (): undefined => undefined;

async function abrir(s: ReturnType<typeof servidor>) {
  const fallos: string[] = [];
  const b = new Borrador(sesion, await inicio(sesion), { fallo: (e) => fallos.push((e as Error).message), estado: nada, reemplazado: nada });
  return { b, fallos };
}

/** Abrir el editor no depende del tamaño del mundo (salvo el índice, ~20 bytes por celda). */
export async function abrirSegunTamano() {
  const out: Record<number, { celdasPedidas: number; objetos: number; bytes: number; ms: number }> = {};
  for (const lado of [10, 60]) {
    const s = servidor(lado);
    const t = performance.now();
    const { b } = await abrir(s);
    out[lado] = { celdasPedidas: s.pedidas.length, objetos: b.doc.objetos.length, bytes: s.leer(), ms: performance.now() - t };
  }
  return out;
}

export async function paseo() {
  Borrador.comprobar = true;
  const s = servidor(60);
  const { b, fallos } = await abrir(s);
  const alAbrir = b.doc.objetos.length;
  // Alrededor de la cámara en el centro.
  b.cerca(0, 0, true);
  await b.asegurar([]); // nada nuevo: solo espera lo que ya se pide
  await new Promise((r) => setTimeout(r, 0));
  const pedidasCentro = s.pedidas.length;
  await Promise.all([...new Set(s.pedidas)].map((k) => b.asegurar([k])));
  const centro = { celdas: b.cuentaCeldas(), objetos: b.doc.objetos.length, pedidas: pedidasCentro };

  // Llevar un objeto a una celda que no se ha bajado (c/20/0, a 1600 m): se deshace y la celda se pide.
  const lejos = "c/20/0";
  const o = b.doc.objetos[0];
  const antes = [...o.pos];
  o.pos = [20 * CELDA + 5, 1, 5];
  const rechazado = !b.commit({ objetos: [o] });
  const vuelto = b.doc.objetos.some((x) => x.pos[0] === antes[0] && x.pos[2] === antes[2]) && !b.doc.objetos.some((x) => x.pos[0] === 20 * CELDA + 5);
  const avisado = fallos.some((f) => f.includes(lejos));
  await b.asegurar([lejos]);
  // Ya bajada, el mismo cambio vale y se guarda junto a lo que tenía.
  const otro = b.doc.objetos[0];
  const origen = claveCelda(otro.pos[0], otro.pos[2]);
  otro.pos = [20 * CELDA + 5, 1, 5];
  const aceptado = b.commit({ objetos: [otro] });
  await b.guardar();
  const guardado = Object.keys(s.guardados.at(-1) ?? {}).sort();
  const enLejos = (s.trozos.get(lejos)?.doc as { objetos: Objeto[] }).objetos.length;

  // Irse lejos suelta lo de atrás, salvo lo que está en el historial (la celda de origen y la lejana).
  b.cerca(5000, 5000, true);
  const tras = { celdas: b.cuentaCeldas(), objetos: b.doc.objetos.length, sigueLejos: b.bajada(lejos) };
  // Volver: se vuelven a pedir, sin duplicar objetos.
  b.cerca(0, 0, true);
  await new Promise((r) => setTimeout(r, 0));
  await b.asegurar(s.pedidas.slice(-200));
  const vuelta = { celdas: b.cuentaCeldas(), objetos: b.doc.objetos.length };

  // Deshacer y rehacer el paso con la celda lejana.
  b.irA(b.hi - 1);
  const deshecho = !b.doc.objetos.some((x) => x.pos[0] === 20 * CELDA + 5);
  b.irA(b.hi + 1);

  // Borrar un asset: el servidor dice qué celdas sin bajar lo usan.
  const usos = await b.usosSinBajar("a1");
  return { alAbrir, origen, centro, rechazado, vuelto, avisado, aceptado, guardado, enLejos, tras, vuelta, deshecho, usos: usos.length, descuadres: Borrador.descuadres };
}

/** Lo que trae una celda y el editor no conoce (`gen`, un campo nuevo, terreno fuera de la isla). */
export async function camposAjenos() {
  const s = servidor(10);
  const terreno = { alturas: "AAAA", colores: "AA" };
  s.trozos.set("c/3/0", { doc: { objetos: [objeto("a0", 250, 10)], gen: 7, otro: { x: 1 }, terreno }, rev: 4 });
  s.trozos.set("c/4/0", { doc: { objetos: [objeto("a0", 330, 10)], gen: 7 }, rev: 2 });
  const { b } = await abrir(s);
  await b.asegurar(["c/3/0", "c/4/0"]);
  const o = b.doc.objetos.find((x) => x.pos[0] === 250) as Objeto;
  o.pos = [251, 1, 10];
  b.commit({ objetos: [o] });
  await b.guardar();
  const [enviado] = s.guardados;
  return { claves: Object.keys(enviado), celda: enviado["c/3/0"] };
}

/**
 * El pincel en el borde x = 80 entre la celda 0/0 (bajada) y la 1/0 (existe y no se ha bajado), y en el borde x = 0 con la -1/0 (no
 * existe): la 1/0 no se toca ni se manda, la -1/0 se crea.
 */
export async function pincelEnElBorde() {
  const s = servidor(0);
  s.trozos.set("c/0/0", { doc: { objetos: [objeto("a0", 40, 40)] }, rev: 3 });
  s.trozos.set("c/1/0", { doc: { objetos: [objeto("a0", 120, 40)] }, rev: 5 });
  const { b } = await abrir(s);
  await b.asegurar(["c/0/0"]);
  rejillas.clear();
  const tocadas = new Set<string>();
  const rejilla = (cx: number, cz: number) => {
    tocadas.add(`c/${cx}/${cz}`);
    return rejillaEditable(cx, cz, b.doc.terreno?.[`c/${cx}/${cz}`]);
  };
  // Una franja de vértices de x = -4 a x = 84 m (gx de -2 a 42), z de 10 a 20 m, subida a 50 m.
  const nuevos: number[] = [];
  for (let gz = 5; gz <= 10; gz++) for (let gx = -2; gx <= 42; gx++) nuevos.push(gx, gz, 50);
  escribirPaso(nuevos, null, (k) => b.bajada(k), rejilla);
  // Lo que hace `terminar`: cada celda tocada pasa al documento.
  const terreno = { ...b.doc.terreno };
  for (const k of tocadas) {
    const [cx, cz] = k.split("/").slice(1).map(Number);
    terreno[k] = terrenoDe(rejillas.get(numeroDe(cx, cz))!).terreno;
  }
  b.doc.terreno = terreno;
  b.commit({});
  await b.guardar();
  const alt = rejillas.get(numeroDe(0, 0))!.alt;
  return {
    tocadas: [...tocadas].sort(),
    guardadas: Object.keys(s.guardados[0] ?? {}).sort(),
    revs: Object.fromEntries(Object.entries(s.guardados[0] ?? {}).map(([k, t]) => [k, t.rev])),
    dentro: alt[5 * NV + 20],
    bordeLejos: alt[5 * NV + NV - 1],
    bordeNueva: alt[5 * NV],
    sigue: s.trozos.get("c/1/0")?.rev,
  };
}
