"""The band surfing module (c/k6/mod/surf.c) on an emulated Cortex-M0, against the modelled
FD6818 receiver of test_k6_rxmap (RSSI restarts at -173 dBm on every tune and ramps as Phase 0
measured) and the real keypad matrix (test_k6_i2s run(..., keypad=...)): the bargraph on its lens
scale (the listening frequency always in the middle, 2.5 kHz bins within +-20 kHz, wider ones
out to the span) with its caret and ruler, peak jumps with the side keys, UP/DOWN moving the
scale, band and zoom keys, the band's edge, monitor, the squelch's open and hang, and EXIT
putting the audio path back. SPDX-License-Identifier: MIT"""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, need_clang, run  # noqa: E402
from test_k6_rxmap import FLOOR, Receiver  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_screen  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "surf.c")
AUTO = os.path.join(ROOT, "c", "k6", "mod", "surfauto.c")
KEYS = os.path.join(ROOT, "c", "k6", "mod", "surfkeys.c")
FOREVER = 10**12
ARENA = 11776                   # the voice images' module arena: header + code + tag (EXPLORE's
                                # 5,376 B is too small since the axis and the narrow lens)
C, KI, NV = 31, 8, 63           # the middle bin, the lens's bins each side, the bins on screen
SPANS = [40, 100, 200, 400, 1000, 2000]     # x 2.5 kHz: +-100 kHz .. +-5 MHz
SEVENTY = (430_000_000, 450_000_000)
# Aura's receive set-up for FM: the AGC table's top entry, REG_14 / REG_49 (AGC), REG_73 (AFC
# on), REG_7E (AGC, not fixed)
AURA = {0x13: 0x03BE, 0x14: 0x0019, 0x49: 0x2A38, 0x73: 0x0000, 0x7E: 0x302E}


LK = {40: (82, 60201), 100: (344, 11442), 200: (520, 6458), 400: (688, 4196), 1000: (906, 2620),
      2000: (1070, 1925)}         # surf.c's LK, LG: the curve's ratio (Q12) and 256 a, per span


def lens(span=400):
    """each bin's offset from the middle (x 500 Hz), as surf.c's lens() works it out: a geometric
    curve, each step at least one unit"""
    k, g0 = LK[span]
    g, off = g0, [0]
    for _ in range(C):
        g += (g * k) >> 12
        off.append(max(off[-1] + 1, (g - g0 + 128) >> 8))
    return off


def scale(cen, span=400, band=SEVENTY):
    """the 63 bins' frequencies around cen, 0 outside the band"""
    off = lens(span)
    hz = [cen + (1 if i > C else -1) * 500 * off[abs(i - C)] for i in range(NV)]
    return [f if band[0] <= f <= band[1] else 0 for f in hz]


def bin_for(f, cen, span=400, band=SEVENTY):
    return min((abs(h - f), i) for i, h in enumerate(scale(cen, span, band)) if h)[1]


def labels(span=400):
    """line 5: the axis's labels, the span at each end and the lens's edges under their ticks"""
    k = span * 2.5
    t = (f"{k / 1000:g}M" if k >= 1000 else f"{k:g}k")
    line = list(" " * 21)
    line[0:1 + len(t)] = "-" + t
    line[20 - len(t):21] = "+" + t
    line[6:15] = "-20k +20k"
    return "".join(line)


