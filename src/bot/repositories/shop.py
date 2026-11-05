"""Persistencia de la tienda: catálogo, ventas e inventario.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `shop_items`: el catálogo de cada servidor, tal y como lo configuran los
  administradores con `catalogo`.
- `shop_purchases`: una fila por venta, con su número de factura (correlativo
  por servidor), lo cobrado y si se devolvió.
- `shop_inventory`: lo que tiene cada miembro. Guarda una copia del nombre,
  el emoji y la clave de serie para que un objeto siga en la mochila (y se
  pueda usar) aunque el artículo salga del catálogo. Los alquileres y
  potenciadores llevan `expires_at`; los objetos gastados pasan a `expired`.
- `shop_seeded`: qué artículos del surtido de serie
  (`bot.services.shop_catalog`) se han metido ya en cada servidor, para no
  volver a meter lo que un administrador haya retirado.
- `shop_skin_worn`: la skin que lleva cada miembro en cada mesa.
- `shop_skin_images`: la imagen que ha subido cada miembro para sus skins
  propias (`Skin.custom`, una por persona y skin), ya limpia y en JPEG
  (`bot.services.skin_images`), con quién la subió (el dueño o un admin).
- `shop_wishlist`: la lista de deseos de cada miembro, con lo que ya se sabía de
  cada artículo (`seen`) para avisar solo de lo nuevo (`bot.services.shop_wishlist`).
- `shop_wish_muted`: quién no quiere los avisos de su lista por mensaje privado.

El dinero no se toca aquí: la venta se apunta con `reserve` (o `reserve_cart`, un
carrito entero), que corre dentro de la transacción de
`EconomyRepository.purchase_lines`, así que el cobro, el IGIC, las unidades
vendidas y las facturas se guardan juntos o no se guarda nada.

Solo se guardan IDs, nombres de artículos, marcas de tiempo y las imágenes de
las skins propias (que su dueño o un admin pueden borrar cuando quieran). Todo
se borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from bot.repositories import sqlite
from bot.services.shop import (
    BEST_SELLER_MIN,
    Kind,
    Quote,
    Sale,
    ShopError,
    ShopItem,
    boost_window,
    ineligibility,
    quote,
    rental_end,
)
from bot.services.shop_catalog import is_skin, use_of
from bot.services.shop_wishlist import Wish, dump_seen, parse_seen

if TYPE_CHECKING:
    from bot.services.shop_catalog import CatalogEntry

T = TypeVar("T")

#: Columnas que `update_item` deja cambiar; el resto (id, tipo, ventas) es fijo.
EDITABLE_FIELDS = frozenset(
    {
        "name",
        "emoji",
        "description",
        "price",
        "igic",
        "duration",
        "multiplier",
        "stock",
        "per_user",
        "min_level",
        "discount",
        "discount_until",
        "visible",
    }
)


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """Algo que tiene un miembro.

    Attributes:
        item_id: Artículo de origen (puede que ya no esté en el catálogo).
        serial: Número de serie si era de edición limitada.
        edition: Unidades de esa edición.
        starts_at: Desde cuándo vale (potenciadores en cola).
        expires_at: Hasta cuándo; `None` para siempre.
        equipped: Si el rol está puesto (solo roles para siempre).
        catalog_key: Clave del surtido de serie del artículo de origen. Se
            copia al comprar para que un objeto se pueda seguir usando aunque
            el artículo salga del catálogo.
    """

    id: int
    item_id: int
    kind: Kind
    name: str
    emoji: str
    role_id: int | None
    serial: int | None
    edition: int | None
    multiplier: int | None
    starts_at: float
    expires_at: float | None
    equipped: bool
    catalog_key: str | None = None


@dataclass(frozen=True, slots=True)
class ShopStats:
    """Resumen de ventas de un servidor para la trastienda."""

    sales: int
    revenue: int
    tax: int


def _item(row: sqlite3.Row) -> ShopItem:
    return ShopItem(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        kind=Kind(row["kind"]),
        name=row["name"],
        emoji=row["emoji"],
        description=row["description"],
        price=int(row["price"]),
        igic_key=row["igic"],
        role_id=row["role_id"],
        duration=row["duration"],
        multiplier=row["multiplier"],
        stock=row["stock"],
        sold=int(row["sold"]),
        per_user=row["per_user"],
        min_level=int(row["min_level"]),
        discount=int(row["discount"]),
        discount_until=row["discount_until"],
        visible=bool(row["visible"]),
        created_at=float(row["created_at"]),
        catalog_key=row["catalog_key"],
    )


def _holding_values(rows: Sequence[sqlite3.Row], now: float) -> list[tuple[sqlite3.Row, int]]:
    """Lo que vale cada bien: lo pagado sin IGIC, o el precio de hoy si no se compró; lo que
    caduca, la parte de vida que le queda."""
    out = []
    for row in rows:
        value = int(row["paid"] if row["paid"] is not None else row["price"] or 0)
        expires = row["expires_at"]
        if expires is not None:
            life = float(expires) - float(row["starts_at"])
            left = max(0.0, float(expires) - now)
            value = round(value * left / life) if life > 0 else 0
        out.append((row, value))
    return out


_HOLDINGS_SQL = """
    SELECT i.user_id, i.name, i.emoji, i.kind, i.starts_at, i.expires_at,
        (SELECT SUM(p.base) FROM shop_purchases p
         WHERE p.guild_id = i.guild_id AND p.inventory_id = i.id
           AND p.refunded = 0) AS paid,
        (SELECT it.price FROM shop_items it
         WHERE it.guild_id = i.guild_id AND it.id = i.item_id) AS price
    FROM shop_inventory i
    WHERE i.guild_id = ? AND i.status = 'active' AND (i.expires_at IS NULL OR i.expires_at > ?)
