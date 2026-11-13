"""Does the K6's range burst (c/k6/mod/rangetx.c) keep its tone on the bit edges? Reads a
recording of one burst (the IC-7100's FM-D audio at 48 kHz, best) and says, through the burst, how
far the tone's phase at each bit edge is from where phase-continuous FFSK puts it.

  python3 tools/k6_rangetx_check.py REC [--fs 48000] [--packets 6]

It finds the burst by its lead (matched to the ideal audio), fits the time base to the tone's
changes (the recorder's clock against the K6's), then fits the tone's phase over each bit and
runs it back to the bit's leading edge. Error = that phase less the ideal one (a half cycle a 600
Hz bit, a whole one a 1200 Hz bit, from the burst's start), with each tone's own constant offset
(the receive filters' phase at 600 and 1200 Hz) taken out from the first 0.5 s. rangetx holds
both tones to one error, so a tone p ppm off its bit clock beyond what RT_PPM says walks both by
about 0.32 p degrees a second (the model's figure); a good burst stays within a few degrees.
It also gives the 1200 Hz bits' level against the 600 Hz bits' (rangetx sends them at RT_GAIN12
/ RT_GAIN, 2.5 times: pre-emphasis). MIT licence."""
import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_tonewr as tw  # noqa: E402
from modem import k6r  # noqa: E402

BAUD = 1200.0
PPM_DEG_S = 0.32        # rangetx's walk (deg/s at either tone) a ppm of the tone against its bits


def tone_bits(npk):
    return k6r.tone_bits(k6r.burst_bits(k6r.test_payloads(npk)))


def find_burst(x, fs, tb):
    """the sample where the burst's first bit starts: the lead (0.5 s) matched to the audio
    (complex, so the tone's start phase doesn't matter)"""
    from scipy import signal
    n = int(0.5 * fs)
    lead = k6r.audio(k6r.burst_bits(k6r.test_payloads(1))[:600], fs=fs, level=1.0)[:n]
    ref = signal.hilbert(lead)
    c = np.abs(signal.fftconvolve(x, np.conj(ref[::-1]), "valid"))
    return int(np.argmax(c))


def changes(tb):
    return np.flatnonzero(np.diff(tb.astype(int))) + 1


def timebase(x, fs, s0, tb):
    """-> (sample of bit 0's edge, samples a bit) fitted to the tone's changes, followed through
    the burst (refitted every 0.5 s, so a few hundred ppm don't lose it)"""
    from scipy import signal
    spb = fs / BAUD
    y = signal.hilbert(x[s0:s0 + int((len(tb) + 50) * spb)])
    f = np.diff(np.unwrap(np.angle(y))) * fs / (2 * np.pi)
    k = max(1, int(fs * 0.0003))
    f = np.convolve(f, np.ones(k) / k, "same")
    a, b = 0.0, spb                                  # edge k at a + b k (from s0)
    ks, ss = [], []
    w = int(0.0004 * fs)
    last_fit = 0
    for kc in changes(tb):
        c = int(a + b * kc)
        if c + w >= len(f) or c - w < 0:
            break
        up = tb[kc] == 0                             # to 1200 Hz
        seg = f[c - w:c + w] > 900 if up else f[c - w:c + w] < 900
        if seg.any():
            ks.append(kc)
            ss.append(c - w + int(np.argmax(seg)))
        if kc - last_fit > 600 and len(ks) > 20:
            b, a = np.polyfit(ks, ss, 1)
            last_fit = kc
    b, a = np.polyfit(ks, ss, 1)
    res = np.asarray(ss) - (a + b * np.asarray(ks))
    keep = np.abs(res) < 4 * max(np.std(res), 1.0)
    b, a = np.polyfit(np.asarray(ks)[keep], np.asarray(ss)[keep], 1)
    return s0 + a, b, float(np.std(res[keep]) / fs)


def edge_errors(x, fs, e0, spb, tb, f6, f12):
    """-> (bit index, tone, error in cycles) for each bit: the tone's phase at its leading edge
    (fitted over the bit's middle 70%) less the ideal phase from bit 0"""
    want = np.concatenate([[0.0], np.cumsum(np.where(tb == 1, 0.5, 1.0))])
    out = []
    for k in range(len(tb)):
        n0 = e0 + k * spb
        a, b = int(n0 + 0.15 * spb), int(n0 + 0.85 * spb)
        if b > len(x):
            break
        f = f6 if tb[k] else f12
        ph = tw.inst_phase(x, fs, a, b, f, n0) / (2 * np.pi)
        out.append((k, int(tb[k]), ph - want[k]))
    return out


