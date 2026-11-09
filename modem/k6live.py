"""Live talk-through: sotto frames as they come from an encoder, sent to a K6 as one open-ended
stream message while the speaker is still talking (tools/k6_talk.py runs it on the bench).

The K6 side needs no change. A voice image with streaming (the sotto 1200 images' default, or a
`stream-on` control packet) plays a message through a ring of whole interleave groups while the
rest arrives, and a message whose header says total = 0xFFFF has no end: playback stops after
the last group heard, once the radio has heard nothing for 2 s (c/k6/sotto_pager.h).

What goes on air, all on one carrier, PHY F at 1800 baud, profile XL:

    lead (0x55)  [control: stream-on]  filler ...  group 0  filler ...  group 1  ...  tail

- A group is D packets (depth D: 4 by default) carrying F x D frames, packet k of group g
  frames g*F*D + s*D + k (s < F), as pager.Profile.pack lays out a full group. Its seq is
  g*D + k with no short-group bit and total 0xFFFF in every header.
- Each group goes out as soon as its last frame is in. Its packets are framed as a burst's
  first packets are (pager.Air with modem.k6's XL/1800 numbers): 16 bytes of 0x55 before the
  first, 12 + 12 before the second (the K6 notices the first sync late after a pause), 12 before
  the rest.
- Between groups the carrier carries filler: a fixed pseudo-random byte sequence with no run of
  0x55 and no sync word at any bit offset. That is what the radio's receiver hears between
  messages anyway (noise), whereas a long run of 0x55 made the K6 false-sync (docs/k6-pager.md).
  Every packet has at least 50 ms of filler (or the lead) before its preamble.
- While the next group is more than a packet away, the last group's packets go again one at a
  time (Sender: repeats). They keep the K6's 2 s end-of-stream timer from running out between
  a slow codec's groups, and give a lost packet a second chance.
- Group 0 waits `slack_s` more before it goes. The K6 starts playing once a group is in, and
  every later group then arrives slack_s ahead of when the K6 needs it, which covers the
  encoder's and the audio chain's jitter.

Real time: sotto 1200 at depth 4 is 80 frames (3.2 s of speech) a group; its 4 XL packets take
2.63 s on air at 1800 baud, 82 % of real time. `air_ratio` says the same for any choice, and
the sender refuses anything over 0.95 (sotto 2400, or depth 4 with parity).
SPDX-License-Identifier: MIT
"""
import numpy as np

from . import fsk, pager

OPEN = 0xFFFF                     # total frames in an open-ended stream's headers
BAUD = 1800                       # PHY F's boot rate on images after b1f0492
FIRST_PRE = 16                    # 0x55 bytes before a group's first packet
LATER_PRE = 12                    # before the rest at 1800 baud (modem.k6 build_audio)
LEAD_MS = 150.0
TAIL_MS = 300.0
SECOND_EXTRA_MS = 50.0
FILL_CHUNK = 9                    # filler bytes per step while waiting (40 ms at 1800 baud)
GAP_MS = 50.0                     # filler before any packet after another (the K6's re-arm)
END_S = 2.0                       # the K6 ends a stream after this long with no packet
MAX_RATIO = 0.95


def _has_sync(b, sync=pager.SYNC):
    bits = np.unpackbits(np.frombuffer(bytes(b), np.uint8))
    sb = np.unpackbits(np.frombuffer(sync, np.uint8))
    w = np.lib.stride_tricks.sliding_window_view(bits, len(sb))
    return bool(np.any(np.all(w == sb, axis=1)))


def make_filler(n=1024, seed=0x6B3E):
    """n pseudo-random bytes, the same every time: no 0x55 or 0xAA byte (so no preamble-like run
    longer than 15 bits) and no sync word anywhere, cycled included"""
    rng = np.random.default_rng(seed)
    while True:
        b = bytes(int(v) for v in rng.integers(0, 256, n) if v not in (0x55, 0xAA))[:n]
        if len(b) == n and not _has_sync(b + b[:8]):
            return b


FILLER = make_filler()


def _bytes_ms(ms, baud=BAUD):
    return int(np.ceil(ms / 1000 * baud / 8))


def group_bytes(payloads, baud=BAUD):
    """one group's packets as on air, with their preambles (no lead, no tail)"""
    p = bytes([pager.PREAMBLE_BYTE])
    out = b""
    for i, x in enumerate(payloads):
        n = FIRST_PRE if i == 0 else LATER_PRE + (_bytes_ms(SECOND_EXTRA_MS, baud) if i == 1 else 0)
        out += p * n + pager.SYNC + bytes(x)
    return out


def air_ratio(codec="1200", depth=4, payload="XL", parity=False, baud=BAUD):
    """air time of one group over the speech it carries: under 1 keeps up with a talker"""
    prof = pager.profile(payload, codec, depth, short_last=False, parity=parity)
    n = depth + (1 if parity else 0)
    air = len(group_bytes([bytes(prof.payload)] * n, baud)) * 8 / baud
    return air / (prof.group_frames * prof.frame_s)


