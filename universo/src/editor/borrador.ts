// El borrador del mundo que comparten los modos «Mundo» y «Assets»: el documento, el historial de deshacer (solo los trozos que
// cambian en cada paso, con tope), el guardado automático por trozos y las llamadas al servidor. No toca la pantalla: avisa por ganchos.
// El documento sigue siendo el `MundoDoc` plano. Cada cambio solo convierte a JSON los trozos que toca (`Toca` y lo que se ve solo,
// ver `commit`); deshacer y rehacer solo leen los del paso (`parchear`), y guardar solo compara los que cambiaron desde el último.
// Por celdas: al abrir solo se baja el índice (qué trozos hay y su `rev`) con los assets, zonas y teleportes; las celdas se bajan al
// acercarse la cámara (`cerca`) o cuando alguien las pide (`asegurar`, el pincel) y las lejanas sin cambios se sueltan. El `MundoDoc` solo lleva las celdas bajadas; una celda que el servidor tiene y no se ha bajado (`porBajar`) no la
// toca ningún cambio: `commit` deshace el que lo intente.
import * as api from "./api";
import { Conflicto, type Indice, type Sesion, type TrozoBorrador, type Version } from "./api";
import { CELDA, type MundoDoc, type Objeto, type Terreno } from "./formato";
import {
  aTrozos,
  claveDeNumero,
  coordsDeClave,
  deTrozos,
  extraCelda,
  mismoTerreno,
  numeroCelda,
  numeroDeClave,
  parchear,
  partirTerreno,
  trozoCelda,
  trozosSueltos,
} from "./trozos";

const AUTOGUARDADO_MS = 20_000;
export const MAX_HISTORIAL = 50;
/** Bytes de un guardado como mucho; el servidor acepta 8 MB por petición (`MAX_EDITOR_BYTES`). Cada tanda va todo o nada; un guardado grande va en varias. */
const MAX_TANDA = 4 * 1024 * 1024;
/** Trozos de un guardado como mucho (`MAX_SAVE_PIECES` del servidor). */
const MAX_TROZOS = 5000;
/** Se bajan las celdas a menos de esto de la cámara y se sueltan las que pasan de `SOLTAR` (lo mismo que el juego, `cercania.ts`). */
const RADIO = 480;
const SOLTAR = 540;

/** Lo que baja el editor al abrir o recargar: el índice, sin ninguna celda. */
export async function inicio(s: Sesion): Promise<Indice> {
  const i = await api.indice(s);
  if (!i.borrados) throw new Error("El editor está desfasado: recarga la página.");
  return i;
}

/** Distancia del punto (x, z) a la celda (cx, cz), 0 si está dentro. */
function aCelda(x: number, z: number, cx: number, cz: number): number {
  const dx = Math.max(cx * CELDA - x, 0, x - (cx + 1) * CELDA);
  const dz = Math.max(cz * CELDA - z, 0, z - (cz + 1) * CELDA);
  return Math.hypot(dx, dz);
}

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
  /** Han llegado celdas del servidor: sus objetos van al final de `doc.objetos`, así que los índices de antes siguen valiendo. */
  bajadas?(): void;
}

export class Borrador {
  doc: MundoDoc;
  version: number;
  sucio = false;
  guardando = false;
  conflicto = false;
  /** Lo que pesa lo bajado del borrador (los trozos de `actual` en JSON): solo un dato para la barra, el mundo no tiene tope. */
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
  /** Celdas que el servidor tiene y no se han bajado: ningún cambio las puede tocar. Las que no tiene cuentan como bajadas y vacías. */
  private porBajar = new Set<string>();
  /** Las que se están bajando, con la petición de su lote. */
  private pidiendo = new Map<string, Promise<void>>();
  /** Sube al recargar: lo que llegue de antes ya no vale. */
  private epoca = 0;
  /** La celda de la cámara en la última llamada a `cerca` (y si se podía soltar): sin cambios, no hay nada que mirar. */
  private foco = "";

  /** `indice`, lo de `inicio`. `semilla`: el mundo con el que empezar si el servidor no tiene nada; queda sucio para que se guarde. */
  constructor(
    private readonly sesion: Sesion,
    indice: Indice,
    private readonly g: Ganchos,
    semilla?: MundoDoc,
  ) {
    this.doc = this.cargar(indice, semilla);
    this.version = indice.version;
    this.sucio = semilla !== undefined;
  }

  /** Celdas bajadas con algo dentro y celdas del servidor sin bajar, para la barra. */
  cuentaCeldas(): { bajadas: number; sinBajar: number } {
    let bajadas = 0;
    for (const k of this.actual.keys()) if (k.startsWith("c/")) bajadas++;
    return { bajadas, sinBajar: this.porBajar.size };
  }

