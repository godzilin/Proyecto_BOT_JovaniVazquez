// Lo que se dibuja en 2D para las máquinas: el pintor de pantallas (la escena de la tragaperras del bot, ver escena.ts)
// el atlas de visores, etiquetas de botones y números, que es una sola textura para todas las máquinas, y el panel de cada letrero.

import type { Escena } from "./escena";

export interface Tragas {
  strips: string[];
  symbols: Record<string, string>;
  stills: unknown[];
  pot_text: string;
}

export const PANTALLA_W = 544;
export const PANTALLA_H = 412;

/** Un solo pintor para todas las máquinas: pinta el estado de una y se copia a la pantalla de esa máquina. */
export class Pintor {
  private constructor(private readonly escena: Escena) {}

  static async crear(t: Tragas): Promise<Pintor> {
    // La escena va en un chunk aparte (con el resto del casino) y se evalúa al pedirla, ya a escala 1.
    const { cargarEscena, SIMBOLOS } = await import("./escena");
    const escena = await cargarEscena();
    const symbols = Object.fromEntries(Object.entries(t.symbols).map(([clave, archivo]) => [clave, SIMBOLOS[archivo]]));
    await escena.setup({ strips: t.strips, symbols, symbolsKey: "plaza-v1", out: [PANTALLA_W, PANTALLA_H] });
    return new Pintor(escena);
  }

  /** Pinta `estado` y lo copia en `destino`. */
  pintar(estado: unknown, destino: CanvasRenderingContext2D): void {
    destino.drawImage(this.escena.paintState(estado), 0, 0);
  }

  destruir(): void {
    // Nada que soltar: la escena vive en la página y la siguiente vez se reutiliza.
  }
}

// -- Atlas -------------------------------------------------------------------------------------------------------

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
// Sitio para `MAX_TRAGAS` (24) máquinas: 8 filas de 3 visores, las 5 etiquetas y 2 filas de 16 números.
export const ATLAS_W = 1024;
export const ATLAS_H = 1024;
export const visorDe = (i: number): Rect => ({ x: (i % 3) * 341, y: Math.floor(i / 3) * 96, w: 341, h: 96 });
export const ETIQUETAS = ["GIRAR", "½", "×2", "AUTO", "PARAR"] as const;
export const etiquetaDe = (k: number): Rect => ({ x: k * 200, y: 768, w: 200, h: 64 });
export const numeroDe = (i: number): Rect => ({ x: (i % 16) * 64, y: 832 + Math.floor(i / 16) * 64, w: 64, h: 64 });

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

/** Un bote para los letreros, como lo manda el servidor (`/api/botes`). */
export interface Bote {
  clave: string;
  nombre: string;
  emoji: string;
  texto: string;
}

/** Texto que cabe en `ancho`: baja el tamaño hasta que entra (y nunca de `minimo`). */
function ajustado(g: CanvasRenderingContext2D, s: string, tam: number, ancho: number, minimo = 10): number {
  g.font = `700 ${tam}px ${FUENTE}`;
  const w = g.measureText(s).width;
  return w <= ancho ? tam : Math.max(minimo, Math.floor((tam * ancho) / w));
}

/**
 * El panel de un letrero en un lienzo de `w` × `h`. Sin bote, el título solo y centrado (un cartel cualquiera). Con bote, a la
 * izquierda el título (o «BOTE») y el nombre del bote y a la derecha la cifra; `bote` es `null` si aún no se sabe.
 */
export function dibujarLetrero(g: CanvasRenderingContext2D, w: number, h: number, titulo: string, bote: Bote | null, conBote: boolean): void {
  const r = { x: 0, y: 0, w, h };
  g.clearRect(0, 0, w, h);
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#1c1660");
  grad.addColorStop(1, "#0b0a2c");
  const borde = Math.max(4, Math.round(h * 0.11));
  placa(g, r, grad, "#e2b441", Math.min(14, h / 4));
  const m = Math.max(borde * 2, Math.round(h * 0.34)); // margen a los lados
  if (!conBote) {
    const t = titulo || " ";
    const tam = ajustado(g, t, Math.round(h * 0.6), w - 2 * m);
    texto(g, t, w / 2, h / 2 + tam * 0.36, tam, "#fff2b8", "center");
    return;
  }
  const cifra = bote?.texto ?? "…";
  const tamCifra = ajustado(g, cifra, Math.round(h * 0.67), w * 0.62 - m);
  g.font = `700 ${tamCifra}px ${FUENTE}`;
  const anchoCifra = g.measureText(cifra).width;
  const izq = w - 2 * m - anchoCifra - h * 0.25;
  const arriba = titulo || "BOTE";
  const tamArriba = ajustado(g, arriba, Math.round(h * 0.31), izq);
  texto(g, arriba, m, h * 0.5, tamArriba, "#e2b441");
  if (bote) {
    const tamNombre = ajustado(g, bote.nombre, Math.round(h * 0.2), izq);
    texto(g, bote.nombre, m, h * 0.5 + tamNombre * 1.2, tamNombre, "#b8b0f0");
  }
  texto(g, cifra, w - m, h / 2 + tamCifra * 0.36, tamCifra, "#fff2b8", "right");
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

/** El número `n` de la máquina en el hueco `i` del atlas. */
export function dibujarNumero(g: CanvasRenderingContext2D, i: number, n: number): void {
  const r = numeroDe(i);
  g.clearRect(r.x, r.y, r.w, r.h);
  g.fillStyle = "#14141b";
  g.beginPath();
  g.arc(r.x + 32, r.y + 32, 29, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = 4;
  g.strokeStyle = "#e2b441";
  g.stroke();
  texto(g, String(n), r.x + 32, r.y + 45, n > 9 ? 32 : 38, "#ffffff", "center");
}
