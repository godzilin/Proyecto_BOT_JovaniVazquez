"""Pruebas de las mascotas: reglas, especies, repositorio y el cog `mascota`.

Comprueba que el vínculo solo sube (con su cupo diario y sus esperas), que
los cameos salen siempre en los momentos sonados y de vez en cuando en los
demás, que cada especie arma todas sus frases sin huecos, que las bases de
datos de antes de las mascotas aceptan el tipo nuevo sin perder artículos, y
los flujos del panel: cuidar, dar de comer lo de la mochila, ponerle nombre,
elegir la activa y que aparezca la cucaracha al quedarse a cero.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction
from render_fakes import FakePetScene

from bot.cogs.pets import FoodPicker, Mascotas, PetPanel
from bot.repositories.economy import EconomyRepository
from bot.repositories.pets import PetRepository
from bot.repositories.shop import ShopRepository
from bot.services.achievements import (
    PET_SPAWN_KINDS_STAT,
    PET_SPECIES_STAT,
    pet_adopt_stats,
    pet_care_stats,
    pet_name_stats,
    with_derived,
)
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.pets import (
    BIG_WIN_MIN,
    BOND_LEVELS,
    CAMEO_GAP,
    CARE_RULES,
    FAVOURITE_BONUS,
    MAX_BOND_LEVEL,
    MAX_NAME,
    XP_BONUS_PER_LEVEL,
    Care,
    Event,
    Moment,
    Mood,
    PetState,
    apply_care,
    bet_moment,
    bond_level,
    cameo_text,
    care_text,
    clean_name,
    pick_line,
    streak_after,
    wants_cameo,
    xp_bonus,
)
from bot.services.pets_catalog import RARITIES, SPAWNING, SPECIES, SPECIES_BY_KEY
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


class Rigged(random.Random):
    """Azar que devuelve siempre `chance` en `random` (y lo normal en lo demás)."""

    def __init__(self, chance: float) -> None:
        super().__init__(0)
        self.chance = chance

    def random(self) -> float:
        return self.chance


def state(species: str = "gato", bond: int = 0) -> PetState:
    return PetState(id=1, guild_id=GUILD, user_id=OWNER, species=species, bond=bond)


# -- Momentos --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stake", "net", "balance", "event", "loud"),
    [
        (100, -100, 0, Event.BUST, True),
        (100, -100, 50, Event.LOSE, False),
        (100, BIG_WIN_MIN, 5_000, Event.BIG_WIN, True),
        (1_000, 2_000, 5_000, Event.WIN, False),  # no llega a diez veces la apuesta
        (50_000, 100_000, 200_000, Event.BIG_WIN, True),  # premio enorme, sea como sea
        (100, 0, 100, Event.MONEY, False),
    ],
)
def test_cada_jugada_es_un_momento(
    stake: int, net: int, balance: int, event: Event, loud: bool
) -> None:
    moment = bet_moment(stake=stake, net=net, balance_after=balance)
    assert moment.event is event and moment.loud is loud


def test_un_all_in_siempre_es_sonado() -> None:
    assert bet_moment(stake=500, net=-500, balance_after=20, all_in=True).loud
    assert bet_moment(stake=500, net=500, balance_after=1_000, all_in=True).loud


# -- Vínculo y cuidados ----------------------------------------------------------------


def test_niveles_de_vinculo() -> None:
    assert bond_level(0) == 0
    assert bond_level(BOND_LEVELS[1] - 1) == 0
    assert bond_level(BOND_LEVELS[1]) == 1
    assert bond_level(10**6) == MAX_BOND_LEVEL
    assert list(BOND_LEVELS) == sorted(set(BOND_LEVELS)), "siempre subiendo"


def test_bonus_de_xp_pequeno_y_con_canal_favorito() -> None:
    assert xp_bonus(0, "todo", voice=False) == 1.0
    top = xp_bonus(MAX_BOND_LEVEL, "todo", voice=False)
    assert top == pytest.approx(1 + MAX_BOND_LEVEL * XP_BONUS_PER_LEVEL)
    assert top <= 1.05, "un bonus pequeño: la gracia es la personalidad"
    assert xp_bonus(4, "voz", voice=True) == pytest.approx(1 + 2 * 4 * XP_BONUS_PER_LEVEL)
    assert xp_bonus(4, "voz", voice=False) == 1.0
    assert xp_bonus(4, "mensajes", voice=False) > 1.0


def test_cuidar_suma_hasta_el_cupo_del_dia_y_nunca_resta() -> None:
    pet = state()
    rule = CARE_RULES[Care.PET]
    for i in range(rule.daily + 2):
        outcome = apply_care(pet, Care.PET, NOW + i * rule.cooldown)
        expected = rule.points if i < rule.daily else 0
        assert outcome.points == expected and outcome.wait == 0
    assert pet.bond == rule.daily * rule.points
    assert pet.totals[Care.PET.value] == rule.daily + 2
    # Al día siguiente vuelve a sumar.
    assert apply_care(pet, Care.PET, NOW + 86_400).points == rule.points


def test_cada_cuidado_tiene_su_espera() -> None:
    pet = state()
    apply_care(pet, Care.PLAY, NOW)
    waiting = apply_care(pet, Care.PLAY, NOW + 10)
    assert waiting.wait > 0 and waiting.points == 0
    assert pet.totals[Care.PLAY.value] == 1, "esperar no cuenta"
    assert apply_care(pet, Care.PET, NOW + 10).wait == 0, "cada cuidado espera lo suyo"


def test_comida_favorita_y_subir_de_nivel_con_truco() -> None:
    pet = state(bond=BOND_LEVELS[3] - CARE_RULES[Care.FEED].points - FAVOURITE_BONUS)
    outcome = apply_care(pet, Care.FEED, NOW, favourite=True)
    assert outcome.points == CARE_RULES[Care.FEED].points + FAVOURITE_BONUS
    assert outcome.level_up == 3 and outcome.new_trick


def test_racha_de_dias_cuidando() -> None:
    assert streak_after("", 0, "2026-10-07") == 1
    assert streak_after("2026-10-06", 4, "2026-10-07") == 5
    assert streak_after("2026-10-07", 5, "2026-10-07") == 5
    assert streak_after("2026-10-01", 9, "2026-10-07") == 1


def test_nombres_limpios_y_sin_menciones() -> None:
    assert clean_name("  Michi   Gato ") == "Michi Gato"
    for bad in ("", "   ", "@everyone", "<@123>", "**negrita**", "x" * (MAX_NAME + 1)):
        with pytest.raises(ValueError):
            clean_name(bad)


# -- Cameos ----------------------------------------------------------------------------


def test_los_momentos_sonados_siempre_y_los_demas_a_veces() -> None:
    loud = Moment(Event.BUST)
    assert wants_cameo(0.1, loud, Rigged(0.99), hour=12, since_last=1)
    normal = Moment(Event.WIN)
    assert not wants_cameo(1.0, normal, Rigged(0.0), hour=12, since_last=CAMEO_GAP - 1)
    assert wants_cameo(1.0, normal, Rigged(0.0), hour=12)
    assert not wants_cameo(1.0, normal, Rigged(0.99), hour=12)
    # Las de madrugada salen el doble de 0:00 a 6:00.
    assert wants_cameo(1.0, normal, Rigged(0.2), hour=3, night_owl=True)
    assert not wants_cameo(1.0, normal, Rigged(0.2), hour=15, night_owl=True)


def test_el_turno_del_negocio_lleva_el_animo_de_su_nota() -> None:
    assert Moment(Event.BUSINESS).mood == Mood.NEUTRAL
    assert Moment(Event.BUSINESS, feel=Mood.GOOD).mood == Mood.GOOD
    assert Moment(Event.BUSINESS, feel=Mood.BAD).mood == Mood.BAD


@pytest.mark.parametrize("species", SPECIES, ids=lambda s: s.key)
def test_cada_especie_arma_sus_frases_sin_huecos(species) -> None:  # noqa: ANN001
    for event in Event:
        for seed in range(12):
            line = pick_line(species.lines, Moment(event), random.Random(seed))
            text = cameo_text(line, emoji=species.emoji, name="Toby", sound=species.sound)
            assert "{" not in text and "}" not in text, text
            assert text.startswith(f"-# {species.emoji} ") and "**Toby**" in text
            assert len(text) <= 300
    for care in Care:
        text = care_text(
            care, random.Random(1), name="Toby", sound=species.sound, food="🥚 Huevo",
            trick=species.tricks[0], eats=species.diet != "nada",
        )  # fmt: skip
        assert "{" not in text and "**Toby**" in text


def test_cada_especie_tiene_personalidad_completa() -> None:
    assert len(SPECIES) > 20, "más de veinte especies"
    assert len({s.key for s in SPECIES}) == len(SPECIES)
    for species in SPECIES:
        assert species.rarity in RARITIES, species.key
        assert len(species.tricks) == 3 and all(species.tricks), species.key
        for mood in ("good", "bad", "neutral"):
            assert species.lines.get(mood), (species.key, mood)
        assert set(species.lines) <= {"good", "bad", "neutral"} | {e.value for e in Event}
        assert species.diet in {"comida", "todo", "nada"}
        assert species.xp_focus in {"todo", "mensajes", "voz"}
        assert "{pet}" in species.gift_line and "{gift}" in species.gift_line
    assert all(not s.adoptable for s in SPAWNING) and len(SPAWNING) >= 5
    for species in SPAWNING:
        assert species.spawn is not None and species.spawn.hint


def test_perros_gatos_y_hurones_se_adoptan_no_se_venden() -> None:
    """Ley 7/2023, art. 56: las tiendas no pueden venderlos."""
    for key in ("perro", "gato", "huron", "bardino", "presa", "caniche"):
        species = SPECIES_BY_KEY[key]
        assert species.adoption and "adopción" in species.description.lower(), key


# -- Logros ----------------------------------------------------------------------------


def test_estadisticas_de_mascotas() -> None:
    adopt = pet_adopt_stats(species="gato", spawned=False, owned=2, adoption=True)
    assert adopt.add == {"pet_species_gato": 1, "pet_adopted": 1, "pet_protectora": 1}
    assert adopt.peak == {"pet_owned_max": 2}
    spawn = pet_adopt_stats(species="cucaracha", spawned=True, owned=1)
    assert spawn.add["pet_spawned"] == 1 and "pet_adopted" not in spawn.add
    san_anton = datetime(2027, 1, 17, 4, tzinfo=TIMEZONE).timestamp()
    care = pet_care_stats(
        action="comer", points=6, favourite=True, level=2, tricks=0, streak=3, gift=True,
        when=san_anton, food_key="modelo_100", species="cabra",
    )  # fmt: skip
    for stat in ("pet_cares", "pet_fed", "pet_favourite", "pet_gifts", "pet_goat_tax",
                 "pet_night", "pet_san_anton"):  # fmt: skip
        assert care.add[stat] == 1, stat
    spam = pet_care_stats(
        action="acariciar", points=0, favourite=False, level=2, tricks=0, streak=3,
        gift=False, when=NOW,
    )  # fmt: skip
    assert "pet_cares" not in spam.add, "lo que no suma vínculo no cuenta"
    assert pet_name_stats(name="Pedro Sánchez").add["pet_named_sanxe"] == 1
    full = with_derived({"pet_species_gato": 1, "pet_species_cucaracha": 1, "pet_species_x": 4})
    assert full[PET_SPECIES_STAT] == 2 and full[PET_SPAWN_KINDS_STAT] == 1


# -- Repositorio -----------------------------------------------------------------------


async def make_stores(tmp_path: Path) -> tuple[EconomyService, ShopRepository, PetRepository]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE * 1_000)
    await economy_repo.initialize()
    shop = ShopRepository(tmp_path / "bot.db")
    await shop.initialize()
    pets = PetRepository(tmp_path / "bot.db")
    await pets.initialize()
    return EconomyService(economy_repo, clock=Clock()), shop, pets


async def adopt(economy: EconomyService, shop: ShopRepository, key: str) -> None:
    item = await shop.item_by_key(GUILD, key)
    assert item is not None
    price = quote(item, NOW)
    await economy.purchase(
        GUILD, OWNER, base=price.base, tax=price.tax, concept=item.kind.value,
        reserve=shop.reserve(GUILD, OWNER, item.id, expected=price, level=99, now=NOW),
    )  # fmt: skip


async def test_base_de_datos_antigua_acepta_mascotas_sin_perder_nada(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('rol', 'xp', 'objeto')),
            name TEXT NOT NULL, emoji TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            price INTEGER NOT NULL CHECK (price > 0), igic TEXT NOT NULL,
            role_id INTEGER, duration INTEGER, multiplier INTEGER, stock INTEGER,
            sold INTEGER NOT NULL DEFAULT 0 CHECK (sold >= 0), per_user INTEGER,
            min_level INTEGER NOT NULL DEFAULT 0, discount INTEGER NOT NULL DEFAULT 0,
            discount_until REAL, visible INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
        );
        INSERT INTO shop_items (id, guild_id, kind, name, emoji, price, igic, created_at, sold)
        VALUES (42, 1, 'objeto', 'Piedra', '🪨', 1, 'general', 0, 3);
        -- El 77 se retiró: su id no se puede volver a usar.
        INSERT INTO shop_items (id, guild_id, kind, name, emoji, price, igic, created_at)
        VALUES (77, 1, 'objeto', 'Retirado', '🗑️', 1, 'general', 0);
        DELETE FROM shop_items WHERE id = 77;
        """
    )
    connection.commit()
    connection.close()
    shop = ShopRepository(path)
    await shop.initialize()
    await shop.initialize()  # idempotente
    old = await shop.item(GUILD, 42)
    assert old is not None and old.name == "Piedra" and old.sold == 3
    await shop.stock_catalog(GUILD, [CATALOG_BY_KEY["mascota_gato"]], NOW)
    cat = await shop.item_by_key(GUILD, "mascota_gato")
    assert cat is not None and cat.kind is Kind.PET and cat.id > 77, "ningún id retirado vuelve"


