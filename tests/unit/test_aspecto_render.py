"""Pruebas del dibujo de avatares y banners del aspecto."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from bot.services import aspecto_render as render
from bot.services.aspecto import CANARIAS, CASINO
from bot.services.image_input import ImageTooLargeError, InvalidImageError


def _png(size: tuple[int, int], mode: str = "RGB", color: object = (200, 40, 40)) -> bytes:
    image = Image.new(mode, size, color)  # type: ignore[arg-type]
    # Un detalle para que el recorte no sea invisible.
    image.paste(Image.new(mode, (size[0] // 4, size[1] // 4), (0, 0, 255, 255)[: len(mode)]))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


@pytest.mark.parametrize("size", [(800, 300), (300, 900), (512, 512), (64, 40)])
def test_el_avatar_sale_cuadrado_del_tamano_exacto(size: tuple[int, int]) -> None:
    result = render.prepare_avatar(_png(size))
    assert _size(result) == (render.AVATAR_SIDE, render.AVATAR_SIDE)


def test_el_avatar_con_transparencia_la_conserva() -> None:
    data = _png((400, 200), "RGBA", (10, 200, 10, 0))
    with Image.open(io.BytesIO(render.prepare_avatar(data))) as image:
        assert image.size == (render.AVATAR_SIDE, render.AVATAR_SIDE)
        assert (
            image.convert("RGBA").getpixel((render.AVATAR_SIDE - 1, render.AVATAR_SIDE - 1))[3] == 0
        )


@pytest.mark.parametrize(
    ("size", "mode"),
    [((3000, 600), "RGB"), ((400, 1200), "RGB"), ((1200, 480), "RGB"), ((500, 500), "RGBA")],
)
def test_el_banner_sale_del_tamano_exacto(size: tuple[int, int], mode: str) -> None:
    result = render.prepare_banner(_png(size, mode, (10, 200, 10, 90)[: len(mode)]))
    assert _size(result) == render.BANNER_SIZE


def test_el_banner_con_transparencia_sale_sin_canal_alfa() -> None:
    result = render.prepare_banner(_png((500, 500), "RGBA", (10, 200, 10, 0)))
    with Image.open(io.BytesIO(result)) as image:
        assert image.mode == "RGB"


@pytest.mark.parametrize("function", [render.prepare_avatar, render.prepare_banner])
def test_datos_que_no_son_imagen_lanzan_error(function) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(InvalidImageError):
        function(b"esto no es una imagen, ni de lejos")


def test_el_banner_de_degradado_y_el_de_imagen_tienen_el_tamano_exacto() -> None:
    base = render.prepare_banner(_png((900, 900)))
    for background in (None, base):
        data = render.draw_banner(CASINO, background, "Casino", ["Uno", "Dos", "Tres"], "9.9.9")
        assert _size(data) == render.BANNER_SIZE


def test_el_banner_es_determinista() -> None:
    base = render.prepare_banner(_png((900, 900)))
    for background in (None, base):
        first = render.draw_banner(CANARIAS, background, "Canarias", ["Ñu 1", "2"], "1.2.3")
        second = render.draw_banner(CANARIAS, background, "Canarias", ["Ñu 1", "2"], "1.2.3")
        assert first == second


def test_el_degradado_va_del_color_de_arriba_al_de_abajo() -> None:
    with Image.open(io.BytesIO(render.draw_banner(CASINO, None, "", [], ""))) as image:
        assert image.getpixel((0, 0)) == CASINO.top
        assert image.getpixel((0, render.BANNER_SIZE[1] - 1)) == CASINO.bottom


def test_el_velo_oscurece_la_imagen_de_fondo() -> None:
    base = render.prepare_banner(_png((900, 900), "RGB", (250, 250, 250)))
    with Image.open(io.BytesIO(render.draw_banner(CASINO, base, "", [], ""))) as image:
        assert max(image.getpixel((600, 400))) < 250


def test_textos_larguisimos_y_con_emojis_no_rompen_el_banner() -> None:
    long = "Palabra " * 80
    data = render.draw_banner(CASINO, None, long, [long, "🎰" * 50, "x" * 500, "cuarta"], "1.0.0")
    assert _size(data) == render.BANNER_SIZE


def test_los_emojis_se_quitan_y_los_espacios_se_limpian() -> None:
    assert render.printable("🎰  Casino 🎲 Jovani 🇪🇸") == "Casino Jovani"
    assert render.printable("Canarias 🌋") == "Canarias"
    assert render.printable("Ñandú, camión y ¿qué?") == "Ñandú, camión y ¿qué?"
    assert render.printable("🎰🎲") == ""


def test_un_emoji_no_cambia_lo_que_se_pinta() -> None:
    plain = render.draw_banner(CASINO, None, "Casino", ["Ganas"], "1.0")
    emoji = render.draw_banner(CASINO, None, "🎰 Casino 🎰", ["Ganas 🎲"], "1.0")
    assert plain == emoji


def test_el_texto_no_pinta_nada_en_el_38_por_ciento_izquierdo() -> None:
    width = render.BANNER_SIZE[0]
    strip = (0, 0, int(width * 0.38), render.BANNER_SIZE[1])
    base = render.prepare_banner(_png((900, 900), "RGB", (90, 120, 200)))
    texts = [
        ("Casino", ["Una línea", "Otra más", "La tercera"], "12.34.56"),
        ("Título larguísimo " * 20, ["Línea larguísima " * 30] * 3, "1.0.0"),
    ]
    for background in (None, base):
        empty = render.draw_banner(CASINO, background, "", [], "")
        for title, lines, version in texts:
            full = render.draw_banner(CASINO, background, title, lines, version)
            with Image.open(io.BytesIO(empty)) as a, Image.open(io.BytesIO(full)) as b:
                assert ImageChops.difference(a.crop(strip), b.crop(strip)).getbbox() is None
                assert ImageChops.difference(a, b).getbbox() is not None


def test_el_texto_se_alinea_a_la_derecha_y_deja_margen() -> None:
    width = render.BANNER_SIZE[0]
    empty = render.draw_banner(CASINO, None, "", [], "")
    full = render.draw_banner(CASINO, None, "Casino", ["Datos"], "1.0")
    with Image.open(io.BytesIO(empty)) as a, Image.open(io.BytesIO(full)) as b:
        box = ImageChops.difference(a, b).getbbox()
    assert box is not None
    assert box[2] <= width - render.MARGIN + 6
    assert box[0] >= int(width * 0.38)


def test_las_lineas_de_datos_comparten_tamano_aunque_una_sea_corta() -> None:
    short = render.shared_size(["Corta"], 38, 704)
    both = render.shared_size(
        ["Corta", "Una línea bastante más larga que no cabe en el banner"], 38, 704
    )
    assert short == 38
    assert both < short


def test_sin_lineas_el_tamano_no_cambia() -> None:
    assert render.shared_size([], 38, 704) == 38


def _gif(size: tuple[int, int], frames: int = 4, durations: list[int] | None = None) -> bytes:
    images = [Image.new("RGB", size, (40 * index, 200 - 40 * index, 90)) for index in range(frames)]
    for index, image in enumerate(images):
        image.paste((255, 255, 255), (index * 3, 0, index * 3 + size[0] // 4, size[1] // 4))
    buffer = io.BytesIO()
    images[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=durations or [60 + 10 * index for index in range(frames)],
        loop=0,
    )
    return buffer.getvalue()


def _gif_info(data: bytes) -> tuple[str, tuple[int, int], list[int], list[bytes]]:
    durations, pixels = [], []
    with Image.open(io.BytesIO(data)) as image:
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(image.info["duration"])
            pixels.append(image.convert("RGB").tobytes())
        return str(image.format), image.size, durations, pixels


def test_un_gif_animado_cuadrado_devuelve_los_mismos_bytes() -> None:
    data = _gif((64, 64))
    assert render.prepare_avatar(data) == data


@pytest.mark.parametrize("size", [(120, 60), (60, 120)])
def test_un_gif_animado_no_cuadrado_sale_como_gif_cuadrado_animado(
    size: tuple[int, int],
) -> None:
    durations = [50, 70, 90, 110]
    result = render.prepare_avatar(_gif(size, 4, durations))
    format_, out_size, out_durations, pixels = _gif_info(result)
    assert format_ == "GIF"
    assert out_size == (render.AVATAR_GIF_SIDE, render.AVATAR_GIF_SIDE)
    assert out_durations == durations
    assert len(pixels) == 4
    assert len(set(pixels)) > 1


def test_un_gif_de_un_fotograma_sale_como_png() -> None:
    result = render.prepare_avatar(_gif((120, 60), frames=1))
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "PNG"
        assert image.size == (render.AVATAR_SIDE, render.AVATAR_SIDE)


def test_un_gif_con_demasiados_fotogramas_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "MAX_AVATAR_FRAMES", 3)
    with pytest.raises(ImageTooLargeError, match="demasiados fotogramas"):
        render.prepare_avatar(_gif((120, 60), frames=4))
    assert render.prepare_avatar(_gif((64, 64), frames=3))


def test_un_gif_truncado_es_una_imagen_invalida() -> None:
    data = _gif((120, 60), frames=6)
    with pytest.raises(InvalidImageError):
        render.prepare_avatar(data[: len(data) * 2 // 3])


def test_png_y_jpeg_siguen_dando_png_de_avatar() -> None:
    jpeg = io.BytesIO()
    Image.new("RGB", (300, 200), (10, 20, 30)).save(jpeg, format="JPEG")
    for data in (_png((300, 200)), jpeg.getvalue()):
        with Image.open(io.BytesIO(render.prepare_avatar(data))) as image:
            assert image.format == "PNG"
            assert image.size == (render.AVATAR_SIDE, render.AVATAR_SIDE)


def test_el_banner_de_un_gif_animado_es_un_png() -> None:
    with Image.open(io.BytesIO(render.prepare_banner(_gif((120, 60))))) as image:
        assert image.format == "PNG"
        assert image.size == render.BANNER_SIZE


def test_un_gif_enorme_que_no_es_cuadrado_se_rechaza_antes_de_recodificarlo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(render, "MAX_GIF_PIXELS", 10)
    monkeypatch.setattr(render, "_square_gif", None)
    with pytest.raises(ImageTooLargeError, match="demasiado grande"):
        render.prepare_avatar(_gif((64, 32)))


def test_el_tope_de_pixeles_no_afecta_a_un_gif_cuadrado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(render, "MAX_GIF_PIXELS", 10)
    data = _gif((64, 64))
    assert render.prepare_avatar(data) == data
