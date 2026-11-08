// Carga por cercanía (fase 2 de `docs/mundo-celdas.md`): de todo el mundo, la página tiene solo el índice (qué trozos hay y en qué versión,
// más las zonas y los teleportes, que son pequeños y van siempre) y baja las celdas que hay alrededor de la cámara, con los assets que
// usan, y suelta las que se quedan lejos. Más allá de las cargadas, hasta `LEJOS`, solo el resumen del terreno (el horizonte). Lo bajado
// se monta por tandas en cada fotograma (`trabajar`), lo más cercano antes, para no dar tirones al cruzar de celda.
//
// El mundo llega de una `Fuente`: la del servidor (`/api/mundo/indice` y `/api/mundo/trozos`, ver `fuenteServidor`) o una local hecha
// de un `MundoDoc` (el mundo de serie, el borrador del editor, el extraplano: `fuenteLocal`). Cambiar de fuente (alguien publica, el
// editor cambia algo) solo vuelve a bajar y a montar los trozos cuya versión ha cambiado.
import { type Asset, CELDA, type MundoDoc, type Objeto, type Teleporte, type Terreno, type Zona } from "../editor/formato";
import { aTrozos, coordsDeClave } from "../editor/trozos";
import type { Obras } from "./obras";
import type { Suelo } from "./suelo";
import { LEJOS } from "./suelo";
import { numeroDe, resumenDe } from "./terreno";

/** Qué hay en el mundo: la versión de cada trozo (celdas y assets) y lo que va siempre. `null`: lo de serie. */
export interface Indice {
  version: number;
  zonas: Record<string, Zona> | null;
  teleportes: Teleporte[] | null;
  trozos: Map<string, number>;
}

export interface Fuente {
  /** De dónde salen las versiones: solo se comparan las de dos fuentes del mismo origen (el mismo servidor, el mismo editor). */
  readonly origen: string;
  readonly indice: Indice;
  /** Los documentos de esas claves, en la versión del índice. */
  trozos(claves: string[]): Promise<Record<string, unknown>>;
  /** El resumen del terreno de esas celdas (base64, vacío si no tienen). */
  resumenes(claves: string[]): Promise<Record<string, string>>;
}

/** Lo bajado ya no está en el servidor (se podó esa versión): hay que pedir el índice otra vez. */
export class Caducado extends Error {}

interface Celda {
  objetos: Objeto[];
  terreno?: Terreno;
}

/** Celdas que se piden de una vez (el tope del servidor, `MAX_LOTE`) y peticiones a la vez: al entrar son ~130 celdas, dos idas y vueltas. */
const LOTE = 64;
const A_LA_VEZ = 2;
/** Una celda cargada se suelta pasada su distancia de carga más esto (m), para no ir y venir en el borde. */
const HISTERESIS = 60;
/** Tras un fallo de red, lo que se espera antes de volver a pedir (ms). */
const ESPERA_FALLO = 4000;

const B = 1 << 20;
const coords = (n: number): [number, number] => [Math.floor(n / (2 * B)) - B, (n % (2 * B)) - B];
const clave = (cx: number, cz: number): string => `c/${cx}/${cz}`;
/** Distancia de (x, z) al punto más cercano de la celda. */
const aCelda = (cx: number, cz: number, x: number, z: number): number =>
  Math.hypot(Math.max(cx * CELDA - x, 0, x - (cx + 1) * CELDA), Math.max(cz * CELDA - z, 0, z - (cz + 1) * CELDA));

