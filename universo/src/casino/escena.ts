// Carga en la página la escena de la tragaperras clásica del bot (la misma que pinta los GIF de Discord) como módulos
// del paquete, con sus fuentes y símbolos: el CSP de las Activities de Discord no deja scripts en línea ni iframes srcdoc.
import "./escala"; // window.TRAGAS_S = 1 antes de evaluar tragas.js
import "../../../src/bot/assets/tragas.js";
import "../../../src/bot/assets/slots/escena.js";
import luckiest from "../../../src/bot/assets/botes/fonts/luckiest-guy.woff?url";
import montserrat from "../../../src/bot/assets/memes/fonts/MontserratBold.ttf?url";

/** URL de cada PNG de los símbolos, por nombre de archivo (el servidor manda `{clave: "cherry.png"}`). */
const PNG = import.meta.glob("../../../src/bot/assets/slots/*.png", { query: "?url", import: "default", eager: true }) as Record<string, string>;
export const SIMBOLOS: Record<string, string> = Object.fromEntries(Object.entries(PNG).map(([ruta, url]) => [ruta.split("/").pop() as string, url]));

export interface Escena {
  setup(meta: unknown): Promise<boolean>;
  paintState(estado: unknown): HTMLCanvasElement;
}

/** Registra las fuentes de la escena (en el bot van en el `<style>` de escena.html) y devuelve sus funciones. */
export async function cargarEscena(): Promise<Escena> {
  const fuentes = [new FontFace("Luckiest", `url(${luckiest})`), new FontFace("Montserrat", `url(${montserrat})`, { weight: "700" })];
  for (const f of fuentes) document.fonts.add(f);
  await Promise.all(fuentes.map((f) => f.load()));
  return window as unknown as Escena;
}
