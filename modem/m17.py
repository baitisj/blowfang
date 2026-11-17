"""M17 (m17project.org) frames for the UV-380's HR_C6000 physical layer mode, and their decode from
a Pluto capture: milestone 1 of docs/uv380-m17-scope.md.

The C6000's physical layer mode sends TX RAM bytes as 4FSK, a dibit a symbol, 01 +3, 00 +1, 10 -1,
11 -3: the same map as M17, so an M17 transmission's bytes go to the chip unchanged
(firmware/uv380-openrtx/mod/c6m17tx.c; this file writes its m17tx_*.h data). Written from the M17
specification; checked against OpenRTX's reference packet frame (tests/unit/M17_packet.cpp) in
tests/test_uv380_m17.py.

  python3 -m modem.m17 header PLAN OUT.h [--c2 VOICE.c2]  a module's bytes: bert, sms, voice
  python3 -m modem.m17 plan PLAN [--c2 VOICE.c2]           frames and air time
  python3 -m modem.m17 c2enc IN.wav OUT.c2                 Codec2 3200 (libcodec2, 8 kHz mono)
  python3 -m modem.m17 decode CAP.cf32 [--baseband OUT.raw] a Pluto capture: LSFs, packets,
                                                          stream frames, BERT errors, deviation per
                                                          level, symbol rate; OUT.raw = 24 kHz s16
                                                          for OpenRTX's m17_replay_tool
  python3 -m modem.m17 sim PLAN [--alpha 0.2]              the chip's shape at 2400 Hz, decoded

Layout per frame (40 ms, 192 symbols, 48 bytes): 16 bit sync, then 368 bits: payload, K=5 rate 1/2
convolutional code (G1 0x19, G2 0x17, 4 flush bits), punctured (LSF P1, stream P2, packet P3, BERT
P2), the quadratic interleaver (45 i + 92 i^2 mod 368), XOR the 46 byte decorrelator. A stream
frame's 368 bits start with its LICH (a 5 byte sixth of the LSF plus its index, Golay(24,12) x 4).
SPDX-License-Identifier: MIT
"""
import argparse
import ctypes
import ctypes.util
import os
import sys
import wave

import numpy as np

SYM_RATE = 4800
FRAME_BYTES = 48
OUTER_HZ = 2400.0                       # M17: +3 = +2.4 kHz
C6000_OUTER_HZ = 1944.0                 # the chip's DMR deviation before 0x45/0x46 scaling

SYNC_LSF = bytes([0x55, 0xF7])
SYNC_STREAM = bytes([0xFF, 0x5D])
SYNC_PACKET = bytes([0x75, 0xFF])
SYNC_BERT = bytes([0xDF, 0x55])
EOT = bytes([0x55, 0x5D])
PRE_LSF = 0x77                          # +3 -3 +3 -3: the preamble before an LSF
PRE_BERT = 0xDD                         # -3 +3: before BERT frames

DECORR = bytes([
    0xd6, 0xb5, 0xe2, 0x30, 0x82, 0xff, 0x84, 0x62, 0xba, 0x4e, 0x96, 0x90,
    0xd8, 0x98, 0xdd, 0x5d, 0x0c, 0xc8, 0x52, 0x43, 0x91, 0x1d, 0xf8, 0x6e,
    0x68, 0x2f, 0x35, 0xda, 0x14, 0xea, 0xcd, 0x76, 0x19, 0x8d, 0xd5, 0x80,
    0xd1, 0x33, 0x87, 0x13, 0x57, 0x18, 0x2d, 0x29, 0x78, 0xc3])
P1 = [1, 1, 0, 1] * 15 + [1]            # LSF: 61, 46 kept
P2 = [1] * 11 + [0]                     # stream, BERT
P3 = [1] * 7 + [0]                      # packet
GOLAY = [0x8eb, 0x93e, 0xa97, 0xdc6, 0x367, 0x6cd, 0xd99, 0x3da, 0x7b4, 0xf68, 0x63b, 0xc75]
CALL_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."
LEVEL = {0b01: 3, 0b00: 1, 0b10: -1, 0b11: -3}
DIBIT = {v: k for k, v in LEVEL.items()}
NBITS = 368

TYPE_PACKET_DATA = 0x0002               # packet mode, data
TYPE_STREAM_VOICE = 0x0005              # stream mode, voice (Codec2 3200)
SMS = 0x05                              # packet protocol: text message

IDENT = "AG7EW"


# ---- small pieces -----------------------------------------------------------------------------

