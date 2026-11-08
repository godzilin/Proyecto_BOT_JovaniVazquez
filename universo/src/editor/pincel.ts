// Pinceles de terreno del editor de niveles (pestaña «Mundo», herramienta «Terreno»): esculpir (subir, bajar, aplanar,
// suavizar), pintar el color del suelo y sembrar o quitar vegetación. Trabaja sobre la rejilla viva de `mundo/terreno.ts`
// mientras dura el trazo (solo se rehace la parte tocada de la malla) y, al soltar, la pasa al borrador en un solo paso
// del historial. Los objetos que estaban en el suelo suben o bajan con él.
import { BufferGeometry, Float32BufferAttribute, LineBasicMaterial, LineLoop, type Scene, type Vector3 } from "three";
import type { Mundo } from "../mundo";
import { alturaEn, alturas, CELDAS, fijarTerreno, MAX_ALTURA, MIN_ALTURA, MITAD, N1, PALETA, PASO, pinturas, sueloEn, terrenoDoc, terrenoEn } from "../mundo/terreno";
import { h } from "./dom";
import type { MundoDoc, Objeto } from "./formato";
import type { Anfitrion } from "./panel";

export type Herramienta = "subir" | "aplanar" | "suavizar" | "pintar" | "vegetacion";

const HERRAMIENTAS: [Herramienta, string, string][] = [
  ["subir", "Subir / bajar", "Sube el suelo; con Mayús, lo baja"],
  ["aplanar", "Aplanar", "Lleva el suelo a la altura del punto donde empieza el trazo"],
  ["suavizar", "Suavizar", "Quita picos y escalones"],
  ["pintar", "Pintar color", "Pinta el color elegido; con Mayús, vuelve al color natural"],
  ["vegetacion", "Vegetación", "Siembra los assets marcados; con Mayús, los quita"],
];
const COLOR_ANILLO: Record<Herramienta, number> = { subir: 0xffd86b, aplanar: 0x6bd8ff, suavizar: 0xb08cff, pintar: 0x7dff8a, vegetacion: 0x4fd36b };
const MIN_RADIO = 1;
const MAX_RADIO = 40;
/** Metros por segundo que sube el suelo a fuerza 1 en el centro del pincel. */
const VELOCIDAD = 8;
/** Un objeto a menos de esto de su suelo antes del trazo se considera apoyado y lo sigue. */
const APOYADO = 4;
// Los mismos topes que el editor de niveles y el servidor.
const MAX_OBJETOS = 20_000;
const MAX_POR_PASO = 6;
/** Cada cuánto se rehacen las obras mientras se siembra (rehacerlas es lo caro). */
const REHACER_MS = 350;

const r4 = (v: number): number => Math.round(v * 1e4) / 1e4;
const limitar = (v: number, min: number, max: number): number => Math.min(Math.max(v, min), max);
const celda = (v: number): number => (v + MITAD) / PASO;

/** Lo que el pincel necesita del editor de niveles. */
export interface Ganchos {
  pedir(): void;
  /** Rehace las obras del mundo con el borrador (los objetos sembrados, los que siguen al suelo). */
  rehacerObras(): void;
}

export class PincelTerreno {
  readonly panel: HTMLElement;
  herramienta: Herramienta = "subir";
  radio = 8;
  fuerza = 0.5;
  pintura = 5;
  separacion = 3;
  private readonly sembrar = new Set<string>();
  private readonly anillo: LineLoop;
  private readonly material = new LineBasicMaterial({ color: COLOR_ANILLO.subir, depthTest: false, fog: false });
  private readonly botones = new Map<Herramienta, HTMLButtonElement>();
  private readonly listaVeg = h("div", { className: "ed-lista" });
  private readonly filtroVeg = h("input", { type: "search", placeholder: "Filtrar assets", className: "ed-filtro" });
  private readonly campoRadio = h("input", { type: "range", min: String(MIN_RADIO), max: String(MAX_RADIO), step: "1" });
  private readonly campoFuerza = h("input", { type: "range", min: "0.05", max: "1", step: "0.05" });
  private readonly campoSeparacion = h("input", { type: "number", min: "0.5", max: "20", step: "0.5" });
  private readonly textoRadio = h("span");
  private readonly textoFuerza = h("span");
  private readonly paleta = h("div", { className: "ed-fila ed-envuelve" });
  private readonly cajaPintar = h("div", { className: "ed-props" });
  private readonly cajaVeg = h("div", { className: "ed-props" });

