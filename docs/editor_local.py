"""Editor del Jovaniverso en local: para probar el editor de assets sin Discord ni el bot.

Levanta en `http://localhost:8080` la aplicación web del cog de verdad
(`Universo(...).build_app()`) con un bot falso: un servidor con un único miembro
con permiso de editor y el mundo en un SQLite (`--db`, por defecto en el directorio temporal
del sistema, así que el borrador sobrevive entre ejecuciones). Añade la ruta
`GET /api/dev/sesion`, que da una sesión firmada de ese miembro; esa ruta solo existe
aquí.

    python docs/editor_local.py                 # servidor en :8080
    python docs/editor_local.py --db mundo.db   # con otra base de datos

Con el servidor en marcha, en otra terminal `npm run dev` dentro de `universo/` (Vite
reenvía `/api` a este servidor) y se abre `http://localhost:5173/?editor`: sale el
botón «Editor» y guarda y publica contra este servidor. La sala multijugador no
conecta en este modo (no hay Discord que le diga quién entra).
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from bot.cogs.universo import Universo
from bot.repositories.universo_mundo import UniversoMundoRepository

GUILD_ID = 1
USER_ID = 2


async def sesion(request: web.Request) -> web.Response:
    cog: Universo = request.app["cog"]
    return web.json_response({"session": cog.signer.issue(USER_ID), "guild_id": str(GUILD_ID)})


async def main(db: Path, port: int) -> None:
    repo = UniversoMundoRepository(db)
    await repo.initialize()
    await repo.set_permission(GUILD_ID, USER_ID, "editor", True, USER_ID)
    admin = SimpleNamespace(
        id=USER_ID,
        bot=False,
        display_name="Admin local",
        guild_permissions=SimpleNamespace(administrator=True),
    )
    guild = SimpleNamespace(id=GUILD_ID, name="Local", get_member=lambda _id: admin)
    bot = SimpleNamespace(
        universo_mundo=repo,
        application_id=1,
        telemetry=SimpleNamespace(count=lambda *_a, **_k: None),
        get_guild=lambda guild_id: guild if guild_id == GUILD_ID else None,
    )
    cog = Universo(bot, client_secret="local", port=port)  # type: ignore[arg-type]
    app = cog.build_app()
    app["cog"] = cog
    app.router.add_get("/api/dev/sesion", sesion)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "localhost", port).start()
    print(f"Editor local en http://localhost:{port} (base de datos: {db})")
    try:
        await asyncio.Event().wait()
    finally:
        await cog.salas.close()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=Path(tempfile.gettempdir()) / "jv_editor.db")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.db, args.port))
    except KeyboardInterrupt:
        pass
