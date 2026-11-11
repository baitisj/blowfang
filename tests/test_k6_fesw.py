"""The front-end switch probe (c/k6/mod/fesw.c, modem/fesw.py, docs/k6-fesw.md) on the emulated
M0, with a model of the two pins (GPIOA's ODR, BSRR and BRR) and of a receiver that loses
OTHER dB on the wrong path and NEITHER dB with both paths off. What the real board loses is
the bench's question. Checked here: the lines, the report, that the two pins are never high
together, that the path the firmware picked is put back after every frequency, and that a
board whose pins are not as expected is left alone. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_aprsclk import FDMA, K6, need_clang  # noqa: E402

from modem import fesw  # noqa: E402

pytestmark = need_clang

UHF, VHF = 1 << 13, 1 << 14
ODR, BSRR, BRR = 0x48000014, 0x48000018, 0x48000028
OTHER, NEITHER = 31, 47
# what the model's air holds: dBm on each frequency with the right path, and its own floor
AIR = {144390: -102, 162550: -70, 440925: -118, 440850: -121}
FLOOR = -123


def run(rounds, tune_pins=None, carrier_from=None):
    """fesw on the model -> (return value, lines, the emulator). tune_pins: what api->tune
    leaves on the pins (default: as Aura, VHF below 300 MHz). carrier_from: the round from
    which 440.850 holds the bench's carrier (-21 dBm)"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    code, entry = le.build("x", [("FE_ROUNDS", f"{rounds}u"), ("FE_STEP_US", "2000u"), ("FE_SETTLE_MS", "20u")],
                           os.path.join(K6, "mod", "fesw.c"))

    class Board(le.Emu):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.reg[ODR] = 0x0008                        # another pin of port A, to be left alone
            self.khz, self.tunes, self.odrs, self.round = 0, [], [], 0

        def mmio_w(self, uc, off, size, value, page):
            a = page + off
            if a in (BSRR, BRR):
                assert value & ~(UHF | VHF) == 0, hex(value)    # the two pins only
                o = self.reg[ODR]
                self.reg[ODR] = o | value if a == BSRR else o & ~value
                assert self.reg[ODR] & (UHF | VHF) != UHF | VHF  # never both
                self.odrs.append(self.reg[ODR] & (UHF | VHF))
                return
            assert a != ODR
            super().mmio_w(uc, off, size, value, page)

        def on_stub(self, uc, addr, size, user):
            name, r0 = self.names[(addr - self.stubs) // 16], uc.reg_read(self.A.UC_ARM_REG_R0)
            if name == "tune":
                assert self.tunes == [] or self.reg[ODR] & (UHF | VHF) == self.own, "not put back"
                self.khz = r0 // 1000
                self.round = len(self.tunes) // len(AIR)
                self.tunes.append((r0, uc.reg_read(self.A.UC_ARM_REG_R1)))
                self.own = (tune_pins or (lambda k: VHF if k < 300000 else UHF))(self.khz)
                self.reg[ODR] = self.reg[ODR] & ~(UHF | VHF) | self.own
            super().on_stub(uc, addr, size, user)
            if name == "fd_read" and r0 == 0x65:
                uc.reg_write(self.A.UC_ARM_REG_R0, 0x1200 | 40)   # the noise meter, upper bits set
            if name == "fd_read" and r0 == 0x63:
                uc.reg_write(self.A.UC_ARM_REG_R0, 0x0300 | 17)

        def rssi(self):
            lvl = AIR[self.khz]
            if self.khz == 440850 and carrier_from is not None and self.round >= carrier_from:
                lvl = -21
            p = self.reg[ODR] & (UHF | VHF)
            lvl -= 0 if p == self.own else OTHER if p else NEITHER
            return max(lvl, FLOOR)

    e = Board(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=rounds * 4 * 3 * 0.25 + 5, level=8)
    r = e.run()
    assert r is not None, e.said[-5:]
    assert r[1] < 700, r[1]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    return r[0], e.said, e


def test_fesw_on_the_model():
    ret, said, e = run(4, carrier_from=2)
    assert ret == 4 * 4 * 3 and said[0] == "fesw start n=4 rounds=4 odr=0" and said[-1] == "fesw end n=48", said
    assert e.tunes == [(k * 1000, 0) for k in AIR] * 4, e.tunes
    assert e.reg[ODR] == 0x0008 | UHF                     # as the last tune left it; bit 3 untouched
    assert 0x13 not in e.writes and 0x7E not in e.writes and e.writes == {}, e.writes   # no chip register
    fe = [ln.split() for ln in said if ln.startswith("fe ")]
    assert [ln[1:4] for ln in fe] == [[str(r), f"f={k}", f"s={s}"] for r in range(4) for k in AIR for s in range(3)]
    assert not any(ln[-1] == "x" for ln in fe)
    # the line's numbers: the model's levels, the masks of the two meters
    assert said[1].split()[5:] == ["r=-102", "-102", "-102", "n=40", "g=17"], said[1]
    assert said[2].split()[5:] == ["r=-123", "-123", "-123", "n=40", "g=17"], said[2]
    rep = fesw.report(["K6 MOD say " + ln for ln in said])
    assert rep == [
        "fe 144390 all: 4 rounds, RSSI -102 / -123 / -123 dBm (own, other, neither), loss 21 and 21 dB, "
        "noise 40 / 40 / 40",
        f"fe 162550 all: 4 rounds, RSSI -70 / {-70 - OTHER} / {-70 - NEITHER} dBm (own, other, neither), "
        f"loss {OTHER} and {NEITHER} dB, noise 40 / 40 / 40",
        "fe 440850 carrier: 2 rounds, RSSI -21 / -52 / -68 dBm (own, other, neither), loss 31 and 47 dB, "
        "noise 40 / 40 / 40",
        "fe 440850 no carrier: 2 rounds, RSSI -121 / -123 / -123 dBm (own, other, neither), loss 2 and 2 dB, "
        "noise 40 / 40 / 40",
        "fe 440925 all: 4 rounds, RSSI -118 / -123 / -123 dBm (own, other, neither), loss 5 and 5 dB, "
        "noise 40 / 40 / 40"], rep
    # each state's pins: the one the firmware picked, the other, neither, and the first again
    want = []
    for k in list(AIR) * 4:
        own = VHF if k < 300000 else UHF
        want += [own, own, 0, own ^ (UHF | VHF), 0, 0, own]   # BRR then BSRR a state; BRR alone for neither
    assert e.odrs == want, (e.odrs[:14], want[:14])


def test_fesw_leaves_an_unknown_board_alone():
    ret, said, e = run(2, tune_pins=lambda k: 0)
    assert ret == (-5) & 0xFFFFFFFF and said == ["fesw start n=4 rounds=2 odr=0", "fesw pins=0 f=144390"], said
    assert e.odrs == [] and e.reg[ODR] == 0x0008
    ret, said, e = run(2, tune_pins=lambda k: UHF | VHF if k > 300000 else VHF)
    assert ret == (-5) & 0xFFFFFFFF and said[-1] == f"fesw pins={UHF | VHF} f=440925", said
    assert e.odrs[-1] == VHF                              # 162.550's path put back before the next tune


def test_report_marks_moved_pins():
    lines = ["fe 0 f=144390 s=0 t=10 r=-103 -102 -101 n=40 g=17",
             "fe 0 f=144390 s=1 t=20 r=-103 -102 -101 n=40 g=17 x",
             "fe 0 f=144390 s=2 t=30 r=-120 -119 -118 n=60 g=30"]
    assert fesw.report(lines) == ["fe 144390 all: 1 rounds, RSSI -102 / -102 / -119 dBm (own, other, neither), "
                                  "loss 0 and 17 dB, noise 40 / 40 / 60, pins moved in 1"]
    assert fesw.report(lines[:2]) == ["fe 144390 all: no round with all three states"]
