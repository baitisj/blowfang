"""DSotto raw: live sotto over the UV-380's HR_C6000 physical layer mode (0x10 = 0x66, 4800 symbols
a second, 9600 bit/s), framed for a 1200 bit/s voice stream instead of borrowing M17's or YSF's
frames. docs/uv380-dsotto-raw.md has the design, the simulation results and the bench plan.

  python3 -m modem.dsr plan                         the two formats: layout, rate, latency
  python3 -m modem.dsr sim [--fmt dsr120] [--cn0 50] [--fade 0] [--frames 200]
                                                    one point through the FM channel model
  python3 -m modem.dsr curves OUT.json [--quick]    every format, static and fading channels
  python3 -m modem.dsr wav OUT.wav [--sf 48]        the Pluto's ladder transmission (DSR120, DSR40,
                                                    and M17 live, the same superframes); send with
                                                    --dev 4800 (2400 Hz outer)
  python3 -m modem.dsr tap LOG [--json OUT]         decode the UV-380's own FM tap (c6fmtap_1c
                                                    log, runner plan uv380.rx tap1c)
  python3 -m modem.dsr decode CAP.cf32              decode a Pluto capture (a UV-380 send)
  python3 -m modem.dsr bytes OUT.bin [--fmt dsr120] [--sf N]   a test message's air bytes (for the
                                                    C framer's host test)

On air (both formats): the chip's 4FSK, a dibit a symbol (01 +3, 00 +1, 10 -1, 11 -3, the M17
map), RRC alpha 0.2, 2400 Hz outer (TX_DEV 123 % of the DMR pair, as M17). Each frame:

  sync (outer symbols only) | coded bits: K=7 tail-biting convolutional code over
  [header 3 bytes | sotto superframes | CRC-16], interleaved, whitened

  DSR40   40 ms, 192 symbols: 16-symbol sync, 1 superframe, 88 info bits at rate 1/4 = 352
  DSR120 120 ms, 576 symbols (4 C6000 slots): 24-symbol sync, 3 superframes, 184 info bits at
          rate 1/6 = 1104 (the choice: docs/uv380-dsotto-raw.md)
  DSR40D  DSR40 with each codeword spread over 3 frames (a diagonal interleaver), its own sync

Header: type (4 bits: 1 sotto 1200, 2 sotto 2400 reserved, 15 end), frame counter (8), and a 12
bit chunk of the sender's callsign (M17's base-40, 48 bits, chunk = counter mod 4). CRC: M17's
CRC-16 over header and payload. The code's generators (bit 0 = the newest input bit) are nested:
the first four make the rate 1/4 code (free distance 20), all six the rate 1/6 code (30).
Interleaver: coded bit i goes to position A i mod N. Whitening: PN9 (x^9 + x^5 + 1, seed 0x1FF).
SPDX-License-Identifier: MIT
"""
import argparse
import json
import math
import sys
import time

import numpy as np

from . import m17

SYM_RATE = 4800
OUTER_HZ = 2400.0
LEVEL = {0b01: 3, 0b00: 1, 0b10: -1, 0b11: -3}
POLYS = (0o105, 0o127, 0o157, 0o167, 0o113, 0o173)
CALL = "AG7EW"
T_SOTTO1200, T_SOTTO2400, T_END = 0x1, 0x2, 0xF
SF = 6
FS_TAP = 8000
FS_RX = 24000
SPS = FS_RX // SYM_RATE


class Fmt:
    def __init__(self, name, nsf, sync_hex, nsym, rate, a, diag=1):
        self.name, self.nsf, self.nsym, self.rate, self.a = name, nsf, nsym, rate, a
        self.diag = diag
        self.sync = bytes.fromhex(sync_hex)
        self.ninfo = 8 * (3 + SF * nsf + 2)
        self.ncoded = self.ninfo * rate
        self.nsync = 4 * len(self.sync)
        assert 2 * self.nsync + self.ncoded == 2 * nsym, name
        assert math.gcd(a, self.ncoded) == 1
        self.perm = (np.arange(self.ncoded) * a) % self.ncoded
        self.pn = pn9(self.ncoded)
        self.frame_s = nsym / SYM_RATE
        # diag > 1: each codeword's interleaved bits go out over `diag` frames, frame t carrying
        # segment j of codeword t - j (a convolutional interleaver: no wait at the sender, the
        # receiver decodes codeword c once frame c + diag - 1 is in)
        cuts = [round(self.ncoded * j / diag) for j in range(diag + 1)]
        self.segs = [(cuts[j], cuts[j + 1]) for j in range(diag)]

    def __repr__(self):
        return self.name


def pn9(n, seed=0x1FF):
    s, out = seed, np.zeros(n, np.uint8)
    for i in range(n):
        b = ((s >> 8) ^ (s >> 4)) & 1
        out[i] = s & 1
        s = ((s << 1) | b) & 0x1FF
    return out


DSR40 = Fmt("dsr40", 1, "FF75F5D5", 192, 4, 117)
DSR120 = Fmt("dsr120", 3, "7FF57D775D5F", 576, 6, 205)
DSR40D = Fmt("dsr40d", 1, "5DD7D5FF", 192, 4, 117, diag=3)
FMTS = {"dsr40": DSR40, "dsr120": DSR120, "dsr40d": DSR40D}


