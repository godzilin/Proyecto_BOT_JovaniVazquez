"""Salas del multijugador del Jovaniverso: entrar, salir, poses, ritmo y topes."""

from __future__ import annotations

import json
import struct

import pytest

from bot.services import universo_sala as sala
from bot.services.universo import world_places
from bot.services.universo_sala import SalaError, Salas, parse_pose


class FakeClient:
    """Una conexión que apunta lo que recibe."""

    def __init__(self) -> None:
        self.texts: list[dict[str, object]] = []
        self.blobs: list[bytes] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.texts.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.blobs.append(data)

    async def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def pose(x: int = 10, y: int = 20, z: int = 30, yaw: int = 7, anim: int = 1) -> bytes:
    return sala.pack_pose(x, y, z, yaw, anim)


Entry = tuple[int, int, int, int, int, int]


def entries(blob: bytes) -> tuple[int, list[Entry]]:
    """El tipo de un mensaje de la sala y sus poses `(hueco, x, y, z, giro, anim)`, en absoluto."""
    kind, o, out = blob[0], 1, []
    while o < len(blob):
        rx, rz, count = struct.unpack_from("<hhB", blob, o)
        o += 5
        for _ in range(count):
            slot, dx, y, dz, yaw, anim = struct.unpack_from("<BhHhBB", blob, o)
            o += 9
            x0, z0 = rx * sala.REGION_DM, rz * sala.REGION_DM
            out.append((slot, x0 + dx, y + sala.MIN_Y_DM, z0 + dz, yaw, anim))
    return kind, sorted(out)


def slots(blob: bytes) -> list[int]:
    return [entry[0] for entry in entries(blob)[1]]


@pytest.fixture
async def salas() -> Salas:
    rooms = Salas(clock=Clock())
    yield rooms
    await rooms.close()


async def test_al_entrar_te_dan_tu_hueco_y_los_demas_se_enteran(salas: Salas) -> None:
    ana, bea = FakeClient(), FakeClient()
    first = await salas.join(1, 10, "Ana", ana)
    second = await salas.join(1, 20, "Bea", bea)
    assert ana.texts[0] == {"t": "bienvenida", "slot": first.slot, "id": "10", "jugadores": []}
    assert bea.texts[0]["jugadores"] == [{"slot": first.slot, "id": "10", "nombre": "Ana"}]
    assert ana.texts[1] == {"t": "entra", "slot": second.slot, "id": "20", "nombre": "Bea"}
    await salas.leave(1, second)
    assert ana.texts[2] == {"t": "sale", "slot": second.slot}


async def test_un_tick_manda_lo_cambiado_en_un_solo_mensaje_igual_para_todos(salas: Salas) -> None:
    ana, bea = FakeClient(), FakeClient()
    a = await salas.join(1, 10, "Ana", ana)
    b = await salas.join(1, 20, "Bea", bea)
    salas.pose(1, a, pose(x=5))
    salas.pose(1, b, pose(x=7, anim=2))
    room = salas.rooms[1]
    await salas.tick(room)
    # La primera vez, todo lo que ven (acaban de llegar).
    both = [(a.slot, 5, 20, 30, 7, 1), (b.slot, 7, 20, 30, 7, 2)]
    assert entries(ana.blobs[0]) == entries(bea.blobs[0]) == (sala.FULL, both)
    salas.clock.now += 0.1  # type: ignore[attr-defined]
    salas.pose(1, a, pose(x=6))
    salas.pose(1, b, pose(x=8, anim=2))
    await salas.tick(room)
    # Luego, solo lo cambiado, y los mismos bytes (el mismo objeto) para los de la región.
    assert ana.blobs[1] is bea.blobs[1]
    moved = [(a.slot, 6, 20, 30, 7, 1), (b.slot, 8, 20, 30, 7, 2)]
    assert entries(ana.blobs[1]) == (sala.POSE, moved)
    # Si lo único cambiado eres tú, a ti no se te manda (la página ignora su propia pose).
    salas.clock.now += 0.1  # type: ignore[attr-defined]
    salas.pose(1, a, pose(x=7))
    await salas.tick(room)
    assert len(ana.blobs) == 2
    assert entries(bea.blobs[2]) == (sala.POSE, [(a.slot, 7, 20, 30, 7, 1)])
    # Sin cambios no se manda nada; la misma pose tampoco cuenta como cambio.
    salas.pose(1, a, pose(x=7))
    await salas.tick(room)
    assert (len(ana.blobs), len(bea.blobs)) == (2, 3)


