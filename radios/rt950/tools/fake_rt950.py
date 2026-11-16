#!/usr/bin/env python3
"""fake_rt950.py: an RT-950 Pro's CPS port on a pseudo-terminal, for dry runs of rt950_backup.py.

It answers as the stock firmware does (--mode stock: 2-byte read addresses, a 64 KB view) or as
the dump-only image does (--mode backup: 3-byte addresses, the 4 MB SPI flash and the MCU's
flash from 0x800000, identity RT950-BACKUPONLY), with a made-up image, and the session XOR key
picked from the host's SEND packet as the radio picks it. It counts every write or write-end
opcode it is sent, which must stay 0, and can drop one reply or flip a bit in one, to exercise the tool's retry and its extra pass.

  python3 fake_rt950.py --mode backup      prints the pty's path, then serves until killed
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import os
import random
import sys
import threading
import tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rt950_backup import XOR_KEYS
from rt950_backup import xor

STOCK_IDENTITY = b"RT950PRO-STOCK\x00\x00"   # (made up: the real one is recorded on the day)
BACKUP_IDENTITY = b"RT950-BACKUPONLY"
MODEL = b"RT-950      "


def image(seed=1, spi_len=0x400000, mcu_len=0x3000):
    """-> (spi bytes, mcu bytes): random with runs of 0x00 and 0xFF, as flash is"""
    rng = random.Random(seed)
    spi = bytearray(rng.randbytes(spi_len))
    for _ in range(spi_len // 4096):
        a = rng.randrange(spi_len - 256)
        n = rng.randrange(256)
        spi[a:a + n] = b"\xff" * n
    mcu = bytearray(rng.randbytes(mcu_len))
    return bytes(spi), bytes(mcu)


class FakeRadio:
    def __init__(self, mode="backup", spi=None, mcu=None, drop_at=None, corrupt_at=None):
        self.mode = mode
        self.spi, self.mcu = (spi, mcu) if spi is not None else image()
        self.drop_at = drop_at          # a read address whose first reply is dropped
        self.corrupt_at = corrupt_at    # a read address whose first reply has a bit flipped
        self.writes = 0
        self.reads = 0
        self.sessions = 0
        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.path = os.ttyname(slave)
        self._slave = slave
        self._stop = False

    def _mem(self, addr, n):
        if self.mode == "backup" and addr >= 0x800000:
            off = addr - 0x800000
            return (self.mcu[off:off + n] + b"\xff" * n)[:n]
        if self.mode == "stock":
            addr &= 0xFFFF
        return (self.spi[addr:addr + n] + b"\xff" * n)[:n]

    def _get(self, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = os.read(self.master, n - len(buf))
            except OSError:
                raise EOFError
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def serve(self):
        """the radio's side, until the pty closes"""
        abytes = 2 if self.mode == "stock" else 3
        key = None
        hs = b""
        try:
            while not self._stop:
                if key is None:
                    hs = (hs + self._get(1))[-14:]
                    if hs != b"PROGRAMBT9000U":
                        continue
                    os.write(self.master, b"\x06")
                    while self._get(1) != b"F":
                        pass
                    os.write(self.master, STOCK_IDENTITY if self.mode == "stock" else BACKUP_IDENTITY)
                    while self._get(1) != b"M":
                        pass
                    os.write(self.master, MODEL)
                    pkt = self._get(25)
                    sel = pkt[4]
                    idx = ((sel - 0x20) * 2 + 1 if sel & 0x20 else (sel - 0x10) * 2) + 1
                    key = XOR_KEYS[pkt[4 + idx]]
                    os.write(self.master, b"\x06")
                    self.sessions += 1
                    hs = b""
                    continue
                op = self._get(1)[0]
                if op == 0x52:
                    rest = self._get(abytes + 1)
                    addr = int.from_bytes(rest[:abytes], "big")
                    n = rest[abytes]
                    self.reads += 1
                    if addr == self.drop_at:
                        self.drop_at = None
                        key = None          # (the radio gave up on the session)
                        continue
                    data = bytearray(self._mem(addr, n))
                    if addr == self.corrupt_at:
                        self.corrupt_at = None
                        data[0] ^= 0x10
                    os.write(self.master, bytes([op]) + rest + xor(bytes(data), key))
                elif op in (0x57, 0x58):
                    self.writes += 1
                elif op == 0x45:
                    key = None
                elif op == 0x06:
                    key = None
        except EOFError:
            pass

    def start(self):
        t = threading.Thread(target=self.serve, daemon=True)
        t.start()
        return t

    def close(self):
        self._stop = True
        for fd in (self._slave, self.master):
            try:
                os.close(fd)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=("stock", "backup"), default="backup")
    a = ap.parse_args()
    r = FakeRadio(a.mode)
    print(r.path, flush=True)
    r.serve()


if __name__ == "__main__":
    main()
