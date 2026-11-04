"""Muestras de las imágenes de `/deportes`: el boleto, la combinada y el marcador final.

Sirve para revisar la escena (`assets/deportes/escena.html`) a ojo y a las pruebas
(`tests/unit/test_sports_scene.py`), que importan `samples()`.

Uso (desde la raíz del repo, con PYTHONPATH=src):

    python docs/muestras_deportes.py <carpeta>

Deja un PNG por muestra en `<carpeta>` y dice lo que tardó cada una.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from bot.services.sports_scene import SportsScene
from bot.services.sports_teams import team_style

LALIGA, CHAMPIONS = "#ff4b44", "#3b6cff"


def _boleto(home: str, away: str, **extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "kind": "boleto",
        "league": "LaLiga",
        "accent": LALIGA,
        "home": team_style(home),
        "away": team_style(away),
        "kickoff": "sáb 1/11 · 21:00",
        "market": "Más/menos goles",
        "pick": "Más de 2,5 goles",
        "odds": "×1,72",
        "stake": "2.000 Y$",
        "payout": "3.440 Y$",
        "player": "Diego",
        "serial": "Nº 000123",
    }
    return {**state, **extra}


def _leg(home: str, away: str, **extra: Any) -> dict[str, Any]:
    leg: dict[str, Any] = {
        "league": "LaLiga",
        "accent": LALIGA,
        "home": team_style(home),
        "away": team_style(away),
        "kickoff": "sáb 1/11 · 21:00",
        "market": "Resultado",
        "pick": f"1 ({home})",
        "odds": "×1,85",
    }
    return {**leg, **extra}


def _combinada(legs: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "kind": "combinada",
        "accent": LALIGA,
        "legs": legs,
        "odds": "×7,58",
        "stake": "100 Y$",
        "payout": "758 Y$",
        "capped": False,
        "player": "Diego",
        "serial": "Nº C00012",
    }
    return {**state, **extra}


def _final(home: str, away: str, hg: int, ag: int, **extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "kind": "final",
        "league": "Champions",
        "accent": CHAMPIONS,
        "home": team_style(home),
        "away": team_style(away),
        "homeGoals": hg,
        "awayGoals": ag,
        "date": "mié 5/11",
        "chips": [
            ["Resultado", "1"],
            ["Goles", str(hg + ag)],
            ["Ambos marcan", "Sí"],
            ["Exacto", f"{hg}-{ag}"],
        ],
        "summary": "2 cobran · 3 pierden",
    }
    return {**state, **extra}


def samples() -> dict[str, dict[str, Any]]:
    """Un estado por caso: nombres cortos, largos, emojis, empate y equipo desconocido."""
    return {
        "boleto_laliga": _boleto("Real Madrid", "Barcelona"),
        "boleto_champions_largo": _boleto(
            "Real Racing Club de Santander",
            "ŠK Slovan Bratislava",
            league="Champions",
            accent=CHAMPIONS,
            market="Resultado exacto",
            pick="Gana ŠK Slovan Bratislava por 2-1",
            odds="×11,50",
            stake="1.250.000 Y$",
            payout="14.375.000 Y$",
            player="Ángel 🎰🔥 ☆",
            serial="Nº 987654",
        ),
        "boleto_desconocido": _boleto(
            "Peñarol Montevideo",
            "Club Atlético Nacional de Medellín",
            market="Resultado",
            pick="Empate",
            odds="×3,40",
        ),
        "combinada_2": _combinada(
            [
                _leg("Real Madrid", "Barcelona"),
                _leg(
                    "Sevilla",
                    "Betis",
                    market="Más/menos goles",
                    pick="Más de 2,5 goles",
                    odds="×1,72",
                ),
            ],
            odds="×3,18",
            payout="318 Y$",
        ),
        "combinada_5_mixta": _combinada(
            [
                _leg("Real Racing Club de Santander", "Atlético Madrid", odds="×2,10"),
                _leg(
                    "Sevilla",
                    "Villarreal",
                    market="Más/menos goles",
                    pick="Más de 2,5 goles",
                    odds="×1,72",
                    kickoff="dom 2/11 · 16:15",
                ),
                _leg(
                    "ŠK Slovan Bratislava",
                    "Bayern Munich",
                    league="Champions",
                    accent=CHAMPIONS,
                    market="Ambos marcan",
                    pick="Marcan los dos",
                    odds="×1,95",
                    kickoff="mar 5/11 · 21:00",
                ),
                _leg(
                    "Paris Saint Germain",
                    "Real Racing Club de Santander",
                    league="Champions",
                    accent=CHAMPIONS,
                    pick="X (empate)",
                    odds="×3,60",
                    kickoff="mié 6/11 · 18:45",
                ),
                _leg(
                    "Barcelona",
                    "ŠK Slovan Bratislava",
                    league="Champions",
                    accent=CHAMPIONS,
                    pick="1 (Barcelona)",
                    odds="×1,25",
                    kickoff="mié 6/11 · 21:00",
                ),
            ],
            odds="×37,64",
            stake="5.000 Y$",
            payout="188.200 Y$",
            serial="Nº C00345",
        ),
        "combinada_tope": _combinada(
            [
                _leg("Real Madrid", "Barcelona", odds="×12,40"),
                _leg("Atlético Madrid", "Sevilla", pick="X (empate)", odds="×9,80"),
                _leg("Villarreal", "Betis", pick="2 (Betis)", odds="×8,30"),
            ],
            odds="×1.008,6",
            stake="2.500.000 Y$",
            payout="1.000.000 Y$",
            capped=True,
            player="Ángel 🎰🔥 ☆",
            serial="Nº C99999",
        ),
        "final_3_1": _final("Bayern Munich", "Paris Saint Germain", 3, 1),
        "final_0_0": _final(
            "Atlético Madrid",
            "Sevilla",
            0,
            0,
            league="LaLiga",
            accent=LALIGA,
            chips=[["Resultado", "X"], ["Goles", "0"], ["Ambos marcan", "No"], ["Exacto", "0-0"]],
            summary="Nadie cobra",
        ),
        "final_largo": _final("Real Racing Club de Santander", "ŠK Slovan Bratislava", 12, 10),
        "final_desconocido": _final(
            "Peñarol Montevideo", "Zxqv United", 2, 2, chips=[["Resultado", "X"], ["Goles", "4"]]
        ),
    }


async def main(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    scene = SportsScene()
    try:
        await scene.still(samples()["boleto_laliga"])  # calienta Node
        for name, state in samples().items():
            start = time.perf_counter()
            png = await scene.still(state)
            took = time.perf_counter() - start
            if png is None:
                print(f"{name}: no se pudo pintar")
                continue
            (folder / f"{name}.png").write_bytes(png)
            print(f"{name}: {took * 1000:.0f} ms, {len(png) / 1024:.0f} KB")
    finally:
        await scene.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "muestras_deportes")))