"""


def wealth_goods_in(connection: sqlite3.Connection, guild_id: int, now: float) -> dict[int, int]:
    """Valor de los objetos de la tienda de cada miembro para el Impuesto sobre el Patrimonio.

    Solo los objetos (coleccionables y títulos: joyas, coches, cuadros, arts. 18 y 19 de la
    Ley 19/1991), al mismo valor que el patrimonio de `perfil`. Roles, potenciadores y
    mascotas no cuentan (ajuar doméstico y derechos que caducan, art. 4.Cuatro). Lo que está
    en un puesto del Rastro es de una cuenta con id negativo y no cuenta. Corre dentro de la
    transacción de `EconomyRepository.charge_wealth_tax`.
    """
    rows = connection.execute(
        _HOLDINGS_SQL + " AND i.user_id > 0 AND i.kind = ?", (guild_id, now, Kind.TROPHY.value)
    ).fetchall()
    out: dict[int, int] = {}
    for row, value in _holding_values(rows, now):
        member = int(row["user_id"])
        out[member] = out.get(member, 0) + value
    return out


def _collection_count(connection: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    """Coleccionables distintos del miembro: lo que la mochila enseña como tales.

    Los objetos de un solo uso (cajas, bulos, mascarillas) y las skins también son
    `objeto`, pero no son piezas de vitrina y no cuentan para los logros de colección.
    Lo que se usa sin gastarse (la piedra, el dado) sí: se queda para siempre.
    """
    rows = connection.execute(
        """
        SELECT DISTINCT item_id, catalog_key FROM shop_inventory
        WHERE guild_id = ? AND user_id = ? AND kind = 'objeto' AND status = 'active'
        """,
        (guild_id, user_id),
    ).fetchall()
    return sum(
        1
        for _, key in rows
        if not is_skin(key) and not ((use := use_of(key)) is not None and use.consumes)
    )


def _wish(row: sqlite3.Row) -> Wish:
    return Wish(
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        item_id=int(row["item_id"]),
        created_at=float(row["created_at"]),
        seen=parse_seen(row["seen"]),
    )


def _line_name(connection: sqlite3.Connection, item_id: int) -> str:
    """`🍌 Plátano` para poner delante del error de una línea del carrito."""
    row = connection.execute(
        "SELECT emoji, name FROM shop_items WHERE id = ?", (item_id,)
    ).fetchone()
    return f"{row[0]} {row[1]}" if row else "Un artículo"


def _entry(row: sqlite3.Row) -> InventoryEntry:
    return InventoryEntry(
        id=int(row["id"]),
        item_id=int(row["item_id"]),
        kind=Kind(row["kind"]),
        name=row["name"],
        emoji=row["emoji"],
        role_id=row["role_id"],
        serial=row["serial"],
        edition=row["edition"],
        multiplier=row["multiplier"],
        starts_at=float(row["starts_at"]),
        expires_at=row["expires_at"],
        equipped=bool(row["equipped"]),
        catalog_key=row["catalog_key"],
    )


# Orden del escaparate: roles, potenciadores, mascotas y coleccionables; dentro, de
# barato a caro.
_ITEM_ORDER = """
    ORDER BY CASE kind WHEN 'rol' THEN 0 WHEN 'xp' THEN 1 WHEN 'mascota' THEN 2 ELSE 3 END,
        price, id
