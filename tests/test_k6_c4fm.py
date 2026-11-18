"""C4FM (YSF) and DMR-shaped 4FSK from the K6's own FSK modem (c/k6/mod/c4probe.c,
modem/k6c4fm.py, docs/k6-c4fm.md): the frames and tables, the analysis on the air model, and the
module on an emulated Cortex-M0 against a model of the FD6818's FSK transmitter, its REG_70 size
writes turned back into the air a Pluto would hear and decoded. SPDX-License-Identifier: MIT"""
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, k6_clock, need_clang, run  # noqa: E402

sys.path.insert(0, ROOT)
from modem import k6c4fm, ysf  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "c4probe.c")
HDR = os.path.join(ROOT, "c", "k6", "mod", "c4probe_frames.h")
SYM_NS = 1e9 / k6c4fm.BAUD


def define(name):
    m = re.search(rf"#define {name} \(?(-?\d+)\)?", open(SRC).read())
    return int(m.group(1))


# ---------------------------------------------------------------- host side
def test_frames_decode_and_split():
    fr = k6c4fm.ysf_frames()
    assert len(fr) == 4
    for i, f in enumerate(fr):
        d = ysf.decode_frame(1.0 - 2.0 * f)
        assert d["fich"] is not None
        assert d["fich"].fi == (0, 1, 1, 2)[i]
        c, s = k6c4fm.split(f)
        # back to dibits: chip bit 1 = +, so msb = 1 - chip; lsb = size
        assert np.array_equal(np.stack([1 - c, s], 1).reshape(-1), f)
    assert b"AG7EW K6 C4FM" in ysf.decode_frame(1.0 - 2.0 * fr[2])["dch"][1]


def test_dmr_sync_signs():
    # DFF57D75DF5D: D = 11 01 -> msbs 1 0
    assert list(k6c4fm.dmr_sync_signs()[:4]) == [1, 0, 1, 1]
    d = k6c4fm.dmr_dibits().reshape(-1, 2)
    assert np.all(d[:, 1] == 1)                       # outer only: the chip alone can send it


def test_header_is_current(tmp_path):
    out = tmp_path / "h.h"
    k6c4fm.header(str(out))
    assert out.read_text() == open(HDR).read(), "python3 -m modem.k6c4fm header c/k6/mod/c4probe_frames.h"


def test_module_offsets_match():
    assert define("C4_OFF_FIRST") == k6c4fm.OFFSETS[0]
    assert define("C4_NOFF") == len(k6c4fm.OFFSETS)
    assert define("C4_GAIN") == k6c4fm.GAIN and define("C4_INNER") == k6c4fm.INNER


def test_sim_finds_the_timing():
    lines, res = k6c4fm.analyze(k6c4fm.simulate(0.0, noise_hz=100.0))
    good = [n for n, r in res.items() if r.get("fich")]
    assert "Y08" in good and len(good) >= 3, lines
    assert abs(np.mean(res["P1"]["land"])) < 0.3
    assert res["P5"]["dmr_syncs"] == [54, 198, 342]
    assert res["P1"]["shares"][3] == pytest.approx(1 / 3, abs=0.05)


def test_sim_late_chip():
    # the chip's symbols 2.4 symbols behind the writes: later packets decode
    lines, res = k6c4fm.analyze(k6c4fm.simulate(-500.0, noise_hz=100.0))
    good = [int(n[1:]) for n, r in res.items() if r.get("fich")]
    assert good and 16 <= np.median(good) <= 20, lines


def test_sim_latched():
    lines, res = k6c4fm.analyze(k6c4fm.simulate(0.0, latched=True))
    assert not any(r.get("fich") for r in res.values())
    assert any("LATCHED" in ln for ln in lines)


# ---------------------------------------------------------------- the module, emulated
class FskTx:
    """the FD6818's FSK transmitter as c4probe drives it: REG_59 clears and enables, REG_5F fills
    the FIFO, TX done (REG_0C bit 0) a lead and payload after the enable, REG_02 write clears"""

    def __init__(self):
        self.fifo, self.pkts, self.done, self.ctrl = [], [], None, 0

    def read(self, reg, us):
        if reg == 0x0C:
            return int(self.done is not None and us >= self.done)
        return None

    def write(self, reg, v, us):
        if reg == 0x59:
            if v & 0xC000:
                self.fifo = []
            if v & 0x0800 and not self.ctrl & 0x0800:
                self.pkts.append({"words": list(self.fifo)})
                self.done = us + (k6c4fm.LEAD_SYMS + k6c4fm.PKT_SYMS) * SYM_NS / 1000 + 300
            self.ctrl = v
        elif reg == 0x5F:
            self.fifo.append(v)
        elif reg == 0x02:
            self.done = None

    def key(self, name, us):
        return 0


REGS = {0x40: 0x3060, 0x2B: 0xC000, 0x58: 0x3F01, 0x59: 0x10F8, 0x72: 0x4898, 0x3F: 0x3000,
        0x67: 0x00}


