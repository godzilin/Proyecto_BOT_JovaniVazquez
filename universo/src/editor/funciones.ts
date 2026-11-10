// Lo propio de los objetos con función (tragaperras, letreros y puertas) en el editor de niveles: el número de cada máquina, sin
// repetir; el título y los botes de cada letrero; a qué interior lleva cada puerta y el propio interior (`EditorInterior`); los
// avisos de la barra de estado; y la «Función» de un asset en el editor de assets.
import type { Bote } from "../casino/dibujo";
import { type Sesion } from "./api";
import { h } from "./dom";
import {
  type Asset,
  type Funcion,
  type Interior,
  MAX_ALTO_INTERIOR,
  MAX_BOTES_LETRERO,
  MAX_LADO_INTERIOR,
  MAX_LADO_LETRERO,
  MAX_OBJETOS_INTERIOR,
  MAX_TITULO,
  MAX_TRAGAS,
  MAX_USABLES,
  MIN_ALTO_INTERIOR,
  MIN_LADO_INTERIOR,
  type MundoDoc,
  type Objeto,
} from "./formato";

/** El panel de serie de un letrero nuevo: el del letrero de la plaza. */
export const PANEL_DE_SERIE: Extract<Funcion, { tipo: "letrero" }> = { tipo: "letrero", pos: [0, 1.5, 0.1], ancho: 3, alto: 0.6 };

const esTraga = (doc: MundoDoc, o: Objeto): boolean => doc.assets[o.asset]?.funcion?.tipo === "traga";

/** Los números de tragaperras que ya usa el mundo (sin contar `salvo`). */
function usados(doc: MundoDoc, salvo?: Objeto): Set<number> {
  const s = new Set<number>();
  for (const o of doc.objetos) if (o !== salvo && esTraga(doc, o) && o.datos?.n !== undefined) s.add(o.datos.n);
  return s;
}

/**
 * Deja listo un objeto recién puesto, duplicado, pegado o cambiado de asset: una tragaperras sin número o con uno que ya tiene
 * otra se lleva el primero libre; un letrero sin datos, el bote de la tragaperras. Devuelve `false` si no queda ningún número.
 */
export function prepararObjeto(doc: MundoDoc, o: Objeto): boolean {
  const f = doc.assets[o.asset]?.funcion;
  if (!f) return true;
  if (f.tipo === "letrero") {
    o.datos ??= { botes: ["tragas"] };
    return true;
  }
  if (f.tipo === "puerta") {
    const primero = Object.keys(doc.interiores ?? {})[0];
    if (!o.datos?.interior && primero) o.datos = { ...o.datos, interior: primero };
    return true;
  }
  if (f.tipo !== "traga") return true;
  const ocupados = usados(doc, o);
  if (o.datos?.n !== undefined && !ocupados.has(o.datos.n)) return true;
  for (let n = 1; n <= MAX_TRAGAS; n++) {
    if (ocupados.has(n)) continue;
    o.datos = { ...o.datos, n };
    return true;
  }
  if (o.datos) delete o.datos.n;
  return false;
}

/** Avisos para la barra de estado: tragaperras sin número o con números repetidos (el servidor no guarda una celda así). */
export function avisosFunciones(doc: MundoDoc): string[] {
  const vistos = new Map<number, number>();
  let sinNumero = 0;
  for (const o of doc.objetos) {
    if (!esTraga(doc, o)) continue;
    const n = o.datos?.n;
    if (n === undefined) sinNumero++;
    else vistos.set(n, (vistos.get(n) ?? 0) + 1);
  }
  const repetidos = [...vistos].filter(([, c]) => c > 1).map(([n]) => n);
  const avisos: string[] = [];
  if (repetidos.length) avisos.push(`⚠ Tragaperras con el número repetido: ${repetidos.join(", ")}`);
  if (sinNumero) avisos.push(`⚠ ${sinNumero} tragaperras sin número (no se juegan)`);
  const sinInterior = doc.objetos.filter((o) => doc.assets[o.asset]?.funcion?.tipo === "puerta" && !doc.interiores?.[o.datos?.interior ?? ""]).length;
  if (sinInterior) avisos.push(`⚠ ${sinInterior} puertas que no llevan a ningún interior`);
  return avisos;
}

