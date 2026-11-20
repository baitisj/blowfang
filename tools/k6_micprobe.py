"""Read the K6 mic probe modules' dumps out of a serial log (docs/k6-mic-probe.md):

  python3 tools/k6_micprobe.py K6.LOG [--out DIR]

  voxcap (c/k6/mod/voxcap.c)  PA0, the VOX pin, sampled at 16 kHz while the K6's speaker plays
                              tones: does the pin carry the waveform, or only a loudness?
  afpin (c/k6/mod/afpin.c)    PA11, the beep pin, run as a one-pin delta-sigma converter on the
                              FD6818's audio output: noise with the output muted, the receiver's
                              hiss, each of REG_47's sixteen output selects, and a tone the
                              bench sends

  micpin (c/k6/mod/micpin.c)  the mic's audio on the AF pin (the mic ADC and TX DSP on, output
                              select 4), read by PA11: quiet, then with a tone at the mic
  micrec (c/k6/mod/micrec.c)  three seconds from the mic by that route, as IMA ADPCM: decoded,
                              and with --out written as a WAV (and one with the pre-emphasis undone)
  micprobe (c/k6/mod/micprobe.c)  the chip's mic ADC switched on: PA11's converter at each AF
                              output select, GP0 at each of its selects, every register, the
                              level meters and PA0, with the room quiet (q=0) and with a tone
                              at the mic (q=1): what follows the sound? (--tone HZ, default 1000)

It prints one line a capture and, with --out, writes each as a 16-bit WAV (and all of them in
captures.npz). The log is the bench logger's (lines with `K6 MOD say `) or the bare say lines.
SPDX-License-Identifier: MIT"""
import argparse
import os
import re
import sys
import wave

import numpy as np

VOX_FS = 16000.0
VOX_K = {"a": 27, "b": 16, "c": 8, "d": 4, "e": 8, "f": 8, "S": 8}     # voxcap's PLAN: half periods
VOX_ON = (512, 1536)                                                   # S: the tone's samples


def say_lines(text):
    """the modules' say lines of a log, in order"""
    out = []
    for ln in text.splitlines():
        m = re.search(r"K6 MOD say (.*)$", ln)
        out.append(m.group(1).rstrip() if m else ln.strip())
    return out


def sections(lines, name):
    """[[lines]] between each `NAME start` and `NAME done` (or the log's end)"""
    out, cur = [], None
    for ln in lines:
        if ln.startswith(name + " start"):
            cur = [ln]
            out.append(cur)
        elif cur is not None:
            cur.append(ln)
            if ln.startswith(name + " done"):
                cur = None
    return out


def captures(sec, name, digits):
    """{tag: (samples, {key: int})} of one section: the dump lines `T iii hhhh...` (digits hex
    digits a sample) and the `NAME T key=hex ...` line after them. A capture with a gap in its
    indices (a lost line) is kept up to the gap and marked {"short": 1}. The index is the
    sample's number modulo 0x1000."""
    data, out = {}, {}
    for ln in sec:
        m = re.fullmatch(r"(\w) ([0-9a-f]{3}) ((?:[0-9a-f]{%d})+)" % digits, ln)
        if m:
            tag, at, h = m.group(1), int(m.group(2), 16), m.group(3)
            d = data.setdefault(tag, {"x": [], "short": 0})
            if at != len(d["x"]) & 0xFFF:          # (three digits: a long capture's index wraps)
                d["short"] = 1
            elif not d["short"]:
                d["x"] += [int(h[i:i + digits], 16) for i in range(0, len(h), digits)]
            continue
        m = re.fullmatch(r"%s (\w) (.*)" % name, ln)
        if m and m.group(1) in data and "=" in m.group(2):
            d = data[m.group(1)]
            info = {k: int(v, 16) for k, v in (kv.split("=") for kv in m.group(2).split())}
            info["short"] = d["short"]
            out[m.group(1)] = (np.array(d["x"], float), info)
    return out


