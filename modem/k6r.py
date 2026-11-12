"""PHY R, the K6's range mode (docs/k6-range.md): a two-level mode for the FD6818's plain FSK
receiver (the chip's sensitive FM path, used as a sign sampler at 4800 a second), sent as FFSK
that the UV-380's pager sender (c/common/ffsk_tx.c) already makes: tones 600 Hz (bit 1) and
1200 Hz (bit 0) at 1200 baud, phase continuous. The K6 side is c/k6/mod/rangerx.c; this module
is the sender and a reference receiver that does in floats what the module does in integers.

Why these tones: at 1200 baud 600 Hz is half a cycle a bit and 1200 Hz a whole one, so the audio
changes sign at every bit edge and, for a 0, once more in the middle (biphase). The chip's
sampler gets a sign change at least every bit (the POCSAG work: its clock slips in long runs),
the line has no DC (the IC-7100's audio high-pass at about 300 Hz bends an NRZ line until it
fails: sim/k6if/range.py), and at 4800 samples a second a bit is exactly four samples: 600 Hz a
run of four, 1200 Hz two and two.

Precoding: the tone bit sent is b[k] = a[k] ^ a[k-1], where a is the line bit stream. Then the
audio's sign over the window from the middle of bit k to the middle of bit k + 1 (across the
edge, where it always flips) follows a[k] itself: y[k] = (second half of bit k) - (first half of
bit k + 1) is a coherent soft value of a[k], up to one sign for the whole burst, which the sync
word settles. Detecting the tone bits one by one instead (did it flip mid-bit) is differential
and costs about 1 dB on the model.

A burst, in line bits a:
  LEAD_BITS of lead: tone bits 1 1 1 1 1 0 0 0 over and over (lead_bits: five 600 Hz bits in
    a row give the chip's sync word F0F0 once in 8 bits)
  then for each packet: SYNC (32 bits, 1ACFFC1D, MSB first) and the packet's 2064 coded bits
    (packets after the first: GAP_BITS of lead before the sync, for the module to re-arm the
    chip and catch the next one: it finds a packet's end 8 FIFO words, 32 bits, late)
  TAIL_BITS of lead
A packet is a K6 pager payload (XL, 128 bytes, whitened, CRC: modem/pager.py) coded as PHY G
codes it (modem/k6g.py: K=7, polynomials 133 and 171, 1032 steps with 8 zero tail bits, 2064
coded bits c) and interleaved in 24 rows of 86: line bit i of the packet is c[86 (i % 24) + i // 24],
so a burst of up to 24 wrong line bits lands on coded bits 43 code steps apart.
MIT licence."""
import numpy as np

from modem import fsk, k6g, pager

BAUD = 1200
MARK, SPACE = 600.0, 1200.0     # tone bit 1, tone bit 0
SPS = 4                         # the chip's samples a bit (REG_72 0xC195: 4800 a second)
LEVEL = 0.10                    # peak (IC-7100 model: about 1.2-2 kHz deviation; the model
                                # reads best low with the narrow filter, sim/k6if/range.py)
LEAD_BITS = 600                 # 0.5 s
GAP_BITS = 128
TAIL_BITS = 32
SYNC = 0x1ACFFC1D
SYNC_BITS = np.array([(SYNC >> (31 - i)) & 1 for i in range(32)], np.uint8)
SYNC_LEAD = 0                   # lead bits ahead of SYNC in the receiver's pattern (16 made false syncs in the lead)
HUNT_BITS = np.concatenate([np.arange(SYNC_LEAD) & 1, SYNC_BITS]).astype(np.uint8)
STEPS = k6g.STEPS               # 1032
CODED = 2 * STEPS               # 2064
ROWS, COLS = 24, 86
RESYNC_BITS = 160               # a packet's first soft values: a sync better by RESYNC_MARGIN
RESYNC_MARGIN = 16              # than the one it started on starts it again there
SYNC_MIN = 56                   # the module's sync threshold: |correlation| of 32 soft values
                                # (each -4..4 at 4 samples a bit) with the sync's +-1


