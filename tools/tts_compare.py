#!/usr/bin/env python3
"""Which text-to-speech voice survives sotto 1200 best, for the speech hamlet sends to the UV-380
player (docs/uv380-tts.md). Each voice speaks the same sentences; each sentence goes through
the radio's own path on the host: 8 kHz, peak -1 dBFS, the fixed-point sotto 1200 encoder and
decoder from the sotto submodule (the UV-380's codec build, VARIANT=v3.7), and is then scored:

    wer_pk   word error rate of NVIDIA Parakeet TDT 0.6B v2 on the decoded speech
    wer_wh   word error rate of Whisper base.en (a weaker listener, so it spreads the voices)
    ovrl     DNSMOS P.835 overall quality of the decoded speech (1-5)
    sig      DNSMOS speech quality only (1-5)
    pesq     PESQ narrowband, decoded against the 8 kHz voice before the codec: what sotto loses
    s_per_w  seconds of speech a word (air time: PHY R carries about 0.43 s of speech a second)
    rtf      synthesis time over speech time on this machine

    make -C sotto/c VARIANT=v3.7 fixed PYTHON=false
    python tools/tts_compare.py --models ~/blowfang-work/tts/models --out RUN_DIR [--only kokoro]

Needs the voices (tools/tts_models.sh) and, for scoring, sherpa-onnx, pesq and jiwer
(tools/tts_requirements.txt) with the scoring models tools/tts_models.sh --score fetches.
Writes RUN_DIR/scores.csv, RUN_DIR/summary.txt and every wav before and after the codec.
SPDX-License-Identifier: MIT
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import tts_say  # noqa: E402

ENC = os.path.join(ROOT, "sotto", "c", "fixed", "sotto_enc_fx")
DEC = os.path.join(ROOT, "sotto", "c", "fixed", "sotto_dec_fx")

# Harvard sentences (IEEE 1969, lists 1-2: phonetically balanced) and the kind of thing the
# player says. Numbers are written out so every engine reads them the same way.
SENTENCES = (
    "The birch canoe slid on the smooth planks.",
    "Glue the sheet to the dark blue background.",
    "It's easy to tell the depth of a well.",
    "These days a chicken leg is a rare dish.",
    "Rice is often served in round bowls.",
    "The juice of lemons makes fine punch.",
    "The box was thrown beside the parked truck.",
    "Four hours of steady work faced us.",
    "Received range. Signal is good, three packets, no errors.",
    "This is A G seven E W, testing the sotto player on four four zero point eight five.",
)

VOICES = (
    ("espeak", "en-us"),
    ("piper", "en-us-amy-low"),
    ("piper", "en-us-lessac-medium"),
    ("piper", "en_US-lessac-high"),
    ("piper", "en-us-ryan-medium"),
    ("piper", "en-us-ryan-high"),
    ("piper", "en_US-hfc_female-medium"),
    ("piper", "en_US-hfc_male-medium"),
    ("piper", "en_US-joe-medium"),
    ("piper", "en_US-john-medium"),
    ("piper", "en_US-kristin-medium"),
    ("piper", "en_US-norman-medium"),
    ("piper", "en_GB-alan-medium"),
    ("piper", "en_GB-cori-high"),
    ("piper", "en_GB-northern_english_male-medium"),
    ("kokoro", "af_heart"),
    ("kokoro", "af_bella"),
    ("kokoro", "af_nicole"),
    ("kokoro", "af_sarah"),
    ("kokoro", "am_michael"),
    ("kokoro", "am_fenrir"),
    ("kokoro", "am_puck"),
    ("kokoro", "bf_emma"),
    ("kokoro", "bm_george"),
    ("kokoro", "bm_fable"),
)


def codec(pcm8k, d):
    """int16 8 kHz -> the same after sotto 1200 encode and decode (fixed point)"""
    raw, bit, out = (os.path.join(d, n) for n in ("in.raw", "a.bit", "out.raw"))
    pcm8k.tofile(raw)
    subprocess.run([ENC, "-1200", raw, bit], check=True, capture_output=True)
    subprocess.run([DEC, "-1200", bit, out], check=True, capture_output=True)
    return np.fromfile(out, np.int16)


def words(s):
    s = s.lower().replace("-", " ")
    return re.sub(r"[^a-z' ]", " ", s).split()


class Scorer:
    def __init__(self, models):
        import onnxruntime as ort
        import sherpa_onnx
        pk = os.path.join(models, "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")
        self.pk = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=os.path.join(pk, "encoder.int8.onnx"),
            decoder=os.path.join(pk, "decoder.int8.onnx"),
            joiner=os.path.join(pk, "joiner.int8.onnx"),
            tokens=os.path.join(pk, "tokens.txt"),
            model_type="nemo_transducer", num_threads=4)
        wh = os.path.join(models, "sherpa-onnx-whisper-base.en")
        self.wh = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=os.path.join(wh, "base.en-encoder.int8.onnx"),
            decoder=os.path.join(wh, "base.en-decoder.int8.onnx"),
            tokens=os.path.join(wh, "base.en-tokens.txt"), num_threads=4)
        self.dns = ort.InferenceSession(os.path.join(models, "sig_bak_ovr.onnx"))

    def asr(self, rec, y, fs):
        s = rec.create_stream()
        s.accept_waveform(fs, y.astype(np.float32))
        rec.decode_stream(s)
        return s.result.text

    def dnsmos(self, y16k):
        """DNSMOS P.835 (personalized=False) over 9.01 s windows, polynomial-fitted"""
        n = int(9.01 * 16000)
        while len(y16k) < n:
            y16k = np.concatenate([y16k, y16k])
        hop = 16000
        out = []
        for s in range(0, max(1, len(y16k) - n + 1), hop):
            seg = y16k[s:s + n].astype(np.float32)[None, :]
            sig, bak, ovr = self.dns.run(None, {"input_1": seg})[0][0]
            out.append((np.polyval([-0.08397278, 1.22083953, 0.0052439], sig),
                        np.polyval([-0.13166888, 1.60915514, -0.39604546], bak),
                        np.polyval([-0.06766283, 1.11546468, 0.04602535], ovr)))
        return np.mean(out, axis=0)


def wer(ref, hyp):
    import jiwer
    return jiwer.wer(" ".join(words(ref)), " ".join(words(hyp)) or "x")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", default=tts_say.models_dir())
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="engine or voice substring")
    ap.add_argument("--no-score", action="store_true", help="just write the wavs")
    a = ap.parse_args(argv)
    from pesq import pesq
    tts_say_res = tts_say.resample
    os.makedirs(a.out, exist_ok=True)
    sc = None if a.no_score else Scorer(a.models)
    rows = []
    for eng, voice in VOICES:
        tag = f"{eng}-{voice}"
        if a.only and a.only not in tag:
            continue
        vd = os.path.join(a.out, tag)
        os.makedirs(vd, exist_ok=True)
        for i, text in enumerate(SENTENCES):
            t0 = time.time()
            y, fs = tts_say.synth(text, eng, voice, 1.0, a.models)
            dt = time.time() - t0
            pcm = tts_say.to_int16(tts_say_res(y, fs, 8000))
            with tempfile.TemporaryDirectory() as d:
                dec = codec(pcm, d)
            tts_say.write_wav(os.path.join(vd, f"{i:02d}-in8k.wav"), pcm, 8000)
            tts_say.write_wav(os.path.join(vd, f"{i:02d}-sotto1200.wav"), dec, 8000)
            row = dict(engine=eng, voice=voice, sent=i, secs=round(len(pcm) / 8000, 2),
                       s_per_w=round(len(pcm) / 8000 / len(words(text)), 3),
                       rtf=round(dt / (len(y) / fs), 3))
            if sc:
                x = dec.astype(np.float32) / 32768
                row["hyp_pk"] = sc.asr(sc.pk, x, 8000)
                row["hyp_wh"] = sc.asr(sc.wh, x, 8000)
                row["wer_pk"] = round(wer(text, row["hyp_pk"]), 3)
                row["wer_wh"] = round(wer(text, row["hyp_wh"]), 3)
                row["wer_pk_clean"] = round(wer(text, sc.asr(sc.pk, pcm / 32768, 8000)), 3)
                sig, bak, ovr = sc.dnsmos(tts_say_res(x, 8000, 16000))
                row["sig"], row["ovrl"] = round(float(sig), 3), round(float(ovr), 3)
                n = min(len(pcm), len(dec))
                try:
                    row["pesq"] = round(pesq(8000, pcm[:n].astype(float), dec[:n].astype(float),
                                             "nb"), 3)
                except Exception:
                    row["pesq"] = float("nan")
            rows.append(row)
        print(tag, *(f"{k}={np.mean([r[k] for r in rows if r['voice'] == voice]):.3f}"
                     for k in ("wer_pk", "wer_wh", "ovrl", "pesq", "s_per_w", "rtf")
                     if k in rows[-1]), flush=True)
    keys = list(rows[0])
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(os.path.join(a.out, "scores.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(rows)
    if sc:
        summ = []
        for eng, voice in VOICES:
            rs = [r for r in rows if r["voice"] == voice]
            if not rs:
                continue
            m = {k: float(np.nanmean([r[k] for r in rs])) for k in
                 ("wer_pk", "wer_wh", "wer_pk_clean", "ovrl", "sig", "pesq", "s_per_w", "rtf")}
            summ.append((eng, voice, m))
        # rank: words first (both listeners), then sound
        summ.sort(key=lambda t: (t[2]["wer_pk"] + t[2]["wer_wh"], -t[2]["ovrl"]))
        hdr = (f"{'engine':7} {'voice':36} {'wer_pk':>6} {'wer_wh':>6} {'clean':>6} "
               f"{'ovrl':>5} {'sig':>5} {'pesq':>5} {'s/w':>5} {'rtf':>5}")
        lines = [hdr] + [f"{e:7} {v:36} {m['wer_pk']:6.3f} {m['wer_wh']:6.3f} "
                         f"{m['wer_pk_clean']:6.3f} {m['ovrl']:5.2f} {m['sig']:5.2f} "
                         f"{m['pesq']:5.2f} {m['s_per_w']:5.3f} {m['rtf']:5.3f}"
                         for e, v, m in summ]
        open(os.path.join(a.out, "summary.txt"), "w").write("\n".join(lines) + "\n")
        print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
