"""Los botones contestan a Discord antes de tocar la base de datos, con el bot real.

Discord da 3 segundos para contestar a un clic. Si antes de contestar el botón
espera a SQLite (que puede estar esperando a otra escritura) o dibuja una
imagen, el botón se queda «pensando» y acaba en «Esta interacción ha fallado».
La regla (CLAUDE.md, «Botones rápidos») es aceptar el clic con
`bot.utils.interactions.ack` antes de ese trabajo.

Cada caso pulsa un botón de un cog cargado como en producción y apunta, en el
mismo orden en que pasan, las respuestas a la interacción y las conexiones a la
base de datos (`bot.repositories.sqlite.connect`). La primera tiene que ser una
respuesta. Un caso que no llegue a tocar la base de datos no prueba nada, así que
también se exige que la toque.
"""

from __future__ import annotations

import itertools
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from coin_fakes import Scripted
from craps_fakes import Scripted as DiceScripted
from interaction_fakes import fake_interaction
from render_fakes import use_fake_drawings

import bot.repositories.sqlite as sqlite_module
from bot.app import INITIAL_EXTENSIONS, BotClient
from bot.cogs.craps_mesa import FichaModal
from bot.cogs.shop_front import WISH_TAB, CartPanel, ItemSheet
from bot.services.blackjack import BlackjackGame, Card
from bot.services.coin import Outcome, Side
from bot.services.craps import Bet
from bot.services.todo import Priority
from bot.services.war import WarGame

GUILD_ID = 1
OWNER_ID = 10


