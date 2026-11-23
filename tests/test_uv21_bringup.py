"""radios/uv21/bringup: the UV-21 Pro bring-up image, run in an emulated AT32F421 (Unicorn) with
models of the chips on the board's pins (radios/uv21/bringup/board.h, from the stock dump): an RF
chip on a 3-wire bus, an SPI flash, an ST7735-like LCD, the key matrix, the power switch sense
and the serial line, whose receive pin is also PTT.

What must hold: the RF chip only ever gets reads (no register write, so no transmit: its PA
enables are its own GPIOs), the SPI flash only wake (AB) and JEDEC ID, no flash register is touched, SWD stays
SWD, and only the board's listed pins are driven."""
import os
import struct
import subprocess
import sys

import pytest

unicorn = pytest.importorskip("unicorn")
from unicorn import UC_ARCH_ARM, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc  # noqa: E402,E501
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_SP  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import uv21_kdh as kdh  # noqa: E402

UV21 = os.path.join(ROOT, "radios", "uv21")
A, B, C, F = 0, 1, 2, 5
GPIO = 0x48000000
SYST_CVR = 0xE000E018
AIRCR = 0xE000ED0C
RF_ID = 0x4819
NOR_JEDEC = 0xEF4015
LCD_ID = 0x7C89F0
# board.h
RF_SCN, RF_SCK, RF_SDA = (B, 3), (B, 5), (B, 6)
NOR_CS, NOR_SCK, NOR_MISO, NOR_MOSI = (A, 2), (A, 5), (A, 6), (A, 7)
LCD_CS, LCD_SCK, LCD_DC, LCD_SDA, LCD_RST = (B, 12), (B, 13), (C, 15), (B, 15), (B, 8)
KEY_COLS = ((B, 0), (B, 4), (B, 9), (B, 10), (B, 11))
KEY_ROWS = ((C, 13), (C, 14), (F, 6), (B, 14))
LATCH, SENSE = (A, 8), (A, 15)
BL = (F, 7)                                # the backlight, high = on
WATCH = (RF_SCN, RF_SCK, RF_SDA, NOR_CS, NOR_SCK, NOR_MISO, NOR_MOSI, LCD_CS, LCD_SCK, LCD_DC, LCD_SDA)
WMASK = {port: sum(1 << b for p, b in WATCH if p == port) for port in (A, B, C, F)}
CYCLES_PER_READ = 400                     # SysTick counts this much between two reads (50 us)


def build(target):
    subprocess.run(["make", "-s", "-C", UV21, target], check=True)
    return open(os.path.join(UV21, target), "rb").read()


@pytest.fixture(scope="module")
def image():
    return build("build/bringup.bin")


class Pins:
    """GPIO ports A, B, C, F: mode, pull and output registers as the code writes them"""

    def __init__(self):
        self.cfgr = {p: 0 for p in (A, B, C, F)}
        self.pull = {p: 0 for p in (A, B, C, F)}
        self.odt = {p: 0 for p in (A, B, C, F)}
        self.cfgr[A] = 0x28000000             # PA13/PA14 SWD after reset
        self.pull[A] = 0x24000000

    def mode(self, port, bit):
        return (self.cfgr[port] >> (bit * 2)) & 3

    def pullup(self, port, bit):
        return (self.pull[port] >> (bit * 2)) & 3 == 1

    def out(self, port, bit):
        return (self.odt[port] >> bit) & 1


