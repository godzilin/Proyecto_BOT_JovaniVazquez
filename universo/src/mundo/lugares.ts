// Lugares con clave fija (para logros): cada uno es un radio alrededor de un punto o una condición.
// El jugador los "pisa" una sola vez por sesión de página; los nuevos se acumulan hasta el siguiente informe.
import { DRAGO } from "./vegetacion";
import { BAND, CANTERAS, DUNAS, FARO, NIVEL_MAR, NUBLO, PICO, PLAZA, enMuelle, heightAt } from "./terreno";

interface Lugar {
  clave: string;
  /** x, z y y de los pies. */
  dentro: (x: number, y: number, z: number) => boolean;
}

const radio = (p: { x: number; z: number }, r: number) => (x: number, _y: number, z: number): boolean => Math.hypot(x - p.x, z - p.z) < r;

const LUGARES: Lugar[] = [
  { clave: "nieves", dentro: (x, y, z) => Math.hypot(x - PICO.x, z - PICO.z) < 10 && y > heightAt(PICO.x, PICO.z) - 6 },
  { clave: "nublo", dentro: radio(NUBLO, 12) },
  { clave: "bandama", dentro: (x, y, z) => {
    const d = Math.hypot(x - BAND.x, z - BAND.z);
    return d > 12 && d < 24 && y > BAND.alt - 4;
  } },
  { clave: "dunas", dentro: radio(DUNAS, 24) },
  { clave: "faro", dentro: radio(FARO, 9) },
  { clave: "plaza", dentro: radio(PLAZA, 10) },
  { clave: "drago", dentro: radio(DRAGO, 7) },
  { clave: "muelle", dentro: (x, y, z) => enMuelle(x, z) && y > NIVEL_MAR + 0.8 },
  { clave: "canteras", dentro: radio(CANTERAS, 16) },
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
