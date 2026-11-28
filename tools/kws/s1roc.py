import sys, pickle, numpy as np, jax, jax.numpy as jnp, train as T, textgen as tg
T.NOUT, T.BLANK = 2, 1
m = pickle.load(open(sys.argv[1], "rb")); p = jax.tree_util.tree_map(jnp.asarray, m["params"])
fwdj = jax.jit(lambda p, x: jax.nn.softmax(T.fwd(p, x, 2), -1))
RID = tg.VOCAB.index("repeater")
def scores(t, c):
    d, _ = T.load(t, 0, [c]); s = []; y = []
    for f, l in d[c]:
        pr = np.asarray(fwdj(p, f[None]))[0][:int(np.ceil(len(f) / 2)), 0]
        s.append(pr.max()); y.append(RID in list(l))
    return np.array(s), np.array(y)
# threshold from Kokoro test snr20 negatives at 1% FA
s, y = scores("../data/f_test.pkl", "snr20"); th = np.quantile(s[~y], 0.99); print("threshold", round(float(th), 3))
for t in ["../data/f_test.pkl", "../data/f_testacc.pkl", "../data/f_flite.pkl"]:
    for c in ["clean", "snr20", "snr10"]:
        s, y = scores(t, c)
        print(f"{t.split('/')[-1]:14s} {c:6s} recall {np.mean(s[y] > th)*100:5.1f}%  FA {np.mean(s[~y] > th)*100:4.1f}%")
