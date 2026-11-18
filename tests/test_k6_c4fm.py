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
from test_k6_i2s import ROOT, START, k6_clock, need_clang, rssi_reg, run  # noqa: E402

sys.path.insert(0, ROOT)
from modem import k6c4fm, ysf  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "c4probe.c")
YSF_SRC = os.path.join(ROOT, "c", "k6", "mod", "c4ysf.c")
HDR = os.path.join(ROOT, "c", "k6", "mod", "c4probe_frames.h")
SYM_NS = 1e9 / k6c4fm.BAUD


def define(name, src=SRC):
    """the last #define of name (c4lock.c's C4_DEMO block comes first)"""
    m = re.findall(rf"#define {name} \(?(-?\d+)\)?", open(src).read())
    return int(m[-1])


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
        self.len5d = (k6c4fm.PKT_BYTES - 1) << 8

    def read(self, reg, us):
        if reg == 0x0C:
            return int(self.done is not None and us >= self.done)
        return None

    def write(self, reg, v, us):
        if reg == 0x59:
            if v & 0xC000:
                self.fifo = []
            if v & 0x0800 and not self.ctrl & 0x0800:
                nsym = ((self.len5d >> 8) + 1) * 8
                self.pkts.append({"words": list(self.fifo), "pre": len(self.fifo), "late": [],
                                  "t_en": us})
                self.done = us + (k6c4fm.LEAD_SYMS + nsym) * SYM_NS / 1000 + 300
            self.ctrl = v
        elif reg == 0x5F:
            if self.ctrl & 0x0800:                  # written while it sends: when, which word
                p = self.pkts[-1]
                p["late"].append((us, len(p["words"])))
                p["words"].append(v)
            else:
                self.fifo.append(v)
        elif reg == 0x5D:
            self.len5d = v
        elif reg == 0x02:
            self.done = None

    def key(self, name, us):
        return 0


TRIGGER_US = (40_000_000, 44_000_000)


def bench_rssi(us):
    """the channel as the bench job leaves it: the upload's last 3 s, the uploader's report wait,
    the job's 4 s PSK31 trigger, then quiet"""
    busy = us < 3_000_000 or TRIGGER_US[0] <= us < TRIGGER_US[1]
    return rssi_reg(-65 if busy else -120)


REGS = {0x40: 0x3060, 0x2B: 0xC000, 0x58: 0x3F01, 0x59: 0x10F8, 0x72: 0x4898, 0x3F: 0x3000,
        0x67: bench_rssi}
# what c4probe may write while keyed: the FSK modem, its gain and deviation, the tone oscillator
KEYED_REGS = {0x02, 0x2B, 0x3F, 0x40, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5F, 0x70, 0x71, 0x72}


def send(write_us=94, src=SRC, **kw):
    mmio, rcc, tim = k6_clock()
    tx, fsk = kw.pop("tx", {}), FskTx()
    regs0 = {**REGS, **kw.pop("regs", {})}
    ret, said, regs, _ = run(regs0, src=src, level=kw.pop("level", 3), tx=tx, mmio=mmio,
                             write_us=write_us, fsk=fsk, count=2_000_000_000, **kw)
    assert rcc[0x18] == 0 and not tim.get(0x00, 0) & 1          # TIM17 and its clock put back
    ev = [(ns, r, v) for (r, v, _), ns in zip(tx.get("w", []), tx.get("w_ns", []))]
    return ret, said, regs, tx, fsk, ev


def air(ev, fsk, fs=48000, lag_ns=0.0):
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
        t0 = t0 + lag_ns
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
    m = re.match(r"c4 w=(\d+) late=(\d+) wr=(\d+)-(\d+)", said[2])
    assert m and int(m.group(2)) == 0, said
    assert said[-1].startswith(f"c4 sent n={n} ") and "c4 trigger heard" in said
    names = [x[0] for x in tx["log"]]
    assert names[0] == "tx_on" and names[-1] == "tx_off" and ("tx_pa", 0) == tx["log"][1][:2]
    assert names.count("tx_on") == 1
    t_on, t_off = tx["log"][0][2], tx["log"][-1][2]
    assert t_on >= TRIGGER_US[1] + 9_900_000             # the capture's start after the trigger
    assert (t_off - t_on) / 1000 < tx["log"][0][1] <= 120_000
    assert {r for r, v, us in tx["w"] if t_on <= us <= t_off} <= KEYED_REGS
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


