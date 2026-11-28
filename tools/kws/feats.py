"""Channel model + sotto 1200 frame features (and an audio log-mel baseline)."""
import subprocess, os, numpy as np
from scipy.signal import butter, sosfilt, lfilter
BIN = os.path.join(os.path.dirname(__file__), "../kws_feat")
SR = 8000
_hp = butter(4, 300, "hp", fs=SR, output="sos")
_lp = butter(6, 3000, "lp", fs=SR, output="sos")

def channel(x, rng, snr=None, kind="fm", perturb=False):
    """x int16 -> float waveform after a model FM voice channel (band-limit, limiter, noise, tilt)."""
    x = x.astype(np.float64) / 32768
    if perturb and rng.random() < 0.6:               # speed perturbation (pitch + formants + tempo)
        from scipy.signal import resample_poly
        up = int(rng.integers(18, 23)); x = resample_poly(x, up, 20)
    pad = lambda n: np.zeros(int(n * SR))
    x = np.concatenate([pad(rng.uniform(0.1, 0.6)), x, pad(rng.uniform(0.1, 0.6))])
    if kind == "clean":
        return x
    if kind == "narrow":                             # 146.800-style: steep HP, +6 dB/oct, LP ~2.7 kHz, low SNR
        from scipy.signal import butter as _b
        x = lfilter([1, -rng.uniform(0.6, 0.95)], [1], x)
        x = sosfilt(_b(int(rng.integers(4, 7)), rng.uniform(330, 560), "hp", fs=SR, output="sos"), x)
        x = sosfilt(_b(8, rng.uniform(2350, 2950), "lp", fs=SR, output="sos"), x)
        act = x[np.abs(x) > 0.05 * np.max(np.abs(x))]
        x = x / (np.sqrt(np.mean(act ** 2)) + 1e-9) * 0.15
        drive = rng.uniform(1, 3); x = np.tanh(x * drive) / np.tanh(drive) * 0.5
        if snr is None: snr = rng.uniform(3, 20)
        n = sosfilt(_lp, rng.standard_normal(len(x)))
        srms = np.sqrt(np.mean(x[np.abs(x) > 0.05] ** 2)) if np.any(np.abs(x) > 0.05) else 0.1
        x = x + n / np.sqrt(np.mean(n ** 2)) * srms * 10 ** (-snr / 20)
        if rng.random() < 0.3:
            k = int(SR * rng.uniform(0.08, 0.25)); x = np.concatenate([x, rng.standard_normal(k) * srms * 1.5])
        return x * 10 ** (rng.uniform(-20, 0) / 20)
    a = rng.uniform(-0.6, 0.6)                        # spectral tilt (pre/de-emphasis mismatch)
    x = lfilter([1, -a], [1], x) if a > 0 else lfilter([1], [1, a], x)
    x = sosfilt(_hp, x); x = sosfilt(_lp, x)
    act = x[np.abs(x) > 0.05 * np.max(np.abs(x))]
    x = x / (np.sqrt(np.mean(act ** 2)) + 1e-9) * 0.15
    drive = rng.uniform(1, 4)                         # deviation limiter
    x = np.tanh(x * drive) / np.tanh(drive) * 0.5
    if snr is None: snr = rng.uniform(0, 35)
    n = rng.standard_normal(len(x))
    if rng.random() < 0.5: n = lfilter([1], [1, -0.7], n)   # redder noise
    n = sosfilt(_lp, n)
    srms = np.sqrt(np.mean(x[np.abs(x) > 0.05] ** 2)) if np.any(np.abs(x) > 0.05) else 0.1
    x = x + n / np.sqrt(np.mean(n ** 2)) * srms * 10 ** (-snr / 20)
    if rng.random() < 0.2:                            # hum
        t = np.arange(len(x)) / SR
        x += srms * 10 ** (-rng.uniform(15, 35) / 20) * np.sin(2 * np.pi * rng.choice([60, 120]) * t)
    if rng.random() < 0.3:                            # squelch tail burst
        k = int(SR * rng.uniform(0.08, 0.25)); x = np.concatenate([x, rng.standard_normal(k) * srms * 1.5])
    x *= 10 ** (rng.uniform(-20, 0) / 20)             # receive level
    return x

def sotto(xf):
    pcm = np.clip(xf * 32768, -32767, 32767).astype("<i2").tobytes()
    out = subprocess.run([BIN], input=pcm, capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).reshape(-1, 22).copy()

def norm_sotto(f):
    """raw (code, wi, ei, c1..c19) -> model input (22): voiced flags, pitch, energy, shape."""
    code, wi, ei, c = f[:, 0], f[:, 1], f[:, 2], f[:, 3:]
    v = np.stack([(code == 1) | (code == 2), code == 2], 1).astype(np.float32)   # voiced, mixed
    pit = np.where(code > 0, wi / 127.0, 0.0)[:, None]
    e = (ei / 31.0)[:, None]
    return np.concatenate([v[:, :1], pit, e, c / 10.0], 1).astype(np.float32)

# audio baseline: 20 mel bands 100-3800 Hz, log, DCT -> 19 coeffs + log energy, 20 ms hop
def _mel(n=20, nfft=256):
    m = lambda f: 2595 * np.log10(1 + f / 700); im = lambda z: 700 * (10 ** (z / 2595) - 1)
    pts = im(np.linspace(m(100), m(3800), n + 2)); bins = np.fft.rfftfreq(nfft, 1 / SR)
    W = np.zeros((n, len(bins)))
    for i in range(n):
        l, c, r = pts[i:i + 3]
        W[i] = np.clip(np.minimum((bins - l) / (c - l), (r - bins) / (r - c)), 0, None)
    return W
_W = _mel(); _D = np.cos(np.pi / 20 * (np.arange(20)[:, None] + 0.5) * np.arange(20)[None, :])
def logmel(xf):
    hop, win = 160, 256
    xf = np.concatenate([xf, np.zeros(win)])
    nfr = (len(xf) - win) // hop
    fr = np.stack([xf[i * hop:i * hop + win] for i in range(nfr)]) * np.hanning(win)
    p = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    lm = 10 * np.log10(p @ _W.T + 1e-10)
    cep = lm @ _D
    return np.concatenate([(cep[:, :1] / 20 + 60 / 20) / 10 * 3, cep[:, 1:] / 50], 1).astype(np.float32)
