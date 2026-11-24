"""A behavioural BK4819-family receiver (the FD6818 / BK4829 of the UV-K6 and the UV-21 Pro) fed by
I/Q: what the firmware reads back over the 3-wire bus, computed from an IqSource.

    REG_67 RSSI     channel power in dBm (FFT bins inside the channel), on the board's scale:
                    raw = (dBm + rssi_offset) * 2, bits 8:0 (174 for the K6 and the UV-21 port
                    today; the classic BK4819 rule is 160, and the UV-21 offset is unmeasured)
    REG_65 noise    out-of-band discriminator energy above 5 kHz, 0 (clean) to 127 (no signal)
    REG_63 glitch   discriminator clicks (phase steps over ~92 degrees) per 10 ms, 0-255
    REG_0C bit 1    squelch open: RSSI against REG_78 (open <15:8>, close <7:0>, in raw/2) and
                    noise against REG_4F (close <14:8>, open <6:0>), with hysteresis; with neither
                    written, open while noise <= 60
    speaker         FM discriminator, 3 kHz low-pass, 8 kHz int16 chunks (pcm_after)

None of this is the chip's own arithmetic, which isn't published: filter shapes, AGC and the
squelch maths are modelled, to be fitted against the real chip (the Pluto sends a known level,
the K6 reports its registers, the same I/Q goes through this model). Two clocks: virtual (the
board's emulated time pulls samples, for tests and files) or live (an SDR's reader thread pushes
them in wall time). SPDX-License-Identifier: MIT"""
from __future__ import annotations

import collections
import math
import threading

import numpy as np

from .dsp import FirDecim, highpass, lowpass

AUDIO_HZ = 48_000               # discriminator rate (approximately: the input rate over an integer)
PCM_HZ = 8_000                  # speaker chunks (approximately)
PCM_FULL_HZ = 6_000.0           # deviation that gives full-scale PCM


