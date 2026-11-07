"""Los pintores comunes de las tragaperras (`assets/tragas.js`, objeto `Tragas`).

La clásica los usa: que salga igual píxel a píxel que antes de sacarlos lo comprueban las pruebas de
`test_slots_scene.py` (reparto entre procesos incluido). Aquí, lo que la clásica no ejerce: los
tintes, el tamaño y el texto de los carteles, que los de serie sean los de siempre y la salida.
Las que pintan se saltan si no hay Node con `@napi-rs/canvas`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from test_node_scene import needs_node, node_ready

from bot.services.node_scene import NodeScene
from bot.services.slots_scene import SCENE

module_loop = pytest.mark.asyncio(loop_scope="module")
TIERS = ("big", "mega", "epic")

#: Pinta un cartel en una capa y devuelve una firma de sus píxeles.
DRAW = """
(() => {
  const sign = (b, opts) => {
    const l = Tragas.layer(520, 130);
    Tragas.drawBanner(l.g, { scale: 1, flash: 0, spin: 7, ...b }, { x: 260, y: 65, ...opts });
    const d = l.g.getImageData(0, 0, l.c.width, l.c.height).data;
    let h = 0;
    for (let i = 0; i < d.length; i++) h = (h * 31 + d[i]) | 0;
    return h;
  };
  const blank = sign({ tier: "big", scale: 0 });
  const out = { blank };
  for (const tier of ["big", "mega", "epic"]) {
    const three = ["#112233", "#445566", "#778899"];
    const own = { [tier]: { edge: "#00ff00", plate: three, rays: three } };
    out[tier] = {
      plain: sign({ tier }),
      serie: sign({ tier }, { tint: { [tier]: Tragas.TINT[tier] } }),
      tinted: sign({ tier }, { tint: own }),
      small: sign({ tier }, { size: 0.5 }),
      text: sign({ tier, text: "OTRO" }),
      flash: sign({ tier, flash: 1 }),
    };
  }
  return out;
})()
"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def painter() -> NodeScene:
    """El pintor de la clásica, que ya carga `tragas.js`, compartido por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    scene = NodeScene(SCENE, name="El pintor de la tragaperras")
    yield scene
    await scene.close()


def test_la_clasica_carga_tragas_antes_que_su_propio_script() -> None:
    html = SCENE.read_text(encoding="utf-8")
    shared = html.index('<script src="../tragas.js"></script>')
    # El código de la escena va en escena.js, sin scripts en línea (la página del
    # Jovaniverso lo importa como módulo y el CSP de Discord no los deja).
    assert shared < html.index('<script src="escena.js"></script>')
    assert "<script>" not in html
    assert (Path(SCENE).parent.parent / "tragas.js").is_file()
    assert (Path(SCENE).parent / "escena.js").is_file()


@needs_node
@module_loop
async def test_los_carteles_de_serie_son_los_de_siempre_y_cada_opcion_los_cambia(
    painter: NodeScene,
) -> None:
    got = await painter.evaluate(DRAW)
    for tier in TIERS:
        sign = got[tier]
        assert sign["plain"] != got["blank"]
        assert sign["serie"] == sign["plain"]
        # Cada cosa que se cambia da un cartel distinto del de serie (y distinto entre sí).
        changed = [sign[k] for k in ("tinted", "small", "text", "flash")]
        assert len({sign["plain"], *changed}) == 5


@needs_node
@module_loop
async def test_la_salida_pide_un_lienzo_de_medida_par(painter: NodeScene) -> None:
    check = (
        "(() => { const c = document.createElement('canvas'); c.width = 301; c.height = 200;"
        " try { Tragas.output(c); return 'sin error'; } catch (e) { return e.message; } })()"
    )
    assert "par" in await painter.evaluate(check)
