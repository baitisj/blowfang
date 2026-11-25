#!/usr/bin/env python3
"""rt950_backup.py: read-only backup of a Radtel RT-950 Pro over its programming cable.

It speaks the radio's CPS protocol (the handshake, the session XOR key and the read command,
as jaymart1983/Radtel-RT950-Pro-Firmware's tools/cps_flash.py documents them) and sends nothing
but reads. It has no write command: the one place bytes go out refuses a CPS write or write-end
opcode, so a mistake here can't change the radio.

Two modes, for the two stages of docs/rt950-backup.md:

  --addr 2   the stock Radtel firmware, whose read command takes a 2-byte address and so reaches
             the first 64 KB of the SPI flash (channels, settings, calibration at 0xF000).
  --addr 3   the dump-only image (radios/rt950/backup), whose read command takes a 3-byte
             address: the whole 4 MB SPI flash, and the MCU's own flash from 0x800000 (the 12 KB
             bootloader is 0x800000-0x802FFF). It must answer with the identity RT950-BACKUPONLY,
             or nothing is read.

Every range is read at least twice, and a range only counts once two passes hash the same.
Each pass is kept, with a manifest (manifest.json and SHA256SUMS) of what was read, from which
radio identity, and when.

  rt950_backup.py read /dev/serial/by-id/... runs/rt950-backup-20261117/stageA --addr 2
  rt950_backup.py read /dev/serial/by-id/... runs/rt950-backup-20261117/stageB --addr 3
  rt950_backup.py info /dev/serial/by-id/...
  rt950_backup.py compare OLD/cps64k.bin NEW/cps64k.bin --json OUT.json   (which CPS regions and
                         4 KB pages differ, and how many bytes: offsets and counts only, no bytes)
  rt950_backup.py read PORT ~/blowfang-work/private/rt950/ID --summary RUNDIR   (the bench runner's
                         rt950.read: the bytes stay private, RUNDIR gets manifest.json and SHA256SUMS)
  rt950_backup.py read ~/blowfang-work/rt950/ble-pty OUT --addr 2 --block 32 --timeout 4
                         (over Bluetooth, through rt950_ble_bridge.py)

Nothing it writes belongs in git: these are Radtel's firmware and the radio's own calibration.
Needs pyserial. SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import datetime
import hashlib
import json
import os
import random
import sys
import time

HANDSHAKE = b"PROGRAMBT9000U"
ACK = 0x06
CMD_READ = 0x52
CMD_TERMINATE = 0x45
NEVER_SEND = (
    0x57,   # CPS write
    0x58,   # CPS write end
)
BLOCK = 128
BAUD = 115200
BACKUP_IDENTITY = b"RT950-BACKUPONLY"
STOCK_MODEL = "RT-950"         # what the stock firmware answers to M (Jeff's RT-950 Pro, 2026-11-16)

# the OEM's 20 session keys (from its CPS, via cps_flash.py)
XOR_KEYS = [
    b"BHT ",
    b"CO 7",
    b"A ES",
    b" EIY",
    b"M PQ",
    b"XN Y",
    b"RVB ",
    b" HQP",
    b"W RC",
    b"MS N",
    b" SAT",
    b"K DH",
    b"ZO R",
    b"C SL",
    b"6RB ",
    b" JCG",
    b"PN V",
    b"J PK",
    b"EK L",
    b"I LZ",
]

# what each mode reads: (name, first CPS address, length)
STOCK_RANGES = [
    ("cps64k", 0x0000, 0x10000),
]
STOCK_REGIONS = [               # --cps-regions: only what Radtel's CPS itself reads
    ("channels", 0x0000, 31680),
    ("vfo_config", 0x8000, 128),
    ("system_config", 0x9000, 128),
    ("ext_config_1", 0xA000, 384),
    ("ext_config_2", 0xB000, 256),
    ("dtmf_modulation", 0xC000, 256),
    ("aprs_misc", 0xD000, 768),
    ("cal_e000", 0xE000, 128),
    ("cal_f000", 0xF000, 128),
    ("cal_f200", 0xF200, 128),
]
SPI_LEN = 0x400000
MCU_WINDOW = 0x800000
BOOTLOADER_LEN = 0x3000


def xor(data, key):
    """the CPS session XOR (its own inverse): bytes 0x00, 0xFF, the key byte and its complement
    pass through, and so does every byte under a space in the key"""
    out = bytearray(data)
    for i, b in enumerate(out):
        k = key[i % 4]
        if k == 0x20 or b in (0x00, 0xFF, k, k ^ 0xFF):
            continue
        out[i] = b ^ k
    return bytes(out)


def key_packet(rng):
    """-> (the 25-byte SEND packet, the key it selects), as the OEM CPS builds it"""
    selector = (rng.randint(1, 2) << 4) | rng.randint(0, 4)
    pad = bytes(rng.randint(0, 19) for _ in range(19)) + b"\x00"
    pkt = b"SEND" + bytes([selector]) + pad
    if selector & 0x20:
        idx = (selector - 0x20) * 2 + 1
    else:
        idx = (selector - 0x10) * 2
    return pkt, XOR_KEYS[pkt[4 + idx + 1]]


class Radio:
    def __init__(self, port, addr_bytes, baud=BAUD, timeout=2.0, log=None, rng=None, block=BLOCK):
        import serial
        self.ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        self.addr_bytes = addr_bytes
        self.block = block
        self.key = None
        self.identity = b""
        self.model = ""
        self.log = log or (lambda msg: None)
        self.rng = rng or random.Random()

    def send(self, data):
        """the only way out: refuses a write opcode once a session is open"""
        if self.key is not None and data and data[0] in NEVER_SEND:
            raise RuntimeError("refused: rt950_backup never writes to the radio")
        self.ser.write(data)

    def take(self, n, what):
        got = self.ser.read(n)
        if len(got) != n:
            raise TimeoutError(f"{what}: {len(got)} of {n} bytes")
        return got

    def handshake(self):
        self.key = None
        self.ser.reset_input_buffer()
        self.send(HANDSHAKE)
        if self.take(1, "handshake ACK")[0] != ACK:
            raise RuntimeError("no ACK to the handshake: is the radio on, and on the cable?")
        self.send(b"F")
        self.identity = self.take(16, "identity")
        self.send(b"M")
        self.model = self.take(12, "model").rstrip(b"\x00 ").decode("ascii", "replace")
        pkt, key = key_packet(self.rng)
        self.send(pkt)
        if self.take(1, "key ACK")[0] != ACK:
            raise RuntimeError("no ACK to the session key")
        self.key = key
        self.key_index = XOR_KEYS.index(key)
        self.log(f"session: identity {self.identity!r} model {self.model!r}")
        return self.identity, self.model

    def read_block(self, addr, n):
        if self.addr_bytes == 2:
            if addr + n > 0x10000:
                raise ValueError("a 2-byte read can't reach past 64 KB")
            hdr = bytes([CMD_READ, addr >> 8 & 0xFF, addr & 0xFF, n])
        else:
            hdr = bytes([CMD_READ, addr >> 16 & 0xFF, addr >> 8 & 0xFF, addr & 0xFF, n])
        self.send(hdr)
        resp = self.take(len(hdr) + n, f"read 0x{addr:06X}")
        if resp[:len(hdr)] != hdr:
            raise RuntimeError(f"read 0x{addr:06X}: the reply's header was {resp[:len(hdr)].hex()}")
        return xor(resp[len(hdr):], self.key)

    def close(self, terminate):
        try:
            if terminate:
                self.send(bytes([CMD_TERMINATE]))
            self.send(bytes([ACK]))
            time.sleep(0.05)
        finally:
            self.ser.close()


def read_range(radio, start, length, progress, retries=4):
    """one pass over a range; a block that times out is retried after a fresh handshake"""
    out = bytearray()
    a = start
    while a < start + length:
        n = min(radio.block, start + length - a)
        for attempt in range(retries + 1):
            try:
                out += radio.read_block(a, n)
                break
            except (TimeoutError, RuntimeError) as e:
                if attempt == retries:
                    raise
                radio.log(f"retry 0x{a:06X} ({e})")
                time.sleep(2.5)      # (the radio drops a session after 2 s of quiet)
                radio.handshake()
        a += n
        progress(a - start, length)
    return bytes(out)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def backup(radio, outdir, ranges, min_passes=2, max_passes=4, progress=None, terminate=False):
    """reads each range until two passes hash the same; -> the manifest dict. Opens the session
    unless the caller already has (a second handshake inside a session would be read as commands)"""
    os.makedirs(outdir, exist_ok=True)
    if radio.key is None:
        radio.handshake()
    identity, model = radio.identity, radio.model
    man = {
        "tool": "rt950_backup.py",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "addr_bytes": radio.addr_bytes,
        "identity_hex": identity.hex(),
        "identity": identity.decode("ascii", "replace"),
        "model": model,
        "key_index": getattr(radio, "key_index", None),   # the session's XOR key (compare --keys)
        "ranges": [],
    }
    ok = True
    for name, start, length in ranges:
        hashes = []
        agreed = None
        for p in range(1, max_passes + 1):
            data = read_range(radio, start, length, progress or (lambda d, t: None))
            fn = f"{name}.pass{p}.bin"
            with open(os.path.join(outdir, fn), "wb") as f:
                f.write(data)
            h = sha(data)
            hashes.append({"file": fn, "sha256": h})
            radio.log(f"{name} pass {p}: {h}")
            if p >= min_passes and [x["sha256"] for x in hashes].count(h) >= 2:
                agreed = h
                good = fn
                break
        if agreed:
            with open(os.path.join(outdir, good), "rb") as f:
                data = f.read()
            with open(os.path.join(outdir, f"{name}.bin"), "wb") as f:
                f.write(data)
        else:
            ok = False
        entry = {
            "name": name,
            "cps_addr": f"0x{start:06X}",
            "length": length,
            "passes": hashes,
            "agreed_sha256": agreed,
        }
        if agreed:                      # each CPS region inside it (calibration at 0xE000/0xF000/0xF200)
            entry["regions"] = {r: sha(data[a - start:a - start + n]) for r, a, n in STOCK_REGIONS
                                if start <= a and a + n <= start + length}
        man["ranges"].append(entry)
    man["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    man["ok"] = ok
    radio.close(terminate)
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(man, f, indent=2)
        f.write("\n")
    sums = []
    for fn in sorted(os.listdir(outdir)):
        if fn.endswith(".bin"):
            with open(os.path.join(outdir, fn), "rb") as f:
                sums.append(f"{sha(f.read())}  {fn}")
    with open(os.path.join(outdir, "SHA256SUMS"), "w") as f:
        f.write("\n".join(sums) + "\n")
    return man


def write_summary(man, outdir, summary):
    """manifest.json and SHA256SUMS (hashes only, no radio bytes) copied to summary"""
    os.makedirs(summary, exist_ok=True)
    for fn in ("manifest.json", "SHA256SUMS"):
        with open(os.path.join(outdir, fn), "rb") as f, open(os.path.join(summary, fn), "wb") as g:
            g.write(f.read())


def key_pairs(a, b):
    """the (i, j) with xor(a, XOR_KEYS[i]) == xor(b, XOR_KEYS[j]); -1 is no XOR. A region the radio
    sends without the session XOR comes out of a read scrambled by that session's key, so two
    sessions disagree there until each read's own key is put back"""
    ks = [None] + XOR_KEYS
    un_a = [a if k is None else xor(a, k) for k in ks]
    un_b = [b if k is None else xor(b, k) for k in ks]
    return [(i - 1, j - 1) for i, x in enumerate(un_a) for j, y in enumerate(un_b) if x == y]


