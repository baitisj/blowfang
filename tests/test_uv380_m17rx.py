"""M17 receive on the UV-380 (docs/uv380-m17-rx.md): the Pluto test signal and the readers for the
FM tap (c6fmtap) and the chip's own demodulator (c6phyrx), on simulated logs.
SPDX-License-Identifier: MIT"""
import os

import numpy as np
import pytest

pytest.importorskip("scipy")
from modem import m17rx  # noqa: E402

MOD = os.path.join(os.path.dirname(__file__), "..", "firmware", "uv380-openrtx", "mod")


def test_test_signal_has_tones_then_bert():
    x, data = m17rx.test_signal(2.0, 1.0)
    assert np.max(np.abs(x)) < 1.0                       # no clipping at --dev 4800
    r, share = m17rx.tone_response(x[:8000 * 6][::6])
    assert share > 0.99 and np.all(np.abs(r[:-1]) < 0.5)
    assert data.count(bytes([0xDF, 0x55])) >= m17rx.bert_frames(1.0)


def test_dc_coupled_tap_reads_flat_and_decodes():
    out = "\n".join(m17rx.tap_report(m17rx.sim_tap_log(tones_s=2.0, bert_s=2.0)))
    assert "within 3 dB: 10-3000 Hz" in out
    assert " 0 of " in out and "BERT: no frames" not in out


def test_inverted_tap_decodes():
    """the UV-380's tap reads +3 negative (11-18): the reader tries both polarities"""
    out = "\n".join(m17rx.tap_report(m17rx.sim_tap_log(tones_s=1.0, bert_s=2.0, invert=True)))
    assert "BERT: no frames" not in out and "/0-" in out


def test_300hz_high_pass_closes_the_eye():
    out = "\n".join(m17rx.tap_report(m17rx.sim_tap_log(hp_hz=300.0, tones_s=2.0, bert_s=2.0)))
    assert "within 3 dB: 300-" in out and "BERT: no frames" in out


def test_tap_settings_reported_one_by_one():
    lines = m17rx.sim_tap_log(tones_s=4.0, bert_s=0.5, v34=(0x1C, 0x3C), step_s=2.0)
    out = "\n".join(m17rx.tap_report(lines))
    assert "0x34=1c (" in out and "0x34=3c (" in out


def test_phy_windows_join_into_m17():
    out = "\n".join(m17rx.phy_report(m17rx.sim_phy_log(nframes=30)))
    assert "29 of 29 gaps 192 symbols" in out and "0 of 5910 bits wrong" in out


def test_phy_lost_window_splits_the_run_and_inverted_polarity_is_found():
    out = "\n".join(m17rx.phy_report(m17rx.sim_phy_log(nframes=30, drop=10)))
    assert "30(" in out and "60(1)" in out and out.count("\nrun ") == 2
    out = "\n".join(m17rx.phy_report(m17rx.sim_phy_log(nframes=30, invert=True)))
    assert "polarity -" in out and "29 of 29 gaps" in out


def test_receive_modules_build():
    from modem import uv380mod
    for name, most in (("c6fmtap.c", 5120), ("c6fmtap_1c.c", 5120), ("c6phyrx.c", 5120), ("c6dmrrx.c", 5120)):
        code, entry = uv380mod.build(os.path.join(MOD, name))
        assert entry == 0 and len(code) < most, (name, len(code))
