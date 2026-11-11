"""POCSAG for the K6's pager receiver (c/k6/mod/pocsag.c, docs/k6-pocsag.md): the format, a bench
test signal, and a reader for the module's serial lines.

POCSAG (ITU-R M.584) is direct FSK: the carrier itself sits high or low for each bit, at 512, 1200
or 2400 baud, so behind an FM discriminator it is a plain NRZ line. That is what the FD6818's plain
FSK receiver (REG_58 RX mode 0) is made for: it slices the discriminator's sign on a clock that
follows the data's own transitions, finds a sync word of two or four bytes and hands over bytes.

A transmission: a preamble of at least 576 bits of 1010..., then batches. A batch is the sync
codeword 7CD215D8 and eight frames of two codewords each. A codeword is 32 bits, the top bit
first: a flag (0 address, 1 message), 20 bits, a BCH(31,21) check of 10 bits (generator
x^10+x^9+x^8+x^6+x^5+x^3+1) and an even parity bit. An address codeword holds the top 18 bits of
the 21-bit address (RIC) and 2 function bits; the low 3 bits of the RIC are the frame it sits in.
The message codewords after it carry 20 bits each: 7-bit characters (alphanumeric) or 4-bit digits
(numeric), each the low bit first. A message ends at the next address codeword or the idle
codeword 7A89C197.

  python3 -m modem.pocsag wav OUT.wav [--rounds 20] [--bauds 512,1200,2400] [--level 0.35]
                                       the bench's test signal: every round one transmission at
                                       each baud (three messages, one long enough for three
                                       batches), every second round upside down; text
                                       'AG7EW TEST ...' (send it with tools/k6_bench.py send)
  python3 -m modem.pocsag report LOG   what pocsag_440 made of it: its p, k and m lines in the
                                       K6's serial log, against the messages the rounds carry
  python3 -m modem.pocsag decode BITS  a string of 0 and 1 (or a file of them): the messages in it
  python3 -m modem.pocsag dump LOG     what the probe pocdump's captures hold: each laid against
                                       the bench's bits (the round found from them): bits wrong,
                                       dropped and added, where, and the codewords after the sync

The bench's messages are made from the round and the baud alone, so the report knows them without
a key file. On a channel outside the amateur band the module says counts only (US ECPA: a page's
text is protected), and so does this reader: `report` never prints text from a line that is not
one of the bench's own messages unless --show-text is given (for the amateur band, e.g. DAPNET).
SPDX-License-Identifier: MIT
"""
import argparse
import re
import sys

import numpy as np

SYNC = 0x7CD215D8
IDLE = 0x7A89C197
GEN = 0x769            # x^10 + x^9 + x^8 + x^6 + x^5 + x^3 + 1
PREAMBLE = 576
FS = 48000
BAUDS = (512, 1200, 2400)
NUMERIC = "0123456789*U -)("

# the bench's messages: (RIC, function, kind)
BENCH = (
    (1234, 3, "alpha"),     # frame 2
    (2005, 0, "numeric"),   # frame 5
    (4711, 3, "alpha"),     # frame 7: the long one
)
PANGRAM = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789"


# ---- the code ----

def bch_rem(c31):
    """the remainder of a 31-bit word (BCH data and check) over the generator: 0 for a codeword"""
    for i in range(30, 9, -1):
        if c31 >> i & 1:
            c31 ^= GEN << (i - 10)
    return c31


def parity(v):
    return bin(v).count("1") & 1


def codeword(data21):
    """a codeword from its top 21 bits (flag and 20 bits): check bits and even parity added"""
    c = data21 << 10
    c |= bch_rem(c)
    c = c << 1
    return c | parity(c)


_SYN = [bch_rem(1 << i) for i in range(31)]


def fix(cw):
    """(codeword, bits fixed) with up to two wrong bits put right, or (cw, -1): what the module's
    fix() does"""
    s = bch_rem(cw >> 1)
    if s == 0:
        return (cw, 0) if not parity(cw) else (cw ^ 1, 1)
    for i in range(31):
        if _SYN[i] == s:
            c = cw ^ (2 << i)
            return (c, 1) if not parity(c) else (c ^ 1, 2)
    for i in range(31):
        for j in range(i + 1, 31):
            if _SYN[i] ^ _SYN[j] == s:
                c = cw ^ (2 << i) ^ (2 << j)
                return (c, 2) if not parity(c) else (cw, -1)
    return cw, -1


