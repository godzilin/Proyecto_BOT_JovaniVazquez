// El terreno de la isla: altura analítica (heightAt), disposición de los lugares, malla con color por vértice
// y un suelo interpolado idéntico a la malla para que los pies no floten ni se hundan. La rejilla sale de la
// fórmula o, si el mundo publicado trae un terreno esculpido en el editor, de sus datos (`fijarTerreno`).
import { BufferAttribute, BufferGeometry, Color, DataTexture, LinearFilter, Mesh, MeshLambertMaterial, RedFormat, UnsignedByteType } from "three";
import { TERRENO_CELDAS, TERRENO_LADO, TERRENO_PINTURAS, type Terreno } from "../editor/formato";
import type { Culling } from "./culling";
import { PRUEBAS } from "./modo";
import { alturaPista } from "./pista";
import { clamp, fbm, lerp, ruido, smoothstep } from "./ruido";

export const NIVEL_MAR = 0;
export const LADO = TERRENO_LADO; // metros de malla; el resto es mar profundo
export const CELDAS = TERRENO_CELDAS;
export const PASO = LADO / CELDAS;
export const MITAD = LADO / 2;
export const SUELO_AGUA = -1.1; // hasta aquí se puede vadear

/** Radio de la costa en la dirección unitaria (ux, uz), con forma irregular sin usar trigonometría. */
export function costaRadio(ux: number, uz: number): number {
  return 118 + 28 * (fbm(ux * 1.7 + 7, uz * 1.7 + 3, 2) - 0.5);
}
/** Punto de la costa en el ángulo dado (grados), metido `dentro` metros hacia el interior. */
export function costaPunto(grados: number, dentro: number): { x: number; z: number } {
  const a = (grados * Math.PI) / 180;
  const ux = Math.cos(a);
  const uz = Math.sin(a);
  const r = costaRadio(ux, uz) - dentro;
  return { x: ux * r, z: uz * r };
}

// Disposición (N = +z, E = +x). Gran Canaria estilizada: macizo central, caldera, dunas, puertos y acantilados.
export const PICO = { x: -5, z: 8, radio: 95, alto: 42 };
export const NUBLO = { x: 16, z: 12 };
export const BENTAYGA = { x: -40, z: -22 };
const BAND_P = costaPunto(40, 42);
export const BAND = { x: BAND_P.x, z: BAND_P.z, alt: 13, radio: 14, fondo: 11 };
const DUN_P = costaPunto(-90, 28);
export const DUNAS = { x: DUN_P.x, z: DUN_P.z };
export const CHARCA = { x: DUN_P.x + 17, z: DUN_P.z + 10 };
const FARO_P = costaPunto(-70, 4);
export const FARO = { x: FARO_P.x, z: FARO_P.z };
export const PLAZA = { x: -8, z: 60, alt: 8 };
const CANT_P = costaPunto(62, 6);
export const CANTERAS = { x: CANT_P.x, z: CANT_P.z };
export const MOGAN = costaPunto(225, 0);
const ACANTILADO = { x: Math.cos((150 * Math.PI) / 180), z: Math.sin((150 * Math.PI) / 180) };

/** Peso del acantilado (oeste/noroeste) para la dirección unitaria dada. */
export function pesoAcantilado(ux: number, uz: number): number {
  return smoothstep(0.55, 0.85, ux * ACANTILADO.x + uz * ACANTILADO.z);
}

const bulto = (x: number, z: number, cx: number, cz: number, sigma: number, a: number): number =>
  a * Math.exp(-((x - cx) ** 2 + (z - cz) ** 2) / (2 * sigma * sigma));