def analyse(x, fs, npk=6):
    x = tw.bandpass(np.asarray(x, float), fs)
    tb = tone_bits(npk)
    s0 = find_burst(x, fs, tb)
    e0, spb, rms = timebase(x, fs, s0, tb)
    k6_s = spb * BAUD                                # recorder samples a K6 second
    # the tones' frequencies in recorder time, from the steadiest stretch: 600 Hz from the
    # lead's five-bit runs is short, so fit the phase per bit and use the K6's words
    f6 = tw.W6 * fs / k6_s
    f12 = tw.W12 * fs / k6_s
    err = edge_errors(x, fs, e0, spb, tb, f6, f12)
    # the tones' levels: rms over the middle of each bit, 1200 Hz bits against 600 Hz ones (the
    # K6 sends the 1200s at RT_GAIN12 / RT_GAIN; a flat receiver should show that ratio)
    lv = [[], []]
    for k in range(len(tb)):
        a, b = int(e0 + (k + 0.15) * spb), int(e0 + (k + 0.85) * spb)
        if b > len(x):
            break
        lv[int(tb[k])].append(np.sqrt(np.mean(x[a:b] ** 2)))
    k = np.array([e[0] for e in err])
    tone = np.array([e[1] for e in err])
    ph = np.array([e[2] for e in err])
    t = k / BAUD
    out = {"start_s": e0 / fs, "ppm": (k6_s / fs - 1) * 1e6, "fit_us": rms * 1e6, "rows": [],
           "level_db": float(20 * np.log10(np.mean(lv[0]) / np.mean(lv[1])))}
    ref = {}
    for tn in (0, 1):
        m = (tone == tn) & (t < 0.5)
        ref[tn] = np.angle(np.mean(np.exp(2j * np.pi * ph[m]))) / (2 * np.pi)
    dev = (ph - np.array([ref[v] for v in tone]) + 0.5) % 1.0 - 0.5
    out["dev"] = (t, tone, dev)
    for t0 in np.arange(0, t[-1], 0.5):
        row = [t0]
        for tn in (1, 0):
            m = (tone == tn) & (t >= t0) & (t < t0 + 0.5)
            row.append(np.degrees(np.angle(np.mean(np.exp(2j * np.pi * dev[m])))) if m.any() else np.nan)
            row.append(float(abs(np.mean(np.exp(2j * np.pi * dev[m])))) if m.any() else np.nan)
        out["rows"].append(row)
    # the walk: the unwrapped per-0.5 s means against time (deg/s)
    for tn, f in ((1, 600), (0, 1200)):
        means = np.unwrap(np.radians([r[1 if tn else 3] for r in out["rows"]]))
        tt = np.array([r[0] for r in out["rows"]])
        ok = np.isfinite(means)
        out["walk_%d" % f] = float(np.degrees(np.polyfit(tt[ok], means[ok], 1)[0])) if ok.sum() > 2 else np.nan
    return out


def report(r):
    out = [f"burst at {r['start_s']:.3f} s; the recorder {r['ppm']:+.0f} ppm against the K6's bit clock "
           f"(tone changes, fit rms {r['fit_us']:.0f} us)",
           f"walk: 600 Hz bits {r['walk_600']:+.1f} deg/s, 1200 Hz bits {r['walk_1200']:+.1f} deg/s: "
           f"the tone against rangetx's bit clock, about {r['walk_600'] / PPM_DEG_S:+.0f} ppm more than "
           f"its RT_PPM says",
           f"level: 1200 Hz bits {r['level_db']:+.1f} dB against 600 Hz bits",
           "    s   600 Hz deg  R     1200 Hz deg  R"]
    for t0, m6, r6, m12, r12 in r["rows"]:
        out.append(f"{t0:5.1f}  {m6:+8.0f}  {r6:5.2f}  {m12:+10.0f}  {r12:5.2f}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("rec")
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--packets", type=int, default=6)
    a = ap.parse_args(argv)
    x, fs = tw.load(a.rec, a.fs)
    print("\n".join(report(analyse(x, fs, a.packets))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
