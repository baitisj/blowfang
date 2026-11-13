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
    chip and catch the next one: it finds a packet's end about 50 bits late)
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
SYNC_MIN = 96                   # the module's sync threshold: |correlation| of 32 soft values
                                # (+-4 from a clean bit) with the sync's +-1: 128 from a clean
                                # sync, 116 or more from every packet that decoded at 46-47 dB-Hz
                                # on the model; 56 (run 1 and 2) let coded bits start false
                                # packets about once in 80 bits


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
SEG_D = 64                      # samples the segment decoder looks back before it says a segment
SEG_TB = 16                     # it traces back every SEG_TB samples
SEG_RING = 128                  # samples and decisions kept (more than SEG_D + SEG_TB + 3)
SEG_MAX = 32                    # segments said by one trace at most


class SegSlicer:
    """chip samples (0/1) one at a time -> soft line bits y (-6..6, +-4 from a clean bit), as
    rangerx.c's slicer since bench run 2.

    Why not a clock: the chip's sampler is no steady clock. It is locked to the audio's edges, so
    a 1200 Hz half cycle is two samples and a 600 Hz bit four, but now and then a run is a sample
    short or long (run 2: the rate wanders 3.88..4.0 samples a bit from block to block, and the
    slips come at the ends of runs of 600 Hz bits). Windows on a bit clock lost about a third of
    the bits that way; the samples themselves were clean.

    So it parses the samples into half bits ("segments") of 1 to 3 samples each, with a Viterbi
    over 12 states: the segment's sign s, whether it is a bit's first half or second (p), and how
    many samples it has had (l). A sample against s costs 1, a segment of 1 or 3 samples costs 1
    (2 is right). A bit's edge always changes the sign (the precoded biphase); the middle of a bit
    may (1200 Hz) or may not (600 Hz). Every SEG_TB samples it traces back from the best state and
    says the segments that end more than SEG_D samples back. At each edge, the line bit's soft
    value is (the samples of the bit's second half) - (those of the next bit's first half), each
    sample +-1: what the window slicer summed, on the edges the samples show.

    Decisions, a 16 bit word a sample: bits 3s..3s+2 for a second half of sign s begun on this
    sample (the first half's length l, and 4 if that half's sign was the other), bits 6+2s..7+2s
    for a first half of sign s begun here (the second half before it, of sign 1-s: its length)."""

    def __init__(self):
        self.c = [0] * 12               # cost of state (2 s + p) * 3 + l - 1
        self.dec = [0] * SEG_RING
        self.smp = [0] * SEG_RING       # +-1
        self.n = 0                      # samples in
        self.em = 0                     # the next segment to say begins on this sample
        self.last = None                # the segment said last: (start, length, s, p)
        self.out = []
        self.flips = 0                  # traces that disagreed with what was said (samples lost)
        self.odd = 0                    # segments said of 1 or 3 samples

    def feed(self, x):
        c, o = self.c, (1 if x else 0, 0 if x else 1)
        e, el = [0] * 4, [0] * 4        # a segment ends: (2 s + p) -> best cost, its length
        for k in range(4):
            v1, v2, v3 = c[3 * k] + 1, c[3 * k + 1], c[3 * k + 2] + 1
            if v2 <= v1 and v2 <= v3:
                e[k], el[k] = v2, 2
            elif v1 <= v3:
                e[k], el[k] = v1, 1
            else:
                e[k], el[k] = v3, 3
        nc, d = [0] * 12, 0
        for sg in (0, 1):
            k0, k1 = 3 * (2 * sg), 3 * (2 * sg + 1)
            nc[k0 + 1], nc[k0 + 2] = c[k0] + o[sg], c[k0 + 1] + o[sg]
            nc[k1 + 1], nc[k1 + 2] = c[k1] + o[sg], c[k1 + 1] + o[sg]
            a, b = e[2 * sg], e[2 * (1 - sg)]               # a second half after a first
            if a <= b:
                nc[k1], d = a + o[sg], d | el[2 * sg] << 3 * sg
            else:
                nc[k1], d = b + o[sg], d | (el[2 * (1 - sg)] | 4) << 3 * sg
            nc[k0] = e[2 * (1 - sg) + 1] + o[sg]               # a first half after a second
            d |= el[2 * (1 - sg) + 1] << (6 + 2 * sg)
        m = min(nc)
        self.c = [v - m for v in nc]
        i = self.n % SEG_RING
        self.dec[i], self.smp[i] = d, 1 if x else -1
        self.n += 1
        if self.n % SEG_TB == 0 and self.n >= SEG_D + SEG_TB:
            self.trace(self.n - SEG_D)

    def trace(self, upto):
        """say the segments from em on that end before sample upto"""
        k = min(range(12), key=lambda j: self.c[j])
        sg, p, ln = k // 6, (k // 3) & 1, k % 3 + 1
        t, segs = self.n - 1, []
        while True:
            start = t - ln + 1
            if start < self.em:
                break
            if t < upto and len(segs) < SEG_MAX:
                segs.append((start, ln, sg, p))
            if start == self.em:
                break
            d = self.dec[start % SEG_RING]
            if p:
                f = d >> 3 * sg & 7
                ln, sg, p = f & 3, 1 - sg if f & 4 else sg, 0
            else:
                ln, sg, p = d >> (6 + 2 * sg) & 3, 1 - sg, 1
            t = start - 1
        if segs and segs[-1][0] != self.em:
            self.flips += 1
        for seg in reversed(segs):
            self.say(seg)
            self.em = seg[0] + seg[1]

    def flush(self):
        """the stream's end: say the rest, SEG_TB samples a trace"""
        u = self.em + SEG_TB
        while u < self.n:
            self.trace(u)
            u += SEG_TB
        self.trace(self.n)

    def total(self, seg):
        return sum(self.smp[(seg[0] + j) % SEG_RING] for j in range(seg[1]))

    def say(self, seg):
        self.odd += seg[1] != 2
        if self.last is not None and self.last[3] == 1 and seg[3] == 0:
            self.out.append(self.total(self.last) - self.total(seg))
        self.last = seg


