"""The tone-oscillator probe (c/k6/mod/tonewr.c) and its report (tools/k6_tonewr.py): the module
keeps the transmit rules (tests/test_k6_txrules.py runs it too), lands its writes on tonewr's
schedule, and the report, given the audio of those writes from a model oscillator that runs on
through a write or one that a write sets to phase 0, says which, through receive filters, a
recorder 200 ppm off the K6 and noise. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, k6_clock, need_clang, run  # noqa: E402

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_tonewr  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "tonewr.c")
WPH = 1032444 / 100000


@pytest.fixture(scope="module")
def sent():
    mmio, rcc, tim = k6_clock()
    rng = np.random.default_rng(1)
    tx = {}
    ret, said, regs, _ = run(START, src=SRC, level=3, tx=tx, mmio=mmio,
                             write_us=lambda r, us: int(115 + rng.integers(0, 3)))
    assert rcc[0x18] == 0 and not tim.get(0x00, 0) & 1
    ev = [(ns * 1e-9, r, v) for (r, v, _), ns in zip(tx["w"], tx["w_ns"])]
    return ret, said, regs, tx, ev


def audio(ev, t_end, fs, model, ppm=0.0):
    """the tone oscillators' sum from the writes. continuous: tone 1 and tone 2 are NCOs that run
    on through every write, enabled or not; reset: a REG_71 write sets tone 1 to phase 0, and
    turning a tone on starts it at phase 0. The recorder's clock is ppm off the K6's."""
    n = int(t_end * fs)
    t = np.arange(n) / fs * (1 + ppm * 1e-6)          # K6 time of each recorder sample
    w71 = w72 = 0
    r70 = 0
    marks = []
    for tt, r, v in ev:
        if r == 0x71:
            w71 = v
        elif r == 0x72:
            w72 = v
        elif r == 0x70:
            r70 = v
        else:
            continue
        marks.append((tt, w71, w72, r70, r))
    x = np.zeros(n)
    ph1 = ph2 = 0.0
    last = 0.0
    prev = (0, 0, 0)
    for i, (tt, a, b, c, reg) in enumerate(marks + [(t[-1] + 1, 0, 0, 0, 0)]):
        f1, f2 = prev[0] / WPH, prev[1] / WPH
        m = (t >= last) & (t < tt)
        tm = t[m] - last
        if prev[2] & 0x8000:
            x[m] += np.sin(2 * np.pi * (ph1 + f1 * tm))
        if prev[2] & 0x0080:
            x[m] += np.sin(2 * np.pi * (ph2 + f2 * tm))
        ph1 = (ph1 + f1 * (tt - last)) % 1
        ph2 = (ph2 + f2 * (tt - last)) % 1
        if model == "reset" and i < len(marks):
            if reg == 0x71:
                ph1 = 0.0
            if reg == 0x70 and c & 0x8000 and not prev[2] & 0x8000:
                ph1 = 0.0
            if reg == 0x70 and c & 0x0080 and not prev[2] & 0x0080:
                ph2 = 0.0
        last, prev = tt, (a, b, c)
    return x


def receive(x, fs, seed=1):
    from scipy import signal
    sos = signal.butter(2, [300, 3000], "bandpass", fs=fs, output="sos")
    y = signal.sosfilt(sos, x) * 8000
    return y + np.random.default_rng(seed).normal(0, 300, len(y))


@need_clang
def test_lands_its_writes_on_the_schedule(sent):
    ret, said, regs, tx, ev = sent
    assert ret == 137 + 54 + 18 + 53 + 1, said            # B, C, D, E, tone off
    assert said[0] == "tonewr pa=0 g=60 r72=0000 rx=440850000"
    w = dict(v.split("=") for v in said[1].split()[1:])
    assert int(w["late"]) == 0 and int(w["d"]) < 960, said[1]
    on = next(tt for tt, r, v in ev if r == 0x70 and v & 0x8000)
    got = [(tt - on, r, v) for tt, r, v in ev if on + 0.5 < tt < on + 7.2]
    want = k6_tonewr.schedule()
    assert len(got) == len(want) + 1 and got[-1][1:] == (0x70, 0)   # (and tone off again after)
    assert max(abs(g[0] - s[0]) for g, s in zip(got, want)) < 10e-6
    # REG_72 was tone 2's before the key-up and is back after
    log = tx["log"]
    w72 = [(tt, v) for tt, r, v in ev if r == 0x72]
    assert w72[0][0] < log[0][2] * 1e-6 and w72[-1][0] > log[-1][2] * 1e-6 and regs[0x72] == 0
    assert all(a == 0 for n, a, _ in log if n == "tx_pa")          # the PA off throughout


@need_clang
@pytest.mark.parametrize("fs", [48000, 8000])
@pytest.mark.parametrize("model", ["continuous", "reset"])
def test_report_tells_continuous_from_reset(sent, model, fs):
    pytest.importorskip("scipy")
    *_, ev = sent
    on = next(tt for tt, r, v in ev if r == 0x70 and v & 0x8000)
    ev = [(tt - on + 1.0, r, v) for tt, r, v in ev]          # tone-on 1 s into the recording
    x = receive(audio(ev, 9.5, fs, model, ppm=-200), fs)
    res = k6_tonewr.analyse(x, fs)
    text = "\n".join(k6_tonewr.report(res))
    assert abs(res["ppm"] - 200) < 40, text
    assert res["B"]["rms"] < 15 if model == "continuous" else res["B"]["rms"] > 30, text
    for kind in ("up", "down"):
        c = res["C_" + kind]
        assert k6_tonewr.verdict(c["jump"][0], c["after"][0]) == model, text
    assert abs(res["E_t2_hz"] - 1200) < 30, text
    want = "free running" if model == "continuous" else "restarts"
    for name in ("D_on", "E_to_t1", "E_to_t2"):
        assert k6_tonewr.free(res[name]["after"], res[name]["runs_on"]) == want, (name, text)
