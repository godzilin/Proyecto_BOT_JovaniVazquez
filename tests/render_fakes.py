"""Dobles de los dibujantes de las máquinas del casino, para pruebas que no miran la imagen.

`SlotsScene`, `HoldWinScene`, `WheelRenderer`, `RouletteScene` y `PachinkoRenderer` tardan entre
0,05 y 3 s por tirada (más el precalentamiento de los tres al cargar el cog, que dibuja las 38
casillas de la ruleta o las piezas de cada tablero en segundo plano). Las pruebas de los botones
(`tests/integration/test_button_speed.py`) y de los puentes entre cogs
(`tests/integration/test_cog_bridges.py`) solo miran el orden de las respuestas, la
base de datos, los logros y las jugadas: la imagen no entra en ninguna aserción.

`use_fake_drawings` cambia esas clases (y las del pozo de `picar`, la Torre del dragón,
Allá tú, la Guerra, la lotería, el blackjack, los negocios de `pala` y el retrato de
`mascota`) en sus módulos de
servicio ANTES de cargar los cogs. `load_extension` vuelve a ejecutar cada cog y su
`from ... import SlotsScene` recoge el doble, así que el cog, el bot y la base de datos
son los reales y solo se sustituye el dibujo. Los dobles devuelven los mismos tipos
(`SlotsMedia`, `SpinMedia`, `PachinkoMedia`, `Media` de Botes) con bytes de relleno y
`seconds=0.0`, y
respetan `turbo` (GIF vacío), que es lo que el cog mira para decidir qué enseña. Las
pruebas del dibujo de verdad están en `tests/unit/test_*_render.py`, que no usan esto.

Se importa como `from render_fakes import use_fake_drawings` (`pyproject.toml` añade
`tests/` al path de importación de pytest).
"""

from __future__ import annotations

import pytest

import bot.services.allatu_scene as allatu_scene
import bot.services.blackjack_scene as blackjack_scene
import bot.services.business_scene as business_scene
import bot.services.hold_win_render as hold_win_render
import bot.services.hold_win_scene as hold_win_scene
import bot.services.lottery_scene as lottery_scene
import bot.services.pachinko_render as pachinko_render
import bot.services.pet_scene as pet_scene
import bot.services.picar_scene as picar_scene
import bot.services.renta_scene as renta_scene
import bot.services.roulette_render as roulette_render
import bot.services.roulette_scene as roulette_scene
import bot.services.slots_render as slots_render
import bot.services.slots_scene as slots_scene
import bot.services.sports_scene as sports_scene
import bot.services.tower_scene as tower_scene
import bot.services.war_scene as war_scene
from bot.services.allatu_scene import Media as AllatuMedia
from bot.services.bacara_render import Media as WarMedia
from bot.services.blackjack_scene import Media as BlackjackMedia
from bot.services.lottery_render import Media as LotteryMedia
from bot.services.picar_scene import Part as PicarPart
from bot.services.tower_scene import Media as TowerMedia


class FakeSlotsScene:
    """`SlotsScene` sin dibujar: PNG y GIF de relleno."""

    async def spin(
        self,
        spin: object,
        *,
        turbo: bool = False,
        won: int = 0,
        stake: int = 0,
        respin: bool = False,
    ) -> slots_render.SlotsMedia:
        return slots_render.SlotsMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    async def still(self, spin: object, *, highlight: bool | None = None) -> bytes:
        return b"PNG"

    async def double(
        self,
        *,
        stops: object,
        amount: int,
        pick: str,
        won: bool,
        history: object,
        fresh: bool,
    ) -> slots_render.SlotsMedia | None:
        return slots_render.SlotsMedia(gif=b"GIF", png=b"PNG", seconds=0.0)

    async def close(self) -> None:
        return None


