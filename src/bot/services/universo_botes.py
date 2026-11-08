"""Botes que puede enseñar un letrero del Jovaniverso.

Un letrero (un asset con `funcion` `letrero`, ver `bot.services.universo_editor`) dice en sus
`datos.botes` qué botes enseña, por la clave de `POTS`. Solo están los botes que son de todo
el servidor y crecen con lo que juega todo el mundo: el de la tragaperras, el 💎 del póker, el
Gran Premio de los caballos y los de las loterías de bote. Los MINI y MAJOR de Botes son de
cada jugador, así que no salen.

Las cifras las pide `bot.cogs.universo` a cada juego con sus funciones puente; aquí solo está
el catálogo (clave, nombre y emoji, en el orden en que salen en el editor) y el texto.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.economy import format_amount
from bot.services.lottery import GAME_BY_KEY

#: Loterías con bote que se acumula (`bot.cogs.lottery.POOL_GAMES`).
LOTTERY_POTS = ("primitiva", "bonoloto", "gordo", "euromillones")


@dataclass(frozen=True, slots=True)
class PotInfo:
    """Un bote que se puede poner en un letrero.

    Attributes:
        key: La clave que guarda el letrero en `datos.botes`.
        name: Cómo se llama en el letrero y en el editor.
        emoji: El del juego.
    """

    key: str
    name: str
    emoji: str


POTS: tuple[PotInfo, ...] = (
    PotInfo("tragas", "Tragaperras", "🎰"),
    PotInfo("poker", "Bote del póker", "💎"),
    PotInfo("caballos", "Gran Premio", "🏇"),
    *(PotInfo(k, GAME_BY_KEY[k].name, GAME_BY_KEY[k].emoji) for k in LOTTERY_POTS),
)
POT_BY_KEY = {p.key: p for p in POTS}


def pot_catalog() -> list[dict[str, str]]:
    """Todos los botes que se pueden poner en un letrero, aunque su juego no esté cargado."""
    return [{"clave": p.key, "nombre": p.name, "emoji": p.emoji} for p in POTS]


def pot_list(amounts: dict[str, int]) -> list[dict[str, str]]:
    """Los botes que se saben, en el orden del catálogo, listos para la página.

    Args:
        amounts: Clave → cifra; los que falten (el juego no está cargado) no salen.
    """
    return [
        {"clave": p.key, "nombre": p.name, "emoji": p.emoji, "texto": format_amount(amounts[p.key])}
        for p in POTS
        if p.key in amounts
    ]
