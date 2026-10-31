"""Pruebas de los logros: catálogo, reglas, repositorio, cog y premios con IRPF."""

from __future__ import annotations

import asyncio
import itertools
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.achievements import (
    Achievements,
    category_embed,
    category_page_count,
    format_value,
    group_embed,
    summary_embed,
    unlock_embeds,
)
from bot.repositories.achievements import AchievementRepository, Profile
from bot.repositories.economy import EconomyRepository
from bot.repositories.message_stats import MessageStatsRepository
from bot.services.achievements import (
    APUESTAS_PAGES,
    APUESTAS_PERIODS,
    ATTACH_KINDS,
    ATTACH_KINDS_STAT,
    AVAILABLE,
    BACKGAMMON_BEAT_PREFIX,
    BJ_HIGH_STAKE,
    BUS_WIN_PREFIX,
    BY_ID,
    CASINO_DATES,
    CASINO_GAMES_100_STAT,
    CASINO_GAMES_1000_STAT,
    CASINO_GAMES_STAT,
    CASINO_GROUP,
    CASINO_NIGHT_GAMES_STAT,
    CATALOG,
    CATEGORIES,
    CHICKEN_VEHICLE_KINDS,
    COUNTED,
    ENRAYA_BEAT_PREFIX,
    FIRST_TAX_WEEKLY,
    FUNNY_TOPICS,
    GALLOS_BEAT_PREFIX,
    GALLOS_FREE_SKINS,
    GALLOS_SKIN_PREFIX,
    GALLOS_TRAINED_BEAT_PREFIX,
    HORSE_BACKED_KINDS_STAT,
    HORSE_BACKED_MAX_STAT,
    HORSE_BET_KINDS,
    HORSE_DISTANCES,
    HOUSING_ACTIONS,
    IMG_EFFECTS_STAT,
    IRONY_PATTERNS,
    LAUGH_KINDS,
    LINK_KINDS_STAT,
    LINK_SITES,
    MESSAGES_TOTAL_STAT,
    PET_SPAWN_KINDS_STAT,
    PET_SPECIES_PREFIX,
    PET_SPECIES_STAT,
    PISOS_BARRIO_PREFIX,
    PISOS_BARRIOS_STAT,
    PISOS_BUILDING_PREFIX,
    PISOS_BUILDINGS_SEEN_STAT,
    PISOS_EVENT_PREFIX,
    PISOS_KIND_PREFIX,
    PISOS_KINDS_STAT,
    PISOS_LEVEL_PREFIX,
    PISOS_NEWS_PREFIX,
    PISOS_NEWS_STAT,
    PISOS_REFORM_KINDS_STAT,
    PISOS_REFORM_PREFIX,
    PISOS_ROOM_TENANTS_STAT,
    PISOS_TENANT_PREFIX,
    PISOS_TENANTS_STAT,
    PISOS_TERM_PREFIX,
    PISOS_TERMS_STAT,
    PISOS_THEMES_STAT,
    POKER_MADE_PREFIX,
    PORRA_GAME_PREFIX,
    PORRA_GAMES,
    PORRA_PROP_PREFIX,
    PORRA_PROPS,
    PORRA_STATS,
    REACT_KINDS_STAT,
    REACT_PREFIX,
    REACTED_PREFIX,
    REACTION_EMOJIS,
    ROOM_TENANTS,
    ROULETTE_FAVOURITE_STAT,
    ROULETTE_HIT_PREFIX,
    ROULETTE_NUMBERS_STAT,
    SALDO_DAYS_STAT,
    SALDO_FUNNY,
    SALDO_KINDS_STAT,
    SALDO_STREAK_STAT,
    SECRET_STAKES,
    SHOP_AISLES_STAT,
    SHOP_TRACKED_KEYS,
    SHOP_USE_KINDS_STAT,
    SPORTS_LEAGUE_PREFIX,
    SPORTS_LEAGUES,
    SPORTS_MARKET_PREFIX,
    SPORTS_MARKETS,
    SPORTS_PICK_PREFIX,
    SPORTS_PICKS,
    TOPIC_KINDS_STAT,
    UNLOCKED_STAT,
    VOICE_BESTIE_STAT,
    VOICE_GAME_PREFIX,
    VOICE_GAMES,
    VOICE_GAMES_STAT,
    VOICE_PEOPLE_STAT,
    VOICE_STREAK_STAT,
    VOICE_WITH_PREFIX,
    Rarity,
    StatDelta,
    beernight_close_stats,
    beernight_drink_stats,
    blackjack_shoe_stats,
    blackjack_stats,
    casino_record_stats,
    casino_stats,
    group_sections,
    housing_action_stats,
    housing_panel_stats,
    housing_purchase_stats,
    housing_sale_stats,
    housing_streets_stats,
    is_laugh,
    jovani_stats,
    menu_entries,
    message_stats,
    meta_stats,
    newly_unlocked,
    pachinko_autoplay_stats,
    pachinko_stats,
    progress,
    roulette_stats,
    slots_autoplay_stats,
    slots_stats,
    sports_bet_stats,
    sports_result_stats,
    sports_slip_stats,
    sports_void_stats,
    total_reward,
    with_derived,
)
from bot.services.allatu import TABLES
from bot.services.aspecto import BUILTIN_LOOKS
from bot.services.autoplay import StopReason
from bot.services.backgammon import LEVELS as BACKGAMMON_LEVELS
from bot.services.beernight import Reason as BeerReason
from bot.services.blackjack import PIT_BOSS_SPREAD, BlackjackGame, Card, Hand
from bot.services.bus import Pick as BusPick
from bot.services.casino_stats import GAMES as CASINO_GAMES
from bot.services.chicken import DIFFICULTIES as CHICKEN_DIFFICULTIES
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService, IncomeResult
from bot.services.enraya import LEVELS as ENRAYA_LEVELS
from bot.services.gallos import HOUSE as GALLOS_HOUSE
from bot.services.housing import EVENTS as _HOUSING_EVENTS
from bot.services.housing_catalog import BARRIO_BY_KEY as HOUSING_BARRIOS
from bot.services.housing_catalog import BARRIO_NEWS as HOUSING_NEWS_LIST
from bot.services.housing_catalog import KINDS as HOUSING_KINDS
from bot.services.housing_catalog import LISTING_BY_KEY as HOUSING_LISTING_BY_KEY
from bot.services.housing_catalog import REFORMS as HOUSING_REFORM_LIST
from bot.services.housing_catalog import RENT_LEVELS as HOUSING_LEVELS
from bot.services.housing_catalog import TENANTS as HOUSING_TENANT_LIST
from bot.services.housing_streets import BUILDINGS as STREET_BUILDINGS
from bot.services.housing_streets import STREET_UNIT_BY_KEY, STREET_UNITS
from bot.services.housing_streets import Building as StreetBuilding
from bot.services.levels import TIMEZONE
from bot.services.lootboxes import BOXES as LOOTBOXES
from bot.services.lootboxes import COLLECTIONS as LOOTBOX_COLLECTIONS
from bot.services.lootboxes import Rarity as LootRarity
from bot.services.lottery import GAMES as LOTTERY_GAMES
from bot.services.pachinko import CLASSIC, Ball, Draw, build_volley
from bot.services.pachinko import Kind as PachinkoKind
from bot.services.pachinko_motion import motion_for
from bot.services.pets_catalog import SPECIES as PET_SPECIES
from bot.services.picar_physics import Run as PicarRun
from bot.services.picar_world import MINERALS as PICAR_MINERALS
from bot.services.poker import Rank as PokerRank
from bot.services.robuso import DAILY as ROBUSO_DAILY
from bot.services.robuso import ECHOES as ROBUSO_ECHOES
from bot.services.robuso import ROBUSO_ID
from bot.services.roulette import DOUBLE_ZERO, OUTSIDE_BETS, RoundOutcome, Wager, parse_bet
from bot.services.shop_uses import USES as SHOP_USES
from bot.services.slots import REEL_STRIPS, Kind, spin_at
from bot.services.slots import pot_share as slots_pot_share
from bot.services.tower import DIFFICULTIES as TOWER_DIFFICULTIES
from bot.services.work import WISHES
from bot.services.work_catalog import EVENTS, RESIGN_EVENT

GUILD = 1
USER = 10

# Todas las estadísticas que produce algún sitio del bot. Si un logro usa
# otra, nadie la sumaría nunca y sería imposible de conseguir.
#: Contadores de las acciones de las fases 2 a 4.
HOUSING_ACTIONS_STATS = tuple(HOUSING_ACTIONS.values())

#: Lo que puede pasar un día en un inmueble: los imprevistos y los cambios de estado.
HOUSING_EVENT_KEYS = (
    *(event.key for event in _HOUSING_EVENTS),
    "firma", "renueva", "se_va", "impago", "paga", "okupa", "desahucio",
    "cuota_impagada", "ejecucion", "sorteo_vpo", "vpo_caducada", "partida_agotada",
    "tope_ley", "recargo_ibi", "oferta_fondo", "ejecucion_aval", "fianza_devuelta",
    "fianza_incautada", "firma_negocio", "candidato", "seguro", "noticia", "obra_terminada",
    "decision_cunado", "decision_vuelve", "decision_espera", "decision_parte",
    "decision_mediacion", "superanfitrion_logrado", "limpieza", "licencia_renovada",
    "licencia_caduca", "fondo_reserva", "prorroga", "no_renovado", "sale_llaves",
    "sale_necesidad",
)  # fmt: skip

