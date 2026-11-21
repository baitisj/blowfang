#!/usr/bin/env python3
"""The Pluto as a sotto parrot (Jeff 2026-11-19: "Can we also build a test parrot?"): it listens
on one 70 cm channel, and each YSF or M17 sotto message it hears (the UV-380's image sending its
stored message on PTT, in its M17 or YSF sub-mode) goes back out on the same channel a moment
later in the same mode, so the UV-380 that sent it plays it on its own speaker. The words come
back as they were heard: the sotto bytes decoded and coded again (src AG7EW), not the audio, so
the reply is clean however the message arrived. pluto_bench.py's send keys it: AG7EW in MCW
before and after, the bench's PTT lock and owner check.

    python3 tools/pluto_bench.py parrot [--hz 441850000] [--gain -40] [--minutes 20]
        [--delay-s 2] [--rx-gain 20] [--max-replies 30] [--save DIR]

Receive: the RX LO sits RX_IF_HZ above the channel (its own leak and DC stay out of it), the
2.4 MS/s stream is mixed down and cut to 240 kS/s, and a burst is a run of 55 ms chunks whose
power in the channel (8 kHz low-pass) is 10 dB over the quiet floor; it ends after END_QUIET_S
below 6 dB. The burst (ident and message, one
key-up) is then decoded as modem.ysf and modem.m17 decode a capture, and the RX stops while the
reply goes out. A burst with no sotto in it (FM voice, a DN call) gets no reply.

cue() is the same ear used as a trigger (Jeff 2026-11-21, after the K6 micgain run mgn1b: "send
a trigger message then a call back that goes over the air for each stage"): pluto_bench.py cue
waits for one burst on --listen-hz between CUE_MIN_S and CUE_MAX_S long (a K6 module's CW ID,
its "ready" call, sent while its programming cable is out and the job can't read its log), then
sends the job's audio on --hz. One stage per job step, so the steps in between (the IC-7100's AF)
still run between the stages.

Standard bench rules: 70 cm only, gain <= -30 dB, ident on every send. SPDX-License-Identifier: MIT
"""
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

FS = 240_000                 # the burst's rate (pluto_bench IQ_FS_SAVE)
RX_IF_HZ = 50_000            # RX LO this far above the channel
CHANNEL_HZ = 8000.0          # the burst detector's low-pass: one 12.5 kHz channel, with the offset
START_DB = 10.0              # a chunk's channel power this far over the floor starts a burst
END_DB = 6.0                 # ... and the burst ends after END_QUIET_S under this
END_QUIET_S = 1.0
PRE_S = 0.2                  # kept from before the burst's first loud chunk
MAX_BURST_S = 40.0           # the UV-380 keys 5 s at most; anything longer is not ours
MIN_SOTTO_SF = 5             # superframes: less is noise, not a message
CUE_MIN_S = 1.5             # a cue burst (with PRE_S and END_QUIET_S): a K6 CW ID of 5 letters is ~4-5 s
CUE_MAX_S = 15.0
DEV = {"ysf": 5400.0, "m17": 4800.0}     # pluto_bench send --dev for a WAV_HALF wav (2.7 / 2.4 kHz)
HALF = 0.5


def mix_decimate(raw, state, rate, q, if_hz):
    """int16 IQ at rate -> complex64 at rate / q with the channel (if_hz below the LO) at 0 Hz:
    mixed, then summed in blocks of q (a boxcar: its first null at rate / q, plenty for one FM
    channel on a quiet bench). state carries the mixer phase and the samples left over."""
    x = np.frombuffer(raw, np.int16).astype(np.float32)
    if len(x):
        state["peak"] = max(state.get("peak", 0.0), float(np.max(np.abs(x))))
    iq = (x[0::2] + 1j * x[1::2]).astype(np.complex64) / 2048.0
    n0 = state.get("n", 0)
    ph = np.exp(2j * np.pi * if_hz * (n0 + np.arange(len(iq))) / rate).astype(np.complex64)
    state["n"] = (n0 + len(iq)) % rate
    iq = np.concatenate([state.get("left", np.zeros(0, np.complex64)), iq * ph])
    m = len(iq) // q * q
    state["left"] = iq[m:]
    return iq[:m].reshape(-1, q).mean(axis=1).astype(np.complex64)


