"""M17 receive on the UV-380 with no hardware mod: the test signal the Pluto sends, and the readers
for the two receive paths the bench measures (docs/uv380-m17-rx.md).

  python3 -m modem.m17rx wav OUT.wav [--tones-s 30] [--bert-s 30]     Pluto test signal, 48 kHz
  python3 -m modem.m17rx tap LOG [--wav OUT.wav]    path B: c6fmtap's 8 kHz FM tap
  python3 -m modem.m17rx phy LOG                    path A: c6phyrx's chip-demodulated windows

The test signal is spec M17 (RRC alpha 0.5) at half scale: send it with `pluto_bench.py send
OUT.wav --dev 4800` for M17's +-2400 Hz outer deviation (--dev 3888 gives DMR's 1944). It starts
with TONES-S seconds of a multitone (TONES, equal levels, Schroeder phases, 0.2 s period: any
whole second holds every tone on an FFT bin), then BERT-S seconds of M17 BERT (PRBS9) frames.

tap: the "a" lines as audio, cut by the module's "v" lines (one 0x34 setting each). Per setting:
the multitone's response against 1 kHz (where the low corner and the top edge sit), and M17 BERT
frames demodulated from the tap at 8 kHz (x6 to 48 kHz, RRC 0.5) with the bit errors.

phy: the "r" lines' 36-byte windows (288 bits, one per 30 ms) joined in time order. Window spacing
(30 ms = none lost), then M17 BERT syncwords in the joined symbols at every symbol offset and
both polarities: how far apart they sit (192 symbols = the windows join with no symbol lost or
doubled), and the decoded frames' bit errors. The "sf" soft values give the chip's level histogram.
SPDX-License-Identifier: MIT
"""
import argparse
import re
import sys

import numpy as np

from . import m17

FS = 48000
TONES = (10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 700, 1000, 1500, 2000, 2500, 3000, 3400, 3800)
HALF = 0.5                      # the outer symbol's level in the WAV (RRC overshoot stays under 1)


# ---- the test signal ----------------------------------------------------------------------------

def multitone(seconds, fs=FS, peak=HALF):
    if seconds <= 0:
        return np.zeros(0)
    t = np.arange(int(seconds * fs)) / fs
    n = len(TONES)
    ph = np.pi * np.arange(n) * (np.arange(n) + 1) / n           # Schroeder: low crest factor
    x = sum(np.cos(2 * np.pi * f * t + p) for f, p in zip(TONES, ph))
    return x / np.max(np.abs(x)) * peak


def bert_frames(seconds):
    return max(1, int(seconds / 0.040))


def bert_audio(nframes, fs=FS, alpha=0.5):
    """M17 BERT (preamble, frames, EOT) as a discriminator track, outer symbol at HALF"""
    data = m17.tx_bert(nframes)
    return m17.modulate(data, fs=fs, alpha=alpha, outer_hz=HALF), data


def test_signal(tones_s=30.0, bert_s=30.0, fs=FS):
    gap = np.zeros(int(0.5 * fs) if tones_s > 0 else 0)
    bert, data = bert_audio(bert_frames(bert_s), fs)
    return np.concatenate([multitone(tones_s, fs), gap, bert]), data


# ---- path B: the FM tap -------------------------------------------------------------------------

def tap_parse(lines):
    """c6fmtap's log -> (samples int16 array, lost lines, [(first sample, 0x34)], other lines)"""
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import uv380_i2sprobe as probe
    lines = [ln.strip() for ln in lines]
    # the log's lines carry "mod say " before a module's say() text, the "a" lines don't
    samples, lost, _, _ = probe.audio_lines(lines)
    marks, info = [], []
    for ln in lines:
        m = re.search(r"\bv (\d+) idx=(\d+) 34=([0-9a-f]{2}) seq=(\d+)", ln)
        if m:
            marks.append((int(m.group(4)) * 64, int(m.group(3), 16)))
        elif re.search(r"\b(tap start|st \d+|end halves|d \d+ half)", ln):
            info.append(ln[ln.find("say ") + 4:] if "say " in ln else ln)
    return np.asarray(samples, float), lost, marks, info


