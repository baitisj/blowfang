"""Help cards (tools/k6_cards.py): rendering, the page format, decks, the core deck's screens"""
import os
import struct
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_cards as kc  # noqa: E402

from modem import k6mod  # noqa: E402

CORE = os.path.join(ROOT, "docs", "manual", "cards", "core.txt")
# every core screen a held MENU asks for (aura-pager.patch: explore::help_name, pager help_key)
CORE_SCREENS = [
    "EXPLORE",
    "TRY",
    "LISTEN",
    "NEWCALL",
    "EDIT",
    "ARENA",
    "MENU",
]
HELP_PAGES = 4                     # pages a screen may have (pager.rs HELP_PAGES)


def test_page_format_round_trips_and_bit_0_is_the_top_row():
    fb = kc.blank()
    fb[0][0] = fb[7][1] = fb[8][2] = fb[63][127] = 1
    card = kc.pages(fb)
    assert len(card) == 1024
    assert card[0] == 0x01 and card[1] == 0x80 and card[128 + 2] == 0x01 and card[7 * 128 + 127] == 0x80
    assert kc.unpages(card) == fb


def test_title_bar_is_lit_and_text_dark_on_it():
    fb = kc.render("AB", [])
    assert all(fb[0][x] for x in range(128)) and all(fb[9][x] for x in range(128))
    assert any(not fb[y][x] for y in range(1, 8) for x in range(1, 12))      # the title, dark
    assert not any(fb[y][x] for y in range(10, 64) for x in range(128))


def test_key_caps_are_lit_and_too_wide_lines_refused():
    fb = kc.render("T", ["[5] save"])
    assert fb[10][0] and fb[16][3]                 # the cap's box, 1 px round its glyph
    kc.render("T", ["x" * 31])
    with pytest.raises(kc.CardError):
        kc.render("T", ["x" * 33])
    with pytest.raises(kc.CardError):
        kc.render("T", ["[MENU]" + "x" * 28])
    with pytest.raises(kc.CardError):
        kc.render("T", [""] * 10)
    with pytest.raises(kc.CardError):
        kc.render("X" * 22, [])


def test_parse_checks_names_and_pages():
    src = "= A 1\nTITLE\nline\n\n= A 2\nT2\n= B 1\nT3\n"
    cards = kc.parse(src)
    assert [(n, p) for n, p, _, _ in cards] == [("A", 0), ("A", 1), ("B", 0)]
    assert cards[0][3] == ["line"]
    for bad in ["= A 2\nT\n", "= A 1\nT\n= A 1\nT\n", "= lower 1\nT\n", "= TOOLONGNAME 1\nT\n", "x\n= A 1\nT\n"]:
        with pytest.raises(kc.CardError):
            kc.parse(bad)


def test_deck_layout_and_find():
    cards = [("EXPLORE", 0, bytes([1]) * 1024), ("EXPLORE", 1, bytes([2]) * 1024), ("MENU", 0, bytes([3]) * 1024)]
    d = kc.deck(cards)
    assert d[:4] == b"K6HC" and d[4] == 3 and d[5] == 1 and d[6:16] == bytes(10)
    assert len(d) == 16 + 3 * 16 + 3 * 1024
    e = d[16:32]
    assert e[:8] == b"EXPLORE\0" and e[8] == 0 and struct.unpack("<I", e[12:16])[0] == 16 + 48
    assert kc.find(d, "EXPLORE", 1) == (bytes([2]) * 1024, 2)
    assert kc.find(d, "MENU", 0) == (bytes([3]) * 1024, 1)
    assert kc.find(d, "EDIT", 0) == (None, 0)
    with pytest.raises(kc.CardError):
        kc.deck(cards * 4)                         # 12 cards: more than an upload holds


def test_core_deck_covers_every_screen_and_fits_an_upload():
    data, pics = kc.build(open(CORE).read())
    assert len(data) <= kc.UPLOAD_MAX and len(pics) <= kc.MAX_CARDS == 11
    names = {}
    for n, p, _ in pics:
        names.setdefault(n, []).append(p)
    for s in CORE_SCREENS:
        assert s in names, s
    for n, ps in names.items():
        assert len(ps) <= HELP_PAGES, n
    for n, p, fb in pics:
        card, count = kc.find(data, n, p)
        assert kc.unpages(card) == fb and count == len(names[n])


def test_a_deck_makes_a_library_image_with_the_deck_flag():
    data, _ = kc.build(open(CORE).read())
    img = k6mod.image(data, k6mod.bin_entry(data), 1, bytes(16), lib=13, cards=True)
    assert img[13] == k6mod.LIB_FLAG | k6mod.CARDS_FLAG and img[15] == 13
    assert img[16:16 + len(data)] == data and len(img) == 16 + len(data) + 8
