// Modo «Mundo» del editor: el editor de niveles. Se pinta dentro de la propia isla, con el dibujante y la escena del mundo (en pausa) y su
// propia cámara, y solo cuando algo cambia. El objeto elegido sale de las obras y se pinta suelto bajo el gizmo; al cambiar la selección las
// obras se rehacen con el borrador (con retardo, nunca en cada movimiento del ratón). También mueve las zonas de los logros.
import {
  Box3,
  Box3Helper,
  BufferGeometry,
  Group,
  LineBasicMaterial,
  LineLoop,
  Matrix4,
  MOUSE,
  Mesh,
  MeshLambertMaterial,
  Object3D,
  PerspectiveCamera,
  Raycaster,
  Ray,
  Vector2,
  Vector3,
  type WebGLRenderer,
} from "three";
import { MapControls } from "three/addons/controls/MapControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import type { Mundo } from "../mundo";
import { zonasDeSerie } from "../mundo/lugares";
import { matriz } from "../mundo/obras";
import { PLAZA, sueloEn } from "../mundo/terreno";
import { cargarGlb, geometriaAsset, type MundoDoc, type Objeto, type Vec3 } from "./formato";
import { h } from "./dom";
import type { Anfitrion, Panel } from "./panel";

type ModoGizmo = "translate" | "rotate" | "scale";
type Seleccion = { tipo: "objeto"; i: number } | { tipo: "zona"; clave: string } | null;

const GRADOS = Math.PI / 180;
/** Presupuesto de la Biblia («Jovaniverso: rendimiento imperativo») en la peor vista. */
const MAX_LLAMADAS = 120;
const MAX_TRIANGULOS = 250_000;
// Los mismos topes que el servidor (`universo_editor.py`).
const MAX_OBJETOS = 20_000;
const MITAD = 260;
const MIN_Y = -20;
const MAX_Y = 120;
const MIN_ESC = 0.02;
const MAX_ESC = 60;
const MIN_TINTE = 0.3;
const MAX_TINTE = 2;
const MAX_RADIO = 60;
/** Lo que se espera a rehacer las obras tras un cambio de selección, por si llegan varios seguidos. */
const ESPERA_MS = 150;
const MODOS: [ModoGizmo, string, string][] = [
  ["translate", "Mover (W)", "w"],
  ["rotate", "Girar (E)", "e"],
  ["scale", "Escalar (R)", "r"],
];
const NOMBRES: Record<string, string> = { nublo: "Roque Nublo", dunas: "Dunas", faro: "Faro", plaza: "Plaza", drago: "Drago", canteras: "Canteras" };
const nombreZona = (clave: string): string => NOMBRES[clave] ?? clave;

const redondear = (v: number, d = 1e4): number => Math.round(v * d) / d;
const limitar = (v: number, min: number, max: number): number => Math.min(Math.max(v, min), max);
const numero = (n: number): string => String(Math.round(n * 1000) / 1000);

export class PanelMundo implements Panel {
  readonly elementos: HTMLElement[];
  private readonly ctx: Mundo["contexto"];
  private readonly renderer: WebGLRenderer;
  private readonly camara = new PerspectiveCamera(55, 1, 0.3, 1600);
  private readonly controles: MapControls;
  private readonly transform: TransformControls;
  private readonly contenedor = h("div", { className: "ed-vista" });
  private readonly etiquetas = h("div", { className: "ed-etiquetas" });
  private readonly padre: HTMLElement | null;
  private readonly anillos = new Group();
  private readonly ancla = new Object3D();
  private readonly lineaZona = new LineBasicMaterial({ color: 0xffd86b, depthTest: false, fog: false });
  private readonly lineaZonaSel = new LineBasicMaterial({ color: 0x6bffd8, depthTest: false, fog: false });
  private readonly observador: ResizeObserver;
  private readonly inicio = new Vector2();
  private readonly rayoPantalla = new Raycaster();

  // Lo elegido y lo que se ve de ello.
  private sel: Seleccion = null;
  private visual: Group | null = null;
  private geoVisual: BufferGeometry | null = null;
  private matVisual: MeshLambertMaterial | null = null;
  private cajaVisual: Box3Helper | null = null;
  private colocando: string | null = null;
  private modoGizmo: ModoGizmo = "translate";
  private readonly cajas = new Map<string, Box3 | null>();
  private etiquetasZona = new Map<string, HTMLButtonElement>();

  // Pintado.
  private pendiente = 0;
  private espera = 0;
  private activo = false;
  private cerrada = false;
  private llamadas = 0;
  private triangulos = 0;
  private ultimaCarga: number | null = null;

