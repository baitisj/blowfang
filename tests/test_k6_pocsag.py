"""POCSAG on the K6 (c/k6/mod/pocsag.c, modem/pocsag.py, docs/k6-pocsag.md): the code, the bench's
test signal, and the module on an emulated M0 against a model of the FD6818's plain FSK receiver
at the pager's own baud (a clock locked to the data, the sync word found at any bit, sixteen bits a
FIFO word, an empty FIFO read as the word eight before). The model cannot say how the real
receiver takes a 512 or 2400 baud line or which way up the IC-7100 sends it: the bench says.
SPDX-License-Identifier: MIT"""
import math
import os
import shutil
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K6 = os.path.join(ROOT, "c", "k6")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem import pocsag  # noqa: E402

need_clang = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                reason="needs clang and ld.lld")


def test_codewords_and_fixes():
    # the sync and idle codewords are codewords of the code (a check of the generator)
    assert pocsag.codeword(pocsag.SYNC >> 11) == pocsag.SYNC
    assert pocsag.codeword(pocsag.IDLE >> 11) == pocsag.IDLE
    rng = np.random.default_rng(1)
    for _ in range(300):
        c = pocsag.codeword(int(rng.integers(0, 1 << 21)))
        i, j, k = rng.choice(32, 3, replace=False)
        assert pocsag.fix(c ^ (1 << int(i))) == (c, 1)
        assert pocsag.fix(c ^ (1 << int(i)) ^ (1 << int(j))) == (c, 2)
        got, n = pocsag.fix(c ^ (1 << int(i)) ^ (1 << int(j)) ^ (1 << int(k)))
        assert n == -1 or got != c                    # three: never "fixed" back to c


@pytest.mark.parametrize("baud", pocsag.BAUDS)
def test_bench_transmission_reads_back(baud):
    msgs = pocsag.bench_messages(7, baud)
    bits = pocsag.transmission_bits(msgs)
    assert len(bits) == pocsag.PREAMBLE + 4 * 544            # the long message takes three batches
    for b in (bits, 1 - bits):
        got = pocsag.decode_bits(b)
        assert [(r, f, t) for r, f, t, _ in got] == [(r, f, t) for r, f, t, _ in msgs]


def test_bench_audio_slices_back():
    """the WAV's line, sliced at each bit's middle, is the transmission (either sense)"""
    y, times = pocsag.bench_wav(rounds=2, gap=0.3)
    assert len(times) == 6 and all(t1 - t0 > 1.0 for t0, t1, *_ in times)
    for t0, t1, r, baud, inv in times:
        i0 = round(t0 * pocsag.FS)
        bits = pocsag.transmission_bits(pocsag.bench_messages(r, baud))
        mid = i0 + ((np.arange(len(bits)) + 0.75) * pocsag.FS / baud).astype(int)
        got = (y[mid] < 0).astype(np.uint8) ^ inv
        assert np.array_equal(got, bits), (r, baud)
        assert [t for _, _, t, _ in pocsag.decode_bits(got)] == [t for _, _, t, _ in pocsag.bench_messages(r, baud)]


def test_report_reads_module_lines():
    lines = ["K6 MOD say " + s for s in [
        "pocsag start hz=440850000 n=6 tune=1 text=1",
        "s 0 t=32000 cfg=00e9 a=0000 h=1",
        "p 1 s=0 b=1200 t=33000 w=180 i=1 e=0 r=-41",
        "k 1 q=4 ok=64 f=0 x=0 c=1 m=3",
        "m 1234 3 e0 AG7EW TEST 1 1200",
        "m 2005 0 e0 001-1200",
        "m 4711 3 e0 AG7EW POCSAG 1200 R1 THE QUICK BROWN FOX JUMPS OVER T",
        "+ HE LAZY DOG 0123456789",
        "m 99 3 e0 SECRET",
        "v 0 n=1 m=3 z=0",
    ]]
    rep = pocsag.report(lines)
    assert "messages 4, exact 3 (short 1, numeric 1, long 1 rounds); inverted 1" in rep[1], rep
    assert "<6 chars>" in rep[2] and "SECRET" not in "\n".join(rep)     # unknown text is not shown


