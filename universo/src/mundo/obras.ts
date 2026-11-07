// Dibuja el mundo publicado desde el editor (`MundoDoc`): cada asset se monta una vez; los muy repetidos van instanciados por celdas
// y el resto se funde por celdas, todo con culling. Sin documento (o vacío) no añade ni una malla. Los círculos de colisión de cada
// asset pasan al mundo con la posición, el giro en y y la escala de cada objeto.
import { BufferGeometry, Color, Euler, InstancedMesh, Matrix4, Mesh, type Material, type Object3D, Quaternion, type Scene, Vector3 } from "three";
import { type Asset, cargarGlb, geometriaAsset, type MundoDoc, type Objeto } from "../editor/formato";
import { añadirCirculo, type Circulo, quitarCirculos } from "./colision";
import { celdaDe, type Culling, mallasPorCeldas } from "./culling";
import { CELDA_OBRA, CELDA_VEGETACION, DIST_OBRA } from "./medidas";

/** Desde cuántos objetos iguales compensa una malla instanciada en lugar de copias fundidas. */
const INSTANCIAR = 8;

const euler = new Euler();
const quat = new Quaternion();
const uno = new Vector3();

/** La matriz de un objeto del documento (posición, giro YXZ y escala). */
export function matriz(o: Objeto): Matrix4 {
  quat.setFromEuler(euler.set(o.rot[0], o.rot[1], o.rot[2], "YXZ"));
  return new Matrix4().compose(uno.set(...o.pos), quat, new Vector3(...o.esc));
}

export class Obras {
  private mallas: (Mesh | InstancedMesh)[] = [];
  private geometrias: BufferGeometry[] = [];
  private circulos: Circulo[] = [];
  private generacion = 0;

  constructor(
    private readonly escena: Scene,
    private readonly culling: Culling,
    private readonly material: Material,
  ) {}

  /** Sustituye todo lo dibujado por el contenido de `doc`. */
  cargar(doc: MundoDoc): void {
    this.vaciar();
    const gen = this.generacion;
    // Los assets que solo se diferencian en la colisión se dibujan juntos (una malla instanciada por celda, no una por asset).
    const usos = new Map<string, Objeto[]>();
    for (const o of doc.objetos) {
      if (!doc.assets[o.asset]) continue;
      const l = usos.get(o.asset);
      if (l) l.push(o);
      else usos.set(o.asset, [o]);
    }
    const grupos = new Map<string, { asset: Asset; objetos: Objeto[] }>();
    for (const [id, objetos] of usos) {
      const a = doc.assets[id];
      this.colisiones(a, objetos);
      const clave = JSON.stringify([a.tipo === "glb" ? a.glb : [a.piezas, a.malla], a.distancia]);
      const g = grupos.get(clave);
      if (g) g.objetos.push(...objetos);
      else grupos.set(clave, { asset: a, objetos });
    }
    const sueltas = new Map<number, BufferGeometry[]>();
    for (const { asset: a, objetos } of grupos.values()) {
      const distancia = a.distancia ?? DIST_OBRA;
      if (a.tipo === "glb") {
        const sha = a.glb;
        cargarGlb(sha).then(
          (modelo) => gen === this.generacion && this.instanciarModelo(modelo, objetos, distancia),
          (e: unknown) => console.warn(`No se ha podido cargar el modelo ${sha}`, e),
        );
        continue;
      }
      const g = geometriaAsset(a);
      if (objetos.length >= INSTANCIAR) {
        this.geometrias.push(g);
        this.instanciar(g, objetos, distancia);
      } else {
        const lista = sueltas.get(distancia) ?? [];
        for (const o of objetos) lista.push(this.copia(g, o));
        sueltas.set(distancia, lista);
        g.dispose();
      }
    }
    for (const [distancia, lista] of sueltas) {
      for (const m of mallasPorCeldas(lista, CELDA_OBRA, this.material)) this.añadir(m, distancia);
    }
  }

  /** Quita de la escena, del culling y de las colisiones todo lo que se cargó. El material es compartido y no se libera. */
  vaciar(): void {
    this.generacion++;
    for (const m of this.mallas) {
      this.escena.remove(m);
      this.culling.quitar(m);
      if (!(m as InstancedMesh).isInstancedMesh) m.geometry.dispose(); // las instanciadas comparten la geometría del asset
      else (m as InstancedMesh).dispose();
    }
    this.mallas = [];
    this.geometrias.forEach((g) => g.dispose());
    this.geometrias = [];
    quitarCirculos(this.circulos);
    this.circulos = [];
  }

  private añadir(m: Mesh | InstancedMesh, distancia: number): void {
    this.escena.add(m);
    this.culling.registrarMalla(m, distancia);
    this.mallas.push(m);
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

  /** Una malla instanciada por celda de `CELDA_VEGETACION`, como la vegetación. */
  private instanciar(g: BufferGeometry, objetos: Objeto[], distancia: number): void {
    const celdas = new Map<string, Objeto[]>();
    for (const o of objetos) {
      const k = celdaDe(o.pos[0], o.pos[2], CELDA_VEGETACION);
      const c = celdas.get(k);
      if (c) c.push(o);
      else celdas.set(k, [o]);
    }
    const color = new Color();
    for (const items of celdas.values()) {
      const m = new InstancedMesh(g, this.material, items.length);
      items.forEach((o, i) => {
        m.setMatrixAt(i, matriz(o));
        m.setColorAt(i, color.setScalar(o.tinte ?? 1));
      });
      m.instanceMatrix.needsUpdate = true;
      m.castShadow = true;
      m.receiveShadow = true;
      m.computeBoundingSphere();
      this.añadir(m, distancia);
    }
  }

  /** Un GLB: una malla instanciada por cada `Mesh` del modelo, con todas las instancias (matriz del objeto por la del nodo). */
  private instanciarModelo(modelo: Object3D, objetos: Objeto[], distancia: number): void {
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
      this.añadir(m, distancia);
    });
  }

  private colisiones(a: Asset, objetos: Objeto[]): void {
    if (!a.colision) return;
    for (const o of objetos) {
      const cos = Math.cos(o.rot[1]);
      const sin = Math.sin(o.rot[1]);
      const radio = Math.max(o.esc[0], o.esc[2]);
      for (const [cx, cz, r] of a.colision) {
        const x = cx * o.esc[0];
        const z = cz * o.esc[2];
        this.circulos.push(añadirCirculo(o.pos[0] + x * cos + z * sin, o.pos[2] - x * sin + z * cos, r * radio));
      }
    }
  }
}
