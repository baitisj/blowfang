"""The mic probe modules (docs/k6-mic-probe.md) on an emulated Cortex-M0, against models of what
they measure, and their reader (tools/k6_micprobe.py):

  voxcap  PA0's ADC with a modelled VOX detector (a rectifier and a hold after the speaker's
          tone), TIM17 as its 16 kHz clock, TIM1's PWM as the tone
  afpin   PA11 as a one-pin delta-sigma converter: the board's two RC sections, the pin's
          Schmitt input, the FD6818's audio output behind 22k
SPDX-License-Identifier: MIT"""
import math
import os
import random
import sys
import tempfile

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, need_clang  # noqa: E402
from test_k6_i2s import run as run_k6  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import k6mod  # noqa: E402
import k6_micprobe as mp  # noqa: E402

VOXCAP = os.path.join(ROOT, "c", "k6", "mod", "voxcap.c")
AFPIN = os.path.join(ROOT, "c", "k6", "mod", "afpin.c")
MICPROBE = os.path.join(ROOT, "c", "k6", "mod", "micprobe.c")
MICPIN = os.path.join(ROOT, "c", "k6", "mod", "micpin.c")
MICREC = os.path.join(ROOT, "c", "k6", "mod", "micrec.c")
MICLVL = os.path.join(ROOT, "c", "k6", "mod", "miclvl.c")
MICDISC = os.path.join(ROOT, "c", "k6", "mod", "micdisc.c")
MICKEY = os.path.join(ROOT, "c", "k6", "mod", "mickey.c")


def build(src, defs=()):
    if not defs:
        return k6mod.build(src)
    with tempfile.TemporaryDirectory() as d:
        w = os.path.join(d, "w.c")
        with open(w, "w") as f:
            f.writelines(f"#define {k} {v}\n" for k, v in defs)
            f.write(f'#include "{src}"\n')
        return k6mod.build(w)


def now_ns(st):
    return st["us"] * 1000 + st.get("ns", 0)


def tick(st, ns):
    st["ns"] = st.get("ns", 0) + ns
    st["us"] += st["ns"] // 1000
    st["ns"] %= 1000


# ---- voxcap ----

