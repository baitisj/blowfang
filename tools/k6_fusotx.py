#!/usr/bin/env python3
"""FuSotto from the K6 (c/k6/mod/fusotx.c, c/k6/mod/outbox.h) on the PC: the outbox a recording
leaves (outbox), the frames the sender makes from it (planes: a deck for a library slot, for
fusoslot.c on a K6 without API level 15), and what a Pluto capture of the call holds (air).

  python3 tools/k6_fusotx.py deck SF.bit OUT.bin      superframes -> the frames, for
                                                       k6_upload.py OUT.bin --cards --install 15
  python3 tools/k6_fusotx.py air CAP.wav [--sf SF.bit] the call's superframes from a capture
                                                       (and how many match SF.bit)
  python3 tools/k6_fusotx.py log RUN.log OUT.bit      a FM SOTTO run's "s N hex" lines -> SF.bit

SPDX-License-Identifier: MIT"""
import argparse
import os
import struct
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from modem import ysf  # noqa: E402

OB_LO, OB_HI = 0x190000, 0x1A0000
OB_DATA, OB_SF, OB_PL, OB_GRP, OB_GFRAMES = 256, 6, 0x194000, 256, 4
OB_GBYTES = OB_GFRAMES * 120
OB_GROUPS = (OB_HI - OB_PL - OB_GRP) // OB_GBYTES
OB_SF_MAX = (OB_GROUPS * OB_GFRAMES - 2) * 5
SOTTO_1200 = 0xA9
SLOT_DATA = 0x4000 - 24                 # a library slot: header 16, data, tag 8
DECK_DATA = 15408 - 24                  # but a deck travels through the K6's RAM arena (level 13)


def outbox(sf):
    """superframes -> the outbox's bytes from 0x190000 as FM SOTTO leaves them (header page,
    superframes; the rest erased)"""
    sf = bytes(sf)
    n = len(sf) // OB_SF
    hdr = struct.pack("<4I", 0x424F364B, 1 | SOTTO_1200 << 8, n, sum(sf[:n * OB_SF]))
    return hdr + b"\xff" * (OB_DATA - 16) + sf[:n * OB_SF]


def frames(sf, src=ysf.IDENT):
    """the call fusotx sends: header, VW frames, terminator (120 bytes each); at most OB_SF_MAX
    superframes"""
    sf = bytes(sf)[:OB_SF_MAX * OB_SF]
    b = ysf.tx_sotto(sf, src=src)[24:]
    return [b[i:i + 120] for i in range(0, len(b), 120)]


def split(frame):
    """120 bytes -> (signs, sizes), 60 bytes each (modem.k6c4fm.split, packed MSB first)"""
    d = ysf.bytes_to_bits(frame).reshape(-1, 2)
    return ysf.bits_to_bytes(1 - d[:, 0]), ysf.bits_to_bytes(d[:, 1])


def planes(sf, src=ysf.IDENT):
    """the sender's frames as it writes them at OB_PL: header page, then groups of four frames
    (their signs, then their sizes), a short last group padded with zeros"""
    fr = frames(sf, src)
    nsf = min(len(bytes(sf)) // OB_SF, OB_SF_MAX)
    groups = b""
    for g in range(0, len(fr), OB_GFRAMES):
        part = [split(f) for f in fr[g:g + OB_GFRAMES]]
        part += [(bytes(60), bytes(60))] * (OB_GFRAMES - len(part))
        groups += b"".join(s for s, _ in part) + b"".join(z for _, z in part)
    hdr = struct.pack("<4I", 0x504F364B, len(fr), nsf, sum(groups))
    return hdr + b"\xff" * (OB_GRP - 16) + groups


def deck(sf, src=ysf.IDENT):
    """planes for a library slot (fusoslot.c): as many superframes as reach it (610: the upload
    goes through the K6's 15,408 B arena, smaller than a slot)"""
    per = (min(SLOT_DATA, DECK_DATA) - OB_GRP) // OB_GBYTES * OB_GFRAMES - 2
    return planes(bytes(sf)[:per * 5 * OB_SF], src)


def call_superframes(frames_):
    """modem.ysf frames (dicts) -> each sotto VW call's superframes, None where a CRC failed"""
    calls, cur = [], None
    for f in frames_:
        fich = f.get("fich")
        if fich is None:
            continue
        if fich.fi == ysf.FI_HEADER:
            cur = []
            calls.append(cur)
        elif fich.fi == ysf.FI_COMMS and fich.sotto and cur is not None:
            for k in range(ysf.SOTTO_NF):
                cur.append(f["sotto"][6 * k:6 * k + 6] if f["sotto_ok"][k] else None)
    return calls


def decode(disc, fs=48000):
    """a discriminator (Hz) -> each sotto VW call's superframes (the polarity with more frames)"""
    disc = np.asarray(disc, float)
    best = []
    for pol in (1, -1):
        fr = ysf.demod(pol * (disc - np.median(disc)), fs)
        if len(fr) > len(best):
            best = fr
    return call_superframes(sorted(best, key=lambda f: f["at"]))


def from_log(text):
    import re
    got = {}
    for ln in text.splitlines():
        m = re.search(r"(?:^|\s)s (\d+) ([0-9a-f]+)\s*$", ln.strip())
        if m and len(m.group(2)) % 12 == 0:
            n, b = int(m.group(1)), bytes.fromhex(m.group(2))
            for k in range(len(b) // 6):
                got[n + k] = b[6 * k:6 * k + 6]
    return b"".join(got[k] for k in sorted(got))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deck")
    d.add_argument("sf")
    d.add_argument("out")
    a = sub.add_parser("air")
    a.add_argument("wav")
    a.add_argument("--sf")
    lg = sub.add_parser("log")
    lg.add_argument("log")
    lg.add_argument("out")
    args = ap.parse_args(argv)
    if args.cmd == "deck":
        b = deck(open(args.sf, "rb").read())
        open(args.out, "wb").write(b)
        nf, nsf = struct.unpack("<2I", b[4:12])
        print(f"{args.out}: {len(b)} bytes, {nf} frames, {nsf} superframes ({nsf * 0.04:.1f} s)")
    elif args.cmd == "log":
        b = from_log(open(args.log, errors="replace").read())
        open(args.out, "wb").write(b)
        print(f"{args.out}: {len(b) // 6} superframes")
    else:
        from modem import k6c4fm
        calls = decode(k6c4fm.read_wav(args.wav))
        want = open(args.sf, "rb").read() if args.sf else None
        for i, c in enumerate(calls):
            ok = sum(s is not None for s in c)
            line = f"call {i}: {len(c)} superframes, {ok} pass CRC"
            if want:
                same = sum(s == want[6 * k:6 * k + 6] for k, s in enumerate(c) if s is not None)
                line += f", {same} match"
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