PRODUCED_STATS = {
    # 🏪 Negocio propio de pala (`business_stats` y las acciones del negocio)
    "biz_opened", "biz_turns", "biz_turns_cafeteria", "biz_turns_tienda", "biz_turns_taller",
    "biz_sales", "biz_tips", "biz_best_turn", "biz_clients_turn_max", "biz_stars_max",
    "biz_loyalty_max", "biz_usual_hit", "biz_perfect", "biz_grade_d", "biz_wrong", "biz_exact",
    "biz_caught", "biz_missed_thief", "biz_false_accuse", "biz_diag_ok", "biz_bad_diag",
    "biz_quote_caro", "biz_night", "biz_canarias", "biz_tired", "biz_empty_turn", "biz_withheld",
    "biz_garnished", "biz_streak_max", "biz_turns_day_max", "biz_clients", "biz_sectors_tried",
    "biz_themes_tried", "biz_islands_tried", "biz_restyled", "biz_purchases",
    "biz_price_barato", "biz_price_normal", "biz_price_caro", "biz_price_carisimo",
    "biz_nights_unpaid", "biz_closed_red", "biz_evicted", "work_jornada_4", "work_jornada_2",
    "biz_sunday", "biz_fast", "biz_left", "biz_rejected", "biz_over", "biz_short",
    "biz_spent_stock", "biz_nights", "biz_closed",
    "biz_turns_heladeria", "biz_turns_ferreteria", "biz_reviews", "biz_reviews_5",
    "biz_reviews_1", "biz_recommended", "biz_equipment", "biz_spent_equipment",
    "biz_equipment_max", "biz_campaigns", "biz_campaign_buzoneo", "biz_campaign_redes",
    "biz_campaign_radio", "biz_spent_marketing", "biz_price_product", "biz_moves",
    "biz_own_local", "biz_size_max", "biz_traspasos", "biz_traspaso_money", "biz_traspaso_max",
    "work_jornada_3",
    "work_excedencia", "work_excedencia_back",
    "biz_sl_founded", "biz_sl_capital_max", "biz_sl_contributed", "biz_sl_lent", "biz_sl_repaid",
    "biz_sl_salaries", "biz_sl_salary_paid", "biz_sl_dividends", "biz_sl_dividend_paid",
    "biz_sl_weeks", "biz_sl_tax_paid", "biz_sl_loss", "biz_sl_profit_max", "biz_sl_liquidated",
    "biz_sl_sunk", "biz_sl_quota_max", "biz_pluri_refund",
    # Préstamos al negocio: business_loan_*_stats (banco_negocio), sus noches y el cierre.
    "biz_loans_signed", "biz_loan_principal_max", "biz_loans_open_max", "biz_loan_company",
    "biz_loan_longest", "biz_loans_repaid_early", "biz_loan_installments", "biz_loan_unpaid",
    "biz_loans_finished", "biz_loan_defaults", "biz_loan_assumed",
    # La RIC y la ZEC (fase 4 bis), desde el cog.
    "biz_ric_set", "biz_ric_reserved", "biz_ric_lost", "biz_zec_joined", "biz_zec_weeks",
    # La plantilla (fase 4): staff_*_stats.
    "staff_turns", "staff_clients", "staff_sales", "staff_turn_s", "staff_turn_d",
    "staff_absences", "staff_idle", "staff_sales_turn_max", "staff_clients_turn_max",
    "staff_nights", "staff_payroll", "staff_unpaid", "staff_inspected", "staff_fines",
    "staff_courses_done", "staff_trials", "staff_contract_end", "staff_art50", "staff_quit",
    "staff_hired", "staff_hired_indefinido", "staff_hired_temporal", "staff_hired_b",
    "staff_hired_half", "staff_hired_bonus", "staff_hired_senior", "staff_hired_young",
    "staff_size_max", "staff_wage_max", "staff_fired", "staff_fired_prueba",
    "staff_fired_disciplinario", "staff_fired_objetivo", "staff_fired_improcedente",
    "staff_fired_b", "staff_severance", "staff_bonus_refund", "staff_raises",
    "staff_permanent", "staff_courses", "staff_course_spent", "staff_fundae", "staff_skill_max",
    # Las cadenas (fase 5): locales, nave, importar, gerentes y vender la sociedad.
    "biz_locales_max", "biz_chain_sectors_max", "biz_chain_islands_max", "biz_chain_brand_max",
    "biz_nave_size_max", "biz_nave_fill_max", "biz_managers_max", "biz_sale_price_max",
    "biz_sale_locales_max", "biz_sl_chain_max", "biz_chain_opened", "biz_branch_closed",
    "biz_nave_opened", "biz_nave_own", "biz_nave_closed", "biz_nave_lost", "biz_imports",
    "biz_import_spent", "biz_aiem_paid", "biz_import_saved", "biz_import_aiem_free",
    "biz_containers", "biz_from_nave", "biz_managers_hired", "biz_manager_restocks",
    "biz_manager_nave", "biz_manager_hires", "biz_manager_absent", "biz_company_sold",
    "biz_sale_gain", "biz_sale_tax", "biz_sale_loss",
    # cogs/achievements.py: mensajes, voz, reacciones y la siembra del historial
    *message_stats("x", when=datetime(2026, 1, 1, tzinfo=TIMEZONE)).keys(),
    "msg_night", "msg_morning", "msg_long", "msg_short", "msg_caps", "msg_questions",
    "msg_links", "msg_xd", "msg_laughs", "msg_attachments", "msg_stickers", "msg_replies",
    "msg_mentions", "msg_leet", "msg_new_year", "msg_halloween",
    "msg_christmas", "msg_canarias", "msg_own_birthday", "msg_bot_call", "msg_sanxe",
    "messages_imported", MESSAGES_TOTAL_STAT,
    "voice_minutes", "voice_muted", "voice_stream", "voice_video", "voice_night",
    "voice_alone", "voice_session_max", "voice_crowd_max",
    "reactions_given", "reactions_received", "reactions_on_message_max",
    # Cumpleaños, niveles, IMV, renta y premios
    "greetings_sent", "greetings_received", "level_max", "activity_streak_max",
    "imv_claims", "imv_streak_max", "imv_midnight", "renta_filed", "renta_refunded", "tax_paid",
    "balance_max",
    # La declaración semanal (cogs/renta.py: track_opened, _detail y _close_weeks)
    "renta_opened", "renta_opened_pay", "renta_opened_refund", "renta_detail", "renta_closed",
    "renta_breakdown", "renta_advice", "renta_advice_donated", "renta_advice_exact",
    "renta_paid", "renta_debt", "renta_rate_max", "renta_split", "debt_paid", "debt_garnished",
    # Bienvenida (botón 👋 de cogs/welcome.py)
    "welcomes_given", "welcomes_fast",
    # Lista de tareas (cogs/todo.py)
    "todo_added", "todo_done", "todo_edited", "todo_demoted",
    # 🎤 Jovani (`jovani_stats` y `with_derived`)
    "jovani_days", "jovani_looks", "jovani_1337", "jovani_midnight", "jovani_friday13",
    *(f"jovani_look_{key}" for key in (
        "pijama", "hacienda", "casino", "actualizado", "beernight", "dragon", "carnaval",
        "canarias", "halloween", "navidad", "aniversario",
    )),
    # Beernight (cogs/beernight.py): beber, cerrar la noche y los botones de cada uno
    *(stat for reason in BeerReason
      for stat in (*beernight_drink_stats(reason.value, 1, forgiven=1).add,
                   *beernight_drink_stats(reason.value, 1).peak)),
    *(stat for day in ((12, 31), (5, 30), (9, 7), (10, 31), (6, 23))
      for delta in [beernight_close_stats(
          sips=69, minutes=0, crowd=1, mvp=True, host=True, streak=1,
          started=datetime(2026, *day, 22, tzinfo=TIMEZONE),
          ended=datetime(2026, *day, 6, tzinfo=TIMEZONE))]
      for stat in (*delta.add, *delta.peak)),
    "beer_thursday", "beer_monday", "beer_zero_night",
    "beer_reports_ok", "beer_given", "beer_snitch_host", "beer_confirms", "beer_denies",
    "beer_duels_won", "beer_duels_lost", "beer_challenges_ok", "beer_challenges_failed",
    "beer_custom_added", "beer_custom_broken", "beer_sound_saved", "beer_retired",
    "beer_events_forced",
    # Patrimonio (cogs/patrimonio.py) y donativos (cogs/donations.py)
    "wealth_tax_paid", "wealth_tax_weeks", "wealth_embargo", "donated", "ongs_supported",
    "wealth_joint_limit", "wealth_goods_taxed", "wealth_solidarity",
    # Intereses de la cuenta (cogs/intereses.py: day_stats, savings_stats y hint_for)
    "interest_earned", "interest_days", "interest_tax", "interest_capped", "interest_rounding",
    "interest_zero", "interest_grasshopper", "interest_gambled", "interest_beats_imv",
    "interest_bizum_trick", "interest_avg_max", "interest_capped_streak", "interest_floor_streak",
    "interest_resist_streak", "interest_still_streak", "interest_ant_streak", "savings_rate_max",
    "savings_week_max",
    "interest_comeback",
    # Saldo (cogs/casino.py: saldo_stats y saldo_peek_stats; test_saldo.py las suma)
    "saldo_checks", "saldo_peeks", "saldo_peek_richer", "saldo_pending_max", "saldo_all_sides",
    "saldo_red", "saldo_clean", "saldo_debt", "saldo_sealed", "saldo_night", "saldo_monday",
    "saldo_mortgage_unpaid", "saldo_imv_late", "saldo_zero",
    *(f"saldo_exact_{n}" for n in SALDO_FUNNY), SALDO_DAYS_STAT, SALDO_STREAK_STAT,
    SALDO_KINDS_STAT,
    # Tienda (cogs/shop.py: shop_stats)
    "shop_purchases", "shop_spent", "shop_igic", "shop_roles", "shop_renewals", "shop_boosts",
    "shop_boost_queue_max", "shop_collection_max", "shop_sale_buys", "shop_discount_max",
    "shop_luxury", "shop_big_buy_max", "shop_limited", "shop_first_serial", "shop_last_unit",
    "shop_broke_buy", SHOP_AISLES_STAT, *(f"shop_key_{key}" for key in SHOP_TRACKED_KEYS),
    # Carrito, ficha y lista de deseos (cogs/shop.py y cogs/shop_front.py: shop_cart_stats,
    # shop_wish_stats, shop_wish_alert_stats y shop_browse_stats)
    "shop_carts", "shop_cart_lines_max", "shop_cart_units_max", "shop_ticket_rates_max",
    "shop_same_max", "shop_wishes", "shop_wishlist_max", "shop_wish_buys", "shop_wish_sale_buys",
    "shop_wish_alerts", "shop_wish_stalls", "shop_searches", "shop_sheets",
    "shop_cart_black_friday", "shop_cart_rebajas", "shop_cart_42", "shop_cart_emptied",
    "shop_cart_failed",
    # Títulos (cogs/shop.py: shop_stats al comprar y shop_title_stats en la mochila)
    "shop_titles", "shop_title_swaps", "shop_title_off",
    # Skins (cogs/shop.py: shop_stats al comprar y shop_skin_stats al cambiarla)
    "shop_skins", "shop_skin_swaps", "shop_skin_panel", "shop_skin_image_uploads",
    "shop_skin_image_admin", "shop_skin_image_censored",
    # Usar objetos (cogs/shop.py: shop_use_stats y shop_hit_stats)
    "shop_uses", SHOP_USE_KINDS_STAT, *(f"shop_used_{key}" for key in SHOP_USES),
    "shop_consumed", "shop_use_targeted", "shop_use_self", "shop_use_bot", "shop_use_hits",
    "shop_use_backfires", "shop_egg_collector", "shop_caught", "shop_d20_nat20",
    "shop_d20_nat1", "shop_padron_hot", "shop_robuso_asleep", "shop_nickname", "shop_mystery",
    "shop_mystery_jackpot", "shop_mystery_dupe", "shop_mystery_best", "shop_use_night",
    "shop_use_newyear", "shop_use_halloween", "shop_use_canarias", "shop_use_pino",
    "shop_got_hit", "shop_got_messy", "shop_got_love", "shop_got_raided",
    "shop_clover_broken", "shop_no_parsley", "shop_flash", "shop_fake_champagne",
    "shop_ball_dogs",
    # Cajas (cogs/shop.py: lootbox_stats al abrir, lootbox_free_stats al ganarlas jugando)
    "lootbox_opened", "lootbox_tapete", "lootbox_dupes", "lootbox_season", "lootbox_night",
    "lootbox_free", *(f"lootbox_box_{box.key}" for box in LOOTBOXES),
    *(f"lootbox_got_{rarity.stat}" for rarity in LootRarity),
    *(collection.stat for collection in LOOTBOX_COLLECTIONS),
    # Mascotas (cogs/pets.py: pet_adopt_stats, pet_care_stats, pet_cameo_stats…)
    "pet_adopted", "pet_spawned", "pet_protectora", "pet_owned_max", PET_SPECIES_STAT,
    PET_SPAWN_KINDS_STAT, *(f"{PET_SPECIES_PREFIX}{s.key}" for s in PET_SPECIES),
    "pet_cares", "pet_petted", "pet_played", "pet_fed", "pet_favourite", "pet_gifts",
    "pet_bond_max", "pet_tricks_max", "pet_streak_max", "pet_fed_nothing", "pet_goat_tax",
    "pet_goat_odd", "pet_night", "pet_san_anton", "pet_christmas", "pet_halloween",
    "pet_canarias", "pet_cameos", "pet_cameo_bust", "pet_cameo_big", "pet_cameo_night",
    "pet_renamed", "pet_named_sanxe", "pet_named_beast", "pet_switches",
    # cogs/rastro.py: ventas, compras, pujas y puestos del Rastro
    "market_sold", "market_sold_y", "market_bids_on_mine_max", "market_gain_tax",
    "market_swaps", "market_swap_money", "market_swap_lopsided", "market_swap_fleeced",
    "donations", "donated_desmontar", "donated_ayudante", "donated_mares", "donated_hambre",
    "donation_one", "donation_allin", "donation_666", "donation_christmas",
    "donated_gamers", "donated_influencers", "donated_platano", "donated_jovani",
    "patron_level_max", "ong_raffle_won", "ong_gifts", "ong_exclusive_gifts",
    "donated_corona", "donated_buitre", "donated_acogida", "patron_ongs", "patron_gold_ongs",
    "ong_exclusives_owned", "ong_exclusive_set_max", "donation_max", "donation_69",
    "donation_420", "donation_exact_level", "donation_dawn", "donation_canarias",
    "donation_last_call", "donation_corona_minor",
    "ong_raffle_entered", "ong_raffle_lost", "ong_raffle_week_max", "ong_raffle_alone",
    "ong_raffle_underdog", "donation_top_ong", "donation_top_all", "donation_friday13",
    "donation_halloween", "donation_innocents", "donation_new_year", "donation_kings",
    "donation_pino", "donation_ticket_exact", "donation_corona_rescue", "donation_buitre_rent",
    "donation_gamers_1337", "donation_hambre_lunch",
    *(f"ong_seen_{key}" for key in (
        "desmontar", "ayudante", "mares", "hambre", "gamers", "influencers", "platano",
        "jovani", "corona", "buitre", "acogida",
    )),
    "market_swap_titles",
    "market_loss", "market_gifts_given", "market_sold_one", "market_666", "market_sunday",
    "market_bought", "market_spent", "market_bought_max", "market_itp",
    "market_gift_received", "market_auction_won", "market_serial_bought",
    "market_title_bought", "market_tapete_bought", "market_listings", "market_offers",
    "market_withdrawn",
    "market_expired", "market_bids", "market_bid_max", "market_outbid",
    # cogs/rastro.py: el Monte de Piedad del panel de `vender` (monte_*_stats)
    "monte_sold", "monte_sold_y", "monte_title_sold", "monte_pawned", "monte_loan_max",
    "monte_tickets_max", "monte_redeemed", "monte_redeem_late", "monte_forfeited",
    # cogs/banco.py y el cierre de cogs/pisos.py: préstamos y cuentas (loan_*_stats,
    # bank_panel_stats)
    "loans_signed", "loan_principal_max", "loans_open_max", "loan_longest", "loan_insured",
    "loan_preferred", "loans_finished", "loans_repaid_early", "loans_desisted", "loan_covered",
    "loan_unpaid", "loan_defaults", "asnef_seen", "bank_books",
    # cogs/banco_compras.py: 💳 Financiar (financed_stats)
    "financed", "financed_bnpl", "financed_coffee", "financed_zero", "financed_trap",
    "financed_grace", "financed_works", "financed_max", "financed_open_max",
    "financed_could_pay", "financed_high_ipc",
    # cogs/banco.py (🔐 Garantías) y las ejecuciones de cogs/pisos.py: remortgage_*_stats
    "remortgages_signed", "remortgage_principal_max", "remortgages_open_max", "remortgage_home",
    "remortgage_not_home", "remortgage_shortest", "remortgage_full", "remortgage_minimum",
    "remortgage_executed",
    # cogs/banco_avales.py y el cierre de cogs/pisos.py: guarantee_*_stats
    "guarantees_given", "guarantees_received", "guarantees_open_max", "guarantee_refused",
    "guarantee_claimed", "guarantee_defaulted", "guarantee_repaid", "guarantee_recovered",
    # cogs/banco_anticipos.py y el cierre de cogs/pisos.py: advance_*_stats
    "advances_signed", "advance_longest", "advance_shortest", "advance_principal_max",
    "advances_open_max", "advance_repaid", "advance_desisted", "advance_orphan",
    # cogs/pisos.py: housing_panel_stats, housing_purchase_stats, housing_sale_stats y
    # housing_day_stats (los eventos salen de housing.EVENTS y de advance_day)
    "pisos_opened", "pisos_exit", "pisos_mortgages", "pisos_loan_max",
    "pisos_installments", "pisos_bono", *HOUSING_ACTIONS_STATS, "pisos_tax_total",
    "pisos_mortgage_floor", "pisos_mortgage_ceiling", "pisos_aval_mortgage",
    "pisos_overburden", "pisos_effort_peak", "pisos_unicorn", "pisos_guarantee",
    "pisos_aval_rent", "pisos_promotions", "pisos_promoter_fees", "pisos_new_build",
    "pisos_own_build",
    "pisos_tax_alquiler",
    "pisos_tax_ganancia", "pisos_tax_over_rent", "pisos_level_changes", "pisos_evictions",
    "pisos_squatters_evicted",
    "pisos_bought", "pisos_spent", "pisos_itp", "pisos_one_share", "pisos_whole_buy",
    "pisos_night", "pisos_canarias_day", "pisos_broke", "pisos_buy_max", "pisos_holdings_max",
    "pisos_whole_max", "pisos_islands_max", "pisos_sold", "pisos_sold_y", "pisos_sold_whole",
    "pisos_flip", "pisos_loss", "pisos_arrears_paid", "pisos_gain_max", "pisos_rent",
    "pisos_rent_days", "pisos_ibi", PISOS_BARRIOS_STAT, PISOS_KINDS_STAT, PISOS_THEMES_STAT,
    "pisos_tensed", "pisos_barrio_whole",
    # Fase 2 de la ampliación (cogs/pisos.py: housing_day_stats y housing_action_stats)
    "pisos_candidates_seen", "pisos_chose_risky", "pisos_chose_safe", "pisos_young70",
    "pisos_insurer_paid", "pisos_lawyer_free", "pisos_vacant_bonus", PISOS_TENANTS_STAT,
    PISOS_NEWS_STAT,
    # Fase 3 (cogs/pisos.py: housing_action_stats de la venta, las pujas y las subastas)
    "pisos_snipe", "pisos_judicial_won", "pisos_member_gift", "pisos_preference",
    "pisos_member_reference", "pisos_moved_in", "pisos_member_overpaid", "pisos_auction_max",
    # Fase 4 (housing_action_stats de la reforma y housing_day_stats con el humor)
    "pisos_reform_spent", "pisos_rehab", "pisos_mood_max", "pisos_mood_low",
    # Fase 5 (housing_day_stats con las reseñas)
    "pisos_reviews", "pisos_five_stars", "pisos_one_star",
    # Fase 6 (housing_action_stats de la junta aprobada sin más votos)
    "pisos_silence",
    # 9.12.0 (plantas, habitaciones y el atajo de candidatos)
    "pisos_split_spent", "pisos_split_units", "pisos_shortcut", "pisos_rooms_rushed",
    "pisos_split_mortgaged", "pisos_floor_sold", PISOS_ROOM_TENANTS_STAT,
    PISOS_TERMS_STAT, *(f"{PISOS_TERM_PREFIX}{n}" for n in (12, 24, 36, 48, 60)),
    "pisos_keys_offers", "pisos_keys_free_asks", "pisos_keys_refused", "pisos_keys_trench",
    "pisos_keys_dropped", "pisos_keys_bought", "pisos_keys_paid", "pisos_keys_max",
    "pisos_keys_fame", "pisos_keys_free", "pisos_keys_generous", "pisos_keys_counter",
    "pisos_keys_member", "pisos_keys_sold", "pisos_keys_sold_amount", "pisos_keys_haggles",
    "pisos_no_renew", "pisos_need", "pisos_need_fine", "pisos_need_fine_paid",
    "pisos_term_changes",
    PISOS_REFORM_KINDS_STAT, *(f"{PISOS_REFORM_PREFIX}{r.key}" for r in HOUSING_REFORM_LIST),
    *(f"{PISOS_TENANT_PREFIX}{t.key}" for t in HOUSING_TENANT_LIST),
    *(f"{PISOS_NEWS_PREFIX}{n.key}" for n in HOUSING_NEWS_LIST),
    *(f"{PISOS_KIND_PREFIX}{kind}" for kind in HOUSING_KINDS),
    *(f"{PISOS_BARRIO_PREFIX}{barrio}" for barrio in HOUSING_BARRIOS),
    *(f"{PISOS_LEVEL_PREFIX}{level}" for level in HOUSING_LEVELS),
    *(f"{PISOS_EVENT_PREFIX}{event}" for event in HOUSING_EVENT_KEYS),
    # 9.14.0 (las calles: housing_panel_stats y housing_streets_stats tras cada compra)
    "pisos_streets_opened", PISOS_BUILDINGS_SEEN_STAT, "pisos_street_units_max",
    "pisos_buildings_max", "pisos_streets_barrio_max", "pisos_pack", "pisos_whole_bloque",
    "pisos_whole_guardamuebles", "pisos_whole_aparcamiento", "pisos_whole_pantalan",
    "pisos_attic_basement", "pisos_above_shop", "pisos_penthouses_max",
    *(f"{PISOS_BUILDING_PREFIX}{b.key}" for b in STREET_BUILDINGS),
    # Bizum (cogs/bizum.py: bizum_stats y bizum_received_stats)
    "bizum_sent_count", "bizum_sent", "bizum_max", "bizum_day_max", "bizum_full", "bizum_min",
    "bizum_broke", "bizum_received_count", "bizum_received",
    # Loterías (cogs/lottery.py: lottery_buy_stats, lottery_prize_stats, scratch_stats)
    "lottery_bets", "lottery_spent", "lottery_prizes", "lottery_won", "lottery_win_max",
    "lottery_reintegros", "lottery_navidad", "lottery_nino", "lottery_pedrea",
    "lottery_gordo_navidad", "lottery_euro_bets", "lottery_lotto4", "lottery_lotto5",
    "lottery_jackpot", "lottery_scratches", "lottery_scratch_top", "lottery_draw_bets_max",
    "lottery_broke_buy", "lottery_gravamen",
    # Loterías 9.25.0: el rasca dibujado, los botes que crecen, los números con guasa,
    # las fechas, la racha de días y comprobar el boleto (las dos últimas, derivadas).
    "lottery_scratch_near", "lottery_jackpot_seen", "lottery_pool_bets_max", "lottery_capicua",
    "lottery_zeros", "lottery_666", "lottery_123456", "lottery_scratch_night",
    "lottery_scratch_friday13", "lottery_xmas_last", "lottery_streak_max", "lottery_check_days",
    # Casino
    "roulette_spins", "roulette_wins", "roulette_straight_wins", "roulette_green_wins",
    "roulette_double_zero_wins", "roulette_color_wins", "roulette_wagers_max",
    "roulette_streak_max", "roulette_repeat_pocket",
    "bj_hands", "bj_wins", "bj_naturals", "bj_double_wins", "bj_splits", "bj_split_sweeps",
    "bj_streak_max",
    "bj_busts", "bj_pushes", "bj_21_multi", "bj_dealer_busts", "bj_dealer_naturals",
    "bj_dealer_bj_saved", "bj_max_stake", "bj_cut_cards", "bj_hot_wins", "bj_spread_max",
    "bj_pit_boss", "bj_cold_shoe", "bj_hot_shoe", "bj_whale_wins", "bj_whale_natural",
    "bj_dev_insurance", "bj_dev_stand_16", "bj_dev_stand_12", "bj_dev_double_ten",
    "bj_dev_split_tens", "bj_insured", "bj_insurance_wasted", "bj_insured_bust",
    "bj_natural_insured", "bj_natural_insured_saved", "bj_natural_greed",
    "bj_bad_beat", "bj_kamikaze", "bj_cards_max",
    "casino_wagered", "casino_win_max", "casino_loss_max", "casino_all_in",
    "casino_all_in_wins", "casino_broke", "casino_bet_666", "casino_bet_42",
    "casino_win_streak_max", "casino_loss_streak_max", "tax_refunds",
    # Tragaperras (cogs/slots.py: slots_stats, slots_autoplay_stats y el botón de Ráfaga)
    "slots_spins", "slots_wins", "slots_jackpots", "slots_jackpot_max", "slots_win_max",
    *(f"slots_three_{symbol}" for symbol in "CLGBD7"),
    "slots_ldw", "slots_near_miss", "slots_anticipation", "slots_scatter_tease",
    "slots_free_triggers", "slots_free_spins", "slots_free_retriggers", "slots_hot_spins",
    "slots_hot_big",
    "slots_wild_wins", "slots_turbo", "slots_auto", "slots_session_max", "slots_pot_fed",
    "slots_night",
    "slots_autoplay_spins", "slots_autoplay_sessions", "slots_autoplay_full",
    "slots_autoplay_loss_limit", "slots_autoplay_bigwin", "slots_autoplay_jackpot",
    "slots_autoplay_broke", "slots_autoplay_manual", "slots_autoplay_quick_quit",
    "slots_autoplay_exit_ahead", "slots_autoplay_free", "slots_autoplay_even",
    # Revamp: re-giro, doble o nada, bote misterioso, giro diario, celebraciones, calor
    # que se enfría y ticket (slots_stats, slots_respin_stats, slots_double_stats,
    # slots_cooled_stats y slots_ticket_stats)
    "slots_win_big", "slots_win_mega", "slots_win_epic", "slots_tiny_big", "slots_daily_big",
    "slots_mystery_pots", "slots_pot_drought", "slots_pot_quick", "slots_daily",
    "slots_daily_streak",
    "slots_respins", "slots_respin_spent", "slots_respin_price_max", "slots_respin_saved",
    "slots_respin_bailout", "slots_respin_jackpots", "slots_respin_fail_chain",
    "slots_doubles", "slots_double_wins", "slots_double_chain", "slots_double_win_max",
    "slots_double_fives", "slots_double_loss_max", "slots_double_nada",
    "slots_double_heartbreak",
    # La tragaperras pintada (7.19): apagón camino del bote, frenazo que se pasa de la
    # línea y la carta del doble o nada (sus palos, en `with_derived`, y sus rachas).
    "slots_jackpot_tease", "slots_slipped", "slots_card_run", "slots_heart_loss",
    "slots_spade_five", "slots_card_suits",
    "slots_cooled", "slots_cooled_max",
    "slots_bonus_fills", "slots_bonus_slowest", "slots_bonus_quick",
    "slots_tickets", "slots_ticket_gross_max", "slots_ticket_best", "slots_ticket_loss_max",
    "slots_ticket_creative", "slots_ticket_taxed", "slots_ticket_zero", "slots_ticket_dry",
    "slots_ticket_quick",
    # Botes (cogs/hold_win.py: hold_win_stats, hold_win_bonus_stats y el botón de Auto)
    "botes_spins", *(f"botes_spins_{theme}" for theme in ("volcan", "mina", "olimpo")),
    "botes_collects", "botes_double_collect", "botes_near_miss", "botes_ways_5",
    "botes_wild_wins", "botes_chips", "botes_turbo", "botes_night", "botes_win_max",
    "botes_bonuses", *(f"botes_bonus_{kind}" for kind in ("green", "blue", "red", "grand")),
    "botes_mini", "botes_major", "botes_grand", "botes_almost_grand", "botes_mult_max",
    "botes_bonus_max", "botes_mystery", "botes_instant", "botes_maximizer", "botes_auto",
    "botes_rescue", "botes_rescue_big", "botes_slipped", "botes_five_wait", "botes_epic",
    "botes_last_save", "botes_chase_hit",
    # Crash (cogs/crash.py: crash_stats) y Minas (cogs/mines.py: mines_stats)
    "crash_rounds", "crash_cashouts", "crash_cashout_max", "crash_win_max", "crash_auto",
    "crash_close", "crash_last_out", "crash_instant", "crash_greedy", "crash_moon",
    "crash_party_max",
    # Pachinko (cogs/pachinko.py: pachinko_stats, pachinko_autoplay_stats y el botón de Ráfaga)
    # ⛏️ Pico (`picar_stats`)
    "picar_digs", "picar_blocks", "picar_minerals", "picar_diamonds", "picar_debris",
    "picar_crystals", "picar_tnt", "picar_chests", "picar_treasures", "picar_crafts",
    "picar_mults", "picar_beacons", "picar_nether", "picar_end", "picar_dragon", "picar_zero",
    "picar_records", "picar_wood_shallow", "picar_gold_shallow", "picar_netherite_no_end",
    "picar_row_42", "picar_row_69", "picar_depth_max",
    "picar_diamonds_run_max", "picar_chain_max", "picar_multiplier_max", "picar_mult_x100_max",
    "picar_win_max", "picar_obsidian", "picar_shulkers", "picar_near_nether", "picar_near_end",
    "picar_near_dragon", "picar_tourist_nether", "picar_tourist_end", "picar_capped",
    "picar_even", "picar_almost", "picar_server_records", "picar_blocks_run_max",
    "picar_chests_run_max", "picar_tnt_run_max", "picar_crafts_run_max", "picar_seconds_max",
    "picar_days_max", "picar_tax_max",
    *(f"picar_row_{row}" for row in (13, 112)),
    "picar_paid_404", "picar_paid_666", "picar_hits_300", "picar_mystery_10", "picar_ldw",
    "picar_mults_run_max",
    *(f"picar_min_{key}" for key in PICAR_MINERALS),
    *(f"picar_mult_{key}" for key in ("gold_block", "diamond_block", "netherite_block",
                                      "mystery")),
    *(f"picar_{kind}_{pick}" for kind in ("dig", "nether", "end", "dragon")
      for pick in ("wood", "stone", "iron", "gold", "diamond", "netherite")),
    "pachinko_volleys", "pachinko_starts", "pachinko_reach", "pachinko_fake_reach",
    "pachinko_atari", "pachinko_rush", "pachinko_super", "pachinko_renchan_max",
    "pachinko_corners", "pachinko_full_hold", "pachinko_wasted", "pachinko_blank",
    "pachinko_win_max", "pachinko_session_max", "pachinko_burst", "pachinko_turbo",
    "pachinko_night", "pachinko_bounces", "pachinko_bounce_max", "pachinko_bounce_volley_max",
    "pachinko_slow_balls", "pachinko_swift_balls", "pachinko_clean_balls",
    "pachinko_hits", "pachinko_hits_max", "pachinko_hit_ball_max", "pachinko_balls_hit_max",
    "pachinko_hit_corner", "pachinko_no_hits", "pachinko_delayed",
    "pachinko_autoplay_volleys", "pachinko_autoplay_sessions", "pachinko_autoplay_full",
    "pachinko_autoplay_loss_limit", "pachinko_autoplay_bigwin", "pachinko_autoplay_super",
    "pachinko_autoplay_broke", "pachinko_autoplay_manual", "pachinko_autoplay_quick_quit",
    "pachinko_autoplay_exit_ahead", "pachinko_autoplay_even", "pachinko_autoplay_fake_reach",
    "pachinko_autoplay_renchan_max",
    *(f"pachinko_board_{key}" for key in ("sakura", "clasica", "dragon", "oni")),
    *(f"pachinko_atari_{key}" for key in ("sakura", "clasica", "dragon", "oni")),
    "mines_games", "mines_gems", "mines_cashouts", "mines_booms", "mines_first_boom",
    "mines_almost", "mines_mult_max", "mines_win_max", "mines_24", "mines_clear",
    "mines_clear_hard", "mines_random", "mines_streak_max",
    # cogs/horses.py (`horses_stats`); los de cada caballo se derivan en `with_derived`
    "horse_bets", "horse_hits", "horse_odds_max", "horse_win_max", "horse_long_shots",
    "horse_photo_wins", "horse_nose_losses", "horse_seconds", "horse_lasts", "horse_bolted",
    "horse_stumble_wins", "horse_comebacks", "horse_manual_comeback", "horse_rain_wins",
    "horse_mud_wins", "horse_tired_wins", "horse_sanxe_hits", "horse_contra_sanxe",
    "horse_via_sanxe", "horse_via_pueblo", "horse_via_azar", "horse_lone_wins",
    "horse_party_max", "horse_gp_bets", "horse_pot_wins", "horse_pot_max", "horse_fav_flops",
    "horse_reversed", "horse_jumbled", "horse_night", "horse_uco_wins", "horse_puerta_long",
    "horse_falcon_bets", "horse_taxed", "horse_paguita_imv", "horse_colchon_night",
    "horse_wepa_gp", "horse_ready", "horse_starter", "horse_flash", HORSE_BACKED_KINDS_STAT,
    HORSE_BACKED_MAX_STAT,
    *(f"horse_kind_{kind}" for kind in HORSE_BET_KINDS),
    *(f"horse_hits_{kind}" for kind in HORSE_BET_KINDS),
    *(f"horse_dist_{d}" for d in HORSE_DISTANCES),
    # cogs/bacara.py (`baccarat_stats`)
    "bac_all_three", "bac_alone_win", "bac_banker_min", "bac_banker_wins", "bac_comebacks",
    "bac_commission", "bac_dragon_ride", "bac_dragon_slay", "bac_friday13", "bac_hands",
    "bac_hedge", "bac_herd_loss", "bac_losses", "bac_lost_by_one", "bac_natural_beaten",
    "bac_natural_tie", "bac_naturals_won", "bac_newyear", "bac_night", "bac_nine_wins",
    "bac_pingpong", "bac_pino", "bac_player_wins", "bac_pushes", "bac_road_longest",
    "bac_shoe_first", "bac_shoe_hands_max", "bac_shoe_last", "bac_six_cards",
    "bac_squeeze_nine", "bac_starter", "bac_table_max", "bac_tie_wins", "bac_tie_zero",
    "bac_win_max", "bac_win_streak_max", "bac_wins",
    # cogs/chicken.py (`chicken_stats`)
    "chicken_games", "chicken_lanes", "chicken_auto_lanes", "chicken_auto_runs",
    "chicken_cashouts", "chicken_mult_max", "chicken_win_max", "chicken_hardcore_cashouts",
    "chicken_gallina", "chicken_close", "chicken_left_on_table", "chicken_road_free",
    "chicken_splats", "chicken_first_splat", "chicken_last_lane_splat", "chicken_lost_big",
    *(f"chicken_finish_{d.key}" for d in CHICKEN_DIFFICULTIES),
    *(f"chicken_lanes_max_{d.key}" for d in CHICKEN_DIFFICULTIES),
    *(f"chicken_hit_{kind}" for kind in CHICKEN_VEHICLE_KINDS),
    # cogs/gallos.py (`gallos_stats`, `gallos_bet_stats` y lo social)
    "gallos_fights", "gallos_wins", "gallos_losses", "gallos_kos", "gallos_ko_taken",
    "gallos_fled", "gallos_made_flee", "gallos_points_wins", "gallos_crits", "gallos_dodges",
    "gallos_damage_max", "gallos_flawless", "gallos_close_win", "gallos_blitz",
    "gallos_streak_max", "gallos_days_max", "gallos_temu_loss", "gallos_favourite_loss",
    "gallos_win_max", "gallos_duels", "gallos_duel_wins", "gallos_duel_big",
    "gallos_crowd_max", "gallos_bets", "gallos_bets_won", "gallos_bet_odds_max",
    "gallos_bet_refund", "gallos_challenges", "gallos_rejects", "gallos_rejected",
    "gallos_renamed", "gallos_rake", "gallos_paid_skin_win", "gallos_stake_155",
    "gallos_hispanidad", "gallos_canarias", "gallos_july4", "gallos_china_day",
    "gallos_europe_day", "gallos_africa_day", "gallos_night", "gallos_dawn",
    "gallos_nochevieja", "gallos_friday13",
    # stats, entrenamiento y muertes (`gallos_stats` y `gallos_train_stats`)
    "gallos_trains", "gallos_train_spent", "gallos_train_dawn", "gallos_trains_day_max",
    "gallos_strength_max", "gallos_speed_max", "gallos_endurance_max", "gallos_power_max",
    "gallos_upset", "gallos_deaths", "gallos_kills", "gallos_phoenix",
    "gallos_death_veteran", "gallos_death_debut",
    # cogs/enraya.py (`enraya_stats`, `enraya_bet_stats` y los retos)
    "enraya_games", "enraya_wins", "enraya_losses", "enraya_draws", "enraya_resigns",
    "enraya_timeouts", "enraya_opp_timeouts", "enraya_beat_imposible_second",
    "enraya_lose_facil", "enraya_draw_imposible", "enraya_win_h", "enraya_win_v",
    "enraya_win_d", "enraya_double", "enraya_five", "enraya_quick_win", "enraya_quick_loss",
    "enraya_full_board", "enraya_last_piece_win", "enraya_blocks", "enraya_missed",
    "enraya_missed_loss", "enraya_streak_max", "enraya_days_max", "enraya_duels",
    "enraya_duel_wins", "enraya_friendlies", "enraya_casino_duels", "enraya_duel_big",
    "enraya_win_max", "enraya_crowd_max", "enraya_bets", "enraya_bets_won",
    "enraya_bet_odds_max", "enraya_bet_refund", "enraya_challenges", "enraya_rejects",
    "enraya_rejected", "enraya_rake", "enraya_retos", "enraya_retos_full", "enraya_stake_42",
    "enraya_april4", "enraya_nochevieja", "enraya_canarias", "enraya_night",
    "enraya_friday13",
    *(f"{ENRAYA_BEAT_PREFIX}{lv.key}" for lv in ENRAYA_LEVELS),
    # cogs/backgammon.py (`backgammon_stats`, `backgammon_bet_stats`, el cubo y los retos)
    "backgammon_backgammoned", "backgammon_backgammons", "backgammon_bet_odds_max",
    "backgammon_bet_refund", "backgammon_bets", "backgammon_bets_won", "backgammon_canarias",
    "backgammon_casino_duels", "backgammon_challenges", "backgammon_closed_out",
    "backgammon_comeback", "backgammon_crowd_max", "backgammon_cube_max", "backgammon_days_max",
    "backgammon_double_run", "backgammon_doubles", "backgammon_doubles_offered",
    "backgammon_doubles_taken", "backgammon_drops", "backgammon_duel_big", "backgammon_duel_wins",
    "backgammon_duels", "backgammon_friday13", "backgammon_friendlies", "backgammon_games",
    "backgammon_gammon_imposible", "backgammon_gammoned", "backgammon_gammons",
    "backgammon_hit_taken", "backgammon_hits", "backgammon_last_checker", "backgammon_lose_facil",
    "backgammon_losses", "backgammon_night", "backgammon_nochevieja", "backgammon_opp_drops",
    "backgammon_opp_timeouts", "backgammon_quick_win", "backgammon_rake", "backgammon_rejected",
    "backgammon_rejects", "backgammon_resigns", "backgammon_retos", "backgammon_retos_full",
    "backgammon_reyes", "backgammon_stake_15", "backgammon_streak_max", "backgammon_timeouts",
    "backgammon_untouched", "backgammon_value_max", "backgammon_win_max", "backgammon_wins",
    *(f"{BACKGAMMON_BEAT_PREFIX}{lv.key}" for lv in BACKGAMMON_LEVELS),
    *(f"{GALLOS_BEAT_PREFIX}{r.key}" for r in GALLOS_HOUSE),
    *(f"{GALLOS_TRAINED_BEAT_PREFIX}{r.key}" for r in GALLOS_HOUSE),
    *(f"{GALLOS_SKIN_PREFIX}{k}" for k in ("serie", *GALLOS_FREE_SKINS)),
    # cogs/coin.py (`coin_stats`)
    "coin_games", "coin_flips", "coin_wins", "coin_wins_cara", "coin_wins_cruz",
    "coin_cashouts", "coin_losses", "coin_edges", "coin_streak_max", "coin_cash_mult_max",
    "coin_finish", "coin_win_max", "coin_lost_big", "coin_edge_big", "coin_gallina",
    "coin_first_fail", "coin_next_edge", "coin_loyal_cara", "coin_loyal_cruz", "coin_flipflop",
    "coin_edge_lost", "coin_night", "coin_hispanidad", "coin_nochevieja", "coin_friday13",
    "coin_cash_666", "coin_first_edge",
    # cogs/bus.py (`bus_stats`)
    "bus_games", "bus_hands", "bus_wins", "bus_cashouts", "bus_losses", "bus_complete",
    "bus_turned", "bus_turn_lost", "bus_mult_max", "bus_win_max", "bus_suit_wins",
    "bus_longshots", "bus_contrarian", "bus_sure_fail", "bus_first_fail", "bus_last_fail",
    "bus_gallina", "bus_missed_longshot", "bus_lost_big", "bus_all_red", "bus_trio",
    "bus_cash_666", "bus_cash_69", "bus_min_stake", "bus_night", "bus_rush", "bus_canarias",
    "bus_friday13",
    *(f"{BUS_WIN_PREFIX}{pick.key}" for pick in BusPick),
    # cogs/poker.py (`poker_stats`)
    "poker_games", "poker_calls", "poker_folds", "poker_wins", "poker_no_qualify",
    "poker_ties", "poker_losses", "poker_air_call_wins", "poker_bluff", "poker_wheel_win",
    "poker_72_win", "poker_tie_board", "poker_bad_beat", "poker_dealer_monster",
    "poker_kicker", "poker_aces", "poker_aces_lost", "poker_flop_monster", "poker_min_stake",
    "poker_win_max", "poker_cash_666", "poker_cash_69", "poker_fold_would_win",
    "poker_fold_saved", "poker_fold_monster", "poker_night", "poker_reyes", "poker_inocentes",
    "poker_friday13",
    # cogs/timba.py (`timba_stats`)
    "timba_hands", "timba_pots_won", "timba_showdowns_won", "timba_steals", "timba_raises",
    "timba_allins", "timba_allin_wins", "timba_busted", "timba_knockouts", "timba_pot_max",
    "timba_splits", "timba_side_pots", "timba_heads_up_wins", "timba_full_table",
    "timba_bad_beat", "timba_cooler", "timba_aces", "timba_aces_cracked", "timba_72_win",
    "timba_preflop_folds", "timba_timeouts", "timba_rake", "timba_night", "timba_friday13",
    "timba_micro", "timba_pot_666",
    *(f"timba_won_{rank.key}" for rank in PokerRank if rank.order >= PokerRank.STRAIGHT.order),
    # cogs/poker.py (`poker_club_stats`)
    "poker_rank", "poker_streak", "poker_retos", "poker_retos_full", "poker_bonus_bets",
    "poker_bonus_wins", "poker_pot_sf", "poker_pot_royal", "poker_pot_max", "poker_table_hands",
    "poker_table_full", "poker_lone_win",
    *(f"{POKER_MADE_PREFIX}{rank.key}" for rank in PokerRank if rank is not PokerRank.HIGH_CARD),
    # cogs/craps.py (`craps_stats`)
    "dice_games", "dice_rolls", "dice_pass_games", "dice_dont_games", "dice_naturals",
    "dice_craps_rolls", "dice_elevens", "dice_points_set", "dice_points_made", "dice_hard",
    "dice_seven_outs", "dice_seven_first", "dice_odds_games", "dice_odds_full", "dice_wins",
    "dice_pass_wins", "dice_dont_wins", "dice_dont_seven", "dice_odds_full_won", "dice_profit",
    "dice_cash_777", "dice_win_max", "dice_losses", "dice_long_lost", "dice_odds_full_lost",
    "dice_boxcars_lost", "dice_dont_bar", "dice_night", "dice_canarias", "dice_nochevieja",
    "dice_inocentes", "dice_friday13", "dice_snake_eyes", "dice_boxcars",
    "dice_game_rolls_max", "dice_hand_points_max", "dice_hand_rolls_max", "dice_fire_max",
    "dice_repeat_max",
    # Mesa compartida (`dados mesa`).
    "dice_table_rolls", "dice_shooter", "dice_public_points_max", "dice_table_alone",
    "dice_table_shooter_wins", "dice_table_vs_shooter", "dice_table_players_max",
    "dice_table_lonely", "dice_table_boxcars", "dice_table_wipeout", "dice_table_unanimous",
    *(f"dice_total_{t}" for t in range(2, 13)),
    *(f"dice_point_made_{p}" for p in (4, 5, 6, 8, 9, 10)),
    *(f"dice_hard_{p}" for p in (4, 6, 8, 10)),
    # Trabajo (cogs/work.py: work_stats, el panel y los eventos; cogs/casino.py: el IMV)
    "work_shifts", "work_shifts_day_max", "work_streak_max", "work_perfect", "work_good",
    "work_night", "work_sunday", "work_birthday", "work_christmas", "work_reyes",
    "work_mayday", "work_black", "work_past_limit", "work_caught_inspeccion", "work_zombie",
    "work_accidents", "work_family_zero", "work_happy_shifts", "work_gifts", "work_gift_finde_sur",
    "work_favors", "work_favor_bizum", "work_favor_excusa", "work_favor_cubre", "work_favor_tupper",
    "work_favor_aval",
    "work_fee", "work_pipes", "work_slackers",
    "work_overruns", "work_perfect_orders", "work_perfect_votes", "work_happy_clients",
    "work_dodged", "work_no_recuerdo", "work_payslips", "work_taxes", "work_irpf_pct_max",
    "work_half_salary", "work_max_base", "work_partner",
    "work_job_changes", "work_coffees_day_max", "work_promotions", "work_declined",
    "work_late", "work_late_black", "work_black_last_franja", "work_caught_late",
    "work_late_crisis", "work_coffees",
    "work_jobs_top", "work_top_obra", "work_top_hosteleria", "work_top_politica",
    "work_demoted", "imv_with_salary", "imv_floor",
    *(outcome.stat for event in (*EVENTS, RESIGN_EVENT) for _label, outcome in event.options
      if outcome.stat),
    "work_caught_uco", "work_pardoned", "work_caught_expediente", "work_caught_hacienda",
    "work_guards", "work_zombie_guard", "work_off_duty_tries", "work_missed_guards",
    "work_stretcher", "work_rounds", "work_triage", "work_mir", "work_google",
    "work_coffee_orders", "work_bugs", "work_reviews", "work_meetings", "work_pitches",
    "work_remote", "work_office", "work_options_max", "work_exit", "work_bankrupt",
    "work_top_sanidad", "work_top_oficina", "work_abroad", "work_hk_shifts",
    "work_nonresident", "work_7p", "work_double_tax", "work_hk_tax", "work_jetlag",
    "imv_abroad", "work_return", "work_beckham", "work_abroad_days_max", "work_jobs_tried",
    "work_zero", "work_black_total",
    "work_tools_owned", "work_saves", "work_insured", "work_fifty", "work_fast",
    "work_last_second", "work_first_miss", "work_stale_clicks", "work_tremor_perfect",
    "work_memory_flawless", "work_posters", "work_clean_digs", "work_board",
    # Oficios nuevos: reparto, funcionariado e influencer (work_stats y eventos)
    "work_orders", "work_routes", "work_fleet", "work_founder", "work_rain", "work_calima",
    "work_rain_fall", "work_app_blocked", "work_five_stars", "work_flat_fee",
    "work_top_reparto", "work_study", "work_temario", "work_ventanilla", "work_registro",
    "work_stamps", "work_citizens", "work_frauds", "work_triennia_max", "work_caught_defensor",
    "work_top_funcionariado", "work_dances", "work_metrics", "work_stream", "work_collabs",
    "work_tv", "work_viral", "work_megaviral", "work_flop", "work_followers_max",
    "work_caught_consumo", "work_caught_regalos", "work_top_influencer",
    # Carrera larga (ramas, jubilación, pensión y deseo del día) y las ramas de la política
    "work_wishes", "work_wish_streak_max", "work_wish_perfecto", "work_wish_nocturno",
    *(f"work_wish_{wish.key}" for wish in WISHES),
    "work_branch_chosen", "work_retired", "work_pensions", "work_pension_irpf",
    "work_jobs_done", "work_veteran_max", "work_pension_pct", "work_pension_active",
    "work_pension_whole", "work_retired_low_quality",
    "work_top_politica_moncloa", "work_top_politica_bruselas", "work_retired_politica",
    "work_spokes", "work_decrees", "work_ministry", "work_partners", "work_control",
    "work_plenary", "work_amendments", "work_lobbies", "work_trilogues", "work_commission",
    "work_censure_survived", "work_snap_election", "work_partner_deal", "work_partner_refused",
    "work_cis", "work_omnibus", "work_suitcase", "work_suitcase_refused", "work_caught_olaf",
    "work_strasbourg", "work_per_diem", "work_lobby_dinner", "work_constitution",
    # Las ramas de la obra: Ladrillo y Obra pública
    "work_top_obra_ladrillo", "work_top_obra_publica", "work_retired_obra", "work_flats",
    "work_land", "work_bank", "work_mayor", "work_box", "work_tenders", "work_concessions",
    "work_ministry_deals", "work_abroad_deals", "work_caught_sepblac",
    # Las ramas de la sanidad: Sanidad pública y Privada
    "work_top_sanidad_publica", "work_top_sanidad_privada", "work_retired_sanidad",
    "work_surgeries", "work_referrals", "work_medical_director", "work_hospital_manager",
    "work_health_minister", "work_fine_print", "work_touchups", "work_clinic",
    "work_health_deals", "work_insurer", "work_guard_nye", "work_caught_incompatibilidades",
    "work_caught_abusivas",
    # La hostelería: el tronco y las ramas Hotelera y Alta cocina
    "work_top_hosteleria_hotel", "work_top_hosteleria_estrella", "work_retired_hosteleria",
    "work_wristbands", "work_complaints", "work_gatecrashers", "work_hotel_chain",
    "work_employers", "work_plating", "work_guide_inspector", "work_tv_judge", "work_brigade",
    "work_dubai", "work_four_hours", "work_full_contract", "work_chorizo_paella",
    "work_extra_tables", "work_caught_terraza", "work_review_bribe", "work_one_star",
    "work_free_dinner", "work_cruise_menu", "work_cruise_closed", "work_heatwave_cook",
    "work_waiters_paid", "work_no_waiters", "work_balconing_sealed", "work_overbook_across",
    "work_overbook_two_star", "work_tourist_tax_passed", "work_costas_demolish",
    "work_costas_appeal", "work_caught_costas", "work_kellys_outsourced", "work_kellys_hired",
    "work_falcon_guest", "work_lobby_envelope", "work_lobby_clean", "work_limit_banner",
    "work_stagier_free", "work_stagier_paid", "work_guide_pampered", "work_allergic_menu",
    "work_onion_with", "work_onion_without", "work_tv_crew", "work_tv_kicked", "work_gold_all",
    "work_gold_dessert", "work_foam_sold", "work_health_hidden",
    "work_caught_sanidad_alimentaria", "work_health_open",
    # El reparto: el tronco y las ramas Correos e Imperio logístico
    "work_top_reparto_correos", "work_top_reparto_logistica", "work_retired_reparto",
    "work_parcels", "work_rural_route", "work_lost_parcel", "work_correos_board",
    "work_correos_chair", "work_dark_store", "work_rider_law", "work_last_mile",
    "work_labour_inspector", "work_rocket", "work_caught_junta_electoral",
    # El funcionariado: las ramas Alta Administración y Puerta giratoria
    "work_top_funcionariado_alta", "work_top_funcionariado_giratoria",
    "work_retired_funcionariado", "work_boe", "work_circuit", "work_colada",
    "work_undersecretaries", "work_state_council", "work_wealth", "work_big_four",
    "work_sandwich", "work_mariachis", "work_andorra_desk", "work_caught_tribunal_cuentas",
    "work_caught_fiscalia",
    # El influencer: las ramas Tele y Andorra
    "work_top_influencer_tele", "work_top_influencer_andorra", "work_retired_influencer",
    "work_tertulia", "work_escaleta", "work_guests", "work_share", "work_rtve",
    "work_subathon", "work_brand", "work_boxing", "work_rug_spotted", "work_party",
    "work_caught_ordenacion_juego", "work_caught_campana", "work_caught_honor",
    "work_caught_etiquetado", "work_caught_cnmv", "work_caught_suplicatorio",
    # La oficina: las ramas Consultora y Unicornio
    "work_top_oficina_consultora", "work_top_oficina_unicornio", "work_retired_oficina",
    "work_hours_billed", "work_tender_files", "work_public_app_bugs",
    "work_partner_committee", "work_managing_partner", "work_pitch_founder", "work_pivots",
    "work_runway_burn",
    "work_funding_rounds", "work_social_network",
    "work_small_change_done", "work_small_change_quoted", "work_postit_told",
    "work_postit_noted", "work_ai_blame_owned", "work_ai_blamed", "work_hackathon_went",
    "work_hackathon_skipped", "work_free_test_used", "work_free_test_paid",
    "work_alignment_active", "work_alignment_camera_off",
    "work_ministry_hours_billed", "work_ministry_hours_honest", "work_sepe_server_blamed",
    "work_sepe_budget_asked", "work_junior_as_senior", "work_junior_honest",
    "work_tailored_tender", "work_open_tender", "work_ex_minister_hired",
    "work_ex_minister_waited", "work_report_on_report", "work_report_on_report_refused",
    "work_layoff_call", "work_layoff_face", "work_inflated_valuation",
    "work_realistic_valuation", "work_nephew_cto", "work_nephew_refused", "work_ibiza_party",
    "work_ibiza_skipped", "work_buy_network", "work_delete_tweet", "work_delaware",
    "work_stay_in_spain",
    # cogs/fun.py: `hongkong`
    "hk_clock", "hk_clock_tomorrow", "hk_clock_sleeping", "hk_clock_lunch", "hk_clock_tour",
    "hk_clock_new_year",
    # Chat (cogs/achievements.py: message_delta, ChatTracker, laugh_reply_stats, ediciones)
    "msg_rae", "msg_exclaim", "msg_everyone", "msg_mass_ping", "msg_spoiler", "msg_code",
    "msg_emoji_heavy", "msg_only_emoji", "msg_stretch", "msg_long_word", "msg_palindrome",
    "msg_nice", "msg_canario", "msg_boricua", "msg_swear", "msg_mild_swear", "msg_thanks",
    "msg_sorry", "msg_good_morning", "msg_good_night", "msg_politics", "msg_falcon",
    "msg_fango", *IRONY_PATTERNS, "msg_23f", "msg_july4", "msg_20n", "msg_paguita", "msg_manual",
    "msg_hacienda", "msg_cuñado", "msg_ola_k_ase",
    "msg_bizum_ask", "msg_siesta", "msg_office", "msg_weekend", "msg_cinderella", "msg_reyes",
    "msg_valentin", "msg_pino", "msg_hispanidad", "msg_aniversario", "msg_inocentes",
    "msg_friday13",
    "msg_monologue_max", "msg_day_max", "msg_first_of_day", "msg_echo", "msg_necro",
    "msg_edits",
    # Risas
    *(f"laugh_{kind}" for kind in LAUGH_KINDS), "msg_laugh_caps", "msg_laugh_dry",
    "laugh_night", "laugh_sanxe", "laugh_len_max", "laugh_kinds_max", "laugh_replies",
    "laughs_caused", "laugh_self", "laugh_at_bot", "laugh_losing", "laugh_chain_max",
    "laugh_reacts_given", "laugh_reacts_received", "laugh_reacts_on_message_max",
    # Voz (collect_voice y on_voice_state_update de cogs/achievements.py)
    "voice_deaf", "voice_afk", "voice_server_muted", "voice_stream_crowd", "voice_multitask",
    "voice_mute_streak_max", "voice_morning", "voice_siesta", "voice_weekend",
    "voice_new_year", "voice_christmas", "voice_duo", "voice_music", "voice_joins",
    "voice_hops", "voice_ghost", "voice_stream_starts",
    # Sonidos de entrada (cogs/entrance.py) y música (cogs/music.py)
    "entrance_saved", "entrance_played", "entrance_volume_max", "entrance_deleted",
    "music_queued", "music_skips", "music_stops", "music_volume_max", "music_whisper",
    "music_track_max", "music_queue_max", "music_clears", "music_removes", "music_jovani",
    "music_despacito", "music_macarena", "music_pedro",
    # Imágenes (cogs/images.py: image_stats) y babel (cogs/fun.py: babel_stats)
    "img_made", "img_magik", "img_video", "img_on_others", "img_self", IMG_EFFECTS_STAT,
    "babel_phrases", "babel_renames", "babel_channels", "babel_full", "babel_lost",
    # `logros` y su ranking, y las virtuales de Coleccionista (meta_stats)
    "logros_views", "logros_others", "logros_ranking",
    # `perfil` (cogs/perfil.py) y su recuento de secciones (with_derived)
    "perfil_views", "perfil_others", "perfil_bot", "perfil_night", "perfil_sections",
    *meta_stats([]).keys(),
    # Segunda tanda: más estadísticas del casino, loterías, lista y derivadas
    "roulette_dozen_wins", "roulette_half_wins", "roulette_pyrrhic", "roulette_cover_max",
    "roulette_zero_sweep", *(f"{ROULETTE_HIT_PREFIX}{n}" for n in range(38)),
    "roulette_storm_max", "roulette_lucky_wins", "roulette_lucky_max", "roulette_lucky_zero",
    "roulette_lucky_missed", "roulette_near_miss", "roulette_hot_bets", "roulette_cold_bets",
    "roulette_hot_hits", "roulette_cold_hits",
    ROULETTE_NUMBERS_STAT, ROULETTE_FAVOURITE_STAT,
    "bj_suited_natural", "bj_triple_seven", "bj_five_21", "bj_double_loss", "bj_stand_low",
    "bj_split_aces", "bj_both_bj", "bj_dealer_five",
    *(f"casino_bet_{n}" for n in SECRET_STAKES),
    "crash_cash_low", "crash_missed_moon",
    "mines_cash_one", "mines_greedy", *(f"mines_level_{n}" for n in range(1, 13)),
    # cogs/allatu.py (`allatu_stats`)
    "allatu_games", "allatu_boxes", "allatu_random", "allatu_no_deals", "allatu_haggles",
    "allatu_haggle_up", "allatu_haggle_hang", "allatu_offer_max", "allatu_gordo_first",
    "allatu_deals", "allatu_first_deal", "allatu_own_gordo", "allatu_deal_gordo",
    "allatu_visionary", "allatu_finals", "allatu_all_no", "allatu_swaps", "allatu_keeps",
    "allatu_swap_good", "allatu_swap_bad", "allatu_swap_gordo_lost", "allatu_final_gordo",
    "allatu_greedy", "allatu_mult_max", "allatu_win_max", "allatu_zero", "allatu_min_stake",
    "allatu_cash_666", "allatu_cash_69", "allatu_cash_777", "allatu_night", "allatu_friday13",
    "allatu_inocentes", "allatu_canarias", "allatu_gordo_max", "allatu_streak",
    "allatu_condolences", "allatu_condolence_deals", "allatu_condolence_rich",
    "allatu_comebacks", "allatu_comeback_max",
    *(f"allatu_games_{t.key}" for t in TABLES),
    # cogs/tower.py (`tower_stats`)
    "tower_games", "tower_floors", "tower_random", "tower_cashouts", "tower_mult_max",
    "tower_win_max", "tower_cash_one", "tower_cash_one_maestro", "tower_cash_eight",
    "tower_crowns", "tower_dice_crown", "tower_cash_777", "tower_cash_666", "tower_cash_69",
    "tower_burns", "tower_first_burn", "tower_easy_first_burn", "tower_ninth_burn",
    "tower_ninth_burn_hard", "tower_burn_late", "tower_greedy", "tower_floor_max",
    *(f"tower_games_{d.key}" for d in TOWER_DIFFICULTIES),
    *(f"tower_crown_{d.key}" for d in TOWER_DIFFICULTIES),
    *(f"tower_floors_max_{d.key}" for d in TOWER_DIFFICULTIES),
    # cogs/war.py (`war_stats`)
    "war_hands", "war_wins", "war_ties", "war_wars", "war_surrenders", "war_war_wins",
    "war_war_losses", "war_war_ties", "war_side_bets", "war_side_hits", "war_triple",
    "war_win_streak_max", "war_lose_streak_max", "war_tie_streak_max", "war_win_max",
    "war_max_stake", "war_ace_ace", "war_king_lost", "war_tie_two", "war_win_two",
    "war_surrender_ace", "war_surrender_one", "war_cash_777", "war_cash_666", "war_cash_69",
    *(f"war_win_rank_{n}" for n in range(3, 15)),
    *(f"war_tie_rank_{n}" for n in range(2, 15)),
    *(f"chicken_games_{d.key}" for d in CHICKEN_DIFFICULTIES),
    *(f"lottery_game_{g.key}" for g in LOTTERY_GAMES),
    "todo_done_batch_max",
    # patrimonio, fortunas y hacienda (cogs/patrimonio.py y cogs/casino.py)
    "patrimonio_views", "patrimonio_snoop", "fortunas_views", "net_worth_max",
    "fortunas_first", "fortunas_last", "patrimonio_illiquid", "hacienda_views",
    "fortunas_fourth", "fortunas_pages", "fortunas_last_page", "fortunas_me", "fortunas_far",
    "fortunas_borrowed",
    "hacienda_self", "hacienda_snoop", "hacienda_pillar", "hacienda_hidden",
    # Las pestañas de `hacienda` (`hacienda_page_stats`).
    "hacienda_business", "hacienda_business_top", "hacienda_landlord_top", "hacienda_all_tabs",
    # Estadísticas del casino (cogs/apuestas.py: apuestas_stats)
    "apuestas_views", "apuestas_snoop", "apuestas_insomnia", "apuestas_virgin",
    "apuestas_denial", "apuestas_even", "apuestas_ruin", "apuestas_rich",
    *(f"apuestas_page_{page}" for page in APUESTAS_PAGES),
    *(f"apuestas_period_{period}" for period in APUESTAS_PERIODS),
    # Cualquier jugada del casino (cogs/apuestas.py: casino_record_stats)
    "casino_plays", *CASINO_DATES.values(),
    *(
        f"{prefix}{game}"
        for prefix in ("casino_game_", "casino_night_", "casino_friday13_",
                       "casino_canarias_", "casino_nochevieja_")
        for game in CASINO_GAMES
    ),
    CASINO_GAMES_STAT, CASINO_GAMES_100_STAT, CASINO_GAMES_1000_STAT, CASINO_NIGHT_GAMES_STAT,
    # Chat y voz, segunda tanda (message_delta, ChatTracker, reaction_stats y voz)
    *LINK_SITES, *ATTACH_KINDS, *FUNNY_TOPICS, LINK_KINDS_STAT, ATTACH_KINDS_STAT,
    TOPIC_KINDS_STAT, "msg_album", "msg_screenshot", "msg_image_spoiler", "msg_len_69",
    "msg_len_420", "msg_len_1", "msg_only_digits", "msg_only_question", "msg_dots",
    "msg_no_vowels", "msg_1111", "msg_2222", "msg_420", "msg_pi", "msg_2359", "msg_in_voice",
    "msg_voice_text", "msg_while_streaming", "msg_on_camera", "laugh_in_voice",
    "msg_rally_max", "msg_quick", "msg_silence_break", "reactions_first", "reactions_custom",
    *(f"{prefix}{key}" for prefix in (REACT_PREFIX, REACTED_PREFIX)
      for key in REACTION_EMOJIS.values()),
    REACT_KINDS_STAT, "voice_midnight", "voice_leet", "voice_420", "voice_1111", "voice_gaming",
    "voice_spotify", *(f"{VOICE_GAME_PREFIX}{key}" for key in VOICE_GAMES), VOICE_GAMES_STAT,
    "voice_host", "voice_last_out", VOICE_STREAK_STAT, VOICE_PEOPLE_STAT, VOICE_BESTIE_STAT,
    # Porras (cogs/porras.py; test_porras.py comprueba que cada una sale de verdad)
    *PORRA_STATS,
    *(f"{PORRA_GAME_PREFIX}{game}" for game in PORRA_GAMES),
    *(f"{PORRA_PROP_PREFIX}{prop}" for prop in PORRA_PROPS),
    # Apuestas deportivas (`sports_*_stats`; las pruebas del final comprueban cada una)
    *(f"{SPORTS_LEAGUE_PREFIX}{league}" for league in SPORTS_LEAGUES),
    *(f"{SPORTS_PICK_PREFIX}{pick}" for pick in SPORTS_PICKS),
    *(f"{SPORTS_MARKET_PREFIX}{market}" for market in SPORTS_MARKETS),
    "sports_match_markets_max", "sports_win_exacto", "sports_win_otro", "sports_win_over35",
    "sports_win_under15", "sports_win_goles", "sports_win_ambos", "sports_lost_on_line",
    "sports_exact_near", "sports_lost_ambos_rout",
    "sports_bets", "sports_staked", "sports_bet_long", "sports_bet_safe", "sports_all_in",
    "sports_bet_min", "sports_bet_cap", "sports_bet_666", "sports_bet_69", "sports_last_minute",
    "sports_early", "sports_night", "sports_nochevieja", "sports_canarias", "sports_friday13",
    "sports_stake_max", "sports_odds_max", "sports_settled", "sports_wins", "sports_losses",
    "sports_win_max", "sports_odds_won_max", "sports_win_streak_max", "sports_win_draw",
    "sports_win_00", "sports_win_rout", "sports_win_by_one", "sports_lost_safe",
    "sports_lost_tied", "sports_lost_rout", "sports_lost_by_one", "sports_lost_goals",
    "sports_refunds", "sports_slips",
    # 🎸 Robuso (cogs/robuso.py y los ecos de services/robuso.py; test_robuso.py las suma)
    *ROBUSO_ECHOES.values(), *ROBUSO_DAILY, f"{VOICE_WITH_PREFIX}{ROBUSO_ID}",
    "robuso_hk_new_year", "robuso_silence_max", "robuso_voice_gap_max", "robuso_hk_vc_minutes",
    "robuso_trips", "robuso_returns", "robuso_trip_days_max", "robuso_replies", "robuso_laughs",
    "robuso_reacts", "robuso_reunions", "robuso_welcomes", "robuso_missed", "robuso_envy",
    UNLOCKED_STAT,
}  # fmt: skip


