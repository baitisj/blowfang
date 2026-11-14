"""Draw a K6 RAM module's screen (module API level 6) as the radio's 128x64 LCD shows it.

The emulator harness (tests/test_k6_i2s.py run(..., lcd={})) records one frame a lcd_flush:
six text lines, a bar a line and the waterfall rows. render() lays them out the way the
firmware's draw_mod_screen does (firmware/k6-aura/aura-pager.patch, ui/pager.rs): an inverted
title bar 11 px tall, text at x = 1 and baselines y = 9 + 10 * line, the bar after a
line's text, and the waterfall rows from y = 21 over lines 2-4. png() saves frames side by side,
one square a pixel, for docs and bench reports.

The title is FONT_6X10, the lines 1-5 a 5x7 (Jeff 2026-11-14: "keep the font in the title but
move everything else to 5x7 and make that the default"). FONT_6X10 and FONT_5X7 are
embedded-graphics 0.8's (MIT/Apache-2.0, from the X11 misc-fixed fonts), dumped from the crate
Aura links (project file k6-fonts/fonts.txt), bit 7 leftmost. The radio draws the title in that
FONT_6X10 and the lines in the stock 5x7 from its SPI flash (docs/k6-stock-firmware.md: caps 7
rows tall, bottom row on the baseline, in 6-pixel cells). The stock font isn't in this repository,
so the renders stand in embedded-graphics' FONT_5X7 (caps 6 rows, bottom row on the baseline).
SPDX-License-Identifier: MIT"""
W, H, WF_TOP = 128, 64, 21
FONT = {
    32: "00000000000000000000",
    33: "00202020202000200000",
    34: "00505050000000000000",
    35: "005050f850f850500000",
    36: "002070a0702870200000",
    37: "0048a8502050a8900000",
    38: "0040a0a040a890680000",
    39: "00202020000000000000",
    40: "00102040404020100000",
    41: "00402010101020400000",
    42: "00008850f85088000000",
    43: "00002020f82020000000",
    44: "00000000000030204000",
    45: "00000000f80000000000",
    46: "00000000000020702000",
    47: "00080810204080800000",
    48: "00205088888850200000",
    49: "002060a0202020f80000",
    50: "00708808304080f80000",
    51: "00f80810300888700000",
    52: "0010305090f810100000",
    53: "00f880b0c80888700000",
    54: "00304080b0c888700000",
    55: "00f80810102040400000",
    56: "00708888708888700000",
    57: "00708898680810600000",
    58: "00002070200020702000",
    59: "00002070200030204000",
    60: "00081020402010080000",
    61: "000000f800f800000000",
    62: "00402010081020400000",
    63: "00708810202000200000",
    64: "00708898a8b080700000",
    65: "0020508888f888880000",
    66: "00f04848704848f00000",
    67: "00708880808088700000",
    68: "00f04848484848f00000",
    69: "00f88080f08080f80000",
    70: "00f88080f08080800000",
    71: "00708880809888700000",
    72: "00888888f88888880000",
    73: "00702020202020700000",
    74: "00381010101090600000",
    75: "008890a0c0a090880000",
    76: "00808080808080f80000",
    77: "008888d8a88888880000",
    78: "008888c8a89888880000",
    79: "00708888888888700000",
    80: "00f08888f08080800000",
    81: "007088888888a8700800",
    82: "00f08888f0a090880000",
    83: "00708880700888700000",
    84: "00f82020202020200000",
    85: "00888888888888700000",
    86: "00888888505050200000",
    87: "00888888a8a8d8880000",
    88: "00888850205088880000",
    89: "00888850202020200000",
    90: "00f80810204080f80000",
    91: "00704040404040700000",
    92: "00808040201008080000",
    93: "00701010101010700000",
    94: "00205088000000000000",
    95: "0000000000000000f800",
    96: "20100000000000000000",
    97: "00000070087888780000",
    98: "008080b0c888c8b00000",
    99: "00000070888088700000",
    100: "00080868988898680000",
    101: "0000007088f880700000",
    102: "00304840f04040400000",
    103: "00000078888878088870",
    104: "008080b0c88888880000",
    105: "00200060202020700000",
    106: "00080018080808484830",
    107: "0080808890e090880000",
    108: "00602020202020700000",
    109: "000000d0a8a8a8880000",
    110: "000000b0c88888880000",
    111: "00000070888888700000",
    112: "000000b0c888c8b08080",
    113: "00000068988898680808",
    114: "000000b0c88080800000",
    115: "00000070807008f00000",
    116: "004040f0404048300000",
    117: "00000088888898680000",
    118: "00000088885050200000",
    119: "0000008888a8a8500000",
    120: "00000088502050880000",
    121: "00000088889868088870",
    122: "000000f8102040f80000",
    123: "00182010601020180000",
    124: "00202020202020200000",
    125: "00601020182010600000",
    126: "0048a890000000000000",
}