CHIP_SYNC = 0xF0F0              # the chip's sync word: a capture starts on the sample after it
CHIP_SAMPLES = 1024 * 16        # and is 1024 words of 16 samples at most (rangerx.c's NWORDS)


class Receiver:
    """rangerx.c's receiver above the slicer, with the chip's captures: the chip starts one on
    its sync word F0F0 in the samples (the lead's 600 Hz bits) and stops after CHIP_SAMPLES or
    when the module has its packet and re-arms it. In a capture, a new slicer; hunt the sync in
    the soft values (|correlation| at least SYNC_MIN), then take CODED of them, its sign put
    right and clamped to +-Q_MAX, decode, check"""

    def __init__(self, thr=SYNC_MIN, armed=False):
        self.thr = thr
        self.sl = SegSlicer()
        self.armed, self.w, self.k = armed, 0, 0
        self.ring = []
        self.pkt, self.pol = None, 1
        self.got = []
        self.pc = self.resyncs = self.caps = 0
        self.pcs = []                   # each packet's |sync correlation|
        self.errs = []                  # each packet's channel errors (the module's e=)
        self.ref = 1 - 2 * HUNT_BITS.astype(int)

    def feed(self, s):
        if not self.armed:
            self.w = (self.w << 1 | (1 if s else 0)) & 0xFFFF
            if self.w == CHIP_SYNC:
                self.armed, self.k, self.caps = True, 0, self.caps + 1
                self.sl, self.ring, self.pkt = SegSlicer(), [], None
            return
        self.sl.feed(s)
        for y in self.sl.out:
            self.soft(y)
        del self.sl.out[:]
        self.k += 1
        if self.armed and self.k >= CHIP_SAMPLES:
            self.armed, self.w = False, 0        # the chip's capture is over; a packet cut off is lost

    def flush(self):
        if self.armed:
            self.sl.flush()
            for y in self.sl.out:
                self.soft(y)
            del self.sl.out[:]

    def soft(self, y):
        if not self.armed:
            return
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
                self.pcs.append(self.pc)
                self.errs.append(channel_errors(p, self.pkt))
                self.pkt, self.armed, self.w = None, False, 0     # re-armed for the next one
            return
        if abs(c) >= self.thr:
            self.pkt, self.pol, self.pc = [], 1 if c > 0 else -1, abs(c)


