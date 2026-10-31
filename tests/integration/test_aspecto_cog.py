"""El aspecto de Jovani con el bot cargado de verdad: perfil, `aspecto` y `jovani`.

El perfil se prueba con un doble de `guild.me.edit`: Discord no se toca en las pruebas.
Qué look toca depende de la hora, así que aquí se fija uno o se compara con `_desired`.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from PIL import Image

from bot import __version__
from bot.app import INITIAL_EXTENSIONS, BotClient
from bot.services import aspecto as rules
from bot.services.aspecto import AspectoState
from bot.services.levels import TIMEZONE

GUILD_ID = 1
OWNER_ID = 10


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.economy.repository.initialize()
    await client.casino_stats.initialize()
    await client.achievements.initialize()
    await client.shop.initialize()
    await client.pets.initialize()
    await client.picar.initialize()
    await client.telemetry_repository.initialize()
    await client.aspecto.initialize()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    return client


def fake_guild(guild_id: int = GUILD_ID) -> SimpleNamespace:
    """Servidor con un `me` cuyo `edit` apunta lo que se le pide."""
    me = SimpleNamespace(
        edit=AsyncMock(),
        display_avatar=SimpleNamespace(url="https://cdn.example/avatar.png"),
        display_banner=None,
    )
    return SimpleNamespace(
        id=guild_id,
        me=me,
        member_count=5,
        created_at=datetime(2020, 3, 3, tzinfo=UTC),
    )


def fake_responder(guild: SimpleNamespace) -> SimpleNamespace:
    member = MagicMock(spec=discord.Member)
    member.id = OWNER_ID
    member.bot = False
    return SimpleNamespace(
        guild=guild,
        member=member,
        channel=None,
        start_progress=AsyncMock(),
        finish=AsyncMock(),
        send_error=AsyncMock(),
    )


def attachment(data: bytes, content_type: str = "image/png") -> SimpleNamespace:
    return SimpleNamespace(
        content_type=content_type, size=len(data), read=AsyncMock(return_value=data)
    )


def png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def edited(guild: SimpleNamespace) -> list[str]:
    """Piezas del perfil que se han cambiado, en orden."""
    return [
        name for call in guild.me.edit.await_args_list for name in call.kwargs if name != "reason"
    ]


def http_error(status: int, *, retry_after: str | None = None) -> discord.HTTPException:
    response = MagicMock()
    response.status = status
    response.reason = "error"
    response.headers = {"Retry-After": retry_after} if retry_after else {}
    return discord.HTTPException(response, {"code": 0, "message": "no"})


@pytest.fixture
async def client(tmp_path: Path):  # noqa: ANN201
    bot = await load_bot(tmp_path)
    yield bot
    await bot.close()


async def test_el_primer_repaso_pone_banner_y_bio_sin_tocar_el_avatar_si_no_hay(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    state = await cog.sync(guild)
    assert edited(guild) == ["banner", "bio"]
    assert state.applied_key == cog._desired(guild, state, {})
    assert state.last_version == __version__
    assert state.last_error is None
    assert await client.aspecto.state(GUILD_ID) == state


async def test_un_segundo_repaso_no_vuelve_a_subir_nada(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    await cog.sync(guild)
    guild.me.edit.reset_mock()
    await cog.sync(guild)
    guild.me.edit.assert_not_awaited()


async def test_el_avatar_solo_se_sube_si_ha_cambiado(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    await cog.manage(responder, "fijar", "casino", None, "", None)
    await cog.manage(responder, "avatar", "casino", attachment(png_bytes()), "", None)
    assert "avatar" in edited(guild)
    guild.me.edit.reset_mock()
    await cog.sync(guild, force=True)
    assert edited(guild) == ["banner", "bio"]


async def test_cambiar_el_avatar_de_diario_llega_al_look_que_lo_usa_de_reserva(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    await cog.manage(responder, "fijar", "casino", None, "", None)
    guild.me.edit.reset_mock()
    await cog.manage(responder, "avatar", "normal", attachment(png_bytes()), "", None)
    assert "avatar" in edited(guild)


async def test_el_texto_de_aspecto_no_fija_para_siempre_si_las_horas_no_son_un_numero(
    client,  # noqa: ANN001
) -> None:
    ctx = SimpleNamespace(
        guild=fake_guild(),
        author=fake_responder(fake_guild()).member,
        channel=None,
        message=SimpleNamespace(attachments=[]),
        send=AsyncMock(),
        command=None,
    )
    await client.get_cog("Admin").aspecto_text.callback(
        client.get_cog("Admin"), ctx, "fijar", "casino", texto="mañana"
    )
    assert "tienen que ser un número" in ctx.send.await_args.args[0]
    assert (await client.aspecto.state(GUILD_ID)).pinned_key is None


async def test_un_gif_animado_llega_a_discord_sin_perder_la_animacion(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    frames = [Image.new("RGB", (64, 64), color) for color in ((200, 0, 0), (0, 200, 0))]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=80)
    gif = buffer.getvalue()
    await cog.manage(responder, "fijar", "casino", None, "", None)
    guild.me.edit.reset_mock()
    await cog.manage(responder, "avatar", "casino", attachment(gif, "image/gif"), "", None)
    avatars = [c.kwargs["avatar"] for c in guild.me.edit.await_args_list if "avatar" in c.kwargs]
    assert avatars == [gif]
    assert discord.utils._get_mime_type_for_image(avatars[0]) == "image/gif"


async def test_un_look_sin_avatar_usa_el_de_diario(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    await cog.manage(responder, "avatar", "normal", attachment(png_bytes()), "", None)
    await cog.manage(responder, "fijar", "casino", None, "", None)
    avatars = [c.kwargs["avatar"] for c in guild.me.edit.await_args_list if "avatar" in c.kwargs]
    assert avatars == [await client.aspecto_images.load(GUILD_ID, "normal", "avatar")]


async def test_si_discord_rechaza_una_pieza_las_demas_se_aplican_y_se_avisa(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()

    async def edit(**fields: object) -> None:
        if "bio" in fields:
            raise http_error(400)

    guild.me.edit.side_effect = edit
    state = await cog.sync(guild)
    assert edited(guild) == ["banner", "bio"]
    assert state.applied_key is not None
    assert state.last_error == "Bio: Discord lo ha rechazado (error 400)."
    assert state.blocked_until == 0.0


async def test_un_429_para_y_respeta_la_espera_que_pide_discord(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    guild.me.edit.side_effect = http_error(429, retry_after="900")
    state = await cog.sync(guild)
    assert edited(guild) == ["banner"]
    assert state.applied_key is None
    assert 800 < state.blocked_until - datetime.now(TIMEZONE).timestamp() <= 900
    guild.me.edit.reset_mock()
    await cog.sync(guild, force=True)
    guild.me.edit.assert_not_awaited()


async def test_si_discord_no_contesta_se_deja_para_luego(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    guild.me.edit.side_effect = TimeoutError
    state = await cog.sync(guild)
    assert state.blocked_until > 0
    assert state.last_error == "Discord no ha contestado a tiempo."


async def test_fijar_un_look_lo_aplica_ya_y_auto_lo_quita(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    await cog.manage(responder, "fijar", "halloween", None, "", 6)
    state = await client.aspecto.state(GUILD_ID)
    assert (state.pinned_key, state.applied_key) == ("halloween", "halloween")
    assert state.pinned_until is not None
    assert responder.finish.await_args.args[0] == "✅ Look fijado: `halloween`."
    await cog.manage(responder, "auto", "", None, "", None)
    state = await client.aspecto.state(GUILD_ID)
    assert state.pinned_key is None
    assert state.applied_key == cog._desired(guild, state, {})


async def test_un_look_fijado_que_caduca_se_olvida(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    await client.aspecto.save_state(GUILD_ID, AspectoState(pinned_key="casino", pinned_until=1.0))
    state = await cog.sync(fake_guild())
    assert state.pinned_key is None


@pytest.mark.parametrize("hours", [0, rules.MAX_PIN_HOURS + 1])
async def test_las_horas_fuera_de_rango_se_rechazan(client, hours: int) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    responder = fake_responder(fake_guild())
    await cog.manage(responder, "fijar", "casino", None, "", hours)
    assert "⚠️" in responder.finish.await_args.args[0]
    assert (await client.aspecto.state(GUILD_ID)).pinned_key is None


async def test_crear_fijar_y_borrar_un_look_propio(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    await cog.manage(responder, "crear", "", None, "Modo Tortilla", None)
    assert responder.finish.await_args.args[0].startswith("✅ Look creado: `modo-tortilla`.")
    await cog.manage(responder, "avatar", "modo-tortilla", attachment(png_bytes()), "", None)
    await cog.manage(responder, "fijar", "modo-tortilla", None, "", None)
    assert (await client.aspecto.state(GUILD_ID)).applied_key == "modo-tortilla"
    assert await cog.look_options(GUILD_ID, "tortilla") == [("Modo Tortilla", "modo-tortilla")]

    await cog.manage(responder, "borrar", "modo-tortilla", None, "", None)
    state = await client.aspecto.state(GUILD_ID)
    assert state.pinned_key is None
    assert state.applied_key != "modo-tortilla"
    assert await client.aspecto_images.kinds(GUILD_ID, "modo-tortilla") == set()


async def test_los_looks_de_serie_no_se_borran_ni_se_pisan(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    responder = fake_responder(fake_guild())
    await cog.manage(responder, "borrar", "casino", None, "", None)
    assert "no se borran" in responder.finish.await_args.args[0]
    await cog.manage(responder, "crear", "", None, "Casino", None)
    assert "Ya hay un look de serie" in responder.finish.await_args.args[0]


@pytest.mark.parametrize("key", ["../../secreto", "a/b", "..\\x", "CON.png"])
async def test_una_clave_rara_no_llega_al_disco(client, tmp_path: Path, key: str) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    responder = fake_responder(fake_guild())
    await cog.manage(responder, "avatar", key, attachment(png_bytes()), "", None)
    assert "No hay ningún look" in responder.finish.await_args.args[0]
    assert not (tmp_path / "aspecto").exists()


async def test_un_adjunto_que_no_es_imagen_se_rechaza(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    responder = fake_responder(fake_guild())
    fake = attachment(b"hola", "text/plain")
    await cog.manage(responder, "banner", "casino", fake, "", None)
    assert responder.finish.await_args.args[0] == "⚠️ El adjunto tiene que ser una imagen."
    fake.read.assert_not_awaited()


async def test_una_imagen_que_miente_se_rechaza_sin_guardarse(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    responder = fake_responder(fake_guild())
    await cog.manage(responder, "avatar", "casino", attachment(b"no soy un png"), "", None)
    assert "⚠️" in responder.finish.await_args.args[0]
    assert await client.aspecto_images.kinds(GUILD_ID, "casino") == set()


async def test_una_bio_con_variable_desconocida_se_rechaza(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    responder = fake_responder(fake_guild())
    await cog.manage(responder, "bio", "casino", None, "Hola {token}", None)
    assert "{token}" in responder.finish.await_args.args[0]
    assert await client.aspecto.overrides(GUILD_ID) == {}


async def test_la_bio_y_el_estado_propios_se_aplican_y_se_pueden_quitar(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    await cog.manage(responder, "fijar", "casino", None, "", None)
    guild.me.edit.reset_mock()
    await cog.manage(responder, "bio", "casino", None, "Mesa cerrada, {version}", None)
    bios = [c.kwargs["bio"] for c in guild.me.edit.await_args_list if "bio" in c.kwargs]
    assert bios == [f"Mesa cerrada, {__version__}"]
    await cog.manage(responder, "estado", "casino", None, "Sin fichas", None)
    override = (await client.aspecto.overrides(GUILD_ID))["casino"]
    assert (override.bio, override.status) == ("Mesa cerrada, {version}", "Sin fichas")
    await cog.manage(responder, "bio", "casino", None, "", None)
    await cog.manage(responder, "estado", "casino", None, "", None)
    assert await client.aspecto.overrides(GUILD_ID) == {}


async def test_apagar_quita_el_perfil_y_no_se_vuelve_a_tocar(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    client.change_presence = AsyncMock()
    await cog.sync(guild)
    guild.me.edit.reset_mock()
    await cog.manage(responder, "apagar", "", None, "", None)
    calls = [c.kwargs for c in guild.me.edit.await_args_list]
    assert [(c.get("avatar", 0), c.get("banner", 0), c.get("bio", 0)) for c in calls] == [
        (None, 0, 0),
        (0, None, 0),
        (0, 0, None),
    ]
    state = await client.aspecto.state(GUILD_ID)
    assert (state.enabled, state.applied_key) == (False, None)
    guild.me.edit.reset_mock()
    await cog.sync(guild, force=True)
    guild.me.edit.assert_not_awaited()


async def test_encender_vuelve_a_poner_el_look(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    client.change_presence = AsyncMock()
    await cog.manage(responder, "apagar", "", None, "", None)
    await cog.manage(responder, "encender", "", None, "", None)
    state = await client.aspecto.state(GUILD_ID)
    assert state.enabled
    assert state.applied_key is not None


async def test_el_panel_dice_como_esta_todo(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    responder = fake_responder(guild)
    await cog.manage(responder, "avatar", "casino", attachment(png_bytes()), "", None)
    await cog.manage(responder, "", "", None, "", None)
    embed = responder.finish.await_args.kwargs["embed"]
    assert "**Estado:** encendido" in embed.description
    assert "`casino` 🖼️" in embed.description
    assert len(embed.description) <= 4096
    responder.start_progress.assert_awaited_with("🪞 Cambiando el aspecto…", ephemeral=True)


async def test_el_dragon_de_picar_llega_al_look_con_el_bot_cargado(client) -> None:  # noqa: ANN001
    """`picar` carga antes que `aspecto`: su puente tiene que encontrar el cog de verdad."""
    cog = client.get_cog("Aspecto")
    picar = sys.modules[type(client.get_cog("Pico")).__module__]
    picar.aspecto.dragon_killed(client, GUILD_ID)
    state = await cog.sync(fake_guild())
    assert state.applied_key == "dragon"


async def test_una_version_nueva_pone_el_look_de_actualizado(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    await client.aspecto.save_state(GUILD_ID, AspectoState(last_version="0.0.1"))
    state = await cog.sync(fake_guild())
    assert state.last_version == __version__
    assert state.applied_key == "actualizado"


async def test_una_beernight_abierta_pone_su_look(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    client.get_cog("Beernight").nights[GUILD_ID] = MagicMock()
    try:
        assert (await cog.sync(fake_guild())).applied_key == "beernight"
    finally:
        client.get_cog("Beernight").nights.clear()


async def test_el_estado_turna_datos_y_frases_del_look(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    client.change_presence = AsyncMock()
    guild = fake_guild()
    client._connection._guilds = {GUILD_ID: guild}
    await client.aspecto.save_state(GUILD_ID, AspectoState(applied_key="casino"))
    for _ in range(3):
        await cog._rotate_status()
    names = [c.kwargs["activity"].name for c in client.change_presence.await_args_list]
    # Sin datos solo está la versión, y detrás van las frases del look.
    assert names == [f"v{__version__}", *rules.CASINO.statuses[:2]]
    client._connection._guilds = {}


async def test_apagado_el_bot_no_lleva_estado(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    client.change_presence = AsyncMock()
    client._connection._guilds = {GUILD_ID: fake_guild()}
    await client.aspecto.save_state(GUILD_ID, AspectoState(enabled=False))
    await cog._rotate_status()
    client.change_presence.assert_awaited_once_with(activity=None)
    client._connection._guilds = {}


async def test_los_datos_salen_de_la_telemetria_y_el_acumulado_no_duplica(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    telemetry = client.telemetry_repository
    day = "2020-01-01"
    with telemetry._connect() as connection:
        connection.executemany(
            "INSERT INTO telemetry_counts (day, hour, guild_id, kind, name, events) "
            "VALUES (?, 0, ?, ?, ?, ?)",
            [
                (day, GUILD_ID, "slash", "tragas", 5),
                (day, GUILD_ID, "text", "tragas", 2),
                (day, 0, "arranque", "9.0.0", 3),
                (day, 99, "slash", "ajeno", 50),
            ],
        )
    today = datetime.now(TIMEZONE).date()
    first = await cog._lifetime(GUILD_ID, today)
    second = await cog._lifetime(GUILD_ID, today)
    assert first["commands"] == second["commands"] == 7
    assert first["restarts"] == 3


async def test_jovani_enseña_la_ficha_y_cuenta_el_dia_y_el_look(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    await client.aspecto.save_state(GUILD_ID, AspectoState(applied_key="casino"))
    responder = fake_responder(guild)
    await cog._jovani_impl(responder)
    embed = responder.finish.await_args.kwargs["embed"]
    assert embed.title == f"🎤 Jovani Vázquez · v{__version__}"
    assert embed.fields[0].value == rules.CASINO.name
    assert embed.thumbnail.url == guild.me.display_avatar.url
    profile = await client.get_cog("Achievements").fresh_profile(GUILD_ID, OWNER_ID)
    assert profile.stats["jovani_look_casino"] == 1
    assert "jovani_day_1" in profile.unlocked
    assert "jovani_saw_casino" in profile.unlocked


async def test_un_look_fijado_a_mano_no_cuenta_para_los_logros(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    await client.aspecto.save_state(
        GUILD_ID, AspectoState(applied_key="dragon", pinned_key="dragon")
    )
    await cog._jovani_impl(fake_responder(fake_guild()))
    profile = await client.get_cog("Achievements").fresh_profile(GUILD_ID, OWNER_ID)
    assert "jovani_look_dragon" not in profile.stats
    assert "jovani_day_1" in profile.unlocked


async def test_al_salir_del_servidor_se_borra_todo(client) -> None:  # noqa: ANN001
    cog = client.get_cog("Aspecto")
    guild = fake_guild()
    await cog.manage(fake_responder(guild), "avatar", "casino", attachment(png_bytes()), "", None)
    await cog.on_guild_remove(guild)
    assert await client.aspecto.state(GUILD_ID) == AspectoState()
    assert await client.aspecto_images.kinds(GUILD_ID, "casino") == set()
