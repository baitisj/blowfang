"""DMR's sync words through the chip's plain FSK receiver (c/k6/mod/dfskcap_dmr.c,
modem/dmrsign.py, docs/k6-dmr.md): the signs of the sync words' symbols as the chip's sync
word. The model of the FD6818's packet mode (tests/test_k6_aprsclk.py modem_chip) gives the
symbols' signs as its bits; whether the real chip's slicer and clock do is the bench's question.
Checked here: the pattern, the module's settings round and round, its lines, the report's
syncs (in the model's stream and in run J's capture off the air), the registers put back. SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_aprsclk import FDMA, K6, modem_chip, need_clang  # noqa: E402

from modem import dmrsign  # noqa: E402

pytestmark = need_clang

# sync word, a= (REG_2B's bits), the wide filter, the AFC off, REG_58
DMR = [("9b4d", 0x0700, 0, 1, "00e9"),
       ("64b2", 0x0700, 0, 1, "00e9"),
       ("9b4d", 0, 0, 1, "00e9"),
       ("64b2", 0, 0, 1, "00e9"),
       ("9b4d", 0x0700, 1, 1, "00e9"),
       ("64b2", 0x0700, 1, 1, "00e9"),
       ("9b4d", 0x0700, 0, 0, "00e9"),
       ("64b2", 0x0700, 0, 0, "00e9")]
DMR2 = [("9b4d", 0x0700, 1, 1, "00e9"),
        ("cd4d", 0x0700, 1, 1, "00e9"),
        ("9b4d", 0x0700, 0, 1, "00e9"),
        ("cd4d", 0x0700, 0, 1, "00e9"),
        ("9b4d", 0x0700, 1, 1, "00a9"),
        ("9b4d", 0, 1, 1, "00e9"),
        ("9b4d", 0x0700, 1, 0, "00e9"),
        ("cd4d", 0x0700, 1, 0, "00e9")]
# run L: one setting, the receiver's gain (REG_13) stepped down
G13 = ["03ff", "035f", "031f", "021f", "011f", "001f", "0007", "0004"]
DMR3 = [("9b4d", 0x0700, 0, 1, "00e9", g) for g in G13]
# dfskcap_dmr4: the mixer's steps between 001f and 0007 in the place of two strong steps
G13B = ["03ff", "021f", "011f", "001f", "0017", "000f", "0007", "0004"]
DMR4 = [("9b4d", 0x0700, 0, 1, "00e9", g) for g in G13B]
# dfskcap_dmr5: one setting tuned off the repeater, in 100 Hz
OFFS = [-13,
        -9,
        -5,
        -2,
        2,
        5,
        9,
        13]
DMR5 = [("9b4d", 0x0700, 0, 1, "00e9")] * 8


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
    assert dmrsign.find_syncs(cap) == [(120 + 144 * i, "-" if i % 3 == 1 else "+", "whole") for i in range(7)]
    assert dmrsign.find_syncs(rng.integers(0, 2, 100000).astype(np.int8)) == []
    slip = np.delete(cap, 120 + 15)                               # the sign the real chip drops
    assert dmrsign.find_syncs(slip)[:2] == [(120, "+", "short"), (263, "-", "whole")]


# Run J (2026-11-11 07:39:48Z, build 13c7783): the one key-up of the 440.925 repeater's in the
# 12 minutes, half a second at -81 dBm, as dfskcap_dmr's setting 4 caught it (sync word 9B4D,
# the wide filter, the AFC off): idle bursts, both slots.
RUN_J = """s 4 t=539642 cfg=00e9 b=c195 sy=9b4d l=7f00
c4 1 t=539697 w=139 k=0 r=-81
d4 1 000 413bec8261b9c13c5803550855da1cda6b04ef9904c373e1
d4 1 00c b1600aa842aed0e6a6b04ef9904c37387dc5803550855b43
d4 1 018 9a9a8277d904c373f931600d542156d0e6d3504efb20986e
d4 1 024 702f1600aa842aed0e6a6b04ef9904c373e1b160aa840d54
d4 1 030 215768735358277d904c37387d8b006aa10ab68753583535
d4 1 03c 04efb304c373f931600d5421b687b687609d3535f320986e
d4 1 048 70278b00554215768735358277cc8261b9f0d8b006aa1576
d4 1 054 10ab687353504efb20986e70fb1600d5421568736d0e6d35
d4 1 060 04efb20986e7f262c01550855da1cda6b04ef9904c37f262
d4 1 06c 3813c5803550855da1cd4d609df64130dcf86e2c01aa855d
d4 1 078 842ada1cd4d413bec8261b9cfb1600d542157687369a13be
d4 1 084 c13be64130dc800000389f007687
v4 n=1 w=139 b=0 p=-81 f=-130"""


def test_run_j_capture():
    """what the real chip gave on a real repeater: thirteen sync fields in 2224 signs, five
    whole and eight with sign 16 dropped, some 133 to 152 bits apart and not 144: the clock
    drops signs. Chance would give none (and gave none in the run's 48 captures of noise)"""
    rep = dmrsign.report(["12.5 K6 MOD say " + ln for ln in RUN_J.splitlines()])
    assert rep == ["c4 t=539697 r=-81: 13 syncs in 2224 signs, at 109 242s 376s 506 638s 787s 1215s 1363s 1510 "
                   "1641 1793s 1941s 2071",
                   "dmr4 sy=9b4d cfg=00e9: 1 slots, 1 captures (2224 signs), 1 with a sync inside; "
                   "syncs 5 whole, 8 short; RSSI -81 to -81; slot peaks -81 to -81; floor -130; "
                   "carrier in 1 slots, a sync in 1 (peaks -81 to -81)"], rep
    # with quiet slots round it and a carrier that is not DMR (the repeater's analog ID)
    quiet = ["s 4 t=1 cfg=00e9 b=c195 sy=9b4d l=7f00", "v4 n=0 w=0 b=0 p=-129 f=-131"] * 3
    rep = dmrsign.report(quiet + RUN_J.splitlines() + quiet[:1] + ["v4 n=0 w=0 b=0 p=-88 f=-89"])
    assert rep[-1].endswith("slot peaks -129 to -81; floor -131; carrier in 2 slots, a sync in 1 (peaks -81 to -81), "
                            "none in 1 (peaks -88 to -88)"), rep


def on_the_model(name, extra, table, rounds, air, block=0, offs=None):
    """a dfskcap_dmr build on the emulated M0, rounds times round its table of eight settings
    of 0.8 s, the air as signs -> its report's line a setting. Checked on the way: the lines,
    the tuning (the repeater's, in the filter each setting names), the AFC bit, the registers
    put back. block: dfskcap_dmr4's blocks of that many rounds, three kinds by turns (0 tuned
    at every setting, 1 not tuned, 2 not tuned but once at the block's start, the AFC on):
    the report's lines are then keyed (setting, kind). offs: dfskcap_dmr5's tuning offsets
    in 100 Hz, a setting each (o= on the s lines and in the report)"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    from modem import k6mod
    src = os.path.join(K6, "mod", name + ".c")
    code, entry = le.build("x", [("DFSK_ACARS", "1"), ("DFSK_NARROW", "1"), ("DFSK_DMR", "1")] + extra
                           + [("DFSK_ROUNDS", f"{rounds}u"), ("DFSK_DUMP_ALL", "1"), ("PROBE_HZ", "440925000u"),
                              ("SLOT_SCALE", "100u"), ("FE_S", "8"), ("WAIT_MS", "200u"), ("QN", "2u")],
                           os.path.join(K6, "mod", "dfskcap.c"))
    full, _ = k6mod.build(src)
    total, n = rounds * 8 * 0.8 + 4, rounds * 8
    Chip = modem_chip(le, lambda r58: air, 4800)

    class Tuned(Chip):
        long5d, early_done, ring = True, True, True
        r = {**Chip.r, 0x47: 0}
        found = {0x13: 0x1234, 0x7E: 0x3ABC}                      # the gain registers as found

        def on_stub(self, uc, addr, size, user):
            name, r0 = self.names[(addr - self.stubs) // 16], uc.reg_read(self.A.UC_ARM_REG_R0)
            if name == "tune":
                self.tunes.append((r0, uc.reg_read(self.A.UC_ARM_REG_R1)))
            super().on_stub(uc, addr, size, user)
            if name == "fd_read" and r0 in self.found:
                uc.reg_write(self.A.UC_ARM_REG_R0, self.found[r0])

    Tuned.tunes = []
    e = Tuned(code, entry, np.zeros(16, np.uint8), 0, FDMA, 1.5, max_s=total + 12, level=8,
              scratch=11776 - 24 - len(full))
    r = e.run()
    assert r is not None, e.said[-5:]
    ret, stack = r
    assert stack < 700, stack
    assert e.over == 0 or ("DFSK_CAPMAX" in dict(extra)), e.over   # a cut capture leaves the chip's words unread
    assert e.said[0] == "dfskcap start hz=440925000 n=8" and e.said[-1] == f"dfskcap end n={ret}", e.said[-2:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    s = [ln.split() for ln in e.said if ln.startswith("s ")]
    kind = [i // 8 // block % 3 if block else -1 for i in range(n)]
    assert [ln[1:2] + ln[3:] for ln in s] == \
        [[str(i % 8), "cfg=" + table[i % 8][4], "b=c195", "sy=" + table[i % 8][0], "l=7f00"]
         + ["g=" + x for x in table[i % 8][5:]] + [f"bk={kind[i]}"] * (block > 0)
         + ([f"o={offs[i % 8]}"] if offs else []) for i in range(n)], s
    hz = [440925000 + 100 * o for o in offs or [0] * 8]
    assert e.tunes == [(440925000, 1)] + [(hz[i % 8], table[i % 8][2]) for i in range(n)
                                          if kind[i] <= 0 or kind[i] == 2 and i % (8 * block) == 0], e.tunes
    assert e.writes[0x73][:n] == [0x10 if table[i % 8][3] and kind[i] != 2 else 0 for i in range(n)]
    assert e.writes[0x73][-1] == 0
    assert {w & 0x0700 for w in e.writes[0x2B][:-1]} == {t[1] for t in table}, e.writes[0x2B]
    rep = dmrsign.report(["K6 MOD say " + ln for ln in e.said])
    if block:
        dmr = {(int(ln.split()[0][3:]), int(ln.split(" bk=")[1][0])): ln for ln in rep if ln.startswith("dmr")}
        assert sorted(dmr) == [(fe, k) for fe in range(8) for k in sorted(set(kind))], rep
        for (fe, k), ln in dmr.items():
            assert f"sy={table[fe][0]} cfg={table[fe][4]} g={table[fe][5]} bk={k}: {kind.count(k) // 8} slots" in ln, ln
    else:
        dmr = {int(ln.split()[0][3:]): ln for ln in rep if ln.startswith("dmr")}
        assert sorted(dmr) == list(range(8)), rep
        for fe, ln in dmr.items():
            g = "".join(" g=" + x for x in table[fe][5:]) + (f" o={offs[fe]}" if offs else "")
            assert f"sy={table[fe][0]} cfg={table[fe][4]}{g}: {rounds} slots" in ln, ln
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F, 0x47, 0x73):
        assert e.writes[reg][-1] == 0, hex(reg)
    if len(table[0]) > 5:                                         # the gain fixed a setting, then as found
        assert e.writes[0x13] == [int(table[i % 8][5], 16) for i in range(n)] + [0x1234], e.writes[0x13]
        assert e.writes[0x7E] == [0xBABC] * n + [0x3ABC], e.writes[0x7E]
    else:
        assert 0x13 not in e.writes and 0x7E not in e.writes
    return dmr, e.said


def syncs_of(ln):
    return tuple(int(x.split()[0]) for x in ln.split("syncs ")[1].split(";")[0].split(", "))


def test_dfskcap_dmr_on_the_model():
    """dfskcap_dmr, three times round. The air is a repeater's bursts as signs, the first
    half one way up and the second half the other, so the settings with 9b4d find the syncs
    in the first and those with 64b2 in the second, all whole (the model's clock keeps time)"""
    rng = np.random.default_rng(7)
    half = dmrsign.air_signs(int((3 * 8 * 0.8 + 4) * 4800 / 144 / 2) + 1, rng, voice_every=6)
    dmr, _ = on_the_model("dfskcap_dmr", [], DMR, 3, np.concatenate([half, 1 - half, half]))
    assert all(syncs_of(ln)[1] == 0 for ln in dmr.values()), dmr
    assert sum(syncs_of(ln)[0] for ln in dmr.values()) >= 40, dmr  # most slots are in a half that suits them


def test_dfskcap_dmr2_on_the_model():
    """dfskcap_dmr2's table (run K), once round. The air's sync fields come with sign 16
    dropped, as the real chip gives most of them: the settings with the sync word cd4d start
    on them and the report counts them as short; those with 9b4d get nothing but chance"""
    rng = np.random.default_rng(9)
    air = dmrsign.air_signs(int((8 * 0.8 + 4) * 4800 / 144) + 2, rng)
    air = np.delete(air, [66 + 15 + 144 * i for i in range(len(air) // 144)])
    dmr, _ = on_the_model("dfskcap_dmr2", [("DFSK_DMR2", "1")], DMR2, 1, air)
    for fe, ln in dmr.items():
        whole, short = syncs_of(ln)
        assert whole == 0 and (short >= 3) == (DMR2[fe][0] == "cd4d"), ln


def test_dfskcap_dmr3_on_the_model():
    """dfskcap_dmr3's table (run L), twice round: one setting, the receiver's gain fixed at
    eight values of REG_13 (REG_7E bit 15 with index 3) and both registers put back; the s
    lines and the report name the gain; a capture is cut short (at 40 words here, 96 in the
    module: the model's packets end at 65), so a slot has several"""
    rng = np.random.default_rng(11)
    air = dmrsign.air_signs(int((2 * 8 * 0.8 + 4) * 4800 / 144) + 2, rng)
    dmr, said = on_the_model("dfskcap_dmr3", [("DFSK_DMR3", "1"), ("DFSK_GAIN", "1"), ("DFSK_CAPMAX", "40u")],
                             DMR3, 2, air)
    for fe, ln in dmr.items():
        assert f"cfg=00e9 g={G13[fe]}: 2 slots" in ln and syncs_of(ln)[0] >= 10, ln
    w = [int(ln.split(" w=")[1].split()[0]) for ln in said if ln[:1] == "c"]
    assert len(w) >= 32 and set(w) == {40}, w


def test_dfskcap_dmr4_on_the_model():
    """dfskcap_dmr4 (run M's module): eight gains (G13B) in blocks of three kinds, here of
    one round each, four rounds: tuned at every setting (kind 0, as run L), not tuned (1), the
    AFC on and tuned once at the block's start (2), and kind 0 again, which turns the AFC off
    again. on_the_model checks the tuning, the AFC bit and bk= on the s lines; the gain is
    written at every setting whatever the kind, and the report has a line a gain and kind"""
    rng = np.random.default_rng(13)
    air = dmrsign.air_signs(int((4 * 8 * 0.8 + 4) * 4800 / 144) + 2, rng)
    dmr, said = on_the_model("dfskcap_dmr4", [("DFSK_DMR3", "1"), ("DFSK_GAIN", "1"), ("DFSK_CAPMAX", "40u"),
                                              ("DFSK_BLOCK", "1u")], DMR4, 4, air, block=1)
    assert len(dmr) == 24 and all(syncs_of(ln)[0] >= 4 for ln in dmr.values()), dmr
    assert sum(syncs_of(ln)[0] for (_, k), ln in dmr.items() if k == 0) >= 150, dmr


def test_dfskcap_dmr5_on_the_model():
    """dfskcap_dmr5 (the off-tune sweep), three times round: one setting tuned 1.3, 0.9, 0.5
    and 0.2 kHz below and above the repeater (on_the_model checks the tunings and o= on the s
    lines), the gain registers left alone. The report names the offset and ends a setting's
    line with the share of ones in its captures with a sync: a half here, the model's signs
    being the symbols' own whatever the tuning (what the real chip makes of it is the run's
    question). ones_at says what a slicer at the tuned centre would give"""
    rng = np.random.default_rng(17)
    air = dmrsign.air_signs(int((3 * 8 * 0.8 + 4) * 4800 / 144) + 2, rng)
    dmr, _ = on_the_model("dfskcap_dmr5", [("DFSK_DMR5", "1")], DMR5, 3, air, offs=OFFS)
    for fe, ln in dmr.items():
        assert f"cfg=00e9 o={OFFS[fe]}: 3 slots" in ln and syncs_of(ln)[0] >= 10, ln
        assert 45 <= int(ln.rsplit("; ones ", 1)[1].rstrip("%")) <= 55, ln
    assert [dmrsign.ones_at(100 * o) for o in OFFS] == [75, 75, 50, 50, 50, 50, 25, 25]
    with open(os.path.join(K6, "mod", "dfskcap_dmr5.c")) as f:
        d5 = dict(ln.split()[1:3] for ln in f if ln.startswith("#define ") and len(ln.split()) == 3)
    assert d5["DFSK_ROUNDS"] == "840u" and d5["PROBE_HZ"] == "440925000u" and "DFSK_GAIN" not in d5


def test_dfskcap_dmr4_is_dmr3_in_blocks():
    """the module as built: nine blocks of 144 rounds (each at least 921.6 s, the repeater keys
    every 903 s), all else dfskcap_dmr3's but the gains (dfskcap.c's table under DFSK_BLOCK)"""
    with open(os.path.join(K6, "mod", "dfskcap_dmr4.c")) as f:
        d4 = dict(ln.split()[1:3] for ln in f if ln.startswith("#define ") and len(ln.split()) == 3)
    with open(os.path.join(K6, "mod", "dfskcap_dmr3.c")) as f:
        d3 = dict(ln.split()[1:3] for ln in f if ln.startswith("#define ") and len(ln.split()) == 3)
    assert d4.pop("DFSK_BLOCK") == "144u" and d4.pop("DFSK_ROUNDS") == "1296u" and d3.pop("DFSK_ROUNDS") == "280u"
    assert d4 == d3 and 144 * 8 * 0.8 > 903 + 15


def test_report_reads_run_m():
    """three of run M's seventeen key-ups (2026-11-11/12, docs/k6-dmr.md), one of each kind of
    block, with four rounds of quiet before each for the floors: 22:44:06Z (kind 0, tuned at
    every setting), 00:30:17Z (kind 1, not tuned) and 23:14:32Z (kind 2, not tuned, the AFC
    on). The report keeps the kinds apart; the sync is found in every kind at every gain but
    the weakest of kind 0, and the gain's floor is the same whatever the kind"""
    import gzip
    import re
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "k6", "dfskcap-dmr4-runM-20261111.log.gz")
    with gzip.open(path, "rt") as f:
        lines = f.read().splitlines()
    assert len(lines) == 809 and lines[0].endswith("dfskcap start hz=440925000 n=8")
    assert lines[-1].endswith("dfskcap end n=1025")
    rep = [ln for ln in dmrsign.report(lines) if ln.startswith("dmr")]
    assert [ln.split(":")[0] for ln in rep] == [f"dmr{fe} sy=9b4d cfg=00e9 g={G13B[fe]} bk={k}"
                                                for fe in range(8) for k in range(3)], rep
    tot, floor = {}, {}
    for ln in rep:
        k, fe = int(ln.split(" bk=")[1][0]), int(ln[3])
        sy, car = re.search(r"syncs (\d+) whole, (\d+) short", ln), re.search(r"carrier in (\d+) slots, a sync in (\d+)", ln)
        t = tot.setdefault(k, [0, 0, 0, 0])
        for i, x in enumerate(sy.groups() + car.groups()):
            t[i] += int(x)
        floor.setdefault(fe, []).append(int(ln.split("; floor ")[1].split(";")[0]))
    # sync fields whole and short, slots with a carrier, those with a sync
    assert tot == {0: [47, 33, 13, 8], 1: [58, 45, 8, 8], 2: [75, 36, 9, 9]}, tot
    assert all(max(f) - min(f) <= 2 for f in floor.values()), floor
    assert [min(f) for f in floor.values()] == [-125, -156, -156, -156, -150, -147, -146, -155], floor
    assert rep[21] == ("dmr7 sy=9b4d cfg=00e9 g=0004 bk=0: 7 slots, 1 captures (1536 signs), 0 with a sync inside; "
                       "syncs 0 whole, 0 short; slot peaks -153 to -144; floor -154; carrier in 1 slots, a sync in 0, "
                       "none in 1 (peaks -144 to -144)")
    assert rep[23] == ("dmr7 sy=9b4d cfg=00e9 g=0004 bk=2: 7 slots, 4 captures (5184 signs), 2 with a sync inside; "
                       "syncs 4 whole, 3 short; RSSI -143 to -142; slot peaks -154 to -144; floor -155; "
                       "carrier in 1 slots, a sync in 1 (peaks -144 to -144)")