  // Pantalla.
  private readonly filtro = h("input", { type: "search", placeholder: "Filtrar assets", className: "ed-filtro" });
  private readonly listaAssets = h("div", { className: "ed-lista" });
  private readonly irA = h("div", { className: "ed-fila ed-envuelve" });
  private readonly verZonas = h("input", { type: "checkbox" });
  private readonly ajuste = h("input", { type: "checkbox" });
  private readonly botones = new Map<string, HTMLButtonElement>();
  private readonly botonesModo = new Map<ModoGizmo, HTMLButtonElement>();
  private readonly selAsset = h("select");
  private readonly campos: { campo: "pos" | "rot" | "esc"; i: number; input: HTMLInputElement }[] = [];
  private readonly tinte = h("input", { type: "number", step: "0.05", min: String(MIN_TINTE), max: String(MAX_TINTE), placeholder: "sin tinte" });
  private readonly propsObjeto = h("div", { className: "ed-props" });
  private readonly propsZona = h("div", { className: "ed-props" });
  private readonly tituloZona = h("h2");
  private readonly camposZona: { campo: "x" | "z" | "r"; input: HTMLInputElement }[] = [];
  private readonly vacio = h("p", { className: "ed-vacio" });

  constructor(
    private readonly a: Anfitrion,
    private readonly mundo: Mundo,
  ) {
    this.ctx = mundo.contexto;
    this.renderer = this.ctx.renderer;
    this.padre = this.renderer.domElement.parentElement;

    this.elementos = [this.izquierda(), this.centro(), this.derecha()];
    this.contenedor.append(this.etiquetas);
    this.ancla.rotation.order = "YXZ";

    // La cámara empieza encima del jugador, mirando a la plaza.
    const yo = this.ctx.yo;
    this.camara.position.set(yo.x, yo.y + 40, yo.z + 0.5);
    this.controles = new MapControls(this.camara, this.contenedor);
    this.controles.target.set(PLAZA.x, PLAZA.alt, PLAZA.z);
    this.controles.mouseButtons = { LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN };
    this.controles.minDistance = 2;
    this.controles.maxDistance = 300;
    this.controles.maxPolarAngle = 85 * GRADOS;
    this.controles.addEventListener("change", () => this.pedir());
    this.controles.update();

    this.transform = new TransformControls(this.camara, this.contenedor);
    this.transform.addEventListener("change", () => this.pedir());
    this.transform.addEventListener("dragging-changed", (e) => {
      this.controles.enabled = !e.value;
      if (!e.value) this.alSoltar();
    });
    this.transform.addEventListener("objectChange", () => this.alMover());
    const escena = this.ctx.escena;
    escena.add(this.transform.getHelper(), this.anillos, this.ancla);
    this.anillos.visible = false;

    this.contenedor.addEventListener("pointerdown", this.alBajar);
    this.contenedor.addEventListener("pointerup", this.alSubir);
    this.observador = new ResizeObserver(() => this.ajustar());
    this.observador.observe(this.contenedor);
  }

  // ---------------------------------------------------------------- pantalla

  private boton(clave: string, texto: string, fn: () => void, titulo = ""): HTMLButtonElement {
    const b = h("button", { className: "ed-b", textContent: texto, title: titulo, onclick: fn });
    this.botones.set(clave, b);
    return b;
  }

  private izquierda(): HTMLElement {
    this.filtro.addEventListener("input", () => this.pintarAssets());
    return h(
      "aside",
      { className: "ed-panel ed-izq" },
      h("h2", { textContent: "Colocar" }),
      this.filtro,
      h("p", { className: "ed-vacio", textContent: "Elige un asset y haz clic en el terreno; Escape para parar." }),
      this.listaAssets,
      h("h2", { textContent: "Ir a" }),
      this.irA,
    );
  }

  private centro(): HTMLElement {
    const modos = MODOS.map(([m, texto]) => {
      const b = h("button", { className: "ed-b", textContent: texto, onclick: () => this.cambiarModo(m) });
      this.botonesModo.set(m, b);
      return b;
    });
    this.ajuste.onchange = () => this.aplicarAjuste();
    this.verZonas.onchange = () => this.alVerZonas();
    return h(
      "main",
      { className: "ed-centro" },
      h(
        "div",
        { className: "ed-barra" },
        ...modos,
        h("label", { className: "ed-etiqueta", title: "0,5 m, 15° y 0,1 de escala" }, this.ajuste, " Ajuste"),
        h("span", { className: "ed-sep" }),
        this.boton("deshacer", "Deshacer", () => this.a.b.irA(this.a.b.hi - 1), "Ctrl+Z"),
        this.boton("rehacer", "Rehacer", () => this.a.b.irA(this.a.b.hi + 1), "Ctrl+Y"),
        this.boton("enfocar", "Enfocar", () => this.enfocar(), "F"),
        h("span", { className: "ed-sep" }),
        h("label", { className: "ed-etiqueta" }, this.verZonas, " Ver zonas"),
      ),
      this.contenedor,
    );
  }

