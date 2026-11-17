"""The off-air crystal calibration module (c/k6/mod/refcal.c) on an emulated Cortex-M0 against
gp0dma's modelled DMA and GP0 IF, with a receiver that follows the module's tunes: TV pilots and
weather stations at chosen levels, a K6 crystal E_PPB off, and an IF that REG_3B moves (one step
= the frequency x 5 Hz / 26 MHz, the sign the chip's). Also the MCU's ADC (temperature sensor and
VREFINT). Checks the scan, the probe, null-seeking, the median of several references, the dry
run putting REG_3B back and REFCAL_APPLY keeping the trim. SPDX-License-Identifier: MIT"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, dma_bench, need_clang, rssi_reg, run  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "refcal.c")
NOMINAL = 26e6 / 1536
BASE3B, X3C = 0x5880, 0x4F88
XTAL = ((X3C >> 8) << 16 | BASE3B) * 5        # the crystal REG_3B / 3C say: 26 MHz
SIGN = -1                                      # the IF's move per +1 step, as a sign (the chip's)
FLOOR = -112
PILOT = lambda ch: 470_000_000 + 6_000_000 * (ch - 14) + 309_440.559   # noqa: E731


def small(tmp_path, extra=""):
    w = tmp_path / "refcal_small.c"
    w.write_text(f"#define REFCAL_BLOCKS 8\n#define REFCAL_NREF 3\n{extra}#include \"{SRC}\"\n")
    return str(w)


class Air:
    """what the K6 hears: {true Hz: (dBm, offset Hz)}; the crystal e_ppb off (its LO e_ppb high
    moves every carrier's IF by -e_ppb x f, as REG_3B's steps do with SIGN)"""

    def __init__(self, stations, e_ppb, sign=SIGN):
        self.st, self.e, self.hz, self.sign = stations, e_ppb, 440_850_000, sign
        self.tunes = []

    def tune(self, hz, wide, us):
        self.hz = hz
        self.tunes.append((us, hz, wide))

    def near(self):
        for f, (d, o) in self.st.items():
            if abs(f - self.hz) < 3_000:
                return f + o, d                          # (where it really is)
        return None, FLOOR

    def rssi(self, us):
        return rssi_reg(self.near()[1])

    def f_if(self, t, regs):
        f, d = self.near()
        k = regs.get(0x3B, BASE3B) - BASE3B
        if not regs.get(0x73, 0) & 0x10:               # the AFC on: pulled to the middle
            return NOMINAL + 0 * t
        if not f:
            return NOMINAL + 3000 + 0 * t                    # (noise: no carrier to measure)
        # the carrier's offset from the LO (the LO e high, k steps taking it down), as the IF
        # sees it: SIGN for the chip's mixing side
        off = (f - self.hz) - self.e * 1e-9 * self.hz + k * self.hz * 5 / XTAL
        return NOMINAL + self.sign * off + 0 * t


def adc_bench(t16=1733 * 16, v17=1540 * 16, aden=1):
    st = {0x08: aden, 0x308: 0x0000, 0x28: 1 << 1, 0x14: 0x3}
    seen = {"ccr": []}

    def r(uc, off, size, s):
        if off == 0x00:
            return 4                                      # EOC
        if off == 0x40:
            ch = st[0x28]
            if ch == 1 << 16:
                assert st[0x308] & (1 << 23), "temperature read with TSEN off"
                return t16 // 16
            if ch == 1 << 17:
                assert st[0x308] & (1 << 22)
                return v17 // 16
            return 0
        if off == 0x08:
            return st[0x08] & ~4
        return st.get(off, 0)

    def w(uc, off, size, value, s):
        st[off] = value
        if off == 0x308:
            seen["ccr"].append(value)
    return (0x40012400, 0x400, r, w), st, seen


def go(tmp_path, stations, e_ppb, extra="", aden=1, sign=SIGN, dev=0.0, air_cls=None):
    air = (air_cls or Air)(stations, e_ppb, sign)
    mmio, rcc, tim, dma, sim = dma_bench(f_if=air.f_if, dev=dev)
    adc, ast, seen = adc_bench(aden=aden)
    regs = {**START, 0x73: 0x4682, 0x43: 0x3028, 0x3B: BASE3B, 0x3C: X3C, 0x67: air.rssi}
    ret, said, regs, writes = run(regs, src=small(tmp_path, extra), spare=1100, mmio=mmio + [adc], level=4,
                                  tune=air.tune, count=3_000_000_000)
    said = [ln for ln in said if not ln.startswith("R ")]    # (gp0dma's register dumps)
    return ret, said, regs, writes, air, ast, seen


def field(line, key):
    return next(w.split("=")[1] for w in line.split() if w.startswith(key + "="))


@need_clang
def test_builds_level_4_with_room_for_the_ring():
    from modem import k6mod
    code, entry = k6mod.build(SRC)
    assert k6mod.source_level(SRC) == 4 and entry == 0
    assert 11776 - 24 - len(code) >= 1100, len(code)        # the voice images' arena, and the ring


@need_clang
def test_scans_pilots_nulls_on_the_strongest_and_takes_the_median(tmp_path):
    # three pilots (RF 14 strongest, 26, 35) and a weak one under the threshold; the K6 330 ppb
    # off (ifcal's bench pair, 2026-11-06); RF 26 itself 1 kHz off (a bad pilot: the median
    # leaves it out)
    st = {PILOT(14): (-70, 0.0), PILOT(26): (-80, 1000.0), PILOT(35): (-76, 0.0), PILOT(20): (-106, 0.0)}
    ret, said, regs, writes, air, ast, seen = go(tmp_path, st, e_ppb=-330)
    assert said[0].startswith("refcal t=27728 v=24640 3b=5880 3c=4f88"), said[:3]
    scans = [ln.split() for ln in said if ln.startswith("refcal s ")]
    assert len(scans) == 30 and scans[0][2:] == ["470309.44", "-70", f"{FLOOR}"]
    assert scans[23][2] == "162400.00"
    step = next(ln for ln in said if ln.startswith("refcal step "))
    assert step.split()[2] == "470309.44"
    d, g = float(field(step, "d")), float(field(step, "g"))
    assert abs(d - SIGN * g) < 0.1 * g, step                 # the probe: 6 steps, the chip's sign
    rs = [ln for ln in said if ln.startswith("refcal r ")]
    assert [ln.split()[2] for ln in rs] == ["470309.44", "596309.44", "542309.44"], rs
    ppbs = [int(field(ln, "ppb")) for ln in rs]
    assert abs(ppbs[0] + 330) <= 8 and abs(ppbs[1] + 330) <= 8, rs
    assert abs(ppbs[2] - (-330 - 1e12 / PILOT(26))) <= 8      # the bad pilot reads 1 kHz off
    assert int(field(rs[0], "k")) == -2, rs                  # -330 / 192.3 = -1.7 steps
    assert all(abs(float(field(ln, "res"))) < 60 for ln in rs[:2])          # near the null
    last = said[-2]
    assert last.startswith("refcal ppb=") and last.endswith(" dry") and abs(ret + 330) <= 8, said[-4:]
    assert field(last, "n") == "3" and field(last, "t") == "27728"
    # put back: REG_3B, the AFC, GP0, the ADC, the clocks; back on the pager's frequency
    assert regs[0x3B] == BASE3B and regs[0x73] == 0x4682 and regs[0x34] == 0
    assert air.tunes[-1][1] == 440_850_000
    assert ast[0x308] == 0 and ast[0x28] == 1 << 1 and ast[0x14] == 3
    assert said[-1] == "refcal done" and all(len(ln) <= 60 for ln in said)


@need_clang
def test_apply_keeps_the_trim(tmp_path):
    st = {PILOT(14): (-70, 0.0)}
    ret, said, regs, *_ = go(tmp_path, st, e_ppb=+900, extra="#define REFCAL_APPLY 1\n")
    last = said[-2]
    assert last.endswith(" set") and abs(ret - 900) <= 8, said[-5:]
    k = int(field(last, "k"))
    assert k == 5 and regs[0x3B] == BASE3B + k               # 900 / 192.3 = 4.7 steps
    assert field(last, "n") == "1"


@need_clang
def test_no_reference_keeps_nothing(tmp_path):
    ret, said, regs, *_ = go(tmp_path, {PILOT(20): (-106, 0.0)}, e_ppb=-330)
    assert ret == -6 and "refcal no reference" in said and regs[0x3B] == BASE3B


@need_clang
def test_scan_only_and_an_adc_left_off(tmp_path):
    ret, said, regs, writes, air, ast, seen = go(tmp_path, {PILOT(14): (-70, 0.0)}, e_ppb=0,
                                                 extra="#define REFCAL_SCAN_ONLY 1\n", aden=0)
    assert ret == 0 and said[0].startswith("refcal t=0 v=0 ")
    assert not seen["ccr"]                                   # the ADC untouched while off
    assert sum(ln.startswith("refcal s ") for ln in said) == 30
    assert not any(ln.startswith(("refcal m ", "refcal step")) for ln in said)
    assert regs[0x73] == 0x4682 and regs[0x3B] == BASE3B


@need_clang
@pytest.mark.parametrize("sign", [-1, 1])
def test_either_mixing_side_gives_the_same_crystal_error(tmp_path, sign):
    ret, said, regs, *_ = go(tmp_path, {PILOT(35): (-75, 0.0)}, e_ppb=-1200, sign=sign)
    step = next(ln for ln in said if ln.startswith("refcal step "))
    assert (float(field(step, "d")) > 0) == (sign > 0)
    assert abs(ret + 1200) <= 8 and field(said[-2], "k") == "-6", said[-4:]   # -1200 / 192.3


@need_clang
def test_a_weather_station_with_voice_on_it(tmp_path):
    # FM voice (modelled as a 1 kHz tone at 3 kHz deviation): its mean IF is still the carrier;
    # read twice as long
    ret, said, regs, *_ = go(tmp_path, {162_550_000: (-80, 0.0)}, e_ppb=+500, dev=3000.0)
    ms = [ln for ln in said if ln.startswith("refcal m 162550.00")]
    assert ms and all(field(ln, "n") in ("16", "8") for ln in ms), ms
    assert abs(ret - 500) <= 15, said[-4:]


@need_clang
def test_a_crystal_4_ppm_off_still_nulls(tmp_path):
    # 4 ppm at 596 MHz puts the carrier 2.4 kHz off the nominal IF before the first null
    ret, said, regs, *_ = go(tmp_path, {PILOT(35): (-75, 0.0)}, e_ppb=+4000, extra="#define REFCAL_APPLY 1\n")
    assert abs(ret - 4000) <= 8 and regs[0x3B] == BASE3B + 21, said[-4:]   # 4000 / 192.3 = 20.8


@need_clang
def test_the_bench_carrier_on_440850(tmp_path):
    # the Pluto as the go-between (2026-11-17): refcal for 440.850 alone, the module's upload
    # followed by a PSK31 'AG7EW TEST' carrier (modelled as a 1 kHz tone at 600 Hz deviation)
    # strong at the K6; the K6 1.5 ppm slow
    ret, said, regs, *_ = go(tmp_path, {440_850_000: (-63, 0.0)}, e_ppb=-1500, dev=600.0,
                             extra="#define REFCAL_HZ 440850000\n")
    scans = [ln for ln in said if ln.startswith("refcal s ")]
    assert scans == ["refcal s 440850.00 -63 -112"], scans
    assert abs(ret + 1500) <= 8 and field(said[-2], "k") == "-8" and said[-2].endswith(" dry"), said[-5:]
    assert regs[0x3B] == BASE3B


@need_clang
def test_a_reference_failing_the_probe_moves_on_to_the_next(tmp_path):
    # RF 14 strongest, but the IF there follows something else (TV data: 3 kHz high, the steps
    # barely moving it); RF 35 behind it is a clean carrier
    class Swamped(Air):
        def f_if(self, t, regs):
            if abs(self.hz - PILOT(14)) < 3_000 and regs.get(0x73, 0) & 0x10:
                return NOMINAL + 3100 + 0.25 * (Air.f_if(self, t, regs) - NOMINAL) + 0 * t
            return Air.f_if(self, t, regs)
    ret, said, regs, *_ = go(tmp_path, {PILOT(14): (-70, 0.0), PILOT(35): (-76, 0.0)}, e_ppb=-330,
                             air_cls=Swamped)
    steps = [ln.split()[2] for ln in said if ln.startswith("refcal step ")]
    assert steps == ["470309.44", "596309.44"], said
    assert "refcal the probe moved the IF wrongly" in said
    rs = [ln.split()[2] for ln in said if ln.startswith("refcal r ")]
    assert rs == ["596309.44"] and abs(ret + 330) <= 8, said[-4:]


@need_clang
def test_every_probe_failing_keeps_nothing(tmp_path):
    class Flat(Air):
        def f_if(self, t, regs):
            return NOMINAL + 3100 + 0 * t if regs.get(0x73, 0) & 0x10 and self.near()[0] else Air.f_if(self, t, regs)
    ret, said, regs, *_ = go(tmp_path, {PILOT(14): (-70, 0.0), PILOT(20): (-72, 0.0)}, e_ppb=-330,
                             air_cls=Flat)
    assert ret == -12 and "refcal no reference passed the probe: nothing kept" in said
    assert regs[0x3B] == BASE3B