# ---- the module on a model of the chip ----
def plain_chip(le, segs, flip=0.0, seed=3, smp=None):
    """an emulator with a model of the FD6818's plain FSK receiver: segs [(t0 s, true baud, bits)]
    are the air; while one plays at a baud within 2% of REG_72's the chip's clock is the data's
    (each bit taken once, at its middle), or at a half or a quarter of it the chip samples each bit
    two or four times, otherwise it takes random bits at REG_72's rate. The
    sync word (REG_5A, or REG_5A REG_5B by REG_59<3>) in any bit position starts a packet of
    REG_5D's length (bits 15:8 and 7:5); each 16 bits go into an 8-word FIFO, the first bit at
    the top of the byte, the first byte in the low byte. An empty FIFO's read gives the slot it
    would read next (the word of eight before). flip: the chance a bit is wrong. smp: the
    sampler's own rate a second (four samples a bit at REG_72's rate, not locked to the data: the
    bench's 1200 baud came at 3.93 samples a bit in a transmission's text, run 4)"""
    rng = np.random.default_rng(seed)

    class Chip(le.Emu):
        r = {0x58: 0, 0x59: 0, 0x5A: 0, 0x5B: 0, 0x5D: 0, 0x72: 0}
        tn, sr, pkt, done, nbit, acc = 0.0, 0, False, False, 0, 0
        ring, rp, wp, cnt, over, starts = [0] * 8, 0, 0, 0, 0, 0

        def rssi(self):
            return -60

        def baud(self):
            return round(self.r[0x72] / 10.32444)

        def advance(self):
            now = self.cyc / 96e6
            if not (self.r[0x59] & 0x1000 and self.r[0x58] & 1 and self.r[0x72]):
                self.tn = now
                return
            br = self.baud()
            while True:
                seg = next(((s, m) for s in segs for m in (1, 2, 4)
                            if s[0] <= self.tn < s[0] + len(s[2]) / s[1]
                            and abs(m * s[1] - br) < 0.02 * br), None)
                if seg:
                    seg, m = seg
                    rate = smp if smp and m == 4 else m * seg[1]
                    k = max(0, math.ceil((self.tn - seg[0]) * rate - 0.5 - 1e-6))
                    self.tn = seg[0] + (k + 0.5) / rate
                    if self.tn > now:
                        return
                    j = int(k * seg[1] / rate) if smp and m == 4 else k // m
                    if j >= len(seg[2]):
                        continue
                    bit = int(seg[2][j])
                    step = 1 / rate
                else:
                    if self.tn > now:
                        return
                    bit, step = int(rng.integers(0, 2)), 1 / br
                if flip and rng.random() < flip:
                    bit ^= 1
                self.take(bit)
                self.tn += step

        def take(self, bit):
            if self.done:
                return
            if not self.pkt:
                self.sr = (self.sr << 1 | bit) & 0xFFFFFFFF
                four = self.r[0x59] & 8
                want = self.r[0x5A] << 16 | self.r[0x5B] if four else self.r[0x5A]
                if (self.sr if four else self.sr & 0xFFFF) == want:
                    self.pkt, self.nbit, self.acc = True, 0, 0
                    self.starts += 1
                return
            self.acc = self.acc << 1 | bit
            self.nbit += 1
            if self.nbit % 16 == 0:
                v = self.acc & 0xFFFF
                if self.cnt < 8:
                    self.ring[self.wp] = (v & 0xFF) << 8 | v >> 8
                    self.wp, self.cnt = (self.wp + 1) % 8, self.cnt + 1
                else:
                    self.over += 1
            n = ((self.r[0x5D] >> 5 & 7) << 8 | self.r[0x5D] >> 8) + 1
            if self.nbit >= 8 * n:
                self.done = True

        def on_stub(self, uc, addr, size, user):
            A = self.A
            name = self.names[(addr - self.stubs) // 16]
            r0, r1 = uc.reg_read(A.UC_ARM_REG_R0), uc.reg_read(A.UC_ARM_REG_R1)
            if name == "fd_write" and r0 in self.r:
                self.advance()
                self.r[r0] = r1
                if r0 == 0x59 and r1 & 0x4000:
                    self.cnt, self.rp = 0, self.wp
                if r0 == 0x59 and r1 & 0x1000:
                    self.sr, self.pkt, self.done = 0, False, False
            if name == "fd_read" and r0 == 0x5F:
                self.advance()
                ret = self.ring[self.rp]
                if self.cnt:
                    self.rp, self.cnt = (self.rp + 1) % 8, self.cnt - 1
                self.tick(self.fd_us * 96)
                uc.reg_write(A.UC_ARM_REG_R0, ret)
                return
            super().on_stub(uc, addr, size, user)

    return Chip


def lines_of(said):
    return pocsag.parse_log(["K6 MOD say " + s for s in said])


@need_clang
def test_pocsag_440_on_the_model():
    """the bench build's three settings on the bench's signal (every baud, every second round the
    other way up; some rounds sent 0.3% fast or slow, and a bit or sample in 2000 wrong): every
    transmission's three messages come out exact, 1200 baud through the 4800 sampler"""
    pytest.importorskip("unicorn")
    import k6_live_emu as le
    scale, wait = 600, 0.2
    code, entry = le.build("x", [("SLOT_SCALE", f"{scale}u"), ("WAIT_MS", f"{int(wait * 1000)}u")],
                           os.path.join(K6, "mod", "pocsag_440.c"))
    total = wait + 3 * 60 * scale / 1000
    segs, t, r = [], 0.5, 1
    while t < total - 1:
        for baud in pocsag.BAUDS:
            bits = pocsag.transmission_bits(pocsag.bench_messages(r, baud))
            inv = r % 2 == 0
            true = baud * (1 + 0.003 * ((r % 3) - 1))
            segs.append((t, true, (1 - bits) if inv else bits, r, baud, inv))
            t += len(bits) / true + 0.6
        r += 1
    Chip = plain_chip(le, segs, flip=5e-4)
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, 5.49e6, 1.5, max_s=total + 20, level=8,
             scratch=4096)
    res = e.run()
    assert res is not None, e.said[-5:]
    ret, stack = res
    assert stack < 600, stack
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    assert e.said[0] == "pocsag start hz=440850000 n=3 tune=1 text=1", e.said[0]
    assert e.said[-1].startswith("pocsag end ") and e.over == 0, (e.said[-1], e.over)
    sets, caps, msgs = lines_of(e.said)
    assert sorted(sets) == list(range(3)), sets
    start = {n: int(v["t"]) / 1000 for n, v in sets.items()}
    ends = {n: start.get(n + 1, total) for n in sets}
    known = pocsag.expected()
    exact = {(known[m["text"]][0], known[m["text"]][1], known[m["text"]][2], m["set"])
             for m in msgs if m["text"] in known and m["err"] == 0}
    checked = 0
    for t0, true, bits, r, baud, inv in segs:
        t1 = t0 + len(bits) / true
        n = next((k for k in sets if start[k] + 0.05 < t0 and t1 < ends[k] - 0.05), None)
        if n is None:
            continue                                  # across a change of setting
        checked += 1
        got = [(r, baud, k, n) in exact for k in range(3)]
        assert got == [True] * 3, (n, r, baud, inv, got, [c for c in caps if c["set"] == n])
    assert checked >= 20, checked
    # no message that was not sent, and the false starts said as such
    assert all(m["text"] in known for m in msgs), [m for m in msgs if m["text"] not in known]
    assert ret == len(msgs)
    fixed = sum(int(c["f"]) for c in caps)
    assert fixed > 0 and all(int(c["x"]) == 0 for c in caps), caps
    for reg in (0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72, 0x2B, 0x3F):
        assert e.writes[reg][-1] == 0, hex(reg)                       # put back as read


