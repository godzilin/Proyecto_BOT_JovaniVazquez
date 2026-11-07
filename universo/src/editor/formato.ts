// Formato del mundo editable (par en TypeScript de `bot/services/universo_editor.py`): tipos, topes, geometría de cada forma básica y carga de GLB.
import { BoxGeometry, BufferAttribute, BufferGeometry, ConeGeometry, CylinderGeometry, Mesh, type Object3D, SphereGeometry } from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { fundir, pintar, poner } from "../mundo/geom";

export type Vec3 = [number, number, number];
export type Forma = "caja" | "cuna" | "cilindro" | "cono" | "esfera";

export interface Pieza {
  forma: Forma;
  pos: Vec3;
  /** Radianes, orden YXZ (como `poner`). */
  rot: Vec3;
  esc: Vec3;
  /** 0xRRGGBB. */
  color: number;
}
export type Asset =
  | { nombre: string; tipo: "piezas"; piezas: Pieza[]; colision: number | null }
  | { nombre: string; tipo: "glb"; glb: string; colision: number | null };
export interface Objeto {
  asset: string;
  pos: Vec3;
  rot: Vec3;
  esc: Vec3;
}
export interface Zona {
  x: number;
  z: number;
  r: number;
}
export interface MundoDoc {
  v: 1;
  assets: Record<string, Asset>;
  objetos: Objeto[];
  zonas: Record<string, Zona>;
}

// Los mismos números que en Python; los vigila tests/unit/test_universo_formato_ts.py.
export const FORMATO = 1;
export const TRIANGULOS: Record<Forma, number> = { caja: 12, cuna: 8, cilindro: 48, cono: 24, esfera: 168 };
export const FORMAS = Object.keys(TRIANGULOS) as Forma[];
export const MAX_PIEZAS = 300;
export const MAX_ASSETS = 400;
export const MAX_TRIANGULOS_GLB = 6000;
export const MAX_NOMBRE = 40;
export const ID = /^[a-z0-9_-]{1,40}$/;

/** Rampa: base en y=-0.5, cara vertical en z=-0.5 y pendiente hasta z=+0.5. 8 triángulos. */
function cuna(): BufferGeometry {
  const A = [-0.5, -0.5, -0.5], B = [-0.5, -0.5, 0.5], C = [-0.5, 0.5, -0.5];
  const A2 = [0.5, -0.5, -0.5], B2 = [0.5, -0.5, 0.5], C2 = [0.5, 0.5, -0.5];
  const tris = [A, B, C, A2, C2, B2, A, A2, B2, A, B2, B, A, C, C2, A, C2, A2, B, B2, C2, B, C2, C];
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(new Float32Array(tris.flat()), 3));
  g.computeVertexNormals();
  return g;
}

/** Geometría de una forma básica: 1 m de lado, centrada en el origen. */
export function geometriaForma(forma: Forma): BufferGeometry {
  switch (forma) {
    case "caja":
      return new BoxGeometry(1, 1, 1);
    case "cuna":
      return cuna();
    case "cilindro":
      return new CylinderGeometry(0.5, 0.5, 1, 12);
    case "cono":
      return new ConeGeometry(0.5, 1, 12);
    case "esfera":
      return new SphereGeometry(0.5, 12, 8);
  }
}

export function contarTriangulos(g: BufferGeometry): number {
  return (g.index ? g.index.count : g.getAttribute("position").count) / 3;
}

/** Falla si alguna forma no tiene los triángulos que dice `TRIANGULOS` (y valida el servidor). */
export function comprobarFormas(): void {
  for (const f of FORMAS) {
    const g = geometriaForma(f);
    const n = contarTriangulos(g);
    g.dispose();
    if (n !== TRIANGULOS[f]) throw new Error(`La forma ${f} tiene ${n} triángulos y el formato dice ${TRIANGULOS[f]}`);
  }
}

/** Todas las piezas fundidas en una geometría con color por vértice (lo que dibuja el mundo). */
export function geometriaPiezas(piezas: Pieza[]): BufferGeometry {
  return fundir(piezas.map((p) => poner(pintar(geometriaForma(p.forma), p.color), p.pos, p.rot, p.esc)));
}

const cargador = new GLTFLoader();
const modelos = new Map<string, Promise<Object3D>>();

/** Modelo de un GLB subido, con caché por resumen. Quien lo use lo clona (`clone(true)`) y no lo libera. */
export function cargarGlb(sha: string): Promise<Object3D> {
  let m = modelos.get(sha);
  if (!m) {
    m = cargador.loadAsync(`/api/glb/${sha}`).then((g) => g.scene);
    m.catch(() => modelos.delete(sha));
    modelos.set(sha, m);
  }
  return m;
}

export function triangulosModelo(o: Object3D): number {
  let n = 0;
  o.traverse((x) => {
    if ((x as Mesh).isMesh) n += contarTriangulos((x as Mesh).geometry);
  });
  return n;
}
