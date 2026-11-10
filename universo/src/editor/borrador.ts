// El borrador del mundo que comparten los modos «Mundo» y «Assets»: el documento, el historial de deshacer (solo los trozos que
// cambian en cada paso, con tope), el guardado automático por trozos y las llamadas al servidor. No toca la pantalla: avisa por ganchos.
// El documento sigue siendo el `MundoDoc` plano. Cada cambio solo convierte a JSON los trozos que toca (`Toca` y lo que se ve solo,
// ver `commit`); deshacer y rehacer solo leen los del paso (`parchear`), y guardar solo compara los que cambiaron desde el último.
import * as api from "./api";
import { Conflicto, type Sesion, type TrozoBorrador, type Version } from "./api";
import type { MundoDoc, Objeto, Terreno } from "./formato";
import { aTrozos, claveDeNumero, deTrozos, numeroCelda, numeroDeClave, parchear, partirTerreno, trozoCelda, trozosSueltos } from "./trozos";

const AUTOGUARDADO_MS = 20_000;
export const MAX_HISTORIAL = 50;
/** Bytes de un guardado como mucho; el servidor acepta 8 MB por petición (`MAX_EDITOR_BYTES`). Cada tanda va todo o nada; un guardado grande va en varias. */
const MAX_TANDA = 4 * 1024 * 1024;
/** Trozos de un guardado como mucho (`MAX_SAVE_PIECES` del servidor). */
const MAX_TROZOS = 5000;

/** Trozos de un paso del historial: clave → JSON, o `null` si el trozo no existe en ese lado. */
type Cambios = Map<string, string | null>;
interface Paso {
  antes: Cambios;
  despues: Cambios;
}

/**
 * Lo que un cambio ha tocado por dentro: objetos editados en su sitio y assets editados. Lo demás se ve solo: objetos nuevos,
 * borrados o que cambian de celda, assets nuevos o borrados, zonas, teleportes y terreno.
 */
