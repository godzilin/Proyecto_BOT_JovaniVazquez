// Colisiones del mundo: círculos en el plano (troncos, casas, rocas, faro...) contra los que el jugador se empuja.
// Van en una rejilla de cubos de `CUBO` m por su centro (los más grandes que `GRANDE`, aparte), para que con el mundo cercano
// cargado (decenas de miles de círculos) cada consulta mire solo los de alrededor.

export interface Circulo {
  x: number;
  z: number;
  r: number;
}

const CUBO = 16;
/** Desde este radio un círculo va en `grandes` y se mira siempre; los demás, por su cubo. */
const GRANDE = 24;
const B = 1 << 20;
const cubos = new Map<number, Circulo[]>();
const grandes: Circulo[] = [];
let cuenta = 0;
/** Dentro de un interior, solo cuentan sus círculos (los muebles): los del mundo de fuera no están. */
let aparte: Circulo[] | null = null;

/** Con una lista, solo chocan esos círculos (al entrar en un interior); con `null`, los del mundo (al salir). */
export function soloEstos(lista: Circulo[] | null): void {
  aparte = lista;
}

const cubo = (ix: number, iz: number): number => (ix + B) * 2 * B + iz + B;
const de = (c: Circulo): Circulo[] | undefined => (c.r > GRANDE ? grandes : cubos.get(cubo(Math.floor(c.x / CUBO), Math.floor(c.z / CUBO))));

export function añadirCirculo(x: number, z: number, r: number): Circulo {
  const c = { x, z, r };
  if (r > GRANDE) grandes.push(c);
  else {
    const k = cubo(Math.floor(x / CUBO), Math.floor(z / CUBO));
    const l = cubos.get(k);
    if (l) l.push(c);
    else cubos.set(k, [c]);
  }
  cuenta++;
  return c;
}

/** Saca del mundo los círculos que devolvió `añadirCirculo`. */
export function quitarCirculos(lista: Circulo[]): void {
  for (const c of lista) {
    const l = de(c);
    const i = l ? l.indexOf(c) : -1;
    if (i < 0) continue;
    l![i] = l![l!.length - 1];
    l!.pop();
    if (!l!.length && l !== grandes) cubos.delete(cubo(Math.floor(c.x / CUBO), Math.floor(c.z / CUBO)));
    cuenta--;
  }
}

/** Cuántos círculos hay (las devtools). */
export const numeroCirculos = (): number => cuenta;

/** Todos los círculos cargados (las devtools los pintan). */
export function todosLosCirculos(): Circulo[] {
  const out = [...grandes];
  for (const l of cubos.values()) for (const c of l) out.push(c);
  return out;
}

/** Los círculos que pueden tocar el disco de radio `r` en (x, z): los de los cubos de alrededor y los grandes (sin filtrar). */
export function circulosCerca(x: number, z: number, r: number): Circulo[] {
  if (aparte) return aparte.slice();
  const out = grandes.slice();
  const ix0 = Math.floor((x - r - GRANDE) / CUBO);
  const ix1 = Math.floor((x + r + GRANDE) / CUBO);
  const iz0 = Math.floor((z - r - GRANDE) / CUBO);
  const iz1 = Math.floor((z + r + GRANDE) / CUBO);
  for (let ix = ix0; ix <= ix1; ix++) {
    for (let iz = iz0; iz <= iz1; iz++) {
      const l = cubos.get(cubo(ix, iz));
      if (l) for (const c of l) out.push(c);
    }
  }
  return out;
}

/** Saca (x, z) de cualquier círculo que toque con un radio `r` propio; devuelve la posición corregida. */
export function empujar(x: number, z: number, r: number, fuera: { x: number; z: number }): void {
  for (const c of circulosCerca(x, z, r)) {
    const dx = x - c.x;
    const dz = z - c.z;
    const min = c.r + r;
    const d2 = dx * dx + dz * dz;
    if (d2 < min * min) {
      const d = Math.sqrt(d2) || 1e-4;
      x = c.x + (dx / d) * min;
      z = c.z + (dz / d) * min;
    }
  }
  fuera.x = x;
  fuera.z = z;
}
