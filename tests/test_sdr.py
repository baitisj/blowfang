"""modem.sdr stands alone: pluto_bench rx OUT.wav and k6 --iq import it."""
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_imports_without_c4fm():
    # a fresh interpreter, so another test's imports can't hide a missing module
    r = subprocess.run([sys.executable, "-c", "import modem.sdr, sys; "
                        "assert 'modem.c4fm' not in sys.modules"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_fm_round_trip():
    sys.path.insert(0, ROOT)
    from modem import sdr
    t = np.arange(48000) / 48000.0
    f = 2500.0 * np.sin(2 * np.pi * 1000.0 * t)
    d = sdr.fm_demodulate(sdr.fm_modulate(f), bw_hz=8000.0)
    mid = slice(2000, -2000)
    lag = int(np.argmax(np.correlate(d[4000:8000], f[4000:4400], "valid")))
    assert np.max(np.abs(d[mid])) > 2300
    assert np.std(d[4000 + lag:4000 + lag + 30000] - f[4000:34000]) < 100
