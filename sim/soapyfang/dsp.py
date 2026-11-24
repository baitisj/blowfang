"""Small numpy-only DSP pieces for the chip model: windowed-sinc FIR design and an overlap-save
FIR with decimation that keeps its state across blocks. SPDX-License-Identifier: MIT"""
from __future__ import annotations

import numpy as np


def lowpass(cutoff_hz, rate_hz, taps):
    """linear-phase low-pass, unity gain at DC (Hamming-windowed sinc, taps odd)"""
    taps |= 1
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff_hz / rate_hz * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


def highpass(cutoff_hz, rate_hz, taps):
    """spectral inversion of lowpass(): unity gain at rate/2"""
    h = -lowpass(cutoff_hz, rate_hz, taps)
    h[len(h) // 2] += 1.0
    return h


class FirDecim:
    """y = (x * taps)[::decim], block by block, as if run on one long input (overlap-save)"""

    def __init__(self, taps, decim=1, complex_in=True):
        self.h = np.asarray(taps, dtype=np.complex64 if complex_in else np.float32)
        self.decim = int(decim)
        self.tail = np.zeros(len(self.h) - 1, dtype=self.h.dtype)
        self.phase = 0                      # input samples to skip before the next kept output
        self._H = {}

    def process(self, x):
        x = np.asarray(x, dtype=self.h.dtype)
        if not len(x):
            return x[:0]
        m = len(self.h)
        buf = np.concatenate((self.tail, x))
        nfft = 1 << int(np.ceil(np.log2(len(buf))))
        H = self._H.get(nfft)
        if H is None:
            H = self._H[nfft] = np.fft.fft(self.h, nfft)
        y = np.fft.ifft(np.fft.fft(buf, nfft) * H)[m - 1:len(buf)]
        if self.h.dtype != np.complex64:
            y = y.real
        self.tail = buf[len(buf) - (m - 1):] if m > 1 else buf[:0]
        out = y[self.phase::self.decim]
        self.phase = (self.phase - len(x)) % self.decim
        return out.astype(self.h.dtype)
