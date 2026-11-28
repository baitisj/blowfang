"""The UV-21 Pro's clock moved from a RAM module and put back (radios/uv21/mod/clk120.c), in the
emulated board: from a 60 MHz port (main's, as the radio runs it: the emulated image's clock
registers set to main's values) to 120 MHz and back, and from this branch's 120 MHz image down to
60 MHz and back. The module must set the peripherals the port derives from its clock for the new
one and put the port's own values back, switch the way Artery's BSP does (auto step around the jump
to the PLL), never touch the flash wait states, arm the watchdog deadman first, refuse a clock setup
it doesn't know, and go home when the PLL doesn't lock. Its screen grabs must match at both clocks,
its test card must read back whole, and radios/uv21/mod/clk120.py must call the run a pass."""
import os
import re
import struct
import sys

import pytest

pytest.importorskip("unicorn")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "radios", "uv21", "mod"))
import clk120 as uv21_clk  # noqa: E402
import screen_grab  # noqa: E402
from modem import uv21mod  # noqa: E402
from unicorn import UC_MEM_READ  # noqa: E402
from sim.uv21.board import Station, UV21Board  # noqa: E402
from test_uv21_mod import MOD, built, upload  # noqa: E402,F401
from test_uv21_surf import wait  # noqa: E402

CRM_CTRL, CRM_CFG, CRM_MISC2 = 0x40021000, 0x40021004, 0x40021054
FLASH_PSR, WDT = 0x40022000, 0x40003000
BAUDR, SPI2_CTRL1, TMR14_DIV = 0x40013808, 0x40003800, 0x40002028
Y0, ROWS = 36, 12                      # bars and checkerboard: a few rows keep the run short
FAST = [f"SD_Y0={Y0}", f"SD_ROWS={ROWS}", "CK_SOAK_S=1", "CK_FL_LEN=0x800"]


def mult(cfg):
    return ((cfg >> 29 & 3) << 4 | (cfg >> 18 & 0xF)) + 1


def card(x, y):
    bars = [0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000]
    if y < 40:
        return bars[x >> 4]
    if y < 80:
        return 0xFFFF if (x ^ y) & 1 else 0
    if y < 120:
        return (x >> 2) << 11 | ((y - 80) & 63) << 5 | ((x + y) & 31)
    return 1 << ((x + y) & 15)


class NoLock(UV21Board):
    """a PLL that never locks at x30, once armed (the image locks there itself at boot)"""
    armed = False

    def mmio(self, uc, access, addr, size, value, ud):
        super().mmio(uc, access, addr, size, value, ud)
        if self.armed and addr == CRM_CTRL and access == UC_MEM_READ:
            cfg = struct.unpack("<I", uc.mem_read(CRM_CFG, 4))[0]
            if mult(cfg) == 30:
                v = struct.unpack("<I", uc.mem_read(addr, 4))[0]
                uc.mem_write(addr, struct.pack("<I", v & ~(1 << 25)))


def board(built, cls=UV21Board):  # noqa: F811
    b = cls(*built, band=[Station("a", 439_500_000, -70)])
    b.run(until="boot done", limit=300_000_000)
    wait(b, 1)
    return b


def rd(b, addr):
    return struct.unpack("<I", b.mu.mem_read(addr, 4))[0]


def wr(b, addr, v):
    b.mu.mem_write(addr, struct.pack("<I", v))


def as_60_mhz_port(b):
    """main's 60 MHz clock registers in place of this branch's 120 MHz ones (the emulator times by
    its own clock, so only the module sees them): x15, ADCDIV /2, 521, SPI2 /4, TMR14 5999"""
    cfg = rd(b, CRM_CFG)
    wr(b, CRM_CFG, (cfg & ~(0xF << 18 | 3 << 29 | 3 << 14 | 1 << 28)) | 14 << 18)
    wr(b, BAUDR, 521)
    wr(b, SPI2_CTRL1, (rd(b, SPI2_CTRL1) & ~(7 << 3)) | 1 << 3)
    wr(b, TMR14_DIV, 5999)


def regs(b):
    return {a: rd(b, a) for a in (CRM_CFG, BAUDR, SPI2_CTRL1, TMR14_DIV, CRM_MISC2)}


def run(b, *defines):
    n = len(b.writes)
    img = uv21mod.image(*uv21mod.build(os.path.join(MOD, "clk120.c"), [*FAST, *defines]))
    ret, out = upload(b, img)
    return ret, out, b.writes[n:]


def line(out, pat):
    m = re.search(pat, out)
    assert m, (pat, out[-1500:])
    return m