def vox_world(rise_ms=1.0, fall_ms=12.0, ripple=0.2):
    """the MCU's registers voxcap touches, and the detector: PA0 = 500 + the speaker tone's
    swing held by a fast-attack, slow-release detector, with some of the waveform left on it"""
    w = {"rcc": {0x18: 0}, "tim1": {}, "tim17": {}, "gpioa": {0x00: 0x28000000, 0x08: 0, 0x24: 0},
         "adc": {0x08: 1, 0x14: 7, 0x28: 0x2}, "amp": [], "env": 0.0, "t_env": 0, "done": None, "ccr": []}

    def rcc_r(uc, off, size, st):
        return w["rcc"].get(off, 0)

    def rcc_w(uc, off, size, value, st):
        w["rcc"][off] = value

    def t17_r(uc, off, size, st):
        if off == 0x24:
            tick(st, 4000)
            if w["tim17"].get(0x00, 0) & 1:
                return (now_ns(st) * 96 // 1000 // (w["tim17"].get(0x28, 0) + 1)) & 0xFFFF
            return 0
        return w["tim17"].get(off, 0)

    def t17_w(uc, off, size, value, st):
        assert w["rcc"][0x18] & (1 << 18), "TIM17 written with its clock off"
        w["tim17"][off] = value

    def t1_r(uc, off, size, st):
        return w["tim1"].get(off, 0)

    def t1_w(uc, off, size, value, st):
        assert w["rcc"][0x18] & (1 << 11), "TIM1 written with its clock off"
        w["tim1"][off] = value
        if off == 0x40:
            w["ccr"].append((now_ns(st), value))

    def level(st):
        """the detector at this moment"""
        t = now_ns(st)
        ccr = w["tim1"].get(0x40, 1000)
        playing = (w["tim1"].get(0x00, 0) & 1 and w["tim1"].get(0x44, 0) & 0x8000 and w["amp"] and w["amp"][-1]
                   and w["gpioa"][0x00] >> 22 & 3 == 2)
        swing = abs(ccr - 1000) * 0.5 if playing else 0.0
        dt = (t - w["t_env"]) / 1e6
        tau = rise_ms if swing > w["env"] else fall_ms
        w["env"] += (swing - w["env"]) * (1 - math.exp(-dt / tau))
        w["t_env"] = t
        return 500 + w["env"] * (1 + (ripple if ccr > 1000 else -ripple if ccr < 1000 else 0))

    def adc_r(uc, off, size, st):
        if off == 0x00:
            tick(st, 2000)
            return 4 if w["done"] is not None and now_ns(st) >= w["done"] else 0
        if off == 0x40:
            assert w["done"] is not None and now_ns(st) >= w["done"], "DR read before EOC"
            assert w["adc"][0x28] == 1, "not channel 0"
            w["done"] = None
            return int(level(st)) & 0xFFF
        return w["adc"].get(off, 0)

    def adc_w(uc, off, size, value, st):
        if off == 0x08:
            assert value & ~5 == 0
            if value & 4:
                level(st)
                w["done"] = now_ns(st) + 18000
        elif off == 0x28:
            w["adc"][off] = value
        else:
            raise AssertionError(f"ADC+{off:#x} written")

    def ga_r(uc, off, size, st):
        return w["gpioa"].get(off, 0)

    def ga_w(uc, off, size, value, st):
        assert off in (0x00, 0x08, 0x24), hex(off)
        w["gpioa"][off] = value

    def gpiob(off, value, st):
        assert off in (0x18, 0x28) and value == 4, (hex(off), value)
        w["amp"].append(off == 0x18)
    mmio = [(0x40021000, 0x400, rcc_r, rcc_w), (0x40014800, 0x400, t17_r, t17_w), (0x40012C00, 0x400, t1_r, t1_w),
            (0x40012400, 0x400, adc_r, adc_w), (0x48000000, 0x400, ga_r, ga_w)]
    return w, mmio, gpiob


@need_clang
def test_voxcap_sees_the_detector_and_puts_everything_back():
    w, mmio, gpiob = vox_world()
    ret, said, regs, writes = run_k6({0x64: 0x000a}, src=VOXCAP, mmio=mmio, gpiob=gpiob, level=4, spare=8192,
                                     count=400_000_000)
    assert ret == 8 and writes == [] and said[0].startswith("voxcap start cr=00000001 smpr=7 ch=00000002 r64=000a")
    assert said[-1] == "voxcap done" and all(len(s) <= 60 for s in said)
    # everything back: the timers off and unclocked, PA11's mode, the ADC's channel, the amplifier
    assert w["rcc"][0x18] == 0 and not w["tim17"][0x00] & 1 and not w["tim1"][0x00] & 1
    assert not w["tim1"][0x44] & 0x8000 and w["gpioa"] == {0x00: 0x28000000, 0x08: 0, 0x24: 0}
    assert w["adc"][0x28] == 0x2 and w["amp"] == [True, False]
    sec = mp.sections(mp.say_lines("\n".join("12:00:00 K6 MOD say " + s for s in said)), "voxcap")
    assert len(sec) == 1
    caps = mp.captures(sec[0], "voxcap", 3)
    assert list(caps) == list("QSabcdef")
    assert [len(caps[t][0]) for t in caps] == [1026, 3078, 1026, 1026, 1026, 1026, 1026, 1026]
    assert all(i["late"] == 0 and not i["short"] for _, i in caps.values())
    q, s = caps["Q"][0], caps["S"][0]
    assert q.min() == q.max() == 500                                  # no tone, speaker off
    assert s[:500].max() <= 501 and s[900:1500].min() > 700 and s[-500:].max() < 520
    c1, _ = mp.tone_fit(caps["c"][0], mp.VOX_FS, 1000.0)
    a1, _ = mp.tone_fit(caps["a"][0], mp.VOX_FS, 16000 / 54)
    e1, _ = mp.tone_fit(caps["e"][0], mp.VOX_FS, 1000.0)
    assert 50 < c1 < 100 and 50 < a1 < 100 and 10 < e1 < 30           # 0.2 of the level, as a square wave
    rep = mp.vox_report(caps)
    assert "rise" in rep[1] and "tone 1000 Hz" in rep[4] and "tone 296 Hz" in rep[2]
    rise = float(rep[1].split("rise ")[1].split(" ms")[0])
    fall = float(rep[1].split("fall ")[1].split(" ms")[0])
    assert 0.3 < rise < 3 and 6 < fall < 20, rep[1]


@need_clang
def test_voxcap_refuses_while_something_plays_or_without_room():
    w, mmio, gpiob = vox_world()
    w["tim1"][0x00] = 0x81                                            # a message playing
    ret, said, regs, writes = run_k6({0x64: 0x000a}, src=VOXCAP, mmio=mmio, gpiob=gpiob, level=4, spare=8192)
    assert ret == -7 and said == [] and w["rcc"][0x18] == 0 and w["amp"] == []
    w, mmio, gpiob = vox_world()
    ret, said, *_ = run_k6({0x64: 0x000a}, src=VOXCAP, mmio=mmio, gpiob=gpiob, level=4, spare=4096)
    assert ret == -8 and said == []


# ---- afpin ----

CYC = 1 / 96e6
T0_TURN, T0_DRIVE = 30 * CYC, 8 * CYC                  # way 0's loop: a turn, the pin's time as an output
T1_TURN, T1_UP, T1_DOWN = 59 * CYC, 6 * CYC, 21 * CYC  # way 1: a turn, its fixed push up, its pull down
R1, C1, R2, C2, R3 = 4700.0, 3.9e-9, 4700.0, 22e-9, 22000.0 + 2200.0
V_HI, V_LO, VDD = 1.6, 1.3, 3.3


def af_world(tone_mv=60.0, hiss_mv=40.0, carrier_after=None, seed=3, src=None):
    """PA11, its capacitors and the AF output behind them. The pin's Schmitt input keeps its
    state between the thresholds, and driving the pin sets it. The AF output: silent while
    REG_47's select is 0; with received audio selected, hiss until carrier_after reads of the
    pin (None: never), then a 1 kHz tone (its resting level the pin's lower threshold: the 1 uF
    capacitor in the way lets no DC through). The model tells the two ways apart by their order of
    writes (way 1 sets the output bit before it reads) and times them from the loops' cycles.
    (Way 0, the plain loop, was afpin's first try: dead on the radio, and gone from the module.)"""
    w = {"moder": 0x28000000, "odr": 0, "ospeed": 0, "vc": V_LO, "v2": V_LO, "sch": 0, "turns": 0, "regs": None,
         "t": 0.0, "rng": random.Random(seed), "noise": 0.0, "way1": False, "last": "", "spent": 0.0, "pre": False}

    def source():
        regs = w["regs"]
        if src is not None:                        # the test's own AF output, volts about the rest level
            return V_LO + src(w, regs or {})
        if regs is None or regs.get(0x47, 0) >> 8 & 15 != 1:
            return V_LO
        if carrier_after is not None and w["turns"] >= carrier_after:
            return V_LO + tone_mv / 1000 * math.sin(2 * math.pi * 1000 * w["t"])
        w["noise"] += (w["rng"].gauss(0, hiss_mv / 1000 * 6) - w["noise"]) * 0.03   # a few kHz wide
        return V_LO + w["noise"]

    def step(dt, driven):
        vs = source()
        i12 = (w["v2"] - w["vc"]) / R2
        idrv = ((VDD if w["odr"] >> 11 & 1 else 0.0) - w["vc"]) / R1 if driven else 0.0
        w["vc"] += (i12 + idrv) * dt / C1
        w["v2"] += ((vs - w["v2"]) / R3 - i12) * dt / C2
        w["t"] += dt
        w["spent"] += dt

    def ga_r(uc, off, size, st):
        if off == 0x10:
            assert w["moder"] >> 22 & 3 == 0, "PA11 read while it drives"
            w["regs"] = st["regs"]
            step(max(0.0, (T1_TURN if w["way1"] else T0_TURN) - w["spent"]), False)   # the rest of the turn
            w["spent"] = 0.0
            w["turns"] += 1
            w["sch"] = 1 if w["vc"] > V_HI else 0 if w["vc"] < V_LO else w["sch"]
            w["last"] = "read"
            return w["sch"] << 11
        return {0x00: w["moder"], 0x08: w["ospeed"], 0x0C: 0x24000000, 0x14: w["odr"]}.get(off, 0)

    def ga_w(uc, off, size, value, st):
        if off == 0x18:
            assert value in (1 << 11, 1 << 27), hex(value)
            if w["last"] != "read":
                w["way1"] = True
            elif value == 1 << 11:
                w["way1"] = False
            w["pre"] = w["last"] != "read"
            w["odr"] = (w["odr"] | value & 0xFFFF) & ~(value >> 16)
        elif off == 0x00:
            assert value & ~(3 << 22) == 0x28000000 & ~(3 << 22) and value >> 22 & 3 in (0, 1), hex(value)
            if w["moder"] >> 22 & 3 == 1 and value >> 22 & 3 == 0:
                assert w["ospeed"] >> 22 & 3 == 3, "PA11 driven at low speed"
                step(T1_UP if w["pre"] else T1_DOWN if w["way1"] else T0_DRIVE, True)
                w["sch"] = w["odr"] >> 11 & 1                    # the driven pin sets the Schmitt input
            w["moder"] = value
        elif off == 0x08:
            assert value & ~(3 << 22) == 0, hex(value)
            w["ospeed"] = value
        else:
            raise AssertionError(f"GPIOA+{off:#x} written")
        w["last"] = "write"
    return w, [(0x48000000, 0x400, ga_r, ga_w)]


AF_REGS = {0x47: 0x6040, 0x48: 0xb3a8, 0x67: 60}
AF_N, AF_SEL, AF_PRE = 598, 130, 400
AF_DEFS = [("N", f"{AF_N}u"), ("N_SEL", f"{AF_SEL}u"), ("PRE", f"{AF_PRE}u"), ("WAIT_MS", "900u"), ("HOLD_MS", "200u")]
AF_FS = {1: 1 / (90 * T1_TURN)}
AF_FIRST = (2 * (AF_N + AF_PRE) + 16 * (AF_SEL + AF_PRE)) * 90   # reads of the pin before the carrier's wait
AF_TAGS = "mh" + "ABCDEFGHIJKLMNOP"


def af_caps(said):
    return mp.captures(mp.sections(mp.say_lines("\n".join(said)), "afpin")[0], "afpin", 4)


def af_y(cap):
    return cap[0] / cap[1]["w"] ** 2


def af_amp():
    """the module's writes to the amplifier's pin"""
    seen = []

    def gpiob(off, value, st):
        assert off in (0x18, 0x28) and value == 4, (hex(off), value)
        seen.append(off == 0x18)
    return seen, gpiob


@need_clang
def test_afpin_reads_the_audio_output():
    w, mmio = af_world(carrier_after=AF_FIRST)
    amp, gpiob = af_amp()
    regs = dict(AF_REGS)
    # the carrier: a short one first (the uploader's CW ID), a gap, then the one that stays
    t_on = []

    def rssi(us):
        if w["turns"] < AF_FIRST:
            return 60
        t_on.append(us)
        ms = (us - t_on[0]) / 1000
        return 60 if ms < 50 or 150 <= ms < 250 else 250
    regs[0x67] = rssi
    ret, said, regs, writes = run_k6(regs, src=AFPIN, code=build(AFPIN, AF_DEFS), mmio=mmio, gpiob=gpiob, level=4,
                                     spare=4096, count=1_500_000_000)
    assert ret == 24, said[-3:]
    assert said[0] == "afpin start 47=6040 48=b3a8 moder=28000000 pupdr=24000000" and said[-1] == "afpin done"
    assert "afpin carrier rssi=-31" in said and all(len(s) <= 60 for s in said)
    # the carrier that came and went did not count: the tone's captures start 200 ms into the second
    at = said.index("afpin carrier rssi=-31")
    assert said[at - 1].startswith("afpin P ") and said[at + 1].startswith("t 000 ")
    # only REG_47's select changed, and it is back; PA11 an input again, at its old speed; the amplifier off
    assert {r for r, _ in writes} == {0x47} and regs[0x47] == 0x6040
    assert [v for _, v in writes] == [0x6040 | s << 8 for s in [0, 1] + list(range(16)) + [1]] + [0x6040]
    assert w["moder"] == 0x28000000 and w["odr"] == 0 and w["ospeed"] == 0 and amp == [False]
    caps = af_caps(said)
    assert list(caps) == list(AF_TAGS + "tuvwxy")
    assert [len(caps[t][0]) for t in caps] == [AF_N] * 2 + [AF_SEL] * 16 + [AF_N] * 6
    assert [caps[t][1]["hold"] for t in "mhAPtuvwxy"] == [3, 3, 5, 5, 5, 3, 2, 8, 5, 5]
    assert [caps[t][1]["w"] for t in "mAtx"] == [90, 90, 90, 180]
    assert [caps[t][1]["sel"] for t in AF_TAGS + "t"] == [0, 1] + list(range(16)) + [1]
    assert caps["m"][1]["rssi"] == -144 and caps["t"][1]["rssi"] == -49
    # muted: the converter idles quietly; with the tone: 1 kHz, well clear of its own noise
    m = af_y(caps["m"])
    assert 0.2 < m.mean() < 0.8 and mp.band_rms(m, AF_FS[1], 300, 3000) < 0.0005
    f0, amp1, sinad = mp.peak_tone(af_y(caps["u"]), AF_FS[1])     # (the model times every pull-down as drive time 3's)
    assert abs(f0 - 1000) < 30 and amp1 > 0.012 and sinad > 25, (f0, amp1, sinad)
    # the select sweep: only select 1 (received audio) carries the hiss
    loud = {t: mp.band_rms(af_y(caps[t]), AF_FS[1], 300, 3000) for t in AF_TAGS[2:]}
    assert max(loud, key=loud.get) == "B" and loud["B"] > 5 * max(v for t, v in loud.items() if t != "B"), loud
    rep = mp.af_report(caps, fs=AF_FS)
    assert len(rep) == 24 and "SINAD" in rep[-1] and "dB on muted" in rep[1] and "sel 15" in rep[17]
    assert "rssi -49 dBm" in rep[-1] and "fs=%.0f Hz" % (AF_FS[1] / 2) in rep[-2]


@need_clang
def test_afpin_hears_hiss_and_gives_up_without_a_carrier():
    w, mmio = af_world()
    amp, gpiob = af_amp()
    code = build(AFPIN, AF_DEFS)
    regs = dict(AF_REGS)
    regs[0x67] = lambda us: 250 if (us // 100_000) % 2 else 60     # a carrier that never stays 200 ms
    ret, said, regs, writes = run_k6(regs, src=AFPIN, code=code, mmio=mmio, gpiob=gpiob, level=4, spare=4096,
                                     count=900_000_000)
    assert ret == 18 and said[-2:] == ["afpin no carrier", "afpin done"] and regs[0x47] == 0x6040
    caps = af_caps(said)
    assert list(caps) == list(AF_TAGS)
    quiet = mp.band_rms(af_y(caps["m"]), AF_FS[1], 300, 3000)
    hiss = mp.band_rms(af_y(caps["h"]), AF_FS[1], 300, 3000)
    assert hiss > 5 * quiet, (quiet, hiss)
    ret, said, *_ = run_k6(AF_REGS, src=AFPIN, code=code, mmio=af_world()[1], gpiob=af_amp()[1], level=4, spare=1024)
    assert ret == -8 and said == []


@need_clang
def test_voxcap_gives_up_on_a_dead_adc():
    w, mmio, gpiob = vox_world()
    adc = mmio[3]
    mmio[3] = (adc[0], adc[1], lambda uc, off, size, st: 0 if off == 0x00 else adc[2](uc, off, size, st), adc[3])
    ret, said, regs, writes = run_k6({0x64: 0x000a}, src=VOXCAP, mmio=mmio, gpiob=gpiob, level=4, spare=8192)
    assert ret == -5 and said[-2:] == ["voxcap: no clock or no ADC answer", "voxcap done"]
    assert w["rcc"][0x18] == 0 and w["gpioa"][0x00] == 0x28000000 and w["adc"][0x28] == 0x2


# ---- micprobe ----

MIC_N, MIC_PRE, MIC_GPW = 104, 200, 40
MIC_DEFS = [("N_CAP", f"{MIC_N}u"), ("PRE", f"{MIC_PRE}u"), ("GP_W", f"{MIC_GPW}u"), ("QUIET_WAIT_MS", "3000u"),
            ("HOLD_MS", "200u"), ("WAIT_MS", "3000u")]
MIC_REGS = {0x30: 0xBFF1, 0x36: 0x007F, 0x47: 0x6040, 0x48: 0x82DF, 0x33: 0x9100, 0x34: 0x0007, 0x43: 0x3028,
            0x7D: 0xE952, 0x6F: 0x0020}
MIC_FS = {"af": 1 / (90 * T1_TURN), "gp": 16000.0}
MIC_FULL = {"af": 8100.0, "gp": float(MIC_GPW)}
HOME_HZ, SOUND_HZ = 440_850_000, 445_850_000


def mic_world(af_sel=9, gp_sel=7, sound=True, tail_us=400_000):
    """The radio micprobe looks at, with a mic route to find: while REG_30's mic bit is set and
    the room sounds, a 1 kHz tone on the AF pin at REG_47 select af_sel, GP0's share of ones
    following it at REG_34 select gp_sel, and REG_64 up. The room sounds (and 445.850 carries
    the bench's signal) from 100 ms after the module first tunes there; 440.850 carries the
    upload's tail until tail_us. A tune puts REG_30 back to the core's receive word. The chip's
    bus pins (PB3, PB5, PB6) are decoded: a write lands in the registers and in w["bus"]."""
    w, mmio = af_world(src=lambda w, regs: (0.05 * math.sin(2 * math.pi * 1000 * w["t"])
                                             if regs.get(0x47, 0) >> 8 & 15 == af_sel and micd(regs) else 0.0))
    w.update(tuned=HOME_HZ, sound_at=None, us=0, bus=[], amp=[], tunes=[], pins={"scn": 1, "sck": 0, "sda": 0},
             bits=None, adc={0x28: 0x2}, adc_writes=[])

    def micd(regs):
        return bool(regs.get(0x30, 0) & 4) and w["sound_at"] is not None and w["us"] >= w["sound_at"]

    def gpiob(off, value, st):
        assert off in (0x18, 0x28), hex(off)
        w["us"], pins = st["us"], w["pins"]
        level = int(off == 0x18)
        if value == 4:
            w["amp"].append(bool(level))
        elif value == 1 << 3:
            if pins["scn"] and not level:
                w["bits"] = []
            elif not pins["scn"] and level and w["bits"] is not None:
                assert len(w["bits"]) == 24, len(w["bits"])
                word = int("".join(map(str, w["bits"])), 2)
                assert not word >> 23, "a read on the bus"
                w["bus"].append((word >> 16, word & 0xFFFF, st["regs"].get(0x30), st["regs"].get(0x36)))
                st["regs"][word >> 16] = word & 0xFFFF
                w["bits"] = None
            pins["scn"] = level
        elif value == 1 << 5:
            if not pins["sck"] and level and not pins["scn"]:
                w["bits"].append(pins["sda"])
            pins["sck"] = level
        elif value == 1 << 6:
            pins["sda"] = level
        else:
            raise AssertionError(f"GPIOB+{off:#x} = {value:#x}")

    def tune(hz, wide, us):
        assert wide == 1
        w["tuned"], w["us"] = hz, us
        w["tunes"].append(hz)
        if hz == SOUND_HZ and w["sound_at"] is None and sound:
            w["sound_at"] = us + 100_000
        if w["regs"] is not None:
            w["regs"][0x30] = 0xBFF1

    def rssi(us):
        w["us"] = us
        if w["tuned"] == HOME_HZ:
            return 250 if us < tail_us else 60
        return 250 if w["sound_at"] is not None and us >= w["sound_at"] else 60

    def r64(us):
        w["us"] = us
        return 0x0150 if micd(w["regs"] or {}) else 0x000a

    def gp0(sel, reads, regs, us):
        w["us"] = us
        if sel == 13:
            return reads >> 3 & 1
        if sel == gp_sel and micd(regs):
            return int(w["rng"].random() < 0.5 + 0.3 * math.sin(2 * math.pi * reads / (16 * MIC_GPW)))
        return 0

    def adc_r(uc, off, size, st):
        return {0x00: 4, 0x40: 9}.get(off, w["adc"].get(off, 0))

    def adc_w(uc, off, size, value, st):
        assert off in (0x08, 0x28), hex(off)
        w["adc"][off] = value
        w["adc_writes"].append((off, value))
    regs = dict(MIC_REGS)
    regs[0x67], regs[0x64] = rssi, r64
    return w, regs, mmio + [(0x40012400, 0x400, adc_r, adc_w)], gpiob, tune, gp0


def mic_run(level=4, **kw):
    w, regs, mmio, gpiob, tune, gp0 = mic_world(**kw)
    ret, said, regs, writes = run_k6(regs, src=MICPROBE, code=build(MICPROBE, MIC_DEFS), mmio=mmio, gpiob=gpiob,
                                     tune=tune, model=gp0, level=level, spare=4096, count=4_000_000_000)
    return w, ret, said, regs, writes


@need_clang
def test_micprobe_finds_a_mic_route_and_keeps_the_rule():
    w, ret, said, regs, writes = mic_run()
    assert ret == 6, said[-4:]
    assert said[0] == "micprobe start 30=bff1 47=6040 33=9100 34=0007 7d=e952 gp=1"
    assert said[-1] == "micprobe done" and all(len(s) <= 60 for s in said)
    assert "micprobe quiet rssi=-90" in said and "micprobe sound rssi=-31" in said and "micprobe check rssi=-31" in said
    # REG_30 over the bus pins only (this level's fd_write refuses it): the two bits and nothing else,
    # from the receive word, the PA off; fd_write was asked first each time
    assert [(r, v) for r, v, _, _ in w["bus"]] == [(0x30, 0xBFF5), (0x30, 0xBFF7), (0x30, 0xBFF1)] * 2
    assert all(was & ~6 == 0xBFF1 & ~6 and pa == 0x007F for _, _, was, pa in w["bus"])
    assert [v for r, v in writes if r == 0x30] == [0xBFF5, 0xBFF7, 0xBFF1] * 2
    assert {r for r, _ in writes} == {0x30, 0x47, 0x33, 0x34}
    # everything back: the registers, the tuning (wide, as found), PA11, the ADC's channel, the amplifier
    assert [regs[r] for r in (0x30, 0x47, 0x33, 0x34)] == [0xBFF1, 0x6040, 0x9100, 0x0007]
    assert w["tunes"] == [SOUND_HZ, HOME_HZ, SOUND_HZ, HOME_HZ]
    assert w["moder"] == 0x28000000 and w["odr"] == 0 and w["ospeed"] == 0 and w["amp"] == [False]
    assert w["adc"][0x28] == 0x2 and w["pins"] == {"scn": 1, "sck": 0, "sda": 0}
    runs = mp.mic_runs(mp.say_lines("\n".join("12:00:00 K6 MOD say " + s for s in said)))
    assert len(runs) == 1
    run = runs[0]
    assert len(run["caps"]) == 2 * 3 * (16 + 16 + 2) and set(run["regs"]) == set(run["a0"]) == {(q, s) for q in (0, 1)
                                                                                                    for s in range(3)}
    assert all(len(x) == MIC_N and not i["short"] for (k, *_), (x, i) in run["caps"].items() if k == "af")
    assert len(run["regs"][(0, 0)]) == 128 and run["regs"][(0, 1)][0x30] == (0xBFF5, 0xBFF5)
    assert run["regs"][(1, 2)][0x30] == (0xBFF7, 0xBFF7) and run["regs"][(0, 0)][0x5F] == (0xFFFF, 0)
    assert run["a0"][(1, 2)] == {"q": 1, "st": 2, "lo": 9, "hi": 9, "sum": 9 * 64}
    rep = mp.mic_report(run, fs=MIC_FS, full=MIC_FULL)
    assert rep[-1] == ("follows the sound: af st 1 (mic ADC) sel 9; af st 2 (mic ADC + TX DSP) sel 9; "
                       "gp st 1 (mic ADC) sel 7; gp st 2 (mic ADC + TX DSP) sel 7; "
                       "REG_64 st 1 (mic ADC); REG_64 st 2 (mic ADC + TX DSP)"), rep[-1]
    assert sum("the state moved it" in ln for ln in rep) == 2      # REG_30 itself, in states 1 and 2


@need_clang
def test_micprobe_uses_fd_write_where_the_image_takes_it_and_says_no_sound():
    w, ret, said, regs, writes = mic_run(level=14, sound=False)
    assert ret == 3 and said[-2:] == ["micprobe no sound", "micprobe done"]
    assert w["bus"] == [] and [v for r, v in writes if r == 0x30] == [0xBFF5, 0xBFF7, 0xBFF1]
    assert "micprobe state q=0 st=2 30=bff7 rssi=-90 via=0" in said
    assert regs[0x30] == 0xBFF1 and w["tunes"] == [SOUND_HZ, HOME_HZ]


@need_clang
def test_micprobe_never_touches_the_state_unless_receiving_with_the_pa_off():
    for reg, value in ((0x36, 0x00FF), (0x30, 0xC1FA), (0x30, 0xBFF9)):
        w, regs, mmio, gpiob, tune, gp0 = mic_world()
        regs[reg] = value
        ret, said, regs, writes = run_k6(regs, src=MICPROBE, code=build(MICPROBE, MIC_DEFS), mmio=mmio, gpiob=gpiob,
                                         tune=tune, model=gp0, level=4, spare=4096)
        assert ret == -5 and w["bus"] == [] and not [1 for r, _ in writes if r == 0x30], (reg, said)
        assert said[-1] == "micprobe done"


# ---- micpin ----

PIN_N, PIN_LONG = 598, 1300
PIN_DEFS = [("N", f"{PIN_N}u"), ("N_LONG", f"{PIN_LONG}u"), ("PRE", "400u"), ("QUIET_WAIT_MS", "3000u"),
            ("HOLD_MS", "200u"), ("WAIT_MS", "3000u")]


def pin_run(level=4, spare=4096, **kw):
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=4, **kw)
    ret, said, regs, writes = run_k6(regs, src=MICPIN, code=build(MICPIN, PIN_DEFS), mmio=mmio, gpiob=gpiob,
                                     tune=tune, model=gp0, level=level, spare=spare, count=2_000_000_000)
    return w, ret, said, regs, writes


@need_clang
def test_micpin_reads_the_mic_through_the_beep_pin_and_puts_everything_back():
    w, ret, said, regs, writes = pin_run()
    assert ret == 8, said[-4:]
    assert said[0] == "micpin start 30=bff1 47=6040 48=82df 7d=e952" and said[-1] == "micpin done"
    assert all(len(s) <= 60 for s in said) and "micpin sound rssi=-31" in said and "micpin check rssi=-31" in said
    # REG_30 by the rule only: both bits for Q; after the retune both again, the mic ADC alone for f,
    # both for L, then off (the last, at the end, finds them off already after the retune)
    assert [v for r, v, _, _ in w["bus"]] == [0xBFF7, 0xBFF7, 0xBFF5, 0xBFF7, 0xBFF1]
    assert all(r == 0x30 and pa == 0x007F for r, _, _, pa in w["bus"])
    assert {r for r, _ in writes} == {0x30, 0x47, 0x48, 0x7D}
    assert [regs[r] for r in (0x30, 0x47, 0x48, 0x7D)] == [0xBFF1, 0x6040, 0x82DF, 0xE952]
    assert (0x48, 0x83FF) in writes and (0x7D, 0xE95F) in writes
    assert w["tunes"] == [SOUND_HZ, HOME_HZ, SOUND_HZ, HOME_HZ]
    assert w["moder"] == 0x28000000 and w["odr"] == 0 and w["ospeed"] == 0 and w["amp"] == [False]
    caps = mp.captures(mp.sections(mp.say_lines("\n".join(said)), "micpin")[0], "micpin", 4)
    assert list(caps) == list("QabcdefL")
    assert [len(caps[t][0]) for t in caps] == [PIN_N] * 7 + [PIN_LONG]
    assert [caps[t][1]["sel"] for t in caps] == [4, 4, 4, 4, 4, 5, 4, 4]
    assert [caps[t][1]["hold"] for t in "QdL"] == [3, 2, 3] and [caps[t][1]["w"] for t in "QL"] == [90, 180]
    assert [caps[t][1][0x48 and "48"] for t in "Qabc"] == [0x82DF, 0x82DF, 0x83FF, 0x83FF]
    quiet = mp.band_rms(af_y(caps["Q"]), AF_FS[1], 300, 3000)
    for t in "abcf":                               # (the model: the mic's tone on select 4 whenever the mic ADC is on)
        f0, amp, sinad = mp.peak_tone(af_y(caps[t]), AF_FS[1])
        assert abs(f0 - 1000) < 30 and amp > 30 * quiet and sinad > 25, (t, f0, amp, sinad)
    assert mp.band_rms(af_y(caps["e"]), AF_FS[1], 300, 3000) < 3 * quiet
    f0, amp, sinad = mp.peak_tone(af_y(caps["L"]), AF_FS[1] / 2)
    assert abs(f0 - 1000) < 30 and sinad > 20, (f0, amp, sinad)
    rep = mp.af_report(caps, fs=AF_FS, name="micpin")
    assert rep[0].startswith("micpin Q: hold 3 w 90") and "dB on muted" in rep[1] and "sel 5" in rep[5]


@need_clang
def test_micpin_says_no_sound_and_refuses_a_keyed_chip_or_a_small_scratch():
    w, ret, said, regs, writes = pin_run(sound=False)
    assert ret == 1 and said[-2:] == ["micpin no sound", "micpin done"]
    assert [regs[r] for r in (0x30, 0x47, 0x48, 0x7D)] == [0xBFF1, 0x6040, 0x82DF, 0xE952]
    w, ret, said, regs, writes = pin_run(spare=1024)
    assert ret == -8 and said == [] and w["bus"] == []
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=4)
    regs[0x36] = 0x00FF
    ret, said, regs, writes = run_k6(regs, src=MICPIN, code=build(MICPIN, PIN_DEFS), mmio=mmio, gpiob=gpiob, tune=tune,
                                     model=gp0, level=4, spare=4096)
    assert ret == -5 and w["bus"] == [] and said[-1] == "micpin done"


