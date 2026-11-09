"""Trabajo: `pala`, coger la pala y currar.

`pala` abre el **inicio** (solo lo toca su dueño): una imagen de tu día con sus 8
franjas (`bot.services.agenda`), la tarjeta de tu curro y la de tu negocio, y un botón
para cada uno: 🪏 **Trabajo** (o 💼 **Buscar curro**) y 🏪 **Negocio** (o 🏪 **Montar
negocio**, que lleva al panel de `bot.cogs.business`). La imagen la pinta la escena de los
negocios (`negocio.paint`); sin Node ni Chromium, el inicio sale en texto.

🪏 **Trabajo** es el panel de tu curro:

- Sin contrato, un menú para elegir oficio (obra, hostelería, política, sanidad,
  oficina, reparto, funcionariado o influencer).
- Con contrato: puesto, sueldo, batería, rendimiento, familia, jornada y lo
  que falta para ascender. Botones para ⛏️ **Fichar**, 📈 **Ascender** cuando
  toca, 🎓 **Formación** cuando hace falta un curso, 📜 **Vida laboral** y 🏠
  **Inicio**, y menús para la máquina de café, la jornada (reducción de jornada y
  excedencia, sin dinero) y para cambiar de oficio.

Según el oficio y el puesto aparecen también 🚑 **Guardia** (sanidad), 🏠
**Teletrabajo** (oficina), 🌏 **Irse a Hong Kong** / ✈️ **Volver a casa**
(oficina, desde senior) y 📚 **Estudiar** (funcionariado: la oposición, sin
sueldo). Viviendo fuera no se cambia de oficio. El panel enseña también lo
propio de cada oficio: el tiempo y las estrellas de la app (reparto), los
trienios y la plaza fija (funcionariado) y los seguidores (influencer).

**Carrera larga.** Los oficios con ramas (`Job.branches`) piden elegir una al
ascender desde el final del tronco: 📈 **Ascender** enseña las ramas con su
primer puesto, su techo y su pensión. Cada puesto puede dar una ventaja (🏅)
que se suma a las herramientas. En lo más alto de una rama aparece 🏖️
**Jubilarse**: se sigue por la otra rama y se gana una pensión diaria que se
cobra con 💰 **Cobrar pensión** (también sin contrato). El panel enseña el
🌠 **deseo del día**: el turno que lo cumple paga más y sube la barra.

**Anuncios.** Los hitos se cuentan en el canal del panel, en un mensaje aparte
y sin mencionar a nadie: ascensos pasado el tronco, llegar a lo más alto,
jubilarse, un exit o una quiebra y los vídeos virales.

Fichar lanza el minijuego del puesto en el mismo mensaje (cavar, detectar,
memoria, diálogo, clasificar o calcular; de 30 a 60 s). Al acabar, o al agotarse el tiempo, se
cobra la nómina y se enseña con su desglose; a veces sale un evento con dos
opciones. Las herramientas de curro de la tienda (`bot.services.work_tools`)
se aplican solas al fichar si el miembro las tiene.

**Botones del minijuego.** No los atiende la vista, sino el listener
`Work.on_interaction`, por su `custom_id` (`pala:<panel>:<ronda>:<opción>`).
discord.py desengancha los botones de una vista al redibujarla y solo vuelve a
registrar los nuevos cuando Discord confirma la edición, así que un clic que
llegaba en esos 100–500 ms se perdía («Esta interacción ha fallado»). En la
memoria, donde se pulsa seguido, pasaba a cada rato. Con el listener todo clic
llega: si es de la ronda en juego cuenta, si es de una ronda ya pasada se
acepta sin hacer nada, y las ediciones van de una en una (`_render_lock`)
para que el panel nunca se quede en un estado viejo.

Las reglas están en `bot.services.work`, los minijuegos en
`bot.services.work_games` y los casos de uso (y el dinero) en
`bot.services.pala`.

Dinero: nóminas con IRPF y Seguridad Social (`pay_salary`), turnos en B y
sobres sin declarar (`pay_undeclared`), multas (`sanction`), cuota de
autónomos, café y formación con IGIC. Todas las acciones con dinero que llegan
por botón llevan el aviso de la Renta. Alimenta los logros de 🪏 Trabajo y 👷
Oficios.

Canales: si un administrador elige canales con `tajo`, `pala` solo se abre
en ellos y en sus hilos. En otro canal dice dónde está (`bot.utils.channels`); con
`.pala`, el aviso y el comando se borran solos. Permisos del bot: enviar mensajes en
el canal (y gestionar mensajes, para borrar el `.pala` mal puesto).

Funciones puente para `pisos`: el favor 👴 «Tus padres te avalan» (`parents_aval`,
`spend_parents_aval` y `parents_lose_home`) y quién levanta la obra nueva de los lunes
desde la rama Ladrillo (`ladrillo_builders`). Para `saldo`: la pensión de hoy, la
cuota de autónomo y lo que se le debe a mamá (`pending`). Para el negocio: volver al
inicio (`home`) y saber si alguien está fichando (`busy`). No lanzan y no hacen nada
sin el cog.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import random
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import banco_compras as financiar
from bot.cogs import business as negocio
from bot.cogs import pets as mascotas
from bot.cogs import renta
from bot.cogs import shop as tienda
from bot.services import credit
from bot.services.achievements import StatDelta, work_stats
from bot.services.agenda import JORNADA_NAMES, JORNADAS
from bot.services.business_view import dia_state
from bot.services.economy import CURRENCY_EMOJI, format_amount, next_daily_at
from bot.services.levels import TIMEZONE, local_day
from bot.services.pala import (
    OPTIONS_EXEMPT,
    EventResult,
    FavorResult,
    NeedsBlack,
    OffDuty,
    Pension,
    Retirement,
    Shift,
    ShiftOutcome,
    Status,
    WorkError,
    WorkService,
)
from bot.services.pending import Pending, Side
from bot.services.pets import Event as PetEvent
from bot.services.pets import Moment
from bot.services.taxes import (
    EXEMPT_7P_PER_DAY,
    TAX_COLLECTOR,
    ForeignPayslip,
    Payslip,
    format_rate,
    igic,
)
from bot.services.telemetry import LISTENER_ITEM, TimedLock
from bot.services.work import (
    ACTIVE_SHARES,
    BATTERY_MAX,
    BATTERY_MIN,
    EXCEDENCIA_MIN_DAYS,
    FAMILY_REGEN_PER_HOUR,
    FAMILY_TIERS,
    FLIGHT_PRICE,
    LEGAL_EXTRAS_PER_WEEK,
    MAX_COFFEES,
    MOM_REPAY_SHARE,
    ORDINARY_SHIFTS,
    PENSION_CAP,
    PENSION_WEIGHTS,
    RAIN_PAY,
    SELF_EMPLOYED_FEE,
    TRIENNIUM_PAY,
    VIRAL_AT,
    WISH_PAY,
    WISH_PERFORMANCE,
    Career,
    Contract,
    FamilyTier,
    Mechanic,
    ShiftKind,
    Training,
    battery_bar,
    battery_eta,
    battery_state,
    excedencia_blocker,
    excedencia_return_at,
    family_ready_at,
    family_tier,
    inspection_chance,
    inspection_surcharge,
    performance_bar,
    rested_at,
    stars,
)
from bot.services.work_catalog import (
    CAUGHT_TEXT,
    COFFEES,
    EVENT_BY_KEY,
    FAVOR_BY_KEY,
    FAVORS,
    GIFT_BY_KEY,
    GIFTS,
    JOB_BY_KEY,
    JOBS,
    NIGHT_CAUGHT_TEXT,
    Event,
)
from bot.services.work_games import (
    AUDIT_HIT,
    DEAL_BREAK,
    DESK_MAX,
    GRACE_SECONDS,
    ORDAGO_LOST,
    ORDAGO_WON,
    AuditGame,
    DealGame,
    DeskGame,
    PlanGame,
)
from bot.services.work_tools import TOOL_KEYS
from bot.utils.channels import wrong_channel
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, claim_custom_ids, edit, notify
from bot.utils.responder import ContextResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Segundos sin tocar el panel antes de que caduque.
PANEL_TIMEOUT = 300
#: Nombre del PNG del inicio en el mensaje.
HOME_PNG = "pala.png"
COLOR_IDLE = discord.Color.from_rgb(230, 126, 34)
COLOR_GAME = discord.Color.from_rgb(88, 101, 242)
COLOR_BAD = discord.Color.from_rgb(80, 84, 92)

MECHANIC_NAMES = {
    Mechanic.DIG: "cavar",
    Mechanic.SPOT: "detectar",
    Mechanic.MEMORY: "memoria",
    Mechanic.DIALOGUE: "diálogo",
    Mechanic.SORT: "clasificar",
    Mechanic.CALC: "calcular",
    Mechanic.PLAN: "cuadrante",
    Mechanic.DEAL: "negociación",
    Mechanic.AUDIT: "auditoría",
    Mechanic.DESK: "despacho",
}
#: Cómo se ve el tiempo en el panel del reparto.
WEATHER_TEXT = {
    "sol": "☀️ Sol: un día normal de reparto.",
    "lluvia": f"🌧️ **Llueve**: los pedidos pagan ×{str(RAIN_PAY).replace('.', ',')}, "
    "pero el suelo resbala (riesgo de caída).",
    "calima": "🌫️ **Calima**: el turno cansa la mitad más.",
}
KIND_TEXT = {
    ShiftKind.ORDINARY: "ordinario",
    ShiftKind.EXTRA: "extra (×1,25)",
    ShiftKind.BLACK: "extra en B 🤫",
    ShiftKind.GUARD: "de guardia 🚑",
    ShiftKind.STUDY: "de estudio 📚",
}
#: Cómo se juega cada minijuego, en una línea.
HOW_TO = {
    Mechanic.DIG: "Pulsa la casilla con una marca segura del plano. La roca no suma y "
    "romper algo resta. Ojo: los mirones no siempre aciertan.",
    Mechanic.SPOT: "Lee la lista y pulsa el que no encaja.",
    Mechanic.MEMORY: "Memoriza la secuencia (el reloj se para hasta 5 segundos), pulsa ✅ "
    "Memorizado y repítela en el mismo orden. Puedes pulsar seguido: no hace falta esperar "
    "a que se actualice el panel.",
    Mechanic.DIALOGUE: "Lee la situación y elige la mejor respuesta.",
    Mechanic.SORT: "Lee el papel y mándalo a su sitio con el botón que toque.",
    Mechanic.CALC: "Haz la cuenta y elige la mejor oferta. La buena gana por goleada.",
    Mechanic.PLAN: "Manda cada pieza a un sitio donde quepa. Ves la cola entera y todo "
    "encaja si lo piensas: lo que se queda fuera resta. Con un fallo gratis puedes deshacer.",
    Mechanic.DEAL: "Aprieta para sacar más y cierra antes de que se levante: si se va, te "
    f"quedas con el {DEAL_BREAK} %. Fíjate en lo que hace y en su carácter. Tantear dice "
    "cuánto aguanta.",
    Mechanic.AUDIT: "Abre todas las casillas buenas sin tocar las malas. Cada casilla "
    "abierta dice cuántas malas tiene al lado. La primera nunca falla; abrir una mala "
    f"resta {AUDIT_HIT}.",
    Mechanic.DESK: "Cada opción sube una barra y baja otra. Tu nota es cómo queda el "
    "despacho al final: la suma de las barras, con la más baja contando triple. Si una "
    "llega a cero, crisis: se acaba el turno.",
}
#: Pasillo de la tienda con los regalos para la familia (botón 🎁 del panel).
GIFT_AISLE = "familia"
GIFT_KEYS = frozenset(gift.key for gift in GIFTS)
#: Marrones de los que te libra la excusa familiar (no la dimisión).
EVENT_KEYS = frozenset(EVENT_BY_KEY)
#: Prefijo de los `custom_id` de los botones del minijuego.
GAME_ID_PREFIX = "pala:"
#: Opción del botón ✅ Memorizado en el `custom_id`.
HIDE_CHOICE = "ver"


def game_id(token: int, round_index: int, choice: int | str) -> str:
    """`custom_id` de un botón del minijuego: panel, ronda y opción."""
    return f"{GAME_ID_PREFIX}{token}:{round_index}:{choice}"


def parse_game_id(custom_id: str) -> tuple[int, int, int | str] | None:
    """Lo contrario de `game_id`; `None` si no es un botón del minijuego."""
    if not custom_id.startswith(GAME_ID_PREFIX):
        return None
    parts = custom_id[len(GAME_ID_PREFIX) :].split(":")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    choice: int | str = int(parts[2]) if parts[2].isdigit() else parts[2]
    if choice != HIDE_CHOICE and not isinstance(choice, int):
        return None
    return int(parts[0]), int(parts[1]), choice


PHASE_TEXT = {
    "residente": "sigues siendo residente fiscal en España (art. 7.p LIRPF: exento hasta "
    f"{format_amount(EXEMPT_7P_PER_DAY)} al día)",
    "no_residente": "ya no eres residente fiscal en España: solo paga Hong Kong",
}


def decimal(value: float) -> str:
    """`2.5` → `2,5` (coma decimal, al estilo español)."""
    return f"{value:.1f}".replace(".", ",")


def _pct(rate: float) -> str:
    return format_rate(rate)


def payslip_text(
    slip: Payslip, *, title: str, self_employed: bool, fee: int = SELF_EMPLOYED_FEE
) -> str:
    """Desglose de una nómina para enseñarla."""
    lines = [f"📄 **Nómina** · {title}", f"Bruto: **{format_amount(slip.gross)}**"]
    if slip.ss_worker:
        lines.append(
            f"Seguridad Social: −{format_amount(slip.ss_worker)} ({_pct(slip.rates.ss_worker)})"
        )
    if slip.irpf:
        lines.append(
            f"IRPF: −{format_amount(slip.irpf)} (tu tipo esta semana: {_pct(slip.rates.irpf)})"
        )
    else:
        lines.append("IRPF: 0 Y$ (con tu renta de la semana aún no llegas al mínimo)")
    lines.append(f"## {CURRENCY_EMOJI} Neto: {format_amount(slip.net)}")
    if self_employed:
        lines.append(
            "-# 🧾 Eres autónomo: no cotizas por turno, pagas la cuota cada semana "
            f"({format_amount(fee)}" + (", tarifa plana" if fee < SELF_EMPLOYED_FEE else "") + ")."
        )
    else:
        lines.append(
            f"-# 🏢 La empresa paga además {format_amount(slip.ss_employer)} de Seguridad "
            f"Social. Coste total: {format_amount(slip.employer_cost)}."
        )
    if slip.net > 0 and slip.total_taxes:
        share = round(slip.total_taxes / slip.net * 100)
        lines.append(
            f"-# 🐶 Impuestos de este turno: {format_amount(slip.total_taxes)}, "
            f"un {share} % de lo que te llega."
        )
    return "\n".join(lines)


def foreign_payslip_text(slip: ForeignPayslip, *, title: str) -> str:
    """Desglose de una nómina cobrada en Hong Kong."""
    lines = [
        f"📄 **Payslip** · {title} · 🇭🇰 Hong Kong",
        f"Bruto (paquete de expatriado): **{format_amount(slip.gross)}**",
        f"MPF (5 %): −{format_amount(slip.mpf_worker)}",
        f"Salaries tax: −{format_amount(slip.hk_tax)}"
        if slip.hk_tax
        else "Salaries tax: 0 Y$ (aún no pasas de la deducción personal de HK$132.000)",
    ]
    if slip.resident:
        lines.append(f"Exento en España (art. 7.p LIRPF): {format_amount(slip.exempt)}")
        if slip.irpf or slip.double_tax_relief:
            lines.append(
                f"IRPF español: −{format_amount(slip.irpf)} (ya descontados "
                f"{format_amount(slip.double_tax_relief)} por doble imposición, art. 80 LIRPF)"
            )
    else:
        lines.append("IRPF español: 0 Y$ (ya no eres residente en España)")
    lines.append(f"## {CURRENCY_EMOJI} Neto: {format_amount(slip.net)}")
    lines.append(
        f"-# 🇭🇰 Hong Kong se queda {format_amount(slip.foreign)} (MPF de los dos y salaries "
        f"tax). IRPF español: {format_amount(slip.irpf)}."
    )
    return "\n".join(lines)


def battery_note(status: Status) -> str:
    """Cuándo se puede volver a fichar y cuándo estará llena la batería."""
    if status.battery >= BATTERY_MAX:
        return " · llena"
    contract, now = status.contract, status.now
    full = f"llena <t:{int(battery_eta(contract, BATTERY_MAX, now))}:R>"
    if status.battery <= BATTERY_MIN:
        return f" · 🛏️ vuelves a fichar <t:{int(rested_at(contract, now))}:R> · {full}"
    return f" · {full}"


#: Qué hace cada tramo de la familia, para el panel.
TIER_EFFECT = {
    "En casa feliz": "la batería recarga ×1,5 y la barra sube +3 por turno",
    "Tranquila": "recargas a ritmo normal",
    "Mosqueada": "la batería recarga a la mitad y la barra sube la mitad",
    "En crisis": "la batería recarga a un cuarto y la barra no sube",
}


def tier_text(tier: FamilyTier) -> str:
    """`🏡 **En casa feliz**: la batería recarga ×1,5…`."""
    return f"{tier.emoji} **{tier.name}**: {TIER_EFFECT[tier.name]}"


def family_lines(status: Status) -> list[str]:
    """La familia en el panel: valor, tramo, qué hace y cómo cambia."""
    contract, family = status.contract, status.family
    tier = family_tier(family)
    lines = [f"❤️ Familia: {round(family)}/100 · {tier_text(tier)}."]
    better = [t for t in FAMILY_TIERS if t.low > family]
    hint = (
        f"Con {better[-1].low} pasas a {better[-1].emoji} {better[-1].name}"
        if better
        else f"Bajo {tier.low} pierdes el bonus"
    )
    # La casa (`casa`) la hace recuperarse antes: se dice la de verdad.
    home = " (con tu casa)" if contract.home_family > 1 else ""
    lines.append(
        f"-# Sube {decimal(FAMILY_REGEN_PER_HOUR * contract.home_family)} por hora sola{home} y "
        f"con los regalos de la tienda; cada turno cuesta (las extras, el B y la madrugada, "
        f"más). {hint}."
    )
    if contract.no_extras_day == local_day(status.now).isoformat():
        lines.append("👪 **Intervención familiar:** hoy, ni extras, ni B, ni guardias.")
    if contract.motivated:
        lines.append("💪 El próximo turno va con +15 % de tiempo (lo haces por los niños).")
    if contract.mom_debt:
        lines.append(
            f"💸 Le debes **{format_amount(contract.mom_debt)}** a tu madre: se lleva el "
            f"{round(MOM_REPAY_SHARE * 100)} % de lo que cobras en cada turno."
        )
    return lines


def family_change(before: float, after: float, parts: list[tuple[str, int]]) -> str:
    """`❤️ 70/100 (−11: turno −6, madrugada −5)`."""
    text = f"❤️ {round(after)}/100"
    if parts:
        detail = ", ".join(f"{name} {value:+d}".replace("-", "−") for name, value in parts)
        text += f" ({round(after - before):+d}: {detail})".replace("-", "−")
    return text


def wait_text(ready: float, now: float) -> str:
    """`listo`, `en 40 min` o `en 5 h`."""
    left = ready - now
    if left <= 0:
        return "listo"
    if left < 3600:
        return f"en {max(1, round(left / 60))} min"
    return f"en {round(left / 3600)} h"


def family_options(
    contract: Contract, gifts: frozenset[str], now: float
) -> list[discord.SelectOption]:
    """Opciones del desplegable de la familia: los regalos y los favores.

    Los regalos que tienes se dan (`g:`); los que no, se compran y se dan en el
    acto (`b:`), con la caja de la tienda dentro del panel.
    """
    options = [
        discord.SelectOption(
            label=f"Dar: {gift.name} · +{gift.family} ❤️",
            value=f"g:{gift.key}",
            emoji=gift.emoji,
            description="Regalo de tu mochila · "
            + wait_text(family_ready_at(contract, gift.key, gift.cooldown), now),
        )
        if gift.key in gifts
        else discord.SelectOption(
            label=f"Comprar y dar: {gift.name} · +{gift.family} ❤️",
            value=f"b:{gift.key}",
            emoji=gift.emoji,
            description="De la tienda, sin salir de aquí · "
            + wait_text(family_ready_at(contract, gift.key, gift.cooldown), now),
        )
        for gift in GIFTS
    ]
    options.extend(
        discord.SelectOption(
            label=f"{favor.name} · −{favor.cost} ❤️",
            value=f"f:{favor.key}",
            emoji=favor.emoji,
            description=f"{favor.text} · "
            f"{wait_text(family_ready_at(contract, favor.key, favor.cooldown), now)}"[:100],
        )
        for favor in FAVORS
        if favor.key != "excusa"
    )
    return options


def where_text(status: Status) -> str:
    """`Política (PSOE) · 🏛️ Moncloa`: el oficio y, si está en una, la rama."""
    branch = status.branch
    return status.job.name + (f" · {branch.emoji} {branch.name}" if branch else "")


def status_text(status: Status, notes: list[str]) -> str:
    """Texto del panel de un contrato."""
    contract, job, position = status.contract, status.job, status.position
    state = battery_state(status.battery)
    state_text = {"bien": "", "reventado": " · **reventado**", "zombi": " · **zombi** 🧟"}[state]
    lines = [
        f"## {job.emoji} {position.title} · {where_text(status)} ({contract.level}/{job.top})",
        f"Sueldo base: **{format_amount(position.base_pay)}** por turno · Minijuego: "
        f"{MECHANIC_NAMES[position.mechanic]} ({position.seconds} s)"
        + (" · autónomo" if position.self_employed else ""),
        f"{battery_bar(status.battery)}{state_text}{battery_note(status)}",
        performance_bar(contract.performance)
        + (" · ⚠️ aviso: si vuelve a 0, te bajan" if contract.warned else ""),
        *family_lines(status),
        f"🗓️ Hoy: {min(contract.shifts_today, contract.jornada)}/{contract.jornada} turnos "
        f"ordinarios · Extras legales esta semana: "
        f"{min(contract.extras_week, LEGAL_EXTRAS_PER_WEEK)}/{LEGAL_EXTRAS_PER_WEEK} · "
        f"franjas libres hoy: {status.agenda.free} (+{status.agenda.night_free} de noche 🌙)",
        "Próximo turno: " + next_turn_text(status),
    ]
    if contract.on_excedencia:
        back = int(excedencia_return_at(contract))
        lines.append(
            "🏝️ **De excedencia voluntaria** (art. 46.2 ET): no fichas ni reservas franjas. "
            f"Puedes volver <t:{back}:R>, al mismo puesto y con la barra a la mitad."
        )
    elif contract.reduced:
        lines.append(
            f"🧸 **{JORNADA_NAMES[contract.jornada]}** (art. 37.6 ET): cobras por turno lo mismo, "
            "pero sin horas extra, B ni guardias."
        )
    if contract.jornada_next:
        lines.append(
            f"-# A partir de mañana: {JORNADA_NAMES[contract.jornada_next].lower()} (preaviso, "
            "art. 37.7 ET)."
        )
    if status.can_guard:
        required = job.required_guards(contract.level)
        lines.append(
            f"🚑 Guardias esta semana: {contract.guards_week}"
            + (f"/{required} obligatorias" if required else "")
            + " · la barra solo sube de verdad con guardias"
        )
    if status.off_duty:
        lines.append(f"🛌 **Saliente de guardia** hasta <t:{int(contract.off_duty_until)}:t>.")
    if contract.abroad:
        days = (status.now - contract.abroad_since) / 86_400
        lines.append(
            f"🇭🇰 **En Hong Kong** desde hace {decimal(days)} días: {PHASE_TEXT[status.phase]}. "
            "Sin IMV mientras vivas fuera."
        )
    if status.beckham:
        lines.append(
            f"⚽ **Ley Beckham** hasta <t:{int(contract.beckham_until)}:d>: IRPF al 24 % fijo."
        )
    if job.has_options(contract.level, contract.branch) or contract.options:
        lines.append(
            f"🦄 Stock options acumuladas: **{format_amount(contract.options)}** (en papel)"
        )
    lines.extend(job_lines(status))
    if status.veteran:
        plural = "s" if status.veteran > 1 else ""
        lines.append(
            f"🎖️ Veteranía: {status.veteran} rama{plural} de otros oficios · "
            f"+{round(status.veteran_bonus * 100)} % de sueldo y menos días y tareas para ascender"
        )
    perks = job.perks(contract.level, contract.branch)
    if perks:
        lines.append("🏅 Ventajas del puesto: " + " · ".join(p.text for p in perks))
    if status.wish is not None:
        lines.append(
            f"🌠 **Deseo del día:** {status.wish.text} Premio: sueldo ×{decimal(WISH_PAY)} "
            f"y +{WISH_PERFORMANCE} de barra."
        )
    elif contract.wish_streak:
        lines.append(f"🌠 Deseo del día cumplido · racha de {contract.wish_streak} días.")
    if status.sick:
        lines.append(f"🩹 **De baja** hasta <t:{int(contract.sick_until)}:t>.")
    if status.on_leave:
        lines.append(
            "🏖️ Vuelves de una excedencia: la barra empieza a la mitad en tu próximo turno."
        )
    following = status.next_position
    fork = status.fork
    if fork:
        names = " o ".join(f"{b.emoji} {b.name}" for b in fork)
        if status.promotion_ready:
            lines.append(f"\n📈 **¡Te ofrecen el ascenso!** Eliges rama: {names}.")
        else:
            lines.append(f"\n**Para ascender** (eliges rama: {names}):")
            lines.extend(f"- {item}" for item in status.blockers)
    elif following is None:
        lines.append("\n🏆 **Estás en lo más alto del oficio.**")
        if status.can_retire:
            branch = status.branch
            assert branch is not None
            lines.append(
                f"🏖️ Puedes jubilarte de {branch.emoji} {branch.name}: tu pensión pasaría a "
                f"{format_amount(status.pension_after)} brutos al día, para siempre."
            )
    elif status.promotion_ready:
        lines.append(f"\n📈 **¡Te ofrecen el ascenso a {following.title}!** Pulsa 📈 Ascender.")
    else:
        lines.append(
            f"\n**Para ascender a {following.title}** "
            f"({format_amount(following.base_pay)} por turno):"
        )
        lines.extend(f"- {item}" for item in status.blockers)
    if following is not None and following.perk is not None:
        lines.append(f"-# 🏅 Desbloquea: {following.perk.text}")
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines)


def next_turn_text(status: Status) -> str:
    """Qué sería el próximo turno, contando el saliente, la excedencia y la reducción."""
    contract = status.contract
    if contract.on_excedencia:
        return "ninguno (excedencia)"
    if status.off_duty:
        return "cuando acabe el saliente"
    if contract.reduced and status.next_kind is not ShiftKind.ORDINARY:
        return "mañana (la reducción no deja hacer horas extra)"
    text = KIND_TEXT[status.next_kind]
    if status.next_kind is ShiftKind.ORDINARY:
        return text
    if not status.agenda.left:
        return "mañana (ya has trasnochado toda la noche)"
    night = status.agenda.next_night
    if night:
        text += f" · 🌙 trasnochando ({night}.ª de noche)"
    if status.next_kind is ShiftKind.BLACK:
        text += f" · {black_risk_text(night)}"
    return text


def black_risk_text(late_franja: int) -> str:
    """`30 % de que te pille la Inspección`: el riesgo de un turno en B, de día o de noche."""
    return f"{round(inspection_chance(late_franja) * 100)} % de que te pille la Inspección"


def black_offer_text(late_franja: int) -> str:
    """La letra pequeña de la oferta del B: el riesgo de ahora y lo que pasa si te pillan."""
    fine = round(inspection_surcharge(late_franja) * 100)
    if late_franja:
        return (
            f"🌙 Es tu {late_franja}.ª franja de noche y de noche la Inspección mira más: "
            f"**{black_risk_text(late_franja)}**. Si te pilla, devuelves el turno doble "
            f"(lo cobrado y un {fine} % de multa) y te quedas sin IMV 3 días."
        )
    return (
        f"Cobras el turno sin impuestos: **{black_risk_text(late_franja)}**. Si te pilla, "
        f"devuelves lo cobrado con un {fine} % de recargo y te quedas sin IMV 3 días. "
        "De noche (trasnochando) el riesgo sube."
    )


def home_text(state: dict[str, Any], *, image: bool) -> str:
    """El texto del inicio: corto bajo la imagen y completo si no hay imagen."""
    lines = [f"## 🗓️ {state['date']} · {state['free']}"]
    if not image:
        icons = {
            "hecha": "🟩", "jornada": "🟨", "extra": "🟧", "guardia": "🟥",
            "estudio": "🟪", "negocio": "🟦", "libre": "⬜", "noche": "⬛",
        }  # fmt: skip
        slots = [icons[slot["kind"]] for slot in state["slots"]]
        night = state["night_from"]
        lines.append("".join(slots[:night]) + " 🌙 " + "".join(slots[night:]))
        lines.append(
            "-# 🟩 fichado · 🟨 jornada · 🟧 extra · 🟥 guardia · 🟪 estudio · "
            "🟦 negocio · ⬜ libre · ⬛ noche"
        )
        if job := state["job"]:
            lines.append(f"🪏 {job['title']} ({job['name']}, {job['level']}) · {job['today']}")
        if business := state["business"]:
            lines.append(
                f"🏪 {business['name']} · {business['sector_name']} en {business['barrio']} · "
                f"{business['today']}"
            )
    lines.append(f"-# {state['jornada']} · {state['tip']}")
    return "\n".join(lines)


def jornada_options(contract: Contract, now: float) -> list[discord.SelectOption]:
    """Opciones del menú de la jornada: los tramos y la excedencia."""
    if contract.on_excedencia:
        return [
            discord.SelectOption(
                label="Volver de la excedencia",
                value="x:off",
                emoji="🔙",
                description=(
                    f"Al mismo puesto y con la barra a la mitad (mínimo {EXCEDENCIA_MIN_DAYS} días)"
                ),
            )
        ]
    pending = contract.jornada_next or contract.jornada
    options = [
        discord.SelectOption(
            label=JORNADA_NAMES[franjas],
            value=f"j:{franjas}",
            emoji="🗓️" if franjas == ORDINARY_SHIFTS else "🧸",
            description=(
                f"{franjas} franjas de jornada"
                + (" · sin horas extra ni guardias" if franjas < ORDINARY_SHIFTS else "")
                + (" · la que tienes" if franjas == contract.jornada else " · desde mañana")
            )[:100],
            default=franjas == pending,
        )
        for franjas in JORNADAS
    ]
    blocker = excedencia_blocker(contract, now)
    options.append(
        discord.SelectOption(
            label="Pedir la excedencia voluntaria",
            value="x:on",
            emoji="🏝️",
            description=(
                "Deja libres todas tus franjas, sin sueldo"
                if blocker is None
                else "Aún no puedes pedirla"
            )[:100],
        )
    )
    return options


def job_lines(status: Status) -> list[str]:
    """Lo propio de cada oficio en el panel: tiempo, estrellas, trienios, seguidores."""
    contract, job = status.contract, status.job
    lines = []
    if status.weather:
        lines.append(WEATHER_TEXT[status.weather])
    if job.rates(contract.branch):
        line = f"📱 Valoración en la app: {stars(contract.rating)}"
        if status.blocked:
            line += f" · 📵 **desconectado** hasta <t:{int(contract.blocked_until)}:t>"
        lines.append(line + " · por debajo de 3,5 la app te desconecta")
    if job.earns_triennia(contract.level, contract.branch):
        tenured = job.tenured(contract.level, contract.branch)
        tenure = " · 🔒 plaza fija: no te baja nadie" if tenured else ""
        bonus = round(TRIENNIUM_PAY * status.triennia * 100)
        lines.append(f"📅 Trienios: {status.triennia} (+{bonus} % de sueldo){tenure}")
    if status.can_study:
        lines.append(
            f"📚 Temario acertado en este puesto: {contract.progress.get('temario', 0)}. "
            "Estudiar no paga, pero sin oposición no hay ascenso."
        )
    if job.viral:
        lines.append(f"👥 Seguidores: **{contract.followers:,}**".replace(",", "."))
    return lines


def branch_offer_text(status: Status) -> str:
    """La oferta de ascenso al final del tronco: una ficha por rama."""
    lines = [
        "## 📈 Te ofrecen el ascenso",
        "Se acabó el tronco: ahora eliges rama. Puedes cambiar más adelante, pero solo "
        "jubilándote de la que elijas.",
    ]
    for branch in status.fork:
        first, last = branch.positions[0], branch.positions[-1]
        weight = round(PENSION_WEIGHTS[branch.kind] * 100)
        lines.append(
            f"\n### {branch.emoji} {branch.name}\n{branch.blurb}\n"
            f"- Empiezas de **{first.title}** ({format_amount(first.base_pay)} por turno)\n"
            f"- Llegas a **{last.title}** ({format_amount(last.base_pay)} por turno)\n"
            f"- Al jubilarte, tu pensión suma el **{weight} %** de lo que le falte hasta el "
            f"tope ({format_amount(PENSION_CAP)} al día)"
        )
    return "\n".join(lines)


def outlook_text(outlook: dict[str, tuple[int, float] | None], job_key: str) -> str:
    """Lo que costaría cambiar a un oficio sin probar, para el menú (`""` si no se sabe)."""
    if job_key not in outlook:
        return ""
    found = outlook[job_key]
    if found is None:
        return "Ni arriba cobrarías lo de ahora (con más veteranía, sí)"
    days, gain = found
    top = f"luego, arriba, un {round(gain * 100)} % más" if gain > 0 else "arriba, lo mismo"
    if days == 0:
        return f"Cobras lo de ahora desde el primer día; {top}"
    return f"Unos {days} días fichando cobras menos; {top}"


def share_text(pension: Pension) -> str:
    """Cómo se cobra la pensión: entera, en jubilación activa o con el suelo de antes."""
    if pension.floor and pension.amount == pension.floor and pension.floor > pension.full:
        return (
            f"Cobras lo de antes de la 8.0.0 ({format_amount(pension.floor)}): nadie cobra "
            "menos que entonces."
        )
    if pension.share < 1:
        steps = ", ".join(f"{round(s * 100)} %" for s in ACTIVE_SHARES)
        return (
            f"Jubilación activa: mientras sigas fichando cobras el {round(pension.share * 100)} % "
            f"de {format_amount(pension.full)}; sube cada semana ({steps}) y, si lo dejas, entera."
        )
    return f"Pensión entera: {format_amount(pension.full)} al día."


def pension_text(slip: Payslip, pension: Pension) -> str:
    """El cobro de la pensión del día."""
    lines = ["## 🏖️ Pensión de hoy"]
    lines.extend(
        f"- {job.emoji} {branch.emoji} {branch.retired_as}" for job, branch in pension.branches
    )
    lines.append(f"Bruto: **{format_amount(slip.gross)}** · {share_text(pension)}")
    if slip.irpf:
        lines.append(
            f"IRPF: −{format_amount(slip.irpf)} (tu tipo esta semana: {_pct(slip.rates.irpf)})"
        )
    else:
        lines.append("IRPF: 0 Y$ (con tu renta de la semana aún no llegas al mínimo)")
    lines.append(f"## {CURRENCY_EMOJI} Neto: {format_amount(slip.net)}")
    lines.append(
        "-# Las pensiones son rendimientos del trabajo (art. 17.2.a LIRPF): IRPF sí, "
        f"cotizar ya no. {TAX_COLLECTOR} te sigue queriendo igual."
    )
    return "\n".join(lines)


def promotion_announcement(name: str, status: Status) -> str | None:
    """Anuncio de un ascenso, solo pasado el tronco (los primeros no se cuentan)."""
    job, contract, position = status.job, status.contract, status.position
    if contract.level <= job.trunk_top:
        return None
    where = where_text(status)
    if contract.level >= job.top:
        return f"🏆 **{name}** llega a lo más alto: **{position.title}** ({job.emoji} {where})."
    return f"📣 **{name}** asciende a **{position.title}** ({job.emoji} {where})."


#: Calidad de lo cotizado por debajo de la cual jubilarse de una rama tiene logro oculto.
LOW_QUALITY = 0.5


def jobs_completed(pension: Pension) -> int:
    """Oficios de los que se ha jubilado de todas las ramas."""
    jobs = {job.key: job for job, _branch in pension.branches}
    done = [job.key for job, _branch in pension.branches]
    return sum(1 for key, job in jobs.items() if done.count(key) >= len(job.branches))


def retire_stats(retirement: Retirement, pension: Pension) -> StatDelta:
    """Estadísticas de logros de una jubilación: ramas, oficios, veteranía y calidad."""
    delta = StatDelta(
        add={"work_retired": 1},
        peak={
            f"work_retired_{retirement.job.key}": len(retirement.status.retired),
            "work_jobs_done": jobs_completed(pension),
            "work_veteran_max": retirement.status.veteran,
        },
    )
    if retirement.quality < LOW_QUALITY:
        delta.add["work_retired_low_quality"] = 1
    return delta


def pension_stats(pension: Pension) -> StatDelta:
    """Estadísticas de logros de cobrar la pensión: cuánto del tope y si va entera."""
    whole = pension.share >= 1
    return StatDelta(
        add={"work_pensions": 1, "work_pension_whole" if whole else "work_pension_active": 1},
        peak={"work_pension_pct": pension.full * 100 // PENSION_CAP},
    )


def retirement_announcement(name: str, retirement: Retirement, pension: Pension) -> str:
    """Anuncio de una jubilación."""
    branch = retirement.branch
    text = (
        f"🏖️ **{name}** se jubila de {retirement.title} ({retirement.job.emoji} "
        f"{branch.emoji} {branch.name}). Ahora es {branch.retired_as}, con una pensión de "
        f"{format_amount(max(pension.full, pension.floor))} brutos al día."
    )
    following = retirement.following
    if following is not None:
        text += f" Y vuelve a la carga por {following.emoji} {following.name}."
    return text


def outcome_announcement(name: str, outcome: ShiftOutcome) -> str | None:
    """Anuncio de un turno con un hito (exit, quiebra o vídeo viral), o `None`."""
    if outcome.exit_payout:
        return (
            f"🚀 **¡EXIT!** La startup de **{name}** sale a bolsa: sus stock options valen "
            f"{format_amount(outcome.exit_payout)}."
        )
    if outcome.bankrupt:
        return f"💀 La startup de **{name}** quiebra. Sus stock options valen lo que el papel."
    if outcome.viral >= VIRAL_AT:
        return f"🔥 **¡VIRAL!** Un vídeo de **{name}** revienta: sueldo ×{decimal(outcome.viral)}."
    return None


def study_text(outcome: ShiftOutcome) -> str:
    """Resultado de un turno de estudio."""
    lines = [
        f"## 📚 Estudio de la oposición · {outcome.score}/100",
        f"Aciertas **{outcome.temario}** preguntas del temario. No cobras nada: "
        "opositar es invertir en ti (eso dice la academia).",
        f"{battery_bar(outcome.battery_after)} · "
        f"{family_change(outcome.family_before, outcome.family_after, outcome.family_parts)} · "
        f"Saldo: **{format_amount(outcome.balance)}**",
    ]
    if outcome.intervention:
        lines.append(
            "👪 **Intervención familiar.** Tu familia ya no te reconoce detrás del temario: "
            "mañana, ni extras, ni B, ni guardias."
        )
    if outcome.promotion_ready:
        lines.append("📈 **¡Te ofrecen el ascenso!** Vuelve al panel y pulsa 📈 Ascender.")
    return "\n".join(lines)


def outcome_text(outcome: ShiftOutcome) -> str:
    """Resultado de un turno."""
    if outcome.kind is ShiftKind.STUDY:
        return study_text(outcome)
    game = outcome.game
    where = " · 🏠 teletrabajo" if outcome.remote else " · 🇭🇰" if outcome.abroad else ""
    lines = [f"## ⛏️ Turno {KIND_TEXT[outcome.kind]}{where} · {outcome.score}/100"]
    if outcome.missed_guards:
        lines.append(
            f"📋 **Tu tutor te busca:** la semana pasada te faltaron {outcome.missed_guards} "
            "guardias obligatorias. La barra lo paga."
        )
    detail = f"{game.correct} aciertos"
    if game.mechanic is Mechanic.DIG and game.broken:
        detail += f" · 💥 {game.broken} roturas"
    if game.mechanic is Mechanic.MEMORY:
        detail = f"{game.perfect_rounds} rondas perfectas"
    if isinstance(game, PlanGame):
        detail = f"{game.correct} de {len(game.pieces)} piezas encajadas"
    if isinstance(game, DealGame):
        detail = "Tratos: " + " · ".join(
            f"{got} % de {deal.best} %" for got, deal in zip(game.got, game.deals, strict=False)
        )
        if game.walked:
            detail += f" · 🚪 {game.walked} se levantaron"
    if isinstance(game, AuditGame):
        detail = f"{game.clean_boards} de {game.boards} tableros limpios"
        if game.blown:
            detail += f" · {game.blown} malas abiertas"
    if isinstance(game, DeskGame):
        assert game.pack is not None
        detail = " · ".join(
            f"{name} {value}/{DESK_MAX}"
            for name, value in zip(game.pack.bars, game.bars, strict=True)
        )
        if game.crisis:
            detail += f" · 🚨 crisis en la decisión {game.turn}"
    if game.ordago == ORDAGO_WON:
        detail += " · 🎲 órdago ganado: extra doble"
    elif game.ordago == ORDAGO_LOST:
        detail += " · 🎲 órdago perdido: sin extra"
    lines.append(f"-# {detail}")
    if outcome.weather == "lluvia":
        lines.append(f"🌧️ Con lluvia, el turno paga ×{str(RAIN_PAY).replace('.', ',')}.")
    if outcome.triennia:
        lines.append(
            f"📅 Con tus {outcome.triennia} trienios cobras un "
            f"{round(TRIENNIUM_PAY * outcome.triennia * 100)} % más."
        )
    if outcome.wish is not None:
        lines.append(
            f"🌠 **¡Deseo del día cumplido!** {outcome.wish.text} Sueldo ×{decimal(WISH_PAY)} "
            f"y +{WISH_PERFORMANCE} de barra · racha de {outcome.wish_streak} días."
        )
    if outcome.viral >= VIRAL_AT:
        lines.append(f"## 🔥 ¡VIRAL! El vídeo revienta: sueldo ×{decimal(outcome.viral)}")
    elif outcome.viral:
        lines.append(f"-# 📉 El algoritmo te da ×{decimal(outcome.viral)} hoy.")
    if outcome.payslip is not None:
        lines.append(
            payslip_text(
                outcome.payslip,
                title=outcome.position.title,
                self_employed=outcome.position.self_employed,
                fee=outcome.position.fee,
            )
        )
    if outcome.foreign is not None:
        lines.append(foreign_payslip_text(outcome.foreign, title=outcome.position.title))
    if outcome.beckham:
        lines.append("-# ⚽ Ley Beckham: IRPF al 24 % fijo (art. 93 LIRPF).")
    if outcome.options_added:
        lines.append(
            f"🦄 +{format_amount(outcome.options_added)} en stock options (no se cobran). "
            f"Llevas {format_amount(outcome.options_total)} en papel."
        )
    if outcome.exit_payout:
        lines.append(f"## 🚀 ¡EXIT! Tus opciones valen {format_amount(outcome.exit_payout)}")
        lines.append(
            f"-# Exentas hasta {format_amount(OPTIONS_EXEMPT)} (art. 42.3.f LIRPF, Ley "
            "28/2022); el resto, con IRPF."
        )
    if outcome.bankrupt:
        lines.append("💀 **La startup quiebra.** Tus stock options valen lo que el papel.")
    if outcome.kind is ShiftKind.GUARD:
        lines.append(
            "🛌 Ahora estás **saliente**: 12 h sin fichar. La guardia pagó "
            "1,6 veces la base por el doble de trabajo: la hora sale más barata."
        )
    if outcome.black:
        lines.append(f"## 🤫 En B: +{format_amount(outcome.black)}")
        lines.append(f"-# Sin IRPF, sin cotizar y sin que lo vea el IMV. {TAX_COLLECTOR} llora.")
    if outcome.caught:
        lines.append(NIGHT_CAUGHT_TEXT if outcome.late_franja else CAUGHT_TEXT["inspeccion"])
        lines.append(f"-# Multa cobrada: {format_amount(outcome.fine)}.")
    if outcome.fee:
        lines.append(f"🧾 Cuota de autónomos de la semana: −{format_amount(outcome.fee)}.")
    if outcome.mom_repaid:
        lines.append(f"💸 Le devuelves {format_amount(outcome.mom_repaid)} a tu madre.")
    lines.append(
        f"{battery_bar(outcome.battery_after)} · "
        f"{family_change(outcome.family_before, outcome.family_after, outcome.family_parts)} · "
        f"Saldo: **{format_amount(outcome.balance)}**"
    )
    if outcome.rating:
        lines.append(f"📱 Valoración: {stars(outcome.rating)}")
    if outcome.blocked:
        lines.append(
            "📵 **La app te desconecta** por mala valoración: 6 horas sin pedidos. "
            "Vuelves con 4 estrellas."
        )
    if outcome.followers_gained or outcome.followers:
        lines.append(
            f"👥 +{outcome.followers_gained:,} seguidores · ahora {outcome.followers:,}".replace(
                ",", "."
            )
        )
    if outcome.accident and outcome.weather == "lluvia" and outcome.battery_before >= 20:
        lines.append(
            "🚑 **Te caes con la moto en un charco.** 24 h de baja. La mutua te paga "
            f"{format_amount(outcome.sick_pay)} brutos (el 75 % de tu sueldo diario medio)."
        )
    elif outcome.accident:
        lines.append(
            "🚑 **Accidente laboral.** Ibas reventado y pasó lo que tenía que pasar: 24 h de "
            f"baja. La mutua te paga {format_amount(outcome.sick_pay)} brutos (el 75 % de tu "
            "sueldo diario medio)."
        )
    if outcome.performance == "warned":
        lines.append("⚠️ **Tu jefe te ha llamado al despacho.** Si la barra vuelve a 0, te bajan.")
    elif outcome.performance == "demoted":
        lines.append("📉 **Te han bajado de puesto.** De vuelta a la pala.")
    if outcome.intervention:
        lines.append(
            "👪 **Intervención familiar.** Tu familia ya no te reconoce: mañana, ni extras, "
            "ni B, ni guardias."
        )
    if outcome.promotion_ready:
        lines.append("📈 **¡Te ofrecen el ascenso!** Vuelve al panel y pulsa 📈 Ascender.")
    return "\n".join(lines)


def favor_text(result: FavorResult) -> str:
    """Lo que pasa al pedirle un favor a la familia."""
    text = f"{result.favor.done} ❤️ {round(result.family)}/100."
    if result.amount:
        text += (
            f"\n{CURRENCY_EMOJI} +{format_amount(result.amount)} (Bizum exento, como entre "
            "colegas). Se lo devuelves con la mitad de cada turno."
        )
    return text


def event_result_text(result: EventResult) -> str:
    """Lo que pasa al elegir una opción de un evento."""
    lines = [result.text]
    if result.paid is not None:
        lines.append(
            f"{CURRENCY_EMOJI} +{format_amount(result.paid.net)} netos "
            f"({format_amount(result.paid.gross)} brutos, con sus impuestos)."
        )
    if result.black:
        lines.append(f"🤫 +{format_amount(result.black)} en negro.")
    if result.caught:
        lines.append(CAUGHT_TEXT[result.caught_by])
        if result.pardoned:
            lines.append(
                "🕊️ **Pero el Consejo de Ministros te indulta.** No pagas nada. "
                "La justicia es igual para todos, aunque no lo parezca."
            )
        else:
            lines.append(f"-# Multa cobrada: {format_amount(result.fine)}.")
    if result.demoted:
        lines.append("📉 Bajas un puesto.")
    return "\n".join(lines)


class PalaPanel(ui.LayoutView):
    """El panel de un miembro: su curro, el minijuego y los resultados.

    Solo lo toca su dueño. Guarda el turno en marcha; el dinero y el estado
    se guardan siempre a través de `WorkService`.
    """

    def __init__(self, cog: Work, *, guild_id: int, owner: discord.abc.User) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.service = cog.service
        self.guild_id = guild_id
        self.owner = owner
        self.message: discord.Message | None = None
        self.channel: object = None
        self.shift: Shift | None = None
        self.notes: list[str] = []
        self.pending_event: Event | None = None
        self._lock = TimedLock("work:PalaPanel._lock")
        self._timer: asyncio.Task[None] | None = None
        self._finishing = False
        #: Identifica el panel en los `custom_id` del minijuego.
        self.token = cog.new_token()
        #: Una edición del panel cada vez durante el minijuego.
        self._render_lock = TimedLock("work:PalaPanel._render_lock")
        #: Clics del minijuego recibidos; solo el último redibuja.
        self._ticket = 0
        #: Herramientas de curro que tenía al fichar (para los logros).
        self._tools_owned = 0
        #: La pensión, leída al abrir o refrescar el panel (botón 💰).
        self.pension: Pension | None = None
        #: Lo que costaría empezar cada oficio sin probar (`WorkService.switch_outlook`).
        self.outlook: dict[str, tuple[int, float] | None] = {}
        #: Regalos para la familia que tiene en la mochila (claves de la tienda).
        self.gifts: frozenset[str] = frozenset()
        #: Caja de la tienda del regalo que está a punto de comprar y dar.
        self.checkout: tienda.Checkout | None = None
        #: Si el mensaje enseña la imagen del inicio, y el cambio de adjuntos de la
        #: próxima edición (`None` = no tocarlos; `[]` = quitar la imagen).
        self.has_image = False
        #: La formación del puesto siguiente que enseña el panel (💳 Financiar sin leer la base).
        self._offered_training: Training | None = None
        self._attach: list[discord.File] | None = None

    # -- Dibujo -----------------------------------------------------------------------

    def _button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        custom_id: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        # Sin `custom_id`, el nombre del método (`_clock_in` -> `pala-panel:clock-in`);
        # los callbacks de cierre (`_choose`, `_dodge`...) lo pasan. No empieza por
        # `pala:`, que es del minijuego.
        if custom_id is None:
            custom_id = f"pala-panel:{callback.__name__.strip('_').replace('_', '-')}"
        button: ui.Button = ui.Button(
            label=label[:80], style=style, disabled=disabled, custom_id=custom_id
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    def _game_button(
        self,
        label: str,
        round_index: int,
        choice: int | str,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> ui.Button:
        """Botón del minijuego: lo atiende `Work.on_interaction` por su `custom_id`.

        Va marcado para que la telemetría no lo cuente al pasar por la vista, que
        lo rechaza sin contestar: lo cuenta el listener.
        """
        button: ui.Button = ui.Button(
            label=label[:80], style=style, custom_id=game_id(self.token, round_index, choice)
        )
        setattr(button, LISTENER_ITEM, True)
        return button

    def _frame(self, text: str, color: discord.Color, rows: list[ui.ActionRow]) -> None:
        if self.has_image:
            self._attach, self.has_image = [], False
        self.clear_items()
        container = ui.Container(accent_colour=color)
        container.add_item(ui.TextDisplay(text[:3900]))
        self.add_item(container)
        for row in rows:
            if row.children:
                self.add_item(row)

    def show_hiring(self, history: dict[str, Career], notes: list[str]) -> None:
        """Pantalla para elegir oficio (sin contrato)."""
        lines = [
            "## 🪏 Coge la pala",
            "Elige un curro. Cada uno tiene sus puestos, sus minijuegos y sus marrones. "
            "Puedes cambiar cuando quieras: si vuelves a un oficio, entras en el puesto "
            "en el que lo dejaste.",
            "",
        ]
        for job in JOBS:
            career = history.get(job.key, Career(1, 1))
            if career.level > job.trunk_top and job.branch(career.branch) is None:
                career = Career(job.trunk_top, career.max_level)
            position = job.position(career.level, career.branch)
            lines.append(
                f"{job.emoji} **{job.name}** · {job.blurb}\n-# Entrarías de "
                f"{position.title} ({format_amount(position.base_pay)} por turno)"
            )
        lines.extend(notes)
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._job_select(None))
        rows = [row]
        more = ui.ActionRow(self._button("🏠 Inicio", self._go_home_screen))
        if (button := self._pension_button()) is not None:
            more.add_item(button)
        rows.append(more)
        self._frame("\n".join(lines), COLOR_IDLE, rows)

    def _pension_button(self) -> ui.Button | None:
        """💰 Cobrar pensión, si la tiene y aún no la ha cobrado hoy."""
        pension = self.pension
        if pension is None or not pension.due:
            return None
        return self._button(
            f"💰 Cobrar pensión · {format_amount(pension.amount)}",
            self._claim_pension,
            style=discord.ButtonStyle.success,
        )

    def _job_select(self, current: str | None) -> ui.Select:
        options = [
            discord.SelectOption(
                label=job.name,
                value=job.key,
                emoji=job.emoji,
                description=outlook_text(self.outlook, job.key) or job.blurb[:100],
            )
            for job in JOBS
            if job.key != current
        ]
        select: ui.Select = ui.Select(
            placeholder="💼 Cambiar de curro" if current else "💼 Elige tu curro",
            options=options,
            custom_id="pala-panel:curro",
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._hire(interaction, select.values[0])

        select.callback = callback  # type: ignore[method-assign]
        return select

    def _coffee_select(self, coffees_today: int) -> ui.Select:
        options = [
            discord.SelectOption(
                label=f"{coffee.name} · +{coffee.battery} 🔋",
                value=coffee.key,
                emoji=coffee.emoji,
                description=(
                    f"{format_amount(coffee.price + igic(coffee.price))} con IGIC"
                    + (" · ⚠️ temblores" if coffees_today >= MAX_COFFEES else "")
                ),
            )
            for coffee in COFFEES
        ]
        # Financiar un café (110-200 Y$) solo va en 3 cuotas: de eso va el chiste.
        options += [
            discord.SelectOption(
                label=f"{financiar.LABEL} un {coffee.name.lower()}",
                value=f"fin:{coffee.key}",
                emoji=coffee.emoji,
                description=f"{format_amount(coffee.price + igic(coffee.price))} con IGIC",
            )
            for coffee in COFFEES
            if credit.refusal(coffee.price + igic(coffee.price)) is None
        ]
        select: ui.Select = ui.Select(
            placeholder=f"☕ Máquina de café ({coffees_today}/{MAX_COFFEES} hoy)",
            options=options,
            custom_id="pala-panel:cafe",
        )

        async def callback(interaction: discord.Interaction) -> None:
            value = select.values[0]
            if value.startswith("fin:"):
                await self._finance_coffee(interaction, value[4:])
            else:
                await self._coffee(interaction, value)

        select.callback = callback  # type: ignore[method-assign]
        return select

    def _family_select(self, status: Status) -> ui.Select:
        select: ui.Select = ui.Select(
            placeholder="❤️ Familia: dar un regalo o pedir un favor",
            options=family_options(status.contract, self.gifts, status.now),
            custom_id="pala-panel:familia",
        )

        async def callback(interaction: discord.Interaction) -> None:
            kind, _, key = select.values[0].partition(":")
            if kind == "g":
                await self._give_gift(interaction, key)
            elif kind == "b":
                await self._buy_gift(interaction, key)
            else:
                await self._favor(interaction, key)

        select.callback = callback  # type: ignore[method-assign]
        return select

    def show_status(self, status: Status) -> None:
        """Panel de un contrato."""
        notes, self.notes = self.notes, []
        green = discord.ButtonStyle.success
        actions: ui.ActionRow = ui.ActionRow()
        blocked = status.sick or status.off_duty or status.blocked
        actions.add_item(self._button("⛏️ Fichar", self._clock_in, style=green, disabled=blocked))
        if status.can_study:
            actions.add_item(
                self._button(
                    "📚 Estudiar",
                    self._clock_in_study,
                    style=discord.ButtonStyle.primary,
                    disabled=status.sick,
                )
            )
        if status.can_guard:
            actions.add_item(
                self._button(
                    "🚑 Guardia",
                    self._clock_in_guard,
                    style=discord.ButtonStyle.danger,
                    disabled=blocked,
                )
            )
        if status.job.remote:
            actions.add_item(
                self._button("🏠 Teletrabajo", self._clock_in_remote, disabled=blocked)
            )
        if status.promotion_ready:
            actions.add_item(
                self._button("📈 Ascender", self._offer, style=discord.ButtonStyle.primary)
            )
        following = status.next_position
        self._offered_training = None
        if (
            following is not None
            and following.training is not None
            and following.training.key not in status.trainings
        ):
            self._offered_training = following.training
            price = following.training.price + igic(following.training.price)
            actions.add_item(self._button(f"🎓 Formación · {format_amount(price)}", self._training))
        more: ui.ActionRow = ui.ActionRow()
        more.add_item(self._button("📜 Vida laboral", self._career))
        if status.can_retire:
            more.add_item(
                self._button("🏖️ Jubilarse", self._retire_offer, style=discord.ButtonStyle.primary)
            )
        if (pension := self._pension_button()) is not None:
            more.add_item(pension)
        flight = format_amount(FLIGHT_PRICE)  # exento de IGIC: transporte internacional
        if status.can_go_abroad:
            more.add_item(self._button(f"🌏 Irse a Hong Kong · {flight}", self._go_abroad))
        elif status.contract.abroad:
            more.add_item(self._button(f"✈️ Volver a casa · {flight}", self._go_home))
        more.add_item(self._button("🎁 Regalos", self._gift_shop))
        more.add_item(self._button("🏠 Inicio", self._go_home_screen))
        finance_row: ui.ActionRow = ui.ActionRow()
        offered = self._offered_training
        if offered is not None and credit.refusal(offered.price + igic(offered.price)) is None:
            finance_row.add_item(
                self._button(
                    f"{financiar.LABEL} formación",
                    self._finance_training,
                    custom_id="pala-panel:financiar_formacion",
                )
            )
        if status.can_go_abroad and credit.refusal(FLIGHT_PRICE) is None:
            finance_row.add_item(
                self._button(
                    f"{financiar.LABEL} el vuelo",
                    self._finance_flight,
                    custom_id="pala-panel:financiar_vuelo",
                )
            )
        coffee_row: ui.ActionRow = ui.ActionRow()
        coffee_row.add_item(self._coffee_select(status.contract.coffees))
        family_row: ui.ActionRow = ui.ActionRow()
        family_row.add_item(self._family_select(status))
        jornada_row: ui.ActionRow = ui.ActionRow()
        jornada_row.add_item(self._jornada_select(status))
        rows = [actions, more, finance_row, coffee_row, family_row, jornada_row]
        if not status.contract.abroad:
            job_row: ui.ActionRow = ui.ActionRow()
            job_row.add_item(self._job_select(status.contract.job))
            rows.append(job_row)
        hints = []
        if offered is not None and (
            hint := financiar.teaser(offered.price + igic(offered.price), "pala")
        ):
            hints.append(f"-# 🎓 Formación: {hint}.")
        if status.can_go_abroad and (hint := financiar.teaser(FLIGHT_PRICE, "pala")):
            hints.append(f"-# ✈️ Vuelo a Hong Kong: {hint}.")
        cheapest = min(COFFEES, key=lambda c: c.price)
        if hint := financiar.teaser(cheapest.price + igic(cheapest.price), "cafe"):
            hints.append(f"-# ☕ Café: {hint}.")
        self._frame(status_text(status, [*notes, *hints]), COLOR_IDLE, rows)

    def _jornada_select(self, status: Status) -> ui.Select:
        contract = status.contract
        label = "Excedencia" if contract.on_excedencia else JORNADA_NAMES[contract.jornada]
        select: ui.Select = ui.Select(
            placeholder=f"🗓️ Jornada: {label}",
            options=jornada_options(contract, status.now),
            custom_id="pala-panel:jornada",
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._jornada(interaction, select.values[0])

        select.callback = callback  # type: ignore[method-assign]
        return select

    def show_home(self, state: dict[str, Any], png: bytes | None) -> None:
        """El inicio: la imagen del día y los botones del curro y del negocio."""
        self.clear_items()
        container = ui.Container(accent_colour=COLOR_IDLE)
        if png is not None:
            container.add_item(
                ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{HOME_PNG}"))
            )
            self._attach, self.has_image = [discord.File(io.BytesIO(png), filename=HOME_PNG)], True
        elif self.has_image:
            self._attach, self.has_image = [], False
        notes, self.notes = self.notes, []
        text = home_text(state, image=png is not None)
        container.add_item(ui.TextDisplay("\n".join([text, *notes])[:3900]))
        self.add_item(container)
        blue = discord.ButtonStyle.primary
        row: ui.ActionRow = ui.ActionRow()
        if state["job"] is not None:
            row.add_item(
                self._button("🪏 Trabajo", self._home, custom_id="pala-panel:trabajo", style=blue)
            )
        if state["business"] is not None:
            row.add_item(
                self._button(
                    "🏪 Negocio", self._business, custom_id="pala-panel:negocio", style=blue
                )
            )
        if state["job"] is None:
            row.add_item(self._button("💼 Buscar curro", self._home, custom_id="pala-panel:buscar"))
        if state["business"] is None:
            row.add_item(
                self._button("🏪 Montar negocio", self._business, custom_id="pala-panel:montar")
            )
        if (pension := self._pension_button()) is not None:
            row.add_item(pension)
        self.add_item(row)

    def show_game(self) -> None:
        """Pantalla del minijuego en marcha.

        Arriba, cómo se juega, las herramientas y el reloj; luego lo que ha
        pasado con la última pulsación (bien visible, para que un fallo no
        pase desapercibido) y la ronda en juego.
        """
        shift = self.shift
        assert shift is not None
        game = shift.game
        current = game.current
        head = [f"### {shift.job.emoji} {shift.position.title} · turno {KIND_TEXT[shift.kind]}"]
        head.append(f"-# 🎮 {HOW_TO[game.mechanic]}")
        if shift.perks.tools:
            spent = shift.perks.retries and not game.retries
            head.append(
                f"-# 🛠️ {shift.perks.summary}" + (" (fallo gratis gastado)" if spent else "")
            )
        if game.header:
            head.append(game.header)
        if shift.tremors:
            head.append("☕☕☕☕ *Te tiemblan las manos: los botones bailan.*")
        head.append(f"⏱️ Se acaba <t:{int(game.deadline) + 1}:R>")
        rows: list[ui.ActionRow] = []
        if current is None:
            self._frame("\n".join(head), COLOR_GAME, [])
            return
        if game.showing:
            if game.last:
                head.append(f"**Ronda anterior:** {game.last}")
            head.append(current.reveal or "")
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(
                self._game_button(
                    "✅ Memorizado", game.index, HIDE_CHOICE, style=discord.ButtonStyle.primary
                )
            )
            self._frame("\n".join(head), COLOR_GAME, [row])
            return
        if game.last:
            head.append(f"**{game.last}**")
        body = current.prompt
        if game.mechanic is Mechanic.MEMORY:
            done = [current.options[i].split(" ", 1)[0] for i in current.answer[: game.step]]
            todo = ["❔"] * (len(current.answer) - game.step)
            body += f"\nLlevas: {' → '.join(done + todo)} ({game.step}/{len(current.answer)})"
        indices = list(range(len(current.options)))
        # En la Auditoría el sitio de cada casilla es el juego: ahí no bailan.
        if shift.tremors and game.mechanic is not Mechanic.AUDIT:
            self.service.rng.shuffle(indices)
        row = ui.ActionRow()
        for i in indices:
            if len(row.children) == 5:
                rows.append(row)
                row = ui.ActionRow()
            row.add_item(self._game_button(current.options[i], game.index, i))
        rows.append(row)
        self._frame("\n".join([*head, body]), COLOR_GAME, rows)

    def show_outcome(self, outcome: ShiftOutcome, extra: str | None = None) -> None:
        """Resultado del turno, con el evento si sale."""
        text = outcome_text(outcome)
        if extra:
            text += "\n" + extra
        self.pending_event = outcome.event
        self._show_after(text, outcome.event)

    def _show_after(self, text: str, event: Event | None) -> None:
        row: ui.ActionRow = ui.ActionRow()
        if event is not None:
            text += f"\n\n### 🎲 Marrón\n{event.text}"
            for index, (label, _outcome) in enumerate(event.options):
                row.add_item(
                    self._button(
                        label,
                        self._choose(event.key, index),
                        custom_id=f"pala-panel:elegir:{event.key}:{index}",
                        style=discord.ButtonStyle.primary,
                    )
                )
            if event.key in EVENT_KEYS:
                excuse = FAVOR_BY_KEY["excusa"]
                row.add_item(
                    self._button(
                        f"{excuse.emoji} Me llama mi madre (−{excuse.cost} ❤️)",
                        self._dodge(event.key),
                        custom_id=f"pala-panel:excusa:{event.key}",
                    )
                )
        else:
            row.add_item(
                self._button("⛏️ Otro turno", self._clock_in, style=discord.ButtonStyle.success)
            )
            row.add_item(self._button("🏠 Panel", self._home))
        self._frame(text, COLOR_IDLE, [row])

    def disable_all(self) -> None:
        """Apaga todos los controles (panel caducado)."""
        for item in self.walk_children():
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño toca su pala. Los botones del minijuego van por el listener."""
        custom_id = (interaction.data or {}).get("custom_id")
        if isinstance(custom_id, str) and parse_game_id(custom_id) is not None:
            return False
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta pala es de {self.owner.display_name}. Coge la tuya con `pala`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había turno a medias, se cierra y se cobra."""
        if self.shift is not None:
            await self._finish(None)
        self.cog.panels.discard(self)
        self.disable_all()
        await self._edit(None)

    async def _edit(self, interaction: discord.Interaction | None) -> None:
        kwargs: dict[str, Any] = {"view": self}
        if self._attach is not None:
            # Poner o quitar la imagen del inicio: solo cuando cambia (subirla cuesta ~1 s).
            kwargs["attachments"], self._attach = self._attach, None
        try:
            if interaction is not None:
                await edit(interaction, **kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar el panel de pala", exc_info=True)

    async def home_state(self) -> dict[str, Any]:
        """El estado de la escena del inicio con lo de ahora."""
        status = await self.service.status(self.guild_id, self.owner.id)
        self.pension = await self.service.pension(self.guild_id, self.owner.id)
        locales = await negocio.chain_of(self.cog.bot, self.guild_id, self.owner.id)
        business = locales[0] if locales else None
        agenda = (
            status.agenda
            if status is not None
            else await self.service.agenda(self.guild_id, self.owner.id)
        )
        battery = (
            await self.service.battery(self.guild_id, self.owner.id)
            if status is None and business is not None
            else None
        )
        return dia_state(self._name, self.service.now(), agenda, status, business, battery, locales)

    async def show_home_screen(self, interaction: discord.Interaction | None) -> None:
        """Pinta el inicio y lo pone en el mensaje."""
        state = await self.home_state()
        png = await negocio.paint(self.cog.bot, state)
        self.show_home(state, png)
        await self._edit(interaction)

    async def refresh(self, interaction: discord.Interaction | None = None) -> None:
        """Vuelve al panel con el estado actual."""
        self.pension = await self.service.pension(self.guild_id, self.owner.id)
        owned = await tienda.owned_keys(self.cog.bot, self.guild_id, self.owner.id)
        self.gifts = frozenset(owned & GIFT_KEYS)
        status = await self.service.status(self.guild_id, self.owner.id)
        if status is None:
            history = await self.service.history(self.guild_id, self.owner.id)
            notes, self.notes = self.notes, []
            self.show_hiring(history, notes)
        else:
            self.outlook = await self.service.switch_outlook(self.guild_id, self.owner.id, status)
            self.show_status(status)
        await self._edit(interaction)

    # -- Acciones del panel -----------------------------------------------------------

    # Todas las acciones del panel leen o guardan el contrato en la base de datos:
    # aceptan el clic con `ack` antes de nada. Solo los clics del minijuego, que no
    # tocan la base de datos hasta el último, contestan directamente.

    async def _home(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        await self.refresh(interaction)

    async def _go_home_screen(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        await self.show_home_screen(interaction)

    async def _business(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.shift is not None:
            await notify(interaction, "Termina el turno primero.")
            return
        self.stop()
        self.cog.panels.discard(self)
        if not await negocio.open_panel(self.cog.bot, interaction, self.owner, self.channel):
            await notify(interaction, "Los negocios no están disponibles ahora mismo.")

    async def _jornada(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        kind, _, arg = value.partition(":")
        try:
            if kind == "j" and arg.isdigit():
                status = await self.service.set_jornada(self.guild_id, self.owner.id, int(arg))
                stat = f"work_jornada_{arg}"
                if status.contract.jornada_next:
                    self.notes.append(
                        f"🗓️ Pedido: {JORNADA_NAMES[status.contract.jornada_next].lower()} desde "
                        "mañana (art. 37.7 ET: con preaviso)."
                    )
                else:
                    self.notes.append("🗓️ Te quedas con la jornada que tienes.")
            elif value == "x:on":
                status = await self.service.request_excedencia(self.guild_id, self.owner.id)
                stat = "work_excedencia"
                self.notes.append(
                    "🏝️ **Excedencia voluntaria.** Tus franjas quedan libres; tu puesto te espera. "
                    "Sin sueldo, claro."
                )
            elif value == "x:off":
                status = await self.service.end_excedencia(self.guild_id, self.owner.id)
                stat = "work_excedencia_back"
                self.notes.append("🔙 Vuelves al curro. La barra empieza a la mitad: a ganársela.")
            else:
                return
        except WorkError as error:
            await notify(interaction, str(error))
            return
        self.show_status(status)
        await self._edit(interaction)
        await logros.track(
            self.cog.bot, self.guild_id, self.owner, self.channel, StatDelta(add={stat: 1})
        )

    async def _hire(self, interaction: discord.Interaction, job_key: str) -> None:
        await ack(interaction)
        had_job = await self.service.status(self.guild_id, self.owner.id) is not None
        try:
            status = await self.service.hire(self.guild_id, self.owner.id, job_key)
        except WorkError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"✍️ Firmas de **{status.position.title}** en {status.job.emoji} {status.job.name}. "
            "¡A currar!"
        )
        if status.veteran:
            self.notes.append(
                f"🎖️ Llegas con veteranía: +{round(status.veteran_bonus * 100)} % de sueldo y los "
                "ascensos piden menos días y tareas. Aquí no empieza nadie de cero dos veces."
            )
        self.outlook = await self.service.switch_outlook(self.guild_id, self.owner.id, status)
        self.show_status(status)
        await self._edit(interaction)
        hired = StatDelta(peak={"work_veteran_max": status.veteran})
        if had_job:
            hired.add["work_job_changes"] = 1
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, hired)

    async def _coffee(self, interaction: discord.Interaction, key: str) -> None:
        await ack(interaction)
        try:
            battery, coffees, _balance = await self.service.buy_coffee(
                self.guild_id, self.owner.id, key
            )
        except WorkError as error:
            await notify(interaction, str(error))
            return
        await self._coffee_done(interaction, interaction, key, battery, coffees)

    async def _coffee_done(
        self,
        interaction: discord.Interaction,
        via: discord.Interaction | None,
        key: str,
        battery: float,
        coffees: int,
    ) -> None:
        """Lo de después del café; `via` es el clic que repinta el panel (`None`: el mensaje)."""
        coffee = next(c for c in COFFEES if c.key == key)
        note = (
            f"{coffee.emoji} Te tomas un {coffee.name.lower()}: {battery_bar(battery)} "
            f"({format_amount(coffee.price + igic(coffee.price))}, IGIC incluido)."
        )
        if coffees > MAX_COFFEES:
            note += " Te tiemblan hasta las pestañas."
        self.notes.append(note)
        await self.refresh(via)
        await renta.remind(self.cog.bot, interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"work_coffees": 1}, peak={"work_coffees_day_max": coffees}),
        )

    async def _gift_shop(self, interaction: discord.Interaction) -> None:
        """Atajo al pasillo de la familia de la tienda, en privado."""
        await ack(interaction, new_message=True)
        if not await tienda.open_aisle(self.cog.bot, interaction, GIFT_AISLE):
            await edit(interaction, content="La tienda está cerrada ahora mismo.")

    async def _buy_gift(self, interaction: discord.Interaction, key: str) -> None:
        """Caja de un regalo que no tiene, en el propio panel: compra y lo da en el acto."""
        await ack(interaction)
        if key in await tienda.owned_keys(self.cog.bot, self.guild_id, self.owner.id):
            # Lo compró después de pintar el panel: se da sin cobrar otra vez.
            await self._give_gift(interaction, key)
            return
        gift = GIFT_BY_KEY[key]
        status = await self.service.status(self.guild_id, self.owner.id)
        if status is None:
            await self.refresh(interaction)
            return
        ready = family_ready_at(status.contract, key, gift.cooldown)
        if ready > status.now:
            # Se mira antes de cobrar: comprarlo para no poder darlo es tirar el dinero.
            await notify(
                interaction,
                f"{gift.emoji} Tu familia todavía está con el último. Otro <t:{int(ready)}:R>.",
            )
            return
        checkout = await tienda.prepare_checkout(self.cog.bot, interaction, key)
        if checkout is None:
            await notify(interaction, "La tienda está cerrada ahora mismo.")
            return
        self.checkout = checkout
        price = checkout.price
        lines = [f"## {gift.emoji} {gift.name} · +{gift.family} ❤️", tienda.ticket_preview(price)]
        lines.append(tienda.igic_line(price))
        missing = price.total - checkout.balance
        if checkout.failed:
            lines.append(checkout.result or "")
        elif missing > 0:
            lines.append(
                f"😬 Tienes {format_amount(checkout.balance)}: te faltan "
                f"**{format_amount(missing)}**."
            )
        else:
            lines.append(
                f"{CURRENCY_EMOJI} Tienes {format_amount(checkout.balance)} → te quedan "
                f"**{format_amount(checkout.balance - price.total)}**"
            )
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            self._button(
                f"💳 Comprar y dar · {format_amount(price.total)}",
                self._pay_gift,
                style=discord.ButtonStyle.success,
                disabled=checkout.failed or missing > 0,
            )
        )
        row.add_item(self._button("Cancelar", self._home))
        self._frame("\n".join(lines), COLOR_IDLE, [row])
        await self._edit(interaction)

    async def _pay_gift(self, interaction: discord.Interaction) -> None:
        # Se suelta antes de esperar a nada: un segundo clic no cobra dos veces.
        checkout, self.checkout = self.checkout, None
        await ack(interaction)
        if checkout is None:
            return
        paid = await tienda.pay_checkout(checkout, interaction)
        if paid is None:
            self.notes.append(checkout.result or "")
            await self.refresh(interaction)
            return
        self.notes.append(
            f"💳 Pagas {format_amount(checkout.price.total)} "
            f"({format_amount(checkout.price.tax)} de IGIC)."
        )
        await self._give_gift(interaction, checkout.item.catalog_key or "")
        await tienda.after_checkout(checkout, interaction, paid)

    async def _give_gift(self, interaction: discord.Interaction, key: str) -> None:
        await ack(interaction)

        async def consume() -> bool:
            return await tienda.consume_key(self.cog.bot, self.guild_id, self.owner.id, key)

        try:
            gift, family = await self.service.give_gift(self.guild_id, self.owner.id, key, consume)
        except WorkError as error:
            await notify(interaction, str(error))
            await self.refresh(interaction)
            return
        note = f"{gift.emoji} Le das {gift.name.lower()} a tu familia: ❤️ {round(family)}/100."
        if pet := await mascotas.cameo(
            self.cog.bot, self.guild_id, self.owner.id, Moment(PetEvent.WORK)
        ):
            note += f"\n{pet}"
        self.notes.append(note)
        await self.refresh(interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"work_gifts": 1, f"work_gift_{gift.key}": 1}),
        )

    async def _favor(self, interaction: discord.Interaction, key: str) -> None:
        await ack(interaction)
        try:
            result = await self.service.ask_favor(self.guild_id, self.owner.id, key)
        except WorkError as error:
            await notify(interaction, str(error))
            return
        note = favor_text(result)
        if result.amount:
            extra = await renta.hint(
                self.cog.bot, self.guild_id, self.owner.id, Moment(PetEvent.BIZUM)
            )
        else:
            extra = await mascotas.cameo(
                self.cog.bot, self.guild_id, self.owner.id, Moment(PetEvent.WORK)
            )
        if extra:
            note += f"\n{extra}"
        self.notes.append(note)
        await self.refresh(interaction)
        if result.amount:
            await renta.remind(self.cog.bot, interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"work_favors": 1, f"work_favor_{result.favor.key}": 1}),
        )

    def _dodge(self, event_key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if self.pending_event is None or self.pending_event.key != event_key:
                await ack(interaction)
                return
            await ack(interaction)
            try:
                result = await self.service.dodge_event(self.guild_id, self.owner.id, event_key)
            except WorkError as error:
                await notify(interaction, str(error))
                return
            self.pending_event = None
            self._show_after(favor_text(result), None)
            await self._edit(interaction)
            await logros.track(
                self.cog.bot,
                self.guild_id,
                self.owner,
                self.channel,
                StatDelta(add={"work_favors": 1, "work_favor_excusa": 1}),
            )

        return callback

    async def _training(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            name, base, tax, _balance = await self.service.buy_training(
                self.guild_id, self.owner.id
            )
        except WorkError as error:
            await notify(interaction, str(error))
            return
        await self._training_done(interaction, interaction, name, base, tax)

    async def _training_done(
        self,
        interaction: discord.Interaction,
        via: discord.Interaction | None,
        name: str,
        base: int,
        tax: int,
    ) -> None:
        self.notes.append(
            f"🎓 Consigues {name}: {format_amount(base)} + {format_amount(tax)} de IGIC."
        )
        await self.refresh(via)
        await renta.remind(self.cog.bot, interaction)

    # -- 💳 Financiar: la oferta va en un mensaje privado nuevo y el panel se repinta después --

    async def _finance(
        self,
        interaction: discord.Interaction,
        purchase: credit.Purchase,
        buy: financiar.Buy,
        after: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        deal = financiar.Deal(purchase, buy, after=after)
        if not await financiar.open_finance(self.cog.bot, interaction, deal):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def _finance_training(self, interaction: discord.Interaction) -> None:
        training = self._offered_training
        if training is None:
            await ack(interaction)
            await self.refresh(interaction)
            return
        done: list[tuple[str, int, int]] = []

        async def buy(_interaction: discord.Interaction) -> str | None:
            try:
                name, base, tax, _balance = await self.service.buy_training(
                    self.guild_id, self.owner.id
                )
            except WorkError as error:
                return str(error)
            done.append((name, base, tax))
            return None

        async def after(after_interaction: discord.Interaction) -> None:
            await self._training_done(after_interaction, None, *done[0])

        purchase = credit.Purchase(
            f"🎓 {training.name}", training.price + igic(training.price), "pala"
        )
        await self._finance(interaction, purchase, buy, after)

    async def _finance_flight(self, interaction: discord.Interaction) -> None:
        done: list[Status] = []

        async def buy(_interaction: discord.Interaction) -> str | None:
            try:
                done.append(await self.service.move_abroad(self.guild_id, self.owner.id))
            except WorkError as error:
                return str(error)
            return None

        async def after(after_interaction: discord.Interaction) -> None:
            await self._abroad_done(after_interaction, None, done[0])

        purchase = credit.Purchase("✈️ Vuelo a Hong Kong", FLIGHT_PRICE, "pala")
        await self._finance(interaction, purchase, buy, after)

    async def _finance_coffee(self, interaction: discord.Interaction, key: str) -> None:
        coffee = next((c for c in COFFEES if c.key == key), None)
        if coffee is None:
            await ack(interaction)
            return
        done: list[tuple[float, int]] = []

        async def buy(_interaction: discord.Interaction) -> str | None:
            try:
                battery, coffees, _balance = await self.service.buy_coffee(
                    self.guild_id, self.owner.id, key
                )
            except WorkError as error:
                return str(error)
            done.append((battery, coffees))
            return None

        async def after(after_interaction: discord.Interaction) -> None:
            await self._coffee_done(after_interaction, None, key, *done[0])

        purchase = credit.Purchase(
            f"{coffee.emoji} {coffee.name}", coffee.price + igic(coffee.price), "cafe"
        )
        await self._finance(interaction, purchase, buy, after)

    async def _career(self, interaction: discord.Interaction) -> None:
        await ack(interaction, new_message=True)
        lines = [f"📜 **Vida laboral de {discord.utils.escape_markdown(self.owner.display_name)}**"]
        lines += await self.cog.career_lines(self.guild_id, self.owner.id)
        if len(lines) == 1:
            lines.append("Nada todavía. Ni unas prácticas.")
        await edit(interaction, content="\n".join(lines))

    async def _offer(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        status = await self.service.status(self.guild_id, self.owner.id)
        if status is not None and status.promotion_ready and status.fork:
            row: ui.ActionRow = ui.ActionRow()
            for branch in status.fork:
                row.add_item(
                    self._button(
                        f"{branch.emoji} {branch.name}",
                        self._accept_branch(branch.key),
                        custom_id=f"pala-panel:rama:{branch.key}",
                        style=discord.ButtonStyle.success,
                    )
                )
            row.add_item(self._button("🙅 Me quedo con la paguita", self._decline))
            self._frame(branch_offer_text(status), COLOR_IDLE, [row])
            await self._edit(interaction)
            return
        if status is None or not status.promotion_ready or status.next_position is None:
            await self.refresh(interaction)
            return
        following = status.next_position
        text = (
            f"## 📈 Te ofrecen el ascenso\n**{following.title}** · "
            f"{format_amount(following.base_pay)} por turno (ahora "
            f"{format_amount(status.position.base_pay)}).\n"
            "Cobrar más reduce tu IMV, pero cada Y$ de más solo te quita medio. "
            "¿Lo aceptas?" + (f"\n🏅 Desbloquea: {following.perk.text}" if following.perk else "")
        )
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._button("✅ Acepto", self._accept, style=discord.ButtonStyle.success))
        row.add_item(self._button("🙅 Me quedo con la paguita", self._decline))
        self._frame(text, COLOR_IDLE, [row])
        await self._edit(interaction)

    async def _accept(self, interaction: discord.Interaction, branch: str = "") -> None:
        await ack(interaction)
        try:
            status = await self.service.accept_promotion(self.guild_id, self.owner.id, branch)
        except WorkError as error:
            await notify(interaction, str(error))
            return
        note = (
            f"🎉 **¡Ascendido a {status.position.title}!** Ahora cobras "
            f"{format_amount(status.position.base_pay)} brutos por turno. ¡Toma mangó!"
        )
        if status.branch is not None and branch:
            note += f" Has elegido {status.branch.emoji} **{status.branch.name}**."
        if status.position.perk is not None:
            note += f"\n🏅 Nueva ventaja: {status.position.perk.text}"
        self.notes.append(note)
        self.show_status(status)
        await self._edit(interaction)
        if text := promotion_announcement(self._name, status):
            await self._announce(text)
        delta = StatDelta(add={"work_promotions": 1})
        if branch:
            delta.add["work_branch_chosen"] = 1
        delta.merge(await self.cog.top_stats(self.guild_id, self.owner.id))
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    def _accept_branch(self, branch: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._accept(interaction, branch)

        return callback

    # -- Jubilación y pensión ---------------------------------------------------------

    async def _retire_offer(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        status = await self.service.status(self.guild_id, self.owner.id)
        if status is None or not status.can_retire or status.branch is None:
            await self.refresh(interaction)
            return
        branch = status.branch
        others = [b for b in status.job.branches if b.key not in status.retired | {branch.key}]
        then = (
            f"Sigues en {status.job.name} por {others[0].emoji} {others[0].name}, desde "
            f"{others[0].positions[0].title}, con la barra a la mitad."
            if others
            else "Ya no te queda otra rama: te quedas donde estás, pero con pensión."
        )
        first = round(ACTIVE_SHARES[0] * 100)
        text = (
            f"## 🏖️ Jubilarse de {branch.emoji} {branch.name}\n"
            f"Dejas de ser {status.position.title} y pasas a ser {branch.retired_as}. "
            f"Tu pensión pasa a **{format_amount(status.pension_after)}** brutos al día, para "
            "siempre, con el botón 💰 del panel: crece con cada rama que completes y con lo "
            f"que cotizaste en ella. Mientras sigas fichando cobras una parte (el {first} % la "
            f"primera semana, entera desde la quinta).\n{then}\n-# No tiene vuelta atrás."
        )
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._button("✅ Me jubilo", self._retire, style=discord.ButtonStyle.success))
        row.add_item(self._button("🏠 Todavía no", self._home))
        self._frame(text, COLOR_IDLE, [row])
        await self._edit(interaction)

    async def _retire(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            retirement = await self.service.retire(self.guild_id, self.owner.id)
        except WorkError as error:
            await notify(interaction, str(error))
            return
        branch = retirement.branch
        self.notes.append(
            f"🏖️ **¡Te jubilas!** Ahora eres {branch.retired_as}. Pulsa 💰 cada día para "
            "cobrar la pensión."
        )
        self.pension = await self.service.pension(self.guild_id, self.owner.id)
        self.show_status(retirement.status)
        await self._edit(interaction)
        await self._announce(retirement_announcement(self._name, retirement, self.pension))
        delta = retire_stats(retirement, self.pension)
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    async def _claim_pension(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            slip, _balance = await self.service.claim_pension(self.guild_id, self.owner.id)
        except WorkError as error:
            await notify(interaction, str(error))
            return
        # La de este momento (la del panel puede ser de antes del último turno).
        pension = await self.service.pension(self.guild_id, self.owner.id)
        self.notes.append(pension_text(slip, pension))
        extra = await renta.hint(self.cog.bot, self.guild_id, self.owner.id, Moment(PetEvent.MONEY))
        if extra:
            self.notes.append(extra)
        await self.refresh(interaction)
        await renta.remind(self.cog.bot, interaction)
        delta = pension_stats(pension)
        if slip.irpf:
            delta.add["tax_paid"] = slip.irpf
            delta.add["work_pension_irpf"] = 1
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    # -- Anuncios ---------------------------------------------------------------------

    @property
    def _name(self) -> str:
        return discord.utils.escape_markdown(self.owner.display_name)

    async def _announce(self, text: str) -> None:
        """Cuenta un hito en el canal del panel, aparte y sin mencionar a nadie."""
        send = getattr(self.channel, "send", None)
        if send is None:
            return
        try:
            await send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un hito de pala", exc_info=True)

    async def _decline(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        await self.service.decline_promotion(self.guild_id, self.owner.id)
        self.notes.append("🪑 Rechazas el ascenso. Mañana te lo vuelven a ofrecer.")
        await self.refresh(interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"work_declined": 1}),
        )

    # -- Turno ------------------------------------------------------------------------

    async def _clock_in(
        self,
        interaction: discord.Interaction,
        *,
        black_ok: bool = False,
        guard: bool = False,
        remote: bool = False,
        study: bool = False,
    ) -> None:
        # Antes del candado: un segundo clic esperaría a que el primero acabe con la
        # base de datos sin haber contestado a Discord.
        await ack(interaction)
        async with self._lock:
            if self.shift is not None:
                return
            key = (self.guild_id, self.owner.id)
            if negocio.busy(self.cog.bot, *key):
                await notify(interaction, "Estás atendiendo tu negocio. Una cosa cada vez.")
                return
            if key in self.cog.working:
                # Dos paneles abiertos no pueden fichar a la vez: el segundo se
                # saltaría la jornada, la batería y la pregunta del B.
                await notify(interaction, "Ya estás fichando en otro panel. Una pala cada vez.")
                return
            owned = await tienda.owned_keys(self.cog.bot, self.guild_id, self.owner.id)
            try:
                shift = await self.service.start_shift(
                    self.guild_id,
                    self.owner.id,
                    black_ok=black_ok,
                    guard=guard,
                    remote=remote,
                    study=study,
                    tools=owned,
                )
            except NeedsBlack as error:
                row: ui.ActionRow = ui.ActionRow()
                row.add_item(
                    self._button(
                        "💶 Venga, en B", self._clock_in_black, style=discord.ButtonStyle.danger
                    )
                )
                row.add_item(self._button("🏠 Me voy a casa", self._home))
                self._frame(
                    f"## 🤫 Horas extra\n{error}\n{black_offer_text(error.late_franja)}",
                    COLOR_BAD,
                    [row],
                )
                await self._edit(interaction)
                return
            except OffDuty as error:
                await notify(interaction, str(error))
                await logros.track(
                    self.cog.bot,
                    self.guild_id,
                    self.owner,
                    self.channel,
                    StatDelta(add={"work_off_duty_tries": 1}),
                )
                return
            except WorkError as error:
                await notify(interaction, str(error))
                return
            self.shift = shift
            self._tools_owned = len(owned & TOOL_KEYS)
            self.cog.working.add(key)
            self._finishing = False
            self.show_game()
            await self._edit(interaction)
            if interaction.message is not None:
                self.message = interaction.message
            self._timer = asyncio.create_task(self._deadline(shift))
            self.cog.timers.add(self._timer)
            self._timer.add_done_callback(self.cog.timers.discard)

    async def _clock_in_black(self, interaction: discord.Interaction) -> None:
        await self._clock_in(interaction, black_ok=True)

    async def _clock_in_guard(self, interaction: discord.Interaction) -> None:
        await self._clock_in(interaction, guard=True)

    async def _clock_in_remote(self, interaction: discord.Interaction) -> None:
        await self._clock_in(interaction, remote=True)

    async def _clock_in_study(self, interaction: discord.Interaction) -> None:
        await self._clock_in(interaction, study=True)

    # -- Hong Kong --------------------------------------------------------------------

    async def _go_abroad(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            status = await self.service.move_abroad(self.guild_id, self.owner.id)
        except WorkError as error:
            await notify(interaction, str(error))
            return
        await self._abroad_done(interaction, interaction, status)

    async def _abroad_done(
        self, interaction: discord.Interaction, via: discord.Interaction | None, status: Status
    ) -> None:
        self.notes.append(
            "✈️ **¡Te vas a Hong Kong!** Doce horas de vuelo, un jet lag que te deja la "
            "batería tiritando y Robuso esperándote en el aeropuerto. Cobrarás el doble "
            "(paquete de expatriado), pero pagarás MPF y "
            "salaries tax, y no hay IMV mientras vivas fuera (art. 36.e de la Ley 19/2021). "
            "Pasados 4 días dejas de ser residente fiscal en España (183 días del año, "
            "art. 9.1.a LIRPF, a la escala del juego)."
        )
        self.show_status(status)
        await self._edit(via)
        await renta.remind(self.cog.bot, interaction)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"work_abroad": 1}),
        )

    async def _go_home(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            status, beckham, days = await self.service.come_home(self.guild_id, self.owner.id)
        except WorkError as error:
            await notify(interaction, str(error))
            return
        note = f"🏠 **Vuelves a casa** tras {decimal(days)} días en Hong Kong. Tu madre llora."
        if beckham:
            note += (
                " Y como has pasado fuera 5 «años», te toca la **Ley Beckham** (art. 93 LIRPF): "
                "durante 6 semanas tu IRPF es un 24 % fijo."
            )
        self.notes.append(note)
        self.show_status(status)
        await self._edit(interaction)
        await renta.remind(self.cog.bot, interaction)
        delta = StatDelta(add={"work_return": 1}, peak={"work_abroad_days_max": int(days)})
        if beckham:
            delta.add["work_beckham"] = 1
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    async def _deadline(self, shift: Shift) -> None:
        """Cierra el turno al acabarse el tiempo aunque nadie pulse nada."""
        # En memoria el plazo se mueve (el reloj se para al memorizar): se vuelve a mirar
        # cada segundo como mucho.
        while (delay := shift.game.deadline + GRACE_SECONDS - self.service.now()) > 0:
            await asyncio.sleep(min(delay, 1.0))
        if self.shift is shift:
            await self._finish(None)

    async def game_click(
        self, interaction: discord.Interaction, round_index: int, choice: int | str
    ) -> None:
        """Un clic en un botón del minijuego (llega por `Work.on_interaction`).

        La jugada se apunta en cuanto llega, en orden. Para redibujar, cada clic
        espera su turno y, si mientras tanto ha llegado otro, se limita a
        aceptar la interacción: el último pinta el estado final. Así una ráfaga
        de clics en la memoria no deja el panel en un estado viejo.
        """
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                f"Esta pala es de {self.owner.display_name}. Coge la tuya con `pala`.",
                ephemeral=True,
            )
            return
        shift = self.shift
        if shift is None or self._finishing:
            await ack(interaction)
            return
        game = shift.game
        if round_index != game.index:
            # Un clic que salió antes de ver la ronda nueva (doble clic, prisa).
            game.stale += 1
            await ack(interaction)
            return
        now = self.service.now()
        if choice == HIDE_CHOICE:
            game.hide(now)
        elif isinstance(choice, int):
            game.press(choice, now)
        if game.finished(now):
            await self._finish(interaction)
            return
        self._ticket += 1
        ticket = self._ticket
        if self._render_lock.locked():
            # Otro clic o el cierre del turno están editando el panel: se acepta ya, antes
            # de esperar el candado (regla 1 de «Botones rápidos», como en el negocio).
            await ack(interaction)
        async with self._render_lock:
            stale = ticket != self._ticket or self.shift is not shift
            if not stale:
                self.show_game()
                await self._edit(interaction)
        if stale:
            # Fuera del candado: si se aceptara dentro, una ráfaga de clics haría
            # cola para aceptarse uno detrás de otro y los últimos pasarían de 3 s.
            await ack(interaction)

    async def _finish(self, interaction: discord.Interaction | None) -> None:
        """Cobra el turno y enseña el resultado (una sola vez por turno)."""
        if self._finishing or self.shift is None:
            if interaction is not None:
                await ack(interaction)
            return
        self._finishing = True
        if interaction is not None:
            # La nómina (cotizaciones, IRPF, IMV) se calcula y se guarda: se acepta antes.
            await ack(interaction)
        shift = self.shift
        if self._timer is not None and asyncio.current_task() is not self._timer:
            self._timer.cancel()
        try:
            outcome = await self.service.finish_shift(self.guild_id, self.owner.id, shift)
        except WorkError as error:
            self.shift = None
            self.notes.append(str(error))
            await self.refresh(interaction)
            return
        finally:
            self.shift = None
            self.cog.working.discard((self.guild_id, self.owner.id))
        extra = await renta.hint(self.cog.bot, self.guild_id, self.owner.id, Moment(PetEvent.WORK))
        # Bajo el mismo candado que los clics: que una edición del minijuego que
        # aún va de camino no pise el resultado.
        async with self._render_lock:
            self.show_outcome(outcome, extra)
            await self._edit(interaction)
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        if text := outcome_announcement(self._name, outcome):
            await self._announce(text)
        delta = work_stats(
            outcome, birthday=await self.cog.is_birthday(self.guild_id, self.owner.id)
        )
        if self._tools_owned:
            delta.peak["work_tools_owned"] = self._tools_owned
        delta.merge(await self.cog.top_stats(self.guild_id, self.owner.id))
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)

    def _choose(
        self, event_key: str, option: int
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if self.pending_event is None or self.pending_event.key != event_key:
                await ack(interaction)
                return
            self.pending_event = None
            await ack(interaction)
            try:
                result = await self.service.resolve_event(
                    self.guild_id, self.owner.id, event_key, option
                )
            except WorkError as error:
                await notify(interaction, str(error))
                return

            self.pending_event = result.follow_up
            self._show_after(
                f"### 🎲 {result.event.options[option][0]}\n" + event_result_text(result),
                result.follow_up,
            )
            await self._edit(interaction)
            if result.paid is not None or result.black:
                await renta.remind(self.cog.bot, interaction)
            if result.stats:
                await logros.track(
                    self.cog.bot,
                    self.guild_id,
                    self.owner,
                    self.channel,
                    StatDelta(add=dict(result.stats)),
                )

        return callback


class Work(commands.Cog, name="Trabajo"):
    """`pala`: oficios, turnos con minijuego, nóminas y ascensos."""

    def __init__(self, bot: commands.Bot, service: WorkService) -> None:
        self.bot = bot
        self.service = service
        self.panels: set[PalaPanel] = set()
        #: Miembros con un turno en marcha (en cualquier panel).
        self.working: set[tuple[int, int]] = set()
        #: Temporizadores de turnos en marcha, para cancelarlos al descargar.
        self.timers: set[asyncio.Task[None]] = set()
        self._tokens = random.SystemRandom()
        claim_custom_ids(GAME_ID_PREFIX)

    def new_token(self) -> int:
        """Número de panel para los `custom_id` del minijuego.

        Al azar y no correlativo: un contador volvería a empezar en 1 tras
        reiniciar el bot y un botón de un panel viejo caería en uno nuevo.
        """
        taken = {panel.token for panel in self.panels}
        while (token := self._tokens.randrange(1, 10**9)) in taken:
            pass
        return token

    async def cog_unload(self) -> None:
        """Cierra los turnos a medias (se cobran) y cancela los temporizadores."""
        for panel in list(self.panels):
            if panel.shift is not None:
                with contextlib.suppress(Exception):
                    await panel._finish(None)
            panel.stop()
        self.panels.clear()
        for timer in list(self.timers):
            timer.cancel()

    async def is_birthday(self, guild_id: int, user_id: int) -> bool:
        """Si hoy (hora canaria) es el cumpleaños del miembro."""
        birthdays = getattr(self.bot, "birthdays", None)
        if birthdays is None:
            return False
        try:
            birthday = await birthdays.get_birthday(guild_id, user_id)
        except Exception:
            logger.exception("No se pudo consultar el cumpleaños de %s", user_id)
            return False
        if birthday is None:
            return False
        today = local_day(datetime.now(TIMEZONE).timestamp())
        return (birthday.day, birthday.month) == (today.day, today.month)

    async def top_stats(self, guild_id: int, user_id: int) -> StatDelta:
        """Máximos de la carrera: oficios coronados, lo más alto de cada uno y de cada rama."""
        history = await self.service.history(guild_id, user_id)
        delta = StatDelta(peak={})
        known = {job: career for job, career in history.items() if job in JOB_BY_KEY}
        tops = [job for job, career in known.items() if career.max_level >= JOB_BY_KEY[job].top]
        delta.peak["work_jobs_top"] = len(tops)
        delta.peak["work_jobs_tried"] = len(history)
        for job in tops:
            delta.peak[f"work_top_{job}"] = 1
        for job, career in known.items():
            # Solo se ve la rama en la que está: se apunta al llegar arriba.
            if career.branch and career.level >= JOB_BY_KEY[job].top:
                delta.peak[f"work_top_{job}_{career.branch}"] = 1
        return delta

    async def career_lines(self, guild_id: int, user_id: int) -> list[str]:
        """Vida laboral: un renglón por oficio con el puesto actual y el más alto,
        y otro por cada jubilación."""
        history = await self.service.history(guild_id, user_id)
        lines = []
        for job in JOBS:
            career = history.get(job.key)
            if career is None:
                continue
            branch = career.branch if job.branch(career.branch) else ""
            level = min(career.level, job.trunk_top) if not branch else career.level
            # shortcut: el máximo se nombra por la rama actual, porque el historial
            # no guarda en qué rama se llegó a él; si hace falta, guardarlo aparte.
            top = career.max_level if branch else min(career.max_level, job.trunk_top)
            lines.append(
                f"{job.emoji} {job.name}: {job.position(level, branch).title} "
                f"(máximo: {job.position(top, branch).title})"
            )
        pension = await self.service.pension(guild_id, user_id)
        lines.extend(
            f"🏖️ Jubilado de {job.emoji} {branch.emoji} {branch.name}: {branch.retired_as}"
            for job, branch in pension.branches
        )
        if pension.branches:
            lines.append(
                f"💰 Pensión: {format_amount(pension.amount)} al día. {share_text(pension)}"
            )
        return lines

    async def job_line(self, guild_id: int, user_id: int) -> str:
        """El curro de alguien en un renglón, para el resumen de `perfil`."""
        status = await self.service.status(guild_id, user_id)
        if status is None:
            return "En el paro. Ni unas prácticas."
        job, position = status.job, status.position
        where = " · en Hong Kong 🇭🇰" if status.contract.abroad else ""
        level = f"{status.contract.level}/{job.top}"
        return f"{job.emoji} {position.title} · {where_text(status)} ({level}){where}"

    async def work_embed(self, guild_id: int, member: discord.abc.User) -> discord.Embed:
        """Sección 🪏 Trabajo de `perfil`: contrato actual y vida laboral. No ficha nada."""
        name = discord.utils.escape_markdown(member.display_name)
        status = await self.service.status(guild_id, member.id)
        career = await self.career_lines(guild_id, member.id)
        text = status_text(status, []) if status is not None else "## 🪏 En el paro"
        embed = discord.Embed(title=f"🪏 Trabajo de {name}", description=text, color=COLOR_IDLE)
        embed.add_field(
            name="📜 Vida laboral",
            value="\n".join(career)[:1_024] if career else "Nada todavía. Ni unas prácticas.",
            inline=False,
        )
        if line := await negocio.business_line(self.bot, guild_id, member.id):
            embed.add_field(name="🏪 Negocio", value=line[:1_024], inline=False)
        embed.set_footer(text="Para fichar, ascender o cambiar de curro: pala.")
        return embed

    async def channel_error(self, guild: discord.Guild, channel: object) -> str | None:
        """Mensaje si `pala` no se puede usar en este canal, o `None`."""
        return wrong_channel(await self.service.channels(guild.id), channel, "La pala", "🪏")

    async def _open(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/pala` y `.pala`: abre el panel."""
        if guild is None:
            await send_error("La pala solo se coge dentro de un servidor.")
            return
        if error := await self.channel_error(guild, channel):
            await send_error(error)
            return
        panel = PalaPanel(self, guild_id=guild.id, owner=user)
        panel.channel = channel
        state = await panel.home_state()
        png = await negocio.paint(self.bot, state)
        panel.show_home(state, png)
        files, panel._attach = panel._attach or [], None
        panel.message = await send(
            view=panel, files=files, allowed_mentions=discord.AllowedMentions.none()
        )
        self.panels.add(panel)

    @app_commands.command(name="pala", description="Coge la pala: tu curro, turnos y ascensos.")
    @app_commands.guild_only()
    async def pala(self, interaction: discord.Interaction) -> None:
        """Abre el panel de tu curro. Solo en los canales elegidos con `tajo`, si los hay."""

        async def send(**kwargs: Any) -> discord.Message:
            return await interaction.followup.send(wait=True, **kwargs)

        # Pintar el inicio puede pasar del segundo: se acepta antes.
        await interaction.response.defer()

        async def send_error(text: str) -> None:
            await interaction.followup.send(text, ephemeral=True)

        await self._open(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            send=send,
            send_error=send_error,
        )

    @commands.command(name="pala")
    @commands.guild_only()
    async def pala_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.pala`) de `/pala`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._open(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Atiende los botones del minijuego (ver la docstring del módulo)."""
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id")
        if not isinstance(custom_id, str) or (parsed := parse_game_id(custom_id)) is None:
            return
        token, round_index, choice = parsed
        try:
            panel = next((p for p in self.panels if p.token == token), None)
            if panel is None:
                await interaction.response.send_message(
                    "Este panel de la pala ya caducó. Abre otro con `pala`.", ephemeral=True
                )
                return
            await panel.game_click(interaction, round_index, choice)
        finally:
            # La vista no cuenta estos botones (`LISTENER_ITEM`): se cuentan aquí,
            # ya contestados, con el mismo nombre en cada clic.
            telemetry = self.bot.telemetry
            telemetry.count(
                "button",
                "PalaPanel.game_click",
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                ms=telemetry.take_answer(interaction.id),
                error=not interaction.response.is_done(),
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra el trabajo del servidor cuando el bot deja de pertenecer a él."""
        await self.service.repository.delete_guild_data(guild.id)


# -- Funciones puente ---------------------------------------------------------------


async def parents_aval(bot: commands.Bot, guild_id: int, user_id: int) -> bool:
    """Si tus padres te avalan ahora (favor `aval` en vigor y sin gastar), o `False`."""
    cog = find_cog(bot, Work)
    if cog is None:
        return False
    try:
        return await cog.service.parents_aval(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo mirar el aval de %s", user_id)
        return False


async def spend_parents_aval(bot: commands.Bot, guild_id: int, user_id: int) -> None:
    """Gasta el aval de tus padres tras avalar una hipoteca o un alquiler."""
    cog = find_cog(bot, Work)
    if cog is None:
        return
    try:
        await cog.service.spend_parents_aval(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo gastar el aval de %s", user_id)


async def parents_lose_home(bot: commands.Bot, guild_id: int, user_id: int) -> None:
    """Baja la familia de quien pierde en una ejecución el piso que avalaron sus padres."""
    cog = find_cog(bot, Work)
    if cog is None:
        return
    try:
        await cog.service.parents_lose_home(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo bajar la familia de %s", user_id)


async def ladrillo_builders(bot: commands.Bot, guild_id: int) -> tuple[int, ...]:
    """Quienes fichan en la rama Ladrillo (levantan la obra nueva de `pisos`), o `()`."""
    cog = find_cog(bot, Work)
    if cog is None:
        return ()
    try:
        return await cog.service.ladrillo_builders(guild_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo mirar quién está en la rama Ladrillo")
        return ()


async def ladrillo_level(bot: commands.Bot, guild_id: int, user_id: int) -> int:
    """El puesto de alguien en la rama Ladrillo (construye en los solares de `pisos`), o 0."""
    cog = find_cog(bot, Work)
    if cog is None:
        return 0
    try:
        return await cog.service.ladrillo_level(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo mirar el puesto en la rama Ladrillo")
        return 0


async def home(
    bot: commands.Bot, interaction: discord.Interaction, owner: discord.abc.User, channel: object
) -> None:
    """Pone el inicio de `pala` en el mensaje del botón (ya aceptado con `ack`).

    Lo usa el panel del negocio para volver. El mensaje trae la imagen del negocio, así
    que el inicio la cambia siempre por la suya (o la quita).
    """
    cog = find_cog(bot, Work)
    if cog is None or interaction.guild_id is None:
        return
    panel = PalaPanel(cog, guild_id=interaction.guild_id, owner=owner)
    panel.channel = channel
    panel.message = interaction.message
    panel.has_image = True
    cog.panels.add(panel)
    try:
        await panel.show_home_screen(interaction)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo abrir el inicio de pala de %s", owner.id)


def busy(bot: commands.Bot, guild_id: int, user_id: int) -> bool:
    """Si el miembro está fichando en la pala ahora mismo (solo memoria)."""
    cog = find_cog(bot, Work)
    return cog is not None and (guild_id, user_id) in cog.working


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Lo pendiente del trabajo (para `saldo`): la pensión de hoy, la cuota y lo de mamá.

    Devuelve `[]` sin el cog o si falla la lectura.
    """
    cog = find_cog(bot, Work)
    if cog is None:
        return []
    try:
        pension = await cog.service.pension(guild_id, user_id)
        fee, mom = await cog.service.dues(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer lo pendiente del trabajo de %s", user_id)
        return []
    items = []
    if pension.due:
        items.append(
            Pending(
                Side.COLLECT, "pension", "👴",
                f"Pensión de hoy: {format_amount(pension.amount)} brutos · se cobra en `pala` "
                "y no se acumula",
                pension.amount, next_daily_at(cog.service.now()),
            )
        )  # fmt: skip
    if fee:
        items.append(
            Pending(
                Side.PAY, "self_employed_fee", "🧾",
                f"Cuota de autónomo de esta semana: {format_amount(fee)} · se cobra en tu "
                "primer turno",
                fee,
            )
        )  # fmt: skip
    if mom:
        items.append(
            Pending(
                Side.PAY, "mom_debt", "👩",
                f"Le debes {format_amount(mom)} a tu madre · se queda el "
                f"{round(MOM_REPAY_SHARE * 100)} % de lo que cobras en cada turno",
                mom,
            )
        )  # fmt: skip
    return items


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el servicio de trabajo del bot."""
    await bot.add_cog(Work(bot, bot.work))
