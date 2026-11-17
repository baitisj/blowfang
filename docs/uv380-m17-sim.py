"""Numbers behind docs/uv380-m17-scope.md: what the HR_C6000's 4FSK shape costs an M17
receiver, and what an 8 kHz receive tap with an unknown filter costs ours.

Noise free: each line is the inter-symbol interference alone, as a signal to ISI ratio at the
best sampling phase, and the worst eye opening (100 % = clean, below 0 = symbols cross over).
The AF filters are stand-ins (windowed-sinc low pass, one-pole high pass): the real ones are
what milestone 2 measures. numpy only.  python3 docs/uv380-m17-sim.py
"""
import numpy as np

RNG = np.random.default_rng(1)
FS, SPS, N = 48000, 10, 4000
SYMS = RNG.choice([-3, -1, 1, 3], N).astype(float)


def rrc(alpha, sps, span=8):
    t = np.arange(-span * sps, span * sps + 1) / sps
    h = np.zeros_like(t)
    for i, x in enumerate(t):
        if abs(x) < 1e-9:
            h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * x) - 1) < 1e-9:
            h[i] = alpha / np.sqrt(2) * ((1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha))
                                         + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha)))
        else:
            h[i] = (np.sin(np.pi * x * (1 - alpha)) + 4 * alpha * x * np.cos(np.pi * x * (1 + alpha))) \
                / (np.pi * x * (1 - (4 * alpha * x) ** 2))
    return h / np.sqrt(np.sum(h ** 2))


def lpf(fc, fs, n):
    t = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * fc / fs * t) * np.hamming(n)
    return h / h.sum()


def hp1(x, fc, fs):
    a, px, py, y = np.exp(-2 * np.pi * fc / fs), 0.0, 0.0, np.zeros_like(x)
    for i, v in enumerate(x):
        py = a * (py + v - px)
        px = v
        y[i] = py
    return y


def tx(alpha):
    up = np.zeros(N * SPS)
    up[::SPS] = SYMS
    h = rrc(alpha, SPS)
    return np.convolve(up, h), (len(h) - 1) // 2


def dec(v):
    return np.clip(2 * np.round((v - 1) / 2) + 1, -3, 3)


def score(y, d0, sps, mode="plain"):
    best = None
    for off in range(-(sps // 2), sps // 2 + 1):
        idx = d0 + off + np.arange(200, N - 200) * sps
        idx = idx[idx < len(y)]
        s, ref = y[idx], SYMS[200:200 + len(idx)]
        s = s * np.dot(ref, ref) / np.dot(s, ref)
        if mode == "dfe":   # decision-directed baseline restorer + 7-tap NLMS-style equaliser
            k_, w, base, out = 3, np.zeros(7), 0.0, []
            w[3] = 1
            for k in range(k_, len(s) - k_):
                win = s[k - k_:k + k_ + 1] - base
                v = np.dot(w, win)
                e = v - dec(v)
                base += 0.05 * e
                w -= 0.002 * e * win
                out.append(v)
            s, ref = np.array(out)[500:], ref[k_:len(ref) - k_][500:]
        e = s - ref
        snr = 10 * np.log10(np.mean(ref ** 2) / np.mean(e ** 2))
        eye = 1 - np.max(np.abs(e))
        if best is None or snr > best[0]:
            best = (snr, eye)
    return best


def tap(fc_lp, fc_hp):
    """Standard M17 (RRC 0.5) through an AF filter, read at 8 kHz, x3 to 24 kHz, RRC 0.5."""
    x, d = tx(0.5)
    f = lpf(fc_lp, FS, 255)
    y = np.convolve(x, f)
    d += (len(f) - 1) // 2
    if fc_hp:
        y = hp1(y, fc_hp, FS)
    up = np.zeros(len(y[::6]) * 3)
    up[::3] = y[::6] * 3
    ip = lpf(4000, 24000, 63)
    y24 = np.convolve(up, ip)
    h = rrc(0.5, 5)
    return np.convolve(y24, h), int(round(d / 2 + (len(ip) - 1) // 2 + (len(h) - 1) // 2))


def line(name, r):
    print(f"{name:52s} {r[0]:6.1f} dB  eye {r[1] * 100:7.1f} %")


if __name__ == "__main__":
    print("TX shaping into an M17 receiver (48 kHz, matched RRC 0.5 unless said)")
    for a in (0.5, 0.2):
        x, d = tx(a)
        h = rrc(0.5, SPS)
        line(f"  TX RRC {a} -> RX RRC 0.5", score(np.convolve(x, h), d + (len(h) - 1) // 2, SPS))
    x, d = tx(0.2)
    h = rrc(0.2, SPS)
    line("  TX RRC 0.2 -> RX RRC 0.2", score(np.convolve(x, h), d + (len(h) - 1) // 2, SPS))
    print("Standard M17 into an 8 kHz receive tap (low pass, high pass)")
    for lp in (3800, 3400, 3000, 2600):
        z, dz = tap(lp, 0)
        line(f"  LPF {lp} Hz, DC coupled", score(z, dz, 5))
        line(f"  LPF {lp} Hz, DC coupled, + equaliser", score(z, dz, 5, "dfe"))
    for hp in (20, 50, 100, 300):
        z, dz = tap(3400, hp)
        line(f"  LPF 3400 Hz, one-pole HP {hp} Hz, + restorer/eq", score(z, dz, 5, "dfe"))
