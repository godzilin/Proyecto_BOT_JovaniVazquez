// Lo que se dibuja en 2D para las máquinas: el pintor de pantallas (la escena de la tragaperras del bot, en un iframe oculto)
// y el atlas de letrero, visores, etiquetas de botones y números, que es una sola textura para todo el casino.

export interface Tragas {
  machines: number;
  strips: string[];
  symbols: Record<string, string>;
  stills: unknown[];
  pot_text: string;
}

interface EscenaVentana extends Window {
  setup(meta: unknown): Promise<boolean>;
  paintState(estado: unknown): HTMLCanvasElement;
}

export const PANTALLA_W = 544;
export const PANTALLA_H = 412;
const RUTA = "/assets/tragas/slots/";

/** Un solo pintor para todas las máquinas: pinta el estado de una y se copia a la pantalla de esa máquina. */
export class Pintor {
  private constructor(
    private readonly marco: HTMLIFrameElement,
    private readonly w: EscenaVentana,
  ) {}

  static async crear(t: Tragas): Promise<Pintor> {
    const html = await (await fetch(`${RUTA}escena.html`)).text();
    const marco = document.createElement("iframe");
    marco.setAttribute("aria-hidden", "true");
    marco.tabIndex = -1;
    marco.style.cssText = "position:fixed;left:-9999px;top:0;width:600px;height:460px;border:0;visibility:hidden;pointer-events:none";
    // Escala 1 (544 × 412) y rutas relativas a la carpeta de la escena.
    marco.srcdoc = html
        .replace('src="../tragas.js"', 'src="/assets/tragas/tragas.js"') // sin esto, el precargador de scripts lo pide antes de leer <base>
        .replace("<head>", `<head><base href="${RUTA}"><script>window.TRAGAS_S = 1</script>`);
    const cargado = new Promise<void>((ok, mal) => {
      marco.onload = () => ok();
      marco.onerror = () => mal(new Error("No se ha podido cargar la escena de la tragaperras"));
    });
    document.body.append(marco);
    await cargado;
    const w = marco.contentWindow as EscenaVentana;
    const symbols = Object.fromEntries(Object.entries(t.symbols).map(([clave, archivo]) => [clave, RUTA + archivo]));
    await w.setup({ strips: t.strips, symbols, symbolsKey: "plaza-v1", out: [PANTALLA_W, PANTALLA_H] });
    return new Pintor(marco, w);
  }

  /** Pinta `estado` y lo copia en `destino`. */
  pintar(estado: unknown, destino: CanvasRenderingContext2D): void {
    destino.drawImage(this.w.paintState(estado), 0, 0);
  }

  destruir(): void {
    this.marco.remove();
  }
}

// -- Atlas -------------------------------------------------------------------------------------------------------

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export const ATLAS_W = 1024;
export const ATLAS_H = 512;
export const LETRERO: Rect = { x: 0, y: 0, w: 1024, h: 128 };
export const visorDe = (i: number): Rect => ({ x: (i % 3) * 341, y: 128 + Math.floor(i / 3) * 96, w: 341, h: 96 });
export const ETIQUETAS = ["GIRAR", "½", "×2", "AUTO", "PARAR"] as const;
export const etiquetaDe = (k: number): Rect => ({ x: k * 200, y: 320, w: 200, h: 64 });
export const numeroDe = (i: number): Rect => ({ x: i * 64, y: 384, w: 64, h: 64 });

export interface DatosVisor {
  apuesta: string;
  gratis: number;
  bonus: number;
  calor: number;
  calorMax: number;
}

const FUENTE = "system-ui, 'Segoe UI', Roboto, Arial, sans-serif";

function placa(g: CanvasRenderingContext2D, r: Rect, fondo: string | CanvasGradient, borde: string, radio = 10): void {
  g.save();
  g.beginPath();
  g.roundRect(r.x + 2, r.y + 2, r.w - 4, r.h - 4, radio);
  g.fillStyle = fondo;
  g.fill();
  g.lineWidth = 3;
  g.strokeStyle = borde;
  g.stroke();
  g.restore();
}