@pytest.fixture(autouse=True)
def fake_drawings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin dibujar las máquinas del casino: aquí la imagen no entra en ninguna aserción.

    La prueba solo apunta el orden entre las respuestas a Discord y las conexiones a
    SQLite. Dibujar el GIF de la tragaperras, la ruleta o el pachinko (y su
    precalentamiento al cargar el cog) tardaba 1-3 s por caso sin cambiar ese orden.
    Los cogs, el bot y la base de datos siguen siendo los reales (ver `render_fakes`).
    """
    use_fake_drawings(monkeypatch)


#: Un clic: recibe la interacción falsa.
Click = Callable[[MagicMock], Awaitable[None]]
#: Cada caso prepara lo que haga falta (sin contar) y devuelve el clic que se mide.
Press = Callable[[BotClient, MagicMock], Awaitable[Click]]


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
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
    await client.todo.initialize()
    await client.slots_repository.initialize()
    await client.picar.initialize()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    return client


def module_of(client: BotClient, cog_name: str) -> ModuleType:
    """El módulo que cargó `load_extension` (no el importado desde aquí)."""
    cog = client.get_cog(cog_name)
    assert cog is not None, cog_name
    return sys.modules[type(cog).__module__]


def make_owner() -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    guild.name = "Servidor"
    owner = MagicMock(spec=discord.Member)
    owner.id = OWNER_ID
    owner.bot = False
    owner.display_name = "Diego"
    owner.mention = f"<@{OWNER_ID}>"
    owner.guild = guild
    owner.guild_permissions = discord.Permissions.all()
    owner.roles = []
    guild.get_member = MagicMock(return_value=owner)
    guild.get_role = MagicMock(return_value=None)
    return owner


async def press_slots(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Tragaperras")
    cog.ahead = False  # pintar por adelantado no es lo que se mide
    module_of(client, "Tragaperras").REVEAL_MARGIN_SECONDS = 0  # sin esperar al final del GIF
    view = module_of(client, "Tragaperras").SlotMachineView(
        cog, guild_id=GUILD_ID, owner=owner, stake=1
    )

    async def click(interaction: MagicMock) -> None:
        await view.play(interaction)

    return click


async def press_slots_double(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Tragaperras")
    cog.ahead = False
    view = module_of(client, "Tragaperras").SlotMachineView(
        cog, guild_id=GUILD_ID, owner=owner, stake=1
    )

    async def click(interaction: MagicMock) -> None:
        await view._double_stake(interaction)

    return click


def slots_view(client: BotClient, owner: MagicMock):  # noqa: ANN201
    cog = client.get_cog("Tragaperras")
    cog.ahead = False
    module = module_of(client, "Tragaperras")
    module.REVEAL_MARGIN_SECONDS = 0
    return module.SlotMachineView(cog, guild_id=GUILD_ID, owner=owner, stake=1)


async def press_slots_respin(client: BotClient, owner: MagicMock) -> Click:
    from bot.services.slots import REEL_STRIPS, spin_at

    view = slots_view(client, owner)
    near = next(
        spin
        for stops in itertools.product(*(range(len(strip)) for strip in REEL_STRIPS))
        if (spin := spin_at(stops)).teaser is not None
    )
    # El precio enseñado, de sobra: así el clic cobra y gira.
    view.respin_offer = (near, 1, 10**6, 1)

    async def click(interaction: MagicMock) -> None:
        await view._respin(interaction)

    return click


async def press_slots_gamble(client: BotClient, owner: MagicMock) -> Click:
    view = slots_view(client, owner)
    view.double_offer = (1, 0)

    async def click(interaction: MagicMock) -> None:
        await view._double_red(interaction)

    return click


async def press_slots_daily(client: BotClient, owner: MagicMock) -> Click:
    view = slots_view(client, owner)
    view.daily_streak = 1

    async def click(interaction: MagicMock) -> None:
        await view._daily(interaction)

    return click


async def press_slots_autoplay(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Tragaperras")
    cog.ahead = False
    module = module_of(client, "Tragaperras")
    # Una sola tirada y sin esperas: lo que se mide es la respuesta al clic.
    module.AUTOPLAY_MAX = 1
    module.AUTOPLAY_MIN_GAP = 0
    module.REVEAL_MARGIN_SECONDS = 0
    view = module.SlotMachineView(cog, guild_id=GUILD_ID, owner=owner, stake=1)

    async def click(interaction: MagicMock) -> None:
        await view._autoplay_click(interaction)
        await view.autoplay.task

    return click


async def picar_view(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """El pozo de `picar` con la calibración ya comprobada y saldo para el pico más caro."""
    cog = client.get_cog("Pico")
    module = module_of(client, "Pico")
    module.REVEAL_MARGIN_SECONDS = 0
    await cog._check_task
    assert cog.disabled_reason is None, cog.disabled_reason
    await client.economy.grant(GUILD_ID, owner.id, amount=10_000, reason="prueba")
    return module.PicarView(cog, guild_id=GUILD_ID, owner=owner)


async def press_picar_dig(client: BotClient, owner: MagicMock) -> Click:
    view = await picar_view(client, owner)

    async def click(interaction: MagicMock) -> None:
        await view._dig(interaction, "wood")

    return click


async def press_picar_records(client: BotClient, owner: MagicMock) -> Click:
    view = await picar_view(client, owner)

    async def click(interaction: MagicMock) -> None:
        await view._records(interaction)

    return click


async def press_pachinko_autoplay(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Pachinko")
    module = module_of(client, "Pachinko")
    # Una sola tanda, sin animación y sin esperas: lo que se mide es la respuesta al clic.
    module.AUTOPLAY_MAX = 1
    module.AUTOPLAY_MIN_GAP = 0
    module.REVEAL_MARGIN_SECONDS = 0
    view = module.PachinkoView(cog, guild_id=GUILD_ID, owner=owner, stake=10)
    view.turbo = True

    async def click(interaction: MagicMock) -> None:
        await view._autoplay_click(interaction)
        await view.autoplay.task

    return click


async def press_pachinko_launch(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Pachinko")
    module = module_of(client, "Pachinko")
    module.REVEAL_MARGIN_SECONDS = 0
    view = module.PachinkoView(cog, guild_id=GUILD_ID, owner=owner, stake=10)
    view.turbo = True

    async def click(interaction: MagicMock) -> None:
        await view._launch(interaction)

    return click


def coin_view(client: BotClient, owner: MagicMock, *results: Outcome):  # noqa: ANN201
    """Mesa de la moneda con dibujo falso, sin esperas y con el azar de guion."""
    cog = client.get_cog("Moneda")
    module = module_of(client, "Moneda")
    module.REVEAL_MARGIN_SECONDS = 0
    cog.renderer = MagicMock()
    cog.renderer.toss = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    cog.renderer.board = AsyncMock(return_value=b"PNG")
    cog.rng = Scripted(*results, Outcome.CARA)
    cog.ahead = False
    return module.CoinView(cog, guild_id=GUILD_ID, owner=owner, stake=10)


async def press_coin_flip(client: BotClient, owner: MagicMock) -> Click:
    view = coin_view(client, owner, Outcome.CARA)

    async def click(interaction: MagicMock) -> None:
        await view._flip_cara(interaction)

    return click


async def press_coin_cash_out(client: BotClient, owner: MagicMock) -> Click:
    view = coin_view(client, owner, Outcome.CARA)
    # Una racha con un acierto ya en marcha (la apuesta cobrada antes de medir).
    await view.play(Side.CARA, AsyncMock())

    async def click(interaction: MagicMock) -> None:
        await view._cash_out(interaction)

    return click


def gallos_cog(client: BotClient):  # noqa: ANN201
    """Los gallos con dibujo falso y sin esperas."""
    cog = client.get_cog("Gallos")
    module = module_of(client, "Gallos")
    module.REVEAL_MARGIN_SECONDS = 0
    cog.renderer = MagicMock()
    cog.renderer.still = AsyncMock(return_value=b"PNG")
    cog.renderer.fight = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    return cog, module


async def press_gallos_fight(client: BotClient, owner: MagicMock) -> Click:
    cog, module = gallos_cog(client)
    panel = module.HousePanel(cog, guild_id=GUILD_ID, owner=owner, stake=10)

    async def click(interaction: MagicMock) -> None:
        await panel._fight(interaction)

    return click


async def press_gallos_train(client: BotClient, owner: MagicMock) -> Click:
    cog, module = gallos_cog(client)
    panel = module.HousePanel(cog, guild_id=GUILD_ID, owner=owner, stake=10)
    panel.record = module.RoosterRecord(name="Kiki")
    panel.training = True
    panel.rebuild()

    async def click(interaction: MagicMock) -> None:
        await panel._train(interaction, module.Stat.STRENGTH)

    return click


async def press_gallos_accept(client: BotClient, owner: MagicMock) -> Click:
    cog, module = gallos_cog(client)
    rival = make_owner()
    rival.id = owner.id + 1
    red = module.Owner(rival, module.RoosterRecord(name="Rojo"), None)
    blue = module.Owner(owner, module.RoosterRecord(name="Azul"), None)
    duel = module.Duel(cog, guild_id=GUILD_ID, channel=None, red=red, blue=blue, stake=10)

    async def click(interaction: MagicMock) -> None:
        await duel.accept(interaction)
        if duel.task is not None:
            duel.task.cancel()
        if duel.prepared is not None:
            await duel.prepared

    return click


def enraya_cog(client: BotClient):  # noqa: ANN201
    """El 4 en raya con dibujo falso, sin esperas y sin pintar por adelantado."""
    cog = client.get_cog("Enraya")
    module = module_of(client, "Enraya")
    module.REVEAL_MARGIN_SECONDS = 0
    cog.ahead = False
    cog.renderer = MagicMock()
    cog.renderer.still = AsyncMock(return_value=b"PNG")
    cog.renderer.animate = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    return cog, module


async def press_enraya_drop(client: BotClient, owner: MagicMock) -> Click:
    cog, module = enraya_cog(client)
    panel = module.SoloTable(cog, guild_id=GUILD_ID, owner=owner, channel=None)
    # Las rojas ganan soltando en la columna 4: así el clic llega a apuntar la partida.
    for column in (0, 0, 1, 1, 2, 2):
        panel.board = panel.board.play(column)
    panel.rebuild()

    async def click(interaction: MagicMock) -> None:
        await panel._column(interaction, 3)

    return click


def backgammon_cog(client: BotClient):  # noqa: ANN201
    """El backgammon con dibujo falso, sin esperas y sin pintar por adelantado."""
    cog = client.get_cog("Backgammon")
    module = module_of(client, "Backgammon")
    module.REVEAL_MARGIN_SECONDS = 0
    cog.ahead = False
    cog.renderer = MagicMock()
    cog.renderer.still = AsyncMock(return_value=b"PNG")
    cog.renderer.animate = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    return cog, module


async def press_backgammon_confirm(client: BotClient, owner: MagicMock) -> Click:
    cog, module = backgammon_cog(client)
    panel = module.SoloTable(cog, guild_id=GUILD_ID, owner=owner, channel=None)
    # A las blancas les queda una ficha: la sacan y ✅ gana, así el clic apunta la partida.
    points = [0] * 24
    points[0], points[20] = 1, -14
    start = module.Position(tuple(points), (0, 0), (14, 1))
    panel.phase, panel.position, panel.origin = "play", start, start
    panel.hand = module.Hand(start, module.Side.WHITE, (1, 2))
    panel.hand.play(panel.hand.legal()[0])
    panel.rebuild()

    async def click(interaction: MagicMock) -> None:
        await panel._confirm(interaction)

    return click


async def press_backgammon_accept(client: BotClient, owner: MagicMock) -> Click:
    cog, module = backgammon_cog(client)
    challenger = make_owner()
    challenger.id = owner.id + 1
    duel = module.Duel(
        cog, guild_id=GUILD_ID, channel=None, challenger=challenger, rival=owner, stake=10
    )

    async def click(interaction: MagicMock) -> None:
        await duel.accept(interaction)
        if duel.timer is not None:
            duel.timer.cancel()

    return click


async def press_backgammon_double(client: BotClient, owner: MagicMock) -> Click:
    cog, module = backgammon_cog(client)
    rival = make_owner()
    rival.id = owner.id + 1
    duel = module.Duel(
        cog, guild_id=GUILD_ID, channel=None, challenger=owner, rival=rival, stake=10
    )
    duel.phase = module.Phase.PLAYING
    duel.stage = module.Stage.DECIDE
    duel.turn = module.Side.WHITE

    async def click(interaction: MagicMock) -> None:
        await duel.double(interaction)
        if duel.timer is not None:
            duel.timer.cancel()

    return click


async def press_enraya_accept(client: BotClient, owner: MagicMock) -> Click:
    cog, module = enraya_cog(client)
    challenger = make_owner()
    challenger.id = owner.id + 1
    duel = module.Duel(
        cog, guild_id=GUILD_ID, channel=None, challenger=challenger, rival=owner, stake=10
    )

    async def click(interaction: MagicMock) -> None:
        await duel.accept(interaction)
        if duel.timer is not None:
            duel.timer.cancel()

    return click


def bus_view(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """Mesa del autobús con dibujo falso, sin esperas y con cartas fijas (todas rojas)."""
    cog = client.get_cog("Autobús")
    module = module_of(client, "Autobús")
    module.REVEAL_MARGIN_SECONDS = 0
    media = module.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
    cog.renderer = MagicMock()
    cog.renderer.reveal = AsyncMock(return_value=module.Reveal(win=media, lose=media))
    cog.renderer.board = AsyncMock(return_value=b"PNG")
    view = module.BusView(cog, guild_id=GUILD_ID, owner=owner, stake=10)
    view.deck = (module.Card(7, 1),) * 5
    return view, module


async def press_bus_pick(client: BotClient, owner: MagicMock) -> Click:
    view, module = bus_view(client, owner)

    async def click(interaction: MagicMock) -> None:
        await view._pick(interaction, module.Pick.RED)

    return click


async def press_bus_cash_out(client: BotClient, owner: MagicMock) -> Click:
    view, module = bus_view(client, owner)
    # Un acierto ya en marcha (la apuesta cobrada antes de medir).
    await view.play(module.Pick.RED, AsyncMock())

    async def click(interaction: MagicMock) -> None:
        await view._cash_out(interaction)

    return click


def tower_board(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """Torre de Fácil con 10 Y$ y un dragón en la puerta 0 de cada piso."""
    module = module_of(client, "Torre")
    board = module.TowerBoard(
        client.get_cog("Torre"),
        guild_id=GUILD_ID,
        owner=owner,
        stake=10,
        difficulty=module.Difficulty.EASY,
    )
    return board


async def press_tower_again(client: BotClient, owner: MagicMock) -> Click:
    board = tower_board(client, owner)

    async def click(interaction: MagicMock) -> None:
        await board._again(interaction)

    return click


async def started_tower(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """Torre con la apuesta ya cobrada (antes de medir) y el primer piso subido."""
    board = tower_board(client, owner)
    assert await board.start() is None
    assert board.game is not None
    board.game.dragons = tuple(frozenset({0}) for _ in board.game.dragons)
    board.game.climb(1)
    return board


async def press_tower_cash_out(client: BotClient, owner: MagicMock) -> Click:
    board = await started_tower(client, owner)

    async def click(interaction: MagicMock) -> None:
        await board._cash_out(interaction)

    return click


async def press_tower_burn(client: BotClient, owner: MagicMock) -> Click:
    board = await started_tower(client, owner)

    async def click(interaction: MagicMock) -> None:
        await board._climb(interaction, 0)

    return click


def allatu_board(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """Plató de Allá tú con 10 Y$ en la tabla Normal (de texto: construido a mano)."""
    module = module_of(client, "Allá tú")
    return module.AllatuBoard(
        client.get_cog("Allá tú"),
        guild_id=GUILD_ID,
        owner=owner,
        stake=10,
        table=module.Table.NORMAL,
    )


async def press_allatu_again(client: BotClient, owner: MagicMock) -> Click:
    board = allatu_board(client, owner)

    async def click(interaction: MagicMock) -> None:
        await board._again(interaction)

    return click


async def offered_allatu(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """Plató con la apuesta cobrada (antes de medir) y la primera oferta de la banca."""
    board = allatu_board(client, owner)
    assert await board.start() is None
    game = board.game
    assert game is not None
    game.choose(0)
    while game.offer is None:
        game.open(game.openable[0])
    return board


async def press_allatu_deal(client: BotClient, owner: MagicMock) -> Click:
    board = await offered_allatu(client, owner)

    async def click(interaction: MagicMock) -> None:
        await board._deal(interaction)

    return click


async def press_allatu_keep(client: BotClient, owner: MagicMock) -> Click:
    board = await offered_allatu(client, owner)
    game = board.game
    while game.other is None:
        if game.offer is not None:
            game.no_deal()
        else:
            game.open(game.openable[0])

    async def click(interaction: MagicMock) -> None:
        await board._keep(interaction)

    return click


def poker_table(client: BotClient):  # noqa: ANN201
    """Mesa del póker del canal con dibujo falso, sin esperas y sin bucle propio."""
    cog = client.get_cog("Póker")
    module = module_of(client, "Póker")
    module.REVEAL_MARGIN_SECONDS = 0
    media = module.Media(gif=b"GIF", png=b"PNG", seconds=0.0)
    cog.renderer = MagicMock()
    cog.renderer.deal = AsyncMock(return_value=media)
    cog.renderer.showdown = AsyncMock(return_value=media)
    cog.sleep = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 5
    channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return module.Table(cog, guild_id=GUILD_ID, channel=channel, png=b"PNG", pot=0)


async def press_poker_sit(client: BotClient, owner: MagicMock) -> Click:
    table = poker_table(client)

    async def click(interaction: MagicMock) -> None:
        await table.sit_click(interaction)

    return click


async def press_poker_call(client: BotClient, owner: MagicMock) -> Click:
    table = poker_table(client)
    # Sentado y con las cartas repartidas antes de medir.
    await table.sit(owner)
    await table.deal()

    async def click(interaction: MagicMock) -> None:
        await table.call_click(interaction)

    return click


async def press_poker_bonus(client: BotClient, owner: MagicMock) -> Click:
    table = poker_table(client)

    async def click(interaction: MagicMock) -> None:
        await table.toggle_bonus(interaction)

    return click


async def press_poker_club(client: BotClient, owner: MagicMock) -> Click:
    table = poker_table(client)

    async def click(interaction: MagicMock) -> None:
        await table.club_panel(interaction)

    return click


async def press_poker_mode(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Póker")
    module = module_of(client, "Póker")
    cog.start = MagicMock()
    cog.renderer = MagicMock()
    cog.renderer.board = AsyncMock(return_value=b"PNG")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 9
    channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    picker = module.ModePicker(cog, owner, "100", channel=channel)

    async def click(interaction: MagicMock) -> None:
        await picker.choose(module.BANCA, interaction)

    return click


async def press_timba_sit(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Timba")
    module = module_of(client, "Timba")
    cog.renderer = MagicMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 6
    table = module.Table(cog, guild_id=GUILD_ID, channel=channel, big_blind=20, png=b"PNG")

    async def click(interaction: MagicMock) -> None:
        await table.sit_click(interaction)

    return click


def dice_view(client: BotClient, owner: MagicMock, *rolls: tuple[int, int]):  # noqa: ANN201
    """Mesa de los dados con dibujo falso, sin esperas y con el azar de guion."""
    cog = client.get_cog("Dados")
    module = module_of(client, "Dados")
    module.REVEAL_MARGIN_SECONDS = 0
    cog.renderer = MagicMock()
    cog.renderer.throw = AsyncMock(
        return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0, rest=module.OPENING_REST)
    )
    cog.renderer.board = AsyncMock(return_value=b"PNG")
    cog.rng = DiceScripted(*rolls)
    cog.ahead = False
    return module.CrapsView(cog, guild_id=GUILD_ID, owner=owner, stake=10, bet=Bet.PASS)


async def press_dice_pass(client: BotClient, owner: MagicMock) -> Click:
    view = dice_view(client, owner, (3, 4))

    async def click(interaction: MagicMock) -> None:
        await view._pass(interaction)

    return click


async def press_dice_roll(client: BotClient, owner: MagicMock) -> Click:
    view = dice_view(client, owner, (1, 5), (3, 4))
    # El punto ya puesto (la apuesta cobrada antes de medir).
    await view.play(Bet.PASS, AsyncMock())

    async def click(interaction: MagicMock) -> None:
        await view._roll(interaction)

    return click


async def press_dice_odds(client: BotClient, owner: MagicMock) -> Click:
    view = dice_view(client, owner, (1, 5))
    await view.play(Bet.PASS, AsyncMock())

    async def click(interaction: MagicMock) -> None:
        await view._odds_one(interaction)

    return click


async def press_dice_odds_max(client: BotClient, owner: MagicMock) -> Click:
    view = dice_view(client, owner, (1, 5))
    await view.play(Bet.PASS, AsyncMock())

    async def click(interaction: MagicMock) -> None:
        await view._odds_max(interaction)

    return click


def dice_table(client: BotClient, owner: MagicMock, *rolls: tuple[int, int]):  # noqa: ANN201
    """Mesa compartida de los dados del bot real, con dibujo falso, sin esperas y azar de guion."""
    cog = client.get_cog("Dados")
    module = module_of(client, "Dados")
    cog.renderer = MagicMock()
    cog.renderer.throw = AsyncMock(
        return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0, rest=module.OPENING_REST)
    )
    cog.renderer.board = AsyncMock(return_value=b"PNG")
    cog.rng = DiceScripted(*rolls)
    cog.ahead = False

    async def no_wait(seconds: float) -> None:
        return None

    cog.sleep = no_wait
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 7
    table = module.CrapsTable(cog, guild_id=GUILD_ID, channel=channel, png=b"PNG")
    table.open_round()
    return table


async def press_dice_table_bet(client: BotClient, owner: MagicMock) -> Click:
    table = dice_table(client, owner)

    async def click(interaction: MagicMock) -> None:
        await table.bet_callback(Bet.DONT)(interaction)

    return click


async def press_dice_table_repeat(client: BotClient, owner: MagicMock) -> Click:
    table = dice_table(client, owner)
    table.cog.last_bets[(GUILD_ID, OWNER_ID)] = (Bet.PASS, 10)

    async def click(interaction: MagicMock) -> None:
        await table.repeat(interaction)

    return click


async def press_dice_table_double(client: BotClient, owner: MagicMock) -> Click:
    table = dice_table(client, owner)

    async def click(interaction: MagicMock) -> None:
        await table.double(interaction)

    return click


async def press_dice_table_odds(client: BotClient, owner: MagicMock) -> Click:
    table = dice_table(client, owner, (1, 5))
    # El punto ya puesto (la apuesta cobrada antes de medir).
    table.place(owner, Bet.PASS, 10)
    await table.roll()

    async def click(interaction: MagicMock) -> None:
        await table.odds(interaction)

    return click


async def press_dice_table_modal(client: BotClient, owner: MagicMock) -> Click:
    table = dice_table(client, owner)
    modal = FichaModal(table)
    modal.amount._value = "50"  # lo que Discord rellena al enviar

    async def click(interaction: MagicMock) -> None:
        await modal.on_submit(interaction)

    return click


def sports_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """El panel de `deportes` con un partido de mañana en memoria."""
    import time

    from bot.services.sports import SPORTS, Match, Pick

    cog = client.get_cog("Deportes")
    odds = {Pick.HOME: 185, Pick.DRAW: 360, Pick.AWAY: 410}
    match = Match("e1", SPORTS[0].key, "Real Madrid", "Barcelona", time.time() + 86_400, odds)
    cog.matches[SPORTS[0].key] = [match]
    view = module_of(client, "Deportes").SportsView(cog, owner_id=OWNER_ID, sport=SPORTS[0].key)
    return view, match


async def press_sports_stake(client: BotClient, owner: MagicMock) -> Click:
    view, match = sports_panel(client, owner)
    modal = module_of(client, "Deportes").StakeModal(view, match, "1")
    modal.amount._value = "50"  # lo que Discord rellena al enviar

    async def click(interaction: MagicMock) -> None:
        interaction.channel_id = 77
        await modal.on_submit(interaction)

    return click


async def press_sports_combo(client: BotClient, owner: MagicMock) -> Click:
    view, match = sports_panel(client, owner)
    from dataclasses import replace

    other = replace(match, event_id="e2", home="Betis", away="Sevilla")
    view.cog.matches[match.sport] = [match, other]
    legs = [("e1", "1", 185), ("e2", "X", 360)]
    modal = module_of(client, "Deportes").ComboStakeModal(view, legs)
    modal.amount._value = "50"  # lo que Discord rellena al enviar

    async def click(interaction: MagicMock) -> None:
        interaction.channel_id = 77
        await modal.on_submit(interaction)

    return click


async def press_sports_slip(client: BotClient, owner: MagicMock) -> Click:
    view, _ = sports_panel(client, owner)
    button = next(
        c for c in view.walk_children() if getattr(c, "custom_id", None) == "deportes:mis"
    )

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


def predice_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """El panel de `predice` en la ficha de una pregunta, con Polymarket sustituido."""
    import json
    import time
    from dataclasses import replace
    from types import SimpleNamespace

    from bot.services.polymarket import parse_event
    from bot.services.predice import build_sections

    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "polymarket" / "elecciones.json")
        .read_text(encoding="utf-8")
    )  # fmt: skip
    base = next(
        e
        for e in map(parse_event, raw)
        if e is not None and e.title.startswith("Spain General Election: PP + Vox")
    )
    now = time.time()
    end = now + 3 * 86_400
    market = replace(
        base.markets[0], end=end, game_start=None, bid=0.61, ask=0.63, prices=(0.62, 0.38)
    )
    event = replace(base, start=None, end=end, markets=(market,))
    cog = client.get_cog("Predice")
    cog.offers = build_sections({"espana": [event]}, now=now)
    cog.offers_at = now
    cog.client = SimpleNamespace(quote=AsyncMock(return_value=(0.61, 0.63)))
    view = module_of(client, "Predice").PredictView(cog, owner_id=OWNER_ID, section="espana")
    view.event_id = event.id
    view.rebuild()
    return view, event, market


async def press_predice_stake(client: BotClient, owner: MagicMock) -> Click:
    from bot.services.predice import options

    view, _, market = predice_panel(client, owner)
    option = options(market, market.mid)[0]
    modal = module_of(client, "Predice").StakeModal(view, view.selected(), market, option)
    modal.amount._value = "50"  # lo que Discord rellena al enviar

    async def click(interaction: MagicMock) -> None:
        interaction.channel_id = 77
        await modal.on_submit(interaction)

    return click


async def press_predice_slip(client: BotClient, owner: MagicMock) -> Click:
    view, _, _ = predice_panel(client, owner)
    view.event_id = None  # el botón está en la portada
    view.rebuild()
    button = next(c for c in view.walk_children() if getattr(c, "custom_id", None) == "predice:mis")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_roulette(client: BotClient, owner: MagicMock) -> Click:
    module = module_of(client, "Casino")
    module.REVEAL_MARGIN_SECONDS = 0  # el doble del dibujo ya dice que la rueda gira en 0 s
    table = module.RouletteTable(client.get_cog("Casino"), guild_id=GUILD_ID, owner=owner, stake=1)

    async def click(interaction: MagicMock) -> None:
        await table.choose(interaction, module.OUTSIDE_BETS["red"])

    return click


def blackjack_module(client: BotClient):  # noqa: ANN201
    """El módulo del blackjack, sin esperar al final del GIF ni pintar por adelantado."""
    module = module_of(client, "Blackjack")
    module.REVEAL_MARGIN_SECONDS = 0
    client.get_cog("Blackjack").ahead = False  # no es lo que se mide
    return module


async def press_blackjack(client: BotClient, owner: MagicMock) -> Click:
    table = blackjack_module(client).BlackjackTable(
        client.get_cog("Blackjack"), guild_id=GUILD_ID, owner=owner, stake=1
    )

    async def click(interaction: MagicMock) -> None:
        await table._deal_again(interaction)

    return click


async def press_blackjack_insurance(client: BotClient, owner: MagicMock) -> Click:
    table = blackjack_module(client).BlackjackTable(
        client.get_cog("Blackjack"), guild_id=GUILD_ID, owner=owner, stake=2
    )
    # Banca con un as a la vista: la mano espera a que se decida el seguro.
    shoe = [Card(rank, 0) for rank in (13, 10, 1, 10)]
    table.game = BlackjackGame(stake=2, shoe=shoe)
    table.game.deal()

    async def click(interaction: MagicMock) -> None:
        await table.insure(interaction, take=True)

    return click


def war_table(client: BotClient, owner: MagicMock, *, tied: bool):  # noqa: ANN201
    """Mesa de la Guerra con ficha de 2; si `tied`, con un empate por decidir."""
    module = module_of(client, "Guerra")
    module.REVEAL_MARGIN_SECONDS = 0  # sin esperar al final del GIF
    client.get_cog("Guerra").ahead = False  # sin pintar por adelantado: no es lo que se mide
    table = module.WarTable(client.get_cog("Guerra"), guild_id=GUILD_ID, owner=owner, stake=2)
    if tied:
        table.game = WarGame(stake=2, shoe=[Card(7, 0)] * 6 + [Card(9, 1), Card(9, 0)])
        table.game.deal()
        table._paid = False
    return table


async def press_war_deal(client: BotClient, owner: MagicMock) -> Click:
    table = war_table(client, owner, tied=False)

    async def click(interaction: MagicMock) -> None:
        await table._deal_again(interaction)

    return click


async def press_war_war(client: BotClient, owner: MagicMock) -> Click:
    table = war_table(client, owner, tied=True)

    async def click(interaction: MagicMock) -> None:
        await table._war(interaction)

    return click


async def press_war_surrender(client: BotClient, owner: MagicMock) -> Click:
    table = war_table(client, owner, tied=True)

    async def click(interaction: MagicMock) -> None:
        await table._surrender(interaction)

    return click


async def business_panel(client: BotClient, owner: MagicMock, *, opened: bool = True):  # noqa: ANN201
    """Panel del negocio con un negocio montado (o sin él) y dinero de sobra."""
    cog = client.get_cog("Negocio")
    await client.economy.repository.apply(GUILD_ID, owner.id, [_entry()])
    if opened:
        await cog.service.open(
            GUILD_ID, owner.id, sector="cafeteria", barrio="triana", name="Bar", palette=0,
            emblem=0,
        )  # fmt: skip
        await cog.service.buy(GUILD_ID, owner.id, "cafe", 5)
    panel = module_of(client, "Negocio").NegocioPanel(
        cog, guild_id=GUILD_ID, owner=owner, channel=MagicMock(send=AsyncMock())
    )
    cog.panels.add(panel)
    await panel.refresh()
    return panel


def _entry():  # noqa: ANN202
    from bot.repositories.economy import LedgerEntry

    return LedgerEntry(5_000_000, "prueba")


async def press_business_from_home(client: BotClient, owner: MagicMock) -> Click:
    panel = module_of(client, "Trabajo").PalaPanel(
        client.get_cog("Trabajo"), guild_id=GUILD_ID, owner=owner
    )

    async def click(interaction: MagicMock) -> None:
        interaction.message = MagicMock(edit=AsyncMock())
        await panel._business(interaction)

    return click


async def press_pala_home(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._go_home_screen(interaction)

    return click


async def press_pala_jornada(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._jornada(interaction, "j:3")

    return click


async def press_business_open(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner, opened=False)
    panel.draft.update(sector="tienda", barrio="ofra", name="Súper")

    async def click(interaction: MagicMock) -> None:
        await panel._open(interaction)

    return click


async def press_business_buy(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    panel.product = "zumo"

    async def click(interaction: MagicMock) -> None:
        await panel._buy(1)(interaction)

    return click


async def press_business_turn(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._start(interaction)
        if panel._timer is not None:
            panel._timer.cancel()

    return click


async def press_business_price(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._set_price(interaction, "caro")

    return click


async def press_business_close(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._close(interaction)

    return click


async def press_business_screen(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._almacen(interaction)

    return click


async def press_business_equipment(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    panel.equipment = "cafetera2"

    async def click(interaction: MagicMock) -> None:
        await panel._install(interaction)

    return click


async def press_business_campaign(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    panel.campaign = "buzoneo"

    async def click(interaction: MagicMock) -> None:
        await panel._launch(interaction)

    return click


async def press_business_product_price(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    panel.product = "cafe"

    async def click(interaction: MagicMock) -> None:
        await panel._set_product_price(interaction, "caro")

    return click


async def press_business_traslado(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._traslado(interaction)

    return click


async def press_business_move(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    panel.move = {"barrio": "schamann", "size": 1, "own": ""}

    async def click(interaction: MagicMock) -> None:
        await panel._do_move(interaction)

    return click


async def press_business_constitute(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    panel.capital = 100

    async def click(interaction: MagicMock) -> None:
        await panel._constitute(interaction)

    return click


async def press_business_company_money(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    await client.get_cog("Negocio").service.constitute(GUILD_ID, owner.id, 100)

    async def click(interaction: MagicMock) -> None:
        await panel.company_action(interaction, "aportar", "1000")

    return click


async def staffed_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """Panel del negocio con una persona contratada y elegida en 👥 Plantilla."""
    panel = await business_panel(client, owner)
    service = client.get_cog("Negocio").service
    business = await service.business(GUILD_ID, owner.id)
    candidate = service.candidates(business)[0]
    jornada = 2 if candidate["part_time"] else 4
    _business, employee = await service.hire(
        GUILD_ID, owner.id, candidate["key"], contract="indefinido", jornada=jornada,
        shift="manana",
    )  # fmt: skip
    panel.employee = employee["id"]
    return panel


async def press_business_staff(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._plantilla(interaction)

    return click


async def press_business_hire(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    service = client.get_cog("Negocio").service
    business = await service.business(GUILD_ID, owner.id)
    candidate = service.candidates(business)[0]
    panel.candidate = candidate["key"]
    panel.offer.update(jornada=2 if candidate["part_time"] else 4)

    async def click(interaction: MagicMock) -> None:
        await panel._hire(interaction)

    return click


async def press_business_fire(client: BotClient, owner: MagicMock) -> Click:
    panel = await staffed_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._fire("prueba")(interaction)

    return click


async def press_business_course(client: BotClient, owner: MagicMock) -> Click:
    panel = await staffed_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._course(interaction, "acierto")

    return click


async def press_business_raise(client: BotClient, owner: MagicMock) -> Click:
    panel = await staffed_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._raise(interaction, "0.05")

    return click


async def press_business_ric(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    await client.get_cog("Negocio").service.constitute(GUILD_ID, owner.id, 100)

    async def click(interaction: MagicMock) -> None:
        await panel._ric(interaction, "0.5")

    return click


async def press_pala_hire(client: BotClient, owner: MagicMock) -> Click:
    panel = module_of(client, "Trabajo").PalaPanel(
        client.get_cog("Trabajo"), guild_id=GUILD_ID, owner=owner
    )

    async def click(interaction: MagicMock) -> None:
        await panel._hire(interaction, "obra")

    return click


async def press_pala_study(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Trabajo")
    await cog.service.hire(GUILD_ID, owner.id, "funcionariado")
    panel = module_of(client, "Trabajo").PalaPanel(cog, guild_id=GUILD_ID, owner=owner)

    async def click(interaction: MagicMock) -> None:
        await panel._clock_in_study(interaction)
        if panel._timer is not None:
            panel._timer.cancel()

    return click


async def politics_panel(client: BotClient, owner: MagicMock, level: int, branch: str):  # noqa: ANN201
    """Panel de pala con un contrato de política en ese puesto y rama."""
    cog = client.get_cog("Trabajo")
    await cog.service.hire(GUILD_ID, owner.id, "politica")
    contract = await cog.service.repository.contract(GUILD_ID, owner.id)
    contract.level, contract.branch = level, branch
    await cog.service.repository.save_contract(GUILD_ID, owner.id, contract)
    panel = module_of(client, "Trabajo").PalaPanel(cog, guild_id=GUILD_ID, owner=owner)
    panel.channel = MagicMock(send=AsyncMock())
    return panel


async def press_pala_branch(client: BotClient, owner: MagicMock) -> Click:
    panel = await politics_panel(client, owner, 5, "")

    async def click(interaction: MagicMock) -> None:
        await panel._accept_branch("moncloa")(interaction)

    return click


async def press_pala_retire_offer(client: BotClient, owner: MagicMock) -> Click:
    panel = await politics_panel(client, owner, 10, "moncloa")

    async def click(interaction: MagicMock) -> None:
        await panel._retire_offer(interaction)

    return click


async def press_pala_retire(client: BotClient, owner: MagicMock) -> Click:
    panel = await politics_panel(client, owner, 10, "moncloa")

    async def click(interaction: MagicMock) -> None:
        await panel._retire(interaction)

    return click


async def press_pala_pension(client: BotClient, owner: MagicMock) -> Click:
    panel = await politics_panel(client, owner, 10, "bruselas")
    await panel.service.retire(GUILD_ID, owner.id)

    async def click(interaction: MagicMock) -> None:
        await panel._claim_pension(interaction)

    return click


async def obra_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """Panel de pala con un contrato de obra."""
    cog = client.get_cog("Trabajo")
    await cog.service.hire(GUILD_ID, owner.id, "obra")
    panel = module_of(client, "Trabajo").PalaPanel(cog, guild_id=GUILD_ID, owner=owner)
    panel.channel = MagicMock(send=AsyncMock())
    return panel


async def press_pala_favor(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._favor(interaction, "tupper")

    return click


async def press_pala_gift(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)
    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    items = await client.shop.items(GUILD_ID)
    flores = next(item for item in items if item.catalog_key == "flores_mama")
    await client.shop.grant(GUILD_ID, owner.id, flores, 0.0)

    async def click(interaction: MagicMock) -> None:
        await panel._give_gift(interaction, "flores_mama")

    return click


async def press_pala_gift_shop(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._gift_shop(interaction)

    return click


async def press_pets_food_shop(client: BotClient, owner: MagicMock) -> Click:
    await client.pets.initialize()
    panel = await client.get_cog("Mascotas").panel(owner.guild, owner, owner)
    callback = panel._shop("animales", "todo")

    async def click(interaction: MagicMock) -> None:
        await callback(interaction)

    return click


async def press_pala_buy_gift(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._buy_gift(interaction, "flores_mama")

    return click


async def press_pala_pay_gift(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)
    await client.economy.grant(GUILD_ID, owner.id, amount=10_000, reason="prueba")
    interaction = fake_interaction(owner)
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.id = GUILD_ID
    await panel._buy_gift(interaction, "flores_mama")

    async def click(interaction: MagicMock) -> None:
        await panel._pay_gift(interaction)

    return click


async def press_pala_dodge(client: BotClient, owner: MagicMock) -> Click:
    panel = await obra_panel(client, owner)
    panel.pending_event = module_of(client, "Trabajo").EVENT_BY_KEY["quedarse"]

    async def click(interaction: MagicMock) -> None:
        await panel._dodge("quedarse")(interaction)

    return click


async def press_checkout(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    (item, *_rest) = await client.shop.items(GUILD_ID)

    async def click(interaction: MagicMock) -> None:
        await tienda.open_checkout(interaction, item.id)

    return click


async def _shop_item(client: BotClient) -> object:
    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    (item, *_rest) = await client.shop.items(GUILD_ID)
    return item


async def press_shop_sheet(client: BotClient, owner: MagicMock) -> Click:
    """Elegir un artículo en el escaparate: la ficha lee la base de datos tras aceptar."""
    tienda = client.get_cog("Tienda")
    view = await tienda.storefront(owner.guild, owner)
    select = next(
        c for c in view.walk_children()
        if isinstance(c, discord.ui.Select) and c.custom_id == "tienda:elegir"
    )  # fmt: skip

    async def click(interaction: MagicMock) -> None:
        select._values = [select.options[0].value]  # lo que Discord rellena al elegir
        await select.callback(interaction)

    return click


async def press_shop_sheet_pay(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    item = await _shop_item(client)
    sheet = await ItemSheet.build(tienda, owner.guild, owner, item.id)  # type: ignore[attr-defined]
    assert sheet is not None

    async def click(interaction: MagicMock) -> None:
        await sheet.pay(interaction)

    return click


async def press_shop_wish(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    item = await _shop_item(client)
    sheet = await ItemSheet.build(tienda, owner.guild, owner, item.id)  # type: ignore[attr-defined]
    assert sheet is not None

    async def click(interaction: MagicMock) -> None:
        await sheet.wish(interaction)

    return click


async def press_shop_cart(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    view = await tienda.storefront(owner.guild, owner)

    async def click(interaction: MagicMock) -> None:
        await view.cart_button(interaction)

    return click


async def press_shop_cart_pay(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    item = await _shop_item(client)
    tienda.put_in_cart(GUILD_ID, OWNER_ID, item.id, 1)  # type: ignore[attr-defined]
    panel = CartPanel(tienda, owner.guild, owner)
    await panel.load()

    async def click(interaction: MagicMock) -> None:
        await panel.pay(interaction)

    return click


async def press_shop_mute(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    view = await tienda.storefront(owner.guild, owner)
    view.prefs.tab = WISH_TAB

    async def click(interaction: MagicMock) -> None:
        await view.toggle_mute(interaction)

    return click


async def press_shop_wishes_to_cart(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    item = await _shop_item(client)
    await tienda.toggle_wish(GUILD_ID, owner, item, None)
    view = await tienda.storefront(owner.guild, owner)

    async def click(interaction: MagicMock) -> None:
        await view.wishes_to_cart(interaction)

    return click


async def press_open_lootbox(client: BotClient, owner: MagicMock) -> Click:
    """Abrir una caja desde la mochila: el clic se acepta antes de gastarla y sortearla."""
    tienda = client.get_cog("Tienda")
    tienda.lootbox_pause = 0  # sin esperar entre fotogramas
    await tienda.stock_up(GUILD_ID)
    box = await client.shop.item_by_key(GUILD_ID, "caja_regalo")
    assert box is not None
    await client.shop.grant(GUILD_ID, OWNER_ID, box, 0.0)
    backpack = await tienda.backpack_view(owner.guild, owner, owner)

    async def click(interaction: MagicMock) -> None:
        await tienda.start_use(interaction, backpack, box.id)

    return click


async def press_wear_title(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    item = await client.shop.item_by_key(GUILD_ID, "verificado")
    assert item is not None
    await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
    backpack = await tienda.backpack_view(owner.guild, owner, owner)
    select = next(
        c for c in backpack.walk_children()
        if isinstance(c, discord.ui.Select) and c.placeholder == "🎖️ Qué título luces"
    )  # fmt: skip

    async def click(interaction: MagicMock) -> None:
        select._values = ["verificado"]  # lo que Discord rellena al elegir
        interaction.data = {"values": ["verificado"]}
        await select.callback(interaction)

    return click


SELLER_ID = 20


async def verified_in_backpack(client: BotClient, user_id: int) -> int:
    """Da a `user_id` la «Cuenta verificada» (se vende en el Rastro) y devuelve la entrada."""
    await client.get_cog("Tienda").stock_up(GUILD_ID)
    item = await client.shop.item_by_key(GUILD_ID, "verificado")
    assert item is not None
    entry, _dupe = await client.shop.grant(GUILD_ID, user_id, item, 0.0)
    return entry.id


async def rastro_stall(client: BotClient) -> int:
    """Un puesto del Rastro de otro miembro; devuelve su id (ya en la memoria del cog)."""
    cog = client.get_cog("Rastro")
    entry = await verified_in_backpack(client, SELLER_ID)
    listing = await client.rastro.open_stall(
        GUILD_ID, SELLER_ID, entry, start_price=10, channel_id=None, now=cog.clock()
    )
    cog.listings[listing.id] = listing
    return listing.id


async def press_rastro_accept(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    cog = client.get_cog("Rastro")
    entry_id = await verified_in_backpack(client, SELLER_ID)
    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(100_000, "test")])
    entry = next(
        e for e in await client.shop.inventory(GUILD_ID, SELLER_ID, cog.clock())
        if e.id == entry_id
    )  # fmt: skip
    seller = MagicMock(spec=discord.Member)
    seller.id, seller.bot, seller.display_name = SELLER_ID, False, "Ana"
    view = module_of(client, "Rastro").OfferView(cog, GUILD_ID, seller, owner, entry, 100)

    async def click(interaction: MagicMock) -> None:
        await view.accept.callback(interaction)

    return click


async def press_rastro_swap(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    cog = client.get_cog("Rastro")
    given = await verified_in_backpack(client, SELLER_ID)
    book = await client.shop.item_by_key(GUILD_ID, "manual_resistencia")
    assert book is not None
    received, _dupe = await client.shop.grant(GUILD_ID, OWNER_ID, book, 0.0)
    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(100_000, "test")])
    await client.economy.repository.apply(GUILD_ID, SELLER_ID, [LedgerEntry(100_000, "test")])
    seller_entries = await client.shop.inventory(GUILD_ID, SELLER_ID, cog.clock())
    owner_entries = await client.shop.inventory(GUILD_ID, OWNER_ID, cog.clock())
    mine = next(e for e in seller_entries if e.id == given)
    theirs = next(e for e in owner_entries if e.id == received.id)
    seller = MagicMock(spec=discord.Member)
    seller.id, seller.bot, seller.display_name = SELLER_ID, False, "Ana"
    view = module_of(client, "Rastro").SwapView(cog, GUILD_ID, seller, owner, mine, theirs, 0)

    async def click(interaction: MagicMock) -> None:
        await view.accept.callback(interaction)

    return click


async def press_rastro_bid(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    cog = client.get_cog("Rastro")
    listing_id = await rastro_stall(client)
    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(100_000, "test")])
    form = module_of(client, "Rastro").BidForm(cog, cog.listings[listing_id])

    async def click(interaction: MagicMock) -> None:
        await form.on_submit(interaction)

    return click


async def press_rastro_withdraw(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Rastro")
    entry = await verified_in_backpack(client, OWNER_ID)
    listing = await client.rastro.open_stall(
        GUILD_ID, OWNER_ID, entry, start_price=10, channel_id=None, now=cog.clock()
    )
    cog.listings[listing.id] = listing
    button = module_of(client, "Rastro").WithdrawButton(listing.id)

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def bought_in_shop(client: BotClient, user_id: int) -> int:
    """Compra en la tienda un coleccionable para `user_id` (lo acepta el Monte) y da su entrada."""
    from bot.repositories.economy import LedgerEntry
    from bot.services.shop import Kind, quote

    now = client.get_cog("Rastro").clock()
    item = await client.shop.create_item(
        GUILD_ID, kind=Kind.TROPHY, name="Guitarra", emoji="🎸", description="", price=10_000,
        igic="general", now=now,
    )  # fmt: skip
    price = quote(item, now)
    await client.economy.repository.apply(GUILD_ID, user_id, [LedgerEntry(price.total, "test")])
    await client.economy.purchase(
        GUILD_ID, user_id, base=price.base, tax=price.tax, concept="objeto",
        reserve=client.shop.reserve(GUILD_ID, user_id, item.id, expected=price, level=99, now=now),
    )  # fmt: skip
    found = await client.shop.inventory(GUILD_ID, user_id, now)
    return next(e.id for e in found if e.item_id == item.id)


async def banco_panel(client: BotClient, owner: MagicMock, tab: str):  # noqa: ANN201
    """El panel de `banco` ya pintado (leer la base al prepararlo no cuenta)."""
    panel = module_of(client, "Banco").BankPanel(client.get_cog("Banco"), GUILD_ID, owner, tab)
    await panel.screen()
    return panel


def banco_payroll(client: BotClient) -> None:
    """Nóminas de los últimos días, para que el banco vea ingresos."""
    connection = client.housing._connect()  # noqa: SLF001
    try:
        connection.execute(
            "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
            "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', 180000, 0, 0, 0, "
            "180000, ?)",
            (GUILD_ID, OWNER_ID, client.get_cog("Banco").clock() - 86_400),
        )
        connection.commit()
    finally:
        connection.close()


def child(panel, kind: type, custom_id: str | None = None):  # noqa: ANN001, ANN201
    return next(
        c for c in panel.children
        if isinstance(c, kind) and (custom_id is None or c.custom_id == custom_id)
    )  # fmt: skip


async def press_banco_tab(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_panel(client, owner, "loans")
    button = child(panel, discord.ui.Button, "banco:tab:books")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_banco_term(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_panel(client, owner, "loans")
    select = child(panel, discord.ui.Select, "banco:plazo")

    async def click(interaction: MagicMock) -> None:
        select._values = ["12"]  # lo que Discord rellena al elegir
        await select.callback(interaction)

    return click


async def press_banco_insurance(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_panel(client, owner, "loans")
    button = child(panel, discord.ui.Button, "banco:seguro")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_banco_amount(client: BotClient, owner: MagicMock) -> Click:
    banco_payroll(client)
    panel = await banco_panel(client, owner, "loans")
    form = module_of(client, "Banco").AmountForm(panel, panel.most)

    async def click(interaction: MagicMock) -> None:
        form.amount._value = "5000"  # lo que Discord rellena al escribir
        await form.on_submit(interaction)

    return click


async def press_banco_sign(client: BotClient, owner: MagicMock) -> Click:
    from bot.services import loans as lending

    banco_payroll(client)
    panel = await banco_panel(client, owner, "loans")
    desk, _income = await panel.cog.housing.loan_desk(GUILD_ID, OWNER_ID)
    rate = lending.loan_rate(desk.euribor, lending.profile_factors(desk.profile, insured=False))
    panel.quote = lending.quote(5_000, 12, rate, insured=False)
    await panel.screen()
    button = child(panel, discord.ui.Button, "banco:firmar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_banco_repay(client: BotClient, owner: MagicMock) -> Click:
    from bot.services import loans as lending

    banco_payroll(client)
    service = client.get_cog("Banco").housing
    desk, _income = await service.loan_desk(GUILD_ID, OWNER_ID)
    rate = lending.loan_rate(desk.euribor, lending.profile_factors(desk.profile, insured=False))
    signed, _ = await service.take_loan(
        GUILD_ID, OWNER_ID, principal=5_000, months=12, insured=False, rate=rate
    )
    panel = await banco_panel(client, owner, "loans")
    panel.selected_loan = signed.loan.id
    await panel.screen()
    button = child(panel, discord.ui.Button, "banco:amortizar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def banco_business_panel(client: BotClient, owner: MagicMock, *, signed: bool = False):  # noqa: ANN201
    """El panel de `banco` en 🏪 Negocio, con un negocio montado, nómina y (o no) un préstamo."""
    await business_panel(client, owner)
    banco_payroll(client)
    panel = await banco_panel(client, owner, "business")
    if signed:
        found = await client.negocio.loan_desk(GUILD_ID, owner.id)
        assert found is not None
        await client.negocio.take_loan(
            GUILD_ID, owner.id, principal=5_000, months=12, rate=found[1].rate
        )
        await panel.screen()
    return panel


async def press_banco_business_tab(client: BotClient, owner: MagicMock) -> Click:
    await business_panel(client, owner)
    panel = await banco_panel(client, owner, "loans")
    button = child(panel, discord.ui.Button, "banco:tab:business")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_banco_business_term(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_business_panel(client, owner)
    select = child(panel, discord.ui.Select, "banco:negocio_plazo")

    async def click(interaction: MagicMock) -> None:
        select._values = ["12"]  # lo que Discord rellena al elegir
        await select.callback(interaction)

    return click


async def press_banco_business_amount(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_business_panel(client, owner)
    form = module_of(client, "Banco").negocio_tab.AmountForm(panel, panel.business_most)

    async def click(interaction: MagicMock) -> None:
        form.amount._value = "5000"  # lo que Discord rellena al escribir
        await form.on_submit(interaction)

    return click


async def press_banco_business_sign(client: BotClient, owner: MagicMock) -> Click:
    from bot.services import loans as lending

    panel = await banco_business_panel(client, owner)
    found = await client.negocio.loan_desk(GUILD_ID, owner.id)
    assert found is not None
    panel.business_offer = lending.quote(5_000, 12, found[1].rate, insured=False)
    await panel.screen()
    button = child(panel, discord.ui.Button, "banco:negocio_firmar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_banco_business_repay(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_business_panel(client, owner, signed=True)
    button = child(panel, discord.ui.Button, "banco:negocio_amortizar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_business_bank(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._bank(interaction)

    return click


async def press_business_chain(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._cadena(interaction)

    return click


async def press_business_pick_local(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    service = client.get_cog("Negocio").service
    other, _fee = await service.open(
        GUILD_ID, owner.id, sector="tienda", barrio="ofra", name="Súper", palette=0, emblem=0
    )

    async def click(interaction: MagicMock) -> None:
        await panel._pick_local(interaction, str(other.id))

    return click


async def press_business_nave(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._nave(interaction)

    return click


async def press_business_open_nave(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    panel.nave_pick.update(barrio="telde", size=0)

    async def click(interaction: MagicMock) -> None:
        await panel._open_nave(interaction)

    return click


async def press_business_import(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    await client.get_cog("Negocio").service.open_nave(GUILD_ID, owner.id, barrio="telde", size=0)
    panel.nave_pick["product"] = "cafe"

    async def click(interaction: MagicMock) -> None:
        await panel._import(interaction)

    return click


async def press_business_sell(client: BotClient, owner: MagicMock) -> Click:
    panel = await business_panel(client, owner)
    service = client.get_cog("Negocio").service
    await service.constitute(GUILD_ID, owner.id, 100)
    business = await service.business(GUILD_ID, owner.id)
    business.company["results"] = [100_000, 100_000, 100_000]
    await service.repository.save(GUILD_ID, business)
    panel.screen = "venta"
    await panel.refresh()

    async def click(interaction: MagicMock) -> None:
        await panel._sell(interaction)

    return click


async def garantias_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """El panel de `banco` en 🔐 Garantías con un inmueble pagado al contado y nómina."""
    banco_payroll(client)
    await pisos_owned(client, "taco_bajo")
    return await banco_panel(client, owner, "guarantees")


async def press_garantia_pick(client: BotClient, owner: MagicMock) -> Click:
    panel = await garantias_panel(client, owner)
    select = child(panel, discord.ui.Select, "banco:garantia")

    async def click(interaction: MagicMock) -> None:
        select._values = ["taco_bajo"]  # lo que Discord rellena al elegir
        await select.callback(interaction)

    return click


async def press_garantia_term(client: BotClient, owner: MagicMock) -> Click:
    panel = await garantias_panel(client, owner)
    select = child(panel, discord.ui.Select, "banco:garantia_plazo")

    async def click(interaction: MagicMock) -> None:
        select._values = ["120"]
        await select.callback(interaction)

    return click


async def press_garantia_amount(client: BotClient, owner: MagicMock) -> Click:
    panel = await garantias_panel(client, owner)
    form = module_of(client, "Banco").garantias.AmountForm(panel, panel.remortgage_most)

    async def click(interaction: MagicMock) -> None:
        form.amount._value = "20000"
        await form.on_submit(interaction)

    return click


async def press_garantia_sign(client: BotClient, owner: MagicMock) -> Click:
    from bot.services import loans as lending

    panel = await garantias_panel(client, owner)
    desk, _income = await panel.cog.housing.remortgage_desk(GUILD_ID, OWNER_ID)
    (found,) = desk.collaterals
    quote = lending.remortgage_quote(20_000, 360, desk.rate, found.value)
    panel.remortgage_offer = (quote, found.key, found.title)
    await panel.screen()
    button = child(panel, discord.ui.Button, "banco:garantia_firmar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


def guarantor_mock() -> MagicMock:
    """Una avalista del servidor (no es el dueño del panel)."""
    bea = MagicMock(spec=discord.Member)
    bea.id, bea.bot, bea.display_name, bea.mention = 77, False, "Bea", "<@77>"
    return bea


async def press_aval_button(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_panel(client, owner, "loans")
    button = child(panel, discord.ui.Button, "banco:avalista")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_aval_pick(client: BotClient, owner: MagicMock) -> Click:
    panel = await banco_panel(client, owner, "loans")
    panel.picking_guarantor = True
    await panel.screen()
    select = child(panel, discord.ui.UserSelect, "banco:avalista_elige")

    async def click(interaction: MagicMock) -> None:
        select._values = [guarantor_mock()]  # lo que Discord rellena al elegir
        await select.callback(interaction)

    return click


async def press_aval_ask(client: BotClient, owner: MagicMock) -> Click:
    from bot.services import loans as lending

    banco_payroll(client)
    panel = await banco_panel(client, owner, "loans")
    panel.guarantor = guarantor_mock()
    panel.quote = lending.quote(5_000, 12, 0.07, insured=False)
    await panel.screen()
    button = child(panel, discord.ui.Button, "banco:firmar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_aval_accept(client: BotClient, owner: MagicMock) -> Click:
    from bot.services import loans as lending

    banco_payroll(client)
    module = module_of(client, "Banco")
    panel = await banco_panel(client, owner, "loans")
    request = module.avales.Request(loan=lending.quote(5_000, 12, 0.07, insured=False))
    bea = guarantor_mock()
    view = module.avales.GuaranteeView(panel, request, bea)

    async def click(interaction: MagicMock) -> None:
        interaction.user = bea
        await view.accept.callback(interaction)

    return click


def pisos_let(client: BotClient, key: str) -> None:
    """Alquila un inmueble a una familia de la gestora, con contrato para rato."""
    connection = client.housing._connect()  # noqa: SLF001
    try:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2099-01-01' WHERE guild_id = ? AND listing = ?",
            (GUILD_ID, key),
        )
        connection.commit()
    finally:
        connection.close()


async def anticipos_panel(client: BotClient, owner: MagicMock, *, signed: bool = False):  # noqa: ANN201
    """El panel de `banco` en 💸 Anticipos con un inmueble alquilado (y un anticipo firmado)."""
    await pisos_owned(client, "taco_bajo")
    pisos_let(client, "taco_bajo")
    if signed:
        service = client.get_cog("Banco").housing
        desk = await service.advance_desk(GUILD_ID, OWNER_ID)
        await service.advance(GUILD_ID, OWNER_ID, "taco_bajo", months=3, rate=desk.rate)
    return await banco_panel(client, owner, "advances")


async def press_anticipo_pick(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipos_panel(client, owner)
    select = child(panel, discord.ui.Select, "banco:anticipo")

    async def click(interaction: MagicMock) -> None:
        select._values = ["taco_bajo"]  # lo que Discord rellena al elegir
        await select.callback(interaction)

    return click


async def press_anticipo_term(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipos_panel(client, owner)
    select = child(panel, discord.ui.Select, "banco:anticipo_plazo")

    async def click(interaction: MagicMock) -> None:
        select._values = ["6"]
        await select.callback(interaction)

    return click


async def press_anticipo_ask(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipos_panel(client, owner)
    button = child(panel, discord.ui.Button, "banco:anticipo_pedir")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def anticipo_offer(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """El panel con la ficha de un anticipo a 12 meses enseñada."""
    from bot.services import loans as lending

    panel = await anticipos_panel(client, owner)
    desk = await panel.cog.housing.advance_desk(GUILD_ID, OWNER_ID)
    (found,) = desk.shares
    panel.advance_offer = (lending.advance_quote(found.payment, 12, desk.rate), found.key, "Bajo")
    await panel.screen()
    return panel


async def press_anticipo_sign(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipo_offer(client, owner)
    button = child(panel, discord.ui.Button, "banco:anticipo_firmar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_anticipo_back(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipo_offer(client, owner)
    button = child(panel, discord.ui.Button, "banco:anticipo_volver")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_anticipo_list(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipos_panel(client, owner, signed=True)
    select = child(panel, discord.ui.Select, "banco:anticipo_lista")

    async def click(interaction: MagicMock) -> None:
        select._values = [select.options[0].value]
        await select.callback(interaction)

    return click


async def press_anticipo_desist(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipos_panel(client, owner, signed=True)
    button = child(panel, discord.ui.Button, "banco:anticipo_desistir")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_anticipo_repay(client: BotClient, owner: MagicMock) -> Click:
    panel = await anticipos_panel(client, owner, signed=True)
    button = child(panel, discord.ui.Button, "banco:anticipo_cancelar")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_pisos_advance(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    pisos_let(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "gestion"

    async def click(interaction: MagicMock) -> None:
        await panel._advance(interaction)

    return click


async def vender_panel(client: BotClient, owner: MagicMock, selected: int | None = None):  # noqa: ANN201
    """El panel de `vender` ya pintado, con `selected` elegido (leerlo no cuenta)."""
    panel = module_of(client, "Rastro").VenderPanel(client.get_cog("Rastro"), GUILD_ID, owner)
    panel.selected = selected
    await panel.screen()
    return panel


async def press_vender_item(client: BotClient, owner: MagicMock) -> Click:
    entry = await bought_in_shop(client, OWNER_ID)
    panel = await vender_panel(client, owner)
    select = child(panel, discord.ui.Select, "vender:objeto")

    async def click(interaction: MagicMock) -> None:
        select._values = [str(entry)]
        await select.callback(interaction)

    return click


async def press_vender_monte(client: BotClient, owner: MagicMock, custom_id: str) -> Click:
    entry = await bought_in_shop(client, OWNER_ID)
    panel = await vender_panel(client, owner, entry)
    button = child(panel, discord.ui.Button, custom_id)

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_vender_sell(client: BotClient, owner: MagicMock) -> Click:
    return await press_vender_monte(client, owner, "vender:monte")


async def press_vender_pawn(client: BotClient, owner: MagicMock) -> Click:
    return await press_vender_monte(client, owner, "vender:empenar")


async def press_vender_redeem(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Rastro")
    entry = await bought_in_shop(client, OWNER_ID)
    ticket, _ = await client.economy.monte(
        GUILD_ID, client.monte.pawn(GUILD_ID, OWNER_ID, entry, cog.clock())
    )
    cog.tickets[ticket.id] = ticket
    panel = await vender_panel(client, owner)
    button = child(panel, discord.ui.Button, f"vender:desempenar:{ticket.id}")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_vender_stall(client: BotClient, owner: MagicMock) -> Click:
    entry = await bought_in_shop(client, OWNER_ID)
    panel = await vender_panel(client, owner, entry)
    form = module_of(client, "Rastro").StallForm(panel)

    async def click(interaction: MagicMock) -> None:
        interaction.channel.id = 99  # el canal del anuncio, que se guarda con el puesto
        form.price._value = "5000"  # lo que Discord rellena al escribir
        await form.on_submit(interaction)

    return click


async def press_vender_member(client: BotClient, owner: MagicMock) -> Click:
    entry = await bought_in_shop(client, OWNER_ID)
    panel = await vender_panel(client, owner, entry)
    form = module_of(client, "Rastro").MemberForm(panel)
    buyer = MagicMock(spec=discord.Member)
    buyer.id, buyer.bot, buyer.display_name = SELLER_ID, False, "Ana"
    buyer.mention = f"<@{SELLER_ID}>"

    async def click(interaction: MagicMock) -> None:
        form.buyer._values = [buyer]  # lo que Discord rellena al elegir
        form.price._value = "5000"
        await form.on_submit(interaction)

    return click


def pisos_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    cog = client.get_cog("Pisos")
    painter = MagicMock()
    painter.paint = AsyncMock(side_effect=lambda states: [b"PNG"] * len(states))
    painter.close = AsyncMock()
    cog.painter.scene = painter
    return module_of(client, "Pisos").Panel(cog, guild=owner.guild, owner=owner)


async def press_pisos_barrio(client: BotClient, owner: MagicMock) -> Click:
    panel = pisos_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"values": ["schamann"]}
        await panel._pick_barrio(interaction)

    return click


async def press_pisos_pay(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(1_000_000, "test")])
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen, panel.pending = "taco_trastero", "caja", (False, 10)

    async def click(interaction: MagicMock) -> None:
        await panel._pay(interaction)

    return click


async def press_pisos_level(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(1_000_000, "test")])
    cog = client.get_cog("Pisos")
    await cog.service.buy(GUILD_ID, OWNER_ID, "taco_trastero", 100)
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_trastero", "ficha"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"values": ["mercado"]}
        await panel._pick_level(interaction)

    return click


async def press_pisos_mortgage(client: BotClient, owner: MagicMock) -> Click:
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen, panel.pending = "taco_trastero", "caja", (False, 10)

    async def click(interaction: MagicMock) -> None:
        await panel._toggle_mortgage(interaction)

    return click


async def press_pisos_vpo_signup(client: BotClient, owner: MagicMock) -> Click:
    panel = pisos_panel(client, owner)
    panel.screen = "vpo"

    async def click(interaction: MagicMock) -> None:
        await panel._vpo_signup(interaction)

    return click


async def press_pisos_license(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(10_000_000, "test")])
    cog = client.get_cog("Pisos")
    await cog.service.buy(GUILD_ID, OWNER_ID, "taco_bajo", 100)
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen, panel.confirm = "taco_bajo", "ficha", "licencia"

    async def click(interaction: MagicMock) -> None:
        await panel._license(interaction)

    return click


async def pisos_owned(client: BotClient, key: str) -> None:
    from bot.repositories.economy import LedgerEntry

    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(10_000_000, "test")])
    await client.get_cog("Pisos").service.buy(GUILD_ID, OWNER_ID, key, 100)


def pisos_signed_long_ago(client: BotClient, key: str) -> None:
    """El contrato lleva más de un año (12 días) firmado: se puede pedir para vivir."""
    connection = client.housing._connect()  # noqa: SLF001
    try:
        connection.execute(
            "UPDATE housing_leases SET signed = '2026-01-01' WHERE guild_id = ? AND listing = ?",
            (GUILD_ID, key),
        )
        connection.commit()
    finally:
        connection.close()


async def _keys_panel(client: BotClient, owner: MagicMock):  # noqa: ANN202
    """El panel en 🔑 Recuperar el piso de un bajo alquilado a una familia de la gestora."""
    await pisos_owned(client, "taco_bajo")
    pisos_let(client, "taco_bajo")
    pisos_signed_long_ago(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "llaves"
    return panel


async def press_pisos_keys_open(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    pisos_let(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "ficha"

    async def click(interaction: MagicMock) -> None:
        await panel._go_keys(interaction)

    return click


async def press_pisos_keys_free(client: BotClient, owner: MagicMock) -> Click:
    panel = await _keys_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._keys_free(interaction)

    return click


async def press_pisos_no_renew(client: BotClient, owner: MagicMock) -> Click:
    panel = await _keys_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._toggle_no_renew(interaction)

    return click


async def press_pisos_need(client: BotClient, owner: MagicMock) -> Click:
    panel = await _keys_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await panel._claim_need(interaction)

    return click


async def press_pisos_term(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "gestion"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"values": ["24"]}
        await panel._pick_term(interaction)

    return click


async def press_pisos_member_keys(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    await pisos_owned(client, "taco_bajo")
    bea = guarantor_mock()
    await client.economy.repository.apply(GUILD_ID, bea.id, [LedgerEntry(1_000_000, "test")])
    cog = client.get_cog("Pisos")
    await cog.service.rent_to_member(GUILD_ID, OWNER_ID, "taco_bajo", bea.id)
    module = module_of(client, "Pisos")
    listing = (await cog.service.market(GUILD_ID)).listing("taco_bajo")
    view = module.MemberKeysOffer(cog, owner.guild, owner, bea, listing, 10_000, 60)

    async def click(interaction: MagicMock) -> None:
        interaction.user = bea
        await view.accept.callback(interaction)

    return click


async def press_pisos_pick(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "gestion"

    async def click(interaction: MagicMock) -> None:
        await panel._toggle_pick(interaction)

    return click


async def press_pisos_page(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry
    from bot.services.housing_catalog import LISTINGS

    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(10**9, "test")])
    for listing in LISTINGS[:11]:  # una más de las que caben: dos páginas
        await client.get_cog("Pisos").service.buy(GUILD_ID, OWNER_ID, listing.key, 1)
    panel = pisos_panel(client, owner)
    panel.screen = "cartera"

    async def click(interaction: MagicMock) -> None:
        await panel._page_next(interaction)

    return click


async def press_pisos_remortgage(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "gestion"

    async def click(interaction: MagicMock) -> None:
        await panel._remortgage(interaction)

    return click


async def press_pisos_insurance(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen, panel.confirm = "taco_bajo", "gestion", "seguro"

    async def click(interaction: MagicMock) -> None:
        await panel._toggle_insurance(interaction)

    return click


async def press_pisos_evict(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    with sqlite3.connect(client.database_path) as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'impago', tenant = 'rider', rent = 3000, unpaid = 3"
        )
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "ficha"

    async def click(interaction: MagicMock) -> None:
        await panel._evict(interaction)

    return click


async def press_pisos_bid(client: BotClient, owner: MagicMock) -> Click:
    from bot.repositories.economy import LedgerEntry

    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(10_000_000, "test")])
    cog = client.get_cog("Pisos")
    seller = OWNER_ID + 1
    await client.economy.repository.apply(GUILD_ID, seller, [LedgerEntry(10_000_000, "test")])
    await cog.service.buy(GUILD_ID, seller, "taco_bajo", 100)
    auction = await cog.service.create_auction(GUILD_ID, seller, "taco_bajo", 100, 10_000)
    panel = pisos_panel(client, owner)
    panel.screen = "subastas"

    async def click(interaction: MagicMock) -> None:
        await panel.place_bid(interaction, auction.id, 20_000)

    return click


async def press_pisos_reform(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen, panel.confirm = "taco_bajo", "reformas", "reforma:pintura"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"custom_id": "pisos:reforma:pintura:si"}
        await panel._reform(interaction)

    return click


async def press_pisos_split(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "isleta_piso3")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "isleta_piso3", "dividir"
    panel.confirm = "dividir:habitaciones"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"custom_id": "pisos:dividir:habitaciones:si"}
        await panel._split(interaction)

    return click


async def press_pisos_join_rooms(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "isleta_piso3")
    cog = client.get_cog("Pisos")
    await cog.service.split(GUILD_ID, OWNER_ID, "isleta_piso3", "habitaciones")
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen, panel.confirm = "isleta_piso3", "plantas", "juntar"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"custom_id": "pisos:juntar:si"}
        await panel._join_rooms(interaction)

    return click


async def press_pisos_decide(client: BotClient, owner: MagicMock) -> Click:
    await pisos_owned(client, "taco_bajo")
    with sqlite3.connect(client.database_path) as connection:
        connection.execute(
            "INSERT INTO housing_decisions (guild_id, listing, event, cost, day, until) "
            "VALUES (?, 'taco_bajo', 'averia', 3000, '2026-10-14', '2099-01-01')",
            (GUILD_ID,),
        )
        (decision,) = connection.execute("SELECT id FROM housing_decisions").fetchone()
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "imprevistos"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"custom_id": f"pisos:decidir:{decision}:tecnico"}
        await panel._decide(interaction)

    return click


async def pisos_shared(client: BotClient) -> None:
    """`taco_bajo` a medias entre el dueño del panel (60) y otro miembro (40): hay junta."""
    from bot.repositories.economy import LedgerEntry

    cog = client.get_cog("Pisos")
    for member, shares in ((OWNER_ID, 60), (OWNER_ID + 1, 40)):
        await client.economy.repository.apply(GUILD_ID, member, [LedgerEntry(10_000_000, "t")])
        await cog.service.buy(GUILD_ID, member, "taco_bajo", shares)


async def press_pisos_vote(client: BotClient, owner: MagicMock) -> Click:
    await pisos_shared(client)
    vote = await client.get_cog("Pisos").service.propose(
        GUILD_ID, OWNER_ID + 1, "taco_bajo", "renta", "mercado"
    )
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "junta"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"custom_id": f"pisos:junta:si:{vote.id}"}
        await panel._vote(interaction)

    return click


async def press_pisos_propose(client: BotClient, owner: MagicMock) -> Click:
    await pisos_shared(client)
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen = "taco_bajo", "junta"

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"values": ["seguro:1"]}
        await panel._propose(interaction)

    return click


async def press_pisos_divide(client: BotClient, owner: MagicMock) -> Click:
    await pisos_shared(client)
    panel = pisos_panel(client, owner)
    panel.listing, panel.screen, panel.confirm = "taco_bajo", "junta", "division"

    async def click(interaction: MagicMock) -> None:
        await panel._divide(interaction)

    return click


async def granate_in_backpack(client: BotClient) -> None:
    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    item = await client.shop.item_by_key(GUILD_ID, "skin_ruleta_granate")
    assert item is not None
    await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)


async def press_choose_skin(client: BotClient, owner: MagicMock) -> Click:
    await granate_in_backpack(client)
    tienda = client.get_cog("Tienda")
    backpack = await tienda.backpack_view(owner.guild, owner, owner)
    select = next(
        c for c in backpack.walk_children()
        if isinstance(c, discord.ui.Select) and c.placeholder == "🎨 Cambiar la skin de una mesa"
    )  # fmt: skip

    async def click(interaction: MagicMock) -> None:
        select._values = ["ruleta"]  # lo que Discord rellena al elegir
        interaction.data = {"values": ["ruleta"]}
        await select.callback(interaction)

    return click


async def press_table_skin_button(client: BotClient, owner: MagicMock) -> Click:
    await granate_in_backpack(client)
    button = module_of(client, "Tienda").skin_button(client, "ruleta")
    assert button is not None

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def pieles_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    """El panel de `pieles` del dueño, con el tapete propio y una imagen ya puesta."""
    import io

    from PIL import Image

    from bot.services.skin_images import prepare
    from bot.services.skins import SKIN_BY_KEY

    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    item = await client.shop.item_by_key(GUILD_ID, "skin_tapete_propio")
    assert item is not None
    await client.shop.grant(GUILD_ID, OWNER_ID, item, 0.0)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), "purple").save(buffer, format="PNG")
    skin = SKIN_BY_KEY["tapete_propio"]
    await tienda.set_skin_image(GUILD_ID, OWNER_ID, skin, prepare(buffer.getvalue()), set_by=1)
    panel = await client.get_cog("Pieles").open_panel(owner.guild, owner, owner)
    buttons = {c.label: c for c in panel.walk_children() if isinstance(c, discord.ui.Button)}
    return panel, buttons, buffer.getvalue()


async def press_pieles_remove(client: BotClient, owner: MagicMock) -> Click:
    _panel, buttons, _png = await pieles_panel(client, owner)

    async def click(interaction: MagicMock) -> None:
        await buttons["🗑️ Quitar imagen"].callback(interaction)

    return click


async def press_pieles_upload(client: BotClient, owner: MagicMock) -> Click:
    panel, _buttons, png = await pieles_panel(client, owner)
    module = module_of(client, "Pieles")
    form = module.ImageForm(panel, module.custom_skins()[0])
    upload = MagicMock(spec=discord.Attachment)
    upload.size = len(png)
    upload.read = AsyncMock(return_value=png)
    form.upload._values = [upload]  # lo que rellena Discord al enviar

    async def click(interaction: MagicMock) -> None:
        await form.on_submit(interaction)

    return click


async def press_casino_stats(client: BotClient, owner: MagicMock) -> Click:
    view = module_of(client, "Apuestas").StatsView(
        client.get_cog("Apuestas"), owner=owner, guild=owner.guild, member=owner
    )

    async def click(interaction: MagicMock) -> None:
        await view.show(interaction, page="resumen")

    return click


async def press_ranking(client: BotClient, owner: MagicMock) -> Click:
    view = await client.get_cog("Achievements").build_view(owner.guild, OWNER_ID, owner)

    async def click(interaction: MagicMock) -> None:
        await view.ranking.callback(interaction)

    return click


async def press_renta(client: BotClient, owner: MagicMock) -> Click:

    async def click(interaction: MagicMock) -> None:
        await client.get_cog("Renta").present(interaction)

    return click


async def press_renta_open(client: BotClient, owner: MagicMock) -> Click:
    # Una declaración cerrada y sin abrir: `renta` la enseña en un sobre.
    with client.economy.repository._transaction() as connection:  # noqa: SLF001
        connection.execute(
            "INSERT INTO economy_irpf_returns (guild_id, user_id, week_start, base, quota, "
            "deductions, withheld, result, created_at) VALUES (?, ?, '2026-10-05', 0, 0, 0, 0, "
            "0, 0)",
            (GUILD_ID, OWNER_ID),
        )
    view = await client.get_cog("Renta")._view(owner.guild, owner)  # noqa: SLF001
    (button,) = [c for c in view.walk_children() if getattr(c, "custom_id", "") == "renta:abrir"]

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def _donar_panel(client: BotClient, owner: MagicMock) -> object:
    cog = client.get_cog("Donaciones")
    panel = await cog.panel(owner.guild, owner)
    panel.page = module_of(client, "Donaciones").BY_KEY["mares"]
    panel.rebuild()
    return panel


async def press_donar_choose(client: BotClient, owner: MagicMock) -> Click:
    panel = await _donar_panel(client, owner)
    (menu,) = [c for c in panel.walk_children() if getattr(c, "custom_id", "") == "donar:ong"]

    async def click(interaction: MagicMock) -> None:
        interaction.data = {"values": ["hambre"]}
        await menu.callback(interaction)

    return click


async def press_donar_quick(client: BotClient, owner: MagicMock) -> Click:
    panel = await _donar_panel(client, owner)
    (button,) = [c for c in panel.walk_children() if getattr(c, "custom_id", "") == "donar:dar:500"]

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def _renta_button(client: BotClient, owner: MagicMock, custom_id: str) -> Click:
    """Un botón de la vista de `renta` con una declaración abierta que sale a pagar."""
    with client.economy.repository._transaction() as connection:  # noqa: SLF001
        connection.execute(
            "INSERT INTO economy_irpf_returns (guild_id, user_id, week_start, base, quota, "
            "deductions, withheld, result, created_at, opened, payment, pay_due, second_due) "
            "VALUES (?, ?, '2026-10-05', 0, 0, 0, 0, 500, 0, 1, 'pending', 9e9, 9e9)",
            (GUILD_ID, OWNER_ID),
        )
        connection.execute(
            "INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, 300)",
            (GUILD_ID, OWNER_ID),
        )
    view = await client.get_cog("Renta")._view(owner.guild, owner)  # noqa: SLF001
    (button,) = [c for c in view.walk_children() if getattr(c, "custom_id", "") == custom_id]

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_renta_pay(client: BotClient, owner: MagicMock) -> Click:
    return await _renta_button(client, owner, "renta:pagar")


async def press_renta_split(client: BotClient, owner: MagicMock) -> Click:
    return await _renta_button(client, owner, "renta:fraccionar")


async def press_renta_debt(client: BotClient, owner: MagicMock) -> Click:
    return await _renta_button(client, owner, "renta:liquidar")


async def press_renta_donate(client: BotClient, owner: MagicMock) -> Click:
    """Enviar el formulario de Donar del consejo de la Renta."""

    async def click(interaction: MagicMock) -> None:
        await client.get_cog("Renta").donate(interaction, "hambre", "100", suggested=0)

    return click


async def press_todo(client: BotClient, owner: MagicMock) -> Click:
    task = await client.todo.add_task(GUILD_ID, OWNER_ID, "probar los botones", Priority.ALTA)

    async def click(interaction: MagicMock) -> None:
        await client.get_cog("Lista").complete_from_menu(interaction, [task.id])

    return click


async def press_todo_page(client: BotClient, owner: MagicMock) -> Click:
    async def click(interaction: MagicMock) -> None:
        await client.get_cog("Lista").show_page(interaction, 1)

    return click


async def press_todo_priority(client: BotClient, owner: MagicMock) -> Click:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=MagicMock(id=600, channel=MagicMock(id=20)))
    picker = module_of(client, "Lista").PriorityPicker(
        client.get_cog("Lista"), owner, channel, "probar los botones"
    )

    async def click(interaction: MagicMock) -> None:
        await picker.choose(Priority.ALTA, interaction)

    return click


async def press_todo_edit(client: BotClient, owner: MagicMock) -> Click:
    task = await client.todo.add_task(GUILD_ID, OWNER_ID, "probar los botones", Priority.ALTA)

    async def click(interaction: MagicMock) -> None:
        interaction.message.edit = AsyncMock()
        await client.get_cog("Lista").open_edit_form(
            interaction, task.id, task.text, task.priority, OWNER_ID, 0
        )

    return click


async def press_todo_edit_save(client: BotClient, owner: MagicMock) -> Click:
    task = await client.todo.add_task(GUILD_ID, OWNER_ID, "probar los botones", Priority.ALTA)

    async def click(interaction: MagicMock) -> None:
        await client.get_cog("Lista").save_edit(interaction, task.id, "probar", Priority.BAJA)

    return click


async def admin_panel(client: BotClient, owner: MagicMock):  # noqa: ANN201
    await client.moderation.initialize()
    await client.last_seen.initialize()
    module = module_of(client, "Moderation")
    return module.AdminPanel(client.get_cog("Moderation"), owner.guild, owner, None)


async def press_admin_list(client: BotClient, owner: MagicMock) -> Click:
    panel = await admin_panel(client, owner)
    select = panel.children[1]

    async def click(interaction: MagicMock) -> None:
        select._values = ["sanciones"]  # lo que Discord rellena al elegir
        interaction.data = {"values": ["sanciones"]}
        await select.callback(interaction)

    return click


async def press_admin_note(client: BotClient, owner: MagicMock) -> Click:
    panel = await admin_panel(client, owner)
    panel.member = owner
    owner.guild.get_member.return_value = None  # se ha ido: el panel vuelve al servidor
    module = module_of(client, "Moderation")
    captured: list[discord.ui.Modal] = []
    opener = fake_interaction(owner)
    opener.response.send_modal = AsyncMock(side_effect=captured.append)
    await panel._add_note(opener)

    async def click(interaction: MagicMock) -> None:
        form = captured[0]
        assert isinstance(form, module._Form)
        form.fields[0]._value = "Sospechoso"
        await form.on_submit(interaction)

    return click


def perfil_view(client: BotClient, owner: MagicMock):  # noqa: ANN201
    module = module_of(client, "Perfil")
    return module.PerfilView(
        client.get_cog("Perfil"), guild=owner.guild, owner=owner, target=owner, channel=None
    )


async def press_perfil_section(client: BotClient, owner: MagicMock) -> Click:
    view = perfil_view(client, owner)
    select = view.children[0]

    async def click(interaction: MagicMock) -> None:
        select._values = ["rachas"]  # lo que Discord rellena al elegir
        interaction.data = {"values": ["rachas"]}
        await select.callback(interaction)

    return click


async def press_perfil_backpack(client: BotClient, owner: MagicMock) -> Click:
    view = perfil_view(client, owner)

    async def click(interaction: MagicMock) -> None:
        await view._backpack(interaction)

    return click


def bacara_table(client: BotClient):  # noqa: ANN201
    """Una mesa de bacará del bot real, abierta y sin pintar nada por adelantado."""
    cog = client.get_cog("Bacará")
    cog.ahead = False
    module = module_of(client, "Bacará")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 5
    table = module.Table(cog, guild_id=GUILD_ID, channel=channel, state=cog.shoe_for(5), png=b"PNG")
    table.open_round()
    return module, table


async def press_bacara_bet(client: BotClient, owner: MagicMock) -> Click:
    module, table = bacara_table(client)

    async def click(interaction: MagicMock) -> None:
        await table.bet_callback(module.Side.BANKER)(interaction)

    return click


async def press_bacara_repeat(client: BotClient, owner: MagicMock) -> Click:
    module, table = bacara_table(client)
    table.cog.last_bets[(GUILD_ID, OWNER_ID)] = {module.Side.PLAYER: 100, module.Side.TIE: 20}

    async def click(interaction: MagicMock) -> None:
        await table.repeat(interaction)

    return click


async def press_bacara_double(client: BotClient, owner: MagicMock) -> Click:
    module, table = bacara_table(client)
    await table.place(owner, module.Side.PLAYER, 100)

    async def click(interaction: MagicMock) -> None:
        await table.double(interaction)

    return click


async def _lottery_button(client: BotClient, owner: MagicMock, tab: str, custom_id: str) -> Click:
    """Un botón del panel de la lotería en la pestaña `tab`, sin pintar por adelantado."""
    cog = client.get_cog("Loteria")
    cog.ahead = False  # pintar por adelantado no es lo que se mide
    module = module_of(client, "Loteria")
    module.REVEAL_MARGIN_SECONDS = 0  # sin esperar al final del GIF
    panel = module.LotteryPanel(cog, owner.guild, owner)
    panel.tab = tab
    panel.rebuild()
    (button,) = [c for c in panel.walk_children() if getattr(c, "custom_id", "") == custom_id]

    async def click(interaction: MagicMock) -> None:
        interaction.channel_id = 5  # el canal del anuncio se guarda con la compra
        await button.callback(interaction)

    return click


async def press_lottery_scratch(client: BotClient, owner: MagicMock) -> Click:
    return await _lottery_button(client, owner, "rasca", "loteria:rascar:x10")


async def press_lottery_buy(client: BotClient, owner: MagicMock) -> Click:
    return await _lottery_button(client, owner, "primitiva", "loteria:rapido:primitiva:5")


async def press_lottery_tab(client: BotClient, owner: MagicMock) -> Click:
    return await _lottery_button(client, owner, "primitiva", "loteria:tab:inicio")


async def press_lottery_mine(client: BotClient, owner: MagicMock) -> Click:
    return await _lottery_button(client, owner, "inicio", "loteria:tab:mios")


async def entrada_panel(client: BotClient, owner: MagicMock, *, sound: bool = False):  # noqa: ANN201
    """El panel de `entrada` del dueño, con dinero y, si se pide, un sonido en la ranura 1."""
    from bot.repositories.economy import LedgerEntry
    from bot.services.entrance import Profile, Slot

    await client.economy.repository.apply(GUILD_ID, OWNER_ID, [LedgerEntry(1_000_000, "test")])
    cog = client.get_cog("Entrance")
    if sound:
        slot = Slot(1, "Bocina", 100, "normal", False, 0, 3000, 3000, 3000, "▁█▁")
        cog._profiles[(GUILD_ID, OWNER_ID)] = Profile().with_sound(slot)
    panel = module_of(client, "Entrance").EntrancePanel(cog, owner.guild, owner)
    return cog, panel


def entrada_button(panel, custom_id: str) -> discord.ui.Button:  # noqa: ANN001
    return next(
        c for c in panel.walk_children()
        if isinstance(c, discord.ui.Button) and c.custom_id == custom_id
    )  # fmt: skip


async def press_entrada_buy(client: BotClient, owner: MagicMock) -> Click:
    _cog, panel = await entrada_panel(client, owner)
    panel.page, panel.confirm = "shop", "comprar:ranura"  # Ya pulsó una vez.
    panel.rebuild()
    button = entrada_button(panel, "entrada:comprar:ranura")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_entrada_delete(client: BotClient, owner: MagicMock) -> Click:
    _cog, panel = await entrada_panel(client, owner, sound=True)
    panel.page, panel.slot, panel.confirm = "slot", 1, "borrar:1"
    panel.rebuild()
    button = entrada_button(panel, "entrada:borrar:1")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


async def press_entrada_mode(client: BotClient, owner: MagicMock) -> Click:
    _cog, panel = await entrada_panel(client, owner)
    button = entrada_button(panel, "entrada:modo")

    async def click(interaction: MagicMock) -> None:
        await button.callback(interaction)

    return click


CASES: dict[str, Press] = {
    "tragaperras: tirar": press_slots,
    "tragaperras: ×2": press_slots_double,
    "tragaperras: auto": press_slots_autoplay,
    "tragaperras: re-girar": press_slots_respin,
    "tragaperras: doble o nada": press_slots_gamble,
    "tragaperras: giro del día": press_slots_daily,
    "pachinko: lanzar": press_pachinko_launch,
    "pachinko: auto": press_pachinko_autoplay,
    "pico: picar": press_picar_dig,
    "pico: récords": press_picar_records,
    "moneda: cara": press_coin_flip,
    "moneda: cobrar": press_coin_cash_out,
    "gallos: pelear contra la casa": press_gallos_fight,
    "gallos: entrenar": press_gallos_train,
    "gallos: aceptar un duelo": press_gallos_accept,
    "4enraya: soltar ficha contra Jovani": press_enraya_drop,
    "4enraya: aceptar un duelo": press_enraya_accept,
    "backgammon: confirmar la jugada que gana": press_backgammon_confirm,
    "backgammon: aceptar un duelo": press_backgammon_accept,
    "backgammon: doblar": press_backgammon_double,
    "autobús: pedir carta": press_bus_pick,
    "autobús: cobrar": press_bus_cash_out,
    "torre: jugar otra": press_tower_again,
    "torre: cobrar": press_tower_cash_out,
    "torre: quemarse": press_tower_burn,
    "allatu: otra": press_allatu_again,
    "allatu: trato": press_allatu_deal,
    "allatu: quedarse": press_allatu_keep,
    "póker: sentarse": press_poker_sit,
    "póker: ir": press_poker_call,
    "póker: bote": press_poker_bonus,
    "póker: club": press_poker_club,
    "póker: elegir modo": press_poker_mode,
    "timba: sentarse": press_timba_sit,
    "dados: pase": press_dice_pass,
    "dados: tirar": press_dice_roll,
    "dados: odds": press_dice_odds,
    "dados: odds al máximo": press_dice_odds_max,
    "dados mesa: apostar": press_dice_table_bet,
    "dados mesa: repetir": press_dice_table_repeat,
    "dados mesa: ×2": press_dice_table_double,
    "dados mesa: odds": press_dice_table_odds,
    "dados mesa: ficha": press_dice_table_modal,
    "deportes: apostar en el formulario": press_sports_stake,
    "deportes: apostar una combinada": press_sports_combo,
    "deportes: mis apuestas": press_sports_slip,
    "predice: apostar en el formulario": press_predice_stake,
    "predice: mis predicciones": press_predice_slip,
    "ruleta: apostar": press_roulette,
    "blackjack: repartir": press_blackjack,
    "blackjack: seguro": press_blackjack_insurance,
    "guerra: repartir": press_war_deal,
    "guerra: a la guerra": press_war_war,
    "guerra: rendirse": press_war_surrender,
    "bacará: apostar": press_bacara_bet,
    "bacará: repetir": press_bacara_repeat,
    "bacará: doblar": press_bacara_double,
    "pala: elegir curro": press_pala_hire,
    "pala: estudiar la oposición": press_pala_study,
    "pala: elegir rama": press_pala_branch,
    "pala: ofrecer la jubilación": press_pala_retire_offer,
    "pala: jubilarse": press_pala_retire,
    "pala: cobrar la pensión": press_pala_pension,
    "pala: pedir un favor a la familia": press_pala_favor,
    "pala: dar un regalo": press_pala_gift,
    "pala: ir a los regalos de la tienda": press_pala_gift_shop,
    "mascota: atajo a la comida de la tienda": press_pets_food_shop,
    "pala: caja de un regalo": press_pala_buy_gift,
    "pala: comprar y dar un regalo": press_pala_pay_gift,
    "pala: excusa familiar": press_pala_dodge,
    "pala: volver al inicio": press_pala_home,
    "pala: elegir jornada": press_pala_jornada,
    "pala: abrir el negocio desde el inicio": press_business_from_home,
    "negocio: montarlo": press_business_open,
    "negocio: comprar al mayorista": press_business_buy,
    "negocio: abrir un turno": press_business_turn,
    "negocio: precios": press_business_price,
    "negocio: cerrarlo": press_business_close,
    "negocio: ir al almacén": press_business_screen,
    "negocio: instalar una mejora": press_business_equipment,
    "negocio: lanzar una campaña": press_business_campaign,
    "negocio: precio de un producto": press_business_product_price,
    "negocio: abrir el traslado": press_business_traslado,
    "negocio: trasladarse": press_business_move,
    "negocio: constituir la sociedad": press_business_constitute,
    "negocio: aportar a la sociedad": press_business_company_money,
    "negocio: abrir la plantilla": press_business_staff,
    "negocio: contratar": press_business_hire,
    "negocio: despedir": press_business_fire,
    "negocio: pagar un curso": press_business_course,
    "negocio: subir el sueldo": press_business_raise,
    "negocio: dotar la RIC": press_business_ric,
    "tienda: comprar": press_checkout,
    "tienda: ver la ficha": press_shop_sheet,
    "tienda: pagar en la ficha": press_shop_sheet_pay,
    "tienda: apuntar en la lista de deseos": press_shop_wish,
    "tienda: abrir el carrito": press_shop_cart,
    "tienda: pagar el carrito": press_shop_cart_pay,
    "tienda: silenciar los avisos de la lista": press_shop_mute,
    "tienda: lista de deseos al carrito": press_shop_wishes_to_cart,
    "rastro: aceptar una oferta": press_rastro_accept,
    "rastro: aceptar un trueque": press_rastro_swap,
    "rastro: pujar": press_rastro_bid,
    "rastro: retirar un puesto": press_rastro_withdraw,
    "banco: cambiar de mostrador": press_banco_tab,
    "banco: elegir plazo": press_banco_term,
    "banco: seguro": press_banco_insurance,
    "banco: pedir importe": press_banco_amount,
    "banco: firmar": press_banco_sign,
    "banco: amortizar": press_banco_repay,
    "banco: mostrador del negocio": press_banco_business_tab,
    "banco: plazo del préstamo al negocio": press_banco_business_term,
    "banco: importe del préstamo al negocio": press_banco_business_amount,
    "banco: firmar el préstamo al negocio": press_banco_business_sign,
    "banco: amortizar el préstamo del negocio": press_banco_business_repay,
    "negocio: abrir el banco": press_business_bank,
    "negocio: abrir la cadena": press_business_chain,
    "negocio: cambiar de local": press_business_pick_local,
    "negocio: abrir la nave": press_business_nave,
    "negocio: alquilar la nave": press_business_open_nave,
    "negocio: importar un contenedor": press_business_import,
    "negocio: vender la sociedad": press_business_sell,
    "banco: elegir inmueble para rehipotecar": press_garantia_pick,
    "banco: plazo de la rehipoteca": press_garantia_term,
    "banco: importe de la rehipoteca": press_garantia_amount,
    "banco: firmar la rehipoteca": press_garantia_sign,
    "pisos: rehipotecar": press_pisos_remortgage,
    "banco: avalista": press_aval_button,
    "banco: elegir avalista": press_aval_pick,
    "banco: pedir el aval": press_aval_ask,
    "aval: avalar": press_aval_accept,
    "banco: elegir inmueble para el anticipo": press_anticipo_pick,
    "banco: meses del anticipo": press_anticipo_term,
    "banco: adelantar rentas": press_anticipo_ask,
    "banco: firmar el anticipo": press_anticipo_sign,
    "banco: volver de la ficha del anticipo": press_anticipo_back,
    "banco: elegir anticipo": press_anticipo_list,
    "banco: desistir del anticipo": press_anticipo_desist,
    "banco: cancelar el anticipo": press_anticipo_repay,
    "pisos: adelantar rentas": press_pisos_advance,
    "vender: elegir objeto": press_vender_item,
    "vender: vender al Monte": press_vender_sell,
    "vender: empeñar": press_vender_pawn,
    "vender: desempeñar": press_vender_redeem,
    "vender: poner en el Rastro": press_vender_stall,
    "vender: ofrecer a un miembro": press_vender_member,
    "pisos: elegir barrio": press_pisos_barrio,
    "pisos: pasar de página en Mis pisos": press_pisos_page,
    "pisos: pagar en la caja": press_pisos_pay,
    "pisos: cambiar la renta": press_pisos_level,
    "pisos: caja con hipoteca": press_pisos_mortgage,
    "pisos: apuntarse a la VPO": press_pisos_vpo_signup,
    "pisos: pedir la licencia": press_pisos_license,
    "pisos: elegir yo al inquilino": press_pisos_pick,
    "pisos: contratar el seguro": press_pisos_insurance,
    "pisos: desahuciar con abogado": press_pisos_evict,
    "pisos: abrir Recuperar el piso": press_pisos_keys_open,
    "pisos: pedir las llaves por las buenas": press_pisos_keys_free,
    "pisos: no renovar": press_pisos_no_renew,
    "pisos: pedirlo para vivir": press_pisos_need,
    "pisos: duración del contrato": press_pisos_term,
    "pisos: aceptar las llaves entre miembros": press_pisos_member_keys,
    "pisos: pujar en una subasta": press_pisos_bid,
    "pisos: empezar una reforma": press_pisos_reform,
    "pisos: alquilar por habitaciones": press_pisos_split,
    "pisos: juntar las habitaciones": press_pisos_join_rooms,
    "pisos: decidir un imprevisto": press_pisos_decide,
    "pisos: votar en la junta": press_pisos_vote,
    "pisos: proponer a la junta": press_pisos_propose,
    "pisos: pedir la acción de división": press_pisos_divide,
    "tienda: lucir un título": press_wear_title,
    "tienda: abrir una caja": press_open_lootbox,
    "tienda: elegir skin": press_choose_skin,
    "mesa: botón 🎨": press_table_skin_button,
    "pieles: subir imagen": press_pieles_upload,
    "pieles: quitar imagen": press_pieles_remove,
    "apuestas: cambiar de página": press_casino_stats,
    "logros: ranking": press_ranking,
    "perfil: cambiar de sección": press_perfil_section,
    "admin: elegir lista": press_admin_list,
    "admin: apuntar nota": press_admin_note,
    "perfil: abrir la mochila": press_perfil_backpack,
    "renta: presentar": press_renta,
    "renta: abrir el sobre": press_renta_open,
    "renta: pagar todo": press_renta_pay,
    "renta: fraccionar": press_renta_split,
    "renta: liquidar deuda": press_renta_debt,
    "renta: donar desde el consejo": press_renta_donate,
    "donar: elegir ONG": press_donar_choose,
    "donar: donar 500": press_donar_quick,
    "lista: tachar": press_todo,
    "lista: pasar página": press_todo_page,
    "lista: elegir prioridad": press_todo_priority,
    "lista: abrir el formulario de editar": press_todo_edit,
    "lista: guardar lo editado": press_todo_edit_save,
    "lotería: rascar": press_lottery_scratch,
    "lotería: comprar apuestas": press_lottery_buy,
    "lotería: volver al cartel": press_lottery_tab,
    "lotería: mis boletos": press_lottery_mine,
    "entrada: comprar una mejora": press_entrada_buy,
    "entrada: borrar una ranura": press_entrada_delete,
    "entrada: cambiar el modo": press_entrada_mode,
}


@pytest.mark.parametrize("press", CASES.values(), ids=CASES.keys())
async def test_el_boton_contesta_antes_de_tocar_la_base_de_datos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, press: Press
) -> None:
    client = await load_bot(tmp_path)
    try:
        events: list[str] = []
        real_connect = sqlite_module.connect

        def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            events.append("db")
            return real_connect(*args, **kwargs)  # type: ignore[arg-type]

        owner = make_owner()
        click = await press(client, owner)
        monkeypatch.setattr(sqlite_module, "connect", connect)
        interaction = fake_interaction(owner, events=events)
        interaction.client = client
        interaction.guild = owner.guild
        interaction.guild_id = GUILD_ID
        interaction.channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.send = AsyncMock()

        await click(interaction)

        assert "db" in events, "el caso no toca la base de datos: no prueba nada"
        assert events[0].startswith("response."), events[:5]
    finally:
        await client.close()
