// Modo «Assets» del editor del Jovaniverso (solo con permiso de editor, solo ordenador): monta y edita assets de piezas o importa GLB.
// Se pueden elegir varias piezas (clic, Ctrl+clic, Mayús+clic en la lista, Mayús+arrastrar en la vista) y todo actúa sobre lo elegido.
// El borrador, guardar, publicar y deshacer son del editor entero (`borrador.ts`, `editor.ts`).
import * as api from "./api";
import { dialogo, h, modal } from "./dom";
import { type Analisis, analizar, optimizar } from "./optimizador";
import {
  type Asset,
  cargarGlb,
  comprobarFormas,
  FORMAS,
  FORMATO,
  type Forma,
  ID,
  MAX_ASSETS,
  MAX_CIRCULOS,
  MAX_DISTANCIA,
  MAX_LADOS,
  MAX_NOMBRE,
  MAX_PIEZAS,
  MAX_TRIANGULOS_GLB,
  MIN_DISTANCIA,
  MIN_LADOS,
  type MundoDoc,
  type Pieza,
  triangulosMalla,
  triangulosModelo,
  triangulosPieza,
  type Vec3,
} from "./formato";
import { Seleccion } from "./grupo";
import type { Anfitrion, Panel } from "./panel";
import { type Modo, Vista } from "./vista";

const GRADOS = Math.PI / 180;
const MAX_COLISION = 40;
const MAX_DESPLAZAMIENTO = 60;
/** Lo más que pesa un GLB subido (`MAX_GLB_BYTES` del servidor). */
const MAX_BYTES_GLB = 1024 * 1024;
const mb = (bytes: number) => (bytes / MAX_BYTES_GLB).toLocaleString("es-ES", { maximumFractionDigits: 1 });
const MODOS: [Modo, string, string][] = [
  ["translate", "Mover (W)", "w"],
  ["rotate", "Girar (E)", "e"],
  ["scale", "Escalar (R)", "r"],
];

/** Atajos y ratón de esta pestaña, para el menú de ayuda del editor. */
export const AYUDA_ASSETS: [string, string][] = [
  ["Clic en una pieza", "Elige solo esa pieza (en la vista o en la lista)"],
  ["Ctrl+clic", "Añade o quita una pieza de la selección"],
  ["Mayús+clic en la lista", "Elige todas las piezas entre la última elegida y la pulsada"],
  ["Mayús+arrastrar en la vista", "Dibuja una caja y elige las piezas cuyo centro queda dentro (con Ctrl, se suman a las elegidas)"],
  ["Clic en el vacío", "Deja de elegir ninguna pieza"],
  ["Arrastrar con Ctrl", "Invierte el ajuste a rejilla mientras dura el arrastre"],
  ["W / E / R", "Mover, girar o escalar con el gizmo"],
  ["X", "Cambia el gizmo entre ejes locales y globales"],
  ["Ctrl+D", "Duplica las piezas elegidas, 0,5 m a un lado"],
  ["Ctrl+C / Ctrl+X / Ctrl+V", "Copia, corta y pega piezas (también entre assets)"],
  ["Supr o Retroceso", "Borra las piezas elegidas"],
  ["Ctrl+A", "Elige todas las piezas visibles"],
  ["Escape", "Deja de elegir ninguna pieza"],
  ["F", "Encuadra lo elegido o, si no hay nada, todo el asset"],
  ["H", "Oculta las piezas elegidas (solo en el editor, no se guarda)"],
  ["Alt+H", "Enseña todas las piezas ocultas"],
  ["Flechas", "Mueven lo elegido 0,1 m en x y z (con Mayús, 1 m)"],
  ["Re Pág / Av Pág", "Suben o bajan lo elegido 0,1 m (con Mayús, 1 m)"],
  ["Ctrl+Z / Ctrl+Y", "Deshacer y rehacer"],
];

/** Portapapeles de piezas, en memoria: vale entre assets mientras el editor siga abierto. */
let portapapeles: Pieza[] = [];

function numero(n: number): string {
  return String(Math.round(n * 1000) / 1000);
}

function redondear(v: Vec3): Vec3 {
  return v.map((x) => Math.round(x * 1e4) / 1e4) as Vec3;
}

/** Id válido (`ID`) sacado del nombre y que no se repite. */
function sacarId(nombre: string, existentes: Record<string, unknown>): string {
  const base =
    nombre
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, MAX_NOMBRE - 4) || "asset";
  let id = base;
  for (let n = 2; id in existentes; n++) id = `${base}-${n}`;
  return id;
}

export class PanelAssets implements Panel {
  private selId: string | null = null;
  private readonly sel = new Seleccion();
  private readonly ocultas = new Set<Pieza>();
  private local = false;
  private modo: Modo = "translate";
  private glbMostrado: string | null = null;
  private enfocarAlCargar = false;
  private readonly triangulosGlb = new Map<string, number>();
  readonly elementos: HTMLElement[];
  private readonly vista: Vista;
  private readonly contenedor = h("div", { className: "ed-vista" });
  private readonly listaAssets = h("div", { className: "ed-lista" });
  private readonly listaPiezas = h("div", { className: "ed-lista" });
  private readonly filtro = h("input", { type: "search", placeholder: "Filtrar assets", className: "ed-filtro" });
  private readonly botones = new Map<string, HTMLButtonElement>();
  private readonly botonesModo = new Map<Modo, HTMLButtonElement>();
  private readonly campos: { campo: "pos" | "rot" | "esc"; i: number; input: HTMLInputElement }[] = [];
  private readonly forma = h("select");
  private readonly color = h("input", { type: "color" });
  private readonly listaColision = h("div", { className: "ed-lista" });
  private readonly distancia = h("input", { type: "number", min: String(MIN_DISTANCIA), max: String(MAX_DISTANCIA), step: "10", placeholder: "de serie" });
  private readonly propsCaja = h("div", { className: "ed-props" });
  private readonly propsPrisma = h("div", { className: "ed-props" });
  private readonly camposPrisma: { campo: "lados" | "arriba" | "abajo" | "fase"; input: HTMLInputElement }[] = [];
  private readonly archivo = h("input", { type: "file", accept: ".glb", hidden: true });
  private mensaje = "";

