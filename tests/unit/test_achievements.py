"""Pruebas de los logros: catálogo, reglas, repositorio, cog y premios con IRPF."""

from __future__ import annotations

import asyncio
import itertools
import sqlite3
from datetime import datetime
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
    unlock_embed,
)
from bot.repositories.achievements import AchievementRepository, Profile
from bot.repositories.economy import EconomyRepository
from bot.repositories.message_stats import MessageStatsRepository
from bot.services.achievements import (
    APUESTAS_PAGES,
    APUESTAS_PERIODS,
    AVAILABLE,
    BJ_HIGH_STAKE,
    BUS_WIN_PREFIX,
    BY_ID,
    CASINO_GROUP,
    CATALOG,
    CATEGORIES,
    CHICKEN_VEHICLE_KINDS,
    HORSE_BACKED_KINDS_STAT,
    HORSE_BACKED_MAX_STAT,
    HORSE_BET_KINDS,
    HORSE_DISTANCES,
    IMG_EFFECTS_STAT,
    LAUGH_KINDS,
    MESSAGES_TOTAL_STAT,
    PET_SPAWN_KINDS_STAT,
    PET_SPECIES_PREFIX,
    PET_SPECIES_STAT,
    PORRA_GAME_PREFIX,
    PORRA_GAMES,
    PORRA_PROP_PREFIX,
    PORRA_PROPS,
    PORRA_STATS,
    ROULETTE_FAVOURITE_STAT,
    ROULETTE_HIT_PREFIX,
    ROULETTE_NUMBERS_STAT,
    SHOP_AISLES_STAT,
    SHOP_TRACKED_KEYS,
    SHOP_USE_KINDS_STAT,
    UNLOCKED_STAT,
    Rarity,
    StatDelta,
    beernight_close_stats,
    beernight_drink_stats,
    blackjack_stats,
    casino_stats,
    group_sections,
    is_laugh,
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
    total_reward,
)
from bot.services.autoplay import StopReason
from bot.services.beernight import Reason as BeerReason
from bot.services.blackjack import BlackjackGame, Card, Hand
from bot.services.bus import Pick as BusPick
from bot.services.chicken import DIFFICULTIES as CHICKEN_DIFFICULTIES
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService, IncomeResult
from bot.services.levels import TIMEZONE
from bot.services.lottery import GAMES as LOTTERY_GAMES
from bot.services.pachinko import CLASSIC, Ball, Draw, build_volley
from bot.services.pachinko import Kind as PachinkoKind
from bot.services.pachinko_motion import motion_for
from bot.services.pets_catalog import SPECIES as PET_SPECIES
from bot.services.roulette import DOUBLE_ZERO, OUTSIDE_BETS, RoundOutcome, Wager, parse_bet
from bot.services.shop_uses import USES as SHOP_USES
from bot.services.slots import REEL_STRIPS, Kind, spin_at
from bot.services.work_catalog import EVENTS, RESIGN_EVENT

GUILD = 1
USER = 10

