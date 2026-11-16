"""Plain 2FSK out of the UV-380's HR_C6000 physical layer mode (firmware/uv380-openrtx/mod/c6phytx.c):
the reference for c/common/c6phy_fsk.h (a test checks the two byte for byte), what the K6 should
see (sim), and the Pluto capture's decode on the bench.

  python3 -m modem.c6phy plan                       packets a 4.6 s key-up carries, per rate
  python3 -m modem.c6phy sim [--baud 2400]          the chip's 4FSK (RRC, +-1944 Hz) of the slots
                                                    through a flat FM discriminator, read by
                                                    modem.fsk's demodulator as the K6's D profile
  python3 -m modem.c6phy decode CAP.cf32            a Pluto capture (tools/pluto_bench.py rx, tuned
                                                    50 kHz up, --hz 440900000, so its RX LO stays off
                                                    the K6's channel): the
         [--baud 2400]                              packets (variant, sequence, CRC, PN9 bits
                                                    wrong, which polarity), the carrier offset and
                                                    the outer deviation, the gaps in the carrier

In the physical layer mode the chip sends TX RAM as DMR's 4FSK (4800 symbols a second, a dibit a
symbol: 01 +3, 00 +1, 10 -1, 11 -3, outer +-1944 Hz, RRC alpha 0.2), 36 bytes a 30 ms slot.
Each bit as two equal outer symbols is 2400 baud plain FSK (bit 1 = nibble 0x5, bit 0 = 0xF);
four is 1200. The stream is fsk24rx's: pager.Air's framing around fsk24's 64-byte test packets.
Polarity 0 sends bit 1 as +3, 1 as -3, 2 alternates packet by packet; a packet's variant
(fsk24.VARIANTS 20-23) says its rate and polarity.
SPDX-License-Identifier: MIT
"""
import argparse
import json
import os
import sys

import numpy as np

from . import fsk, fsk24, pager

SLOT = 36
SLOT_S = 0.030
SYM_RATE = 4800
OUTER_HZ = 1944.0
PRE = 16
FRAME = PRE + len(pager.SYNC) + fsk24.NB
VAR = {2400: 20, 1200: 22}
LEVEL = {0b01: 3, 0b00: 1, 0b10: -1, 0b11: -3}
TX_MS, TAIL_MS, SLOT_MS = 4600, 350, 30
BENCH_HZ = 440_850_000
SETTLE_MS = 80


def bytes_ms(ms, baud):
    return (ms * baud + 7999) // 8000


def fit(ms, baud):
    """packets that fit in ms of slots (c6phy_fsk.h cf_fit)"""
    b, over = ms * baud // 8000, bytes_ms(150, baud) + bytes_ms(50, baud) + 4
    return (b - over) // FRAME if b > over else 0


def key_up_packets(baud, tx_ms=TX_MS):
    """packets c6phytx plans in one key-up: the tone's time less the settle, the tail and 4 slots"""
    return fit(tx_ms - SETTLE_MS - TAIL_MS - 4 * SLOT_MS, baud)


def pol_of(pol, k):
    return (k & 1) if pol == 2 else pol


def stream(baud, pol, npk, seq0=0, extra_bytes=0):
    """the data bytes as they go on air (inverted for a polarity 1 packet), through npk packets
    and extra_bytes of fill: cf_next's sequence"""
    lead, extra = bytes_ms(150, baud), bytes_ms(50, baud)
    out = bytearray()
    out += bytes([0x55 ^ (0xFF if pol_of(pol, 0) else 0)]) * lead
    for k in range(npk):
        inv = 0xFF if pol_of(pol, k) else 0
        pre = PRE + (extra if k == 1 else 0)
        frame = bytes([0x55]) * pre + pager.SYNC + fsk24.packet(VAR[baud] + pol_of(pol, k), (seq0 + k) & 0xFF)
        out += bytes(b ^ inv for b in frame)
    last = 0xFF if pol_of(pol, max(npk - 1, 0)) else 0
    out += bytes([0x55 ^ last]) * extra_bytes
    return bytes(out)


def expand(data, baud):
    """data bytes -> TX RAM bytes (2400: a nibble 0x5 / 0xF a bit; 1200: a byte 0x55 / 0xFF)"""
    bits = np.unpackbits(np.frombuffer(bytes(data), np.uint8))
    if baud == 1200:
        return bytes(np.where(bits > 0, 0x55, 0xFF).astype(np.uint8))
    nib = np.where(bits > 0, 0x5, 0xF).astype(np.uint8)
    return bytes((nib[0::2] << 4) | nib[1::2])


