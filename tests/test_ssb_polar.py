"""Polar SSB (c/common/ssb_polar.c): the envelope and the instantaneous frequency of the
analytic signal, sent as a radio would, with PLL writes of 10 Hz steps N times a second, P PA
levels at their own update rate and D samples between the two paths (docs/rt950-polar-sim.md,
docs/rt950-plan.md "SSB transmit"). A single tone has a constant envelope and comes out clean
whatever the PA does; a two-tone and speech need fast writes, many levels and the paths lined
up to a fraction of a sample. These are the numbers the RT-950 must deliver.
Needs make -C c/common (ssb_polar_host). SPDX-License-Identifier: MIT"""
import os
import subprocess
import tempfile
import wave

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.path.join(ROOT, "c", "common", "ssb_polar_host")
HTS1A = os.path.join(ROOT, "sotto", "codec", "codec2", "raw", "hts1a.raw")
pytestmark = pytest.mark.skipif(not os.path.exists(HOST), reason="needs make -C c/common")

IDEAL = (
    "--writes", 0,
    "--step", 0,
    "--levels", 0,
    "--env-rate", 0,
)
SPEECH = ("--speech", "--secs", 4)


def run(*opts, out=False):
    """-> the host's line as a dict (and the rebuilt baseband with out=True)"""
    with tempfile.TemporaryDirectory() as d:
        args = [HOST, *map(str, opts)]
        if out:
            args += ["--out", os.path.join(d, "y.cf32")]
        line = subprocess.run(args, check=True, capture_output=True, text=True).stdout
        res = {k: v for k, v in (w.split("=", 1) for w in line.split() if "=" in w)}
        if out:
            v = np.fromfile(os.path.join(d, "y.cf32"), "<f4")
            return res, v[0::2] + 1j * v[1::2]
    return res


def sup(*opts):
    return float(run(*opts)["sup_db"])


def peaks(y, fs=96000, n=3):
    """the n strongest spectral lines (Hz, signed)"""
    w = np.hanning(len(y))
    s = np.abs(np.fft.fft(y * w))
    f = np.fft.fftfreq(len(y), 1 / fs)
    out = []
    for i in np.argsort(s)[::-1]:
        if all(abs(f[i] - g) > 50 for g in out):
            out.append(f[i])
        if len(out) == n:
            break
    return sorted(out)


def test_two_tone_lands_on_the_wanted_side():
    """USB: 700 and 1900 Hz above the carrier; LSB: below, equally clean"""
    usb, y = run(out=True)
    p = peaks(y, n=2)
    assert abs(p[0] - 700) < 15 and abs(p[1] - 1900) < 15, p
    lsb, y = run("--lsb", out=True)
    p = peaks(y, n=2)
    assert abs(p[0] + 1900) < 15 and abs(p[1] + 700) < 15, p
    assert abs(float(usb["sup_db"]) - float(lsb["sup_db"])) < 1.0


def test_single_tone_is_clean_with_a_crude_pa():
    """one tone: constant envelope, constant frequency; 16 PA levels, two samples of delay and
    1000 writes/s cost nothing (the frequency on the 10 Hz grid); off the grid the error
    feedback's dither leaves the opposite side still 55 dB down"""
    for opts in ((), ("--levels", 16), ("--delay", 2), ("--writes", 1000), ("--lsb",)):
        assert sup("--tone", 1000, "--levels", 16, *opts) > 90, opts
    assert sup("--tone", 1003, "--levels", 16) > 55


def test_ideal_radio_ceiling_and_polar_rate():
    """with a perfect radio, what limits suppression is the rate the polar split (and the
    radio's updates) run at: the envelope and phase are not band-limited. At 8 kHz a two-tone
    gets under 30 dB; at 32 kHz (up 4, the default) 50 dB"""
    assert sup("--up", 1, *IDEAL) < 30
    assert sup("--up", 4, *IDEAL) > 45
    assert sup("--up", 8, *IDEAL) > sup("--up", 4, *IDEAL) + 6
    assert sup("--up", 4, *IDEAL, *SPEECH) > 50


