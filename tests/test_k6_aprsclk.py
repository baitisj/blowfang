"""The K6 APRS clock module (c/k6/mod/aprsclk.c, docs/k6-aprs-clock.md).

APRS packets (modem/aprs.py: AX.25 UI frames as Bell 202 AFSK) go through the study's model of
the K6's receive path (sim/k6if: FM, the FD6818's filter and AGC, the GP0 code 13 sigma-delta at
the DMA's rate). The receiver after the DMA is built for this machine (c/k6/aprsclk_host.c) for
the demodulator's margins and the parser's and the clock vote's cases; then the whole module runs
on an emulated Cortex-M0 against the modelled DMA stream (tests/k6_live_emu.py): it must decode
every packet, set the clock within a second of the truth with no counts lost, keep its CPU margin,
and stay inside the arena and the stack. Needs a C compiler; the module test also clang, ld.lld
and unicorn. SPDX-License-Identifier: MIT"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K6 = os.path.join(ROOT, "c", "k6")
SRC = os.path.join(K6, "mod", "aprsclk.c")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem import aprs  # noqa: E402

need_clang = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                reason="needs clang and ld.lld")
DAY = 86400.0
UTC0 = 8 * DAY + 17 * 3600 + 52 * 60 + 0.25   # the true UTC at the module's start (day 8, 17:52:00.25)
FDMA = 5.49e6
CPS8 = int(round(96e6 * 256 / FDMA))


@pytest.fixture(scope="module")
def lib():
    if not shutil.which("cc"):
        pytest.skip("needs a C compiler")
    so = os.path.join(tempfile.mkdtemp(), "libaprsclk.so")
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-I" + K6,
                    os.path.join(K6, "aprsclk_host.c"), "-o", so], check=True)
    L = ctypes.CDLL(so)
    L.aprs_counts.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    L.aprs_frame.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32]
    L.aprs_lines.restype = ctypes.c_char_p
    return L


def result(L):
    r = (ctypes.c_int32 * 9)()
    L.aprs_result(r)
    return dict(zip("votes wid utc0 ok bad nts day mon yr".split(), r))


def truth_ms():
    return round((UTC0 % DAY) * 1000)


# ---- the traffic: three stations, each stamping a little before it keys ----
def packets():
    """[(start s, frames for the UTC at the first bit)]: a GPS tracker's HHMMSSh, a weather
    station's DDHHMMz through a digipeater, and a raw $GPRMC"""
    return [
        (0.5, lambda u: [aprs.frame("VE7ABC-9", "APRS", "/" + aprs.ts_hms(u - 0.4) + "4857.00N/12229.00W>hi")]),
        (2.6, lambda u: [aprs.frame("VE7WX", "APRS", "@" + aprs.ts_dhm(u - 20) + "4857.00N/12229.00W_090/000g000t050",
                                    path=("WIDE2-1",), repeated=1)]),
        (4.4, lambda u: [aprs.frame("N0CALL-7", "GPS", aprs.rmc(u - 0.7))]),
    ]


def scene(cn0=55.0, f_off=0.0, twist=0.0, dev=3000.0, seed=1, total=6.5, fdma=FDMA):
    """the DMA's bytes (bit 1 = PB1) for the packets on 144.390: dev is the louder tone's
    deviation (Hz), twist the space tone over the mark (dB)"""
    from sim.k6if import frontend as fe, study as st
    a = np.zeros(int(total * st.FSA))
    for t, mk in packets():
        x, t0 = aprs.packet_audio(mk(UTC0 + t + 40 * 8 / aprs.BAUD), fs=st.FSA, twist_db=twist)
        i = int(t * st.FSA)
        a[i:i + len(x)] += x
    s = st.fm(a * dev / max(1.0, 10 ** (twist / 20)), f_off)
    bits = fe.receive(s, st.FZ, cn0, bw=8000.0, seed=seed, L=0, fdma=fdma)
    return bits.astype(np.uint8) * 2


