"""DMR's sync words through the chip's plain FSK receiver (c/k6/mod/dfskcap_dmr.c,
modem/dmrsign.py, docs/k6-dmr.md): the signs of the sync words' symbols as the chip's sync
word. The model of the FD6818's packet mode (tests/test_k6_aprsclk.py modem_chip) gives the
symbols' signs as its bits; whether the real chip's slicer and clock do is the bench's question.
Checked here: the pattern, the module's settings round and round, its lines, the report's later
syncs, the registers put back. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_aprsclk import FDMA, K6, modem_chip, need_clang  # noqa: E402

from modem import dmrsign  # noqa: E402

pytestmark = need_clang

# sync word, a= (REG_2B's bits), the wide filter, the AFC off
DMR = [("9b4d", 0x0700, 0, 1),
       ("64b2", 0x0700, 0, 1),
       ("9b4d", 0, 0, 1),
       ("64b2", 0, 0, 1),
       ("9b4d", 0x0700, 1, 1),
       ("64b2", 0x0700, 1, 1),
       ("9b4d", 0x0700, 0, 0),
       ("64b2", 0x0700, 0, 0)]


def test_sync_signs():
    """the BS data sync's symbols are outer ones and their signs are 439B4D; a word with an
    inner symbol is refused; a stream's later syncs are found every 144 bits, either way up"""
    assert int("".join(map(str, dmrsign.SIGNS)), 2) == 0x439B4D
    assert int("".join(map(str, dmrsign.signs(0x755FD7DF75F7))), 2) == 0x439B4D ^ 0xFFFFFF   # the voice sync
    with pytest.raises(AssertionError):
        dmrsign.signs(0xDFF57D75DF5C)
    rng = np.random.default_rng(3)
    air = dmrsign.air_signs(8, rng, voice_every=3)
    assert list(air[66:90]) == list(dmrsign.SIGNS)
    cap = air[90:90 + 1024]                                       # what follows the first sync
    assert dmrsign.next_syncs(cap) == [24, 0, 24, 24, 0, 24, 24]
    assert dmrsign.next_syncs(1 - cap) == [0, 24, 0, 0, 24, 0, 0]
    assert all(6 <= x <= 18 for x in dmrsign.next_syncs(rng.integers(0, 2, 1024).astype(np.int8)))


def test_dfskcap_dmr_on_the_model():
    """dfskcap_dmr on the emulated M0: eight settings of 0.8 s, three times round here. The
    air is a repeater's bursts as signs, the first half one way up and the second half the
    other, so the settings with 9b4d find the syncs in the first and those with 64b2 in the
    second; the report finds the later syncs in every such capture. The registers are put
    back, the tuning is the repeater's in the filter each setting names"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_dmr.c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_NARROW", "1"), ("DFSK_DMR", "1"), ("DFSK_ROUNDS", "3u"),
                                 ("DFSK_DUMP_ALL", "1"), ("PROBE_HZ", "440925000u"), ("SLOT_SCALE", "100u"),
                                 ("FE_S", "8"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, BASE = 3 * 8 * 0.8 + 4, 4800
    rng = np.random.default_rng(7)
    half = dmrsign.air_signs(int(total * BASE / 144 / 2) + 1, rng, voice_every=6)
    air = np.concatenate([half, 1 - half, half])
    Chip = modem_chip(le, lambda r58: air, BASE)

    class Tuned(Chip):
        long5d, early_done, ring = True, True, True
        r = {**Chip.r, 0x47: 0}

        def on_stub(self, uc, addr, size, user):
            if self.names[(addr - self.stubs) // 16] == "tune":
                self.tunes.append((uc.reg_read(self.A.UC_ARM_REG_R0), uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 12, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=440925000 n=8" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    s = [ln.split() for ln in e.said if ln.startswith("s ")]
    assert [ln[1:2] + ln[3:] for ln in s] == \
        [[str(i % 8), "cfg=00e9", "b=c195", "sy=" + DMR[i % 8][0], "l=7f00"] for i in range(24)], s
    assert e.tunes == [(440925000, 1)] + [(440925000, DMR[i % 8][2]) for i in range(24)], e.tunes
    assert e.writes[0x73][:24] == [0x10 if DMR[i % 8][3] else 0 for i in range(24)] and e.writes[0x73][-1] == 0
    rep = dmrsign.report(["K6 MOD say " + ln for ln in e.said])
    dmr = {int(ln.split()[0][3:]): ln for ln in rep if ln.startswith("dmr")}
    assert sorted(dmr) == list(range(8)), rep
    found = 0
    for fe, ln in dmr.items():
        assert f"sy={DMR[fe][0]} cfg=00e9: 3 slots" in ln, ln
        ok = int(ln.split(" captures, ")[1].split()[0])
        if ok:
            got, looked = (int(x) for x in ln.split("syncs ")[1].split(",")[0].split(";")[0].split(" of "))
            assert got == looked and f"signs right {24 * got} of {24 * got}" in ln, ln
            found += got
    assert found >= 40, rep                                       # most slots are in a half that suits them
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47, 0x73):
        assert e.writes[reg][-1] == 0, hex(reg)
