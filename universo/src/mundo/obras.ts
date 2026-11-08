// Dibuja lo fijo del mundo (los objetos del editor) de las celdas cargadas (`docs/mundo-celdas.md`): llegan y se van por celdas de datos
// (`ponerCelda`, `quitarCelda`) y se dibujan por regiones de una rejilla propia, medida con docs/medir_universo.py: en cada celda de
// `CELDA_VEGETACION` los assets repetidos 8 veces o más (y los GLB) van instanciados, uno por grupo; el resto se funde por celdas de
// `CELDA_OBRA`, una malla por distancia. Cambiar una celda de datos solo marca las regiones que toca, que se rehacen por tandas
// (`trabajar`) para no dar tirones. Los círculos de colisión van por celda de datos, con la posición, el giro en y y la escala de cada
// objeto.
import { type BufferAttribute, BufferGeometry, Color, Euler, InstancedMesh, Matrix4, Mesh, type Material, type Object3D, Quaternion, type Scene, Vector3 } from "three";
import { type Asset, CELDA, cargarGlb, geometriaAsset, type Objeto } from "../editor/formato";
import { añadirCirculo, type Circulo, quitarCirculos } from "./colision";
import type { Culling } from "./culling";
import { fundir } from "./geom";
import { CELDA_OBRA, CELDA_VEGETACION, DIST_OBRA } from "./medidas";

/**
 * Desde cuántos objetos iguales en una celda de vegetación compensa una malla instanciada en lugar de copias fundidas. Con 8, un
 * mundo sintético de 60 objetos por celda pasaba de 120 llamadas; con 16 se queda en ~90 (docs/medir_universo.py --recorrido).
 */
const INSTANCIAR = 16;
const B = 1 << 20;
const num = (ix: number, iz: number): number => (ix + B) * 2 * B + iz + B;
const coords = (n: number): [number, number] => [Math.floor(n / (2 * B)) - B, (n % (2 * B)) - B];

function soltarCopia(this: { array: unknown }): void {
  this.array = null;
}

const euler = new Euler();
const quat = new Quaternion();
const uno = new Vector3();

/** La matriz de un objeto del documento (posición, giro YXZ y escala). */
export function matriz(o: Objeto): Matrix4 {
  quat.setFromEuler(euler.set(o.rot[0], o.rot[1], o.rot[2], "YXZ"));
  return new Matrix4().compose(uno.set(...o.pos), quat, new Vector3(...o.esc));
}

/** Los assets que solo se diferencian en la colisión se dibujan juntos (una malla instanciada por celda, no una por asset). */
interface Grupo {
  id: number;
  asset: Asset;
  distancia: number;
  /** Objetos cargados que lo usan: sin ninguno se suelta su geometría. */
  usos: number;
  geo?: BufferGeometry;
}

/** Lo dibujado de una región. `viva` en falso cuando se quita (un GLB que llega tarde no se añade). */
interface Region {
  mallas: (Mesh | InstancedMesh)[];
  viva: boolean;
}

export class Obras {
  private assets = new Map<string, Asset>();
  private celdas = new Map<number, Objeto[]>();
  private circulos = new Map<number, Circulo[]>();
  /** El grupo de cada asset, y los grupos por su contenido. */
  private grupos = new Map<string, Grupo>();
  private porContenido = new Map<string, Grupo>();
  private regionesV = new Map<number, Region>();
  private regionesO = new Map<number, Region>();
  private suciasV = new Set<number>();
  private suciasO = new Set<number>();

  constructor(
    private readonly escena: Scene,
    private readonly culling: Culling,
    private readonly material: Material,
  ) {}

  /** Un asset que usan (o van a usar) las celdas cargadas. Si cambia, se rehacen las regiones de los objetos que lo usan. */
  ponerAsset(id: string, a: Asset): void {
    const antes = this.assets.get(id);
    if (antes === a) return;
    this.assets.set(id, a);
    if (antes) this.cambiarAsset(id);
  }

  quitarAsset(id: string): void {
    if (this.assets.delete(id)) this.cambiarAsset(id);
  }

  tieneAsset(id: string): boolean {
    return this.assets.has(id);
  }