FONT5 = {
    32: "00000000000000",
    33: "20202020002000",
    34: "50505000000000",
    35: "0050f850f85000",
    36: "0070a070287000",
    37: "80902040901000",
    38: "0040a040a05000",
    39: "20202000000000",
    40: "20404040402000",
    41: "40202020204000",
    42: "00502070205000",
    43: "002020f8202000",
    44: "00000000302040",
    45: "000000f0000000",
    46: "00000000606000",
    47: "00102040800000",
    48: "20505050502000",
    49: "20602020207000",
    50: "6090102040f000",
    51: "f0106010906000",
    52: "2060a0f0202000",
    53: "f080e010906000",
    54: "6080e090906000",
    55: "f0102020404000",
    56: "60906090906000",
    57: "60909070106000",
    58: "00606000606000",
    59: "00606000604080",
    60: "00102040201000",
    61: "0000f000f00000",
    62: "00402010204000",
    63: "20501020002000",
    64: "6090b0b0806000",
    65: "609090f0909000",
    66: "e090e09090e000",
    67: "60908080906000",
    68: "e090909090e000",
    69: "f080e08080f000",
    70: "f080e080808000",
    71: "609080b0907000",
    72: "9090f090909000",
    73: "70202020207000",
    74: "10101010906000",
    75: "90a0c0c0a09000",
    76: "8080808080f000",
    77: "90f0f090909000",
    78: "90d0d0b0b09000",
    79: "60909090906000",
    80: "e09090e0808000",
    81: "60909090d06010",
    82: "e09090e0a09000",
    83: "60904020906000",
    84: "70202020202000",
    85: "90909090906000",
    86: "90909090606000",
    87: "909090f0f09000",
    88: "90906060909000",
    89: "50505020202000",
    90: "f010204080f000",
    91: "70404040407000",
    92: "00804020100000",
    93: "70101010107000",
    94: "20500000000000",
    95: "0000000000f000",
    96: "40200000000000",
    97: "00007090b05000",
    98: "8080e09090e000",
    99: "00006080806000",
    100: "10107090907000",
    101: "000060b0c06000",
    102: "205040e0404000",
    103: "00007090608070",
    104: "8080e090909000",
    105: "20006020207000",
    106: "10001010105020",
    107: "8080a0c0a09000",
    108: "60202020207000",
    109: "0000a0f0909000",
    110: "0000e090909000",
    111: "00006090906000",
    112: "0000e09090e080",
    113: "00007090907010",
    114: "0000e090808000",
    115: "000070c030e000",
    116: "4040e040403000",
    117: "00009090907000",
    118: "00005050502000",
    119: "00009090f0f000",
    120: "00009060609000",
    121: "00009090502040",
    122: "0000f02040f000",
    123: "10206020201000",
    124: "20202020202000",
    125: "40203020204000",
    126: "50a00000000000",
}

def _text5(fb, s, x, y, ink):
    """a 5x7 line in 6-pixel cells, the caps' bottom row on the baseline y"""
    for i, ch in enumerate(s):
        if ord(ch) >= 0x80:
            continue
        g = bytes.fromhex(FONT5.get(ord(ch), FONT5[ord("?")]))
        for r in range(7):
            for c in range(6):
                px, py = x + 6 * i + c, y - 5 + r
                if 0 <= px < W and 0 <= py < H and g[r] & (0x80 >> c):
                    fb[py][px] = ink


def _text(fb, s, x, y, ink):
    for i, ch in enumerate(s):
        if ord(ch) >= 0x80:            # a module's own glyph (level 9): drawn by _glyphs
            continue
        g = bytes.fromhex(FONT.get(ord(ch), FONT[ord("?")]))
        for r in range(10):
            for c in range(6):
                px, py = x + 6 * i + c, y - 7 + r
                if 0 <= px < W and 0 <= py < H and g[r] & (0x80 >> c):
                    fb[py][px] = ink


def _rect(fb, x, y, w, h, ink, fill=True):
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if 0 <= xx < W and 0 <= yy < H and (fill or yy in (y, y + h - 1) or xx in (x, x + w - 1)):
                fb[yy][xx] = ink


def _glyphs(fb, s, y, ink, gl):
    """a line's module glyphs (API level 9): chr(0x80 + k) draws gl[k], six 16-bit columns, column c
    at x = 6 * cell + c, bit r on row y - 8 + r (bits 0-9)"""
    for i, ch in enumerate(s):
        k = ord(ch) - 0x80
        if 0 <= k < len(gl):
            for c, m in enumerate(gl[k]):
                for r in range(10):
                    px, py = 6 * i + c, y - 8 + r
                    if m >> r & 1 and 0 <= px < W and 0 <= py < H:
                        fb[py][px] = ink