async def test_quien_llega_ve_donde_estan_los_demas(salas: Salas) -> None:
    a = await salas.join(1, 10, "Ana", FakeClient())
    salas.pose(1, a, pose(x=42))
    await salas.tick(salas.rooms[1])
    late = FakeClient()
    b = await salas.join(1, 20, "Bea", late)
    assert late.blobs == []  # aún no se sabe qué tiene cerca
    salas.pose(1, b, pose(x=-42))
    await salas.tick(salas.rooms[1])
    assert entries(late.blobs[0]) == (
        sala.FULL,
        [(a.slot, 42, 20, 30, 7, 1), (b.slot, -42, 20, 30, 7, 1)],
    )


async def test_solo_te_llegan_los_que_tienes_cerca() -> None:
    rooms = Salas(clock=Clock(), check=False)
    ana, bea, eva = FakeClient(), FakeClient(), FakeClient()
    a = await rooms.join(1, 10, "Ana", ana)
    b = await rooms.join(1, 20, "Bea", bea)
    e = await rooms.join(1, 30, "Eva", eva)
    sure = sala.REGION_DM - 2 * sala.HYSTERESIS_DM  # a menos de esto se ve siempre
    rooms.pose(1, a, pose(x=sala.HYSTERESIS_DM - 1, z=0))
    rooms.pose(1, b, pose(x=sala.HYSTERESIS_DM - 1 - sure + 1, z=0))
    rooms.pose(1, e, pose(x=200_000, z=-150_000))  # a 25 km
    await rooms.tick(rooms.rooms[1])
    assert slots(ana.blobs[0]) == [a.slot, b.slot]
    assert slots(eva.blobs[0]) == [e.slot]
    # Eva se mueve: no le llega a nadie más; Ana se mueve: le llega a Bea, no a Eva.
    rooms.pose(1, e, pose(x=200_010, z=-150_000))
    rooms.pose(1, a, pose(x=10, z=0))
    await rooms.tick(rooms.rooms[1])
    assert slots(bea.blobs[1]) == [a.slot]
    assert len(eva.blobs) == 1  # solo se ha movido ella
    await rooms.close()


async def test_quien_se_va_lejos_desaparece_y_quien_llega_ve_a_los_quietos() -> None:
    rooms = Salas(clock=Clock(), check=False)
    ana, bea = FakeClient(), FakeClient()
    a = await rooms.join(1, 10, "Ana", ana)
    b = await rooms.join(1, 20, "Bea", bea)
    room = rooms.rooms[1]
    rooms.pose(1, a, pose(x=0, z=0))
    rooms.pose(1, b, pose(x=100, z=0))
    await rooms.tick(room)
    ana.texts.clear()
    # Bea se va a 1 km: Ana deja de verla (`lejos`) y Bea recibe lo suyo, sin Ana.
    rooms.pose(1, b, pose(x=10_000, z=0))
    await rooms.tick(room)
    assert ana.texts == [{"t": "lejos", "slots": [b.slot]}]
    assert entries(bea.blobs[-1]) == (sala.FULL, [(b.slot, 10_000, 20, 0, 7, 1)])
    # Vuelve: le llega Ana, que está quieta, y a Ana, la pose de Bea como cualquier cambio.
    rooms.pose(1, b, pose(x=50, z=0))
    await rooms.tick(room)
    assert entries(bea.blobs[-1]) == (
        sala.FULL,
        [(a.slot, 0, 20, 0, 7, 1), (b.slot, 50, 20, 0, 7, 1)],
    )
    assert entries(ana.blobs[-1]) == (sala.POSE, [(b.slot, 50, 20, 0, 7, 1)])
    assert room.cells == {(0, 0): {a.slot, b.slot}}
    await rooms.leave(1, b)
    assert room.cells == {(0, 0): {a.slot}}
    await rooms.close()


