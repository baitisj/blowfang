#!/usr/bin/env python3
"""DMR data bursts as FM transmit audio, and back: the IC-7100 test signal for the UV-380's HR_C6000
receiver (firmware/uv380-openrtx/mod/c6dmrrx.c, docs/uv380-c6000-modem.md), and the decoder for
what the UV-380 sends with c6dmrtx (an IC-7100 recording).

  python3 tools/uv380_dmrburst.py wav OUT.wav [--msg MSG.bit] [--amp 0.35] [--deemph] [--eq EQ.json]
  python3 tools/uv380_dmrburst.py decode IN.wav [--out MSG.bit]
  python3 tools/uv380_dmrburst.py fit C6DMRRX.log [--out EQ.json]
  python3 tools/uv380_dmrburst.py rx C6DMRRX.log [--out MSG.bit]

The signal is a repeater-style continuous stream (BS data sync, CACH, slot 2 idle) so a receiver
in passive timing locks on it: lead-in idle bursts, CSBK preambles, then the message as rate 1/2
data blocks in slot 1 (modem/dmrsotto.py: head, the ident text, sotto 1200 superframes, end),
then idle. Without --msg the message is a test pattern: 30 superframes counting up. Bursts are
built by sim/k6if/dmrdec.py (checked on 440.925's real traffic) and shaped with the DMR
transmitter's RRC (alpha 0.2), 4800 symbols a second, +-3 = full deviation at --amp of full scale.

--deemph runs the audio through a 750 us de-emphasis first, for a transmitter that pre-emphasises
(the IC-7100's FM does: its 1200 Hz AFSK arrives flat at the UV-380, which de-emphasises), so the
4FSK reaches the air flat. Which of the two decodes better on the UV-380 is a bench question.

fit reads the soft values a c6dmrrx run logged ("sf" lines) while the IC-7100 sent the plain
stream (same --msg / --max-sf / --id / --text / --cc as the wav), places each dump in the known
symbol stream, and fits the whole chain (RRC, IC-7100 audio and FM, AT1846S, the C6000's filter
and symbol timing) as a symbol-rate FIR from the symbols to the chip's levels. It prints the
chip's symbol error rate, the level of a +-3 symbol (24 is where the chip wants it: its inner /
outer threshold is 16) and writes a pre-equaliser: wav --eq sends the symbols through it before
the RRC, so the levels arrive where the chip wants them. fit --base EQ.json refines an equaliser
from a run that was sent with it. 11-16 bench: the IC-7100's audio path
(pre-emphasis and a low cut) closes the eye on the plain stream; --deemph alone does not open it.

rx reads the bursts a c6dmrrx run logged ("b" lines), counts them by data type, marks each
payload that is one of the stream's blocks, and unpacks the rate 1/2 blocks into the message.

Ident: the stream carries "AG7EW" in its text block; the bench tool adds the CW ID after it.

MIT licence."""
import argparse
import json
import os
import sys
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modem import dmrsotto  # noqa: E402
from sim.k6if import dmr, dmrdec as dd  # noqa: E402


def test_message(nsf=30):
    return bytes((k * 6 + i) & 255 for k in range(nsf) for i in range(6))


