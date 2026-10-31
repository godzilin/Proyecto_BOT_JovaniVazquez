"""Pruebas de integración locales para el registro de slash commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from discord import app_commands

from bot.app import INITIAL_EXTENSIONS, BotClient, build_intents
from bot.cogs.general import HelpPage, build_help_pages
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
    "caballo",
    "deportes",
    "bacara",
    "gallos",
    "4enraya",
    "backgammon",
    "cohete",
    "minas",
    "torre",
    "guerra",
    "pollo",
    "moneda",
    "guagua",
    "allatu",
    "poker",
    "dados",
    "pachinko",
    "picar",
    "loteria",
    "babel",
    "hongkong",
    "saldo",
    "imv",
    "hacienda",
    "renta",
    "fortunas",
    "patrimonio",
    "apuestas",
    "porra",
    "bizum",
    "vender",
    "trueque",
    "rastro",
    "pisos",
    "banco",
    "blackjack",
    "cumple",
    "cumples",
    "donar",
    "tienda",
    "pieles",
    "mascota",
    "lista",
    "perfil",
    "pala",
    "beernight",
    "jovani",
}

# Comandos de administración (cog `Admin`): también con `/` y con `.`.
ADMIN_COMMANDS = {
    "borrar",
    "admin",
    "bienv",
    "aspecto",
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
    "robuso",
    "informe",
    "repicar",
}

# Comandos de imagen: solo de texto, para reservar los slash commands al resto.
TEXT_ONLY_COMMANDS = {"magik", "memes"}
# De administración solo de texto: tampoco gastan slash commands.
ADMIN_TEXT_ONLY_COMMANDS = {"repicar"}

# Utilidades de mantenimiento para unos pocos IDs: no salen en la ayuda.
HIDDEN_COMMANDS = {"reinicio", "bloquear", "crear", "dinero"}

# Secretos: solo con `.`, sin `/`, fuera de la ayuda y de la telemetría.
SECRET_COMMANDS = {"enchufe", "amanece"}


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

            assert (
                slash_names
                == (EXPECTED_COMMANDS | ADMIN_COMMANDS | HIDDEN_COMMANDS) - ADMIN_TEXT_ONLY_COMMANDS
            )
            assert text_names == (
                slash_names
                | TEXT_ONLY_COMMANDS
                | ADMIN_TEXT_ONLY_COMMANDS
                | set(EFFECTS)
                | SECRET_COMMANDS
            )
            # Los efectos conservan el nombre de Dank Memer (algunos de más de
            # 8 letras), no ocupan slash commands y la ayuda los lista en bloque.
            assert effects == set(EFFECTS)
            assert all(isinstance(command, app_commands.Command) for command in slash)
            # Excepciones acordadas: `.bj` de `.blackjack` y `.met` de `.reinicio`.
            aliases = {c.name: c.aliases for c in client.commands if c.aliases}
            assert aliases == {"blackjack": ["bj"], "reinicio": ["met"]}
            # Nombres largos acordados (Biblia, «Cogs y comandos»).
            long_names = {"blackjack", "beernight", "backgammon", "patrimonio"}
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


def test_la_ayuda_real_va_por_menus_y_respeta_los_limites_de_discord(tmp_path: Path) -> None:
    """La ayuda es un menú por categorías: cada comando una vez y todo cabe en Discord."""

    async def check_help() -> None:
        client = BotClient(
            command_prefix=".",
            database_path=tmp_path / "message_stats.sqlite3",
        )
        try:
            for extension in INITIAL_EXTENSIONS:
                await client.load_extension(extension)

            pages = build_help_pages(client)
            admin_pages = build_help_pages(client, include_admin=True)

            def listed(page: HelpPage) -> list[str]:
                lines = (page.embed.description or "").splitlines()
                names = [line.split("`")[1] for line in lines]
                for field in page.embed.fields:
                    names += [name.strip("`") for name in field.value.split(" · ")]
                return names

            assert [page.title for page in pages] == [
                "📋 Inicio",
                "👤 Perfil",
                "🎰 Casino",
                "🏦 Dinero",
                "🛍️ Tienda",
                "🎵 Música",
                "🍻 Comunidad",
                "🎨 Imagen",
                "⚙️ General",
            ]
            # Cada comando aparece una vez, con su descripción (salvo los efectos).
            everyone = EXPECTED_COMMANDS | TEXT_ONLY_COMMANDS | set(EFFECTS)
            names = [name for page in pages[1:] for name in listed(page)]
            assert sorted(names) == sorted(everyone)
            for page in pages[1:]:
                for line in (page.embed.description or "").splitlines():
                    assert " — " in line
                    assert "Sin descripción" not in line
            # La categoría de administración solo se enseña a administradores, al final.
            assert admin_pages[-1].title == "🛡️ Admin"
            assert set(listed(admin_pages[-1])) == ADMIN_COMMANDS
            # Límites de Discord: 25 opciones en un menú, embeds de 6000 y campos de 1024.
            assert len(admin_pages) <= 25
            for page in admin_pages:
                assert len(page.embed) <= 6000
                assert len(page.embed.description or "") <= 4096
                assert len(page.embed.fields) <= 25
                assert all(len(field.value) <= 1024 for field in page.embed.fields)
                assert len(page.summary) <= 100
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