  constructor(private readonly a: Anfitrion) {
    this.selId = Object.keys(this.doc.assets)[0] ?? null;
    this.elementos = [this.izquierda(), this.centro(), this.derecha()];
    this.vista = new Vista(this.contenedor);
    this.vista.alElegir = (i, aditivo) => (i >= 0 ? this.clicPieza(i, aditivo ? "alternar" : "solo") : aditivo ? undefined : this.clicPieza(-1, "solo"));
    this.vista.alCaja = (indices, aditivo) => {
      this.sel.poner(aditivo ? [...this.sel.lista.filter((i) => !indices.includes(i)), ...indices] : indices);
      this.dibujar();
    };
    this.vista.alTransformar = (fin) => this.arrastrar(fin);
    this.filtro.addEventListener("input", () => this.pintarAssets());
    this.archivo.addEventListener("change", () => void this.importar());
    this.dibujar();
    this.enfocarAlCargar = true;
    this.vista.enfocar([]);
  }

  private get doc(): MundoDoc {
    return this.a.b.doc;
  }

  activar(): void {
    this.alCambiarDoc();
    this.vista.pedir();
  }

  desactivar(): void {}

  /** El documento ha cambiado desde fuera (deshacer, rehacer, recargar). */
  alCambiarDoc(): void {
    this.reajustarSeleccion();
    this.glbMostrado = null;
    this.dibujar();
  }

  /** Elige un asset desde otro modo (el «Editar este asset» del mundo). */
  elegir(id: string): void {
    if (this.doc.assets[id]) this.elegirAsset(id);
  }

  dispose(): void {
    this.vista.dispose();
  }

  // ---------------------------------------------------------------- construcción de la pantalla

  private boton(clave: string, texto: string, fn: () => void, titulo = ""): HTMLButtonElement {
    const b = h("button", { className: "ed-b", textContent: texto, title: titulo, onclick: fn });
    this.botones.set(clave, b);
    return b;
  }

  private izquierda(): HTMLElement {
    return h(
      "aside",
      { className: "ed-panel ed-izq ed-assets" },
      h("h2", { textContent: "Assets" }),
      this.filtro,
      h(
        "div",
        { className: "ed-fila" },
        this.boton("nuevo", "Nuevo", () => void this.nuevo()),
        this.boton("importar", "Importar GLB", () => this.archivo.click()),
        this.boton("duplicar", "Duplicar", () => void this.duplicarAsset()),
        this.boton("renombrar", "Renombrar", () => void this.renombrar()),
        this.boton("borrar", "Borrar", () => void this.borrarAsset()),
        this.boton("optimizar", "Optimizar polígonos", () => void this.optimizarAsset(), "Baja los triángulos del modelo GLB elegido"),
      ),
      this.listaAssets,
      this.archivo,
    );
  }

  private centro(): HTMLElement {
    const formas = FORMAS.map((f) => this.boton(`add-${f}`, f, () => this.anadir(f), `Añadir ${f}`));
    const modos = MODOS.map(([m, texto]) => {
      const b = h("button", { className: "ed-b", textContent: texto, title: texto, onclick: () => this.cambiarModo(m) });
      this.botonesModo.set(m, b);
      return b;
    });
    const ajuste = h("input", { type: "checkbox", onchange: () => this.vista.ajuste(ajuste.checked) });
    return h(
      "main",
      { className: "ed-centro ed-assets" },
      h(
        "div",
        { className: "ed-barra" },
        ...formas,
        h("span", { className: "ed-sep" }),
        this.boton("dup-pieza", "Duplicar", () => this.duplicarPieza(), "Duplica lo elegido (Ctrl+D)"),
        this.boton("espejo", "Espejo X", () => this.espejoX(), "Copias de lo elegido reflejadas en x"),
        this.boton("borrar-pieza", "Borrar", () => this.borrarPieza(), "Borra lo elegido (Supr o Retroceso)"),
        this.boton("copiar", "Copiar", () => this.copiar(), "Ctrl+C"),
        this.boton("cortar", "Cortar", () => this.cortar(), "Ctrl+X"),
        this.boton("pegar", "Pegar", () => this.pegar(), "Ctrl+V"),
        h("span", { className: "ed-sep" }),
        this.boton("alinear-x", "Alinear X", () => this.alinear(0), "Lleva la x de lo elegido a la de la pieza principal (la última elegida)"),
        this.boton("alinear-y", "Alinear Y", () => this.alinear(1), "Lleva la y de lo elegido a la de la pieza principal (la última elegida)"),
        this.boton("alinear-z", "Alinear Z", () => this.alinear(2), "Lleva la z de lo elegido a la de la pieza principal (la última elegida)"),
        h("span", { className: "ed-sep" }),
        this.boton("deshacer", "Deshacer", () => this.a.b.irA(this.a.b.hi - 1), "Ctrl+Z"),
        this.boton("rehacer", "Rehacer", () => this.a.b.irA(this.a.b.hi + 1), "Ctrl+Y"),
        this.boton("enfocar", "Enfocar", () => this.vista.enfocar(this.sel.lista), "Encuadra lo elegido (F)"),
        this.boton("espacio", "Ejes: globales", () => this.cambiarEspacio(), "Ejes del gizmo locales o globales (X)"),
        h("span", { className: "ed-sep" }),
        ...modos,
        h("label", { className: "ed-etiqueta", title: "0,1 m, 15° y 0,1 de escala" }, ajuste, " Ajuste"),
      ),
      this.contenedor,
    );
  }

