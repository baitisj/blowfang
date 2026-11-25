"""The FM chip probe (c/k6/mod/fmprobe.c) on an emulated Cortex-M0 against a model of the K6's
RDA5807M on its bit-banged I2C (PA4 SCL, PA12 SDA): the bus protocol bit by bit (start, stop,
acknowledge, sequential 0x10 and random 0x11 access), the pins put back exactly and no other pin
touched, no line driven high against the chip, the sweep / shape / settling / expert rows, LNA
port choice, verbose mode through REG 0x56 (and OPEN_MODE when the write needs it), RDS counts,
the station name confirmed twice, and the clock group. SPDX-License-Identifier: MIT"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, need_clang, run  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_fmprobe as fp  # noqa: E402

SCL, SDA = 4, 12
MASK2 = (3 << 8) | (3 << 24)
GROUP_US = 87_600


def ps_groups(pi, name):
    """0A groups carrying name, two characters a group (segment in B's last two bits)"""
    out = []
    for seg in range(4):
        out.append((pi, (0 << 12) | seg, 0xE0CD, ord(name[2 * seg]) << 8 | ord(name[2 * seg + 1])))
    return out


def ct_group(pi, when, off_half_h):
    """4A for UTC `when` (a datetime), local offset in half hours"""
    mjd = (when.date() - datetime.date(1858, 11, 17)).days
    b = (4 << 12) | (mjd >> 15)
    c = ((mjd & 0x7FFF) << 1) | (when.hour >> 4)
    d = ((when.hour & 15) << 12) | (when.minute << 6) | (32 if off_half_h < 0 else 0) | abs(off_half_h)
    return (pi, b, c, d)


class Rda:
    """RDA5807M on a bit-banged bus. stations: {kHz: (peak RSSI, stereo, PI, PS or None)}"""

    def __init__(self, stations, present=True, open_needed=False, ex_base=50_000, utc0=None, corrupt_every=7):
        self.stations, self.present, self.open_needed, self.ex_base = stations, present, open_needed, ex_base
        self.utc0 = utc0 or datetime.datetime(2026, 11, 25, 1, 41, 59, 200_000)
        self.corrupt_every = corrupt_every
        self.g = {0x00: 0x5804, 0x56: 0xAEC0}
        self.gpio = {0x00: 0x2800_0000 | (1 << 8) | (1 << 24), 0x04: 0, 0x08: 0x0C00_0000, 0x0C: 0x2400_0000,
                     0x14: 0x0000_8000 | (1 << SCL) | (1 << SDA)}
        self.start_gpio = dict(self.gpio)
        self.lines = (1, 1)
        self.drive = 1                      # the chip's SDA
        self.active, self.bit, self.cur, self.read, self.out = False, 0, 0, False, []
        self.wbytes, self.addr, self.ptr = [], None, 0
        self.contention, self.log, self.writes = 0, [], []
        self.tune_us, self.freq, self.rds_t0 = 0, 0, None
        self.us = 0
        self.cts = []

    # ---- the pins ----
    def level(self, pin):
        mode = self.gpio[0x00] >> (2 * pin) & 3
        od = self.gpio[0x04] >> pin & 1
        o = self.gpio[0x14] >> pin & 1
        if mode != 1:
            return 1                         # input with a pull-up
        if not od and o and pin == SDA and not self.drive:
            self.contention += 1
        return 0 if not o else 1

    def lines_now(self):
        return self.level(SCL), self.level(SDA) & self.drive

    def gpio_r(self, uc, off, size, st):
        self.us = st["us"]
        if off == 0x10:
            scl, sda = self.lines_now()
            return (scl << SCL) | (sda << SDA)  # PA15 (power knob) low
        return self.gpio.get(off, 0)

    def gpio_w(self, uc, off, size, value, st):
        self.us = st["us"]
        if off in (0x00, 0x08, 0x0C):
            assert not (value ^ self.gpio[off]) & ~MASK2, f"GPIOA+{off:#x} touched other pins {value:#x}"
            self.gpio[off] = value
        elif off == 0x04:
            assert not (value ^ self.gpio[off]) & ~((1 << SCL) | (1 << SDA)), f"OTYPER {value:#x}"
            self.gpio[off] = value
        elif off == 0x18:
            assert not value & ~((1 << SCL) | (1 << SDA) | (1 << SCL + 16) | (1 << SDA + 16)), f"BSRR {value:#x}"
            self.gpio[0x14] = (self.gpio[0x14] | (value & 0xFFFF)) & ~(value >> 16)
        elif off == 0x28:
            assert not value & ~((1 << SCL) | (1 << SDA)), f"BRR {value:#x}"
            self.gpio[0x14] &= ~value
        else:
            raise AssertionError(f"GPIOA+{off:#x}")
        self.edge()

    # ---- the bus ----
    def edge(self):
        old_scl, old_sda = self.lines
        scl, sda = self.lines_now()
        self.lines = (scl, sda)
        if not self.present:
            return
        if old_scl and scl and old_sda and not sda:          # start
            self.finish()
            self.active, self.bit, self.cur, self.read, self.wbytes, self.addr = True, 0, 0, False, [], None
            self.drive, self.rose = 1, False
        elif old_scl and scl and not old_sda and sda:        # stop
            self.finish()
            self.active, self.drive = False, 1
        elif self.active and not old_scl and scl:            # rising: sample
            self.rose = True
            if self.bit < 8 and not self.read:
                self.cur = self.cur << 1 | sda
            elif self.bit == 8 and self.read:
                self.mack = not sda
        elif self.active and old_scl and not scl and self.rose:   # falling: next bit
            self.rose = False
            if self.bit < 7:
                self.bit += 1
                if self.read:
                    self.drive = self.rbyte >> (7 - self.bit) & 1
            elif self.bit == 7:
                self.bit = 8
                if self.read:
                    self.drive = 1
                else:
                    self.drive = 0 if self.take(self.cur) else 1
                    self.cur = 0
            else:
                self.bit = 0
                if self.read and self.addr is not None and not getattr(self, "mack", False):
                    self.drive = 1
                    self.active_read_done = True
                    return
                if self.addr is not None and self.addr & 1 and (not self.read or self.mack):
                    self.read = True
                    self.rbyte = self.next_byte()
                    self.drive = self.rbyte >> 7 & 1
                else:
                    self.drive = 1

    def take(self, b):
        if self.addr is None:
            if b >> 1 not in (0x10, 0x11):
                self.addr = None
                self.active = False
                return False
            self.addr = b
            if b & 1:
                self.rq = [0x0A + i for i in range(0x40)] if b >> 1 == 0x10 else [self.ptr + i for i in range(0x40)]
                self.rpos = 0
            return True
        self.wbytes.append(b)
        if self.addr >> 1 == 0x11:
            if len(self.wbytes) == 1:
                self.ptr = b
            elif len(self.wbytes) % 2 == 1:
                self.write(self.ptr + (len(self.wbytes) - 3) // 2, self.wbytes[-2] << 8 | b)
        elif len(self.wbytes) % 2 == 0:
            self.write(2 + (len(self.wbytes) - 2) // 2, self.wbytes[-2] << 8 | b)
        return True

    def next_byte(self):
        reg = self.rq[self.rpos // 2] & 0x7F
        if self.rpos % 2 == 0:
            self.word = self.reg(reg)
            self.log.append(("r", reg, self.word))
        b = self.word >> 8 if self.rpos % 2 == 0 else self.word & 0xFF
        self.rpos += 1
        return b

    def finish(self):
        pass

    # ---- the chip ----
    def write(self, reg, v):
        self.writes.append((reg, v, self.us))
        old = self.g.get(reg, 0)
        if reg == 0x56 and self.open_needed and (self.g.get(6, 0) >> 13) & 3 != 3:
            return
        self.g[reg] = v
        if reg == 2:
            if v & 2:
                keep = {0x00: 0x5804, 0x56: 0xAEC0}
                self.g = {**keep, 2: v}
            if v & 8 and (not old & 8 or v & 2):
                self.rds_t0 = self.us + 40_000
            if not v & 8:
                self.rds_t0 = None
        if reg == 3 and v & 0x10:
            self.tune_us = self.us
            band, space, ch = v >> 2 & 3, v & 3, v >> 6
            r7 = self.g.get(7, 0x4202)
            if r7 & 1:
                base = self.ex_base if band == 3 and not r7 & 0x200 else (87_000 if band == 0 else 76_000)
                self.freq = base + self.g.get(8, 0)
            else:
                base = [87_000, 76_000, 76_000, 65_000 if r7 & 0x200 else 50_000][band]
                self.freq = base + ch * [100, 200, 50, 25][space]
            if self.g.get(2, 0) & 8:
                self.rds_t0 = self.us + 40_000

    def station(self):
        best, st = 0, None
        port = self.g.get(5, 0) >> 6 & 3
        for f, s in self.stations.items():
            off = abs(self.freq - f)
            if off < 120:
                r = s[0] - off // 4
                r -= {0: 60, 1: 30, 2: 0, 3: 2}[port]
                if r > best:
                    best, st = r, (f, s, off)
        return max(best, 12), st

    def rssi(self):
        full, _ = self.station()
        t = (self.us - self.tune_us) / 300_000
        return int(full * min(1.0, t)) if self.g.get(2, 0) & 1 else 0

    def verbose(self):
        return self.g.get(0x56, 0) & 0xFF == 0xFF

    def group(self):
        """the latest group: (index, blocks, BLER a..d) or None"""
        full, st = self.station()
        if not st or self.rds_t0 is None or st[2] > 20 or full < 65 or not st[1][3]:
            return None
        f, (pk, stereo, pi, name), _ = st
        k = int((self.us - self.rds_t0) // GROUP_US)
        if k < 0:
            return None
        when = self.utc0 + datetime.timedelta(microseconds=self.us)
        start = self.utc0 + datetime.timedelta(microseconds=self.rds_t0 + k * GROUP_US)
        if k == 10:                         # the clock (a station sends it on the minute; here at group 10)
            g = ct_group(pi, start.replace(second=0, microsecond=0), -16)
            if not self.cts or self.cts[-1][0] != (self.rds_t0, k):
                self.cts.append(((self.rds_t0, k), start.replace(second=0, microsecond=0)))
        else:
            cyc = ps_groups(pi, name) + [(pi, 2 << 12, 0x4142, 0x4344)]
            g = cyc[k % len(cyc)]
        bler = [0, 0, 0, 0]
        if self.corrupt_every and k % self.corrupt_every == 3 and g[1] >> 12 == 0:
            g = (g[0], g[1], g[2], g[3] ^ 0x1F1F)
            bler[3] = 3
        del when
        return k, g, bler

    def reg(self, r):
        if r == 0x0A:
            g = self.group()
            rdsr = 0
            if g:
                t_in = (self.us - self.rds_t0) % GROUP_US
                rdsr = int(t_in < 20_000)
            stc = int(self.us - self.tune_us >= 10_000)
            _, st = self.station()
            stereo = int(bool(st and st[1][1] and st[2] < 30 and self.rssi() > 40))
            return rdsr << 15 | stc << 14 | int(bool(g)) << 12 | stereo << 10
        if r == 0x0B:
            g = self.group()
            _, st = self.station()
            fm = int(bool(st and st[2] <= 20 and self.rssi() > 20))
            bl = (g[2][0] << 2 | g[2][1]) if g and self.verbose() else 0
            return self.rssi() << 9 | fm << 8 | bl
        if 0x0C <= r <= 0x0F:
            g = self.group()
            return g[1][r - 0x0C] if g else 0
        if r == 0x10:
            g = self.group()
            return (g[2][2] << 14 | g[2][3] << 12) if g and self.verbose() else 0
        return self.g.get(r, 0)


STATIONS = {98_100: (80, True, 0x1234, "KAAA-FM "), 104_300: (90, True, 0x4321, "KBBB 104"),
            94_900: (55, False, 0x1111, None), 101_500: (68, True, 0x2222, "KCCC    ")}
FAST = dict(quick_ms=200, settle_ms=320, rds1_s=3, rds_s=1, shape_n=30)


def go(rda, keys=(), **par):
    from modem import k6mod
    code, entry = k6mod.build(fp.SRC)
    mmio = [(0x48000000, 0x400, rda.gpio_r, rda.gpio_w)]
    blk = fp.pack(**{**FAST, **par})
    return run(dict(START), code=(fp.patch(code, blk), entry), mmio=mmio, keys=keys, level=4, spare=2048,
               count=4_000_000_000, paint=True)


@need_clang
def test_builds_level_2_one_block_fits_the_store():
    from modem import k6mod
    code, entry = k6mod.build(fp.SRC)
    assert k6mod.source_level(fp.SRC) == 2 and entry == 0
    assert code.count(fp.MAGIC) == 1 and len(code) <= 11_752 - 16 - 8
    assert len(fp.pack()) == 32 and fp.unpack(fp.pack())["sw_hi"] == 319


@need_clang
def test_no_chip_leaves_quickly_and_restores_the_pins():
    rda = Rda(STATIONS, present=False)
    ret, said, regs, writes = go(rda)
    assert ret == -4 and said[0] == "a 10=0 11=0 60=0" and said[-1].startswith("E n=-4")
    assert rda.gpio == rda.start_gpio


@need_clang
def test_full_run():
    rda = Rda(STATIONS)
    ret, said, regs, writes = go(rda)
    text = "\n".join(said)
    r = fp.decode(text, FAST)
    assert said[0] == "a 10=1 11=1 60=0"
    assert r["dump"]["d"][0] == 0x5804 and r["dump"]["d"][0x56] == 0xAEC0
    assert r["dump"]["D"][2] & 1                                     # enabled after power-up
    # the sweep: 101 channels, the four stations' channels are the peaks, strongest first
    assert len(r["sweep"]) == 101
    loud = {f: v for f, v, fl in r["sweep"] if fl in "tS"}
    assert set(loud) == set(STATIONS)
    assert [p[0] for p in r["peaks"]] == [104_300, 98_100, 101_500, 94_900]   # the floor isn't a peak
    assert r["peaks"][0][2] == 90 and r["peaks"][0][3] == "tS" and r["peaks"][3][3] == "t."
    assert r["lna"] == [30, 60, 90, 88]                              # LNAP best: kept
    stc, curve = r["settle"]
    assert 10 <= stc <= 16 and curve[-1] == 90 and curve[0] < 20 and 13 <= curve.index(90) <= 15
    # shape: peak in the middle (OE3HBW's 50 MHz base), 1 unit per 4 kHz
    h = r["h"]
    assert len(h) == 61 and h.index(max(h)) == 30 and max(h) == 60      # quick reads: 2/3 settled
    assert fp.width(h, 5, 10)[0] == 120
    assert r["k"][4] == 60 and r["k"][3] == r["k"][5] < 60 and r["k"][0] < 20
    # expert sweep: loud only on the stations' MHz (98, 104, ... ), in the right place
    x = r["x"]
    assert len(x) == 66 and x[98 - 50] > x[60 - 50] + 20 and x[104 - 50] == x[60 - 50]   # 104.0 is 300 kHz off
    assert r["verbose"] == "aec0>aeff ok"
    # RDS: the strongest and the next two, then the weakest peak (94.9: no RDS)
    assert list(r["rds"]) == [104_300, 98_100, 101_500, 94_900]
    d = r["rds"][104_300]
    assert d["g"] >= 25 and d["ok"] < d["g"] and d["ab"] == d["g"] and d["q"] == 90
    assert r["rds"][94_900]["g"] == 0
    assert r["ps"][104_300] == ("4321", "KBBB 104") and r["ps"][98_100][1].startswith("KAAA")
    assert [c["khz"] for c in r["ct"]] == [104_300, 98_100, 101_500]   # one each; 94.9 has no RDS
    ct, want = r["ct"][0], rda.cts[-1][1]
    assert (ct["hh"], ct["mm"], ct["off"], ct["e"]) == (want.hour, want.minute, -16, "0000")
    assert fp.mjd_date(ct["mjd"]) == want.date() == datetime.date(2026, 11, 25)
    assert ret == len(loud)
    # the end: 0x56 back, chip off, the pins exactly as they were, never driven against the chip
    assert rda.g[0x56] == 0xAEC0 and not rda.g[2] & 1
    assert rda.gpio == rda.start_gpio and rda.contention == 0
    assert said[-1].startswith(f"E n={ret} ")
    rep = fp.report(r, FAST)
    assert "verbose" in rep and f"{want:%Y-%m-%d %H:%M} UTC" in rep


@need_clang
def test_verbose_needs_open_mode():
    rda = Rda({104_300: STATIONS[104_300]}, open_needed=True)
    ret, said, regs, writes = go(rda, n_rds=1, flags=4)
    r = fp.decode("\n".join(said), FAST)
    assert r["verbose"] == "aec0 om>aeff ok"
    assert rda.g[0x56] == 0xAEC0 and rda.g.get(6, 0) == 0           # both put back


@need_clang
def test_without_verbose_the_name_still_needs_two_matches():
    rda = Rda({104_300: STATIONS[104_300]}, open_needed=True)
    rda.write = (lambda w: (lambda reg, v: None if reg == 0x56 else w(reg, v)))(rda.write)
    ret, said, regs, writes = go(rda, n_rds=1, flags=4)
    r = fp.decode("\n".join(said), FAST)
    assert r["verbose"].endswith(" no")
    assert r["ps"][104_300][1] == "KBBB 104"                          # corrupted D never repeats


@need_clang
def test_key_stops_and_cleans_up():
    rda = Rda(STATIONS)
    ret, said, regs, writes = go(rda, keys=((3_000_000, 4_000_000),))
    assert ret == -2 and not rda.g[2] & 1
    assert rda.gpio == rda.start_gpio