export class Cercania {
  /** Metros alrededor de la cámara cuyas celdas se cargan enteras (objetos, colisiones y suelo). */
  radio = 480;
  /** Se llama (como mucho una vez por `trabajar`) cuando cambia lo cargado: el documento de `doc()` es otro. */
  alCambiar: () => void = () => undefined;
  /** Se llama si el servidor ya no tiene lo que dice el índice: hay que pedir otro (`cambiarFuente`). */
  alCaducar: () => void = () => undefined;
  private fuente: Fuente | null = null;
  /** Celdas montadas (con la versión de su trozo; 0 si no está en el índice) y las que esperan a montarse con su documento. */
  private montadas = new Map<number, number>();
  private celdas = new Map<number, Celda>();
  private listas = new Map<number, { version: number; doc: Celda | null }>();
  private pedidas = new Set<number>();
  private cola: number[] = [];
  private enVuelo = 0;
  /** Assets bajados (id → versión) y los que se están bajando. */
  private assets = new Map<string, number>();
  private docsAssets = new Map<string, Asset>();
  private assetsPedidos = new Map<string, Promise<void>>();
  /** Celdas lejanas con su resumen puesto (n → versión) y las pedidas. */
  private lejanas = new Map<number, number>();
  private lejanasPedidas = new Set<number>();
  private x = 0;
  private z = 0;
  private celdaCentro = NaN;
  private mirar = true;
  private cambiado = false;
  private esperaHasta = 0;
  private docCache: MundoDoc | null = null;

  constructor(
    private readonly obras: Obras,
    private readonly suelo: Suelo | null,
  ) {}

  get indice(): Indice | null {
    return this.fuente?.indice ?? null;
  }

  /** Celdas montadas y las que faltan por montar o bajar (las devtools y las pruebas). */
  get estado(): { montadas: number; pendientes: number; lejanas: number; assets: number } {
    return { montadas: this.montadas.size, pendientes: this.pedidas.size + this.listas.size, lejanas: this.lejanas.size, assets: this.assets.size };
  }

  /**
   * Pasa a otra fuente. Lo montado se queda mientras llega lo nuevo: solo se vuelven a bajar las celdas, los assets y los resúmenes
   * cuya versión ha cambiado, y se sueltan los que ya no están.
   */
  cambiarFuente(f: Fuente): void {
    const antes = this.fuente;
    this.fuente = f;
    this.pedidas.clear();
    this.cola = [];
    this.listas.clear();
    this.lejanasPedidas.clear();
    this.assetsPedidos.clear();
    this.esperaHasta = 0;
    const t = f.indice.trozos;
    const mismo = antes?.origen === f.origen;
    if (antes) {
      for (const [id, v] of this.assets) {
        if (mismo && t.get(`a/${id}`) === v) continue;
        this.assets.delete(id);
        if (!t.has(`a/${id}`)) {
          this.obras.quitarAsset(id);
          this.docsAssets.delete(id);
        }
      }
      for (const [n, v] of this.lejanas) if (!mismo || (t.get(clave(...coords(n))) ?? 0) !== v) this.lejanas.delete(n);
    }
    // Los assets cambiados se vuelven a bajar con las celdas que los usan: se vuelven a pedir todas las que los usen.
    for (const [n, v] of this.montadas) {
      const usaCambiado = this.celdas.get(n)?.objetos.some((o) => !this.assets.has(o.asset)) ?? false;
      if (!mismo || (t.get(clave(...coords(n))) ?? 0) !== v || usaCambiado) this.montadas.set(n, -1);
    }
    this.mirar = true;
    this.cambiado = true;
  }

  /** Con la cámara en (x, z): decide qué bajar y qué soltar. Barato si no ha cambiado de celda. */
  actualizar(x: number, z: number): void {
    this.x = x;
    this.z = z;
    this.suelo?.actualizar(x, z);
    const f = this.fuente;
    if (!f) return;
    const centro = numeroDe(Math.floor(x / CELDA), Math.floor(z / CELDA));
    if (centro !== this.celdaCentro || this.mirar) {
      this.celdaCentro = centro;
      this.mirar = false;
      this.planificar(f);
    }
    this.pedir(f);
  }

  /** Monta lo bajado y deja que obras y suelo rehagan lo suyo, hasta `hasta` (ms de `performance.now`). Dice si queda algo. */
  trabajar(hasta: number): boolean {
    if (this.listas.size) {
      const orden = [...this.listas.keys()].sort((a, b) => this.distancia(a) - this.distancia(b));
      for (const n of orden) {
        if (performance.now() > hasta) break;
        const l = this.listas.get(n)!;
        this.listas.delete(n);
        this.montar(n, l.version, l.doc);
      }
    }
    let queda = this.obras.trabajar(hasta);
    queda = (this.suelo?.trabajar(hasta) ?? false) || queda;
    if (this.cambiado) {
      this.cambiado = false;
      this.docCache = null;
      this.alCambiar();
    }
    return queda || this.listas.size > 0 || this.pedidas.size > 0;
  }