def send(write_us=94):
    mmio, rcc, tim = k6_clock()
    tx, fsk = {}, FskTx()
    ret, said, regs, _ = run(dict(REGS), src=SRC, level=3, tx=tx, mmio=mmio, write_us=write_us,
                             fsk=fsk, count=2_000_000_000)
    assert rcc[0x18] == 0 and not tim.get(0x00, 0) & 1          # TIM17 and its clock put back
    ev = [(ns, r, v) for (r, v, _), ns in zip(tx["w"], tx["w_ns"])]
    return ret, said, regs, tx, fsk, ev


def air(ev, fsk, fs=48000):
    """the discriminator (in units of the outer level x 2700 Hz) the writes make: each packet's
    symbols from its FIFO words at 4800 baud from its enable, each symbol's size from REG_70's
    gain in force at its middle (REG_40 likewise for P2), the chip's latency 0"""
    en = [ns for ns, r, v in ev if r == 0x59 and v & 0x0800]
    g70 = [(ns, v & 0x7F) for ns, r, v in ev if r == 0x70 and v & 0x80]
    g40 = [(ns, v & 0xFF) for ns, r, v in ev if r == 0x40]
    assert len(en) == len(fsk.pkts)
    t_end = (en[-1] + 1e9) / 1e9
    d = np.zeros(int(t_end * fs))
    t70 = np.array([a for a, _ in g70])
    t40 = np.array([a for a, _ in g40]) if g40 else np.array([0.0])
    v40 = np.array([b for _, b in g40]) if g40 else np.array([0x60])
    r40 = REGS[0x40] & 0xFF
    for t0, p in zip(en, fsk.pkts):
        b = np.unpackbits(np.array([[w & 0xFF, w >> 8] for w in p["words"]], np.uint8).reshape(-1))
        chip = np.concatenate([np.arange(k6c4fm.LEAD_SYMS) % 2, b])
        for k, c in enumerate(chip):
            mid = t0 + (k + 0.5) * SYM_NS
            g = g70[np.searchsorted(t70, mid) - 1][1]
            i40 = np.searchsorted(t40, mid) - 1
            dv = (v40[i40] if i40 >= 0 else r40) / r40
            a, z = int((t0 + k * SYM_NS) * 1e-9 * fs), int((t0 + (k + 1) * SYM_NS) * 1e-9 * fs)
            d[a:z] = (2 * int(c) - 1) * g / k6c4fm.GAIN * dv * 2700.0
    return d


@need_clang
def test_module_sends_and_decodes():
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send()
    n = 6 + len(k6c4fm.OFFSETS)
    assert ret == n, said
    assert said[0] == "c4 n=38 g=96/32 pa=0 rx=440850000"
    m = re.match(r"c4 w=(\d+) late=(\d+) wr=(\d+)-(\d+)", said[1])
    assert m and int(m.group(2)) == 0, said
    assert said[-1].startswith(f"c4 sent n={n} ")
    names = [x[0] for x in tx["log"]]
    assert names[0] == "tx_on" and names[-1] == "tx_off" and ("tx_pa", 0) == tx["log"][1][:2]
    assert len(fsk.pkts) == n and all(len(p["words"]) == 30 for p in fsk.pkts)
    # the 4800-baud set-up, and every register put back
    w = {r: v for _, r, v in ev}
    assert any(r == 0x72 and v == 0xC195 for _, r, v in ev)
    for r in (0x40, 0x2B, 0x58, 0x72, 0x3F):
        assert regs[r] == REGS[r], hex(r)
    assert regs[0x59] == REGS[0x59] and w[0x70] == 0
    # the ID: tone 1 on at gain 60, after the last packet
    last_en = max(ns for ns, r, v in ev if r == 0x59 and v & 0x0800)
    assert sum(1 for ns, r, v in ev if r == 0x70 and v == 0x8000 | 60 << 8 and ns > last_en) == 14   # A G 7 E W elements
    # the size writes land on the symbol edges (YSF packet 8 = offset 0)
    en = [ns for ns, r, v in ev if r == 0x59 and v & 0x0800]
    t0 = en[6 + 8]
    lands = [ns for ns, r, v in ev if r == 0x70 and v & 0x80 and t0 < ns < t0 + 140e6]
    k = (np.array(lands[:-1]) - t0) / SYM_NS - k6c4fm.LEAD_SYMS
    assert np.max(np.abs(k - np.round(k))) < 0.02, k
    lines, res = k6c4fm.analyze(air(ev, fsk))
    good = [nm for nm, r in res.items() if r.get("fich")]
    assert "Y08" in good, "\n".join(lines)
    assert res["Y08"]["sym_err"] == 0
    assert res["P1"]["shares"][3] == pytest.approx(1 / 3, abs=0.03)
    assert res["P2"]["shares"][3] == pytest.approx(1 / 3, abs=0.03)
    assert res["P5"]["dmr_syncs"] == [54, 198, 342]


@need_clang
def test_module_refuses_slow_writes():
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send(write_us=170)
    assert ret == -8, said
    assert not fsk.pkts
    assert tx["log"][-1][0] == "tx_off"
    assert regs[0x40] == REGS[0x40]
