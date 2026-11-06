"""La factura fiscal de cada miembro: todo lo que ha pagado, directo o indirecto.

`EconomyRepository.tax_breakdown` devuelve, por miembro, lo pagado en cada tipo
de impuesto. Este módulo dice qué es cada tipo, cómo se agrupa y cómo se
escribe cada pestaña del panel de `hacienda` (`bot.cogs.hacienda_panel`). No toca
Discord ni la base de datos.

Grupos:

- **Directos:** los que paga quien gana o tiene el dinero (IRPF de cada renta,
  cotización del trabajador y cuota de autónomos, Patrimonio y gravamen de
  loterías).
- **Indirectos:** los que paga sin verlos (IGIC dentro del precio de la
  `tienda`, la Seguridad Social que la empresa ingresa por su nómina, que es coste
  de su puesto aunque no salga de su bolsillo, igual que en la vida real, y el
  IAJ que sale del bote de cada `porra`).
- **Otros pagos al Estado:** multas de la Inspección. No son impuestos, pero
  salen del bolsillo y van al Estado.
- **Devuelto:** lo que la renta semanal le devolvió (resta).
- **Fuera de España:** lo pagado en Hong Kong. No va a Perro Sanxe y no suma
  al total, pero se enseña aparte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import discord

from bot.repositories.economy import Treasury
from bot.services.economy import format_amount, treasury_rules
from bot.services.taxes import SOLIDARITY_WEALTH_THRESHOLD, TAX_COLLECTOR


class Group(Enum):
    """Cómo se presenta cada tipo de pago."""

    DIRECT = "directos"
    INDIRECT = "indirectos"
    OTHER = "otros"
    REFUND = "devuelto"
    FOREIGN = "extranjero"


@dataclass(frozen=True, slots=True)
class TaxKind:
    """Un tipo de pago de la factura."""

    key: str
    emoji: str
    label: str
    short: str
    group: Group
    law: str


#: Tipos de pago, en el orden en que salen. Las claves son las de la consulta.
TAX_KINDS: tuple[TaxKind, ...] = (
    TaxKind("irpf_trabajo", "🪏", "IRPF de las nóminas", "IRPF nóm.", Group.DIRECT,
            "art. 17 LIRPF"),
    TaxKind("irpf_otros", "🎁", "IRPF de premios y otros ingresos", "IRPF otros", Group.DIRECT,
            "art. 99 LIRPF"),
    TaxKind("irpf_renta", "📑", "IRPF de la declaración del lunes", "IRPF renta",
            Group.DIRECT, "arts. 96 y 97 LIRPF"),
    TaxKind("irpf_casino", "🎰", "IRPF del casino", "IRPF casino", Group.DIRECT,
            "art. 33.5.d LIRPF"),
    TaxKind("irpf_ahorro", "🏦", "IRPF del ahorro (intereses y plusvalías)", "IRPF ahorro",
            Group.DIRECT, "arts. 25.2, 33 y 66 LIRPF"),
    TaxKind("irpf_alquiler", "🔑", "IRPF de los alquileres", "IRPF alq.", Group.DIRECT,
            "arts. 22 y 23 LIRPF"),
    TaxKind("sociedades", "🏭", "Impuesto sobre Sociedades (de tu sociedad)", "Sociedades",
            Group.DIRECT, "Ley 27/2014"),
    TaxKind("sociedades_banco", "🏦", "Impuesto sobre Sociedades del banco", "IS banco",
            Group.DIRECT, "art. 29.6 LIS"),
    TaxKind("tasa_juego", "🎰", "Tasa fiscal sobre el juego del casino", "Tasa juego",
            Group.DIRECT, "art. 3.4 RDL 16/1977"),
    TaxKind("ibi", "🏘️", "IBI de los pisos", "IBI", Group.DIRECT, "art. 60 RDL 2/2004"),
    TaxKind("ss_trabajador", "🧑‍🏭", "Seguridad Social del trabajador y autónomos", "SS",
            Group.DIRECT, "LGSS"),
    TaxKind("patrimonio", "🏰", "Impuesto sobre el Patrimonio", "Patrim.", Group.DIRECT,
            "Ley 19/1991"),
    TaxKind("deudas", "⚖️", "Deudas con Hacienda cobradas después", "Deudas", Group.DIRECT,
            "art. 169 LGT"),
    TaxKind("grandes_fortunas", "💎", "Impuesto de Solidaridad de las Grandes Fortunas",
            "Grandes F.", Group.DIRECT, "art. 3 Ley 38/2022"),
    TaxKind("loteria", "🎟️", "Gravamen especial de loterías", "Lotería", Group.DIRECT,
            "DA 33ª LIRPF"),
    TaxKind("igic", "🛒", "IGIC de las compras (y primas de seguros)", "IGIC", Group.INDIRECT,
            "Ley 4/2012 y art. 12 Ley 13/1996"),
    TaxKind("aiem", "🚢", "AIEM de lo que importa tu negocio", "AIEM", Group.INDIRECT,
            "art. 92 DLeg 1/2025 de Canarias"),
    TaxKind("ss_empresa", "🏢", "Seguridad Social que paga la empresa", "SS empresa",
            Group.INDIRECT, "LGSS"),
    TaxKind("itp", "🧺", "Transmisiones Patrimoniales (Rastro)", "ITP", Group.INDIRECT,
            "art. 31.1.d DLeg 1/2009 de Canarias"),
    TaxKind("itp_pisos", "🏠", "Transmisiones Patrimoniales (pisos)", "ITP pisos",
            Group.INDIRECT, "art. 31 DLeg 1/2009 de Canarias"),
    TaxKind("iaj", "🎫", "Impuesto sobre Actividades de Juego (porras)", "IAJ",
            Group.INDIRECT, "art. 48 Ley 13/2011"),
    TaxKind("icio", "🏗️", "ICIO de las obras de los pisos", "ICIO", Group.INDIRECT,
            "art. 102 RDL 2/2004"),
    TaxKind("tasas", "🏖️", "Tasas de licencias (vacacionales, de obra y de apertura)", "Tasas",
            Group.OTHER, "Ley 6/2025 de Canarias y ordenanzas municipales"),
    TaxKind("multas", "🚨", "Multas de la Inspección", "Multas", Group.OTHER, "LGT"),
    TaxKind("devuelto", "📬", "Devuelto en la renta", "Devuelto", Group.REFUND,
            "art. 103 LIRPF"),
    TaxKind("extranjero", "🇭🇰", "Impuestos y MPF en Hong Kong", "Hong Kong", Group.FOREIGN,
            "IRD de Hong Kong"),
)  # fmt: skip
KIND_BY_KEY: dict[str, TaxKind] = {kind.key: kind for kind in TAX_KINDS}


@dataclass(frozen=True, slots=True)
class TaxBill:
    """Lo pagado por un miembro (o por todo el servidor), por tipo.

    Attributes:
        user_id: Miembro, o 0 para el total del servidor.
        amounts: Importe por clave de `TAX_KINDS`. `devuelto` va en positivo.
    """

    user_id: int
    amounts: dict[str, int] = field(default_factory=dict)

    def group_total(self, group: Group) -> int:
        """Suma de los tipos de un grupo."""
        return sum(
            amount
            for key, amount in self.amounts.items()
            if key in KIND_BY_KEY and KIND_BY_KEY[key].group is group
        )

    @property
    def direct(self) -> int:
        """Impuestos directos."""
        return self.group_total(Group.DIRECT)

    @property
    def indirect(self) -> int:
        """Impuestos indirectos."""
        return self.group_total(Group.INDIRECT)

    @property
    def refunded(self) -> int:
        """Lo que le devolvieron (positivo)."""
        return self.group_total(Group.REFUND)

    @property
    def total(self) -> int:
        """Lo que se ha quedado el Estado: directos + indirectos + multas − devuelto."""
        return self.direct + self.indirect + self.group_total(Group.OTHER) - self.refunded

    @property
    def foreign(self) -> int:
        """Lo pagado fuera (no cuenta en `total`)."""
        return self.group_total(Group.FOREIGN)


def bills(breakdown: dict[int, dict[str, int]]) -> list[TaxBill]:
    """Facturas de cada miembro, de la que más paga a la que menos."""
    out = [TaxBill(user_id, dict(amounts)) for user_id, amounts in breakdown.items()]
    return sorted(out, key=lambda bill: (-bill.total, bill.user_id))


def server_bill(all_bills: list[TaxBill]) -> TaxBill:
    """Todas las facturas sumadas en una."""
    amounts: dict[str, int] = {}
    for bill in all_bills:
        for key, amount in bill.amounts.items():
            amounts[key] = amounts.get(key, 0) + amount
    return TaxBill(0, amounts)


def _sign(kind: TaxKind) -> str:
    """Las devoluciones salen con signo menos: restan de lo pagado."""
    return "−" if kind.group is Group.REFUND else ""


#: Impuestos que salen en la línea corta de cada miembro (los que más ha pagado).
COMPACT_KINDS = 5


def compact_line(bill: TaxBill) -> str:
    """`IRPF nóm. 1.200 · SS empresa 3.000 · IGIC 70`: los que más ha pagado.

    Solo lo que no es cero y como mucho `COMPACT_KINDS`, de más a menos, para que
    la lista de `hacienda` quepa en un embed aunque todos paguen de todo.
    """
    kinds = sorted(
        (k for k in TAX_KINDS if bill.amounts.get(k.key) and k.group is not Group.FOREIGN),
        key=lambda k: -abs(bill.amounts[k.key]),
    )
    parts = [
        f"{kind.short} {_sign(kind)}{format_amount(bill.amounts[kind.key]).removesuffix(' Y$')}"
        for kind in kinds[:COMPACT_KINDS]
    ]
    if len(kinds) > COMPACT_KINDS:
        parts.append(f"y {len(kinds) - COMPACT_KINDS} más")
    return " · ".join(parts) if parts else "Nada todavía"


def detail_lines(bill: TaxBill, group: Group, *, law: bool = False) -> list[str]:
    """Una línea por tipo del grupo con algo pagado (con su norma, si `law`)."""
    return [
        f"{kind.emoji} {kind.label}: **{_sign(kind)}{format_amount(bill.amounts[kind.key])}**"
        + (f" · {kind.law}" if law else "")
        for kind in TAX_KINDS
        if kind.group is group and bill.amounts.get(kind.key)
    ]


def verdict(bill: TaxBill, share: float) -> str:
    """Una frase con guasa según lo que paga alguien.

    Args:
        share: Parte de lo recaudado en el servidor que ha pagado (0–1).
    """
    if bill.total <= 0:
        return "Ni un Y$ para Sanxe. O eres muy listo o no haces nada."
    if share >= 0.5:
        return "Tú solo sostienes el Estado. El Falcon va con tu gasolina."
    if bill.indirect > bill.direct:
        return "Pagas más sin verlo que viéndolo. Así funciona el truco."
    if share >= 0.2:
        return "Contribuyente de los que Hacienda llama por su nombre."
    return "Pagas lo tuyo, como todo el mundo. Bueno, como casi todo el mundo."


# -- Pestañas de `hacienda` ----------------------------------------------------------------

COLOR = discord.Color.from_rgb(170, 21, 27)
#: Miembros que salen en cada lista (menos si no caben).
TOP_BILLS = 10
#: Impuestos con barra en el resumen; el resto, en 📜 Impuestos.
SUMMARY_KINDS = 6
#: Texto que cabe en una pestaña. Discord admite 4.000 caracteres entre todos los
#: `TextDisplay` de un mensaje; el resto es para la cabecera del panel.
PAGE_LIMIT = 3_700
_MEDALS = ("🥇", "🥈", "🥉")

#: Pestañas del panel: `(clave, emoji, nombre)`.
PAGES: tuple[tuple[str, str, str], ...] = (
    ("resumen", "🏛️", "Resumen"),
    ("impuestos", "📜", "Impuestos"),
    ("gente", "👥", "Contribuyentes"),
    ("caseros", "🏠", "Caseros"),
    ("empresas", "🏪", "Empresarios"),
)


@dataclass(frozen=True, slots=True)
class TaxLine:
    """Un impuesto de las pestañas 🏠 y 🏪 (y de los bloques de la factura).

    Attributes:
        key: Clave de `housing_tax_breakdown` o `business_tax_breakdown`.
        short: Nombre corto, para el desglose de cada miembro.
    """

    key: str
    emoji: str
    label: str
    short: str
    law: str


#: Lo que pagan los pisos, en el orden de 🏠.
HOUSING_TAXES: tuple[TaxLine, ...] = (
    TaxLine("itp", "🏠", "ITP al comprar", "ITP", "art. 31 DLeg 1/2009 de Canarias"),
    TaxLine("ibi", "🏘️", "IBI", "IBI", "art. 60 RDL 2/2004"),
    TaxLine("alquiler", "🔑", "IRPF de las rentas", "IRPF rentas", "arts. 22 y 23 LIRPF"),
    TaxLine("ganancia", "📈", "IRPF de lo ganado al vender y de la SOCIMI", "IRPF ventas",
     "arts. 33 y 66 LIRPF"),
    TaxLine("igic", "🏖️", "IGIC de las estancias vacacionales", "IGIC vac.",
     "art. 32 DLeg 1/2025 de Canarias"),
    TaxLine("igic_gastos", "🧾", "IGIC de notaría, agencia, reparaciones y abogados", "IGIC gastos",
     "art. 32 DLeg 1/2025 de Canarias"),
    TaxLine("ips", "🛡️", "Impuesto sobre las primas de los seguros (también los de préstamos)",
     "IPS", "art. 12 Ley 13/1996"),
    TaxLine("icio", "🏗️", "ICIO de las reformas y divisiones", "ICIO", "art. 102 RDL 2/2004"),
    TaxLine("licencia_obra", "📜", "Tasas de licencias de obra", "Tasa obra",
     "ordenanza fiscal (tasa)"),
    TaxLine("ajd", "✒️", "AJD de las escrituras de división horizontal", "AJD",
     "art. 36 DLeg 1/2009 de Canarias"),
    TaxLine("licencia", "📜", "Tasas de licencias vacacionales", "Tasa vac.",
     "Ley 6/2025 de Canarias"),
)  # fmt: skip

#: Lo que pagan los negocios de `pala`, en el orden de 🏪 (claves de
#: `EconomyRepository.business_tax_breakdown`).
BUSINESS_TAXES: tuple[TaxLine, ...] = (
    TaxLine("igic", "🛒", "IGIC de compras, alquiler, suministros y servicios", "IGIC",
     "arts. 32 y 89-91 DLeg 1/2025 de Canarias"),
    TaxLine("cotizacion", "🏢", "Seguridad Social de la plantilla", "SS plantilla",
     "LGSS (recargo del art. 30)"),
    TaxLine("retencion_plantilla", "🧑‍🍳", "IRPF de las nóminas de la plantilla", "IRPF plantilla",
     "arts. 99 y 101 LIRPF"),
    TaxLine("irpf", "🪏", "IRPF del dueño (a cuenta de las ventas, sueldo, dividendos y ganancias)",
     "IRPF dueño", "arts. 17.2.e, 25.1.a, 27 y 33 LIRPF"),
    TaxLine("sociedades", "🏭", "Impuesto sobre Sociedades", "Sociedades", "art. 29.1 LIS"),
    TaxLine("aiem", "🚢", "AIEM de las importaciones", "AIEM", "art. 92 DLeg 1/2025 de Canarias"),
    TaxLine("retencion", "🔑", "Retención del alquiler del local", "Ret. local",
     "art. 100 RIRPF y 101.8 LIRPF"),
    TaxLine("tasa", "📜", "Tasas de apertura, traslado y terraza", "Tasas", "art. 20 TRLRHL"),
    TaxLine("multa", "🚨", "Multas de la Inspección de Trabajo", "Multas", "LISOS"),
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Lo que enseña el panel de `hacienda`, leído una vez al abrirlo.

    Attributes:
        guild_id: Servidor.
        year: Año de `treasury.collected_since`.
        treasury: Saldo y recaudación de la cuenta del Estado.
        bills: Facturas de cada miembro, de la que más paga a la que menos.
        housing: Lo de `EconomyService.housing_tax_breakdown`.
        business: Lo de `EconomyService.business_tax_breakdown`.
        solidarity: `(declaraciones, contribuyentes, lo pagado)` de las Grandes Fortunas.
        names: Nombre de cada miembro que sale.
    """

    guild_id: int
    year: int
    treasury: Treasury
    bills: list[TaxBill]
    housing: dict[int, dict[str, int]]
    business: dict[int, dict[str, int]]
    solidarity: tuple[int, int, int]
    names: dict[int, str]

    @property
    def server(self) -> TaxBill:
        """Todas las facturas sumadas."""
        return server_bill(self.bills)


