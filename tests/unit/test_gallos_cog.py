"""Pruebas de bot.cogs.gallos: el reñidero contra la casa y los duelos con grada.

Se usa la economía real sobre un SQLite temporal, un dibujante falso (las
imágenes tienen sus propias pruebas), esperas a cero y un azar cuyo primer
número decide quién gana (`Fixed`): el resto de la pelea sale de una semilla.
"""

from __future__ import annotations

import asyncio
import random
from fractions import Fraction
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import gallos as gallos_cog
from bot.cogs.gallos import Duel, Gallos, HousePanel, Phase
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import SHOP_ACCOUNT_ID, STATE_ACCOUNT_ID, EconomyRepository
from bot.repositories.gallos import GallosRepository
from bot.services import gallos_gym as gym
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.gallos import (
    FORM_PER_FIGHT,
    HOUSE,
    PAID_FORM,
    RIVAL_BY_KEY,
    TRAIN_COOLDOWN,
    TRAIN_PER_DAY,
    Corner,
    Exchange,
    Fight,
    Finish,
    RoosterRecord,
    Stat,
    default_name,
    duel_prize,
    format_odds,
    house_odds,
    house_payout,
    injured,
    pool_payouts,
    stat_bar,
    train_price,
)
from bot.services.gallos_render import Media
from bot.services.taxes import gambling_day_tax, igic

GUILD_ID = 1
OWNER_ID = 10
RIVAL_ID = 11
FAN_IDS = (20, 21, 22)
WIN, LOSE = 0.0, 0.999


class Fixed(random.Random):
    """Azar cuyo primer `random()` es `first` (quién gana); lo demás, de una semilla."""

    def __init__(self, first: float) -> None:
        super().__init__(7)
        self.first: float | None = first

    def random(self) -> float:
        if self.first is not None:
            value, self.first = self.first, None
            return value
        return super().random()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gallos_cog, "REVEAL_MARGIN_SECONDS", 0)


def make_user(user_id: int) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = f"Persona {user_id}"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def fake_renderer() -> MagicMock:
    renderer = MagicMock()
    renderer.still = AsyncMock(return_value=b"png")
    renderer.fight = AsyncMock(return_value=Media(gif=b"GIF89a", png=b"png", seconds=0.0))
    renderer.close = AsyncMock()
    return renderer


class Scripted(random.Random):
    """Azar que da primero los `values` pedidos (quién gana, el sorteo de la muerte…).

    Después sigue con una semilla. Las peleas de las pruebas de muerte se fijan
    aparte (`ko_fight`), así que cada `random()` cuenta.
    """

    def __init__(self, *values: float) -> None:
        super().__init__(7)
        self.values = list(values)

    def random(self) -> float:
        return self.values.pop(0) if self.values else super().random()


async def make_cog(tmp_path: Path, first: float, rng: random.Random | None = None) -> Gallos:
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    repository = GallosRepository(tmp_path / "bot.db")
    await repository.initialize()

    async def no_sleep(seconds: float) -> None:
        # Las esperas del GIF no se esperan; las del reto y la grada, nunca acaban.
        if seconds >= 30:
            await asyncio.Event().wait()

    return Gallos(
        MagicMock(),
        economy=EconomyService(economy_repository),
        repository=repository,
        rng=rng or Fixed(first),
        renderer=fake_renderer(),
        sleep=no_sleep,
    )


def ledger(tmp_path: Path, user_id: int) -> list[tuple[str, int]]:
    """Movimientos de un miembro (motivo, importe), sin el saldo de bienvenida."""
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


# -- Contra la casa ----------------------------------------------------------------------


async def open_panel(cog: Gallos, amount: str = "100") -> HousePanel:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    challenged = await cog._gallos_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text=amount,
        rival=None,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    assert not challenged
    assert send.await_args.kwargs["file"].filename == "gallos.png"
    (panel,) = cog.panels
    return panel


def irpf_juego(tax: int) -> list[tuple[str, int]]:
    """El apunte de la retención del juego, si la hay (los días que se gana poco no hay)."""
    return [("irpf:juego", -tax)] if tax else []


async def test_ganar_a_la_casa_cobra_la_cuota_y_apunta_la_victoria(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    rival = RIVAL_BY_KEY["falcon"]
    assert panel.rival is rival
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._fight(interaction)
    assert ledger(tmp_path, OWNER_ID) == [
        ("gallos:apuesta", -100),
        ("gallos:premio", house_payout(100, rival)),
        *irpf_juego(gambling_day_tax(house_payout(100, rival) - 100)),
    ]
    assert panel.record.wins == 1 and panel.record.fights == 1
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).wins == 1
    cog.renderer.fight.assert_awaited_once()
    assert "+" in (panel.result or "")


async def test_perder_contra_la_casa_no_paga_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, LOSE)
    panel = await open_panel(cog)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert ledger(tmp_path, OWNER_ID) == [("gallos:apuesta", -100)]
    assert panel.record.wins == 0 and panel.record.fights == 1