def address_cw(ric, func):
    return codeword((ric >> 3) << 2 | (func & 3))


def message_bits(text, kind):
    """the message's bits in the order they go on the air"""
    bits = []
    if kind == "numeric":
        for ch in text:
            d = NUMERIC.index(ch)
            bits += [d >> i & 1 for i in range(4)]
    else:
        for ch in text:
            c = ord(ch) & 0x7F
            bits += [c >> i & 1 for i in range(7)]
        # an EOT after the text, then zeros to the codeword's end
        bits += [0, 0, 1, 0, 0, 0, 0]
        while len(bits) % 20:
            bits.append(0)
    if kind == "numeric":
        while len(bits) % 20:
            bits += [0, 0, 1, 1]       # space (C), low bit first
    return bits


def message_cws(text, kind):
    b = message_bits(text, kind)
    out = []
    for i in range(0, len(b), 20):
        v = 0
        for x in b[i:i + 20]:
            v = v << 1 | x
        out.append(codeword(1 << 20 | v))
    return out


def batches(messages):
    """codewords of a transmission (without its preamble and sync words): [(ric, func, text,
    kind)] -> a list of batches, each 16 codewords"""
    cws = []
    for ric, func, text, kind in messages:
        frame = ric & 7
        while len(cws) % 16 != 2 * frame:
            cws.append(IDLE)
        cws.append(address_cw(ric, func))
        cws += message_cws(text, kind)
    cws.append(IDLE)          # a message ends at an idle codeword (or the next address)
    while len(cws) % 16:
        cws.append(IDLE)
    return [cws[i:i + 16] for i in range(0, len(cws), 16)]


def transmission_bits(messages, preamble=PREAMBLE):
    bits = [1 - (i & 1) for i in range(preamble)]
    for b in batches(messages):
        for cw in [SYNC] + b:
            bits += [cw >> (31 - i) & 1 for i in range(32)]
    return np.array(bits, np.uint8)


# ---- the bench signal ----

def bench_messages(rnd, baud):
    return [
        (BENCH[0][0], BENCH[0][1], f"AG7EW TEST {rnd} {baud}", BENCH[0][2]),
        (BENCH[1][0], BENCH[1][1], f"{rnd:03d}-{baud}", BENCH[1][2]),
        (BENCH[2][0], BENCH[2][1], f"AG7EW POCSAG {baud} R{rnd} {PANGRAM}", BENCH[2][2]),
    ]


def nrz_audio(bits, baud, fs=FS, invert=False, bw=1.0):
    """the bits as an NRZ line at fs (a 1 low: ITU's sense, the lower frequency), low-passed by a
    4th-order Bessel filter at bw x baud so the IC-7100's audio chain sees no sharp edges"""
    from scipy.signal import bessel, lfilter
    n = len(bits)
    t = (np.arange(int(np.ceil(n * fs / baud))) * baud / fs).astype(int)
    x = np.where(np.asarray(bits)[np.minimum(t, n - 1)] > 0, -1.0, 1.0)
    if invert:
        x = -x
    b, a = bessel(4, min(0.95, 2 * bw * baud / fs), norm="mag")
    return lfilter(b, a, x)


def bench_wav(rounds=20, bauds=BAUDS, fs=FS, level=0.35, gap=0.6, lead=0.4):
    """the test signal: (audio, [(t0, t1, round, baud, inverted)])"""
    parts, times, t = [np.zeros(int(lead * fs))], [], lead
    for r in range(1, rounds + 1):
        for baud in bauds:
            inv = r % 2 == 0
            x = nrz_audio(transmission_bits(bench_messages(r, baud)), baud, fs, inv)
            times.append((t, t + len(x) / fs, r, baud, inv))
            parts += [x, np.zeros(int(gap * fs))]
            t += len(x) / fs + gap
    y = np.concatenate(parts)
    return (level * y / np.max(np.abs(y))).astype(np.float32), times


