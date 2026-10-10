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
from bot.services.blackjack import BlackjackGame, Card
from bot.services.coin import Outcome, Side
from bot.services.craps import Bet
from bot.services.todo import Priority

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
    await client.work.repository.initialize()
    await client.horses.initialize()
    await client.porras.initialize()
    await client.todo.initialize()
    await client.slots_repository.initialize()
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
    module_of(client, "Tragaperras").REVEAL_MARGIN_SECONDS = 0  # sin esperar al final del GIF
    view = module_of(client, "Tragaperras").SlotMachineView(
        cog, guild_id=GUILD_ID, owner=owner, stake=1
    )

    async def click(interaction: MagicMock) -> None:
        await view.play(interaction)

    return click


async def press_slots_double(client: BotClient, owner: MagicMock) -> Click:
    cog = client.get_cog("Tragaperras")
    view = module_of(client, "Tragaperras").SlotMachineView(
        cog, guild_id=GUILD_ID, owner=owner, stake=1
    )

    async def click(interaction: MagicMock) -> None:
        await view._double_stake(interaction)

    return click


def slots_view(client: BotClient, owner: MagicMock):  # noqa: ANN201
    cog = client.get_cog("Tragaperras")
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


async def press_roulette(client: BotClient, owner: MagicMock) -> Click:
    module = module_of(client, "Casino")
    module.REVEAL_MARGIN_SECONDS = 0  # el doble del dibujo ya dice que la rueda gira en 0 s
    table = module.RouletteTable(client.get_cog("Casino"), guild_id=GUILD_ID, owner=owner, stake=1)

    async def click(interaction: MagicMock) -> None:
        await table.choose(interaction, module.OUTSIDE_BETS["red"])

    return click


async def press_blackjack(client: BotClient, owner: MagicMock) -> Click:
    table = module_of(client, "Blackjack").BlackjackTable(
        client.get_cog("Blackjack"), guild_id=GUILD_ID, owner=owner, stake=1
    )

    async def click(interaction: MagicMock) -> None:
        await table._deal_again(interaction)

    return click


async def press_blackjack_insurance(client: BotClient, owner: MagicMock) -> Click:
    table = module_of(client, "Blackjack").BlackjackTable(
        client.get_cog("Blackjack"), guild_id=GUILD_ID, owner=owner, stake=2
    )
    # Banca con un as a la vista: la mano espera a que se decida el seguro.
    shoe = [Card(rank, 0) for rank in (13, 10, 1, 10)]
    table.game = BlackjackGame(stake=2, shoe=shoe)
    table.game.deal()

    async def click(interaction: MagicMock) -> None:
        await table.insure(interaction, take=True)

    return click


async def press_pala_hire(client: BotClient, owner: MagicMock) -> Click:
    panel = module_of(client, "Trabajo").PalaPanel(
        client.get_cog("Trabajo"), guild_id=GUILD_ID, owner=owner
    )

    async def click(interaction: MagicMock) -> None:
        await panel._hire(interaction, "obra")

    return click


async def press_checkout(client: BotClient, owner: MagicMock) -> Click:
    tienda = client.get_cog("Tienda")
    await tienda.stock_up(GUILD_ID)
    (item, *_rest) = await client.shop.items(GUILD_ID)

    async def click(interaction: MagicMock) -> None:
        await tienda.open_checkout(interaction, item.id)

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


async def press_todo(client: BotClient, owner: MagicMock) -> Click:
    task = await client.todo.add_task(GUILD_ID, OWNER_ID, "probar los botones", Priority.ALTA)

    async def click(interaction: MagicMock) -> None:
        await client.get_cog("Lista").complete_from_menu(interaction, [task.id])

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


CASES: dict[str, Press] = {
    "tragaperras: tirar": press_slots,
    "tragaperras: ×2": press_slots_double,
    "tragaperras: auto": press_slots_autoplay,
    "tragaperras: re-girar": press_slots_respin,
    "tragaperras: doble o nada": press_slots_gamble,
    "tragaperras: giro del día": press_slots_daily,
    "pachinko: lanzar": press_pachinko_launch,
    "pachinko: auto": press_pachinko_autoplay,
    "moneda: cara": press_coin_flip,
    "moneda: cobrar": press_coin_cash_out,
    "autobús: pedir carta": press_bus_pick,
    "autobús: cobrar": press_bus_cash_out,
    "dados: pase": press_dice_pass,
    "dados: tirar": press_dice_roll,
    "dados: odds": press_dice_odds,
    "dados: odds al máximo": press_dice_odds_max,
    "ruleta: apostar": press_roulette,
    "blackjack: repartir": press_blackjack,
    "blackjack: seguro": press_blackjack_insurance,
    "pala: elegir curro": press_pala_hire,
    "tienda: comprar": press_checkout,
    "apuestas: cambiar de página": press_casino_stats,
    "logros: ranking": press_ranking,
    "perfil: cambiar de sección": press_perfil_section,
    "perfil: abrir la mochila": press_perfil_backpack,
    "renta: presentar": press_renta,
    "lista: tachar": press_todo,
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
