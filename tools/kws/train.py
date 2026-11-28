"""Tiny CTC keyword spotter (depthwise-separable TCN) in JAX.
train.py --feat sotto|mel --C 48 --blocks 4 --epochs 30 --out model.pkl"""
import argparse, pickle, time, numpy as np, jax, jax.numpy as jnp, optax
from functools import partial
import textgen as tg
V = len(tg.VOCAB); BLANK = V; NOUT = V + 1
LMAX = 32

GRU = 0
def init(key, cin, C, blocks, k1=5, k=5):
    ks = jax.random.split(key, 5 + 2 * blocks)
    p = {"c1": jax.random.normal(ks[0], (k1, cin, C)) * np.sqrt(2 / (k1 * cin)), "b1": jnp.zeros(C),
         "out": jax.random.normal(ks[1], (C, NOUT)) * np.sqrt(1 / C), "bo": jnp.zeros(NOUT).at[BLANK].set(2.0),
         "blk": []}
    for i in range(blocks):
        p["blk"].append({"dw": jax.random.normal(ks[2 + 2 * i], (k, 1, C)) * np.sqrt(2 / k), "db": jnp.zeros(C),
                         "pw": jax.random.normal(ks[3 + 2 * i], (C, C)) * np.sqrt(2 / C) * 0.5, "pb": jnp.zeros(C)})
    if GRU:
        H = GRU
        p["gx"] = jax.random.normal(ks[-1], (C, 3 * H)) * np.sqrt(1 / C)
        p["gh"] = jax.random.normal(ks[-2], (H, 3 * H)) * np.sqrt(1 / H)
        p["gb"] = jnp.zeros(3 * H)
        p["out"] = jax.random.normal(ks[1], (H, NOUT)) * np.sqrt(1 / H)
    return p

def gru(p, h):
    H = p["gh"].shape[0]
    xs = h @ p["gx"] + p["gb"]                       # B,T,3H
    def stepf(s, x):
        hh = s @ p["gh"]
        z = jax.nn.sigmoid(x[:, :H] + hh[:, :H]); r = jax.nn.sigmoid(x[:, H:2*H] + hh[:, H:2*H])
        n = jnp.tanh(x[:, 2*H:] + r * hh[:, 2*H:])
        s = (1 - z) * n + z * s
        return s, s
    _, ys = jax.lax.scan(stepf, jnp.zeros((h.shape[0], H)), jnp.swapaxes(xs, 0, 1))
    return jnp.swapaxes(ys, 0, 1)

def dil(i): return [1, 2, 4, 8, 1, 2, 4, 8][i]

