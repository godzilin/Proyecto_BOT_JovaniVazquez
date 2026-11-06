// Colisiones del mundo: círculos en el plano (troncos, casas, rocas, faro...) contra los que el jugador se empuja.

export interface Circulo {
  x: number;
  z: number;
  r: number;
}

export const circulos: Circulo[] = [];

export function añadirCirculo(x: number, z: number, r: number): void {
  circulos.push({ x, z, r });
}

/** Saca (x, z) de cualquier círculo que toque con un radio `r` propio; devuelve la posición corregida. */
export function empujar(x: number, z: number, r: number, fuera: { x: number; z: number }): void {
  for (const c of circulos) {
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
