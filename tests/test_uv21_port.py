"""radios/uv21/fw: the UV-21 Pro port of the UV-5RH open firmware, run in the emulated AT32F421 of
test_uv21_bringup.py, with its SPI peripherals, PLL, timer and SysTick modelled as well.

What must hold before an image goes on the radio with nobody at the bench: it boots on the
internal clock (the crystal doesn't start) and says so on the serial line; it drives only the
UV-21's pins (the power sense stays an input; PA13/PA14, the SWD pins, are the receive band
switch since port 12); the RF chip never gets a transmit state, a PA bias or a TX switch, not
even with PTT held; the bootloader's command 42 frame resets it, so the next image can go on
with no hands; the SPI flash is never written; and the keys come out as the right key ids."""
import os
import struct
import subprocess
import sys

import pytest

pytest.importorskip("unicorn")
from unicorn import UC_MEM_WRITE  # noqa: E402
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
import test_uv21_bringup as bu  # noqa: E402

A, B, C, F = bu.A, bu.B, bu.C, bu.F
UV21 = bu.UV21
SPI1, SPI2, TMR14 = 0x40013000, 0x40003800, 0x40002000
SYST_CSR = 0xE000E010
REG_STATE, REG_GPIO, REG_PA = 0x30, 0x33, 0x36
PA_OFF = 0x0022
JEDEC = bytes([0xC4, 0x60, 0x15])
KDH_42 = bytes([0xAA, 0x42, 0x00, 0x00, 0x00, 0x83, 0xF4, 0xEF])
RETURN = 0x0800FFF0                         # an address no code uses: where a called ISR returns
# the UV-21's pins (radios/uv21/fw/src/board)
OUTPUTS = {bu.LATCH, bu.RF_SCN, bu.RF_SCK, bu.RF_SDA, bu.NOR_CS, bu.LCD_CS, bu.LCD_RST, bu.LCD_DC,
           bu.BL, (B, 2), (B, 7), (A, 3), (A, 4), (A, 13), (A, 14)} | set(bu.KEY_COLS)  # A3: stock FM boot pin
ALTERNATE = {(A, 9), (A, 10), (A, 5), (A, 6), (A, 7), (B, 13), (B, 15)}
# KeyId numbers (src/device/keypad.rs)
KEY_ID = {"1": 1, "0": 0, "*": 10, "#": 11, "MENU": 12, "EXIT": 13, "UP": 14, "DOWN": 15, "VM": 16,
          "AB": 17, "BAND": 18, "SIDE1": 19, "SIDE2": 20}


@pytest.fixture(scope="module")
def built():
    subprocess.run(["make", "-s", "-C", UV21, "port"], check=True)
    image = open(os.path.join(UV21, "build", "port.bin"), "rb").read()
    nm = subprocess.run(["llvm-nm", os.path.join(UV21, "build", "port.elf")], check=True,
                        capture_output=True, text=True).stdout
    syms = {ln.split()[2]: int(ln.split()[0], 16) for ln in nm.splitlines() if len(ln.split()) == 3}
    return image, syms


