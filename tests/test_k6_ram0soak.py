"""ram0soak (c/k6/mod/ram0soak.c) on the emulated K6: the watchdog armed first, the bootloader's
RAM filled from the top down a block at a time, txdog's crash note naming the block, and the
planned reset at the end (Jeff, 2026-11-19: no hand reboots)."""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
K6 = os.path.join(HERE, "..", "c", "k6")

NOTE = 0x20005FF0
AIRCR = 0xE000ED0C


def soak(corrupt=None):
    """-> (the emulator, the reset's AIRCR value or None, the notes seen at each say).
    corrupt: (after this many say lines, an address) to break the pattern there"""
    if not (shutil.which("clang") and shutil.which("ld.lld")):
        pytest.skip("needs clang and ld.lld")
    pytest.importorskip("unicorn")
    import numpy as np
    import test_k6_dmr as T
    import k6_live_emu as le
    code, entry = T.build([("HOLD_S", "3u"), ("STEP_MS", "20u")], os.path.join(K6, "mod", "ram0soak.c"))
    e = le.Emu(code, entry, np.zeros(1000, np.uint8), 0, 5.49e6, max_s=30.0, level=2)
    got = {"reset": None}
    notes = []

    def w(uc, off, size, value, page):
        if page + off == AIRCR:
            got["reset"] = value
            e.max_cyc = 0                       # the radio resets here
        e.mmio_w(uc, off, size, value, page)

    for page in (0x40003000, 0xE000E000):
        e.mu.mmio_map(page, 0x1000, e.mmio_r, page, w, page)
    said = e.said

    class Watch(list):
        def append(self, s):
            notes.append(tuple(int.from_bytes(e.mu.mem_read(NOTE + 4 * i, 4), "little") for i in range(3)))
            super().append(s)
            if corrupt and len(self) == corrupt[0]:
                e.mu.mem_write(corrupt[1], b"\0\0\0\0")

    e.said = Watch(said)
    e.run()
    return e, got["reset"], notes


def test_ram0soak_arms_the_watchdog_marks_each_block_and_resets():
    e, reset, notes = soak()
    txt = "\n".join(e.said)
    assert e.said[0].startswith("r0 start csr="), txt
    assert e.reg[0x40003004] == 4 and e.reg[0x40003008] == 3750, txt    # IWDG about 6 s
    assert e.reg[0x40003000] == 0xAAAA, txt                             # fed
    blocks = [ln for ln in e.said if ln.startswith("r0 b=")]
    assert blocks == [f"r0 b={b:x} bad=000" for b in range(15, 0, -1)], txt
    # each block's line is said while txdog's note names that block (pc 0a30000N)
    for ln, nt in zip(e.said[1:16], notes[1:16]):
        assert nt == (0xFA170001, 0x0A300000 | int(ln[5], 16), 0x0A3050AC), (ln, nt)
    assert "r0 done bad=000 first=00000000 blk=0000 lo=same uv=same" in e.said, txt
    assert e.said[-1] == "r0 reset", txt
    assert notes[-1] == (0, 0x0A300000, 0x0A3050AC), notes[-1]          # cleared before the reset
    assert reset == 0x05FA0004


def test_ram0soak_says_where_the_pattern_broke():
    e, reset, _ = soak(corrupt=(5, 0x20000E10))     # after block c's line: a word of block e
    txt = "\n".join(e.said)
    assert "r0 b=b bad=001" in e.said, txt
    done = next(ln for ln in e.said if ln.startswith("r0 done"))
    assert done.startswith("r0 done bad=001 first=20000e10 blk=4000"), txt
    assert reset == 0x05FA0004
