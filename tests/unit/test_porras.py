"""Porras: reparto parimutuel, propuestas, dinero con IAJ y derechos de imagen, y el panel."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import blackjack as blackjack_cog
from bot.cogs import bus as bus_cog
from bot.cogs import chicken as chicken_cog
from bot.cogs import coin as coin_cog
from bot.cogs import craps as craps_cog
from bot.cogs import mines as mines_cog
from bot.cogs.porras import Porras, Table, parse_text_args, resolve_game
from bot.repositories.economy import (
    PORRA_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    EconomyRepository,
    PorraCapError,
    PorraClosedError,
    PorraSideError,
)
from bot.repositories.porras import PorraRepository
from bot.services import casino_stats as casino_stats_module
from bot.services.achievements import (
    PORRA_GAME_PREFIX,
    PORRA_PROP_PREFIX,
    PORRA_STATS,
    porra_answer_stats,
    porra_bet_stats,
    porra_bettor_stats,
    porra_no_show_stats,
    porra_open_stats,
    porra_snoop_stats,
    porra_subject_stats,
)
from bot.services.casino_stats import Play
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.porras import (
    BINOCULARS_KEY,
    EXCLUDED_GAMES,
    IMAGE_SHARE,
    MAX_PLAYS,
    MAX_PLAYS_NOTEBOOK,
    NOTEBOOK_KEY,
    POOL_FACTOR,
    PROPOSITION_BY_KEY,
    PROPOSITIONS,
    Bet,
    Porra,
    Status,
    VoidReason,
    allowed_games,
    is_one_sided,
    max_plays,
    merge_bets,
    payout_multiplier,
    pool_cap,
    propositions_for,
    refund_split,
    split,
)
from bot.services.shop_catalog import CATALOG_BY_KEY
from bot.services.taxes import GAMING_TAX_RATE, gaming_tax, image_rights_withholding

GUILD = 1
LUIS, ANA, PEPE, MARI, JUAN = 10, 20, 30, 40, 50


def play(
    net: int, *, stake: int = 100, balance: int = 5_000, game: str = "minas", **details: int
) -> Play:
    return Play(
        game=game,
        stake=stake,
        payout=stake + net,
        tax=0,
        balance_after=balance,
        details=tuple(details.items()),
    )


def porra(**changes: object) -> Porra:
    base = Porra(
        id=1,
        guild_id=GUILD,
        channel_id=5,
        opener_id=LUIS,
        subject_id=ANA,
        game="minas",
        proposition="signo",
        plays=3,
        stake=100,
    )
    for key, value in changes.items():
        setattr(base, key, value)
    return base


# -- Reparto ---------------------------------------------------------------------------


def test_el_reparto_suma_justo_el_bote_y_cada_uno_paga_su_iaj() -> None:
    bets = [Bet(PEPE, 0, 1_000), Bet(MARI, 0, 333), Bet(JUAN, 1, 2_017)]
    sp = split(bets, 0)
    assert sp.pool == 1_000 + 333 + 2_017
    assert sp.image == sum(stake * IMAGE_SHARE // 100 for stake in (1_000, 333, 2_017))
    assert sp.payouts[JUAN] == 0
    # Los ganadores se reparten lo que queda en proporción a lo que pusieron.
    pot = sp.pool - sum(gaming_tax(s) for s in (1_000, 333, 2_017)) - sp.image
    assert sp.payouts[PEPE] == pot * 1_000 // 1_333
    assert sp.payouts[MARI] == pot * 333 // 1_333
    # El IAJ es el 10 % de cada apuesta; el redondeo lo paga quien más se lleva.
    assert sp.taxes[JUAN] == gaming_tax(2_017)
    assert sp.taxes[MARI] == gaming_tax(333)
    assert sp.taxes[PEPE] >= gaming_tax(1_000)
    assert gaming_tax(1_000) == round(1_000 * GAMING_TAX_RATE)


def test_si_nadie_acierta_se_devuelve_todo_sin_comisiones() -> None:
    bets = [Bet(PEPE, 1, 500), Bet(MARI, 2, 700)]
    sp = split(bets, 0)
    assert sp.refund and sp.winning == 0
    assert sp.payouts == {PEPE: 500, MARI: 700}
    assert sp.taxes == {} and sp.image == 0


def test_todo_el_dinero_en_una_opcion_no_es_porra() -> None:
    assert is_one_sided([Bet(PEPE, 0, 500), Bet(MARI, 0, 100)], 2)
    assert is_one_sided([], 2)
    assert not is_one_sided([Bet(PEPE, 0, 500), Bet(MARI, 1, 100)], 2)


def test_varias_apuestas_del_mismo_miembro_se_juntan() -> None:
    assert merge_bets([(PEPE, 1, 100), (MARI, 0, 50), (PEPE, 1, 200)]) == [
        Bet(PEPE, 1, 300),
        Bet(MARI, 0, 50),
    ]


def test_la_cuota_estimada_descuenta_iaj_e_imagen() -> None:
    mult = payout_multiplier([1_000, 1_000], 0)
    assert mult == pytest.approx(2 * (1 - GAMING_TAX_RATE - IMAGE_SHARE / 100))
    assert payout_multiplier([0, 500], 0) is None


def test_el_tope_del_bote_es_cinco_veces_lo_comprometido() -> None:
    assert POOL_FACTOR == 5
    assert pool_cap(500, 3) == 5 * 500 * 3


def test_la_libreta_deja_montar_porras_mas_largas() -> None:
    assert max_plays(set()) == MAX_PLAYS
    assert max_plays({NOTEBOOK_KEY}) == MAX_PLAYS_NOTEBOOK
    assert {NOTEBOOK_KEY, BINOCULARS_KEY} <= set(CATALOG_BY_KEY)
    assert all(CATALOG_BY_KEY[key].aisle == "porra" for key in (NOTEBOOK_KEY, BINOCULARS_KEY))


# -- Propuestas ------------------------------------------------------------------------


def test_sin_crash_y_sin_porras_de_porras() -> None:
    games = allowed_games()
    assert "crash" not in games and "porra" not in games
    assert set(games) == set(casino_stats_module.GAMES) - EXCLUDED_GAMES


def test_un_juego_nuevo_tiene_porras_genericas_sin_hacer_nada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    games = dict(casino_stats_module.GAMES, dados=("🎲", "Dados"))
    monkeypatch.setattr("bot.services.porras.GAMES", games)
    assert "dados" in allowed_games()
    generic = {p.key for p in PROPOSITIONS if p.games is None}
    assert generic <= {p.key for p in propositions_for("dados", 3)}


def test_cada_propuesta_especifica_tiene_un_juego_que_manda_sus_datos() -> None:
    """Si un juego deja de mandar `boom`, `splat` o `bust`, su propuesta se rompería."""
    modules = {
        m.GAME: inspect.getsource(m)
        for m in (mines_cog, chicken_cog, coin_cog, bus_cog, craps_cog, blackjack_cog)
    }
    for prop in PROPOSITIONS:
        if prop.games is None:
            assert not prop.details, prop.key
            continue
        for game in prop.games:
            assert game in allowed_games(), prop.key
            for key in (*prop.details, "started"):
                assert f'("{key}"' in modules[game], (prop.key, key)


@pytest.mark.parametrize(
    ("key", "plays", "expected"),
    [
        ("signo", [play(100), play(-50)], 0),
        ("signo", [play(-100), play(100)], 1),
        ("pleno", [play(10), play(10)], 0),
        ("pleno", [play(10), play(-10)], 1),
        ("cuantas", [play(10), play(-10), play(30)], 2),
        ("dobla", [play(100), play(100)], 0),
        ("dobla", [play(99), play(100)], 1),
        ("palo", [play(-40), play(-50)], 0),
        ("palo", [play(-100), play(-1)], 1),
        ("gorda", [play(400)], 0),
        ("gorda", [play(399)], 1),
        ("tieso", [play(-100, balance=100)], 0),
        ("tieso", [play(-100, balance=99)], 1),
        ("mina", [play(50, boom=0), play(-100, boom=1)], 1),
        ("mina", [play(50, boom=0)], 0),
        ("atropello", [play(-100, game="pollo", splat=1)], 1),
        ("pasarse", [play(-100, game="blackjack", bust=1)], 1),
        ("natural", [play(150, game="blackjack", natural=1)], 0),
        ("racha", [play(-100, game="moneda", wins=2), play(700, game="moneda", wins=3)], 0),
        ("racha", [play(100, game="moneda", wins=1), play(-100, game="moneda", wins=2)], 1),
        ("trayecto", [play(-100, game="autobus", wins=3), play(900, game="autobus", wins=4)], 0),
        ("trayecto", [play(100, game="autobus", wins=3), play(-100, game="autobus", wins=0)], 1),
        ("punto", [play(-100, game="dados", made=0), play(100, game="dados", made=1)], 0),
        ("punto", [play(100, game="dados", made=0), play(-100, game="dados", made=0)], 1),
    ],
)
def test_cada_propuesta_decide_bien(key: str, plays: list[Play], expected: int) -> None:
    assert PROPOSITION_BY_KEY[key].decide(plays, 100) == expected


def test_las_opciones_de_cuantas_van_de_cero_a_n() -> None:
    assert PROPOSITION_BY_KEY["cuantas"].options_for(3) == ("0 de 3", "1 de 3", "2 de 3", "3 de 3")
    assert not PROPOSITION_BY_KEY["pleno"].fits("minas", 1)
    assert not PROPOSITION_BY_KEY["mina"].fits("pollo", 3)


def test_solo_cuentan_las_jugadas_del_juego_y_la_apuesta_y_tras_el_cierre() -> None:
    p = porra(status=Status.LOCKED, locked_at=1_000.0)
    assert p.counts(play(10))
    assert not p.counts(play(10, game="ruleta"))
    assert not p.counts(play(10, stake=99))
    assert not p.counts(play(10, started=999))
    assert p.counts(play(10, started=1_000))
    assert not porra(status=Status.OPEN).counts(play(10))


def test_se_resuelve_al_llegar_a_las_jugadas_o_al_quedarse_tieso() -> None:
    p = porra(status=Status.LOCKED, locked_at=0.0)
    assert not p.add(play(10))
    assert p.add(play(-100, balance=50))
    p = porra(status=Status.LOCKED, locked_at=0.0, plays=2)
    assert not p.add(play(10)) and p.add(play(10))


def test_una_jugada_que_llega_antes_del_reparto_no_cuenta() -> None:
    p = porra(status=Status.LOCKED, locked_at=0.0, plays=1)
    assert p.add(play(10))
    assert not p.counts(play(-100))


def test_tamayazo_es_cuando_la_ultima_jugada_da_la_vuelta() -> None:
    p = porra(status=Status.LOCKED, locked_at=0.0, seen=[play(-100), play(150)])
    assert p.flipped_at_the_end()
    p = porra(status=Status.LOCKED, locked_at=0.0, seen=[play(100), play(50)])
    assert not p.flipped_at_the_end()


def test_argumentos_de_texto() -> None:
    assert parse_text_args([]) == (None, None, None)
    assert parse_text_args(["mina", "3", "500"]) == ("mina", 3, "500")
    assert parse_text_args(["3"]) == (None, 3, None)
    assert parse_text_args(["2k"]) == (None, None, "2k")
    assert parse_text_args(["500"]) == (None, None, "500")
    assert resolve_game("Tragas") == "tragaperras"
    assert resolve_game("crash") is None


# -- Logros ----------------------------------------------------------------------------


def test_las_estadisticas_de_las_porras_son_las_declaradas_y_salen_todas() -> None:
    when = datetime(2026, 12, 31, 3, tzinfo=TIMEZONE)
    deltas = [
        porra_open_stats(game="minas", proposition="mina", plays=7),
        *porra_answer_stats(accepted=False),
        *porra_answer_stats(accepted=True),
        porra_bet_stats(stake=666, balance_before=666, opener_against=True, when=when),
        porra_bet_stats(stake=69, balance_before=5_000, opener_against=False, when=when),
        porra_bet_stats(
            stake=10, balance_before=5_000, opener_against=False,
            when=datetime(2026, 5, 30, 17, tzinfo=TIMEZONE),
        ),
        porra_bet_stats(
            stake=10, balance_before=5_000, opener_against=False,
            when=datetime(2026, 11, 13, 17, tzinfo=TIMEZONE),
        ),
        porra_bet_stats(
            stake=10, balance_before=5_000, opener_against=False,
            when=datetime(2026, 10, 11, 17, tzinfo=TIMEZONE),
        ),
        porra_bettor_stats(
            stake=100, payout=1_000, refund=False, nobody=False, tax=10, streak=3,
            lone_wolf=True, favourite_flop=False, loyal_loss=False, flipped=True, crowd=5,
        ),
        porra_bettor_stats(
            stake=100, payout=0, refund=False, nobody=False, tax=10, streak=0,
            lone_wolf=False, favourite_flop=True, loyal_loss=True, flipped=False, crowd=5,
        ),
        porra_bettor_stats(
            stake=100, payout=100, refund=True, nobody=True, tax=0, streak=0,
            lone_wolf=False, favourite_flop=False, loyal_loss=False, flipped=False, crowd=5,
        ),
        porra_subject_stats(
            image=20, pool=1_000, favourable=True, heroic=True, broke=True, crowd=5
        ),
        porra_no_show_stats(),
        porra_snoop_stats(),
    ]  # fmt: skip
    seen = set()
    for delta in deltas:
        seen |= set(delta.add) | set(delta.peak)
    prefixed = {s for s in seen if s.startswith((PORRA_GAME_PREFIX, PORRA_PROP_PREFIX))}
    assert seen - prefixed <= PORRA_STATS
    assert seen - prefixed == PORRA_STATS


# -- Dinero ----------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 7, 18, tzinfo=TIMEZONE).timestamp()

    def __call__(self) -> float:
        return self.now


async def make_economy(tmp_path: Path) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=Clock())


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


async def test_apostar_deja_el_dinero_en_el_deposito_y_el_libro_cuadra(tmp_path: Path) -> None:
    economy = await make_economy(tmp_path)
    receipt = await economy.porra_bet(GUILD, 7, PEPE, outcome=0, stake=300, cap=1_000)
    assert receipt.balance == STARTING_BALANCE - 300
    assert receipt.pool == 300
    receipt = await economy.porra_bet(GUILD, 7, PEPE, outcome=0, stake=200, cap=1_000)
    assert receipt.stake == 500
    for user in (PEPE, PORRA_ACCOUNT_ID):
        assert ledger_sum(tmp_path, user) == await economy.balance(GUILD, user)
    assert await economy.balance(GUILD, PORRA_ACCOUNT_ID) == 500


async def test_una_opcion_por_miembro_y_el_tope_del_bote(tmp_path: Path) -> None:
    economy = await make_economy(tmp_path)
    await economy.porra_bet(GUILD, 7, PEPE, outcome=0, stake=600, cap=1_000)
    with pytest.raises(PorraSideError) as side:
        await economy.porra_bet(GUILD, 7, PEPE, outcome=1, stake=10, cap=1_000)
    assert side.value.outcome == 0
    with pytest.raises(PorraCapError) as cap:
        await economy.porra_bet(GUILD, 7, MARI, outcome=1, stake=500, cap=1_000)
    assert cap.value.room == 400
    assert await economy.balance(GUILD, MARI) == STARTING_BALANCE


async def test_liquidar_paga_iaj_al_estado_e_imagen_con_retencion(tmp_path: Path) -> None:
    economy = await make_economy(tmp_path)
    await economy.porra_bet(GUILD, 7, PEPE, outcome=0, stake=800, cap=10_000)
    await economy.porra_bet(GUILD, 7, MARI, outcome=1, stake=900, cap=10_000)
    bets = merge_bets(await economy.porra_bets(GUILD, 7))
    sp = split(bets, 0)
    payment = await economy.settle_porra(
        GUILD, 7, payouts=sp.payouts, taxes=sp.taxes, refund=False, image=sp.image, subject_id=ANA
    )
    assert await economy.balance(GUILD, PORRA_ACCOUNT_ID) == 0
    assert await economy.balance(GUILD, PEPE) == STARTING_BALANCE - 800 + sp.payouts[PEPE] - (
        payment.bets[PEPE].tax_delta
    )
    image_tax = image_rights_withholding(sp.image)
    assert payment.image_tax == image_tax
    assert await economy.balance(GUILD, ANA) == STARTING_BALANCE + sp.image - image_tax
    # El Estado recibe exactamente el IAJ, la retención de la imagen y el IRPF del juego.
    irpf = sum(result.tax_delta for result in payment.bets.values())
    assert await economy.state_balance(GUILD) == sp.tax + image_tax + irpf
    for user in (PEPE, MARI, ANA, STATE_ACCOUNT_ID, PORRA_ACCOUNT_ID):
        assert ledger_sum(tmp_path, user) == await economy.balance(GUILD, user)
    breakdown = await economy.repository.tax_breakdown(GUILD)
    assert breakdown[PEPE]["iaj"] + breakdown[MARI]["iaj"] == sp.tax
    with pytest.raises(PorraClosedError):
        await economy.settle_porra(
            GUILD, 7, payouts=sp.payouts, taxes=sp.taxes, refund=False, image=0, subject_id=ANA
        )
    with pytest.raises(PorraClosedError):
        await economy.porra_bet(GUILD, 7, JUAN, outcome=0, stake=10, cap=10_000)


async def test_devolver_deshace_la_apuesta_sin_tocar_al_estado(tmp_path: Path) -> None:
    economy = await make_economy(tmp_path)
    await economy.porra_bet(GUILD, 7, PEPE, outcome=0, stake=400, cap=10_000)
    bets = merge_bets(await economy.porra_bets(GUILD, 7))
    sp = refund_split(bets)
    await economy.settle_porra(
        GUILD, 7, payouts=sp.payouts, taxes={}, refund=True, image=0, subject_id=ANA
    )
    assert await economy.balance(GUILD, PEPE) == STARTING_BALANCE
    assert await economy.state_balance(GUILD) == 0
    assert ledger_sum(tmp_path, PORRA_ACCOUNT_ID) == 0


async def test_un_reparto_que_no_cuadra_no_mueve_nada(tmp_path: Path) -> None:
    economy = await make_economy(tmp_path)
    await economy.porra_bet(GUILD, 7, PEPE, outcome=0, stake=400, cap=10_000)
    with pytest.raises(ValueError):
        await economy.settle_porra(
            GUILD, 7, payouts={PEPE: 500}, taxes={}, refund=True, image=0, subject_id=ANA
        )
    assert await economy.balance(GUILD, PORRA_ACCOUNT_ID) == 400
    assert not await economy.porra_settled(GUILD, 7)


# -- Repositorio -----------------------------------------------------------------------


async def test_el_repositorio_guarda_estado_y_rachas(tmp_path: Path) -> None:
    repository = PorraRepository(tmp_path / "bot.db")
    await repository.initialize()
    p = porra(id=0)
    await repository.create(p)
    assert p.id > 0
    assert [x.id for x in await repository.unfinished()] == [p.id]
    p.status, p.void_reason = Status.VOID, VoidReason.NO_SHOW
    await repository.save(p, finished_at=1.0)
    assert await repository.unfinished() == []
    assert await repository.bump_streak(GUILD, PEPE, won=True) == 1
    assert await repository.bump_streak(GUILD, PEPE, won=True) == 2
    assert await repository.bump_streak(GUILD, PEPE, won=False) == 0


# -- El cog ----------------------------------------------------------------------------


def member(user_id: int, name: str) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    user.display_name = name
    user.mention = f"<@{user_id}>"
    return user


def interaction(user: MagicMock) -> MagicMock:
    inter = fake_interaction(user)
    inter.channel = None
    return inter


async def never(_seconds: float) -> None:
    """Los temporizadores no saltan solos: la prueba decide cuándo pasa cada fase."""
    await asyncio.Event().wait()


@pytest.fixture(autouse=True)
async def _cancel_timers() -> object:
    """Cancela al acabar los temporizadores que han quedado esperando."""
    yield
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current and not task.done():
            task.cancel()


async def make_cog(tmp_path: Path) -> tuple[Porras, EconomyService, dict[int, MagicMock]]:
    economy = await make_economy(tmp_path)
    repository = PorraRepository(tmp_path / "bot.db")
    await repository.initialize()
    people = {
        uid: member(uid, name)
        for uid, name in [
            (LUIS, "Luis"),
            (ANA, "Ana"),
            (PEPE, "Pepe"),
            (MARI, "Mari"),
            (JUAN, "Juan"),
        ]
    }
    bot = MagicMock()
    bot.get_guild.return_value = None
    bot.get_user.side_effect = people.get
    cog = Porras(bot, economy, repository, clock=economy._clock, sleep=never)
    return cog, economy, people


async def open_porra(cog: Porras, people: dict[int, MagicMock], **kwargs: object) -> Table:
    message = MagicMock()
    message.id = 99
    message.edit = AsyncMock()
    message.channel.send = AsyncMock()
    args: dict[str, object] = dict(
        guild=SimpleNamespace(id=GUILD),
        channel=None,
        opener=people[LUIS],
        subject=people[ANA],
        game_text="minas",
        prop_key=None,
        plays=2,
        stake_text="200",
        send=AsyncMock(return_value=message),
        send_error=AsyncMock(),
    )
    args.update(kwargs)
    p = await cog.open(**args)  # type: ignore[arg-type]
    assert p is not None, args["send_error"].await_args  # type: ignore[union-attr]
    return cog.tables[p.id]


async def test_no_te_puedes_montar_una_porra_ni_al_crash(tmp_path: Path) -> None:
    cog, _economy, people = await make_cog(tmp_path)
    errors = AsyncMock()
    base = dict(
        guild=SimpleNamespace(id=GUILD), channel=None, opener=people[LUIS], prop_key=None,
        plays=1, stake_text=None, send=AsyncMock(), send_error=errors,
    )  # fmt: skip
    assert await cog.open(subject=people[LUIS], game_text="minas", **base) is None
    assert "ti mismo" in errors.await_args.args[0]
    assert await cog.open(subject=people[ANA], game_text="crash", **base) is None
    assert await cog.open(subject=people[ANA], game_text="minas", **{**base, "plays": 6}) is None
    assert "Libreta" in errors.await_args.args[0]
    assert cog.tables == {}


async def test_una_porra_entera_de_principio_a_fin(tmp_path: Path) -> None:
    cog, economy, people = await make_cog(tmp_path)
    table = await open_porra(cog, people)
    porra_ = table.porra
    assert porra_.status is Status.PROPOSED and porra_.cap == POOL_FACTOR * 200 * 2

    # Solo Ana acepta; Ana no puede apostar.
    await cog.answer(interaction(people[PEPE]), table, accepted=True)
    assert porra_.status is Status.PROPOSED
    await cog.answer(interaction(people[ANA]), table, accepted=True)
    assert porra_.status is Status.OPEN
    ana = interaction(people[ANA])
    await cog.ask_bet(ana, table, 0)
    ana.response.send_modal.assert_not_awaited()

    await cog.bet(interaction(people[PEPE]), table, 0, "600")
    await cog.bet(interaction(people[MARI]), table, 1, "400")
    await cog.bet(interaction(people[JUAN]), table, 1, "200")
    assert await economy.balance(GUILD, PORRA_ACCOUNT_ID) == 1_200

    await cog.lock(table)
    assert porra_.status is Status.LOCKED

    # Una jugada de otro juego o de antes del cierre no cuenta.
    await cog.observe(GUILD, ANA, play(500, stake=200, game="ruleta"))
    await cog.observe(GUILD, ANA, play(500, stake=200, started=int(porra_.locked_at or 0) - 5))
    assert porra_.seen == []
    await cog.observe(GUILD, ANA, play(-200, stake=200))
    await cog.observe(GUILD, ANA, play(700, stake=200))
    # Se resuelve en segundo plano. Hasta 10 s: con toda la batería en paralelo
    # (y escenas de Node y Chromium arrancando a la vez) 2 s se quedaban cortos.
    for _ in range(1000):
        if porra_.status is Status.RESOLVED:
            break
        await asyncio.sleep(0.01)
    for task in list(cog._tasks):
        task.cancel()

    assert porra_.status is Status.RESOLVED and porra_.outcome == 0
    assert await economy.balance(GUILD, PORRA_ACCOUNT_ID) == 0
    bets = [Bet(PEPE, 0, 600), Bet(MARI, 1, 400), Bet(JUAN, 1, 200)]
    sp = split(bets, 0)
    assert sp.payouts[PEPE] > 600
    assert await economy.balance(GUILD, MARI) == STARTING_BALANCE - 400
    assert await economy.balance(
        GUILD, ANA
    ) == STARTING_BALANCE + sp.image - image_rights_withholding(sp.image)
    assert table.message.edit.await_args.kwargs["view"] is None
    assert porra_.id not in cog.tables


async def test_si_todo_va_a_lo_mismo_se_anula_y_se_devuelve(tmp_path: Path) -> None:
    cog, economy, people = await make_cog(tmp_path)
    table = await open_porra(cog, people)
    await cog.answer(interaction(people[ANA]), table, accepted=True)
    await cog.bet(interaction(people[PEPE]), table, 0, "300")
    await cog.lock(table)
    assert table.porra.status is Status.VOID
    assert table.porra.void_reason is VoidReason.ONE_SIDED
    assert await economy.balance(GUILD, PEPE) == STARTING_BALANCE


async def test_si_no_juega_a_tiempo_es_una_espantada(tmp_path: Path) -> None:
    cog, economy, people = await make_cog(tmp_path)
    table = await open_porra(cog, people)
    await cog.answer(interaction(people[ANA]), table, accepted=True)
    await cog.bet(interaction(people[PEPE]), table, 0, "300")
    await cog.bet(interaction(people[MARI]), table, 1, "300")
    await cog.lock(table)
    await cog._no_show(table)
    assert table.porra.void_reason is VoidReason.NO_SHOW
    assert await economy.balance(GUILD, PEPE) == STARTING_BALANCE
    assert await economy.balance(GUILD, MARI) == STARTING_BALANCE
    assert await economy.state_balance(GUILD) == 0


async def test_al_reiniciar_se_devuelve_lo_que_quedo_a_medias(tmp_path: Path) -> None:
    cog, economy, people = await make_cog(tmp_path)
    table = await open_porra(cog, people)
    await cog.answer(interaction(people[ANA]), table, accepted=True)
    await cog.bet(interaction(people[PEPE]), table, 0, "300")
    # Un bot nuevo sobre la misma base de datos, como tras una caída.
    fresh = Porras(cog.bot, economy, cog.repository, clock=economy._clock, sleep=never)
    await fresh.recover()
    assert await economy.balance(GUILD, PEPE) == STARTING_BALANCE
    assert await cog.repository.unfinished() == []
    for task in list(fresh._tasks) + list(cog._tasks):
        task.cancel()


async def test_rechazar_anula_y_el_protagonista_ya_puede_tener_otra(tmp_path: Path) -> None:
    cog, _economy, people = await make_cog(tmp_path)
    table = await open_porra(cog, people)
    await cog.answer(interaction(people[ANA]), table, accepted=False)
    assert table.porra.void_reason is VoidReason.DECLINED
    assert cog.subject_table(GUILD, ANA) is None
    await open_porra(cog, people)


async def test_los_prismaticos_hacen_falta_para_ver_quien_apuesta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _economy, people = await make_cog(tmp_path)
    table = await open_porra(cog, people)
    await cog.answer(interaction(people[ANA]), table, accepted=True)
    await cog.bet(interaction(people[PEPE]), table, 0, "300")
    mirona = interaction(people[MARI])
    await cog.snoop(mirona, table)
    assert "Prismáticos" in mirona.edit_original_response.await_args.kwargs["content"]
    monkeypatch.setattr(
        "bot.cogs.porras.shop.owned_keys", AsyncMock(return_value=frozenset({BINOCULARS_KEY}))
    )
    await cog.snoop(mirona, table)
    assert f"<@{PEPE}>" in mirona.edit_original_response.await_args.kwargs["content"]
