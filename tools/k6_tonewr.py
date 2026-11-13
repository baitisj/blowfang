"""What a write does to the K6's tone oscillator's phase: reads a recording of the tonewr module
(c/k6/mod/tonewr.c) and says, for each kind of write, whether the tone's phase runs on through
it (continuous) or is set to a phase at the write (reset).

  python3 tools/k6_tonewr.py report REC [--fs 48000]

REC: 16-bit mono raw (--fs, default 48000: the IC-7100's FM-D audio), a .wav, or a UV-380 rxrec
log (8 kHz). The schedule (from tone-on, in the K6's own time) is tonewr.c's:
  A 0-1 s 600 Hz; B 1-2 s the same word rewritten every 7.3 ms; C 2-4 s 1200/600 in turn; D 4-5 s
  tone 1 off for a step, on for two, 9 times; E 5-7 s tone 2 (1200) and tone 1 in turn. C, D and
  E's steps are 37.1 ms plus 0.1-1.6 ms (jit), so that a write that sets the phase shows.
C's changes fix the time base (the recorder's clock against the K6's: offset and rate, so the
schedule lands within microseconds). At each write the tone's absolute phase is fitted just
before and just after: if the oscillator runs on, the jump (after less before) is the same at
every write of a kind (only the receive filters' phase difference between the tones) and the
phase after it is spread out; if the write sets the phase, the phase after it is the same at
every write and the jump is spread out. R is the length of the mean unit vector (1: all the
same, near 0: spread). MIT licence."""
import argparse
import os
import sys

import numpy as np

F1, F2 = 600.0, 12389 / 10.32444      # tone 1 (600.033 written as 6195: near enough), tone 2
STEP = 0.0371
STEP_B = 0.0073
W6 = 6195 / 10.32444
W12 = 12389 / 10.32444


def jit(n):
    """tonewr.c's extra delay of write n of C, D or E: 100 us x j(n), j(0) = 1,
    j(n + 1) = 3 j(n) mod 17"""
    return 0.0001 * pow(3, n, 17)


def schedule():
    """-> list of (t from tone-on in K6 seconds, segment, what): every write after tone-on"""
    ev = []
    for k in range(137):
        ev.append((1.0 + k * STEP_B, "B", "same"))
    for k in range(54):
        ev.append((2.0 + k * STEP + jit(k), "C", "down" if k & 1 else "up"))
    for k in range(27):
        if k % 3 != 2:
            ev.append((4.0 + k * STEP + jit(k), "D", "on" if k % 3 else "off"))
    for k in range(53):
        ev.append((5.0 + k * STEP + jit(k), "E", "t1" if k & 1 else "t2"))
    ev.append((7.0, "E", "off"))
    return ev


def tone_at(t):
    """the tone (Hz, or 0 for none) the schedule has on at K6 time t from tone-on"""
    if t < 0 or t >= 7.0:
        return 0.0
    if t < 2.0:
        return W6
    last = None
    for ts, seg, what in schedule():
        if ts > t:
            break
        last = (seg, what)
    seg, what = last
    if seg == "C":
        return W12 if what == "up" else W6
    if seg == "D":
        return 0.0 if what == "off" else W6
    return W12 if what == "t2" else W6


def bandpass(x, fs, lo=300.0, hi=1600.0):
    from scipy import signal
    sos = signal.butter(4, [lo, hi], "bandpass", fs=fs, output="sos")
    return signal.sosfiltfilt(sos, x)


def find_on(x, fs):
    """tone-on: the start of the first 0.9 s with 600 Hz holding most of the power (20 ms
    frames), refined to the frame where the envelope reaches half its steady level"""
    n = int(0.02 * fs)
    t = np.arange(n) / fs
    ref = np.exp(-2j * np.pi * F1 * t)
    run = 0
    for i in range(0, len(x) - n, n):
        seg = x[i:i + n]
        tot = np.sum(seg ** 2)
        p = abs(np.sum(seg * ref)) ** 2 * 2 / n
        run = run + 1 if tot > 0 and p > 0.5 * tot else 0
        if run == 45:
            s0 = i - 44 * n
            env = np.abs(bb(x[max(0, s0 - n * 5):s0 + n * 10], fs, F1))
            lvl = np.median(env[-n * 5:])
            k = int(np.argmax(env > lvl / 2))
            return max(0, s0 - n * 5) + k
    return None


