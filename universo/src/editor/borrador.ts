// El borrador del mundo que comparten los modos «Mundo» y «Assets»: el documento, el historial de deshacer (solo los trozos que
// cambian en cada paso, con tope), el guardado automático por trozos y las llamadas al servidor. No toca la pantalla: avisa por ganchos.
// El documento sigue siendo el `MundoDoc` plano; solo se parte en trozos (`aTrozos`) al comparar y guardar, y se junta al cargar.
import * as api from "./api";
import { Conflicto, type Sesion, type TrozoBorrador, type Version } from "./api";
import type { MundoDoc } from "./formato";
import { aTrozos, deTrozos } from "./trozos";

const AUTOGUARDADO_MS = 20_000;
export const MAX_HISTORIAL = 50;
/** Bytes de un guardado como mucho; el servidor acepta 8 MB por petición (`MAX_EDITOR_BYTES`). Cada tanda va todo o nada. */
const MAX_TANDA = 4 * 1024 * 1024;
/** Trozos de un guardado como mucho (`MAX_SAVE_PIECES` del servidor). */
const MAX_TROZOS = 5000;

/** Trozos de un paso del historial: clave → JSON, o `null` si el trozo no existe en ese lado. */
type Cambios = Map<string, string | null>;
interface Paso {
  antes: Cambios;
  despues: Cambios;
}

export interface Ganchos {
  /** Un fallo del servidor o de la red. */
  fallo(e: unknown): void;
  /** Cambió algo que se ve en la barra de estado (guardando, sucio, versión). */
  estado(): void;
  /** El documento entero se ha sustituido (deshacer, rehacer, recargar, volver a una versión). */
  reemplazado(): void;
}

export class Borrador {
  doc: MundoDoc;
  version: number;
  sucio = false;
  guardando = false;
  conflicto = false;
  /** `hist[i]` lleva lo que cambió al pasar del estado i - 1 al i; el 0 es el de partida y no lleva nada. */
  hist: Paso[] = [];
  hi = 0;
  /** Los trozos del último cierre de cambio (`commit`), el estado al que apunta `hi`. */
  private actual = new Map<string, string>();
  /** Los trozos como los tiene el servidor, y el `rev` de cada clave que alguna vez existió (también las borradas). */
  private guardado = new Map<string, string>();
  private revs = new Map<string, number>();
  private cambios = 0;
  private timer = 0;

  /** `semilla`: el mundo con el que empezar si el servidor no tiene nada; queda sucio para que se guarde. */
  constructor(
    private readonly sesion: Sesion,
    b: { version: number; trozos: Record<string, TrozoBorrador> },
    private readonly g: Ganchos,
    semilla?: MundoDoc,
  ) {
    this.doc = this.cargar(b, semilla);
    this.version = b.version;
    this.sucio = semilla !== undefined;
  }

  /** Deja el borrador como lo tiene el servidor (o como la semilla) y devuelve el documento. */
  private cargar(b: { trozos: Record<string, TrozoBorrador> }, semilla?: MundoDoc): MundoDoc {
    const docs: Record<string, unknown> = {};
    this.revs = new Map();
    for (const [k, t] of Object.entries(b.trozos)) {
      this.revs.set(k, t.rev);
      if (t.doc) docs[k] = t.doc;
    }
    const doc = semilla ?? deTrozos(docs);
    this.actual = aTrozos(doc);
    // El servidor se compara con el documento ya partido y no con su texto: lo que solo cambia de forma no se vuelve a mandar.
    this.guardado = semilla ? new Map() : new Map(this.actual);
    this.hist = [{ antes: new Map(), despues: new Map() }];
    this.hi = 0;
    return doc;
  }

  iniciar(): void {
    this.timer = window.setInterval(() => {
      if (this.sucio && !this.guardando && !this.conflicto) void this.guardar();
    }, AUTOGUARDADO_MS);
  }

  parar(): void {
    clearInterval(this.timer);
  }

  /** Cierra un cambio: entra en el historial y el borrador queda sucio. Devuelve si algo cambió de verdad. */
  commit(): boolean {
    const nuevo = aTrozos(this.doc);
    const paso: Paso = { antes: new Map(), despues: new Map() };
    for (const [k, v] of nuevo) {
      if (this.actual.get(k) !== v) {
        paso.antes.set(k, this.actual.get(k) ?? null);
        paso.despues.set(k, v);
      }
    }
    for (const [k, v] of this.actual) {
      if (!nuevo.has(k)) {
        paso.antes.set(k, v);
        paso.despues.set(k, null);
      }
    }
    if (paso.despues.size === 0) return false;
    this.actual = nuevo;
    this.hist.length = this.hi + 1;
    this.hist.push(paso);
    if (this.hist.length > MAX_HISTORIAL) this.hist.shift();
    this.hi = this.hist.length - 1;
    this.marcarSucio();
    return true;
  }

