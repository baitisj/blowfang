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
    # run 2: each bit as 2 (2400 baud) or 4 (1200) DMR symbols of +-3 at 4800 a second, RRC
    # shaped, through the pre-equaliser the UV-380's C6000 measured on the IC-7100 (its soft
    # values, docs/uv380-c6000-modem.md R1-R4: tools/uv380_dmrburst.py fit). level: a +-3
    # symbol's peak before the equaliser's gain, as that tool's --amp (0.10-0.12 was its best)
    Variant(2400, "c6eq", 0.08),          # 8
    Variant(2400, "c6eq", 0.12),          # 9
    Variant(2400, "c6eq", 0.16),          # 10
    Variant(1200, "c6eq", 0.12),          # 11
    Variant(2400, "rrc", 0.25),           # 12: the same symbols with no equaliser
    # run 3: a flat FM transmitter (the ADALM-Pluto, tools/pluto_bench.py): Gaussian (BT 0.5) NRZ
    # straight onto the carrier; level is the peak deviation in kHz
    Variant(1200, "iq", 2.2),             # 13: the control
    Variant(2400, "iq", 2.2),             # 14: the RT-950 note's FSK 2400 deviation
    Variant(2400, "iq", 1.5),             # 15
    Variant(2400, "iq", 3.0),             # 16
    Variant(2400, "iq", 4.5),             # 17: POCSAG's
    # the sensitivity sweep (sweep_track): FFSK 1200/1800 tones (the pager's F) as any voice HT
    # sends them, 750 us pre-emphasised; level is the peak deviation in kHz after it
    Variant(1800, "iqF", 3.0),            # 18: the pager's F/1800, today's default
    Variant(1200, "iqF", 3.0),            # 19: the pager's F/1200
    # 20-23: kept for the UV-380's C6000 physical layer mode (blowfang PR #139 defines them)
    Variant(2400, "reserved", 0.0),       # 20
    Variant(2400, "reserved", 0.0),       # 21
    Variant(1200, "reserved", 0.0),       # 22
    Variant(1200, "reserved", 0.0),       # 23
    Variant(1800, "iq", 2.2),             # 24: D/1800 for the third sweep (Jeff 11-16)
)
RUN1 = tuple(range(8))
RUN2 = (0, 8, 9, 10, 11, 12)
RUN3 = (13, 14, 15, 16, 17)
# the sensitivity sweep: one slot of SWEEP_SLOT_S per rate, the rates in turn, at each level step
# (dB below the top level); the module (c/k6/mod/fsk24rx_sweep.c) sets each slot's receive recipe
# a slot: (name, the bench's variant, the module's REG_58, REG_72, word in 1/16 ticks, REG_2B
# bits). Sweep 1 (11-16 20:48Z) had D/2400, F/1800 and F/1200, every recipe read by time: D/2400
# good, the FFSK slots failed even at full level (timed reads don't suit FFSK in this probe; sweep
# 2's interrupt reads didn't either), so F/1800 was measured through the pager (steps_report).
# Sweep 3 (Jeff 11-16 21:54Z, "Should we test D/1800 and D/1200 then?"): plain FSK at each rate,
# all at 2.2 kHz peak, the RX high-pass off, read by time as sweep 1's D/2400
SWEEP_SLOTS = (
    ("D/2400", 14, (0x00E9, 24779, 1067, 0x0400)),         # run 3's setting 2 (FSK 2.4K bandwidth)
    ("D/1800", 24, (0x00E9, 18584, 1422, 0x0400)),         # the 2.4K bandwidth, BAUD_1800
    ("D/1800 1.2K", 24, (0x00E1, 18584, 1422, 0x0400)),    # the 1.2K bandwidth: narrower, enough?
    ("D/1200", 13, (0x00E1, 12389, 2133, 0x0400)),         # FSK 1.2K bandwidth (run 3's setting 0)
)
SWEEP_STEPS = tuple(range(0, -24, -3))     # 0 to -21 dB: from about -88 dBm at TX gain -70
SWEEP_SLOT_S = 12
SWEEP_GUARD_S = (1.5, 1.0)                 # carrier only at a slot's start and end
EQ = {"taps": [1.0], "centre": 0}          # the measured equaliser: wav --eq loads it here


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