def bb(x, fs, f, lp=150.0):
    """complex baseband of x at f (low-passed at lp Hz)"""
    from scipy import signal
    t = np.arange(len(x)) / fs
    z = x * np.exp(-2j * np.pi * f * t)
    sos = signal.butter(4, lp, fs=fs, output="sos")
    return signal.sosfiltfilt(sos, z.real) + 1j * signal.sosfiltfilt(sos, z.imag)


def ls_phase(x, fs, a, b, f):
    """the phase p of the best fit A cos(2 pi f n / fs + p) to x[a:b], n counted from the
    recording's start: so 2 pi f n / fs + p is the tone's instantaneous phase at sample n"""
    n = np.arange(a, b)
    w = 2 * np.pi * f * n / fs
    m = np.stack([np.cos(w), -np.sin(w)], 1)
    (c, d), *_ = np.linalg.lstsq(m, x[a:b], rcond=None)
    return float(np.arctan2(d, c))


def inst_phase(x, fs, a, b, f, n):
    """the tone's instantaneous phase at sample n (a float), from a fit over a..b at f"""
    return ls_phase(x, fs, a, b, f) + 2 * np.pi * f * n / fs


def tone_freq(x, fs, a, b, f0, chunk=0.05):
    """the tone's frequency over a..b (recorder time): a tone at f0 + df shows ls_phase at f0
    growing at 2 pi df a second"""
    m = int(chunk * fs)
    ks = np.arange(a, b - m, m)
    ph = np.unwrap([ls_phase(x, fs, k, k + m, f0) for k in ks])
    return f0 + float(np.polyfit(ks / fs, ph, 1)[0] / (2 * np.pi))


def circ(v):
    """-> (R, mean in degrees) of angles v (rad)"""
    if len(v) == 0:
        return 0.0, 0.0
    m = np.mean(np.exp(1j * np.asarray(v, float)))
    return float(abs(m)), float(np.degrees(np.angle(m)))


def wrap(v):
    return (np.asarray(v, float) + np.pi) % (2 * np.pi) - np.pi


def change_times(x, fs, on):
    """C's changes as measured: the sample where the instantaneous frequency crosses 900 Hz, near
    each scheduled change. -> [(scheduled K6 s, measured sample)]"""
    from scipy import signal
    y = signal.hilbert(x[on:on + int(4.3 * fs)])
    f = np.diff(np.unwrap(np.angle(y))) * fs / (2 * np.pi)
    k = max(1, int(fs * 0.0005))
    f = np.convolve(f, np.ones(k) / k, "same")
    out = []
    for ts, seg, what in schedule():
        if seg != "C":
            continue
        c, w = int(ts * fs), int(0.004 * fs)
        if c + w >= len(f):
            break
        s = f[c - w:c + w] > 900 if what == "up" else f[c - w:c + w] < 900
        if s.any():
            out.append((ts, on + c - w + int(np.argmax(s))))
    return out


def timebase(x, fs, on):
    """-> (sample of K6 time 0, samples a K6 second, fit rms s) from C's changes"""
    pts = change_times(x, fs, on)
    t, s = np.array([p[0] for p in pts]), np.array([p[1] for p in pts], float)
    k, c = np.polyfit(t, s, 1)
    res = s - (k * t + c)
    keep = np.abs(res) < 4 * max(np.std(res), 1.0)
    k, c = np.polyfit(t[keep], s[keep], 1)
    return c, k, float(np.std((s - (k * t + c))[keep]) / fs)


def writes(kind):
    return [ts for ts, seg, what in schedule() if (seg, what) == kind]