export interface Toca {
  objetos?: Iterable<Objeto>;
  assets?: Iterable<string>;
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
  /** Lo que pesa el borrador (los trozos de `actual` en JSON): solo un dato para la barra, el mundo no tiene tope. */
  bytes = 0;
  /** `hist[i]` lleva lo que cambió al pasar del estado i - 1 al i; el 0 es el de partida y no lleva nada. */
  hist: Paso[] = [];
  hi = 0;
  /** En desarrollo (o desde una prueba), cada `commit` con `Toca` y cada deshacer se comparan con el camino entero. */
  static comprobar = import.meta.env.DEV;
  /** Veces que el camino rápido no dio lo mismo que el entero (y se usó el entero). */
  static descuadres = 0;
  /** Los trozos del último cierre de cambio (`commit`), el estado al que apunta `hi`. */
  private actual = new Map<string, string>();
  /** Los trozos como los tiene el servidor, y el `rev` de cada clave que alguna vez existió (también las borradas). */
  private guardado = new Map<string, string>();
  private revs = new Map<string, number>();
  /** Las claves que han cambiado desde el último guardado: solo esas se comparan con `guardado`. */
  private pendientes = new Set<string>();
  /** Lo que había en `actual`, para ver solo lo que cambia: la celda de cada objeto, los assets y el terreno de cada celda. */
  private celdas = new Map<Objeto, number>();
  private ids = new Set<string>();
  private terreno: Map<string, Terreno> | undefined;
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
    this.bytes = 0;
    for (const v of this.actual.values()) this.bytes += codificador.encode(v).length;
    // El servidor se compara con el documento ya partido y no con su texto: lo que solo cambia de forma no se vuelve a mandar.
    this.guardado = semilla ? new Map() : new Map(this.actual);
    this.pendientes = semilla ? new Set(this.actual.keys()) : new Set();
    this.hist = [{ antes: new Map(), despues: new Map() }];
    this.hi = 0;
    this.indexar(doc);
    return doc;
  }

  private indexar(doc: MundoDoc): void {
    this.celdas = new Map();
    for (const o of doc.objetos) this.celdas.set(o, numeroCelda(o.pos[0], o.pos[2]));
    this.ids = new Set(Object.keys(doc.assets));
    this.terreno = doc.terreno && partirTerreno(doc.terreno);
  }

  iniciar(): void {
    this.timer = window.setInterval(() => {
      if (this.sucio && !this.guardando && !this.conflicto) void this.guardar();
    }, AUTOGUARDADO_MS);
  }

  parar(): void {
    clearInterval(this.timer);
  }

  /**
   * Cierra un cambio: entra en el historial y el borrador queda sucio. Devuelve si algo cambió de verdad. Con `toca` solo se
   * convierten a JSON los trozos tocados; sin él (quien no sabe qué ha tocado), el mundo entero.
   */
  commit(toca?: Toca): boolean {
    let nuevos = toca && this.tocados(toca);
    if (nuevos && Borrador.comprobar && !this.cuadra(nuevos)) nuevos = undefined;
    nuevos ??= this.todos();
    const paso: Paso = { antes: new Map(), despues: new Map() };
    for (const [k, v] of nuevos) {
      const a = this.actual.get(k) ?? null;
      if (a === v) continue;
      paso.antes.set(k, a);
      paso.despues.set(k, v);
      this.poner(k, v);
      this.pendientes.add(k);
    }
    if (paso.despues.size === 0) return false;
    this.hist.length = this.hi + 1;
    this.hist.push(paso);
    if (this.hist.length > MAX_HISTORIAL) this.hist.shift();
    this.hi = this.hist.length - 1;
    this.marcarSucio();
    return true;
  }

  /** Cambia un trozo de `actual` llevando la cuenta de `bytes` (solo codifica ese trozo). */
  private poner(k: string, v: string | null): void {
    const a = this.actual.get(k);
    if (a !== undefined) this.bytes -= codificador.encode(a).length;
    if (v === null) this.actual.delete(k);
    else {
      this.actual.set(k, v);
      this.bytes += codificador.encode(v).length;
    }
  }

  /** Un paso intermedio (arrastrar, elegir un color): no entra en el historial hasta el `commit`. */
  marcarSucio(): void {
    this.sucio = true;
    this.cambios++;
    this.g.estado();
  }

  /** Todos los trozos, partiendo el mundo entero (el camino de siempre). */
  private todos(): Cambios {
    const todo: Cambios = aTrozos(this.doc);
    for (const k of this.actual.keys()) if (!todo.has(k)) todo.set(k, null);
    this.indexar(this.doc);
    return todo;
  }

  /** Solo los trozos que ha podido cambiar el paso desde `actual`: lo que dice `toca` y lo que se ve solo. */
  private tocados(toca: Toca): Cambios {
    const doc = this.doc;
    const celdas = new Set<number>();
    // Objetos nuevos o que han cambiado de celda; los que faltan se buscan solo si quedan menos de los que había.
    const habia = this.celdas.size;
    let siguen = 0;
    for (const o of doc.objetos) {
      const c = numeroCelda(o.pos[0], o.pos[2]);
      const p = this.celdas.get(o);
      if (p !== undefined) siguen++;
      if (p === c) continue;
      if (p !== undefined) celdas.add(p);
      celdas.add(c);
      this.celdas.set(o, c);
    }
    if (siguen < habia) {
      const vivos = new Set(doc.objetos);
      for (const [o, c] of this.celdas) {
        if (vivos.has(o)) continue;
        celdas.add(c);
        this.celdas.delete(o);
      }
    }
    for (const o of toca.objetos ?? []) {
      const c = this.celdas.get(o);
      if (c !== undefined) celdas.add(c);
    }
    // El terreno: con el mismo, `partirTerreno` da el mismo `Map`; si no, las celdas cuyo terreno cambia.
    const terreno = doc.terreno && partirTerreno(doc.terreno);
    if (terreno !== this.terreno) {
      for (const k of new Set([...(terreno?.keys() ?? []), ...(this.terreno?.keys() ?? [])])) {
        const a = this.terreno?.get(k);
        const b = terreno?.get(k);
        if (a?.alturas !== b?.alturas || a?.colores !== b?.colores) celdas.add(numeroDeClave(k) as number);
      }
      this.terreno = terreno;
    }
    const nuevos: Cambios = new Map(trozosSueltos(doc));
    for (const k of this.actual.keys()) if (k.startsWith("i/") && !nuevos.has(k)) nuevos.set(k, null); // un interior que ya no está
    if (celdas.size) {
      const objetos = new Map<number, Objeto[]>();
      for (const c of celdas) objetos.set(c, []);
      for (const o of doc.objetos) objetos.get(numeroCelda(o.pos[0], o.pos[2]))?.push(o);
      for (const [c, os] of objetos) {
        const k = claveDeNumero(c);
        nuevos.set(k, trozoCelda(os, terreno?.get(k)));
      }
    }
    // Assets: los que dice `toca` y los que han aparecido o desaparecido.
    const ids = new Set(Object.keys(doc.assets));
    const assets = new Set(toca.assets ?? []);
    for (const id of ids) if (!this.ids.has(id)) assets.add(id);
    for (const id of this.ids) if (!ids.has(id)) assets.add(id);
    this.ids = ids;
    for (const id of assets) nuevos.set(`a/${id}`, doc.assets[id] ? JSON.stringify(doc.assets[id]) : null);
    return nuevos;
  }

  /** ¿Dejar `actual` con `nuevos` da lo mismo que partir el mundo entero? Si no, lo cuenta y lo dice en la consola. */
  private cuadra(nuevos: Cambios): boolean {
    const despues = new Map(this.actual);
    for (const [k, v] of nuevos) {
      if (v === null) despues.delete(k);
      else despues.set(k, v);
    }
    const todo = aTrozos(this.doc);
    const malos = [...new Set([...todo.keys(), ...despues.keys()])].filter((k) => todo.get(k) !== despues.get(k));
    if (!malos.length) return true;
    Borrador.descuadres++;
    console.error("El borrador por trozos no da lo mismo que el mundo entero en", malos.slice(0, 10));
    return false;
  }

  /** Deshace o rehace hasta el estado `i`: solo se leen los trozos que cambian por el camino. */
  irA(i: number): void {
    if (i < 0 || i >= this.hist.length) return;
    const cambios: Cambios = new Map();
    for (let j = this.hi; j > i; j--) for (const [k, v] of this.hist[j].antes) cambios.set(k, v);
    for (let j = this.hi + 1; j <= i; j++) for (const [k, v] of this.hist[j].despues) cambios.set(k, v);
    this.hi = i;
    const docs: Record<string, unknown> = {};
    for (const [k, v] of cambios) {
      this.poner(k, v);
      this.pendientes.add(k);
      docs[k] = v === null ? null : JSON.parse(v);
    }
    const antes = this.doc;
    this.doc = parchear(antes, docs);
    // El índice, como `parchear`: fuera los objetos de las celdas cambiadas; los suyos nuevos van al final de la lista.
    const celdas = new Set<number>();
    for (const k of cambios.keys()) {
      const n = numeroDeClave(k);
      if (n !== null) celdas.add(n);
    }
    if (celdas.size) {
      let quedan = 0;
      for (const o of antes.objetos) {
        if (celdas.has(numeroCelda(o.pos[0], o.pos[2]))) this.celdas.delete(o);
        else quedan++;
      }
      for (const o of this.doc.objetos.slice(quedan)) this.celdas.set(o, numeroCelda(o.pos[0], o.pos[2]));
    }
    this.ids = new Set(Object.keys(this.doc.assets));
    this.terreno = this.doc.terreno && partirTerreno(this.doc.terreno);
    if (Borrador.comprobar) this.cuadra(new Map());
    this.marcarSucio();
    this.g.reemplazado();
  }

  async guardar(): Promise<boolean> {
    if (this.guardando || this.conflicto) return false;
    this.guardando = true;
    this.g.estado();
    const n = this.cambios;
    // Se guarda lo cerrado con `commit` (lo de un arrastre a medias entra con su `commit`, que vuelve a dejarlo sucio).
    const claves = this.pendientes;
    this.pendientes = new Set();
    try {
      const distintos: [string, string | null][] = [];
      for (const k of claves) {
        const v = this.actual.get(k) ?? null;
        if ((this.guardado.get(k) ?? null) !== v) distintos.push([k, v]);
      }
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
      for (const k of claves) this.pendientes.add(k); // lo que sí se guardó ya no sale distinto la próxima vez
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