# ---- miclvl ----

LVL_N = 299
LVL_DEFS = [("N", f"{LVL_N}u"), ("PRE", "400u"), ("QUIET_WAIT_MS", "3000u"), ("HOLD_MS", "200u"), ("WAIT_MS", "3000u"),
            ("RUN_MS", "700u")]


def lvl_run(spare=4096, **kw):
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=4, **kw)
    regs[0x19] = 0x1041
    ret, said, regs, writes = run_k6(regs, src=MICLVL, code=build(MICLVL, LVL_DEFS), mmio=mmio, gpiob=gpiob, tune=tune,
                                     model=gp0, level=4, spare=spare, count=2_000_000_000)
    return w, ret, said, regs, writes


@need_clang
def test_miclvl_takes_the_route_with_and_without_the_mic_adc_and_the_agc():
    w, ret, said, regs, writes = lvl_run()
    assert ret == 5, said[-4:]
    assert said[0] == "miclvl start 30=bff1 48=82df 19=1041 7d=e952" and said[-1] == "miclvl done"
    assert all(len(s) <= 60 for s in said) and "miclvl sound rssi=-31" in said and "miclvl check rssi=-31" in said
    # REG_30 by the rule only: both bits, both again after the retune, the TX DSP alone, both, then off
    assert [v for r, v, _, _ in w["bus"]] == [0xBFF7, 0xBFF7, 0xBFF3, 0xBFF7, 0xBFF1]
    assert all(r == 0x30 and pa == 0x007F for r, _, _, pa in w["bus"])
    assert {r for r, _ in writes} == {0x30, 0x47, 0x19} and (0x19, 0x9041) in writes
    assert [regs[r] for r in (0x30, 0x47, 0x19)] == [0xBFF1, 0x6040, 0x1041]
    assert w["tunes"] == [SOUND_HZ, HOME_HZ, SOUND_HZ, HOME_HZ]
    assert w["moder"] == 0x28000000 and w["odr"] == 0 and w["ospeed"] == 0 and w["amp"] == [False]
    caps = mp.captures(mp.sections(mp.say_lines("\n".join(said)), "miclvl")[0], "miclvl", 4)
    assert list(caps) == list("01234") and all(len(caps[t][0]) == LVL_N for t in caps)
    assert [caps[t][1]["st"] for t in caps] == [2, 2, 1, 2, 3]
    assert [caps[t][1]["19"] for t in caps] == [0x1041] * 4 + [0x9041]
    assert caps["0"][1]["ms"] == 0 and caps["1"][1]["ms"] < caps["2"][1]["ms"] < caps["4"][1]["ms"]
    amp = {t: mp.tone_fit(caps[t][0], AF_FS[1], 1000)[0] for t in caps}
    # (the model: the mic's tone on select 4 while the mic ADC is on and the room sounds)
    assert min(amp["1"], amp["3"], amp["4"]) > 30 * max(amp["0"], amp["2"]), amp
    rep = mp.lvl_report(caps)
    assert len(rep) == 5 and "mic ADC off" in rep[2] and "AGC bit flipped" in rep[4] and "19=9041" in rep[4]


