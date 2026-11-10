"""APRS (Bell 202 AFSK on FM) through the K6's receive model: aprsclk's discriminator against a
noncoherent matched filter on the IF, in float, for the packet rate at each C/N0.

Both detectors see the same complex samples aprsclk makes (counts mixed down by the nominal IF,
a 2nd-order CIC, decimated by 6: ~14.3 kHz) and share its bit clock, NRZI and HDLC, so the only
difference is the mark / space decision each sample:

  disc: the phase step (FM discriminator), DC removed, correlated over one bit with 1200 and
        2200 Hz (aprsclk.c's audio()).
  mf:   the IF itself, turned back by the discriminator's DC (the carrier offset), correlated
        over one bit with exp(j beta sin(2 pi f t + theta)) for each tone and P audio phases,
        beta = deviation / f; the tone's metric is the largest |correlation| over its phases.

  python3 -m sim.k6if.aprs_mf [--cn0 46 48 50 52] [--dev 3000] [--twist 0] [--n 24] [--tracked]

  --tracked: the matched filter's beta from each tone's discriminator level (dev_track), as the
  module does, instead of a fixed --mf-dev. The module (c/k6/mod/aprsclk.c) sums over each bit
  between its clock's ticks; this slides a window every sample (a little better, too slow on the M0).

SPDX-License-Identifier: MIT"""
import argparse

import numpy as np

from modem import aprs
from sim.k6if import frontend as fe, study as st

FDMA = 5.49e6
FC = FDMA / 64          # counts a second
DEC = 6
FS = FC / DEC           # ~14.3 kHz complex


def scene(npk, cn0, dev=3000.0, twist=0.0, f_off=0.0, seed=1, gap=0.25):
    """npk packets of assorted lengths back to back; returns (counts, the frames)"""
    rng = np.random.default_rng(seed)
    frames, parts = [], []
    for k in range(npk):
        info = "/%06dh4857.00N/12229.00W>" % rng.integers(0, 235959) + "x" * int(rng.integers(5, 40))
        f = aprs.frame("VE7A%02d-%d" % (k % 100, k % 16), "APRS", info)
        frames.append(f)
        x, _ = aprs.packet_audio([f], fs=st.FSA, twist_db=twist, pre_flags=24)
        parts += [np.zeros(int(gap * st.FSA)), x]
    a = np.concatenate(parts + [np.zeros(int(gap * st.FSA))])
    s = st.fm(a * dev / max(1.0, 10 ** (twist / 20)), f_off)
    c = fe.receive(s, st.FZ, cn0, bw=8000.0, seed=seed, L=64, fdma=FDMA)
    return c.astype(np.float64), frames


def baseband(c):
    n = np.arange(len(c))
    z = c * np.exp(-2j * np.pi * fe.FIF * n / FC)
    k = np.ones(DEC)
    z = np.convolve(np.convolve(z, k), k)[DEC - 1::DEC]
    return z


def disc_metrics(z, nwin):
    ang = np.angle(z)
    d = np.angle(np.exp(1j * np.diff(ang, prepend=ang[0])))
    # aprsclk: d -= dc >> 8; dc += d  (a one-pole high-pass, about 9 Hz)
    out = np.empty_like(d)
    dc = 0.0
    for i, v in enumerate(d):
        v -= dc / 256
        dc += v
        out[i] = v
    t = np.arange(len(z)) / FS
    m = np.abs(np.convolve(out * np.exp(-2j * np.pi * 1200 * t), np.ones(nwin))[:len(z)])
    s = np.abs(np.convolve(out * np.exp(-2j * np.pi * 2200 * t), np.ones(nwin))[:len(z)])
    return m, s, d


def mf_metrics(z, nwin, dev=3000.0, P=8, dc_tau=256, betas=None):
    ang = np.angle(z)
    d = np.angle(np.exp(1j * np.diff(ang, prepend=ang[0])))
    # the carrier offset from the discriminator's slow mean, as aprsclk keeps it
    off = np.empty_like(d)
    dc = 0.0
    for i, v in enumerate(d):
        off[i] = dc / dc_tau
        dc += v - dc / dc_tau
    zr = z * np.exp(-1j * np.cumsum(off))
    t = np.arange(len(z)) / FS
    res = []
    for f in (1200.0, 2200.0):
        bs = betas[f] if betas else [dev / f]
        best = np.zeros(len(z))
        for b in bs:
            for p in range(P):
                g = np.exp(1j * b * np.sin(2 * np.pi * f * t + 2 * np.pi * p / P))
                c = np.abs(np.convolve(zr * np.conj(g), np.ones(nwin))[:len(z)])
                best = np.maximum(best, c)
        res.append(best)
    return res[0], res[1]


DEVS = np.arange(1250.0, 5001.0, 250.0)   # the deviation grid the tracker picks from


def dev_track(z, nwin, K=64):
    """each tone's deviation (Hz) as the discriminator sees it, averaged over the samples where
    that tone wins (decision-directed, a one-pole of K samples): returns two arrays"""
    m, s, _ = disc_metrics(z, nwin)
    scale = FS / (np.pi * nwin)
    dm, ds = 3000.0, 3000.0
    om, os_ = np.empty(len(z)), np.empty(len(z))
    for i in range(len(z)):
        if m[i] > s[i]:
            dm += (m[i] * scale - dm) / K
        else:
            ds += (s[i] * scale - ds) / K
        om[i], os_[i] = dm, ds
    return om, os_


