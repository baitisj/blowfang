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
                                                         channel, receive only

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


def plain_read(words):
    """a capture's words -> (the block's text or None, why, odd runs): SYN SYN looked for in
    the tone bits, the block read after it"""
    t, odd = plain_tones([int(c) for w in words for c in f"{w:016b}"])
    s, want = "".join(map(str, t)), f"{SYNC2:016b}"
    pos, why = s.find(want), "no SYN SYN"
    while pos >= 0:
        text, why = read_block(t[pos + 16:])
        if text is not None:
            return text, "ok", odd
        pos = s.find(want, pos + 1)
    return None, why, odd


def plain_report(lines):
    """dfskcap_acars' say lines (as in the serial log) -> its s, v and b lines, and a line a
    capture: the c line, and for one with words (those that went on like a pre-key) the share
    of runs of one, two and more samples and the block read from them"""
    said = [ln.split("say ", 1)[-1].strip() for ln in lines]
    said = said[max([i for i, ln in enumerate(said) if ln.startswith("dfskcap start")] or [0]):]
    caps, out = {}, []
    for ln in said:
        f = ln.split()
        if ln[:1] == "d" and len(f) == 4 and f[0][1:].isdigit() and f[1].isdigit():
            w = caps.setdefault((int(f[0][1:]), int(f[1])), [])
            if int(f[2], 16) == len(w):
                w.extend(int(f[3][i:i + 4], 16) for i in range(0, len(f[3]) - 3, 4))
    for ln in said:
        f = ln.split()
        if ln[:1] == "c" and len(f) > 3 and f[0][1:].isdigit() and f[1].isdigit():
            words = caps.get((int(f[0][1:]), int(f[1])))
            if words:
                b = np.array([int(c) for w in words for c in f"{w:016b}"], np.int8)
                runs = np.diff(np.concatenate([[0], np.flatnonzero(np.diff(b)) + 1, [len(b)]]))
                text, why, odd = plain_read(words)
                ln += (f" : runs 1:{np.mean(runs == 1):.0%} 2:{np.mean(runs == 2):.0%} 3+:{np.mean(runs >= 3):.0%}"
                       f" odd {odd}; " + (f"block [{text}]" if text is not None else why))
            out.append(ln)
        elif ln[:1] in ("s", "v", "b") or ln.startswith("dfskcap "):
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
    a = ap.parse_args(argv)
    if a.cmd in ("report", "plain"):
        with open(a.log, errors="replace") as f:
            print("\n".join((report if a.cmd == "report" else plain_report)(f.read().splitlines())))
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
