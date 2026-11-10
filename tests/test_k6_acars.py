"""The K6's ACARS probe on the chip's own FSK modem (c/k6/mod/acarsfsk.c, docs/k6-acars.md):
modem/acars.py's test blocks, and the probe module on an emulated Cortex-M0 against the model of
the FD6818's packet mode that the APRS probes built (tests/test_k6_aprsclk.py modem_chip). The
model says nothing about whether the real chip starts on an ACARS block or follows an AM signal:
that is what the probe asks on the air. Here the module's own parts are checked: its settings
round, the timed reads at 2400 baud, the block reader for either sense of the tones, its lines
and the report on them, the registers put back. SPDX-License-Identifier: MIT"""
import os
import struct
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


def test_plain_samples_and_back():
    """the plain FSK receiver's view of a block (two samples a bit, as an ideal slicer of the
    sign gives them) reads back, from words that start anywhere in the pre-key; one sample
    lost fails the check"""
    t = acars.unit_tones("R", "AG7EW TEST PLAIN")
    x = acars.plain_samples(t)
    tt, odd = acars.plain_tones(x)
    assert odd == 0 and list(tt) == list(t)
    bits = "".join(map(str, x)) + "1100" * 8              # (the chip's packet goes on after the block)
    assert bits.startswith("10" * 100)                    # the pre-key: the sync word AAAA
    for at in (16, 37, 190):
        words = [int(bits[j:j + 16], 2) for j in range(at, len(bits) - 15, 16)]
        text, why, odd = acars.plain_read(words)
        assert why == "ok" and odd <= 3 and text.endswith("\x02AG7EW TEST PLAIN"), (at, text, why, odd)
    words = [int((bits[:700] + bits[701:])[j:j + 16], 2) for j in range(16, len(bits) - 16, 16)]
    assert acars.plain_read(words)[0] is None