class ChipModel:
    def __init__(self, source, rssi_offset=174.0, channel_bw_hz=25_000):
        self.src = source
        self.rate = source.rate
        self.rssi_offset = rssi_offset
        self.bw = float(channel_bw_hz)
        self.fft_n = int(min(16384, max(512, 2 ** round(math.log2(self.rate * 0.0017)))))
        self.win = np.hanning(self.fft_n).astype(np.float32)
        self.norm = self.fft_n * float(np.sum(self.win ** 2))
        self.bins = np.fft.fftshift(np.fft.fftfreq(self.fft_n, 1 / self.rate))
        self.lock = threading.RLock()
        self.t = 0.0                            # source time consumed (virtual mode), s
        self.tuned_hz = None
        self._reset_window()
        # the tuned channel's audio chain
        self.d1 = max(1, round(self.rate / AUDIO_HZ))
        self.fa = self.rate / self.d1
        self.d2 = max(1, round(self.fa / PCM_HZ))
        self.pcm_rate = self.fa / self.d2
        self._reset_audio()
        self.pcm = collections.deque(maxlen=200)    # (seq, int16 chunk, noise squelch open)
        self.pcm_seq = 0
        self.noise_hz = 1e4                      # HF discriminator rms, Hz (smoothed)
        self.glitch = 0.0                        # clicks per 10 ms (smoothed)
        self.sq = False
        self.retunes = 0
        self._thread = None
        self._run = False

    # ---- windows and retuning ----
    def _reset_window(self):
        self.pending = np.zeros(0, dtype=np.complex64)
        self.spec = None

    def _reset_audio(self):
        cut = 8_000 if self.bw > 15_000 else 4_500
        self.chan = FirDecim(lowpass(cut, self.rate, 6 * self.d1 + 1), self.d1)
        self.hf = FirDecim(highpass(5_000, self.fa, 63), 1, complex_in=False)
        self.af = FirDecim(lowpass(3_000, self.fa, 8 * self.d2 + 1), self.d2, complex_in=False)
        self.nco = 0.0
        self.prev = np.zeros(1, dtype=np.complex64)

    def in_window(self, hz, bw=None):
        bw = self.bw if bw is None else bw
        return abs(hz - self.src.center_hz) + bw / 2 <= 0.45 * self.rate

    def _retune(self, hz):
        if self.src.tune(hz):
            self.retunes += 1
            self._reset_window()
            self._reset_audio()

    def tune(self, hz):
        """the frequency the firmware set (REG_38/39 x 10)"""
        with self.lock:
            if hz == self.tuned_hz:
                return
            self.tuned_hz = hz
            if hz and not self.in_window(hz) and self.src.tunable:
                self._retune(hz)

    # ---- time ----
    def advance(self, seconds):
        """virtual mode: pull the source up to `seconds` (a long jump skips, keeping 50 ms)"""
        if self.src.live:
            return
        with self.lock:
            n = int(round((seconds - self.t) * self.rate))
            if n <= 0:
                return
            keep = int(0.05 * self.rate)
            if n > keep + int(self.rate):
                self.src.skip(n - keep)
                self.t += (n - keep) / self.rate
                n = keep
                self._reset_window()
            while n > 0:
                k = min(n, 1 << 16)
                self._ingest(self.src.read(k))
                self.t += k / self.rate
                n -= k

    def start(self):
        """live mode: a thread reads the SDR as fast as it delivers"""
        if not self.src.live or self._thread:
            return
        self._run = True

        def loop():
            while self._run:
                x = self.src.read(self.fft_n * 4)
                with self.lock:
                    self._ingest(x)
        self._thread = threading.Thread(target=loop, name="soapyfang-rx", daemon=True)
        self._thread.start()

    def stop(self, close_source=True):
        self._run = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if close_source:
            self.src.close()

    # ---- DSP ----
    def _ingest(self, x):
        buf = np.concatenate((self.pending, x)) if len(self.pending) else x
        n = self.fft_n
        while len(buf) >= n:
            blk = buf[:n]
            p = np.fft.fftshift(np.abs(np.fft.fft(blk * self.win)) ** 2) / self.norm
            self.spec = p if self.spec is None else 0.5 * self.spec + 0.5 * p
            buf = buf[n:]
        self.pending = buf.copy()
        if self.tuned_hz:
            self._audio(x)

    def _audio(self, x):
        off = self.tuned_hz - self.src.center_hz
        if abs(off) > 0.45 * self.rate:
            return
        w = 2 * np.pi * off / self.rate
        ph = self.nco - w * np.arange(len(x))
        self.nco = float((self.nco - w * len(x)) % (2 * np.pi))
        y = self.chan.process(x * np.exp(1j * ph).astype(np.complex64))
        if not len(y):
            return
        z = np.concatenate((self.prev, y))
        self.prev = y[-1:]
        d = np.angle(z[1:] * np.conj(z[:-1])).astype(np.float32)
        hz = d * (self.fa / (2 * np.pi))
        hf = self.hf.process(hz)
        k = min(1.0, len(d) / (0.01 * self.fa))          # about a 10 ms time constant
        self.noise_hz += k * (float(np.sqrt(np.mean(hf ** 2))) - self.noise_hz)
        clicks = float(np.count_nonzero(np.abs(d) > 1.6)) * (0.01 * self.fa) / len(d)
        self.glitch += k * (clicks - self.glitch)
        a = self.af.process(hz)
        if len(a):
            self.pcm_seq += 1
            pcm = np.clip(a / PCM_FULL_HZ * 32767, -32768, 32767).astype(np.int16)
            self.pcm.append((self.pcm_seq, pcm, self.noise_raw() <= 60))

    def _fresh(self):
        """a spectrum to read: virtual mode reads one block now if a retune emptied it"""
        if self.spec is None and not self.src.live:
            self._ingest(self.src.read(self.fft_n))
            self.t += self.fft_n / self.rate

    # ---- what the registers report ----
    def rssi_dbm(self, hz=None, bw=None):
        with self.lock:
            hz = self.tuned_hz if hz is None else hz
            bw = self.bw if bw is None else bw
            if hz is None:
                return -160.0
            if not self.in_window(hz, bw) and self.src.tunable:
                self._retune(hz)
            self._fresh()
            if self.spec is None:
                return -160.0
            binw = self.rate / self.fft_n
            if self.in_window(hz, bw):
                off = hz - self.src.center_hz
                p = float(np.sum(self.spec[np.abs(self.bins - off) <= bw / 2]))
            else:                       # a recording that doesn't reach: its floor
                p = float(np.median(self.spec)) * bw / binw
            return 10 * math.log10(p + 1e-30) + self.src.dbm_offset

    def rssi_raw(self, hz=None):
        return max(0, min(0x1FF, int(round((self.rssi_dbm(hz) + self.rssi_offset) * 2))))

    def noise_raw(self):
        r = 2.5 * 20 * math.log10(max(self.noise_hz, 20.0) / 20.0)
        return max(0, min(127, int(round(r))))

    def glitch_raw(self):
        return max(0, min(255, int(round(self.glitch))))

    def squelch(self, rssi_raw, regs):
        r78, r4f = regs.get(0x78), regs.get(0x4F)
        noise = self.noise_raw()
        if not r78 and not r4f:
            self.sq = noise <= 60
            return self.sq
        lvl = rssi_raw >> 1
        open_ok = (not r78 or lvl >= (r78 >> 8)) and (not r4f or noise <= (r4f & 0x7F))
        close = (r78 and lvl < (r78 & 0xFF)) or (r4f and noise > ((r4f >> 8) & 0x7F))
        if open_ok:
            self.sq = True
        elif close:
            self.sq = False
        return self.sq

    def read_reg(self, a, regs):
        """the value for register a, or None where the model has nothing to say"""
        if a == 0x67:
            return self.rssi_raw()
        if a == 0x65:
            return self.noise_raw()
        if a == 0x63:
            return self.glitch_raw()
        if a == 0x0C:
            v = regs.get(0x0C, 0) & ~0x0002
            return v | (0x0002 if self.squelch(self.rssi_raw(), regs) else 0)
        return None

    def pcm_after(self, seq, gated=False):
        """(last seq, int16 samples newer than seq) for the speaker; gated: only chunks with the
        noise squelch open (silence in their place otherwise)"""
        with self.lock:
            chunks = [c if (o or not gated) else np.zeros_like(c) for s, c, o in self.pcm if s > seq]
            return self.pcm_seq, (np.concatenate(chunks) if chunks else np.zeros(0, np.int16))