class Packer:
    """frames in, one group's payloads out each time F x D frames are in"""

    def __init__(self, codec="1200", depth=4, msg_id=0, payload="XL", parity=False):
        if codec not in pager.CODECS:
            raise ValueError(f"codec {codec}: have {', '.join(pager.CODECS)}")
        self.prof = pager.profile(payload, codec, depth, short_last=False, parity=parity)
        self.msg_id = msg_id & 0xFF
        self.fpg = self.prof.group_frames
        # the K6 takes total = 0xFFFF's last group as a short one (seq bit 13), and seq has 13 bits
        self.max_groups = min(OPEN // self.fpg, 0x2000 // depth)
        self.g = 0
        self.buf = []

    @property
    def max_s(self):
        return self.max_groups * self.fpg * self.prof.frame_s

    def push(self, frame):
        """one 6-byte frame -> a group's payloads when it completes it, else None"""
        if len(frame) != pager.FRAME_BYTES:
            raise ValueError("a sotto frame is 6 bytes")
        self.buf.append(bytes(frame))
        return self._group() if len(self.buf) == self.fpg else None

    def flush(self):
        """the partial group, padded with zero frames (energy 0: as quiet as sotto gets), or None"""
        if not self.buf:
            return None
        self.buf += [bytes(6)] * (self.fpg - len(self.buf))
        return self._group()

    def _group(self):
        if self.g >= self.max_groups:
            raise ValueError(f"stream too long: {self.max_groups} groups ({self.max_s / 60:.1f} min)")
        prof, d = self.prof, self.prof.depth
        out, acc = [], None
        for k in range(d):
            seq = self.g * d + k
            body = b"".join(self.buf[s * d + k] for s in range(prof.slots))
            out.append(prof._seal(prof._header(prof.magic, self.msg_id, seq, OPEN) + body))
            if prof.parity:
                acc = body if acc is None else bytes(a ^ b for a, b in zip(acc, body))
        if prof.parity:
            out.append(prof._seal(prof._header(prof.magic | pager.PARITY, self.msg_id, self.g, OPEN) + acc))
        self.g += 1
        self.buf = []
        return out


class Modulator:
    """bytes -> PHY F audio, phase-continuous across calls (fsk.ffsk's tones and bit timing)"""

    def __init__(self, baud=BAUD, fs=fsk.FS, level=0.3, tones=None, invert=False, ramp_ms=5.0):
        self.baud, self.fs, self.level, self.invert = baud, fs, level, invert
        self.mark, self.space = tones or fsk.ffsk_tones(baud)
        self.nbits = self.nsamp = 0
        self.phase = 0.0
        self.ramp = int(fs * ramp_ms / 1000)

    @property
    def seconds(self):
        return self.nsamp / self.fs

    def __call__(self, data, last=False):
        bits = np.unpackbits(np.frombuffer(bytes(data), np.uint8)).astype(np.int8)
        if self.invert:
            bits = 1 - bits
        # sample k plays bit k * baud // fs: this call's samples are those whose bit it has
        end = -(-(self.nbits + len(bits)) * self.fs // self.baud)
        k = np.arange(self.nsamp, end)
        f = np.where(bits[k * self.baud // self.fs - self.nbits] > 0, self.mark, self.space)
        ph = self.phase + 2 * np.pi * np.cumsum(f) / self.fs
        x = self.level * np.sin(ph)
        if len(ph):
            self.phase = float(ph[-1] % (2 * np.pi))
        if self.nsamp == 0 and len(x) > self.ramp:      # fade in at key-up, out at the end
            x[:self.ramp] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(self.ramp) / self.ramp)
        if last and len(x) > self.ramp:
            x[-self.ramp:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(self.ramp) / self.ramp)
        self.nbits += len(bits)
        self.nsamp = end
        return x


class Sender:
    """The byte stream: lead, then groups as they complete, filler or repeats while waiting, then
    the tail.

    Call push() with frames as they come and step(now) whenever the transmitter wants more,
    with `now` the on-air time (s since key-up) the next byte would start at. step returns the
    next bytes to send, or None once finish() was called and everything is out. `log` collects
    (on-air start, end, what) for everything sent.

    Repeats: while the next group is more than a packet's air time (plus 0.1 s) away, the last
    group's packets go again, one at a time, round robin. The K6 takes them as duplicates (or
    fills a packet it lost), and each one restarts its 2 s end-of-stream timer, which a slow
    codec's long wait between groups would otherwise run out (sotto 600 or 400: 1.6 or 2.4 s of
    speech per XL packet). Every packet, repeat or group, has at least GAP_MS of filler before
    it, so the K6 has re-armed after the one before (pager.Air's second_extra_ms)."""

    def __init__(self, packer, baud=BAUD, slack_s=0.5, control=None, repeat=True):
        self.packer, self.baud, self.slack_s, self.repeat = packer, baud, slack_s, repeat
        self.queue = []                  # (time the group was ready, payloads)
        self.started = False
        self.finished = self.done = False
        self.control = control           # a control payload to send after the lead, or None
        self.fill_pos = 0
        self.log = []
        self.t = 0.0                     # on-air time of the next byte
        self.sent = 0                    # groups sent
        self.last = None                 # the last group sent, for repeats
        self.rep = 0
        self.last_push = 0.0
        self.gap_bytes = _bytes_ms(GAP_MS, baud)
        self.gap = self.gap_bytes        # filler bytes since the last packet
        self.packet_s = len(group_bytes([bytes(packer.prof.payload)], baud)) * 8 / baud
        self.ends = []

    def push(self, frame, now):
        self.last_push = now
        g = self.packer.push(frame)
        if g is not None:
            self.queue.append((now, g))

    def finish(self, now):
        g = self.packer.flush()
        if g is not None:
            self.queue.append((now, g))
        self.finished = True

    def _fill(self, n):
        out = bytearray()
        for _ in range(n):
            out.append(FILLER[self.fill_pos % len(FILLER)])
            self.fill_pos += 1
        self.gap += n
        return bytes(out)

    def _note(self, data, what):
        t1 = self.t + len(data) * 8 / self.baud
        self.log.append((self.t, t1, what))
        self.t = t1
        return data

    def next_ready(self):
        """when the next group should be in (its last frame), from the frames' pace so far"""
        left = self.packer.fpg - len(self.packer.buf)
        return self.last_push + left * self.packer.prof.frame_s

    def step(self, now=None):
        now = self.t if now is None else now
        self.t = max(self.t, now)
        if self.done:
            return None
        if not self.started:
            self.started = True
            data = bytes([pager.PREAMBLE_BYTE]) * _bytes_ms(LEAD_MS, self.baud)
            if self.control is not None:     # as pager.Air's first packet: lead, then a preamble
                data += group_bytes([self.control], self.baud)
                self.gap = 0
            return self._note(data, "lead")
        gap_ok = self.gap >= self.gap_bytes
        if self.queue:
            ready, g = self.queue[0]
            if self.t >= ready + (self.slack_s if self.sent == 0 else 0.0) and gap_ok:
                self.queue.pop(0)
                self.last, self.rep, self.gap = g, 0, 0
                self.sent += 1
                for i in range(len(g)):
                    self.ends.append(self.t + len(group_bytes(g[:i + 1], self.baud)) * 8 / self.baud)
                return self._note(group_bytes(g, self.baud), f"group {self.sent - 1}")
        elif self.finished:
            self.done = True
            return self._note(bytes([pager.PREAMBLE_BYTE]) * _bytes_ms(TAIL_MS, self.baud), "tail")
        elif (self.repeat and self.last is not None and gap_ok and not self.finished
              and self.next_ready() - self.t > self.packet_s + 0.1):
            p = self.last[self.rep % len(self.last)]
            self.rep += 1
            self.gap = 0
            self.ends.append(self.t + self.packet_s)
            return self._note(group_bytes([p], self.baud), f"repeat {self.sent - 1}")
        return self._note(self._fill(FILL_CHUNK), "fill")

    def groups(self):
        """[(on-air start, end)] of each group, in order"""
        return [(t0, t1) for t0, t1, w in self.log if w.startswith("group")]

    def packet_ends(self):
        """on-air end of every packet sent (group or repeat), in order: the K6's idle timer
        restarts at each"""
        return list(self.ends)


def k6_schedule(groups, packer, start_after=0.0):
    """When a streaming K6 plays each group, given the groups' on-air (start, end): it starts
    once group 0 is in, plays a slot per frame_s, and a group must be in before its first slot is
    due (or it waits, SP_PLAY_WAIT). -> (play start of each group, total wait in s)"""
    dur = packer.fpg * packer.prof.frame_s
    play, t, waited = [], None, 0.0
    for i, (_, end) in enumerate(groups):
        due = end + start_after if t is None else t
        if end > due:
            waited += end - due
            due = end
        play.append(due)
        t = due + dur
    return play, waited


def offline(frames, codec="1200", depth=4, msg_id=1, slack_s=0.5, enc_delay_s=0.0, level=0.3,
            control=None, fs=fsk.FS, parity=False, repeat=True):
    """The whole send for frames spoken in real time (frame i is ready at (i + 1) x frame_s +
    enc_delay_s after key-up), modulated: -> (audio, sender). The transmitter starts at key-up."""
    pk = Packer(codec, depth, msg_id, parity=parity)
    snd = Sender(pk, slack_s=slack_s, control=control, repeat=repeat)
    mod = Modulator(fs=fs, level=level)
    out, i, fdur = [], 0, pk.prof.frame_s
    while True:
        while i < len(frames) and (i + 1) * fdur + enc_delay_s <= snd.t:
            snd.push(frames[i], (i + 1) * fdur + enc_delay_s)
            i += 1
        if i == len(frames) and not snd.finished:
            snd.finish(snd.t)
        data = snd.step()
        if data is None:
            break
        out.append(mod(data, last=snd.done))
    return np.concatenate(out), snd
