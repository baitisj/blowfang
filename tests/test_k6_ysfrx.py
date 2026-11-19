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
    assert "Y call fr=10 fich=10 vw=40/40 sf=40 miss=0 crc=d821 from=AG7EW" in lines, out
    assert stored(lines) == want()


@pytest.mark.parametrize("lose", [(0,), (9,), (0, 9)])
def test_host_receiver_reads_the_callers_callsign(host, lose):
    """the caller's callsign (CSD1's src) from the header, or from the terminator when the
    header is lost (on the radio the carrier is often seen only half a frame in), and none
    when both are lost: the CSD decoded in the K=7 decoder's place"""
    cnt, _ = k6_counts(ysf_symbols(NSF, lose), 56.0)
    out = subprocess.run([host], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    calls = [ln for ln in out.splitlines() if ln.startswith("Y call")]
    assert len(calls) == 1 and " sf=40 miss=0 crc=d821" in calls[0], out   # (a lost terminator is tried as a VW)
    assert calls[0].endswith(" crc=d821" if lose == (0, 9) else " crc=d821 from=AG7EW"), out


def test_host_receiver_reads_a_long_callsign(host):
    """ten characters, the field's whole width"""
    b = ysf.bytes_to_bits(ysf.tx_sotto(ysf.sotto_test(NSF), src="KE7ABC-12/"))
    sym = np.array([ysf.LEVEL[(int(b[2 * i]) << 1) | int(b[2 * i + 1])] for i in range(len(b) // 2)], float)
    cnt, _ = k6_counts(sym, 56.0)
    out = subprocess.run([host], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    assert any(ln.startswith("Y call ") and ln.endswith(" from=KE7ABC-12/") for ln in out.splitlines()), out


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
    # the trace (the host builds it, as the bench builds do): the grid's step, then 3 bytes for
    # each of the first 24 frames (frame 21, lost: no sync, no superframe good; the next sync
    # looked for wide, its FICH and five superframes good)
    assert any(ln.startswith("Y t w=") and ln.endswith(" ho=0") for ln in lines), out
    tr = "".join(ln.split()[3] for ln in lines if ln.startswith("Y t ") and not ln.startswith("Y t w="))
    assert len(tr) == 6 * 24 and tr[6 * 21 + 4:6 * 21 + 6] == "20" and tr[6 * 22 + 4:6 * 22 + 6] == "ff", out


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


@pytest.mark.parametrize("cps8", [4610, 4413])
def test_host_receiver_retimes_after_lost_frames(host, cps8):
    """the grid 3% off with frames lost, one right after the sync that timed it: the sync after
    a miss is two frames' offset (bench run 13: taken as one, the step swung as far the other way
    each time and the call fell apart), so the call holds and the message from its third VW
    frame on is in place"""
    lose = (2, 5, 9)
    cnt, _ = k6_counts(ysf_symbols(NSF_LONG, lose), 56.0)
    out = subprocess.run([host, str(cps8)], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    lines = out.splitlines()
    calls = [ln for ln in lines if ln.startswith("Y call")]
    assert len(calls) == 1 and calls[0].startswith("Y call fr=18 ") and " sf=80 miss=20 " in calls[0], out
    assert stored(lines)[60:] == want(NSF_LONG, lose)[60:], out


@pytest.mark.parametrize("cn0", [30.0, 20.0])
def test_host_receiver_says_nothing_on_noise(host, cn0):
    """no message from noise, and a false sync in it ends within two frames (bench run 14: the
    wide search's best of noise, taken weak, held calls of up to 311 frames)"""
    cnt, _ = k6_counts(ysf_symbols(), cn0)
    out = subprocess.run([host], input=cnt.tobytes(), capture_output=True, check=True).stdout.decode()
    assert not any(ln.startswith("Y d") for ln in out.splitlines()), out
    assert all(int(ln.split()[2][3:]) <= 2 for ln in out.splitlines() if ln.startswith("Y call")), out


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
    # (the bench build for images before module API level 11: the message in the bootloader's
    # RAM, where level 11's msg_mem puts it, and its bytes said)
    code, entry = T.build([("RUN_S", "6u"), ("S_MS", "1000u"), ("YX_RAM0", "1"), ("YX_DUMP", "1")],
                          os.path.join(K6, "mod", "ysfrx.c"))
    lose = (6, -10)
    nsf = NSF_LONG
    _, (ib, on, off) = k6_counts(ysf_symbols(nsf, lose), 56.0, post_s=4.0)   # the call is ~1.9 s

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
    # the message buffer: the bootloader's RAM less the DMA ring, 18.4 s
    assert int(e.said[0].split("msg=")[1]) == 459, txt
    assert " floor=-60 " in e.said[0], txt
    # the carrier is seen at the next housekeeping, ~half a frame in: the header frame is gone
    # (frame 6 lost whole and frame 10's payload: 10 superframes filled in)
    assert any(ln.startswith("Y call fr=17 ") and " vw=70/80 sf=80 miss=10 " in ln for ln in e.said), txt
    assert stored(e.said) == want(nsf, lose), txt
    assert ret == 17, txt
    lost = [int(ln.split("lost=")[1].split()[0]) for ln in e.said if ln.startswith("s ")]
    assert lost and lost[-1] == 0, txt
    assert e.writes[0x13][-1] == 0 and e.writes[0x7E][-1] == 0
    assert stack < 1500, stack


def test_ysfrx_module_hands_the_call_to_sotto_play():
    """on an image with module API level 11 the module hands the call it heard to sotto_play once
    its lines are out (the message, sotto 1200, listen again after playback, the caller from the
    terminator: the header went by while the carrier was seen) and returns at once, long before
    RUN_S, so the core can play it"""
    if not (shutil.which("clang") and shutil.which("ld.lld")):
        pytest.skip("needs clang and ld.lld")
    pytest.importorskip("unicorn")
    import test_k6_dmr as T
    import k6_live_emu as le
    from sim.k6if import frontend as fe
    code, entry = T.build([("RUN_S", "20u"), ("S_MS", "1000u")], os.path.join(K6, "mod", "ysfrx.c"))
    lose = (6, -10)
    _, (ib, on, off) = k6_counts(ysf_symbols(NSF_LONG, lose), 56.0, post_s=4.0)
    e = le.Emu(code, entry, ib, on, fe.FDMA, 1.5, max_s=len(ib) / fe.FDMA + 0.5, level=11, stack_win=7000,
               scratch=(11776 - 24 - len(code)) & ~3, fd_us=300)
    e.rssi = lambda: -60 if on <= e.sample_now() < off or e.sample_now() < on // 2 else -125
    ret, stack = e.run()
    txt = "\n".join(e.said)
    print(txt, stack, len(code))
    assert any(ln.startswith("Y call fr=17 ") and ln.endswith(" sf=80 miss=10 crc=" + ln.split("crc=")[1][:4] + " from=AG7EW")
               for ln in e.said), txt
    assert e.played == [(want(NSF_LONG, lose), 0xA9, 1, "AG7EW")], e.played
    assert ret == 17 and e.cyc / le.FCPU < off / fe.FDMA + 2.0, (ret, e.cyc / le.FCPU)
    assert stack < 1500, stack


def test_ysfrx_module_with_the_dma_following_the_cpu():
    """the DMA's rate following what the CPU does (the emulator's wobble: 3% faster in a chunk that
    reads CNDTR than in one that computes): on the radio (bench runs 15-23) its syncs still came
    on time while the symbols between them drifted and most superframes failed. The grid's step
    follows the DMA's rate over ~0.7 ms (dmrdet.c), and the call decodes whole"""
    if not (shutil.which("clang") and shutil.which("ld.lld")):
        pytest.skip("needs clang and ld.lld")
    pytest.importorskip("unicorn")
    import test_k6_dmr as T
    import k6_live_emu as le
    from sim.k6if import frontend as fe
    code, entry = T.build([("RUN_S", "3u"), ("S_MS", "1000u"), ("YX_RAM0", "1"), ("YX_DUMP", "1")],
                          os.path.join(K6, "mod", "ysfrx.c"))
    fh = 6.5e6                                  # the sigma-delta's own bits (le.Emu.fhi)
    s, _ = dmr.dmr_baseband(ysf_symbols() * 2700.0 / 3 / dmr.UNIT, pre_s=0.0, post_s=0.0)
    bits = fe.receive(s, T.st.FZ, 56.0, seed=1, L=0, fdma=fh, jit=0.0)
    rng = np.random.default_rng(2)
    a = int(0.5 * fh)
    ib = np.concatenate([rng.integers(0, 2, a), bits, rng.integers(0, 2, int(1.0 * fh))]).astype(np.uint8) * 2
    on, off = a / fh, (a + len(bits)) / fh
    e = le.Emu(code, entry, ib, int(on * fe.FDMA), fe.FDMA, 1.5, max_s=4.0, wobble=0.03, wob_reads=1,
               level=6, stack_win=7000, scratch=(11776 - 24 - len(code)) & ~3, fd_us=300)
    e.rssi = lambda: -60 if on <= e.cyc / le.FCPU < off else -125
    r = e.run()
    txt = "\n".join(e.said)
    assert r is not None, txt
    ks = [int(ln.split("ks=")[1]) for ln in e.said if ln.startswith("s ")]
    assert ks and ks[-1] > 5560, txt            # (the DMA ran faster than its 5.49 MS/s)
    # (the header may go by while the carrier is seen; then the callsign comes from the terminator)
    assert any(ln.startswith(("Y call fr=9 ", "Y call fr=10 "))
               and ln.endswith(" vw=40/40 sf=40 miss=0 crc=d821 from=AG7EW") for ln in e.said), txt
    assert stored(e.said) == want(), txt
