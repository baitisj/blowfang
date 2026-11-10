"""DMR mute inside EXPLORE's Listen for ID (c/k6/xdmr.c, docs/k6-dmr.md): xlisten's steps with the
DMR detector draining the GP0 DMA ring in their waits, on the emulated Cortex-M0 against the
modelled stream (tests/k6_live_emu.py). A repeater's DMR must be found within a step or two and
mute the speaker, FM voice must open it after the hold, the ring must keep up while the tone
detector is polled every 2 ms, and the stack (the 2 KB ring on it) must stay in budget.
Needs clang, ld.lld and unicorn. SPDX-License-Identifier: MIT"""
import os
import re
import shutil
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K6 = os.path.join(ROOT, "c", "k6")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem import k6mod  # noqa: E402
from sim.k6if import dmr, frontend as fe, study as st  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                reason="needs clang and ld.lld")

AF_FM = 0x6140
# a listen as EXPLORE runs it: xl_start, xd_start, steps until the time is up, xd_stop, xl_stop
HARNESS = r"""
#define K6_MODULE_LEVEL 2
#include "cwid.c"
#include "xlisten.c"
#include "xdmr.c"
int k6_main(const k6_api_t *api) {
  xl_t x;
  xd_t d;
  int r;
  xl_start(&x, api, 440925000u, RUN_S);
  xd_start(&d, api, 1, 0);
#ifdef KNOWN
  xd_known(&d);
#endif
  while ((r = xd_step(&d, &x)) != XL_TIME && r != XL_OK) {}
  r = xd_stop(&d);
  xl_stop(&x, 2);
  return r;
}
"""


def build(run_s, known=False):
    with tempfile.TemporaryDirectory() as d:
        w = os.path.join(d, "w.c")
        with open(w, "w") as f:
            f.write(f"#define RUN_S {run_s}u\n#include <stdint.h>\n")
            if known:
                f.write("#define KNOWN 1\n")
            f.write(HARNESS.replace('#include "', f'#include "{K6}/'))
        return k6mod.build(w)


def stream(s, cn0, pre_s, post_s, seed=1):
    bits = fe.receive(s, st.FZ, cn0, seed=seed, L=0, fdma=fe.FDMA)
    rng = np.random.default_rng(seed + 1)
    a, b = int(pre_s * fe.FDMA), int(post_s * fe.FDMA)
    ib = np.concatenate([rng.integers(0, 2, a), bits, rng.integers(0, 2, b)]).astype(np.uint8) * 2
    return ib, a, a + len(bits)