@need_clang
@pytest.mark.parametrize("hz", [146_520_000, 446_000_000, 440_800_000])
def test_only_on_440_850(hz):
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send(rx_hz=hz)
    assert ret == -2 and "log" not in tx


@need_clang
def test_never_on_a_busy_channel_or_without_a_trigger():
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send(regs={0x67: lambda us: rssi_reg(-40)})
    assert ret == -7 and "log" not in tx
    ret, said, regs, tx, fsk, ev = send(regs={0x67: rssi_reg(-120)})     # no trigger: no key-up
    assert ret == -7 and "log" not in tx


@need_clang
def test_needs_level_3():
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send(level=2)
    assert ret == -1 and "log" not in tx


@need_clang
def test_every_way_out_keys_down():
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send(tx={"on_ret": -1})
    assert ret == -3 and [n for n, *_ in tx["log"]] == ["tx_on"]
    t_on = send()[3]["log"][0][2]
    # the core's time limit 1.5 s into the key-up: down within one packet
    dead = t_on + 1_500_000
    ret, said, regs, tx, fsk, ev = send(tx={"dead_us": dead})
    assert ret == -5 and tx["log"][-1][0] == "tx_off" and tx["log"][-1][2] < dead + 250_000
    assert regs[0x70] == 0 and regs[0x40] == REGS[0x40]
    # a key press 1 s in
    t_key = t_on + 1_000_000
    ret, said, regs, tx, fsk, ev = send(keys=((t_key, t_key + 5_000_000),))
    assert ret == -4 and tx["log"][-1][0] == "tx_off" and tx["log"][-1][2] < t_key + 250_000
    assert regs[0x70] == 0 and regs[0x40] == REGS[0x40]


# ---------------------------------------------------------------- c4ysf (run 2)
def test_ysf_module_matches_host():
    assert define("C4_OFF", YSF_SRC) == k6c4fm.YSF_OFF


@need_clang
def test_ysf_module_sends_whole_transmissions():
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send(src=YSF_SRC, write_us=117)
    assert ret == 7, said
    assert said[0] == "c4ysf n=7 g=78/27 off=7 pa=0 rx=440850000"
    m = re.match(r"c4ysf w=(\d+) f=(\d+) late=(\d+)", said[2])
    assert m and int(m.group(3)) == 0, said
    assert int(m.group(2)) == (120 - 64) + (120 - 32)     # B2's and B3's words written while sending
    assert [p["pre"] for p in fsk.pkts] == [30, 30, 30, 30, 120, 64, 32]
    assert all(len(p["words"]) == 30 for p in fsk.pkts[:4])
    assert all(len(p["words"]) == 120 for p in fsk.pkts[4:])
    t_on, t_off = tx["log"][0][2], tx["log"][-1][2]
    assert {r for r, v, us in tx["w"] if t_on <= us <= t_off} <= KEYED_REGS
    for p in fsk.pkts[5:]:
        for us, j in p["late"]:
            # the chip takes word j at payload symbol 16 j (lagging 1.75 symbols): in well before,
            # and never more than pre words in the FIFO
            need = p["t_en"] + (k6c4fm.LEAD_SYMS + 16 * j) * SYM_NS / 1000
            assert us < need - 16 * SYM_NS / 1000, (j, us, need)
            taken = max(0, int((us - p["t_en"]) * 1000 / SYM_NS) - k6c4fm.LEAD_SYMS) // 16
            assert j - taken <= p["pre"]
    lines, res = k6c4fm.analyze(air(ev, fsk, lag_ns=1.75 * SYM_NS), plan="ysf")
    for n in ("A0", "A1", "A2", "A3"):
        assert res[n]["fich"] and res[n]["sym_err"] == 0, "\n".join(lines)
    for n in ("B1", "B2", "B3"):
        assert res[n]["fich_n"] == 4 and res[n]["sym_err"] == 0, "\n".join(lines)
    assert res["plain"]["runs"] == [4, 4, 4], "\n".join(lines)