  private derecha(): HTMLElement {
    const fila = (titulo: string, campo: "pos" | "rot" | "esc", paso: string): HTMLElement => {
      const inputs = [0, 1, 2].map((i) => {
        const input = h("input", { type: "number", step: paso });
        input.onchange = () => this.editarCampo(campo, i, input);
        this.campos.push({ campo, i, input });
        return input;
      });
      return h("label", { className: "ed-vec" }, h("span", { textContent: titulo }), ...inputs);
    };
    this.selAsset.onchange = () => this.cambiarAsset(this.selAsset.value);
    this.tinte.onchange = () => this.editarTinte();
    this.propsObjeto.append(
      h("label", { className: "ed-vec" }, h("span", { textContent: "Asset" }), this.selAsset),
      fila("Posición", "pos", "0.1"),
      fila("Giro (°)", "rot", "15"),
      fila("Escala", "esc", "0.1"),
      h("label", { className: "ed-vec" }, h("span", { textContent: "Tinte" }), this.tinte),
      h(
        "div",
        { className: "ed-fila ed-envuelve" },
        this.boton("suelo", "Pegar al suelo", () => this.pegarAlSuelo()),
        this.boton("duplicar", "Duplicar", () => this.duplicar(), "Ctrl+D"),
        this.boton("borrar", "Borrar", () => this.borrar(), "Supr"),
        this.boton("editar", "Editar este asset", () => this.editarAsset()),
      ),
    );
    for (const [campo, titulo, paso] of [["x", "x", "0.5"], ["z", "z", "0.5"], ["r", "Radio (m)", "1"]] as const) {
      const input = h("input", { type: "number", step: paso });
      input.onchange = () => this.editarZona(campo, input);
      this.camposZona.push({ campo, input });
      this.propsZona.append(h("label", { className: "ed-vec" }, h("span", { textContent: titulo }), input));
    }
    this.propsZona.prepend(h("p", { className: "ed-vacio", textContent: "Una zona se mueve en x y z; no se puede borrar ni crear." }));
    return h("aside", { className: "ed-panel ed-der" }, this.tituloZona, this.vacio, this.propsObjeto, this.propsZona);
  }

  private get doc(): MundoDoc {
    return this.a.b.doc;
  }

  private llenarAssets(): void {
    const ids = Object.entries(this.doc.assets).sort((x, y) => x[1].nombre.localeCompare(y[1].nombre, "es"));
    this.selAsset.replaceChildren(...ids.map(([id, a]) => h("option", { value: id, textContent: `${a.nombre} (${id})` })));
    this.pintarAssets();
    const zonas = { ...zonasDeSerie(), ...this.doc.zonas };
    this.irA.replaceChildren(
      ...Object.entries(zonas).map(([clave, z]) => h("button", { className: "ed-b", textContent: nombreZona(clave), onclick: () => this.mirar(z.x, z.z) })),
      h("button", { className: "ed-b", textContent: "Jugador", onclick: () => this.mirar(this.ctx.yo.x, this.ctx.yo.z) }),
    );
  }

  private pintarAssets(): void {
    const q = this.filtro.value.trim().toLowerCase();
    const items = Object.entries(this.doc.assets)
      .filter(([id, a]) => !q || a.nombre.toLowerCase().includes(q) || id.includes(q))
      .map(([id, a]) => {
        const b = h("button", { className: "ed-item", onclick: () => this.colocar(this.colocando === id ? null : id) }, a.nombre, h("small", { textContent: ` ${id}` }));
        b.setAttribute("aria-pressed", String(id === this.colocando));
        return b;
      });
    this.listaAssets.replaceChildren(...(items.length ? items : [h("p", { className: "ed-vacio", textContent: "No hay assets." })]));
  }

  private pintarProps(): void {
    const s = this.sel;
    this.propsObjeto.hidden = s?.tipo !== "objeto";
    this.propsZona.hidden = s?.tipo !== "zona";
    this.tituloZona.hidden = s?.tipo !== "zona";
    this.vacio.hidden = !!s;
    this.vacio.textContent = "Haz clic en un objeto del mundo para elegirlo.";
    const libre = (input: HTMLElement): boolean => document.activeElement !== input;
    if (s?.tipo === "objeto") {
      const o = this.doc.objetos[s.i];
      if (!o) return;
      for (const { campo, i, input } of this.campos) if (libre(input)) input.value = numero(o[campo][i] / (campo === "rot" ? GRADOS : 1));
      if (libre(this.selAsset)) this.selAsset.value = o.asset;
      if (libre(this.tinte)) this.tinte.value = o.tinte === undefined ? "" : numero(o.tinte);
    } else if (s?.tipo === "zona") {
      const z = this.doc.zonas[s.clave];
      if (!z) return;
      this.tituloZona.textContent = `Zona: ${nombreZona(s.clave)}`;
      for (const { campo, input } of this.camposZona) if (libre(input)) input.value = numero(z[campo]);
    }
  }