"""


def _allow_pets(connection: sqlite3.Connection) -> None:
    """Deja entrar el tipo `mascota` en las bases de datos de antes de las mascotas.

    SQLite no deja cambiar un `CHECK`: hay que rehacer la tabla. Se copia
    entera con sus `id` (las ventas y las mochilas apuntan a ellos) y en una
    sola transacción, así que si algo falla queda la tabla de antes.
    """
    (sql,) = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'shop_items'"
    ).fetchone()
    if "'mascota'" in sql:
        return
    # El contador de AUTOINCREMENT se guarda aparte: sin él, un artículo nuevo
    # podría heredar el id de uno retirado que sigue en las mochilas.
    sequence = connection.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'shop_items'"
    ).fetchone()
    with connection:
        connection.execute("ALTER TABLE shop_items RENAME TO shop_items_old")
        connection.execute(
            sql.replace("'objeto')", "'objeto', 'mascota')").replace(
                "CREATE TABLE shop_items_old", "CREATE TABLE shop_items"
            )
        )
        columns = ", ".join(
            row[1] for row in connection.execute("PRAGMA table_info(shop_items_old)")
        )
        connection.execute(
            f"INSERT INTO shop_items ({columns}) SELECT {columns} FROM shop_items_old"
        )
        connection.execute("DROP TABLE shop_items_old")
        connection.execute("CREATE INDEX IF NOT EXISTS shop_items_guild ON shop_items (guild_id)")
        if sequence is not None:
            connection.execute(
                "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'shop_items'",
                (sequence[0],),
            )
            connection.execute(
                "INSERT INTO sqlite_sequence (name, seq) SELECT 'shop_items', ? "
                "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'shop_items')",
                (sequence[0],),
            )


class ShopRepository:
    """Acceso SQLite al catálogo y al inventario de la tienda.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shop_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('rol', 'xp', 'objeto', 'mascota')),
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    price INTEGER NOT NULL CHECK (price > 0),
                    igic TEXT NOT NULL,
                    role_id INTEGER,
                    duration INTEGER,
                    multiplier INTEGER,
                    stock INTEGER,
                    sold INTEGER NOT NULL DEFAULT 0 CHECK (sold >= 0),
                    per_user INTEGER,
                    min_level INTEGER NOT NULL DEFAULT 0,
                    discount INTEGER NOT NULL DEFAULT 0,
                    discount_until REAL,
                    visible INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS shop_items_guild ON shop_items (guild_id);

                CREATE TABLE IF NOT EXISTS shop_purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    invoice INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    base INTEGER NOT NULL,
                    tax INTEGER NOT NULL,
                    discount INTEGER NOT NULL,
                    igic TEXT NOT NULL,
                    inventory_id INTEGER NOT NULL,
                    -- Fin del alquiler antes de esta compra, si la compra lo renovó.
                    previous_expires_at REAL,
                    renewal INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    refunded INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (guild_id, invoice)
                );

                CREATE INDEX IF NOT EXISTS shop_purchases_member
                    ON shop_purchases (guild_id, user_id, item_id);

                CREATE TABLE IF NOT EXISTS shop_inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    role_id INTEGER,
                    serial INTEGER,
                    edition INTEGER,
                    multiplier INTEGER,
                    starts_at REAL NOT NULL,
                    expires_at REAL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'expired', 'refunded')),
                    equipped INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS shop_inventory_member
                    ON shop_inventory (guild_id, user_id, status);

                CREATE TABLE IF NOT EXISTS shop_seeded (
                    guild_id INTEGER NOT NULL,
                    catalog_key TEXT NOT NULL,
                    PRIMARY KEY (guild_id, catalog_key)
                );
                """
            )
            # Bases de datos de antes del surtido de serie: la columna se añade
            # vacía (los artículos de entonces son todos «de la casa»).
            for table in ("shop_items", "shop_inventory"):
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if "catalog_key" not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN catalog_key TEXT")
            _create_skin_worn(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shop_skin_images (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    skin TEXT NOT NULL,
                    image BLOB NOT NULL,
                    set_by INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, skin)
                )
                """
            )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shop_wishlist (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    -- Marcas de lo que ya se sabía del artículo (`shop_wishlist.wish_state`).
                    seen TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (guild_id, user_id, item_id)
                );

                CREATE INDEX IF NOT EXISTS shop_wishlist_item ON shop_wishlist (guild_id, item_id);

                CREATE TABLE IF NOT EXISTS shop_wish_muted (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )
            connection.commit()
            _allow_pets(connection)
        finally:
            connection.close()

    # -- Catálogo --------------------------------------------------------------------

    async def create_item(
        self,
        guild_id: int,
        *,
        kind: Kind,
        name: str,
        emoji: str,
        description: str,
        price: int,
        igic: str,
        now: float,
        role_id: int | None = None,
        duration: int | None = None,
        multiplier: int | None = None,
        stock: int | None = None,
    ) -> ShopItem:
        """Añade un artículo al catálogo y lo devuelve."""
        return await self._run(
            self._create_sync,
            guild_id,
            kind,
            name,
            emoji,
            description,
            price,
            igic,
            now,
            role_id,
            duration,
            multiplier,
            stock,
        )

    def _create_sync(
        self,
        guild_id: int,
        kind: Kind,
        name: str,
        emoji: str,
        description: str,
        price: int,
        igic: str,
        now: float,
        role_id: int | None,
        duration: int | None,
        multiplier: int | None,
        stock: int | None,
    ) -> ShopItem:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO shop_items
                    (guild_id, kind, name, emoji, description, price, igic, role_id,
                     duration, multiplier, stock, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    kind.value,
                    name,
                    emoji,
                    description,
                    price,
                    igic,
                    role_id,
                    duration,
                    multiplier,
                    stock,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM shop_items WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        connection.close()
        return _item(row)

    async def item(self, guild_id: int, item_id: int) -> ShopItem | None:
        """Un artículo del servidor, o `None` si no existe."""
        return await self._run(self._item_sync, guild_id, item_id)

    def _item_sync(self, guild_id: int, item_id: int) -> ShopItem | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
            ).fetchone()
        finally:
            connection.close()
        return _item(row) if row else None

    async def item_by_key(self, guild_id: int, catalog_key: str) -> ShopItem | None:
        """El artículo del surtido de serie con esa clave, aunque esté oculto."""
        return await self._run(self._item_by_key_sync, guild_id, catalog_key)

    def _item_by_key_sync(self, guild_id: int, catalog_key: str) -> ShopItem | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM shop_items WHERE guild_id = ? AND catalog_key = ? "
                "ORDER BY id LIMIT 1",
                (guild_id, catalog_key),
            ).fetchone()
        finally:
            connection.close()
        return _item(row) if row else None

    async def items(self, guild_id: int, *, include_hidden: bool = False) -> list[ShopItem]:
        """Catálogo del servidor en el orden del escaparate."""
        return await self._run(self._items_sync, guild_id, include_hidden)

    def _items_sync(self, guild_id: int, include_hidden: bool) -> list[ShopItem]:
        where = "" if include_hidden else " AND visible = 1"
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM shop_items WHERE guild_id = ?{where} {_ITEM_ORDER}", (guild_id,)
            ).fetchall()
        finally:
            connection.close()
        return [_item(row) for row in rows]

    async def update_item(self, guild_id: int, item_id: int, **fields: object) -> ShopItem:
        """Cambia campos de un artículo y lo devuelve actualizado.

        Raises:
            ShopError: Si el artículo no existe o si se dejan menos
                existencias de las ya vendidas.
            ValueError: Si se pide cambiar un campo que no es editable.
        """
        unknown = set(fields) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Campos no editables: {sorted(unknown)}")
        return await self._run(self._update_sync, guild_id, item_id, fields)

    def _update_sync(self, guild_id: int, item_id: int, fields: dict[str, object]) -> ShopItem:
        connection = self._connect()
        try:
            with connection:
                row = connection.execute(
                    "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
                ).fetchone()
                if row is None:
                    raise ShopError("Ese artículo ya no existe.")
                stock = fields.get("stock", row["stock"])
                if isinstance(stock, int) and stock < int(row["sold"]):
                    raise ShopError(
                        f"Ya se han vendido {row['sold']}: no puedes dejar menos existencias."
                    )
                if fields:
                    # Los nombres de columna salen de EDITABLE_FIELDS, nunca del usuario.
                    assignments = ", ".join(f"{column} = ?" for column in fields)
                    connection.execute(
                        f"UPDATE shop_items SET {assignments} WHERE guild_id = ? AND id = ?",
                        (*fields.values(), guild_id, item_id),
                    )
                row = connection.execute(
                    "SELECT * FROM shop_items WHERE id = ?", (item_id,)
                ).fetchone()
        finally:
            connection.close()
        return _item(row)

    async def delete_item(self, guild_id: int, item_id: int) -> bool:
        """Saca un artículo del catálogo. Lo ya comprado se queda en las mochilas."""
        return await self._run(self._delete_item_sync, guild_id, item_id)

    def _delete_item_sync(self, guild_id: int, item_id: int) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
                )
                connection.execute(
                    "DELETE FROM shop_wishlist WHERE guild_id = ? AND item_id = ?",
                    (guild_id, item_id),
                )
        finally:
            connection.close()
        return cursor.rowcount > 0

    async def best_seller(self, guild_id: int) -> int | None:
        """El artículo visible más vendido, si ha vendido lo bastante para presumir."""
        return await self._run(self._best_seller_sync, guild_id)

    def _best_seller_sync(self, guild_id: int) -> int | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT id FROM shop_items
                WHERE guild_id = ? AND visible = 1 AND sold >= ?
                ORDER BY sold DESC, id ASC LIMIT 1
                """,
                (guild_id, BEST_SELLER_MIN),
            ).fetchone()
        finally:
            connection.close()
        return int(row["id"]) if row else None

    async def stats(self, guild_id: int) -> ShopStats:
        """Ventas, facturación (base) e IGIC de la tienda del servidor."""
        return await self._run(self._stats_sync, guild_id)

    def _stats_sync(self, guild_id: int) -> ShopStats:
        connection = self._connect()
        try:
            sales, revenue, tax = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(base), 0), COALESCE(SUM(tax), 0)
                FROM shop_purchases WHERE guild_id = ? AND refunded = 0
                """,
                (guild_id,),
            ).fetchone()
        finally:
            connection.close()
        return ShopStats(int(sales), int(revenue), int(tax))

    # -- Venta -----------------------------------------------------------------------

    def reserve(
        self,
        guild_id: int,
        user_id: int,
        item_id: int,
        *,
        expected: Quote,
        level: int,
        now: float,
    ) -> Callable[[sqlite3.Connection], tuple[ShopItem, Sale]]:
        """Prepara el apunte de una venta para `EconomyRepository.purchase`.

        Dentro de la transacción del cobro vuelve a leer el artículo y lo
        comprueba todo otra vez: entre que se abre la caja y se paga, un
        administrador puede cambiar el precio o se puede agotar.

        Args:
            expected: Precio que vio el comprador en la caja.
            level: Nivel actual del comprador.

        Returns:
            Una función que recibe la conexión de la transacción y devuelve
            `(artículo, venta)`, o lanza `ShopError` y se deshace todo.
        """

        def run(connection: sqlite3.Connection) -> tuple[ShopItem, Sale]:
            return self._reserve_in(connection, guild_id, user_id, item_id, expected, level, now)

        return run

    def reserve_cart(
        self,
        guild_id: int,
        user_id: int,
        lines: Sequence[tuple[int, int, Quote]],
        *,
        level: int,
        now: float,
    ) -> Callable[[sqlite3.Connection], list[tuple[ShopItem, list[Sale]]]]:
        """Como `reserve`, pero para un carrito: `(artículo, unidades, precio visto)` por línea.

        Cada unidad es una venta con su factura y, si es limitada, su número de serie
        (seguidos). Los límites se miran unidad a unidad dentro de la transacción, así que
        la segunda unidad ya cuenta la primera. Si una línea falla, `ShopError` con el
        nombre del artículo delante y no se apunta ninguna.

        Returns:
            Una función para `EconomyRepository.purchase_lines` que devuelve, por línea,
            `(artículo, ventas)`.
        """

        def run(connection: sqlite3.Connection) -> list[tuple[ShopItem, list[Sale]]]:
            out = []
            for item_id, qty, expected in lines:
                sales: list[Sale] = []
                item: ShopItem | None = None
                for _ in range(qty):
                    try:
                        item, sale = self._reserve_in(
                            connection, guild_id, user_id, item_id, expected, level, now
                        )
                    except ShopError as error:
                        raise ShopError(f"{_line_name(connection, item_id)}: {error}") from error
                    sales.append(sale)
                if item is not None:
                    out.append((item, sales))
            return out

        return run

    def _reserve_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        item_id: int,
        expected: Quote,
        level: int,
        now: float,
    ) -> tuple[ShopItem, Sale]:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM shop_items WHERE guild_id = ? AND id = ?", (guild_id, item_id)
        ).fetchone()
        if row is None:
            raise ShopError("Ese artículo ya no existe.")
        item = _item(row)
        current = quote(item, now)
        if (current.base, current.tax) != (expected.base, expected.tax):
            raise ShopError(
                "El precio ha cambiado mientras mirabas. Vuelve a abrir la caja para ver el nuevo."
            )
        (bought,) = connection.execute(
            """
            SELECT COUNT(*) FROM shop_purchases
            WHERE guild_id = ? AND user_id = ? AND item_id = ? AND refunded = 0
            """,
            (guild_id, user_id, item_id),
        ).fetchone()
        owns_role = False
        if item.kind is Kind.ROLE:
            owns_role = (
                connection.execute(
                    """
                    SELECT 1 FROM shop_inventory
                    WHERE guild_id = ? AND user_id = ? AND role_id = ? AND status = 'active'
                      AND expires_at IS NULL
                    """,
                    (guild_id, user_id, item.role_id),
                ).fetchone()
                is not None
            )
        reason = ineligibility(item, now=now, level=level, bought=int(bought), owns_role=owns_role)
        if reason is not None:
            raise ShopError(reason)

        serial = item.sold + 1 if item.stock is not None else None
        connection.execute("UPDATE shop_items SET sold = sold + 1 WHERE id = ?", (item.id,))
        (invoice,) = connection.execute(
            "SELECT COALESCE(MAX(invoice), 0) + 1 FROM shop_purchases WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()

        starts_at, expires_at = now, None
        inventory_id: int | None = None
        previous_end: float | None = None
        if item.kind is Kind.ROLE and item.duration is not None:
            rental = connection.execute(
                """
                SELECT id, expires_at FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND role_id = ? AND status = 'active'
                  AND expires_at IS NOT NULL
                ORDER BY expires_at DESC LIMIT 1
                """,
                (guild_id, user_id, item.role_id),
            ).fetchone()
            if rental is not None:
                inventory_id = int(rental["id"])
                previous_end = float(rental["expires_at"])
                expires_at = rental_end(now, previous_end, item.duration)
                connection.execute(
                    "UPDATE shop_inventory SET expires_at = ? WHERE id = ?",
                    (expires_at, inventory_id),
                )
            else:
                expires_at = now + item.duration
        elif item.kind is Kind.BOOST:
            (queue_end,) = connection.execute(
                """
                SELECT MAX(expires_at) FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND kind = 'xp' AND status = 'active'
                  AND expires_at > ?
                """,
                (guild_id, user_id, now),
            ).fetchone()
            starts_at, expires_at = boost_window(now, queue_end, item.duration or 3600)

        if inventory_id is None:
            cursor = connection.execute(
                """
                INSERT INTO shop_inventory
                    (guild_id, user_id, item_id, kind, name, emoji, role_id, serial, edition,
                     multiplier, starts_at, expires_at, catalog_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    item.id,
                    item.kind.value,
                    item.name,
                    item.emoji,
                    item.role_id,
                    serial,
                    item.stock if serial is not None else None,
                    item.multiplier,
                    starts_at,
                    expires_at,
                    item.catalog_key,
                ),
            )
            inventory_id = int(cursor.lastrowid or 0)

        cursor = connection.execute(
            """
            INSERT INTO shop_purchases
                (guild_id, invoice, user_id, item_id, item_name, kind, base, tax, discount, igic,
                 inventory_id, previous_expires_at, renewal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                invoice,
                user_id,
                item.id,
                item.name,
                item.kind.value,
                current.base,
                current.tax,
                current.discount,
                current.rate.key,
                inventory_id,
                previous_end,
                int(previous_end is not None),
                now,
            ),
        )
        collection = _collection_count(connection, guild_id, user_id)
        sale = Sale(
            purchase_id=int(cursor.lastrowid or 0),
            invoice=int(invoice),
            serial=serial,
            edition=item.stock if serial is not None else None,
            starts_at=starts_at,
            expires_at=expires_at,
            renewed=previous_end is not None,
            last_unit=item.stock is not None and serial == item.stock,
            collection=int(collection),
        )
        return item, sale

    def release(self, purchase_id: int) -> Callable[[sqlite3.Connection], None]:
        """Prepara la anulación de una venta para `EconomyRepository.refund_purchase`.

        Marca la venta como devuelta, repone la unidad y quita lo entregado
        (o devuelve el alquiler a como estaba si la venta lo renovó).
        """

        def run(connection: sqlite3.Connection) -> None:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM shop_purchases WHERE id = ? AND refunded = 0", (purchase_id,)
            ).fetchone()
            if row is None:
                raise ShopError("Esa compra ya estaba devuelta.")
            connection.execute(
                "UPDATE shop_purchases SET refunded = 1 WHERE id = ?", (purchase_id,)
            )
            connection.execute(
                "UPDATE shop_items SET sold = MAX(sold - 1, 0) WHERE id = ?", (row["item_id"],)
            )
            if row["renewal"]:
                connection.execute(
                    "UPDATE shop_inventory SET expires_at = ? WHERE id = ?",
                    (row["previous_expires_at"], row["inventory_id"]),
                )
            else:
                connection.execute(
                    "UPDATE shop_inventory SET status = 'refunded' WHERE id = ?",
                    (row["inventory_id"],),
                )

        return run

    async def purchase_counts(self, guild_id: int, user_id: int) -> Counter[int]:
        """Compras de cada artículo (`id`) de un miembro, sin las devueltas.

        Es lo que cuenta para el límite por persona (`ineligibility`): lo comprado,
        aunque ya se haya gastado o vendido en el Rastro.
        """
        return await self._run(self._purchase_counts_sync, guild_id, user_id)

    def _purchase_counts_sync(self, guild_id: int, user_id: int) -> Counter[int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT item_id, COUNT(*) FROM shop_purchases
                WHERE guild_id = ? AND user_id = ? AND refunded = 0
                GROUP BY item_id
                """,
                (guild_id, user_id),
            ).fetchall()
        finally:
            connection.close()
        return Counter({int(item_id): int(count) for item_id, count in rows})

    async def owners(self, guild_id: int, item_id: int) -> int:
        """Miembros distintos que tienen ahora un artículo (sin contar los puestos del Rastro)."""
        return await self._run(self._owners_sync, guild_id, item_id)

    def _owners_sync(self, guild_id: int, item_id: int) -> int:
        connection = self._connect()
        try:
            (count,) = connection.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM shop_inventory
                WHERE guild_id = ? AND item_id = ? AND status = 'active' AND user_id > 0
                """,
                (guild_id, item_id),
            ).fetchone()
        finally:
            connection.close()
        return int(count)

    # -- Lista de deseos -------------------------------------------------------------

    async def wishlist(self, guild_id: int, user_id: int) -> list[Wish]:
        """La lista de deseos de un miembro, por orden de llegada."""
        return await self._run(self._wishlist_sync, guild_id, user_id)

    def _wishlist_sync(self, guild_id: int, user_id: int) -> list[Wish]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM shop_wishlist WHERE guild_id = ? AND user_id = ? "
                "ORDER BY created_at, item_id",
                (guild_id, user_id),
            ).fetchall()
        finally:
            connection.close()
        return [_wish(row) for row in rows]

    async def add_wish(
        self, guild_id: int, user_id: int, item_id: int, now: float, seen: Collection[str]
    ) -> bool:
        """Apunta un deseo con lo que ya se sabe de él. `False` si ya estaba."""
        return await self._run(self._add_wish_sync, guild_id, user_id, item_id, now, seen)

    def _add_wish_sync(
        self, guild_id: int, user_id: int, item_id: int, now: float, seen: Collection[str]
    ) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO shop_wishlist
                        (guild_id, user_id, item_id, created_at, seen)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, item_id, now, dump_seen(seen)),
                )
        finally:
            connection.close()
        return cursor.rowcount > 0

    async def remove_wishes(self, guild_id: int, user_id: int, item_ids: Collection[int]) -> int:
        """Quita deseos (al comprarlos o a mano). Devuelve cuántos había."""
        if not item_ids:
            return 0
        return await self._run(self._remove_wishes_sync, guild_id, user_id, tuple(item_ids))

    def _remove_wishes_sync(self, guild_id: int, user_id: int, item_ids: tuple[int, ...]) -> int:
        marks = ", ".join("?" for _ in item_ids)
        connection = self._connect()
        try:
            with connection:
                # `marks` son solo interrogaciones: los ids van como parámetros.
                cursor = connection.execute(
                    f"DELETE FROM shop_wishlist WHERE guild_id = ? AND user_id = ? "
                    f"AND item_id IN ({marks})",
                    (guild_id, user_id, *item_ids),
                )
        finally:
            connection.close()
        return cursor.rowcount

    async def wishers(self, guild_id: int, item_id: int) -> list[int]:
        """Quién tiene un artículo en su lista de deseos."""
        return await self._run(self._wishers_sync, guild_id, item_id)

    def _wishers_sync(self, guild_id: int, item_id: int) -> list[int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT user_id FROM shop_wishlist WHERE guild_id = ? AND item_id = ? "
                "ORDER BY user_id",
                (guild_id, item_id),
            ).fetchall()
        finally:
            connection.close()
        return [int(user_id) for (user_id,) in rows]

    async def all_wishes(self) -> list[Wish]:
        """Todos los deseos de todos los servidores (para mirar si hay que avisar)."""
        return await self._run(self._all_wishes_sync)

    def _all_wishes_sync(self) -> list[Wish]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM shop_wishlist ORDER BY guild_id, user_id, created_at"
            ).fetchall()
        finally:
            connection.close()
        return [_wish(row) for row in rows]

    async def set_wish_seen(self, changes: Sequence[tuple[Wish, Collection[str]]]) -> None:
        """Guarda lo visto de varios deseos de una vez."""
        if changes:
            await self._run(self._set_wish_seen_sync, tuple(changes))

    def _set_wish_seen_sync(self, changes: tuple[tuple[Wish, Collection[str]], ...]) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.executemany(
                    "UPDATE shop_wishlist SET seen = ? "
                    "WHERE guild_id = ? AND user_id = ? AND item_id = ?",
                    [
                        (dump_seen(seen), wish.guild_id, wish.user_id, wish.item_id)
                        for wish, seen in changes
                    ],
                )
        finally:
            connection.close()

    async def muted(self) -> set[tuple[int, int]]:
        """`(servidor, miembro)` que no quieren avisos de su lista."""
        return await self._run(self._muted_sync)

    def _muted_sync(self) -> set[tuple[int, int]]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT guild_id, user_id FROM shop_wish_muted").fetchall()
        finally:
            connection.close()
        return {(int(guild_id), int(user_id)) for guild_id, user_id in rows}

    async def set_muted(self, guild_id: int, user_id: int, muted: bool) -> None:
        """Silencia o vuelve a activar los avisos de la lista de un miembro."""
        await self._run(self._set_muted_sync, guild_id, user_id, muted)

    def _set_muted_sync(self, guild_id: int, user_id: int, muted: bool) -> None:
        connection = self._connect()
        try:
            with connection:
                if muted:
                    connection.execute(
                        "INSERT OR IGNORE INTO shop_wish_muted (guild_id, user_id) VALUES (?, ?)",
                        (guild_id, user_id),
                    )
                else:
                    connection.execute(
                        "DELETE FROM shop_wish_muted WHERE guild_id = ? AND user_id = ?",
                        (guild_id, user_id),
                    )
        finally:
            connection.close()

    # -- Inventario ------------------------------------------------------------------

    async def inventory(self, guild_id: int, user_id: int, now: float) -> list[InventoryEntry]:
        """Lo que tiene un miembro y sigue en vigor, de lo más nuevo a lo más viejo."""
        return await self._run(self._inventory_sync, guild_id, user_id, now)

    def _inventory_sync(self, guild_id: int, user_id: int, now: float) -> list[InventoryEntry]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY id DESC
                """,
                (guild_id, user_id, now),
            ).fetchall()
        finally:
            connection.close()
        return [_entry(row) for row in rows]

    async def holdings(
        self, guild_id: int, user_id: int | None, now: float
    ) -> list[tuple[int, str, str, str, int, float | None]]:
        """Bienes en vigor con su valor, para el patrimonio.

        El valor es lo pagado sin IGIC (sumando las renovaciones y sin las
        compras devueltas). Lo que caduca pierde valor con el tiempo: vale la
        parte que le queda de vida. Lo que se recibió sin comprarlo (caja
        botín, regalos) vale el precio actual del artículo.

        Args:
            user_id: Un miembro, o `None` para todo el servidor.

        Returns:
            `(miembro, nombre, emoji, tipo, valor, caduca)` por cada bien.
        """
        return await self._run(self._holdings_sync, guild_id, user_id, now)

    def _holdings_sync(
        self, guild_id: int, user_id: int | None, now: float
    ) -> list[tuple[int, str, str, str, int, float | None]]:
        where = ""
        args: tuple[object, ...] = (guild_id, now)
        if user_id is not None:
            where = " AND i.user_id = ?"
            args = (*args, user_id)
        else:
            # Lo que está en un puesto del Rastro lo guarda una cuenta con id negativo.
            where = " AND i.user_id > 0"
        connection = self._connect()
        try:
            rows = connection.execute(
                _HOLDINGS_SQL + where + " ORDER BY i.user_id, i.id", args
            ).fetchall()
        finally:
            connection.close()
        out = []
        for row, value in _holding_values(rows, now):
            expires = row["expires_at"]
            out.append(
                (
                    int(row["user_id"]),
                    str(row["name"]),
                    str(row["emoji"]),
                    str(row["kind"]),
                    value,
                    float(expires) if expires is not None else None,
                )
            )
        return out

    async def set_equipped(
        self, guild_id: int, user_id: int, entry_id: int, equipped: bool
    ) -> InventoryEntry | None:
        """Marca un rol para siempre como puesto o quitado. `None` si no es suyo."""
        return await self._run(self._set_equipped_sync, guild_id, user_id, entry_id, equipped)

    def _set_equipped_sync(
        self, guild_id: int, user_id: int, entry_id: int, equipped: bool
    ) -> InventoryEntry | None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE shop_inventory SET equipped = ?
                    WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
                    """,
                    (int(equipped), entry_id, guild_id, user_id),
                )
                row = connection.execute(
                    """
                    SELECT * FROM shop_inventory
                    WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
                    """,
                    (entry_id, guild_id, user_id),
                ).fetchone()
        finally:
            connection.close()
        return _entry(row) if row else None

    async def wear_one(
        self, guild_id: int, user_id: int, catalog_key: str | None, keys: Collection[str]
    ) -> None:
        """De los artículos `keys` del miembro, deja puesto solo `catalog_key`.

        Para lo que se lleva de uno en uno: el título, o la skin de cada juego
        (`keys` son entonces las skins de ese juego). Con `None`, ninguno. Usa la
        columna `equipped`, como los roles.

        Args:
            keys: Claves de serie del grupo (`TITLE_KEYS`, las skins de un juego…).
        """
        await self._run(self._wear_one_sync, guild_id, user_id, catalog_key, tuple(keys))

    def _wear_one_sync(
        self, guild_id: int, user_id: int, catalog_key: str | None, keys: tuple[str, ...]
    ) -> None:
        if not keys:
            return
        marks = ", ".join("?" for _ in keys)
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    f"""
                    UPDATE shop_inventory SET equipped = (catalog_key IS ?)
                    WHERE guild_id = ? AND user_id = ? AND status = 'active'
                        AND catalog_key IN ({marks})
                    """,
                    (catalog_key, guild_id, user_id, *keys),
                )
        finally:
            connection.close()

    async def wear_skin(
        self, guild_id: int, user_id: int, game: str, catalog_key: str | None
    ) -> None:
        """Pone la skin `catalog_key` en la mesa `game` (con `None`, la de serie).

        Va por mesa y no en `equipped`: un tapete se lleva en varias mesas a la vez.
        """
        await self._run(self._wear_skin_sync, guild_id, user_id, game, catalog_key)

    def _wear_skin_sync(
        self, guild_id: int, user_id: int, game: str, catalog_key: str | None
    ) -> None:
        connection = self._connect()
        try:
            with connection:
                if catalog_key is None:
                    connection.execute(
                        "DELETE FROM shop_skin_worn WHERE guild_id = ? AND user_id = ? "
                        "AND game = ?",
                        (guild_id, user_id, game),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO shop_skin_worn (guild_id, user_id, game, catalog_key)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT (guild_id, user_id, game)
                        DO UPDATE SET catalog_key = excluded.catalog_key
                        """,
                        (guild_id, user_id, game, catalog_key),
                    )
        finally:
            connection.close()

    async def worn_skins(self) -> list[tuple[int, int, str, str]]:
        """La skin que lleva cada miembro en cada mesa: `(servidor, miembro, juego, clave)`."""
        return await self._run(self._worn_skins_sync)

    def _worn_skins_sync(self) -> list[tuple[int, int, str, str]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT guild_id, user_id, game, catalog_key FROM shop_skin_worn"
            ).fetchall()
        finally:
            connection.close()
        return [(int(r[0]), int(r[1]), str(r[2]), str(r[3])) for r in rows]

    async def set_skin_image(
        self, guild_id: int, user_id: int, skin: str, image: bytes, *, set_by: int, now: float
    ) -> None:
        """Guarda la imagen de la skin propia `skin` de un miembro (sustituye la que hubiera).

        Args:
            skin: Clave de la skin (`tapete_propio`), no la del artículo.
            set_by: Quién la sube: el dueño o un administrador.
        """
        await self._run(self._set_skin_image_sync, guild_id, user_id, skin, image, set_by, now)

    def _set_skin_image_sync(
        self, guild_id: int, user_id: int, skin: str, image: bytes, set_by: int, now: float
    ) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO shop_skin_images
                        (guild_id, user_id, skin, image, set_by, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id, skin) DO UPDATE SET
                        image = excluded.image, set_by = excluded.set_by,
                        updated_at = excluded.updated_at
                    """,
                    (guild_id, user_id, skin, image, set_by, now),
                )
        finally:
            connection.close()

    async def delete_skin_image(self, guild_id: int, user_id: int, skin: str) -> bool:
        """Borra la imagen de la skin propia `skin` de un miembro; `False` si no tenía."""
        return await self._run(self._delete_skin_image_sync, guild_id, user_id, skin)

    def _delete_skin_image_sync(self, guild_id: int, user_id: int, skin: str) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM shop_skin_images WHERE guild_id = ? AND user_id = ? AND skin = ?",
                    (guild_id, user_id, skin),
                )
            return cursor.rowcount > 0
        finally:
            connection.close()

    async def skin_images(self) -> list[tuple[int, int, str, bytes]]:
        """Todas las imágenes de skins propias: `(servidor, miembro, skin, JPEG)`."""
        return await self._run(self._skin_images_sync)

    def _skin_images_sync(self) -> list[tuple[int, int, str, bytes]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT guild_id, user_id, skin, image FROM shop_skin_images"
            ).fetchall()
        finally:
            connection.close()
        return [(int(r[0]), int(r[1]), str(r[2]), bytes(r[3])) for r in rows]

    async def worn_among(self, keys: Collection[str]) -> list[tuple[int, int, str, str, str]]:
        """Lo que lleva puesto cada miembro de entre `keys`.

        Returns:
            `(servidor, miembro, clave de serie, emoji, nombre)` de cada artículo
            puesto. Quien lleve dos del mismo grupo (no debería) sale dos veces:
            el que llama se queda con el más reciente, que va detrás.
        """
        return await self._run(self._worn_among_sync, tuple(keys))

    def _worn_among_sync(self, keys: tuple[str, ...]) -> list[tuple[int, int, str, str, str]]:
        if not keys:
            return []
        marks = ", ".join("?" for _ in keys)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT guild_id, user_id, catalog_key, emoji, name FROM shop_inventory
                WHERE status = 'active' AND equipped = 1 AND catalog_key IN ({marks})
                ORDER BY id
                """,
                keys,
            ).fetchall()
        finally:
            connection.close()
        return [
            (
                int(row["guild_id"]),
                int(row["user_id"]),
                str(row["catalog_key"]),
                str(row["emoji"]),
                str(row["name"]),
            )
            for row in rows
        ]

    async def active_boosts(self, now: float) -> list[tuple[int, int, float, float, int]]:
        """Potenciadores que aún no han acabado: `(servidor, miembro, inicio, fin, %)`."""
        return await self._run(self._active_boosts_sync, now)

    def _active_boosts_sync(self, now: float) -> list[tuple[int, int, float, float, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT guild_id, user_id, starts_at, expires_at, multiplier FROM shop_inventory
                WHERE kind = 'xp' AND status = 'active' AND expires_at > ?
                """,
                (now,),
            ).fetchall()
        finally:
            connection.close()
        return [
            (
                int(r["guild_id"]),
                int(r["user_id"]),
                float(r["starts_at"]),
                float(r["expires_at"]),
                int(r["multiplier"] or 100),
            )
            for r in rows
        ]

    async def due_rentals(self, now: float) -> list[tuple[int, int, int, int | None]]:
        """Roles alquilados que ya han vencido: `(fila, servidor, miembro, rol)`."""
        return await self._run(self._due_rentals_sync, now)

    def _due_rentals_sync(self, now: float) -> list[tuple[int, int, int, int | None]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, guild_id, user_id, role_id FROM shop_inventory
                WHERE kind = 'rol' AND status = 'active' AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (now,),
            ).fetchall()
        finally:
            connection.close()
        return [(int(r["id"]), int(r["guild_id"]), int(r["user_id"]), r["role_id"]) for r in rows]

    async def mark_expired(self, entry_ids: list[int]) -> None:
        """Da por vencidas filas del inventario (alquileres que ya se han quitado)."""
        if entry_ids:
            await self._run(self._mark_expired_sync, entry_ids)

    def _mark_expired_sync(self, entry_ids: list[int]) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.executemany(
                    "UPDATE shop_inventory SET status = 'expired' WHERE id = ?",
                    [(entry_id,) for entry_id in entry_ids],
                )
        finally:
            connection.close()

    # -- Surtido de serie ------------------------------------------------------------

    async def stock_catalog(
        self,
        guild_id: int,
        entries: Sequence[CatalogEntry],
        now: float,
        *,
        restock: bool = False,
        revised: Mapping[str, int] | None = None,
        retired: Collection[str] = (),
        retaxed: Mapping[str, str] | None = None,
    ) -> int:
        """Mete en el catálogo del servidor los artículos de serie que falten.

        Sin `restock`, solo mete los que nunca se han metido en ese servidor:
        lo que un administrador haya retirado no vuelve. Con `restock` (botón
        «Reponer surtido» de la trastienda) vuelve a meter también los
        retirados, pero nunca duplica uno que siga en el catálogo, aunque esté
        oculto.

        Args:
            revised: Precio de antes de los artículos de serie que lo han cambiado
                (`shop_catalog.REVISED_PRICES`). Los que aún lo tienen pasan al
                precio, nivel mínimo, existencias y descripción de `entries`; las
                existencias nunca bajan de lo ya vendido.
            retired: Claves de los artículos de serie que ya no se venden
                (`shop_catalog.RETIRED_ITEMS`). Se ocultan una sola vez por servidor
                (queda apuntado en `shop_seeded` como `retirado:<clave>`), así que un
                administrador que los vuelva a enseñar no los ve ocultarse otra vez.
            retaxed: Tipo de impuesto de antes de los artículos de serie que lo han
                cambiado (`shop_catalog.REVISED_TAXES`). Los que aún lo tienen pasan al
                tipo y la descripción de `entries`.

        Returns:
            Cuántos artículos ha añadido.
        """
        return await self._run(
            self._stock_sync,
            guild_id,
            list(entries),
            now,
            restock,
            dict(revised or {}),
            tuple(retired),
            dict(retaxed or {}),
        )

    def _stock_sync(
        self,
        guild_id: int,
        entries: list[CatalogEntry],
        now: float,
        restock: bool,
        revised: dict[str, int],
        retired: tuple[str, ...],
        retaxed: dict[str, str],
    ) -> int:
        connection = self._connect()
        try:
            with connection:
                seeded = {
                    row[0]
                    for row in connection.execute(
                        "SELECT catalog_key FROM shop_seeded WHERE guild_id = ?", (guild_id,)
                    )
                }
                present = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT catalog_key FROM shop_items
                        WHERE guild_id = ? AND catalog_key IS NOT NULL
                        """,
                        (guild_id,),
                    )
                }
                skip = present if restock else seeded | present
                added = 0
                for entry in entries:
                    if entry.key in skip:
                        continue
                    connection.execute(
                        """
                        INSERT INTO shop_items
                            (guild_id, kind, name, emoji, description, price, igic, duration,
                             multiplier, stock, per_user, min_level, created_at, catalog_key,
                             visible)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            guild_id,
                            entry.kind.value,
                            entry.name,
                            entry.emoji,
                            entry.description,
                            entry.price,
                            entry.igic,
                            entry.duration,
                            entry.multiplier,
                            entry.stock,
                            entry.per_user,
                            entry.min_level,
                            now,
                            entry.key,
                            int(entry.visible),
                        ),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO shop_seeded (guild_id, catalog_key) VALUES (?, ?)",
                        (guild_id, entry.key),
                    )
                    added += 1
                for key in retired:
                    marker = f"retirado:{key}"
                    if marker in seeded:
                        continue
                    connection.execute(
                        "UPDATE shop_items SET visible = 0 WHERE guild_id = ? AND catalog_key = ?",
                        (guild_id, key),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO shop_seeded (guild_id, catalog_key) VALUES (?, ?)",
                        (guild_id, marker),
                    )
                for entry in entries:
                    old_price = revised.get(entry.key)
                    if old_price is None or old_price == entry.price:
                        continue
                    connection.execute(
                        """
                        UPDATE shop_items
                        SET price = ?, min_level = ?, description = ?,
                            stock = CASE WHEN ? IS NULL THEN NULL ELSE MAX(?, sold) END
                        WHERE guild_id = ? AND catalog_key = ? AND price = ?
                        """,
                        (
                            entry.price,
                            entry.min_level,
                            entry.description,
                            entry.stock,
                            entry.stock,
                            guild_id,
                            entry.key,
                            old_price,
                        ),
                    )
                for entry in entries:
                    old_tax = retaxed.get(entry.key)
                    if old_tax is None or old_tax == entry.igic:
                        continue
                    connection.execute(
                        "UPDATE shop_items SET igic = ?, description = ? "
                        "WHERE guild_id = ? AND catalog_key = ? AND igic = ?",
                        (entry.igic, entry.description, guild_id, entry.key, old_tax),
                    )
        finally:
            connection.close()
        return added

    # -- Usar objetos ----------------------------------------------------------------

    async def consume(self, guild_id: int, user_id: int, entry_id: int) -> bool:
        """Gasta un objeto de la mochila. `False` si ya no estaba (doble clic, otra pestaña)."""
        return await self._run(self._consume_sync, guild_id, user_id, entry_id)

    def _consume_sync(self, guild_id: int, user_id: int, entry_id: int) -> bool:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE shop_inventory SET status = 'expired'
                    WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
                      AND kind = 'objeto'
                    """,
                    (entry_id, guild_id, user_id),
                )
        finally:
            connection.close()
        return cursor.rowcount > 0

    async def restore(self, entry_id: int) -> None:
        """Devuelve a la mochila un objeto gastado (si el uso no se pudo completar)."""
        await self._run(self._restore_sync, entry_id)

    def _restore_sync(self, entry_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE shop_inventory SET status = 'active'
                    WHERE id = ? AND status = 'expired'
                    """,
                    (entry_id,),
                )
        finally:
            connection.close()

    async def grant(
        self, guild_id: int, user_id: int, item: ShopItem, now: float
    ) -> tuple[InventoryEntry, bool]:
        """Mete un objeto en la mochila sin venta (premio de la caja botín).

        No cuenta como unidad vendida ni lleva número de serie: la caja solo
        da artículos de existencias ilimitadas.

        Returns:
            `(entrada, repe)`: `repe` si ya tenía uno igual.
        """
        return await self._run(self._grant_sync, guild_id, user_id, item, now)

    def _grant_sync(
        self, guild_id: int, user_id: int, item: ShopItem, now: float
    ) -> tuple[InventoryEntry, bool]:
        connection = self._connect()
        try:
            with connection:
                dupe = (
                    connection.execute(
                        """
                        SELECT 1 FROM shop_inventory
                        WHERE guild_id = ? AND user_id = ? AND item_id = ? AND status = 'active'
                        """,
                        (guild_id, user_id, item.id),
                    ).fetchone()
                    is not None
                )
                cursor = connection.execute(
                    """
                    INSERT INTO shop_inventory
                        (guild_id, user_id, item_id, kind, name, emoji, multiplier, starts_at,
                         catalog_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        item.id,
                        item.kind.value,
                        item.name,
                        item.emoji,
                        item.multiplier,
                        now,
                        item.catalog_key,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM shop_inventory WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
        finally:
            connection.close()
        return _entry(row), dupe

    async def grant_numbered(
        self, guild_id: int, user_id: int, catalog_key: str, now: float
    ) -> tuple[InventoryEntry, int, int] | None:
        """Mete en la mochila la siguiente unidad numerada de un artículo, sin venta.

        Es el premio del expositor de la Rueda: cuenta como unidad dada (`sold`) para que
        el número de serie avance y se agote, pero no hay factura ni IGIC. Todo en una
        transacción, así que dos premios a la vez no se llevan el mismo número.

        Returns:
            `(entrada, número, edición)`, o `None` si ya no quedan, si el artículo no
            existe en el servidor o si el miembro ya tiene una (son de uno por persona).
        """
        return await self._run(self._grant_numbered_sync, guild_id, user_id, catalog_key, now)

    def _grant_numbered_sync(
        self, guild_id: int, user_id: int, catalog_key: str, now: float
    ) -> tuple[InventoryEntry, int, int] | None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM shop_items WHERE guild_id = ? AND catalog_key = ?",
                    (guild_id, catalog_key),
                ).fetchone()
                if row is None:
                    return None
                item = _item(row)
                if item.stock is None or item.sold >= item.stock:
                    return None
                owned = connection.execute(
                    """
                    SELECT 1 FROM shop_inventory
                    WHERE guild_id = ? AND user_id = ? AND item_id = ? AND status = 'active'
                    """,
                    (guild_id, user_id, item.id),
                ).fetchone()
                if owned is not None:
                    return None
                serial = item.sold + 1
                connection.execute("UPDATE shop_items SET sold = sold + 1 WHERE id = ?", (item.id,))
                cursor = connection.execute(
                    """
                    INSERT INTO shop_inventory
                        (guild_id, user_id, item_id, kind, name, emoji, serial, edition,
                         starts_at, catalog_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id, user_id, item.id, item.kind.value, item.name, item.emoji,
                        serial, item.stock, now, item.catalog_key,
                    ),
                )  # fmt: skip
                entry = connection.execute(
                    "SELECT * FROM shop_inventory WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
        finally:
            connection.close()
        return _entry(entry), serial, int(item.stock)

    async def collection_size(self, guild_id: int, user_id: int) -> int:
        """Objetos distintos que tiene un miembro (para los logros de colección)."""
        return await self._run(self._collection_sync, guild_id, user_id)

    def _collection_sync(self, guild_id: int, user_id: int) -> int:
        connection = self._connect()
        try:
            return _collection_count(connection, guild_id, user_id)
        finally:
            connection.close()

    # -- Limpieza --------------------------------------------------------------------

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra el catálogo, las ventas, los inventarios y el surtido de un servidor."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                for table in (
                    "shop_items",
                    "shop_purchases",
                    "shop_inventory",
                    "shop_seeded",
                    "shop_skin_worn",
                    "shop_skin_images",
                    "shop_wishlist",
                    "shop_wish_muted",
                ):
                    # `table` sale de una tupla fija, nunca de entrada del usuario.
                    connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()


