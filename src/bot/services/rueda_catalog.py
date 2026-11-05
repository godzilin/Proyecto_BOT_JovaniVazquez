"""Lo que solo da la Rueda: la colección de la Feria, los títulos del expositor y la chuche.

Son artículos ocultos de la tienda (`visible=False`): no se venden, solo salen de la
rueda (`bot.services.rueda`). `shop_catalog` genera su artículo, como con las
piezas de las cajas o los regalos de las ONG, en el pasillo 🎉 Fiestas y verbenas
(una rueda de feria es una verbena).

- **Colección de la Feria** (`PIECES`): ocho piezas de Común a Mítica. Al contrario
  que en las cajas, todas salen bastante (`Piece.weight`): completarla cuesta
  alrededor de un año de vueltas, o menos cambiando repes en el Rastro. Completarla
  regala el tapete «Feria» (logro `rueda_col_full`).
- **Títulos del expositor** (`TITLES`): seis títulos numerados (`TITLE_STOCK` de
  cada uno en el servidor) que se lucen junto al nombre. Cada semana el expositor
  enseña uno (`rueda.showcase_for`). Se venden y se cambian en el Rastro; el Monte de
  Piedad no los acepta porque no salen de la tienda.
- **Piruleta de feria** (`TREAT`): se come; las mascotas la aceptan en `mascota`.

Su precio de catálogo es lo que valen para el Rastro y el patrimonio, y lo que se
apunta en la Renta como premio en especie al ganarlos.
"""

from __future__ import annotations

from dataclasses import dataclass

AISLE = "cotillon"


@dataclass(frozen=True, slots=True)
class Piece:
    """Una pieza de la colección de la Feria.

    Attributes:
        key: Clave del artículo de la tienda; no se cambia nunca.
        rarity: Rareza que enseña (Común, Rara, Épica, Legendaria o Mítica).
        weight: Lo que pesa al sortear cuál sale (sobre `PIECE_WEIGHT`).
    """

    key: str
    emoji: str
    name: str
    description: str
    rarity: str
    price: int
    weight: int


@dataclass(frozen=True, slots=True)
class Title:
    """Un título numerado del expositor."""

    key: str
    emoji: str
    name: str
    description: str
    price: int


@dataclass(frozen=True, slots=True)
class Treat:
    """La chuche de la rueda: un objeto que se come."""

    key: str
    emoji: str
    name: str
    description: str
    price: int


# fmt: off
PIECES: tuple[Piece, ...] = (
    Piece("rueda_pieza_algodon", "🍥", "Algodón de azúcar a medio comer",
          "Pieza común de la colección de la Feria. Se pega a todo, incluida la mochila.",
          "Común", 200, 20),
    Piece("rueda_pieza_pato", "🦆", "Pato de la caseta de tiro",
          "Pieza común de la colección de la Feria. Tiene tres agujeros y ninguno es de "
          "la escopeta de quien lo ganó.", "Común", 250, 18),
    Piece("rueda_pieza_boleto", "🎟️", "Boleto de la tómbola",
          "Pieza común de la colección de la Feria. Pone «Siga participando».",
          "Común", 300, 16),
    Piece("rueda_pieza_peluche", "🧸", "Peluche gigante de la caseta",
          "Pieza rara de la colección de la Feria. No cabe en el coche ni en el portal.",
          "Rara", 800, 14),
    Piece("rueda_pieza_saltamontes", "🎢", "Ficha del Saltamontes",
          "Pieza rara de la colección de la Feria. Vale para un viaje y un mareo.",
          "Rara", 1_000, 12),
    Piece("rueda_pieza_bombilla", "💡", "Bombilla de la portada",
          "Pieza épica de la colección de la Feria. Una de las veinte mil de la portada; "
          "esta es la que siempre se funde.", "Épica", 3_000, 10),
    Piece("rueda_pieza_caballito", "🎠", "Caballito del tiovivo",
          "Pieza legendaria de la colección de la Feria. El que todos querían y nadie "
          "pillaba a la primera.", "Legendaria", 10_000, 6),
    Piece("rueda_pieza_llave", "🗝️", "Llave de la noria",
          "Pieza mítica de la colección de la Feria. Arranca la noria y la para arriba "
          "del todo, que es lo bueno.", "Mítica", 40_000, 4),
)

#: Unidades de cada título del expositor en todo el servidor.
TITLE_STOCK = 10

TITLES: tuple[Title, ...] = (
    Title("rueda_titulo_eje", "🎡", "Eje de la Rueda",
          "Título numerado del expositor de la Rueda. Todo gira a tu alrededor, y ahora "
          "consta.", 50_000),
    Title("rueda_titulo_giro", "🌀", "Giro de 360°",
          "Título numerado del expositor de la Rueda. Diste la vuelta completa y volviste "
          "con premio.", 50_000),
    Title("rueda_titulo_feria", "🎪", "Alcaldía de la Feria",
          "Título numerado del expositor de la Rueda. Con bastón de mando y pase para "
          "todas las casetas.", 50_000),
    Title("rueda_titulo_trebol", "🍀", "Trébol de cuatro hojas",
          "Título numerado del expositor de la Rueda. La suerte, enmarcada.", 50_000),
    Title("rueda_titulo_pedestal", "🏆", "Pedestal propio",
          "Título numerado del expositor de la Rueda. Lo que estaba en el expositor ahora "
          "eres tú.", 50_000),
    Title("rueda_titulo_tombola", "🎊", "Tómbola de honor",
          "Título numerado del expositor de la Rueda. Te tocó lo que no le toca a nadie.",
          50_000),
)

TREAT = Treat("rueda_piruleta", "🍭", "Piruleta de feria",
              "Se come. Premio de la Rueda: una piruleta gigante de las de la caseta de las "
              "chuches. A las mascotas les encanta.", 150)
# fmt: on

PIECE_BY_KEY: dict[str, Piece] = {p.key: p for p in PIECES}
TITLE_BY_KEY: dict[str, Title] = {t.key: t for t in TITLES}
TITLE_KEYS: tuple[str, ...] = tuple(t.key for t in TITLES)
#: Suma de los pesos de las piezas.
PIECE_WEIGHT = sum(p.weight for p in PIECES)


def piece_value() -> float:
    """Lo que vale de media una pieza al salir, a precio de catálogo."""
    return sum(p.price * p.weight for p in PIECES) / PIECE_WEIGHT


def pick_piece(roll: int) -> Piece:
    """La pieza que sale con `roll` en `[0, PIECE_WEIGHT)`."""
    for piece in PIECES:
        if roll < piece.weight:
            return piece
        roll -= piece.weight
    return PIECES[-1]
