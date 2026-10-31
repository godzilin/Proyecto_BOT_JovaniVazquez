"""Reglas del aspecto de Jovani: qué look toca, qué se cambia y qué textos salen."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from bot.services import aspecto
from bot.services.aspecto import (
    BIO_MAX,
    BLOCK_SECONDS,
    BUILTIN_BY_KEY,
    BUILTIN_LOOKS,
    DRAGON_SECONDS,
    MIN_PROFILE_SECONDS,
    STATUS_MAX,
    UPDATED_SECONDS,
    AspectoError,
    AspectoState,
    LookOverride,
    Snapshot,
    WeekStats,
)
from bot.services.levels import TIMEZONE

KNOWN = frozenset(BUILTIN_BY_KEY)


def at(*parts: int) -> datetime:
    """Un momento en hora canaria."""
    return datetime(*parts, tzinfo=TIMEZONE)


# Un miércoles normal, sin fiesta ni evento.
WEDNESDAY = at(2026, 6, 10, 12)
BUSY = Snapshot(
    week=WeekStats(commands=1204, mistyped=37, drawings=412, songs=9, top_command="tragas"),
    house_net=12_300,
    state_balance=900_000,
    achievements=52,
    dragons=1,
    restarts=4,
)


def test_las_claves_de_serie_son_seguras_y_no_se_repiten() -> None:
    keys = [look.key for look in BUILTIN_LOOKS]
    assert len(keys) == len(set(keys))
    assert all(aspecto.valid_key(key) for key in keys)


def test_todos_los_textos_de_serie_usan_solo_variables_que_existen() -> None:
    for look in BUILTIN_LOOKS:
        for text in look.bios:
            assert aspecto.check_template(text, BIO_MAX) == text
        for text in look.statuses:
            assert aspecto.check_template(text, STATUS_MAX) == text


@pytest.mark.parametrize(
    ("year", "expected"),
    [(2024, date(2024, 3, 31)), (2026, date(2026, 4, 5)), (2027, date(2027, 3, 28))],
)
def test_la_pascua_cae_en_su_domingo(year: int, expected: date) -> None:
    assert aspecto.easter(year) == expected


@pytest.mark.parametrize(
    ("moment", "key"),
    [
        (WEDNESDAY, "normal"),
        (at(2026, 6, 10, 3), "pijama"),
        (at(2026, 6, 10, 7), "normal"),
        (at(2026, 6, 8, 12), "hacienda"),
        (at(2026, 6, 12, 19), "normal"),
        (at(2026, 6, 12, 20), "casino"),
        (at(2026, 6, 13, 23), "casino"),
        (at(2026, 6, 14, 21), "normal"),
        (at(2026, 5, 30, 12), "canarias"),
        (at(2026, 10, 31, 12), "halloween"),
        (at(2026, 12, 24, 12), "navidad"),
        (at(2027, 1, 6, 12), "navidad"),
        (at(2027, 1, 7, 12), "normal"),
        (at(2026, 2, 17, 12), "carnaval"),
    ],
)
def test_cada_momento_tiene_su_look(moment: datetime, key: str) -> None:
    assert aspecto.automatic_look(AspectoState(), moment).key == key


def test_el_orden_de_los_looks_automaticos() -> None:
    """Dragón, beernight, versión nueva y fiesta, por ese orden, ganan al calendario."""
    halloween = at(2028, 10, 31, 12)
    moment = halloween.timestamp()
    assert aspecto.automatic_look(AspectoState(), halloween).key == "halloween"
    updated = AspectoState(updated_until=moment + 1)
    assert aspecto.automatic_look(updated, halloween).key == "actualizado"
    assert aspecto.automatic_look(updated, halloween, beernight=True).key == "beernight"
    dragon = replace(updated, dragon_until=moment + 1)
    assert aspecto.automatic_look(dragon, halloween, beernight=True).key == "dragon"


def test_el_aniversario_gana_a_las_demas_fiestas() -> None:
    moment = at(2026, 12, 24, 12)
    assert aspecto.automatic_look(AspectoState(), moment, anniversary=(12, 24)).key == (
        "aniversario"
    )


def test_lo_fijado_a_mano_gana_hasta_que_caduca() -> None:
    moment = WEDNESDAY.timestamp()
    pinned = AspectoState(pinned_key="casino", pinned_until=moment + 60, dragon_until=moment + 60)
    assert aspecto.desired_key(pinned, WEDNESDAY, known=KNOWN) == "casino"
    expired = replace(pinned, pinned_until=moment - 1, dragon_until=0.0)
    assert aspecto.desired_key(expired, WEDNESDAY, known=KNOWN) == "normal"


def test_lo_fijado_sin_fecha_no_caduca() -> None:
    pinned = AspectoState(pinned_key="casino")
    assert aspecto.desired_key(pinned, WEDNESDAY, known=KNOWN) == "casino"


def test_un_look_fijado_que_ya_no_existe_no_cuenta() -> None:
    pinned = AspectoState(pinned_key="borrado")
    assert aspecto.desired_key(pinned, WEDNESDAY, known=KNOWN) == "normal"


def test_cambiar_de_look_toca_las_tres_piezas() -> None:
    plan = aspecto.plan_profile(AspectoState(applied_key="pijama"), "normal", WEDNESDAY)
    assert (plan.avatar, plan.banner, plan.bio) == (True, True, True)
    assert not plan.empty


def test_no_se_cambia_de_look_antes_de_la_espera_minima() -> None:
    moment = WEDNESDAY.timestamp()
    state = AspectoState(applied_key="pijama", profile_at=moment - MIN_PROFILE_SECONDS + 1)
    assert aspecto.plan_profile(state, "normal", WEDNESDAY).empty
    waited = replace(state, profile_at=moment - MIN_PROFILE_SECONDS)
    assert not aspecto.plan_profile(waited, "normal", WEDNESDAY).empty


def test_un_administrador_no_espera_pero_si_respeta_el_bloqueo_de_discord() -> None:
    moment = WEDNESDAY.timestamp()
    state = AspectoState(applied_key="pijama", profile_at=moment)
    assert not aspecto.plan_profile(state, "normal", WEDNESDAY, force=True).empty
    blocked = replace(state, blocked_until=moment + BLOCK_SECONDS)
    assert aspecto.plan_profile(blocked, "normal", WEDNESDAY, force=True).empty


def test_apagado_no_se_toca_el_perfil() -> None:
    state = AspectoState(enabled=False, applied_key="pijama")
    assert aspecto.plan_profile(state, "normal", WEDNESDAY, force=True).empty


def test_el_primer_repaso_del_dia_renueva_banner_y_bio_y_no_el_avatar() -> None:
    state = AspectoState(applied_key="normal", refreshed_day="2026-06-09")
    plan = aspecto.plan_profile(state, "normal", WEDNESDAY)
    assert (plan.avatar, plan.banner, plan.bio) == (False, True, True)
    done = replace(state, refreshed_day=WEDNESDAY.date().isoformat())
    assert aspecto.plan_profile(done, "normal", WEDNESDAY).empty


def test_de_madrugada_no_se_renueva_nada() -> None:
    state = AspectoState(applied_key="pijama", refreshed_day="2026-06-09")
    assert aspecto.plan_profile(state, "pijama", at(2026, 6, 10, 3)).empty


def test_la_primera_version_vista_no_arranca_el_look_de_actualizado() -> None:
    state = aspecto.seen_version(AspectoState(), 1000.0, "9.0.0")
    assert state.last_version == "9.0.0"
    assert state.updated_until == 0.0


def test_una_version_nueva_arranca_el_look_de_actualizado_una_vez() -> None:
    state = aspecto.seen_version(AspectoState(last_version="9.0.0"), 1000.0, "9.1.0")
    assert state.updated_until == 1000.0 + UPDATED_SECONDS
    assert aspecto.seen_version(state, 2000.0, "9.1.0") is state


def test_el_dragon_dura_un_dia() -> None:
    assert DRAGON_SECONDS == 86_400


@pytest.mark.parametrize(
    ("name", "key"),
    [("Modo Tortilla", "modo-tortilla"), ("  Año nuevo!! ", "ano-nuevo"), ("A" * 40, "a" * 24)],
)
def test_la_clave_de_un_look_sale_limpia_del_nombre(name: str, key: str) -> None:
    assert aspecto.slugify(name) == key


@pytest.mark.parametrize("key", ["../../etc", "a/b", "a\\b", "", "-a", "A", "a" * 25, "a.png"])
def test_una_clave_peligrosa_no_es_valida(key: str) -> None:
    assert not aspecto.valid_key(key)


def test_un_nombre_sin_letras_se_rechaza() -> None:
    with pytest.raises(AspectoError):
        aspecto.slugify("🎉🎉")


def test_una_plantilla_con_variable_desconocida_se_rechaza() -> None:
    with pytest.raises(AspectoError, match="token"):
        aspecto.check_template("hola {token}", BIO_MAX)


def test_una_plantilla_no_puede_leer_atributos() -> None:
    with pytest.raises(AspectoError):
        aspecto.check_template("{version.__class__}", BIO_MAX)


def test_una_plantilla_larga_o_vacia_se_rechaza() -> None:
    with pytest.raises(AspectoError):
        aspecto.check_template("a" * (BIO_MAX + 1), BIO_MAX)
    with pytest.raises(AspectoError):
        aspecto.check_template("   ", BIO_MAX)


def test_los_datos_no_enseñan_cifras_a_cero() -> None:
    assert aspecto.facts(Snapshot()) == []
    lines = aspecto.facts(BUSY)
    assert "⌨️ 1.204 comandos esta semana" in lines
    assert "🎰 La casa gana 12.300 Y$ esta semana" in lines
    assert not any("canciones saltadas" in line for line in lines)


def test_una_sola_unidad_va_en_singular() -> None:
    lines = aspecto.facts(Snapshot(week=WeekStats(mistyped=1, songs=1), dragons=1, restarts=1))
    assert lines == [
        "🤦 1 comando mal escrito esta semana",
        "🎵 1 canción escuchada esta semana",
        "🐉 1 dragón matado",
        "🔁 1 reinicio",
    ]


def test_si_la_casa_pierde_se_dice_sin_signo() -> None:
    lines = aspecto.facts(Snapshot(house_net=-500))
    assert lines == ["🎰 La casa pierde 500 Y$ esta semana"]


@pytest.mark.parametrize("look", BUILTIN_LOOKS, ids=lambda look: look.key)
def test_las_bios_de_serie_caben_con_el_dato_mas_largo(look) -> None:  # noqa: ANN001
    huge = Snapshot(week=WeekStats(mistyped=10**12), state_balance=10**15)
    for seed in range(8):
        text = aspecto.bio_text(look, None, huge, seed=seed, version="99.99.99")
        assert len(text) <= BIO_MAX
        assert "{" not in text


def test_la_bio_sin_datos_sigue_siendo_una_frase() -> None:
    text = aspecto.bio_text(aspecto.NORMAL, None, Snapshot(), seed=0, version="9.0.0")
    assert text == "Jovani Vázquez, versión 9.0.0. Aquí sigo."


def test_la_bio_de_un_administrador_sustituye_a_las_de_serie() -> None:
    override = LookOverride("normal", bio="Yo mando. {version}")
    text = aspecto.bio_text(aspecto.NORMAL, override, BUSY, seed=3, version="9.0.0")
    assert text == "Yo mando. 9.0.0"


def test_los_estados_intercalan_datos_y_frases_del_look() -> None:
    lines = aspecto.status_lines(aspecto.CASINO, None, BUSY, version="9.0.0")
    assert lines[0] == "v9.0.0"
    own = [line for line in lines if line in aspecto.CASINO.statuses]
    assert own
    assert len(own) < len(lines) / 2 + 1
    assert all(len(line) <= STATUS_MAX for line in lines)


def test_sin_datos_tambien_hay_estados() -> None:
    lines = aspecto.status_lines(aspecto.NORMAL, None, Snapshot(), version="9.0.0")
    assert lines[0] == "v9.0.0"
    assert len(lines) > 1


def test_el_estado_de_un_administrador_sustituye_a_los_de_serie() -> None:
    override = LookOverride("casino", status="Solo hoy en {version}")
    lines = aspecto.status_lines(aspecto.CASINO, override, BUSY, version="9.0.0")
    assert "Solo hoy en 9.0.0" in lines
    assert not set(lines) & set(aspecto.CASINO.statuses)


def test_el_banner_lleva_tres_datos_que_rotan_con_el_dia() -> None:
    first = aspecto.banner_lines(BUSY, seed=0)
    second = aspecto.banner_lines(BUSY, seed=1)
    assert len(first) == 3
    assert first != second


def test_un_look_propio_tiene_el_nombre_que_le_dieron() -> None:
    look = aspecto.custom_look(LookOverride("tortilla", name="🥔 Tortilla"))
    assert (look.key, look.name) == ("tortilla", "🥔 Tortilla")
    assert look.bios == aspecto.NORMAL.bios


def test_las_cifras_de_uso_salen_de_la_telemetria() -> None:
    counts = {
        "slash": {"tragas": 5, "ruleta": 6},
        "text": {"tragas": 3},
        "uso_mal": {"tragas": 2},
        "render": {"moneda": 4},
        "musica": {"escuchada": 7, "saltada": 1, "búsqueda": 90},
        "message": {"123": 11},
        "arranque": {"9.0.0": 2},
    }
    totals = aspecto.usage_totals(counts)
    assert totals == {
        "commands": 14,
        "mistyped": 2,
        "drawings": 4,
        "songs": 7,
        "skipped": 1,
        "messages": 11,
        "restarts": 2,
    }
    assert aspecto.top_command(counts) == "tragas"
    assert aspecto.stats_from(totals, top="tragas") == WeekStats(14, 2, 4, 7, 1, 11, "tragas")


def test_sin_comandos_no_hay_comando_estrella() -> None:
    assert aspecto.top_command({}) is None
    assert aspecto.usage_totals({})["commands"] == 0
