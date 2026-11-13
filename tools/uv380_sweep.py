"""The UV-380's receive passband with no hardware mod: the IC-7100 sends stepped tones on FM, the
UV-380 records what its MCU hears (firmware/uv380-openrtx/mod/rxrec.c), and this says the level of
each tone against 1000 Hz, its purity, the corners and the noise between tones.

  python3 tools/uv380_sweep.py wav OUT.wav          the IC-7100's audio (48 kHz), about 57 s
  python3 tools/uv380_sweep.py report REC.log|REC.raw

The wav: PSK31 "AG7EW TEST" (the ident, 4 s), a 1500 Hz sync tone (1 s), then 39 tones 100 to
3900 Hz, 0.5 s each, at the wav's level (pass A) and again 10 dB down (pass B, to see whether
pass A compresses), a log chirp 100-3900 Hz (8 s), and PSK31 again. Each tone gets 10 ms ramps.
What the report gives is the IC-7100's FM transmit response (FM-D from its USB audio) times the
UV-380's receive response: the sum of both radios, as a sender like it would be heard.

report reads rxrec's log (or 16-bit 8 kHz raw audio), finds the sync tone and measures each
tone over the middle 0.3 s of its slot. MIT licence."""
import argparse
import os
import sys

import numpy as np

FS_TX = 48000
FS_RX = 8000
STEPS = list(range(100, 4000, 100))
STEP_S = 0.5
GAP_S = 0.3
SYNC_HZ = 1500.0
SYNC_S = 1.0
LEVEL = 0.3
PASS_B_DB = -10.0
CHIRP_S = 8.0


def tone(f, s, fs, level, ramp=0.01):
    t = np.arange(int(s * fs)) / fs
    x = level * np.sin(2 * np.pi * f * t)
    r = int(ramp * fs)
    w = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    x[:r] *= w
    x[-r:] *= w[::-1]
    return x


def layout():
    """-> [(start s after the sync tone's start, f, pass)] for the stepped tones"""
    out, t = [], SYNC_S + GAP_S
    for p in ("A", "B"):
        for f in STEPS:
            out.append((t, f, p))
            t += STEP_S
        t += GAP_S
    return out


def ident(fs=FS_TX, level=LEVEL):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from modem import psk31
    return psk31.modulate(psk31.repeat_to("AG7EW TEST ", 4.0), fs=fs, tone=1000.0, level=level,
                          lead_s=0.2, tail_s=0.2)


def sync_offset(fs=FS_TX):
    """seconds from the wav's start to the sync tone's"""
    return len(ident(fs)) / fs + GAP_S


def make_wav(fs=FS_TX, level=LEVEL):
    ident_x = ident(fs, level)
    gap = np.zeros(int(GAP_S * fs))
    parts = [ident_x, gap, tone(SYNC_HZ, SYNC_S, fs, level), gap]
    for p, lv in (("A", level), ("B", level * 10 ** (PASS_B_DB / 20))):
        parts += [tone(f, STEP_S, fs, lv) for f in STEPS] + [gap]
    t = np.arange(int(CHIRP_S * fs)) / fs
    k = np.log(3900 / 100) / CHIRP_S
    parts += [level * np.sin(2 * np.pi * 100 * (np.exp(k * t) - 1) / k), gap, ident_x]
    return np.concatenate(parts)


def power_at(x, f, fs=FS_RX):
    """mean power of x at f (a Goertzel over all of x, Hann-windowed), as a sine's amplitude^2/2"""
    n = len(x)
    w = np.hanning(n)
    c = np.sum(x * w * np.exp(-2j * np.pi * f * np.arange(n) / fs))
    a = 2 * abs(c) / np.sum(w)
    return a * a / 2


def find_sync(x, fs=FS_RX):
    """the sample where the sync tone starts (20 ms frames where 1500 Hz holds most of the power,
    the first run of 0.8 s), or None"""
    n = int(0.02 * fs)
    run = 0
    for i in range(0, len(x) - n, n):
        seg = x[i:i + n]
        tot = np.mean(seg ** 2)
        hit = tot > 0 and power_at(seg, SYNC_HZ, fs) > 0.6 * tot
        run = run + 1 if hit else 0
        if run == 40:
            start = i - 39 * n
            # refine to 1 ms: back while the tone holds
            m = int(0.001 * fs)
            while start - m >= 0 and power_at(x[start - m:start - m + n], SYNC_HZ, fs) > 0.6 * np.mean(
                    x[start - m:start - m + n] ** 2):
                start -= m
            return start
    return None


