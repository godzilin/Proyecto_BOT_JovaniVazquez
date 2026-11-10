// La malla del suelo, por celdas (`docs/mundo-celdas.md`): cada celda cargada cerca de la cámara tiene su malla de la rejilla
// (entera hasta `FINO_CERCA`, uno de cada dos vértices hasta `FINO`), con un faldón que tapa las rendijas con la vecina de otro
// detalle. Más lejos, el horizonte: teselas de `TESELA` × `TESELA` celdas con 5 × 5 alturas por celda (las del resumen que manda el
// servidor, o las de la rejilla si está cargada), una llamada de dibujo por tesela. Todo se monta por tandas (`trabajar`) para que
// cruzar de celda no dé tirones.
import { BufferAttribute, BufferGeometry, type Material, Mesh, type Scene } from "three";
import { CELDA, type Terreno } from "../editor/formato";
import type { Culling } from "./culling";
import {
  colorEn,
  enIsla,
  LADO_RESUMEN,
  NV,
  numeroDe,
  PASO,
  PASO_RESUMEN,
  POR_CELDA,
  ponerRejilla,
  ponerResumen,
  quitarRejilla,
  quitarResumen,
  rejillas,
  resumenes,
  terrenoEn,
  verticeEn,
} from "./terreno";

// Medidas con docs/medir_universo.py: con la isla de serie, como las 16 baldosas de antes.
/** A menos de esto (m, del centro de la celda), la rejilla entera; hasta `FINO`, uno de cada dos vértices; más allá, el horizonte. */
const FINO_CERCA = 150;
export const FINO = 250;
const HISTERESIS = 12;
/** Lo que cuelga el borde de cada malla de celda (m). */
const FALDON = 3;
/** Hasta dónde llega el horizonte: a 900 m la niebla tapa ya el 99,8 %. */
export const LEJOS = 900;
/** Celdas por lado de una tesela del horizonte (640 m). */
const TESELA = 8;
const B = 1 << 20;
const coords = (n: number): [number, number] => [Math.floor(n / (2 * B)) - B, (n % (2 * B)) - B];

interface Fina {
  malla: Mesh;
  paso: number;
}

export class Suelo {
  private finas = new Map<number, Fina>();
  /** El detalle que toca a cada celda con rejilla (0: horizonte) y las que hay que rehacer. */
  private quiere = new Map<number, number>();
  private sucias = new Set<number>();
  private teselas = new Map<number, Mesh | null>();
  private teselasSucias = new Set<number>();
  private indices = new Map<number, BufferAttribute>();
  private cx = Infinity;
  private cz = Infinity;
  private mirar = true;
  /** Se llama cuando cambia el suelo de la isla del centro (el mapa de profundidad del mar). */
  alCambiarIsla: () => void = () => undefined;

  constructor(
    private readonly escena: Scene,
    private readonly culling: Culling,
    private readonly material: Material,
  ) {}

  /** Una celda cargada, con su terreno (o sin él: la fórmula de la isla, o mar). */
  ponerCelda(cx: number, cz: number, t?: Terreno): void {
    const n = numeroDe(cx, cz);
    if (!ponerRejilla(cx, cz, t)) this.quitarFina(n);
    else if (this.finas.has(n)) this.sucias.add(n);
    this.cambio(cx, cz);
  }

  quitarCelda(cx: number, cz: number): void {
    const n = numeroDe(cx, cz);
    if (!quitarRejilla(cx, cz)) return;
    this.quitarFina(n);
    this.quiere.delete(n);
    this.cambio(cx, cz);
  }

  /** El resumen de una celda lejana (base64 de 25 alturas; vacío, sin terreno). */
  ponerResumen(cx: number, cz: number, b64: string): void {
    ponerResumen(cx, cz, b64);
    this.teselasSucias.add(this.tesela(cx, cz));
  }

  quitarResumen(cx: number, cz: number): void {
    if (quitarResumen(cx, cz)) this.teselasSucias.add(this.tesela(cx, cz));
  }

