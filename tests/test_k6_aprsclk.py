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
import gzip
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
def modem_chip(le, pick, BASE=4800):
    """an emulator with a model of the FD6818's packet mode as the probes found it on the air: a
    bit clock at REG_72's rate over the air's bits pick(REG_58) (BASE samples a second; a write
    to either register takes hold at the next bit, also in a packet), the sync word in any bit
    position (2 or 4 bytes by REG_59<3>) and its interrupt 16 bits later, 16 bits a FIFO word up
    to REG_5D's length, the first byte on the air in the word's low byte, an empty FIFO read as
    its last word again. REG_0B<7> or <6> after a sync word, by which way up it was. With spur,
    arming raises the sync flag by itself, with neither bit (aprsfsk's second bench run)"""
    class Chip(le.Emu):
        pos, reg, buf, taken, pkt = 0.0, 0, None, 0, False
        either = False      # the sync word upside down too (what probe 4's captures suggest)
        spur, pol = False, 0
        long5d = False      # REG_5D<7:5> as three more bits of the length (a BK4819's; dfskcap asks)
        early_done = False  # the RX-done flag with a packet's first word, the packet going on
                            # (the plain FSK receiver on the bench, 2026-11-10 12:00Z)
        ring = False        # an empty FIFO read gives the word of eight reads before (the bench,
                            # 2026-11-10 12:20Z), not the last one
        rate = 1.0          # the chip's bit clock against REG_72's (the plain receiver's follows the
                            # audio: 0.92 on 2200 Hz)
        sync = done = full = False
        last, empty, over = 0, 0, 0
        r = {0x58: 0, 0x59: 0, 0x5A: 0, 0x5B: 0, 0x5D: 0, 0x5E: 4, 0x72: 0}
        hist = None         # the packets' bits, as the chip took them

        def advance(self):
            """the air's bits up to now"""
            now = self.cyc / 96e6 * BASE * self.rate
            if not self.r[0x59] & 0x1000 or self.r[0x72] < 0x3000 or not self.r[0x58] & 1:
                self.pos = now
            while self.pos < now:
                view = pick(self.r[0x58])
                bit = int(view[int(self.pos) % len(view)])
                self.pos += BASE // round(self.r[0x72] / 10.32444)
                if self.done:
                    continue
                if not self.pkt:
                    self.reg = (self.reg << 1 | bit) & 0xFFFFFFFF
                    want = self.r[0x5A] << 16 | self.r[0x5B] if self.r[0x59] & 8 else self.r[0x5A]
                    got = self.reg if self.r[0x59] & 8 else self.reg & 0xFFFF
                    if got == want or (self.either and got == want ^ (0xFFFFFFFF if self.r[0x59] & 8 else 0xFFFF)):
                        self.pkt, self.buf, self.taken = True, [], 0
                        self.pol = 0xA0 if got == want else 0x60
                    continue
                self.buf.append(bit)
                self.sync |= len(self.buf) == 16        # probe 2: the first word is in by then
                if self.early_done and len(self.buf) == 40:
                    self.end = True
                n = len(self.buf) // 16 - self.taken
                self.full |= n == (self.r[0x5E] & 7 or 4) and len(self.buf) % 16 == 0
                self.over += n > 8
                if len(self.buf) == 8 * ((self.r[0x5D] >> 8) + ((self.r[0x5D] & 0xE0) << 3 if self.long5d else 0) + 1):
                    self.done = True
                    self.end = not self.early_done

        def on_stub(self, uc, addr, size, user):
            A = self.A
            name = self.names[(addr - self.stubs) // 16]
            r0, r1 = uc.reg_read(A.UC_ARM_REG_R0), uc.reg_read(A.UC_ARM_REG_R1)
            if name == "fd_write" and r0 in self.r:
                self.advance()
                self.r[r0] = r1
                if r0 == 0x59 and r1 & 0x1000:          # armed: the search starts
                    if self.buf and self.hist is not None:
                        self.hist.append(self.buf)
                    self.reg, self.pkt, self.buf = 0, False, None
                    self.sync = self.done = self.full = self.end = False
                    self.sync, self.pol = self.spur, 0
            if name == "fd_read" and r0 == 0x0B:
                self.advance()
                self.tick(self.fd_us * 96)
                uc.reg_write(A.UC_ARM_REG_R0, 0xCA02 | self.pol)
                return
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
                        if self.ring and self.buf is not None and self.taken >= 8:
                            v = 0
                            for b in self.buf[at - 128:at - 112]:
                                v = v << 1 | b
                            self.last = (v & 0xFF) << 8 | v >> 8
                    ret = self.last
                elif r0 == 0x0C:
                    ret = 1 if flags else 0
                else:
                    ret, self.sync, self.end, self.full = flags, False, False, False
                self.tick(self.fd_us * 96)
                uc.reg_write(A.UC_ARM_REG_R0, ret)
                return
            super().on_stub(uc, addr, size, user)

    return Chip


@need_clang
def test_fskaprs_probe_4_on_a_model_of_the_chips_modem():
    """fskaprs on the emulated M0 against a model of the FD6818's packet mode as the probes found
    it on the air: a bit clock at REG_72's rate and a receiver by REG_58<12:10> (a write to either
    takes hold at the next bit, also in a packet), the sync word in any bit position (2 or 4
    bytes by REG_59<3>) and its interrupt 16 bits later, 16 bits a FIFO word up to REG_5D's
    length, the first byte on the air in the word's low byte, an empty FIFO read as its last
    word again. What it hears of the probe's units (modem.aprs.fskprobe_parts): its own tones as
    sent; Bell 202 as out[i] = sent[i] AND sent[i+1] in the 1200/1800 receiver (probe 2), and
    here as sent in any other (what probe 4 looks for)."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    src = os.path.join(K6, "mod", "fskaprs.c")
    code, entry = le.build("x", [("SLOT_MS", "7000u"), ("WAIT_MS", "9000u")], src)
    BASE = 4800                                         # the model's samples a second
    names = ("P0", "C", "A+", "A-")

    def heard(name, seq, erode):
        out = []
        for bits, baud, f1, f0 in aprs.fskprobe_parts(name, seq):
            b = np.asarray(bits, int)
            if erode and f0 == aprs.SPACE:
                b = b & np.append(b[1:], 0)
            out.append(np.repeat(b, BASE // baud))
        return np.concatenate(out + [np.zeros(int(0.3 * BASE), int)])

    air = [np.concatenate([np.zeros(BASE, int)] + [heard(n, k, e) for k, n in enumerate(names)]) for e in (0, 1)]
    line = aprs.fskprobe_line(True, aprs.FSKPROBE_APRS_S).astype(int)
    eroded = line & np.append(line[1:], 0)

    Chip = modem_chip(le, lambda r58: air[r58 >> 10 & 7 == 7], BASE)
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=240, level=7)
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700, stack
    assert e.empty == 0 and e.over == 0, (e.empty, e.over)       # the timed reads keep in step
    assert e.said[0] == "fskaprs start hz=440850000 slot=7000", e.said[:3]
    v = {int(ln[1:3]): dict(kv.split("=") for kv in ln.split()[2:]) for ln in e.said if ln[0] == "v"}
    c = {(int(ln[1:3]), int(ln.split()[1])): dict(kv.split("=") for kv in ln.split()[2:])
         for ln in e.said if ln[0] == "c"}
    d = {}
    for ln in e.said:
        if ln[0] == "d":
            f = ln.split()
            assert int(f[2]) == len(d.setdefault((int(f[0][1:]), int(f[1])), [])), ln
            d[int(f[0][1:]), int(f[1])] += [int(w, 16) for w in f[3:]]
    assert sorted(v) == list(range(27)) and sorted(c) == sorted(d), sorted(v)
    assert e.said[-1] == f"fskaprs end ok={ret}" and ret == sum(int(x["ok"]) for x in v.values()), e.said[-3:]

    def bits(key):
        return np.array([int(b) for w in d[key] for b in f"{w:016b}"])

    def unit(key):
        return {0xA0: "P0", 0xAC: "C"}.get(d[key][0] >> 8)

    # the pager's sync, then REG_58 (and REG_72) written at its interrupt: every named unit, and
    # in this model's other receivers the Bell 202 frames pass their CRC in the module
    for n in range(21):
        two = n in (1, 2, 3)
        ks = [k for k in d if k[0] == n]
        assert {unit(k) for k in ks} == {"P0", "C"}, (n, v[n])
        assert all(len(d[k]) == (128 if two else 64) and c[k]["p"] == "12"[two] for k in ks), n
        assert all(abs(int(c[k]["dn"]) - 840) < 30 and int(c[k]["i"], 16) & 0x2002 == 0x2002 for k in ks), n
        assert two or int(v[n]["ok"]) >= 2 * len(ks) and v[n]["bad"] == "0", (n, v[n])
    k = next(k for k in d if k[0] == 0 and unit(k) == "P0")
    assert np.array_equal(bits(k)[32:], line[16:1008]) and np.array_equal(bits(k)[16:18], eroded[:2])
    k = next(k for k in d if k[0] == 1 and unit(k) == "P0")
    assert any(not np.ptp(bits(k)[40 + o:1840 + o].reshape(-1, 2), axis=1).any() for o in (0, 1))   # each bit twice
    # a sync word of flags as that receiver reads them: FE (01 where the long tone is the space),
    # FFFC at two samples a bit
    for n in (21, 22, 23, 24, 25, 26):
        assert int(v[n]["sy"]) >= 2 and any(k[0] == n for k in d), (n, v[n])
        assert all(c[k]["p"] == "12"[n > 23] for k in d if k[0] == n)
    assert int(v[21]["ok"]) >= 2 and int(v[23]["ok"]) >= 2, (v[21], v[23])
    # the report (python3 -m modem.aprs fskreport) on the module's lines
    rep = aprs.fskprobe_report(["K6 MOD say " + ln for ln in e.said])
    got = [ln for ln in rep if ln.startswith("v00 #") and " P0#" in ln]
    assert got and all("loose 100.0% 0 slips, frames 3" in ln or "loose 99" in ln for ln in got), got
    got = [ln for ln in rep if ln.startswith("v01 #") and " P0#" in ln]
    assert got and all("2 a bit" in ln and "frames 3" in ln for ln in got), got
    assert any(" A-@" in ln and "frames" in ln and "frames 0" not in ln for ln in rep if ln.startswith("v23 #")), rep
    assert rep[-1] == e.said[-1] and sum(ln.startswith("v") and "w=" in ln for ln in rep) == 27
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F):
        assert e.writes[reg][-1] == 0, hex(reg)         # put back as read (the emulator reads 0)
    assert {0x0100, 0x0200} <= set(e.writes[0x2B]) and {0x33E3, 0x33E9, 0x23E3, 0x37E3} <= set(e.writes[0x58])
    assert 0x60CA in e.writes[0x72]


# ---- dfskcap: the chip's plain FSK receiver as a sampler of the audio (a probe) ----
@need_clang
@pytest.mark.parametrize("long5d", [False, True])
def test_dfskcap_records_the_plain_fsk_receivers_words(long5d):
    """dfskcap on the emulated M0 against modem_chip. At 4800 baud the model hears Bell 202 as
    the chip gave it on the bench (aprs.dfsk_like: 2200 Hz as alternating bits, 3.67 a bit,
    1200 Hz as 0011); at 6000 baud as
    a true 1-bit sampler of the audio would, with random bits between the packets. Every
    capture's words are the bits the model took, none lost and none read twice, with REG_5D's
    length as it is known (256 bytes) and as the probe hopes (2048), and whether the model's
    RX-done flag comes at the packet's end or (as the chip's did on the bench) with its first
    word; the report reads the frames back out of them."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap.c")
    scale = 150 if long5d else 60
    code, entry = le.build("x", [("SLOT_SCALE", f"{scale}u"), ("WAIT_MS", "200u")], src)
    full, _ = k6mod.build(src)
    assert len(full) < 2400, len(full)
    BASE, total = 24000, 138 * scale / 1000 + 6
    n = int(total * BASE)
    rng = np.random.default_rng(7)
    like, true, t, k = np.repeat(rng.integers(0, 2, n // 5 + 1), 5)[:n], rng.integers(0, 2, n), 0.3, 0
    while t + 1.0 < total:
        fr = aprs.frame(f"AG7EW-{k % 8 + 1}", "APRS", f">dfskcap {k}")
        line = aprs.hdlc_bits([fr], pre_flags=40).astype(int)
        x = aprs.dfsk_like(line, k)
        i = int(t * BASE)
        like[i:i + 5 * len(x)] = np.repeat(x, 5)
        a = aprs.afsk(line, BASE)
        true[i:i + len(a)] = a > 0
        t, k = t + len(a) / BASE + 0.3, k + 1
    Chip = modem_chip(le, lambda r58: true if Chip.r[0x72] > 0xE000 else like, BASE)
    Chip.either, Chip.long5d, Chip.hist, Chip.early_done = True, long5d, [], long5d
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
             scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=440850000 n=7" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    sets = [ln.split()[3:] for ln in e.said if ln.startswith("s ")]
    assert sets == [
        ["cfg=00e9", "b=c195", "sy=aaaa", "l=ffe0"],
        ["cfg=00e9", "b=c195", "sy=aaaa", "l=ff00"],
        ["cfg=00e9", "b=c195", "sy=6666", "l=ffe0"],
        ["cfg=03e9", "b=c195", "sy=aaaa", "l=ffe0"],
        ["cfg=00e9", "b=f1fb", "sy=e739", "l=ffe0"],
        ["cfg=00e9", "b=f1fb", "sy=d2da", "l=ffe0"],
        ["cfg=00e9", "b=c195", "sy=aaaa", "l=ffe0"],
    ], sets
    c = {(int(ln[1]), int(ln.split()[1])): dict(kv.split("=") for kv in ln.split()[2:])
         for ln in e.said if ln[0] == "c"}
    d = {}
    for ln in e.said:
        if ln[0] == "d" and ln[1].isdigit():
            f = ln.split()
            w = d.setdefault((int(f[0][1:]), int(f[1])), [])
            assert int(f[2], 16) == len(w), ln
            w += [int(f[3][i:i + 4], 16) for i in range(0, len(f[3]), 4)]
    v = {int(ln[1]): dict(kv.split("=") for kv in ln.split()[1:]) for ln in e.said if ln[0] == "v"}
    assert sorted(v) == list(range(7)) and sorted(c) == sorted(d) and ret == sum(int(x["n"]) for x in v.values()), v
    assert all(int(x["n"]) >= 1 for x in v.values()), v           # every setting starts on something
    # each capture: the model's packet bit for bit, then (after RX-done) its last word again
    took = ["".join(map(str, b)) for b in e.hist]
    for key, words in d.items():
        kv = c[key]
        byt = 256 if (not long5d or key[0] == 1) else 2048
        baud = 6000 if key[0] in (4, 5) else 4800
        # the capture ends where the FIFO is empty, wherever RX-done was flagged (a packet whose
        # last words are the same word loses those: they look like the empty FIFO's)
        assert int(kv["w"]) == len(words) and byt // 2 - 2 <= len(words) <= byt // 2, (key, kv)
        assert int(kv["i"], 16) & 0x2000, (key, kv)
        if long5d:
            assert int(kv["dn"]) <= 12 and int(kv["dl"]) <= 12, (key, kv)
        else:
            assert abs(int(kv["dl"]) - (byt * 8 - 16) * 1000 / baud) < 25, (key, kv, byt)
        bits = "".join(f"{w:016b}" for w in words)
        assert any(b.startswith(bits) and len(b) == byt * 8 for b in took), (key, kv)
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F):
        assert e.writes[reg][-1] == 0, hex(reg)                      # put back as read
    assert 0x0100 in e.writes[0x2B] and 0x0206 in e.writes[0x5E]
    # the report: at 4800 baud the frames again, where the chip started in the opening (the
    # sync word of 16 alternating bits)
    rep = aprs.dfsklog_report(["K6 MOD say " + ln for ln in e.said])
    got = [ln for ln in rep if ln[0] == "c"]
    assert len(got) == len(c), rep
    if long5d:
        assert all((": 25" if ln[1] == "1" else ": 204") in ln for ln in got), got
        assert all("frames 0" not in ln and "[AG7EW-" in ln for ln in got if ln[1] in "036"), got
        assert all("frames" not in ln for ln in got if ln[1] in "45"), got


# ---- the bench's captures of the plain FSK receiver, and the receiver built on them ----
DFSK_LOG = os.path.join(ROOT, "tests", "data", "k6", "dfskcap-20261110.log.gz")
DFSK_FIRST = {0: 5, 1: 2, 3: 5, 6: 3}       # the kind (station number) of each setting's first capture
CLEAN = (1, 2, 3, 4, 7)                     # flat, pre-emphasised, de-emphasised, low, a short opening


def dfsk_bench():
    """dfskcap's lines of the bench run of 2026-11-10 12:20Z (K6 on 440.850 MHz, the IC-7100
    sending modem.aprs wav --kinds): 44 captures, put together again from the run's dumps (the s
    lines without their times, four c lines with t= and w= only)"""
    with gzip.open(DFSK_LOG, "rt") as f:
        return f.read().splitlines()


def test_dfsklog_reads_the_bench_captures():
    """What the chip's plain FSK receiver gave on the bench, read by modem.aprs (dfsk_destale,
    dfsk_line): every clean packet's frame passes its FCS, the pre-emphasised ones too, which
    the chip's 1200/2400 Hz receiver never read; no packet with noise does. With REG_5D = ff00
    (setting 1) the chip's packet ends at 256 bytes, inside the frame, and only the packet with
    the short opening fits. Sync word 6666 (setting 2) starts inside a frame."""
    rep = aprs.dfsklog_report(dfsk_bench())
    c = [ln for ln in rep if ln[0] == "c" and ln[1].isdigit()]
    assert len(c) == 44 and sum(ln.startswith("s ") for ln in rep) == 5, rep[:3]
    read = {}
    for ln in c:
        st, n = int(ln[1]), int(ln.split()[1])
        assert " at 4800 baud" in ln and "empty reads " in ln, ln
        empty = int(ln.split("empty reads ")[1].split(";")[0])
        words = int(ln.split(" w=")[1].split()[0])
        assert 7 <= 11 * empty <= 2 * words, ln              # about every eleventh read, from the 30th on
        if st == 2:
            assert "frames 0" in ln, ln
            continue
        kind = (DFSK_FIRST[st] + n - 2) % 8 + 1
        want = kind in CLEAN and (st != 1 or kind == 7)
        if want:
            assert f"frames 1 [AG7EW-{kind}>APRS:/1220" in ln or f"frames 1 [AG7EW-{kind}>APRS:/122" in ln, ln
            assert ln.endswith(f"W>K6 {aprs.KINDS[kind][0]}]"), ln
            read[kind] = read.get(kind, 0) + 1
        else:
            assert "frames 0" in ln, ln
    assert read == {1: 3, 2: 3, 3: 4, 4: 4, 7: 6}, read


def test_dfsk_line_reads_the_model_of_the_chips_bits():
    """dfsk_like (the tests' model of the chip's bits) and dfsk_line agree, either tone as the
    flags' long one, and a closing flag before silence is not held back"""
    fr = aprs.frame("AG7EW-1", "APRS", ">hello there 12345 " + "\x00" * 6 + "~~~~")
    for seed in range(12):
        line = aprs.hdlc_bits([fr], pre_flags=10).astype(int)
        for inv in (0, 1):
            x = np.concatenate([aprs.dfsk_like(line ^ inv, seed), np.zeros(40, int)])
            got = [f for _, _, ok, f in aprs.hdlc_spans(aprs.dfsk_line(x)) if ok]
            assert got == [bytes(fr)], (seed, inv)


@need_clang
@pytest.mark.parametrize("mode", [0, 1, 2])
def test_aprsdfsk_reads_the_bench_captures_on_the_emulated_m0(mode):
    """aprsdfsk (aprsclk's HDLC, parser and vote behind the chip's plain FSK receiver, FSK_PLAIN)
    on the emulated M0. The model's air is the bench's own captures, a packet after another with
    silence between: 16 alternating bits (the sync word the chip started on) and the words the
    chip gave, at a bit clock 8% slow as the chip's is on 2200 Hz, the FIFO a ring of eight
    whose empty read gives the word of eight reads before. The module must read every clean
    packet (flat, pre-emphasised, de-emphasised, low, short opening), none of the noisy, set
    the clock from the stations' stamps and put the registers back. Mode 0 takes the FIFO four
    words at the almost-full flag and never reads it empty; mode 1 reads by time, as dfskcap
    did, and drops the empty reads; mode 2 is the bench's build, one then the other."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "aprsdfsk.c")
    _, _, caps = aprs.dfsklog_caps(dfsk_bench())
    keys = [(0, 5), (0, 6), (0, 9), (0, 7), (0, 8), (0, 11), (3, 6), (0, 10), (6, 7), (6, 9)]
    BASE, rate, t, air, sent = 4800, 0.92, 0.6, [], []
    for key in keys:
        good, _, fr = aprs.dfsk_read(caps[key])
        kind = (DFSK_FIRST[key[0]] + key[1] - 2) % 8 + 1
        assert (len(fr) == 1) == (kind in CLEAN), key
        bits = [int(b) for w in [0xAAAA] + good for b in f"{w:016b}"]
        sent.append((t, kind, fr))
        air.append((int(t * BASE * rate), bits))
        t += len(bits) / (BASE * rate) + 0.3
    total = t + 0.5
    stream = np.zeros(int((total + 2) * BASE * rate), np.int8)
    for i, bits in air:
        stream[i:i + len(bits)] = bits
    defs = [("RUN_MS", f"{int(total * 1000)}u"), ("DF_MODE", str(mode)), ("DF_SWAP", f"{int(total * 500)}u")]
    code, entry = le.build("x", defs, src)
    full, _ = k6mod.build(src)
    bench, _ = k6mod.build(os.path.join(K6, "mod", "aprsdfsk_440.c"))
    assert len(full) < 5900 and len(bench) < 6100, (len(full), len(bench))
    Chip = modem_chip(le, lambda r58: stream, BASE)
    Chip.either, Chip.long5d, Chip.early_done, Chip.ring, Chip.rate = True, True, True, True, rate
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 1, level=8,
             scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    clean = [k for _, k, _ in sent if k in CLEAN]
    assert e.said[0] == "aprs start hz=144390000 fsk=233 sync=240 plain", e.said[:2]
    assert stack < 700 and e.over == 0, (stack, e.over)
    f = [ln.split()[1] for ln in e.said if ln.startswith("f ")]
    y = [dict(kv.split("=") for kv in ln.split()[2:]) for ln in e.said if ln.startswith("y ")]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert len(y) == len(keys) and all(110 < int(v["w"]) < 200 for v in y), y
    # every clean packet in its turn, and no other
    assert f == [f"AG7EW-{k}" for k in clean], (f, y)
    assert [int(v["ok"]) for v in y] == [int(k in CLEAN) for _, k, _ in sent], y
    if mode == 0:
        # four words at the flag: the FIFO is never read empty inside a packet (e counts the
        # words that happen to be the one eight before: a few in the flags), and the flag
        # never fails to come
        assert all(v["m"] == "0" and int(v["e"]) <= 6 for v in y), y
        assert " late=0" in e.said[-2], e.said[-2]
    elif mode == 1:
        # by time: about every twelfth read finds the FIFO empty
        assert all(v["m"] == "1" and 5 <= int(v["e"]) <= 24 for v in y), y
    else:
        s = [ln for ln in e.said if ln.startswith("s ")]
        assert s and s[0].startswith("s 1 t=") and s[0].endswith(" ok=4"), s
        assert {v["m"] for v in y} == {"0", "1"}, y
        # the log's report (python3 -m modem.aprs plainlog): packets and frames for each way
        rep = aprs.plainlog_report(["K6 MOD say " + ln for ln in e.said])
        m = [ln for ln in rep if ln.startswith("m=")]
        assert len(m) == 2 and m[0].startswith("m=0: 5 packets, 4 with a frame, e="), rep
        assert m[0].endswith("; AG7EW-1 1, AG7EW-2 1, AG7EW-3 1, AG7EW-4 1"), m
        assert m[1].startswith("m=1: 5 packets, 4 with a frame, e=") and "AG7EW-7 1" in m[1], m
        assert rep[0] == e.said[0] and rep[-3] == e.said[-1], rep
    end = e.said[-1]
    assert f"ok={len(clean)} " in end and end.endswith(" lost=0") and len(end) <= 60, e.said[-2:]
    assert ret >= 2 and f" v={ret}" in end, (ret, end)
    assert e.said[-2].startswith("aprs fsk ms=") and " key=0 " in e.said[-2], e.said[-2]
    assert e.lcd[0].strip() == "APRS CLOCK 144.390" and e.lcd[3].startswith("heard "), e.lcd
    assert any(on for _, on in e.led)
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F, 0x2B):
        assert e.writes[reg][-1] == 0, hex(reg)         # put back as read (the emulator reads 0)
    assert e.writes[0x5A][0] == 0xAAAA and e.writes[0x5D][0] == 0xFFE0 and e.writes[0x72][0] == 0xC195
    assert e.writes[0x5E][0] == 0x0204 and 0x00E9 in e.writes[0x58] and 0x10F0 in e.writes[0x59]


# ---- the same clock behind the chip's own FSK modem (c/k6/mod/aprsfsk.c) ----
@need_clang
@pytest.mark.parametrize("name,spur,again_ms", [
    ("aprsfsk", False, 700),
    ("aprsfsk", True, 700),
    ("aprsfskb", True, 700),
    ("aprsfskb", True, 0),
])
def test_aprsfsk_on_a_model_of_the_chips_modem(name, spur, again_ms):
    """aprsfsk (aprsclk's HDLC, parser and vote behind the FD6818's packet modem) on the emulated
    M0 against modem_chip: the three stations' line bits as the 1200/2400 Hz receiver slices
    them with REG_58 = 33a3 (probe 4), the second with its tones swapped, random bits between
    them. It must take every packet, set the clock, give up a packet's read where the line goes
    idle and turn the modem off and on for the next, and put the modem's registers back.
    With spur the model raises the sync flag by itself at every arming, as the chip did on the
    bench (the second run): aprsfsk then reads three stale words every round and takes the
    packets inside such reads, as it did on the air. aprsfskb reads the FIFO all the time and
    starts on the first word that is not the last one again. again_ms 0 is aprsfskb as it is built,
    its search started again every 59 ms: a start that falls in a packet's sync word loses an
    opening of fewer than about eight flags, so the four-flag opening is tried at 700 ms."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    stream, ctrl = name == "aprsfskb", 0xF8
    src = os.path.join(K6, "mod", name + ".c")
    total, BASE = 6.5, 4800
    defs = [("RUN_MS", f"{int(total * 1000)}u"), ("APRS_FSK_DUMP", "2")]
    if again_ms:
        defs.append(("FSK_AGAIN", f"{again_ms}u"))
    code, entry = le.build("x", defs, src)
    full, _ = k6mod.build(src)
    assert len(full) < 5800, len(full)              # the tap build is 7.6 KB
    rng = np.random.default_rng(5)
    air = rng.integers(0, 2, int((total + 2) * BASE // 4))
    for k, (t, mk) in enumerate(packets()):
        # aprsfskb's third packet opens with four flags only: the sync word, then the frame
        pre = (4 if again_ms else 8) if stream and k == 2 else 40
        b = aprs.hdlc_bits(mk(UTC0 + t + pre * 8 / aprs.BAUD), pre_flags=pre).astype(int)
        i = int(t * aprs.BAUD)
        air[i:i + len(b)] = b ^ (k == 1)
    air = np.repeat(air, BASE // aprs.BAUD)
    Chip = modem_chip(le, lambda r58: air, BASE)
    Chip.either, Chip.spur = True, spur
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 1, level=8,
             scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert ret == 3 and stack < 700, (ret, stack, e.said)
    assert e.over == 0 and (spur or e.empty == 0), (e.empty, e.over)
    assert e.said[0] == f"aprs start hz=144390000 fsk=13219 sync={ctrl}" + " stream" * stream, e.said[:2]
    end = e.said[-1]
    assert "ok=3 " in end and "ts=3 v=3" in end and end.endswith(" lost=0") and len(end) <= 60, e.said
    hh, mm, ss = end.split("utc=")[1].split()[0].split(":")
    assert abs(int(hh) * 3600 + int(mm) * 60 + float(ss) - (UTC0 + total) % DAY) < 0.6, end
    f = [ln.split()[1] for ln in e.said if ln.startswith("f ")]
    assert f == ["VE7ABC-9", "VE7WX", "N0CALL-7"], e.said
    # each packet's y line: read to a little past its closing flags, never the chip's whole 128 words
    y = [dict(kv.split("=") for kv in ln.split()[2:]) for ln in e.said if ln.startswith("y ")]
    good = [v for v in y if v["ok"] == "1"]
    assert len(good) == 3 and all(20 < int(v["w"]) < 80 for v in good), y
    assert not stream or int(good[2]["w"]) < 55, good    # 73 with its 40 flags
    assert all(int(v["w"]) < 128 for v in y), y
    fsk = dict(kv.split("=") for kv in e.said[-2].split()[2:])
    assert e.said[-2].startswith("aprs fsk ms=") and fsk["key"] == "0" and len(e.said[-2]) <= 60, e.said[-2]
    if stream:
        # nothing but the three packets: no read of stale words, the search started again
        # only while nothing came
        assert len(y) == int(fsk["pre"]) and fsk["fake"] == "0", (y, fsk)
    elif spur:
        assert len(y) > 40 and int(fsk["fake"]) > 40 and all(v["w"] == "3" for v in y if v["ok"] == "0"), fsk
    else:
        assert int(fsk["pre"]) == len(y) == 3 and fsk["fake"] == "0", (y, fsk)
    # its d lines hold the words as read: the first good packet's open with flags (01: this
    # station's long tone is the space)
    n = next(ln.split()[1] for ln in e.said if ln.startswith("y ") and " ok=1" in ln)
    d = [w for ln in e.said if ln.startswith(f"d {n} ") for w in ln.split()[3:]]
    flag = ("0101", "0202", "0404", "0808", "1010", "2020", "4040", "8080")
    assert len(d) == int(good[0]["w"]) and (d[0] in flag or spur and not stream and d[3] in flag), d[:4]
    # the log's report (python3 -m modem.aprs fsklog): each packet's frame again from its words,
    # and a frame with one tone bit wrong named as such
    rep = aprs.fsklog_report(["K6 MOD say " + ln for ln in e.said])
    got = [ln for ln in rep if ln.startswith("y ") and " ok=1 " in ln]
    assert len(got) == 3 and "ok VE7ABC-9>APRS:/" in got[0] and "ok N0CALL-7>GPS:$GPRMC" in got[2], rep
    assert all(int(ln.split("flags=")[1].split()[0]) >= 20 for ln in got[:2]), got
    hurt = []
    for ln in e.said:
        if ln.startswith(f"d {n} 030 "):
            f = ln.split()
            f[5] = f"{int(f[5], 16) ^ 0x0100:04x}"
            ln = " ".join(f)
        hurt.append(ln)
    bad = next(ln for ln in aprs.fsklog_report(hurt) if ln.startswith(f"y {n} "))
    assert "bad, bit " in bad and "wrong: VE7ABC-9>APRS:/" in bad, bad
    # the radio's clock set from it (level 8), within its bound
    assert len(e.clock_sets) == 2, e.clock_sets
    for ms, utc, date, w, ok in e.clock_sets:
        assert abs(utc - round(((UTC0 + ms / 1000) % DAY) * 1000)) <= w and w < 9000, (ms, utc, w)
    if stream or not spur:
        assert e.lcd[0].strip() == "APRS CLOCK 144.390" and e.lcd[1].startswith("UTC 17:52:0"), e.lcd
        assert e.lcd[3].startswith("heard ") and e.lcd[3].endswith(" ok 3 ts 3"), e.lcd
    else:
        # as on the bench: a read of stale words is always under way, so the screen and the
        # minute's line never get their turn (what aprsfskb is for)
        assert not e.lcd, e.lcd
    assert any(on for _, on in e.led)
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F, 0x2B):
        assert e.writes[reg][-1] == 0, hex(reg)         # put back as read (the emulator reads 0)
    # the modem off and on for each packet, armed twice each time (aprsfskb: and every 2 s
    # with nothing heard)
    w58 = e.writes[0x58]
    again = w58.count(0x33A3) - len(y) - 1
    lim = (1, 6) if again_ms else (40, 90)          # 6.5 s less three packets, a start every 59 ms
    assert (lim[0] <= again <= lim[1] if stream else again == 0) and w58.count(0) >= len(y) + 2, (again, len(y), w58)
    assert e.writes[0x5A][0] == 0xFEFE and e.writes[0x5B][0] == 0xFEFE and e.writes[0x5D][0] == 0xFF00
    assert e.writes[0x59].count(0x1000 | ctrl) == 2 * w58.count(0x33A3) and 0x3002 in e.writes[0x3F], e.writes[0x59][:8]


def test_kinds_wav_a_rehearsal_of_real_packets():
    """modem.aprs wav --now --kinds: packets as they come off the air (pre-emphasis, a
    receiver's de-emphasis, low deviation, a weak signal's noise, eight opening flags), a kind
    a station number. The float decoder takes all but the noisiest, each stamped with the UTC
    at its first bit."""
    t0 = 1794305724.9
    x, sent = aprs.kinds_wav(t0, rounds=2)
    assert len(sent) == 16 and np.abs(x).max() <= 1.0 and len(x) / aprs.FS < 20
    assert [c for c, _, _ in sent[:8]] == [f"AG7EW-{k}" for k in range(1, 9)]
    got = [aprs.describe(f) for _, f in aprs.decode_any(0.5 * x, aprs.FS)]
    for k in (1, 2, 3, 4, 5, 7):
        assert sum(f"AG7EW-{k}>" in g for g in got) == 2, (k, got)
    assert any("K6 pre n9" in g for g in got), got
    for c, info, end in sent:
        hms = info[1:7]
        u = int(t0 + end - 0.4) % 86400               # a second or less before the frame's end
        assert abs(int(hms[:2]) * 3600 + int(hms[2:4]) * 60 + int(hms[4:]) - u) <= 1, (c, info, end)


@need_clang
def test_aprsfsk_sweep_goes_round_its_settings():
    """aprsfsks_440 (FSK_SWEEP): a setting of REG_58 (its bits 7:4) every so many ms, said in an
    s line, only between packets; the packets still decode (the model's receiver does not mind the
    setting) and the registers are put back."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    total, BASE = 6.5, 4800
    code, entry = le.build("x", [("RUN_MS", f"{int(total * 1000)}u"), ("FSK_SWEEP", "700u")],
                           os.path.join(K6, "mod", "aprsfsk.c"))
    full, _ = k6mod.build(os.path.join(K6, "mod", "aprsfsks_440.c"))
    assert len(full) < 5800, len(full)
    air = np.random.default_rng(5).integers(0, 2, int((total + 2) * BASE // 4))
    for k, (t, mk) in enumerate(packets()):
        b = aprs.hdlc_bits(mk(UTC0 + t + 40 * 8 / aprs.BAUD)).astype(int)
        i = int(t * aprs.BAUD)
        air[i:i + len(b)] = b
    Chip = modem_chip(le, lambda r58: np.repeat(air, BASE // aprs.BAUD), BASE)
    Chip.either, Chip.spur = True, True
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 1, level=8,
             scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None and r[0] == 3, (r, e.said[-5:])
    s = [dict(kv.split("=") for kv in ln.split()[2:]) | {"n": ln.split()[1]} for ln in e.said if ln.startswith("s ")]
    assert [v["n"] for v in s] == [str(k) for k in range(1, 10)], s
    assert [v["cfg"] for v in s] == [f"33{k:x}3" for k in range(1, 10)], s
    # on the 700 ms grid, late only by a read under way (the next one is then early); the
    # longest is 128 words of a stale word that reads as flags, as on 144.390
    assert all(0 <= int(v["t"]) - 700 * (k + 1) < 2000 for k, v in enumerate(s)), [v["t"] for v in s]
    assert [int(v["ok"]) for v in s] == sorted(int(v["ok"]) for v in s) and s[-1]["ok"] in ("2", "3"), s
    assert e.writes[0x58][2] == 0x3303 and {0x3303 | k << 4 for k in range(10)} <= set(e.writes[0x58])
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x3F, 0x2B):
        assert e.writes[reg][-1] == 0, hex(reg)
    assert "ok=3 " in e.said[-1], e.said[-3:]
