"""Sonidos de entrada: cada miembro sube audios cortos que suenan al entrar a voz.

Comando: `/entrada` (y `.entrada` en texto). Sin nada abre el panel 🔔 Tus
entradas, donde se hace todo:

- Hasta 7 ranuras de entrada (la primera gratis, el resto se compran) y una
  de salida, que suena al irte de voz (también se compra).
- Cada ranura tiene nombre, volumen, efecto (eco, robot, ardilla…), fundido y
  recorte: se sube un audio largo y se elige desde qué segundo empieza.
- Modo: siempre la misma, al azar o por turnos.
- 🛒 Mejoras: más ranuras, más duración (de 3 s a 5 s) y el sonido de salida.
- 🏆 Ranking: las entradas que más han sonado en el servidor, con su audio.

Las opciones del comando (`archivo`, `inicio`, `volumen`, `borrar`) siguen
valiendo como atajos sobre la ranura activa.

Comportamiento en voz, pensado para gastar lo mínimo en el NAS:

- Las fichas de todos están en memoria desde que carga el cog: entrar a voz no
  consulta la base de datos. Solo se lee del disco el clip que va a sonar, y los
  últimos se guardan ya troceados en paquetes (`CLIP_CACHE`).
- El bot NO se queda en el canal. Se conecta ensordecido (`self_deaf=True`,
  así Discord no le envía el audio de nadie), reproduce el clip y se va.
- Al reproducir no hay `ffmpeg`: el clip ya está en paquetes Opus y se manda tal
  cual. Efectos, recorte y volumen se aplican una sola vez, al guardar.
- Si varias personas entran seguidas, los sonidos se encolan y se reproducen
  aprovechando la misma conexión, con un tope de sonidos y de segundos.
- Si el bot ya está en voz por otra razón (la música), no suena: la música
  tiene prioridad.
- Cada persona dispara su entrada (y su salida) como mucho una vez cada
  `COOLDOWN_SECONDS`.

Requisitos: intent de estados de voz (incluido en `Intents.default()`) y los
permisos **Conectar** y **Hablar** en los canales de voz.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import banco_compras as financiar
from bot.cogs import pets as mascotas
from bot.cogs import renta
from bot.cogs.casino import insufficient_text
from bot.repositories.economy import InsufficientFundsError
from bot.repositories.entrance_sounds import EntranceSoundStore
from bot.services import credit
from bot.services.achievements import StatDelta
from bot.services.economy import format_amount
from bot.services.entrance import (
    DEFAULT_EFFECT,
    EFFECTS,
    EXIT_SLOT,
    MAX_SLOTS,
    MAX_TENTHS,
    MODE_LABELS,
    SOURCE_SECONDS,
    EntranceError,
    Mode,
    Profile,
    Slot,
    Upgrade,
    clean_name,
    effect_of,
    next_mode,
    next_offer,
    offer_label,
    parse_seconds,
    pick,
    seconds_text,
    total_spent,
    wave_text,
)
from bot.services.entrance_sound import (
    DEFAULT_VOLUME_PERCENT,
    MAX_UPLOAD_BYTES,
    MAX_VOLUME_PERCENT,
    MIN_VOLUME_PERCENT,
    PACKET_SECONDS,
    EntranceSoundError,
    build_raw_source,
    play_packets,
    probe_audio_duration,
    read_opus_packets,
    render_slot,
    validate_volume,
    wait_until_audible,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment
from bot.services.taxes import IGIC_DEFAULT, TAX_COLLECTOR, igic
from bot.utils.interactions import ack, edit
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

#: Tiempo mínimo entre dos entradas (o dos salidas) de la misma persona.
COOLDOWN_SECONDS = 30.0
#: Sonidos en espera por servidor. Si entra media lista de golpe, el resto se omite.
MAX_PENDING_PER_GUILD = 5
#: Segundos de sonido en espera por servidor: con clips de 5 s, cinco seguidos
#: serían 25 s de bot hablando.
MAX_PENDING_MS = 15_000
#: Límite de memoria del registro de enfriamientos (una entrada por persona).
MAX_COOLDOWN_ENTRIES = 1000
#: Clips troceados que se guardan en memoria (unos 60 KB cada uno a 5 s).
CLIP_CACHE = 64
CONNECT_TIMEOUT_SECONDS = 10.0
#: Pausa tras conectar (y tras tener listo el cifrado de voz, `wait_until_audible`)
#: antes de emitir. Sin ella Discord puede comerse el principio del clip.
PRE_ROLL_SECONDS = 0.3
#: Conversiones de audio simultáneas (cada una lanza un `ffmpeg`).
MAX_CONCURRENT_JOBS = 2
#: Tipos MIME que se aceptan cuando Discord informa uno. Si no lo informa,
#: decide `ffprobe`. Se admite vídeo porque un clip de vídeo con audio es
#: una forma habitual de compartir sonidos.
ACCEPTED_MIME_PREFIXES = ("audio/", "video/", "application/ogg")
#: El panel deja de responder a los 10 minutos (el token de Discord dura 15).
PANEL_TIMEOUT = 600
#: Sonidos del ranking.
RANKING_SIZE = 10
#: Veces que tiene que haber sonado el primero del ranking para que cuente su logro.
TOP_MIN_PLAYS = 10
COLOR = discord.Colour.from_rgb(240, 178, 50)
NO_MENTIONS = discord.AllowedMentions.none()

USAGE_TEXT = (
    "Abre el panel con `/entrada` (o `.entrada`). Atajos: `/entrada archivo:` sube un audio "
    "(con `inicio:` eliges desde qué segundo), `/entrada volumen:150` cambia el volumen de "
    "la ranura activa y `/entrada borrar:True` la vacía. En texto: `.entrada` con el audio "
    "adjunto, `.entrada 150` o `.entrada borrar`."
)

SAVED_LINES = (
    "✅ Guardado.",
    "✅ Listo: suena la próxima vez que entres a voz.",
    "✅ Guardado. Ya tienes con qué hacerte notar.",
    "✅ Guardado. Yo soy yo… y así te van a tener que oír.",
    "✅ Hecho. Quien entre detrás de ti, que se aguante.",
    "✅ Guardado. Más puntual que la guagua no será, pero sonar, suena.",
)
BOUGHT_LINES = (
    "🛒 Comprado.",
    "🛒 Comprado. Ya es tuyo.",
    "🛒 Hecho. Más sitio para hacer ruido.",
    "🛒 Comprado. ¿Qué? ¿Qué? ¡Toma mejora!",
)


def _is_accepted_attachment(attachment: discord.Attachment) -> bool:
    """Filtro rápido por tipo MIME antes de descargar nada."""
    content_type = attachment.content_type
    return content_type is None or content_type.startswith(ACCEPTED_MIME_PREFIXES)


def parse_text_option(option: str | None) -> tuple[int | None, bool]:
    """Interpreta el argumento de `.entrada`: un volumen, `borrar` o nada.

    Returns:
        `(volumen, borrar)`.

    Raises:
        EntranceSoundError: Si el argumento no es ni un número ni `borrar`.
    """
    if option is None:
        return None, False
    cleaned = option.strip().lower().rstrip("%")
    if cleaned == "borrar":
        return None, True
    if cleaned.isdigit():
        return validate_volume(int(cleaned)), False
    raise EntranceSoundError(USAGE_TEXT)


def slot_title(sound: Slot | None, slot: int) -> str:
    """«1 · Bocina» o «🚪 Salida · Portazo»; sin sonido, «2 · Vacía»."""
    head = "🚪 Salida" if slot == EXIT_SLOT else str(slot)
    return f"{head} · {sound.name if sound else 'Vacía'}"


def slot_summary(sound: Slot) -> str:
    """La línea pequeña de una ranura: onda, duración, volumen, efecto y veces."""
    effect = effect_of(sound.effect)
    parts = [f"`{sound.wave}`" if sound.wave else "", seconds_text(sound.clip_ms)]
    parts.append(f"🔊 {sound.volume} %")
    if effect is not DEFAULT_EFFECT:
        parts.append(f"{effect.emoji} {effect.label}")
    if sound.fade:
        parts.append("🌫️ Fundido")
    times = "vez" if sound.plays == 1 else "veces"
    parts.append(f"sonó {sound.plays} {times}")
    return " · ".join(p for p in parts if p)


def tax_text(tax: int) -> str:
    """La mordida del IGIC de una mejora, en subtexto."""
    rate = round(IGIC_DEFAULT.rate * 100)
    return (
        f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(tax)} de IGIC "
        f"({IGIC_DEFAULT.label.lower()}, {rate} %, {IGIC_DEFAULT.law})."
    )


@dataclass(frozen=True, slots=True)
class Pending:
    """Un sonido en la cola de un servidor."""

    member_id: int
    channel_id: int
    slot: int
    ms: int


# -- Formularios -------------------------------------------------------------------------


def _effect_select(current: str) -> ui.Select:
    return ui.Select(
        options=[
            discord.SelectOption(
                label=e.label, value=e.key, emoji=e.emoji, default=e.key == current
            )
            for e in EFFECTS
        ],
        min_values=1,
        max_values=1,
        required=True,
    )


class UploadForm(ui.Modal):
    """📤 Subir un audio a una ranura: archivo, nombre, inicio, efecto y volumen."""

    def __init__(self, panel: EntrancePanel, slot: int) -> None:
        sound = panel.profile.sounds.get(slot)
        title = "🚪 Sonido de salida" if slot == EXIT_SLOT else f"📤 Ranura {slot}"
        super().__init__(title=title, timeout=PANEL_TIMEOUT)
        self.panel = panel
        self.slot = slot
        limit = seconds_text(panel.profile.max_ms)
        self.upload: ui.FileUpload = ui.FileUpload(min_values=1, max_values=1, required=True)
        self.add_item(
            ui.Label(
                text="Audio",
                description=(
                    f"mp3, ogg, wav, m4a o un vídeo, hasta {MAX_UPLOAD_BYTES // 2**20} MB. "
                    f"Suenan {limit}."
                ),
                component=self.upload,
            )
        )
        self.name: ui.TextInput = ui.TextInput(
            default=sound.name if sound else None,
            placeholder="Bocina, Fanfarria, Portazo…",
            max_length=32,
            required=False,
        )
        self.add_item(ui.Label(text="Nombre", component=self.name))
        self.start: ui.TextInput = ui.TextInput(
            placeholder="0, 12,5 o 1:05 (vacío: desde el principio)",
            max_length=10,
            required=False,
        )
        self.add_item(
            ui.Label(
                text="Empieza en el segundo",
                description="Para quedarte con un trozo de una canción o un vídeo largo.",
                component=self.start,
            )
        )
        self.effect = _effect_select(sound.effect if sound else DEFAULT_EFFECT.key)
        self.add_item(ui.Label(text="Efecto", component=self.effect))
        self.volume: ui.TextInput = ui.TextInput(
            default=str(sound.volume if sound else DEFAULT_VOLUME_PERCENT),
            placeholder=f"{MIN_VOLUME_PERCENT}-{MAX_VOLUME_PERCENT}",
            max_length=4,
            required=False,
        )
        self.add_item(ui.Label(text="Volumen (%)", component=self.volume))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.cog.form_upload(interaction, self)


class AdjustForm(ui.Modal):
    """✏️ Ajustar una ranura sin volver a subir el audio."""

    def __init__(self, panel: EntrancePanel, sound: Slot) -> None:
        super().__init__(title=f"✏️ {slot_title(sound, sound.slot)}"[:45], timeout=PANEL_TIMEOUT)
        self.panel = panel
        self.sound = sound
        source = seconds_text(sound.source_ms)
        self.name: ui.TextInput = ui.TextInput(default=sound.name, max_length=32, required=False)
        self.add_item(ui.Label(text="Nombre", component=self.name))
        self.start: ui.TextInput = ui.TextInput(
            default=f"{sound.start_ms / 1000:g}".replace(".", ","), max_length=10, required=False
        )
        self.add_item(
            ui.Label(
                text="Empieza en el segundo",
                description=f"Del audio guardado, que dura {source}.",
                component=self.start,
            )
        )
        self.length: ui.TextInput = ui.TextInput(
            default=f"{sound.length_ms / 1000:g}".replace(".", ","), max_length=6, required=False
        )
        self.add_item(
            ui.Label(
                text="Duración (s)",
                description=f"Hasta {seconds_text(panel.profile.max_ms)}.",
                component=self.length,
            )
        )
        self.effect = _effect_select(sound.effect)
        self.add_item(ui.Label(text="Efecto", component=self.effect))
        self.volume: ui.TextInput = ui.TextInput(
            default=str(sound.volume), max_length=4, required=False
        )
        self.add_item(ui.Label(text="Volumen (%)", component=self.volume))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.cog.form_adjust(interaction, self)


def _volume_from(text: str | None, current: int) -> int:
    """El volumen escrito en un formulario («150», «150 %»), o el que había."""
    if text is None or not text.strip():
        return current
    cleaned = text.strip().rstrip("%").strip()
    if not cleaned.isdigit():
        raise EntranceError(
            f"El volumen es un número entre {MIN_VOLUME_PERCENT} y {MAX_VOLUME_PERCENT}."
        )
    try:
        return validate_volume(int(cleaned))
    except EntranceSoundError as error:
        raise EntranceError(str(error)) from None


# -- Panel -----------------------------------------------------------------------------


class EntrancePanel(ui.LayoutView):
    """El panel 🔔 Tus entradas de un miembro.

    Todo lo que enseña sale de la ficha en memoria del cog, así que cambiar de
    página contesta al momento (`edit_message`). Solo lo que escribe (subir,
    ajustar, comprar, borrar) hace `ack` antes.

    Páginas: `main`, `slot` (con `slot` elegida), `shop` y `ranking`.
    """

    def __init__(self, cog: Entrance, guild: discord.Guild, member: discord.abc.User) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.page = "main"
        self.slot = 1
        #: Última línea de resultado, bajo la cabecera.
        self.note: str | None = None
        #: Botón que espera un segundo clic para confirmar (comprar, borrar).
        self.confirm: str | None = None
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None
        self.rebuild()

    @property
    def profile(self) -> Profile:
        return self.cog.profile(self.guild.id, self.member.id)

    # -- Montaje ----------------------------------------------------------------------

    def rebuild(self) -> None:
        self.clear_items()
        builder = {
            "main": self._main,
            "slot": self._slot_page,
            "shop": self._shop,
            "ranking": self._ranking,
        }[self.page]
        builder()

    def _header(self, container: ui.Container, title: str, subtitle: str) -> None:
        text = f"# {title}\n-# {subtitle}"
        if self.note:
            text += f"\n{self.note}"
        container.add_item(ui.TextDisplay(text))
        container.add_item(ui.Separator())

    def _main(self) -> None:
        profile = self.profile
        container = ui.Container(accent_colour=COLOR)
        filled = len(profile.entrances)
        self._header(
            container,
            "🔔 Tus entradas",
            f"{MODE_LABELS[profile.mode]} · hasta {seconds_text(profile.max_ms)} · "
            f"{filled} de {profile.slots} ranuras con sonido",
        )
        for n in range(1, profile.slots + 1):
            container.add_item(self._slot_section(n))
        if profile.slots < MAX_SLOTS:
            first = profile.slots + 1
            span = f"Ranura {first}" if first == MAX_SLOTS else f"Ranuras {first} a {MAX_SLOTS}"
            container.add_item(ui.TextDisplay(f"-# 🔒 {span}: en 🛒 Mejoras."))
        container.add_item(ui.Separator())
        if profile.exit_owned:
            container.add_item(self._slot_section(EXIT_SLOT))
        else:
            container.add_item(
                ui.TextDisplay("-# 🚪 Sonido de salida, al irte de voz: en 🛒 Mejoras.")
            )
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            self._button(f"Modo: {MODE_LABELS[profile.mode]}", self._toggle_mode, "entrada:modo")
        )
        row.add_item(self._button("🛒 Mejoras", self._goto("shop"), "entrada:mejoras"))
        row.add_item(self._button("🏆 Ranking", self._goto("ranking"), "entrada:ranking"))
        container.add_item(row)
        self.add_item(container)

    def _slot_section(self, n: int) -> ui.Section:
        profile = self.profile
        sound = profile.sounds.get(n)
        star = (
            " ⭐"
            if sound and n != EXIT_SLOT and profile.mode is Mode.FIXED and profile.active == n
            else ""
        )
        if sound is None:
            text = f"**{slot_title(None, n)}**\n-# Sube un audio y sonará aquí."
            button = self._button("📤 Subir", self._open_upload(n), f"entrada:subir:{n}")
        else:
            text = f"**{slot_title(sound, n)}**{star}\n-# {slot_summary(sound)}"
            button = self._button("Abrir", self._open_slot(n), f"entrada:abrir:{n}")
        return ui.Section(ui.TextDisplay(text), accessory=button)

    def _slot_page(self) -> None:
        profile = self.profile
        sound = profile.sounds.get(self.slot)
        if sound is None:
            self.page = "main"
            self._main()
            return
        effect = effect_of(sound.effect)
        container = ui.Container(accent_colour=COLOR)
        self._header(
            container,
            f"🎵 {slot_title(sound, sound.slot)}",
            "Suena al irte de voz" if sound.is_exit else f"Ranura {sound.slot} de entrada",
        )
        lines = [
            f"## `{sound.wave}`" if sound.wave else "",
            f"Dura **{seconds_text(sound.clip_ms)}** y empieza en el segundo "
            f"{seconds_text(sound.start_ms)[:-2]} del audio guardado "
            f"({seconds_text(sound.source_ms)}).",
            f"Volumen **{sound.volume} %** · {effect.emoji} {effect.label} · "
            f"Fundido: {'sí' if sound.fade else 'no'}",
            f"Ha sonado {sound.plays} {'vez' if sound.plays == 1 else 'veces'}.",
        ]
        container.add_item(ui.TextDisplay("\n".join(line for line in lines if line)))
        n = sound.slot
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._button("▶ Escuchar", self._listen(n), f"entrada:oir:{n}", primary=True))
        row.add_item(self._button("✏️ Ajustar", self._open_adjust(n), f"entrada:ajustar:{n}"))
        row.add_item(
            self._button(
                f"🌫️ Fundido: {'sí' if sound.fade else 'no'}",
                self._toggle_fade(n),
                f"entrada:fundido:{n}",
            )
        )
        if not sound.is_exit and profile.active != n:
            row.add_item(self._button("⭐ Usar esta", self._activate(n), f"entrada:activa:{n}"))
        container.add_item(row)
        row2: ui.ActionRow = ui.ActionRow()
        row2.add_item(self._button("📤 Cambiar audio", self._open_upload(n), f"entrada:subir:{n}"))
        confirming = self.confirm == f"borrar:{n}"
        row2.add_item(
            self._button(
                "🗑️ ¿Seguro? Pulsa otra vez" if confirming else "🗑️ Borrar",
                self._delete(n),
                f"entrada:borrar:{n}",
                danger=confirming,
            )
        )
        row2.add_item(self._button("◀ Volver", self._goto("main"), "entrada:volver"))
        container.add_item(row2)
        self.add_item(container)

    def _shop(self) -> None:
        profile = self.profile
        container = ui.Container(accent_colour=COLOR)
        exit_text = "con sonido de salida" if profile.exit_owned else "sin sonido de salida"
        self._header(
            container,
            "🛒 Mejoras de tus entradas",
            f"{profile.slots} de {MAX_SLOTS} ranuras · hasta {seconds_text(profile.max_ms)} "
            f"de {seconds_text(MAX_TENTHS * 100)} · {exit_text}",
        )
        blurbs = {
            Upgrade.SLOT: "Un sonido más para ir cambiando, al azar o por turnos.",
            Upgrade.LENGTH: "Medio segundo más para cada sonido. Los guardados se alargan en ✏️.",
            Upgrade.EXIT: "Un sonido aparte que suena cuando te vas de voz.",
        }
        done = {
            Upgrade.SLOT: f"✅ **Ranuras**: tienes las {MAX_SLOTS}.",
            Upgrade.LENGTH: f"✅ **Duración**: ya llegas a {seconds_text(MAX_TENTHS * 100)}.",
            Upgrade.EXIT: "✅ **Sonido de salida**: ya lo tienes.",
        }
        for upgrade in Upgrade:
            offer = next_offer(profile, upgrade)
            if offer is None:
                container.add_item(ui.TextDisplay(done[upgrade]))
                continue
            total = offer.base + igic(offer.base)
            confirming = self.confirm == f"comprar:{upgrade.value}"
            label = f"¿Pagar {format_amount(total)}?" if confirming else "Comprar"
            text = (
                f"**{offer_label(offer)}** · {format_amount(total)}\n"
                f"-# {blurbs[upgrade]} {format_amount(offer.base)} + IGIC."
            )
            if hint := financiar.teaser(total, "entrada"):
                text += f"\n-# {hint}."
            button = self._button(
                label, self._buy(upgrade), f"entrada:comprar:{upgrade.value}", success=confirming
            )
            container.add_item(ui.Section(ui.TextDisplay(text), accessory=button))
        if spent := total_spent(profile):
            container.add_item(ui.TextDisplay(f"-# Llevas {format_amount(spent)} en mejoras."))
        names = {Upgrade.SLOT: "ranura", Upgrade.LENGTH: "duración", Upgrade.EXIT: "salida"}
        row: ui.ActionRow = ui.ActionRow()
        for upgrade in Upgrade:
            offer = next_offer(profile, upgrade)
            if offer is not None and credit.refusal(offer.base + igic(offer.base)) is None:
                row.add_item(
                    self._button(
                        f"{financiar.LABEL} {names[upgrade]}",
                        self._finance(upgrade),
                        f"entrada:financiar:{upgrade.value}",
                    )
                )
        row.add_item(self._button("◀ Volver", self._goto("main"), "entrada:volver"))
        container.add_item(row)
        self.add_item(container)

    def _ranking(self) -> None:
        container = ui.Container(accent_colour=COLOR)
        top = self.cog.ranking(self.guild.id)
        self._header(container, "🏆 Las entradas que más suenan", "En este servidor")
        if not top:
            container.add_item(ui.TextDisplay("Todavía no ha sonado ninguna entrada."))
        else:
            medals = ("🥇", "🥈", "🥉")
            lines = []
            for i, (user_id, sound) in enumerate(top):
                mark = medals[i] if i < len(medals) else f"`{i + 1}.`"
                times = "vez" if sound.plays == 1 else "veces"
                kind = " 🚪" if sound.is_exit else ""
                lines.append(
                    f"{mark} **{sound.name}**{kind} de <@{user_id}> · {sound.plays} {times}"
                )
            container.add_item(ui.TextDisplay("\n".join(lines)))
            select: ui.Select = ui.Select(
                placeholder="▶ Escuchar una",
                options=[
                    discord.SelectOption(
                        label=f"{i + 1}. {sound.name}"[:100],
                        value=f"{user_id}:{sound.slot}",
                        description=self._owner_name(user_id)[:100],
                    )
                    for i, (user_id, sound) in enumerate(top)
                ],
                custom_id="entrada:ranking:oir",
            )

            async def listen(interaction: discord.Interaction) -> None:
                user_id, slot = (int(v) for v in select.values[0].split(":"))
                await self.cog.button_listen(interaction, self, user_id, slot)

            select.callback = listen  # type: ignore[method-assign]
            select_row: ui.ActionRow = ui.ActionRow()
            select_row.add_item(select)
            container.add_item(select_row)
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._button("◀ Volver", self._goto("main"), "entrada:volver"))
        container.add_item(row)
        self.add_item(container)

    def _owner_name(self, user_id: int) -> str:
        member = self.guild.get_member(user_id)
        return member.display_name if member else "Alguien que ya no está"

    @staticmethod
    def _button(  # noqa: ANN205
        label: str,
        callback,  # noqa: ANN001
        custom_id: str,
        *,
        primary: bool = False,
        danger: bool = False,
        success: bool = False,
    ) -> ui.Button:
        style = discord.ButtonStyle.secondary
        if primary:
            style = discord.ButtonStyle.primary
        if success:
            style = discord.ButtonStyle.success
        if danger:
            style = discord.ButtonStyle.danger
        button: ui.Button = ui.Button(label=label[:80], style=style, custom_id=custom_id)
        button.callback = callback  # type: ignore[method-assign]
        return button

    # -- Navegación (solo memoria) ----------------------------------------------------

    async def show(self, interaction: discord.Interaction) -> None:
        """Repinta el panel en la respuesta del clic (sin `ack`: solo memoria)."""
        self.rebuild()
        await edit(interaction, view=self, allowed_mentions=NO_MENTIONS)

    def _goto(self, page: str):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            self.page, self.note, self.confirm = page, None, None
            await self.show(interaction)
            if page == "ranking":
                logros.note(
                    self.cog.bot,
                    self.guild.id,
                    self.member.id,
                    StatDelta(add={"entrance_ranking": 1}),
                    getattr(interaction.channel, "id", None),
                )

        return callback

    def _open_slot(self, n: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            self.page, self.slot, self.note, self.confirm = "slot", n, None, None
            await self.show(interaction)

        return callback

    def _open_upload(self, n: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            # Un formulario tiene que ser la primera respuesta: nada de `ack` antes.
            await interaction.response.send_modal(UploadForm(self, n))

        return callback

    def _open_adjust(self, n: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            sound = self.profile.sounds.get(n)
            if sound is None:
                await self.show(interaction)
                return
            await interaction.response.send_modal(AdjustForm(self, sound))

        return callback

    # -- Acciones ---------------------------------------------------------------------

    async def _toggle_mode(self, interaction: discord.Interaction) -> None:
        await self.cog.button_mode(interaction, self)

    def _activate(self, n: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.button_activate(interaction, self, n)

        return callback

    def _toggle_fade(self, n: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.button_fade(interaction, self, n)

        return callback

    def _listen(self, n: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.button_listen(interaction, self, self.member.id, n)

        return callback

    def _delete(self, n: int):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            if self.confirm != f"borrar:{n}":
                self.confirm = f"borrar:{n}"
                await self.show(interaction)
                return
            await self.cog.button_delete(interaction, self, n)

        return callback

    def _finance(self, upgrade: Upgrade):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.button_finance(interaction, self, upgrade)

        return callback

    def _buy(self, upgrade: Upgrade):  # noqa: ANN202
        async def callback(interaction: discord.Interaction) -> None:
            if self.confirm != f"comprar:{upgrade.value}":
                self.confirm = f"comprar:{upgrade.value}"
                await self.show(interaction)
                return
            await self.cog.button_buy(interaction, self, upgrade)

        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.member.id:
            return True
        await interaction.response.send_message(
            "Este panel no es tuyo. Abre el tuyo con `entrada`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        try:
            if self.message is not None:
                await self.message.edit(view=None)
            elif self.interaction is not None:
                await self.interaction.edit_original_response(view=None)
        except discord.HTTPException:
            pass  # Borrado o caducado: el panel ya no se puede tocar igual.


# -- Cog -------------------------------------------------------------------------------


class Entrance(commands.Cog):
    """Sonidos personalizados que suenan cuando un miembro entra o sale de voz."""

    def __init__(
        self, bot: commands.Bot, store: EntranceSoundStore, rng: random.Random | None = None
    ) -> None:
        self.bot = bot
        self.store = store
        self.rng = rng or random.Random()
        self._jobs = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._profiles: dict[tuple[int, int], Profile] = {}
        self._pending: dict[int, deque[Pending]] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._last_played: dict[tuple[int, int, bool], float] = {}
        #: Última ranura de entrada que sonó de cada uno (modos al azar y por turnos).
        self._last_slot: dict[tuple[int, int], int] = {}
        self._clips: OrderedDict[tuple[int, int, int], list[bytes]] = OrderedDict()

    async def cog_load(self) -> None:
        """Carga en memoria todas las fichas (una lectura al arrancar)."""
        self._profiles = await self.store.load_all()

    # -- Fichas en memoria ------------------------------------------------------------

    def profile(self, guild_id: int, user_id: int) -> Profile:
        """La ficha de un miembro (vacía si no tiene nada)."""
        return self._profiles.get((guild_id, user_id)) or Profile()

    def _set(self, guild_id: int, user_id: int, profile: Profile) -> None:
        if profile.empty:
            self._profiles.pop((guild_id, user_id), None)
        else:
            self._profiles[(guild_id, user_id)] = profile

    def _forget_clip(self, guild_id: int, user_id: int, slot: int) -> None:
        self._clips.pop((guild_id, user_id, slot), None)

    def ranking(self, guild_id: int) -> list[tuple[int, Slot]]:
        """Los sonidos que más han sonado en el servidor, `(dueño, ranura)`."""
        sounds = [
            (user_id, sound)
            for (g, user_id), profile in self._profiles.items()
            if g == guild_id
            for sound in profile.sounds.values()
            if sound.plays > 0
        ]
        sounds.sort(key=lambda item: (-item[1].plays, item[0], item[1].slot))
        return sounds[:RANKING_SIZE]

    # -- Guardar y ajustar ------------------------------------------------------------

    async def save_upload(
        self,
        guild_id: int,
        user_id: int,
        slot: int,
        attachment: discord.Attachment,
        *,
        name: str | None = None,
        start_text: str | None = None,
        effect: str | None = None,
        volume: int | None = None,
    ) -> tuple[Slot, bytes, float]:
        """Descarga, recorta y guarda un audio en una ranura.

        Returns:
            `(ranura, clip, segundos del audio subido)`.

        Raises:
            EntranceError, EntranceSoundError: Con un texto para el miembro.
            discord.HTTPException: Si no se pudo descargar.
        """
        profile = self.profile(guild_id, user_id)
        if slot != EXIT_SLOT and not 1 <= slot <= profile.slots:
            raise EntranceError("Esa ranura no es tuya. Cómprala en 🛒 Mejoras.")
        if slot == EXIT_SLOT and not profile.exit_owned:
            raise EntranceError("El sonido de salida se compra en 🛒 Mejoras.")
        if not _is_accepted_attachment(attachment):
            raise EntranceError("Ese archivo no parece un audio.")
        if attachment.size > MAX_UPLOAD_BYTES:
            raise EntranceError(
                f"El archivo es demasiado grande (máximo {MAX_UPLOAD_BYTES // 2**20} MB)."
            )
        start = parse_seconds(start_text, default=0.0) or 0.0
        old = profile.sounds.get(slot)
        async with self._jobs:
            work = await asyncio.to_thread(self.store.make_work_dir)
            try:
                work_dir = Path(work.name)
                upload = work_dir / "upload"
                await asyncio.to_thread(upload.write_bytes, await attachment.read())
                duration = await probe_audio_duration(upload)
                if start >= duration - 0.05:
                    raise EntranceError(
                        f"El audio dura {seconds_text(round(duration * 1000))}: no puede "
                        f"empezar en el segundo {seconds_text(round(start * 1000))[:-2]}."
                    )
                source = work_dir / "source.ogg"
                await build_raw_source(upload, source, start=start, seconds=SOURCE_SECONDS)
                source_ms = round(min(duration - start, SOURCE_SECONDS) * 1000)
                draft = Slot(
                    slot=slot,
                    name=clean_name(name, old.name if old else self._default_name(slot)),
                    # Lo que no se dice se queda como estaba en la ranura.
                    volume=volume
                    if volume is not None
                    else (old.volume if old else DEFAULT_VOLUME_PERCENT),
                    effect=effect_of(effect or (old.effect if old else DEFAULT_EFFECT.key)).key,
                    fade=old.fade if old else False,
                    start_ms=0,
                    length_ms=profile.max_ms,
                    source_ms=source_ms,
                    clip_ms=0,
                    wave="",
                    plays=old.plays if old else 0,
                )
                sound, clip = await self._render(guild_id, user_id, draft, source, work_dir)
                await self.store.save_sound(
                    guild_id, user_id, sound, clip=work_dir / "clip.ogg", source=source
                )
            finally:
                await asyncio.to_thread(work.cleanup)
        self._store_sound(guild_id, user_id, sound)
        return sound, clip, duration

    async def adjust(self, guild_id: int, user_id: int, sound: Slot) -> tuple[Slot, bytes]:
        """Vuelve a sacar el clip de una ranura con otros ajustes, desde su fuente.

        Raises:
            EntranceError, EntranceSoundError: Con un texto para el miembro.
        """
        source, _clip = self.store.slot_paths(guild_id, user_id, sound.slot)
        if not await asyncio.to_thread(source.is_file):
            raise EntranceError("Falta el audio guardado de esta ranura. Vuelve a subirlo.")
        async with self._jobs:
            work = await asyncio.to_thread(self.store.make_work_dir)
            try:
                work_dir = Path(work.name)
                sound, clip = await self._render(guild_id, user_id, sound, source, work_dir)
                await self.store.save_sound(guild_id, user_id, sound, clip=work_dir / "clip.ogg")
            finally:
                await asyncio.to_thread(work.cleanup)
        self._store_sound(guild_id, user_id, sound)
        return sound, clip

    async def _render(
        self, guild_id: int, user_id: int, draft: Slot, source: Path, work_dir: Path
    ) -> tuple[Slot, bytes]:
        """Saca el clip de `draft` en `work_dir/clip.ogg` y completa duración y onda."""
        profile = self.profile(guild_id, user_id)
        length = max(500, min(draft.length_ms, profile.max_ms))
        start = max(0, min(draft.start_ms, max(0, draft.source_ms - 100)))
        effect = effect_of(draft.effect)
        clip_path = work_dir / "clip.ogg"
        await render_slot(
            source,
            clip_path,
            start_ms=start,
            length_ms=length,
            volume_percent=draft.volume,
            effect_filter=effect.filter,
            speed=effect.speed,
            fade=draft.fade,
        )
        clip = await asyncio.to_thread(clip_path.read_bytes)
        packets = read_opus_packets(clip)  # Falla aquí, y no al entrar a voz, si salió mal.
        sound = replace(
            draft,
            start_ms=start,
            length_ms=length,
            clip_ms=round(len(packets) * PACKET_SECONDS * 1000),
            wave=wave_text(packets),
        )
        return sound, clip

    def _store_sound(self, guild_id: int, user_id: int, sound: Slot) -> None:
        profile = self.profile(guild_id, user_id).with_sound(sound)
        if sound.slot != EXIT_SLOT and profile.active not in profile.sounds:
            profile = replace(profile, active=sound.slot)
        self._set(guild_id, user_id, profile)
        self._forget_clip(guild_id, user_id, sound.slot)

    @staticmethod
    def _default_name(slot: int) -> str:
        return "Mi salida" if slot == EXIT_SLOT else f"Entrada {slot}"

    async def delete_slot(self, guild_id: int, user_id: int, slot: int) -> bool:
        """Vacía una ranura. Devuelve si tenía sonido."""
        removed = await self.store.delete_sound(guild_id, user_id, slot)
        profile = self.profile(guild_id, user_id).without(slot)
        if slot == profile.active and profile.entrances:
            profile = replace(profile, active=profile.entrances[0].slot)
            await self.store.save_settings(guild_id, user_id, profile)
        self._set(guild_id, user_id, profile)
        self._forget_clip(guild_id, user_id, slot)
        return removed

    def _saved_stats(
        self, sound: Slot, *, uploaded: bool, trimmed: bool = False, adjusted: bool = False
    ) -> StatDelta:
        """Lo que cuentan los logros al guardar o ajustar una ranura."""
        profile_add: dict[str, int] = {}
        if uploaded:
            profile_add["entrance_saved"] = 1
        if trimmed:
            profile_add["entrance_trimmed"] = 1
        if adjusted:
            profile_add["entrance_adjusted"] = 1
        if sound.effect != DEFAULT_EFFECT.key:
            profile_add[f"entrance_fx_{sound.effect}"] = 1
        if sound.is_exit:
            profile_add["entrance_exit_saved"] = 1
        return StatDelta(
            add=profile_add,
            peak={"entrance_volume_max": sound.volume, "entrance_clip_ms_max": sound.clip_ms},
        )

    def _filled_stats(self, guild_id: int, user_id: int) -> StatDelta:
        profile = self.profile(guild_id, user_id)
        return StatDelta(peak={"entrance_filled": len(profile.entrances)})

    async def _cameo(self, guild_id: int, user_id: int) -> str:
        line = await mascotas.cameo(self.bot, guild_id, user_id, Moment(Event.USE))
        return f"\n{line}" if line else ""

    # -- Botones y formularios --------------------------------------------------------

    async def form_upload(self, interaction: discord.Interaction, form: UploadForm) -> None:
        """El formulario 📤: guarda el audio en la ranura y enseña su página."""
        panel = form.panel
        # Descargar y convertir tarda: se acepta el envío antes.
        await ack(interaction)
        guild_id, user_id = panel.guild.id, panel.member.id
        files = form.upload.values
        if not files:
            panel.note = "❌ No ha llegado ningún archivo."
            await panel.show(interaction)
            return
        try:
            volume = _volume_from(form.volume.value, DEFAULT_VOLUME_PERCENT)
            sound, _clip, duration = await self.save_upload(
                guild_id,
                user_id,
                form.slot,
                files[0],
                name=form.name.value,
                start_text=form.start.value,
                effect=form.effect.values[0] if form.effect.values else None,
                volume=volume,
            )
        except (EntranceError, EntranceSoundError) as error:
            panel.note = f"❌ {error}"
            await panel.show(interaction)
            return
        except discord.HTTPException:
            logger.warning("No se pudo descargar el audio de entrada", exc_info=True)
            panel.note = "❌ No se pudo descargar el archivo. Inténtalo de nuevo."
            await panel.show(interaction)
            return
        except Exception:
            logger.exception("Error inesperado al preparar un sonido de entrada")
            panel.note = "❌ Algo salió mal al preparar el sonido."
            await panel.show(interaction)
            return
        trimmed = (parse_seconds(form.start.value, default=0.0) or 0.0) > 0
        cut = (
            f" Me quedo con {seconds_text(sound.clip_ms)} de "
            f"{seconds_text(round(duration * 1000))}."
            if duration * 1000 > sound.clip_ms + 100
            else ""
        )
        panel.page, panel.slot, panel.confirm = "slot", sound.slot, None
        panel.note = f"{self.rng.choice(SAVED_LINES)}{cut}{await self._cameo(guild_id, user_id)}"
        await panel.show(interaction)
        self._after_save(interaction, guild_id, sound, uploaded=True, trimmed=trimmed)

    async def form_adjust(self, interaction: discord.Interaction, form: AdjustForm) -> None:
        """El formulario ✏️: aplica nombre, recorte, duración, efecto y volumen."""
        panel = form.panel
        await ack(interaction)
        guild_id, user_id = panel.guild.id, panel.member.id
        current = panel.profile.sounds.get(form.sound.slot)
        if current is None:
            panel.page, panel.note = "main", "❌ Esa ranura ya está vacía."
            await panel.show(interaction)
            return
        try:
            start = parse_seconds(form.start.value, default=current.start_ms / 1000)
            length = parse_seconds(form.length.value, default=current.length_ms / 1000)
            volume = _volume_from(form.volume.value, current.volume)
            effect = form.effect.values[0] if form.effect.values else current.effect
            name = clean_name(form.name.value, current.name)
            wanted = replace(
                current,
                name=name,
                start_ms=round((start or 0) * 1000),
                length_ms=round((length or 0) * 1000),
                volume=volume,
                effect=effect_of(effect).key,
            )
            if (wanted.start_ms, wanted.length_ms, wanted.volume, wanted.effect) == (
                current.start_ms,
                current.length_ms,
                current.volume,
                current.effect,
            ):
                # Solo el nombre: no hace falta volver a sacar el audio.
                sound = wanted
                await self.store.rename(guild_id, user_id, sound.slot, sound.name)
                self._store_sound(guild_id, user_id, sound)
            else:
                sound, _clip = await self.adjust(guild_id, user_id, wanted)
        except (EntranceError, EntranceSoundError) as error:
            panel.note = f"❌ {error}"
            await panel.show(interaction)
            return
        except Exception:
            logger.exception("Error inesperado al ajustar un sonido de entrada")
            panel.note = "❌ Algo salió mal al ajustar el sonido."
            await panel.show(interaction)
            return
        capped = (
            f" La duración se queda en {seconds_text(sound.length_ms)}: más, en 🛒 Mejoras."
            if wanted.length_ms > sound.length_ms
            else ""
        )
        panel.note = f"✅ Ajustado.{capped}"
        await panel.show(interaction)
        self._after_save(interaction, guild_id, sound, uploaded=False, adjusted=True)

    def _after_save(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        sound: Slot,
        *,
        uploaded: bool,
        trimmed: bool = False,
        adjusted: bool = False,
    ) -> None:
        delta = self._saved_stats(sound, uploaded=uploaded, trimmed=trimmed, adjusted=adjusted)
        delta.merge(self._filled_stats(guild_id, interaction.user.id))
        logros.note(
            self.bot,
            guild_id,
            interaction.user.id,
            delta,
            getattr(interaction.channel, "id", None),
        )

    async def button_fade(
        self, interaction: discord.Interaction, panel: EntrancePanel, n: int
    ) -> None:
        """🌫️ Fundido: lo pone o lo quita y vuelve a sacar el clip."""
        await ack(interaction)
        guild_id, user_id = panel.guild.id, panel.member.id
        sound = panel.profile.sounds.get(n)
        if sound is None:
            panel.page = "main"
            await panel.show(interaction)
            return
        try:
            sound, _clip = await self.adjust(guild_id, user_id, replace(sound, fade=not sound.fade))
        except (EntranceError, EntranceSoundError) as error:
            panel.note = f"❌ {error}"
        else:
            panel.note = "🌫️ Con fundido." if sound.fade else "Sin fundido."
        panel.confirm = None
        await panel.show(interaction)
        if sound.fade:
            logros.note(
                self.bot,
                guild_id,
                user_id,
                StatDelta(add={"entrance_faded": 1}),
                getattr(interaction.channel, "id", None),
            )

    async def button_mode(self, interaction: discord.Interaction, panel: EntrancePanel) -> None:
        """Modo: pasa al siguiente. Cambia memoria, contesta y guarda después."""
        guild_id, user_id = panel.guild.id, panel.member.id
        profile = self.profile(guild_id, user_id)
        profile = replace(profile, mode=next_mode(profile.mode))
        self._set(guild_id, user_id, profile)
        self._last_slot.pop((guild_id, user_id), None)
        panel.note, panel.confirm = None, None
        await panel.show(interaction)
        await self.store.save_settings(guild_id, user_id, profile)
        logros.note(
            self.bot,
            guild_id,
            user_id,
            StatDelta(add={f"entrance_mode_{profile.mode.value}": 1}),
            getattr(interaction.channel, "id", None),
        )

    async def button_activate(
        self, interaction: discord.Interaction, panel: EntrancePanel, n: int
    ) -> None:
        """⭐ Usar esta: la deja como la que suena en modo fijo."""
        guild_id, user_id = panel.guild.id, panel.member.id
        profile = self.profile(guild_id, user_id)
        if n not in profile.sounds:
            panel.page = "main"
            await panel.show(interaction)
            return
        profile = replace(profile, active=n, mode=Mode.FIXED)
        self._set(guild_id, user_id, profile)
        panel.note = "⭐ Ahora suena esta al entrar."
        await panel.show(interaction)
        await self.store.save_settings(guild_id, user_id, profile)

    async def button_listen(
        self, interaction: discord.Interaction, panel: EntrancePanel, owner_id: int, n: int
    ) -> None:
        """▶ Escuchar: manda el clip en un mensaje privado nuevo."""
        await ack(interaction, new_message=True)
        guild_id = panel.guild.id
        sound = self.profile(guild_id, owner_id).sounds.get(n)
        clip = await self.store.read_clip(guild_id, owner_id, n) if sound else None
        if sound is None or clip is None:
            await edit(interaction, content="Ese sonido ya no está.")
            return
        own = owner_id == interaction.user.id
        who = "" if own else f" de <@{owner_id}>"
        await edit(
            interaction,
            content=f"▶ **{sound.name}**{who} · {seconds_text(sound.clip_ms)}",
            attachments=[discord.File(io.BytesIO(clip), "entrada.ogg")],
            allowed_mentions=NO_MENTIONS,
        )
        if not own:
            logros.note(
                self.bot,
                guild_id,
                interaction.user.id,
                StatDelta(add={"entrance_listened": 1}),
                getattr(interaction.channel, "id", None),
            )

    async def button_delete(
        self, interaction: discord.Interaction, panel: EntrancePanel, n: int
    ) -> None:
        """🗑️ Borrar (segundo clic): vacía la ranura."""
        await ack(interaction)
        guild_id, user_id = panel.guild.id, panel.member.id
        removed = await self.delete_slot(guild_id, user_id, n)
        panel.page, panel.confirm = "main", None
        panel.note = "🗑️ Ranura vaciada." if removed else "Esa ranura ya estaba vacía."
        await panel.show(interaction)
        if removed:
            logros.note(
                self.bot,
                guild_id,
                user_id,
                StatDelta(add={"entrance_deleted": 1}),
                getattr(interaction.channel, "id", None),
            )

    async def buy(self, guild_id: int, user_id: int, upgrade: Upgrade) -> tuple[str, int] | str:
        """Compra la siguiente mejora de un tipo.

        Tratamiento fiscal: IGIC al tipo general (7 %). Es una prestación de
        servicios hecha en Canarias a título oneroso (art. 4 de la Ley 20/1991)
        y el tipo general es el del art. 51 de la Ley 4/2012 (ver
        `EconomyService.purchase`). La base va a la caja de la tienda, el IGIC
        al Estado y la mejora se apunta en la misma transacción
        (`EntranceSoundStore.buy`). Es un sumidero: no crea dinero. No hay IRPF
        (gastar no es renta) ni retención de juego (no es una apuesta).

        Returns:
            `(texto del resultado, IGIC pagado)` si se ha comprado, o el texto
            del problema sin haber movido dinero.
        """
        profile = self.profile(guild_id, user_id)
        offer = next_offer(profile, upgrade)
        if offer is None:
            return "Eso ya lo tienes al máximo."
        tax = igic(offer.base)
        economy = getattr(self.bot, "economy", None)
        if economy is None:
            return "El banco está cerrado ahora mismo."
        try:
            bought, _balance = await economy.purchase(
                guild_id,
                user_id,
                base=offer.base,
                tax=tax,
                concept=f"entrada:{upgrade.value}",
                reserve=self.store.buy(guild_id, user_id, offer),
            )
        except EntranceError as error:
            return str(error)
        except InsufficientFundsError as error:
            return insufficient_text(error.balance, offer.base + tax)
        current = self.profile(guild_id, user_id)
        self._set(
            guild_id,
            user_id,
            replace(
                current,
                slots=bought.slots,
                max_tenths=bought.max_tenths,
                exit_owned=bought.exit_owned,
            ),
        )
        extra = {
            Upgrade.SLOT: f" Ya tienes {bought.slots} ranuras.",
            Upgrade.LENGTH: (
                f" Tus sonidos pueden durar {seconds_text(bought.max_ms)}: los que ya tenías "
                "se alargan en ✏️ Ajustar."
            ),
            Upgrade.EXIT: " Súbelo en el panel: suena cuando te vas de voz.",
        }[upgrade]
        text = (
            f"{self.rng.choice(BOUGHT_LINES)} **{offer_label(offer)}** por "
            f"{format_amount(offer.base + tax)}.{extra}\n{tax_text(tax)}"
        )
        return text, tax

    async def button_buy(
        self, interaction: discord.Interaction, panel: EntrancePanel, upgrade: Upgrade
    ) -> None:
        """🛒 Comprar (segundo clic): cobra la mejora y repinta la tienda."""
        await ack(interaction)
        guild_id, user_id = panel.guild.id, panel.member.id
        result = await self.buy(guild_id, user_id, upgrade)
        panel.confirm = None
        if isinstance(result, str):
            panel.note = f"❌ {result}"
            await panel.show(interaction)
            return
        text, _tax = result
        if hint := await renta.hint(self.bot, guild_id, user_id, Moment(Event.BUY)):
            text += f"\n{hint}"
        panel.note = text
        await panel.show(interaction)
        await renta.remind(self.bot, interaction)
        await self._track_bought(interaction, upgrade)

    async def button_finance(
        self, interaction: discord.Interaction, panel: EntrancePanel, upgrade: Upgrade
    ) -> None:
        """💳 Financiar una mejora: la oferta del banco sustituye al panel (↩️ Volver lo
        devuelve) y, al firmar, se compra por `buy` como siempre."""
        guild_id, user_id = panel.guild.id, panel.member.id
        offer = next_offer(self.profile(guild_id, user_id), upgrade)
        if offer is None:
            panel.confirm, panel.note = None, "Eso ya lo tienes al máximo."
            await panel.show(interaction)
            return

        async def buy(_interaction: discord.Interaction) -> str | None:
            result = await self.buy(guild_id, user_id, upgrade)
            if isinstance(result, str):
                return result
            panel.confirm, panel.note = None, result[0]
            panel.rebuild()
            return None

        async def after(after_interaction: discord.Interaction) -> None:
            await self._track_bought(after_interaction, upgrade)

        purchase = credit.Purchase(
            f"🔔 {offer_label(offer)}", offer.base + igic(offer.base), "entrada"
        )
        deal = financiar.Deal(purchase, buy, host=panel, after=after)
        if not await financiar.open_finance(self.bot, interaction, deal):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def _track_bought(self, interaction: discord.Interaction, upgrade: Upgrade) -> None:
        """Los logros de comprar una mejora, al contado o financiada."""
        guild_id, user_id = interaction.guild_id or 0, interaction.user.id
        profile = self.profile(guild_id, user_id)
        await logros.track(
            self.bot,
            guild_id,
            interaction.user,
            interaction.channel,
            StatDelta(
                add={f"entrance_bought_{upgrade.value}": 1},
                peak={
                    "entrance_slots": profile.slots,
                    "entrance_tenths": profile.max_tenths,
                    "entrance_spent": total_spent(profile),
                },
            ),
        )

    # -- Comando ----------------------------------------------------------------------

    async def _entrada_impl(
        self,
        responder: CommandResponder,
        attachment: discord.Attachment | None,
        volume: int | None,
        delete: bool,
        start: str | None = None,
    ) -> bool:
        """Los atajos de `/entrada` y `.entrada` sobre la ranura activa.

        Returns:
            `False` si no hay atajo que hacer y hay que abrir el panel.
        """
        guild, member = responder.guild, responder.member
        if guild is None or member is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return True
        profile = self.profile(guild.id, member.id)

        if delete:
            if attachment is not None or volume is not None:
                await responder.send_error("Para borrar tu sonido, usa `borrar` sin nada más.")
                return True
            removed = await self.delete_slot(guild.id, member.id, profile.active)
            if removed:
                logros.note_for(self.bot, responder, StatDelta(add={"entrance_deleted": 1}))
            text = (
                f"🗑️ Ranura {profile.active} vaciada."
                if removed
                else "No tenías sonido en la ranura activa."
            )
            await responder.send(text, ephemeral=True)
            return True

        if attachment is None and volume is None:
            return False

        if attachment is None:
            sound = profile.sounds.get(profile.active)
            if sound is None:
                await responder.send_error(
                    f"Aún no tienes sonido. Sube primero un audio de hasta "
                    f"{seconds_text(profile.max_ms)}."
                )
                return True
        await responder.start_progress("🎧 Preparando tu sonido...", ephemeral=True)
        try:
            if attachment is not None:
                slot = profile.default_slot()
                sound, clip, _duration = await self.save_upload(
                    guild.id,
                    member.id,
                    slot,
                    attachment,
                    start_text=start,
                    volume=volume,
                )
                uploaded = True
            else:
                sound, clip = await self.adjust(
                    guild.id, member.id, replace(sound, volume=volume or sound.volume)
                )
                uploaded = False
        except (EntranceError, EntranceSoundError) as error:
            await responder.finish(f"❌ {error}")
            return True
        except discord.HTTPException:
            logger.warning("No se pudo descargar el audio de entrada", exc_info=True)
            await responder.finish("No se pudo descargar el archivo. Inténtalo de nuevo.")
            return True
        except Exception:
            # Fallo imprevisto: se registra con traza y se avisa, para que el
            # aviso de progreso nunca se quede colgado.
            logger.exception("Error inesperado al preparar un sonido de entrada")
            await responder.finish("Algo salió mal al preparar el sonido.")
            return True

        delta = self._saved_stats(
            sound, uploaded=uploaded, trimmed=bool(start and (parse_seconds(start) or 0) > 0)
        )
        delta.merge(self._filled_stats(guild.id, member.id))
        logros.note_for(self.bot, responder, delta)
        content = (
            f"✅ **{slot_title(sound, sound.slot)}** guardada "
            f"({seconds_text(sound.clip_ms)}, volumen {sound.volume} %). "
            "Sonará cuando entres a un canal de voz. El resto, en el panel de `entrada`."
            f"{await self._cameo(guild.id, member.id)}"
        )
        try:
            await responder.finish(content, file=discord.File(io.BytesIO(clip), "entrada.ogg"))
        except discord.HTTPException:
            # Ya está guardado; si el canal no admite adjuntos, basta el texto.
            logger.warning("No se pudo adjuntar la vista previa del sonido", exc_info=True)
            await responder.finish(content)
        return True

    @app_commands.command(name="entrada", description="Tus sonidos al entrar y salir de voz.")
    @app_commands.guild_only()
    @app_commands.describe(
        archivo="Atajo: sube un audio a tu ranura activa (mp3, ogg, wav, m4a...).",
        inicio="Con archivo: desde qué segundo empieza (12, 12,5 o 1:05).",
        volumen=f"Atajo: volumen de la activa ({MIN_VOLUME_PERCENT}-{MAX_VOLUME_PERCENT} %).",
        borrar="Atajo: vacía tu ranura activa.",
    )
    async def entrada(
        self,
        interaction: discord.Interaction,
        archivo: discord.Attachment | None = None,
        inicio: str | None = None,
        volumen: app_commands.Range[int, MIN_VOLUME_PERCENT, MAX_VOLUME_PERCENT] | None = None,
        borrar: bool = False,
    ) -> None:
        """Sin opciones abre el panel en privado; con ellas, hace el atajo."""
        if await self._entrada_impl(
            InteractionResponder(interaction), archivo, volumen, borrar, inicio
        ):
            return
        guild = interaction.guild
        if guild is None:
            return
        panel = EntrancePanel(self, guild, interaction.user)
        panel.interaction = interaction
        # El panel sale de memoria: se contesta directamente, sin «pensando…».
        await interaction.response.send_message(
            view=panel, ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        logros.note(
            self.bot,
            guild.id,
            interaction.user.id,
            StatDelta(add={"entrance_panel": 1}),
            getattr(interaction.channel, "id", None),
        )

    @commands.command(name="entrada")
    @commands.guild_only()
    async def entrada_text(self, ctx: commands.Context, opcion: str | None = None) -> None:
        """Versión de texto (`.entrada`) de `/entrada`.

        El audio va adjunto al propio mensaje; `opcion` es un volumen o `borrar`.
        Sin nada, abre el panel (que solo maneja quien lo abre).
        """
        responder = ContextResponder(ctx)
        try:
            volume, delete = parse_text_option(opcion)
        except EntranceSoundError as error:
            await responder.send_error(str(error))
            return
        attachment = ctx.message.attachments[0] if ctx.message.attachments else None
        if await self._entrada_impl(responder, attachment, volume, delete):
            return
        if ctx.guild is None:
            return
        panel = EntrancePanel(self, ctx.guild, ctx.author)
        panel.message = await ctx.send(view=panel, allowed_mentions=NO_MENTIONS)
        logros.note_for(self.bot, responder, StatDelta(add={"entrance_panel": 1}))

    # ------------------------------------------------------------------ #
    # Reproducción al entrar y salir de voz
    # ------------------------------------------------------------------ #

    def _cooldown_allows(self, guild_id: int, user_id: int, leaving: bool = False) -> bool:
        """Registra el intento y dice si ha pasado el enfriamiento."""
        now = time.monotonic()
        key = (guild_id, user_id, leaving)
        if now - self._last_played.get(key, float("-inf")) < COOLDOWN_SECONDS:
            return False
        if len(self._last_played) >= MAX_COOLDOWN_ENTRIES:
            # Las entradas caducadas ya no bloquean nada: se pueden tirar.
            self._last_played = {
                k: t for k, t in self._last_played.items() if now - t < COOLDOWN_SECONDS
            }
        self._last_played[key] = now
        return True

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Encola la entrada de quien llega (o cambia) a un canal, o la salida de quien se va.

        Silenciarse, ensordecerse o empezar a emitir también disparan este
        evento; se ignoran porque el canal no cambia. Solo mira memoria.
        """
        if member.bot or before.channel == after.channel:
            return
        guild = member.guild
        profile = self._profiles.get((guild.id, member.id))
        if profile is None:
            return
        if after.channel is not None:
            if guild.afk_channel is not None and after.channel.id == guild.afk_channel.id:
                return
            key = (guild.id, member.id)
            sound = pick(profile, self._last_slot.get(key), self.rng)
            if sound is None or not self._cooldown_allows(guild.id, member.id):
                return
            self._last_slot[key] = sound.slot
            self._enqueue(guild, Pending(member.id, after.channel.id, sound.slot, sound.clip_ms))
            return
        # Se ha ido de voz: su salida suena en el canal que deja, si queda alguien.
        sound = profile.exit
        channel = before.channel
        if sound is None or channel is None or not self._has_listeners(channel, member.id):
            return
        if not self._cooldown_allows(guild.id, member.id, leaving=True):
            return
        self._enqueue(guild, Pending(member.id, channel.id, EXIT_SLOT, sound.clip_ms))

    @staticmethod
    def _has_listeners(channel: discord.abc.GuildChannel, leaving_id: int) -> bool:
        members = getattr(channel, "members", [])
        return any(not m.bot and m.id != leaving_id for m in members)

    def _enqueue(self, guild: discord.Guild, item: Pending) -> None:
        queue = self._pending.setdefault(guild.id, deque())
        if len(queue) >= MAX_PENDING_PER_GUILD:
            return
        if sum(p.ms for p in queue) + item.ms > MAX_PENDING_MS:
            return
        queue.append(item)
        self._ensure_worker(guild)

    def _ensure_worker(self, guild: discord.Guild) -> None:
        """Arranca el reproductor del servidor si no hay uno en marcha."""
        worker = self._workers.get(guild.id)
        if worker is None or worker.done():
            self._workers[guild.id] = asyncio.create_task(
                self._drain(guild), name=f"entrance-sounds-{guild.id}"
            )

    async def _packets(self, guild_id: int, user_id: int, slot: int) -> list[bytes] | None:
        """Los paquetes del clip de una ranura, de memoria o del disco."""
        key = (guild_id, user_id, slot)
        if (cached := self._clips.get(key)) is not None:
            self._clips.move_to_end(key)
            return cached
        clip = await self.store.read_clip(guild_id, user_id, slot)
        if clip is None:
            return None
        try:
            packets = read_opus_packets(clip)
        except EntranceSoundError:
            logger.warning(
                "Sonido de entrada dañado: servidor %s, miembro %s, ranura %s",
                guild_id,
                user_id,
                slot,
            )
            return None
        self._clips[key] = packets
        while len(self._clips) > CLIP_CACHE:
            self._clips.popitem(last=False)
        return packets

    def _target(self, guild: discord.Guild, item: Pending) -> discord.VoiceChannel | None:
        """El canal donde debe sonar, si sigue teniendo sentido."""
        if item.slot == EXIT_SLOT:
            channel = guild.get_channel(item.channel_id)
            if channel is None or not self._has_listeners(channel, item.member_id):
                return None
            return channel  # type: ignore[return-value]
        member = guild.get_member(item.member_id)
        channel = member.voice.channel if member and member.voice else None
        if channel is None or channel.id != item.channel_id:
            return None  # Ya se fue o cambió de canal; su nuevo evento lo cubrirá.
        return channel  # type: ignore[return-value]

    async def _drain(self, guild: discord.Guild) -> None:
        """Reproduce la cola del servidor reutilizando una sola conexión de voz."""
        voice: discord.VoiceClient | None = None
        queue = self._pending.setdefault(guild.id, deque())
        try:
            while queue:
                item = queue.popleft()
                current = guild.voice_client
                if current is not None and current is not voice:
                    # El bot ya está en voz por otro motivo (música): no se interrumpe.
                    queue.clear()
                    break

                channel = self._target(guild, item)
                if channel is None or not self._can_join(channel):
                    continue
                packets = await self._packets(guild.id, item.member_id, item.slot)
                if packets is None:
                    continue

                try:
                    voice = await self._join(channel, voice)
                except (discord.ClientException, discord.HTTPException, TimeoutError):
                    logger.warning(
                        "No se pudo entrar al canal %s para un sonido de entrada",
                        channel.id,
                        exc_info=True,
                    )
                    break

                await self._play(voice, packets)
                await self._played(guild.id, item, channel.id)
                if not voice.is_connected():
                    # Nos desconectaron (o la música tomó la voz): no se insiste.
                    queue.clear()
                    break
        except Exception:
            logger.exception("Error inesperado reproduciendo sonidos de entrada en %s", guild.id)
        finally:
            if voice is not None and guild.voice_client is voice:
                try:
                    await voice.disconnect(force=False)
                except discord.HTTPException:
                    logger.warning(
                        "Error al salir del canal tras un sonido de entrada", exc_info=True
                    )
            self._workers.pop(guild.id, None)
            # Alguien pudo entrar mientras salíamos: su sonido no debe perderse.
            if queue and not self.bot.is_closed():
                self._ensure_worker(guild)

    async def _played(self, guild_id: int, item: Pending, channel_id: int) -> None:
        """Apunta la reproducción: ranking, memoria y logros."""
        profile = self.profile(guild_id, item.member_id)
        if (sound := profile.sounds.get(item.slot)) is not None:
            self._set(
                guild_id, item.member_id, profile.with_sound(replace(sound, plays=sound.plays + 1))
            )
        try:
            await self.store.add_play(guild_id, item.member_id, item.slot)
        except Exception:
            logger.warning("No se pudo apuntar una reproducción de entrada", exc_info=True)
        logros.note(
            self.bot, guild_id, item.member_id, self._played_stats(guild_id, item), channel_id
        )

    def _played_stats(self, guild_id: int, item: Pending) -> StatDelta:
        """Lo que cuentan los logros cada vez que suena un sonido."""
        stat = "entrance_exit_played" if item.slot == EXIT_SLOT else "entrance_played"
        delta = StatDelta(add={stat: 1})
        now = datetime.now(TIMEZONE)
        if now.hour < 6:
            delta.add["entrance_played_dawn"] = 1
        if (now.month, now.day) == (12, 31):
            delta.add["entrance_played_nye"] = 1
        top = self.ranking(guild_id)
        if top and top[0][0] == item.member_id and top[0][1].plays >= TOP_MIN_PLAYS:
            delta.peak["entrance_top1"] = 1
        return delta

    def _can_join(self, channel: discord.abc.GuildChannel) -> bool:
        """Comprueba permisos y aforo antes de intentar conectar."""
        me = channel.guild.me
        permissions = channel.permissions_for(me)
        if not (permissions.connect and permissions.speak):
            return False
        user_limit = getattr(channel, "user_limit", 0)
        members = getattr(channel, "members", [])
        is_full = bool(user_limit) and len(members) >= user_limit
        return not is_full or permissions.move_members

    async def _join(
        self,
        channel: discord.VoiceChannel | discord.StageChannel,
        voice: discord.VoiceClient | None,
    ) -> discord.VoiceClient:
        """Conecta (ensordecido) o se mueve al canal, reutilizando la conexión si existe."""
        if voice is None or not voice.is_connected():
            voice = await channel.connect(timeout=CONNECT_TIMEOUT_SECONDS, self_deaf=True)
        elif voice.channel != channel:
            await voice.move_to(channel)
        else:
            return voice
        # Sin esto el clip sale antes de que el cifrado de voz esté listo y no se oye.
        await wait_until_audible(voice)
        await asyncio.sleep(PRE_ROLL_SECONDS)
        return voice

    async def _play(self, voice: discord.VoiceClient, packets: list[bytes]) -> None:
        """Reproduce los paquetes y espera a que terminen (con un tope de seguridad)."""
        await play_packets(voice, packets)

    async def cog_unload(self) -> None:
        """Cancela los reproductores en curso; su `finally` cierra la voz."""
        # Se vacían las colas antes de cancelar para que ningún `finally`
        # vuelva a arrancar un reproductor.
        for queue in self._pending.values():
            queue.clear()
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        for worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de sonidos de entrada en el cliente."""
    await bot.add_cog(Entrance(bot, bot.entrance_sounds))