let catalogo: Promise<Bote[]> | null = null;
/**
 * Los botes que se pueden poner en un letrero (`/api/botes`), pedidos una vez por sesión del editor: todos los del catálogo, con
 * su cifra si su juego está cargado («—» si no).
 */
function pedirBotes(s: Sesion): Promise<Bote[]> {
  catalogo ??= fetch(`/api/botes?guild_id=${encodeURIComponent(s.guildId)}`, { headers: { Authorization: `Bearer ${s.session}` } })
    .then((r) => (r.ok ? (r.json() as Promise<{ botes: Bote[]; catalogo?: Omit<Bote, "texto">[] }>) : { botes: [], catalogo: [] }))
    .then((d) => (d.catalogo ?? d.botes).map((c) => ({ ...c, texto: d.botes.find((b) => b.clave === c.clave)?.texto ?? "—" })))
    .catch(() => {
      catalogo = null;
      return [];
    });
  return catalogo;
}

/** Los campos de la derecha del editor de niveles para un objeto con función (solo con uno elegido). */
export class PropsFuncion {
  readonly el = h("div", { className: "ed-props" });
  private objeto: Objeto | null = null;
  private tipo: string | null = null;
  private readonly numero = h("input", { type: "number", min: "1", max: String(MAX_TRAGAS), step: "1" });
  private readonly titulo = h("input", { type: "text", maxLength: MAX_TITULO, placeholder: "BOTE" });
  private readonly listaBotes = h("div", { className: "ed-lista" });
  private readonly destino = h("select");
  private readonly interior: EditorInterior;

  constructor(
    private readonly sesion: Sesion,
    private readonly doc: () => MundoDoc,
    private readonly cambiado: () => void,
    private readonly aviso: (texto: string) => void,
  ) {
    this.el.hidden = true;
    this.interior = new EditorInterior(doc, cambiado, aviso);
    this.destino.onchange = () => {
      if (!this.objeto) return;
      this.objeto.datos = { ...this.objeto.datos, interior: this.destino.value };
      if (!this.destino.value) delete this.objeto.datos.interior;
      this.cambiado();
    };
    this.numero.onchange = () => this.editarNumero();
    this.titulo.onchange = () => {
      if (!this.objeto) return;
      const t = this.titulo.value.trim().slice(0, MAX_TITULO);
      this.objeto.datos = { ...this.objeto.datos, titulo: t };
      if (!t) delete this.objeto.datos.titulo;
      this.cambiado();
    };
  }

  /** Enseña los campos de `o` (o nada si no tiene función o hay varios elegidos). */
  pintar(o: Objeto | null): void {
    const f = o ? this.doc().assets[o.asset]?.funcion : undefined;
    this.el.hidden = !f;
    if (!o || !f) {
      this.objeto = null;
      this.tipo = null;
      return;
    }
    if (o !== this.objeto || f.tipo !== this.tipo) {
      this.objeto = o;
      this.tipo = f.tipo;
      this.montar(f.tipo);
    }
    if (document.activeElement !== this.numero) this.numero.value = o.datos?.n !== undefined ? String(o.datos.n) : "";
    if (document.activeElement !== this.titulo) this.titulo.value = o.datos?.titulo ?? "";
    if (f.tipo === "puerta") {
      const ids = Object.keys(this.doc().interiores ?? {});
      const opciones = ["", ...ids].join("\n");
      if (this.destino.dataset.opciones !== opciones) {
        this.destino.dataset.opciones = opciones;
        this.destino.replaceChildren(h("option", { value: "", textContent: "— ninguno —" }), ...ids.map((id) => h("option", { value: id, textContent: `${this.doc().interiores?.[id].nombre} (${id})` })));
      }
      this.destino.value = o.datos?.interior ?? "";
      this.interior.pintar(o.datos?.interior ?? null);
    }
  }