async def test_pasear_por_la_raya_de_una_region_no_cambia_de_region() -> None:
    edge, h = sala.REGION_DM, sala.HYSTERESIS_DM
    assert sala.region_of(edge - 1, 0) == (0, 0)
    assert sala.region_of(edge, 0) == (1, 0)
    # Ya dentro de una, hasta salirse más de `HYSTERESIS_DM` no cambia.
    assert sala.region_of(edge + h - 1, 0, (0, 0)) == (0, 0)
    assert sala.region_of(-h, 0, (0, 0)) == (0, 0)
    assert sala.region_of(edge + h, 0, (0, 0)) == (1, 0)
    assert sala.region_of(-h - 1, 0, (0, 0)) == (-1, 0)
    rooms = Salas(clock=Clock(), check=False)
    ana = FakeClient()
    a = await rooms.join(1, 10, "Ana", ana)
    for i in range(20):  # a un lado y a otro de la raya, a 5 m
        rooms.clock.now += 0.1  # type: ignore[attr-defined]
        rooms.pose(1, a, pose(x=edge + (50 if i % 2 else -50)))
        await rooms.tick(rooms.rooms[1])
    assert a.region == (0, 0)
    assert [blob[0] for blob in ana.blobs] == [sala.FULL]  # nunca le ha tocado otro
    await rooms.close()


async def test_el_mundo_grande_y_el_teide_caben_en_la_pose() -> None:
    far, top = sala.WORLD_DM, 37_180  # el Teide, en dm
    assert parse_pose(pose(x=far, y=top, z=-far)) is not None
    rooms = Salas(clock=Clock(), check=False)
    ana = FakeClient()
    a = await rooms.join(1, 10, "Ana", ana)
    rooms.pose(1, a, pose(x=-far, y=top, z=far - 3))
    await rooms.tick(rooms.rooms[1])
    assert entries(ana.blobs[0]) == (sala.FULL, [(a.slot, -far, top, far - 3, 7, 1)])
    assert sala.place(a.pose) == (-far, top, far - 3)  # type: ignore[arg-type]
    await rooms.close()


async def test_las_poses_raras_o_fuera_del_mundo_no_cuentan() -> None:
    assert parse_pose(pose()) is not None
    assert parse_pose(pose()[:-1]) is None
    assert parse_pose(b"\x01" + pose()[1:]) is None  # el tipo de la versión 1
    assert parse_pose(struct.pack("<BhhhBB", 1, 0, 0, 0, 0, 0)) is None  # su pose
    assert parse_pose(pose(x=sala.WORLD_DM + 1)) is None
    assert parse_pose(pose(z=-sala.WORLD_DM - 1)) is None
    assert parse_pose(pose(y=sala.MAX_Y_DM + 1)) is None
    with pytest.raises(struct.error):
        pose(y=sala.MIN_Y_DM - 1)  # ni se puede mandar


async def test_mas_de_quince_poses_por_segundo_se_descartan(salas: Salas) -> None:
    a = await salas.join(1, 10, "Ana", FakeClient())
    for i in range(30):
        salas.pose(1, a, pose(x=i))
    assert a.pose == pose(x=sala.MAX_POSES_PER_SECOND - 1)[1:]
    salas.clock.now += 1.0  # type: ignore[attr-defined]
    salas.pose(1, a, pose(x=99))
    assert a.pose == pose(x=99)[1:]


async def test_la_misma_persona_en_otra_pestana_echa_a_la_vieja(salas: Salas) -> None:
    old, new = FakeClient(), FakeClient()
    first = await salas.join(1, 10, "Ana", old)
    await salas.join(1, 10, "Ana", new)
    assert old.closed
    assert old.texts[-1] == {"t": "error", "error": sala.ELSEWHERE}
    assert [p.client for p in salas.present(1)] == [new]
    salas.pose(1, first, pose())  # la conexión vieja ya no mueve a nadie
    assert salas.rooms[1].dirty == set()
    assert salas.rooms[1].task is not None


async def test_la_sala_vacia_se_cierra_y_la_llena_no_deja_entrar(salas: Salas) -> None:
    players = [await salas.join(1, i, f"P{i}", FakeClient()) for i in range(sala.MAX_PLAYERS)]
    with pytest.raises(SalaError):
        await salas.join(1, 999, "Tarde", FakeClient())
    for player in players:
        salas.pose(1, player, pose(x=player.slot))
    await salas.tick(salas.rooms[1])
    assert len(salas.rooms[1].cells[0, 0]) == sala.MAX_PLAYERS
    task = salas.rooms[1].task
    for player in players:
        await salas.leave(1, player)
    assert 1 not in salas.rooms
    assert task is not None and (task.cancelled() or task.cancelling())