  /** Los objetos de la celda de datos (cx, cz); sustituye a los que tuviera. */
  ponerCelda(cx: number, cz: number, objetos: Objeto[]): void {
    this.quitarCelda(cx, cz);
    const n = num(cx, cz);
    this.celdas.set(n, objetos);
    const circulos: Circulo[] = [];
    for (const o of objetos) {
      const a = this.assets.get(o.asset);
      if (a?.colision) this.colisiones(circulos, a, o);
      const g = this.grupoDe(o.asset);
      if (g) g.usos++;
    }
    this.circulos.set(n, circulos);
    this.marcar(cx, cz);
  }

  quitarCelda(cx: number, cz: number): void {
    const n = num(cx, cz);
    const objetos = this.celdas.get(n);
    if (!objetos) return;
    this.celdas.delete(n);
    quitarCirculos(this.circulos.get(n) ?? []);
    this.circulos.delete(n);
    for (const o of objetos) {
      const g = this.grupos.get(o.asset);
      if (g && --g.usos <= 0) {
        g.geo?.dispose(); // three la vuelve a subir si una región que aún no se ha rehecho la dibuja
        g.geo = undefined;
      }
    }
    this.marcar(cx, cz);
  }

  /** Cuántas regiones quedan por rehacer. */
  get pendientes(): number {
    return this.suciasV.size + this.suciasO.size;
  }

  /** Rehace las regiones marcadas hasta `hasta` (ms de `performance.now`; `Infinity`, todas). Dice si queda alguna. */
  trabajar(hasta: number): boolean {
    const cuentas = new Map<number, Map<Grupo, number>>();
    for (const v of [...this.suciasV]) {
      if (performance.now() > hasta) return true;
      this.suciasV.delete(v);
      this.montarV(v);
    }
    for (const o of [...this.suciasO]) {
      if (performance.now() > hasta) return true;
      this.suciasO.delete(o);
      this.montarO(o, cuentas);
    }
    return false;
  }

  /** Quita de la escena, del culling y de las colisiones todo lo cargado. El material es compartido y no se libera. */
  vaciar(): void {
    for (const r of [...this.regionesV.values(), ...this.regionesO.values()]) this.quitar(r);
    for (const c of this.circulos.values()) quitarCirculos(c);
    this.regionesV.clear();
    this.regionesO.clear();
    this.circulos.clear();
    this.celdas.clear();
    this.suciasV.clear();
    this.suciasO.clear();
    for (const g of this.porContenido.values()) g.geo?.dispose();
    this.grupos.clear();
    this.porContenido.clear();
    this.assets.clear();
  }

  /** Mallas montadas ahora (las pruebas y las devtools). */
  get mallas(): number {
    let n = 0;
    for (const r of [...this.regionesV.values(), ...this.regionesO.values()]) n += r.mallas.length;
    return n;
  }

  private cambiarAsset(id: string): void {
    const g = this.grupos.get(id);
    this.grupos.delete(id);
    for (const [n, objetos] of this.celdas) {
      if (!objetos.some((o) => o.asset === id)) continue;
      const [cx, cz] = coords(n);
      if (g) g.usos -= objetos.filter((o) => o.asset === id).length;
      const nuevo = this.grupoDe(id);
      if (nuevo) nuevo.usos += objetos.filter((o) => o.asset === id).length;
      // Los círculos del asset pueden haber cambiado: se rehacen los de la celda.
      quitarCirculos(this.circulos.get(n) ?? []);
      const circulos: Circulo[] = [];
      for (const o of objetos) {
        const a = this.assets.get(o.asset);
        if (a?.colision) this.colisiones(circulos, a, o);
      }
      this.circulos.set(n, circulos);
      this.marcar(cx, cz);
    }
  }

  /** Marca las regiones que toca la celda de datos (cx, cz): las de vegetación que la cortan y las de obra de alrededor. */
  private marcar(cx: number, cz: number): void {
    const [x0, z0, x1, z1] = [cx * CELDA, cz * CELDA, (cx + 1) * CELDA, (cz + 1) * CELDA];
    const rango = (a: number, b: number, lado: number): [number, number] => [Math.floor(a / lado), Math.ceil(b / lado) - 1];
    const [vx0, vx1] = rango(x0, x1, CELDA_VEGETACION);
    const [vz0, vz1] = rango(z0, z1, CELDA_VEGETACION);
    for (let i = vx0; i <= vx1; i++) for (let j = vz0; j <= vz1; j++) this.suciasV.add(num(i, j));
    // Que un grupo vaya suelto depende de cuántos haya en su celda de vegetación: se marcan las de obra que la cortan.
    const [ox0, ox1] = rango(vx0 * CELDA_VEGETACION, (vx1 + 1) * CELDA_VEGETACION, CELDA_OBRA);
    const [oz0, oz1] = rango(vz0 * CELDA_VEGETACION, (vz1 + 1) * CELDA_VEGETACION, CELDA_OBRA);
    for (let i = ox0; i <= ox1; i++) for (let j = oz0; j <= oz1; j++) this.suciasO.add(num(i, j));
  }