class Port(bu.Radio):
    def __init__(self, image, syms):
        super().__init__(image)
        self.syms = syms
        self.nor_mem = bytearray(b"\xff" * 0x200000)
        self.tmr_step = 1
        self.nor_q, self.nor_ops = None, []
        self.spi_dt = {SPI1: 0xFF, SPI2: 0xFF}
        self.tmr = 0
        self.lfb = [[None] * 160 for _ in range(160)]       # the screen as drawn (landscape or portrait)
        self.lcd.update({"x": (0, 159), "y": (0, 127), "madctl": None})

    # the RF chip: register 0 reads 0x4829 here
    def rf(self, lv, rise, fall):
        if bu.RF_SCN in fall:
            self.rf_txn = {"out": [], "addr": None}
        if bu.RF_SCN in rise and self.rf_txn is not None:
            self.rf_log.append(self.rf_txn)
            self.rf_txn = None
        t = self.rf_txn
        if t is None or bu.RF_SCK not in rise:
            return
        if self.pins.mode(*bu.RF_SDA) == 1:
            t["out"].append(lv[bu.RF_SDA])
            if len(t["out"]) == 8:
                t["addr"] = int("".join(map(str, t["out"])), 2)
                v = 0x4829 if (t["addr"] & 0x7F) == 0 else 0x0000
                self.rf_bits = [(v >> (15 - i)) & 1 for i in range(16)]
        else:
            self.ext[bu.RF_SDA] = self.rf_bits.pop(0) if self.rf_bits else 1

    def rf_writes(self):
        out = []
        for t in self.rf_log:
            if t["addr"] is not None and not t["addr"] & 0x80 and len(t["out"]) >= 24:
                out.append((t["addr"], int("".join(map(str, t["out"][8:24])), 2)))
        return out

    # the SPI flash on SPI1, chip select PA2: what each byte clocked in answers
    def nor(self, lv, rise, fall):
        if bu.NOR_CS in fall:
            self.nor_q = []
        if bu.NOR_CS in rise and self.nor_q:
            q, self.nor_q = self.nor_q, None
            cmd, addr = q[0], (q[1] << 16 | q[2] << 8 | q[3]) if len(q) >= 4 else None
            self.nor_ops.append((cmd, addr))
            if cmd == 0x20:
                a = addr & ~0xFFF
                self.nor_mem[a:a + 0x1000] = b"\xff" * 0x1000
            elif cmd == 0x02:
                for i, b in enumerate(q[4:]):
                    self.nor_mem[addr + i] &= b
            elif cmd == 0xC7:
                self.nor_mem[:] = b"\xff" * len(self.nor_mem)

    def nor_byte(self, b):
        if self.nor_q is None:
            return 0xFF
        q = self.nor_q
        q.append(b)
        n, cmd = len(q) - 1, q[0]
        if cmd == 0x9F and 1 <= n <= 3:
            return JEDEC[n - 1]
        if cmd == 0x05:
            return 0x00
        if cmd == 0x03 and n >= 4:
            return self.nor_mem[(q[1] << 16 | q[2] << 8 | q[3]) + n - 4]
        return 0xFF

    # the LCD on SPI2: commands with D/C low, data with it high
    def lcd_byte(self, b, dc):
        d = self.lcd
        if not dc:
            d["cmd"], d["args"] = b, []
            d["cmds"].append(b)
            if b == 0x2C:
                d["px"], d["hi"] = (d["x"][0], d["y"][0]), None
            return
        d["args"].append(b)
        a = d["args"]
        if d["cmd"] == 0x36:
            d["madctl"] = b
        elif d["cmd"] == 0x2A and len(a) == 4:
            d["x"] = (a[0] << 8 | a[1], a[2] << 8 | a[3])
        elif d["cmd"] == 0x2B and len(a) == 4:
            d["y"] = (a[0] << 8 | a[1], a[2] << 8 | a[3])
        elif d["cmd"] == 0x2C:
            if d["hi"] is None:
                d["hi"] = b
                return
            colour, d["hi"] = d["hi"] << 8 | b, None
            x, y = d["px"]
            if 0 <= x < 160 and 0 <= y < 160:
                self.lfb[y][x] = colour
            x += 1
            if x > d["x"][1]:
                x, y = d["x"][0], y + 1
            d["px"] = (x, y)

    # the same controller read by bit-banging (radios/uv21/mod/scrdump.c): bring-up's bus model,
    # plus RAMRD (0x2E) answering from the screen as SPI2 drew it: a dummy clock and a dummy byte
    # (as the radio does, measured 2026-11-28), then each pixel of the CASET/RASET window as three
    # bytes, its 6-bit R, G, B in the top bits (5-bit R and B widened by repeating their top bit)
    def lcd_bus(self, lv, rise, fall):
        d = self.lcd
        n = len(d["cmds"])
        super().lcd_bus(lv, rise, fall)
        if len(d["cmds"]) > n and d["cmds"][-1] == 0x2E:
            bits = [0] * 9
            for y in range(d["y"][0], d["y"][1] + 1):
                for x in range(d["x"][0], d["x"][1] + 1):
                    c = self.lfb[y][x] if 0 <= y < 160 and 0 <= x < 160 and self.lfb[y][x] is not None else 0
                    r, g, b = c >> 11 & 31, c >> 5 & 63, c & 31
                    for v in ((r << 1 | r >> 4) << 2, g << 2, (b << 1 | b >> 4) << 2):
                        bits += [(v >> (7 - i)) & 1 for i in range(8)]
            d["read"] = bits

    def mmio(self, uc, access, addr, size, value, ud):
        put = lambda v: uc.mem_write(addr, struct.pack("<I", v & 0xFFFFFFFF))  # noqa: E731
        mem = lambda: struct.unpack("<I", uc.mem_read(addr, 4))[0]  # noqa: E731
        if access == UC_MEM_WRITE:
            if addr == SPI1 + 0x0C:
                self.spi_dt[SPI1] = self.nor_byte(value & 0xFF)
            elif addr == SPI2 + 0x0C:
                if not self.level(*bu.LCD_CS):
                    self.lcd_byte(value & 0xFF, self.level(*bu.LCD_DC))
            return super().mmio(uc, access, addr, size, value, ud)
        if addr == 0x40021000:                      # CRM_CTRL: HICK and the PLL lock at once
            v = mem()
            put(v | (v & 1) << 1 | (v >> 24 & 1) << 25)
        elif addr == 0x40021004:                    # CRM_CFG: the clock switch follows the select
            v = mem()
            put((v & ~0xC) | (v & 3) << 2)
        elif addr in (SPI1 + 0x08, SPI2 + 0x08):    # STS: sent, received, not busy
            put(0x03)
        elif addr in (SPI1 + 0x0C, SPI2 + 0x0C):
            put(self.spi_dt[addr - 0x0C])
        elif addr == TMR14 + 0x24:                  # CVAL: 100 us ticks, tmr_step a read
            self.tmr = (self.tmr + self.tmr_step) & 0xFFFF
            put(self.tmr)
        else:
            super().mmio(uc, access, addr, size, value, ud)

    def core(self, uc, access, addr, size, value, ud):
        if access != UC_MEM_WRITE and addr == SYST_CSR:         # every SysTick wait ends at once
            v = struct.unpack("<I", uc.mem_read(addr, 4))[0]
            uc.mem_write(addr, struct.pack("<I", v | 1 << 16))
            return
        super().core(uc, access, addr, size, value, ud)

    def serial_in(self, data):
        """bytes arriving on the serial line, each through the USART1 interrupt handler"""
        for b in data:
            self.rx = [b]
            ctx = self.mu.context_save()
            self.mu.reg_write(UC_ARM_REG_LR, RETURN | 1)
            self.mu.emu_start(self.syms["USART1"] | 1, RETURN, count=200_000)
            if self.reset_requested:
                return
            self.mu.context_restore(ctx)
            self.pc = self.mu.reg_read(UC_ARM_REG_PC) | 1