def fwd(p, x, stride=2):
    dn = ("NWC", "WIO", "NWC")
    h = jax.lax.conv_general_dilated(x, p["c1"], (stride,), "SAME", dimension_numbers=dn) + p["b1"]
    h = jax.nn.relu(h)
    for i, b in enumerate(p["blk"]):
        # depthwise dilated conv as K shifted multiply-adds: same result as a grouped
        # conv_general_dilated ("SAME"), about 3x faster forward+backward on XLA CPU
        K, dr = b["dw"].shape[0], dil(i); pad = dr * (K // 2); T = h.shape[1]
        hp = jnp.pad(h, ((0, 0), (pad, pad), (0, 0)))
        d = sum(hp[:, k * dr:k * dr + T] * b["dw"][k, 0] for k in range(K)) + b["db"]
        d = jax.nn.relu(d)
        h = h + jax.nn.relu(d @ b["pw"] + b["pb"])
    if "gh" in p: h = gru(p, h)
    return h @ p["out"] + p["bo"]

def nparams(p): return sum(int(np.prod(a.shape)) for a in jax.tree_util.tree_leaves(p))

def batchify(items, stride):
    T = max(len(f) for f, _ in items); T = int(np.ceil(T / 64) * 64)
    B = len(items); cin = items[0][0].shape[1]
    X = np.zeros((B, T, cin), np.float32); XP = np.ones((B, T // stride), np.float32)
    Y = np.zeros((B, LMAX), np.int32); YP = np.ones((B, LMAX), np.float32)
    for i, (f, l) in enumerate(items):
        X[i, :len(f)] = f; XP[i, :int(np.ceil(len(f) / stride))] = 0
        l = list(l)[:LMAX]; Y[i, :len(l)] = l; YP[i, :len(l)] = 0
    return X, XP, Y, YP

@partial(jax.jit, static_argnums=(5,))
def loss_fn(p, X, XP, Y, YP, stride):
    lg = fwd(p, X, stride)
    return jnp.mean(optax.ctc_loss(lg, XP, Y, YP, blank_id=BLANK))

def greedy(lg):
    return greedy_ids(np.argmax(lg, -1))

def greedy_ids(a):
    out = []; prev = -1
    for t in a:
        if t != prev and t != BLANK: out.append(int(t))
        prev = t
    return out

def edit(a, b):
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        pr, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            pr, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, pr + (a[i - 1] != b[j - 1]))
    return d[len(b)]

BUCKET = 64                                                # frames; inputs pad to a multiple (one compile per bucket)

def padded(f):
    """f (T, cin) -> (1, T rounded up to BUCKET, cin) zero-padded, so jit compiles once per bucket, not per length."""
    T = int(np.ceil(len(f) / BUCKET) * BUCKET); x = np.zeros((1, T, f.shape[1]), np.float32); x[0, :len(f)] = f
    return x

def evaluate(p, data, stride, fwdj, B=64):
    """data: list of (feat, lab). Returns TER, utt-exact, false-alarm rate on empty refs.
    Utterances run in length-bucketed batches of up to B, padded like training batches."""
    E = N = ok = fa = ne = 0; RP = [0, 0, 0]
    idx = sorted(range(len(data)), key=lambda i: len(data[i][0])); hyp = {}
    for s in range(0, len(idx), B):
        grp = idx[s:s + B]; T = int(np.ceil(len(data[grp[-1]][0]) / BUCKET) * BUCKET)
        X = np.zeros((B, T, data[grp[0]][0].shape[1]), np.float32)
        for k, i in enumerate(grp): X[k, :len(data[i][0])] = data[i][0]
        A = np.asarray(fwdj(p, X)).argmax(-1)
        for k, i in enumerate(grp): hyp[i] = A[k, :int(np.ceil(len(data[i][0]) / stride))]
    for i, (f, l) in enumerate(data):
        h = greedy_ids(hyp[i]); l = list(l)
        if len(l) == 0:
            ne += 1; fa += len(h) > 0; continue
        E += edit(h, l); N += len(l); ok += h == l
        RP[0] += min(h.count(V - 1), l.count(V - 1)); RP[1] += l.count(V - 1); RP[2] += h.count(V - 1)
    n = len(data) - ne
    return dict(ter=E / max(N, 1), utt=ok / max(n, 1), fa=fa / max(ne, 1), n=n, ne=ne,
                rrec=RP[0] / max(RP[1], 1), rprec=RP[0] / max(RP[2], 1), rn=RP[1])

CMN = False; NOPITCH = False
def cmn(f, fi):
    if NOPITCH and fi == 0: f = f.copy(); f[:, 1] = 0
    if not CMN: return f
    f = f.copy(); c0 = 3 if fi == 0 else 1
    e = f[:, 2] if fi == 0 else f[:, 0]
    act = e > np.max(e) - (0.3 if fi == 0 else 0.6)
    f[:, c0:] -= f[act, c0:].mean(0) if act.sum() > 3 else f[:, c0:].mean(0)
    return f

def load(path, fi, keys):
    d = pickle.load(open(path, "rb"))
    out = {k: [] for k in keys}
    import re
    for F, l, txt in zip(d["feats"], d["lab"], d["text"]):
        if re.search("[A-Za-z]", str(txt)): l = tg.labels(str(txt))     # labels from text (vocab may grow)
        for k in keys:
            if k in F: out[k].append((cmn(F[k][fi], fi), np.asarray(l, np.int32)))
    return out, d

def augment(f, rng):
    f = f.copy()
    for _ in range(2):                                     # time masks
        if rng.random() < 0.5:
            w = rng.integers(1, 6); s = rng.integers(0, max(1, len(f) - w)); f[s:s + w] = 0
    if rng.random() < 0.5:                                 # shape coefficient masks
        c = rng.integers(3, f.shape[1]); f[:, c] = 0
    return f

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--feat", default="sotto"); ap.add_argument("--C", type=int, default=48)
    ap.add_argument("--blocks", type=int, default=4); ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--stride", type=int, default=2); ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--train", nargs="+", default=["../data/f_train_1.pkl"])
    ap.add_argument("--test", nargs="+", default=["../data/f_test.pkl"])
    ap.add_argument("--cmn", type=int, default=0); ap.add_argument("--gru", type=int, default=0); ap.add_argument("--out", default="../data/model.pkl"); ap.add_argument("--seed", type=int, default=0); ap.add_argument("--init", default=""); ap.add_argument("--nopitch", type=int, default=0)
    a = ap.parse_args()
    fi = 0 if a.feat == "sotto" else 1
    globals()["CMN"] = bool(a.cmn); globals()["NOPITCH"] = bool(a.nopitch); globals()["GRU"] = a.gru
    tr = []
    for t in a.train:
        d, _ = load(t, fi, ["v0", "v1", "v2"]); tr += d["v0"] + d["v1"] + d["v2"]
    tests = {}
    for t in a.test:
        d, _ = load(t, fi, ["clean", "snr20", "snr10", "snr5"]); tests[t] = d
    cin = tr[0][0].shape[1]
    p = init(jax.random.PRNGKey(a.seed), cin, a.C, a.blocks)
    if a.init:
        q = pickle.load(open(a.init, "rb"))["params"]
        if np.shape(q["out"])[1] != NOUT:               # vocab grew: keep old word columns, move blank, fresh new columns
            vo = np.shape(q["out"])[1] - 1; o = np.array(p["out"]); bo = np.array(p["bo"])
            o[:, :vo] = q["out"][:, :vo]; o[:, BLANK] = q["out"][:, vo]; bo[:vo] = q["bo"][:vo]; bo[BLANK] = q["bo"][vo]
            q = dict(q); q["out"] = o; q["bo"] = bo; print("vocab grew", vo, "->", V, flush=True)
        p = jax.tree_util.tree_map(jnp.asarray, q); print("init from", a.init, flush=True)
    print(f"train {len(tr)} utts, cin {cin}, params {nparams(p)}", flush=True)
    steps = a.epochs * int(np.ceil(len(tr) / 32))
    opt = optax.chain(optax.clip_by_global_norm(5.0), optax.adamw(optax.cosine_onecycle_schedule(steps, a.lr), weight_decay=1e-4))
    st = opt.init(p)
    @jax.jit
    def step(p, st, X, XP, Y, YP):
        l, g = jax.value_and_grad(loss_fn)(p, X, XP, Y, YP, a.stride)
        u, st = opt.update(g, st, p); return optax.apply_updates(p, u), st, l
    fwdj = jax.jit(lambda p, x: fwd(p, x, a.stride))
    rng = np.random.default_rng(a.seed)
    lens = np.array([len(f) for f, _ in tr]); t0 = time.time()
    for ep in range(a.epochs):
        # same-bucket utterances share batches (little padding), but who shares a batch changes each epoch,
        # and the last partial batch is filled from the start so no utterance is dropped
        order = np.lexsort((rng.random(len(tr)), lens // 64))
        order = np.concatenate([order, order[:(-len(order)) % 32]])
        bs = [order[i:i + 32] for i in range(0, len(order), 32)]; rng.shuffle(bs)
        L = []
        for b in bs:
            X, XP, Y, YP = batchify([(augment(tr[i][0], rng), tr[i][1]) for i in b], a.stride)
            p, st, l = step(p, st, X, XP, Y, YP); L.append(l)          # stays on device: no per-step sync
        msg = f"ep {ep} loss {float(jnp.mean(jnp.stack(L))):.3f} {time.time() - t0:.0f}s"
        if ep % 5 == 4 or ep == a.epochs - 1:
            first = list(tests)[0]; r = evaluate(p, tests[first]["snr20"], a.stride, fwdj)
            msg += f"  [{first.split('/')[-1]} snr20 ter {r['ter']:.3f} utt {r['utt']:.3f} fa {r['fa']:.2f}]"
        print(msg, flush=True)
    res = {}
    for t, d in tests.items():
        for c, items in d.items():
            if items: res[(t.split('/')[-1], c)] = evaluate(p, items, a.stride, fwdj)
    for k, r in res.items():
        print(f"{k[0]:18s} {k[1]:6s} TER {r['ter']*100:5.1f}%  utt {r['utt']*100:5.1f}%  FA {r['fa']*100:4.1f}%  (n={r['n']}, empty={r['ne']})  repeater recall {r['rrec']*100:.0f}% prec {r['rprec']*100:.0f}% (n={r['rn']})")
    pickle.dump(dict(params=jax.tree_util.tree_map(np.asarray, p), args=vars(a), res=res), open(a.out, "wb"))
