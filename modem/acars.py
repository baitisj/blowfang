"""ACARS-format test blocks for the K6's FSK modem probe (c/k6/mod/acarsfsk.c,
docs/k6-acars.md): can the FD6818's own 1200/2400 Hz receiver read ACARS?

ACARS (ARINC 618) on VHF is AM with MSK on the envelope at 2400 bit/s: a bit that differs from
the one before it is half a cycle of 1200 Hz, a bit that repeats it a whole cycle of 2400 Hz.
A block: 16 characters of all ones (2400 Hz), "+" "*", SYN SYN, SOH, the mode, 7 characters of
address, an acknowledgement, a 2-character label, a block id, STX, the text, ETX, a 16-bit check
(CRC-CCITT, reflected, over the mode to the ETX) and DEL. Characters are 7 bits and odd parity,
the low bit first.

  python3 -m modem.acars probe OUT.wav [--seconds 200]   the probe's test signal (send it in FM
                                                         for the control, in AM for the test)
      [--only R] [--where 50.650]                        real blocks only; the frequency their
                                                         text names
  python3 -m modem.acars report LOG                      what the chip made of it (the module's
                                                         c and d lines in the K6's serial log)
  python3 -m modem.acars plain LOG                       dfskcap_acars' log: the plain FSK receiver
                                                         (4800 baud, two samples a bit) on a real
                                                         channel, receive only; a front end over
                                                         5000 baud is read as the chip's clock
                                                         gives it (follow_tones)
                                                         and one under 3000 baud as a sample
                                                         a bit, the data itself (nrz_blocks)
      [--fit "AG7EW TEST 440.850 R"]                     the bench: the text the blocks open with;
                                                         a line a front end of how many samples
                                                         the chip gave an interval of 1, 1.5 and 2

The test blocks carry AG7EW as their address and "AG7EW TEST" in their text. They are for
440.850 MHz (FM) and, with the owner's word, 6 m (AM): nothing is ever sent on airband.
SPDX-License-Identifier: MIT
"""
import argparse

import numpy as np

FS = 48000
BAUD = 2400
F_CHANGE, F_SAME = 1200.0, 2400.0
SYN, SOH, STX, ETX, DEL = 0x16, 0x01, 0x02, 0x03, 0x7F
# the pager's sync word (the chip's own kind of packet opening), for the K units
K_SYNC = bytes([0x07, 0x6B, 0x3E, 0x36])


def parity(c):
    """a 7-bit character with its odd parity bit"""
    c &= 0x7F
    return c | (0 if bin(c).count("1") & 1 else 0x80)


def crc(data, c=0):
    """CRC-CCITT, reflected (0x8408), start 0: the block check, low byte first on the air"""
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0x8408 if c & 1 else 0)
    return c


def block(text, addr="AG7EW..", label="T0", mode="2", ack="\x15", bid="1"):
    """the block's bytes from the SOH to the DEL (parity set)"""
    body = bytes(parity(ord(ch)) for ch in mode + addr[:7].ljust(7, ".") + ack + label + bid)
    body += bytes([parity(STX)]) + bytes(parity(ord(ch)) for ch in text) + bytes([parity(ETX)])
    c = crc(body)
    return bytes([parity(SOH)]) + body + bytes([c & 0xFF, c >> 8, DEL])


def bits_lsb(data):
    return np.array([(b >> i) & 1 for b in data for i in range(8)], np.int8)


def tones(data, last=1):
    """data bits -> tone bits: 1 where the bit differs from the one before (1200 Hz)"""
    d = np.asarray(data, np.int8)
    return d ^ np.concatenate([[last], d[:-1]]).astype(np.int8)


def head(prekey=16):
    """the opening of a real block as bytes: the pre-key, + *, SYN SYN"""
    return bytes([0xFF] * prekey + [parity(ord("+")), parity(ord("*")), SYN, SYN])


# SYN SYN as tone bits, top bit first (the chip's sync word), and with the "+*" before it
SYNC2 = int("".join(map(str, tones(bits_lsb(head()))[-16:])), 2)
SYNC4 = int("".join(map(str, tones(bits_lsb(head()))[-32:])), 2)


