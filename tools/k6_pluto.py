#!/usr/bin/env python3
"""The ADALM-Pluto on the hamlet bench: a flat FM transmitter (the far end the IC-7100's audio
path can't be) and a flat receiver, through libiio's command-line tools (iio_attr, iio_writedev,
iio_readdev; libiio 0.26 on hamlet), so nothing but numpy is needed.

  python3 tools/k6_pluto.py info                       the Pluto's attributes as found
  python3 tools/k6_pluto.py fsk24 [--rounds N]         fsk24 run 3: plain FSK from IQ to the K6
         [--offset-hz F] [--gain -60] [--rf-off]       (c/k6/mod/fsk24rx.c listening), AG7EW MCW
                                                       first and last
  python3 tools/k6_pluto.py rx OUT.cf32 --seconds S    capture (TX powered down) at 240 kHz complex64
  python3 tools/k6_pluto.py offset CAP.cf32            a capture's carrier: its offset in Hz and ppm
                                                       (capture the IC-7100's CW ID: its TCXO is the
                                                       reference) and the peak deviation

Rules it keeps (docs/hamlet-permissions.md, the project's bench rules): it transmits only on
440.850 MHz +- 20 kHz of offset (the K6 bench frequency, inside 430-450), at most gain -30 dB
(the Pluto's attenuation; -60, the default, is about -55 dBm into the antenna), only under the
bench's PTT lock and the bench owner check, every transmission identifies (AG7EW in MCW, an 800
Hz tone at 2.5 kHz deviation, before and after the data), and the TX LO is powered down before
the stream starts and after it ends (and on any error), so there is never a bare carrier
outside a transmission. --rf-off streams with the TX LO left powered down: a rehearsal that
checks the USB link keeps up (no RF at all).

The Pluto's crystal is +-25 ppm (+-11 kHz at 440 MHz): measure it first (rx while the IC-7100
sends a CW ID, then offset) and pass the result as --offset-hz (the Pluto's error, its sign as
offset prints it: the LO is moved by minus that).
SPDX-License-Identifier: MIT
"""
import argparse
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

URI = os.environ.get("PLUTO_URI", "usb:")
HZ = 440_850_000
OFFSET_MAX = 20_000
GAIN_MAX = -30.0
GAIN_MIN = -89.75
RATE = 2_400_000            # above 2.083 MS/s: no FIR needed in the AD9361
FS_TRACK = 48_000
IQ_FS_SAVE = 240_000
AMP = 0.5 * 32767           # the DAC takes the top 12 of 16 bits; half scale
CHUNK = 1 << 17             # samples a write (about 55 ms at 2.4 MS/s)
TX_DEV = "cf-ad9361-dds-core-lpc"
RX_DEV = "cf-ad9361-lpc"
PHY = "ad9361-phy"
ID_TEXT = "AG7EW"
ID_DEV = 2500.0


def iio_attr(*args, uri=None, check=True):
    cmd = ["iio_attr", "-u", uri or URI, "-q"] + [str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"{' '.join(cmd)}: {r.stderr.strip() or r.returncode}")
    return r.stdout.strip()


def tx_power(on, uri=None):
    """the TX LO on (0) or powered down (1): no RF at all while it is down"""
    iio_attr("-c", "-o", PHY, "altvoltage1", "powerdown", 0 if on else 1, uri=uri)


def setup(rate=RATE, tx_hz=None, rx_hz=None, gain=None, uri=None):
    tx_power(False, uri)
    iio_attr("-c", "-o", PHY, "voltage0", "sampling_frequency", int(rate), uri=uri)
    iio_attr("-c", "-o", PHY, "voltage0", "rf_bandwidth", 200000, uri=uri)
    iio_attr("-c", PHY, "voltage0", "rf_bandwidth", 200000, uri=uri)
    for ch in ("altvoltage0", "altvoltage1", "altvoltage2", "altvoltage3"):
        iio_attr("-c", "-o", TX_DEV, ch, "raw", 0, uri=uri, check=False)   # the DDS test tones off
    if tx_hz is not None:
        iio_attr("-c", "-o", PHY, "altvoltage1", "frequency", int(tx_hz), uri=uri)
        iio_attr("-c", "-o", PHY, "voltage0", "hardwaregain", f"{gain:.2f}", uri=uri)
    if rx_hz is not None:
        iio_attr("-c", "-o", PHY, "altvoltage0", "frequency", int(rx_hz), uri=uri)


def check_tx(hz, offset, gain):
    if abs(offset) > OFFSET_MAX:
        raise SystemExit(f"refusing: offset {offset} Hz is more than {OFFSET_MAX}")
    lo = hz - offset
    if not (430_010_000 <= lo <= 449_990_000) or abs(hz - HZ) > 0:
        raise SystemExit(f"refusing: transmit only on {HZ} Hz (asked {hz}, LO {lo})")
    if not (GAIN_MIN <= gain <= GAIN_MAX):
        raise SystemExit(f"refusing: gain {gain} dB outside {GAIN_MIN}..{GAIN_MAX}")
    return lo


