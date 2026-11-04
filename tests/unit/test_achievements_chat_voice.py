"""Pruebas de los logros de chat, risas, voz, música, imágenes, babel y Coleccionista."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from bot.cogs.achievements import Achievements, group_embed
from bot.repositories.achievements import AchievementRepository, Profile
from bot.services.achievements import (
    AVAILABLE,
    BY_ID,
    CATALOG,
    CATEGORIES,
    CHAT_GROUP,
    IMG_EFFECTS_STAT,
    VOICE_GROUP,
    Achievement,
    ChatTracker,
    Rarity,
    analyze_laugh,
    babel_stats,
    group_sections,
    image_stats,
    is_keyboard_smash,
    is_laugh,
    is_laugh_emoji,
    laugh_reply_stats,
    message_delta,
    meta_stats,
    music_queue_stats,
    music_volume_stats,
    newly_unlocked,
    reaction_stats,
    voice_minute_stats,
    voice_move_stats,
    with_derived,
)
from bot.services.levels import TIMEZONE

GUILD = 1


def at(hour: int, minute: int = 0, *, month: int = 3, day: int = 10) -> datetime:
    """10 de marzo de 2026 (martes) a la hora indicada, en hora canaria."""
    return datetime(2026, month, day, hour, minute, tzinfo=TIMEZONE)


# -- Risas -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("jajaja", "es"),
        ("JAJAJAJ", "es"),
        ("jsjsjs", "es"),
        ("ajajajaj", "es"),
        ("jajsjajsj", "es"),
        ("jejeje", "es"),
        ("jijiji", "es"),
        ("jajá", "es"),
        ("ja ja ja", "es"),
        ("hahaha", "en"),
        ("ahahah", "en"),
        ("huehue", "en"),
        ("lol", "en"),
        ("loool", "en"),
        ("lmaooo", "en"),
        ("rofl", "en"),
        ("kekw", "en"),
        ("ha ha", "en"),
        ("xD", "xd"),
        ("xddd", "xd"),
        ("😂", "emoji"),
        ("🤣🤣", "emoji"),
        ("<:kekw:123456>", "emoji"),
        ("<a:pepelaugh:42>", "emoji"),
        ("💀", "skull"),
        ("ajsjsjsjd", "smash"),
        ("asdfghjklñ", "smash"),
        ("kkkkk", "intl"),
        ("rsrsrs", "intl"),
        ("mdr", "intl"),
        ("ptdr", "intl"),
        ("wwwww", "intl"),
        ("ㅋㅋㅋ", "intl"),
        ("哈哈哈", "intl"),
        ("хахаха", "intl"),
        ("me meo", "phrase"),
        ("me parto contigo", "phrase"),
        ("qué risa", "phrase"),
        ("me cago de risa", "phrase"),
        ("lloroooo", "phrase"),
    ],
)
def test_cada_forma_de_reirse_cuenta_con_su_tipo(text: str, kind: str) -> None:
    laugh = analyze_laugh(text)
    assert kind in laugh.kinds
    assert is_laugh(text)


@pytest.mark.parametrize(
    "text",
    [
        "ja",
        "jamón",
        "jeans",
        "hola",
        "José",
        "ajá",
        "jauja",
        "alaska",
        "falsas",
        "salsa",
        "www.google.com",
        "mira https://x.com/jajaja",
        "he comido",
        "me muero de hambre",
    ],
)
def test_palabras_que_no_son_risas(text: str) -> None:
    assert not is_laugh(text)


@pytest.mark.parametrize("text", ["ja", "ja.", "jaja.", "ja, ja.", "JE."])
def test_la_risa_seca_de_funcionario_no_es_risa(text: str) -> None:
    laugh = analyze_laugh(text)
    assert laugh.dry
    assert not laugh.laughed


def test_risa_larga_gritada_y_mezclada() -> None:
    laugh = analyze_laugh("JAJAJAJAJAJAJAJAJAJAJA lol 😂")
    assert laugh.kinds == {"es", "en", "emoji"}
    assert laugh.longest == 22
    assert laugh.shouted


def test_aporreo_del_teclado() -> None:
    assert is_keyboard_smash("ajsjsjs")
    assert is_keyboard_smash("aksjdhaksjd")
    assert not is_keyboard_smash("shhhhh")  # dos letras distintas
    assert not is_keyboard_smash("dallas")  # demasiadas aes


@pytest.mark.parametrize("emoji", ["😂", "🤣", "💀", "☠️", "kekw", "pepelaugh", "OMEGALUL"])
def test_reacciones_de_risa(emoji: str) -> None:
    assert is_laugh_emoji(emoji)


@pytest.mark.parametrize("emoji", ["👍", "❤️", "pepehands"])
def test_reacciones_que_no_son_de_risa(emoji: str) -> None:
    assert not is_laugh_emoji(emoji)


def test_un_mensaje_con_risa_suma_sus_tipos_y_maximos() -> None:
    delta = message_delta("jajajaja xd 💀", when=at(3))
    assert delta.add["msg_laughs"] == 1
    assert delta.add["laugh_es"] == 1
    assert delta.add["laugh_xd"] == 1
    assert delta.add["laugh_skull"] == 1
    assert delta.add["msg_xd"] == 1
    assert delta.add["laugh_night"] == 1
    assert delta.peak == {"laugh_len_max": 8, "laugh_kinds_max": 3}


def test_reirse_de_hacienda() -> None:
    assert message_delta("jajaja Hacienda otra vez", when=at(12)).add["laugh_sanxe"] == 1
    assert "laugh_sanxe" not in message_delta("Hacienda otra vez", when=at(12)).add


def test_politiglota_de_la_risa_pide_todos_los_tipos() -> None:
    kinds = {f"laugh_{kind}": 1 for kind in ("es", "en", "emoji", "skull", "smash", "intl")}
    assert "laugh_all_kinds" not in newly_unlocked(kinds, [])
    kinds |= {"laugh_phrase": 1, "laugh_xd": 1}
    assert "laugh_all_kinds" in newly_unlocked(kinds, [])


# -- Reírse de alguien -----------------------------------------------------------------


def test_reirse_respondiendo_da_credito_al_gracioso() -> None:
    out = laugh_reply_stats(author_id=1, replied_author_id=2, replied_is_bot=False)
    assert out[1].add == {"laugh_replies": 1}
    assert out[2].add == {"laughs_caused": 1}


def test_reirse_de_uno_mismo_y_del_bot() -> None:
    own = laugh_reply_stats(author_id=1, replied_author_id=1, replied_is_bot=False)
    assert own[1].add["laugh_self"] == 1
    assert list(own) == [1]
    bot = laugh_reply_stats(author_id=1, replied_author_id=999, replied_is_bot=True)
    assert bot[1].add["laugh_at_bot"] == 1
    assert 999 not in bot


# -- Estilo y lengua -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "stat"),
    [
        ("chacho, ¿cogemos la guagua?", "msg_canario"),
        ("ños, qué calor", "msg_canario"),
        ("papas arrugadas con mojo", "msg_canario"),
        ("wepa, bendito", "msg_boricua"),
        ("me cago en todo", "msg_swear"),
        ("joder", "msg_swear"),
        ("ostras, jolín", "msg_mild_swear"),
        ("muchas gracias", "msg_thanks"),
        ("perdón, me equivoqué", "msg_sorry"),
        ("buenos días", "msg_good_morning"),
        ("buenas nocheees", "msg_good_night"),
        ("el PSOE y el Congreso", "msg_politics"),
        ("ya despega el Falcon", "msg_falcon"),
        ("eso es un bulo", "msg_fango"),
        ("el franquismo y el NO-DO", "msg_franco"),
        ("todo atado y bien atado", "msg_franco"),
        ("Trump otra vez con aranceles", "msg_trump"),
        ("Putin y su mesa", "msg_putin"),
        ("Kim Jong-un lanza otro misil", "msg_kim"),
        ("Xi Jinping lo ve todo", "msg_xi"),
        ("Netanyahu sigue sin juicio", "msg_netanyahu"),
        ("yes we can, Obama", "msg_obama"),
        ("los correos de Hillary Clinton", "msg_clinton"),
        ("Elon Musk a Marte", "msg_musk"),
        ("Milei con la motosierra", "msg_milei"),
        ("¿qué? ¡toma mangó!", "msg_mango"),
        ("hazme café, quiero café", "msg_cafe"),
        ("¿con pulpa? ¡sin pulpa!", "msg_pulpa"),
        ("móntate en mi motora", "msg_motora"),
        ("¿y mi paguita?", "msg_paguita"),
        ("léete el Manual de resistencia", "msg_manual"),
        ("Hacienda somos todos", "msg_hacienda"),
        ("eso lo arreglaba yo en dos días", "msg_cuñado"),
        ("ola k ase", "msg_ola_k_ase"),
        ("hazme un bizum", "msg_bizum_ask"),
        ("¿vienes?", "msg_rae"),
        ("¡¡¡vamos!!!", "msg_exclaim"),
        ("el final es ||que muere||", "msg_spoiler"),
        ("usa `print()`", "msg_code"),
        ("😀😃😄😁😆", "msg_emoji_heavy"),
        ("🗿", "msg_only_emoji"),
        ("👍🏽", "msg_only_emoji"),
        ("holaaaaaaa", "msg_stretch"),
        ("esternocleidomastoideos", "msg_long_word"),
        ("Dábale arroz a la zorra el abad", "msg_palindrome"),
        ("tengo 69 Y$", "msg_nice"),
    ],
)
def test_lo_que_se_dice_cuenta(text: str, stat: str) -> None:
    assert message_delta(text, when=at(12)).add.get(stat) == 1


@pytest.mark.parametrize(
    ("text", "stat"),
    [
        ("nos vemos", "msg_canario"),  # «nos» no es «ños»
        ("podemos quedar", "msg_politics"),  # el verbo, no el partido
        ("te lo digo franco", "msg_franco"),  # el adjetivo, no el dictador
        ("un mango maduro", "msg_mango"),
        ("quiero un café con leche", "msg_cafe"),
        ("mi moto", "msg_motora"),
        ("kimono nuevo", "msg_kim"),
        ("tengo 690 Y$", "msg_nice"),
        ("jajajaja", "msg_palindrome"),  # palíndromo de dos letras: no vale
        ("hola 🗿", "msg_only_emoji"),
    ],
)
def test_lo_que_no_cuenta(text: str, stat: str) -> None:
    assert stat not in message_delta(text, when=at(12)).add


def test_menciones_masivas_y_everyone() -> None:
    delta = message_delta("venid", when=at(12), mention_everyone=True, people_mentioned=5)
    assert delta.add["msg_everyone"] == 1
    assert delta.add["msg_mass_ping"] == 1
    assert "msg_mass_ping" not in message_delta("hola", when=at(12), people_mentioned=4).add


@pytest.mark.parametrize(
    ("when", "stat"),
    [
        (at(16), "msg_siesta"),
        (at(10), "msg_office"),  # martes por la mañana
        (datetime(2026, 3, 14, 12, tzinfo=TIMEZONE), "msg_weekend"),  # sábado
        (at(0, 0), "msg_cinderella"),
        (at(12, month=1, day=6), "msg_reyes"),
        (at(12, month=2, day=14), "msg_valentin"),
        (at(12, month=9, day=8), "msg_pino"),
        (at(12, month=10, day=12), "msg_hispanidad"),
        (at(12, month=12, day=28), "msg_inocentes"),
        (datetime(2026, 2, 13, 12, tzinfo=TIMEZONE), "msg_friday13"),
    ],
)
def test_fechas_nuevas(when: datetime, stat: str) -> None:
    assert message_delta("hola", when=when).add.get(stat) == 1


# -- Conversación ----------------------------------------------------------------------


def observe(tracker: ChatTracker, author: int, *, t: float, text: str = "hola", **kw: object):
    local = datetime.fromtimestamp(t, TIMEZONE)
    return tracker.observe(
        GUILD,
        kw.pop("channel", 100),  # type: ignore[arg-type]
        author,
        at=t,
        local=local,
        content=text,
        laughed=bool(kw.pop("laughed", False)),
    )


T0 = at(10).timestamp()


def test_monologo_y_mensajes_del_dia() -> None:
    tracker = ChatTracker()
    for i in range(4):
        out = observe(tracker, 1, t=T0 + i)
    assert out[1].peak["msg_monologue_max"] == 4
    assert out[1].peak["msg_day_max"] == 4
    out = observe(tracker, 2, t=T0 + 10)
    assert "msg_monologue_max" not in out[2].peak


def test_abre_la_persiana_una_vez_al_dia() -> None:
    tracker = ChatTracker()
    assert observe(tracker, 1, t=T0)[1].add["msg_first_of_day"] == 1
    assert "msg_first_of_day" not in observe(tracker, 2, t=T0 + 5)[2].add
    # Antes de las 6:00 del día siguiente aún no abre nadie.
    early = at(5, month=3, day=11).timestamp()
    assert "msg_first_of_day" not in observe(tracker, 2, t=early)[2].add
    later = at(7, month=3, day=11).timestamp()
    assert observe(tracker, 3, t=later)[3].add["msg_first_of_day"] == 1


def test_eco_de_bancada_y_nigromante() -> None:
    tracker = ChatTracker()
    observe(tracker, 1, t=T0, text="Viva Canarias")
    assert observe(tracker, 2, t=T0 + 1, text="viva canarias ")[2].add["msg_echo"] == 1
    later = T0 + 25 * 3600
    assert observe(tracker, 3, t=later)[3].add["msg_necro"] == 1


def test_cadena_de_risas_suma_a_todos_los_que_se_rien() -> None:
    tracker = ChatTracker()
    observe(tracker, 1, t=T0, laughed=True)
    observe(tracker, 2, t=T0 + 1, laughed=True)
    out = observe(tracker, 3, t=T0 + 2, laughed=True)
    assert {user: d.peak["laugh_chain_max"] for user, d in out.items()} == {1: 3, 2: 3, 3: 3}
    # Un mensaje sin risa corta la cadena.
    observe(tracker, 4, t=T0 + 3)
    out = observe(tracker, 1, t=T0 + 4, laughed=True)
    assert "laugh_chain_max" not in out[1].peak


def test_la_memoria_de_canales_esta_acotada() -> None:
    tracker = ChatTracker()
    for channel in range(600):
        observe(tracker, 1, t=T0 + channel, channel=channel)
    assert len(tracker._channels) == 500


# -- Voz -------------------------------------------------------------------------------


def test_entrar_cambiar_irse_rapido_y_compartir_pantalla() -> None:
    def move(before: int | None, after: int | None, **kw: object) -> dict[str, int]:
        return voice_move_stats(
            before_channel=before,
            after_channel=after,
            started_stream=bool(kw.get("stream", False)),
            joined_at=kw.get("joined_at"),  # type: ignore[arg-type]
            now=100.0,
        ).add

    assert move(None, 1) == {"voice_joins": 1}
    assert move(1, 2) == {"voice_hops": 1}
    assert move(1, 1, stream=True) == {"voice_stream_starts": 1}
    assert move(1, None, joined_at=90.0) == {"voice_ghost": 1}
    assert move(1, None, joined_at=10.0) == {}


def voice_member(user_id: int, **flags: bool) -> SimpleNamespace:
    state = {
        "self_mute": False,
        "self_deaf": False,
        "mute": False,
        "deaf": False,
        "self_stream": False,
        "self_video": False,
        **flags,
    }
    return SimpleNamespace(id=user_id, bot=False, voice=SimpleNamespace(**state))


async def make_cog(tmp_path: Path, *guilds: object) -> Achievements:
    repository = AchievementRepository(tmp_path / "bot.db")
    await repository.initialize()
    bot = MagicMock()
    bot.guilds = list(guilds)
    by_id = {g.id: g for g in guilds}  # type: ignore[attr-defined]
    bot.get_guild = lambda guild_id: by_id.get(guild_id)
    bot.get_cog = lambda _name: None
    bot.user = SimpleNamespace(id=999)
    return Achievements(bot, repository)


async def test_voz_cuenta_multitarea_cine_y_mordaza(tmp_path: Path) -> None:
    channel = SimpleNamespace(
        id=70,
        members=[
            voice_member(1, self_stream=True, self_video=True),
            voice_member(2, mute=True),
            *(voice_member(i) for i in range(3, 6)),
        ],
    )
    guild = SimpleNamespace(id=GUILD, voice_channels=[channel], afk_channel=None)
    cog = await make_cog(tmp_path, guild)
    sunday_siesta = datetime(2026, 3, 15, 16, tzinfo=TIMEZONE).timestamp()
    cog.collect_voice(sunday_siesta)

    streamer = cog._pending[GUILD][1].add
    assert streamer["voice_multitask"] == 1
    assert streamer["voice_stream_crowd"] == 1
    assert streamer["voice_siesta"] == 1
    assert streamer["voice_weekend"] == 1
    assert "voice_duo" not in streamer
    assert cog._pending[GUILD][2].add["voice_server_muted"] == 1


async def test_la_racha_de_silencio_se_corta_al_hablar(tmp_path: Path) -> None:
    quiet = voice_member(1, self_mute=True)
    channel = SimpleNamespace(id=70, members=[quiet, voice_member(2)])
    guild = SimpleNamespace(id=GUILD, voice_channels=[channel], afk_channel=None)
    cog = await make_cog(tmp_path, guild)
    noon = at(12).timestamp()
    cog.collect_voice(noon)
    cog.collect_voice(noon + 60)
    assert cog._mute_streaks[(GUILD, 1)] == 2
    quiet.voice.self_mute = False
    cog.collect_voice(noon + 120)
    assert (GUILD, 1) not in cog._mute_streaks


async def test_voz_suma_entradas_y_huidas(tmp_path: Path) -> None:
    guild = SimpleNamespace(id=GUILD)
    cog = await make_cog(tmp_path, guild)
    clock = iter([0.0, 0.0, 5.0])
    cog._clock = lambda: next(clock)
    member = SimpleNamespace(id=7, bot=False, guild=guild)
    out = SimpleNamespace(channel=None, self_stream=False)
    inside = SimpleNamespace(channel=SimpleNamespace(id=70, members=[]), self_stream=False)

    await cog.on_voice_state_update(member, out, inside)  # type: ignore[arg-type]
    await cog.on_voice_state_update(member, inside, out)  # type: ignore[arg-type]

    assert cog._pending[GUILD][7].add == {"voice_joins": 1, "voice_ghost": 1}


# -- Mensajes y reacciones en el cog --------------------------------------------------


def fake_message(
    author_id: int,
    content: str,
    *,
    channel_id: int = 100,
    replied: object = None,
) -> SimpleNamespace:
    reference = SimpleNamespace(resolved=replied) if replied is not None else None
    return SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        author=SimpleNamespace(id=author_id, bot=False),
        webhook_id=None,
        is_system=lambda: False,
        created_at=at(12),
        reference=reference,
        type=discord.MessageType.reply if replied is not None else discord.MessageType.default,
        mentions=[],
        mention_everyone=False,
        attachments=[],
        stickers=[],
        content=content,
        channel=SimpleNamespace(id=channel_id),
        flags=SimpleNamespace(voice=False),
    )


async def test_reirse_respondiendo_suma_al_gracioso(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    joke = SimpleNamespace(author=SimpleNamespace(id=2, bot=False))
    await cog.on_message(fake_message(1, "JAJAJAJA", replied=joke))  # type: ignore[arg-type]
    assert cog._pending[GUILD][1].add["laugh_replies"] == 1
    assert cog._pending[GUILD][2].add["laughs_caused"] == 1


async def test_reir_con_racha_de_derrotas_es_reir_por_no_llorar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog._casino_streaks[(GUILD, 1)] = -5
    await cog.on_message(fake_message(1, "jajaja me arruiné"))  # type: ignore[arg-type]
    assert cog._pending[GUILD][1].add["laugh_losing"] == 1


async def test_editar_un_mensaje_cuenta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    before = fake_message(1, "hola")
    after = fake_message(1, "hola!!")
    await cog.on_message_edit(before, after)  # type: ignore[arg-type]
    await cog.on_message_edit(after, after)  # type: ignore[arg-type]
    assert cog._pending[GUILD][1].add == {"msg_edits": 1}


async def test_reacciones_de_risa_una_por_persona(tmp_path: Path) -> None:
    guild = SimpleNamespace(id=GUILD, get_member=lambda uid: SimpleNamespace(id=uid, bot=False))
    cog = await make_cog(tmp_path, guild)

    def react(user_id: int, emoji: str) -> SimpleNamespace:
        return SimpleNamespace(
            guild_id=GUILD,
            message_id=7,
            user_id=user_id,
            message_author_id=1,
            channel_id=50,
            member=SimpleNamespace(id=user_id, bot=False),
            emoji=SimpleNamespace(name=emoji, id=None),
        )

    for user_id, emoji in ((2, "😂"), (2, "💀"), (3, "kekw"), (4, "👍")):
        await cog.on_raw_reaction_add(react(user_id, emoji))  # type: ignore[arg-type]

    author = cog._pending[GUILD][1]
    assert author.add["laugh_reacts_received"] == 2
    assert author.peak["laugh_reacts_on_message_max"] == 2
    assert cog._pending[GUILD][2].add["laugh_reacts_given"] == 1
    assert "laugh_reacts_given" not in cog._pending[GUILD][4].add


# -- Música, imágenes y babel ---------------------------------------------------------


def test_poner_musica_y_sus_secretos() -> None:
    delta = music_queue_stats(
        query="pedro pedro pedro", title="Pedro (Remix)", duration_seconds=1_500, position=11
    )
    assert delta.add == {"music_queued": 1, "music_pedro": 1}
    assert delta.peak == {"music_track_max": 25, "music_queue_max": 11}
    assert (
        "music_jovani"
        in music_queue_stats(query="jovani vázquez", title="x", duration_seconds=60, position=1).add
    )


def test_volumen_de_la_musica() -> None:
    assert music_volume_stats(200).peak["music_volume_max"] == 200
    assert music_volume_stats(5).add["music_whisper"] == 1
    assert "music_whisper" not in music_volume_stats(50).add


def test_imagenes_cuentan_efectos_distintos() -> None:
    first = image_stats("magik", "png", subject_is_author=True)
    assert first.add == {"img_made": 1, "img_fx_magik": 1, "img_magik": 1, "img_self": 1}
    other = image_stats("ataud", "mp4", subject_is_author=False)
    assert other.add["img_video"] == 1
    assert other.add["img_on_others"] == 1
    stats = {"img_fx_magik": 3, "img_fx_ataud": 1, "img_made": 4}
    assert with_derived(stats)[IMG_EFFECTS_STAT] == 2


def test_babel_frase_nombres_y_vueltas() -> None:
    assert babel_stats(completed=True, lost=False).add == {"babel_phrases": 1, "babel_full": 1}
    names = babel_stats(renamed_members=2, renamed_channels=1, completed=False, lost=True)
    assert names.add == {"babel_renames": 2, "babel_channels": 1, "babel_lost": 1}


# -- Coleccionista ---------------------------------------------------------------------


def test_meta_stats_cuenta_rarezas_secretos_y_categorias() -> None:
    ids = ["chat_1", "chat_100", "leet", "laugh_1"]
    stats = meta_stats(ids)
    assert stats["achievements"] == 4
    assert stats["achievements_secret"] == 1
    assert stats["achievements_rare"] == 1  # «1337»
    assert stats["achievements_categories"] == 3  # chat, time y laughs
    assert stats["achievement_points"] == sum(BY_ID[i].rarity.points for i in ids)


def test_completar_una_categoria() -> None:
    todo = [a.id for a in AVAILABLE if a.category == "todo"]
    assert meta_stats(todo)["achievements_categories_done"] == 1
    assert meta_stats(todo[:-1])["achievements_categories_done"] == 0


def test_combo_al_desbloquear_varios_de_golpe() -> None:
    new = newly_unlocked({"messages": 100, "msg_laughs": 10}, [])
    assert {"chat_1", "chat_100", "laugh_1", "laugh_10"} <= set(new)
    assert "meta_combo_3" in new


def test_logro_de_logros_de_logros_salta_con_el_ultimo_de_coleccionista() -> None:
    metas = [a.id for a in AVAILABLE if a.category == "meta" and a.id != "metacompletionist"]
    assert "metacompletionist" in newly_unlocked({}, metas)
    assert "metacompletionist" not in newly_unlocked({}, metas[:-1])


# -- Menú ------------------------------------------------------------------------------


def test_chat_y_voz_son_grupos_con_secciones() -> None:
    chat = {c.key for c in group_sections(CHAT_GROUP.key)}
    voice = {c.key for c in group_sections(VOICE_GROUP.key)}
    assert {
        "chat", "style", "laughs", "funny", "lengua", "convo", "time", "memes", "format",
        "topics", "personajes",
    } == chat  # fmt: skip
    assert {
        "voice", "voice_mic", "voice_moves", "entrance", "music", "voice_games", "voice_chat",
    } == voice  # fmt: skip
    for group in (CHAT_GROUP, VOICE_GROUP):
        embed = group_embed(group.key, "Diego", Profile(stats={}, unlocked={}))
        assert embed.description is not None
        assert len(embed.description) <= 4096


def test_no_hay_categorias_vacias() -> None:
    for category in CATEGORIES:
        assert any(a.category == category.key for a in AVAILABLE), category.key


def test_la_rareza_no_baja_al_subir_la_meta() -> None:
    """Dentro de una estadística, un escalón más alto nunca es más común."""
    order = list(Rarity)
    by_stat: dict[str, list[Achievement]] = {}
    for achievement in AVAILABLE:
        if len(achievement.conditions) == 1:
            by_stat.setdefault(achievement.stat, []).append(achievement)
    for tiers in by_stat.values():
        ranks = [order.index(a.rarity) for a in tiers]
        assert ranks == sorted(ranks), [a.id for a in tiers]


def test_las_categorias_largas_se_parten_en_paginas_sin_perder_logros() -> None:
    from bot.cogs.achievements import category_embed, category_page_count

    profile = Profile(stats={}, unlocked={})
    category = next(c for c in CATEGORIES if c.key == "slots")
    pages = category_page_count(category, profile, {}, 10)
    assert pages >= 2
    text = "".join(
        category_embed(category, "Diego", profile, {}, 10, page).description or ""
        for page in range(pages)
    )
    visible = [a for a in AVAILABLE if a.category == "slots" and not a.secret]
    assert all(a.name in text for a in visible)
    last = category_embed(category, "Diego", profile, {}, 10, 99)
    assert f"Página {pages}/{pages}" in (last.footer.text or "")


# -- Segunda tanda: más logros del casino y del resto ----------------------------------


def _roulette(pocket: int, *bets: str):
    from bot.services import roulette

    wheel = roulette.Wheel(lambda _n: roulette.POCKETS.index(pocket), lightning=False)
    wagers = [roulette.Wager(roulette.parse_bet(bet), 100) for bet in bets]
    return roulette.play_round(wheel, wagers)


def test_ruleta_cuenta_plenos_por_numero_y_victorias_pirricas() -> None:
    from bot.services.achievements import roulette_stats

    outcome = _roulette(13, "13", "rojo", "negro", "1-18", "docena2")
    delta = roulette_stats(outcome, table_streak=1, previous_pocket=None)
    assert delta.add["roulette_hit_13"] == 1
    assert delta.add["roulette_half_wins"] == 1  # 1-18
    assert delta.add["roulette_dozen_wins"] == 1
    assert delta.peak["roulette_cover_max"] == 36
    stats = with_derived({"roulette_hit_13": 2, "roulette_hit_7": 1})
    assert stats["roulette_numbers_hit"] == 2
    assert stats["roulette_hit_max"] == 2

    pyrrhic = roulette_stats(
        _roulette(1, "rojo", "19-36", "docena3"), table_streak=0, previous_pocket=None
    )
    assert pyrrhic.add["roulette_pyrrhic"] == 1


def test_ruleta_el_cero_barre_la_mesa() -> None:
    from bot.services.achievements import roulette_stats

    delta = roulette_stats(_roulette(0, "rojo", "negro"), table_streak=0, previous_pocket=None)
    assert delta.add["roulette_zero_sweep"] == 1


def _blackjack(player: list[tuple[int, int]], dealer: list[tuple[int, int]], hits: int = 0):
    from bot.services import blackjack as bj

    # Reparto: jugador, banca, jugador, banca; luego las cartas que se pidan
    # y al final las de la banca. El mazo se roba desde el final.
    order = [player[0], dealer[0], player[1], dealer[1], *player[2:], *dealer[2:]]
    shoe = [bj.Card(rank, suit) for rank, suit in reversed(order)]
    game = bj.BlackjackGame(100, shoe=shoe)
    game.deal()
    for _ in range(hits):
        if game.player_turn:
            game.act(bj.Action.HIT)
    while game.player_turn:
        game.act(bj.Action.STAND)
    game.reveal_hole()
    while game.dealer_should_draw():
        game.dealer_draw()
    game.settle()
    return game


def test_blackjack_tres_sietes_y_blackjack_del_mismo_palo() -> None:
    from bot.services.achievements import blackjack_stats

    sevens = blackjack_stats(_blackjack([(7, 0), (7, 1), (7, 2)], [(10, 0), (7, 0)], hits=1))
    assert sevens.add["bj_triple_seven"] == 1
    suited = blackjack_stats(_blackjack([(1, 2), (13, 2)], [(9, 0), (8, 0)]))
    assert suited.add["bj_suited_natural"] == 1


def test_blackjack_plantarse_con_once_y_la_banca_que_se_lo_curra() -> None:
    from bot.services.achievements import blackjack_stats

    game = _blackjack([(5, 0), (6, 0)], [(2, 0), (2, 1), (2, 2), (3, 0), (10, 0)])
    delta = blackjack_stats(game)
    assert delta.add["bj_stand_low"] == 1
    assert delta.add["bj_dealer_five"] == 1


def test_minas_cuenta_niveles_cobrar_con_uno_y_la_avaricia() -> None:
    import random

    from bot.services import mines
    from bot.services.achievements import mines_stats

    game = mines.MinesGame.new(100, 3, random.Random(1))
    game.reveal(game.random_hidden(random.Random(2)))
    # «Con uno» es el primer diamante con riesgo: la primera casilla es segura.
    game.reveal(next(t for t in game.hidden if t not in game.mine_tiles))
    game.cash_out()
    delta = mines_stats(game)
    assert delta.add["mines_level_3"] == 1
    assert delta.add["mines_cash_one"] == 1


def test_minas_retirarse_en_la_casilla_segura_es_una_devolucion() -> None:
    """All-in, la primera casilla (siempre buena) y cobrar: no arriesga nada, no es jugada."""
    import random

    from bot.services import mines

    game = mines.MinesGame.new(5_000, 2, random.Random(1))
    game.reveal(0)
    assert game.cash_out() == 5_000
    assert game.net == 0
    assert game.risk_free

    risked = mines.MinesGame.new(5_000, 2, random.Random(1))
    risked.reveal(0)
    risked.reveal(next(t for t in risked.hidden if t not in risked.mine_tiles))
    risked.cash_out()
    assert not risked.risk_free


def test_crash_cobarde_y_cohete_perdido() -> None:
    from bot.services.achievements import crash_stats

    seat = SimpleNamespace(cashed_cents=105, by_auto=False, net=5)
    delta = crash_stats(seat, crash_cents=12_000, players=1, last_out=False)  # type: ignore[arg-type]
    assert delta.add["crash_cash_low"] == 1
    assert delta.add["crash_missed_moon"] == 1


def test_apuestas_secretas_del_casino() -> None:
    from bot.services.achievements import casino_stats

    for stake in (1, 69, 777):
        assert (
            casino_stats(stake=stake, net=-stake, balance_after=1_000).add[f"casino_bet_{stake}"]
            == 1
        )


def test_loterias_cuentan_por_juego() -> None:
    from bot.services.achievements import lottery_buy_stats

    delta = lottery_buy_stats(game="bonoloto", units=3, cost=15, owned_in_draw=3, balance_after=100)
    assert delta.add["lottery_game_bonoloto"] == 3


def test_no_hay_dos_logros_con_el_mismo_nombre() -> None:
    names = [a.name for a in CATALOG]
    assert len(names) == len(set(names))


def test_ruleta_cuenta_rayos_casis_y_corazonadas() -> None:
    from bot.services import roulette
    from bot.services.achievements import roulette_stats

    class Tormenta(roulette.Wheel):
        def strike(self) -> dict[int, int]:
            return {1: 500, 13: 50, 0: 100, 5: 50, 9: 50}

    def play(pocket: int, *bets: str):
        wheel = Tormenta(lambda _n: roulette.POCKETS.index(pocket))
        wagers = [roulette.Wager(roulette.parse_bet(bet), 10) for bet in bets]
        return roulette.play_round(wheel, wagers)

    won = roulette_stats(
        play(1, "1"), table_streak=1, previous_pocket=None, hunches={"in:1": "hot"}
    )
    assert won.add["roulette_lucky_wins"] == 1
    assert won.peak["roulette_lucky_max"] == 500
    assert won.peak["roulette_storm_max"] == 5
    assert won.add["roulette_hot_bets"] == 1
    assert won.add["roulette_hot_hits"] == 1

    # En la rueda: 13, 1, 00. El 13 tenía rayo, está al lado y no sale.
    missed = roulette_stats(
        play(1, "13"), table_streak=0, previous_pocket=None, hunches={"in:13": "cold"}
    )
    assert missed.add["roulette_lucky_missed"] == 1
    assert missed.add["roulette_near_miss"] == 1
    assert missed.add["roulette_cold_bets"] == 1
    assert "roulette_cold_hits" not in missed.add
    assert "roulette_lucky_wins" not in missed.add

    green = roulette_stats(play(0, "0"), table_streak=1, previous_pocket=None)
    assert green.add["roulette_lucky_zero"] == 1


# -- Chat y voz, segunda tanda ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "stat"),
    [
        ("mira https://youtu.be/abc", "msg_youtube"),
        ("https://www.tiktok.com/@x/video/1", "msg_tiktok"),
        ("https://x.com/user/status/1", "msg_twitter"),
        ("https://es.wikipedia.org/wiki/Gofio", "msg_wikipedia"),
        ("https://tenor.com/view/gato", "msg_gif"),
        ("la pizza con piña es un crimen", "msg_pineapple"),
        ("tortilla sin cebolla, por favor", "msg_tortilla"),
        ("eso lo dice un terraplanista", "msg_flat_earth"),
        ("se lo he preguntado a ChatGPT", "msg_chatgpt"),
        ("lo compré en el Mercadona", "msg_mercadona"),
        ("los reptilianos gobiernan", "msg_conspiracy"),
        ("es culpa de Mercurio retrógrado", "msg_horoscope"),
        ("bitcoin to the moon", "msg_crypto"),
        ("hoy toca día de pierna", "msg_gym"),
        ("el lunes empiezo la dieta", "msg_diet"),
        ("me ha escrito mi ex", "msg_ex"),
        ("vaya resaca llevo", "msg_hangover"),
        ("este año sí, la 33", "msg_alonso"),
        ("ok boomer", "msg_boomer"),
        ("qué calima hay hoy", "msg_calima"),
        ("12345", "msg_only_digits"),
        ("???", "msg_only_question"),
        ("...", "msg_dots"),
        ("brrrrr pfff", "msg_no_vowels"),
        ("k", "msg_len_1"),
        ("x" * 69, "msg_len_69"),
    ],
)
def test_lo_nuevo_que_se_dice_cuenta(text: str, stat: str) -> None:
    assert message_delta(text, when=at(12)).add.get(stat) == 1


@pytest.mark.parametrize(
    ("text", "stat"),
    [
        ("mañana tengo examen, mi examen es duro", "msg_ex"),  # «mi examen» no es la ex
        ("me voy al cine", "msg_crypto"),
        ("la criptografía es difícil", "msg_crypto"),
        ("piña colada", "msg_pineapple"),
        ("jsjsjsjsjs", "msg_no_vowels"),  # es una risa
        ("xdddddd", "msg_no_vowels"),
        ("ok", "msg_no_vowels"),
        ("33 euros", "msg_alonso"),
        ("https://youtube.example.com", "msg_youtube"),
        ("🗿", "msg_len_1"),
        ("12a", "msg_only_digits"),
        ("¿qué?", "msg_only_question"),
    ],
)
def test_lo_nuevo_que_no_cuenta(text: str, stat: str) -> None:
    assert stat not in message_delta(text, when=at(12)).add


def test_adjuntos_por_tipo_y_desde_la_llamada() -> None:
    files = [("image/png", "Captura de pantalla.png", False)] * 4 + [("video/mp4", "a.mp4", True)]
    delta = message_delta(
        "jaja mira", when=at(12), files=files, poll=True, voice=(True, True, False)
    )
    for stat in (
        "msg_images", "msg_videos", "msg_album", "msg_screenshot", "msg_image_spoiler",
        "msg_poll", "msg_in_voice", "msg_voice_text", "msg_while_streaming", "laugh_in_voice",
    ):  # fmt: skip
        assert delta.add.get(stat) == 1, stat
    assert "msg_on_camera" not in delta.add
    note = message_delta(
        "", when=at(12), files=[("audio/ogg", "voice.ogg", False)], voice_note=True
    )
    assert note.add.get("msg_voice_note") == 1
    assert "msg_audio_files" not in note.add
    assert "msg_in_voice" not in message_delta("hola", when=at(12)).add


def test_las_horas_con_guasa() -> None:
    assert message_delta("ya", when=at(11, 11)).add.get("msg_1111") == 1
    assert message_delta("ya", when=at(16, 20)).add.get("msg_420") == 1
    assert message_delta("ya", when=at(22, 22)).add.get("msg_2222") == 1
    assert message_delta("ya", when=at(15, 14)).add.get("msg_pi") == 1
    assert message_delta("ya", when=at(23, 59)).add.get("msg_2359") == 1
    assert "msg_420" not in message_delta("ya", when=at(16, 21)).add


def test_ping_pong_respuestas_rapidas_y_silencios() -> None:
    tracker = ChatTracker()

    def say(author: int, second: float) -> dict:
        return tracker.observe(
            GUILD, 9, author, at=second, local=at(12), content=f"m{second}", laughed=False
        )

    say(1, 0)
    quick = say(2, 3)
    assert quick[2].add.get("msg_quick") == 1
    say(1, 30)
    rally = say(2, 60)
    assert rally[1].peak["msg_rally_max"] == 4
    assert rally[2].peak["msg_rally_max"] == 4
    assert "msg_rally_max" not in say(3, 90)[3].peak  # entra un tercero: se acaba el peloteo
    assert "msg_rally_max" not in say(3, 120).get(3).peak  # el mismo autor corta la racha
    silence = say(1, 120 + 3600)
    assert silence[1].add.get("msg_silence_break") == 1
    assert "msg_necro" not in silence[1].add


def test_reacciones_con_emoji_propio_primero_y_corazon() -> None:
    given, received = reaction_stats("❤️", custom=False, first=True)
    assert given.add == {"reactions_first": 1, "react_heart": 1}
    assert received.add == {"reacted_heart": 1}
    given, received = reaction_stats("kekw", custom=True, first=False)
    assert given.add == {"reactions_custom": 1}
    assert not received


def test_voz_por_minuto_con_quien_hora_y_juegos() -> None:
    minute = voice_minute_stats(
        datetime(2026, 3, 10, 13, 37, tzinfo=TIMEZONE),
        others=[5, 6],
        games=["Minecraft", "Juego raro"],
        spotify=True,
    )
    assert minute == {
        "voice_day_20260310": 1,
        "voice_with_5": 1,
        "voice_with_6": 1,
        "voice_leet": 1,
        "voice_gaming": 1,
        "voice_game_minecraft": 1,
        "voice_spotify": 1,
    }


def test_racha_de_voz_personas_y_companero_salen_de_los_contadores() -> None:
    full = with_derived({
        "voice_day_20261230": 5, "voice_day_20261231": 1, "voice_day_20270101": 2,
        "voice_day_20270103": 1, "voice_with_5": 600, "voice_with_6": 30,
        "voice_game_minecraft": 3, "react_moai": 1, "msg_youtube": 2, "msg_gym": 1,
    })  # fmt: skip
    assert full["voice_streak_max"] == 3  # cruza el cambio de año
    assert full["voice_people"] == 2
    assert full["voice_bestie_max"] == 600
    assert full["voice_games"] == 1
    assert full["react_kinds"] == 1
    assert full["msg_link_kinds"] == 1
    assert full["msg_topic_kinds"] == 1


async def test_abrir_una_sala_que_se_llena_y_ser_el_ultimo_en_salir(tmp_path: Path) -> None:
    guild = SimpleNamespace(id=GUILD)
    cog = await make_cog(tmp_path, guild)
    cog._clock = lambda: 1_000.0
    room = SimpleNamespace(id=70, members=[])
    out = SimpleNamespace(channel=None, self_stream=False)
    inside = SimpleNamespace(channel=room, self_stream=False)
    people = [SimpleNamespace(id=i, bot=False, guild=guild) for i in range(1, 6)]
    for person in people:
        room.members.append(person)
        await cog.on_voice_state_update(person, out, inside)  # type: ignore[arg-type]
    assert cog._pending[GUILD][1].add.get("voice_host") == 1
    for person in people[:-1]:
        room.members.remove(person)
        await cog.on_voice_state_update(person, inside, out)  # type: ignore[arg-type]
    last = people[-1]
    cog._sessions[(GUILD, last.id)] = 3
    room.members.remove(last)
    await cog.on_voice_state_update(last, inside, out)  # type: ignore[arg-type]
    assert cog._pending[GUILD][5].add.get("voice_last_out") == 1
    assert "voice_last_out" not in cog._pending[GUILD][4].add
