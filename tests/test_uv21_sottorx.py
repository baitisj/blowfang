"""[S12] the UV-21 Pro port's built-in sotto 1200 receiver (radios/uv21/fw/src/sotto.rs,
radios/uv21/sotto/uv21_sotto.c) in the emulated radio of sim/uv21/board.py: `um s` (or a long 0)
leaves SURF, the receiver checks its codebook in the SPI flash at 0x1F0000 (the K6's image,
radios/uv21/build/sotto/sotto-cb.bin, as cbspi.c wrote it on the radio), arms the RF chip's FSK
receiver, listens, and SURF comes back. Without the codebook it plays nothing. Neither path writes
the SPI flash or a transmit register. SPDX-License-Identifier: MIT"""
import os
import sys

import pytest

pytest.importorskip("unicorn")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sim.uv21.board import Station, UV21Board, build  # noqa: E402
from test_uv21_surf import until, wait  # noqa: E402

CB = os.path.join(ROOT, "radios", "uv21", "build", "sotto", "sotto-cb.bin")
CB_SPI = 0x1F0000
HZ = 440_850_000
WRITES = (0x06, 0x02, 0x20, 0x52, 0xD8, 0xC7, 0x60, 0x01)   # write enable, program, erases, status


@pytest.fixture(scope="module")
def built():
    return build()


def radio(built, codebook):
    b = UV21Board(*built, band=[Station("a", 439_500_000, -70)])
    if codebook:
        img = open(CB, "rb").read()
        b.nor_mem[CB_SPI:CB_SPI + len(img)] = img
    b.run(until="boot done", limit=300_000_000)
    wait(b, 1)
    return b


def listen(b, seconds):
    b.serial_in(f"um s {HZ:08x} {seconds:04x}\n".encode())
    m, out = until(b, r"so ret r=[0-9a-f]{8} stk=[0-9a-f]{4}", seconds + 20)
    assert m, out[-600:]
    if "surf ram" not in out[out.index(m.group(0)):]:      # SURF starts again after the receiver
        m2, more = until(b, r"surf ram", 10)
        assert m2, more[-300:]
        out += more
    return out


@pytest.mark.skipif(not os.path.exists(CB), reason="needs sh radios/uv21/sotto/build.sh")
def test_listens_with_the_codebook_and_surf_comes_back(built):
    b = radio(built, True)
    out = listen(b, 2)
    assert "um ok s" in out and "so begin hz=1a46d650" in out
    assert "so start hz=440850000 cb=1" in out, out[-800:]         # the codebook checks
    assert "so end pk=0 good=0" in out and "so ret r=00000000 stk=" in out
    # it armed the FSK receiver as the K6 pager's D/2400 rung (direct 2-FSK, the one the BK4829
    # decodes on air: job s12rx3), with its sync words and XL length, then turned it off again;
    # the PA register only ever got its off value
    w = b.rf_writes()
    assert (0x58, 0x00E9) in w and (0x72, 0x60CB) in w and (0x5D, 0x7F00) in w and (0x5A, 0x076B) in w
    assert any(r == 0x2B and v & 0x0400 and not v & 0x4000 for r, v in w)   # RX HPF and DC cancel off
    assert (0x58, 0x0000) in w[[i for i, x in enumerate(w) if x == (0x58, 0x00E9)][-1]:]
    assert {v for r, v in b.tx_watch if r == 0x36} <= {0x0022}
    assert not [op for op in b.nor_ops if op[0] in WRITES]


def test_without_the_codebook_it_plays_nothing(built):
    b = radio(built, False)
    out = listen(b, 2)
    assert "so start hz=440850000 cb=-1" in out, out[-800:]
    assert "so ret r=ffffffff stk=" in out
    assert not [op for op in b.nor_ops if op[0] in WRITES]


def test_um_s_refuses_a_frequency_out_of_range(built):
    b = radio(built, False)
    b.serial_in(b"um s 00001000 0002\n")
    m, out = until(b, r"um (ok|bad)[^\r\n]*", 3)
    assert m and m.group(0).startswith("um bad sotto"), out[-300:]