def render(frame):
    """frame: {"text": six lines, "bar": six (num, den), "wf": rows of 16 bytes, "glyphs": a module's
    glyphs (optional)} -> 64 rows of 128 0/1"""
    fb = [[0] * W for _ in range(H)]
    _rect(fb, 0, 0, W, 11, 1)
    wf = frame.get("wf") or []
    for i in range(6):
        if wf and 2 <= i < 5:
            continue
        t = frame["text"][i][:21]
        y = 9 + 10 * i
        ink = 0 if i == 0 else 1
        (_text if i == 0 else _text5)(fb, t, 1, y, ink)
        _glyphs(fb, t, y, ink, frame.get("glyphs") or [])
        num, den = frame.get("bar", [(0, 0)] * 6)[i]
        x0 = 1 + 6 * len(t) + (3 if t else 0)
        if den and x0 < 118:
            w = 126 - x0
            _rect(fb, x0, y - 7, w + 1, 8, ink, fill=False)
            fill = w * min(num, den) // den
            if fill:
                _rect(fb, x0, y - 7, fill, 8, ink)
    for r, row in enumerate(wf):
        for x in range(W):
            if row[x >> 3] & (0x80 >> (x & 7)):
                fb[WF_TOP + r][x] = 1
    return fb


def png(frames, path, scale=3, captions=None):
    """frames side by side, each scale x scale squares a pixel, an optional caption under each"""
    from PIL import Image, ImageDraw
    on, off, gap = (0x1C, 0x24, 0x1A), (0x9A, 0xA8, 0x8C), 12
    cap = 18 if captions else 0
    img = Image.new("RGB", (len(frames) * (W * scale + gap) + gap, H * scale + 2 * gap + cap), (250, 250, 248))
    d = ImageDraw.Draw(img)
    for n, f in enumerate(frames):
        fb = render(f)
        ox = gap + n * (W * scale + gap)
        for y in range(H):
            for x in range(W):
                x0, y0 = ox + x * scale, gap + y * scale
                d.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill=on if fb[y][x] else off)
        if captions:
            d.text((ox, gap + H * scale + 4), captions[n], fill=(40, 40, 40))
    img.save(path)


def ascii_art(frame):
    return "\n".join("".join("#" if v else "." for v in row) for row in render(frame))


def surf_frame(lines):
    """The last bargraph a surfauto run says as a frame for render() and png(): the bars as the
    radio drew them, with the scale's edges. c/k6/mod/surf.c says "bar <middle MHz> <span> lens"
    (the lens scale, 2026-11-09: the edges are the middle +- the span) or, before that, "bar <first
    bin MHz> <step>" (64 even bins), then lines of a letter a bin: a = 1 px .. x = 24 px, '-' a bin
    outside the band. lines: the run's say lines (with or without the "K6 MOD say " prefix);
    returns None if it says no bargraph."""
    lines = [ln.split("K6 MOD say ", 1)[-1].strip() for ln in lines]
    try:
        i = max(n for n, ln in enumerate(lines) if ln.startswith("bar "))
    except ValueError:
        return None
    head = lines[i].split()
    lo, step = head[1], head[2]
    hs = []
    for ln in lines[i + 1:]:
        if not ln or not all("a" <= c <= "x" or c == "-" for c in ln):
            break
        hs += [0 if c == "-" else ord(c) - ord("a") + 1 for c in ln]
    kstep = float(step.rstrip("k"))
    if head[3:4] == ["lens"]:
        mid = float(lo)
        lo, hi = mid - kstep / 1000, mid + kstep / 1000
    else:
        lo = float(lo)
        hi = lo + (len(hs) - 1) * kstep / 1000
    wf = [bytearray(16) for _ in range(30)]
    base = 27 if head[3:4] == ["lens"] else 30      # the lens build: bars on an axis line, row 27
    for b, h in enumerate(hs):
        for r in range(base - h, base):
            wf[r][b >> 2] |= 0xC0 >> (2 * (b & 3))
        if base < 30 and h:
            wf[base][b >> 2] |= 0xC0 >> (2 * (b & 3))
    text = ["SURF bars " + step, "", "", "", "", f"{lo:.3f}".ljust(14) + f"{hi:.3f}"]
    return {"text": text, "bar": [(0, 0)] * 6, "wf": [bytes(r) for r in wf], "heights": hs,
            "lo_mhz": lo, "step_khz": kstep}


if __name__ == "__main__":
    # python tools/k6_screen.py RUN.txt OUT.png: draw the last bargraph a surfauto run said
    import sys
    f = surf_frame(open(sys.argv[1]).read().splitlines())
    if f is None:
        sys.exit(f"{sys.argv[1]}: no 'bar' lines")
    if len(sys.argv) > 2:
        png([f], sys.argv[2], captions=[f"from {f['lo_mhz']:.4f} MHz ({f['step_khz']:g} kHz)"])
    print(ascii_art(f))