/** Altura natural del terreno en metros sobre el mar. Función pura y determinista. */
export function heightAt(x: number, z: number): number {
  const r = Math.hypot(x, z) || 1e-6;
  const ux = x / r;
  const uz = z / r;
  const R = costaRadio(ux, uz);
  const d = r - R;
  let h: number;
  if (d < 0) {
    const m = -d;
    h = 5.5 * smoothstep(0, 36, m) + 0.03 * Math.min(m, 30);
    h += (fbm(x * 0.022, z * 0.022, 3) - 0.5) * 7 * smoothstep(8, 40, m);
    h += (fbm(x * 0.09 + 40, z * 0.09, 2) - 0.5) * 1.2 * smoothstep(5, 25, m);
    // Macizo central que sube hacia el Pico de las Nieves, con barrancos radiales que bajan al mar.
    const dp = Math.hypot(x - PICO.x, z - PICO.z);
    if (dp < PICO.radio) {
      const q = Math.sqrt(dp * dp + 36);
      let cono = PICO.alto * Math.pow(1 - q / (PICO.radio + 6), 1.5);
      cono *= 1 + (fbm(x * 0.05 + 11, z * 0.05 + 5, 3) - 0.5) * 0.3;
      const cresta = 1 - Math.abs(2 * fbm(ux * 4.2 + 5, uz * 4.2 + 2, 2) - 1);
      cono -= 6 * Math.pow(cresta, 3) * smoothstep(20, 60, r) * smoothstep(0, 18, m);
      h += cono * smoothstep(0, 25, m);
    }
    h += bulto(x, z, PICO.x, PICO.z, 7, 5);
    // Acantilados altos al oeste y noroeste: una pared oscura que cae al mar.
    const wc = pesoAcantilado(ux, uz);
    h += wc * 30 * smoothstep(0, 9, m) * (1 - smoothstep(55, 95, m));
    // Caldera de Bandama: terreno llano con un cuenco redondo.
    const db = Math.hypot(x - BAND.x, z - BAND.z);
    h = lerp(h, BAND.alt, 1 - smoothstep(24, 40, db));
    h -= BAND.fondo * (1 - smoothstep(0, BAND.radio - 2, db));
    h += 1.6 * Math.exp(-(((db - BAND.radio) / 2.5) ** 2));
    // Dunas de Maspalomas: arena baja con crestas, y una charca al lado.
    const dd = Math.hypot(x - DUNAS.x, z - DUNAS.z);
    const md = 1 - smoothstep(22, 42, dd);
    if (md > 0) {
      h = lerp(h, 2.6, md);
      h += 7 * Math.pow(1 - Math.abs(2 * ruido(x * 0.035 + 9, z * 0.065 + 4) - 1), 1.6) * md * smoothstep(3, 14, m) * smoothstep(7, 24, Math.hypot(x - CHARCA.x, z - CHARCA.z));
      const dc = Math.hypot(x - CHARCA.x, z - CHARCA.z);
      h = lerp(h, -0.9, 1 - smoothstep(4, 13, dc));
    }
  } else {
    h = -10 * (1 - Math.exp(-d / 35));
  }
  h += bulto(x, z, FARO.x, FARO.z, 9, 3);
  h += bulto(x, z, NUBLO.x, NUBLO.z, 12, 5);
  const dpl = Math.hypot(x - PLAZA.x, z - PLAZA.z);
  h = lerp(h, PLAZA.alt, 1 - smoothstep(30, 50, dpl));
  return h;
}

// --- Muelle: su cubierta cuenta como suelo ---
const PUNTO_MUELLE = costaPunto(225, 0);
const DIR_MUELLE = (() => {
  const r = Math.hypot(PUNTO_MUELLE.x, PUNTO_MUELLE.z);
  return { x: PUNTO_MUELLE.x / r, z: PUNTO_MUELLE.z / r };
})();
export const MUELLE = (() => {
  // Empieza donde la arena baja de 1 m, mirando al mar.
  let t = -30;
  while (heightAt(PUNTO_MUELLE.x + DIR_MUELLE.x * t, PUNTO_MUELLE.z + DIR_MUELLE.z * t) > 1.0 && t < 20) t += 0.5;
  const largo = 26;
  const x0 = PUNTO_MUELLE.x + DIR_MUELLE.x * (t - 3);
  const z0 = PUNTO_MUELLE.z + DIR_MUELLE.z * (t - 3);
  return {
    x0,
    z0,
    dx: DIR_MUELLE.x,
    dz: DIR_MUELLE.z,
    largo,
    ancho: 3.2,
    alt: 1.1,
    cx: x0 + DIR_MUELLE.x * largo * 0.55,
    cz: z0 + DIR_MUELLE.z * largo * 0.55,
  };
})();

export function enMuelle(x: number, z: number): boolean {
  if (PRUEBAS) return false;
  const px = x - MUELLE.x0;
  const pz = z - MUELLE.z0;
  const a = px * MUELLE.dx + pz * MUELLE.dz;
  const c = Math.abs(-px * MUELLE.dz + pz * MUELLE.dx);
  return a >= 0 && a <= MUELLE.largo && c <= MUELLE.ancho / 2;
}