def across(x, fs, t0, sps, ts, fb, fa, gap=0.0015, span=0.012):
    """the tone's instantaneous phase just before and just after the write at K6 time ts, both
    run to the write's sample: (before or None, after or None)"""
    n = t0 + ts * sps
    a0, a1 = int(n - (gap + span) * fs), int(n - gap * fs)
    b0, b1 = int(n + gap * fs), int(n + (gap + span) * fs)
    if a0 < 0 or b1 > len(x):
        return None, None
    return (inst_phase(x, fs, a0, a1, fb, n) if fb else None,
            inst_phase(x, fs, b0, b1, fa, n) if fa else None)


def runs_on(ts, ph, f):
    """(R, mean deg) of the phase after each write less the last one's run on at f: R 1 and mean
    0 if the tone ran freely between them (ts in recorder seconds)"""
    if len(ts) < 2:
        return 0.0, 0.0
    return circ(np.diff(ph) - 2 * np.pi * f * np.diff(ts))


def free(after, ran):
    """free running, restarts (the phase at the on always the same) or unclear"""
    if after[0] > 0.9:
        return "restarts"
    if ran[0] > 0.9 and abs(ran[1]) < 20:
        return "free running"
    return "unclear"


def analyse(x, fs):
    x = bandpass(np.asarray(x, float), fs)
    on = find_on(x, fs)
    if on is None:
        return None
    t0, sps, jit = timebase(x, fs, on)
    res = {"on_s": on / fs, "t0_s": t0 / fs, "ppm": (sps / fs - 1) * 1e6, "fit_us": jit * 1e6}
    a0, a1 = int(t0 + 0.05 * sps), int(t0 + 0.95 * sps)
    f6 = tone_freq(x, fs, a0, a1, W6 * fs / sps)
    f12 = f6 * W12 / W6
    res["f600"], res["f600_k6"] = f6, f6 * sps / fs
    fr = {W6: f6, W12: f12, 0.0: 0.0}

    def ftone(t):
        return fr[tone_at(t)]
    # B: the same word rewritten: the phase in each 7.3 ms gap against A's tone run on
    ref = ls_phase(x, fs, a0, a1, f6)
    gaps = []
    for ts in writes(("B", "same")):
        n0, n1 = int(t0 + (ts + 0.0008) * sps), int(t0 + (ts + STEP_B - 0.0008) * sps)
        gaps.append(ls_phase(x, fs, n0, n1, f6) - ref)
    d = wrap(np.diff(gaps))
    res["B"] = {"n": len(gaps), "rms": float(np.degrees(np.sqrt(np.mean(d ** 2)))), "R": circ(gaps)[0]}
    # C: up and down
    for kind in ("up", "down"):
        bs, af = [], []
        for ts in writes(("C", kind)):
            b, a = across(x, fs, t0, sps, ts, ftone(ts - 0.001), ftone(ts + 0.001))
            if a is not None:
                bs.append(b)
                af.append(a)
        res["C_" + kind] = {"n": len(af), "jump": circ(np.subtract(af, bs)), "after": circ(af)}
    # D: tone 1 on again
    ts_on = writes(("D", "on"))
    af = [across(x, fs, t0, sps, ts, 0.0, f6)[1] for ts in ts_on]
    res["D_on"] = {"n": len(af), "after": circ(af),
                   "runs_on": runs_on(np.array(ts_on) * sps / fs, af, f6)}
    # E: tone 2 there? and the swaps
    e0, e1 = int(t0 + (5.0 + 0.004) * sps), int(t0 + (5.0 + STEP - 0.004) * sps)
    z = x[e0:e1]
    spec = np.abs(np.fft.rfft(z * np.hanning(len(z)), 8 * len(z)))
    hz = np.fft.rfftfreq(8 * len(z), 1 / fs)
    band = (hz > 300) & (hz < 1600)
    res["E_t2_hz"] = float(hz[band][np.argmax(spec[band])])

    def level(a, b, f):
        n = np.arange(a, b)
        return float(2 * abs(np.mean(x[a:b] * np.exp(-2j * np.pi * f * n / fs))))
    res["E_levels"] = {"t2": level(e0, e1, f12), "t1": level(a0, a1, f6)}
    for kind, name, f in (("t1", "E_to_t1", f6), ("t2", "E_to_t2", f12)):
        tl = writes(("E", kind))
        bs, af = [], []
        for ts in tl:
            b, a = across(x, fs, t0, sps, ts, f6 if kind == "t2" else f12, f, span=0.010)
            bs.append(b)
            af.append(a)
        res[name] = {"n": len(af), "jump": circ(np.subtract(af, bs)), "after": circ(af),
                     "runs_on": runs_on(np.array(tl) * sps / fs, af, f)}
    return res


