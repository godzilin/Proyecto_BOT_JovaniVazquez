// Culling: reparte lo fijo del mundo en celdas (para que three descarte cada una con su esfera, en la cámara y en la del sol)
// y oculta por distancia lo que ya no se aprecia, con histéresis; también dice qué objetos sueltos se ven este fotograma.
import { Frustum, InstancedMesh, Matrix4, Mesh, Sphere, Vector3 } from "three";
import type { Camera, Object3D } from "three";

const HISTERESIS = 0.05; // se oculta al pasar de d·1,05 y reaparece por debajo de d·0,95

export interface OpcionesRegistro {
  /** Radio de la esfera que envuelve al objeto, en metros. */
  radio: number;
  /** Distancia máxima a la cámara (hasta el borde de la esfera) a la que se ve. */
  distancia: number;
  /** Centro en el mundo; por omisión, la posición del objeto. */
  centro?: Vector3;
  /** Si es true, `objeto.visible` sigue a la distancia (el frustum lo descarta three con su `boundingSphere`). */
  ocultar?: boolean;
  /** Si es true, `escala` no cambia su distancia (el suelo: acortarlo dejaría huecos). */
  fija?: boolean;
}

interface Entrada {
  centro: Vector3;
  radio: number;
  distancia: number;
  ocultar: boolean;
  fija: boolean;
  cerca: boolean;
  dentro: boolean;
}

export class Culling {
  /** Multiplica todas las distancias: menos de 1 cuando `Calidad` baja la calidad. */
  escala = 1;
  private entradas = new Map<Object3D, Entrada>();
  private frustum = new Frustum();
  private m = new Matrix4();
  private esfera = new Sphere();

  registrar(objeto: Object3D, o: OpcionesRegistro): void {
    objeto.updateWorldMatrix(true, false);
    const centro = o.centro?.clone() ?? new Vector3().setFromMatrixPosition(objeto.matrixWorld);
    this.entradas.set(objeto, { centro, radio: o.radio, distancia: o.distancia, ocultar: o.ocultar ?? false, fija: o.fija ?? false, cerca: true, dentro: true });
  }

  /** Registra una malla con la esfera que ya tiene (la de su geometría o, en `InstancedMesh`, la de todas sus instancias). */
  registrarMalla(malla: Mesh | InstancedMesh, distancia: number, fija = false): void {
    const e = esferaDe(malla);
    this.registrar(malla, { radio: e.radius, distancia, centro: e.center, ocultar: true, fija });
  }

  quitar(objeto: Object3D): void {
    this.entradas.delete(objeto);
  }

  /** ¿Está dentro del frustum de la cámara y de su distancia? Calculado en el último `actualizar`. */
  visible(objeto: Object3D): boolean {
    return this.entradas.get(objeto)?.dentro ?? true;
  }

  /** Una vez por fotograma, con la cámara ya colocada. */
  actualizar(camara: Camera): void {
    camara.updateMatrixWorld();
    this.frustum.setFromProjectionMatrix(this.m.multiplyMatrices(camara.projectionMatrix, camara.matrixWorldInverse));
    const c = camara.position;
    for (const [objeto, e] of this.entradas) {
      const d = e.centro.distanceTo(c) - e.radio;
      const lim = e.fija ? e.distancia : e.distancia * this.escala;
      e.cerca = d < (e.cerca ? lim * (1 + HISTERESIS) : lim * (1 - HISTERESIS));
      e.dentro = e.cerca && this.frustum.intersectsSphere(this.esfera.set(e.centro, e.radio));
      if (e.ocultar) objeto.visible = e.cerca;
    }
  }
}

/** Esfera en el mundo de una malla (la geometría ya está en coordenadas del mundo en todas las de este archivo). */
function esferaDe(malla: Mesh | InstancedMesh): Sphere {
  let e: Sphere | null;
  if ((malla as InstancedMesh).isInstancedMesh) {
    const im = malla as InstancedMesh;
    if (!im.boundingSphere) im.computeBoundingSphere();
    e = im.boundingSphere;
  } else {
    if (!malla.geometry.boundingSphere) malla.geometry.computeBoundingSphere();
    e = malla.geometry.boundingSphere;
  }
  return e!.clone().applyMatrix4(malla.matrixWorld);
}

/** Clave de la celda de lado `celda` que contiene (x, z). */
export const celdaDe = (x: number, z: number, celda: number): string => `${Math.floor(x / celda)},${Math.floor(z / celda)}`;
