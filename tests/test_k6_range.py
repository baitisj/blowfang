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


@pytest.mark.parametrize("rate,lock", [(4800.0, None), (4716.0, None), (4880.0, None), (4716.0, 1.0),
                                       (4880.0, 0.5)])
def test_receivers_on_a_clean_link(rate, lock):
    """flat model, no noise: the integer receiver reads every packet whether the chip's sampler
    runs at 4 samples a bit or 1.75% off either way (the POCSAG bench's 3.93), on a steady clock
    or one locked to the edges as the chip's seems to be; the ideal receiver at 4800"""
    from sim.k6if import range as rg
    pls = k6r.test_payloads(3, seed=4)
    smp, _ = rg.link(pls, None, rate=rate, tx="flat", lock=lock)
    got, sl = k6r.receive_samples(smp)
    assert [c for _, c in got] == [True] * 3 and [p for p, _ in got] == pls
    if rate == 4800.0 and lock is None:
        assert [p for p, c in k6r.receive(smp, rg.best_phase(smp)) if c] == pls


def capture(n):
    with open(os.path.join(ROOT, "tests", "data", "k6", f"k6r_run2_c{n}.txt")) as f:
        w = [int(x, 16) for ln in f if not ln.startswith("#") for x in ln.split()]
    return np.array([x >> (15 - i) & 1 for x in w for i in range(16)], np.uint8)


@pytest.mark.parametrize("n", [27, 29])
def test_bench_captures(n):
    """two captures of bench run 2 (the chip's own samples, a pre-emphasised burst, the K6's
    de-emphasis off): the segment decoder reads each packet with no line bit wrong, though the
    sampler slipped (runs of 3 among the 2s and 4s), and the receiver checks its CRC"""
    smp = capture(n)
    runs = np.diff(np.flatnonzero(np.diff(smp.astype(int))))
    assert np.sum(runs == 3) > 100 and np.sum(runs == 1) == 0
    got, sl = k6r.receive_samples(smp, armed=True)
    assert len(got) == 1 and got[0][1] and got[0][0] in k6r.bench_payloads()
    y, _ = k6r.slice_samples(smp)
    cor = np.correlate(y, 1 - 2 * k6r.SYNC_BITS.astype(int), "valid")
    i = int(np.argmax(np.abs(cor)))
    assert abs(cor[i]) >= 124
    assert k6r.channel_errors(got[0][0], np.sign(cor[i]) * y[i + 32:i + 32 + k6r.CODED]) == 0


def test_weak_signal_on_the_model():
    """the flat model at 47 dB-Hz: every packet, on a steady clock and on an edge-locked one;
    at 40 none"""
    from sim.k6if import range as rg
    pls = k6r.test_payloads(4, seed=5)
    for lock, rate in ((None, 4800.0), (1.0, 4716.0)):
        smp, _ = rg.link(pls, 47, seed=6, tx="flat", rate=rate, lock=lock)
        got, _ = k6r.receive_samples(smp)
        assert sum(1 for p, c in got if c and p in pls) == 4, (lock, got)
    smp, _ = rg.link(pls, 40, seed=6, tx="flat")
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
                                   for t0, t1, _, _ in times)
    t0, t1, _, _ = times[1]
    d = rg.chip_audio(y[int((t0 - 0.2) * k6r.FS):int((t1 + 0.2) * k6r.FS)], None, tx="flat")
    got, _ = k6r.receive_samples(rg.sample(d))
    assert [p for p, c in got if c] == k6r.test_payloads(2, seed=101)
    # pre-emphasised every other burst ("alt"): 1200 Hz up on 600 Hz by about the filter's
    # 20 log10(|1 + 4j| / |1 + 2j|) = 5.4 dB, the same peak
    y, times = k6r.bench_wav(10.0, npk=2, level=k6r.LEVEL, preemph="alt")
    assert [pe for _, _, _, pe in times] == [False, True]

    def ratio(t0, t1):
        x = y[int(t0 * k6r.FS):int(t1 * k6r.FS)]
        f = np.abs(np.fft.rfft(x)) ** 2
        hz = np.fft.rfftfreq(len(x), 1 / k6r.FS)
        return f[abs(hz - 1200) < 150].sum() / f[abs(hz - 600) < 150].sum()
    db = 10 * np.log10(ratio(*times[1][:2]) / ratio(*times[0][:2]))
    assert 4 < db < 7, db
    assert abs(np.max(np.abs(y)) - k6r.LEVEL) < 1e-3


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
@pytest.mark.parametrize("rate,lock", [(4800.0, None), (4716.0, None), (4880.0, None), (4716.0, 1.0)])
def test_rangerx_on_the_model(rate, lock):
    """the module reads every packet of a clean burst at each sampler rate, says a line for each
    with its seq, CRC right and no channel errors, re-arms the chip for each, sets the de-emphasis
    off while it listens and puts the registers back"""
    pytest.importorskip("unicorn")
    from sim.k6if import range as rg
    pls = k6r.test_payloads(4, seed=10)
    smp, _ = rg.link(pls, None, rate=rate, tx="flat", lock=lock)
    e, (ret, stack), code = run_module(smp, rate, 0.3 + len(smp) / rate + 1)
    assert len(code) < 5000, len(code)
    assert stack < 600, stack
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    _, pk, ends = k6r.parse_log(["K6 MOD say " + s for s in e.said])
    assert ret == 4 and [p["ok"] for p in pk] == ["1"] * 4, e.said
    assert [p["s"] for p in pk] == ["0", "1", "2", "3"]
    assert all(int(p["e"]) == 0 for p in pk), pk
    assert ends and ends[-1]["c"] == "4" and ends[-1]["z"] == "0", ends
    assert 0x0100 in e.writes[0x2B]
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F):
        assert e.writes[reg][-1] == 0, hex(reg)


