"""The UV-21 Pro's FM chip probe (radios/uv21/mod/fmprobe.c), a cable RAM module, run on an
emulated Cortex-M4 against the K6 test's RDA5807M model moved to the UV-21's pins: SCL PA4 and
SDA PB6, the pin the BK4829's 3-wire bus shares. What must hold: both images fit the 4 KB block
with room for their RAM; the address scan finds the chip (and a TEA5767-mode 0x60); no other pin
is touched (the BK4829's SCN on PB3 stays high), nothing is driven high against the chip and both
pins end exactly as they were (and i2cpeek, which reads the chip both the I2C way and the Beken
BK1080 way, reads each model right); part 7 gives the dumps, sweep, peaks, LNA, settling and shapes;
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
    SCR, CLR at the STM32F0's offsets). tea: the chip also answers 0x60. at30: it answers 0x30
    too, with 0x11's random access (the stock firmware's address). dead: every register reads
    0xFFFF until REG 02H's ENABLE is set (what the UV-21's chip did before its stock setup);
    never: it stays that way."""

    def __init__(self, stations, tea=True, at30=False, dead=False, never=False, **kw):
        super().__init__(stations, **kw)
        self.tea, self.at30, self.dead, self.never = tea, at30, dead, never
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
        if self.addr is None and self.at30 and b >> 1 == 0x30:
            b = 0x22 | (b & 1)
        return super().take(b)

    def reg(self, r):
        if self.never or (self.dead and not self.g.get(2, 0) & 1):
            return 0xFFFF
        return super().reg(r)


def go(rda, part, extra=()):
    from modem import uv21mod
    return go_img(rda, uv21mod.image(*uv21mod.build(SRC, FAST + [f"FP_PART={part}", *extra])))


def go_img(rda, img):
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
        assert len(img) + 7 + {7: 380, 8: 240}[part] <= 4096, (part, len(img))   # sc_t, about


@need_clang
def test_no_chip_leaves_quickly_and_restores_the_pins():
    rda = Rda21(STATIONS, present=False, at30=True, tea=False, dead=True)
    ret, said, _ = go(rda, 7)
    assert ret == -4 and said[1] == "A" and said[2] == "a 10=0 11=0 60=0" and said[-1].startswith("E n=-4")
    assert (rda.pa, rda.pb) == rda.start and rda.contention == 0


@need_clang
def test_part_7_identifies_the_chip_and_sizes_the_lens():
    rda = Rda21(STATIONS, at30=True, tea=False, dead=True)
    ret, said, n = go(rda, 7)
    text = "\n".join(said)
    r = fp.decode(text)
    assert said[0].split()[1:] == ["119", "319", "2", "5", "30", "50", "115", "6", "7"]
    assert said[1] == "A 10 11 30" and said[2] == "a 10=1 11=1 60=0"
    assert said[3] == "b10 " + "f" * 32                                 # off: nothing but 0xFF
    assert any(x.startswith("i 5804 ") for x in said)
    assert r["dump"]["d"][0] == 0xFFFF and r["dump"]["D"][0] == 0x5804
    assert r["dump"]["D"][2] == 0x8069                                  # 26 MHz, muted, RDS, on
    # the stock setup, muted: soft reset, 02 without DMUTE, volume 0, and its hidden registers
    regs = [w[0] for w in rda.writes]
    assert rda.writes[0][:2] == (2, 0x0002) and regs[:9] == [2, 2, 5, 7, 0x12, 0x16, 0x1B, 0x36, 0x39]
    assert (0x43, 0x0202) in [w[:2] for w in rda.writes]
    assert all(not v & 0x4000 for r_, v, _ in rda.writes if r_ == 2)  # never unmuted
    assert all(not v & 15 for r_, v, _ in rda.writes if r_ == 5)      # volume 0
    assert r["par"]["shape_n"] == 30
    assert len(r["sweep"]) == 101 and {f for f, v, fl in r["sweep"] if fl in "tS"} == set(STATIONS)
    assert [p[0] for p in r["peaks"]] == [104_300, 98_100, 101_500, 94_900]
    assert r["lna"] == [30, 60, 90, 88]
    stc, curve = r["settle"]
    assert 10 <= stc <= 16 and curve[-1] == 88                        # both inputs (stock), kept
    h = r["h"]
    top = [i for i, v in enumerate(h) if v == max(h)]
    assert len(h) == 61 and top[0] + top[-1] == 60 and 120 <= fp.width(h, 5, 10)[0] <= 130
    assert len(r["x"]) == 66 and "verbose" not in r and not r["rds"]
    assert ret == len(STATIONS) and said[-1].startswith(f"E n={ret} ")
    assert not rda.g[2] & 1 and rda.contention == 0 and (rda.pa, rda.pb) == rda.start
    rep = fp.report(r)
    assert "expert shape: centre +0 kHz" in rep and "chip id 5804 " in rep


