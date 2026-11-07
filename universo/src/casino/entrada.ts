// Puerta del casino: el mueble de las máquinas y los letreros son objetos del mundo (los dibuja `Obras`); el resto (pantallas,
// botones, paneles de los letreros, sonido) se carga con import() cuando el jugador se acerca a alguno y se vuelve a colocar
// cada vez que cambia el mundo (lo publicado, el editor).
import type { MundoDoc } from "../editor/formato";
import type { Mundo } from "../mundo";
import type { Casino, ConfigCasino } from "./casino";

const DISTANCIA_CARGA = 80; // m a la máquina o el letrero más cercano

/** Dónde hay algo del casino en `doc` (x, z). */
function sitios(doc: MundoDoc): [number, number][] {
  return doc.objetos.filter((o) => doc.assets[o.asset]?.funcion).map((o) => [o.pos[0], o.pos[2]]);
}

export function activarCasino(mundo: Mundo, cfg: ConfigCasino): void {
  const ctx = mundo.contexto;
  let doc = ctx.doc();
  let cerca = sitios(doc);
  let casino: Casino | null = null;
  let pedido = false;

  let listo: () => void = () => undefined;
  const jv = (window as unknown as { __jv?: Record<string, unknown> }).__jv;
  if (jv) jv.casinoListo = new Promise<void>((ok) => (listo = ok));

  ctx.alCargar((d) => {
    doc = d;
    cerca = sitios(d);
    casino?.colocar(d);
  });
  let cuenta = 0;
  ctx.enganchar(() => {
    if (pedido || cuenta++ % 15) return; // mirarlo cuatro veces por segundo basta
    const { x, z } = ctx.yo;
    if (!cerca.some(([sx, sz]) => Math.hypot(x - sx, z - sz) <= DISTANCIA_CARGA)) return;
    pedido = true;
    import("./casino")
      .then((m) => m.montarCasino(ctx, cfg))
      .then((c) => {
        casino = c;
        c.colocar(doc);
      })
      .catch((error) => console.error("No se ha podido montar el casino", error))
      .finally(listo);
  });
}
