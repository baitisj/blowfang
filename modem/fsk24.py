"""The bench signal and report for c/k6/mod/fsk24rx.c: can the K6's own FSK modem receive plain
(direct) FSK at 2400 baud once the IC-7100's transmit audio is pre-equalised?

  python3 -m modem.fsk24 wav OUT.wav [--rounds N]    the bench signal (rounds of every variant)
  python3 -m modem.fsk24 sim [--channel ic7100]      the variants through a model of the
                                                     IC-7100's transmit path, read by our own
                                                     demodulator (what to expect, not the chip)
  python3 -m modem.fsk24 report LOG                  the module's lines: per setting and variant

A round is one block per variant: the pager's framing (pager.Air: lead, 16 bytes of 0x55, sync
07 6B 3E 36) around PER_BLOCK test packets, each 64 bytes:
  0 0xF2, 1 variant, 2 sequence, 3..61 PN9 (the same every packet), 62..63 CRC-16/CCITT-FALSE.
A variant is the baud rate, the pre-equaliser against the IC-7100's transmit audio and the level.
The IC-7100's USB audio in FM-D fitted best as pre-emphasis with a low cut near 300 Hz and a top
near 3 kHz (docs/uv380-c6000-modem.md, C2); plain FSK carries its data down to DC, which that
low cut takes. The round ends with carrier only; the wav ends with AG7EW in MCW.
SPDX-License-Identifier: MIT
"""
import argparse
import re
import sys
from dataclasses import dataclass

import numpy as np

from . import fsk, pager

MAGIC = 0xF2
NB = 64
PN_FIRST, PN_N = 3, 59
PER_BLOCK = 4
GAP_S = 0.15                 # carrier only between blocks
LEAD_S = 0.6                 # carrier only at the start (the K6's squelch and AGC)


@dataclass(frozen=True)
class Variant:
    baud: int
    comp: str                # none, em, emhp, emhplp, hp
    level: float
    gain_db: float = 15.0    # the pre-equaliser's cap above its 1 kHz gain


VARIANTS = (
    Variant(1200, "none", 0.25),          # 0: the control (the pager's D/1200 read 31/31 so, 11-05)
    Variant(1200, "emhp", 0.3, 30.0),     # 1
    Variant(2400, "none", 0.25),          # 2: as every 2400 try before
    Variant(2400, "emhp", 0.15, 20.0),    # 3: the C6000 fit's model undone (sim: 20 dB is enough
    Variant(2400, "emhp", 0.3, 20.0),     # 4  at 2400, 1200 needs 30)
    Variant(2400, "emhp", 0.2, 30.0),     # 5
    Variant(2400, "emhplp", 0.25, 24.0),  # 6: and the 3 kHz top
    Variant(2400, "em", 0.25),            # 7: the pre-emphasis alone undone
)


def channel(comp):
    """the IC-7100 transmit path a variant undoes (fsk.Channel), or None"""
    return {
        "none": None,
        "em": fsk.Channel(pre_emph=300.0, hpf=None, lpf=None),
        "hp": fsk.Channel(pre_emph=None, hpf=300.0, lpf=None),
        "emhp": fsk.Channel(pre_emph=300.0, hpf=300.0, lpf=None),
        "emhplp": fsk.Channel(pre_emph=300.0, hpf=300.0, lpf=3000.0),
    }[comp]


def pn9(n):
    out, st = bytearray(), 0x1FF
    for _ in range(n):
        out.append(st & 0xFF)
        for _ in range(8):
            st = st >> 1 | ((st ^ st >> 5) & 1) << 8
    return bytes(out)


def crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else c << 1
            c &= 0xFFFF
    return c


def packet(variant, seq):
    body = bytes([MAGIC, variant & 0xFF, seq & 0xFF]) + pn9(PN_N)
    return body + crc16(body).to_bytes(2, "big")


def block_bits(v_index, seq0, n=PER_BLOCK):
    v = VARIANTS[v_index]
    air = pager.Air(preamble_bytes=16, lead_ms=150.0, tail_ms=40.0)
    return air.burst_bits([packet(v_index, seq0 + k) for k in range(n)], v.baud)


def block_audio(v_index, seq0, fs=fsk.FS, n=PER_BLOCK):
    v = VARIANTS[v_index]
    bits = block_bits(v_index, seq0, n)
    return fsk.modulate(bits, "D", v.baud, fs, v.level, comp=channel(v.comp), max_gain_db=v.gain_db)