def counts(ib):
    b = (ib // 2).astype(np.int32)
    n = len(b) // 64
    return np.ascontiguousarray((2 * b[:n * 64].reshape(n, 64).sum(1) - 64).astype(np.int8))


def host_run(L, ib, cps8=CPS8):
    c = counts(ib)
    L.aprs_reset(cps8)
    L.aprs_counts(c.ctypes.data, len(c), 0)
    return result(L), L.aprs_lines().decode()


# ---- the demodulator, on the host ----
def test_decodes_every_packet_and_sets_the_clock(lib):
    r, lines = host_run(lib, scene())
    assert r["ok"] == 3 and r["nts"] == 3, lines
    assert r["votes"] == 3
    # the estimate leans on the latest stamp: within 0.5 s of the truth, never more than 1 s off
    assert abs(r["utc0"] - truth_ms()) < 500, (r, truth_ms())
    assert (r["day"], r["mon"], r["yr"]) == (8, 11, 26)
    assert "ts=17:52:00h" in lines and "ts=17:51:00z" in lines and "ts=17:52:04.210r" in lines


@pytest.fixture(scope="module")
def disc_lib():
    """the same receiver deciding each bit on the discriminator (-DAPRS_DISC), as before the
    matched filter"""
    if not shutil.which("cc"):
        pytest.skip("needs a C compiler")
    so = os.path.join(tempfile.mkdtemp(), "libaprsdisc.so")
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-DAPRS_DISC",
                    "-I" + K6, os.path.join(K6, "aprsclk_host.c"), "-o", so], check=True)
    L = ctypes.CDLL(so)
    L.aprs_counts.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    L.aprs_lines.restype = ctypes.c_char_p
    return L


@pytest.mark.parametrize("kw", [dict(), dict(twist=6.0), dict(dev=4500.0, cn0=52.0)])
def test_matched_filter_beats_the_discriminator(lib, disc_lib, kw):
    """twelve packets at 48 dB-Hz (52 for 4.5 kHz deviation, which the chip's filter clips):
    below the discriminator's threshold, above the matched filter's (sim/k6if/aprs_mf.py)"""
    from sim.k6if import aprs_mf
    kw = dict(kw)
    cn0 = kw.pop("cn0", 48.0)
    c, frames = aprs_mf.scene(12, cn0, seed=11, **kw)
    c = np.ascontiguousarray(c.astype(np.int8))
    got = {}
    for name, L in (("mf", lib), ("disc", disc_lib)):
        L.aprs_reset(CPS8)
        L.aprs_counts(c.ctypes.data, len(c), 0)
        got[name] = result(L)["ok"]
    assert got["disc"] <= 2 and got["mf"] >= 6, got


@pytest.mark.parametrize("kw", [dict(twist=6.0), dict(twist=-4.0), dict(f_off=800.0), dict(f_off=-800.0),
                                dict(dev=2000.0), dict(dev=4500.0), dict(cn0=53.0, seed=2)])
def test_margins(lib, kw):
    """a sender's pre-emphasis (space tone louder) or none, a carrier off frequency (the chip's
    AFC pulls it in on the radio, not here), low and high deviation, a weak signal"""
    r, lines = host_run(lib, scene(**kw))
    assert r["ok"] == 3, (kw, lines)
    assert abs(r["utc0"] - truth_ms()) < 500


def test_bench_loopback_packets(lib):
    """the on-air loopback's audio (modem/aprs.py wav --now, sent by the IC-7100 to
    aprsclk_440): three AG7EW stations, each stamped with the true UTC at its own first bit"""
    from sim.k6if import frontend as fe, study as st
    x, sent = aprs.bench_wav(UTC0 + 0.5, gap=0.3, fs=st.FSA)
    assert [c for c, _, _ in sent] == ["AG7EW-1", "AG7EW-2", "AG7EW-3"]
    a = np.zeros(int(0.5 * st.FSA) + len(x) + int(0.5 * st.FSA))
    a[int(0.5 * st.FSA):][:len(x)] = x
    s = st.fm(a * 3000.0, 0.0)
    ib = fe.receive(s, st.FZ, 55.0, bw=8000.0, seed=4, L=0, fdma=FDMA).astype(np.uint8) * 2
    r, lines = host_run(lib, ib)
    assert r["ok"] == 3 and r["nts"] == 3 and r["votes"] == 3, lines
    # exact stamps put the interval's low edge on the truth; the estimate adds the half second a
    # real station's stamp leads its keying (lo + min(w/2, 0.5 s)), so the loopback reads +0.5 s
    assert 450 < r["utc0"] - truth_ms() < 550, (r, truth_ms())
    assert r["day"] == 9  # UTC0 counts days from the epoch: 9 Jan 1970