class Bursts:
    """chunks in, whole bursts out: feed() returns a burst's IQ once it has ended, else None"""

    def __init__(self, fs=FS, chunk_s=0.0546):
        self.fs, self.floor, self.quiet = fs, None, []
        self.pre = int(PRE_S / chunk_s) + 1
        self.end_n = int(END_QUIET_S / chunk_s) + 1
        self.max_n = int(MAX_BURST_S / chunk_s)
        self.hist, self.burst, self.below = [], None, 0
        from scipy import signal
        self.sps = signal
        self.sos = signal.butter(4, CHANNEL_HZ, fs=fs, output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2), complex)

    def feed(self, y):
        # the power in the channel only: over the whole 240 kHz the noise is 15 dB more, and a
        # signal 20 dB over the noise in its channel never stood out (11-19 04:31-04:51Z on air)
        c, self.zi = self.sps.sosfilt(self.sos, y, zi=self.zi)
        p = float(np.mean(np.abs(c) ** 2)) + 1e-20
        if self.burst is None:
            self.hist = (self.hist + [y])[-self.pre:]
            if self.floor is not None and p > self.floor * 10 ** (START_DB / 10):
                self.burst, self.below = list(self.hist), 0
                return None
            self.quiet = (self.quiet + [p])[-40:]
            self.floor = float(np.median(self.quiet))
            return None
        self.burst.append(y)
        self.below = self.below + 1 if p < self.floor * 10 ** (END_DB / 10) else 0
        if self.below >= self.end_n or len(self.burst) >= self.max_n:
            out = np.concatenate(self.burst)
            self.burst, self.hist = None, []
            return out
        return None


def heard(iq, fs=FS):
    """a burst -> (mode, sotto bytes, src, report lines); mode None when no sotto was in it"""
    from modem import c6phy, m17, ysf
    off, on, disc = c6phy.capture_disc(iq, fs, bw_hz=12000.0)
    if disc is None:
        return None, b"", "", ["no signal"]
    lines = [f"burst {len(iq) / fs:.1f} s, carrier {off:+.0f} Hz"]
    yfr = ysf.demod(disc)
    s = ys0 = ysf.summarize(yfr)
    if s["sotto_n"] >= MIN_SOTTO_SF:
        src = "/".join(sorted(s["src"])) or "?"
        lines.append(f"YSF sotto {s['sotto_ok']}/{s['sotto_n']} superframes CRC good, from {src}")
        return "ysf", s["sotto"], src, lines
    fr = m17.demod(disc)
    s = m17.summarize(fr)
    if s["sotto_ok"] * 2 >= MIN_SOTTO_SF:
        src = s.get("src", "?")
        lines.append(f"M17 sotto {s['sotto_ok']}/{s['sotto_frames']} frames CRC good, "
                     f"{len(s['sotto']) // 6} superframes, from {src}")
        return "m17", s["sotto"], src, lines
    yf, ys = yfr, ys0
    kinds = {}
    for f in fr:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    d = disc[np.abs(disc) < 8000]
    lines.append(f"no sotto: YSF syncs {len(yf)}, FICH good {ys['fich_ok']}, kinds {ys['dt']}; "
                 f"M17 frames {kinds or 0}; deviation p50 {np.percentile(np.abs(d), 50):.0f} Hz, "
                 f"p99 {np.percentile(np.abs(d), 99):.0f} Hz")
    return None, b"", "", lines


