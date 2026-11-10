"""The UV-380's Morse ident (c/common/cw_tx.c) keys exactly as modem.k6.cw, the ident the
IC-7100 and K6 tools send: same marks and gaps, same tone, same length.
Needs make -C c/common. SPDX-License-Identifier: MIT"""
import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest
from scipy import signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "c", "common", "cw_tx_host")
sys.path.insert(0, ROOT)
from modem import k6  # noqa: E402

pytestmark = pytest.mark.skipif(not os.path.exists(EXE), reason="needs make -C c/common")


def render(text, fs=48000, wpm=20):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "cw.raw")
        subprocess.run([EXE, text, out, str(fs), str(wpm), "800", "13107"], check=True)
        return np.frombuffer(open(out, "rb").read(), "<i2").astype(float) / 32768


def runs(x, fs):
    env = np.convolve(np.abs(signal.hilbert(x)), np.ones(fs // 500) / (fs // 500), "same")
    key = env > 0.5 * np.max(env)
    edges = np.flatnonzero(np.diff(key.astype(int))) + 1
    return np.diff(np.concatenate([[0], edges])) / fs, key[0]


@pytest.mark.parametrize("text,fs,wpm", [("AG7EW", 48000, 20), ("AG7EW TEST", 8000, 20),
                                         ("de AG7EW/9", 48000, 25)])
def test_keying_matches_modem(text, fs, wpm):
    got, want = render(text, fs, wpm), k6.cw(text, fs, wpm)
    assert abs(len(got) - len(want)) <= fs // 200 + 2
    rg, kg = runs(got, fs)
    rw, kw = runs(want, fs)
    assert kg == kw and len(rg) == len(rw)
    assert np.max(np.abs(rg[1:-1] - rw[1:-1])) < 0.003       # every mark and gap within 3 ms
    f = np.abs(np.fft.rfft(got, 1 << 18))
    assert abs(np.argmax(f) * fs / (1 << 18) - 800) < 2
    assert 0.37 < np.max(np.abs(got)) <= 0.4      # 8 kHz: 10 samples a cycle miss the peak
