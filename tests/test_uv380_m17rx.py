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
    for name, most in (("c6fmtap.c", 5120), ("c6fmtap_1c.c", 5120), ("c6phyrx.c", 5120), ("c6dmrrx.c", 5120),
                       ("c6m17rx.c", 9216), ("c6m17rx_tap.c", 9216)):
        code, entry = uv380mod.build(os.path.join(MOD, name))
        assert entry == 0 and len(code) < most, (name, len(code))


def test_on_radio_receiver_fits_the_module_arena():
    """c6m17rx keeps m17_rx, the frame and an SMS in scratch: image + state within 16 KB"""
    import re
    from modem import uv380mod
    code, _ = uv380mod.build(os.path.join(MOD, "c6m17rx_tap.c"))
    h = open(os.path.join(os.path.dirname(__file__), "..", "c", "common", "m17_rx.h")).read()
    yn, ntap = (int(re.search(rf"#define {k} (\d+)", h).group(1)) for k in ("M17_YN", "M17_NTAP"))
    state = 4 * ntap + 2 * yn + 32 + 64 + 2 * 248 + 4 * (16 + 488 + 368) + 64    # m17_rx, about
    sms = int(re.search(r"#define SMS_MAX (\d+)u", open(os.path.join(MOD, "c6m17rx.c")).read()).group(1))
    assert (len(code) + 7) // 8 * 8 + state + 64 + sms + 128 <= 16384


# ---- the C receiver for the radio (c/common/m17_rx.c) ------------------------------------------

def _tap_audio(data, rng, noise_s=1.0, invert=False):
    """M17 bytes as the UV-380's tap would hand them over: 8 kHz, 2400 Hz = 8000 units, flat to
    3.4 kHz, with noise-only stretches (full scale, as the tap reads with no signal) either side"""
    from scipy import signal
    from modem import m17
    x = m17.modulate(data, fs=48000, alpha=0.5) / 2400.0 * 8000.0
    b, a = signal.butter(6, 3400.0, fs=48000)
    x = signal.lfilter(b, a, x)[::6] + rng.normal(0, 300, len(x) // 6 + (len(x) % 6 > 0))
    quiet = lambda: np.clip(rng.normal(0, 12000, int(8000 * noise_s)), -32768, 32767)  # noqa: E731
    y = np.concatenate([quiet(), x, quiet()]) * (-1 if invert else 1)
    return np.clip(np.round(y), -32768, 32767).astype("<i2")


def _c_receive(tmp_path, audio, invert=False):
    import subprocess
    root = os.path.join(os.path.dirname(__file__), "..", "c", "common")
    subprocess.run(["make", "-s", "-C", root, "m17_rx_host"], check=True)
    raw = tmp_path / "tap.raw"
    raw.write_bytes(audio.tobytes())
    out = subprocess.run([os.path.join(root, "m17_rx_host"), str(raw)] + (["invert"] if invert else []),
                         check=True, capture_output=True, text=True).stdout.splitlines()
    return [ln.split() for ln in out if ln.startswith("f ")], out[-1]


def test_c_receiver_decodes_sms_voice_and_bert(tmp_path):
    from modem import m17
    rng = np.random.default_rng(1)
    data = m17.tx_sms(["CQ M17 DE AG7EW"]) + m17.tx_voice(bytes(range(64))) + m17.tx_bert(20)
    frames, end = _c_receive(tmp_path, _tap_audio(data, rng, invert=True), invert=True)
    kinds = [f[2] for f in frames]
    assert all(f[3] == "ok=1" for f in frames if f[2] != "eot"), frames
    lsfs = [f for f in frames if f[2] == "lsf"]
    assert len(lsfs) == 2 and all("src=AG7EW" in f for f in lsfs)
    packets = "".join(f[7] for f in frames if f[2] == "packet")
    assert bytes.fromhex(packets).startswith(m17.sms_payload("CQ M17 DE AG7EW"))
    stream = b"".join(bytes.fromhex(f[7])[2:] for f in frames if f[2] == "stream")
    assert stream == bytes(range(64))
    assert kinds.count("bert") == 20 and kinds.count("eot") == 3


def test_c_receiver_finds_nothing_in_noise(tmp_path):
    rng = np.random.default_rng(2)
    noise = np.clip(np.round(rng.normal(0, 12000, 8000 * 60)), -32768, 32767).astype("<i2")
    frames, end = _c_receive(tmp_path, noise)
    assert frames == [], frames[:5]