def compare(a, b, keys=False):
    """two reads of the same CPS window -> which regions and 4 KB pages differ (counts and
    offsets only: the result may leave hamlet, the bytes may not)"""
    out = {"a_sha256": sha(a), "b_sha256": sha(b), "a_len": len(a), "b_len": len(b), "same": a == b}
    n = min(len(a), len(b))
    diff = [i for i in range(n) if a[i] != b[i]]
    def span(lo, hi):
        d = [i for i in diff if lo <= i < hi]
        r = {"differ": len(d)}
        if d:
            r.update(first=f"0x{d[0]:04X}", last=f"0x{d[-1]:04X}")
        return r
    out["regions"] = {name: span(addr, addr + ln) for name, addr, ln in STOCK_REGIONS}
    out["pages_4k"] = {f"0x{p:04X}": span(p, p + 0x1000)["differ"] for p in range(0, n, 0x1000)
                       if span(p, p + 0x1000)["differ"]}
    out["differ"] = len(diff)
    if keys:                            # which session keys would make each differing region agree
        out["key_pairs"] = {}
        for name, addr, ln in STOCK_REGIONS:
            if out["regions"][name]["differ"]:
                pairs = key_pairs(a[addr:addr + ln], b[addr:addr + ln])
                out["key_pairs"][name] = [list(x) for x in pairs[:40]]
    return out


