"""Las funciones puente entre cogs encuentran su cog con el bot cargado de verdad.

`load_extension` vuelve a ejecutar el módulo de cada extensión, así que los
cogs que importaron `bot.cogs.achievements`, `bot.cogs.renta` o
`bot.cogs.shop` antes de que se cargaran tienen una copia antigua del módulo.
Las pruebas unitarias crean los cogs a mano y no lo ven; aquí se carga todo
con `INITIAL_EXTENSIONS`, en el mismo orden que en producción.
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from coin_fakes import Scripted
from craps_fakes import Scripted as DiceScripted
from interaction_fakes import fake_interaction
from render_fakes import use_fake_drawings

from bot.app import INITIAL_EXTENSIONS, BotClient
from bot.repositories.economy import LedgerEntry
from bot.repositories.rastro import Listing
from bot.services import housing as housing_rules
from bot.services.achievements import StatDelta
from bot.services.blackjack import Card
from bot.services.coin import Outcome
from bot.services.donations import BY_KEY
from bot.services.economy import format_amount
from bot.services.enraya import Board, Disc
from bot.services.hold_win_render import Media
from bot.services.robuso import ROBUSO_ID
from bot.services.skins import SKIN_BY_KEY
from bot.services.work import ShiftRecord

GUILD_ID = 1
GUILD = GUILD_ID
OWNER_ID = 10
OTHER_ID = 20
GAMES = (
    "Casino",
    "Blackjack",
    "Tragaperras",
    "Botes",
    "Crash",
    "Minas",
    "Torre",
    "Guerra",
    "Pollo",
    "Moneda",
    "Dados",
    "Pachinko",
    "Pico",
    "Caballos",
    "Bacará",
    "Gallos",
    "Enraya",
    "Backgammon",
    "Porras",
    "Loteria",
)


@pytest.fixture(autouse=True)
def fake_drawings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin dibujar las máquinas del casino: ninguna aserción de aquí mira una imagen.

    Estas pruebas comprueban que lo jugado llega a logros, apuestas y Renta. El GIF de
    la tragaperras, la ruleta o el pachinko (y su precalentamiento al cargar el cog) no
    cambia nada de eso y costaba 1-3 s por prueba. Cogs, bot y base de datos siguen siendo
    los reales; solo se sustituye el dibujo (ver `render_fakes`).
    """
    use_fake_drawings(monkeypatch)


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "message_stats.sqlite3")
    await client.message_stats.initialize()
    await client.economy.repository.initialize()
    await client.casino_stats.initialize()
    await client.birthdays.initialize()
    await client.achievements.initialize()
    await client.welcome.initialize()
    await client.shop.initialize()
    await client.housing.initialize()
    await client.work.repository.initialize()
    await client.negocio.repository.initialize()
    await client.horses.initialize()
    await client.gallos.initialize()
    await client.enraya.initialize()
    await client.backgammon.initialize()
    await client.poker.initialize()
    await client.porras.initialize()
    await client.sports.initialize()
    await client.predice.initialize()
    await client.rueda.initialize()
    await client.slots_repository.initialize()
    await client.picar.initialize()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    return client


def importer(cog_name: str, client: BotClient):  # noqa: ANN201
    """Módulo (el que quedó registrado) del cog `cog_name`."""
    cog = client.get_cog(cog_name)
    assert cog is not None
    return sys.modules[type(cog).__module__]


async def test_los_juegos_que_cargan_antes_encuentran_logros_y_renta(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        for game in GAMES:
            module = importer(game, client)
            assert module.logros._cog(client) is client.get_cog("Achievements"), game
        renta_cog = client.get_cog("Renta")
        renta_cog.hint_for = AsyncMock(return_value="📬 Tienes la renta pendiente")
        for game in GAMES:
            module = importer(game, client)
            hint = await module.renta.hint(client, GUILD_ID, OWNER_ID)
            assert hint == "📬 Tienes la renta pendiente", game
    finally:
        await client.close()


async def test_robuso_guarda_donde_esta_y_lo_suyo_llega_a_los_logros(tmp_path: Path) -> None:
    """`robuso` de Admin llega al cog de Robuso, que lo apunta en sus logros, y lo de
    Robuso suma su eco en 🎸 Robuso con los cogs cargados de verdad."""
    client = await load_bot(tmp_path)
    try:
        responder = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_ID),
            channel=None,
            start_progress=AsyncMock(),
            finish=AsyncMock(),
            send_error=AsyncMock(),
        )
        await client.get_cog("Admin")._robuso_impl(responder, True)
        assert responder.finish.await_args.args[0] == "Hecho: Robuso está ahora en **Hong Kong**."
        robuso = importer("Admin", client).robuso_logros
        assert await robuso.in_hong_kong(client, GUILD_ID) is True
        achievements = client.get_cog("Achievements")
        achievements.note(GUILD_ID, ROBUSO_ID, StatDelta(add={"messages": 1}))
        profile = await achievements.fresh_profile(GUILD_ID, ROBUSO_ID)
        assert profile.stats["robuso_msgs"] == 1
        assert profile.stats["robuso_trips"] == 1
        assert {"robuso_hola", "robuso_viaje_1"} <= set(profile.unlocked)
        # El cog de Robuso suma a los demás a través del puente de logros.
        client.get_cog("Robuso")._note(GUILD_ID, OWNER_ID, {"robuso_pings": 1})
        friend = await achievements.fresh_profile(GUILD_ID, OWNER_ID)
        assert friend.stats["robuso_pings"] == 1
        assert "robuso_mencion_1" in friend.unlocked
    finally:
        await client.close()


async def test_los_juegos_que_cargan_antes_apuntan_sus_jugadas_en_apuestas(
    tmp_path: Path,
) -> None:
    """`apuestas` carga después que los juegos: su función puente tiene que verlo."""
    client = await load_bot(tmp_path)
    try:
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        for game in GAMES[:-1]:  # la lotería no es una jugada del casino
            module = importer(game, client)
            await module.apuestas.record(
                client, GUILD_ID, owner, game="ruleta", stake=100, net=-100, balance_after=0, tax=0
            )
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.total.plays == len(GAMES) - 1
        # Y cada jugada cuenta también para los logros del casino entero.
        pending = client.get_cog("Achievements")._pending[GUILD_ID][OWNER_ID]
        assert pending.add["casino_plays"] == len(GAMES) - 1
        assert pending.add["casino_game_ruleta"] == len(GAMES) - 1
    finally:
        await client.close()


