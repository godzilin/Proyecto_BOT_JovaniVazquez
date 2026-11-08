// Los lugares de los logros que son un círculo y se mueven con el editor (`zonas` del mundo). Dónde se pisa cada lugar lo
// mira la sala del servidor con las poses (`world_places` y `Places` de `src/bot/services/universo.py`, que tiene estos
// mismos números en decímetros, `ZONES_DM`), no la página.
import { DRAGO } from "./vegetacion";
import { CANTERAS, DUNAS, FARO, NUBLO, PLAZA } from "./terreno";

/** Centro y radio de serie de los lugares que son un círculo (los mismos que las zonas del mundo de serie). */
const BASE: Record<string, { x: number; z: number; r: number }> = {
  nublo: { ...NUBLO, r: 12 },
  dunas: { ...DUNAS, r: 24 },
  faro: { ...FARO, r: 9 },
  plaza: { x: PLAZA.x, z: PLAZA.z, r: 10 },
  drago: { ...DRAGO, r: 7 },
  canteras: { ...CANTERAS, r: 16 },
};

/** Una copia de las zonas de serie, para el documento del mundo. */
export function zonasDeSerie(): Record<string, { x: number; z: number; r: number }> {
  return Object.fromEntries(Object.entries(BASE).map(([k, z]) => [k, { x: Math.round(z.x * 1e4) / 1e4, z: Math.round(z.z * 1e4) / 1e4, r: z.r }]));
}
