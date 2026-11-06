"""Salas del multijugador del Jovaniverso: entrar, salir, poses, ritmo y topes."""

from __future__ import annotations

import json
import struct

import pytest

from bot.services import universo_sala as sala
from bot.services.universo_sala import SalaError, Salas, parse_pose, snapshot


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
    return struct.pack("<BhhhBB", sala.POSE, x, y, z, yaw, anim)


def entries(blob: bytes) -> list[tuple[int, int, int, int, int, int]]:
    kind, count = struct.unpack_from("<BB", blob)
    assert kind == sala.POSE
    return [struct.unpack_from("<BhhhBB", blob, 2 + 9 * i) for i in range(count)]


@pytest.fixture
async def salas() -> Salas:
    rooms = Salas(clock=Clock())
    yield rooms
    await rooms.close()


async def test_al_entrar_te_dan_tu_hueco_y_los_demas_se_enteran(salas: Salas) -> None:
    ana, bea = FakeClient(), FakeClient()
    first = await salas.join(1, 10, "Ana", ana)
    second = await salas.join(1, 20, "Bea", bea)
    assert ana.texts[0] == {"t": "bienvenida", "slot": first.slot, "jugadores": []}
    assert bea.texts[0]["jugadores"] == [{"slot": first.slot, "id": "10", "nombre": "Ana"}]
    assert ana.texts[1] == {"t": "entra", "slot": second.slot, "id": "20", "nombre": "Bea"}
    await salas.leave(1, second)
    assert ana.texts[2] == {"t": "sale", "slot": second.slot}


async def test_un_tick_manda_lo_cambiado_en_un_solo_mensaje_igual_para_todos(salas: Salas) -> None:
    ana, bea = FakeClient(), FakeClient()
    a = await salas.join(1, 10, "Ana", ana)
    b = await salas.join(1, 20, "Bea", bea)
    salas.pose(1, a, pose(x=5))
    salas.pose(1, b, pose(x=-7, anim=2))
    room = salas.rooms[1]
    await salas.tick(room)
    assert ana.blobs == bea.blobs
    assert entries(ana.blobs[0]) == [(a.slot, 5, 20, 30, 7, 1), (b.slot, -7, 20, 30, 7, 2)]
    # Sin cambios no se manda nada; la misma pose tampoco cuenta como cambio.
    salas.pose(1, a, pose(x=5))
    await salas.tick(room)
    assert len(ana.blobs) == 1


async def test_quien_llega_ve_donde_estan_los_demas(salas: Salas) -> None:
    a = await salas.join(1, 10, "Ana", FakeClient())
    salas.pose(1, a, pose(x=42))
    late = FakeClient()
    await salas.join(1, 20, "Bea", late)
    assert entries(late.blobs[0]) == [(a.slot, 42, 20, 30, 7, 1)]


async def test_las_poses_raras_o_fuera_del_mundo_no_cuentan() -> None:
    assert parse_pose(pose()) is not None
    assert parse_pose(pose()[:-1]) is None
    assert parse_pose(b"\x02" + pose()[1:]) is None
    assert parse_pose(pose(x=sala.WORLD_DM + 1)) is None
    assert parse_pose(pose(y=sala.MIN_Y_DM - 1)) is None


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
    assert [p.client for p in salas.present(1)] == [new]
    salas.pose(1, first, pose())  # la conexión vieja ya no mueve a nadie
    assert salas.rooms[1].dirty == set()
    assert salas.rooms[1].task is not None


async def test_la_sala_vacia_se_cierra_y_la_llena_no_deja_entrar(salas: Salas) -> None:
    players = [await salas.join(1, i, f"P{i}", FakeClient()) for i in range(sala.MAX_PLAYERS)]
    with pytest.raises(SalaError):
        await salas.join(1, 999, "Tarde", FakeClient())
    task = salas.rooms[1].task
    for player in players:
        await salas.leave(1, player)
    assert 1 not in salas.rooms
    assert task is not None and (task.cancelled() or task.cancelling())


async def test_al_que_va_atrasado_se_le_saltan_ticks(salas: Salas) -> None:
    slow, fast = FakeClient(), FakeClient()
    a = await salas.join(1, 10, "Lento", slow, backlog=lambda: sala.MAX_BACKLOG_BYTES + 1)
    await salas.join(1, 20, "Rápido", fast)
    salas.pose(1, a, pose())
    await salas.tick(salas.rooms[1])
    assert slow.blobs == []
    assert len(fast.blobs) == 1


def test_el_tick_de_una_sala_llena_cabe_en_medio_kb() -> None:
    blob = snapshot([(slot, pose()[1:]) for slot in range(sala.MAX_PLAYERS)])
    assert len(blob) == 2 + 9 * sala.MAX_PLAYERS < 512
