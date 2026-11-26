"""The RT-950 Pro's SURF image (jaymart1983's base firmware with blowfang's dual-chip SURF linked
in: radios/rt950, `make surf-image`) on an emulated board: an AT32F403A (Cortex-M4F, 120 MHz)
in unicorn, with what the image touches modelled around it.

- GPIO: the F1-style ports A-E (CRL/CRH/IDR/ODR/SCR/CLR). Inputs read what the board drives:
  the power switch on (PE0 low), PTT and the side keys up unless pressed, the keypad matrix
  (columns PC0-PC3 driven, rows PD4-PD7 read, keys 1/4/7/* on no column), and the BK4829 bus's
  data line while the chip answers a read.
- The two BK4829s: the shared bit-banged bus (PE10 clock, PE11 data, chip selects PE8 for chip 0
  and PE15 for chip 1) decoded edge by edge, or, with fast_bus (the default), the driver's
  register calls answered directly at bk4829_read_reg / bk4829_write_reg (their .part.0 bodies,
  which every caller reaches). Each chip keeps the registers written to it; REG_00 reads 0x4829;
  REG_67 (RSSI) is the level at the chip's own tuned frequency (REG_38/39 in 10 Hz units) as
  dBm + 160, x 2 (radios/rt950/port/jm_glue.c: RT_RSSI_OFS).
- Both chips hear the same antenna: level_dbm(chip, hz) answers from one band of Stations
  (sim/uv21/board.py) here, and from SoapyFang's chip model in sim/rt950/soapy.py.
- The LCD: the ST7789V on its 8080 bus (PD8-PD15 data, WR PD0, CS PD1, D/C PD3), 240 x 320,
  decoded from the pins or, with fast_lcd (the default), at lcd_write_command / lcd_write_data
  and whole lcd_fill_rect / lcd_draw_string(_2x) calls (drawn with the image's own font).
- Time: one cycle an instruction at 120 MHz. The SysTick interrupt's whole job (systick_ms += 1)
  is done by writing the image's systick_ms from that clock; delay_ms jumps the clock; the DWT
  cycle counter (jm_glue.c's delay_us) advances on each read.
- Clocks, ADC, SPI2 (the SPI flash, blank, reads 0xFF; writes recorded, never kept past the run),
  the USARTs and the flash controller answer "ready". Nothing else is modelled: the base's audio
  DDS, the Si4732, Bluetooth and GPS sit idle, as their interrupts never fire.

What it is for: the SURF screen and its keys from emulated firmware, and SoapyFang's real I/Q into
both chips. What it can't show: audio, and anything timing-exact on the RF bus.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import math
import os
import random
import struct
import subprocess

from unicorn import (UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE,
                     UC_MODE_MCLASS, UC_MODE_THUMB, Uc)
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_CPU_ARM_CORTEX_M4)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RT950 = os.path.join(ROOT, "radios", "rt950")
CPU_HZ = 120_000_000
APP = 0x08003000                        # the app area, after the 12 KB bootloader
GPIO = 0x40010800                       # GPIOA; B-E follow at 0x400 apart
A, B, C, D, E = range(5)
RF_SCK, RF_SDA, RF_CS = (E, 10), (E, 11), {0: (E, 8), 1: (E, 15)}
LCD_WR, LCD_CS, LCD_DC = (D, 0), (D, 1), (D, 3)
FLASH_CS = (B, 12)
SPI2_SR, SPI2_DR = 0x40003808, 0x4000380C
CRM_CR, CRM_CFGR = 0x40021000, 0x40021004
DWT_CYCCNT = 0xE0001004
AIRCR = 0xE000ED0C
W, H = 240, 320
# keys: the matrix position (column 0-3 = PC0-PC3 driven low, 4 = on no column; row 0-3 = PD7-PD4)
# from the base firmware's scan_keymap, or a side input pulled low
MATRIX = {
    "OK": (0, 0), "ABC": (0, 1), "BACK": (0, 2), "VM": (0, 3),
    "3": (1, 0), "6": (1, 1), "9": (1, 2), "#": (1, 3),
    "2": (2, 0), "5": (2, 1), "8": (2, 2), "0": (2, 3),
    "UP": (3, 0), "DOWN": (3, 1), "LEFT": (3, 2), "RIGHT": (3, 3),
    "1": (4, 0), "4": (4, 1), "7": (4, 2), "*": (4, 3),
}
SIDE = {"SIDE1": (E, 5), "SIDE4": (A, 12), "PTT": (E, 3), "PTT2": (E, 2)}
ROW_PIN = {0: 7, 1: 6, 2: 5, 3: 4}      # row index -> PD pin
# the BK4829 states the base firmware's bk4829_enable_tx writes (REG_30): never seen here
REG_STATE = 0x30
# pins whose going high would mean transmit: PTT relay, V3T_EN PB1, SW3T_EN PE14
TX_PINS = {(B, 1), (E, 14)}


def build(jm=None):
    """make surf-image against a checkout of the base firmware (JM, default radios/rt950's ../jm
    or $RT950_JM); returns (image bytes at APP, symbols)"""
    jm = jm or os.environ.get("RT950_JM") or os.path.join(ROOT, "jm")
    subprocess.run(["make", "-s", "-C", RT950, "surf-image", f"JM={jm}"], check=True)
    return load(os.path.join(RT950, "build", "rt950-surf2.bin"), os.path.join(RT950, "build", "rt950-surf2.elf"))


def load(binary, elf):
    image = open(binary, "rb").read()
    nm = subprocess.run(["llvm-nm", elf], check=True, capture_output=True, text=True).stdout
    syms = {}
    for ln in nm.splitlines():
        f = ln.split()
        if len(f) == 3:
            syms.setdefault(f[2], int(f[0], 16))
    return image, syms


def nm_file(elf, out):
    """the symbol listing SoapyFang's runner step takes beside the image (llvm-nm's format)"""
    with open(out, "w") as f:
        f.write(subprocess.run(["llvm-nm", elf], check=True, capture_output=True, text=True).stdout)


def symbols(path):
    syms = {}
    for ln in open(path):
        f = ln.split()
        if len(f) == 3:
            syms.setdefault(f[2], int(f[0], 16))
    return syms


class Chip:
    """one BK4829's registers as the firmware wrote them"""

    def __init__(self, n):
        self.n = n
        self.regs = {}
        self.writes = 0
        self.states = []                # every REG_30 value written

    @property
    def hz(self):
        return ((self.regs.get(0x39, 0) << 16) | self.regs.get(0x38, 0)) * 10