async def test_las_que_aparecen_solas_estan_ocultas_y_no_se_venden(tmp_path: Path) -> None:
    economy, shop, _pets = await make_stores(tmp_path)
    await shop.stock_catalog(GUILD, CATALOG, NOW)
    shown = {i.catalog_key for i in await shop.items(GUILD)}
    assert "mascota_gato" in shown and "mascota_cucaracha" not in shown
    roach = await shop.item_by_key(GUILD, "mascota_cucaracha")
    assert roach is not None and not roach.visible
    # Aunque un administrador la saque al escaparate, no se vende.
    await shop.update_item(GUILD, roach.id, visible=1)
    from bot.services.shop import ShopError

    with pytest.raises(ShopError, match="aparece sola"):
        await adopt(economy, shop, "mascota_cucaracha")


async def test_adoptar_crea_su_estado_y_una_de_cada_especie(tmp_path: Path) -> None:
    economy, shop, pets = await make_stores(tmp_path)
    await shop.stock_catalog(GUILD, CATALOG, NOW)
    await adopt(economy, shop, "mascota_gato")
    from bot.services.shop import ShopError

    with pytest.raises(ShopError, match="por persona"):
        await adopt(economy, shop, "mascota_gato")
    (cat,) = await pets.pets(GUILD, OWNER)
    assert cat.species == "gato" and cat.bond == 0 and cat.name == ""
    assert await pets.owned_species(GUILD, OWNER) == {"gato"}
    cat.name, cat.bond = "Michi", 12
    await pets.save_care(cat, streak=2, best_streak=5, day="2026-10-07")
    await pets.set_active(GUILD, OWNER, cat.id)
    owner = await pets.owner(GUILD, OWNER)
    assert (owner.active_id, owner.streak, owner.best_streak) == (cat.id, 2, 5)
    (active,) = await pets.active_pets()
    assert active.name == "Michi" and active.bond == 12
    await pets.delete_guild_data(GUILD)
    assert await pets.active_pets() == []


