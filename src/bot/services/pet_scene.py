"""El retrato de una mascota con canvas: en Node y, si no, en Chromium.

La escena (`assets/mascotas/escena.html`) dibuja la mascota (su especie y su raza,
`bot.services.pets_looks`), lo que lleva puesto (`bot.services.pet_wardrobe`), el fondo y
su cara del momento. No decide nada: Python le pasa el estado ya hecho (`portrait_state`).
Mide `W` × `H` y sale en PNG.

Se prueba Node con Skia (`bot.services.node_scene`) y, si falla, Chromium
(`bot.services.browser_scene`). No hay Pillow de reserva: si ninguno puede, `paint`
devuelve `None` y el panel de `mascota` sale en texto, como antes del retrato.

**Contrato con la escena** (un estado por imagen)::

    {
      "backdrop": {"shape", "colors", "pattern"},     # el fondo (pet_wardrobe)
      "pets": [{                                      # una o dos (una visita)
          "species": "gato", "plan": "gato",          # pets_looks
          "look": {...},                              # parámetros de la raza
          "outfit": {"cabeza": {"shape", "colors", "pattern"}, ...},
      }],
      "mood": "tranquila" | "contenta" | "dormida" | "fiesta",
      "plate": {"name": "Michi", "line": "Gato · Nivel 4"} | None,
      "caption": "Nivel 6 · 12/10/2026" | None,       # marco de foto del álbum
      "cake": False,                                  # la tarta del aniversario
      "seed": 0,                                      # el azar de la decoración
    }
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png
from bot.services.pet_wardrobe import HOME, Outfit, Slot
from bot.services.pets_looks import Breed, look_of, slots_of

SCENE = Path(__file__).resolve().parent.parent / "assets" / "mascotas" / "escena.html"
W = 640
H = 400

__all__ = ["H", "MOODS", "SCENE", "W", "PetScene", "pet_entry", "portrait_key", "portrait_state"]

#: Caras del momento que entiende la escena.
MOODS: tuple[str, ...] = ("tranquila", "contenta", "dormida", "fiesta")
#: Nivel de compresión del PNG: el 3 tarda bastante menos que el 6 y pesa casi lo mismo.
PNG_LEVEL = 3


def pet_entry(species: str, breed: Breed, look: Mapping[Slot, Outfit]) -> dict[str, Any]:
    """Una mascota para la escena: su dibujo y lo que lleva en los huecos que tiene."""
    plan = look_of(species).plan
    allowed = set(slots_of(species))
    return {
        "species": species,
        "plan": plan,
        "look": dict(breed.look),
        "outfit": {
            slot.value: outfit.drawing()
            for slot, outfit in look.items()
            if slot is not Slot.BACKDROP and slot.value in allowed
        },
    }


def portrait_state(
    pets: list[dict[str, Any]],
    *,
    backdrop: Outfit | None = None,
    mood: str = "tranquila",
    plate: dict[str, str] | None = None,
    caption: str | None = None,
    cake: bool = False,
    seed: int = 0,
) -> dict[str, Any]:
    """El estado de un retrato (ver el contrato en el módulo)."""
    if mood not in MOODS:
        raise ValueError(f"Cara desconocida: {mood}")
    return {
        "backdrop": (backdrop or HOME).drawing(),
        "pets": pets,
        "mood": mood,
        "plate": plate,
        "caption": caption,
        "cake": cake,
        "seed": seed,
    }


def portrait_key(state: Mapping[str, Any]) -> str:
    """Resumen de un retrato: si no cambia, la imagen es la misma y no se vuelve a subir."""
    return hashlib.sha1(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]


def _png(patch: dict[str, Any]) -> bytes:
    if "image" not in patch:
        return patch_png(patch)
    buffer = io.BytesIO()
    patch["image"].save(buffer, format="PNG", compress_level=PNG_LEVEL)
    return buffer.getvalue()


class PetScene:
    """Dibuja retratos de mascotas con Node y, si no, con Chromium.

    Args:
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(self, *, executable_path: str | None = None, node: str | None = None) -> None:
        self.painter = NodeScene(SCENE, name="El pintor de las mascotas", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de las mascotas",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya no se dibuja nada."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes] | None:
        """Un PNG por estado, en el mismo orden; `None` si ni Node ni Chromium pueden."""
        if not states:
            return []
        meta = {"w": W, "h": H}
        patches = await self.painter.render(meta, states, gif=False)
        if patches is None:
            patches = await self.browser.render(meta, states)
        if patches is None:
            return None
        return [await asyncio.to_thread(_png, patch) for patch in patches]

    async def still(self, state: dict[str, Any]) -> bytes | None:
        """PNG de un retrato; `None` si no se puede pintar."""
        pngs = await self.paint([state])
        return pngs[0] if pngs else None
