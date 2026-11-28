import sys, pickle, jax, jax.numpy as jnp, numpy as np, train as T
m = pickle.load(open(sys.argv[1], "rb")); p = jax.tree_util.tree_map(jnp.asarray, m["params"]); a = m["args"]; T.CMN = bool(a.get("cmn", 0)); T.NOPITCH = bool(a.get("nopitch", 0))
fi = 0 if a["feat"] == "sotto" else 1
fwdj = jax.jit(lambda p, x: T.fwd(p, x, a["stride"]))
conds = sys.argv[3].split(",") if len(sys.argv) > 3 else ["clean", "snr20", "snr10", "snr5"]
for t in sys.argv[2].split(","):
    d, raw = T.load(t, fi, conds)
    for c in conds:
        r = T.evaluate(p, d[c], a["stride"], fwdj)
        print(f"{t.split('/')[-1]:16s} {c:6s} TER {r['ter']*100:5.1f}% utt {r['utt']*100:5.1f}% FA {r['fa']*100:4.1f}%", flush=True)