def bar(share: float, width: int = 10) -> str:
    """`▰▰▰▱▱▱▱▱▱▱`: la parte de un total, en monoespaciada para que las barras se alineen."""
    share = min(1.0, max(0.0, share))
    # Lo que se ha pagado, aunque sea poco, se ve.
    filled = max(1, round(share * width)) if share > 0 else 0
    return f"`{'▰' * filled}{'▱' * (width - filled)}`"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + " %"


def _bare(amount: int) -> str:
    return format_amount(amount).removesuffix(" Y$")


def _name(names: dict[int, str], user_id: int) -> str:
    return names.get(user_id, f"<@{user_id}>")


def ranking(
    rows: list[tuple[int, int, str]], names: dict[int, str], *, room: int, more: str
) -> str:
    """Los que más pagan, con su desglose debajo, de más a menos.

    Si no cabe en `room`, quita los últimos (los que menos pagan) y dice cuántos faltan.

    Args:
        rows: `(miembro, total, desglose)`, ya ordenadas.
        more: Lo que sigue a «Y N» en la línea de los que no salen.
    """
    lines = [
        f"{_MEDALS[i] if i < len(_MEDALS) else f'`{i + 1}.`'} {_name(names, user_id)} · "
        f"**{format_amount(total)}**\n-# {detail}"
        for i, (user_id, total, detail) in enumerate(rows[:TOP_BILLS])
    ]

    def text(shown: int) -> str:
        out = "\n".join(lines[:shown])
        return out + (f"\n-# Y {len(rows) - shown} {more}" if len(rows) > shown else "")

    shown = len(lines)
    while shown > 1 and len(text(shown)) > room:
        shown -= 1
    return text(shown)