class RT950Board:
    def __init__(self, image=None, syms=None, band=None, floor_dbm=-125.0, noise_db=1.0, seed=1,
                 fast_bus=True, fast_lcd=True):
        if image is None:
            image, syms = build()
        self.image, self.syms = image, syms
        self.cycles = 0
        self.chips = [Chip(0), Chip(1)]
        if band is None:
            from sim.band import default_band
            band = default_band()
        self.band = band
        self.floor, self.noise_db, self.rng = floor_dbm, noise_db, random.Random(seed)
        # GPIO: per port CRL|CRH as one 64-bit config, ODR
        self.cfg = [0x4444444444444444] * 5     # reset: every pin a floating input
        self.odr = [0] * 5
        self.ext = {(E, 0): 0}                  # the power switch on
        self.pressed = set()
        self.holding = None
        self.driven_high = set()                # every pin the image ever drove high
        # the RF bus
        self.rf_txn, self.rf_bits = None, []
        self.rf_log = []                        # (chip, reg, value, "r" | "w"), the last 4000
        self.fast_reads = self.fast_writes = 0
        # the LCD
        self.fb = bytearray(W * H * 2)          # RGB565, big-endian as sent
        self.lcd = {"cmd": None, "args": [], "x": (0, W - 1), "y": (0, H - 1), "px": None,
                    "hi": None, "madctl": 0, "on": False}
        self.lcd_bytes = 0
        self.frames = 0                         # RAMWR commands seen: how often the screen moved
        # SPI flash
        self.flash_q, self.flash_ops = None, []
        self.uart = bytearray()
        self.reset_requested = False
        self.flash_ctrl_writes = []
        self._ms = -1

        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        mu.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M4)
        mu.mem_map(0x08000000, 0x100000)                # internal flash, 1 MB
        mu.mem_write(0x08000000, b"\xff" * 0x100000)
        mu.mem_write(APP, image)
        mu.mem_map(0x1FFFE000, 0x2000)                  # system memory (UID, option bytes)
        mu.mem_map(0x20000000, 0x20000)                 # SRAM (96 KB; 128 KB mapped)
        mu.mem_map(0x40000000, 0x30000)                 # APB1, APB2, AHB peripherals
        mu.mem_map(0xE0000000, 0x100000)                # the core's private peripherals
        mu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.mmio, begin=0x40000000, end=0x4002FFFF)
        mu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.core, begin=0xE0000000, end=0xE00FFFFF)
        self.font = self._find_font(image)
        self.fast_bus = fast_bus and self._hook({
            "bk4829_read_reg.part.0": self._fast_rf_read,
            "bk4829_write_reg.part.0": self._fast_rf_write,
        })
        self.fast_lcd = fast_lcd and self.font is not None and self._hook({
            "lcd_write_command": self._fast_lcd_cmd,
            "lcd_write_data": self._fast_lcd_data,
            "lcd_fill_rect": self._fast_fill,
            "lcd_draw_string": self._fast_text,
            "lcd_draw_string_2x": self._fast_text2,
        })
        self._hook({"delay_us": self._fast_delay_us})
        for k, f in (("delay_ms", self._fast_delay_ms),):
            if k in syms:
                mu.hook_add(UC_HOOK_CODE, f, begin=syms[k] & ~1, end=syms[k] & ~1)
        sp, pc = struct.unpack_from("<II", image, 0)
        mu.reg_write(UC_ARM_REG_SP, sp)
        self.pc = pc | 1
        self.tick_addr = syms.get("systick_ms")

    def _hook(self, hooks):
        """a code hook at each function's first instruction, if the image has them all"""
        if not all(k in self.syms for k in hooks):
            return False
        for k, f in hooks.items():
            a = self.syms[k] & ~1
            self.mu.hook_add(UC_HOOK_CODE, f, begin=a, end=a)
        return True

    # ---- time ----
    @property
    def seconds(self):
        return self.cycles / CPU_HZ

    def advance(self, cycles):
        self.cycles += int(cycles)
        self._tick()

    def _tick(self):
        ms = self.cycles * 1000 // CPU_HZ
        if ms != self._ms and self.tick_addr is not None:
            self._ms = ms
            self.mu.mem_write(self.tick_addr, struct.pack("<I", ms & 0xFFFFFFFF))

    # ---- GPIO ----
    def mode(self, port, bit):
        """0 input, 1 output, 2 alternate function output"""
        m = (self.cfg[port] >> (4 * bit)) & 0xF
        if not m & 3:
            return 0
        return 2 if m & 8 else 1

    def level(self, port, bit):
        if self.mode(port, bit):
            return (self.odr[port] >> bit) & 1
        if (port, bit) in self.ext:
            return self.ext[(port, bit)]
        if port == D and 4 <= bit <= 7:             # a keypad row
            row = {7: 0, 6: 1, 5: 2, 4: 3}[bit]
            for key in self.pressed:
                c, r = MATRIX[key]
                if r != row:
                    continue
                if c == 4 or (self.mode(C, c) and not (self.odr[C] >> c) & 1):
                    return 0
        cnf = (self.cfg[port] >> (4 * bit + 2)) & 3
        if cnf == 2:                                # pull-up or pull-down: the ODR bit chooses
            return (self.odr[port] >> bit) & 1
        return 1

    def idr(self, port):
        return sum(self.level(port, b) << b for b in range(16))

    def _odr_set(self, port, new):
        old, self.odr[port] = self.odr[port], new & 0xFFFF
        moved = old ^ self.odr[port]
        if not moved:
            return
        for b in range(16):
            if moved >> b & 1 and self.odr[port] >> b & 1 and self.mode(port, b):
                self.driven_high.add((port, b))
        if port == E and moved & 0x8D00:            # PE8 PE10 PE11 PE15: the RF bus
            self._rf_edges(old, self.odr[port])
        elif port == D and moved & 0x0001 and self.odr[D] & 1 and not self.odr[D] & 2:
            self._lcd_byte((self.odr[D] >> 8) & 0xFF, (self.odr[D] >> 3) & 1)   # WR rose, CS low

    # ---- the RF bus, edge by edge (fast_bus off) ----
    def _rf_edges(self, old, new):
        for chip, (_, cs) in RF_CS.items():
            if old >> cs & 1 and not new >> cs & 1:
                self.rf_txn = {"chip": chip, "out": [], "addr": None}
            if not old >> cs & 1 and new >> cs & 1 and self.rf_txn and self.rf_txn["chip"] == chip:
                t, self.rf_txn = self.rf_txn, None
                if t["addr"] is not None and not t["addr"] & 0x80 and len(t["out"]) >= 24:
                    v = int("".join(map(str, t["out"][8:24])), 2)
                    self.rf_write(chip, t["addr"] & 0x7F, v)
        t = self.rf_txn
        if t is None or not (not old >> 10 & 1 and new >> 10 & 1):     # SCK rising only
            return
        if self.mode(*RF_SDA):
            t["out"].append(new >> 11 & 1)
            if len(t["out"]) == 8:
                t["addr"] = int("".join(map(str, t["out"])), 2)
                if t["addr"] & 0x80:
                    v = self.rf_read(t["chip"], t["addr"] & 0x7F)
                    self.rf_bits = [(v >> (15 - i)) & 1 for i in range(16)]
        else:
            self.ext[RF_SDA] = self.rf_bits.pop(0) if self.rf_bits else 1

    # ---- the two chips ----
    def level_dbm(self, chip, hz):
        """the antenna's level at hz for one chip (both hear the same band)"""
        t = self.seconds
        p = 10 ** (self.floor / 10)
        for s in self.band:
            if not s.keyed(t):
                continue
            d = abs(hz - s.hz)
            att = 0.0 if d <= s.width_hz else min(90.0, (d - s.width_hz) / 1000.0 * 6.0)
            p += 10 ** ((s.dbm - att) / 10)
        return 10 * math.log10(p) + self.rng.gauss(0, self.noise_db)

    def reg_value(self, chip, a):
        c = self.chips[chip]
        if a == 0x00:
            return 0x4829
        if a == 0x67:
            raw = int(round((self.level_dbm(chip, c.hz) + 160) * 2))
            return max(0, min(0x1FF, raw))
        return c.regs.get(a, 0)

    def rf_read(self, chip, a):
        v = self.reg_value(chip, a) & 0xFFFF
        self._log(chip, a, v, "r")
        return v

    def rf_write(self, chip, a, v):
        c = self.chips[chip]
        c.regs[a] = v
        c.writes += 1
        if a == REG_STATE:
            c.states.append(v)
        self._log(chip, a, v, "w")

    def _log(self, chip, a, v, rw):
        self.rf_log.append((chip, a, v, rw))
        if len(self.rf_log) > 8000:
            del self.rf_log[:4000]

    # ---- fast paths: called at the first instruction of the driver's functions ----
    def _return(self, uc):
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)

    def _fast_rf_read(self, uc, address, size, ud):
        chip, a = uc.reg_read(UC_ARM_REG_R0) & 0xFF, uc.reg_read(UC_ARM_REG_R1) & 0x7F
        uc.reg_write(UC_ARM_REG_R0, self.rf_read(chip, a))
        self.fast_reads += 1
        self.advance(CPU_HZ // 1_000_000 * 60)       # ~60 us: 24 bits of the bus's ~1.3 us waits
        self._return(uc)

    def _fast_rf_write(self, uc, address, size, ud):
        chip = uc.reg_read(UC_ARM_REG_R0) & 0xFF
        a, v = uc.reg_read(UC_ARM_REG_R1) & 0x7F, uc.reg_read(UC_ARM_REG_R2) & 0xFFFF
        self.rf_write(chip, a, v)
        self.fast_writes += 1
        self.advance(CPU_HZ // 1_000_000 * 65)
        self._return(uc)

    def _fast_lcd_cmd(self, uc, address, size, ud):
        self._lcd_byte(uc.reg_read(UC_ARM_REG_R0) & 0xFF, 0)
        self._set_lcd_pins(0)
        self.advance(40)
        self._return(uc)

    def _fast_lcd_data(self, uc, address, size, ud):
        self._lcd_byte(uc.reg_read(UC_ARM_REG_R0) & 0xFF, 1)
        self._set_lcd_pins(1)
        self.advance(40)
        self._return(uc)

    def _set_lcd_pins(self, dc):
        """leave D/C and CS as the real function does (CS high; D/C as sent), without the edges"""
        self.odr[D] = (self.odr[D] | 0x0002 | 0x0001) & ~0x0008 | (dc << 3)

    def _fast_delay_us(self, uc, address, size, ud):
        """jm_glue.c's DWT busy wait"""
        self.advance(uc.reg_read(UC_ARM_REG_R0) * (CPU_HZ // 1_000_000))
        self._return(uc)

    # whole LCD calls at once: the same pixels the byte path would draw, without a hook a byte
    @staticmethod
    def _find_font(image):
        """lcd.c's 8x8 font (printable ASCII from 0x20, 8 bytes a glyph, MSB the left pixel), found
        by its '0' glyph"""
        zero = bytes([0x3C, 0x46, 0x4A, 0x52, 0x62, 0x42, 0x3C, 0x00])
        i = image.find(zero)
        return image[i - 16 * 8:i - 16 * 8 + 95 * 8] if i >= 128 else None

    def _stack_arg(self, uc, n=0):
        return struct.unpack("<I", uc.mem_read(uc.reg_read(UC_ARM_REG_SP) + 4 * n, 4))[0]

    def _rect(self, x, y, w, h, colour):
        hi, lo = colour >> 8 & 0xFF, colour & 0xFF
        for yy in range(max(0, y), min(H, y + h)):
            for xx in range(max(0, x), min(W, x + w)):
                o = 2 * (yy * W + xx)
                self.fb[o], self.fb[o + 1] = hi, lo

    def _fast_fill(self, uc, address, size, ud):
        x, y = uc.reg_read(UC_ARM_REG_R0) & 0xFFFF, uc.reg_read(UC_ARM_REG_R1) & 0xFFFF
        w, h = uc.reg_read(UC_ARM_REG_R2) & 0xFFFF, uc.reg_read(UC_ARM_REG_R3) & 0xFFFF
        self._rect(x, y, w, h, self._stack_arg(uc) & 0xFFFF)
        self.frames += 1
        self.advance(400 + 80 * w * h)               # ~80 cycles a pixel on the 8080 bus
        self._return(uc)

    def _text(self, uc, k):
        x, y = uc.reg_read(UC_ARM_REG_R0) & 0xFFFF, uc.reg_read(UC_ARM_REG_R1) & 0xFFFF
        p, fg, bg = uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3) & 0xFFFF, self._stack_arg(uc) & 0xFFFF
        s = bytes(uc.mem_read(p, 128)).split(b"\0")[0]
        if self.font is None:
            raise RuntimeError("no LCD font in the image")
        for ch in s:
            g = self.font[((ch if 0x20 <= ch <= 0x7E else 0x20) - 0x20) * 8:][:8]
            for r in range(8):
                for c in range(8):
                    self._rect(x + c * k, y + r * k, k, k, fg if g[r] << c & 0x80 else bg)
            x += 8 * k
            self.frames += 1
        self.advance(600 + 160 * 64 * k * k * len(s))
        self._return(uc)

    def _fast_text(self, uc, address, size, ud):
        self._text(uc, 1)

    def _fast_text2(self, uc, address, size, ud):
        self._text(uc, 2)

    def _fast_delay_ms(self, uc, address, size, ud):
        self.advance(uc.reg_read(UC_ARM_REG_R0) * (CPU_HZ // 1000))
        self._return(uc)

    # ---- the LCD ----
    def _lcd_byte(self, b, dc):
        d = self.lcd
        self.lcd_bytes += 1
        if not dc:
            d["cmd"], d["args"] = b, []
            if b == 0x2C:
                d["px"], d["hi"] = (d["x"][0], d["y"][0]), None
                self.frames += 1
            elif b == 0x29:
                d["on"] = True
            return
        a = d["args"]
        cmd = d["cmd"]
        if cmd == 0x2C:
            if d["hi"] is None:
                d["hi"] = b
                return
            x, y = d["px"]
            if 0 <= x < W and 0 <= y < H:
                o = 2 * (y * W + x)
                self.fb[o], self.fb[o + 1] = d["hi"], b
            d["hi"] = None
            x += 1
            if x > d["x"][1]:
                x, y = d["x"][0], y + 1
            d["px"] = (x, y)
            return
        a.append(b)
        if cmd == 0x36:
            d["madctl"] = b
        elif cmd == 0x2A and len(a) == 4:
            d["x"] = (a[0] << 8 | a[1], a[2] << 8 | a[3])
        elif cmd == 0x2B and len(a) == 4:
            d["y"] = (a[0] << 8 | a[1], a[2] << 8 | a[3])

    def pixel(self, x, y):
        o = 2 * (y * W + x)
        return self.fb[o] << 8 | self.fb[o + 1]

    def screen_rgb(self):
        """the screen as rows of (r, g, b)"""
        out = []
        for y in range(H):
            row = []
            for x in range(W):
                c = self.pixel(x, y)
                row.append(((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31))
            out.append(row)
        return out

    def screen_png(self, scale=2):
        import zlib
        rows = self.screen_rgb()
        raw = b""
        for r in rows:
            line = b"".join(bytes(px) * scale for px in r)
            raw += (b"\x00" + line) * scale

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))

    # ---- the SPI flash (SPI2, chip select PB12): blank ----
    def _flash_byte(self, b):
        if self.level(*FLASH_CS):
            return 0xFF
        q = self.flash_q
        if q is None:
            q = self.flash_q = []
        q.append(b)
        n, cmd = len(q) - 1, q[0]
        if cmd == 0x9F and 1 <= n <= 3:
            return (0xEF, 0x40, 0x16)[n - 1]          # a 4 MB W25Q32
        if cmd == 0x05:
            return 0x00                               # never busy
        return 0xFF

    # ---- peripherals ----
    def mmio(self, uc, access, addr, size, value, ud):
        put = lambda v: uc.mem_write(addr, struct.pack("<I", v & 0xFFFFFFFF))  # noqa: E731
        mem = lambda: struct.unpack("<I", uc.mem_read(addr, 4))[0]  # noqa: E731
        if GPIO <= addr < GPIO + 0x1400:
            port, off = (addr - GPIO) // 0x400, (addr - GPIO) % 0x400
            if access == UC_MEM_WRITE:
                if off == 0x00:
                    self.cfg[port] = (self.cfg[port] & ~0xFFFFFFFF) | (value & 0xFFFFFFFF)
                elif off == 0x04:
                    self.cfg[port] = (self.cfg[port] & 0xFFFFFFFF) | (value & 0xFFFFFFFF) << 32
                elif off == 0x0C:
                    self._odr_set(port, value)
                elif off == 0x10:
                    self._odr_set(port, (self.odr[port] | (value & 0xFFFF)) & ~(value >> 16))
                elif off == 0x14:
                    self._odr_set(port, self.odr[port] & ~value)
                return
            if off == 0x08:
                put(self.idr(port))
            elif off == 0x0C:
                put(self.odr[port])
            elif off == 0x00:
                put(self.cfg[port] & 0xFFFFFFFF)
            elif off == 0x04:
                put(self.cfg[port] >> 32)
            return
        if access == UC_MEM_WRITE:
            if addr == SPI2_DR:
                was_low = not self.level(*FLASH_CS)
                self._spi2_rx = self._flash_byte(value & 0xFF) if was_low else 0xFF
            elif 0x40022000 <= addr < 0x40022400:
                self.flash_ctrl_writes.append((addr, value))
            elif addr in (0x40013804, 0x40004404, 0x40004804, 0x40004C04, 0x40005004):
                self.uart.append(value & 0xFF)
            return
        if addr == CRM_CR:
            v = mem()
            put(v | ((v >> 16) & 1) << 17 | ((v >> 24) & 1) << 25 | (v & 1) << 1)
        elif addr == CRM_CFGR:
            v = mem()
            put((v & ~0xC) | (v & 3) << 2)
        elif addr == SPI2_SR:
            put(0x03)
        elif addr == SPI2_DR:
            put(getattr(self, "_spi2_rx", 0xFF))
        elif addr in (0x40013800, 0x40004400, 0x40004800, 0x40004C00, 0x40005000):
            put(0xC0)                                  # USART: sent, nothing received
        elif addr in (0x40012400, 0x40012800, 0x40013C00):
            put(0x02)                                  # ADC: conversion done
        elif addr in (0x40012408, 0x40012808, 0x40013C08):
            put(mem() & ~0xC)                          # calibration done
        elif addr in (0x4001244C, 0x4001284C, 0x40013C4C):
            put(2600)                                  # ~7.6 V on the battery divider
        elif addr == 0x4002200C:
            put(0)                                     # FLASH_STS: never busy

    def core(self, uc, access, addr, size, value, ud):
        if access == UC_MEM_WRITE:
            if addr == AIRCR and (value & 0xFFFF0004) == 0x05FA0004:
                self.reset_requested = True
                uc.emu_stop()
            return
        if addr == DWT_CYCCNT:
            self.advance(24)                           # a look at the clock and its loop's turn
            uc.mem_write(addr, struct.pack("<I", self.cycles & 0xFFFFFFFF))
        elif addr == 0xE000E010:                       # SysTick CTRL: COUNTFLAG set
            v = struct.unpack("<I", uc.mem_read(addr, 4))[0]
            uc.mem_write(addr, struct.pack("<I", v | 1 << 16))

    # ---- keys ----
    def press(self, key, down=True):
        if key in SIDE:
            if down:
                self.ext[SIDE[key]] = 0
            else:
                self.ext.pop(SIDE[key], None)
        else:
            (self.pressed.add if down else self.pressed.discard)(key)

    def tap(self, key, hold_s=0.15):
        """press now; step() lets go after hold_s of emulated time"""
        self.press(key, True)
        self.holding = (key, self.cycles + int(hold_s * CPU_HZ))

    # ---- running ----
    def step(self, instructions=2_000_000, chunk=20_000):
        """run about this many instructions, a chunk at a time (the clock moves a chunk's worth
        each, so systick_ms stays within ~0.2 ms of it)"""
        while instructions > 0 and not self.reset_requested:
            n = min(instructions, chunk)
            self._tick()
            self.mu.emu_start(self.pc, 0, count=n)
            self.pc = self.mu.reg_read(UC_ARM_REG_PC) | 1
            self.advance(n)
            instructions -= n
            if self.holding and self.cycles >= self.holding[1]:
                self.press(self.holding[0], False)
                self.holding = None

    def run_for(self, seconds, chunk=20_000):
        """run until the emulated clock has moved this far"""
        end = self.cycles + int(seconds * CPU_HZ)
        while self.cycles < end and not self.reset_requested:
            self.step(min(chunk, max(1, end - self.cycles)), chunk)