def unit_tones(kind, text):
    """one unit's tone bits (1 = 1200 Hz). R: a real block. K: the same block after an opening
    of the chip's own kind, alternating tones then K_SYNC as tone bits, so the chip's modem has
    its usual preamble to start on (a control for the framing)"""
    blk = bits_lsb(block(text))
    if kind == "R":
        return tones(np.concatenate([bits_lsb(head()), blk]))
    pre = np.array([int(b) for byte in bytes([0x55]) * 24 + K_SYNC for b in f"{byte:08b}"], np.int8)
    return np.concatenate([pre, tones(blk, last=0)])


def audio(tone_bits, fs=FS):
    """tone bits -> phase-continuous audio, peak 1"""
    f = np.repeat(np.where(np.asarray(tone_bits) == 1, F_CHANGE, F_SAME), fs // BAUD)
    return np.sin(2 * np.pi * np.cumsum(f) / fs)


def probe_audio(seconds, fs=FS, gap=0.25, kinds="KR", where="440.850"):
    """-> (audio, [(kind, start s, text)]): K and R units in turn (or kinds), a gap between
    them, the frequency they are sent on in their text"""
    out, sent, pos, k = [np.zeros(int(0.2 * fs))], [], 0.2, 0
    while pos < seconds:
        kind = kinds[k % len(kinds)]
        text = f"AG7EW TEST {where} {kind}{k:03d} THE K6 READS ACARS"
        x = audio(unit_tones(kind, text), fs)
        out += [x, np.zeros(int(gap * fs))]
        sent.append((kind, pos, text))
        pos += len(x) / fs + gap
        k += 1
    return np.concatenate(out), sent


def read_block(tone_bits, last=0):
    """tone bits from just after a sync word (SYN SYN: the data bit before is 0; K_SYNC: 0 too)
    -> (text or None, why): the block if its SOH, parity and check are right"""
    t = np.asarray(tone_bits, np.int8)
    d = np.cumsum(t) % 2 ^ last                      # undo the differences
    n = len(d) // 8
    by = bytes(int("".join(map(str, d[8 * i:8 * i + 8][::-1])), 2) for i in range(n))
    if not by or by[0] != parity(SOH):
        return None, "no SOH"
    for i in range(1, n - 3):
        if by[i] & 0x7F == ETX:
            if crc(by[1:i + 3]) != 0:
                return None, "check"
            if any(parity(b) != b for b in by[1:i + 1]):
                return None, "parity"
            return bytes(b & 0x7F for b in by[1:i]).decode("latin-1"), "ok"
    return None, "no ETX"


def report(lines):
    """the module's say lines (as in the serial log) -> a line a capture: its variant, and the
    block read from its words (either sense of the tones, and a bit either side of the place the
    chip gave), then the module's v lines"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("acarsfsk start")] or [0]):]
    caps, out = {}, []
    for ln in said:
        f = ln.split()
        if ln[:1] == "d" and len(f) > 3 and f[0][1:].isdigit():
            caps.setdefault((f[0][1:], int(f[1])), []).extend(int(w, 16) for w in f[3:])
    for (v, k), words in sorted(caps.items()):
        bits = np.array([int(b) for w in words for b in f"{w:016b}"], np.int8)
        best = (None, "nothing")
        for inv in (0, 1):
            for off in (0, 1, 2, 3):
                got = read_block((bits ^ inv)[off:])
                if got[0] is not None or best[1] == "nothing" or (got[1] != "no SOH" and best[1] == "no SOH"):
                    best = (got[0], got[1] + (" inverted" if inv else "") + (f" +{off}" if off else ""))
                if got[0] is not None:
                    break
            if best[0] is not None:
                break
        out.append(f"v{v} #{k} w={len(words)} {best[1]}" + (f": {best[0][13:]}" if best[0] else f" [{words[0]:04x} {words[1]:04x} {words[2]:04x}]" if len(words) > 2 else ""))
    return out + [ln for ln in said if ln[:1] == "v" or ln.startswith("acarsfsk")]


def plain_samples(tone_bits, level=0):
    """tone bits -> the plain FSK receiver's bits at 4800 baud as an ideal slicer of the audio's
    sign would give them: the sign turns over at every half cycle, so a bit of 2400 Hz is two
    unlike samples and one of 1200 Hz two like ones"""
    out = []
    for t in np.asarray(tone_bits, int):
        level ^= 1
        out.append(level)
        if not t:
            level ^= 1
        out.append(level)
    return np.array(out, np.int8)


def plain_tones(samples):
    """the plain FSK receiver's bits at 4800 baud -> (tone bits, odd runs): a run of one sample
    is half a bit of 2400 Hz, a run of two a bit of 1200 Hz. A lone single (no partner) or a run
    of three and more is counted odd and read as best it can be"""
    b = np.asarray(samples, int)
    if not len(b):
        return np.zeros(0, np.int8), 0
    edges = np.flatnonzero(np.diff(b)) + 1
    runs = np.diff(np.concatenate([[0], edges, [len(b)]]))
    out, odd, i = [], 0, 0
    while i < len(runs):
        r = int(runs[i])
        if r == 1 and i + 1 < len(runs) and runs[i + 1] == 1:
            out.append(0)
            i += 2
            continue
        if r != 2:
            odd += 1
        out += [1] * max(r // 2, 0)
        i += 1
    return np.array(out, np.int8), odd


def turns(tone_bits):
    """tone bits -> when the chip's slicer turns over, in samples of 4800: it sees the audio's
    derivative (FM with the sender's pre-emphasis; AM at the zero IF, where the discriminator
    makes a phase of the envelope), whose sign changes at the audio's peaks: a quarter and
    three quarters into a bit of 2400 Hz, the middle of one of 1200 Hz. So the intervals are 1
    (inside 2400 Hz), 2 (inside 1200 Hz) and 1.5 (at every change of tone)"""
    out = []
    for k, b in enumerate(np.asarray(tone_bits, int)):
        out += [2 * k + 1.0] if b else [2 * k + 0.5, 2 * k + 1.5]
    return np.array(out)


def chip_samples(tone_bits, baud=4800, at=0.55, jitter=0.0, rng=None, when=False):
    """tone bits -> the plain FSK receiver's bits as the chip gives them (the seventh 6 m run,
    docs/k6-acars.md): its clock starts again at every turn of the slicer and takes a sample
    `at` of a period after it and every period from there, so an interval of x periods of its
    baud is floor(x - at) + 1 samples whatever came before. At 4800 baud an interval of 1.5 is
    one sample like 1 (a lone half cycle of 1200 Hz is lost among 2400 Hz); from about 5400
    baud it is two like 2, and the tones can be told. jitter: on the turns, in samples of 4800.
    when: also the second each sample is taken at"""
    T = turns(tone_bits)
    if jitter:
        T = T + (rng or np.random.default_rng(0)).normal(0, jitter, len(T))
    n = np.maximum(1, np.floor(np.diff(T) * baud / 4800.0 - at).astype(int) + 1)
    out = np.repeat(np.arange(len(n)) & 1, n).astype(np.int8)
    if not when:
        return out
    first = np.concatenate([[0], np.cumsum(n)[:-1]])
    return out, np.repeat(T[:-1] / 4800.0, n) + (at + np.arange(len(out)) - np.repeat(first, n)) / baud


# how likely the chip gives an interval of 1, 1.5 and 2 (the keys 2, 3, 4) as a run of 1, 2 or 3
# samples: at 4800 baud as the seventh 6 m run had it, and with its clock fast enough for 1.5
FOLLOW = {
    False: {2: (0.97, 0.03, 0.001), 3: (0.92, 0.08, 0.001), 4: (0.03, 0.95, 0.02)},
    True: {2: (0.97, 0.03, 0.001), 3: (0.10, 0.90, 0.001), 4: (0.01, 0.95, 0.04)},
}


def follow_tones(samples, fast=True):
    """the plain FSK receiver's bits -> tone bits, by the likeliest way the runs between its
    turns come from chip_samples' intervals: a bit of 2400 Hz is two intervals (1.5 after
    1200 Hz or 1, then 1), one of 1200 Hz is one (1.5 after 2400 Hz, or 2). fast: the chip's
    clock over about 5400 baud, where 1.5 is two samples and the answer has no second reading;
    at 4800 baud (not fast) it mostly has several, and this is one of them"""
    b = np.asarray(samples, int)
    if len(b) < 2:
        return np.zeros(0, np.int8)
    runs = np.minimum(np.diff(np.concatenate([[0], np.flatnonzero(np.diff(b)) + 1, [len(b)]])), 4)
    lp = {k: np.log(np.array((1e-4,) + v + (1e-4,))) for k, v in FOLLOW[bool(fast)].items()}
    n = len(runs)
    best = np.full((n + 2, 2), -1e18)       # [runs used, the tone before: 0 = 2400 Hz]
    back = np.zeros((n + 2, 2), np.int8)
    best[0, 0] = best[1, 0] = 0.0           # the capture may begin half way into a bit of 2400 Hz
    back[1, 0] = 2
    for j in range(n):
        r = runs[j]
        r2 = runs[j + 1] if j + 1 < n else 0
        for st in (0, 1):
            v = best[j, st]
            if v < -1e17:
                continue
            c = v + lp[4 if st else 3][r]                       # a bit of 1200 Hz
            if c > best[j + 1, 1]:
                best[j + 1, 1], back[j + 1, 1] = c, st
            if r2:
                c = v + lp[3 if st else 2][r] + lp[2][r2]       # a bit of 2400 Hz
                if c > best[j + 2, 0]:
                    best[j + 2, 0], back[j + 2, 0] = c, st
    st, j, out = int(np.argmax(best[n])), n, []
    while j > 0:
        prev = back[j, st]
        if prev == 2:
            break
        out.append(st)
        j, st = j - (1 if st else 2), int(prev)
    return np.array(out[::-1], np.int8)


# the ways the chip may round the intervals 2, 3, 4 (half samples of 4800) to samples, from a
# slow clock to a fast one
FITS = (
    {2: 1, 3: 1, 4: 2},
    {2: 1, 3: 2, 4: 2},
    {2: 1, 3: 2, 4: 3},
    {2: 2, 3: 2, 4: 3},
)


def baud_of(reg72):
    """REG_72 -> the baud (C195 is 4800, F1FB 6000)"""
    return reg72 / 10.32444


def plain_blocks(words, baud=4800):
    """a capture's words -> (the texts of the blocks in it, why the last try failed, odd runs):
    SYN SYN looked for in the tone bits, a block read after each. Over 5000 baud the tone bits
    are follow_tones'"""
    bits = [int(c) for w in words for c in f"{w:016b}"]
    if baud > 5000:
        t, odd = follow_tones(bits), 0
    else:
        t, odd = plain_tones(bits)
    s, want = "".join(map(str, t)), f"{SYNC2:016b}"
    pos, why, out = s.find(want), "no SYN SYN", []
    while pos >= 0:
        text, why = read_block(t[pos + 16:])
        if text is not None:
            out.append(text)
        pos = s.find(want, pos + 1)
    return out, "ok" if out else why, odd