  estado(): string[] {
    const sobra = (n: number, tope: number): string => (n > tope ? "⚠ " : "");
    const partes = [
      `${this.doc.objetos.length} objetos`,
      `${sobra(this.llamadas, MAX_LLAMADAS)}${this.llamadas} / ${MAX_LLAMADAS} llamadas`,
      `${sobra(this.triangulos, MAX_TRIANGULOS)}${this.triangulos.toLocaleString("es-ES")} / ${MAX_TRIANGULOS.toLocaleString("es-ES")} triángulos`,
    ];
    if (this.ultimaCarga !== null) partes.push(`Mundo rehecho en ${Math.round(this.ultimaCarga)} ms`);
    if (this.colocando) partes.push(`Colocando: ${this.doc.assets[this.colocando]?.nombre ?? this.colocando}`);
    const objeto = this.sel?.tipo === "objeto";
    for (const c of ["suelo", "duplicar", "borrar", "editar"]) this.botones.get(c)!.disabled = !objeto;
    this.botones.get("deshacer")!.disabled = this.a.b.hi <= 0;
    this.botones.get("rehacer")!.disabled = this.a.b.hi >= this.a.b.hist.length - 1;
    for (const [m, b] of this.botonesModo) {
      b.setAttribute("aria-pressed", String(m === this.modoGizmo));
      b.disabled = !this.sel || (this.sel.tipo === "zona" && m !== "translate");
    }
    return partes;
  }

  // ---------------------------------------------------------------- ciclo de vida

  activar(): void {
    this.activo = true;
    const lienzo = this.renderer.domElement;
    // El canvas del mundo pasa a la vista del editor; sin eventos, que los reciben los controles de aquí.
    lienzo.style.pointerEvents = "none";
    this.contenedor.prepend(lienzo);
    this.controles.enabled = true;
    this.alCambiarDoc();
    this.ajustar();
  }

  desactivar(): void {
    this.activo = false;
    cancelAnimationFrame(this.pendiente);
    this.pendiente = 0;
  }

  /** El borrador ha cambiado fuera de este modo (otro modo, deshacer, recargar): se empieza sin selección y con las obras al día. */
  alCambiarDoc(): void {
    this.cajas.clear();
    if (this.colocando && !this.doc.assets[this.colocando]) this.colocando = null;
    this.elegirYa(null);
    this.llenarAssets();
    this.pintarAnillos();
    this.cargarObras();
    this.pintarProps();
    this.pedir();
    this.a.estado();
  }

  dispose(): void {
    this.cerrada = true;
    cancelAnimationFrame(this.pendiente);
    clearTimeout(this.espera);
    this.observador.disconnect();
    this.contenedor.removeEventListener("pointerdown", this.alBajar);
    this.contenedor.removeEventListener("pointerup", this.alSubir);
    this.transform.detach();
    const escena = this.ctx.escena;
    escena.remove(this.transform.getHelper(), this.anillos, this.ancla);
    this.transform.dispose();
    this.controles.dispose();
    this.quitarVisual();
    this.vaciarAnillos();
    this.lineaZona.dispose();
    this.lineaZonaSel.dispose();
    // El canvas vuelve al mundo; el tamaño lo recupera el evento `resize` que manda el editor al cerrar.
    const lienzo = this.renderer.domElement;
    lienzo.style.pointerEvents = "";
    this.padre?.append(lienzo);
  }

  private ajustar(): void {
    const { clientWidth: w, clientHeight: alto } = this.contenedor;
    if (!w || !alto || !this.activo) return;
    this.renderer.setSize(w, alto);
    this.camara.aspect = w / alto;
    this.camara.updateProjectionMatrix();
    this.pedir();
  }

  // ---------------------------------------------------------------- pintado

  /** Pide un fotograma; varias peticiones seguidas pintan una sola vez. */
  private pedir(): void {
    if (this.pendiente || this.cerrada || !this.activo) return;
    this.pendiente = requestAnimationFrame(() => {
      this.pendiente = 0;
      this.pintar();
    });
  }

  private pintar(): void {
    this.ctx.culling.actualizar(this.camara);
    this.renderer.render(this.ctx.escena, this.camara);
    const r = this.renderer.info.render;
    if (r.calls !== this.llamadas || r.triangles !== this.triangulos) {
      this.llamadas = r.calls;
      this.triangulos = r.triangles;
      this.a.estado();
    }
    this.colocarEtiquetas();
  }