class FakeHoldWinScene:
    """`HoldWinScene` sin dibujar: PNG y GIF de relleno (el GIF vacío en turbo).

    Las cuatro tiradas de las máquinas de Botes (la base, la entrada al bonus, una del bonus
    y la quieta) devuelven bytes fijos y duran 0 s.
    """

    @property
    def disabled(self) -> bool:
        return False

    async def base(
        self,
        theme: str,
        spin: object,
        *,
        stake: int,
        before: object,
        after: object,
        banner: object = None,
        turbo: bool = False,
    ) -> hold_win_render.Media:
        return hold_win_render.Media(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    async def still(
        self, theme: str, spin: object, *, stake: int, panel: object, highlight: bool = False
    ) -> bytes:
        return b"PNG"

    async def bonus_intro(
        self,
        theme: str,
        spin: object,
        *,
        base_stake: int,
        before: object,
        after: object,
        game: object,
        panel: object,
        name: str,
        banner: object,
        turbo: bool = False,
    ) -> hold_win_render.Media:
        return hold_win_render.Media(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    async def bonus_step(
        self,
        theme: str,
        before: object,
        step: object,
        *,
        stake: int,
        panel_before: object,
        panel_after: object,
        finish: object = None,
        banner: object = None,
        turbo: bool = False,
    ) -> hold_win_render.Media:
        return hold_win_render.Media(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    async def close(self) -> None:
        return None


class FakeWheelRenderer:
    """`WheelRenderer` sin dibujar: ni la casilla ni el precalentamiento cuestan nada."""

    def media(self, pocket: int) -> roulette_render.SpinMedia:
        return roulette_render.SpinMedia(gif=b"GIF", png=b"PNG")

    def idle_png(self) -> bytes:
        return b"PNG"


class FakeRouletteScene:
    """`RouletteScene` sin abrir Chromium: PNG y GIF de relleno y giro de 0 s."""

    async def spin(
        self, outcome: object, *, history: object, seed: int, skin: object = None
    ) -> roulette_scene.Media:
        return roulette_scene.Media(gif=b"GIF", png=b"PNG", seconds=0.0)

    async def board(self, history: object, wagers: object = (), *, skin: object = None) -> bytes:
        return b"PNG"

    async def close(self) -> None:
        return None


class FakePachinkoRenderer:
    """`PachinkoRenderer` sin dibujar: PNG y GIF de relleno, y sin piezas que precalentar."""

    def warm_up(self) -> None:
        return None

    def idle_png(self, board: object) -> bytes:
        return b"PNG"

    def still_png(self, volley: object, motion: object = None) -> bytes:
        return b"PNG"

    def render(
        self, volley: object, *, turbo: bool = False, motion: object = None
    ) -> pachinko_render.PachinkoMedia:
        return pachinko_render.PachinkoMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)


class FakeBlackjackScene:
    """`BlackjackScene` sin dibujar: apunta lo que se le pide y devuelve un PNG de relleno.

    Sin GIF (`gif=None`, como el plan B de Pillow): cada jugada acaba en una sola edición
    con el PNG, sin esperar al final de una animación.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls: list[tuple[object, object]] = []

    async def step(
        self,
        before: object,
        after: object,
        *,
        shoe: object = None,
        streak: object = (0, 0),
        skin: object = None,
    ) -> BlackjackMedia:
        self.calls.append((before, after))
        return BlackjackMedia(gif=None, png=b"PNG", seconds=0.0)

    async def still(self, game: object, *, streak: int = 0, skin: object = None) -> bytes:
        return b"PNG"

    async def close(self) -> None:
        return None


class FakeWarScene:
    """`WarScene` sin dibujar: apunta lo que se le pide y devuelve un PNG de relleno.

    Sin GIF (`gif=None`, como el plan B de Pillow): cada jugada acaba en una sola edición
    con el PNG, sin esperar al final de una animación.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls: list[tuple[str, object]] = []

    async def _media(self, name: str, game: object) -> WarMedia:
        self.calls.append((name, game))
        return WarMedia(gif=None, png=b"PNG", seconds=0.0)

    async def deal(self, game: object, *, skin: object = None) -> WarMedia:
        return await self._media("deal", game)

    async def war(self, game: object, *, skin: object = None) -> WarMedia:
        return await self._media("war", game)

    async def surrender(self, game: object, *, skin: object = None) -> WarMedia:
        return await self._media("surrender", game)

    async def still(self, game: object, *, skin: object = None) -> bytes:
        return b"PNG"

    async def close(self) -> None:
        return None


class FakePicarScene:
    """El pozo de `picar` sin dibujo: un solo GIF de relleno que dura 0 s y un PNG."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def parts(self, outcome: object, values: object, *, budget: int):  # noqa: ANN201
        self.calls.append(("parts", outcome, budget))
        yield PicarPart(gif=b"gif", ms=0, hold_ms=0, last=True, frames=1)

    async def still(self, outcome: object, values: object) -> bytes:
        self.calls.append(("still", outcome))
        return b"png"

    async def close(self) -> None:
        return None


class FakeTowerScene:
    """La Torre del dragón sin dibujo: un PNG y un GIF de relleno que duran 0 s.

    Con `images=False` hace como si no hubiera Node ni navegador (todo `None`) y
    la torre se queda con texto.
    """

    def __init__(self, *args: object, images: bool = True, **kwargs: object) -> None:
        self.images = images
        self.calls: list[tuple[object, ...]] = []

    def _media(self, *call: object) -> TowerMedia | None:
        self.calls.append(call)
        return TowerMedia(gif=b"gif", png=b"png", seconds=0.0) if self.images else None

    async def opening(self, difficulty: object, stake: object) -> bytes | None:
        self.calls.append(("opening", difficulty, stake))
        return b"png" if self.images else None

    async def still(self, game: object) -> bytes | None:
        self.calls.append(("still", game))
        return b"png" if self.images else None

    async def pick(self, game: object, door: int, *, roulette: bool = False) -> TowerMedia | None:
        return self._media("pick", door, roulette)

    async def cashout(self, game: object) -> TowerMedia | None:
        return self._media("cashout")

    async def close(self) -> None:
        return None


class FakeAllatuScene:
    """Allá tú sin dibujo: PNG y GIF de relleno que duran 0 s, y apunta cada dibujo.

    Con `images=False` hace como si no hubiera Node ni navegador (todo `None`) y
    el plató se queda con texto.
    """

    def __init__(self, *args: object, images: bool = True, **kwargs: object) -> None:
        self.images = images
        self.calls: list[tuple[object, ...]] = []

    @property
    def disabled(self) -> bool:
        return not self.images

    def _media(self, *call: object) -> AllatuMedia | None:
        self.calls.append(call)
        return AllatuMedia(gif=b"gif", png=b"png", seconds=0.0) if self.images else None

    def _png(self, *call: object) -> bytes | None:
        self.calls.append(call)
        return b"png" if self.images else None

    async def still(self, game: object) -> bytes | None:
        return self._png("still")

    async def picks(self, game: object) -> list[bytes] | None:
        self.calls.append(("picks",))
        return [b"png"] * 22 if self.images else None

    async def opening(self, game: object) -> AllatuMedia | None:
        return self._media("opening")

    async def open_box(self, game: object, box: int) -> AllatuMedia | None:
        return self._media("open_box", box)

    async def call(self, game: object) -> AllatuMedia | None:
        return self._media("call")

    async def deal(self, game: object) -> AllatuMedia | None:
        return self._media("deal")

    async def haggle(self, game: object) -> AllatuMedia | None:
        return self._media("haggle")

    async def no_deal(self, game: object) -> bytes | None:
        return self._png("no_deal")

    async def final(self, game: object, *, swap: bool) -> AllatuMedia | None:
        return self._media("final", swap)

    async def close(self) -> None:
        return None


class FakeBusinessScene:
    """`BusinessScene` sin dibujar: un PNG de relleno por pantalla."""

    @property
    def disabled(self) -> bool:
        return False

    async def paint(self, states: list[dict[str, object]]) -> list[bytes] | None:
        return [b"PNG" for _state in states]

    async def still(self, state: dict[str, object]) -> bytes | None:
        return b"PNG"

    async def close(self) -> None:
        return None


class FakeSportsScene:
    """`SportsScene` sin dibujar: un PNG de relleno por imagen; apunta lo que le piden."""

    def __init__(self) -> None:
        self.states: list[dict[str, object]] = []

    @property
    def disabled(self) -> bool:
        return False

    async def paint(self, states: list[dict[str, object]]) -> list[bytes] | None:
        self.states += states
        return [b"PNG" for _state in states]

    async def still(self, state: dict[str, object]) -> bytes | None:
        self.states.append(state)
        return b"PNG"

    async def close(self) -> None:
        return None


class FakeRentaScene:
    """`RentaScene` sin dibujar: un PNG de relleno por carta; apunta lo que le piden."""

    def __init__(self) -> None:
        self.states: list[dict[str, object]] = []

    @property
    def disabled(self) -> bool:
        return False

    async def paint(self, states: list[dict[str, object]]) -> list[bytes] | None:
        self.states += states
        return [b"PNG" for _state in states]

    async def still(self, state: dict[str, object]) -> bytes | None:
        self.states.append(state)
        return b"PNG"

    async def close(self) -> None:
        return None


class FakePetScene:
    """`PetScene` sin dibujar: un PNG de relleno por retrato; apunta lo que le piden."""

    def __init__(self) -> None:
        self.states: list[dict[str, object]] = []

    @property
    def disabled(self) -> bool:
        return False

    async def paint(self, states: list[dict[str, object]]) -> list[bytes] | None:
        self.states += states
        return [b"PNG" for _state in states]

    async def still(self, state: dict[str, object]) -> bytes | None:
        self.states.append(state)
        return b"PNG"

    async def close(self) -> None:
        return None


class FakeLotteryScene:
    """`LotteryScene` sin dibujar: PNG y GIF de relleno; apunta los modos que le piden."""

    def __init__(self) -> None:
        self.modes: list[str] = []

    @property
    def disabled(self) -> bool:
        return False

    async def still(self, state: dict[str, object]) -> bytes | None:
        self.modes.append(str(state["mode"]))
        return b"PNG"

    async def animate(
        self, meta: dict[str, object], states: list[dict[str, object]], durations: object
    ) -> LotteryMedia | None:
        self.modes.append(str(meta["mode"]))
        return LotteryMedia(gif=b"GIF", png=b"PNG", seconds=0.0)

    async def close(self) -> None:
        return None


def use_fake_drawings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sustituye los dibujantes por sus dobles; llamar antes de cargar los cogs."""
    monkeypatch.setattr(slots_scene, "SlotsScene", FakeSlotsScene)
    monkeypatch.setattr(hold_win_scene, "HoldWinScene", FakeHoldWinScene)
    monkeypatch.setattr(roulette_render, "WheelRenderer", FakeWheelRenderer)
    monkeypatch.setattr(roulette_scene, "RouletteScene", FakeRouletteScene)
    monkeypatch.setattr(pachinko_render, "PachinkoRenderer", FakePachinkoRenderer)
    monkeypatch.setattr(picar_scene, "PicarScene", FakePicarScene)
    monkeypatch.setattr(war_scene, "WarScene", FakeWarScene)
    monkeypatch.setattr(blackjack_scene, "BlackjackScene", FakeBlackjackScene)
    monkeypatch.setattr(tower_scene, "TowerScene", FakeTowerScene)
    monkeypatch.setattr(allatu_scene, "AllatuScene", FakeAllatuScene)
    monkeypatch.setattr(business_scene, "BusinessScene", FakeBusinessScene)
    monkeypatch.setattr(sports_scene, "SportsScene", FakeSportsScene)
    monkeypatch.setattr(renta_scene, "RentaScene", FakeRentaScene)
    monkeypatch.setattr(lottery_scene, "LotteryScene", FakeLotteryScene)
    monkeypatch.setattr(pet_scene, "PetScene", FakePetScene)