def table_lines(
    table: tuple[TaxLine, ...], amounts: dict[str, int], *, law: bool = True
) -> list[str]:
    """Una línea por impuesto de `HOUSING_TAXES` o `BUSINESS_TAXES` que se haya pagado."""
    return [
        f"{line.emoji} {line.label}: **{format_amount(amounts[line.key])}**"
        + (f" · {line.law}" if law else "")
        for line in table
        if amounts.get(line.key)
    ]


def solidarity_line(filed: int, people: int, paid: int) -> str:
    """La línea de las Grandes Fortunas de `hacienda`: se declaran aunque salgan a cero."""
    who = "fortuna" if people == 1 else "fortunas"
    return (
        f"{filed} declaraciones de {people} {who} de más de "
        f"{format_amount(SOLIDARITY_WEALTH_THRESHOLD)}: **{format_amount(paid)}**. Se "
        "descuenta el Patrimonio pagado y Canarias no lo bonifica, así que sale a cero "
        "· art. 3 Ley 38/2022"
    )


def housing_lines(amounts: dict[str, int]) -> list[str]:
    """Una línea por impuesto de los pisos que se haya pagado."""
    return table_lines(HOUSING_TAXES, amounts)


def _table_detail(table: tuple[TaxLine, ...], amounts: dict[str, int]) -> str:
    return " · ".join(
        f"{line.emoji} {line.short} {_bare(amounts[line.key])}"
        for line in table
        if amounts.get(line.key)
    )