  /**
   * El pincel del editor ha cambiado las rejillas de las celdas (`rejillas`) entre esos vértices de la rejilla del mundo (cada `PASO`
   * m desde 0): se rehacen ya sus mallas. Una celda que acaba de tener rejilla se monta con todo el detalle.
   */
  retocar(gx0: number, gz0: number, gx1: number, gz1: number): void {
    for (let cz = Math.ceil(gz0 / POR_CELDA) - 1; cz <= Math.floor(gz1 / POR_CELDA); cz++) {
      for (let cx = Math.ceil(gx0 / POR_CELDA) - 1; cx <= Math.floor(gx1 / POR_CELDA); cx++) {
        const n = numeroDe(cx, cz);
        if (!rejillas.has(n)) continue;
        const paso = this.finas.get(n)?.paso ?? 1;
        if (!this.quiere.has(n)) {
          this.quiere.set(n, paso);
          this.mirar = true; // que `actualizar` le dé el detalle que le toca por distancia
        }
        this.montarFina(n, paso);
        this.teselasSucias.add(this.tesela(cx, cz));
      }
    }
  }

  /** Con la cámara en (x, z): decide el detalle de cada celda y qué teselas del horizonte hacen falta. Barato si no se ha movido. */
  actualizar(x: number, z: number): void {
    if (!this.mirar && Math.hypot(x - this.cx, z - this.cz) < 8) return;
    this.mirar = false;
    this.cx = x;
    this.cz = z;
    for (const n of rejillas.keys()) {
      const [cx, cz] = coords(n);
      const d = Math.hypot((cx + 0.5) * CELDA - x, (cz + 0.5) * CELDA - z);
      const ahora = this.finas.get(n)?.paso ?? 0;
      const h = (lim: number, paso: number): number => (ahora && ahora <= paso ? lim + HISTERESIS : lim - HISTERESIS);
      const paso = d < h(FINO_CERCA, 1) ? 1 : d < h(FINO, 2) ? 2 : 0;
      this.quiere.set(n, paso);
      if (paso !== ahora) this.sucias.add(n);
    }
    const t0x = Math.floor((x - LEJOS) / (CELDA * TESELA));
    const t1x = Math.floor((x + LEJOS) / (CELDA * TESELA));
    const t0z = Math.floor((z - LEJOS) / (CELDA * TESELA));
    const t1z = Math.floor((z + LEJOS) / (CELDA * TESELA));
    const vivas = new Set<number>();
    for (let tx = t0x; tx <= t1x; tx++) {
      for (let tz = t0z; tz <= t1z; tz++) {
        const n = numeroDe(tx, tz);
        vivas.add(n);
        if (!this.teselas.has(n)) this.teselasSucias.add(n);
      }
    }
    for (const n of [...this.teselas.keys()]) {
      if (vivas.has(n)) continue;
      this.quitarTesela(n);
      this.teselasSucias.delete(n);
    }
    for (const n of [...this.teselasSucias]) if (!vivas.has(n)) this.teselasSucias.delete(n);
  }

  /** Monta lo pendiente, lo más cercano antes, hasta `hasta` (ms de `performance.now`). Dice si queda algo. */
  trabajar(hasta: number): boolean {
    if (this.mirar && Number.isFinite(this.cx)) this.actualizar(this.cx, this.cz); // han llegado o se han ido celdas
    if (this.sucias.size) {
      const orden = [...this.sucias].sort((a, b) => this.distancia(a) - this.distancia(b));
      for (const n of orden) {
        if (performance.now() > hasta) return true;
        this.sucias.delete(n);
        if (!rejillas.has(n)) continue;
        const paso = this.quiere.get(n) ?? 0;
        const antes = this.finas.get(n)?.paso ?? 0;
        if (paso) this.montarFina(n, paso);
        else this.quitarFina(n);
        if (paso !== antes) this.teselasSucias.add(this.tesela(...coords(n)));
      }
    }
    for (const n of [...this.teselasSucias]) {
      if (performance.now() > hasta) return true;
      this.teselasSucias.delete(n);
      this.montarTesela(n);
    }
    return false;
  }

  /** Quita todas las mallas y olvida todas las rejillas y los resúmenes. */
  vaciar(): void {
    for (const n of [...this.finas.keys()]) this.quitarFina(n);
    for (const n of [...this.teselas.keys()]) this.quitarTesela(n);
    rejillas.clear();
    resumenes.clear();
    this.quiere.clear();
    this.sucias.clear();
    this.teselasSucias.clear();
    this.mirar = true;
  }

  /** Mallas montadas (finas y teselas): para las pruebas y las devtools. */
  get mallas(): number {
    return this.finas.size + [...this.teselas.values()].filter(Boolean).length;
  }

