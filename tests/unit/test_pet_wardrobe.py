"""Pruebas del armario, las razas, el álbum y el retrato de las mascotas.

Comprueba los registros (cada prenda tiene una forma que la escena sabe pintar, cada raza
su artículo oculto, las de caja son piezas de la colección 👗 Armario), las reglas nuevas
(aniversario, cara del retrato, pies de foto, paseo y visita) y los flujos del panel con un
pintor de mentira: vestir y desvestir, lo vendido deja de verse, cambiar de raza, el álbum
que se llena solo al subir de nivel, enseñar una foto, visitar la mascota de otro y que
el retrato no se vuelva a subir si no cambia.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs.pets import Mascotas, Mode, PetPanel, breed_for
from bot.repositories.economy import EconomyRepository
from bot.repositories.pets import PetRepository
from bot.repositories.shop import ShopRepository
from bot.services.achievements import BY_ID, pet_dress_stats
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.lootboxes import COLLECTION_BY_KEY, PIECE_KEYS
from bot.services.pet_scene import MOODS, pet_entry, portrait_key, portrait_state
from bot.services.pet_wardrobe import (
    BOX_OUTFITS,
    HOME,
    OUTFIT_BY_KEY,
    OUTFITS,
    PATTERNS,
    SHAPES,
    SLOTS,
    Slot,
    complete,
    outfit_of,
    rewards_for,
    wearing,
)
from bot.services.pets import (
    ALBUM_LEVELS,
    ALBUM_SIZE,
    BOND_LEVELS,
    CARE_RULES,
    Care,
    Event,
    Moment,
    PetState,
    Photo,
    anniversary_years,
    apply_care,
    photo_caption,
    portrait_mood,
)
from bot.services.pets_catalog import SPECIES
from bot.services.pets_looks import (
    BREED_ITEMS,
    LOOKS,
    PLAN_SLOTS,
    breed_catalog_key,
    breed_of_item,
    look_of,
    slots_of,
)
from bot.services.rastro import untradeable
from bot.services.shop import Kind, quote
from bot.services.shop_catalog import CATALOG, CATALOG_BY_KEY

GUILD = 1
OWNER = 10
OTHER = 11
NOW = datetime(2026, 10, 7, 18, tzinfo=TIMEZONE).timestamp()


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeScene:
    """Pintor de mentira: un PNG distinto por retrato y apunta lo que le piden."""

    disabled = False

    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []

    async def still(self, state: dict[str, Any]) -> bytes:
        self.states.append(state)
        return b"\x89PNG" + portrait_key(state).encode()

    async def close(self) -> None:
        return None


# -- Registros --------------------------------------------------------------------------


def test_cada_prenda_tiene_forma_estampado_y_clave_unica() -> None:
    assert len({o.key for o in OUTFITS}) == len(OUTFITS)
    assert len({o.catalog_key for o in OUTFITS}) == len(OUTFITS)
    for outfit in OUTFITS:
        assert outfit.shape in SHAPES[outfit.slot], outfit.key
        assert outfit.pattern in PATTERNS, outfit.key
        assert outfit.colors and all(c.startswith("#") for c in outfit.colors), outfit.key
        assert outfit.price > 0, outfit.key
        if outfit.price >= 100_000:
            assert outfit.min_level, f"{outfit.key}: lo caro pide nivel"
    assert HOME.shape in SHAPES[Slot.BACKDROP]


def test_las_prendas_estan_en_la_tienda_con_su_visibilidad() -> None:
    for outfit in OUTFITS:
        entry = CATALOG_BY_KEY[outfit.catalog_key]
        assert entry.kind is Kind.TROPHY, outfit.key
        assert entry.visible is outfit.for_sale, outfit.key
        if not outfit.box:
            assert entry.per_user == 1, f"{outfit.key}: una de cada prenda por persona"
    for outfit in BOX_OUTFITS:
        assert outfit.catalog_key in PIECE_KEYS
    pieces = COLLECTION_BY_KEY["armario"].pieces
    assert [p.key for p in pieces] == [o.catalog_key for o in BOX_OUTFITS]


def test_la_ropa_de_logro_existe_y_no_se_vende_en_el_rastro() -> None:
    rewarded = [o for o in OUTFITS if o.achievement]
    assert rewarded
    for outfit in rewarded:
        assert outfit.achievement in BY_ID, outfit.key
        assert untradeable(Kind.TROPHY, outfit.catalog_key) is not None
    assert rewards_for({"pet_bond_10"}) == (OUTFIT_BY_KEY["laurel"],)
    assert untradeable(Kind.TROPHY, OUTFIT_BY_KEY["chistera"].catalog_key) is None


def test_cada_especie_tiene_dibujo_y_sus_razas_su_articulo() -> None:
    assert set(LOOKS) == {s.key for s in SPECIES}
    for species, look in LOOKS.items():
        assert look.default.price == 0, species
        assert len({b.key for b in look.breeds}) == len(look.breeds), species
        for breed in look.breeds[1:]:
            key = breed_catalog_key(species, breed.key)
            entry = CATALOG_BY_KEY[key]
            assert entry.price == breed.price > 0
            assert entry.visible is False and entry.per_user == 1
            assert breed_of_item(key) == (species, breed)
            assert untradeable(Kind.TROPHY, key) is not None, "la raza va con la mascota"
    assert len(BREED_ITEMS) == sum(len(look.breeds) - 1 for look in LOOKS.values())
    for plan, slots in PLAN_SLOTS.items():
        assert set(slots) <= {s.value for s in SLOTS}, plan


def test_lo_puesto_solo_se_ve_si_su_dueno_lo_tiene() -> None:
    chistera, gafas = OUTFIT_BY_KEY["chistera"], OUTFIT_BY_KEY["gafas_sol"]
    worn = {"cabeza": chistera.catalog_key, "ojos": gafas.catalog_key, "cuello": "basura"}
    look = wearing(worn, {chistera.catalog_key})
    assert look == {Slot.HEAD: chistera}
    # Una prenda guardada en un hueco que no es el suyo no se ve.
    assert wearing({"ojos": chistera.catalog_key}, {chistera.catalog_key}) == {}
    full = {o.slot: o for o in (chistera, gafas, OUTFIT_BY_KEY["pajarita"], OUTFIT_BY_KEY["capa"])}
    assert complete(full) and not complete(look)


def test_la_entrada_de_la_escena_solo_lleva_los_huecos_de_la_especie() -> None:
    look = {
        Slot.HEAD: OUTFIT_BY_KEY["chistera"],
        Slot.NECK: OUTFIT_BY_KEY["pajarita"],
        Slot.BACKDROP: OUTFIT_BY_KEY["fondo_teide"],
    }
    fish = pet_entry("pez", look_of("pez").default, look)
    assert set(fish["outfit"]) == {"cabeza"}, "un pez no tiene cuello y el fondo va aparte"
    assert set(slots_of("pez")) == {"cabeza", "ojos"}
    state = portrait_state([fish], backdrop=look[Slot.BACKDROP], mood="dormida")
    assert state["backdrop"]["shape"] == "teide"
    assert portrait_key(state) == portrait_key(dict(state))
    with pytest.raises(ValueError):
        portrait_state([fish], mood="triste")
    assert set(MOODS) == {"tranquila", "contenta", "dormida", "fiesta"}


def test_estadisticas_de_vestir_marcan_las_combinaciones() -> None:
    delta = pet_dress_stats(
        species="cabra",
        outfit="corona",
        owned=3,
        complete=False,
        look={"cabeza": "corona"},
        aisles={"cabeza": "lujo"},
    )
    assert delta.add["pet_dress_cabra_corona"] == 1 and delta.peak["pet_outfits_owned"] == 3
    canary = pet_dress_stats(
        species="gato",
        outfit="manta",
        owned=3,
        complete=False,
        look={},
        aisles={"cabeza": "canarias", "cuerpo": "canarias", "fondo": "canarias"},
    )
    assert canary.add["pet_dress_canarias"] == 1
    undress = pet_dress_stats(
        species="gato", outfit=None, owned=1, complete=False, look={}, aisles={}
    )
    assert "pet_dressed" not in undress.add
    pope = pet_dress_stats(
        species="schnauzer",
        outfit="disfraz_papa",
        owned=1,
        complete=False,
        look={"cuerpo": "disfraz_papa"},
        aisles={"cuerpo": "cotillon"},
    )
    assert pope.add["pet_dress_papa"] == 1 and pope.add["pet_dress_schnauzer_papa"] == 1
    for key in ("disfraz_tiburon", "disfraz_papa_arrugada"):
        stat = f"pet_dress_{key.removeprefix('disfraz_')}"
        delta = pet_dress_stats(
            species="gato", outfit=key, owned=1, complete=False, look={"cuerpo": key}, aisles={}
        )
        assert delta.add[stat] == 1 and stat in BY_ID


def test_la_ficha_no_cambia_con_cada_caricia() -> None:
    from bot.services.pet_scene import card
    from bot.services.pets_catalog import SPECIES_BY_KEY

    cat = PetState(id=1, guild_id=GUILD, user_id=OWNER, species="gato", bond=BOND_LEVELS[4] + 1)
    look = {Slot.HEAD: OUTFIT_BY_KEY["chistera"], Slot.BACKDROP: OUTFIT_BY_KEY["fondo_teide"]}

    def text() -> dict:
        siames = look_of("gato").breed("siames")
        return card(cat, SPECIES_BY_KEY["gato"], siames, look, name="Michi",
                    xp="+2,0 % de XP", active=True, streak=3, best_streak=9)  # fmt: skip

    before = text()
    apply_care(cat, Care.PET, NOW)
    assert text() == before, "lo que cambia en cada cuidado no va en la imagen"
    assert before["level"] == 4 and before["stars"] == 1 and before["breed"] == "Siamés"
    assert [w["slot"] for w in before["wearing"]] == ["cabeza", "fondo"]
    assert before["theme"] and before["active"] is True


# -- Reglas -----------------------------------------------------------------------------


def stamp(year: int, month: int, day: int, hour: int = 12) -> float:
    return datetime(year, month, day, hour, tzinfo=TIMEZONE).timestamp()


def test_el_aniversario_cuenta_anos_y_el_29_de_febrero_cae_el_28() -> None:
    adopted = stamp(2025, 10, 7)
    assert anniversary_years(adopted, stamp(2026, 10, 7)) == 1
    assert anniversary_years(adopted, stamp(2026, 10, 8)) == 0
    assert anniversary_years(adopted, stamp(2025, 10, 7, 20)) == 0, "el mismo día no cuenta"
    leap = stamp(2024, 2, 29)
    assert anniversary_years(leap, stamp(2025, 2, 28)) == 1
    assert anniversary_years(leap, stamp(2028, 2, 29)) == 4
    assert anniversary_years(0, stamp(2026, 1, 1)) == 0


def test_la_cara_del_retrato_nunca_es_triste() -> None:
    pet = PetState(id=1, guild_id=GUILD, user_id=OWNER, species="gato")
    assert portrait_mood(pet, stamp(2026, 10, 7, 3)) == "dormida"
    assert portrait_mood(pet, stamp(2026, 10, 7, 18)) == "tranquila"
    assert portrait_mood(pet, stamp(2026, 10, 7, 3), cared=True) == "contenta"
    assert portrait_mood(pet, stamp(2026, 10, 7, 3), party=True) == "fiesta"
    apply_care(pet, Care.PET, stamp(2026, 10, 7, 9))
    assert portrait_mood(pet, stamp(2026, 10, 7, 18)) == "contenta"


def test_pies_de_foto_y_paseo_diario() -> None:
    when = stamp(2026, 10, 12)
    assert photo_caption(Photo.LEVEL, when, level=6) == "Nivel 6 · 12/10/2026"
    assert photo_caption(Photo.ANNIVERSARY, when, years=1).startswith("1 año en casa")
    assert photo_caption(Photo.ANNIVERSARY, when, years=2).startswith("2 años en casa")
    pet = PetState(id=1, guild_id=GUILD, user_id=OWNER, species="perro")
    morning = stamp(2026, 10, 7, 8)
    first = apply_care(pet, Care.WALK, morning)
    assert first.points == CARE_RULES[Care.WALK].points
    again = apply_care(pet, Care.WALK, morning + CARE_RULES[Care.WALK].cooldown)
    assert again.points == 0, "un paseo que suma al día"


# -- Repositorio ------------------------------------------------------------------------


async def make_stores(tmp_path: Path) -> tuple[EconomyService, ShopRepository, PetRepository]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE * 1_000)
    await economy_repo.initialize()
    shop = ShopRepository(tmp_path / "bot.db")
    await shop.initialize()
    pets = PetRepository(tmp_path / "bot.db")
    await pets.initialize()
    return EconomyService(economy_repo, clock=Clock()), shop, pets


async def buy(economy: EconomyService, shop: ShopRepository, key: str, user: int = OWNER) -> None:
    item = await shop.item_by_key(GUILD, key)
    assert item is not None
    price = quote(item, NOW)
    await economy.purchase(
        GUILD, user, base=price.base, tax=price.tax, concept=item.kind.value,
        reserve=shop.reserve(GUILD, user, item.id, expected=price, level=99, now=NOW),
    )  # fmt: skip


async def give(shop: ShopRepository, key: str, user: int = OWNER) -> None:
    item = await shop.item_by_key(GUILD, key)
    assert item is not None
    await shop.grant(GUILD, user, item, NOW)


async def test_la_base_de_datos_de_antes_gana_raza_y_aniversario(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE pets (
            id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            species TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
            bond INTEGER NOT NULL DEFAULT 0, adopted_at REAL NOT NULL,
            day TEXT NOT NULL DEFAULT '', today TEXT NOT NULL DEFAULT '{}',
            last TEXT NOT NULL DEFAULT '{}', totals TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        "INSERT INTO pets (id, guild_id, user_id, species, adopted_at) VALUES (1, 1, 10, 'gato', 0)"
    )
    connection.commit()
    connection.close()
    repo = PetRepository(path)
    await repo.initialize()
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(pets)")}
    assert {"breed", "celebrated"} <= columns


async def test_el_album_guarda_el_retrato_y_no_pasa_del_tope(tmp_path: Path) -> None:
    _economy, _shop, pets = await make_stores(tmp_path)
    pet = PetState(id=7, guild_id=GUILD, user_id=OWNER, species="gato")
    for i in range(ALBUM_SIZE + 3):
        await pets.add_photo(
            pet, taken_at=NOW + i, reason="foto", caption=f"#{i}", portrait={"i": i}
        )
    album = await pets.photos(7)
    assert len(album) == ALBUM_SIZE
    assert album[0].caption == "#3" and album[-1].state == {"i": ALBUM_SIZE + 2}
    await pets.wear(pet, "cabeza", "ropa_chistera")
    await pets.wear(pet, "cabeza", "ropa_gorra")
    await pets.wear(pet, "ojos", "ropa_gafas_sol")
    await pets.wear(pet, "ojos", None)
    assert await pets.outfits([7, 8]) == {7: {"cabeza": "ropa_gorra"}, 8: {}}
    await pets.delete_guild_data(GUILD)
    assert await pets.photos(7) == [] and await pets.outfits([7]) == {7: {}}


# -- Cog --------------------------------------------------------------------------------


def make_member(user_id: int = OWNER) -> MagicMock:
    guild = MagicMock()
    guild.id = GUILD
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = f"Miembro{user_id}"
    member.mention = f"<@{user_id}>"
    member.bot = False
    member.guild = guild
    return member


def click(member: MagicMock) -> MagicMock:
    interaction = fake_interaction(member)
    interaction.guild = member.guild
    interaction.channel = MagicMock()
    return interaction


async def make_cog(tmp_path: Path, clock: Clock | None = None):  # noqa: ANN201
    economy, shop, pets = await make_stores(tmp_path)
    await shop.stock_catalog(GUILD, CATALOG, NOW)
    bot = MagicMock()
    bot.get_cog.return_value = None  # sin logros ni tienda
    scene = FakeScene()
    cog = Mascotas(bot, pets, shop, clock=clock or Clock(), rng=random.Random(3), scene=scene)
    await cog.cog_load()
    return cog, economy, scene


def texts(view: ui.LayoutView) -> str:
    return "\n".join(c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay))


def select(view: ui.LayoutView, custom_id: str) -> ui.Select:
    return next(
        c for c in view.walk_children() if isinstance(c, ui.Select) and c.custom_id == custom_id
    )


async def adopted_cat(tmp_path: Path, clock: Clock | None = None):  # noqa: ANN201
    cog, economy, scene = await make_cog(tmp_path, clock)
    member = make_member()
    await buy(economy, cog.shop, "mascota_gato")
    item = await cog.shop.item_by_key(GUILD, "mascota_gato")
    await cog.adopted(GUILD, member, None, item)
    panel = await cog.panel(member.guild, member, member)
    return cog, economy, scene, member, panel


async def test_el_panel_lleva_retrato_y_no_lo_resube_si_no_cambia(tmp_path: Path) -> None:
    cog, _economy, scene, member, panel = await adopted_cat(tmp_path)
    kinds = [type(c) for c in panel.walk_children()]
    assert ui.MediaGallery in kinds
    first = panel.send_kwargs()
    assert len(first["files"]) == 1
    assert scene.states[-1]["pets"][0]["plan"] == look_of("gato").plan
    # Repintar sin cambios no vuelve a subir la imagen.
    await panel.paint()
    assert "attachments" not in panel.edit_kwargs()
    # Acariciar la pone contenta: retrato nuevo, una sola subida.
    await cog.care(click(member), panel, Care.PET)
    assert scene.states[-1]["mood"] == "contenta"
    assert "attachments" not in panel.edit_kwargs(), "la edición del cuidado ya la subió"


async def test_sin_pintor_el_panel_sale_en_texto(tmp_path: Path) -> None:
    cog, _economy, scene, member, _panel = await adopted_cat(tmp_path)

    async def broken(state: dict[str, Any]) -> bytes:
        raise RuntimeError("sin Node ni Chromium")

    scene.still = broken  # type: ignore[method-assign]
    panel = await cog.panel(member.guild, member, member)
    assert ui.MediaGallery not in [type(c) for c in panel.walk_children()]
    assert "Gato" in texts(panel) and "files" not in panel.send_kwargs()


async def test_vestir_con_lo_que_tienes_y_lo_vendido_deja_de_verse(tmp_path: Path) -> None:
    cog, economy, scene, member, panel = await adopted_cat(tmp_path)
    await buy(economy, cog.shop, "ropa_chistera")
    panel.mode = Mode.WARDROBE
    await panel.refresh(click(member))
    options = [o.value for o in select(panel, "mascota:ropa:cabeza").options]
    assert "chistera" in options and "comprar:corona" in options and "-" in options
    # Lo de temporada fuera de su temporada no sale (octubre: Halloween sí, Navidad no).
    assert "comprar:papa_noel" not in options and "comprar:bruja" in options

    await cog.dress(click(member), panel, Slot.HEAD, "chistera")
    assert scene.states[-1]["pets"][0]["outfit"]["cabeza"]["shape"] == "chistera"
    album = await cog.repository.photos(panel.current.id)
    assert [p.reason for p in album] == [Photo.DRESSED.value], "el primer modelito, a foto"
    assert "Chistera" in texts(panel)

    # Si la vende (sale de su mochila), deja de verse sin borrar nada.
    (entry,) = [
        e for e in await cog.shop.inventory(GUILD, OWNER, NOW) if e.catalog_key == "ropa_chistera"
    ]
    await cog.shop.consume(GUILD, OWNER, entry.id)
    await panel.refresh(click(member))
    assert panel.looks[panel.current.id] == {}
    await cog.dress(click(member), panel, Slot.HEAD, "chistera")
    assert "cabeza" not in scene.states[-1]["pets"][0]["outfit"]


async def test_cambiar_de_raza_solo_con_la_que_tienes(tmp_path: Path) -> None:
    cog, _economy, scene, member, panel = await adopted_cat(tmp_path)
    cat = panel.current
    assert breed_for(cat, frozenset()).key == "europeo"
    interaction = click(member)
    await cog.choose_breed(interaction, panel, "siames")
    interaction.followup.send.assert_awaited()  # «esa raza ya no es tuya»
    await give(cog.shop, breed_catalog_key("gato", "siames"))
    await cog.choose_breed(click(member), panel, "siames")
    (saved,) = await cog.repository.pets(GUILD, OWNER)
    assert saved.breed == "siames"
    assert scene.states[-1]["pets"][0]["look"] == dict(look_of("gato").breed("siames").look)
    await cog.choose_breed(click(member), panel, "europeo")
    (saved,) = await cog.repository.pets(GUILD, OWNER)
    assert saved.breed == "", "la de serie no guarda nada"


async def test_subir_a_nivel_de_album_hace_foto(tmp_path: Path) -> None:
    clock = Clock()
    cog, _economy, _scene, member, panel = await adopted_cat(tmp_path, clock)
    cat = panel.current
    level = min(ALBUM_LEVELS)
    cat.bond = BOND_LEVELS[level] - CARE_RULES[Care.PLAY].points
    await cog.repository.save(cat)
    await cog.care(click(member), panel, Care.PLAY)
    album = await cog.repository.photos(cat.id)
    assert [p.reason for p in album] == [Photo.LEVEL.value]
    assert album[0].caption.startswith(f"Nivel {level}")

    panel.mode = Mode.ALBUM
    panel.page = 99
    await panel.refresh(click(member))
    assert panel.page == 0 and "Foto 1 de 1" in texts(panel)
    assert panel.image is not None


async def test_ensenar_publica_la_foto_y_espera_un_minuto(tmp_path: Path) -> None:
    clock = Clock()
    cog, _economy, _scene, member, panel = await adopted_cat(tmp_path, clock)
    interaction = click(member)
    await cog.show(interaction, panel)
    sent = interaction.followup.send.await_args
    assert sent.kwargs["ephemeral"] is False and sent.kwargs["file"].filename == "mascota.png"
    assert [p.reason for p in await cog.repository.photos(panel.current.id)] == ["foto"]
    again = click(member)
    await cog.show(again, panel)
    assert again.response.send_message.await_args.kwargs["ephemeral"] is True
    clock.now += 61
    later = click(member)
    await cog.show(later, panel)
    assert later.followup.send.await_args.kwargs["ephemeral"] is False


async def test_visitar_suma_a_las_dos_y_deja_foto(tmp_path: Path) -> None:
    cog, economy, scene, member, panel = await adopted_cat(tmp_path)
    host = make_member(OTHER)
    await buy(economy, cog.shop, "mascota_perro", user=OTHER)
    item = await cog.shop.item_by_key(GUILD, "mascota_perro")
    await cog.adopted(GUILD, host, None, item)

    interaction = click(member)
    await cog.visit(interaction, panel, host)
    public = interaction.followup.send.await_args
    assert public.kwargs["ephemeral"] is False
    assert public.kwargs["allowed_mentions"].users == [host]
    duo = [st for st in scene.states if len(st["pets"]) == 2]
    assert len(duo) == 1 and duo[0]["card"]["sub"].startswith("De visita")
    assert [gst["role"] for gst in duo[0]["card"]["guests"]] == [
        "Viene de visita",
        "Recibe en casa",
    ]
    points = CARE_RULES[Care.VISIT].points
    (cat,) = await cog.repository.pets(GUILD, OWNER)
    (dog,) = await cog.repository.pets(GUILD, OTHER)
    assert cat.bond == points and dog.bond == points
    assert len(await cog.repository.photos(cat.id)) == 1
    assert len(await cog.repository.photos(dog.id)) == 1

    # Otra visita enseguida: espera, sin publicar nada.
    again = click(member)
    await cog.visit(again, panel, host)
    again.followup.send.assert_not_awaited()
    # A uno mismo o a quien no lleva mascota, no.
    alone = click(member)
    await cog.visit(alone, panel, make_member(99))
    assert "no lleva ninguna mascota" in alone.edit_original_response.await_args.kwargs["content"]


async def test_el_aniversario_sale_una_vez_al_ano_con_foto(tmp_path: Path) -> None:
    clock = Clock()
    cog, _economy, _scene, _member, panel = await adopted_cat(tmp_path, clock)
    cat = cog.active[(GUILD, OWNER)]
    clock.now = datetime.fromtimestamp(cat.adopted_at, TIMEZONE).replace(year=2027).timestamp()
    line = await cog.cameo(GUILD, OWNER, Moment(Event.WIN))
    assert line is not None and "🎂" in line
    album = await cog.repository.photos(cat.id)
    assert [p.reason for p in album] == [Photo.ANNIVERSARY.value]
    assert "1 año en casa" in album[0].caption
    assert album[0].state["cake"] is True and album[0].state["mood"] == "fiesta"
    clock.now += 120
    await cog.cameo(GUILD, OWNER, Moment(Event.WIN))
    assert len(await cog.repository.photos(cat.id)) == 1, "solo una vez ese año"
    (saved,) = await cog.repository.pets(GUILD, OWNER)
    assert saved.celebrated == 2027


async def test_los_demas_solo_miran(tmp_path: Path) -> None:
    cog, _economy, _scene, member, _panel = await adopted_cat(tmp_path)
    panel = await cog.panel(member.guild, member, make_member(OTHER))
    assert isinstance(panel, PetPanel)
    labels = {c.label for c in panel.walk_children() if isinstance(c, ui.Button)}
    assert "Vestir" not in labels and "Enseñar" not in labels


def test_cada_hueco_tiene_algo_que_ponerse() -> None:
    for slot in SLOTS:
        assert any(o.slot is slot and o.for_sale for o in OUTFITS), slot
    assert outfit_of("ropa_chistera") is OUTFIT_BY_KEY["chistera"]
    assert outfit_of("mascota_gato") is None
