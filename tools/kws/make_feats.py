"""npz utterances -> features.  make_feats.py IN.npz OUT.pkl MODE   (MODE: train3 | test)"""
import sys, pickle, numpy as np
from multiprocessing import Pool
import feats
CONDS = {"clean": dict(kind="clean"), "snr20": dict(snr=20), "snr10": dict(snr=10), "snr5": dict(snr=5)}
def work(a):
    i, x, mode = a
    out = {}
    if mode == "train3":
        for v in range(3):
            rng = np.random.default_rng(1000 * i + v)
            y = feats.channel(x, rng, kind="clean" if v == 0 and rng.random() < 0.3 else "fm", perturb=True)
            out[f"v{v}"] = (feats.norm_sotto(feats.sotto(y)), feats.logmel(y))
    elif mode == "narrow3":
        for v in range(3):
            rng = np.random.default_rng(5000 * i + v)
            y = feats.channel(x, rng, kind="narrow", perturb=True)
            out[f"v{v}"] = (feats.norm_sotto(feats.sotto(y)), feats.logmel(y))
    elif mode == "ntest":
        for c, kw in {"clean": dict(snr=15), "snr20": dict(snr=10), "snr10": dict(snr=5), "snr5": dict(snr=2)}.items():
            rng = np.random.default_rng(9000 + i)
            y = feats.channel(x, rng, kind="narrow", **kw)
            out[c] = (feats.norm_sotto(feats.sotto(y)), feats.logmel(y))
    else:
        for c, kw in CONDS.items():
            rng = np.random.default_rng(7000 + i)
            y = feats.channel(x, rng, **kw)
            out[c] = (feats.norm_sotto(feats.sotto(y)), feats.logmel(y))
    return out
if __name__ == "__main__":
    inp, outp, mode = sys.argv[1:4]
    d = np.load(inp, allow_pickle=True)
    with Pool(int(sys.argv[4]) if len(sys.argv) > 4 else 4) as p:
        F = p.map(work, [(i, x, mode) for i, x in enumerate(d["pcm"])], chunksize=8)
    pickle.dump(dict(feats=F, lab=list(d["lab"]), text=list(d["text"]), voice=list(d["voice"])), open(outp, "wb"))
    print("wrote", outp, len(F))