def coded_bits(payload):
    """one 128-byte payload (as on air: whitened) -> its 2064 coded bits, in code order"""
    bits = np.unpackbits(np.frombuffer(bytes(payload), np.uint8))
    return k6g.conv_encode(np.concatenate([bits, np.zeros(k6g.TAIL_BITS, np.int8)])).astype(np.uint8)


def interleave(c):
    """code order -> line order"""
    return np.asarray(c).reshape(ROWS, COLS).T.reshape(-1)


def deinterleave(a):
    """line order -> code order"""
    return np.asarray(a).reshape(COLS, ROWS).T.reshape(-1)


def alternating(n, first=0):
    return (np.arange(n) + first) & 1


LEAD_TONES = np.array([1, 1, 1, 1, 1, 0, 0, 0], np.uint8)     # the lead's tone bits, repeated


def lead_bits(n, prev=0):
    """n line bits of lead or gap: tone bits LEAD_TONES over and over, ending on the pattern's
    end. Five 600 Hz bits in a row give the chip's sync word F0F0 (either way up) once in 8 bits,
    and the three 1200 Hz bits make the lead sound like data to the bit clock: a lead of 600 Hz
    alone held the clock where a 600 Hz edge falls, and on the IC-7100 model (its 300 Hz high-pass
    turns 600 Hz on about 0.7 sample more than 1200 Hz) the sync after it came out garbled"""
    b = np.resize(LEAD_TONES, n + (-n) % len(LEAD_TONES))[-n:] if n else np.zeros(0, np.uint8)
    return ((np.cumsum(b) + prev) & 1).astype(np.uint8)


def burst_bits(payloads, lead=LEAD_BITS, gap=GAP_BITS, tail=TAIL_BITS):
    """the line bits a of a burst"""
    parts = [lead_bits(lead)]
    for i, p in enumerate(payloads):
        if i:
            parts.append(lead_bits(gap, parts[-1][-1]))
        parts += [SYNC_BITS, interleave(coded_bits(p))]
    parts.append(lead_bits(tail, parts[-1][-1]))
    return np.concatenate(parts).astype(np.uint8)


def tone_bits(a):
    """precoding: line bits -> the tone bits sent (1: MARK 600 Hz)"""
    a = np.asarray(a, np.uint8)
    return np.concatenate([[1], a[1:] ^ a[:-1]]).astype(np.uint8)


def audio(a, fs=48000, level=LEVEL):
    """line bits -> the sender's audio (FFSK 600/1200 Hz, 1200 baud), `level` peak"""
    return fsk.ffsk(tone_bits(a), BAUD, fs, mark=MARK, space=SPACE) * level


def burst_seconds(npackets, lead=LEAD_BITS, gap=GAP_BITS, tail=TAIL_BITS):
    return (lead + npackets * (32 + CODED) + max(0, npackets - 1) * gap + tail) / BAUD


def test_payloads(n, codec="400", msg_id=0x52, seed=1):
    """n XL pager payloads (whitened, CRC right) of random sotto frames: what a bench run sends,
    so the module's lines tell which packets came (seq) and whether each one checked"""
    rng = np.random.default_rng(seed)
    prof = pager.profile("XL", codec, depth=1)
    frames = bytes(rng.integers(0, 256, 6 * 20 * n, dtype=np.uint8))
    return prof.pack(frames, msg_id=msg_id)[:n]


def crc_ok(payload):
    """a whitened XL payload's CRC checks"""
    p = bytes(x ^ w for x, w in zip(payload, pager.WHITEN))
    return pager.crc16_ccitt(p[:126]) == (p[126] << 8 | p[127])


# ---- the receiver's side (c/k6/mod/rangerx.c does the same in integers) ----

def window_soft(samples, phase=0):
    """chip samples (0/1) at exactly SPS a bit, bit edges `phase` samples in -> y[k], the soft
    value of line bit k (> 0: the sign the burst calls 0 once the sync has said which)"""
    v = 2 * np.asarray(samples, int)[phase:] - 1
    n = len(v) // SPS
    s = v[:n * SPS].reshape(n, SPS)
    h = SPS // 2
    return s[:-1, h:].sum(1) - s[1:, :h].sum(1)


