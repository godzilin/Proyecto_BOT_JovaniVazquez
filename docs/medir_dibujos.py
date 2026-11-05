"""Mide lo que tarda cada juego en tener su GIF listo, como en el NAS (4 hilos).

Para decidir con números antes y después de tocar el dibujo de un juego
(«Juego nuevo con dibujo» en CLAUDE.md). Por cada juego da dos tiempos, en ms:

- `1 abierto`: la primera jugada tras un rato sin jugar, con solo el proceso de
  Node que siempre está abierto (los demás se cierran a los 10 minutos).
- `caliente`: la mediana de 4 jugadas seguidas, con todos los procesos abiertos.

Uso, desde la raíz del repo (necesita `node` y `npm ci` hecho):

    python docs/medir_dibujos.py                     # todos los juegos, 4 hilos
    python docs/medir_dibujos.py moneda caballos     # solo esos
    python docs/medir_dibujos.py --repo ../viejo     # el código de otra copia

`--hilos N` (4 por defecto, los del N100) ata el proceso y los Node que abre a N
procesadores, saltando de dos en dos si hay hyperthreading; `--hilos 0` no ata
nada. Para comparar con la versión de antes: `git worktree add ../viejo
upstream/main` y medir las dos copias, mejor dos veces cada una (el ruido en
esta máquina ronda el 5 %). Con `--repo`, Node usa el `node_modules` de esta copia.

Un juego nuevo se añade a `GAMES` con su escena y una jugada de sus pruebas.
Solo usa lo instalado para las pruebas; no escribe nada.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent
HOT_PLAYS = 4


def pin(threads: int) -> None:
    """Ata este proceso (y los que abra) a `threads` procesadores."""
    count = os.cpu_count() or threads
    step = 2 if count >= 2 * threads else 1
    cpus = [i * step for i in range(min(threads, count))]
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, cpus)
    elif sys.platform == "win32":
        import ctypes

        mask = sum(1 << cpu for cpu in cpus)
        kernel = ctypes.windll.kernel32
        kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), mask)


def games() -> dict[str, tuple[Callable[[], Any], Callable[[Any], Awaitable[Any]]]]:
    """Cada juego: cómo crear su escena (con Node, sin Chromium) y una jugada de prueba."""
    from craps_fakes import game_after, table_for
    from test_allatu_scene import game_in
    from test_bacara_node import BEFORE, HEADER, THIRDS
    from test_backgammon_scene import META as BG_META
    from test_backgammon_scene import typical_turn
    from test_blackjack_scene import c, dealt, standing
    from test_bus_scene import CARDS
    from test_business_scene import samples as negocio_samples
    from test_coin_scene import lost_game
    from test_hold_win_scene import STAKE as BOTES_STAKE
    from test_hold_win_scene import bonus_step, find_spin, is_rescue_miss
    from test_horses_cog import fixed_result
    from test_horses_scene import card_of
    from test_housing_scene import NAMES, samples
    from test_housing_scene import market as housing_market
    from test_roulette_scene_browser import HISTORY, outcome
    from test_slots_scene import LINE_TEASE, LOSS, SEVENS
    from test_tower_scene import game_at
    from test_war_scene import tied, war_of, won

    from bot.cogs.hold_win import base_panel
    from bot.services import housing_view
    from bot.services.allatu import Phase
    from bot.services.allatu_scene import AllatuScene
    from bot.services.bacara_scene import BacaraScene
    from bot.services.backgammon_scene import BackgammonScene
    from bot.services.blackjack_scene import BlackjackScene
    from bot.services.bus_scene import BusScene
    from bot.services.business_scene import BusinessScene
    from bot.services.coin import Side
    from bot.services.coin_scene import CoinScene
    from bot.services.craps_scene import CrapsScene
    from bot.services.hold_win import Meters
    from bot.services.hold_win_scene import HoldWinScene, tease_of
    from bot.services.horses_scene import SceneRenderer
    from bot.services.housing_catalog import BARRIOS
    from bot.services.housing_scene import HousingScene
    from bot.services.lottery_scene import LotteryScene
    from bot.services.roulette_scene import RouletteScene
    from bot.services.rueda import Draw as WheelDraw
    from bot.services.rueda import layout as wheel_layout
    from bot.services.rueda_render import banner_for as wheel_banner
    from bot.services.rueda_render import meta_state as wheel_meta
    from bot.services.rueda_render import spin_states as wheel_states
    from bot.services.rueda_scene import RuedaScene
    from bot.services.slots_scene import SlotsScene
    from bot.services.tower import Difficulty
    from bot.services.tower_scene import TowerScene
    from bot.services.war_scene import WarScene

    no = "/no/existe/chromium"
    # Las muestras de la lotería viven en `docs/` (main lo pone en el path).
    from muestras_loteria import animations as lottery_animations
    from muestras_loteria import stills as lottery_stills

    lottery_anims, lottery_pngs = lottery_animations(), lottery_stills()

    async def tie_and_war(scene: WarScene) -> None:
        """Un empate y la guerra que sigue: lo más largo que se pinta en una mano."""
        await scene.deal(tied())
        await scene.war(war_of())

    spin = outcome(17, [("17", 100), ("rojo", 50)], {17: 200})
    panel = base_panel(Meters(), BOTES_STAKE)

    def botes(predicate):  # noqa: ANN001, ANN202
        found = find_spin(predicate)
        return lambda s: s.base("volcan", found, stake=BOTES_STAKE, before=panel, after=panel)

    def botes_bonus(s: HoldWinScene):  # noqa: ANN202
        game, before, step, pb, pa, finish = bonus_step(
            lambda pb, st, fin: fin is not None and fin.result.coins >= 10
        )
        return s.bonus_step(
            "volcan",
            before,
            step,
            stake=game.stake,
            panel_before=pb,
            panel_after=pa,
            finish=finish,
        )

    wedges = wheel_layout()
    wheel_spin = WheelDraw(4, wedges[4].prize.key, 0.4, 12345)
    wheel = wheel_states(wedges, wheel_spin, wheel_banner(wheel_spin.prize, wedges[4].prize.kind))
    showcase = {"key": "rueda_titulo_eje", "name": "Eje de la Rueda", "left": 7, "of": 10}

    return {
        # La Rueda: una vuelta sin tensión (se pinta antes del clic).
        "rueda": (
            lambda: RuedaScene(executable_path=no),
            lambda s: s.spin(wheel_meta(wedges, showcase=showcase, streak=3), *wheel),
        ),
        "moneda": (
            lambda: CoinScene(executable_path=no),
            lambda s: s.toss(lost_game(), start=Side.CARA, seed=3),
        ),
        "autobus": (lambda: BusScene(executable_path=no), lambda s: s.reveal(CARDS, 3, seed=4)),
        "dados": (
            lambda: CrapsScene(executable_path=no),
            lambda s: s.throw(table_for(game_after((1, 5))), seed=4),
        ),
        "bacara": (
            lambda: BacaraScene(executable_path=no),
            lambda s: s.coup(THIRDS, BEFORE, HEADER, seed=4),
        ),
        "ruleta": (
            lambda: RouletteScene(executable_path=no),
            lambda s: s.spin(spin, history=HISTORY, seed=3),
        ),
        # Repartir una mano sin empate, y un empate con su guerra (dos GIF seguidos).
        "guerra": (lambda: WarScene(executable_path=no), lambda s: s.deal(won())),
        # Un dragón en el piso 6 de Experto: tensión, fuego y la torre destapada.
        "torre": (
            lambda: TowerScene(executable_path=no),
            lambda s: s.pick(game_at(Difficulty.EXPERT, 5), 0),
        ),
        "guerra_2": (lambda: WarScene(executable_path=no), tie_and_war),
        # Blackjack: repartir (se pinta antes del clic) y plantarse con la banca robando
        # y el resultado (lo más largo de una mano sin barajar).
        "blackjack": (
            lambda: BlackjackScene(executable_path=no),
            lambda s: s.step(None, dealt(c(10), c(9, 1), c(6, 2), c(8, 3))),
        ),
        "blackjack_banca": (
            lambda: BlackjackScene(executable_path=no),
            lambda s: s.step(*standing(), streak=(1, 2)),
        ),
        # Allá tú: abrir una caja (se pinta antes del clic) y la llamada de la banca
        # (se pinta mientras se ve la caja).
        "allatu": (
            lambda: AllatuScene(executable_path=no),
            lambda s: s.open_box(game_in(Phase.OFFER), 21),
        ),
        "allatu_llamada": (
            lambda: AllatuScene(executable_path=no),
            lambda s: s.call(game_in(Phase.OFFER)),
        ),
        # Tragaperras: una tirada sin nada, una con el tercer rodillo lento, un ÉPICO
        # (lo más largo) y el doble o nada. Todas se pintan antes del clic.
        "tragas": (lambda: SlotsScene(executable_path=no), lambda s: s.spin(LOSS)),
        "tragas_lenta": (lambda: SlotsScene(executable_path=no), lambda s: s.spin(LINE_TEASE)),
        "tragas_epico": (
            lambda: SlotsScene(executable_path=no),
            lambda s: s.spin(SEVENS, won=20_000, stake=100),
        ),
        "tragas_doble": (
            lambda: SlotsScene(executable_path=no),
            lambda s: s.double(
                stops=LOSS.stops, amount=400, pick="red", won=True, history=["S"], fresh=True
            ),
        ),
        # Botes: una tirada sin nada, un rescate que se hace esperar y el final de un bonus
        # con su recuento. Las dos primeras se pintan antes del clic, y el bonus también.
        "botes": (
            lambda: HoldWinScene(executable_path=no),
            botes(lambda sp: not sp.points and tease_of(sp).kind is None),
        ),
        "botes_espera": (lambda: HoldWinScene(executable_path=no), botes(is_rescue_miss)),
        "botes_bonus": (lambda: HoldWinScene(executable_path=no), botes_bonus),
        "caballos": (
            lambda: SceneRenderer(executable_path=no),
            lambda s: s.race(card_of(), fixed_result((0, 1, 2, 3, 4, 5))),
        ),
        # Un turno contra Jovani: tu jugada con una comida, su tirada, su jugada y tus dados.
        "backgammon": (
            lambda: BackgammonScene(executable_path=no),
            lambda s: s.animate(BG_META, *typical_turn()),
        ),
        # El panel de `pisos`: una ficha (un clic) y los 20 barrios que se pintan por
        # adelantado al abrir el mapa.
        "pisos": (
            lambda: HousingScene(executable_path=no),
            lambda s: s.still(samples()["ficha_schamann_bajo"]),
        ),
        "pisos_20": (
            lambda: HousingScene(executable_path=no),
            lambda s: s.paint(
                [housing_view.barrio_state(housing_market(), b.key, 10, NAMES) for b in BARRIOS]
            ),
        ),
        # El negocio de `pala`: el inicio, la portada y un cliente del minijuego (este se
        # pinta mientras se atiende al de antes, y el primero antes de pulsar 🔑 Abrir).
        **{
            name: (
                lambda: BusinessScene(executable_path=no),
                lambda s, sample=sample: s.still(negocio_samples()[sample]),
            )
            for name, sample in {
                "negocio_dia": "dia_curro_y_negocio",
                "negocio": "negocio_cafeteria_centro",
                "negocio_cliente": "cliente_comandas",
                "negocio_taller": "cliente_diagnostico",
                "negocio_mejoras": "mejoras_cafeteria",
                "negocio_resenas": "resenas",
                "negocio_sociedad": "sociedad_sl",
                "negocio_plantilla": "plantilla_llena",
                "negocio_candidatos": "candidatos_oferta",
                "negocio_cadena": "cadena_cuatro_con_nave",
                "negocio_nave": "nave_cotiza_mal_aiem",
                "negocio_nave_alquilar": "nave_alquilar",
                "negocio_venta": "venta_con_comprador",
            }.items()
        },
        # La lotería: el rasca, un sorteo de cada tipo y las imágenes quietas del panel.
        **{
            f"loteria_{name}": (
                lambda: LotteryScene(executable_path=no),
                lambda s, sample=sample: s.animate(*lottery_anims[sample]),
            )
            for name, sample in {
                "rasca": "rasca_x10_premio",
                "sorteo": "sorteo_primitiva_bote",
                "nacional": "sorteo_navidad",
            }.items()
        },
        **{
            f"loteria_{name}": (
                lambda: LotteryScene(executable_path=no),
                lambda s, sample=sample: s.still(lottery_pngs[sample]),
            )
            for name, sample in {
                "decimo": "decimo_navidad",
                "boleto": "boleto_gordo_10",
                "cartel": "cartel",
            }.items()
        },
        # Cada una de las otras pantallas del panel de `pisos`.
        **{
            f"pisos_{screen}": (
                lambda: HousingScene(executable_path=no),
                lambda s, sample=sample: s.still(samples()[sample]),
            )
            for screen, sample in {
                "mapa": "mapa",
                "barrio": "barrio_schamann",
                "cartera": "cartera",
                "caja": "caja_compra",
                "vpo": "vpo_apuntada",
            }.items()
        },
    }


async def measure(
    make: Callable[[], Any], play: Callable[[Any], Awaitable[Any]]
) -> tuple[float, float]:
    """Milisegundos con un solo proceso abierto y en caliente (mediana)."""
    from bot.services import node_scene

    await node_scene.warm()
    scene = make()
    await play(scene)  # carga la escena en el proceso que siempre está abierto
    pool = node_scene._pool(None)
    for worker in list(pool.workers[node_scene.MIN_WORKERS :]):
        pool.workers.remove(worker)
        await worker.close()
    start = time.perf_counter()
    await play(scene)
    first = time.perf_counter() - start
    hot = []
    for _ in range(HOT_PLAYS):
        start = time.perf_counter()
        await play(scene)
        hot.append(time.perf_counter() - start)
    await scene.close()
    await node_scene.shutdown()
    return first * 1000, statistics.median(hot) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("juegos", nargs="*", help="Juegos a medir (todos si no se dice).")
    parser.add_argument("--repo", type=Path, default=HERE, help="Copia del repo a medir.")
    parser.add_argument("--hilos", type=int, default=4, help="Procesadores (0: sin atar).")
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.path[:0] = [
        str(repo / "src"),
        str(repo / "tests"),
        str(repo / "tests" / "unit"),
        str(repo / "docs"),
    ]
    os.environ.setdefault("NODE_PATH", str(HERE / "node_modules"))
    if args.hilos:
        pin(args.hilos)
    table = games()
    names = args.juegos or list(table)
    print(f"{'juego':9} {'1 abierto':>10} {'caliente':>9}")
    for name in names:
        first, hot = asyncio.run(measure(*table[name]))
        print(f"{name:9} {first:10.0f} {hot:9.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
