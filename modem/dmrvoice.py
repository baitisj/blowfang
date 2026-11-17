"""sotto in DMR voice bursts (docs/uv380-c6000-modem.md, "Sotto in DMR voice bursts"): one
modem/dmrsotto.py block (12 bytes) as the 216-bit payload of a voice burst, under our own FEC. The
C encoder for the radio is c/common/dmr_voice.h; tests check the two agree byte for byte.

A DMR voice burst carries 216 payload bits (108, then the sync or EMB, then 108) that a radio
fills with three AMBE+2 frames; the HR_C6000 takes them as 27 bytes over its voice SPI port and
sends them as given. Ours carry one block:

  the block's 96 bits MSB first, 6 zero tail bits (102)
  K=7 rate 1/2 convolutional code, generators 0171 0133 (octal), input into bit 0 (204 bits)
  interleaved: coded bit j -> payload bit (j * 61) mod 216; the 12 bits left over are 0
  payload bit p = bit 7 - p % 8 of byte p // 8

Decoding is soft Viterbi (positive soft value = 1, as sim/k6if/dmrdec.soft_bits gives them),
then the block's own CRC-16 says whether to keep it. One block per burst, one burst per 60 ms:
1200 bit/s of payload, sotto 1200 in real time.

pn(k) is the raw test pattern (no FEC): PN9 per burst, for learning the chip's mapping of page 3
bits onto the air (learn_map), should it not be the plain one.

  python3 -m modem.dmrvoice pack MSG.bit OUT.v27 [--id N] [--text AG7EW]   27-byte payloads
  python3 -m modem.dmrvoice unpack IN.v27 OUT.bit

MIT licence."""
import argparse

import numpy as np

from . import dmrsotto

BYTES, BITS, INFO, STEPS, CODED, SPREAD = 27, 216, 96, 102, 204, 61
G1, G2 = 0o171, 0o133
PLACE = np.array([(j * SPREAD) % BITS for j in range(CODED)])     # coded bit j -> payload bit


def _parity(v):
    return bin(v).count("1") & 1


def _bits(by):
    return np.array([(b >> (7 - i)) & 1 for b in by for i in range(8)], np.uint8)


def _bytes(bits):
    bits = np.asarray(bits, np.uint8)
    return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def conv_encode(info):
    """bits (with the tail already on) -> 2 coded bits each"""
    reg, out = 0, []
    for b in info:
        reg = ((reg << 1) | int(b)) & 0x7F
        out += [_parity(reg & G1), _parity(reg & G2)]
    return np.array(out, np.uint8)


def encode(blk):
    """12-byte block -> 27-byte burst payload"""
    blk = bytes(blk)
    assert len(blk) == dmrsotto.BLOCK
    coded = conv_encode(np.concatenate([_bits(blk), np.zeros(STEPS - INFO, np.uint8)]))
    pay = np.zeros(BITS, np.uint8)
    pay[PLACE] = coded
    return _bytes(pay)


def pn(k):
    """burst k's raw test pattern (27 bytes): PN9 x^9 + x^5 + 1 from (k * 97) % 511 + 1"""
    s, out = (k * 97) % 511 + 1, []
    for _ in range(BITS):
        b = ((s >> 8) ^ (s >> 4)) & 1
        s = ((s << 1) | b) & 0x1FF
        out.append(b)
    return _bytes(out)


# trellis: state = the last 6 inputs (bit 0 newest); from s on input b: reg = s << 1 | b
_S = np.arange(64)
_PREV = np.stack([(_S >> 1), (_S >> 1) | 32], axis=1)               # [next state, which] -> prev
_OUT = np.zeros((64, 2, 2))                                          # [next, which, 0/1] -> +-1
for _n in range(64):
    for _w in range(2):
        _r = (_PREV[_n, _w] << 1 | (_n & 1)) & 0x7F
        _OUT[_n, _w] = [2 * _parity(_r & G1) - 1, 2 * _parity(_r & G2) - 1]


