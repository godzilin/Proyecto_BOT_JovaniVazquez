// Las últimas poses de un jugador remoto y dónde estaba en un instante del pasado. La sala manda poses a 10 por
// segundo, pero llegan con el ritmo irregular de la red (y alguna vez se salta un tick): a cada pose se le pone la hora
// de su tick (múltiplos de PERIODO desde la anterior, pegados poco a poco a la de llegada) y se interpola entre las
// dos que rodean al instante pedido. Con solo las dos últimas, cualquier retraso mayor que PERIODO daba saltos.
// Sin imports, para poder probarla con Node a secas. No reserva memoria al muestrear.

export const PERIODO = 100; // ms entre ticks de la sala (TICK_HZ de universo_sala.py)
const N = 8; // poses guardadas: de sobra para el retraso con que se dibuja
const SALTO_MAX = 8; // m: más que esto es un teletransporte, no se interpola
const TAU = Math.PI * 2;

export class Estela {
  private readonly t = new Float64Array(N);
  private readonly px = new Float64Array(N);
  private readonly py = new Float64Array(N);
  private readonly pz = new Float64Array(N);
  private readonly pyaw = new Float64Array(N);
  private readonly panim = new Uint8Array(N);
  private n = 0;
  // Lo que deja `muestrear`.
  x = 0;
  y = 0;
  z = 0;
  yaw = 0;
  anim = 0;
  /** m/s del tramo en curso (0 si está parado en la última pose). */
  v = 0;

  get vacia(): boolean {
    return this.n === 0;
  }

  /** La última pose recibida (para saber a qué distancia está). */
  get ultimaX(): number {
    return this.px[this.n - 1];
  }

  get ultimaZ(): number {
    return this.pz[this.n - 1];
  }

  vaciar(): void {
    this.n = 0;
  }

  meter(x: number, y: number, z: number, yaw: number, anim: number, ahora: number): void {
    const u = this.n - 1;
    if (u >= 0 && (Math.hypot(x - this.px[u], z - this.pz[u]) > SALTO_MAX || Math.abs(y - this.py[u]) > SALTO_MAX)) this.n = 0;
    if (this.n === 0) {
      this.poner(ahora, x, y, z, yaw, anim);
      return;
    }
    const ultimo = this.t[u];
    const k = Math.round((ahora - ultimo) / PERIODO);
    if (k > 2) {
      // Tras una pausa (quieto, sin poses) estaba en la última hasta un tick antes: no se desliza lo que duró la pausa.
      this.poner(ahora - PERIODO, this.px[u], this.py[u], this.pz[u], this.pyaw[u], this.panim[u]);
      this.poner(ahora, x, y, z, yaw, anim);
      return;
    }
    const tick = ultimo + Math.max(k, 1) * PERIODO;
    this.poner(tick + (ahora - tick) * 0.1, x, y, z, yaw, anim);
  }

  /** Deja en x, y, z, yaw, anim y v dónde estaba en el instante `t` (ms), sin pasar de la última pose. */
  muestrear(t: number): void {
    const n = this.n;
    let i = 0;
    let j = 0;
    let a = 0;
    if (n > 1 && t >= this.t[n - 1]) i = j = n - 1;
    else if (n > 1 && t > this.t[0]) {
      while (this.t[i + 1] <= t) i++;
      j = i + 1;
      a = (t - this.t[i]) / (this.t[j] - this.t[i]);
    }
    const dx = this.px[j] - this.px[i];
    const dz = this.pz[j] - this.pz[i];
    this.x = this.px[i] + dx * a;
    this.y = this.py[i] + (this.py[j] - this.py[i]) * a;
    this.z = this.pz[i] + dz * a;
    let dy = this.pyaw[j] - this.pyaw[i];
    dy -= TAU * Math.round(dy / TAU);
    this.yaw = this.pyaw[i] + dy * a;
    this.anim = a < 0.5 ? this.panim[i] : this.panim[j];
    this.v = i === j ? 0 : Math.hypot(dx, dz) / ((this.t[j] - this.t[i]) / 1000);
  }

  private poner(t: number, x: number, y: number, z: number, yaw: number, anim: number): void {
    if (this.n === N) {
      for (const a of [this.t, this.px, this.py, this.pz, this.pyaw, this.panim]) a.copyWithin(0, 1);
      this.n--;
    }
    const i = this.n++;
    this.t[i] = t;
    this.px[i] = x;
    this.py[i] = y;
    this.pz[i] = z;
    this.pyaw[i] = yaw;
    this.panim[i] = anim;
  }
}
