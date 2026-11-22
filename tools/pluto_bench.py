#!/usr/bin/env python3
"""The ADALM-Pluto on the hamlet bench: the standard instrument for 2 m and 70 cm HT tests (Jeff,
2026-11-16), so the IC-7100 stays free for other work. A flat FM transmitter (no audio path: no
pre-emphasis, no low cut, no 3 kHz top) and a flat receiver, driven through libiio's command-line
tools (iio_attr, iio_writedev, iio_readdev; libiio 0.26 on hamlet): numpy and scipy only.
docs/pluto-bench.md has the bench notes.

  python3 tools/pluto_bench.py info                 the Pluto's state, range and stored calibration
  python3 tools/pluto_bench.py quiet                TX LO down, DDS test tones off (the Pluto boots
                                                    with tones keyed at 2.45 GHz: run after a replug),
                                                    RX LO parked at 2.4 GHz (it leaks out of the RX port)
  python3 tools/pluto_bench.py send WAV             any audio as FM on 440.850 (or --hz in 430-450),
         [--hz F] [--dev 3000] [--gain -60]         full scale = --dev Hz peak, AG7EW in MCW first
         [--preemph] [--rf-off]                     and last; --preemph for voice (750 us)
         [--peak HZ]                                or the wav's own peak = HZ (before --preemph):
                                                    modem.k6 wavs are half scale, so --dev 3700
                                                    gives them only ~1850 Hz; --peak 3000 = what
                                                    k6_upload sends (PLUTO_PEAK_HZ)
  python3 tools/pluto_bench.py steps WAV            the same audio at each TX gain in turn (a
         --gains -70,-73,... [--preemph] [--out J]  sensitivity ladder; times to J for a report)
  python3 tools/pluto_bench.py cue WAV [--hz F]     wait for one burst on --listen-hz (a K6 module's
         [--listen-hz F] [--wait-s 900]             CW ID: its over-the-air "ready" call), then send
         [--delay-s 1] [--min-s S] [--max-s S]      WAV as send does (tools/pluto_parrot.py cue);
         [--min-peak P] [send's options]            --min-s 3.5 --min-peak 30: only a K6 call
  python3 tools/pluto_bench.py rx OUT [--hz F]      capture, TX down: OUT.cf32 = complex64 at 240 kHz
         [--seconds 10] [--gain 30]                 (beside it OUT.cf32.json: frequency, rate, xo),
                                                    OUT.wav = the FM discriminator, flat, 48 kHz,
                                                    5 kHz deviation = full scale
  python3 tools/pluto_bench.py measure CAP.cf32     carrier offset (Hz, ppm), peak deviation, level
  python3 tools/pluto_bench.py cal CAP.cf32         from a capture of a carrier on a known frequency
         [--ref-hz F] [--save]                      (the IC-7100's CW ID: its TCXO): the Pluto's
                                                    crystal (xo_correction); --save stores it and
                                                    every later command applies it
  python3 tools/pluto_bench.py fsk24 [--rounds N]   fsk24 run 3 (docs/k6-fsk24.md)
  python3 tools/pluto_bench.py fsk24 --sweep        the sensitivity sweep for fsk24rx_sweep: timed
         --follow LOG --gain -70                    from the K6 logger's "fsk24 start" line, 0 to
                                                    -44 dB in 4 dB steps (digital), the signal
                                                    250 kHz above the LO (its leak off channel)

Receive works anywhere the Pluto tunes (this one is an ad9364: 70 MHz to 6 GHz). Transmit keeps
the project's bench rules: only 430.010-449.990 MHz (440.850, the K6 bench frequency, by default;
anything outside 70 cm needs Jeff's OK and a change here), at most gain -30 dB (-60, the default,
is about -55 dBm into the antenna), only under the bench's PTT lock and owner check
(tools/k6_bench.py), AG7EW in MCW (800 Hz at 2.5 kHz deviation) before and after every
transmission, and the TX LO powered down before the stream starts and after it ends (and on any
error), so there is never a bare carrier outside a transmission. --rf-off streams with the TX LO
left down: a rehearsal that checks USB keeps up, no RF at all.

The Pluto's crystal is +-25 ppm (+-11 kHz at 440 MHz). cal sets the AD9361's xo_correction, which
moves TX and RX together; the stored value lives in $BLOWFANG_WORK/state/pluto-cal.json.
SPDX-License-Identifier: MIT
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

# ip: (the Pluto's USB network link), not usb:: over usb: iio_writedev claims the USB interface
# and the iio_attr that powers the TX LO up and down can't open a second context (11-16 run 3)
URI = os.environ.get("PLUTO_URI", "ip:192.168.2.1")
HZ = 440_850_000
TX_LO_MIN, TX_LO_MAX = 430_010_000, 449_990_000     # the bench authorization: 70 cm only
OFFSET_MAX = 20_000
IF_MAX = 500_000            # the sweep's IF: the signal this far from the LO at most
SWEEP_IF = 250_000          # fsk24 --sweep: LO leak and image 250 and 500 kHz below the channel
GAIN_MAX = -30.0
GAIN_MIN = -89.75
RATE = 2_400_000            # above 2.083 MS/s: no FIR needed in the AD9361
FS_TRACK = 48_000
IQ_FS_SAVE = 240_000
WAV_FULL_DEV = 5000.0       # rx OUT.wav: this deviation = full scale
AMP = 0.5 * 32767           # the DAC takes the top 12 of 16 bits; half scale
WIDE_BW = 2_000_000          # RX analog filter for several channels (rx --channels, chan)
CHUNK = 1 << 17             # samples a buffer (about 55 ms at 2.4 MS/s)
TX_DEV = "cf-ad9361-dds-core-lpc"
RX_DEV = "cf-ad9361-lpc"
PHY = "ad9361-phy"
ID_TEXT = "AG7EW"
ID_DEV = 2500.0
XO_NOMINAL = 40_000_000


# ---- libiio's command-line tools ----
def _iio(args, uri=None, check=True):
    cmd = ["iio_attr", "-u", uri or URI] + [str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"{' '.join(cmd)}: {r.stderr.strip() or r.returncode}")
    return r.stdout


def parse_value(out):
    """iio_attr's read line (libiio 0.26 prints nothing for a read under -q) -> the value"""
    m = re.findall(r"value '([^']*)'", out)
    return m[-1] if m else out.strip()


def get(where, *path, uri=None):
    """where: "in"/"out" (a channel attribute: path = dev, channel, attr) or "dev" (dev, attr)"""
    flag = {"in": ["-c", "-i"], "out": ["-c", "-o"], "dev": ["-d"]}[where]
    return parse_value(_iio(flag + list(path), uri=uri, check=False))