  /** Los objetos cargados dentro del cuadrado de lado `lado` (ix, iz). */
  private objetosEn(ix: number, iz: number, lado: number): Objeto[] {
    const [x0, z0, x1, z1] = [ix * lado, iz * lado, (ix + 1) * lado, (iz + 1) * lado];
    const out: Objeto[] = [];
    for (let cx = Math.floor(x0 / CELDA); cx * CELDA < x1; cx++) {
      for (let cz = Math.floor(z0 / CELDA); cz * CELDA < z1; cz++) {
        for (const o of this.celdas.get(num(cx, cz)) ?? []) {
          if (o.pos[0] >= x0 && o.pos[0] < x1 && o.pos[2] >= z0 && o.pos[2] < z1) out.push(o);
        }
      }
    }
    return out;
  }

  private grupoDe(id: string): Grupo | undefined {
    let g = this.grupos.get(id);
    if (g) return g;
    const a = this.assets.get(id);
    if (!a) return undefined;
    const clave = JSON.stringify([a.tipo === "glb" ? a.glb : [a.piezas, a.malla], a.distancia]);
    g = this.porContenido.get(clave);
    if (!g) {
      g = { id: this.porContenido.size, asset: a, distancia: a.distancia ?? DIST_OBRA, usos: 0 };
      this.porContenido.set(clave, g);
    }
    this.grupos.set(id, g);
    return g;
  }

  /** Cuántos objetos de cada grupo hay en una celda de vegetación (para saber si van instanciados). */
  private cuenta(v: number, memo: Map<number, Map<Grupo, number>>): Map<Grupo, number> {
    let c = memo.get(v);
    if (c) return c;
    c = new Map();
    const [i, j] = coords(v);
    for (const o of this.objetosEn(i, j, CELDA_VEGETACION)) {
      const g = this.grupoDe(o.asset);
      if (g) c.set(g, (c.get(g) ?? 0) + 1);
    }
    memo.set(v, c);
    return c;
  }

  /** Una región de vegetación: una malla instanciada por cada grupo repetido 8 veces o más, y los GLB. */
  private montarV(v: number): void {
    const viejo = this.regionesV.get(v);
    if (viejo) this.quitar(viejo);
    this.regionesV.delete(v);
    const [i, j] = coords(v);
    const porGrupo = new Map<Grupo, Objeto[]>();
    for (const o of this.objetosEn(i, j, CELDA_VEGETACION)) {
      const g = this.grupoDe(o.asset);
      if (!g) continue;
      const l = porGrupo.get(g);
      if (l) l.push(o);
      else porGrupo.set(g, [o]);
    }
    const r: Region = { mallas: [], viva: true };
    for (const [g, objetos] of porGrupo) {
      if (g.asset.tipo === "glb") {
        const sha = g.asset.glb;
        cargarGlb(sha).then(
          (modelo) => r.viva && this.instanciarModelo(r, modelo, objetos, g.distancia),
          (e: unknown) => console.warn(`No se ha podido cargar el modelo ${sha}`, e),
        );
      } else if (objetos.length >= INSTANCIAR) this.instanciar(r, this.geometria(g), objetos, g.distancia);
    }
    this.regionesV.set(v, r);
  }