# ---------------------------------------------------------------- c4lock (run 3)
LOCK_SRC = os.path.join(ROOT, "c", "k6", "mod", "c4lock.c")
FTM_SRC = os.path.join(ROOT, "c", "k6", "mod", "c4ftm.c")
SOTTO_SRC = os.path.join(ROOT, "c", "k6", "mod", "c4sotto.c")
FIFO_SRC = os.path.join(ROOT, "c", "k6", "mod", "c4fifo.c")
LOCK_GAIN = define("C4_GAIN", LOCK_SRC)


class FskGrid(FskTx):
    """the FSK transmitter on its own bit clock, as runs 1 and 2 heard it: one 4800-baud grid
    (ppm against the MCU, a random phase) that runs on through the key-up. A packet starts on the
    first tick at least lat_en after its enable; TX done rises done_sym after its last symbol."""

    def __init__(self, ppm=-520.0, phase=0.37, lat_en=0.3, done_sym=0.6, poll_jitter=0.0):
        super().__init__()
        self.jit, self.rng = poll_jitter, np.random.default_rng(7)
        self.ts = SYM_NS / 1000 * (1 + ppm * 1e-6)          # the chip's symbol, us of MCU time
        self.g0, self.lat_en, self.done_sym = phase * self.ts, lat_en, done_sym

    def write(self, reg, v, us):
        en = reg == 0x59 and v & 0x0800 and not self.ctrl & 0x0800
        super().write(reg, v, us)
        if en:
            p = self.pkts[-1]
            n = int(np.ceil((us + self.lat_en * self.ts - self.g0) / self.ts))
            p["t_start"] = self.g0 + n * self.ts
            nsym = ((self.len5d >> 8) + 1) * 8
            p["nsym"] = nsym
            self.done = p["t_start"] + (k6c4fm.LEAD_SYMS + nsym + self.done_sym) * self.ts
            # the radio's polls: a read is 117 us and the flag shows somewhere inside it
            self.done += self.rng.uniform(0, self.jit) * self.ts


def air_grid(ev, fsk, fs=48000, data_lag=1.21):
    """the discriminator from FskGrid's packets: symbols on the chip's grid (from each packet's
    start, delayed data_lag symbols on the way out), each symbol's size the REG_70 gain in force
    at its middle; sampled on the chip's clock, as the Pluto heard it (4800.0 baud, the MCU off)"""
    g70 = [(ns, v & 0x7F) for ns, r, v in ev if r == 0x70 and v & 0x80]
    t70 = np.array([a for a, _ in g70])
    ts = fsk.ts * 1000
    fs = fs * SYM_NS / ts
    t_end = (fsk.pkts[-1]["t_start"] * 1000 + 1e9) / 1e9
    d = np.zeros(int(t_end * fs))
    for p in fsk.pkts:
        t0 = p["t_start"] * 1000 + data_lag * ts
        b = np.unpackbits(np.array([[w & 0xFF, w >> 8] for w in p["words"]], np.uint8).reshape(-1))
        chip = np.concatenate([np.arange(k6c4fm.LEAD_SYMS) % 2, b[:p["nsym"]]])
        for k, c in enumerate(chip):
            mid = t0 + (k + 0.5) * ts
            g = g70[np.searchsorted(t70, mid) - 1][1]
            a, z = int((t0 + k * ts) * 1e-9 * fs), int((t0 + (k + 1) * ts) * 1e-9 * fs)
            d[a:z] = (2 * int(c) - 1) * g / LOCK_GAIN * 2700.0
    return d



def send_lock(ppm=-520.0, phase=0.37, lat_en=0.3, done_sym=0.6, write_us=117, poll_jitter=0.0,
              src=LOCK_SRC, **kw):
    mmio, rcc, tim = k6_clock()
    tx, fsk = kw.pop("tx", {}), FskFifo(ppm, phase, lat_en, done_sym, poll_jitter)
    ret, said, regs, _ = run({**REGS, **kw.pop("regs", {})}, src=src, level=3, tx=tx,
                             mmio=mmio, write_us=write_us, fsk=fsk, count=2_000_000_000, **kw)
    assert rcc[0x18] == 0 and not tim.get(0x00, 0) & 1
    ev = [(ns, r, v) for (r, v, _), ns in zip(tx.get("w", []), tx.get("w_ns", []))]
    return ret, said, regs, tx, fsk, ev


