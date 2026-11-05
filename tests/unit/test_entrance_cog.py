"""Pruebas de bot.cogs.entrance: panel, ranuras, compras y disparo al entrar o salir de voz."""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs.entrance import (
    COOLDOWN_SECONDS,
    MAX_PENDING_MS,
    AdjustForm,
    Entrance,
    EntrancePanel,
    Pending,
    UploadForm,
    parse_text_option,
)
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import SHOP_ACCOUNT_ID, STATE_ACCOUNT_ID, EconomyRepository
from bot.repositories.entrance_sounds import LEGACY_NAME, EntranceSoundStore
from bot.services.economy import EconomyService
from bot.services.entrance import (
    EXIT_PRICE,
    EXIT_SLOT,
    MAX_SLOTS,
    SLOT_PRICES,
    EntranceError,
    Mode,
    Profile,
    Slot,
    Upgrade,
)
from bot.services.entrance_sound import EntranceSoundError
from bot.services.taxes import igic

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg no está instalado"
)

GUILD_ID = 10
USER_ID = 7
RICH = 1_000_000


class FakeResponder:
    """Responder que guarda lo que el cog intenta enviar."""

    def __init__(self, *, user_id: int = USER_ID) -> None:
        self.guild = SimpleNamespace(id=GUILD_ID)
        self.member = MagicMock(spec=discord.Member)
        self.member.id = user_id
        self.member.bot = False
        self.channel = MagicMock()
        self.sent: list[tuple[str | None, dict[str, object]]] = []
        self.errors: list[str] = []
        self.finished: list[dict[str, object]] = []
        self.progress_ephemeral: list[bool] = []

    async def send(self, content=None, **kwargs) -> None:  # noqa: ANN001
        self.sent.append((content, kwargs))

    async def send_error(self, content: str) -> None:
        self.errors.append(content)

    async def start_progress(self, placeholder: str = "", *, ephemeral: bool = False) -> None:
        self.progress_ephemeral.append(ephemeral)

    async def finish(self, content=None, **kwargs) -> None:  # noqa: ANN001
        self.finished.append({"content": content, **kwargs})


def tone_bytes(tmp_path: Path, seconds: float, frequency: int = 440) -> bytes:
    """WAV sintético de la duración indicada."""
    path = tmp_path / f"tono-{seconds}-{frequency}.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return path.read_bytes()


def make_attachment(data: bytes, *, content_type: str | None = "audio/wav") -> MagicMock:
    """Adjunto de Discord simulado con el contenido dado."""
    attachment = MagicMock(spec=discord.Attachment)
    attachment.content_type = content_type
    attachment.size = len(data)
    attachment.read = AsyncMock(return_value=data)
    return attachment


async def make_cog(tmp_path: Path, *, balance: int = RICH) -> Entrance:
    """Cog con almacén y economía de verdad en una carpeta temporal y un bot simulado."""
    database = tmp_path / "bot.db"
    economy_repository = EconomyRepository(database, starting_balance=balance)
    await economy_repository.initialize()
    bot = MagicMock()
    bot.is_closed = MagicMock(return_value=False)
    bot.economy = EconomyService(economy_repository)
    store = EntranceSoundStore(tmp_path / "entradas", database)
    await store.initialize()
    cog = Entrance(bot, store)
    await cog.cog_load()
    return cog


def make_guild() -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    guild.get_member = MagicMock(return_value=None)
    return guild


def make_user(user_id: int = USER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    user.display_name = "Diego"
    return user


def sound(slot: int, *, plays: int = 0, name: str = "Bocina", ms: int = 1000) -> Slot:
    return Slot(
        slot=slot,
        name=name,
        volume=100,
        effect="normal",
        fade=False,
        start_ms=0,
        length_ms=3000,
        source_ms=ms,
        clip_ms=ms,
        wave="▁█▁",
        plays=plays,
    )


def ledger(tmp_path: Path, user_id: int) -> list[tuple[str, int]]:
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id",
            (GUILD_ID, user_id),
        ).fetchall()
    finally:
        connection.close()
    return [(row[0], row[1]) for row in rows if row[0] != "bienvenida"]


