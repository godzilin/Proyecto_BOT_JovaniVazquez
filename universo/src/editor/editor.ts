// Editor del Jovaniverso (solo con permiso de editor, solo ordenador): una pantalla con dos modos, «Mundo» (colocar y mover objetos y zonas
// dentro de la isla) y «Assets» (montar los modelos), que comparten un solo borrador, un solo historial y la misma barra de
// guardar, publicar y versiones.
import * as api from "./api";
import { Conflicto, type Sesion, type TrozoBorrador } from "./api";
import { Borrador } from "./borrador";
import { dialogo, h, modal } from "./dom";
import { comprobarFormas, type MundoDoc } from "./formato";
import type { Anfitrion, Panel } from "./panel";
import { AYUDA_ASSETS, PanelAssets } from "./assets";
import { AYUDA_MUNDO, PanelMundo } from "./niveles";
import { AYUDA_TERRENO } from "./pincel";
import { mundoDeSerie } from "../mundo/serie";
import type { Mundo } from "../mundo";

/** Lo que el editor necesita de la página: el mundo (pausarlo y pintar en su escena) y volver a lo publicado al cerrar. */
export interface Entorno {
  mundo: Mundo;
  /** Deja en el mundo lo publicado (o el de serie si no hay nada). */
  recargar(): Promise<void>;
}

type Pestana = "mundo" | "assets";

/** Lo que vale en las dos pestañas. */
const AYUDA_GENERAL: [string, string][] = [
  ["F1 o ?", "Abre esta ayuda"],
  ["Ctrl+S", "Guarda el borrador (también se guarda solo cada 20 segundos)"],
  ["Ctrl+Z", "Deshace el último cambio"],
  ["Ctrl+Y o Ctrl+Mayús+Z", "Rehace lo deshecho"],
  ["Publicar", "Todos los jugadores pasan a ver el borrador; hasta entonces, nadie más lo ve"],
  ["Versiones", "Lista lo publicado y deja volver el borrador a una versión anterior"],
  ["Cerrar", "Sale del editor; el mundo vuelve a lo publicado"],
];

let abierto = false;

/**
 * Abre el editor a pantalla completa. Se resuelve cuando el borrador está cargado; el editor sigue
 * abierto hasta que quien edita lo cierra. Si no se puede cargar, lanza el error del servidor.
 */
export async function abrirEditor(sesion: Sesion, entorno: Entorno): Promise<void> {
  if (abierto) return;
  abierto = true;
  try {
    if (import.meta.env.DEV) comprobarFormas();
    const b = await api.borrador(sesion);
    if (!b.trozos) throw new Error("El editor está desfasado: recarga la página.");
    // Sin borrador ni nada publicado, se empieza desde el mundo de serie y no desde un lienzo vacío (y queda sucio para que se guarde).
    const vacio = b.version === 0 && Object.values(b.trozos).every((t) => !t.doc);
    new Editor(sesion, entorno, b, vacio ? mundoDeSerie() : undefined).iniciar();
  } catch (e) {
    abierto = false;
    throw e;
  }
}

class Editor implements Anfitrion {
  readonly raiz = h("div", { className: "ed" });
  readonly b: Borrador;
  private readonly paneles: Partial<Record<Pestana, Panel>> = {};
  private activa: Pestana = "mundo";
  private readonly cartel = h("div", { className: "ed-aviso", hidden: true });
  private readonly barra = h("footer", { className: "ed-estado" });
  private readonly botones = new Map<string, HTMLButtonElement>();
  private readonly pestanas = new Map<Pestana, HTMLButtonElement>();

  constructor(
    readonly sesion: Sesion,
    private readonly entorno: Entorno,
    b: { version: number; tope?: number; trozos: Record<string, TrozoBorrador> },
    semilla?: MundoDoc,
  ) {
    this.b = new Borrador(sesion, b, {
      fallo: (e) => this.fallo(e),
      estado: () => this.estado(),
      reemplazado: () => {
        this.panel(this.activa).alCambiarDoc();
        this.estado();
      },
    }, semilla);
    this.raiz.append(this.arriba(), this.barra, this.cartel);
    // Las teclas escritas en un campo no llegan al mundo, que escucha en window.
    this.raiz.addEventListener("keydown", (e) => e.stopPropagation());
    this.raiz.addEventListener("keyup", (e) => e.stopPropagation());
    document.body.append(this.raiz);
  }

  iniciar(): void {
    this.entorno.mundo.pausar(true);
    window.addEventListener("keydown", this.alTecla, true);
    window.addEventListener("keyup", this.alSoltarTecla, true);
    this.b.iniciar();
    this.ir("mundo");
  }

  // ---------------------------------------------------------------- pantalla

  private boton(clave: string, texto: string, fn: () => void, titulo = ""): HTMLButtonElement {
    const b = h("button", { className: "ed-b", textContent: texto, title: titulo, onclick: fn });
    this.botones.set(clave, b);
    return b;
  }