# -- Cog -------------------------------------------------------------------------------


def make_guild() -> MagicMock:
    guild = MagicMock()
    guild.id = GUILD
    return guild


def make_member(guild: MagicMock, user_id: int = OWNER) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = f"Miembro{user_id}"
    member.bot = False
    member.guild = guild
    return member


def make_interaction(member: MagicMock) -> MagicMock:
    interaction = fake_interaction(member)
    interaction.guild = member.guild
    interaction.channel = MagicMock()
    return interaction


async def make_cog(tmp_path: Path, clock: Clock, rng: random.Random | None = None):  # noqa: ANN201
    economy, shop, pets = await make_stores(tmp_path)
    await shop.stock_catalog(GUILD, CATALOG, NOW)
    bot = MagicMock()
    bot.get_cog.return_value = None  # sin logros en estas pruebas
    cog = Mascotas(bot, pets, shop, clock=clock, rng=rng or random.Random(3), scene=FakePetScene())
    await cog.cog_load()
    return cog, economy


def texts(view: ui.LayoutView) -> str:
    return "\n".join(c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay))


async def test_sin_mascotas_el_panel_dice_como_conseguirlas(tmp_path: Path) -> None:
    cog, _economy = await make_cog(tmp_path, Clock())
    guild = make_guild()
    panel = await cog.panel(guild, make_member(guild), make_member(guild))
    assert "tienda" in texts(panel) and "Cucaracha" in texts(panel)


