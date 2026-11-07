// El borrador del mundo que comparten los modos «Mundo» y «Assets»: el documento, su versión, el historial de deshacer
// (instantáneas en JSON, con tope), el guardado automático y las llamadas al servidor. No toca la pantalla: avisa por ganchos.
import * as api from "./api";
import { Conflicto, type Sesion, type Version } from "./api";
import type { MundoDoc } from "./formato";

const AUTOGUARDADO_MS = 20_000;
/** El mundo pesa unos 145 KB en JSON: 50 instantáneas son ~7 MB. */
export const MAX_HISTORIAL = 50;

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
  rev: number;
  version: number;
  sucio = false;
  guardando = false;
  conflicto = false;
  hist: string[];
  hi = 0;
  private cambios = 0;
  private timer = 0;

  constructor(
    private readonly sesion: Sesion,
    b: { doc: MundoDoc; rev: number; version: number },
    private readonly g: Ganchos,
  ) {
    this.doc = b.doc;
    this.rev = b.rev;
    this.version = b.version;
    this.hist = [JSON.stringify(b.doc)];
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
    const s = JSON.stringify(this.doc);
    if (s === this.hist[this.hi]) return false;
    this.hist.length = this.hi + 1;
    this.hist.push(s);
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
    this.hi = i;
    this.doc = JSON.parse(this.hist[i]) as MundoDoc;
    this.marcarSucio();
    this.g.reemplazado();
  }

  async guardar(): Promise<boolean> {
    if (this.guardando || this.conflicto) return false;
    this.guardando = true;
    this.g.estado();
    const n = this.cambios;
    try {
      this.rev = (await api.guardar(this.sesion, this.rev, this.doc)).rev;
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
      const b = await api.borrador(this.sesion);
      this.doc = b.doc;
      this.rev = b.rev;
      this.version = b.version;
      this.hist = [JSON.stringify(b.doc)];
      this.hi = 0;
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
    // Sin borrador guardado nunca no hay nada que publicar: se guarda aunque no haya cambios.
    if ((this.sucio || this.rev === 0) && !(await this.guardar())) return null;
    try {
      this.version = (await api.publicar(this.sesion, this.rev)).version;
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
      this.rev = (await api.volver(this.sesion, this.rev, version)).rev;
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