def count_components(item: object) -> int:
    """Componentes de una vista contados como Discord (cada uno, anidado o no)."""
    children = list(getattr(item, "children", []))
    accessory = getattr(item, "accessory", None)
    if accessory is not None:
        children.append(accessory)
    return sum(1 + count_components(child) for child in children)


# -- Atajos del comando --------------------------------------------------------------------


def test_parse_text_option_entiende_volumen_borrar_y_nada() -> None:
    assert parse_text_option(None) == (None, False)
    assert parse_text_option("150%") == (150, False)
    assert parse_text_option("BORRAR") == (None, True)
    with pytest.raises(EntranceSoundError):
        parse_text_option("fuerte")
    with pytest.raises(EntranceSoundError):
        parse_text_option("500")


async def test_sin_opciones_el_comando_pide_el_panel(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = FakeResponder()
    assert await cog._entrada_impl(responder, None, None, False) is False
    assert responder.sent == [] and responder.errors == []


@requires_ffmpeg
async def test_subir_un_audio_valido_lo_guarda_y_adjunta_la_vista_previa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(responder, make_attachment(tone_bytes(tmp_path, 2.0)), 150, False)

    assert responder.errors == []
    assert responder.progress_ephemeral == [True]
    assert "volumen 150 %" in responder.finished[-1]["content"]
    assert isinstance(responder.finished[-1]["file"], discord.File)
    saved = cog.profile(GUILD_ID, USER_ID).sounds[1]
    assert saved.volume == 150 and 1900 <= saved.clip_ms <= 2100 and saved.wave
    assert await cog.store.read_clip(GUILD_ID, USER_ID, 1) is not None
    # Lo de memoria es lo mismo que se guarda.
    assert (await cog.store.load_all())[(GUILD_ID, USER_ID)] == cog.profile(GUILD_ID, USER_ID)


@requires_ffmpeg
async def test_un_audio_largo_se_recorta_desde_el_segundo_elegido(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(
        responder, make_attachment(tone_bytes(tmp_path, 9.0)), None, False, start="2,5"
    )

    saved = cog.profile(GUILD_ID, USER_ID).sounds[1]
    assert 2900 <= saved.clip_ms <= 3100  # El máximo de serie.
    assert 6400 <= saved.source_ms <= 6600  # Se guarda el resto para recortar después.
    assert "guardada" in responder.finished[-1]["content"]


@requires_ffmpeg
async def test_empezar_despues_del_final_se_explica_y_no_guarda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(
        responder, make_attachment(tone_bytes(tmp_path, 2.0)), None, False, start="1:00"
    )

    assert "no puede empezar" in responder.finished[-1]["content"]
    assert cog.profile(GUILD_ID, USER_ID).empty


@requires_ffmpeg
async def test_cambiar_el_volumen_regenera_el_clip_sin_volver_a_subirlo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog._entrada_impl(
        FakeResponder(), make_attachment(tone_bytes(tmp_path, 1.0)), None, False
    )
    before = await cog.store.read_clip(GUILD_ID, USER_ID, 1)

    responder = FakeResponder()
    await cog._entrada_impl(responder, None, 40, False)

    assert cog.profile(GUILD_ID, USER_ID).sounds[1].volume == 40
    assert await cog.store.read_clip(GUILD_ID, USER_ID, 1) != before
    assert "volumen 40 %" in responder.finished[-1]["content"]


async def test_cambiar_el_volumen_sin_sonido_explica_que_hay_que_subir_uno(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(responder, None, 120, False)

    assert "Sube primero" in responder.errors[0]


async def test_una_imagen_se_rechaza_antes_de_descargarla(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = FakeResponder()
    attachment = make_attachment(b"png", content_type="image/png")

    await cog._entrada_impl(responder, attachment, None, False)

    assert responder.finished[-1]["content"] == "❌ Ese archivo no parece un audio."
    attachment.read.assert_not_called()


async def test_borrar_sin_sonido_lo_dice_y_es_efimero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    responder = FakeResponder()

    await cog._entrada_impl(responder, None, None, True)

    assert responder.sent == [("No tenías sonido en la ranura activa.", {"ephemeral": True})]


# -- Ranuras, efectos y recorte -------------------------------------------------------------


async def test_no_se_sube_a_una_ranura_sin_comprar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    with pytest.raises(EntranceError, match="Mejoras"):
        await cog.save_upload(GUILD_ID, USER_ID, 2, make_attachment(b"x"))
    with pytest.raises(EntranceError, match="salida"):
        await cog.save_upload(GUILD_ID, USER_ID, EXIT_SLOT, make_attachment(b"x"))


@requires_ffmpeg
async def test_la_ardilla_acelera_sin_acortar_el_sonido(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    plain, _clip, _d = await cog.save_upload(
        GUILD_ID, USER_ID, 1, make_attachment(tone_bytes(tmp_path, 8.0))
    )
    from dataclasses import replace

    squirrel, _clip = await cog.adjust(GUILD_ID, USER_ID, replace(plain, effect="ardilla"))

    assert squirrel.effect == "ardilla"
    assert abs(squirrel.clip_ms - plain.clip_ms) <= 100


@requires_ffmpeg
@pytest.mark.parametrize("effect", ["eco", "catedral", "robot", "grave", "radio", "alreves"])
async def test_cada_efecto_saca_un_clip_que_se_puede_reproducir(
    tmp_path: Path, effect: str
) -> None:
    cog = await make_cog(tmp_path)
    saved, clip, _d = await cog.save_upload(
        GUILD_ID, USER_ID, 1, make_attachment(tone_bytes(tmp_path, 2.0)), effect=effect
    )
    assert saved.effect == effect and saved.clip_ms > 1500 and clip


# -- Compras ---------------------------------------------------------------------------------


async def test_comprar_una_ranura_cobra_con_igic_al_estado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)

    result = await cog.buy(GUILD_ID, USER_ID, Upgrade.SLOT)

    base = SLOT_PRICES[2]
    tax = igic(base)
    assert isinstance(result, tuple) and result[1] == tax
    assert "Perro Sanxe" in result[0] and "IGIC" in result[0]
    assert ledger(tmp_path, USER_ID) == [("tienda:entrada:ranura", -base), ("tienda:igic", -tax)]
    assert ledger(tmp_path, SHOP_ACCOUNT_ID) == [("tienda:venta", base)]
    assert ("igic:tienda", tax) in ledger(tmp_path, STATE_ACCOUNT_ID)
    assert cog.profile(GUILD_ID, USER_ID).slots == 2
    assert (await cog.store.load_all())[(GUILD_ID, USER_ID)].slots == 2


async def test_sin_dinero_no_se_compra_ni_se_apunta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, balance=100)

    result = await cog.buy(GUILD_ID, USER_ID, Upgrade.EXIT)

    assert isinstance(result, str) and "Necesitas" in result
    assert ledger(tmp_path, USER_ID) == []
    assert not cog.profile(GUILD_ID, USER_ID).exit_owned
    assert await cog.store.load_all() == {}


async def test_una_compra_repetida_desde_otro_panel_no_cobra_dos_veces(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.buy(GUILD_ID, USER_ID, Upgrade.EXIT)
    # Otro panel con la ficha de antes en memoria intenta lo mismo.
    cog._profiles[(GUILD_ID, USER_ID)] = Profile()

    result = await cog.buy(GUILD_ID, USER_ID, Upgrade.EXIT)

    assert isinstance(result, str) and "han cambiado" in result
    charged = [
        delta for reason, delta in ledger(tmp_path, USER_ID) if reason.startswith("tienda:entrada")
    ]
    assert charged == [-EXIT_PRICE]


async def test_al_maximo_no_se_vende_mas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    for _ in range(MAX_SLOTS - 1):
        assert isinstance(await cog.buy(GUILD_ID, USER_ID, Upgrade.SLOT), tuple)
    assert await cog.buy(GUILD_ID, USER_ID, Upgrade.SLOT) == "Eso ya lo tienes al máximo."


# -- Panel -----------------------------------------------------------------------------------


async def test_el_panel_lleno_cabe_en_los_limites_de_discord(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    profile = Profile(slots=MAX_SLOTS, exit_owned=True)
    for n in [*range(1, MAX_SLOTS + 1), EXIT_SLOT]:
        profile = profile.with_sound(sound(n, plays=n))
    cog._profiles[(GUILD_ID, USER_ID)] = profile
    panel = EntrancePanel(cog, make_guild(), make_user())
    for page in ("main", "slot", "shop", "ranking"):
        panel.page = page
        panel.rebuild()
        assert count_components(panel) <= 40, page


async def test_cambiar_de_pagina_contesta_sin_ack(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = EntrancePanel(cog, make_guild(), make_user())
    buttons = {c.custom_id: c for c in panel.walk_children() if isinstance(c, discord.ui.Button)}
    interaction = fake_interaction(make_user())

    await buttons["entrada:mejoras"].callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    interaction.response.defer.assert_not_called()
    assert panel.page == "shop"


async def test_comprar_pide_confirmacion_y_luego_cobra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = EntrancePanel(cog, make_guild(), make_user())
    panel.page = "shop"
    panel.rebuild()

    def buy_button() -> discord.ui.Button:
        return next(
            c
            for c in panel.walk_children()
            if isinstance(c, discord.ui.Button) and c.custom_id == "entrada:comprar:ranura"
        )

    first = fake_interaction(make_user())
    await buy_button().callback(first)
    assert ledger(tmp_path, USER_ID) == []
    assert buy_button().label.startswith("¿Pagar")

    second = fake_interaction(make_user())
    second.channel = MagicMock()
    await buy_button().callback(second)

    second.response.defer.assert_awaited_once()
    second.edit_original_response.assert_awaited()
    assert cog.profile(GUILD_ID, USER_ID).slots == 2
    assert "**Ranura 2**" in (panel.note or "") and "IGIC" in (panel.note or "")


async def test_el_modo_cambia_en_memoria_y_se_guarda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = EntrancePanel(cog, make_guild(), make_user())
    interaction = fake_interaction(make_user())
    interaction.channel = MagicMock()

    await cog.button_mode(interaction, panel)

    interaction.response.edit_message.assert_awaited_once()
    assert cog.profile(GUILD_ID, USER_ID).mode is Mode.RANDOM
    assert (await cog.store.load_all())[(GUILD_ID, USER_ID)].mode is Mode.RANDOM


async def test_el_panel_de_otro_no_se_toca(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = EntrancePanel(cog, make_guild(), make_user())
    intruder = fake_interaction(make_user(99))
    assert await panel.interaction_check(intruder) is False
    intruder.response.send_message.assert_awaited_once()


@requires_ffmpeg
async def test_el_formulario_de_subida_guarda_y_abre_la_ranura(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel = EntrancePanel(cog, make_guild(), make_user())
    form = UploadForm(panel, 1)
    form.upload._values = [make_attachment(tone_bytes(tmp_path, 4.0))]
    form.name._value = "Fanfarria"
    form.start._value = "0,5"
    form.volume._value = "120"
    form.effect._values = ["eco"]
    interaction = fake_interaction(make_user())
    interaction.channel = MagicMock()

    await form.on_submit(interaction)

    interaction.response.defer.assert_awaited_once()
    saved = cog.profile(GUILD_ID, USER_ID).sounds[1]
    assert (saved.name, saved.volume, saved.effect) == ("Fanfarria", 120, "eco")
    assert panel.page == "slot" and panel.slot == 1
    assert "Me quedo con 3 s de 4 s" in (panel.note or "")


@requires_ffmpeg
async def test_ajustar_solo_el_nombre_no_vuelve_a_sacar_el_audio(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    saved, _clip, _d = await cog.save_upload(
        GUILD_ID, USER_ID, 1, make_attachment(tone_bytes(tmp_path, 2.0))
    )
    panel = EntrancePanel(cog, make_guild(), make_user())
    form = AdjustForm(panel, saved)
    form.name._value = "Nuevo"
    form.effect._values = [saved.effect]
    cog.adjust = AsyncMock()  # type: ignore[method-assign]
    interaction = fake_interaction(make_user())
    interaction.channel = MagicMock()

    await form.on_submit(interaction)

    cog.adjust.assert_not_called()
    assert cog.profile(GUILD_ID, USER_ID).sounds[1].name == "Nuevo"
    assert (await cog.store.load_all())[(GUILD_ID, USER_ID)].sounds[1].name == "Nuevo"


async def test_el_ranking_ordena_por_veces_y_solo_del_servidor(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog._profiles = {
        (GUILD_ID, 1): Profile().with_sound(sound(1, plays=5)),
        (GUILD_ID, 2): Profile().with_sound(sound(1, plays=9)).with_sound(sound(2, plays=0)),
        (99, 3): Profile().with_sound(sound(1, plays=50)),
    }
    top = cog.ranking(GUILD_ID)
    assert [(user, s.plays) for user, s in top] == [(2, 9), (1, 5)]


# -- Migración de antes de las ranuras --------------------------------------------------------


@requires_ffmpeg
async def test_el_sonido_de_antes_pasa_a_la_ranura_1(tmp_path: Path) -> None:
    from bot.services.entrance_sound import build_source_clip, render_clip

    guild_dir = tmp_path / "entradas" / str(GUILD_ID)
    guild_dir.mkdir(parents=True)
    wav = tmp_path / "t.wav"
    wav.write_bytes(tone_bytes(tmp_path, 2.0))
    await build_source_clip(wav, guild_dir / f"{USER_ID}.source.ogg")
    await render_clip(guild_dir / f"{USER_ID}.source.ogg", guild_dir / f"{USER_ID}.ogg", 130)
    (guild_dir / f"{USER_ID}.json").write_text(json.dumps({"volume_percent": 130}))

    store = EntranceSoundStore(tmp_path / "entradas", tmp_path / "bot.db")
    await store.initialize()
    await store.initialize()  # Dos arranques seguidos no duplican nada.

    profiles = await store.load_all()
    migrated = profiles[(GUILD_ID, USER_ID)].sounds[1]
    assert (migrated.name, migrated.volume) == (LEGACY_NAME, 130)
    assert 1900 <= migrated.clip_ms <= 2100 and migrated.wave
    assert not (guild_dir / f"{USER_ID}.ogg").exists()
    assert not (guild_dir / f"{USER_ID}.json").exists()
    assert await store.read_clip(GUILD_ID, USER_ID, 1) is not None
    assert (guild_dir / str(USER_ID) / "1.source.ogg").is_file()


# -- Voz -------------------------------------------------------------------------------------


def test_el_enfriamiento_bloquea_repeticiones_de_la_misma_persona(tmp_path: Path) -> None:
    cog = Entrance(MagicMock(), EntranceSoundStore(tmp_path, tmp_path / "bot.db"))
    assert cog._cooldown_allows(1, 2)
    assert not cog._cooldown_allows(1, 2)
    assert cog._cooldown_allows(1, 3)
    # Entrar y salir llevan enfriamientos separados.
    assert cog._cooldown_allows(1, 2, leaving=True)
    assert COOLDOWN_SECONDS > 0


def voice_member(user_id: int = USER_ID, *, bot: bool = False) -> MagicMock:
    """Miembro de un servidor sin canal AFK."""
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = bot
    member.guild = SimpleNamespace(id=GUILD_ID, afk_channel=None)
    return member


def voice_cog(tmp_path: Path, profile: Profile | None) -> Entrance:
    bot = MagicMock()
    bot.is_closed = MagicMock(return_value=False)
    cog = Entrance(bot, EntranceSoundStore(tmp_path, tmp_path / "bot.db"))
    if profile is not None:
        cog._profiles[(GUILD_ID, USER_ID)] = profile
    cog._ensure_worker = MagicMock()  # type: ignore[method-assign]
    return cog


async def test_silenciarse_dentro_del_mismo_canal_no_dispara_el_sonido(tmp_path: Path) -> None:
    cog = voice_cog(tmp_path, Profile().with_sound(sound(1)))
    state = SimpleNamespace(channel=SimpleNamespace(id=99))

    await cog.on_voice_state_update(voice_member(), state, state)

    assert cog._pending == {}


async def test_entrar_a_un_canal_encola_la_ranura_que_toca(tmp_path: Path) -> None:
    profile = Profile(slots=3, mode=Mode.CYCLE).with_sound(sound(1)).with_sound(sound(3))
    cog = voice_cog(tmp_path, profile)
    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=SimpleNamespace(id=99))

    await cog.on_voice_state_update(voice_member(), before, after)
    cog._last_played.clear()
    await cog.on_voice_state_update(voice_member(), before, after)

    assert [p.slot for p in cog._pending[GUILD_ID]] == [1, 3]
    cog._ensure_worker.assert_called()


async def test_sin_ficha_no_se_encola_nada(tmp_path: Path) -> None:
    cog = voice_cog(tmp_path, None)
    after = SimpleNamespace(channel=SimpleNamespace(id=99))
    await cog.on_voice_state_update(voice_member(), SimpleNamespace(channel=None), after)
    assert cog._pending == {}


async def test_los_bots_no_tienen_sonido_de_entrada(tmp_path: Path) -> None:
    cog = voice_cog(tmp_path, Profile().with_sound(sound(1)))
    after = SimpleNamespace(channel=SimpleNamespace(id=99))
    await cog.on_voice_state_update(voice_member(bot=True), SimpleNamespace(channel=None), after)
    assert cog._pending == {}


async def test_al_irse_suena_la_salida_si_queda_alguien(tmp_path: Path) -> None:
    profile = Profile(exit_owned=True).with_sound(sound(EXIT_SLOT))
    cog = voice_cog(tmp_path, profile)
    friend = SimpleNamespace(id=55, bot=False)
    channel = SimpleNamespace(id=99, members=[friend])

    await cog.on_voice_state_update(
        voice_member(), SimpleNamespace(channel=channel), SimpleNamespace(channel=None)
    )

    assert list(cog._pending[GUILD_ID]) == [Pending(USER_ID, 99, EXIT_SLOT, 1000)]


async def test_al_irse_sin_nadie_o_sin_salida_no_suena(tmp_path: Path) -> None:
    alone = SimpleNamespace(id=99, members=[SimpleNamespace(id=1, bot=True)])
    cog = voice_cog(tmp_path, Profile(exit_owned=True).with_sound(sound(EXIT_SLOT)))
    await cog.on_voice_state_update(
        voice_member(), SimpleNamespace(channel=alone), SimpleNamespace(channel=None)
    )
    assert cog._pending == {}

    crowded = SimpleNamespace(id=99, members=[SimpleNamespace(id=55, bot=False)])
    # Sin haberla comprado, aunque tenga el audio de antes, no suena.
    cog = voice_cog(tmp_path, Profile(exit_owned=False).with_sound(sound(EXIT_SLOT)))
    await cog.on_voice_state_update(
        voice_member(), SimpleNamespace(channel=crowded), SimpleNamespace(channel=None)
    )
    assert cog._pending == {}


async def test_la_cola_tiene_tope_de_segundos(tmp_path: Path) -> None:
    cog = voice_cog(tmp_path, None)
    guild = make_guild()
    long_ms = 5000
    for user_id in range(MAX_PENDING_MS // long_ms + 2):
        cog._enqueue(guild, Pending(user_id, 99, 1, long_ms))
    assert sum(p.ms for p in cog._pending[GUILD_ID]) <= MAX_PENDING_MS


async def test_si_la_musica_ocupa_la_voz_no_suena_ni_se_conecta(tmp_path: Path) -> None:
    cog = voice_cog(tmp_path, None)
    cog._join = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock()
    guild.id = GUILD_ID
    guild.voice_client = MagicMock()  # Conexión de la música.
    cog._pending[GUILD_ID] = deque([Pending(USER_ID, 99, 1, 1000)])

    await cog._drain(guild)

    cog._join.assert_not_called()
    assert not cog._pending[GUILD_ID]


async def test_varios_sonidos_seguidos_reutilizan_la_conexion_y_cuentan_para_el_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = voice_cog(tmp_path, None)
    await cog.store.initialize()
    for user_id in (1, 2):
        cog._profiles[(GUILD_ID, user_id)] = Profile().with_sound(sound(1))
    channel = SimpleNamespace(id=99)
    guild = MagicMock()
    guild.id = GUILD_ID
    guild.voice_client = None

    def member_in_channel(member_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=member_id, voice=SimpleNamespace(channel=channel))

    guild.get_member = member_in_channel
    cog._can_join = MagicMock(return_value=True)  # type: ignore[method-assign]
    cog.store.read_clip = AsyncMock(return_value=b"clip")  # type: ignore[method-assign]
    voice = MagicMock()
    voice.is_connected = MagicMock(return_value=True)
    voice.disconnect = AsyncMock()

    async def fake_join(_channel: object, current: object) -> MagicMock:
        guild.voice_client = voice
        return voice

    cog._join = AsyncMock(side_effect=fake_join)  # type: ignore[method-assign]
    cog._play = AsyncMock()  # type: ignore[method-assign]
    cog._pending[GUILD_ID] = deque([Pending(1, 99, 1, 1000), Pending(2, 99, 1, 1000)])

    # Se parchea en los globals reales del cog: las pruebas de integración
    # recargan el módulo con `load_extension`, así que importarlo aquí podría
    # devolver otra copia.
    monkeypatch.setitem(Entrance._drain.__globals__, "read_opus_packets", lambda data: [data])
    await cog._drain(guild)

    assert cog._play.await_count == 2
    assert cog._join.await_args_list[1].args[1] is voice  # Segunda vez reutiliza.
    voice.disconnect.assert_awaited_once()
    assert [s.plays for _u, s in cog.ranking(GUILD_ID)] == [1, 1]


async def test_el_clip_se_lee_del_disco_una_vez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = voice_cog(tmp_path, None)
    cog.store.read_clip = AsyncMock(return_value=b"clip")  # type: ignore[method-assign]
    monkeypatch.setitem(Entrance._packets.__globals__, "read_opus_packets", lambda data: [data])
    assert await cog._packets(GUILD_ID, USER_ID, 1) == [b"clip"]
    assert await cog._packets(GUILD_ID, USER_ID, 1) == [b"clip"]
    cog.store.read_clip.assert_awaited_once()
    cog._forget_clip(GUILD_ID, USER_ID, 1)
    await cog._packets(GUILD_ID, USER_ID, 1)
    assert cog.store.read_clip.await_count == 2


async def test_play_espera_al_callback_del_hilo_de_audio(tmp_path: Path) -> None:
    cog = voice_cog(tmp_path, None)
    voice = MagicMock()

    def fake_play(source: object, *, after) -> None:  # noqa: ANN001
        # discord.py llama a `after` desde otro hilo al terminar.
        asyncio.get_running_loop().run_in_executor(None, after, None)

    voice.play = fake_play
    await asyncio.wait_for(cog._play(voice, [b"x"] * 5), timeout=2)
    voice.stop.assert_not_called()


def test_la_tabla_de_fichas_existe_tras_cargar(tmp_path: Path) -> None:
    store = EntranceSoundStore(tmp_path / "e", tmp_path / "bot.db")
    assert asyncio.run(store.load_all()) == {}
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    assert {"entrance_profiles", "entrance_slots"} <= tables


async def test_la_entrada_no_suena_hasta_que_el_cifrado_esta_listo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = voice_cog(tmp_path, None)
    order: list[str] = []
    voice = MagicMock()

    async def connect(**_kwargs: object) -> MagicMock:
        order.append("conecta")
        return voice

    async def audible(_voice: object) -> bool:
        order.append("cifrado listo")
        return True

    channel = MagicMock()
    channel.connect = connect
    monkeypatch.setitem(Entrance._join.__globals__, "wait_until_audible", audible)
    monkeypatch.setitem(Entrance._join.__globals__, "PRE_ROLL_SECONDS", 0)

    assert await cog._join(channel, None) is voice
    assert order == ["conecta", "cifrado listo"]