def id_track(fs=FS_TRACK, text=ID_TEXT):
    from modem import k6
    a = k6.cw(text, fs, wpm=20, tone=800.0, level=1.0)
    return np.concatenate([np.zeros(int(0.2 * fs)), a * ID_DEV, np.zeros(int(0.2 * fs))])


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
    only while it plays. -> (seconds of signal, seconds it took)"""
    import k6_bench as kb
    secs = len(track) / FS_TRACK
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


def cmd_info(a):
    for args in (("-c", "-o", PHY, "altvoltage1", "frequency"), ("-c", "-o", PHY, "altvoltage1", "powerdown"),
                 ("-c", "-o", PHY, "voltage0", "sampling_frequency"), ("-c", "-o", PHY, "voltage0", "hardwaregain"),
                 ("-c", PHY, "altvoltage0", "frequency"), ("-c", PHY, "voltage0", "gain_control_mode"),
                 ("-c", PHY, "voltage0", "hardwaregain"), ("-d", PHY, "xo_correction")):
        print(" ".join(args[1:]), "=", iio_attr(*args, uri=a.uri, check=False))


def cmd_fsk24(a):
    from modem import fsk24
    lo = check_tx(HZ, a.offset_hz, a.gain)
    track, segs = fsk24.freq_track(a.rounds)
    ident = id_track()
    full = np.concatenate([ident, track, ident])
    print(f"fsk24 run 3: {len(full) / FS_TRACK:.1f} s ({a.rounds} rounds, {len(segs) * fsk24.PER_BLOCK} packets),"
          f" LO {lo} Hz, gain {a.gain} dB, rate {a.rate}, {'RF OFF (rehearsal)' if a.rf_off else 'RF on'}")
    setup(a.rate, tx_hz=lo, gain=a.gain, uri=a.uri)
    t = time.time()
    print(f"start {time.strftime('%H:%M:%S', time.gmtime(t))}Z", flush=True)
    secs, took = stream(full, a.rate, a.uri, rf=not a.rf_off, what="fsk24 run 3")
    print(f"end {time.strftime('%H:%M:%S', time.gmtime())}Z: {secs:.1f} s of signal in {took:.1f} s"
          f" ({'kept up' if took < secs + 2.0 else 'SLOW: the link did not keep up, gaps on air'})")


def cmd_rx(a):
    from scipy import signal as sps
    setup(RATE, rx_hz=HZ, uri=a.uri)
    iio_attr("-c", PHY, "voltage0", "gain_control_mode", "manual", uri=a.uri)
    iio_attr("-c", PHY, "voltage0", "hardwaregain", a.gain, uri=a.uri)
    n = int(a.seconds * RATE)
    cmd = ["iio_readdev", "-u", a.uri or URI, "-b", str(CHUNK), "-s", str(n), RX_DEV, "voltage0", "voltage1"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32)
    iq = (x[0::2] + 1j * x[1::2]) / 2048.0
    y = sps.resample_poly(iq, 1, RATE // IQ_FS_SAVE).astype(np.complex64)
    y.tofile(a.out)
    print(f"{a.out}: {len(y) / IQ_FS_SAVE:.1f} s at {IQ_FS_SAVE} complex64, rms {np.sqrt(np.mean(np.abs(y) ** 2)):.4f}")


def offset_of(iq, fs=IQ_FS_SAVE):
    """(carrier offset Hz, peak deviation Hz, share of the capture with a signal): the mean and
    the 99.5th percentile of the discriminator where the signal is 20 dB over the capture's floor"""
    from scipy import signal as sps
    h = sps.firwin(255, 8000, fs=fs)
    x = sps.lfilter(h, 1.0, iq)
    p = np.abs(x) ** 2
    floor = np.percentile(p, 10)
    on = p > 100 * max(floor, 1e-20)
    d = np.angle(x[1:] * np.conj(x[:-1])) * fs / (2 * np.pi)
    d = d[on[1:]]
    if len(d) < fs * 0.2:
        return None, None, float(np.mean(on))
    c = float(np.mean(d))
    return c, float(np.percentile(np.abs(d - c), 99.5)), float(np.mean(on))


def cmd_offset(a):
    iq = np.fromfile(a.cap, np.complex64)
    c, dev, share = offset_of(iq)
    if c is None:
        print(f"no signal 20 dB over the floor (on {share:.1%} of the capture)")
        return
    print(f"carrier {c:+.0f} Hz from {HZ} ({c / HZ * 1e6:+.2f} ppm), peak deviation {dev:.0f} Hz, "
          f"signal on {share:.0%} of the capture")
    print("if this was the IC-7100 (its TCXO the reference), the Pluto reads it that far off: send with "
          f"--offset-hz {-c:+.0f}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tools/k6_pluto.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default=None, help=f"libiio URI (default $PLUTO_URI or {URI})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    f = sub.add_parser("fsk24")
    f.add_argument("--rounds", type=int, default=20)
    f.add_argument("--offset-hz", type=float, default=0.0,
                   help="the Pluto's frequency error as offset printed it, with its sign flipped (Hz)")
    f.add_argument("--gain", type=float, default=-60.0, help=f"TX attenuation, dB ({GAIN_MIN}..{GAIN_MAX})")
    f.add_argument("--rate", type=int, default=RATE)
    f.add_argument("--rf-off", action="store_true", help="stream with the TX LO powered down (rehearsal)")
    r = sub.add_parser("rx")
    r.add_argument("out")
    r.add_argument("--seconds", type=float, default=8.0)
    r.add_argument("--gain", type=int, default=30, help="RX gain, dB (manual)")
    o = sub.add_parser("offset")
    o.add_argument("cap")
    a = ap.parse_args(argv)
    {"info": cmd_info, "fsk24": cmd_fsk24, "rx": cmd_rx, "offset": cmd_offset}[a.cmd](a)


if __name__ == "__main__":
    main()