def test_noise_alone_sets_nothing(lib):
    from sim.k6if import frontend as fe, study as st
    s = np.full(int(4 * st.FZ), 1e-9, complex)  # no carrier: the receiver's own noise
    bits = fe.receive(s, st.FZ, 40.0, bw=8000.0, seed=3, L=0, fdma=FDMA)
    r, lines = host_run(lib, bits.astype(np.uint8) * 2)
    assert r["ok"] == 0 and r["votes"] == 0 and "clk" not in lines


# ---- the parser and the vote, frames handed straight to it ----
def feed(L, items):
    """items: (module ms at the frame's end, frame). nbits: the frame's own bits, unstuffed"""
    L.aprs_reset(CPS8)
    for ms, f in items:
        L.aprs_frame(f, len(f), int(ms), len(f) * 8)
    return result(L), L.aprs_lines().decode()


def at(ms, src, info, path=(), repeated=0, dst="APRS"):
    return ms, aprs.frame(src, dst, info, path=path, repeated=repeated)


def tod(utc_s):
    return aprs.ts_hms(utc_s)


def utc_at(ms_end, f):
    """the true UTC when this frame's first bit went out"""
    return UTC0 + ms_end / 1000 - (len(f) * 8 + 8) / aprs.BAUD


def test_one_station_is_not_enough(lib):
    ms = 3000
    f = aprs.frame("VE7ABC-9", "APRS", "/" + tod(UTC0 + 2.5) + "4857.00N/12229.00W>")
    r, lines = feed(lib, [(ms, f)])
    assert r["votes"] == 1 and r["utc0"] == -1 and "clk --:--:--" in lines


def test_a_digipeated_copy_is_one_vote(lib):
    f = aprs.frame("VE7ABC-9", "APRS", "/" + tod(UTC0 + 1.0) + "4857.00N/12229.00W>", path=("WIDE1-1",))
    g = aprs.frame("VE7ABC-9", "APRS", "/" + tod(UTC0 + 1.0) + "4857.00N/12229.00W>", path=("WIDE1-1",),
                   repeated=1)
    r, _ = feed(lib, [(1800, f), (2900, g)])
    assert r["votes"] == 1 and r["nts"] == 1


def test_marzullo_outvotes_a_wrong_clock(lib):
    """three GPS stations agree; one with its clock 7 minutes slow loses"""
    items = []
    for k, (call, slow) in enumerate([("AA7AA", 0), ("BB7BB", 420), ("CC7CC", 0), ("DD7DD", 0)]):
        ms = 2000 + 1500 * k
        info = "/" + tod(UTC0 + ms / 1000 - 0.6 - slow) + "4857.00N/12229.00W>"
        items.append((ms, aprs.frame(call, "APRS", info)))
    r, lines = feed(lib, items)
    assert r["votes"] == 3, lines
    assert abs(r["utc0"] - truth_ms()) < 1000


def test_local_time_and_bad_stamps_are_ignored(lib):
    items = [
        at(1000, "AA7AA", "/081052/4857.00N/12229.00W>local time"),     # DDHHMM/ (local)
        at(1500, "BB7BB", "@992575z4857.00N/12229.00W_"),               # day 99, hour 25
        at(2000, "CC7CC", "$GPRMC,175201,V,4857.00,N,12229.00,W,0,0,081126,,*00"),  # no fix
        at(2500, "DD7DD", "!4857.00N/12229.00W>no time"),
        at(3000, "EE7EE", "}FF7FF>APRS,TCPIP,EE7EE*:/175202h4857.00N/12229.00W>"),  # third party
    ]
    r, lines = feed(lib, items)
    assert r["ok"] == 5 and r["nts"] == 0, lines