async def test_sin_saldo_no_se_pelea(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    panel.stake = STARTING_BALANCE * 10
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._fight(interaction)
    assert ledger(tmp_path, OWNER_ID) == []
    interaction.followup.send.assert_awaited_once()
    cog.renderer.fight.assert_not_awaited()


async def test_elegir_rival_cambia_la_cuota(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    interaction = fake_interaction(make_user(OWNER_ID))
    interaction.data = {"values": ["marte"]}
    await panel._choose_rival(interaction)
    assert panel.rival is RIVAL_BY_KEY["marte"]
    assert "×9,50" in panel.description()


async def test_bautizar_al_gallo_lo_guarda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await panel.rename(fake_interaction(make_user(OWNER_ID)), "Espolón de Oro")
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).name == "Espolón de Oro"
    assert "Espolón de Oro" in panel.description()


async def repaint(panel: HousePanel) -> dict:
    """Repinta el panel con un clic nuevo y devuelve lo que se mandó a Discord."""
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel.repaint(interaction)
    return interaction.response.edit_message.await_args.kwargs


async def test_repintar_la_misma_presentacion_no_vuelve_a_subir_la_imagen(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    assert "attachments" in await repaint(panel)
    cog.renderer.still.reset_mock()
    kwargs = await repaint(panel)
    assert "attachments" not in kwargs
    assert kwargs["view"] is panel
    cog.renderer.still.assert_not_awaited()


async def test_repintar_con_otro_rival_nombre_o_apuesta_sube_la_imagen(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await repaint(panel)
    cambios = (
        ("rival", RIVAL_BY_KEY["marte"]),
        ("record", gallos_cog.RoosterRecord(name="Espolón de Oro")),
        ("stake", panel.stake + 1),
    )
    for attribute, value in cambios:
        setattr(panel, attribute, value)
        cog.renderer.still.reset_mock()
        kwargs = await repaint(panel)
        assert len(kwargs["attachments"]) == 1, attribute
        cog.renderer.still.assert_awaited_once()


async def test_tras_una_pelea_el_siguiente_repintado_vuelve_a_subir_la_imagen(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await repaint(panel)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert "attachments" in await repaint(panel)


# -- Duelos ------------------------------------------------------------------------------


async def challenge(cog: Gallos, amount: str = "500") -> Duel:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    challenged = await cog._gallos_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text=amount,
        rival=make_user(RIVAL_ID),
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    assert challenged
    (duel,) = cog.duels
    return duel


async def accept(duel: Duel) -> None:
    await duel.accept(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.LOBBY
    # La pelea se corre a mano: sin esperar a la grada.
    assert duel.task is not None
    duel.task.cancel()


async def test_un_duelo_cobra_a_los_dos_y_paga_al_ganador(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    assert duel.timer is not None
    await accept(duel)
    assert ledger(tmp_path, OWNER_ID) == [("gallos:apuesta", -500)]
    assert ledger(tmp_path, RIVAL_ID) == [("gallos:apuesta", -500)]
    for fan, corner in zip(FAN_IDS, (Corner.RED, Corner.BLUE, Corner.RED), strict=True):
        await duel.place(fake_interaction(make_user(fan)), corner, "200")
    await duel.brawl()
    assert duel.fight is not None and duel.phase is Phase.DONE
    winner = duel.fight.winner
    loser_id = RIVAL_ID if winner is Corner.RED else OWNER_ID
    winner_id = OWNER_ID if winner is Corner.RED else RIVAL_ID
    won = [
        ("gallos:premio", duel_prize(500)),
        *irpf_juego(gambling_day_tax(duel_prize(500) - 500)),
    ]
    assert ledger(tmp_path, winner_id)[-len(won) :] == won
    assert ledger(tmp_path, loser_id) == [("gallos:apuesta", -500)]
    payouts = pool_payouts({k: s.bet for k, s in duel.crowd.items()}, winner)
    paid = 0
    for fan in FAN_IDS:
        moves = ledger(tmp_path, fan)
        assert moves[0] == ("gallos:apuesta", -200)
        prizes = sum(amount for reason, amount in moves[1:] if reason != "irpf:juego")
        withheld = -sum(amount for reason, amount in moves[1:] if reason == "irpf:juego")
        paid += prizes
        assert prizes == payouts[fan]
        assert withheld == gambling_day_tax(payouts[fan] - 200)
    # La grada no crea dinero: se reparte lo apostado menos la comisión.
    assert paid <= 600
    assert (await cog.repository.get(GUILD_ID, winner_id)).wins == 1
    assert not cog.busy


async def test_los_duenos_no_apuestan_en_la_grada_y_cada_uno_apuesta_una_vez(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    await accept(duel)
    owner = fake_interaction(make_user(OWNER_ID))
    await duel._open_bet(owner, Corner.RED)
    owner.response.send_modal.assert_not_awaited()
    fan = fake_interaction(make_user(FAN_IDS[0]))
    await duel._open_bet(fan, Corner.RED)
    fan.response.send_modal.assert_awaited_once()
    await duel.place(fake_interaction(make_user(FAN_IDS[0])), Corner.RED, "100")
    again = fake_interaction(make_user(FAN_IDS[0]))
    await duel._open_bet(again, Corner.BLUE)
    again.response.send_modal.assert_not_awaited()
    assert len(duel.crowd) == 1


async def test_rechazar_no_mueve_dinero_y_libera_a_los_dos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    stranger = fake_interaction(make_user(FAN_IDS[0]))
    await duel.reject(stranger)
    assert duel.phase is Phase.CHALLENGE
    await duel.reject(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.DONE
    assert ledger(tmp_path, OWNER_ID) == [] and ledger(tmp_path, RIVAL_ID) == []
    assert not cog.busy and not cog.duels


async def test_no_se_puede_estar_en_dos_duelos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    await challenge(cog)
    errors = AsyncMock()
    challenged = await cog._gallos_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(FAN_IDS[0]),
        amount_text="100",
        rival=make_user(RIVAL_ID),
        send=AsyncMock(),
        send_error=errors,
    )
    assert not challenged
    errors.assert_awaited_once()


async def test_apagar_con_la_grada_abierta_devuelve_lo_apostado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    await accept(duel)
    await duel.place(fake_interaction(make_user(FAN_IDS[0])), Corner.BLUE, "300")
    await cog.cog_unload()
    for user_id, stake in ((OWNER_ID, 500), (RIVAL_ID, 500), (FAN_IDS[0], 300)):
        assert ledger(tmp_path, user_id) == [("gallos:apuesta", -stake), ("gallos:premio", stake)]
    assert duel.phase is Phase.DONE


# -- Stats, entrenamiento y muertes -------------------------------------------------------

#: Un mediodía canario cualquiera: seis entrenamientos con su hora de espera caben en el día.
NOON = 1_760_000_000.0


def wallet_total(tmp_path: Path) -> int:
    """Todo el dinero que hay en los monederos del servidor (el libro se mueve, no se crea)."""
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(balance), 0) FROM economy_wallets WHERE guild_id = ?",
            (GUILD_ID,),
        ).fetchone()
    finally:
        connection.close()
    return int(total)


def books_balance(tmp_path: Path) -> bool:
    """Si el saldo de cada monedero es la suma de sus apuntes del libro."""
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        rows = connection.execute(
            """
            SELECT w.balance, COALESCE(SUM(l.delta), 0) FROM economy_wallets w
            LEFT JOIN economy_ledger l ON l.guild_id = w.guild_id AND l.user_id = w.user_id
            WHERE w.guild_id = ? GROUP BY w.user_id
            """,
            (GUILD_ID,),
        ).fetchall()
    finally:
        connection.close()
    return all(balance == booked for balance, booked in rows)


async def set_stats(cog: Gallos, user_id: int = OWNER_ID, **stats: int) -> RoosterRecord:
    """Le da stats a mano al gallo de un miembro (lo que dejaría haber entrenado mucho)."""
    await cog.repository.rename(GUILD_ID, user_id, "Roca")
    connection = sqlite_module.connect(cog.repository.database_path)
    try:
        for column, value in stats.items():
            connection.execute(
                f"UPDATE gallos SET {column} = ? WHERE guild_id = ? AND user_id = ?",
                (value, GUILD_ID, user_id),
            )
        connection.commit()
    finally:
        connection.close()
    return await cog.repository.get(GUILD_ID, user_id)


def ko_fight(winner: Corner) -> Fight:
    """Una pelea que `winner` gana por KO (la vida cae a cero en el último golpe)."""
    hp = (100, 0) if winner is Corner.RED else (0, 100)
    return Fight(winner, Finish.KO, (Exchange(1, winner, "patada", 100, True, False, hp),))


def points_fight(winner: Corner) -> Fight:
    """Una pelea que `winner` gana a los puntos."""
    hp = (60, 40) if winner is Corner.RED else (40, 60)
    return Fight(winner, Finish.POINTS, (Exchange(3, winner, "aletazo", 10, False, False, hp),))


async def test_entrenar_cobra_con_igic_al_estado_y_sube_el_stat(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    total_before = wallet_total(tmp_path)
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._train(interaction, Stat.STRENGTH)
    base = train_price(0)
    tax = igic(base)
    assert tax > 0
    assert ledger(tmp_path, OWNER_ID) == [
        ("tienda:gallos:entreno", -base),
        ("tienda:igic", -tax),
    ]
    assert ledger(tmp_path, SHOP_ACCOUNT_ID) == [("tienda:venta", base)]
    assert ("igic:tienda", tax) in ledger(tmp_path, STATE_ACCOUNT_ID)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - base - tax
    stored = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert stored.strength == panel.record.strength == 3 and stored.speed == 0
    # Tras pagar empieza el minijuego; el texto del pago sale al acabarlo.
    assert panel.drill is not None and panel.drill.stat is Stat.STRENGTH
    assert "IGIC" in panel.drill.paid and "Perro Sanxe" in panel.drill.paid
    # Es un sumidero: el dinero cambia de manos (caja y Estado) y no se crea ni se destruye.
    assert wallet_total(tmp_path) == total_before
    assert books_balance(tmp_path)
    assert not panel._busy


async def test_entrenar_contesta_antes_de_tocar_la_base_de_datos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    events: list[str] = []
    interaction = fake_interaction(make_user(OWNER_ID), events=events)
    await panel._train(interaction, Stat.SPEED)
    assert events[0] == "response.defer"
    assert "edit_original_response" in events


async def test_sin_saldo_no_se_entrena_ni_se_cobra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await cog.economy.place_bet(GUILD_ID, OWNER_ID, game="gallos", stake=STARTING_BALANCE - 10)
    await panel._train(fake_interaction(make_user(OWNER_ID)), Stat.ENDURANCE)
    assert (panel.result or "").startswith("❌")
    assert panel.record.endurance == 0
    assert [reason for reason, _ in ledger(tmp_path, OWNER_ID)] == ["gallos:apuesta"]


async def test_una_hora_de_espera_y_seis_entrenamientos_al_dia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await cog.economy.pay_winnings(GUILD_ID, OWNER_ID, game="gallos", amount=100_000)
    clock = [NOON]
    cog.wall_clock = lambda: clock[0]
    owner = make_user(OWNER_ID)

    async def train() -> bool:
        before = panel.record.strength
        await panel._train(fake_interaction(owner), Stat.STRENGTH)
        return panel.record.strength > before

    assert await train()
    # Sin esperar la hora, no: ni se sube ni se cobra.
    spent = [reason for reason, _ in ledger(tmp_path, OWNER_ID)].count("tienda:gallos:entreno")
    assert not await train() and "descansa" in (panel.result or "")
    clock[0] += TRAIN_COOLDOWN - 1
    assert not await train()
    assert [r for r, _ in ledger(tmp_path, OWNER_ID)].count("tienda:gallos:entreno") == spent
    for _ in range(TRAIN_PER_DAY - 1):
        clock[0] += TRAIN_COOLDOWN
        assert await train()
    assert panel.record.trains_today == TRAIN_PER_DAY
    clock[0] += TRAIN_COOLDOWN
    assert not await train() and "entrenamientos" in (panel.result or "")
    assert panel.record.strength == TRAIN_PER_DAY * 3
    # Al día siguiente (hora canaria) vuelve el cupo.
    clock[0] += 24 * 3600
    assert await train()
    assert books_balance(tmp_path)


async def test_un_stat_al_maximo_se_entrena_solo_por_la_forma(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    panel.record = await set_stats(cog, strength=100)
    await panel._train(fake_interaction(make_user(OWNER_ID)), Stat.STRENGTH)
    base = train_price(100)
    assert ledger(tmp_path, OWNER_ID) == [
        ("tienda:gallos:entreno", -base),
        ("tienda:igic", -igic(base)),
    ]
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert record.strength == 100 and record.form == PAID_FORM
    assert panel.drill is not None
    assert f"🔋 Forma 0 → {round(PAID_FORM)}" in panel.drill.paid


async def test_el_gimnasio_tiene_un_boton_por_stat_con_custom_id_fijo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    ids = {c.custom_id for c in panel.children if isinstance(c, discord.ui.Button)}
    assert "gallos:train" in ids
    await panel._gym(fake_interaction(make_user(OWNER_ID)))
    assert panel.training
    ids = {c.custom_id for c in panel.children if isinstance(c, discord.ui.Button)}
    assert ids == {
        "gallos:train_fuerza",
        "gallos:train_velocidad",
        "gallos:train_aguante",
        "gallos:train_back",
        # 💳 Financiar cada entrenamiento (9.20.0)
        "gallos:financiar_fuerza",
        "gallos:financiar_velocidad",
        "gallos:financiar_aguante",
        # 🌽 Echarle de comer: para los demás
        "gallos:feed",
        "gallos:spar_open",
    }
    assert "Gimnasio" in panel.embed().description
    await panel._leave_gym(fake_interaction(make_user(OWNER_ID)))
    assert not panel.training and "gallos:fight" in {
        c.custom_id for c in panel.children if isinstance(c, discord.ui.Button)
    }


async def test_no_se_entrena_en_pleno_duelo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    cog.busy.add((GUILD_ID, OWNER_ID))
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._gym(interaction)
    assert not panel.training
    interaction.response.send_message.assert_awaited_once()


async def test_no_se_pelea_con_la_casa_en_pleno_duelo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    cog.busy.add((GUILD_ID, OWNER_ID))
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._fight(interaction)
    assert ledger(tmp_path, OWNER_ID) == []
    interaction.followup.send.assert_awaited_once()
    cog.renderer.fight.assert_not_awaited()


async def test_un_gallo_entrenado_ve_otra_cuota_y_cobra_la_nueva(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    rival = RIVAL_BY_KEY["falcon"]
    flat = panel.description()
    panel.record = await set_stats(cog, strength=100, speed=100, endurance=100)
    assert panel.power == 100
    text = panel.description()
    assert format_odds(rival.multiplier_for(100)) in text and text != flat
    assert f"{round(float(rival.chance_for(100)) * 100)} %" in text
    panel.rebuild()
    select = next(c for c in panel.children if isinstance(c, discord.ui.Select))
    labels = [option.label for option in select.options]
    assert labels == [f"{r.name} · {format_odds(r.multiplier_for(100))}" for r in HOUSE]
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert ledger(tmp_path, OWNER_ID) == [
        ("gallos:apuesta", -100),
        ("gallos:premio", house_payout(100, rival, 100)),
    ]
    assert books_balance(tmp_path)


async def test_el_azar_de_la_pelea_usa_la_probabilidad_con_stats(tmp_path: Path) -> None:
    rival = RIVAL_BY_KEY["falcon"]  # 50 % sin entrenar
    # Un 0,6 pierde sin entrenar (0,5) y gana con un gallo de poder 100 (~95 %).
    cog = await make_cog(tmp_path, 0.6)
    panel = await open_panel(cog)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert panel.record.wins == 0
    other = tmp_path / "otro"
    other.mkdir()
    cog = await make_cog(other, 0.6)
    panel = await open_panel(cog)
    panel.record = await set_stats(cog, strength=100, speed=100, endurance=100)
    assert float(rival.chance_for(100)) > 0.6
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert panel.record.wins == 1


# -- Muertes ------------------------------------------------------------------------------


async def test_perder_por_ko_y_fallar_el_sorteo_mata_al_gallo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    cog = await make_cog(tmp_path, LOSE, rng=Scripted(LOSE, 0.0))
    panel = await open_panel(cog)
    panel.record = await set_stats(cog, strength=30, fights=9, wins=4)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    # Nada de dinero por morir: solo la apuesta perdida.
    assert ledger(tmp_path, OWNER_ID) == [("gallos:apuesta", -100)]
    (grave,) = await cog.repository.graveyard(GUILD_ID, OWNER_ID)
    assert (grave.name, grave.fights, grave.strength) == ("Roca", 10, 30)
    assert (grave.killer_kind, grave.killer) == ("casa", "falcon")
    fresh = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert fresh.name == default_name(OWNER_ID) and fresh.fights == 0 and fresh.strength == 0
    assert fresh.deaths == 1 and panel.record == fresh
    assert "🪦" in (panel.result or "") and "⚰️" in (panel.result or "")
    assert not panel._busy


async def test_el_sorteo_de_la_muerte_se_salva_si_cae_por_encima(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    cog = await make_cog(tmp_path, LOSE, rng=Scripted(LOSE, 0.5))
    panel = await open_panel(cog)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert await cog.repository.graveyard(GUILD_ID, OWNER_ID) == []
    assert panel.record.fights == 1 and panel.record.deaths == 0
    assert "🪦" not in (panel.result or "")


@pytest.mark.parametrize(
    ("endurance", "form", "roll", "dies"),
    [
        # Agotado (forma 0): del 15 % sin aguante al 7,5 % con aguante 100.
        (0, 0, 0.14, True),
        (100, 0, 0.14, False),
        (100, 0, 0.07, True),
        # En plena forma, la cuarta parte: el 7 % ya no mata ni sin aguante.
        (0, 100, 0.07, False),
        (0, 100, 0.02, True),
    ],
)
async def test_el_aguante_y_la_forma_bajan_la_probabilidad_de_morir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endurance: int,
    form: int,
    roll: float,
    dies: bool,
) -> None:
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    cog = await make_cog(tmp_path, LOSE, rng=Scripted(LOSE, roll))
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    panel.record = await set_stats(cog, endurance=endurance, form=form, form_at=1_000_000)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert bool(await cog.repository.graveyard(GUILD_ID, OWNER_ID)) is dies


async def test_solo_se_muere_perdiendo_por_ko(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: points_fight(winner))
    cog = await make_cog(tmp_path, LOSE, rng=Scripted(LOSE, 0.0))
    panel = await open_panel(cog)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert await cog.repository.graveyard(GUILD_ID, OWNER_ID) == []
    # Ni ganando por KO.
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    other = tmp_path / "otro"
    other.mkdir()
    cog = await make_cog(other, WIN, rng=Scripted(WIN, 0.0))
    panel = await open_panel(cog)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert await cog.repository.graveyard(GUILD_ID, OWNER_ID) == []
    assert panel.record.wins == 1


async def test_en_un_duelo_el_que_pierde_por_ko_puede_morir_y_lo_mata_el_otro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    # El duelo gasta el primer número en su semilla; luego, quién gana y el sorteo de la muerte.
    cog = await make_cog(tmp_path, WIN, rng=Scripted(0.5, WIN, 0.0))
    duel = await challenge(cog)
    await accept(duel)
    await duel.brawl()
    assert duel.fight is not None and duel.fight.winner is Corner.RED
    assert set(duel.fallen) == {Corner.BLUE}
    (grave,) = await cog.repository.graveyard(GUILD_ID, RIVAL_ID)
    assert (grave.killer_kind, grave.killer) == ("miembro", str(OWNER_ID))
    assert await cog.repository.graveyard(GUILD_ID, OWNER_ID) == []
    assert (await cog.repository.get(GUILD_ID, RIVAL_ID)).fights == 0
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).wins == 1
    assert books_balance(tmp_path)


async def test_en_un_duelo_los_stats_cambian_quien_gana(tmp_path: Path) -> None:
    # Un 0,6 da la victoria al azul con los gallos iguales (0,5) y al rojo si está entrenado.
    cog = await make_cog(tmp_path, 0.6, rng=Scripted(0.5, 0.6))
    duel = await challenge(cog)
    assert duel.chance == Fraction(1, 2)
    await accept(duel)
    assert duel.fight is not None and duel.fight.winner is Corner.BLUE

    other = tmp_path / "otro"
    other.mkdir()
    cog = await make_cog(other, 0.6, rng=Scripted(0.5, 0.6))
    await set_stats(cog, strength=100)
    duel = await challenge(cog)
    assert duel.chance > Fraction(6, 10)
    await accept(duel)
    assert duel.fight is not None and duel.fight.winner is Corner.RED
    assert "Probabilidad de ganar" in duel.challenge_text()


async def test_la_ficha_enseña_los_stats_y_los_tres_ultimos_caidos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    await set_stats(cog, strength=40, speed=20, endurance=0)
    for name in ("Uno", "Dos", "Tres", "Cuatro"):
        await cog.repository.rename(GUILD_ID, OWNER_ID, name)
        await cog.bury(GUILD_ID, OWNER_ID, killer_kind="casa", killer="marte")
    await cog.repository.rename(GUILD_ID, OWNER_ID, "Vivo")
    embed = await cog.gallo_embed(GUILD_ID, make_user(OWNER_ID))
    text = embed.description or ""
    assert "Fuerza" in text and "Velocidad" in text and "Aguante" in text and "Poder" in text
    assert "En memoria de" in text
    assert "Cuatro" in text and "Tres" in text and "Dos" in text and "Uno" not in text
    assert "Gallo de Marte" in text


def test_las_frases_de_entrenamiento_y_luto_se_arman_sin_huecos() -> None:
    for stat, lines in gallos_cog.TRAIN_LINES.items():
        assert len(lines) >= 6, stat
        for line in lines:
            assert "{" not in line.format(name="Roca"), line
    for line in gallos_cog.DEATH_LINES:
        assert "Roca" in line.format(name="Roca") and "{" not in line.format(name="Roca")
    for line in gallos_cog.BURIAL_LINES:
        text = line.format(name="Roca", new="Pluma")
        assert "{" not in text and "Pluma" in text
    assert len(gallos_cog.DEATH_LINES) >= 10 and len(gallos_cog.BURIAL_LINES) >= 6


# -- Forma ----------------------------------------------------------------------------------


async def test_en_forma_contra_un_rival_duro_cobra_la_cuota_con_ventaja_y_gasta_forma(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    panel.rival = RIVAL_BY_KEY["marte"]
    panel.stake = 1_000
    flat = house_odds(panel.rival, 0, 0, 1_000)[1]
    panel.record = await set_stats(cog, form=80, form_at=1_000_000)
    assert panel.form == 80
    chance, multiplier = panel.odds()
    assert multiplier > flat
    text = panel.description()
    assert format_odds(multiplier) in text and "🔋" in text and "Ventaja" in text
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    # Detrás va la retención del juego: el premio pasa de lo exento.
    assert ledger(tmp_path, OWNER_ID)[:2] == [
        ("gallos:apuesta", -1_000),
        ("gallos:premio", int(1_000 * multiplier)),
    ]
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).form == 80 - FORM_PER_FIGHT
    assert books_balance(tmp_path)


async def test_entrenar_guarda_la_forma(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    await panel._train(fake_interaction(make_user(OWNER_ID)), Stat.SPEED)
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert (record.speed, record.form, record.form_at) == (3, PAID_FORM, 1_000_000.0)
    assert f"`{stat_bar(round(PAID_FORM))}` {round(PAID_FORM)}" in panel.gym_text()


# -- Minijuegos del gimnasio ----------------------------------------------------------------


class Graded(gym.GymGame):
    """Minijuego de prueba: un botón `ok` lo acaba con la nota fijada."""

    kind = "pesas"
    stat = Stat.STRENGTH

    def __init__(self, grade: float) -> None:
        super().__init__()
        self.grade = grade

    def text(self) -> str:
        return "Pulsa ok."

    def _buttons(self) -> list[gym.GymButton]:
        return [gym.GymButton("ok", "OK", "primary")]

    def _apply(self, key: str, now: float) -> None:
        self._finish(self.grade)

    def summary(self) -> str:
        return "Hecho."


async def paid_drill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, grade: float, event: str | None = None
) -> tuple[Gallos, HousePanel]:
    """Un panel con una sesión de fuerza recién pagada y su minijuego de prueba."""
    monkeypatch.setattr(gallos_cog.gym, "start", lambda kind, rng, now: Graded(grade))
    monkeypatch.setattr(
        gallos_cog.gym,
        "roll_event",
        lambda rng: gym.EVENT_BY_KEY[event] if event else None,
    )
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    await panel._train(fake_interaction(make_user(OWNER_ID)), Stat.STRENGTH)
    assert panel.drill is not None
    return cog, panel


async def test_pagar_abre_el_minijuego_con_botones_de_custom_id_fijo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cog, panel = await paid_drill(tmp_path, monkeypatch, 0.5)
    ids = {c.custom_id for c in panel.children if isinstance(c, discord.ui.Button)}
    assert ids == {
        "gallos:gym:ok",
        "gallos:gym_quit",
        "gallos:gym_coach",
        "gallos:cheer",
        "gallos:sabotage",
    }
    assert "Pulsa ok." in panel.drill_text()


async def test_acabar_bien_el_minijuego_suma_forma_combo_y_el_punto_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, panel = await paid_drill(tmp_path, monkeypatch, 1.0)
    trait = gym.trait_of(OWNER_ID, 0)
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._press(interaction, "ok")
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    extra = gym.session_form(1.0, 0, gym.trait_factor(trait, Stat.STRENGTH))
    assert record.form == pytest.approx(PAID_FORM + extra)
    assert record.strength == 3 + 1  # +3 del pago y +1 por la nota perfecta
    assert record.combo == 1
    assert panel.drill is None
    assert "10/10" in (panel.result or "") and "IGIC" in (panel.result or "")
    assert not panel._busy and interaction.response.is_done()


async def test_dejar_el_minijuego_se_queda_con_lo_pagado_y_no_toca_la_ficha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, panel = await paid_drill(tmp_path, monkeypatch, 1.0)
    before = await cog.repository.get(GUILD_ID, OWNER_ID)
    await panel._quit_drill(fake_interaction(make_user(OWNER_ID)))
    assert panel.drill is None and "Dejas" in (panel.result or "")
    assert await cog.repository.get(GUILD_ID, OWNER_ID) == before


async def test_un_evento_de_forma_se_aplica_al_acabar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, panel = await paid_drill(tmp_path, monkeypatch, 0.0, event="paloma")
    await panel._press(fake_interaction(make_user(OWNER_ID)), "ok")
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    paloma = gym.EVENT_BY_KEY["paloma"]
    assert record.form == pytest.approx(max(0.0, PAID_FORM + paloma.form))
    assert "paloma" in (panel.result or "")


@pytest.mark.parametrize(("roll", "hurt"), [(0.0, True), (0.99, False)])
async def test_el_suplemento_sube_el_stat_y_puede_lesionar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, roll: float, hurt: bool
) -> None:
    cog, panel = await paid_drill(tmp_path, monkeypatch, 0.5, event="suplemento")
    await panel._press(fake_interaction(make_user(OWNER_ID)), "ok")
    assert panel.drill is not None and panel.drill.event is not None
    ids = {c.custom_id for c in panel.children if isinstance(c, discord.ui.Button)}
    assert ids == {"gallos:evt_yes", "gallos:evt_no"}
    cog.rng = Scripted(roll)
    await panel._supplement_yes(fake_interaction(make_user(OWNER_ID)))
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert record.strength == 3 + gym.SUPPLEMENT_GAIN
    assert injured(record, 1_000_000.0) is hurt
    assert panel.drill is None


async def test_rechazar_el_suplemento_no_toca_la_ficha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, panel = await paid_drill(tmp_path, monkeypatch, 0.5, event="suplemento")
    await panel._press(fake_interaction(make_user(OWNER_ID)), "ok")
    before = await cog.repository.get(GUILD_ID, OWNER_ID)
    await panel._supplement_no(fake_interaction(make_user(OWNER_ID)))
    assert await cog.repository.get(GUILD_ID, OWNER_ID) == before
    assert panel.drill is None and "rechaza" in (panel.result or "")


async def test_un_gallo_lesionado_no_pelea_contra_la_casa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    panel.record = await set_stats(cog, injured_until=1_000_000 + 3600)
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._fight(interaction)
    assert ledger(tmp_path, OWNER_ID) == []
    cog.renderer.fight.assert_not_awaited()
    assert "lesionado" in panel.description()


async def test_perder_por_ko_agotado_y_sobrevivir_puede_lesionar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    # Pierde (LOSE), no muere (0.99) y se lesiona (0.0).
    cog = await make_cog(tmp_path, LOSE, rng=Scripted(LOSE, 0.99, 0.0))
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert injured(record, 1_000_000.0)
    assert "lesionado" in (panel.result or "")


async def test_a_muerte_se_activa_en_el_panel_cambia_la_cuota_y_mata_al_perder_por_ko(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    cog = await make_cog(tmp_path, LOSE, rng=Scripted(LOSE, 0.99))
    panel = await open_panel(cog)
    panel.rival = RIVAL_BY_KEY["marte"]
    flat = panel.odds()[1]
    await panel._toggle_deadly(fake_interaction(make_user(OWNER_ID)))
    assert panel.deadly and panel.odds()[1] > flat and "A muerte" in panel.description()
    ids = {c.custom_id for c in panel.children if isinstance(c, discord.ui.Button)}
    assert "gallos:deadly" in ids
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    # El 0,99 salvaría a cualquiera; a muerte no hay sorteo.
    assert await cog.repository.graveyard(GUILD_ID, OWNER_ID)


async def test_el_pique_se_anuncia_al_picar_al_rival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.services.gallos import GRUDGE_WINS

    monkeypatch.setattr(gallos_cog, "simulate", lambda winner, rng: ko_fight(winner))
    cog = await make_cog(tmp_path, WIN, rng=Scripted(*([WIN] * GRUDGE_WINS)))
    cog.shout = AsyncMock()  # type: ignore[method-assign]
    panel = await open_panel(cog)
    for _ in range(GRUDGE_WINS):
        await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert panel.record.grudge_wins == GRUDGE_WINS
    assert any("picado" in call.args[1] for call in cog.shout.await_args_list)
    assert "picado" in panel.description()


# -- Lo que hacen los demás ---------------------------------------------------------------


def clicked(user_id: int, custom_id: str) -> MagicMock:
    """Una interacción de `user_id` sobre el botón `custom_id` (para `interaction_check`)."""
    interaction = fake_interaction(make_user(user_id))
    interaction.data = {"custom_id": custom_id}
    return interaction


async def test_los_demas_solo_pueden_comer_animar_y_sabotear(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    assert await panel.interaction_check(clicked(FAN_IDS[0], "gallos:feed"))
    assert await panel.interaction_check(clicked(FAN_IDS[0], "gallos:cheer"))
    assert not await panel.interaction_check(clicked(FAN_IDS[0], "gallos:fight"))
    assert await panel.interaction_check(clicked(OWNER_ID, "gallos:fight"))


async def test_echarle_de_comer_da_forma_una_vez_al_dia_y_hasta_tres_personas(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    own = fake_interaction(make_user(OWNER_ID))
    await panel._feed(own)
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).form == 0
    for fan in FAN_IDS:
        await panel._feed(fake_interaction(make_user(fan)))
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).form == 3 * gym.FEED_FORM
    again = fake_interaction(make_user(FAN_IDS[0]))
    await panel._feed(again)
    fourth = fake_interaction(make_user(99))
    await panel._feed(fourth)
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    assert record.form == 3 * gym.FEED_FORM and record.fed_count == gym.FEEDS_PER_DAY
    assert "ya le han echado" in fourth.followup.send.await_args.args[0]


async def test_animar_suma_forma_al_minijuego_una_vez_por_persona(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, panel = await paid_drill(tmp_path, monkeypatch, 1.0)
    await panel._cheer(fake_interaction(make_user(OWNER_ID)))
    await panel._cheer(fake_interaction(make_user(FAN_IDS[0])))
    await panel._cheer(fake_interaction(make_user(FAN_IDS[0])))
    assert panel.drill is not None and panel.drill.cheers == {FAN_IDS[0]}
    await panel._press(fake_interaction(make_user(OWNER_ID)), "ok")
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    factor = gym.trait_factor(gym.trait_of(OWNER_ID, 0), Stat.STRENGTH) * gym.cheer_factor(1)
    assert record.form == pytest.approx(PAID_FORM + gym.session_form(1.0, 0, factor))


async def test_sabotear_cobra_con_igic_y_baja_la_nota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, panel = await paid_drill(tmp_path, monkeypatch, 1.0)
    rival = FAN_IDS[1]
    await panel._sabotage(fake_interaction(make_user(rival)))
    base = gym.SABOTAGE_PRICE
    assert ledger(tmp_path, rival) == [
        ("tienda:gallos:sabotaje", -base),
        ("tienda:igic", -igic(base)),
    ]
    assert panel.drill is not None and panel.drill.saboteur is not None
    # Otro no puede tirar otra cáscara.
    await panel._sabotage(fake_interaction(make_user(FAN_IDS[2])))
    assert ledger(tmp_path, FAN_IDS[2]) == []
    await panel._press(fake_interaction(make_user(OWNER_ID)), "ok")
    record = await cog.repository.get(GUILD_ID, OWNER_ID)
    # Nota 1,0 menos el sabotaje: ya no es perfecta, así que no hay +1.
    assert record.strength == 3
    assert "Saboteado" in (panel.result or "")
    assert books_balance(tmp_path)


async def test_el_entrenador_es_el_primero_que_pulsa_y_el_dueno_ya_no_toca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cog, panel = await paid_drill(tmp_path, monkeypatch, 0.5)
    assert not await panel.interaction_check(clicked(FAN_IDS[0], "gallos:gym:ok"))
    await panel._toggle_coach(fake_interaction(make_user(OWNER_ID)))
    assert panel.drill is not None and panel.drill.coach_open
    assert await panel.interaction_check(clicked(FAN_IDS[0], "gallos:gym:ok"))
    panel.drill.game = Graded2()
    await panel._press(fake_interaction(make_user(FAN_IDS[0])), "ok")
    assert panel.drill.coach is not None and panel.drill.coach.id == FAN_IDS[0]
    assert not await panel.interaction_check(clicked(OWNER_ID, "gallos:gym:ok"))
    assert not await panel.interaction_check(clicked(FAN_IDS[1], "gallos:gym:ok"))
    await panel._press(fake_interaction(make_user(FAN_IDS[0])), "ok")
    assert panel.drill is None and "Lo jugó" in (panel.result or "")


class Graded2(Graded):
    """Como `Graded`, pero hacen falta dos pulsaciones para acabar."""

    def __init__(self) -> None:
        super().__init__(0.5)
        self.presses = 0

    def _apply(self, key: str, now: float) -> None:
        self.presses += 1
        if self.presses == 2:
            self._finish(self.grade)


# -- Sparring -------------------------------------------------------------------------------


async def open_sparring(tmp_path: Path) -> tuple[Gallos, gallos_cog.Sparring]:
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: 1_000_000.0
    panel = await open_panel(cog)
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    panel.channel = channel
    await panel._open_sparring(fake_interaction(make_user(OWNER_ID)))
    (sparring,) = cog.sparrings
    channel.send.assert_awaited_once()
    return cog, sparring


async def test_el_sparring_cobra_la_sesion_a_los_dos_y_reparte_forma_por_el_pulso(
    tmp_path: Path,
) -> None:
    cog, sparring = await open_sparring(tmp_path)
    assert (GUILD_ID, OWNER_ID) in cog.busy
    await sparring.join(fake_interaction(make_user(OWNER_ID)))
    assert sparring.guest is None
    await sparring.join(fake_interaction(make_user(RIVAL_ID)))
    assert sparring.phase == "fight"
    base = train_price(0)
    for user in (OWNER_ID, RIVAL_ID):
        assert ledger(tmp_path, user) == [
            ("tienda:gallos:entreno", -base),
            ("tienda:igic", -igic(base)),
        ]
    # El dueño gana los cinco asaltos: aguantar contra empujar.
    for _ in range(5):
        await sparring.pick(fake_interaction(make_user(OWNER_ID)), "aguantar")
        await sparring.pick(fake_interaction(make_user(RIVAL_ID)), "empujar")
    assert sparring.phase == "done" and not cog.sparrings
    assert (GUILD_ID, OWNER_ID) not in cog.busy and (GUILD_ID, RIVAL_ID) not in cog.busy
    winner = await cog.repository.get(GUILD_ID, OWNER_ID)
    loser = await cog.repository.get(GUILD_ID, RIVAL_ID)
    assert winner.form > loser.form
    assert winner.form == pytest.approx(
        PAID_FORM
        + gym.session_form(1.0, 0, gym.trait_factor(gym.trait_of(OWNER_ID, 0), Stat.STRENGTH))
        + gym.SPARRING_WIN_FORM
    )
    assert loser.form == pytest.approx(PAID_FORM)  # nota 0: el minijuego no suma
    assert books_balance(tmp_path)


async def test_en_el_sparring_no_se_puede_elegir_dos_veces_ni_entrar_de_fuera(
    tmp_path: Path,
) -> None:
    _cog, sparring = await open_sparring(tmp_path)
    await sparring.join(fake_interaction(make_user(RIVAL_ID)))
    await sparring.pick(fake_interaction(make_user(OWNER_ID)), "amagar")
    await sparring.pick(fake_interaction(make_user(OWNER_ID)), "empujar")
    assert sparring.picks == {OWNER_ID: "amagar"}
    outsider = fake_interaction(make_user(FAN_IDS[0]))
    await sparring.pick(outsider, "empujar")
    assert OWNER_ID in sparring.picks and FAN_IDS[0] not in sparring.picks


async def test_si_el_que_lo_busca_ya_no_puede_entrenar_se_devuelve_lo_cobrado(
    tmp_path: Path,
) -> None:
    cog, sparring = await open_sparring(tmp_path)
    # Entre la invitación y aceptar, el anfitrión entrena por su cuenta: le toca descansar.
    await cog.train(GUILD_ID, OWNER_ID, await cog.repository.get(GUILD_ID, OWNER_ID), Stat.SPEED)
    await sparring.join(fake_interaction(make_user(RIVAL_ID)))
    assert sparring.phase == "open" and sparring.guest is None
    base = train_price(0)
    moves = ledger(tmp_path, RIVAL_ID)
    assert ("tienda:gallos:entreno", -base) in moves
    assert sum(delta for _reason, delta in moves) == 0
    assert (await cog.repository.get(GUILD_ID, RIVAL_ID)).strength == 0
    assert books_balance(tmp_path)


def test_puntos_del_pulso() -> None:
    assert gym.pulso_points("aguantar", "empujar") == 1
    assert gym.pulso_points("empujar", "aguantar") == 0
    assert gym.pulso_points("amagar", "amagar") == 0.5
    assert gym.pulso_points("", "amagar") == 0 and gym.pulso_points("amagar", "") == 1
    assert gym.pulso_points("", "") == 0.5
