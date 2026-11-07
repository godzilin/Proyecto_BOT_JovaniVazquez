"""Login y sesiones del Jovaniverso (`bot.services.universo`).

El login va contra un Discord falso (un servidor aiohttp local) que contesta como
la API de OAuth2: así se prueba la petición de verdad, no un doble de la sesión.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.services.shop import Kind
from bot.services.universo import (
    SESSION_SECONDS,
    DiscordOAuth,
    SessionSigner,
    UniversoError,
    backpack_list,
    bearer,
    platform_of,
    player_list,
    snowflake,
    teleport_report,
)

CODE = "abcdefghijklmnopqrstuvwxyz0123"
CLIENT_ID = 1234567890
SECRET = "secreto-de-prueba"


class Clock:
    """Reloj que avanza a mano."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# -- Sesiones ----------------------------------------------------------------------


def test_una_sesion_recien_dada_dice_de_quien_es() -> None:
    signer = SessionSigner(b"k" * 32, clock=Clock())
    assert signer.verify(signer.issue(42)) == 42


def test_una_sesion_caduca_a_su_hora() -> None:
    clock = Clock()
    signer = SessionSigner(b"k" * 32, clock=clock)
    token = signer.issue(42)
    clock.now += SESSION_SECONDS - 1
    assert signer.verify(token) == 42
    clock.now += 1
    assert signer.verify(token) is None


def test_una_sesion_tocada_no_vale() -> None:
    signer = SessionSigner(b"k" * 32, clock=Clock())
    user_id, expires, signature = signer.issue(42).split(".")
    assert signer.verify(f"43.{expires}.{signature}") is None
    assert signer.verify(f"{user_id}.{int(expires) + 999}.{signature}") is None


def test_una_sesion_de_antes_de_reiniciar_no_vale() -> None:
    clock = Clock()
    token = SessionSigner(b"a" * 32, clock=clock).issue(42)
    assert SessionSigner(b"b" * 32, clock=clock).verify(token) is None


@pytest.mark.parametrize("token", [None, 42, "", "a.b.c", "1.2", "1.2.3.4", "x.100.firma"])
def test_lo_que_no_tiene_forma_de_sesion_no_vale(token: object) -> None:
    assert SessionSigner(b"k" * 32, clock=Clock()).verify(token) is None


def test_sin_clave_cada_arranque_sortea_la_suya() -> None:
    token = SessionSigner().issue(42)
    assert SessionSigner().verify(token) is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer  abc ", "abc"),
        ("Basic abc", None),
        ("Bearer ", None),
        ("", None),
        (None, None),
    ],
)
def test_la_sesion_va_en_la_cabecera_bearer(header: str | None, expected: str | None) -> None:
    assert bearer(header) == expected


def test_los_ids_y_la_plataforma_se_filtran_al_llegar_de_la_pagina() -> None:
    assert snowflake("123456789012345678") == 123456789012345678
    assert snowflake(123) is None
    assert snowflake("12a") is None
    assert snowflake("1" * 21) is None
    assert platform_of("mobile") == "mobile"
    assert platform_of("desktop") == "desktop"
    assert platform_of("nevera") is None
    assert platform_of(None) is None


# -- Login con Discord ----------------------------------------------------------------


class FakeDiscord:
    """API de OAuth2 de Discord, la justa para el login."""

    def __init__(self) -> None:
        self.token_status = 200
        self.user_status = 200
        self.token_forms: list[dict[str, str]] = []
        self.user_headers: list[str] = []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/oauth2/token", self.token)
        app.router.add_get("/users/@me", self.me)
        return app

    async def token(self, request: web.Request) -> web.Response:
        self.token_forms.append(dict(await request.post()))
        if self.token_status != 200:
            return web.json_response({"error": "invalid_grant"}, status=self.token_status)
        return web.json_response({"access_token": "token-de-acceso", "token_type": "Bearer"})

    async def me(self, request: web.Request) -> web.Response:
        self.user_headers.append(request.headers.get("Authorization", ""))
        if self.user_status != 200:
            return web.json_response({"message": "401: Unauthorized"}, status=self.user_status)
        return web.json_response({"id": "555", "username": "yeyo"})


@pytest.fixture
async def discord_api() -> AsyncIterator[tuple[FakeDiscord, DiscordOAuth]]:
    fake = FakeDiscord()
    server = TestServer(fake.app())
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        oauth = DiscordOAuth(
            session, client_id=CLIENT_ID, client_secret=SECRET, api=str(server.make_url(""))
        )
        yield fake, oauth
    await server.close()