def stream_symbols(blocks, cc=1, lead=6, pre=3, tail=4, seed=0):
    """12-byte blocks -> 4800/s symbols: lead idle frames, pre CSBK preambles, the blocks as rate
    1/2 data (DT 7), tail idle frames; each 60 ms frame is slot 1's burst then slot 2's idle"""
    rng = np.random.default_rng(seed)
    ds = dd.bits(-np.array(dmr.PAT["bs"]))

    def bits96(by):
        return np.array([(b >> (7 - i)) & 1 for b in by for i in range(8)], np.uint8)

    s1 = [dd.burst_bits(cc, 9, rng.integers(0, 2, 196), ds, tc=0, rng=rng) for _ in range(lead)]
    a = [0xFF, 0xFF, 0xFF, 0, 0, 0]
    for k in range(pre):
        s1.append(dd.burst_bits(cc, 3, dd.csbk_bits([0x3D, 0, 0x80, pre - k + len(blocks)] + a, 0xA5A5),
                                ds, tc=0, rng=rng))
    for b in blocks:
        s1.append(dd.burst_bits(cc, 7, dd.bptc196_encode(bits96(b)), ds, tc=0, rng=rng))
    s1 += [dd.burst_bits(cc, 9, rng.integers(0, 2, 196), ds, tc=0, rng=rng) for _ in range(tail)]
    out = []
    for b in s1:
        out.append(dd.bits_to_symbols(b))
        out.append(dd.bits_to_symbols(dd.burst_bits(cc, 9, rng.integers(0, 2, 196), ds, tc=1, rng=rng)))
    return np.concatenate(out)


def deemphasis(x, fs, tau=750e-6):
    a = np.exp(-1.0 / (fs * tau))
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = a * acc + (1 - a) * v
        y[i] = acc
    return y


def preemphasis(y, fs, tau=750e-6):
    """deemphasis undone (a pre-emphasising transmitter as the receiver sees it)"""
    a = np.exp(-1.0 / (fs * tau))
    x = (y - a * np.concatenate([[0.0], y[:-1]])) / (1 - a)
    return x / max(1e-9, np.max(np.abs(x)))


def equalise(sym, eq):
    """symbols through the pre-equaliser {"taps": [...], "centre": m}"""
    g = np.asarray(eq["taps"], float)
    y = np.convolve(np.asarray(sym, float), g)
    return y[eq["centre"]:eq["centre"] + len(sym)]


def symbols_audio(sym, fs=48000, amp=0.35, deemph=False, pad_s=0.1, eq=None):
    """symbols -> float audio in [-1, 1]: +-3 symbols at +-amp after the RRC (before an eq's gain)"""
    sps = fs // dmr.BAUD
    assert sps * dmr.BAUD == fs, "fs must be a multiple of 4800"
    up = np.zeros(len(sym) * sps)
    up[::sps] = (equalise(sym, eq) if eq else np.asarray(sym, float)) * sps
    x = np.convolve(up, dmr.rrc(sps), "same") / 3.0
    if deemph:
        x = deemphasis(x, fs)
        x *= 1.0 / max(1e-9, np.percentile(np.abs(x), 99.5))
    x = np.concatenate([np.zeros(int(pad_s * fs)), x * amp, np.zeros(int(pad_s * fs))])
    return np.clip(x, -1.0, 1.0)


def write_wav(path, x, fs):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes((np.round(x * 32767)).astype("<i2").tobytes())


def read_wav(path):
    with wave.open(path, "rb") as w:
        fs, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    assert sw == 2, "16-bit wav only"
    x = np.frombuffer(raw, "<i2").astype(float) / 32768.0
    return x.reshape(-1, ch)[:, 0], fs


def decode(x, fs, thr=0.85):
    """audio -> (events, the rate 1/2 blocks in air order, dmrsotto.unpack's result or None)"""
    pol, ev = dd.decode_stream(dd.audio_symbols(x, fs), thr=thr)
    blocks = []
    for e in ev:
        if e.get("kind") == "data" and e.get("dtype") == 7 and "bytes" in e:
            blocks.append(bytes(int(v) for v in e["bytes"]))
    return ev, blocks, dmrsotto.unpack(blocks)


SOFT_N = 264


def _fields(line):
    """a module log line's words, without the i2sprobe logger's "mod say" prefix"""
    f = line.split()
    return f[2:] if f[:2] == ["mod", "say"] else f