def run_plain(src, defs, segs, total, smp=None):
    import k6_live_emu as le
    code, entry = le.build("x", defs, src)
    Chip = plain_chip(le, segs, smp=smp)
    e = Chip(code, entry, np.zeros(16, np.uint8), 0, 5.49e6, 1.5, max_s=total, level=8, scratch=4096)
    res = e.run()
    assert res is not None, e.said[-5:]
    return e, res[0]


@need_clang
def test_outside_the_amateur_band_counts_only():
    """the same receiver built for a channel outside 430-450 MHz says counts and never a page's
    address or text (US ECPA), and the code that would is not in the module"""
    pytest.importorskip("unicorn")
    from modem import k6mod
    import tempfile
    src = os.path.join(K6, "mod", "pocsag.c")
    sizes = {}
    for hz in ("439987500u", "157450000u"):
        with tempfile.TemporaryDirectory() as d:
            w = os.path.join(d, "w.c")
            with open(w, "w") as f:
                f.write(f"#define POC_HZ {hz}\n#include \"{src}\"\n")
            sizes[hz] = len(k6mod.build(w)[0])
    assert sizes["157450000u"] < sizes["439987500u"] - 300, sizes
    segs, t = [], 0.4
    for r in range(1, 4):
        bits = pocsag.transmission_bits(pocsag.bench_messages(r, 1200))
        segs.append((t, 1200.0, bits))
        t += len(bits) / 1200 + 0.5
    e, ret = run_plain(src, [("POC_HZ", "157450000u"), ("FE_S", "12u")], segs, t + 15)
    assert e.said[0] == "pocsag start hz=157450000 n=1 tune=1 text=0", e.said[0]
    sets, caps, msgs = lines_of(e.said)
    assert len(caps) == 3 and all(c["q"] == "4" and c["m"] == "3" for c in caps), caps
    assert not msgs and not any("AG7EW" in ln or ln.startswith(("m ", "+ ")) for ln in e.said), e.said
    assert ret == 9


