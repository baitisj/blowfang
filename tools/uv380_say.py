#!/usr/bin/env python3
"""Text to a PHY R send for the UV-380 player: speech (tools/tts_say.py, Kokoro by default),
8 kHz at peak 0.6, the radio's own sotto 1200 encoder (sotto submodule, VARIANT=v3.7), XL pager
packets (depth 8, short last group) under one msg id, PHY R tones with 300 Hz pre-emphasis at
peak 0.3, then the AG7EW CW ident: the wav hamlet's echo_data.build makes, with better speech.
Play it with tools/k6_send.sh (or hamlet's send_once.sh, IC-7100 at power 0, 440.850).

    make -C sotto/c VARIANT=v3.7 fixed PYTHON=false
    ~/blowfang-work/tts/bin/python tools/uv380_say.py "This is a test. A G 7 E W." OUT.wav \
        [--msg-id 0x5d] [--engine kokoro --voice af_heart] [--speed 1.0] [--keep-8k SPEECH.wav]

The radio ignores a msg id it played in the last 60 s, so each new test wants a new id
(default: from the frames' CRC). A message is cut to 375 frames (15 s of speech).
SPDX-License-Identifier: MIT
"""
import argparse
import os
import subprocess
import sys
import tempfile
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import tts_say  # noqa: E402
from modem import fsk, k6, k6r, pager  # noqa: E402

ENC = os.path.join(ROOT, "sotto", "c", "fixed", "sotto_enc_fx")
FS = 48000
LEVEL = 0.3
SPEECH_PEAK = 0.6
MAX_FRAMES = 375
CALL = "AG7EW"


def speech_frames(text, engine, voice, speed, models=None, keep=None):
    """text -> sotto 1200 frames (6 bytes each) from the 8 kHz speech"""
    y, fs = tts_say.synth(text, engine, voice, speed, models)
    y = tts_say.resample(y, fs, 8000)
    pcm = (y / max(1e-9, float(np.max(np.abs(y)))) * SPEECH_PEAK * 32767).astype("<i2")
    if keep:
        tts_say.write_wav(keep, pcm, 8000)
    with tempfile.TemporaryDirectory() as d:
        raw, bit = os.path.join(d, "p.raw"), os.path.join(d, "p.bit")
        pcm.tofile(raw)
        subprocess.run([ENC, "-1200", raw, bit], check=True, capture_output=True)
        return open(bit, "rb").read()


def send_wav(frames, msg_id):
    """frames -> (48 kHz float audio: PHY R burst + CW ident, packets)"""
    frames = frames[:6 * MAX_FRAMES]
    frames = frames[:len(frames) // 6 * 6]
    payloads = pager.profile("XL", "1200", depth=8, short_last=True).pack(frames, msg_id=msg_id)
    x = k6r.audio(k6r.burst_bits(payloads), FS, LEVEL)
    x = fsk.Channel(pre_emph=300, emph_top=3000, hpf=None, lpf=None).apply(x, FS)
    x = LEVEL * x / np.max(np.abs(x))
    y = np.concatenate([np.zeros(int(0.15 * FS)), x, np.zeros(int(0.4 * FS)),
                        k6.cw(CALL, FS, wpm=20, tone=800.0, level=LEVEL)])
    return y, len(payloads)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("text")
    ap.add_argument("out")
    ap.add_argument("--msg-id", type=lambda s: int(s, 0))
    ap.add_argument("--engine", choices=tts_say.ENGINES, default=tts_say.DEFAULT_ENGINE)
    ap.add_argument("--voice")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--models")
    ap.add_argument("--keep-8k", help="also write the 8 kHz speech the encoder got")
    a = ap.parse_args(argv)
    frames = speech_frames(a.text, a.engine, a.voice, a.speed, a.models, a.keep_8k)
    mid = a.msg_id if a.msg_id is not None else zlib.crc32(frames) & 0xFF
    y, npk = send_wav(frames, mid)
    tts_say.write_wav(a.out, np.round(y * 32767).astype(np.int16), FS)
    nf = min(len(frames) // 6, MAX_FRAMES)
    print(f"{a.out}: {nf} frames ({nf * 0.04:.1f} s speech), {npk} packets, msg id 0x{mid:02x}, "
          f"{len(y) / FS:.1f} s on air")


if __name__ == "__main__":
    sys.exit(main())
