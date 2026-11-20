"""K6 help cards (the release plan, Jeff 2026-11-13): full-screen 128x64 pictures made here and
kept on the radio in decks, in the SPI module library (slots 13-15). A held MENU shows the card
for the screen you're on, UP/DOWN turn its pages, any other key goes back.

  python3 tools/k6_cards.py deck docs/manual/cards/core.txt -o out/cards-core.bin --png out/cards-core.png
  python -m modem.k6 mod OUT.wav out/cards-core.bin --cards --install 13 --lz4 --key-file KEY

Card source (docs/manual/cards/*.txt), one card after another:

  = EXPLORE 1             a card: the screen's name (8 characters at most, as the radio asks
                          for it) and its page, 1 up
  EXPLORE  1/2            the title, in the 5x7 on a lit bar (21 characters)
  UP DN  pick a row       then up to 9 lines in the 4x6 (32 characters, less 2 px a key cap):
  [8] edit  [5] save      [X] draws X as a lit key cap; a line of --- is a dotted rule
  # blank lines count; trailing ones are dropped

A card is 1,024 B in the display's own page format: 8 pages of 128 column bytes, bit 0 the
top row of the page (the stock boot picture's format). The radio reads a page at a time from
SPI straight into the band it draws through, so a card costs it no buffer and no text code.

A deck: a 16 B header ('K6HC', count, version 2, 10 zero bytes), count 16 B index entries (the
screen's name, 8 B NUL padded; its page, 0 up; a zero byte; 2 zero bytes; the card's offset from
the deck's start, u32), then the cards. Version 2 (module API level 15, docs/k6-lz4.md) keeps
each card packed: 8 band ends (u16, from the card's start, band 0 starting at 16), then the 8
bands of 128 B, each one LZ4 block (modem/lz4.py, c/k6/lz4d.c) or the 128 B as they are; the
radio unpacks one band into the band it draws through, so a card still costs it no buffer. The
core deck goes from 9,376 B to about 7,100 B in SPI. It travels as a module image with the library
and deck flags (modem.k6mod.image(cards=True), packed on top with lz4=True: about 5.6 KB on the
air), and unpacks into the module block on arrival, so a deck holds DECK_MAX bytes, about 19
cards. A level 14 image reads only version 1 decks, a level 15 one only version 2.

The fonts are embedded-graphics 0.8's FONT_5X7 and FONT_4X6 (MIT/Apache-2.0, from the X11
misc-fixed fonts, public domain), in tools/k6_card_fonts.txt.
SPDX-License-Identifier: MIT"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modem import lz4  # noqa: E402

W, H = 128, 64
CARD_BYTES = W * H // 8
MAGIC = b"K6HC"
VERSION = 2                         # 2 (API level 15, docs/k6-lz4.md): cards packed a band at a time
HDR = 16
ENTRY = 16
NAME_LEN = 8
CARD_TABLE = 16                     # a v2 card's 8 band ends
MAX_CARDS = 40                      # app::pager HELP_CARDS: index entries the radio reads
# The deck travels packed (modem.k6 mod --cards --lz4) and unpacks into the module block on
# arrival, so its own bytes must fit there with km_unpack's margin (k6_mod.h KM_LZ4_MARGIN)
DECK_MAX = 15408 - 16 - 8 - 8 - 128
TITLE_COLS = 21
TEXT_COLS = 32
TEXT_LINES = 9

FONTS_TXT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "k6_card_fonts.txt")


def load_fonts(path=FONTS_TXT):
    fonts, cur = {}, None
    for ln in open(path):
        p = ln.split()
        if not p:
            continue
        if p[0] == "FONT":
            cur = p[1]
            fonts[cur] = {"w": int(p[2]), "h": int(p[3]), "g": {}}
        else:
            fonts[cur]["g"][int(p[1])] = p[2]
    return fonts


FONTS = load_fonts()


def blank():
    return [[0] * W for _ in range(H)]


def fill(fb, x0, y0, x1, y1, v=1):
    for y in range(max(0, y0), min(H, y1)):
        for x in range(max(0, x0), min(W, x1)):
            fb[y][x] = v


def text(fb, font, s, x, y, ink=1, cell=None):
    """s in font from (x, y), its top row; ink 1 lit, 0 dark. The x after it."""
    f = FONTS[font]
    cell = cell or f["w"]
    for ch in s:
        g = f["g"].get(ord(ch), f["g"][ord("?")])
        for r in range(f["h"]):
            for c in range(f["w"]):
                px, py = x + c, y + r
                if g[r * f["w"] + c] == "1" and 0 <= px < W and 0 <= py < H:
                    fb[py][px] = ink
        x += cell
    return x


class CardError(ValueError):
    pass


def render(title, lines):
    """A card's picture: the title on a lit bar (as the screens' headers), then the lines"""
    if len(title) > TITLE_COLS:
        raise CardError(f"title {title!r}: {len(title)} characters, {TITLE_COLS} fit")
    if len(lines) > TEXT_LINES:
        raise CardError(f"{title!r}: {len(lines)} lines, {TEXT_LINES} fit")
    fb = blank()
    fill(fb, 0, 0, W, 10)
    text(fb, "FONT_5X7", title, 1, 1, ink=0, cell=6)
    for i, ln in enumerate(lines):
        y = 11 + 6 * i
        if ln.strip() and set(ln.strip()) == {"-"}:
            for x in range(0, W, 2):
                fb[y + 2][x] = 1
            continue
        x, k = 1, 0
        while k < len(ln):
            if ln[k] == "[" and "]" in ln[k:]:
                cap = ln[k + 1:ln.index("]", k)]
                fill(fb, x - 1, y - 1, x + 4 * len(cap), y + 6)
                x = text(fb, "FONT_4X6", cap, x, y, ink=0) + 2   # a gap after a key cap
                k += len(cap) + 2
            else:
                x = text(fb, "FONT_4X6", ln[k], x, y)
                k += 1
            if x > W + 1:                   # (a glyph's last column is blank)
                raise CardError(f"{title!r} line {i + 1} is wider than the screen: {ln!r}")
    return fb


def pages(fb):
    """The picture in the display's page format: 1,024 B"""
    out = bytearray(CARD_BYTES)
    for p in range(8):
        for x in range(W):
            b = 0
            for r in range(8):
                if fb[8 * p + r][x]:
                    b |= 1 << r
            out[p * W + x] = b
    return bytes(out)


