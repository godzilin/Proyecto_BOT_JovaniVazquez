// Los gestos del robot (se compran en la tienda del bot) y su panel: un botón 👋 que abre una rejilla con los 7.
// Los ids son los del campo `anim` de la sala (4-10). Aquí no hay mallas ni texturas: solo animación y DOM.
import type { Jugador } from "./jugador";

export interface Gesto {
  id: number;
  clip: string;
  emoji: string;
  etiqueta: string;
  tecla: string;
  /** «una»: una vez y vuelve a quieto; «bucle»: se repite hasta que te mueves; «queda»: una vez y se queda en el
   * último fotograma hasta que te mueves (Sitting del GLB es el movimiento de sentarse, no una pose). */
  modo: "una" | "bucle" | "queda";
}

export const GESTOS: readonly Gesto[] = [
  { id: 4, clip: "Wave", emoji: "👋", etiqueta: "Saludar", tecla: "1", modo: "una" },
  { id: 5, clip: "Dance", emoji: "💃", etiqueta: "Bailar", tecla: "2", modo: "bucle" },
  { id: 6, clip: "ThumbsUp", emoji: "👍", etiqueta: "Pulgar", tecla: "3", modo: "una" },
  { id: 7, clip: "Yes", emoji: "✅", etiqueta: "Sí", tecla: "4", modo: "una" },
  { id: 8, clip: "No", emoji: "❌", etiqueta: "No", tecla: "5", modo: "una" },
  { id: 9, clip: "Sitting", emoji: "🪑", etiqueta: "Sentarse", tecla: "6", modo: "queda" },
  { id: 10, clip: "Punch", emoji: "👊", etiqueta: "Puñetazo", tecla: "7", modo: "una" },
];

/** Gesto por su id de la sala (4-10), o undefined. */
export const gestoDe = (id: number): Gesto | undefined => GESTOS[id - 4];

const BLOQUEADO = "Cómpralo en la tienda del bot (tienda) y vuelve a abrir el Jovaniverso";

/** Crea el botón y el panel en `capa`. `comprados` son los ids que tiene el jugador. */
export function iniciarGestos(capa: HTMLElement, yo: Jugador, comprados: readonly number[]): void {
  const tengo = new Set(comprados);
  const tactil = window.matchMedia("(pointer: coarse)").matches;

  const boton = document.createElement("button");
  boton.className = "boton-gestos";
  boton.type = "button";
  boton.setAttribute("aria-label", "Gestos");
  boton.setAttribute("aria-expanded", "false");
  boton.textContent = "👋";

  const panel = document.createElement("div");
  panel.className = "panel-gestos";
  panel.hidden = true;
  const rejilla = document.createElement("div");
  rejilla.className = "rejilla-gestos";
  const nota = document.createElement("p");
  nota.className = "nota-gestos";
  nota.setAttribute("role", "status");
  nota.hidden = true;
  nota.textContent = BLOQUEADO;
  panel.append(rejilla, nota);

  const abrir = (si: boolean): void => {
    panel.hidden = !si;
    nota.hidden = true;
    boton.setAttribute("aria-expanded", String(si));
  };
  const hacer = (g: Gesto): void => {
    yo.gesto(g.id);
    abrir(false);
  };

  for (const g of GESTOS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "gesto";
    const libre = tengo.has(g.id);
    if (!libre) {
      b.classList.add("bloqueado");
      b.setAttribute("aria-disabled", "true");
    }
    const emoji = document.createElement("span");
    emoji.className = "gesto-emoji";
    emoji.textContent = libre ? g.emoji : "🔒";
    const texto = document.createElement("span");
    texto.textContent = g.etiqueta;
    b.append(emoji, texto);
    if (!tactil) {
      const t = document.createElement("span");
      t.className = "gesto-tecla";
      t.textContent = g.tecla;
      b.append(t);
    }
    b.addEventListener("click", () => {
      if (libre) hacer(g);
      else nota.hidden = false;
    });
    rejilla.append(b);
  }

  boton.addEventListener("click", () => abrir(panel.hidden === true));
  capa.append(panel, boton);

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") return abrir(false);
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    const g = /^Digit[1-7]$/.test(e.code) ? GESTOS[Number(e.code[5]) - 1] : undefined;
    if (g && tengo.has(g.id)) hacer(g);
  });
  // Tocar fuera cierra el panel (el toque sigue su camino: mover la cámara o el joystick).
  window.addEventListener(
    "pointerdown",
    (e) => {
      const t = e.target as Node;
      if (!panel.hidden && !panel.contains(t) && !boton.contains(t)) abrir(false);
    },
    true,
  );
}
