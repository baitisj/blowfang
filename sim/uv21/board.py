"""A UV-21 Pro running the real port image (radios/uv21/build/port.bin) in the emulated AT32F421
of tests/test_uv21_port.py, with what that test model leaves out for an interactive run:

- time: every SysTick wait the firmware makes (delay::us, delay::ms) moves a virtual clock by its
  length, and TMR14 (the firmware's uptime) counts that clock, so SURF's 3 ms and 12 ms reads,
  its 200 ms screen and the 60 s wake rest all run at their real proportions;
- the RF chip's registers keep what was written, and REG_67 (RSSI) reads the level of a model
  band at the frequency REG_38/39 tune: stations with a level, a width and a keying pattern
  (carrier, voice overs, a CW ident), over a noise floor, through the wide filter's skirts;
- keys by name (the faceplate's), held for a real 150 ms or longer.

Nothing here is a radio measurement: the levels and the filter are a model, made to give SURF
peaks to find. SPDX-License-Identifier: MIT"""
from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_uv21_bringup as bu  # noqa: E402
import test_uv21_port as tp  # noqa: E402

UV21 = tp.UV21
SCLK = 60_000_000
SYST_CSR, SYST_RVR = 0xE000E010, 0xE000E014
TMR14_CVAL = 0x40002024
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

MORSE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
         "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
         "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
         "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..", "0": "-----",
         "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....",
         "7": "--...", "8": "---..", "9": "----.", "/": "-..-."}


def morse_keying(text, wpm=20):
    """[(on, seconds), ...] for text in CW at wpm (a dot is 1.2 / wpm s)"""
    dot = 1.2 / wpm
    out = []
    for w, word in enumerate(text.upper().split()):
        if w:
            out.append((0, 7 * dot))
        for c, ch in enumerate(word):
            if c:
                out.append((0, 3 * dot))
            for e, el in enumerate(MORSE.get(ch, "")):
                if e:
                    out.append((0, dot))
                out.append((1, dot if el == "." else 3 * dot))
    return out


@dataclass
class Station:
    """one signal of the model band"""
    name: str
    hz: int
    dbm: float
    kind: str = "carrier"          # carrier | voice (overs) | cw (an ident every period_s) | off
    width_hz: int = 6000           # half the occupied width: full level inside it
    on_s: float = 6.0              # voice: an over this long ...
    period_s: float = 20.0         # ... every period (voice), or an ident every period (cw)
    phase_s: float = 0.0
    text: str = "AG7EW"            # cw: what it sends
    enabled: bool = True
    _cw: list = field(default_factory=list, repr=False)

    def keyed(self, t):
        if not self.enabled or self.kind == "off":
            return False
        if self.kind == "carrier":
            return True
        u = (t + self.phase_s) % self.period_s
        if self.kind == "voice":
            return u < self.on_s
        if not self._cw:
            self._cw = morse_keying(self.text)
        for on, d in self._cw:
            if u < d:
                return bool(on)
            u -= d
        return False


def default_band():
    """made-up 70 cm activity round 440 MHz (no real station's data), and the CW wake beacon on
    440.850, the frequency the port listens on for it"""
    return [
        Station("repeater A", 441_200_000, -71, "voice", on_s=8, period_s=25, phase_s=3),
        Station("repeater B", 442_900_000, -88, "voice", on_s=5, period_s=17, phase_s=9),
        Station("simplex", 446_000_000, -95, "voice", on_s=4, period_s=30, phase_s=14),
        Station("beacon", 432_300_000, -102, "carrier", width_hz=1500),
        Station("data burst", 439_975_000, -84, "voice", on_s=0.4, period_s=1.1),
        Station("wake (CW)", 440_850_000, -78, "cw", period_s=15, text="AG7EW AG7EW"),
    ]


class UV21Board(tp.Port):
    """the port image on the emulated board, with time, the RF chip's registers and the band"""

    def __init__(self, image=None, syms=None, band=None, floor_dbm=-125.0, seed=1):
        if image is None:
            image, syms = build()
        super().__init__(image, syms)
        self.cycles = 0                         # the virtual clock, in 60 MHz cycles
        self.syst_due = 0
        self.regs = {}
        self.band = default_band() if band is None else band
        self.floor = floor_dbm
        self.rng = random.Random(seed)
        self.holding = None                     # (key, release at cycles)
        self.tx_watch = []                      # every write to REG_30/33/36 (state, GPIO, PA)
        self.instructions = 0

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
        return 10 * math.log10(p) + self.rng.gauss(0, 1.0)

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