  /** Una región de obra: los objetos sueltos (los que no van instanciados), fundidos en una malla por distancia. */
  private montarO(n: number, memo: Map<number, Map<Grupo, number>>): void {
    const viejo = this.regionesO.get(n);
    if (viejo) this.quitar(viejo);
    this.regionesO.delete(n);
    const [i, j] = coords(n);
    const porDistancia = new Map<number, BufferGeometry[]>();
    for (const o of this.objetosEn(i, j, CELDA_OBRA)) {
      const g = this.grupoDe(o.asset);
      if (!g || g.asset.tipo === "glb") continue;
      const v = num(Math.floor(o.pos[0] / CELDA_VEGETACION), Math.floor(o.pos[2] / CELDA_VEGETACION));
      if ((this.cuenta(v, memo).get(g) ?? 0) >= INSTANCIAR) continue;
      const l = porDistancia.get(g.distancia);
      const copia = this.copia(this.geometria(g), o);
      if (l) l.push(copia);
      else porDistancia.set(g.distancia, [copia]);
    }
    const r: Region = { mallas: [], viva: true };
    for (const [distancia, copias] of porDistancia) {
      const malla = new Mesh(fundir(copias), this.material);
      malla.castShadow = true;
      malla.receiveShadow = true;
      malla.geometry.computeBoundingSphere();
      // Nadie vuelve a leer las copias fundidas (las colisiones van aparte): en cuanto están en la GPU se suelta su copia en memoria.
      for (const a of Object.values(malla.geometry.attributes)) (a as BufferAttribute).onUpload(soltarCopia);
      this.añadir(r, malla, distancia);
    }
    this.regionesO.set(n, r);
  }

  private geometria(g: Grupo): BufferGeometry {
    return (g.geo ??= geometriaAsset(g.asset as Extract<Asset, { tipo: "piezas" }>));
  }

  private quitar(r: Region): void {
    r.viva = false;
    for (const m of r.mallas) {
      this.escena.remove(m);
      this.culling.quitar(m);
      if (!(m as InstancedMesh).isInstancedMesh) m.geometry.dispose(); // las instanciadas comparten la geometría del asset
      else (m as InstancedMesh).dispose();
    }
  }

  private añadir(r: Region, m: Mesh | InstancedMesh, distancia: number): void {
    this.escena.add(m);
    this.culling.registrarMalla(m, distancia);
    r.mallas.push(m);
  }

  /** La geometría del asset con la matriz del objeto aplicada y los colores multiplicados por su tinte. */
  private copia(g: BufferGeometry, o: Objeto): BufferGeometry {
    const c = g.clone().applyMatrix4(matriz(o));
    if (o.tinte !== undefined && o.tinte !== 1) {
      const col = c.getAttribute("color");
      for (let i = 0; i < col.count; i++) col.setXYZ(i, col.getX(i) * o.tinte, col.getY(i) * o.tinte, col.getZ(i) * o.tinte);
    }
    return c;
  }

  /** Una malla instanciada con los objetos de una celda de `CELDA_VEGETACION`, como la vegetación. */
  private instanciar(r: Region, g: BufferGeometry, objetos: Objeto[], distancia: number): void {
    const color = new Color();
    const m = new InstancedMesh(g, this.material, objetos.length);
    objetos.forEach((o, i) => {
      m.setMatrixAt(i, matriz(o));
      m.setColorAt(i, color.setScalar(o.tinte ?? 1));
    });
    m.instanceMatrix.needsUpdate = true;
    m.castShadow = true;
    m.receiveShadow = true;
    m.computeBoundingSphere();
    this.añadir(r, m, distancia);
  }

  /** Un GLB: una malla instanciada por cada `Mesh` del modelo, con todas las instancias (matriz del objeto por la del nodo). */
  private instanciarModelo(r: Region, modelo: Object3D, objetos: Objeto[], distancia: number): void {
    modelo.updateMatrixWorld(true);
    const color = new Color();
    modelo.traverse((n) => {
      const mesh = n as Mesh;
      if (!mesh.isMesh || (mesh as { isSkinnedMesh?: boolean }).isSkinnedMesh) return;
      const m = new InstancedMesh(mesh.geometry, mesh.material, objetos.length);
      objetos.forEach((o, i) => {
        m.setMatrixAt(i, matriz(o).multiply(mesh.matrixWorld));
        m.setColorAt(i, color.setScalar(o.tinte ?? 1));
      });
      m.instanceMatrix.needsUpdate = true;
      m.castShadow = true;
      m.receiveShadow = true;
      m.computeBoundingSphere();
      this.añadir(r, m, distancia);
    });
  }

  private colisiones(circulos: Circulo[], a: Asset, o: Objeto): void {
    if (!a.colision) return;
    const cos = Math.cos(o.rot[1]);
    const sin = Math.sin(o.rot[1]);
    const radio = Math.max(o.esc[0], o.esc[2]);
    for (const [cx, cz, rc] of a.colision) {
      const x = cx * o.esc[0];
      const z = cz * o.esc[2];
      circulos.push(añadirCirculo(o.pos[0] + x * cos + z * sin, o.pos[2] - x * sin + z * cos, rc * radio));
    }
  }
}