  private colocarEtiquetas(): void {
    if (!this.verZonas.checked) return;
    const { clientWidth: w, clientHeight: alto } = this.contenedor;
    const v = new Vector3();
    for (const [clave, b] of this.etiquetasZona) {
      const z = this.doc.zonas[clave];
      if (!z) continue;
      v.set(z.x, sueloEn(z.x, z.z) + 3, z.z).project(this.camara);
      b.hidden = v.z > 1;
      b.style.transform = `translate(${((v.x + 1) / 2) * w}px, ${((1 - v.y) / 2) * alto}px) translate(-50%, -50%)`;
    }
  }

  /** Rehace las obras del mundo con el borrador, sin el objeto elegido (ese se pinta suelto). */
  private cargarObras(): void {
    clearTimeout(this.espera);
    const t = performance.now();
    const i = this.sel?.tipo === "objeto" ? this.sel.i : -1;
    const doc = this.doc;
    this.mundo.cargarMundo(i < 0 ? doc : { ...doc, objetos: doc.objetos.filter((_, n) => n !== i) });
    this.ultimaCarga = performance.now() - t;
    this.pedir();
    this.a.estado();
  }

  /** Lo mismo, pero con retardo: varios cambios seguidos rehacen el mundo una sola vez. */
  private reconstruir(): void {
    clearTimeout(this.espera);
    this.espera = window.setTimeout(() => !this.cerrada && this.cargarObras(), ESPERA_MS);
  }

  // ---------------------------------------------------------------- selección

  private elegir(s: Seleccion): void {
    const antes = this.sel;
    this.elegirYa(s);
    if (antes?.tipo === "objeto" || s?.tipo === "objeto") this.reconstruir();
    this.pintarAnillos();
    this.pintarProps();
    this.a.estado();
    this.pedir();
  }

  /** Cambia la selección y su pintado, sin tocar las obras. */
  private elegirYa(s: Seleccion): void {
    this.sel = s;
    this.quitarVisual();
    this.transform.detach();
    if (s?.tipo === "objeto") {
      this.mostrarObjeto(s.i);
      this.transform.setMode(this.modoGizmo);
      this.transform.showY = true;
    } else if (s?.tipo === "zona") {
      const z = this.doc.zonas[s.clave];
      this.ancla.position.set(z.x, sueloEn(z.x, z.z), z.z);
      this.transform.setMode("translate");
      this.transform.showY = false;
      this.transform.attach(this.ancla);
    }
  }

  /** La caja envolvente del asset (en sus coordenadas); `null` mientras un GLB aún se carga. */
  private caja(id: string): Box3 | null {
    const guardada = this.cajas.get(id);
    if (guardada !== undefined) return guardada;
    const a = this.doc.assets[id];
    if (!a) return null;
    if (a.tipo === "piezas") {
      const g = geometriaAsset(a);
      g.computeBoundingBox();
      const c = g.boundingBox && !g.boundingBox.isEmpty() ? g.boundingBox.clone() : null;
      g.dispose();
      this.cajas.set(id, c);
      return c;
    }
    this.cajas.set(id, null);
    const sha = a.glb;
    cargarGlb(sha).then(
      (modelo) => {
        const aa = this.doc.assets[id];
        if (aa?.tipo !== "glb" || aa.glb !== sha) return;
        modelo.updateMatrixWorld(true);
        this.cajas.set(id, new Box3().setFromObject(modelo));
      },
      () => {},
    );
    return null;
  }

  private mostrarObjeto(i: number): void {
    const o = this.doc.objetos[i];
    const asset = o && this.doc.assets[o.asset];
    if (!o || !asset) return;
    const grupo = new Group();
    grupo.rotation.order = "YXZ";
    this.visual = grupo;
    if (asset.tipo === "piezas") {
      this.geoVisual = geometriaAsset(asset);
      this.matVisual = new MeshLambertMaterial({ vertexColors: true, flatShading: true });
      this.matVisual.color.setScalar(o.tinte ?? 1);
      const m = new Mesh(this.geoVisual, this.matVisual);
      m.castShadow = true;
      m.receiveShadow = true;
      grupo.add(m);
      this.ponerCaja(grupo, this.caja(o.asset));
    } else {
      const id = o.asset;
      cargarGlb(asset.glb).then(
        (modelo) => {
          if (this.visual !== grupo) return;
          const c = modelo.clone(true);
          c.traverse((n) => ((n as Mesh).isMesh && ((n as Mesh).castShadow = (n as Mesh).receiveShadow = true)));
          grupo.add(c);
          modelo.updateMatrixWorld(true);
          this.ponerCaja(grupo, new Box3().setFromObject(modelo));
          this.cajas.set(id, new Box3().setFromObject(modelo));
          this.pedir();
        },
        () => this.a.aviso("No se ha podido cargar el modelo GLB."),
      );
    }
    this.aplicar(o);
    this.ctx.escena.add(grupo);
    this.transform.attach(grupo);
  }