def viterbi(soft):
    """204 soft values (positive = 1) -> (102 decoded bits, path metric per coded bit)"""
    soft = np.asarray(soft, float).reshape(STEPS, 2)
    m = np.full(64, -1e18)
    m[0] = 0.0
    back = np.zeros((STEPS, 64), np.uint8)
    for i in range(STEPS):
        cand = m[_PREV] + _OUT @ soft[i]                             # [next, which]
        w = np.argmax(cand, axis=1)
        back[i] = w
        m = cand[_S, w]
    s, out = 0, np.zeros(STEPS, np.uint8)                            # the tail ends in state 0
    for i in range(STEPS - 1, -1, -1):
        out[i] = s & 1
        s = _PREV[s, back[i, s]]
    return out, float(m[0]) / CODED


def decode(soft216):
    """the burst's 216 soft payload values (positive = 1, in page 3's bit order) -> (12-byte block
    or None if its CRC fails, the decoded bytes either way, metric per bit)"""
    soft216 = np.asarray(soft216, float)
    scale = np.mean(np.abs(soft216)) or 1.0
    bits, metric = viterbi(soft216[PLACE] / scale)
    by = _bytes(bits[:INFO])
    return (by if dmrsotto.check(by) else None), by, metric


def hard(by27):
    """27 bytes -> soft values +-1 (a perfect channel)"""
    return 2.0 * _bits(by27) - 1.0


def apply_map(soft216, mp):
    """air-order soft values -> page 3 order: mp = [(air bit, sign)] for each page 3 bit"""
    soft216 = np.asarray(soft216, float)
    return np.array([s * soft216[a] for a, s in mp])


def learn_map(bursts, ks):
    """air-order soft payloads of bursts that carried pn(k) for k in ks -> (map, the weakest page 3
    bit's |correlation|, whether the map is a permutation)"""
    air = np.sign(np.asarray(bursts, float))
    sent = np.array([hard(pn(k)) for k in ks])
    c = sent.T @ air / len(air)                                      # [page bit, air bit]
    best = np.argmax(np.abs(c), axis=1)
    peak = c[np.arange(BITS), best]
    mp = [(int(a), int(np.sign(p))) for a, p in zip(best, peak)]
    return mp, float(np.min(np.abs(peak))), len(set(best.tolist())) == BITS


def pn_errors(bursts, ks, mp=None):
    """bit errors of air-order payloads against pn(k) for k in ks (through mp, if given)"""
    err = 0
    for k, b in zip(ks, bursts):
        s = apply_map(b, mp) if mp else np.asarray(b, float)
        err += int(np.sum((s > 0) != (_bits(pn(k)) == 1)))
    return err


def pack(msg, msg_id=1, text="AG7EW"):
    """superframe bytes -> [27-byte burst payloads], one per dmrsotto block"""
    return [encode(b) for b in dmrsotto.pack(msg, msg_id, text)]


def unpack(payloads, mp=None):
    """[216 soft values (air order; mp maps them) or None for a burst not heard] -> (dmrsotto.unpack's
    result or None, the blocks in air order (None where the CRC failed))"""
    blocks = []
    for s in payloads:
        if s is None:
            blocks.append(None)
            continue
        blk, by, _ = decode(apply_map(s, mp) if mp else s)
        blocks.append(blk if blk is not None else by)
    return dmrsotto.unpack(blocks), [b if b is not None and dmrsotto.check(b) else None for b in blocks]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("bit")
    p.add_argument("out")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--text", default="AG7EW")
    u = sub.add_parser("unpack")
    u.add_argument("v27")
    u.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "pack":
        msg = open(a.bit, "rb").read()
        msg = msg[:len(msg) - len(msg) % dmrsotto.SF_BYTES]
        open(a.out, "wb").write(b"".join(pack(msg, a.id, a.text)))
    else:
        raw = open(a.v27, "rb").read()
        r, _ = unpack([hard(raw[i:i + BYTES]) for i in range(0, len(raw), BYTES)])
        if r is None:
            raise SystemExit("no head block")
        open(a.out, "wb").write(r["msg"])
        print({k: v for k, v in r.items() if k != "msg"})


if __name__ == "__main__":
    main()
