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
    assert len(six) < 3800 and struct.pack("<I", 50650000) in six and struct.pack("<I", 131550000) not in six
    assert len(six) > len(full)                                            # its sixteen settings
    fm, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap_acarsb.c"))       # the clock sweep in FM
    assert len(full) < len(fm) < len(six) and struct.pack("<I", 440850000) in fm
    sets, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap_acarsc.c"))    # the settings in FM
    assert len(fm) < len(sets) < len(six) and struct.pack("<I", 440850000) in sets
    tone, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap_acarsd.c"))   # the tone receiver in FM
    assert len(sets) < len(tone) < len(six) and struct.pack("<I", 440850000) in tone
    tone2, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap_acarse.c"))  # its second round
    assert len(sets) < len(tone2) < len(tone) and struct.pack("<I", 440850000) in tone2
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


def run_six(defs, total, follow=False):
    """the 6 m bench build with defs on the emulated M0, noise only -> the emulator. follow: the
    chip's clock at the air's 4800 samples a second whatever REG_72 says (it follows the audio)"""
    import k6_live_emu as le
    from modem import k6mod
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_DUMP_ALL", "1"), ("PROBE_HZ", "50650000u"),
                                 ("FE_S", "2"), ("WAIT_MS", "200u"), ("QN", "2u")] + defs,
                           os.path.join(K6, "mod", "dfskcap.c"))
    six, _ = k6mod.build(os.path.join(K6, "mod", "dfskcap_acars6.c"))
    BASE = 4800
    rng = np.random.default_rng(5)
    noise = rng.integers(0, 2, int(total * BASE))
    for i in range(BASE // 2, len(noise) - BASE, BASE):           # a sync word in the noise every second
        noise[i:i + 16] = [1, 0] * 8
    Chip = modem_chip(le, lambda r58: noise, BASE)

    class Tuned(Chip):
        long5d, early_done = True, True
        r = {**Chip.r, 0x47: 0}
        if_now = 0x2AAB                                           # REG_3D reads back as written

        def on_stub(self, uc, addr, size, user):
            name, r0 = self.names[(addr - self.stubs) // 16], uc.reg_read(self.A.UC_ARM_REG_R0)
            if name == "tune":
                self.tunes.append((r0, uc.reg_read(self.A.UC_ARM_REG_R1)))
            if name == "fd_write" and r0 == 0x3D:
                self.if_now = uc.reg_read(self.A.UC_ARM_REG_R1)
            super().on_stub(uc, addr, size, user)
            if name == "fd_read" and r0 == 0x3D:
                uc.reg_write(self.A.UC_ARM_REG_R0, self.if_now)

    Tuned.tunes = []
    Tuned.times = np.arange(8 * len(noise)) / BASE if follow else None
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(six))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=50650000 n=8" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[1] for ln in e.said if ln.startswith("s ")] == list("01234567")
    c = {(int(ln[1]), int(ln.split()[1])) for ln in e.said if ln[0] == "c"}
    d = {(int(ln[1]), int(ln.split()[1])) for ln in e.said if ln[0] == "d" and ln[1].isdigit()}
    v = {int(ln[1]): dict(kv.split("=") for kv in ln.split()[1:]) for ln in e.said if ln[0] == "v"}
    assert sorted(v) == [0, 1, 2, 3, 4, 5, 6, 7] and ret == len(c) == sum(int(x["n"]) for x in v.values()) >= 8, v
    assert d == c, (sorted(c - d), sorted(d - c))                 # every capture's words
    return e


def test_dfskcap_acars6_narrow_front_ends_on_the_model():
    """the sixth run's eight front ends (DFSK_NARROW): all in the narrow filter and FM, seven
    with the AFC off (REG_73 bit 4) tuned from 300 Hz low to 300 Hz high in steps of 100, the
    last as tuned with the AFC on. REG_47 is left alone and REG_73 put back."""
    pytest.importorskip("unicorn")
    e = run_six([("DFSK_NARROW", "1")], 16.5)
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
    assert 0x3D not in e.writes or not e.writes[0x3D]


def test_dfskcap_acars6_zero_if_front_ends_on_the_model():
    """the 6 m bench build as it is (DFSK_ZIF): seven front ends at the zero IF (REG_3D = 0, the
    chip's double sideband mode) with the AF sources 5, 5 narrow, 5 tuned 2.4 and 1.0 kHz high,
    4, 7 and as found, then the IF as found in the narrow filter; the AFC off in all. Each s
    line says REG_3D as read back. At the end the registers are put back and the receiver is
    tuned once more for them to take hold."""
    pytest.importorskip("unicorn")
    e = run_six([("DFSK_ZIF", "1")], 16.5)
    assert e.tunes[-9:] == [
        (50650000, 1),
        (50650000, 0),
        (50652400, 1),
        (50651000, 1),
        (50650000, 1),
        (50650000, 1),
        (50650000, 1),
        (50650000, 0),
        (50650000, 1),
    ], e.tunes
    assert [w >> 8 & 15 for w in e.writes[0x47]][-9:] == [5, 5, 5, 5, 4, 7, 0, 0, 0], e.writes[0x47]
    assert e.writes[0x3D] == [0] * 7 + [0x2AAB] * 2, e.writes[0x3D]
    assert e.writes[0x73] == [0x10] * 8 + [0], e.writes[0x73]
    ifs = [ln.split()[-1] for ln in e.said if ln.startswith("s ")]
    assert ifs == ["if=0000"] * 7 + ["if=2aab"], ifs
    assert 0x30 not in e.writes or not e.writes[0x30]             # the tuning kept the IF: no second restart


SWEEP_72 = ["b=d1b7", "b=d9c8", "b=dfd5", "b=e5e1", "b=ebee", "b=f1fb", "b=f807", "b=fe14"]


def test_dfskcap_acars6_clock_sweep_on_the_model():
    """the 6 m bench build as it is (DFSK_ZIF and DFSK_SWEEP): eight front ends at the zero IF,
    wide, the AFC off, AF source 5, the receiver at 5200, 5400, 5550, 5700, 5850, 6000, 6150
    and 6300 baud. The registers are put back and the receiver tuned once more."""
    pytest.importorskip("unicorn")
    e = run_six([("DFSK_ZIF", "1"), ("DFSK_SWEEP", "1")], 16.5, follow=True)
    assert e.tunes[-9:] == [(50650000, 1)] * 9, e.tunes
    assert [w >> 8 & 15 for w in e.writes[0x47]][-9:] == [5] * 8 + [0], e.writes[0x47]
    assert e.writes[0x3D] == [0] * 8 + [0x2AAB], e.writes[0x3D]
    assert e.writes[0x73] == [0x10] * 8 + [0], e.writes[0x73]
    s = [ln.split() for ln in e.said if ln.startswith("s ")]
    assert [x[4] for x in s] == SWEEP_72 and all(x[-1] == "if=0000" for x in s), s
    assert [round(acars.baud_of(int(x[2:], 16))) for x in SWEEP_72] == [5200, 5400, 5550, 5700, 5850, 6000, 6150, 6300]


def test_follow_tones_reads_what_the_chip_gives():
    """the chip's plain FSK receiver as the seventh 6 m run showed it (chip_samples): the
    slicer turns over at the audio's peaks and the clock starts again at each turn, so an
    interval of 1.5 samples of 4800 is one sample at 4800 baud, like 1, and no block reads;
    at 5700 baud it is two, like 2, and follow_tones reads every block, with a little jitter
    on the turns too. plain_fit counts what the chip made of each interval."""
    rng = np.random.default_rng(3)
    text = "AG7EW TEST 440.850 R123 THE K6 READS ACARS"
    t = acars.unit_tones("R", text)
    head = acars.fit_head("AG7EW TEST 440.850 R")
    assert set(np.round(2 * np.diff(acars.turns(t))).astype(int)) == {2, 3, 4}

    def words(baud, at, jitter):
        x = np.concatenate([acars.chip_samples(t, baud, at, jitter, rng), np.zeros(40, np.int8)] * 3)[16:]
        x = np.concatenate([x, np.zeros(-len(x) % 16, np.int8)])
        return [int("".join(map(str, x[i:i + 16])), 2) for i in range(0, len(x), 16)]

    for at, bauds in ((0.45, (5400, 5550, 5700)), (0.55, (5700, 5850, 6000)), (0.65, (6000, 6150, 6300))):
        for baud in bauds:
            got, why, _ = acars.plain_blocks(words(baud, at, 0.02), baud)
            assert len(got) == 3 and all(g.endswith(text) for g in got), (at, baud, why, got)
            found, counts = acars.plain_fit(words(baud, at, 0.0), head)
            assert found == 3 and set(counts) == {(2, 1), (3, 2), (4, 2)}, (at, baud, found, counts)
    w = words(4800, 0.55, 0.0)
    assert acars.plain_blocks(w, 4800)[0] == [] and acars.plain_blocks(w, 5700)[0] == []
    found, counts = acars.plain_fit(w, head)
    assert found == 3 and set(counts) == {(2, 1), (3, 1), (4, 2)}, counts
    x, when = acars.chip_samples(t, 5700, 0.55, when=True)
    assert len(x) == len(when) and np.all(np.diff(when) > 0.5 / 5700) and np.all(np.diff(when) < 1.6 / 5700)
    assert abs(when[-1] - len(t) / 2400) < 2 / 4800


def test_dfskcap_acarsb_clock_sweep_on_the_model():
    """dfskcap_acarsb (DFSK_SWEEP in FM on 440.850 MHz) on the emulated M0. The model's chip
    gives the blocks as chip_samples has them at 5700 baud in every front end (a sample when
    its clock, which follows the audio, takes it; between the blocks 5700 samples a second),
    and an empty FIFO gives the word of eight reads before, as on the bench. The module reads
    the FIFO at 4800 baud's pace, never faster than the chip makes words: the report reads the
    blocks in every front end and its fit lines say what the chip made of each interval."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_acarsb.c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_SWEEP", "1"), ("DFSK_DUMP_ALL", "1"),
                                 ("PROBE_HZ", "440850000u"), ("FE_S", "3"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, CHIP = 25.0, 5700
    bits, when, t, k = [], [], 0.3, 0
    gap0 = np.arange(0, 0.3, 1 / CHIP)
    bits.append(np.zeros(len(gap0), np.int8)), when.append(gap0)
    while t < total + 9:
        x, w = acars.chip_samples(acars.unit_tones("R", f"AG7EW TEST 440.850 R{k:03d} THE K6 READS ACARS"), CHIP, when=True)
        g = np.arange(1, int(0.25 * CHIP)) / CHIP
        bits += [x, (np.arange(len(g)) % 37 == 36).astype(np.int8)]   # the seventh run's silence: a lone bit now and then
        when += [t + w, t + w[-1] + g]
        t, k = t + w[-1] + g[-1] + 1 / CHIP, k + 1
    air, T = np.concatenate(bits), np.concatenate(when)
    assert np.all(np.diff(T) > 0)
    Chip = modem_chip(le, lambda r58: air, 4800)

    class Tuned(Chip):
        long5d, early_done, ring = True, True, True
        r = {**Chip.r, 0x47: 0}
        times = T

        def on_stub(self, uc, addr, size, user):
            if self.names[(addr - self.stubs) // 16] == "tune":
                self.tunes.append((uc.reg_read(self.A.UC_ARM_REG_R0), uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=440850000 n=8" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[3:] for ln in e.said if ln.startswith("s ")] == [["cfg=00e9", b, "sy=aaaa", "l=ff20"] for b in SWEEP_72]
    assert e.tunes[-8:] == [(440850000, 1)] * 8, e.tunes
    assert 0x3D not in e.writes or not e.writes[0x3D]
    assert 0x73 not in e.writes or not e.writes[0x73]
    rep = acars.plain_report(["K6 MOD say " + ln for ln in e.said], "AG7EW TEST 440.850 R")
    fit = [ln for ln in rep if ln.startswith("fit")]
    assert len(fit) == 8, rep
    for fe, ln in enumerate(fit):
        n, found = int(ln.split()[3]), int(ln.split()[6])
        assert ln.startswith(f"fit{fe} ") and n >= 3 and found >= n, ln
        assert ln.endswith("1 -> 1:100%; 1.5 -> 2:100%; 2 -> 2:100%"), ln
    got = [ln for ln in rep if "block [" in ln]
    assert len(got) >= 8 and all("AG7EW TEST 440.850 R0" in ln for ln in got), rep
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47):
        assert e.writes[reg][-1] == 0, hex(reg)


SETS = [("00e9", "60cb", "6868"),
        ("00e9", "60cb", "9797"),
        ("00e9", "60cb", "d554"),
        ("00e9", "60cb", "2aab"),
        ("00a9", "60cb", "6868"),
        ("00a9", "60cb", "9797"),
        ("00e5", "60cb", "6868"),
        ("00e5", "60cb", "9797"),
        ("00e9", "ffff", "aaaa"),
        ("00e9", "c195", "aaaa"),
        ("00e1", "c195", "aaaa"),
        ("00e5", "c195", "aaaa"),
        ("00ed", "c195", "aaaa"),
        ("00a9", "c195", "aaaa"),
        ("0069", "c195", "aaaa"),
        ("04e9", "c195", "aaaa")]


def test_nrz_samples_are_the_block():
    """the slope's sign at each bit's edge is the data bit: nrz_samples of a unit's tone bits
    are its bytes' bits, and nrz_blocks reads the block from them either way up, from just
    after SYN SYN (the chip's sync word) or with "+*" SYN SYN still in front"""
    text = "AG7EW TEST 50.650 R007 THE K6 READS ACARS"
    d = acars.nrz_samples(acars.unit_tones("R", text))
    assert np.array_equal(d, np.concatenate([acars.bits_lsb(acars.head()), acars.bits_lsb(acars.block(text))]))
    assert "".join(map(str, d[8 * 16:8 * 20])) == f"{0xD554:016b}{0x6868:016b}"
    for inv in (0, 1):
        for before in (0, 16, 32):
            b = np.concatenate([d[8 * len(acars.head()) - before:], np.zeros(40, np.int8)]) ^ inv
            words = [int("".join(map(str, b[i:i + 16])), 2) for i in range(0, len(b) - 15, 16)]
            texts, why, up = acars.nrz_blocks(words)
            assert texts == ["2AG7EW..\x15T01\x02" + text] and why == "ok" and up == inv, (inv, before, texts, why)
            assert acars.nrz_fit(words, acars.nrz_head("AG7EW TEST 50.650 R")) == 0
            words[3] ^= 0x0180
            assert acars.nrz_blocks(words)[0] == [] and acars.nrz_fit(words, acars.nrz_head("AG7EW TEST 50.650 R")) == 2


def test_dfskcap_acarsc_settings_on_the_model():
    """dfskcap_acarsc (DFSK_SETS in FM on 440.850 MHz) on the emulated M0: sixteen settings one
    after the other. The model's chip here gives a sample a bit at 2400 baud that is the data
    bit (nrz_samples: what a clock settled between the turns would give; whether the real
    chip's does is the bench's question), so the settings whose sync word is SYN SYN or "+*"
    as they come get the blocks and those with the words upside down get none; the report
    reads them and counts the opening's bits off. The registers are put back."""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_acarsc.c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_SETS", "1"), ("DFSK_DUMP_ALL", "1"),
                                 ("PROBE_HZ", "440850000u"), ("FE_S", "2"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, BASE, k = 33.0, 19200, 0
    air = [np.zeros(BASE // 4, np.int8)]
    while sum(map(len, air)) < (total + 9) * BASE:
        d = acars.nrz_samples(acars.unit_tones("R", f"AG7EW TEST 440.850 R{k:03d} THE K6 READS ACARS"))
        air += [np.repeat(d, BASE // 2400), np.zeros(BASE // 4, np.int8)]
        k += 1
    air = np.concatenate(air)
    Chip = modem_chip(le, lambda r58: air, BASE)

    class Tuned(Chip):
        long5d, early_done, ring = True, True, True
        r = {**Chip.r, 0x47: 0}

        def on_stub(self, uc, addr, size, user):
            if self.names[(addr - self.stubs) // 16] == "tune":
                self.tunes.append((uc.reg_read(self.A.UC_ARM_REG_R0), uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=440850000 n=16" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[1:2] + ln.split()[3:] for ln in e.said if ln.startswith("s ")] == \
        [[str(i), "cfg=" + c, "b=" + b, "sy=" + y, "l=ff20"] for i, (c, b, y) in enumerate(SETS)]
    assert round(acars.baud_of(0x60CB)) == 2400 and round(acars.baud_of(0xFFFF)) == 6348
    assert e.tunes[-16:] == [(440850000, 1)] * 16, e.tunes
    rep = acars.plain_report(["K6 MOD say " + ln for ln in e.said], "AG7EW TEST 440.850 R")
    nrz = {int(ln.split()[0][3:]): ln for ln in rep if ln.startswith("nrz")}
    assert sorted(nrz) == list(range(8)), rep
    for fe, ln in nrz.items():
        n, caps = int(ln.split()[3]), int(ln.split()[6])
        if fe % 2 == 0:                                           # the sync word as the bits come
            assert n >= 2 and caps >= n and ln.split("off: ")[1].split()[:n] == ["0"] * n, ln
        else:
            assert n == 0, ln
    got = [ln for ln in rep if "block [" in ln]
    assert len(got) >= 8 and all("AG7EW TEST 440.850 R0" in ln and " up 0; " in ln for ln in got), rep
    assert sum(ln.startswith("fit") for ln in rep) == 8, rep     # the eight at 4800 baud and over
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47):
        assert e.writes[reg][-1] == 0, hex(reg)



TONE = [("33a9", "60cb", "0000", "f0", "0"),
        ("33a9", "60cb", "ffff", "f0", "0"),
        ("33a9", "60cb", "5c5c", "f0", "0"),
        ("33a9", "60cb", "3ffe5c5c", "f8", "0"),
        ("33a9", "60cb", "076b3e36", "f8", "0"),
        ("33a9", "60cb", "5c5c", "00", "0"),
        ("33a9", "60cb", "076b3e36", "08", "0"),
        ("33e9", "60cb", "0000", "f0", "0"),
        ("33e9", "60cb", "076b3e36", "f8", "0"),
        ("33a3", "60cb", "0000", "f0", "0"),
        ("3329", "60cb", "0000", "f0", "0"),
        ("33a9", "60cb", "0000", "f0", "1"),
        ("33a9", "60cb", "0000", "f0", "7"),
        ("33a9", "c195", "0000", "f0", "0"),
        ("33a3", "3065", "076b3e36", "f8", "0"),
        ("33a9", "3065", "076b3e36", "f8", "0")]


def test_tone_blocks_and_places():
    """the host's framing of the tone receiver's bits: a block is found after SYN SYN or the
    pager's sync word anywhere in them, either way up; the known opening is placed with its
    bits off counted, and shown"""
    ref = acars.tone_ref("AG7EW TEST 440.850 ")
    assert len(ref) == 264
    rng = np.random.default_rng(5)
    for kind in "KRL":
        text = f"AG7EW TEST 440.850 {kind}004 THE K6 READS ACARS"
        t = acars.unit_tones(kind, text)
        for inv in (0, 1):
            b = np.concatenate([rng.integers(0, 2, 37), t, rng.integers(0, 2, 50)]).astype(np.int8) ^ inv
            texts, up = acars.tone_blocks(b)
            assert texts == ["2AG7EW..\x15T01\x02" + text] and up == inv, (kind, inv, texts)
            way, places, D = acars.tone_places(b, ref)
            assert way == inv and [o for _, o in places] == [0], (kind, inv, places)
            bad = np.delete(b, places[0][0] - 100)                 # a bit lost: no block, one bit off
            bad[places[0][0] - 50] ^= 1                            # and one wrong
            assert acars.tone_blocks(bad)[0] == []
            way, places, D = acars.tone_places(bad, ref)
            assert way == inv and [o for _, o in places] == [2], places
            shown = acars.tone_show(bad, ref, way, places[0][0], D)
            assert len(shown) == 6 and "".join(shown[1::2]).count(".") == 1 and "." not in "".join(shown[0::2])
    x, sent = acars.probe_audio(6.0, kinds="KRL", gap=0.4)
    assert [k for k, _, _ in sent[:4]] == list("KRLK")
    t = acars.unit_tones("T", "")                                  # the ladder: no block in it, and found
    assert len(t) == 656 and acars.tone_blocks(t)[0] == [] and list(t[:384]) == [0] * 192 + [1] * 192
    b = np.concatenate([rng.integers(0, 2, 60), t, rng.integers(0, 2, 60)]).astype(np.int8) ^ 1
    way, places, _ = acars.tone_places(b, acars.LADDER_REF)
    assert way == 1 and [o for _, o in places] == [0] and len(acars.LADDER_REF) == 336
    y, _ = acars.probe_audio(6.0, kinds="KRL", gap=0.4, levels=(1.0, 1.8))     # every second round louder
    r0, r1 = (int(sent[i][1] * 48000) for i in (0, 3))
    assert np.allclose(y[r0:r0 + 4800], x[r0:r0 + 4800]) and np.allclose(y[r1:r1 + 4800], 1.8 * x[r1:r1 + 4800])
    # an L unit is a K unit at half the speed: a cycle of 1200 Hz or two of 2400 Hz a bit
    k, l = (acars.audio(acars.unit_tones(c, "X"), baud=acars.UNIT_BAUD[c]) for c in "KL")
    assert len(l) == 2 * len(k) and abs(l[39] - np.sin(2 * np.pi * 2400 * 40 / 48000)) < 1e-9


def test_dfskcap_acarsd_tone_receiver_on_the_model():
    """dfskcap_acarsd (DFSK_TONE in FM on 440.850 MHz) on the emulated M0: sixteen settings of
    the chip's 1200/2400 Hz tone receiver. The model's chip gives the units' tone bits as sent
    (whether the real one does at 2400 baud is the bench's question) and zeros in silence, so
    with the pre-key (0000) as the sync word it hands over every unit unframed and the report
    finds the K and R blocks in them; with SYN SYN it starts on the R units, with the pager's
    sync word on the K units; at 4800 baud it gives two samples a bit; at 1200 baud it reads
    the L units. The T units' ladder of tones is found where the tone bits come unframed. A
    setting's REG_5B, REG_59 and REG_2B are its own; the registers are put back"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_acarsd.c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_TONE", "1"), ("DFSK_DUMP_ALL", "1"),
                                 ("PROBE_HZ", "440850000u"), ("FE_S", "4"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, BASE, k = 65.0, 4800, 0
    air = [np.zeros(BASE // 4, np.int8)]
    while sum(map(len, air)) < (total + 9) * BASE:
        kind = "KRLT"[k % 4]
        t = acars.unit_tones(kind, f"AG7EW TEST 440.850 {kind}{k:03d} THE K6 READS ACARS")
        air += [np.repeat(t, BASE // acars.UNIT_BAUD[kind]), np.zeros(3 * BASE // 10, np.int8)]
        k += 1
    air = np.concatenate(air)
    Chip = modem_chip(le, lambda r58: air, BASE)

    class Tuned(Chip):
        long5d, early_done, ring, either = True, True, True, True
        r = {**Chip.r, 0x47: 0}

        def on_stub(self, uc, addr, size, user):
            if self.names[(addr - self.stubs) // 16] == "tune":
                self.tunes.append((uc.reg_read(self.A.UC_ARM_REG_R0), uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700 and e.over == 0, (stack, e.over)
    assert e.said[0] == "dfskcap start hz=440850000 n=16" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[1:2] + ln.split()[3:] for ln in e.said if ln.startswith("s ")] == \
        [[str(i), "cfg=" + c, "b=" + b, "sy=" + y, "c=" + ctl, "a=" + a] for i, (c, b, y, ctl, a) in enumerate(TONE)]
    assert round(acars.baud_of(0x3065)) == 1200
    assert e.tunes[-16:] == [(440850000, 1)] * 16, e.tunes
    # a setting's own registers: REG_5B, REG_59 (armed: + 1000) and REG_2B
    assert {0xFFFF, 0x5C5C, 0x3E36} == set(e.writes[0x5B]) - {0}
    assert {0x10F0, 0x10F8, 0x1000, 0x1008} <= set(e.writes[0x59])
    assert {0x0100, 0x0700} <= set(e.writes[0x2B]) and {0xFF20, 0x7F00} == set(e.writes[0x5D]) - {0}
    rep = acars.tone_report(["K6 MOD say " + ln for ln in e.said], show=1)
    tone = {int(ln.split()[0][4:]): ln for ln in rep if ln.startswith("tone")}
    assert sorted(tone) == list(range(16)), rep
    kinds = {}
    for ln in rep:
        if ln[:1] == "c" and " read [" in ln:
            kinds.setdefault(int(ln.split()[0][1:]), set()).add(ln.split("AG7EW TEST 440.850 ")[1][0])
    for fe, ln in tone.items():
        n, caps, found = int(ln.split()[5]), int(ln.split()[8]), int(ln.split()[11][:-1])
        assert n >= 1 and caps >= 1 and found >= n and ln.split("off: ")[1].split()[:n] == ["0"] * n, ln
        want = {"R"} if fe in (2, 3, 5) else {"K"} if fe in (4, 6, 8) else {"L"} if fe >= 14 else {"K", "R"}
        assert kinds[fe] == want, (fe, kinds[fe], ln)
    assert not any("upside down" in ln for ln in rep)
    rungs = {int(ln.split(":")[0][6:]): ln.split("off: ")[1].split() for ln in rep if ln.startswith("ladder")}
    assert sorted(rungs) == list(range(16)), rep
    for fe, off in rungs.items():       # the ladder whole wherever the tone bits come unframed at 2400 a second
        assert off[0] == ("0" if fe in (0, 1, 7, 9, 10, 11, 12, 13) else "none"), (fe, off)
    assert sum(ln.endswith("'s ladder, 0 off:") for ln in rep) == 8, rep
    assert sum(ln.startswith("  sent ") for ln in rep) == 16 * 3 + 8 * 4, rep
    assert sum(ln.endswith("'s best place, 0 off:") for ln in rep) == 16, rep     # the best capture is the one shown
    # a capture that is one word all through (silence after a restart) has its c line and no words
    for ln in e.said:
        if ln[:1] == "c" and " k=0 " in ln:
            assert not any(d.startswith("d" + ln.split()[0][1:] + " " + ln.split()[1] + " ") for d in e.said), ln
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47):
        assert e.writes[reg][-1] == 0, hex(reg)


TONE2 = [("33a9", "c195", "0"),
         ("33a9", "d9c8", "0"),
         ("33a9", "f1fb", "0"),
         ("33a9", "ffff", "0"),
         ("33a3", "c195", "0"),
         ("33a3", "f1fb", "0"),
         ("33e9", "c195", "0"),
         ("3369", "c195", "0"),
         ("3329", "c195", "0"),
         ("33a1", "c195", "0"),
         ("33a5", "c195", "0"),
         ("33ab", "c195", "0"),
         ("33ad", "c195", "0"),
         ("3fa9", "c195", "0"),
         ("37a9", "c195", "0"),
         ("33a9", "c195", "1")]


def test_dfskcap_acarse_second_round_on_the_model():
    """dfskcap_acarse (DFSK_TONE 2): sixteen settings of the tone receiver, all with the pre-key
    as the sync word, at 4800 baud and over. On the model (which gives the tone bits as sent,
    two samples a bit at 4800 baud) every setting at 4800 baud hands over the ladder and the
    blocks; the faster ones are only checked for their registers (the model's clock has whole
    steps). The registers are put back"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_acarse.c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_TONE", "2"), ("DFSK_DUMP_ALL", "1"),
                                 ("PROBE_HZ", "440850000u"), ("FE_S", "3"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, BASE, k = 49.0, 19200, 0
    air = [np.zeros(BASE // 4, np.int8)]
    while sum(map(len, air)) < (total + 9) * BASE:
        kind = "KRT"[k % 3]
        t = acars.unit_tones(kind, f"AG7EW TEST 440.850 {kind}{k:03d} THE K6 READS ACARS")
        air += [np.repeat(t, BASE // 2400), np.zeros(3 * BASE // 10, np.int8)]
        k += 1
    air = np.concatenate(air)
    Chip = modem_chip(le, lambda r58: air, BASE)

    class Tuned(Chip):
        long5d, early_done, ring, either = True, True, True, True
        r = {**Chip.r, 0x47: 0}

    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700, stack
    assert e.said[0] == "dfskcap start hz=440850000 n=16" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[1:2] + ln.split()[3:] for ln in e.said if ln.startswith("s ")] == \
        [[str(i), "cfg=" + c, "b=" + b, "sy=0000", "c=f0", "a=" + a] for i, (c, b, a) in enumerate(TONE2)]
    assert [round(acars.baud_of(int(b, 16))) for _, b, _ in TONE2[:4]] == [4800, 5400, 6000, 6348]
    rep = acars.tone_report(["K6 MOD say " + ln for ln in e.said])
    tone = {int(ln.split()[0][4:]): ln for ln in rep if ln.startswith("tone")}
    rungs = {int(ln.split(":")[0][6:]): ln.split("off: ")[1].split() for ln in rep if ln.startswith("ladder")}
    for fe, (_, b, _) in enumerate(TONE2):
        if b == "c195":
            assert int(tone[fe].split()[5]) >= 2 and rungs[fe][0] == "0", (tone[fe], rungs[fe])
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47):
        assert e.writes[reg][-1] == 0, hex(reg)


TONE3 = [("33a9", "60cb", "0000", "f0", "0"),
         ("33a9", "60cb", "ffff", "f0", "0"),
         ("33a3", "60cb", "0000", "f0", "0"),
         ("33a9", "60cb", "5c5c", "f0", "0"),
         ("33a9", "60cb", "3ffe5c5c", "f8", "0"),
         ("33a9", "60cb", "076b3e36", "f8", "0"),
         ("33a9", "60cb", "0000", "f0", "7"),
         ("33a9", "c195", "0000", "f0", "0"),
         ("33a9", "c195", "0000", "f0", "1"),
         ("33a1", "c195", "0000", "f0", "0"),
         ("33ab", "c195", "0000", "f0", "0"),
         ("33ad", "c195", "0000", "f0", "0"),
         ("33a3", "c195", "0000", "f0", "0"),
         ("33e9", "c195", "0000", "f0", "0"),
         ("3329", "c195", "0000", "f0", "0"),
         ("33a9", "ffff", "0000", "f0", "0")]


def test_dfskcap_acars6_tone_receiver_at_the_zero_if_on_the_model():
    """the 6 m bench build as it is (DFSK_ZIF, DFSK_TONE 3): the pick of the two FM rounds'
    settings of the tone receiver, every one at the zero IF with the AFC off and the AF source
    5. On the model (the tone bits as sent) the raw settings at 2400 and 4800 baud hand over
    the K and R blocks and the ladder, SYN SYN starts on the R units and the pager's word on
    the K units. The IF and the other registers are put back"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_acars6.c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_ZIF", "1"), ("DFSK_TONE", "3"), ("DFSK_DUMP_ALL", "1"),
                                 ("PROBE_HZ", "50650000u"), ("FE_S", "3"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, BASE, k = 49.0, 19200, 0
    air = [np.zeros(BASE // 4, np.int8)]
    while sum(map(len, air)) < (total + 9) * BASE:
        kind = "KRT"[k % 3]
        t = acars.unit_tones(kind, f"AG7EW TEST 50.650 {kind}{k:03d} THE K6 READS ACARS")
        air += [np.repeat(t, BASE // 2400), np.zeros(3 * BASE // 10, np.int8)]
        k += 1
    air = np.concatenate(air)
    Chip = modem_chip(le, lambda r58: air, BASE)

    class Tuned(Chip):
        long5d, early_done, ring, either = True, True, True, True
        r = {**Chip.r, 0x47: 0}
        if_now = 0x2AAB                                           # REG_3D reads back as written

        def on_stub(self, uc, addr, size, user):
            name, r0 = self.names[(addr - self.stubs) // 16], uc.reg_read(self.A.UC_ARM_REG_R0)
            if name == "tune":
                self.tunes.append((r0, uc.reg_read(self.A.UC_ARM_REG_R1)))
            if name == "fd_write" and r0 == 0x3D:
                self.if_now = uc.reg_read(self.A.UC_ARM_REG_R1)
            super().on_stub(uc, addr, size, user)
            if name == "fd_read" and r0 == 0x3D:
                uc.reg_write(self.A.UC_ARM_REG_R0, self.if_now)

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700, stack
    assert e.said[0] == "dfskcap start hz=50650000 n=16" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert [ln.split()[1:2] + ln.split()[3:8] for ln in e.said if ln.startswith("s ")] == \
        [[str(i), "cfg=" + c, "b=" + b, "sy=" + y, "c=" + ctl, "a=" + a] for i, (c, b, y, ctl, a) in enumerate(TONE3)]
    assert e.tunes[-17:-1] == [(50650000, 1)] * 16, e.tunes
    assert e.writes[0x3D][:16] == [0] * 16 and e.writes[0x3D][-1] == 0x2AAB, e.writes[0x3D]
    assert set(e.writes[0x73][:-1]) == {0x10} and e.writes[0x73][-1] == 0, e.writes[0x73]
    assert {w >> 8 & 15 for w in e.writes[0x47][:-1]} == {5}, e.writes[0x47]
    rep = acars.tone_report(["K6 MOD say " + ln for ln in e.said], fit="AG7EW TEST 50.650 ")
    tone = {int(ln.split()[0][4:]): ln for ln in rep if ln.startswith("tone")}
    rungs = {int(ln.split(":")[0][6:]): ln.split("off: ")[1].split() for ln in rep if ln.startswith("ladder")}
    kinds = {}
    for ln in rep:
        if ln[:1] == "c" and " read [" in ln:
            kinds.setdefault(int(ln.split()[0][1:]), set()).add(ln.split("AG7EW TEST 50.650 ")[1][0])
    for fe in range(15):                                          # (the model's clock has whole steps: not ffff)
        assert int(tone[fe].split()[5]) >= 1, tone[fe]
        assert kinds[fe] == ({"R"} if fe in (3, 4) else {"K"} if fe == 5 else {"K", "R"}), (fe, kinds[fe])
        assert rungs[fe][0] == ("none" if fe in (3, 4, 5) else "0"), (fe, rungs[fe])
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47, 0x73):
        assert e.writes[reg][-1] == 0, hex(reg)


# cfg, REG_72, sync, a=, tuning offset (100 Hz), front end bits, AF source
TONE4 = [("33a9", "c195", "0000", "7", 0, 11, 7),
         ("33a9", "c195", "0000", "7", 0, 3, 7),
         ("33a9", "c195", "0000", "0", 0, 11, 7),
         ("33a9", "c195", "0000", "7", 0, 11, 0),
         ("33a9", "c195", "0000", "7", 0, 10, 7),
         ("33a9", "c195", "ffff", "7", 0, 11, 7),
         ("33a9", "60cb", "0000", "7", 0, 11, 7),
         ("33ab", "c195", "0000", "7", 0, 11, 7),
         ("33a1", "c195", "0000", "7", 0, 11, 7),
         ("33a9", "ffff", "0000", "7", 0, 11, 7),
         ("33a9", "c195", "0000", "7", 0, 11, 4),
         ("33a9", "c195", "0000", "7", 0, 11, 5),
         ("33a9", "c195", "0000", "7", 25, 2, 0),
         ("33a9", "c195", "0000", "7", -25, 2, 0),
         ("33a9", "c195", "0000", "7", 50, 3, 0),
         ("33a9", "c195", "0000", "0", 0, 7, 5)]


def test_dfskcap_acars6am_tone_receiver_in_am_on_the_model():
    """the 6 m bench build with the chip in AM at its normal IF (DFSK_ZIF, DFSK_TONE 4): the
    tone receiver with the pre-key as its sync word, fifteen front ends at the IF as found
    (the AF source, the AM mode's AGC in REG_49 and REG_14, the filter, the tuning) and the
    zero IF's once more. The model knows nothing of AM (it gives the tone bits as sent): what
    is checked is every setting's registers, that the blocks and the ladder come through the
    recorder, and that all is put back"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", "dfskcap_acars6am.c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_ZIF", "1"), ("DFSK_TONE", "4"), ("DFSK_DUMP_ALL", "1"),
                                 ("PROBE_HZ", "50650000u"), ("FE_S", "3"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, BASE, k = 49.0, 19200, 0
    air = [np.zeros(BASE // 4, np.int8)]
    while sum(map(len, air)) < (total + 9) * BASE:
        kind = "KRT"[k % 3]
        t = acars.unit_tones(kind, f"AG7EW TEST 50.650 {kind}{k:03d} THE K6 READS ACARS")
        air += [np.repeat(t, BASE // 2400), np.zeros(3 * BASE // 10, np.int8)]
        k += 1
    air = np.concatenate(air)
    Chip = modem_chip(le, lambda r58: air, BASE)

    class Tuned(Chip):
        long5d, early_done, ring, either = True, True, True, True
        r = {**Chip.r, 0x47: 0}
        if_now = 0x2AAB                                           # REG_3D reads back as written
        found = {0x47: 0x6140, 0x49: 0x2A38, 0x14: 0x0019}        # as the firmware leaves them in FM

        def on_stub(self, uc, addr, size, user):
            name, r0 = self.names[(addr - self.stubs) // 16], uc.reg_read(self.A.UC_ARM_REG_R0)
            if name == "tune":
                self.tunes.append((r0, uc.reg_read(self.A.UC_ARM_REG_R1)))
            if name == "fd_write" and r0 == 0x3D:
                self.if_now = uc.reg_read(self.A.UC_ARM_REG_R1)
            super().on_stub(uc, addr, size, user)
            if name == "fd_read" and r0 == 0x3D:
                uc.reg_write(self.A.UC_ARM_REG_R0, self.if_now)
            if name == "fd_read" and r0 in self.found:
                uc.reg_write(self.A.UC_ARM_REG_R0, self.found[r0])

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 8, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700, stack
    assert e.said[0] == "dfskcap start hz=50650000 n=16" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    s_lines = [ln.split() for ln in e.said if ln.startswith("s ")]
    assert [ln[1:2] + ln[3:6] + ln[7:9] for ln in s_lines] == \
        [[str(i), "cfg=" + c, "b=" + b, "sy=" + y, "a=" + a, "if=" + ("0000" if fe & 4 else "2aab")]
         for i, (c, b, y, a, off, fe, af) in enumerate(TONE4)]
    assert e.tunes[-17:-1] == [(50650000 + 100 * off, fe & 1) for c, b, y, a, off, fe, af in TONE4], e.tunes
    assert e.writes[0x3D][:16] == [0 if t[5] & 4 else 0x2AAB for t in TONE4] and e.writes[0x3D][-1] == 0x2AAB
    assert set(e.writes[0x73][:-1]) == {0x10} and e.writes[0x73][-1] == 0, e.writes[0x73]
    assert [w >> 8 & 15 for w in e.writes[0x47][:16]] == [t[6] or 1 for t in TONE4], e.writes[0x47]
    assert e.writes[0x49][:16] == [0x1920 if t[5] & 8 else 0x2A38 for t in TONE4], e.writes[0x49]
    assert e.writes[0x14][:16] == [0 if t[5] & 8 else 0x0019 for t in TONE4], e.writes[0x14]
    assert e.writes[0x2B][:1] == [0x0700], e.writes[0x2B][:4]
    rep = acars.tone_report(["K6 MOD say " + ln for ln in e.said], fit="AG7EW TEST 50.650 ")
    tone = {int(ln.split()[0][4:]): ln for ln in rep if ln.startswith("tone")}
    rungs = {int(ln.split(":")[0][6:]): ln.split("off: ")[1].split() for ln in rep if ln.startswith("ladder")}
    kinds = {}
    for ln in rep:
        if ln[:1] == "c" and " read [" in ln:
            kinds.setdefault(int(ln.split()[0][1:]), set()).add(ln.split("AG7EW TEST 50.650 ")[1][0])
    for fe in range(16):
        if fe == 9:                                               # (the model's clock has whole steps: not ffff)
            continue
        assert int(tone[fe].split()[5]) >= 1, tone[fe]
        assert kinds[fe] == {"K", "R"}, (fe, kinds[fe])
        assert rungs[fe][0] == "0", (fe, rungs[fe])
    for reg, found in ((0x58, 0), (0x59, 0), (0x5A, 0), (0x5B, 0), (0x5C, 0), (0x5D, 0), (0x5E, 0), (0x72, 0),
                       (0x2B, 0), (0x3F, 0), (0x47, 0x6140), (0x73, 0), (0x49, 0x2A38), (0x14, 0x0019)):
        assert e.writes[reg][-1] == found, hex(reg)