class Radio:
    """an AT32F421 running one image at 0x08001000, with the board's chips on its pins"""

    def __init__(self, image):
        self.pins = Pins()
        self.uart, self.rx = bytearray(), []
        self.writes, self.flash_writes, self.reset_requested = [], [], False
        self.ext = {}                          # (port, bit) -> level a device or a key drives
        self.pressed = set()                   # (col, row) matrix keys held
        self.cvr = 0
        self.stop_text, self.start, self.redo = None, 0, False
        self.rf_txn, self.rf_log, self.rf_bits = None, [], []
        self.nor_txn, self.nor_cmds = None, []
        self.lcd = {"cs": 1, "byte": 0, "n": 0, "cmd": None, "args": [], "cmds": [], "read": [],
                    "x": (0, 127), "y": (0, 159), "px": None, "hi": None}
        self.fb = [[None] * 128 for _ in range(160)]
        self.prev = self.levels()
        self.adc_osq3 = 0
        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        mu.mem_map(0x08000000, 0x10000)
        mu.mem_write(0x08001000, image)
        sysm = bytearray(0x2000)
        sysm[0x17E0:0x17E2] = struct.pack("<H", 64)
        mu.mem_map(0x1FFFE000, 0x2000)
        mu.mem_write(0x1FFFE000, bytes(sysm))
        mu.mem_map(0x20000000, 0x4000)
        mu.mem_map(0x40000000, 0x30000)
        mu.mem_map(0x48000000, 0x2000)
        mu.mem_write(GPIO, struct.pack("<IIII", 0x28000000, 0, 0, 0x24000000))   # PA13/PA14: SWD
        mu.mem_map(0xE0000000, 0x100000)
        mu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.mmio, begin=0x40000000, end=0x4FFFFFFF)
        mu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.core, begin=0xE000E000, end=0xE000EFFF)
        sp, pc = struct.unpack_from("<II", image, 0)
        mu.reg_write(UC_ARM_REG_SP, sp)
        self.pc = pc | 1

    # ---- the board: who drives each line ----

    def level(self, port, bit):
        p = self.pins
        if p.mode(port, bit) == 1:
            return p.out(port, bit)
        if (port, bit) in self.ext:
            return self.ext[(port, bit)]
        if (port, bit) in KEY_ROWS:
            row = KEY_ROWS.index((port, bit))
            for c, r in self.pressed:
                if r == row and p.mode(*KEY_COLS[c]) == 1 and not p.out(*KEY_COLS[c]):
                    return 0
        return 1 if p.pullup(port, bit) else 0

    def levels(self):                      # the bus lines the chip models watch
        return {k: self.level(*k) for k in WATCH}

    def idt(self, port):
        return sum(self.level(port, b) << b for b in range(16))

    def edges(self):
        now = self.levels()
        rise = {k for k in now if now[k] and not self.prev[k]}
        fall = {k for k in now if not now[k] and self.prev[k]}
        self.prev = now
        self.rf(now, rise, fall)
        self.nor(now, rise, fall)
        self.lcd_bus(now, rise, fall)

    def moved(self, port, bits):           # edges() for output writes, without re-reading every line
        rise, fall = set(), set()
        for b in range(16):
            if bits >> b & 1 and self.pins.mode(port, b) == 1:
                v = self.pins.out(port, b)
                if v != self.prev[(port, b)]:
                    self.prev[(port, b)] = v
                    (rise if v else fall).add((port, b))
        if rise or fall:
            self.rf(self.prev, rise, fall)
            self.nor(self.prev, rise, fall)
            self.lcd_bus(self.prev, rise, fall)

    def rf(self, lv, rise, fall):
        if RF_SCN in fall:
            self.rf_txn = {"out": [], "addr": None, "value": None}
        if RF_SCN in rise and self.rf_txn is not None:
            self.rf_log.append(self.rf_txn)
            self.rf_txn = None
        t = self.rf_txn
        if t is None or RF_SCK not in rise:
            return
        if self.pins.mode(*RF_SDA) == 1:
            t["out"].append(lv[RF_SDA])
            if len(t["out"]) == 8:
                t["addr"] = int("".join(map(str, t["out"])), 2)
                v = RF_ID if (t["addr"] & 0x7F) == 0 else 0x1000 + (t["addr"] & 0x7F)
                t["value"] = v
                self.rf_bits = [(v >> (15 - i)) & 1 for i in range(16)]
        else:
            self.ext[RF_SDA] = self.rf_bits.pop(0) if self.rf_bits else 1

    def nor(self, lv, rise, fall):
        if NOR_CS in fall:
            self.nor_txn = {"bits": [], "resp": []}
        if NOR_CS in rise and self.nor_txn is not None:
            self.nor_txn = None
        t = self.nor_txn
        if t is None or NOR_SCK not in rise:
            return
        t["bits"].append(lv[NOR_MOSI])
        if len(t["bits"]) > 8:                     # after the command: the answer, bit by bit
            self.ext[NOR_MISO] = t["resp"].pop(0) if t["resp"] else 1
        elif len(t["bits"]) == 8:
            cmd = int("".join(map(str, t["bits"])), 2)
            self.nor_cmds.append(cmd)
            if cmd == 0x9F:
                t["resp"] = [(NOR_JEDEC >> (23 - i)) & 1 for i in range(24)]

    def lcd_bus(self, lv, rise, fall):
        d = self.lcd
        if LCD_CS in fall:
            d["n"], d["byte"] = 0, 0
        if LCD_SCK not in rise or lv[LCD_CS]:
            return
        if self.pins.mode(*LCD_SDA) != 1:          # a read: the controller drives SDA
            self.ext[LCD_SDA] = d["read"].pop(0) if d["read"] else 0
            return
        d["byte"] = (d["byte"] << 1) | lv[LCD_SDA]
        d["n"] += 1
        if d["n"] < 8:
            return
        b, d["n"], d["byte"] = d["byte"], 0, 0
        if not lv[LCD_DC]:                         # a command
            d["cmd"], d["args"] = b, []
            d["cmds"].append(b)
            if b == 0x04:
                d["read"] = [0] + [(LCD_ID >> (23 - i)) & 1 for i in range(24)] + [0] * 7
            elif b == 0x09:
                d["read"] = [0] + [(0x00610000 >> (31 - i)) & 1 for i in range(32)]
            elif b == 0x2C:
                d["px"], d["hi"] = (d["x"][0], d["y"][0]), None
            return
        d["args"].append(b)
        if d["cmd"] == 0x2A and len(d["args"]) == 4:
            a = d["args"]
            d["x"] = (a[0] << 8 | a[1], a[2] << 8 | a[3])
        elif d["cmd"] == 0x2B and len(d["args"]) == 4:
            a = d["args"]
            d["y"] = (a[0] << 8 | a[1], a[2] << 8 | a[3])
        elif d["cmd"] == 0x2C:
            if d["hi"] is None:
                d["hi"] = b
                return
            colour, d["hi"] = d["hi"] << 8 | b, None
            x, y = d["px"]
            if 0 <= x < 128 and 0 <= y < 160:
                self.fb[y][x] = colour
            x += 1
            if x > d["x"][1]:
                x, y = d["x"][0], y + 1
            d["px"] = (x, y)

    # ---- memory-mapped registers ----

    def mmio(self, uc, access, addr, size, value, ud):
        put = lambda v: uc.mem_write(addr, struct.pack("<I", v & 0xFFFFFFFF))  # noqa: E731
        if access == UC_MEM_WRITE:
            self.writes.append((addr, value))
            if 0x40022000 <= addr < 0x40022400:
                self.flash_writes.append((addr, value))
            if GPIO <= addr < GPIO + 0x1800:
                port, off = (addr - GPIO) // 0x400, (addr - GPIO) % 0x400
                p = self.pins
                old = p.odt[port]
                if off == 0x00:
                    p.cfgr[port] = value
                elif off == 0x0C:
                    p.pull[port] = value
                elif off == 0x14:
                    p.odt[port] = value & 0xFFFF
                elif off == 0x18:
                    p.odt[port] = (p.odt[port] | (value & 0xFFFF)) & ~(value >> 16)
                elif off == 0x28:
                    p.odt[port] &= ~value & 0xFFFF
                if off in (0x00, 0x0C):
                    self.edges()
                elif (old ^ p.odt[port]) & WMASK.get(port, 0):            # a watched line moved
                    self.moved(port, (old ^ p.odt[port]) & WMASK[port])
            elif addr == 0x40013804:
                if self.redo:                  # the store we stopped on, run again on resume
                    self.redo = False
                    return
                self.uart.append(value & 0xFF)
                if self.stop_text and self.stop_text in self.uart[max(self.start, len(self.uart) - 200):]:
                    self.redo = True
                    uc.emu_stop()
            elif addr == 0x40012434:
                self.adc_osq3 = value & 0x1F
            return
        if GPIO <= addr < GPIO + 0x1800 and (addr - GPIO) % 0x400 == 0x10:
            put(self.idt((addr - GPIO) // 0x400))
        elif GPIO <= addr < GPIO + 0x1800 and (addr - GPIO) % 0x400 == 0x14:
            put(self.pins.odt[(addr - GPIO) // 0x400])
        elif addr == 0x40013800:
            st = 0xC0
            if self.rx:
                st |= 0x02 if self.rx[0] is None else 0x20
            put(st)
        elif addr == 0x40013804 and self.rx:
            b = self.rx.pop(0)
            put(0 if b is None else b)
        elif addr == 0x40021000:
            v = struct.unpack("<I", uc.mem_read(addr, 4))[0]
            put((v & ~(1 << 17)) | ((v >> 16) & 1) << 17)        # the crystal starts at once
        elif addr == 0x40021004:
            put(0)
        elif addr == 0x40012400:
            put(2)                                                # conversion done
        elif addr == 0x40012408:
            v = struct.unpack("<I", uc.mem_read(addr, 4))[0]
            put(v & ~0xC)                                         # calibration done
        elif addr == 0x4001244C:
            put(1489 if self.adc_osq3 == 17 else 1000 + self.adc_osq3)

    def core(self, uc, access, addr, size, value, ud):
        if access == UC_MEM_WRITE:
            if addr == AIRCR and value == 0x05FA0004:
                self.reset_requested = True
                uc.emu_stop()
            return
        if addr == SYST_CVR:
            self.cvr = (self.cvr - CYCLES_PER_READ) & 0xFFFFFF
            uc.mem_write(addr, struct.pack("<I", self.cvr))

    # ---- running ----

    def run(self, until=None, limit=60_000_000):
        """run until the serial output shows `until` (or the instruction limit); returns the new text"""
        start = self.start = len(self.uart)
        self.stop_text = until.encode() if until else None
        self.mu.emu_start(self.pc, 0, count=limit)
        self.pc = self.mu.reg_read(UC_ARM_REG_PC) | 1
        self.stop_text = None
        return self.uart[start:].decode("latin-1")

    def lines(self):
        return self.uart.decode("latin-1").split("\r\n")


@pytest.fixture(scope="module")
def radio(image):
    r = Radio(image)
    r.run(until="O off, R reset\r\n")
    return r


def driven(r):
    """(port, bit, mode) of every pin the image left as output or alternate function"""
    return {(p, b, r.pins.mode(p, b)) for p in (A, B, C, F) for b in range(16)
            if r.pins.mode(p, b) in (1, 2)} - {(A, 13, 2), (A, 14, 2)}


def test_image_fits_the_app_area_and_the_flash_tool_takes_it(image):
    assert len(image) <= 0xF000
    sp, pc = struct.unpack_from("<II", image)
    assert sp == 0x20004000 and 0x08001000 < pc < 0x08001000 + len(image) and pc & 1
    assert sp & 0x2FFE0000 == 0x20000000                          # the bootloader's whole app check
    kdh.packages(image)
    words = {struct.unpack_from("<I", image, o)[0] for o in range(0, len(image) - 3, 2)}
    assert not words & {0x45670123, 0xCDEF89AB}                   # FLASH_UNLOCK keys


def test_boots_and_reads_every_chip_id(radio):
    text = "\r\n".join(radio.lines())
    assert radio.lines()[0] == "blowfang uv21 bringup 2"
    assert "board=uv21" in text
    assert "clock sclk=hick_8mhz hext_ok=1" in text
    assert "rf id=4819" in text
    assert "nor jedec=ef4015" in text
    assert f"lcd rddid_raw={LCD_ID << 7:08x}" in text
    assert "adc vdd_mv=3300" in text and "ch0=1000 ch1=1001" in text
    assert "power sense=0 auto_off=1" in text


def test_drives_only_the_board_pins(radio):
    radio.run(limit=20_000_000)                                    # past the first screen: backlight on
    outs = {(p, b) for p, b, m in driven(radio) if m == 1}
    afs = {(p, b) for p, b, m in driven(radio) if m == 2}
    assert afs == {(A, 9), (A, 10)}                                # USART1
    assert outs == {LATCH, BL, RF_SCN, RF_SCK, RF_SDA, NOR_CS, NOR_SCK, NOR_MOSI,
                         LCD_CS, LCD_SCK, LCD_DC, LCD_SDA, LCD_RST} | set(KEY_COLS)
    assert radio.pins.out(*LATCH) == 1
    for p, b in ((B, 2), (B, 7), (A, 11), (A, 4)):                 # amp, flashlight, audio PWM, I2C SCL
        assert radio.pins.mode(p, b) == 0


def test_rf_chip_only_ever_gets_reads(radio):
    radio.rx += list(b"r")
    radio.run(until="rf regs 70:")
    radio.run(until="\r\n")
    assert radio.rf_log, "no RF transactions"
    for t in radio.rf_log:
        assert len(t["out"]) == 8 and t["addr"] & 0x80, f"not a read: {t}"
    regs = [ln.split() for ln in radio.lines() if ln.startswith("rf regs ")]
    assert len(regs) == 8
    assert regs[0][3:5] == ["4819", "1001"] and regs[7][-1] == "107f"


def test_spi_flash_gets_only_jedec_id(radio):
    assert radio.nor_cmds[:2] == [0xAB, 0x9F] and set(radio.nor_cmds) == {0xAB, 0x9F}   # wake, JEDEC ID


def test_no_flash_register_and_swd_kept(radio):
    assert not radio.flash_writes
    assert radio.pins.mode(A, 13) == 2 and radio.pins.mode(A, 14) == 2


def test_screen_test_pattern(radio):
    fb = radio.fb
    assert fb[0][0] == 0xFFFF and fb[159][127] == 0xFFFF and fb[80][0] == 0xFFFF   # the frame
    assert fb[3][3] == 0xF800                                                      # top left red
    assert fb[45][5] == 0xF800 and fb[45][35] == 0x07E0 and fb[45][65] == 0x001F    # R G B bars
    cmds = radio.lcd["cmds"]
    assert cmds.index(0x11) < cmds.index(0x3A) < cmds.index(0x29) < cmds.index(0x2C)


def test_keys_ptt_and_pin_census(radio):
    radio.pressed.add((1, 2))                                      # column PB4, row PF6: UP
    assert "keys matrix=00000040 side=0 direct=00 name=UP" in radio.run(until="name=UP\r\n")
    radio.pressed.clear()
    assert "keys matrix=00000000 side=0" in radio.run(until="matrix=00000000 side=0 direct=00\r\n")
    radio.pressed.add((4, 3))                                      # the fifth column (PB11): bit 19
    assert "keys matrix=00080000 side=0" in radio.run(until="matrix=00080000 side=0 direct=00\r\n")
    radio.pressed.clear()
    radio.run(until="matrix=00000000 side=0 direct=00\r\n")
    radio.ext[(C, 14)] = 0                                         # the top side key grounds row PC14
    out = radio.run(until="name=SIDE TOP\r\n")
    assert "keys matrix=00000000 side=2 direct=00 name=SIDE TOP" in out
    del radio.ext[(C, 14)]
    radio.run(until="matrix=00000000 side=0 direct=00\r\n")
    radio.ext[(A, 12)] = 1                                         # a pin nobody mapped
    out = radio.run(until="pins a=1040 b=4000 c=6000 f=0040\r\n")   # PA6 MISO and the rows pulled up
    assert "pins a=1040 b=4000 c=6000 f=0040" in out
    radio.rx.append(None)                                          # PTT: a break on the RX pin
    assert "ptt break n=1" in radio.run(until="ptt break n=1\r\n")


def test_command_42_from_the_flash_tool_resets_into_the_bootloader(image):
    r = Radio(image)
    r.run(until="O off, R reset\r\n")
    r.rx += list(kdh.kdh_frame(kdh.CMD_BOOT, 0))
    out = r.run(until="never")
    assert "bootloader: command 42 seen, resetting" in out and r.reset_requested


def test_power_switch_off_drops_the_latch(image):
    r = Radio(image)
    r.run(until="O off, R reset\r\n")
    r.ext[SENSE] = 1                                               # the switch says off
    assert "power off" in r.run(until="power off\r\n")
    r.run(limit=20_000)
    assert r.pins.out(*LATCH) == 0


def test_a_power_sense_that_reads_off_at_boot_is_ignored(image):
    r = Radio(image)
    r.ext[SENSE] = 1                                               # wrong polarity in board.h
    out = r.run(until="O off, R reset\r\n")
    assert "power sense=1 auto_off=0" in out
    r.run(limit=3_000_000)
    assert r.pins.out(*LATCH) == 1 and "power off" not in r.uart.decode("latin-1")


def test_backlight_comes_on_after_the_first_screen(image):
    r = Radio(image)
    r.run(until="O off, R reset\r\n")
    assert r.pins.out(*BL) == 0 or r.pins.mode(*BL) != 1          # dark while the screen is set up
    r.run(limit=20_000_000)
    assert r.pins.mode(*BL) == 1 and r.pins.out(*BL) == 1
    assert r.pins.mode(A, 3) == 0 and r.pins.mode(B, 1) == 0      # the old candidates stay undriven
