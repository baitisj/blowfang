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
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        y, times = bench_wav(a.rounds, tuple(int(b) for b in a.bauds.split(",")), level=a.level, gap=a.gap)
        write_wav(a.out, y)
        print(f"{a.out}: {len(y) / FS:.1f} s, {len(times)} transmissions")
        for t0, t1, r, baud, inv in times[:6]:
            print(f"  {t0:7.2f}-{t1:7.2f} s  round {r} {baud} baud{' inverted' if inv else ''}")
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
