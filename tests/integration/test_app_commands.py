"""Pruebas de integración locales para el registro de slash commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from discord import app_commands

from bot.app import INITIAL_EXTENSIONS, BotClient, build_intents
from bot.cogs.general import build_help_embed
from bot.services.memes import EFFECTS

# Máxima longitud de un nombre de comando: deben ser cortos y fáciles de teclear.
MAX_COMMAND_NAME_LENGTH = 8

EXPECTED_COMMANDS = {
    "latencia",
    "entrada",
    "ayuda",
    "ranking",
    "poner",
    "pausar",
    "seguir",
    "saltar",
    "parar",
    "cola",
    "quitar",
    "vaciar",
    "volumen",
    "ruleta",
    "tragas",
    "volcan",
    "caballo",
    "cohete",
    "minas",
    "pollo",
    "moneda",
    "guagua",
    "dados",
    "pachinko",
    "loteria",
    "babel",
    "hongkong",
    "saldo",
    "imv",
    "hacienda",
    "renta",
    "fortunas",
    "apuestas",
    "porra",
    "bizum",
    "blackjack",
    "cumple",
    "cumples",
    "donar",
    "tienda",
    "mascota",
    "lista",
    "perfil",
    "pala",
    "beernight",
}

# Comandos de administración (cog `Admin`): también con `/` y con `.`.
ADMIN_COMMANDS = {
    "borrar",
    "bienv",
    "callar",
    "hablar",
    "echar",
    "banear",
    "indultar",
    "cerrar",
    "abrir",
    "lento",
    "decir",
    "apodo",
    "rol",
    "niveles",
    "cambios",
    "catalogo",
    "tajo",
}

# Comandos de imagen: solo de texto, para reservar los slash commands al resto.
TEXT_ONLY_COMMANDS = {"magik", "memes"}

# Utilidades de mantenimiento para unos pocos IDs: no salen en la ayuda.
HIDDEN_COMMANDS = {"reinicio"}


def test_comandos_slash_y_texto_comparten_nombres_cortos_y_sin_alias(tmp_path: Path) -> None:
    """`/x` y `.x` son el mismo comando: mismos nombres, cortos, planos y sin alias."""

    async def load_cogs() -> None:
        client = BotClient(
            command_prefix=".",
            database_path=tmp_path / "message_stats.sqlite3",
        )
        try:
            for extension in INITIAL_EXTENSIONS:
                await client.load_extension(extension)

            slash = client.tree.get_commands()
            slash_names = {command.name for command in slash}
            text_names = {c.name for c in client.commands}
            effects = {c.name for c in client.commands if "help_group" in c.extras}

            assert slash_names == EXPECTED_COMMANDS | ADMIN_COMMANDS | HIDDEN_COMMANDS
            assert text_names == slash_names | TEXT_ONLY_COMMANDS | set(EFFECTS)
            # Los efectos conservan el nombre de Dank Memer (algunos de más de
            # 8 letras), no ocupan slash commands y la ayuda los lista en bloque.
            assert effects == set(EFFECTS)
            assert all(isinstance(command, app_commands.Command) for command in slash)
            # Única excepción acordada: `.blackjack` con su atajo `.bj`.
            aliases = {c.name: c.aliases for c in client.commands if c.aliases}
            assert aliases == {"blackjack": ["bj"]}
            long_names = {"blackjack", "beernight"}
            assert all(
                len(name) <= MAX_COMMAND_NAME_LENGTH for name in text_names - effects - long_names
            )
            assert client.get_cog("Welcome") is not None
            assert client.get_cog("Music") is not None
            # Los de administración no aparecen en el menú `/` de quien no es admin.
            for command in slash:
                if command.name in ADMIN_COMMANDS | HIDDEN_COMMANDS:
                    assert command.default_permissions is not None
                    assert command.default_permissions.administrator
                    assert command.guild_only
        finally:
            await client.close()

    asyncio.run(load_cogs())


def test_la_ayuda_real_es_breve_y_respeta_los_limites_de_discord(tmp_path: Path) -> None:
    """La ayuda lista cada comando una vez, agrupada, y cabe en un embed."""

    async def check_help() -> None:
        client = BotClient(
            command_prefix=".",
            database_path=tmp_path / "message_stats.sqlite3",
        )
        try:
            for extension in INITIAL_EXTENSIONS:
                await client.load_extension(extension)

            embed = build_help_embed(client)
            admin_embed = build_help_embed(client, include_admin=True)

            def listed(fields: list) -> list[str]:
                values = " · ".join(field.value for field in fields)
                return [name.strip("`") for name in values.split(" · ")]

            assert [field.name for field in embed.fields] == [
                "⚙️ General (2)",
                "👤 Perfil (1)",
                "🎵 Música (9)",
                "📊 Niveles (1)",
                "🎰 Casino (22)",
                "🛍️ Tienda (2)",
                "🪏 Trabajo (1)",
                "🎂 Cumpleaños (2)",
                "📝 Lista (1)",
                "🍻 Beernight (1)",
                "🔔 Entradas (1)",
                "🎨 Imagen (2)",
                "🎨 Imagen · avatar (46)",
                "🎨 Imagen · avatar + texto (10)",
                "🎨 Imagen · texto (49)",
                "🎨 Imagen · vídeo (3)",
                "🗼 Diversión (2)",
            ]
            # Cada comando aparece una vez, solo por nombre y sin descripción.
            everyone = EXPECTED_COMMANDS | TEXT_ONLY_COMMANDS | set(EFFECTS)
            assert sorted(listed(embed.fields)) == sorted(everyone)
            assert "—" not in "".join(field.value for field in embed.fields)
            # Dentro de cada categoría, en orden alfabético.
            for field in embed.fields:
                names = listed([field])
                assert names == sorted(names)
            # La categoría de administración solo se enseña a administradores.
            assert admin_embed.fields[-1].name == f"🛡️ Admin ({len(ADMIN_COMMANDS)})"
            assert set(listed([admin_embed.fields[-1]])) == ADMIN_COMMANDS
            for result in (embed, admin_embed):
                assert all(len(field.value) <= 1024 for field in result.fields)
                assert len(result) <= 6000
                assert len(result.fields) <= 25
        finally:
            await client.close()

    asyncio.run(check_help())


def test_build_intents_habilita_eventos_de_miembros() -> None:
    """Se activa el intent necesario para detectar entradas y salidas."""
    assert build_intents().members


def test_build_intents_habilita_contenido_de_mensajes() -> None:
    """Se activa el intent necesario para leer comandos de texto con prefijo."""
    assert build_intents().message_content


def test_el_bot_usa_unicamente_el_prefijo_configurado(tmp_path: Path) -> None:
    """No hay prefijos ocultos: solo el configurado activa comandos de texto."""

    async def check_prefix() -> None:
        client = BotClient(command_prefix=".", database_path=tmp_path / "s.sqlite3")
        try:
            assert client.command_prefix == "."
        finally:
            await client.close()

    asyncio.run(check_prefix())
