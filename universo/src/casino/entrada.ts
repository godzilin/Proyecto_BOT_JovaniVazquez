// Puerta del casino: pone el mueble en la plaza y carga el resto (pantallas, botones, sonido) cuando el jugador se acerca.
import type { Mundo } from "../mundo";
import { PLAZA } from "../mundo/terreno";
import type { ConfigCasino } from "./casino";
import { crearMueble } from "./mueble";

const DISTANCIA_CARGA = 80; // m a la plaza

export function activarCasino(mundo: Mundo, cfg: ConfigCasino): void {
  const { escena, culling, yo, enganchar } = mundo.contexto;
  const mueble = crearMueble();
  escena.add(mueble);
  culling.registrarMalla(mueble, 330);

  let listo: () => void = () => undefined;
  const jv = (window as unknown as { __jv?: Record<string, unknown> }).__jv;
  if (jv) jv.casinoListo = new Promise<void>((ok) => (listo = ok));

  let pedido = false;
  enganchar(() => {
    if (pedido || Math.hypot(yo.x - PLAZA.x, yo.z - PLAZA.z) > DISTANCIA_CARGA) return;
    pedido = true;
    import("./casino")
      .then((m) => m.montarCasino(mundo.contexto, cfg))
      .catch((error) => console.error("No se ha podido montar el casino", error))
      .finally(listo);
  });
}