def rounds(n_rounds, fs=fsk.FS, variants=None):
    """-> audio, [(t0 s, baud, bits, variant)] of every block"""
    variants = list(range(len(VARIANTS))) if variants is None else variants
    parts, segs, t = [np.zeros(int(LEAD_S * fs))], [], LEAD_S
    for r in range(n_rounds):
        for vi in variants:
            seq0 = (r * PER_BLOCK) & 0xFF
            x = block_audio(vi, seq0, fs)
            segs.append((t, float(VARIANTS[vi].baud), block_bits(vi, seq0), vi))
            parts += [x, np.zeros(int(GAP_S * fs))]
            t += (len(x) + int(GAP_S * fs)) / fs
    return np.concatenate(parts), segs


def round_seconds(fs=fsk.FS):
    x, _ = rounds(1, fs)
    return (len(x) - int(LEAD_S * fs)) / fs


# ---- what to expect: our demodulator through a model of the IC-7100's transmit path ----
def sim(n_rounds=2, ch="ic7100", fs=fsk.FS):
    """each variant through the model channel, read by modem.fsk's demodulator -> {variant: (ok, n)}"""
    path = {"ic7100": fsk.Channel(pre_emph=300.0, hpf=300.0, lpf=3000.0),
            "flat": None}[ch]
    out = {}
    for vi, v in enumerate(VARIANTS):
        got = sent = 0
        for r in range(n_rounds):
            x = block_audio(vi, r * PER_BLOCK, fs)
            y = path.apply(x, fs) if path else x
            dem = fsk.Demodulator("D", v.baud, fs, payload=NB)
            for _, pl, _, _ in dem.demodulate(y):
                if pl[:2] == bytes([MAGIC, vi]) and crc16(pl[:62]) == int.from_bytes(pl[62:], "big"):
                    got += 1
            sent += PER_BLOCK
        out[vi] = (got, sent)
    return out


# ---- the module's lines ----
LINE = re.compile(r"K6 MOD say (.*)")


def parse_log(lines):
    """-> settings {s: dict}, packets [dict]"""
    sets, pk = {}, []
    for ln in lines:
        m = LINE.search(ln)
        if not m:
            continue
        t = m.group(1).split()
        if not t:
            continue
        kvs = dict(x.split("=", 1) for x in t[2:] if "=" in x)
        if t[0] == "s":
            sets[int(t[1])] = kvs
        elif t[0] == "p":
            pk.append({"s": int(t[1]), **{k: int(v) for k, v in kvs.items()}})
        elif t[0] == "v" and int(t[1]) in sets:
            sets[int(t[1])]["end"] = kvs
    return sets, pk


def report(lines):
    sets, pk = parse_log(lines)
    out = []
    for s, kv in sorted(sets.items()):
        b = round(int(kv.get("b", "0"), 16) / 10.32444)
        mine = [p for p in pk if p["s"] == s]
        out.append(f"setting {s}: REG_58 {kv.get('cfg')} {b} baud REG_2B +{kv.get('a')}00: "
                   f"{len(mine)} packets, {sum(p['c'] for p in mine)} CRC right")
        for vi, v in enumerate(VARIANTS):
            ps = [p for p in mine if p["v"] == vi]
            if not ps:
                continue
            ok = sum(p["c"] for p in ps)
            e = sorted(p["e"] for p in ps)
            inv = sum(p["i"] for p in ps)
            out.append(f"  v{vi} {v.baud} {v.comp:6s} {v.level:.2f}: {ok}/{len(ps)} right, "
                       f"bits wrong median {e[len(e) // 2]} of 472, upside down {inv}")
        other = [p for p in mine if p["v"] >= len(VARIANTS)]
        if other:
            out.append(f"  {len(other)} with a broken header (median {sorted(p['e'] for p in other)[len(other) // 2]} wrong)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m modem.fsk24", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wav")
    w.add_argument("out")
    w.add_argument("--rounds", type=int, default=12)
    w.add_argument("--cw-id", default="AG7EW")
    s = sub.add_parser("sim")
    s.add_argument("--channel", choices=("ic7100", "flat"), default="ic7100")
    r = sub.add_parser("report")
    r.add_argument("log")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        from .k6 import _write, cw
        x, segs = rounds(a.rounds)
        if a.cw_id:
            x = np.concatenate([x, np.zeros(int(0.3 * fsk.FS)), cw(a.cw_id, fsk.FS, level=0.3)])
        _write(a.out, x, fsk.FS)
        print(f"{a.out}: {len(x) / fsk.FS:.1f} s, {a.rounds} rounds of {round_seconds():.1f} s, "
              f"{len(segs) * PER_BLOCK} packets, then {a.cw_id or 'no ID'}")
    elif a.cmd == "sim":
        for vi, (got, sent) in sim(ch=a.channel).items():
            v = VARIANTS[vi]
            print(f"v{vi} {v.baud} {v.comp:6s} {v.level:.2f}: {got}/{sent}")
    else:
        with open(a.log, errors="replace") as f:
            print("\n".join(report(f.read().splitlines())))


if __name__ == "__main__":
    sys.exit(main())