# -- Catálogo --------------------------------------------------------------------------


def test_el_catalogo_es_grande_y_los_ids_no_se_repiten() -> None:
    assert len(CATALOG) >= 200
    assert len(BY_ID) == len(CATALOG)


def test_todos_los_logros_disponibles_usan_estadisticas_que_alguien_suma() -> None:
    missing = {stat for a in AVAILABLE for stat, _goal in a.conditions} - PRODUCED_STATS
    assert missing == set()


def test_cada_pagina_de_cada_categoria_cabe_en_un_embed() -> None:
    profile = Profile(stats={}, unlocked={})
    for category in CATEGORIES:
        for page in range(category_page_count(category, profile, {}, 10)):
            embed = category_embed(category, "Diego", profile, {}, 10, page)
            assert embed.description is not None
            assert len(embed.description) <= 4096
            assert len(embed) <= 6000


def test_el_menu_de_logros_cabe_en_un_desplegable_de_discord() -> None:
    # 25 opciones como mucho, y una es el Resumen. Con las secretas a la vista.
    assert len(menu_entries(BY_ID)) <= 24
    keys = [key for key, _title in menu_entries(BY_ID)]
    assert len(keys) == len(set(keys))


def test_todos_los_juegos_del_casino_van_en_una_sola_entrada_del_menu() -> None:
    sections = {c.key for c in group_sections(CASINO_GROUP.key)}
    assert {
        "casino",
        "roulette",
        "blackjack",
        "slots",
        "botes",
        "crash",
        "mines",
        "chicken",
        "pachinko",
    } <= sections
    keys = {key for key, _title in menu_entries()}
    assert CASINO_GROUP.key in keys
    assert not keys & sections
    # Un segundo desplegable lista las secciones (más la portada); si no caben, un tercero
    # con el resto (`SectionSelect`, `part=1`). Más de dos no caben en la vista.
    assert len(sections) + 1 <= 2 * 25 - 1