  private derecha(): HTMLElement {
    for (const f of FORMAS) this.forma.append(h("option", { value: f, textContent: f }));
    this.forma.append(h("option", { value: "", textContent: "—", disabled: true }));
    this.forma.onchange = () =>
      this.forma.value &&
      this.editar((p) => {
        p.forma = this.forma.value as Forma;
        // Cada pieza guarda solo los campos de su forma: el servidor rechaza los de un prisma en otra.
        if (p.forma === "prisma") Object.assign(p, { lados: p.lados ?? 6, arriba: p.arriba ?? 1, abajo: p.abajo ?? 1, fase: p.fase ?? 0 });
        else for (const k of ["lados", "arriba", "abajo", "fase"] as const) delete p[k];
      });
    this.color.oninput = () => this.editar((p) => (p.color = parseInt(this.color.value.slice(1), 16)), false);
    this.color.onchange = () => this.commit();

    const fila = (titulo: string, campo: "pos" | "rot" | "esc", paso: string): HTMLElement => {
      const inputs = [0, 1, 2].map((i) => {
        const input = h("input", { type: "number", step: paso, placeholder: "—" });
        input.onchange = () => this.editarCampo(campo, i, input);
        this.campos.push({ campo, i, input });
        return input;
      });
      return h("label", { className: "ed-vec" }, h("span", { textContent: titulo }), ...inputs);
    };
    this.propsCaja.append(
      h("label", { className: "ed-vec" }, h("span", { textContent: "Forma" }), this.forma),
      fila("Posición", "pos", "0.1"),
      fila("Giro (°)", "rot", "15"),
      fila("Escala", "esc", "0.1"),
      h("label", { className: "ed-vec" }, h("span", { textContent: "Color" }), this.color),
    );

    const prisma: [string, "lados" | "arriba" | "abajo" | "fase", string][] = [
      ["Lados", "lados", "1"],
      ["Tapa de arriba", "arriba", "0.05"],
      ["Tapa de abajo", "abajo", "0.05"],
      ["Fase (°)", "fase", "5"],
    ];
    for (const [titulo, campo, paso] of prisma) {
      const input = h("input", { type: "number", step: paso, placeholder: "—" });
      input.onchange = () => this.editarPrisma(campo, input);
      this.camposPrisma.push({ campo, input });
      this.propsPrisma.append(h("label", { className: "ed-vec" }, h("span", { textContent: titulo }), input));
    }
    this.propsCaja.append(this.propsPrisma);

    this.distancia.onchange = () => {
      const a = this.asset;
      if (!a) return;
      const v = parseFloat(this.distancia.value);
      if (Number.isNaN(v)) delete a.distancia;
      else a.distancia = Math.min(Math.max(v, MIN_DISTANCIA), MAX_DISTANCIA);
      this.commit();
    };
    const colision = h(
      "div",
      { className: "ed-colision" },
      h("h2", { textContent: "Colisión" }),
      this.listaColision,
      h(
        "div",
        { className: "ed-fila" },
        this.boton("circulo", "Añadir círculo", () => this.anadirCirculo()),
        this.boton("ajustar", "Ajustar", () => this.ajustarColision(), "Deja un solo círculo, sacado de la caja envolvente en x y z"),
      ),
      h("label", { className: "ed-vec", title: "Metros hasta donde se dibuja; vacío, la de las construcciones" }, h("span", { textContent: "Distancia (m)" }), this.distancia),
    );
    return h("aside", { className: "ed-panel ed-der ed-assets" }, h("h2", { textContent: "Piezas" }), this.listaPiezas, this.propsCaja, colision);
  }

  // ---------------------------------------------------------------- datos

  private get asset(): Asset | null {
    return this.selId ? (this.doc.assets[this.selId] ?? null) : null;
  }

  private get piezas(): Pieza[] | null {
    const a = this.asset;
    return a?.tipo === "piezas" ? a.piezas : null;
  }

  /** La pieza principal (la última elegida). */
  private get pieza(): Pieza | null {
    return this.piezas?.[this.sel.principal] ?? null;
  }

  /** Todas las elegidas, de menor a mayor índice. */
  private elegidas(): Pieza[] {
    const piezas = this.piezas ?? [];
    return this.sel.lista.flatMap((i) => (piezas[i] ? [piezas[i]] : []));
  }

