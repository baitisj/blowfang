"""tools/pluto_parrot.py: the Pluto parrot hears a YSF or M17 sotto burst and codes the same bytes
again, without a Pluto (synthetic FM IQ); and the bench runner's checks of a parrot step."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import pluto_parrot as pp  # noqa: E402
from modem import m17, ysf  # noqa: E402


def fm_iq(disc_hz, fs_in=48000, fs=pp.FS, off_hz=300.0, snr_db=30.0, lead_s=1.0, seed=1):
    """a discriminator track (Hz) -> FM IQ at fs, with quiet noise before and after"""
    from scipy import signal
    d = signal.resample_poly(disc_hz, fs // fs_in, 1) + off_hz
    iq = np.exp(2j * np.pi * np.cumsum(d) / fs)
    rng = np.random.default_rng(seed)
    n = int(lead_s * fs)
    sig = np.concatenate([np.zeros(n), iq, np.zeros(2 * n)])
    noise = (rng.standard_normal(len(sig)) + 1j * rng.standard_normal(len(sig))) * 10 ** (-snr_db / 20) / np.sqrt(2)
    return (sig + noise).astype(np.complex64)


def sotto(n):
    return bytes((7 * i + 3) & 0xFF for i in range(n * 6))


def chunks(iq, n=13107):
    for k in range(0, len(iq), n):
        yield iq[k:k + n]


@pytest.mark.parametrize("mode", ["ysf", "m17"])
def test_parrot_hears_and_replies_with_the_same_bytes(mode):
    msg = sotto(40)
    x, dev = pp.reply_wav(mode, msg)
    iq = fm_iq(x * dev)
    b = pp.Bursts()
    bursts = [y for y in (b.feed(c) for c in chunks(iq)) if y is not None]
    assert len(bursts) == 1
    got_mode, got, src, lines = pp.heard(bursts[0])
    assert got_mode == mode, lines
    assert got[:len(msg)] == msg
    assert "AG7EW" in src


def test_noise_alone_is_not_a_burst():
    rng = np.random.default_rng(2)
    b = pp.Bursts()
    for _ in range(200):
        y = ((rng.standard_normal(13107) + 1j * rng.standard_normal(13107)) * 0.01).astype(np.complex64)
        assert b.feed(y) is None


def test_fm_voice_gets_no_reply():
    t = np.arange(int(3 * 48000)) / 48000
    iq = fm_iq(2500 * np.sin(2 * np.pi * 1000 * t))
    b = pp.Bursts()
    bursts = [y for y in (b.feed(c) for c in chunks(iq)) if y is not None]
    assert len(bursts) == 1
    assert pp.heard(bursts[0])[0] is None


def test_mix_decimate_brings_the_channel_to_zero():
    rate, q, f = 2_400_000, 10, -50_000
    n = 131072
    st, out = {}, []
    for k in range(4):
        t = (k * n + np.arange(n)) / rate
        z = np.exp(2j * np.pi * f * t) * 1000
        raw = np.empty(2 * n, np.int16)
        raw[0::2], raw[1::2] = np.round(z.real), np.round(z.imag)
        out.append(pp.mix_decimate(raw.tobytes(), st, rate, q, -f))
    y = np.concatenate(out)
    assert len(y) == 4 * n // q
    ph = np.unwrap(np.angle(y[100:]))
    assert abs(np.mean(np.diff(ph))) < 1e-3          # no rotation left: the channel sits at 0 Hz


def test_reply_wavs_decode_as_sent():
    msg = sotto(25)
    x, dev = pp.reply_wav("ysf", msg)
    s = ysf.summarize(ysf.demod(x * dev))
    assert s["sotto"][:len(msg)] == msg
    x, dev = pp.reply_wav("m17", msg)
    s = m17.summarize(m17.demod(x * dev))
    assert s["sotto"][:len(msg)] == msg


def runner_problems(args, freq=441.85):
    import bench_runner as br
    job = {"id": "parrot1", "who": "t", "freq_mhz": freq, "minutes": 26,
           "steps": [{"do": "pluto", "args": args}]}
    return [p for p in br.check_job(job) if "checkout" not in p and "keeper" not in p]


def test_runner_takes_a_parrot_on_the_uv380_channel():
    assert runner_problems(["parrot", "--hz", "441850000", "--gain", "-40", "--minutes", "20"]) == []


@pytest.mark.parametrize("args, why", [
    (["parrot", "--gain", "-40"], "needs --hz"),
    (["parrot", "--hz", "441850000", "--gain", "-20"], "above"),
    (["parrot", "--hz", "441850000", "--minutes", "40"], "over 25"),
    (["parrot", "--hz", "441850000", "--save", "/tmp/x"], "--save"),
    (["parrot", "--hz", "146520000"], ""),
])
def test_runner_refuses_a_bad_parrot(args, why):
    probs = runner_problems(args)
    assert probs and any(why in p for p in probs), probs
