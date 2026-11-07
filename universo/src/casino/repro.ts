// Reproducción de una tirada: recorre los estados que manda el servidor respetando sus `ms`, interpola los rodillos
// para pintar a 60 fps y saca de los estados los sucesos que hacen ruido (paradas, tensión, monedas, carteles).

export interface Estado {
  reels: number[];
  blur: number[];
  glow: number;
  glowColor?: string;
  bulbs: { shape?: string; lit: (string | null)[] };
  banner: { tier: string } | null;
  meter: { text: string } | null;
  ms?: number;
  [clave: string]: unknown;
}

export type Suceso =
  | { t: number; tipo: "parada"; rodillo: number; lenta: boolean }
  | { t: number; tipo: "tension"; on: boolean }
  | { t: number; tipo: "moneda"; variante: number }
  | { t: number; tipo: "cartel"; tier: string }
  | { t: number; tipo: "resultado" };

/** Como mucho una moneda cada tanto (unas 12 por segundo). */
const HUECO_MONEDA = 80;

export class Reproduccion {
  readonly inicios: number[] = [];
  readonly total: number;
  readonly sucesos: Suceso[] = [];
  private cursor = 0;

  /**
   * @param estados Los fotogramas de la tirada; el último, sin `ms`, es la máquina parada.
   * @param largo Longitud de cada tira, para envolver los rodillos.
   * @param t0 Instante (ms del reloj de la página) en que empieza.
   */
  constructor(
    readonly estados: Estado[],
    private readonly largo: number[],
    readonly t0: number,
  ) {
    let t = 0;
    for (const e of estados) {
      this.inicios.push(t);
      t += e.ms ?? 0;
    }
    this.total = t;
    this.extraerSucesos();
  }

  /** Índice del estado que toca a los `t` ms de empezar. */
  indice(t: number): number {
    const e = this.estados;
    if (t >= this.total) return e.length - 1;
    let i = 0;
    while (i + 1 < e.length && this.inicios[i + 1] <= t) i++;
    return i;
  }

  hecha(ahora: number): boolean {
    return ahora - this.t0 >= this.total;
  }

  /** El estado a los `t` ms con los rodillos entre un fotograma y el siguiente (camino más corto en la tira). */
  interpolado(t: number): Estado {
    const i = this.indice(t);
    const a = this.estados[i];
    const b = this.estados[i + 1];
    if (!b || !a.ms) return a;
    const f = Math.min(1, Math.max(0, (t - this.inicios[i]) / a.ms));
    const reels = a.reels.map((x, r) => {
      const n = this.largo[r] || 1;
      let d = (((b.reels[r] - x) % n) + n) % n;
      if (d > n / 2) d -= n;
      return x + d * f;
    });
    return { ...a, reels, blur: a.blur.map((x, r) => x + (b.blur[r] - x) * f) };
  }

  /** Sucesos que ya tocan (los de más de `caducan` ms de antigüedad se saltan: la pestaña estaba oculta). */
  pendientes(ahora: number, caducan = 300): Suceso[] {
    const t = ahora - this.t0;
    const fuera: Suceso[] = [];
    while (this.cursor < this.sucesos.length && this.sucesos[this.cursor].t <= t) {
      const s = this.sucesos[this.cursor++];
      if (t - s.t <= caducan) fuera.push(s);
    }
    return fuera;
  }

  private extraerSucesos(): void {
    const { estados, inicios, sucesos } = this;
    let hubo = false; // algún rodillo ha girado ya
    let tension = false;
    let esperaVista = false; // ha habido anticipación (el último rodillo frena despacio)
    let ultimaMoneda = -1e9;
    let variante = 0;
    let resultado = false;
    for (let i = 1; i < estados.length; i++) {
      const [prev, st, t] = [estados[i - 1], estados[i], inicios[i]];
      if (st.blur.some((b) => b > 0)) hubo = true;
      if (st.glow > 0) esperaVista = true;
      for (let r = 0; r < st.blur.length; r++) {
        if (prev.blur[r] > 0 && st.blur[r] === 0) sucesos.push({ t, tipo: "parada", rodillo: r, lenta: r === st.blur.length - 1 && esperaVista });
      }
      const espera = st.glow > 0 && st.blur[st.blur.length - 1] > 0;
      if (espera !== tension) sucesos.push({ t, tipo: "tension", on: (tension = espera) });
      if (st.meter && st.meter.text !== prev.meter?.text && t - ultimaMoneda >= HUECO_MONEDA) {
        sucesos.push({ t, tipo: "moneda", variante: (variante++ % 3) + 1 });
        ultimaMoneda = t;
      }
      if (st.banner && !prev.banner) sucesos.push({ t, tipo: "cartel", tier: st.banner.tier });
      if (hubo && st.blur.every((b) => b === 0) && !resultado) {
        resultado = true;
        sucesos.push({ t: t + 80, tipo: "resultado" });
      }
    }
    sucesos.sort((x, y) => x.t - y.t);
  }
}

/** Cuántos rodillos siguen girando en un estado (para el zumbido). */
export const girando = (e: Estado): number => e.blur.filter((b) => b > 0).length;
