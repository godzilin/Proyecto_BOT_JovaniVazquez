// El mundo de serie de la isla como datos (`MundoDoc`): el pueblo, los hitos y la vegetación que antes se generaban con código.
// Es lo que dibuja `mundo.ts` al arrancar (por `Obras`, como cualquier mundo publicado) y desde lo que empieza el editor
// cuando nadie ha guardado ni publicado nada. Determinista: las mismas semillas y las mismas posiciones de siempre.
import type { MundoDoc } from "../editor/formato";
import { crearCanteras, crearFaro, crearMogan, crearMuelle, crearRoques } from "./hitos";
import { zonasDeSerie } from "./lugares";
import { type Aporte, vacio } from "./piezas";
import { crearPueblo } from "./pueblo";
import { crearVegetacion } from "./vegetacion";

export function mundoDeSerie(): MundoDoc {
  const a: Aporte = vacio();
  crearPueblo(a);
  crearFaro(a);
  crearMuelle(a);
  crearRoques(a);
  crearCanteras(a);
  crearMogan(a);
  crearVegetacion(a);
  return { v: 2, assets: a.assets, objetos: a.objetos, zonas: zonasDeSerie() };
}
