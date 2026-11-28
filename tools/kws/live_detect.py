"""Live 70 cm detection report: word spotter + known-ID template check on each captured transmission.
usage: live_detect.py OUT.md RUNDIR [RUNDIR ...]   (RUNDIR = bench_inbox get destination with files/c70/*.flac)
Writes one row per transmission; skips 440.100 (P25) and 442.300 (noise bursts). SITE-SPECIFIC frequencies."""
import sys, glob, os, subprocess, pickle, numpy as np
sys.path.insert(0, '/home/user/kws/src')
import feats as F, train as T, textgen as tg
import jax, jax.numpy as jnp
from scipy.signal import butter, sosfiltfilt
F.BIN = '/home/user/kws/kws_feat'
MODEL = '/home/user/kws/data/m_nB.pkl'
m = pickle.load(open(MODEL, 'rb')); P = jax.tree_util.tree_map(jnp.asarray, m['params']); ST = m['args']['stride']
T.CMN = bool(m['args'].get('cmn', 0)); T.NOPITCH = bool(m['args'].get('nopitch', 0))
fw = jax.jit(lambda p, x: T.fwd(p, x, ST)); V = tg.VOCAB; DT = ST * 0.02
hp = butter(8, 300, 'hp', fs=8000, output='sos'); bp = butter(4, [300, 3000], 'bp', fs=8000, output='sos')

def audio(f):
    x = np.frombuffer(subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', f, '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'],
                                     capture_output=True, check=True).stdout, np.float32).astype(float)
    return sosfiltfilt(hp, x)

def snr_db(x):
    """Rough: 90th vs 10th percentile of 20 ms band power (pauses and squelch tail stand in for noise)."""
    y = sosfiltfilt(bp, x); n = len(y) // 160
    if n < 10: return float('nan')
    pw = (y[:n * 160].reshape(n, 160) ** 2).mean(1) + 1e-12
    return 10 * np.log10(np.percentile(pw, 90) / np.percentile(pw, 10))

def feats(x):
    x = (x / (np.max(np.abs(x)) + 1e-9) * 0.5).astype(np.float32)
    return T.cmn(F.norm_sotto(F.sotto(x)), 0)

def post(f):
    return np.asarray(jax.nn.log_softmax(fw(P, jnp.asarray(f[None])), -1))[0]

def score(Fx, Tm):
    n = len(Tm)
    if len(Fx) < n: Fx = np.vstack([Fx, np.zeros((n - len(Fx), Fx.shape[1]))])
    Tz = (Tm - Tm.mean(0)) / (Tm.std(0) + 1e-6); best, bi = -1, 0
    for i in range(len(Fx) - n + 1):
        W = Fx[i:i + n]; s = (((W - W.mean(0)) / (W.std(0) + 1e-6)) * Tz).mean()
        if s > best: best, bi = s, i
    return best, bi

def decode(lp):
    pr = np.exp(lp); bl = pr.shape[1] - 1; am = pr.argmax(-1); out = []; last = bl
    for t, k in enumerate(am):
        if k != bl and k != last and pr[t, k] > 0.5: out.append(V[k])
        last = k
    return ' '.join(out)

# templates: (source file, start s, end s, sotto threshold, posteriorgram threshold)
TM = {'VE7RMR': ('/home/user/kws/cont/kws-cont-c-r1/files/b2m/146.8000_20261122T1858545Z.flac', 1.2, 3.6, 0.25, 0.50),
      'W7ECG': ('/home/user/kws/n70/kws-n70/files/c70/442.2500_20261123T0754008Z.flac', 6.9, 10.3, 0.30, 0.55)}
TS = {}
for k, (tf, a, b, th, tp) in TM.items():
    f = feats(audio(tf)); TS[k] = (f[int(a / .02):int(b / .02), 2:], post(f)[int(a / DT):int(b / DT)], th, tp)

def main():
    out = sys.argv[1]; fs = []
    for d in sys.argv[2:]: fs += sorted(glob.glob(os.path.join(d, 'files/c70/*.flac')))
    fs = [f for f in fs if not os.path.basename(f).startswith(('440.1000', '442.3000'))]
    done = set()
    if os.path.exists(out):
        done = {l.split('|')[1].strip() for l in open(out) if l.startswith('| ') and 'Z' in l.split('|')[1]}
    new = [f for f in fs if os.path.basename(f)[:-5] not in done]
    if not os.path.exists(out):
        open(out, 'w').write('# 70 cm live detections (SITE-SPECIFIC)\n\nSotto = sotto-frame template score, PG = posteriorgram score. '
                             'Hit = sotto or PG above its threshold (VE7RMR 0.25/0.50, W7ECG 0.30/0.55).\n\n'
                             '| Transmission | s | SNR dB | Spotter words | VE7RMR sotto / PG | W7ECG sotto / PG | Hit |\n|---|---|---|---|---|---|---|\n')
    with open(out, 'a') as o:
        for f in new:
            x = audio(f); fx = feats(x); lp = post(fx); cells = []; hits = []
            for k, (ts, tpg, th, tp) in TS.items():
                s1, _ = score(fx[:, 2:], ts); s2, _ = score(lp, tpg); cells.append(f'{s1:.2f} / {s2:.2f}')
                if s1 > th or s2 > tp: hits.append(k + ('' if s1 > th and s2 > tp else ' (one)'))
            o.write(f'| {os.path.basename(f)[:-5]} | {len(x)/8000:.1f} | {snr_db(x):.0f} | {decode(lp) or "-"} | {cells[0]} | {cells[1]} | {", ".join(hits) or "-"} |\n')
            o.flush()
    print(len(new), 'new rows ->', out)

if __name__ == '__main__': main()