def tone_response(x, fs=8000):
    """one second (fs samples) -> (dB of each tone against 1 kHz, share of power on the tones)"""
    X = np.abs(np.fft.rfft(x[:fs] - np.mean(x[:fs])))
    p = X ** 2
    on = np.array([p[f] for f in TONES])
    share = float(on.sum() / max(p[1:].sum(), 1e-9))
    ref = np.sqrt(p[1000])
    return 20 * np.log10(np.sqrt(on) / max(ref, 1e-9) + 1e-12), share


def corners(resp_db):
    """(lowest tone within 3 dB of 1 kHz, highest such tone) from a response"""
    ok = [f for f, d in zip(TONES, resp_db) if d > -3.0]
    return (ok[0], ok[-1]) if ok else (None, None)


def tap_bert(x, fs=8000, polarity=0):
    """M17 BERT frames from tap audio: x6 to 48 kHz, scaled so its rms is a 4FSK's at 2400 Hz.
    polarity 0 tries both and keeps the one with more frames (the UV-380's tap is inverted: +3
    reads negative, 11-18 run m17rx-tap-1118a); the summary's "polarity" says which"""
    from scipy import signal
    y = signal.resample_poly(x - np.mean(x), FS // fs, 1)
    rms = np.sqrt(np.mean(y ** 2)) or 1.0
    y = y / rms * (m17.OUTER_HZ * np.sqrt(5) / 3)
    best = None
    for pol in ((polarity,) if polarity else (-1, 1)):
        frames = m17.demod(pol * y, FS, 0.5)
        s = m17.summarize(frames)
        # good frames only: inverted BERT (sync DF55) reads as packet syncs (75FF), all junk
        good = s["bert_frames"] + s["lsf"] + s["packets"] + s["stream"] - s["lich_bad"]
        if best is None or good > best[0]:
            best = (good, pol, frames, s)
    _, pol, frames, s = best
    s["polarity"] = pol
    return s, frames


def tap_report(lines, wav=None):
    x, lost, marks, info = tap_parse(lines)
    out = [f"tap: {len(x)} samples ({len(x) / 8000:.1f} s), {lost} lines lost"] + info[:3] + \
        [ln for ln in info if ln.startswith("end")]
    if wav:
        m17.write_wav(wav, np.clip(x, -32768, 32767), 8000)
        out.append(f"audio {wav} (8 kHz)")
    if not len(x):
        return out
    marks = marks or [(0, None)]
    bounds = [(a, marks[i + 1][0] if i + 1 < len(marks) else len(x), v) for i, (a, v) in enumerate(marks)]
    per = {}
    for a, b, v in bounds:
        seg = x[a:b]
        d = per.setdefault(v, {"resp": [], "bert": None, "secs": 0.0, "rms": []})
        d["secs"] += len(seg) / 8000
        if len(seg):
            d["rms"].append(float(np.sqrt(np.mean((seg - seg.mean()) ** 2))))
        for k in range(0, len(seg) - 8000 + 1, 8000):
            r, share = tone_response(seg[k:k + 8000])
            if share > 0.5:
                d["resp"].append(r)
        s, _ = tap_bert(seg)
        if s["bert_frames"]:
            b0 = d["bert"] or {"bert_frames": 0, "bert_bits": 0, "bert_errors": 0}
            for key in ("bert_frames", "bert_bits", "bert_errors"):
                b0[key] += s[key]
            b0.setdefault("windows", []).append((a / 8000, s["bert_frames"], s["bert_errors"], s["polarity"]))
            d["bert"] = b0
    out.append("tone dB vs 1 kHz: " + " ".join(f"{f}" for f in TONES))
    for v, d in per.items():
        name = f"0x34={v:02x}" if v is not None else "0x34=?"
        if d["resp"]:
            r = np.median(np.array(d["resp"]), axis=0)
            lo, hi = corners(r)
            out.append(f"{name} ({len(d['resp'])} s of tones): " + " ".join(f"{z:+.0f}" for z in r)
                       + f"  | within 3 dB: {lo}-{hi} Hz")
        else:
            out.append(f"{name}: no whole second of tones ({d['secs']:.0f} s, rms {np.median(d['rms'] or [0]):.0f})")
        b = d["bert"]
        if b:
            out.append(f"{name} BERT: {b['bert_frames']} frames, {b['bert_errors']} of {b['bert_bits']} bits wrong"
                       f" (BER {b['bert_errors'] / max(1, b['bert_bits']):.2e})")
            out.append(f"{name} BERT windows (s, frames, bits wrong, polarity): "
                       + " ".join(f"{t:.0f}:{n}/{e}{'+' if p > 0 else '-'}" for t, n, e, p in b["windows"]))
        else:
            out.append(f"{name} BERT: no frames")
    return out


# ---- path A: the chip's own demodulator ---------------------------------------------------------

def phy_parse(lines):
    """c6phyrx's log -> ([(ms, 36 bytes)], {ms: soft levels}, info lines)"""
    wins, soft, info = [], {}, []
    for ln in lines:
        m = re.search(r"\br (\d+) se=\d+ fe=\d+ d=([0-9a-f]{72})", ln)
        if m:
            wins.append((int(m.group(1)), bytes.fromhex(m.group(2))))
            continue
        m = re.search(r"\bsf (\d+) ([0-9a-f]{3}) ([0-9a-f]{3}) ([0-9a-f]+)", ln)
        if m:
            b = bytes.fromhex(m.group(4))
            lv = [(v & 0x7F) - 128 if v & 0x40 else v & 0x7F for v in b[0::2]]
            soft.setdefault(int(m.group(1)), []).extend(lv)
            continue
        if re.search(r"\b(st \d+|end slots|listening|at1846)", ln):
            info.append(ln[ln.find("say ") + 4:] if "say " in ln else ln)
    return wins, soft, info


def runs_of_windows(wins, period=30, tol=6):
    """windows in runs with no window lost (each one period after the last)"""
    out, cur = [], []
    for t, d in wins:
        if cur and abs(t - cur[-1][0] - period) > tol:
            out.append(cur)
            cur = []
        cur.append((t, d))
    if cur:
        out.append(cur)
    return out


def bert_in_symbols(sym):
    """BERT frames in a symbol stream (+-1, +-3) at any offset, either polarity -> (frames,
    sync positions, polarity)"""
    sync = m17.symbols(m17.SYNC_BERT)
    best = None
    for pol in (1, -1):
        s = sym * pol
        hits = [i for i in range(len(s) - 192 + 1) if np.array_equal(s[i:i + 8], sync)]
        if best is None or len(hits) > len(best[1]):
            best = (pol, hits)
    pol, hits = best
    s = sym * pol
    frames = []
    for i in hits:
        lv = s[i:i + 192]
        dib = [m17.DIBIT[int(v)] for v in lv]
        by = bytes(dib[j] << 6 | dib[j + 1] << 4 | dib[j + 2] << 2 | dib[j + 3] for j in range(0, 192, 4))
        frames.append(m17.decode_frame("bert", by[2:]))
    return frames, hits, pol


def phy_report(lines):
    wins, soft, info = phy_parse(lines)
    out = [f"phy: {len(wins)} windows"] + info[:2] + [ln for ln in info if ln.startswith("end")]
    if not wins:
        return out
    dt = np.diff([t for t, _ in wins])
    if len(dt):
        vals, cnt = np.unique(np.clip(dt, 0, 200), return_counts=True)
        top = sorted(zip(cnt, vals), reverse=True)[:6]
        out.append("window spacing ms (count): " + " ".join(f"{v}({c})" for c, v in top))
    tot = {"bert_frames": 0, "bert_bits": 0, "bert_errors": 0}
    for r in runs_of_windows(wins):
        if len(r) < 8:
            continue
        sym = m17.symbols(b"".join(d for _, d in r))
        frames, hits, pol = bert_in_symbols(sym)
        if not frames:
            continue
        gaps = np.diff(hits)
        g192 = int(np.sum(gaps == 192))
        s = m17.summarize(frames)
        for k in tot:
            tot[k] += s[k]
        other = sorted(set(int(g) for g in gaps if g != 192))[:8]
        out.append(f"run {r[0][0]}-{r[-1][0]} ms ({len(r)} windows): {len(hits)} BERT syncs, polarity "
                   f"{'+' if pol > 0 else '-'}, {g192} of {len(gaps)} gaps 192 symbols"
                   + (f" (others {other})" if other else "")
                   + f", {s['bert_errors']} of {s['bert_bits']} bits wrong")
    if tot["bert_frames"]:
        out.append(f"BERT total {tot['bert_frames']} frames, {tot['bert_errors']} of {tot['bert_bits']} bits wrong "
                   f"(BER {tot['bert_errors'] / max(1, tot['bert_bits']):.2e})")
    else:
        out.append("no BERT frames in the joined windows")
    if soft:
        lv = np.concatenate([np.array(v) for v in soft.values()])
        h, e = np.histogram(lv, bins=np.arange(-40, 42, 4))
        out.append(f"soft levels ({len(lv)}): " + " ".join(f"{int(a)}:{c}" for a, c in zip(e[:-1], h) if c))
    return out


# ---- simulated logs (tests) ---------------------------------------------------------------------

def sim_tap_log(hp_hz=0.0, top_hz=3400.0, tones_s=6.0, bert_s=6.0, v34=(0x1C,), step_s=None, invert=False):
    """a c6fmtap log of the test signal through a flat FM receiver, an optional one-pole high pass
    and a low pass, at 8 kHz (scaled like a discriminator, 2400 Hz = 8000)"""
    from scipy import signal
    x, _ = test_signal(tones_s, bert_s, FS)
    x = x / HALF * 8000 * (-1 if invert else 1)
    if hp_hz:
        b, a = signal.butter(1, hp_hz, "high", fs=FS)
        x = signal.lfilter(b, a, x)
    b, a = signal.butter(6, top_hz, fs=FS)
    x = signal.lfilter(b, a, x)
    y = np.clip(np.round(x[::6]), -32768, 32767).astype(int)
    n = len(y) // 64
    lines = [f"mod say tap start v34={','.join(f'{v:02x}' for v in v34)} step=5000 s=150 at=ok"]
    step = int((step_s or 1e9) * 8000 / 64)
    for i in range(n):
        if i % step == 0:
            lines.append(f"mod say v {i * 8} idx=0 34={v34[(i // step) % len(v34)]:02x} seq={i}")
        lines.append(f"a {i} " + " ".join(f"{v & 0xFFFF:04x}" for v in y[64 * i:64 * i + 64]))
    return lines


def sim_phy_log(nframes=60, offset_bytes=5, drop=None, invert=False):
    """a c6phyrx log: BERT bytes cut in 36-byte windows every 30 ms (drop: one index lost)"""
    data = m17.tx_bert(nframes)
    data = bytes(offset_bytes) + data
    if invert:
        data = bytes(b ^ 0xAA for b in data)          # 01<->11, 00<->10: every level negated
    lines = []
    for k in range(len(data) // 36):
        if k == drop:
            continue
        lines.append(f"mod say r {1000 + 30 * k} se=0 fe=0 d={data[36 * k:36 * k + 36].hex()}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m modem.m17rx", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wav")
    w.add_argument("out")
    w.add_argument("--tones-s", type=float, default=30.0)
    w.add_argument("--bert-s", type=float, default=30.0)
    t = sub.add_parser("tap")
    t.add_argument("log")
    t.add_argument("--wav")
    p = sub.add_parser("phy")
    p.add_argument("log")
    a = ap.parse_args(argv)
    if a.cmd == "wav":
        x, data = test_signal(a.tones_s, a.bert_s)
        m17.write_wav(a.out, np.round(x * 32767), FS)
        print(f"{a.out}: {len(x) / FS:.1f} s at {FS} Hz, multitone {a.tones_s:g} s then "
              f"{bert_frames(a.bert_s)} BERT frames; send with --dev 4800 (M17 2400 Hz outer)")
        return 0
    lines = open(a.log, errors="replace").read().splitlines()
    for ln in (tap_report(lines, a.wav) if a.cmd == "tap" else phy_report(lines)):
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
