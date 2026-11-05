"""Los dobles de `render_fakes` se llaman igual que los dibujantes de verdad.

Las pruebas con el bot real que no miran la imagen cambian los dibujantes del
casino por dobles. Si un dibujante cambia de firma y su doble no, esas pruebas
seguirían pasando con una llamada que en producción rompe: aquí se comprueba que
cada método del doble existe en el real con los mismos parámetros.
"""

from __future__ import annotations

import inspect

import pytest
from render_fakes import (
    FakeBusinessScene,
    FakeHoldWinScene,
    FakeLotteryScene,
    FakePachinkoRenderer,
    FakePetScene,
    FakePicarScene,
    FakeRentaScene,
    FakeSlotsScene,
    FakeSportsScene,
    FakeWheelRenderer,
)

from bot.services.business_scene import BusinessScene
from bot.services.hold_win_scene import HoldWinScene
from bot.services.lottery_scene import LotteryScene
from bot.services.pachinko_render import PachinkoRenderer
from bot.services.pet_scene import PetScene
from bot.services.picar_scene import PicarScene
from bot.services.renta_scene import RentaScene
from bot.services.roulette_render import WheelRenderer
from bot.services.slots_scene import SlotsScene
from bot.services.sports_scene import SportsScene


@pytest.mark.parametrize(
    ("fake", "real"),
    [
        (FakeSlotsScene, SlotsScene),
        (FakeHoldWinScene, HoldWinScene),
        (FakeWheelRenderer, WheelRenderer),
        (FakePachinkoRenderer, PachinkoRenderer),
        (FakePicarScene, PicarScene),
        (FakeBusinessScene, BusinessScene),
        (FakeSportsScene, SportsScene),
        (FakeRentaScene, RentaScene),
        (FakeLotteryScene, LotteryScene),
        (FakePetScene, PetScene),
    ],
    ids=lambda cls: cls.__name__,
)
def test_cada_doble_tiene_la_firma_del_dibujante_real(fake: type, real: type) -> None:
    for name, method in vars(fake).items():
        if name.startswith("_") or not callable(method):
            continue
        assert hasattr(real, name), f"{real.__name__} ya no tiene {name}"
        fake_params = list(inspect.signature(method).parameters)
        real_params = list(inspect.signature(getattr(real, name)).parameters)
        assert fake_params == real_params, name
