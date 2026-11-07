// Modo «Assets» del editor del Jovaniverso (solo administradores, solo ordenador): monta y edita assets de piezas o importa GLB.
// El borrador, guardar, publicar y deshacer son del editor entero (`borrador.ts`, `editor.ts`).
import * as api from "./api";
import { dialogo, h } from "./dom";
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
import type { Anfitrion, Panel } from "./panel";
import { type Modo, Vista } from "./vista";

const GRADOS = Math.PI / 180;
const MAX_COLISION = 40;
const MAX_DESPLAZAMIENTO = 60;
const MODOS: [Modo, string, string][] = [
  ["translate", "Mover (W)", "w"],
  ["rotate", "Girar (E)", "e"],
  ["scale", "Escalar (R)", "r"],
];

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
  private piezaSel = -1;
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
    this.vista.alElegir = (i) => this.elegirPieza(i);
    this.vista.alTransformar = (i, fin) => this.arrastrar(i, fin);
    this.filtro.addEventListener("input", () => this.pintarAssets());
    this.archivo.addEventListener("change", () => void this.importar());
    this.dibujar();
    this.enfocarAlCargar = true;
    this.vista.enfocar(-1);
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
      ),
      this.listaAssets,
      this.archivo,
    );
  }

  private centro(): HTMLElement {
    const formas = FORMAS.map((f) => this.boton(`add-${f}`, f, () => this.anadir(f), `Añadir ${f}`));
    const modos = MODOS.map(([m, texto]) => {
      const b = h("button", { className: "ed-b", textContent: texto, onclick: () => this.cambiarModo(m) });
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
        this.boton("dup-pieza", "Duplicar pieza", () => this.duplicarPieza(), "Ctrl+D"),
        this.boton("espejo", "Espejo X", () => this.espejoX()),
        this.boton("borrar-pieza", "Borrar pieza", () => this.borrarPieza(), "Supr"),
        h("span", { className: "ed-sep" }),
        this.boton("deshacer", "Deshacer", () => this.a.b.irA(this.a.b.hi - 1), "Ctrl+Z"),
        this.boton("rehacer", "Rehacer", () => this.a.b.irA(this.a.b.hi + 1), "Ctrl+Y"),
        this.boton("enfocar", "Enfocar", () => this.vista.enfocar(this.piezaSel), "F"),
        h("span", { className: "ed-sep" }),
        ...modos,
        h("label", { className: "ed-etiqueta", title: "0,1 m, 15° y 0,1 de escala" }, ajuste, " Ajuste"),
      ),
      this.contenedor,
    );
  }

  private derecha(): HTMLElement {
    for (const f of FORMAS) this.forma.append(h("option", { value: f, textContent: f }));
    this.forma.onchange = () =>
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
        const input = h("input", { type: "number", step: paso });
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
      const input = h("input", { type: "number", step: paso });
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

  private get pieza(): Pieza | null {
    return this.piezas?.[this.piezaSel] ?? null;
  }

  private triangulos(a: Asset): number | null {
    if (a.tipo === "glb") return this.triangulosGlb.get(a.glb) ?? null;
    return a.piezas.reduce((n, p) => n + triangulosPieza(p), a.malla ? triangulosMalla(a.malla) : 0);
  }

  private editarPrisma(campo: "lados" | "arriba" | "abajo" | "fase", input: HTMLInputElement): void {
    const v = parseFloat(input.value);
    const p = this.pieza;
    if (!p || p.forma !== "prisma" || Number.isNaN(v)) return this.refrescar();
    if (campo === "lados") p.lados = Math.min(Math.max(Math.round(v), MIN_LADOS), MAX_LADOS);
    else if (campo === "fase") p.fase = Math.min(Math.max(v, -720), 720) * GRADOS;
    else p[campo] = Math.min(Math.max(v, 0), 1);
    // El servidor pide alguna tapa con radio.
    if (!p.arriba && !p.abajo) p[campo === "abajo" ? "abajo" : "arriba"] = 0.05;
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

  /** Cambia la pieza elegida; con `cerrar` falso es un paso intermedio (no entra en el historial). */
  private editar(fn: (p: Pieza) => void, cerrar = true): void {
    const p = this.pieza;
    if (!p) return;
    fn(p);
    if (cerrar) this.commit();
    else {
      this.a.b.marcarSucio();
      this.dibujar();
    }
  }

  private editarCampo(campo: "pos" | "rot" | "esc", i: number, input: HTMLInputElement): void {
    const v = parseFloat(input.value);
    const p = this.pieza;
    if (!p || Number.isNaN(v)) return this.refrescar();
    const [min, max, k] = campo === "pos" ? [-60, 60, 1] : campo === "esc" ? [0.02, 60, 1] : [-720, 720, GRADOS];
    p[campo][i] = Math.min(Math.max(v, min), max) * k;
    this.commit();
  }

  private arrastrar(i: number, fin: boolean): void {
    const p = this.piezas?.[i];
    if (!p) return;
    const t = this.vista.transformacion(i);
    [p.pos, p.rot, p.esc] = [redondear(t.pos), redondear(t.rot), redondear(t.esc)];
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
      this.vista.mostrarPiezas(a.piezas, this.piezaSel, a.malla);
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
        if (this.enfocarAlCargar) this.vista.enfocar(-1);
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
      const b = h("button", { className: "ed-item", onclick: () => this.elegirPieza(i) }, `${i + 1}. ${p.forma} `, h("span", { className: "ed-muestra" }));
      (b.lastChild as HTMLElement).style.background = `#${p.color.toString(16).padStart(6, "0")}`;
      b.setAttribute("aria-pressed", String(i === this.piezaSel));
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
      for (const { campo, i, input } of this.campos) {
        if (document.activeElement !== input) input.value = numero(p[campo][i] / (campo === "rot" ? GRADOS : 1));
      }
      this.forma.value = p.forma;
      this.propsPrisma.hidden = p.forma !== "prisma";
      for (const { campo, input } of this.camposPrisma) {
        if (document.activeElement !== input) input.value = numero(p.forma !== "prisma" ? 0 : (p[campo] ?? 0) / (campo === "fase" ? GRADOS : 1));
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
    if (this.mensaje) partes.push(this.mensaje);

    const tienePieza = !!this.pieza;
    const hayPiezas = !!this.piezas;
    const puedo = (clave: string, si: boolean): void => {
      this.botones.get(clave)!.disabled = !si;
    };
    for (const f of FORMAS) puedo(`add-${f}`, hayPiezas);
    puedo("dup-pieza", tienePieza);
    puedo("espejo", tienePieza);
    puedo("borrar-pieza", tienePieza);
    puedo("deshacer", this.a.b.hi > 0);
    puedo("rehacer", this.a.b.hi < this.a.b.hist.length - 1);
    for (const c of ["duplicar", "renombrar", "borrar", "ajustar"]) puedo(c, !!a);
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
    this.piezaSel = -1;
    this.glbMostrado = null;
    this.enfocarAlCargar = true;
    this.dibujar();
    this.vista.enfocar(-1);
  }

  private elegirPieza(i: number): void {
    this.piezaSel = i;
    this.dibujar();
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
    this.piezaSel = piezas.length - 1;
    this.commit();
  }

  private duplicarPieza(): void {
    this.copiarPieza((p) => (p.pos[0] += 0.5));
  }

  private espejoX(): void {
    this.copiarPieza((p) => {
      p.pos[0] = -p.pos[0];
      p.rot = [p.rot[0], -p.rot[1], -p.rot[2]];
    });
  }

  private copiarPieza(cambio: (p: Pieza) => void): void {
    const piezas = this.piezas;
    const p = this.pieza;
    if (!piezas || !p) return;
    if (piezas.length >= MAX_PIEZAS) return this.avisar(`Un asset puede tener ${MAX_PIEZAS} piezas como mucho.`);
    const copia = structuredClone(p);
    cambio(copia);
    piezas.push(copia);
    this.piezaSel = piezas.length - 1;
    this.commit();
  }

  private borrarPieza(): void {
    if (!this.pieza) return;
    this.piezas!.splice(this.piezaSel, 1);
    this.piezaSel = -1;
    this.commit();
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
    if (!this.pieza) this.piezaSel = -1;
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
      const r = await api.subirGlb(this.a.sesion, await f.arrayBuffer());
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
    this.piezaSel = -1;
    this.glbMostrado = null;
    this.commit();
  }

  // ---------------------------------------------------------------- teclado

  /** Atajos de este modo (el editor ya ha tratado Ctrl+S, Ctrl+Z y Ctrl+Y). */
  tecla(_e: KeyboardEvent, k: string, ctrl: boolean): boolean {
    if (ctrl && k === "d") this.duplicarPieza();
    else if (!ctrl && k === "delete") this.borrarPieza();
    else if (!ctrl && k === "f") this.vista.enfocar(this.piezaSel);
    else if (!ctrl && MODOS.some(([, , tecla]) => tecla === k)) this.cambiarModo(MODOS.find(([, , tecla]) => tecla === k)![0]);
    else return false;
    return true;
  }
}
