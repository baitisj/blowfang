"""YSF (C4FM) receive on the UV-380 (docs/uv380-multimode.md milestone 5): c/common/ysf_rx.c, the C
receiver for the HR_C6000's 8 kHz FM tap, on simulated tap audio made by modem/ysf.py.
SPDX-License-Identifier: MIT"""
import os
import subprocess

import numpy as np
import pytest

pytest.importorskip("scipy")
from modem import ysf  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "c", "common")


def _tap_audio(data, rng, noise=300.0, invert=False, outer_hz=2712.0):
    """YSF bytes as the UV-380's tap would hand them over: 8 kHz, 2400 Hz = 8000 units, flat to
    3.4 kHz, a 0x77 preamble first, noise-only stretches (full scale) either side"""
    from scipy import signal
    x = ysf.modulate(bytes([ysf.PREAMBLE]) * 24 + data, fs=48000, alpha=0.2, outer_hz=outer_hz) / 2400.0 * 8000.0
    b, a = signal.butter(6, 3400.0, fs=48000)
    x = signal.lfilter(b, a, x)[::6]
    x = x + rng.normal(0, noise, len(x))
    quiet = lambda: np.clip(rng.normal(0, 12000, 8000), -32768, 32767)  # noqa: E731
    y = np.concatenate([quiet(), x, quiet()]) * (-1 if invert else 1)
    return np.clip(np.round(y), -32768, 32767).astype("<i2")


def _c_receive(tmp_path, audio, invert=False):
    subprocess.run(["make", "-s", "-C", ROOT, "ysf_rx_host"], check=True)
    raw = tmp_path / "tap.raw"
    raw.write_bytes(audio.tobytes())
    out = subprocess.run([os.path.join(ROOT, "ysf_rx_host"), str(raw)] + (["invert"] if invert else []),
                         check=True, capture_output=True, text=True).stdout.splitlines()
    frames = []
    for ln in out:
        if not ln.startswith("f "):
            continue
        w = ln.split()
        f = dict(kv.split("=", 1) for kv in w[2:] if "=" in kv)
        f["at"] = int(w[1])
        f["data"] = bytes.fromhex(w[-1]) if "=" not in w[-1] else b""
        frames.append(f)
    return frames, out[-1]


def test_sotto_vw_decodes_every_superframe(tmp_path):
    sotto = bytes(range(6 * 23))
    rng = np.random.default_rng(1)
    frames, end = _c_receive(tmp_path, _tap_audio(ysf.tx_sotto(sotto), rng))
    assert [f["fi"] for f in frames] == ["0"] + ["1"] * 5 + ["2"], frames
    hdr = frames[0]["data"]
    assert hdr[:10].strip() == b"ALL" and hdr[10:20].strip() == b"AG7EW" and frames[0]["ok"] == "3"
    comms = frames[1:-1]
    assert all(f["ok"] == "1f" and f["sotto"] == "1" and f["fich"] == "1" for f in comms)
    assert [f["fn"] for f in comms] == [f"{k}/6" for k in range(5)]
    got = b"".join(f["data"] for f in comms)
    assert got[:len(sotto)] == sotto and got[len(sotto):] == sotto[-6:] * 2    # the last one repeats
    assert end == "end frames=7 lost=0 bad=0"


def test_frames_are_100_ms_apart(tmp_path):
    rng = np.random.default_rng(2)
    frames, _ = _c_receive(tmp_path, _tap_audio(ysf.tx_sotto(bytes(60)), rng))
    gaps = np.diff([f["at"] for f in frames])
    assert np.all(np.abs(gaps - 2400) <= 3), gaps


def test_inverted_tap_decodes(tmp_path):
    """the UV-380's tap reads +3 negative: the host's invert (the module's YSF_INVERT)"""
    rng = np.random.default_rng(3)
    frames, end = _c_receive(tmp_path, _tap_audio(ysf.tx_sotto(bytes(range(60))), rng, invert=True), invert=True)
    assert b"".join(f["data"] for f in frames if f["fi"] == "1") == bytes(range(60))


