#!/usr/bin/env python3
"""DMR data bursts as FM transmit audio, and back: the IC-7100 test signal for the UV-380's HR_C6000
receiver (firmware/uv380-openrtx/mod/c6dmrrx.c, docs/uv380-c6000-modem.md), and the decoder for
what the UV-380 sends with c6dmrtx (an IC-7100 recording).

  python3 tools/uv380_dmrburst.py wav OUT.wav [--msg MSG.bit] [--amp 0.35] [--deemph]
  python3 tools/uv380_dmrburst.py decode IN.wav [--out MSG.bit]

The signal is a repeater-style continuous stream (BS data sync, CACH, slot 2 idle) so a receiver
in passive timing locks on it: lead-in idle bursts, CSBK preambles, then the message as rate 1/2
data blocks in slot 1 (modem/dmrsotto.py: head, the ident text, sotto 1200 superframes, end),
then idle. Without --msg the message is a test pattern: 30 superframes counting up. Bursts are
built by sim/k6if/dmrdec.py (checked on 440.925's real traffic) and shaped with the DMR
transmitter's RRC (alpha 0.2), 4800 symbols a second, +-3 = full deviation at --amp of full scale.

--deemph runs the audio through a 750 us de-emphasis first, for a transmitter that pre-emphasises
(the IC-7100's FM does: its 1200 Hz AFSK arrives flat at the UV-380, which de-emphasises), so the
4FSK reaches the air flat. Which of the two decodes better on the UV-380 is a bench question.

Ident: the stream carries "AG7EW" in its text block; the bench tool adds the CW ID after it.

MIT licence."""
import argparse
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


def symbols_audio(sym, fs=48000, amp=0.35, deemph=False, pad_s=0.1):
    """symbols -> float audio in [-1, 1]: +-3 symbols at +-amp after the RRC"""
    sps = fs // dmr.BAUD
    assert sps * dmr.BAUD == fs, "fs must be a multiple of 4800"
    up = np.zeros(len(sym) * sps)
    up[::sps] = np.asarray(sym, float) * sps
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wav")
    w.add_argument("out")
    w.add_argument("--msg", help="sotto 1200 superframes (.bit); default a test pattern")
    w.add_argument("--max-sf", type=int, default=60, help="superframes at most (60 = 2.4 s)")
    w.add_argument("--id", type=int, default=1)
    w.add_argument("--text", default="AG7EW")
    w.add_argument("--cc", type=int, default=1)
    w.add_argument("--fs", type=int, default=48000)
    w.add_argument("--amp", type=float, default=0.35)
    w.add_argument("--deemph", action="store_true")
    d = sub.add_parser("decode")
    d.add_argument("wav")
    d.add_argument("--out")
    d.add_argument("--thr", type=float, default=0.85)
    d.add_argument("--preemph", action="store_true", help="undo a 750 us de-emphasis first")
    a = ap.parse_args()
    if a.cmd == "wav":
        msg = open(a.msg, "rb").read() if a.msg else test_message()
        msg = msg[:min(len(msg) - len(msg) % 6, 6 * a.max_sf)]
        blocks = dmrsotto.pack(msg, a.id, a.text)
        sym = stream_symbols(blocks, a.cc)
        x = symbols_audio(sym, a.fs, a.amp, a.deemph)
        write_wav(a.out, x, a.fs)
        print(f"{a.out}: {len(blocks)} blocks, {len(msg) // 6} superframes, {len(x) / a.fs:.2f} s, "
              f"amp {a.amp}{' deemph' if a.deemph else ''}")
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
