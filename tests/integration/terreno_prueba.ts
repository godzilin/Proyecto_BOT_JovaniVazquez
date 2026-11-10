// Guiones de `test_universo_terreno.py`: el terreno por celdas con `base` (`universo/src/mundo/terreno.ts`) y su paso a trozos
// (`editor/trozos.ts`), sin navegador ni dibujo.
import type { MundoDoc, Terreno } from "../../universo/src/editor/formato";
import { aTrozos, deTrozos, parchear } from "../../universo/src/editor/trozos";
import {
  celdasDelVertice,
  NV,
  numeroDe,
  POR_CELDA,
  ponerRejilla,
  ponerResumen,
  quitarRejilla,
  rejillaEditable,
  rejillas,
  resumenDe,
  resumenes,
  terrenoDe,
  terrenoEn,
  verticeEn,
} from "../../universo/src/mundo/terreno";

const alt = (cx: number, cz: number): Float32Array => (rejillas.get(numeroDe(cx, cz)) as { alt: Float32Array }).alt;

/** Un cerro de `alto` m con la cima en (x, z) que cruza el borde entre dos celdas, escrito vértice a vértice como el pincel. */
function cerro(x: number, z: number, alto: number, radio: number, terrenos: Record<string, Terreno>): void {
  const g = Math.round(radio / 2);
  for (let gz = z / 2 - g; gz <= z / 2 + g; gz++) {
    for (let gx = x / 2 - g; gx <= x / 2 + g; gx++) {
      const d = Math.hypot(gx * 2 - x, gz * 2 - z) / radio;
      if (d >= 1) continue;
      for (const [cx, cz, i] of celdasDelVertice(gx, gz)) {
        const r = rejillaEditable(cx, cz, terrenos[`c/${cx}/${cz}`]);
        r.alt[i] = Math.max(r.alt[i], alto * (1 - d * d));
      }
    }
  }
}

/** El Teide (371 m) en el borde entre las celdas 10/3 y 11/3, fuera de la isla, y lo que se lee al volver del documento. */
export function teide(): unknown {
  rejillas.clear();
  // Las dos celdas son mar sin terreno: se crean al primer trazo con el suelo que se ve (el fondo).
  const fondo = terrenoEn(10 * 80 + 40, 3 * 80 + 40);
  cerro(880, 280, 371, 70, {});
  // Una meseta a 200 m en el resto de la 11/3 (sin tocar su borde con la 10/3): las dos celdas necesitan bases distintas.
  const meseta = alt(11, 3);
  for (let i = 0; i < meseta.length; i++) if (i % NV > 0) meseta[i] = Math.max(meseta[i], 200);
  const a = terrenoDe(rejillas.get(numeroDe(10, 3))!);
  const b = terrenoDe(rejillas.get(numeroDe(11, 3))!);
  const vivo = [alt(10, 3), alt(11, 3)].map((x) => x.slice());
  // Vuelta del documento: se leen como las lee la página.
  quitarRejilla(10, 3);
  quitarRejilla(11, 3);
  ponerRejilla(10, 3, a.terreno);
  ponerRejilla(11, 3, b.terreno);
  let difMax = 0;
  for (const [k, [cx, cz]] of [[10, 3], [11, 3]].entries()) {
    const leido = alt(cx, cz);
    for (let i = 0; i < leido.length; i++) difMax = Math.max(difMax, Math.abs(leido[i] - vivo[k][i]));
  }
  // El borde x = 880: la última columna de la 10/3 y la primera de la 11/3.
  let escalon = 0;
  for (let k = 0; k < NV; k++) escalon = Math.max(escalon, Math.abs(alt(10, 3)[k * NV + NV - 1] - alt(11, 3)[k * NV]));
  return {
    fondo,
    terrenos: { "c/10/3": a.terreno, "c/11/3": b.terreno },
    recortado: a.recortado || b.recortado,
    cima: terrenoEn(880, 280),
    escalon,
    difMax,
    resumenes: { "c/10/3": resumenDe(a.terreno), "c/11/3": resumenDe(b.terreno) },
    // El horizonte de la 11/3 leído de su resumen frente a su rejilla, cada 20 m.
    horizonte: horizonte(11, 3, resumenDe(b.terreno)),
  };
}

/** La mayor diferencia (m) entre las 5 × 5 alturas que la página lee del resumen y las de la rejilla de la celda. */
function horizonte(cx: number, cz: number, b64: string): number {
  ponerResumen(cx, cz, b64);
  const lejos = resumenes.get(numeroDe(cx, cz))!;
  const cerca = alt(cx, cz);
  let dif = 0;
  for (let i = 0; i < 25; i++) dif = Math.max(dif, Math.abs(lejos[i] - cerca[Math.floor(i / 5) * 10 * NV + (i % 5) * 10]));
  return dif;
}

/** Una celda con más desnivel del que cabe en 16 bits: se recorta por abajo. */
export function desnivel(): unknown {
  rejillas.clear();
  const r = rejillaEditable(-30, 2);
  r.alt.fill(100);
  r.alt[0] = 900; // 800 m de desnivel
  const t = terrenoDe(r);
  ponerRejilla(-30, 2, t.terreno);
  const leido = alt(-30, 2);
  return { recortado: t.recortado, alto: leido[0], bajo: Math.min(...leido), base: t.terreno.base ?? 0 };
}

/** Lo de antes (sin `base`) se lee igual y sale igual: no hay que migrar nada. */
export function sinBase(): unknown {
  rejillas.clear();
  const r = rejillaEditable(0, 0); // de la isla de serie
  const t = terrenoDe(r);
  return { base: "base" in t.terreno, max: Math.max(...r.alt), min: Math.min(...r.alt) };
}

/** Una celda nueva junto a una con terreno empieza con su borde: no hay escalón. */
export function vecinaNueva(): unknown {
  rejillas.clear();
  const r = rejillaEditable(5, 5);
  r.alt.fill(250.5);
  const nueva = rejillaEditable(6, 5);
  let escalon = 0;
  for (let k = 0; k < NV; k++) escalon = Math.max(escalon, Math.abs(nueva.alt[k * NV] - r.alt[k * NV + NV - 1]));
  return { escalon, borde: verticeEn(6 * POR_CELDA, 5 * POR_CELDA + 3), dentro: nueva.alt[NV + 5] };
}

/** El documento con el terreno por celdas pasa a trozos y vuelve igual, con `base`; `parchear` cambia solo la celda tocada. */
export function trozos(): unknown {
  rejillas.clear();
  const terreno: Record<string, Terreno> = {};
  for (const [cx, cz, h] of [[-1, 0, 5], [40, -7, 3000]] as const) {
    const r = rejillaEditable(cx, cz);
    r.alt.fill(h);
    terreno[`c/${cx}/${cz}`] = terrenoDe(r).terreno;
  }
  const doc: MundoDoc = { v: 2, assets: {}, objetos: [], zonas: {}, terreno };
  const t = aTrozos(doc);
  const junto = deTrozos(Object.fromEntries([...t].map(([k, v]) => [k, JSON.parse(v)])));
  const otro = { ...terreno["c/40/-7"], base: (terreno["c/40/-7"].base as number) + 100 };
  const parcheado = parchear(doc, { "c/40/-7": { objetos: [], terreno: otro }, "c/-1/0": null });
  return { trozos: Object.fromEntries(t), junto: junto.terreno, parcheado: parcheado.terreno };
}