def channel_errors(payload, ys):
    """the line bits whose soft value the decoded payload's own code disagrees with (0 counts as
    wrong), as rangerx.c's e="""
    c = interleave(coded_bits(payload))
    ys = np.asarray(ys)
    return int(np.sum(np.where(c == 1, ys >= 0, ys <= 0)))


def receive_samples(samples, thr=SYNC_MIN, armed=False):
    """armed: the samples are one of the chip's captures (rangerx_d's dump), begun on its F0F0"""
    r = Receiver(thr, armed)
    for s in samples:
        r.feed(int(s))
    r.flush()
    return r.got, r.sl


def slice_samples(samples):
    sl = SegSlicer()
    for s in samples:
        sl.feed(int(s))
    sl.flush()
    return np.array(sl.out, int), sl


# ---- the bench: the IC-7100's wav and the log's report (docs/k6-range.md) ----

FS = 48000


def bench_wav(seconds=440.0, npk=6, level=0.3, gap=0.5, lead=0.3, fs=FS, preemph="off"):
    """bursts of npk packets back to back for `seconds` (rangerx_b: a 32 s wait, then nine
    settings of 45 s; the module starts about 29 s before its upload ends, so a wav sent straight
    after the upload covers the ladder from its 3rd second) -> (audio, [(t0, t1, burst, pre)]).
    level: the wav's peak (the IC-7100 at USB MOD 30% as the POCSAG bench: its 0.35 was clean).
    preemph: "on" puts a voice transmitter's pre-emphasis on each burst (+6 dB an octave from
    300 Hz to 3 kHz, as the UV-380's PAGER mode does in the C6000), "alt" on every odd burst"""
    pre = fsk.Channel(pre_emph=300, emph_top=3000, hpf=None, lpf=None)
    parts, times, t, b = [np.zeros(int(lead * fs))], [], lead, 0
    while t + burst_seconds(npk) < seconds:
        x = audio(burst_bits(test_payloads(npk, seed=100 + b)), fs, level)
        pe = preemph == "on" or (preemph == "alt" and b % 2 == 1)
        if pe:
            x = pre.apply(x, fs)
            x = level * x / np.max(np.abs(x))
        times.append((t, t + len(x) / fs, b, pe))
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


# ---- rangerx_d's dumps: what the chip's sampler handed over (python3 -m modem.k6r dump LOG) ----

def dump_captures(lines):
    """rangerx_d's c and d lines -> [dict(n, t, k, samples)]"""
    import re
    caps = {}
    for ln in lines:
        m = re.search(r"K6 MOD say (.*)", ln)
        if not m:
            continue
        f = m.group(1).split()
        if len(f) < 2 or not f[1].isdigit():
            continue
        n = int(f[1])
        if f[0] == "c":
            kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
            caps[n] = dict(kv, n=n, words=[])
        elif f[0] == "d" and len(f) >= 4 and n in caps:
            caps[n]["words"] += [int(f[3][k:k + 4], 16) for k in range(0, len(f[3]) - 3, 4)]
    out = []
    for n in sorted(caps):
        c = caps[n]
        c["samples"] = np.array([w >> (15 - i) & 1 for w in c["words"] for i in range(16)], np.uint8)
        out.append(c)
    return out


def bench_payloads(bursts=48, npk=6):
    """every packet modem.k6r wav sends (bursts seeded 100, 101, ...)"""
    return [p for b in range(bursts) for p in test_payloads(npk, seed=100 + b)]


