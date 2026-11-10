"""The DMA sharing probe (c/k6/mod/dmashare.c) on an emulated Cortex-M0: it runs channel 5 as
the pager's playback does but with no output, counts its transfers with channel 1 off, high and
low, notices a channel 5 that a busy channel 1 starves, refuses while something plays, and puts
every register back. SPDX-License-Identifier: MIT"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, need_clang, run  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "dmashare.c")
STEP_US = 20.0            # the time one TIM17 read moves the model on


def bench(starve, tim1_cr1=0, ccr5=0):
    """RCC, TIM17 (1 MHz), TIM1 and DMA channels 1 and 5: channel 1 moves 5.5 bytes a us while
    on; channel 5 one sample every 125 us while TIM1 counts with UDE, unless starve(pl) says
    channel 1 at that priority starves it. Time = the API's time plus STEP_US a TIM17 read"""
    rcc = {0x14: 0x14, 0x18: 0}
    t17, t1 = {}, {0x00: tim1_cr1}
    dma = {0x08 + 20 * 4: ccr5}
    m = {"reads": 0, "on1": None, "on5": None, "n5": 0, "t5": 0.0, "log": []}

    def now(st):
        return st["us"] + m["reads"] * STEP_US

    def rcc_r(uc, off, size, st):
        return rcc.get(off, 0)

    def rcc_w(uc, off, size, value, st):
        rcc[off] = value

    def t17_r(uc, off, size, st):
        if off == 0x24:
            m["reads"] += 1
            return int(now(st)) & 0xFFFF if t17.get(0, 0) & 1 else 0
        return t17.get(off, 0)

    def t17_w(uc, off, size, value, st):
        assert rcc[0x18] & (1 << 18), "TIM17 written with its clock off"
        t17[off] = value

    def t1_r(uc, off, size, st):
        return t1.get(off, 0)

    def t1_w(uc, off, size, value, st):
        assert rcc[0x18] & (1 << 11), "TIM1 written with its clock off"
        assert off not in (0x20, 0x44), f"TIM1 output stage written ({off:#x})"
        t1[off] = value

    def ch5_moves(st):
        t = now(st)
        running = dma.get(0x58, 0) & 1 and t1.get(0, 0) & 1 and t1.get(0x0C, 0) & 0x100
        ccr1 = dma.get(0x08, 0)
        starved = ccr1 & 1 and starve((ccr1 >> 12) & 3)
        if running and not starved:
            m["n5"] += int((t - m["t5"]) // 125)
        m["t5"] = t - (t - m["t5"]) % 125 if running and not starved else t

    def dma_r(uc, off, size, st):
        if not rcc[0x14] & 1:
            return 0x37
        if off == 0x0C and dma.get(0x08, 0) & 1:
            n = int((now(st) - m["on1"]) * 5.5)
            return 1024 - n % 1024
        if off == 0x5C and dma.get(0x58, 0) & 1:
            ch5_moves(st)
            return 640 - m["n5"] % 640
        return dma.get(off, 0)

    def dma_w(uc, off, size, value, st):
        assert rcc[0x14] & 1, "DMA written with its clock off"
        if off == 0x04:
            return
        if off in (0x08, 0x58):
            ch5_moves(st)
        dma[off] = value
        if off == 0x08 and value & 1:
            assert dma[0x10] == 0x48000410 and dma[0x0C] == 1024
            assert value & 0xCFFE == 1 << 7 | 1 << 5, hex(value)
            m["on1"] = now(st)
            m["log"].append((value >> 12) & 3)
        if off == 0x58 and value & 1:
            assert dma[0x60] == 0x40012C4C and dma[0x5C] == 640, dma
            assert value == 0x25B1, hex(value)
            m["t5"] = now(st)
    mmio = [(0x40021000, 0x400, rcc_r, rcc_w), (0x40014800, 0x400, t17_r, t17_w),
            (0x40012C00, 0x400, t1_r, t1_w), (0x40020000, 0x400, dma_r, dma_w)]
    return mmio, rcc, t17, t1, dma, m


def small(tmp_path):
    w = tmp_path / "dmashare_small.c"
    w.write_text(f'#define N_CALLS 10u\n#define N_FD 10u\n#define N_RAM 1000u\n#include "{SRC}"\n')
    return str(w)


def lines(said, tag):
    return [dict(kv.split("=") for kv in ln.split()[2:]) for ln in said if ln.startswith(tag + " ")]


@need_clang
def test_counts_channel_5_with_channel_1_off_high_and_low(tmp_path):
    mmio, rcc, t17, t1, dma, m = bench(starve=lambda pl: False)
    ret, said, regs, writes = run(START, src=small(tmp_path), spare=4000, mmio=mmio, count=400_000_000)
    assert said[0].startswith("dmash start ccr5=0000 cr1=0000 room="), said
    assert said[-1] == "dmash done n5=2000/2000/2000", said
    assert ret == 2000 << 16 | 2000
    w = lines(said, "w")
    assert [ln.split()[2] for ln in said if ln.startswith("w ")] == ["p=-", "p=3", "p=0"] * 2
    for i, x in enumerate(w):
        assert 1999 <= int(x["n5"]) <= 2001 and 125 <= int(x["gap"]) <= 145, x
        assert (int(x["laps"]) == 0) == (i % 3 == 0), x
        if i % 3:
            assert 1300 <= int(x["laps"]) <= 1360, x
    u = lines(said, "u")
    assert len(u) == 6 and all(40000 <= int(x["calls"]) <= 40400 for x in u), u
    assert m["log"] == [3, 0, 3, 0]
    assert not writes                             # no radio register written
    assert rcc == {0x14: 0x14, 0x18: 0}           # clocks as they were
    assert t1[0x00] == 0 and t1[0x0C] == 0 and dma[0x58] == 0 and dma[0x08] == 0
    assert t17[0x00] == 0 and t17[0x0C] == 0 and t17[0x28] == 0


@need_clang
def test_sees_a_starved_channel_5(tmp_path):
    mmio, rcc, t17, t1, dma, m = bench(starve=lambda pl: pl == 3)
    ret, said, regs, writes = run(START, src=small(tmp_path), spare=4000, mmio=mmio, count=400_000_000)
    w = lines(said, "w")
    assert int(w[1]["n5"]) == 0 and int(w[1]["gap"]) >= 249_000, w[1]
    assert 1999 <= int(w[2]["n5"]) <= 2001, w[2]
    assert said[-1] == "dmash done n5=0/2000/2000" and ret == 2000


@need_clang
def test_refuses_while_something_plays(tmp_path):
    for cr1, ccr5 in ((1, 0), (0, 0x25B1)):
        mmio, rcc, t17, t1, dma, m = bench(starve=lambda pl: False, tim1_cr1=cr1, ccr5=ccr5)
        rcc[0x18] = 1 << 11
        ret, said, regs, writes = run(START, src=small(tmp_path), spare=4000, mmio=mmio)
        assert ret == -9 and len(said) == 1, said
        assert rcc == {0x14: 0x14, 0x18: 1 << 11} and m["log"] == []


@need_clang
def test_wants_its_scratch(tmp_path):
    mmio, rcc, t17, t1, dma, m = bench(starve=lambda pl: False)
    ret, said, regs, writes = run(START, src=small(tmp_path), spare=2000, mmio=mmio)
    assert ret == -8 and said == []
