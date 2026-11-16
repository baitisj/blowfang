"""FT8 and WSPR sent as steps of a PLL with 10 Hz resolution (c/common/tone_fsk.c, the RT-950's
first transmit milestone; docs/rt950-polar-sim.md). The symbols come from the repo's own
encoders ("CQ AG7EW CN87", "AG7EW CN87 10"). With error feedback each symbol's mean frequency
is right to well under 0.2 Hz at 1000 writes a second, though FT8's tones are 6.25 Hz apart and
WSPR's 1.46 Hz; plain rounding is off by several Hz. The rebuilt baseband carries every symbol
on its tone, and FT8 and WSPR decode it back to the message with the repo's decoders.
Needs make -C c/common (tone_fsk_host). SPDX-License-Identifier: MIT"""
import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
HOST = os.path.join(ROOT, "c", "common", "tone_fsk_host")
pytestmark = pytest.mark.skipif(not os.path.exists(HOST), reason="needs make -C c/common")

FT8_TEXT = "CQ AG7EW CN87"
WSPR_TEXT = "AG7EW CN87 10"
FS = 12000
SYM = {
    "ft8": 0.16,
    "wspr": 8192 / 12000,
}
SPACING = {
    "ft8": 6.25,
    "wspr": 12000 / 8192,
}


@pytest.fixture(scope="module")
def tones():
    pytest.importorskip("scipy")
    from sim.k6digi import ft8ref
    from sim.k6digi import wsprref
    return {
        "ft8": list(ft8ref.encode(FT8_TEXT)[1]),
        "wspr": list(wsprref.encode(WSPR_TEXT)),
    }


def run(mode, *opts, iq=False, audio=False, regs=False, pad=0.0):
    """-> (the host's line as a dict, {name: file contents})"""
    with tempfile.TemporaryDirectory() as d:
        args = [HOST, mode, *map(str, opts), "--pad", str(pad)]
        files = {}
        if iq:
            files["iq"] = os.path.join(d, "y.cf32")
        if audio:
            files["audio"] = os.path.join(d, "a.raw")
        if regs:
            files["regs"] = os.path.join(d, "r.txt")
        for k, p in files.items():
            args += ["--" + k, p]
        out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
        got = {}
        if iq:
            v = np.fromfile(files["iq"], "<f4")
            got["iq"] = v[0::2] + 1j * v[1::2]
        if audio:
            got["audio"] = np.fromfile(files["audio"], "<i2").astype(float)
        if regs:
            got["regs"] = open(files["regs"]).read()
    line = dict(w.split("=", 1) for w in out.split())
    return line, got


def digits(t):
    return "".join(map(str, t))


@pytest.mark.parametrize("mode", ["ft8", "wspr"])
def test_built_in_tones_are_the_encoders(mode, tones):
    """the host's default symbols are the encoder's for the AG7EW messages"""
    _, a = run(mode, regs=True)
    _, b = run(mode, "--tones", digits(tones[mode]), regs=True)
    assert a["regs"] == b["regs"]


@pytest.mark.parametrize("mode", ["ft8", "wspr"])
@pytest.mark.parametrize("base", [1500.0, 1503.3])
def test_error_feedback_gets_each_symbol_right(mode, base):
    """10 Hz steps, 1000 writes/s: every symbol's mean frequency within 0.2 Hz (in fact under
    0.06 Hz) with first- or second-order error feedback; plain rounding misses by Hz"""
    for dither in ("sd1", "sd2"):
        line, _ = run(mode, "--writes", 1000, "--step", 10, "--dither", dither, "--base", base)
        assert float(line["err_max_hz"]) < 0.1, line
    line, _ = run(mode, "--writes", 1000, "--step", 10, "--dither", "none", "--base", base)
    assert float(line["err_max_hz"]) > 1.0, line


def test_write_rate_needed_for_ft8():
    """FT8's 160 ms symbols need about 300 writes/s for 0.2 Hz at 10 Hz steps (the error is
    about a step over the writes in a symbol); WSPR's 683 ms symbols manage it at 100"""
    assert float(run("ft8", "--writes", 200, "--base", 1503.3)[0]["err_max_hz"]) > 0.2
    assert float(run("ft8", "--writes", 500, "--base", 1503.3)[0]["err_max_hz"]) < 0.2
    assert float(run("wspr", "--writes", 100, "--base", 1503.3)[0]["err_max_hz"]) < 0.2


def test_dither_spurs_are_reported():
    """the stepping's own error spectrum: about 43 dB down at 1000 writes/s, lower at higher
    rates and with a fine trim; without error feedback the phase walks off (near 0 dB)"""
    d1000 = float(run("ft8", "--writes", 1000, "--base", 1503.3)[0]["dith_db"])
    d8000 = float(run("ft8", "--writes", 8000, "--base", 1503.3)[0]["dith_db"])
    trim = float(run("ft8", "--writes", 1000, "--base", 1503.3, "--trim", 1, "--trim-max", 5)[0]["dith_db"])
    rnd = float(run("ft8", "--writes", 1000, "--base", 1503.3, "--dither", "none")[0]["dith_db"])
    assert d1000 < -38, d1000
    assert d8000 < d1000 - 10, (d8000, d1000)
    assert trim < d1000 - 10, (trim, d1000)
    assert rnd > -10, rnd


