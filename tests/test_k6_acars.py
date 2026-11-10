"""The K6's ACARS probe on the chip's own FSK modem (c/k6/mod/acarsfsk.c, docs/k6-acars.md):
modem/acars.py's test blocks, and the probe module on an emulated Cortex-M0 against the model of
the FD6818's packet mode that the APRS probes built (tests/test_k6_aprsclk.py modem_chip). The
model says nothing about whether the real chip starts on an ACARS block or follows an AM signal:
that is what the probe asks on the air. Here the module's own parts are checked: its settings
round, the timed reads at 2400 baud, the block reader for either sense of the tones, its lines
and the report on them, the registers put back. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_aprsclk import FDMA, K6, modem_chip, need_clang  # noqa: E402

from modem import acars  # noqa: E402


def test_block_and_sync_words():
    """a block reads back from its tone bits, a wrong bit fails its check, and the sync words
    are SYN SYN (and "+*" before them) as the tones a differential decoder sees"""
    assert (acars.SYNC2, acars.SYNC4) == (0x5C5C, 0x3FFE5C5C)
    for kind in "RK":
        t = acars.unit_tones(kind, "AG7EW TEST")
        s = "".join(map(str, t))
        w = f"{acars.SYNC2:016b}" if kind == "R" else "".join(f"{b:08b}" for b in acars.K_SYNC)
        at = s.index(w) + len(w)
        text, why = acars.read_block(t[at:])
        assert why == "ok" and text.endswith("\x02AG7EW TEST") and text.startswith("2AG7EW.."), (text, why)
        bad = t.copy()
        bad[at + 100] ^= 1
        assert acars.read_block(bad[at:])[0] is None
    b = acars.block("X")
    assert b[0] == 0x01 and b[-1] == 0x7F and all(bin(c).count("1") & 1 for c in b[:-3])   # odd parity
    assert acars.crc(b[1:-1]) == 0
    x, sent = acars.probe_audio(3.0)
    assert [k for k, _, _ in sent[:4]] == list("KRKR") and abs(np.abs(x).max() - 1) < 1e-3


def air_of(seconds, BASE):
    """the probe's units as the chip's 1200/2400 Hz receiver would slice them: tone bits at 2400
    baud (1 = 1200 Hz), silence between"""
    out, pos, k = [np.zeros(int(0.2 * 2400), int)], 0.2, 0
    while pos < seconds:
        kind = "KR"[k % 2]
        t = acars.unit_tones(kind, f"AG7EW TEST 440.850 {kind}{k:03d} THE K6 READS ACARS").astype(int)
        out += [t, np.zeros(int(0.25 * 2400), int)]
        pos += len(t) / 2400 + 0.25
        k += 1
    return np.repeat(np.concatenate(out), BASE // 2400)


def run(name, defs, pick_of, max_s):
    import k6_live_emu as le
    src = os.path.join(K6, "mod", name)
    code, entry = le.build("x", defs, src)
    hold = {}
    Chip = modem_chip(le, lambda r58: pick_of(hold["e"]), 4800)
    class Tuned(Chip):
        either = True                   # the sync word in either sense of the tones
        r = {**Chip.r, 0x47: 0}

        def on_stub(self, uc, addr, size, user):
            if self.names[(addr - self.stubs) // 16] == "tune":
                self.tunes.append((uc.reg_read(self.A.UC_ARM_REG_R0), uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)

    Tuned.tunes = []
    e = hold["e"] = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=max_s, level=7)
    r = e.run()
    assert r is not None, e.said[-5:]
    assert r[1] < 700, r
    assert e.empty == 0 and e.over == 0, (e.empty, e.over)       # the timed reads keep in step
    v = {}
    for ln in e.said:
        if ln[0] == "v":
            f = ln.split()
            v.setdefault(int(f[0][1:]), dict([("cfg", f[2]), ("sync", f[3])] + [kv.split("=") for kv in f[1:2] + f[4:]]))
    return e, r[0], v


@need_clang
def test_acarsfsk_fm_control_on_the_model():
    pytest.importorskip("unicorn")
    air = air_of(26, 4800)
    e, ret, v = run("acarsfsk.c", [("SLOT_MS", "2000u"), ("RUN_MS", "24500u")], lambda e: air, 26)
    assert e.said[0] == "acarsfsk start hz=440850000 slot=2000 n=12", e.said[:2]
    assert sorted(v) == list(range(12)), sorted(v)
    assert [v[n]["cfg"] for n in range(0, 12, 3)] == ["33e9", "33a9", "33a3", "33e3"]
    assert [v[n]["sync"] for n in range(3)] == ["076b", "5c5c", "3ffe"]
    for n in range(12):
        # (a sync word found by chance inside the other kind of unit opens no block)
        assert int(v[n]["sy"]) >= int(v[n]["ok"]) >= 1 and v[n]["soh"] == v[n]["ok"] and v[n]["f"] == "0", (n, v[n])
    assert e.said[-1] == f"acarsfsk end ok={ret}" and ret >= 12, e.said[-2:]
    rep = acars.report(["K6 MOD say " + ln for ln in e.said])
    caps = [ln for ln in rep if " #" in ln]
    good = [ln for ln in caps if " ok: AG7EW TEST 440.850 " in ln]
    assert len(good) >= 12 and len(good) >= len(caps) - 3, caps
    assert any(ln.startswith("v00 ") and " K0" in ln for ln in caps) and any(ln.startswith("v01 ") and " R0" in ln for ln in caps)
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x47, 0x3F):
        assert e.writes[reg][-1] == 0, hex(reg)         # put back as read (the emulator reads 0)
    assert set(e.writes[0x72]) == {0x60CA, 0} and e.writes[0x5D][0] == 0x4F00
    assert {0x10F8, 0x10F0} <= set(e.writes[0x59])


@need_clang
def test_acarsfsk_am_front_ends_on_the_model():
    """the AM build's round: six front ends by three modem settings and two sync words. In this
    model the modem hears the signal only while REG_47's AF source is AM (7), with the tones the
    other way up, and noise otherwise: the module must find it in front ends 1 and 2 only"""
    pytest.importorskip("unicorn")
    air = air_of(74, 4800) ^ 1
    noise = np.random.default_rng(3).integers(0, 2, len(air))
    e, ret, v = run("acarsfsk_am.c", [("SLOT_MS", "2000u"), ("RUN_MS", "72500u")],
                    lambda e: air if e.r[0x47] >> 8 & 15 == 7 else noise, 74)
    assert e.said[0] == "acarsfsk start hz=440850000 slot=2000 n=36" and sorted(v) == list(range(36)), e.said[:2]
    for n in range(36):
        assert v[n]["f"] == str(n // 6), (n, v[n])
        if n // 6 in (1, 2):
            assert int(v[n]["ok"]) >= 1 and v[n]["ok"] == v[n]["soh"], (n, v[n])
        else:
            assert v[n]["ok"] == "0", (n, v[n])
    assert {(440850000, 1), (440850000, 0), (440852500, 0), (440847500, 0), (440854000, 0)} == set(e.tunes), set(e.tunes)
    assert 0x0700 in e.writes[0x47] and e.writes[0x47][-1] == 0
    rep = acars.report(["K6 MOD say " + ln for ln in e.said])
    assert any("ok inverted: AG7EW TEST" in ln for ln in rep), rep[:5]