def _table_page(
    table: tuple[TaxLine, ...],
    by_member: dict[int, dict[str, int]],
    names: dict[int, str],
    *,
    title: str,
    list_title: str,
    more: str,
) -> str:
    """Pestaña 🏠 o 🏪: lo que paga todo el servidor por impuesto y quién paga más."""
    totals: dict[str, int] = {}
    for amounts in by_member.values():
        for key, value in amounts.items():
            totals[key] = totals.get(key, 0) + value
    head = f"### {title} · {format_amount(sum(totals.values()))}\n" + "\n".join(
        table_lines(table, totals)
    )
    ranked = sorted(by_member.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    rows = [(uid, sum(amounts.values()), _table_detail(table, amounts)) for uid, amounts in ranked]
    head += f"\n\n### {list_title}\n"
    return head + ranking(rows, names, room=PAGE_LIMIT - len(head), more=more)


def leader(snapshot: Snapshot, page: str) -> int | None:
    """Quién encabeza la lista de una pestaña, si hay al menos dos en ella."""
    if page == "caseros":
        by_member = snapshot.housing
    elif page == "empresas":
        by_member = snapshot.business
    else:
        paying = [bill for bill in snapshot.bills if bill.total > 0]
        return paying[0].user_id if len(paying) >= 2 else None
    if len(by_member) < 2:
        return None
    return min(by_member.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))[0]


