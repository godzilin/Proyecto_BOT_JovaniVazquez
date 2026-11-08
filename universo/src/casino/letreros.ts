// Los letreros del mundo (objetos con la función `letrero`): cada uno pinta su panel en su propio lienzo (una llamada de dibujo) con
// su título y los botes que se le han puesto en el editor, que van rotando si son varios. Los botes los pide al servidor
// (`/api/botes`) cada `CADA_MS` mientras alguno está a la vista; el de la tragaperras llega además con cada tirada.
import { CanvasTexture, LinearFilter, Mesh, MeshBasicMaterial, PlaneGeometry, SRGBColorSpace } from "three";
import type { Contexto } from "../mundo";
import type { Sesion } from "../paseo";
import { type Bote, dibujarLetrero } from "./dibujo";
import type { Letrero } from "./sitio";

const DISTANCIA = 120; // m: más lejos no se dibujan
const CADA_MS = 30_000;
const ROTAR_MS = 5000;
const ANCHO_LIENZO = 1024;

interface Panel {
  datos: Letrero;
  malla: Mesh;
  g: CanvasRenderingContext2D;
  tex: CanvasTexture;
  w: number;
  h: number;
  /** Lo que tiene pintado, para no repintar si no cambia. */
  pintado: string;
}

export class Letreros {
  private paneles: Panel[] = [];
  private readonly botes = new Map<string, Bote>();
  private ultimaPeticion = 0;
  private pidiendo = false;

  constructor(
    private readonly ctx: Contexto,
    private readonly sesion: Sesion | null,
    iniciales: Bote[],
  ) {
    for (const b of iniciales) this.botes.set(b.clave, b);
    this.ultimaPeticion = performance.now();
  }

  /** Sustituye los letreros por los de `lista`. */
  colocar(lista: Letrero[]): void {
    for (const p of this.paneles) {
      this.ctx.escena.remove(p.malla);
      this.ctx.culling.quitar(p.malla);
      p.malla.geometry.dispose();
      p.tex.dispose();
      (p.malla.material as MeshBasicMaterial).dispose();
    }
    this.paneles = lista.map((d) => {
      const w = ANCHO_LIENZO;
      const h = Math.round(Math.min(Math.max((ANCHO_LIENZO * d.alto) / d.ancho, 48), 1024));
      const lienzo = document.createElement("canvas");
      lienzo.width = w;
      lienzo.height = h;
      const g = lienzo.getContext("2d")!;
      const tex = new CanvasTexture(lienzo);
      tex.colorSpace = SRGBColorSpace;
      tex.minFilter = LinearFilter;
      tex.generateMipmaps = false;
      tex.anisotropy = 4;
      const malla = new Mesh(new PlaneGeometry(d.ancho, d.alto), new MeshBasicMaterial({ map: tex, toneMapped: false }));
      d.matriz.decompose(malla.position, malla.quaternion, malla.scale);
      malla.updateMatrixWorld();
      this.ctx.escena.add(malla);
      this.ctx.culling.registrarMalla(malla, DISTANCIA);
      return { datos: d, malla, g, tex, w, h, pintado: "" };
    });
    this.tick(performance.now());
  }

  /** Un bote que ha cambiado sin preguntar (la tragaperras, tras una tirada). */
  poner(clave: string, texto: string): void {
    const b = this.botes.get(clave);
    if (b) b.texto = texto;
  }

  tick(ahora: number): void {
    if (!this.paneles.length) return;
    let alguno = false;
    for (const p of this.paneles) {
      if (!this.ctx.culling.visible(p.malla)) continue;
      alguno = true;
      const claves = p.datos.botes;
      const clave = claves.length ? claves[Math.floor(ahora / ROTAR_MS) % claves.length] : null;
      const bote = clave === null ? null : (this.botes.get(clave) ?? null);
      const firma = `${p.datos.titulo}|${clave}|${bote?.texto}`;
      if (firma === p.pintado) continue;
      p.pintado = firma;
      dibujarLetrero(p.g, p.w, p.h, p.datos.titulo, bote, clave !== null);
      p.tex.needsUpdate = true;
    }
    if (alguno && !document.hidden && !this.pidiendo && ahora - this.ultimaPeticion > CADA_MS) void this.pedir(ahora);
  }

  private async pedir(ahora: number): Promise<void> {
    this.pidiendo = true;
    this.ultimaPeticion = ahora;
    try {
      const q = this.sesion?.guildId ? `?guild_id=${encodeURIComponent(this.sesion.guildId)}` : "";
      const r = await fetch(`/api/botes${q}`, { headers: { Authorization: `Bearer ${this.sesion?.session ?? "dev"}` } });
      if (!r.ok) return;
      const datos = (await r.json()) as { botes: Bote[] };
      for (const b of datos.botes) this.botes.set(b.clave, b);
    } catch {
      // Sin red: se sigue enseñando lo último que se supo.
    } finally {
      this.pidiendo = false;
    }
  }

  /** Para las pruebas (`window.__jv.casino.letreros`). */
  estado(): { titulo: string; botes: string[]; pintado: string }[] {
    return this.paneles.map((p) => ({ titulo: p.datos.titulo, botes: p.datos.botes, pintado: p.pintado }));
  }
}
