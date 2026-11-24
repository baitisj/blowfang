"""Where SoapyFang's I/Q comes from. Every source gives complex64 baseband round a centre
frequency at a fixed rate, and knows how its full scale maps to dBm at the antenna:

- SyntheticSource: a made-up band (sim/uv21/board.py's Station list), FM-modulated and keyed in
  sample time, over a noise floor. The default: CI and the workbench need no SDR.
- SigmfSource: a SigMF recording (cf32_le, ci16_le or ci8). Generated test signals only in the
  repo; real off-air captures stay in Git-ignored folders.
- SoapySource: any SoapySDR device (driver=plutosdr, driver=rtlsdr, a SoapyRemote server, ...).
  Receive only: it never opens a transmit stream.
- PlutoIioSource: the ADALM-Pluto through libiio's command-line tools and tools/pluto_bench.py's
  set-up (its stored crystal correction, TX powered down), the way hamlet's bench already talks to
  it: no SoapySDR needed there. Receive only.

open_source("synthetic" | "sigmf:PATH" | "soapy:ARGS" | "pluto[:URI]") builds one from a string.

A live source may leave dbm_offset unset (None) with auto_floor_dbm set: the chip then sets the
offset from the first spectrum so that the median 25 kHz channel reads auto_floor_dbm. That is a
floor reference, not a calibration: RSSI is right relative to the band's floor.

Levels: dBm = 10 log10(mean |x|^2) + dbm_offset. For the synthetic band and the SigMF files it
writes, a full-scale complex tone (power 1.0) is REF_DBM. For an SDR the offset depends on its
gain and front end and is not calibrated here: pass --dbm-offset from a known signal.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import json
import math
import os
import subprocess

import numpy as np

REF_DBM = -10.0                 # synthetic and generated-SigMF full scale (power 1.0), dBm
NOISE_BW_HZ = 25_000            # the floor's dBm is measured in this bandwidth
VOICE_TONE_HZ, VOICE_DEV_HZ = 1000.0, 2500.0     # a "voice" over: a steady FM test tone
CW_TONE_HZ, CW_DEV_HZ = 800.0, 2500.0            # a CW ident: keyed carrier with an MCW tone


class IqSource:
    """the interface: read(n) the next n samples; tune(hz) moves the window if it can"""
    rate = 0.0
    center_hz = 0.0
    dbm_offset = REF_DBM
    auto_floor_dbm = None
    live = False                # True: samples arrive in wall time (an SDR), not on demand
    tunable = False

    def read(self, n):
        raise NotImplementedError

    def skip(self, n):
        """drop n samples (a jump in virtual time); sources that can do it cheaply override"""
        while n > 0:
            k = min(n, 1 << 18)
            self.read(k)
            n -= k

    def tune(self, hz):
        return False

    def close(self):
        pass

    def describe(self):
        return type(self).__name__


class SyntheticSource(IqSource):
    """the Station list as I/Q. Stations are keyed at 1 ms resolution in sample time, so a
    source read in step with a board's virtual clock agrees with Station.keyed(board.seconds)."""
    tunable = True

    def __init__(self, band=None, rate=1_200_000, center_hz=440_000_000, floor_dbm=-125.0, seed=1):
        if band is None:
            from sim.uv21.board import default_band
            band = default_band()
        self.band = band                    # shared, not copied: the workbench edits it live
        self.rate = float(rate)
        self.center_hz = float(center_hz)
        self.floor_dbm = floor_dbm
        self.rng = np.random.default_rng(seed)
        self.n = 0                          # samples given so far: t = n / rate
        self.carrier_ph = {}                # per station: carrier phase, modulation phase
        self.mod_ph = {}

    def describe(self):
        return f"synthetic band ({len(self.band)} stations, floor {self.floor_dbm:g} dBm)"

    def tune(self, hz):
        self.center_hz = float(hz)
        return True

    def skip(self, n):
        self.n += int(n)

    def read(self, n):
        n = int(n)
        out = np.empty(n, dtype=np.complex64)
        seg = max(1, int(self.rate / 1000))             # keying resolution: 1 ms
        for a in range(0, n, seg):
            b = min(n, a + seg)
            out[a:b] = self._segment(self.n + a, b - a)
        self.n += n
        return out

    def _segment(self, n0, k):
        t0 = n0 / self.rate
        sigma2 = 10 ** ((self.floor_dbm - REF_DBM) / 10) * self.rate / NOISE_BW_HZ
        x = (self.rng.standard_normal(k) + 1j * self.rng.standard_normal(k)) * math.sqrt(sigma2 / 2)
        t = np.arange(k) / self.rate
        for i, s in enumerate(self.band):
            off = s.hz - self.center_hz
            if abs(off) > 0.5 * self.rate or not s.keyed(t0):
                continue
            amp = math.sqrt(10 ** ((s.dbm - REF_DBM) / 10))
            if s.kind == "voice":
                tone, dev = VOICE_TONE_HZ, VOICE_DEV_HZ
            elif s.kind == "cw":
                tone, dev = CW_TONE_HZ, CW_DEV_HZ
            else:
                tone, dev = 0.0, 0.0
            mph = self.mod_ph.get(i, 0.0)
            # FM by a tone: instantaneous phase = 2 pi off t + (dev / tone) sin(2 pi tone t)
            ph = self.carrier_ph.get(i, 0.0) + 2 * np.pi * off * t
            if tone:
                ph = ph + (dev / tone) * np.sin(mph + 2 * np.pi * tone * t)
                self.mod_ph[i] = (mph + 2 * np.pi * tone * k / self.rate) % (2 * np.pi)
            self.carrier_ph[i] = (self.carrier_ph.get(i, 0.0) + 2 * np.pi * off * k / self.rate) % (2 * np.pi)
            x += amp * np.exp(1j * ph)
        return x


