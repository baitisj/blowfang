"""FuSotto from the K6 (c/k6/mod/fusotx.c, outbox.h, tools/k6_fusotx.py): the sender makes the
call's frames from the outbox as the PC does, sends them on c4lock's chip-locked C4FM (an emulated
Cortex-M0 against the FD6818 FSK model of tests/test_k6_c4fm.py), and a plain YSF receiver gets
the superframes back; through clock wander, without a recording, and from a library slot.
SPDX-License-Identifier: MIT"""
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, FL_LO, k6_clock, need_clang, run  # noqa: E402
from test_k6_c4fm import LOCK_GAIN, REGS, FskFifo  # noqa: E402

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import k6c4fm, k6mod  # noqa: E402
import k6_fusotx as F  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "fusotx.c")
SLOT_SRC = os.path.join(ROOT, "c", "k6", "mod", "fusoslot.c")
ARENA = k6mod.ARENA_BYTES_13
STACK = 2136 - 512      # the radio's "K6 MOD run ... stack=2136 B", less the 512 B to keep untouched
                        # (docs/k6-modules.md); 11-20 fuso1a: 1,584 B here ran out on the radio
_BUILT = {}


def built(src):
    if src not in _BUILT:
        _BUILT[src] = k6mod.build(src)
    return _BUILT[src]


def superframes(n, seed=1):
    return np.random.default_rng(seed).integers(0, 256, 6 * n, dtype=np.uint8).tobytes()


def flash_with(at, data):
    mem = bytearray(b"\xff" * (0x200000 - FL_LO))
    mem[at - FL_LO:at - FL_LO + len(data)] = data
    return mem


class Wander(FskFifo):
    """FskFifo whose chip symbol drifts against the MCU: ppm moves by drift ppm a second of MCU
    time from the first packet on (the MCU's RC clock wandering, 11-18 lock5)"""

    def __init__(self, *a, drift=0.0, **kw):
        super().__init__(*a, **kw)
        self.drift, self.ts0, self.t0 = drift, self.ts, None

    def write(self, reg, v, us):
        if self.drift and reg == 0x59 and v & 0x0800 and not self.ctrl & 0x0800:
            if self.t0 is None:
                self.t0 = us
            # re-anchor the grid where it is now, at the new symbol (the grid stays continuous)
            n = (us - self.g0) / self.ts
            ts = self.ts0 * (1 + self.drift * 1e-6 * (us - self.t0) / 1e6)
            self.g0, self.ts = self.g0 + n * self.ts - n * ts, ts
        en = reg == 0x59 and v & 0x0800 and not self.ctrl & 0x0800
        super().write(reg, v, us)
        if en:
            p = self.pkts[-1]
            p["ts"], p["n"] = self.ts, int(round((p["t_start"] - self.g0) / self.ts))


def air(ev, fsk, fs=48000, data_lag=k6c4fm.LOCK_LAG4 / 4 - 0.8):
    """air_grid on the chip's clock through wander: each packet's symbols at their place on the
    chip's continuous grid (4800 baud as the Pluto hears it), each one's size the REG_70 gain in
    force at its middle in MCU time (the packet's own symbol length)"""
    g70 = [(ns, v & 0x7F) for ns, r, v in ev if r == 0x70 and v & 0x80]
    t70 = np.array([a for a, _ in g70])
    last = fsk.pkts[-1]
    d = np.zeros(int((last["n"] + last["lead"] + last["nsym"] + 4800) / 4800 * fs))
    for p in fsk.pkts:
        ts = p["ts"] * 1000
        b = np.unpackbits(np.array([[w & 0xFF, w >> 8] for w in p["words"]], np.uint8).reshape(-1))
        chip = np.concatenate([np.arange(p["lead"]) % 2, b[:p["nsym"]]])
        for k, c in enumerate(chip):
            mid = p["t_start"] * 1000 + (data_lag + k + 0.5) * ts
            g = g70[np.searchsorted(t70, mid) - 1][1]
            a, z = int((p["n"] + data_lag + k) / 4800 * fs), int((p["n"] + data_lag + k + 1) / 4800 * fs)
            d[a:z] = (2 * int(c) - 1) * g / LOCK_GAIN * 2700.0
    return d


def send(sf=None, src=SRC, level=15, flash=None, drift=0.0, ppm=-604.0, read_us=1.5, keys=((6_000_000, 6_300_000),),
         **kw):
    code = kw.pop("code", None) or built(src)
    mmio, rcc, tim = k6_clock()
    tx, fsk = kw.pop("tx", {}), Wander(ppm, 0.4, 0.3, 0.6, 0.3, drift=drift)
    if flash is None:
        flash = {"mem": flash_with(F.OB_LO, F.outbox(sf))}
    flash.setdefault("read_us", read_us)
    lcd = {}
    ret, said, regs, _ = run(REGS, src=src, code=code, level=level, tx=tx, mmio=mmio, write_us=117, fsk=fsk,
                             flash=flash, lcd=lcd, paint=True, read_us=kw.pop("fd_read_us", 20), spare=ARENA - 24 - len(code[0]), keys=keys,
                             count=4_000_000_000, **kw)
    assert rcc[0x18] == 0 and not tim.get(0x00, 0) & 1
    ev = [(ns, r, v) for (r, v, _), ns in zip(tx.get("w", []), tx.get("w_ns", []))]
    return ret, said, tx, fsk, ev, flash, lcd


