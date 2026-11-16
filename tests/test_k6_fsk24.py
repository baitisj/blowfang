"""fsk24rx (c/k6/mod/fsk24rx.c, modem/fsk24.py): the K6's own FSK modem on plain FSK at 2400
baud. The bench signal's packets and pre-equalisers, and the module on an emulated M0 against
test_k6_pocsag's model of the FD6818's plain FSK receiver (a clock locked to the data at REG_72's
baud, the sync word at any bit, sixteen bits a FIFO word). The model can't say whether the real
chip reads 2400 baud through the IC-7100: the bench says.
SPDX-License-Identifier: MIT"""
import os
import shutil
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K6 = os.path.join(ROOT, "c", "k6")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem import fsk24  # noqa: E402

need_clang = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                reason="needs clang and ld.lld")


def test_packet_layout():
    p = fsk24.packet(5, 17)
    assert len(p) == 64 and p[:3] == bytes([0xF2, 5, 17])
    assert p[3:62] == fsk24.pn9(59) and fsk24.crc16(p[:62]) == int.from_bytes(p[62:], "big")
    assert fsk24.crc16(b"123456789") == 0x29B1          # CRC-16/CCITT-FALSE's check value
    assert fsk24.pn9(3) == bytes([0xFF, 0xE1, 0x1D])     # x^9 + x^5 + 1 from 0x1FF


def test_pre_equalised_variants_survive_the_model_channel():
    """our demodulator through the IC-7100 model: the pre-equalised 2400 variants read, the plain
    one doesn't (the old result); flat, the plain ones read"""
    got = fsk24.sim(n_rounds=1)
    for vi in (1, 3, 4, 5, 6):
        assert got[vi][0] == got[vi][1], (vi, got)
    assert got[2][0] == 0, got
    flat = fsk24.sim(n_rounds=1, ch="flat")
    assert flat[0][0] == flat[0][1] and flat[2][0] == flat[2][1], flat


def test_report_reads_module_lines():
    lines = ["K6 MOD say " + s for s in [
        "fsk24 start hz=440850000 n=5",
        "s 2 t=30000 cfg=00e9 b=60cb a=04",
        "p 2 v=3 q=4 c=1 i=0 e=0 x=0 r=-41",
        "p 2 v=3 q=5 c=0 i=1 e=12 x=1 r=-41",
        "p 2 v=200 q=9 c=0 i=0 e=230 x=4 r=-41",
        "v 2 n=3 ok=1 z=1",
    ]]
    rep = fsk24.report(lines)
    assert rep[0].startswith("setting 2: REG_58 00e9 2400 baud REG_2B +0400: 3 packets, 1 CRC right"), rep
    assert "v3 2400 emhp   0.15: 1/2 right" in rep[1] and "upside down 1" in rep[1], rep
    assert "1 with a broken header" in rep[2], rep


@need_clang
def test_fsk24rx_on_the_model():
    """two settings (1200, then 2400 baud) on the bench signal's blocks: each reads its own
    baud's packets with the CRC right and nothing wrong, the other baud's go by unseen"""
    pytest.importorskip("unicorn")
    from test_k6_pocsag import run_plain
    _, segs = fsk24.rounds(2, variants=[0, 2, 3])
    segs = [(t + 0.3, baud, bits) for t, baud, bits, _ in segs]
    total = segs[-1][0] + len(segs[-1][2]) / segs[-1][1] + 1.0
    half = int(total / 2 * 1000)
    e, ret = run_plain(os.path.join(K6, "mod", "fsk24rx.c"),
                       [("WAIT_MS", "100u"), ("SLOT_SCALE", f"{half // 30}u"),
                        ("F24_NV", "2"),
                        ("F24_V", "{0x00E1, 12389, 2133, 0}, {0x00E9, 24779, 1067, 0x0400}")],
                       segs, total + 5)
    assert e.said[0] == "fsk24 start hz=440850000 n=2" and e.said[-1].startswith("fsk24 end"), e.said[-3:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    sets, pk = fsk24.parse_log(["K6 MOD say " + s for s in e.said])
    assert set(sets) == {0, 1}, sets
    s0 = [p for p in pk if p["s"] == 0]
    s1 = [p for p in pk if p["s"] == 1]
    assert s0 and all(p["v"] == 0 for p in s0), s0
    assert len(s1) >= 4 and all(p["v"] in (2, 3) for p in s1), s1
    assert all(p["c"] == 1 and p["e"] == 0 and p["i"] == 0 for p in pk), pk
    assert ret == len(pk), (ret, len(pk))


@need_clang
def test_fsk24rx_dumps_bad_packets():
    """bits flipped now and then: packets with a few wrong are counted and say their words
    (the model has no upside-down sync, so the module's upside-down check isn't run here)"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from test_k6_pocsag import plain_chip
    _, segs = fsk24.rounds(1, variants=[2, 2])
    segs = [(t + 0.3, baud, bits) for t, baud, bits, _ in segs]
    total = segs[-1][0] + len(segs[-1][2]) / segs[-1][1] + 1.0
    code, entry = le.build("x", [("WAIT_MS", "100u"), ("SLOT_SCALE", f"{int(total * 1000) // 30 + 1}u"),
                                 ("F24_NV", "1"), ("F24_V", "{0x00E9, 24779, 1067, 0x0400}")],
                           os.path.join(K6, "mod", "fsk24rx.c"))
    Chip = plain_chip(le, segs, flip=0.004, seed=5)
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, 5.49e6, 1.5, max_s=total + 5, level=8, scratch=4096)
    assert e.run() is not None, e.said[-5:]
    _, pk = fsk24.parse_log(["K6 MOD say " + s for s in e.said])
    assert len(pk) >= 5 and all(p["i"] == 0 and p["e"] < 20 for p in pk), e.said
    bad = [p for p in pk if not p["c"]]
    assert bad and any(p["c"] for p in pk), pk
    assert sum(s.startswith("d 0 00 ") for s in e.said) == min(len(bad), 3), e.said


def test_run2_symbol_variants_read_flat_and_take_the_equaliser():
    """run 2's bits as +-3 DMR symbols (RRC) read back through a flat channel, and the
    equaliser is applied: a two-tap one changes the audio, the identity doesn't"""
    flat = fsk24.sim(n_rounds=1, ch="flat")
    for vi in fsk24.RUN2:
        assert flat[vi][0] == flat[vi][1], (vi, flat)
    plain = fsk24.block_audio(9, 0)
    old = dict(fsk24.EQ)
    try:
        fsk24.EQ.update(taps=[1.0, -0.5], centre=0)
        tilted = fsk24.block_audio(9, 0)
    finally:
        fsk24.EQ.clear()
        fsk24.EQ.update(old)
    assert len(plain) == len(tilted) and np.max(np.abs(plain - tilted)) > 0.01
    assert np.array_equal(plain, fsk24.block_audio(9, 0))