  // El trazo en marcha.
  private punto: Vector3 | null = null;
  private trazando = false;
  private invertir = false;
  private objetivo = 0;
  private antes: Float32Array | null = null;
  private caja = [Infinity, Infinity, -Infinity, -Infinity];
  private sembrados = false;
  private bucle = 0;
  private ultimo = 0;
  private ultimaObra = 0;

  constructor(
    private readonly a: Anfitrion,
    private readonly mundo: Mundo,
    private readonly escena: Scene,
    private readonly g: Ganchos,
  ) {
    const geo = new BufferGeometry();
    geo.setAttribute("position", new Float32BufferAttribute(new Float32Array(64 * 3), 3));
    this.anillo = new LineLoop(geo, this.material);
    this.anillo.renderOrder = 10;
    this.anillo.frustumCulled = false;
    this.anillo.visible = false;
    escena.add(this.anillo);
    this.panel = this.montar();
  }

  private get doc(): MundoDoc {
    return this.a.b.doc;
  }

  // ---------------------------------------------------------------- pantalla

  private montar(): HTMLElement {
    const herramientas = HERRAMIENTAS.map(([k, texto, titulo], n) => {
      const b = h("button", { className: "ed-b", textContent: texto, title: `${titulo} (${n + 1})`, onclick: () => this.elegir(k) });
      this.botones.set(k, b);
      return b;
    });
    this.campoRadio.oninput = () => this.ponerRadio(Number(this.campoRadio.value));
    this.campoFuerza.oninput = () => this.ponerFuerza(Number(this.campoFuerza.value));
    this.campoSeparacion.onchange = () => {
      this.separacion = limitar(Number(this.campoSeparacion.value) || 3, 0.5, 20);
      this.campoSeparacion.value = String(this.separacion);
    };
    this.filtroVeg.oninput = () => this.pintarVegetacion();
    PALETA.forEach(({ nombre, color }, i) => {
      const b = h("button", { className: "ed-b ed-pintura", title: nombre, onclick: () => this.elegirPintura(i + 1) }, h("span", { className: "ed-muestra" }), ` ${nombre}`);
      (b.firstChild as HTMLElement).style.background = `#${color.getHexString()}`;
      this.paleta.append(b);
    });
    this.cajaPintar.append(h("h2", { textContent: "Color" }), this.paleta);
    this.cajaVeg.append(
      h("h2", { textContent: "Qué sembrar" }),
      h("p", { className: "ed-vacio", textContent: "Marca uno o varios assets: cada ejemplar sale de uno de ellos al azar." }),
      this.filtroVeg,
      this.listaVeg,
      h("label", { className: "ed-vec" }, h("span", { textContent: "Separación (m)" }), this.campoSeparacion),
    );
    return h(
      "div",
      { className: "ed-props" },
      h("h2", { textContent: "Pincel" }),
      h("div", { className: "ed-fila ed-envuelve" }, ...herramientas),
      h("label", { className: "ed-vec", title: "Ctrl+rueda o + y -" }, h("span", { textContent: "Radio" }), this.campoRadio, this.textoRadio),
      h("label", { className: "ed-vec", title: "Mayús+rueda" }, h("span", { textContent: "Fuerza" }), this.campoFuerza, this.textoFuerza),
      this.cajaPintar,
      this.cajaVeg,
      h("p", { className: "ed-vacio", textContent: "Clic izquierdo pinta; botón central gira la cámara y el derecho la mueve." }),
      h(
        "div",
        { className: "ed-fila" },
        h("button", { className: "ed-b", textContent: "Volver a la isla de serie", title: "Quita todo lo esculpido y pintado (se puede deshacer)", onclick: () => this.deSerie() }),
      ),
    );
  }

  /** Pone los controles al día con el estado del pincel y el documento. */
  refrescar(): void {
    for (const [k, b] of this.botones) b.setAttribute("aria-pressed", String(k === this.herramienta));
    this.campoRadio.value = String(this.radio);
    this.textoRadio.textContent = `${this.radio} m`;
    this.campoFuerza.value = String(this.fuerza);
    this.textoFuerza.textContent = `${Math.round(this.fuerza * 100)} %`;
    this.campoSeparacion.value = String(this.separacion);
    [...this.paleta.children].forEach((b, i) => b.setAttribute("aria-pressed", String(i + 1 === this.pintura)));
    this.cajaPintar.hidden = this.herramienta !== "pintar";
    this.cajaVeg.hidden = this.herramienta !== "vegetacion";
    for (const id of this.sembrar) if (!this.doc.assets[id]) this.sembrar.delete(id);
    this.pintarVegetacion();
    this.material.color.setHex(COLOR_ANILLO[this.herramienta]);
    this.colocarAnillo();
  }

