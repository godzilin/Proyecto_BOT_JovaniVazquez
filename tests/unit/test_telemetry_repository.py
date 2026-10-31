"""Pruebas del repositorio de telemetría contra un SQLite real en `tmp_path`.

Cubren el upsert de contadores, la creación de tablas sin `initialize`, la
retención (pliegue a `hour = -1` y borrados) y los agregados del informe, con las
tablas ajenas (`casino_plays`, `economy_*`) creadas a mano o ausentes.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from bot.repositories.casino_stats import CasinoStatsRepository
from bot.repositories.economy import EconomyRepository
from bot.repositories.telemetry import (
    COUNTS_RETENTION_DAYS,
    HOURLY_RETENTION_DAYS,
    SYSTEM_RETENTION_DAYS,
    USER_RETENTION_DAYS,
    TelemetryRepository,
)
from bot.services.levels import TIMEZONE
from bot.services.telemetry import (
    Batch,
    Counter,
    CounterKey,
    SystemSample,
    UserKey,
)

GUILD = 1
OTHER_GUILD = 2
# Martes 6 de octubre de 2026, 21:30 en Canarias.
NOW = datetime(2026, 10, 6, 21, 30, tzinfo=TIMEZONE).timestamp()
TODAY = datetime.fromtimestamp(NOW, TIMEZONE).date()


def day_ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def counter(**values: int) -> Counter:
    result = Counter()
    for name, value in values.items():
        setattr(result, name, value)
    return result


def fetch(path: Path, sql: str, *args: object) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(sql, args).fetchall()


def test_initialize_es_idempotente(tmp_path: Path) -> None:
    repository = TelemetryRepository(tmp_path / "bot.db")

    async def scenario() -> None:
        await repository.initialize()
        await repository.initialize()

    asyncio.run(scenario())
    tables = {r[0] for r in fetch(tmp_path / "bot.db", "SELECT name FROM sqlite_master")}
    assert {"telemetry_counts", "telemetry_users", "telemetry_system"} <= tables


def test_write_sin_initialize_previo_crea_las_tablas(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    batch = Batch({CounterKey("2026-10-06", 21, GUILD, "slash", "x"): counter(events=1)})
    asyncio.run(TelemetryRepository(path).write(batch))
    assert fetch(path, "SELECT events FROM telemetry_counts") == [(1,)]


def test_dos_flush_suman_con_upsert_y_conservan_el_maximo(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    repository = TelemetryRepository(path)
    key = CounterKey("2026-10-06", 21, GUILD, "slash", "x")

    async def scenario() -> None:
        await repository.initialize()
        first = Counter()
        first.add(events=2, error=True, ms=2000)
        second = Counter()
        second.add(events=3, error=False, ms=100)
        await repository.write(Batch({key: first}))
        await repository.write(Batch({key: second}))

    asyncio.run(scenario())
    rows = fetch(
        path,
        "SELECT events, errors, ms_count, ms_total, ms_max, ms_lt500, ms_lt3000 "
        "FROM telemetry_counts",
    )
    assert rows == [(5, 1, 2, 2100, 2000, 1, 1)]


def test_write_guarda_usuarios_sin_duplicar_y_muestras(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    user = UserKey("2026-10-06", GUILD, "slash", "x", 7)
    sample = SystemSample(NOW, "2026-10-06", None, 120.5, 3.0, None, 1, 2)

    async def scenario() -> None:
        repository = TelemetryRepository(path)
        await repository.write(Batch(users={user}, samples=[sample]))
        await repository.write(Batch(users={user}))

    asyncio.run(scenario())
    assert fetch(path, "SELECT user_id FROM telemetry_users") == [(7,)]
    assert fetch(path, "SELECT memory_mb, rss_mb, chromium, node FROM telemetry_system") == [
        (None, 120.5, 1, 2)
    ]


def test_prune_pliega_horas_viejas_conservando_totales(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    old = day_ago(HOURLY_RETENTION_DAYS + 1)
    recent = day_ago(1)
    batch = Batch(
        {
            CounterKey(old, 3, GUILD, "slash", "x"): counter(
                events=2, ms_count=1, ms_total=100, ms_max=100, ms_lt500=1
            ),
            CounterKey(old, 20, GUILD, "slash", "x"): counter(
                events=5, errors=1, ms_count=1, ms_total=900, ms_max=900, ms_lt1000=1
            ),
            CounterKey(old, -1, GUILD, "slash", "x"): counter(events=10, ms_max=50),
            CounterKey(recent, 3, GUILD, "slash", "x"): counter(events=4),
        }
    )

    async def scenario() -> None:
        repository = TelemetryRepository(path)
        await repository.write(batch)
        await repository.prune(now=NOW)
        # Segunda pasada: no cambia nada.
        await repository.prune(now=NOW)

    asyncio.run(scenario())
    rows = fetch(
        path,
        "SELECT day, hour, events, errors, ms_count, ms_total, ms_max, ms_lt500, ms_lt1000 "
        "FROM telemetry_counts ORDER BY day, hour",
    )
    assert rows == [
        (old, -1, 17, 1, 2, 1000, 900, 1, 1),
        (recent, 3, 4, 0, 0, 0, 0, 0, 0),
    ]


def test_prune_borra_contadores_usuarios_y_sistema_viejos(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    ancient = day_ago(COUNTS_RETENTION_DAYS + 1)
    batch = Batch(
        {
            CounterKey(ancient, -1, GUILD, "slash", "x"): counter(events=1),
            CounterKey(day_ago(COUNTS_RETENTION_DAYS - 1), -1, GUILD, "slash", "x"): counter(
                events=1
            ),
        },
        {
            UserKey(day_ago(USER_RETENTION_DAYS + 1), GUILD, "slash", "x", 1),
            UserKey(day_ago(USER_RETENTION_DAYS - 1), GUILD, "slash", "x", 2),
        },
        [
            SystemSample(
                NOW - (SYSTEM_RETENTION_DAYS + 1) * 86400, day_ago(15), 1.0, 1.0, 1.0, None, 0, 0
            ),
            SystemSample(NOW - 3600, day_ago(0), 1.0, 1.0, 1.0, None, 0, 0),
        ],
    )

    async def scenario() -> None:
        repository = TelemetryRepository(path)
        await repository.write(batch)
        await repository.prune(now=NOW)

    asyncio.run(scenario())
    assert len(fetch(path, "SELECT 1 FROM telemetry_counts")) == 1
    assert fetch(path, "SELECT user_id FROM telemetry_users") == [(2,)]
    assert len(fetch(path, "SELECT 1 FROM telemetry_system")) == 1


async def seed_report(path: Path) -> TelemetryRepository:
    repository = TelemetryRepository(path)
    today = TODAY.isoformat()
    await repository.write(
        Batch(
            {
                CounterKey(today, 21, GUILD, "slash", "poner"): counter(
                    events=3, errors=1, ms_count=3, ms_total=600, ms_max=300, ms_lt500=3
                ),
                CounterKey(today, 22, GUILD, "slash", "poner"): counter(events=2),
                CounterKey(day_ago(1), -1, GUILD, "slash", "poner"): counter(events=10),
                CounterKey(today, 21, GUILD, "text", "ayuda"): counter(events=1),
                CounterKey(today, 21, OTHER_GUILD, "slash", "poner"): counter(events=99),
                CounterKey(day_ago(30), 5, GUILD, "slash", "poner"): counter(events=77),
            },
            {
                UserKey(today, GUILD, "slash", "poner", 5),
                UserKey(today, OTHER_GUILD, "slash", "poner", 6),
                UserKey(day_ago(30), GUILD, "slash", "poner", 7),
            },
            [
                SystemSample(NOW, today, 400.0, 200.0, 5.0, 40.0, 1, 2),
                SystemSample(NOW + 1, today, 600.0, 300.0, 9.0, None, 0, 1),
            ],
        )
    )
    return repository


def test_report_data_agrega_contadores_usuarios_y_sistema(tmp_path: Path) -> None:
    async def scenario():
        repository = await seed_report(tmp_path / "bot.db")
        return await repository.report_data(
            guild_id=GUILD, since_day=day_ago(6), since_ts=NOW - 6 * 86400
        )

    data = asyncio.run(scenario())
    usage = {(r["kind"], r["name"]): r for r in data.usage}
    assert usage[("slash", "poner")]["events"] == 15
    assert usage[("slash", "poner")]["errors"] == 1
    assert usage[("slash", "poner")]["ms_total"] == 600
    assert usage[("slash", "poner")]["lt500"] == 3
    assert usage[("text", "ayuda")]["events"] == 1
    assert sorted(data.daily) == [
        (day_ago(1), "slash", 10, 0),
        (TODAY.isoformat(), "slash", 5, 1),
        (TODAY.isoformat(), "text", 1, 0),
    ]
    # La fila de día entero (hour -1) no entra en el desglose por hora.
    assert sorted(data.hourly) == [(21, "slash", 3), (21, "text", 1), (22, "slash", 2)]
    assert data.users == [(TODAY.isoformat(), "slash", "poner", 5)]
    (system,) = data.system
    assert system["day"] == TODAY.isoformat()
    assert system["memory_max"] == 600.0
    assert system["memory_avg"] == 500.0
    assert system["rss_max"] == 300.0
    assert system["lag_max"] == 9.0
    assert system["gateway_avg"] == 40.0
    assert (system["chromium_max"], system["node_max"]) == (1, 2)


def test_report_data_sin_tablas_ajenas_devuelve_esas_partes_vacias(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"

    async def scenario():
        repository = await seed_report(path)
        return await repository.report_data(
            guild_id=GUILD, since_day=day_ago(6), since_ts=NOW - 6 * 86400
        )

    data = asyncio.run(scenario())
    assert data.usage
    assert data.casino == []
    assert data.balances == []
    assert data.system_accounts == {}
    assert data.ledger == []
    names = {r[0] for r in fetch(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not names & {"casino_plays", "economy_wallets", "economy_ledger"}


def test_report_data_lee_casino_y_economia_sin_escribirlas(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"

    async def create_foreign_tables() -> None:
        await CasinoStatsRepository(path).initialize()
        await EconomyRepository(path, starting_balance=0).initialize()

    asyncio.run(create_foreign_tables())
    with sqlite3.connect(path) as connection:
        plays = [
            # (guild, user, game, stake, payout, created_at)
            (GUILD, 10, "ruleta", 100, 200, NOW - 10),
            (GUILD, 10, "ruleta", 100, 0, NOW - 9),
            (GUILD, 11, "ruleta", 0, 50, NOW - 8),
            (GUILD, 11, "dados", 50, 25, NOW - 7),
            (GUILD, 12, "ruleta", 1000, 1000, NOW - 30 * 86400),
            (OTHER_GUILD, 10, "ruleta", 500, 0, NOW - 5),
        ]
        for guild, user, game, stake, payout, at in plays:
            connection.execute(
                "INSERT INTO casino_plays (guild_id, user_id, game, stake, payout, balance_after,"
                " created_at, day, hour, weekday) VALUES (?, ?, ?, ?, ?, 0, ?, 'd', 0, 0)",
                (guild, user, game, stake, payout, at),
            )
        for guild, user, balance in [
            (GUILD, 10, 500),
            (GUILD, 11, 0),
            (GUILD, 12, 250),
            (GUILD, 0, 9000),
            (GUILD, -100, 40),
            (OTHER_GUILD, 10, 7),
        ]:
            connection.execute(
                "INSERT INTO economy_wallets (guild_id, user_id, balance) VALUES (?, ?, ?)",
                (guild, user, balance),
            )
        for guild, user, delta, reason, at in [
            (GUILD, 10, 100, "trabajo", NOW - 5),
            (GUILD, 10, -30, "tienda:compra", NOW - 4),
            (GUILD, 11, 20, "trabajo", NOW - 3),
            (GUILD, 0, 999, "estado:impuesto", NOW - 3),
            (GUILD, 10, 1, "viejo", NOW - 30 * 86400),
            (OTHER_GUILD, 10, 5, "trabajo", NOW - 3),
        ]:
            connection.execute(
                "INSERT INTO economy_ledger (guild_id, user_id, delta, balance_after, reason,"
                " created_at) VALUES (?, ?, ?, 0, ?, ?)",
                (guild, user, delta, reason, at),
            )
    before = {
        t: fetch(path, f"SELECT COUNT(*) FROM {t}")
        for t in ("casino_plays", "economy_wallets", "economy_ledger")
    }

    async def scenario():
        repository = TelemetryRepository(path)
        return await repository.report_data(
            guild_id=GUILD, since_day=day_ago(6), since_ts=NOW - 6 * 86400
        )

    data = asyncio.run(scenario())
    casino = {row["game"]: row for row in data.casino}
    assert casino["ruleta"]["plays"] == 2
    assert casino["ruleta"]["free"] == 1
    assert casino["ruleta"]["players"] == 2
    assert casino["ruleta"]["wagered"] == 200
    assert casino["ruleta"]["paid"] == 250
    assert (casino["dados"]["plays"], casino["dados"]["wagered"]) == (1, 50)
    assert sorted(data.balances) == [0, 250, 500]
    assert data.system_accounts == {0: 9000, -100: 40}
    ledger = {row["reason"]: row for row in data.ledger}
    assert set(ledger) == {"trabajo", "tienda:compra"}
    assert (ledger["trabajo"]["moves"], ledger["trabajo"]["inflow"]) == (2, 120)
    assert (ledger["tienda:compra"]["outflow"], ledger["tienda:compra"]["inflow"]) == (30, 0)
    after = {
        t: fetch(path, f"SELECT COUNT(*) FROM {t}")
        for t in ("casino_plays", "economy_wallets", "economy_ledger")
    }
    assert after == before


def test_una_base_de_datos_vieja_recibe_las_columnas_nuevas_del_sistema(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE telemetry_system (at REAL NOT NULL, day TEXT NOT NULL, memory_mb REAL, "
            "rss_mb REAL NOT NULL, loop_lag_ms REAL NOT NULL, gateway_ms REAL, "
            "chromium INTEGER NOT NULL, node INTEGER NOT NULL)"
        )
    repository = TelemetryRepository(path)
    today = TODAY.isoformat()

    async def scenario():
        await repository.initialize()
        await repository.write(
            Batch(
                {
                    CounterKey(today, 21, 0, "arranque", "7.11.0"): counter(events=2),
                    CounterKey(today, 21, GUILD, "button", "Mesa.tirar"): counter(
                        events=3, ms_count=3, ms_total=900, ms_ge3000=1
                    ),
                },
                set(),
                [
                    SystemSample(
                        *(NOW, today, 400.0, 200.0, 5.0, 40.0, 1, 2, 30.0, 10.0, 900.0),
                        chromium_mb=170.0,
                        node_mb=80.0,
                        io_read_mb=1.0,
                        io_write_mb=2.0,
                        tasks=30,
                    ),
                    SystemSample(
                        *(NOW + 60, today, 400.0, 200.0, 5.0, 40.0, 1, 2, 50.0, 12.0, 800.0),
                        node_mb=120.0,
                        io_read_mb=0.5,
                        io_write_mb=3.0,
                        tasks=45,
                    ),
                ],
            )
        )
        return await repository.report_data(
            guild_id=GUILD, since_day=day_ago(6), since_ts=NOW - 6 * 86400
        )

    data = asyncio.run(scenario())
    (system,) = data.system
    assert (system["cpu_max"], system["cpu_avg"]) == (50.0, 40.0)
    assert (system["db_max"], system["disk_min"]) == (12.0, 800.0)
    assert (system["chromium_mb_max"], system["node_mb_max"]) == (170.0, 120.0)
    assert (system["io_read"], system["io_write"], system["tasks_max"]) == (1.5, 5.0, 45)
    assert data.starts == [(today, "7.11.0", 2)]
    assert data.daily_ms == [(today, "button", 3, 900, 1)]


def test_report_data_lee_logros_tienda_mascotas_y_economia_por_dia(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    # Domingo 25 de octubre de 2026: en Canarias se atrasa la hora a las 2:00 (UTC+1 a UTC).
    before_change = datetime(2026, 10, 24, 23, 30, tzinfo=TIMEZONE).timestamp()
    after_change = datetime(2026, 10, 25, 23, 30, tzinfo=TIMEZONE).timestamp()
    now = datetime(2026, 10, 26, 12, 0, tzinfo=TIMEZONE).timestamp()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE achievement_unlocks (guild_id INTEGER, user_id INTEGER,
                achievement_id TEXT, unlocked_at REAL);
            CREATE TABLE shop_purchases (guild_id INTEGER, user_id INTEGER, item_name TEXT,
                kind TEXT, base INTEGER, tax INTEGER, discount INTEGER, renewal INTEGER,
                created_at REAL, refunded INTEGER);
            CREATE TABLE pets (id INTEGER, guild_id INTEGER, user_id INTEGER, species TEXT);
            CREATE TABLE pet_owners (guild_id INTEGER, user_id INTEGER, active_id INTEGER);
            CREATE TABLE economy_ledger (guild_id INTEGER, user_id INTEGER, delta INTEGER,
                reason TEXT, created_at REAL);
            """
        )
        connection.executemany(
            "INSERT INTO achievement_unlocks VALUES (?, ?, ?, ?)",
            [
                (GUILD, 1, "a", now),
                (GUILD, 2, "a", now - 90 * 86400),
                (GUILD, 1, "b", now),
                (OTHER_GUILD, 3, "a", now),
            ],
        )
        connection.executemany(
            "INSERT INTO shop_purchases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (GUILD, 1, "Rol VIP", "rol", 90, 6, 10, 0, now, 0),
                (GUILD, 2, "Rol VIP", "rol", 100, 7, 0, 1, now, 0),
                (GUILD, 2, "Rol VIP", "rol", 100, 7, 0, 0, now, 1),
                (GUILD, 3, "Viejo", "objeto", 5, 0, 0, 0, now - 90 * 86400, 0),
            ],
        )
        connection.executemany(
            "INSERT INTO pets VALUES (?, ?, ?, ?)",
            [(1, GUILD, 1, "gato"), (2, GUILD, 1, "perro"), (3, GUILD, 2, "gato")],
        )
        connection.executemany(
            "INSERT INTO pet_owners VALUES (?, ?, ?)", [(GUILD, 1, 1), (GUILD, 2, None)]
        )
        connection.executemany(
            "INSERT INTO economy_ledger VALUES (?, ?, ?, ?, ?)",
            [
                (GUILD, 1, 100, "sueldo", before_change),
                (GUILD, 1, -30, "casino", before_change),
                (GUILD, 1, 50, "sueldo", after_change),
                (GUILD, 0, 999, "estado", after_change),
                (GUILD, 0, 12, "irpf:juego", before_change),
                (GUILD, 0, -5, "devolucion:renta", after_change),
            ],
        )
    repository = TelemetryRepository(path)
    data = asyncio.run(
        repository.report_data(guild_id=GUILD, since_day="2026-10-01", since_ts=now - 30 * 86400)
    )
    assert sorted(data.achievements) == [("a", 2, 1), ("b", 1, 1)]
    assert data.achievement_members == 2
    (vip,) = data.shop
    # Lo pagado es base + IGIC (la rebaja ya va restada en la base); sin las devueltas.
    assert (vip["name"], vip["sales"], vip["renewals"], vip["buyers"], vip["spent"]) == (
        "Rol VIP",
        2,
        1,
        2,
        203,
    )
    assert sorted((p["species"], p["total"], p["active"]) for p in data.pets) == [
        ("gato", 2, 1),
        ("perro", 1, 0),
    ]
    # Los movimientos de las 23:30 van a su día canario aunque cambie la hora.
    assert data.ledger_daily == [("2026-10-24", 100, 30, 2), ("2026-10-25", 50, 0, 1)]
    # El Estado (user_id 0): lo que recauda y gasta, por la primera parte del motivo.
    assert data.state_daily == [
        ("2026-10-24", "irpf", 12, 0),
        ("2026-10-25", "devolucion", 0, 5),
        ("2026-10-25", "estado", 999, 0),
    ]