def matched(calls, sf):
    """the call's superframes a receiver got right (the data is random: each one is unique)"""
    want = {sf[k:k + 6] for k in range(0, len(sf), 6)}
    return len({s for c in calls for s in c if s in want})


def line(said, head):
    return next(s for s in said if s.startswith(head))


def test_it_fits_the_arena():
    code, _ = built(SRC)
    assert k6mod.source_level(SRC) == 15 and k6mod.source_level(SLOT_SRC) == 13
    assert 16 + len(code) + 8 + 664 + 4 <= ARENA, len(code)   # C4_FUSO_SCRATCH in the scratch


@need_clang
def test_it_sends_the_recording():
    """60 superframes: the frames the PC makes land at OB_PL, the call goes out in four chained
    packets, and a plain YSF receiver gets every superframe back"""
    pytest.importorskip("unicorn")
    sf = superframes(60)
    ret, said, tx, fsk, ev, flash, lcd = send(sf)
    assert ret > 0, said
    want = F.planes(sf)
    got = bytes(flash["mem"][F.OB_PL - FL_LO:F.OB_PL - FL_LO + len(want)])
    assert got == want
    assert "fusotx frames=14 sf=60 of 60" in line(said, "fusotx frames="), said
    m = re.match(r"fusotx sf=60 frames=14 trk=(\d+) dmax=(-?\d+) big=(\d+) slack=(-?\d+)", line(said, "fusotx sf="))
    assert m and int(m.group(1)) == 3 and int(m.group(3)) == 0, said
    assert "late=0" in line(said, "c4lock chain "), said
    calls = F.decode(air(ev, fsk))
    got = [c for c in calls if len(c) >= 60]
    assert len(got) == 1, [len(c) for c in calls]
    assert [s for s in got[0][:60]] == [sf[6 * k:6 * k + 6] for k in range(60)]
    assert lcd["st"]["stack"] <= STACK, lcd["st"]["stack"]


@need_clang
@pytest.mark.parametrize("drift", [0.0, 50.0, -80.0])
def test_a_long_call_through_clock_wander(drift):
    """400 superframes (82 frames, 21 packets, 8 s on air) with the MCU's clock moving up to 80
    ppm a second against the chip's: each packet's TX done steers the grid, so all of them stay on
    it and the receiver loses next to nothing"""
    pytest.importorskip("unicorn")
    sf = superframes(400, seed=2)
    ret, said, tx, fsk, ev, flash, lcd = send(sf, drift=drift)
    assert ret > 0, said
    m = re.match(r"fusotx sf=400 frames=82 trk=(\d+) dmax=(-?\d+) big=(\d+) slack=(-?\d+)", line(said, "fusotx sf="))
    assert m and int(m.group(1)) == 20 and int(m.group(2)) < 60 and int(m.group(4)) > 1000, said
    calls = F.decode(air(ev, fsk))
    assert matched(calls, sf) >= 396, (matched(calls, sf), said)


@need_clang
def test_no_recording_no_key_up():
    """no outbox, one whose sum doesn't add up, or a K6 under API level 15: -12 or -1, never keyed"""
    pytest.importorskip("unicorn")
    ret, said, tx, *_, lcd = send(flash={"mem": flash_with(F.OB_LO, b"")})
    assert ret == -12 and "log" not in tx, said
    assert lcd["st"]["stack"] <= STACK, lcd["st"]["stack"]
    bad = bytearray(F.outbox(superframes(20)))
    bad[300] ^= 1
    ret, said, tx, *_ = send(flash={"mem": flash_with(F.OB_LO, bytes(bad))})
    assert ret == -12 and "log" not in tx, said
    ret, said, tx, *_ = send(superframes(20), level=13)
    assert ret == -1 and "log" not in tx, said


@need_clang
def test_from_a_library_slot():
    """fusoslot.c (level 13, the K6 as it is): the frames from slot 15 (tools/k6_fusotx.py deck),
    nothing written to SPI"""
    pytest.importorskip("unicorn")
    sf = superframes(100, seed=3)
    d = F.deck(sf)
    assert len(d) <= F.DECK_DATA
    flash = {"mem": flash_with(0x110000 + 15 * 0x4000 + 16, d)}
    ret, said, tx, fsk, ev, flash, lcd = send(src=SLOT_SRC, level=13, flash=flash)
    assert ret > 0 and flash["progs"] == 0 and flash["erases"] == 0, said
    calls = F.decode(air(ev, fsk))
    c = max(calls, key=len)
    assert [s for s in c[:100]] == [sf[6 * k:6 * k + 6] for k in range(100)]
    assert lcd["st"]["stack"] <= STACK, lcd["st"]["stack"]


def test_the_deck_fills_a_slot():
    sf = superframes(2000)
    d = F.deck(sf)
    nf, nsf = int.from_bytes(d[4:8], "little"), int.from_bytes(d[8:12], "little")
    assert len(d) <= F.DECK_DATA and nf == 124 and nsf == 610
    assert F.planes(sf[:6 * 610]) == d
