#!/usr/bin/env python3
"""Keep on-air recordings as CI regression tests (tests/data/k6/air/).

Every recording of the K6 on the air that taught us something becomes a test: the audio (FLAC,
trimmed to the part that matters) and a sidecar NAME.json saying what it is and what each
decoder must get out of it. tests/test_k6_air_cases.py runs every sidecar in CI, so a change to
a decoder that loses a real signal fails there, not on the bench. One recording, one sidecar:
two threads adding recordings never edit the same file.

  python3 tools/k6_air.py add REC.wav NAME --what "..." --image abc1234 \\
        --check ft8 [--check cw:mode=tone] [--from S --to S]
      trims REC (any rate; the first channel) to [from, to] s, writes tests/data/k6/air/NAME.flac
      and NAME.json, with each check's decode as its expectation. Refuses a check that decodes
      nothing, and a FLAC over 2 MB (trim it).
  python3 tools/k6_air.py check [NAME ...]      decode the sidecars again, report any change
  python3 tools/k6_air.py decode REC.wav CHECK   what one decoder makes of a recording

A CHECK is a decoder and its options, KIND[:key=value,...]:
  ft8       FT8 with the K6's receiver core on the host (sim/k6digi/ft8_wav.py): every text
            (options lo, hi: the search band, Hz)
  wspr      WSPR (sim/k6digi/wsprref.py): every text (fc: tone 0's audio frequency, 1500)
  cw        Morse by ear: the text (mode=tone: MCW, a keyed audio tone, as the K6's and the
            IC-7100's CW IDs; mode=quiet: a keyed carrier heard on FM as noise that goes quiet
            while the key is down, as the K6's cwkey; wpm=N fixes the speed, else it is found)
  fsk       the pager's packets (modem.fsk.receive): good packets and message ids
            (phy=D|F, baud=1200|1800|2400, payload=L|XL, invert=0|1)
  psk31     PSK31 (modem/psk31.py): the text (tone, 1000)

Needs numpy, scipy and soundfile, as the rest of the bench tools. SPDX-License-Identifier: MIT
"""
import argparse
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AIR = os.path.join(ROOT, "tests", "data", "k6", "air")
MAX_BYTES = 2_000_000
sys.path.insert(0, ROOT)


# ---- Morse by ear ----

MORSE = {
    ".-": "A",
    "-...": "B",
    "-.-.": "C",
    "-..": "D",
    ".": "E",
    "..-.": "F",
    "--.": "G",
    "....": "H",
    "..": "I",
    ".---": "J",
    "-.-": "K",
    ".-..": "L",
    "--": "M",
    "-.": "N",
    "---": "O",
    ".--.": "P",
    "--.-": "Q",
    ".-.": "R",
    "...": "S",
    "-": "T",
    "..-": "U",
    "...-": "V",
    ".--": "W",
    "-..-": "X",
    "-.--": "Y",
    "--..": "Z",
    "-----": "0",
    ".----": "1",
    "..---": "2",
    "...--": "3",
    "....-": "4",
    ".....": "5",
    "-....": "6",
    "--...": "7",
    "---..": "8",
    "----.": "9",
    "-..-.": "/",
    "-...-": "=",
    ".-.-.": "+",
}
HOP_S = 0.004


def _envelope(x, fs, mode, tone=None):
    """(level in dB every HOP_S, keyed-is-high flag)"""
    x = np.asarray(x, float) - np.mean(x)
    hop = max(1, int(round(fs * HOP_S)))
    win = 2 * hop
    if mode == "tone":
        if not tone:
            spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
            f = np.fft.rfftfreq(len(x), 1 / fs)
            band = (f > 300) & (f < 1500)
            tone = float(f[band][np.argmax(spec[band])])
        z = x * np.exp(-2j * np.pi * tone * np.arange(len(x)) / fs)
        k = max(1, int(fs * 0.006))
        z = np.convolve(z, np.ones(k) / k, "same")
        p = np.abs(z) ** 2
    elif mode == "quiet":
        d = np.diff(x, 2, prepend=x[:2])        # noise above the speech band: the squelch's hiss
        p = d * d
    else:
        raise ValueError(f"cw mode {mode!r}: tone or quiet")
    n = (len(p) - win) // hop
    e = np.array([p[i * hop:i * hop + win].mean() for i in range(max(n, 0))])
    return 10 * np.log10(e + 1e-20), mode == "tone"