class Bumpy(Receiver):
    """bumps: [(hz, dBm)] that show only in reads under 5 ms after a tune (the noise bumps bench
    run 1 jumped to on 2026-11-08), not in a settled read"""

    def __init__(self, signals, bumps):
        super().__init__(signals)
        self.bumps, self.now, self.wide, self.tunes = bumps, 0, 1, []

    def tune(self, hz, wide, us):
        super().tune(hz, wide, us)
        self.wide = wide
        self.tunes.append((us, hz))

    def rssi(self, us):
        """the narrow filter passes +-6 kHz (the wide one +-12) and settles in ~20 ms (12)"""
        self.now = us
        if self.wide:
            return super().rssi(us)
        from test_k6_rxmap import ramp, rssi_reg
        g = ramp((us - self.t) * 12 // 20)
        return rssi_reg(round(-173 + (self.level(us) + 173) * g))

    def level(self, us):
        import math
        half = 12_000 if self.wide else 6_000
        p = 10 ** (FLOOR / 10)
        for hz, dbm, a, b in self.sig:
            if abs(hz - self.f) <= half and a <= us < b:
                p += 10 ** (dbm / 10)
        if us - self.t < 5000:
            p += sum(10 ** (d / 10) for hz, d in self.bumps if abs(hz - self.f) <= half)
        return 10 * math.log10(p)


def rt_record(slot, rx_hz, call=b"", off=0, tx_tone=0, state=0xFFFC):
    """(slot, 64 bytes): an explorer repeater-table record as c/k6/rt.h lays it out (no CRC: surf
    doesn't check it)"""
    return slot, struct.pack("<HHIiHHHBBI16s10sBBBBHII", state, 1, rx_hz, off, 0, tx_tone, 0, 0, 0, 0,
                             b"", call, 0, 0, 0, 0, slot + 1, slot + 1, 0)


def go(signals, keys, rx_hz=440_600_000, level=7, regs=None, src=SRC, bumps=(), table=()):
    from modem import k6mod
    code, entry = k6mod.build(src)
    m = Bumpy([(f, d, a, b) for f, d, a, b in signals], list(bumps))
    lcd = {}
    mem = bytearray(b"\xff" * 0x110000)
    for slot, rec in table:
        mem[64 * slot:64 * slot + 64] = rec
    r = {**START, **AURA, 0x67: m.rssi, 0x47: 0x6040, **(regs or {})}
    ret, said, regs, writes = run(r, code=(code, entry), tune=m.tune, level=level, lcd=lcd, keypad=keys,
                                  spare=ARENA - 24 - len(code), count=3_000_000_000, rx_hz=rx_hz,
                                  flash={"mem": mem}, paint=True)
    lcd["tunes"] = m.tunes
    return ret, said, regs, writes, lcd


def taps(keys, t0, gap=200_000, hold=100_000):
    """keys pressed one after another from t0 (us): [(from, to, key)]"""
    return [(t0 + i * gap, t0 + i * gap + hold, k) for i, k in enumerate(keys)]


AX = 27                         # the axis line's row; its ticks in rows 28 (all) and 29 (major)
ROWS = 30


def lit(frame, r, x):
    return bool(frame["wf"][r][x >> 3] & (0x80 >> (x & 7)))


def split(frame):
    """(the bars' pixels with the carets taken off, {bin: caret tip row}). A remembered peak's
    caret is XORed over the bars: its tip on both pixels of its bin in row r, its feet on the
    neighbours' near pixels in row r + 1. The bars fill both pixels of a bin alike, so only feet
    make a bin's two pixels differ: in a feet row, bin j's differ where c[j - 1] ^ c[j + 1] (c: a
    caret at that bin), and x 126 (past the last bin) is the last bin's right foot alone, which
    solves each row's carets exactly"""
    if "_split" in frame:
        return frame["_split"]
    g = [[lit(frame, r, x) for x in range(128)] for r in range(ROWS)]
    tips = {}
    for R in range(7, AX):
        d = [g[R][2 * j] != g[R][2 * j + 1] for j in range(NV)] + [g[R][126]]
        c = [False] * NV
        c[NV - 1] = d[NV]
        for i in range(NV - 3, -1, -2):
            c[i] = d[i + 1] ^ c[i + 2]
        c[1] = d[0]
        for i in range(3, NV, 2):
            c[i] = d[i - 1] ^ c[i - 2]
        for i in (i for i in range(NV) if c[i]):
            tips[i] = R - 1
            for x, r in ((2 * i, R - 1), (2 * i + 1, R - 1), (2 * i - 1, R), (2 * i + 2, R)):
                if 0 <= x < 128:
                    g[r][x] = not g[r][x]
    frame["_split"] = (g, tips)
    return g, tips


def height(frame, i):
    g = split(frame)[0]
    return sum(1 for r in range(6, AX) if g[r][2 * i])


def carets(frame):
    """the remembered peaks' carets: {bin: its height in px over the floor (1 .. 19)}"""
    return {i: AX - 2 - r for i, r in split(frame)[1].items()}


def ticks(frame, major=False):
    """the axis's ticks (x), all or the major ones, without the middle's (x 62-63)"""
    return {x for x in range(128) if x not in (62, 63) and lit(frame, AX + 1 + major, x)}


def caret(frame):
    """the caret's bin: the single pixel pair of its tip (row 3), near the middle"""
    row = frame["wf"][3]
    xs = [x for x in range(56, 72) if row[x >> 3] & (0x80 >> (x & 7))]
    return xs[0] // 2 if xs else None


def marks(frame):
    """the bins with a known-repeater mark (row 0)"""
    row = frame["wf"][0]
    return {x // 2 for x in range(0, 128, 2) if row[x >> 3] & (0x80 >> (x & 7))}


def sels(said):
    return [ln.split()[1] for ln in said if ln.startswith("sel ")]


def at(frames, us):
    return next(f for f in frames if f["us"] >= us)


@need_clang
@pytest.mark.parametrize("src", [SRC, AUTO, KEYS])
def test_builds_level_6_and_fits_the_store(src):
    from modem import k6mod
    code, entry = k6mod.build(src)
    assert k6mod.source_level(src) == 6 and entry == 0
    # the arena, with the remembered peaks (128 x 8 B, the rest of the store) and room to grow
    # (the bitmap and the state: the stack)
    assert len(code) <= ARENA - 24 - 8 * 128 - 200


def test_lens_scale_steps_grow_outward_and_end_on_the_span():
    for span in SPANS:
        off = lens(span)
        steps = [b - a for a, b in zip(off, off[1:])]
        assert abs(off[KI] * 500 - 20_000) <= 500                # the lens's edge on +-20 kHz
        assert off[C] * 500 == span * 2500                       # the last bin on the span
        assert all(st > 0 for st in steps)
        # log all the way (Jeff 2026-11-10): each step about a fixed ratio over the last, also
        # through the lens's edge (it was 2.5 kHz flat inside, then a jump)
        assert all(b >= a - 1 for a, b in zip(steps, steps[1:]))    # growing (whole units)
        assert steps[0] < steps[KI] <= 1.5 * steps[KI - 1] + 1     # no jump at the lens's edge
    assert lens(400)[C] * 500 == 1_000_000 and lens(400)[1] * 500 == 1500


@need_clang
def test_bench_build_surfs_by_itself_and_wraps():
    # 70cm from 440.600: peaks at 440.850 and 441.200, none above (the screen is +-1 MHz), so it
    # wraps to 430.000 and finds 430.300; then straight back up to 440.850, 10 MHz off the screen
    # but remembered (its caret still up: Jeff 2026-11-11), and round again
    sig = [(440_850_000, -80, 0, FOREVER), (441_200_000, -95, 0, FOREVER), (430_300_000, -90, 0, FOREVER)]
    ret, said, regs, writes, lcd = go(sig, [], src=AUTO)
    s = sels(said)
    assert s[:2] == ["440.8500", "441.2000"]                 # each with its confirming read
    lv = [ln.split()[2:] for ln in said if ln.startswith("sel ")][:2]
    assert lv[0][0] == "-80" and int(lv[0][1]) < -120 and int(lv[1][0]) > int(lv[1][1]) + 10
    assert s[2:6] == ["430.3000", "440.8500", "441.2000", "430.3000"] and said[-1].startswith("end sw=")
    assert lcd["st"]["us"] >= 60_000_000 and regs[0x47] == 0x6040
    # the last bargraph comes back as say lines (under 60 characters), and draws as the screen did
    assert all(len(ln) < 60 for ln in said)
    f = k6_screen.surf_frame(said)
    assert f is not None and len(f["heights"]) == NV
    last = lcd["frames"][-1]
    same = sum(height(f, i) == height(last, i) for i in range(NV))
    assert same >= NV - 4, same                             # (a last sweep)
    # every bin in the band draws
    cen = float(next(ln for ln in said if ln.startswith("bar ")).split()[1]) * 1e6
    assert all(f["heights"][i] >= 1 for i, h in enumerate(scale(round(cen))) if h)


@need_clang
def test_bargraph_peaks_side_keys_and_audio():
    sig = [(440_850_000, -70, 0, FOREVER), (441_200_000, -95, 0, FOREVER), (440_500_000, -105, 0, FOREVER)]
    keys = [(3_000_000, 3_200_000, "A"), (4_000_000, 4_200_000, "A"), (5_000_000, 5_200_000, "M"),
            (6_000_000, 6_200_000, "B"), (6_500_000, 6_700_000, "M"), (8_000_000, 8_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    assert said[0] == "surf 70cm 440.600 25k" and said[-1].startswith("end sw=") and ret > 0
    # SIDE1 twice: the -70 at 440.850 and then the -95 at 441.200; SIDE2 back to 440.850
    assert sels(said) == ["441.2000", "440.8500"]
    # the audio path, the AFC and the gain written only to be put back
    assert {r for r, _ in writes} == {0x47, 0x13, 0x73, 0x7E} and regs[0x47] == 0x6040
    assert all(regs[r] == v for r, v in AURA.items())
    spk = lcd["st"]["spk"]
    assert spk[0] == (0, 0) and spk[-1][1] == 0                          # speaker amp off again
    # opens on the first jump, once the moved scale has been swept
    assert 3_000_000 < spk[1][0] < 3_700_000 and spk[1][1] == 4
    fr = lcd["frames"]
    quiet = at(fr, 2_000_000)
    assert quiet["text"][0] == "SURF 70cm 25k"
    assert quiet["text"][1] == "440.6000 -137dBm"
    assert quiet["text"][5] == labels(400) == "-1M   -20k +20k   +1M"
    # the three signals stand out in order of level (a carrier between two wide bins lights
    # both), the rest is floor
    hs = {i: height(quiet, i) for i in range(NV)}
    b85, b12, b05 = (bin_for(f, 440_600_000) for f in (440_850_000, 441_200_000, 440_500_000))
    assert hs[b85] == 21 and 12 <= hs[b12] < 21 and 8 <= hs[b05]
    lit = {i for i, h in hs.items() if h > 1} - {C}
    assert {b85, b12, b05} <= lit and len(lit) <= 5, lit
    assert caret(quiet) == C
    on = at(fr, 3_700_000)
    assert on["text"][1] == "440.8500 -70dBm RX" and caret(on) == C
    assert on["text"][5] == labels(400)
    # the lens: the carrier fills the middle bins within the wide filter (+-12 kHz), and no
    # further (the narrow one, until Jeff saw its flat top on 2026-11-10: +-6)
    off = lens(400)
    assert all(height(on, i) >= 18 for i in range(NV) if off[abs(i - C)] * 500 <= 11_000)
    assert all(height(on, i) <= 2 for i in range(C - KI, C + KI + 1) if off[abs(i - C)] * 500 >= 13_000)
    assert at(fr, 5_100_000)["text"][1] == "441.2000 -95dBm RX"
    # the listening bar is drawn like the others, steady (it blinked until Jeff, 2026-11-10)
    rx = [f for f in fr if f["text"][1] == "440.8500 -70dBm RX"]
    assert len(rx) >= 2 and all(height(f, C) >= 20 for f in rx)


@need_clang
def test_side_keys_skip_bumps_a_settled_read_doesnt_confirm():
    # bumps at 440.700 and 441.000 in the quick reads only; the station at 441.200 is real
    sig = [(441_200_000, -95, 0, FOREVER)]
    # (MENU 0.8 s after each jump: a jump onto a station sweeps the moved scale before the audio)
    keys = taps("AM", 2_000_000, gap=800_000) + taps("BM", 4_000_000, gap=800_000) + [(6_000_000, 6_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys, bumps=[(440_700_000, -90), (441_000_000, -90)])
    fr = lcd["frames"]
    hs = {i: height(at(fr, 1_900_000), i) for i in range(NV)}
    assert hs[bin_for(440_700_000, 440_600_000)] > 8                # the bumps do show as bars
    assert hs[bin_for(441_000_000, 440_600_000)] > 8
    # a settled read never confirmed them: no caret (the station has one)
    cs = carets(at(fr, 1_900_000))
    assert bin_for(441_200_000, 440_600_000) in cs
    assert not {bin_for(440_700_000, 440_600_000), bin_for(441_000_000, 440_600_000)} & set(cs)
    got = sels(said)
    assert got[0] == "441.2000"                              # SIDE1 skips them
    assert got[1] not in ("441.0000", "440.7000")            # SIDE2 too (it hunts on down the band)


@need_clang
def test_known_repeaters_marked_and_named():
    # the explorer's table: 440.850 simplex (AG7EW), 441.200 +5 MHz tone 103.5 (N0CALL), a replaced
    # record at 441.000 and one off the scale (443.525); the bar visits the first two
    table = [rt_record(0, 440_850_000, b"AG7EW"), rt_record(1, 441_000_000, b"OLD", state=0xFFF8),
             rt_record(70, 441_200_000, b"N0CALL", 5_000_000, 1035), rt_record(900, 443_525_000, b"FAR")]
    sig = [(440_850_000, -70, 0, FOREVER), (441_200_000, -95, 0, FOREVER)]
    keys = taps("A", 3_000_000) + taps("A", 4_000_000) + [(5_500_000, 5_800_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys, table=table)
    fr = lcd["frames"]
    quiet = at(fr, 2_000_000)
    assert marks(quiet) == {bin_for(440_850_000, 440_600_000), bin_for(441_200_000, 440_600_000)}
    assert quiet["text"][5] == labels(400)                  # bar on 440.600: not a known one
    on = at(fr, 3_700_000)
    assert on["text"][5] == "AG7EW" and C in marks(on)
    assert marks(on) == {C, bin_for(441_200_000, 440_850_000)}
    assert at(fr, 4_700_000)["text"][5] == "N0CALL +5.0 103.5"


@need_clang
def test_up_down_move_the_scale_with_the_middle():
    keys = taps("U" * 33 + "M", 1_000_000) + taps("DM", 9_000_000) + [(10_000_000, 10_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys)
    assert sels(said) == ["441.4250", "441.4000"]           # 33 channels of 25 kHz up, one down
    fr = lcd["frames"]
    assert at(fr, 900_000)["text"][1].startswith("440.6000 ")
    late = at(fr, 8_500_000)
    assert late["text"][1].startswith("441.4250 ") and late["text"][5] == labels(400)
    assert caret(late) == C                                 # the bar stays in the middle
    # a station's bar moves with the scale at once (each bin keeps the old level nearest it)
    keys = taps("UUUU", 2_000_000) + [(3_500_000, 3_800_000, "X")]
    ret, said, regs, writes, lcd = go([(441_200_000, -95, 0, FOREVER)], keys)
    fr = lcd["frames"]
    assert height(at(fr, 1_900_000), bin_for(441_200_000, 440_600_000)) > 15
    # (drawn at once after the press: the old bin's loudest read can land one bin off)
    moved = next(f for f in fr if f["text"][1].startswith("440.7000 "))
    b = bin_for(441_200_000, 440_700_000)
    assert max(height(moved, j) for j in (b - 1, b, b + 1)) > 15


@need_clang
def test_axis_ticks_show_the_frequency_intervals():
    keys = taps("3", 2_000_000) + taps("1", 3_000_000) + taps("1", 4_000_000) + [(5_000_000, 5_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys)
    fr = lcd["frames"]
    every = [5, 10, 15, 20] + list(range(30, 100, 10)) + list(range(100, 1000, 100)) + [1000, 2000, 3000, 4000, 5000]

    def want(cen, span, major=False):
        xs = set()
        for v in every:
            if major and v not in (20, 100, 1000):
                continue
            o = 1000 * v
            if o > 2500 * span:
                break
            for e in (-1, 1):
                d = [(abs(h - (cen + e * o)), i) for i, h in enumerate(scale(cen, span)) if h]
                dist, j = min(d)
                lens_ = lens(span)
                a = abs(j - C)
                step = 500 * (lens_[a + 1] - lens_[a] if a < C else lens_[C] - lens_[C - 1])
                if 2 * dist <= step:
                    xs.add(2 * j + (e > 0))
        return xs

    one = at(fr, 1_500_000)                                   # +-1 MHz
    assert ticks(one) == want(440_600_000, 400) and ticks(one, True) == want(440_600_000, 400, True)
    assert {46, 79} <= ticks(one, True)                      # +-20 kHz, the lens's edges, are major
    assert all(lit(one, AX, x) for x in range(126))          # the axis line under every bin
    assert one["text"][5] == labels(400)
    two = at(fr, 2_500_000)                                   # 3: +-2.5 MHz
    assert two["text"][5] == labels(1000) == "-2.5M -20k +20k +2.5M"
    assert ticks(two) == want(440_600_000, 1000) and ticks(two, True) == want(440_600_000, 1000, True)
    half = at(fr, 4_500_000)                                  # 1, 1: +-500 kHz
    assert half["text"][5] == labels(200) == "-500k -20k +20k +500k"
    assert ticks(half) == want(440_600_000, 200)


@need_clang
def test_band_and_zoom_keys():
    keys = taps("N", 1_000_000) + taps("1", 3_000_000) + taps("3", 5_000_000) + taps("NNNNN", 7_000_000) \
        + [(9_000_000, 9_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys)
    fr = lcd["frames"]
    gm = (462_550_000, 467_725_000)
    gmrs = at(fr, 2_000_000)
    assert gmrs["text"][0] == "SURF GMRS 12.5k"
    assert gmrs["text"][1].startswith("465.1375 ")          # mid-band, on the 12.5 kHz raster
    assert gmrs["text"][5] == labels(1000)                  # +-2.5 MHz
    assert at(fr, 4_000_000)["text"][5] == labels(400)      # 1: +-1 MHz
    assert at(fr, 4_000_000)["text"][1].startswith("465.1375 ")           # the middle stays
    assert at(fr, 6_000_000)["text"][5] == labels(1000)
    # five more: marine/WX, airband, 2m, 1.25m, 70cm
    assert at(fr, 8_500_000)["text"][0] == "SURF 70cm 25k"
    assert at(fr, 8_500_000)["text"][1].startswith("440.0000 ")


def writes_of(writes, reg):
    return [v for r, v in writes if r == reg]


@need_clang
def test_airband_listens_in_am_and_other_bands_in_fm():
    # Jeff 2026-11-10: "add air band (AM)". Receive only: no TX call exists at level 6.
    sig = [(127_500_000, -80, 3_000_000, 5_000_000)]
    keys = taps("NNN", 1_000_000, gap=400_000) + taps("N", 6_000_000) + [(7_000_000, 7_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    fr = lcd["frames"]
    air = at(fr, 2_500_000)
    assert air["text"][0] == "SURF AIR 25k" and air["text"][1].startswith("127.5000 ")
    assert at(fr, 4_000_000)["text"][1] == "127.5000 -80dBm RX"
    # AM (AF 7, AFC off) on the way in, FM (Aura's REG_73) on the way out; Aura's AM AGC left
    # out (REG_14 / 49: the RSSI never rose with it on 2026-11-06)
    assert 0x6740 in writes_of(writes, 0x47)
    assert 0x10 in writes_of(writes, 0x73)
    assert not {0x14, 0x49} & {r for r, _ in writes}
    two = at(fr, 6_500_000)
    assert two["text"][0] == "SURF 2m 10k" and two["text"][1].startswith("146.0000 ")
    assert regs[0x73] == 0 and regs[0x14] == 0x0019 and regs[0x49] == 0x2A38
    i = max(j for j, (r, v) in enumerate(writes) if v == 0x6740)
    assert writes_of(writes[i:], 0x73)[0] == 0                   # back to FM after airband


@need_clang
def test_step_key_cycles_the_steps_and_a_band_resets_it():
    # Jeff 2026-11-10: # goes through the common steps; a band change goes back to its default
    keys = taps("#", 1_000_000) + taps("UM", 2_000_000) + taps("##", 3_000_000, gap=400_000) + taps("UM", 4_000_000) \
        + taps("#", 5_000_000) + taps("N", 6_000_000) + [(7_500_000, 7_800_000, "X")]
    ret, said, regs, writes, lcd = go([], keys)
    fr = lcd["frames"]
    assert at(fr, 1_300_000)["text"][0] == "SURF 70cm 50k"
    assert at(fr, 1_300_000)["text"][5] == "step 50k"
    assert at(fr, 4_000_000)["text"][0] == "SURF 70cm 5k"      # 100k, then round to 5k
    assert at(fr, 5_300_000)["text"][0] == "SURF 70cm 6.25k"
    assert sels(said) == ["440.6500", "440.6550"]               # one 50 kHz step, one of 5 kHz
    keys = taps("####", 1_000_000, gap=400_000) + taps("UM", 3_000_000) + [(4_000_000, 4_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys)
    assert sels(said) == ["440.60625"]                          # 6.25 kHz: five decimals
    assert at(lcd["frames"], 3_700_000)["text"][1].startswith("440.60625 ")
    gm = at(fr, 7_000_000)
    assert gm["text"][0] == "SURF GMRS 12.5k"


@need_clang
def test_key_5_goes_to_the_band_centre():
    keys = taps("5M", 1_000_000) + [(2_000_000, 2_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys, rx_hz=432_100_000)
    assert sels(said) == ["440.0000"]


@need_clang
def test_gain_keys_fix_the_gain_and_8_gives_back_the_agc():
    # Jeff 2026-11-10: 7 decrease, 8 auto, 9 increase fixed gain
    keys = taps("9", 1_000_000) + taps("77", 2_000_000, gap=400_000) + taps("8", 3_000_000) + taps("7", 4_000_000) \
        + [(5_000_000, 5_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys)
    fr = lcd["frames"]
    assert writes_of(writes, 0x13)[:4] == [0x03FF, 0x03BE, 0x037E, 0x03BE]   # top, -7, -12, auto
    assert writes_of(writes, 0x7E)[:4] == [0xB02E, 0xB02E, 0xB02E, 0x302E]
    assert at(fr, 1_300_000)["text"][0] == "G0 70cm 25k" and at(fr, 1_300_000)["text"][5] == "gain 0 dB"
    assert at(fr, 2_600_000)["text"][0] == "G-12 70cm 25k"
    assert at(fr, 3_300_000)["text"][0] == "SURF 70cm 25k" and at(fr, 3_300_000)["text"][5] == "gain auto"
    assert at(fr, 4_300_000)["text"][0] == "G-7 70cm 25k"      # 7 from auto: just under the top
    assert regs[0x13] == 0x03BE and regs[0x7E] == 0x302E        # put back at EXIT


@need_clang
def test_side_key_with_no_peak_stays():
    # no remembered peak that way: "no peak", and the middle never moves
    keys = taps("A", 2_000_000) + taps("B", 3_000_000) + [(5_000_000, 5_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys, rx_hz=448_000_000)
    fr = lcd["frames"]
    assert at(fr, 2_300_000)["text"][5] == "no peak" and at(fr, 3_300_000)["text"][5] == "no peak"
    assert all(f["text"][1].startswith("448.0000 ") for f in fr)
    assert not any(carets(f) for f in fr)


@need_clang
def test_a_transient_leaves_a_caret_the_side_key_jumps_to():
    # Jeff 2026-11-11: signals go away when people stop keying; a caret over each peak heard lately
    # floats up while it is heard and slowly falls; SIDE1 / SIDE2 jump to the carets, not to what
    # is on the air at the press. 441.200 keys up for 1.5 s; 440.850 is on all the time.
    sig = [(441_200_000, -90, 1_500_000, 3_000_000), (440_850_000, -80, 0, FOREVER)]
    keys = taps("AM", 6_000_000, gap=600_000) + taps("AM", 7_500_000, gap=600_000) + [(9_000_000, 9_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    fr = lcd["frames"]
    b12, b85 = bin_for(441_200_000, 440_600_000), bin_for(440_850_000, 440_600_000)
    # (a sweep of +-1 MHz takes ~0.6 s here, each lifts a caret 8 px; at the start's fall, 1 min
    # for the whole 19 px, it falls 1 px in ~3 s)
    before, heard, later = (carets(at(fr, t)) for t in (1_400_000, 3_400_000, 5_700_000))
    assert b12 not in before and b85 in before
    assert heard[b12] >= 14                                   # lifted by every sweep it was in
    assert 0 < later[b12] < heard[b12]                        # falling since it stopped
    assert later[b85] >= 17                                   # still on the air: kept up
    # SIDE1 from 440.600: 440.850 (the nearest caret above), then 441.200, quiet for 3 s by then
    assert sels(said) == ["440.8500", "441.2000"]
    assert at(fr, 8_000_000)["text"][1].startswith("441.2000 ")
    assert "RX" not in at(fr, 8_000_000)["text"][1]           # (nothing there now: no audio)


@need_clang
def test_side_keys_hop_between_remembered_peaks_after_they_stop():
    # two short overs, both gone before the presses: SIDE1 to the one above, SIDE2 past the start
    # (no caret there) to the one below, SIDE2 again: none further down, SIDE1: the one above
    sig = [(441_200_000, -90, 500_000, 2_000_000), (440_400_000, -95, 500_000, 2_000_000)]
    keys = taps("AM", 4_000_000, gap=500_000) + taps("BM", 5_000_000, gap=500_000) \
        + taps("BM", 6_000_000, gap=500_000) + taps("AM", 7_000_000, gap=500_000) + [(8_000_000, 8_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    assert sels(said) == ["441.2000", "440.4000", "440.4000", "441.2000"]
    assert at(lcd["frames"], 6_200_000)["text"][5] == "no peak"
    # the peaks live in the module store's free RAM (128 x 8 B), not on the stack: still under
    # 2.3 KB of the ~3.1 KB the voice images give a module (2,184 B before the carets)
    assert lcd["st"]["stack"] <= 2300, lcd["st"]["stack"]


@need_clang
def test_keys_answer_at_once_even_during_a_refresh_sweep():
    # Jeff 2026-11-11: jumps and UP / DOWN took a long time. Parked on a carrier, a refresh sweep
    # (every 5 s, ~0.6 s here, 1-2 s on the radio) held the keys until it ended. Now a key cuts any
    # sweep short and the screen redraws at once: A/B presses every 0.7 s, some during refreshes
    sig = [(440_600_000, -80, 0, FOREVER)]
    t = [1_500_000 + 700_000 * n for n in range(14)]
    keys = [(a, a + 100_000, "W") for a in t] + [(12_000_000, 12_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    fr = lcd["frames"]
    notes = ["fall 5m", "fall 20m", "fall 15s", "fall 1m"]
    for n, a in enumerate(t):
        f = next(f for f in fr if f["us"] >= a and f["text"][5] == notes[n % 4])
        assert f["us"] - a < 100_000, (n, f["us"] - a)
    assert "RX" in at(fr, 11_000_000)["text"][1]              # still parked on it


@need_clang
def test_a_jump_onto_a_live_signal_opens_the_audio_quickly():
    # the moved scale's lens only (17 reads) before the audio, not the whole sweep
    sig = [(440_850_000, -80, 0, FOREVER)]
    keys = taps("A", 3_000_000) + [(5_000_000, 5_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    spk = lcd["st"]["spk"]
    assert spk[1][1] == 4 and 3_000_000 < spk[1][0] < 3_200_000, spk
    on = at(lcd["frames"], 3_300_000)
    assert on["text"][1] == "440.8500 -80dBm RX"


@need_clang
def test_a_b_key_sets_the_fall_and_a_fallen_caret_is_not_hoppable():
    # A/B cycles the fall: 1 min (the start), 5 min, 20 min, 15 s. At 15 s a full caret is down in
    # ~15 s, and then SIDE1 finds nothing
    sig = [(441_200_000, -90, 500_000, 2_000_000)]
    keys = taps("WWW", 2_500_000, gap=400_000) + taps("A", 21_000_000) + [(22_000_000, 22_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    fr = lcd["frames"]
    assert [at(fr, t)["text"][5] for t in (2_700_000, 3_100_000, 3_500_000)] == ["fall 5m", "fall 20m", "fall 15s"]
    b12 = bin_for(441_200_000, 440_600_000)
    h = [carets(at(fr, t)).get(b12, 0) for t in (3_600_000, 8_600_000, 13_600_000, 20_000_000)]
    assert h[0] > h[1] > h[2] > 0 and h[3] == 0, h            # 5 s: 50 x 1/8 px, about 6 px
    assert 5 <= h[0] - h[1] <= 7
    assert at(fr, 21_300_000)["text"][5] == "no peak"
    assert all(f["text"][1].startswith("440.6000 ") for f in fr)


@need_clang
def test_band_edge_leaves_the_bins_past_it_empty():
    keys = taps("DDDDM", 1_000_000) + [(3_000_000, 3_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys, rx_hz=430_100_000)
    assert sels(said) == ["430.0000"]                        # DOWN stops at the band's bottom
    f = at(fr := lcd["frames"], 2_500_000)
    assert f["text"][1].startswith("430.0000 ") and caret(f) == C
    assert all(height(f, i) == 0 for i in range(C))          # nothing below 430.000
    assert all(height(f, i) >= 1 for i in range(C + 1, NV))
    assert f["text"][5] == labels(400)
    assert not any(lit(f, AX, x) for x in range(60)) and lit(f, AX, 70)   # the axis stops there too


@need_clang
def test_monitor_opens_the_audio_without_a_signal():
    keys = taps("*", 1_000_000) + taps("*", 3_000_000) + [(4_000_000, 4_300_000, "X")]
    ret, said, regs, writes, lcd = go([], keys)
    spk = lcd["st"]["spk"]
    assert [v for _, v in spk] == [0, 4, 0]
    assert 1_000_000 < spk[1][0] < 1_200_000 and 3_000_000 < spk[2][0] < 3_300_000
    fr = lcd["frames"]
    assert at(fr, 2_000_000)["text"][0] == "SURF 70cm 25k MON"
    assert at(fr, 3_500_000)["text"][0] == "SURF 70cm 25k"


@need_clang
def test_squelch_opens_on_a_signal_and_closes_after_the_hang():
    sig = [(440_600_000, -100, 2_000_000, 3_000_000)]
    ret, said, regs, writes, lcd = go(sig, [(6_000_000, 6_300_000, "X")])
    spk = lcd["st"]["spk"]
    assert [v for _, v in spk] == [0, 4, 0]
    assert 2_000_000 < spk[1][0] < 2_150_000                 # a check every 16 bins (~70 ms here)
    assert 4_500_000 <= spk[2][0] < 4_700_000                 # 1.5 s after it dropped


@need_clang
def test_speaker_is_off_whenever_the_receiver_retunes():
    # Jeff 2026-11-10: a buzz from the speaker when it scanned with the audio open. Parked on a
    # long signal, the refresh sweeps (every 5 s), a side-key seek, and monitor with UP must not
    # tune with the amp on.
    sig = [(440_600_000, -90, 1_000_000, FOREVER), (440_850_000, -80, 0, FOREVER)]
    keys = taps("A", 9_000_000) + taps("*", 11_000_000) + taps("U", 12_000_000) + [(14_000_000, 14_300_000, "X")]
    ret, said, regs, writes, lcd = go(sig, keys)
    spk, tunes = lcd["st"]["spk"], lcd["tunes"]
    on = [(a, b) for (a, va), (b, _) in zip(spk, spk[1:] + [(lcd["st"]["us"], 0)]) if va]
    assert len(on) >= 3                                      # opened, a refresh broke it, the jump
    assert not [t for t, hz in tunes if any(a <= t < b for a, b in on)]
    gaps = [b2 - b1 for (_, b1), (b2, _) in zip(on, on[1:]) if b2 - b1 < 1_000_000]
    assert gaps and max(gaps) < 600_000                      # a refresh mutes it for one sweep only


@need_clang
def test_exit_waits_for_the_key_release():
    ret, said, regs, writes, lcd = go([], [(1_000_000, 2_500_000, "X")])
    assert lcd["st"]["us"] >= 2_500_000 and said[-1].startswith("end sw=")


@need_clang
def test_refuses_an_image_without_a_screen():
    ret, said, regs, writes, lcd = go([], [], level=5)
    assert ret == -3 and said == [] and writes == []


def test_screen_renderer_lays_out_like_the_firmware():
    f = {"text": ["SURF", "1", "", "", "", ""], "bar": [(0, 0)] * 6, "wf": [bytes([0x80] + [0] * 15)] * 30}
    fb = k6_screen.render(f)
    assert all(fb[y][127] == 1 for y in range(11)) and fb[11][127] == 0   # inverted title bar
    assert fb[21][0] == 1 and fb[50][0] == 1 and fb[20][0] == 0 and fb[51][0] == 0
    assert sum(fb[y][x] for y in range(12, 20) for x in range(1, 7)) > 0  # the "1" on line 1


if __name__ == "__main__":
    # python tests/test_k6_surf.py OUT.png: the emulated screens of the side-key run, for docs
    sig = [(440_850_000, -70, 0, FOREVER), (441_200_000, -95, 0, FOREVER), (440_500_000, -105, 0, FOREVER),
           (442_900_000, -90, 0, FOREVER)]
    keys = [(3_000_000, 3_200_000, "A"), (4_000_000, 4_200_000, "A"), (5_500_000, 5_700_000, "3"),
            (8_500_000, 8_800_000, "X")]
    table = [rt_record(0, 440_850_000, b"AG7EW"), rt_record(1, 441_200_000, b"N0CALL", 5_000_000, 1035)]
    _, _, _, _, lcd = go(sig, keys, table=table)
    fr = lcd["frames"]
    q = at(fr, 2_000_000)
    k6_screen.png([q, at(fr, 3_700_000), at(fr, 4_700_000), at(fr, 8_000_000)],
                  sys.argv[1] if len(sys.argv) > 1 else "surf.png",
                  captions=["440.600 in the middle, +-1 MHz", "SIDE1: 440.850 moves to the middle",
                            "SIDE1: next peak, a known repeater", "3: zoomed out to +-2.5 MHz"])


@need_clang
def test_bench_build_presses_the_new_keys_and_says_each_result():
    # surfkeys.c: 9, 7 x 7, 8, BAND x 3 (airband), #, 5, BAND x 3, one every 3 s, then it ends
    ret, said, regs, writes, lcd = go([(440_850_000, -70, 0, FOREVER)], [], rx_hz=440_850_000, src=KEYS)
    ks = [ln.split() for ln in said if ln.startswith("k")]
    assert [k[0] for k in ks] == ["k" + c for c in "97777777" "8" "NNN" "#5" "NNN"]
    assert [int(k[4][1:]) for k in ks[:9]] == [1, 2, 3, 4, 5, 6, 7, 8, 0]
    assert ks[0][1] == "440.8500" and int(ks[0][2]) > -75       # parked on the carrier
    assert ks[10][5:] == ["MAR/WX", "25k"] and ks[11][5:] == ["AIR", "25k"]
    assert ks[12][5:] == ["AIR", "50k"] and ks[13][1] == "127.5000"
    assert ks[-1][5:] == ["70cm", "25k"]
    assert said[-1].startswith("end sw=")
    assert all(regs[r] == v for r, v in AURA.items()) and regs[0x47] == 0x6040