def tone_fit(x, fs, f):
    """(amplitude, residual rms) of the best sine at f in x (mean removed)"""
    x = x - x.mean()
    t = np.arange(len(x)) / fs
    a = np.stack([np.cos(2 * np.pi * f * t), np.sin(2 * np.pi * f * t)], 1)
    c, *_ = np.linalg.lstsq(a, x, rcond=None)
    return float(np.hypot(*c)), float(np.sqrt(np.mean((x - a @ c) ** 2)))


def band_rms(x, fs, lo, hi):
    """rms of x between lo and hi Hz"""
    x = x - x.mean()
    s = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / fs)
    s[(f < lo) | (f > hi)] = 0
    return float(np.sqrt(np.mean(np.fft.irfft(s, len(x)) ** 2)))


def peak_tone(x, fs, lo=250.0, hi=3400.0):
    """the strongest tone between lo and hi: (Hz, amplitude, SINAD dB in lo..hi)"""
    x = x - x.mean()
    n = len(x)
    s = np.abs(np.fft.rfft(x * np.hanning(n), 8 * n))
    f = np.fft.rfftfreq(8 * n, 1 / fs)
    k = (f >= lo) & (f <= hi)
    f0, span = float(f[k][np.argmax(s[k])]), fs / n / 4
    t = np.arange(n) / fs

    def fit(fq):
        a = np.stack([np.cos(2 * np.pi * fq * t), np.sin(2 * np.pi * fq * t)], 1)
        c, *_ = np.linalg.lstsq(a, x, rcond=None)
        return a @ c, float(np.hypot(*c))
    for _ in range(3):                             # the FFT's grid is too coarse for a clean fit
        cand = np.linspace(f0 - span, f0 + span, 21)
        f0 = float(cand[np.argmin([np.sum((x - fit(fq)[0]) ** 2) for fq in cand])])
        span /= 8
    y, amp = fit(f0)
    rest = band_rms(x - y, fs, lo, hi)
    return f0, amp, float(20 * np.log10(max(amp, 1e-12) / np.sqrt(2) / max(rest, 1e-12)))


