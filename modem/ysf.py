"""Yaesu System Fusion (YSF) frames for the UV-380's HR_C6000 physical layer mode, and their decode
from a Pluto capture: C4FM 4FSK with Yaesu framing straight out of the chip (docs/uv380-ysf.md).

Written from the public YSF frame layout (the frame and field structure that open-source hotspot
and decoder projects document); no Yaesu code or firmware is used or redistributed. The frame
codec started as sotto's modem/ysf.py (MIT, same author); modem/c4fm.py imports it from here too.

  python3 -m modem.ysf header PLAN OUT.h      a module's bytes (ysftx_PLAN.h): dn, data
  python3 -m modem.ysf plan PLAN              frames, air time, and the plan decoded back
  python3 -m modem.ysf decode CAP.cf32|.wav   a Pluto capture: frames, FICH, callsigns, DN voice
                                              frames (silence), data text, deviation per level
  python3 -m modem.ysf sim PLAN [--noise-hz N] the chip's shape at 2.7 kHz, decoded

One YSF frame is 100 ms = 480 symbols at 4800 a second = 960 bits = 120 bytes:
  sync (40 bits) | FICH (200 bits) | payload (720 bits = 5 sections of 144 bits, 20 ms each)

The FICH carries 32 bits of frame information plus a CRC-16 (CCITT, init 0, inverted), as four
Golay(24,12) words, a K=5 rate 1/2 convolutional code (g1 = 1 + D^3 + D^4, g2 = 1 + D + D^2 + D^4,
4 flush bits) and a 5 x 20 dibit interleaver. Payload by data type (DT):
  header / terminator (FI 0 / 2): two CSD fields of 20 bytes (dest+src, downlink+uplink), each
      whitened, CRC-16, convolutional code, 9 x 20 dibit interleaver; CSD1 takes the first 72 bits
      of each section, CSD2 the last 72
  V/D mode 2 "DN" (DT 2): per section 40 DCH bits then 104 VCH bits. The DCH (10 bytes a frame:
      FN 0 dest, 1 src, 2 downlink, 3 uplink, 4-5 remarks, 6-7 data) is coded like a CSD with a
      5 x 20 interleaver. The VCH is one 49 bit AMBE+2 frame: its first 27 bits each sent three
      times, the other 22 once, a pad bit, whitened, a 26 x 4 bit interleaver
  Data full rate (DT 1): CSD-coded 20 byte DCH1 and DCH2 a frame (FN 0: CSD1 and CSD2; FN 1:
      CSD3 and data; later frames data)
Whitening is the PN9 sequence x^9 + x^5 + 1, seed 0x1C9: bytes 93 D7 51 21 ...

C4FM: dibit 01 +3, 00 +1, 10 -1, 11 -3 (the C6000's own map, and M17's), +3 = +2.7 kHz (the inner
levels +-0.9 kHz): the chip's DMR deviation (+-1944 Hz) times 1.39, which is what RadioFoundry's
C4FM port does to the same two-point registers. The chip's shaping is DMR's RRC alpha 0.2, the
same roll-off YSF uses. A transmission is a 0x77 preamble, a header frame, communication frames
FN 0..FT over and over, a terminator.

All bit arrays are numpy int8 arrays of 0/1, most significant bit first.
SPDX-License-Identifier: MIT
"""
import argparse
import os
import sys
from itertools import combinations

import numpy as np

SYNC_BYTES = bytes([0xD4, 0x71, 0xC9, 0x63, 0x4D])
FRAME_BITS = 960
FRAME_SYMS = 480
FRAME_BYTES = 120
SYNC_BITS = 40
FICH_BITS = 200
PAYLOAD_BITS = 720
PAYLOAD_OFFSET = SYNC_BITS + FICH_BITS
SYM_RATE = 4800
OUTER_HZ = 2700.0                       # YSF: +3 = +2.7 kHz
IDENT = "AG7EW"
PREAMBLE = 0x77                         # +3 -3 +3 -3 ...: what hotspots send before a YSF frame
MAX_BYTES = 4800                        # what a module key-up carries (c6ysftx.c: 4 s)

FI_HEADER, FI_COMMS, FI_TERMINATOR, FI_TEST = 0, 1, 2, 3
DT_VD_MODE1, DT_DATA_FR, DT_VD_MODE2, DT_VOICE_FR = 0, 1, 2, 3
DT_NAMES = {0: "V/D mode 1", 1: "data FR", 2: "V/D mode 2 (DN)", 3: "voice FR (VW)"}
FI_NAMES = {0: "header", 1: "comms", 2: "terminator", 3: "test"}

LEVEL = {0b01: 3, 0b00: 1, 0b10: -1, 0b11: -3}
DIBIT = {v: k for k, v in LEVEL.items()}


def _pn9(nbits, seed=0x1C9):
    s, out = seed, []
    for _ in range(nbits):
        out.append(s & 1)
        s = (s >> 1) | ((((s >> 4) ^ s) & 1) << 8)
    return np.array(out, np.int8)


WHITENING = _pn9(160)
WHITENING_BYTES = np.packbits(WHITENING.astype(np.uint8)).tobytes()   # 93 d7 51 21 9c 2f ...


