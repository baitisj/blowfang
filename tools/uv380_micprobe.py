#!/usr/bin/env python3
"""micprobe's log (firmware/uv380-openrtx/mod/micprobe.c) setting by setting: level, the share of
power in the voice band (300-3000 Hz), and, given a recording of what was played into the mic
(a wav at any rate, e.g. the IC-7100's USB audio taken at the same time), the best normalised
correlation of the setting's audio with any stretch of it (corr), and of its voice-band envelope
(env, which the room's echoes don't smear). Noise against a minute of reference peaks at about
0.05-0.09 on corr; sound picked up by the mic should stand well clear of that on both.

  python3 tools/uv380_micprobe.py LOG [--ref REF.wav] [--wav-dir DIR]

--wav-dir writes each setting's audio as DIR/setNN.wav (8 kHz) to listen to.
SPDX-License-Identifier: MIT"""
import argparse
import os
import re
import sys
import wave

import numpy as np

FS = 8000


def settings(lines):
    """[(k, label, samples, got line)] in log order"""
    out, cur = [], None
    for ln in lines:
        m = re.search(r"mod say (set (\d+) .*)$", ln)
        if m:
            cur = [int(m.group(2)), m.group(1).strip(), [], ""]
            out.append(cur)
            continue
        m = re.search(r"mod say (got \d+ .*)$", ln)
        if m and cur is not None:
            cur[3] = m.group(1).strip()
            continue
        w = ln.split()
        if cur is not None and len(w) > 2 and w[0] == "a" and w[1].isdigit():
            try:
                v = [int(h, 16) for h in w[2:]]
            except ValueError:
                continue
            cur[2] += [x - 65536 if x >= 32768 else x for x in v]
    return [(k, lab, np.array(s, dtype=float), got) for k, lab, s, got in out]


def read_wav(path):
    with wave.open(path, "rb") as w:
        n, ch, sw, fs = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"{path}: want 16-bit samples, got {8 * sw}-bit")
    x = np.frombuffer(raw, dtype="<i2").astype(float).reshape(-1, ch).mean(axis=1)
    if fs != FS:
        # band-limit to 3.4 kHz and resample by linear interpolation (enough for correlation)
        f = np.fft.rfftfreq(len(x), 1 / fs)
        X = np.fft.rfft(x)
        X[f > 3400] = 0
        x = np.fft.irfft(X, len(x))
        t = np.arange(0, len(x) / fs, 1 / FS)
        x = np.interp(t, np.arange(len(x)) / fs, x)
    return x


def bandpass(x, lo=300.0, hi=3000.0):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / FS)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def voice_share(x):
    if len(x) < 256:
        return 0.0
    P = np.abs(np.fft.rfft(x - x.mean())) ** 2
    f = np.fft.rfftfreq(len(x), 1 / FS)
    tot = P[f > 50].sum()
    return float(P[(f >= 300) & (f <= 3000)].sum() / tot) if tot > 0 else 0.0


def best_corr(seg, ref):
    """the largest |normalised correlation| of seg (band-passed) with any stretch of ref"""
    if len(seg) < 800 or len(ref) < len(seg):
        return 0.0, 0
    s = bandpass(seg - seg.mean())
    s /= np.linalg.norm(s) or 1.0
    n = 1 << int(np.ceil(np.log2(len(ref) + len(s))))
    c = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(s, n)), n)[:len(ref) - len(s) + 1]
    e = np.concatenate([[0.0], np.cumsum(ref * ref)])
    norm = np.sqrt(np.maximum(e[len(s):] - e[:-len(s)], 1e-9))
    r = np.abs(c) / norm
    i = int(np.argmax(r))
    return float(r[i]), i


def envelope(x, rate=100):
    """the voice band's envelope at `rate` Hz (rectified, 8 Hz low-pass), mean removed"""
    e = np.abs(bandpass(x, 300.0, 3400.0))
    E = np.fft.rfft(e)
    E[np.fft.rfftfreq(len(e), 1 / FS) > 8] = 0
    e = np.fft.irfft(E, len(e))[::FS // rate]
    return e - e.mean()


def env_corr(seg, ref_env):
    """the best correlation of seg's envelope with any stretch of the reference's: robust to the
    room's echoes, which smear the waveform but not the speech's rhythm"""
    s = envelope(seg)
    if len(s) < 50 or len(ref_env) < len(s):
        return 0.0
    s /= np.linalg.norm(s) or 1.0
    best = 0.0
    for i in range(len(ref_env) - len(s) + 1):
        r = ref_env[i:i + len(s)] - ref_env[i:i + len(s)].mean()
        n = np.linalg.norm(r)
        if n > 0:
            best = max(best, float(np.dot(r, s) / n))
    return best


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("--ref")
    ap.add_argument("--wav-dir")
    a = ap.parse_args(argv)
    lines = open(a.log, errors="replace").read().splitlines()
    sets = settings(lines)
    ref = ref_env = None
    if a.ref:
        raw = read_wav(a.ref)
        ref, ref_env = bandpass(raw), envelope(raw)
    for ln in lines:
        if any(w in ln for w in ("micprobe", "micadc", "rate n=", "STOP")):
            print(ln[ln.index("mod say") + 8:] if "mod say" in ln else ln)
    print(f"{'k':>3} {'rms':>7} {'voice':>6} {'corr':>6} {'env':>6}  setting / device line")
    for k, lab, x, got in sets:
        rms = float(np.sqrt(np.mean((x - x.mean()) ** 2))) if len(x) else 0.0
        c = (f"{best_corr(x, ref)[0]:6.3f} {env_corr(x, ref_env):6.3f}" if ref is not None
             else "     -      -")
        print(f"{k:3d} {rms:7.0f} {voice_share(x):6.2f} {c}  {lab[len(f'set {k} '):]}  [{got}]")
        if a.wav_dir and len(x):
            os.makedirs(a.wav_dir, exist_ok=True)
            with wave.open(os.path.join(a.wav_dir, f"set{k:02d}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(FS)
                w.writeframes(np.clip(x, -32768, 32767).astype("<i2").tobytes())
    return 0


if __name__ == "__main__":
    sys.exit(main())