def reply_wav(mode, sotto, fs=48000):
    """the same sotto bytes, coded again in the same mode -> (discriminator wav at WAV_HALF, dev)"""
    from modem import m17, ysf
    gap = np.zeros(fs // 4)
    if mode == "ysf":
        x = ysf.modulate(ysf.tx_sotto(sotto), fs=fs, outer_hz=HALF)
    else:
        x = m17.modulate(m17.tx_sotto(sotto), fs=fs, alpha=0.5, outer_hz=HALF)
    return np.concatenate([gap, x, gap]), DEV[mode]


class Listener:
    """iio_readdev on the RX, read in its own thread so the receive never stops while a burst is
    decoded (11-19 04:32Z: Jeff's FUNC send took 30 s to decode and his PTT send came in that
    gap). Whole bursts go to .bursts as (iq, ADC peak); stop() ends it and parks the RX LO."""

    def __init__(self, a, rate, q, t_end):
        import pluto_bench as pb
        self.pb, self.a, self.err = pb, a, None
        self.bursts = queue.Queue()
        pb.setup(rate, rx_hz=a.hz + RX_IF_HZ, rx_gain=a.rx_gain, uri=a.uri)
        left = max(1, int((t_end - time.time()) * rate))
        cmd = ["iio_readdev", "-u", a.uri or pb.URI, "-b", str(pb.CHUNK), "-s", str(left), pb.RX_DEV,
               "voltage0", "voltage1"]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.th = threading.Thread(target=self._read, args=(rate, q), daemon=True)
        self.th.start()
        print(f"listening {time.strftime('%H:%M:%S', time.gmtime())}Z", flush=True)

    def _read(self, rate, q):
        st, b = {}, Bursts()
        while True:
            raw = self.p.stdout.read(4 * self.pb.CHUNK)
            if not raw:
                break
            y = b.feed(mix_decimate(raw[:len(raw) // 4 * 4], st, rate, q, RX_IF_HZ))
            if y is not None:
                self.bursts.put((y, st.get("peak", 0.0)))
                st["peak"] = 0.0
        if self.p.poll() not in (None, 0, -9):
            self.err = self.p.stderr.read().decode(errors="replace").strip() or f"rc {self.p.returncode}"

    def alive(self):
        return self.th.is_alive()

    def stop(self):
        if self.p.poll() is None:
            self.p.kill()
            self.p.wait()
            self.th.join(timeout=5)
            self.pb.park_rx(self.a.uri)


def run(a):
    import pluto_bench as pb
    pb.check_tx(a.hz, 0, a.gain)
    rate, q = pb.RATE, pb.RATE // FS
    t_end = time.time() + a.minutes * 60
    replies = 0
    print(f"parrot on {a.hz} Hz until {time.strftime('%H:%M:%SZ', time.gmtime(t_end))}: YSF or M17 "
          f"sotto heard goes back {a.delay_s:g} s later, gain {a.gain:g} dB", flush=True)
    while time.time() < t_end and replies < a.max_replies:
        rx = Listener(a, rate, q, t_end)
        replied = False
        try:
            while not replied and time.time() < t_end and replies < a.max_replies:
                try:
                    burst, peak = rx.bursts.get(timeout=1.0)
                except queue.Empty:
                    if not rx.alive():
                        break
                    continue
                t = time.strftime('%H:%M:%S', time.gmtime())
                print(f"{t}Z heard: ADC peak {peak:.0f} of 2047 (rx gain {a.rx_gain} dB)", flush=True)
                mode, sotto, src, lines = heard(burst)          # the Listener keeps listening meanwhile
                for ln in lines:
                    print(f"{t}Z heard: {ln}", flush=True)
                if a.save:
                    os.makedirs(a.save, exist_ok=True)
                    burst.tofile(os.path.join(a.save, f"burst-{t.replace(':', '')}.cf32"))
                if mode is None:
                    continue
                x, dev = reply_wav(mode, sotto)
                rx.stop()                                      # no RX while the reply goes out
                time.sleep(a.delay_s)
                track = pb.audio_track(x, 48000, dev)
                pb.send(track, a.hz, 0, a.gain, rate, a.uri, True,
                        f"parrot {mode} {len(sotto) // 6} superframes")
                replies += 1
                replied = True
        finally:
            rx.stop()
        if not replied and not rx.alive() and time.time() < t_end and rx.err:
            print(f"receive stopped: {rx.err}", flush=True)
            break
    print(f"parrot done: {replies} replies", flush=True)


def cue(a, track, rate=None):
    """wait up to a.wait_s for one burst CUE_MIN_S..CUE_MAX_S long on a.listen_hz (shorter or
    longer ones are told and ignored), then after a.delay_s send track on a.hz. -> the burst's
    length in s; SystemExit when none came"""
    import types
    import pluto_bench as pb
    pb.check_tx(a.hz, a.offset_hz, a.gain)
    rate = rate or pb.RATE
    q = rate // FS
    t_end = time.time() + a.wait_s
    la = types.SimpleNamespace(hz=a.listen_hz, rx_gain=a.rx_gain, uri=a.uri)
    got = None
    print(f"cue: listening on {a.listen_hz} Hz up to {a.wait_s:.0f} s for a burst of {CUE_MIN_S:g}-{CUE_MAX_S:g} s,"
          f" then {a.what} on {a.hz} Hz", flush=True)
    while got is None and time.time() < t_end:
        rx = Listener(la, rate, q, t_end)
        try:
            while time.time() < t_end:
                try:
                    burst, peak = rx.bursts.get(timeout=1.0)
                except queue.Empty:
                    if not rx.alive():
                        break
                    continue
                secs = len(burst) / FS
                ok = CUE_MIN_S <= secs <= CUE_MAX_S
                print(f"{time.strftime('%H:%M:%S', time.gmtime())}Z cue: heard a burst of {secs:.1f} s, ADC peak "
                      f"{peak:.0f} of 2047{'' if ok else ' (not a cue: ignored)'}", flush=True)
                if ok:
                    got = secs
                    break
        finally:
            rx.stop()
        if got is None and rx.err:
            raise SystemExit(f"cue: receive stopped: {rx.err}")
    if got is None:
        raise SystemExit(f"cue: no burst on {a.listen_hz} Hz in {a.wait_s:.0f} s")
    time.sleep(a.delay_s)
    pb.send(track, a.hz, a.offset_hz, a.gain, rate, a.uri, not getattr(a, "rf_off", False), a.what)
    return got