  private montar(tipo: Funcion["tipo"]): void {
    if (tipo === "puerta") {
      const nuevo = h("button", { className: "ed-b", textContent: "➕ Nuevo interior" });
      nuevo.onclick = () => {
        const o = this.objeto;
        if (!o) return;
        o.datos = { ...o.datos, interior: this.interior.crear() };
        this.cambiado();
      };
      this.el.replaceChildren(
        h("h2", { textContent: "Puerta" }),
        h("label", { className: "ed-vec" }, h("span", { textContent: "Lleva a" }), this.destino),
        h("div", { className: "ed-fila" }, nuevo),
        this.interior.el,
      );
      return;
    }
    if (tipo === "usable") {
      this.el.replaceChildren(
        h("h2", { textContent: "Usable" }),
        h("p", { className: "ed-vacio", textContent: "Un usable solo funciona dentro de un interior: añádelo a la lista de objetos del interior (elige una puerta que lleve a él)." }),
      );
      return;
    }
    if (tipo === "traga") {
      this.el.replaceChildren(
        h("h2", { textContent: "Tragaperras" }),
        h("label", { className: "ed-vec" }, h("span", { textContent: "Nº de máquina" }), this.numero),
        h("p", {
          className: "ed-vacio",
          textContent: `Del 1 al ${MAX_TRAGAS}, sin repetir. Con el número se guardan el calor, la barra de bonus y los giros gratis de cada jugador en esa máquina; las 1 a 6 cuentan para el logro «Ruta de las tragas».`,
        }),
      );
      return;
    }
    this.el.replaceChildren(
      h("h2", { textContent: "Letrero" }),
      h("label", { className: "ed-vec" }, h("span", { textContent: "Título" }), this.titulo),
      h("p", { className: "ed-vacio", textContent: `Botes que enseña (hasta ${MAX_BOTES_LETRERO}; con varios, rotan cada 5 s; sin ninguno, solo el título):` }),
      this.listaBotes,
    );
    this.listaBotes.replaceChildren(h("p", { className: "ed-vacio", textContent: "Cargando botes…" }));
    const o = this.objeto;
    void pedirBotes(this.sesion).then((botes) => o === this.objeto && this.pintarBotes(botes));
  }

  private pintarBotes(botes: Bote[]): void {
    const o = this.objeto;
    if (!o) return;
    const elegidos = new Set(o.datos?.botes ?? []);
    // Los que tiene puestos y ya no da el servidor (el juego no está cargado) siguen saliendo, para poder quitarlos.
    const todos = [...botes.map((b) => ({ clave: b.clave, nombre: `${b.emoji} ${b.nombre}`, texto: b.texto })), ...[...elegidos].filter((k) => !botes.some((b) => b.clave === k)).map((k) => ({ clave: k, nombre: k, texto: "no disponible" }))];
    if (!todos.length) {
      this.listaBotes.replaceChildren(h("p", { className: "ed-vacio", textContent: "El servidor no ha dado ningún bote." }));
      return;
    }
    this.listaBotes.replaceChildren(
      ...todos.map((b) => {
        const caja = h("input", { type: "checkbox", checked: elegidos.has(b.clave) });
        caja.onchange = () => {
          const lista = new Set(o.datos?.botes ?? []);
          if (caja.checked) lista.add(b.clave);
          else lista.delete(b.clave);
          if (lista.size > MAX_BOTES_LETRERO) {
            caja.checked = false;
            return this.aviso(`Un letrero enseña ${MAX_BOTES_LETRERO} botes como mucho.`);
          }
          // En el orden del catálogo, que es el de la rotación.
          o.datos = { ...o.datos, botes: todos.map((t) => t.clave).filter((k) => lista.has(k)) };
          this.cambiado();
        };
        return h("label", { className: "ed-fila" }, caja, h("span", { textContent: `${b.nombre} · ${b.texto}` }));
      }),
    );
  }

  private editarNumero(): void {
    const o = this.objeto;
    if (!o) return;
    const n = Number(this.numero.value);
    if (!Number.isInteger(n) || n < 1 || n > MAX_TRAGAS) {
      this.aviso(`El número de máquina va del 1 al ${MAX_TRAGAS}.`);
      return this.pintar(o);
    }
    if (usados(this.doc(), o).has(n)) {
      this.aviso(`Ya hay otra tragaperras con el número ${n}.`);
      return this.pintar(o);
    }
    o.datos = { ...o.datos, n };
    this.cambiado();
  }
}