def test_la_portada_del_casino_cabe_en_un_embed() -> None:
    embed = group_embed(CASINO_GROUP.key, "Diego", Profile(stats={}, unlocked={}))
    assert embed.description is not None
    assert len(embed.description) <= 4096
    assert "🐔 Pollo" in embed.description


def test_completista_pide_todos_los_logros_normales() -> None:
    # Los de las categorías secretas (🎸 Robuso) van aparte: casi nadie puede tenerlos todos.
    normal = [a for a in COUNTED if a.category != "meta"]
    assert BY_ID["completionist"].goal == len(normal)


def test_las_metas_de_cada_estadistica_van_de_menor_a_mayor() -> None:
    by_stat: dict[str, list[int]] = {}
    for a in CATALOG:
        if len(a.conditions) == 1:
            by_stat.setdefault(a.stat, []).append(a.goal)
    for goals in by_stat.values():
        assert goals == sorted(goals)


# -- Reglas ----------------------------------------------------------------------------


def test_un_logro_salta_al_llegar_a_la_meta_y_no_antes() -> None:
    assert "chat_100" not in newly_unlocked({"messages": 99}, [])
    assert "chat_100" in newly_unlocked({"messages": 100}, [])


def test_los_mensajes_importados_cuentan_para_los_logros_de_chat() -> None:
    new = newly_unlocked({"messages": 10, "messages_imported": 995}, [])
    assert "chat_1k" in new
    assert "chat_5k" not in new


