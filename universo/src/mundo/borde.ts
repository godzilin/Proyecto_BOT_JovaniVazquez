// El borde del mundo: hasta dónde puede ir el jugador. Es la caja de las celdas del mundo publicado (`fijarBorde`) más
// MAR metros de agua, y nunca menos que la isla de serie (±300 m). Lo más lejos que puede llegar es donde acaban las
// celdas del editor (`LIMITE`, `CELL_LIMIT` de `universo_editor.py`), que es también lo que acepta la sala (`WORLD_DM`,
// `MIN_Y_DM` y `MAX_Y_DM` de `src/bot/services/universo_sala.py`, que descarta las poses de fuera); lo vigila
// `tests/unit/test_universo_borde_ts.py`. Sin imports, para poder probarlo con Node a secas.

/** Lado de una celda del mundo en metros y celdas a cada lado del 0 (`CELL` y `CELL_LIMIT` del editor). */
export const CELDA_M = 80;
export const LIMITE_CELDAS = 500;
/** Lo más lejos del centro, en decímetros, en x y en z por separado (un cuadrado de 80 km). */
export const MUNDO_DM = CELDA_M * LIMITE_CELDAS * 10;
/** Altura mínima y máxima, en decímetros (el Teide mide 3715 m). */
export const MIN_Y_DM = -500;
export const MAX_Y_DM = 40000;

export const LIMITE = MUNDO_DM / 10;
export const MIN_Y = MIN_Y_DM / 10;
export const MAX_Y = MAX_Y_DM / 10;
/** Metros de mar alrededor de las celdas publicadas: con las 4 × 4 de la isla de serie, ±300 m. */
export const MAR = 140;
/** El borde de la isla de serie, en metros: el de un mundo sin publicar o con poco. */
export const SERIE = 300;

/** El borde de ahora, en metros: x y z mínimas y máximas. Se lee en vivo (no se copia: cambia al publicar). */
export const borde = { x0: -SERIE, x1: SERIE, z0: -SERIE, z1: SERIE };

const CLAVE_CELDA = /^c\/(-?\d+)\/(-?\d+)$/;
const limitar = (v: number, a: number, b: number): number => (v < a ? a : v > b ? b : v);

/**
 * Pone el borde con las claves de los trozos del mundo publicado (las de celda, `c/<cx>/<cz>`; las demás no cuentan). Con
 * `ampliar`, solo crece (al llegar los trozos cambiados de una publicación): una celda borrada no lo encoge hasta recargar.
 */
export function fijarBorde(claves: Iterable<string>, ampliar = false): void {
  let x0 = -SERIE;
  let x1 = SERIE;
  let z0 = -SERIE;
  let z1 = SERIE;
  if (ampliar) ({ x0, x1, z0, z1 } = borde);
  for (const clave of claves) {
    const m = CLAVE_CELDA.exec(clave);
    if (!m) continue;
    const cx = Number(m[1]) * CELDA_M;
    const cz = Number(m[2]) * CELDA_M;
    x0 = Math.min(x0, cx - MAR);
    x1 = Math.max(x1, cx + CELDA_M + MAR);
    z0 = Math.min(z0, cz - MAR);
    z1 = Math.max(z1, cz + CELDA_M + MAR);
  }
  borde.x0 = Math.max(x0, -LIMITE);
  borde.x1 = Math.min(x1, LIMITE);
  borde.z0 = Math.max(z0, -LIMITE);
  borde.z1 = Math.min(z1, LIMITE);
}

/** Si (x, z) cae dentro del borde. */
export function dentro(x: number, z: number): boolean {
  return x >= borde.x0 && x <= borde.x1 && z >= borde.z0 && z <= borde.z1;
}

/** Lo deja dentro del borde: una pared invisible. Dice si estaba fuera. Redondeado a dm sigue dentro (300,0 → 3000). */
export function encerrar(p: { x: number; y: number; z: number }): boolean {
  const x = limitar(p.x, borde.x0, borde.x1);
  const y = limitar(p.y, MIN_Y, MAX_Y);
  const z = limitar(p.z, borde.z0, borde.z1);
  const fuera = x !== p.x || y !== p.y || z !== p.z;
  p.x = x;
  p.y = y;
  p.z = z;
  return fuera;
}