def symbol_audio(bits, baud, fs, amp, eq=None):
    """bits as +-3 DMR symbols (4800 a second, 4800 / baud a bit), through eq ({"taps",
    "centre"}, uv380_dmrburst's), the RRC (alpha 0.2), a +-3 symbol at +-amp before eq's gain"""
    from sim.k6if import dmr
    per = int(round(dmr.BAUD / baud))
    sym = np.repeat(np.where(np.asarray(bits) > 0, 3.0, -3.0), per)
    if eq:
        g = np.asarray(eq["taps"], float)
        sym = np.convolve(sym, g)[eq["centre"]:eq["centre"] + len(sym)]
    sps = fs // dmr.BAUD
    up = np.zeros(len(sym) * sps)
    up[::sps] = sym * sps
    x = np.convolve(up, dmr.rrc(sps), "same") / 3.0 * amp
    return np.clip(fsk._ramp(x, fs), -1.0, 1.0)


def block_audio(v_index, seq0, fs=fsk.FS, n=PER_BLOCK):
    v = VARIANTS[v_index]
    bits = block_bits(v_index, seq0, n)
    if v.comp in ("c6eq", "rrc"):
        return symbol_audio(bits, v.baud, fs, v.level, EQ if v.comp == "c6eq" else None)
    if v.comp == "iq":                    # what a flat receiver's discriminator gives: 9 kHz = 1.0
        return fsk._ramp(fsk.nrz(bits, v.baud, fs, 0.5) * v.level / 9.0, fs)
    if v.comp == "iqF":                   # the tones as a de-emphasising receiver gives them
        return fsk.modulate(bits, "F", v.baud, fs, v.level / 9.0)
    return fsk.modulate(bits, "D", v.baud, fs, v.level, comp=channel(v.comp), max_gain_db=v.gain_db)


def rounds(n_rounds, fs=fsk.FS, variants=None):
    """-> audio, [(t0 s, baud, bits, variant)] of every block"""
    variants = list(RUN1) if variants is None else variants
    parts, segs, t = [np.zeros(int(LEAD_S * fs))], [], LEAD_S
    for r in range(n_rounds):
        for vi in variants:
            seq0 = (r * PER_BLOCK) & 0xFF
            x = block_audio(vi, seq0, fs)
            segs.append((t, float(VARIANTS[vi].baud), block_bits(vi, seq0), vi))
            parts += [x, np.zeros(int(GAP_S * fs))]
            t += (len(x) + int(GAP_S * fs)) / fs
    return np.concatenate(parts), segs


def freq_track(n_rounds, fs=fsk.FS, variants=RUN3):
    """run 3: the instantaneous frequency (Hz, at fs) a flat FM transmitter sends -> track, segs
    [(t0 s, baud, bits, variant)]; carrier only (0 Hz) in the lead and the gaps"""
    parts, segs, t = [np.zeros(int(LEAD_S * fs))], [], LEAD_S
    for r in range(n_rounds):
        for vi in variants:
            v = VARIANTS[vi]
            assert v.comp == "iq", (vi, v)
            seq0 = (r * PER_BLOCK) & 0xFF
            bits = block_bits(vi, seq0)
            x = fsk._ramp(fsk.nrz(bits, v.baud, fs, 0.5) * v.level * 1000.0, fs)
            segs.append((t, float(v.baud), bits, vi))
            parts += [x, np.zeros(int(GAP_S * fs))]
            t += (len(x) + int(GAP_S * fs)) / fs
    return np.concatenate(parts), segs


def preemph(x, fs):
    """750 us pre-emphasis (6 dB an octave from 212 Hz), shelved at 3 kHz, 0 dB at 1 kHz"""
    from scipy import signal as sps
    w1, w2 = 1.0 / 750e-6, 2 * np.pi * 3000.0
    b, a = sps.bilinear([1.0 / w1, 1.0], [1.0 / w2, 1.0], fs)
    _, h = sps.freqz(b, a, [1000.0], fs=fs)
    return sps.lfilter(b, a, np.asarray(x, float)) / abs(h[0])


