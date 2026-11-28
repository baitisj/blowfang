"""Two-stage test: stage 1 finds 'repeater', stage 2 decodes only the W seconds of frames before it."""
import sys, re, pickle, numpy as np, jax, jax.numpy as jnp, optax, train as T, textgen as tg
s1 = pickle.load(open(sys.argv[1], "rb")); m2 = pickle.load(open(sys.argv[2], "rb"))
p1 = jax.tree_util.tree_map(jnp.asarray, s1["params"]); p2 = jax.tree_util.tree_map(jnp.asarray, m2["params"])
NO2 = p2["out"].shape[1]
f1 = jax.jit(lambda p, x: jax.nn.softmax(T.fwd(p, x, 2), -1))
f2 = jax.jit(lambda p, x: T.fwd(p, x, 2))
TH = float(sys.argv[3]) if len(sys.argv) > 3 else 0.265
def call_before(text):
    """labels of the NATO/digit run right before the word 'repeater'"""
    w = re.findall(r"[A-Za-z][A-Za-z'-]*", text.lower()); i = w.index("repeater") if "repeater" in w else -1
    out = []
    j = i - 1
    while j >= 0 and tg.ALIASES.get(w[j], w[j]) in tg.IDX and w[j] != "repeater":
        out.insert(0, tg.IDX[tg.ALIASES.get(w[j], w[j])]); j -= 1
    return out
@jax.jit
def ctc_scores(lg, Y, YP):
    L = jnp.repeat(lg[None], Y.shape[0], 0); return optax.ctc_loss(L, jnp.zeros(L.shape[:2]), Y, YP, blank_id=NO2 - 1)
rng = np.random.default_rng(0)
for t in sys.argv[4].split(","):
    for c in sys.argv[5].split(","):
        d, raw = T.load(t, 0, [c])
        items = [(F, call_before(txt)) for (F, _), txt in zip(d[c], raw["text"]) if "repeater" in txt.lower()]
        items = [(F, cs) for F, cs in items if len(cs) >= 3]
        calls = [cs for _, cs in items]
        for W in (2.0, 3.0, 4.0):
            trig = ex = near = cset = 0
            for k, (F, cs) in enumerate(items):
                pr = np.asarray(f1(p1, F[None]))[0][:int(np.ceil(len(F) / 2)), 0]
                if pr.max() <= TH: continue
                trig += 1
                end = (int(np.argmax(pr)) + 1) * 2               # input frame index of the trigger
                end = min(len(F), end + 15)                     # the rest of 'repeater' (0.3 s)
                win = F[max(0, end - int(W * 50) - 15):end]
                lg = np.asarray(f2(p2, win[None]))[0][:int(np.ceil(len(win) / 2))]
                h = [x for x in T.greedy(lg) if x < 36]
                # the decoded run that ends last (closest before 'repeater')
                ex += h[-len(cs):] == cs; near += T.edit(h[-len(cs):], cs) <= 1
                others = [calls[j] for j in rng.choice(len(calls), min(len(calls), 60), replace=False) if j != k][:49]
                Y = np.zeros((len(others) + 1, T.LMAX), np.int32); YP = np.ones_like(Y, np.float32)
                for q, s in enumerate([cs] + others):
                    Y[q, :len(s)] = s; YP[q, :len(s)] = 0
                # known-call match on the window: free text around the call is absorbed by blank
                sc = np.asarray(ctc_scores(jnp.asarray(lg), jnp.asarray(Y), jnp.asarray(YP)))
                cset += int(np.argmin(sc) == 0)
            n = len(items)
            print(f"{t.split('/')[-1]:14s} {c:6s} W={W:.0f}s n={n} triggered {trig/n*100:5.1f}%  of those: call exact {ex/max(trig,1)*100:5.1f}%  within 1 {near/max(trig,1)*100:5.1f}%  known-call {cset/max(trig,1)*100:5.1f}%", flush=True)
