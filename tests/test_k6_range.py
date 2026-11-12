"""PHY R, the K6 range mode (docs/k6-range.md): the Python sender and receivers (modem/k6r.py), the
portable C sender the UV-380 would use (c/common/range_tx.c, needs make -C c/common), and the K6
module (c/k6/mod/rangerx.c) on an emulated M0 with a model of the chip's plain FSK receiver
(tests/test_k6_pocsag.py's plain_chip), fed the signs of a modelled discriminator
(sim/k6if/range.py). SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import k6g, k6r  # noqa: E402

EXE = os.path.join(ROOT, "c", "common", "range_tx_host")
SRC = os.path.join(ROOT, "c", "k6", "mod", "rangerx.c")
need_clang = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                reason="needs clang and ld.lld")
need_host = pytest.mark.skipif(not os.path.exists(EXE), reason="needs make -C c/common")


def test_line_code():
    """the burst's layout, the precoding and the interleaver"""
    pls = k6r.test_payloads(3)
    assert all(k6r.crc_ok(p) for p in pls)
    a = k6r.burst_bits(pls)
    assert len(a) == k6r.LEAD_BITS + 3 * (32 + k6r.CODED) + 2 * k6r.GAP_BITS + k6r.TAIL_BITS
    b = k6r.tone_bits(a)
    assert np.array_equal(b[1:], a[1:] ^ a[:-1])
    # every lead and gap: tone bits 1 1 1 1 1 0 0 0, ending on the pattern's end
    assert list(b[k6r.LEAD_BITS - 8:k6r.LEAD_BITS]) == list(k6r.LEAD_TONES)
    g = k6r.LEAD_BITS + 32 + k6r.CODED
    assert list(b[g + k6r.GAP_BITS - 8:g + k6r.GAP_BITS]) == list(k6r.LEAD_TONES)
    c = np.arange(k6r.CODED)
    assert np.array_equal(k6r.deinterleave(k6r.interleave(c)), c)
    i = np.arange(k6r.CODED)
    assert np.array_equal(k6r.interleave(c), 86 * (i % 24) + i // 24)
    # 1200 Hz is a whole cycle a bit, 600 Hz half of one: the sign flips at every edge
    x = k6r.audio(a[:200], 48000, 1.0)
    edges = x[40::40][:198]
    assert np.all(np.abs(edges) < 0.2)


@pytest.mark.parametrize("rate", [4800.0, 4716.0, 4880.0])
def test_receivers_on_a_clean_link(rate):
    """flat model, no noise: the integer receiver reads every packet whether the chip's sampler
    runs at 4 samples a bit or 1.75% off either way (the POCSAG bench's 3.93); the ideal receiver
    at 4800"""
    from sim.k6if import range as rg
    pls = k6r.test_payloads(3, seed=4)
    smp, _ = rg.link(pls, None, rate=rate, tx="flat")
    got, sl = k6r.receive_samples(smp)
    assert [c for _, c in got] == [True] * 3 and [p for p, _ in got] == pls
    assert abs(sl.t / 256 - rate / 1200) < 0.03
    if rate == 4800.0:
        assert [p for p, c in k6r.receive(smp, rg.best_phase(smp)) if c] == pls


def test_weak_signal_on_the_model():
    """the IC-7100 model at 46 dB-Hz: every packet; at 38 none (the edge is near 42)"""
    from sim.k6if import range as rg
    pls = k6r.test_payloads(4, seed=5)
    smp, _ = rg.link(pls, 46, seed=6)
    got, _ = k6r.receive_samples(smp)
    assert sum(1 for p, c in got if c and p in pls) == 4
    smp, _ = rg.link(pls, 38, seed=6)
    got, _ = k6r.receive_samples(smp)
    assert sum(1 for p, c in got if c) == 0


def test_decoder_matches_phy_g():
    """the code and decoder are PHY G's: a packet's coded bits decode back, and 4% of them
    wrong (hard decisions) still decode"""
    p = k6r.test_payloads(1, seed=7)[0]
    c = k6r.interleave(k6r.coded_bits(p))
    y = 4 * (1 - 2 * c.astype(int))
    assert k6r.decode_soft(y) == p
    rng = np.random.default_rng(1)
    flip = rng.random(len(y)) < 0.04
    assert k6r.decode_soft(np.where(flip, -y, y) // 2) == p
    assert np.array_equal(k6r.coded_bits(p), k6g.conv_encode(np.concatenate(
        [np.unpackbits(np.frombuffer(p, np.uint8)), np.zeros(8, np.int8)])))


def test_bench_wav_reads():
    """the IC-7100's wav: whole bursts, each read back through the flat model"""
    from sim.k6if import range as rg
    y, times = k6r.bench_wav(26.0, npk=2, level=k6r.LEVEL)
    assert len(times) == 5 and all(t1 - t0 == pytest.approx(k6r.burst_seconds(2), abs=1e-3)
                                   for t0, t1, _ in times)
    t0, t1, _ = times[1]
    d = rg.chip_audio(y[int((t0 - 0.2) * k6r.FS):int((t1 + 0.2) * k6r.FS)], None, tx="flat")
    got, _ = k6r.receive_samples(rg.sample(d))
    assert [p for p, c in got if c] == k6r.test_payloads(2, seed=101)


def host(mode, pls, *opts):
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "p.bin"), os.path.join(d, "out")
        with open(src, "wb") as f:
            f.write(b"".join(pls))
        subprocess.run([EXE, mode, src, out, *map(str, opts)], check=True)
        with open(out, "rb") as f:
            return f.read()


@need_host
def test_c_sender_tone_bits_match():
    """the UV-380's sender sends the very tone bits modem.k6r does"""
    for n in (1, 3):
        pls = k6r.test_payloads(n, seed=8)
        got = np.frombuffer(host("tones", pls), np.uint8)
        assert np.array_equal(got, k6r.tone_bits(k6r.burst_bits(pls)))


@need_host
def test_c_sender_audio_decodes():
    """its FFSK audio (ffsk_tx at 600/1200 Hz, 1200 baud) through the model reads back"""
    from sim.k6if import range as rg
    pls = k6r.test_payloads(2, seed=9)
    x = np.frombuffer(host("audio", pls, "--fs", 48000, "--level", 16384), np.int16) / 32768.0
    z = np.zeros(int(0.2 * rg.FSA))
    d = rg.chip_audio(np.concatenate([z, x * (k6r.LEVEL / 0.5), z]), None, tx="flat")
    got, _ = k6r.receive_samples(rg.sample(d))
    assert [p for p, c in got if c] == pls


def run_module(smp, rate, total, scratch=4096):
    import k6_live_emu as le
    from test_k6_pocsag import plain_chip
    code, entry = le.build("x", [("RG_S", f"{int(total) + 1}u")], SRC)
    Chip = plain_chip(le, [(0.3, rate, smp)])
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, 5.49e6, 1.5, max_s=total + 4, level=8,
             scratch=scratch)
    res = e.run()
    assert res is not None, e.said[-5:]
    return e, res, code


