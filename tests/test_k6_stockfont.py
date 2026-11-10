"""The stockfont module (c/k6/mod/stockfont.c) on an emulated Cortex-M0 against a modelled SPI
flash on SPI1 (chip select PA2): it must send only READ (0x03) and READ ID (0x9F), give zlib's
CRC-32 of each stock font and the 64 KB block map, and draw the glyphs it read in the LCD's page
format (bit 0 of a column byte at the top) onto the module screen's rows.

`python3 tests/test_k6_stockfont.py out.png` draws the emulated screens.
SPDX-License-Identifier: MIT"""
import os
import random
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, need_clang, run  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "stockfont.c")
F8X16, F5X7, F6X12, GB16, GB12 = 0x04B7C0, 0x04BFC0, 0x076D40, 0x010000, 0x04CF80
SIZE = 2 << 20
CR1 = 0x034F                             # Aura: mode 3, /4, soft NSS, MSTR, SPE


def flash_image(blank_ascii=False, seed=1):
    """2 MB of 0xFF with fake fonts where the stock ones live, and one data byte at the start
    of every 64 KB block except a few (so the map has both kinds and the run stays short)."""
    r = random.Random(seed)
    m = bytearray(b"\xff" * SIZE)
    for a, n in ((GB16, F8X16 - GB16), (GB12, F6X12 - GB12)):
        m[a:a + n] = bytes(r.getrandbits(8) for _ in range(n))
    if not blank_ascii:
        for a, n in ((F8X16, 95 * 16), (F5X7, 95 * 8), (F6X12, 95 * 12)):
            m[a:a + n] = bytes(n)
        # 'A' (8x16): a full first column, both pages; '5' (5x7): a 7-pixel first column
        m[F8X16 + 33 * 16] = 0xFF
        m[F8X16 + 33 * 16 + 8] = 0xFF
        m[F5X7 + 21 * 8] = 0x7F
        # '6' (6x12): column 0 all 12 rows (6 top-page bytes, then 6 bottom-page)
        m[F6X12 + 22 * 12] = 0xFF
        m[F6X12 + 22 * 12 + 6] = 0x0F
    for blk in range(32):
        if blk not in (9, 10, 11, 29, 30, 31):
            m[blk << 16] = m[blk << 16] & 0x7F
    return m


class SpiFlash:
    """SPI1 (0x40013000) and GPIOA (0x48000000) as the module sees them."""

    def __init__(self, mem, cr1=CR1):
        self.mem, self.cr1 = mem, cr1
        self.cs, self.rx, self.cmds, self.state = 1, [], [], None

    def spi_read(self, uc, off, size, st):
        if off == 0x00:
            return self.cr1
        if off == 0x08:
            return 0x02 | (0x01 if self.rx else 0)        # TXE, RXNE while a byte waits
        if off == 0x0C:
            return self.rx.pop(0) if self.rx else 0
        return 0

    def spi_write(self, uc, off, size, value, st):
        assert off == 0x0C and size == 1, f"SPI1+{off:#x} size {size}"
        assert self.cs == 0, "SPI byte with the chip deselected"
        b = value & 0xFF
        s = self.state
        if s is None:
            self.cmds.append(b)
            assert b in (0x03, 0x9F), f"command {b:#04x} (only READ and READ ID allowed)"
            self.state = ["read", 0, 0] if b == 0x03 else ["id", 0]
            out = 0xFF
        elif s[0] == "id":
            out = (0x20, 0x40, 0x15, 0xFF)[min(s[1], 3)]   # XM25QH16C
            s[1] += 1
        elif s[1] < 3:
            s[2] = s[2] << 8 | b
            s[1] += 1
            out = 0xFF
        else:
            out = self.mem[s[2] % SIZE]
            s[2] += 1
        self.rx.append(out)

    def gpio_read(self, uc, off, size, st):
        return 0

    def gpio_write(self, uc, off, size, value, st):
        assert off in (0x18, 0x28), f"GPIOA+{off:#x}"
        assert value == 1 << 2, f"GPIOA write {value:#x}: only PA2"
        self.cs = 1 if off == 0x18 else 0
        self.state = None if self.cs == 0 else self.state

    def mmio(self):
        return ((0x40013000, 0x400, self.spi_read, self.spi_write),
                (0x48000000, 0x400, self.gpio_read, self.gpio_write))


def emulate(mem, cr1=CR1, keys=()):
    fl = SpiFlash(mem, cr1)
    lcd = {}
    ret, said, _, _ = run({}, src=SRC, level=8, mmio=fl.mmio(), lcd=lcd, spare=2048, keys=keys,
                          paint=True, count=2_000_000_000)
    return ret, said, lcd, fl


def bit(row, x):
    return row[x >> 3] >> (7 - (x & 7)) & 1


@need_clang
def test_crcs_map_and_glyphs():
    pytest.importorskip("unicorn")
    mem = flash_image()
    ret, said, lcd, fl = emulate(mem)
    assert ret == 0, said
    assert set(fl.cmds) <= {0x03, 0x9F} and fl.cs == 1
    assert said[0] == f"spi1 cr1={CR1:04x} jedec=204015"
    crc = {n: zlib.crc32(bytes(mem[a:a + k])) for n, a, k in
           (("8x16", F8X16, 1520), ("5x7", F5X7, 760), ("6x12", F6X12, 1140),
            ("gb16", GB16, F8X16 - GB16), ("gb12", GB12, F6X12 - GB12))}
    assert said[1] == f"crc 8x16 {crc['8x16']:08x} 5x7 {crc['5x7']:08x} 6x12 {crc['6x12']:08x}"
    assert said[2] == f"crc gb16 {crc['gb16']:08x} gb12 {crc['gb12']:08x}"
    assert said[3] == "map " + "#########...#################..."
    assert said[-1] == "drawn: done"
    frames = lcd["frames"]
    assert len(frames) == 4 and frames[0]["text"][0] == "STOCK FONTS 1/2"
    assert frames[1]["text"][0] == "STOCK FONTS 2/2"
    wf = frames[0]["wf"]
    assert all(bit(wf[y], 0) for y in range(16))          # 8x16 'A': column 0, rows 0-15
    assert not bit(wf[16], 0)
    # 5x7 line "5x7 ...": '5' at x 0, y 19-25
    assert all(bit(wf[y], 0) for y in range(19, 26)) and not bit(wf[26], 0) and not bit(wf[18], 0)
    wf = frames[1]["wf"]
    assert all(bit(wf[y], 0) for y in range(12)) and not bit(wf[12], 0)   # 6x12 '6'
    assert lcd["st"]["stack"] < 600, lcd["st"]["stack"]


@need_clang
def test_blank_fonts_and_key_out():
    pytest.importorskip("unicorn")
    ret, said, lcd, fl = emulate(flash_image(blank_ascii=True), keys=((0, 10**12),))
    assert ret == 1, said
    assert "FF" in said[1] and said[-1] == "drawn, key: done"
    assert len(lcd["frames"]) == 1 and lcd["frames"][0]["text"][5].startswith("ASCII BLANK")


@need_clang
def test_refuses_an_unset_spi():
    pytest.importorskip("unicorn")
    ret, said, lcd, fl = emulate(flash_image(), cr1=0)
    assert ret == -2 and fl.cmds == [] and "not set up" in said[0]


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import k6_screen
    _, said, lcd, _ = emulate(flash_image())
    print("\n".join(said))
    k6_screen.png(lcd["frames"][:2], sys.argv[1] if len(sys.argv) > 1 else "stockfont.png")