async def test_adoptar_la_lleva_contigo_y_da_bonus_al_cuidarla(tmp_path: Path) -> None:
    clock = Clock()
    cog, economy = await make_cog(tmp_path, clock)
    guild = make_guild()
    member = make_member(guild)
    await adopt(economy, cog.shop, "mascota_canario")
    item = await cog.shop.item_by_key(GUILD, "mascota_canario")
    await cog.adopted(GUILD, member, None, item)
    assert cog.active[(GUILD, OWNER)].species == "canario"
    assert cog.xp_bonus(GUILD, OWNER, voice=True) == 1.0, "a nivel 0 no hay bonus"

    panel = await cog.panel(guild, member, member)
    assert "Va contigo" in texts(panel) and "Canario" in texts(panel)
    for _ in range(CARE_RULES[Care.PET].daily):
        click = make_interaction(member)
        await cog.care(click, panel, Care.PET)
        click.response.defer.assert_awaited_once()
        click.edit_original_response.assert_awaited()
        clock.now += CARE_RULES[Care.PET].cooldown
    assert "vínculo" in texts(panel)
    pet = cog.active[(GUILD, OWNER)]
    assert pet.bond == CARE_RULES[Care.PET].daily * CARE_RULES[Care.PET].points
    pet.bond = BOND_LEVELS[2]
    # El canario canta en voz: ahí el bonus es doble y por escrito no hay.
    assert cog.xp_bonus(GUILD, OWNER, voice=True) > 1.0
    assert cog.xp_bonus(GUILD, OWNER, voice=False) == 1.0
    # Demasiado pronto: espera y no suma.
    click = make_interaction(member)
    clock.now -= CARE_RULES[Care.PET].cooldown - 1
    await cog.care(click, panel, Care.PET)
    assert click.followup.send.await_args.kwargs["ephemeral"] is True