def test_lock_module_matches_host():
    assert define("NCAL", LOCK_SRC) == k6c4fm.LOCK_NCAL
    assert define("NR", LOCK_SRC) == k6c4fm.LOCK_NR
    assert define("NA", LOCK_SRC) == len(k6c4fm.LOCK_A)
    assert define("A_FIRST", LOCK_SRC) == k6c4fm.LOCK_A[0]
    assert define("C4_LAG4", LOCK_SRC) == k6c4fm.LOCK_LAG4
    src = open(LOCK_SRC).read()
    bq = re.findall(r"BQ\[NB\] = \{([^}]*)\}", src)[-1]
    assert tuple(int(x) for x in bq.split(",")) == k6c4fm.LOCK_B


@need_clang
@pytest.mark.parametrize("ppm,phase,lat_en,jit", [(-520.0, 0.37, 0.3, 0.0), (-760.0, 0.81, 0.9, 0.0),
                                                  (-650.0, 0.05, 0.55, 0.5), (-630.0, 0.6, 0.02, 0.5),
                                                  (-1500.0, 0.2, 0.4, 0.5), (+300.0, 0.7, 0.3, 0.3)])
def test_lock_module_locks_to_the_chip(ppm, phase, lat_en, jit):
    """the chip's clock off the MCU's by ppm (the prior says -630), its grid at any phase, its
    enable threshold anywhere: the fitted rate, every data packet on its predicted tick, and the
    frames whole on air with the chip's gain path (data_lag) set where Lq 0 should land; jit: the
    TX-done polls as coarse as on the radio (run 3's 0.42-symbol residuals). -1500: the K6 after
    11-18 04:24Z, which the fit can't reach from C4_PPM alone: the rate packets find it first"""
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send_lock(ppm, phase, lat_en, poll_jitter=jit)
    n = len(k6c4fm.packets("lock"))
    assert ret == n, said
    assert said[0] == f"c4lock n={n} g=78/27 lag=9 ppm=-630 pa=0 rx=440850000"
    assert said[-1].startswith(f"c4lock sent n={n} ")
    rate = next(s for s in said if s.startswith("c4lock rate"))
    assert rate.startswith("c4lock rate ppm=") and " ok=1111111 " in rate, said
    assert int(re.search(r"ppm=(-?\d+)", rate).group(1)) / 10 == pytest.approx(ppm, abs=40), rate
    rq = [int(v) / 10 for v in next(s for s in said if s.startswith("c4lock rq ")).split()[2:]]
    assert len(rq) == 7 and all(v == pytest.approx(ppm, abs=40) for v in rq), said
    w = next(s for s in said if s.startswith("c4lock w="))
    m = re.match(r"c4lock w=(\d+) late=(\d+) miss=(\d+) lost=(\d+) wr=\d+-\d+ rd=(\d+)$", w)
    assert m and m.group(2) == "0" and m.group(3) == "0" and m.group(4) == "0", said
    fit = next(s for s in said if s.startswith("c4lock fit"))
    got = int(re.search(r"ppm=(-?\d+)", fit).group(1)) / 10
    # the module's ppm is the chip's symbol in MCU cycles: the chip fast = fewer cycles
    assert got == pytest.approx(ppm, abs=3 + 20 * jit), said
    t_on, t_off = tx["log"][0][2], tx["log"][-1][2]
    assert {r for r, v, us in tx["w"] if t_on <= us <= t_off} <= KEYED_REGS | {0x5E}   # 5E: rate packets
    assert [len(p["words"]) for p in fsk.pkts] == ([128] * k6c4fm.LOCK_NR + [4] * k6c4fm.LOCK_NCAL
                                                   + [31] * len(k6c4fm.LOCK_A) + [128, 121] * len(k6c4fm.LOCK_B))
    assert not any(p.get("under") for p in fsk.pkts)
    # the data packets' enables half a symbol from the threshold: start - enable the same for all
    gaps = [(p["t_start"] - p["t_en"]) / fsk.ts for p in fsk.pkts[k6c4fm.LOCK_NR + k6c4fm.LOCK_NCAL:]
            if len(p["words"]) != 128]
    # (coarse polls: the fitted phase wanders about +-0.15 symbol, the noise floor of 16 polls)
    assert max(gaps) - min(gaps) < 0.2 + 0.4 * jit, gaps
    assert min(g - lat_en for g in gaps) > 0.15 and max(g - lat_en for g in gaps) < 0.85, gaps
    # the chip's data path set so that Lq 0 lands: the writes aim C4_LAG4 / 4 symbols after the
    # done grid's ticks, less the mean enable-to-start time (lat_en + 0.5) as runs 1 and 2 saw it
    lag = k6c4fm.LOCK_LAG4 / 4 - 0.5 - lat_en
    lines, res = k6c4fm.analyze(air_grid(ev, fsk, data_lag=lag), plan="lock")
    a0 = k6c4fm.LOCK_A.index(0)
    assert res[f"A{a0:02d}"]["fich"] and res[f"A{a0:02d}"]["sym_err"] == 0, "\n".join(lines)
    b0 = k6c4fm.LOCK_B.index(0)
    assert res[f"B{b0}"]["fich_n"] == 4 and res[f"B{b0}"]["sym_err"] == 0, "\n".join(lines)
    assert abs(res[f"B{b0}"]["land"]) < 0.1, "\n".join(lines)
    assert 4 in res["plain"]["runs"], "\n".join(lines)