# ---- NOAA SAME: the probe (whether the chip reads SAME at all is the bench's question) ----
def test_same_bursts_read_back():
    from modem import same
    bits = same.burst_bits("AG7EW SAME FORMAT TEST 3")
    words = [int("".join(map(str, bits[i:i + 16])), 2) for i in range(0, len(bits) - 15, 16)]
    assert words[0] == 0xD5D5                       # AB, low bit first, as the chip packs it
    assert same.best_text(words)[0].endswith("AG7EW SAME FORMAT TEST 3")
    y = same.bench_wav(rounds=1)
    assert 4.5 < len(y) / same.FS < 5.5


@need_clang
def test_samecap_on_the_model():
    """samecap against the plain receiver's model as if the chip handed over SAME's bits (what the
    datasheet's SAME mode would do): every setting with the sync word D5D5 captures the bursts
    and the report reads their text"""
    pytest.importorskip("unicorn")
    from modem import same
    segs, t, r = [], 0.3, 1
    while t < 4 * 40 * 0.15 + 0.5:
        b = same.burst_bits(f"AG7EW SAME FORMAT TEST {r}")
        segs.append((t, same.BAUD, b))
        t, r = t + len(b) / same.BAUD + 1.0, r + 1
    e, ret = run_plain(os.path.join(K6, "mod", "samecap.c"), [("SLOT_SCALE", "150u"), ("WAIT_MS", "200u")],
                       segs, t + 5)
    assert e.said[0] == "samecap start hz=440850000 n=4" and e.said[-1].startswith("samecap end"), e.said[-3:]
    assert all(len(ln) <= 60 for ln in e.said)
    rep = same.report(["K6 MOD say " + s for s in e.said])
    got = [ln for ln in rep if "AG7EW SAME FORMAT TEST" in ln]
    assert ret >= 4 and len(got) >= 4 and all(" s=0 " in ln or " s=2 " in ln for ln in got), rep