async def test_dar_de_comer_gasta_lo_de_la_mochila(tmp_path: Path) -> None:
    cog, economy = await make_cog(tmp_path, Clock(), Rigged(0.99))  # sin regalo
    guild = make_guild()
    member = make_member(guild)
    await adopt(economy, cog.shop, "mascota_gato")
    await adopt(economy, cog.shop, "lata_atun")
    await adopt(economy, cog.shop, "piedra")  # no se come
    panel = await cog.panel(guild, member, member)

    click = make_interaction(member)
    await cog.open_feeding(click, panel)
    picker = click.followup.send.await_args.kwargs["view"]
    assert isinstance(picker, FoodPicker)
    assert [o.description for o in picker.select.options] == ["⭐ Su favorita"]
    picker.select._values = [picker.select.options[0].value]  # type: ignore[attr-defined]
    pick = make_interaction(member)
    await picker._picked(pick)
    assert "favorita" in pick.edit_original_response.await_args.kwargs["content"]
    left = {e.catalog_key for e in await cog.shop.inventory(GUILD, OWNER, NOW)}
    assert "lata_atun" not in left and "piedra" in left
    (cat,) = await cog.repository.pets(GUILD, OWNER)
    assert cat.bond == CARE_RULES[Care.FEED].points + FAVOURITE_BONUS