def test_lo_ya_desbloqueado_no_se_repite() -> None:
    assert newly_unlocked({"messages": 5}, ["chat_1"]) == []


def test_coleccionista_cuenta_los_logros_que_saltan_a_la_vez() -> None:
    nine = [a.id for a in AVAILABLE if a.category != "meta" and a.id != "chat_1"][:9]
    new = newly_unlocked({"messages": 1}, nine)
    assert "chat_1" in new
    assert "meta_10" in new


def test_un_logro_combinado_necesita_todas_sus_condiciones() -> None:
    assert "versatile" not in newly_unlocked({"roulette_spins": 3}, [])
    assert "versatile" in newly_unlocked({"roulette_spins": 3, "bj_hands": 1}, [])


def test_la_tragaperras_ya_desbloquea_sus_logros() -> None:
    """Era la categoría "próximamente"; desde que existe la máquina, cuenta."""
    assert "slots_1" in newly_unlocked({"slots_spins": 50}, [])


def test_el_progreso_no_pasa_de_la_meta() -> None:
    assert progress(BY_ID["chat_100"], {"messages": 250}) == (100, 100)


def test_el_premio_depende_de_la_rareza() -> None:
    assert total_reward(["chat_1", "chat_10k"]) == (
        BY_ID["chat_1"].rarity.reward + BY_ID["chat_10k"].rarity.reward
    )


# -- Qué cuenta cada cosa --------------------------------------------------------------


def at(hour: int, minute: int = 0, *, month: int = 3, day: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TIMEZONE)


def test_mensaje_de_madrugada_largo_con_enlace() -> None:
    text = "mira https://example.com " + "a" * 600
    stats = message_stats(text, when=at(3))
    assert stats["messages"] == 1
    assert stats["msg_night"] == 1
    assert stats["msg_long"] == 1
    assert stats["msg_links"] == 1
    assert "msg_morning" not in stats


def test_gritos_preguntas_y_mensajes_cortos() -> None:
    assert message_stats("NO ME LO PUEDO CREER", when=at(12))["msg_caps"] == 1
    assert "msg_caps" not in message_stats("OK", when=at(12))
    assert message_stats("¿vienes?", when=at(12))["msg_questions"] == 1
    assert message_stats("k", when=at(12))["msg_short"] == 1


@pytest.mark.parametrize("text", ["jajaja", "JAJAJ", "jsjsjs", "jejeje", "lol que bueno"])
def test_risas_que_cuentan(text: str) -> None:
    assert is_laugh(text)


@pytest.mark.parametrize("text", ["ja", "jamón", "jeans", "hola"])
def test_palabras_que_no_son_risas(text: str) -> None:
    assert not is_laugh(text)


def test_xd_cuenta_solo_como_palabra() -> None:
    assert message_stats("xddd", when=at(12))["msg_xd"] == 1
    assert "msg_xd" not in message_stats("exdirector", when=at(12))


def test_fechas_y_horas_secretas() -> None:
    assert message_stats("hola", when=at(13, 37))["msg_leet"] == 1
    assert message_stats("hola", when=at(0, 5, month=1, day=1))["msg_new_year"] == 1
    assert message_stats("hola", when=at(18, month=10, day=31))["msg_halloween"] == 1
    assert message_stats("hola", when=at(18, month=5, day=30))["msg_canarias"] == 1
    assert message_stats("hola", when=at(18), own_birthday=True)["msg_own_birthday"] == 1
    assert message_stats("ese Perro Sanxe", when=at(18))["msg_sanxe"] == 1
    assert message_stats("oye jovani", when=at(18))["msg_bot_call"] == 1


def test_casino_detecta_all_in_ruina_y_cifras_secretas() -> None:
    lost = casino_stats(stake=666, net=-666, balance_after=0)
    assert lost.add["casino_all_in"] == 1
    assert lost.add["casino_broke"] == 1
    assert lost.add["casino_bet_666"] == 1
    assert lost.peak["casino_loss_max"] == 666
    assert "casino_all_in_wins" not in lost.add

    won = casino_stats(stake=500, net=500, balance_after=1_000, tax_delta=40)
    assert won.add["casino_all_in_wins"] == 1
    assert won.peak["casino_win_max"] == 500
    assert won.add["tax_paid"] == 40

    partial = casino_stats(stake=100, net=-100, balance_after=900, tax_delta=-20)
    assert "casino_all_in" not in partial.add
    assert partial.add["tax_refunds"] == 1

    franco = casino_stats(stake=1975, net=-1975, balance_after=5_000)
    assert franco.add["casino_bet_1975"] == 1
    assert "casino_bet_1975" not in casino_stats(stake=1976, net=0, balance_after=5_000).add


def outcome(pocket: int, *bets: tuple[str, int]) -> RoundOutcome:
    wagers = tuple(
        Wager(OUTSIDE_BETS[key] if key in OUTSIDE_BETS else parse_bet(key), stake)
        for key, stake in bets
    )
    returns = tuple(w.bet.total_return(w.stake, pocket) for w in wagers)
    return RoundOutcome(pocket=pocket, wagers=wagers, returns=returns)


def test_ruleta_pleno_color_y_deja_vu() -> None:
    round_ = outcome(17, ("17", 10), ("black", 10), ("red", 10))
    delta = roulette_stats(round_, table_streak=2, previous_pocket=17)
    assert delta.add["roulette_spins"] == 1
    assert delta.add["roulette_wins"] == 1
    assert delta.add["roulette_straight_wins"] == 1
    assert delta.add["roulette_color_wins"] == 1
    assert delta.add["roulette_repeat_pocket"] == 1
    assert delta.peak["roulette_wagers_max"] == 3
    assert delta.peak["roulette_streak_max"] == 2


def test_ruleta_doble_cero() -> None:
    delta = roulette_stats(outcome(DOUBLE_ZERO, ("00", 10)), table_streak=1, previous_pocket=None)
    assert delta.add["roulette_green_wins"] == 1
    assert delta.add["roulette_double_zero_wins"] == 1


def card(rank: int) -> Card:
    return Card(rank, 0)


def settled_game(hands: list[list[int]], dealer: list[int], **flags: bool) -> BlackjackGame:
    game = BlackjackGame(stake=100, shoe=[])
    game.hands = [Hand([card(r) for r in ranks], 100, done=True, **flags) for ranks in hands]
    game.dealer = [card(r) for r in dealer]
    game.hole_revealed = True
    game.settle()
    return game


def test_blackjack_natural() -> None:
    delta = blackjack_stats(settled_game([[1, 13]], [9, 8]))
    assert delta.add["bj_hands"] == 1
    assert delta.add["bj_naturals"] == 1
    assert delta.add["bj_wins"] == 1


def test_blackjack_racha_de_la_mesa_cuenta_como_maximo() -> None:
    game = settled_game([[1, 13]], [9, 8])
    assert blackjack_stats(game, streak=4).peak["bj_streak_max"] == 4
    assert "bj_streak_max" not in blackjack_stats(game).peak


def test_blackjack_de_la_banca_sin_seguro_se_pierde_y_no_cuenta_como_rescate() -> None:
    lost = blackjack_stats(settled_game([[10, 9]], [1, 13]))
    assert lost.add["bj_dealer_naturals"] == 1
    assert "bj_dealer_bj_saved" not in lost.add
    assert "bj_pushes" not in lost.add
    both = blackjack_stats(settled_game([[1, 12]], [1, 13]))
    assert both.add["bj_pushes"] == 1


def insured_game(hand: list[int], dealer: list[int]) -> BlackjackGame:
    game = BlackjackGame(stake=100, shoe=[])
    game.hands = [Hand([card(r) for r in hand], 100, done=True)]
    game.dealer = [card(r) for r in dealer]
    game.insurance = 50
    game.hole_revealed = True
    game.settle()
    return game


def test_blackjack_seguro_cobrado_es_el_rescate() -> None:
    delta = blackjack_stats(insured_game([10, 9], [1, 13]))
    assert delta.add["bj_insured"] == 1
    assert delta.add["bj_dealer_bj_saved"] == 1
    assert "bj_insurance_wasted" not in delta.add


def test_blackjack_seguro_perdido_y_siniestro_total() -> None:
    wasted = blackjack_stats(insured_game([10, 9], [1, 7]))
    assert wasted.add["bj_insurance_wasted"] == 1
    assert "bj_insured_bust" not in wasted.add
    bust = blackjack_stats(insured_game([10, 6, 9], [1, 7]))
    assert bust.add["bj_insured_bust"] == 1


def test_blackjack_propio_asegurado_y_rechazado() -> None:
    natural, ace_ten = [1, 13], [1, 13]
    saved = blackjack_stats(insured_game(natural, ace_ten))
    assert saved.add["bj_natural_insured"] == 1
    assert saved.add["bj_natural_insured_saved"] == 1
    lost = blackjack_stats(insured_game(natural, [1, 7]))
    assert lost.add["bj_natural_insured"] == 1
    assert "bj_natural_insured_saved" not in lost.add
    declined = settled_game([natural], ace_ten)
    assert blackjack_stats(declined).add["bj_natural_greed"] == 1
    # Con un 10 a la vista no hay seguro que rechazar.
    assert "bj_natural_greed" not in blackjack_stats(settled_game([natural], [13, 1])).add


def test_blackjack_mano_de_5000_o_mas() -> None:
    game = settled_game([[10, 9]], [10, 8])
    assert "bj_max_stake" not in blackjack_stats(game).add
    game.stake = BJ_HIGH_STAKE
    assert blackjack_stats(game).add["bj_max_stake"] == 1


def test_blackjack_kamikaze_con_17_duro() -> None:
    delta = blackjack_stats(settled_game([[10, 7, 3]], [10, 8]))
    assert delta.add["bj_kamikaze"] == 1
    assert "bj_kamikaze" not in blackjack_stats(settled_game([[10, 6, 4]], [10, 8])).add


def test_blackjack_cinco_cartas_y_banca_pasada() -> None:
    delta = blackjack_stats(settled_game([[2, 3, 2, 4, 5]], [10, 6, 10]))
    assert delta.peak["bj_cards_max"] == 5
    assert delta.add["bj_dealer_busts"] == 1


def test_blackjack_por_los_pelos() -> None:
    delta = blackjack_stats(settled_game([[10, 10]], [10, 5, 6]))
    assert delta.add["bj_bad_beat"] == 1
    assert "bj_wins" not in delta.add


def test_blackjack_separar_y_ganar_las_dos() -> None:
    delta = blackjack_stats(settled_game([[10, 9], [10, 8]], [10, 7], from_split=True))
    assert delta.add["bj_splits"] == 1
    assert delta.add["bj_split_sweeps"] == 1


def shoe_stats(game: BlackjackGame, count: float, spread: int | None = 2) -> StatDelta:
    return blackjack_shoe_stats(game, caught=False, cut=False, count=count, spread=spread)


def test_blackjack_desvios_de_la_cuenta_jugados_y_ganados() -> None:
    stand_16 = settled_game([[10, 6]], [10, 6, 10])
    assert "bj_dev_stand_16" in shoe_stats(stand_16, 0).add
    assert "bj_dev_stand_16" not in shoe_stats(stand_16, -1).add
    assert "bj_dev_stand_16" not in shoe_stats(settled_game([[1, 5]], [10, 6, 10]), 0).add
    stand_12 = settled_game([[10, 2]], [3, 9, 10])
    assert "bj_dev_stand_12" in shoe_stats(stand_12, 3).add
    assert "bj_dev_stand_12" not in shoe_stats(stand_12, 2).add
    double_ten = settled_game([[6, 4, 10]], [13, 9], doubled=True)
    assert "bj_dev_double_ten" in shoe_stats(double_ten, 4).add
    assert "bj_dev_double_ten" not in shoe_stats(double_ten, 3).add
    split_tens = settled_game([[13, 9], [12, 10]], [6, 10, 10], from_split=True)
    assert "bj_dev_split_tens" in shoe_stats(split_tens, 4).add
    insured = insured_game([10, 9], [1, 13])
    assert "bj_dev_insurance" in shoe_stats(insured, 3).add
    assert "bj_dev_insurance" not in shoe_stats(insured, 2).add
    # Con la cuenta a favor, perder la mano no cuenta.
    assert "bj_dev_stand_16" not in shoe_stats(settled_game([[10, 6]], [10, 8]), 5).add


