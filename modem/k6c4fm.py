"""C4FM (YSF) and DMR-shaped 4800-baud 4FSK from the K6's own FD6818: the frames c/k6/mod/c4probe.c
sends, a model of what goes on air, and the analysis of a Pluto discriminator capture
(docs/k6-c4fm.md).

The FD6818's FSK modem is 2-level. Run at 4800 baud (REG_72 = 0xC195) each bit is one symbol's
sign, so the chip alone sends +3/-3. C4FM's dibit is (sign, size): 01 +3, 00 +1, 10 -1, 11 -3, so
the second bit of each dibit says outer (1) or inner (0). c4probe.c puts the signs in the FIFO and
makes the size by rewriting REG_70's FSK gain (tone 2, bits 6:0) on the symbol edges, timed on
TIM17. Whether the gain takes effect mid-packet is the question the probe packets answer.

  python3 -m modem.k6c4fm header OUT.h      the module's tables (c4probe_frames.h)
  python3 -m modem.k6c4fm plan              the packets, their air time, the YSF frames decoded back
  python3 -m modem.k6c4fm sim [--delay-us D] [--noise-hz N] [--latched]
                                            the whole run as the Pluto would hear it, analysed
  python3 -m modem.k6c4fm analyze CAP.wav   a Pluto capture (pluto_bench.py rx OUT.wav): packet by
                                            packet, deviation, gain steps, droop, DMR syncs, YSF

All bit arrays are numpy int8 arrays of 0/1, most significant bit first.
SPDX-License-Identifier: MIT
"""
import argparse
import sys

import numpy as np

from . import ysf

BAUD = 4800
REG72 = 0xC195                     # 4800 x 10.32444
LEAD_SYMS = 16 * 8 + 32            # the chip's 16 preamble bytes and its 4-byte sync word
PKT_SYMS = 480                     # one payload: 60 bytes, one YSF frame's worth of symbols
PKT_BYTES = PKT_SYMS // 8
GAP_S = 0.022                      # carrier between packets (FIFO fill, the 20 ms settle)
GAIN = 96                          # REG_70 tone-2 gain for the outer symbols (the old PHY D's 0x60)
STEPS = (96, 72, 48, 32, 16)       # P1's REG_70 gains, P2's REG_40 deviation as the same shares
STEP_EVERY = 96                    # symbols a step
INNER = 32                         # REG_70 gain for the inner symbols: a third of GAIN if linear
OFFSETS = tuple(range(-8, 24))     # the YSF packets' size-write timing, quarter symbols from the aim
BS_DATA = 0xDFF57D75DF5D           # DMR BS-sourced data sync: +3/-3 symbols only
TEXT = "AG7EW K6 C4FM"

# the packets in send order: (name, what it tests)
PROBES = (("P0", "PN9 signs, gain 96: 4800-baud deviation and eye"),
          ("P1", "REG_70 gain steps 96/72/48/32/16 every 96 symbols: live? linear? where?"),
          ("P2", "REG_40 deviation at the same shares: live?"),
          ("P3", "240 zeros then 240 ones: droop (TX high-pass)"),
          ("P4", "P3 with REG_2B TX filter bits 2:0 set"),
          ("P5", "DMR BS data sync every 144 symbols (outer only)"))


# ---------------------------------------------------------------- what the module sends
def pn_signs(n=PKT_SYMS):
    return ysf._pn9(n, seed=0x1FF)


