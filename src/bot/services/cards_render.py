"""Dibujo de la mesa de blackjack: cartas de la banca y del jugador en un PNG.

Cada acción del blackjack cambia las cartas, así que la imagen se compone
en cada paso. Para que sea rápido (~10 ms) las cartas se dibujan una sola
vez y se guardan (52 caras y un dorso, caché acotada); cada imagen solo
pega esos recortes sobre el tapete y escribe los totales. El PNG se reduce
a una paleta corta y pesa ~20-30 KB.

Los palos se dibujan con formas geométricas, sin depender de que la fuente
tenga los símbolos ♠♥♦♣. Rojo y negro van sobre carta blanca y cada palo
tiene una forma distinta, así que no dependen solo del color.
"""

from __future__ import annotations

import io
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.services.blackjack import Card

FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts" / "MontserratBold.ttf"
)

WIDTH = 560
HEIGHT = 330
CARD_W = 76
CARD_H = 106
SUPERSAMPLE = 3
#: Separación horizontal entre cartas de una misma mano; se reduce si no caben.
CARD_STEP = 44

FELT = (21, 83, 64)
FELT_EDGE = (14, 58, 45)
CARD_FACE = (250, 250, 247)
CARD_BORDER = (190, 190, 185)
RED = (200, 35, 45)
BLACK = (25, 25, 28)
BACK = (40, 70, 160)
BACK_PATTERN = (70, 105, 200)
GOLD = (255, 214, 92)
TEXT = (235, 240, 236)
MUTED = (160, 200, 185)


@dataclass(frozen=True, slots=True)
class HandView:
    """Lo que se dibuja de una mano del jugador.

    Attributes:
        cards: Cartas de la mano.
        title: Rótulo encima, p. ej. `TÚ · 18` o `MANO 2 · 21`.
        active: Si es la mano que se está jugando (se resalta).
        badge: Texto del resultado, p. ej. `+200`, o `None` si aún se juega.
        badge_good: Si el resultado es bueno (dorado) o malo (gris).
    """

    cards: Sequence[Card]
    title: str
    active: bool = False
    badge: str | None = None
    badge_good: bool = False