def write_wav(path, y, fs=FS):
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


# ---- reading bits ----

def text_of(bits, kind):
    out = []
    if kind == "numeric":
        for i in range(0, len(bits) - 3, 4):
            out.append(NUMERIC[sum(bits[i + k] << k for k in range(4))])
        return "".join(out).rstrip()
    for i in range(0, len(bits) - 6, 7):
        c = sum(bits[i + k] << k for k in range(7))
        if c in (0, 3, 4):
            continue
        out.append(chr(c) if 32 <= c < 127 else ".")
    return "".join(out)


def decode_bits(bits, most=2):
    """messages in a bit stream, either way up: [(ric, func, text, errors)] (alphanumeric for
    function != 0, as the module reads them)"""
    bits = [int(b) for b in bits]
    msgs, cur, sr, st, nb, ncw, inv = [], None, 0, "hunt", 0, 0, 0

    def end():
        nonlocal cur
        if cur:
            ric, fn, data, err = cur
            msgs.append((ric, fn, text_of(data, "numeric" if fn == 0 else "alpha"), err))
        cur = None
    for b in bits:
        b ^= inv
        sr = (sr << 1 | b) & 0xFFFFFFFF
        if st == "hunt":
            if bin(sr ^ SYNC).count("1") <= most:
                st, nb, ncw = "batch", 0, 0
            elif bin(sr ^ SYNC ^ 0xFFFFFFFF).count("1") <= most:
                inv ^= 1
                st, nb, ncw = "batch", 0, 0
            continue
        nb += 1
        if nb < 32:
            continue
        nb = 0
        if st == "sync":
            if bin(sr ^ SYNC).count("1") <= 4:
                st, ncw = "batch", 0
            else:
                st = "hunt"
                end()
            continue
        cw, e = fix(sr)
        frame = ncw >> 1
        ncw += 1
        if ncw == 16:
            st = "sync"
        if e < 0:
            if cur:
                cur[2].extend([0] * 20)
                cur[3] += 1
            continue
        if cw == IDLE:
            end()
        elif not cw >> 31:
            end()
            cur = [(cw >> 13 & 0x3FFFF) << 3 | frame, cw >> 11 & 3, [], 0]
        elif cur:
            cur[2].extend((cw >> (30 - i)) & 1 for i in range(20))
    end()
    return msgs


# ---- the module's lines ----

LINE = re.compile(r"K6 MOD say (.*)")


def say_lines(lines):
    for ln in lines:
        m = LINE.search(ln)
        if m:
            yield m.group(1).rstrip("\r\n")


def parse_log(lines):
    """the module's lines: (settings {n: dict}, captures [dict], messages [dict])"""
    sets, caps, msgs, cur_set, cap = {}, [], [], None, None
    for s in say_lines(lines):
        f = s.split()
        if not f:
            continue
        if f[0] == "s" and len(f) > 1 and f[1].isdigit():
            cur_set = int(f[1])
            sets[cur_set] = dict(kv.split("=", 1) for kv in f[2:] if "=" in kv)
        elif f[0] == "p" and len(f) > 1 and f[1].isdigit():
            cap = dict(kv.split("=", 1) for kv in f[2:] if "=" in kv)
            cap["n"], cap["set"], cap["msgs"] = int(f[1]), cur_set, []
            caps.append(cap)
        elif f[0] == "k" and cap is not None and len(f) > 1 and f[1].isdigit() and int(f[1]) == cap["n"]:
            cap.update(dict(kv.split("=", 1) for kv in f[2:] if "=" in kv))
        elif f[0] == "m" and len(f) >= 4:
            m = {"ric": int(f[1]), "fn": int(f[2]), "err": int(f[3].lstrip("e")),
                 "text": s.split(" ", 4)[4] if len(f) > 4 else "", "set": cur_set,
                 "cap": cap["n"] if cap else None, "baud": int(cap.get("b", 0)) if cap else 0}
            msgs.append(m)
            if cap is not None:
                cap["msgs"].append(m)
        elif f[0] == "+" and msgs:
            msgs[-1]["text"] += s[2:]
    for m in msgs:
        m["text"] = m["text"].rstrip()
    return sets, caps, msgs