def crc16(data):
    """M17's CRC: polynomial 0x5935, initial 0xFFFF, no reflection, no final XOR"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x5935) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def callsign(s):
    """base 40, big endian, 6 bytes ("" or ALL = broadcast FF..FF)"""
    s = s.upper()
    if s in ("", "ALL", "@ALL"):
        return b"\xff" * 6
    v = 0
    for ch in reversed(s):
        v = v * 40 + CALL_CHARS.index(ch)
    return v.to_bytes(6, "big")


def callsign_text(b):
    v = int.from_bytes(b, "big")
    if v == 0xFFFFFFFFFFFF:
        return "ALL"
    out = ""
    while v:
        out += CALL_CHARS[v % 40]
        v //= 40
    return out


def to_bits(data):
    return np.unpackbits(np.frombuffer(bytes(data), np.uint8))


def to_bytes(bits):
    return bytes(np.packbits(np.asarray(bits, np.uint8)))


def conv(bits):
    """K=5 rate 1/2, G1 = 1 + D^3 + D^4 (0x19), G2 = 1 + D + D^2 + D^4 (0x17), 4 flush bits"""
    m, out = 0, []
    for b in list(bits) + [0, 0, 0, 0]:
        m = ((m << 1) | int(b)) & 0x1F
        out.append(bin(m & 0x19).count("1") & 1)
        out.append(bin(m & 0x17).count("1") & 1)
    return out


def puncture(bits, pattern, n=NBITS):
    out = [b for i, b in enumerate(bits) if pattern[i % len(pattern)]]
    return out[:n]


def depuncture(soft, pattern, n_in):
    """kept positions get the values, punctured ones 0 (an erasure), n_in long"""
    out, k = np.zeros(n_in), 0
    for i in range(n_in):
        if pattern[i % len(pattern)] and k < len(soft):
            out[i] = soft[k]
            k += 1
    return out


_PERM = [(45 * i + 92 * i * i) % NBITS for i in range(NBITS)]


def interleave(bits):
    out = [0] * NBITS
    for i, b in enumerate(bits):
        out[_PERM[i]] = b
    return out


def deinterleave(bits):
    return [bits[_PERM[i]] for i in range(NBITS)]


def decorrelate(data):
    return bytes(a ^ b for a, b in zip(data, DECORR))


def golay_encode(d12):
    c = 0
    for i in range(12):
        if d12 >> i & 1:
            c ^= GOLAY[i]
    return d12 << 12 | c


def golay_decode(cw):
    """the 12 data bits, correcting up to 3 errors (exhaustive over small error patterns), or None"""
    if golay_encode(cw >> 12) == cw:
        return cw >> 12
    for w in range(1, 4):
        for e in _golay_errors(w):
            x = cw ^ e
            if golay_encode(x >> 12) == x:
                return x >> 12
    return None


_GE = {}


def _golay_errors(w):
    if w not in _GE:
        from itertools import combinations
        _GE[w] = [sum(1 << i for i in c) for c in combinations(range(24), w)]
    return _GE[w]


# ---- frames -----------------------------------------------------------------------------------

def lsf(src=IDENT, dst="", typ=TYPE_STREAM_VOICE, meta=b"\x00" * 14):
    body = callsign(dst) + callsign(src) + typ.to_bytes(2, "big") + bytes(meta)
    return body + crc16(body).to_bytes(2, "big")


def _finish(sync, bits368):
    return sync + decorrelate(to_bytes(interleave(bits368)))


def frame_lsf(l30):
    return _finish(SYNC_LSF, puncture(conv(to_bits(l30)), P1))


def lich(l30, idx):
    chunk = l30[5 * idx:5 * idx + 5]
    blocks = [chunk[0] << 4 | chunk[1] >> 4, (chunk[1] & 0xF) << 8 | chunk[2],
              chunk[3] << 4 | chunk[4] >> 4, (chunk[4] & 0xF) << 8 | idx << 5]
    return b"".join(golay_encode(b).to_bytes(3, "big") for b in blocks)


def frame_stream(l30, fn, payload16, last=False):
    head = ((fn & 0x7FFF) | (0x8000 if last else 0)).to_bytes(2, "big")
    coded = puncture(conv(to_bits(head + bytes(payload16))), P2, 272)
    return _finish(SYNC_STREAM, list(to_bits(lich(l30, fn % 6))) + coded)


def frame_packet(chunk, eof, counter):
    """25 bytes and the metadata byte: EOF (bit 7), counter (bits 6-2): the frame's number, or on
    the last frame how many of its 25 bytes are used"""
    body = bytes(chunk).ljust(25, b"\x00") + bytes([(0x80 if eof else 0) | (counter & 0x1F) << 2])
    return _finish(SYNC_PACKET, puncture(conv(to_bits(body)), P3))


class Prbs9:
    """x^9 + x^5 + 1, seeded 1 (M17's BERT)"""

    def __init__(self, state=1):
        self.s = state

    def bit(self):
        b = ((self.s >> 8) ^ (self.s >> 4)) & 1
        self.s = ((self.s << 1) | b) & 0x1FF
        return b


def _prbs_seq():
    p = Prbs9()
    seq = np.array([p.bit() for _ in range(511)], np.uint8)
    return np.concatenate([seq, seq])


_PRBS_SEQ2 = _prbs_seq()       # two periods: any 197-bit window of the sequence


def frame_bert(prbs):
    bits = [prbs.bit() for _ in range(197)]
    return _finish(SYNC_BERT, puncture(conv(bits), P2)), bits


def eot():
    return EOT * (FRAME_BYTES // 2)


def preamble(byte):
    return bytes([byte]) * FRAME_BYTES


def sms_payload(text):
    msg = bytes([SMS]) + text.encode("ascii") + b"\x00"
    return msg + crc16(msg).to_bytes(2, "big")


def packet_frames(payload):
    out, k = [], 0
    for i in range(0, len(payload), 25):
        chunk = payload[i:i + 25]
        last = i + 25 >= len(payload)
        out.append(frame_packet(chunk, last, len(chunk) if last else k))
        k += 1
    return out


# ---- transmissions ----------------------------------------------------------------------------

MAX_BYTES = 4800                # what a module key-up carries (c6m17tx.c: 4 s of slots)


def tx_sms(texts, src=IDENT):
    """one transmission per text: preamble, packet LSF, packet frames, EOT"""
    out = b""
    for t in texts:
        out += preamble(PRE_LSF) + frame_lsf(lsf(src, "", TYPE_PACKET_DATA))
        out += b"".join(packet_frames(sms_payload(t))) + eot()
    return out


def tx_voice(c2, src=IDENT):
    """Codec2 3200 (8 bytes per 20 ms): preamble, voice LSF, stream frames (16 bytes each), EOT"""
    c2 = bytes(c2)
    c2 += b"\x00" * (-len(c2) % 16)
    l30 = lsf(src, "", TYPE_STREAM_VOICE)
    n = len(c2) // 16
    out = preamble(PRE_LSF) + frame_lsf(l30)
    for fn in range(n):
        out += frame_stream(l30, fn, c2[16 * fn:16 * fn + 16], fn == n - 1)
    return out + eot()


def tx_bert(nframes):
    prbs = Prbs9()
    return preamble(PRE_BERT) + b"".join(frame_bert(prbs)[0] for _ in range(nframes)) + eot()


def build(plan, c2=None):
    if plan == "sms":
        return tx_sms([f"{IDENT} M17 TEST {k:02d}" for k in range(1, 21)])
    if plan == "bert":
        return tx_bert(MAX_BYTES // FRAME_BYTES - 2)
    if plan == "voice":
        if c2 is None:
            raise SystemExit("voice needs --c2 FILE (python3 -m modem.m17 c2enc IN.wav OUT.c2)")
        n = (MAX_BYTES // FRAME_BYTES - 3) * 16
        return tx_voice(c2[:n])
    raise SystemExit(f"unknown plan {plan}: bert, sms, voice")


def header(plan, data, src_note=""):
    """the module's C header: the bytes, MSB first, as the chip sends them"""
    lines = [f"/* m17tx_{plan}.h: written by python3 -m modem.m17 header {plan}{src_note}; do not edit.",
             f"   {len(data)} bytes = {len(data) // FRAME_BYTES} M17 frames, {len(data) / 1.2:.0f} ms at 4800"
             " symbols a second. SPDX-License-Identifier: MIT */",
             f"#define M17_TX_N {len(data)}u",
             f"#define M17_TX_FILL 0x{data[0]:02x}u",
             "static const uint8_t M17_TX[M17_TX_N] = {"]
    for i in range(0, len(data), 16):
        lines.append("    " + ", ".join(f"0x{b:02x}" for b in data[i:i + 16]) + ",")
    lines.append("};")
    return "\n".join(lines) + "\n"


# ---- Codec2 -----------------------------------------------------------------------------------

def _libcodec2():
    name = ctypes.util.find_library("codec2") or "libcodec2.so.1.2"
    lib = ctypes.CDLL(name)
    lib.codec2_create.restype = ctypes.c_void_p
    lib.codec2_create.argtypes = [ctypes.c_int]
    lib.codec2_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_short)]
    lib.codec2_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_short), ctypes.c_char_p]
    lib.codec2_destroy.argtypes = [ctypes.c_void_p]
    return lib


