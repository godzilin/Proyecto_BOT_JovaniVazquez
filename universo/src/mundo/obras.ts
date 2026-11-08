// Dibuja el mundo publicado desde el editor (`MundoDoc`): cada asset se monta una vez; los muy repetidos van instanciados por celdas
// y el resto se funde por celdas, todo con culling. Sin documento (o vacío) no añade ni una malla. Los círculos de colisión de cada
// asset pasan al mundo con la posición, el giro en y y la escala de cada objeto.
// Lo dibujado va por regiones (la celda de dibujo de cada grupo de assets, o el grupo entero si es un GLB), para que `actualizar`
// rehaga solo las regiones de unas celdas del documento cuando se publica un cambio pequeño.
import { BufferGeometry, Color, Euler, InstancedMesh, Matrix4, Mesh, type Material, type Object3D, Quaternion, type Scene, Vector3 } from "three";
import { type Asset, cargarGlb, geometriaAsset, type MundoDoc, type Objeto } from "../editor/formato";
import { numeroCelda, numeroDeClave } from "../editor/trozos";
import { añadirCirculo, type Circulo, quitarCirculos } from "./colision";
import { celdaDe, type Culling } from "./culling";
import { fundir } from "./geom";
import { CELDA_OBRA, CELDA_VEGETACION, DIST_OBRA } from "./medidas";

/** Desde cuántos objetos iguales compensa una malla instanciada en lugar de copias fundidas. */
const INSTANCIAR = 8;

/** La celda de dibujo de lado `lado` de un objeto, en número (`celdaDe` sin crear un texto). */
const numero = (o: Objeto, lado: number): number => Math.floor(o.pos[0] / lado) * 65536 + Math.floor(o.pos[2] / lado);

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
  /** Objetos del mundo que lo usan, y si por eso va instanciado (los GLB siempre lo van, todos juntos). */
  n: number;
  instanciado: boolean;
  geo?: BufferGeometry;
}

/** Lo dibujado de una región: sus mallas y sus círculos. `viva` en falso cuando se quita (un GLB que llega tarde no se añade). */
interface Region {
  mallas: (Mesh | InstancedMesh)[];
  circulos: Circulo[];
  viva: boolean;
}

export class Obras {
  private regiones = new Map<string, Region>();
  /** El grupo de cada asset usado, y los grupos por su contenido. */
  private grupos = new Map<string, Grupo>();
  private porContenido = new Map<string, Grupo>();
  private doc: MundoDoc | null = null;

  constructor(
    private readonly escena: Scene,
    private readonly culling: Culling,
    private readonly material: Material,
  ) {}

  /** Sustituye todo lo dibujado por el contenido de `doc`. */
  cargar(doc: MundoDoc): void {
    this.vaciar();
    this.doc = doc;
    for (const [g, n] of this.contar(doc)) {
      g.n = n;
      g.instanciado = g.asset.tipo === "glb" || n >= INSTANCIAR;
    }
    this.montar(doc);
  }

  /**
   * `doc` es el mundo de antes con esas claves de trozo cambiadas (`parchear`) y los mismos assets: se rehacen solo las regiones de
   * esas celdas. Devuelve `false` (sin tocar nada) si no se puede y hay que `cargar` entero: otros assets, o un grupo que pasa de
   * suelto a instanciado o al revés.
   */
  actualizar(doc: MundoDoc, claves: Iterable<string>): boolean {
    const antes = this.doc;
    if (!antes || doc.assets !== antes.assets) return false;
    const celdas = new Set<number>();
    for (const k of claves) {
      const n = numeroDeClave(k);
      if (n !== null) celdas.add(n);
    }
    const cuentas = this.contar(doc);
    for (const [g, n] of cuentas) if (g.asset.tipo !== "glb" && n >= INSTANCIAR !== g.instanciado) return false;
    for (const [g, n] of cuentas) g.n = n;
    const afectadas = new Set<string>();
    // Las celdas de dibujo afectadas, en números: así `montar` no tiene que sacar la clave de región de todos los objetos.
    const cerca = new Set<number>();
    for (const d of [antes, doc]) {
      for (const o of d.objetos) {
        if (!celdas.has(numeroCelda(o.pos[0], o.pos[2]))) continue;
        const g = this.grupos.get(o.asset);
        if (!g) continue;
        afectadas.add(this.region(g, o));
        cerca.add(numero(o, g.instanciado ? CELDA_VEGETACION : CELDA_OBRA));
      }
    }
    const circulos: Circulo[] = [];
    for (const k of afectadas) {
      const r = this.regiones.get(k);
      if (!r) continue;
      this.quitar(r);
      circulos.push(...r.circulos);
      this.regiones.delete(k);
    }
    quitarCirculos(circulos);
    this.doc = doc;
    this.montar(doc, afectadas, (g, o) => g.asset.tipo === "glb" || cerca.has(numero(o, g.instanciado ? CELDA_VEGETACION : CELDA_OBRA)));
    return true;
  }