def measure(x, fs=FS_RX):
    """-> dict: sync (sample), rows [(f, pass, dB re 1000 Hz of its pass, purity 0-1, peak)],
    noise rms in the gap after pass A, clipped fraction"""
    x = np.asarray(x, float)
    s = find_sync(x, fs)
    if s is None:
        return None
    rows, ref = [], {}
    for t0, f, p in layout():
        a = s + int((t0 + 0.1) * fs)
        seg = x[a:a + int(0.3 * fs)]
        if len(seg) < int(0.3 * fs):
            break
        pf, tot = power_at(seg, f, fs), np.mean(seg ** 2)
        rows.append([f, p, pf, pf / tot if tot else 0.0, float(np.max(np.abs(seg)))])
        if f == 1000:
            ref[p] = pf
    for r in rows:
        r[2] = 10 * np.log10(r[2] / ref[r[1]]) if ref.get(r[1]) and r[2] > 0 else -99.0
    gap_a = s + int((SYNC_S + GAP_S + len(STEPS) * STEP_S + 0.05) * fs)
    noise = float(np.sqrt(np.mean(x[gap_a:gap_a + int(0.2 * fs)] ** 2)))
    end = s + int((SYNC_S + GAP_S + 2 * (len(STEPS) * STEP_S + GAP_S)) * fs)
    span = x[s:end]
    return {"sync": s, "rows": [tuple(r) for r in rows], "noise": noise,
            "clipped": float(np.mean(np.abs(span) >= 32000)) if len(span) else 0.0,
            "ref_amp": {p: float(np.sqrt(2 * v)) for p, v in ref.items()}}


def corners(rows, p="A", drop=3.0):
    """the lowest and highest frequency of the run around 1000 Hz within `drop` dB of 1000 Hz"""
    d = {f: db for f, q, db, _, _ in rows if q == p}
    lo = hi = 1000
    while lo - 100 in d and d[lo - 100] >= -drop:
        lo -= 100
    while hi + 100 in d and d[hi + 100] >= -drop:
        hi += 100
    return lo, hi


def report(m):
    if m is None:
        return ["no sync tone found"]
    out = [f"sync at {m['sync'] / FS_RX:.2f} s; 1000 Hz amplitude A {m['ref_amp'].get('A', 0):.0f}, "
           f"B {m['ref_amp'].get('B', 0):.0f}; noise between tones rms {m['noise']:.0f}; "
           f"clipped {100 * m['clipped']:.3f}%"]
    for drop in (1.0, 3.0, 6.0):
        lo, hi = corners(m["rows"], "A", drop)
        out.append(f"within {drop:.0f} dB of 1000 Hz (pass A): {lo}-{hi} Hz")
    out.append("   Hz   A dB  B dB  purity A  peak A")
    b = {f: db for f, q, db, _, _ in m["rows"] if q == "B"}
    for f, q, db, pur, pk in m["rows"]:
        if q == "A":
            out.append(f"{f:5d} {db:6.1f} {b.get(f, float('nan')):5.1f}  {pur:8.3f}  {pk:6.0f}")
    return out


def load(path):
    if path.endswith(".raw"):
        return np.fromfile(path, "<i2").astype(float)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import uv380_i2sprobe
    with open(path, errors="replace") as f:
        x, _, _, _ = uv380_i2sprobe.audio_lines(f)
    return np.array(x, float)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("wav")
    q.add_argument("out")
    q.add_argument("--level", type=float, default=LEVEL)
    q = sub.add_parser("report")
    q.add_argument("rec")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from modem import k6r
        y = make_wav(level=a.level)
        k6r.write_wav(a.out, y, FS_TX)
        print(f"{a.out}: {len(y) / FS_TX:.1f} s")
    else:
        print("\n".join(report(measure(load(a.rec)))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