  /** El valor que comparten todas las elegidas, o `undefined` si difieren. */
  private comun<T>(f: (p: Pieza) => T): T | undefined {
    const ps = this.elegidas();
    return ps.length && ps.every((p) => f(p) === f(ps[0])) ? f(ps[0]) : undefined;
  }

  private triangulos(a: Asset): number | null {
    if (a.tipo === "glb") return this.triangulosGlb.get(a.glb) ?? null;
    return a.piezas.reduce((n, p) => n + triangulosPieza(p), a.malla ? triangulosMalla(a.malla) : 0);
  }

  private editarPrisma(campo: "lados" | "arriba" | "abajo" | "fase", input: HTMLInputElement): void {
    const v = parseFloat(input.value);
    const ps = this.elegidas();
    if (!ps.length || ps.some((p) => p.forma !== "prisma") || Number.isNaN(v)) return this.refrescar();
    for (const p of ps) {
      if (campo === "lados") p.lados = Math.min(Math.max(Math.round(v), MIN_LADOS), MAX_LADOS);
      else if (campo === "fase") p.fase = Math.min(Math.max(v, -720), 720) * GRADOS;
      else p[campo] = Math.min(Math.max(v, 0), 1);
      // El servidor pide alguna tapa con radio.
      if (!p.arriba && !p.abajo) p[campo === "abajo" ? "abajo" : "arriba"] = 0.05;
    }
    this.commit();
  }

  private redondear2(r: number, max: number): number {
    return Math.min(Math.max(Math.round(r * 100) / 100, -max), max);
  }

  /** Cierra un cambio: entra en el historial, marca el borrador como sucio y lo vuelve a dibujar. */
  private commit(): void {
    this.a.b.commit();
    this.dibujar();
  }

  /** Cambia todas las elegidas; con `cerrar` falso es un paso intermedio (no entra en el historial). */
  private editar(fn: (p: Pieza) => void, cerrar = true): void {
    const ps = this.elegidas();
    if (!ps.length) return;
    ps.forEach(fn);
    if (cerrar) this.commit();
    else {
      this.a.b.marcarSucio();
      this.dibujar();
    }
  }

  private editarCampo(campo: "pos" | "rot" | "esc", i: number, input: HTMLInputElement): void {
    const v = parseFloat(input.value);
    const ps = this.elegidas();
    if (!ps.length || Number.isNaN(v)) return this.refrescar();
    const [min, max, k] = campo === "pos" ? [-60, 60, 1] : campo === "esc" ? [0.02, 60, 1] : [-720, 720, GRADOS];
    for (const p of ps) p[campo][i] = Math.min(Math.max(v, min), max) * k;
    this.commit();
  }

  private arrastrar(fin: boolean): void {
    const piezas = this.piezas;
    if (!piezas) return;
    for (const i of this.sel.lista) {
      if (!piezas[i]) continue;
      const t = this.vista.transformacion(i);
      [piezas[i].pos, piezas[i].rot, piezas[i].esc] = [redondear(t.pos), redondear(t.rot), redondear(t.esc)];
    }
    if (fin) this.commit();
    else this.refrescar();
  }

  // ---------------------------------------------------------------- pintado

  /** Pone la vista y los paneles al día con el documento. */
  private dibujar(): void {
    const a = this.asset;
    if (!a) {
      this.glbMostrado = null;
      this.vista.limpiar();
      this.vista.colision(null);
    } else if (a.tipo === "piezas") {
      this.glbMostrado = null;
      this.vista.mostrarPiezas(a.piezas, this.sel.lista, this.sel.principal, a.piezas.map((p) => this.ocultas.has(p)), a.malla);
      this.vista.colision(a.colision);
    } else {
      if (this.glbMostrado !== a.glb) this.cargarModelo(a.glb);
      this.vista.colision(a.colision);
    }
    this.refrescar();
  }

  private cargarModelo(sha: string): void {
    this.glbMostrado = sha;
    this.vista.limpiar();
    cargarGlb(sha).then(
      (modelo) => {
        this.triangulosGlb.set(sha, triangulosModelo(modelo));
        const a = this.asset;
        if (this.glbMostrado !== sha || a?.tipo !== "glb" || a.glb !== sha) return;
        this.vista.mostrarModelo(modelo);
        if (this.enfocarAlCargar) this.vista.enfocar([]);
        this.enfocarAlCargar = false;
        this.refrescar();
      },
      () => {
        if (this.glbMostrado === sha) this.glbMostrado = null;
        this.avisar("No se ha podido cargar el modelo GLB.");
      },
    );
  }

  private refrescar(): void {
    this.pintarAssets();
    this.pintarPiezas();
    this.pintarProps();
    this.pintarEstado();
  }

  private pintarAssets(): void {
    const q = this.filtro.value.trim().toLowerCase();
    const items = Object.entries(this.doc.assets)
      .filter(([id, a]) => !q || a.nombre.toLowerCase().includes(q) || id.includes(q))
      .map(([id, a]) => {
        const b = h("button", { className: "ed-item", onclick: () => this.elegirAsset(id) }, a.nombre, h("small", { textContent: ` ${id} · ${a.tipo}` }));
        b.setAttribute("aria-pressed", String(id === this.selId));
        return b;
      });
    this.listaAssets.replaceChildren(...(items.length ? items : [h("p", { className: "ed-vacio", textContent: "No hay assets." })]));
  }