# Todas las estadísticas que produce algún sitio del bot. Si un logro usa
# otra, nadie la sumaría nunca y sería imposible de conseguir.
PRODUCED_STATS = {
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
    "imv_claims", "imv_streak_max", "renta_filed", "renta_refunded", "tax_paid",
    "balance_max",
    # Bienvenida (botón 👋 de cogs/welcome.py)
    "welcomes_given", "welcomes_fast",
    # Botón 📜 Leído de las novedades (cogs/deploy.py)
    "news_read", "news_first",
    # Lista de tareas (cogs/todo.py)
    "todo_added", "todo_done",
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
    "wealth_tax_paid", "wealth_tax_weeks", "donated", "ongs_supported",
    # Intereses de la cuenta (cogs/intereses.py: day_stats, savings_stats y hint_for)
    "interest_earned", "interest_days", "interest_tax", "interest_capped", "interest_rounding",
    "interest_zero", "interest_grasshopper", "interest_gambled", "interest_beats_imv",
    "interest_bizum_trick", "interest_avg_max", "interest_capped_streak", "interest_floor_streak",
    "interest_resist_streak", "interest_still_streak", "interest_ant_streak", "savings_rate_max",
    "interest_comeback",
    # Tienda (cogs/shop.py: shop_stats)
    "shop_purchases", "shop_spent", "shop_igic", "shop_roles", "shop_renewals", "shop_boosts",
    "shop_boost_queue_max", "shop_collection_max", "shop_sale_buys", "shop_discount_max",
    "shop_luxury", "shop_big_buy_max", "shop_limited", "shop_first_serial", "shop_last_unit",
    "shop_broke_buy", SHOP_AISLES_STAT, *(f"shop_key_{key}" for key in SHOP_TRACKED_KEYS),
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
    # Mascotas (cogs/pets.py: pet_adopt_stats, pet_care_stats, pet_cameo_stats…)
    "pet_adopted", "pet_spawned", "pet_protectora", "pet_owned_max", PET_SPECIES_STAT,
    PET_SPAWN_KINDS_STAT, *(f"{PET_SPECIES_PREFIX}{s.key}" for s in PET_SPECIES),
    "pet_cares", "pet_petted", "pet_played", "pet_fed", "pet_favourite", "pet_gifts",
    "pet_bond_max", "pet_tricks_max", "pet_streak_max", "pet_fed_nothing", "pet_goat_tax",
    "pet_goat_odd", "pet_night", "pet_san_anton", "pet_christmas", "pet_halloween",
    "pet_canarias", "pet_cameos", "pet_cameo_bust", "pet_cameo_big", "pet_cameo_night",
    "pet_renamed", "pet_named_sanxe", "pet_named_beast", "pet_switches",
    # Bizum (cogs/bizum.py: bizum_stats y bizum_received_stats)
    "bizum_sent_count", "bizum_sent", "bizum_max", "bizum_day_max", "bizum_full", "bizum_min",
    "bizum_broke", "bizum_received_count", "bizum_received",
    # Loterías (cogs/lottery.py: lottery_buy_stats, lottery_prize_stats, scratch_stats)
    "lottery_bets", "lottery_spent", "lottery_prizes", "lottery_won", "lottery_win_max",
    "lottery_reintegros", "lottery_navidad", "lottery_nino", "lottery_pedrea",
    "lottery_gordo_navidad", "lottery_euro_bets", "lottery_lotto4", "lottery_lotto5",
    "lottery_jackpot", "lottery_scratches", "lottery_scratch_top", "lottery_draw_bets_max",
    "lottery_broke_buy", "lottery_gravamen",
    # Casino
    "roulette_spins", "roulette_wins", "roulette_straight_wins", "roulette_green_wins",
    "roulette_double_zero_wins", "roulette_color_wins", "roulette_wagers_max",
    "roulette_streak_max", "roulette_repeat_pocket",
    "bj_hands", "bj_wins", "bj_naturals", "bj_double_wins", "bj_splits", "bj_split_sweeps",
    "bj_busts", "bj_pushes", "bj_21_multi", "bj_dealer_busts", "bj_dealer_naturals",
    "bj_dealer_bj_saved", "bj_max_stake", "bj_insured", "bj_insurance_wasted", "bj_insured_bust",
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
    "slots_cooled", "slots_cooled_max",
    "slots_bonus_fills", "slots_bonus_slowest", "slots_bonus_quick",
    "slots_tickets", "slots_ticket_gross_max", "slots_ticket_best", "slots_ticket_loss_max",
    "slots_ticket_creative", "slots_ticket_taxed", "slots_ticket_zero", "slots_ticket_dry",
    "slots_ticket_quick",
    # Botes (cogs/hold_win.py: hold_win_stats, hold_win_bonus_stats y el botón de Auto)
    "botes_spins", "botes_spins_volcan",
    "botes_collects", "botes_double_collect", "botes_near_miss", "botes_ways_5",
    "botes_wild_wins", "botes_chips", "botes_turbo", "botes_night", "botes_win_max",
    "botes_bonuses", *(f"botes_bonus_{kind}" for kind in ("green", "blue", "red", "grand")),
    "botes_mini", "botes_major", "botes_grand", "botes_almost_grand", "botes_mult_max",
    "botes_bonus_max", "botes_mystery", "botes_instant", "botes_maximizer", "botes_auto",
    # Crash (cogs/crash.py: crash_stats) y Minas (cogs/mines.py: mines_stats)
    "crash_rounds", "crash_cashouts", "crash_cashout_max", "crash_win_max", "crash_auto",
    "crash_close", "crash_last_out", "crash_instant", "crash_greedy", "crash_moon",
    "crash_party_max",
    # Pachinko (cogs/pachinko.py: pachinko_stats, pachinko_autoplay_stats y el botón de Ráfaga)
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
    # cogs/chicken.py (`chicken_stats`)
    "chicken_games", "chicken_lanes", "chicken_auto_lanes", "chicken_auto_runs",
    "chicken_cashouts", "chicken_mult_max", "chicken_win_max", "chicken_hardcore_cashouts",
    "chicken_gallina", "chicken_close", "chicken_left_on_table", "chicken_road_free",
    "chicken_splats", "chicken_first_splat", "chicken_last_lane_splat", "chicken_lost_big",
    *(f"chicken_finish_{d.key}" for d in CHICKEN_DIFFICULTIES),
    *(f"chicken_lanes_max_{d.key}" for d in CHICKEN_DIFFICULTIES),
    *(f"chicken_hit_{kind}" for kind in CHICKEN_VEHICLE_KINDS),
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
    *(f"dice_total_{t}" for t in range(2, 13)),
    *(f"dice_point_made_{p}" for p in (4, 5, 6, 8, 9, 10)),
    *(f"dice_hard_{p}" for p in (4, 6, 8, 10)),
    # Trabajo (cogs/work.py: work_stats, el panel y los eventos; cogs/casino.py: el IMV)
    "work_shifts", "work_shifts_day_max", "work_streak_max", "work_perfect", "work_good",
    "work_night", "work_sunday", "work_birthday", "work_christmas", "work_reyes",
    "work_mayday", "work_black", "work_past_limit", "work_caught_inspeccion", "work_zombie",
    "work_accidents", "work_family_zero", "work_fee", "work_pipes", "work_slackers",
    "work_overruns", "work_perfect_orders", "work_perfect_votes", "work_happy_clients",
    "work_dodged", "work_no_recuerdo", "work_payslips", "work_taxes", "work_irpf_pct_max",
    "work_half_salary", "work_max_base", "work_partner",
    "work_job_changes", "work_coffees_day_max", "work_promotions", "work_declined",
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
    # cogs/fun.py: `hongkong`
    "hk_clock", "hk_clock_tomorrow", "hk_clock_sleeping", "hk_clock_lunch", "hk_clock_tour",
    "hk_clock_new_year",
    # Chat (cogs/achievements.py: message_delta, ChatTracker, laugh_reply_stats, ediciones)
    "msg_rae", "msg_exclaim", "msg_everyone", "msg_mass_ping", "msg_spoiler", "msg_code",
    "msg_emoji_heavy", "msg_only_emoji", "msg_stretch", "msg_long_word", "msg_palindrome",
    "msg_nice", "msg_canario", "msg_boricua", "msg_swear", "msg_mild_swear", "msg_thanks",
    "msg_sorry", "msg_good_morning", "msg_good_night", "msg_politics", "msg_falcon",
    "msg_fango", "msg_paguita", "msg_manual", "msg_hacienda", "msg_cuñado", "msg_ola_k_ase",
    "msg_bizum_ask", "msg_siesta", "msg_office", "msg_weekend", "msg_cinderella", "msg_reyes",
    "msg_valentin", "msg_pino", "msg_hispanidad", "msg_inocentes", "msg_friday13",
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
    "casino_bet_1", "casino_bet_69", "casino_bet_777",
    "crash_cash_low", "crash_missed_moon",
    "mines_cash_one", "mines_greedy", *(f"mines_level_{n}" for n in range(1, 13)),
    *(f"chicken_games_{d.key}" for d in CHICKEN_DIFFICULTIES),
    *(f"lottery_game_{g.key}" for g in LOTTERY_GAMES),
    "todo_done_batch_max",
    # patrimonio, fortunas y hacienda (cogs/patrimonio.py y cogs/casino.py)
    "patrimonio_views", "patrimonio_snoop", "fortunas_views", "net_worth_max",
    "fortunas_first", "fortunas_last", "patrimonio_illiquid", "hacienda_views",
    "hacienda_self", "hacienda_snoop", "hacienda_pillar", "hacienda_hidden",
    # Estadísticas del casino (cogs/apuestas.py: apuestas_stats)
    "apuestas_views", "apuestas_snoop", "apuestas_insomnia", "apuestas_virgin",
    "apuestas_denial", "apuestas_even", "apuestas_ruin", "apuestas_rich",
    *(f"apuestas_page_{page}" for page in APUESTAS_PAGES),
    *(f"apuestas_period_{period}" for period in APUESTAS_PERIODS),
    # Porras (cogs/porras.py; test_porras.py comprueba que cada una sale de verdad)
    *PORRA_STATS,
    *(f"{PORRA_GAME_PREFIX}{game}" for game in PORRA_GAMES),
    *(f"{PORRA_PROP_PREFIX}{prop}" for prop in PORRA_PROPS),
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
    # 25 opciones como mucho, y una es el Resumen.
    assert len(menu_entries()) <= 24
    keys = [key for key, _title in menu_entries()]
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
    # Un segundo desplegable lista las secciones (más la portada).
    assert len(sections) + 1 <= 25


def test_la_portada_del_casino_cabe_en_un_embed() -> None:
    embed = group_embed(CASINO_GROUP.key, "Diego", Profile(stats={}, unlocked={}))
    assert embed.description is not None
    assert len(embed.description) <= 4096
    assert "🐔 Pollo" in embed.description


def test_completista_pide_todos_los_logros_normales() -> None:
    normal = [a for a in AVAILABLE if a.category != "meta"]
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
    assert "Perro Sanxe" in embed.description
    # La retención cuenta para "Contribuyente" y compañía en la siguiente escritura.
    assert cog._pending[GUILD][USER].add["tax_paid"] == treasury.collected_total


async def test_la_primera_retencion_dispara_el_discurso_de_perro_sanxe(tmp_path: Path) -> None:
    """El gancho de la primera retención: un ingreso que supera el mínimo personal
    retiene IRPF, eso suma `tax_paid` y salta `tax_first` con su discurso, una vez."""
    cog, _repository, economy = await make_cog(tmp_path)
    channel = FakeChannel()
    small = await economy.pay_income(GUILD, USER, gross=1_000, concept="nivel:1")
    assert small.tax == 0  # por debajo del mínimo, Perro Sanxe no toca nada

    big = await economy.pay_income(GUILD, USER, gross=10_000, concept="nivel:10")
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
    # Dos escuchando: además de la llamada, cuenta como «cara a cara».
    assert first.add == {
        "voice_minutes": 2,
        "voice_muted": 2,
        "voice_stream": 2,
        "voice_duo": 2,
    }
    assert first.peak["voice_session_max"] == 2
    assert first.peak["voice_crowd_max"] == 2
    assert first.peak["voice_mute_streak_max"] == 2
    assert cog._pending[GUILD][2].add == {"voice_minutes": 2, "voice_duo": 2}
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


def test_aviso_de_muchos_logros_se_resume() -> None:
    ids = [a.id for a in AVAILABLE[:12]]
    embed = unlock_embed("Diego", None, ids, None)
    assert "12 logros" in (embed.author.name or "")
    assert "y 4 más" in (embed.description or "")


def test_aviso_de_un_logro_cabe_en_tres_lineas() -> None:
    achievement = next(a for a in AVAILABLE if not a.story and a.rarity is Rarity.COMMON)
    income = IncomeResult(gross=50, tax=0, rate=0.0, balance=1_050)
    embed = unlock_embed("Diego", None, [achievement.id], income)
    assert embed.author.name == "Diego · 🏆 ¡Logro desbloqueado!"
    assert embed.title is None
    lines = (embed.description or "").splitlines()
    assert lines == [
        f"{achievement.rarity.emoji} **{achievement.name}** · Común · 🪙 **+50 Y$**",
        f"-# {achievement.description} · 🐶 Perro Sanxe no te retiene nada: no llegas al mínimo.",
    ]


def test_aviso_con_retencion_dice_cuanto_se_lleva_hacienda() -> None:
    achievement = next(a for a in AVAILABLE if not a.story and a.rarity is Rarity.RARE)
    income = IncomeResult(gross=200, tax=38, rate=0.19, balance=5_162)
    description = unlock_embed("Diego", None, [achievement.id], income).description or ""
    assert "🪙 **+162 Y$**" in description.splitlines()[0]
    assert "se lleva 38 Y$ (19,00 % de 200 Y$)" in description.splitlines()[1]


def test_los_secretos_no_se_ven_hasta_conseguirlos() -> None:
    category = next(c for c in CATEGORIES if c.key == "time")
    hidden = category_embed(category, "Diego", Profile({}, {}), {}, 5)
    shown = category_embed(category, "Diego", Profile({}, {"leet": 1.0}), {"leet": 1}, 5)
    assert "1337" not in (hidden.description or "")
    assert "1337" in (shown.description or "")


def test_resumen_sugiere_los_logros_mas_cercanos() -> None:
    embed = summary_embed("Diego", None, Profile({"messages": 95}, {}), {}, 3)
    near = next(f for f in embed.fields if f.name == "Casi lo tienes")
    assert "Ya se te oye" in near.value


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
    assert delta.add["slots_pot_fed"] == 3


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