def c2enc(pcm8k):
    """int16 8 kHz -> Codec2 3200 bytes (160 samples -> 8 bytes)"""
    lib = _libcodec2()
    c2 = lib.codec2_create(0)                   # CODEC2_MODE_3200
    pcm = np.asarray(pcm8k, np.int16)
    pcm = np.concatenate([pcm, np.zeros(-len(pcm) % 160, np.int16)])
    out = b""
    for i in range(0, len(pcm), 160):
        buf = ctypes.create_string_buffer(8)
        blk = np.ascontiguousarray(pcm[i:i + 160])
        lib.codec2_encode(c2, buf, blk.ctypes.data_as(ctypes.POINTER(ctypes.c_short)))
        out += buf.raw
    lib.codec2_destroy(c2)
    return out


def c2dec(data):
    lib = _libcodec2()
    c2 = lib.codec2_create(0)
    out = []
    for i in range(0, len(data) - 7, 8):
        blk = np.zeros(160, np.int16)
        lib.codec2_decode(c2, blk.ctypes.data_as(ctypes.POINTER(ctypes.c_short)), bytes(data[i:i + 8]))
        out.append(blk)
    lib.codec2_destroy(c2)
    return np.concatenate(out) if out else np.zeros(0, np.int16)


def read_wav8k(path):
    with wave.open(path) as w:
        fs, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), np.int16).astype(float)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if fs != 8000:
        from scipy import signal
        g = np.gcd(fs, 8000)
        x = signal.resample_poly(x, 8000 // g, fs // g)
    return np.clip(x, -32768, 32767).astype(np.int16)


def write_wav(path, pcm, fs=8000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(np.asarray(pcm, np.int16).tobytes())


# ---- modulation model and receiver ------------------------------------------------------------

def dc_block(x, n=1920):
    """x less its running mean over n samples (40 ms at 48 kHz, a frame): a capture's carrier
    estimate spans the FM ident and the raw mode's burst, which the chip puts kHz apart"""
    x = np.asarray(x, float)
    if len(x) < n:
        return x - np.mean(x)
    c = np.cumsum(np.concatenate([[0.0], x]))
    m = (c[n:] - c[:-n]) / n
    m = np.concatenate([np.full(n // 2, m[0]), m, np.full(len(x) - len(m) - n // 2, m[-1])])
    return x - m


def rrc(alpha, sps, span=8):
    t = np.arange(-span * sps, span * sps + 1) / sps
    h = np.zeros_like(t)
    for i, x in enumerate(t):
        if abs(x) < 1e-9:
            h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * x) - 1) < 1e-9:
            h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha))
                                         + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else:
            h[i] = (np.sin(np.pi * x * (1 - alpha)) + 4 * alpha * x * np.cos(np.pi * x * (1 + alpha))) \
                / (np.pi * x * (1 - (4 * alpha * x) ** 2))
    return h / np.sum(h)


def symbols(data):
    """bytes -> symbols (+-1, +-3), MSB dibit first"""
    b = np.frombuffer(bytes(data), np.uint8)
    d = np.stack([b >> 6 & 3, b >> 4 & 3, b >> 2 & 3, b & 3], axis=1).ravel()
    lut = np.array([LEVEL[k] for k in range(4)], float)
    return lut[d]


def modulate(data, fs=48000, alpha=0.2, outer_hz=OUTER_HZ):
    """the discriminator (Hz) a flat FM receiver sees: the chip's RRC (alpha 0.2) by default"""
    sps = fs // SYM_RATE
    up = np.zeros(len(data) * 4 * sps)
    up[::sps] = symbols(data)
    return np.convolve(up, rrc(alpha, sps) * sps, "same") * outer_hz / 3.0


SYNCS = {"lsf": SYNC_LSF, "stream": SYNC_STREAM, "packet": SYNC_PACKET, "bert": SYNC_BERT}


def _viterbi(soft):
    """soft: +1 = bit 0 ... -1 = bit 1, 0 = erasure, pairs (G1, G2). -> bits (flush included)"""
    n = len(soft) // 2
    INF = 1e18
    metric = np.full(16, INF)
    metric[0] = 0.0
    hist = np.zeros((n, 16), np.int8)
    # state = last 4 input bits (m >> 1 after the shift, newest in bit 0)
    nxt_out = {}
    for s in range(16):
        for b in (0, 1):
            m = ((s << 1) | b) & 0x1F
            o1 = bin(m & 0x19).count("1") & 1
            o2 = bin(m & 0x17).count("1") & 1
            nxt_out[(s, b)] = (m & 0xF, 1 - 2 * o1, 1 - 2 * o2)
    for k in range(n):
        a, c = soft[2 * k], soft[2 * k + 1]
        new = np.full(16, INF)
        for s in range(16):
            if metric[s] >= INF:
                continue
            for b in (0, 1):
                ns, e1, e2 = nxt_out[(s, b)]
                mm = metric[s] - a * e1 - c * e2
                if mm < new[ns]:
                    new[ns] = mm
                    hist[k, ns] = (s << 1) | b
        metric = new
    s = int(np.argmin(metric))
    bits = []
    for k in range(n - 1, -1, -1):
        v = int(hist[k, s])
        bits.append(v & 1)
        s = v >> 1
    return bits[::-1]


def _soft_from_frame(frame46):
    bits = deinterleave(list(to_bits(decorrelate(frame46))))
    return np.array([1.0 - 2.0 * b for b in bits])


def decode_frame(kind, frame46):
    """one frame's 46 bytes after its sync -> dict"""
    soft = _soft_from_frame(frame46)
    if kind == "lsf":
        bits = _viterbi(depuncture(soft, P1, 488))[:240]
        l30 = to_bytes(bits)
        ok = crc16(l30[:28]) == int.from_bytes(l30[28:], "big")
        return {"kind": "lsf", "ok": ok, "src": callsign_text(l30[6:12]), "dst": callsign_text(l30[:6]),
                "type": int.from_bytes(l30[12:14], "big"), "lsf": l30}
    if kind == "stream":
        hard = [0 if s > 0 else 1 for s in soft]
        cws = [int("".join(map(str, hard[24 * i:24 * i + 24])), 2) for i in range(4)]
        blocks = [golay_decode(c) for c in cws]
        bits = _viterbi(depuncture(soft[96:], P2, 296))[:144]
        body = to_bytes(bits)
        fn = int.from_bytes(body[:2], "big")
        return {"kind": "stream", "fn": fn & 0x7FFF, "last": bool(fn & 0x8000), "payload": body[2:],
                "lich_ok": None not in blocks, "lich_idx": (blocks[3] >> 5 & 7) if blocks[3] is not None else None}
    if kind == "packet":
        bits = _viterbi(depuncture(soft, P3, 424))[:208]
        body = to_bytes(bits)
        return {"kind": "packet", "data": body[:25], "eof": bool(body[25] & 0x80), "counter": body[25] >> 2 & 0x1F}
    if kind == "bert":
        bits = _viterbi(depuncture(soft, P2, 402))[:197]
        return {"kind": "bert", "bits": bits}
    raise ValueError(kind)


def decode_bytes(data):
    """a byte stream as the chip sends it (frames on 48 byte boundaries, any order) -> frames"""
    out = []
    for i in range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES):
        f = data[i:i + FRAME_BYTES]
        for kind, sw in SYNCS.items():
            if f[:2] == sw:
                out.append(decode_frame(kind, f[2:]))
    return out


