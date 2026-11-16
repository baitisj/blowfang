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
GAIN_MAX = -30.0
GAIN_MIN = -89.75
RATE = 2_400_000            # above 2.083 MS/s: no FIR needed in the AD9361
FS_TRACK = 48_000
IQ_FS_SAVE = 240_000
WAV_FULL_DEV = 5000.0       # rx OUT.wav: this deviation = full scale
AMP = 0.5 * 32767           # the DAC takes the top 12 of 16 bits; half scale
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


def setup(rate=RATE, tx_hz=None, rx_hz=None, gain=None, rx_gain=None, uri=None):
    quiet(uri)
    c = load_cal()
    if c:
        put("dev", PHY, "xo_correction", int(c["xo"]), uri=uri)
    put("out", PHY, "voltage0", "sampling_frequency", int(rate), uri=uri)
    put("out", PHY, "voltage0", "rf_bandwidth", 200000, uri=uri)
    put("in", PHY, "voltage0", "rf_bandwidth", 200000, uri=uri)
    if tx_hz is not None:
        put("out", PHY, "altvoltage1", "frequency", int(tx_hz), uri=uri)
        put("out", PHY, "voltage0", "hardwaregain", f"{gain:.2f}", uri=uri)
    if rx_hz is not None:
        put("out", PHY, "altvoltage0", "frequency", int(rx_hz), uri=uri)   # RX_LO is an output channel
        put("in", PHY, "voltage0", "gain_control_mode", "manual", uri=uri)
        put("in", PHY, "voltage0", "hardwaregain", int(rx_gain if rx_gain is not None else 30), uri=uri)


def check_tx(hz, offset, gain):
    if abs(offset) > OFFSET_MAX:
        raise SystemExit(f"refusing: offset {offset} Hz is more than {OFFSET_MAX}")
    lo = hz - offset
    if not (TX_LO_MIN <= hz <= TX_LO_MAX and TX_LO_MIN <= lo <= TX_LO_MAX):
        raise SystemExit(f"refusing: transmit only in {TX_LO_MIN}..{TX_LO_MAX} Hz (asked {hz}, LO {lo});"
                         " outside 70 cm needs Jeff's OK")
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


def iq_chunks(track, fs_track=FS_TRACK, rate=RATE, chunk=CHUNK):
    """instantaneous frequency (Hz at fs_track) -> int16 interleaved IQ bytes, chunk by chunk,
    phase continuous (the track interpolated linearly to rate)"""
    n_out = int(len(track) * rate / fs_track)
    t_src = np.arange(len(track)) / fs_track
    phase = 0.0
    for a in range(0, n_out, chunk):
        b = min(n_out, a + chunk)
        f = np.interp(np.arange(a, b) / rate, t_src, track)
        ph = phase + 2 * np.pi * np.cumsum(f) / rate
        phase = float(ph[-1] % (2 * np.pi))
        iq = np.empty(2 * (b - a), np.int16)
        iq[0::2] = np.round(AMP * np.cos(ph))
        iq[1::2] = np.round(AMP * np.sin(ph))
        yield iq.tobytes()


def stream(track, rate=RATE, uri=None, rf=True, what="pluto"):
    """the track through iio_writedev, under the bench's PTT lock and owner check; the TX LO up
    only while it plays. The caller has put the ID in the track. -> (seconds of signal, took)"""
    import k6_bench as kb
    secs = len(track) / FS_TRACK
    if (uri or URI).startswith("usb:"):
        raise SystemExit("refusing to stream over usb: (iio_writedev holds the interface, so the TX LO"
                         " could not be powered down from here): use ip:192.168.2.1")
    kb.check_owner()
    with kb.ptt_lock():
        cmd = ["iio_writedev", "-u", uri or URI, "-b", str(CHUNK), TX_DEV, "voltage0", "voltage1"]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        t0 = time.time()
        try:
            gen = iq_chunks(track, rate=rate)
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
                kb.note_tx(t0, t1, f"{what} (Pluto IQ, {secs:.1f} s)")
                kb.note_id(t0, t1, f"{what}: MCW {ID_TEXT} first and last")
    return secs, t1 - t0