def move_entry_in(
    connection: sqlite3.Connection, guild_id: int, entry_id: int, from_user: int, to_user: int
) -> InventoryEntry | None:
    """Pasa un objeto de la mochila de `from_user` a la de `to_user` (el Rastro).

    Corre dentro de una transacción ya abierta (la de `EconomyRepository.market`).
    El objeto llega sin poner (`equipped = 0`) y, si quien lo suelta lo llevaba
    como skin en alguna mesa, se le quita. Las ventas de la tienda
    (`shop_purchases`) se quedan como están: son de quien lo compró.

    Returns:
        El objeto ya movido, o `None` si `from_user` no lo tenía en vigor.
    """
    cursor = connection.execute(
        """
        UPDATE shop_inventory SET user_id = ?, equipped = 0
        WHERE id = ? AND guild_id = ? AND user_id = ? AND status = 'active'
        """,
        (to_user, entry_id, guild_id, from_user),
    )
    if cursor.rowcount != 1:
        return None
    row = connection.execute("SELECT * FROM shop_inventory WHERE id = ?", (entry_id,)).fetchone()
    if row["catalog_key"]:
        connection.execute(
            "DELETE FROM shop_skin_worn WHERE guild_id = ? AND user_id = ? AND catalog_key = ?",
            (guild_id, from_user, row["catalog_key"]),
        )
    return _entry(row)


def _create_skin_worn(connection: sqlite3.Connection) -> None:
    """Crea la tabla de skins puestas por mesa y, la primera vez, trae las de `equipped`.

    Antes cada skin se llevaba en un solo juego y se marcaba con `equipped`. Al
    pasar a una por mesa, la skin puesta de entonces sigue puesta en su juego: los
    gallos en `gallos` y el tapete granate, que era de la ruleta, en `ruleta`.
    """
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shop_skin_worn'"
    ).fetchone()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_skin_worn (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            game TEXT NOT NULL,
            catalog_key TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id, game)
        )
        """
    )
    if exists:
        return
    connection.execute(
        """
        INSERT OR REPLACE INTO shop_skin_worn (guild_id, user_id, game, catalog_key)
        SELECT guild_id, user_id,
            CASE WHEN substr(catalog_key, 1, 11) = 'skin_gallo_' THEN 'gallos' ELSE 'ruleta' END,
            catalog_key
        FROM shop_inventory
        WHERE status = 'active' AND equipped = 1
            AND (substr(catalog_key, 1, 11) = 'skin_gallo_' OR catalog_key = 'skin_ruleta_granate')
        ORDER BY id
        """
    )
