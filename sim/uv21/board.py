"""A UV-21 Pro running the real port image (radios/uv21/build/port.bin) in the emulated AT32F421
of tests/test_uv21_port.py, with what that test model leaves out for an interactive run:

- time: every SysTick wait the firmware makes (delay::us, delay::ms) moves a virtual clock by its
  length, and TMR14 (the firmware's uptime) counts that clock, so SURF's 3 ms and 12 ms reads,
  its 200 ms screen and the 60 s wake rest all run at their real proportions;
- the RF chip's registers keep what was written, and REG_67 (RSSI) reads the level of a model
  band at the frequency REG_38/39 tune: stations with a level, a width and a keying pattern
  (carrier, voice overs, a CW ident), over a noise floor, through the wide filter's skirts;
- keys by name (the faceplate's), held for a real 150 ms or longer;
- fast_bus (on by default): the image's RF register read (Fd6818::read_reg), the byte shifter
  under its register write (Fd6818::write_bits) and its microsecond waits (hal::delay::us) are
  answered at their entry by a code hook, with the time they would take added to the virtual
  clock, instead of bit by bit through the GPIO model (about 300 Python callbacks a read, 200 a
  write): SURF runs about 2.7 times faster than with reads alone (1.3x real time on one 2.1 GHz
  core). A write still runs write_reg itself, so the image's own receive-only gate (rx_only) and
  the chip-select edges are the image's: the three bytes it shifts land in the same transaction
  the bit-level model builds, and tx_watch sees exactly what the image sends.

Nothing here is a radio measurement: the levels and the filter are a model, made to give SURF
peaks to find. SPDX-License-Identifier: MIT"""
from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
try:                                    # the helpers below live in test modules that import pytest
    import pytest  # noqa: F401
except ImportError:                     # a machine without it (hamlet): just the two names they use
    import importlib
    import types
    _pt = types.ModuleType("pytest")
    _pt.importorskip = importlib.import_module
    _pt.fixture = lambda f=None, **kw: f if callable(f) else (lambda g: g)
    sys.modules["pytest"] = _pt
import test_uv21_bringup as bu  # noqa: E402
import test_uv21_port as tp  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0  # noqa: E402

UV21 = tp.UV21
SCLK = 60_000_000
SYST_CSR, SYST_RVR = 0xE000E010, 0xE000E014
TMR14_CVAL = 0x40002024
READ_REG_US = 52                # Fd6818::read_reg: 52 waits of BIT_DELAY_US (1 us), fd6818b.rs
WRITE_BITS_US = 16              # Fd6818::write_bits: 8 bits of two 1 us waits
B, C, F = bu.B, bu.C, bu.F

# the faceplate: matrix keys as (column, row) of tests/test_uv21_bringup.py's KEY_COLS / KEY_ROWS
# (radios/uv21/fw/src/device/keypad.rs KEY_TABLE), side keys as the row line they ground
MATRIX = {
    "VM": (0, 0), "MENU": (0, 1), "AB": (0, 3),
    "1": (1, 0), "4": (1, 1), "UP": (1, 2), "7": (1, 3),
    "*": (2, 0), "0": (2, 1), "EXIT": (2, 2), "#": (2, 3),
    "2": (3, 0), "5": (3, 1), "DOWN": (3, 2), "8": (3, 3),
    "3": (4, 0), "6": (4, 1), "9": (4, 3),
}
SIDE = {"SIDE1": (C, 14), "SIDE2": (B, 14), "BAND": (F, 6)}   # top, middle, bottom
KEYS = tuple(MATRIX) + tuple(SIDE)

from sim.band import MORSE, Station, default_band, morse_keying  # noqa: E402,F401  (were here)