def verdict(jump_r, after_r):
    if jump_r > 0.9 and after_r < 0.6:
        return "continuous"
    if after_r > 0.9 and jump_r < 0.6:
        return "reset"
    return "unclear"


def report(res):
    if res is None:
        return ["no tone-on found (0.9 s of 600 Hz)"]
    out = [f"tone-on at {res['on_s']:.3f} s; K6 time 0 at {res['t0_s']:.4f} s; the recorder "
           f"{res['ppm']:+.0f} ppm against the K6 (C's changes, fit rms {res['fit_us']:.0f} us)",
           f"A: the 600 Hz tone at {res['f600']:.3f} Hz recorder time, {res['f600_k6']:.3f} Hz K6 time "
           f"({(res['f600_k6'] / W6 - 1) * 1e6:+.0f} ppm against its word: the chip's crystal against "
           f"the MCU's)"]
    b = res["B"]
    out.append(f"B: {b['n']} same-word writes: phase step rms {b['rms']:.1f} deg -> "
               f"{'no effect' if b['rms'] < 15 else 'THE WRITE MOVES THE PHASE'}")
    for kind in ("up", "down"):
        c = res["C_" + kind]
        out.append(f"C {kind:4s} n={c['n']}: jump R {c['jump'][0]:.2f} mean {c['jump'][1]:+.0f} deg; "
                   f"phase after R {c['after'][0]:.2f} mean {c['after'][1]:+.0f} deg -> "
                   f"{verdict(c['jump'][0], c['after'][0])}")
    d = res["D_on"]
    out.append(f"D on   n={d['n']}: phase at the on R {d['after'][0]:.2f} mean {d['after'][1]:+.0f} deg; "
               f"ran on while off R {d['runs_on'][0]:.2f} mean {d['runs_on'][1]:+.0f} deg -> "
               f"{free(d['after'], d['runs_on'])}")
    lv = res["E_levels"]
    out.append(f"E: tone 2 at {res['E_t2_hz']:.0f} Hz, level {lv['t2']:.0f} against tone 1's {lv['t1']:.0f}")
    for name in ("E_to_t1", "E_to_t2"):
        e = res[name]
        out.append(f"{name} n={e['n']}: phase after R {e['after'][0]:.2f}; ran on while off R "
                   f"{e['runs_on'][0]:.2f} mean {e['runs_on'][1]:+.0f} deg -> {free(e['after'], e['runs_on'])}; "
                   f"jump R {e['jump'][0]:.2f} mean {e['jump'][1]:+.0f} deg")
    return out


def load(path, fs):
    if path.endswith(".wav"):
        import soundfile
        x, fs = soundfile.read(path)
        return (x[:, 0] if x.ndim > 1 else x).astype(float), fs
    if path.endswith(".log"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import uv380_i2sprobe
        with open(path, errors="replace") as f:
            x, _, _, _ = uv380_i2sprobe.audio_lines(f)
        return np.array(x, float), 8000
    return np.fromfile(path, "<i2").astype(float), fs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("report")
    q.add_argument("rec")
    q.add_argument("--fs", type=int, default=48000)
    a = ap.parse_args(argv)
    x, fs = load(a.rec, a.fs)
    print("\n".join(report(analyse(x, fs))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