def test_fine_trim_alone_fixes_rounding():
    """a 1 Hz trim (+-5 units) under plain rounding: each write within 0.5 Hz"""
    line, _ = run("ft8", "--dither", "none", "--trim", 1, "--trim-max", 5, "--base", 1503.3)
    assert float(line["err_max_hz"]) <= 0.5, line


def test_registers_are_10_hz_units_around_the_tones(tones):
    """each write is the register offset from the dial in 10 Hz units; with first-order
    error feedback a steady WSPR tone uses only the two values either side of it"""
    _, got = run("wspr", "--writes", 1000, regs=True)
    rows = [ln.split() for ln in got["regs"].splitlines()]
    assert len(rows) == int(np.ceil(162 * SYM["wspr"] * 1000 - 1e-6))
    for reg, trim, hz in rows:
        assert int(trim) == 0 and abs(int(reg) * 10 - float(hz)) < 1e-3
    regs = np.array([int(r[0]) for r in rows])
    assert set(regs) <= {150, 151}   # 1500-1504.4 Hz


def detect(y, mode, base=1500.0, ntone=None):
    """each symbol's strongest tone, by a DFT at each tone over the symbol's middle 80 %"""
    ns = SYM[mode] * FS
    ntone = ntone or (8 if mode == "ft8" else 4)
    out = []
    for j in range(int(round(len(y) / ns))):
        a, b = int(j * ns + 0.1 * ns), min(int((j + 1) * ns - 0.1 * ns), len(y))
        t = np.arange(a, b) / FS
        p = [abs(np.sum(y[a:b] * np.exp(-2j * np.pi * (base + k * SPACING[mode]) * t)))
             for k in range(ntone)]
        out.append(int(np.argmax(p)))
    return out


@pytest.mark.parametrize("mode", ["ft8", "wspr"])
def test_every_symbol_on_its_tone(mode, tones):
    """the rebuilt baseband (the PLL's phase, each write held until the next) carries every
    symbol on its tone with error feedback; plain rounding merges neighbouring tones"""
    _, got = run(mode, "--writes", 1000, iq=True)
    assert detect(got["iq"], mode) == tones[mode]
    _, got = run(mode, "--writes", 1000, "--dither", "none", iq=True)
    wrong = sum(a != b for a, b in zip(detect(got["iq"], mode), tones[mode]))
    assert wrong > len(tones[mode]) // 10, wrong


def noisy(x, snr_db, rng):
    """x plus white noise, snr_db in 2500 Hz (WSJT-X's convention)"""
    p = np.mean(x ** 2)
    n = p / 10 ** (snr_db / 10) * (FS / 2) / 2500
    return x + np.sqrt(n) * rng.standard_normal(len(x))


def test_ft8_decodes():
    """the FT8 frame as a USB receiver at the dial hears it (12 kHz audio, -10 dB SNR in
    2500 Hz) decodes to the message with the K6's FT8 receiver, tone 0 at 1500 Hz"""
    from sim.k6digi.ft8_wav import decode_audio
    _, got = run("ft8", "--writes", 1000, audio=True, pad=1.0)
    x = noisy(got["audio"], -10, np.random.default_rng(1))
    found = decode_audio(x, FS, lo=1300, hi=1800)
    assert FT8_TEXT in found, found
    assert abs(found[FT8_TEXT][0] - 1500) < 7


def test_wspr_decodes():
    """the WSPR frame decodes to its message with sim/k6digi/wsprref.py; plain rounding puts
    all four tones on 1500 Hz and it does not"""
    from sim.k6digi import wsprref
    _, got = run("wspr", "--writes", 1000, audio=True, pad=1.0)
    x = noisy(got["audio"], -10, np.random.default_rng(2))
    found = wsprref.decode_audio(x, FS)
    assert any(d[0] == WSPR_TEXT and abs(d[1] - 1500) < 3 for d in found), found
    _, got = run("wspr", "--writes", 1000, "--dither", "none", audio=True, pad=1.0)
    found = wsprref.decode_audio(got["audio"], FS)
    assert not any(d[0] == WSPR_TEXT for d in found), found


def test_weak_ft8_needs_the_dither():
    """at -18 dB SNR the dithered frame decodes as often as WSJT-X's ideal waveform would, and
    the rounded one (tones up to 3.75 Hz off, 1 and 2 on the same 1510 Hz) mostly does not"""
    from sim.k6digi.ft8_wav import decode_audio
    hits = {}
    for dither in ("sd1", "none"):
        _, got = run("ft8", "--writes", 1000, "--dither", dither, audio=True, pad=1.0)
        hits[dither] = sum(FT8_TEXT in decode_audio(noisy(got["audio"], -18, np.random.default_rng(s)),
                                                    FS, lo=1400, hi=1600)
                           for s in range(4))
    assert hits["sd1"] >= 3 and hits["none"] < hits["sd1"], hits
