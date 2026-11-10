// El piso de serie como datos del editor: un edificio con su puerta, al oeste de la plaza mirando hacia ella, y su interior (un
// salón con muebles que se ponen y se quitan y cosas que se arreglan), de `universo_piso.json`. Lo usan el mundo de serie y el
// extraplano de pruebas (en otro sitio), y el servidor lo lee para dejar entrar cuando no hay nada publicado
// (`bot/cogs/universo/interiores.py`). Sin imports del mundo: la pista lo carga y el terreno carga la pista.
import piso from "../../../src/bot/assets/universo_piso.json";
import type { Asset, Interior, Objeto } from "../editor/formato";

interface Piso {
  assets: Record<string, Asset>;
  objetos: Objeto[];
  interiores: Record<string, Interior>;
}

/**
 * Assets, objetos (el edificio y su puerta) e interiores nuevos (una copia, para que el editor pueda cambiarlos). Con `donde` y
 * `giro`, el edificio y su puerta van ahí (el extraplano) en vez de en la plaza de la isla.
 */
export function pisoDeSerie(donde?: [number, number, number], giro?: number): Piso {
  const p = structuredClone(piso) as unknown as Piso;
  if (!donde || giro === undefined) return p;
  const [edificio, ...resto] = p.objetos;
  const [x0, , z0] = edificio.pos;
  const delta = giro - edificio.rot[1];
  const cos = Math.cos(delta);
  const sin = Math.sin(delta);
  for (const o of [edificio, ...resto]) {
    const dx = o.pos[0] - x0;
    const dz = o.pos[2] - z0;
    o.pos = [donde[0] + dx * cos + dz * sin, donde[1], donde[2] - dx * sin + dz * cos];
    o.rot = [o.rot[0], o.rot[1] + delta, o.rot[2]];
  }
  return p;
}