def unpages(card):
    """pages()'s inverse: the picture again"""
    fb = blank()
    for p in range(8):
        for x in range(W):
            for r in range(8):
                fb[8 * p + r][x] = card[p * W + x] >> r & 1
    return fb


def parse(src):
    """Card source text -> [(name, page, title, lines)], pages counted from 0"""
    cards, cur = [], None
    for n, ln in enumerate(src.splitlines(), 1):
        if ln.startswith("#"):
            continue
        if ln.startswith("= "):
            p = ln[2:].split()
            if len(p) != 2 or not p[1].isdigit() or int(p[1]) < 1:
                raise CardError(f"line {n}: '= NAME PAGE' (page 1 up): {ln!r}")
            name = p[0]
            if len(name) > NAME_LEN or not all(c.isupper() or c.isdigit() or c in "-_" for c in name):
                raise CardError(f"line {n}: name {name!r}: A-Z 0-9 - _, {NAME_LEN} at most")
            cur = [name, int(p[1]) - 1, None, []]
            cards.append(cur)
        elif cur is None:
            if ln.strip():
                raise CardError(f"line {n}: text before the first '= NAME PAGE'")
        elif cur[2] is None:
            cur[2] = ln.rstrip()
        else:
            cur[3].append(ln.rstrip())
    for c in cards:
        while c[3] and not c[3][-1].strip():
            c[3].pop()
        if c[2] is None:
            raise CardError(f"card {c[0]} {c[1] + 1}: no title")
    seen = {}
    for name, page, _, _ in cards:
        seen.setdefault(name, []).append(page)
    for name, ps in seen.items():
        if sorted(ps) != list(range(len(ps))):
            raise CardError(f"{name}: pages {[p + 1 for p in ps]}, want 1..{len(ps)} once each")
    return [tuple(c) for c in cards]


def pack_card(card):
    """A card's 1,024 B -> its v2 form: 8 band ends (u16, from the card's start) and the bands,
    each one LZ4 block of 128 B, or the 128 B as they are when packing doesn't make them smaller"""
    if len(card) != CARD_BYTES:
        raise CardError("a card is 1,024 B")
    ends, body = [], bytearray()
    for p in range(8):
        band = card[p * W:(p + 1) * W]
        z = lz4.compress(band)
        if len(z) >= W or lz4.decompress(z, W) != band:
            z = band
        body += z
        ends.append(CARD_TABLE + len(body))
    return struct.pack("<8H", *ends) + bytes(body)