def plain_read(words, baud=4800):
    """a capture's words -> (its first block's text or None, why, odd runs)"""
    out, why, odd = plain_blocks(words, baud)
    return (out[0] if out else None), why, odd


def fit_head(prefix):
    """the tone bits every block of the bench's signal opens with: the pre-key's last two
    characters, + *, SYN SYN, the block up to the end of prefix (the text before the number)"""
    t = unit_tones("R", prefix)
    return t[8 * (len(head()) - 6):8 * (len(head()) + 14 + len(prefix))]


def plain_fit(words, head_tones):
    """a capture's words laid against the turns of the blocks' known opening -> (blocks found,
    {(interval, samples): count}): the interval 2, 3 or 4 half samples of 4800 (1, 1.5, 2), the
    samples the chip gave it. The opening is looked for anywhere in the capture's runs, a run
    an interval, once for each way the chip may round (FITS: 1.5 as one sample or two, 2 as two
    or three, 1 as one or two) and the way with the fewest intervals off is kept; a place
    counts with at most one interval in five off, the best first and none within the
    opening's length of another"""
    b = np.array([int(c) for w in words for c in f"{w:016b}"], np.int8)
    iv = np.round(2 * np.diff(turns(head_tones))).astype(int)
    if len(b) < 2 or not len(iv):
        return 0, {}
    runs = np.minimum(np.diff(np.concatenate([[0], np.flatnonzero(np.diff(b)) + 1, [len(b)]])), 9)
    n, m = len(iv), len(runs)
    col, best = np.arange(m + 1), None
    for fine in FITS:
        D = np.zeros((n + 1, m + 1), np.int32)
        D[1:, 0] = np.arange(1, n + 1)
        for i in range(1, n + 1):
            row = np.minimum(np.concatenate([[D[i, 0]], D[i - 1, :-1] + (runs != fine[int(iv[i - 1])])]), D[i - 1] + 1)
            D[i] = np.minimum.accumulate(row - col) + col      # a run too many: one more each
        counts, end, used, off = {}, D[n], [], 0
        for k in sorted((int(k) for k in np.flatnonzero(end <= n // 5) if k), key=lambda k: (end[k], k)):
            if any(abs(k - u) < n for u in used):
                continue
            used.append(k)
            off += int(end[k])
            i, j = n, k
            while i > 0 and j > 0:
                if D[i, j] == D[i - 1, j - 1] + (int(runs[j - 1]) != fine[int(iv[i - 1])]):
                    key = (int(iv[i - 1]), int(runs[j - 1]))
                    counts[key] = counts.get(key, 0) + 1
                    i, j = i - 1, j - 1
                elif D[i, j] == D[i - 1, j] + 1:
                    i -= 1
                else:
                    j -= 1
        if best is None or (-len(used), off) < best[0]:
            best = ((-len(used), off), len(used), counts)
    return best[1], best[2]


def nrz_samples(tone_bits):
    """what the plain FSK receiver would give at 2400 baud, a sample a bit, with its clock
    settled between the audio's turns: the slope's sign at each bit's edge, which is the data
    bit itself (a bit of 1200 Hz turns it over once, one of 2400 Hz twice). The bench has yet
    to say that the chip's clock does settle there"""
    return (1 ^ np.cumsum(np.asarray(tone_bits, np.int8)) % 2).astype(np.int8)


def nrz_blocks(words):
    """a capture's words from the receiver at 2400 baud -> (the blocks' texts, why the last try
    failed, which way up the samples were read: 0 as they are). The samples are taken as the
    data bits (nrz_samples), either way up; a block is read from the capture's start (the
    chip's sync word was SYN SYN) and after every SYN SYN in it"""
    b = np.array([int(c) for w in words for c in f"{w:016b}"], np.int8)
    want = "".join(map(str, bits_lsb(bytes([SYN, SYN]))))
    best = ([], "no SOH", 0)
    for inv in (0, 1):
        d = b ^ inv
        s, out, why = "".join(map(str, d)), [], "no SOH"
        pos = 0
        while pos >= 0:
            text, w = read_block(tones(d[pos:], last=0))
            if text is not None:
                out.append(text)
            elif why == "no SOH":
                why = w
            pos = s.find(want, pos)
            pos = pos + 16 if pos >= 0 else -1
        if len(out) > len(best[0]) or (not best[0] and best[1] == "no SOH"):
            best = (out, "ok" if out else why, inv)
    return best


def nrz_head(prefix):
    """the data bits every block of the bench's signal has after SYN SYN: the SOH to the end
    of prefix"""
    return bits_lsb(block(prefix))[:8 * (14 + len(prefix))]


def nrz_fit(words, head_bits):
    """a capture's words at 2400 baud against the bits of the blocks' known opening -> the
    fewest bits off (wrong, missing or too many) of any place in the capture and either way
    up"""
    b = np.array([int(c) for w in words for c in f"{w:016b}"], np.int8)
    ref = np.asarray(head_bits, np.int8)
    n, m = len(ref), len(b)
    if not m:
        return n
    col, best = np.arange(m + 1), n
    for inv in (0, 1):
        d = b ^ inv
        D = np.arange(n + 1, dtype=np.int32)[:, None] * np.ones(m + 1, np.int32)
        D[0] = 0
        for i in range(1, n + 1):
            row = np.minimum(np.concatenate([[D[i, 0]], D[i - 1, :-1] + (d != ref[i - 1])]), D[i - 1] + 1)
            D[i] = np.minimum.accumulate(row - col) + col
        best = min(best, int(D[n].min()))
    return best


def plain_report(lines, fit=None):
    """dfskcap_acars' say lines (as in the serial log) -> its s, v and b lines, and a line a
    capture: the c line, and for one with words (those that went on like a pre-key) the share
    of runs of one, two and more samples and the block read from them (and how many more),
    by the baud of its front end's s line. fit: the text the bench's blocks open with; then a
    line a front end before its v line, `fit0 5200 baud: 31 blocks read, 33 openings; 1 -> 1:99%
    2:1%; 1.5 -> ...`: the samples the chip gave each interval (plain_fit). A front end under
    3000 baud is read as a sample a bit (nrz_blocks), and its line is `nrz0 2400 baud: 17 blocks
    read, 18 captures; of the opening's 272 bits off: 0 0 1 ...`, a number a capture, the
    fewest first (nrz_fit)"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("dfskcap start")] or [0]):]
    caps, out = {}, []
    for ln in said:
        f = ln.split()
        if ln[:1] == "d" and len(f) == 4 and f[0][1:].isdigit() and f[1].isdigit():
            w = caps.setdefault((int(f[0][1:]), int(f[1])), [])
            if int(f[2], 16) == len(w):
                w.extend(int(f[3][i:i + 4], 16) for i in range(0, len(f[3]) - 3, 4))
    baud, head_tones, tally = {}, fit_head(fit) if fit else None, {}
    for ln in said:
        f = ln.split()
        if ln[:2] == "s " and len(f) > 2 and f[1].isdigit():
            b72 = [x[2:] for x in f if x.startswith("b=")]
            baud[int(f[1])] = baud_of(int(b72[0], 16)) if b72 else 4800.0
        if ln[:1] == "c" and len(f) > 3 and f[0][1:].isdigit() and f[1].isdigit():
            fe = int(f[0][1:])
            words = caps.get((fe, int(f[1])))
            if words:
                b = np.array([int(c) for w in words for c in f"{w:016b}"], np.int8)
                runs = np.diff(np.concatenate([[0], np.flatnonzero(np.diff(b)) + 1, [len(b)]]))
                nrz = baud.get(fe, 4800.0) < 3000
                if nrz:
                    texts, why, odd = nrz_blocks(words)
                else:
                    texts, why, odd = plain_blocks(words, baud.get(fe, 4800.0))
                ln += (f" : runs 1:{np.mean(runs == 1):.0%} 2:{np.mean(runs == 2):.0%} 3+:{np.mean(runs >= 3):.0%}"
                       + (f" up {odd}; " if nrz else f" odd {odd}; ") + (f"block [{texts[0]}]" + (f" and {len(texts) - 1} more" if len(texts) > 1 else "")
                                           if texts else why))
                t = tally.setdefault(fe, [0, 0, {}])
                t[0] += len(texts)
                if nrz:
                    t[1] += 1
                    if fit:
                        t[2].setdefault("off", []).append(nrz_fit(words, nrz_head(fit)))
                elif head_tones is not None:
                    found, counts = plain_fit(words, head_tones)
                    t[1] += found
                    for key, c in counts.items():
                        t[2][key] = t[2].get(key, 0) + c
            out.append(ln)
        elif ln[:1] == "v" and f[0][1:].isdigit() and fit:
            fe = int(f[0][1:])
            n, found, counts = tally.get(fe, [0, 0, {}])
            if baud.get(fe, 4800.0) < 3000:
                off = sorted(counts.get("off", []))
                out += [f"nrz{fe} {baud[fe]:.0f} baud: {n} blocks read, {found} captures; of the opening's "
                        f"{len(nrz_head(fit))} bits off: " + (" ".join(map(str, off)) or "no capture"), ln]
                continue
            parts = []
            for iv, name in ((2, "1"), (3, "1.5"), (4, "2")):
                tot = sum(c for (i, _), c in counts.items() if i == iv)
                parts.append(f"{name} -> " + " ".join(f"{r}:{counts[(iv, r)] / tot:.0%}" for r in range(1, 10) if (iv, r) in counts)
                             if tot else f"{name} -> none")
            out += [f"fit{fe} {baud.get(fe, 4800.0):.0f} baud: {n} blocks read, {found} openings; " + "; ".join(parts), ln]
        elif ln[:1] in ("s", "v", "b", "q") or ln.startswith("dfskcap "):
            out.append(ln)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("probe")
    q.add_argument("out")
    q.add_argument("--seconds", type=float, default=200.0)
    q.add_argument("--only", default="KR", help="the kinds of unit in turn (K, R; default both)")
    q.add_argument("--where", default="440.850", help="the frequency named in the text")
    q = sub.add_parser("report")
    q.add_argument("log")
    q = sub.add_parser("plain")
    q.add_argument("log")
    q.add_argument("--fit", help="the text the bench's blocks open with: a fit line a front end")
    a = ap.parse_args(argv)
    if a.cmd in ("report", "plain"):
        with open(a.log, errors="replace") as f:
            lines = f.read().splitlines()
        print("\n".join(report(lines) if a.cmd == "report" else plain_report(lines, a.fit)))
        return 0
    import soundfile as sf
    if not a.only or set(a.only) - set("KR"):
        ap.error("--only takes K and R")
    x, sent = probe_audio(a.seconds, kinds=a.only, where=a.where)
    sf.write(a.out, 0.5 * x, FS)
    print(f"{len(x) / FS:.2f} s, {len(sent)} units, sync words {SYNC2:04x} {SYNC4:08x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