def expected(rounds=60, bauds=BAUDS):
    """{text: (round, baud, which)} of every bench message"""
    out = {}
    for r in range(1, rounds + 1):
        for baud in bauds:
            for k, (_, _, text, _) in enumerate(bench_messages(r, baud)):
                out[text] = (r, baud, k)
    return out


def report(lines, show_text=False):
    sets, caps, msgs = parse_log(lines)
    known = expected()
    out = []
    for n in sorted(sets) or [None]:
        cs = [c for c in caps if c["set"] == n]
        ms = [m for m in msgs if m["set"] == n]
        head = f"setting {n}: " + " ".join(f"{k}={v}" for k, v in sets.get(n, {}).items() if k != "t")
        out.append(head)
        for baud in sorted({int(c.get("b", 0)) for c in cs}):
            cb = [c for c in cs if int(c.get("b", 0)) == baud]
            mb = [m for m in ms if m["baud"] == baud]
            good = [m for m in mb if m["text"] in known and m["err"] == 0]
            which = {}
            for m in good:
                which.setdefault(known[m["text"]][2], set()).add(known[m["text"]][0])
            tot = lambda k: sum(int(c.get(k, 0)) for c in cb)
            out.append(f"  {baud:4d} baud: captures {len(cb)}, syncs {tot('q')}, codewords ok {tot('ok')}"
                       f" fixed {tot('f')} bad {tot('x')}; messages {len(mb)}, exact {len(good)}"
                       f" (short {len(which.get(0, ()))}, numeric {len(which.get(1, ()))},"
                       f" long {len(which.get(2, ()))} rounds); inverted {sum(int(c.get('i', 0)) for c in cb)}")
            for m in mb:
                if m["text"] not in known or m["err"]:
                    t = m["text"] if (show_text or m["text"].startswith("AG7EW")) else f"<{len(m['text'])} chars>"
                    out.append(f"    ric {m['ric']} fn {m['fn']} e {m['err']}: {t}")
    return out


# ---- the probe pocdump (c/k6/mod/pocdump.c): what the chip hands over ----

DUMP_GAP = None
DUMP_LAG = 5                  # words pocdump's reads stay behind the chip


def dump_captures(lines):
    """pocdump's captures: [dict] with the setting's fields (cfg, b, sy, m), the c line's and
    'words' (None for a gap: an empty FIFO read)"""
    sets, caps, cur = {}, {}, None
    for s in say_lines(lines):
        f = s.split()
        if len(f) < 2 or not f[1].isdigit():
            continue
        kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
        if f[0] == "s":
            cur = int(f[1])
            sets[cur] = kv
        elif f[0] == "c":
            caps[int(f[1])] = dict(sets.get(int(kv.get("s", -1)), {}), **kv, n=int(f[1]), words=[])
        elif f[0] == "d" and len(f) >= 4 and int(f[1]) in caps:
            for k in range(0, len(f[3]) - 3, 4):
                w = f[3][k:k + 4]
                caps[int(f[1])]["words"].append(DUMP_GAP if w == "----" else int(w, 16))
    return [caps[n] for n in sorted(caps)]


def word_bits(words):
    return np.array([w >> (15 - i) & 1 for w in words if w is not None for i in range(16)], np.uint8)


def runs_of(bits):
    """[(bit, run length)]"""
    out = []
    for b in bits:
        if out and out[-1][0] == b:
            out[-1][1] += 1
        else:
            out.append([int(b), 1])
    return out


def slice_samples(bits, m):
    """a line sampled m times a bit back to its bits: (bits, samples a bit as fitted, share of runs
    more than a quarter bit off a whole number of bits); the end runs are left out"""
    rs = runs_of(bits)[1:-1]
    if not rs:
        return np.zeros(0, np.uint8), float(m), 0.0
    n = np.array([r[1] for r in rs], float)
    grid = np.linspace(0.8 * m, 1.2 * m, 161)
    err = [np.sum((n / g - np.maximum(1, np.round(n / g))) ** 2) for g in grid]
    g = float(grid[int(np.argmin(err))])
    k = np.maximum(1, np.round(n / g)).astype(int)
    off = float(np.mean(np.abs(n / g - k) > 0.25))
    return np.array([b for (b, _), c in zip(rs, k) for _ in range(c)], np.uint8), g, off


