"""A sotto message put back together from M17 and YSF receive (c/common/sotto_rx.c,
docs/uv380-multimode.md milestone 5), on simulated UV-380 tap audio made by modem/m17.py and
modem/ysf.py: the receive half of the sotto framing the radio sends.
SPDX-License-Identifier: MIT"""
import os
import subprocess

import numpy as np
import pytest

pytest.importorskip("scipy")
from modem import m17, ysf  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "c", "common")


def _tap(x48, rng, noise=300.0):
    """a 48 kHz discriminator signal (2400 Hz = 8000 units already) as the tap hands it over:
    8 kHz, flat to 3.4 kHz, noise-only stretches either side"""
    from scipy import signal
    b, a = signal.butter(6, 3400.0, fs=48000)
    x = signal.lfilter(b, a, x48)[::6]
    x = x + rng.normal(0, noise, len(x))
    quiet = lambda: np.clip(rng.normal(0, 12000, 8000), -32768, 32767)  # noqa: E731
    return np.clip(np.round(np.concatenate([quiet(), x, quiet()])), -32768, 32767).astype("<i2")


def m17_audio(data, rng, **kw):
    return _tap(m17.modulate(data, fs=48000, alpha=0.5) / 2400.0 * 8000.0, rng, **kw)


def ysf_audio(data, rng, **kw):
    return _tap(ysf.modulate(bytes([ysf.PREAMBLE]) * 24 + data, fs=48000, alpha=0.2, outer_hz=2712.0)
                / 2400.0 * 8000.0, rng, **kw)


def receive(tmp_path, air, audio):
    subprocess.run(["make", "-s", "-C", ROOT, "sotto_rx_host"], check=True)
    raw = tmp_path / "tap.raw"
    raw.write_bytes(audio.tobytes())
    out = subprocess.run([os.path.join(ROOT, "sotto_rx_host"), air, str(raw)], check=True,
                         capture_output=True, text=True).stdout.splitlines()
    msgs = []
    for ln in out:
        if ln.startswith("msg "):
            w = ln.split()
            m = dict(kv.split("=", 1) for kv in w[1:-1])
            m = {k: int(v) for k, v in m.items()}
            m["data"] = bytes.fromhex(w[-1])
            msgs.append(m)
    return msgs, out[-1]


def sotto(n, seed=7):
    return bytes(np.random.default_rng(seed).integers(0, 256, 6 * n, dtype=np.uint8))


@pytest.mark.parametrize("n", [1, 24, 75])
def test_m17_stream_comes_back_whole(tmp_path, n):
    s = sotto(n, seed=n)
    msgs, end = receive(tmp_path, "m17", m17_audio(m17.tx_sotto(s), np.random.default_rng(n)))
    assert len(msgs) == 1, msgs
    m = msgs[0]
    pad = s[-6:] * (n % 2)                       # an odd count: the last superframe fills the frame
    assert m["data"] == s + pad and m["missing"] == 0 and m["bad"] == 0 and m["ended"] == 1


def test_m17_real_time_stream_places_the_repeats(tmp_path):
    s = sotto(30)
    msgs, _ = receive(tmp_path, "m17", m17_audio(m17.tx_sotto(s, repeat=True), np.random.default_rng(2)))
    assert len(msgs) == 1 and msgs[0]["data"] == s and msgs[0]["missing"] == 0


@pytest.mark.parametrize("n", [1, 23, 60])
def test_ysf_sotto_vw_comes_back_whole(tmp_path, n):
    s = sotto(n, seed=40 + n)
    msgs, _ = receive(tmp_path, "ysf", ysf_audio(ysf.tx_sotto(s), np.random.default_rng(n)))
    assert len(msgs) == 1, msgs
    m = msgs[0]
    pad = s[-6:] * ((-n) % 5)                    # the last frame filled with the last superframe
    assert m["data"] == s + pad and m["missing"] == 0 and m["ended"] == 1