// --- La rejilla: alturas y pintura de cada vértice ---
export const N1 = CELDAS + 1;
/** Altura de cada vértice (metros), fila a fila desde -x -z. El editor la esculpe en sitio y avisa con `Suelo.actualizar`. */
export const alturas = new Float32Array(N1 * N1);
/** Pintura de cada vértice: 0 es el color natural; de 1 en adelante, `PALETA[n - 1]`. */
export const pinturas = new Uint8Array(N1 * N1);
export const MIN_ALTURA = -20;
export const MAX_ALTURA = 120;
let fijado: Terreno | undefined;

function deSerie(): void {
  for (let iz = 0; iz < N1; iz++) for (let ix = 0; ix < N1; ix++) alturas[iz * N1 + ix] = heightAt(ix * PASO - MITAD, iz * PASO - MITAD);
  pinturas.fill(0);
}
deSerie();

const aBytes = (b64: string): Uint8Array => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
function aBase64(b: Uint8Array): string {
  let s = "";
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000));
  return btoa(s);
}

/**
 * Pone la rejilla del mundo publicado (o la de serie sin terreno). Devuelve si ha cambiado algo, para rehacer la malla
 * y el mapa del mar solo entonces. Un terreno que no cuadra con la rejilla se descarta (el servidor ya lo valida).
 */
export function fijarTerreno(t: Terreno | undefined): boolean {
  if (PRUEBAS || (t?.alturas === fijado?.alturas && t?.colores === fijado?.colores)) return false;
  fijado = t;
  const h = t && aBytes(t.alturas);
  const c = t && aBytes(t.colores);
  if (!h || !c || h.length !== 2 * N1 * N1 || c.length !== N1 * N1) {
    deSerie();
    return true;
  }
  const v = new DataView(h.buffer);
  for (let i = 0; i < N1 * N1; i++) alturas[i] = v.getInt16(i * 2, true) / 100;
  pinturas.set(c.map((x) => (x > TERRENO_PINTURAS ? 0 : x)));
  return true;
}

/** La rejilla tal como está, en el formato del documento (alturas en centímetros). */
export function terrenoDoc(): Terreno {
  const h = new Uint8Array(N1 * N1 * 2);
  const v = new DataView(h.buffer);
  for (let i = 0; i < N1 * N1; i++) v.setInt16(i * 2, Math.round(clamp(alturas[i], MIN_ALTURA, MAX_ALTURA) * 100), true);
  const t = { alturas: aBase64(h), colores: aBase64(pinturas) };
  fijado = t; // es la que hay: al volver con el documento no se rehace nada
  return t;
}

/** Altura interpolada en una rejilla de alturas (la de ahora u otra copia, como la de antes de un trazo). */
export function alturaEn(rejilla: Float32Array, x: number, z: number): number {
  const gx = (x + MITAD) / PASO;
  const gz = (z + MITAD) / PASO;
  if (gx < 0 || gz < 0 || gx >= CELDAS || gz >= CELDAS) return -10;
  const ix = Math.floor(gx);
  const iz = Math.floor(gz);
  const u = gx - ix;
  const v = gz - iz;
  const h00 = rejilla[iz * N1 + ix];
  const h10 = rejilla[iz * N1 + ix + 1];
  const h01 = rejilla[(iz + 1) * N1 + ix];
  const h11 = rejilla[(iz + 1) * N1 + ix + 1];
  return u + v <= 1 ? h00 + (h10 - h00) * u + (h01 - h00) * v : h11 + (h01 - h11) * (1 - u) + (h10 - h11) * (1 - v);
}

/** Altura del terreno tal y como se dibuja (sin cubierta del muelle). */
export function terrenoEn(x: number, z: number): number {
  if (PRUEBAS) return alturaPista(x, z); // el extraplano: suelo a 0 m y los bloques de la pista
  return alturaEn(alturas, x, z);
}

/** Suelo sobre el que se camina: terreno o cubierta del muelle, con el fondo del vadeo como mínimo. */
export function sueloEn(x: number, z: number): number {
  const h = Math.max(terrenoEn(x, z), SUELO_AGUA);
  return enMuelle(x, z) ? Math.max(h, MUELLE.alt) : h;
}

export function pendienteEn(x: number, z: number): number {
  const e = 1;
  const gx = (terrenoEn(x + e, z) - terrenoEn(x - e, z)) / (2 * e);
  const gz = (terrenoEn(x, z + e) - terrenoEn(x, z - e)) / (2 * e);
  return Math.hypot(gx, gz);
}