def unpack_card(data, off):
    """pack_card's inverse, read as the radio reads it (help_band): the card at off in a deck"""
    ends = struct.unpack_from("<8H", data, off)
    out = bytearray()
    lo = CARD_TABLE
    for hi in ends:
        n = hi - lo
        if n > W or n < 1 or off + hi > len(data):
            raise CardError("a band runs past the deck or is longer than 128 B")
        b = data[off + lo:off + hi]
        out += b if n == W else lz4.decompress(b, W)
        lo = hi
    return bytes(out)


def deck(cards):
    """[(name, page, card bytes)] -> the deck's bytes (version 2: each card packed, pack_card)"""
    if not cards:
        raise CardError("an empty deck")
    if len(cards) > MAX_CARDS:
        raise CardError(f"{len(cards)} cards: a deck holds {MAX_CARDS}")
    n = len(cards)
    packed = [pack_card(card) for _, _, card in cards]
    out = bytearray(MAGIC + bytes([n, VERSION]) + bytes(HDR - 6))
    off = HDR + ENTRY * n
    for (name, page, _), pc in zip(cards, packed):
        out += name.encode().ljust(NAME_LEN, b"\0") + bytes([page, 0, 0, 0]) + struct.pack("<I", off)
        off += len(pc)
    for pc in packed:
        out += pc
    if len(out) > DECK_MAX:
        raise CardError(f"{len(out)} B: a deck holds {DECK_MAX} B (it unpacks into the module block "
                        "on arrival); split it into two decks")
    return bytes(out)


def read_deck(data):
    """A deck's bytes -> [(name, page, card bytes)], checked as the radio checks it"""
    if len(data) < HDR or data[:4] != MAGIC or data[5] != VERSION:
        raise CardError("not a K6HC version 2 deck")
    n = data[4]
    out = []
    for i in range(n):
        e = data[HDR + i * ENTRY:HDR + (i + 1) * ENTRY]
        name = e[:NAME_LEN].rstrip(b"\0").decode()
        off = struct.unpack("<I", e[12:16])[0]
        if off < HDR or off + CARD_TABLE > len(data):
            raise CardError(f"card {i} runs past the deck")
        out.append((name, e[8], unpack_card(data, off)))
    return out


def find(data, name, page):
    """The card the radio would show: its bytes, and the screen's page count; or (None, 0)"""
    cards = [c for c in read_deck(data) if c[0] == name]
    hit = [c[2] for c in cards if c[1] == page]
    return (hit[0] if hit else None), len(cards)


def build(src):
    """Card source text -> (deck bytes, [(name, page, picture)])"""
    parsed = parse(src)
    pics = [(name, page, render(title, lines)) for name, page, title, lines in parsed]
    return deck([(name, page, pages(fb)) for name, page, fb in pics]), pics


def png(pics, path, scale=3, cols=3):
    from PIL import Image, ImageDraw
    pad, cap = 8, 16
    cw, ch = W * scale + 2 * pad, H * scale + 2 * pad + cap
    rows = (len(pics) + cols - 1) // cols
    img = Image.new("RGB", (cw * cols, ch * rows), (250, 250, 248))
    d = ImageDraw.Draw(img)
    for i, (name, page, fb) in enumerate(pics):
        ox, oy = (i % cols) * cw + pad, (i // cols) * ch + pad
        d.text((ox, oy), f"{name} {page + 1}", fill=(20, 20, 20))
        oy += cap
        for y in range(H):
            for x in range(W):
                c = (0x1c, 0x24, 0x1a) if fb[y][x] else (0x9a, 0xa8, 0x8c)
                d.rectangle([ox + x * scale, oy + y * scale, ox + x * scale + scale - 1,
                             oy + y * scale + scale - 1], fill=c)
    img.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deck", help="card source(s) -> a deck (.bin) and a PNG of its cards")
    d.add_argument("source", nargs="+")
    d.add_argument("-o", "--output", required=True)
    d.add_argument("--png")
    a = ap.parse_args(argv)
    if a.cmd == "deck":
        data, pics = build("\n".join(open(f).read() for f in a.source))
        with open(a.output, "wb") as f:
            f.write(data)
        if a.png:
            png(pics, a.png)
        names = sorted({n for n, _, _ in pics})
        print(f"{a.output}: {len(pics)} cards, {len(data)} of {DECK_MAX} B; screens: {' '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
