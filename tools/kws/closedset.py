"""Closed-set callsign ID: score the true label sequence against 49 others from the same set by CTC loss."""
import sys, pickle, numpy as np, jax, jax.numpy as jnp, optax, train as T
m = pickle.load(open(sys.argv[1], "rb")); a = m["args"]; T.GRU = a.get("gru", 0)
p = jax.tree_util.tree_map(jnp.asarray, m["params"]); S = a["stride"]
fwdj = jax.jit(lambda p, x: T.fwd(p, x, S))
@jax.jit
def score(lg, Y, YP):
    n = Y.shape[0]
    L = jnp.repeat(lg[None], n, 0); LP = jnp.zeros(L.shape[:2])
    return optax.ctc_loss(L, LP, Y, YP, blank_id=T.BLANK)
rng = np.random.default_rng(0)
for t in sys.argv[2].split(","):
    for c in sys.argv[3].split(","):
        d, _ = T.load(t, 0, [c]); items = [(f, l) for f, l in d[c] if len(l) >= 3]
        ok = 0
        for i, (f, l) in enumerate(items):
            lg = fwdj(p, f[None])[0][:int(np.ceil(len(f) / S))]
            others = [items[j][1] for j in rng.choice(len(items), 60, replace=False) if j != i][:49]
            cands = [l] + others
            Y = np.zeros((50, T.LMAX), np.int32); YP = np.ones((50, T.LMAX), np.float32)
            for k, s in enumerate(cands): Y[k, :len(s)] = s[:T.LMAX]; YP[k, :len(s)] = 0
            sc = np.asarray(score(lg, jnp.asarray(Y), jnp.asarray(YP)))
            ok += int(np.argmin(sc) == 0)
        print(f"{t.split('/')[-1]:14s} {c:6s} closed-set (1 of 50) correct {ok/len(items)*100:5.1f}%  n={len(items)}", flush=True)