# ---- framing ----------------------------------------------------------------------------------

def header(typ, counter, call=CALL):
    cs = int.from_bytes(m17.callsign(call), "big")
    meta = (cs >> (12 * (3 - (counter & 3)))) & 0xFFF
    return bytes([(typ & 0xF) << 4 | (counter >> 4 & 0xF), (counter & 0xF) << 4 | meta >> 8, meta & 0xFF])


def info_bytes(fmt, typ, counter, payload, call=CALL):
    body = header(typ, counter, call) + bytes(payload).ljust(SF * fmt.nsf, b"\x00")
    return body + m17.crc16(body).to_bytes(2, "big")


def _parity(x):
    x ^= x >> 4
    x ^= x >> 2
    x ^= x >> 1
    return x & 1


_OUT = np.array([[_parity(m & g) for g in POLYS] for m in range(128)], np.uint8)


def tb_encode(bits, rate):
    """tail-biting K=7: the register starts as the last six bits"""
    bits = np.asarray(bits, np.uint8)
    s = 0
    for b in bits[-6:]:
        s = ((s << 1) | int(b)) & 63
    out = np.zeros((len(bits), rate), np.uint8)
    for i, b in enumerate(bits):
        m = ((s << 1) | int(b)) & 0x7F
        out[i] = _OUT[m, :rate]
        s = m & 63
    return out.ravel()


def codeword(fmt, info):
    """info bytes -> the codeword's interleaved bits (not whitened)"""
    coded = tb_encode(m17.to_bits(info), fmt.rate)
    air = np.zeros(fmt.ncoded, np.uint8)
    air[fmt.perm] = coded
    return air


def frame_bytes(fmt, info):
    return fmt.sync + m17.to_bytes(codeword(fmt, info) ^ fmt.pn)


def diag_frames(fmt, words):
    """interleaved codewords -> frames for a diagonal format: frame t has segment j of word t - j
    (zeros where there is no such word); len(words) + diag - 1 frames"""
    out = b""
    zero = np.zeros(fmt.ncoded, np.uint8)
    for t in range(len(words) + fmt.diag - 1):
        air = np.zeros(fmt.ncoded, np.uint8)
        for j, (a, b) in enumerate(fmt.segs):
            w = words[t - j] if 0 <= t - j < len(words) else zero
            air[a:b] = w[a:b]
        out += fmt.sync + m17.to_bytes(air ^ fmt.pn)
    return out