  private ponerCaja(grupo: Group, caja: Box3 | null): void {
    if (!caja) return;
    this.cajaVisual = new Box3Helper(caja, 0x6bffd8);
    const m = this.cajaVisual.material as LineBasicMaterial;
    m.fog = false;
    m.depthTest = false;
    grupo.add(this.cajaVisual);
  }

  private quitarVisual(): void {
    if (this.visual) this.ctx.escena.remove(this.visual);
    this.visual = null;
    this.geoVisual?.dispose();
    this.matVisual?.dispose();
    this.geoVisual = this.matVisual = null;
    if (this.cajaVisual) {
      this.cajaVisual.geometry.dispose();
      (this.cajaVisual.material as LineBasicMaterial).dispose();
      this.cajaVisual = null;
    }
  }

  /** Pone el objeto suelto tal como está en el documento. */
  private aplicar(o: Objeto): void {
    const v = this.visual;
    if (!v) return;
    v.position.set(...o.pos);
    v.rotation.set(o.rot[0], o.rot[1], o.rot[2], "YXZ");
    v.scale.set(...o.esc);
  }

  private get objetoSel(): Objeto | null {
    return this.sel?.tipo === "objeto" ? (this.doc.objetos[this.sel.i] ?? null) : null;
  }

  /** El gizmo se está moviendo: el documento sigue al objeto suelto (sin historial hasta soltarlo). */
  private alMover(): void {
    const s = this.sel;
    if (s?.tipo === "objeto" && this.visual) {
      const o = this.objetoSel;
      if (!o) return;
      const v = this.visual;
      o.pos = [redondear(v.position.x), redondear(v.position.y), redondear(v.position.z)];
      o.rot = [redondear(v.rotation.x), redondear(v.rotation.y), redondear(v.rotation.z)];
      o.esc = [redondear(v.scale.x), redondear(v.scale.y), redondear(v.scale.z)];
    } else if (s?.tipo === "zona") {
      const z = this.doc.zonas[s.clave];
      z.x = limitar(redondear(this.ancla.position.x, 100), -MITAD, MITAD);
      z.z = limitar(redondear(this.ancla.position.z, 100), -MITAD, MITAD);
      this.ancla.position.y = sueloEn(z.x, z.z); // la altura no se toca
      this.pintarAnillos();
    }
    this.pintarProps();
  }

  private alSoltar(): void {
    if (this.sel) this.commit();
  }

  private commit(): void {
    this.a.b.commit();
    this.pintarProps();
    this.a.estado();
    this.pedir();
  }

  // ---------------------------------------------------------------- edición

  private editarCampo(campo: "pos" | "rot" | "esc", i: number, input: HTMLInputElement): void {
    const o = this.objetoSel;
    const v = parseFloat(input.value);
    if (!o || Number.isNaN(v)) return this.pintarProps();
    const [min, max, k] = campo === "pos" ? (i === 1 ? [MIN_Y, MAX_Y, 1] : [-MITAD, MITAD, 1]) : campo === "esc" ? [MIN_ESC, MAX_ESC, 1] : [-720, 720, GRADOS];
    o[campo][i] = limitar(v, min, max) * k;
    this.aplicar(o);
    this.commit();
  }

  private editarTinte(): void {
    const o = this.objetoSel;
    if (!o) return;
    const v = parseFloat(this.tinte.value);
    if (Number.isNaN(v)) delete o.tinte;
    else o.tinte = limitar(v, MIN_TINTE, MAX_TINTE);
    this.matVisual?.color.setScalar(o.tinte ?? 1);
    this.commit();
  }

  private cambiarAsset(id: string): void {
    const o = this.objetoSel;
    if (!o || !this.doc.assets[id] || this.sel?.tipo !== "objeto") return;
    o.asset = id;
    this.elegirYa({ tipo: "objeto", i: this.sel.i });
    this.commit();
  }

  private editarZona(campo: "x" | "z" | "r", input: HTMLInputElement): void {
    const z = this.sel?.tipo === "zona" ? this.doc.zonas[this.sel.clave] : null;
    const v = parseFloat(input.value);
    if (!z || Number.isNaN(v)) return this.pintarProps();
    z[campo] = campo === "r" ? limitar(v, 1, MAX_RADIO) : limitar(v, -MITAD, MITAD);
    this.ancla.position.set(z.x, sueloEn(z.x, z.z), z.z);
    this.pintarAnillos();
    this.commit();
  }

