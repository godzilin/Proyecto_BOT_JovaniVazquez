// Editor de assets del Jovaniverso (solo administradores, solo ordenador): monta y edita assets de piezas o importa GLB, guarda el borrador y publica el mundo.
import * as api from "./api";
import { Conflicto, type Sesion, type Version } from "./api";
import { dialogo, h, modal } from "./dom";
import {
  type Asset,
  cargarGlb,
  comprobarFormas,
  FORMAS,
  FORMATO,
  type Forma,
  ID,
  MAX_ASSETS,
  MAX_NOMBRE,
  MAX_PIEZAS,
  MAX_TRIANGULOS_GLB,
  type MundoDoc,
  type Pieza,
  TRIANGULOS,
  triangulosModelo,
  type Vec3,
} from "./formato";
import { type Modo, Vista } from "./vista";

const GRADOS = Math.PI / 180;
const AUTOGUARDADO_MS = 20_000;
const MAX_HISTORIAL = 100;
const MAX_COLISION = 40;
const MODOS: [Modo, string, string][] = [
  ["translate", "Mover (W)", "w"],
  ["rotate", "Girar (E)", "e"],
  ["scale", "Escalar (R)", "r"],
];

let abierto = false;

/**
 * Abre el editor a pantalla completa. Se resuelve cuando el borrador está cargado; el editor sigue
 * abierto hasta que el administrador lo cierra. Si no se puede cargar, lanza el error del servidor.
 */
export async function abrirEditor(sesion: Sesion, mundo: { pausar(si: boolean): void } | null): Promise<void> {
  if (abierto) return;
  abierto = true;
  try {
    if (import.meta.env.DEV) comprobarFormas();
    const b = await api.borrador(sesion);
    if (b.doc.v !== FORMATO) throw new Error("El editor está desfasado: recarga la página.");
    new Editor(sesion, mundo, b).iniciar();
  } catch (e) {
    abierto = false;
    throw e;
  }
}

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

class Editor {
  private doc: MundoDoc;
  private rev: number;
  private version: number;
  private selId: string | null = null;
  private piezaSel = -1;
  private sucio = false;
  private guardando = false;
  private conflicto = false;
  private cambios = 0;
  private hist: string[] = [];
  private hi = 0;
  private modo: Modo = "translate";
  private glbMostrado: string | null = null;
  private enfocarAlCargar = false;
  private readonly triangulosGlb = new Map<string, number>();
  private timer = 0;

  private readonly raiz = h("div", { className: "ed" });
  private readonly vista: Vista;
  private readonly listaAssets = h("div", { className: "ed-lista" });
  private readonly listaPiezas = h("div", { className: "ed-lista" });
  private readonly filtro = h("input", { type: "search", placeholder: "Filtrar assets", className: "ed-filtro" });
  private readonly aviso = h("div", { className: "ed-aviso", hidden: true });
  private readonly estado = h("footer", { className: "ed-estado" });
  private readonly botones = new Map<string, HTMLButtonElement>();
  private readonly botonesModo = new Map<Modo, HTMLButtonElement>();
  private readonly campos: { campo: "pos" | "rot" | "esc"; i: number; input: HTMLInputElement }[] = [];
  private readonly forma = h("select");
  private readonly color = h("input", { type: "color" });
  private readonly colisionOn = h("input", { type: "checkbox" });
  private readonly colisionR = h("input", { type: "number", min: "0", max: String(MAX_COLISION), step: "0.05" });
  private readonly propsCaja = h("div", { className: "ed-props" });
  private readonly archivo = h("input", { type: "file", accept: ".glb", hidden: true });
  private mensaje = "";