def write_sigmf(base, iq, rate, center_hz, dbm_offset=REF_DBM, description="generated test signal"):
    """base.sigmf-data (cf32_le) and base.sigmf-meta; returns the meta path"""
    np.asarray(iq, dtype=np.complex64).tofile(base + ".sigmf-data")
    meta = {"global": {"core:datatype": "cf32_le", "core:sample_rate": float(rate),
                       "core:version": "1.0.0", "core:description": description,
                       "soapyfang:dbm_offset": float(dbm_offset)},
            "captures": [{"core:sample_start": 0, "core:frequency": float(center_hz)}],
            "annotations": []}
    with open(base + ".sigmf-meta", "w") as f:
        json.dump(meta, f, indent=1)
    return base + ".sigmf-meta"


class SigmfSource(IqSource):
    """a SigMF recording, looped. It can't retune: frequencies outside it read as its floor."""
    DTYPES = {"cf32_le": (np.complex64, 1.0), "ci16_le": (np.int16, 1 / 32768.0),
              "ci8": (np.int8, 1 / 128.0)}

    def __init__(self, path, loop=True, dbm_offset=None):
        base = path[:-len(".sigmf-meta")] if path.endswith(".sigmf-meta") else \
            path[:-len(".sigmf-data")] if path.endswith(".sigmf-data") else path
        with open(base + ".sigmf-meta") as f:
            meta = json.load(f)
        g = meta["global"]
        dt = g["core:datatype"]
        if dt not in self.DTYPES:
            raise ValueError(f"unsupported SigMF datatype {dt} (cf32_le, ci16_le, ci8)")
        kind, scale = self.DTYPES[dt]
        raw = np.fromfile(base + ".sigmf-data", dtype=kind)
        self.iq = raw if kind is np.complex64 else \
            (raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) * scale
        self.iq = self.iq.astype(np.complex64)
        if not len(self.iq):
            raise ValueError(base + ".sigmf-data is empty")
        self.rate = float(g["core:sample_rate"])
        self.center_hz = float((meta.get("captures") or [{}])[0].get("core:frequency", 0.0))
        self.dbm_offset = float(dbm_offset if dbm_offset is not None else g.get("soapyfang:dbm_offset", REF_DBM))
        self.loop = loop
        self.pos = 0
        self.path = base

    def describe(self):
        return f"SigMF {os.path.basename(self.path)} ({len(self.iq) / self.rate:.1f} s at {self.rate / 1e6:g} MS/s)"

    def skip(self, n):
        self.pos = (self.pos + int(n)) % len(self.iq) if self.loop else min(len(self.iq), self.pos + int(n))

    def read(self, n):
        n = int(n)
        if self.loop:
            idx = (self.pos + np.arange(n)) % len(self.iq)
            self.pos = (self.pos + n) % len(self.iq)
            return self.iq[idx]
        out = np.zeros(n, dtype=np.complex64)
        k = max(0, min(n, len(self.iq) - self.pos))
        out[:k] = self.iq[self.pos:self.pos + k]
        self.pos += k
        return out