  /** Lo cargado como documento: los assets bajados, los objetos de las celdas montadas, las zonas y los teleportes. */
  doc(): MundoDoc {
    if (this.docCache) return this.docCache;
    const i = this.fuente?.indice;
    const objetos: Objeto[] = [];
    for (const c of this.celdas.values()) for (const o of c.objetos) objetos.push(o);
    const doc: MundoDoc = { v: 2, assets: Object.fromEntries(this.docsAssets), objetos, zonas: i?.zonas ?? {} };
    if (i?.teleportes) doc.teleportes = i.teleportes;
    return (this.docCache = doc);
  }

  /** Suelta todo lo montado. */
  vaciar(): void {
    for (const n of [...this.montadas.keys()]) this.soltar(n);
    for (const n of this.lejanas.keys()) this.suelo?.quitarResumen(...coords(n));
    this.lejanas.clear();
    this.assets.clear();
    this.docsAssets.clear();
    this.fuente = null;
    this.cambiado = true;
  }

  private distancia(n: number): number {
    const [cx, cz] = coords(n);
    return aCelda(cx, cz, this.x, this.z);
  }

  private planificar(f: Fuente): void {
    const t = f.indice.trozos;
    const { x, z, radio } = this;
    // Soltar lo que se ha quedado lejos.
    for (const n of [...this.montadas.keys()]) if (this.distancia(n) > radio + HISTERESIS) this.soltar(n);
    for (const n of [...this.lejanas.keys()]) {
      if (this.distancia(n) <= LEJOS + HISTERESIS) continue;
      this.lejanas.delete(n);
      this.suelo?.quitarResumen(...coords(n));
    }
    // Pedir lo que falta: las celdas cercanas enteras y, de las lejanas con trozo, el resumen.
    const cerca: number[] = [];
    const lejos: string[] = [];
    const [c0x, c1x] = [Math.floor((x - LEJOS) / CELDA), Math.floor((x + LEJOS) / CELDA)];
    const [c0z, c1z] = [Math.floor((z - LEJOS) / CELDA), Math.floor((z + LEJOS) / CELDA)];
    for (let cx = c0x; cx <= c1x; cx++) {
      for (let cz = c0z; cz <= c1z; cz++) {
        const d = aCelda(cx, cz, x, z);
        if (d > LEJOS) continue;
        const n = numeroDe(cx, cz);
        const k = clave(cx, cz);
        if (d <= radio) {
          const v = this.montadas.get(n);
          if (v !== undefined && v >= 0) continue;
          if (this.pedidas.has(n) || this.listas.has(n)) continue;
          if (t.has(k)) cerca.push(n);
          else this.listas.set(n, { version: 0, doc: null }); // sin trozo: vacía (la isla de la fórmula, o mar)
        } else if (t.has(k) && !this.montadas.has(n) && !this.lejanas.has(n) && !this.lejanasPedidas.has(n)) lejos.push(k);
      }
    }
    this.cola = [...new Set([...this.cola, ...cerca])].sort((a, b) => this.distancia(a) - this.distancia(b));
    for (const n of cerca) this.pedidas.add(n);
    for (let i = 0; i < lejos.length; i += 64) this.pedirResumenes(f, lejos.slice(i, i + 64));
  }

  private pedir(f: Fuente): void {
    while (this.enVuelo < A_LA_VEZ && this.cola.length && performance.now() >= this.esperaHasta) {
      const lote = this.cola.splice(0, LOTE);
      this.enVuelo++;
      this.bajar(f, lote).finally(() => this.enVuelo--);
    }
  }