  private pintarVegetacion(): void {
    const q = this.filtroVeg.value.trim().toLowerCase();
    const items = Object.entries(this.doc.assets)
      .filter(([id, a]) => !q || a.nombre.toLowerCase().includes(q) || id.includes(q))
      .sort((x, y) => x[1].nombre.localeCompare(y[1].nombre, "es"))
      .map(([id, a]) => {
        const marca = h("input", { type: "checkbox", checked: this.sembrar.has(id) });
        marca.onchange = () => (marca.checked ? this.sembrar.add(id) : this.sembrar.delete(id));
        return h("label", { className: "ed-item" }, marca, a.nombre, h("small", { textContent: ` ${id}` }));
      });
    this.listaVeg.replaceChildren(...(items.length ? items : [h("p", { className: "ed-vacio", textContent: "No hay assets." })]));
  }

  elegir(k: Herramienta): void {
    this.herramienta = k;
    this.refrescar();
    this.a.estado();
  }

  private elegirPintura(n: number): void {
    this.pintura = n;
    this.refrescar();
  }

  ponerRadio(r: number): void {
    this.radio = limitar(Math.round(r), MIN_RADIO, MAX_RADIO);
    this.refrescar();
    this.g.pedir();
  }

  ponerFuerza(f: number): void {
    this.fuerza = limitar(Math.round(f * 20) / 20, 0.05, 1);
    this.refrescar();
  }

  /** Cifras para la barra de estado. */
  estado(): string[] {
    return [`Pincel: ${HERRAMIENTAS.find(([k]) => k === this.herramienta)![1]}, ${this.radio} m, ${Math.round(this.fuerza * 100)} %`];
  }

  /** Enseña u oculta el anillo (al entrar y salir de la herramienta). */
  mostrar(si: boolean): void {
    this.anillo.visible = si && !!this.punto;
    if (!si) this.terminar();
  }

  // ---------------------------------------------------------------- anillo

  /** El ratón está sobre ese punto del terreno (o fuera de él). */
  apuntar(p: Vector3 | null): void {
    this.punto = p;
    this.colocarAnillo();
  }

  private colocarAnillo(): void {
    const p = this.punto;
    this.anillo.visible = !!p;
    if (!p) return this.g.pedir();
    const pos = this.anillo.geometry.getAttribute("position") as Float32BufferAttribute;
    for (let i = 0; i < 64; i++) {
      const x = p.x + Math.cos((i / 64) * 2 * Math.PI) * this.radio;
      const z = p.z + Math.sin((i / 64) * 2 * Math.PI) * this.radio;
      pos.setXYZ(i, x, terrenoEn(x, z) + 0.3, z);
    }
    pos.needsUpdate = true;
    this.g.pedir();
  }

  // ---------------------------------------------------------------- trazo

  /** Empieza un trazo en el punto apuntado; `invertir` es la Mayús (bajar, borrar color, quitar vegetación). */
  empezar(invertir: boolean): void {
    if (!this.punto || this.trazando) return;
    if (this.herramienta === "vegetacion" && !this.sembrar.size) {
      this.a.aviso(invertir ? "Marca primero qué assets quitar." : "Marca primero qué assets sembrar.");
      return;
    }
    this.trazando = true;
    this.invertir = invertir;
    this.objetivo = terrenoEn(this.punto.x, this.punto.z);
    this.antes = alturas.slice();
    this.caja = [Infinity, Infinity, -Infinity, -Infinity];
    this.sembrados = false;
    this.ultimo = performance.now();
    this.ultimaObra = this.ultimo;
    this.paso(1 / 30);
    this.bucle = requestAnimationFrame(this.alFotograma);
  }

  // Mientras se mantiene pulsado se sigue aplicando aunque el ratón esté quieto (subir, aplanar, sembrar).
  private readonly alFotograma = (t: number): void => {
    if (!this.trazando) return;
    const dt = Math.min((t - this.ultimo) / 1000, 0.1);
    this.ultimo = t;
    this.paso(dt);
    if (this.sembrados && t - this.ultimaObra > REHACER_MS) {
      this.ultimaObra = t;
      this.g.rehacerObras();
    }
    this.bucle = requestAnimationFrame(this.alFotograma);
  };