def align(c, e, start, band=96):
    """c laid against e by edit distance, c starting near e[start] (within band) and wandering at
    most band bits; e's ends free. -> (ops [(kind, i in c, j in e)], cost); kind '=' same,
    'x' wrong, 'd' a bit of e that c dropped, 'a' a bit c added, '+' past e's end"""
    n, w, big = len(c), 2 * band + 1, 1 << 14
    e = np.asarray(e, np.int16)
    D = np.empty((n + 1, w), np.int32)
    D[0] = 0
    ks = np.arange(w)
    for i in range(1, n + 1):
        j = start + i + ks - band                       # e index + 1 of cell k
        ok = (j >= 1) & (j <= len(e))
        sub = np.where(ok, e[np.clip(j - 1, 0, len(e) - 1)] != c[i - 1], big)
        row = D[i - 1] + sub
        row[:-1] = np.minimum(row[:-1], D[i - 1][1:] + 1)
        row = np.minimum.accumulate(row - ks) + ks       # drops: from k - 1 in the same row
        D[i] = np.minimum(row, big)
    # c's end free too (bits after the transmission): the cheapest cell where e is used up
    ends = [(int(D[n][k]), -n, k) for k in range(w)]
    for i in range(n):
        k = len(e) - start - i + band
        if 0 <= k < w:
            ends.append((int(D[i][k]), -i, k))
    cost, i, k = min(ends)
    i, ops = -i, [("+", q, None) for q in range(n - 1, -i - 1, -1)]
    while i > 0:
        j = start + i + k - band
        sub = int(not (1 <= j <= len(e)) or e[j - 1] != c[i - 1]) * (1 if 1 <= j <= len(e) else big)
        if 1 <= j <= len(e) and D[i][k] == D[i - 1][k] + sub:
            ops.append(("x" if sub else "=", i - 1, j - 1))
            i -= 1
        elif k + 1 < w and D[i][k] == D[i - 1][k + 1] + 1:
            ops.append(("a", i - 1, j - 1))
            i, k = i - 1, k + 1
        else:
            ops.append(("d", i - 1, j - 1))
            k -= 1
    return ops[::-1], cost


def codewords_after_sync(bits):
    """what the module would read from these bits without help: (sync at, inverted, right, put
    right, past help) for the codewords of the first batch"""
    sr = 0
    for i, b in enumerate(bits):
        sr = (sr << 1 | int(b)) & 0xFFFFFFFF
        for inv in (0, 1):
            if i >= 31 and bin(sr ^ SYNC ^ (0xFFFFFFFF if inv else 0)).count("1") <= 2:
                ok = fx = bad = 0
                for k in range(16):
                    p = i + 1 + 32 * k
                    if p + 32 > len(bits):
                        break
                    cw = 0
                    for b2 in bits[p:p + 32]:
                        cw = cw << 1 | (int(b2) ^ inv)
                    _, e = fix(cw)
                    ok, fx, bad = ok + (e == 0), fx + (e > 0), bad + (e < 0)
                return i - 31, inv, ok, fx, bad
    return None


def lay_against_bench(bits, baud, rounds=80):
    """the bench transmission these bits came from: (round, inverted, ops) or None"""
    if len(bits) < 160:
        return None
    ref = transmission_bits(bench_messages(1, baud))
    best = None
    for inv in (0, 1):
        e = ref ^ inv
        for off in range(PREAMBLE - 200, PREAMBLE + 64):
            score = int(np.sum(e[off:off + 160] == bits[:160]))
            if best is None or score > best[0]:
                best = (score, inv, off)
    _, inv, off = best
    # the round: the first message ('AG7EW TEST <round> <baud>') is in the first batch
    head = bits[:900]
    cand = [(align(head, transmission_bits(bench_messages(r, baud)) ^ inv, off, 48)[1], r)
            for r in range(1, rounds + 1)]
    r = min(cand)[1]
    ops, _ = align(bits, transmission_bits(bench_messages(r, baud)) ^ inv, off)
    return r, inv, ops