@need_clang
@pytest.mark.parametrize("rate", [4800.0, 4716.0, 4880.0])
def test_rangerx_on_the_model(rate):
    """the module reads every packet of a clean burst at each sampler rate, says a line for each
    with its seq, CRC right and no channel errors, re-arms the chip for each, and puts the
    registers back"""
    pytest.importorskip("unicorn")
    from sim.k6if import range as rg
    pls = k6r.test_payloads(4, seed=10)
    smp, _ = rg.link(pls, None, rate=rate, tx="flat")
    e, (ret, stack), code = run_module(smp, rate, 0.3 + len(smp) / rate + 1)
    assert len(code) < 4600, len(code)
    assert stack < 600, stack
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    _, pk, ends = k6r.parse_log(["K6 MOD say " + s for s in e.said])
    assert ret == 4 and [p["ok"] for p in pk] == ["1"] * 4, e.said
    assert [p["s"] for p in pk] == ["0", "1", "2", "3"]
    # (off rate a window's end now and then takes a sample from the far side of a bit's middle,
    # and a window that sums to 0 counts as wrong: up to 7% on a clean link at 3.93 a bit)
    assert all(int(p["e"]) <= (2 if rate == 4800.0 else 160) for p in pk), pk
    assert ends and ends[-1]["c"] == "4" and ends[-1]["z"] == "0", ends
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F):
        assert e.writes[reg][-1] == 0, hex(reg)