@pytest.fixture(scope="module")
def port(built):
    r = Port(*built)
    r.run(until="boot done", limit=300_000_000)
    r.run(until="\r\n", limit=1_000_000)
    r.run(limit=5_000_000)                          # into the main loop
    return r


def test_image_fits_and_passes_the_bootloader_check(built):
    image, syms = built
    assert len(image) <= 0xF000
    sp, pc = struct.unpack_from("<II", image)
    assert sp & 0x2FFE0000 == 0x20000000 and 0x08001000 < pc < 0x08001000 + len(image)
    bu.kdh.packages(image)
    assert "USART1" in syms


def test_boots_on_the_internal_clock_and_says_so(port):
    text = port.uart.decode("latin-1")
    assert "blowfang uv21 port 20 build=" in text
    assert "sclk_mhz=78 power_sense_ok=1 rx_only=1 nor_read_only=1" in text   # 120 MHz
    assert "rf id=4829" in text
    assert "calibration applied=0" in text and "boot done" in text
    crm_cfg = [v for a, v in port.writes if a == 0x40021004]
    assert crm_cfg and not any(v & 1 << 16 for v in crm_cfg)           # the PLL never asks for HEXT
    assert all(not (v & 1 << 16) for a, v in port.writes if a == 0x40021000)   # HEXT never enabled


def test_the_serial_port_runs_at_115200_on_the_120_mhz_clock(port):
    # 4 MHz (HICK/2) x 30 (PLLMULT = 0b01_1101 = 29) = 120 MHz; USART1 divisor 1042 -> 115,163 baud
    cfg = [v for a, v in port.writes if a == 0x40021004][-1]
    assert cfg & 0x3 == 2 and (cfg >> 11) & 7 < 4                       # PLL; APB2 (USART1) undivided
    pllmult = (((cfg >> 29) & 0x3) << 4) | ((cfg >> 18) & 0xF)
    assert pllmult == 0b01_1101, f"expected CRM_PLL_MULT_30 (29), got {pllmult}"
    div = [v for a, v in port.writes if a == 0x40013808][-1] & 0xFFFF   # USART1 BAUDR
    assert abs(120_000_000 / div - 115_200) < 115_200 * 0.01, (div, 120_000_000 / div)
    # SPI2 (ST7735S LCD) must stay at 15 MHz (PCLK / 8 -> mdiv2_0 = 2) at 120 MHz
    spi2_ctrl1 = [v for a, v in port.writes if a == 0x40003800][-1]
    assert (spi2_ctrl1 >> 3) & 0x7 == 2, f"expected SPI2 MDIV=2 (/8 = 15 MHz), got {(spi2_ctrl1 >> 3) & 0x7}"