def dump_report(lines, rounds=80):
    out = []
    for cap in dump_captures(lines):
        m = int(cap.get("m", 1))
        chip = int(cap.get("b", "0"), 16) / 10.32444
        baud = min(BAUDS, key=lambda b: abs(b - chip / m))
        words = cap["words"]
        gaps = sum(w is None for w in words)
        raw = word_bits(words)
        head = (f"c{cap['n']} s={cap.get('s')} {baud} baud, chip {chip:.0f}"
                f"{' (sampler, %d a bit)' % m if m > 1 else ''} r=-{cap.get('r', '?').lstrip('-')}: "
                f"{len(words) - gaps} words, gaps {gaps}, two alike {cap.get('q', '?')}")
        u, nw = int(cap.get("u", 0)), int(cap.get("w", 0))
        if u > 0 and not gaps:
            head += f", about {16 * (nw + DUMP_LAG - 1) * 1000 / u / m:.0f} bits/s by time"
        out.append(head)
        bits = raw
        if m > 1:
            bits, g, off = slice_samples(raw, m)
            out.append(f"  samples a bit {g:.2f}, runs a quarter bit or more off {100 * off:.1f}%, "
                       f"{len(bits)} bits")
        lay = lay_against_bench(bits, baud, rounds)
        if lay is None:
            out.append("  too short to lay against the bench's bits")
            continue
        r, inv, ops = lay
        cnt = {k: sum(1 for o in ops if o[0] == k) for k in "=xda+"}
        slips = [i for kind, i, _ in ops if kind in "da"]
        wrong = [i for kind, i, _ in ops if kind == "x"]
        j0 = next(j for k, _, j in ops if j is not None)
        out.append(f"  round {r}{' inverted' if inv else ''}: {len(bits)} bits from bit {j0}: "
                   f"wrong {cnt['x']}, dropped {cnt['d']}, added {cnt['a']}"
                   f"{', past the end %d' % cnt['+'] if cnt['+'] else ''}")
        if slips:
            out.append("  slips at " + " ".join(f"{k}{i}" for k, i, _ in ops if k in "da")[:200])
        if wrong:
            out.append("  wrong at " + " ".join(map(str, wrong))[:200])
        cw = codewords_after_sync(bits)
        if cw:
            out.append(f"  sync codeword at bit {cw[0]}{' inverted' if cw[1] else ''}; first batch: "
                       f"right {cw[2]}, put right {cw[3]}, past help {cw[4]}")
        else:
            out.append("  no sync codeword")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("wav")
    q.add_argument("out")
    q.add_argument("--rounds", type=int, default=20)
    q.add_argument("--bauds", default="512,1200,2400")
    q.add_argument("--level", type=float, default=0.35)
    q.add_argument("--gap", type=float, default=0.6)
    q = sub.add_parser("report")
    q.add_argument("log")
    q.add_argument("--show-text", action="store_true")
    q = sub.add_parser("decode")
    q.add_argument("bits")
    q = sub.add_parser("dump")
    q.add_argument("log")
    q.add_argument("--rounds", type=int, default=80)
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        y, times = bench_wav(a.rounds, tuple(int(b) for b in a.bauds.split(",")), level=a.level, gap=a.gap)
        write_wav(a.out, y)
        print(f"{a.out}: {len(y) / FS:.1f} s, {len(times)} transmissions")
        for t0, t1, r, baud, inv in times[:6]:
            print(f"  {t0:7.2f}-{t1:7.2f} s  round {r} {baud} baud{' inverted' if inv else ''}")
    elif a.cmd == "dump":
        with open(a.log, errors="replace") as f:
            print("\n".join(dump_report(f, a.rounds)))
    elif a.cmd == "report":
        with open(a.log, errors="replace") as f:
            print("\n".join(report(f, a.show_text)))
    else:
        s = a.bits
        try:
            with open(s) as f:
                s = f.read()
        except OSError:
            pass
        for ric, fn, text, err in decode_bits([c == "1" for c in s if c in "01"]):
            print(f"{ric} {fn} e{err}: {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
