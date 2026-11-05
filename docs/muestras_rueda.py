"""Muestras de la Rueda: pinta cada caso y deja su GIF, su PNG y una hoja de fotogramas.

Uso (desde la raíz del repositorio):

    python docs/muestras_rueda.py [carpeta]          # por defecto, muestras_rueda/

Para cada caso (antes de girar, premio pequeño, frenada con tensión cerca de una
raya, Inspección, Otra vuelta, el expositor, con el hámster…) deja `<caso>.gif`,
`<caso>.png` y `<caso>_hoja.png` con uno de cada pocos fotogramas, y escribe en
pantalla cuántos fotogramas tiene, lo que dura, lo que pesa el GIF y lo que ha
tardado en pintarse. Sirve para revisar el dibujo y para vigilar el peso del GIF.

Necesita Node con `@napi-rs/canvas` (`npm ci` en la raíz). Si no lo hay, pinta con
Chromium o con Pillow, como el bot.
"""

from __future__ import annotations

import asyncio
import io
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from bot.services import node_scene  # noqa: E402
from bot.services.rueda import PRIZE_BY_KEY, Draw, draw, layout  # noqa: E402
from bot.services.rueda_render import (  # noqa: E402
    banner_for,
    board_state,
    meta_state,
    spin_states,
    tension,
)
from bot.services.rueda_scene import RuedaScene  # noqa: E402

SHOWCASE = {"key": "rueda_titulo_eje", "name": "Eje de la Rueda", "left": 7, "of": 10}


def find(predicate, *, levels: bool = True) -> Draw:  # noqa: ANN001
    """La primera vuelta sorteada que cumple `predicate(wedge, draw)`."""
    wedges = layout(levels=levels)
    rng = random.Random(7)
    for _ in range(200_000):
        wedge, stop, seed = draw(rng, wedges)
        candidate = Draw(wedge.index, wedge.prize.key, stop, seed)
        if predicate(wedge, candidate):
            return candidate
    raise LookupError("No sale ninguna vuelta así")


def cases() -> dict[str, tuple[Draw, dict]]:
    wedges = layout()
    return {
        "pequeno": (find(lambda w, d: d.prize == "y300" and not tension(wedges, d)), {}),
        "tension": (find(lambda w, d: d.prize == "y600" and tension(wedges, d)), {}),
        "nada": (find(lambda w, d: d.prize == "nada"), {}),
        "otra": (find(lambda w, d: d.prize == "otra"), {}),
        "caja": (find(lambda w, d: d.prize == "regalo"), {"detail": "A la mochila"}),
        "pieza": (
            find(lambda w, d: d.prize == "pieza"),
            {"detail": "Caballito del tiovivo · Legendaria"},
        ),
        "expositor": (
            find(lambda w, d: d.prize == "expositor"),
            {"detail": "Eje de la Rueda · n.º 4 de 10"},
        ),
        "gordo": (find(lambda w, d: d.prize == "y15000"), {}),
        "hamster": (find(lambda w, d: d.prize == "xp250"), {"pet": "hamster"}),
    }


def sheet(gif: bytes, every: int = 4) -> Image.Image:
    """Una hoja con uno de cada `every` fotogramas del GIF (y el último)."""
    image = Image.open(io.BytesIO(gif))
    picks = sorted({*range(0, image.n_frames, every), image.n_frames - 1})
    thumbs = []
    for index in picks:
        image.seek(index)
        thumbs.append(image.convert("RGB").resize((image.width // 2, image.height // 2)))
    cols = 6
    rows = (len(thumbs) + cols - 1) // cols
    w, h = thumbs[0].size
    out = Image.new("RGB", (cols * w, rows * h), (0, 0, 0))
    for i, thumb in enumerate(thumbs):
        out.paste(thumb, ((i % cols) * w, (i // cols) * h))
    return out


async def main(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    scene = RuedaScene()
    wedges = layout()
    try:
        meta = meta_state(wedges, showcase=SHOWCASE, streak=4)
        first = find(lambda w, d: True)
        png = await scene.board(meta, board_state(first))
        (folder / "antes.png").write_bytes(png)
        for name, (spin, extra) in cases().items():
            meta = meta_state(wedges, showcase=SHOWCASE, streak=4, pet=extra.get("pet"))
            prize = PRIZE_BY_KEY[spin.prize]
            banner = banner_for(spin.prize, prize.kind, extra.get("detail"))
            states, durations = spin_states(wedges, spin, banner, hamster=meta["pet"] is not None)
            start = time.perf_counter()
            media = await scene.spin(meta, states, durations)
            took = time.perf_counter() - start
            (folder / f"{name}.gif").write_bytes(media.gif)
            (folder / f"{name}.png").write_bytes(media.png)
            sheet(media.gif).save(folder / f"{name}_hoja.png")
            print(
                f"{name:10} {len(states):3} fotogramas  {media.seconds:4.1f} s  "
                f"{len(media.gif) / 1024:6.0f} KB  pintado en {took:4.2f} s"
            )
    finally:
        await scene.close()
        await node_scene.shutdown()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("muestras_rueda")))