  private arriba(): HTMLElement {
    const pestana = (p: Pestana, texto: string): HTMLButtonElement => {
      const b = h("button", { className: "ed-b ed-pestana", textContent: texto, onclick: () => this.ir(p) });
      this.pestanas.set(p, b);
      return b;
    };
    return h(
      "header",
      { className: "ed-arriba" },
      h("h1", { textContent: "Editor" }),
      pestana("mundo", "Mundo"),
      pestana("assets", "Assets"),
      h("span", { className: "ed-hueco" }),
      this.boton("guardar", "Guardar borrador", () => void this.guardar(), "Ctrl+S"),
      this.boton("publicar", "Publicar", () => void this.publicar()),
      this.boton("versiones", "Versiones", () => void this.verVersiones()),
      this.boton("ayuda", "Ayuda", () => this.ayuda(), "F1"),
      this.boton("cerrar", "Cerrar", () => void this.cerrar()),
    );
  }

  /** El panel de un modo; se crea la primera vez que se entra en él. */
  private panel(p: Pestana): Panel {
    let x = this.paneles[p];
    if (!x) {
      x = this.paneles[p] = p === "mundo" ? new PanelMundo(this, this.entorno.mundo) : new PanelAssets(this);
      for (const e of x.elementos) this.raiz.insertBefore(e, this.barra);
    }
    return x;
  }

  /** Cambia de pestaña: enseña las columnas de ese modo y esconde las del otro. */
  private ir(p: Pestana): void {
    const antes = this.paneles[this.activa];
    if (antes && this.activa !== p) {
      antes.desactivar();
      antes.elementos.forEach((e) => (e.hidden = true));
    }
    this.activa = p;
    const x = this.panel(p);
    x.elementos.forEach((e) => (e.hidden = false));
    for (const [k, b] of this.pestanas) b.setAttribute("aria-pressed", String(k === p));
    x.activar();
    this.estado();
  }

  editarAsset(id: string): void {
    this.ir("assets");
    (this.paneles.assets as PanelAssets).elegir(id);
  }

  aviso(texto: string, accion?: { texto: string; fn: () => void }, error = true): void {
    this.cartel.replaceChildren(h("span", { textContent: texto }));
    if (accion) this.cartel.append(h("button", { className: "ed-b", textContent: accion.texto, onclick: accion.fn }));
    this.cartel.append(h("button", { className: "ed-b", textContent: "×", title: "Cerrar aviso", onclick: () => (this.cartel.hidden = true) }));
    this.cartel.setAttribute("role", error ? "alert" : "status");
    this.cartel.classList.toggle("ed-ok", !error);
    this.cartel.hidden = false;
  }

  fallo(e: unknown): void {
    const texto = e instanceof Error ? e.message : "Error desconocido.";
    if (e instanceof Conflicto) {
      this.aviso(`${texto} Al recargar se pierden los cambios sin guardar de esta pantalla.`, { texto: "Recargar", fn: () => void this.recargar() });
    } else this.aviso(texto);
  }

  estado(): void {
    const b = this.b;
    const partes = [...(this.paneles[this.activa]?.estado() ?? [])];
    partes.push(b.guardando ? "Guardando…" : b.sucio ? "Sin guardar" : "Guardado");
    partes.push(b.version ? `Publicada: versión ${b.version}` : "Sin publicar");
    if (b.tope) partes.push(`${b.bytes > b.tope ? "⚠ " : ""}Mundo: ${mb(b.bytes)} de ${mb(b.tope)} MB`);
    this.barra.replaceChildren(...partes.map((t) => h("span", { textContent: t, className: t.startsWith("⚠") ? "ed-alerta" : "" })));
    this.botones.get("guardar")!.disabled = b.guardando;
  }

  // ---------------------------------------------------------------- servidor

  private async guardar(): Promise<boolean> {
    const ok = await this.b.guardar();
    if (ok) this.cartel.hidden = true;
    return ok;
  }

  private async recargar(): Promise<void> {
    await this.b.recargar();
    this.cartel.hidden = true;
  }

  private async publicar(): Promise<void> {
    if (this.b.conflicto) return;
    const ok = await dialogo(this.raiz, "Publicar el mundo", "Todos los jugadores verán este mundo en cuanto lo publiques. ¿Publicar?", "Publicar");
    if (ok === null) return;
    const version = await this.b.publicar();
    // El mundo de la pantalla sigue siendo el borrador: lo publicado se carga al cerrar.
    if (version !== null) this.aviso(`Mundo publicado: versión ${version}.`, undefined, false);
  }

