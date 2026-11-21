#!/usr/bin/env python3
"""A K6 mic recording (mickrec's dump in a serial log) through the slim sotto 1200 encoder the K6
runs (sotto's c/fixed/sotto_fx_slim.c, built for the PC: the same bits), then decoded: what the
K6 would send of it, to listen to before the encoder runs on the radio (docs/k6-mic-probe.md).

    python3 tools/k6_mic2sotto.py K6.LOG OUTPREFIX [--sotto DIR]

writes OUTPREFIX-input.wav (the encoder's input: the transmit pre-emphasis undone as
k6_micprobe's deemph, a 150 Hz high-pass, -20 dBFS rms) and OUTPREFIX-sotto1200.wav, 8 kHz.
--sotto: a sotto checkout (default: this repo's sotto submodule, else ~/sotto); its encoder and
decoder are built with make if missing. Standard library, numpy and scipy.
SPDX-License-Identifier: MIT"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv=None):
    from scipy import signal
    from scipy.io import wavfile
    import k6_micprobe as mp
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("prefix")
    ap.add_argument("--sotto", default=None)
    a = ap.parse_args(argv)
    sd = a.sotto or next((d for d in (os.path.join(os.path.dirname(HERE), "sotto"), os.path.expanduser("~/sotto"))
                          if os.path.exists(os.path.join(d, "c", "fixed", "sotto_fx_slim.c"))), None)
    if not sd:
        raise SystemExit("no sotto checkout (--sotto DIR)")
    fx = os.path.join(sd, "c", "fixed")
    for b in ("sotto_slim_enc", "sotto_dec_fx"):
        if not os.path.exists(os.path.join(fx, b)):
            subprocess.run(["make", "-C", os.path.join(sd, "c"), "fixed/" + b], check=True)
    runs = mp.rec_runs(mp.say_lines(open(a.log, errors="replace").read()))
    if not runs:
        raise SystemExit("no recording in the log")
    x, info, notes = runs[-1]
    fs = round(info["n"] / (info["ticks"] / 1e4))
    y = mp.deemph(x, fs)
    y = signal.resample_poly(y, 8000, fs)
    y = signal.sosfilt(signal.butter(4, 150, "high", fs=8000, output="sos"), y)
    y = np.clip(y / (np.sqrt(np.mean(y ** 2)) + 1e-9) * 3277.0, -32767, 32767).astype(np.int16)
    d = tempfile.mkdtemp()
    raw, bit, out = (os.path.join(d, n) for n in ("in.raw", "s.bit", "out.raw"))
    y.tofile(raw)
    subprocess.run([os.path.join(fx, "sotto_slim_enc"), raw, bit], check=True)
    subprocess.run([os.path.join(fx, "sotto_dec_fx"), "-1200", bit, out], check=True, stdout=subprocess.DEVNULL)
    wavfile.write(a.prefix + "-input.wav", 8000, y)
    wavfile.write(a.prefix + "-sotto1200.wav", 8000, np.fromfile(out, np.int16))
    nb = os.path.getsize(bit)
    print(f"{info['n']} samples at {fs} Hz ({len(y) / 8000:.2f} s), clipped {info.get('over', 0)}, gain "
          f"{info.get('g', 1)}: {nb} B of sotto 1200 -> {a.prefix}-input.wav, {a.prefix}-sotto1200.wav")


if __name__ == "__main__":
    main()