  constructor(
    private readonly sesion: Sesion,
    private readonly mundo: { pausar(si: boolean): void } | null,
    b: { doc: MundoDoc; rev: number; version: number },
  ) {
    this.doc = b.doc;
    this.rev = b.rev;
    this.version = b.version;
    this.hist = [JSON.stringify(this.doc)];
    this.selId = Object.keys(this.doc.assets)[0] ?? null;

    this.raiz.append(this.arriba(), this.izquierda(), this.centro(), this.derecha(), this.estado, this.aviso, this.archivo);
    // Las teclas escritas en un campo no llegan al mundo, que escucha en window.
    this.raiz.addEventListener("keydown", (e) => e.stopPropagation());
    this.raiz.addEventListener("keyup", (e) => e.stopPropagation());
    document.body.append(this.raiz);
    this.vista = new Vista(this.raiz.querySelector<HTMLElement>(".ed-vista")!);
    this.vista.alElegir = (i) => this.elegirPieza(i);
    this.vista.alTransformar = (i, fin) => this.arrastrar(i, fin);
  }

  iniciar(): void {
    this.mundo?.pausar(true);
    window.addEventListener("keydown", this.alTecla, true);
    window.addEventListener("keyup", this.alSoltarTecla, true);
    this.filtro.addEventListener("input", () => this.pintarAssets());
    this.archivo.addEventListener("change", () => void this.importar());
    this.timer = window.setInterval(() => {
      if (this.sucio && !this.guardando && !this.conflicto) void this.guardar();
    }, AUTOGUARDADO_MS);
    this.dibujar();
    this.enfocarAlCargar = true;
    this.vista.enfocar(-1);
  }

  // ---------------------------------------------------------------- construcción de la pantalla

  private boton(clave: string, texto: string, fn: () => void, titulo = ""): HTMLButtonElement {
    const b = h("button", { className: "ed-b", textContent: texto, title: titulo, onclick: fn });
    this.botones.set(clave, b);
    return b;
  }

  private arriba(): HTMLElement {
    return h(
      "header",
      { className: "ed-arriba" },
      h("h1", { textContent: "Editor de assets" }),
      h("span", { className: "ed-hueco" }),
      this.boton("guardar", "Guardar borrador", () => void this.guardar(), "Ctrl+S"),
      this.boton("publicar", "Publicar", () => void this.publicar()),
      this.boton("versiones", "Versiones", () => void this.verVersiones()),
      this.boton("cerrar", "Cerrar", () => void this.cerrar()),
    );
  }

  private izquierda(): HTMLElement {
    return h(
      "aside",
      { className: "ed-panel ed-izq" },
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
      { className: "ed-centro" },
      h(
        "div",
        { className: "ed-barra" },
        ...formas,
        h("span", { className: "ed-sep" }),
        this.boton("dup-pieza", "Duplicar pieza", () => this.duplicarPieza(), "Ctrl+D"),
        this.boton("espejo", "Espejo X", () => this.espejoX()),
        this.boton("borrar-pieza", "Borrar pieza", () => this.borrarPieza(), "Supr"),
        h("span", { className: "ed-sep" }),
        this.boton("deshacer", "Deshacer", () => this.deshacer(), "Ctrl+Z"),
        this.boton("rehacer", "Rehacer", () => this.rehacer(), "Ctrl+Y"),
        this.boton("enfocar", "Enfocar", () => this.vista.enfocar(this.piezaSel), "F"),
        h("span", { className: "ed-sep" }),
        ...modos,
        h("label", { className: "ed-etiqueta", title: "0,1 m, 15° y 0,1 de escala" }, ajuste, " Ajuste"),
      ),
      h("div", { className: "ed-vista" }),
    );
  }