  /** Un paso intermedio (arrastrar, elegir un color): no entra en el historial hasta el `commit`. */
  marcarSucio(): void {
    this.sucio = true;
    this.cambios++;
    this.g.estado();
  }

  irA(i: number): void {
    if (i < 0 || i >= this.hist.length) return;
    const trozos = new Map(this.actual);
    const aplicar = (c: Cambios): void => {
      for (const [k, v] of c) {
        if (v === null) trozos.delete(k);
        else trozos.set(k, v);
      }
    };
    for (let j = this.hi; j > i; j--) aplicar(this.hist[j].antes);
    for (let j = this.hi + 1; j <= i; j++) aplicar(this.hist[j].despues);
    this.hi = i;
    this.actual = trozos;
    const docs: Record<string, unknown> = {};
    for (const [k, v] of trozos) docs[k] = JSON.parse(v);
    this.doc = deTrozos(docs);
    this.marcarSucio();
    this.g.reemplazado();
  }

  async guardar(): Promise<boolean> {
    if (this.guardando || this.conflicto) return false;
    this.guardando = true;
    this.g.estado();
    const n = this.cambios;
    try {
      const ahora = aTrozos(this.doc);
      const distintos: [string, string | null][] = [];
      for (const [k, v] of ahora) if (this.guardado.get(k) !== v) distintos.push([k, v]);
      for (const k of this.guardado.keys()) if (!ahora.has(k)) distintos.push([k, null]);
      // El servidor comprueba los assets de las celdas en cada tanda: primero los assets nuevos, al final los que se borran.
      const orden = ([k, v]: [string, string | null]): number => (k.startsWith("a/") ? (v === null ? 2 : 0) : 1);
      const tandas = partir(distintos.sort((a, b) => orden(a) - orden(b)));
      for (const tanda of tandas) {
        const { revs } = await api.guardar(
          this.sesion,
          tanda.map(([k, v]) => [k, this.revs.get(k) ?? 0, v]),
        );
        // Cada tanda es definitiva: si falla la siguiente, la que ya está no se vuelve a mandar.
        for (const [k, v] of tanda) {
          if (v === null) this.guardado.delete(k);
          else this.guardado.set(k, v);
          this.revs.set(k, revs[k]);
        }
      }
      if (n === this.cambios) this.sucio = false;
      return true;
    } catch (e) {
      this.fallar(e);
      return false;
    } finally {
      this.guardando = false;
      this.g.estado();
    }
  }

  async recargar(): Promise<void> {
    try {
      this.doc = this.cargar(await api.borrador(this.sesion));
      this.sucio = false;
      this.conflicto = false;
      this.g.reemplazado();
      this.g.estado();
    } catch (e) {
      this.fallar(e);
    }
  }

  /** Guarda si hace falta y publica. Devuelve la versión, o `null` si no se pudo. */
  async publicar(): Promise<number | null> {
    if (this.conflicto) return null;
    if (this.sucio && !(await this.guardar())) return null;
    try {
      this.version = (await api.publicar(this.sesion)).version;
      return this.version;
    } catch (e) {
      this.fallar(e);
      return null;
    } finally {
      this.g.estado();
    }
  }

  async versiones(): Promise<Version[] | null> {
    try {
      return (await api.versiones(this.sesion)).versiones;
    } catch (e) {
      this.fallar(e);
      return null;
    }
  }

  async volverA(version: number): Promise<void> {
    try {
      await api.volver(this.sesion, version);
    } catch (e) {
      return this.fallar(e);
    }
    await this.recargar();
  }

  private fallar(e: unknown): void {
    if (e instanceof Conflicto) this.conflicto = true;
    this.g.fallo(e);
  }
}

const codificador = new TextEncoder();

/** Reparte los trozos en tandas de `MAX_TANDA` bytes como mucho (un trozo suelto nunca pasa de 1 MB). */
function partir(trozos: [string, string | null][]): [string, string | null][][] {
  const tandas: [string, string | null][][] = [];
  let tanda: [string, string | null][] = [];
  let bytes = 0;
  for (const t of trozos) {
    const b = t[1] === null ? 4 : codificador.encode(t[1]).length;
    if (tanda.length > 0 && (bytes + b > MAX_TANDA || tanda.length >= MAX_TROZOS)) {
      tandas.push(tanda);
      tanda = [];
      bytes = 0;
    }
    tanda.push(t);
    bytes += b;
  }
  if (tanda.length > 0) tandas.push(tanda);
  return tandas;
}