def summarize(frames):
    """what a run's frames add up to: LSFs, packets (CRC checked), stream frames, BERT errors"""
    s = {"lsf": 0, "lsf_bad": 0, "packets": 0, "packets_bad": 0, "texts": [], "stream": 0,
         "lich_bad": 0, "bert_frames": 0, "bert_bits": 0, "bert_errors": 0, "payload": b""}
    pkt = b""
    for f in frames:
        if f["kind"] == "lsf":
            s["lsf"] += 1
            s["lsf_bad"] += not f["ok"]
            if f["ok"]:
                s["src"] = f["src"]
        elif f["kind"] == "packet":
            pkt += f["data"][:f["counter"]] if f["eof"] else f["data"]
            if f["eof"]:
                ok = len(pkt) >= 3 and crc16(pkt[:-2]) == int.from_bytes(pkt[-2:], "big")
                s["packets"] += ok
                s["packets_bad"] += not ok
                if ok and pkt[0] == SMS:
                    s["texts"].append(pkt[1:-3].decode("ascii", "replace"))
                pkt = b""
        elif f["kind"] == "stream":
            s["stream"] += 1
            s["lich_bad"] += not f["lich_ok"]
            s["payload"] += f["payload"]
        elif f["kind"] == "bert":
            bits = np.array(f["bits"], np.uint8)
            errs = int(np.min([(np.roll(_PRBS_SEQ2, -k)[:197] != bits).sum() for k in range(511)]))
            s["bert_frames"] += 1
            s["bert_bits"] += 197
            s["bert_errors"] += errs
            s.setdefault("bert_per_frame", []).append(errs)
    return s


