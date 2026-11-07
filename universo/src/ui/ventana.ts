// Ventana con pestañas del sistema de interfaz (`ui/ui.css`, con sus líneas de diseño): la base de los paneles del Jovaniverso.
// Mientras está abierta se queda con el teclado (WASD, espacio y los números no mueven al robot ni hacen gestos), salvo Esc,
// que la cierra, y las flechas izquierda y derecha, que cambian de pestaña.
import "./ui.css";
import { h } from "../editor/dom";

export interface Pestana {
  clave: string;
  titulo: string;
  /** Llena el cuerpo cada vez que se elige la pestaña. */
  pintar(cuerpo: HTMLElement): void;
}

export class Ventana {
  private readonly velo: HTMLElement;
  private readonly cuerpo = h("div", { className: "jv-cuerpo" });
  private readonly botones = new Map<string, HTMLButtonElement>();
  private actual: string;

  constructor(
    capa: HTMLElement,
    titulo: string,
    private readonly pestanas: Pestana[],
    pie: string,
  ) {
    this.actual = pestanas[0].clave;
    const lista = h("div", { className: "jv-pestanas" });
    lista.setAttribute("role", "tablist");
    for (const p of pestanas) {
      const b = h("button", { className: "jv-pestana", type: "button", textContent: p.titulo, onclick: () => this.elegir(p.clave) });
      b.setAttribute("role", "tab");
      this.botones.set(p.clave, b);
      lista.append(b);
    }
    const cerrar = h("button", { className: "jv-pestana", type: "button", textContent: "✕", onclick: () => this.cerrar() });
    cerrar.setAttribute("aria-label", "Cerrar");
    lista.append(cerrar);
    const caja = h(
      "div",
      { className: "jv-ventana" },
      h("header", { className: "jv-cabecera" }, h("h2", { className: "jv-titulo", textContent: titulo }), lista),
      this.cuerpo,
      h("footer", { className: "jv-pie", textContent: pie }),
    );
    caja.setAttribute("role", "dialog");
    caja.setAttribute("aria-label", titulo);
    this.velo = h("div", { className: "jv-velo", hidden: true }, caja);
    // Tocar fuera de la ventana la cierra.
    this.velo.addEventListener("pointerdown", (e) => {
      if (e.target === this.velo) this.cerrar();
    });
    capa.append(this.velo);
    window.addEventListener("keydown", this.alTeclear, true);
  }

  get abierta(): boolean {
    return !this.velo.hidden;
  }

  abrir(clave = this.actual): void {
    if (document.pointerLockElement) document.exitPointerLock();
    this.velo.hidden = false;
    this.elegir(clave);
  }

  cerrar(): void {
    this.velo.hidden = true;
  }

  /** Repinta la pestaña elegida si es `clave` (cuando llegan sus datos). */
  repintar(clave: string): void {
    if (this.abierta && this.actual === clave) this.elegir(clave);
  }

  private elegir(clave: string): void {
    const p = this.pestanas.find((x) => x.clave === clave) ?? this.pestanas[0];
    this.actual = p.clave;
    for (const [k, b] of this.botones) b.setAttribute("aria-selected", String(k === p.clave));
    this.cuerpo.replaceChildren();
    p.pintar(this.cuerpo);
  }

  private alTeclear = (e: KeyboardEvent): void => {
    if (!this.abierta || e.code === "Tab") return; // el tabulador lo atiende quien abre la ventana
    if (e.key === "Escape") this.cerrar();
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const i = this.pestanas.findIndex((p) => p.clave === this.actual);
      const n = this.pestanas.length;
      this.elegir(this.pestanas[(i + (e.key === "ArrowLeft" ? n - 1 : 1)) % n].clave);
      e.preventDefault();
    }
    if (e.key !== "Escape") e.stopPropagation();
  };
}

/** Rótulo de sección con su línea hasta el borde. */
export const seccion = (texto: string): HTMLElement => h("h3", { className: "jv-seccion", textContent: texto });

/** Fila de una lista: icono, nombre y lo de la derecha (cifras y fichas). */
export function fila(icono: string, nombre: string, ...meta: (Node | string)[]): HTMLLIElement {
  return h(
    "li",
    { className: "jv-fila" },
    h("span", { className: "jv-icono", textContent: icono }),
    h("span", { className: "jv-nombre", textContent: nombre, title: nombre }),
    h("span", { className: "jv-meta" }, ...meta),
  );
}

export const ficha = (texto: string, tipo = ""): HTMLElement => h("span", { className: `jv-ficha ${tipo}`.trim(), textContent: texto });
export const cifra = (texto: string): HTMLElement => h("span", { className: "jv-cifra", textContent: texto });
export const vacio = (texto: string, error = false): HTMLElement => h("p", { className: error ? "jv-error" : "jv-vacio", textContent: texto });