async def test_el_login_cambia_el_codigo_por_token_y_pregunta_quien_es(
    discord_api: tuple[FakeDiscord, DiscordOAuth],
) -> None:
    fake, oauth = discord_api
    login = await oauth.exchange(CODE)
    assert login.access_token == "token-de-acceso"
    assert login.user_id == 555
    assert fake.token_forms == [
        {
            "client_id": str(CLIENT_ID),
            "client_secret": SECRET,
            "grant_type": "authorization_code",
            "code": CODE,
        }
    ]
    assert fake.user_headers == ["Bearer token-de-acceso"]


@pytest.mark.parametrize("code", [None, 123, "", "corto", "con espacios dentro 123", "x" * 101])
async def test_un_codigo_sin_forma_no_llega_a_discord(
    discord_api: tuple[FakeDiscord, DiscordOAuth], code: object
) -> None:
    fake, oauth = discord_api
    with pytest.raises(UniversoError) as raised:
        await oauth.exchange(code)
    assert raised.value.status == 400
    assert fake.token_forms == []


async def test_un_codigo_que_discord_rechaza_pide_repetir_el_login(
    discord_api: tuple[FakeDiscord, DiscordOAuth],
) -> None:
    fake, oauth = discord_api
    fake.token_status = 400
    with pytest.raises(UniversoError) as raised:
        await oauth.exchange(CODE)
    assert raised.value.status == 401
    assert fake.user_headers == []


async def test_si_discord_falla_se_dice_sin_detalles(
    discord_api: tuple[FakeDiscord, DiscordOAuth],
) -> None:
    fake, oauth = discord_api
    fake.user_status = 500
    with pytest.raises(UniversoError) as raised:
        await oauth.exchange(CODE)
    assert raised.value.status == 502
    assert SECRET not in raised.value.message


async def test_si_discord_no_contesta_el_login_falla_con_502() -> None:
    async with aiohttp.ClientSession() as session:
        # Un puerto donde no escucha nadie.
        oauth = DiscordOAuth(
            session, client_id=CLIENT_ID, client_secret=SECRET, api="http://127.0.0.1:9"
        )
        with pytest.raises(UniversoError) as raised:
            await oauth.exchange(CODE)
    assert raised.value.status == 502


def test_la_lista_del_menu_pone_primero_a_los_de_dentro_y_luego_por_saldo() -> None:
    members = [(1, "Ana"), (2, "Beto"), (3, "Carla"), (4, "Dani"), (5, "Eva")]
    balances = {1: 50, 2: 900, 3: 10, 5: 400}
    rows = player_list(members, {3}, balances, {2: 250, 3: 0}, 1, limit=3)
    # Carla está dentro; tú (Ana) vas siempre; del resto, el que más tiene hasta llenar.
    assert [r["nombre"] for r in rows] == ["Carla", "Ana", "Beto"]
    assert rows[0] == {
        "nombre": "Carla",
        "saldo": rows[0]["saldo"],
        "nivel": 0,
        "dentro": True,
        "yo": False,
    }
    assert rows[1]["yo"] and not rows[1]["dentro"]
    assert rows[2]["nivel"] == 1  # 100 XP el nivel 1 y 200 más el 2
    assert all(r["nivel"] is None for r in player_list(members, set(), balances, None, 1))
    assert len(player_list(members, set(), balances, None, 1)) == 5


class _Entry:
    def __init__(self, name: str, kind: Kind, emoji: str = "🎩", **kw: object) -> None:
        self.name, self.kind, self.emoji = name, kind, emoji
        self.equipped = kw.get("equipped", False)
        self.expires_at = kw.get("expires_at")


def test_la_mochila_del_menu_agrupa_iguales_y_dice_cuanto_les_queda() -> None:
    rows = backpack_list(
        [
            _Entry("Chistera", Kind.TROPHY),
            _Entry("Chistera", Kind.TROPHY),
            _Entry("Turbo", Kind.BOOST, "<:turbo:123>", expires_at=1_500.0),
            _Entry("Turbo", Kind.BOOST, "<:turbo:123>", expires_at=1_100.0),
            _Entry("VIP", Kind.ROLE, "⭐", equipped=True),
        ],
        1_000.0,
    )
    by_name = {r["nombre"]: r for r in rows}
    assert by_name["Chistera"]["cantidad"] == 2
    assert by_name["Turbo"]["caduca"] == 100  # la más cercana
    assert by_name["Turbo"]["emoji"] == Kind.BOOST.icon  # el emoji propio no se pinta
    assert by_name["VIP"]["puesto"] is True
    assert by_name["Chistera"]["caduca"] is None
    assert backpack_list([], 0) == []


def test_los_teleportes_del_paseo_se_recortan_al_tiempo() -> None:
    assert teleport_report({"teleports": 50}, 10) == 5
    assert teleport_report({"teleports": 2}, 10) == 2
    for raro in (-1, True, "3", None):
        assert teleport_report({"teleports": raro}, 10) == 0