def test_data_fr_text(tmp_path):
    text = "AG7EW YSF DATA TEST"
    rng = np.random.default_rng(4)
    frames, _ = _c_receive(tmp_path, _tap_audio(ysf.tx_data(4, text=text), rng))
    dfr = [f for f in frames if f["fi"] == "1"]
    assert len(dfr) == 4 and all(f["dt"] == "1" and f["ok"] == "3" for f in dfr)
    assert dfr[0]["data"][:20] == b"ALL       AG7EW     "
    blocks = dfr[1]["data"][20:] + dfr[2]["data"] + dfr[3]["data"]
    assert blocks == b"".join(ysf.data_chunks(text, 8)[:5])


def test_dn_voice_gives_its_fich(tmp_path):
    rng = np.random.default_rng(5)
    frames, _ = _c_receive(tmp_path, _tap_audio(ysf.tx_dn(8), rng, outer_hz=2700.0))
    comms = [f for f in frames if f["fi"] == "1"]
    assert len(comms) == 8 and all(f["dt"] == "2" and f["fich"] == "1" for f in comms)
    assert [f["fn"] for f in comms] == [f"{k % 8}/7" for k in range(8)]


def test_lost_fich_reads_on_as_sotto(tmp_path):
    """a frame whose FICH is gone (here: its FICH bytes inverted) is read on as the next sotto VW
    frame once locked; its CRC-8s decide"""
    sotto = bytes(range(6 * 20))
    data = bytearray(ysf.tx_sotto(sotto))
    k = 3                                   # header, FN 0, FN 1, then this one (FN 2)
    for i in range(5, 30):
        data[24 + 120 * k + i] ^= 0xFF
    rng = np.random.default_rng(6)
    frames, end = _c_receive(tmp_path, _tap_audio(bytes(data), rng))
    lost = [f for f in frames if f["fich"] == "0"]
    assert len(lost) == 1 and lost[0]["fn"] == "2/6" and lost[0]["ok"] == "1f"
    assert b"".join(f["data"] for f in frames if f["fi"] == "1") == sotto
    assert end.endswith("bad=1")


@pytest.mark.parametrize("noise", [2500.0, 3000.0])
def test_noisy_tap_still_decodes(tmp_path, noise):
    sotto = bytes(range(6 * 20))
    rng = np.random.default_rng(7)
    frames, _ = _c_receive(tmp_path, _tap_audio(ysf.tx_sotto(sotto), rng, noise=noise))
    assert b"".join(f["data"] for f in frames if f["fi"] == "1") == sotto


def test_finds_nothing_in_noise(tmp_path):
    rng = np.random.default_rng(8)
    noise = np.clip(np.round(rng.normal(0, 12000, 8000 * 60)), -32768, 32767).astype("<i2")
    frames, end = _c_receive(tmp_path, noise)
    assert frames == [] and end == "end frames=0 lost=0 bad=0"


MOD = os.path.join(os.path.dirname(__file__), "..", "firmware", "uv380-openrtx", "mod")


def test_receive_module_builds_and_fits_the_arena():
    """c6ysfrx keeps ysf_rx and a frame in scratch: image + state (+ the module's counts) in 16 KB"""
    import re
    from modem import uv380mod
    subprocess.run(["make", "-s", "-C", ROOT, "ysf_rx_host"], check=True)
    out = subprocess.run([os.path.join(ROOT, "ysf_rx_host"), "--sizeof"], check=True, capture_output=True,
                         text=True).stdout
    state = sum(int(v) for v in re.findall(r"=(\d+)", out)) + 64
    for name in ("c6ysfrx.c", "c6ysfrx_tap.c"):
        code, entry = uv380mod.build(os.path.join(MOD, name))
        assert entry == 0 and (len(code) + 7) // 8 * 8 + state <= 16384, (name, len(code), state)


def test_wav_for_the_pluto(tmp_path):
    """python3 -m modem.ysf wav: the sotto test transmission as the Pluto sends it (--dev 5400 puts
    the outer symbol at 2700 Hz), and it decodes back"""
    from modem import ysf as y
    out = tmp_path / "s.wav"
    assert y.main(["wav", str(out), "--sotto", "40", "--repeat", "2"]) == 0
    disc = y.read_wav_disc(str(out)) * 5400.0 / y.WAV_FULL_DEV
    frames = y.demod(disc)
    s = y.summarize(frames)
    assert s["sotto_ok"] == s["sotto_n"] == 2 * 40
    assert s["sotto"][:80 * 6] == y.sotto_test(40) * 2