/** El bloque «Función» del editor de assets: ninguna, tragaperras o letrero (con su panel). */
export class CamposFuncion {
  readonly el: HTMLElement;
  private readonly tipo = h("select");
  private readonly panel = h("div", { className: "ed-props" });
  private readonly campos: { k: "x" | "y" | "z" | "ancho" | "alto"; input: HTMLInputElement }[] = [];
  private asset: Asset | null = null;
  private readonly ayuda = h("p", { className: "ed-vacio" });

  constructor(private readonly cambiado: () => void) {
    this.tipo.append(
      h("option", { value: "", textContent: "Ninguna (solo se ve)" }),
      h("option", { value: "traga", textContent: "Tragaperras" }),
      h("option", { value: "letrero", textContent: "Letrero" }),
      h("option", { value: "puerta", textContent: "Puerta a un interior" }),
      h("option", { value: "usable:mueble", textContent: "Mueble (se pone y se quita)" }),
      h("option", { value: "usable:reparar", textContent: "Se arregla (y se vuelve a romper)" }),
    );
    this.tipo.onchange = () => this.cambiarTipo();
    for (const [k, titulo] of [["x", "Centro x"], ["y", "Centro y"], ["z", "Centro z"], ["ancho", "Ancho (m)"], ["alto", "Alto (m)"]] as const) {
      const input = h("input", { type: "number", step: "0.05" });
      input.onchange = () => this.editarPanel(k, input);
      this.campos.push({ k, input });
      this.panel.append(h("label", { className: "ed-vec" }, h("span", { textContent: titulo }), input));
    }
    this.el = h(
      "div",
      { className: "ed-colision" },
      h("h2", { textContent: "Función" }),
      h("label", { className: "ed-vec" }, h("span", { textContent: "Hace" }), this.tipo),
      this.ayuda,
      this.panel,
    );
  }

  pintar(a: Asset | null): void {
    this.asset = a;
    this.tipo.disabled = !a;
    const f = a?.funcion;
    if (document.activeElement !== this.tipo) this.tipo.value = f ? (f.tipo === "usable" ? `usable:${f.uso}` : f.tipo) : "";
    this.panel.hidden = f?.tipo !== "letrero";
    this.ayuda.textContent =
      f?.tipo === "traga"
        ? "Se juega como las de la plaza: la pantalla, las luces y la botonera van en el sitio de la máquina de serie (el rectángulo y la caja verdes de la vista). Cada objeto lleva su número en el editor de niveles."
        : f?.tipo === "letrero"
          ? "El panel (el rectángulo verde) enseña el título y los botes que se elijan en cada objeto. Mira hacia +z."
          : f?.tipo === "puerta"
            ? "Con E se entra en el interior que se elija en cada objeto (en el editor de niveles). Se entra desde delante (+z)."
            : f?.tipo === "usable"
              ? `Dentro de un interior, con E: ${f.uso === "mueble" ? "se quita y se vuelve a poner" : "se arregla; a las 24 h vuelve a estar roto"}. El estado lo guarda el servidor por su número.`
              : "";
    if (f?.tipo !== "letrero") return;
    const v = { x: f.pos[0], y: f.pos[1], z: f.pos[2], ancho: f.ancho, alto: f.alto };
    for (const { k, input } of this.campos) if (document.activeElement !== input) input.value = String(Math.round(v[k] * 1000) / 1000);
  }

  private cambiarTipo(): void {
    const a = this.asset;
    if (!a) return;
    const t = this.tipo.value;
    if (t === "traga" || t === "puerta") a.funcion = { tipo: t };
    else if (t === "usable:mueble" || t === "usable:reparar") a.funcion = { tipo: "usable", uso: t === "usable:mueble" ? "mueble" : "reparar" };
    else if (t === "letrero") a.funcion = a.funcion?.tipo === "letrero" ? a.funcion : structuredClone(PANEL_DE_SERIE);
    else delete a.funcion;
    this.cambiado();
  }