  private pegarAlSuelo(): void {
    const o = this.objetoSel;
    if (!o) return;
    o.pos[1] = redondear(limitar(sueloEn(o.pos[0], o.pos[2]), MIN_Y, MAX_Y));
    this.aplicar(o);
    this.commit();
  }

  private duplicar(): boolean {
    const o = this.objetoSel;
    if (!o) return false;
    if (this.doc.objetos.length >= MAX_OBJETOS) {
      this.a.aviso(`El mundo puede tener ${MAX_OBJETOS} objetos como mucho.`);
      return true;
    }
    const copia = structuredClone(o);
    copia.pos[0] = limitar(copia.pos[0] + 1, -MITAD, MITAD);
    this.doc.objetos.push(copia);
    this.elegir({ tipo: "objeto", i: this.doc.objetos.length - 1 });
    this.commit();
    return true;
  }

  private borrar(): boolean {
    const s = this.sel;
    if (s?.tipo !== "objeto") return false;
    this.elegir(null);
    this.doc.objetos.splice(s.i, 1);
    this.commit();
    return true;
  }

  private editarAsset(): void {
    const o = this.objetoSel;
    if (o) this.a.editarAsset(o.asset);
  }

  private colocar(id: string | null): void {
    this.colocando = id;
    this.contenedor.style.cursor = id ? "crosshair" : "";
    this.pintarAssets();
    this.a.estado();
  }

  private ponerEn(rayo: Ray): void {
    const id = this.colocando;
    const p = id ? this.suelo(rayo) : null;
    if (!id || !p) return;
    if (Math.abs(p.x) > MITAD || Math.abs(p.z) > MITAD) return this.a.aviso("Ahí queda fuera del mundo.");
    if (this.doc.objetos.length >= MAX_OBJETOS) return this.a.aviso(`El mundo puede tener ${MAX_OBJETOS} objetos como mucho.`);
    const pos: Vec3 = [redondear(p.x, 100), redondear(sueloEn(p.x, p.z)), redondear(p.z, 100)];
    this.doc.objetos.push({ asset: id, pos, rot: [0, 0, 0], esc: [1, 1, 1] });
    this.elegir({ tipo: "objeto", i: this.doc.objetos.length - 1 });
    this.commit();
  }

  // ---------------------------------------------------------------- zonas

  private alVerZonas(): void {
    this.anillos.visible = this.verZonas.checked;
    if (!this.verZonas.checked && this.sel?.tipo === "zona") this.elegir(null);
    this.pintarAnillos();
    this.pedir();
  }

  private vaciarAnillos(): void {
    for (const a of this.anillos.children) (a as LineLoop).geometry.dispose();
    this.anillos.clear();
    for (const b of this.etiquetasZona.values()) b.remove();
    this.etiquetasZona.clear();
  }

  /** Un anillo en el suelo por cada zona y un botón con su nombre. */
  private pintarAnillos(): void {
    this.vaciarAnillos();
    if (!this.verZonas.checked) return;
    for (const [clave, z] of Object.entries(this.doc.zonas)) {
      const puntos = Array.from({ length: 64 }, (_, i) => {
        const x = z.x + Math.cos((i / 64) * 2 * Math.PI) * z.r;
        const zz = z.z + Math.sin((i / 64) * 2 * Math.PI) * z.r;
        return new Vector3(x, sueloEn(x, zz) + 0.6, zz);
      });
      const sel = this.sel?.tipo === "zona" && this.sel.clave === clave;
      const anillo = new LineLoop(new BufferGeometry().setFromPoints(puntos), sel ? this.lineaZonaSel : this.lineaZona);
      anillo.renderOrder = 10;
      anillo.frustumCulled = false;
      this.anillos.add(anillo);
      const b = h("button", { className: "ed-etiqueta-zona", textContent: nombreZona(clave), onclick: () => this.elegir({ tipo: "zona", clave }) });
      b.setAttribute("aria-pressed", String(sel));
      // Sin esto los controles de la cámara capturan el puntero y el clic no llega al botón.
      b.addEventListener("pointerdown", (e) => e.stopPropagation());
      this.etiquetas.append(b);
      this.etiquetasZona.set(clave, b);
    }
  }

  // ---------------------------------------------------------------- ratón

  private rayo(e: PointerEvent): Ray {
    const r = this.contenedor.getBoundingClientRect();
    this.rayoPantalla.setFromCamera(new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camara);
    return this.rayoPantalla.ray;
  }