@need_clang
def test_miclvl_says_no_sound_and_refuses_a_keyed_chip_or_a_small_scratch():
    w, ret, said, regs, writes = lvl_run(sound=False)
    assert ret == 1 and said[-2:] == ["miclvl no sound", "miclvl done"]
    assert [regs[r] for r in (0x30, 0x47, 0x19)] == [0xBFF1, 0x6040, 0x1041]
    w, ret, said, regs, writes = lvl_run(spare=512)
    assert ret == -8 and said == [] and w["bus"] == []
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=4)
    regs[0x36] = 0x00FF
    ret, said, regs, writes = run_k6(regs, src=MICLVL, code=build(MICLVL, LVL_DEFS), mmio=mmio, gpiob=gpiob, tune=tune,
                                     model=gp0, level=4, spare=4096)
    assert ret == -5 and w["bus"] == [] and said[-1] == "miclvl done"


# ---- micdisc ----

DISC_DEFS = [("N", "130u"), ("PRE", "300u"), ("QUIET_WAIT_MS", "3000u"), ("HOLD_MS", "200u"), ("WAIT_MS", "3000u")]


def disc_run(spare=4096, **kw):
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=1, **kw)
    regs[0x44], regs[0x7E] = 0x8ED8, 0x37EE
    ret, said, regs, writes = run_k6(regs, src=MICDISC, code=build(MICDISC, DISC_DEFS), mmio=mmio, gpiob=gpiob,
                                     tune=tune, model=gp0, level=4, spare=spare, count=4_000_000_000)
    return w, ret, said, regs, writes


