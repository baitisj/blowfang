"""The portable PHY F receiver (c/common/fsk_rx.c), which the UV-380 runs beside range_rx in
PAGER mode to hear the K6 pager's plain FSK packets: pager_tx's FFSK audio at 48 kHz, taken to
8 kHz through a voice-band filter, with noise, the sender's clock off by up to 0.5 %, the UV-380's
squeezed tones (1209/1804 Hz, measured 11-14), and L as well as XL packets, must come back packet
for packet, and the message whole through pager_rx; noise alone must give no packet.
Needs make -C c/common. SPDX-License-Identifier: MIT"""
import os
import subprocess
import tempfile

import numpy as np
import pytest

signal = pytest.importorskip("scipy.signal")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, "c", "common")
TX = os.path.join(C, "pager_tx_host")
FRX = os.path.join(C, "fsk_rx_host")
RX = os.path.join(C, "pager_rx_host")
pytestmark = pytest.mark.skipif(not all(os.path.exists(p) for p in (TX, FRX, RX)),
                                reason="needs make -C c/common")


def frames_of(n, seed=1):
    rng = np.random.default_rng(seed)
    return bytes(rng.integers(0, 256, 6 * n, dtype=np.uint8))


def tx(frames, *opts):
    """-> (payloads back to back, 48 kHz audio)"""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "m.sot")
        open(src, "wb").write(frames)
        subprocess.run([TX, "packets", src, os.path.join(d, "p.bin"), *opts], check=True)
        subprocess.run([TX, "audio", src, os.path.join(d, "a.raw"), "--fs", "48000", "--level", "12000",
                        *opts], check=True)
        return open(os.path.join(d, "p.bin"), "rb").read(), np.fromfile(os.path.join(d, "a.raw"), "<i2")


def channel(a, ppm=0, noise=0.0, seed=1):
    """48 kHz -> 8 kHz (the sender's clock ppm off), 300-2300 Hz, noise"""
    x = signal.resample_poly(a.astype(float), 1_000_000 + ppm, 6_000_000)
    x = signal.sosfilt(signal.butter(4, [300, 2300], "bandpass", fs=8000, output="sos"), x)
    x = np.concatenate([np.zeros(4000), x, np.zeros(4000)])
    x += np.random.default_rng(seed).normal(0, noise, len(x))
    return np.clip(np.round(x), -32768, 32767).astype("<i2")


def receive(x):
    """-> [(len, ok, payload)]"""
    with tempfile.TemporaryDirectory() as d:
        x.tofile(os.path.join(d, "x.raw"))
        out = subprocess.run([FRX, os.path.join(d, "x.raw")], check=True, capture_output=True,
                             text=True).stdout.splitlines()
    res = []
    for ln in out:
        f = ln.split()
        res.append((int(f[2][4:]), int(f[3][3:]), bytes.fromhex(f[-1])))
    return res


def split(blob, n):
    return [blob[i:i + n] for i in range(0, len(blob), n)]


@pytest.mark.parametrize("ppm,noise", [
    (0, 0),
    (0, 1000),
    (-5000, 500),
    (5000, 500),
])
def test_xl_packets(ppm, noise):
    pk, a = tx(frames_of(230, 2))
    got = [p for n, ok, p in receive(channel(a, ppm, noise)) if ok]
    assert got == split(pk, 128)


def test_uv380_tones():
    pk, a = tx(frames_of(120, 3), "--mark", "1209", "--space", "1804")
    got = [p for n, ok, p in receive(channel(a, 0, 800)) if ok]
    assert got == split(pk, 128)


def test_l_packets():
    pk, a = tx(frames_of(60, 4), "--profile", "L")
    got = receive(channel(a, 0, 500))
    assert [n for n, ok, p in got if ok] == [64] * (len(pk) // 64)
    assert [p for n, ok, p in got if ok] == split(pk, 64)


def test_message_whole():
    fr = frames_of(230, 5)
    pk, a = tx(fr)
    heard = [p for n, ok, p in receive(channel(a, 2000, 800)) if ok]
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "p.bin"), "wb").write(b"".join(heard))
        subprocess.run([RX, os.path.join(d, "p.bin"), "128", os.path.join(d, "m.sot")], check=True,
                       capture_output=True)
        assert open(os.path.join(d, "m.sot"), "rb").read() == fr


def test_noise_alone():
    x = np.clip(np.round(np.random.default_rng(9).normal(0, 3000, 8000 * 60)), -32768, 32767)
    assert [r for r in receive(x.astype("<i2")) if r[1]] == []