  private derecha(): HTMLElement {
    for (const f of FORMAS) this.forma.append(h("option", { value: f, textContent: f }));
    this.forma.onchange = () => this.editar((p) => (p.forma = this.forma.value as Forma));
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

    this.colisionOn.onchange = () => {
      const a = this.asset;
      if (!a) return;
      a.colision = this.colisionOn.checked ? Math.max(this.redondearRadio(this.vista.radioEnvolvente()), 0.1) : null;
      this.commit();
    };
    this.colisionR.onchange = () => {
      const a = this.asset;
      const v = parseFloat(this.colisionR.value);
      if (!a || a.colision === null || Number.isNaN(v)) return this.refrescar();
      a.colision = Math.min(Math.max(v, 0), MAX_COLISION);
      this.commit();
    };
    const colision = h(
      "div",
      { className: "ed-colision" },
      h("h2", { textContent: "Colisión" }),
      h(
        "div",
        { className: "ed-fila" },
        h("label", { className: "ed-etiqueta" }, this.colisionOn, " Sólido"),
        this.colisionR,
        this.boton("ajustar", "Ajustar", () => this.ajustarColision(), "El radio sale de la caja envolvente en x y z"),
      ),
    );
    return h("aside", { className: "ed-panel ed-der" }, h("h2", { textContent: "Piezas" }), this.listaPiezas, this.propsCaja, colision);
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
    return a.tipo === "piezas" ? a.piezas.reduce((n, p) => n + TRIANGULOS[p.forma], 0) : (this.triangulosGlb.get(a.glb) ?? null);
  }

  private redondearRadio(r: number): number {
    return Math.min(Math.round(r * 100) / 100, MAX_COLISION);
  }

  /** Cierra un cambio: entra en el historial, marca el borrador como sucio y lo vuelve a dibujar. */
  private commit(): void {
    const s = JSON.stringify(this.doc);
    if (s !== this.hist[this.hi]) {
      this.hist.length = this.hi + 1;
      this.hist.push(s);
      if (this.hist.length > MAX_HISTORIAL) this.hist.shift();
      this.hi = this.hist.length - 1;
      this.sucio = true;
      this.cambios++;
    }
    this.dibujar();
  }