@need_clang
def test_micdisc_tries_the_guides_disc_mode_on_every_select_and_puts_it_back():
    w, ret, said, regs, writes = disc_run()
    assert ret == 44, said[-4:]
    assert said[0] == "micdisc start 30=bff1 44=8ed8 47=6040 7e=37ee" and said[-1] == "micdisc done"
    assert all(len(s) <= 60 for s in said) and "micdisc sound rssi=-31" in said and "micdisc check rssi=-31" in said
    assert all(r == 0x30 and v in (0xBFF1, 0xBFF5, 0xBFF7) and pa == 0x007F for r, v, _, pa in w["bus"])
    assert {r for r, _ in writes} == {0x30, 0x44, 0x47} and {v for r, v in writes if r == 0x44} == {0x8ED8, 0x8ED9}
    assert [regs[r] for r in (0x30, 0x44, 0x47)] == [0xBFF1, 0x8ED8, 0x6040]
    assert w["tunes"] == [SOUND_HZ, HOME_HZ, SOUND_HZ, HOME_HZ]
    assert w["moder"] == 0x28000000 and w["odr"] == 0 and w["ospeed"] == 0 and w["amp"] == [False]
    caps = mp.captures(mp.sections(mp.say_lines("\n".join(said)), "micdisc")[0], "micdisc", 4)
    assert "".join(caps) == "0123456789abcdefghijklABCDEFGHIJKLMNOPQRSTUV"
    half = [(caps[t][1]["st"], caps[t][1]["d"], caps[t][1]["sel"]) for t in "0123456789abcdefghijkl"]
    assert half == [(0, 0, 1), (1, 0, 1), (0, 1, 1)] + [(1, 1, k) for k in range(16)] + [(2, 1, 1), (2, 1, 4), (2, 1, 9)]
    assert [caps[t][1]["44"] for t in "012l"] == [0x8ED8, 0x8ED8, 0x8ED9, 0x8ED9]
    amp = {t: mp.tone_fit(caps[t][0], AF_FS[1], 1000)[0] for t in caps}
    # (the model: the mic's tone on select 1 while the mic ADC is on and the room sounds)
    assert min(amp[t] for t in "BET") > 30 * max(amp[t] for t in "14jACDF"), amp
    rep = mp.cfg_report(caps, "micdisc", 1000)
    assert len(rep) == 44 and rep[4].startswith("micdisc 4: st=1 d=1 sel=1 44=8ed9")