  private editarPanel(k: "x" | "y" | "z" | "ancho" | "alto", input: HTMLInputElement): void {
    const f = this.asset?.funcion;
    const v = parseFloat(input.value);
    if (f?.tipo !== "letrero" || Number.isNaN(v)) return this.pintar(this.asset);
    if (k === "ancho" || k === "alto") f[k] = Math.min(Math.max(v, 0.1), MAX_LADO_LETRERO);
    else f.pos["xyz".indexOf(k)] = Math.min(Math.max(v, -60), 60);
    this.cambiado();
  }
}

/**
 * El interior al que lleva la puerta elegida, en el editor de niveles: nombre, medidas, colores, si es un piso y la lista de sus
 * objetos (en coordenadas de la habitación: el 0 es el centro del suelo y la salida está en la pared +z). Lo mínimo para hacer uno
 * sin colocarlo en 3D: se mira entrando por la puerta en el juego. Cambia `doc.interiores` en su sitio y avisa con `cambiado`.
 */
class EditorInterior {
  readonly el = h("div", { className: "ed-props" });
  private id: string | null = null;
  private pintado = "";

  constructor(
    private readonly doc: () => MundoDoc,
    private readonly cambiado: () => void,
    private readonly aviso: (texto: string) => void,
  ) {}

  /** Crea un interior vacío (10 × 8 × 3 m) y devuelve su id. */
  crear(): string {
    const doc = this.doc();
    doc.interiores ??= {};
    let n = 1;
    while (doc.interiores[`interior-${n}`]) n++;
    const id = `interior-${n}`;
    doc.interiores[id] = { nombre: `Interior ${n}`, ancho: 10, fondo: 8, alto: 3, suelo: 0xb08d6a, pared: 0xf1e7d4, objetos: [] };
    return id;
  }

  pintar(id: string | null): void {
    const i = id ? this.doc().interiores?.[id] : undefined;
    this.el.hidden = !i;
    if (!i || !id) return;
    const texto = JSON.stringify(i);
    if (id === this.id && texto === this.pintado) return;
    if (id === this.id && this.el.contains(document.activeElement)) return; // no se rehace mientras se escribe
    this.id = id;
    this.pintado = texto;
    this.montar(id, i);
  }

  private hecho(): void {
    this.pintado = "";
    this.cambiado();
  }

  private montar(id: string, i: Interior): void {
    const num = (titulo: string, v: number, min: number, max: number, poner: (v: number) => void, paso = "0.1"): HTMLElement => {
      const input = h("input", { type: "number", step: paso, value: String(v) });
      input.onchange = () => {
        const x = parseFloat(input.value);
        if (Number.isNaN(x)) return;
        poner(Math.min(Math.max(x, min), max));
        this.hecho();
      };
      return h("label", { className: "ed-vec" }, h("span", { textContent: titulo }), input);
    };
    const color = (titulo: string, v: number, poner: (v: number) => void): HTMLElement => {
      const input = h("input", { type: "color", value: `#${v.toString(16).padStart(6, "0")}` });
      input.onchange = () => {
        poner(parseInt(input.value.slice(1), 16));
        this.hecho();
      };
      return h("label", { className: "ed-vec" }, h("span", { textContent: titulo }), input);
    };
    const nombre = h("input", { type: "text", maxLength: 40, value: i.nombre });
    nombre.onchange = () => {
      if (!nombre.value.trim()) return;
      i.nombre = nombre.value.trim().slice(0, 40);
      this.hecho();
    };
    const piso = h("input", { type: "checkbox", checked: i.piso === true });
    piso.onchange = () => {
      if (piso.checked) i.piso = true;
      else delete i.piso;
      this.hecho();
    };
    const borrar = h("button", { className: "ed-b", textContent: "🗑 Borrar interior" });
    borrar.onclick = () => {
      delete this.doc().interiores?.[id];
      this.hecho();
    };
    const añadir = h("button", { className: "ed-b", textContent: "➕ Objeto" });
    añadir.onclick = () => {
      if (i.objetos.length >= MAX_OBJETOS_INTERIOR) return this.aviso(`Un interior lleva ${MAX_OBJETOS_INTERIOR} objetos como mucho.`);
      const primero = Object.keys(this.doc().assets)[0];
      if (!primero) return this.aviso("Primero hace falta algún asset.");
      const o: Objeto = { asset: primero, pos: [0, 0, 0], rot: [0, 0, 0], esc: [1, 1, 1] };
      i.objetos.push(o);
      this.numerar(i, o);
      this.hecho();
    };
    this.el.replaceChildren(
      h("h2", { textContent: `Interior «${id}»` }),
      h("label", { className: "ed-vec" }, h("span", { textContent: "Nombre" }), nombre),
      num("Ancho (x, m)", i.ancho, MIN_LADO_INTERIOR, MAX_LADO_INTERIOR, (v) => (i.ancho = v)),
      num("Fondo (z, m)", i.fondo, MIN_LADO_INTERIOR, MAX_LADO_INTERIOR, (v) => (i.fondo = v)),
      num("Alto (m)", i.alto, MIN_ALTO_INTERIOR, MAX_ALTO_INTERIOR, (v) => (i.alto = v)),
      color("Suelo", i.suelo, (v) => (i.suelo = v)),
      color("Paredes", i.pared, (v) => (i.pared = v)),
      h("label", { className: "ed-fila" }, piso, h("span", { textContent: "Es un piso: quien tenga vivienda habitual en «pisos» entra en el suyo; el resto, en el piso piloto." })),
      h("p", { className: "ed-vacio", textContent: "Objetos, en metros desde el centro del suelo (la salida está en la pared +z) y el giro en grados. Los usables llevan su número (del 1 al 24, sin repetir)." }),
      ...i.objetos.map((o, k) => this.fila(i, o, k)),
      h("div", { className: "ed-fila" }, añadir, borrar),
    );
  }

