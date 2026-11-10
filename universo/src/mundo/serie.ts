// El mundo de serie de la isla como datos (`MundoDoc`): el pueblo, los hitos y la vegetación que antes se generaban con código.
// Es lo que dibuja `mundo.ts` al arrancar (por `Obras`, como cualquier mundo publicado) y desde lo que empieza el editor
// cuando nadie ha guardado ni publicado nada. Determinista: las mismas semillas y las mismas posiciones de siempre. El casino de la
// plaza (seis tragaperras, su toldo y el letrero del bote) viene de `casino_plaza.json`, que el servidor también usa para dárselo a
// los mundos guardados antes de que las máquinas fueran objetos (`add_casino` de `universo_editor.py`). El edificio de pisos del
// oeste de la plaza, con su puerta y su interior, viene de `universo_piso.json` (`pisoDeSerie`), que el servidor usa para dejar
// entrar cuando no hay nada publicado.
import { casinoDeSerie } from "../casino/serie";
import { pisoDeSerie } from "../interiores/serie";
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
  const c = casinoDeSerie();
  const p = pisoDeSerie();
  return { v: 2, assets: { ...a.assets, ...c.assets, ...p.assets }, objetos: [...a.objetos, ...c.objetos, ...p.objetos], interiores: p.interiores, zonas: zonasDeSerie() };
}