def test_usdx_clamp_costs_suppression():
    """clamping the frequency to 0-3000 Hz (as the uSDX does) holds the opposite side to
    about 25 dB down, whether the clamped phase is dropped or paid back; following the nulls
    (no clamp) is what gets past 40 dB"""
    for sig in ((), SPEECH):
        none = sup(*IDEAL, *sig)
        assert none > 45
        assert sup(*IDEAL, *sig, "--clamp", "plain") < 30
        assert sup(*IDEAL, *sig, "--clamp", "track") < 30


def test_write_rate():
    """suppression from the frequency path alone rises with the PLL write rate: about 12 dB at
    1000/s, 30-35 dB at 8000/s, 40-50 dB at 16000/s"""
    for sig in ((), SPEECH):
        s = [sup(*IDEAL, *sig, "--writes", n, "--step", 10) for n in (1000, 4000, 8000, 16000)]
        assert s[0] < 20 and s[0] < s[1] < s[2] < s[3], s
        assert s[2] > 30 and s[3] > 39, s


def test_step_does_not_matter_with_error_feedback():
    """at 8000 writes/s the 10 Hz step costs nothing against 1 Hz, and 50 Hz under 1 dB"""
    for sig in ((), SPEECH):
        one = sup(*IDEAL, *sig, "--writes", 8000, "--step", 1)
        assert abs(sup(*IDEAL, *sig, "--writes", 8000, "--step", 10) - one) < 0.3
        assert sup(*IDEAL, *sig, "--writes", 8000, "--step", 50) > one - 1.0


def test_pa_levels_and_rate():
    """speech needs PA levels (about 64 for 35 dB, 256 for 45) and a PA updated at 8000/s or
    more; a two-tone gets by with 16 levels (its envelope is a rectified sine)"""
    s = [sup(*IDEAL, *SPEECH, "--levels", p) for p in (16, 64, 256)]
    assert s[0] < s[1] < s[2] and s[1] > 33 and s[2] > 44, s
    assert sup(*IDEAL, "--levels", 16) > 45
    r = [sup(*IDEAL, *SPEECH, "--env-rate", e) for e in (4000, 8000, 16000)]
    assert r[0] < 25 and r[0] < r[1] < r[2] and r[1] > 33, r


def test_delay_match():
    """the paths must line up to a fraction of a sample: an eighth of a sample (16 us) costs
    the speech 15 dB, half a sample leaves 25-28 dB, a whole one under 20 dB; early and late
    are alike"""
    for sig in ((), SPEECH):
        d = [sup(*IDEAL, *sig, "--delay", x) for x in (0, 0.1, 0.5, 1)]
        assert d[0] > d[1] > d[2] > d[3] and d[3] < 20 and d[2] < 30, d
        assert abs(sup(*IDEAL, *sig, "--delay", -0.5) - d[2]) < 1.5


def test_whole_radios_for_30_and_40_db():
    """docs/rt950-polar-sim.md's two recipes: 8000 writes/s of 10 Hz, PA at 8000/s with 64
    levels, paths matched, gives 30 dB; 16000 writes/s and PA at 32000/s with 256 levels, 40"""
    r30 = ("--writes", 8000, "--env-rate", 8000, "--levels", 64)
    r40 = ("--writes", 16000, "--env-rate", 32000, "--levels", 256)
    for sig in ((), SPEECH):
        assert sup(*r30, *sig) > 30
        assert sup(*r40, *sig) > 39.5


def test_wav_and_raw_input_agree():
    """an 8 kHz 16-bit wav and the same samples raw give the same result"""
    t = np.arange(16000) / 8000
    x = (8000 * (np.sin(2 * np.pi * 700 * t) + np.sin(2 * np.pi * 1900 * t))).astype("<i2")
    with tempfile.TemporaryDirectory() as d:
        raw, wav = os.path.join(d, "x.raw"), os.path.join(d, "x.wav")
        x.tofile(raw)
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(x.tobytes())
        a, b = run("--in", raw), run("--in", wav)
    assert a["sup_db"] == b["sup_db"] and float(a["sup_db"]) > 25


@pytest.mark.skipif(not os.path.exists(HTS1A), reason="needs the sotto submodule (codec2 speech)")
def test_real_speech():
    """codec2's hts1a.raw behaves like the synthetic speech"""
    assert sup("--in", HTS1A, *IDEAL) > 50
    assert sup("--in", HTS1A, "--writes", 8000, "--env-rate", 8000, "--levels", 64) > 30