function texto(g: CanvasRenderingContext2D, s: string, x: number, y: number, tam: number, color: string, alinear: CanvasTextAlign = "left"): void {
  g.font = `700 ${tam}px ${FUENTE}`;
  g.textAlign = alinear;
  g.textBaseline = "alphabetic";
  g.fillStyle = color;
  g.fillText(s, x, y);
}

export function dibujarLetrero(g: CanvasRenderingContext2D, bote: string): void {
  const r = LETRERO;
  const grad = g.createLinearGradient(0, r.y, 0, r.y + r.h);
  grad.addColorStop(0, "#1c1660");
  grad.addColorStop(1, "#0b0a2c");
  placa(g, r, grad, "#e2b441", 14);
  texto(g, "BOTE COMÚN", 44, r.y + 74, 40, "#e2b441");
  texto(g, bote, r.w - 44, r.y + 92, 86, "#fff2b8", "right");
}

export function dibujarVisor(g: CanvasRenderingContext2D, i: number, d: DatosVisor): void {
  const r = visorDe(i);
  placa(g, r, "#07140f", "#2f7d4a", 8);
  const [x0, x1] = [r.x + 12, r.x + r.w - 12];
  texto(g, "APUESTA", x0, r.y + 32, 19, "#6fbf8a");
  texto(g, d.apuesta, x1, r.y + 36, 32, "#ffd24a", "right");
  texto(g, "BONUS", x0, r.y + 62, 17, "#6fbf8a");
  const barra = { x: r.x + 84, y: r.y + 48, w: 140, h: 15 };
  g.fillStyle = "#12301f";
  g.beginPath();
  g.roundRect(barra.x, barra.y, barra.w, barra.h, 7);
  g.fill();
  if (d.bonus > 0) {
    g.fillStyle = "#ffb93a";
    g.beginPath();
    g.roundRect(barra.x, barra.y, Math.max(barra.h, barra.w * Math.min(1, d.bonus)), barra.h, 7);
    g.fill();
  }
  if (d.gratis > 0) texto(g, `GRATIS ${d.gratis}`, x1, r.y + 62, 20, "#7dffa0", "right");
  texto(g, "CALOR", x0, r.y + 88, 17, "#6fbf8a");
  for (let k = 0; k < d.calorMax; k++) {
    g.fillStyle = k < d.calor ? "#ff6a2a" : "#2a2a2a";
    g.beginPath();
    g.arc(r.x + 100 + k * 26, r.y + 82, 8, 0, Math.PI * 2);
    g.fill();
  }
}

export function dibujarEtiqueta(g: CanvasRenderingContext2D, k: number): void {
  const r = etiquetaDe(k);
  placa(g, r, "#16161e", "#b8bcc8", 12);
  const texto1 = ETIQUETAS[k];
  const cy = r.y + r.h / 2;
  if (k === 3 || k === 4) {
    // Un símbolo dibujado (▶ o ■) delante de la palabra: las fuentes del navegador no siempre traen los emojis.
    g.fillStyle = k === 3 ? "#ffd24a" : "#ff6b6b";
    g.beginPath();
    if (k === 3) {
      g.moveTo(r.x + 22, cy - 15);
      g.lineTo(r.x + 22, cy + 15);
      g.lineTo(r.x + 46, cy);
      g.closePath();
    } else {
      g.rect(r.x + 22, cy - 13, 26, 26);
    }
    g.fill();
    texto(g, texto1, r.x + 58, cy + 14, 40, "#ffffff");
  } else {
    texto(g, texto1, r.x + r.w / 2, cy + 15, k === 0 ? 44 : 50, "#ffffff", "center");
  }
}

export function dibujarNumero(g: CanvasRenderingContext2D, i: number): void {
  const r = numeroDe(i);
  g.fillStyle = "#14141b";
  g.beginPath();
  g.arc(r.x + 32, r.y + 32, 29, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = 4;
  g.strokeStyle = "#e2b441";
  g.stroke();
  texto(g, String(i + 1), r.x + 32, r.y + 45, 38, "#ffffff", "center");
}
