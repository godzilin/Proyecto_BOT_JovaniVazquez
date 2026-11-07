// Sitios de la isla a los que se salta: los de las devtools (`irA`, con su encuadre) y los de serie del panel «Viajar» del menú,
// que salen de los mismos puntos. Los que pone el editor van en el trozo `teleportes` del mundo (`editor/formato.ts`).
import type { Teleporte } from "../editor/formato";
import { BAND, CANTERAS, DUNAS, FARO, MUELLE, NUBLO, PICO, PLAZA } from "./terreno";

/** Objetivo (x, z), ángulo `a` y distancia `d` desde la que se mira, e inclinación de la cámara. */
export const SITIOS_ISLA: Record<string, [number, number, number, number, number]> = {
  nublo: [NUBLO.x, NUBLO.z, 3.5, 15, 0.2],
  bandama: [BAND.x, BAND.z, 2.2, 17, 0.45],
  dunas: [DUNAS.x - 4, DUNAS.z - 2, 1.4, 14, 0.3],
  faro: [FARO.x, FARO.z, 3.9, 26, 0.12],
  muelle: [MUELLE.cx, MUELLE.cz, 0, 0, 0.3],
  mogan: [MUELLE.x0, MUELLE.z0, 3.0, 18, 0.25],
  canteras: [CANTERAS.x, CANTERAS.z, 1.0, 10, 0.3],
  dunasfaro: [FARO.x, FARO.z, 3.3, 40, 0.2],
  cenit: [PICO.x, PICO.z, 0, 0, 1.45],
  nieves: [PICO.x, PICO.z, 1.0, 6, 0.35],
  drago: [PLAZA.x - 7, PLAZA.z + 4, 1.3, 9, 0.2],
  casino: [PLAZA.x - 0.3, PLAZA.z + 11, -Math.PI / 2, 7, 0.2],
};

/** Dónde se pone el jugador para mirar un sitio. */
export function puntoDe(nombre: string): [number, number] {
  const [tx, tz, a, d] = SITIOS_ISLA[nombre];
  return [tx + Math.cos(a) * d, tz + Math.sin(a) * d];
}

/** Clave de `SITIOS_ISLA`, nombre y, si el punto de la cámara cae en el agua, ángulo y distancia propios (medidos en tierra). */
const DE_SERIE: [string, string, [number, number]?][] = [
  ["casino", "Plaza y casino"],
  ["drago", "El Drago"],
  ["canteras", "Las Canteras", [3.93, 8]],
  ["muelle", "Muelle"],
  ["mogan", "Mogán", [0.79, 8]],
  ["faro", "Faro de Maspalomas", [1.57, 7]],
  ["dunas", "Dunas"],
  ["bandama", "Caldera de Bandama"],
  ["nublo", "Roque Nublo"],
  ["nieves", "Pico de las Nieves"],
];

/** Los teleportes de un mundo que no trae los suyos. */
export function teleportesDeSerie(): Teleporte[] {
  return DE_SERIE.map(([clave, nombre, propio]) => {
    const [tx, tz] = SITIOS_ISLA[clave];
    const [x, z] = propio ? [tx + Math.cos(propio[0]) * propio[1], tz + Math.sin(propio[0]) * propio[1]] : puntoDe(clave);
    return { nombre, x: Math.round(x * 100) / 100, z: Math.round(z * 100) / 100 };
  });
}