  private pintarPiezas(): void {
    const piezas = this.piezas;
    const items = (piezas ?? []).map((p, i) => {
      const oculta = this.ocultas.has(p);
      const b = h(
        "button",
        { className: "ed-item", onclick: (e) => this.clicPieza(i, e.shiftKey ? "rango" : e.ctrlKey || e.metaKey ? "alternar" : "solo") },
        `${i + 1}. ${p.forma}${oculta ? " (oculta)" : ""} `,
        h("span", { className: "ed-muestra" }),
      );
      (b.lastChild as HTMLElement).style.background = `#${p.color.toString(16).padStart(6, "0")}`;
      b.setAttribute("aria-pressed", String(this.sel.tiene(i)));
      return b;
    });
    const a = this.asset;
    const vacio = !a ? "Elige un asset." : a.tipo === "glb" ? "Un modelo GLB no se edita por piezas." : "Sin piezas.";
    const malla = a?.tipo === "piezas" && a.malla ? [h("p", { className: "ed-vacio", textContent: `Malla hecha: ${triangulosMalla(a.malla)} triángulos (no se edita pieza a pieza)` })] : [];
    this.listaPiezas.replaceChildren(...(items.length ? items : malla.length ? [] : [h("p", { className: "ed-vacio", textContent: vacio })]), ...malla);
  }

  private pintarProps(): void {
    const p = this.pieza;
    this.propsCaja.hidden = !p;
    if (p) {
      // Cada campo enseña el valor si todas las elegidas lo comparten y, si no, se queda vacío («—»).
      for (const { campo, i, input } of this.campos) {
        if (document.activeElement !== input) input.value = this.comun((q) => numero(q[campo][i] / (campo === "rot" ? GRADOS : 1))) ?? "";
      }
      this.forma.value = this.comun((q) => q.forma) ?? "";
      this.propsPrisma.hidden = this.elegidas().some((q) => q.forma !== "prisma");
      for (const { campo, input } of this.camposPrisma) {
        if (document.activeElement !== input) input.value = this.comun((q) => numero((q[campo] ?? 0) / (campo === "fase" ? GRADOS : 1))) ?? "";
      }
      this.color.value = `#${p.color.toString(16).padStart(6, "0")}`;
    }
    this.pintarColision();
  }

  private pintarColision(): void {
    const a = this.asset;
    const circulos = a?.colision ?? [];
    this.listaColision.replaceChildren(
      ...(circulos.length ? [] : [h("p", { className: "ed-vacio", textContent: "Sin colisión: se atraviesa." })]),
      ...circulos.map((c, i) => {
        const campo = (j: number, titulo: string, min: number, max: number): HTMLInputElement => {
          const input = h("input", { type: "number", step: "0.05", min: String(min), max: String(max), title: titulo, value: numero(c[j]) });
          input.onchange = () => {
            const v = parseFloat(input.value);
            if (Number.isNaN(v)) return this.pintarColision();
            c[j] = Math.min(Math.max(Math.round(v * 100) / 100, min), max);
            this.commit();
          };
          return input;
        };
        return h(
          "div",
          { className: "ed-fila" },
          campo(0, "x", -MAX_DESPLAZAMIENTO, MAX_DESPLAZAMIENTO),
          campo(1, "z", -MAX_DESPLAZAMIENTO, MAX_DESPLAZAMIENTO),
          campo(2, "radio", 0, MAX_COLISION),
          h("button", { className: "ed-b", textContent: "×", title: "Quitar círculo", onclick: () => this.quitarCirculo(i) }),
        );
      }),
    );
    if (document.activeElement !== this.distancia) this.distancia.value = a?.distancia !== undefined ? numero(a.distancia) : "";
  }

  private pintarEstado(): void {
    this.a.estado();
  }

  /** Las cifras del asset elegido para la barra de estado; deja al día los botones. */
  estado(): string[] {
    const a = this.asset;
    const partes: string[] = [];
    if (a) {
      const t = this.triangulos(a);
      partes.push(
        a.tipo === "glb" ? `${t ?? "…"} / ${MAX_TRIANGULOS_GLB} triángulos` : `${t} triángulos`,
        ...(a.tipo === "piezas" ? [`${a.piezas.length} / ${MAX_PIEZAS} piezas`] : []),
      );
    }
    const n = this.sel.lista.length;
    if (n > 1) partes.push(`${n} piezas elegidas`);
    if (this.mensaje) partes.push(this.mensaje);

    const tienePieza = n > 0 && !!this.pieza;
    const hayPiezas = !!this.piezas;
    const puedo = (clave: string, si: boolean): void => {
      this.botones.get(clave)!.disabled = !si;
    };
    for (const f of FORMAS) puedo(`add-${f}`, hayPiezas);
    puedo("dup-pieza", tienePieza);
    puedo("espejo", tienePieza);
    puedo("borrar-pieza", tienePieza);
    puedo("copiar", tienePieza);
    puedo("cortar", tienePieza);
    puedo("pegar", hayPiezas && portapapeles.length > 0);
    for (const c of ["x", "y", "z"]) puedo(`alinear-${c}`, n > 1);
    puedo("deshacer", this.a.b.hi > 0);
    puedo("rehacer", this.a.b.hi < this.a.b.hist.length - 1);
    for (const c of ["duplicar", "renombrar", "borrar", "ajustar"]) puedo(c, !!a);
    puedo("optimizar", a?.tipo === "glb");
    puedo("circulo", !!a && (a.colision?.length ?? 0) < MAX_CIRCULOS);
    this.distancia.disabled = !a;
    for (const [m, b] of this.botonesModo) b.setAttribute("aria-pressed", String(m === this.modo));
    return partes;
  }

