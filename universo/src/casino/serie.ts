// El casino de la plaza como datos del editor (seis tragaperras, su toldo y el letrero del bote), de `casino_plaza.json`: lo usan el
// mundo de serie y el extraplano de pruebas, y el servidor se lo da a los mundos guardados antes de que las máquinas fueran
// objetos (`add_casino` de `bot/services/universo_editor.py`). Sin imports del mundo: la pista lo carga y el terreno carga la pista.
import casino from "../../../src/bot/assets/casino_plaza.json";
import type { Asset, Objeto } from "../editor/formato";

/** Assets y objetos nuevos (una copia, para que el editor pueda cambiarlos). */
export function casinoDeSerie(): { assets: Record<string, Asset>; objetos: Objeto[] } {
  return structuredClone(casino) as unknown as { assets: Record<string, Asset>; objetos: Objeto[] };
}
