"""Pruebas de bot.services.memes: registro, validación de entradas y render de cada efecto."""

from __future__ import annotations

import io
import shutil

import pytest
from PIL import Image

from bot.services.memes import (
    EFFECTS,
    MAX_TEXT_LENGTH,
    MemeInputError,
    build_request,
    render,
)
from bot.services.memes.toolkit import asset_path, clean_text, draw_fitted_text

# Cabecera de cada formato de salida, para comprobar que el archivo es lo que dice ser.
MAGIC = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "gif": b"GIF8",
}


def avatar_bytes(color: tuple[int, int, int] = (200, 60, 30)) -> bytes:
    """Avatar PNG de 128 px con un círculo, para que haya bordes que procesar."""
    image = Image.new("RGB", (128, 128), color)
    image.paste((250, 220, 0), (32, 32, 96, 96))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def example_texts(name: str) -> list[str]:
    effect = EFFECTS[name]
    return [part.strip() for part in effect.example.split("|")] if effect.example else []


def test_se_registran_los_108_efectos_de_dank_memer() -> None:
    """Todos los de imgen salvo `magik` (comando propio), `profile` y `yomomma`."""
    assert len(EFFECTS) == 108
    assert {"magik", "profile", "yomomma"}.isdisjoint(EFFECTS)


@pytest.mark.parametrize("name", sorted(EFFECTS))
def test_cada_efecto_esta_documentado_y_trae_un_ejemplo_valido(name: str) -> None:
    """Cada efecto tiene descripción, y su ejemplo aporta exactamente los textos que pide."""
    effect = EFFECTS[name]
    assert effect.description
    assert effect.render.__doc__ == effect.description
    texts = example_texts(name)
    assert effect.texts <= len(texts) <= effect.texts + effect.optional_texts


@pytest.mark.lenta
@pytest.mark.parametrize("name", sorted(EFFECTS))
def test_cada_efecto_genera_un_archivo_valido(name: str) -> None:
    """Se ejecuta de verdad cada efecto con su ejemplo y se valida el archivo resultante."""
    effect = EFFECTS[name]
    if effect.output == "mp4" and shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg no está instalado en este entorno")
    request = build_request(
        effect,
        [avatar_bytes(), avatar_bytes((30, 90, 200))],
        example_texts(name),
        ["Nombre", "usuario"],
    )

    result = render(effect, request)

    if result.extension == "mp4":
        assert result.data[4:8] == b"ftyp"
    else:
        assert result.data.startswith(MAGIC[result.extension])
        with Image.open(io.BytesIO(result.data)) as image:
            assert image.width > 0 and image.height > 0
    # Discord sin mejoras admite 10 MB por archivo.
    assert len(result.data) < 8 * 1024 * 1024


def test_build_request_rechaza_textos_que_faltan() -> None:
    with pytest.raises(MemeInputError, match="Faltan"):
        build_request(EFFECTS["brain"], [], ["uno", "dos"], [])


def test_build_request_rechaza_textos_de_mas() -> None:
    with pytest.raises(MemeInputError, match="Sobran"):
        build_request(EFFECTS["cry"], [], ["uno", "dos"], [])


def test_build_request_rechaza_textos_demasiado_largos() -> None:
    with pytest.raises(MemeInputError, match=str(MAX_TEXT_LENGTH)):
        build_request(EFFECTS["cry"], [], ["a" * (MAX_TEXT_LENGTH + 1)], [])


def test_un_texto_que_solo_tiene_emojis_cuenta_como_vacio() -> None:
    """Tras quitar los emojis no queda nada que dibujar: se pide texto."""
    with pytest.raises(MemeInputError, match="Faltan"):
        build_request(EFFECTS["cry"], [], ["😂🔥"], [])


def test_build_request_reduce_fotos_grandes() -> None:
    """Una foto de 3000 px se trabaja a 1024 px como máximo."""
    buffer = io.BytesIO()
    Image.new("RGB", (3000, 1500), "white").save(buffer, format="JPEG")

    request = build_request(EFFECTS["invert"], [buffer.getvalue()], [], [])

    assert max(request.avatars[0].size) == 1024
    assert request.avatars[0].mode == "RGBA"


def test_meme_admite_solo_el_texto_de_arriba() -> None:
    """El texto de abajo de `meme` es opcional."""
    effect = EFFECTS["meme"]
    result = render(effect, build_request(effect, [avatar_bytes()], ["solo arriba"], []))
    assert result.extension == "png"


def test_clean_text_convierte_emojis_de_discord_y_quita_los_unicode() -> None:
    """Las fuentes no tienen glifos de emoji: `<:pog:123>` pasa a `:pog:` y 😂 se elimina."""
    assert clean_text("hola <:pog:123> y <a:baile:456> 😂👍🏽") == "hola :pog: y :baile:"


def test_draw_fitted_text_avisa_si_el_texto_no_cabe() -> None:
    """Un texto imposible de encajar da un error comprensible, no una imagen rota."""
    with pytest.raises(MemeInputError, match="demasiado largo"):
        draw_fitted_text(
            Image.new("RGB", (40, 20)),
            "palabra " * 50,
            (0, 0, 40, 20),
            "arimobold.ttf",
            20,
            max_lines=1,
        )


def test_expanddong_tiene_todas_las_letras() -> None:
    """Cada letra de la a a la z tiene su plantilla."""
    for letter in "abcdefghijklmnopqrstuvwxyz":
        assert asset_path(f"expanddong/{letter}").is_file()
