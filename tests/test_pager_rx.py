"""The portable pager receiver (c/common/pager_rx.c), which the UV-380 uses to put a message back
together from the packets it hears and play it: from pager_tx's packets in any order, with
repeats, a second pass, packets lost, parity packets, a plain (no short last group) layout and
another message's packets after it, the frames must come back exactly where they were; and from
PHY R audio (range_tx -> 8 kHz -> range_rx) the whole message.
Needs make -C c/common. SPDX-License-Identifier: MIT"""
import os
import subprocess
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C = os.path.join(ROOT, "c", "common")
TX = os.path.join(C, "pager_tx_host")
RX = os.path.join(C, "pager_rx_host")
RTX = os.path.join(C, "range_tx_host")
RRX = os.path.join(C, "range_rx_host")
pytestmark = pytest.mark.skipif(not (os.path.exists(TX) and os.path.exists(RX)),
                                reason="needs make -C c/common")


def frames_of(n, seed=1):
    rng = np.random.default_rng(seed)
    return bytes(rng.integers(0, 256, 6 * n, dtype=np.uint8))


def packets(frames, *opts):
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "m.sot"), os.path.join(d, "p.bin")
        open(src, "wb").write(frames)
        subprocess.run([TX, "packets", src, out, *map(str, opts)], check=True)
        return open(out, "rb").read()


def receive(pk, n=128, conceal=False):
    """-> (summary dict, frames, per-packet results)"""
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "p.bin"), os.path.join(d, "m.sot")
        open(src, "wb").write(b"".join(pk))
        r = subprocess.run([RX, src, str(n), out] + (["--conceal"] if conceal else []), check=True,
                           capture_output=True, text=True).stdout.splitlines()
        got = open(out, "rb").read()
    res = [int(ln.split()[2][2:]) for ln in r if ln.startswith("p ")]
    summ = dict(v.split("=") for v in r[-1].split()[1:])
    return {k: int(v) for k, v in summ.items()}, got, res


def split(blob, n=128):
    return [blob[i:i + n] for i in range(0, len(blob), n)]


@pytest.mark.parametrize("nf,opts", [
    (1, ()),
    (20, ()),
    (159, ()),
    (160, ()),
    (161, ()),
    (530, ()),
    (750, ()),
    (530, ("--depth", 4)),
    (530, ("--depth", 1)),
    (530, ("--plain",)),
    (100, ("--profile", "L")),
])
def test_in_order(nf, opts):
    fr = frames_of(nf, nf)
    n = 64 if "L" in opts else 128
    s, got, res = receive(split(packets(fr, *opts), n), n)
    assert s["total"] == nf and s["got"] == nf and s["missing"] == 0 and s["bad"] == 0
    assert got == fr
    assert res[-1] == 2 and all(r == 1 for r in res[:-1])


def test_shuffled_repeats_parity():
    fr = frames_of(530, 7)
    pk = split(packets(fr, "--parity", "--passes", 2))
    rng = np.random.default_rng(3)
    order = rng.permutation(len(pk))
    s, got, res = receive([pk[i] for i in order])
    assert got == fr and s["got"] == 530
    assert len(pk) == 2 * (27 + 4) and s["bad"] == 8   # 27 data packets and 4 parity a pass
    assert res.count(2) == 1


def test_lost_packets_and_conceal():
    fr = frames_of(530, 9)
    pk = split(packets(fr))
    lost = {3, 11}
    s, got, _ = receive([p for i, p in enumerate(pk) if i not in lost])
    assert s["missing"] == 40 and s["got"] == 490
    # without conceal the lost frames are zeros and every other frame is in place
    have = [got[6 * f:6 * f + 6] == fr[6 * f:6 * f + 6] for f in range(530)]
    assert sum(have) == 490
    s, cg, _ = receive([p for i, p in enumerate(pk) if i not in lost], conceal=True)
    for f in range(530):
        if not have[f]:
            # the frame before it (received or itself filled), or for frame 0 the next received
            assert cg[6 * f:6 * f + 6] == cg[6 * (f - 1):6 * f] if f else True
        else:
            assert cg[6 * f:6 * f + 6] == fr[6 * f:6 * f + 6]


def test_bad_crc_and_new_message():
    a, b = frames_of(100, 1), frames_of(60, 2)
    pa, pb = split(packets(a)), split(packets(b))
    broken = bytearray(pa[0])
    broken[40] ^= 1
    s, got, res = receive([bytes(broken)] + pa[1:3] + pb)
    assert res[0] == -1
    assert s["total"] == 60 and s["got"] == 60 and got == b and s["bad"] == 1


@pytest.mark.skipif(not (os.path.exists(RTX) and os.path.exists(RRX)), reason="needs make -C c/common")
def test_phy_r_audio():
    signal = pytest.importorskip("scipy.signal")
    fr = frames_of(230, 5)
    pk = packets(fr)
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "in.bin"), "wb").write(pk)
        subprocess.run([RTX, "audio", os.path.join(d, "in.bin"), os.path.join(d, "a.raw"), "--fs", "48000",
                        "--level", "12000"], check=True)
        a = np.fromfile(os.path.join(d, "a.raw"), "<i2").astype(float)
        x = signal.resample_poly(a, 1, 6)
        x += np.random.default_rng(1).normal(0, 300, len(x))
        np.clip(np.round(x), -32768, 32767).astype("<i2").tofile(os.path.join(d, "x.raw"))
        out = subprocess.run([RRX, os.path.join(d, "x.raw"), "8000"], check=True, capture_output=True,
                             text=True).stdout
    heard = [bytes.fromhex(ln.split()[-1]) for ln in out.splitlines() if " ok=1 " in ln]
    s, got, _ = receive(heard)
    assert s["got"] == 230 and got == fr