def test_drives_only_the_uv21_pins(port):
    modes = {(p, b): port.pins.mode(p, b) for p in (A, B, C, F) for b in range(16)}
    outs = {k for k, m in modes.items() if m == 1}
    afs = {k for k, m in modes.items() if m == 2}
    assert outs <= OUTPUTS, outs - OUTPUTS
    assert afs == ALTERNATE
    assert modes[(A, 13)] == 1 and modes[(A, 14)] == 1                 # the receive band switch
    assert modes[bu.SENSE] == 0 and port.pins.out(*bu.LATCH) == 1
    assert port.pins.out(*bu.BL) == 1                                   # backlight on
    assert port.lcd["madctl"] == 0x00 and 0x29 in port.lcd["cmds"]


def test_the_rf_chip_never_gets_a_transmit_setting(port):
    port.ext[(A, 10)] = 0                                               # hold PTT
    port.run(limit=20_000_000)
    port.ext.pop((A, 10))
    port.run(limit=5_000_000)
    writes = port.rf_writes()
    assert writes, "no RF register writes seen at all"
    for reg, v in writes:
        if reg == REG_STATE:
            assert not (v & 0x00F0 and v & 0x000E), hex(v)              # VCO on with a TX enable
        elif reg == REG_PA:
            assert v == PA_OFF, hex(v)
        elif reg == REG_GPIO:
            assert not v & 0x001C, hex(v)                               # GPIO2-4: TX switches
    assert any(reg == REG_STATE and v & 0x00F0 and v & 1 for reg, v in writes)   # it does receive


def test_keys_come_out_as_key_ids(port):
    for press, name in (((4, 2), "MENU"), ((0, 0), "VM"), ((2, 0), "*"), ((1, 0), "1")):
        port.pressed.add(press)
        out = port.run(until="key id=%02x\r\n" % KEY_ID[name], limit=30_000_000)
        assert "key id=%02x" % KEY_ID[name] in out, (name, out)
        port.pressed.clear()
        port.run(limit=3_000_000)
    for row, name in (((C, 14), "SIDE1"), ((B, 14), "SIDE2"), ((F, 6), "BAND")):
        port.ext[row] = 0                                               # a side key grounds its row
        out = port.run(until="key id=%02x\r\n" % KEY_ID[name], limit=30_000_000)
        assert "key id=%02x" % KEY_ID[name] in out, (name, out)
        port.ext.pop(row)
        port.run(limit=3_000_000)


def test_the_spi_flash_is_never_written(port):
    # the SPI flash still holds the stock firmware's data (the 5RH code saved its own VFO over the
    # stock one at 0x8000 on UP or DOWN): port images must send no write enable, program or erase
    for press in ((1, 2), (3, 2)):                                      # UP, DOWN
        port.pressed.add(press)
        port.run(limit=10_000_000)
        port.pressed.clear()
        port.run(limit=10_000_000)
    assert [op for op in port.nor_ops if op[0] == 0x03], "no SPI flash reads at all"
    assert not [op for op in port.nor_ops if op[0] in (0x06, 0x02, 0x20, 0x52, 0xD8, 0xC7, 0x60)]


def test_command_42_resets_for_a_hands_free_reflash(built):
    r = Port(*built)
    r.run(until="boot done", limit=300_000_000)
    r.run(limit=5_000_000)
    r.serial_in(b"\xaa\x42\x00\x00")                                   # half a frame: nothing
    assert not r.reset_requested
    r.serial_in(b"\x55\x00\x83\xf4\xef")                               # broken off: still nothing
    assert not r.reset_requested
    r.serial_in(KDH_42)
    assert r.reset_requested


def subpixel_fringes(port, rows, cols=range(128)):
    lit = [port.lfb[y][x] for y in rows for x in cols if port.lfb[y][x]]
    chans = lambda c: (c >> 11, (c >> 5) & 63, c & 31)  # noqa: E731
    return lit, [c for c in lit if len({v > 0 for v in chans(c)}) > 1 and chans(c) != chans(lit[0])]


