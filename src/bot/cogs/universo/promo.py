"""El botón 🌌 Jovaniverso que se añade a todos los paneles del bot.

Se pone al serializar cada vista (`install_promo`), sin tocar ninguna. Lo contesta
`Universo.on_interaction`, con `promo_embed`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import discord
from discord import ui

from bot.utils.interactions import claim_custom_ids

#: `custom_id` del botón 🌌 Jovaniverso que se añade a los paneles del bot; detrás va
#: el módulo del panel (`universo:promo:coin`). Lo contesta `Universo.on_interaction`.
PROMO_ID = "universo:promo:"
#: Módulos sin el botón: el propio universo y las herramientas de administración.
PROMO_SKIP = frozenset({"universo", "admin", "shop_admin", "deploy", "moderation", "telemetry"})
#: Lo que se dice al pulsar el botón en un panel cuyo juego aún no está dentro.
PROMO_SOON = (
    "Esto tendrá una **versión mejorada** en el Jovaniverso: el bot convertido en un "
    "mundo 3D dentro de Discord, donde lo juegas con tu personaje y con la gente del "
    "servidor alrededor."
)
#: Paneles cuyo juego ya está en el Jovaniverso.
PROMO_READY = {
    "slots": (
        "Las tragaperras **ya están** en la plaza del Jovaniverso: seis máquinas que se "
        "juegan en primera persona, con sonido, ▶️ Auto y el mismo saldo de siempre."
    ),
}
PROMO_WORKS = (
    "Entras con tu cuenta de Discord, paseas por Gran Canaria con tu robot, te cruzas "
    "con quien esté dentro, haces los gestos que compres en la tienda y juegas en las "
    "tragaperras de la plaza con tu saldo. Lo demás del bot irá llegando."
)
PROMO_HOW = "Escribe `/universo` o pulsa **Abrir el Jovaniverso** aquí debajo."
PROMO_HOW_CLOSED = "Aún no está abierto en este servidor; cuando lo esté, se entra con `/universo`."


def promo_embed(panel: str, *, is_open: bool) -> discord.Embed:
    """El aviso privado del botón 🌌 Jovaniverso de un panel (`panel`, su módulo)."""
    embed = discord.Embed(
        title="🌌 Jovaniverso",
        description=PROMO_READY.get(panel, PROMO_SOON),
        color=discord.Color.dark_purple(),
    )
    embed.add_field(name="Ya funciona", value=PROMO_WORKS, inline=False)
    embed.add_field(
        name="Cómo entrar", value=PROMO_HOW if is_open else PROMO_HOW_CLOSED, inline=False
    )
    return embed


def _promo_button(view: ui.BaseView) -> dict[str, Any] | None:
    """El botón 🌌 Jovaniverso de `view`, o `None` si ese panel no lo lleva."""
    module = type(view).__module__
    panel = module.rsplit(".", 1)[-1]
    if not module.startswith("bot.") or panel in PROMO_SKIP:
        return None
    return {
        "type": 2,
        "style": discord.ButtonStyle.secondary.value,
        "label": "Jovaniverso",
        "emoji": {"name": "🌌"},
        "custom_id": PROMO_ID + panel,
    }


def _with_promo(original: Callable[[Any], list[dict[str, Any]]]) -> Any:
    """Envuelve `to_components` para que cada panel lleve el botón 🌌 Jovaniverso.

    Va al serializar y no en cada vista: así llega a todas, también tras un
    `clear_items()`, sin tocar ninguna. Solo entra si cabe: en una fila nueva (en un
    `View`, si hay menos de 5) o al final de la última fila de botones; en un
    `LayoutView`, en una fila suya si no pasa de 40 piezas. Una vista vacía (la que se
    manda para quitar los botones) se queda vacía.
    """

    @functools.wraps(original)
    def to_components(self: Any) -> list[dict[str, Any]]:
        components = original(self)
        button = _promo_button(self) if components else None
        if button is None:
            return components
        if isinstance(self, ui.LayoutView):
            if len(tuple(self.walk_children())) + 2 <= 40:
                components.append({"type": 1, "components": [button]})
        elif len(components) < 5:
            components.append({"type": 1, "components": [button]})
        else:
            last = components[-1]["components"]
            if len(last) < 5 and all(item["type"] == 2 for item in last):
                last.append(button)
        return components

    to_components.__promo__ = True  # type: ignore[attr-defined]
    return to_components


def install_promo() -> None:
    """Pone el botón 🌌 Jovaniverso en todos los paneles (una vez aunque se llame más)."""
    for cls in (ui.View, ui.LayoutView):
        if not getattr(cls.to_components, "__promo__", False):
            cls.to_components = _with_promo(cls.to_components)  # type: ignore[method-assign]
    claim_custom_ids(PROMO_ID)


def uninstall_promo() -> None:
    """Quita el botón 🌌 Jovaniverso de los paneles."""
    for cls in (ui.View, ui.LayoutView):
        if getattr(cls.to_components, "__promo__", False):
            cls.to_components = cls.to_components.__wrapped__  # type: ignore[method-assign]