  private avisar(texto: string): void {
    this.a.aviso(texto);
  }

  // ---------------------------------------------------------------- selección y piezas

  private elegirAsset(id: string): void {
    this.selId = id;
    this.sel.vaciar();
    this.ocultas.clear();
    this.glbMostrado = null;
    this.enfocarAlCargar = true;
    this.dibujar();
    this.vista.enfocar([]);
  }

  /** Clic en una pieza (o en el vacío, con -1): sola, añadida o quitada, o el rango desde la principal. Lo oculto no se elige. */
  private clicPieza(i: number, como: "solo" | "alternar" | "rango"): void {
    const p = this.piezas?.[i];
    if (p && this.ocultas.has(p)) return;
    if (como === "alternar") this.sel.alternar(i);
    else if (como === "rango") this.sel.rango(i);
    else this.sel.solo(i);
    this.dibujar();
  }

  private cambiarEspacio(): void {
    this.local = !this.local;
    this.vista.espacio(this.local);
    this.botones.get("espacio")!.textContent = this.local ? "Ejes: locales" : "Ejes: globales";
  }

  private cambiarModo(m: Modo): void {
    this.modo = m;
    this.vista.modo(m);
    this.pintarEstado();
  }

  private anadir(forma: Forma): void {
    const piezas = this.piezas;
    if (!piezas) return;
    if (piezas.length >= MAX_PIEZAS) return this.avisar(`Un asset puede tener ${MAX_PIEZAS} piezas como mucho.`);
    const prisma = forma === "prisma" ? { lados: 6, arriba: 1, abajo: 1, fase: 0 } : {};
    piezas.push({ forma, pos: [0, 0.5, 0], rot: [0, 0, 0], esc: [1, 1, 1], color: 0xc8c8d0, ...prisma });
    this.sel.solo(piezas.length - 1);
    this.commit();
  }

  private duplicarPieza(): void {
    this.anadirCopias(this.elegidas(), (p) => (p.pos[0] += 0.5));
  }

  private espejoX(): void {
    this.anadirCopias(this.elegidas(), (p) => {
      p.pos[0] = -p.pos[0];
      p.rot = [p.rot[0], -p.rot[1], -p.rot[2]];
    });
  }

  /** Añade una copia de cada pieza (con `cambio` aplicado) y las deja elegidas, en un solo paso de historial. */
  private anadirCopias(origen: Pieza[], cambio: (p: Pieza) => void = () => {}): void {
    const piezas = this.piezas;
    if (!piezas || !origen.length) return;
    if (piezas.length + origen.length > MAX_PIEZAS) return this.avisar(`Un asset puede tener ${MAX_PIEZAS} piezas como mucho.`);
    const copias = origen.map((p) => {
      const c = structuredClone(p);
      cambio(c);
      return c;
    });
    const n = piezas.length;
    piezas.push(...copias);
    this.sel.poner(copias.map((_, k) => n + k));
    this.commit();
  }

  private borrarPieza(): void {
    const piezas = this.piezas;
    const indices = this.sel.lista;
    if (!piezas || !indices.length) return;
    for (const i of [...indices].reverse()) piezas.splice(i, 1);
    this.sel.trasBorrar(indices);
    this.commit();
  }

  private copiar(): void {
    const ps = this.elegidas();
    if (ps.length) portapapeles = ps.map((p) => structuredClone(p));
    this.pintarEstado();
  }

  private cortar(): void {
    this.copiar();
    this.borrarPieza();
  }

  /** Pega en el sitio de donde se copió; lo pegado queda elegido. */
  private pegar(): void {
    this.anadirCopias(portapapeles);
  }

  /** Lleva la coordenada `c` (0 x, 1 y, 2 z) de todas las elegidas a la de la principal. */
  private alinear(c: 0 | 1 | 2): void {
    const ref = this.pieza?.pos[c];
    if (ref === undefined) return;
    for (const p of this.elegidas()) p.pos[c] = ref;
    this.commit();
  }

  /** Mueve lo elegido (metros), sin salirse de ±60 como los campos de posición. */
  private mover(d: Vec3): void {
    const ps = this.elegidas();
    if (!ps.length) return;
    for (const p of ps) p.pos = redondear(p.pos.map((x, k) => Math.min(Math.max(x + d[k], -60), 60)) as Vec3);
    this.commit();
  }

  /** H: oculta lo elegido (solo en el editor); Alt+H lo enseña todo. */
  private ocultar(todoVisible: boolean): void {
    if (todoVisible) this.ocultas.clear();
    else for (const p of this.elegidas()) this.ocultas.add(p);
    this.sel.poner(this.sel.lista.filter((i) => !this.ocultas.has(this.piezas![i])));
    this.dibujar();
  }

  private anadirCirculo(): void {
    const a = this.asset;
    if (!a) return;
    (a.colision ??= []).push([0, 0, 0.5]);
    this.commit();
  }

  private quitarCirculo(i: number): void {
    const a = this.asset;
    if (!a?.colision) return;
    a.colision.splice(i, 1);
    if (!a.colision.length) a.colision = null;
    this.commit();
  }

