"""Baja una captura de Polymarket para las pruebas de `bot.services.polymarket`.

Las pruebas no tienen red, así que leen datos reales guardados en
`tests/fixtures/polymarket/`. Este script los vuelve a bajar: eventos por su `slug`
(Gamma API) y un libro de órdenes (CLOB API), y los recorta a los campos que usa el bot
para que la captura pese poco. Lo que no se recorta se queda tal cual llega (los números
como texto, `outcomes` y `outcomePrices` como texto JSON), porque eso es lo que tiene que
saber leer el cliente.

Uso, desde la raíz del repositorio (necesita red hacia `gamma-api.polymarket.com` y
`clob.polymarket.com`; no hace falta clave):

    python docs/captura_polymarket.py

Escribe un archivo por grupo de `CAPTURES` y `clob_libro.json`, y sobrescribe los que
hubiera. Los partidos de `CAPTURES` son de noviembre de 2026: si se vuelve a bajar más
tarde, los que estaban abiertos saldrán cerrados y algunas pruebas cambiarán de sentido.
Para una captura nueva, cambia los `slug` (salen en la URL de cada evento en
polymarket.com) y repasa las pruebas.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "polymarket"

# Grupo de la captura -> slugs de sus eventos.
CAPTURES: dict[str, tuple[str, ...]] = {
    # Un partido de LaLiga por jugar, con todos sus eventos de mercados.
    "partido_abierto": (
        "lal-sev-ala-2026-11-06",
        "lal-sev-ala-2026-11-06-more-markets",
        "lal-sev-ala-2026-11-06-exact-score",
        "lal-sev-ala-2026-11-06-halftime-result",
        "lal-sev-ala-2026-11-06-first-to-score",
        "lal-sev-ala-2026-11-06-total-corners",
    ),
    # Partidos terminados, con marcador y mercados resueltos.
    "partidos_cerrados": (
        "lal-vil-esp-2026-10-30",
        "lal-bet-mala-2026-10-31",
        "lal-bar-ala-2026-10-31",
    ),
    # Un partido del 10 de octubre que Gamma aún da por abierto (un «zombi»).
    "partido_zombi": ("lal-ray-bil-2026-10-10",),
    # Política: varias opciones con candidatos de relleno, un Sí/No y fechas escalonadas.
    "elecciones": (
        "spain-general-election-winner",
        "spain-general-election-pp-vox-majority",
        "spain-general-election-las-palmas-winner",
        "pedro-snchez-out-as-pm-of-spain-by",
    ),
}

# Libro de órdenes de una opción con mucho movimiento («Xi Jinping out before 2027?», Sí).
BOOK_TOKEN = "32338220190071351435772801779725302244575775216413325951443816017994629993401"
BOOK_LEVELS = 3

EVENT_KEYS = (
    "id", "slug", "title", "description", "startTime", "endDate", "negRisk", "tags",
    "image", "volume24hr", "active", "closed", "score", "period", "ended", "live", "gameId", "markets",
)
MARKET_KEYS = (
    "id", "slug", "question", "description", "groupItemTitle", "outcomes",
    "outcomePrices", "clobTokenIds", "positionIds", "version", "bestBid", "bestAsk",
    "spread", "lastTradePrice", "liquidityNum", "volume24hr", "endDate", "gameStartTime",
    "sportsMarketType", "active", "closed", "acceptingOrders", "umaResolutionStatus",
    "umaResolutionStatuses", "closedTime", "negRisk", "image",
)
# Las descripciones son largas (reglas de resolución): con el principio basta.
DESCRIPTION_CHARS = 400


def _get(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "bot-captura/1.0"})
    with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310 (URL fija)
        return json.load(reply)


def _trim(raw: dict, keys: tuple[str, ...]) -> dict:
    out = {key: raw[key] for key in keys if key in raw}
    if isinstance(out.get("description"), str):
        out["description"] = out["description"][:DESCRIPTION_CHARS]
    if isinstance(out.get("tags"), list):
        out["tags"] = [
            {"label": tag.get("label"), "slug": tag.get("slug")}
            for tag in out["tags"]
            if isinstance(tag, dict)
        ]
    return out


def event(slug: str) -> dict:
    """Un evento por su slug, recortado, con sus mercados recortados."""
    raw = _get(f"{GAMMA_URL}/events/slug/{urllib.parse.quote(slug)}")
    if not isinstance(raw, dict):
        raise SystemExit(f"{slug}: Gamma no devuelve un evento")
    out = _trim(raw, EVENT_KEYS)
    out["markets"] = [_trim(m, MARKET_KEYS) for m in raw.get("markets", [])]
    return out


def book(token: str) -> dict:
    """El libro de órdenes de una opción, con solo los mejores niveles de cada lado.

    CLOB manda las compras de menor a mayor precio y las ventas de mayor a menor, así que
    los mejores precios son los últimos de cada lista.
    """
    raw = _get(f"{CLOB_URL}/book?token_id={token}")
    if not isinstance(raw, dict):
        raise SystemExit("CLOB no devuelve un libro")
    return {
        "asset_id": raw.get("asset_id"),
        "bids": raw.get("bids", [])[-BOOK_LEVELS:],
        "asks": raw.get("asks", [])[-BOOK_LEVELS:],
        "timestamp": raw.get("timestamp"),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, slugs in CAPTURES.items():
        events = [event(slug) for slug in slugs]
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(events, ensure_ascii=False, indent=1) + "\n", "utf-8")
        markets = sum(len(e["markets"]) for e in events)
        print(f"{path.name}: {len(events)} eventos, {markets} mercados")
    path = OUT / "clob_libro.json"
    path.write_text(json.dumps(book(BOOK_TOKEN), indent=1) + "\n", "utf-8")
    print(f"{path.name}: libro de {BOOK_TOKEN[:12]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