async def test_al_que_va_atrasado_se_le_saltan_ticks_y_luego_le_llega_todo(salas: Salas) -> None:
    slow, fast = FakeClient(), FakeClient()
    behind = [True]
    late = await salas.join(
        1, 10, "Lento", slow, backlog=lambda: (sala.MAX_BACKLOG_BYTES + 1) * behind[0]
    )
    quick = await salas.join(1, 20, "Rápido", fast)
    salas.pose(1, late, pose())
    salas.pose(1, quick, pose(x=0))
    room = salas.rooms[1]
    await salas.tick(room)
    assert slow.blobs == []
    assert len(fast.blobs) == 1
    # Se pone al día sin que nadie se mueva: en el tick siguiente, todo lo que ve.
    behind[0] = False
    await salas.tick(room)
    assert entries(slow.blobs[0])[0] == sala.FULL
    assert slots(slow.blobs[0]) == [late.slot, quick.slot]
    await salas.tick(room)
    assert len(slow.blobs) == 1 and len(fast.blobs) == 1


def test_el_tick_de_una_sala_llena_en_un_sitio_cabe_en_medio_kb() -> None:
    full = sala.group((0, 0), [(slot, pose()[1:]) for slot in range(sala.MAX_PLAYERS)])
    # Como en la versión 1 (9 bytes por jugador), más 5 de la cabecera del grupo.
    assert 1 + len(full) == 1 + 5 + 9 * sala.MAX_PLAYERS < 512


async def test_un_gesto_sin_comprar_se_ve_como_quieto() -> None:
    hits: list[int] = []
    salas = Salas(clock=Clock(), on_gesture=lambda _g, _p, anim, _n: hits.append(anim))
    try:
        ana = await salas.join(1, 10, "Ana", FakeClient(), gestures=frozenset({5}))
        salas.pose(1, ana, pose(anim=9))
        assert ana.pose is not None and ana.pose[-1] == 0
        salas.pose(1, ana, pose(anim=5))
        assert ana.pose[-1] == 5
        assert hits == [5]
        assert parse_pose(pose(anim=200), frozenset({5})) == pose(anim=0)[1:]
        # Agacharse y deslizarse son de todos y no cuentan como gesto.
        salas.pose(1, ana, pose(anim=12))
        assert ana.pose[-1] == 12
        assert hits == [5]
    finally:
        await salas.close()


async def test_el_gesto_avisa_al_empezar_con_quien_hay_cerca_y_no_mas_de_uno_por_segundo() -> None:
    clock = Clock()
    hits: list[tuple[int, list[int]]] = []
    salas = Salas(
        clock=clock,
        on_gesture=lambda _g, p, anim, near: hits.append((anim, [o.user_id for o in near])),
    )
    try:
        everything = frozenset(range(4, 11))
        ana = await salas.join(1, 10, "Ana", FakeClient(), gestures=everything)
        bea = await salas.join(1, 20, "Bea", FakeClient())
        eva = await salas.join(1, 30, "Eva", FakeClient())
        salas.pose(1, bea, pose(x=10 + sala.NEAR_DM, z=30))
        salas.pose(1, eva, pose(x=10 + sala.NEAR_DM + 1, z=30))
        salas.pose(1, ana, pose(anim=4))
        salas.pose(1, ana, pose(x=11, anim=4))  # sigue el mismo gesto: no avisa
        clock.now += 0.5
        salas.pose(1, ana, pose(anim=6))  # otro gesto antes de un segundo: no cuenta
        clock.now += 1.0
        salas.pose(1, ana, pose(anim=5))
        assert hits == [(4, [20]), (5, [20])]
    finally:
        await salas.close()


async def test_un_aviso_de_cerca_llega_a_quien_esta_cerca_y_no_a_quien_esta_lejos(
    salas: Salas,
) -> None:
    ana, bea, eva = FakeClient(), FakeClient(), FakeClient()
    tira = await salas.join(1, 10, "Ana", ana)
    cerca = await salas.join(1, 20, "Bea", bea)
    lejos = await salas.join(1, 30, "Eva", eva)
    for client in (ana, bea, eva):
        client.texts.clear()
    around = sala.SPIN_SEEN_DM
    # Sin pose de quien tira no se sabe dónde ocurre: no llega a nadie.
    await salas.announce(1, {"t": "traga"}, skip_user=10, around_dm=around)
    assert ana.texts == bea.texts == eva.texts == []
    salas.pose(1, tira, pose(x=0, z=0))
    salas.pose(1, cerca, pose(x=around, z=0))
    salas.pose(1, lejos, pose(x=around, z=1))
    await salas.announce(1, {"t": "traga"}, skip_user=10, around_dm=around)
    assert bea.texts == [{"t": "traga"}]
    assert ana.texts == eva.texts == []


