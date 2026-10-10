"""Pruebas del comando `reinicio` y del aviso de novedades: el buzón
(bot.services.deploy) y el cog. Lo que se pide a GitHub está en
`test_changelog.py`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs.deploy import (
    CHARS_PER_MESSAGE,
    DEPLOYERS,
    DETAIL_CHARS,
    EMBEDS_PER_MESSAGE,
    NEWS_LIMIT,
    Deploy,
    OldNewsButton,
    news_messages,
    result_message,
)
from bot.repositories.news import NewsSettings
from bot.services.changelog import NewsEntry, NewsItem, PullRequest
from bot.services.deploy import (
    NEWS_FILE,
    REQUEST_FILE,
    RESULT_FILE,
    RUNNING_FILE,
    STALE_AFTER,
    DeployRequest,
    DeployResult,
    Mailbox,
)
from bot.utils.responder import CommandResponder

NOW = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)
YEYO = 403646452414545921
DANI = 498473711687434241


class FakeResponder(CommandResponder):
    """Responder que registra cada envío, sin tocar Discord."""

    def __init__(self, user_id: int | None, channel_id: int = 555) -> None:
        self.guild = SimpleNamespace(id=1)
        self.member = SimpleNamespace(id=user_id) if user_id is not None else None
        self.channel = SimpleNamespace(id=channel_id)
        self.sent: list[str] = []
        self.errors: list[str] = []

    async def send(self, content=None, **kwargs) -> None:  # noqa: ANN001, ANN003
        self.sent.append(content)

    async def send_error(self, content: str) -> None:
        self.errors.append(content)

    async def start_progress(self, placeholder: str = "", **kwargs) -> None:  # noqa: ANN003
        raise AssertionError("reinicio no usa progreso")

    async def update_progress(self, content: str) -> None:
        raise AssertionError("reinicio no usa progreso")

    async def finish(self, content=None, **kwargs) -> None:  # noqa: ANN001, ANN003
        raise AssertionError("reinicio no usa progreso")


def make_cog(tmp_path: Path, bot: object | None = None, fetch: object | None = None) -> Deploy:
    return Deploy(
        bot or MagicMock(),
        Mailbox(tmp_path / "buzon"),
        clock=lambda: NOW,
        fetch=fetch or AsyncMock(return_value=None),
    )


# -- Buzón -------------------------------------------------------------------------------


def test_los_que_pueden_reiniciar_son_yeyo_y_dani() -> None:
    assert DEPLOYERS == {YEYO, DANI}


def test_la_peticion_queda_escrita_para_el_nas(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path / "buzon")
    mailbox.request(DeployRequest(555, YEYO, NOW))

    written = DeployRequest.from_json((tmp_path / "buzon" / REQUEST_FILE).read_text())
    assert written == DeployRequest(555, YEYO, NOW)
    assert mailbox.pending(NOW) == written
    assert not (tmp_path / "buzon" / f"{REQUEST_FILE}.tmp").exists()


def test_la_peticion_en_curso_tambien_cuenta_como_pendiente(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RUNNING_FILE).write_text(DeployRequest(555, DANI, NOW).to_json())

    assert Mailbox(buzon).pending(NOW + timedelta(minutes=5)).user_id == DANI


def test_una_peticion_caducada_se_descarta(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path / "buzon")
    mailbox.request(DeployRequest(555, YEYO, NOW))

    assert mailbox.pending(NOW + STALE_AFTER + timedelta(seconds=1)) is None
    assert not (tmp_path / "buzon" / REQUEST_FILE).exists()


def test_una_nota_estropeada_no_bloquea(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / REQUEST_FILE).write_text("{esto no es json")

    assert Mailbox(buzon).pending(NOW) is None


def test_recoger_el_resultado_vacia_el_buzon(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RUNNING_FILE).write_text(DeployRequest(555, YEYO, NOW).to_json())
    (buzon / RESULT_FILE).write_text("ok\nDesplegado abc1234: Logros nuevos\n")
    mailbox = Mailbox(buzon)

    result = mailbox.take_result()

    expected = DeployResult(
        True, "Desplegado abc1234: Logros nuevos", DeployRequest(555, YEYO, NOW)
    )
    assert result == expected
    assert not any(buzon.iterdir())
    assert mailbox.take_result() is None


def test_un_resultado_de_error(tmp_path: Path) -> None:
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RESULT_FILE).write_text("error\nLa imagen no se ha podido construir.\n")

    result = Mailbox(buzon).take_result()

    assert result is not None and not result.ok
    assert result.request is None
    assert result.summary == "La imagen no se ha podido construir."


# -- Cog -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solo_yeyo_y_dani_pueden_pedirlo(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder(user_id=12345)

    await cog.request_impl(responder)

    assert responder.errors and not responder.sent
    assert not (tmp_path / "buzon").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [YEYO, DANI])
async def test_pedirlo_deja_la_nota_y_confirma(tmp_path: Path, user_id: int) -> None:
    cog = make_cog(tmp_path)
    responder = FakeResponder(user_id=user_id)

    await cog.request_impl(responder)

    assert not responder.errors
    assert "Pedido" in responder.sent[0]
    assert Mailbox(tmp_path / "buzon").pending(NOW) == DeployRequest(555, user_id, NOW)


@pytest.mark.asyncio
async def test_no_se_pide_dos_veces(tmp_path: Path) -> None:
    cog = make_cog(tmp_path)
    await cog.request_impl(FakeResponder(user_id=YEYO))
    second = FakeResponder(user_id=DANI)

    await cog.request_impl(second)

    assert "Ya hay un reinicio pedido" in second.errors[0]
    assert Mailbox(tmp_path / "buzon").pending(NOW).user_id == YEYO


@pytest.mark.asyncio
async def test_si_no_puede_escribir_lo_explica(tmp_path: Path) -> None:
    (tmp_path / "buzon").write_text("un archivo donde debería haber una carpeta")
    cog = make_cog(tmp_path)
    responder = FakeResponder(user_id=YEYO)

    await cog.request_impl(responder)

    assert "buzón" in responder.errors[0]


@pytest.mark.asyncio
async def test_publica_el_resultado_en_el_canal_de_la_peticion(tmp_path: Path) -> None:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    cog = make_cog(tmp_path, bot)
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / RUNNING_FILE).write_text(DeployRequest(555, YEYO, NOW).to_json())
    (buzon / RESULT_FILE).write_text("ok\nDesplegado abc1234: Pollo más rápido\n")

    assert await cog.announce() is True

    bot.get_channel.assert_called_once_with(555)
    text = channel.send.await_args.args[0]
    assert f"<@{YEYO}>" in text and "abc1234" in text
    assert await cog.announce() is False


def test_mensaje_de_error_dice_que_sigue_la_version_anterior() -> None:
    text = result_message(DeployResult(False, "Hay archivos tocados a mano.", None))

    assert text.startswith("❌") and "versión de antes" in text


# -- Novedades ---------------------------------------------------------------------------


def test_recoger_las_novedades_las_borra(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path)
    (tmp_path / NEWS_FILE).write_text("Pollo más rápido\n\n  Caballos  \n")

    assert mailbox.take_news() == ["Pollo más rápido", "Caballos"]
    assert not (tmp_path / NEWS_FILE).exists()
    assert mailbox.take_news() is None


def test_un_archivo_de_novedades_vacio_no_se_publica(tmp_path: Path) -> None:
    (tmp_path / NEWS_FILE).write_text("\n")

    assert Mailbox(tmp_path).take_news() is None


def _pull(number: int, body: str = "Descripción del cambio.") -> PullRequest:
    return PullRequest(
        repo="godzilin/Proyecto_BOT_JovaniVazquez",
        number=number,
        url=f"https://github.com/godzilin/Proyecto_BOT_JovaniVazquez/pull/{number}",
        body=body,
        author="Yeyo-Yeyex",
        author_url="https://github.com/Yeyo-Yeyex",
        author_avatar=None,
        merged_at=NOW,
    )


def _entry(number: int, body: str = "Descripción del cambio.") -> NewsEntry:
    return NewsEntry(NewsItem(f"Cambio {number}", number, "yeyo/rama"), _pull(number, body))


def test_el_resumen_lista_cada_cambio_con_su_pr_y_su_autor() -> None:
    entries = [_entry(5), NewsEntry(NewsItem("Sin PR conocido"))]

    (embeds,) = news_messages(entries, detailed=False)

    assert len(embeds) == 1
    description = embeds[0].description or ""
    assert "• Cambio 5 · [#5](https://github.com/godzilin/Proyecto_BOT_JovaniVazquez/pull/5)" in (
        description
    )
    assert "Yeyo-Yeyex" in description
    assert "• Sin PR conocido" in description


def test_el_detallado_añade_una_ficha_por_pr_con_su_descripcion() -> None:
    entries = [_entry(5, "**Qué cambia.** Todo."), NewsEntry(NewsItem("Sin PR conocido"))]

    (embeds,) = news_messages(entries, detailed=True)

    assert len(embeds) == 2, "el cambio sin PR solo sale en la lista"
    ficha = embeds[1]
    assert ficha.title == "Cambio 5"
    assert ficha.url == _pull(5).url
    assert ficha.description == "**Qué cambia.** Todo."
    assert ficha.author.name == "Yeyo-Yeyex"


def test_el_aviso_es_serio() -> None:
    embeds = news_messages([_entry(5)], detailed=True)[0]
    texto = " ".join(f"{e.title} {e.description} {e.footer.text or ''}".lower() for e in embeds)

    for broma in ("leído", "hacienda", "consejo de ministros", "boe"):
        assert broma not in texto


def test_una_descripcion_larga_se_corta_y_enlaza_a_github() -> None:
    body = "\n".join(f"Línea {n} " + "x" * 80 for n in range(200))

    ficha = news_messages([_entry(5, body)], detailed=True)[0][1]

    assert len(ficha.description or "") <= DETAIL_CHARS
    assert (ficha.description or "").endswith(f"[Sigue en GitHub]({_pull(5).url})")


def test_muchos_cambios_se_reparten_en_mensajes_que_caben_en_discord() -> None:
    body = "x" * DETAIL_CHARS
    entries = [_entry(n, body) for n in range(NEWS_LIMIT + 5)]

    messages = news_messages(entries, detailed=True)

    assert sum(len(embeds) for embeds in messages) == 1 + NEWS_LIMIT
    for embeds in messages:
        assert len(embeds) <= EMBEDS_PER_MESSAGE
        assert sum(len(e) for e in embeds) <= CHARS_PER_MESSAGE
        assert all(len(e.description or "") <= 4096 for e in embeds)
    assert (messages[0][0].description or "").endswith("…y 5 más.")


def _guild_with_channel(guild_id: int = 1) -> tuple[MagicMock, MagicMock]:
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.text_channels = [channel]
    return guild, channel


def _bot_with(guild: MagicMock, settings: NewsSettings | None = None) -> MagicMock:
    bot = MagicMock()
    bot.guilds = [guild]
    bot.news = None
    if settings is not None:
        bot.news = SimpleNamespace(settings=AsyncMock(return_value=settings))
    return bot


@pytest.mark.asyncio
async def test_publica_las_novedades_con_lo_que_cuenta_github(tmp_path: Path) -> None:
    guild, channel = _guild_with_channel()
    fetch = AsyncMock(
        return_value={
            "number": 5,
            "html_url": "https://github.com/godzilin/Proyecto_BOT_JovaniVazquez/pull/5",
            "body": "Los caballos corren de verdad.",
            "user": {"login": "Yeyo-Yeyex"},
            "head": {"label": "yeyo:caballos"},
            "merged_at": "2026-10-06T21:00:00Z",
        }
    )
    cog = make_cog(tmp_path, _bot_with(guild), fetch)
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / NEWS_FILE).write_text("#5\tyeyo/caballos\ttitulo\tCarreras de caballos\n")

    assert await cog.announce_news() is True

    embeds = channel.send.await_args.kwargs["embeds"]
    assert "Carreras de caballos" in (embeds[0].description or "")
    assert embeds[1].description == "Los caballos corren de verdad."
    assert "view" not in channel.send.await_args.kwargs, "el aviso ya no lleva botón"
    assert await cog.announce_news() is False


@pytest.mark.asyncio
async def test_sin_github_sale_la_lista_de_titulos(tmp_path: Path) -> None:
    guild, channel = _guild_with_channel()
    cog = make_cog(tmp_path, _bot_with(guild))
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / NEWS_FILE).write_text("Carreras de caballos\n")

    await cog.announce_news()

    (embed,) = channel.send.await_args.kwargs["embeds"]
    assert "• Carreras de caballos" in (embed.description or "")


@pytest.mark.asyncio
async def test_un_servidor_con_el_aviso_apagado_no_lo_recibe(tmp_path: Path) -> None:
    guild, channel = _guild_with_channel()
    cog = make_cog(tmp_path, _bot_with(guild, NewsSettings(enabled=False)))
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / NEWS_FILE).write_text("Carreras de caballos\n")

    assert await cog.announce_news() is True

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_aviso_va_al_canal_elegido(tmp_path: Path) -> None:
    guild, general = _guild_with_channel()
    elegido = MagicMock(spec=discord.TextChannel)
    elegido.send = AsyncMock()
    guild.get_channel_or_thread = lambda channel_id: elegido if channel_id == 77 else None
    cog = make_cog(tmp_path, _bot_with(guild, NewsSettings(channel_id=77)))
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / NEWS_FILE).write_text("Carreras de caballos\n")

    await cog.announce_news()

    general.send.assert_not_awaited()
    elegido.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_el_aviso_va_a_un_hilo_archivado(tmp_path: Path) -> None:
    """Los hilos archivados no están en la caché: el bot los pide a Discord."""
    guild, general = _guild_with_channel()
    hilo = MagicMock(spec=discord.Thread)
    hilo.send = AsyncMock()
    guild.get_channel_or_thread = lambda channel_id: None
    guild.fetch_channel = AsyncMock(return_value=hilo)
    cog = make_cog(tmp_path, _bot_with(guild, NewsSettings(channel_id=88)))
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / NEWS_FILE).write_text("Carreras de caballos\n")

    await cog.announce_news()

    guild.fetch_channel.assert_awaited_once_with(88)
    general.send.assert_not_awaited()
    hilo.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_si_el_hilo_ya_no_existe_vuelve_a_chat_general(tmp_path: Path) -> None:
    guild, general = _guild_with_channel()
    guild.get_channel_or_thread = lambda channel_id: None
    guild.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
    cog = make_cog(tmp_path, _bot_with(guild, NewsSettings(channel_id=88)))
    buzon = tmp_path / "buzon"
    buzon.mkdir()
    (buzon / NEWS_FILE).write_text("Carreras de caballos\n")

    await cog.announce_news()

    general.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_el_boton_leido_de_los_avisos_viejos_contesta_sin_fallar() -> None:
    interaction = fake_interaction(SimpleNamespace(id=YEYO, bot=False))

    await OldNewsButton(123).callback(interaction)

    assert interaction.response.is_done()
