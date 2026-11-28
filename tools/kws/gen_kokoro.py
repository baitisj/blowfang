"""Kokoro TTS -> 8 kHz int16 utterances with keyword labels.  gen_kokoro.py SPLIT N SEED"""
import sys, os, random, numpy as np
os.environ["OMP_NUM_THREADS"] = "1"
from multiprocessing import Pool
from scipy.signal import resample_poly
import textgen as tg

TRAIN = ["af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_sky",
         "af_sarah", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_onyx", "am_santa",
         "bf_alice", "bf_emma", "bf_isabella", "bm_daniel", "bm_fable", "bm_george"]
TRAIN_ACC = ["ef_dora", "em_alex", "ff_siwis", "hf_beta", "hm_omega", "if_sara", "pf_dora", "pm_alex"]
TEST = ["af_nova", "af_river", "am_michael", "am_puck", "bf_lily", "bm_lewis"]
TEST_ACC = ["im_nicola", "hf_alpha", "hm_psi", "em_santa"]

K = None
def init():
    global K
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
    sess = ort.InferenceSession(os.path.join(os.path.dirname(__file__), "../models/kokoro-v1.0.onnx"), so)
    K = Kokoro.from_session(sess, os.path.join(os.path.dirname(__file__), "../models/voices-v1.0.bin"))

def job(args):
    split, i, seed = args
    rng = random.Random(seed * 100003 + i)
    if split == "train":
        pool = TRAIN + TRAIN_ACC if rng.random() < 0.25 else TRAIN
    elif split == "test":
        pool = TEST
    else:
        pool = TEST_ACC
    v = rng.choice(pool); name = v
    if split == "train" and rng.random() < 0.4:
        v2 = rng.choice(TRAIN); a = rng.uniform(0.2, 0.8)
        v = a * K.get_voice_style(v) + (1 - a) * K.get_voice_style(v2); name = f"{name}+{v2}@{a:.2f}"
    text = tg.sentence(rng)
    speed = rng.uniform(0.85, 1.25)
    try:
        s, sr = K.create(text, voice=v, speed=speed, lang="en-us")
    except Exception as e:
        return None
    x = resample_poly(s, 1, 3)
    pk = np.max(np.abs(x)) + 1e-9
    x = np.clip(x / pk * 0.7 * 32767, -32767, 32767).astype(np.int16)
    return dict(text=text, voice=name, speed=speed, lab=tg.labels(text), pcm=x)

if __name__ == "__main__":
    split, n, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    out = f"{os.path.dirname(__file__)}/../data/kok_{split}_{seed}.npz"
    res = []
    with Pool(4, initializer=init) as p:
        for k, r in enumerate(p.imap(job, [(split, i, seed) for i in range(n)], chunksize=4)):
            if r: res.append(r)
            if k % 200 == 0: print(split, k, flush=True)
    np.savez_compressed(out, text=np.array([r["text"] for r in res]), voice=np.array([r["voice"] for r in res]),
                        lab=np.array([np.array(r["lab"], np.int8) for r in res], dtype=object),
                        pcm=np.array([r["pcm"] for r in res], dtype=object))
    print("wrote", out, len(res))
