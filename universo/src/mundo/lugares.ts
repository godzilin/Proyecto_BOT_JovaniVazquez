// Lugares con clave fija (para logros): cada uno es un radio alrededor de un punto o una condición.
// El jugador los "pisa" una sola vez por sesión de página; los nuevos se acumulan hasta el siguiente informe.
import { DRAGO } from "./vegetacion";
import { BAND, CANTERAS, DUNAS, FARO, NIVEL_MAR, NUBLO, PICO, PLAZA, enMuelle, terrenoEn } from "./terreno";

interface Lugar {
  clave: string;
  /** x, z y y de los pies. */
  dentro: (x: number, y: number, z: number) => boolean;
}

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

/** Zonas movidas desde el editor (`fijarZonas`); las que no vienen siguen en su sitio de siempre. */
let zonas: Record<string, { x: number; z: number; r: number }> = {};

/** Mueve los lugares que son un radio alrededor de un punto; el resto de claves del documento no hace nada. */
export function fijarZonas(nuevas: Record<string, { x: number; z: number; r: number }>): void {
  zonas = nuevas;
}

const radio = (clave: string) => (x: number, _y: number, z: number): boolean => {
  const o = zonas[clave] ?? BASE[clave];
  return Math.hypot(x - o.x, z - o.z) < o.r;
};

const LUGARES: Lugar[] = [
  { clave: "nieves", dentro: (x, y, z) => Math.hypot(x - PICO.x, z - PICO.z) < 10 && y > terrenoEn(PICO.x, PICO.z) - 6 },
  { clave: "nublo", dentro: radio("nublo") },
  { clave: "bandama", dentro: (x, y, z) => {
    const d = Math.hypot(x - BAND.x, z - BAND.z);
    return d > 12 && d < 24 && y > BAND.alt - 4;
  } },
  { clave: "dunas", dentro: radio("dunas") },
  { clave: "faro", dentro: radio("faro") },
  { clave: "plaza", dentro: radio("plaza") },
  { clave: "drago", dentro: radio("drago") },
  { clave: "muelle", dentro: (x, y, z) => enMuelle(x, z) && y > NIVEL_MAR + 0.8 },
  { clave: "canteras", dentro: radio("canteras") },
  { clave: "agua", dentro: (_x, y) => y < NIVEL_MAR - 0.25 },
];

export const CLAVES = LUGARES.map((l) => l.clave);

export class Lugares {
  private visitados = new Set<string>();
  private nuevos: string[] = [];

  comprobar(x: number, y: number, z: number): void {
    for (const l of LUGARES) {
      if (!this.visitados.has(l.clave) && l.dentro(x, y, z)) {
        this.visitados.add(l.clave);
        this.nuevos.push(l.clave);
      }
    }
  }

  /** Da por visitados, sin contarlos, los lugares donde está ese punto (al llegar saltando, no andando). */
  saltar(x: number, y: number, z: number): void {
    for (const l of LUGARES) if (l.dentro(x, y, z)) this.visitados.add(l.clave);
  }

  /** Vacía y devuelve los lugares pisados por primera vez desde la última vez. */
  tomar(): string[] {
    return this.nuevos.splice(0);
  }

  devolver(claves: string[]): void {
    this.nuevos.unshift(...claves);
  }
}
