"""The K6's PHY R sender (c/k6/mod/rangetx.c) on an emulated Cortex-M0 with the K6's timers
(test_k6_i2s.k6_clock): it sends rangetx_bits.h's burst through the tone oscillator, each tone
change timed on TIM17 so the tone's phase stays on the bit edges, and the audio of its REG_70/71
writes, through a model of the UV-380's receive path, decodes 6 of 6 with the UV-380's receiver
(c/common/range_rx.c, needs make -C c/common). The transmit rules themselves (440.850 only, the
ID, every way out keys down) are tests/test_k6_txrules.py's. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, k6_clock, need_clang, run  # noqa: E402

sys.path.insert(0, ROOT)
from modem import k6r  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "rangetx.c")
HDR = os.path.join(ROOT, "c", "k6", "mod", "rangetx_bits.h")
RX = os.path.join(ROOT, "c", "common", "range_rx_host")
WPH = 1032444 / 100000              # REG_71 words per Hz
BIT_NS = 1e9 / 1200


def send(write_us=0, src=SRC, **kw):
    """run the module; -> (ret, said, regs, tx, events [(s, reg, value)])"""
    mmio, rcc, tim = k6_clock()
    tx = {}
    ret, said, regs, _ = run(START, src=src, level=3, tx=tx, mmio=mmio, write_us=write_us, **kw)
    assert rcc[0x18] == 0 and not tim.get(0x00, 0) & 1          # TIM17 and its clock put back
    ev = [(ns * 1e-9, r, v) for (r, v, _), ns in zip(tx["w"], tx["w_ns"]) if r in (0x70, 0x71)]
    return ret, said, regs, tx, ev


def phase_track(ev, ppm=0.0):
    """-> (event times, the tone's frequency after each, its phase (cycles) at each, its level
    after each (REG_70's gain / 60, 0 off)): the tone oscillator, phase continuous, starting at
    phase 0 when it's turned on (what the chip does there isn't known: the receiver reads any
    start phase); a gain change leaves the phase alone"""
    t = np.array([e[0] for e in ev])
    f, on, fs, ons, ph = 0.0, 0, [], [], []
    for i, (_, r, v) in enumerate(ev):
        p = ph[-1] + fs[-1] * (t[i] - t[i - 1]) if i else 0.0
        if r == 0x71:
            f = v / WPH * (1 + ppm * 1e-6)
        else:
            if v & 0x8000 and not on:
                p = 0.0
            on = ((v >> 8) & 0x7F) / 60 if v & 0x8000 else 0
        fs.append(f)
        ons.append(on)
        ph.append(p)
    return t, np.array(fs), np.array(ph), np.array(ons)


def phase_at(track, tq):
    t, fs, ph, _ = track
    i = np.searchsorted(t, tq, side="right") - 1
    return ph[i] + fs[i] * (tq - t[i])


def edge_error(ev, ppm=0.0):
    """the tone's phase at each bit edge (TIM17's time) less a half cycle a 600 Hz bit and a
    whole one a 1200 Hz bit from tone-on, in cycles"""
    on = next(i for i, (_, r, v) in enumerate(ev) if r == 0x70 and v & 0x8000)
    tb = k6r.tone_bits(k6r.burst_bits(k6r.test_payloads(6)))
    edges = ev[on][0] + np.arange(len(tb) + 1) * BIT_NS * 1e-9
    want = np.concatenate([[0.0], np.cumsum(np.where(tb == 1, 0.5, 1.0))])
    return phase_at(phase_track(ev, ppm), edges) - want


def audio(ev, t_end, fs=48000, ppm=0.0):
    """the tone oscillator's audio at fs, from the writes' exact times"""
    track = phase_track(ev, ppm)
    tq = np.arange(int(t_end * fs)) / fs
    i = np.searchsorted(track[0], tq, side="right") - 1
    x = np.zeros(len(tq))
    m = i >= 0
    x[m] = np.sin(2 * np.pi * phase_at(track, tq[m])) * track[3][i[m]]
    return x


def uv380(x, fs=48000, deemph=True, snr_db=None, seed=1):
    """the UV-380's receive path, roughly: the FM receiver's de-emphasis (750 us, when the K6
    doesn't pre-emphasise its tone), the voice band (300 Hz - 3 kHz), 8 kHz, 16 bits"""
    from scipy import signal
    if deemph:
        b, a = signal.bilinear([1], [750e-6, 1], fs)
        x = signal.lfilter(b, a, x) * 4
    b, a = signal.butter(2, 300, "hp", fs=fs)
    x = signal.lfilter(b, a, x)
    b, a = signal.butter(6, 3000, "lp", fs=fs)
    x = signal.lfilter(b, a, x)
    y = signal.resample_poly(x, 1, fs // 8000) * 8000
    if snr_db is not None:
        rms = np.sqrt(np.mean(y[np.abs(y) > 1] ** 2))
        y = y + np.random.default_rng(seed).normal(0, rms / 10 ** (snr_db / 20), len(y))
    return y


def uv380_measured(x, fs=48000, snr_db=None, seed=1, n=127):
    """the UV-380's receive path as measured (tests/data/uv380/rx-response-20261113.txt) for a
    flat source, at 8 kHz: K10's magnitude (6 dB less at 1200 Hz than at 600: its de-emphasis) to
    1450 Hz and 6 dB an octave down above, U7's phase (the same as K10's where both are good),
    as an n-tap FIR"""
    from scipy import signal
    f, mag, u7, ph = np.loadtxt(os.path.join(ROOT, "tests", "data", "uv380", "rx-response-20261113.txt")).T
    mag = np.where(f <= 1450, mag, mag[f == 1450][0] - 20 * np.log10(f / 1450) + u7 - u7[f == 1450][0])
    mag = np.where(f < 330, u7 + 5, mag)
    fg = np.fft.rfftfreq(4 * n, 1 / 8000)
    H = 10 ** (np.interp(fg, f, mag, left=-60, right=-60) / 20) * np.exp(1j * np.interp(fg, f, np.unwrap(np.radians(ph))))
    h = np.roll(np.fft.irfft(H, 4 * n), n // 2)[:n] * np.hanning(n)
    y = np.convolve(signal.resample_poly(x, 1, fs // 8000), h, "same")
    y = y / np.sqrt(np.mean(y[np.abs(y) > 1e-3] ** 2)) * 4000
    if snr_db is not None:
        y = y + np.random.default_rng(seed).normal(0, 4000 / 10 ** (snr_db / 20), len(y))
    return y


def jitter(seed=1):
    """a register write's length on the radio, roughly: 180-200 us"""
    rng = np.random.default_rng(seed)
    return lambda reg, us: int(180 + rng.integers(0, 21))


def test_header_is_the_burst():
    assert open(HDR).read() == k6r.txhdr(6)


@need_clang
def test_builds_level_3_fits():
    from modem import k6mod
    code, entry = k6mod.build(SRC)
    assert k6mod.source_level(SRC) == 3 and entry == 0
    assert 24 + len(code) <= 6144, len(code)


@need_clang
def test_sends_the_tone_bits_on_time():
    ret, said, regs, tx, ev = send(write_us=jitter())
    assert ret == 6, said
    assert said[0] == "rangetx n=6 bits=13848 g=44/110 pa=1 rx=440850000"
    assert said[-1] == "rangetx sent bits=13848 ms=11540 id=52" or \
        said[-1] == "rangetx sent bits=13848 ms=11541 id=52", said
    w = dict(v.split("=") for v in said[1].split()[1:])
    assert int(w["late"]) == 0 and int(w["d"]) < 2400, said[1]
    # the burst: from tone-on to tone-off, the words follow the tone bits
    on = next(i for i, (_, r, v) in enumerate(ev) if r == 0x70 and v & 0x8000)
    off = next(i for i, (_, r, v) in enumerate(ev) if r == 0x70 and v == 0 and i > on)
    tb = k6r.tone_bits(k6r.burst_bits(k6r.test_payloads(6)))
    changes = np.flatnonzero(np.diff(tb)) + 1
    words = [v for _, r, v in ev[on:off] if r == 0x71]
    assert len(words) == len(changes) == int(w["w"])
    assert words == [6195 if tb[k] else 12389 for k in changes]
    assert ev[on - 1] == (ev[on - 1][0], 0x71, 6195) and tb[0] == 1
    # the tone's phase at every bit edge: a half cycle a 600 Hz bit, a whole one a 1200 Hz bit,
    # from tone-on; the module holds it within a few degrees of that through the 11.5 s burst
    assert np.max(np.abs(edge_error(ev))) * 360 < 6, np.max(np.abs(edge_error(ev))) * 360
    assert abs(ev[off][0] - ev[on][0] - len(tb) * BIT_NS * 1e-9) < 300e-6
    # the core's keep-alive and key checks happened through the burst
    pa = [us for n, _, us in tx["log"] if n == "tx_pa" and us < ev[off][0] * 1e6]
    assert len(pa) > 50 and np.max(np.diff(pa)) < 250_000


@need_clang
@pytest.mark.skipif(not os.path.exists(RX), reason="needs make -C c/common")
@pytest.mark.parametrize("deemph,ppm", [(True, 0), (False, 0), (True, 340), (False, -340)],
                         ids=["deemph", "flat", "deemph+340ppm", "flat-340ppm"])
def test_the_uv380_reads_it(deemph, ppm):
    """ppm: the radio chip's crystal (the tone) that far off the MCU's clock (the bits) with
    RT_PPM 0, as the K6 on the bench (270-380 ppm, wandering): the tone's phase walks against the
    edges about 110 degrees a second, and the UV-380's receiver follows it"""
    pytest.importorskip("scipy")
    import test_range_rx
    ret, said, regs, tx, ev = send(write_us=jitter(2))
    x = audio(ev, tx["log"][-1][2] * 1e-6, ppm=ppm)
    pk = test_range_rx.receive(uv380(x, deemph=deemph, snr_db=8))
    assert len(test_range_rx.good(pk)) == 6, [(e, ok) for _, _, e, ok, _ in pk]
    assert max(e for _, _, e, ok, _ in pk if ok) < 40, pk


@need_clang
@pytest.mark.skipif(not os.path.exists(RX), reason="needs make -C c/common")
@pytest.mark.parametrize("g12", [110, 44], ids=["pre-emphasised", "flat"])
def test_the_uv380_as_measured(g12, tmp_path):
    """through the UV-380's measured receive path (6 dB less at 1200 Hz than at 600: K7-K10 read
    0 of 6) with the tone 100 ppm off its bits: the 1200 Hz bits at 2.5 times the 600's gain
    (RT_GAIN12, the default) read; sent flat, packets go or come back with hundreds of errors"""
    pytest.importorskip("scipy")
    import test_range_rx
    w = tmp_path / "rt.c"
    w.write_text('#define RT_GAIN12 %d\n#include "%s"\n' % (g12, SRC))
    ret, said, regs, tx, ev = send(write_us=jitter(2), src=str(w))
    assert ret == 6
    x = audio(ev, tx["log"][-1][2] * 1e-6, ppm=100)
    pk = test_range_rx.receive(uv380_measured(x, snr_db=12, seed=3))
    e = [e for _, _, e, ok, p in pk if ok and p in test_range_rx.PL]
    if g12 == 110:
        assert len(e) == 6 and max(e) < 20, pk
    else:
        assert len(e) < 6 or max(e) > 100, pk


@need_clang
def test_rt_ppm_trims_the_chips_crystal(tmp_path):
    """the FD6818's crystal 30 ppm fast against the MCU's: the phase walks a third of a cycle
    over the burst unless RT_PPM says so"""
    ret, said, regs, tx, ev = send(write_us=jitter(3))
    assert 90 < np.max(np.abs(edge_error(ev, ppm=30))) * 360 < 150
    w = tmp_path / "rangetx_ppm.c"
    w.write_text('#define RT_PPM 30.0\n#include "%s"\n' % SRC)
    ret, said, regs, tx, ev = send(write_us=jitter(3), src=str(w))
    assert ret == 6 and np.max(np.abs(edge_error(ev, ppm=30))) * 360 < 6


@need_clang
def test_a_slow_write_stops_before_the_burst():
    ret, said, regs, tx, ev = send(write_us=700)
    assert ret == -8, said
    assert tx["log"][-1][0] == "tx_off" and regs.get(0x70, 0) == 0 and regs.get(0x71, 0) == 0
    assert said[-1].startswith("rangetx w=0 late=0 wr=")


@need_clang
def test_lo_keeps_the_pa_off():
    """rangetx_lo: the PA off from just after tx_on to the end, the burst and the ID the same"""
    lo = os.path.join(ROOT, "c", "k6", "mod", "rangetx_lo.c")
    ret, said, regs, tx, ev = send(write_us=jitter(4), src=lo)
    assert ret == 6 and said[0] == "rangetx n=6 bits=13848 g=44/110 pa=0 rx=440850000", said
    log = tx["log"]
    assert log[0][0] == "tx_on" and log[1][:2] == ("tx_pa", 0) and log[-1][0] == "tx_off"
    assert all(a == 0 for n, a, _ in log if n == "tx_pa"), log
    assert np.max(np.abs(edge_error(ev))) * 360 < 6


@need_clang
@pytest.mark.parametrize("trim", [0.0, 271.0])
def test_check_measures_the_walk(trim, tmp_path):
    """tools/k6_rangetx_check.py on a modelled burst whose tone runs 271 ppm fast against the
    MCU (K9's figure), with RT_PPM 0 and 271, through the receive filters and a recorder 269 ppm
    off: it walks about 87 deg/s untrimmed and stays put trimmed"""
    pytest.importorskip("scipy")
    from scipy import signal
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import k6_rangetx_check as chk
    w = tmp_path / "rt.c"
    w.write_text('#define RT_PPM %.1f\n#include "%s"\n' % (trim, SRC))
    ret, said, regs, tx, ev = send(write_us=jitter(5), src=str(w))
    fs = 48000
    x = audio(ev, tx["log"][-1][2] * 1e-6, fs, ppm=271.0)
    x = signal.sosfilt(signal.butter(2, [300, 3000], "bandpass", fs=fs, output="sos"), x) * 8000
    t = np.arange(int(len(x) * (1 - 269e-6))) * (1 + 269e-6)
    x = np.interp(t, np.arange(len(x)), x) + np.random.default_rng(1).normal(0, 300, len(t))
    r = chk.analyse(x, fs)
    assert abs(r["ppm"] + 269) < 10
    walk = r["walk_600"] / chk.PPM_DEG_S
    assert (abs(walk - 271) < 30) if trim == 0 else abs(walk) < 10, "\n".join(chk.report(r))
    # the 1200 Hz bits at 110 / 44 of the 600s' gain (8 dB), less the band-pass's tilt
    assert 5 < r["level_db"] < 10, "\n".join(chk.report(r))
