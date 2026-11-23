#!/usr/bin/env python3
"""Read-only clone of a Baofeng radio on the K-plug cable that speaks the UV-17 Pro family's
programming protocol (UV-21 Pro, UV-17 Pro, UV-5RM, GM-5RH, BF-F8HP Pro, ...).

It sends the ident handshake and "R" read requests only. There is no write command in this file:
the codeplug, the radio's settings and its firmware are never changed. The bench runner's
kplug.read step runs it on the K6 cable (found by its by-id name), so this is the one tool other
than k6_install / k6_update that writes bytes to that port. Before the handshake it listens, and
refuses if it hears the K6's own report lines: the handshake is meant for a radio in its stock
programming mode, not for the K6.

  bench_kplug.py read --port PORT --out RUNDIR --private PRIVDIR [--ident normal|color|...]
                      [--model UV-21ProV2] [--extra 0xB000[:0xC000]] [--listen-s 1.0]

Outputs:
  PRIVDIR/clone.img   the whole codeplug, de-scrambled, in CHIRP's layout with CHIRP's model tag
                      on the end, so CHIRP can open it (File > Open). It holds the channel list,
                      so it stays on hamlet: the run folder may go back through the jobs inbox to
                      a public branch.
  PRIVDIR/raw.bin     every reply as it came off the wire (4-byte header + scrambled block)
  RUNDIR/ident.json   which ident answered, the F / M / SEND replies (hex and ASCII)
  RUNDIR/summary.json each region's size and sha256, how many channels are programmed and on
                      which bands (counts only, no frequencies), printable strings in the
                      non-channel regions, the extra blocks' status
  RUNDIR/region_XXXX.bin  the regions outside the channel memories (settings, ANI/DTMF, the
                      0x9000 / 0xD000 blocks) and any --extra blocks, de-scrambled
  RUNDIR/hexdump.txt  the same bytes as a hex dump

Protocol facts (as documented by CHIRP's baofeng_uv17Pro driver, reimplemented here): 115200 8N1;
a 16-byte ident magic answered by 0x06; then "F" (16-byte reply), "M" (15 or 7 bytes) and a
"SEND!" magic (1 byte); then reads of "R" + 16-bit big-endian address + length (0x40), each
answered by a 4-byte header and the block, XOR-scrambled with a 4-byte key ("CO 7" on the UV-17
Pro family) except where the key byte is a space or the data byte is 0x00, 0xFF, the key byte or
its complement. The scramble is its own inverse.

Exit status: 0 read done; 2 no radio answered the ident; 3 short or garbled replies; 4 refused
(something else is talking on the cable). SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

BAUD = 115200
BLOCK = 0x40
REPLY_TIMEOUT = 1.5

IDENTS = {                                 # name -> magic (16 bytes)
    "normal": b"PROGRAMBFNORMALU",         # UV-17 Pro, UV-21 Pro V2, UV-5RM, UV-17R Plus, UV-25
    "color": b"PROGRAMCOLORPROU",          # UV-17 Pro GPS, UV-21 Pro GPS, UV-28 Plus, UV-5R Mini
    "gmrs5rh": b"PROGRAMBFGMRS05U",        # GM-5RH, UV-5G Plus
    "f8hppro": b"PROGRAMBF5RTECHU",        # BTECH BF-F8HP Pro
}
DEFAULT_IDENTS = ("normal", "color")
SEND_MAGIC = (b"\x53\x45\x4E\x44\x21\x05\x0D\x01\x01\x01\x04\x11\x08\x05"
              b"\x0D\x0D\x01\x11\x0F\x09\x12\x09\x10\x04\x00")
KEY = b"CO 7"

# the codeplug's regions on the radio, in the order CHIRP lays them end to end in its image
REGIONS = ((0x0000, 0x8040), (0x9000, 0x0040), (0xA000, 0x02C0), (0xD000, 0x0040))
CHANNELS, CHAN_SIZE = 1000, 32             # 0x0000-0x7D00: the channel memories (kept private)
MODELS = ("UV-21ProV2", "UV-21ProGPS", "UV-17Pro", "UV-17ProGPS", "5RM", "UV-5RH", "GM-5RH")
EXTRA_MAX_BLOCKS = 512                     # 32 KB of probing per run
BANDS = (("air 108-136", 108e6, 136e6), ("2 m 136-174", 136e6, 174e6),
         ("1.25 m 200-260", 200e6, 260e6), ("350-390", 350e6, 390e6),
         ("70 cm 400-480", 400e6, 480e6), ("480-520", 480e6, 520e6))


def scramble(data, key=KEY):
    """the radio's XOR scramble (and its inverse: the same function)"""
    out = bytearray(data)
    for i, b in enumerate(out):
        k = key[i % len(key)]
        if k != 0x20 and b not in (0x00, 0xFF, k, k ^ 0xFF):
            out[i] = b ^ k
    return bytes(out)


def read_frame(addr, n=BLOCK):
    if not (0 <= addr <= 0xFFFF and 0 < n <= 0xFF):
        raise ValueError(f"read 0x{addr:x}+{n}: out of range")
    return b"R" + addr.to_bytes(2, "big") + bytes([n])