async def test_la_cabra_no_se_come_lo_de_edicion_limitada(tmp_path: Path) -> None:
    """Ni comprado (con número de serie) ni dado con `crear` (sin número)."""
    cog, economy = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    await adopt(economy, cog.shop, "mascota_cabra")
    await adopt(economy, cog.shop, "patata_espana")  # ejemplar único, nº 1/1
    await adopt(economy, cog.shop, "piedra")
    cubito = await cog.shop.item_by_key(GUILD, "cubito")  # cien numerados
    assert cubito is not None and cubito.stock is not None
    await cog.shop.grant(GUILD, OWNER, cubito, NOW)
    panel = await cog.panel(guild, member, member)
    assert "edición limitada" in texts(panel)

    click = make_interaction(member)
    await cog.open_feeding(click, panel)
    picker = click.followup.send.await_args.kwargs["view"]
    assert [o.label for o in picker.select.options] == ["Piedra"]


async def test_sin_comida_avisa_y_la_piedra_no_come(tmp_path: Path) -> None:
    cog, economy = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    await adopt(economy, cog.shop, "mascota_gato")
    panel = await cog.panel(guild, member, member)
    click = make_interaction(member)
    await cog.open_feeding(click, panel)
    assert "nada de comer" in click.followup.send.await_args.args[0]

    await adopt(economy, cog.shop, "mascota_pedrusco")
    await panel.load()
    panel.selected = next(p.id for p in panel.pets if p.species == "pedrusco")
    click = make_interaction(member)
    await cog.open_feeding(click, panel)
    assert "no come" in texts(panel)


async def test_ponerle_nombre_y_cambiar_de_mascota(tmp_path: Path) -> None:
    cog, economy = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    for key in ("mascota_gato", "mascota_perro"):
        await adopt(economy, cog.shop, key)
        await cog.adopted(GUILD, member, None, await cog.shop.item_by_key(GUILD, key))
    assert cog.active[(GUILD, OWNER)].species == "gato", "la primera se queda"
    panel = await cog.panel(guild, member, member)
    panel.selected = next(p.id for p in panel.pets if p.species == "perro")
    await cog.activate(make_interaction(member), panel)
    assert cog.active[(GUILD, OWNER)].species == "perro"

    bad = make_interaction(member)
    await cog.rename(bad, panel, panel.current, "<@123>")
    assert bad.response.send_message.await_args.kwargs["ephemeral"] is True
    await cog.rename(make_interaction(member), panel, panel.current, "  Toby  ")
    assert cog.active[(GUILD, OWNER)].name == "Toby"
    assert "Toby" in texts(panel)