def test_60_to_120_mhz_and_back(built):  # noqa: F811
    b = board(built)
    as_60_mhz_port(b)
    before = regs(b)
    ret, out, writes = run(b)
    assert ret == 0, out[-2000:]
    new = line(out, r"clk new mhz=120 c=([0-9a-f]{8}) p=153 b=([0-9a-f]{4}) s=([0-9a-f]{3}) t=([0-9a-f]{4})")
    cfg = int(new.group(1), 16)
    assert mult(cfg) == 30 and cfg & 0xF == 0xA                      # 4 MHz x 30, SCLK on the PLL
    assert (cfg >> 14 & 3) == 2 and not cfg >> 28 & 1                 # ADCDIV /6: 20 MHz
    assert int(new.group(2), 16) == 1042                              # 115,163 baud
    assert int(new.group(3), 16) >> 3 & 7 == 2                        # SPI2 /8: 15 MHz
    assert int(new.group(4), 16) == 11999                             # TMR14: 100 us
    assert regs(b) == before                                          # the port's own, back
    line(out, r"clk back mhz=60 ")
    # Artery's order: auto step on, SCLK to the PLL, auto step off; the wait states never touched
    seq = [(a, v) for a, v in writes if a in (CRM_CFG, CRM_MISC2)]
    up = next(i for i, (a, v) in enumerate(seq) if a == CRM_CFG and v & 3 == 2 and mult(v) == 30)
    assert seq[up - 1][0] == CRM_MISC2 and seq[up - 1][1] >> 4 & 3 == 3
    assert seq[up + 1][0] == CRM_MISC2 and seq[up + 1][1] >> 4 & 3 == 0
    assert all(mult(v) in (15, 30) for a, v in writes if a == CRM_CFG)
    assert not [w for w in writes if w[0] == FLASH_PSR]
    wdt = [v & 0xFFFF for a, v in writes if a in (WDT, WDT + 4, WDT + 8)]
    arm = wdt.index(0x5555)                                           # (feeds before it: no effect)
    assert wdt[arm:arm + 5] == [0x5555, 6, 0xFFF, 0xAAAA, 0xCCCC] and 0xAAAA in wdt[arm + 5:]
    started = next(i for i, (a, v) in enumerate(writes) if a == WDT and v & 0xFFFF == 0xCCCC)
    assert started < next(i for i, (a, v) in enumerate(writes) if a == CRM_CFG and mult(v) == 30)
    # the checks: RF, flash, the card, SURF's frame the same at both clocks
    for tag in ("home", "new", "back"):
        line(out, rf"clk rf {tag} id=4829 n=\d+ bad=0")
        line(out, rf"clk fl {tag} crc=[0-9a-f]{{8}} ok")
    line(out, r"clk pat new draw=\d+ bad=0")
    line(out, r"clk pat back draw=\d+ bad=0")
    line(out, r"clk soak s=1 rounds=[1-9]\d* bad=0")
    line(out, r"clk done fails=000")
    grabs = uv21_clk.grabs(out)
    assert set(grabs) == {"surf home", "surf new", "card new"}
    assert grabs["surf home"][3] == grabs["surf new"][3]
    px = grabs["card new"][3]
    assert [px[y] for y in range(ROWS)] == [[card(x, Y0 + y) for x in range(128)] for y in range(ROWS)]
    v = uv21_clk.verdict(out, ret)
    assert v["pass"], v
    # SURF draws its own screen again over the card
    wait(b, 2)
    assert [b.lfb[Y0][x] or 0 for x in range(128)] != [card(x, Y0) for x in range(128)]


def test_120_mhz_image_down_to_60_and_back(built):  # noqa: F811
    b = board(built)
    before = regs(b)
    assert mult(before[CRM_CFG]) == 30
    ret, out, _ = run(b, "CK_MULT=15", "CK_GRAB=0", "CK_RSSI=0")
    assert ret == 0, out[-2000:]
    new = line(out, r"clk new mhz=60 c=([0-9a-f]{8}) p=153 b=([0-9a-f]{4}) s=([0-9a-f]{3}) t=([0-9a-f]{4})")
    cfg = int(new.group(1), 16)
    assert mult(cfg) == 15 and (cfg >> 14 & 3) == 1                  # ADCDIV /4 at 60: 15 MHz (the port leaves /2 = 30)
    assert int(new.group(2), 16) == 521 and int(new.group(3), 16) >> 3 & 7 == 1
    assert int(new.group(4), 16) == 5999
    assert regs(b) == before


def test_refuses_a_clock_it_does_not_know(built):  # noqa: F811
    b = board(built)
    ret, out, writes = run(b, "CK_MULT=30")                           # already at x30
    assert ret == 1 and "clk refused" in out
    assert not [w for w in writes if w[0] in (CRM_CTRL, CRM_CFG, CRM_MISC2, WDT)]


def test_goes_home_when_the_pll_does_not_lock(built):  # noqa: F811
    b = board(built, NoLock)
    b.armed = True
    as_60_mhz_port(b)
    before = regs(b)
    ret, out, _ = run(b, "CK_GRAB=0", "CK_RSSI=0")
    assert ret == 2, out[-2000:]                                       # F_LOCK only
    line(out, r"clk new mhz=60 ")
    line(out, r"clk back mhz=60 ")
    assert regs(b) == before
    assert not uv21_clk.verdict(out, ret)["pass"]
