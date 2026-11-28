"""espeak-ng and flite utterances (held-out engines, like repeater controller voices)."""
import sys, os, random, subprocess, tempfile, numpy as np, soundfile as sf
from scipy.signal import resample_poly
from math import gcd
import textgen as tg
ESPEAK = ["en-us", "en-gb", "en-us+m3", "en-us+f2", "en-gb-scotland", "en-us+klatt", "en-gb-x-rp+f4"]
FLITE = ["kal", "awb", "rms", "slt", "kal16"]
def synth(eng, voice, text, speed, path):
    if eng == "espeak":
        subprocess.run(["espeak-ng", "-v", voice, "-s", str(int(165 * speed)), "-w", path, text], check=True,
                       capture_output=True)
    else:
        subprocess.run(["flite", "-voice", voice, "--setf", f"duration_stretch={1/speed:.2f}", "-t", text,
                        "-o", path], check=True, capture_output=True)
def main(eng, n, seed):
    rng = random.Random(seed); res = []
    tmp = tempfile.mktemp(suffix=".wav")
    for i in range(n):
        v = rng.choice(ESPEAK if eng == "espeak" else FLITE)
        text = tg.sentence(rng); sp = rng.uniform(0.85, 1.2)
        synth(eng, v, text, sp, tmp)
        x, sr = sf.read(tmp)
        if x.ndim > 1: x = x[:, 0]
        g = gcd(sr, 8000); x = resample_poly(x, 8000 // g, sr // g)
        x = np.clip(x / (np.max(np.abs(x)) + 1e-9) * 0.7 * 32767, -32767, 32767).astype(np.int16)
        res.append(dict(text=text, voice=f"{eng}:{v}", lab=tg.labels(text), pcm=x))
    out = f"{os.path.dirname(__file__)}/../data/{eng}_test_{seed}.npz"
    np.savez_compressed(out, text=np.array([r["text"] for r in res]), voice=np.array([r["voice"] for r in res]),
                        lab=np.array([np.array(r["lab"], np.int8) for r in res], dtype=object),
                        pcm=np.array([r["pcm"] for r in res], dtype=object))
    print("wrote", out, len(res))
if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