@need_clang
def test_rangerx_near_the_edge():
    """at 46 dB-Hz on the flat model, an edge-locked sampler at 3.93 samples a bit: the module
    reads what the Python receiver reads, step for step (the same packets, the same channel
    errors in each)"""
    pytest.importorskip("unicorn")
    from sim.k6if import range as rg
    pls = k6r.test_payloads(6, seed=11)
    smp, _ = rg.link(pls, 46, seed=12, tx="flat", rate=4716.0, lock=1.0)
    r = k6r.Receiver()
    for x in smp:
        r.feed(int(x))
    py = [(p in pls and c, e) for (p, c), e in zip(r.got, r.errs)]
    e, (ret, _), _ = run_module(smp, 4716.0, 0.3 + len(smp) / 4716 + 1)
    _, pk, _ = k6r.parse_log(["K6 MOD say " + s for s in e.said])
    mod = [(p["ok"] == "1", int(p["e"])) for p in pk]
    assert mod == py and len(mod) == 6 and ret >= 2, (mod, py, e.said)
    assert any(e > 20 for _, e in mod), mod


@need_clang
@pytest.mark.parametrize("start", [1 << 13, 0])
def test_rangerx_b_ladder(start):
    """the bench build's ladder, shortened: nine settings, each with its s and v lines (and its
    first capture's words), the
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
    assert len(code) < 5816, len(code)   # (16 B of it rangerx.c's name)
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
    e = Board(code, entry, np.zeros(16, np.uint8), 0, 5.49e6, 1.5, max_s=total, level=8, scratch=6144)
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
    # (saying the first capture's words takes the gap before the second packet: it is missed)
    assert ret == 1 and sum(int(d["n"]) for d in nv) == 1 and sum(int(d["ok"]) for d in nv) == 1, e.said
    caps = k6r.dump_captures(["K6 MOD say " + ln for ln in e.said])
    assert caps and all(len(c["words"]) == int(c["w"]) for c in caps), e.said   # the setting's first capture
    rep = k6r.report(["K6 MOD say " + ln for ln in e.said])
    rows = [ln for ln in rep if ln.startswith("setting ")]
    assert len(rows) == 9 and sum(int(ln.split(": ")[1].split()[2]) for ln in rows) == 1, rep


@need_clang
def test_rangerx_d_dump():
    """the dump build says each capture's words; the host's dump report reads them back into the
    samples the module sliced, finds the sync and lays the packet on the bench's own: no bit
    wrong on a clean link"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from sim.k6if import range as rg
    from test_k6_pocsag import plain_chip
    pls = k6r.test_payloads(6, seed=100)[:2]
    smp, _ = rg.link(pls, None, tx="flat")
    total = 0.3 + len(smp) / 4800 + 1
    code, entry = le.build("x", [("WAIT_MS", "0u"), ("RG_S", f"{int(total) + 1}u")],
                           os.path.join(ROOT, "c", "k6", "mod", "rangerx_d.c"))
    Chip = plain_chip(le, [(0.3, 4800.0, smp)])
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, 5.49e6, 1.5, max_s=total + 4, level=8, scratch=6144)
    res = e.run()
    assert res is not None, e.said[-5:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    log = ["K6 MOD say " + ln for ln in e.said]
    caps = k6r.dump_captures(log)
    assert len(caps) >= 1 and all(len(c["words"]) == int(c["w"]) for c in caps)
    rep = k6r.dump_report(log, cands=k6r.test_payloads(6, seed=100))
    hits = [ln for ln in rep if "best match" in ln]
    assert hits and all(" 0 of 2064 wrong" in ln for ln in hits), rep