def send(track, hz, offset, gain, rate, uri, rf, what):
    """ID + track + ID on hz, with the bench checks; prints the start and end in UTC"""
    lo = check_tx(hz, offset, gain)
    ident = id_track()
    full = np.concatenate([ident, track, ident])
    setup(rate, tx_hz=lo, gain=gain, uri=uri)
    print(f"{what}: {len(full) / FS_TRACK:.1f} s on {hz} Hz (LO {lo}), gain {gain} dB, rate {rate}, "
          f"{'RF OFF (rehearsal)' if not rf else 'RF on'}")
    print(f"start {time.strftime('%H:%M:%S', time.gmtime())}Z", flush=True)
    secs, took = stream(full, rate, uri, rf=rf, what=what)
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


def capture(seconds, hz=HZ, gain=30, uri=None, rate=RATE):
    """-> complex64 at IQ_FS_SAVE, the meta dict"""
    import k6_bench as kb
    del kb                                  # imported for its error if the tools dir is missing
    setup(rate, rx_hz=hz, rx_gain=gain, uri=uri)
    xo = get("dev", PHY, "xo_correction", uri=uri)
    n = int(seconds * rate)
    cmd = ["iio_readdev", "-u", uri or URI, "-b", str(CHUNK), "-s", str(n), RX_DEV, "voltage0", "voltage1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    dec, parts, t0 = Decimator(rate, rate // IQ_FS_SAVE), [], time.time()
    try:
        while True:
            raw = p.stdout.read(4 * CHUNK)
            if not raw:
                break
            parts.append(dec(raw[: len(raw) // 4 * 4]))
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


def cmd_send(a):
    from scipy.io import wavfile
    fs, x = wavfile.read(a.wav)
    x = np.asarray(x, float)
    if x.ndim > 1:
        x = x[:, 0]
    if np.issubdtype(np.asarray(x).dtype, np.floating) and np.max(np.abs(x)) > 1.5:
        x = x / 32768.0                     # integer PCM read as float
    track = audio_track(x, fs, a.dev, a.preemph)
    send(track, a.hz, a.offset_hz, a.gain, a.rate, a.uri, not a.rf_off,
         f"send {os.path.basename(a.wav)}")


def cmd_fsk24(a):
    from modem import fsk24
    track, segs = fsk24.freq_track(a.rounds)
    print(f"fsk24 run 3: {a.rounds} rounds, {len(segs) * fsk24.PER_BLOCK} packets")
    send(track, HZ, a.offset_hz, a.gain, a.rate, a.uri, not a.rf_off, "fsk24 run 3")


def cmd_rx(a):
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tools/pluto_bench.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default=None, help=f"libiio URI (default $PLUTO_URI or {URI})")
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
    s.add_argument("--preemph", action="store_true", help="750 us pre-emphasis (voice)")
    tx_args(s)
    f = sub.add_parser("fsk24")
    f.add_argument("--rounds", type=int, default=20)
    tx_args(f)
    r = sub.add_parser("rx")
    r.add_argument("out", help="OUT.cf32, or OUT.wav (the FM discriminator; the .cf32 is kept beside it)")
    r.add_argument("--hz", type=int, default=HZ)
    r.add_argument("--seconds", type=float, default=10.0)
    r.add_argument("--gain", type=int, default=30, help="RX gain, dB (manual)")
    m = sub.add_parser("measure")
    m.add_argument("cap")
    c = sub.add_parser("cal")
    c.add_argument("cap")
    c.add_argument("--ref-hz", type=int, default=None, help="the carrier's true frequency (default: the capture's)")
    c.add_argument("--save", action="store_true")
    a = ap.parse_args(argv)
    {"info": cmd_info, "quiet": cmd_quiet, "send": cmd_send, "fsk24": cmd_fsk24, "rx": cmd_rx,
     "measure": cmd_measure, "cal": cmd_cal}[a.cmd](a)


if __name__ == "__main__":
    main()