class SoapySource(IqSource):
    """a SoapySDR receiver. RX only: no transmit stream is ever set up. Its LO sits a quarter of
    the rate above the frequency asked for, so the LO leak and the DC spike stay off the channel
    the radio listens on. On close a Pluto's LO is parked at 2.4 GHz (docs/pluto-bench.md)."""
    live = True
    tunable = True

    def __init__(self, args="driver=rtlsdr", rate=2_400_000, center_hz=440_000_000, gain=None,
                 dbm_offset=None, channel=0, soapy=None):
        if soapy is None:
            import SoapySDR as soapy    # Arch: pacman -S soapysdr soapyplutosdr soapyrtlsdr
        self.soapy = soapy
        self.args = args
        self.ch = channel
        self.dev = soapy.Device(args)
        self.dev.setSampleRate(soapy.SOAPY_SDR_RX, channel, float(rate))
        self.rate = float(self.dev.getSampleRate(soapy.SOAPY_SDR_RX, channel))
        if gain is not None:
            self.dev.setGainMode(soapy.SOAPY_SDR_RX, channel, False)
            self.dev.setGain(soapy.SOAPY_SDR_RX, channel, float(gain))
        self.gain = gain
        # uncalibrated: full scale ~ REF_DBM less the gain, until set from a known signal
        self.dbm_offset = float(dbm_offset) if dbm_offset is not None else REF_DBM - float(gain or 0)
        self.center_hz = 0.0
        self.tune(center_hz)
        self.stream = self.dev.setupStream(soapy.SOAPY_SDR_RX, soapy.SOAPY_SDR_CF32, [channel])
        self.dev.activateStream(self.stream)
        self.overflows = 0

    def describe(self):
        g = f", gain {self.gain:g} dB" if self.gain is not None else ""
        return f"SoapySDR {self.args} ({self.rate / 1e6:g} MS/s{g})"

    def tune(self, hz):
        lo = float(hz) + 0.25 * self.rate
        self.dev.setFrequency(self.soapy.SOAPY_SDR_RX, self.ch, lo)
        self.center_hz = float(self.dev.getFrequency(self.soapy.SOAPY_SDR_RX, self.ch))
        return True

    def read(self, n):
        n = int(n)
        out = np.zeros(n, dtype=np.complex64)
        got, tries = 0, 0
        while got < n and tries < 50:
            buf = out[got:]
            sr = self.dev.readStream(self.stream, [buf], len(buf), timeoutUs=500_000)
            ret = sr.ret if hasattr(sr, "ret") else sr[0]
            if ret > 0:
                got += ret
            elif ret == getattr(self.soapy, "SOAPY_SDR_OVERFLOW", -4):
                self.overflows += 1
            else:
                tries += 1
        return out

    def close(self):
        try:
            self.dev.deactivateStream(self.stream)
            self.dev.closeStream(self.stream)
        finally:
            if "plutosdr" in self.args:
                self.dev.setFrequency(self.soapy.SOAPY_SDR_RX, self.ch, 2.4e9)