def variant_freq(vi, bits, fs=fsk.FS):
    """a flat FM transmitter's instantaneous frequency (Hz) for one block of variant vi"""
    v = VARIANTS[vi]
    if v.comp == "iq":
        return fsk._ramp(fsk.nrz(bits, v.baud, fs, 0.5) * v.level * 1000.0, fs)
    assert v.comp == "iqF", (vi, v)
    x = preemph(fsk.modulate(bits, "F", v.baud, fs, 1.0), fs)
    return fsk._ramp(x / np.max(np.abs(x)) * v.level * 1000.0, fs)


def sweep_plan():
    """-> [(slot, step dB, variant, SWEEP_SLOTS index)]: every slot kind in turn at each step"""
    kinds = [(st, si) for st in SWEEP_STEPS for si in range(len(SWEEP_SLOTS))]
    return [(k, st, SWEEP_SLOTS[si][1], si) for k, (st, si) in enumerate(kinds)]


def sweep_table():
    """the module's slot table (F24_V) -> C initializer lines"""
    return ["{0x%04X, %d, %d, 0x%04X}," % SWEEP_SLOTS[si][2] for _, _, _, si in sweep_plan()]


def sweep_track(fs=fsk.FS):
    """-> track (Hz at fs, t = 0 at the module's slot 0, its "s 0" line), level (linear, a
    sample's amplitude against the top step), segs [(t0 s, slot, step, variant, packets)].
    Each slot carries its rate's blocks at its step's level, inside the guards; carrier only
    between them. Sequence numbers: step index * 16 + packet (so a packet names its step)."""
    plan = sweep_plan()
    n_slot = int(SWEEP_SLOT_S * fs)
    track = np.zeros(n_slot * len(plan))
    level = np.ones(len(track))
    segs = []
    for k, st, vi, _ in plan:
        a, end = k * n_slot, (k + 1) * n_slot
        level[a:end] = 10 ** (st / 20.0)
        t = a + int(SWEEP_GUARD_S[0] * fs)
        q = (SWEEP_STEPS.index(st) * 16) & 0xFF
        sent = 0
        while True:
            bits = block_bits(vi, q + sent)
            x = variant_freq(vi, bits, fs)
            if t + len(x) > end - int(SWEEP_GUARD_S[1] * fs) or sent + PER_BLOCK > 16:
                break
            track[t:t + len(x)] = x
            t += len(x) + int(GAP_S * fs)
            sent += PER_BLOCK
        segs.append((a / fs, k, st, vi, sent))
    return track, level, segs


