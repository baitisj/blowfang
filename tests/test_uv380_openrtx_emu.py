"""The UV-380 OpenRTX firmware's PAGER mode, run in OpenRTX's own Linux emulator: the patched
OpenRTX (firmware/uv380-openrtx) starts in PAGER mode on 440.850 MHz (BLOWFANG_PAGER set), the
emulator shell presses PTT, and the samples the mode makes (48 kHz, signed 16-bit, written to
BLOWFANG_PAGER_OUT on Linux instead of the modulator) must decode with modem.fsk's F receiver
(the bench model of the K6's chip receiver) back into blowfang_message.h, then end with the
AG7EW Morse ident. This runs the same OpMode_Pager code the radio image has, through OpenRTX's
real state, threads, UI and rtx layers; only the audio sink differs.

Needs sh firmware/uv380-openrtx/build.sh linux (out/openrtx_linux).
SPDX-License-Identifier: MIT"""
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware", "uv380-openrtx")
EMU = os.path.join(FW, "out", "openrtx_linux")
sys.path.insert(0, ROOT)
from modem import fsk, k6, pager  # noqa: E402

pytestmark = pytest.mark.skipif(not os.path.exists(EMU),
                                reason="needs sh firmware/uv380-openrtx/build.sh linux")

FS = 48000


def message_frames():
    with open(os.path.join(FW, "blowfang_message.h")) as f:
        src = f.read()
    n = int(re.search(r"BLOWFANG_MESSAGE_FRAMES (\d+)u", src).group(1))
    body = src[src.index("blowfang_message["):]
    body = body[body.index("{") + 1:body.index("}")]
    data = bytes(int(h, 16) for h in re.findall(r"0x([0-9A-Fa-f]{2})", body))
    assert len(data) == 6 * n
    return data


def run_emulator(d, script):
    out = os.path.join(d, "pager.raw")
    env = dict(os.environ)
    env.update({
        "BLOWFANG_PAGER": "1",
        "BLOWFANG_PAGER_OUT": out,
        "XDG_STATE_HOME": d,
        "HOME": d,
        "SDL_AUDIODRIVER": "dummy",
    })
    env.setdefault("SDL_VIDEODRIVER", "dummy")
    r = subprocess.run([EMU], input=script, cwd=d, env=env, capture_output=True, text=True,
                       timeout=120)
    return r, out


@pytest.fixture(scope="module")
def sent():
    with tempfile.TemporaryDirectory() as d:
        # PTT down then up again: one press sends the whole message
        r, out = run_emulator(d, "sleep 3000\nptt\nsleep 500\nptt\nsleep 1000\nquit\n")
        log = r.stdout + r.stderr
        assert "blowfang: pager message sent" in log, log[-3000:]
        with open(out, "rb") as f:
            x = np.frombuffer(f.read(), "<i2").astype(float) / 32768
        yield x, log


def test_k6_receiver_decodes_openrtx_pager(sent):
    x, _ = sent
    frames = message_frames()
    n = len(frames) // 6
    prof = pager.profile("XL", "1200")
    asm, times = fsk.receive(x, FS, "F", 1800, prof)
    assert sum(ok for _, ok, _, _ in times) == prof.air_packets(n) and asm.bad == 0
    (mid,) = asm.messages
    got, good = asm.frames(mid)
    assert mid == k6.msg_id_for(frames) and got == frames and good.all()


def test_burst_then_ident(sent):
    x, _ = sent
    frames = message_frames()
    prof = pager.profile("XL", "1200")
    burst = pager.Air(preamble_bytes=16, later_preamble_bytes=12).burst(
        prof.pack(frames, k6.msg_id_for(frames)), 1800)
    n_ffsk = int(round(len(burst) * 8 * FS / 1800)) + 240
    ident = k6.cw("AG7EW", FS)
    # the file is whole 20 ms blocks: FFSK, ident, then silence to the end of the last block
    assert abs(len(x) - n_ffsk - len(ident)) <= 960 + 250
    tail = np.abs(x[len(x) - len(ident) - 960 - 480:])
    on = np.convolve(tail, np.ones(96) / 96, "same") > 0.1
    ref = np.convolve(np.abs(ident), np.ones(96) / 96, "same") > 0.1
    best = max(np.mean(on[k:k + len(ref)] == ref) for k in range(0, len(on) - len(ref) + 1, 8))
    assert best > 0.99


def test_rtx_keyed_and_released(sent):
    _, log = sent
    assert "enableTx() called" in log
    assert log.rfind("disableRtx() called") > log.find("enableTx() called")