def test_the_portrait_screen_is_drawn_with_5x5_subpixel_text(built):
    port = Port(*built)                                                 # SURF at boot, no keys
    port.run(until="boot done", limit=300_000_000)
    port.run(limit=30_000_000)
    # every pixel of the 128x160 portrait panel is painted, and SURF's axis labels (5x5 text on the
    # subpixels, grey on black, left of the axis) have the colour fringes only subpixel text makes
    assert all(port.lfb[y][x] is not None for y in range(160) for x in range(128))
    lit, fringes = subpixel_fringes(port, list(range(22, 28)) + list(range(146, 152)), range(0, 20))
    assert len(lit) > 60, len(lit)
    assert fringes, "no subpixel colour fringes in SURF's axis labels"


def test_it_says_alive_every_10_s(built):
    # a logger that opens after the boot lines still sees the image running
    r = Port(*built)
    r.run(until="boot done", limit=300_000_000)
    r.tmr_step = 100                                                    # time runs faster
    out = ""
    for _ in range(10):
        out += r.run(limit=50_000_000)
        if "alive surf s=000a" in out:
            break
    assert "alive surf s=000a" in out, out[-200:]                    # (SURF, the boot screen)


def test_it_says_it_started_before_changing_the_clock(built):
    # on the bootloader's serial setup, so an image that hangs before the banner still shows it ran
    r = Port(*built)
    r.mu.mem_write(0x40021018, struct.pack("<I", 1 << 14))              # APB2EN: USART1 on
    r.mu.mem_write(0x4001380C, struct.pack("<I", 1 << 13 | 1 << 3))    # USART1 CTRL1: UEN, TEN
    out = r.run(until="boot done", limit=300_000_000)
    assert out.index("blowfang uv21 port start\r\n") < out.index("blowfang uv21 port 20 build=")
    assert out.count("port start") == 1


# The UV-21 stock firmware's BK4829 init (read from the stock image, 2026-11-24): port 13 sends it
# in this order in place of the UV-5RH's (REG_40 read-modify-written in between, low 12 bits 0x50A)
STOCK_INIT = [
    (0x00, 0x8000), (0x00, 0x0000), (0x37, 0x9D1F), (0x10, 0x0318), (0x11, 0x033A),
    (0x12, 0x03DB), (0x13, 0x03DE), (0x14, 0x0210), (0x49, 0x2A32), (0x7B, 0x73DC),
    (0x17, 0x7829), (0x1C, 0x0422), (0x1D, 0x2AAB), (0x1E, 0x4C58), (0x1F, 0x1657),
    (0x3E, 0x94C6), (0x73, 0x6681), (0x77, 0x88EF), (0x28, 0x0B40), (0x29, 0xAA00),
    (0x2A, 0x6600), (0x2C, 0x0022), (0x2F, 0x9890), (0x53, 0x2028), (0x7E, 0x303E),
    (0x19, 0x1041), (0x3A, 0x9A7C), (0x46, 0x6050), (0x4D, 0xA015), (0x4E, 0x5C10),
    (0x4F, 0x2A28), (0x4C, 0xE520), (0x25, 0x6DBA), (0x40, None), (0x7D, 0xE912),
    (0x48, 0xB3FF),
] + [(0x09, v) for v in (0x006F, 0x106B, 0x2067, 0x3062, 0x4050, 0x5047, 0x603A, 0x702C,
                         0x8041, 0x9037, 0xA025, 0xB017, 0xC0E4, 0xD0CB, 0xE0B5, 0xF09F)] + [
    (0x75, 0xFA02), (0x74, 0xFEF0), (0x44, 0x8D8F), (0x45, 0x3359),
]


def test_the_rf_chip_gets_the_stock_init(port):
    w = port.rf_writes()
    i = w.index((0x00, 0x8000))
    got = w[i:i + len(STOCK_INIT)]
    for (reg, v), (greg, gv) in zip(STOCK_INIT, got):
        assert greg == reg, (hex(reg), hex(greg))
        if v is None:
            assert gv & 0x0FFF == 0x050A, hex(gv)
        else:
            assert gv == v, (hex(reg), hex(gv), hex(v))
    later = {r: v for r, v in w[i + len(STOCK_INIT):]}
    for reg in (0x44, 0x45, 0x74, 0x75):                                # the AF filters stand
        assert reg not in later, (hex(reg), hex(later[reg]))