def bytes_to_bits(b):
    return np.unpackbits(np.frombuffer(bytes(b), np.uint8)).astype(np.int8)


def bits_to_bytes(bits):
    return np.packbits(np.asarray(bits, np.uint8)).tobytes()


SYNC = bytes_to_bits(SYNC_BYTES)


def _dibit_interleaver(cols, rows=20):
    """dibit i of the coded stream goes to field bits n, n+1"""
    i = np.arange(cols * rows)
    return (i % cols) * 40 + (i // cols) * 2


IL_FICH = _dibit_interleaver(5)       # 100 dibits -> 200 bits
IL_CSD = _dibit_interleaver(9)        # 180 dibits -> 360 bits
IL_DCH2 = _dibit_interleaver(5)       # DN DCH: 100 dibits -> 200 bits
IL_VCH = (np.arange(104) % 26) * 4 + np.arange(104) // 26   # VCH bit i -> field bit IL_VCH[i]


# ---------------------------------------------------------------- CRC-16 (CCITT, inverted)
def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc ^ 0xFFFF


def add_crc(data):
    c = crc16(data)
    return bytes(data) + bytes([c >> 8, c & 0xFF])


def check_crc(data):
    return crc16(data[:-2]) == (data[-2] << 8 | data[-1])


# ---------------------------------------------------------------- Golay(24,12)
_GOLAY_POLY = 0xC75   # x^11 + x^10 + x^6 + x^5 + x^4 + x^2 + 1


def _golay23(d):
    r = d << 11
    for i in range(22, 10, -1):
        if r & (1 << i):
            r ^= _GOLAY_POLY << (i - 11)
    return (d << 11) | r


def golay24_encode(d):
    """12 data bits -> 24-bit codeword: data | 11 parity | overall even parity."""
    c = _golay23(d)
    return (c << 1) | (bin(c).count("1") & 1)


def _syndrome23(c):
    r = c
    for i in range(22, 10, -1):
        if r & (1 << i):
            r ^= _GOLAY_POLY << (i - 11)
    return r


# The (23,12) Golay code is perfect: every syndrome maps to one pattern of weight <= 3.
_SYN_TABLE = {}
for _w in range(4):
    for _pos in combinations(range(23), _w):
        _e = sum(1 << p for p in _pos)
        _SYN_TABLE.setdefault(_syndrome23(_e), _e)


def golay24_decode(c):
    """Returns (data, ok). ok is False when the overall parity shows 4 errors."""
    c23 = c >> 1
    e = _SYN_TABLE[_syndrome23(c23)]
    fixed = c23 ^ e
    nerr = bin(e).count("1")
    parity_ok = ((bin(fixed).count("1") + (c & 1)) & 1) == 0
    ok = nerr < 3 or parity_ok
    return fixed >> 11, ok


# ---------------------------------------------------------------- K=5 rate-1/2 conv code
_G5 = ((1, 0, 0, 1, 1), (1, 1, 1, 0, 1))


def conv5_encode(bits):
    reg = [0, 0, 0, 0]
    out = np.zeros(2 * len(bits), np.int8)
    for i, b in enumerate(bits):
        w = [int(b)] + reg
        out[2 * i] = sum(t * x for t, x in zip(_G5[0], w)) & 1
        out[2 * i + 1] = sum(t * x for t, x in zip(_G5[1], w)) & 1
        reg = [int(b)] + reg[:3]
    return out


def _conv5_trellis():
    prev = np.zeros((16, 2), int)
    outs = np.zeros((16, 2, 2), int)
    for s in range(16):
        b = s >> 3
        for k, p in enumerate((((s & 7) << 1), ((s & 7) << 1) | 1)):
            prev[s, k] = p
            d = [(p >> 3) & 1, (p >> 2) & 1, (p >> 1) & 1, p & 1]
            w = [b] + d
            outs[s, k] = [sum(t * x for t, x in zip(g, w)) & 1 for g in _G5]
    return prev, 1 - 2 * outs


_P5, _S5 = _conv5_trellis()


def conv5_decode(llr, n_bits, terminated=True):
    """Soft Viterbi. llr: 2*n_bits values, positive means bit 0. Returns n_bits bits."""
    llr = np.asarray(llr, float).reshape(n_bits, 2)
    pm = np.full(16, -1e9)
    pm[0] = 0.0
    dec = np.zeros((n_bits, 16), np.int8)
    for i in range(n_bits):
        cand = pm[_P5] + _S5[:, :, 0] * llr[i, 0] + _S5[:, :, 1] * llr[i, 1]
        k = np.argmax(cand, 1)
        dec[i] = k
        pm = cand[np.arange(16), k]
        pm -= pm.max()
    s = 0 if terminated else int(np.argmax(pm))
    out = np.zeros(n_bits, np.int8)
    for i in range(n_bits - 1, -1, -1):
        out[i] = s >> 3
        s = _P5[s, dec[i, s]]
    return out


def _conv_field(data_bytes, il):
    """bytes (CRC included) + 4 flush bits -> convolutional code -> dibit interleaver"""
    bits = np.concatenate([bytes_to_bits(data_bytes), np.zeros(4, np.int8)])
    coded = conv5_encode(bits)
    out = np.zeros(2 * len(il), np.int8)
    out[il] = coded[0::2]
    out[il + 1] = coded[1::2]
    return out


def _unconv_field(llr, il, nbytes):
    llr = np.asarray(llr, float)
    coded = np.empty(2 * len(il))
    coded[0::2] = llr[il]
    coded[1::2] = llr[il + 1]
    return bits_to_bytes(conv5_decode(coded, len(il))[:8 * nbytes])


def _whiten(b):
    return bytes(x ^ w for x, w in zip(b, WHITENING_BYTES))


# ---------------------------------------------------------------- FICH
class FICH:
    def __init__(self, fi=FI_COMMS, cs=2, cm=0, bn=0, bt=0, fn=0, ft=6, dev=False,
                 mr=0, voip=False, dt=DT_VOICE_FR, sql=False, dgid=0, sotto=False):
        self.fi, self.cs, self.cm, self.bn, self.bt = fi, cs, cm, bn, bt
        self.fn, self.ft, self.dev, self.mr, self.voip, self.dt = fn, ft, dev, mr, voip, dt
        self.sql, self.dgid, self.sotto = sql, dgid, sotto

    def to_bytes(self):
        b0 = (self.fi << 6) | (self.cs << 4) | (self.cm << 2) | self.bn
        b1 = (self.bt << 6) | (self.fn << 3) | self.ft
        # bit 7 of byte 2 is unused by Fusion radios; sotto uses it (provisionally) as its flag
        b2 = (self.sotto << 7) | (self.dev << 6) | (self.mr << 3) | (self.voip << 2) | self.dt
        b3 = (self.sql << 7) | self.dgid
        return bytes([b0, b1, b2, b3])

    @classmethod
    def from_bytes(cls, b):
        return cls(fi=b[0] >> 6, cs=(b[0] >> 4) & 3, cm=(b[0] >> 2) & 3, bn=b[0] & 3,
                   bt=b[1] >> 6, fn=(b[1] >> 3) & 7, ft=b[1] & 7, dev=bool(b[2] & 0x40),
                   mr=(b[2] >> 3) & 7, voip=bool(b[2] & 4), dt=b[2] & 3,
                   sql=bool(b[3] & 0x80), dgid=b[3] & 0x7F, sotto=bool(b[2] & 0x80))

    def __eq__(self, o):
        return isinstance(o, FICH) and self.to_bytes() == o.to_bytes()

    def __repr__(self):
        return (f"FICH(fi={self.fi}, fn={self.fn}/{self.ft}, dt={self.dt}, dgid={self.dgid}, "
                f"sotto={self.sotto})")

    def encode(self):
        """-> 200 interleaved bits."""
        raw = add_crc(self.to_bytes())                          # 48 bits
        word = int.from_bytes(raw, "big")
        cw = b"".join(golay24_encode((word >> (36 - 12 * k)) & 0xFFF).to_bytes(3, "big")
                      for k in range(4))
        return _conv_field(cw, IL_FICH)

    @classmethod
    def decode(cls, llr):
        """llr: 200 soft values for the FICH field. Returns FICH or None."""
        cw = _unconv_field(llr, IL_FICH, 12)
        word = 0
        for k in range(4):
            d, ok = golay24_decode(int.from_bytes(cw[3 * k:3 * k + 3], "big"))
            if not ok:
                return None
            word = (word << 12) | d
        raw = word.to_bytes(6, "big")
        if not check_crc(raw):
            return None
        return cls.from_bytes(raw[:4])


# ---------------------------------------------------------------- CSD / data FR DCH (20 bytes)
def csd_encode(field20):
    """20 bytes -> 360 interleaved bits (whitened, CRC over the whitened bytes)."""
    assert len(field20) == 20
    return _conv_field(add_crc(_whiten(field20)), IL_CSD)


def csd_decode(llr):
    raw = _unconv_field(llr, IL_CSD, 22)
    return _whiten(raw[:20]) if check_crc(raw) else None


def _section_positions(first, n):
    """payload bit positions of bits first..first+n-1 of each of the 5 sections"""
    return np.concatenate([k * 144 + first + np.arange(n) for k in range(5)])


CSD_POS = (_section_positions(0, 72), _section_positions(72, 72))
DCH2_POS = _section_positions(0, 40)                       # DN: the DCH
VCH_POS = [k * 144 + 40 + np.arange(104) for k in range(5)]  # DN: one VCH a section


def callsign_field(s):
    return s.upper().encode("ascii")[:10].ljust(10, b" ")


def header_payload(dest="ALL", src=IDENT, csd2=b" " * 20):
    p = np.zeros(PAYLOAD_BITS, np.int8)
    p[CSD_POS[0]] = csd_encode(callsign_field(dest) + callsign_field(src))
    p[CSD_POS[1]] = csd_encode(bytes(csd2))
    return p


def decode_header_payload(llr):
    c1 = csd_decode(np.asarray(llr)[CSD_POS[0]])
    c2 = csd_decode(np.asarray(llr)[CSD_POS[1]])
    if c1 is None:
        return None
    return {"dest": c1[:10].decode("ascii", "replace").strip(),
            "src": c1[10:].decode("ascii", "replace").strip(), "csd2": c2}


def data_fr_payload(dch1, dch2):
    p = np.zeros(PAYLOAD_BITS, np.int8)
    p[CSD_POS[0]] = csd_encode(bytes(dch1))
    p[CSD_POS[1]] = csd_encode(bytes(dch2))
    return p


def decode_data_fr_payload(llr):
    llr = np.asarray(llr)
    return csd_decode(llr[CSD_POS[0]]), csd_decode(llr[CSD_POS[1]])


# ---------------------------------------------------------------- V/D mode 2 (DN)
def dch2_encode(field10):
    """10 bytes -> 200 interleaved bits"""
    assert len(field10) == 10
    return _conv_field(add_crc(_whiten(field10)), IL_DCH2)


def dch2_decode(llr):
    raw = _unconv_field(llr, IL_DCH2, 12)
    return _whiten(raw[:10]) if check_crc(raw) else None


def ambe_silence():
    """an AMBE+2 (2450 bit/s) silence frame, 49 bits: b0 = 124 (bits 0-3 and 37-39 = 1111 100),
    everything else 0. b0 120-123 erasure, 124-125 silence, 126-127 tone (P25 half rate)"""
    a = np.zeros(49, np.int8)
    a[0:4] = 1
    a[37] = 1
    return a


def ambe_b0(a):
    return int(a[0]) << 6 | int(a[1]) << 5 | int(a[2]) << 4 | int(a[3]) << 3 | \
        int(a[37]) << 2 | int(a[38]) << 1 | int(a[39])


def vch_encode(ambe49):
    """49 AMBE bits -> 104 field bits: 27 bits x3, 22 bits, a 0 pad; whitened; interleaved"""
    a = np.asarray(ambe49, np.int8)
    v = np.concatenate([np.repeat(a[:27], 3), a[27:], [0]]).astype(np.int8) ^ WHITENING[:104]
    out = np.zeros(104, np.int8)
    out[IL_VCH] = v
    return out


def vch_decode(llr104):
    """-> (49 bits, triplets that disagreed). llr: positive = 0"""
    v = np.asarray(llr104, float)[IL_VCH] * (1 - 2 * WHITENING[:104])
    trip = v[:81].reshape(27, 3)
    hard3 = (trip < 0).astype(int)
    bad = int(np.sum((hard3.sum(1) % 3) != 0))
    a = np.concatenate([(trip.sum(1) < 0), v[81:103] < 0]).astype(np.int8)
    return a, bad


def dn_payload(dch10, ambe_frames):
    p = np.zeros(PAYLOAD_BITS, np.int8)
    p[DCH2_POS] = dch2_encode(dch10)
    for k in range(5):
        p[VCH_POS[k]] = vch_encode(ambe_frames[k])
    return p


def decode_dn_payload(llr):
    llr = np.asarray(llr)
    dch = dch2_decode(llr[DCH2_POS])
    voice = [vch_decode(llr[VCH_POS[k]]) for k in range(5)]
    return dch, voice


RADIO_ID = "BLOWF"                  # our 5-character radio ID (a Yaesu radio's own is fixed)


def dn_dest(rid=RADIO_ID):
    """the destination a Yaesu radio sends on a local DN call: five stars and its radio ID
    (FTM-300DR capture 11-18, k6-c4fm/ftmref: '*****' + its ID, call mode 1)"""
    return "*****" + rid


# DCH frames 6 and 7 of a Yaesu radio's DN call: a 20-byte block, radio type then a GPS position
# (APRS Mic-E style: 6 latitude characters, then longitude degrees, minutes and hundredths + 28),
# byte 19 = the sum of bytes 0-18. An FTM-300DR shows the caller's callsign only when this block is
# there (bench, 11-18: frames identical but for it gave a blue bar and no callsign). The first five
# bytes and bytes 14-18 are as an FTM-300DR sends them; the position is made up (47 00.00 N,
# 122 00.00 W, the same N / +100 / W flag letters as the radio's).
GPS_HEAD = bytes.fromhex("5522625f31")
GPS_TAIL = bytes.fromhex("6c23272003")


def gps_block(lat=b"T7PPPP", lon=(22, 0, 0)):
    b = GPS_HEAD + lat + bytes(28 + v for v in lon) + GPS_TAIL
    assert len(b) == 19
    return b + bytes([sum(b) & 0xFF])


def gps_block_ok(b):
    return len(b) == 20 and (sum(b[:19]) & 0xFF) == b[19]


def dn_dch(fn, dest="ALL", src=IDENT, down="", up="", rem=" " * 20, rid=None):
    """the DN data channel's 10 bytes for frame fn (0-7). With rid, the Yaesu layout an FTM-300DR
    sends: frame 5 carries five spaces and the radio ID, frames 6 and 7 the radio-type and GPS
    block (frame 4 keeps the first half of rem)"""
    rem = rem.encode("ascii")[:20].ljust(20, b" ")
    if rid:
        rem2 = b" " * 5 + rid.encode("ascii")[:5].ljust(5, b" ")
        r3, r4 = gps_block()[:10], gps_block()[10:]
    else:
        rem2, r3, r4 = rem[10:], b" " * 10, b" " * 10
    return [callsign_field(dest), callsign_field(src), callsign_field(down), callsign_field(up),
            rem[:10], rem2, r3, r4][fn]


# ---------------------------------------------------------------- frame assembly
def build_frame(fich, payload_bits):
    assert len(payload_bits) == PAYLOAD_BITS
    return np.concatenate([SYNC, fich.encode(), np.asarray(payload_bits, np.int8)])


def split_frame(llr960):
    llr960 = np.asarray(llr960)
    return llr960[SYNC_BITS:PAYLOAD_OFFSET], llr960[PAYLOAD_OFFSET:]


def tx_dn(ncomms, dest=None, src=IDENT, ft=7, rem="", dgid=0, cm=1,
          rid=RADIO_ID):
    """a V/D mode 2 transmission: header, ncomms comms frames of silence (FN 0..ft), terminator.
    The defaults copy a real FTM-300DR's local call (call mode 1 = radio ID, FT 7, destination
    '*****' + radio ID, the ID again in frame 5); cm=0, ft=6, dest='ALL', rid=None is the
    11-18 layout the FTM showed a blue bar for but no callsign"""
    if dest is None:
        dest = dn_dest(rid) if rid else "ALL"
    out = [bytes([PREAMBLE]) * 24]
    hdr = header_payload(dest, src, csd2=b" " * 20)
    mk = dict(cs=2, cm=cm, ft=ft, dt=DT_VD_MODE2, dgid=dgid)
    out.append(bits_to_bytes(build_frame(FICH(fi=FI_HEADER, fn=0, **mk), hdr)))
    sil = [ambe_silence()] * 5
    for k in range(ncomms):
        fn = k % (ft + 1)
        pl = dn_payload(dn_dch(fn, dest, src, rem=rem, rid=rid), sil)
        out.append(bits_to_bytes(build_frame(FICH(fi=FI_COMMS, fn=fn, **mk), pl)))
    out.append(bits_to_bytes(build_frame(FICH(fi=FI_TERMINATOR, fn=0, **mk), hdr)))
    return b"".join(out)


def data_chunks(text, n):
    """text repeated to n 20-byte blocks, each block numbered"""
    out = []
    for k in range(n):
        out.append(f"{k:02d} {text}".encode("ascii")[:20].ljust(20, b" "))
    return out


def tx_data(ncomms, dest="ALL", src=IDENT, ft=7, text=f"{IDENT} YSF DATA", dgid=0):
    """a data full rate transmission: FN 0 carries CSD1 (dest+src) and CSD2, FN 1 CSD3 and the
    first data block, the others two data blocks each"""
    out = [bytes([PREAMBLE]) * 24]
    csd1 = callsign_field(dest) + callsign_field(src)
    hdr = header_payload(dest, src)
    mk = dict(cs=2, ft=ft, dt=DT_DATA_FR, dgid=dgid)
    out.append(bits_to_bytes(build_frame(FICH(fi=FI_HEADER, fn=0, **mk), hdr)))
    blocks = iter(data_chunks(text, 2 * ncomms))
    for k in range(ncomms):
        fn = k % (ft + 1)
        if fn == 0:
            d1, d2 = csd1, b" " * 20
        elif fn == 1:
            d1, d2 = b" " * 20, next(blocks)
        else:
            d1, d2 = next(blocks), next(blocks)
        out.append(bits_to_bytes(build_frame(FICH(fi=FI_COMMS, fn=fn, **mk), data_fr_payload(d1, d2))))
    out.append(bits_to_bytes(build_frame(FICH(fi=FI_TERMINATOR, fn=0, **mk), hdr)))
    return b"".join(out)


# ---------------------------------------------------------------- sotto VW (sotto docs/softmodem.md)
# The sotto softmodem's voice payload (sotto modem/payload.py SottoVW, MIT, same author): five
# 48-bit frames, each + CRC-8, tail-biting K=7 (133, 171, 165) punctured to rate 7/18 = 720 bits,
# coded bit i to payload bit (i % 24) * 30 + i // 24, XOR PN9 seeded 0x1FF. FICH DT = voice FR with
# the (provisional) sotto flag. On the UV-380 the five frames are sotto 1200 superframes.
SOTTO_NF = 5
SOTTO_SF = 6
_SOTTO_I = np.arange(PAYLOAD_BITS)
SOTTO_PERM = (_SOTTO_I % 24) * (PAYLOAD_BITS // 24) + _SOTTO_I // 24
SOTTO_PN = _pn9(PAYLOAD_BITS, seed=0x1FF)


def sotto_vw_payload(frames):
    """five 6-byte frames (30 bytes) -> the 720 payload bits"""
    from . import fec
    frames = bytes(frames)
    assert len(frames) == SOTTO_NF * SOTTO_SF
    f = bytes_to_bits(frames).reshape(SOTTO_NF, 8 * SOTTO_SF)
    u = np.concatenate([np.concatenate([x, fec.crc8(x)]) for x in f])
    c = fec.RATE_7_18.encode(u)
    out = np.zeros(PAYLOAD_BITS, np.int8)
    out[SOTTO_PERM] = c
    return out ^ SOTTO_PN


def decode_sotto_vw_payload(llr):
    """-> (30 bytes, five CRC flags)"""
    from . import fec
    llr = np.where(SOTTO_PN == 1, -1.0, 1.0) * np.asarray(llr, float)
    u = fec.RATE_7_18.decode(llr[SOTTO_PERM], SOTTO_NF * (8 * SOTTO_SF + 8)).reshape(SOTTO_NF, -1)
    ok = [bool((fec.crc8(x[:48]) == x[48:]).all()) for x in u]
    return bits_to_bytes(u[:, :48].reshape(-1)), ok


def tx_sotto(sotto, dest="ALL", src=IDENT, dgid=0):
    """sotto 1200 as the sotto softmodem's YSF: a 0x77 preamble, header (CSD dest+src), comms
    frames FN 0-6 of five superframes each (the last superframe repeats to fill the last
    frame), terminator. FICH as sotto's link.Transmitter: FT 6, DT voice FR, sotto flag."""
    sotto = bytes(sotto)
    n = len(sotto) // SOTTO_SF
    if n == 0:
        raise ValueError("no sotto superframes")
    sotto = sotto[:n * SOTTO_SF]
    sotto += sotto[-SOTTO_SF:] * (-n % SOTTO_NF)
    mk = dict(ft=6, dt=DT_VOICE_FR, dgid=dgid, sotto=True)
    hdr = header_payload(dest, src)
    out = [bytes([PREAMBLE]) * 24, bits_to_bytes(build_frame(FICH(fi=FI_HEADER, **mk), hdr))]
    per = SOTTO_NF * SOTTO_SF
    for k in range(len(sotto) // per):
        pl = sotto_vw_payload(sotto[per * k:per * k + per])
        out.append(bits_to_bytes(build_frame(FICH(fi=FI_COMMS, fn=k % 7, **mk), pl)))
    out.append(bits_to_bytes(build_frame(FICH(fi=FI_TERMINATOR, **mk), hdr)))
    return b"".join(out)


PLANS = ("dn", "data")


def build(plan):
    n = (MAX_BYTES - 24) // FRAME_BYTES - 2            # header and terminator
    if plan == "dn":
        return tx_dn(n)
    if plan == "data":
        return tx_data(n)
    raise SystemExit(f"unknown plan {plan}: {', '.join(PLANS)}")


def header(plan, data):
    """the module's C header: the bytes, MSB first, as the chip sends them"""
    lines = [f"/* ysftx_{plan}.h: written by python3 -m modem.ysf header {plan}; do not edit.",
             f"   {len(data)} bytes = a 0x77 preamble and {len(data) // FRAME_BYTES} YSF frames,"
             f" {len(data) / 1.2:.0f} ms at 4800 symbols a second. SPDX-License-Identifier: MIT */",
             f"#define YSF_TX_N {len(data)}u",
             f"#define YSF_TX_FILL 0x{data[0]:02x}u",
             "static const uint8_t YSF_TX[YSF_TX_N] = {"]
    for i in range(0, len(data), 16):
        lines.append("    " + ", ".join(f"0x{b:02x}" for b in data[i:i + 16]) + ",")
    lines.append("};")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- frame decode
def decode_frame(llr960):
    """960 soft values (positive = 0) -> a dict: fich, and what the payload holds"""
    f_llr, p_llr = split_frame(llr960)
    fich = FICH.decode(f_llr)
    fr = {"fich": fich}
    if fich is None:
        return fr
    if fich.fi in (FI_HEADER, FI_TERMINATOR):
        fr["csd"] = decode_header_payload(p_llr)
    elif fich.dt == DT_VD_MODE2:
        dch, voice = decode_dn_payload(p_llr)
        fr["dch"] = dch
        fr["ambe"] = [a for a, _ in voice]
        fr["vch_bad"] = sum(b for _, b in voice)
    elif fich.dt == DT_DATA_FR:
        fr["dch"] = decode_data_fr_payload(p_llr)
    return fr


def llr_from_levels(lv):
    """symbols scaled to +-1/+-3 -> 2 soft values each: msb is 1 below 0, lsb 1 outside +-2"""
    lv = np.asarray(lv, float)
    out = np.empty(2 * len(lv))
    out[0::2] = lv
    out[1::2] = 2.0 - np.abs(lv)
    return out


def bits_to_levels(bits):
    b = np.asarray(bits).reshape(-1, 2)
    lut = np.array([LEVEL[k] for k in range(4)], float)
    return lut[b[:, 0] * 2 + b[:, 1]]


def decode_bytes(data):
    """a plan's bytes, decoded as a perfect receiver would (frames found by their sync)"""
    bits = bytes_to_bits(data)
    out = []
    i = 0
    while i + FRAME_BITS <= len(bits):
        if np.array_equal(bits[i:i + SYNC_BITS], SYNC):
            fr = decode_frame(1.0 - 2.0 * bits[i:i + FRAME_BITS])
            fr["at"] = i / 2 / SYM_RATE
            out.append(fr)
            i += FRAME_BITS
        else:
            i += 2
    return out


def summarize(frames):
    s = {"frames": len(frames), "fich_ok": 0, "fi": {}, "dt": {}, "dest": set(), "src": set(),
         "dch_ok": 0, "dch": 0, "silence": 0, "ambe": 0, "vch_bad": 0, "data": [], "fn": []}
    for f in frames:
        fi = f["fich"]
        if fi is None:
            continue
        s["fich_ok"] += 1
        s["fi"][FI_NAMES[fi.fi]] = s["fi"].get(FI_NAMES[fi.fi], 0) + 1
        s["dt"][DT_NAMES[fi.dt]] = s["dt"].get(DT_NAMES[fi.dt], 0) + 1
        if fi.fi == FI_COMMS:
            s["fn"].append(fi.fn)
        if f.get("csd"):
            s["dest"].add(f["csd"]["dest"])
            s["src"].add(f["csd"]["src"])
        if "ambe" in f:
            s["dch"] += 1
            s["dch_ok"] += f["dch"] is not None
            if f["dch"] is not None and fi.fn == 1:
                s["src"].add(f["dch"].decode("ascii", "replace").strip())
            s["ambe"] += 5
            s["silence"] += sum(ambe_b0(a) in (124, 125) for a in f["ambe"])
            s["vch_bad"] += f["vch_bad"]
        elif fi.dt == DT_DATA_FR and fi.fi == FI_COMMS:
            for k, d in enumerate(f["dch"]):
                s["dch"] += 1
                s["dch_ok"] += d is not None
                if d is not None and (fi.fn >= 2 or (fi.fn == 1 and k == 1)):
                    s["data"].append(d)
    return s


def report(frames, s):
    lines = [f"frames {s['frames']}, FICH good {s['fich_ok']}"]
    if s["fi"]:
        lines.append("kinds " + ", ".join(f"{k} {v}" for k, v in s["fi"].items()) + "; "
                     + ", ".join(f"{k} {v}" for k, v in s["dt"].items()))
    if s["src"] or s["dest"]:
        lines.append(f"from {'/'.join(sorted(s['src']))} to {'/'.join(sorted(s['dest']))}")
    if s["dch"]:
        lines.append(f"data channel blocks good {s['dch_ok']}/{s['dch']}")
    if s["ambe"]:
        lines.append(f"AMBE frames {s['ambe']}, silence {s['silence']}, voice triplets corrected "
                     f"{s['vch_bad']}")
    if s["data"]:
        lines.append(f"data blocks {len(s['data'])}: first {s['data'][0]!r}, last {s['data'][-1]!r}")
    if frames and "outer_hz" in frames[0]:
        o = np.array([f["outer_hz"] for f in frames])
        lines.append(f"outer level {np.median(o):.0f} Hz (YSF wants 2700), frames at "
                     f"{frames[0]['at']:.3f}..{frames[-1]['at']:.3f} s")
        lv = {}
        for f in frames:
            for k, v in f.get("levels", {}).items():
                lv.setdefault(k, []).append(v)
        if lv:
            lines.append("levels " + ", ".join(f"{k:+d}: {np.median(v):+.0f} Hz" for k, v in sorted(lv.items())))
    return lines


# ---------------------------------------------------------------- modem (Pluto discriminator)
def rrc(alpha, sps, span=8):
    from . import m17
    return m17.rrc(alpha, sps, span)


def modulate(data, fs=48000, alpha=0.2, outer_hz=OUTER_HZ):
    """the discriminator (Hz) a flat FM receiver sees: the chip's RRC (alpha 0.2)"""
    sps = fs // SYM_RATE
    up = np.zeros(len(data) * 4 * sps)
    up[::sps] = bits_to_levels(bytes_to_bits(data))
    return np.convolve(up, rrc(alpha, sps) * sps, "same") * outer_hz / 3.0


SYNC_LEVELS = bits_to_levels(SYNC)


def runs(frames, gap=0.100, tol=0.002):
    """frames in runs 100 ms apart; a lone sync match makes no run"""
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


def demod(disc, fs=48000, alpha=0.2):
    """discriminator (Hz) -> frames in runs. DC off over a frame first, then each run's own DC"""
    from . import m17
    disc = np.asarray(disc, float)
    first = [f for r in runs(_demod(m17.dc_block(disc, fs // 10), fs, alpha)) for f in r]
    if not first:
        return []
    d2 = m17.dc_block(disc, fs // 10)
    pad = int(0.2 * fs)
    for r in runs(first):
        a, b = int(r[0]["at"] * fs), int((r[-1]["at"] + 0.100) * fs)
        dc = float(np.mean(disc[a:b]))
        d2[max(0, a - pad):b + pad] = disc[max(0, a - pad):b + pad] - dc
    return [f for r in runs(_demod(d2, fs, alpha)) for f in r]


def _demod(disc, fs=48000, alpha=0.2):
    """RRC matched filter, sync correlation (20 symbols), the outer level from the sync"""
    sps = fs // SYM_RATE
    y = np.convolve(disc, rrc(alpha, sps), "same")
    p = SYNC_LEVELS
    pn = np.linalg.norm(p)
    out, i, n = [], 0, len(y)
    span = FRAME_SYMS * sps
    ns = len(p)
    while i + span + sps < n:
        v = y[i + np.arange(ns) * sps]
        sc = v @ p / (np.linalg.norm(v) * pn + 1e-9)
        if sc < 0.9 or np.max(np.abs(v)) < 300:
            i += 1
            continue
        cands = []
        for d in range(-sps // 2, sps // 2 + 1):
            idx = i + d + np.arange(ns) * sps
            if idx[0] < 0 or idx[-1] >= n:
                continue
            cands.append((y[idx] @ p, d))
        _, d = max(cands)
        start = i + d
        idx = start + np.arange(FRAME_SYMS) * sps
        if idx[-1] >= n:
            break
        v = y[idx]
        gain = (v[:ns] @ p) / (p @ p)
        if gain <= 0:
            i += 1
            continue
        lv = v / gain
        fr = decode_frame(llr_from_levels(lv))
        fr["at"] = start / fs
        fr["outer_hz"] = gain * 3.0
        q = np.clip(np.round((lv + 3) / 2) * 2 - 3, -3, 3)
        fr["levels"] = {int(L): float(np.mean(v[q == L])) for L in (-3, -1, 1, 3) if np.any(q == L)}
        out.append(fr)
        i = start + span - sps // 2
    return out


WAV_FULL_DEV = 5000.0                  # pluto_bench.py rx OUT.wav: this deviation = full scale


def read_wav_disc(path):
    """pluto_bench.py rx OUT.wav (the flat FM discriminator, 48 kHz, 5 kHz = full scale) -> Hz"""
    from scipy.io import wavfile
    fs, x = wavfile.read(path)
    integer = np.issubdtype(x.dtype, np.integer)
    x = np.asarray(x, float)
    if x.ndim > 1:
        x = x[:, 0]
    if integer:
        x = x / 32768.0
    if fs != 48000:
        from scipy import signal
        x = signal.resample_poly(x, 48000, fs)
    return x * WAV_FULL_DEV


def decode_wav(path):
    """the bench runner returns files up to 20 MB: a capture's .wav (12 MB for 130 s) comes back,
    its .cf32 (250 MB) stays on hamlet. Tune the Pluto onto the channel (--hz 441850000): the wav
    is the discriminator about the capture's centre, 8 kHz wide"""
    disc = read_wav_disc(path)
    frames = demod(disc)
    lines = [f"{path}: {len(disc) / 48000:.1f} s of discriminator"]
    if frames:
        burst = np.concatenate([disc[int(f["at"] * 48000):int((f["at"] + 0.1) * 48000)] for f in frames])
        lines.append(f"YSF burst {float(np.mean(burst)):+.0f} Hz from the capture's centre")
        for r in runs(frames):
            lines.append(f"run {r[0]['at']:.2f}..{r[-1]['at'] + 0.1:.2f} s: {len(r)} frames")
    return lines + report(frames, summarize(frames))


def decode_capture(cap):
    if cap.endswith(".wav"):
        return decode_wav(cap)
    from . import c6phy
    iq, meta = c6phy.read_capture(cap)
    fs = int(meta.get("fs", 240_000))
    off, on, disc = c6phy.capture_disc(iq, fs, bw_hz=12000.0)
    if disc is None:
        return ["no signal in the capture"]
    frames = demod(disc)
    lines = [f"carrier {off:+.0f} Hz from the capture's centre (ident and burst together)"]
    if frames:
        burst = np.concatenate([disc[int(f["at"] * 48000):int((f["at"] + 0.1) * 48000)] for f in frames])
        lines.append(f"YSF burst {off + float(np.mean(burst)):+.0f} Hz from the capture's centre")
    return lines + report(frames, summarize(frames))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m modem.ysf", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("header")
    h.add_argument("plan", choices=PLANS)
    h.add_argument("out")
    p = sub.add_parser("plan")
    p.add_argument("plan", choices=PLANS)
    d = sub.add_parser("decode")
    d.add_argument("cap")
    s = sub.add_parser("sim")
    s.add_argument("plan", choices=PLANS)
    s.add_argument("--alpha", type=float, default=0.2)
    s.add_argument("--outer-hz", type=float, default=OUTER_HZ)
    s.add_argument("--noise-hz", type=float, default=0.0)
    s.add_argument("--dc-hz", type=float, default=0.0)
    a = ap.parse_args(argv)
    if a.cmd == "header":
        data = build(a.plan)
        with open(a.out, "w") as f:
            f.write(header(a.plan, data))
        print(f"{a.out}: {len(data)} bytes, {len(data) // FRAME_BYTES} frames, {len(data) / 1.2:.0f} ms")
    elif a.cmd == "plan":
        data = build(a.plan)
        print(f"{a.plan}: {len(data)} bytes, {len(data) // FRAME_BYTES} frames, {len(data) / 1.2:.0f} ms")
        print("\n".join(report([], summarize(decode_bytes(data)))))
    elif a.cmd == "sim":
        data = build(a.plan)
        disc = modulate(bytes([PREAMBLE]) * 60 + data + bytes([PREAMBLE]) * 60, alpha=a.alpha,
                        outer_hz=a.outer_hz) + a.dc_hz
        if a.noise_hz:
            disc = disc + np.random.default_rng(1).normal(0, a.noise_hz, len(disc))
        fr = demod(disc)
        print("\n".join(report(fr, summarize(fr))))
    else:
        print("\n".join(decode_capture(a.cap)))


if __name__ == "__main__":
    sys.exit(main())