def put(where, *path_value, uri=None, check=True):
    """write with -q; -i or -o always given, so a write never lands on both directions' channel
    of the same name (voltage0 is RX and TX on ad9361-phy)"""
    flag = {"in": ["-c", "-i"], "out": ["-c", "-o"], "dev": ["-d"]}[where]
    _iio(["-q"] + flag + list(path_value), uri=uri, check=check)


# ---- the stored calibration ----
def cal_path():
    import k6_bench as kb
    return os.path.join(kb.work(), "state", "pluto-cal.json")


def load_cal():
    try:
        with open(cal_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def xo_from(offset_hz, ref_hz, xo_used):
    """a carrier on ref_hz read offset_hz high with the AD9361 told its crystal is xo_used: the
    real LO was offset_hz low, so the crystal is slow by offset_hz / ref_hz -> the corrected xo"""
    return int(round(xo_used * (1.0 - offset_hz / ref_hz)))


def tx_power(on, uri=None):
    """the TX LO on (0) or powered down (1): no RF at all while it is down"""
    put("out", PHY, "altvoltage1", "powerdown", 0 if on else 1, uri=uri)


RX_PARK_HZ = 2_400_000_000   # the RX LO leaks out of the RX port: park it far from the bands under test


def park_rx(uri=None):
    put("out", PHY, "altvoltage0", "frequency", RX_PARK_HZ, uri=uri, check=False)


def quiet(uri=None):
    tx_power(False, uri)
    park_rx(uri)
    for ch in ("altvoltage0", "altvoltage1", "altvoltage2", "altvoltage3"):
        put("out", TX_DEV, ch, "raw", 0, uri=uri, check=False)   # the DDS test tones off


def setup(rate=RATE, tx_hz=None, rx_hz=None, gain=None, rx_gain=None, uri=None, rx_bw=200000):
    """rx_bw: the RX analog filter, Hz (200 kHz for one channel; rx --channels and chan open it to
    WIDE_BW so channels 750 kHz off the LO are not cut)"""
    quiet(uri)
    c = load_cal()
    if c:
        put("dev", PHY, "xo_correction", int(c["xo"]), uri=uri)
    put("out", PHY, "voltage0", "sampling_frequency", int(rate), uri=uri)
    put("out", PHY, "voltage0", "rf_bandwidth", 200000, uri=uri)
    put("in", PHY, "voltage0", "rf_bandwidth", int(rx_bw), uri=uri)
    if tx_hz is not None:
        put("out", PHY, "altvoltage1", "frequency", int(tx_hz), uri=uri)
        put("out", PHY, "voltage0", "hardwaregain", f"{gain:.2f}", uri=uri)
    if rx_hz is not None:
        put("out", PHY, "altvoltage0", "frequency", int(rx_hz), uri=uri)   # RX_LO is an output channel
        put("in", PHY, "voltage0", "gain_control_mode", "manual", uri=uri)
        put("in", PHY, "voltage0", "hardwaregain", int(rx_gain if rx_gain is not None else 30), uri=uri)


def check_tx(hz, offset, gain, if_hz=0):
    """-> the TX LO. if_hz: the signal sent that far above the LO (digitally), so the LO's leak
    and the IQ image (the LO minus if_hz) sit off the channel; all three stay in the band"""
    if abs(offset) > OFFSET_MAX:
        raise SystemExit(f"refusing: offset {offset} Hz is more than {OFFSET_MAX}")
    if abs(if_hz) > IF_MAX:
        raise SystemExit(f"refusing: IF {if_hz} Hz is more than {IF_MAX}")
    lo = hz - offset - if_hz
    image = lo - if_hz
    if not all(TX_LO_MIN <= f <= TX_LO_MAX for f in (hz, lo, image)):
        raise SystemExit(f"refusing: transmit only in {TX_LO_MIN}..{TX_LO_MAX} Hz (asked {hz}, LO {lo},"
                         f" image {image}); outside 70 cm needs Jeff's OK")
    if not (GAIN_MIN <= gain <= GAIN_MAX):
        raise SystemExit(f"refusing: gain {gain} dB outside {GAIN_MIN}..{GAIN_MAX}")
    return lo


# ---- what goes on air ----
def id_track(fs=FS_TRACK, text=ID_TEXT):
    from modem import k6
    a = k6.cw(text, fs, wpm=20, tone=800.0, level=1.0)
    return np.concatenate([np.zeros(int(0.2 * fs)), a * ID_DEV, np.zeros(int(0.2 * fs))])


def audio_track(x, fs, dev, preemph=False):
    """audio (full scale +-1) -> instantaneous frequency in Hz at FS_TRACK, +-dev at full scale.
    preemph: 750 us (6 dB an octave from 212 Hz), shelved at 3 kHz, the level kept at 1 kHz"""
    from scipy import signal as sps
    x = np.asarray(x, float)
    if fs != FS_TRACK:
        g = np.gcd(int(fs), FS_TRACK)
        x = sps.resample_poly(x, FS_TRACK // g, int(fs) // g)
    if preemph:
        w1, w2 = 1.0 / 750e-6, 2 * np.pi * 3000.0
        b, a = sps.bilinear([1.0 / w1, 1.0], [1.0 / w2, 1.0], FS_TRACK)
        _, h = sps.freqz(b, a, [1000.0], fs=FS_TRACK)
        x = sps.lfilter(b, a, x) / abs(h[0])
    return np.clip(x, -1.0, 1.0) * dev


def full_scale_dev(x, dev, peak=None):
    """the deviation (Hz) for full scale: --dev as given, or with --peak the one that puts the
    wav's own peak there (modem.k6 audio is 0.5 level: --dev 3700 sent F/1800 test packets at
    ~1850 Hz, under the 3000 Hz an upload uses, and the K6 dropped bits or never synced,
    2026-11-17, docs/k6-fsk24.md)"""
    if peak is None:
        return dev
    m = float(np.max(np.abs(np.asarray(x, float)))) if len(x) else 0.0
    if m <= 0:
        raise SystemExit("--peak: the wav is silent")
    return peak / m


def iq_chunks(track, fs_track=FS_TRACK, rate=RATE, chunk=CHUNK, level=None, if_hz=0.0):
    """instantaneous frequency (Hz at fs_track) -> int16 interleaved IQ bytes, chunk by chunk,
    phase continuous (the track interpolated linearly to rate). level: the amplitude (1 = AMP)
    a track sample, held; if_hz: added to the frequency (the signal that far above the LO)"""
    n_out = int(len(track) * rate / fs_track)
    t_src = np.arange(len(track)) / fs_track
    phase = 0.0
    for a in range(0, n_out, chunk):
        b = min(n_out, a + chunk)
        f = np.interp(np.arange(a, b) / rate, t_src, track) + if_hz
        ph = phase + 2 * np.pi * np.cumsum(f) / rate
        phase = float(ph[-1] % (2 * np.pi))
        amp = AMP
        if level is not None:
            amp = AMP * np.asarray(level)[np.minimum((np.arange(a, b) * fs_track) // rate, len(level) - 1)]
        iq = np.empty(2 * (b - a), np.int16)
        iq[0::2] = np.round(amp * np.cos(ph))
        iq[1::2] = np.round(amp * np.sin(ph))
        yield iq.tobytes()


def stream(track, rate=RATE, uri=None, rf=True, what="pluto", level=None, if_hz=0.0, hz=None):
    """the track through iio_writedev, under the bench's PTT lock and owner check; the TX LO up
    only while it plays. The caller has put the ID in the track; hz is the signal's frequency for
    the owner check (None: unknown, refused while anyone else holds any claim).
    -> (seconds of signal, took)"""
    import k6_bench as kb
    secs = len(track) / FS_TRACK
    if (uri or URI).startswith("usb:"):
        raise SystemExit("refusing to stream over usb: (iio_writedev holds the interface, so the TX LO"
                         " could not be powered down from here): use ip:192.168.2.1")
    kb.check_owner(hz=hz)
    with kb.ptt_lock(), kb.unkey_on_signal():     # a SIGTERM still powers the TX LO down
        cmd = ["iio_writedev", "-u", uri or URI, "-b", str(CHUNK), TX_DEV, "voltage0", "voltage1"]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        t0 = time.time()
        try:
            gen = iq_chunks(track, rate=rate, level=level, if_hz=if_hz)
            p.stdin.write(next(gen))          # first buffer queued before RF comes up
            if rf:
                tx_power(True, uri)
            for buf in gen:
                p.stdin.write(buf)
            p.stdin.close()
            p.wait(timeout=10)
        finally:
            tx_power(False, uri)
            if p.poll() is None:
                p.kill()
            t1 = time.time()
            if rf:
                # its own MCW ID first and last: logged, but kept out of the IC-7100 keeper's state
                kb.note_self_ided(t0, t1, f"{what} (Pluto IQ, {secs:.1f} s)", ID_TEXT)
    return secs, t1 - t0


def send(track, hz, offset, gain, rate, uri, rf, what):
    """ID + track + ID on hz, with the bench checks; prints the start and end in UTC"""
    lo = check_tx(hz, offset, gain)
    ident = id_track()
    full = np.concatenate([ident, track, ident])
    import k6_bench as kb
    with kb.ptt_lock():                     # set-up too: no retuning under another Pluto user's stream
        setup(rate, tx_hz=lo, gain=gain, uri=uri)
        print(f"{what}: {len(full) / FS_TRACK:.1f} s on {hz} Hz (LO {lo}), gain {gain} dB, rate {rate}, "
              f"{'RF OFF (rehearsal)' if not rf else 'RF on'}")
        print(f"start {time.strftime('%H:%M:%S', time.gmtime())}Z", flush=True)
        secs, took = stream(full, rate, uri, rf=rf, what=what, hz=hz)
    print(f"end {time.strftime('%H:%M:%S', time.gmtime())}Z: {secs:.1f} s of signal in {took:.1f} s"
          f" ({'kept up' if took < secs + 2.0 else 'SLOW: the link did not keep up, gaps on air'})")


# ---- receive and measure ----
class Decimator:
    """int16 IQ at rate -> complex64 at rate / q, chunk by chunk (a 100 kHz low-pass first)"""

    def __init__(self, rate, q):
        from scipy import signal as sps
        self.sps, self.q, self.k = sps, q, 0
        self.h = sps.firwin(161, 100e3, fs=rate)
        self.zi = np.zeros(len(self.h) - 1, complex)

    def __call__(self, raw):
        x = np.frombuffer(raw, np.int16).astype(np.float32)
        iq = (x[0::2] + 1j * x[1::2]) / 2048.0
        y, self.zi = self.sps.lfilter(self.h, 1.0, iq, zi=self.zi)
        out = y[(-self.k) % self.q::self.q]
        self.k = (self.k + len(iq)) % self.q
        return out.astype(np.complex64)


def capture(seconds, hz=HZ, gain=30, uri=None, rate=RATE, sink=None, raw=False, rx_bw=200000):
    """-> complex64 at IQ_FS_SAVE, the meta dict; with sink, each part goes to sink(part) instead
    and nothing is kept (y is then empty): long captures without holding the IQ in memory.
    raw=True hands the sink the int16 IQ at rate as read, not decimated (rx --channels)"""
    import k6_bench as kb
    del kb                                  # imported for its error if the tools dir is missing
    setup(rate, rx_hz=hz, rx_gain=gain, uri=uri, rx_bw=rx_bw)
    xo = get("dev", PHY, "xo_correction", uri=uri)
    n = int(seconds * rate)
    cmd = ["iio_readdev", "-u", uri or URI, "-b", str(CHUNK), "-s", str(n), RX_DEV, "voltage0", "voltage1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    dec, parts, t0 = Decimator(rate, rate // IQ_FS_SAVE), [], time.time()
    try:
        while True:
            chunk = p.stdout.read(4 * CHUNK)
            if not chunk:
                break
            if raw:
                sink(chunk[: len(chunk) // 4 * 4])
                parts.append(np.zeros(0, np.complex64))
                continue
            part = dec(chunk[: len(chunk) // 4 * 4])
            if sink is None:
                parts.append(part)
            else:
                sink(part)
                parts.append(part[:0])
        err = p.stderr.read().decode(errors="replace").strip()
    finally:
        if p.poll() is None:
            p.kill()
        park_rx(uri)                        # its LO sat on the K6's channel after a capture (11-16)
    if p.wait() or not parts:
        raise SystemExit(f"iio_readdev failed: {err or p.returncode}"
                         + (" (stuck RX buffer: unplug and replug the Pluto)" if "busy" in err else ""))
    y = np.concatenate(parts)
    meta = {"hz": int(hz), "fs": IQ_FS_SAVE, "rate": int(rate), "gain": gain,
            "xo": int(float(xo)) if xo.replace(".", "", 1).isdigit() else None,
            "t0": t0, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))}
    return y, meta


def measure(iq, fs=IQ_FS_SAVE):
    """-> dict: carrier offset Hz (the mean of the discriminator where the signal is 20 dB over
    the capture's floor), peak deviation (99.5th percentile about it), the share of the capture
    with a signal, and its level and the floor in dBFS"""
    from scipy import signal as sps
    h = sps.firwin(255, 8000, fs=fs)
    x = sps.lfilter(h, 1.0, iq)
    p = np.abs(x) ** 2
    floor = max(float(np.percentile(p, 10)), 1e-20)
    on = p > 100 * floor
    out = {"share": float(np.mean(on)), "floor_dbfs": 10 * np.log10(floor),
           "offset": None, "dev": None, "level_dbfs": None}
    d = np.angle(x[1:] * np.conj(x[:-1])) * fs / (2 * np.pi)
    d = d[on[1:]]
    if len(d) < fs * 0.2:
        return out
    c = float(np.mean(d))
    out.update(offset=c, dev=float(np.percentile(np.abs(d - c), 99.5)),
               level_dbfs=10 * np.log10(float(np.mean(p[on]))))
    return out


def offset_of(iq, fs=IQ_FS_SAVE):
    m = measure(iq, fs)
    return m["offset"], m["dev"], m["share"]


def read_cap(path):
    iq = np.fromfile(path, np.complex64)
    try:
        with open(path + ".json") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {"hz": HZ, "fs": IQ_FS_SAVE, "xo": None}
    return iq, meta


# ---- commands ----
def cmd_info(a):
    rows = [("out", PHY, "altvoltage1", "frequency"), ("out", PHY, "altvoltage1", "powerdown"),
            ("out", PHY, "altvoltage1", "frequency_available"),
            ("out", PHY, "voltage0", "sampling_frequency"), ("out", PHY, "voltage0", "hardwaregain"),
            ("out", PHY, "altvoltage0", "frequency"), ("in", PHY, "voltage0", "gain_control_mode"),
            ("in", PHY, "voltage0", "hardwaregain"), ("dev", PHY, "xo_correction")]
    rows += [("out", TX_DEV, f"altvoltage{i}", "raw") for i in range(4)]
    for r in rows:
        print(" ".join(r[1:]), "=", get(r[0], *r[1:], uri=a.uri))
    print("stored calibration:", load_cal() or "none (see cal)")


def cmd_quiet(a):
    quiet(a.uri)
    print("TX LO powerdown =", get("out", PHY, "altvoltage1", "powerdown", uri=a.uri))


def wav_track(a):
    """the wav a.wav as an FM track (a.dev, a.peak, a.preemph), its scaling printed"""
    from scipy.io import wavfile
    fs, x = wavfile.read(a.wav)
    x = np.asarray(x, float)
    if x.ndim > 1:
        x = x[:, 0]
    if np.issubdtype(np.asarray(x).dtype, np.floating) and np.max(np.abs(x)) > 1.5:
        x = x / 32768.0                     # integer PCM read as float
    dev = full_scale_dev(x, a.dev, a.peak)
    print(f"send: wav peak {np.max(np.abs(x)):.3f} of full scale -> {np.max(np.abs(x)) * dev:.0f} Hz "
          f"before pre-emphasis", flush=True)
    return audio_track(x, fs, dev, a.preemph)


def cmd_send(a):
    track = wav_track(a)
    send(track, a.hz, a.offset_hz, a.gain, a.rate, a.uri, not a.rf_off,
         f"send {os.path.basename(a.wav)}")


SWEEP_WAIT_S = 30.0          # fsk24rx_sweep's WAIT_MS: slot 0 starts this long after "fsk24 start"
SWEEP_EARLY_S = 0.3          # start writing this much before the plan (iio_writedev's queue)


def follow(path, needle, timeout_s, poll=0.05):
    """wait for a new line holding needle at the end of a log another process writes ->
    (the time it was seen, the line)"""
    t_end = time.time() + timeout_s
    while not os.path.exists(path):
        if time.time() > t_end:
            raise SystemExit(f"no log {path} after {timeout_s:.0f} s")
        time.sleep(poll)
    with open(path, errors="replace") as f:
        f.seek(0, os.SEEK_END)
        buf = ""
        while time.time() < t_end:
            got = f.read()
            if not got:
                time.sleep(poll)
                continue
            buf += got
            *done, buf = buf.split("\n")
            for ln in done:
                if needle in ln:
                    return time.time(), ln
    raise SystemExit(f"no '{needle}' in {path} after {timeout_s:.0f} s")


def cmd_fsk24_sweep(a):
    """the sensitivity sweep: each of the module's slots gets its rate's packets at its step's
    level, on the module's grid (slot 0 SWEEP_WAIT_S after its "fsk24 start" line)"""
    from modem import fsk24
    track, level, segs = fsk24.sweep_track(FS_TRACK)
    lo = check_tx(HZ, a.offset_hz, a.gain, a.if_hz)
    ident = id_track()
    one = np.ones(len(ident))
    full = np.concatenate([ident, track, ident])
    lev = np.concatenate([one, level, one])
    id_s = len(ident) / FS_TRACK
    print(f"fsk24 sweep: {len(segs)} slots of {fsk24.SWEEP_SLOT_S} s, steps {fsk24.SWEEP_STEPS[0]} to "
          f"{fsk24.SWEEP_STEPS[-1]} dB, {sum(s[4] for s in segs)} packets, {len(full) / FS_TRACK:.1f} s on"
          f" {HZ} Hz (LO {lo}, IF {a.if_hz:+.0f} Hz), gain {a.gain} dB,"
          f" {'RF OFF (rehearsal)' if a.rf_off else 'RF on'}", flush=True)
    if a.follow:
        print(f"waiting for 'fsk24 start' in {a.follow} ...", flush=True)
        seen, ln = follow(a.follow, "say fsk24 start", a.wait_s)
        print(f"{time.strftime('%H:%M:%S', time.gmtime(seen))}Z {ln.strip()}", flush=True)
        s0 = seen + SWEEP_WAIT_S
    elif a.t0 == "now":
        s0 = time.time() + id_s + 1.0
    else:
        s0 = float(a.t0) + SWEEP_WAIT_S
    go = s0 - id_s - SWEEP_EARLY_S
    import k6_bench as kb
    # the PTT lock from set-up to the end: another Pluto user (k6_upload --via pluto, whose last
    # send can still be on air after the K6 starts the module) neither retunes it under us nor
    # keys over us; it waits for that send to end
    with kb.ptt_lock():
        setup(a.rate, tx_hz=lo, gain=a.gain, uri=a.uri)
        if go < time.time():
            raise SystemExit(f"too late: slot 0 was due {time.time() - s0:.1f} s ago")
        print(f"slot 0 due {time.strftime('%H:%M:%S', time.gmtime(s0))}Z; keying at "
              f"{time.strftime('%H:%M:%S', time.gmtime(go))}Z", flush=True)
        time.sleep(go - time.time())
        print(f"start {time.strftime('%H:%M:%S', time.gmtime())}Z", flush=True)
        secs, took = stream(full, a.rate, a.uri, rf=not a.rf_off, what="fsk24 sweep", level=lev,
                            if_hz=a.if_hz, hz=HZ)
    print(f"end {time.strftime('%H:%M:%S', time.gmtime())}Z: {secs:.1f} s of signal in {took:.1f} s"
          f" ({'kept up' if took < secs + 2.0 else 'SLOW: the link did not keep up, the grid slipped'})")


def cmd_fsk24(a):
    from modem import fsk24
    if a.sweep:
        return cmd_fsk24_sweep(a)
    track, segs = fsk24.freq_track(a.rounds)
    print(f"fsk24 run 3: {a.rounds} rounds, {len(segs) * fsk24.PER_BLOCK} packets")
    send(track, HZ, a.offset_hz, a.gain, a.rate, a.uri, not a.rf_off, "fsk24 run 3")


def cmd_steps(a):
    """the same audio at each TX gain in turn, each its own transmission (ID first and last), a
    pause between; the times go to --out (json) for a report against the receiver's log"""
    from scipy.io import wavfile
    gains = [float(g) for g in a.gains.split(",")]
    for g in gains:
        check_tx(a.hz, a.offset_hz, g)
    fs, x = wavfile.read(a.wav)
    x = np.asarray(x, float)
    if x.ndim > 1:
        x = x[:, 0]
    if np.max(np.abs(x)) > 1.5:
        x = x / 32768.0
    dev = full_scale_dev(x, a.dev, a.peak)
    track = audio_track(x, fs, dev, a.preemph)
    print(f"steps: {len(gains)} sends of {os.path.basename(a.wav)} ({len(x) / fs:.1f} s), peak deviation "
          f"{np.max(np.abs(x)) * dev:.0f} Hz before pre-emphasis, {np.max(np.abs(track)):.0f} Hz sent, "
          f"gains {a.gains} dB", flush=True)
    out = {"wav": os.path.abspath(a.wav), "hz": a.hz, "dev": dev, "peak": a.peak, "preemph": a.preemph,
           "steps": []}
    for g in gains:
        t0 = time.time()
        send(track, a.hz, a.offset_hz, g, a.rate, a.uri, not a.rf_off, f"steps gain {g:g}")
        out["steps"].append({"gain": g, "t0": t0, "t1": time.time()})
        if a.out:
            with open(a.out, "w") as f:
                json.dump(out, f, indent=1)
        time.sleep(a.pause)


class AudioSink:
    """complex64 parts at IQ_FS_SAVE -> FM discriminator audio at fs, written to a WAV as it comes
    (no IQ kept), and the carrier level each 0.1 s (dBFS): rx --wav-only"""

    def __init__(self, path, fs=8000):
        import soundfile as sf
        from scipy import signal as sps
        self.sps, self.q = sps, IQ_FS_SAVE // fs
        self.wav = sf.SoundFile(path, "w", samplerate=fs, channels=1, subtype="PCM_16")
        self.hc = sps.firwin(255, 8000, fs=IQ_FS_SAVE)           # the channel
        self.zc = np.zeros(len(self.hc) - 1, complex)
        self.ha = sps.firwin(255, 0.45 * fs, fs=IQ_FS_SAVE)       # audio, before the decimation
        self.za = np.zeros(len(self.ha) - 1)
        self.last, self.k, self.acc, self.levels = 1 + 0j, 0, [], []

    def __call__(self, part):
        if not len(part):
            return
        x, self.zc = self.sps.lfilter(self.hc, 1.0, part.astype(complex), zi=self.zc)
        self.acc.extend(np.abs(x) ** 2)
        n10 = IQ_FS_SAVE // 10
        while len(self.acc) >= n10:
            self.levels.append(round(10 * np.log10(np.mean(self.acc[:n10]) + 1e-20), 1))
            del self.acc[:n10]
        d = np.angle(x * np.conj(np.concatenate([[self.last], x[:-1]]))) * IQ_FS_SAVE / (2 * np.pi)
        self.last = x[-1]
        a, self.za = self.sps.lfilter(self.ha, 1.0, d, zi=self.za)
        out = a[(-self.k) % self.q::self.q]
        self.k = (self.k + len(a)) % self.q
        self.wav.write(np.clip(out / WAV_FULL_DEV, -1, 1).astype(np.float32))

    def close(self):
        self.wav.close()


class StreamFir:
    """a decimating FIR over a stream: x in parts, h applied, every q-th output kept; the parts
    can be any size and the state carries over (only the kept outputs are computed)"""

    def __init__(self, h, q, dtype=np.complex64):
        self.h, self.q = np.asarray(h[::-1], np.float32), q
        self.tail, self.ph = np.zeros(len(h) - 1, dtype), 0

    def __call__(self, x):
        from numpy.lib.stride_tricks import sliding_window_view
        z = np.concatenate([self.tail, x.astype(self.tail.dtype)])
        L = len(self.h)
        w = sliding_window_view(z, L)[self.ph::self.q]
        y = w @ self.h if len(w) else np.zeros(0, z.dtype)
        self.ph += len(w) * self.q - (len(z) - (L - 1))
        self.tail = z[len(z) - (L - 1):]
        return y


class ChannelSink:
    """int16 IQ at rate, centred on lo_hz -> one FM channel at hz: mixed down, filtered to 32 kS/s,
    discriminated, 8 kHz audio to a WAV and the carrier level each 0.1 s (rx --channels: several of
    these share one capture). Only the kept samples of each filter are computed, so six channels
    run in real time on hamlet"""
    FS1 = 32_000

    def __init__(self, path, hz, lo_hz, rate=RATE, fs=8000):
        from math import gcd

        import soundfile as sf
        from scipy import signal as sps
        self.hz, self.off, self.rate = int(hz), int(hz) - int(lo_hz), rate
        if rate % self.FS1 or self.FS1 % fs:
            raise SystemExit(f"rx --channels: rate {rate} must be a multiple of {self.FS1}")
        if abs(self.off) > rate / 2 - 40e3:
            raise SystemExit(f"rx --channels: {hz} is {self.off / 1e3:+.1f} kHz from the LO {lo_hz}, "
                             f"outside +-{(rate / 2 - 40e3) / 1e3:.0f} kHz")
        self.mix = StreamFir(sps.firwin(601, 8000, fs=rate), rate // self.FS1)    # the channel, +-8 kHz
        self.aud = StreamFir(sps.firwin(127, 0.45 * fs, fs=self.FS1), self.FS1 // fs, np.float32)
        p = rate // gcd(rate, abs(self.off) or rate)                            # the LO table's period
        self.tab = np.exp(-2j * np.pi * self.off * np.arange(p) / rate).astype(np.complex64)
        self.n, self.last, self.acc, self.levels = 0, np.complex64(1), np.zeros(0, np.float32), []
        self.wav = sf.SoundFile(path, "w", samplerate=fs, channels=1, subtype="PCM_16")

    def __call__(self, iq):
        """iq: complex64 at rate, scaled as the Decimator's (full scale 1.0)"""
        lo = self.tab[(self.n + np.arange(len(iq))) % len(self.tab)]
        self.n = (self.n + len(iq)) % len(self.tab)
        x = self.mix(iq * lo)
        if not len(x):
            return
        self.acc = np.concatenate([self.acc, (np.abs(x) ** 2).astype(np.float32)])
        n10 = self.FS1 // 10
        k = len(self.acc) // n10 * n10
        for v in self.acc[:k].reshape(-1, n10).mean(axis=1):
            self.levels.append(round(10 * np.log10(float(v) + 1e-20), 1))
        self.acc = self.acc[k:]
        d = np.angle(x * np.conj(np.concatenate([[self.last], x[:-1]]))) * self.FS1 / (2 * np.pi)
        self.last = x[-1]
        self.wav.write(np.clip(self.aud(d.astype(np.float32)) / WAV_FULL_DEV, -1, 1))

    def close(self):
        self.wav.close()


class MultiSink:
    """one raw capture -> a ChannelSink each"""

    def __init__(self, sinks):
        self.sinks = sinks

    def __call__(self, chunk):
        x = np.frombuffer(chunk, np.int16).astype(np.float32)
        iq = ((x[0::2] + 1j * x[1::2]) / 2048.0).astype(np.complex64)
        for s in self.sinks:
            s(iq)

    def close(self):
        for s in self.sinks:
            s.close()


def channel_path(out, hz):
    """rx --channels: OUT.wav -> OUT_442.2500.wav for each channel"""
    return f"{out[:-4]}_{hz / 1e6:.4f}.wav"


def rx_channels(a):
    """rx OUT.wav --channels HZ,HZ,...: the LO at --hz, one WAV and levels json per channel"""
    hzs = [int(float(v)) for v in a.channels.split(",") if v.strip()]
    if not a.out.endswith(".wav"):
        raise SystemExit("rx --channels: OUT must end in .wav")
    for h in hzs:
        if abs(h - a.hz) < 15_000:
            raise SystemExit(f"rx --channels: {h} is within 15 kHz of the LO {a.hz} (its DC spur):"
                             " move --hz between channels")
    sinks = [ChannelSink(channel_path(a.out, h), h, a.hz, RATE, a.wav_rate) for h in hzs]
    ms = MultiSink(sinks)
    t0 = time.time()
    try:
        _, meta = capture(a.seconds, a.hz, a.gain, a.uri, sink=ms, raw=True, rx_bw=WIDE_BW)
    finally:
        ms.close()
    took = time.time() - t0
    for s in sinks:
        lv = np.array(s.levels) if s.levels else np.zeros(1)
        floor = float(np.percentile(lv, 10))
        m = dict(meta, hz=s.hz, lo_hz=int(a.hz), wav_rate=a.wav_rate, levels_dbfs_100ms=s.levels,
                 floor_dbfs=floor)
        with open(channel_path(a.out, s.hz) + ".json", "w") as f:
            json.dump(m, f)
        print(f"{channel_path(a.out, s.hz)}: {len(s.levels) / 10:.1f} s on {s.hz} Hz"
              f" ({s.off / 1e3:+.1f} kHz from the LO), floor {floor:.1f} dBFS, carrier (floor + 20 dB)"
              f" on {float(np.mean(lv > floor + 20)):.0%}")
    print(f"{len(sinks)} channels in {took:.1f} s for {a.seconds:.0f} s of capture"
          f" ({'kept up' if took < a.seconds + 10 else 'SLOW: samples may have been dropped'})")


def cmd_rx(a):
    if a.channels:
        return rx_channels(a)
    if a.wav_only:
        if not a.out.endswith(".wav"):
            raise SystemExit("rx --wav-only: OUT must end in .wav")
        snk = AudioSink(a.out, a.wav_rate)
        try:
            _, meta = capture(a.seconds, a.hz, a.gain, a.uri, sink=snk)
        finally:
            snk.close()
        lv = np.array(snk.levels) if snk.levels else np.zeros(1)
        floor = float(np.percentile(lv, 10))
        meta.update(wav_rate=a.wav_rate, levels_dbfs_100ms=snk.levels, floor_dbfs=floor)
        with open(a.out + ".json", "w") as f:
            json.dump(meta, f)
        print(f"{a.out}: {len(snk.levels) / 10:.1f} s on {a.hz} Hz, FM audio at {a.wav_rate} Hz only"
              f" (no IQ kept), floor {floor:.1f} dBFS, carrier (floor + 20 dB) on"
              f" {float(np.mean(lv > floor + 20)):.0%}")
        return
    y, meta = capture(a.seconds, a.hz, a.gain, a.uri)
    base = a.out
    if base.endswith(".wav"):
        from modem import k6, sdr
        d = sdr.fm_demodulate(y, IQ_FS_SAVE, FS_TRACK, bw_hz=8000.0)
        k6._write(base, d / WAV_FULL_DEV, FS_TRACK)
        base = base[:-4] + ".cf32"
    y.tofile(base)
    with open(base + ".json", "w") as f:
        json.dump(meta, f)
    m = measure(y)
    print(f"{a.out}: {len(y) / IQ_FS_SAVE:.1f} s on {a.hz} Hz at {IQ_FS_SAVE} complex64 ({base}),"
          f" floor {m['floor_dbfs']:.1f} dBFS, signal on {m['share']:.0%}")


def cmd_cue(a):
    import pluto_parrot
    a.what = f"cue {os.path.basename(a.wav)}"
    pluto_parrot.cue(a, wav_track(a), a.rate)


def cmd_parrot(a):
    import pluto_parrot
    pluto_parrot.run(a)


def cmd_measure(a):
    iq, meta = read_cap(a.cap)
    m = measure(iq, meta.get("fs", IQ_FS_SAVE))
    if m["offset"] is None:
        print(f"no signal 20 dB over the floor ({m['floor_dbfs']:.1f} dBFS; on {m['share']:.1%} of the capture)")
        return
    hz = meta.get("hz", HZ)
    print(f"carrier {m['offset']:+.0f} Hz from {hz} ({m['offset'] / hz * 1e6:+.2f} ppm), peak deviation "
          f"{m['dev']:.0f} Hz, level {m['level_dbfs']:.1f} dBFS over a floor of {m['floor_dbfs']:.1f}, "
          f"signal on {m['share']:.0%} of the capture")


def cmd_cal(a):
    iq, meta = read_cap(a.cap)
    m = measure(iq, meta.get("fs", IQ_FS_SAVE))
    if m["offset"] is None:
        raise SystemExit("no signal in the capture to calibrate on")
    ref = a.ref_hz or meta.get("hz", HZ)
    xo_used = meta.get("xo") or XO_NOMINAL
    off = m["offset"] + (meta.get("hz", HZ) - ref)
    xo = xo_from(off, ref, xo_used)
    print(f"carrier read {off:+.0f} Hz from {ref} ({off / ref * 1e6:+.2f} ppm) with xo {xo_used}: "
          f"crystal {xo} Hz ({(xo / XO_NOMINAL - 1) * 1e6:+.2f} ppm from nominal)")
    if a.save:
        c = {"xo": xo, "measured_utc": meta.get("utc"), "ref_hz": ref, "offset_hz": round(off, 1),
             "xo_used": xo_used, "cap": os.path.abspath(a.cap)}
        os.makedirs(os.path.dirname(cal_path()), exist_ok=True)
        with open(cal_path(), "w") as f:
            json.dump(c, f, indent=1)
        print(f"saved {cal_path()}: every later command sets xo_correction {xo}")


# ---- chan: the channelizer on the Pluto's own ARM (tools/pluto_chan/) ----
CHAN_ALLOWED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pluto_chan", "allowed.sha256")
CHAN_REMOTE = "/tmp/pluto_chan"
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=10", "-o", "PubkeyAuthentication=no", "-o", "NumberOfPasswordPrompts=1"]


def chan_allowed(path):
    """the binary's sha256 must be listed in tools/pluto_chan/allowed.sha256 (on main): a queued job
    names the file, but only a reviewed build goes onto the Pluto, which could otherwise key its TX"""
    import hashlib
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    ok = set()
    if os.path.exists(CHAN_ALLOWED):
        for line in open(CHAN_ALLOWED):
            if line.strip() and not line.startswith("#"):
                ok.add(line.split()[0].lower())
    if h not in ok:
        raise SystemExit(f"chan: {os.path.basename(path)} (sha256 {h[:16]}...) is not in {CHAN_ALLOWED}")
    return h


def ssh_cmd(uri, remote):
    """ssh to the Pluto over its USB Ethernet (stock firmware: root / analog, from $PLUTO_PASS)"""
    import tempfile
    host = (uri or URI).split(":", 1)[1] if (uri or URI).startswith("ip:") else None
    if not host:
        raise SystemExit("chan: needs an ip: URI (the Pluto's USB Ethernet)")
    fd, ask = tempfile.mkstemp(prefix="pluto_ask_", suffix=".sh")
    os.write(fd, b"#!/bin/sh\necho \"${PLUTO_PASS:-analog}\"\n")
    os.close(fd)
    os.chmod(ask, 0o700)
    env = dict(os.environ, SSH_ASKPASS=ask, SSH_ASKPASS_REQUIRE="force", DISPLAY=os.environ.get("DISPLAY", ":0"))
    return ["ssh"] + SSH_OPTS + [f"root@{host}", remote], env, ask


def ssh_run(uri, remote, stdin=None, timeout=60):
    cmd, env, ask = ssh_cmd(uri, remote)
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, env=env, timeout=timeout, check=False,
                              stdin=None if stdin is not None else subprocess.DEVNULL)
    finally:
        os.unlink(ask)


def cmd_chan(a):
    """the channelizer on the Pluto: setup from here (as rx), the IQ read and split on the Pluto,
    only gated HamFLAC overs and levels back over the link"""
    import pluto_chan.pluto_chan as pc
    if a.probe:
        r = ssh_run(a.uri, "uname -a; grep -m1 -i 'model name\\|Processor' /proc/cpuinfo; nproc; free;"
                           " which iio_readdev; iio_readdev -h 2>&1 | head -3; cat /etc/os-release 2>/dev/null"
                           " | head -2; df -h /tmp | tail -1")
        print(r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace"))
        return
    if not a.bin or not a.out:
        raise SystemExit("chan: BIN and OUTDIR (or --probe)")
    hzs = [int(float(v)) for v in a.channels.split(",") if v.strip()]
    for h in hzs:
        if abs(h - a.hz) < 15_000 or abs(h - a.hz) > RATE / 2 - 40e3:
            raise SystemExit(f"chan: {h} must be 15 kHz to {(RATE / 2 - 40e3) / 1e3:.0f} kHz from the LO {a.hz}")
    sha = chan_allowed(a.bin)
    r = ssh_run(a.uri, f"cat > {CHAN_REMOTE} && chmod 755 {CHAN_REMOTE} && sha256sum {CHAN_REMOTE}",
                stdin=open(a.bin, "rb").read())
    if r.returncode or sha not in r.stdout.decode():
        raise SystemExit(f"chan: upload failed: {(r.stdout + r.stderr).decode(errors='replace').strip()}")
    print(f"chan: {os.path.basename(a.bin)} ({sha[:16]}...) in the Pluto's RAM at {CHAN_REMOTE}")
    os.makedirs(a.out, exist_ok=True)
    setup(RATE, rx_hz=a.hz, rx_gain=a.gain, uri=a.uri, rx_bw=WIDE_BW)
    n = int(a.seconds * RATE)
    remote = (f"iio_readdev -b {CHUNK} -s {n} {RX_DEV} voltage0 voltage1 | {CHAN_REMOTE} -f {a.hz} {RATE} "
              + " ".join(str(h) for h in hzs))
    cmd, env, ask = ssh_cmd(a.uri, remote)
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=env)
    try:
        res = pc.write_stream(p.stdout, a.out, hzs, t0)
        err = p.stderr.read().decode(errors="replace").strip()
        rc = p.wait(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
        os.unlink(ask)
        park_rx(a.uri)
        ssh_run(a.uri, f"rm -f {CHAN_REMOTE}", timeout=20)
    took = time.time() - t0
    meta = {"lo_hz": int(a.hz), "rate": RATE, "gain": a.gain, "rx_bw": WIDE_BW, "bin_sha256": sha,
            "seconds": a.seconds, "took_s": round(took, 1), "pluto_stderr": err, "rc": rc,
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), **res}
    with open(os.path.join(a.out, "chan.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(err)
    print(f"chan: {a.seconds:.0f} s in {took:.1f} s on {len(hzs)} channels, {res['overs']} overs,"
          f" {res['bytes']} bytes ({res['bytes'] / max(a.seconds, 1):.0f} B/s)"
          f" ({'kept up' if took < a.seconds + 15 else 'SLOW: the Pluto did not keep up'})")
    if rc:
        raise SystemExit(f"chan: the Pluto side exited {rc}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tools/pluto_bench.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default=None, help=f"libiio URI (default $PLUTO_URI or {URI})")
    ap.add_argument("--as", dest="as_who", metavar="WHO",
                    help="act as bench holder WHO (sets $K6_BENCH_AS: no env prefix, so one allow rule fits)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    sub.add_parser("quiet")

    def tx_args(p):
        p.add_argument("--offset-hz", type=float, default=0.0,
                       help="a fine correction on top of the stored calibration (Hz; the LO moves by minus it)")
        p.add_argument("--gain", type=float, default=-60.0, help=f"TX attenuation, dB ({GAIN_MIN}..{GAIN_MAX})")
        p.add_argument("--rate", type=int, default=RATE)
        p.add_argument("--rf-off", action="store_true", help="stream with the TX LO powered down (rehearsal)")

    s = sub.add_parser("send")
    s.add_argument("wav")
    s.add_argument("--hz", type=int, default=HZ)
    s.add_argument("--dev", type=float, default=3000.0, help="peak deviation at full scale, Hz")
    s.add_argument("--peak", type=float, default=None, metavar="HZ",
                   help="scale the wav so its own peak is HZ (before --preemph), overriding --dev; "
                        "3000 matches k6_upload's F/1800 uploads")
    s.add_argument("--preemph", action="store_true", help="750 us pre-emphasis (voice)")
    tx_args(s)
    cu = sub.add_parser("cue", help="wait for one burst on --listen-hz (a module's CW 'ready' call), then send WAV")
    cu.add_argument("wav")
    cu.add_argument("--hz", type=int, default=HZ, help="where the WAV goes")
    cu.add_argument("--listen-hz", type=int, default=HZ, help="where the call comes (the K6's bench frequency)")
    cu.add_argument("--wait-s", type=float, default=900.0, help="how long to wait for the call")
    cu.add_argument("--delay-s", type=float, default=1.0, help="after the call ends, before the send")
    cu.add_argument("--min-s", type=float, default=None, help="shortest call, s (default: pluto_parrot.CUE_MIN_S)")
    cu.add_argument("--max-s", type=float, default=None, help="longest call, s (default: pluto_parrot.CUE_MAX_S)")
    cu.add_argument("--min-peak", type=float, default=0.0, help="weakest call, ADC peak of 2047 (the K6's: 63-66)")
    cu.add_argument("--rx-gain", type=int, default=10, help="RX gain, dB (manual; the K6 keys 5 W nearby)")
    cu.add_argument("--dev", type=float, default=3000.0, help="peak deviation at full scale, Hz")
    cu.add_argument("--peak", type=float, default=None, metavar="HZ", help="as send --peak")
    cu.add_argument("--preemph", action="store_true", help="750 us pre-emphasis (voice)")
    tx_args(cu)
    f = sub.add_parser("fsk24")
    f.add_argument("--rounds", type=int, default=20)
    f.add_argument("--sweep", action="store_true",
                   help="the sensitivity sweep for c/k6/mod/fsk24rx_sweep.c (use --gain -70)")
    f.add_argument("--follow", metavar="LOG", help="--sweep: time it from the K6 logger's 'fsk24 start' line")
    f.add_argument("--t0", help="--sweep: the module's start (epoch s), or 'now' (a rehearsal)")
    f.add_argument("--wait-s", type=float, default=900.0, help="--follow: how long to wait for the line")
    f.add_argument("--if-hz", type=float, default=SWEEP_IF,
                   help="--sweep: the signal this far above the TX LO (its leak off the channel)")
    tx_args(f)
    st = sub.add_parser("steps", help="the same audio at each of --gains in turn (a sensitivity ladder)")
    st.add_argument("wav")
    st.add_argument("--gains", required=True, help="TX gains in dB, comma separated, e.g. -70,-73,-76")
    st.add_argument("--hz", type=int, default=HZ)
    st.add_argument("--dev", type=float, default=3000.0, help="peak deviation at full scale, Hz")
    st.add_argument("--peak", type=float, default=None, metavar="HZ",
                    help="scale the wav so its own peak is HZ (before --preemph), overriding --dev; "
                         "3000 matches k6_upload's F/1800 uploads")
    st.add_argument("--preemph", action="store_true", help="750 us pre-emphasis (voice, FFSK)")
    st.add_argument("--pause", type=float, default=3.0, help="seconds between sends")
    st.add_argument("--out", help="the sends' gains and times (json), for modem.fsk24 report-steps")
    tx_args(st)
    st.set_defaults(gain=GAIN_MAX)
    r = sub.add_parser("rx")
    r.add_argument("out", help="OUT.cf32, or OUT.wav (the FM discriminator; the .cf32 is kept beside it)")
    r.add_argument("--hz", type=int, default=HZ)
    r.add_argument("--seconds", type=float, default=10.0)
    r.add_argument("--gain", type=int, default=30, help="RX gain, dB (manual)")
    r.add_argument("--wav-only", action="store_true",
                   help="OUT.wav: stream the FM audio to the WAV and keep no IQ (long receive captures)")
    r.add_argument("--wav-rate", type=int, default=8000, choices=(8000, 16000, 24000, 48000),
                   help="--wav-only: the WAV's sample rate")
    r.add_argument("--channels", metavar="HZ,HZ,...", default=None,
                   help="FM audio only (as --wav-only) for each of these channels at once, all within"
                        " +-1.16 MHz of --hz (the LO; keep it 15 kHz or more off every channel):"
                        " OUT_<MHz>.wav and .json each. --wav-rate 8000 or 16000")
    ch = sub.add_parser("chan", help="FM channels split on the Pluto's ARM, gated HamFLAC overs back (tools/pluto_chan/)")
    ch.add_argument("bin", nargs="?", help="pluto_chan.armhf (its sha256 listed in tools/pluto_chan/allowed.sha256)")
    ch.add_argument("out", nargs="?", help="OUTDIR: <MHz>_<UTC>.flac a over, levels.json, overs.json, chan.json")
    ch.add_argument("--hz", type=int, default=443_000_000, help="the RX LO, between channels")
    ch.add_argument("--channels", default="", metavar="HZ,HZ,...")
    ch.add_argument("--seconds", type=float, default=60.0)
    ch.add_argument("--gain", type=int, default=50, help="RX gain, dB (manual)")
    ch.add_argument("--probe", action="store_true", help="only report the Pluto's CPU, memory and iio_readdev")
    pa = sub.add_parser("parrot", help="send back each YSF or M17 sotto message heard (tools/pluto_parrot.py)")
    pa.add_argument("--hz", type=int, default=441_850_000)
    pa.add_argument("--minutes", type=float, default=20.0)
    pa.add_argument("--delay-s", type=float, default=2.0, help="after the burst ends, before the reply")
    pa.add_argument("--rx-gain", type=int, default=20, help="RX gain, dB (manual)")
    pa.add_argument("--max-replies", type=int, default=30)
    pa.add_argument("--save", metavar="DIR", help="keep each burst heard (cf32 at 240 kS/s)")
    tx_args(pa)
    pa.set_defaults(gain=-40.0)
    m = sub.add_parser("measure")
    m.add_argument("cap")
    c = sub.add_parser("cal")
    c.add_argument("cap")
    c.add_argument("--ref-hz", type=int, default=None, help="the carrier's true frequency (default: the capture's)")
    c.add_argument("--save", action="store_true")
    a = ap.parse_args(argv)
    if a.as_who:
        os.environ["K6_BENCH_AS"] = a.as_who
    {"info": cmd_info, "quiet": cmd_quiet, "send": cmd_send, "fsk24": cmd_fsk24, "rx": cmd_rx, "chan": cmd_chan, "steps": cmd_steps,
     "measure": cmd_measure, "cal": cmd_cal, "parrot": cmd_parrot,
     "cue": cmd_cue}[a.cmd](a)


if __name__ == "__main__":
    main()
