"""Read micalias's dump (c/k6/mod/micalias.c, docs/k6-mic-probe.md): does micsotto's burst
sampling fold 7 kHz into the voice band?

  python3 tools/k6_micalias.py K6.LOG [--fold-in HZ]

micalias records the mic three ways for each of three sounds (1: a 1000 Hz tone, 2: a 7000 Hz
tone, 3: quiet):
  A  8 kHz, 48 turns at the start of each period (micsotto's sampling)
  B  32 kHz, 12 turns a period, raw: what reaches PA11 up to 16 kHz
  C  8 kHz, turns filling each period (mickrec's sampling)
Levels are in the converter's full scale (w * w counts). The fold: a tone at f7 lands at
|8000 - f7| at 8 kHz. Each way's rejection of it is its fold line over its 1 kHz line, against
the same ratio in B at the true frequencies (B sees both tones as they are at the pin, so the
phone's own response cancels). B is also decimated here to 8 kHz two ways, the fix's candidates:
B4 (4 samples summed) and B7 (a 7-tap triangle, 4 summed twice).
SPDX-License-Identifier: MIT"""
import argparse
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k6_micprobe as mp  # noqa: E402


def segments(lines):
    """micalias's segments -> {"1A": (samples in full-scale units, info)}; the last run in the log"""
    out, cur, data = {}, None, None
    for ln in lines:
        if ln.startswith("micalias start"):
            out, cur = {}, None
        m = re.fullmatch(r"ma (\d[ABC]) (.*)", ln)
        if m:
            cur = m.group(1)
            info = {k: int(v, 16) if k in ("rest", "over") else int(v)
                    for k, v in (kv.split("=") for kv in m.group(2).split())}
            data = bytearray(info["n"] // 2)
            out[cur] = [data, info]
            continue
        m = re.fullmatch(r"p ([0-9a-f]{4}) ((?:[0-9a-f]{2})+)", ln)
        if m and cur:
            at = int(m.group(1), 16)
            b = bytes.fromhex(m.group(2))
            data[at:at + len(b)] = b
    res = {}
    for k, (data, info) in out.items():
        x = np.array(mp.ima_decode(data), float) / info["g"] / info["w"] ** 2
        res[k] = (x - x.mean(), info)
    return res


def decimate(x, taps):
    y = np.convolve(x, taps / taps.sum(), mode="valid")
    return y[::4]


def line(x, fs, f, span=60.0):
    """the strongest tone within span Hz of f: (Hz, amplitude)"""
    lo, hi = max(f - span, 20.0), min(f + span, fs / 2 - 20.0)
    f0, amp, _ = mp.peak_tone(x, fs, lo, hi)
    return f0, amp


def db(v):
    return 20 * np.log10(max(v, 1e-12))


def report(seg, fold_in=None):
    out = []
    ways = {}
    for t in "123":
        if t + "B" not in seg:
            continue
        b, bi = seg[t + "B"]
        ways[t] = {"A": (seg[t + "A"][0], 8000.0), "C": (seg[t + "C"][0], 8000.0),
                   "B4": (decimate(b, np.ones(4)), 8000.0),
                   "B7": (decimate(b, np.array([1, 2, 3, 4, 3, 2, 1], float)), 8000.0), "B": (b, 32000.0)}
    for k, (x, info) in sorted(seg.items()):
        out.append(f"{k}: {info['hz']} Hz w={info['w']} n={info['n']} ms={info['ms']} (expect "
                   f"{1000 * info['n'] // info['hz']}) over={info['over']} rms {db(x.std()):6.1f} dBFS")
    if "1" in ways and "2" in ways:
        b1 = line(ways["1"]["B"][0], 32000.0, 1000.0, 200.0)
        f7, a7 = mp.peak_tone(ways["2"]["B"][0], 32000.0, 5000.0, 10000.0)[:2] if fold_in is None else \
            line(ways["2"]["B"][0], 32000.0, fold_in, 200.0)
        fold = abs(8000.0 - f7)
        out.append(f"at the pin (B): 1 kHz tone {db(b1[1]):6.1f} dBFS at {b1[0]:.0f} Hz; high tone "
                   f"{db(a7):6.1f} dBFS at {f7:.0f} Hz (folds to {fold:.0f} Hz)")
        ref = a7 / b1[1]
        for w in ("A", "C", "B4", "B7"):
            x1, fs = ways["1"][w]
            x2, _ = ways["2"][w]
            r1 = line(x1, fs, 1000.0, 200.0)[1]
            r2 = line(x2, fs, fold, 80.0)[1]
            rej = db(r2 / r1) - db(ref)
            out.append(f"  {w:3s}: 1 kHz {db(r1):6.1f} dBFS, fold at {fold:.0f} Hz {db(r2):6.1f} dBFS: "
                       f"rejects the high tone by {-rej:5.1f} dB")
    if "3" in ways:
        b = ways["3"]["B"][0]
        out.append(f"quiet: at the pin 300-3400 Hz {db(mp.band_rms(b, 32000.0, 300, 3400)):6.1f} dBFS, "
                   f"4-16 kHz {db(mp.band_rms(b, 32000.0, 4000, 15900)):6.1f} dBFS")
        for w in ("A", "C", "B4", "B7"):
            x, fs = ways["3"][w]
            out.append(f"  {w:3s}: 300-3400 Hz {db(mp.band_rms(x, fs, 300, 3400)):6.1f} dBFS")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log")
    ap.add_argument("--fold-in", type=float, help="the high tone's Hz, if not the strongest 5-10 kHz")
    a = ap.parse_args()
    seg = segments(mp.say_lines(open(a.log, errors="replace").read()))
    if not seg:
        sys.exit("no micalias segments in the log")
    print("\n".join(report(seg, a.fold_in)))


if __name__ == "__main__":
    main()
