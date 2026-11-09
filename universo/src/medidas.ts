// Medidas de la página para la telemetría (`/api/medidas` del cog): fps medio y percentil bajo, calidad adaptativa, llamadas
// de dibujo y triángulos de verdad (`renderer.info`), tiempos de entrada, GPU por familia, pantalla redondeada y
// reconexiones de la sala. Solo cifras. Por fotograma solo suma y compara (el percentil sale de un histograma fijo); cada
// 5 min y al ocultarse manda el tramo y empieza otro. Si falla, se pierde ese tramo: es telemetría. Sin logros.
import type { Mundo } from "./mundo";
import { reconexiones } from "./sala";

const CADA_MS = 300_000;
const MIN_MS = 60_000; // el tope del servidor (`MEDIDAS_SECONDS`): antes, lo medido se queda para el siguiente
const FPS_TOPE = 120;
const PERCENTIL = 0.05;

/** Momentos de la entrada, en ms desde que se abrió la página (`performance.now()`); los apunta `main.ts`. */
export const entrada: { login?: number; api?: number; mundo?: number; frame?: number } = {};

/** La familia de la GPU a partir de su nombre (sin modelo ni nada que identifique); `otra` si no se conoce. */
export function familiaGpu(texto: string): string {
  const t = texto.toLowerCase();
  const familias: [RegExp, string][] = [
    [/adreno/, "adreno"],
    [/mali/, "mali"],
    [/powervr/, "powervr"],
    [/xclipse/, "xclipse"],
    [/apple/, "apple"],
    [/swiftshader|llvmpipe|software/, "software"],
    [/nvidia|geforce|quadro/, "nvidia"],
    [/\bamd\b|radeon/, "amd"],
    [/intel/, "intel"],
  ];
  return familias.find(([re]) => re.test(t))?.[1] ?? "otra";
}

export function iniciarMedidas(mundo: Mundo, s: { session: string; guildId: string | null }, platform: string): void {
  const { renderer, calidad } = mundo.contexto;
  const hist = new Uint32Array(FPS_TOPE + 1);
  let segundos = 0;
  let fotogramas = 0;
  let llamadas = 0;
  let llamadasMax = 0;
  let triangulos = 0;
  let triangulosMax = 0;
  let previo = -1;
  let enviado = -Infinity;
  let primero = true;
  let base = { bajadas: calidad.bajadas, subidas: calidad.subidas, reconexiones };

  mundo.contexto.enganchar((_dt, ahora) => {
    entrada.frame ??= performance.now();
    const real = (ahora - previo) / 1000;
    previo = ahora;
    if (real <= 0 || real > 1) return; // el primero y las pausas (pestaña oculta, editor) no cuentan
    // Lo dibujado en el fotograma anterior, sombras incluidas.
    const { calls, triangles } = renderer.info.render;
    segundos += real;
    fotogramas++;
    hist[Math.min(FPS_TOPE, Math.round(1 / real))]++;
    llamadas += calls;
    triangulos += triangles;
    if (calls > llamadasMax) llamadasMax = calls;
    if (triangles > triangulosMax) triangulosMax = triangles;
  });

  const percentil = (): number => {
    let n = 0;
    for (let f = 0; f <= FPS_TOPE; f++) if ((n += hist[f]) >= fotogramas * PERCENTIL) return f;
    return FPS_TOPE;
  };

  const enviar = (): void => {
    const ahora = performance.now();
    if (!s.guildId || ahora - enviado < MIN_MS || (!primero && fotogramas === 0)) return;
    enviado = ahora;
    const cuerpo: Record<string, unknown> = {
      guild_id: s.guildId,
      platform,
      segundos,
      fotogramas,
      fps_p5: fotogramas ? percentil() : 0,
      llamadas: fotogramas ? llamadas / fotogramas : 0,
      llamadas_max: llamadasMax,
      triangulos: fotogramas ? triangulos / fotogramas : 0,
      triangulos_max: triangulosMax,
      ratio: calidad.ratio,
      ratio_max: calidad.inicial,
      sombras: calidad.sombras,
      bajadas: calidad.bajadas - base.bajadas,
      subidas: calidad.subidas - base.subidas,
      reconexiones: reconexiones - base.reconexiones,
    };
    if (primero) {
      const gl = renderer.getContext();
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      const nombre: unknown = gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
      Object.assign(cuerpo, {
        entrada,
        gpu: typeof nombre === "string" ? familiaGpu(nombre) : "otra",
        ancho: Math.round(window.innerWidth / 100) * 100,
        alto: Math.round(window.innerHeight / 100) * 100,
      });
      primero = false;
    }
    base = { bajadas: calidad.bajadas, subidas: calidad.subidas, reconexiones };
    hist.fill(0);
    segundos = fotogramas = llamadas = llamadasMax = triangulos = triangulosMax = 0;
    fetch("/api/medidas", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${s.session}` },
      body: JSON.stringify(cuerpo),
      keepalive: true,
    }).catch(() => {});
  };
  setInterval(enviar, CADA_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") enviar();
  });
}