  private cambio(cx: number, cz: number): void {
    this.mirar = true;
    this.teselasSucias.add(this.tesela(cx, cz));
    if (enIsla(cx, cz)) this.alCambiarIsla();
  }

  private tesela(cx: number, cz: number): number {
    return numeroDe(Math.floor(cx / TESELA), Math.floor(cz / TESELA));
  }

  private distancia(n: number): number {
    const [cx, cz] = coords(n);
    return Math.hypot((cx + 0.5) * CELDA - this.cx, (cz + 0.5) * CELDA - this.cz);
  }

  private quitarFina(n: number): void {
    const f = this.finas.get(n);
    if (!f) return;
    this.escena.remove(f.malla);
    this.culling.quitar(f.malla);
    f.malla.geometry.dispose();
    this.finas.delete(n);
  }

  private quitarTesela(n: number): void {
    const m = this.teselas.get(n);
    this.teselas.delete(n);
    if (!m) return;
    this.escena.remove(m);
    this.culling.quitar(m);
    m.geometry.dispose();
  }

  /** Los índices de una malla de celda de `lado` vértices por lado más su faldón (comunes a todas las de ese detalle). */
  private indicesDe(lado: number): BufferAttribute {
    let a = this.indices.get(lado);
    if (a) return a;
    const idx: number[] = [];
    for (let iz = 0; iz < lado - 1; iz++) {
      for (let ix = 0; ix < lado - 1; ix++) {
        const v00 = iz * lado + ix;
        idx.push(v00, v00 + lado, v00 + 1, v00 + 1, v00 + lado, v00 + lado + 1);
      }
    }
    // Faldón: cada borde (sur, norte, oeste, este) repetido `FALDON` m más abajo, girado hacia fuera.
    const borde = (k: number, b: number): number => [k, (lado - 1) * lado + k, k * lado, k * lado + lado - 1][b];
    for (let b = 0; b < 4; b++) {
      const s = lado * lado + b * lado;
      const girar = b === 1 || b === 2;
      for (let k = 0; k < lado - 1; k++) {
        const [a0, a1, s0, s1] = [borde(k, b), borde(k + 1, b), s + k, s + k + 1];
        if (girar) idx.push(a1, a0, s0, a1, s0, s1);
        else idx.push(a0, a1, s0, s0, a1, s1);
      }
    }
    a = new BufferAttribute(new Uint16Array(idx), 1);
    this.indices.set(lado, a);
    return a;
  }