  /** Quita de la escena, del culling y de las colisiones todo lo que se cargó. El material es compartido y no se libera. */
  vaciar(): void {
    const circulos: Circulo[] = [];
    for (const r of this.regiones.values()) {
      this.quitar(r);
      circulos.push(...r.circulos);
    }
    quitarCirculos(circulos);
    this.regiones.clear();
    for (const g of this.porContenido.values()) g.geo?.dispose();
    this.grupos.clear();
    this.porContenido.clear();
    this.doc = null;
  }

  /** Los objetos de cada grupo en `doc` (crea los grupos de los assets que aún no tenían). */
  private contar(doc: MundoDoc): Map<Grupo, number> {
    const cuentas = new Map<Grupo, number>();
    for (const o of doc.objetos) {
      const g = this.grupoDe(doc, o.asset);
      if (g) cuentas.set(g, (cuentas.get(g) ?? 0) + 1);
    }
    return cuentas;
  }

  private grupoDe(doc: MundoDoc, id: string): Grupo | undefined {
    let g = this.grupos.get(id);
    if (g) return g;
    const a = doc.assets[id];
    if (!a) return undefined;
    const clave = JSON.stringify([a.tipo === "glb" ? a.glb : [a.piezas, a.malla], a.distancia]);
    g = this.porContenido.get(clave);
    if (!g) {
      g = { id: this.porContenido.size, asset: a, distancia: a.distancia ?? DIST_OBRA, n: 0, instanciado: false };
      this.porContenido.set(clave, g);
    }
    this.grupos.set(id, g);
    return g;
  }

  /** La región de un objeto: el GLB entero, la celda de vegetación de su grupo instanciado o la celda de obra de su distancia. */
  private region(g: Grupo, o: Objeto): string {
    if (g.asset.tipo === "glb") return `g${g.id}`;
    if (g.instanciado) return `i${g.id}:${celdaDe(o.pos[0], o.pos[2], CELDA_VEGETACION)}`;
    return `s${g.distancia}:${celdaDe(o.pos[0], o.pos[2], CELDA_OBRA)}`;
  }

  /** Monta las regiones de `doc` (solo las de `solo`, si se da; `previo` descarta antes, sin sacar la clave). */
  private montar(doc: MundoDoc, solo?: Set<string>, previo?: (g: Grupo, o: Objeto) => boolean): void {
    const porRegion = new Map<string, Objeto[]>();
    for (const o of doc.objetos) {
      const g = this.grupos.get(o.asset);
      if (!g || (previo && !previo(g, o))) continue;
      const k = this.region(g, o);
      if (solo && !solo.has(k)) continue;
      const l = porRegion.get(k);
      if (l) l.push(o);
      else porRegion.set(k, [o]);
    }
    for (const [k, objetos] of porRegion) this.montarRegion(doc, k, objetos);
  }

  private montarRegion(doc: MundoDoc, k: string, objetos: Objeto[]): void {
    const r: Region = { mallas: [], circulos: [], viva: true };
    this.regiones.set(k, r);
    for (const o of objetos) this.colisiones(r, doc.assets[o.asset], o);
    const g = this.grupos.get(objetos[0].asset) as Grupo;
    if (g.asset.tipo === "glb") {
      const sha = g.asset.glb;
      cargarGlb(sha).then(
        (modelo) => r.viva && this.instanciarModelo(r, modelo, objetos, g.distancia),
        (e: unknown) => console.warn(`No se ha podido cargar el modelo ${sha}`, e),
      );
    } else if (g.instanciado) this.instanciar(r, this.geometria(g), objetos, g.distancia);
    else {
      // Suelta: las copias de todos los grupos con esta distancia en esta celda, fundidas en una malla.
      const malla = new Mesh(fundir(objetos.map((o) => this.copia(this.geometria(this.grupos.get(o.asset) as Grupo), o))), this.material);
      malla.castShadow = true;
      malla.receiveShadow = true;
      malla.geometry.computeBoundingSphere();
      this.añadir(r, malla, g.distancia);
    }
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

  private colisiones(r: Region, a: Asset, o: Objeto): void {
    if (!a.colision) return;
    const cos = Math.cos(o.rot[1]);
    const sin = Math.sin(o.rot[1]);
    const radio = Math.max(o.esc[0], o.esc[2]);
    for (const [cx, cz, rc] of a.colision) {
      const x = cx * o.esc[0];
      const z = cz * o.esc[2];
      r.circulos.push(añadirCirculo(o.pos[0] + x * cos + z * sin, o.pos[2] - x * sin + z * cos, rc * radio));
    }
  }
}