@need_clang
def test_part_8_reads_rds_and_the_clock():
    rda = Rda21(STATIONS, at30=True, tea=False, dead=True)
    ret, said, n = go(rda, 8, ["FP_NRDS=2"])
    r = fp.decode("\n".join(said))
    assert not any(s.startswith(("A", "d", "D", "h", "x", "l ", "c ")) for s in said), said
    assert any(x.startswith("i 5804 ") for x in said) and r["verbose"] == "aec0>aeff ok"
    assert list(r["rds"]) == [104_300, 98_100]
    assert r["ps"][104_300] == ("4321", "KBBB 104")
    ct, want = r["ct"][0], rda.cts[0][1]
    assert (ct["hh"], ct["mm"], ct["off"], ct["e"]) == (want.hour, want.minute, -16, "0000")
    assert fp.mjd_date(ct["mjd"]) == want.date() == datetime.date(2026, 11, 25)
    assert rda.g[0x56] == 0xAEC0 and not rda.g[2] & 1
    assert (rda.pa, rda.pb) == rda.start and rda.contention == 0


@need_clang
def test_a_chip_that_never_wakes_is_powered_down_and_left():
    rda = Rda21(STATIONS, at30=True, tea=False, never=True)
    ret, said, _ = go(rda, 7)
    assert ret == -5 and "i ffff ffff ffff" in said and not any(s.startswith(("D00", "w0")) for s in said)
    assert not rda.g[2] & 1 and (rda.pa, rda.pb) == rda.start          # powered down again


class Beken(Rda21):
    """A BK1080-style 2-wire chip at device byte 0x80: the direction is the next byte's low bit,
    (reg << 1) | 1, and a read sends that register's 16 bits (REG 01H = 0x1080)."""

    REGS = {0x00: 0x0006, 0x01: 0x1080}

    def take(self, b):
        if self.addr is None:
            if b & 0xFE != 0x80:
                self.active = False
                return False
            self.addr = 0x80
            return True
        self.wbytes.append(b)
        if len(self.wbytes) == 1 and b & 1:
            self.addr, self.rq, self.rpos = 0x81, [b >> 1] * 64, 0
            self.mack = True
        return True

    def reg(self, r):
        return self.REGS.get(r, 0)


def peek(rda):
    from modem import uv21mod
    src = os.path.join(ROOT, "radios", "uv21", "mod", "i2cpeek.c")
    return go_img(rda, uv21mod.image(*uv21mod.build(src, ["IP_REGS=8"])))


@need_clang
def test_peek_reads_an_rda5807_with_a_repeated_start():
    rda = Rda21(STATIONS)
    ret, said, _ = peek(rda)
    assert ret == 3 and said[0] == "A 10 11 60"
    assert "q22 00 5804 0000 0000 0000 0000 0000 0000 0000" in said
    assert not any(x.startswith("k") for x in said)                  # 0x40 never answered
    assert not rda.writes and (rda.pa, rda.pb) == rda.start and rda.contention == 0


@need_clang
def test_peek_reads_a_beken_chip_its_own_way():
    bk = Beken(STATIONS, tea=False)
    ret, said, _ = peek(bk)
    assert ret == 1 and said[0] == "A 40"
    assert said[1].startswith("q80 00 ") and "1080" not in said[1]
    assert said[2] == "k80 00 0006 1080 0000 0000 0000 0000 0000 0000"
    assert (bk.pa, bk.pb) == bk.start and bk.contention == 0
