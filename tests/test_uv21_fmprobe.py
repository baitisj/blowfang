"""The UV-21 Pro's FM chip probe (radios/uv21/mod/fmprobe.c), a cable RAM module, run on an
emulated Cortex-M4 against the K6 test's RDA5807M model moved to the UV-21's pins: SCL PA4 and
SDA PB6, the pin the BK4829's 3-wire bus shares. What must hold: both images fit the 4 KB block
with room for their RAM; the address scan finds the chip (and a TEA5767-mode 0x60); no other pin
is touched (the BK4829's SCN on PB3 stays high), nothing is driven high against the chip and both
pins end exactly as they were; part 7 gives the dumps, sweep, peaks, LNA, settling and shapes;
part 8 gives verbose mode, RDS, the station name and the clock group; and tools/k6_fmprobe.py
report reads both. SPDX-License-Identifier: MIT"""
import datetime
import os
import sys

import pytest

unicorn = pytest.importorskip("unicorn")
import unicorn.arm_const as A  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from test_k6_fmprobe import STATIONS, Rda  # noqa: E402
from test_k6_i2s import need_clang  # noqa: E402

import k6_fmprobe as fp  # noqa: E402

SRC = os.path.join(ROOT, "radios", "uv21", "mod", "fmprobe.c")
NAMES = ["fd_read", "fd_write", "tune", "delay_us", "ms", "say", "front_end"]
SCL, SDA, SCN = 4, 6, 3
FAST = ["FP_QUICK_MS=200", "FP_SETTLE_MS=320", "FP_RDS1_S=3", "FP_RDS_S=1", "FP_SHAPE_N=30"]


class Rda21(Rda):
    """The model on two ports: SCL on GPIOA, SDA on GPIOB (AT32F421: CFGR, OMODE, PULL, IDT, ODT,
    SCR, CLR at the STM32F0's offsets). tea: the chip also answers 0x60."""

    def __init__(self, stations, tea=True, **kw):
        super().__init__(stations, **kw)
        self.tea = tea
        self.pa = {0x00: 0x2800_0000, 0x04: 0, 0x0C: 0x2400_0000, 0x14: 0x0000_8000}
        self.pb = {0x00: (1 << 2 * SCN) | (1 << 2 * 5) | (1 << 2 * SDA), 0x04: 0, 0x0C: 0,
                   0x14: 1 << SCN}
        self.start = (dict(self.pa), dict(self.pb))

    def level(self, port, pin):
        g = self.pa if port == "a" else self.pb
        mode, od, o = g[0x00] >> 2 * pin & 3, g[0x04] >> pin & 1, g[0x14] >> pin & 1
        if mode != 1:
            return 1
        if not od and o and port == "b" and pin == SDA and not self.drive:
            self.contention += 1
        return o

    def lines_now(self):
        return self.level("a", SCL), self.level("b", SDA) & self.drive

    def port_r(self, port):
        def r(uc, off, size, st):
            self.us = st["us"]
            g = self.pa if port == "a" else self.pb
            if off == 0x10:
                scl, sda = self.lines_now()
                return scl << SCL if port == "a" else (sda << SDA) | (g[0x14] & (1 << SCN))
            return g.get(off, 0)
        return r

    def port_w(self, port):
        pin = SCL if port == "a" else SDA
        bit, m2 = 1 << pin, 3 << 2 * pin

        def w(uc, off, size, value, st):
            self.us = st["us"]
            g = self.pa if port == "a" else self.pb
            if off in (0x00, 0x0C):
                assert not (value ^ g[off]) & ~m2, f"GPIO{port}+{off:#x} touched other pins {value:#x}"
                g[off] = value
            elif off == 0x04:
                assert not (value ^ g[off]) & ~bit, f"GPIO{port} OMODE {value:#x}"
                g[off] = value
            elif off == 0x18:
                assert value == bit, f"GPIO{port} SCR {value:#x}"
                g[0x14] |= bit
            elif off == 0x28:
                assert value == bit, f"GPIO{port} CLR {value:#x}"
                g[0x14] &= ~bit
            else:
                raise AssertionError(f"GPIO{port}+{off:#x}")
            self.edge()
        return w

    def take(self, b):
        if self.addr is None and self.tea and b >> 1 == 0x60:
            b = 0x20 | (b & 1)                         # TEA5767 mode: reads the status like 0x10
        return super().take(b)


