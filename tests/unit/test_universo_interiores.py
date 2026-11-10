"""Los interiores del Jovaniverso: su formato (`i/<id>`, puertas y usables), sus reglas y
las capas de la sala."""

from __future__ import annotations

import copy
import json

import pytest
from test_universo_sala import Clock, FakeClient, entries, pose

from bot.cogs.universo.interiores import SERIE_FILE, serie_pieces
from bot.services.universo_editor import (
    MAX_USABLES,
    MundoError,
    parse_key,
    split_world,
    validate_piece,
    validate_world,
)
from bot.services.universo_interiores import (
    DOOR_REACH_DM,
    REPAIR_SECONDS,
    Usable,
    find_door,
    near,
    next_state,
    place_key,
    usables_of,
    view,
)
from bot.services.universo_sala import Salas
from bot.services.universo_sala import near as sala_near_of

SERIE = json.loads(SERIE_FILE.read_text(encoding="utf-8"))
ROOM = SERIE["interiores"]["piso-plaza"]


def test_el_piso_de_serie_vale_y_trae_su_interior() -> None:
    pieces = split_world({**SERIE, "zonas": {}})
    clean = validate_world(pieces, {})
    assert clean["i/piso-plaza"]["piso"] is True
    assert serie_pieces()["i/piso-plaza"] == pieces["i/piso-plaza"]
    # La puerta lleva a él y el interior usa sus assets (así no se pueden borrar).
    _doc, refs = validate_piece("i/piso-plaza", ROOM, {})
    assert {"sofa", "lampara-pie"} <= refs


def test_la_clave_de_un_interior() -> None:
    assert parse_key("i/piso-plaza") == ("i", "piso-plaza")
    with pytest.raises(MundoError):
        parse_key("i/Mal")


@pytest.mark.parametrize(
    ("cambio", "texto"),
    [
        (lambda r: r.update(ancho=100), "el ancho"),
        (lambda r: r["objetos"][0]["pos"].__setitem__(0, 9), "no cae dentro de la habitación"),
        (lambda r: r["objetos"][2]["datos"].update(n=1), "dos usables con el mismo número"),
        (lambda r: r["objetos"][2]["datos"].update(n=MAX_USABLES + 1), "número"),
        (lambda r: r.update(piso=False), "«piso»"),
        (lambda r: r.update(techo=1), "campos que no se conocen"),
    ],
)
def test_un_interior_malo_dice_por_que(cambio: object, texto: str) -> None:
    room = copy.deepcopy(ROOM)
    cambio(room)  # type: ignore[operator]
    with pytest.raises(MundoError, match=texto):
        validate_piece("i/x", room, {})


def test_las_funciones_nuevas_y_sus_datos() -> None:
    sofa = copy.deepcopy(SERIE["assets"]["sofa"])
    assert validate_piece("a/sofa", sofa, {})[0]["funcion"] == {"tipo": "usable", "uso": "mueble"}
    sofa["funcion"] = {"tipo": "usable", "uso": "romper"}
    with pytest.raises(MundoError, match="mueble o reparar"):
        validate_piece("a/sofa", sofa, {})
    sofa["funcion"] = {"tipo": "puerta", "uso": "mueble"}
    with pytest.raises(MundoError, match="no lleva más datos"):
        validate_piece("a/sofa", sofa, {})
    cell = {"objetos": [copy.deepcopy(SERIE["objetos"][1])]}
    cell["objetos"][0]["datos"]["interior"] = "Mal"
    with pytest.raises(MundoError, match="interior que no existe"):
        validate_piece("c/-1/0", cell, {})


def test_la_puerta_se_busca_cerca_de_quien_entra() -> None:
    door = SERIE["objetos"][1]
    cell = {"objetos": [SERIE["objetos"][0], door]}
    x, z = round(door["pos"][0] * 10), round(door["pos"][2] * 10)
    assert find_door(cell, "piso-plaza", x + DOOR_REACH_DM - 1, z) == door["pos"]
    assert (
        find_door(cell, "piso-plaza", x + DOOR_REACH_DM + 1, z) is None
    )  # la puerta está en -274,2 dm
    assert find_door(cell, "otro", x, z) is None


def test_los_usables_salen_de_sus_assets_y_van_donde_la_puerta() -> None:
    usables = usables_of(ROOM, SERIE["assets"], [10.0, 0.0, 20.0])
    assert sorted(usables) == [1, 2, 3, 4]
    assert usables[1] == Usable("mueble", 100, 200 - 28)
    assert usables[3].uso == "reparar"
    assert usables_of(ROOM, {}, [0, 0, 0]) == {}  # sin sus assets, nada se usa
    assert near(100, 172 + 30, usables[1]) and not near(100, 172 + 31, usables[1])


def test_estados_de_serie_y_lo_arreglado_se_rompe_con_el_tiempo() -> None:
    assert view("mueble", None, 0) == 1
    assert view("reparar", None, 0) == 0
    assert view("reparar", (1, 0.0), REPAIR_SECONDS - 1) == 1
    assert view("reparar", (1, 0.0), REPAIR_SECONDS) == 0
    assert view("mueble", (0, 0.0), 10 * REPAIR_SECONDS) == 0
    assert (next_state("mueble", 1), next_state("mueble", 0)) == (0, 1)
    assert (next_state("reparar", 0), next_state("reparar", 1)) == (1, None)


def test_el_lugar_de_cada_uno() -> None:
    assert place_key("piso", owner=None, pruebas=False) == "piso"
    assert place_key("piso", owner=7, pruebas=False) == "piso@7"
    assert place_key("piso", owner=7, pruebas=True) == "pruebas/piso@7"


async def test_en_otra_capa_ni_se_ve_ni_se_esta_cerca() -> None:
    rooms = Salas(clock=Clock(), check=False)
    ana, bea, cris = FakeClient(), FakeClient(), FakeClient()
    a = await rooms.join(1, 10, "Ana", ana)
    b = await rooms.join(1, 20, "Bea", bea)
    c = await rooms.join(1, 30, "Cris", cris, layer="piso")
    room = rooms.rooms[1]
    for p in (a, b, c):
        rooms.pose(1, p, pose(x=0, z=0))
    await rooms.tick(room)
    assert {p.user_id for p in rooms.in_layer(1, "")} == {10, 20}
    assert sala_near(rooms, a) == [20]
    # Ana entra: Bea deja de verla y Ana pasa a ver solo a Cris.
    assert rooms.set_layer(1, 10, "piso")
    await rooms.tick(room)
    assert bea.texts[-1] == {"t": "lejos", "slots": [a.slot]}
    assert sorted(slot for slot, *_ in decode(ana.blobs[-1])) == [a.slot, c.slot]
    assert sala_near(rooms, a) == [30]
    # Lo de una capa solo le llega a esa capa.
    await rooms.announce(1, {"t": "uso"}, skip_user=10, layer="piso")
    assert cris.texts[-1] == {"t": "uso"} and bea.texts[-1]["t"] == "lejos"
    assert not rooms.set_layer(1, 99, "piso")
    await rooms.close()


def sala_near(rooms: Salas, player: object) -> list[int]:
    return [p.user_id for p in sala_near_of(rooms.rooms[1], player)]  # type: ignore[arg-type]


def decode(blob: bytes) -> list[tuple[int, ...]]:
    return list(entries(blob)[1])