def vox_report(caps):
    """voxcap's captures -> lines"""
    out = []
    q = caps.get("Q")
    rest = float(q[0].std()) if q else float("nan")
    for tag, (x, info) in caps.items():
        ln = f"voxcap {tag}: n={len(x)} mean={x.mean():.1f} rms={x.std():.2f} min={x.min():.0f} max={x.max():.0f}" \
             f" late={info.get('late', 0)} conv={info.get('conv', 0) / 16:.1f} us"
        if tag in VOX_K and tag != "S" and len(x) >= 256:
            f0 = VOX_FS / (2 * VOX_K[tag])
            a1, _ = tone_fit(x, VOX_FS, f0)
            a2, _ = tone_fit(x, VOX_FS, 2 * f0)
            ln += f" | tone {f0:.0f} Hz: amp {a1:.2f}, at twice {a2:.2f}, rest noise {rest:.2f}"
        if tag == "S" and len(x) >= VOX_ON[1] + 256:
            a, b = VOX_ON
            base, top = x[:a].mean(), x[a + (b - a) // 2:b].mean()
            ln += f" | level off {base:.1f} on {top:.1f}"
            if abs(top - base) > 4 * max(rest, 0.5):
                up = x[a:b] - base
                dn = x[b:] - base
                r = np.argmax(np.abs(up) >= 0.63 * abs(top - base))
                d = np.argmax(np.abs(dn) <= 0.37 * abs(top - base))
                ln += f", rise {1000 * r / VOX_FS:.1f} ms, fall {1000 * d / VOX_FS:.1f} ms"
                a1, _ = tone_fit(x[a + (b - a) // 2:b], VOX_FS, 1000.0)
                a2, _ = tone_fit(x[a + (b - a) // 2:b], VOX_FS, 2000.0)
                ln += f", 1 kHz ripple {a1:.2f}, 2 kHz {a2:.2f}"
        if info.get("short"):
            ln += " (lines lost)"
        out.append(ln)
    return out


def af_rate(info, n):
    """afpin's sample rate from a capture's length in 100 us ticks"""
    return n / (info["ticks"] * 1e-4) if info.get("ticks") else float("nan")


def af_report(caps, fs=None, name="afpin"):
    """afpin's captures -> lines. fs: {way: sample rate at 90 turns a sample}, if the ticks
    aren't to be trusted. A capture is compared with the muted one of its drive time (m, or the
    select sweep's A; micpin's quiet one, Q), else with any muted one. name: the module's."""
    out = []
    ref = {}
    for tag, (x, info) in caps.items():
        way, hold, w = info.get("way", 0), info.get("hold", 0), info["w"]
        r = fs[way] * 90 / w if fs else af_rate(info, len(x))
        y = x / w ** 2                             # the triangle-weighted count's full scale
        b = band_rms(y, r, 300, 3000)
        ln = f"{name} {tag}: hold {hold} w {w} n={len(x)} fs={r:.0f} Hz mean={y.mean():.3f} rms={y.std():.4f} " \
             f"300-3000 Hz rms={b:.5f}"
        if "sel" in info:
            ln += f" sel {info['sel']}"
        if "rssi" in info:
            ln += f" rssi {info['rssi']} dBm"
        if tag == "Q" or info.get("sel", 0 if tag in "mn" else 1) == 0:   # (the first run's lines had no sel)
            ref[hold] = b
        else:
            if ref:
                q = ref.get(hold, next(iter(ref.values())))
                ln += f" ({20 * np.log10(max(b, 1e-9) / max(q, 1e-9)):+.1f} dB on muted)"
            f0, amp, sinad = peak_tone(y, r)
            ln += f" | peak {f0:.0f} Hz amp {amp:.4f} SINAD {sinad:.1f} dB"
        if info.get("short"):
            ln += " (lines lost)"
        out.append(ln)
    return out


LVL_ST = {1: "mic ADC off", 2: "mic route", 3: "AGC bit flipped"}


def lvl_report(caps, tone=1000.0):
    """miclvl's captures, a line each: when (ms after the sound), the kind (st), REG_19, the
    tone's amplitude and the rest of 300-3000 Hz, both as shares of full scale"""
    out = []
    for tag, (x, info) in caps.items():
        if len(x) < 64 or not info.get("ticks"):
            continue
        fs, full = af_rate(info, len(x)), float(info.get("w", 90)) ** 2
        t = np.arange(len(x)) / fs
        a = np.stack([np.cos(2 * np.pi * tone * t), np.sin(2 * np.pi * tone * t)], 1)
        c, *_ = np.linalg.lstsq(a, x - x.mean(), rcond=None)
        rest = band_rms(x - x.mean() - a @ c, fs, 300, 3000)
        out.append(f"miclvl {tag}: {info['ms'] / 1000:6.2f} s  {LVL_ST.get(info['st'], info['st']):<15} "
                   f"19={info['19']:04x}  rest {x.mean() / full:.3f}  {tone:.0f} Hz {np.hypot(*c) / full:.5f}  "
                   f"others {rest / full:.5f}" + ("  (lines lost)" if info["short"] else ""))
    return out


def cfg_report(caps, name, tone=1300.0):
    """micdisc's and mickey's captures, a line each: the settings its line gives, then the mean,
    300-3000 Hz rms and the tone's amplitude, as shares of full scale (w 90)"""
    out = []
    for tag, (x, info) in caps.items():
        if len(x) < 26 or not info.get("ticks"):
            continue
        fs, y = af_rate(info, len(x)), x / 8100.0
        keys = " ".join(f"{k}={v:x}" for k, v in info.items() if k not in ("ticks", "short", "rssi", "n"))
        out.append(f"{name} {tag}: {keys:<24} mean {y.mean():.3f}  300-3000 Hz {band_rms(y, fs, 300, 3000):.5f}  "
                   f"{tone:.0f} Hz {tone_fit(y, fs, tone)[0]:.5f}" + ("  (lines lost)" if info["short"] else ""))
    return out


def mic_runs(lines):
    """micprobe's sections -> [{"caps": {(kind, q, st, sel): (samples, info)}, "regs": {(q, st):
    {reg: (least, most)}}, "a0": {(q, st): info}, "notes": [lines]}]. A capture's d lines come
    before its `micprobe KIND q= st= sel= ...` line (none: the samples did not move)."""
    out = []
    for sec in sections(lines, "micprobe"):
        run = {"caps": {}, "regs": {}, "a0": {}, "notes": [sec[0]]}
        x, short, regs = [], 0, {}
        for ln in sec[1:]:
            m = re.fullmatch(r"d ([0-9a-f]{3}) ((?:[0-9a-f]{4})+)", ln)
            if m:
                if int(m.group(1), 16) != len(x):
                    short = 1
                elif not short:
                    h = m.group(2)
                    x += [int(h[i:i + 4], 16) for i in range(0, len(h), 4)]
                continue
            m = re.fullmatch(r"r ([0-9a-f]{2})((?: [0-9a-f]{8})+)", ln)
            if m:
                for i, h in enumerate(m.group(2).split()):
                    regs[int(m.group(1), 16) + i] = (int(h[:4], 16), int(h[4:], 16))
                continue
            m = re.fullmatch(r"micprobe (af|gp|mt|rg|a0) (.*)", ln)
            if not m:
                run["notes"].append(ln)
                continue
            info = {k: int(v, 16) for k, v in (kv.split("=") for kv in m.group(2).split())}
            key = (info["q"], info["st"])
            if m.group(1) == "rg":
                run["regs"][key], regs = regs, {}
            elif m.group(1) == "a0":
                run["a0"][key] = info
            else:
                info["short"] = short
                run["caps"][(m.group(1),) + key + (info.get("sel", info.get("reg")),)] = (np.array(x, float), info)
            x, short = [], 0
        out.append(run)
    return out


MIC_ST = ("as found", "mic ADC", "mic ADC + TX DSP")
MIC_FULL = {"af": 8100.0, "gp": 600.0}


def mic_report(run, tone=1000.0, fs=None, full=None):
    """one micprobe run -> lines: per capture the quiet and the sounding pass side by side, and
    what moved with the sound. fs: {kind: sample rate} and full: {kind: full scale}, if the
    ticks and the module's defaults aren't to be trusted."""
    full = full or MIC_FULL
    out = list(run["notes"])
    hits = []

    def look(kind, x, info):
        if not len(x):
            return info["lo"] / full[kind], 0.0, 0.0
        r = fs[kind] if fs else af_rate(info, len(x))
        y = x / full[kind]
        return float(y.mean()), band_rms(y, r, 300, 3000), tone_fit(y, r, tone)[0]
    for kind in ("af", "gp"):
        for st in range(3):
            for sel in range(16):
                a, b = run["caps"].get((kind, 0, st, sel)), run["caps"].get((kind, 1, st, sel))
                if not a or not b:
                    continue
                (m0, r0, t0), (m1, r1, t1) = look(kind, *a), look(kind, *b)
                ln = f"{kind} st {st} sel {sel:2d}: quiet mean {m0:.3f} rms {r0:.5f} tone {t0:.5f}" \
                     f" | sound mean {m1:.3f} rms {r1:.5f} tone {t1:.5f}"
                if t1 > 4 * max(t0, 2e-4) or abs(m1 - m0) > 0.05:
                    ln += "  <-- follows the sound"
                    hits.append(f"{kind} st {st} ({MIC_ST[st]}) sel {sel}")
                out.append(ln)
    for st in range(3):
        for reg in (0x64, 0x6F):
            a, b = run["caps"].get(("mt", 0, st, reg)), run["caps"].get(("mt", 1, st, reg))
            if a and b:
                ln = f"mt st {st} reg {reg:02x}: quiet {a[1]['lo']:04x}..{a[1]['hi']:04x} | sound {b[1]['lo']:04x}..{b[1]['hi']:04x}"
                if b[1]["lo"] > a[1]["hi"] or b[1]["hi"] < a[1]["lo"]:
                    ln += "  <-- follows the sound"
                    hits.append(f"REG_{reg:02X} st {st} ({MIC_ST[st]})")
                out.append(ln)
        a, b = run["a0"].get((0, st)), run["a0"].get((1, st))
        if a and b:
            out.append(f"a0 st {st}: quiet {a['lo']}..{a['hi']} mean {a['sum'] / 64:.1f} | sound {b['lo']}..{b['hi']}"
                       f" mean {b['sum'] / 64:.1f}")
        a, b = run["regs"].get((0, st), {}), run["regs"].get((1, st), {})
        base = run["regs"].get((0, 0), {})
        for reg in sorted(set(a) & set(b)):
            if reg == 0x5F:
                continue
            (l0, h0), (l1, h1) = a[reg], b[reg]
            apart = l1 > h0 or h1 < l0
            ln = f"rg st {st} reg {reg:02x}: quiet {l0:04x}..{h0:04x} | sound {l1:04x}..{h1:04x}"
            if apart:
                out.append(ln + "  <-- apart")
                hits.append(f"REG_{reg:02X} st {st} ({MIC_ST[st]})")
            elif st and reg in base and (l0, h0) != base[reg] and (l0 > base[reg][1] or h0 < base[reg][0]):
                out.append(ln + f"  (as found {base[reg][0]:04x}..{base[reg][1]:04x}: the state moved it)")
    out.append("follows the sound: " + ("; ".join(dict.fromkeys(hits)) if hits else "nothing"))
    return out


IMA_STEPS = (7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88,
             97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
             724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660,
             4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818,
             18500, 20350, 22385, 24623, 27086, 29794, 32767)


def ima_decode(data):
    """4-bit IMA ADPCM, a byte's low four bits first -> samples"""
    out, pred, index = [], 0, 0
    for b in data:
        for c in (b & 15, b >> 4):
            step = IMA_STEPS[index]
            q = (step >> 3) + (step if c & 4 else 0) + (step >> 1 if c & 2 else 0) + (step >> 2 if c & 1 else 0)
            pred = max(-32768, min(32767, pred - q if c & 8 else pred + q))
            index = max(0, min(88, index + (2 * (c & 3) + 2 if c & 4 else -1)))
            out.append(pred)
    return out


def rec_runs(lines):
    """micrec's sections -> [(samples in the converter's counts, {key: int}, notes)]: the `p`
    lines' bytes decoded and scaled back (the module codes 8 times the sample's distance from
    its running mean). Bytes of a lost line are zeros, and info["short"] counts the lines."""
    out = []
    for sec in sections(lines, "micrec"):
        data, info, notes, short = bytearray(), {}, [sec[0]], 0
        for ln in sec[1:]:
            m = re.fullmatch(r"p ([0-9a-f]{4}) ((?:[0-9a-f]{2})+)", ln)
            if m:
                at = int(m.group(1), 16)
                if at > len(data):
                    short += 1
                    data += bytes(at - len(data))
                data[at:] = bytes.fromhex(m.group(2))
                continue
            m = re.fullmatch(r"micrec p (.*)", ln)
            if m:
                info = {k: int(v, 16) for k, v in (kv.split("=") for kv in m.group(1).split())}
            else:
                notes.append(ln)
        if info:
            info["short"] = short
            out.append((np.array(ima_decode(data), float) / 8, info, notes))
    return out


def deemph(x, fs, corner=300.0):
    """the transmit side's pre-emphasis undone: one pole at corner Hz, and the hum under it off"""
    s = np.fft.rfft(x - x.mean())
    f = np.fft.rfftfreq(len(x), 1 / fs)
    s = s / (1 + 1j * f / corner)
    s[f < 100] = 0
    return np.fft.irfft(s, len(x))


def write_wav(path, x, fs):
    x = x - x.mean()
    x = x / max(1e-9, np.abs(x).max()) * 20000
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(round(fs)))
        w.writeframes(x.astype("<i2").tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--out", help="write WAVs and captures.npz here")
    ap.add_argument("--tone", type=float, default=1000.0, help="the sounding tone, Hz (default 1000)")
    a = ap.parse_args()
    lines = say_lines(open(a.log, errors="replace").read())
    keep = {}
    for i, sec in enumerate(sections(lines, "voxcap")):
        caps = captures(sec, "voxcap", 3)
        print(sec[0])
        print("\n".join(vox_report(caps)))
        for tag, (x, _) in caps.items():
            keep[f"voxcap{i}_{tag}"] = (x, VOX_FS)
    for name in ("afpin", "micpin"):
        for i, sec in enumerate(sections(lines, name)):
            caps = captures(sec, name, 4)
            print(sec[0])
            print("\n".join(ln for ln in sec if re.match(name + r" (carrier|no carrier|sound|no sound|check)", ln)))
            print("\n".join(af_report(caps, name=name)))
            for tag, (x, info) in caps.items():
                keep[f"{name}{i}_{tag}"] = (x, af_rate(info, len(x)))
    for i, sec in enumerate(sections(lines, "miclvl")):
        caps = captures(sec, "miclvl", 4)
        print(sec[0])
        print("\n".join(ln for ln in sec if re.match(r"miclvl (sound|no sound|check)", ln)))
        print("\n".join(lvl_report(caps, a.tone)))
        for tag, (x, info) in caps.items():
            keep[f"miclvl{i}_{tag}"] = (x, af_rate(info, len(x)))
    for name in ("micdisc", "mickey"):
        for i, sec in enumerate(sections(lines, name)):
            caps = captures(sec, name, 4)
            print("\n".join(ln for ln in sec if ln.startswith(name + " ") and " ticks=" not in ln))
            print("\n".join(cfg_report(caps, name, a.tone)))
            for tag, (x, info) in caps.items():
                keep[f"{name}{i}_{tag}"] = (x, af_rate(info, len(x)))
    for i, (x, info, notes) in enumerate(rec_runs(lines)):
        r = af_rate(info, len(x))
        y = x / info["w"] ** 2
        print("\n".join(notes))
        print(f"micrec {i}: n={len(x)} fs={r:.0f} Hz ({len(x) / r:.2f} s) rest={info['rest'] / info['w'] ** 2:.3f} "
              f"rms={y.std():.5f} peak={np.abs(y).max():.5f} 300-3000 Hz rms={band_rms(y, r, 300, 3000):.5f} "
              f"clipped={info['over']}" + (f" ({info['short']} lines lost)" if info["short"] else ""))
        keep[f"micrec{i}"] = (x, r)
        keep[f"micrec{i}_deemph"] = (deemph(x, r), r)
    runs = mic_runs(lines)
    for i, run in enumerate(runs):
        print("\n".join(mic_report(run, a.tone)))
        for (kind, q, st, sel), (x, info) in run["caps"].items():
            if kind == "af" and len(x):
                keep[f"micprobe{i}_af_q{q}_st{st}_sel{sel}"] = (x, af_rate(info, len(x)))
    if not keep and not runs:
        print("no voxcap, afpin, micpin, miclvl, micdisc, mickey, micrec or micprobe capture in the log")
        return 1
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        np.savez(os.path.join(a.out, "captures.npz"), **{k: v[0] for k, v in keep.items()},
                 **{k + "_fs": v[1] for k, v in keep.items()})
        for k, (x, fs) in keep.items():
            if len(x) and np.isfinite(fs):
                write_wav(os.path.join(a.out, k + ".wav"), x, fs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
