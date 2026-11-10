"""The portable pager transmitter (c/common/pager_tx.c, ffsk_tx.c) that the UV-380 uses to send
sotto messages the K6 can receive: its packets must equal modem.pager's byte for byte, its burst
must equal modem.pager.Air's, and its FFSK audio must decode with modem.fsk's F/1800 receiver
(the bench model of the K6's chip receiver) back into the same message.
Needs make -C c/common. SPDX-License-Identifier: MIT"""
import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "c", "common", "pager_tx_host")
sys.path.insert(0, ROOT)
from modem import fsk, k6, pager  # noqa: E402

pytestmark = pytest.mark.skipif(not os.path.exists(EXE), reason="needs make -C c/common")


def frames_of(n, seed=1):
    rng = np.random.default_rng(seed)
    return bytes(rng.integers(0, 256, 6 * n, dtype=np.uint8))


def run(mode, frames, *opts):
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "m.sot"), os.path.join(d, "out")
        with open(src, "wb") as f:
            f.write(frames)
        subprocess.run([EXE, mode, src, out, *map(str, opts)], check=True)
        with open(out, "rb") as f:
            return f.read()


CASES = [
    # (profile name, codec, depth, parity, short_last, frames)
    ("XL", "1200", 8, False, True, 1),
    ("XL", "1200", 8, False, True, 160),
    ("XL", "1200", 8, False, True, 170),
    ("XL", "1200", 8, True, True, 333),
    ("XL", "1200", 4, True, True, 95),
    ("XL", "2400", 8, False, False, 170),
    ("L", "2400", 8, False, True, 70),
    ("L", "1200", 2, True, True, 41),
    ("L", "600", 1, True, True, 9),
    ("L", "400", 8, False, False, 64),
]


@pytest.mark.parametrize("name,codec,depth,parity,short,n", CASES)
def test_packets_match_python(name, codec, depth, parity, short, n):
    frames = frames_of(n, seed=n)
    prof = pager.profile(name, codec, depth=depth, parity=parity)
    if not short:
        from dataclasses import replace
        prof = replace(prof, short_last=False)
    want = b"".join(prof.pack(frames, k6.msg_id_for(frames)))
    opts = ["--profile", name, "--magic", pager.CODECS[codec][0], "--depth", depth]
    opts += ["--parity"] if parity else []
    opts += [] if short else ["--plain"]
    assert run("packets", frames, *opts) == want


@pytest.mark.parametrize("name,baud", [("XL", 1800), ("XL", 1200), ("L", 1800), ("L", 2400)])
def test_burst_matches_air(name, baud):
    frames = frames_of(90)
    prof = pager.profile(name, "1200")
    pk = prof.pack(frames, k6.msg_id_for(frames))
    later = (12 if baud >= 1800 else 24) if name == "XL" else 16
    want = pager.Air(preamble_bytes=16, later_preamble_bytes=later).burst(pk, baud)
    got = run("bits", frames, "--profile", name, "--magic", pager.MAGIC_1200, "--baud", baud)
    assert got == want


@pytest.mark.parametrize("fs,baud", [(48000, 1800), (28800, 1800), (8000, 1800), (48000, 1200)])
def test_ffsk_decodes(fs, baud):
    """The burst as audio, through modem.fsk's F receiver: every packet with a good CRC, and the
    assembled message equal to the frames sent."""
    frames = frames_of(250, seed=7)
    raw = run("audio", frames, "--profile", "XL", "--magic", pager.MAGIC_1200, "--baud", baud,
              "--fs", fs, "--level", 16000)
    x = np.frombuffer(raw, "<i2").astype(float) / 32768
    bits = pager.Air(preamble_bytes=16, later_preamble_bytes=12 if baud >= 1800 else 24).burst_bits(
        pager.profile("XL", "1200").pack(frames, k6.msg_id_for(frames)), baud)
    assert abs(len(x) - len(bits) * fs / baud - fs / 200) <= 2      # the burst plus the 5 ms ramp
    prof = pager.profile("XL", "1200")
    asm, times = fsk.receive(x, fs, "F", baud, prof)
    n = prof.air_packets(250)
    assert sum(ok for _, ok, _, _ in times) == n and asm.bad == 0
    (mid,) = asm.messages
    got, good = asm.frames(mid)
    assert mid == k6.msg_id_for(frames) and got == frames and good.all()


def test_ffsk_level_and_ramp():
    """Peak at --level; the 5 ms raised-cosine ramps start and end near zero. (Tone polarity, bit 1
    = 1200 Hz, is what test_ffsk_decodes checks: modem.fsk's F receiver assumes it.)"""
    frames = frames_of(20)
    raw = run("audio", frames, "--fs", 48000, "--level", 20000)
    x = np.frombuffer(raw, "<i2").astype(float)
    assert 19900 < np.max(np.abs(x)) <= 20000
    assert np.max(np.abs(x[:24])) < 1000 and np.max(np.abs(x[-24:])) < 1000
    assert np.max(np.abs(x[240:2400])) > 19000
