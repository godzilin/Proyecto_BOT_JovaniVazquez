// Utilidades matemáticas y de azar determinista: el mundo tiene que salir idéntico para todos.

export const clamp = (v: number, a: number, b: number): number => (v < a ? a : v > b ? b : v);
export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
export function smoothstep(a: number, b: number, x: number): number {
  const t = clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
}

/** Generador con semilla (mulberry32): misma semilla, misma secuencia. */
export function mulberry32(semilla: number): () => number {
  let s = semilla | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Hash entero de una celda (sin Math.sin, que varía entre motores). Devuelve [0, 1). */
export function hash2(ix: number, iz: number): number {
  let h = Math.imul(ix, 374761393) ^ Math.imul(iz, 668265263) ^ 0x5bd1e995;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Ruido de valor suave en [0, 1]. */
export function ruido(x: number, z: number): number {
  const ix = Math.floor(x);
  const iz = Math.floor(z);
  const fx = x - ix;
  const fz = z - iz;
  const u = fx * fx * fx * (fx * (fx * 6 - 15) + 10);
  const v = fz * fz * fz * (fz * (fz * 6 - 15) + 10);
  const a = hash2(ix, iz);
  const b = hash2(ix + 1, iz);
  const c = hash2(ix, iz + 1);
  const d = hash2(ix + 1, iz + 1);
  return lerp(lerp(a, b, u), lerp(c, d, u), v);
}

/** Suma de octavas de ruido, normalizada a [0, 1]. */
export function fbm(x: number, z: number, octavas = 3): number {
  let suma = 0;
  let amp = 1;
  let total = 0;
  let f = 1;
  for (let i = 0; i < octavas; i++) {
    suma += ruido(x * f + i * 17.3, z * f - i * 9.1) * amp;
    total += amp;
    amp *= 0.5;
    f *= 2.03;
  }
  return suma / total;
}