def summary_page(snapshot: Snapshot) -> str:
    """🏛️ Resumen: el saldo del Estado, de dónde sale lo recaudado y quién paga más."""
    treasury, server = snapshot.treasury, snapshot.server
    lines = [
        f"## 💰 {format_amount(treasury.balance)}",
        "-# Saldo de la cuenta del Estado",
        f"📅 Recaudado en {snapshot.year}: **{format_amount(treasury.collected_since)}**",
        f"🗂️ Desde siempre: **{format_amount(treasury.collected_total)}**",
    ]
    if treasury.debt:
        lines.append(
            f"💸 Deuda pública (premios de lotería sin fondos): **{format_amount(treasury.debt)}**"
        )
    paid = sorted(
        (
            kind
            for kind in TAX_KINDS
            if server.amounts.get(kind.key, 0) > 0
            and kind.group in (Group.DIRECT, Group.INDIRECT, Group.OTHER)
        ),
        key=lambda kind: -server.amounts[kind.key],
    )
    gross = sum(server.amounts[kind.key] for kind in paid)
    if paid:
        lines += ["", "### 📊 De dónde sale"]
        for kind in paid[:SUMMARY_KINDS]:
            amount = server.amounts[kind.key]
            lines.append(
                f"{bar(amount / gross)} **{_pct(amount / gross)}** {kind.emoji} {kind.short}"
                f" · {format_amount(amount)}"
            )
        if len(paid) > SUMMARY_KINDS:
            lines.append(f"-# Y {len(paid) - SUMMARY_KINDS} impuestos más en 📜 Impuestos.")
        refunded = f" · devuelto −{format_amount(server.refunded)}" if server.refunded else ""
        lines.append(
            f"-# Neto para {TAX_COLLECTOR}: {format_amount(server.total)} · directos "
            f"{format_amount(server.direct)} · indirectos {format_amount(server.indirect)}"
            f"{refunded}"
        )
    paying = [bill for bill in snapshot.bills if bill.total > 0]
    if paying:
        lines += ["", "### 🏆 Quién más paga"]
        for i, bill in enumerate(paying[: len(_MEDALS)]):
            share = bill.total / server.total if server.total > 0 else 0.0
            lines.append(
                f"{_MEDALS[i]} {_name(snapshot.names, bill.user_id)} · "
                f"**{format_amount(bill.total)}** · {_pct(share)}"
            )
    parts = []
    if snapshot.housing:
        homes = sum(sum(amounts.values()) for amounts in snapshot.housing.values())
        parts.append(f"🏠 los pisos, {format_amount(homes)}")
    if snapshot.business:
        shops = sum(sum(amounts.values()) for amounts in snapshot.business.values())
        parts.append(f"🏪 los negocios, {format_amount(shops)}")
    if parts:
        lines.append("-# De todo esto, " + " y ".join(parts) + ".")
    if not paid and not paying:
        lines += ["", "Nadie ha pagado nada todavía. Sanxe espera sentado."]
    return "\n".join(lines)