def test_report_data_cuenta_las_muestras_de_cada_dia(tmp_path: Path) -> None:
    async def scenario():
        repository = await seed_report(tmp_path / "bot.db")
        return await repository.report_data(
            guild_id=GUILD, since_day=day_ago(6), since_ts=NOW - 6 * 86400
        )

    (system,) = asyncio.run(scenario()).system
    assert system["samples"] == 2


async def test_usage_counts_filtra_por_tipo_dias_y_servidor(tmp_path: Path) -> None:
    repository = TelemetryRepository(tmp_path / "t.sqlite3")
    await repository.initialize()
    with repository._connect() as connection:
        connection.executemany(
            "INSERT INTO telemetry_counts (day, hour, guild_id, kind, name, events) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("2026-01-01", 1, 7, "slash", "tragas", 2),
                ("2026-01-01", 2, 7, "slash", "tragas", 3),
                ("2026-01-02", -1, 7, "slash", "ruleta", 1),
                ("2026-01-03", 1, 0, "arranque", "9.0.0", 4),
                ("2026-01-01", 1, 8, "slash", "ajeno", 9),
                ("2026-01-01", 1, 7, "button", "x", 9),
            ],
        )
    everything = await repository.usage_counts(7, ("slash", "arranque"))
    assert everything == {"slash": {"tragas": 5, "ruleta": 1}, "arranque": {"9.0.0": 4}}
    window = await repository.usage_counts(
        7, ("slash", "arranque"), since_day="2026-01-02", until_day="2026-01-02"
    )
    assert window == {"slash": {"ruleta": 1}}
