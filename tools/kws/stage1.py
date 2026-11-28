"""Stage 1: tiny always-on 'repeater' spotter (CTC over {repeater} + blank). stage1.py --C 16 --blocks 3"""
import argparse, pickle, time, re, numpy as np, jax, jax.numpy as jnp, optax
import train as T, textgen as tg
ap = argparse.ArgumentParser()
ap.add_argument("--C", type=int, default=16); ap.add_argument("--blocks", type=int, default=3)
ap.add_argument("--epochs", type=int, default=15); ap.add_argument("--out", default="../data/s1.pkl")
ap.add_argument("--train", nargs="+", default=["../data/f_train_1.pkl", "../data/f_train_4.pkl", "../data/f_train_5.pkl", "../data/f_espeak_train.pkl"])
ap.add_argument("--test", nargs="+", default=["../data/f_test.pkl", "../data/f_testacc.pkl", "../data/f_flite.pkl"])
a = ap.parse_args()
T.NOUT, T.BLANK = 2, 1
RID = tg.VOCAB.index("repeater")
def rel(l): return [0] * list(l).count(RID)
def load(paths, keys):
    out = []
    for pth in paths:
        d, _ = T.load(pth, 0, keys)
        for k in keys: out += [(f, np.array(rel(l), np.int32)) for f, l in d[k]]
    return out
tr = load(a.train, ["v0", "v1", "v2"])
print("train", len(tr), "with repeater", sum(len(l) > 0 for _, l in tr), flush=True)
p = T.init(jax.random.PRNGKey(0), 22, a.C, a.blocks)
p["out"] = p["out"][:, :2]; p["bo"] = jnp.array([0.0, 2.0])
print("params", T.nparams(p), flush=True)
steps = a.epochs * (len(tr) // 32)
opt = optax.chain(optax.clip_by_global_norm(5.0), optax.adamw(optax.cosine_onecycle_schedule(steps, 3e-3), weight_decay=1e-4))
st = opt.init(p)
@jax.jit
def step(p, st, X, XP, Y, YP):
    l, g = jax.value_and_grad(T.loss_fn)(p, X, XP, Y, YP, 2)
    u, st = opt.update(g, st, p); return optax.apply_updates(p, u), st, l
fwdj = jax.jit(lambda p, x: T.fwd(p, x, 2))
rng = np.random.default_rng(0); order = np.argsort([len(f) for f, _ in tr]); t0 = time.time()
for ep in range(a.epochs):
    bs = [order[i:i + 32] for i in range(0, len(order) - 31, 32)]; rng.shuffle(bs); L = []
    for b in bs:
        X, XP, Y, YP = T.batchify([(T.augment(tr[i][0], rng), tr[i][1]) for i in b], 2)
        p, st, l = step(p, st, X, XP, Y, YP); L.append(float(l))
    print(f"ep {ep} loss {np.mean(L):.3f} {time.time()-t0:.0f}s", flush=True)
for t in a.test:
    for c in ["clean", "snr20", "snr10", "snr5"]:
        items = load([t], [c]); hit = npos = fa = nneg = 0
        for f, l in items:
            h = T.greedy(np.asarray(fwdj(p, f[None]))[0][:int(np.ceil(len(f) / 2))])
            if len(l): npos += 1; hit += len(h) > 0
            else: nneg += 1; fa += len(h) > 0
        print(f"{t.split('/')[-1]:14s} {c:6s} recall {hit/max(npos,1)*100:5.1f}% (n={npos})  false alarm {fa/max(nneg,1)*100:5.1f}% (n={nneg})", flush=True)
pickle.dump(dict(params=jax.tree_util.tree_map(np.asarray, p), args=vars(a)), open(a.out, "wb"))