// --- Color por vértice ---
const c = (hex: number): Color => new Color(hex);
const ARENA = c(0xe9c47c);
const ARENA_DUNA = c(0xf0cf86);
const FONDO = c(0xbfa77a);
const SECO = c(0xb59f5c);
const VERDE = c(0x8c9a52);
const PINAR = c(0x6b7d44);
const TIERRA = c(0x9a6a45);
const ALTA = c(0x8c7662);
const CENIZA = c(0x6a5a58);
const ROCA = c(0x85705f);
const ACANT = c(0x57493f);
const PIEDRA = c(0xd9c9a5);
const CAMINO = c(0xc9b48a);

/** Los colores con los que pinta el editor, en el orden de `pinturas` (el 1 es el primero). */
export const PALETA: { nombre: string; color: Color }[] = [
  { nombre: "Arena", color: ARENA },
  { nombre: "Duna", color: ARENA_DUNA },
  { nombre: "Fondo del mar", color: FONDO },
  { nombre: "Hierba seca", color: SECO },
  { nombre: "Hierba", color: VERDE },
  { nombre: "Pinar", color: PINAR },
  { nombre: "Tierra", color: TIERRA },
  { nombre: "Cumbre", color: ALTA },
  { nombre: "Ceniza", color: CENIZA },
  { nombre: "Roca", color: ROCA },
  { nombre: "Acantilado", color: ACANT },
  { nombre: "Piedra", color: PIEDRA },
  { nombre: "Camino", color: CAMINO },
];
if (PALETA.length !== TERRENO_PINTURAS) throw new Error("La paleta del terreno no cuadra con TERRENO_PINTURAS");

function colorEn(x: number, z: number, h: number, pend: number, pintura: number): Color {
  const out = new Color();
  if (pintura) return out.copy(PALETA[pintura - 1].color).multiplyScalar(0.94 + fbm(x * 0.7, z * 0.7, 1) * 0.12);
  const r = Math.hypot(x, z) || 1e-6;
  const dp = Math.hypot(x - PICO.x, z - PICO.z);
  out.copy(SECO).lerp(VERDE, smoothstep(0.35, 0.65, fbm(x * 0.045 + 3, z * 0.045 - 8, 2)));
  out.lerp(PINAR, smoothstep(80, 55, dp) * smoothstep(8, 14, h) * smoothstep(0.1, 0.3, fbm(x * 0.03, z * 0.03, 2)));
  out.lerp(TIERRA, smoothstep(40, 25, dp) * 0.7);
  out.lerp(ALTA, smoothstep(26, 12, dp));
  out.lerp(ROCA, smoothstep(0.55, 0.95, pend));
  out.lerp(ACANT, pesoAcantilado(x / r, z / r) * smoothstep(0.6, 1.3, pend));
  const db = Math.hypot(x - BAND.x, z - BAND.z);
  out.lerp(CENIZA, (1 - smoothstep(BAND.radio - 2, BAND.radio + 3, db)) * 0.8);
  const dpl = Math.hypot(x - PLAZA.x, z - PLAZA.z);
  out.lerp(CAMINO, smoothstep(30, 18, dpl) * 0.55);
  out.lerp(PIEDRA, smoothstep(14, 10, dpl));
  out.lerp(ARENA, 1 - smoothstep(1.3, 2.6, h));
  const dd = Math.hypot(x - DUNAS.x, z - DUNAS.z);
  out.lerp(ARENA_DUNA, 1 - smoothstep(24, 36, dd));
  const dcant = Math.hypot(x - CANTERAS.x, z - CANTERAS.z);
  out.lerp(ARENA, (1 - smoothstep(10, 22, dcant)) * (1 - smoothstep(3, 5, h)));
  out.lerp(FONDO, smoothstep(0.1, -0.8, h) * 0.8);
  return out.multiplyScalar(0.94 + fbm(x * 0.7, z * 0.7, 1) * 0.12);
}

/** La malla del terreno: sus baldosas y cómo ponerla al día cuando cambia la rejilla. */
export interface Suelo {
  baldosas: Mesh[];
  /** Vuelve a leer la rejilla entre esos vértices (incluidos); sin argumentos, entera. */
  actualizar(ix0?: number, iz0?: number, ix1?: number, iz1?: number): void;
}

/**
 * El terreno en `teselas` × `teselas` baldosas (divisor de 160), para que three descarte las que no se ven. Cada baldosa
 * copia sus vértices de la rejilla entera (las juntas coinciden) y se registra en el culling con su esfera.
 */
