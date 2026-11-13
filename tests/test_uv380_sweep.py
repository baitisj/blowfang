"""tools/uv380_sweep.py: the stepped-tone wav, taken to 8 kHz through a known band-pass and
noise, must give that band-pass back (each tone's level against 1000 Hz, the corners), and find
its sync tone after the PSK31 ident. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import uv380_sweep as sw  # noqa: E402

signal = pytest.importorskip("scipy.signal")


def test_sweep_gives_back_a_known_passband():
    y = sw.make_wav()
    assert 50 < len(y) / sw.FS_TX < 65 and np.max(np.abs(y)) <= sw.LEVEL + 1e-6
    x = signal.resample_poly(y, 1, 6) * 30000 / sw.LEVEL * 0.7
    b, a = signal.butter(2, [300 / 4000, 2800 / 4000], "bandpass")
    x = signal.lfilter(b, a, x)
    x = np.concatenate([np.random.default_rng(1).normal(0, 300, 12345), x + np.random.default_rng(2).normal(
        0, 300, len(x))])
    m = sw.measure(x)
    assert m is not None and abs(m["sync"] - (12345 + sw.sync_offset() * 8000)) < 80
    w, h = signal.freqz(b, a, worN=[f / 4000 * np.pi for f in sw.STEPS])
    ref = 20 * np.log10(abs(h[sw.STEPS.index(1000)]))
    want = {f: 20 * np.log10(abs(v)) - ref for f, v in zip(sw.STEPS, h)}
    for f, p, db, pur, _ in m["rows"]:
        if 200 <= f <= 3600:        # the decimation's own droop near 4 kHz, and the noise
            assert abs(db - want[f]) < (0.5 if f <= 3200 else 1.5), (f, p, db, want[f])
        if p == "A" and 400 <= f <= 2600:
            assert pur > 0.95
    lo, hi = sw.corners(m["rows"], "A", 3.0)
    assert 300 <= lo <= 400 and 2600 <= hi <= 2900, (lo, hi)
    assert abs(m["ref_amp"]["A"] / m["ref_amp"]["B"] - 10 ** 0.5) < 0.1
    assert 250 < m["noise"] < 400
    assert any("within 3 dB" in ln for ln in sw.report(m))


def test_no_sync_says_so():
    assert sw.measure(np.random.default_rng(3).normal(0, 1000, 80000)) is None
    assert sw.report(None) == ["no sync tone found"]