class CardRenderer:
    """Compone imágenes de la mesa; seguro para usar desde varios hilos."""

    def __init__(self, font_path: Path = FONT_PATH) -> None:
        self._font_path = font_path
        self._sprites: dict[tuple[int, int] | None, Image.Image] = {}
        self._lock = threading.Lock()
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}

    # -- API ------------------------------------------------------------------------

    def render(
        self,
        *,
        dealer: Sequence[Card],
        hide_hole: bool,
        dealer_title: str,
        hands: Sequence[HandView],
    ) -> bytes:
        """PNG de la mesa completa.

        Args:
            dealer: Cartas de la banca.
            hide_hole: Si la segunda carta de la banca va boca abajo.
            dealer_title: Rótulo de la banca, p. ej. `BANCA · 10`.
            hands: Manos del jugador (una, o dos tras separar).
        """
        image = Image.new("RGB", (WIDTH, HEIGHT), FELT)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=FELT_EDGE, width=6)
        draw.line((24, HEIGHT // 2, WIDTH - 24, HEIGHT // 2), fill=FELT_EDGE, width=2)

        draw.text((24, 14), dealer_title, font=self._font(18), fill=MUTED)
        dealer_cards: list[Card | None] = list(dealer)
        if hide_hole and len(dealer_cards) > 1:
            dealer_cards[1] = None
        self._paste_hand(image, dealer_cards, x=24, y=42, max_width=WIDTH - 48)

        area = (WIDTH - 48) // max(1, len(hands))
        for index, hand in enumerate(hands):
            x = 24 + index * area
            title_color = GOLD if hand.active else MUTED
            draw.text((x, HEIGHT // 2 + 12), hand.title, font=self._font(18), fill=title_color)
            width = self._paste_hand(
                image, list(hand.cards), x=x, y=HEIGHT // 2 + 40, max_width=area - 16
            )
            if hand.active:
                draw.rounded_rectangle(
                    (x - 6, HEIGHT // 2 + 34, x + width + 6, HEIGHT // 2 + 46 + CARD_H),
                    radius=10,
                    outline=GOLD,
                    width=3,
                )
            if hand.badge:
                self._badge(draw, hand.badge, hand.badge_good, x + width + 14, HEIGHT // 2 + 70)

        buffer = io.BytesIO()
        image.quantize(colors=64, method=Image.Quantize.MEDIANCUT).save(
            buffer, format="PNG", optimize=True
        )
        return buffer.getvalue()

    # -- Composición ----------------------------------------------------------------

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        font = self._fonts.get(size)
        if font is None:
            font = ImageFont.truetype(str(self._font_path), size)
            self._fonts[size] = font
        return font

    def _paste_hand(
        self, image: Image.Image, cards: Sequence[Card | None], *, x: int, y: int, max_width: int
    ) -> int:
        """Pega las cartas solapadas y devuelve el ancho ocupado."""
        if not cards:
            return 0
        step = CARD_STEP
        if len(cards) > 1:
            step = min(CARD_STEP, (max_width - CARD_W) // (len(cards) - 1))
        for index, card in enumerate(cards):
            sprite = self._sprite(None if card is None else (card.rank, card.suit))
            image.paste(sprite, (x + index * step, y), sprite)
        return CARD_W + step * (len(cards) - 1)

    def _badge(self, draw: ImageDraw.ImageDraw, text: str, good: bool, x: int, y: int) -> None:
        font = self._font(20)
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        fill = GOLD if good else (95, 100, 108)
        color = BLACK if good else TEXT
        draw.rounded_rectangle((left - 10, top - 7, right + 10, bottom + 7), radius=12, fill=fill)
        draw.text((x, y), text, font=font, fill=color)

    # -- Cartas ---------------------------------------------------------------------

    def card_sprite(self, card: Card | None) -> Image.Image:
        """Imagen RGBA de una carta (o del dorso si `card` es `None`), a 76×106.

        La comparten otros dibujos de cartas (el Autobús). Es la de la caché:
        quien la use debe copiarla antes de modificarla.
        """
        return self._sprite(None if card is None else (card.rank, card.suit))

    def _sprite(self, key: tuple[int, int] | None) -> Image.Image:
        """Carta (o dorso si `key` es `None`) a tamaño final, con transparencia."""
        sprite = self._sprites.get(key)
        if sprite is not None:
            return sprite
        with self._lock:
            sprite = self._sprites.get(key)
            if sprite is None:
                sprite = self._draw_card(key)
                self._sprites[key] = sprite
            return sprite

    def _draw_card(self, key: tuple[int, int] | None) -> Image.Image:
        s = SUPERSAMPLE
        w, h = CARD_W * s, CARD_H * s
        card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        radius = 8 * s
        if key is None:
            draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=CARD_FACE)
            inset = 5 * s
            draw.rounded_rectangle(
                (inset, inset, w - inset, h - inset), radius=radius - 3 * s, fill=BACK
            )
            # Rombos del dorso: dan textura sin pesar en la paleta.
            for row in range(0, h, 12 * s):
                for col in range(0, w, 12 * s):
                    cx, cy = col + 6 * s, row + 6 * s
                    if inset * 2 < cx < w - inset * 2 and inset * 2 < cy < h - inset * 2:
                        d = 3 * s
                        draw.polygon(
                            [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)],
                            fill=BACK_PATTERN,
                        )
        else:
            rank, suit = key
            face = Card(rank, suit)
            color = RED if face.is_red else BLACK
            draw.rounded_rectangle(
                (0, 0, w - 1, h - 1), radius=radius, fill=CARD_FACE, outline=CARD_BORDER, width=s
            )
            font = ImageFont.truetype(str(self._font_path), 22 * s)
            draw.text((7 * s, 4 * s), face.label, font=font, fill=color)
            self._draw_suit(draw, suit, 7 * s + 2 * s, 32 * s, 14 * s, color)
            # Palo grande abajo a la derecha, dentro de la carta: es lo que se ve
            # de la última carta; empieza pasado `CARD_STEP` para que no asome
            # por debajo de la carta siguiente cuando se solapan.
            self._draw_suit(draw, suit, w - 31 * s, h - 35 * s, 26 * s, color)
        return card.resize((CARD_W, CARD_H), Image.Resampling.LANCZOS)

    @staticmethod
    def _draw_suit(
        draw: ImageDraw.ImageDraw, suit: int, x: float, y: float, size: float, color: tuple
    ) -> None:
        """Dibuja un palo en el cuadrado de lado `size` con esquina en (x, y)."""
        r = size / 4
        cx = x + size / 2
        if suit == 2:  # diamantes
            draw.polygon(
                [
                    (cx, y),
                    (x + size * 0.85, y + size / 2),
                    (cx, y + size),
                    (x + size * 0.15, y + size / 2),
                ],
                fill=color,
            )
        elif suit == 1:  # corazones
            draw.ellipse((x, y, x + 2 * r, y + 2 * r), fill=color)
            draw.ellipse((x + 2 * r, y, x + 4 * r, y + 2 * r), fill=color)
            draw.polygon(
                [(x + r * 0.15, y + r * 1.35), (x + size - r * 0.15, y + r * 1.35), (cx, y + size)],
                fill=color,
            )
        elif suit == 0:  # picas: corazón invertido con tallo
            draw.polygon(
                [(cx, y), (x + size - r * 0.15, y + size * 0.62), (x + r * 0.15, y + size * 0.62)],
                fill=color,
            )
            draw.ellipse((x, y + size * 0.35, x + 2 * r, y + size * 0.35 + 2 * r), fill=color)
            draw.ellipse(
                (x + 2 * r, y + size * 0.35, x + 4 * r, y + size * 0.35 + 2 * r), fill=color
            )
            draw.polygon(
                [(cx, y + size * 0.6), (cx + r * 0.7, y + size), (cx - r * 0.7, y + size)],
                fill=color,
            )
        else:  # tréboles
            draw.ellipse((cx - r, y, cx + r, y + 2 * r), fill=color)
            draw.ellipse((x, y + r * 1.4, x + 2 * r, y + r * 3.4), fill=color)
            draw.ellipse((x + 2 * r, y + r * 1.4, x + 4 * r, y + r * 3.4), fill=color)
            draw.polygon(
                [(cx, y + size * 0.5), (cx + r * 0.7, y + size), (cx - r * 0.7, y + size)],
                fill=color,
            )
