"""The RDS module (c/k6/mod/fmrds.c with c/common/rds.h) on an emulated Cortex-M0 against the
FM chip probe's RDA5807M model (tests/test_k6_fmprobe.py), here with stations that send what a
real one does: the name, programme type and flags in every group's B block, radio text (2A),
the extended country code (1A), and the clock (4A) as the first group to start after each minute
edge of the station's own clock. The checks: the stations and their names, the text, a station
whose clock runs 2 minutes fast outvoted, an errored clock group that reads hour 26 refused, the
vote and the radio's clock set from it (level 8), one station alone not setting it, a key
stopping cleanly, and the pins put back. Station names here are made up.
SPDX-License-Identifier: MIT"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_fmprobe import GROUP_US, Rda, ct_group  # noqa: E402
from test_k6_i2s import START, need_clang, run  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import k6_fmrds as fr  # noqa: E402

UTC0 = datetime.datetime(2026, 11, 25, 9, 59, 20, 0)
TEXT = "Morning show with the weather at the top of the hour\r"


class RdsRda(Rda):
    """extra: {kHz: dict(pty, tp, ms, ecc, rt, err_ms, bad_ct)} for the stations' content"""

    def __init__(self, stations, extra, **kw):
        super().__init__(stations, utc0=UTC0, corrupt_every=0, **kw)
        self.extra = extra
        self.sent = []

    def b(self, x, ty, ver, rest):
        return ty << 12 | ver << 11 | x.get("tp", 0) << 10 | x.get("pty", 0) << 5 | rest

    def group(self):
        full, st = self.station()
        if not st or self.rds_t0 is None or st[2] > 20 or full < 65 or not st[1][3]:
            return None
        f, (pk, stereo, pi, name), _ = st
        x = self.extra.get(f, {})
        k = int((self.us - self.rds_t0) // GROUP_US)
        if k < 0:
            return None
        err = datetime.timedelta(milliseconds=x.get("err_ms", 0))
        start = UTC0 + datetime.timedelta(microseconds=self.rds_t0 + k * GROUP_US) + err
        prev = start - datetime.timedelta(microseconds=GROUP_US)
        bler = [0, 0, 0, 0]
        if start.replace(second=0, microsecond=0) != prev.replace(second=0, microsecond=0):
            minute = start.replace(second=0, microsecond=0)
            pi_, b, c, d = ct_group(pi, minute, -16)
            g = (pi, (b & ~0x7FF) | (b & 3) | x.get("tp", 0) << 10 | x.get("pty", 0) << 5, c, d)
            if x.get("bad_ct"):                         # hour 26, BLER 2 in C
                g = (pi, g[1], (g[2] & ~1) | 1, (g[3] & 0x0FFF) | (10 << 12))
                bler[2] = 2
            if not self.sent or self.sent[-1][:2] != (f, minute):
                self.sent.append((f, minute, self.us))
            return k, g, bler
        rt = x.get("rt")
        j = k % (8 if rt else 5)
        if j < 4:
            g = (pi, self.b(x, 0, 0, x.get("ms", 1) << 3 | j), 0xE0CD, ord(name[2 * j]) << 8 | ord(name[2 * j + 1]))
        elif j == 4:
            g = (pi, self.b(x, 1, 0, 0), x.get("ecc", 0), 0)
        else:                                           # radio text, four characters a group
            seg = (k // 8 * 3 + j - 5) % 16
            t = (rt + " " * 64)[4 * seg:4 * seg + 4]
            g = (pi, self.b(x, 2, 0, seg), ord(t[0]) << 8 | ord(t[1]), ord(t[2]) << 8 | ord(t[3]))
        return k, g, bler


STATIONS = {98_100: (80, True, 0x1234, "KAAA-FM "), 104_300: (90, True, 0x4321, "KBBB 104"),
            94_900: (55, False, 0x1111, None), 101_500: (78, True, 0x2222, "KCCC    ")}
EXTRA = {104_300: dict(pty=10, tp=1, ecc=0xA0, rt=TEXT),
         98_100: dict(pty=1, ecc=0xA0),
         101_500: dict(pty=5, err_ms=120_000)}       # a clock 2 minutes fast
FAST = dict(quick_ms=150, settle_ms=320, scan_ms=2000, clk_s=65, lead_s=3, n_clk=3)


def go(rda, keys=(), clock=None, level=8, lcd=None, **par):
    from modem import k6mod
    code, entry = k6mod.build(fr.SRC)
    mmio = [(0x48000000, 0x400, rda.gpio_r, rda.gpio_w)]
    blk = fr.pack(**{**FAST, **par})
    return run(dict(START), code=(fr.patch(code, blk), entry), mmio=mmio, keys=keys, level=level, spare=2048,
               count=6_000_000_000, clock=clock, paint=True,
               lcd={} if lcd is None else lcd)


@need_clang
def test_builds_and_fits():
    from modem import k6mod
    code, entry = k6mod.build(fr.SRC)
    assert k6mod.source_level(fr.SRC) == 2
    assert code.count(fr.MAGIC) == 1 and len(code) <= 11_752 - 16 - 8
    assert len(fr.pack()) == 32 and fr.unpack(fr.pack(n_clk=5))["n_clk"] == 5


@need_clang
def test_stations_names_text_and_the_voted_clock():
    rda = RdsRda(STATIONS, EXTRA)
    clock, lcd = {"fix": None}, {}
    ret, said, regs, writes = go(rda, clock=clock, lcd=lcd)
    r = fr.decode("\n".join(said))
    assert r["verbose"] == "5804 aec0>aeff ok"
    assert list(r["st"]) == [104_300, 98_100, 101_500, 94_900]          # strongest first
    a = r["st"][104_300]
    assert a["ps"] == "KBBB 104" and a["pi"] == "4321" and a["pty"] == 10 and a["fl"] == "tm"
    assert a["ok"] == a["g"] >= 15
    assert r["st"][98_100]["ps"] == "KAAA-FM " and r["st"][94_900]["g"] == 0
    assert "".join(r["rt"][104_300]).startswith("Morning show with")
    assert r["y"][104_300]["ecc"] == 0xA0 and {"0A", "1A", "2A", "4A"} <= set(r["y"][104_300]["gt"])
    # three clock groups, the fast station's 2 minutes off; the two that agree vote
    assert [c["khz"] for c in r["ct"]] == [104_300, 98_100, 101_500]
    assert all(c["e"] == "000" and c["off"] == -16 for c in r["ct"])
    assert ret == 2 and r["clock"]["v"] == 2
    errs = dict(r["d"])
    assert abs(errs[104_300]) < 100 and abs(errs[98_100]) < 100 and 119_000 < errs[101_500] < 121_000
    # the clock set: UTC within 150 ms of the model's at the moment of the call, the date right
    (us, ms, date, w) = clock["sets"][-1]
    true = UTC0 + datetime.timedelta(microseconds=us)
    true_ms = ((true.hour * 60 + true.minute) * 60 + true.second) * 1000 + true.microsecond // 1000
    assert abs(ms - true_ms) < 150, (ms, true_ms)
    assert date == 25 | 11 << 8 | 2026 << 16 and 500 <= w < 700
    assert rda.gpio == rda.start_gpio and rda.contention == 0 and not rda.g[2] & 1
    assert rda.g[0x56] == 0xAEC0
    shown = [f["text"] for f in lcd["frames"]]
    assert any(t[0] == "FM RDS" and "104.3 KBBB 104" in t for t in shown)
    assert shown[-1][1].startswith("UTC 10:0") and shown[-1][1].endswith(" V2")
    assert lcd["st"]["stack"] < 600, lcd["st"]["stack"]
    rep = fr.report(r)
    assert "Country" in rep and "News" in rep and "voted clock: 2026-11-25" in rep
    assert fr.check(r) == (True, [])


@need_clang
def test_a_garbled_clock_group_is_refused_and_one_station_does_not_set_the_clock():
    st = {104_300: STATIONS[104_300], 98_100: STATIONS[98_100]}
    ex = {104_300: dict(pty=10), 98_100: dict(pty=1, bad_ct=True)}
    rda = RdsRda(st, ex)
    clock = {"fix": (0, 1000, 0, 60_000)}                # the radio had a loose clock already
    ret, said, regs, writes = go(rda, clock=clock, n_clk=2)
    r = fr.decode("\n".join(said))
    assert [c["khz"] for c in r["ct"]] == [104_300]      # hour 26 with BLER 2: never a clock line
    assert ret == 1 and r["clock"]["v"] == 1
    assert "radio" in r and clock.get("sets") is None    # compared, not set (one station)
    ret2, said2, _, _ = go(RdsRda(st, ex), clock={"fix": None}, n_clk=2, flags=2)
    assert ret2 == 1                                      # flags 2: one clean station may set it


@need_clang
def test_key_stops_and_cleans_up():
    rda = RdsRda(STATIONS, EXTRA)
    ret, said, regs, writes = go(rda, keys=((20_000_000, 21_000_000),), clock={"fix": None})
    assert ret == -2 and said[-1].startswith("E n=-2")
    assert rda.gpio == rda.start_gpio and not rda.g[2] & 1


def test_check_fails_a_run_that_heard_nothing_or_stopped():
    r = fr.decode("v 5804 aec0>aeff ok\nf0 ....\nE n=-2 ms=100")
    ok, bad = fr.check(r)
    assert not ok and any("returned -2" in b for b in bad) and any("no station" in b for b in bad)
    assert not fr.check(fr.decode("a 10=0 11=0"))[0]
