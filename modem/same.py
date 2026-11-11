"""NOAA SAME-format bursts for the K6's SAME probe (c/k6/mod/samecap.c, docs/k6-pocsag.md, "NOAA
SAME"), and a reader for its lines.

SAME (47 CFR 11.31): AFSK on the FM audio, mark 2083.3 Hz (a 1), space 1562.5 Hz (a 0), 520.83
baud, bytes with the low bit first; a header is sixteen bytes of AB and then its text, sent three
times about a second apart. The bench's bursts have the format but no EAS header (no "ZCZC", no
event code): their text is 'AG7EW SAME FORMAT TEST <n>', so nothing on the air looks like an alert.

  python3 -m modem.same wav OUT.wav [--rounds 33] [--level 0.35]   the bench signal (samecap's 160 s)
  python3 -m modem.same report LOG                                  what samecap's captures hold
  python3 -m modem.same df LOG                                      samedf's samples, read
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


# ---- the plain receiver as a sampler of the audio (c/k6/mod/samedf.c) ----
# At REG_72 for 8 x 520.83 = 4166.7 samples a second the 2083.3 Hz mark is two samples a cycle:
# the chip's clock, which follows the transitions it slices (docs/k6-aprs-clock.md: 2200 Hz at a
# nominal 4800 came at 4400), takes it as 1010... and the sync word AAAA starts a capture on the
# preamble's runs of three mark bits. The 1562.5 Hz space is 2.67 samples a cycle. A bit is eight
# samples: four cycles of mark or three of space, so over a bit's eight samples the two tones are
# orthogonal.
DF_SPS = 8
DF_RATE = DF_SPS * BAUD
DF_B72 = int(round(DF_RATE * 10.32444))
DF_SYNC = 0xAAAA


def df_samples(bits, phase=0.5, rate=1.0, noise=0.0, seed=1, lead=0.0):
    """the chip as a sampler of SAME's audio: the sign of the tone (plus noise, as a share of its
    amplitude) at DF_RATE x rate, sample k at (k + phase) / that, from lead s before the burst"""
    rng = np.random.default_rng(seed)
    fs = DF_RATE * rate
    n = int((len(bits) / BAUD + lead) * fs)
    t = (np.arange(n) + phase) / fs - lead
    b = np.clip(np.floor(t * BAUD).astype(int), 0, len(bits) - 1)
    f = np.where(np.asarray(bits)[b] > 0, MARK, SPACE)
    y = np.sin(2 * np.pi * f * (t - b / BAUD))
    y = np.where(t < 0, 0.0, y) + noise * rng.standard_normal(n)
    return (y > 0).astype(np.uint8)


def df_metric(samples, fs=DF_RATE):
    """for each start i: mark against space over a bit's worth of samples from i (fs: samples a
    second)"""
    L = max(4, int(round(fs / BAUD)))
    k = np.arange(L)
    mk, sp = np.exp(-2j * np.pi * MARK / fs * k), np.exp(-2j * np.pi * SPACE / fs * k)
    x = 2.0 * np.asarray(samples, float) - 1
    if len(x) < L:
        return np.zeros(0)
    w = np.lib.stride_tricks.sliding_window_view(x, L)
    return np.abs(w @ mk) - np.abs(w @ sp)


def df_bits(samples, fs=DF_RATE):
    """the bits: the phase that best tells the tones apart over the first 64 bits, then moved
    every eight bits toward the better of a sample early or late (half a sample at most)"""
    d = df_metric(samples, fs)
    sps = fs / BAUD
    if len(d) < 16 * sps:
        return np.zeros(0, np.uint8)
    L0 = int(round(sps))
    p = max(range(L0), key=lambda q: np.sum(np.abs(d[np.round(q + sps * np.arange(min(64, int((len(d) - q) / sps)))).astype(int)])))
    out, pos, k = [], float(p), 0
    while int(round(pos)) < len(d):
        i = int(round(pos))
        out.append(1 if d[i] > 0 else 0)
        k += 1
        if k % 8 == 0:
            back = np.round(pos - sps * np.arange(8)).astype(int)
            if back.min() - 1 >= 0 and back.max() + 1 < len(d):
                e, c, l = (np.sum(np.abs(d[back + o])) for o in (-1, 0, 1))
                pos += float(np.clip((l - e) / (c + 1e-9), -0.5, 0.5))
        pos += sps
    return np.array(out, np.uint8)


def df_best(samples, fs):
    """the reading over samples-a-second from 0.8 to 1.2 of fs (the chip's clock follows the
    tones): (text, preamble bytes, samples a second)"""
    best = ("", 0, fs)
    for f in fs * np.linspace(0.8, 1.2, 41):
        t, n = df_text(df_bits(samples, f))
        if (len(t) + 4 * n) > (len(best[0]) + 4 * best[1]):
            best = (t, n, f)
    return best


def df_report(lines):
    """samedf's captures, read"""
    from modem.pocsag import dump_captures, runs_of, word_bits
    out = []
    for cap in dump_captures(lines):
        fs = int(cap.get("b", "0"), 16) / 10.32444
        words = cap["words"]
        x = word_bits(words)
        rs = [r[1] for r in runs_of(x)[1:-1]]
        hist = " ".join(f"{k}:{rs.count(k)}" for k in range(1, 7))
        t, n, f = df_best(x, fs)
        out.append(f"c{cap['n']} s={cap.get('s')} cfg={cap.get('cfg')} sy={cap.get('sy')} {fs:.0f}/s "
                   f"r=-{cap.get('r', '?').lstrip('-')}: {len(x)} samples, gaps "
                   f"{sum(w is None for w in words)}, runs {hist}")
        out.append(f"  read at {f:.0f}/s: preamble bytes {n}, text {t!r}")
    return out


def df_text(bits):
    """(text after the preamble, preamble bytes seen): the bytes from the first AB on, low bit
    first; the text runs to the first byte that is not printable"""
    bits = list(int(b) for b in bits)
    for o in range(len(bits) - 15):
        if sum(bits[o + k] << k for k in range(8)) == 0xAB and sum(bits[o + 8 + k] << k for k in range(8)) == 0xAB:
            break
    else:
        return "", 0
    npre, text, i = 0, "", o
    while i + 8 <= len(bits):
        c = sum(bits[i + k] << k for k in range(8))
        i += 8
        if not text and c == 0xAB:
            npre += 1
            continue
        if not (32 <= c < 127):
            break
        text += chr(c)
    return text, npre


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
    q = sub.add_parser("df")
    q.add_argument("log")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        from modem.pocsag import write_wav
        y = bench_wav(a.rounds, level=a.level)
        write_wav(a.out, y)
        print(f"{a.out}: {len(y) / FS:.1f} s, {3 * a.rounds} bursts")
    else:
        with open(a.log, errors="replace") as f:
            print("\n".join((report if a.cmd == "report" else df_report)(f)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