def mf_tracked(z, nwin, P=16, K=64, dc_tau=256, bias=1.0):
    """the matched filter with each tone's beta from dev_track, picked from the DEVS grid"""
    dm, ds = dev_track(z, nwin, K)
    ang = np.angle(z)
    d = np.angle(np.exp(1j * np.diff(ang, prepend=ang[0])))
    off = np.empty_like(d)
    dc = 0.0
    for i, v in enumerate(d):
        off[i] = dc / dc_tau
        dc += v - dc / dc_tau
    zr = z * np.exp(-1j * np.cumsum(off))
    t = np.arange(len(z)) / FS
    res = []
    for f, de in ((1200.0, dm), (2200.0, ds)):
        idx = np.clip(np.round((de * bias - DEVS[0]) / 250.0).astype(int), 0, len(DEVS) - 1)
        best = np.zeros(len(z))
        for k in np.unique(idx):
            sel = idx == k
            bk = np.zeros(len(z))
            for p in range(P):
                g = np.exp(1j * DEVS[k] / f * np.sin(2 * np.pi * f * t + 2 * np.pi * p / P))
                bk = np.maximum(bk, np.abs(np.convolve(zr * np.conj(g), np.ones(nwin))[:len(z)]))
            best[sel] = bk[sel]
        res.append(best)
    return res[0], res[1]


class Hdlc:
    def __init__(self):
        self.sr, self.inf, self.cur, self.frames, self.pre, self.run = 0, False, [], [], 0, 0

    def bit(self, b):
        self.sr = ((self.sr << 1) | b) & 0xFF
        if self.sr == 0x7E:
            if self.inf and len(self.cur) >= 18 * 8 and len(self.cur) % 8 == 7:
                c = self.cur[:-7]  # the flag's first six bits went in before it was seen
                by = bytes(sum(c[i + k] << k for k in range(8)) for i in range(0, len(c), 8))
                if aprs.crc16(by[:-2]) == by[-2] | by[-1] << 8:
                    self.frames.append(by)
            self.run = self.run + 1 if self.inf and len(self.cur) == 7 else 0
            if self.run == 3:
                self.pre += 1
            self.inf, self.cur = True, []
            return
        if self.sr & 0x7F == 0x7F:
            self.inf = False
            return
        if not self.inf:
            return
        if self.sr & 0x3F == 0x3E:
            return  # a stuffed zero
        self.cur.append(b)


def bits_and_frames(m, s, spb):
    """aprsclk's bit clock, NRZI and HDLC over the decisions m > s; returns good frames"""
    v = (m > s).astype(int)
    stb = int(round(2 ** 32 / spb)) & 0xFFFFFFFF
    pll = 0
    lastv = lastbit = 0
    h = Hdlc()
    for x in v:
        prev = pll
        pll = (pll + stb) & 0xFFFFFFFF
        if prev < 2 ** 31 <= pll:
            h.bit(int(x == lastbit))
            lastbit = x
        if x != lastv:
            p = pll - 2 ** 32 if pll >= 2 ** 31 else pll
            p = p - (p >> 2) if h.inf else p >> 1
            pll = p & 0xFFFFFFFF
            lastv = x
    return h.frames


def run(cn0, n, seed, **kw):
    dev = kw.get("dev", 3000.0)
    c, frames = scene(n, cn0, seed=seed, dev=dev, twist=kw.get("twist", 0.0), f_off=kw.get("f_off", 0.0))
    z = baseband(c)
    spb = FS / 1200
    nwin = int(round(spb))
    want = {bytes(f) for f in frames}
    m, s, _ = disc_metrics(z, nwin)
    got_d = len(want & set(bits_and_frames(m, s, spb)))
    if kw.get("tracked"):
        m2, s2 = mf_tracked(z, nwin + 1, P=16)
    else:
        m2, s2 = mf_metrics(z, nwin, dev=kw.get("mf_dev", 3000.0), P=kw.get("P", 8), betas=kw.get("betas"))
    got_m = len(want & set(bits_and_frames(m2, s2, spb)))
    return got_d, got_m, len(want)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cn0", type=float, nargs="+", default=[44, 46, 48, 50, 52])
    ap.add_argument("--dev", type=float, default=3000.0)
    ap.add_argument("--mf-dev", type=float, default=3000.0)
    ap.add_argument("--twist", type=float, default=0.0)
    ap.add_argument("--f-off", type=float, default=0.0)
    ap.add_argument("--P", type=int, default=8)
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--tracked", action="store_true")
    a = ap.parse_args()
    for cn0 in a.cn0:
        td = tm = tt = 0
        for sd in range(a.seeds):
            d, m, t = run(cn0, a.n, 10 + sd, dev=a.dev, mf_dev=a.mf_dev, twist=a.twist, f_off=a.f_off, P=a.P,
                          tracked=a.tracked)
            td, tm, tt = td + d, tm + m, tt + t
        print("cn0 %.0f  disc %d/%d  mf %d/%d" % (cn0, td, tt, tm, tt), flush=True)


if __name__ == "__main__":
    main()
