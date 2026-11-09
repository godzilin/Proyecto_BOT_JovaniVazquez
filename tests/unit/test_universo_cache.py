"""La caché por bytes del mundo publicado (`bot.cogs.universo.mundo.ByteCache`)."""

from __future__ import annotations

from bot.cogs.universo.mundo import ByteCache


def test_la_cache_no_pasa_de_su_tope_y_suelta_lo_mas_viejo() -> None:
    cache = ByteCache(10)
    cache.put("a", "1234")
    cache.put("b", b"5678")
    assert (cache.bytes, len(cache)) == (8, 2)
    cache.get("a")  # a es ahora la más reciente
    cache.put("c", "ab")  # justo en el tope
    assert cache.bytes == 10
    cache.put("d", "xyz")  # no cabe: se va b, la menos usada
    assert (cache.get("b"), cache.get("a"), cache.get("c"), cache.get("d")) == (
        None,
        "1234",
        "ab",
        "xyz",
    )
    assert cache.bytes == 9 and len(cache) == 3
    # Cabía a costa de soltar varias.
    cache.put("e", "0123456789")
    assert (cache.bytes, len(cache)) == (10, 1)


def test_reponer_una_clave_cuenta_una_vez_y_lo_que_no_cabe_no_se_guarda() -> None:
    cache = ByteCache(6)
    cache.put("a", "123")
    cache.put("a", "12345")
    assert (cache.bytes, len(cache), cache.get("a")) == (5, 1, "12345")
    # Un valor que por sí solo no cabe no entra ni echa a nadie; reponer una clave con uno así
    # suelta el viejo.
    cache.put("b", "1234567")
    assert (cache.bytes, len(cache), cache.get("b")) == (5, 1, None)
    cache.put("a", "1234567")
    assert (cache.bytes, len(cache)) == (0, 0)
    assert cache.get("nada") is None
