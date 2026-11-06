"""Casa: `casa [miembro]`, el Confort de la vivienda habitual y lo que da.

Solo lee: la casa sale de `HousingRepository.home_status` (la vivienda habitual de `pisos`,
con su Confort de `bot.services.home`). El panel cuenta dónde vive el miembro, su Confort
pilar a pilar, los bufos de su tramo en `pala`, lo que le falta para el siguiente, los
suministros de esta noche y si se los cortaron. También es la sección 🏡 Casa de `perfil`
(`Casa.home_embed`). No mueve dinero y no tiene botones.

Logros: `home_panel_stats` (mirar la casa propia o la de otro, y de madrugada). La mascota
activa comenta al mirar la casa propia.

Permisos: enviar mensajes e insertar enlaces.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.services import home as casa
from bot.services.achievements import home_panel_stats
from bot.services.economy import format_amount
from bot.services.levels import TIMEZONE, local_day
from bot.services.pets import Event, Moment
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.housing import HomeStatus, HousingRepository

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(214, 158, 46)
_BAR_WIDTH = 10
_KIND_NAMES = {casa.OWN: "Tu casa", casa.VPO: "Tu VPO", casa.RENTED: "De alquiler"}


def _num(value: float) -> str:
    """Un decimal a la española: 1,25."""
    return f"{value:g}".replace(".", ",")


def _bar(total: int) -> str:
    full = round(_BAR_WIDTH * total / 100)
    return "▰" * full + "▱" * (_BAR_WIDTH - full)


def _hint(comfort: casa.Comfort) -> str:
    """Qué mejorar: el pilar más lejos de su máximo (el equipamiento llega en la fase 2)."""
    gaps = {
        "space": casa.SPACE_POINTS - comfort.space,
        "quality": casa.QUALITY_POINTS - comfort.quality,
        "area": casa.AREA_POINTS - comfort.area,
    }
    worst = max(gaps, key=lambda k: gaps[k])
    if gaps[worst] <= 0:
        return "💡 Tu casa está al máximo en espacio, calidad y entorno."
    if worst == "space":
        return "💡 Lo que más te falta es espacio: una casa más grande sube el Confort."
    if worst == "quality":
        return (
            "💡 Lo que más te falta es calidad: las reformas de `pisos` suman puntos "
            "(☀️ Eficiencia energética sube el certificado a B) y no subir escaleras ayuda."
        )
    return "💡 Lo que más te falta es entorno: los barrios de lujo o de playa puntúan más."


def home_text(status: HomeStatus | None, today: date) -> list[tuple[str, str]]:
    """Los campos del panel como `(título, texto)`; sin casa, cómo emanciparse."""
    if status is None:
        return [
            (
                "🏠 Vives con tus padres",
                "Aquí no hay Confort ni suministros. Para emanciparte, usa «🏠 Vivir aquí» en "
                "una vivienda entera tuya de `pisos`, en la VPO, o alquila el piso de otro "
                "miembro.\nVivir en tu propia casa da bufos en `pala` y se pagan los "
                "suministros cada noche.",
            )
        ]
    place, comfort = status.place, status.comfort
    tier = comfort.tier
    fields = [
        (
            "📍 Dónde vives",
            f"**{place.name}** · {_KIND_NAMES.get(place.kind, place.kind)}\n"
            f"{place.m2} m² · certificado energético {place.energy}",
        ),
        (
            "🛋️ Confort",
            f"**{comfort.total}/100** {_bar(comfort.total)}\n{tier.emoji} **{tier.name}**\n"
            f"Espacio: {comfort.space}/{casa.SPACE_POINTS}\n"
            f"Calidad: {comfort.quality}/{casa.QUALITY_POINTS}\n"
            f"Equipamiento: {comfort.gear}/{casa.GEAR_POINTS} (llega con los muebles del hogar)\n"
            f"Entorno: {comfort.area}/{casa.AREA_POINTS}",
        ),
    ]
    nxt = comfort.next_tier
    upto = (
        f"Te faltan **{nxt.low - comfort.total}** puntos para {nxt.emoji} {nxt.name}."
        if nxt
        else "Es el tramo más alto."
    )
    wear = _num(round((1 - tier.strain) * 100, 1))
    fields.append(
        (
            "⚡ Lo que da en `pala`",
            f"Recarga de la batería ×{_num(tier.regen)}\n"
            f"La familia se recupera ×{_num(tier.family)}\n"
            f"Cada turno desgasta a la familia −{wear} %\n"
            f"{upto}",
        )
    )
    bill = status.bill
    bills = (
        f"💡 Luz: {format_amount(bill.power)} + {format_amount(bill.power_tax)} del Impuesto "
        f"sobre la Electricidad\n"
        f"🚿 Agua: {format_amount(bill.water)}\n"
        f"🌐 Fibra: {format_amount(bill.internet)} + {format_amount(bill.igic)} de IGIC\n"
        f"**Total: {format_amount(bill.total)}**"
    )
    if status.cut(today):
        bills += (
            "\n🔌 Te han cortado los suministros por no pagar: hasta que vuelvan, la casa no "
            "da bufos (todo como en casa de tus padres). Esta noche se te cobran otra vez."
        )
    fields.append(("🧾 Suministros de esta noche", bills))
    fields.append(("💡 Para subir el Confort", _hint(comfort).removeprefix("💡 ")))
    return fields


class Casa(commands.Cog):
    """El panel `casa` y la sección 🏡 Casa de `perfil`."""

    def __init__(self, bot: BotClient, repository: HousingRepository) -> None:
        self.bot = bot
        self.repository = repository

    async def home_embed(self, guild_id: int, member: discord.abc.User) -> discord.Embed:
        """El panel de la casa de `member` (también la sección de `perfil`)."""
        embed = discord.Embed(
            title=f"🏡 Casa de {discord.utils.escape_markdown(member.display_name)}", color=COLOR
        )
        try:
            status = await self.repository.home_status(guild_id, member.id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer la casa de %s", member.id)
            embed.description = "No he podido leer la casa ahora mismo. Inténtalo en un rato."
            return embed
        now = datetime.now(TIMEZONE)
        for name, value in home_text(status, local_day(now.timestamp())):
            embed.add_field(name=name, value=value, inline=False)
        return embed

    async def _casa_impl(self, responder: CommandResponder, member: discord.Member | None) -> None:
        guild, author = responder.guild, responder.member
        if guild is None or author is None:
            await responder.send_error("La casa solo se mira dentro de un servidor.")
            return
        target = member or author
        embed = await self.home_embed(guild.id, target)
        own = target.id == author.id
        if own and (pet := await mascotas.cameo(self.bot, guild.id, author.id, Moment(Event.HOME))):
            embed.set_footer(text=pet)
        await responder.finish(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        delta = home_panel_stats(own=own, hour=datetime.now(TIMEZONE).hour)
        logros.note(self.bot, guild.id, author.id, delta)

    @app_commands.command(
        name="casa", description="Tu casa: el Confort, lo que te da en pala y los suministros."
    )
    @app_commands.describe(miembro="De quién ver la casa (por defecto, la tuya)")
    @app_commands.guild_only()
    async def casa(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Panel de la casa."""
        await self._casa_impl(InteractionResponder(interaction), miembro)

    @commands.command(name="casa")
    @commands.guild_only()
    async def casa_text(self, ctx: commands.Context, miembro: discord.Member | None = None) -> None:
        """Versión de texto: `.casa` o `.casa @alguien`."""
        await self._casa_impl(ContextResponder(ctx), miembro)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el repositorio de pisos del bot."""
    await bot.add_cog(Casa(bot, repository=bot.housing))