def parse_extra(s):
    """'0xB000' (one block) or '0xB000:0xC000' (END exclusive) -> [addr, ...]"""
    m = re.fullmatch(r"(0x[0-9a-fA-F]{1,4})(?::(0x[0-9a-fA-F]{1,5}))?", s)
    if not m:
        raise ValueError(f"--extra {s}: 0xADDR or 0xSTART:0xEND")
    a = int(m.group(1), 16)
    b = int(m.group(2), 16) if m.group(2) else a + BLOCK
    if a % BLOCK or b % BLOCK or not a < b <= 0x10000:
        raise ValueError(f"--extra {s}: 0x40-aligned, START < END <= 0x10000")
    return list(range(a, b, BLOCK))


class Port:
    """pyserial behind write(b) / read(n, timeout) / drain(seconds)"""

    def __init__(self, dev):
        import serial                      # only on hamlet; the tests use a fake radio
        self.s = serial.Serial(dev, BAUD, timeout=0.05, write_timeout=2.0)

    def write(self, b):
        self.s.write(b)
        self.s.flush()

    def read(self, n, timeout=REPLY_TIMEOUT, gap=None):
        """up to n bytes: stop at n, at timeout, or (gap) after that long with nothing new"""
        out = bytearray()
        end = time.monotonic() + timeout
        last = time.monotonic()
        while len(out) < n and time.monotonic() < end:
            got = self.s.read(n - len(out))
            if got:
                out += got
                last = time.monotonic()
            elif gap and out and time.monotonic() - last > gap:
                break
        return bytes(out)

    def drain(self, seconds):
        return self.read(1 << 16, timeout=seconds)

    def close(self):
        self.s.close()


def ident(port, names, log):
    """the handshake: (name, {F, M, SEND} replies) of the first ident the radio acks, or None"""
    for name in names:
        junk = port.drain(0.05)
        if junk:
            log(f"discarded {len(junk)} bytes before ident {name}")
        port.write(IDENTS[name])
        ack = port.read(1)
        log(f"ident {name}: {ack.hex() or 'no answer'}")
        if ack != b"\x06":
            continue
        rep = {}
        port.write(b"F")
        rep["F"] = port.read(16, gap=0.3)
        port.write(b"M")
        rep["M"] = port.read(15, gap=0.3)
        port.write(SEND_MAGIC)
        rep["SEND"] = port.read(1)
        for k, v in rep.items():
            log(f"  {k}: {v.hex()} {printable(v)!r}")
        return name, rep
    return None


def read_block(port, addr):
    """(header, scrambled data) for one 64-byte block; short replies come back short"""
    port.write(read_frame(addr))
    r = port.read(BLOCK + 4)
    return r[:4], r[4:]


def printable(b):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def strings(b, base, least=5):
    """(address, text) of printable runs at least `least` long"""
    return [(base + m.start(), m.group().decode()) for m in re.finditer(rb"[\x20-\x7e]{%d,}" % least, b)]


def bcd_le(b):
    """little-endian packed BCD (CHIRP's lbcd) -> int, or None if not BCD"""
    v = 0
    for c in reversed(b):
        hi, lo = c >> 4, c & 15
        if hi > 9 or lo > 9:
            return None
        v = v * 100 + hi * 10 + lo
    return v


def channel_census(mem):
    """how many of the 1000 channel slots are programmed, per band (counts only)"""
    bands = {name: 0 for name, _, _ in BANDS}
    bands["other"] = 0
    n = 0
    for i in range(CHANNELS):
        rec = mem[i * CHAN_SIZE:(i + 1) * CHAN_SIZE]
        if len(rec) < 4 or rec[:4] in (b"\xff" * 4, b"\x00" * 4):
            continue
        f = bcd_le(rec[:4])
        if f is None:
            continue
        n += 1
        hz = f * 10
        band = next((name for name, lo, hi in BANDS if lo <= hz < hi), "other")
        bands[band] += 1
    return {"programmed": n, "bands": {k: v for k, v in bands.items() if v}}


def hexdump(b, base):
    out = []
    for i in range(0, len(b), 16):
        row = b[i:i + 16]
        out.append(f"{base + i:05x}  {row.hex(' '):<47}  {printable(row)}")
    return out