def sweep_report(lines):
    """the module's lines from a sweep -> table lines: per step, the RSSI (median of every
    packet heard in that step) and packets right of sent, per rate"""
    sent = {k: n for _, k, _, _, n in sweep_track()[2]}
    sets, pk = parse_log(lines)
    head = "step dB | RSSI dBm | " + " | ".join(name for name, _, _ in SWEEP_SLOTS)
    out = [head, "--- | --- | " + " | ".join("---" for _ in SWEEP_SLOTS)]
    for i, st in enumerate(SWEEP_STEPS):
        cells, rs = [], []
        for k, s2, vi, _ in sweep_plan():
            if s2 != st:
                continue
            mine = [p for p in pk if p["s"] == k]
            rs += [p["r"] for p in mine if "r" in p]
            ok = sum(1 for p in mine if p["c"] and p["v"] == vi and p["q"] // 16 == i)
            cells.append(f"{ok}/{sent[k]}" if k in sets else f"-/{sent[k]}")
        r = f"{sorted(rs)[len(rs) // 2]}" if rs else "-"
        out.append(f"{st} | {r} | " + " | ".join(cells))
    return out


def steps_report(lines, steps):
    """the pager's own receive path through a sensitivity ladder (tools/pluto_bench.py steps):
    the K6 logger's lines ("EPOCH<tab>BUILD<tab>K6 PKT ...") and the sends' {"steps": [{gain,
    t0, t1}]} -> table lines: per gain, packets with the CRC right, failed, and the failed ones'
    median bit errors against the test message"""
    from .k6_harness import parse_line
    pk = []
    for ln in lines:
        f = ln.split("\t")
        if len(f) < 2 or "K6 PKT" not in f[-1]:
            continue
        try:
            t = float(f[0])
        except ValueError:
            continue
        d = parse_line(f[-1])
        if d:
            pk.append((t, d))
    out = ["TX gain dB | CRC right | failed | bits wrong (median, failed)", "--- | --- | --- | ---"]
    for st in steps["steps"]:
        mine = [d for t, d in pk if st["t0"] <= t <= st["t1"] + 2.0]
        ok = [d for d in mine if isinstance(d.get("res"), int) and d["res"] >= 0]
        bad = [d for d in mine if d not in ok]
        be = sorted(d["be"] for d in bad if isinstance(d.get("be"), int))
        out.append(f"{st['gain']:g} | {len(ok)} | {len(bad)} | {be[len(be) // 2] if be else '-'}")
    return out


def round_seconds(fs=fsk.FS, variants=None):
    x, _ = rounds(1, fs, variants)
    return (len(x) - int(LEAD_S * fs)) / fs


# ---- what to expect: our demodulator through a model of the IC-7100's transmit path ----
def sim(n_rounds=2, ch="ic7100", fs=fsk.FS):
    """each variant through the model channel, read by modem.fsk's demodulator -> {variant: (ok, n)}"""
    path = {"ic7100": fsk.Channel(pre_emph=300.0, hpf=300.0, lpf=3000.0),
            "flat": None}[ch]
    out = {}
    for vi, v in enumerate(VARIANTS):
        if v.comp == "reserved":
            continue
        got = sent = 0
        for r in range(n_rounds):
            x = block_audio(vi, r * PER_BLOCK, fs)
            y = path.apply(x, fs) if path else x
            dem = fsk.Demodulator("F" if v.comp == "iqF" else "D", v.baud, fs, payload=NB)
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
    w.add_argument("--run", type=int, choices=(1, 2), default=1,
                   help="1: variants 0-7 (the model pre-equalisers); 2: the control and 8-12 (needs --eq)")
    w.add_argument("--eq", help="run 2: the pre-equaliser json from tools/uv380_dmrburst.py fit")
    s = sub.add_parser("sim")
    s.add_argument("--channel", choices=("ic7100", "flat"), default="ic7100")
    r = sub.add_parser("report")
    r.add_argument("log")
    r.add_argument("--sweep", action="store_true", help="a sensitivity sweep's log (fsk24rx_sweep)")
    sub.add_parser("sweep-table", help="the sweep module's slot table (c/k6/mod/fsk24rx_sweep.c)")
    rs = sub.add_parser("report-steps", help="the pager's K6 PKT lines through a pluto_bench steps ladder")
    rs.add_argument("log")
    rs.add_argument("steps", help="pluto_bench steps --out json")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        from .k6 import _write, cw
        vs = None
        if a.run == 2:
            if not a.eq:
                raise SystemExit("--run 2 needs --eq EQ.json (uv380_dmrburst.py fit --out)")
            import json
            with open(a.eq) as f:
                e = json.load(f)
            EQ.clear()
            EQ.update(taps=e["taps"], centre=e["centre"])
            vs = list(RUN2)
        x, segs = rounds(a.rounds, variants=vs)
        if a.cw_id:
            x = np.concatenate([x, np.zeros(int(0.3 * fsk.FS)), cw(a.cw_id, fsk.FS, level=0.3)])
        _write(a.out, x, fsk.FS)
        print(f"{a.out}: {len(x) / fsk.FS:.1f} s, {a.rounds} rounds of {round_seconds(variants=vs):.1f} s, "
              f"{len(segs) * PER_BLOCK} packets, then {a.cw_id or 'no ID'}")
    elif a.cmd == "sim":
        for vi, (got, sent) in sim(ch=a.channel).items():
            if RUN2[1] <= vi < RUN3[0]:
                continue
            v = VARIANTS[vi]
            print(f"v{vi} {v.baud} {v.comp:6s} {v.level:.2f}: {got}/{sent}")
    elif a.cmd == "report-steps":
        import json
        with open(a.log, errors="replace") as f, open(a.steps) as g:
            print("\n".join(steps_report(f.read().splitlines(), json.load(g))))
    elif a.cmd == "sweep-table":
        print("\n".join(sweep_table()))
    else:
        with open(a.log, errors="replace") as f:
            ls = f.read().splitlines()
        print("\n".join(sweep_report(ls) if a.sweep else report(ls)))


if __name__ == "__main__":
    sys.exit(main())
