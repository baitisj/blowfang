"""APRS test traffic: AX.25 UI frames as Bell 202 AFSK 1200 audio (for the K6's APRS clock module,
c/k6/mod/aprsclk.c, docs/k6-aprs-clock.md), and a plain float decoder for checking recordings.

  python -m modem.aprs wav OUT.wav                   a few timestamped packets, as audio
  python -m modem.aprs decode REC.wav                frames and timestamps in a recording

Receive-only work: nothing here keys a radio. SPDX-License-Identifier: MIT
"""
import argparse
import sys

import numpy as np

FS = 48000
BAUD = 1200
MARK, SPACE = 1200.0, 2200.0


def crc16(data):
    """CRC-16/X.25 (the AX.25 FCS), as sent: little-endian after the frame"""
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0x8408 if c & 1 else 0)
    return c ^ 0xFFFF


def addr(call, last=False, h=False):
    """'VE7ABC-9' -> 7 address bytes; h: the has-been-repeated bit (a digipeater's)"""
    c, _, ssid = call.partition("-")
    b = bytes((ord(ch) << 1) for ch in c.ljust(6)[:6])
    return b + bytes([0x60 | (int(ssid or 0) << 1) | (0x80 if h else 0) | (1 if last else 0)])


def frame(src, dst, info, path=(), repeated=0):
    """a UI frame with its FCS. path: digipeater calls; repeated: how many of them have
    repeated it (their H bits set)"""
    hops = [dst, src, *path]
    a = b"".join(addr(c, last=i == len(hops) - 1, h=(i >= 2 and i - 2 < repeated))
                 for i, c in enumerate(hops))
    body = a + b"\x03\xf0" + (info.encode() if isinstance(info, str) else info)
    f = crc16(body)
    return body + bytes([f & 0xFF, f >> 8])


def hdlc_bits(frames, pre_flags=40, gap_flags=2, tail_flags=3):
    """frames -> the line bits (1 mark) after bit stuffing and NRZI"""
    raw = []
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    raw += flag * pre_flags
    for k, f in enumerate(frames):
        ones = 0
        for byte in f:
            for i in range(8):
                b = (byte >> i) & 1
                raw.append(b)
                ones = ones + 1 if b else 0
                if ones == 5:
                    raw.append(0)
                    ones = 0
        raw += flag * (gap_flags if k < len(frames) - 1 else tail_flags)
    out, tone = [], 1
    for b in raw:
        if b == 0:
            tone ^= 1
        out.append(tone)
    return np.array(out, np.int8)