  /** Deja un solo círculo, sacado de la caja envolvente en x y z. */
  private ajustarColision(): void {
    const a = this.asset;
    const c = this.vista.cajaEnvolvente();
    if (!a || !c) return;
    a.colision = [[this.redondear2(c[0], MAX_DESPLAZAMIENTO), this.redondear2(c[1], MAX_DESPLAZAMIENTO), Math.min(Math.max(this.redondear2(c[2], MAX_COLISION), 0), MAX_COLISION)]];
    this.commit();
  }

  private reajustarSeleccion(): void {
    if (!this.asset) this.selId = Object.keys(this.doc.assets)[0] ?? null;
    const piezas = this.piezas;
    if (piezas) this.sel.ajustar(piezas.length);
    else this.sel.vaciar();
  }

  // ---------------------------------------------------------------- assets

  private async nuevo(): Promise<void> {
    if (Object.keys(this.doc.assets).length >= MAX_ASSETS) return this.avisar(`El mundo puede tener ${MAX_ASSETS} assets como mucho.`);
    const nombre = (await dialogo(this.a.raiz, "Nuevo asset", "Nombre del asset:", "Crear", ""))?.trim().slice(0, MAX_NOMBRE);
    if (!nombre) return;
    const pieza: Pieza = { forma: "caja", pos: [0, 0.5, 0], rot: [0, 0, 0], esc: [1, 1, 1], color: 0xc8c8d0 };
    this.anadirAsset(nombre, { nombre, tipo: "piezas", piezas: [pieza], colision: null });
  }

  private anadirAsset(nombre: string, asset: Asset): void {
    const id = sacarId(nombre, this.doc.assets);
    if (!ID.test(id)) return this.avisar("No se ha podido sacar un id válido del nombre.");
    this.doc.assets[id] = asset;
    this.filtro.value = "";
    this.elegirAsset(id);
    this.commit();
  }

  private async importar(): Promise<void> {
    const f = this.archivo.files?.[0];
    this.archivo.value = "";
    if (!f) return;
    if (Object.keys(this.doc.assets).length >= MAX_ASSETS) return this.avisar(`El mundo puede tener ${MAX_ASSETS} assets como mucho.`);
    this.mensaje = "Subiendo modelo…";
    this.pintarEstado();
    try {
      let datos = await f.arrayBuffer();
      const info = await analizar(datos).catch(() => null); // si no se lee, que lo diga el servidor
      if (info && (info.triangulos > MAX_TRIANGULOS_GLB || datos.byteLength > MAX_BYTES_GLB)) {
        const nuevo = await this.ventanaOptimizar(datos, info, Math.min(info.triangulos, MAX_TRIANGULOS_GLB));
        if (!nuevo) return;
        datos = nuevo;
      }
      const r = await api.subirGlb(this.a.sesion, datos);
      this.triangulosGlb.set(r.sha256, r.triangles);
      const nombre = f.name.replace(/\.glb$/i, "").slice(0, MAX_NOMBRE) || "modelo";
      this.anadirAsset(nombre, { nombre, tipo: "glb", glb: r.sha256, colision: null });
    } catch (e) {
      this.a.fallo(e);
    } finally {
      this.mensaje = "";
      this.pintarEstado();
    }
  }

