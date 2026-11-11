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
      [--only KRLT --gap 0.4]                            for the tone receiver's recorder
                                                         (dfskcap_acarsd): L is a K unit at
                                                         1200 baud, the control; T a ladder of
                                                         steady and alternating tones
      [--levels 1,1.8]                                   the rounds' peaks in turn (the file's
                                                         peak is half of it): two depths of AM
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
  python3 -m modem.acars tone LOG                        dfskcap_acarsd's log: the chip's 1200/2400
      [--fit "AG7EW TEST 440.850 "] [--show 2]           Hz tone receiver with the pre-key as its
                                                         sync word; the blocks found in its tone
                                                         bits, how many bits of the known opening
                                                         are off, and (--show) the first captures'
                                                         bits laid against it

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
    its usual preamble to start on (a control for the framing). L: K's bits, sent at 1200 baud
    (UNIT_BAUD: a cycle of 1200 Hz or two of 2400 Hz a bit, which the chip's tone receiver
    reads for aprsfsk), a control for the recorder. T: no block but a ladder (LADDER): a steady
    2400 Hz, a steady 1200 Hz, then the two in turn, 8, 4, 2 and 1 bits of each"""
    if kind == "T":
        return LADDER.copy()
    blk = bits_lsb(block(text))
    if kind == "R":
        return tones(np.concatenate([bits_lsb(head()), blk]))
    pre = np.array([int(b) for byte in bytes([0x55]) * 24 + K_SYNC for b in f"{byte:08b}"], np.int8)
    return np.concatenate([pre, tones(blk, last=0)])


UNIT_BAUD = {"K": BAUD, "R": BAUD, "L": BAUD // 2, "T": BAUD}
# the T unit's tone bits: 80 ms of each tone alone (the recorder takes 107 ms of one tone for
# an empty FIFO), then 8, 4, 2 and 1 bits of each in turn, and 2400 Hz to end on
LADDER = np.array([0] * 192 + [1] * 192 + ([0] * 8 + [1] * 8) * 6 + ([0] * 4 + [1] * 4) * 8
                  + ([0] * 2 + [1] * 2) * 12 + [0, 1] * 24 + [0] * 16, np.int8)
LADDER_REF = LADDER[320:]       # what a capture is laid against: the last of the steady 1200 Hz on


def audio(tone_bits, fs=FS, baud=BAUD):
    """tone bits -> phase-continuous audio, peak 1"""
    f = np.repeat(np.where(np.asarray(tone_bits) == 1, F_CHANGE, F_SAME), fs // baud)
    return np.sin(2 * np.pi * np.cumsum(f) / fs)


def probe_audio(seconds, fs=FS, gap=0.25, kinds="KR", where="440.850", levels=(1.0,)):
    """-> (audio, [(kind, start s, text)]): K and R units in turn (or kinds), a gap between
    them, the frequency they are sent on in their text. levels: the peak of each round of the
    kinds in turn (a unit's number says which: number // len(kinds) % len(levels)), to try an
    AM sender's depth of modulation"""
    out, sent, pos, k = [np.zeros(int(0.2 * fs))], [], 0.2, 0
    while pos < seconds:
        kind = kinds[k % len(kinds)]
        text = f"AG7EW TEST {where} {kind}{k:03d} THE K6 READS ACARS"
        x = levels[k // len(kinds) % len(levels)] * audio(unit_tones(kind, text), fs, UNIT_BAUD[kind])
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


def tone_ref(prefix):
    """the tone bits every unit's block opens with, whatever its kind, from just after its sync
    word (SYN SYN or K_SYNC): the SOH to the end of prefix"""
    return tones(nrz_head(prefix), last=0)


def tone_blocks(bits):
    """the tone receiver's bits -> (the texts of the blocks in them, which way up: 1 = the
    chip's 1 is the 2400 Hz tone). A block is read from the start (the chip's sync word may
    have been SYN SYN or K_SYNC) and after every SYN SYN or K_SYNC in the bits"""
    b = np.asarray(bits, np.int8)
    marks = (f"{SYNC2:016b}", "".join(f"{x:08b}" for x in K_SYNC))
    best = ([], 0)
    for inv in (0, 1):
        t = b ^ inv
        s, at = "".join(map(str, t)), {0}
        for w in marks:
            pos = s.find(w)
            while pos >= 0:
                at.add(pos + len(w))
                pos = s.find(w, pos + 1)
        out = [text for text in (read_block(t[k:])[0] for k in sorted(at)) if text is not None]
        if len(out) > len(best[0]):
            best = (out, inv)
    return best


def tone_places(bits, ref, most=0.2):
    """the tone receiver's bits against the known opening's -> (which way up, [(end, bits
    off)], rows): every place where the opening fits with at most `most` of its bits off
    (wrong, missing or too many), the best first, none within its length of another; or the
    one best place if none is that good. The way up is the one with the more places, then
    the fewer bits off. rows: the edit table of that way, for tone_show"""
    b, ref = np.asarray(bits, np.int8), np.asarray(ref, np.int8)
    n, m = len(ref), len(b)
    if not m:
        return 0, [], None
    col, best = np.arange(m + 1), None
    for inv in (0, 1):
        d = b ^ inv
        D = np.zeros((n + 1, m + 1), np.int32)
        D[1:, 0] = np.arange(1, n + 1)
        for i in range(1, n + 1):
            row = np.minimum(np.concatenate([[D[i, 0]], D[i - 1, :-1] + (d != ref[i - 1])]), D[i - 1] + 1)
            D[i] = np.minimum.accumulate(row - col) + col
        end, used = D[n], []
        for k in sorted((int(k) for k in np.flatnonzero(end <= most * n) if k), key=lambda k: (end[k], k)):
            if all(abs(k - u) >= n for u, _ in used):
                used.append((k, int(end[k])))
        good = len(used)
        if not used:
            k = int(np.argmin(end[1:])) + 1
            used = [(k, int(end[k]))]
        key = (-good, sum(o for _, o in used))
        if best is None or key < best[0]:
            best = (key, inv, used, D)
    return best[1], best[2], best[3]


def tone_show(bits, ref, inv, end, D, width=96):
    """-> lines: the opening's bits over the receiver's at the place ending at `end` (a dot
    where one has no partner), `width` a line"""
    d, ref = np.asarray(bits, np.int8) ^ inv, np.asarray(ref, np.int8)
    i, j, a, b = len(ref), end, [], []
    while i > 0:
        if j > 0 and D[i, j] == D[i - 1, j - 1] + (int(d[j - 1]) != int(ref[i - 1])):
            a.append(str(ref[i - 1])), b.append(str(d[j - 1]))
            i, j = i - 1, j - 1
        elif D[i, j] == D[i - 1, j] + 1:
            a.append(str(ref[i - 1])), b.append(".")
            i -= 1
        else:
            a.append("."), b.append(str(d[j - 1]))
            j -= 1
    a, b = "".join(a[::-1]), "".join(b[::-1])
    out = []
    for k in range(0, len(a), width):
        out += ["  sent " + a[k:k + width], "  got  " + b[k:k + width]]
    return out


def tone_report(lines, fit="AG7EW TEST 440.850 ", show=0):
    """dfskcap_acarsd's say lines (as in the serial log) -> its s, v, b and q lines, and a line a
    capture: the c line, the share of ones and of runs of one, two and more bits, the blocks
    read from its tone bits (tone_blocks), and how many bits of the units' known opening
    (tone_ref of fit) are off at each place it is found (tone_places). A setting at 4800 baud
    has two samples a tone bit: each half is read by itself and the better kept. Before a
    setting's v line, `tone3 33a9 2400 baud sy=0000: 9 blocks read, 5 captures; openings 11;
    of 264 bits off: 0 0 1 ...`. show: for that many captures of a setting, those with the
    fewest bits off, their best place's bits under the opening's (before the tone line). And
    `ladder3: 2 found; of 336 bits off: 0 4`: the T units' ladder (LADDER_REF) wherever a
    capture has it with at most one bit in five off, shown likewise"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("dfskcap start")] or [0]):]
    caps, out = {}, []
    for ln in said:
        f = ln.split()
        if ln[:1] == "d" and len(f) == 4 and f[0][1:].isdigit() and f[1].isdigit():
            w = caps.setdefault((int(f[0][1:]), int(f[1])), [])
            if int(f[2], 16) == len(w):
                w.extend(int(f[3][i:i + 4], 16) for i in range(0, len(f[3]) - 3, 4))
    ref, sets, tally, ladder = tone_ref(fit), {}, {}, {}
    for ln in said:
        f = ln.split()
        if ln[:2] == "s " and len(f) > 2 and f[1].isdigit():
            kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
            sets[int(f[1])] = (kv.get("cfg", "?"), baud_of(int(kv.get("b", "c195"), 16)), kv.get("sy", "?"))
        if ln[:1] == "c" and len(f) > 3 and f[0][1:].isdigit() and f[1].isdigit():
            fe = int(f[0][1:])
            words = caps.get((fe, int(f[1])))
            if words:
                b = np.array([int(c) for w in words for c in f"{w:016b}"], np.int8)
                runs = np.diff(np.concatenate([[0], np.flatnonzero(np.diff(b)) + 1, [len(b)]]))
                step = max(1, int(round(sets.get(fe, ("", 2400.0))[1] / 2400)))
                got = None
                for ph in range(step):
                    x = b[ph::step]
                    texts, up = tone_blocks(x)
                    inv, places, D = tone_places(x, ref)
                    good = [o for _, o in places if o <= 0.2 * len(ref)]
                    key = (-len(texts), -len(good), sum(o for _, o in places))
                    if got is None or key < got[0]:
                        got = (key, texts, up, inv, places, D, x, good)
                _, texts, up, inv, places, D, x, good = got
                rung = min((tone_places(b[ph::step], LADDER_REF) + (b[ph::step],) for ph in range(step)),
                           key=lambda r: r[1][0][1])
                if rung[1][0][1] <= 0.2 * len(LADDER_REF):
                    ladder.setdefault(fe, []).append((rung[1][0][1], f[1], rung))
                ln += (f" : ones {np.mean(b):.0%} runs 1:{np.mean(runs == 1):.0%} 2:{np.mean(runs == 2):.0%} 3+:{np.mean(runs >= 3):.0%}; "
                       + (f"{len(texts)} read" + (" upside down" if up else "") + f" [{texts[0]}]" if texts else "none read")
                       + "; off " + " ".join(str(o) for _, o in places))
                t = tally.setdefault(fe, [0, 0, [], 0, []])
                t[0] += len(texts)
                t[1] += 1
                t[2] += good or [o for _, o in places]
                t[3] += len(good)
                if show and places:
                    t[4] = sorted(t[4] + [(places[0][1], t[1], f"  c{fe} {f[1]}'s best place, {places[0][1]} off"
                                           + (", upside down:" if inv else ":"),
                                           tone_show(x, ref, inv, places[0][0], D))])[:show]
            out.append(ln)
        elif ln[:1] == "v" and f[0][1:].isdigit():
            fe = int(f[0][1:])
            n, ncap, off, good, shown = tally.get(fe, [0, 0, [], 0, []])
            cfg, baud, sy = sets.get(fe, ("?", 0.0, "?"))
            for _, _, title, rows in shown:
                out += [title] + rows
            rungs = sorted(ladder.get(fe, []), key=lambda r: r[0])
            for loff, cap, (linv, lplaces, lD, lx) in rungs[:show]:
                out += [f"  c{fe} {cap}'s ladder, {loff} off" + (", upside down:" if linv else ":")]
                out += tone_show(lx, LADDER_REF, linv, lplaces[0][0], lD)
            out += [f"ladder{fe}: {len(rungs)} found; of {len(LADDER_REF)} bits off: "
                    + (" ".join(str(r[0]) for r in rungs) or "none")]
            out += [f"tone{fe} {cfg} {baud:.0f} baud sy={sy}: {n} blocks read, {ncap} captures; openings {good}; "
                    f"of {len(ref)} bits off: " + (" ".join(map(str, sorted(off))) or "no capture"), ln]
        elif ln[:1] in ("s", "v", "b", "q") or ln.startswith("dfskcap "):
            out.append(ln)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("probe")
    q.add_argument("out")
    q.add_argument("--seconds", type=float, default=200.0)
    q.add_argument("--only", default="KR", help="the kinds of unit in turn (K, R, L; default K and R)")
    q.add_argument("--where", default="440.850", help="the frequency named in the text")
    q.add_argument("--gap", type=float, default=0.25, help="seconds of silence between units")
    q.add_argument("--levels", default="1", help="the rounds' peaks in turn, as 1,1.8 (the file's peak is half of it)")
    q = sub.add_parser("report")
    q.add_argument("log")
    q = sub.add_parser("plain")
    q.add_argument("log")
    q.add_argument("--fit", help="the text the bench's blocks open with: a fit line a front end")
    q = sub.add_parser("tone")
    q.add_argument("log")
    q.add_argument("--fit", default="AG7EW TEST 440.850 ", help="the text every unit's block opens with")
    q.add_argument("--show", type=int, default=0, help="captures a setting whose bits are laid against the opening's")
    a = ap.parse_args(argv)
    if a.cmd in ("report", "plain", "tone"):
        with open(a.log, errors="replace") as f:
            lines = f.read().splitlines()
        print("\n".join(report(lines) if a.cmd == "report" else plain_report(lines, a.fit) if a.cmd == "plain"
                        else tone_report(lines, a.fit, a.show)))
        return 0
    import soundfile as sf
    if not a.only or set(a.only) - set(UNIT_BAUD):
        ap.error("--only takes K, R and L")
    levels = tuple(float(v) for v in a.levels.split(","))
    if not levels or min(levels) <= 0 or max(levels) > 2:
        ap.error("--levels takes peaks over 0 and up to 2")
    x, sent = probe_audio(a.seconds, kinds=a.only, where=a.where, gap=a.gap, levels=levels)
    sf.write(a.out, 0.5 * x, FS)
    print(f"{len(x) / FS:.2f} s, {len(sent)} units, sync words {SYNC2:04x} {SYNC4:08x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