def run_signs():
    return np.concatenate([np.zeros(PKT_SYMS // 2, np.int8), np.ones(PKT_SYMS // 2, np.int8)])


def dmr_sync_signs(word=BS_DATA):
    """the 24 dibit MSBs of a 48-bit DMR sync word (1 = negative)"""
    bits = [(word >> (47 - i)) & 1 for i in range(48)]
    return np.array(bits[0::2], np.int8)


def dmr_dibits():
    """P5: three 144-symbol DMR slots (54 info, 24 sync, 54 info, 12 CACH) of outer-only
    symbols, the info PN9, then 48 more PN9 symbols: as dibits (msb, lsb=1)"""
    pn = ysf._pn9(PKT_SYMS, seed=0x0F3)
    msb = pn.copy()
    for j in range(3):
        msb[144 * j + 54:144 * j + 78] = dmr_sync_signs()
    return np.stack([msb, np.ones(PKT_SYMS, np.int8)], 1).reshape(-1)


def ysf_frames():
    """the YSF packets' frames: header, data FN 0 (callsigns), FN 1 (text), terminator, as
    960-bit arrays"""
    data = ysf.tx_data(2, ft=1, text=TEXT)[24:]           # drop the 0x77 preamble
    assert len(data) == 4 * ysf.FRAME_BYTES
    return [ysf.bytes_to_bits(data[i * 120:(i + 1) * 120]) for i in range(4)]


def split(dibits):
    """960 bits -> (sign bits as the chip sends them, sizes): chip bit 1 = +deviation (the K6 FSK
    spec's assumption; the analysis takes either polarity), size 1 = outer"""
    d = np.asarray(dibits, np.int8).reshape(-1, 2)
    return (1 - d[:, 0]).astype(np.int8), d[:, 1].astype(np.int8)


YSF_OFF = 7                         # c4ysf.c's C4_OFF: run 1's best write timing (quarter symbols)
YSF_PRE = (120, 64, 32)             # c4ysf.c's B1..B3: FIFO words written before the enable
LOCK_NCAL = 17                      # c4lock.c (run 3): calibration packets (64 PN9 signs each)
LOCK_CAL_SYMS = 64
LOCK_A = tuple(range(-6, 7))        # its single-frame packets' size timing, quarter symbols
LOCK_B = (-4, -2, 0, 2, 4)          # its whole-transmission packets'
LOCK_LAG4 = 8                       # its C4_LAG4
PAD_SYMS = 16                       # c4lock's outer alternation after the frames (FIFO word 0x5555)


def packets(plan="probe"):
    """every packet in send order: dicts name, chip (the FIFO bits: 480 a frame), size (1 outer,
    0 inner, None = all outer), mode ('none', 'gain', 'dev', 'size'), off (quarter symbols),
    frames (YSF frame indices, one per 480 symbols), r2b (P4), pre (FIFO words first).
    plan 'probe': c4probe.c's (run 1); 'ysf': c4ysf.c's (run 2); 'lock': c4lock.c's (run 3)"""
    fr = ysf_frames()
    if plan == "lock":
        pad = np.tile(np.array([0, 1], np.int8), PAD_SYMS // 2)
        one = np.ones(PAD_SYMS, np.int8)
        out = [dict(name=f"C{i:02d}", chip=pn_signs()[:LOCK_CAL_SYMS], mode="none")
               for i in range(LOCK_NCAL)]
        for i, q in enumerate(LOCK_A):
            c, sz = split(fr[i % 4])
            out.append(dict(name=f"A{i:02d}", chip=np.concatenate([c, pad]),
                            size=np.concatenate([sz, one]), mode="size", off=q, frames=[i % 4]))
        c, sz = split(np.concatenate(fr))
        for j, q in enumerate(LOCK_B):
            out.append(dict(name=f"B{j}", chip=np.concatenate([c, pad]), size=np.concatenate([sz, one]),
                            mode="size", off=q, frames=[0, 1, 2, 3]))
        return out
    if plan == "ysf":
        out = []
        for i in range(4):
            c, sz = split(fr[i])
            out.append(dict(name=f"A{i}", chip=c, size=sz, mode="size", off=YSF_OFF, frames=[i]))
        c, sz = split(np.concatenate(fr))
        for j, pre in enumerate(YSF_PRE):
            out.append(dict(name=f"B{j + 1}", chip=c, size=sz, mode="size", off=YSF_OFF,
                            frames=[0, 1, 2, 3], pre=pre))
        return out
    out = [dict(name="P0", chip=pn_signs(), mode="none"),
           dict(name="P1", chip=pn_signs(), mode="gain"),
           dict(name="P2", chip=pn_signs(), mode="dev"),
           dict(name="P3", chip=run_signs(), mode="none"),
           dict(name="P4", chip=run_signs(), mode="none", r2b=True)]
    c, _ = split(dmr_dibits())
    out.append(dict(name="P5", chip=c, mode="none"))
    for i, off in enumerate(OFFSETS):
        c, s = split(fr[i % 4])
        out.append(dict(name=f"Y{i:02d}", chip=c, size=s, mode="size", off=off, frames=[i % 4]))
    return out


def fifo_bytes(chip):
    return ysf.bits_to_bytes(chip)


def header(path):
    """c4probe_frames.h: the FIFO bytes (MSB first on air; the module pairs them low byte first)"""
    fr = ysf_frames()
    pk = packets()
    lines = ["/* c4probe_frames.h: written by python3 -m modem.k6c4fm header; do not edit.",
             f"   The probe's payloads (60 bytes = 480 symbols each, chip bit 1 = +deviation) and the",
             f"   YSF frames' signs and sizes (1 = outer): header, data FN 0, FN 1 ('{TEXT}'),",
             "   terminator. SPDX-License-Identifier: MIT */",
             f"#define C4_PKT_BYTES {PKT_BYTES}u",
             f"#define C4_NFRAMES 4u"]

    def arr(name, b):
        lines.append(f"static const uint8_t {name}[] = {{")
        for i in range(0, len(b), 15):
            lines.append("    " + ", ".join(f"0x{x:02x}" for x in b[i:i + 15]) + ",")
        lines.append("};")
    arr("C4_PN", fifo_bytes(pk[0]["chip"]))
    arr("C4_DMR", fifo_bytes(pk[5]["chip"]))
    sg, sz = [], []
    for f in fr:
        c, s = split(f)
        sg.append(fifo_bytes(c))
        sz.append(fifo_bytes(s))
    arr("C4_YSF_SIGN", b"".join(sg))
    arr("C4_YSF_SIZE", b"".join(sz))
    open(path, "w").write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- a model of the air
def packet_levels(p, delay_q=0.0, latched=False, inner_share=None, dev_gain=None):
    """one packet's symbol levels in units of the outer level, at 4 samples a symbol (quarter
    symbols): the lead (alternating, outer), then the payload with the size changes landing
    delay_q quarter symbols late against where the module aimed them (p['off'] already in
    them). latched: the gain is read once per packet (the changes do nothing)"""
    q = 4
    inner_share = INNER / GAIN if inner_share is None else inner_share
    n = LEAD_SYMS + len(p["chip"])
    chip = np.concatenate([np.arange(LEAD_SYMS) % 2, p["chip"]]).astype(float)
    sign = np.repeat(2 * chip - 1, q)
    size = np.ones(n * q)
    if not latched:
        start = LEAD_SYMS * q
        if p["mode"] == "size":
            s = np.asarray(p["size"])
            want = np.where(s == 1, 1.0, inner_share)
            sh = int(round(p["off"] + delay_q))
            seg = np.repeat(want, q)
            a = start + sh
            size[max(a, 0):a + len(seg)] = seg[max(-a, 0):][:n * q - max(a, 0)]
            if a + len(seg) < n * q:
                size[a + len(seg):] = 1.0
        elif p["mode"] in ("gain", "dev"):
            shares = [g / GAIN for g in STEPS]
            if p["mode"] == "dev" and dev_gain is not None:
                shares = dev_gain
            for j, sh_ in enumerate(shares):
                a = start + (j * STEP_EVERY) * q + int(round(delay_q))
                size[a:] = sh_
    return sign * size


def simulate(delay_us=0.0, outer_hz=1944.0 * 1.39, noise_hz=0.0, latched=False, fs=48000, seed=1,
             lpf_hz=3500.0, droop_tau=None, dev_live=True, plan="probe"):
    """the Pluto's discriminator (Hz) for the whole run: lead, packets with gaps, no ID.
    delay_us: where the size changes land, late against the module's aim (chip latency)"""
    from scipy import signal
    rng = np.random.default_rng(seed)
    q = 4
    out = [np.zeros(int(0.3 * fs))]
    for p in packets(plan):
        lv = packet_levels(p, delay_q=delay_us * BAUD * q / 1e6, latched=latched,
                           dev_gain=None if dev_live else [1.0] * len(STEPS))
        x = np.interp(np.arange(int(len(lv) / (BAUD * q) * fs)) * BAUD * q / fs, np.arange(len(lv)), lv)
        out.append(x * outer_hz)
        out.append(np.zeros(int(GAP_S * fs)))
    d = np.concatenate(out)
    # the deviation steps are sharp in the model; round them as the chip's filter might
    b, a = signal.butter(2, lpf_hz / (fs / 2))
    d = signal.lfilter(b, a, d)
    if droop_tau:
        b, a = signal.butter(1, 1 / (2 * np.pi * droop_tau) / (fs / 2), "high")
        d = signal.lfilter(b, a, d)
    return d + rng.normal(0, noise_hz, len(d)) if noise_hz else d


# ---------------------------------------------------------------- analysis
def segments(disc, fs=48000, quiet_hz=None, pkt_syms=PKT_SYMS):
    """packet starts: modulation (a moving standard deviation over two symbols above quiet_hz)
    after at least 8 ms of steady carrier (below it), lasting through the chip's lead (25 ms of
    its 33 ms of outer symbols); each as (start, end) samples, a packet's length from the start.
    A moving deviation, not a level: the K6's carrier sits off the capture's centre, and the
    noise before and after the key-up is full scale. quiet_hz: default 4 x the steadiest stretch's.
    (The MCW ID's dahs qualify too: analyze matches bursts to packets by their signs.)"""
    d = np.asarray(disc, float)
    w = max(2, int(fs * 2 / BAUD))
    k = np.ones(w) / w
    m = np.convolve(d, k, "same")
    sd = np.sqrt(np.maximum(np.convolve(d * d, k, "same") - m * m, 0))
    if quiet_hz is None:
        quiet_hz = max(300.0, 4 * float(np.percentile(sd, 2)))
    on = sd > quiet_hz
    n = int((LEAD_SYMS + pkt_syms) / BAUD * fs)
    quiet, lead = int(0.008 * fs), int(0.025 * fs)
    c = np.concatenate([[0], np.cumsum(on)])
    segs, i = [], quiet
    while i < len(on) - lead:
        if on[i] and not on[i - 1] and c[i] - c[i - quiet] == 0 and c[i + lead] - c[i] >= lead - w:
            a = max(0, i - int(fs / BAUD))
            segs.append((a, min(len(on), i + n + int(2 * fs / BAUD))))
            i += n
        else:
            i += 1
    return segs


def symbols(disc, a, b, fs=48000):
    """a packet's symbols: boxcar matched filter (the chip's NRZ), the sampling phase that opens
    the eye most; -> (values at 4800 a second, the first one's sample position)"""
    sps = fs / BAUD
    x = disc[a:b] - np.mean(disc[a:b])
    w = int(round(sps))
    y = np.convolve(x, np.ones(w) / w, "same")
    best = None
    for ph in np.linspace(0, sps, 20, endpoint=False):
        idx = (ph + np.arange(int((len(y) - ph) / sps)) * sps).astype(int)
        v = y[idx]
        score = float(np.mean(v * v))          # the eye is widest where the power is largest
        if best is None or score > best[0]:
            best = (score, ph, v)
    return best[2], a + best[1]


def locate(v, chip):
    """the payload's start in v and the polarity, by the known signs (+-1 correlation)"""
    want = 2.0 * np.asarray(chip, float) - 1
    s = np.sign(v)
    best = (-1, 0, 1)
    for k in range(max(1, len(v) - len(want) + 1)):
        seg = s[k:k + len(want)]
        if len(seg) < len(want):
            break
        c = float(seg @ want) / len(want)
        if abs(c) > best[0]:
            best = (abs(c), k, 1 if c > 0 else -1)
    return best[1], best[2], best[0]


def analyze(disc, fs=48000, plan="probe"):
    """the whole run -> (report lines, results by packet name)"""
    pk = packets(plan)
    segs = segments(disc, fs, pkt_syms=min(min(len(p["chip"]) for p in pk), PKT_SYMS))
    lines = [f"{len(disc) / fs:.1f} s of discriminator, {len(segs)} packet-length bursts "
             f"(want {len(pk)})"]
    res = {}
    if len(segs) < len(pk):
        lines.append("fewer bursts than packets: matching them in order from the first anyway")
    si = 0
    for p in pk:
        while si < len(segs):
            a, b = segs[si]
            si += 1
            b = min(len(disc), a + int((LEAD_SYMS + len(p["chip"]) + 3) / BAUD * fs))
            v, _ = symbols(disc, a, b, fs)
            k, pol, corr = locate(v, p["chip"])
            if corr >= 0.6:
                break
        else:
            lines.append(f"{p['name']}: not found")
            continue
        nsym = len(p["chip"])
        pay = pol * v[k:k + nsym]
        r = {"t": a / fs, "ms": (b - a) / fs * 1e3, "corr": corr, "pol": pol, "start": k}
        if len(pay) < nsym:
            lines.append(f"{p['name']}: short ({len(pay)} symbols)")
            res[p["name"]] = r
            continue
        want = 2.0 * p["chip"] - 1
        r["sign_err"] = int(np.sum(np.sign(pay) != want))
        mag = np.abs(pay)
        if p["mode"] in ("gain", "dev"):
            lv = [float(np.median(mag[j * STEP_EVERY + 8:(j + 1) * STEP_EVERY - 8])) for j in range(len(STEPS))]
            r["steps"] = lv
            r["shares"] = [x / lv[0] for x in lv]
            # where each change lands: the first symbol below halfway, fractional by its own level
            land = []
            for j in range(1, len(STEPS)):
                hi, lo = lv[j - 1], lv[j]
                if hi - lo < 0.1 * lv[0]:
                    land.append(None)
                    continue
                n0 = j * STEP_EVERY
                w = mag[n0 - 6:n0 + 6]
                frac = np.clip((hi - w) / (hi - lo), 0, 1)
                land.append(float(n0 - 6 + np.sum(1 - frac) - n0))
            r["land"] = land
            ok = all(x is not None for x in land)
            lines.append(f"{p['name']}: levels " + " ".join(f"{x:.0f}" for x in lv) + " Hz, shares "
                         + " ".join(f"{x:.2f}" for x in r["shares"])
                         + (f"; changes land {np.mean(land):+.2f} symbols from the aim" if ok
                            else "; LATCHED (the level never moved mid-packet)" if all(x is None for x in land)
                            else "; some steps unseen"))
        elif p["name"] in ("P3", "P4"):
            h = PKT_SYMS // 2
            r["droop"] = [float(mag[4] and mag[h - 4] / mag[4]), float(mag[h + 4] and mag[-4] / mag[h + 4])]
            lines.append(f"{p['name']}: run start {mag[4]:.0f} Hz, end {mag[h - 4]:.0f} Hz "
                         f"(kept {r['droop'][0]:.0%} / {r['droop'][1]:.0%})")
        elif p["name"] == "P5":
            pat = 2.0 * (1 - dmr_sync_signs()) - 1          # chip bit 1 = +: msb 0 = +
            hits = []
            for j in range(len(pay) - 24):
                c = float(np.sign(pay[j:j + 24]) @ pat) / 24
                if c >= 22 / 24:
                    hits.append(j)
            r["dmr_syncs"] = hits
            r["outer_hz"] = float(np.median(mag))
            lines.append(f"{p['name']}: DMR syncs at {hits} (want 54 198 342), outer {r['outer_hz']:.0f} Hz")
        elif p["mode"] == "size" and len(p["frames"]) == 1:
            r.update(ysf_packet(pay[:PKT_SYMS], p["frames"][0]))
            r["land"] = size_land(pay, p["size"])
            lines.append(f"{p['name']} off {p['off']:+d}/4: " + r["text"] + f", sizes land {r['land']:+.2f}")
        elif p["mode"] == "size":
            subs = [ysf_packet(pay[j * PKT_SYMS:(j + 1) * PKT_SYMS], f) for j, f in enumerate(p["frames"])]
            r["frames"] = subs
            r["sym_err"] = sum(x["sym_err"] for x in subs)
            r["fich_n"] = sum(x["fich"] for x in subs)
            r["land"] = size_land(pay, p["size"])
            r["land_frames"] = [size_land(pay[j * PKT_SYMS:(j + 1) * PKT_SYMS], p["size"][j * PKT_SYMS:(j + 1) * PKT_SYMS])
                                for j in range(len(p["frames"]))]
            what = f"{p.get('pre')} words first" if "pre" in p else f"off {p['off']:+d}/4"
            lines.append(f"{p['name']} ({what}): {r['fich_n']} of {len(subs)} frames "
                         f"with a good FICH, {r['sym_err']} of {nsym} symbols wrong; sizes land "
                         + " ".join(f"{x:+.2f}" for x in r["land_frames"]) + " symbols, frame by frame")
            for j, x in enumerate(subs):
                lines.append(f"  frame {j}: " + x["text"])
        else:
            r["outer_hz"] = float(np.median(mag))
            r["eye"] = float(np.std(mag) / np.mean(mag))
            lines.append(f"{p['name']}: outer {r['outer_hz']:.0f} Hz, spread {r['eye']:.0%}, "
                         f"{r['sign_err']} sign errors, {r['ms']:.1f} ms burst (want "
                         f"{(LEAD_SYMS + nsym) / BAUD * 1e3:.1f})")
        res[p["name"]] = r
    ys = [(n, r) for n, r in res.items() if n[0] in "YA" and "sym_err" in r]
    if ys:
        best = min(ys, key=lambda x: x[1]["sym_err"])
        good = [n for n, r in ys if r.get("fich")]
        lines.append(f"YSF: {len(good)} of {len(ys)} frames with a good FICH; best timing {best[0]} "
                     f"({best[1]['sym_err']} of {PKT_SYMS} symbols wrong)")
    cs = [r for n, r in res.items() if n[0] in "CAB" and "t" in r]
    if plan == "lock" and len(cs) > 2:
        # the packets' starts against one 4800-baud grid (the capture's clock): the chip's grid
        # runs on through the key-up, and the module's enables follow it
        t = np.array([r["t"] + r["start"] / BAUD for r in cs])
        ph = ((t - t[0]) * BAUD + 0.5) % 1 - 0.5
        lines.append("packet starts against a 4800-baud grid (symbols): "
                     + " ".join(f"{x:+.2f}" for x in ph))
        res["grid"] = [float(x) for x in ph]
    if plan in ("ysf", "lock"):
        # as a plain C4FM receiver sees it: RRC filter, sync search, frames in 100 ms runs
        fr = ysf.demod(disc - np.median(disc), fs) + ysf.demod(np.median(disc) - disc, fs)
        rn = ysf.runs(fr)
        lines.append(f"plain YSF receiver (modem.ysf.demod, either polarity): {len(fr)} frames in "
                     f"{len(rn)} runs, {sum(f['fich'] is not None for f in fr)} with a good FICH")
        res["plain"] = {"frames": len(fr), "runs": [len(x) for x in rn],
                        "fich": sum(f["fich"] is not None for f in fr)}
    return lines, res


def size_land(pay, size):
    """where the size changes land against the symbols, in symbols (+ late), from the symbols
    beside each lone change: late by d, the first symbol after it carries d of the old size; early
    by d, the last one before it carries d of the new"""
    mag = np.abs(np.asarray(pay, float))
    s = np.asarray(size)[:len(mag)]
    if not np.any(s == 0) or not np.any(s == 1):
        return 0.0
    hi, lo = float(np.median(mag[s == 1])), float(np.median(mag[s == 0]))
    if hi - lo < 1e-9:
        return 0.0
    est = []
    for k in range(2, len(s) - 2):
        if s[k] == s[k - 1] or s[k - 2] != s[k - 1] or s[k + 1] != s[k]:
            continue
        new, old = (hi, lo) if s[k] else (lo, hi)
        late = (mag[k] - new) / (old - new)
        early = (mag[k - 1] - old) / (new - old)
        est.append(np.clip(late, 0, 1) - np.clip(early, 0, 1))
    return float(np.median(est)) if est else 0.0


def ysf_packet(pay, frame):
    """one YSF frame's symbols (polarity fixed, Hz) -> symbol errors, levels, decode"""
    fr = ysf_frames()[frame]
    lv_want = ysf.bits_to_levels(fr)
    g = float(pay[:20] @ ysf.SYNC_LEVELS) / float(ysf.SYNC_LEVELS @ ysf.SYNC_LEVELS)
    if g <= 0:
        return {"sym_err": PKT_SYMS, "fich": False, "text": "no sync"}
    lv = pay / g
    q = np.clip(np.round((lv + 3) / 2) * 2 - 3, -3, 3)
    err = int(np.sum(q != lv_want))
    means = {int(L): float(np.mean(pay[lv_want == L])) for L in (-3, -1, 1, 3)}
    dec = ysf.decode_frame(ysf.llr_from_levels(lv))
    fich = dec["fich"] is not None
    what = ""
    if fich:
        fi = dec["fich"]
        what = f"FICH {ysf.FI_NAMES[fi.fi]} fn {fi.fn}"
        if "csd" in dec and dec["csd"]:
            what += f" csd {dec['csd']!r}"[:60]
        if "dch" in dec and dec["dch"]:
            what += f" dch {dec['dch']!r}"[:60]
    text = (f"{err} symbols wrong, levels " + " ".join(f"{means[L]:+.0f}" for L in (3, 1, -1, -3))
            + (f", {what}" if fich else ", FICH bad"))
    return {"sym_err": err, "levels": means, "fich": fich, "decode": dec, "text": text}


def read_wav(path):
    return ysf.read_wav_disc(path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m modem.k6c4fm", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("header")
    h.add_argument("out")
    pl = sub.add_parser("plan")
    pl.add_argument("--plan", choices=("probe", "ysf", "lock"), default="probe")
    s = sub.add_parser("sim")
    s.add_argument("--plan", choices=("probe", "ysf", "lock"), default="probe")
    s.add_argument("--delay-us", type=float, default=0.0)
    s.add_argument("--noise-hz", type=float, default=0.0)
    s.add_argument("--latched", action="store_true")
    an = sub.add_parser("analyze")
    an.add_argument("cap")
    an.add_argument("--plan", choices=("probe", "ysf", "lock"), default="probe",
                    help="probe: c4probe.c (run 1); ysf: c4ysf.c (run 2); lock: c4lock.c (run 3)")
    a = ap.parse_args(argv)
    if a.cmd == "header":
        header(a.out)
        print(f"wrote {a.out}")
    elif a.cmd == "plan" and a.plan in ("ysf", "lock"):
        for p in packets(a.plan):
            print(f"{p['name']}: frames {p.get('frames')}, {len(p['chip'])} symbols"
                  + (f", sizes {p['off']:+d}/4" if "off" in p else "")
                  + (f", {p['pre']} FIFO words first" if "pre" in p else ""))
    elif a.cmd == "plan":
        pk = packets()
        t = 0.3 + len(pk) * ((LEAD_SYMS + PKT_SYMS) / BAUD + GAP_S)
        for n, what in PROBES:
            print(f"{n}: {what}")
        print(f"Y00..Y{len(OFFSETS) - 1:02d}: YSF frames (header, data FN 0, FN 1, terminator in turn), "
              f"size writes at {OFFSETS[0]:+d}..{OFFSETS[-1]:+d} quarter symbols from the aim")
        print(f"{len(pk)} packets, about {t:.1f} s keyed before the CW ID")
        for i, f in enumerate(ysf_frames()):
            d = ysf.decode_frame(1.0 - 2.0 * f)
            print(f"frame {i}: {d['fich']!r} {d.get('csd') or d.get('dch')!r}"[:110])
    elif a.cmd == "sim":
        d = simulate(a.delay_us, noise_hz=a.noise_hz, latched=a.latched, plan=a.plan)
        print("\n".join(analyze(d, plan=a.plan)[0]))
    elif a.cmd == "analyze":
        print("\n".join(analyze(read_wav(a.cap), plan=a.plan)[0]))


if __name__ == "__main__":
    sys.exit(main())
