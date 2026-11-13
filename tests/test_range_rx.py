"""PHY R received on audio (c/common/range_rx.c, the UV-380's receiver: firmware/uv380-openrtx/
mod/rangerx.c). Bursts from the portable sender (c/common/range_tx.c with ffsk_tx.c at 48 kHz, as
the IC-7100 plays a wav), taken down to the UV-380's 8 kHz, through what the air and the radios do
to them: noise, a clock off by 200 ppm, de-emphasis, a sender's audio high-pass, the receiver's
audio upside down, noise between bursts (false syncs must not swallow a packet). Every packet must
come back with its CRC right, and the module that wraps the receiver must build position-
independent and fit the 16 KB module arena with its ring.
Needs make -C c/common (range_tx_host, range_rx_host) and scipy. SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modem import k6r  # noqa: E402

TX = os.path.join(ROOT, "c", "common", "range_tx_host")
RX = os.path.join(ROOT, "c", "common", "range_rx_host")
MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod", "rangerx.c")
pytestmark = pytest.mark.skipif(not (os.path.exists(TX) and os.path.exists(RX)),
                                reason="needs make -C c/common")
signal = pytest.importorskip("scipy.signal")

PL = k6r.test_payloads(6)


@pytest.fixture(scope="module")
def burst():
    """the six-packet burst at 8 kHz (sent at 48 kHz, low-passed and decimated)"""
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "in.bin"), "wb").write(b"".join(PL))
        subprocess.run([TX, "audio", os.path.join(d, "in.bin"), os.path.join(d, "a.raw"), "--fs", "48000",
                        "--level", "12000"], check=True)
        a = np.fromfile(os.path.join(d, "a.raw"), "<i2").astype(float)
    return signal.resample_poly(a, 1, 6)


def receive(x):
    """-> [(sync bit, corr, errors, ok, payload)]"""
    with tempfile.TemporaryDirectory() as d:
        np.clip(np.round(x), -32768, 32767).astype("<i2").tofile(os.path.join(d, "x.raw"))
        out = subprocess.run([RX, os.path.join(d, "x.raw"), "8000"], check=True, capture_output=True,
                             text=True).stdout
    pk = []
    for ln in out.splitlines():
        w = ln.split()
        f = dict(v.split("=") for v in w[2:6])
        pk.append((int(w[1]), int(f["c"]), int(f["e"]), int(f["ok"]), bytes.fromhex(w[6])))
    return pk


def good(pk):
    return [p for _, _, _, ok, p in pk if ok and p in PL]


def framed(a, before=4000, after=4000):
    return np.concatenate([np.zeros(before), a, np.zeros(after)])


def test_clean(burst):
    pk = receive(framed(burst))
    assert good(pk) == PL
    assert [e for _, _, e, _, _ in pk] == [0] * 6
    # the first sync after 600 bits of silence, 600 of lead and its 32; then a packet, a gap and a
    # sync apart
    assert [s for s, _, _, _, _ in pk] == [1232 + 2224 * i for i in range(6)]


@pytest.mark.parametrize("name", ["+200ppm", "-200ppm", "deemph", "hp300", "inverted"])
def test_channel(burst, name):
    x = framed(burst)
    if name.endswith("ppm"):
        r = 1 + (2e-4 if name[0] == "+" else -2e-4)
        x = np.interp(np.arange(0, len(x), r), np.arange(len(x)), x)
    elif name == "deemph":
        al = np.exp(-1 / (8000 * 750e-6))
        x = signal.lfilter([1 - al], [1, -al], x) * 3
    elif name == "hp300":
        b, a = signal.butter(2, 300 / 4000, "high")
        x = signal.lfilter(b, a, x)
    else:
        x = -x
    assert good(receive(x)) == PL


@pytest.mark.parametrize("snr", [6, 0, -2])
def test_noise(burst, snr):
    """audio-band SNR (the whole 4 kHz): every packet down to -2 dB"""
    rng = np.random.default_rng(snr + 100)
    x = framed(burst)
    x = x + rng.normal(0, np.sqrt(np.mean(burst ** 2)) / 10 ** (snr / 20), len(x))
    assert good(receive(x)) == PL


def test_noise_between_bursts(burst):
    """seconds of noise around two bursts: false syncs in it come and go, no packet is lost"""
    rng = np.random.default_rng(7)
    sig = np.sqrt(np.mean(burst ** 2))
    lost = 0
    for _ in range(3):
        gap = np.zeros(int(8000 * rng.uniform(3, 8)))
        x = np.concatenate([gap, burst, gap[:20000], burst, gap])
        x = x + rng.normal(0, sig / 10 ** (3 / 20), len(x))
        lost += 12 - len(good(receive(x)))
    assert lost == 0


@pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")
def test_the_module_fits():
    from modem import uv380mod
    code, entry = uv380mod.build(MOD)
    assert entry == 0 and uv380mod.source_table(MOD) == "openrtx"
    image = (16 + len(code) + 8 + 7) // 8 * 8
    # rr_state with RR_HIST 32 (as the module builds it) and the 1024-halfword ring
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "sz.c")
        open(src, "w").write('#include <stdio.h>\n#define RR_HIST 32\n#include "range_rx.h"\n'
                             'int main(void) { printf("%zu\\n", sizeof(rr_state)); return 0; }\n')
        subprocess.run(["cc", "-I", os.path.join(ROOT, "c", "common"), src, "-o", os.path.join(d, "sz")],
                       check=True)
        state = int(subprocess.run([os.path.join(d, "sz")], capture_output=True, text=True).stdout)
    assert image + state + 2 * 1024 <= 16384, (image, state)