def test_objects_and_status_carry_time(lib):
    t1, t2 = 4000, 6000
    o = aprs.frame("VE7DIG", "APRS", ";LEAVENWTH*" + aprs.ts_hms(UTC0 + t1 / 1000 - 1.0) + "4857.00N/12229.00W-")
    s = aprs.frame("VE7XYZ", "APRS", ">" + aprs.ts_dhm(UTC0 + t2 / 1000 - 1.0) + "on the air")
    r, lines = feed(lib, [(t1, o), (t2, s)])
    assert r["nts"] == 2 and r["votes"] == 2, lines
    assert abs(r["utc0"] - truth_ms()) < 1500
    assert r["day"] == 8


def mdhm(utc_s, month=11):
    """positionless weather's MMDDHHMM (zulu) for seconds since the month's day 0"""
    return f"{month:02d}" + aprs.ts_dhm(utc_s)[:6]


def test_positionless_weather_votes_with_others(lib):
    t1, t2 = 3000, 5000
    w = aprs.frame("KC7WX", "APRS", "_" + mdhm(UTC0 + t1 / 1000 - 20) + "c180s000g000t045r000p000h82b10360")
    g = aprs.frame("VE7ABC-9", "APRS", "/" + tod(UTC0 + t2 / 1000 - 0.6) + "4857.00N/12229.00W>")
    r, lines = feed(lib, [(t1, w), (t2, g)])
    assert r["nts"] == 2 and r["votes"] == 2, lines
    assert "ts=17:51:00w" in lines
    assert abs(r["utc0"] - truth_ms()) < 1000
    assert (r["day"], r["mon"]) == (8, 11)


def test_weather_stamps_alone_set_nothing(lib):
    """two weather stations sending local time (UTC-8) agree with each other, not with UTC"""
    items = [at(2000 + 1500 * k, call, "_" + mdhm(UTC0 + 2 + 1.5 * k - 8 * 3600) + "c...s...g...t050")
             for k, call in enumerate(["KC7WX", "KD7WX"])]
    items.append(at(6000, "KE7WX", "_13081752c...s...g...t050"))     # month 13
    r, lines = feed(lib, items)
    assert r["nts"] == 2 and r["votes"] == 1 and r["utc0"] == -1, lines


def test_minute_stamps_alone_take_the_middle(lib):
    """two DDHHMMz stations stamping 20 s before they key: the estimate is the region's middle,
    within half a minute, not its low edge a minute early"""
    t1, t2 = 3000, 5000
    a = at(t1, "VE7DG1", "@" + aprs.ts_dhm(UTC0 + t1 / 1000 - 20) + "4857.00N/12229.00W#")
    b = at(t2, "VE7DG2", "@" + aprs.ts_dhm(UTC0 + t2 / 1000 - 20) + "4857.00N/12229.00W#")
    r, lines = feed(lib, [a, b])
    assert r["votes"] == 2 and r["wid"] > 10000, lines
    assert abs(r["utc0"] - truth_ms()) < 35000, (r, truth_ms())


