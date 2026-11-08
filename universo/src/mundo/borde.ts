// El borde del mundo: hasta dónde puede ir el jugador. Es el mismo que acepta la sala (`WORLD_DM`, `MIN_Y_DM` y
// `MAX_Y_DM` de `src/bot/services/universo_sala.py`, que descarta las poses de fuera); lo vigila
// `tests/unit/test_universo_borde_ts.py`. Más allá de ±3.276,7 m la pose ni cabe en el int16 de la sala.

/** Lo más lejos del centro, en decímetros, en x y en z por separado (un cuadrado). */
export const MUNDO_DM = 3000;
/** Altura mínima y máxima, en decímetros. */
export const MIN_Y_DM = -500;
export const MAX_Y_DM = 2000;

export const BORDE = MUNDO_DM / 10;
export const MIN_Y = MIN_Y_DM / 10;
export const MAX_Y = MAX_Y_DM / 10;

const limitar = (v: number, a: number, b: number): number => (v < a ? a : v > b ? b : v);

/** Lo deja dentro del borde: una pared invisible. Dice si estaba fuera. Redondeado a dm sigue dentro (300,0 → 3000). */
export function encerrar(p: { x: number; y: number; z: number }): boolean {
  const x = limitar(p.x, -BORDE, BORDE);
  const y = limitar(p.y, MIN_Y, MAX_Y);
  const z = limitar(p.z, -BORDE, BORDE);
  const fuera = x !== p.x || y !== p.y || z !== p.z;
  p.x = x;
  p.y = y;
  p.z = z;
  return fuera;
}