  /** Un usable sin número (o con uno repetido) se lleva el primero libre del interior. */
  private numerar(i: Interior, o: Objeto): void {
    if (this.doc().assets[o.asset]?.funcion?.tipo !== "usable") {
      if (o.datos) delete o.datos.n;
      return;
    }
    const usados = new Set(i.objetos.filter((x) => x !== o && x.datos?.n !== undefined).map((x) => x.datos!.n));
    if (o.datos?.n !== undefined && !usados.has(o.datos.n)) return;
    for (let n = 1; n <= MAX_USABLES; n++) {
      if (usados.has(n)) continue;
      o.datos = { ...o.datos, n };
      return;
    }
    this.aviso(`Un interior lleva ${MAX_USABLES} usables como mucho: este se queda sin número y no se usa.`);
  }

  private fila(i: Interior, o: Objeto, k: number): HTMLElement {
    const asset = h("select");
    for (const [id, a] of Object.entries(this.doc().assets)) asset.append(h("option", { value: id, textContent: a.nombre, selected: id === o.asset }));
    asset.onchange = () => {
      o.asset = asset.value;
      this.numerar(i, o);
      this.hecho();
    };
    const coord = (j: 0 | 1 | 2, lim: () => [number, number]): HTMLInputElement => {
      const input = h("input", { type: "number", step: "0.1", value: String(o.pos[j]), title: "xyz"[j] });
      input.onchange = () => {
        const v = parseFloat(input.value);
        if (Number.isNaN(v)) return;
        const [a, b] = lim();
        o.pos[j] = Math.min(Math.max(v, a), b);
        this.hecho();
      };
      return input;
    };
    const giro = h("input", { type: "number", step: "15", value: String(Math.round((o.rot[1] * 180) / Math.PI)), title: "giro" });
    giro.onchange = () => {
      const v = parseFloat(giro.value);
      if (Number.isNaN(v)) return;
      o.rot = [0, (v * Math.PI) / 180, 0];
      this.hecho();
    };
    const quitar = h("button", { className: "ed-b", textContent: "✕", title: "Quitar" });
    quitar.onclick = () => {
      i.objetos.splice(k, 1);
      this.hecho();
    };
    const n = o.datos?.n;
    return h(
      "div",
      { className: "ed-fila" },
      asset,
      coord(0, () => [-i.ancho / 2, i.ancho / 2]),
      coord(1, () => [0, i.alto]),
      coord(2, () => [-i.fondo / 2, i.fondo / 2]),
      giro,
      h("span", { textContent: n !== undefined ? `nº ${n}` : "" }),
      quitar,
    );
  }
}
