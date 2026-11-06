"""Dibujo de reserva de `pisos` con Pillow: una tarjeta sencilla con el texto de la pantalla.

Si no hay Node ni Chromium, `bot.services.housing_scene.HousingScene` pinta esto: la
primera línea de `state["fallback"]` de título y el resto debajo, ajustadas a la
tarjeta. No lleva mapa ni fachadas; solo hace que el panel siga diciendo lo importante.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 640
#: Pantallas que miden más de `H` (el barrio y las plantas, con sus tarjetas en dos filas, las
#: calles, el solar, dividir y el edificio en corte, de hasta diez plantas); la escena
#: (`SCREEN_H` de `assets/pisos/escena.html`) y el dibujo de reserva tienen que coincidir.
SCREEN_H = {
    "barrio": 760,
    "plantas": 760,
    "dividir": 700,
    "calles": 760,
    "solar": 760,
    "edificio": 1000,
}

FONTS = Path(__file__).resolve().parent.parent / "assets" / "memes" / "fonts"
TITLE_FONT = FONTS / "MontserratBold.ttf"
TEXT_FONT = FONTS / "robotomedium.ttf"
BACKGROUND, CARD, INK, MUTED, ACCENT = "#f3f5f8", "#ffffff", "#16202b", "#5a6877", "#ea5a0c"
MARGIN = 48


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int
) -> list[str]:
    """Parte `text` en líneas de como mucho `width` píxeles (una palabra larga se corta)."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        while draw.textlength(word, font=font) > width and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and draw.textlength(word[:cut], font=font) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    return [*lines, line] if line else lines


class HousingRenderer:
    """Pinta cualquier pantalla de `pisos` como una tarjeta de texto."""

    def paint(self, state: dict[str, Any]) -> bytes:
        """PNG de `W` x `SCREEN_H[pantalla]` (o `H`) con las líneas de `state["fallback"]`."""
        lines = [str(line) for line in state.get("fallback") or ["Pisos de Jovani"]]
        h = SCREEN_H.get(state.get("screen", ""), H)
        image = Image.new("RGB", (W, h), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((24, 24, W - 24, h - 24), radius=14, fill=CARD)
        draw.rectangle((24, 24, 38, h - 24), fill=ACCENT)
        title_font = ImageFont.truetype(str(TITLE_FONT), 40)
        body_font = ImageFont.truetype(str(TEXT_FONT), 24)
        width = W - 2 * MARGIN - 24
        limit = h - MARGIN - 30
        y = MARGIN
        for text in _wrap(draw, lines[0], title_font, width)[:2]:
            draw.text((MARGIN + 14, y), text, font=title_font, fill=INK)
            y += 52
        y += 12
        for body in lines[1:]:
            for text in _wrap(draw, body, body_font, width):
                if y > limit:
                    break
                draw.text((MARGIN + 14, y), text, font=body_font, fill=MUTED)
                y += 34
            y += 8
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=6)
        return buffer.getvalue()
