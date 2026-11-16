"""tools/uv380_say.py, text to a PHY R send for the UV-380 player: the wav must decode on the
portable receiver chain the radio runs (8 kHz -> range_rx -> pager_rx) to exactly the frames the
sotto 1200 encoder wrote, under the msg id asked for, with the CW ident after the burst; and
tools/tts_say.py's old espeak call form must still work (hamlet's drive echo uses it).
Uses espeak-ng (espeakng-loader), so no model files. Needs make -C c/common and
make -C sotto/c VARIANT=v3.7 fixed. SPDX-License-Identifier: MIT"""
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, "c", "common")
RX = os.path.join(C, "pager_rx_host")
RRX = os.path.join(C, "range_rx_host")
sys.path.insert(0, os.path.join(ROOT, "tools"))
pytest.importorskip("espeakng_loader")
signal = pytest.importorskip("scipy.signal")
import uv380_say  # noqa: E402

pytestmark = pytest.mark.skipif(not (os.path.exists(RX) and os.path.exists(RRX)
                                     and os.path.exists(uv380_say.ENC)),
                                reason="needs make -C c/common and make -C sotto/c fixed")


def read_wav(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float), w.getframerate()


def test_send_decodes_to_the_frames():
    with tempfile.TemporaryDirectory() as d:
        sp, out = os.path.join(d, "s.wav"), os.path.join(d, "o.wav")
        frames = uv380_say.speech_frames("Received range. A G 7 E W.", "espeak", None, 1.0, keep=sp)
        s, fs_s = read_wav(sp)
        assert fs_s == 8000 and abs(np.max(np.abs(s)) / 32767 - uv380_say.SPEECH_PEAK) < 0.01
        y, npk = uv380_say.send_wav(frames, 0x6e)
        uv380_say.tts_say.write_wav(out, np.round(y * 32767).astype(np.int16), 48000)
        a, fs = read_wav(out)
        assert fs == 48000 and np.max(np.abs(a)) / 32767 <= uv380_say.LEVEL + 0.01
        # the ident: the last 2 s carry an 800 Hz tone
        tail = a[-2 * fs:]
        f = np.fft.rfftfreq(len(tail), 1 / fs)
        assert abs(f[np.argmax(np.abs(np.fft.rfft(tail)))] - 800) < 5
        x = signal.resample_poly(a, 1, 6)
        np.clip(np.round(x), -32768, 32767).astype("<i2").tofile(os.path.join(d, "x.raw"))
        heard = subprocess.run([RRX, os.path.join(d, "x.raw"), "8000"], check=True,
                               capture_output=True, text=True).stdout
        pk = [bytes.fromhex(ln.split()[-1]) for ln in heard.splitlines() if " ok=1 " in ln]
        open(os.path.join(d, "p.bin"), "wb").write(b"".join(pk))
        r = subprocess.run([RX, os.path.join(d, "p.bin"), "128", os.path.join(d, "m.sot")],
                           check=True, capture_output=True, text=True).stdout.splitlines()
        got = open(os.path.join(d, "m.sot"), "rb").read()
    summ = dict(v.split("=") for v in r[-1].split()[1:])
    assert int(summ["missing"]) == 0 and int(summ["bad"]) == 0
    assert got == frames and len(pk) == npk


def test_main_writes_a_send():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "o.wav")
        uv380_say.main(["Testing.", out, "--engine", "espeak", "--msg-id", "0x6f"])
        a, fs = read_wav(out)
    assert fs == 48000 and len(a) > 5 * fs


def test_tts_say_old_form():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "t.wav")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "tts_say.py"), "Received F S K.",
                        out, "140"], check=True, capture_output=True)
        a, fs = read_wav(out)
    assert fs == 22050 and 0.5 < len(a) / fs < 3