def find_syncs(y, thr=SYNC_MIN):
    """indices k (the sync's last soft value) and signs where |corr| >= thr, best of each run"""
    ref = 1 - 2 * HUNT_BITS.astype(int)
    c = np.correlate(np.asarray(y, int), ref, "valid")
    out, k = [], 0
    while k < len(c):
        if abs(c[k]) >= thr:
            j = k + int(np.argmax(np.abs(c[k:k + 8])))
            out.append((j + len(ref) - 1, int(np.sign(c[j])), int(c[j])))
            k = j + 32
        else:
            k += 1
    return out


def decode_soft(ys):
    """2064 soft values (line order, > 0: bit 0) -> the 128 bytes"""
    c = deinterleave(np.asarray(ys, float))
    return np.packbits(k6g.viterbi(c.reshape(-1, 2))[:1024]).tobytes()


def receive(samples, phase=0, thr=SYNC_MIN):
    """chip samples at exactly SPS a bit (ideal clock) -> [(payload, crc ok)]"""
    y = window_soft(samples, phase)
    out = []
    for k, sgn, _ in find_syncs(y, thr):
        seg = y[k + 1:k + 1 + CODED]
        if len(seg) < CODED:
            break
        p = decode_soft(sgn * seg)
        out.append((p, crc_ok(p)))
    return out


# ---- the module's lines ----

def parse_log(lines):
    """the module's say lines -> (start, packets [dict], ends [dict])"""
    import re
    start, pk, ends = None, [], []
    for ln in lines:
        m = re.search(r"K6 MOD say (.*)", ln)
        if not m:
            continue
        s = m.group(1).split()
        if not s:
            continue
        kv = {a: b for a, _, b in (w.partition("=") for w in s[1:] if "=" in w)}
        if s[0] == "range" and len(s) > 1 and s[1] == "start":
            start = kv
        elif s[0] == "range" and len(s) > 1 and s[1] == "end":
            ends.append(kv)
        elif s[0] == "r":
            kv["n"] = s[1]
            pk.append(kv)
    return start, pk, ends


# ---- the module's receiver, step for step in integers (c/k6/mod/rangerx.c) ----

CODE_OUT = [0, 2, 0, 2, 3, 1, 3, 1, 3, 1, 3, 1, 0, 2, 0, 2,       # as c/k6/ifpager.c's ifp_out
            1, 3, 1, 3, 2, 0, 2, 0, 2, 0, 2, 0, 1, 3, 1, 3]
Q_MAX = 7                       # a stored soft value: -7..7 (4 bits)
T0 = SPS << 8                   # samples a bit, Q8
KP = 8                         # the bit clock: an edge moves by KP x (|late| - |early|) (Q8 samples)
KI_SHIFT = 4                    # and the bit's length by the sum of them / 2^KI_SHIFT
T_LIM = 160                     # the length stays within T0 +- T_LIM (Q8): 4 +- 0.62 samples
HALF_WIN = 32                   # bits between half-bit checks
HALF_MARGIN = 24                # the half-shifted windows' sum of |y| must beat the clock's by this