class PlutoIioSource(IqSource):
    """the Pluto over libiio (iio_readdev streaming int16 I/Q, iio_attr for the LO), set up by
    tools/pluto_bench.py: quiet first (TX LO powered down, DDS tones off), its stored crystal
    correction, manual RX gain, a 2 MHz analog filter. Receive only: nothing here writes the TX
    side except pluto_bench's quiet(). The LO sits a quarter of the rate above the frequency asked
    for (never on the channel listened to), and is parked at 2.4 GHz on close."""
    live = True
    tunable = True
    BUF = 1 << 15                       # samples per libiio buffer: ~14 ms at 2.4 MS/s

    def __init__(self, pb, uri=None, rate=2_400_000, center_hz=440_000_000, gain=50, dbm_offset=None,
                 auto_floor_dbm=-122.0):
        """pb: tools/pluto_bench.py (sim/soapyfang/pluto.py passes it; the tests pass a stand-in)"""
        self.pb = pb
        self.uri = uri or pb.URI
        if self.uri.startswith("usb:"):
            raise ValueError("stream over ip: (docs/pluto-bench.md): usb: streams lock the Pluto")
        self.rate = float(rate)
        self.gain = int(gain)
        self.dbm_offset = dbm_offset
        self.auto_floor_dbm = auto_floor_dbm if dbm_offset is None else None
        self.center_hz = float(center_hz) + 0.25 * self.rate
        pb.setup(int(self.rate), rx_hz=int(self.center_hz), rx_gain=self.gain, uri=self.uri,
                 rx_bw=pb.WIDE_BW)
        self.proc = subprocess.Popen(["iio_readdev", "-u", self.uri, "-b", str(self.BUF), pb.RX_DEV,
                                      "voltage0", "voltage1"], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

    def describe(self):
        return f"Pluto {self.uri} ({self.rate / 1e6:g} MS/s, RX gain {self.gain} dB, receive only)"

    def tune(self, hz):
        lo = int(round(float(hz) + 0.25 * self.rate))
        self.pb.put("out", self.pb.PHY, "altvoltage0", "frequency", lo, uri=self.uri)
        self.center_hz = float(lo)
        self.discard = int(0.07 * self.rate)       # libiio's queued buffers still hold the old LO's
        return True

    def read(self, n):
        n = int(n)
        while getattr(self, "discard", 0) > 0:
            k = min(self.discard, 1 << 16)
            if len(self.proc.stdout.read(4 * k)) < 4 * k:
                break
            self.discard -= k
        raw = self.proc.stdout.read(4 * n)
        if len(raw) < 4 * n:
            err = self.proc.stderr.read().decode(errors="replace").strip() if self.proc.poll() is not None else ""
            raise RuntimeError(f"iio_readdev stopped: {err or self.proc.returncode}")
        x = np.frombuffer(raw, np.int16).astype(np.float32)
        return ((x[0::2] + 1j * x[1::2]) / 2048.0).astype(np.complex64)

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
        finally:
            self.pb.park_rx(self.uri)


def open_source(spec="synthetic", band=None, rate=None, center_hz=440_000_000, gain=None,
                dbm_offset=None):
    """'synthetic', 'sigmf:PATH' or 'soapy:ARGS' (e.g. soapy:driver=rtlsdr,
    soapy:driver=plutosdr,uri=ip:192.168.2.1); 'pluto[:URI]' (libiio, hamlet's way) is
    sim/soapyfang/pluto.py's open_pluto, which brings in the bench tools"""
    if spec in ("synthetic", "", None):
        return SyntheticSource(band, rate=rate or 1_200_000, center_hz=center_hz)
    if spec.startswith("sigmf:"):
        return SigmfSource(spec[len("sigmf:"):], dbm_offset=dbm_offset)
    if spec.startswith("soapy:"):
        return SoapySource(spec[len("soapy:"):], rate=rate or 2_400_000, center_hz=center_hz,
                           gain=gain, dbm_offset=dbm_offset)
    raise ValueError(f"unknown source {spec!r}: synthetic, sigmf:PATH, soapy:ARGS or pluto[:URI]")