@need_clang
def test_lock_module_rules():
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send_lock(write_us=170)
    assert ret == -8 and not fsk.pkts and tx["log"][-1][0] == "tx_off"
    ret, said, regs, tx, fsk, ev = send_lock(rx_hz=446_000_000)
    assert ret == -2 and "log" not in tx
    ret, said, regs, tx, fsk, ev = send_lock(regs={0x67: rssi_reg(-120)})
    assert ret == -7 and "log" not in tx
    t_key = send_lock()[3]["log"][0][2] + 1_500_000
    ret, said, regs, tx, fsk, ev = send_lock(keys=((t_key, t_key + 5_000_000),))
    assert ret == -4 and tx["log"][-1][0] == "tx_off" and tx["log"][-1][2] < t_key + 600_000
    assert regs[0x70] == 0 and regs[0x40] == REGS[0x40] and regs[0x59] == REGS[0x59]


@need_clang
def test_ftm_demo_calls():
    """c4ftm: five whole calls at the lag (DN sets 1, 2, 1, 2, data), plain signs; on air the plain
    receiver finds all five"""
    pytest.importorskip("unicorn")
    ret, said, regs, tx, fsk, ev = send_lock(-604.0, 0.4, 0.3, poll_jitter=0.3, src=FTM_SRC)
    assert ret == len(k6c4fm.packets("demo")), said
    lines, res = k6c4fm.analyze(air_grid(ev, fsk, data_lag=k6c4fm.LOCK_LAG4 / 4 - 0.8), plan="demo")
    for j, inv in enumerate(k6c4fm.DEMO_INV):
        r = res[f"D{j}"]
        assert r["fich_n"] == 4 and (r["pol"] < 0) == bool(inv), "\n".join(lines)
    assert res["plain"]["runs"] == [4] * 5, "\n".join(lines)


@need_clang
def test_sotto_demo_carries_the_clip():
    """c4sotto: DN, the sotto 400 clip in three data calls, DN; on air the plain receiver's data
    blocks, in order, are c4sotto.bit"""
    pytest.importorskip("unicorn")
    from modem import ysf
    ret, said, regs, tx, fsk, ev = send_lock(-604.0, 0.4, 0.3, poll_jitter=0.3, src=SOTTO_SRC)
    assert ret == len(k6c4fm.packets("sotto")), said
    lines, res = k6c4fm.analyze(air_grid(ev, fsk, data_lag=k6c4fm.LOCK_LAG4 / 4 - 0.8), plan="sotto")
    for j in range(len(k6c4fm.SOTTO_SETS)):
        assert res[f"D{j}"]["fich_n"] == 4, "\n".join(lines)
    data = b"".join(f["dch"][0] + f["dch"][1] for f in res["plain"]["list"]
                    if f["fich"] is not None and f["fich"].dt == ysf.DT_DATA_FR and f["fich"].fi == ysf.FI_COMMS)
    assert data == open(k6c4fm.SOTTO_BIT, "rb").read()