def test_a_lost_ysf_frame_keeps_its_place(tmp_path):
    """a frame wiped out on air: the frames after it still land where they belong, and its five
    superframes are filled from the one before"""
    s = sotto(30)
    tx = bytearray(ysf.tx_sotto(s))
    lost = 24 + 120 * 3                          # the preamble, the header, then comms frame 2
    tx[lost:lost + 120] = bytes(np.random.default_rng(5).integers(0, 256, 120, dtype=np.uint8))
    msgs, _ = receive(tmp_path, "ysf", ysf_audio(bytes(tx), np.random.default_rng(6)))
    assert len(msgs) == 1
    m = msgs[0]
    assert m["n"] == 30 and m["got"] == 25 and m["missing"] == 5
    got = m["data"]
    assert got[:60] == s[:60] and got[90:] == s[90:]
    assert got[60:90] == s[54:60] * 5            # held over the gap


def test_a_lost_m17_frame_is_filled_from_the_one_before(tmp_path):
    s = sotto(40)
    tx = bytearray(m17.tx_sotto(s))
    # preamble 48 bytes, LSF 48, then 48-byte stream frames: wipe stream frame 5 (superframes 10, 11)
    at = 48 + 48 + 48 * 5
    tx[at + 2:at + 48] = bytes(np.random.default_rng(9).integers(0, 256, 46, dtype=np.uint8))
    msgs, _ = receive(tmp_path, "m17", m17_audio(bytes(tx), np.random.default_rng(10)))
    assert len(msgs) == 1
    m = msgs[0]
    assert m["n"] == 40 and m["missing"] == 2
    assert m["data"][:60] == s[:60] and m["data"][72:] == s[72:] and m["data"][60:72] == s[54:60] * 2


def test_noise_makes_no_message(tmp_path):
    rng = np.random.default_rng(3)
    for air in ("m17", "ysf"):
        msgs, _ = receive(tmp_path, air, np.clip(rng.normal(0, 12000, 8000 * 30), -32768, 32767).astype("<i2"))
        assert msgs == [], air


@pytest.mark.parametrize("air", ["m17", "ysf"])
def test_back_to_back_messages_each_come_back_whole(tmp_path, air):
    """three sends with half a second between: the EOT (terminator) that ends one message, or the
    last-frame flag before it, doesn't carry over and cut the next one short after its first frame"""
    s = sotto(20, seed=11)
    tx = m17.tx_sotto(s) if air == "m17" else ysf.tx_sotto(s)
    one = m17_audio(tx, np.random.default_rng(12)) if air == "m17" else ysf_audio(tx, np.random.default_rng(12))
    msgs, _ = receive(tmp_path, air, np.concatenate([one, one, one]))
    assert len(msgs) == 3, [(m["n"], m["got"]) for m in msgs]
    assert all(m["data"][:120] == s and m["missing"] == 0 and m["ended"] == 1 for m in msgs)


def test_m17rx_wav_sends_the_sotto_test_message(tmp_path):
    """modem.m17rx wav --sotto N --repeat R: the Pluto test signal c6sottorx_m17 hears"""
    from scipy.io import wavfile
    from modem import m17rx
    out = tmp_path / "s.wav"
    assert m17rx.main(["wav", str(out), "--tones-s", "0", "--bert-s", "0", "--sotto", "24", "--repeat", "2"]) == 0
    fs, x = wavfile.read(out)
    msgs, _ = receive(tmp_path, "m17", _tap(x / 32767.0 * 16000.0, np.random.default_rng(13)))
    assert len(msgs) == 2 and all(m["data"] == m17rx.sotto_test(24) for m in msgs)


MOD = os.path.join(os.path.dirname(__file__), "..", "firmware", "uv380-openrtx", "mod")


@pytest.mark.parametrize("name", ["c6sottorx_m17.c", "c6sottorx_ysf.c"])
def test_sottorx_modules_fit_the_arena_with_their_state(name, tmp_path):
    """the RAM module's image (64-byte header, 8-byte aligned) plus its scratch, c6rx_state
    (C6RX_MAX 300 superframes and one receiver), within the 16 KB module arena: checked by the
    compiler on sizeof(c6rx_state) against the image just built"""
    from modem import uv380mod
    code, entry = uv380mod.build(os.path.join(MOD, name))
    assert entry == 0
    probe = tmp_path / "probe.c"
    probe.write_text(f'#include "{os.path.abspath(os.path.join(MOD, name))}"\n'
                     f"_Static_assert(64 + {(len(code) + 7) // 8 * 8} + sizeof(c6rx_state) <= 16384, "
                     '"c6rx_state does not fit");\n')
    r = subprocess.run(["clang", *uv380mod.CFLAGS, "-I" + MOD, "-fsyntax-only", str(probe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
