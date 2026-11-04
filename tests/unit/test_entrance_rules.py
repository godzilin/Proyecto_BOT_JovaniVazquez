"""Pruebas de bot.services.entrance: precios, mejoras, modos, onda y textos."""

from __future__ import annotations

import random

import pytest

from bot.services.entrance import (
    BASE_TENTHS,
    EFFECTS,
    EXIT_PRICE,
    EXIT_SLOT,
    LENGTH_PRICES,
    MAX_SLOTS,
    MAX_TENTHS,
    SLOT_PRICES,
    EntranceError,
    Mode,
    Profile,
    Slot,
    Upgrade,
    apply_offer,
    clean_name,
    next_mode,
    next_offer,
    parse_seconds,
    pick,
    seconds_text,
    total_spent,
    wave_text,
)


def sound(slot: int, name: str = "x") -> Slot:
    return Slot(
        slot=slot,
        name=name,
        volume=100,
        effect="normal",
        fade=False,
        start_ms=0,
        length_ms=3000,
        source_ms=3000,
        clip_ms=3000,
        wave="▁█",
    )


def profile_with(*slots: int, **kwargs: object) -> Profile:
    profile = Profile(**kwargs)  # type: ignore[arg-type]
    for n in slots:
        profile = profile.with_sound(sound(n))
    return profile


def test_los_precios_suben_y_cubren_cada_tramo() -> None:
    assert sorted(SLOT_PRICES) == list(range(2, MAX_SLOTS + 1))
    assert sorted(LENGTH_PRICES) == list(range(BASE_TENTHS + 5, MAX_TENTHS + 1, 5))
    for prices in (SLOT_PRICES, LENGTH_PRICES):
        values = [prices[k] for k in sorted(prices)]
        assert values == sorted(values) and len(set(values)) == len(values)
    # Ninguna mejora llega a 100.000 Y$, que pediría nivel mínimo en la tienda.
    assert max(*SLOT_PRICES.values(), *LENGTH_PRICES.values(), EXIT_PRICE) < 100_000


def test_comprarlo_todo_en_orden_llega_al_maximo_y_se_para() -> None:
    profile = Profile()
    for upgrade in Upgrade:
        while (offer := next_offer(profile, upgrade)) is not None:
            profile = apply_offer(profile, offer)
    assert (profile.slots, profile.max_tenths, profile.exit_owned) == (MAX_SLOTS, MAX_TENTHS, True)
    assert total_spent(profile) == (
        sum(SLOT_PRICES.values()) + sum(LENGTH_PRICES.values()) + EXIT_PRICE
    )


def test_una_oferta_vieja_no_se_aplica() -> None:
    offer = next_offer(Profile(), Upgrade.SLOT)
    assert offer is not None
    bought = apply_offer(Profile(), offer)
    with pytest.raises(EntranceError):
        apply_offer(bought, offer)  # La misma compra desde otro panel.


def test_modo_fijo_usa_la_activa_y_si_no_esta_la_primera() -> None:
    rng = random.Random(1)
    assert pick(profile_with(1, 3, active=3), None, rng).slot == 3
    assert pick(profile_with(1, 2, active=5), None, rng).slot == 1
    assert pick(Profile(), None, rng) is None


def test_la_salida_nunca_suena_al_entrar() -> None:
    profile = profile_with(EXIT_SLOT, exit_owned=True)
    assert pick(profile, None, random.Random(1)) is None


def test_al_azar_no_repite_la_ultima_si_hay_mas() -> None:
    profile = profile_with(1, 2, 3, mode=Mode.RANDOM)
    rng = random.Random(7)
    last = 1
    for _ in range(50):
        chosen = pick(profile, last, rng)
        assert chosen is not None and chosen.slot != last
        last = chosen.slot
    single = profile_with(2, mode=Mode.RANDOM)
    assert pick(single, 2, rng).slot == 2


def test_por_turnos_las_recorre_en_orden_y_vuelve_a_empezar() -> None:
    profile = profile_with(1, 4, 6, mode=Mode.CYCLE)
    rng = random.Random(0)
    order = []
    last = None
    for _ in range(5):
        last = pick(profile, last, rng).slot
        order.append(last)
    assert order == [1, 4, 6, 1, 4]


def test_los_modos_rotan() -> None:
    assert [next_mode(m) for m in Mode] == [Mode.RANDOM, Mode.CYCLE, Mode.FIXED]


def test_donde_va_un_audio_sin_elegir_ranura() -> None:
    assert Profile().default_slot() == 1
    assert profile_with(1, slots=3).default_slot() == 2
    assert profile_with(1, 2, 3, slots=3, active=2).default_slot() == 2


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("12", 12.0), ("12,5", 12.5), ("1:05", 65.0), ("1:05.5", 65.5), (" 3 s", 3.0), ("", None)],
)
def test_parse_seconds_entiende_lo_que_escribe_la_gente(text: str, seconds: float | None) -> None:
    assert parse_seconds(text) == seconds


@pytest.mark.parametrize("text", ["doce", "-3", "1:70", "a:05", "nan"])
def test_parse_seconds_rechaza_lo_raro(text: str) -> None:
    with pytest.raises(EntranceError):
        parse_seconds(text)


def test_textos_de_segundos_y_nombres() -> None:
    assert seconds_text(3000) == "3 s"
    assert seconds_text(3500) == "3,5 s"
    assert seconds_text(4960) == "5 s"
    assert clean_name("  Bocina\n del `barco`  ", "x") == "Bocina del 'barco'"
    assert clean_name("   ", "Entrada 2") == "Entrada 2"
    assert len(clean_name("a" * 80, "x")) == 32


def test_la_onda_sube_donde_hay_mas_bytes() -> None:
    packets = [b"x" * 3] * 50 + [b"x" * 200] * 50 + [b"x" * 3] * 50
    wave = wave_text(packets, width=6)
    assert len(wave) == 6
    assert wave[0] == "▁" and "█" in wave[2:4] and wave[-1] == "▁"
    assert wave_text([]) == ""


def test_los_efectos_tienen_claves_unicas_y_la_primera_es_sin_efecto() -> None:
    keys = [e.key for e in EFFECTS]
    assert len(keys) == len(set(keys))
    assert EFFECTS[0].key == "normal" and EFFECTS[0].filter == ""