def slots(baud, pol, npk, nslots, seq0=0):
    """the first nslots slots' 36 TX RAM bytes (cf_slot, slot after slot)"""
    per = 4 if baud == 2400 else 8
    need = nslots * SLOT
    nb = len(stream(baud, pol, npk, seq0))
    raw = expand(stream(baud, pol, npk, seq0, max(0, -(-need // per) - nb)), baud)
    return [raw[i * SLOT:(i + 1) * SLOT] for i in range(nslots)]


def fill(baud, pol):
    r = expand(bytes([0x55 ^ (0xFF if pol == 1 else 0)]), baud)
    return (r * (SLOT // len(r) + 1))[:SLOT]


def symbols(raw):
    """TX RAM bytes -> symbol levels (+-1, +-3), MSB first"""
    b = np.frombuffer(bytes(raw), np.uint8)
    d = np.stack([(b >> 6) & 3, (b >> 4) & 3, (b >> 2) & 3, b & 3], 1).ravel()
    lut = np.array([LEVEL[i] for i in range(4)], float)
    return lut[d]


def freq_track(raw, fs=48_000, outer_hz=OUTER_HZ):
    """the chip's modulation of TX RAM bytes: RRC shaped symbols, Hz at fs (+-3 = +-outer_hz)"""
    from sim.k6if import dmr
    sym = symbols(raw)
    fsa = SYM_RATE * 10
    up = np.zeros(len(sym) * 10)
    up[::10] = sym * 10
    x = np.convolve(up, dmr.rrc(10), "same") / 3.0 * outer_hz
    return fsk.resample(x, fsa, fs)


def read_packets(disc, fs, baud):
    """a flat discriminator (Hz) -> [(t s, variant, seq, crc ok, inverted, PN9 bits wrong, amp)]"""
    d = fsk.Demodulator("D", baud, fsk.FS, payload=fsk24.NB)
    y = fsk.resample(np.asarray(disc, float), fs, fsk.FS)
    out = []
    pn = np.unpackbits(np.frombuffer(fsk24.pn9(fsk24.PN_N), np.uint8))
    for j, pl, inv, amp in d.demodulate(y):
        ok = fsk24.crc16(pl[:62]) == int.from_bytes(pl[62:64], "big")
        e = int(np.sum(np.unpackbits(np.frombuffer(pl[3:62], np.uint8)) != pn))
        out.append((j / fsk.FS, pl[1], pl[2], ok, inv, e, amp))
    return out


def sim(baud=2400, pol=2, noise_hz=0.0, seed=1):
    """one key-up's slots through a flat FM discriminator (+ white noise in Hz rms) -> packets
    read, packets sent"""
    n = key_up_packets(baud)
    per = 4 if baud == 2400 else 8
    nslots = -(-len(stream(baud, pol, n)) * per // SLOT) + 3
    raw = b"".join(slots(baud, pol, n, nslots))
    x = freq_track(raw)
    if noise_hz:
        x = x + np.random.default_rng(seed).normal(0, noise_hz, len(x))
    return read_packets(x, 48_000, baud), n


def gaps(iq, fs, floor_db=20.0, min_ms=0.5):
    """carrier gaps (s, ms) in a capture: the envelope below its median by floor_db"""
    p = np.abs(iq) ** 2
    w = max(1, int(fs * 2e-4))
    p = np.convolve(p, np.ones(w) / w, "same")
    on = p > np.median(p[p > np.percentile(p, 90) * 0.01]) * 10 ** (-floor_db / 10)
    idx = np.flatnonzero(on)
    if not len(idx):
        return []
    a, b = idx[0], idx[-1]
    off = ~on[a:b]
    out, i = [], 0
    while i < len(off):
        if off[i]:
            j = i
            while j < len(off) and off[j]:
                j += 1
            if (j - i) / fs * 1e3 >= min_ms:
                out.append(((a + i) / fs, (j - i) / fs * 1e3))
            i = j
        else:
            i += 1
    return out


def fm_demod(iq, fs, fs_out=48_000, bw_hz=8000.0):
    """complex IQ at fs -> discriminator in Hz at fs_out (a 255-tap low-pass first)"""
    from scipy import signal
    h = signal.firwin(255, bw_hz, fs=fs)
    x = signal.lfilter(h, 1.0, np.asarray(iq, np.complex64))
    d = np.angle(x[1:] * np.conj(x[:-1])) * fs / (2 * np.pi)
    return fsk.resample(np.concatenate([[0.0], d]), fs, fs_out)


def carrier(iq, fs, on):
    """the carrier's offset (Hz) in a capture: the spectrum's peak where the signal is up, then
    the mean discriminator about it (an FM signal centres on its carrier)"""
    seg = iq[on]
    n = 1 << int(np.log2(max(256, min(len(seg), 1 << 18))))
    spec = np.zeros(n)
    for i in range(0, len(seg) - n + 1, n):
        spec += np.abs(np.fft.fft(seg[i:i + n] * np.hanning(n))) ** 2
    f = np.fft.fftfreq(n, 1 / fs)
    sm = np.convolve(spec, np.ones(64) / 64, "same")     # an FM signal's band, not one line
    f0 = float(f[int(np.argmax(sm))])
    x = seg * np.exp(-2j * np.pi * f0 * np.arange(len(seg)) / fs)
    from scipy import signal
    x = signal.lfilter(signal.firwin(255, 8000.0, fs=fs), 1.0, x)
    d = np.angle(x[1:] * np.conj(x[:-1])) * fs / (2 * np.pi)
    return f0 + float(np.mean(d[255:])) if len(d) > 300 else f0


def read_capture(cap):
    meta = {}
    if os.path.exists(cap + ".json"):
        with open(cap + ".json") as f:
            meta = json.load(f)
    return np.fromfile(cap, np.complex64), meta


def capture_disc(iq, fs):
    """-> carrier offset, signal mask, discriminator at 48 kHz (offset removed)"""
    p = np.abs(iq) ** 2
    on = p > np.percentile(p, 99) * 0.01
    if not on.any():
        return None, on, None
    off = carrier(iq, fs, on)
    shift = np.exp(-2j * np.pi * off * np.arange(len(iq)) / fs).astype(np.complex64)
    return off, on, fm_demod(iq * shift, fs)


def payload_start_bits(baud, k):
    """the stream bit where packet k's 64 bytes start (stream()'s layout)"""
    lead, extra = bytes_ms(150, baud), bytes_ms(50, baud)
    sync = len(pager.SYNC)
    b = lead + sum(PRE + (extra if i == 1 else 0) + sync + fsk24.NB for i in range(k))
    return 8 * (b + PRE + (extra if k == 1 else 0) + sync)


def errors(cap, baud, seq0=None, pol=2):
    """where in the 30 ms slot the wrong bits of a capture's packets fall -> report lines. Each
    packet is matched to the one it is nearest (variant and sequence may be hit too), its wrong
    bits placed in the stream and taken modulo a slot's data bits (72 at 2400, 36 at 1200; the
    stream starts on a slot). The C6000's own 48-bit sync, if it writes one mid-slot as in a DMR
    burst, sits at TX RAM bits 132-179."""
    iq, meta = read_capture(cap)
    fs = meta.get("fs", 240_000)
    off, on, disc = capture_disc(iq, fs)
    if disc is None:
        return ["no signal"]
    seq0 = (0 if baud == 2400 else 100) if seq0 is None else seq0
    d = fsk.Demodulator("D", baud, fsk.FS, payload=fsk24.NB)
    y = fsk.resample(np.asarray(disc, float), 48_000, fsk.FS)
    per_slot = SLOT * 4 // (SYM_RATE // baud)
    raw_per = SLOT * 8 // per_slot
    hist = np.zeros(per_slot, int)
    npk = key_up_packets(baud)
    lines, used = [], 0
    for j, pl, inv, amp in d.demodulate(y):
        got = np.unpackbits(np.frombuffer(pl, np.uint8))
        best = None
        for k in range(npk):
            want = np.unpackbits(np.frombuffer(fsk24.packet(VAR[baud] + pol_of(pol, k), (seq0 + k) & 0xFF), np.uint8))
            e = np.flatnonzero(got != want)
            if best is None or len(e) < len(best[1]):
                best = (k, e)
        k, e = best
        used += 1
        pos = (payload_start_bits(baud, k) + e) % per_slot
        np.add.at(hist, pos, 1)
        lines.append(f"  packet k={k} inverted={int(inv)}: {len(e)} of 512 wrong, slot bits {sorted(set(pos.tolist()))}")
    lo, hi = 132 // raw_per, 179 // raw_per
    head = [f"{baud} baud: {used} packets, {per_slot} data bits a slot, the C6000's mid-slot sync "
            f"(TX RAM bits 132-179) would be data bits {lo}-{hi}"]
    tot = int(hist.sum())
    inside = int(hist[lo:hi + 1].sum())
    head.append(f"wrong bits {tot}, of them {inside} ({inside / max(tot, 1):.0%}) in data bits {lo}-{hi}")
    head.append("per slot bit: " + " ".join(f"{i}:{c}" for i, c in enumerate(hist) if c))
    return head + lines


def decode(cap, baud):
    """a Pluto capture -> report lines"""
    iq, meta = read_capture(cap)
    fs = meta.get("fs", 240_000)
    lines = []
    off, on, disc = capture_disc(iq, fs)
    if disc is None:
        return ["no signal"]
    hz = meta.get("hz", BENCH_HZ)
    lines.append(f"carrier {off + hz - BENCH_HZ:+.0f} Hz from {BENCH_HZ} (captured at {hz}: the RX LO off the "
                 f"K6's channel), on {on.mean():.0%} of the capture")
    pk = read_packets(disc, 48_000, baud)
    good = [x for x in pk if x[3]]
    lines.append(f"{baud} baud: {len(pk)} syncs, {len(good)} CRC right")
    for t, v, s, ok, inv, e, amp in pk:
        lines.append(f"  t={t:7.3f} s v={v} q={s} crc={'ok' if ok else 'bad'} inverted={int(inv)} "
                     f"pn9 wrong={e}/472 eye={amp:.0f} Hz")
    by_v = {}
    for x in good:
        by_v.setdefault(x[1], 0)
        by_v[x[1]] += 1
    if by_v:
        lines.append("CRC right by variant: " + ", ".join(f"v{v} {n}" for v, n in sorted(by_v.items())))
        up = {(x[1] % 2) ^ int(x[4]) for x in good}
        if len(up) == 1:
            lines.append("a +3 symbol (dibit 01) goes out " + ("below" if up.pop() else "above")
                         + " the carrier: the K6 reads polarity " + ("1 (v21/v23)" if good[0][1] % 2 else "0 (v20/v22)")
                         + " if its discriminator agrees with the Pluto's")
    # outer deviation: the top and bottom percentiles of the discriminator while the carrier is up
    on48 = on[::max(1, fs // 48_000)][:len(disc)]
    d_on = disc[:len(on48)][on48]
    if len(d_on):
        lo, hi = np.percentile(d_on, [3, 97])
        lines.append(f"deviation peaks (3rd-97th percentile) {lo:+.0f} / {hi:+.0f} Hz; DMR outer +-{OUTER_HZ:.0f} at the symbol centres, more between them (RRC on transmit only)")
    g = gaps(iq, fs)
    lines.append(f"carrier gaps over 0.5 ms: {len(g)}" + ("".join(f"; {t:.3f} s {ms:.1f} ms" for t, ms in g[:8])))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m modem.c6phy", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    s = sub.add_parser("sim")
    s.add_argument("--baud", type=int, choices=(2400, 1200), default=2400)
    s.add_argument("--pol", type=int, choices=(0, 1, 2), default=2)
    s.add_argument("--noise-hz", type=float, default=0.0)
    d = sub.add_parser("decode")
    d.add_argument("cap")
    d.add_argument("--baud", type=int, choices=(2400, 1200), default=2400)
    e = sub.add_parser("errors", help="where in the slot a capture's wrong bits fall")
    e.add_argument("cap")
    e.add_argument("--baud", type=int, choices=(2400, 1200), default=2400)
    e.add_argument("--seq0", type=int, default=None, help="the run's first sequence (2400: 0, 1200: 100)")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        for b in (2400, 1200):
            n = key_up_packets(b)
            print(f"{b} baud: {n} packets in a {TX_MS} ms key-up ({FRAME} bytes, "
                  f"{FRAME * 8 / b * 1e3:.0f} ms each), {SLOT * 4 // (SYM_RATE // b)} data bits a slot")
    elif a.cmd == "sim":
        pk, n = sim(a.baud, a.pol, a.noise_hz)
        ok = [x for x in pk if x[3]]
        print(f"{a.baud} baud pol {a.pol}: {len(ok)}/{n} CRC right ({len(pk)} syncs); variants "
              + " ".join(f"v{x[1]}{'i' if x[4] else ''}" for x in ok))
    elif a.cmd == "errors":
        print("\n".join(errors(a.cap, a.baud, a.seq0)))
    else:
        print("\n".join(decode(a.cap, a.baud)))


if __name__ == "__main__":
    sys.exit(main())
