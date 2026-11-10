// Modo «Mundo» del editor: el editor de niveles. Se pinta dentro de la propia isla, con el dibujante y la escena del mundo (en pausa) y su
// propia cámara, y solo cuando algo cambia. Los objetos elegidos (hasta `TOPE_SEL`, con la multiselección de `grupo.ts`) salen de las obras
// y se pintan sueltos bajo un solo gizmo; al cambiar la selección las obras se rehacen con el borrador (con retardo, nunca en cada
// movimiento del ratón). También mueve las zonas de los logros, que se eligen de una en una, y pone los teleportes del menú (Viajar).
import {
  Box3,
  Box3Helper,
  BufferGeometry,
  Euler,
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
import { teleportesDeSerie } from "../mundo/teleportes";
import { matriz } from "../mundo/obras";
import { PRUEBAS } from "../mundo/modo";
import { LIMITE } from "../mundo/borde";
import { PLAZA, sueloEn } from "../mundo/terreno";
import { ArrastreGrupo, Recuadro, Seleccion as Conjunto } from "./grupo";
import { PincelTerreno } from "./pincel";
import { cargarGlb, geometriaAsset, MAX_NOMBRE, MAX_TELEPORTES, type MundoDoc, type Objeto, type Teleporte, type Vec3 } from "./formato";
import { h } from "./dom";
import { avisosFunciones, prepararObjeto, PropsFuncion } from "./funciones";
import type { Anfitrion, Panel } from "./panel";

type ModoGizmo = "translate" | "rotate" | "scale";

const GRADOS = Math.PI / 180;
/** Presupuesto de la Biblia («Jovaniverso: rendimiento imperativo») en la peor vista. */
const MAX_LLAMADAS = 120;
const MAX_TRIANGULOS = 250_000;
// Sin tope de objetos del mundo: el servidor pone uno por celda (`MAX_CELL_OBJECTS`) y avisa al guardar.
/** Cada objeto elegido se pinta suelto (una llamada de dibujo): no se eligen más a la vez. */
const TOPE_SEL = 300;
/** Hasta dónde se puede colocar: donde acaban las celdas del editor (`CELL_LIMIT`); el borde del jugador crece al publicar. */
const MITAD = LIMITE;
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

/** Atajos y ratón de la herramienta «Objetos», para el menú de ayuda del editor (el terreno tiene los suyos en `AYUDA_TERRENO`). */
export const AYUDA_MUNDO: [string, string][] = [
  ["Clic en un asset de la lista y clic en el terreno", "Coloca un objeto de ese asset; Escape para dejar de colocar"],
  ["Clic en un objeto", "Elige solo ese objeto"],
  ["Ctrl+clic", "Añade un objeto a la selección o lo quita"],
  ["Clic en el vacío", "Deja de elegir ningún objeto"],
  ["Mayús+arrastrar en la vista", `Dibuja una caja y elige los objetos cuyo centro queda dentro (con Ctrl, se suman). Máximo ${TOPE_SEL} a la vez: si hay más, quedan los más cercanos a la cámara`],
  ["Arrastrar con Ctrl", "Invierte el ajuste a rejilla mientras dura el arrastre del gizmo"],
  ["W / E / R", "Mover, girar o escalar con el gizmo (todos los elegidos a la vez)"],
  ["X", "Cambia el gizmo entre ejes locales y globales"],
  ["Ctrl+D", "Duplica los objetos elegidos, 1 m a un lado; las copias quedan elegidas"],
  ["Ctrl+C / Ctrl+X / Ctrl+V", "Copia, corta y pega. Se pega con el centro del grupo bajo el ratón (o 1 m a un lado), cada objeto a su altura sobre el suelo de origen"],
  ["Supr o Retroceso", "Borra los objetos elegidos"],
  ["Ctrl+A", "Elige todos los objetos del mismo asset que el último elegido, de las celdas bajadas (unos 480 m alrededor de la cámara)"],
  ["Escape", "Deja de colocar y de elegir"],
  ["F", "Encuadra lo elegido"],
  ["H", "Oculta los objetos elegidos (solo en el editor, no se guarda; se olvida al deshacer o recargar)"],
  ["Alt+H", "Enseña todos los objetos ocultos"],
  ["Flechas", "Mueven lo elegido 0,5 m en x y z (con Mayús, 5 m)"],
  ["Re Pág / Av Pág", "Suben o bajan lo elegido 0,5 m (con Mayús, 5 m)"],
  ["Alinear X / Y / Z", "Pone esa coordenada de todos igual que la del último elegido"],
  ["Pegar al suelo", "Baja o sube lo elegido hasta el terreno"],
  ["Ver zonas", "Enseña los anillos de los logros; se eligen de una en una y solo se mueven en x y z"],
  ["T", "Cambia entre las herramientas Objetos y Terreno"],
  ["Ctrl+Z / Ctrl+Y", "Deshacer y rehacer"],
];

/** Portapapeles de objetos, en memoria: vale mientras el editor siga abierto. `sobre` es la altura sobre el suelo de origen. */
let portapapeles: { objeto: Objeto; sobre: number }[] = [];

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
  private readonly sel = new Conjunto();
  private zona: string | null = null;
  /** Los elegidos que se sacaron de las obras la última vez que se rehicieron (para saber si hay que rehacerlas). */
  private sacados = "";
  private readonly visuales = new Map<number, Group>();
  private readonly geos = new Map<string, BufferGeometry>();
  private readonly mats = new Map<number, MeshLambertMaterial>();
  private cajasVis: Box3Helper[] = [];
  private readonly lineaPrincipal = new LineBasicMaterial({ color: 0x6bffd8, depthTest: false, fog: false });
  private readonly lineaOtro = new LineBasicMaterial({ color: 0xffb86b, depthTest: false, fog: false });
  /** Ocultos solo en el editor: no se guardan, no salen en el mundo ni se pueden elegir. */
  private readonly ocultos = new Set<Objeto>();
  /** Con varios elegidos el gizmo va a este pivote. */
  private readonly pivote = new Object3D();
  private arrastre: ArrastreGrupo | null = null;
  private local = false;
  private invertido = false;
  /** El último movimiento del ratón sobre la vista (`null` si está fuera), para pegar bajo el puntero. */
  private raton: PointerEvent | null = null;
  private readonly recuadro: Recuadro;
  private colocando: string | null = null;
  /** Colocar y mover objetos, o esculpir y pintar el terreno con el pincel. */
  private herramienta: "objetos" | "terreno" = "objetos";
  private readonly pincel: PincelTerreno;
  private readonly cajaObjetos = h("div", { className: "ed-props" });
  private readonly botonesHerramienta = new Map<"objetos" | "terreno", HTMLButtonElement>();
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
  private readonly listaTeleportes = h("div", { className: "ed-lista" });
  private readonly verZonas = h("input", { type: "checkbox" });
  private readonly ajuste = h("input", { type: "checkbox" });
  private readonly botones = new Map<string, HTMLButtonElement>();
  private readonly botonesModo = new Map<ModoGizmo, HTMLButtonElement>();
  private readonly selAsset = h("select");
  private readonly campos: { campo: "pos" | "rot" | "esc"; i: number; input: HTMLInputElement }[] = [];
  private readonly tinte = h("input", { type: "number", step: "0.05", min: String(MIN_TINTE), max: String(MAX_TINTE), placeholder: "sin tinte" });
  private readonly propsObjeto = h("div", { className: "ed-props" });
  private readonly avisoVarios = h("p", { className: "ed-vacio" });
  private readonly propsZona = h("div", { className: "ed-props" });
  private readonly tituloZona = h("h2");
  private readonly camposZona: { campo: "x" | "z" | "r"; input: HTMLInputElement }[] = [];
  private readonly vacio = h("p", { className: "ed-vacio" });
  private readonly funcion: PropsFuncion;

  constructor(
    private readonly a: Anfitrion,
    private readonly mundo: Mundo,
  ) {
    this.ctx = mundo.contexto;
    this.renderer = this.ctx.renderer;
    this.padre = this.renderer.domElement.parentElement;

    this.pincel = new PincelTerreno(a, mundo, this.ctx.escena, { pedir: () => this.pedir(), rehacerObras: () => this.cargarObras() });
    this.funcion = new PropsFuncion(a.sesion, () => this.doc, () => this.commit(), (t) => a.aviso(t));
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
      const lista = this.sel.lista;
      this.arrastre = e.value && !this.zona && lista.length > 1 ? new ArrastreGrupo(this.pivote, lista.map((i) => this.doc.objetos[i])) : null;
      if (!e.value) this.alSoltar();
    });
    this.transform.addEventListener("objectChange", () => this.alMover());
    const escena = this.ctx.escena;
    escena.add(this.transform.getHelper(), this.anillos, this.ancla, this.pivote);
    this.anillos.visible = false;

    this.contenedor.addEventListener("pointerdown", this.alBajar);
    this.contenedor.addEventListener("pointerup", this.alSubir);
    // El pincel mira el ratón antes que los controles de la cámara (fase de captura) y se queda el botón izquierdo.
    this.contenedor.addEventListener("pointerdown", this.alBajarPincel, true);
    // La caja de selección (Mayús+arrastrar) para el evento antes que los controles, salvo con el pincel, que usa Mayús.
    this.recuadro = new Recuadro(this.contenedor, (r, aditivo) => this.alCaja(r, aditivo), () => this.herramienta !== "terreno");
    window.addEventListener("pointerdown", this.alPuntero, true);
    window.addEventListener("pointermove", this.alPuntero, true);
    this.contenedor.addEventListener("pointermove", this.alMoverRaton);
    this.contenedor.addEventListener("pointerleave", this.alSalirRaton);
    this.contenedor.addEventListener("pointermove", this.alMoverPincel);
    this.contenedor.addEventListener("pointerup", this.alSoltarPincel);
    this.contenedor.addEventListener("pointerleave", this.alSalirPincel);
    this.contenedor.addEventListener("wheel", this.alRuedaPincel, { capture: true, passive: false });
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
    const herramienta = (k: "objetos" | "terreno", texto: string, titulo: string): HTMLButtonElement => {
      const b = h("button", { className: "ed-b ed-pestana", textContent: texto, title: titulo, onclick: () => this.cambiarHerramienta(k) });
      this.botonesHerramienta.set(k, b);
      return b;
    };
    const terreno = herramienta("terreno", "Terreno", PRUEBAS ? "El extraplano de pruebas no tiene terreno que esculpir" : "Esculpir y pintar la isla (T)");
    terreno.disabled = PRUEBAS;
    this.cajaObjetos.append(
      h("h2", { textContent: "Colocar" }),
      this.filtro,
      h("p", { className: "ed-vacio", textContent: "Elige un asset y haz clic en el terreno; Escape para parar." }),
      this.listaAssets,
    );
    this.pincel.panel.hidden = true;
    return h(
      "aside",
      { className: "ed-panel ed-izq" },
      h("div", { className: "ed-fila" }, herramienta("objetos", "Objetos", "Colocar y mover objetos (T)"), terreno),
      this.cajaObjetos,
      this.pincel.panel,
      h("h2", { textContent: "Ir a" }),
      this.irA,
      h("h2", { textContent: "Teleportes" }),
      h("p", { className: "ed-vacio", textContent: "Los sitios del menú «Viajar»: se aparece en el centro de la vista, a ras de suelo." }),
      this.listaTeleportes,
      h("button", { className: "ed-b", textContent: "Añadir en el centro de la vista", onclick: () => this.anadirTeleporte() }),
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
        this.boton("espacio", "Ejes: globales", () => this.cambiarEspacio(), "Ejes del gizmo locales o globales (X)"),
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
      this.avisoVarios,
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
      h(
        "div",
        { className: "ed-fila ed-envuelve" },
        this.boton("alinx", "Alinear X", () => this.alinear(0), "Pone la x de todos como la del último elegido"),
        this.boton("aliny", "Alinear Y", () => this.alinear(1), "Pone la altura de todos como la del último elegido"),
        this.boton("alinz", "Alinear Z", () => this.alinear(2), "Pone la z de todos como la del último elegido"),
      ),
      this.funcion.el,
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
    this.pintarTeleportes();
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
    const os = this.elegidos;
    const n = os.length;
    this.propsObjeto.hidden = !n;
    this.propsZona.hidden = !this.zona;
    this.tituloZona.hidden = !this.zona;
    this.vacio.hidden = !!(n || this.zona);
    this.vacio.textContent = "Haz clic en un objeto del mundo para elegirlo.";
    this.avisoVarios.hidden = n < 2;
    this.avisoVarios.textContent = `${n} objetos elegidos. Los cambios valen para todos; un campo con «—» es que no coinciden.`;
    const libre = (input: HTMLElement): boolean => document.activeElement !== input;
    this.funcion.pintar(n === 1 ? os[0] : null);
    if (n) {
      // El valor si todos lo comparten; si no, `null`.
      const comun = (f: (o: Objeto) => string): string | null => {
        const v = f(os[0]);
        return os.every((o) => f(o) === v) ? v : null;
      };
      for (const { campo, i, input } of this.campos) {
        if (!libre(input)) continue;
        const v = comun((o) => numero(o[campo][i] / (campo === "rot" ? GRADOS : 1)));
        input.value = v ?? "";
        input.placeholder = v === null ? "—" : "";
      }
      if (libre(this.selAsset)) this.selAsset.value = comun((o) => o.asset) ?? "";
      if (libre(this.tinte)) {
        const v = comun((o) => (o.tinte === undefined ? "" : numero(o.tinte)));
        this.tinte.value = v ?? "";
        this.tinte.placeholder = v === null ? "—" : "sin tinte";
      }
    } else if (this.zona) {
      const z = this.doc.zonas[this.zona];
      if (!z) return;
      this.tituloZona.textContent = `Zona: ${nombreZona(this.zona)}`;
      for (const { campo, input } of this.camposZona) if (libre(input)) input.value = numero(z[campo]);
    }
  }

  estado(): string[] {
    const sobra = (n: number, tope: number): string => (n > tope ? "⚠ " : "");
    const partes = [
      `${this.doc.objetos.length} objetos bajados`,
      `${sobra(this.llamadas, MAX_LLAMADAS)}${this.llamadas} / ${MAX_LLAMADAS} llamadas`,
      `${sobra(this.triangulos, MAX_TRIANGULOS)}${this.triangulos.toLocaleString("es-ES")} / ${MAX_TRIANGULOS.toLocaleString("es-ES")} triángulos`,
    ];
    if (this.ultimaCarga !== null) partes.push(`Mundo rehecho en ${Math.round(this.ultimaCarga)} ms`);
    const n = this.sel.lista.length;
    if (n > 1) partes.push(`${n} objetos elegidos`);
    if (this.ocultos.size) partes.push(`${this.ocultos.size} ocultos`);
    if (this.colocando) partes.push(`Colocando: ${this.doc.assets[this.colocando]?.nombre ?? this.colocando}`);
    if (this.herramienta === "terreno") partes.push(...this.pincel.estado());
    partes.push(...avisosFunciones(this.doc));
    for (const [k, b] of this.botonesHerramienta) b.setAttribute("aria-pressed", String(k === this.herramienta));
    const objeto = n > 0;
    for (const c of ["suelo", "duplicar", "borrar", "editar", "alinx", "aliny", "alinz", "espacio", "enfocar"]) this.botones.get(c)!.disabled = !objeto && !(c === "enfocar" && this.zona);
    this.botones.get("deshacer")!.disabled = this.a.b.hi <= 0;
    this.botones.get("rehacer")!.disabled = this.a.b.hi >= this.a.b.hist.length - 1;
    for (const [m, b] of this.botonesModo) {
      b.setAttribute("aria-pressed", String(m === this.modoGizmo));
      b.disabled = !objeto && (!this.zona || m !== "translate");
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
    this.pincel.terminar();
    this.activo = false;
    cancelAnimationFrame(this.pendiente);
    this.pendiente = 0;
  }

  /** Han llegado celdas: la selección sigue valiendo (sus objetos van al final), solo se rehacen las obras. */
  alLlegarCeldas(): void {
    this.reconstruir();
  }

  /** El borrador ha cambiado fuera de este modo (otro modo, deshacer, recargar): se empieza sin selección y con las obras al día. */
  alCambiarDoc(): void {
    this.cajas.clear();
    if (this.colocando && !this.doc.assets[this.colocando]) this.colocando = null;
    this.sel.vaciar();
    this.zona = null;
    this.ocultos.clear();
    this.elegirYa();
    this.llenarAssets();
    this.pincel.refrescar();
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
    this.contenedor.removeEventListener("pointerdown", this.alBajarPincel, true);
    this.recuadro.dispose();
    window.removeEventListener("pointerdown", this.alPuntero, true);
    window.removeEventListener("pointermove", this.alPuntero, true);
    this.contenedor.removeEventListener("pointermove", this.alMoverRaton);
    this.contenedor.removeEventListener("pointerleave", this.alSalirRaton);
    this.contenedor.removeEventListener("pointermove", this.alMoverPincel);
    this.contenedor.removeEventListener("pointerup", this.alSoltarPincel);
    this.contenedor.removeEventListener("pointerleave", this.alSalirPincel);
    this.contenedor.removeEventListener("wheel", this.alRuedaPincel, true);
    this.pincel.dispose();
    this.transform.detach();
    const escena = this.ctx.escena;
    escena.remove(this.transform.getHelper(), this.anillos, this.ancla, this.pivote);
    this.transform.dispose();
    this.controles.dispose();
    this.quitarVisuales();
    this.vaciarAnillos();
    this.lineaPrincipal.dispose();
    this.lineaOtro.dispose();
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
    // El mundo se carga por cercanía alrededor de la cámara del editor; mientras quede algo por montar se sigue pintando.
    const { x, z } = this.camara.position;
    if (this.mundo.enfocar(x, z)) this.pedir();
    // Y el borrador baja las celdas de alrededor; soltar las lejanas cambia `doc.objetos`, así que solo sin nada elegido ni a medias.
    const quieto = !this.sel.lista.length && !this.zona && !this.colocando && this.herramienta === "objetos";
    if (this.a.b.cerca(x, z, quieto)) this.cargarObras();
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

  /** Rehace las obras del mundo con el borrador, sin los objetos elegidos (esos se pintan sueltos) ni los ocultos. */
  private cargarObras(): void {
    clearTimeout(this.espera);
    const t = performance.now();
    const fuera = this.sel.lista;
    this.sacados = fuera.join();
    const quitar = new Set(fuera);
    const doc = this.doc;
    this.mundo.cargarMundo(quitar.size || this.ocultos.size ? { ...doc, objetos: doc.objetos.filter((o, n) => !quitar.has(n) && !this.ocultos.has(o)) } : doc);
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

  /** Los elegidos con el principal el último, para volver a elegir copias en el mismo orden. */
  private conPrincipalAlFinal(): number[] {
    const p = this.sel.principal;
    return [...this.sel.lista.filter((i) => i !== p), ...(p >= 0 ? [p] : [])];
  }

  /** Los objetos elegidos del documento. */
  private get elegidos(): Objeto[] {
    return this.sel.lista.map((i) => this.doc.objetos[i]).filter(Boolean);
  }

  /** El principal (el último elegido). */
  private get objetoSel(): Objeto | null {
    return this.doc.objetos[this.sel.principal] ?? null;
  }

  /** Tras cambiar `sel` o `zona`: repinta lo elegido y rehace las obras (con retardo) si hace falta. `rehacer` fuerza el rehacerlas. */
  private elegir(rehacer = false): void {
    this.elegirYa();
    if (rehacer || this.sel.lista.join() !== this.sacados) this.reconstruir();
    this.pintarAnillos();
    this.pintarProps();
    this.a.estado();
    this.pedir();
  }

  private limpiar(): void {
    this.sel.vaciar();
    this.zona = null;
    this.elegir();
  }

  private elegirZona(clave: string): void {
    this.sel.vaciar();
    this.zona = clave;
    this.elegir();
  }

  /** Repinta lo elegido y el gizmo, sin tocar las obras. */
  private elegirYa(): void {
    this.quitarVisuales();
    this.transform.detach();
    const lista = this.sel.lista;
    if (this.zona) {
      const z = this.doc.zonas[this.zona];
      this.ancla.position.set(z.x, sueloEn(z.x, z.z), z.z);
      this.transform.setMode("translate");
      this.transform.showY = false;
      this.transform.attach(this.ancla);
    } else if (lista.length) {
      for (const i of lista) this.mostrarObjeto(i);
      this.transform.setMode(this.modoGizmo);
      this.transform.showY = true;
      if (lista.length > 1) {
        this.colocarPivote();
        this.transform.attach(this.pivote);
      } else {
        const v = this.visuales.get(lista[0]);
        if (v) this.transform.attach(v);
      }
    }
  }

  /** Con varios elegidos el gizmo va a este pivote, en el centro de sus posiciones (con el giro del principal en ejes locales). */
  private colocarPivote(): void {
    const os = this.elegidos;
    if (os.length < 2) return;
    const caja = new Box3();
    const v = new Vector3();
    for (const o of os) caja.expandByPoint(v.set(...o.pos));
    caja.getCenter(this.pivote.position);
    const p = this.objetoSel;
    if (this.local && p) this.pivote.quaternion.setFromEuler(new Euler(p.rot[0], p.rot[1], p.rot[2], "YXZ"));
    else this.pivote.quaternion.identity();
    this.pivote.scale.set(1, 1, 1);
    this.pivote.updateMatrixWorld();
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

  /** Pinta suelto el objeto `i`; la geometría y el material se comparten entre los elegidos del mismo asset y tinte. */
  private mostrarObjeto(i: number): void {
    const o = this.doc.objetos[i];
    const asset = o && this.doc.assets[o.asset];
    if (!o || !asset) return;
    const grupo = new Group();
    grupo.rotation.order = "YXZ";
    this.visuales.set(i, grupo);
    const principal = i === this.sel.principal;
    if (asset.tipo === "piezas") {
      let g = this.geos.get(o.asset);
      if (!g) this.geos.set(o.asset, (g = geometriaAsset(asset)));
      const t = o.tinte ?? 1;
      let m = this.mats.get(t);
      if (!m) {
        m = new MeshLambertMaterial({ vertexColors: true, flatShading: true });
        m.color.setScalar(t);
        this.mats.set(t, m);
      }
      const malla = new Mesh(g, m);
      malla.castShadow = true;
      malla.receiveShadow = true;
      grupo.add(malla);
      this.ponerCaja(grupo, this.caja(o.asset), principal);
    } else {
      const id = o.asset;
      cargarGlb(asset.glb).then(
        (modelo) => {
          if (this.visuales.get(i) !== grupo) return;
          const c = modelo.clone(true);
          c.traverse((n) => ((n as Mesh).isMesh && ((n as Mesh).castShadow = (n as Mesh).receiveShadow = true)));
          grupo.add(c);
          let caja = this.cajas.get(id);
          if (!caja) {
            modelo.updateMatrixWorld(true);
            this.cajas.set(id, (caja = new Box3().setFromObject(modelo)));
          }
          this.ponerCaja(grupo, caja, principal);
          this.pedir();
        },
        () => this.a.aviso("No se ha podido cargar el modelo GLB."),
      );
    }
    this.aplicarEn(i);
    this.ctx.escena.add(grupo);
  }

  private ponerCaja(grupo: Group, caja: Box3 | null, principal: boolean): void {
    if (!caja) return;
    const c = new Box3Helper(caja);
    (c.material as LineBasicMaterial).dispose();
    c.material = principal ? this.lineaPrincipal : this.lineaOtro;
    this.cajasVis.push(c);
    grupo.add(c);
  }

  private quitarVisuales(): void {
    for (const g of this.visuales.values()) this.ctx.escena.remove(g);
    this.visuales.clear();
    for (const g of this.geos.values()) g.dispose();
    this.geos.clear();
    for (const m of this.mats.values()) m.dispose();
    this.mats.clear();
    for (const c of this.cajasVis) c.geometry.dispose();
    this.cajasVis = [];
  }

  /** Pone el objeto `i` suelto tal como está en el documento. */
  private aplicarEn(i: number): void {
    const v = this.visuales.get(i);
    const o = this.doc.objetos[i];
    if (!v || !o) return;
    v.position.set(...o.pos);
    v.rotation.set(o.rot[0], o.rot[1], o.rot[2], "YXZ");
    v.scale.set(...o.esc);
  }

  /** Tras cambiar objetos en el documento: los sueltos y el pivote los siguen. */
  private refrescarTodos(): void {
    for (const i of this.sel.lista) this.aplicarEn(i);
    this.colocarPivote();
  }

  /** El gizmo se está moviendo: el documento sigue a lo suelto (sin historial hasta soltarlo). */
  private alMover(): void {
    if (this.zona) {
      const z = this.doc.zonas[this.zona];
      z.x = limitar(redondear(this.ancla.position.x, 100), -MITAD, MITAD);
      z.z = limitar(redondear(this.ancla.position.z, 100), -MITAD, MITAD);
      this.ancla.position.y = sueloEn(z.x, z.z); // la altura no se toca
      this.pintarAnillos();
    } else if (this.arrastre) {
      const ts = this.arrastre.ahora();
      this.sel.lista.forEach((i, k) => {
        const o = this.doc.objetos[i];
        if (!o) return;
        o.pos = ts[k].pos.map((x) => redondear(x)) as Vec3;
        o.rot = ts[k].rot.map((x) => redondear(x)) as Vec3;
        o.esc = ts[k].esc.map((x) => redondear(x)) as Vec3;
        this.aplicarEn(i);
      });
    } else {
      const o = this.objetoSel;
      const v = this.visuales.get(this.sel.principal);
      if (!o || !v) return;
      o.pos = [redondear(v.position.x), redondear(v.position.y), redondear(v.position.z)];
      o.rot = [redondear(v.rotation.x), redondear(v.rotation.y), redondear(v.rotation.z)];
      o.esc = [redondear(v.scale.x), redondear(v.scale.y), redondear(v.scale.z)];
    }
    this.pintarProps();
  }

  private alSoltar(): void {
    if (this.sel.lista.length > 1) this.colocarPivote();
    if (this.sel.lista.length || this.zona) this.commit();
  }

  /** Lo editado en su sitio es siempre lo elegido; lo nuevo, lo borrado, las zonas y los teleportes el borrador lo ve solo. */
  private commit(): void {
    this.a.b.commit({ objetos: this.elegidos });
    this.pintarProps();
    this.a.estado();
    this.pedir();
  }

  // ---------------------------------------------------------------- edición

  private editarCampo(campo: "pos" | "rot" | "esc", i: number, input: HTMLInputElement): void {
    const os = this.elegidos;
    const v = parseFloat(input.value);
    if (!os.length || Number.isNaN(v)) return this.pintarProps();
    const [min, max, k] = campo === "pos" ? (i === 1 ? [MIN_Y, MAX_Y, 1] : [-MITAD, MITAD, 1]) : campo === "esc" ? [MIN_ESC, MAX_ESC, 1] : [-720, 720, GRADOS];
    for (const o of os) o[campo][i] = limitar(v, min, max) * k;
    this.refrescarTodos();
    this.commit();
  }

  private editarTinte(): void {
    const os = this.elegidos;
    if (!os.length) return;
    const v = parseFloat(this.tinte.value);
    for (const o of os) {
      if (Number.isNaN(v)) delete o.tinte;
      else o.tinte = limitar(v, MIN_TINTE, MAX_TINTE);
    }
    this.elegirYa(); // el material depende del tinte
    this.commit();
  }

  private cambiarAsset(id: string): void {
    const os = this.elegidos;
    if (!os.length || !this.doc.assets[id]) return;
    for (const o of os) {
      o.asset = id;
      this.preparar(o);
    }
    this.elegirYa();
    this.commit();
  }

  private editarZona(campo: "x" | "z" | "r", input: HTMLInputElement): void {
    const z = this.zona ? this.doc.zonas[this.zona] : null;
    const v = parseFloat(input.value);
    if (!z || Number.isNaN(v)) return this.pintarProps();
    z[campo] = campo === "r" ? limitar(v, 1, MAX_RADIO) : limitar(v, -MITAD, MITAD);
    this.ancla.position.set(z.x, sueloEn(z.x, z.z), z.z);
    this.pintarAnillos();
    this.commit();
  }

  // ---------------------------------------------------------------- teleportes

  /** Los del documento o, si no trae, los de serie (se copian al documento en el primer cambio). */
  private get teleportes(): Teleporte[] {
    return (this.doc.teleportes ??= teleportesDeSerie());
  }

  private pintarTeleportes(): void {
    const lista = this.doc.teleportes ?? teleportesDeSerie();
    this.listaTeleportes.replaceChildren(
      ...lista.map((t, i) => {
        const nombre = h("input", { type: "text", value: t.nombre, maxLength: MAX_NOMBRE });
        nombre.style.cssText = "flex: 1; min-width: 0";
        nombre.onchange = () => {
          const v = nombre.value.trim();
          if (!v) return void (nombre.value = t.nombre);
          this.teleportes[i].nombre = v;
          this.commitTeleportes();
        };
        return h(
          "div",
          { className: "ed-fila" },
          nombre,
          h("button", { className: "ed-b", textContent: "Ir", title: "Mira este teleporte", onclick: () => this.mirar(t.x, t.z, 25) }),
          h("button", { className: "ed-b", textContent: "Aquí", title: "Lo mueve al centro de la vista", onclick: () => this.moverTeleporte(i) }),
          h("button", { className: "ed-b", textContent: "✕", title: "Borrar", onclick: () => this.borrarTeleporte(i) }),
        );
      }),
    );
  }

  /** El centro de la vista (lo que miran los controles), o `null` fuera del mundo. */
  private centroVista(): [number, number] | null {
    const { x, z } = this.controles.target;
    if (Math.abs(x) > MITAD || Math.abs(z) > MITAD) {
      this.a.aviso("Ahí queda fuera del mundo.");
      return null;
    }
    return [redondear(x, 100), redondear(z, 100)];
  }

  private anadirTeleporte(): void {
    if (this.teleportes.length >= MAX_TELEPORTES) return this.a.aviso(`Puede haber ${MAX_TELEPORTES} teleportes como mucho.`);
    const p = this.centroVista();
    if (!p) return;
    this.teleportes.push({ nombre: `Teleporte ${this.teleportes.length + 1}`, x: p[0], z: p[1] });
    this.commitTeleportes();
  }

  private moverTeleporte(i: number): void {
    const p = this.centroVista();
    if (!p) return;
    [this.teleportes[i].x, this.teleportes[i].z] = p;
    this.commitTeleportes();
  }

  private borrarTeleporte(i: number): void {
    this.teleportes.splice(i, 1);
    this.commitTeleportes();
  }

  private commitTeleportes(): void {
    this.pintarTeleportes();
    this.commit();
  }

  private pegarAlSuelo(): void {
    for (const o of this.elegidos) o.pos[1] = redondear(limitar(sueloEn(o.pos[0], o.pos[2]), MIN_Y, MAX_Y));
    this.refrescarTodos();
    this.commit();
  }

  /** Pone la coordenada `eje` de todos igual que la del principal. */
  private alinear(eje: 0 | 1 | 2): void {
    const p = this.objetoSel;
    if (!p) return;
    for (const o of this.elegidos) o.pos[eje] = p.pos[eje];
    this.refrescarTodos();
    this.commit();
  }

  /** Mueve lo elegido (flechas, RePág, AvPág); `false` si no hay nada elegido. */
  private mover(dx: number, dy: number, dz: number): boolean {
    const os = this.elegidos;
    if (!os.length) return false;
    for (const o of os) {
      o.pos[0] = redondear(limitar(o.pos[0] + dx, -MITAD, MITAD));
      o.pos[1] = redondear(limitar(o.pos[1] + dy, MIN_Y, MAX_Y));
      o.pos[2] = redondear(limitar(o.pos[2] + dz, -MITAD, MITAD));
    }
    this.refrescarTodos();
    this.commit();
    return true;
  }

  private duplicar(): boolean {
    const lista = this.conPrincipalAlFinal();
    if (!lista.length) return false;
    const nuevos = lista.map((i) => {
      const copia = structuredClone(this.doc.objetos[i]);
      copia.pos[0] = limitar(copia.pos[0] + 1, -MITAD, MITAD);
      const nuevo = this.doc.objetos.push(copia) - 1;
      this.preparar(copia);
      return nuevo;
    });
    this.sel.poner(nuevos);
    this.elegir(true);
    this.commit();
    return true;
  }

  /** Borra lo elegido de mayor a menor índice, para que los demás no se muevan antes de tiempo. */
  private borrar(): boolean {
    const lista = this.sel.lista;
    if (!lista.length) return false;
    for (const i of lista.reverse()) this.doc.objetos.splice(i, 1);
    this.sel.vaciar();
    this.elegir(true);
    this.commit();
    return true;
  }

  private copiar(): boolean {
    const os = this.elegidos;
    if (!os.length) return false;
    portapapeles = os.map((o) => ({ objeto: structuredClone(o), sobre: o.pos[1] - sueloEn(o.pos[0], o.pos[2]) }));
    return true;
  }

  private cortar(): boolean {
    return this.copiar() && this.borrar();
  }

  /** Pega con el centro del grupo bajo el ratón (o +1 m en x si no hay terreno), cada uno a su altura sobre el suelo de origen. */
  private pegar(): boolean {
    const items = portapapeles.filter((p) => this.doc.assets[p.objeto.asset]);
    if (!items.length) return false;
    const caja = new Box3();
    const v = new Vector3();
    for (const { objeto } of items) caja.expandByPoint(v.set(...objeto.pos));
    const centro = caja.getCenter(new Vector3());
    const punto = this.raton ? this.suelo(this.rayo(this.raton).clone()) : null;
    const [dx, dz] = punto ? [punto.x - centro.x, punto.z - centro.z] : [1, 0];
    const nuevos = items.map(({ objeto, sobre }) => {
      const o = structuredClone(objeto);
      const x = limitar(o.pos[0] + dx, -MITAD, MITAD);
      const z = limitar(o.pos[2] + dz, -MITAD, MITAD);
      o.pos = [redondear(x, 100), redondear(limitar(sueloEn(x, z) + sobre, MIN_Y, MAX_Y)), redondear(z, 100)];
      const i = this.doc.objetos.push(o) - 1;
      this.preparar(o);
      return i;
    });
    this.sel.poner(nuevos);
    this.zona = null;
    this.elegir(true);
    this.commit();
    return true;
  }

  /** Ctrl+A: todos los objetos del mismo asset que el principal, hasta el tope. */
  private elegirDelAsset(): void {
    const p = this.objetoSel;
    if (!p) return;
    const lista: number[] = [];
    this.doc.objetos.forEach((o, i) => {
      if (o.asset === p.asset && i !== this.sel.principal && !this.ocultos.has(o)) lista.push(i);
    });
    if (lista.length > TOPE_SEL - 1) {
      lista.length = TOPE_SEL - 1;
      this.a.aviso(`Solo se pueden elegir ${TOPE_SEL} objetos a la vez.`);
    }
    this.sel.poner([...lista, this.sel.principal]);
    this.elegir();
  }

  /** H oculta lo elegido y Alt+H lo enseña todo; solo en el editor, no se guarda. */
  private ocultar(todoVisible: boolean): boolean {
    if (todoVisible) {
      if (!this.ocultos.size) return false;
      this.ocultos.clear();
    } else {
      const os = this.elegidos;
      if (!os.length) return false;
      for (const o of os) this.ocultos.add(o);
      this.sel.vaciar();
    }
    this.elegir(true);
    return true;
  }

  private editarAsset(): void {
    const o = this.objetoSel;
    if (o) this.a.editarAsset(o.asset);
  }

  /** Número libre para una tragaperras nueva (o datos de serie para un letrero); avisa si ya no quedan números. */
  private preparar(o: Objeto): void {
    if (!prepararObjeto(this.doc, o)) this.a.aviso("Ya están usados todos los números de tragaperras: esta queda sin número y no se juega.");
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
    const pos: Vec3 = [redondear(p.x, 100), redondear(sueloEn(p.x, p.z)), redondear(p.z, 100)];
    const o: Objeto = { asset: id, pos, rot: [0, 0, 0], esc: [1, 1, 1] };
    this.sel.solo(this.doc.objetos.push(o) - 1);
    this.preparar(o);
    this.zona = null;
    this.elegir(true);
    this.commit();
  }

  /** Caja de selección: los objetos visibles cuyo `pos` proyectado cae dentro; con Ctrl se suman. Tope de `TOPE_SEL`, los más cercanos. */
  private alCaja(rect: DOMRect, aditivo: boolean): void {
    const r = this.contenedor.getBoundingClientRect();
    const v = new Vector3();
    const cam = this.camara.position;
    this.camara.updateMatrixWorld();
    const dentro: { i: number; d: number }[] = [];
    this.doc.objetos.forEach((o, i) => {
      if (this.ocultos.has(o) || (aditivo && this.sel.tiene(i))) return;
      v.set(...o.pos).project(this.camara);
      const x = r.left + ((v.x + 1) / 2) * r.width;
      const y = r.top + ((1 - v.y) / 2) * r.height;
      if (v.z >= 1 || x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) return;
      dentro.push({ i, d: (o.pos[0] - cam.x) ** 2 + (o.pos[1] - cam.y) ** 2 + (o.pos[2] - cam.z) ** 2 });
    });
    if (aditivo && !dentro.length) return;
    const antes = aditivo ? this.sel.lista : [];
    const hueco = TOPE_SEL - antes.length;
    if (dentro.length > hueco) {
      dentro.sort((a, b) => a.d - b.d);
      dentro.length = hueco;
      this.a.aviso(`Solo se pueden elegir ${TOPE_SEL} objetos a la vez: te quedas con los más cercanos.`);
    }
    this.sel.poner([...antes, ...dentro.map((c) => c.i)]);
    this.zona = null;
    this.elegir();
  }

  // ---------------------------------------------------------------- zonas

  private alVerZonas(): void {
    this.anillos.visible = this.verZonas.checked;
    if (!this.verZonas.checked && this.zona) {
      this.zona = null;
      this.elegir();
    }
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
      const sel = this.zona === clave;
      const anillo = new LineLoop(new BufferGeometry().setFromPoints(puntos), sel ? this.lineaZonaSel : this.lineaZona);
      anillo.renderOrder = 10;
      anillo.frustumCulled = false;
      this.anillos.add(anillo);
      const b = h("button", { className: "ed-etiqueta-zona", textContent: nombreZona(clave), onclick: () => this.elegirZona(clave) });
      b.setAttribute("aria-pressed", String(sel));
      // Sin esto los controles de la cámara capturan el puntero y el clic no llega al botón.
      b.addEventListener("pointerdown", (e) => e.stopPropagation());
      this.etiquetas.append(b);
      this.etiquetasZona.set(clave, b);
    }
  }

  // ---------------------------------------------------------------- ratón

  private rayo(e: { clientX: number; clientY: number }): Ray {
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
      if (this.ocultos.has(o)) return;
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
    if (this.herramienta === "terreno" || this.recuadro.reciente) return;
    if (e.button !== 0 || e.target !== this.contenedor || this.inicio.distanceTo(new Vector2(e.clientX, e.clientY)) > 4 || this.transform.axis) return;
    const rayo = this.rayo(e).clone();
    if (this.colocando) return this.ponerEn(rayo);
    const zona = this.verZonas.checked ? this.zonaEn(rayo) : null;
    if (zona) return this.elegirZona(zona);
    const i = this.objetoEn(rayo);
    const aditivo = e.ctrlKey || e.metaKey;
    if (i < 0) return aditivo ? undefined : this.limpiar();
    if (aditivo) {
      if (!this.sel.tiene(i) && this.sel.lista.length >= TOPE_SEL) return this.a.aviso(`Solo se pueden elegir ${TOPE_SEL} objetos a la vez.`);
      this.sel.alternar(i);
    } else this.sel.solo(i);
    this.zona = null;
    this.elegir();
  };

  private readonly alMoverRaton = (e: PointerEvent): void => {
    this.raton = e;
  };

  private readonly alSalirRaton = (): void => {
    this.raton = null;
  };

  // Ctrl (o Cmd) pulsado invierte el ajuste a rejilla mientras dure; se mira antes que el gizmo (fase de captura).
  private readonly alPuntero = (e: PointerEvent): void => {
    if (!this.activo) return;
    const inv = e.ctrlKey || e.metaKey;
    if (inv === this.invertido) return;
    this.invertido = inv;
    this.aplicarAjuste();
  };

  // ---------------------------------------------------------------- terreno

  private cambiarHerramienta(k: "objetos" | "terreno"): void {
    if (k === this.herramienta || (k === "terreno" && PRUEBAS)) return;
    this.herramienta = k;
    const terreno = k === "terreno";
    if (terreno) {
      this.colocar(null);
      this.sel.vaciar();
      this.zona = null;
      this.elegir();
    }
    this.cajaObjetos.hidden = terreno;
    this.pincel.panel.hidden = !terreno;
    this.pincel.mostrar(terreno);
    // Con el pincel, el botón izquierdo pinta y el central gira la cámara.
    this.controles.mouseButtons = { LEFT: MOUSE.ROTATE, MIDDLE: terreno ? MOUSE.ROTATE : MOUSE.DOLLY, RIGHT: MOUSE.PAN };
    this.contenedor.style.cursor = terreno ? "crosshair" : "";
    this.pincel.refrescar();
    this.a.estado();
    this.pedir();
  }

  private readonly alBajarPincel = (e: PointerEvent): void => {
    if (this.herramienta !== "terreno" || e.button !== 0 || e.target !== this.contenedor) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    this.contenedor.setPointerCapture(e.pointerId);
    this.pincel.apuntar(this.suelo(this.rayo(e).clone()));
    this.pincel.empezar(e.shiftKey);
  };

  private readonly alMoverPincel = (e: PointerEvent): void => {
    if (this.herramienta === "terreno") this.pincel.apuntar(this.suelo(this.rayo(e).clone()));
  };

  private readonly alSoltarPincel = (e: PointerEvent): void => {
    if (this.herramienta !== "terreno" || e.button !== 0) return;
    this.pincel.terminar();
    if (this.contenedor.hasPointerCapture(e.pointerId)) this.contenedor.releasePointerCapture(e.pointerId);
  };

  private readonly alSalirPincel = (): void => {
    if (this.herramienta === "terreno") this.pincel.apuntar(null);
  };

  private readonly alRuedaPincel = (e: WheelEvent): void => {
    if (this.herramienta !== "terreno" || !this.pincel.rueda(e)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
  };

  // ---------------------------------------------------------------- cámara y teclado

  private mirar(x: number, z: number, distancia = 60): void {
    const dir = this.camara.position.clone().sub(this.controles.target).setLength(distancia);
    this.controles.target.set(x, sueloEn(x, z), z);
    this.camara.position.copy(this.controles.target).add(dir);
    this.controles.update();
    this.pedir();
  }

  /** Encuadra lo elegido: un objeto como siempre; un grupo, desde más lejos cuanto más extenso sea. */
  private enfocar(): void {
    const z = this.zona ? this.doc.zonas[this.zona] : null;
    if (z) return this.mirar(z.x, z.z);
    const os = this.elegidos;
    if (!os.length) return;
    const caja = new Box3();
    const v = new Vector3();
    for (const o of os) caja.expandByPoint(v.set(...o.pos));
    const c = caja.getCenter(new Vector3());
    const tamano = caja.getSize(new Vector3());
    this.mirar(c.x, c.z, os.length > 1 ? limitar(Math.max(tamano.x, tamano.z) * 1.5 + 20, 60, 250) : 60);
  }

  private cambiarModo(m: ModoGizmo): void {
    this.modoGizmo = m;
    if (this.sel.lista.length) this.transform.setMode(m);
    this.a.estado();
    this.pedir();
  }

  private cambiarEspacio(): void {
    this.local = !this.local;
    this.transform.setSpace(this.local ? "local" : "world");
    this.colocarPivote();
    this.botones.get("espacio")!.textContent = this.local ? "Ejes: locales" : "Ejes: globales";
    this.pedir();
  }

  private aplicarAjuste(): void {
    const si = this.ajuste.checked !== this.invertido;
    this.transform.setTranslationSnap(si ? 0.5 : null);
    this.transform.setRotationSnap(si ? 15 * GRADOS : null);
    this.transform.setScaleSnap(si ? 0.1 : null);
  }

  tecla(e: KeyboardEvent, k: string, ctrl: boolean): boolean {
    if (!ctrl && k === "t") {
      this.cambiarHerramienta(this.herramienta === "terreno" ? "objetos" : "terreno");
      return true;
    }
    if (this.herramienta === "terreno") return !ctrl && this.pincel.tecla(k);
    if (ctrl) {
      if (k === "d") return this.duplicar();
      if (k === "c") return this.copiar();
      if (k === "x") return this.cortar();
      if (k === "v") return this.pegar();
      if (k === "a") return this.elegirDelAsset(), true;
      return false;
    }
    const paso = e.shiftKey ? 5 : 0.5;
    const mueve: Record<string, [number, number, number]> = {
      arrowleft: [-paso, 0, 0],
      arrowright: [paso, 0, 0],
      arrowup: [0, 0, -paso],
      arrowdown: [0, 0, paso],
      pageup: [0, paso, 0],
      pagedown: [0, -paso, 0],
    };
    if (k === "escape") {
      if (!this.colocando && !this.sel.lista.length && !this.zona) return false;
      if (this.colocando) this.colocar(null);
      this.limpiar();
      return true;
    }
    if (k === "delete" || k === "backspace") return this.borrar();
    if (k === "h" || e.code === "KeyH") return this.ocultar(e.altKey);
    if (k in mueve) return this.mover(...mueve[k]);
    const hay = this.sel.lista.length > 0 || !!this.zona;
    if (k === "f") return hay && (this.enfocar(), true);
    if (k === "x") return this.cambiarEspacio(), true;
    const modo = MODOS.find(([, , t]) => t === k);
    if (modo && hay) {
      if (!this.zona) this.cambiarModo(modo[0]);
      return true;
    }
    return false;
  }
}