export function crearTerreno(teselas: number, culling: Culling, distancia: number): Suelo {
  const lado = CELDAS / teselas;
  const n = lado + 1;
  const material = new MeshLambertMaterial({ vertexColors: true, flatShading: true });
  const idx = new Uint16Array(lado * lado * 6);
  let k = 0;
  for (let iz = 0; iz < lado; iz++) {
    for (let ix = 0; ix < lado; ix++) {
      const v00 = iz * n + ix;
      const v10 = v00 + 1;
      const v01 = v00 + n;
      const v11 = v01 + 1;
      idx.set([v00, v01, v10, v10, v01, v11], k);
      k += 6;
    }
  }
  const baldosas: Mesh[] = [];
  for (let t = 0; t < teselas * teselas; t++) {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(n * n * 3), 3));
    g.setAttribute("color", new BufferAttribute(new Float32Array(n * n * 3), 3));
    g.setIndex(new BufferAttribute(idx, 1));
    const mesh = new Mesh(g, material);
    mesh.receiveShadow = true;
    baldosas.push(mesh);
  }
  // Las baldosas en las que está cada fila (o columna) de vértices: dos en las juntas.
  const de = (i: number): number[] => {
    const a = Math.min(Math.floor(i / lado), teselas - 1);
    const b = Math.max(Math.ceil(i / lado) - 1, 0);
    return a === b ? [a] : [b, a];
  };

  const actualizar = (ix0 = 0, iz0 = 0, ix1 = CELDAS, iz1 = CELDAS): void => {
    ix0 = Math.max(ix0, 0);
    iz0 = Math.max(iz0, 0);
    ix1 = Math.min(ix1, CELDAS);
    iz1 = Math.min(iz1, CELDAS);
    const tocadas = new Set<number>();
    for (let iz = iz0; iz <= iz1; iz++) {
      for (let ix = ix0; ix <= ix1; ix++) {
        const i = iz * N1 + ix;
        const x = ix * PASO - MITAD;
        const z = iz * PASO - MITAD;
        const h = alturas[i];
        const hx = alturas[iz * N1 + Math.min(ix + 1, CELDAS)] - alturas[iz * N1 + Math.max(ix - 1, 0)];
        const hz = alturas[Math.min(iz + 1, CELDAS) * N1 + ix] - alturas[Math.max(iz - 1, 0) * N1 + ix];
        const co = colorEn(x, z, h, Math.hypot(hx, hz) / (2 * PASO), pinturas[i]);
        for (const tz of de(iz)) {
          for (const tx of de(ix)) {
            const b = tz * teselas + tx;
            const j = ((iz - tz * lado) * n + ix - tx * lado) * 3;
            const g = baldosas[b].geometry;
            (g.attributes.position.array as Float32Array).set([x, h, z], j);
            (g.attributes.color.array as Float32Array).set([co.r, co.g, co.b], j);
            tocadas.add(b);
          }
        }
      }
    }
    for (const b of tocadas) {
      const m = baldosas[b];
      const g = m.geometry;
      g.attributes.position.needsUpdate = true;
      g.attributes.color.needsUpdate = true;
      g.computeVertexNormals();
      g.computeBoundingSphere();
      // La esfera puede crecer al esculpir: el culling la vuelve a leer.
      culling.quitar(m);
      culling.registrarMalla(m, distancia);
    }
  };
  actualizar();
  return { baldosas, actualizar };
}

/** Textura de profundidad (8 bits: -6 a +6 m) para que el mar sepa dónde es poco profundo y hacer espuma. */
export const MAPA_LADO = 400;
export function crearMapaProfundidad(): DataTexture {
  const n = 256;
  const t = new DataTexture(new Uint8Array(n * n), n, n, RedFormat, UnsignedByteType);
  t.magFilter = LinearFilter;
  t.minFilter = LinearFilter;
  rellenarMapaProfundidad(t);
  return t;
}

/** Vuelve a sacar el mapa de profundidad de la rejilla (tras cambiar el terreno). */
export function rellenarMapaProfundidad(t: DataTexture): void {
  const n = t.image.width;
  const datos = t.image.data as Uint8Array;
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const x = ((i + 0.5) / n) * MAPA_LADO - MAPA_LADO / 2;
      const z = ((j + 0.5) / n) * MAPA_LADO - MAPA_LADO / 2;
      const h = Math.abs(x) < MITAD && Math.abs(z) < MITAD ? terrenoEn(x, z) : -10;
      datos[j * n + i] = Math.round(clamp((h + 6) / 12, 0, 1) * 255);
    }
  }
  t.needsUpdate = true;
}
