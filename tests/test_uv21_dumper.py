"""radios/uv21/dumper: the one-page UV-21 Pro dumper, run in an emulated AT32F421 (Unicorn), read
back by tools/uv21_kdh.py, and checked to read only: no flash register, no SPI command but JEDEC ID
and READ, no RF-chip pin driven."""
import hashlib
import os
import struct
import subprocess
import sys

import pytest

unicorn = pytest.importorskip("unicorn")
from unicorn import UC_ARCH_ARM, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc  # noqa: E402,E501
from unicorn.arm_const import UC_ARM_REG_SP  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import uv21_kdh as kdh  # noqa: E402

BIN = os.path.join(ROOT, "radios", "uv21", "build", "dumper.bin")
GPIOA, GPIOC, GPIOF = 0x48000000, 0x48000800, 0x48001400


@pytest.fixture(scope="module")
def image():
    subprocess.run(["make", "-s", "-C", os.path.join(ROOT, "radios", "uv21")], check=True)
    return open(BIN, "rb").read()


class Radio:
    """an AT32F421 with 64 KB of stock-looking flash, the dumper written over app page 0, a 128 KB
    SPI flash (JEDEC 20 40 11) and a serial line; every peripheral write is recorded"""

    def __init__(self, image, rx=b"", stop_after=2):
        self.flash = bytearray(hashlib.sha256(b"stock").digest() * 2048)
        self.flash[0x1000:0x1400] = image + b"\xff" * (0x400 - len(image))
        self.sysarea = bytes((i * 7) & 0xFF for i in range(0x1000))
        self.info = struct.pack("<H", 64) + bytes(6) + bytes(range(1, 13))
        self.usd = bytes(range(0xA0, 0xB0))
        self.spiflash = bytes((i * 13 + 5) & 0xFF for i in range(1 << 17))
        self.rx, self.stop_after = bytearray(rx), stop_after
        self.uart, self.writes, self.spi_cmds = bytearray(), [], []
        self.cs, self.cmd, self.resp = 1, [], 0
        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        mu.mem_map(0x08000000, 0x10000)
        mu.mem_write(0x08000000, bytes(self.flash))
        sysm = bytearray(0x2000)
        sysm[0x400:0x1400] = self.sysarea
        sysm[0x17E0:0x17F4] = self.info
        sysm[0x1800:0x1810] = self.usd
        mu.mem_map(0x1FFFE000, 0x2000)
        mu.mem_write(0x1FFFE000, bytes(sysm))
        mu.mem_map(0x20000000, 0x4000)
        mu.mem_map(0x40000000, 0x30000)
        mu.mem_map(0x48000000, 0x2000)
        mu.mem_map(0xE0000000, 0x100000)
        mu.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.mmio, begin=0x40000000, end=0x4FFFFFFF)

    def mmio(self, uc, access, addr, size, value, ud):
        put = lambda v: uc.mem_write(addr, struct.pack("<I", v))  # noqa: E731
        if access == UC_MEM_WRITE:
            self.writes.append((addr, value))
            if addr == 0x40013804:
                self.uart.append(value & 0xFF)
                u = self.uart
                if len(u) >= 11 and u[-11:-8] == b"\xa5\x5aE" and u[-4:-2] == b"\0\0":
                    self.stop_after -= 1
                    if self.stop_after == 0:
                        uc.emu_stop()
            elif addr == GPIOA + 0x18 and value & (1 << 8):
                self.cs, self.cmd = 1, []
            elif addr == GPIOA + 0x28 and value & (1 << 8):
                self.cs = 0
            elif addr == 0x4001300C:
                assert self.cs == 0, "SPI byte with CS high"
                self.cmd.append(value & 0xFF)
                c = self.cmd
                if len(c) == 1:
                    self.spi_cmds.append(c[0])
                if c[0] == 0x9F:
                    self.resp = (0, 0x20, 0x40, 0x11)[len(c) - 1] if len(c) <= 4 else 0
                elif c[0] == 0x03:
                    self.resp = 0 if len(c) <= 4 else self.spiflash[(len(c) - 5) % len(self.spiflash)]
                else:
                    self.resp = 0
        else:
            if addr == 0x40013800:
                put(0xC0 | (0x20 if self.rx else 0))
            elif addr == 0x40013804 and self.rx:
                put(self.rx.pop(0))
            elif addr == 0x40013008:
                put(3)
            elif addr == 0x40021004:
                put(0)
            elif addr == 0x4001300C:
                put(self.resp)

    def run(self, limit=200_000_000):
        sp, pc = struct.unpack_from("<II", self.flash, 0x1000)
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.emu_start(pc | 1, 0, count=limit)
        return bytes(self.uart)


@pytest.fixture(scope="module")
def two_passes(image):
    r = Radio(image)
    return r, r.run()


def test_fits_one_page_with_a_vector_table(image):
    assert len(image) <= 1024
    sp, pc = struct.unpack_from("<II", image)
    assert sp == 0x20004000 and 0x08001000 < pc < 0x08001400 and pc & 1
    kdh.packages(image)                              # the flash tool takes it as it is


def test_no_flash_unlock_keys_or_flash_registers_in_the_image(image):
    words = {struct.unpack_from("<I", image, o)[0] for o in range(0, len(image) - 3, 2)}
    assert not words & {0x45670123, 0xCDEF89AB}      # FLASH_UNLOCK keys
    assert not any(0x40022000 <= w < 0x40022400 for w in words)


def test_reads_everything_back_twice(two_passes):
    r, raw = two_passes
    frames, bad = kdh.parse_frames(raw)
    assert bad == 0
    d = kdh.assemble(frames)
    assert d["banner"] == "blowfang uv21 dumper 1" and len(d["passes"]) == 2 and not d["partial"]
    for p in d["passes"]:
        g = kdh.regions(p)
        assert g["flash"] == bytes(r.flash)
        assert g["sysarea"] == r.sysarea and g["info"] == r.info and g["usd"] == r.usd
        assert g["spi"] == r.spiflash and p["jedec"] == 0x114020
    s = kdh.summary(d, bad)
    assert s["complete"] and s["page0_is_dumper"] and s["flash_kb"] == 64 and s["spi_jedec"] == "20 40 11"


def test_only_reads(two_passes):
    r, _ = two_passes
    assert not [a for a, _ in r.writes if 0x40022000 <= a < 0x40022400], "flash register written"
    assert set(r.spi_cmds) == {0x9F, 0x03}
    cfg_a = [v for a, v in r.writes if a == GPIOA]
    assert cfg_a and all((v >> 4) & 0xF == 0 for v in cfg_a)        # PA2, PA3 (RF bus) inputs
    assert all((v >> 26) & 0xF == 0xA for v in cfg_a)               # PA13, PA14 stay SWD
    assert not [a for a, _ in r.writes if GPIOC <= a < GPIOC + 0x400]   # PC13 (RF bus) untouched
    assert (GPIOF + 0x18, 1 << 6) in r.writes                        # power latch held on


def test_a_d_asks_for_another_pass(image):
    r = Radio(image, rx=b"D", stop_after=3)
    d = kdh.assemble(kdh.parse_frames(r.run())[0])
    assert [p["n"] for p in d["passes"]] == [1, 2, 3]