#: Bloques de las facturas, en orden: `(grupo, título en el servidor, título en la factura)`.
_SECTIONS = (
    (Group.DIRECT, "Directos", "Directos"),
    (Group.INDIRECT, "Indirectos (sin verlos)", "Indirectos (sin verlos)"),
    (Group.OTHER, "Otros pagos al Estado", "Otros pagos al Estado"),
    (Group.REFUND, "Devuelto en la renta", "Te ha devuelto"),
    (Group.FOREIGN, "Fuera de España (no va a Sanxe)", "Fuera de España (no va a Sanxe)"),
)


def _sections(bill: TaxBill, *, member: bool, law: bool) -> list[str]:
    out = []
    for group, server_title, member_title in _SECTIONS:
        if lines := detail_lines(bill, group, law=law):
            title = member_title if member else server_title
            sign = "−" if group is Group.REFUND else ""
            out.append(
                f"### {title} · {sign}{format_amount(bill.group_total(group))}\n" + "\n".join(lines)
            )
    return out


def taxes_page(snapshot: Snapshot) -> str:
    """📜 Impuestos: lo recaudado en el servidor por impuesto, con su norma, y qué se cobra."""
    filed, people, paid = snapshot.solidarity
    rules = f"### ❓ Qué se cobra\n-# {treasury_rules()}"
    for law in (True, False):
        blocks = _sections(snapshot.server, member=False, law=law)
        if filed:
            blocks.append(f"💎 Grandes Fortunas: {solidarity_line(filed, people, paid)}")
        text = "\n\n".join([*blocks, rules]) if blocks else rules
        if len(text) <= PAGE_LIMIT:
            return text
    return text[: PAGE_LIMIT - 1] + "…"


