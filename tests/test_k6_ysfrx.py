"""K6 YSF (C4FM) sotto receive: c/k6/ysfdec.h's decoders and c/k6/mod/ysfdec.inc's frame
receiver on dmrsync.h's IF grid (c/k6/ysfrx_host.c), and the ysfrx RAM module on the emulated
K6 (docs/k6-c4fm.md, "Receiving C4FM sotto")."""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem import ysf  # noqa: E402
from sim.k6if import dmr  # noqa: E402

K6 = os.path.join(ROOT, "c", "k6")
NSF = 40                                    # sotto_test(40): 8 VW frames, CRC-16 0xd821
NSF_LONG = 80                               # 3.2 s: 16 VW frames


def ysf_symbols(nsf=NSF, lose=()):
    """the call's symbols; lose: frames (0 the header, 1 the first VW) made random, all of the
    frame (f) or only its payload (-f)"""
    b = ysf.bytes_to_bits(ysf.tx_sotto(ysf.sotto_test(nsf)))
    sym = np.array([ysf.LEVEL[(int(b[2 * i]) << 1) | int(b[2 * i + 1])] for i in range(len(b) // 2)], float)
    rng = np.random.default_rng(5)
    for f in lose:
        a = 96 + 480 * abs(f)                   # after the preamble's 24 bytes
        lo = a if f > 0 else a + 120            # (the sync and FICH: 120 symbols)
        sym[lo:a + 480] = rng.choice([-3.0, -1.0, 1.0, 3.0], a + 480 - lo)
    return sym


def k6_counts(sym, cn0, seed=1, dev=2700.0, inv=False, post_s=0.4):
    """the K6's GP0 counts for a YSF call at dev Hz for the +-3 symbols (C4FM's 2.7 kHz)"""
    import test_k6_dmr as T
    s, _ = dmr.dmr_baseband((-1 if inv else 1) * sym * dev / 3 / dmr.UNIT, pre_s=0.0, post_s=0.0)
    ib, on, off = T.stream(s, cn0=cn0, seed=seed, post_s=post_s)
    b = (ib >> 1) & 1
    n = len(b) // 64
    return (b[:n * 64].reshape(n, 64).sum(1) * 2 - 64).astype(np.int16), (ib, on, off)


def stored(lines):
    """the 'Y d' lines' bytes, in order"""
    out = bytearray()
    for ln in lines:
        if ln.startswith("Y d "):
            out += bytes.fromhex(ln.split()[3])
    return bytes(out)


def want(nsf=NSF, lose=()):
    """the message as the receiver gives it: a lost VW frame's five superframes are the one
    before them, repeated (sotto_rx's srx_conceal)"""
    w = bytearray(ysf.sotto_test(nsf))
    for f in lose:
        j = abs(f) - 1
        for k in range(5 * j, 5 * j + 5):
            w[6 * k:6 * k + 6] = w[6 * (5 * j - 1):6 * 5 * j]
    return bytes(w)


@pytest.fixture(scope="module")
def host():
    if not shutil.which("cc"):
        pytest.skip("no cc")
    exe = os.path.join(tempfile.mkdtemp(), "ysfrx_host")
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", K6, "-o", exe,
                    os.path.join(K6, "ysfrx_host.c")], check=True)
    return exe


@pytest.mark.parametrize("cn0,inv", [(60.0, False), (52.0, False), (54.0, True)])
def test_host_receiver_on_the_k6_model(host, cn0, inv):
    """every superframe of the call byte-exact down to 52 dB-Hz (about where the DMR receiver
    needs to be on the bench), either FM polarity"""
    cnt, _ = k6_counts(ysf_symbols(), cn0, inv=inv)
    out = subprocess.run([host], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    lines = out.splitlines()
    print(out)
    assert "Y call fr=10 fich=10 vw=40/40 sf=40 miss=0 crc=d821" in lines, out
    assert stored(lines) == want()


def test_host_receiver_fills_lost_frames(host):
    """a 12 s message (300 superframes) with one frame lost whole (the flywheel keeps the frame
    count) and one frame's payload lost: every superframe in its place, the ten lost ones filled
    with the superframe before them"""
    lose = (21, -36)
    cnt, _ = k6_counts(ysf_symbols(300, lose), 56.0)
    out = subprocess.run([host], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    lines = out.splitlines()
    assert any(ln.startswith("Y call fr=62 ") and " vw=290/300 sf=300 miss=10 " in ln for ln in lines), out
    assert stored(lines) == want(300, lose)


@pytest.mark.parametrize("cps8", [4539, 4413])
def test_host_receiver_times_its_grid(host, cps8):
    """the grid's rate 1.4% off the signal's (the radio's DMA by its MCU clock, bench run 6):
    the first frames' syncs time the grid (YX_WIDE), so the call holds together and the message
    from its second VW frame on is byte-exact in place (the old receiver broke it into calls)"""
    cnt, _ = k6_counts(ysf_symbols(), 56.0)
    out = subprocess.run([host, str(cps8)], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    lines = out.splitlines()
    calls = [ln for ln in lines if ln.startswith("Y call")]
    assert len(calls) == 1 and calls[0].startswith("Y call fr=10 ") and " sf=40 " in calls[0], out
    assert stored(lines)[30:] == want()[30:], out


def test_host_receiver_says_nothing_on_noise(host):
    cnt, _ = k6_counts(ysf_symbols(), 30.0)
    out = subprocess.run([host], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    assert not any(ln.startswith("Y d") for ln in out.splitlines()), out


def test_ysfrx_module_on_the_emulated_k6():
    """the ysfrx RAM module on the emulated M0 with the modelled DMA, the radio's slow register
    reads and its ~5 ms say lines, started with a carrier on: a 3.2 s message with two frames lost on the serial line after
    the call, byte-exact with the lost superframes filled in, in the shared module block (code + the filter's ring + the receiver's state +
    the DMA ring), no samples lost, the receiver's registers put back; too small a block and it
    returns -6"""
    if not (shutil.which("clang") and shutil.which("ld.lld")):
        pytest.skip("needs clang and ld.lld")
    pytest.importorskip("unicorn")
    import test_k6_dmr as T
    import k6_live_emu as le
    from sim.k6if import frontend as fe
    code, entry = T.build([("RUN_S", "6u"), ("S_MS", "1000u")], os.path.join(K6, "mod", "ysfrx.c"))
    lose = (6, -10)
    _, (ib, on, off) = k6_counts(ysf_symbols(NSF_LONG, lose), 56.0, post_s=4.0)   # the call is ~1.9 s

    def emu(block, ib):
        e = le.Emu(code, entry, ib, on, fe.FDMA, 1.5, max_s=len(ib) / fe.FDMA + 0.5, level=6, stack_win=7000,
                   scratch=(block - 24 - len(code)) & ~3, fd_us=300)
        # a carrier at the start as well (the bench: the upload's own, still on when the module
        # starts): the floor read then is too high until a quiet reading lowers it
        e.rssi = lambda: -60 if on <= e.sample_now() < off or e.sample_now() < on // 2 else -125
        ret, stack = e.run()
        return e, ret, stack

    e, ret, _ = emu(len(code) + 24 + 1024, ib[:on])
    assert ret - (1 << 32) == -6, (ret, e.said)
    e, ret, stack = emu(11776, ib)
    txt = "\n".join(e.said)
    print(txt, stack, len(code))
    # the message buffer is what the block has left: 2.6 s or more
    assert int(e.said[0].split("msg=")[1]) >= 64, txt
    assert " floor=-60 " in e.said[0], txt
    # the carrier is seen at the next housekeeping, ~half a frame in: the header frame is gone
    # (frame 6 lost whole and frame 10's payload: 10 superframes filled in)
    assert any(ln.startswith("Y call fr=17 ") and " vw=70/80 sf=80 miss=10 " in ln for ln in e.said), txt
    assert stored(e.said) == want(NSF_LONG, lose), txt
    assert ret == 17, txt
    lost = [int(ln.split("lost=")[1].split()[0]) for ln in e.said if ln.startswith("s ")]
    assert lost and lost[-1] == 0, txt
    assert e.writes[0x13][-1] == 0 and e.writes[0x7E][-1] == 0
    assert stack < 1500, stack