class UV21Board(tp.Port):
    """the port image on the emulated board, with time, the RF chip's registers and the band"""

    def __init__(self, image=None, syms=None, band=None, floor_dbm=-125.0, seed=1, fast_bus=True,
                 noise_db=1.0):
        if image is None:
            image, syms = build()
        super().__init__(image, syms)
        self.cycles = 0                         # the virtual clock, in 60 MHz cycles
        self.syst_due = 0
        self.regs = {}
        self.band = default_band() if band is None else band
        self.floor = floor_dbm
        self.noise_db = noise_db            # the RSSI's spread on the floor, dB (1 sigma)
        self.rng = random.Random(seed)
        self.holding = None                     # (key, release at cycles)
        self.tx_watch = []                      # every write to REG_30/33/36 (state, GPIO, PA)
        self.instructions = 0
        self.fast_bus = False
        self.fast_reads = 0
        self.fast_writes = 0
        if fast_bus:
            rd = _sym(self.syms, "fd6818b", "8read_reg")
            wb = _sym(self.syms, "fd6818b", "10write_bits")
            us = _sym(self.syms, "5delay2us")
            if rd and wb and us:
                self.mu.hook_add(UC_HOOK_CODE, self._fast_read, begin=rd, end=rd)
                self.mu.hook_add(UC_HOOK_CODE, self._fast_bits, begin=wb, end=wb)
                self.mu.hook_add(UC_HOOK_CODE, self._fast_us, begin=us, end=us)
                self.fast_bus = True

    # ---- time ----
    @property
    def seconds(self):
        return self.cycles / SCLK

    def core(self, uc, access, addr, size, value, ud):
        if addr == SYST_CSR:
            if access == tp.UC_MEM_WRITE:
                if value & 1:                   # the counter enabled: a wait of RVR + 1 cycles
                    self.syst_due = (struct.unpack("<I", uc.mem_read(SYST_RVR, 4))[0] & 0xFFFFFF) + 1
                return super().core(uc, access, addr, size, value, ud)
            if self.syst_due:                   # its first look at COUNTFLAG: the wait is over
                self.cycles += self.syst_due
                self.syst_due = 0
        return super().core(uc, access, addr, size, value, ud)

    def mmio(self, uc, access, addr, size, value, ud):
        if addr == TMR14_CVAL and access != tp.UC_MEM_WRITE:
            self.cycles += 600                  # a look at the clock costs 10 us
            uc.mem_write(addr, struct.pack("<I", (self.cycles // 6000) & 0xFFFF))
            return
        return super().mmio(uc, access, addr, size, value, ud)

    # ---- the RF chip ----
    def tuned_hz(self):
        return ((self.regs.get(0x39, 0) << 16) | self.regs.get(0x38, 0)) * 10

    def level_dbm(self, hz, t=None):
        t = self.seconds if t is None else t
        p = 10 ** (self.floor / 10)
        for s in self.band:
            if not s.keyed(t):
                continue
            d = abs(hz - s.hz)
            # the wide filter (+-12.5 kHz): flat inside the signal's own width, then its skirt
            att = 0.0 if d <= s.width_hz else min(90.0, (d - s.width_hz) / 1000.0 * 6.0)
            p += 10 ** ((s.dbm - att) / 10)
        return 10 * math.log10(p) + self.rng.gauss(0, self.noise_db)

    def reg_value(self, a):
        if a == 0:
            return 0x4829
        if a == 0x67:                           # RSSI: (dBm + 174) x 2 in bits 8:0
            raw = int(round((self.level_dbm(self.tuned_hz()) + 174) * 2))
            return max(0, min(0x1FF, raw))
        return self.regs.get(a, 0)

    def rf(self, lv, rise, fall):
        if bu.RF_SCN in rise and self.rf_txn is not None:
            t = self.rf_txn
            if t["addr"] is not None and not t["addr"] & 0x80 and len(t["out"]) >= 24:
                a, v = t["addr"] & 0x7F, int("".join(map(str, t["out"][8:24])), 2)
                self.regs[a] = v
                if a in (0x30, 0x33, 0x36):
                    self.tx_watch.append((a, v))
        if bu.RF_SCN in fall:
            self.rf_txn = {"out": [], "addr": None}
        if bu.RF_SCN in rise and self.rf_txn is not None:
            self.rf_log.append(self.rf_txn)
            if len(self.rf_log) > 4000:
                del self.rf_log[:2000]
            self.rf_txn = None
        t = self.rf_txn
        if t is None or bu.RF_SCK not in rise:
            return
        if self.pins.mode(*bu.RF_SDA) == 1:
            t["out"].append(lv[bu.RF_SDA])
            if len(t["out"]) == 8:
                t["addr"] = int("".join(map(str, t["out"])), 2)
                v = self.reg_value(t["addr"] & 0x7F)
                self.rf_bits = [(v >> (15 - i)) & 1 for i in range(16)]
        else:
            self.ext[bu.RF_SDA] = self.rf_bits.pop(0) if self.rf_bits else 1

    # ---- the fast bus: called at the first instruction of read_reg / delay::us ----
    # (read_reg's Fd6818 is zero-sized after inlining: the register number arrives in R0, the
    # value goes back in R0; the disassembly's first use is `orn r0, r4, #0x7f` on the R0 copy)
    def _return(self, uc):
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)

    def _fast_read(self, uc, address, size, ud):
        a = uc.reg_read(UC_ARM_REG_R0) & 0x7F
        uc.reg_write(UC_ARM_REG_R0, self.reg_value(a) & 0xFFFF)
        self.cycles += READ_REG_US * (SCLK // 1_000_000)
        self.fast_reads += 1
        self._return(uc)

    def _fast_bits(self, uc, address, size, ud):
        """write_bits(byte in R0): its 8 bits into the open transaction, MSB first, as rf() would
        have taken them off SDA at each SCK rise (outside a transaction they go nowhere, as there)"""
        byte = uc.reg_read(UC_ARM_REG_R0) & 0xFF
        t = self.rf_txn
        if t is not None:
            t["out"].extend((byte >> (7 - i)) & 1 for i in range(8))
            if len(t["out"]) == 8:
                t["addr"] = byte
        self.cycles += WRITE_BITS_US * (SCLK // 1_000_000)
        self.fast_writes += 1
        self._return(uc)

    def _fast_us(self, uc, address, size, ud):
        self.cycles += max(1, uc.reg_read(UC_ARM_REG_R0) * (SCLK // 1_000_000))
        self._return(uc)

    # ---- keys ----
    def press(self, key, down=True):
        if key in SIDE:
            if down:
                self.ext[SIDE[key]] = 0
            else:
                self.ext.pop(SIDE[key], None)
        else:
            (self.pressed.add if down else self.pressed.discard)(MATRIX[key])

    def tap(self, key, hold_s=0.15):
        """press now; step() lets go after hold_s of virtual time"""
        self.press(key, True)
        self.holding = (key, self.cycles + int(hold_s * SCLK))

    def step(self, instructions=2_000_000):
        """run; a tapped key is let go on time (checked every 100k instructions while held)"""
        out = ""
        while instructions > 0:
            n = min(instructions, 100_000) if self.holding else instructions
            out += self.run(limit=n)
            instructions -= n
            self.instructions += n
            if self.holding and self.cycles >= self.holding[1]:
                self.press(self.holding[0], False)
                self.holding = None
        return out

    # ---- the screen ----
    def screen_rgb(self):
        """160 rows of 128 (r, g, b), unpainted pixels dark purple"""
        rows = []
        for y in range(160):
            row = []
            for x in range(128):
                c = self.lfb[y][x]
                if c is None:
                    row.append((40, 0, 40))
                else:
                    row.append((((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31))
            rows.append(row)
        return rows

    def screen_png(self, scale=3):
        import zlib
        rows = self.screen_rgb()
        w, h = 128 * scale, 160 * scale
        raw = bytearray()
        for row in rows:
            line = bytearray([0])
            for px in row:
                line += bytes(px) * scale
            raw += bytes(line) * scale

        def chunk(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def _sym(syms, *parts):
    """the address of the one symbol whose (mangled) name holds every part, or None"""
    hits = [a for n, a in syms.items() if all(p in n for p in parts)]
    return hits[0] & ~1 if len(hits) == 1 else None


def build():
    """make the port image and read its symbols (tests/test_uv21_port.py's fixture, as a function)"""
    r = subprocess.run(["make", "-s", "-C", UV21, "port"], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("make -C radios/uv21 port failed:\n" + r.stdout[-2000:] + r.stderr[-4000:])
    image = open(os.path.join(UV21, "build", "port.bin"), "rb").read()
    nm = subprocess.run(["llvm-nm", os.path.join(UV21, "build", "port.elf")], check=True,
                        capture_output=True, text=True).stdout
    syms = {ln.split()[2]: int(ln.split()[0], 16) for ln in nm.splitlines() if len(ln.split()) == 3}
    return image, syms