def superframes(sotto):
    sotto = bytes(sotto)
    return [sotto[SF * k:SF * k + SF] for k in range(len(sotto) // SF)]


def tx_stream(fmt, sotto, call=CALL, preamble_s=0.040, counter0=0):
    """sotto 1200 superframes -> air bytes: a preamble (+3 -3 ..., 0x77), one frame per nsf
    superframes (the last one padded with its last superframe), an end frame, two bytes of 0x77"""
    sf = superframes(sotto)
    if not sf:
        raise ValueError("no superframes")
    sf += [sf[-1]] * (-len(sf) % fmt.nsf)
    out = bytes([0x77]) * int(round(preamble_s * SYM_RATE / 4))
    infos = [info_bytes(fmt, T_SOTTO1200, (counter0 + k) & 0xFF, b"".join(sf[fmt.nsf * k:fmt.nsf * k + fmt.nsf]), call)
             for k in range(len(sf) // fmt.nsf)]
    infos.append(info_bytes(fmt, T_END, (counter0 + len(sf) // fmt.nsf) & 0xFF, b"", call))
    if fmt.diag > 1:
        out += diag_frames(fmt, [codeword(fmt, i) for i in infos])
    else:
        out += b"".join(frame_bytes(fmt, i) for i in infos)
    return out + b"\x77\x77"


def m17_live(sotto, call=CALL):
    """the same superframes as an M17 stream in real time: sf k and sf k-1 a frame (m17.tx_sotto)"""
    return m17.tx_sotto(sotto, src=call, repeat=True)


def test_sotto(n):
    """c6sotto's test message: superframe k = bytes 6k..6k+5 counting up"""
    return bytes(i & 0xFF for i in range(SF * n))


# ---- modulation and the channel ---------------------------------------------------------------

def modulate(data, fs=48000, outer_hz=OUTER_HZ, alpha=0.2):
    """the transmitter's frequency (Hz) at fs: the chip's RRC (alpha 0.2)"""
    return m17.modulate(data, fs=fs, alpha=alpha, outer_hz=outer_hz)


def _fir_lp(fc, fs, ntap):
    n = np.arange(ntap) - (ntap - 1) / 2
    h = np.sinc(2 * fc / fs * n) * np.hamming(ntap)
    return h / np.sum(h)


def rayleigh(n, fs, fd, rng, m=24):
    """Clarke flat fading, unit mean power, Doppler fd (sum of sinusoids)"""
    t = np.arange(n) / fs
    h = np.zeros(n, complex)
    for k in range(m):
        a = 2 * np.pi * (k + rng.random()) / m
        h += np.exp(1j * (2 * np.pi * fd * np.cos(a) * t + 2 * np.pi * rng.random()))
    return h / np.sqrt(m)


def fm_channel(freq_hz, cn0_db, fs=48000, fade_hz=0.0, offset_hz=0.0, rng=None, if_hz=6000.0,
               tap=True):
    """the transmitter's frequency -> the receiver's discriminator. Carrier power 1 (mean, under
    fading), complex white noise at C/N0 (dB-Hz), the IF filter (+-if_hz), a limiter-
    discriminator; tap=True: then 3.4 kHz low-pass and 8 kHz samples, as the C6000's FM tap"""
    rng = rng or np.random.default_rng(1)
    f = np.asarray(freq_hz, float) + offset_hz
    x = np.exp(2j * np.pi * np.cumsum(f) / fs)
    if fade_hz > 0:
        x = x * rayleigh(len(x), fs, fade_hz, rng)
    sigma = math.sqrt(fs / 10 ** (cn0_db / 10) / 2)
    x = x + sigma * (rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x)))
    x = np.convolve(x, _fir_lp(if_hz, fs, 97), "same")
    d = np.angle(x[1:] * np.conj(x[:-1])) * fs / (2 * np.pi)
    d = np.concatenate([[0.0], d])
    if not tap:
        return d
    d = np.convolve(d, _fir_lp(3400.0, fs, 121), "same")
    return d[::fs // FS_TAP]


# ---- receiver ---------------------------------------------------------------------------------

_RRC = None


def front(disc, fs):
    """discriminator at fs (8 kHz tap or 48 kHz) -> matched-filter output at 24 kHz, DC off"""
    global _RRC
    if _RRC is None:
        _RRC = m17.rrc(0.2, SPS, span=6)
    disc = np.asarray(disc, float)
    if fs == FS_TAP:
        up = np.zeros(len(disc) * 3)
        up[::3] = disc * 3
        y = np.convolve(up, _RRC, "same")
    elif fs == 48000:
        y = np.convolve(disc, m17.rrc(0.2, 10, span=6), "same")[::2]
    elif fs == FS_RX:
        y = np.convolve(disc, _RRC, "same")
    else:
        raise ValueError(f"fs {fs}: 8000, 24000 or 48000")
    return m17.dc_block(y, int(0.12 * FS_RX))


def sync_symbols(sync):
    return m17.symbols(sync)


def _corr(y, pat):
    """normalized correlation of y against pat at symbol spacing, every 24 kHz sample"""
    n = len(y) - (len(pat) - 1) * SPS
    if n <= 0:
        return np.zeros(0), np.zeros(0)
    c = np.zeros(n)
    e = np.zeros(n)
    for k, p in enumerate(pat):
        seg = y[k * SPS:k * SPS + n]
        c += p * seg
        e += seg * seg
    return c / (np.sqrt(e * (pat @ pat)) + 1e-9), c


def _sample(y, pos):
    i = np.floor(pos).astype(int)
    f = pos - i
    i = np.clip(i, 0, len(y) - 2)
    return y[i] * (1 - f) + y[i + 1] * f


def frame_soft(y, start, nsym, sync_pat):
    """the frame's symbols at start (fractional 24 kHz sample), levelled by the sync's LS fit ->
    (levels, gain)"""
    v = _sample(y, start + np.arange(nsym) * SPS)
    ns = len(sync_pat)
    vs = v[:ns]
    pm = float(np.mean(sync_pat))
    pc = sync_pat - pm
    g = float((vs - np.mean(vs)) @ pc / (pc @ pc))          # least squares: v = g p + dc
    dc = float(np.mean(vs)) - g * pm
    if g <= 0:
        return None, g
    return (v - dc) / g, g


def soft_bits(levels, clip=4.0):
    """levels (+-1, +-3 units) -> soft bits, + = 0: MSB the sign, LSB inner (+) or outer (-)"""
    lv = np.clip(levels, -clip, clip)
    out = np.empty(2 * len(lv))
    out[0::2] = lv
    out[1::2] = 2.0 - np.abs(lv)
    return out


# Viterbi tables for K=7 (64 states): the two predecessors of each state and their outputs
_NS = np.arange(64)
_P0 = _NS >> 1
_P1 = (_NS >> 1) | 32
_E0 = 1.0 - 2.0 * _OUT[(_P0 << 1 | (_NS & 1)) & 0x7F].astype(float)
_E1 = 1.0 - 2.0 * _OUT[(_P1 << 1 | (_NS & 1)) & 0x7F].astype(float)


def tb_viterbi(soft, rate, wrap=48):
    """soft (F, n * rate), + = bit 0 -> (bits (F, n), path metric share disagreeing). Tail-biting by
    wrapping: decode the last `wrap` steps, all n, then the first `wrap` steps again; keep the
    middle n"""
    soft = np.atleast_2d(soft)
    F, L = soft.shape
    n = L // rate
    s = soft.reshape(F, n, rate)
    w = min(wrap, n)
    ext = np.concatenate([s[:, n - w:], s, s[:, :w]], axis=1)
    T = ext.shape[1]
    e0, e1 = _E0[:, :rate].T, _E1[:, :rate].T          # (rate, 64)
    metric = np.zeros((F, 64))
    dec = np.zeros((F, T, 64), bool)
    for t in range(T):
        st = ext[:, t, :]
        m0 = metric[:, _P0] + st @ e0
        m1 = metric[:, _P1] + st @ e1
        d = m1 > m0
        dec[:, t] = d
        metric = np.where(d, m1, m0)
    state = np.argmax(metric, axis=1)
    bits = np.zeros((F, T), np.uint8)
    rows = np.arange(F)
    for t in range(T - 1, -1, -1):
        bits[:, t] = state & 1
        d = dec[rows, t, state]
        state = np.where(d, (state >> 1) | 32, state >> 1)
    out = bits[:, w:w + n]
    # the share of soft weight the decoded path disagrees with (a quality figure, as m17_rx's)
    recoded = np.stack([tb_encode(b, rate) for b in out])
    agree = np.sum(np.abs(soft) * ((soft < 0) != recoded.astype(bool)), axis=1)
    q = agree / (np.sum(np.abs(soft), axis=1) + 1e-9)
    return out, q


def decode_soft(fmt, softs, whitened=True):
    """(F, ncoded) soft air bits (after the sync) -> list of dicts (or None where the CRC fails)"""
    softs = np.atleast_2d(softs)
    if whitened:
        softs = softs * (1.0 - 2.0 * fmt.pn)             # whitening off
    coded = softs[:, fmt.perm]                            # de-interleave
    bits, q = tb_viterbi(coded, fmt.rate)
    out = []
    for b, qq in zip(bits, q):
        by = m17.to_bytes(b)
        body, crc = by[:-2], int.from_bytes(by[-2:], "big")
        if m17.crc16(body) != crc:
            out.append(None)
            continue
        typ, counter = body[0] >> 4, (body[0] & 0xF) << 4 | body[1] >> 4
        meta = (body[1] & 0xF) << 8 | body[2]
        out.append({"type": typ, "counter": counter, "meta": meta, "payload": body[3:], "q": float(qq)})
    return out


def find_frames(y, fmt, hunt=None, misses=4, track=3):
    """matched-filter output -> frame starts [(start, score)]. Sync peaks over `hunt` are chained
    into runs a whole number of frames apart (+-track samples a frame: the far end's clock); every
    frame position of a run is read, the gaps between its peaks and up to `misses` frames before
    and after it included (a weak sync still reads its frame there: the flywheel). The CRC decides
    which reads are frames. A radio does the same with a lock and a frame clock (c/common/m17_rx.c)"""
    if hunt is None:
        hunt = 0.80 if fmt.nsym == 192 and len(fmt.sync) <= 2 else 0.60
    pat = sync_symbols(fmt.sync)
    sc, _ = _corr(y, pat)
    n = len(sc)
    span = fmt.nsym * SPS
    if n == 0:
        return []
    # local maxima over hunt, one per half frame
    cand = np.flatnonzero(sc >= hunt)
    peaks = []
    for k in cand:
        if peaks and k - peaks[-1] < span // 2:
            if sc[k] > sc[peaks[-1]]:
                peaks[-1] = k
            continue
        peaks.append(int(k))
    runs = []
    for k in peaks:
        for r in runs:
            m = round((k - r[-1]) / span)
            if 1 <= m <= misses + 1 and abs(k - r[-1] - m * span) <= track * m:
                r.append(k)
                break
        else:
            runs.append([k])

    def refine(k):
        a, b = max(0, k - track), min(n - 1, k + track)
        if a > b:
            return None
        k2 = a + int(np.argmax(sc[a:b + 1]))
        frac = 0.0
        if 0 < k2 < n - 1:
            l, c, r = sc[k2 - 1], sc[k2], sc[k2 + 1]
            den = l - 2 * c + r
            frac = 0.5 * (l - r) / den if den < 0 else 0.0
        return k2 + float(np.clip(frac, -0.5, 0.5)), float(sc[k2])

    out = {}
    for r in runs:
        pos = []
        for a, b in zip(r, r[1:]):
            m = round((b - a) / span)
            pos += [a + (b - a) * j / m for j in range(m)]
        pos.append(r[-1])
        pos = [r[0] - span * j for j in range(misses, 0, -1)] + pos + [r[-1] + span * j for j in range(1, misses + 1)]
        for p in pos:
            k = int(round(p))
            if k < 0 or k + span + SPS >= len(y):
                continue
            got = refine(k)
            if got is None:
                continue
            key = int(round(got[0] / (span / 4)))
            if key not in out or got[1] > out[key][1]:
                out[key] = got
    return sorted(out.values())


def receive(y, fmt):
    """matched-filter output -> decoded frames [{at, score, ...}] (None entries dropped)"""
    pat = sync_symbols(fmt.sync)
    starts = find_frames(y, fmt)
    softs, keep = [], []
    for pos, s in starts:
        lv, g = frame_soft(y, pos, fmt.nsym, pat)
        if lv is None:
            continue
        softs.append(soft_bits(lv[len(pat):]))
        keep.append((pos, s, g))
    if not softs:
        return []
    out = []
    if fmt.diag > 1:
        return _receive_diag(fmt, keep, softs)
    for (pos, s, g), d in zip(keep, decode_soft(fmt, np.array(softs))):
        if d is not None:
            d.update({"at": pos / FS_RX, "score": s, "gain": g})
            out.append(d)
    return out


def _receive_diag(fmt, keep, softs, anchor=0.75, track=3):
    """frames read (positions, soft bits) -> codewords put back from their diag segments. Each read
    needs its number on the far end's frame clock: strong syncs (over `anchor`) are the clock's
    ticks, chained when a whole number of frames apart; a read takes its number from the nearest
    tick it is in step with, and reads in step with none are dropped. A frame with no read is an
    erasure in the codewords it carried"""
    span = fmt.nsym * SPS
    ticks = sorted((p, s) for p, s, g in keep if s >= anchor)
    if not ticks:
        return []
    clock, num = [], []
    for i, (p, s) in enumerate(ticks):
        if i:
            m = round((p - ticks[i - 1][0]) / span)
            if 1 <= m <= 50 and abs(p - ticks[i - 1][0] - m * span) <= track * max(1, m / 8):
                clock.append(clock[-1])
                num.append(num[-1] + m)
                continue
        clock.append(len(set(clock)))
        num.append(0)
    tpos = np.array([p for p, _ in ticks])
    byn = {}
    for (pos, s, g), sb in zip(keep, softs):
        i = int(np.argmin(np.abs(tpos - pos)))
        m = round((pos - tpos[i]) / span)
        if abs(m) > 50 or abs(pos - tpos[i] - m * span) > track * max(1, abs(m) / 8):
            continue
        key = (clock[i], num[i] + m)
        if key not in byn or s > byn[key][1]:
            byn[key] = (pos, s, g, np.asarray(sb) * (1.0 - 2.0 * fmt.pn))
    words, meta = [], []
    for c in sorted({k[0] for k in byn}):
        ns = [k[1] for k in byn if k[0] == c]
        for n in range(min(ns) - fmt.diag + 1, max(ns) + 1):
            w = np.zeros(fmt.ncoded)
            have = []
            for j, (a, b) in enumerate(fmt.segs):
                f = byn.get((c, n + j))
                if f is not None:
                    w[a:b] = f[3][a:b]
                    have.append(f)
            if len(have) < 2:
                continue
            words.append(w)
            ref = have[0]
            meta.append((ref[0] - span * ([byn.get((c, n + j)) for j in range(fmt.diag)].index(ref)), ref[1], ref[2]))
    if not words:
        return []
    out = []
    for (pos, s, g), d in zip(meta, decode_soft(fmt, np.array(words), whitened=False)):
        if d is not None:
            d.update({"at": pos / FS_RX, "score": s, "gain": g})
            out.append(d)
    return out


# M17 live, read the same way (soft bits, the stream sync only)
_M17_PAT = m17.symbols(m17.SYNC_STREAM)
_M17_DEC = np.array([1.0 - 2.0 * b for b in m17.to_bits(m17.DECORR)])
_M17_PERM = np.array(m17._PERM)
_K5_OUT = np.array([[bin(m & 0x19).count("1") & 1, bin(m & 0x17).count("1") & 1] for m in range(32)], np.uint8)


def k5_viterbi(soft):
    """M17's K=5 rate 1/2 with 4 flush bits, soft (F, 2n) -> bits (F, n)"""
    soft = np.atleast_2d(soft)
    F, L = soft.shape
    n = L // 2
    s = soft.reshape(F, n, 2)
    ns = np.arange(16)
    p0, p1 = ns >> 1, (ns >> 1) | 8
    e0 = (1.0 - 2.0 * _K5_OUT[(p0 << 1 | (ns & 1)) & 0x1F]).T
    e1 = (1.0 - 2.0 * _K5_OUT[(p1 << 1 | (ns & 1)) & 0x1F]).T
    metric = np.full((F, 16), -1e9)
    metric[:, 0] = 0
    dec = np.zeros((F, n, 16), bool)
    for t in range(n):
        m0 = metric[:, p0] + s[:, t] @ e0
        m1 = metric[:, p1] + s[:, t] @ e1
        d = m1 > m0
        dec[:, t] = d
        metric = np.where(d, m1, m0)
    state = np.zeros(F, int)
    bits = np.zeros((F, n), np.uint8)
    rows = np.arange(F)
    for t in range(n - 1, -1, -1):
        bits[:, t] = state & 1
        d = dec[rows, t, state]
        state = np.where(d, (state >> 1) | 8, state >> 1)
    return bits


class _M17Fmt:
    name = "m17live"
    nsym = 192
    nsf = 1
    frame_s = 0.040
    sync = m17.SYNC_STREAM


M17LIVE = _M17Fmt()


def receive_m17(y):
    """matched-filter output -> M17 stream frames whose sotto CRC checks: [{at, fn, payload}]"""
    starts = find_frames(y, M17LIVE)
    softs, keep = [], []
    for pos, s in starts:
        lv, g = frame_soft(y, pos, 192, _M17_PAT)
        if lv is None:
            continue
        sb = soft_bits(lv[8:]) * _M17_DEC
        softs.append(sb[_M17_PERM])
        keep.append((pos, s))
    if not softs:
        return []
    softs = np.array(softs)
    dep = np.stack([m17.depuncture(sb[96:], m17.P2, 296) for sb in softs])
    bits = k5_viterbi(dep)[:, :144]
    out = []
    for (pos, s), b in zip(keep, bits):
        body = m17.to_bytes(b)
        p = body[2:]
        if m17.crc16(p[:12]) == int.from_bytes(p[12:14], "big"):
            out.append({"at": pos / FS_RX, "fn": int.from_bytes(body[:2], "big") & 0x7FFF, "payload": p,
                        "score": s})
    return out


def recovered(fmt, frames, nsf_sent):
    """decoded frames -> {superframe index: bytes} for the stream tx_stream / m17_live made"""
    got = {}
    if fmt is M17LIVE:
        for f in frames:
            p, k = f["payload"], f["fn"]
            if k < nsf_sent:
                got[k] = p[:6]
            if p[14] & m17.SOTTO_REPEAT and 0 < k <= nsf_sent:
                got.setdefault(k - 1, p[6:12])
        return got
    fr = sorted((f for f in frames if f["type"] == T_SOTTO1200), key=lambda f: f["at"])
    t0 = None
    for f in fr:
        # the counter wraps at 256 frames: the frame's number is the one nearest its time
        if t0 is None:
            t0 = f["at"] - f["counter"] * fmt.frame_s
        guess = (f["at"] - t0) / fmt.frame_s
        num = f["counter"] + 256 * round((guess - f["counter"]) / 256)
        for j in range(fmt.nsf):
            k = num * fmt.nsf + j
            if 0 <= k < nsf_sent:
                got[k] = f["payload"][SF * j:SF * j + SF]
    return got


def rx_any(y, fmt):
    return receive_m17(y) if fmt is M17LIVE else receive(y, fmt)


# ---- simulation -------------------------------------------------------------------------------

def sim_point(fmt, cn0_db, fade_hz=0.0, nsf=600, seed=1, offset_hz=150.0, ppm=20.0, tap=True):
    """one transmission of nsf random superframes through the FM channel -> dict"""
    rng = np.random.default_rng(seed)
    sotto = rng.integers(0, 256, SF * nsf, dtype=np.uint8).tobytes()
    # counters wrap at 256 frames: keep a run under that
    data = m17_live(sotto) if fmt is M17LIVE else tx_stream(fmt, sotto)
    fs = 48000
    f = modulate(data, fs)
    if ppm:
        # the far end's clock: resample the frequency track by (1 + ppm)
        t = np.arange(int(len(f) / (1 + ppm * 1e-6))) * (1 + ppm * 1e-6)
        f = np.interp(t, np.arange(len(f)), f)
    pad = np.zeros(int(0.1 * fs))
    f = np.concatenate([pad, f, pad])
    d = fm_channel(f, cn0_db, fs, fade_hz, offset_hz, rng, tap=tap)
    y = front(d, FS_TAP if tap else fs)
    frames = rx_any(y, fmt)
    got = recovered(fmt, frames, nsf)
    sf = superframes(sotto)
    good = sum(1 for k, v in got.items() if v == sf[k])
    bad = sum(1 for k, v in got.items() if v != sf[k])
    return {"fmt": fmt.name, "cn0": cn0_db, "fade_hz": fade_hz, "nsf": nsf, "good": good, "bad": bad,
            "sfer": 1.0 - good / nsf, "frames": len(frames)}


def dbm(cn0_db, nf_db=6.0):
    """the input power for a C/N0 at a receiver noise figure nf_db (kTB -174 dBm/Hz)"""
    return cn0_db - 174.0 + nf_db


def curves(quick=False, seeds=None):
    """every format, static (AWGN) and fading (Rayleigh 5 and 20 Hz) -> list of points"""
    fmts = [M17LIVE, DSR40, DSR40D, DSR120]
    out = []
    static = np.arange(38, 53, 1) if not quick else np.arange(40, 52, 2)
    fading = np.arange(44, 70, 2) if not quick else np.arange(46, 66, 4)
    nsf = 1500 if not quick else 240
    for fmt in fmts:
        for fade, grid in ((0.0, static), (5.0, fading), (20.0, fading)):
            for cn0 in grid:
                r = sim_point(fmt, float(cn0), fade, nsf=nsf, seed=int(cn0 * 10 + fade))
                out.append(r)
                print(f"{fmt.name:8s} fade {fade:4.0f} Hz  C/N0 {cn0:4.0f} dB-Hz  SFER {r['sfer']:.4f}"
                      f"  (bad {r['bad']})", flush=True)
    return out


def threshold(points, fmt, fade, target=0.01):
    """the C/N0 where the superframe error rate first falls to target (interpolated, log)"""
    pts = sorted((p["cn0"], p["sfer"]) for p in points if p["fmt"] == fmt and p["fade_hz"] == fade)
    for (c0, e0), (c1, e1) in zip(pts, pts[1:]):
        if e0 > target >= e1:
            l0, l1 = math.log10(max(e0, 1e-4)), math.log10(max(e1, 1e-4))
            return c0 + (c1 - c0) * (l0 - math.log10(target)) / (l0 - l1) if l0 != l1 else c1
    return None


# ---- latency ----------------------------------------------------------------------------------

def latency(fmt, lowdelay=False):
    """mouth-to-ear for a live call, ms: the parts that each format and setting changes"""
    enc = 120 if lowdelay else 200          # sotto 1200 fixed encoder: first superframe out
    dec = 40 if lowdelay else 80            # decoder 1 or 2 superframes behind
    parts = [
        ("sotto encoder (first superframe after speech)", enc),
        ("fill a frame (superframes it carries, after the first)", 40 * (fmt.nsf - 1)),
        ("C6000 slot pipeline (idle half, next slot edge, worst)", 60),
        ("air: the whole frame", round(fmt.frame_s * 1000)),
        ("diagonal interleaver: the codeword's last segment", round((getattr(fmt, "diag", 1) - 1) * fmt.frame_s * 1000)),
        ("FM tap half (32 ms, worst) + decode", 32 + 5),
        ("sotto decoder", dec),
        ("speaker DAC queue (one superframe)", 40),
    ]
    return parts, sum(p[1] for p in parts)


# ---- the Pluto ladder transmission and the decodes of real captures ----------------------------

LADDER = ("dsr120", "dsr40", "dsr40d", "m17live")


def ladder_wav(path, nsf=48, gap_s=0.4, fs=48000):
    """one send for a sensitivity ladder: the test message as DSR120, DSR40, DSR40D, then M17 live,
    gap_s of carrier between (the Pluto's FM stays keyed). Half scale: send with --dev 4800"""
    sotto = test_sotto(nsf)
    gap = np.zeros(int(gap_s * fs))
    parts = [gap]
    marks = {}
    t = len(gap)
    for name in LADDER:
        data = m17_live(sotto) if name == "m17live" else tx_stream(FMTS[name], sotto)
        x = modulate(data, fs, outer_hz=0.5)
        marks[name] = [t / fs, (t + len(x)) / fs]
        parts += [x, gap]
        t += len(x) + len(gap)
    x = np.concatenate(parts)
    m17.write_wav(path, np.round(x * 32767), fs)
    return len(x) / fs, marks


def callsign_from(frames):
    """the sender's callsign from the frames' 12-bit chunks (counter mod 4), or None"""
    parts = {}
    for f in frames:
        parts.setdefault(f["counter"] & 3, f["meta"])
    if len(parts) < 4:
        return None
    v = 0
    for k in range(4):
        v = v << 12 | parts[k]
    return m17.callsign_text(v.to_bytes(6, "big"))


def tap_log(path):
    with open(path, errors="replace") as f:
        lines = f.read().splitlines()
    from . import m17rx
    x, lost, _, info = m17rx.tap_parse(lines)
    rssi = []
    for ln in lines:
        if " st " in ln and "rssi=" in ln:
            try:
                t = int(ln.split(" st ")[1].split()[0])
                rssi.append((t, int(ln.split("rssi=")[1].split()[0])))
            except (ValueError, IndexError):
                pass
    return x, lost, info, rssi


def group_runs(frames, period, gap_s=0.6):
    """frames sorted by time -> runs (a send's block of one format)"""
    out, cur = [], []
    for f in sorted(frames, key=lambda f: f["at"]):
        if cur and f["at"] - cur[-1]["at"] > gap_s:
            out.append(cur)
            cur = []
        cur.append(f)
    if cur:
        out.append(cur)
    return out


def report_tap(path, nsf=48, invert=True):
    """the UV-380's tap log -> per format, each block of frames heard: superframes back of nsf"""
    x, lost, info, rssi = tap_log(path)
    if invert:
        x = -x
    y = front(x, FS_TAP)
    want = superframes(test_sotto(nsf))
    rows = []
    for name in LADDER:
        fmt = M17LIVE if name == "m17live" else FMTS[name]
        frames = rx_any(y, fmt)
        for run in group_runs(frames, fmt.frame_s, gap_s=2.0):
            got = recovered(fmt, run, nsf)
            good = sum(1 for k, v in got.items() if v == want[k])
            t0 = run[0]["at"]
            r = [v for (t, v) in rssi if abs(t / 1000 - t0) < 3]
            rows.append({"fmt": name, "t": round(t0, 2), "frames": len(run), "sf_good": good, "nsf": nsf,
                         "rssi": (sum(r) / len(r)) if r else None})
    rows.sort(key=lambda r: r["t"])
    # one Pluto send = the three blocks a few seconds apart; sends are further apart
    send, seen = -1, set()
    for i, r in enumerate(rows):
        if i == 0 or r["t"] - rows[i - 1]["t"] > 6.0 or r["fmt"] in seen:
            send += 1
            seen = set()
        seen.add(r["fmt"])
        r["send"] = send
    return {"samples": len(x), "lost_lines": lost, "rows": rows}


def ladder_table(rep, gains=None):
    """report_tap's rows -> one line per send: superframes back per format"""
    sends = {}
    for r in rep["rows"]:
        sends.setdefault(r["send"], {})[r["fmt"]] = r
    out = []
    for k in sorted(sends):
        g = f"{gains[k]:g} dB" if gains and k < len(gains) else f"send {k}"
        cells = []
        for name in LADDER:
            r = sends[k].get(name)
            cells.append(f"{name} {r['sf_good'] if r else 0}/{(r or {}).get('nsf', '-')}")
        rs = [r["rssi"] for r in sends[k].values() if r["rssi"] is not None]
        out.append(f"{g:>9s}: " + "  ".join(cells) + (f"  rssi {sum(rs) / len(rs):.0f}" if rs else ""))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m modem.dsr", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    s = sub.add_parser("sim")
    s.add_argument("--fmt", default="dsr120", choices=["dsr40", "dsr40d", "dsr120", "m17live"])
    s.add_argument("--cn0", type=float, default=50.0)
    s.add_argument("--fade", type=float, default=0.0)
    s.add_argument("--sf", type=int, default=600)
    s.add_argument("--seed", type=int, default=1)
    c = sub.add_parser("curves")
    c.add_argument("out")
    c.add_argument("--quick", action="store_true")
    w = sub.add_parser("wav")
    w.add_argument("out")
    w.add_argument("--sf", type=int, default=48)
    t = sub.add_parser("tap")
    t.add_argument("log")
    t.add_argument("--sf", type=int, default=48)
    t.add_argument("--json")
    t.add_argument("--steps", help="the Pluto's steps --out json: label the sends by gain")
    t.add_argument("--no-invert", action="store_true")
    d = sub.add_parser("decode")
    d.add_argument("cap")
    d.add_argument("--fmt", default="dsr120", choices=["dsr40", "dsr40d", "dsr120"])
    b = sub.add_parser("bytes")
    b.add_argument("out")
    b.add_argument("--fmt", default="dsr120", choices=["dsr40", "dsr120"])
    b.add_argument("--sf", type=int, default=75)
    a = ap.parse_args(argv)

    if a.cmd == "plan":
        for fmt in (DSR40, DSR40D, DSR120):
            parts, tot = latency(fmt)
            _, tot_ld = latency(fmt, True)
            print(f"{fmt.name}: {fmt.nsym} symbols ({fmt.frame_s * 1000:.0f} ms), sync {fmt.nsync} symbols, "
                  f"{fmt.nsf} superframe(s), {fmt.ninfo} info bits at rate 1/{fmt.rate} = {fmt.ncoded}; "
                  f"mouth to ear {tot} ms ({tot_ld} ms with the low-delay codec builds)")
            for name, ms in parts:
                if ms:
                    print(f"   {ms:4d} ms  {name}")
        parts, tot = latency(M17LIVE)
        print(f"m17live: 40 ms frames, sf k and k-1 a frame; mouth to ear {tot} ms")
        return 0
    if a.cmd == "sim":
        fmt = M17LIVE if a.fmt == "m17live" else FMTS[a.fmt]
        t0 = time.time()
        r = sim_point(fmt, a.cn0, a.fade, a.sf, a.seed)
        r["s"] = round(time.time() - t0, 1)
        print(json.dumps(r))
        return 0
    if a.cmd == "curves":
        pts = curves(a.quick)
        summary = {f"{fmt}/{fade:g}": threshold(pts, fmt, fade) for fmt in ("m17live", "dsr40", "dsr40d", "dsr120")
                   for fade in (0.0, 5.0, 20.0)}
        with open(a.out, "w") as f:
            json.dump({"points": pts, "cn0_at_1pct": summary, "nf_db_for_dbm": 6.0}, f, indent=1)
        for k, v in summary.items():
            print(f"{k:14s} 1 % SFER at {v if v is None else round(v, 1)} dB-Hz"
                  + ("" if v is None else f" ({dbm(v):.1f} dBm at NF 6 dB)"))
        return 0
    if a.cmd == "wav":
        secs, marks = ladder_wav(a.out, a.sf)
        print(f"{a.out}: {secs:.1f} s at 48 kHz, test message {a.sf} superframes as "
              + ", ".join(f"{k} {v[0]:.2f}-{v[1]:.2f} s" for k, v in marks.items())
              + "; send with --dev 4800 (2400 Hz outer)")
        return 0
    if a.cmd == "tap":
        rep = report_tap(a.log, a.sf, not a.no_invert)
        print(f"{rep['samples']} tap samples ({rep['samples'] / FS_TAP:.1f} s), {rep['lost_lines']} lines lost")
        for r in rep["rows"]:
            print(f"  t {r['t']:7.2f} s  {r['fmt']:8s} frames {r['frames']:4d}  superframes {r['sf_good']:3d}/{r['nsf']}"
                  + (f"  rssi {r['rssi']:.0f}" if r["rssi"] is not None else ""))
        gains = None
        if a.steps:
            with open(a.steps) as f:
                gains = [st["gain"] for st in json.load(f)["steps"]]
        print("per send:")
        for ln in ladder_table(rep, gains):
            print("  " + ln)
        if a.json:
            with open(a.json, "w") as f:
                json.dump(rep, f, indent=1)
        return 0
    if a.cmd == "decode":
        from . import c6phy
        iq, meta = c6phy.read_capture(a.cap)
        fs = int(meta.get("fs", 240000))
        off, on, disc = c6phy.capture_disc(iq, fs)
        if disc is None:
            print("no signal")
            return 1
        y = front(disc, 48000)
        fmt = FMTS[a.fmt]
        frames = receive(y, fmt)
        print(f"carrier {off:+.0f} Hz, {len(frames)} {fmt.name} frames with a good CRC")
        for g in group_runs(frames, fmt.frame_s, gap_s=2.0):
            types = sorted({f['type'] for f in g})
            got = recovered(fmt, g, 1 << 16)
            ks = sorted(got)
            call = callsign_from(g)
            print(f"  t {g[0]['at']:.2f} s: {len(g)} frames, counters {g[0]['counter']}..{g[-1]['counter']}, "
                  f"types {types}, src {call}, superframes {len(ks)}"
                  + (f" ({ks[0]}..{ks[-1]}, {ks[-1] - ks[0] + 1 - len(ks)} missing)"
                     f" first={got[ks[0]].hex()} last={got[ks[-1]].hex()}" if ks else ""))
        return 0
    if a.cmd == "bytes":
        data = tx_stream(FMTS[a.fmt], test_sotto(a.sf))
        with open(a.out, "wb") as f:
            f.write(data)
        print(f"{a.out}: {len(data)} bytes")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
