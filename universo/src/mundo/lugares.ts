// Lugares con clave fija (para logros): cada uno es un radio alrededor de un punto o una condición.
// El jugador los "pisa" una sola vez por sesión de página; los nuevos se acumulan hasta el siguiente informe.
import { DRAGO } from "./vegetacion";
import { BAND, CANTERAS, DUNAS, FARO, NIVEL_MAR, NUBLO, PICO, PLAZA, enMuelle, heightAt } from "./terreno";

interface Lugar {
  clave: string;
  /** x, z y y de los pies. */
  dentro: (x: number, y: number, z: number) => boolean;
}

/** Zonas movidas desde el editor (`fijarZonas`); las que no vienen siguen en su sitio de siempre. */
let zonas: Record<string, { x: number; z: number; r: number }> = {};

/** Mueve los lugares que son un radio alrededor de un punto; el resto de claves del documento no hace nada. */
export function fijarZonas(nuevas: Record<string, { x: number; z: number; r: number }>): void {
  zonas = nuevas;
}

const radio = (clave: string, p: { x: number; z: number }, r: number) => (x: number, _y: number, z: number): boolean => {
  const o = zonas[clave];
  return Math.hypot(x - (o?.x ?? p.x), z - (o?.z ?? p.z)) < (o?.r ?? r);
};

const LUGARES: Lugar[] = [
  { clave: "nieves", dentro: (x, y, z) => Math.hypot(x - PICO.x, z - PICO.z) < 10 && y > heightAt(PICO.x, PICO.z) - 6 },
  { clave: "nublo", dentro: radio("nublo", NUBLO, 12) },
  { clave: "bandama", dentro: (x, y, z) => {
    const d = Math.hypot(x - BAND.x, z - BAND.z);
    return d > 12 && d < 24 && y > BAND.alt - 4;
  } },
  { clave: "dunas", dentro: radio("dunas", DUNAS, 24) },
  { clave: "faro", dentro: radio("faro", FARO, 9) },
  { clave: "plaza", dentro: radio("plaza", PLAZA, 10) },
  { clave: "drago", dentro: radio("drago", DRAGO, 7) },
  { clave: "muelle", dentro: (x, y, z) => enMuelle(x, z) && y > NIVEL_MAR + 0.8 },
  { clave: "canteras", dentro: radio("canteras", CANTERAS, 16) },
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

  /** Vacía y devuelve los lugares pisados por primera vez desde la última vez. */
  tomar(): string[] {
    return this.nuevos.splice(0);
  }

  devolver(claves: string[]): void {
    this.nuevos.unshift(...claves);
  }
}