class Slicer:
    """chip samples (0/1) one at a time -> soft line bits y (ints), as rangerx.c's slicer.
    Times are Q8 samples (sample i's centre at 256 i + 128). Bit k's edge is at e (the next at
    e + t, t the bit's length): y[k] = (samples centred in [e + t/2, e + t)) - (those in
    [e + t, e + 3t/2)), the window across the next edge, where the sign always changes. The same
    window a sample early and a sample late steers the clock (early-late on |y|: the edge moves by
    KP x (|late| - |early|), t by their running sum / 2^KI_SHIFT), and every HALF_WIN bits the window half a bit on is
    compared (sum of |y|): when it beats the clock's by HALF_MARGIN the clock has taken the
    middle of the bits for their edges, and moves half a bit (not while hold: a packet)"""

    def __init__(self):
        self.n = 0                      # samples in
        self.hist = [0] * 16            # the last 16 samples (+-1), by n & 15
        self.e, self.t = 0, T0          # the edge before the window being built
        self.acc = 0                    # the sum of the early-late errors (t = T0 + acc >> KI_SHIFT)
        self.hold = 0
        self.nb = self.son = self.shalf = 0
        self.flips = 0
        self.out = []

    def win(self, a):
        """sum over samples centred in [a, a + t/2) minus those in [a + t/2, a + t)"""
        h = self.t >> 1
        y = 0
        i0 = max(self.n - 16, (a - 128 + 255) >> 8 if a > 128 else 0)
        for i in range(i0, self.n):
            c = 256 * i + 128
            if c < a:
                continue
            if c < a + h:
                y += self.hist[i & 15]
            elif c < a + self.t:
                y -= self.hist[i & 15]
            else:
                break
        return y

    def feed(self, s):
        self.hist[self.n & 15] = 1 if s else -1
        self.n += 1
        t = self.t
        a = self.e + (t >> 1)           # the window: [a, a + t), the next edge at a + t/2
        while 256 * self.n >= a + t + 256 + 128:      # the late window is in
            y = self.win(a)
            el = abs(self.win(a + 256)) - abs(self.win(a - 256))
            self.out.append(y)
            self.nb += 1
            self.son += abs(y)
            self.shalf += abs(self.win(a + (t >> 1)))
            if self.nb >= HALF_WIN:
                if self.shalf > self.son + HALF_MARGIN and not self.hold:
                    self.e += t >> 1
                    self.flips += 1
                self.nb = self.son = self.shalf = 0
            self.e += t + KP * el
            lim = T_LIM << KI_SHIFT
            self.acc = min(max(self.acc + el, -lim), lim)
            self.t = T0 + (self.acc >> KI_SHIFT)
            t = self.t
            a = self.e + (t >> 1)


class Receiver:
    """rangerx.c's receiver above the slicer: hunt the sync in the soft values (|correlation| at
    least SYNC_MIN), then take CODED of them, its sign put right and clamped to +-Q_MAX, decode,
    check; the slicer's half-bit moves held while a packet is read"""

    def __init__(self, thr=SYNC_MIN):
        self.sl, self.thr = Slicer(), thr
        self.ring = []
        self.pkt, self.pol = None, 1
        self.got = []
        self.pc = self.resyncs = 0
        self.ref = 1 - 2 * HUNT_BITS.astype(int)

    def feed(self, s):
        n0 = len(self.sl.out)
        self.sl.feed(s)
        for y in self.sl.out[n0:]:
            self.soft(y)
        del self.sl.out[:]

    def soft(self, y):
        self.ring = (self.ring + [y])[-len(self.ref):]
        c = int(np.dot(self.ring, self.ref)) if len(self.ring) == len(self.ref) else 0
        if self.pkt is not None and len(self.pkt) < RESYNC_BITS and abs(c) >= max(self.thr, self.pc + RESYNC_MARGIN):
            self.pkt, self.pol, self.pc = [], 1 if c > 0 else -1, abs(c)    # a better sync: start again
            self.resyncs += 1
            return
        if self.pkt is not None:
            self.pkt.append(max(-Q_MAX, min(Q_MAX, self.pol * y)))
            if len(self.pkt) == CODED:
                p = decode_soft(self.pkt)
                self.got.append((p, crc_ok(p)))
                self.pkt, self.sl.hold, self.ring = None, 0, []
            return
        if abs(c) >= self.thr:
            self.pkt, self.pol, self.sl.hold, self.pc = [], 1 if c > 0 else -1, 1, abs(c)


def receive_samples(samples, thr=SYNC_MIN):
    r = Receiver(thr)
    for s in samples:
        r.feed(int(s))
    return r.got, r.sl


def slice_samples(samples):
    sl = Slicer()
    for s in samples:
        sl.feed(int(s))
    return np.array(sl.out, int), sl