@need_clang
def test_dfskcap_acars_on_the_model():
    """dfskcap_acars (dfskcap.c with DFSK_ACARS) on the emulated M0: four front ends on 131.550
    MHz. In this model the receiver hears the blocks only while REG_47's AF source is AM (7),
    as two samples a bit, and noise otherwise, with a sync word by chance now and then. Every
    capture gets a c line; only those that go on like a pre-key get their words, and the report
    reads the blocks from them. The RSSI rises with every block whatever the front end: each
    is a b line, and the v lines count them. The registers are put back."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_acars.c")
    code, entry = le.build("x", [("FE_S", "5"), ("WAIT_MS", "200u"), ("QN", "2u")], src)
    full, _ = k6mod.build(src)
    plain, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap.c"))
    assert len(full) < 3200 and len(plain) < 2100, (len(full), len(plain))
    six, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap_acars6.c"))      # the 6 m bench build
    assert len(six) < 3200 and struct.pack("<I", 50650000) in six and struct.pack("<I", 131550000) not in six
    assert len(six) > len(full)                                            # its six front ends
    total, BASE = 21.5, 4800
    rng = np.random.default_rng(11)
    n = int(total * BASE)
    noise = rng.integers(0, 2, n)
    air = noise.copy()
    on = np.zeros(n, bool)
    for i in range(BASE // 2, n - BASE, 3 * BASE // 2):           # a sync word in the noise every 1.5 s
        noise[i:i + 16] = [1, 0] * 8
    t, k = 0.4, 0
    while t + 1.0 < total:
        x = acars.plain_samples(acars.unit_tones("R", f"AG7EW TEST 131.550 R{k:03d} RECEIVE ONLY"))
        i = int(t * BASE)
        air[i:i + len(x)] = x
        on[i:i + len(x)] = True
        t, k = t + len(x) / BASE + 0.9, k + 1
    hold = {}
    Chip = modem_chip(le, lambda r58: air if hold["e"].r[0x47] >> 8 & 15 == 7 else noise, BASE)

    class Tuned(Chip):
        long5d, early_done = True, True
        r = {**Chip.r, 0x47: 0}

        def rssi(self):
            return -84 if on[int(self.cyc / 96e6 * BASE) % n] else -117 - int(self.cyc // 9600) % 3

        def on_stub(self, uc, addr, size, user):
            if self.names[(addr - self.stubs) // 16] == "tune":
                self.tunes.append((uc.reg_read(self.A.UC_ARM_REG_R0), uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)

    Tuned.tunes = []
    e = hold["e"] = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
                          scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=131550000 n=4" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    q = [dict(kv.split("=") for kv in ln.split()[1:]) for ln in e.said if ln.startswith("q ")]
    assert 1 <= len(q) <= 2 and all(x["p"] in ("-117", "-118") and x["f"] in ("-118", "-119") for x in q), q
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[3:] for ln in e.said if ln.startswith("s ")] == [["cfg=00e9", "b=c195", "sy=aaaa", "l=ff20"]] * 4
    assert e.tunes[-4:] == [(131550000, 1), (131550000, 1), (131552500, 0), (131550000, 0)], e.tunes
    c = {(int(ln[1]), int(ln.split()[1])): dict(kv.split("=") for kv in ln.split()[2:])
         for ln in e.said if ln[0] == "c"}
    d = {(int(ln[1]), int(ln.split()[1])) for ln in e.said if ln[0] == "d" and ln[1].isdigit()}
    v = {int(ln[1]): dict(kv.split("=") for kv in ln.split()[1:]) for ln in e.said if ln[0] == "v"}
    assert sorted(v) == [0, 1, 2, 3] and ret == len(c) == sum(int(x["n"]) for x in v.values()), v
    b = [(int(ln[1]), dict(kv.split("=") for kv in ln.split()[2:])) for ln in e.said if ln[0] == "b"]
    edges = np.flatnonzero(np.diff(on.astype(int)) == 1) / BASE * 1000
    starts = [int(ln.split()[2][2:]) for ln in e.said if ln.startswith("s ")]
    for st in range(4):       # every block's carrier, in every front end
        mine = [x for k, x in b if k == st]
        assert v[st]["p"] == "-84" and v[st]["f"] in ("-119", "-118"), v[st]
        assert len(mine) >= 2 and int(v[st]["b"]) - len(mine) in (0, 1), (st, mine, v[st])
        for x in mine:
            near = edges[np.argmin(abs(edges - int(x["t"])))]
            assert x["r"] == "-84" and abs(near - int(x["t"])) <= 400, (x, near)   # a dump's lines hold the polls up
        assert len(mine) >= sum((edges > starts[st] + 300) & (edges < starts[st] + 3500)), (st, mine, edges)
    for st in range(4):
        mine = {k: x for k, x in c.items() if k[0] == st}
        assert len(mine) >= 2, (st, c)
        assert all(240 <= int(x["w"]) <= 256 for x in mine.values()), mine
        if st in (1, 3):      # the blocks: their pre-key goes on, and they are dumped
            assert all(x["k"] in "34" for x in mine.values()) and set(mine) <= d, mine
        else:                 # noise: a c line and no words
            assert all(x["k"] in "012" for x in mine.values()) and not set(mine) & d, mine
    rep = acars.plain_report(["K6 MOD say " + ln for ln in e.said])
    got = [ln for ln in rep if "block [" in ln]
    assert len(got) == len(d) >= 6 and all("AG7EW TEST 131.550 R0" in ln and ln.endswith(" RECEIVE ONLY]") for ln in got), rep
    assert sum(ln[0] == "c" for ln in rep) == len(c) and rep[0] == e.said[0] and rep[-1] == e.said[-1]
    assert sum(ln[0] == "b" for ln in rep) == len(b)
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47):
        assert e.writes[reg][-1] == 0, hex(reg)                  # put back as read (the emulator reads 0)
    assert 0x0700 in e.writes[0x47] and e.writes[0x5D][0] == 0xFF20


def test_dfskcap_acars6_narrow_front_ends_on_the_model():
    """the 6 m bench build's eight front ends (DFSK_NARROW): all in the narrow filter and FM,
    seven with the AFC off (REG_73 bit 4) tuned from 300 Hz low to 300 Hz high in steps of 100,
    the last as tuned with the AFC on. Noise only here: every capture gets its c line and
    (DFSK_DUMP_ALL) its words up to the limit; REG_47 is left alone and REG_73 put back."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    defs = [("DFSK_ACARS", "1"), ("DFSK_NARROW", "1"), ("DFSK_DUMP_ALL", "1"), ("PROBE_HZ", "50650000u"),
            ("FE_S", "2"), ("WAIT_MS", "200u"), ("QN", "2u")]
    code, entry = le.build("x", defs, os.path.join(K6, "mod", "dfskcap.c"))
    six, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap_acars6.c"))
    total, BASE = 16.5, 4800
    rng = np.random.default_rng(5)
    noise = rng.integers(0, 2, int(total * BASE))
    for i in range(BASE // 2, len(noise) - BASE, BASE):           # a sync word in the noise every second
        noise[i:i + 16] = [1, 0] * 8
    Chip = modem_chip(le, lambda r58: noise, BASE)

    class Tuned(Chip):
        long5d, early_done = True, True
        r = {**Chip.r, 0x47: 0}

        def on_stub(self, uc, addr, size, user):
            if self.names[(addr - self.stubs) // 16] == "tune":
                self.tunes.append((uc.reg_read(self.A.UC_ARM_REG_R0), uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(six))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=50650000 n=8" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[1] for ln in e.said if ln.startswith("s ")] == list("01234567")
    assert e.tunes[-8:] == [
        (50649700, 0),
        (50649800, 0),
        (50649900, 0),
        (50650000, 0),
        (50650100, 0),
        (50650200, 0),
        (50650300, 0),
        (50650000, 0),
    ], e.tunes
    assert [w >> 8 & 15 for w in e.writes[0x47]].count(7) == 0 and e.writes[0x47][-1] == 0, e.writes[0x47]
    assert e.writes[0x73] == [0x10] * 7 + [0, 0], e.writes[0x73]  # off seven times, on, as found
    c = {(int(ln[1]), int(ln.split()[1])) for ln in e.said if ln[0] == "c"}
    d = {(int(ln[1]), int(ln.split()[1])) for ln in e.said if ln[0] == "d" and ln[1].isdigit()}
    v = {int(ln[1]): dict(kv.split("=") for kv in ln.split()[1:]) for ln in e.said if ln[0] == "v"}
    assert sorted(v) == [0, 1, 2, 3, 4, 5, 6, 7] and ret == len(c) == sum(int(x["n"]) for x in v.values()) >= 8, v
    assert d == c, (sorted(c - d), sorted(d - c))                 # every capture's words