  private async bajar(f: Fuente, lote: number[]): Promise<void> {
    const claves = lote.map((n) => clave(...coords(n)));
    try {
      const docs = await f.trozos(claves);
      if (this.fuente !== f) return;
      const celdas = lote.map((n, i) => [n, (docs[claves[i]] as Celda | undefined) ?? null] as const);
      // Los assets que usan y aún no están, antes de montar nada: un objeto sin su asset no se dibuja.
      const ids = new Set<string>();
      for (const [, c] of celdas) for (const o of c?.objetos ?? []) if (!this.assets.has(o.asset)) ids.add(o.asset);
      await this.bajarAssets(f, [...ids]);
      if (this.fuente !== f) return;
      for (const [n, c] of celdas) {
        if (!this.pedidas.delete(n)) continue;
        this.listas.set(n, { version: f.indice.trozos.get(clave(...coords(n))) ?? 0, doc: c });
      }
    } catch (e) {
      if (this.fuente !== f) return;
      for (const n of lote) this.pedidas.delete(n);
      if (e instanceof Caducado) return this.alCaducar();
      console.warn("No se han podido bajar unas celdas del mundo.", e);
      this.esperaHasta = performance.now() + ESPERA_FALLO;
      this.mirar = true;
    }
  }

  private async bajarAssets(f: Fuente, ids: string[]): Promise<void> {
    const faltan = ids.filter((id) => !this.assetsPedidos.has(id) && f.indice.trozos.has(`a/${id}`));
    for (let i = 0; i < faltan.length; i += 64) {
      const tanda = faltan.slice(i, i + 64);
      const p = f.trozos(tanda.map((id) => `a/${id}`)).then((docs) => {
        if (this.fuente !== f) return;
        for (const id of tanda) {
          const a = docs[`a/${id}`] as Asset | undefined;
          if (!a) continue;
          this.assets.set(id, f.indice.trozos.get(`a/${id}`) ?? 0);
          this.docsAssets.set(id, a);
          this.obras.ponerAsset(id, a);
        }
      });
      // Si falla, se olvida: la próxima celda que lo use lo vuelve a pedir.
      p.catch(() => tanda.forEach((id) => this.assetsPedidos.get(id) === p && this.assetsPedidos.delete(id)));
      for (const id of tanda) this.assetsPedidos.set(id, p);
    }
    await Promise.all(ids.map((id) => this.assetsPedidos.get(id)));
  }

  private pedirResumenes(f: Fuente, claves: string[]): void {
    const ns = claves.map((k) => {
      const [cx, cz] = coordsDeClave(k) as [number, number];
      return numeroDe(cx, cz);
    });
    for (const n of ns) this.lejanasPedidas.add(n);
    f.resumenes(claves).then(
      (r) => {
        if (this.fuente !== f) return;
        claves.forEach((k, i) => {
          const n = ns[i];
          this.lejanasPedidas.delete(n);
          if (this.montadas.has(n) || this.distancia(n) > LEJOS + HISTERESIS) return;
          this.lejanas.set(n, f.indice.trozos.get(k) ?? 0);
          this.suelo?.ponerResumen(...coords(n), r[k] ?? "");
        });
      },
      (e: unknown) => {
        if (this.fuente !== f) return;
        for (const n of ns) this.lejanasPedidas.delete(n);
        if (e instanceof Caducado) this.alCaducar();
      },
    );
  }

  private montar(n: number, version: number, doc: Celda | null): void {
    const [cx, cz] = coords(n);
    if (this.distancia(n) > this.radio + HISTERESIS) return; // se ha quedado lejos mientras bajaba
    if (this.lejanas.delete(n)) this.suelo?.quitarResumen(cx, cz);
    this.montadas.set(n, version);
    if (doc?.objetos.length) {
      this.celdas.set(n, doc);
      this.obras.ponerCelda(cx, cz, doc.objetos);
    } else if (this.celdas.delete(n)) this.obras.quitarCelda(cx, cz);
    this.suelo?.ponerCelda(cx, cz, doc?.terreno);
    this.cambiado = true;
  }

  private soltar(n: number): void {
    const [cx, cz] = coords(n);
    this.montadas.delete(n);
    if (this.celdas.delete(n)) this.obras.quitarCelda(cx, cz);
    this.suelo?.quitarCelda(cx, cz);
    this.cambiado = true;
  }
}