  private montarFina(n: number, paso: number): void {
    const r = rejillas.get(n);
    if (!r) return;
    const [cx, cz] = coords(n);
    const lado = POR_CELDA / paso + 1;
    const pos = new Float32Array((lado * lado + 4 * lado) * 3);
    const col = new Float32Array(pos.length);
    const gx0 = cx * POR_CELDA;
    const gz0 = cz * POR_CELDA;
    const alt = (gx: number, gz: number): number => {
      const lx = gx - gx0;
      const lz = gz - gz0;
      return lx >= 0 && lz >= 0 && lx < NV && lz < NV ? r.alt[lz * NV + lx] : verticeEn(gx, gz);
    };
    for (let iz = 0; iz < lado; iz++) {
      for (let ix = 0; ix < lado; ix++) {
        const i = iz * paso * NV + ix * paso;
        const gx = gx0 + ix * paso;
        const gz = gz0 + iz * paso;
        const h = r.alt[i];
        const hx = alt(gx + 1, gz) - alt(gx - 1, gz);
        const hz = alt(gx, gz + 1) - alt(gx, gz - 1);
        const c = colorEn(gx * PASO, gz * PASO, h, Math.hypot(hx, hz) / (2 * PASO), r.pin[i]);
        const j = (iz * lado + ix) * 3;
        pos[j] = gx * PASO;
        pos[j + 1] = h;
        pos[j + 2] = gz * PASO;
        col[j] = c.r;
        col[j + 1] = c.g;
        col[j + 2] = c.b;
      }
    }
    const borde = (k: number, b: number): number => [k, (lado - 1) * lado + k, k * lado, k * lado + lado - 1][b];
    for (let b = 0; b < 4; b++) {
      for (let k = 0; k < lado; k++) {
        const de = borde(k, b) * 3;
        const a = (lado * lado + b * lado + k) * 3;
        pos.copyWithin(a, de, de + 3);
        col.copyWithin(a, de, de + 3);
        pos[a + 1] -= FALDON;
      }
    }
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(pos, 3));
    g.setAttribute("color", new BufferAttribute(col, 3));
    g.setIndex(this.indicesDe(lado));
    g.computeBoundingSphere();
    this.quitarFina(n);
    const malla = new Mesh(g, this.material);
    malla.receiveShadow = true;
    this.escena.add(malla);
    this.culling.registrarMalla(malla, FINO + CELDA, true);
    this.finas.set(n, { malla, paso });
  }

  /** Una tesela del horizonte: las celdas con suelo que no tienen malla fina, a 5 × 5 alturas cada una. */
  private montarTesela(t: number): void {
    const [tx, tz] = coords(t);
    const porCelda = LADO_RESUMEN - 1;
    const lado = TESELA * porCelda + 1;
    const gx0 = tx * TESELA * POR_CELDA;
    const gz0 = tz * TESELA * POR_CELDA;
    const quads: number[] = [];
    for (let j = 0; j < TESELA; j++) {
      for (let i = 0; i < TESELA; i++) {
        const cx = tx * TESELA + i;
        const cz = tz * TESELA + j;
        const n = numeroDe(cx, cz);
        if (this.finas.has(n) || !(rejillas.has(n) || resumenes.has(n) || enIsla(cx, cz))) continue;
        for (let b = 0; b < porCelda; b++) for (let a = 0; a < porCelda; a++) quads.push((j * porCelda + b) * lado + i * porCelda + a);
      }
    }
    this.quitarTesela(t);
    if (!quads.length) {
      this.teselas.set(t, null);
      return;
    }
    const muestra = (gx: number, gz: number): [number, number] => {
      const cx = Math.floor(gx / POR_CELDA);
      const cz = Math.floor(gz / POR_CELDA);
      const n = numeroDe(cx, cz);
      const lx = gx - cx * POR_CELDA;
      const lz = gz - cz * POR_CELDA;
      const r = rejillas.get(n);
      if (r) return [r.alt[lz * NV + lx], r.pin[lz * NV + lx]];
      const s = resumenes.get(n);
      if (s) return [s[(lz / PASO_RESUMEN) * LADO_RESUMEN + lx / PASO_RESUMEN], 0];
      return [terrenoEn(gx * PASO, gz * PASO), 0];
    };
    const alturas = new Float32Array(lado * lado);
    const pinturas = new Uint8Array(lado * lado);
    for (let b = 0; b < lado; b++) {
      for (let a = 0; a < lado; a++) [alturas[b * lado + a], pinturas[b * lado + a]] = muestra(gx0 + a * PASO_RESUMEN, gz0 + b * PASO_RESUMEN);
    }
    const pos = new Float32Array(lado * lado * 3);
    const col = new Float32Array(pos.length);
    const paso = PASO * PASO_RESUMEN;
    for (let b = 0; b < lado; b++) {
      for (let a = 0; a < lado; a++) {
        const i = b * lado + a;
        const x = (gx0 + a * PASO_RESUMEN) * PASO;
        const z = (gz0 + b * PASO_RESUMEN) * PASO;
        const h = alturas[i];
        const hx = alturas[b * lado + Math.min(a + 1, lado - 1)] - alturas[b * lado + Math.max(a - 1, 0)];
        const hz = alturas[Math.min(b + 1, lado - 1) * lado + a] - alturas[Math.max(b - 1, 0) * lado + a];
        const c = colorEn(x, z, h, Math.hypot(hx, hz) / (2 * paso), pinturas[i]);
        pos.set([x, h, z], i * 3);
        col.set([c.r, c.g, c.b], i * 3);
      }
    }
    const idx = new Uint16Array(quads.length * 6);
    quads.forEach((v00, k) => idx.set([v00, v00 + lado, v00 + 1, v00 + 1, v00 + lado, v00 + lado + 1], k * 6));
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(pos, 3));
    g.setAttribute("color", new BufferAttribute(col, 3));
    g.setIndex(new BufferAttribute(idx, 1));
    g.computeBoundingSphere();
    const malla = new Mesh(g, this.material);
    this.escena.add(malla);
    this.culling.registrarMalla(malla, LEJOS, true);
    this.teselas.set(t, malla);
  }
}