  /** Cambia la pieza elegida; con `cerrar` falso es un paso intermedio (no entra en el historial). */
  private editar(fn: (p: Pieza) => void, cerrar = true): void {
    const p = this.pieza;
    if (!p) return;
    fn(p);
    if (cerrar) this.commit();
    else {
      this.sucio = true;
      this.cambios++;
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
      this.vista.mostrarPiezas(a.piezas, this.piezaSel);
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
    this.listaPiezas.replaceChildren(...(items.length ? items : [h("p", { className: "ed-vacio", textContent: vacio })]));
  }

  private pintarProps(): void {
    const p = this.pieza;
    this.propsCaja.hidden = !p;
    if (p) {
      for (const { campo, i, input } of this.campos) {
        if (document.activeElement !== input) input.value = numero(p[campo][i] / (campo === "rot" ? GRADOS : 1));
      }
      this.forma.value = p.forma;
      this.color.value = `#${p.color.toString(16).padStart(6, "0")}`;
    }
    const a = this.asset;
    this.colisionOn.disabled = this.colisionR.disabled = !a;
    this.colisionOn.checked = a?.colision != null;
    this.colisionR.disabled = a?.colision == null;
    if (document.activeElement !== this.colisionR) this.colisionR.value = a?.colision != null ? numero(a.colision) : "";
  }

  private pintarEstado(): void {
    const a = this.asset;
    const partes: string[] = [];
    if (a) {
      const t = this.triangulos(a);
      partes.push(
        a.tipo === "glb" ? `${t ?? "…"} / ${MAX_TRIANGULOS_GLB} triángulos` : `${t} triángulos`,
        ...(a.tipo === "piezas" ? [`${a.piezas.length} / ${MAX_PIEZAS} piezas`] : []),
      );
    }
    partes.push(this.guardando ? "Guardando…" : this.sucio ? "Sin guardar" : "Guardado");
    partes.push(this.version ? `Publicada: versión ${this.version}` : "Sin publicar");
    if (this.mensaje) partes.push(this.mensaje);
    this.estado.replaceChildren(...partes.map((t) => h("span", { textContent: t })));

    const tienePieza = !!this.pieza;
    const hayPiezas = !!this.piezas;
    const puedo = (clave: string, si: boolean): void => {
      this.botones.get(clave)!.disabled = !si;
    };
    for (const f of FORMAS) puedo(`add-${f}`, hayPiezas);
    puedo("dup-pieza", tienePieza);
    puedo("espejo", tienePieza);
    puedo("borrar-pieza", tienePieza);
    puedo("deshacer", this.hi > 0);
    puedo("rehacer", this.hi < this.hist.length - 1);
    for (const c of ["duplicar", "renombrar", "borrar", "ajustar"]) puedo(c, !!a);
    puedo("guardar", !this.guardando);
    for (const [m, b] of this.botonesModo) b.setAttribute("aria-pressed", String(m === this.modo));
  }

  private avisar(texto: string, accion?: { texto: string; fn: () => void }, error = true): void {
    this.aviso.replaceChildren(h("span", { textContent: texto }));
    if (accion) this.aviso.append(h("button", { className: "ed-b", textContent: accion.texto, onclick: accion.fn }));
    this.aviso.append(h("button", { className: "ed-b", textContent: "×", title: "Cerrar aviso", onclick: () => (this.aviso.hidden = true) }));
    this.aviso.setAttribute("role", error ? "alert" : "status");
    this.aviso.classList.toggle("ed-ok", !error);
    this.aviso.hidden = false;
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
    piezas.push({ forma, pos: [0, 0.5, 0], rot: [0, 0, 0], esc: [1, 1, 1], color: 0xc8c8d0 });
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

  private ajustarColision(): void {
    const a = this.asset;
    if (!a) return;
    a.colision = this.redondearRadio(this.vista.radioEnvolvente());
    this.commit();
  }

  // ---------------------------------------------------------------- historial

  private deshacer(): void {
    this.irA(this.hi - 1);
  }

  private rehacer(): void {
    this.irA(this.hi + 1);
  }

  private irA(i: number): void {
    if (i < 0 || i >= this.hist.length) return;
    this.hi = i;
    this.doc = JSON.parse(this.hist[i]) as MundoDoc;
    this.sucio = true;
    this.cambios++;
    this.reajustarSeleccion();
    this.dibujar();
  }

  private reajustarSeleccion(): void {
    if (!this.asset) this.selId = Object.keys(this.doc.assets)[0] ?? null;
    if (!this.pieza) this.piezaSel = -1;
  }

  // ---------------------------------------------------------------- assets

  private async nuevo(): Promise<void> {
    if (Object.keys(this.doc.assets).length >= MAX_ASSETS) return this.avisar(`El mundo puede tener ${MAX_ASSETS} assets como mucho.`);
    const nombre = (await dialogo(this.raiz, "Nuevo asset", "Nombre del asset:", "Crear", ""))?.trim().slice(0, MAX_NOMBRE);
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
      const r = await api.subirGlb(this.sesion, await f.arrayBuffer());
      this.triangulosGlb.set(r.sha256, r.triangles);
      const nombre = f.name.replace(/\.glb$/i, "").slice(0, MAX_NOMBRE) || "modelo";
      this.anadirAsset(nombre, { nombre, tipo: "glb", glb: r.sha256, colision: null });
    } catch (e) {
      this.fallo(e);
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
    const nombre = (await dialogo(this.raiz, "Renombrar asset", "Nuevo nombre (el id no cambia):", "Renombrar", a.nombre))?.trim().slice(0, MAX_NOMBRE);
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
    if ((await dialogo(this.raiz, "Borrar asset", `¿Borrar «${a.nombre}»? Se puede deshacer hasta que cierres el editor.`, "Borrar")) === null) return;
    delete this.doc.assets[id];
    this.selId = Object.keys(this.doc.assets)[0] ?? null;
    this.piezaSel = -1;
    this.glbMostrado = null;
    this.commit();
  }

  // ---------------------------------------------------------------- servidor

  private fallo(e: unknown): void {
    const texto = e instanceof Error ? e.message : "Error desconocido.";
    if (e instanceof Conflicto) {
      this.conflicto = true;
      this.avisar(`${texto} Al recargar se pierden los cambios sin guardar de esta pantalla.`, { texto: "Recargar", fn: () => void this.recargar() });
    } else this.avisar(texto);
  }

  private async guardar(): Promise<boolean> {
    if (this.guardando || this.conflicto) return false;
    this.guardando = true;
    this.pintarEstado();
    const n = this.cambios;
    try {
      this.rev = (await api.guardar(this.sesion, this.rev, this.doc)).rev;
      if (n === this.cambios) this.sucio = false;
      this.aviso.hidden = true;
      return true;
    } catch (e) {
      this.fallo(e);
      return false;
    } finally {
      this.guardando = false;
      this.pintarEstado();
    }
  }

  private async recargar(): Promise<void> {
    try {
      const b = await api.borrador(this.sesion);
      this.doc = b.doc;
      this.rev = b.rev;
      this.version = b.version;
      this.hist = [JSON.stringify(b.doc)];
      this.hi = 0;
      this.sucio = false;
      this.conflicto = false;
      this.glbMostrado = null;
      this.aviso.hidden = true;
      this.reajustarSeleccion();
      this.dibujar();
    } catch (e) {
      this.fallo(e);
    }
  }

  private async publicar(): Promise<void> {
    if (this.conflicto) return;
    const ok = await dialogo(this.raiz, "Publicar el mundo", "Todos los jugadores verán este mundo en cuanto lo publiques. ¿Publicar?", "Publicar");
    if (ok === null) return;
    // Sin borrador guardado nunca no hay nada que publicar: se guarda aunque no haya cambios.
    if ((this.sucio || this.rev === 0) && !(await this.guardar())) return;
    try {
      this.version = (await api.publicar(this.sesion, this.rev)).version;
      this.avisar(`Mundo publicado: versión ${this.version}.`, undefined, false);
    } catch (e) {
      this.fallo(e);
    }
    this.pintarEstado();
  }

  private async verVersiones(): Promise<void> {
    let lista: Version[];
    try {
      lista = (await api.versiones(this.sesion)).versiones;
    } catch (e) {
      return this.fallo(e);
    }
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

  private async volverA(version: number): Promise<void> {
    const ok = await dialogo(this.raiz, "Volver a una versión", `El borrador pasará a ser la versión ${version} y se perderán los cambios sin publicar. No se publica nada hasta que pulses Publicar.`, "Volver a esta");
    if (ok === null) return;
    try {
      this.rev = (await api.volver(this.sesion, this.rev, version)).rev;
    } catch (e) {
      return this.fallo(e);
    }
    await this.recargar();
  }

  private async cerrar(): Promise<void> {
    if (this.sucio) {
      const ok = await dialogo(this.raiz, "Cambios sin guardar", "Hay cambios sin guardar y se perderán si cierras.", "Cerrar sin guardar");
      if (ok === null) return;
    }
    clearInterval(this.timer);
    window.removeEventListener("keydown", this.alTecla, true);
    window.removeEventListener("keyup", this.alSoltarTecla, true);
    this.vista.dispose();
    this.raiz.remove();
    this.mundo?.pausar(false);
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
    if (ctrl && k === "s") {
      e.preventDefault();
      (document.activeElement as HTMLElement | null)?.blur(); // el valor a medio escribir entra antes de guardar
      void this.guardar();
      return;
    }
    if (this.esCampoDeTexto(e.target)) return;
    e.stopPropagation(); // el mundo no se entera
    if (ctrl && k === "z") this.irA(this.hi + (e.shiftKey ? 1 : -1));
    else if (ctrl && k === "y") this.rehacer();
    else if (ctrl && k === "d") this.duplicarPieza();
    else if (!ctrl && k === "delete") this.borrarPieza();
    else if (!ctrl && k === "f") this.vista.enfocar(this.piezaSel);
    else if (!ctrl && MODOS.some(([, , tecla]) => tecla === k)) this.cambiarModo(MODOS.find(([, , tecla]) => tecla === k)![0]);
    else return;
    e.preventDefault();
  };

  private readonly alSoltarTecla = (e: KeyboardEvent): void => {
    if (!this.esCampoDeTexto(e.target)) e.stopPropagation();
  };
}
