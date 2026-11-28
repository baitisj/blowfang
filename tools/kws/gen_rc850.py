"""RC-850 (TMS5220) word splices -> npz like kok_*.npz.  gen_rc850.py OUT.npz N SEED [split]
split: 'all' (default) uses every word file; private vocabulary, never publish outputs."""
import sys, glob, os, random, subprocess, numpy as np, textgen as tg
X = '/home/user/kws/rc850_zip/x'
import functools
@functools.lru_cache(maxsize=None)
def ld(p):
    return np.frombuffer(subprocess.run(['ffmpeg','-loglevel','error','-f','mulaw','-ar','8000','-i',p,'-f','s16le','-'],capture_output=True,check=True).stdout,np.int16)
M = {os.path.basename(p)[:-5]: p for p in glob.glob(X + '/_male/*.ulaw')}
F = {os.path.basename(p)[:-5]: p for p in glob.glob(X + '/_female/*.ulaw')}
NUMW = ['zero','one','two','three','four','five','six','seven','eight','nine']
LET = {c: [q for q in (X + '/letters/' + c + '.ulaw', X + '/_male/' + c + '.ulaw') if os.path.exists(q)] for c in 'abcdefghijklmnopqrstuvwxyz'}
def wordfile(w, rng):
    if w.startswith('letter-'): return rng.choice(LET[w[7:]])
    w = {'juliett': 'juliet'}.get(w, w)
    if w in NUMW and rng.random() < 0.3:
        k = str(NUMW.index(w))
        if k in F: return F[k]
        if w in F: return F[w]
    if w in M: return M[w]
    if w in NUMW and str(NUMW.index(w)) in M: return M[str(NUMW.index(w))]
    return None
VOC = set(tg.VOCAB) | {'juliet'}
FILLERS = [k for k in M if k not in VOC and not k[0].isdigit() and not k.startswith(('suffix', 'prefix')) and len(k) > 1]
DAYS = ['sunday','monday','tuesday','wednesday','thursday','friday','saturday']
MONTHS = ['january','february','march','april','may','june','july','august','september','october','november','december']
def callsign_words(rng):
    cs = tg.callsign(rng); return [tg.NATO[ord(c)-65] if c.isalpha() else NUMW[int(c)] for c in cs]
def letters_words(rng, n):
    return [rng.choice(tg.NATO) if rng.random() < 0.75 else rng.choice(NUMW) for _ in range(n)]
def time_words(rng):
    h = rng.randint(1, 12); w = ['the', 'time', 'is'] if rng.random() < 0.7 else ['time']
    w += [str(h)] if str(h) in M else []
    mm = rng.choice([None, rng.randint(1, 59)])
    if mm: w += [f'{mm:02d}'] if f'{mm:02d}' in M else ([str(mm)] if str(mm) in M else [])
    w += [rng.choice(['a_m', 'p_m'])]
    return w
def date_words(rng):
    return [rng.choice(DAYS), rng.choice(MONTHS), str(rng.randint(1, 28))]
def letter_call(rng):
    cs = tg.callsign(rng); return ['letter-' + c.lower() if c.isalpha() else NUMW[int(c)] for c in cs]
def utterance(rng):
    if tg.LETTERS[0] in tg.VOCAB and rng.random() < 0.5:   # plain-letter IDs ("w seven e c g repeater")
        r = rng.random(); ws = []
        if r < 0.6:
            if rng.random() < 0.3: ws += ['this_is']
            ws += letter_call(rng)
            if rng.random() < 0.7: ws += ['repeater']
            if rng.random() < 0.3: ws += time_words(rng)
        elif r < 0.8: ws += ['letter-' + rng.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(rng.randint(2, 6))]
        else: ws += [rng.choice(FILLERS) for _ in range(rng.randint(0, 3))] + letter_call(rng) + [rng.choice(FILLERS) for _ in range(rng.randint(0, 3))]
        return ws
    r = rng.random(); ws = []
    if r < 0.45:   # controller ID shape
        if rng.random() < 0.3: ws += ['this_is']
        ws += callsign_words(rng)
        if rng.random() < 0.7: ws += ['repeater']
        if rng.random() < 0.5: ws += time_words(rng)
        if rng.random() < 0.3: ws += date_words(rng)
    elif r < 0.65: ws += letters_words(rng, rng.randint(2, 8))
    elif r < 0.8:  ws += [rng.choice(FILLERS) for _ in range(rng.randint(1, 4))] + callsign_words(rng) + [rng.choice(FILLERS) for _ in range(rng.randint(0, 3))]
    elif r < 0.9:  ws += [rng.choice(FILLERS) for _ in range(rng.randint(2, 7))]   # no keywords at all
    else:          ws += time_words(rng) + (date_words(rng) if rng.random() < 0.5 else [])
    return ws
def render(ws, rng):
    parts = []; text = []; lab = []
    gap = rng.uniform(0.0, 0.12)
    for w in ws:
        p = wordfile(w, rng) if w in VOC or w in NUMW or w.startswith('letter-') else M.get(w)
        if p is None: continue
        parts.append(ld(p)); parts.append(np.zeros(int(8000 * max(0, gap + rng.uniform(-0.03, 0.06))), np.int16))
        text.append(w)
        n = 'juliett' if w == 'juliet' else w
        if n in tg.IDX: lab.append(tg.IDX[n])
    lead = np.zeros(int(8000 * rng.uniform(0.1, 0.5)), np.int16); tail = np.zeros(int(8000 * rng.uniform(0.1, 0.4)), np.int16)
    x = np.concatenate([lead] + parts + [tail]).astype(np.float32)
    x = x / (np.max(np.abs(x)) + 1e-9) * rng.uniform(0.3, 0.9) * 32767
    return x.astype(np.int16), ' '.join(text), np.array(lab, np.int8)
if __name__ == '__main__':
    out, n, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]); rng = random.Random(seed)
    P, T, L = [], [], []
    for _ in range(n):
        x, t, l = render(utterance(rng), rng); P.append(x); T.append(t); L.append(l)
    pcm = np.empty(n, object); pcm[:] = P; lab = np.empty(n, object); lab[:] = L
    np.savez(out, pcm=pcm, lab=lab, text=np.array(T), voice=np.array(['rc850'] * n)); print('wrote', out, n)
    for t in T[:6]: print(' ', t)