def test_blackjack_ficha_a_tope_y_zapato_hirviendo() -> None:
    win = settled_game([[10, 9]], [10, 8])
    assert shoe_stats(win, 4, PIT_BOSS_SPREAD).add["bj_whale_wins"] == 1
    assert "bj_whale_wins" not in shoe_stats(win, 3, PIT_BOSS_SPREAD).add
    assert "bj_whale_wins" not in shoe_stats(win, 4, PIT_BOSS_SPREAD - 1).add
    natural = settled_game([[1, 13]], [9, 8])
    assert shoe_stats(natural, 0, PIT_BOSS_SPREAD).add["bj_whale_natural"] == 1
    assert shoe_stats(win, 6, None).add["bj_hot_shoe"] == 1


# -- Repositorio -----------------------------------------------------------------------


async def make_repository(tmp_path: Path) -> AchievementRepository:
    repository = AchievementRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def test_sumas_y_maximos_se_guardan(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    for value in (5, 3):
        await repository.record(
            GUILD,
            {USER: StatDelta(add={"messages": 2}, peak={"level_max": value})},
            newly_unlocked,
            now=1.0,
        )
    profile = await repository.profile(GUILD, USER)
    assert profile.stats["messages"] == 4
    assert profile.stats["level_max"] == 5
    assert set(profile.unlocked) >= {"chat_1", "level_5"}


async def test_un_logro_se_desbloquea_una_sola_vez(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    delta = {USER: StatDelta(add={"messages": 1})}
    first = await repository.record(GUILD, delta, newly_unlocked, now=1.0)
    second = await repository.record(GUILD, delta, newly_unlocked, now=2.0)
    assert first == {USER: ["chat_1"]}
    assert second == {}


async def test_escrituras_simultaneas_no_duplican_logros(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    results = await asyncio.gather(
        *(
            repository.record(GUILD, {USER: StatDelta(add={"messages": 1})}, newly_unlocked, now=1)
            for _ in range(5)
        )
    )
    unlocked = [i for result in results for i in result.get(USER, [])]
    assert unlocked.count("chat_1") == 1
    assert (await repository.profile(GUILD, USER)).stats["messages"] == 5


async def test_borrar_servidor_elimina_sus_logros(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await repository.record(GUILD, {USER: StatDelta(add={"messages": 1})}, newly_unlocked, now=1)
    await repository.delete_guild_data(GUILD)
    assert await repository.profile(GUILD, USER) == Profile(stats={}, unlocked={})
    assert await repository.guild_unlocks(GUILD) == ([], 0)


# -- Cog -------------------------------------------------------------------------------


def make_bot(*guilds: object) -> MagicMock:
    bot = MagicMock()
    bot.guilds = list(guilds)
    by_id = {g.id: g for g in guilds}  # type: ignore[attr-defined]
    bot.get_guild = lambda guild_id: by_id.get(guild_id)
    bot.get_cog = lambda _name: None
    bot.user = SimpleNamespace(id=999)
    return bot


async def make_cog(
    tmp_path: Path, *guilds: object, message_stats: MessageStatsRepository | None = None
) -> tuple[Achievements, AchievementRepository, EconomyService]:
    repository = await make_repository(tmp_path)
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    economy = EconomyService(economy_repository)
    cog = Achievements(make_bot(*guilds), repository, economy=economy, message_stats=message_stats)
    return cog, repository, economy


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


class FakeChannel(discord.abc.Messageable):
    """Canal mínimo que guarda lo enviado."""

    def __init__(self, channel_id: int = 50) -> None:
        self.id = channel_id
        self.send = AsyncMock()  # type: ignore[method-assign]

    async def _get_channel(self) -> FakeChannel:  # pragma: no cover - no se usa
        return self


async def test_desbloquear_paga_con_irpf_y_el_libro_cuadra(tmp_path: Path) -> None:
    cog, _repository, economy = await make_cog(tmp_path)
    channel = FakeChannel()

    ids = await cog.apply(GUILD, USER, StatDelta(add={"messages": 10_000}), channel)

    gross = total_reward(ids)
    balance = await economy.balance(GUILD, USER)
    treasury = await economy.treasury(GUILD, since=0)
    assert "chat_10k" in ids
    assert balance == STARTING_BALANCE + gross - treasury.collected_total
    assert ledger_sum(tmp_path, USER) == balance
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == treasury.balance
    embed = channel.send.await_args.kwargs["embed"]
    assert "logros desbloqueados" in embed.author.name
    # Con la vara única de la 8.0.0 un premio pequeño puede no llegar al mínimo: la línea de
    # la retención sale si y solo si Hacienda se ha llevado algo.
    assert ("🐶 −" in embed.description) == (treasury.collected_total > 0)
    # La retención cuenta para "Contribuyente" y compañía en la siguiente escritura.
    assert cog._pending[GUILD][USER].add["tax_paid"] == treasury.collected_total


async def test_la_primera_retencion_dispara_el_discurso_de_perro_sanxe(tmp_path: Path) -> None:
    """El gancho de la primera retención: un ingreso que supera el mínimo personal
    retiene IRPF, eso suma `tax_paid` y salta `tax_first` con su discurso, una vez."""
    cog, _repository, economy = await make_cog(tmp_path)
    channel = FakeChannel()
    small = await economy.pay_income(GUILD, USER, gross=1_000, concept="nivel:1")
    assert small.tax == 0  # por debajo del mínimo, Perro Sanxe no toca nada

    # Pasar de largo el mínimo personal de la semana (`FIRST_TAX_WEEKLY`) ya retiene.
    big = await economy.pay_income(GUILD, USER, gross=FIRST_TAX_WEEKLY * 2, concept="nivel:10")
    assert big.tax > 0
    ids = await cog.apply(GUILD, USER, StatDelta(add={"tax_paid": big.tax}), channel)

    assert "tax_first" in ids
    description = channel.send.await_args.kwargs["embed"].description or ""
    assert "te encontró" in description
    assert "Bienvenido a España" in description

    channel.send.reset_mock()
    later = await cog.apply(GUILD, USER, StatDelta(add={"tax_paid": 5}), channel)
    assert "tax_first" not in later


def test_la_primera_retencion_no_se_anuncia_sin_pagar() -> None:
    assert "tax_first" not in newly_unlocked({"tax_paid": 0, "balance_max": 1}, [])
    assert BY_ID["tax_first"].secret


async def test_sin_logro_nuevo_no_se_paga_ni_se_avisa(tmp_path: Path) -> None:
    cog, _repository, economy = await make_cog(tmp_path)
    channel = FakeChannel()
    await cog.apply(GUILD, USER, StatDelta(add={"messages": 1}), channel)
    channel.send.reset_mock()
    before = await economy.balance(GUILD, USER)

    assert await cog.apply(GUILD, USER, StatDelta(add={"messages": 1}), channel) == []

    assert await economy.balance(GUILD, USER) == before
    channel.send.assert_not_awaited()


def voice_member(user_id: int, *, bot: bool = False, **flags: bool) -> SimpleNamespace:
    state = {
        "self_mute": False,
        "self_deaf": False,
        "mute": False,
        "deaf": False,
        "self_stream": False,
        "self_video": False,
        **flags,
    }
    return SimpleNamespace(id=user_id, bot=bot, voice=SimpleNamespace(**state))


def voice_guild(*channels: SimpleNamespace, afk: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=GUILD, voice_channels=list(channels), afk_channel=afk, system_channel=None
    )


NOON = datetime(2026, 3, 10, 12, tzinfo=TIMEZONE).timestamp()


async def test_voz_cuenta_minutos_con_gente_y_sesiones_seguidas(tmp_path: Path) -> None:
    channel = SimpleNamespace(
        id=70,
        members=[
            voice_member(1, self_mute=True, self_stream=True),
            voice_member(2),
            voice_member(3, self_deaf=True),
            voice_member(4, bot=True),
        ],
    )
    cog, _repository, _economy = await make_cog(tmp_path, voice_guild(channel))

    cog.collect_voice(NOON)
    cog.collect_voice(NOON + 60)

    first = cog._pending[GUILD][1]
    day = f"voice_day_{datetime.fromtimestamp(NOON, TIMEZONE):%Y%m%d}"
    # Dos escuchando: además de la llamada, cuenta como «cara a cara».
    assert first.add == {
        "voice_minutes": 2,
        "voice_muted": 2,
        "voice_stream": 2,
        "voice_duo": 2,
        day: 2,
        "voice_with_2": 2,
    }
    assert first.peak["voice_session_max"] == 2
    assert first.peak["voice_crowd_max"] == 2
    assert first.peak["voice_mute_streak_max"] == 2
    assert cog._pending[GUILD][2].add == {
        "voice_minutes": 2, "voice_duo": 2, day: 2, "voice_with_1": 2,
    }  # fmt: skip
    # El ensordecido no suma llamada, solo «Estoy pero no estoy».
    assert cog._pending[GUILD][3].add == {"voice_deaf": 2}
    assert 4 not in cog._pending[GUILD]


async def test_salir_de_la_llamada_reinicia_la_sesion(tmp_path: Path) -> None:
    channel = SimpleNamespace(id=70, members=[voice_member(1), voice_member(2)])
    cog, _repository, _economy = await make_cog(tmp_path, voice_guild(channel))
    cog.collect_voice(NOON)
    channel.members = [voice_member(2)]
    cog.collect_voice(NOON + 60)
    channel.members = [voice_member(1), voice_member(2)]

    cog.collect_voice(NOON + 120)

    assert cog._sessions[(GUILD, 1)] == 1
    assert cog._pending[GUILD][2].add["voice_alone"] == 1


async def test_el_canal_afk_no_cuenta_como_llamada_sino_como_afk(tmp_path: Path) -> None:
    afk = SimpleNamespace(id=71, members=[voice_member(1), voice_member(2)])
    cog, _repository, _economy = await make_cog(tmp_path, voice_guild(afk, afk=afk))
    cog.collect_voice(NOON)
    assert {user: delta.add for user, delta in cog._pending[GUILD].items()} == {
        1: {"voice_afk": 1},
        2: {"voice_afk": 1},
    }


def reaction(*, message_id: int = 7, user_id: int = 2, author_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        guild_id=GUILD,
        message_id=message_id,
        user_id=user_id,
        message_author_id=author_id,
        channel_id=50,
        member=SimpleNamespace(id=user_id, bot=False),
    )


async def test_reacciones_una_por_persona_y_mensaje(tmp_path: Path) -> None:
    guild = SimpleNamespace(id=GUILD, get_member=lambda uid: SimpleNamespace(id=uid, bot=False))
    cog, _repository, _economy = await make_cog(tmp_path, guild)

    await cog.on_raw_reaction_add(reaction(user_id=2))  # type: ignore[arg-type]
    await cog.on_raw_reaction_add(reaction(user_id=2))  # type: ignore[arg-type]
    await cog.on_raw_reaction_add(reaction(user_id=3))  # type: ignore[arg-type]
    await cog.on_raw_reaction_add(reaction(user_id=1))  # type: ignore[arg-type]

    author = cog._pending[GUILD][1]
    assert author.add == {"reactions_received": 2}
    assert author.peak["reactions_on_message_max"] == 2
    assert cog._pending[GUILD][2].add == {"reactions_given": 1}


async def test_rachas_de_casino_entre_juegos(tmp_path: Path) -> None:
    cog, repository, _economy = await make_cog(tmp_path)
    for _ in range(5):
        await cog.casino_play(GUILD, USER, None, StatDelta(), net=-10)
    await cog.casino_play(GUILD, USER, None, StatDelta(), net=50)

    profile = await repository.profile(GUILD, USER)
    assert profile.stats["casino_loss_streak_max"] == 5
    assert profile.stats["casino_win_streak_max"] == 1
    assert "lstreak_5" in profile.unlocked


async def test_la_primera_vez_se_recupera_el_historial_y_el_nivel(tmp_path: Path) -> None:
    stats = MessageStatsRepository(tmp_path / "bot.db")
    await stats.initialize()
    assert await stats.start_import(GUILD, [20], cutoff_id=500)
    await stats.save_channel_counts(GUILD, 20, {USER: 1_200})
    await stats.finish_import(GUILD)
    await stats.enable_levels(GUILD, historical_xp_per_message=20)
    cog, repository, _economy = await make_cog(tmp_path, message_stats=stats)

    cog.note(GUILD, USER, StatDelta(add={"messages": 1}), 50)
    await cog.flush()
    cog.note(GUILD, USER, StatDelta(add={"messages": 1}), 50)
    await cog.flush()

    profile = await repository.profile(GUILD, USER)
    assert profile.stats["messages"] == 2
    assert profile.stats["messages_imported"] == 1_200
    assert profile.stats["level_max"] > 0
    assert "chat_1k" in profile.unlocked


async def test_flush_avisa_en_el_canal_del_ultimo_mensaje(tmp_path: Path) -> None:
    channel = FakeChannel(55)
    guild = SimpleNamespace(
        id=GUILD,
        get_channel_or_thread=lambda cid: channel if cid == 55 else None,
        get_member=lambda _uid: None,
        system_channel=None,
    )
    cog, _repository, _economy = await make_cog(tmp_path, guild)

    cog.note(GUILD, USER, StatDelta(add={"messages": 1}), 55)
    await cog.flush()

    channel.send.assert_awaited_once()
    assert cog._pending.get(GUILD, {}).get(USER) is not None  # el IRPF del premio


# -- Presentación ----------------------------------------------------------------------


def test_formato_de_horas_y_dinero() -> None:
    assert format_value(45, "min") == "45 min"
    assert format_value(90, "min") == "1,5 h"
    assert format_value(60_000, "min") == "1.000 h"
    assert format_value(12_500, "money") == "12.500 Y$"
    assert format_value(1_234) == "1.234"


def test_aviso_de_muchos_logros_los_lista_todos() -> None:
    ids = [a.id for a in AVAILABLE[:12]]
    [embed] = unlock_embeds("Diego", None, ids, None)
    assert "12 logros" in (embed.author.name or "")
    assert all(f"**{a.name}**" in (embed.description or "") for a in AVAILABLE[:12])


def test_aviso_que_no_cabe_sigue_en_mas_embeds() -> None:
    chosen = AVAILABLE[:200]
    embeds = unlock_embeds("Diego", None, [a.id for a in chosen], None)
    assert len(embeds) > 1
    assert "200 logros" in (embeds[0].author.name or "")
    assert all(e.author.name is None for e in embeds[1:])
    assert all(len(e.description or "") <= 4_096 for e in embeds)
    text = "\n".join(e.description or "" for e in embeds)
    assert all(f"**{a.name}**" in text for a in chosen)


def test_aviso_de_un_logro_cabe_en_tres_lineas() -> None:
    achievement = next(a for a in AVAILABLE if not a.story and a.rarity is Rarity.COMMON)
    income = IncomeResult(gross=50, tax=0, rate=0.0, balance=1_050)
    [embed] = unlock_embeds("Diego", None, [achievement.id], income)
    assert embed.author.name == "Diego · 🏆 ¡Logro desbloqueado!"
    assert embed.title is None
    lines = (embed.description or "").splitlines()
    assert lines == [
        f"{achievement.rarity.emoji} **{achievement.name}** · Común · 🪙 **+50 Y$**",
        f"-# {achievement.description}",
    ]


def test_aviso_con_retencion_dice_cuanto_se_lleva_hacienda() -> None:
    achievement = next(a for a in AVAILABLE if not a.story and a.rarity is Rarity.RARE)
    income = IncomeResult(gross=200, tax=38, rate=0.19, balance=5_162)
    description = unlock_embeds("Diego", None, [achievement.id], income)[0].description or ""
    assert "🪙 **+162 Y$** · 🐶 −38 Y$ (19,00 %)" in description.splitlines()[0]
    assert description.splitlines()[1] == f"-# {achievement.description}"


def test_los_secretos_no_se_ven_hasta_conseguirlos() -> None:
    category = next(c for c in CATEGORIES if c.key == "time")
    hidden = category_embed(category, "Diego", Profile({}, {}), {}, 5)
    shown = category_embed(category, "Diego", Profile({}, {"leet": 1.0}), {"leet": 1}, 5)
    assert "1337" not in (hidden.description or "")
    assert "1337" in (shown.description or "")


def test_resumen_sugiere_los_logros_mas_cercanos() -> None:
    embed = summary_embed("Diego", None, Profile({"messages": 95}, {}), {}, 3)
    near = next(f for f in embed.fields if f.name == "Casi lo tienes")
    assert "Ya no hay quien te calle" in near.value


# -- Ganchos en los juegos -------------------------------------------------------------


async def test_una_tirada_de_ruleta_cuenta_para_los_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bot.cogs.casino as casino_module
    from bot.cogs.casino import Casino, RouletteTable
    from bot.services.roulette import Wheel
    from tests.unit.test_casino_cog import FakeRenderer, make_interaction, make_user

    monkeypatch.setattr(casino_module, "REVEAL_MARGIN_SECONDS", 0)
    achievements, repository, economy = await make_cog(tmp_path)
    bot = MagicMock()
    bot.get_cog = lambda name: achievements if name == "Achievements" else None
    wheel = Wheel(lambda _n: 17, lightning=False)
    casino = Casino(bot, economy=economy, renderer=FakeRenderer(), wheel=wheel)  # type: ignore[arg-type]
    table = RouletteTable(casino, guild_id=GUILD, owner=make_user(USER), stake=100)
    table.message = SimpleNamespace(channel=FakeChannel())  # type: ignore[assignment]

    await table.choose(make_interaction(USER), parse_bet("17"))

    profile = await repository.profile(GUILD, USER)
    assert profile.stats["roulette_spins"] == 1
    assert profile.stats["roulette_straight_wins"] == 1
    assert profile.stats["casino_win_max"] == 2_900
    assert {"rl_1", "pleno_1", "bigwin_1k"} <= set(profile.unlocked)
    table.message.channel.send.assert_awaited_once()  # type: ignore[union-attr]


async def test_una_mano_de_blackjack_cuenta_para_los_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tests.unit.test_blackjack_cog as bj_tests
    from bot.cogs.blackjack import Blackjack

    achievements, repository, economy = await make_cog(tmp_path)
    bot = MagicMock()
    bot.get_cog = lambda name: achievements if name == "Achievements" else None
    blackjack = Blackjack(
        bot,
        economy=economy,
        renderer=bj_tests.FakeRenderer(),  # type: ignore[arg-type]
        shoe_factory=bj_tests.stacked(bj_tests.c(1), bj_tests.c(9), bj_tests.c(13), bj_tests.c(8)),
    )
    owner = MagicMock(spec=discord.Member, id=USER, display_name="Diego", bot=False)
    monkeypatch.setattr(bj_tests, "make_user", lambda user_id=USER: owner)

    await bj_tests.open_table(blackjack)

    profile = await repository.profile(GUILD, USER)
    assert profile.stats["bj_hands"] == 1
    assert profile.stats["bj_naturals"] == 1
    assert {"bj_1", "natural_1"} <= set(profile.unlocked)


# -- Tragaperras -----------------------------------------------------------------------


def _slots_stops(predicate) -> tuple[int, int, int]:  # noqa: ANN001
    for stops in itertools.product(*(range(len(s)) for s in REEL_STRIPS)):
        if predicate(spin_at(stops)):
            return stops
    raise AssertionError("Ninguna parada cumple la condición")


def test_tragaperras_cuenta_el_medio_premio_como_ganar_perdiendo() -> None:
    spin = spin_at(_slots_stops(lambda s: s.kind == Kind.CHERRY))
    delta = slots_stats(
        spin,
        stake=100,
        payout=50,
        jackpot=0,
        free=False,
        hot=False,
        turbo=True,
        session_spins=1,
        when=datetime(2026, 1, 1, 12, tzinfo=TIMEZONE),
    )
    assert delta.add["slots_ldw"] == 1
    assert "slots_wins" not in delta.add
    assert delta.add["slots_turbo"] == 1
    assert delta.add["slots_pot_fed"] == slots_pot_share(100)


def test_tragaperras_cuenta_trios_bote_y_noche() -> None:
    spin = spin_at(_slots_stops(lambda s: s.is_jackpot))
    delta = slots_stats(
        spin,
        stake=100,
        payout=0,
        jackpot=80_000,
        free=False,
        hot=False,
        turbo=False,
        session_spins=7,
        when=datetime(2026, 1, 1, 4, tzinfo=TIMEZONE),
    )
    assert delta.add["slots_jackpots"] == 1
    assert delta.peak["slots_jackpot_max"] == 80_000
    assert delta.peak["slots_win_max"] == 79_900
    assert delta.add["slots_night"] == 1
    assert delta.peak["slots_session_max"] == 7
    assert "jackpot_50k" in newly_unlocked({"slots_jackpots": 1, "slots_jackpot_max": 80_000}, [])


def test_giro_gratis_no_aporta_al_bote_y_cualquier_premio_es_ganar() -> None:
    spin = spin_at(_slots_stops(lambda s: s.kind == Kind.CHERRY), count_scatters=False)
    delta = slots_stats(
        spin,
        stake=100,
        payout=50,
        jackpot=0,
        free=True,
        hot=False,
        turbo=False,
        session_spins=2,
        when=datetime(2026, 1, 1, 12, tzinfo=TIMEZONE),
    )
    assert delta.add["slots_wins"] == 1
    assert delta.add["slots_free_spins"] == 1
    assert "slots_pot_fed" not in delta.add
    assert "slots_free_retriggers" not in delta.add


def test_giros_gratis_dentro_de_un_giro_gratis_cuentan_como_prorroga() -> None:
    spin = spin_at(_slots_stops(lambda s: s.triggers_free_spins))
    when = datetime(2026, 1, 1, 12, tzinfo=TIMEZONE)
    common = dict(
        stake=100, payout=0, jackpot=0, hot=False, turbo=False, session_spins=2, when=when
    )
    assert slots_stats(spin, free=True, **common).add["slots_free_retriggers"] == 1
    assert "slots_free_retriggers" not in slots_stats(spin, free=False, **common).add


def _autoplay_spin_delta(*, autoplay: bool, free: bool = False, jackpot: int = 0) -> StatDelta:
    spin = spin_at(_slots_stops(lambda s: s.is_jackpot if jackpot else s.kind == Kind.CHERRY))
    return slots_stats(
        spin,
        stake=100,
        payout=0,
        jackpot=jackpot,
        free=free,
        hot=False,
        turbo=False,
        session_spins=1,
        when=datetime(2026, 1, 1, 12, tzinfo=TIMEZONE),
        autoplay=autoplay,
    )


def test_cada_tirada_de_auto_cuenta_y_la_de_tirar_no() -> None:
    assert _autoplay_spin_delta(autoplay=True).add["slots_autoplay_spins"] == 1
    assert "slots_autoplay_spins" not in _autoplay_spin_delta(autoplay=False).add


def test_auto_cuenta_sus_giros_gratis_y_su_bote() -> None:
    delta = _autoplay_spin_delta(autoplay=True, free=True)
    assert delta.add["slots_autoplay_free"] == 1
    delta = _autoplay_spin_delta(autoplay=True, jackpot=5_000)
    assert delta.add["slots_autoplay_jackpot"] == 1
    plain = _autoplay_spin_delta(autoplay=False, free=True, jackpot=5_000)
    assert not {"slots_autoplay_free", "slots_autoplay_jackpot"} & plain.add.keys()


@pytest.mark.parametrize(
    ("reason", "net", "spins", "expected"),
    [
        (StopReason.MAX_SPINS, -300, 25, {"slots_autoplay_full"}),
        (StopReason.MAX_SPINS, 0, 25, {"slots_autoplay_full", "slots_autoplay_even"}),
        (StopReason.LOSS_LIMIT, -1_000, 10, {"slots_autoplay_loss_limit"}),
        (StopReason.BIG_PRIZE, 5_000, 4, {"slots_autoplay_bigwin"}),
        (StopReason.NO_FUNDS, -900, 9, {"slots_autoplay_broke"}),
        (StopReason.MANUAL, -50, 12, {"slots_autoplay_manual"}),
        (StopReason.MANUAL, 50, 9, {"slots_autoplay_manual"}),
        (
            StopReason.MANUAL,
            50,
            10,
            {"slots_autoplay_manual", "slots_autoplay_exit_ahead"},
        ),
        (StopReason.MANUAL, -50, 1, {"slots_autoplay_manual", "slots_autoplay_quick_quit"}),
        (StopReason.CLOSED, -50, 3, set()),
        (StopReason.ERROR, -50, 3, set()),
    ],
)
def test_sesion_de_auto_cuenta_segun_como_acaba(
    reason: StopReason, net: int, spins: int, expected: set[str]
) -> None:
    delta = slots_autoplay_stats(spins=spins, net=net, reason=reason)
    assert set(delta.add) == {"slots_autoplay_sessions", *expected}


def test_una_sesion_de_auto_sin_tiradas_no_cuenta() -> None:
    assert slots_autoplay_stats(spins=0, net=0, reason=StopReason.NO_FUNDS).add == {}


def test_los_logros_de_auto_se_desbloquean_con_sus_contadores() -> None:
    assert {"autoplay_1", "autoplay_ses_10"} <= set(
        newly_unlocked({"slots_autoplay_spins": 1, "slots_autoplay_sessions": 10}, [])
    )
    assert "autoplay_loss_1" in newly_unlocked({"slots_autoplay_loss_limit": 1}, [])
    assert "autoplay_jackpot" in newly_unlocked({"slots_autoplay_jackpot": 1}, [])


def _pachinko_volley(*draws: Draw):  # noqa: ANN202
    """Una tanda en la Clásica con una bola en START por cada sorteo dado (hasta 4)."""
    start = CLASSIC.start_pocket
    balls = [Ball((1,) * start + (0,) * (CLASSIC.rows - start))] * len(draws)
    balls += [Ball((1,) * 3 + (0,) * (CLASSIC.rows - 3))] * (10 - len(draws))
    queue = list(draws)
    return build_volley(CLASSIC, balls, lambda: queue.pop(0))


def _pachinko_delta(volley, *, autoplay: bool) -> StatDelta:  # noqa: ANN001
    return pachinko_stats(
        volley,
        motion=motion_for(volley),
        stake=100,
        won=0,
        turbo=False,
        session_volleys=1,
        when=datetime(2026, 1, 1, 12, tzinfo=TIMEZONE),
        autoplay=autoplay,
    )


_PACHINKO_MISS = Draw((1, 2, 3), PachinkoKind.MISS, False, 0)
_PACHINKO_FAKE = Draw((4, 6, 4), PachinkoKind.MISS, True, 0)
_PACHINKO_SUPER = Draw((7, 7, 7), PachinkoKind.SUPER, True, 4)


def test_cada_tanda_de_auto_del_pachinko_cuenta_y_la_de_lanzar_no() -> None:
    volley = _pachinko_volley(_PACHINKO_FAKE, _PACHINKO_MISS, _PACHINKO_SUPER)
    auto = _pachinko_delta(volley, autoplay=True)
    assert auto.add["pachinko_autoplay_volleys"] == 1
    assert auto.add["pachinko_autoplay_fake_reach"] == 1
    assert auto.add["pachinko_autoplay_super"] == 1
    assert auto.peak["pachinko_autoplay_renchan_max"] == 4
    plain = _pachinko_delta(volley, autoplay=False)
    assert not [stat for stat in (*plain.add, *plain.peak) if "autoplay" in stat]
    # Lo demás se cuenta igual: el Auto es otra forma de pulsar 🎯 Lanzar.
    assert {k: v for k, v in auto.add.items() if "autoplay" not in k} == plain.add


@pytest.mark.parametrize(
    ("reason", "net", "volleys", "expected"),
    [
        (StopReason.MAX_SPINS, -300, 25, {"pachinko_autoplay_full"}),
        (StopReason.MAX_SPINS, 0, 25, {"pachinko_autoplay_full", "pachinko_autoplay_even"}),
        (StopReason.LOSS_LIMIT, -1_000, 10, {"pachinko_autoplay_loss_limit"}),
        (StopReason.BIG_PRIZE, 5_000, 4, {"pachinko_autoplay_bigwin"}),
        (StopReason.NO_FUNDS, -900, 9, {"pachinko_autoplay_broke"}),
        (StopReason.MANUAL, -50, 12, {"pachinko_autoplay_manual"}),
        (StopReason.MANUAL, 50, 9, {"pachinko_autoplay_manual"}),
        (StopReason.MANUAL, 50, 10, {"pachinko_autoplay_manual", "pachinko_autoplay_exit_ahead"}),
        (StopReason.MANUAL, -50, 1, {"pachinko_autoplay_manual", "pachinko_autoplay_quick_quit"}),
        (StopReason.CLOSED, -50, 3, set()),
        (StopReason.ERROR, -50, 3, set()),
    ],
)
def test_sesion_de_auto_del_pachinko_cuenta_segun_como_acaba(
    reason: StopReason, net: int, volleys: int, expected: set[str]
) -> None:
    delta = pachinko_autoplay_stats(volleys=volleys, net=net, reason=reason)
    assert set(delta.add) == {"pachinko_autoplay_sessions", *expected}


def test_una_sesion_de_auto_del_pachinko_sin_tandas_no_cuenta() -> None:
    assert pachinko_autoplay_stats(volleys=0, net=0, reason=StopReason.NO_FUNDS).add == {}


def test_los_logros_de_auto_del_pachinko_se_desbloquean_con_sus_contadores() -> None:
    unlocked = set(
        newly_unlocked({"pachinko_autoplay_volleys": 1, "pachinko_autoplay_sessions": 10}, [])
    )
    assert {"pachi_auto_1", "pachi_auto_ses_10"} <= unlocked
    assert "pachi_auto_atari_1" in newly_unlocked({"pachinko_autoplay_bigwin": 1}, [])
    assert "pachi_auto_ren_3" in newly_unlocked({"pachinko_autoplay_renchan_max": 3}, [])


def test_los_logros_de_auto_del_pachinko_no_chocan_con_los_de_la_tragaperras() -> None:
    pachinko = [a for a in CATALOG if a.id.startswith("pachi_auto_")]
    slots = [a for a in CATALOG if a.id.startswith("autoplay_")]
    assert len(pachinko) >= 25
    assert {a.name for a in pachinko}.isdisjoint({a.name for a in slots})
    assert {a.id for a in pachinko}.isdisjoint({a.id for a in slots})
    assert all(a.category == "pachinko" for a in pachinko)
    secrets = sum(a.secret for a in pachinko)
    assert len(pachinko) / 8 <= secrets <= len(pachinko) / 3  # alrededor de uno de cada seis


def test_pisos_cuenta_los_tipos_de_barrio_la_zona_tensionada_y_el_barrio_entero() -> None:
    stats = {f"{PISOS_BARRIO_PREFIX}{key}": 1 for key in ("isleta", "sanandres", "taco")}
    assert with_derived(stats)[PISOS_THEMES_STAT] == 2  # marinero y obrero
    every = {f"{PISOS_BARRIO_PREFIX}{key}": 1 for key in HOUSING_BARRIOS}
    assert with_derived(every)[PISOS_THEMES_STAT] == 7
    delta = housing_purchase_stats(
        kind="cueva", barrio="tafira", total=1, itp=0, shares=1, shares_now=1, holdings=1,
        whole=0, islands=1, balance_after=5_000, when=datetime(2026, 10, 16, 12, tzinfo=TIMEZONE),
        tensed=True, barrio_whole=True,
    )  # fmt: skip
    assert delta.add["pisos_tensed"] == delta.add["pisos_barrio_whole"] == 1
    assert delta.add[f"{PISOS_KIND_PREFIX}cueva"] == 1


def test_pisos_cuenta_plantas_habitaciones_y_el_atajo() -> None:
    plantas = housing_action_stats("plantas", amount=1_200_000, units=5, mortgaged=True)
    assert plantas.add == {
        "pisos_floor_splits": 1, "pisos_split_spent": 1_200_000, "pisos_split_mortgaged": 1,
    }  # fmt: skip
    assert plantas.peak == {"pisos_split_units": 5}
    habitaciones = housing_action_stats("habitaciones", amount=30_000, units=6, mortgaged=True)
    assert "pisos_split_mortgaged" not in habitaciones.add  # solo cuenta por plantas
    assert habitaciones.add["pisos_room_splits"] == 1
    assert habitaciones.peak == {"pisos_split_units": 6}
    assert housing_action_stats("juntar").add == {"pisos_rooms_joined": 1}
    assert housing_action_stats("juntar", rushed=True).add["pisos_rooms_rushed"] == 1
    assert housing_panel_stats(screen="atajo").add == {"pisos_shortcut": 1}
    sale = {"shares": 100, "received": 1, "gain": 0, "arrears": 0, "held_days": 3}
    assert housing_sale_stats(**sale, holdings_left=1, floor=True).add["pisos_floor_sold"] == 1
    assert "pisos_floor_sold" not in housing_sale_stats(**sale, holdings_left=1).add
    member = housing_action_stats("vende_miembro", amount=1, floor_unit=True)
    assert member.add["pisos_floor_sold"] == 1
    # Los 10 inquilinos de habitación, firmados con candidato o con la noche
    assert len(ROOM_TENANTS) == 10 and "universitaria" in ROOM_TENANTS
    signed = housing_action_stats("firma_candidato", tenant="astrofisica").add
    assert signed[f"{PISOS_TENANT_PREFIX}astrofisica"] == 1
    every = {f"{PISOS_TENANT_PREFIX}{key}": 1 for key in ROOM_TENANTS}
    assert with_derived(every)[PISOS_ROOM_TENANTS_STAT] == 10
    assert with_derived({f"{PISOS_TENANT_PREFIX}abuela": 1})[PISOS_ROOM_TENANTS_STAT] == 0
    stats = with_derived({
        **every, "pisos_floor_splits": 6, "pisos_room_splits": 20, "pisos_rooms_joined": 5,
        "pisos_split_spent": 10_000_000, "pisos_split_units": 6, "pisos_shortcut": 50,
        "pisos_rooms_rushed": 1, "pisos_split_mortgaged": 1, "pisos_floor_sold": 1,
    })  # fmt: skip
    new = {
        "pisos_floors_1", "pisos_floors_3", "pisos_floors_6", "pisos_rooms_1", "pisos_rooms_5",
        "pisos_rooms_20", "pisos_joined_1", "pisos_joined_5", "pisos_split_1m",
        "pisos_split_10m", "pisos_units_5", "pisos_units_6", "pisos_room_tenants_1",
        "pisos_room_tenants_5", "pisos_room_tenants_all", "pisos_tenant_astrofisica",
        "pisos_tenant_jubilado_hab", "pisos_shortcut_1", "pisos_shortcut_50", "pisos_rushed_1",
        "pisos_split_mortgaged_1", "pisos_floor_sold_1",
    }  # fmt: skip
    assert new <= set(newly_unlocked(stats, ()))
    assert {a.category for a in CATALOG if a.id in new} == {"pisos"}


def test_pisos_cuenta_las_calles_y_lo_que_se_tiene_en_sus_edificios() -> None:
    assert housing_panel_stats(screen="calles").add == {"pisos_streets_opened": 1}
    seen = housing_panel_stats(screen="edificio", building="triana_e3").add
    assert seen == {f"{PISOS_BUILDING_PREFIX}triana_e3": 1}
    assert housing_panel_stats(screen="barrio", building="triana_e3").add == {}
    every = {f"{PISOS_BUILDING_PREFIX}{b.key}": 1 for b in STREET_BUILDINGS}
    assert with_derived(every)[PISOS_BUILDINGS_SEEN_STAT] == len(STREET_BUILDINGS)

    def kind(key: str) -> str:
        item = STREET_UNIT_BY_KEY.get(key) or HOUSING_LISTING_BY_KEY.get(key)
        return item.kind if item else ""

    def first(of: str) -> StreetBuilding:
        return next(b for b in STREET_BUILDINGS if b.kind == of)

    bloque = next(
        b for b in STREET_BUILDINGS
        if b.kind == "bloque" and {"garaje", "trastero", "local", "atico"}
        <= {kind(u.key) for u in b.units}
    )  # fmt: skip
    keys = [u.key for u in bloque.units]
    # Una participación de cada: el pack y nada entero
    one = housing_streets_stats(dict.fromkeys(keys, 1)).peak
    assert one["pisos_pack"] == 1 and one["pisos_buildings_max"] == 1
    assert one["pisos_streets_barrio_max"] == 1
    assert one["pisos_street_units_max"] == sum(k in STREET_UNIT_BY_KEY for k in keys)
    assert "pisos_whole_bloque" not in one and "pisos_above_shop" not in one
    # El ático y el sótano enteros, sin lo de en medio
    ends = {u.key: 100 for u in bloque.units if u.level == -1 or kind(u.key) == "atico"}
    assert housing_streets_stats(ends).peak.get("pisos_attic_basement") == 1
    assert (
        "pisos_attic_basement"
        not in housing_streets_stats({k: 99 if kind(k) == "atico" else 100 for k in ends}).peak
    )
    # La vivienda y el local de abajo, enteros
    home = next(u.key for u in bloque.units if u.level > 0)
    local = next(u.key for u in bloque.units if kind(u.key) == "local")
    assert housing_streets_stats({home: 100, local: 100}).peak.get("pisos_above_shop") == 1
    # Los áticos caros (9.16.0): uno se cuenta; un ático normal, no.
    from bot.services.housing_streets import STREET_UNITS as _UNITS

    penthouse = next(u.key for u in _UNITS if u.key.endswith("_sa"))
    plain = next(u.key for u in _UNITS if u.key.endswith("_at"))
    assert housing_streets_stats({penthouse: 10}).peak["pisos_penthouses_max"] == 1
    assert housing_streets_stats({plain: 100}).peak["pisos_penthouses_max"] == 0
    # Todo el bloque (una planta de un inmueble dividido cuenta como suyo)
    whole = housing_streets_stats({**dict.fromkeys(keys, 100), f"{home}~1": 100}).peak
    assert whole["pisos_whole_bloque"] == whole["pisos_attic_basement"] == 1
    for of, stat in (
        ("aparcamiento", "pisos_whole_aparcamiento"),
        ("guardamuebles", "pisos_whole_guardamuebles"),
        ("pantalan", "pisos_whole_pantalan"),
    ):
        units = [u.key for u in first(of).units]
        assert housing_streets_stats(dict.fromkeys(units, 100)).peak[stat] == 1
        assert stat not in housing_streets_stats({**dict.fromkeys(units, 100), units[0]: 50}).peak
    # Las tres calles de un barrio y muchas unidades nuevas
    barrio = bloque.barrio
    spread = {b.units[0].key: 1 for b in STREET_BUILDINGS if b.barrio == barrio}
    assert housing_streets_stats(spread).peak["pisos_streets_barrio_max"] == 3
    many = housing_streets_stats({u.key: 1 for u in STREET_UNITS[:100]}).peak
    assert many["pisos_street_units_max"] == 100
    portals = {b.units[0].key: 1 for b in STREET_BUILDINGS[:25]}
    assert housing_streets_stats(portals).peak["pisos_buildings_max"] == 25
    assert housing_streets_stats({}).peak == {
        "pisos_street_units_max": 0, "pisos_buildings_max": 0, "pisos_streets_barrio_max": 0,
        "pisos_penthouses_max": 0,
    }  # fmt: skip
    stats = with_derived({
        **every, "pisos_streets_opened": 1, "pisos_street_units_max": 100,
        "pisos_buildings_max": 25, "pisos_streets_barrio_max": 3, "pisos_pack": 1,
        "pisos_whole_bloque": 1, "pisos_whole_guardamuebles": 1, "pisos_whole_aparcamiento": 1,
        "pisos_whole_pantalan": 1, "pisos_attic_basement": 1, "pisos_above_shop": 1,
    })  # fmt: skip
    new = {
        "pisos_streets_1", "pisos_bldg_seen_10", "pisos_bldg_seen_50", "pisos_bldg_seen_all",
        "pisos_street_units_1", "pisos_street_units_5", "pisos_street_units_25",
        "pisos_street_units_100", "pisos_buildings_5", "pisos_buildings_25",
        "pisos_streets_barrio_3", "pisos_pack_1", "pisos_whole_bloque_1",
        "pisos_whole_guardamuebles_1", "pisos_whole_aparcamiento_1", "pisos_whole_pantalan_1",
        "pisos_attic_basement_1", "pisos_above_shop_1",
    }  # fmt: skip
    assert new <= set(newly_unlocked(stats, ()))
    assert {a.category for a in CATALOG if a.id in new} == {"pisos"}
    assert sum(a.secret for a in CATALOG if a.id in new) == 5


def test_cualquier_jugada_del_casino_cuenta_el_juego_y_la_fecha() -> None:
    viernes13 = datetime(2026, 2, 13, 3, 0, tzinfo=TIMEZONE)
    delta = casino_record_stats("minas", net=-50, when=viernes13)
    assert delta.add == {
        "casino_plays": 1,
        "casino_game_minas": 1,
        "casino_night_minas": 1,
        "casino_friday13_minas": 1,
    }
    ganando = casino_record_stats("minas", net=50, when=viernes13)
    assert "casino_friday13_minas" not in ganando.add
    nochevieja = casino_record_stats(
        "ruleta", net=0, when=datetime(2026, 12, 31, 20, tzinfo=TIMEZONE)
    )
    assert nochevieja.add["casino_nochevieja_ruleta"] == 1
    assert (
        casino_record_stats("pollo", net=0, when=datetime(2026, 12, 25, 12, tzinfo=TIMEZONE)).add[
            "casino_navidad"
        ]
        == 1
    )


def test_los_juegos_distintos_del_casino_salen_de_los_contadores_por_juego() -> None:
    stats = {
        "casino_game_ruleta": 1_000,
        "casino_game_minas": 100,
        "casino_game_pollo": 1,
        "casino_game_inventado": 5_000,
        "casino_night_pollo": 1,
    }
    full = with_derived(stats)
    assert full[CASINO_GAMES_STAT] == 3
    assert full[CASINO_GAMES_100_STAT] == 2
    assert full[CASINO_GAMES_1000_STAT] == 1
    assert full[CASINO_NIGHT_GAMES_STAT] == 1


# -- Apuestas deportivas -----------------------------------------------------------------


def _sports_bet(**over: object) -> StatDelta:
    args = {
        "stake": 100, "odds": 200, "pick": "1", "sport": "soccer_spain_la_liga",
        "balance_before": 1_000, "lead": 3 * 86_400, "when": at(12),
    }  # fmt: skip
    return sports_bet_stats(**{**args, **over})


def test_una_apuesta_deportiva_normal_solo_cuenta_lo_basico() -> None:
    delta = _sports_bet()
    assert delta.add == {
        "sports_bets": 1,
        "sports_staked": 100,
        f"{SPORTS_LEAGUE_PREFIX}soccer_spain_la_liga": 1,
        f"{SPORTS_MARKET_PREFIX}1x2": 1,
        f"{SPORTS_PICK_PREFIX}1": 1,
    }
    assert delta.peak == {
        "sports_stake_max": 100, "sports_odds_max": 200, "sports_match_markets_max": 1,
    }  # fmt: skip


def test_una_apuesta_de_goles_cuenta_su_mercado_y_no_un_signo_del_1x2() -> None:
    delta = _sports_bet(pick="+2.5", market="goles", match_markets=3)
    assert delta.add[f"{SPORTS_MARKET_PREFIX}goles"] == 1
    assert not any(stat.startswith(SPORTS_PICK_PREFIX) for stat in delta.add)
    assert delta.peak["sports_match_markets_max"] == 3


def test_el_signo_se_cuenta_en_minuscula() -> None:
    assert _sports_bet(pick="X").add[f"{SPORTS_PICK_PREFIX}x"] == 1


@pytest.mark.parametrize(
    ("over", "stat"),
    [
        ({"odds": 500}, "sports_bet_long"),
        ({"odds": 130}, "sports_bet_safe"),
        ({"stake": 1_000}, "sports_all_in"),
        ({"stake": 10}, "sports_bet_min"),
        ({"stake": 50_000, "balance_before": 90_000}, "sports_bet_cap"),
        ({"stake": 666}, "sports_bet_666"),
        ({"stake": 69}, "sports_bet_69"),
        ({"lead": 300}, "sports_last_minute"),
        ({"lead": 6 * 86_400}, "sports_early"),
        ({"when": at(3)}, "sports_night"),
        ({"when": at(12, month=12, day=31)}, "sports_nochevieja"),
        ({"when": at(12, month=5, day=30)}, "sports_canarias"),
        ({"when": at(12, month=2, day=13)}, "sports_friday13"),
    ],
)
def test_las_apuestas_deportivas_con_guasa_suman_su_estadistica(over: dict, stat: str) -> None:
    assert _sports_bet(**over).add[stat] == 1


def test_los_limites_de_las_apuestas_deportivas_no_se_pasan_de_largo() -> None:
    assert "sports_bet_long" not in _sports_bet(odds=499).add
    assert "sports_bet_safe" not in _sports_bet(odds=131).add
    assert "sports_last_minute" not in _sports_bet(lead=301).add
    assert "sports_early" not in _sports_bet(lead=6 * 86_400 - 1).add
    assert "sports_night" not in _sports_bet(when=at(6)).add
    assert "sports_all_in" not in _sports_bet(stake=100, balance_before=0).add


def _sports_result(**over: object) -> StatDelta:
    args = {
        "stake": 100, "odds": 200, "pick": "1", "payout": 200, "home": 2, "away": 1, "streak": 1,
    }  # fmt: skip
    return sports_result_stats(**{**args, **over})


def test_un_acierto_suma_ganancia_cuota_y_racha() -> None:
    delta = _sports_result(odds=250, payout=250, streak=3)
    assert delta.add == {"sports_settled": 1, "sports_wins": 1, "sports_win_by_one": 1}
    assert delta.peak == {
        "sports_win_max": 150, "sports_odds_won_max": 250, "sports_win_streak_max": 3,
    }  # fmt: skip


@pytest.mark.parametrize(
    ("over", "stat"),
    [
        ({"pick": "X", "payout": 300, "home": 1, "away": 1}, "sports_win_draw"),
        ({"pick": "X", "payout": 300, "home": 0, "away": 0}, "sports_win_00"),
        ({"home": 4, "away": 0}, "sports_win_rout"),
        ({"pick": "2", "home": 0, "away": 4}, "sports_win_rout"),
        ({"pick": "2", "home": 0, "away": 1}, "sports_win_by_one"),
    ],
)
def test_los_aciertos_con_guasa_suman_su_estadistica(over: dict, stat: str) -> None:
    assert _sports_result(**over).add[stat] == 1


def test_un_empate_acertado_no_es_ni_goleada_ni_por_un_gol() -> None:
    draw = _sports_result(pick="X", payout=300, home=1, away=1)
    assert draw.add == {"sports_settled": 1, "sports_wins": 1, "sports_win_draw": 1}
    assert "sports_win_00" not in _sports_result(pick="X", payout=300, home=2, away=2).add
    assert "sports_win_rout" not in _sports_result(home=3, away=0).add


def test_un_fallo_suma_el_fallo_y_ningun_pico() -> None:
    delta = _sports_result(payout=0, streak=0, home=0, away=2)
    assert delta.add == {"sports_settled": 1, "sports_losses": 1}
    assert not delta.peak


@pytest.mark.parametrize(
    ("over", "stat"),
    [
        ({"odds": 130, "home": 0, "away": 1}, "sports_lost_safe"),
        ({"home": 1, "away": 1}, "sports_lost_tied"),
        ({"pick": "1", "home": 0, "away": 4}, "sports_lost_rout"),
        ({"pick": "2", "home": 4, "away": 0}, "sports_lost_rout"),
        ({"home": 0, "away": 1}, "sports_lost_by_one"),
        ({"pick": "X", "home": 3, "away": 3}, "sports_lost_goals"),
    ],
)
def test_los_fallos_con_guasa_suman_su_estadistica(over: dict, stat: str) -> None:
    assert _sports_result(payout=0, streak=0, **over).add[stat] == 1


def test_un_fallo_al_empate_no_cuenta_como_empate_ni_como_goleada() -> None:
    delta = _sports_result(payout=0, streak=0, pick="X", home=3, away=0)
    assert delta.add.keys() == {"sports_settled", "sports_losses"}
    assert "sports_lost_goals" not in _sports_result(payout=0, pick="X", home=2, away=0).add


@pytest.mark.parametrize(
    ("over", "stat"),
    [
        ({"pick": "+3.5", "market": "goles", "home": 3, "away": 1}, "sports_win_over35"),
        ({"pick": "-1.5", "market": "goles", "home": 1, "away": 0}, "sports_win_under15"),
        ({"pick": "+2.5", "market": "goles", "home": 2, "away": 1}, "sports_win_goles"),
        ({"pick": "si", "market": "ambos", "home": 2, "away": 1}, "sports_win_ambos"),
        ({"pick": "2-1", "market": "exacto"}, "sports_win_exacto"),
        ({"pick": "otro", "market": "exacto", "home": 5, "away": 0}, "sports_win_otro"),
    ],
)
def test_los_aciertos_de_los_mercados_de_goles_suman_su_estadistica(over: dict, stat: str) -> None:
    assert _sports_result(**over).add[stat] == 1


@pytest.mark.parametrize(
    ("over", "stat"),
    [
        ({"pick": "+2.5", "market": "goles", "home": 1, "away": 1}, "sports_lost_on_line"),
        ({"pick": "-2.5", "market": "goles", "home": 2, "away": 1}, "sports_lost_on_line"),
        ({"pick": "2-1", "market": "exacto", "home": 2, "away": 2}, "sports_exact_near"),
        ({"pick": "si", "market": "ambos", "home": 4, "away": 0}, "sports_lost_ambos_rout"),
    ],
)
def test_los_fallos_de_los_mercados_de_goles_suman_su_estadistica(over: dict, stat: str) -> None:
    assert _sports_result(payout=0, streak=0, **over).add[stat] == 1


def test_los_mercados_de_goles_no_cuentan_lo_del_1x2() -> None:
    won = _sports_result(pick="-2.5", market="goles", home=1, away=0)
    assert won.add == {"sports_settled": 1, "sports_wins": 1, "sports_win_goles": 1}
    lost = _sports_result(pick="si", market="ambos", payout=0, streak=0, home=1, away=1)
    assert lost.add == {"sports_settled": 1, "sports_losses": 1}
    far = _sports_result(pick="+3.5", market="goles", payout=0, streak=0, home=1, away=0)
    assert "sports_lost_on_line" not in far.add
    wide = _sports_result(pick="0-0", market="exacto", payout=0, streak=0, home=2, away=1)
    assert "sports_exact_near" not in wide.add


def test_anular_o_mirar_las_apuestas_deportivas_tiene_su_contador() -> None:
    assert sports_void_stats().add == {"sports_refunds": 1}
    assert sports_slip_stats().add == {"sports_slips": 1}


def test_negocio_cuenta_sectores_temas_e_islas_distintos() -> None:
    stats = {
        "biz_opened_taller": 2,
        "biz_opened_tienda": 1,
        "biz_theme_lujo": 3,
        "biz_island_tf": 1,
    }
    full = with_derived(stats)
    assert full["biz_sectors_tried"] == 2
    assert full["biz_themes_tried"] == 1
    assert full["biz_islands_tried"] == 1
    assert "biz_streak_365" in newly_unlocked({"biz_streak_max": 365}, [])


def test_el_pozo_cuenta_lo_que_se_queda_cerca_y_lo_de_cada_tirada() -> None:
    from bot.services.achievements import picar_stats

    def stats(row: int, **extra: object) -> dict[str, int]:
        run = PicarRun(seed=1, pick="diamond", max_row=row, ticks=125 * 60, explosions=3)
        run.broken = {"coal": 2, "quartz": 1, "gold_block": 1, "chest": 4, "crafting_table": 3}
        args = {"pick": "diamond", "cost": 1_000, "payout": 950, "new_record": False}
        args.update(extra)
        delta = picar_stats(run, when=datetime(2026, 10, 28, 12), **args)  # type: ignore[arg-type]
        return {**delta.add, **delta.peak}

    near = stats(77, streak=9, tax=300)
    assert near["picar_near_nether"] == 1 and "picar_tourist_nether" not in near
    assert near["picar_almost"] == 1 and "picar_even" not in near
    assert near["picar_min_coal"] == 2 and near["picar_min_quartz"] == 1
    assert near["picar_mult_gold_block"] == 1 and near["picar_chests_run_max"] == 4
    assert near["picar_crafts_run_max"] == 3 and near["picar_tnt_run_max"] == 3
    assert near["picar_seconds_max"] == 125 and near["picar_days_max"] == 9
    assert near["picar_tax_max"] == 300 and near["picar_blocks_run_max"] == 11
    inside = stats(81, payout=1_000, capped=True, server_record=True)
    assert inside["picar_tourist_nether"] == 1 and "picar_near_nether" not in inside
    assert inside["picar_even"] == inside["picar_capped"] == inside["picar_server_records"] == 1
    assert "picar_tax_max" not in inside
    assert stats(112)["picar_row_112"] == 1
    assert stats(215)["picar_near_dragon"] == 1
    assert stats(30, payout=666)["picar_paid_666"] == 1
    assert near["picar_ldw"] == 1 and "picar_ldw" not in inside


def test_jovani_cuenta_el_dia_y_el_look_y_no_se_suma_dos_veces_el_mismo_dia() -> None:
    when = datetime(2026, 6, 10, 12, tzinfo=UTC)
    delta = jovani_stats("casino", when=when)
    assert delta.add == {"jovani_day_20260610": 1, "jovani_look_casino": 1}
    stats = {"jovani_day_20260610": 3, "jovani_look_casino": 2, "jovani_look_pijama": 1}
    derived = with_derived(stats)
    assert derived["jovani_days"] == 1
    assert derived["jovani_looks"] == 2


def test_jovani_sin_look_automatico_solo_cuenta_el_dia() -> None:
    delta = jovani_stats(None, when=datetime(2026, 6, 10, 12, tzinfo=UTC))
    assert list(delta.add) == ["jovani_day_20260610"]


@pytest.mark.parametrize(
    ("when", "stat"),
    [
        (datetime(2026, 6, 10, 13, 37, tzinfo=UTC), "jovani_1337"),
        (datetime(2026, 6, 10, 0, 0, tzinfo=UTC), "jovani_midnight"),
        (datetime(2026, 11, 13, 12, 0, tzinfo=UTC), "jovani_friday13"),
    ],
)
def test_las_fechas_de_jovani_tienen_su_contador(when: datetime, stat: str) -> None:
    assert jovani_stats(None, when=when).add[stat] == 1


def test_la_coleccion_de_jovani_pide_todos_los_looks_de_serie() -> None:
    assert BY_ID["jovani_looks_12"].conditions == (("jovani_looks", len(BUILTIN_LOOKS)),)
    looks = {key for a in AVAILABLE for key, _ in a.conditions if key.startswith("jovani_look_")}
    assert looks == {f"jovani_look_{look.key}" for look in BUILTIN_LOOKS if look.key != "normal"}