# ---- the bench: the IC-7100's wav and the log's report (docs/k6-range.md) ----

FS = 48000


def bench_wav(seconds=440.0, npk=6, level=0.3, gap=0.5, lead=0.3, fs=FS):
    """bursts of npk packets back to back for `seconds` (rangerx_b: a 32 s wait, then nine
    settings of 45 s; the module starts about 29 s before its upload ends, so a wav sent straight
    after the upload covers the ladder from its 3rd second) -> (audio, [(t0, t1, burst)]). level:
    the wav's peak (the IC-7100 at USB MOD 30% as the POCSAG bench: its 0.35 was clean)"""
    parts, times, t, b = [np.zeros(int(lead * fs))], [], lead, 0
    while t + burst_seconds(npk) < seconds:
        x = audio(burst_bits(test_payloads(npk, seed=100 + b)), fs, level)
        times.append((t, t + len(x) / fs, b))
        parts += [x, np.zeros(int(gap * fs))]
        t += len(x) / fs + gap
        b += 1
    return np.concatenate(parts).astype(np.float32), times


def write_wav(path, y, fs=FS):
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def report(lines):
    """the module's log -> lines: per ladder setting (rangerx_b) or the whole run (rangerx),
    packets read, those whose CRC checked, their channel errors (of 2064 coded bits) and RSSI"""
    import re
    rows, cur, out = [], None, []

    def new(name):
        r = {"name": name, "pk": [], "v": None}
        rows.append(r)
        return r

    for ln in lines:
        m = re.search(r"K6 MOD say (.*)", ln)
        if not m:
            continue
        w = m.group(1).split()
        if not w:
            continue
        kv = {a: b for a, _, b in (x.partition("=") for x in w[1:] if "=" in x)}
        if w[0] == "range" and len(w) > 1 and w[1] == "start":
            out.append("start: " + " ".join(w[2:]))
            cur = None
        elif w[0] == "s" and len(w) > 1:
            cur = new(f"setting {w[1]} path {kv.get('p', '?')} gain {kv.get('g', '?')}")
        elif w[0] == "v" and cur is not None:
            cur["v"] = kv
        elif w[0] == "r" and len(w) > 1:
            (cur or (rows[-1] if rows and rows[-1]["name"] == "run" else new("run")))["pk"].append(kv)
        elif w[0] == "range" and len(w) > 1 and w[1] == "end":
            out.append("end: " + " ".join(w[2:]))
    for r in rows:
        ok = [p for p in r["pk"] if p.get("ok") == "1"]
        e = [int(p["e"]) for p in ok if p.get("e", "").lstrip("-").isdigit()]
        rs = [int(p["r"]) for p in r["pk"] if p.get("r", "").lstrip("-").isdigit()]
        s = f"{r['name']}: {len(r['pk'])} packets, {len(ok)} right"
        if e:
            s += f", channel errors mean {np.mean(e):.0f} max {max(e)} ({100 * np.mean(e) / CODED:.1f}%)"
        if rs:
            s += f", RSSI {min(rs)}..{max(rs)} dBm"
        if r["v"]:
            s += f", band RSSI {r['v'].get('lo', '?')}..{r['v'].get('hi', '?')} dBm"
        out.append(s)
    return out


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("wav", help="the bench's bursts for the IC-7100")
    q.add_argument("out")
    q.add_argument("--seconds", type=float, default=440.0)
    q.add_argument("--packets", type=int, default=6, help="packets a burst")
    q.add_argument("--level", type=float, default=0.3)
    q = sub.add_parser("report", help="a K6 log's range lines, per setting")
    q.add_argument("log")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        y, times = bench_wav(a.seconds, a.packets, a.level)
        write_wav(a.out, y)
        print(f"{a.out}: {len(y) / FS:.1f} s, {len(times)} bursts of {a.packets} packets "
              f"({burst_seconds(a.packets):.1f} s each)")
    else:
        with open(a.log, errors="replace") as f:
            print("\n".join(report(f)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