def cmd_read(a, port=None, log=None):
    log = log or (lambda s: print(s, flush=True))
    try:
        ranges = [parse_extra(e) for e in (a.extra or [])]
        extras = [x for r in ranges for x in r]
    except ValueError as e:
        log(str(e))
        return 3
    if len(extras) > EXTRA_MAX_BLOCKS:
        log(f"--extra asks for {len(extras)} blocks; {EXTRA_MAX_BLOCKS} at most")
        return 3
    names = [a.ident] if a.ident else list(DEFAULT_IDENTS)
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.private, exist_ok=True)
    own = port is None
    port = port or Port(a.port)
    try:
        heard = port.drain(a.listen_s)
        if heard:
            log(f"heard {len(heard)} bytes before the handshake: {printable(heard[:120])!r}")
            if b"K6 " in heard:
                log("REFUSED: that is the K6 talking (its report lines); this tool is for a stock"
                    " radio in programming mode")
                return 4
        got = ident(port, names, log)
        if not got:
            log(f"no answer to {', '.join(names)}: is the radio on, the cable seated, and is it a"
                " UV-17 Pro family radio?")
            return 2
        name, rep = got
        info = {"ident": name, "magic": IDENTS[name].decode(),
                **{k: {"hex": v.hex(), "ascii": printable(v)} for k, v in rep.items()},
                "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        json.dump(info, open(os.path.join(a.out, "ident.json"), "w"), indent=1)
        raw = bytearray()
        regions = []
        bad = []
        for start, size in REGIONS:
            data = bytearray()
            for addr in range(start, start + size, BLOCK):
                hdr, blk = read_block(port, addr)
                raw += hdr + blk
                if len(blk) != BLOCK:
                    bad.append(f"0x{addr:04x}: {len(hdr) + len(blk)} of {BLOCK + 4} bytes")
                    blk = blk.ljust(BLOCK, b"\xff")
                elif hdr[1:] != read_frame(addr)[1:]:
                    bad.append(f"0x{addr:04x}: header {hdr.hex()}")
                data += scramble(blk)
            regions.append((start, bytes(data)))
            log(f"region 0x{start:04x}+0x{size:x}: read" + (f" ({len(bad)} bad blocks so far)" if bad else ""))
            if len(bad) > 4:
                break
        extras_ok = len(bad) <= 4
        extra = []
        silent = 0
        for rng in (ranges if extras_ok else []):
            run = bytearray()
            for addr in rng:
                hdr, blk = read_block(port, addr)
                raw += hdr + blk
                if len(blk) != BLOCK:        # no (whole) answer: skip the rest of this range
                    extra.append({"addr": f"0x{addr:04x}", "status": f"{len(hdr) + len(blk)} bytes",
                                  "header": hdr.hex()})
                    log(f"extra 0x{addr:04x}: {len(hdr) + len(blk)} bytes; skipping to the next range")
                    port.drain(0.3)
                    silent += 1
                    break
                extra.append({"addr": f"0x{addr:04x}", "status": "ok" if hdr[1:] == read_frame(addr)[1:]
                              else f"header {hdr.hex()}", "sha256": hashlib.sha256(scramble(blk)).hexdigest()})
                run += scramble(blk)
            if run:
                regions.append((rng[0], bytes(run)))
            if silent >= 3:
                log("three ranges without an answer: probing stops")
                break
    finally:
        if own:
            port.close()
    open(os.path.join(a.private, "raw.bin"), "wb").write(raw)
    img = b"".join(d for s, d in regions[:len(REGIONS)])
    open(os.path.join(a.private, "clone.img"), "wb").write(img + a.model.encode())
    summary = {"ident": name, "model_tag": a.model, "bad_blocks": bad, "regions": [], "extra": extra}
    dump = []
    for start, d in regions:
        pub = d
        if start == 0x0000:
            summary["channels"] = channel_census(d)
            pub = d[CHANNELS * CHAN_SIZE:]           # 0x7D00-0x8040: VFOs and settings
            start = CHANNELS * CHAN_SIZE
        fn = f"region_{start:04x}.bin"
        open(os.path.join(a.out, fn), "wb").write(pub)
        summary["regions"].append({"addr": f"0x{start:04x}", "size": len(pub), "file": fn,
                                   "sha256": hashlib.sha256(pub).hexdigest(),
                                   "strings": [f"0x{x:04x} {t}" for x, t in strings(pub, start)]})
        dump += [f"# 0x{start:04x} +0x{len(pub):x}"] + hexdump(pub, start)
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    open(os.path.join(a.out, "hexdump.txt"), "w").write("\n".join(dump) + "\n")
    log(f"codeplug {len(img)} bytes -> {os.path.join(a.private, 'clone.img')} (CHIRP: {a.model});"
        f" {summary.get('channels', {}).get('programmed', 0)} channels programmed;"
        f" {len(extra)} extra blocks; {len(bad)} bad blocks")
    log("the radio may stay in its PC/programming screen until it is switched off and on")
    return 3 if bad else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="ident + read the codeplug (read-only)")
    r.add_argument("--port", required=True)
    r.add_argument("--out", required=True, help="run folder: ident, summary, non-channel regions")
    r.add_argument("--private", required=True, help="hamlet-only folder: clone.img, raw.bin")
    r.add_argument("--ident", choices=sorted(IDENTS), help="try only this ident (default: normal, then color)")
    r.add_argument("--model", choices=MODELS, default="UV-21ProV2", help="CHIRP model tag on clone.img")
    r.add_argument("--extra", action="append", help="also read 0xADDR or 0xSTART:0xEND (64-byte blocks)")
    r.add_argument("--listen-s", type=float, default=1.0, help="listen this long before the handshake")
    a = p.parse_args(argv)
    return cmd_read(a)


if __name__ == "__main__":
    sys.exit(main())