@need_clang
def test_micdisc_says_no_sound_and_refuses_a_keyed_chip():
    w, ret, said, regs, writes = disc_run(sound=False)
    assert ret == 22 and said[-2:] == ["micdisc no sound", "micdisc done"]
    assert [regs[r] for r in (0x30, 0x44, 0x47)] == [0xBFF1, 0x8ED8, 0x6040]
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=1)
    regs[0x36], regs[0x44] = 0x00FF, 0x8ED8
    ret, said, regs, writes = run_k6(regs, src=MICDISC, code=build(MICDISC, DISC_DEFS), mmio=mmio, gpiob=gpiob,
                                     tune=tune, model=gp0, level=4, spare=4096)
    assert ret == -5 and w["bus"] == [] and regs[0x44] == 0x8ED8 and said[-1] == "micdisc done"


# ---- mickey ----

KEY_DEFS = [("N_MAX", "52u"), ("PRE", "300u"), ("QUIET_WAIT_MS", "3000u"), ("HOLD_MS", "200u"), ("WAIT_MS", "3000u")]


def key_run(level=14, spare=4096, tx=None, rx_hz=HOME_HZ, **kw):
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=1, **kw)
    tx = {} if tx is None else tx
    ret, said, regs, writes = run_k6(regs, src=MICKEY, code=build(MICKEY, KEY_DEFS), mmio=mmio, gpiob=gpiob, tune=tune,
                                     model=gp0, level=level, spare=spare, tx=tx, rx_hz=rx_hz, count=4_000_000_000)
    return w, tx, ret, said, regs, writes


