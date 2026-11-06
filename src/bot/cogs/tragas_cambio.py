"""🔀 Otras tragaperras: cambiar de máquina sin cerrar el mensaje.

Lo usan la clásica (`bot.cogs.slots.SlotMachineView`) y las de Botes
(`bot.cogs.hold_win.HoldWinView`) a través de `SwitchMixin`. No es una extensión:
no tiene `setup` y no lo carga `INITIAL_EXTENSIONS`.

Cómo va:

1. El botón 🔀 cambia los botones de la máquina por un desplegable con las demás
   tragaperras y un ↩️ Volver. Solo toca memoria: contesta con `edit_message`.
2. Al elegir una, se acepta el clic (`ack`: abrirla lee la base de datos y pinta la
   imagen) y la máquina nueva se abre en el mismo mensaje con la misma apuesta. Si
   esa apuesta no vale allí (la mínima de Botes, el saldo), se abre con la de por
   defecto.
3. La máquina vieja se retira (`retire`: su ticket y sus logros, como al cerrarse)
   y deja de atender clics.

No se cambia con algo a medias que se perdería (giros gratis, un bonus de Botes) ni
con una tirada en marcha: el botón lo explica con un aviso privado.

Las máquinas se abren con `Slots._open_machine`, que ya sabe abrir la clásica y
las de `THEMES`. El cog se busca con `find_cog` e importando `bot.cogs.slots` dentro
de la función: `slots` importa `hold_win`, y `hold_win` importa este módulo.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord

from bot.cogs import achievements as logros
from bot.services.achievements import slots_switch_stats
from bot.services.hold_win import THEMES
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)

#: Clave de la tragaperras clásica en `tragas`; las demás son las de `THEMES`.
CLASSIC = "clasica"
SWITCH_LABEL = "🔀 Otras tragaperras"
BUSY_TEXT = "Espera a que acabe la tirada para cambiar de máquina."


def machine_keys() -> list[str]:
    """Todas las tragaperras, en el orden del desplegable: la clásica y las de Botes."""
    return [CLASSIC, *THEMES]


def machine_options(current: str) -> list[discord.SelectOption]:
    """Opciones del desplegable: todas las tragaperras menos la que está abierta."""
    options = []
    for key in machine_keys():
        if key == current:
            continue
        if key == CLASSIC:
            options.append(
                discord.SelectOption(
                    label="Clásica",
                    value=CLASSIC,
                    emoji="🎰",
                    description="Bote común del servidor, giros gratis y doble o nada",
                )
            )
            continue
        theme = THEMES[key]
        names = list(theme.bonus_names.values())
        options.append(
            discord.SelectOption(
                label=theme.title,
                value=key,
                emoji=theme.emoji,
                description=f"Monedas y botes: {', '.join(names[:-1])} y {names[-1]}",
            )
        )
    return options


async def open_machine(
    bot: commands.Bot,
    key: str,
    *,
    guild: discord.Guild | None,
    channel: object,
    user: discord.abc.User,
    amount_text: str | None,
    send: Callable[..., Awaitable[discord.Message]],
    send_error: Callable[[str], Awaitable[None]],
) -> bool:
    """Abre la tragaperras `key` con `Slots._open_machine`. `False` si el cog no está."""
    from bot.cogs.slots import Slots

    cog = find_cog(bot, Slots)
    if cog is None:
        return False
    await cog._open_machine(
        key,
        guild=guild,
        channel=channel,
        user=user,
        amount_text=amount_text,
        send=send,
        send_error=send_error,
    )
    return True


class SwitchMixin:
    """El botón 🔀 Otras tragaperras de una máquina y su desplegable.

    La vista que lo usa (siempre antes de `discord.ui.View` en las bases) da:

    - `machine_key`: su clave (`clasica`, `volcan`…).
    - `switch_prefix`: el prefijo de sus `custom_id`, para que el rescate de clics del
      cog `Errors` encuentre el desplegable y el ↩️ Volver al rehacer la vista.
    - `_build_buttons()`: añade sus botones (y llama a `add_switch_button`).
    - `switch_blocker()`: el aviso si hay algo a medias que impide cambiar, o `None`.
    - `retire()`: se cierra por el cambio y devuelve el ticket que enseñar, si hay.
    - `_busy`, `stake`, `guild_id`, `owner` y `cog` (con `bot`).
    """

    picking: bool = False

    # Lo da la vista; se declara para que se vea qué usa el mixin.
    machine_key: str
    switch_prefix: str
    _busy: bool
    picker: discord.ui.Select
    stake: int
    guild_id: int
    owner: discord.abc.User
    cog: Any

    def add_switch_button(self, row: int) -> discord.ui.Button:
        """Añade el botón 🔀 en la fila `row`."""
        button: discord.ui.Button = discord.ui.Button(
            label=SWITCH_LABEL, row=row, custom_id=f"{self.switch_prefix}:other"
        )
        button.callback = self._open_picker  # type: ignore[method-assign]
        self.add_item(button)  # type: ignore[attr-defined]
        return button

    def switch_blocker(self) -> str | None:
        """Aviso si ahora no se puede cambiar de máquina; lo redefine cada vista."""
        return None

    async def retire(self) -> str | None:
        """Cierra la máquina tras cambiar a otra; lo redefine cada vista."""
        raise NotImplementedError

    def _build_buttons(self) -> None:
        raise NotImplementedError

    def leave_picker(self) -> None:
        """Si el desplegable está a la vista, vuelve a poner los botones de la máquina."""
        if not self.picking:
            return
        self.picking = False
        self.clear_items()  # type: ignore[attr-defined]
        self._build_buttons()

    def _show_picker(self) -> None:
        self.picking = True
        self.clear_items()  # type: ignore[attr-defined]
        picker: discord.ui.Select = discord.ui.Select(
            placeholder="Elige otra tragaperras",
            options=machine_options(self.machine_key),
            custom_id=f"{self.switch_prefix}:pick",
            row=0,
        )
        picker.callback = self._pick  # type: ignore[method-assign]
        self.picker = picker
        self.add_item(picker)  # type: ignore[attr-defined]
        back: discord.ui.Button = discord.ui.Button(
            label="↩️ Volver", row=1, custom_id=f"{self.switch_prefix}:back"
        )
        back.callback = self._close_picker  # type: ignore[method-assign]
        self.add_item(back)  # type: ignore[attr-defined]

    def _cannot_switch(self) -> str | None:
        return BUSY_TEXT if self._busy else self.switch_blocker()

    async def _open_picker(self, interaction: discord.Interaction) -> None:
        """🔀: cambia los botones por el desplegable. Solo memoria: sin `ack`."""
        if reason := self._cannot_switch():
            await interaction.response.send_message(reason, ephemeral=True)
            return
        self._show_picker()
        await interaction.response.edit_message(view=self)  # type: ignore[arg-type]

    async def _close_picker(self, interaction: discord.Interaction) -> None:
        """↩️ Volver: los botones de la máquina, tal como estaban."""
        self.leave_picker()
        await interaction.response.edit_message(view=self)  # type: ignore[arg-type]

    async def _pick(self, interaction: discord.Interaction) -> None:
        """Abre la máquina elegida en este mismo mensaje y retira esta."""
        key = self.picker.values[0] if self.picker.values else None
        if self._busy:
            # Un segundo clic mientras se abre la otra: si devolviera los botones de
            # esta, podría tapar la máquina nueva. Se acepta sin hacer nada.
            await ack(interaction)
            return
        if reason := self.switch_blocker():
            self.leave_picker()
            await interaction.response.edit_message(view=self)  # type: ignore[arg-type]
            await notify(interaction, reason)
            return
        await ack(interaction)
        self._busy = True
        sent = False
        errors: list[str] = []

        async def send(**kwargs: Any) -> discord.Message:
            nonlocal sent
            file = kwargs.pop("file", None)
            if file is not None:
                kwargs["attachments"] = [file]
            await edit(interaction, **kwargs)
            sent = True
            assert interaction.message is not None
            return interaction.message

        async def send_error(text: str) -> None:
            errors.append(text)

        bot = self.cog.bot
        try:
            if key is not None:
                # Con la apuesta de aquí y, si allí no vale, con la de por defecto.
                for amount in (str(self.stake), None):
                    opened = await open_machine(
                        bot,
                        key,
                        guild=interaction.guild,
                        channel=interaction.channel,
                        user=interaction.user,
                        amount_text=amount,
                        send=send,
                        send_error=send_error,
                    )
                    if sent or not opened:
                        break
        except Exception:
            logger.exception("No se pudo cambiar a la tragaperras %s", key)
        finally:
            self._busy = False
        if not sent:
            self.leave_picker()
            await edit(interaction, view=self)  # type: ignore[arg-type]
            await notify(interaction, errors[-1] if errors else "Esa máquina está en el taller.")
            return
        ticket = await self.retire()
        if ticket:
            await notify(interaction, ticket)
        await logros.track(
            bot, self.guild_id, self.owner, interaction.channel, slots_switch_stats(key or "")
        )
