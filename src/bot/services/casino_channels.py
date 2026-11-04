"""Canales del casino por servidor, guardados en memoria.

Los administradores eligen desde Discord dónde se puede jugar. `CasinoChannels`
carga lo guardado al arrancar y lo sirve sin tocar la base de datos, porque
`ids` se consulta en cada jugada. Las escrituras actualizan la memoria antes de
esperar al disco, así dos llamadas seguidas ven ya el estado nuevo.

Significado de un conjunto: vacío = cualquier canal. Un servidor sin fila usa
`default` (lo que venga de `CASINO_CHANNEL_IDS`).
"""

from __future__ import annotations

from bot.repositories.casino_settings import CasinoSettingsRepository


class CasinoChannels:
    """Canales del casino por servidor, en memoria.

    Args:
        repository: Persistencia de los canales.
        default: Canales de los servidores sin fila guardada.
    """

    def __init__(
        self,
        repository: CasinoSettingsRepository,
        default: frozenset[int] = frozenset(),
    ) -> None:
        self.repository = repository
        self.default = default
        self._by_guild: dict[int, frozenset[int]] = {}

    async def load(self) -> None:
        """Carga lo guardado; se llama una vez al arrancar."""
        self._by_guild = await self.repository.all_channels()

    def ids(self, guild_id: int | None) -> frozenset[int]:
        """Canales permitidos (vacío = cualquiera). Solo mira la memoria."""
        if guild_id is None:
            return self.default
        return self._by_guild.get(guild_id, self.default)

    def configured(self, guild_id: int) -> bool:
        """Indica si el servidor tiene sus canales guardados."""
        return guild_id in self._by_guild

    async def toggle(self, guild_id: int, channel_id: int) -> tuple[bool, frozenset[int]]:
        """Añade el canal si no estaba y lo quita si estaba.

        Returns:
            Si el canal quedó añadido y el conjunto nuevo.
        """
        current = self.ids(guild_id)
        added = channel_id not in current
        new = current | {channel_id} if added else current - {channel_id}
        self._by_guild[guild_id] = new
        await self.repository.set_channels(guild_id, new)
        return added, new

    async def clear(self, guild_id: int) -> None:
        """Permite cualquier canal, aunque `default` tenga canales."""
        self._by_guild[guild_id] = frozenset()
        await self.repository.set_channels(guild_id, frozenset())

    async def forget(self, guild_id: int) -> None:
        """Borra lo guardado: el servidor vuelve a `default`."""
        self._by_guild.pop(guild_id, None)
        await self.repository.forget(guild_id)


type CasinoChannelIds = frozenset[int] | CasinoChannels


def allowed_ids(rule: CasinoChannelIds, guild_id: int | None) -> frozenset[int]:
    """Conjunto de canales permitidos.

    Args:
        rule: Un conjunto fijo (las pruebas lo pasan así) o el servicio.
        guild_id: Servidor que pregunta.
    """
    if isinstance(rule, frozenset | set):
        return frozenset(rule)
    return rule.ids(guild_id)