def listen(s, run_s=3, cn0=60.0, pre_s=0.0, post_s=0.5, level=-85, tone=0, known=False, built=None,
           scratch=4096, dcs=None):
    """-> (DMR transmissions, say lines (ms, text), REG_47 writes (ms, value), stack bytes)"""
    import k6_live_emu as le

    class Emu(le.Emu):
        def on_stub(self, uc, addr, size, _):
            name, r0 = self.names[(addr - self.stubs) // 16], uc.reg_read(self.A.UC_ARM_REG_R0)
            # REG_47 reads as monitor_on left it (RX audio on the speaker)
            if name == "fd_read" and r0 in (0x47, 0x68, 0x69, 0x6A):
                self.tick(20 * 96)
                up = on <= self.sample_now() < off
                # REG_68: the sub-audio scan's CTCSS word for `tone` (0.1 Hz) while the carrier is up;
                # REG_69/6A: the DCS word `dcs` (23 bits), 69 bit 15 set when there is none
                if r0 == 0x47:
                    v = AF_FM
                elif r0 == 0x68:
                    v = tone * 206489 // 100000 + 1 if tone and up else 0x8000
                elif r0 == 0x69:
                    v = dcs >> 12 & 0xFFF if dcs is not None and up else 0x8000
                else:
                    v = dcs & 0xFFF if dcs is not None and up else 0
                uc.reg_write(self.A.UC_ARM_REG_R0, v)
                return
            if name == "fd_write" and r0 == 0x47:
                w47.append((round(self.cyc / 96000), uc.reg_read(self.A.UC_ARM_REG_R1)))
            if name == "say":
                at.append(round(self.cyc / 96000))
            return super().on_stub(uc, addr, size, _)

    w47, at = [], []

    code, entry = built or build(run_s, known)
    ib, on, off = stream(s, cn0, pre_s, post_s)
    e = Emu(code, entry, ib, on, fe.FDMA, 1.5, max_s=run_s + 1.0, scratch=scratch)
    e.rssi = lambda: level if on <= e.sample_now() < off else -120
    # paint 8 KB under the stack: the 2 KB ring and the listener's state are on it
    lo = e.sp0 - 8192
    e.mu.mem_write(lo, b"\xa5" * 8192)
    r = e.run()
    assert r is not None, "\n".join(e.said)
    # the module API's say keeps 60 characters; a longer line also overran xd_stop's buffer once
    assert all(len(ln) < 60 for ln in e.said), [ln for ln in e.said if len(ln) >= 60]
    stk = bytes(e.mu.mem_read(lo, 8192))
    used = 8192 - next(i for i, b in enumerate(stk) if b != 0xA5)
    return r[0], list(zip(at, e.said)), w47, used


def line(said, head):
    return next(((t, ln) for t, ln in said if ln.startswith(head)), (None, None))


SHUT, OPEN = AF_FM & ~0x0F00, AF_FM


def test_listen_mutes_a_repeaters_dmr():
    pytest.importorskip("unicorn")
    sy, _ = dmr.bs_symbols(2.0, ("voice", "idle"), seed=7)
    s, _ = dmr.dmr_baseband(sy, pre_s=0.0, post_s=0.0)
    n, said, w47, stack = listen(s, run_s=3)
    txt = "\n".join(ln for _, ln in said)
    print(txt, w47, stack)
    assert n == 1, txt
    _, on = line(said, "DMR on bs")
    assert int(on.split("+")[1]) <= 150, on          # inside the hold: the speaker never opened
    t_off, _ = line(said, "DMR off")
    assert t_off is not None, txt
    # shut from the start; opened only once DMR was over (the carrier ended at 2 s)
    assert w47[0][1] == SHUT and all(v == SHUT or t >= t_off for t, v in w47), (w47, t_off)
    _, end = line(said, "DMR end")
    assert int(re.search(r"lost=(\d+)", end)[1]) <= 3, end
    _, rate = line(said, "DMR rate")
    ks = int(re.search(r"ks=(\d+)", rate)[1])
    assert abs(ks - fe.FDMA / 1000) < 60, rate
    assert stack < 4096, stack


def test_listen_opens_for_fm_voice():
    pytest.importorskip("unicorn")
    a = dmr.speech("female_libri198_14s__original", 2.0)
    n, said, w47, _ = listen(dmr.fm_voice(a, ctcss=100.0), run_s=2, tone=1000)
    txt = "\n".join(ln for _, ln in said)
    assert n == 0, txt
    assert line(said, "CTCSS 1000")[1] is not None, txt     # two readings in a row: the tone
    assert "ctcss=1000/" in line(said, "DMR rate")[1], txt
    assert line(said, "DMR on")[1] is None, txt
    # shut for the hold, then open about 150 ms in and staying so
    assert w47[0][1] == SHUT and w47[1][1] == OPEN, w47
    assert w47[1][0] - w47[0][0] < 300 and all(v == OPEN for _, v in w47[1:]), w47


def test_a_late_dmr_key_up_mutes_an_open_speaker():
    """the repeater keying up DMR while the speaker is open (FM, or silence, before it)"""
    pytest.importorskip("unicorn")
    sy, _ = dmr.bs_symbols(1.0, ("idle", "idle"), seed=3)
    s, _ = dmr.dmr_baseband(sy, pre_s=0.0, post_s=0.0)
    n, said, w47, _ = listen(s, run_s=3, pre_s=1.0, post_s=0.5)
    txt = "\n".join(ln for _, ln in said)
    assert n == 1, txt
    _, on = line(said, "DMR on bs")
    # the carrier came up ~1.0 s into the stream: DMR within a step or two of it
    assert 1000 <= int(on.split("+")[1]) <= 1200, on
    t_open = next(t for t, v in w47 if v == OPEN)
    t_shut = next(t for t, v in w47 if v == SHUT and t > t_open)
    assert t_open < 400 and 1000 <= t_shut <= 1250, w47


def test_a_known_dmr_row_stays_shut_through_a_late_key_up():
    """Jeff 2026-11-08: a row a listen heard DMR on before starts muted. The speaker stays shut
    across the dead air before the key-up (the hold alone would have opened it at 150 ms), the
    DMR, and until XD_CLEAR_MS after the last sync"""
    pytest.importorskip("unicorn")
    sy, _ = dmr.bs_symbols(1.0, ("idle", "idle"), seed=3)
    s, _ = dmr.dmr_baseband(sy, pre_s=0.0, post_s=0.0)
    n, said, w47, _ = listen(s, run_s=6, pre_s=1.0, post_s=0.5, known=True)
    txt = "\n".join(ln for _, ln in said)
    assert n == 1 and line(said, "DMR known")[1] is not None, txt
    t_off, _ = line(said, "DMR off")
    t_open = next((t for t, v in w47 if v == OPEN), None)
    # the last sync ~2.0 s in: open no sooner than 3 s after it, and never before
    assert w47[0][1] == SHUT and t_open is not None and t_open >= 4900, (w47, t_off)
    assert all(v == SHUT for t, v in w47 if t < t_open), w47


def test_a_known_dmr_row_opens_for_fm_voice_after_the_clear_time():
    """an FM user on a mixed-mode repeater marked DMR: heard, but only after XD_CLEAR_MS"""
    pytest.importorskip("unicorn")
    a = dmr.speech("female_libri198_14s__original", 4.0)
    n, said, w47, _ = listen(dmr.fm_voice(a, ctcss=100.0), run_s=4, tone=1000, known=True)
    txt = "\n".join(ln for _, ln in said)
    assert n == 0 and line(said, "DMR on")[1] is None, txt
    t_open = next((t for t, v in w47 if v == OPEN), None)
    assert w47[0][1] == SHUT and t_open is not None and 3000 <= t_open <= 3300, w47



def build_module(run_s):
    """c/k6/mod/xdmrlisten.c, the same listen as a RAM module (ring and states in the scratch)"""
    with tempfile.TemporaryDirectory() as d:
        w = os.path.join(d, "w.c")
        with open(w, "w") as f:
            f.write(f'#define RUN_S {run_s}u\n#include "{K6}/mod/xdmrlisten.c"\n')
        return k6mod.build(w)


def test_xdmrlisten_module_mutes_a_repeaters_dmr_off_the_stack():
    """the module (for measuring the detector on the radio without a flash) hears what the
    EXPLORE listen hears, fits a voice image's 11,776-byte block, keeps the ring off the ~3 KB
    module stack, and refuses EXPLORE's 5,376-byte block with a line saying why"""
    pytest.importorskip("unicorn")
    sy, _ = dmr.bs_symbols(2.0, ("voice", "idle"), seed=7)
    s, _ = dmr.dmr_baseband(sy, pre_s=0.0, post_s=0.0)
    built = build_module(3)
    n, said, _, stack = listen(s, run_s=3, built=built, scratch=(11776 - 24 - len(built[0])) & ~3)
    txt = "\n".join(ln for _, ln in said)
    print(len(built[0]), stack, txt)
    assert n == 1, txt
    _, end = line(said, "DMR end")
    assert int(re.search(r"lost=(\d+)", end)[1]) <= 3, end
    assert line(said, "DMR diag e=")[1] is not None, txt
    assert stack < 1536, stack                        # the listener's state is on it, the ring is not
    n, said, _, _ = listen(s, run_s=3, built=built, scratch=(5376 - 24 - len(built[0])) & ~3 if
                           5376 - 24 > len(built[0]) else 0)
    assert n == (1 << 32) - 6 and said[0][1].startswith("xdmr scratch "), said


def test_fdtime_module_times_the_api_calls():
    """c/k6/mod/fdtime.c: the emulator's 20 us register reads come out as 20.0 us (0.1 us units)"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    code, entry = k6mod.build(os.path.join(K6, "mod", "fdtime.c"))
    e = le.Emu(code, entry, np.zeros(int(fe.FDMA * 2), np.uint8), 0, fe.FDMA, 1.5, max_s=3, level=2,
               fd_us=20)
    r = e.run()
    assert r is not None and r[0] == 0, e.said
    got = {ln.split()[1]: int(re.search(r"avg=(\d+)", ln)[1]) for ln in e.said}
    assert abs(got["rd0B"] - 200) <= 10 and abs(got["td"] - 400) <= 20, e.said
    assert all(len(ln) < 60 for ln in e.said), e.said


def test_fdirq_module_adds_the_interrupt_lines():
    """c/k6/mod/fdirq.c: fdtime's lines, then irq, td-cli and spin (the emulator has no interrupts)"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    code, entry = k6mod.build(os.path.join(K6, "mod", "fdirq.c"))
    e = le.Emu(code, entry, np.zeros(int(fe.FDMA * 2), np.uint8), 0, fe.FDMA, 1.5, max_s=3, level=2,
               fd_us=20)
    e.mu.mem_map(0xE000E000, 0x1000)                # NVIC, SysTick, SCB: reads as zeros
    r = e.run()
    assert r is not None and r[0] == 0, e.said
    names = [ln.split()[1] for ln in e.said]
    assert names == ["rd0B", "td", "ms", "tk", "irq", "syst=00000000", "td-cli", "spin"], e.said
    td = int(re.search(r"avg=(\d+)", e.said[6])[1])
    on, off = (int(v) for v in re.findall(r"=(\d+)", e.said[7]))
    assert abs(td - 400) <= 20 and on > 0 and off > 0, e.said
    assert all(len(ln) < 60 for ln in e.said), e.said


def test_fdtick_module_clears_tickint_first():
    """c/k6/mod/fdtick.c: SysTick CSR's TICKINT cleared and left so, then fdirq's lines"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    code, entry = k6mod.build(os.path.join(K6, "mod", "fdtick.c"))
    e = le.Emu(code, entry, np.zeros(int(fe.FDMA * 2), np.uint8), 0, fe.FDMA, 1.5, max_s=3, level=2,
               fd_us=20)
    e.mu.mem_map(0xE000E000, 0x1000)
    e.mu.mem_write(0xE000E010, (0x00010006).to_bytes(4, "little"))
    r = e.run()
    assert r is not None and r[0] == 0, e.said
    assert e.said[0] == "fdt tickint was=1" and len(e.said) == 9, e.said
    assert int.from_bytes(e.mu.mem_read(0xE000E010, 4), "little") == 0x00010004, e.said


def dcs_word(code, inv=False, turn=0):
    """DCS's 23-bit Golay word for `code` (the octal digits as 9 bits), inverted, turned"""
    d = 0x800 | code
    r = d << 11
    for b in range(22, 10, -1):
        if r >> b & 1:
            r ^= 0xC75 << (b - 11)
    w = (d | r << 12) ^ (0x7FFFFF if inv else 0)
    return (w >> turn | w << (23 - turn)) & 0x7FFFFF


def test_dcs_word_matches_the_standard_table():
    """the Golay word of DCS 023 as radios send it, so the test's words are right"""
    assert dcs_word(0o23) == 0x763813


@pytest.mark.parametrize("code,inv,turn,said", [
    (0o23, False, 0, "DCS 023"),
    (0o754, False, 7, "DCS 754"),
    (0o23, True, 15, "DCS 047"),                   # 023 inverted is 047: the normal reading first
])
def test_listen_reads_a_dcs_code(code, inv, turn, said):
    """c/k6/xdmr.c xd_ctcss: a DCS word in REG_69/6A, any turn, two alike in a row -> the code"""
    pytest.importorskip("unicorn")
    a = dmr.speech("female_libri198_14s__original", 2.0)
    n, lines, _, _ = listen(dmr.fm_voice(a), run_s=2, dcs=dcs_word(code, inv, turn))
    txt = "\n".join(t for _, t in lines)
    assert line(lines, said)[1] is not None, txt
    assert line(lines, "CTCSS ")[1] is None, txt


@pytest.mark.parametrize("r69,r6a,said", [
    (0x0FB1, 0x0C09, "DCS 023"),
    (0x0F9E, 0x0C41, "DCS 754"),
    (0x0827, 0x01FB, "DCS 047"),                   # 023 sent TX-reversed
])
def test_listen_reads_dcs_from_the_k6s_own_registers(r69, r6a, said):
    """REG_69/6A as Jeff's K6 read them on air (2026-11-10 06:45Z, ctcssprobe on 43a548d, the
    IC-7100 sending DTCS 023 N, 754 N and 023 TX-reversed; 127 of 127 polls were turns of the
    sent word): the same words through xd_ctcss give the code"""
    pytest.importorskip("unicorn")
    a = dmr.speech("female_libri198_14s__original", 2.0)
    w = ((r69 & 0xFFF) << 12 | (r6a & 0xFFF)) & 0x7FFFFF
    n, lines, _, _ = listen(dmr.fm_voice(a), run_s=2, dcs=w)
    txt = "\n".join(t for _, t in lines)
    assert line(lines, said)[1] is not None, txt