  private paso(dt: number): void {
    const p = this.punto;
    if (!p) return;
    if (this.herramienta === "vegetacion") return this.vegetacion(p.x, p.z, dt);
    const r = this.radio;
    const ix0 = Math.max(Math.floor(celda(p.x - r)), 0);
    const iz0 = Math.max(Math.floor(celda(p.z - r)), 0);
    const ix1 = Math.min(Math.ceil(celda(p.x + r)), CELDAS);
    const iz1 = Math.min(Math.ceil(celda(p.z + r)), CELDAS);
    if (ix0 > ix1 || iz0 > iz1) return;
    const copia = this.herramienta === "suavizar" ? alturas.slice() : null;
    const k = this.fuerza * dt;
    for (let iz = iz0; iz <= iz1; iz++) {
      for (let ix = ix0; ix <= ix1; ix++) {
        const d = Math.hypot(ix * PASO - MITAD - p.x, iz * PASO - MITAD - p.z) / r;
        if (d >= 1) continue;
        const w = (1 - d * d) ** 2; // suave en el borde
        const i = iz * N1 + ix;
        const hh = alturas[i];
        if (this.herramienta === "subir") alturas[i] = hh + (this.invertir ? -1 : 1) * VELOCIDAD * k * w;
        else if (this.herramienta === "aplanar") alturas[i] = hh + (this.objetivo - hh) * Math.min(1, 8 * k * w);
        else if (copia) {
          let s = 0;
          let n = 0;
          for (let dz = -1; dz <= 1; dz++) {
            for (let dx = -1; dx <= 1; dx++) {
              const x = ix + dx;
              const z = iz + dz;
              if (x < 0 || z < 0 || x > CELDAS || z > CELDAS) continue;
              s += copia[z * N1 + x];
              n++;
            }
          }
          alturas[i] = hh + (s / n - hh) * Math.min(1, 8 * k * w);
        } else pinturas[i] = this.invertir ? 0 : this.pintura;
        alturas[i] = limitar(alturas[i], MIN_ALTURA, MAX_ALTURA);
      }
    }
    const c = this.caja;
    [c[0], c[1], c[2], c[3]] = [Math.min(c[0], ix0), Math.min(c[1], iz0), Math.max(c[2], ix1), Math.max(c[3], iz1)];
    // Un vértice más por cada lado: su color depende de la pendiente, que mira a los vecinos.
    this.mundo.retocarTerreno(ix0 - 1, iz0 - 1, ix1 + 1, iz1 + 1, false);
    this.colocarAnillo();
  }

  private vegetacion(cx: number, cz: number, dt: number): void {
    const objetos = this.doc.objetos;
    const r = this.radio;
    if (this.invertir) {
      const antes = objetos.length;
      this.doc.objetos = objetos.filter((o) => !this.sembrar.has(o.asset) || Math.hypot(o.pos[0] - cx, o.pos[2] - cz) > r);
      if (this.doc.objetos.length !== antes) this.sembrados = true;
      return;
    }
    const ids = [...this.sembrar];
    // Ejemplares por segundo: los que caben en el círculo con esa separación, a la fuerza elegida.
    const cabida = (Math.PI * r * r) / (this.separacion * this.separacion);
    let intentos = Math.min(MAX_POR_PASO, Math.ceil(cabida * this.fuerza * dt * 2));
    const cerca = objetos.filter((o) => this.sembrar.has(o.asset) && Math.hypot(o.pos[0] - cx, o.pos[2] - cz) < r + this.separacion);
    while (intentos-- > 0) {
      if (objetos.length >= MAX_OBJETOS) {
        this.a.aviso(`El mundo puede tener ${MAX_OBJETOS} objetos como mucho.`);
        break;
      }
      const ang = Math.random() * 2 * Math.PI;
      const d = Math.sqrt(Math.random()) * r;
      const x = cx + Math.cos(ang) * d;
      const z = cz + Math.sin(ang) * d;
      if (Math.abs(x) > MITAD || Math.abs(z) > MITAD || terrenoEn(x, z) < 0.3) continue; // nada en el agua
      if (cerca.some((o) => Math.hypot(o.pos[0] - x, o.pos[2] - z) < this.separacion)) continue;
      const e = 0.8 + Math.random() * 0.4;
      const o: Objeto = {
        asset: ids[Math.floor(Math.random() * ids.length)],
        pos: [Math.round(x * 100) / 100, r4(sueloEn(x, z) - 0.1 * e), Math.round(z * 100) / 100],
        rot: [0, r4(Math.random() * 2 * Math.PI), 0],
        esc: [r4(e), r4(e), r4(e)],
        tinte: Math.round((0.85 + Math.random() * 0.3) * 100) / 100,
      };
      objetos.push(o);
      cerca.push(o);
      this.sembrados = true;
    }
  }