def afsk(bits, fs=FS, twist_db=0.0, baud=BAUD):
    """line bits -> phase-continuous Bell 202 audio, peak 1 (mark); twist_db: the space tone's
    level over the mark's (a sender's pre-emphasis makes it positive)"""
    n = len(bits)
    t = np.arange(int(n * fs / baud))
    sym = np.minimum((t * baud // fs).astype(int), n - 1)
    f = np.where(bits[sym] == 1, MARK, SPACE)
    ph = 2 * np.pi * np.cumsum(f) / fs
    a = np.where(bits[sym] == 1, 1.0, 10 ** (twist_db / 20))
    return a * np.sin(ph)


def packet_audio(frames, fs=FS, twist_db=0.0, pre_flags=40, baud=BAUD, invert=False):
    """(audio, the first frame's first bit in seconds from the audio's start). As built the
    opening flags' long tone is the space; invert swaps the tones (NRZI doesn't mind; a receiver
    that syncs on the flags' pattern might)"""
    bits = hdlc_bits(frames, pre_flags)
    return afsk(bits ^ 1 if invert else bits, fs, twist_db, baud), pre_flags * 8 / baud


def ts_dhm(utc_s):
    """DDHHMMz for seconds since the month's day 0 00:00 (day 1 = 86400 .. )"""
    d, r = divmod(int(utc_s), 86400)
    return f"{d:02d}{r // 3600:02d}{r // 60 % 60:02d}z"


def ts_hms(utc_s):
    r = int(utc_s) % 86400
    return f"{r // 3600:02d}{r // 60 % 60:02d}{r % 60:02d}h"


def rmc(utc_s, date="081126"):
    r = utc_s % 86400
    hh, mm, ss = int(r // 3600), int(r // 60 % 60), int(r % 60 * 100 + 1e-6) / 100  # truncated, as a stamp
    return f"$GPRMC,{hh:02d}{mm:02d}{ss:05.2f},A,4857.00,N,12229.00,W,000.0,000.0,{date},,*00"


def bench_wav(t_utc, call="AG7EW", gap=0.6, fs=FS, invert=False):
    """the on-air loopback (c/k6/mod/aprsclk_440.c): three stations of one call (-1 HHMMSSh, -2
    $GPRMC, -3 DDHHMMz) each stamped with the true UTC at its own first bit if the audio starts at
    t_utc (Unix seconds), truncated, so every stamp is early by the keying delay only.
    -> (audio, [(call, info, the frame's end in s from the audio's start)])"""
    import time as _t
    tm = _t.gmtime(t_utc)
    month0 = t_utc - (tm.tm_mday * 86400 + tm.tm_hour * 3600 + tm.tm_min * 60 + tm.tm_sec)
    pos, out, sent = 0.0, [], []
    for k, kind in enumerate("hrz"):
        pre = 40
        u = t_utc + pos + pre * 8 / BAUD                 # this frame's first bit
        if kind == "h":
            info = "/" + ts_hms(u) + "4857.00N/12229.00W>K6 clock test"
        elif kind == "r":
            g = _t.gmtime(u)
            info = rmc(u % 86400, f"{g.tm_mday:02d}{g.tm_mon:02d}{g.tm_year % 100:02d}")
        else:
            info = "@" + ts_dhm(u - month0) + "4857.00N/12229.00W_K6 clock test"
        c = f"{call}-{k + 1}"
        x, _ = packet_audio([frame(c, "APRS", info)], fs=fs, pre_flags=pre, invert=invert)
        out += [x, np.zeros(int(gap * fs))]
        sent.append((c, info, pos + len(x) / fs))
        pos += (len(x) + int(gap * fs)) / fs
    return np.concatenate(out), sent


# what a packet off the air has that the bench signal lacks, one at a time: a station's number
# (its SSID) -> (name, emphasis: +1 a sender's pre-emphasis, -1 a receiver's de-emphasis of a
# flat sender, 6 dB an octave, 5 dB between the tones; level; noise: the signal over it in dB;
# opening flags)
KINDS = {
    1: ("flat", 0, 1.0, None, 40),         # the bench signal as it was
    2: ("pre", 1, 1.0, None, 40),
    3: ("de", -1, 1.0, None, 40),
    4: ("low", 0, 0.3, None, 40),          # a third of the deviation
    5: ("n12", 0, 0.6, 12.0, 40),          # a weak signal's noise (rising with frequency)
    6: ("n6", 0, 0.6, 6.0, 40),
    7: ("short", 0, 1.0, None, 8),         # eight opening flags
    8: ("pre n9", 1, 0.6, 9.0, 40),        # pre-emphasis and noise
    9: ("inv", 0, 1.0, None, 40),          # the tones swapped: the opening flags' long tone the mark
    10: ("pre inv", 1, 1.0, None, 40),
}
KINDS_USUAL = tuple(range(1, 9))           # a round of --kinds (9.641 s), as the bench runs had it
KINDS_SWAP = (9, 10)
EMPH = 0.95                                # the 1-pole filter's pole at 48 kHz (decode_any's)


def fm_noise(n, fs=FS, seed=0):
    """noise as an FM receiver gives it on a weak signal: its voltage rising with frequency,
    300 to 3000 Hz, unit power"""
    rng = np.random.default_rng(seed)
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / fs)
    X *= np.where((f >= 300) & (f <= 3000), f, 0.0)
    x = np.fft.irfft(X, n)
    return x / np.sqrt(np.mean(x ** 2))


def kinds_wav(t_utc, call="AG7EW", gap=0.6, fs=FS, rounds=1, kinds=None):
    """a rehearsal of 144.390 on the bench: packets of the KINDS in turn, each from <call>-<its
    number> and stamped HHMMSSh with the true UTC at its first bit if the audio starts at t_utc
    -> (audio, [(call, info, the frame's end in s from the audio's start)])"""
    from scipy.signal import lfilter
    pos, out, sent = 0.0, [], []
    for r in range(rounds):
        for k in sorted(kinds or KINDS_USUAL):
            name, emph, level, snr, pre = KINDS[k]
            u = t_utc + pos + pre * 8 / BAUD
            info = "/" + ts_hms(u) + "4857.00N/12229.00W>K6 " + name
            c = f"{call}-{k}"
            x, _ = packet_audio([frame(c, "APRS", info)], fs=fs, pre_flags=pre, invert=k in KINDS_SWAP)
            if emph > 0:
                x = lfilter([1, -EMPH], [1 - EMPH], x)
            elif emph < 0:
                x = lfilter([1 - EMPH], [1, -EMPH], x)
            x = level * x / np.abs(x).max()
            if snr is not None:
                x = x + np.sqrt(np.mean(x ** 2)) * 10 ** (-snr / 20) * fm_noise(len(x), fs, seed=8 * r + k)
                x = x / max(1.0, np.abs(x).max())
            out += [x, np.zeros(int(gap * fs))]
            sent.append((c, info, pos + len(x) / fs))
            pos += (len(x) + int(gap * fs)) / fs
    return np.concatenate(out), sent


# ---- the FD6818 FSK modem probe's test signal (c/k6/mod/fskaprs.c) ----
def fskprobe_line(invert=False, seconds=0.95, call="AG7EW", pre_flags=8):
    """the APRS part of a probe unit: whole frames with 8 flags between them, at least `seconds`
    of line bits (1 = mark; invert swaps the tones, which NRZI doesn't mind: as built the opening
    flags' long tone is the space, inverted the mark)"""
    fr = [frame(f"{call}-1", "APRS", ">K6 fsk probe"),
          frame(f"{call}-2", "APRS", f">{call} TEST 440.850")]
    k = 1
    while True:
        bits = hdlc_bits([fr[i % 2] for i in range(k)], pre_flags=pre_flags, gap_flags=8, tail_flags=8)
        if len(bits) >= seconds * BAUD:
            return bits ^ 1 if invert else bits
        k += 1


# The probe's units (c/k6/mod/fskaprs.c). P0 to P3, C and F24 open with a preamble and sync word
# of the chip's own kind (FFSK 1200/1800 Hz, 1 = 1200 Hz) and a word naming the unit: its code
# here, then its place in the run (mod 256). Then:
#   P0..P3  Bell 202 AX.25, 0 to 3 quarters of a bit late on the preamble's bit clock
#   C       the same line bits at 1200/1800 Hz: the chip's own tones, so these frames must decode
#   F24     preamble, sync and name at 2400 baud, then Bell 202 at 1200
#   A+, A-  a plain APRS packet: 40 flags, their long tone the mark (A+) or the space (A-)
FSKPROBE_UNITS = (
    ("P0", 0xA0),
    ("P1", 0xA1),
    ("P2", 0xA2),
    ("P3", 0xA3),
    ("C", 0xAC),
    ("F24", 0xAF),
    ("A+", None),
    ("A-", None),
)
# the units sent, round and round (probe 4: probe 3 found the quarter bits and F24 tell nothing)
FSKPROBE_ROUND = (
    "P0",
    "P2",
    "C",
    "A+",
    "A-",
)
FSKPROBE_SYNC = bytes([0x07, 0x6B, 0x3E, 0x36])     # the pager's
FSKPROBE_APRS_S = 0.87                              # of APRS after the name: the module's 128 bytes


def fskprobe_parts(name, seq=0):
    """one unit as [(bits or a count of quarter bits, baud, tone of a 1, tone of a 0)], end to end
    and phase-continuous; a count holds the 1200 Hz tone that long"""
    code = dict(FSKPROBE_UNITS)[name]
    if code is None:
        return [(fskprobe_line(name == "A+", FSKPROBE_APRS_S, pre_flags=40), BAUD, MARK, SPACE)]
    from . import fsk
    head = fsk.bits_of(bytes([0x55]) * 20 + FSKPROBE_SYNC + bytes([code, seq & 0xFF]))
    line = fskprobe_line(True, FSKPROBE_APRS_S)
    parts = [(head, 2400 if name == "F24" else BAUD, 1200.0, 1800.0)]
    if name[0] == "P" and name != "P0":
        parts.append((int(name[1]), BAUD, 1200.0, 1200.0))
    parts.append((line, BAUD, MARK, 1800.0 if name == "C" else SPACE))
    return parts


def fskprobe_unit(name, seq=0, fs=FS):
    """one unit's audio, peak 1"""
    f = []
    for bits, baud, f1, f0 in fskprobe_parts(name, seq):
        if isinstance(bits, int):
            f.append(np.full(bits * fs // (4 * baud), f1))
        else:
            f.append(np.repeat(np.where(np.asarray(bits) == 1, f1, f0), fs // baud))
    return np.sin(2 * np.pi * np.cumsum(np.concatenate(f)) / fs)


def fskprobe_audio(seconds, fs=FS, gap=0.3):
    """-> (audio, [(unit, its start in s, its place in the run)]): the units round and round
    with `gap` of bare carrier between them (the module says a packet's words in it)"""
    out, sent, pos = [np.zeros(int(0.2 * fs))], [], 0.2
    k = 0
    while pos < seconds:
        name = FSKPROBE_ROUND[k % len(FSKPROBE_ROUND)]
        x = fskprobe_unit(name, k, fs)
        out += [x, np.zeros(int(gap * fs))]
        sent.append((name, pos, k))
        pos += len(x) / fs + gap
        k += 1
    return np.concatenate(out), sent


# ---- the probe's report: what the chip made of each unit (the module's c and d lines) ----
def hdlc_frames(tones):
    """line bits (tones, one a bit) -> the frames with a good FCS"""
    out, sr, inf, buf, n, last = [], 0, False, 0, 0, int(tones[0]) if len(tones) else 0
    for x in tones:
        bit = int(x == last)
        last = x
        sr = ((sr << 1) | bit) & 0xFF
        if sr == 0x7E:
            if inf and n >= 7 + 8 * 17 and (n - 7) % 8 == 0:
                f = (buf & ((1 << (n - 7)) - 1)).to_bytes((n - 7) // 8, "little")
                if crc16(f[:-2]) == f[-2] | f[-1] << 8:
                    out.append(f)
            inf, buf, n = True, 0, 0
        elif sr & 0x7F == 0x7F:
            inf = False
        elif inf and sr & 0x3F != 0x3E:
            buf |= bit << n
            n += 1
    return out


def _shorten(x, e):
    """x with every run of ones e samples shorter at its end (e < 0: longer)"""
    y = x.copy()
    for k in range(1, abs(e) + 1):
        z = np.append(x[k:], np.zeros(k, x.dtype))
        y = y & z if e > 0 else y | z
    return y


def fskprobe_fit(cap, line, per=1, lo=None, hi=None):
    """how the chip read `line` (sent bits, 1 = mark): cap is what it gave, `per` samples a bit.
    -> (agreement 0..1, e, at): the best fit of 'each run of marks e samples short' with
    cap[0] at sample `at` of the line (searched from lo to hi; by default every place that
    leaves 250 bits of both)"""
    ref = np.repeat(np.asarray(line, np.int8), per)
    c = 2.0 * np.asarray(cap, float) - 1
    need = min(250 * per, len(c))
    if lo is None:
        lo, hi = 0, len(ref) - need
    best = (0.0, 0, 0)
    for e in range(-per, 2 * per + 1):
        m = 2.0 * _shorten(ref, e) - 1
        for at in range(lo, hi + 1):
            a, b = max(at, 0), max(-at, 0)          # at < 0: the capture began before the line
            n = min(len(c) - b, len(m) - a)
            if n >= need:
                got = (float(np.dot(m[a:a + n], c[b:b + n]) / n), e, at)
                best = max(best, got, key=lambda g: (round(g[0], 3), -abs(g[1])))
    return (best[0] + 1) / 2, best[1], best[2]


def fskprobe_local(cap, line, per=1, at=0, win=48, reach=4):
    """cap against the line with no rule at all, but let slip: windows of `win` bits, each at its
    own best place within `reach` bits of where the last one sat (the first: of `at`).
    -> (agreement 0..1 over the windows, how many times the place moved)"""
    ref = np.repeat(np.asarray(line, np.int8), per)
    good = total = moves = 0
    for a in range(0, len(cap) - win * per + 1, win * per):
        c, best = cap[a:a + win * per], (-1, 0)
        for d in sorted(range(-reach * per, reach * per + 1), key=abs):
            r = ref[max(a + at + d, 0):a + at + d + len(c)]
            if a + at + d >= 0 and len(r) == len(c):
                best = max(best, (int((r == c).sum()), d), key=lambda g: g[0])
        if best[0] < 0:
            break
        good, total, moves, at = good + best[0], total + len(c), moves + (best[1] != 0 and a > 0), at + best[1]
    return (good / total if total else 0.0), moves


def fskprobe_report(lines):
    """the module's say lines (as in the serial log) -> the report's lines: for each packet the
    chip took, the unit it was; the best fit of 'every mark run e bits short' and how much of the
    packet that rule explains; how much agrees with the line sent when the place may slip, and
    how often it slipped; and the frames with a good FCS (the best sample of each bit)"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("fskaprs start")] or [0]):]
    caps, head, out = {}, {}, []
    for ln in said:
        f = ln.split()
        if ln[:1] == "c" and len(f) > 2 and f[0][1:].isdigit():
            head[int(f[0][1:]), int(f[1])] = dict(kv.split("=") for kv in f[2:])
        elif ln[:1] == "d" and len(f) > 3 and f[0][1:].isdigit():
            caps.setdefault((int(f[0][1:]), int(f[1])), []).extend(int(w, 16) for w in f[3:])
    names = {code: name for name, code in FSKPROBE_UNITS if code is not None}
    lines_of = {"P": fskprobe_line(True, FSKPROBE_APRS_S),
                "A+": fskprobe_line(True, FSKPROBE_APRS_S, pre_flags=40),
                "A-": fskprobe_line(False, FSKPROBE_APRS_S, pre_flags=40)}
    for (n, k), words in sorted(caps.items()):
        h = head.get((n, k), {})
        per = int(h.get("p", 1))
        bits = np.array([int(b) for w in words for b in f"{w:016b}"], np.int8)
        # the unit's name: the word the chip had in before its sync interrupt (probe 2: that
        # comes 16 bits after the sync word), so before the module wrote REG_58 or REG_72
        name = int("".join(map(str, bits[:16])), 2)
        if name >> 8 in names:
            unit, body, where = "P", bits[16:], f"{names[name >> 8]}#{name & 0xFF}"
            agree, e, at = fskprobe_fit(body, lines_of[unit], per, -4 * per, 4 * per)
        else:                                        # a sync on flags: which unit, and where in it
            fits = {u: fskprobe_fit(bits, ln, per) for u, ln in lines_of.items()}
            unit = max(fits, key=lambda u: fits[u][0])
            (agree, e, at), body, where = fits[unit], bits, f"{unit}@{fits[unit][2] // per}"
        loose, moves = fskprobe_local(body, lines_of[unit], per, at)
        ok = max(len(hdlc_frames(body[o::per])) for o in range(per))
        out.append(f"v{n:02d} #{k} {where:8s} {per} a bit: marks {e / per:+.2f} bit short fits {100 * agree:.1f}%, "
                   f"loose {100 * loose:.1f}% {moves} slips, frames {ok}")
    return out + [ln for ln in said if ln[:1] == "v" or ln.startswith("fskaprs")]


# ---- the receiver on the chip's modem (c/k6/mod/aprsfsk.c): its packets from the serial log ----
def hdlc_spans(tones):
    """line bits -> [(first index, index after the closing flag, good FCS, bytes)] for every run
    between two flags that is a whole number of bytes, 17 or more"""
    out, sr, inf, buf, n, at, last = [], 0, False, 0, 0, 0, int(tones[0]) if len(tones) else 0
    for i, x in enumerate(tones):
        bit = int(x == last)
        last = x
        sr = ((sr << 1) | bit) & 0xFF
        if sr == 0x7E:
            if inf and n >= 7 + 8 * 17 and (n - 7) % 8 == 0:
                f = (buf & ((1 << (n - 7)) - 1)).to_bytes((n - 7) // 8, "little")
                out.append((at, i + 1, crc16(f[:-2]) == f[-2] | f[-1] << 8, f))
            inf, buf, n, at = True, 0, 0, i + 1
        elif sr & 0x7F == 0x7F:
            inf = False
        elif inf and sr & 0x3F != 0x3E:
            buf |= bit << n
            n += 1
    return out


def fsklog_report(lines):
    """aprsfsk's say lines (as in the serial log) -> one line a packet the chip took: its y
    line, the flags in its words, each good frame, and for a run between flags with a bad FCS
    whether one wrong tone bit explains it (then the frame it would have been)"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("aprs start")] or [0]):]
    caps, out = {}, []
    stream = bool(said) and said[0].startswith("aprs start") and said[0].endswith(" stream")
    for ln in said:
        f = ln.split()
        if ln.startswith("d ") and len(f) > 3 and f[1].isdigit():
            caps.setdefault(int(f[1]), []).extend(int(w, 16) for w in f[3:])
    for ln in said:
        f = ln.split()
        if not (ln.startswith("y ") and len(f) > 2 and f[1].isdigit()):
            if ln[:2] in ("f ", "m ") or ln.startswith(("clk ", "aprs ")):
                out.append(ln)
            continue
        words = caps.get(int(f[1]))
        if words is None:
            out.append(ln)
            continue
        bits = np.array([int(b) for w in words for b in f"{w:016b}"], np.int8)
        if stream:
            # aprsfskb's words begin at the bit after the sync word's last flag: that flag again
            # (a 0, a change of tone, follows a flag), so a frame that begins at once is found
            bits = np.concatenate([[1 - bits[0]], [bits[0]] * 7, [1 - bits[0]], bits]).astype(np.int8)
        nrzi = "".join(str(int(a == b)) for a, b in zip(bits[1:], bits[:-1]))
        ends = [i + 2 for i in range(7, len(nrzi)) if nrzi[i - 7:i + 1] == "01111110"]   # index after each flag
        notes, done = [], 0
        for k, p in enumerate(ends):
            for q in ends[k + 1:k + 3]:               # to the next flag, or one past it (a flag made by an error)
                if q - p < 8 * 18 or p < done:
                    continue
                lo = max(p - 9, 0)
                good = [x for x in hdlc_spans(bits[lo:q]) if x[2]]
                if good:
                    notes.append("ok " + describe(good[0][3]))
                    done = q
                    break
                for e in range(lo, q):
                    t = bits[lo:q].copy()
                    t[e - lo] ^= 1
                    good = [x for x in hdlc_spans(t) if x[2]]
                    if good:
                        notes.append(f"bad, bit {e} wrong: {describe(good[0][3])}")
                        done = q
                        break
                else:
                    if q == ends[k + 1]:
                        notes.append(f"bad {(q - p) // 8} B")
                    continue
                break
        flags = len(ends)
        out.append(f"{ln} flags={flags} " + ("; ".join(notes) if notes else "no frame-sized run between flags"))
    return out


# ---- a float decoder for recordings (correlators + PLL + HDLC, as the K6 module) ----
def demod(audio, fs):
    """audio -> list of frames (bytes, with good FCS) and each one's end sample"""
    n = int(round(fs / BAUD))
    t = np.arange(len(audio))
    out = []
    for f in (MARK, SPACE):
        z = audio * np.exp(-2j * np.pi * f * t / fs)
        c = np.convolve(z, np.ones(n), "same")
        out.append(np.abs(c))
    m, s = out
    k = max(1, int(0.05 * fs))
    pm = np.maximum.accumulate(m)  # coarse: normalise by a running mean of each
    pm = np.convolve(m, np.ones(k) / k, "same") + 1e-9
    ps = np.convolve(s, np.ones(k) / k, "same") + 1e-9
    v = (m / pm - s / ps) > 0
    frames, pll, step, last_v, last_bit = [], 0.0, BAUD / fs, v[0], v[0]
    sr, inf, buf, bb = 0, False, [], 0x80
    for i, x in enumerate(v):
        pll += step
        if pll >= 0.5:
            pll -= 1.0
            bit = int(x == last_bit)
            last_bit = x
            sr = ((sr << 1) | bit) & 0xFF
            if sr == 0x7E:
                if inf and len(buf) >= 18 and crc16(bytes(buf[:-2])) == buf[-2] | buf[-1] << 8:
                    frames.append((bytes(buf), i))
                inf, buf, bb = True, [], 0x80
                continue
            if sr & 0x7F == 0x7F:
                inf = False
                continue
            if not inf or sr & 0x3F == 0x3E:
                continue
            if bit:
                bb |= 0x100
            if bb & 1:
                buf.append(bb >> 1)
                bb = 0x80
            else:
                bb >>= 1
        if x != last_v:
            pll *= 0.75 if inf else 0.5
            last_v = x
    return frames


# ---- the FD6818's plain FSK receiver as a sampler of the audio (c/k6/mod/dfskcap.c) ----
# What the bench captures of 2026-11-10 12:20Z show (docs/k6-aprs-clock.md). At 4800 baud the
# receiver's bit clock follows the audio's zero crossings: 2200 Hz comes as alternating bits, a
# sample a half cycle (227 us, so a space bit is 3.67 samples), 1200 Hz as 0011, two samples a
# half cycle (417 us, a mark bit is four). So the time between two transitions says the tone:
# one sample is a half cycle of space, two (or three) one of mark. The half cycle at a change of
# tone goes to either side, which makes a run of n mark bits 2n - 1, 2n or 2n + 1 mark half
# cycles; counted together with the space run after it the sum comes out.
DFSK_T = 44          # a line bit in the slicer's units (18.94 us)
DFSK_SP = 12         # a half cycle of 2200 Hz: an interval of one sample
DFSK_MK = 22         # a half cycle of 1200 Hz: an interval of two samples
DFSK_BIAS = 3        # added to a mark run and its space run before the sum is rounded
DFSK_IDLE = 8        # samples without a transition: no tone of Bell 202 (the runs are flushed)


def dfsk_destale(words, depth=8, apart=6):
    """dfskcap read the FIFO by time, a word every 16 bits at 4800 baud, but the chip's clock
    runs with the audio and is up to a tenth slower: about every eleventh read found the FIFO
    empty, and such a read gives the slot's old word, the one read `depth` (the FIFO's size)
    words before, without taking anything. -> (the words without those reads, their indexes).
    A real word may equal the one eight before it (flags do, and text does now and then); the
    empty reads come ten apart or right after one another, so a match 1 to apart - 1 reads after
    one is kept"""
    good, dropped, since = [], [], apart
    for i, w in enumerate(words):
        if len(good) >= depth and w == good[-depth] and (since == 0 or since >= apart):
            dropped.append(i)
            since = 0
        else:
            good.append(w)
            since += 1
    return good, dropped


def dfsk_line(samples, bias=DFSK_BIAS):
    """the plain FSK receiver's bits at 4800 baud -> line bits (1 = 1200 Hz), as
    c/k6/mod/aprsclk.c's FSK_PLAIN slices them: intervals between transitions (one sample: a
    half cycle of space; more: of mark) summed into runs of one tone, a space run rounded to
    bits by itself, a mark run as the rounded sum of both less the space run's bits"""
    out, last, c, tone, dur, md = [], 0, DFSK_IDLE, 1, 0, -1

    def end_run():
        nonlocal md
        if tone:
            md = dur
            return
        ns = max(1, (dur + DFSK_T // 2) // DFSK_T)
        if md >= 0:
            out.extend([1] * max(1, (md + dur + bias + DFSK_T // 2) // DFSK_T - ns))
            md = -1
        out.extend([0] * ns)

    for b in samples:
        b = int(b)
        if b == last:
            c += 1
            if c == DFSK_IDLE:               # silence: what is held goes out, a pending mark run alone
                end_run()
                if md >= 0:
                    out.extend([1] * max(1, (md + bias + DFSK_T // 2) // DFSK_T))
                    md = -1
                tone, dur = 1, 0
            continue
        if c < DFSK_IDLE:
            t, d = int(c >= 2), DFSK_SP if c == 1 else DFSK_MK if c == 2 else c * DFSK_T // 4
            if t == tone:
                dur += d
            else:
                end_run()
                tone, dur = t, d
        last, c = b, 1
    return out


def dfsk_like(line, seed=0):
    """line bits (1 = 1200 Hz) -> bits as the chip's plain FSK receiver gives them at 4800 baud
    (a model of the bench captures, for the tests): a mark run of n bits as 2n - 1 or 2n
    intervals of two samples, a space run as 3.67 n intervals of one, one more after the
    shorter mark run"""
    rng = np.random.default_rng(seed)
    line = np.asarray(line, int)
    edges = np.flatnonzero(np.diff(line)) + 1
    out, level, short = [], 0, 0
    for seg in np.split(line, edges):
        n = len(seg)
        if seg[0]:
            short = int(rng.integers(0, 2))
            iv = [2] * (2 * n - short)
        else:
            iv = [1] * (int(3.67 * n + 0.4) + short)
            short = 0
        for k in iv:
            level ^= 1
            out += [level] * k
    return np.array(out, np.int8)


def dfsklog_caps(lines):
    """dfskcap's say lines (as in the serial log) -> (the lines said, {setting: its s line's
    values}, {(setting, capture): words})"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("dfskcap start")] or [0]):]
    sets, caps = {}, {}
    for ln in said:
        f = ln.split()
        if ln.startswith("s ") and len(f) > 3 and f[1].isdigit():
            sets[int(f[1])] = dict(kv.split("=") for kv in f[2:])
        elif ln[:1] == "d" and len(f) == 4 and f[0][1:].isdigit() and f[1].isdigit():
            w = caps.setdefault((int(f[0][1:]), int(f[1])), [])
            if int(f[2], 16) == len(w):
                w.extend(int(f[3][i:i + 4], 16) for i in range(0, len(f[3]) - 3, 4))
    return said, sets, caps


def dfsk_read(words):
    """a capture's words at 4800 baud -> (the words without the reads of the empty FIFO, how
    many those were, the frames with a good FCS in the rest). In an opening of flags a word
    often is the one eight before, so both of dfsk_destale's rules are tried"""
    for apart in (6, 1):
        good, dropped = dfsk_destale(words, apart=apart)
        line = dfsk_line([int(c) for w in good for c in f"{w:016b}"])
        fr = [x[3] for x in hdlc_spans(line) if x[2]]
        if fr:
            break
    return good, len(dropped), fr


def dfsklog_report(lines):
    """dfskcap's say lines (as in the serial log) -> one line a capture: its c line, the bytes
    the chip's packet held and how long that is, the share of runs of 1, 2 and more samples,
    and at 4800 baud the reads of the empty FIFO taken out (dfsk_destale) and the frames
    dfsk_line reads in the rest"""
    said, sets, caps = dfsklog_caps(lines)
    out = []
    for ln in said:
        f = ln.split()
        if not (ln[:1] == "c" and len(f) > 3 and f[0][1:].isdigit() and f[1].isdigit()):
            if ln[:1] in ("s", "v") or ln.startswith("dfskcap "):
                out.append(ln)
            continue
        key = (int(f[0][1:]), int(f[1]))
        words = list(caps.get(key, []))
        if key[0] not in sets or not words:
            out.append(ln + " : no words")
            continue
        baud = int(round(int(sets[key[0]]["b"], 16) / 10.32444))
        note = [f"{2 * len(words)} bytes, {len(words) * 16000 / baud:.0f} ms at {baud} baud"]
        b = np.array([int(c) for w in words for c in f"{w:016b}"], np.int8)
        edges = np.flatnonzero(np.diff(b)) + 1
        runs = np.diff(np.concatenate([[0], edges, [len(b)]]))
        note.append(f"runs 1:{np.mean(runs == 1):.0%} 2:{np.mean(runs == 2):.0%} 3+:{np.mean(runs >= 3):.0%}")
        if baud == 4800:
            _, empty, fr = dfsk_read(words)
            note.append(f"empty reads {empty}; frames {len(fr)}" + "".join(" [" + describe(x) + "]" for x in fr))
        out.append(ln + " : " + "; ".join(note))
    return out


def plainlog_report(lines):
    """aprsdfsk's say lines (as in the serial log) -> its start, s and end lines, and for each
    sync word the chip started on (the y lines' s=: 0 AAAA, the flags' long tone the space, 1
    3333, the mark; the first build's logs: for each way of reading the FIFO, m=): the packets,
    those with a good frame, the words that were the one eight before and the reads of the
    empty FIFO among them, and the frames by station (the f lines before a y line are that
    packet's)"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("aprs start")] or [0]):]
    out, modes, calls = [], {}, []
    for ln in said:
        f = ln.split()
        if ln.startswith("f ") and len(f) > 1:
            calls.append(f[1])
        elif ln.startswith("y ") and len(f) > 2:
            kv = dict(x.split("=") for x in f[2:] if "=" in x)
            key = "m=" + kv["m"] if "m" in kv else "sync " + ("3333" if kv.get("s") == "1" else "AAAA")
            m = modes.setdefault(key, {"n": 0, "ok": 0, "e": 0, "d": 0, "by": {}})
            m["n"] += 1
            m["ok"] += int(kv.get("ok", "0")) > 0
            m["e"] += int(kv.get("e", "0"))
            m["d"] += int(kv.get("d", "0"))
            for c in calls:
                m["by"][c] = m["by"].get(c, 0) + 1
            calls = []
        elif ln.startswith(("aprs ", "s ", "m ", "clk ")):
            out.append(ln)
    for k in sorted(modes):
        m = modes[k]
        out.append(f"{k}: {m['n']} packets, {m['ok']} with a frame, e={m['e']}"
                   + ("" if k.startswith("m=") else f" d={m['d']}") + "; "
                   + ", ".join(f"{c} {n}" for c, n in sorted(m["by"].items())))
    return out


def describe(f):
    """'SRC>DST,PATH:info' for a frame (with FCS)"""
    calls, i = [], 0
    while True:
        a = f[i:i + 7]
        c = bytes(b >> 1 for b in a[:6]).decode("latin-1").strip()
        ssid = (a[6] >> 1) & 15
        calls.append(c + (f"-{ssid}" if ssid else "") + ("*" if a[6] & 0x80 and i >= 14 else ""))
        i += 7
        if a[6] & 1:
            break
    info = f[i + 2:-2].decode("latin-1")
    return f"{calls[1]}>{calls[0]}{''.join(',' + c for c in calls[2:])}:{info}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wav")
    w.add_argument("out")
    w.add_argument("--utc", type=float, default=8 * 86400 + 17 * 3600 + 52 * 60 + 1.0)
    w.add_argument("--now", action="store_true",
                   help="the on-air loopback: three AG7EW stations stamped with the UTC now (play it at once)")
    w.add_argument("--call", default="AG7EW")
    w.add_argument("--delay", type=float, default=0.0,
                   help="with --now: seconds from now until the audio starts (keying time); the stamps stay "
                        "truncated, so too small only makes them early")
    w.add_argument("--repeat", type=int, default=1,
                   help="with --now: the three stations this many times over, each stamped for its own "
                        "time (40 is about 170 s: a steady run of packets for a receiver test)")
    w.add_argument("--kinds", action="store_true",
                   help="with --now: packets as they come off the air, a kind after another (KINDS: "
                        "pre-emphasis, de-emphasis, low deviation, noise, a short opening), --repeat rounds; "
                        "a packet's kind is its station's number")
    w.add_argument("--only", default="",
                   help="with --kinds: these kinds only, their numbers with commas (9 and 10 have the "
                        "tones swapped and are in no round unless named here)")
    w.add_argument("--swap", action="store_true",
                   help="with --now: every second round with the tones swapped (the flags' long tone "
                        "the mark), for c/k6/mod/aprsfsk.c's sync word")
    q = sub.add_parser("fskprobe", help="the test signal for c/k6/mod/fskaprs.c: units of the chip's own "
                                        "preamble and sync word, then AX.25, and plain APRS packets")
    q.add_argument("out")
    q.add_argument("--seconds", type=float, default=262.0,
                   help="the module waits for the first unit, then listens for 27 slots of 8.5 s")
    q = sub.add_parser("fskreport", help="what the chip made of each fskprobe unit, from the K6's serial log "
                                         "(the module's c and d lines)")
    q.add_argument("log")
    q = sub.add_parser("dfsklog", help="what c/k6/mod/dfskcap.c's captures of the chip's plain FSK receiver "
                                       "held, from the K6's serial log (its s, c and d lines)")
    q.add_argument("log")
    q = sub.add_parser("plainlog", help="what c/k6/mod/aprsdfsk.c read, from the K6's serial log: packets "
                                        "and frames by station for each way of reading the FIFO")
    q.add_argument("log")
    q = sub.add_parser("fsklog", help="what c/k6/mod/aprsfsk.c's packets held, from the K6's serial log (its y "
                                      "and d lines): flags, good frames, and bad ones a single bit away")
    q.add_argument("log")
    d = sub.add_parser("decode")
    d.add_argument("wav")
    a = ap.parse_args(argv)
    if a.cmd == "dfsklog":
        with open(a.log, errors="replace") as f:
            print("\n".join(dfsklog_report(f.read().splitlines())))
        return 0
    if a.cmd == "plainlog":
        with open(a.log, errors="replace") as f:
            print("\n".join(plainlog_report(f.read().splitlines())))
        return 0
    if a.cmd == "fsklog":
        with open(a.log, errors="replace") as f:
            print("\n".join(fsklog_report(f.read().splitlines())))
        return 0
    if a.cmd == "fskreport":
        with open(a.log, errors="replace") as f:
            print("\n".join(fskprobe_report(f.read().splitlines())))
        return 0
    import soundfile as sf
    if a.cmd == "fskprobe":
        x, sent = fskprobe_audio(a.seconds)
        sf.write(a.out, 0.5 * x, FS)
        print(f"{len(x) / FS:.2f} s, {len(sent)} units")
        for name, pos, _ in sent[:len(FSKPROBE_ROUND) + 1]:
            print(f"{name} +{pos:.3f} s")
        return 0
    if a.cmd == "wav" and a.now:
        import time
        t0 = time.time() + a.delay
        parts, sent, pos = [], [], 0.0
        for k in range(1 if a.kinds else max(1, a.repeat)):
            only = [int(v) for v in a.only.split(",") if v]
            x1, s1 = (kinds_wav(t0, a.call, rounds=max(1, a.repeat), kinds=only) if a.kinds else
                      bench_wav(t0 + pos, a.call, invert=a.swap and k % 2 == 1))
            parts.append(x1)
            sent += [(c, info, pos + end) for c, info, end in s1]
            pos += len(x1) / FS
        x = np.concatenate(parts)
        sf.write(a.out, 0.5 * x, FS)
        print(f"start {t0:.3f}")
        for c, info, end in sent:
            print(f"{c} {info}  (ends +{end:.3f} s)")
        return 0
    if a.cmd == "wav":
        fr = [frame("N0CALL-9", "APRS", "/" + ts_hms(a.utc) + "4857.00N/12229.00W>test"),
              frame("N0CALL-1", "APRS", "@" + ts_dhm(a.utc) + "4857.00N/12229.00W_test")]
        x, _ = packet_audio(fr)
        sf.write(a.out, 0.5 * x, FS)
        return 0
    x, fs = sf.read(a.wav)
    if x.ndim > 1:
        x = x[:, 0]
    for t, f in decode_any(x, fs):
        print(f"{t:9.3f} {describe(f)}")
    return 0


def decode_any(x, fs, deemph=(0.95, 0.9)):
    """a radio's audio: flat, and through a 1-pole de-emphasis (pole per sample at fs), since
    pre-emphasised AFSK off a receiver's discriminator decodes only after it (the bench's 144.390
    recording, 2026-11-08). -> [(end s, frame)], each frame once"""
    from scipy.signal import lfilter
    got = []
    for p in (None,) + tuple(deemph):
        y = x if p is None else lfilter([1 - p], [1, -p], x)
        for f, i in demod(y, fs):
            if not any(f == g and abs(i / fs - t) < 0.2 for t, g in got):
                got.append((i / fs, f))
    return sorted(got, key=lambda tf: tf[0])


if __name__ == "__main__":
    sys.exit(main())