# -- Velocidad y paseo ----------------------------------------------------------------------


def walking() -> tuple[Salas, list[tuple[int, sala.Stroll]]]:
    """Una sala con reloj falso que apunta cada lote de paseo `(usuario, lo paseado)`."""
    lotes: list[tuple[int, sala.Stroll]] = []
    rooms = Salas(clock=Clock(), on_walk=lambda _g, p, s: lotes.append((p.user_id, s)))
    return rooms, lotes


def at(player: sala.Player) -> tuple[int, int, int]:
    assert player.pose is not None
    return sala.place(player.pose)


async def test_una_carrera_a_tope_con_la_red_a_trompicones_se_acepta_entera() -> None:
    rooms, lotes = walking()
    a = await rooms.join(1, 10, "Ana", FakeClient())
    clock = rooms.clock
    # 20 m/s (`VEL_MAX`) a 10 poses por segundo: 20 dm por pose. Cada tres, un parón de la
    # red: dos poses llegan juntas.
    for i in range(290):
        clock.now += 0.0 if i % 3 == 1 else 0.15 if i % 3 == 2 else 0.1  # type: ignore[attr-defined]
        rooms.pose(1, a, pose(x=-2900 + 20 * i, y=20, z=0, anim=2))
    assert at(a) == (-2900 + 20 * 289, 20, 0)
    await rooms.leave(1, a)
    assert sum(s.meters for _u, s in lotes) == 578  # 289 tramos de 2 m
    await rooms.close()


async def test_un_salto_imposible_se_recorta_a_lo_que_da_la_velocidad(salas: Salas) -> None:
    a = await salas.join(1, 10, "Ana", FakeClient())
    salas.pose(1, a, pose(x=0, y=20, z=0))
    salas.clock.now += 0.1  # type: ignore[attr-defined]
    salas.pose(1, a, pose(x=2900, y=20, z=0))  # 290 m en una décima
    x, _y, _z = at(a)
    # Llega lo que le quedaba guardado (`SLACK_SECONDS`) y nada más.
    assert x <= sala.RUN_DM_PER_SECOND * (sala.SLACK_SECONDS + 0.1)
    salas.clock.now += 0.1  # type: ignore[attr-defined]
    salas.pose(1, a, pose(x=2900, y=20, z=0))
    assert at(a)[0] - x <= sala.RUN_DM_PER_SECOND * 0.1 + 1  # luego, a la velocidad máxima
    # Volar hacia arriba tampoco: se sube como mucho a `CLIMB_DM_PER_SECOND`.
    before = at(a)[1]
    salas.clock.now += 0.1  # type: ignore[attr-defined]
    salas.pose(1, a, pose(x=at(a)[0], y=1900, z=0))
    assert at(a)[1] - before <= sala.CLIMB_DM_PER_SECOND * (sala.SLACK_SECONDS + 0.4) + 1
    assert at(a)[1] < 1900
    # Caer, en cambio, no tiene tope.
    salas.clock.now += 0.1  # type: ignore[attr-defined]
    salas.pose(1, a, pose(x=at(a)[0], y=-400, z=0))
    assert at(a)[1] == -400


async def test_un_teleporte_avisado_pasa_entero_y_no_cuenta_como_paseo() -> None:
    rooms, lotes = walking()
    a = await rooms.join(1, 10, "Ana", FakeClient())
    rooms.pose(1, a, pose(x=0, y=20, z=0))
    rooms.text(1, a, json.dumps({"t": "salto"}))
    rooms.clock.now += 0.1  # type: ignore[attr-defined]
    rooms.pose(1, a, pose(x=-50, y=497, z=80))  # a la cima, 30 m más allá
    assert at(a) == (-50, 497, 80)
    # Un segundo aviso enseguida no vale: hasta `LEAP_SECONDS`, se recorta como siempre.
    rooms.text(1, a, json.dumps({"t": "salto"}))
    rooms.clock.now += 0.1  # type: ignore[attr-defined]
    rooms.pose(1, a, pose(x=2900, y=20, z=0))
    assert at(a)[0] < 2900
    # Pasado el plazo, vuelve a valer; y un aviso que no se usa a tiempo caduca.
    rooms.clock.now += sala.LEAP_SECONDS  # type: ignore[attr-defined]
    rooms.text(1, a, json.dumps({"t": "salto"}))
    rooms.clock.now += sala.LEAP_VALID_SECONDS + 0.1  # type: ignore[attr-defined]
    start = at(a)[0]
    rooms.pose(1, a, pose(x=-2900, y=20, z=0))
    assert at(a)[0] > -2900 and at(a)[0] < start
    await rooms.leave(1, a)
    (stroll,) = [s for _u, s in lotes]
    assert "nieves" not in stroll.places  # se llegó saltando