  /** Suelta el trazo: los objetos apoyados siguen al suelo, el mar se pone al día y todo entra en el historial. */
  terminar(): void {
    if (!this.trazando) return;
    this.trazando = false;
    cancelAnimationFrame(this.bucle);
    const [ix0, iz0, ix1, iz1] = this.caja;
    let movidos: Objeto[] = [];
    if (this.antes && ix0 <= ix1) {
      movidos = this.seguirSuelo(this.antes, ix0 * PASO - MITAD, iz0 * PASO - MITAD, ix1 * PASO - MITAD, iz1 * PASO - MITAD);
      this.doc.terreno = terrenoDoc();
      this.mundo.retocarTerreno(ix0 - 1, iz0 - 1, ix1 + 1, iz1 + 1, true);
    }
    this.antes = null;
    if (this.a.b.commit({ objetos: movidos }) || this.sembrados) this.g.rehacerObras();
    this.a.estado();
  }

  /** Mueve en altura los objetos apoyados dentro de ese recuadro lo que haya cambiado su suelo desde `antes`; devuelve los apoyados. */
  private seguirSuelo(antes: Float32Array, x0: number, z0: number, x1: number, z1: number): Objeto[] {
    const movidos: Objeto[] = [];
    for (const o of this.doc.objetos) {
      const [x, y, z] = o.pos;
      if (x < x0 || x > x1 || z < z0 || z > z1) continue;
      const viejo = alturaEn(antes, x, z);
      if (Math.abs(y - viejo) > APOYADO) continue;
      o.pos[1] = r4(limitar(y + terrenoEn(x, z) - viejo, MIN_ALTURA, MAX_ALTURA));
      movidos.push(o);
    }
    return movidos;
  }

  /** Quita el terreno del documento: vuelve la isla de la fórmula, y los objetos apoyados con ella. */
  private deSerie(): void {
    if (!this.doc.terreno) return this.a.aviso("La isla ya es la de serie.", undefined, false);
    const antes = alturas.slice();
    delete this.doc.terreno;
    fijarTerreno(undefined);
    const movidos = this.seguirSuelo(antes, -MITAD, -MITAD, MITAD, MITAD);
    this.mundo.retocarTerreno(0, 0, CELDAS, CELDAS, true);
    this.a.b.commit({ objetos: movidos });
    this.g.rehacerObras();
    this.colocarAnillo();
    this.a.estado();
  }

  // ---------------------------------------------------------------- teclado y rueda

  /** Atajos de la herramienta; `true` si la ha tratado. */
  tecla(k: string): boolean {
    const n = Number(k);
    if (n >= 1 && n <= HERRAMIENTAS.length) this.elegir(HERRAMIENTAS[n - 1][0]);
    else if (k === "+") this.ponerRadio(this.radio + 1);
    else if (k === "-") this.ponerRadio(this.radio - 1);
    else return false;
    return true;
  }

  /** Ctrl+rueda cambia el radio y Mayús+rueda la fuerza; `true` si la rueda era para el pincel. */
  rueda(e: WheelEvent): boolean {
    const sube = (e.deltaY || e.deltaX) < 0;
    if (e.ctrlKey || e.metaKey) this.ponerRadio(this.radio + (sube ? 1 : -1));
    else if (e.shiftKey) this.ponerFuerza(this.fuerza + (sube ? 0.05 : -0.05));
    else return false;
    return true;
  }

  dispose(): void {
    this.terminar();
    this.escena.remove(this.anillo);
    this.anillo.geometry.dispose();
    this.material.dispose();
  }
}

/** Atajos y ratón de la herramienta de terreno, para el menú de ayuda. */
export const AYUDA_TERRENO: [string, string][] = [
  ["T", "Cambia entre colocar objetos y editar el terreno"],
  ["1 a 5", "Subir / bajar, aplanar, suavizar, pintar color y vegetación"],
  ["Clic izquierdo (mantener)", "Aplica el pincel; mientras se mantiene, sigue aplicando"],
  ["Mayús + clic", "Lo contrario: baja el suelo, vuelve al color natural o quita la vegetación marcada"],
  ["Ctrl + rueda, o + y -", "Agranda o achica el pincel"],
  ["Mayús + rueda", "Sube o baja la fuerza del pincel"],
  ["Botón central / derecho", "Gira y mueve la cámara (la rueda acerca)"],
  ["Aplanar", "Toma la altura del punto donde empieza el trazo"],
  ["Al esculpir", "Los objetos apoyados en el suelo suben o bajan con él"],
];