@need_clang
def test_rangerx_near_the_edge():
    """at 44 dB-Hz on the flat model the module reads at least 5 of 6 packets (the Python
    receiver, which never re-arms, the same or one more), and says the channel's errors"""
    pytest.importorskip("unicorn")
    from sim.k6if import range as rg
    pls = k6r.test_payloads(6, seed=11)
    smp, _ = rg.link(pls, 44, seed=12, tx="flat")
    got, _ = k6r.receive_samples(smp)
    py = sum(1 for p, c in got if c and p in pls)
    e, (ret, _), _ = run_module(smp, 4800.0, 0.3 + len(smp) / 4800 + 1)
    _, pk, _ = k6r.parse_log(["K6 MOD say " + s for s in e.said])
    assert ret >= 5 and ret >= py - 1, (ret, py, e.said)
    assert all(50 < int(p["e"]) < 400 for p in pk if p["ok"] == "1"), pk


@need_clang
@pytest.mark.parametrize("start", [1 << 13, 0])
def test_rangerx_b_ladder(start):
    """the bench build's ladder, shortened: nine settings, each with its s and v lines, the
    front end's two pins never both high and as found at the end, REG_13 and REG_7E fixed only
    while a setting fixes the gain and put back; the burst's packets read and counted in their settings. A board
    whose pins are not one of the two paths is left alone (-5, nothing written)"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from sim.k6if import range as rg
    from test_k6_pocsag import plain_chip
    UHF, VHF, ODR, BSRR, BRR = 1 << 13, 1 << 14, 0x48000014, 0x48000018, 0x48000028
    pls = k6r.test_payloads(2, seed=13)
    smp, _ = rg.link(pls, None, tx="flat")
    code, entry = le.build("x", [("WAIT_MS", "0u"), ("LADDER_S", "1u"), ("SLOT_SCALE", "400u")],
                           os.path.join(ROOT, "c", "k6", "mod", "rangerx_b.c"))
    assert len(code) < 5200, len(code)
    Chip = plain_chip(le, [(0.3, 4800.0, smp)])

    class Board(Chip):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.reg[ODR] = start | 0x0008
            self.odrs = []

        def mmio_w(self, uc, off, size, value, page):
            a = page + off
            if a in (BSRR, BRR):
                assert value & ~(UHF | VHF) == 0, hex(value)
                o = self.reg[ODR]
                self.reg[ODR] = o | value if a == BSRR else o & ~value
                assert self.reg[ODR] & (UHF | VHF) != UHF | VHF
                self.odrs.append(self.reg[ODR] & (UHF | VHF))
                return
            assert a != ODR
            super().mmio_w(uc, off, size, value, page)

    total = 0.3 + len(smp) / 4800 + 9 * 0.4 + 2
    e = Board(code, entry, np.zeros(16, np.uint8), 0, 5.49e6, 1.5, max_s=total, level=8, scratch=4096)
    res = e.run()
    assert res is not None, e.said[-5:]
    ret, stack = res
    assert stack < 600, stack
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    if not start:
        assert ret == -5 & 0xFFFFFFFF or ret == -5, ret
        assert e.odrs == [] and not any(e.writes.get(r) for r in (0x13, 0x7E, 0x58)), e.said
        return
    s = [ln for ln in e.said if ln.startswith("s ")]
    v = [ln for ln in e.said if ln.startswith("v ")]
    assert len(s) == 9 and len(v) == 9, e.said
    assert [ln.split()[3] for ln in s] == ["p=0", "p=1", "p=2", "p=2", "p=2", "p=2", "p=2", "p=2", "p=0"]
    assert set(e.odrs) <= {0, UHF, VHF} and VHF in e.odrs and 0 in e.odrs
    assert e.reg[ODR] == UHF | 0x0008
    assert [e.writes[0x13][i] for i in range(-7, -2)] == [0x021F, 0x011F, 0x001F, 0x0007, 0x0004]
    assert e.writes[0x13][-1] == 0 and e.writes[0x7E][-1] == 0
    nv = [dict(kv.split("=") for kv in ln.split()[2:4]) for ln in v]
    assert ret == 2 and sum(int(d["n"]) for d in nv) == 2 and sum(int(d["ok"]) for d in nv) == 2, e.said
    rep = k6r.report(["K6 MOD say " + ln for ln in e.said])
    rows = [ln for ln in rep if ln.startswith("setting ")]
    assert len(rows) == 9 and sum(int(ln.split(": ")[1].split()[2]) for ln in rows) == 2, rep