async def test_los_metros_saltos_y_lugares_salen_de_las_poses_por_lotes() -> None:
    rooms, lotes = walking()
    a = await rooms.join(1, 10, "Ana", FakeClient())
    clock = rooms.clock
    # De la plaza (-80, 600) hacia el este, corriendo, con dos saltos y un deslizamiento.
    anims = [2, 3, 3, 2, 3, 2, 12, 12, 2, 2]
    for i, anim in enumerate(anims):
        clock.now += 0.1  # type: ignore[attr-defined]
        rooms.pose(1, a, pose(x=-80 + 15 * i, y=80, z=600, anim=anim))
    assert lotes == []  # el lote sale cada `WALK_SECONDS`
    clock.now += sala.WALK_SECONDS  # type: ignore[attr-defined]
    rooms.pose(1, a, pose(x=-80 + 15 * 10, y=80, z=600))
    ((user, stroll),) = lotes
    assert user == 10
    assert (stroll.meters, stroll.jumps, stroll.slides) == (15, 2, 1)
    assert stroll.places == ["plaza"]
    # Lo ya pisado no vuelve a contar en la misma visita.
    clock.now += 0.1  # type: ignore[attr-defined]
    rooms.pose(1, a, pose(x=-80, y=80, z=600))
    await rooms.leave(1, a)
    assert lotes[1][1].places == [] and lotes[1][1].meters == 15
    await rooms.close()


async def test_los_saltos_se_recortan_al_tiempo_y_con_devtools_no_hay_paseo() -> None:
    rooms, lotes = walking()
    a = await rooms.join(1, 10, "Ana", FakeClient())
    for i in range(14):  # siete saltos en un segundo
        rooms.clock.now += 0.07  # type: ignore[attr-defined]
        rooms.pose(1, a, pose(x=i, anim=3 if i % 2 else 0))
    rooms.text(1, a, json.dumps({"t": "trucos", "on": True}))
    for i in range(10):
        rooms.clock.now += 0.1  # type: ignore[attr-defined]
        rooms.pose(1, a, pose(x=10 + 20 * i, z=-80, anim=3 if i % 2 else 0))
    rooms.clock.now += 0.25  # type: ignore[attr-defined]
    await rooms.leave(1, a)
    ((_u, stroll),) = lotes
    assert stroll.jumps == 4  # 2,2 s dentro: caben 4 de los 7
    assert stroll.meters == 1  # los 1,3 m de antes de las devtools
    await rooms.close()


async def test_el_extraplano_no_mira_la_velocidad() -> None:
    rooms = Salas(clock=Clock(), check=False)
    a = await rooms.join(1, 10, "Ana", FakeClient())
    rooms.pose(1, a, pose(x=-2900))
    rooms.pose(1, a, pose(x=2900, y=1900))
    assert at(a) == (2900, 1900, 30)
    await rooms.close()


async def test_los_lugares_salen_del_mundo_publicado() -> None:
    rooms, lotes = walking()
    # La plaza, movida 8 m al este y más pequeña.
    zonas = {"zonas": {"plaza": {"x": 0, "z": 60, "r": 5}}}
    a = await rooms.join(
        1, 10, "Ana", FakeClient(), places=world_places({"zonas": json.dumps(zonas)})
    )
    rooms.pose(1, a, pose(x=-80, y=80, z=530))  # en la de serie, no en la movida
    assert rooms.places_at(1, a) == []
    rooms.clock.now += 0.2  # type: ignore[attr-defined]
    rooms.pose(1, a, pose(x=-10, y=80, z=600))
    assert rooms.places_at(1, a) == ["plaza"]
    rooms.set_places(1, world_places({}))  # se publica otra vez la de serie
    rooms.clock.now += 0.2  # type: ignore[attr-defined]
    rooms.pose(1, a, pose(x=-80, y=80, z=530))
    assert rooms.places_at(1, a) == ["plaza"]
    await rooms.leave(1, a)
    assert [p for _u, s in lotes for p in s.places] == ["plaza"]  # una vez por visita
    await rooms.close()
