// Formato del mundo editable (par en TypeScript de `bot/services/universo_editor.py`): tipos, topes, geometría de cada forma básica y carga de GLB.
import {
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  Color,
  ConeGeometry,
  CylinderGeometry,
  DodecahedronGeometry,
  IcosahedronGeometry,
  Mesh,
  type Object3D,
  SphereGeometry,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { fundir, pintar, poner } from "../mundo/geom";

export type Vec3 = [number, number, number];
export type Forma = "caja" | "cuna" | "cilindro" | "cono" | "esfera" | "icosaedro" | "dodecaedro" | "prisma";

export interface Pieza {
  forma: Forma;
  pos: Vec3;
  /** Radianes, orden YXZ (como `poner`). */
  rot: Vec3;
  esc: Vec3;
  /** 0xRRGGBB. */
  color: number;
  /** Solo el prisma: lados (de `MIN_LADOS` a `MAX_LADOS`), radio de cada tapa (0 a 1) y giro de los lados en radianes. */
  lados?: number;
  arriba?: number;
  abajo?: number;
  fase?: number;
}
/** Geometría ya hecha: posiciones en milímetros enteros y un color 0xRRGGBB por vértice, triángulos sueltos. */
export interface Malla {
  pos: number[];
  color: number[];
}
/** Círculo de colisión [x, z, radio] en las coordenadas del asset. */
export type Circulo = [number, number, number];
/**
 * Lo que hace un asset además de verse. `traga`: una tragaperras jugable, con la pantalla y la botonera en su sitio del marco de
 * la máquina (`casino/sitio.ts`). `letrero`: un letrero con su panel (centro en `pos`, `ancho` × `alto` m, mirando a +z).
 */
export type Funcion = { tipo: "traga" } | { tipo: "letrero"; pos: Vec3; ancho: number; alto: number };
interface AssetComun {
  nombre: string;
  colision: Circulo[] | null;
  /** Metros hasta donde se dibuja; sin ella, la de las construcciones. */
  distancia?: number;
  funcion?: Funcion;
}
export type Asset = (AssetComun & { tipo: "piezas"; piezas: Pieza[]; malla?: Malla }) | (AssetComun & { tipo: "glb"; glb: string });
export interface Objeto {
  asset: string;
  pos: Vec3;
  rot: Vec3;
  esc: Vec3;
  /** Multiplica los colores del objeto. */
  tinte?: number;
  /** Lo propio de un objeto con función: `n` en una tragaperras; `titulo` y `botes` en un letrero. */
  datos?: Datos;
}
export interface Datos {
  /** Número de la tragaperras, de 1 a `MAX_TRAGAS`: el servidor guarda con él su calor, su barra y sus logros. */
  n?: number;
  titulo?: string;
  /** Claves de los botes que enseña el letrero (`bot/services/universo_botes.py`), que rotan si hay varios. */
  botes?: string[];
}
export interface Zona {
  x: number;
  z: number;
  r: number;
}
/** Un sitio del panel «Viajar» del menú: se aparece en (x, z), a ras de suelo. */
export interface Teleporte {
  nombre: string;
  x: number;
  z: number;
}
/**
 * El terreno de una celda: rejilla de `CELDA` / 2 + 1 vértices por lado cada 2 m desde su esquina (-x -z), fila a fila, en base64,
 * con los bordes repetidos en la celda de al lado. `alturas`: enteros de 16 bits (little-endian) en centímetros sobre `base` (cm; sin
 * ella, 0): la altura de un vértice es `base + alturas[i]`. `colores`: un byte por vértice, 0 el color natural y de 1 a
 * `TERRENO_PINTURAS` la paleta de `mundo/terreno.ts`. Sin terreno, la isla de serie (o el mar fuera de ella).
 */
export interface Terreno {
  alturas: string;
  colores: string;
  base?: number;
}
export interface MundoDoc {
  v: 2;
  assets: Record<string, Asset>;
  objetos: Objeto[];
  zonas: Record<string, Zona>;
  /** Sin la lista, los de serie (`teleportesDeSerie`); con la lista vacía, ninguno. */
  teleportes?: Teleporte[];
  /** El terreno de cada celda que lo tiene, por su clave `c/<cx>/<cz>`. */
  terreno?: Record<string, Terreno>;
}

// Los mismos números que en Python; los vigila tests/unit/test_universo_formato_ts.py.
export const FORMATO = 2;
export const TRIANGULOS: Record<Forma, number> = { caja: 12, cuna: 8, cilindro: 48, cono: 24, esfera: 168, icosaedro: 20, dodecaedro: 36, prisma: 0 };
export const FORMAS = Object.keys(TRIANGULOS) as Forma[];
export const MAX_PIEZAS = 300;
export const MAX_OBJETOS_CELDA = 4000;
export const MAX_TRIANGULOS_GLB = 6000;
export const MAX_NOMBRE = 40;
export const MIN_LADOS = 3;
export const MAX_LADOS = 32;
export const MAX_TRIANGULOS_MALLA = 2000;
export const MAX_CIRCULOS = 16;
export const MAX_TELEPORTES = 24;
export const MAX_TRAGAS = 24;
export const MAX_BOTES_LETRERO = 8;
export const MAX_TITULO = 40;
export const MAX_LADO_LETRERO = 40;
export const MIN_TINTE = 0.3;
export const MAX_TINTE = 2.0;
export const MIN_DISTANCIA = 20.0;
export const MAX_DISTANCIA = 600.0;
export const TERRENO_LADO = 320;
export const TERRENO_CELDAS = 160;
export const TERRENO_PINTURAS = 13;
/** Lado en metros de una celda del mundo: el mundo se guarda y se descarga en trozos de este tamaño (`CELL` en Python). */
export const CELDA = 80;
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

type ParametrosForma = Pick<Pieza, "forma" | "lados" | "arriba" | "abajo" | "fase">;

/** Triángulos de una pieza (el prisma, según sus lados y sus tapas; como `piece_triangles` de Python). */
export function triangulosPieza(p: ParametrosForma): number {
  if (p.forma !== "prisma") return TRIANGULOS[p.forma];
  return 2 * (p.lados ?? 0) * (Number((p.arriba ?? 0) > 0) + Number((p.abajo ?? 0) > 0));
}

/** Geometría de una forma básica: 1 m de lado, centrada en el origen. */
export function geometriaForma(p: ParametrosForma): BufferGeometry {
  switch (p.forma) {
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
    case "icosaedro":
      return new IcosahedronGeometry(0.5, 0);
    case "dodecaedro":
      return new DodecahedronGeometry(0.5, 0);
    case "prisma":
      return new CylinderGeometry(0.5 * (p.arriba ?? 1), 0.5 * (p.abajo ?? 1), 1, p.lados ?? 6, 1, false, p.fase ?? 0);
  }
}

export function contarTriangulos(g: BufferGeometry): number {
  return (g.index ? g.index.count : g.getAttribute("position").count) / 3;
}

/** Falla si alguna forma no tiene los triángulos que dice `TRIANGULOS` (y valida el servidor). */
export function comprobarFormas(): void {
  const muestras: ParametrosForma[] = [
    ...FORMAS.filter((f) => f !== "prisma").map((forma) => ({ forma })),
    { forma: "prisma", lados: 6, arriba: 1, abajo: 1, fase: 0 },
    { forma: "prisma", lados: 5, arriba: 0, abajo: 0.8, fase: 0.3 },
  ];
  for (const p of muestras) {
    const g = geometriaForma(p);
    const n = contarTriangulos(g);
    g.dispose();
    if (n !== triangulosPieza(p)) throw new Error(`La forma ${p.forma} tiene ${n} triángulos y el formato dice ${triangulosPieza(p)}`);
  }
}

/** Triángulos de una `Malla`. */
export const triangulosMalla = (m: Malla): number => m.pos.length / 9;

/** La malla hecha como geometría no indexada con color por vértice. */
export function geometriaMalla(m: Malla): BufferGeometry {
  const pos = new Float32Array(m.pos.length);
  for (let i = 0; i < pos.length; i++) pos[i] = m.pos[i] / 1000;
  const col = new Float32Array(m.color.length * 3);
  const c = new Color();
  m.color.forEach((hex, i) => {
    c.setHex(hex);
    col[i * 3] = c.r;
    col[i * 3 + 1] = c.g;
    col[i * 3 + 2] = c.b;
  });
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(pos, 3));
  g.setAttribute("color", new BufferAttribute(col, 3));
  g.computeVertexNormals();
  return g;
}

/** La geometría de un asset de piezas: sus piezas fundidas más su malla, en una sola, sin uv. */
export function geometriaAsset(a: Extract<Asset, { tipo: "piezas" }>): BufferGeometry {
  const partes = a.piezas.map((p) => poner(pintar(geometriaForma(p), p.color), p.pos, p.rot, p.esc));
  if (a.malla) partes.push(geometriaMalla(a.malla));
  return fundir(partes);
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