def go(rda, part, extra=()):
    from modem import uv21mod
    img = uv21mod.image(*uv21mod.build(SRC, FAST + [f"FP_PART={part}", *extra]))
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    ram = 0x20000000
    mu.mem_map(ram, 1 << 16)
    block, api, stubs, stop, sp0 = ram + 0x1000, ram + 0x100, ram + 0x9000, ram + 0x9F00, ram + 0x8F00
    mu.mem_write(block, img)
    for i in range(len(NAMES)):
        mu.mem_write(stubs + 16 * i, b"\x70\x47")
    mu.mem_write(stop, b"\x00\xbf" * 8)
    mu.mem_write(api, (1).to_bytes(4, "little")
                 + b"".join((stubs + 16 * i | 1).to_bytes(4, "little") for i in range(len(NAMES))))
    st = {"us": 0, "said": []}
    mu.mmio_map(0x48000000, 0x400, rda.port_r("a"), st, rda.port_w("a"), st)
    mu.mmio_map(0x48000400, 0x400, rda.port_r("b"), st, rda.port_w("b"), st)

    def on_stub(uc, addr, size, _):
        name = NAMES[(addr - stubs) // 16]
        r0 = uc.reg_read(A.UC_ARM_REG_R0)
        ret = 0
        if name == "delay_us":
            st["us"] += r0
        elif name == "ms":
            ret = st["us"] // 1000
        elif name == "say":
            st["said"].append(bytes(uc.mem_read(r0, 80)).split(b"\0")[0].decode())
            assert len(st["said"][-1]) <= 63, st["said"][-1]
        else:
            raise AssertionError(f"called {name}")
        st["us"] += 2
        uc.reg_write(A.UC_ARM_REG_R0, ret & 0xFFFFFFFF)
    mu.hook_add(unicorn.UC_HOOK_CODE, on_stub, begin=stubs, end=stubs + 16 * len(NAMES))
    ram_at = (len(img) + 7) & ~7
    mu.reg_write(A.UC_ARM_REG_SP, sp0)
    mu.reg_write(A.UC_ARM_REG_LR, stop | 1)
    mu.reg_write(A.UC_ARM_REG_R0, api)
    mu.reg_write(A.UC_ARM_REG_R1, block + ram_at)
    mu.reg_write(A.UC_ARM_REG_R2, 4096 - ram_at)
    entry = int.from_bytes(img[6:8], "little")
    mu.emu_start((block + entry) | 1, stop, count=4_000_000_000)
    ret = mu.reg_read(A.UC_ARM_REG_R0)
    return (ret - (1 << 32) if ret >> 31 else ret), st["said"], len(img)


@need_clang
def test_both_parts_fit_the_block_with_their_ram():
    from modem import uv21mod
    for part in (7, 8):
        img = uv21mod.image(*uv21mod.build(SRC, [f"FP_PART={part}"]))
        assert len(img) + 7 + 420 <= 4096, (part, len(img))


@need_clang
def test_no_chip_leaves_quickly_and_restores_the_pins():
    rda = Rda21(STATIONS, present=False)
    ret, said, _ = go(rda, 7)
    assert ret == -4 and said[1] == "A" and said[2] == "a 10=0 11=0 60=0" and said[-1].startswith("E n=-4")
    assert (rda.pa, rda.pb) == rda.start and rda.contention == 0


@need_clang
def test_part_7_identifies_the_chip_and_sizes_the_lens():
    rda = Rda21(STATIONS)
    ret, said, n = go(rda, 7)
    text = "\n".join(said)
    r = fp.decode(text)
    assert said[0].split()[1:] == ["119", "319", "2", "5", "30", "50", "115", "6", "7"]
    assert said[1] == "A 10 11 60" and said[2] == "a 10=1 11=1 60=1"
    assert said[3].startswith("b10 ") and len(said[3]) == 4 + 32 and "i 5804" in said
    assert r["dump"]["d"][0] == 0x5804 and r["dump"]["D"][2] == 0x806D    # 26 MHz, muted, enabled
    assert r["par"]["shape_n"] == 30
    assert len(r["sweep"]) == 101 and {f for f, v, fl in r["sweep"] if fl in "tS"} == set(STATIONS)
    assert [p[0] for p in r["peaks"]] == [104_300, 98_100, 101_500, 94_900]
    assert r["lna"] == [30, 60, 90, 88]
    stc, curve = r["settle"]
    assert 10 <= stc <= 16 and curve[-1] == 90
    h = r["h"]
    assert len(h) == 61 and h.index(max(h)) == 30 and fp.width(h, 5, 10)[0] == 120
    assert len(r["x"]) == 66 and "verbose" not in r and not r["rds"]
    assert ret == len(STATIONS) and said[-1].startswith(f"E n={ret} ")
    assert not rda.g[2] & 1 and rda.contention == 0 and (rda.pa, rda.pb) == rda.start
    rep = fp.report(r)
    assert "expert shape: peak at +0 kHz" in rep and "120 kHz within 10" in rep


@need_clang
def test_part_8_reads_rds_and_the_clock():
    rda = Rda21(STATIONS)
    ret, said, n = go(rda, 8, ["FP_NRDS=2"])
    r = fp.decode("\n".join(said))
    assert not any(s.startswith(("A", "d", "D", "h", "x", "l ", "c ")) for s in said), said
    assert "i 5804" in said and r["verbose"] == "aec0>aeff ok"
    assert list(r["rds"]) == [104_300, 98_100]
    assert r["ps"][104_300] == ("4321", "KBBB 104")
    ct, want = r["ct"][0], rda.cts[0][1]
    assert (ct["hh"], ct["mm"], ct["off"], ct["e"]) == (want.hour, want.minute, -16, "0000")
    assert fp.mjd_date(ct["mjd"]) == want.date() == datetime.date(2026, 11, 25)
    assert rda.g[0x56] == 0xAEC0 and not rda.g[2] & 1
    assert (rda.pa, rda.pb) == rda.start and rda.contention == 0


@need_clang
def test_another_chip_is_reported_and_left_alone():
    rda = Rda21(STATIONS)
    rda.g[0x00] = 0x1080
    ret, said, _ = go(rda, 7)
    assert ret == -5 and "i 1080" in said and not any(s.startswith("d00") for s in said)
    assert not rda.writes and (rda.pa, rda.pb) == rda.start
