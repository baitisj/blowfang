"""Post-training int8 weights (per output channel) + int16 activations; re-evaluate; K6 cost estimate.
quant.py model.pkl test.pkl..."""
import sys, pickle, numpy as np, jax, jax.numpy as jnp
import train as T

def qw(w, axis_out=-1, bits=8):
    m = np.max(np.abs(w), axis=tuple(i for i in range(w.ndim) if i != (w.ndim + axis_out) % w.ndim), keepdims=True)
    s = m / (2 ** (bits - 1) - 1) + 1e-12
    return np.round(w / s) * s

def quantize(p, bits=8):
    q = {"c1": qw(p["c1"], bits=bits), "b1": p["b1"], "out": qw(p["out"], bits=bits), "bo": p["bo"], "blk": []}
    for b in p["blk"]:
        q["blk"].append({"dw": qw(b["dw"], bits=bits), "db": b["db"], "pw": qw(b["pw"], bits=bits), "pb": b["pb"]})
    return q

def cost(p, stride, fps=50):
    k1, cin, C = p["c1"].shape
    macs = k1 * cin * C / stride
    for b in p["blk"]:
        macs += (b["dw"].shape[0] * C + C * C) / stride
    macs += C * p["out"].shape[1] / stride
    nw = sum(int(np.prod(np.shape(a))) for a in jax.tree_util.tree_leaves(p))
    rf = (k1 + sum((b["dw"].shape[0] - 1) * T.dil(i) for i, b in enumerate(p["blk"])) * stride)
    return dict(params=nw, macs_per_frame=macs, macs_per_s=macs * fps, rf_ms=rf * 20,
                act_ram=(C * (max(T.dil(i) for i in range(len(p["blk"]))) * 4 + 1) * len(p["blk"]) * 2))

if __name__ == "__main__":
    m = pickle.load(open(sys.argv[1], "rb")); p = m["params"]; a = m["args"]; stride = a["stride"]
    fi = 0 if a["feat"] == "sotto" else 1
    c = cost(p, stride); print({k: round(v) for k, v in c.items()})
    fwdj = jax.jit(lambda p, x: T.fwd(p, x, stride))
    for bits in (8, 6, 4):
        q = jax.tree_util.tree_map(jnp.asarray, quantize(p, bits))
        for t in sys.argv[2:]:
            d, _ = T.load(t, fi, ["clean", "snr10"])
            for cnd, items in d.items():
                r = T.evaluate(q, items, stride, fwdj)
                print(f"int{bits} {t.split('/')[-1]:14s} {cnd:6s} TER {r['ter']*100:5.1f}% utt {r['utt']*100:5.1f}% FA {r['fa']*100:4.1f}%")