def test_midnight(lib):
    """stamps either side of 00:00 UTC agree (offsets are taken round the day)"""
    global UTC0
    old = UTC0
    try:
        UTC0 = 9 * DAY - 2.0                    # 23:59:58 on the 8th
        a = aprs.frame("AA7AA", "APRS", "/" + tod(UTC0 + 1.0 - 0.3) + "4857.00N/12229.00W>")   # 23:59:58
        b = aprs.frame("BB7BB", "APRS", "/" + tod(UTC0 + 4.0 - 0.3) + "4857.00N/12229.00W>")   # 00:00:01
        r, lines = feed(lib, [(1000 + len(a) * 8 * 5 // 6, a), (4000 + len(b) * 8 * 5 // 6, b)])
        assert r["votes"] == 2, lines
        assert abs((r["utc0"] - truth_ms() + 43200000) % 86400000 - 43200000) < 1000
    finally:
        UTC0 = old


def test_float_decoder_reads_test_audio():
    """modem.aprs's own float decoder (for recordings) reads what it makes"""
    fr = [aprs.frame("VE7ABC-9", "APRS", "/175201h4857.00N/12229.00W>hi", path=("WIDE1-1",), repeated=1),
          aprs.frame("N0CALL", "APRS", aprs.rmc(64321.5))]
    x, _ = aprs.packet_audio(fr)
    got = [aprs.describe(f) for f, _ in aprs.demod(x, aprs.FS)]
    assert got == ["VE7ABC-9>APRS,WIDE1-1*:/175201h4857.00N/12229.00W>hi",
                   "N0CALL>APRS:" + aprs.rmc(64321.5)]


# ---- the whole module on the emulated M0 ----
@need_clang
def test_module_on_the_emulated_m0():
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    total = 6.5
    code, entry = le.build("x", [("RUN_MS", f"{int(total * 1000)}u")], SRC)
    full, _ = k6mod.build(SRC)
    # the arena of the SPI images (11,776 B less the header and tag) holds the code and the state
    st = 1024 + 332 + 4 * 16 * 4 + 12 * 28 + 64 + 16 + 2 * 92 * 2 + 2 * 8 * 4 * 4 + 240
    assert len(full) + 24 + st <= 11776
    ib = scene(total=total)
    # the calibrated cost, then 1.7x it: no counts lost. The matched filter took the margin
    # from 2.7x (CPI 4, the discriminator alone) to 1.7x: at CPI 3 it loses a few counts but
    # still decodes all three (the emulator errs slow: k6-if-modem.md). At -Oz, CI's compiler
    # lost counts at 2.5: the module asks for -Os
    for cpi in (1.5, 2.5):
        e = le.Emu(code, entry, ib, 0, FDMA, cpi, max_s=total + 1, level=7, scratch=11776 - 24 - len(full))
        r = e.run()
        assert r is not None, e.said
        ret, stack = r
        assert ret == 3, e.said
        assert stack < 700, stack                # modules get about 700 B
        end = e.said[-1]
        assert "ok=3 " in end and "ts=3 v=3" in end and "lost=0" in end, e.said  # bad: noise
        # the end line's UTC is at the module's last ms (~6500)
        hh, mm, ss = end.split("utc=")[1].split()[0].split(":")
        got = (int(hh) * 3600 + int(mm) * 60 + float(ss))
        want = (UTC0 + total) % DAY
        assert abs(got - want) < 0.6, (end, want)
    assert e.lcd[1].startswith("UTC 17:52:0") and "stations 3 (sec)" in e.lcd[2], e.lcd
    assert len(e.lcd[1]) <= 21 and e.lcd[1].endswith((" 08/11/26", " 08")), e.lcd   # the date
    assert e.lcd[3] == "heard 3 ok 3 ts 3", e.lcd                       # preambles, good, stamped
    assert e.lcd[4].startswith("last ") and " h" in e.lcd[4] and len(e.lcd[4]) <= 21, e.lcd
    assert any(on for _, on in e.led), "the LED blinks a good frame"
    assert e.writes.get(0x34, [None])[-1] == 0 and e.writes.get(0x33)  # GP0 put back
    assert e.clock_sets == []                    # level 7: no radio clock to set


@need_clang
def test_module_sets_the_radios_clock():
    """API level 8: each clock line with two votes or more goes to the core's clock_set, UTC now
    within the bound"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    total = 6.5
    code, entry = le.build("x", [("RUN_MS", f"{int(total * 1000)}u")], SRC)
    full, _ = k6mod.build(SRC)
    ib = scene(total=total)
    e = le.Emu(code, entry, ib, 0, FDMA, 1.5, max_s=total + 1, level=8, scratch=11776 - 24 - len(full))
    assert e.run()[0] == 3, e.said
    assert len(e.clock_sets) == 2, e.clock_sets          # at the second stamp and the third
    for ms, utc, date, w, r in e.clock_sets:
        assert r == 1
        true = round(((UTC0 + ms / 1000) % DAY) * 1000)
        assert abs(utc - true) <= w, (utc, true, w)
        assert w < 9000
    assert e.clock_sets[-1][2] & 0xFF == 8, hex(e.clock_sets[-1][2])     # day 8 from the stamps
    utc, date, w = e.clock_now()
    assert abs(utc - round(((UTC0 + e.cyc / 96e6) % DAY) * 1000)) <= w
    # (level 7 images get no call: test_module_on_the_emulated_m0)


@need_clang
def test_clkget_says_the_core_clock():
    """clkget (level 8): the core's clock as a line and on screen, or none"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    code, entry = k6mod.build(os.path.join(K6, "mod", "clkget.c"))
    e = le.Emu(code, entry, np.zeros(1000, np.uint8), 0, FDMA, 1.5, max_s=1, level=8)
    assert e.run()[0] == 0xFFFFFFFF and e.said == ["core clk none"], e.said
    e = le.Emu(code, entry, np.zeros(1000, np.uint8), 0, FDMA, 1.5, max_s=1, level=8)
    e.clock = (0, 4 * 3600000 + 52 * 60000 + 10123, 9 | 11 << 8 | 2026 << 16, 14)
    assert e.run()[0] == 14
    assert e.said == ["core clk 04:52:10.123 w=14 d=09/11/2026"], e.said
    assert e.lcd[2] == "clk 04:52:10.123 w=14 d=09/11/2026"[:len(e.lcd[2])], e.lcd
    e = le.Emu(code, entry, np.zeros(1000, np.uint8), 0, FDMA, 1.5, max_s=1, level=7)
    assert e.run()[0] == 0xFFFFFFFD and e.said == []


def test_float_decoder_takes_preemphasised_audio():
    """a receiver's audio of a pre-emphasised mobile: decode_any tries de-emphasis too"""
    from scipy.signal import lfilter
    x, sent = aprs.bench_wav(UTC0, gap=0.3)
    y = lfilter([1, -0.95], [0.05], x)  # the sender's pre-emphasis, as the run 4 recording
    y = y / np.max(np.abs(y))
    got = [aprs.describe(f) for _, f in aprs.decode_any(y, aprs.FS)]
    assert [g.split(">")[0] for g in got] == ["AG7EW-1", "AG7EW-2", "AG7EW-3"], got


# ---- fskaprs: the chip's own FSK modem on AX.25 (a probe; the chip's side is a model) ----
@need_clang
def test_fskaprs_probe_3_on_a_model_of_the_chips_modem():
    """fskaprs on the emulated M0 against a model of the FD6818's packet mode as probe 2 found
    it on the air: a bit clock at REG_72's rate (a write takes hold at the next bit, also in a
    packet), the sync word in any bit position (2 or 4 bytes by REG_59<3>) and its interrupt 16
    bits later, 16 bits a FIFO word up to REG_5D's length, the first byte on the air in the word's low byte, an empty FIFO read
    as its last word again. What it hears of the probe's units (modem.aprs.fskprobe_parts): its
    own tones as sent, and Bell 202 as out[i] = sent[i] AND sent[i+1]."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    src = os.path.join(K6, "mod", "fskaprs.c")
    code, entry = le.build("x", [("SLOT_MS", "7500u"), ("WAIT_MS", "9000u")], src)
    BASE = 14400                                        # the model's samples a second
    names = ("P1", "C", "F24", "A+")

    def heard(name, seq):
        out = []
        for bits, baud, f1, f0 in aprs.fskprobe_parts(name, seq):
            if isinstance(bits, int):
                out.append(np.ones(bits * BASE // (4 * baud), int))
                continue
            b = np.asarray(bits, int)
            if f0 == aprs.SPACE:
                b = b & np.append(b[1:], 0)
            out.append(np.repeat(b, BASE // baud))
        return np.concatenate(out + [np.zeros(int(0.3 * BASE), int)])

    air = np.concatenate([np.zeros(BASE, int)] + [heard(n, k) for k, n in enumerate(names)])
    line = aprs.fskprobe_line(True, aprs.FSKPROBE_APRS_S).astype(int)
    eroded = line & np.append(line[1:], 0)

    class Chip(le.Emu):
        pos, reg, buf, taken, pkt = 0.0, 0, None, 0, False
        sync = done = full = False
        last, empty, over = 0, 0, 0
        r = {0x58: 0, 0x59: 0, 0x5A: 0, 0x5B: 0, 0x5D: 0, 0x72: 0}

        def advance(self):
            """the air's bits up to now"""
            now = self.cyc / 96e6 * BASE
            if not self.r[0x59] & 0x1000 or self.r[0x72] < 0x3000:
                self.pos = now
            while self.pos < now:
                bit = int(air[int(self.pos) % len(air)])
                self.pos += BASE // round(self.r[0x72] / 10.32444)
                if self.done:
                    continue
                if not self.pkt:
                    self.reg = (self.reg << 1 | bit) & 0xFFFFFFFF
                    want = self.r[0x5A] << 16 | self.r[0x5B] if self.r[0x59] & 8 else self.r[0x5A]
                    got = self.reg if self.r[0x59] & 8 else self.reg & 0xFFFF
                    if got == want and self.r[0x58] & 0x1C01 == 0x1C01:
                        self.pkt, self.buf, self.taken = True, [], 0
                    continue
                self.buf.append(bit)
                self.sync |= len(self.buf) == 16        # probe 2: the first word is in by then
                n = len(self.buf) // 16 - self.taken
                self.full |= n == 4 and len(self.buf) % 16 == 0
                self.over += n > 8
                if len(self.buf) == 8 * ((self.r[0x5D] >> 8) + 1):
                    self.done = True
                    self.end = True

        def on_stub(self, uc, addr, size, user):
            A = self.A
            name = self.names[(addr - self.stubs) // 16]
            r0, r1 = uc.reg_read(A.UC_ARM_REG_R0), uc.reg_read(A.UC_ARM_REG_R1)
            if name == "fd_write" and r0 in self.r:
                self.advance()
                self.r[r0] = r1
                if r0 == 0x59 and r1 & 0x1000:          # armed: the search starts
                    self.reg, self.pkt, self.buf = 0, False, None
                    self.sync = self.done = self.full = self.end = False
            if name == "fd_read" and r0 in (0x0C, 0x02, 0x5F):
                self.advance()
                flags = (2 if self.sync else 0) | (0x1000 if self.full else 0) | (0x2000 if getattr(self, "end", False) else 0)
                if r0 == 0x5F:
                    at = 16 * self.taken
                    if self.buf is not None and len(self.buf) >= at + 16:
                        self.taken += 1
                        v = 0
                        for b in self.buf[at:at + 16]:
                            v = v << 1 | b
                        self.last = (v & 0xFF) << 8 | v >> 8
                    else:
                        self.empty += 1
                    ret = self.last
                elif r0 == 0x0C:
                    ret = 1 if flags else 0
                else:
                    ret, self.sync, self.end, self.full = flags, False, False, False
                self.tick(self.fd_us * 96)
                uc.reg_write(A.UC_ARM_REG_R0, ret)
                return
            super().on_stub(uc, addr, size, user)

    e = Chip(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=175, level=7)
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700, stack
    assert e.empty == 0 and e.over == 0, (e.empty, e.over)       # the timed reads keep in step
    assert e.said[0] == "fskaprs start hz=440850000 slot=7500", e.said[:3]
    v = {int(ln[1:3]): dict(kv.split("=") for kv in ln.split()[2:]) for ln in e.said if ln[0] == "v"}
    c = {(int(ln[1:3]), int(ln.split()[1])): dict(kv.split("=") for kv in ln.split()[2:])
         for ln in e.said if ln[0] == "c"}
    d = {}
    for ln in e.said:
        if ln[0] == "d":
            f = ln.split()
            assert int(f[2]) == len(d.setdefault((int(f[0][1:]), int(f[1])), [])), ln
            d[int(f[0][1:]), int(f[1])] += [int(w, 16) for w in f[3:]]
    assert sorted(v) == list(range(18)) and sorted(c) == sorted(d), sorted(v)
    assert e.said[-1] == f"fskaprs end ok={ret}" and ret == sum(int(x["ok"]) for x in v.values()), e.said[-3:]

    def bits(key):
        return np.array([int(b) for w in d[key] for b in f"{w:016b}"])

    def unit(key):
        return {0xA1: "P1", 0xAC: "C", 0xAF: "F24"}.get(d[key][0] >> 8)

    # the pager's sync at 1200 baud: every named unit but F24, the C unit's frames pass their CRC
    for n in (0, 10, 11, 12, 13, 14, 15, 16, 17):
        got = {unit(k) for k in d if k[0] == n}
        assert got == {"P1", "C"} and int(v[n]["ok"]) >= 1 and v[n]["bad"] == "0", (n, got, v[n])
        assert all(len(d[k]) == 64 and int(c[k]["w"]) == 64 for k in d if k[0] == n)
    k = next(k for k in d if k[0] == 0 and unit(k) == "P1")
    assert abs(int(c[k]["dn"]) - 853) < 30 and int(c[k]["i"], 16) & 0x2002 == 0x2002, c[k]
    assert any(np.array_equal(bits(k)[16 + o:], eroded[:1008 - o]) for o in (0, 1))  # (the quarter bit)
    k = next(k for k in d if k[0] == 0 and unit(k) == "C")
    assert np.array_equal(bits(k)[16:], line[:1008])
    # REG_72 changed at the sync interrupt: the line 2, 3 and 4 times over, in 256 bytes
    for n, per in ((1, 2), (2, 3), (3, 4), (4, 2), (9, 2)):
        ks = [k for k in d if k[0] == n]
        assert ks and all(len(d[k]) == 128 for k in ks), (n, v[n])
        for k in ks:
            assert abs(int(c[k]["dn"]) - 2048 / (1.2 * per)) < 30, (n, c[k])
            b = bits(k)[16 + 8 * per:]                   # past the name, the bits before REG_72
                                                         # changed and P1's quarter bit
            assert any(not np.ptp(b[o:o + per * 400].reshape(-1, per), axis=1).any() for o in range(per)), n
            assert unit(k) in ("P1", "C") or n == 9, (n, hex(d[k][0]))   # the name came in at 1200 baud
    assert {unit(k) for k in d if k[0] == 5} == {"F24"} and all(len(d[k]) == 128 for k in d if k[0] == 5)
    # the flags as the chip reads them, for a sync word: 3F3F where the long tone is the mark
    # (the P, F24 and A+ units here), all zeros where it's the space (and here in every gap)
    for n in (6, 7, 8, 9):
        assert int(v[n]["sy"]) >= 2 and any(k[0] == n for k in d), (n, v[n])
    # the report (python3 -m modem.aprs fskreport) finds the model's rule in the module's lines
    rep = aprs.fskprobe_report(["K6 MOD say " + ln for ln in e.said])
    for n in (0, 1, 3, 17):
        got = [ln for ln in rep if ln.startswith(f"v{n:02d} #")]
        assert any(" P1#" in ln and "marks +1.00 bit short, fits 100.0%, frames 0 as read" in ln for ln in got), got
        assert any(" C#" in ln and "marks +0.00 bit short, fits 100.0%" in ln and "frames 0" not in ln for ln in got), got
    assert any(" A+@" in ln and "+1.00 bit short, fits 100.0%" in ln for ln in rep if ln.startswith("v07 #")), rep
    assert rep[-1] == e.said[-1] and sum(ln.startswith("v") and "w=" in ln for ln in rep) == 18
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F):
        assert e.writes[reg][-1] == 0, hex(reg)         # put back as read (the emulator reads 0)
    assert {0x0100, 0x0700} <= set(e.writes[0x2B]) and {0x33E3, 0x33E9, 0x3FE9} <= set(e.writes[0x58])
    assert {0x60CA, 0x9130, 0xC195} <= set(e.writes[0x72])