/** Una fuente hecha de un documento entero (el mundo de serie, el borrador del editor, el extraplano). */
export function fuenteLocal(doc: MundoDoc, previa?: Fuente | null): Fuente {
  const textos = aTrozos(doc);
  const antes = previa instanceof FuenteLocal ? previa : null;
  const trozos = new Map<string, number>();
  let siguiente = antes ? antes.siguiente : 1;
  for (const [k, v] of textos) {
    const igual = antes?.textos.get(k) === v;
    trozos.set(k, igual ? (antes!.indice.trozos.get(k) as number) : siguiente++);
  }
  const origen = antes?.origen ?? `local:${++locales}`;
  return new FuenteLocal(origen, textos, { version: 0, zonas: doc.zonas, teleportes: doc.teleportes ?? null, trozos }, siguiente);
}
let locales = 0;

class FuenteLocal implements Fuente {
  constructor(
    readonly origen: string,
    readonly textos: Map<string, string>,
    readonly indice: Indice,
    readonly siguiente: number,
  ) {}

  trozos(claves: string[]): Promise<Record<string, unknown>> {
    const out: Record<string, unknown> = {};
    for (const k of claves) {
      const t = this.textos.get(k);
      if (t !== undefined) out[k] = JSON.parse(t);
    }
    return Promise.resolve(out);
  }

  resumenes(claves: string[]): Promise<Record<string, string>> {
    const out: Record<string, string> = {};
    for (const k of claves) {
      const t = this.textos.get(k);
      const terreno = t ? (JSON.parse(t) as Celda).terreno : undefined;
      out[k] = terreno ? resumenDe(terreno) : "";
    }
    return Promise.resolve(out);
  }
}

/** Respuesta de `/api/mundo/indice`. */
interface IndiceServidor {
  version: number;
  zonas: { zonas: Record<string, Zona> } | null;
  teleportes: { teleportes: Teleporte[] } | null;
  trozos: Record<string, number>;
}

/**
 * La fuente del mundo publicado de un servidor: pide su índice (con la versión, la URL se guarda para siempre en el navegador) y luego
 * los trozos por lotes, cada uno con la versión de su fila (`MUNDO_CACHE`: tampoco cambian). `null` si no hay nada publicado.
 */
export async function fuenteServidor(sesion: { session: string; guildId: string }, version?: number): Promise<Fuente | null> {
  const q = `guild_id=${encodeURIComponent(sesion.guildId)}`;
  const pedir = async <T>(ruta: string): Promise<T> => {
    const r = await fetch(ruta, { headers: { Authorization: `Bearer ${sesion.session}` } });
    if (r.status === 410) throw new Caducado("El mundo ha cambiado.");
    const datos = (await r.json().catch(() => null)) as (T & { error?: string }) | null;
    if (!r.ok || !datos) throw new Error(datos?.error ?? `HTTP ${r.status}`);
    return datos;
  };
  const i = await pedir<IndiceServidor>(`/api/mundo/indice?${q}${version ? `&v=${version}` : ""}`);
  if (!i.version) return null;
  const indice: Indice = {
    version: i.version,
    zonas: i.zonas?.zonas ?? null,
    teleportes: i.teleportes?.teleportes ?? null,
    trozos: new Map(Object.entries(i.trozos)),
  };
  const lote = (claves: string[]): string => claves.map((k) => `${k}@${indice.trozos.get(k)}`).join(",");
  return {
    origen: `servidor:${sesion.guildId}`,
    indice,
    trozos: async (claves) => {
      const pedidas = claves.filter((k) => indice.trozos.has(k));
      if (!pedidas.length) return {};
      return (await pedir<{ trozos: Record<string, unknown> }>(`/api/mundo/trozos?${q}&t=${encodeURIComponent(lote(pedidas))}`)).trozos;
    },
    resumenes: async (claves) => {
      const pedidas = claves.filter((k) => indice.trozos.has(k));
      if (!pedidas.length) return {};
      return (await pedir<{ resumenes: Record<string, string> }>(`/api/mundo/trozos?${q}&resumen=1&t=${encodeURIComponent(lote(pedidas))}`)).resumenes;
    },
  };
}