def bar(done, total):
    if done == total or done % 0x1000 == 0:
        print(f"\r  {done * 100 // total:3d}%  {done}/{total}", end="" if done < total else "\n",
              file=sys.stderr, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("read", "info"):
        p = sub.add_parser(name)
        p.add_argument("port")
        if name == "read":
            p.add_argument("outdir")
        p.add_argument("--addr", type=int, choices=(2, 3), default=2,
                       help="2: stock firmware (64 KB); 3: the dump-only image (4 MB + bootloader)")
        p.add_argument("--baud", type=int, default=BAUD)
        p.add_argument("--cps-regions", action="store_true",
                       help="--addr 2 only: read just the regions Radtel's CPS reads")
        p.add_argument("--spi-len", type=lambda s: int(s, 0), default=SPI_LEN)
        p.add_argument("--bootloader-len", type=lambda s: int(s, 0), default=BOOTLOADER_LEN)
        p.add_argument("--passes", type=int, default=2)
        p.add_argument("--block", type=int, default=BLOCK, choices=(8, 16, 32, 64, 128),
                       help="bytes per read (smaller over Bluetooth if 128 stalls)")
        p.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for each reply")
        p.add_argument("-v", "--verbose", action="store_true")
        if name == "read":
            p.add_argument("--summary", metavar="DIR",
                           help="also copy manifest.json and SHA256SUMS (hashes only) into DIR")
    p = sub.add_parser("compare", help="which regions of two 64 KB reads differ (no bytes shown)")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--json", metavar="OUT", help="also write the result here")
    p.add_argument("--keys", action="store_true",
                   help="for each differing region, the session-key pairs (indexes) that make it agree")
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        with open(a.old, "rb") as f, open(a.new, "rb") as g:
            res = compare(f.read(), g.read(), keys=a.keys)
        if a.json:
            with open(a.json, "w") as f:
                json.dump(res, f, indent=2)
                f.write("\n")
        for name, r in res["regions"].items():
            print(f"{name:16s} {'same' if not r['differ'] else str(r['differ']) + ' bytes differ, ' + r['first'] + '..' + r['last']}")
        print(f"{'all 64 KB':16s} {'same' if res['same'] else str(res['differ']) + ' bytes differ'}")
        for name, pairs in res.get("key_pairs", {}).items():
            print(f"{name:16s} agrees with session keys (old, new; -1 none): "
                  + (", ".join(f"({i},{j})" for i, j in pairs) if pairs else "no pair"))
        return 0

    log = (lambda m: print(m, file=sys.stderr)) if a.verbose or a.cmd == "info" else None
    radio = Radio(a.port, a.addr, a.baud, timeout=a.timeout, log=log, block=a.block)
    if a.cmd == "info":
        identity, model = radio.handshake()
        radio.close(a.addr == 3)
        print(json.dumps({"identity_hex": identity.hex(), "identity": identity.decode("ascii", "replace"),
                          "model": model}))
        return 0

    if a.addr == 2:
        _, model = radio.handshake()
        if model != STOCK_MODEL:
            radio.close(False)
            print(f"refused: the radio says model {model!r}, not {STOCK_MODEL!r}", file=sys.stderr)
            return 2
        ranges = STOCK_REGIONS if a.cps_regions else STOCK_RANGES
    else:
        identity, _ = radio.handshake()
        if identity != BACKUP_IDENTITY:
            radio.close(False)
            print(f"refused: the radio says {identity!r}, not {BACKUP_IDENTITY!r}; "
                  "--addr 3 is only for the dump-only image", file=sys.stderr)
            return 2
        ranges = [
            ("spi4m", 0x000000, a.spi_len),
            ("bootloader", MCU_WINDOW, a.bootloader_len),
        ]
    man = backup(radio, a.outdir, ranges, min_passes=a.passes, progress=bar, terminate=a.addr == 3)
    if a.summary:
        write_summary(man, a.outdir, a.summary)
    for r in man["ranges"]:
        print(f"{r['name']:12s} {r['length']:8d} B  {'agreed ' + r['agreed_sha256'] if r['agreed_sha256'] else 'NO TWO PASSES AGREE'}")
    return 0 if man["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