def runs(frames, gap=0.040, tol=0.001):
    """frames in runs of one key-up's 40 ms spacing -> lists; a lone sync match (noise, or data
    that happens to look like a sync) has no neighbour and makes no run"""
    out, cur = [], []
    for f in sorted(frames, key=lambda f: f["at"]):
        if cur and abs(f["at"] - cur[-1]["at"] - gap) <= tol:
            cur.append(f)
        else:
            if len(cur) > 1:
                out.append(cur)
            cur = [f]
    if len(cur) > 1:
        out.append(cur)
    return out


def demod(disc, fs=48000, alpha=0.5):
    """discriminator (Hz) -> frames in runs (see runs()). Two passes: frames found with DC blocked
    over a frame, then each run's own DC (its frames' mean, where the data averages out) taken
    off around the run and the frames found again"""
    disc = np.asarray(disc, float)
    first = [f for r in runs(_demod(dc_block(disc, 40 * fs // 1000), fs, alpha)) for f in r]
    if not first:
        return []
    d2 = dc_block(disc, 40 * fs // 1000)
    pad = int(0.2 * fs)
    for r in runs(first):
        a, b = int(r[0]["at"] * fs), int((r[-1]["at"] + 0.040) * fs)
        dc = float(np.mean(disc[a:b]))
        d2[max(0, a - pad):b + pad] = disc[max(0, a - pad):b + pad] - dc
    return [f for r in runs(_demod(d2, fs, alpha)) for f in r]


def _demod(disc, fs=48000, alpha=0.5):
    """discriminator (Hz, DC off) -> frames, with each frame's sampling and level figures.
    RRC matched filter, syncword correlation, the outer level from the sync's +-3 symbols"""
    sps = fs // SYM_RATE
    y = np.convolve(disc, rrc(alpha, sps), "same")
    pats = {k: symbols(v) for k, v in SYNCS.items()}
    out, i, n = [], 0, len(y)
    span = FRAME_BYTES * 4 * sps
    while i + span + sps < n:
        best = None
        for k, p in pats.items():
            idx = i + np.arange(8) * sps
            if idx[-1] >= n:
                break
            v = y[idx]
            sc = v @ p / (np.linalg.norm(v) * np.linalg.norm(p) + 1e-9)
            if best is None or sc > best[0]:
                best = (sc, k)
        if best is None or best[0] < 0.93 or np.max(np.abs(y[i + np.arange(8) * sps])) < 300:
            i += 1
            continue
        # refine: the best phase within +-sps/2 and the frame's own levels
        cands = []
        for d in range(-sps // 2, sps // 2 + 1):
            idx = i + d + np.arange(8) * sps
            if idx[0] < 0 or idx[-1] >= n:
                continue
            v = y[idx]
            cands.append((v @ pats[best[1]], d))
        _, d = max(cands)
        start = i + d
        idx = start + np.arange(192) * sps
        if idx[-1] >= n:
            break
        v = y[idx]
        sync = pats[best[1]]
        gain = (v[:8] @ sync) / (sync @ sync)
        if gain <= 0:
            i += 1
            continue
        lv = v / gain
        dibits = [DIBIT[int(min((3, 1, -1, -3), key=lambda L: abs(x - L)))] for x in lv]
        by = bytes(dibits[j] << 6 | dibits[j + 1] << 4 | dibits[j + 2] << 2 | dibits[j + 3]
                   for j in range(0, 192, 4))
        if by[:2] == SYNCS[best[1]]:
            fr = decode_frame(best[1], by[2:])
            fr["at"] = start / fs
            fr["raw"] = by
            fr["outer_hz"] = gain * 3.0
            fr["levels"] = {L: float(np.mean(v[np.isclose(np.round((lv + 3) / 2) * 2 - 3, L)]))
                            for L in (-3, -1, 1, 3) if np.any(np.isclose(np.round((lv + 3) / 2) * 2 - 3, L))}
            out.append(fr)
            i = start + span - sps // 2
        else:
            i += 1
    return out


ORDERS = {"msb": (0, 1, 2, 3), "lsb": (3, 2, 1, 0), "nibbles swapped": (2, 3, 0, 1),
          "pairs swapped": (1, 0, 3, 2)}


def symbol_track(disc, fs=48000, alpha=0.5, win=1200):
    """discriminator -> one matched-filter sample a symbol, the phase picked per window of `win`
    symbols for the most energy (follows a few hundred ppm of clock between radio and capture)"""
    sps = fs // SYM_RATE
    y = np.convolve(dc_block(disc, 40 * fs // 1000), rrc(alpha, sps), "same")
    out = []
    for w0 in range(0, len(y) - sps, win * sps):
        seg = y[w0:w0 + (win + 1) * sps]
        n = min(win, len(seg) // sps - 1)
        ph = max(range(sps), key=lambda d: float(np.mean(seg[d::sps][:n] ** 2)))
        out.append(seg[ph::sps][:n])
    return np.concatenate(out) if out else np.zeros(0)


def hypothesis_symbols(data, order, perm):
    """the symbols `data` makes if the chip sends a byte's dibits in `order` (positions, MSB dibit
    = 0) and maps dibit d to level perm[d]"""
    b = np.frombuffer(bytes(data), np.uint8)
    d = np.stack([b >> 6 & 3, b >> 4 & 3, b >> 2 & 3, b & 3], axis=1)[:, list(order)].ravel()
    return np.asarray(perm, float)[d]


def probe(r, data, slot=144):
    """which byte-to-symbol rule the received symbols `r` follow, against the bytes the module sent:
    every dibit order in ORDERS x every level map, by cross-correlation -> report lines. Then, under
    the best rule, the symbol errors where the run lines up, by place in the 30 ms slot (144
    symbols), and which sent slot each received slot matches best (a half sent twice or out of
    turn shows as a repeated or skipped number)"""
    import itertools
    from scipy import signal
    r = r - np.mean(r)
    rn = r / (np.std(r) + 1e-9)
    res = []
    for oname, order in ORDERS.items():
        for perm in itertools.permutations((3, 1, -1, -3)):
            e = hypothesis_symbols(data, order, perm)
            c = signal.fftconvolve(rn, e[::-1], "valid") / (np.linalg.norm(e) * np.sqrt(len(e)))
            k = int(np.argmax(c))
            res.append((float(c[k]), k, oname, perm, e))
    res.sort(key=lambda t: -t[0])
    lines = ["rule (dibit order; level of dibit 00 01 10 11): correlation"]
    for sc, k, oname, perm, _ in res[:4]:
        lines.append(f"  {oname:16s} {perm[0]:+d} {perm[1]:+d} {perm[2]:+d} {perm[3]:+d}: {sc:.3f} at symbol {k}")
    sc, k, oname, perm, e = res[0]
    ns = len(e) // slot
    es = e[:ns * slot].reshape(ns, slot)
    pos, wrong, got, gains = k, np.zeros(slot, int), [], []
    for i in range(ns):          # follow the clock slot by slot (+-4 symbols), any sent slot near i
        best = None
        for sh in range(-4, 5):
            a = pos + sh
            if a < 0 or a + slot > len(r):
                continue
            c = r[a:a + slot]
            for jj in range(max(0, i - 3), min(ns, i + 4)):
                g = float(c @ es[jj] / (es[jj] @ es[jj]))
                if g <= 0:
                    continue
                qq = np.clip(np.round((c / g + 3) / 2), 0, 3) * 2 - 3
                n = int((qq != es[jj]).sum())
                if best is None or n < best[0]:
                    best = (n, a, qq, g, jj)
        if best is None:
            break
        n, a, qq, g, jj = best
        wrong += qq != es[i]
        gains.append(g)
        got.append(str(jj) if n < 0.1 * slot else f"{jj}?" if n < 0.4 * slot else "-")
        pos = a + slot
    tot = int(wrong.sum())
    lines.append(f"best: {oname}, levels {perm}: {tot} of {len(got) * slot} symbols wrong "
                 f"(SER {tot / max(1, len(got) * slot):.2e}), outer {np.median(gains) * 3:.0f} Hz")
    if tot:
        lines.append("wrong symbols by place in the slot (16 symbol bins): "
                     + " ".join(str(int(x)) for x in wrong.reshape(-1, 16).sum(1)))
        lines.append("received slot i matches sent slot: " + " ".join(got))
    return lines


def report(frames, s):
    lines = []
    if s["lsf"] or s["lsf_bad"]:
        lines.append(f"LSF {s['lsf']} good, {s['lsf_bad']} bad, source {s.get('src', '?')}")
    if s["packets"] or s["packets_bad"]:
        lines.append(f"packets {s['packets']} CRC good, {s['packets_bad']} bad: " + "; ".join(s["texts"]))
    if s["stream"]:
        lines.append(f"stream frames {s['stream']}, LICH bad {s['lich_bad']}")
    if s["bert_frames"]:
        ber = s["bert_errors"] / max(1, s["bert_bits"])
        lines.append(f"BERT {s['bert_frames']} frames, {s['bert_errors']} of {s['bert_bits']} bits wrong (BER {ber:.2e})")
        bad = [(i, e) for i, e in enumerate(s.get("bert_per_frame", [])) if e]
        if bad:
            lines.append(f"BERT frames with errors ({len(bad)}): "
                         + " ".join(f"#{i}:{e}" for i, e in bad[:30]) + (" ..." if len(bad) > 30 else ""))
    lv = {}
    for f in frames:
        for k, v in f.get("levels", {}).items():
            lv.setdefault(k, []).append(v)
    if lv:
        lines.append("levels Hz " + " ".join(f"{k:+d}:{np.mean(v):+.0f}" for k, v in sorted(lv.items())))
    t = [f["at"] for f in frames if "at" in f]
    if len(t) > 3:
        d = np.diff(t)
        d = d[(d > 0.035) & (d < 0.045)]
        if len(d):
            rate = SYM_RATE * 0.040 / np.median(d)
            lines.append(f"frame spacing median {np.median(d) * 1e3:.3f} ms: symbol rate {rate:.1f}"
                         f" ({(rate / SYM_RATE - 1) * 1e6:+.0f} ppm, from the capture clock)")
    return lines or ["no M17 frames found"]


def decode_capture(cap, baseband=None):
    from . import c6phy
    iq, meta = c6phy.read_capture(cap)
    fs = int(meta.get("fs", 240_000))                     # pluto_bench.py rx saves 240 kS/s
    off, on, disc = c6phy.capture_disc(iq, fs, bw_hz=12000.0)
    if disc is None:
        return ["no signal in the capture"]
    frames = demod(disc)
    lines = [f"carrier {off:+.0f} Hz from the capture's centre (ident and burst together)"]
    if frames:
        at = np.array([f["at"] for f in frames])
        burst = np.concatenate([disc[int(t * 48000):int((t + 0.04) * 48000)] for t in at])
        lines.append(f"M17 burst {off + float(np.mean(burst)):+.0f} Hz from the capture's centre")
    lines += report(frames, summarize(frames))
    if baseband:
        from scipy import signal
        bb = signal.resample_poly(disc, 1, 2)                  # 48 -> 24 kHz
        np.clip(bb / OUTER_HZ * 8000, -32768, 32767).astype("<i2").tofile(baseband)
        lines.append(f"baseband {baseband}: 24 kHz s16, +2.4 kHz = 8000 (m17_replay_tool file.raw)")
    s = summarize(frames)
    if s["payload"]:
        wav = os.path.splitext(cap)[0] + "-voice.wav"
        write_wav(wav, c2dec(s["payload"]))
        lines.append(f"voice {wav}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m modem.m17", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("header")
    h.add_argument("plan")
    h.add_argument("out")
    h.add_argument("--c2")
    p = sub.add_parser("plan")
    p.add_argument("plan")
    p.add_argument("--c2")
    e = sub.add_parser("c2enc")
    e.add_argument("wav")
    e.add_argument("out")
    d = sub.add_parser("decode")
    d.add_argument("cap")
    d.add_argument("--baseband")
    pr = sub.add_parser("probe", help="which symbol rule a capture follows, against a plan")
    pr.add_argument("cap", help="Pluto .cf32, or a 24 kHz s16 .raw from decode --baseband")
    pr.add_argument("plan")
    pr.add_argument("--c2")
    s = sub.add_parser("sim")
    s.add_argument("plan")
    s.add_argument("--c2")
    s.add_argument("--alpha", type=float, default=0.2)
    s.add_argument("--outer-hz", type=float, default=OUTER_HZ)
    s.add_argument("--noise-hz", type=float, default=0.0)
    a = ap.parse_args(argv)
    c2 = open(a.c2, "rb").read() if getattr(a, "c2", None) else None
    if a.cmd == "header":
        data = build(a.plan, c2)
        note = f" --c2 {os.path.basename(a.c2)}" if a.c2 else ""
        with open(a.out, "w") as f:
            f.write(header(a.plan, data, note))
        print(f"{a.out}: {len(data)} bytes, {len(data) // FRAME_BYTES} frames, {len(data) / 1.2:.0f} ms")
    elif a.cmd == "plan":
        data = build(a.plan, c2)
        s_ = summarize(decode_bytes(data))
        print(f"{a.plan}: {len(data)} bytes, {len(data) // FRAME_BYTES} frames, {len(data) / 1.2:.0f} ms")
        print("\n".join(report([], s_)))
    elif a.cmd == "c2enc":
        c = c2enc(read_wav8k(a.wav))
        open(a.out, "wb").write(c)
        print(f"{a.out}: {len(c)} bytes, {len(c) / 8 * 0.02:.2f} s of Codec2 3200")
    elif a.cmd == "sim":
        data = build(a.plan, c2)
        disc = modulate(data, alpha=a.alpha, outer_hz=a.outer_hz)
        if a.noise_hz:
            disc = disc + np.random.default_rng(1).normal(0, a.noise_hz, len(disc))
        fr = demod(disc)
        print("\n".join(report(fr, summarize(fr))))
    elif a.cmd == "probe":
        data = build(a.plan, c2) + eot() * 2
        if a.cap.endswith(".raw"):
            from scipy import signal
            disc = signal.resample_poly(np.fromfile(a.cap, "<i2").astype(float) / 8000 * OUTER_HZ, 2, 1)
        else:
            from . import c6phy
            iq, meta = c6phy.read_capture(a.cap)
            _, _, disc = c6phy.capture_disc(iq, int(meta.get("fs", 240_000)), bw_hz=12000.0)
        print("\n".join(probe(symbol_track(disc), data)))
    else:
        print("\n".join(decode_capture(a.cap, a.baseband)))


if __name__ == "__main__":
    sys.exit(main())
