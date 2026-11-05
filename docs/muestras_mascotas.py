"""Hojas de muestras de los retratos de las mascotas, para mirarlos de un vistazo.

Pinta con `PetScene` (`assets/mascotas/escena.html`) y monta con Pillow hojas de miniaturas
de 320 × 200 con la etiqueta debajo, en la carpeta que se le pasa. Sirve para revisar una
especie nueva, una raza o una prenda antes de entregarla («Añadir una especie» en
`bot/services/pets_looks.py`). Sale:

- `razas_N.png`: cada especie con cada una de sus razas, en cara `tranquila`.
- `caras.png`: seis especies variadas en las cuatro caras (tranquila, contenta, dormida, fiesta).
- `extras.png`: una visita (dos mascotas), el marco de foto del álbum (`caption`), la tarta
  del aniversario (`cake`) y la placa con un nombre largo.
- `ropa_N.png`: cada prenda de `pet_wardrobe.OUTFITS` puesta, cada una en una especie
  distinta (van rotando).
- `conjunto_N.png`: las 29 especies con un conjunto completo (cabeza, ojos, cuello y una
  capa), para ver que los anclajes cuadran en todas.
- `fondos.png`: cada fondo con el mismo gato.

Con `--ropa` solo saca las tres de ropa (lo más rápido para ajustar una prenda).

Uso, desde la raíz del repo (necesita `node` y `npm ci` hecho):

    python docs/muestras_mascotas.py /tmp/muestras
    python docs/muestras_mascotas.py /tmp/muestras --debug          # marca los anclajes
    python docs/muestras_mascotas.py /tmp/muestras --especie gato,loro

`--debug` pinta los anclajes de las prendas: cabeza (rojo: línea del ancho, inclinada), ojos
(azul), cuello (verde: caja) y tronco (naranja: caja discontinua). `--especie` limita las hojas
de razas a esas especies (claves de `LOOKS`, separadas por comas). Solo escribe en la carpeta.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

from bot.services.pet_scene import PetScene, card, pet_entry, portrait_state  # noqa: E402
from bot.services.pet_wardrobe import OUTFIT_BY_KEY, OUTFITS, Slot  # noqa: E402
from bot.services.pets import BOND_LEVELS, PetState  # noqa: E402
from bot.services.pets_catalog import SPECIES_BY_KEY  # noqa: E402
from bot.services.pets_looks import LOOKS  # noqa: E402

FONT = HERE / "src" / "bot" / "assets" / "memes" / "fonts" / "robotomedium.ttf"
THUMB = (320, 200)
LABEL_H = 24
COLUMNS = 4
ROWS_PER_SHEET = 4
#: Seis especies de planes distintos para ver las cuatro caras.
MOOD_SPECIES = ("gato", "perro", "loro", "cabra", "pulpo", "pedrusco")
MOODS = ("tranquila", "contenta", "dormida", "fiesta")

Item = tuple[str, dict[str, Any]]


def _entry(species: str, breed_key: str | None = None) -> dict[str, Any]:
    return pet_entry(species, LOOKS[species].breed(breed_key), {})


def _sheet(tiles: list[tuple[str, bytes]], columns: int = COLUMNS) -> Image.Image:
    """Monta miniaturas con su etiqueta debajo."""
    rows = -(-len(tiles) // columns)
    cell_h = THUMB[1] + LABEL_H
    sheet = Image.new("RGB", (columns * THUMB[0], rows * cell_h), "#2b2433")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(FONT), 14)
    for i, (label, png) in enumerate(tiles):
        x, y = (i % columns) * THUMB[0], (i // columns) * cell_h
        thumb = Image.open(io.BytesIO(png)).convert("RGB").resize(THUMB, Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x, y))
        draw.text((x + 8, y + THUMB[1] + 4), label, fill="#f2e9f7", font=font)
    return sheet


def _with_card(state: dict[str, Any]) -> dict[str, Any]:
    """Rellena la ficha de un retrato de una mascota que no la trae (nivel 4, racha de 5)."""
    if state["card"] or len(state["pets"]) != 1:
        return state
    entry = state["pets"][0]
    species = SPECIES_BY_KEY[entry["species"]]
    look = LOOKS[entry["species"]]
    breed = next((b for b in look.breeds if dict(b.look) == entry["look"]), look.default)
    pet = PetState(id=1, guild_id=1, user_id=1, species=species.key, bond=BOND_LEVELS[4])
    worn = {o.slot: o for o in OUTFITS if o.drawing() in entry["outfit"].values()}
    if state["backdrop"]["shape"] != "casa":
        worn |= {o.slot: o for o in OUTFITS if o.drawing() == state["backdrop"]}
    text = card(pet, species, breed, worn, name=species.name.split()[0], xp="+2,0 % de XP",
                active=True, streak=5, best_streak=12)  # fmt: skip
    return state | {"card": text}


async def _paint(scene: PetScene, items: list[Item], debug: bool) -> list[tuple[str, bytes]]:
    states = [_with_card(state) | ({"debug": True} if debug else {}) for _, state in items]
    pngs = await scene.paint(states)
    if pngs is None:
        raise SystemExit("Ni Node ni Chromium pueden pintar la escena")
    return [(label, png) for (label, _), png in zip(items, pngs, strict=True)]


def _breed_items(only: set[str] | None) -> list[Item]:
    items: list[Item] = []
    for species, look in LOOKS.items():
        if only and species not in only:
            continue
        for breed in look.breeds:
            state = portrait_state([_entry(species, breed.key)], mood="tranquila", seed=3)
            items.append((f"{species} · {breed.name}", state))
    return items


def _mood_items() -> list[Item]:
    return [
        (f"{species} · {mood}", portrait_state([_entry(species)], mood=mood, seed=5))
        for species in MOOD_SPECIES
        for mood in MOODS
    ]


def _extra_items() -> list[Item]:
    gato, perro = _entry("gato", "siames"), _entry("perro", "beagle")
    long_card = {"title": "Su Excelencia Don Bigotes Tercero", "sub": "Gato callejero tuerto"}
    return [
        ("visita (dos mascotas)", portrait_state([gato, perro], mood="contenta", seed=7)),
        (
            "álbum (caption)",
            portrait_state([_entry("loro", "guacamayo")], caption="Nivel 6 · 12/10/2026", seed=2),
        ),
        (
            "tarta (cake)",
            portrait_state([_entry("caniche", "blanco")], mood="fiesta", cake=True, seed=4),
        ),
        (
            "nombre largo en la cabecera",
            portrait_state([_entry("gato_callejero", "negro")], card=long_card, seed=1),
        ),
    ]


#: Especies por las que van rotando las prendas en `ropa_N.png`.
WEAR_SPECIES = ("gato", "perro", "loro", "conejo", "caballo", "pedrusco", "hamster", "cabra")
#: El conjunto completo de `conjunto_N.png`.
FULL_LOOK = ("chistera", "gafas_sol", "pajarita", "capa")


def _wear_items() -> list[Item]:
    items: list[Item] = []
    for i, outfit in enumerate(o for o in OUTFITS if o.slot is not Slot.BACKDROP):
        species = WEAR_SPECIES[i % len(WEAR_SPECIES)]
        entry = pet_entry(species, LOOKS[species].default, {outfit.slot: outfit})
        items.append((f"{outfit.key} · {species}", portrait_state([entry], seed=i)))
    return items


def _full_items() -> list[Item]:
    look = {OUTFIT_BY_KEY[k].slot: OUTFIT_BY_KEY[k] for k in FULL_LOOK}
    return [
        (species, portrait_state([pet_entry(species, LOOKS[species].default, look)], seed=1))
        for species in LOOKS
    ]


def _backdrop_items() -> list[Item]:
    cat = _entry("gato")
    return [
        (outfit.key, portrait_state([cat], backdrop=outfit, seed=i))
        for i, outfit in enumerate(o for o in OUTFITS if o.slot is Slot.BACKDROP)
    ]


async def _save_paged(
    scene: PetScene, items: list[Item], debug: bool, folder: Path, name: str
) -> list[Path]:
    per_sheet = COLUMNS * ROWS_PER_SHEET
    tiles = await _paint(scene, items, debug)
    written = []
    for n, start in enumerate(range(0, len(tiles), per_sheet), 1):
        path = folder / f"{name}_{n}.png"
        _sheet(tiles[start : start + per_sheet]).save(path)
        written.append(path)
    return written


async def main_async(
    folder: Path, debug: bool, only: set[str] | None, clothes: bool = False
) -> list[Path]:
    """Pinta y guarda las hojas; devuelve sus rutas."""
    folder.mkdir(parents=True, exist_ok=True)
    scene = PetScene()
    written: list[Path] = []
    try:
        if clothes or not only:
            written += await _save_paged(scene, _wear_items(), debug, folder, "ropa")
            written += await _save_paged(scene, _full_items(), debug, folder, "conjunto")
            path = folder / "fondos.png"
            _sheet(await _paint(scene, _backdrop_items(), debug)).save(path)
            written.append(path)
            if clothes:
                return written
        per_sheet = COLUMNS * ROWS_PER_SHEET
        tiles = await _paint(scene, _breed_items(only), debug)
        for n, start in enumerate(range(0, len(tiles), per_sheet), 1):
            path = folder / f"razas_{n}.png"
            _sheet(tiles[start : start + per_sheet]).save(path)
            written.append(path)
        if not only:
            path = folder / "caras.png"
            _sheet(await _paint(scene, _mood_items(), debug)).save(path)
            written.append(path)
            path = folder / "extras.png"
            _sheet(await _paint(scene, _extra_items(), debug)).save(path)
            written.append(path)
    finally:
        await scene.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("carpeta", type=Path, help="donde dejar las hojas")
    parser.add_argument("--debug", action="store_true", help="marca los anclajes de las prendas")
    parser.add_argument("--especie", help="solo las razas de estas especies, separadas por comas")
    parser.add_argument("--ropa", action="store_true", help="solo las hojas de ropa y fondos")
    args = parser.parse_args()
    only = {s.strip() for s in args.especie.split(",")} if args.especie else None
    for path in asyncio.run(main_async(args.carpeta, args.debug, only, args.ropa)):
        print(path)


if __name__ == "__main__":
    main()
