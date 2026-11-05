"""El retrato de una mascota con canvas: en Node y, si no, en Chromium.

La escena (`assets/mascotas/escena.html`) dibuja una ficha como las pantallas de los negocios
de `pala`: una cabecera con el nombre, la mascota (su especie y su raza,
`bot.services.pets_looks`) con lo que lleva puesto (`bot.services.pet_wardrobe`, pintado por
`assets/mascotas/ropa.js`), su fondo y su cara del momento en un marco, y unas tarjetas con su
vínculo, su rareza, la racha y lo que lleva. Con dos mascotas es una visita y con `caption`, una
foto del álbum. No decide nada: Python le pasa el estado ya hecho (`portrait_state`), con los
textos de las tarjetas en `card`. Mide `W` × `H` y sale en PNG.

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
      "card": {                                       # los textos de la ficha (ver `card`)
          "title", "sub", "theme": [principal, acento], "active", "level", "xp",
          "rarity", "stars", "breed", "breedNote", "streak", "streakNote",
          "wearing": [{"slot", "name"}], "quote", "tricks",
          # en una visita: "guests": [{"role", "name", "sub", "level"}], "note"
      },
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
from bot.services.pets import PetState, unlocked_tricks
from bot.services.pets_catalog import RARITIES, Species
from bot.services.pets_looks import Breed, look_of, slots_of

SCENE = Path(__file__).resolve().parent.parent / "assets" / "mascotas" / "escena.html"
W = 1000
H = 640

__all__ = [
    "H",
    "MOODS",
    "SCENE",
    "W",
    "PetScene",
    "card",
    "pet_entry",
    "portrait_key",
    "portrait_state",
    "visit_card",
]

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


#: Colores de la cabecera por pasillo de la especie: `(principal, acento)`.
THEMES: dict[str, tuple[str, str]] = {
    "animales": ("#2f7d4f", "#f6d26b"),
    "canarias": ("#0f4c81", "#f6bd2d"),
    "boricua": ("#b3261e", "#ffffff"),
    "hongkong": ("#8a1c2b", "#f6bd2d"),
    "lujo": ("#4b2a7a", "#f6d26b"),
    "moncloa": ("#9a2a2a", "#ffd9d0"),
    "shitpost": ("#2a242d", "#7bd957"),
    "espana": ("#c0392b", "#f6bd2d"),
}
#: Lo que lleva, en el orden de la tarjeta.
_WEAR_ORDER = (Slot.HEAD, Slot.EYES, Slot.NECK, Slot.BODY, Slot.BACKDROP)


def card(
    state: PetState,
    species: Species,
    breed: Breed,
    look: Mapping[Slot, Outfit],
    *,
    name: str,
    xp: str,
    active: bool,
    streak: int,
    best_streak: int,
) -> dict[str, Any]:
    """Los textos de la ficha de una mascota.

    Solo lleva lo que cambia pocas veces (el nivel y no el vínculo exacto, la racha y no los
    cuidados de hoy): así acariciar no cambia la imagen y el panel no la vuelve a subir.
    """
    breeds = look_of(state.species).breeds
    tricks = species.tricks[: unlocked_tricks(state.level)]
    return {
        "title": name,
        "sub": f"{species.name} · {breed.name}" if len(breeds) > 1 else species.name,
        "theme": list(THEMES.get(species.aisle, THEMES["animales"])),
        "active": active,
        "level": state.level,
        "xp": xp,
        "rarity": species.rarity,
        "stars": RARITIES.index(species.rarity) + 1 if species.rarity in RARITIES else 1,
        "breed": breed.name if len(breeds) > 1 else "",
        "breedNote": (
            ("Raza de serie" if breed is breeds[0] else "Raza comprada") if len(breeds) > 1 else ""
        ),
        "streak": streak,
        "streakNote": (
            f"Mejor racha: {best_streak} días" if best_streak else "Cuida hoy para empezarla"
        ),
        "wearing": [
            {"slot": slot.value, "name": look[slot].name} for slot in _WEAR_ORDER if slot in look
        ],
        "quote": species.personality,
        "tricks": (
            "Trucos: " + " · ".join(t.split(".")[0] for t in tricks)
            if tricks
            else "Aprende su primer truco a nivel 3 de vínculo."
        ),
    }


def visit_card(
    guest: tuple[PetState, Species, str], host: tuple[PetState, Species, str]
) -> dict[str, Any]:
    """Los textos de la ficha de una visita: quién viene y quién recibe."""
    (g_state, g_species, g_name), (h_state, h_species, h_name) = guest, host
    return {
        "title": f"{g_name} y {h_name}",
        "sub": f"De visita en casa de {h_name}",
        "theme": list(THEMES.get(h_species.aisle, THEMES["animales"])),
        "guests": [
            {
                "role": "Viene de visita",
                "name": g_name,
                "sub": g_species.name,
                "level": g_state.level,
            },
            {
                "role": "Recibe en casa",
                "name": h_name,
                "sub": h_species.name,
                "level": h_state.level,
            },
        ],  # fmt: skip
        "note": "Una tarde de juegos. La foto va al álbum de las dos mascotas.",
        "quote": "Una visita suma vínculo a las dos mascotas, una vez al día cada una.",
        "tricks": "",
    }


def portrait_state(
    pets: list[dict[str, Any]],
    *,
    backdrop: Outfit | None = None,
    mood: str = "tranquila",
    card: dict[str, Any] | None = None,
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
        "card": card or {},
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