  /** Ventana para bajar los triángulos de un GLB. Devuelve el GLB nuevo, o `null` si se cancela. */
  private ventanaOptimizar(glb: ArrayBuffer, info: Analisis, porDefecto: number): Promise<ArrayBuffer | null> {
    return new Promise((resolver) => {
      const { fondo, caja } = modal(this.a.raiz, "Optimizar polígonos");
      const valor = Math.min(MAX_TRIANGULOS_GLB, Math.max(100, Math.round(porDefecto)));
      const rango = h("input", { type: "range", min: "100", max: String(MAX_TRIANGULOS_GLB), step: "100", value: String(valor) });
      const num = h("input", { type: "number", min: "100", max: String(MAX_TRIANGULOS_GLB), value: String(valor) });
      rango.oninput = () => (num.value = rango.value);
      num.oninput = () => (rango.value = num.value);
      const aviso = h("p", { textContent: "" });
      const ok = h("button", { className: "ed-b ed-primario", textContent: "Optimizar y subir" });
      const cancelar = h("button", { className: "ed-b", textContent: "Cancelar", onclick: () => fin(null) });
      const fin = (v: ArrayBuffer | null): void => {
        fondo.remove();
        resolver(v);
      };
      ok.onclick = async () => {
        const objetivo = Math.min(MAX_TRIANGULOS_GLB, Math.max(100, Math.floor(Number(num.value)) || valor));
        for (const e of [ok, cancelar, rango, num]) e.disabled = true;
        aviso.textContent = "Optimizando…";
        try {
          const r = await optimizar(glb, objetivo);
          if (r.despues > MAX_TRIANGULOS_GLB) {
            aviso.textContent = `Se ha quedado en ${r.despues} triángulos (el máximo es ${MAX_TRIANGULOS_GLB}): tiene muchas piezas pequeñas. Baja más el objetivo.`;
          } else if (r.glb.byteLength > MAX_BYTES_GLB) {
            aviso.textContent = `El modelo optimizado tiene ${r.despues} triángulos pero pesa ${mb(r.glb.byteLength)} MB y el límite es 1 MB. Baja más el objetivo o simplifica el modelo en otro programa.`;
          } else return fin(r.glb);
        } catch (e) {
          aviso.textContent = `No se ha podido optimizar: ${e instanceof Error ? e.message : String(e)}`;
        }
        for (const e of [ok, cancelar, rango, num]) e.disabled = false;
      };
      caja.append(
        h("p", {
          textContent: `El modelo tiene ${info.triangulos.toLocaleString("es-ES")} triángulos y pesa ${mb(glb.byteLength)} MB; el máximo es ${MAX_TRIANGULOS_GLB.toLocaleString("es-ES")} triángulos y 1 MB. Elige a cuántos triángulos reducirlo. Se conservan la forma y los colores por vértice, y las normales se recalculan.`,
        }),
        ...(info.texturas ? [h("p", { textContent: "⚠ Tiene texturas: se quitan y el modelo se verá con el color liso de cada material." })] : []),
        ...(info.animado ? [h("p", { textContent: "⚠ Tiene huesos o animaciones: se queda quieto en su postura de reposo." })] : []),
        h("label", { className: "ed-vec" }, h("span", { textContent: "Triángulos" }), rango, num),
        aviso,
        h("div", { className: "ed-fila" }, ok, cancelar),
      );
      // Las teclas de la ventana no llegan a los atajos del editor.
      caja.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Escape" && !cancelar.disabled) fin(null);
      });
      num.focus();
    });
  }

  private async optimizarAsset(): Promise<void> {
    const a = this.asset;
    if (a?.tipo !== "glb") return;
    this.mensaje = "Descargando modelo…";
    this.pintarEstado();
    try {
      const res = await fetch(`/api/glb/${a.glb}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const datos = await res.arrayBuffer();
      const info = await analizar(datos);
      this.mensaje = "";
      this.pintarEstado();
      const nuevo = await this.ventanaOptimizar(datos, info, info.triangulos / 2);
      if (!nuevo) return;
      this.mensaje = "Subiendo modelo…";
      this.pintarEstado();
      const r = await api.subirGlb(this.a.sesion, nuevo);
      this.triangulosGlb.set(r.sha256, r.triangles);
      a.glb = r.sha256;
      this.commit();
    } catch (e) {
      this.a.fallo(e);
    } finally {
      this.mensaje = "";
      this.pintarEstado();
    }
  }

  private duplicarAsset(): void {
    const a = this.asset;
    if (!a) return;
    if (Object.keys(this.doc.assets).length >= MAX_ASSETS) return this.avisar(`El mundo puede tener ${MAX_ASSETS} assets como mucho.`);
    const nombre = `${a.nombre.slice(0, MAX_NOMBRE - 8)} (copia)`;
    this.anadirAsset(nombre, { ...structuredClone(a), nombre });
  }

  private async renombrar(): Promise<void> {
    const a = this.asset;
    if (!a) return;
    const nombre = (await dialogo(this.a.raiz, "Renombrar asset", "Nuevo nombre (el id no cambia):", "Renombrar", a.nombre))?.trim().slice(0, MAX_NOMBRE);
    if (!nombre) return;
    a.nombre = nombre;
    this.commit();
  }

  private async borrarAsset(): Promise<void> {
    const id = this.selId;
    const a = this.asset;
    if (!id || !a) return;
    const usos = this.doc.objetos.filter((o) => o.asset === id).length;
    if (usos) return this.avisar(`No se puede borrar «${a.nombre}»: ${usos === 1 ? "lo usa 1 objeto" : `lo usan ${usos} objetos`} del mundo.`);
    if ((await dialogo(this.a.raiz, "Borrar asset", `¿Borrar «${a.nombre}»? Se puede deshacer hasta que cierres el editor.`, "Borrar")) === null) return;
    delete this.doc.assets[id];
    this.selId = Object.keys(this.doc.assets)[0] ?? null;
    this.sel.vaciar();
    this.ocultas.clear();
    this.glbMostrado = null;
    this.commit();
  }

  // ---------------------------------------------------------------- teclado

  /** Atajos de este modo (el editor ya ha tratado Ctrl+S, Ctrl+Z y Ctrl+Y). */
  tecla(e: KeyboardEvent, k: string, ctrl: boolean): boolean {
    const paso = e.shiftKey ? 1 : 0.1;
    const flechas: Record<string, Vec3> = {
      arrowleft: [-paso, 0, 0],
      arrowright: [paso, 0, 0],
      arrowup: [0, 0, -paso],
      arrowdown: [0, 0, paso],
      pageup: [0, paso, 0],
      pagedown: [0, -paso, 0],
    };
    if (ctrl) {
      if (k === "d") this.duplicarPieza();
      else if (k === "c") this.copiar();
      else if (k === "x") this.cortar();
      else if (k === "v") this.pegar();
      else if (k === "a") {
        this.sel.todos(this.piezas?.length ?? 0, (i) => this.ocultas.has(this.piezas![i]));
        this.dibujar();
      } else return false;
    } else if (k === "delete" || k === "backspace") this.borrarPieza();
    else if (k === "escape") {
      this.sel.vaciar();
      this.dibujar();
    } else if (k === "f") this.vista.enfocar(this.sel.lista);
    else if (k === "h" || e.code === "KeyH") this.ocultar(e.altKey);
    else if (k === "x") this.cambiarEspacio();
    else if (k in flechas) this.mover(flechas[k]);
    else if (MODOS.some(([, , tecla]) => tecla === k)) this.cambiarModo(MODOS.find(([, , tecla]) => tecla === k)![0]);
    else return false;
    return true;
  }
}
