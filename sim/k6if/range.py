"""PHY R (modem/k6r.py) through a model of the air and the K6's plain FSK receiver as a sign
sampler: the IC-7100's audio path (study.ic7100: pre-emphasis, 300 Hz high-pass, limiter, 3.1 kHz
low-pass), FM, white noise at a C/N0, the chip's channel filter, an FM discriminator,
de-emphasis and a 3 kHz audio low-pass, then the sign at 4800 a second (or at the bench's slower
sampler rate). C/N0 here is against thermal noise at the chip (the plain receiver is not limited
by the GP0 tap's own noise as the IF modes are, docs/k6-if-tap-sensitivity... see
docs/k6-range.md).

  python3 -m sim.k6if.range curve [--cn0 40,42,44] [--packets N] [--bw 4000] [--level 0.1]
  python3 -m sim.k6if.range nrz            the same packets as NRZ direct FSK (fails: the high-pass)
MIT licence."""
import argparse

import numpy as np
from scipy import signal

from modem import fsk, k6r
from . import frontend as fe
from . import study

FSA = study.FSA
FZ = study.FZ
FLAT_CH = fsk.Channel(pre_emph=300, emph_top=3000, hpf=None, lpf=None)


def flat_tx(audio):
    """a sender with no high-pass and pre-emphasis that the receiver's de-emphasis undoes: the
    POCSAG bench's NRZ runs of up to 10 bits came through whole (docs/k6-pocsag.md), which the
    IC-7100 model's 300 Hz high-pass would not let happen; the truth is between the two"""
    pre = FLAT_CH.apply(audio, FSA)
    d = np.clip(pre * study.HZ_PER_UNIT, -study.LIMIT, study.LIMIT)
    return signal.sosfilt(study.TX_LPF, d)


def chip_audio(audio, cn0=None, bw=4000.0, deemph=300.0, seed=1, f_off=0.0, tx="ic7100"):
    """sender audio at 48 kHz -> the discriminator's audio at 48 kHz, in Hz of deviation"""
    z = study.fm((study.ic7100 if tx == "ic7100" else flat_tx)(audio), f_off)
    if cn0 is not None:
        r = np.random.default_rng(seed)
        n0 = 10 ** (-cn0 / 10)
        z = z + r.normal(0, np.sqrt(n0 * FZ / 2), (len(z), 2)) @ [1, 1j]
    z = fe.chip_filter(z, FZ, bw)
    d = np.angle(z[1:] * np.conj(z[:-1])) * FZ / (2 * np.pi)
    d = signal.resample_poly(d, 1, FZ // FSA)
    if deemph:
        b, a = signal.butter(1, deemph, fs=FSA)
        d = signal.lfilter(b, a, d)
    return signal.sosfilt(signal.butter(4, 3000, fs=FSA, output="sos"), d)


def sample(d, rate=4800.0, t0=0.0, lock=None, osr=16):
    """the chip's signs (0/1) at `rate` a second from t0 s. lock=None: a steady clock. lock=g: a
    clock locked to the edges, as the chip's seems to be (bench run 2: runs of exactly 2 and 4
    samples at a rate of 3.93 a bit, a sample short now and then): it ticks osr times a sample,
    and at each change of sign moves its phase g of the way to half a sample before the next
    sample is taken"""
    if lock is None:
        t = np.arange(t0, len(d) / FSA - 1 / rate, 1 / rate)
        return (d[(t * FSA).astype(int)] > 0).astype(np.uint8)
    pos = np.arange(t0 * FSA, len(d) - 1, FSA / (rate * osr)).astype(int)
    v = d[pos] > 0
    out, ph, prev = [], 0.0, v[0]
    for x in v:
        if x != prev:
            ph += lock * (osr / 2 - ph)
            prev = x
        ph -= 1
        if ph <= 0:
            out.append(x)
            ph += osr
    return np.array(out, np.uint8)


def link(payloads, cn0=None, bw=4000.0, level=k6r.LEVEL, seed=1, rate=4800.0, pad=0.2, tx="ic7100",
         lock=None):
    """payloads -> (chip samples, start of the burst in samples)"""
    a = k6r.audio(k6r.burst_bits(payloads), FSA, level)
    z = np.zeros(int(pad * FSA))
    d = chip_audio(np.concatenate([z, a, z]), cn0, bw, seed=seed, tx=tx)
    return sample(d, rate, lock=lock), int(pad * rate)


def best_phase(smp):
    """the ideal receiver's bit phase (0..3): the one whose soft values are biggest"""
    return max(range(k6r.SPS), key=lambda p: np.abs(k6r.window_soft(smp, p)).sum())


def curve(cn0s, npk=8, bw=4000.0, level=k6r.LEVEL, seed=1):
    out = []
    for cn0 in cn0s:
        pls = k6r.test_payloads(npk, seed=seed)
        smp, _ = link(pls, cn0, bw, level, seed=seed)
        got = k6r.receive(smp, best_phase(smp))
        ok = sum(1 for p, c in got if c and p in pls)
        out.append((cn0, ok, npk))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("curve")
    c.add_argument("--cn0", default="40,41,42,43,44,46")
    c.add_argument("--packets", type=int, default=8)
    c.add_argument("--bw", type=float, default=4000.0)
    c.add_argument("--level", type=float, default=k6r.LEVEL)
    a = ap.parse_args(argv)
    if a.cmd == "curve":
        for cn0, ok, n in curve([float(x) for x in a.cn0.split(",")], a.packets, a.bw, a.level):
            print(f"{cn0:5.1f} dB-Hz: {ok}/{n} packets")


if __name__ == "__main__":
    main()