def dump_report(lines, cands=None):
    """per capture: the sample runs (a clean link: runs of 2 at 1200 Hz, 4 at 600 Hz), the
    receiver's result, and the line bits against the best of the bench's packets: wrong bits by
    the two tone bits the soft value's window spans"""
    cands = bench_payloads() if cands is None else cands
    ref = {p: interleave(coded_bits(p)) for p in cands}
    out = []
    for c in dump_captures(lines):
        smp = c["samples"]
        if len(smp) < 64:
            continue
        ed = np.flatnonzero(np.diff(smp.astype(int))) + 1
        runs = np.diff(ed)
        h = np.bincount(np.minimum(runs, 9), minlength=10)[1:]
        out.append(f"c{c['n']} t={c.get('t')} {len(smp)} samples, {100 * smp.mean():.0f}% ones; "
                   f"runs 1..9+: {' '.join(map(str, h))}")
        got, sl = receive_samples(smp, armed=True)
        out.append(f"  receiver: {len(got)} packets, {sum(ok for _, ok in got)} right, "
                   f"segments of 1 or 3 samples {sl.odd}, traces lost {sl.flips}")
        y, _ = slice_samples(smp)
        if len(y) < 32 + CODED:
            continue
        sref = 1 - 2 * SYNC_BITS.astype(int)
        cor = np.correlate(y, sref, "valid")
        i = int(np.argmax(np.abs(cor)))
        if abs(cor[i]) < SYNC_MIN:
            out.append(f"  no sync (best c={cor[i]} at {i})")
            continue
        pol = 1 if cor[i] > 0 else -1
        a = pol * y[i + 32:i + 32 + CODED]
        if len(a) < CODED:
            out.append(f"  sync at {i} (c={cor[i]}): packet cut off")
            continue
        hard = (a < 0).astype(np.uint8)
        best = min(ref, key=lambda p: int(np.sum(ref[p] != hard)))
        line = np.concatenate([SYNC_BITS, ref[best]])
        wrong = ref[best] != hard
        tb = tone_bits(np.concatenate([[0], line, [0]]))[1:]        # tone bits around each line bit
        k = np.arange(32, 32 + CODED)
        ctx = 2 * tb[k] + tb[k + 1]
        names = ["1200|1200", "1200|600", "600|1200", "600|600"]
        by = " ".join(f"{names[v]} {int(wrong[ctx == v].sum())}/{int((ctx == v).sum())}" for v in range(4))
        out.append(f"  sync at {i} (c={cor[i]}), best match {cands.index(best)} (burst {cands.index(best) // 6}): {int(wrong.sum())} of {CODED} "
                   f"wrong, |y| {np.mean(np.abs(a)):.2f}; by tone bits (bit, next): {by}")
        out.append("  first 64 samples after the sync: " + "".join(map(str, smp[(i + 32) * SPS:][:64])))
    return out


def txhdr(npk=6):
    """the C header c/k6/mod/rangetx.c builds in: a burst of test_payloads(npk) as tone bits (1:
    600 Hz), eight a byte, the first in the top bit"""
    t = tone_bits(burst_bits(test_payloads(npk)))
    b = np.packbits(t)
    rows = ",\n".join("  " + ", ".join(f"0x{v:02x}" for v in b[i:i + 16]) for i in range(0, len(b), 16))
    return ("/* written by python -m modem.k6r txhdr: a PHY R burst of %d test packets (modem.k6r\n"
            "   test_payloads) as tone bits for rangetx.c, 1 = 600 Hz, first bit in the top bit.\n"
            "   SPDX-License-Identifier: MIT */\n"
            "#define RT_PACKETS %du\n#define RT_NBITS %du\n"
            "static const uint8_t RT_TONES[%d] = {\n%s};\n" % (npk, npk, len(t), len(b), rows))


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
    q.add_argument("--preemph", choices=["off", "on", "alt"], default="on",
                   help="on (the default since bench run 2: what the UV-380 sends, and what the "
                        "K6 reads with its de-emphasis off), off, or alt (every other burst)")
    q = sub.add_parser("txhdr", help="the C header of tone bits the K6's sender (rangetx.c) builds in")
    q.add_argument("out")
    q.add_argument("--packets", type=int, default=6, help="packets a burst")
    q = sub.add_parser("report", help="a K6 log's range lines, per setting")
    q.add_argument("log")
    q = sub.add_parser("dump", help="rangerx_d's captured words, against the bench's packets")
    q.add_argument("log")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        y, times = bench_wav(a.seconds, a.packets, a.level, preemph=a.preemph)
        write_wav(a.out, y)
        print(f"{a.out}: {len(y) / FS:.1f} s, {len(times)} bursts of {a.packets} packets "
              f"({burst_seconds(a.packets):.1f} s each)")
    elif a.cmd == "txhdr":
        with open(a.out, "w") as f:
            f.write(txhdr(a.packets))
        print(f"{a.out}: {len(burst_bits(test_payloads(a.packets)))} bits")
    elif a.cmd == "dump":
        with open(a.log, errors="replace") as f:
            print("\n".join(dump_report(f)))
    else:
        with open(a.log, errors="replace") as f:
            print("\n".join(report(f)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