def people_page(snapshot: Snapshot) -> str:
    """👥 Contribuyentes: lo que paga cada uno, directo e indirecto."""
    paying = [bill for bill in snapshot.bills if bill.total > 0 or bill.amounts]
    head = (
        "### 👥 Lo que paga cada uno\n-# Directo e indirecto, desde siempre. "
        "`hacienda @miembro` para ver la factura entera de cualquiera.\n"
    )
    if not paying:
        return head + "Nadie ha pagado nada todavía."
    rows = [(bill.user_id, bill.total, compact_line(bill)) for bill in paying]
    return head + ranking(rows, snapshot.names, room=PAGE_LIMIT - len(head), more="más.")


def landlords_page(snapshot: Snapshot) -> str:
    """🏠 Caseros: lo que pagan los pisos del servidor y cada casero."""
    if not snapshot.housing:
        return "### 🏠 Lo que pagan los pisos\nNadie tiene pisos todavía. Se compran en `pisos`."
    return _table_page(
        HOUSING_TAXES, snapshot.housing, snapshot.names,
        title="🏠 Lo que pagan los pisos", list_title="🔑 Los caseros que más pagan",
        more="caseros más.",
    )  # fmt: skip


def owners_page(snapshot: Snapshot) -> str:
    """🏪 Empresarios: lo que pagan los negocios de `pala` y cada dueño."""
    if not snapshot.business:
        return (
            "### 🏪 Lo que pagan los negocios\n"
            "Nadie tiene un negocio todavía. Se monta desde `pala`."
        )
    return _table_page(
        BUSINESS_TAXES, snapshot.business, snapshot.names,
        title="🏪 Lo que pagan los negocios", list_title="💼 Los empresarios que más pagan",
        more="empresarios más.",
    ) + "\n-# Todo esto ya cuenta en 👥 Contribuyentes, a nombre de cada dueño."  # fmt: skip


#: Lo que pinta cada pestaña.
PAGE_TEXT = {
    "resumen": summary_page,
    "impuestos": taxes_page,
    "gente": people_page,
    "caseros": landlords_page,
    "empresas": owners_page,
}


def member_page(
    *,
    member_name: str,
    all_bills: list[TaxBill],
    year_bills: list[TaxBill],
    user_id: int,
    year: int,
    homes: dict[str, int],
    business: dict[str, int],
) -> str:
    """Factura fiscal completa de un miembro: todo lo que ha pagado y cómo."""
    server = server_bill(all_bills)
    bill = next((b for b in all_bills if b.user_id == user_id), TaxBill(user_id))
    this_year = next((b for b in year_bills if b.user_id == user_id), TaxBill(user_id))
    share = bill.total / server.total if server.total > 0 else 0.0
    rank = next((i for i, b in enumerate(all_bills, 1) if b.user_id == user_id), None)
    lines = [
        f"# 🧾 Factura fiscal de {member_name}",
        f"## {format_amount(bill.total)}",
        f"-# Para {TAX_COLLECTOR}, desde siempre · en {year}: {format_amount(this_year.total)}",
        f"{bar(share)} **{_pct(share)}** de todo lo recaudado"
        + (f" · puesto {rank} de {len(all_bills)}" if rank else ""),
        f"*{verdict(bill, share)}*",
    ]
    blocks = ["\n".join(lines), *_sections(bill, member=True, law=False)]
    if not bill.amounts:
        blocks.append(
            "### Nada que declarar\n"
            "Ni nóminas, ni premios, ni compras. Sanxe aún no sabe que existes."
        )
    inside = "\n-# Ya está dentro de los directos e indirectos de arriba."
    if homes:
        blocks.append(
            f"### 🏠 Por sus pisos · {format_amount(sum(homes.values()))}\n"
            + "\n".join(housing_lines(homes))
            + inside
        )
    if business:
        blocks.append(
            f"### 🏪 Por sus negocios · {format_amount(sum(business.values()))}\n"
            + "\n".join(table_lines(BUSINESS_TAXES, business))
            + inside
        )
    blocks.append(
        "-# La SS de la empresa es coste de tu puesto aunque no salga de tu bolsillo. "
        "El IGIC va dentro de cada precio."
    )
    return "\n\n".join(blocks)