  /** Primer punto del terreno bajo el rayo (avanza de metro en metro y afina). */
  private suelo(rayo: Ray): Vector3 | null {
    const p = new Vector3();
    const bajo = (t: number): boolean => {
      rayo.at(t, p);
      return p.y < sueloEn(p.x, p.z);
    };
    if (bajo(0)) return null;
    let t = 1;
    while (t <= 1500 && !bajo(t)) t++;
    if (t > 1500) return null;
    let [a, b] = [t - 1, t];
    for (let n = 0; n < 24; n++) {
      const m = (a + b) / 2;
      if (bajo(m)) b = m;
      else a = m;
    }
    return rayo.at(b, new Vector3());
  }

  /** El objeto más cercano cuya caja envolvente corta el rayo; -1 si ninguno. */
  private objetoEn(rayo: Ray): number {
    const m = new Matrix4();
    const inv = new Matrix4();
    const local = new Ray();
    const golpe = new Vector3();
    let mejor = -1;
    let dist = Infinity;
    this.doc.objetos.forEach((o, i) => {
      const caja = this.caja(o.asset);
      if (!caja) return;
      m.copy(matriz(o));
      inv.copy(m).invert();
      // El rayo en el espacio del objeto, con la dirección sin normalizar para que la escala no cambie la distancia.
      local.origin.copy(rayo.origin).applyMatrix4(inv);
      local.direction.copy(rayo.origin).add(rayo.direction).applyMatrix4(inv).sub(local.origin);
      if (!local.intersectBox(caja, golpe)) return;
      const d = golpe.applyMatrix4(m).distanceTo(rayo.origin);
      if (d < dist) [dist, mejor] = [d, i];
    });
    return mejor;
  }

  /** La zona cuyo anillo está bajo el rayo; `null` si ninguna. */
  private zonaEn(rayo: Ray): string | null {
    const p = this.suelo(rayo);
    if (!p) return null;
    const margen = Math.max(1.5, this.camara.position.distanceTo(p) * 0.02);
    let mejor: string | null = null;
    let dist = margen;
    for (const [clave, z] of Object.entries(this.doc.zonas)) {
      const d = Math.abs(Math.hypot(p.x - z.x, p.z - z.z) - z.r);
      if (d < dist) [dist, mejor] = [d, clave];
    }
    return mejor;
  }

  // Un clic es pulsar y soltar sin moverse y sin haber cogido el gizmo.
  private readonly alBajar = (e: PointerEvent): void => {
    this.inicio.set(e.clientX, e.clientY);
  };

  private readonly alSubir = (e: PointerEvent): void => {
    if (e.button !== 0 || e.target !== this.contenedor || this.inicio.distanceTo(new Vector2(e.clientX, e.clientY)) > 4 || this.transform.axis) return;
    const rayo = this.rayo(e).clone();
    if (this.colocando) return this.ponerEn(rayo);
    const zona = this.verZonas.checked ? this.zonaEn(rayo) : null;
    if (zona) return this.elegir({ tipo: "zona", clave: zona });
    const i = this.objetoEn(rayo);
    this.elegir(i >= 0 ? { tipo: "objeto", i } : null);
  };

  // ---------------------------------------------------------------- cámara y teclado

  private mirar(x: number, z: number): void {
    const dir = this.camara.position.clone().sub(this.controles.target).setLength(60);
    this.controles.target.set(x, sueloEn(x, z), z);
    this.camara.position.copy(this.controles.target).add(dir);
    this.controles.update();
    this.pedir();
  }

  private enfocar(): void {
    const o = this.objetoSel;
    const z = this.sel?.tipo === "zona" ? this.doc.zonas[this.sel.clave] : null;
    if (o) this.mirar(o.pos[0], o.pos[2]);
    else if (z) this.mirar(z.x, z.z);
  }

  private cambiarModo(m: ModoGizmo): void {
    this.modoGizmo = m;
    if (this.sel?.tipo === "objeto") this.transform.setMode(m);
    this.a.estado();
    this.pedir();
  }

  private aplicarAjuste(): void {
    const si = this.ajuste.checked;
    this.transform.setTranslationSnap(si ? 0.5 : null);
    this.transform.setRotationSnap(si ? 15 * GRADOS : null);
    this.transform.setScaleSnap(si ? 0.1 : null);
  }

  tecla(_e: KeyboardEvent, k: string, ctrl: boolean): boolean {
    if (k === "escape") {
      if (this.colocando) this.colocar(null);
      else if (this.sel) this.elegir(null);
      else return false;
      return true;
    }
    if (ctrl && k === "d") return this.duplicar();
    if (ctrl) return false;
    if (k === "delete") return this.borrar();
    if (k === "f") return !!this.sel && (this.enfocar(), true);
    const modo = MODOS.find(([, , t]) => t === k);
    if (modo && this.sel) {
      if (this.sel.tipo === "objeto") this.cambiarModo(modo[0]);
      return true;
    }
    return false;
  }
}