def soft_dumps(lines):
    """c6dmrrx "sf T BASE OFF hex" lines -> the levels of each complete 264-value dump (132
    symbols): byte pairs are (L, 16 - |L|), 7-bit signed; dumps that don't fit are dropped"""
    parts = {}
    for ln in lines:
        f = _fields(ln)
        if len(f) == 5 and f[0] == "sf":
            parts.setdefault((f[1], f[2]), {})[int(f[3], 16)] = bytes.fromhex(f[4])
    out = []
    for key in sorted(parts, key=lambda k: (int(k[0]), k[1])):
        p = parts[key]
        raw = b"".join(p.get(o, b"") for o in range(0, SOFT_N, 66))
        if len(raw) != SOFT_N or any(v & 0x80 for v in raw):
            continue
        v = np.array([x - 128 if x & 0x40 else x for x in raw])
        lv, mg = v[0::2], v[1::2]
        if np.mean((mg == 16 - np.abs(lv)) | (np.abs(lv) == 16)) < 0.9:
            continue
        out.append(lv.astype(float))
    return out


# where each soft value comes from inside a 144-symbol slot (12 CACH, 49 info, 5 slot type, 24 sync,
# 5 slot type, 49 info): the chip stores slot type, sync and the 98 info symbols, not the CACH.
# 11-16 bench: 0x1C8 held slot type | sync | info, 0x0A8 sync | info | slot type
_ST = list(range(61, 66)) + list(range(90, 95))
_SYNC = list(range(66, 90))
_INFO = list(range(12, 61)) + list(range(95, 144))
LAYOUTS = {
    "st|sync|info": np.array(_ST + _SYNC + _INFO),
    "sync|info|st": np.array(_SYNC + _INFO + _ST),
    "sync|st|info": np.array(_SYNC + _ST + _INFO),
}