async def test_otros_miran_pero_no_tocan(tmp_path: Path) -> None:
    cog, economy = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member, other = make_member(guild), make_member(guild, OTHER)
    await adopt(economy, cog.shop, "mascota_gato")
    panel = await cog.panel(guild, member, other)
    assert not [c for c in panel.walk_children() if isinstance(c, ui.Button)]
    click = make_interaction(other)
    assert not await panel.interaction_check(click)


async def test_la_mascota_activa_sale_en_los_mensajes(tmp_path: Path) -> None:
    clock = Clock()
    cog, economy = await make_cog(tmp_path, clock, Rigged(0.99))
    guild = make_guild()
    member = make_member(guild)
    assert await cog.cameo(GUILD, OWNER, None) is None, "sin mascota, nada"
    await adopt(economy, cog.shop, "mascota_gato")
    await cog.adopted(GUILD, member, None, await cog.shop.item_by_key(GUILD, "mascota_gato"))
    assert await cog.cameo(GUILD, OWNER, Moment(Event.WIN)) is None, "una jugada normal, a veces"
    line = await cog.cameo(GUILD, OWNER, Moment(Event.BIG_WIN))
    assert line is not None and line.startswith("-# 🐈 ") and "Gato común europeo" in line


async def test_la_cucaracha_aparece_al_quedarse_a_cero(tmp_path: Path) -> None:
    cog, _economy = await make_cog(tmp_path, Clock(), Rigged(0.0))
    line = await cog.cameo(GUILD, OWNER, bet_moment(stake=100, net=-100, balance_after=0))
    assert line is not None and "Cucaracha" in line and "mascota" in line
    assert await cog.repository.owned_species(GUILD, OWNER) == {"cucaracha"}
    assert cog.active[(GUILD, OWNER)].species == "cucaracha"
    # Ya la tiene: no vuelve a aparecer otra.
    again = await cog.cameo(GUILD, OWNER, bet_moment(stake=100, net=-100, balance_after=0))
    assert again is not None and "Sorpresa" not in again
    assert len(await cog.repository.pets(GUILD, OWNER)) == 1


async def test_sin_momento_no_aparece_ninguna(tmp_path: Path) -> None:
    cog, _economy = await make_cog(tmp_path, Clock(), Rigged(0.0))
    assert await cog.cameo(GUILD, OWNER, None) is None


async def test_el_primer_cuidado_del_dia_puede_traer_un_regalo(tmp_path: Path) -> None:
    cog, economy = await make_cog(tmp_path, Clock(), Rigged(0.0))
    guild = make_guild()
    member = make_member(guild)
    await adopt(economy, cog.shop, "mascota_perro")
    panel = await cog.panel(guild, member, member)
    await cog.care(make_interaction(member), panel, Care.PET)
    gifts = [e for e in await cog.shop.inventory(GUILD, OWNER, NOW) if e.kind is Kind.TROPHY]
    assert len(gifts) == 1 and "🎁" in texts(panel)
    gift = CATALOG_BY_KEY[gifts[0].catalog_key or ""]
    assert gift.use is None and gift.stock is None and gift.price <= 1_000


def test_el_panel_cabe_en_discord() -> None:
    """Con muchas mascotas, el panel no pasa de 40 componentes ni de 4.000 caracteres."""
    guild = make_guild()
    member = make_member(guild)
    panel = PetPanel(MagicMock(clock=Clock()), guild, member, member)
    panel.pets = [
        PetState(id=i, guild_id=GUILD, user_id=OWNER, species=s.key, name="N" * MAX_NAME,
                 bond=10**6)
        for i, s in enumerate(SPECIES * 2)
    ]  # fmt: skip
    panel.selected = 0
    panel.note = "x" * 400
    panel.rebuild()
    assert panel.total_children_count <= 40
    assert len(texts(panel)) <= 4_000