  private async verVersiones(): Promise<void> {
    const lista = await this.b.versiones();
    if (!lista) return;
    const { fondo, caja } = modal(this.raiz, "Versiones publicadas");
    const filas = lista.map((v) => {
      const fecha = new Date(v.fecha);
      const cuando = Number.isNaN(fecha.getTime()) ? v.fecha : fecha.toLocaleString("es-ES");
      return h(
        "div",
        { className: "ed-fila ed-version" },
        h("span", { textContent: `v${v.version} · ${v.autor} · ${cuando} · ${v.kb} KB` }),
        h("button", {
          className: "ed-b",
          textContent: "Volver a esta",
          onclick: () => {
            fondo.remove();
            void this.volverA(v.version);
          },
        }),
      );
    });
    caja.append(
      ...(filas.length ? filas : [h("p", { textContent: "Todavía no se ha publicado nada." })]),
      h("div", { className: "ed-fila" }, h("button", { className: "ed-b", textContent: "Cerrar", onclick: () => fondo.remove() })),
    );
    caja.addEventListener("keydown", (e) => e.key === "Escape" && fondo.remove());
  }

  /** Los atajos y el ratón de cada pestaña, la que está abierta primero. */
  private ayuda(): void {
    const { fondo, caja } = modal(this.raiz, "Ayuda del editor");
    caja.classList.add("ed-caja-ancha");
    const seccion = (titulo: string, filas: [string, string][]): HTMLElement =>
      h(
        "section",
        {},
        h("h3", { textContent: titulo }),
        h("dl", { className: "ed-ayuda" }, ...filas.flatMap(([tecla, que]) => [h("dt", {}, h("kbd", { textContent: tecla })), h("dd", { textContent: que })])),
      );
    const mundo = [seccion("Mundo · Objetos", AYUDA_MUNDO), seccion("Mundo · Terreno", AYUDA_TERRENO)];
    const assets = [seccion("Assets", AYUDA_ASSETS)];
    const cerrar = h("button", { className: "ed-b", textContent: "Cerrar", onclick: () => fondo.remove() });
    caja.append(
      ...(this.activa === "mundo" ? [...mundo, ...assets] : [...assets, ...mundo]),
      seccion("En las dos pestañas", AYUDA_GENERAL),
      h("div", { className: "ed-fila" }, cerrar),
    );
    caja.addEventListener("keydown", (e) => e.key === "Escape" && fondo.remove());
    cerrar.focus({ preventScroll: true });
  }

  private async volverA(version: number): Promise<void> {
    const ok = await dialogo(this.raiz, "Volver a una versión", `El borrador pasará a ser la versión ${version} y se perderán los cambios sin publicar. No se publica nada hasta que pulses Publicar.`, "Volver a esta");
    if (ok === null) return;
    await this.b.volverA(version);
  }

  private async cerrar(): Promise<void> {
    if (this.b.sucio) {
      const ok = await dialogo(this.raiz, "Cambios sin guardar", "Hay cambios sin guardar y se perderán si cierras.", "Cerrar sin guardar");
      if (ok === null) return;
    }
    this.b.parar();
    window.removeEventListener("keydown", this.alTecla, true);
    window.removeEventListener("keyup", this.alSoltarTecla, true);
    for (const p of Object.values(this.paneles)) p.dispose();
    this.raiz.remove();
    // Lo que se ve vuelve a lo publicado antes de reanudar el mundo: el borrador no se queda a la vista.
    await this.entorno.recargar();
    this.entorno.mundo.pausar(false);
    window.dispatchEvent(new Event("resize")); // el mundo recupera el tamaño de la ventana
    abierto = false;
  }

  // ---------------------------------------------------------------- teclado

  private esCampoDeTexto(t: EventTarget | null): boolean {
    if (t instanceof HTMLInputElement) return ["text", "number", "search"].includes(t.type);
    return t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement;
  }

  private readonly alTecla = (e: KeyboardEvent): void => {
    if (this.raiz.querySelector(".ed-fondo")) return;
    const ctrl = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    if (k === "f1") {
      e.preventDefault(); // la ayuda del navegador
      e.stopPropagation();
      this.ayuda();
      return;
    }
    if (ctrl && k === "s") {
      e.preventDefault();
      (document.activeElement as HTMLElement | null)?.blur(); // el valor a medio escribir entra antes de guardar
      void this.guardar();
      return;
    }
    if (this.esCampoDeTexto(e.target)) return;
    e.stopPropagation(); // el mundo no se entera
    if (k === "?") this.ayuda();
    else if (ctrl && k === "z") this.b.irA(this.b.hi + (e.shiftKey ? 1 : -1));
    else if (ctrl && k === "y") this.b.irA(this.b.hi + 1);
    else if (!this.panel(this.activa).tecla(e, k, ctrl)) return;
    e.preventDefault();
  };

  private readonly alSoltarTecla = (e: KeyboardEvent): void => {
    if (!this.esCampoDeTexto(e.target)) e.stopPropagation();
  };
}

/** Bytes en MB con un decimal y coma, como los avisos del servidor. */
function mb(bytes: number): string {
  return (bytes / 1024 / 1024).toFixed(1).replace(".", ",");
}