async def test_los_niveles_ven_los_potenciadores_de_la_tienda(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        client.get_cog("Tienda").xp_multiplier = MagicMock(return_value=2.0)
        tienda = importer("MessageStats", client).tienda
        assert tienda.xp_multiplier(client, GUILD_ID, OWNER_ID, 0.0) == 2.0
    finally:
        await client.close()


async def test_rafaga_de_la_tragaperras_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """El fallo de producción: diez tiradas de la Ráfaga jugadas y ninguna en los logros."""
    client = await load_bot(tmp_path)
    try:
        slots = importer("Tragaperras", client)
        slots.REVEAL_MARGIN_SECONDS = 0
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        view = slots.SlotMachineView(
            client.get_cog("Tragaperras"), guild_id=GUILD_ID, owner=owner, stake=1
        )
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        view.message = MagicMock()
        view.message.channel = channel
        view.cog.ahead = False
        view.cog.renderer = MagicMock()
        view.cog.renderer.still = AsyncMock(return_value=b"PNG")
        view.cog.renderer.close = AsyncMock()
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._burst(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["slots_spins"] == view.session_spins == slots.BURST_SPINS
        assert profile.stats["slots_auto"] == 1
        assert {"slots_1", "auto_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["tragaperras"].plays + plays.by_game["tragaperras"].free_plays == (
            slots.BURST_SPINS
        )
    finally:
        await client.close()


async def test_auto_de_la_tragaperras_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """▶️ Auto con el bot cargado: cada tirada y la sesión llegan a los logros."""
    client = await load_bot(tmp_path)
    try:
        slots = importer("Tragaperras", client)
        slots.REVEAL_MARGIN_SECONDS = 0
        slots.AUTOPLAY_MIN_GAP = 0
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        cog = client.get_cog("Tragaperras")
        view = slots.SlotMachineView(cog, guild_id=GUILD_ID, owner=owner, stake=1)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        view.message = MagicMock()
        view.message.channel = channel
        view.message.edit = AsyncMock()
        cog.ahead = False
        cog.renderer = MagicMock()
        cog.renderer.spin = AsyncMock(
            return_value=slots.SlotsMedia(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        cog.renderer.close = AsyncMock()
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._autoplay_click(interaction)
        await view.autoplay.task

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert view.session_spins >= 1
        assert profile.stats["slots_spins"] == profile.stats["slots_autoplay_spins"]
        assert profile.stats["slots_autoplay_spins"] == view.session_spins
        assert profile.stats["slots_autoplay_sessions"] == 1
        assert {"slots_1", "autoplay_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        played = plays.by_game["tragaperras"]
        assert played.plays + played.free_plays == view.session_spins
    finally:
        await client.close()


async def test_auto_del_pachinko_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """▶️ Auto del pachinko con el bot cargado, con precarga: cada tanda y la sesión llegan."""
    client = await load_bot(tmp_path)
    try:
        pachinko = importer("Pachinko", client)
        pachinko.REVEAL_MARGIN_SECONDS = 0
        pachinko.AUTOPLAY_MIN_GAP = 0
        # Cuatro tandas bastan para probar la precarga (se prepara la siguiente mientras se
        # juega la actual): las 25 de un Auto completo cuestan ~75 ms de física cada una.
        pachinko.AUTOPLAY_MAX = 4
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        cog = client.get_cog("Pachinko")
        view = pachinko.PachinkoView(cog, guild_id=GUILD_ID, owner=owner, stake=10)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        view.message = MagicMock()
        view.message.channel = channel
        view.message.edit = AsyncMock()
        cog.machines.add(view)
        cog.renderer = MagicMock()
        cog.renderer.render = MagicMock(
            return_value=pachinko.PachinkoMedia(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._autoplay_click(interaction)
        await view.autoplay.task

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert view.session_volleys >= 1
        assert profile.stats["pachinko_volleys"] == view.session_volleys
        assert profile.stats["pachinko_autoplay_volleys"] == view.session_volleys
        assert profile.stats["pachinko_autoplay_sessions"] == 1
        assert {"pachi_1", "pachi_auto_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["pachinko"].plays == view.session_volleys
    finally:
        await client.close()


async def test_el_pico_apunta_la_tirada_sus_logros_y_su_jugada_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Una tirada de `picar` con el bot cargado: dinero, tirada, logros, apuestas y repicar."""
    client = await load_bot(tmp_path)
    try:
        picar = importer("Pico", client)
        picar.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Pico")
        await cog._check_task
        assert cog.disabled_reason is None, cog.disabled_reason
        await client.economy.grant(GUILD_ID, OWNER_ID, amount=1_000, reason="prueba")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        view = picar.PicarView(cog, guild_id=GUILD_ID, owner=owner)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None
        interaction.channel = channel

        await view._dig(interaction, "stone")

        record = await client.picar.get(GUILD_ID, 1)
        assert record is not None and record.pick == "stone" and record.user_id == OWNER_ID
        assert record.cost == 50 and record.params == cog.calibration.params
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["picar_digs"] == 1 and profile.stats["picar_dig_stone"] == 1
        assert "picar_1" in profile.unlocked
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["picar"].plays == 1

        # `repicar` (Admin) encuentra el pozo con `find_cog` y vuelve a jugar la semilla.
        admin = client.get_cog("Admin")
        responder = MagicMock()
        responder.guild = MagicMock(spec=discord.Guild)
        responder.guild.id = GUILD_ID
        responder.guild.filesize_limit = 10 * 1024 * 1024
        responder.channel = MagicMock(spec=discord.TextChannel)
        replayed = MagicMock()
        replayed.edit = AsyncMock()
        responder.channel.send = AsyncMock(return_value=replayed)
        responder.send = AsyncMock()
        responder.send_error = AsyncMock()
        await admin._replay_dig_impl(responder, record.id)
        responder.send_error.assert_not_awaited()
        embed = responder.channel.send.await_args.kwargs["embed"]
        assert "Sale igual" in embed.description
    finally:
        await client.close()


async def test_el_pollo_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """El Pollo carga antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        pollo = importer("Pollo", client)
        pollo.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Pollo")
        cog.renderer = MagicMock()
        cog.renderer.hops = AsyncMock(return_value=pollo.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        cog.renderer.close = AsyncMock()
        # Con un dibujo falso, pintar por adelantado contaría dibujos de más.
        cog.ahead = False
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._pollo_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            difficulty=None,
            auto=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        view.game.hit_lane = 1
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._cross(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["chicken_games"] == 1
        assert profile.stats["chicken_splats"] == 1
        assert {"pollo_1", "pollos_1", "pollo_ni_acera"} <= set(profile.unlocked)
    finally:
        await client.close()


async def test_la_moneda_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """La moneda carga antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        moneda = importer("Moneda", client)
        moneda.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Moneda")
        cog.renderer = MagicMock()
        cog.renderer.toss = AsyncMock(
            return_value=moneda.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        # Con un dibujo falso, pintar por adelantado gastaría el azar de guion.
        cog.ahead = False
        cog.rng = Scripted(Outcome.CRUZ, Outcome.CARA)  # pide cara, sale cruz
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._moneda_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            pick=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._flip_cara(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["coin_games"] == 1
        assert profile.stats["coin_losses"] == 1
        assert "moneda_1" in profile.unlocked
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["moneda"].plays == 1
    finally:
        await client.close()


async def test_los_dados_apuntan_sus_logros_y_jugadas_con_el_bot_real(tmp_path: Path) -> None:
    """Los dados cargan antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        dados = importer("Dados", client)
        dados.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Dados")
        cog.renderer = MagicMock()
        cog.renderer.throw = AsyncMock(
            return_value=dados.Media(gif=b"GIF", png=b"PNG", seconds=0.0, rest=dados.OPENING_REST)
        )
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        cog.rng = DiceScripted((1, 1))  # pide pase, sale pifia
        # Con un dibujo falso, pintar por adelantado gastaría el azar de guion.
        cog.ahead = False
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._dados_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            bet=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._pass(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["dice_games"] == 1
        assert profile.stats["dice_losses"] == 1
        assert profile.stats["dice_snake_eyes"] == 1
        assert {"dados_1", "dados_pegasus", "dados_pifia"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["dados"].plays == 1
    finally:
        await client.close()


async def test_la_mesa_compartida_de_los_dados_apunta_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`dados mesa` cuenta sus logros de mesa y sus jugadas, y lleva el 🎨 con el bot cargado."""
    from bot.services.craps import Bet

    client = await load_bot(tmp_path)
    try:
        dados = importer("Dados", client)
        cog = client.get_cog("Dados")
        cog.renderer = MagicMock()
        cog.renderer.throw = AsyncMock(
            return_value=dados.Media(gif=b"GIF", png=b"PNG", seconds=0.0, rest=dados.OPENING_REST)
        )
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        cog.rng = DiceScripted((1, 1))  # pide pase, sale pifia
        cog.ahead = False
        cog.start = MagicMock()

        async def no_wait(seconds: float) -> None:
            return None

        cog.sleep = no_wait
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 9
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        table, _new = await cog.ensure_table(GUILD_ID, channel, OWNER_ID)
        assert table is not None
        for view in (table.open_view, table.point_view):
            assert any(
                isinstance(item, discord.ui.Button) and str(item.emoji) == "🎨"
                for item in view.children
            ), "la mesa compartida sin 🎨"
        await table.bet_now(owner, Bet.PASS, 100)
        await table.roll()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["dice_table_rolls"] == 1
        assert profile.stats["dice_shooter"] == 1
        assert {"dadosm_1", "dadosm_tirador", "dadosm_solitario"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["dados"].plays == 1
    finally:
        await client.close()


async def test_las_carreras_apuntan_sus_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los caballos cargan antes que los logros y `apuestas`: sus boletos deben llegar."""
    from bot.services.horses import SEGMENTS, RaceResult, estimate

    client = await load_bot(tmp_path)
    try:
        caballos = importer("Caballos", client)
        # Las cuotas salen de simular la carrera `ODDS_TRIALS` (80.000) veces, ~0,7 s. Aquí
        # basta con unas pocas: la prueba apunta una apuesta acertada, no mira la cuota.
        monkeypatch.setattr(caballos, "estimate", lambda card, rng: estimate(card, rng, 2_000))
        cog = client.get_cog("Caballos")
        cog.renderer = MagicMock()
        cog.renderer.card = AsyncMock(return_value=b"PNG")
        cog.renderer.ticket = AsyncMock(return_value=b"PNG")
        cog.renderer.race = AsyncMock(
            return_value=caballos.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        cog.sleep = AsyncMock()
        cog.start = MagicMock()
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 77
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        await cog._caballo_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=channel,
            user=owner,
            amount_text="100",
            pick_text="1",
            kind_text=None,
            confirm=AsyncMock(),
            send_error=AsyncMock(),
        )
        race = cog.races[77]
        times = tuple(100.0 + i for i in range(race.card.size))
        splits = tuple(tuple(t * s / SEGMENTS for s in range(SEGMENTS + 1)) for t in times)
        result = RaceResult(order=tuple(range(race.card.size)), times=times, splits=splits)
        monkeypatch.setattr(caballos, "run_race", lambda card, rng: result)

        await race.race()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["horse_bets"] == 1
        assert profile.stats["horse_hits"] == 1
        assert {"caballo_1", "caballo_hit_1"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["caballos"].plays == 1
    finally:
        await client.close()


async def test_el_bacara_apunta_sus_logros_y_jugadas_con_el_bot_real(tmp_path: Path) -> None:
    """El bacará carga antes que los logros y `apuestas`: cada mano jugada debe llegar."""
    from bot.services.bacara import Coup
    from bot.services.blackjack import Card

    client = await load_bot(tmp_path)
    try:
        bacara = importer("Bacará", client)
        cog = client.get_cog("Bacará")
        cog.ahead = False
        cog.renderer = MagicMock()
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        cog.renderer.coup = AsyncMock(
            return_value=bacara.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        cog.sleep = AsyncMock()
        cog.start = MagicMock()
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 78
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        await cog._bacara_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=channel,
            user=owner,
            amount_text="100",
            side_text="banca",
            confirm=AsyncMock(),
            send_error=AsyncMock(),
        )
        table = cog.tables[78]
        # Banca 9 de salida contra Jugador 3: natural de 9 para la Banca.
        table.state.pending = Coup((Card(1, 0), Card(2, 1)), (Card(4, 2), Card(5, 3)))

        await table.deal()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["bac_hands"] == 1
        assert profile.stats["bac_banker_wins"] == 1
        assert profile.stats["bac_nine_wins"] == 1
        assert {"bac_1", "bac_natural", "bac_nueve", "bac_estreno"} <= set(profile.unlocked)
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["bacara"].plays == 1
    finally:
        await client.close()


async def test_auto_de_los_botes_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """Las máquinas de Botes cargan antes que los logros: sus tiradas deben llegar."""
    client = await load_bot(tmp_path)
    await client.hold_win.initialize()
    try:
        botes = importer("Botes", client)
        botes.REVEAL_MARGIN_SECONDS = 0
        botes.BONUS_INTRO_PAUSE = 0
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        cog = client.get_cog("Botes")
        view = botes.HoldWinView(
            cog, theme=botes.THEMES["volcan"], guild_id=GUILD_ID, owner=owner, stake=10
        )
        view.message = None
        cog.ahead = False  # pintar por adelantado gastaría las tiradas del Auto
        cog.renderer = MagicMock()
        cog.renderer.still = AsyncMock(return_value=b"PNG")
        cog.renderer.bonus_intro = AsyncMock(return_value=Media(gif=b"", png=b"PNG", seconds=0.0))
        cog.renderer.close = AsyncMock()
        interaction = fake_interaction()
        interaction.user = owner

        await view._auto(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["botes_spins"] == view.session_spins
        assert profile.stats["botes_spins_volcan"] == view.session_spins
        assert profile.stats["botes_auto"] == 1
        assert "botes_1" in profile.unlocked
    finally:
        await client.close()


async def switch_setup(client: BotClient, *, to_botes: bool, stake: int):  # noqa: ANN201
    """Una máquina abierta (la clásica o el Mina) y la interacción que elige otra en su 🔀."""
    await client.hold_win.initialize()
    slots = importer("Tragaperras", client)
    botes = importer("Botes", client)
    slots.REVEAL_MARGIN_SECONDS = 0
    slots_cog, botes_cog = client.get_cog("Tragaperras"), client.get_cog("Botes")
    for cog in (slots_cog, botes_cog):
        cog.ahead = False
        cog.renderer = MagicMock()
        cog.renderer.still = AsyncMock(return_value=b"PNG")
        cog.renderer.close = AsyncMock()
    owner = MagicMock(spec=discord.Member)
    owner.id, owner.display_name, owner.bot = OWNER_ID, "Diego", False
    owner.mention = f"<@{OWNER_ID}>"
    if to_botes:
        old = slots.SlotMachineView(slots_cog, guild_id=GUILD_ID, owner=owner, stake=stake)
    else:
        old = botes.HoldWinView(
            botes_cog, theme=botes.THEMES["mina"], guild_id=GUILD_ID, owner=owner, stake=stake
        )
    old.cog.machines.add(old)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    interaction = fake_interaction(owner)
    interaction.guild = guild
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.channel.send = AsyncMock()
    interaction.message = MagicMock()
    return slots, botes, old, interaction


async def pick_machine(old, key: str, interaction) -> None:  # noqa: ANN001
    """Pulsa 🔀 y elige `key` en el desplegable, como haría el dueño."""
    await old._open_picker(MagicMock(response=fake_interaction().response))
    old.picker._values = [key]
    await old._pick(interaction)


@pytest.mark.parametrize(
    ("origin", "target"), [("clasica", "volcan"), ("clasica", "olimpo"), ("mina", "clasica")]
)
async def test_otras_tragaperras_cambia_de_maquina_con_el_bot_real(
    tmp_path: Path, origin: str, target: str
) -> None:
    """🔀 abre la otra máquina en el mismo mensaje con la misma apuesta y retira la vieja."""
    client = await load_bot(tmp_path)
    try:
        slots, botes, old, interaction = await switch_setup(
            client, to_botes=origin == "clasica", stake=50
        )
        assert old.machine_key == origin
        old_cog = old.cog
        new_cog = client.get_cog("Botes" if target != "clasica" else "Tragaperras")

        await pick_machine(old, target, interaction)

        # El mensaje se edita (no hay mensaje nuevo) con la vista de la máquina elegida.
        interaction.response.defer.assert_awaited_once()
        assert all("view" not in call.kwargs for call in interaction.channel.send.await_args_list)
        kwargs = interaction.edit_original_response.await_args.kwargs
        new = kwargs["view"]
        expected = botes.HoldWinView if target != "clasica" else slots.SlotMachineView
        assert isinstance(new, expected)
        assert new.machine_key == target
        assert len(kwargs["attachments"]) == 1
        # La vieja está parada, fuera de su cog; la nueva, en el del otro.
        assert old.is_finished()
        assert old not in old_cog.machines
        assert new in new_cog.machines
        assert not new.is_finished()
        assert new.stake == 50
        assert new.message is interaction.message
        # Y cuenta como cambio de máquina.
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["slots_switches"] == 1
        assert profile.stats[f"slots_switch_to_{target}"] == 1
        assert "slots_switch_1" in profile.unlocked
    finally:
        await client.close()


async def test_la_clasica_con_apuesta_menor_que_la_minima_de_botes_se_abre_con_la_de_defecto(
    tmp_path: Path,
) -> None:
    """Con 5 la clásica cambia a Botes (mínima 10): se abre con la apuesta por defecto."""
    client = await load_bot(tmp_path)
    try:
        slots, botes, old, interaction = await switch_setup(client, to_botes=True, stake=5)

        await pick_machine(old, "volcan", interaction)

        new = interaction.edit_original_response.await_args.kwargs["view"]
        assert isinstance(new, botes.HoldWinView)
        assert new.stake == botes.DEFAULT_STAKE != 5
        assert old.is_finished()
        assert new in client.get_cog("Botes").machines
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert "slots_switch_1" in profile.unlocked
    finally:
        await client.close()


async def test_si_la_otra_maquina_no_se_abre_la_vieja_sigue_y_no_hay_logro(
    tmp_path: Path,
) -> None:
    """Sin el cog de Botes cargado: avisa en privado y la máquina vieja se queda como estaba."""
    client = await load_bot(tmp_path)
    try:
        slots, botes, old, interaction = await switch_setup(client, to_botes=True, stake=5)
        await client.remove_cog("Botes")

        await pick_machine(old, "volcan", interaction)

        assert not old.is_finished()
        assert old in old.cog.machines
        assert not old.picking
        interaction.followup.send.assert_awaited()
        assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
        shown = interaction.edit_original_response.await_args.kwargs
        assert shown["view"] is old
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats.get("slots_switches", 0) == 0
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("word", "key"), [("volcán", "volcan"), ("mina", "mina"), ("olimpo", "olimpo")]
)
async def test_tragas_abre_cada_maquina_de_botes_con_el_bot_real(
    tmp_path: Path, word: str, key: str
) -> None:
    """`tragas` abre el Volcán, la Mina y el Olimpo por el cog de Botes, que el bot carga aparte."""
    client = await load_bot(tmp_path)
    await client.hold_win.initialize()
    try:
        botes = importer("Botes", client)
        client.get_cog("Botes").ahead = False
        client.get_cog("Botes").renderer = MagicMock()
        client.get_cog("Botes").renderer.still = AsyncMock(return_value=b"PNG")
        client.get_cog("Botes").renderer.close = AsyncMock()
        tragas = client.get_cog("Tragaperras")
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.display_name, owner.bot = OWNER_ID, "Diego", False
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD_ID)
        ctx.author = owner
        ctx.channel = MagicMock(id=5)
        ctx.send = AsyncMock(return_value=MagicMock())

        await tragas.slots_text.callback(tragas, ctx, word, "50")

        view = ctx.send.await_args.kwargs["view"]
        assert isinstance(view, botes.HoldWinView)
        assert view.theme.key == key and view.stake == 50
    finally:
        await client.close()


async def test_bizum_apunta_a_espaldas_de_sanchez_con_el_bot_real(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        bizum = importer("Bizum", client)
        assert bizum.logros._cog(client) is client.get_cog("Achievements")
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(50_000, "test")])
        sender = MagicMock(spec=discord.Member)
        sender.id, sender.bot, sender.display_name = OWNER_ID, False, "Diego"
        receiver = MagicMock(spec=discord.Member)
        receiver.id, receiver.bot, receiver.display_name = 20, False, "Ana"
        receiver.mention = "<@20>"
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD)
        ctx.author = sender
        ctx.channel = None
        ctx.send = AsyncMock()

        cog = client.get_cog("Bizum")
        await cog.bizum_text.callback(cog, ctx, receiver, "10001")

        mine = await client.achievements.profile(GUILD, OWNER_ID)
        theirs = await client.achievements.profile(GUILD, 20)
        assert "bizum_espaldas" in mine.unlocked
        assert theirs.stats["bizum_received"] == 10_001
    finally:
        await client.close()


async def test_la_lista_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """`lista` carga antes que los logros: apuntar y tachar deben llegar a ellos."""
    client = await load_bot(tmp_path)
    await client.todo.initialize()
    try:
        lista = client.get_cog("Lista")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        owner.guild_permissions = discord.Permissions.none()
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        guild.get_member = MagicMock(return_value=owner)
        owner.guild = guild
        ctx = MagicMock()
        ctx.guild = guild
        ctx.author = owner
        ctx.message.delete = AsyncMock()
        ctx.channel = MagicMock(spec=discord.TextChannel)
        ctx.channel.send = AsyncMock(return_value=MagicMock(id=5, channel=MagicMock(id=6)))

        await lista.lista_text.callback(lista, ctx, tarea="probar la lista prioridad alta")
        (task,) = await client.todo.list_tasks(GUILD_ID)
        interaction = fake_interaction()
        interaction.guild = guild
        interaction.user = owner
        interaction.channel = ctx.channel
        await lista.complete_from_menu(interaction, [task.id])

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["todo_added"] == 1
        assert profile.stats["todo_done"] == 1
        assert {"todo_add_1", "todo_done_1"} <= set(profile.unlocked)
    finally:
        await client.close()


async def test_cambios_configura_el_aviso_que_publica_el_despliegue_con_el_bot_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cambios` (cog Admin) guarda los ajustes y el cog Despliegue los usa al publicar."""
    client = await load_bot(tmp_path)
    await client.news.initialize()
    try:
        elegido = MagicMock(spec=discord.TextChannel)
        elegido.id = 77
        elegido.send = AsyncMock()
        elegido.mention = "#anuncios"
        general = MagicMock(spec=discord.TextChannel)
        general.name = "chat-general"
        general.send = AsyncMock()
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        guild.text_channels = [general, elegido]
        guild.get_channel_or_thread = lambda channel_id: elegido if channel_id == 77 else None
        responder = MagicMock()
        responder.guild = guild
        responder.send = AsyncMock()
        responder.send_error = AsyncMock()
        admin = client.get_cog("Admin")

        await admin._cambios_impl(responder, "resumen", 77)

        responder.send_error.assert_not_awaited()
        deploy = client.get_cog("Despliegue")
        buzon = tmp_path / "buzon"
        buzon.mkdir()
        (buzon / "novedades.txt").write_text(
            "#5\tyeyo/pollo\ttitulo\tPollo más rápido\n", encoding="utf-8"
        )
        deploy.mailbox = importer("Despliegue", client).Mailbox(buzon)
        deploy._fetch = AsyncMock(return_value=None)
        monkeypatch.setattr(type(client), "guilds", property(lambda self: [guild]))

        assert await deploy.announce_news() is True

        general.send.assert_not_awaited()
        embeds = elegido.send.await_args.kwargs["embeds"]
        assert len(embeds) == 1, "en resumen no van fichas"
        assert "Pollo más rápido" in embeds[0].description
    finally:
        await client.close()


async def test_un_turno_de_pala_apunta_sus_logros_con_el_bot_real(tmp_path: Path) -> None:
    """`pala` carga antes que los logros: el turno y la nómina deben llegar a ellos."""
    client = await load_bot(tmp_path)
    try:
        work = importer("Trabajo", client)
        assert work.logros._cog(client) is client.get_cog("Achievements")
        cog = client.get_cog("Trabajo")
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD)
        ctx.author = owner
        ctx.channel = None
        ctx.send = AsyncMock(return_value=MagicMock())
        await cog.pala_text.callback(cog, ctx)
        (panel,) = cog.panels

        def interaction() -> MagicMock:
            fake = fake_interaction()
            fake.user = owner
            fake.message = MagicMock()
            fake.message.edit = AsyncMock()
            return fake

        await panel._hire(interaction(), "obra")
        await panel._clock_in(interaction())
        await panel._finish(None)

        profile = await client.achievements.profile(GUILD, OWNER_ID)
        assert profile.stats["work_shifts"] == 1
        assert profile.stats["work_payslips"] == 1
        assert "pala_1" in profile.unlocked and "primera_nomina" in profile.unlocked
    finally:
        await client.close()


async def test_un_turno_del_negocio_llega_a_logros_saldo_y_perfil_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El negocio carga después de la pala y antes que los logros: todo debe encontrarse."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Negocio")
        work = client.get_cog("Trabajo")
        casino = client.get_cog("Casino")
        assert cog is not None and work is not None and casino is not None
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(2_000_000, "x")])
        await cog.service.open(
            GUILD, OWNER_ID, sector="cafeteria", barrio="triana", name="Bar Pepe", palette=0,
            emblem=0,
        )  # fmt: skip
        await cog.service.buy(GUILD, OWNER_ID, "cafe", 5)

        def interaction() -> MagicMock:
            fake = fake_interaction()
            fake.user = owner
            fake.guild_id = GUILD
            fake.message = MagicMock()
            fake.message.edit = AsyncMock()
            return fake

        module = importer("Negocio", client)
        assert module.logros._cog(client) is client.get_cog("Achievements")
        panel = module.NegocioPanel(cog, guild_id=GUILD, owner=owner, channel=None)
        cog.panels.add(panel)
        await panel._start(interaction())
        assert panel.turn is not None
        await panel._finish(None)

        profile = await client.achievements.profile(GUILD, OWNER_ID)
        assert profile.stats["biz_turns"] == 1
        assert "biz_turns_1" in profile.unlocked
        # `saldo` ve el local de esta noche y `perfil`, el negocio.
        items = await casino._pending(GUILD, OWNER_ID, 0)
        assert any(item.kind == "business_rent" for item in items)
        embed = await work.work_embed(GUILD, owner)
        assert any(field.name == "🏪 Negocio" for field in embed.fields)
        # Y la pala sabe cuántas franjas ha usado el negocio.
        agenda = await client.work.agenda(GUILD, OWNER_ID)
        assert agenda.business == 1
    finally:
        await client.close()


async def test_la_sociedad_del_negocio_sale_en_el_patrimonio_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`patrimonio` encuentra el cog del negocio y suma el valor de la sociedad."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Negocio")
        patrimonio = client.get_cog("Patrimonio")
        assert cog is not None and patrimonio is not None
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(2_000_000, "x")])
        await cog.service.open(
            GUILD, OWNER_ID, sector="tienda", barrio="triana", name="Pino", palette=0,
            emblem=0,
        )  # fmt: skip
        await cog.service.constitute(GUILD, OWNER_ID, 300_000)
        worth = next(w for w in await patrimonio.fortunes(GUILD) if w.user_id == OWNER_ID)
        assert worth.companies >= 300_000  # la caja (el capital) más la obra por amortizar
    finally:
        await client.close()


async def test_los_intereses_encuentran_los_logros_con_el_bot_real(tmp_path: Path) -> None:
    """`Intereses` carga antes que los logros: sus avisos deben llegar al cog real."""
    client = await load_bot(tmp_path)
    try:
        module = importer("Intereses", client)
        assert module.logros._cog(client) is client.get_cog("Achievements")
    finally:
        await client.close()


async def test_el_aviso_de_intereses_llega_a_los_juegos_por_la_renta(tmp_path: Path) -> None:
    """`renta.hint` es el hueco por el que todos los juegos cuentan los intereses."""
    client = await load_bot(tmp_path)
    try:
        client.get_cog("Intereses").hint_for = AsyncMock(return_value="🏦 Ayer cobraste")
        for game in GAMES:
            module = importer(game, client)
            assert await module.renta.hint(client, GUILD_ID, OWNER_ID) == "🏦 Ayer cobraste", game
        renta_cog = client.get_cog("Renta")
        renta_cog.hint_for = AsyncMock(return_value="📬 Renta")
        hint = await importer("Casino", client).renta.hint(client, GUILD_ID, OWNER_ID)
        assert hint == "📬 Renta\n🏦 Ayer cobraste"
    finally:
        await client.close()


async def test_usar_un_objeto_apunta_logros_a_los_dos_con_el_bot_real(tmp_path: Path) -> None:
    """Tirar un huevo desde la mochila llega a los logros de quien tira y de quien lo recibe."""
    from bot.services.shop import quote

    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        egg = next(i for i in await client.shop.items(GUILD_ID) if i.catalog_key == "huevo")
        price = quote(egg, tienda.clock())
        await client.economy.purchase(
            GUILD_ID, OWNER_ID, base=price.base, tax=price.tax, concept="objeto",
            reserve=client.shop.reserve(
                GUILD_ID, OWNER_ID, egg.id, expected=price, level=0, now=tienda.clock()
            ),
        )  # fmt: skip

        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        victim = MagicMock(spec=discord.Member)
        victim.id, victim.bot, victim.mention = 11, False, "<@11>"
        backpack = await tienda.backpack_view(guild, owner, owner)
        (entry,) = backpack.entries
        interaction = fake_interaction(owner)
        interaction.channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.send = AsyncMock()
        await tienda.perform_use(interaction, backpack, entry, tienda_use("huevo"), target=victim)

        thrower = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert thrower.stats["shop_uses"] == 1 and thrower.stats["shop_used_huevo"] == 1
        assert {"use_1", "target_1"} <= set(thrower.unlocked)
        target = await client.achievements.profile(GUILD_ID, 11)
        assert target.stats["shop_got_hit"] == 1 and "hit_1" in target.unlocked
    finally:
        await client.close()


def tienda_use(key: str):  # noqa: ANN201
    from bot.services.shop_uses import USES

    return USES[key]


async def test_las_mascotas_llegan_a_todos_con_el_bot_real(tmp_path: Path) -> None:
    """Niveles, Renta, tienda, IMV, logros y cumpleaños cargan antes o después de
    `bot.cogs.pets`: todos tienen que encontrar la mascota activa."""
    from bot.services.pets import BOND_LEVELS, Event, Moment, PetState

    client = await load_bot(tmp_path)
    try:
        pets = client.get_cog("Mascotas")
        assert pets is not None
        pets.active[(GUILD_ID, OWNER_ID)] = PetState(
            id=1, guild_id=GUILD_ID, user_id=OWNER_ID, species="perro", name="Toby",
            bond=BOND_LEVELS[4],
        )  # fmt: skip
        loud = Moment(Event.BIG_WIN)  # sonado y sin apariciones
        for cog_name in ("MessageStats", "Renta", "Tienda", "Casino", "Achievements",
                         "Birthdays"):  # fmt: skip
            module = importer(cog_name, client)
            assert module.mascotas.xp_bonus(client, GUILD_ID, OWNER_ID, voice=False) > 1.0
            line = await module.mascotas.cameo(client, GUILD_ID, OWNER_ID, loud)
            assert line is not None and "**Toby**" in line, cog_name
        # Los juegos llegan a la mascota a través de la Renta.
        client.get_cog("Renta").hint_for = AsyncMock(return_value=None)
        for game in GAMES:
            module = importer(game, client)
            hint = await module.renta.hint(client, GUILD_ID, OWNER_ID, loud)
            assert hint is not None and "**Toby**" in hint, game
    finally:
        await client.close()


async def test_la_rueda_llega_a_la_tienda_los_niveles_y_la_mascota_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`rueda` carga antes que la tienda, las mascotas y los logros: sus puentes los encuentran."""
    from bot.services.pets import PetState
    from bot.services.rueda_catalog import PIECES, TITLE_KEYS, TITLE_STOCK

    client = await load_bot(tmp_path)
    try:
        module = importer("Rueda", client)
        item = await module.tienda.give_key(client, GUILD_ID, OWNER_ID, PIECES[0].key)
        assert item is not None and item.catalog_key == PIECES[0].key
        given = await module.tienda.give_numbered(client, GUILD_ID, OWNER_ID, TITLE_KEYS[0])
        assert given is not None and given[1:] == (1, TITLE_STOCK)
        assert await module.tienda.give_numbered(client, GUILD_ID, OWNER_ID, TITLE_KEYS[0]) is None
        left = await module.tienda.units_left(client, GUILD_ID, TITLE_KEYS)
        assert left[TITLE_KEYS[0]] == TITLE_STOCK - 1 and left[TITLE_KEYS[1]] == TITLE_STOCK
        assert await module.niveles.levels_enabled(client, GUILD_ID) is False
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot = OWNER_ID, False
        assert await module.niveles.give_xp(client, guild, owner, None, 250) is False
        client.get_cog("Mascotas").active[(GUILD_ID, OWNER_ID)] = PetState(
            id=1, guild_id=GUILD_ID, user_id=OWNER_ID, species="hamster", name="", bond=0
        )
        assert module.mascotas.active_species(client, GUILD_ID, OWNER_ID) == "hamster"
        client.get_cog("Renta").hint_for = AsyncMock(return_value="RENTA")
        assert "RENTA" in (await module.renta.hint(client, GUILD_ID, OWNER_ID) or "")
    finally:
        await client.close()


async def test_la_pala_ve_las_herramientas_de_la_mochila_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`pala` carga antes que la tienda: su puente tiene que encontrar la mochila."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        items = await client.shop.items(GUILD_ID)
        casco = next(item for item in items if item.catalog_key == "casco_linterna")
        await client.shop.grant(GUILD_ID, OWNER_ID, casco, 0.0)
        work = importer("Trabajo", client)
        owned = await work.tienda.owned_keys(client, GUILD_ID, OWNER_ID)
        assert "casco_linterna" in owned
    finally:
        await client.close()


async def test_la_pala_da_los_regalos_de_la_mochila_con_el_bot_real(tmp_path: Path) -> None:
    """El regalo se compra en la tienda y `pala` lo gasta por su puente."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        items = await client.shop.items(GUILD_ID)
        flores = next(item for item in items if item.catalog_key == "flores_mama")
        await client.shop.grant(GUILD_ID, OWNER_ID, flores, 0.0)
        work = importer("Trabajo", client)
        cog = client.get_cog("Trabajo")
        await cog.service.hire(GUILD_ID, OWNER_ID, "obra")

        async def consume() -> bool:
            return await work.tienda.consume_key(client, GUILD_ID, OWNER_ID, "flores_mama")

        await cog.service.give_gift(GUILD_ID, OWNER_ID, "flores_mama", consume)
        assert "flores_mama" not in await work.tienda.owned_keys(client, GUILD_ID, OWNER_ID)
        assert not await work.tienda.consume_key(client, GUILD_ID, OWNER_ID, "flores_mama")
    finally:
        await client.close()


async def test_la_pala_compra_y_da_un_regalo_en_el_panel_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Un regalo que no tienes se compra y se da desde el desplegable, sin salir del panel."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Trabajo")
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD)
        ctx.author = owner
        ctx.channel = None
        ctx.send = AsyncMock(return_value=MagicMock())
        await cog.pala_text.callback(cog, ctx)
        (panel,) = cog.panels

        def interaction() -> MagicMock:
            fake = fake_interaction(owner)
            fake.guild = MagicMock(spec=discord.Guild)
            fake.guild.id = GUILD_ID
            fake.message = MagicMock()
            fake.message.edit = AsyncMock()
            return fake

        await panel._hire(interaction(), "obra")
        money = await client.economy.grant(GUILD_ID, OWNER_ID, amount=10_000, reason="prueba")
        await panel._buy_gift(interaction(), "flores_mama")
        assert panel.checkout is not None
        await panel._pay_gift(interaction())

        status = await cog.service.status(GUILD_ID, OWNER_ID)
        assert "flores_mama" in status.contract.family_used  # dado
        after = await client.economy.balance(GUILD_ID, OWNER_ID)
        assert after < money  # cobrado (los logros de la compra devuelven algún premio)
        assert "flores_mama" not in await importer("Trabajo", client).tienda.owned_keys(
            client, GUILD_ID, OWNER_ID
        )
        await panel._pay_gift(interaction())  # el segundo clic no cobra otra vez
        assert await client.economy.balance(GUILD_ID, OWNER_ID) == after
    finally:
        await client.close()


async def test_perfil_fortunas_y_ranking_ven_el_titulo_con_el_bot_real(tmp_path: Path) -> None:
    """Los tres cogs cargan antes que la tienda: su puente tiene que ver el título puesto."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "verificado")
        assert item is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        await tienda.wear_title(GUILD_ID, OWNER_ID, "verificado")
        for cog_name in ("Perfil", "Patrimonio", "MessageStats"):
            module = importer(cog_name, client)
            assert module.tienda.title_of(client, GUILD_ID, OWNER_ID) == "☑️ Cuenta verificada"
    finally:
        await client.close()


async def test_vender_en_el_rastro_quita_el_titulo_y_apunta_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El Rastro carga después de la tienda y antes de los logros: los dos puentes valen."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        rastro = client.get_cog("Rastro")
        module = importer("Rastro", client)
        assert module.logros._cog(client) is client.get_cog("Achievements")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "verificado")
        assert item is not None
        entry, _dupe = await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        await tienda.wear_title(GUILD_ID, OWNER_ID, "verificado")
        await client.economy.repository.apply(GUILD, 20, [LedgerEntry(100_000, "test")])
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        seller = MagicMock(spec=discord.Member)
        seller.id, seller.bot, seller.display_name = OWNER_ID, False, "Diego"
        buyer = MagicMock(spec=discord.Member)
        buyer.id, buyer.bot, buyer.display_name, buyer.mention = 20, False, "Ana", "<@20>"
        guild.get_member = MagicMock(side_effect={OWNER_ID: seller, 20: buyer}.get)
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        entries = await client.shop.inventory(GUILD_ID, OWNER_ID, rastro.clock())
        view = module.OfferView(
            rastro, GUILD_ID, seller, buyer, next(e for e in entries if e.id == entry.id), 500
        )
        interaction = fake_interaction(buyer)
        interaction.client = client
        interaction.guild = guild
        interaction.channel = None

        await view.accept.callback(interaction)

        assert tienda.title(GUILD_ID, OWNER_ID) is None
        mine = await client.achievements.profile(GUILD, OWNER_ID)
        theirs = await client.achievements.profile(GUILD, 20)
        assert mine.stats["market_sold"] == 1
        assert theirs.stats["market_bought"] == 1
        assert "rastro_itp_1" in theirs.unlocked
    finally:
        await client.close()


async def test_un_puesto_del_rastro_avisa_a_quien_lo_desea_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El Rastro avisa a la tienda al montar un puesto (`shop.wish_listed`) y la ficha de
    la tienda ve el puesto (`rastro.floor`): los dos puentes llegan al cog de verdad."""
    from bot.cogs.shop_front import ItemSheet

    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        rastro = client.get_cog("Rastro")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "verificado")
        assert item is not None
        entry, _dupe = await client.shop.grant(GUILD_ID, OTHER_ID, item, 0.0)
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        guild.name = "Servidor"
        seller = MagicMock(spec=discord.Member)
        seller.id, seller.bot, seller.display_name = OTHER_ID, False, "Ana"
        fan = MagicMock(spec=discord.Member)
        fan.id, fan.bot, fan.display_name, fan.roles = OWNER_ID, False, "Diego", []
        fan.guild = guild
        fan.send = AsyncMock()
        guild.get_member = MagicMock(side_effect={OWNER_ID: fan, OTHER_ID: seller}.get)
        guild.get_role = MagicMock(return_value=None)
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        await tienda.toggle_wish(GUILD_ID, fan, item, None)
        entries = await client.shop.inventory(GUILD_ID, OTHER_ID, rastro.clock())
        stall = next(e for e in entries if e.id == entry.id)

        _text, embed, _view = await rastro._open_stall(guild, None, seller, stall, 300)
        # El aviso va aparte para no hacer esperar a quien vende.
        await asyncio.gather(*list(tienda._tasks))

        assert embed is not None
        fan.send.assert_awaited_once()
        assert "Ana" in fan.send.await_args.args[0]
        sheet = await ItemSheet.build(tienda, guild, fan, item.id)
        assert sheet is not None and sheet.stall is not None and sheet.stall[0] == 300
    finally:
        await client.close()


async def test_la_ruleta_pinta_con_la_skin_de_la_tienda_con_el_bot_real(tmp_path: Path) -> None:
    """El casino carga antes que la tienda: su puente tiene que ver la skin puesta."""
    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "skin_ruleta_granate")
        assert item is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        casino = client.get_cog("Casino")
        assert casino.skin(GUILD_ID, OWNER_ID)["key"] == "serie"
        await tienda.wear_skin(GUILD_ID, OWNER_ID, "ruleta", SKIN_BY_KEY["ruleta_granate"])
        assert casino.skin(GUILD_ID, OWNER_ID)["key"] == "ruleta_granate"
        # Los logros piden las skins de regalo a la tienda del bot cargado.
        shop_module = importer("Tienda", client)
        assert await shop_module.grant_reward_skins(client, GUILD_ID, OWNER_ID, ["x"]) == []
    finally:
        await client.close()


async def test_la_imagen_de_pieles_llega_a_las_mesas_y_a_los_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`pieles` carga después de la tienda y los juegos antes: todos ven la misma imagen."""
    import io

    from PIL import Image

    client = await load_bot(tmp_path)
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        item = await client.shop.item_by_key(GUILD_ID, "skin_tapete_propio")
        assert item is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
        await tienda.wear_skin(GUILD_ID, OWNER_ID, "ruleta", SKIN_BY_KEY["tapete_propio"])
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        buffer = io.BytesIO()
        Image.new("RGB", (64, 36), "purple").save(buffer, format="PNG")
        upload = MagicMock(spec=discord.Attachment)
        upload.size = len(buffer.getvalue())
        upload.read = AsyncMock(return_value=buffer.getvalue())

        text = await client.get_cog("Pieles").upload(GUILD_ID, owner, owner, upload, None)

        assert "🖼️" in text
        skin = client.get_cog("Casino").skin(GUILD_ID, OWNER_ID)
        assert str(skin["key"]).startswith("tapete_propio:")
        assert str(skin["image"]).startswith("data:image/jpeg;base64,")
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["shop_skin_image_uploads"] == 1
        assert "skin_image_1" in profile.unlocked
    finally:
        await client.close()


async def test_los_gallos_apuntan_logros_y_jugadas_y_reciben_sus_skins_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Los gallos cargan antes que la tienda y los logros: sus puentes tienen que verlos."""
    client = await load_bot(tmp_path)
    try:
        gallos = importer("Gallos", client)
        gallos.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Gallos")
        cog.renderer = MagicMock()
        cog.renderer.still = AsyncMock(return_value=b"PNG")
        cog.renderer.fight = AsyncMock(
            return_value=gallos.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        )
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._gallos_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            rival=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        # Al abrir el reñidero, la tienda le da los gallos gratis.
        mochila = {e.catalog_key for e in await client.shop.inventory(GUILD_ID, OWNER_ID, 0.0)}
        assert {"skin_gallo_blanco", "skin_gallo_negro", "skin_gallo_pinto"} <= mochila
        tienda = client.get_cog("Tienda")
        await tienda.wear_skin(GUILD_ID, OWNER_ID, "gallos", SKIN_BY_KEY["gallo_negro"])
        (panel,) = cog.panels
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await panel._fight(interaction)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["gallos_fights"] == 1
        assert profile.stats["gallos_skin_gallo_negro"] == 1
        assert "gallos_1" in profile.unlocked
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["gallos"].plays == 1
    finally:
        await client.close()


RIVAL_ID = 11
#: Las rojas (que salen) ganan en horizontal con su cuarta ficha.
ENRAYA_WIN = [0, 0, 1, 1, 2, 2, 3]


def enraya_member(user_id: int, name: str) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = name
    member.mention = f"<@{user_id}>"
    member.bot = False
    return member


def enraya_cog_with_fake_drawing(client: BotClient):  # noqa: ANN201
    """El 4 en raya del bot real con dibujo falso, sin esperas y sin pintar por adelantado."""
    module = importer("Enraya", client)
    module.REVEAL_MARGIN_SECONDS = 0
    cog = client.get_cog("Enraya")
    cog.ahead = False
    cog.renderer = MagicMock()
    cog.renderer.still = AsyncMock(return_value=b"PNG")
    cog.renderer.animate = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    cog.renderer.close = AsyncMock()
    return cog


def enraya_interaction(member: MagicMock) -> MagicMock:
    interaction = fake_interaction()
    interaction.user = member
    interaction.guild = None
    return interaction


async def test_un_duelo_de_4_en_raya_apunta_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El 4 en raya carga antes que los logros y `apuestas`: sus puentes tienen que verlos."""
    client = await load_bot(tmp_path)
    try:
        cog = enraya_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        rival = enraya_member(RIVAL_ID, "Ana")
        duel = await cog._enraya_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            rival=rival,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        assert duel is not None
        await duel.accept(enraya_interaction(rival))
        for column in ENRAYA_WIN:
            member = duel.member(duel.board.turn)
            await duel.drop(enraya_interaction(member), column)
        assert duel.board.winner is not None

        winner, loser = duel.member(duel.board.winner), duel.member(duel.board.winner.other)
        for member in (winner, loser):
            profile = await client.achievements.profile(GUILD_ID, member.id)
            assert profile.stats["enraya_games"] == 1
            assert profile.stats["enraya_duels"] == 1
            assert profile.stats["enraya_casino_duels"] == 1
            assert "enraya_1" in profile.unlocked
            report = await client.casino_stats.report(
                GUILD_ID, member.id, since=None, today=date(2026, 10, 6)
            )
            assert report.by_game["enraya"].plays == 1
        won = await client.achievements.profile(GUILD_ID, winner.id)
        lost = await client.achievements.profile(GUILD_ID, loser.id)
        assert won.stats["enraya_wins"] == 1 and "enraya_win_1" in won.unlocked
        assert lost.stats["enraya_losses"] == 1 and "enraya_wins" not in lost.stats
        assert won.stats["enraya_win_h"] == 1
    finally:
        await client.close()


async def test_una_partida_contra_jovani_apunta_logros_y_la_ficha_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Sin dinero de por medio: los logros y la ficha cuentan igual, `apuestas` no."""
    client = await load_bot(tmp_path)
    try:
        cog = enraya_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        await cog._enraya_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text=None,
            rival=None,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        (panel,) = cog.panels
        panel.board = Board()
        for column in ENRAYA_WIN[:-1]:
            panel.board = panel.board.play(column)
        panel.rebuild()
        interaction = enraya_interaction(owner)

        button = next(i for i in panel.children if i.custom_id == "enraya:col3")
        await button.callback(interaction)

        assert panel.over and panel.board.winner is Disc.RED
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["enraya_games"] == 1 and profile.stats["enraya_wins"] == 1
        assert "enraya_1" in profile.unlocked and "enraya_win_1" in profile.unlocked
        assert (await client.enraya.get(GUILD_ID, OWNER_ID)).wins == 1
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert "enraya" not in report.by_game
    finally:
        await client.close()


def backgammon_cog_with_fake_drawing(client: BotClient):  # noqa: ANN201
    """El backgammon del bot real con dibujo falso, sin esperas y sin pintar por adelantado."""
    module = importer("Backgammon", client)
    module.REVEAL_MARGIN_SECONDS = 0
    cog = client.get_cog("Backgammon")
    cog.ahead = False
    cog.renderer = MagicMock()
    cog.renderer.still = AsyncMock(return_value=b"PNG")
    cog.renderer.animate = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    cog.renderer.close = AsyncMock()
    return cog, module


async def test_un_duelo_de_backgammon_apunta_logros_y_jugadas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El backgammon carga antes que los logros y `apuestas`: sus puentes tienen que verlos."""
    client = await load_bot(tmp_path)
    try:
        cog, module = backgammon_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        rival = enraya_member(RIVAL_ID, "Ana")
        duel = await cog._backgammon_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            rival=rival,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        assert duel is not None
        duel.swap, duel.opening = False, (6, 1)
        await duel.accept(enraya_interaction(rival))
        # Las negras doblan y las blancas pasan: gana quien dobló.
        hand = duel.hand
        while not hand.done:
            step = hand.legal()[0]
            interaction = enraya_interaction(owner)
            interaction.data = {"values": [f"{step.source}:{step.target}"]}
            await duel.step(interaction, step.die)
        await duel.confirm(enraya_interaction(owner))
        await duel.double(enraya_interaction(rival))
        await duel.drop(enraya_interaction(owner))
        assert duel.phase is module.Phase.DONE

        for member in (owner, rival):
            profile = await client.achievements.profile(GUILD_ID, member.id)
            assert profile.stats["backgammon_games"] == 1
            assert profile.stats["backgammon_casino_duels"] == 1
            assert "bg_1" in profile.unlocked
            report = await client.casino_stats.report(
                GUILD_ID, member.id, since=None, today=date(2026, 10, 6)
            )
            assert report.by_game["backgammon"].plays == 1
        won = await client.achievements.profile(GUILD_ID, RIVAL_ID)
        lost = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert won.stats["backgammon_opp_drops"] == 1 and "bg_le_asustas" in won.unlocked
        assert won.stats["backgammon_doubles_offered"] == 1
        assert lost.stats["backgammon_drops"] == 1 and "backgammon_wins" not in lost.stats
        assert (await client.backgammon.get(GUILD_ID, RIVAL_ID)).wins == 1
    finally:
        await client.close()


async def test_una_partida_de_backgammon_contra_jovani_apunta_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Sin dinero de por medio: los logros y la ficha cuentan igual, `apuestas` no."""
    client = await load_bot(tmp_path)
    try:
        cog, module = backgammon_cog_with_fake_drawing(client)
        owner = enraya_member(OWNER_ID, "Diego")
        await cog._backgammon_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text=None,
            rival=None,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        (panel,) = cog.panels
        points = [0] * 24
        points[0], points[20] = 1, -14
        start = module.Position(tuple(points), (0, 0), (14, 1))
        panel.phase, panel.position, panel.origin = "play", start, start
        panel.hand = module.Hand(start, module.Side.WHITE, (1, 2))
        panel.hand.play(panel.hand.legal()[0])
        panel.rebuild()

        button = next(i for i in panel.children if i.custom_id == "backgammon:confirm")
        await button.callback(enraya_interaction(owner))

        assert panel.phase == "over"
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["backgammon_games"] == 1 and profile.stats["backgammon_wins"] == 1
        assert "bg_1" in profile.unlocked and "bg_win_1" in profile.unlocked
        assert (await client.backgammon.get(GUILD_ID, OWNER_ID)).wins == 1
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert "backgammon" not in report.by_game
    finally:
        await client.close()


async def test_la_torre_apunta_sus_logros_y_su_libro_con_el_bot_real(tmp_path: Path) -> None:
    """La Torre del dragón carga antes que los logros: sus partidas deben llegar a `logros`."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Torre")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._torre_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            difficulty=None,
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (board,) = cog.boards
        board.game.dragons = tuple(frozenset({0}) for _ in board.game.dragons)
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await board._climb(interaction, 0)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["tower_games"] == 1
        assert profile.stats["tower_burns"] == 1
        assert {"tower_1", "tower_burn_1", "tower_burn_first", "tower_easy_burn"} <= set(
            profile.unlocked
        )
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["torre"].plays == 1
    finally:
        await client.close()


async def test_allatu_apunta_sus_logros_y_su_libro_con_el_bot_real(tmp_path: Path) -> None:
    """Allá tú carga antes que los logros: sus partidas deben llegar a `logros` y al casino."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Allá tú")
        cog.ahead = False
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._allatu_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            table=None,
            send=AsyncMock(return_value=MagicMock(edit=AsyncMock())),
            send_error=AsyncMock(),
        )
        (board,) = cog.boards
        board.image = None  # el plató de texto: aquí no se mira ninguna imagen
        game = board.game

        def press() -> MagicMock:
            interaction = fake_interaction()
            interaction.user = owner
            interaction.guild = None
            return interaction

        await board._press_box(press(), 0)
        while game.offer is None:
            await board._press_box(press(), game.openable[0])
        await board._deal(press())

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["allatu_games"] == 1
        assert profile.stats["allatu_deals"] == 1
        assert profile.stats["allatu_day_last"] > 0
        assert {"allatu_1", "allatu_trato", "allatu_gallina"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["allatu"].plays == 1
    finally:
        await client.close()


async def test_la_guerra_apunta_sus_logros_y_su_libro_con_el_bot_real(tmp_path: Path) -> None:
    """La Guerra carga antes que los logros: sus manos deben llegar a `logros` y al casino."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Guerra")
        cog.new_shoe = lambda: [Card(7, 0)] * 10 + [Card(1, 0), Card(13, 0)]
        cog.ahead = False
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        message = MagicMock()
        message.edit = AsyncMock()  # el GIF y el resultado se editan sobre el mensaje enviado
        await cog._guerra_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            send=AsyncMock(return_value=message),
            send_error=AsyncMock(),
        )

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["war_hands"] == 1
        # Rey contra as: la banca gana y salta el secreto del rey sin corona.
        assert {"war_1", "war_king_lost"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["guerra"].plays == 1
    finally:
        await client.close()


async def test_una_porra_cuenta_las_jugadas_y_apunta_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """La jugada de minas llega a la porra por `apuestas.record` y el reparto, a los logros."""
    import asyncio
    from types import SimpleNamespace

    from bot.services.porras import Status

    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Porras")

        async def never(_seconds: float) -> None:
            await asyncio.Event().wait()

        cog.sleep = never
        people = {}
        for user_id, name in ((10, "Luis"), (20, "Ana"), (30, "Pepe"), (40, "Mari")):
            user = MagicMock(spec=discord.Member)
            user.id, user.bot, user.display_name, user.mention = (
                user_id,
                False,
                name,
                f"<@{user_id}>",
            )
            people[user_id] = user
        cog._member = lambda _guild, user_id: people.get(user_id)
        message = MagicMock()
        message.id = 99
        message.edit = AsyncMock()
        message.channel.send = AsyncMock()
        porra = await cog.open(
            guild=SimpleNamespace(id=GUILD_ID), channel=None, opener=people[10],
            subject=people[20], game_text="minas", prop_key="signo", plays=1,
            stake_text="100", send=AsyncMock(return_value=message), send_error=AsyncMock(),
        )  # fmt: skip
        table = cog.tables[porra.id]

        def press(user_id: int) -> MagicMock:
            inter = fake_interaction()
            inter.user, inter.channel, inter.guild_id = people[user_id], None, GUILD_ID
            inter.guild = SimpleNamespace(id=GUILD_ID)
            return inter

        await cog.answer(press(20), table, accepted=True)
        await cog.bet(press(30), table, 0, "200")
        await cog.bet(press(40), table, 1, "200")
        await cog.lock(table)

        mines = importer("Minas", client)
        await mines.apuestas.record(
            client, GUILD_ID, people[20], game="minas", stake=100, net=150,
            balance_after=1_150, tax=0, details=(("boom", 0),),
        )  # fmt: skip
        # El reparto va en otra tarea (el juego no lo espera): se espera a que acabe.
        for _ in range(300):
            stats = (await client.achievements.profile(GUILD_ID, 20)).stats
            if porra.status is Status.RESOLVED and "porra_pool_max" in stats:
                break
            await asyncio.sleep(0.01)
        assert porra.status is Status.RESOLVED

        winner = await client.achievements.profile(GUILD_ID, 30)
        loser = await client.achievements.profile(GUILD_ID, 40)
        subject = await client.achievements.profile(GUILD_ID, 20)
        assert winner.stats.get("porra_wins") == 1
        assert loser.stats.get("porra_losses") == 1
        assert subject.stats.get("porra_image", 0) > 0
        assert "porra_image_1" in subject.unlocked
        report = await client.casino_stats.report(GUILD_ID, 30, since=None, today=date(2026, 10, 6))
        assert report.total.plays == 1
    finally:
        for task in list(client.get_cog("Porras")._tasks):
            task.cancel()
        await client.close()


async def test_la_beernight_apunta_sus_logros_y_saca_la_mascota_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`beernight` carga antes que los logros: confesar y cerrar deben llegar a ellos, y la
    mascota activa tiene que poder brindar en sus mensajes."""
    from bot.services.beernight import Settings
    from bot.services.pets import BOND_LEVELS, Event, Moment, PetState

    client = await load_bot(tmp_path)
    await client.beernight.initialize()
    await client.beernight.save_settings(GUILD_ID, Settings(sound=False))
    try:
        beernight = client.get_cog("Beernight")
        module = importer("Beernight", client)
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        owner.voice = None
        owner.guild_permissions = discord.Permissions.none()
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        guild.get_member = MagicMock(return_value=owner)
        guild.voice_client = None
        owner.guild = guild
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 5
        channel.send = AsyncMock(return_value=MagicMock(id=6, channel=channel))
        channel.get_partial_message = MagicMock(return_value=MagicMock(edit=AsyncMock()))
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        client.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]

        def click() -> MagicMock:
            """Una pulsación nueva: cada botón trae su propia interacción."""
            interaction = fake_interaction(owner)
            interaction.guild = guild
            interaction.channel = channel
            interaction.client = client
            interaction.message = MagicMock(id=7, channel=channel)
            return interaction

        await beernight.start(click())
        state = beernight.nights[GUILD_ID]
        state.task.cancel()
        mandate = state.active[0].mandate
        await beernight.confess(click(), state.night.id, mandate.key)
        await beernight.finish(state, interaction=click())

        # Los sorbos van por `note` (se escriben cada minuto): se fuerza la escritura.
        await client.get_cog("Achievements").flush()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["beer_sips"] == mandate.sips
        assert profile.stats["beer_nights"] == 1
        assert profile.stats["beer_hosted"] == 1
        assert {"beer_sip_1", "beer_night_1", "beer_host_1"} <= set(profile.unlocked)

        pets = client.get_cog("Mascotas")
        pets.active[(GUILD_ID, OWNER_ID)] = PetState(
            id=1, guild_id=GUILD_ID, user_id=OWNER_ID, species="perro", name="Toby",
            bond=BOND_LEVELS[4],
        )  # fmt: skip
        line = await module.mascotas.cameo(client, GUILD_ID, OWNER_ID, Moment(Event.BEER, True))
        assert line is not None and "**Toby**" in line
    finally:
        await client.close()


async def test_el_perfil_junta_todas_las_secciones_con_el_bot_real(tmp_path: Path) -> None:
    """`perfil` pide cada sección a su cog: con el bot real, ninguna sale «no disponible»."""
    client = await load_bot(tmp_path)
    await client.lottery.initialize()
    await client.pets.initialize()
    try:
        perfil = client.get_cog("Perfil")
        module = importer("Perfil", client)
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        owner.guild = guild
        guild.get_member = MagicMock(return_value=owner)
        guild.get_role = MagicMock(return_value=None)

        result = await perfil.open(guild=guild, author=owner, target=None, channel=None)

        assert not isinstance(result, str)
        summary, view = result
        assert "Diego" in summary.title
        for marker in ("📊", "🏰", "🪏", "🏆", "🔥", "🎒"):
            assert marker in summary.description, marker
        for key in module.SECTION_KEYS:
            embed = await perfil.section(view, key)
            assert "no está disponible" not in (embed.description or ""), key
        # Abrir el perfil propio cuenta para sus logros, en el cog de logros de verdad.
        profile = await client.get_cog("Achievements").fresh_profile(GUILD_ID, OWNER_ID)
        assert profile.stats["perfil_views"] == 1
        assert profile.stats["perfil_seen_resumen"] == 1
    finally:
        await client.close()


async def test_el_autobus_apunta_sus_logros_y_su_jugada_con_el_bot_real(tmp_path: Path) -> None:
    """El autobús carga antes que los logros: sus partidas llegan a `logros` y a `apuestas`."""
    client = await load_bot(tmp_path)
    try:
        autobus = importer("Autobús", client)
        autobus.REVEAL_MARGIN_SECONDS = 0
        media = autobus.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        cog = client.get_cog("Autobús")
        cog.renderer = MagicMock()
        cog.renderer.reveal = AsyncMock(return_value=autobus.Reveal(win=media, lose=media))
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        await cog._guagua_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=owner,
            amount_text="100",
            send=AsyncMock(return_value=MagicMock()),
            send_error=AsyncMock(),
        )
        (view,) = cog.views
        view.deck = (Card(2, 0),) * 5  # negra: pedir rojo falla
        interaction = fake_interaction()
        interaction.user = owner
        interaction.guild = None

        await view._pick(interaction, autobus.Pick.RED)

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["bus_games"] == 1
        assert profile.stats["bus_losses"] == 1
        assert {"bus_1", "busp_1"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["autobus"].plays == 1
    finally:
        await client.close()


async def test_el_poker_apunta_sus_logros_su_jugada_y_su_club_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El póker carga antes que los logros: sus manos llegan a `logros`, `apuestas` y al club."""
    client = await load_bot(tmp_path)
    try:
        poker = importer("Póker", client)
        poker.REVEAL_MARGIN_SECONDS = 0
        media = poker.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
        cog = client.get_cog("Póker")
        cog.start = MagicMock()
        cog.sleep = AsyncMock()
        cog.renderer = MagicMock()
        cog.renderer.deal = AsyncMock(return_value=media)
        cog.renderer.showdown = AsyncMock(return_value=media)
        cog.renderer.board = AsyncMock(return_value=b"PNG")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 5
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        table, _ = await cog.ensure_table(GUILD_ID, channel, OWNER_ID)
        await table.sit(owner)
        await table.deal()
        fold = fake_interaction()
        fold.user = owner
        fold.guild = None
        await table.decide(fold, False)
        await table.finish()

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["poker_games"] == 1
        assert profile.stats["poker_folds"] == 1
        assert profile.stats["poker_rank"] == 1
        assert {"poker_1", "pokerr_1"} <= set(profile.unlocked)
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["poker"].plays == 1
        assert (await client.poker.player(GUILD_ID, OWNER_ID)).xp == 1
    finally:
        await client.close()


async def test_la_timba_apunta_logros_y_jugadas_de_los_dos_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """La timba carga antes que los logros: sus manos llegan a `logros` y a `apuestas`."""
    client = await load_bot(tmp_path)
    try:
        cog = client.get_cog("Timba")
        cog.start = MagicMock()
        cog.renderer = MagicMock()
        cog.renderer.timba = AsyncMock(return_value=b"PNG")
        players = []
        for user_id, name in ((OWNER_ID, "Diego"), (OWNER_ID + 1, "Ana")):
            user = MagicMock(spec=discord.Member)
            user.id = user_id
            user.display_name = name
            user.mention = f"<@{user_id}>"
            user.bot = False
            players.append(user)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 6
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        table, _ = await cog.ensure_table(GUILD_ID, channel, OWNER_ID, 20)

        async def fold_first(_seconds: float) -> None:
            hand = table.hand
            if hand is not None and hand.current is not None:
                interaction = fake_interaction()
                interaction.user = next(p for p in players if p.id == hand.current.user_id)
                interaction.guild = None
                await table.fold_click(interaction)

        cog.sleep = fold_first
        for user in players:
            await table.sit(user)
        await table.play_hand()

        for user in players:
            profile = await client.achievements.profile(GUILD_ID, user.id)
            assert profile.stats["timba_hands"] == 1
            assert "timba_1" in profile.unlocked
        report = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert report.by_game["timba"].plays == 1
    finally:
        await client.close()


async def test_poker_en_modo_timba_llega_al_cog_de_la_timba_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`/poker timba` busca el cog `Timba` con `find_cog`: con el bot real tiene que encontrarlo."""
    client = await load_bot(tmp_path)
    try:
        timba = client.get_cog("Timba")
        timba.start = MagicMock()
        timba.renderer = MagicMock()
        timba.renderer.timba = AsyncMock(return_value=b"PNG")
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.display_name = "Diego"
        owner.mention = f"<@{OWNER_ID}>"
        owner.bot = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 8
        channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        confirm, errors = AsyncMock(), AsyncMock()
        await client.get_cog("Póker").play_mode(
            "timba",
            guild=MagicMock(id=GUILD_ID),
            channel=channel,
            user=owner,
            amount_text="50",
            confirm=confirm,
            send_error=errors,
        )
        errors.assert_not_awaited()
        table = timba.tables[8]
        assert table.big_blind == 50 and [c.user.id for c in table.chairs] == [OWNER_ID]
    finally:
        await client.close()


async def test_las_mesas_con_fieltro_llevan_el_boton_de_tapete_antes_y_durante_la_partida(
    tmp_path: Path,
) -> None:
    """El 🎨 sale en moneda, dados y pollo también a mitad de partida, y en la ruleta."""
    import random

    from bot.services.chicken import DIFFICULTY_BY_KEY, ChickenGame
    from bot.services.coin import CoinGame
    from bot.services.craps import Bet, Status

    client = await load_bot(tmp_path)
    owner = MagicMock(id=OWNER_ID)

    def has_paint(view: discord.ui.View) -> bool:
        return any(
            isinstance(item, discord.ui.Button) and str(item.emoji) == "🎨"
            for item in view.children
        )

    try:
        coin = importer("Moneda", client).CoinView(
            client.get_cog("Moneda"), guild_id=GUILD_ID, owner=owner, stake=100
        )
        coin.rebuild()
        assert has_paint(coin), "moneda sin partida"
        coin.game = CoinGame.new(100, random.Random(1))
        coin.rebuild()
        assert has_paint(coin), "moneda a mitad de partida"

        dice = importer("Dados", client).CrapsView(
            client.get_cog("Dados"), guild_id=GUILD_ID, owner=owner, stake=100, bet=Bet.PASS
        )
        dice.rebuild()
        assert has_paint(dice), "dados sin punto"
        dice.game = MagicMock(status=Status.POINT, point=6, odds_room=0, stake=100, bet=Bet.PASS)
        dice.rebuild()
        assert has_paint(dice), "dados con el punto puesto"

        chicken = importer("Pollo", client).ChickenView(
            client.get_cog("Pollo"),
            guild_id=GUILD_ID,
            owner=owner,
            stake=100,
            difficulty=DIFFICULTY_BY_KEY["facil"],
            auto=None,
        )
        chicken.rebuild()
        assert has_paint(chicken), "pollo sin partida"
        chicken.game = ChickenGame(stake=100, difficulty=DIFFICULTY_BY_KEY["facil"], hit_lane=5)
        chicken.rebuild()
        assert has_paint(chicken), "pollo cruzando"

        # La ruleta tiene las 25 casillas llenas: el 🎨 va donde estaban las columnas.
        roulette = importer("Casino", client).RouletteTable(
            client.get_cog("Casino"), guild_id=GUILD_ID, owner=owner, stake=100
        )
        assert has_paint(roulette)
        assert len(roulette.children) <= 25
        labels = [getattr(item, "label", "") for item in roulette.children]
        assert not any("col" in (label or "") for label in labels)
    finally:
        await client.close()


async def test_saldo_patrimonio_y_hacienda_ven_los_pisos_con_el_bot_real(tmp_path: Path) -> None:
    """`pisos` carga después que el casino y el Patrimonio: sus puentes deben llegar."""
    client = await load_bot(tmp_path)
    try:
        pisos_cog = client.get_cog("Pisos")
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(30_000_000, "test")])
        trade = await pisos_cog.service.buy(GUILD, OWNER_ID, "vegueta_edificio", 100)

        fiscal = (await client.housing.member_values(GUILD))[1][OWNER_ID]
        assert fiscal >= trade.deal.quote.price
        patrimonio = client.get_cog("Patrimonio")
        assert patrimonio.housing is client.housing
        worth, _rank, _people = await patrimonio.net_worth(GUILD, OWNER_ID)
        assert worth.homes > 0 and worth.homes_fiscal == fiscal
        assert importer("Pisos", client).logros._cog(client) is client.get_cog("Achievements")

        member = MagicMock(spec=discord.Member)
        member.id, member.bot, member.display_name = OWNER_ID, False, "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        guild.get_member = MagicMock(return_value=member)
        responder = MagicMock()
        responder.guild, responder.member, responder.channel = guild, member, MagicMock()
        responder.send = AsyncMock()

        # `saldo` avisa del Patrimonio con la misma cuenta que `perfil`, pisos incluidos.
        await client.get_cog("Casino")._saldo_impl(responder, member, None)
        embed = responder.send.await_args.kwargs["embed"]
        cash = await client.economy.balance(GUILD, OWNER_ID)
        quote = await patrimonio.quote(GUILD, OWNER_ID, cash=cash, homes_fiscal=fiscal, objects=0)
        due = next(f.value for f in embed.fields if f.name == "⏳ Por pagar")
        assert quote.due and f"Patrimonio del lunes: {format_amount(quote.due)}" in due
        panel, _bills = await client.get_cog("Casino")._hacienda_view(guild, member, None)
        panel.page = "caseros"
        panel.rebuild()
        (_header, page) = [
            c.content for c in panel.walk_children() if isinstance(c, discord.ui.TextDisplay)
        ]
        assert page.startswith("### 🏠 Lo que pagan los pisos") and "ITP al comprar" in page
        assert "Diego" in page
    finally:
        await client.close()


async def test_saldo_junta_lo_pendiente_de_cada_cog_con_el_bot_real(tmp_path: Path) -> None:
    """Los puentes de `saldo` (Renta, pisos, pala, loterías y Rastro) llegan al cog de verdad."""
    client = await load_bot(tmp_path)
    try:
        casino = client.get_cog("Casino")
        now = client.economy.now()
        with client.economy.repository._transaction() as connection:  # noqa: SLF001
            connection.execute(
                "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, 300)",
                (GUILD, OWNER_ID),
            )
        await client.work.hire(GUILD, OWNER_ID, "obra")
        contract = await client.work.repository.contract(GUILD, OWNER_ID)
        contract.mom_debt = 1_234
        await client.work.repository.save_contract(GUILD, OWNER_ID, contract)
        connection = client.lottery._connect()  # noqa: SLF001
        try:
            client.lottery.reserve(
                GUILD, OWNER_ID, game="primitiva", draw_at=now + 3_600, picks=[("1", 2)],
                price=100, channel_id=None, now=now,
            )(connection)  # fmt: skip
            connection.commit()
        finally:
            connection.close()
        connection = client.housing._connect()  # noqa: SLF001
        try:
            connection.execute(
                "INSERT INTO housing_deposits (guild_id, listing, user_id, amount, since) "
                "VALUES (?, 'vegueta_edificio', ?, 1600, 0)",
                (GUILD, OWNER_ID),
            )
            connection.commit()
        finally:
            connection.close()
        rastro = client.get_cog("Rastro")
        rastro.listings[99] = Listing(
            id=99, guild_id=GUILD, seller_id=OTHER_ID, entry_id=1, item_id=1, name="Gorra",
            emoji="🧢", serial=None, edition=None, catalog_key=None, start_price=10,
            channel_id=None, created_at=now, expires_at=now + 86_400, close_at=now + 3_600,
            top_bidder=OWNER_ID, top_bid=50, top_escrow=55, top_reference=50, bids=1,
            status="abierto",
        )  # fmt: skip

        mine = await casino._pending(GUILD, OWNER_ID, 0)
        kinds = {item.kind for item in mine}
        assert {"renta_debt", "imv", "mom_debt", "lottery", "deposit", "rastro_bid"} <= kinds

        # `saldo @miembro` enseña lo mismo que si lo pidiera él: todo lo pendiente.
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        owner, other = MagicMock(spec=discord.Member), MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        other.id, other.bot, other.display_name = OTHER_ID, False, "Cotilla"
        shown = []
        for user, target in ((owner, None), (other, owner)):
            responder = MagicMock()
            responder.guild, responder.member, responder.channel = guild, user, MagicMock()
            responder.send = AsyncMock()
            await casino._saldo_impl(responder, user, target)
            embed = responder.send.await_args.kwargs["embed"]
            shown.append([(f.name, f.value) for f in embed.fields])
            assert embed.footer.text is None
        assert shown[0] == shown[1] and len(shown[0]) == 3  # por pagar, por cobrar y retenido
    finally:
        await client.close()


async def test_vender_lleva_a_los_cuatro_sitios_y_avisa_en_saldo_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El panel de `vender` (cog `Rastro`) carga con la tienda y el Monte: el Monte quita el
    título (`shop.refresh_worn`), cada destino apunta sus logros (los cuatro dan «Vendo Opel
    Corsa»), la papeleta sale en `saldo` y el puesto avisa a la lista de deseos."""
    from bot.services.shop import Kind, quote

    client = await load_bot(tmp_path)
    try:
        rastro = client.get_cog("Rastro")
        casino = client.get_cog("Casino")
        module = importer("Rastro", client)
        assert module.logros._cog(client) is client.get_cog("Achievements")
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        buyer = MagicMock(spec=discord.Member)
        buyer.id, buyer.bot, buyer.display_name, buyer.mention = 77, False, "Ana", "<@77>"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        entries = []
        for name in ("Guitarra", "Bandurria", "Laúd", "Timple"):
            item = await client.shop.create_item(
                GUILD_ID, kind=Kind.TROPHY, name=name, emoji="🎸", description="",
                price=10_000, igic="general", now=rastro.clock(),
            )  # fmt: skip
            price = quote(item, rastro.clock())
            await client.economy.repository.apply(
                GUILD, OWNER_ID, [LedgerEntry(price.total, "test")]
            )
            await client.economy.purchase(
                GUILD, OWNER_ID, base=price.base, tax=price.tax, concept="objeto",
                reserve=client.shop.reserve(
                    GUILD, OWNER_ID, item.id, expected=price, level=99, now=rastro.clock()
                ),
            )  # fmt: skip
            found = await client.shop.inventory(GUILD_ID, OWNER_ID, rastro.clock())
            entries.append(next(e.id for e in found if e.item_id == item.id))
        panel = module.VenderPanel(rastro, GUILD_ID, owner)

        def clicked() -> MagicMock:
            interaction = fake_interaction(owner)
            interaction.client = client
            interaction.guild = guild
            interaction.channel = None
            return interaction

        for entry, button in (
            (entries[0], module.PawnButton), (entries[1], module.MonteSellButton)
        ):  # fmt: skip
            panel.selected = entry
            await panel.screen()
            await next(i for i in panel.children if isinstance(i, button)).callback(clicked())
        panel.selected = entries[2]
        stall = module.StallForm(panel)
        stall.price._value = "5000"
        await stall.on_submit(clicked())
        panel.selected = entries[3]
        offer = module.MemberForm(panel)
        offer.buyer._values = [buyer]
        offer.price._value = "5000"
        interaction = clicked()
        await offer.on_submit(interaction)
        sent = interaction.followup.send.await_args
        assert sent.kwargs["allowed_mentions"].users == [buyer]
        assert isinstance(sent.kwargs["view"], module.OfferView)

        profile = await client.achievements.profile(GUILD, OWNER_ID)
        stats = profile.stats
        assert stats["monte_pawned"] == 1 and stats["monte_sold"] == 1
        assert stats["market_listings"] == 1 and stats["market_offers"] == 1
        assert "vender_four_ways" in profile.unlocked
        kinds = {item.kind for item in await casino._pending(GUILD, OWNER_ID, 0)}
        assert "monte_ticket" in kinds
    finally:
        await client.close()


async def test_el_banco_sin_monte_y_el_fichero_de_morosos_en_perfil_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`banco` carga tras la tienda y `pisos`: sus dos mostradores se pintan, `.banco monte`
    abre los préstamos con el aviso de la mudanza y el fichero de morosos sale en `perfil`."""
    client = await load_bot(tmp_path)
    try:
        banco = client.get_cog("Banco")
        module = importer("Banco", client)
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        panel = module.BankPanel(banco, GUILD_ID, owner)
        for tab in ("loans", "guarantees", "advances", "books"):
            panel.tab = tab
            kwargs = await panel.screen()
            assert kwargs["embed"].fields
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        ctx = MagicMock()
        ctx.guild, ctx.author, ctx.send = guild, owner, AsyncMock()
        await banco.banco_text.callback(banco, ctx, "monte")
        shown = ctx.send.await_args.kwargs
        assert shown["content"] == module.MONTE_MOVED
        assert shown["embed"].title == "🏦 Banco · Préstamos"
        connection = client.housing._connect()  # noqa: SLF001
        try:
            connection.execute(
                "INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, 5000)",
                (GUILD, OWNER_ID),
            )
            connection.commit()
        finally:
            connection.close()
        embed = await client.get_cog("Patrimonio").patrimonio_embed(guild, owner, owner, None)
        assert "ASNEF" in embed.fields[0].value
    finally:
        await client.close()


async def test_patrimonio_ensena_cada_piso_por_sus_participaciones(tmp_path: Path) -> None:
    """`.patrimonio` enseña la parte de cada copropietario y las partes suman el inmueble."""
    client = await load_bot(tmp_path)
    try:
        pisos = client.get_cog("Pisos")
        partner = OWNER_ID + 1
        for user_id in (OWNER_ID, partner):
            await client.economy.repository.apply(GUILD, user_id, [LedgerEntry(30_000_000, "test")])
        await pisos.service.buy(GUILD, OWNER_ID, "vegueta_edificio", 30)
        await pisos.service.buy(GUILD, partner, "vegueta_edificio", 70)
        values = (await client.housing.member_values(GUILD))[0]

        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD
        shown = {}
        for user_id in (OWNER_ID, partner):
            member = MagicMock(spec=discord.Member)
            member.id, member.bot, member.display_name = user_id, False, f"Socio {user_id}"
            progress = MagicMock()
            progress.edit = AsyncMock()
            ctx = MagicMock()
            ctx.guild, ctx.author, ctx.channel = guild, member, MagicMock()
            ctx.send = AsyncMock(return_value=progress)
            cog = client.get_cog("Patrimonio")
            await cog.patrimonio_text.callback(cog, ctx, None)
            embed = progress.edit.await_args.kwargs["embed"]
            field = next(f for f in embed.fields if "participaciones" in f.name)
            shown[user_id] = field.value
            assert f"**{format_amount(values[user_id])}**" in field.value
        market = await client.housing.market(GUILD)
        listing = market.listing("vegueta_edificio")
        whole = housing_rules.price(listing, market.index(listing.barrio))
        assert values[OWNER_ID] == whole * 30 // 100
        assert values[partner] == whole * 70 // 100
        assert "Vegueta · 30 %" in shown[OWNER_ID]
        assert "Vegueta · 70 %" in shown[partner]
        assert shown[OWNER_ID].endswith(f"de {format_amount(whole)}")
        assert shown[partner].endswith(f"de {format_amount(whole)}")

        # Con muchos inmuebles salen los que caben enteros y cuántos faltan.
        cog.home_lines = AsyncMock(return_value=[f"Inmueble {i} " + "x" * 80 for i in range(30)])
        embed = await cog.patrimonio_embed(guild, member, member, None)
        field = next(f for f in embed.fields if "participaciones" in f.name)
        assert len(field.value) <= 1_024
        *lines, more = field.value.split("\n")
        assert more == f"-# Y {30 - len(lines)} inmuebles más."
        assert all(line.endswith("x" * 80) for line in lines)
    finally:
        await client.close()


async def test_pisos_abre_las_garantias_y_se_rehipoteca_con_el_bot_real(tmp_path: Path) -> None:
    """🏦 Rehipotecar de `pisos` abre `banco` en 🔐 Garantías con el inmueble elegido; desde
    ahí se pide, se firma, la cuota sale en `saldo` y los logros se apuntan."""
    client = await load_bot(tmp_path)
    try:
        pisos = client.get_cog("Pisos")
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(10_000_000, "test")])
        await pisos.service.buy(GUILD, OWNER_ID, "taco_bajo", 100)
        connection = client.housing._connect()  # noqa: SLF001
        try:
            connection.execute(
                "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
                "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', 600000, 0, 0, "
                "0, 600000, ?)",
                (GUILD, OWNER_ID, client.get_cog("Banco").clock() - 86_400),
            )
            connection.commit()
        finally:
            connection.close()
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner.guild = guild
        painter = MagicMock()
        painter.paint = AsyncMock(side_effect=lambda states: [b"PNG"] * len(states))
        painter.close = AsyncMock()
        pisos.painter.scene = painter
        panel = importer("Pisos", client).Panel(pisos, guild=guild, owner=owner)
        panel.listing, panel.screen = "taco_bajo", "gestion"
        interaction = fake_interaction(owner)
        interaction.client, interaction.guild, interaction.channel = client, guild, None
        await panel._remortgage(interaction)  # noqa: SLF001
        shown = interaction.edit_original_response.await_args.kwargs
        assert shown["embed"].title == "🔐 Banco · Garantías"
        bank = shown["view"]
        assert bank.collateral == "taco_bajo" and bank.remortgage_most > 0

        garantias = importer("Banco", client).garantias
        form = garantias.AmountForm(bank, bank.remortgage_most)
        form.amount._value = "50000"
        clicked = fake_interaction(owner)
        clicked.client, clicked.guild, clicked.channel = client, guild, None
        await form.on_submit(clicked)
        assert bank.remortgage_offer is not None
        await bank.screen()
        sign = next(
            c for c in bank.children if getattr(c, "custom_id", "") == "banco:garantia_firmar"
        )
        clicked = fake_interaction(owner)
        clicked.client, clicked.guild, clicked.channel = client, guild, None
        await sign.callback(clicked)
        (mortgage,) = (await pisos.service.market(GUILD)).mortgages_of(OWNER_ID)
        assert mortgage.remortgage and mortgage.principal == 50_000
        profile = await client.achievements.profile(GUILD, OWNER_ID)
        assert profile.stats["remortgages_signed"] == 1 and "remortgage_1" in profile.unlocked
        casino = client.get_cog("Casino")
        kinds = {item.kind for item in await casino._pending(GUILD, OWNER_ID, 0)}  # noqa: SLF001
        assert "mortgage" in kinds
    finally:
        await client.close()


async def test_el_negocio_abre_el_banco_y_pide_un_prestamo_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """🏦 Banco de la portada del negocio abre `banco` en 🏪 Negocio; desde ahí se pide y se
    firma a nombre del negocio, los logros se apuntan y la cuota sale en `saldo`."""
    client = await load_bot(tmp_path)
    try:
        negocio = client.get_cog("Negocio")
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(5_000_000, "test")])
        await negocio.service.open(
            GUILD, OWNER_ID, sector="cafeteria", barrio="triana", name="Bar Pepe", palette=0,
            emblem=0,
        )  # fmt: skip
        connection = client.housing._connect()  # noqa: SLF001
        try:
            connection.execute(
                "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
                "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', 600000, 0, 0, "
                "0, 600000, ?)",
                (GUILD, OWNER_ID, client.get_cog("Banco").clock() - 86_400),
            )
            connection.commit()
        finally:
            connection.close()
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner.guild = guild
        panel = importer("Negocio", client).NegocioPanel(
            negocio, guild_id=GUILD, owner=owner, channel=None
        )
        interaction = fake_interaction(owner)
        interaction.client, interaction.guild, interaction.channel = client, guild, None
        await panel._bank(interaction)  # noqa: SLF001
        shown = interaction.edit_original_response.await_args.kwargs
        assert shown["embed"].title == "🏪 Banco · Bar Pepe"
        bank = shown["view"]
        assert bank.tab == "business" and bank.business_most > 50_000

        tab = importer("Banco", client).negocio_tab
        form = tab.AmountForm(bank, bank.business_most)
        form.amount._value = "50000"
        clicked = fake_interaction(owner)
        clicked.client, clicked.guild, clicked.channel = client, guild, None
        await form.on_submit(clicked)
        assert bank.business_offer is not None
        await bank.screen()
        sign = next(
            c for c in bank.children if getattr(c, "custom_id", "") == "banco:negocio_firmar"
        )
        clicked = fake_interaction(owner)
        clicked.client, clicked.guild, clicked.channel = client, guild, None
        await sign.callback(clicked)
        found = await negocio.service.loan_desk(GUILD, OWNER_ID)
        assert found is not None
        (loan,) = found[1].loans
        assert loan.principal == 50_000 and loan.business_id == found[0].id
        profile = await client.achievements.profile(GUILD, OWNER_ID)
        assert profile.stats["biz_loans_signed"] == 1 and "biz_loan_1" in profile.unlocked
        casino = client.get_cog("Casino")
        items = await casino._pending(GUILD, OWNER_ID, 0)  # noqa: SLF001
        assert any(item.kind == "loan" and "de tu negocio" in item.text for item in items)
    finally:
        await client.close()


async def test_un_miembro_avala_un_prestamo_con_el_bot_real(tmp_path: Path) -> None:
    """🤝 En `banco` se elige avalista, se pide el aval en el canal y, al avalar, se firma
    el préstamo con el aval; los logros van a los dos y el aval sale en el `saldo` de Bea."""
    client = await load_bot(tmp_path)
    try:
        banco = client.get_cog("Banco")
        module = importer("Banco", client)
        connection = client.housing._connect()  # noqa: SLF001
        try:
            for user_id, net in ((OWNER_ID, 30_000), (77, 600_000)):
                connection.execute(
                    "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
                    "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, "
                    "?, ?)",
                    (GUILD, user_id, net, net, banco.clock() - 86_400),
                )
            connection.commit()
        finally:
            connection.close()
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        bea = MagicMock(spec=discord.Member)
        bea.id, bea.bot, bea.display_name, bea.mention = 77, False, "Bea", "<@77>"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID

        def clicked(user: MagicMock) -> MagicMock:
            interaction = fake_interaction(user)
            interaction.client, interaction.guild, interaction.channel = client, guild, None
            return interaction

        panel = module.BankPanel(banco, GUILD_ID, owner)
        panel.guarantor = bea
        await panel.screen()
        assert panel.guarantor_problem is None and panel.guarantor_room > 0
        form = module.AmountForm(panel, panel.most)
        form.amount._value = str(panel.most)
        await form.on_submit(clicked(owner))
        await panel.screen()
        sign = next(c for c in panel.children if getattr(c, "custom_id", "") == "banco:firmar")
        asked = clicked(owner)
        await sign.callback(asked)
        view = asked.followup.send.await_args.kwargs["view"]
        assert isinstance(view, module.avales.GuaranteeView)
        await view.accept.callback(clicked(bea))
        market = await client.get_cog("Pisos").service.market(GUILD)
        (loan,) = market.loans_of(OWNER_ID)
        assert market.guaranteed_by(77) == [loan]
        profiles = [await client.achievements.profile(GUILD, who) for who in (OWNER_ID, 77)]
        assert profiles[0].stats["guarantees_received"] == 1
        assert profiles[1].stats["guarantees_given"] == 1
        casino = client.get_cog("Casino")
        kinds = {item.kind for item in await casino._pending(GUILD, 77, 0)}  # noqa: SLF001
        assert "guarantee" in kinds
    finally:
        await client.close()


async def test_pisos_abre_los_anticipos_y_se_adelantan_las_rentas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """💸 Adelantar rentas de `pisos` abre `banco` en 💸 Anticipos con el inmueble elegido;
    desde ahí se pide, se firma, la cuota sale en `saldo` y los logros se apuntan."""
    client = await load_bot(tmp_path)
    try:
        pisos = client.get_cog("Pisos")
        await client.economy.repository.apply(GUILD, OWNER_ID, [LedgerEntry(10_000_000, "test")])
        await pisos.service.buy(GUILD, OWNER_ID, "taco_bajo", 100)
        connection = client.housing._connect()  # noqa: SLF001
        try:
            connection.execute(
                "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', "
                "rent = 8000, until = '2099-01-01' WHERE guild_id = ? AND listing = ?",
                (GUILD, "taco_bajo"),
            )
            connection.commit()
        finally:
            connection.close()
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner.guild = guild
        painter = MagicMock()
        painter.paint = AsyncMock(side_effect=lambda states: [b"PNG"] * len(states))
        painter.close = AsyncMock()
        pisos.painter.scene = painter
        panel = importer("Pisos", client).Panel(pisos, guild=guild, owner=owner)
        panel.listing, panel.screen = "taco_bajo", "gestion"
        interaction = fake_interaction(owner)
        interaction.client, interaction.guild, interaction.channel = client, guild, None
        await panel._advance(interaction)  # noqa: SLF001
        shown = interaction.edit_original_response.await_args.kwargs
        assert shown["embed"].title == "💸 Banco · Anticipos"
        bank = shown["view"]
        assert bank.advance_key == "taco_bajo" and bank.advance_months == 12

        ask = next(
            c for c in bank.children if getattr(c, "custom_id", "") == "banco:anticipo_pedir"
        )
        clicked = fake_interaction(owner)
        clicked.client, clicked.guild, clicked.channel = client, guild, None
        await ask.callback(clicked)
        assert bank.advance_offer is not None
        sign = next(
            c for c in bank.children if getattr(c, "custom_id", "") == "banco:anticipo_firmar"
        )
        clicked = fake_interaction(owner)
        clicked.client, clicked.guild, clicked.channel = client, guild, None
        await sign.callback(clicked)
        (loan,) = (await pisos.service.market(GUILD)).loans_of(OWNER_ID)
        assert loan.advance and loan.listing == "taco_bajo" and loan.payment == 8_000
        profile = await client.achievements.profile(GUILD, OWNER_ID)
        assert profile.stats["advances_signed"] == 1 and "advance_1" in profile.unlocked
        assert "advance_12" in profile.unlocked
        casino = client.get_cog("Casino")
        pending = await casino._pending(GUILD, OWNER_ID, 0)  # noqa: SLF001
        assert any("Anticipo de rentas de" in item.text for item in pending)
    finally:
        await client.close()


async def test_pisos_ve_el_aval_de_los_padres_de_pala_con_el_bot_real(tmp_path: Path) -> None:
    """`pisos` carga antes que `pala`: sus puentes del aval tienen que encontrar el trabajo."""
    client = await load_bot(tmp_path)
    try:
        curro = importer("Pisos", client).curro
        await client.work.hire(GUILD, OWNER_ID, "obra")
        assert not await curro.parents_aval(client, GUILD, OWNER_ID)
        await client.work.ask_favor(GUILD, OWNER_ID, "aval")
        assert await curro.parents_aval(client, GUILD, OWNER_ID)
        await curro.spend_parents_aval(client, GUILD, OWNER_ID)
        assert not await curro.parents_aval(client, GUILD, OWNER_ID)
        before = (await client.work.status(GUILD, OWNER_ID)).contract.family
        await curro.parents_lose_home(client, GUILD, OWNER_ID)
        assert (await client.work.status(GUILD, OWNER_ID)).contract.family < before
        assert await curro.ladrillo_builders(client, GUILD) == ()  # peón, no promotor
        contract = (await client.work.status(GUILD, OWNER_ID)).contract
        contract.level, contract.branch = 6, "ladrillo"
        await client.work.repository.save_contract(GUILD, OWNER_ID, contract)
        await client.work.repository.add_shift(
            GUILD, OWNER_ID,
            ShiftRecord("obra", 6, "2026-10-19", client.work.now(), 80, "ordinario", 1, 1),
        )  # fmt: skip
        assert await curro.ladrillo_builders(client, GUILD) == (OWNER_ID,)
    finally:
        await client.close()


async def test_cada_100_jugadas_del_casino_dan_una_caja_de_carton_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`apuestas.record` cuenta cada jugada de cada juego y la tienda da la caja y avisa."""
    from bot.services.casino_stats import Play

    client = await load_bot(tmp_path)
    try:
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot = OWNER_ID, False
        earlier = Play(game="ruleta", stake=100, payout=0, tax=0, balance_after=0)
        for _ in range(98):
            await client.casino_stats.record(GUILD_ID, OWNER_ID, earlier, now=1.0)
        # La 99 y la 100 llegan por el puente de dos juegos distintos, cargados antes.
        for game in ("Casino", "Moneda"):
            module = importer(game, client)
            await module.apuestas.record(
                client, GUILD_ID, owner, game="ruleta", stake=100, net=-100, balance_after=0, tax=0
            )
            if game == "Casino":
                tienda = client.get_cog("Tienda")
                assert await client.shop.inventory(GUILD_ID, OWNER_ID, tienda.clock()) == []
        tienda = client.get_cog("Tienda")
        mine = await client.shop.inventory(GUILD_ID, OWNER_ID, tienda.clock())
        keys = [e.catalog_key for e in mine]
        assert keys == ["caja_carton"]

        # El aviso espera al siguiente resultado con dinero, de cualquier juego, y sale una vez.
        module = importer("Dados", client)
        hint = await module.renta.hint(client, GUILD_ID, OWNER_ID)
        assert hint is not None and "Caja de cartón" in hint and "100 jugadas" in hint
        assert "Caja de cartón" not in (await module.renta.hint(client, GUILD_ID, OWNER_ID) or "")

        await client.get_cog("Achievements").flush()
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["lootbox_free"] == 1 and "box_free_1" in profile.unlocked
        # Las jugadas 101 a 199 no dan nada más.
        for _ in range(5):
            await importer("Casino", client).apuestas.record(
                client, GUILD_ID, owner, game="ruleta", stake=100, net=-100, balance_after=0, tax=0
            )
        assert len(await client.shop.inventory(GUILD_ID, OWNER_ID, tienda.clock())) == 1
    finally:
        await client.close()


async def test_cada_5_niveles_dan_una_caja_de_regalo_con_el_bot_real(tmp_path: Path) -> None:
    """`message_stats` carga antes que la tienda: su puente tiene que llegar a la mochila."""
    from bot.repositories.message_stats import LevelAward

    client = await load_bot(tmp_path)
    try:
        stats = client.get_cog("MessageStats")
        module = sys.modules[type(stats).__module__]
        assert module.tienda.grant_box is not None
        member = MagicMock(spec=discord.Member)
        member.id, member.bot, member.display_name = OWNER_ID, False, "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        tienda = client.get_cog("Tienda")

        async def level_up(before: int, after: int) -> None:
            award = LevelAward(
                previous_xp=50 * before * (before + 1) + 1,
                total_xp=50 * after * (after + 1) + 1,
                cooldown_seconds=60,
                announce_channel_id=None,
            )
            await stats._handle_level_up(guild, channel, member, award)

        def announcement(channel: MagicMock) -> str:
            """El último aviso de nivel (los de logros llevan un embed y no tienen texto)."""
            return [c.args[0] for c in channel.send.await_args_list if c.args][-1]

        async def boxes() -> list[str]:
            entries = await client.shop.inventory(GUILD_ID, OWNER_ID, tienda.clock())
            # Subir de nivel también puede traer una mascota que aparece sola: se ignora.
            return sorted(e.catalog_key for e in entries if e.catalog_key.startswith("caja_"))

        await level_up(3, 4)
        assert await boxes() == [], "el nivel 4 no es múltiplo de 5"
        await level_up(4, 5)
        assert await boxes() == ["caja_regalo"]
        text = announcement(channel)
        assert "nivel **5**" in text and "Caja de regalo" in text
        await level_up(5, 9)
        assert len(await boxes()) == 1
        await level_up(9, 20)  # cruza el 10, el 15 y el 20
        assert await boxes() == ["caja_regalo"] * 4
        assert "3 **Caja de regalo**" in announcement(channel)
        await client.get_cog("Achievements").flush()
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["lootbox_free"] == 4
    finally:
        await client.close()


async def test_abrir_una_caja_apunta_logros_y_la_mascota_habla_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Abrir una caja llega a logros (cajas, colecciones) y a la mascota con su `Event`."""
    from bot.services.lootboxes import BOX_BY_KEY, COLLECTION_BY_KEY, PIECE_BY_KEY
    from bot.services.pets import BOND_LEVELS, PetState

    client = await load_bot(tmp_path)
    await client.pets.initialize()
    try:
        tienda = client.get_cog("Tienda")
        tienda.lootbox_pause = 0
        await tienda.stock_up(GUILD_ID)
        box = await client.shop.item_by_key(GUILD_ID, "caja_carton")
        assert box is not None
        await client.shop.grant(GUILD_ID, OWNER_ID, box, 0.0)
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        pets = client.get_cog("Mascotas")
        pets.active[(GUILD_ID, OWNER_ID)] = PetState(
            id=1, guild_id=GUILD_ID, user_id=OWNER_ID, species="perro", name="Toby",
            bond=BOND_LEVELS[4],
        )  # fmt: skip
        pets.rng = type("Always", (random.Random,), {"random": lambda self: 0.0})()
        backpack = await tienda.backpack_view(guild, owner, owner)
        interaction = fake_interaction(owner)
        message = MagicMock(edit=AsyncMock())
        interaction.followup.send = AsyncMock(return_value=message)
        interaction.channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.send = AsyncMock()

        await tienda.start_use(interaction, backpack, box.id)

        piece = next(
            PIECE_BY_KEY[e.catalog_key]
            for e in await client.shop.inventory(GUILD_ID, OWNER_ID, tienda.clock())
            if e.catalog_key in PIECE_BY_KEY
        )
        collection = COLLECTION_BY_KEY[piece.collection]
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["lootbox_opened"] == 1
        assert profile.stats[f"lootbox_box_{BOX_BY_KEY['caja_carton'].key}"] == 1
        assert profile.stats[collection.stat] == 1
        assert {"box_open_1", "box_first_carton", "use_1"} <= set(profile.unlocked)
        assert "**Toby**" in message.edit.await_args.kwargs["content"]
    finally:
        await client.close()


async def test_las_sanciones_de_admin_llegan_al_historial_del_panel_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`Admin` importa el panel antes de que cargue: `record` tiene que encontrarlo."""
    from types import SimpleNamespace

    from bot.utils.responder import CommandResponder

    client = await load_bot(tmp_path)
    await client.moderation.initialize()
    try:

        def member(member_id: int, position: int) -> MagicMock:
            fake = MagicMock(spec=discord.Member)
            fake.id = member_id
            fake.top_role = SimpleNamespace(position=position)
            fake.kick = AsyncMock()
            return fake

        guild = SimpleNamespace(id=GUILD_ID, owner_id=OWNER_ID, me=member(3, 10))
        target = member(20, 1)
        target.guild = guild
        responder = MagicMock(spec=CommandResponder)
        responder.guild = guild
        responder.member = member(OWNER_ID, 9)
        responder.send = AsyncMock()

        await client.get_cog("Admin")._kick_impl(responder, target, "por spam")

        [sanction] = await client.moderation.sanctions(GUILD_ID, 20)
        assert (sanction.kind, sanction.actor_id, sanction.reason) == (
            "echar",
            OWNER_ID,
            "por spam",
        )
    finally:
        await client.close()


async def bet_on_match(  # noqa: ANN201
    client: BotClient, *, user_id: int = OWNER_ID, pick: str = "1", market: str = "1x2"
):
    """Una apuesta deportiva abierta (por defecto, 1 al Madrid, 100 Y$ a ×1,85) en `e1`."""
    from bot.repositories.economy import SportsBet

    start = client.economy.now() + 3_600
    ticket = SportsBet(
        id=0,
        guild_id=GUILD_ID,
        user_id=user_id,
        channel_id=77,
        event_id="e1",
        sport="soccer_spain_la_liga",
        home="Real Madrid",
        away="Barcelona",
        commence=start,
        pick=pick,
        odds=185,
        stake=100,
        created_at=client.economy.now(),
        market=market,
    )
    bet, _ = await client.economy.sports_bet(ticket, game="deportes")
    return bet


def sent_texts(channel: MagicMock) -> list[str]:
    """Lo que se mandó al canal: el texto suelto o el de los bloques de una vista."""
    texts = []
    for call in channel.send.await_args_list:
        if call.args:
            texts.append(str(call.args[0]))
        elif (view := call.kwargs.get("view")) is not None:
            texts.append(
                "\n".join(c.content for c in view.walk_children() if hasattr(c, "content"))
            )
    return texts


async def test_saldo_incluye_las_apuestas_deportivas_abiertas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """`deportes` carga después que el casino: su puente de lo pendiente tiene que llegar."""
    client = await load_bot(tmp_path)
    try:
        bet = await bet_on_match(client)
        casino = client.get_cog("Casino")

        mine = await casino._pending(GUILD, OWNER_ID, 0)

        [line] = [item for item in mine if item.kind == "sports_bet"]
        assert (line.amount, line.when) == (100, bet.commence)
        assert "Real Madrid – Barcelona" in line.text
    finally:
        await client.close()


async def test_resolver_una_apuesta_deportiva_llega_a_apuestas_y_a_los_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El puente `deportes` -> `apuestas` y logros con los cogs cargados como en producción."""
    from bot.services.sports import RESULT_DELAY, Match, Score

    client = await load_bot(tmp_path)
    try:
        bet = await bet_on_match(client)
        await bet_on_match(client, pick="+2.5", market="goles")
        cog = client.get_cog("Deportes")
        match = Match("e1", bet.sport, bet.home, bet.away, bet.commence, {})
        await client.sports.save_odds(bet.sport, [match], now=bet.created_at, remaining=None)
        await client.sports.save_scores(
            bet.sport, {"e1": Score(2, 1)}, now=bet.created_at, remaining=None
        )
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.get_member = MagicMock(return_value=owner)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        client.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]

        resolved = await cog.settle(bet.commence + RESULT_DELAY)

        assert [r.bet.status for r in resolved] == ["won", "won"]
        # Un envío es el resultado (con el marcador dibujado, el doble de `render_fakes`);
        # los demás, los avisos de logros que desbloquea.
        sent = sent_texts(channel)
        assert sum("**Final:" in text for text in sent) == 1
        assert any(
            call.kwargs.get("file") is not None and call.kwargs["file"].filename == "final.png"
            for call in channel.send.await_args_list
        )
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["deportes"].plays == 2
        pending = client.get_cog("Achievements")._pending[GUILD_ID][OWNER_ID]
        assert pending.add["casino_plays"] == 2
        assert pending.add["casino_game_deportes"] == 2
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["sports_settled"] == 2
        assert profile.stats["sports_wins"] == 2
        assert profile.stats["sports_win_goles"] == 1
    finally:
        await client.close()


async def bet_on_prediction(client: BotClient, *, user_id: int = OWNER_ID, side: int = 0):  # noqa: ANN201
    """Una predicción abierta (por defecto, 100 Y$ al Sí a ×1,51) y su mercado en Polymarket."""
    import json
    from dataclasses import replace

    from bot.repositories.economy import PredictBet
    from bot.services.polymarket import Resolution, parse_event

    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "elecciones.json")
        .read_text(encoding="utf-8")
    )  # fmt: skip
    base = next(
        e
        for e in map(parse_event, raw)
        if e is not None and e.title.startswith("Spain General Election: PP + Vox")
    )
    ends = client.economy.now() + 3_600
    market = replace(base.markets[0], end=ends)
    ticket = PredictBet(
        id=0,
        guild_id=GUILD_ID,
        user_id=user_id,
        channel_id=77,
        event_id=base.id,
        event_title="Mayoría de PP y Vox",
        market_id=market.id,
        question="Mayoría de PP y Vox",
        side=side,
        side_label="Sí" if side == 0 else "No",
        section="espana",
        odds=151,
        stake=100,
        ends=ends,
        created_at=client.economy.now(),
    )
    bet, _ = await client.economy.predict_bet(ticket, game="predice")
    resolved = replace(
        market, active=False, closed=True, accepting=False, resolution=Resolution.RESOLVED,
        prices=(1.0, 0.0),
    )  # fmt: skip
    return bet, resolved


async def test_saldo_incluye_las_predicciones_abiertas_con_el_bot_real(tmp_path: Path) -> None:
    """`predice` carga después que el casino: su puente de lo pendiente tiene que llegar."""
    client = await load_bot(tmp_path)
    try:
        bet, _ = await bet_on_prediction(client)
        casino = client.get_cog("Casino")

        mine = await casino._pending(GUILD, OWNER_ID, 0)

        [line] = [item for item in mine if item.kind == "predict_bet"]
        assert (line.amount, line.when) == (100, bet.ends)
        assert "Mayoría de PP y Vox" in line.text
    finally:
        await client.close()


async def test_resolver_una_prediccion_llega_a_apuestas_y_a_los_logros_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El puente `predice` -> `apuestas` y logros con los cogs cargados como en producción."""
    client = await load_bot(tmp_path)
    try:
        bet, market = await bet_on_prediction(client)
        cog = client.get_cog("Predice")
        cog.client = SimpleNamespace(market=AsyncMock(return_value=market))
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.get_member = MagicMock(return_value=owner)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        client.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]

        resolved = await cog.settle(bet.ends + 60)

        assert [r.bet.status for r in resolved] == ["won"]
        # Un envío es el resultado; los demás, los avisos de logros que desbloquea.
        assert sum("Resultado: **Sí**" in text for text in sent_texts(channel)) == 1
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["predice"].plays == 1
        pending = client.get_cog("Achievements")._pending[GUILD_ID][OWNER_ID]
        assert pending.add["casino_plays"] == 1
        assert pending.add["casino_game_predice"] == 1
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["predict_settled"] == 1
        assert profile.stats["predict_wins"] == 1
    finally:
        await client.close()


async def combo_on_matches(client: BotClient):  # noqa: ANN201
    """Una combinada abierta de 100 Y$: 1 en `e1` (×1,85) y X en `e2` (×3,60)."""
    from bot.repositories.economy import SportsCombo, SportsLeg

    start = client.economy.now() + 3_600
    legs = tuple(
        SportsLeg(event, "soccer_spain_la_liga", home, away, start, "1x2", pick, odds)
        for event, home, away, pick, odds in (
            ("e1", "Real Madrid", "Barcelona", "1", 185),
            ("e2", "Betis", "Sevilla", "X", 360),
        )
    )
    ticket = SportsCombo(
        id=0,
        guild_id=GUILD_ID,
        user_id=OWNER_ID,
        channel_id=77,
        stake=100,
        odds=666,
        created_at=client.economy.now(),
        legs=legs,
    )
    combo, _ = await client.economy.sports_combo_bet(ticket, game="deportes")
    return combo


async def test_una_combinada_sale_en_saldo_y_al_resolverse_llega_a_apuestas_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """Los puentes de las combinadas (`saldo`, `apuestas`, logros) con los cogs de producción."""
    from bot.services.sports import RESULT_DELAY, Match, Score

    client = await load_bot(tmp_path)
    try:
        combo = await combo_on_matches(client)
        mine = await client.get_cog("Casino")._pending(GUILD, OWNER_ID, 0)
        [line] = [item for item in mine if item.kind == "sports_combo"]
        assert (line.amount, line.when) == (100, combo.ends)

        cog = client.get_cog("Deportes")
        for leg in combo.legs:
            match = Match(leg.event_id, leg.sport, leg.home, leg.away, leg.commence, {})
            await client.sports.save_odds(leg.sport, [match], now=combo.created_at, remaining=None)
        await client.sports.save_scores(
            "soccer_spain_la_liga",
            {"e1": Score(2, 1), "e2": Score(1, 1)},
            now=combo.created_at,
            remaining=None,
        )
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        guild = MagicMock(spec=discord.Guild)
        guild.get_member = MagicMock(return_value=owner)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        client.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
        client.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]

        [done] = await cog.settle_combos(combo.ends + RESULT_DELAY)

        assert (done.combo.status, done.combo.payout) == ("won", 666)
        assert sum("Combinadas resueltas" in text for text in sent_texts(channel)) == 1
        plays = await client.casino_stats.report(
            GUILD_ID, OWNER_ID, since=None, today=date(2026, 10, 6)
        )
        assert plays.by_game["deportes"].plays == 1
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["sports_combo_wins"] == 1
        assert profile.stats["sports_combos_settled"] == 1
    finally:
        await client.close()


async def test_donar_desde_la_renta_llega_al_cog_de_donativos(tmp_path: Path) -> None:
    """El botón Donar del consejo de la Renta dona con el `donar` de siempre."""
    client = await load_bot(tmp_path)
    try:
        ong = BY_KEY["hambre"]
        member = SimpleNamespace(id=OWNER_ID, display_name="Diego", mention="@Diego", bot=False)
        interaction = fake_interaction(member)
        interaction.guild = SimpleNamespace(id=GUILD_ID)
        interaction.channel_id = 5

        await client.get_cog("Renta").donate(interaction, "hambre", "100", suggested=100)

        # El saldo del miembro también cobra el logro del primer donativo: se mira la ONG.
        assert await client.economy.balance(GUILD_ID, ong.account_id) == 100
        assert interaction.followup.send.await_args.kwargs["ephemeral"] is False
    finally:
        await client.close()


async def test_el_rasca_apunta_sus_logros_al_acabar_con_el_bot_real(tmp_path: Path) -> None:
    """El rasca escribe el resultado y apunta los logros cuando acaba el GIF, en el cog real."""
    client = await load_bot(tmp_path)
    try:
        module = importer("Loteria", client)
        module.REVEAL_MARGIN_SECONDS = 0
        cog = client.get_cog("Loteria")
        cog.ahead = False
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id = OWNER_ID
        owner.bot = False
        owner.display_name = "Diego"
        owner.guild = guild
        panel = module.LotteryPanel(cog, guild, owner)
        interaction = fake_interaction(owner)
        interaction.guild = guild
        interaction.channel = None

        await cog.scratch(interaction, panel, module.GAME_BY_KEY["x10"])
        while cog._later:  # noqa: SLF001
            await asyncio.gather(*list(cog._later))  # noqa: SLF001

        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["lottery_scratches"] == 1
        assert "rasca_1" in profile.unlocked
        assert any(stat.startswith("lottery_day_") for stat in profile.stats)
    finally:
        await client.close()


async def test_comprar_ropa_desde_el_armario_cobra_igic_y_la_pone_con_el_bot_real(
    tmp_path: Path,
) -> None:
    """El armario de `mascota` compra con la caja de la tienda: IGIC, mochila y logros.

    Y la ropa que regala un logro llega sola al abrir el armario, aunque el logro sea de
    antes de que existiera.
    """
    from bot.cogs.pets import Mode
    from bot.services.pet_wardrobe import OUTFIT_BY_KEY, Slot

    client = await load_bot(tmp_path)
    await client.pets.initialize()
    try:
        tienda = client.get_cog("Tienda")
        await tienda.stock_up(GUILD_ID)
        cat = await client.shop.item_by_key(GUILD_ID, "mascota_gato")
        await client.shop.grant(GUILD_ID, OWNER_ID, cat, 0.0)
        await client.economy.grant(GUILD_ID, OWNER_ID, amount=50_000, reason="prueba")
        guild = MagicMock(spec=discord.Guild)
        guild.id = GUILD_ID
        owner = MagicMock(spec=discord.Member)
        owner.id, owner.bot, owner.display_name = OWNER_ID, False, "Diego"
        owner.guild = guild
        pets = client.get_cog("Mascotas")
        panel = await pets.panel(guild, owner, owner)

        def click() -> MagicMock:
            interaction = fake_interaction(owner)
            interaction.guild = guild
            interaction.channel = MagicMock(spec=discord.TextChannel)
            interaction.channel.send = AsyncMock()
            return interaction

        before = await client.economy.balance(GUILD_ID, OWNER_ID)
        await pets.dress(click(), panel, Slot.HEAD, "comprar:chistera")
        assert panel.checkout is not None and panel.preview is OUTFIT_BY_KEY["chistera"]
        total = panel.checkout.price.total
        assert total > OUTFIT_BY_KEY["chistera"].price, "con IGIC"
        await panel._pay(click())
        # Lo cobrado, menos lo que paga el logro de la primera prenda (con su IRPF).
        after = await client.economy.balance(GUILD_ID, OWNER_ID)
        assert before - total <= after < before - total // 2
        assert panel.looks[panel.current.id][Slot.HEAD] is OUTFIT_BY_KEY["chistera"]
        profile = await client.achievements.profile(GUILD_ID, OWNER_ID)
        assert profile.stats["pet_dressed"] == 1 and "pet_dress_1" in profile.unlocked

        # La corona de laurel llega al abrir el armario si ya se tenía su logro.
        from bot.services.achievements import StatDelta, newly_unlocked

        await client.achievements.record(
            GUILD_ID,
            {OWNER_ID: StatDelta(peak={"pet_bond_max": 10})},
            newly_unlocked,
            now=0.0,
        )
        await panel._goto(Mode.WARDROBE)(click())
        keys = {e.catalog_key for e in await client.shop.inventory(GUILD_ID, OWNER_ID, 0.0)}
        assert OUTFIT_BY_KEY["laurel"].catalog_key in keys
    finally:
        await client.close()