  /** Deja el borrador como lo tiene el servidor (o como la semilla) y devuelve el documento. */
  private cargar(indice: Indice, semilla?: MundoDoc): MundoDoc {
    this.epoca++;
    this.pidiendo = new Map();
    this.foco = "";
    const docs: Record<string, unknown> = { ...indice.docs };
    this.revs = new Map([...Object.entries(indice.trozos), ...Object.entries(indice.borrados)]);
    this.porBajar = new Set(semilla ? [] : Object.keys(indice.trozos).filter((k) => coordsDeClave(k)));
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
    const sinBajar = [...nuevos].filter(([k, v]) => this.porBajar.has(k) && v !== null).map(([k]) => k);
    if (sinBajar.length) return this.rechazar(nuevos, sinBajar);
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

  /**
   * El cambio llevaba algo a una celda que no se ha bajado: guardarla la pisaría con solo lo nuevo. Se deshace (cada trozo del
   * paso vuelve a como está en `actual`), se piden esas celdas y se avisa.
   */
  private rechazar(nuevos: Cambios, sinBajar: string[]): false {
    const docs: Record<string, unknown> = {};
    for (const k of nuevos.keys()) {
      const a = this.actual.get(k);
      docs[k] = a === undefined ? null : JSON.parse(a);
    }
    this.doc = parchear(this.doc, docs);
    this.indexar(this.doc);
    this.asegurar(sinBajar).catch((e: unknown) => this.fallar(e));
    const que = sinBajar.length === 1 ? `La celda ${sinBajar[0]} aún no se ha bajado` : `Las celdas ${sinBajar.slice(0, 3).join(", ")} aún no se han bajado`;
    this.g.fallo(new Error(`${que} del servidor y el cambio se ha deshecho para no pisarla. Ya se está bajando: vuelve a intentarlo en un momento.`));
    this.g.reemplazado();
    return false;
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
        if (!mismoTerreno(a, b)) celdas.add(numeroDeClave(k) as number);
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
        nuevos.set(k, trozoCelda(os, terreno?.get(k), doc.celdas?.[k]));
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

  // ---------------------------------------------------------------- celdas

  /**
   * La cámara del editor está en (x, z): pide las celdas a menos de `RADIO` que faltan, las más cercanas antes, y, con `soltar`
   * (sin nada elegido ni a medio hacer), suelta las que pasan de `SOLTAR` sin cambios. Devuelve si ha soltado alguna: entonces
   * `doc.objetos` ha cambiado y la vista se rehace. Solo mira algo cuando la cámara cambia de celda.
   */
  cerca(x: number, z: number, soltar: boolean): boolean {
    const cx = Math.floor(x / CELDA);
    const cz = Math.floor(z / CELDA);
    const foco = `${cx}/${cz}/${soltar}`;
    if (foco === this.foco) return false;
    this.foco = foco;
    const r = Math.ceil(RADIO / CELDA);
    const quiero: [number, string][] = [];
    for (let i = cx - r; i <= cx + r; i++) {
      for (let j = cz - r; j <= cz + r; j++) {
        const k = `c/${i}/${j}`;
        const d = aCelda(x, z, i, j);
        if (d <= RADIO && this.porBajar.has(k) && !this.pidiendo.has(k)) quiero.push([d, k]);
      }
    }
    if (quiero.length) {
      quiero.sort((a, b) => a[0] - b[0]);
      this.asegurar(quiero.map(([, k]) => k)).catch((e: unknown) => this.fallar(e));
    }
    return soltar && this.soltar((i, j) => aCelda(x, z, i, j) > SOLTAR);
  }

  /** ¿La celda está bajada (o el servidor no la tiene)? Solo entonces se puede cambiar. */
  bajada(clave: string): boolean {
    return !this.porBajar.has(clave);
  }

  /**
   * Baja esas celdas (las que el servidor tiene y faltan), en lotes de `MAX_LOTE`; se resuelve cuando están todas, y falla si
   * no se pudo. Es lo que usa quien vaya a cambiar una celda lejos de la cámara (el pincel): después, `bajada(clave)` es cierto.
   */
  async asegurar(claves: Iterable<string>): Promise<void> {
    const todas = [...new Set(claves)];
    const faltan = todas.filter((k) => this.porBajar.has(k) && !this.pidiendo.has(k));
    for (let i = 0; i < faltan.length; i += api.MAX_LOTE) {
      const lote = faltan.slice(i, i + api.MAX_LOTE);
      const p = this.bajarLote(lote);
      for (const k of lote) this.pidiendo.set(k, p);
    }
    await Promise.all(new Set(todas.map((k) => this.pidiendo.get(k)).filter((p) => p !== undefined)));
  }

  private async bajarLote(lote: string[]): Promise<void> {
    const epoca = this.epoca;
    let trozos: Record<string, TrozoBorrador>;
    try {
      trozos = (await api.trozos(this.sesion, lote)).trozos;
    } finally {
      if (epoca === this.epoca) for (const k of lote) this.pidiendo.delete(k);
    }
    if (epoca !== this.epoca) return;
    this.incorporar(lote.filter((k) => this.porBajar.has(k)), trozos);
  }

  /**
   * Mete en el documento unas celdas recién bajadas: sus objetos van al final de `doc.objetos` (sin tocar los demás, ni uno que
   * se esté arrastrando hacia ellas), su terreno a `doc.terreno` y lo que traen pasa a ser lo guardado.
   */
  private incorporar(claves: string[], trozos: Record<string, TrozoBorrador>): void {
    if (!claves.length) return;
    const objetos = [...this.doc.objetos];
    const extras = { ...this.doc.celdas };
    const terreno = { ...this.doc.terreno };
    // Lo que `tocados` tiene por terreno de `actual`: con las celdas nuevas, que llegan sin cambios.
    const visto = new Map(this.terreno ?? []);
    for (const k of claves) {
      const t = trozos[k];
      this.porBajar.delete(k);
      this.revs.set(k, t?.rev ?? 0);
      const c = t?.doc as { objetos: Objeto[]; terreno?: Terreno } | null | undefined;
      if (!c) continue;
      const n = numeroDeClave(k) as number;
      for (const o of c.objetos) {
        objetos.push(o);
        this.celdas.set(o, n);
      }
      const extra = extraCelda(c);
      if (extra) extras[k] = extra;
      if (c.terreno) {
        terreno[k] = c.terreno;
        visto.set(k, c.terreno);
      }
      const v = trozoCelda(c.objetos, c.terreno, extra);
      if (v === null) continue;
      this.poner(k, v);
      this.guardado.set(k, v);
    }
    this.doc = { ...this.doc, objetos };
    if (Object.keys(extras).length) this.doc.celdas = extras;
    if (Object.keys(terreno).length) this.doc.terreno = terreno;
    this.terreno = visto.size ? visto : undefined;
    this.g.bajadas?.();
    this.g.estado();
  }

  /** Suelta las celdas lejanas que no tienen nada sin guardar ni están en el historial (deshacerlas necesitaría tenerlas). */
  private soltar(lejos: (cx: number, cz: number) => boolean): boolean {
    const enHistorial = new Set<string>();
    for (const p of this.hist) for (const k of p.antes.keys()) enHistorial.add(k);
    const fuera = new Set<number>();
    for (const [k, v] of this.actual) {
      const c = coordsDeClave(k);
      if (!c || !lejos(...c)) continue;
      if (this.pendientes.has(k) || enHistorial.has(k) || this.guardado.get(k) !== v) continue;
      fuera.add(numeroDeClave(k) as number);
    }
    if (!fuera.size) return false;
    const objetos = this.doc.objetos.filter((o) => {
      if (!fuera.has(numeroCelda(o.pos[0], o.pos[2]))) return true;
      this.celdas.delete(o);
      return false;
    });
    const extras = { ...this.doc.celdas };
    const terreno = { ...this.doc.terreno };
    const visto = new Map(this.terreno ?? []);
    for (const n of fuera) {
      const k = claveDeNumero(n);
      this.poner(k, null);
      this.guardado.delete(k);
      this.porBajar.add(k);
      delete extras[k];
      delete terreno[k];
      visto.delete(k);
    }
    this.doc = { ...this.doc, objetos, celdas: extras, terreno };
    if (!Object.keys(extras).length) delete this.doc.celdas;
    if (!Object.keys(terreno).length) delete this.doc.terreno;
    this.terreno = visto.size ? visto : undefined;
    this.g.estado();
    return true;
  }

  /** Las celdas que usan un asset y no se han bajado (las bajadas las mira quien pregunta, en `doc`). */
  async usosSinBajar(asset: string): Promise<string[]> {
    return (await api.usos(this.sesion, asset)).celdas.filter((k) => this.porBajar.has(k));
  }

  async recargar(): Promise<void> {
    try {
      const indice = await inicio(this.sesion);
      this.doc = this.cargar(indice);
      this.version = indice.version;
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
