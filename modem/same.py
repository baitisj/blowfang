"""NOAA SAME-format bursts for the K6's SAME probe (c/k6/mod/samecap.c, docs/k6-pocsag.md, "NOAA
SAME"), and a reader for its lines.

SAME (47 CFR 11.31): AFSK on the FM audio, mark 2083.3 Hz (a 1), space 1562.5 Hz (a 0), 520.83
baud, bytes with the low bit first; a header is sixteen bytes of AB and then its text, sent three
times about a second apart. The bench's bursts have the format but no EAS header (no "ZCZC", no
event code): their text is 'AG7EW SAME FORMAT TEST <n>', so nothing on the air looks like an alert.

  python3 -m modem.same wav OUT.wav [--rounds 33] [--level 0.35]   the bench signal (samecap's 160 s)
  python3 -m modem.same report LOG                                  what samecap's captures hold
SPDX-License-Identifier: MIT
"""
import argparse
import sys

import numpy as np

FS = 48000
BAUD = 520.0 + 5.0 / 6.0
MARK, SPACE = 2083.0 + 1.0 / 3.0, 1562.5


def burst_bits(text, pre=16):
    data = bytes([0xAB] * pre) + text.encode()
    return np.array([b >> i & 1 for b in data for i in range(8)], np.uint8)


def afsk(bits, fs=FS):
    n = int(np.ceil(len(bits) * fs / BAUD))
    idx = np.minimum((np.arange(n) * BAUD / fs).astype(int), len(bits) - 1)
    f = np.where(bits[idx] > 0, MARK, SPACE)
    return np.sin(2 * np.pi * np.cumsum(f) / fs)


def bench_wav(rounds=30, fs=FS, level=0.35, gap=1.0):
    parts = [np.zeros(int(0.4 * fs))]
    for r in range(1, rounds + 1):
        for k in range(3):
            parts += [afsk(burst_bits(f"AG7EW SAME FORMAT TEST {r}"), fs), np.zeros(int(gap * fs))]
    y = np.concatenate(parts)
    return (level * y).astype(np.float32)


def captures(lines):
    """{n: (header dict, [words])} from the module's c and d lines"""
    caps = {}
    for ln in lines:
        if "K6 MOD say " not in ln:
            continue
        f = ln.split("K6 MOD say ", 1)[1].split()
        if len(f) >= 2 and f[0] == "c" and f[1].isdigit():
            caps[int(f[1])] = (dict(kv.split("=", 1) for kv in f[2:] if "=" in kv), [])
        elif len(f) == 4 and f[0] == "d" and int(f[1]) in caps:
            caps[int(f[1])][1].extend(int(f[3][i:i + 4], 16) for i in range(0, len(f[3]), 4))
    return caps


def best_text(words):
    """the longest printable run over the 8 bit offsets and both senses: (text, offset, inverted)"""
    bits = [w >> (15 - i) & 1 for w in words for i in range(16)]
    best = ("", 0, 0)
    for inv in (0, 1):
        for off in range(8):
            out, run = [], ""
            for i in range(off, len(bits) - 7, 8):
                c = sum((bits[i + k] ^ inv) << k for k in range(8))
                if 32 <= c < 127:
                    run += chr(c)
                else:
                    out.append(run)
                    run = ""
            out.append(run)
            t = max(out, key=len)
            if len(t) > len(best[0]):
                best = (t, off, inv)
    return best


def report(lines):
    out = []
    for n, (h, words) in sorted(captures(lines).items()):
        t, off, inv = best_text(words)
        pre = sum(w in (0xD5D5, 0x2A2A) for w in words)
        out.append(f"c{n} s={h.get('s')} r=-{h.get('r', '?')}: preamble words {pre}, text "
                   f"{t!r} (offset {off}{', inverted' if inv else ''})")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("wav")
    q.add_argument("out")
    q.add_argument("--rounds", type=int, default=33)
    q.add_argument("--level", type=float, default=0.35)
    q = sub.add_parser("report")
    q.add_argument("log")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        from modem.pocsag import write_wav
        y = bench_wav(a.rounds, level=a.level)
        write_wav(a.out, y)
        print(f"{a.out}: {len(y) / FS:.1f} s, {3 * a.rounds} bursts")
    else:
        with open(a.log, errors="replace") as f:
            print("\n".join(report(f)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