# ---- the probe pocdump: the chip's words for the host to lay against the bench's bits ----
def test_dump_slicing_and_alignment():
    bits = pocsag.transmission_bits(pocsag.bench_messages(7, 1200))
    got, g, off = pocsag.slice_samples(np.repeat(bits[500:900], 4), 4)
    assert abs(g - 4) < 0.05 and off == 0 and len(got) > 380
    c = np.delete(bits[540:1400], [100, 300]) ^ 1         # two bits dropped, upside down
    c[50] ^= 1
    r, inv, ops = pocsag.lay_against_bench(c, 1200)
    assert (r, inv) == (7, 1), (r, inv)
    assert [k for k, _, _ in ops].count("d") == 2 and [k for k, _, _ in ops].count("x") == 1


@need_clang
def test_pocdump_on_the_model():
    """pocdump's six settings on the model, 1200 and 2400 baud: the chip at the line's baud hands
    over the bits as sent, and at 4800 it samples them four or two times; the host lays every
    capture against the round it came from with nothing wrong, dropped or added"""
    pytest.importorskip("unicorn")
    scale, wait = 300, 0.2
    total = wait + 6 * 40 * scale / 1000
    segs, t, r = [], 0.5, 1
    while t < total - 1:
        for baud in (1200, 2400):
            bits = pocsag.transmission_bits(pocsag.bench_messages(r, baud))
            inv = r % 2 == 0
            segs.append((t, float(baud), (1 - bits) if inv else bits))
            t += len(bits) / baud + 0.6
        r += 1
    e, ret = run_plain(os.path.join(K6, "mod", "pocdump.c"),
                       [("SLOT_SCALE", f"{scale}u"), ("WAIT_MS", f"{int(wait * 1000)}u")], segs, total + 10)
    assert e.said[0] == "pocdump start hz=440850000 n=6" and e.said[-1].startswith("pocdump end"), e.said[-3:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    caps = pocsag.dump_captures(["K6 MOD say " + s for s in e.said])
    assert ret == len(caps) >= 10, (ret, len(caps))
    rep = pocsag.dump_report(["K6 MOD say " + s for s in e.said])
    seen = set()
    for c in caps:
        seen.add(c["s"])
    assert seen == {"0", "1", "2", "3", "4", "5"}, seen
    lays = [ln for ln in rep if ln.startswith("  round ")]
    assert len(lays) == len(caps), rep
    assert all("wrong 0, dropped 0, added 0" in ln for ln in lays), rep
    assert sum("first batch: right 16" in ln for ln in rep) >= len(caps) - 2, rep


def test_same_sampled_reads_back():
    """SAME through the plain receiver as a sampler, on the host: at 6347 a second any phase and
    some noise reads; at 4166.7 only a clock on the mark's peaks does"""
    from modem import same
    b = same.burst_bits("ZCZC-WXR-RWT-053033+0015-3151800-KSEW/NWS-")
    for ph in (0.0, 0.3, 0.7):
        x = same.df_samples(b, phase=ph, rate=6347.6 / same.DF_RATE, noise=0.2, lead=0.01, seed=5)
        t, n, _ = same.df_best(x, 6347.6)
        assert t.startswith("ZCZC-WXR-RWT-053033+0015-3151800-KSEW/NWS") and n >= 14, (ph, t, n)
    t, n, _ = same.df_best(same.df_samples(b, phase=0.5, lead=0.01), same.DF_RATE)
    assert t.startswith("ZCZC-WXR-RWT-053033+0015-3151800-KSEW/NWS") and n == 16, (t, n)


@need_clang
def test_samedf_on_the_model():
    """samedf on the model, the chip as a fixed-clock sampler of the bursts' audio sign: its
    settings at 4166.7 (on the mark's peaks), 4800 and 6347 a second capture the bursts and the
    host reads their text from the samples"""
    pytest.importorskip("unicorn")
    from modem import same
    scale, wait = 150, 0.2
    total = wait + 4 * 40 * scale / 1000
    segs, t, r = [], 0.4, 1
    while t < total - 1:
        b = same.burst_bits(f"AG7EW SAME FORMAT TEST {r}")
        for fs in (same.DF_RATE, 4800.0, 6347.6):
            x = same.df_samples(b, phase=0.5, rate=fs / same.DF_RATE, seed=r)
            segs.append((t, fs, x))
        t, r = t + len(b) / same.BAUD + 1.2, r + 1
    e, ret = run_plain(os.path.join(K6, "mod", "samedf.c"),
                       [("SLOT_SCALE", f"{scale}u"), ("WAIT_MS", f"{int(wait * 1000)}u")], segs, total + 5)
    assert e.said[0] == "samedf start hz=440850000 n=4" and e.said[-1].startswith("samedf end"), e.said[-3:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    rep = same.df_report(["K6 MOD say " + s for s in e.said])
    caps = [ln for ln in rep if ln.startswith("c")]
    texts = [ln for ln in rep if ln.startswith("  read")]
    assert ret == len(caps), (ret, rep)
    for k in ("0", "1", "2"):
        got = [tx for c, tx in zip(caps, texts) if f" s={k} " in c and "AG7EW SAME FORMAT TEST" in tx]
        assert got, (k, rep)


@need_clang
def test_samerx_440_on_the_model():
    """samerx's three rates on the model (the chip a fixed-clock sampler of the audio's sign, at
    any phase; noise of a fifth of the tone at 6347 a second, a tenth at 4800): every burst wholly inside
    a setting is read exactly, at 4166.7 a second with the clock on the mark's peaks"""
    pytest.importorskip("unicorn")
    from modem import same
    scale, wait = 150, 0.2
    fe = 45 * scale / 1000
    total = wait + 3 * fe
    segs, sent, t, r = [], [], 0.4, 1
    rng = np.random.default_rng(4)
    while t < total - 1:
        text = f"ZCZC-WXR-RWT-053033+0015-31518{r:02d}-KSEW/NWS-"
        b = same.burst_bits(text)
        for fs, noise in ((6347.6, 0.2), (4800.0, 0.1), (same.DF_RATE, 0.0)):
            ph = 0.5 if noise == 0 else float(rng.uniform(0, 1))
            segs.append((t, fs, same.df_samples(b, phase=ph, rate=fs / same.DF_RATE, noise=noise, seed=r)))
        sent.append((t, t + len(b) / same.BAUD, text))
        t, r = t + len(b) / same.BAUD + 1.0, r + 1
    e, ret = run_plain(os.path.join(K6, "mod", "samerx_440.c"),
                       [("SLOT_SCALE", f"{scale}u"), ("WAIT_MS", f"{int(wait * 1000)}u")], segs, total + 5)
    assert e.said[0] == "samerx start hz=440850000 n=3" and e.said[-1].startswith("samerx end"), e.said[-3:]
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    heads, cur = [], None
    for ln in e.said:
        if ln.startswith("h "):
            f = dict(kv.split("=") for kv in ln.split()[2:])
            cur = [int(f["s"]), int(f["t"]) / 1000, ""]
            heads.append(cur)
        elif ln.startswith("+ ") and cur:
            cur[2] += ln[2:]
    assert ret == len(heads)
    for k in range(3):
        t0, t1 = wait + k * fe + 0.05, wait + (k + 1) * fe - 0.6
        want = [x for a, b_, x in sent if t0 < a and b_ < t1]
        got = [h[2] for h in heads if h[0] == k]
        assert len(want) >= 2 and all(w in got for w in want), (k, want, got)


@need_clang
def test_pocsag_slices_the_bench_samples():
    """1200 baud as the chip sampled it at 4800 on the bench (run 2, pocdump's setting 5, the AF
    path as found: chatter of a sample, runs that droop; every second round upside down), handed
    to the module as the chip's samples behind a preamble of its own: every capture's first batch
    comes out with no codeword past help and the round's first message read (the dumps end
    inside the next batch: what follows is the model's noise)"""
    pytest.importorskip("unicorn")
    import gzip
    path = os.path.join(ROOT, "tests", "data", "k6", "pocdump-run2-20261111.log.gz")
    with gzip.open(path, "rt") as f:
        caps = pocsag.dump_captures(f)
    assert len(caps) == 6
    segs, t = [], 0.3
    pre = [1, 1, 1, 1, 0, 0, 0, 0] * 240                   # 480 bits of 1010, four samples each
    for c in caps:
        smp = np.array(pre + list(pocsag.word_bits(c["words"])), np.uint8)
        segs.append((t, 4800.0, smp))
        t += len(smp) / 4800 + 3.0
    e, ret = run_plain(os.path.join(K6, "mod", "pocsag.c"), [("FE_S", f"{int(t) + 2}u")], segs, t + 8)
    _, got, msgs = lines_of(e.said)
    assert len(got) == 6 and all(c["b"] == "1200" and int(c["q"]) >= 1 for c in got), e.said
    assert [int(c["i"]) for c in got] == [0, 1] * 3, got
    first = [c["msgs"][0]["text"] for c in got]
    assert all(m.startswith("AG7EW TEST ") for m in first), first
    known = pocsag.expected()
    exact = {known[m][0] for m in first if m in known and known[m][1:] == (1200, 0)}
    assert len(exact) >= 5, first


@need_clang
def test_pocdump_1200_on_the_model():
    """the dump build says every sampled word of its first six 1200 baud transmissions, either way
    up, and the host lays each one, sliced as the module slices, against its round with nothing
    wrong, dropped or added, while the module still reads every message"""
    pytest.importorskip("unicorn")
    segs, t = [], 0.4
    for r in range(1, 9):
        bits = pocsag.transmission_bits(pocsag.bench_messages(r, 1200))
        segs.append((t, 1200.0, (1 - bits) if r % 2 == 0 else bits))
        t += len(bits) / 1200 + 2.0
    e, ret = run_plain(os.path.join(K6, "mod", "pocdump_1200.c"),
                       [("WAIT_MS", "0u"), ("FE_S", f"{int(t) + 1}u")], segs, t + 10)
    assert all(len(ln) <= 60 for ln in e.said), max(e.said, key=len)
    _, got, msgs = lines_of(e.said)
    assert len(got) == 8 and ret == 24 and all(m["err"] == 0 for m in msgs), got
    caps = pocsag.dump_captures(["K6 MOD say " + s for s in e.said])
    assert len(caps) == 6 and [c["i"] for c in caps] == ["0", "1"] * 3, caps
    rep = pocsag.dump_report(["K6 MOD say " + s for s in e.said])
    lays = [ln for ln in rep if ln.startswith("  round ")]
    assert len(lays) == 6 and all("wrong 0, dropped 0, added 0" in ln for ln in lays), rep
    assert [ln.split()[1].rstrip(":") for ln in lays] == [str(r) for r in range(1, 7)], lays


@need_clang
@pytest.mark.parametrize("smp", [4716, 4880])
def test_pocdump_1200_sampler_off_rate_on_the_model(smp):
    """the sampler not locked to the data (the bench's text came at 3.93 samples a bit, run 4; and
    as far the other way): the reads follow the chip word by word, so the dump lays every
    transmission against its round with nothing wrong, dropped or added, and every message is
    read"""
    pytest.importorskip("unicorn")
    segs, t = [], 0.4
    for r in range(1, 9):
        bits = pocsag.transmission_bits(pocsag.bench_messages(r, 1200))
        segs.append((t, 1200.0, (1 - bits) if r % 2 == 0 else bits))
        t += len(bits) / 1200 + 2.0
    e, ret = run_plain(os.path.join(K6, "mod", "pocdump_1200.c"),
                       [("WAIT_MS", "0u"), ("FE_S", f"{int(t) + 1}u")], segs, t + 10, smp=smp)
    _, got, msgs = lines_of(e.said)
    assert len(got) == 8 and ret == 24 and all(m["err"] == 0 for m in msgs), got
    rep = pocsag.dump_report(["K6 MOD say " + s for s in e.said])
    lays = [ln for ln in rep if ln.startswith("  round ")]
    assert len(lays) == 6 and all("wrong 0, dropped 0, added 0" in ln for ln in lays), rep