def _corr(a, b):
    a, b = a - a.mean(), b - b.mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def place(sym, lv, min_corr=0.3, margin=0.1):
    """-> (stream index of each of the dump's symbols, sign, layout) or None. Every burst has the
    same sync, so the burst is told by the other 108 symbols: the best must clear min_corr and
    beat the best of any other burst by margin"""
    s = np.asarray(sym, float)
    res = []
    for name, lay in LAYOUTS.items():
        keep = np.array([i not in _SYNC for i in lay])
        for b in range(len(s) // 144):
            idx = b * 144 + lay
            c = _corr(lv[keep], s[idx][keep])
            res.append((abs(c), c, name, idx))
    res.sort(key=lambda r: -r[0])
    other = next((r[0] for r in res[1:] if r[3][0] // 144 != res[0][3][0] // 144), 0.0)
    if res[0][0] < min_corr or res[0][0] - other < margin:
        return None
    return res[0][3], (1.0 if res[0][1] > 0 else -1.0), res[0][2]


def fit_channel(sym, dumps, pre=6, post=20):
    """sign * levels = h * symbols + c, h at symbol spacing from -pre to +post -> dict; sign is
    the chip's polarity (+1: it reads the symbols as sent)"""
    s = np.asarray(sym, float)
    rows, ys, truth, signs, layouts = [], [], [], [], []
    for lv in dumps:
        p = place(s, lv)
        if p is None:
            continue
        where, sg, name = p
        signs.append(sg)
        layouts.append(name)
        for k, n in enumerate(where):
            idx = n - np.arange(-pre, post + 1)
            if idx.min() < 0 or idx.max() >= len(s):
                continue
            rows.append(np.concatenate([s[idx], [1.0]]))
            ys.append(sg * lv[k])
            truth.append(s[n])
    if not rows:
        return None
    a, y, t = np.array(rows), np.array(ys), np.array(truth)
    x, *_ = np.linalg.lstsq(a, y, rcond=None)
    h, c = x[:-1], x[-1]
    sign = 1.0 if sum(signs) >= 0 else -1.0
    dec = np.select([y < -16, y < 0, y < 16], [-3, -1, 1], 3) * sign
    return {"h": h.tolist(), "pre": pre, "c": float(c), "dumps": len(dumps), "placed": len(signs),
            "sign": sign, "mixed": len(set(signs)) > 1, "layouts": sorted(set(layouts)),
            "rms": float(np.sqrt(np.mean((a @ x - y) ** 2))), "ser": float(np.mean(dec != t)),
            "outer": float(3 * h[pre])}


def design_eq(fit, ntaps=61, target=8.0, lam=0.1):
    """the pre-equaliser g with h * g ~ sign * target * delta (target 8: a +-3 symbol at level 24,
    in the chip's own polarity)"""
    h, pre = np.asarray(fit["h"]), fit["pre"]
    m = ntaps // 2
    n = len(h) + ntaps - 1
    a = np.zeros((n, ntaps))
    for j in range(ntaps):
        a[j:j + len(h), j] = h
    d = np.zeros(n)
    d[pre + m] = target * fit.get("sign", 1.0)
    g = np.linalg.solve(a.T @ a + lam * np.eye(ntaps), a.T @ d)
    return {"taps": g.tolist(), "centre": m}


def compose_eq(base, fit, ntaps=61, target=8.0, lam=0.1):
    """a run sent with the pre-equaliser base measures what is left (fit): the two in series,
    scaled to base's output power (the chip normalises its input level, clipping is what costs)"""
    g0 = np.asarray(base["taps"], float)
    r = design_eq(fit, ntaps, target, lam)
    g = np.convolve(g0, r["taps"])
    g *= np.linalg.norm(g0) / np.linalg.norm(g)
    return {"taps": g.tolist(), "centre": base["centre"] + r["centre"]}


def rx_bursts(lines):
    """c6dmrrx "b T sc= dt= crc= cc= ... d=HEX" lines -> dicts (t, dt, crc, cc, payload: 12 bytes)"""
    out = []
    for ln in lines:
        f = _fields(ln)
        if not f or f[0] != "b":
            continue
        kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
        if "d" not in kv:
            continue
        out.append({"t": int(f[1]), "sc": int(kv.get("sc", "0")), "dt": int(kv.get("dt", "0"), 16),
                    "crc": int(kv.get("crc", "1")), "cc": int(kv.get("cc", "0")),
                    "payload": bytes.fromhex(kv["d"])[:12]})
    return out


def stream_args(p):
    p.add_argument("--msg", help="sotto 1200 superframes (.bit); default a test pattern")
    p.add_argument("--max-sf", type=int, default=60, help="superframes at most (60 = 2.4 s)")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--text", default="AG7EW")
    p.add_argument("--cc", type=int, default=1)


def stream_for(a):
    msg = open(a.msg, "rb").read() if a.msg else test_message()
    msg = msg[:min(len(msg) - len(msg) % 6, 6 * a.max_sf)]
    blocks = dmrsotto.pack(msg, a.id, a.text)
    return msg, blocks, stream_symbols(blocks, a.cc)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wav")
    w.add_argument("out")
    stream_args(w)
    w.add_argument("--fs", type=int, default=48000)
    w.add_argument("--amp", type=float, default=0.35)
    w.add_argument("--deemph", action="store_true")
    w.add_argument("--eq", help="pre-equaliser from fit (use the --amp of the fitted run)")
    d = sub.add_parser("decode")
    d.add_argument("wav")
    d.add_argument("--out")
    d.add_argument("--thr", type=float, default=0.85)
    d.add_argument("--preemph", action="store_true", help="undo a 750 us de-emphasis first")
    f = sub.add_parser("fit")
    f.add_argument("log")
    stream_args(f)
    f.add_argument("--out", help="write the pre-equaliser here (json)")
    x = sub.add_parser("rx")
    x.add_argument("log")
    stream_args(x)
    x.add_argument("--out")
    f.add_argument("--taps", type=int, default=61)
    f.add_argument("--lam", type=float, default=0.1, help="regularisation: lower = sharper, louder")
    f.add_argument("--target", type=float, default=8.0, help="level per symbol unit (8: +-3 at 24)")
    f.add_argument("--base", help="the run was sent with this pre-equaliser: refine it")
    a = ap.parse_args()
    if a.cmd == "wav":
        msg, blocks, sym = stream_for(a)
        eq = json.load(open(a.eq)) if a.eq else None
        x = symbols_audio(sym, a.fs, a.amp, a.deemph, eq=eq)
        clip = float(np.mean(np.abs(x) >= 1.0))
        write_wav(a.out, x, a.fs)
        print(f"{a.out}: {len(blocks)} blocks, {len(msg) // 6} superframes, {len(x) / a.fs:.2f} s, "
              f"amp {a.amp}{' deemph' if a.deemph else ''}{' eq' if eq else ''}"
              f"{f' CLIPPED {clip:.1%}' if clip > 0.001 else ''}")
    elif a.cmd == "rx":
        _, sent, _ = stream_for(a)
        known = set(sent)
        bs = rx_bursts(open(a.log).read().splitlines())
        kinds, hits, blocks, seen = {}, 0, [], set()
        for b in bs:
            kinds[b["dt"]] = kinds.get(b["dt"], 0) + 1
            hit = b["payload"] in known
            hits += hit
            if b["dt"] == 7 and b["crc"] == 0 and b["payload"] not in seen:
                seen.add(b["payload"])
                blocks.append(b["payload"])
        print(f"bursts {len(bs)} data types {dict(sorted(kinds.items()))} payloads that are sent blocks "
              f"{hits} distinct rate-1/2 {len(blocks)} of {len(sent)} sent")
        r = dmrsotto.unpack(blocks)
        if r is None:
            print("no dmrsotto head block")
            return 1
        print({k: v for k, v in r.items() if k != "msg"}, f"{len(r['msg']) // 6} superframes")
        if a.out:
            open(a.out, "wb").write(r["msg"])
    elif a.cmd == "fit":
        _, _, sym = stream_for(a)
        r = fit_channel(sym, soft_dumps(open(a.log).read().splitlines()))
        if r is None:
            print("no soft dump placed in the stream")
            return 1
        print(f"dumps {r['dumps']} placed {r['placed']} ({' '.join(r['layouts'])}) chip SER {r['ser']:.3f} +-3 at level "
              f"{r['outer']:.1f} (24 wanted) fit rms {r['rms']:.1f}")
        print("h", " ".join(f"{v:+.1f}" for v in r["h"]))
        if r["sign"] < 0 or r["mixed"]:
            print(f"polarity: the chip reads the symbols {'inverted' if r['sign'] < 0 else ''}"
                  f"{' (mixed between dumps)' if r['mixed'] else ''}")
        if a.out:
            if a.base:
                with open(a.base) as fh:
                    eq = compose_eq(json.load(fh), r, a.taps, a.target, a.lam)
            else:
                eq = design_eq(r, a.taps, a.target, a.lam)
            eq["fit"] = r
            json.dump(eq, open(a.out, "w"), indent=1)
            print(f"{a.out}: {len(eq['taps'])} taps")
    else:
        x, fs = read_wav(a.wav)
        if a.preemph:
            x = preemphasis(x, fs)
        ev, blocks, r = decode(x, fs, a.thr)
        kinds = {}
        for e in ev:
            if e.get("kind") == "data":
                kinds[e["dtype"]] = kinds.get(e["dtype"], 0) + 1
        print(f"bursts {len(ev)} data types {dict(sorted(kinds.items()))} rate-1/2 blocks {len(blocks)}")
        if r is None:
            print("no dmrsotto head block")
            return 1
        print({k: v for k, v in r.items() if k != "msg"}, f"{len(r['msg']) // 6} superframes")
        if a.out:
            open(a.out, "wb").write(r["msg"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