@need_clang
def test_mickey_sends_its_id_then_reads_every_select_keyed_with_the_pa_off():
    w, tx, ret, said, regs, writes = key_run()
    assert ret == 40, said[-4:]
    assert said[0] == "mickey start 30=bff1 47=6040 rx=1a46d650" and said[-1] == "mickey done"
    assert all(len(s) <= 60 for s in said) and "mickey sound rssi=-31" in said
    assert [s for s in said if s.startswith("mickey keyed")] == ["mickey keyed q=0 30=c1fa 36=007f now=c3fa",
                                                                 "mickey keyed q=1 30=c1fa 36=007f now=c3fa"]
    log = tx["log"]
    ons = [i for i, (n, _, _) in enumerate(log) if n == "tx_on"]
    assert len(ons) == 2 and [log[i][1] for i in ons] == [20000, 20000]
    for i in ons:                                  # each keyed pass: AG7EW in CW (15 elements), then the PA stays off
        off = next(k for k in range(i, len(log)) if log[k][0] == "tx_off")
        pa = [(a, us) for n, a, us in log[i + 1:off]]
        marks = [(us1 - us0) // 60000 for (a0, us0), (a1, us1) in zip(pa, pa[1:]) if a0 == 1]
        assert marks == [1, 3, 3, 3, 1, 3, 3, 1, 1, 1, 1, 1, 3, 3], marks
        assert pa[-1][0] == 0
        # REG_30 gets the AF DAC and the mic ADC only after the last PA-off, and never another PA-on
        mic = [us for r, v, us in tx["w"] if r == 0x30 and v & 4 and log[i][2] < us < log[off][2]]
        assert mic and min(mic) > pa[-1][1]
    assert {v for r, v, _ in tx["w"] if r == 0x30} == {0xC3FE, 0xC3FA, 0xC1FA}
    assert regs[0x30] == 0xBFF1 and regs[0x47] == 0x6040 and w["bus"] == []
    assert w["tunes"] == [SOUND_HZ, HOME_HZ, HOME_HZ]
    assert w["moder"] == 0x28000000 and w["odr"] == 0 and w["ospeed"] == 0 and w["amp"] == [False]
    caps = mp.captures(mp.sections(mp.say_lines("\n".join(said)), "mickey")[0], "mickey", 4)
    assert "".join(caps) == "0123456789abcdefghijABCDEFGHIJKLMNOPQRST" and all(len(c[0]) == 52 for c in caps.values())
    assert [(caps[t][1]["m"], caps[t][1]["sel"]) for t in "0123456789abcdefghij"] == \
        [(1, k) for k in range(16)] + [(0, 1), (0, 3), (0, 4), (0, 9)]


@need_clang
def test_mickey_keys_nothing_off_the_bench_frequency_or_without_a_transmitter():
    w, tx, ret, said, regs, writes = key_run(rx_hz=441_850_000)
    assert ret == -2 and "log" not in tx and len(said) == 1
    w, tx, ret, said, regs, writes = key_run(tx={"on_ret": -1})
    assert ret == -3 and [n for n, _, _ in tx["log"]].count("tx_on") == 1 and said[-1] == "mickey done"
    assert not [1 for r, v, _ in tx["w"] if r == 0x30] and regs[0x30] == 0xBFF1
    w, tx, ret, said, regs, writes = key_run(spare=1024)
    assert ret == -8 and said == [] and "log" not in tx


# ---- micrec ----

REC_BYTES = 390
REC_DEFS = [("MAX_BYTES", f"{REC_BYTES}u"), ("PRE", "150u"), ("QUIET_WAIT_MS", "3000u"), ("HOLD_MS", "200u"),
            ("WAIT_MS", "3000u")]
REC_FS = 1 / (200 * T1_TURN)


def rec_run(defs=REC_DEFS, spare=4096, **kw):
    w, regs, mmio, gpiob, tune, gp0 = mic_world(af_sel=4, **kw)
    ret, said, regs, writes = run_k6(regs, src=MICREC, code=build(MICREC, defs), mmio=mmio, gpiob=gpiob, tune=tune,
                                     model=gp0, level=4, spare=spare, count=2_000_000_000)
    return w, ret, said, regs, writes


@need_clang
def test_micrec_records_the_mic_as_adpcm_and_the_reader_decodes_it():
    w, ret, said, regs, writes = rec_run()
    assert ret == 2 * REC_BYTES, said[-4:]
    assert said[0] == "micrec start 30=bff1 47=6040 48=82df 7d=e952" and said[-1] == "micrec done"
    assert all(len(s) <= 60 for s in said) and "micrec sound rssi=-31" in said and "micrec check rssi=-31" in said
    assert [v for r, v, _, _ in w["bus"]] == [0xBFF7, 0xBFF1] and {r for r, _ in writes} == {0x30, 0x47, 0x48}
    assert [regs[r] for r in (0x30, 0x47, 0x48)] == [0xBFF1, 0x6040, 0x82DF] and (0x47, 0x6440) in writes
    assert w["tunes"] == [SOUND_HZ, HOME_HZ, SOUND_HZ, HOME_HZ]
    assert w["moder"] == 0x28000000 and w["odr"] == 0 and w["ospeed"] == 0 and w["amp"] == [False]
    recs = mp.rec_runs(mp.say_lines("\n".join("12:00:00 K6 MOD say " + s for s in said)))
    assert len(recs) == 1
    x, info, notes = recs[0]
    assert len(x) == 2 * REC_BYTES and info["n"] == 2 * REC_BYTES and info["w"] == 200 and not info["short"]
    assert info["over"] == 0 and 0.2 < info["rest"] / 40000 < 0.8
    y = x[100:] / 40000                             # (the coder's first steps are its smallest: it takes a while)
    f0, amp, sinad = mp.peak_tone(y, REC_FS)
    assert abs(f0 - 1000) < 30 and amp > 0.01 and sinad > 20, (f0, amp, sinad)
    d = mp.deemph(x, REC_FS)
    assert len(d) == len(x) and abs(mp.tone_fit(d[100:], REC_FS, 1000.0)[0] / 40000 - amp * 0.2873) < 0.2 * amp * 0.2873
    # a lost line: zeros there, counted
    lost = [s for s in said if not s.startswith("p 001a ")]
    x2, info2, _ = mp.rec_runs(mp.say_lines("\n".join(lost)))[0]
    assert info2["short"] == 1 and len(x2) == len(x)


def test_the_reader_decodes_ima_adpcm_as_the_standard_does():
    # by hand: code 7 from rest is step/8 + step + step/2 + step/4 with steps 7, 16, 34; then code 0 at step 73
    assert mp.ima_decode(bytes([0x77, 0x07])) == [11, 41, 104, 113]
    assert mp.ima_decode(bytes([0x88])) == [0, 0] and mp.ima_decode(bytes([0xF7])) == [11, -19]


@need_clang
def test_micrec_at_once_without_the_bench_and_its_refusals():
    w, ret, said, regs, writes = rec_run(defs=REC_DEFS + [("NO_WAIT", "1")], sound=False)
    assert ret == 2 * REC_BYTES and w["tunes"] == [HOME_HZ] and not [s for s in said if "sound" in s or "check" in s]
    assert [v for r, v, _, _ in w["bus"]] == [0xBFF7, 0xBFF1]
    w, ret, said, regs, writes = rec_run(sound=False)
    assert ret == 0 and said[-2:] == ["micrec no sound", "micrec done"] and w["bus"] == []
    w, ret, said, regs, writes = rec_run(spare=512)
    assert ret == -8 and said == []