def cw_decode(x, fs, mode="tone", wpm=None, tone=None):
    """the Morse in a recording as text, word gaps as spaces"""
    db, high = _envelope(x, fs, mode, tone)
    if len(db) < 10:
        return ""
    lo, hi = np.percentile(db, 10), np.percentile(db, 90)
    if hi - lo < 12:                            # no keying: noise alone spreads less
        return ""
    on = db > (lo + hi) / 2 if high else db < (lo + hi) / 2
    runs, cur, n = [], on[0], 0
    for v in on:
        if v == cur:
            n += 1
        else:
            runs.append((bool(cur), n * HOP_S * 1000))
            cur, n = v, 1
    runs.append((bool(cur), n * HOP_S * 1000))
    merged = []
    for k, ms in runs:                          # glitches under 12 ms join their neighbours
        if merged and (ms < 12 or merged[-1][0] == k):
            merged[-1] = (merged[-1][0], merged[-1][1] + ms)
        else:
            merged.append((k, ms))
    while merged and not merged[0][0]:
        merged.pop(0)
    while merged and not merged[-1][0]:
        merged.pop()
    marks = sorted(ms for k, ms in merged if k)
    if not marks:
        return ""
    if wpm:
        unit = 1200.0 / wpm
    else:
        shortest = marks[len(marks) // 10]
        unit = float(np.median([m for m in marks if m < 2 * shortest]))
    out, sym = "", ""
    for k, ms in merged:
        u = ms / unit
        if k:
            sym += "." if u < 2 else "-"
        elif u >= 2:
            out += MORSE.get(sym, "?")
            sym = ""
            if u >= 5:
                out += " "
    out += MORSE.get(sym, "?") if sym else ""
    return out.strip()


# ---- the decoders ----

def parse_check(spec):
    """'cw:mode=tone,wpm=20' -> ('cw', {'mode': 'tone', 'wpm': 20})"""
    kind, _, rest = spec.partition(":")
    opts = {}
    for kv in filter(None, rest.split(",")):
        k, _, v = kv.partition("=")
        try:
            opts[k] = int(v)
        except ValueError:
            try:
                opts[k] = float(v)
            except ValueError:
                opts[k] = v
    if kind not in DECODERS:
        raise SystemExit(f"no decoder {kind!r}: {', '.join(sorted(DECODERS))}")
    return kind, opts


def d_ft8(x, fs, lo=200.0, hi=3000.0):
    from sim.k6digi import ft8_wav
    return {"texts": sorted(ft8_wav.decode_audio(x, fs, lo, hi))}


def d_wspr(x, fs, fc=1500.0):
    from sim.k6digi import wsprref
    return {"texts": sorted({t[0] for t in wsprref.decode_audio(x, fs, fc)})}


def d_cw(x, fs, mode="tone", wpm=None, tone=None):
    return {"text": cw_decode(x, fs, mode, wpm, tone)}


def d_fsk(x, fs, phy="F", baud=1800, payload="XL", invert=0):
    from modem import fsk, pager
    asm, times = fsk.receive(x, fs, phy, float(baud), getattr(pager, payload), invert=bool(invert))
    return {"good": sum(1 for t in times if t[1]), "messages": sorted(int(m) for m in asm.messages)}


def d_psk31(x, fs, tone=1000.0):
    from modem import psk31
    return {"text": psk31.demodulate(x, fs, tone).strip()}


DECODERS = {
    "ft8": d_ft8,
    "wspr": d_wspr,
    "cw": d_cw,
    "fsk": d_fsk,
    "psk31": d_psk31,
}


def decode(kind, opts, x, fs):
    return DECODERS[kind](x, fs, **opts)


def empty(got):
    return not any(v for v in got.values())


# ---- the files ----

def load(path):
    import soundfile as sf
    x, fs = sf.read(path, dtype="float64", always_2d=True)
    return x[:, 0], fs


def cases(d=AIR):
    """every sidecar: [(name, dict)]"""
    out = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            out.append((f[:-5], json.load(open(os.path.join(d, f)))))
    return out


def run_case(meta, d=AIR):
    """[(check, expected, got)] for one sidecar"""
    x, fs = load(os.path.join(d, meta["file"]))
    res = []
    for c in meta["checks"]:
        res.append((c, c["expect"], decode(c["decoder"], c.get("opts", {}), x, fs)))
    return res


def add(rec, name, checks, what, image="", when="", t_from=None, t_to=None, d=AIR):
    import soundfile as sf
    if not name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("NAME: letters, digits, - and _ only (e.g. ft8tx-run6-20261106T1857Z)")
    x, fs = load(rec)
    a = int((t_from or 0) * fs)
    b = int(t_to * fs) if t_to else len(x)
    x = x[a:b]
    out = []
    for spec in checks:
        kind, opts = parse_check(spec)
        got = decode(kind, opts, x, fs)
        if empty(got):
            raise SystemExit(f"{spec}: decoded nothing from {rec} [{a / fs:.1f}, {b / fs:.1f}] s")
        out.append({"decoder": kind, "opts": opts, "expect": got})
    flac = os.path.join(d, name + ".flac")
    side = os.path.join(d, name + ".json")
    for p in (flac, side):
        if os.path.exists(p):
            raise SystemExit(f"{p} exists: pick another NAME")
    sf.write(flac, x, fs, format="FLAC", subtype="PCM_16")
    if os.path.getsize(flac) > MAX_BYTES:
        os.remove(flac)
        raise SystemExit(f"over {MAX_BYTES} bytes as FLAC: trim it with --from/--to")
    meta = {"file": name + ".flac", "what": what, "when": when, "image": image,
            "source": f"{os.path.basename(rec)} [{a / fs:.2f}, {b / fs:.2f}] s", "checks": out}
    with open(side, "w") as f:
        json.dump(meta, f, indent=1)
        f.write("\n")
    return flac, side, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("rec")
    p.add_argument("name")
    p.add_argument("--check", action="append", required=True, help="KIND[:k=v,...], more than once for more")
    p.add_argument("--what", required=True, help="one line: what is on it and what it showed")
    p.add_argument("--image", default="", help="the K6 image's build id")
    p.add_argument("--when", default="", help="UTC, e.g. 2026-11-06 18:57")
    p.add_argument("--from", dest="t_from", type=float)
    p.add_argument("--to", dest="t_to", type=float)
    p = sub.add_parser("check")
    p.add_argument("names", nargs="*")
    p = sub.add_parser("decode")
    p.add_argument("rec")
    p.add_argument("check")
    a = ap.parse_args(argv)
    if a.cmd == "add":
        flac, side, meta = add(a.rec, a.name, a.check, a.what, a.image, a.when, a.t_from, a.t_to)
        print(f"wrote {flac} ({os.path.getsize(flac)} bytes) and {side}:")
        for c in meta["checks"]:
            print(f"  {c['decoder']} {c['opts']} -> {c['expect']}")
        print("add both to git; tests/test_k6_air_cases.py picks them up")
    elif a.cmd == "decode":
        kind, opts = parse_check(a.check)
        x, fs = load(a.rec)
        print(json.dumps(decode(kind, opts, x, fs)))
    else:
        bad = 0
        for name, meta in cases():
            if a.names and name not in a.names:
                continue
            for c, want, got in run_case(meta):
                ok = got == want
                bad += not ok
                print(f"{'ok  ' if ok else 'DIFF'} {name} {c['decoder']}"
                      + ("" if ok else f": want {want}, got {got}"))
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