# ---------------------------------------------------------------- c4fifo (the timing probe)
class FskFifo(FskGrid):
    """FskGrid with the FIFO's level: word j leaves at its first symbol (the packet's start, the
    lead, then 16 symbols a word); the almost-empty flag (REG_3F bit 14) is up while the level is
    at or under REG_5E<9:3>, live; TX done latches until a REG_02 write after it; a REG_02 read
    gives both bits"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.irq_en, self.thr, self.acked = 0, 64, False

    def _ae(self, us):
        if not self.pkts or not self.irq_en & 0x4000 or not self.ctrl & 0x0800 or "t_start" not in self.pkts[-1]:
            return False
        p = self.pkts[-1]
        if self.done is not None and us >= self.done:
            return False
        k = (us - p["t_start"]) / self.ts - k6c4fm.LEAD_SYMS
        gone = 0 if k < 0 else min(len(p["words"]), int(k // 16) + 1)
        return len(p["words"]) - gone <= self.thr

    def _done(self, us):
        return self.done is not None and us >= self.done

    def read(self, reg, us):
        if reg == 0x0C:
            return int((self._done(us) and not self.acked) or self._ae(us))
        if reg == 0x02:
            return (0x8000 if self._done(us) else 0) | (0x4000 if self._ae(us) else 0)
        return None

    def write(self, reg, v, us):
        if reg == 0x02:
            if self._done(us):
                self.acked = True
            return
        if reg == 0x3F:
            self.irq_en = v
        elif reg == 0x5E:
            self.thr = (v >> 3) & 0x7F
        elif reg == 0x5F and self.ctrl & 0x0800:
            p = self.pkts[-1]
            due = p["t_start"] + (k6c4fm.LEAD_SYMS + 16 * len(p["words"])) * self.ts
            if us > due:
                p.setdefault("under", []).append(len(p["words"]))
        if reg == 0x59 and v & 0x0800 and not self.ctrl & 0x0800:
            self.acked = False
        super().write(reg, v, us)


@need_clang
@pytest.mark.parametrize("ppm", [-851.0, -524.0])
def test_fifo_probe_measures_the_rate(ppm):
    """c4fifo: two 128-word packets, the FIFO refilled a word on each almost-empty flag (no
    underrun), 108 marks each 16 symbols apart; the host fit gives the chip's rate, a straight line"""
    pytest.importorskip("unicorn")
    mmio, rcc, tim = k6_clock()
    tx, fsk = {}, FskFifo(ppm, 0.4, 0.3, 0.6, 0.0)
    ret, said, regs, _ = run(REGS, src=FIFO_SRC, level=3, tx=tx, mmio=mmio, write_us=117, fsk=fsk,
                             count=2_000_000_000)
    assert rcc[0x18] == 0 and not tim.get(0x00, 0) & 1
    assert ret == 2, said
    assert [len(p["words"]) for p in fsk.pkts] == [128] * 2
    assert not any(p.get("under") for p in fsk.pkts), [p.get("under") for p in fsk.pkts]
    t_on, t_off = tx["log"][0][2], tx["log"][-1][2]
    assert {r for r, v, us in tx["w"] if t_on <= us <= t_off} <= KEYED_REGS | {0x5E}
    assert (t_off - t_on) / 1000 < tx["log"][0][1] <= 120_000
    assert regs[0x5E] == REGS.get(0x5E, 0) and regs[0x3F] == REGS[0x3F]
    pk = k6c4fm.fifo(said)
    assert sorted(pk) == [0, 1], said
    for p in pk.values():
        assert p["n"] == 108 and p["bad"] == 0 and p["r"] == 0 and p["b"] == 0xC000, p
        assert len(p["d"]) == 108
        # the polls bracket each flag within one read (117 us, 0.56 symbol)
        assert p["ppm"] == pytest.approx(ppm, abs=60), p
        assert p["res"] < 40, p
    lines = k6c4fm.fifo_text(pk)
    assert len(lines) == 3 and lines[-1].startswith("packet rates over time: 0.0 s")
