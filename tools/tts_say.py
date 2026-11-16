#!/usr/bin/env python3
"""Speak a phrase into a wav, for the speech hamlet sends to the UV-380 player and the drive
echo's "Received FSK" / "Received range" (Jeff 11-14). Three engines, all local, no system
install (docs/uv380-tts.md has the comparison after sotto 1200 and the setup):

    kokoro   Kokoro 82M (kokoro-onnx), the default: best words and sound after sotto 1200
    piper    Piper VITS voices (piper-tts)
    espeak   espeak-ng (espeakng-loader), the old robot voice, no model files

    python -m venv ~/blowfang-work/tts
    ~/blowfang-work/tts/bin/pip install -r tools/tts_requirements.txt
    sh tools/tts_models.sh ~/blowfang-work/tts/models
    ~/blowfang-work/tts/bin/python tools/tts_say.py "Received F S K." out.wav
    ... tools/tts_say.py --engine piper --voice en_US-lessac-high --rate 8000 "Hello." out.wav

Writes 16-bit mono at the engine's own rate (Kokoro 24000, Piper 22050 or 16000, espeak-ng
22050), or at --rate (8000 for the sotto 1200 encoder). Model files come from --models, else
$BLOWFANG_TTS_MODELS, else ~/blowfang-work/tts/models. SPDX-License-Identifier: MIT
"""
import argparse
import ctypes
import os
import sys
import wave
from math import gcd

import numpy as np

ENGINES = (
    "kokoro",
    "piper",
    "espeak",
)
DEFAULT_ENGINE = "kokoro"
DEFAULT_VOICE = {
    "kokoro": "af_heart",
    "piper": "en_US-lessac-high",
    "espeak": "en-us",
}
KOKORO_MODEL = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = "voices-v1.0.bin"


def models_dir(path=None):
    return os.path.expanduser(path or os.environ.get("BLOWFANG_TTS_MODELS")
                              or "~/blowfang-work/tts/models")


def _espeak(text, voice, speed):
    import espeakng_loader
    lib = ctypes.CDLL(espeakng_loader.get_library_path())
    rate = lib.espeak_Initialize(2, 0, espeakng_loader.get_data_path().encode(), 0)  # synchronous
    if rate <= 0:
        raise RuntimeError("espeak_Initialize failed")
    pcm = bytearray()
    cbtype = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int,
                              ctypes.c_void_p)

    def cb(wav, n, events):
        if wav and n > 0:
            pcm.extend(ctypes.string_at(wav, n * 2))
        return 0

    keep = cbtype(cb)
    lib.espeak_SetSynthCallback(keep)
    lib.espeak_SetVoiceByName(voice.encode())
    lib.espeak_SetParameter(1, int(140 * speed), 0)  # espeakRATE, words a minute
    raw = text.encode()
    lib.espeak_Synth(raw, len(raw) + 1, 0, 0, 0, 1, None, None)  # espeakCHARS_UTF8
    lib.espeak_Synchronize()
    return np.frombuffer(bytes(pcm), np.int16).astype(np.float32) / 32768, rate


_cache = {}


def _piper_file(voice, models):
    """a voice name (en_US-hfc_female-medium) -> its .onnx under models, either layout:
    models/NAME.onnx or models/vits-piper-NAME/NAME.onnx (sherpa-onnx's tarballs)"""
    for p in (os.path.join(models, voice + ".onnx"),
              os.path.join(models, "vits-piper-" + voice, voice + ".onnx")):
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no Piper voice {voice} under {models} (sh tools/tts_models.sh)")


def _piper(text, voice, speed, models, speaker=None):
    from piper import PiperVoice, SynthesisConfig
    path = _piper_file(voice, models)
    if path not in _cache:
        _cache[path] = PiperVoice.load(path)
    v = _cache[path]
    cfg = SynthesisConfig(speaker_id=speaker, length_scale=1.0 / speed)
    parts = []
    rate = v.config.sample_rate
    for chunk in v.synthesize(text, cfg):
        parts.append(np.frombuffer(chunk.audio_int16_bytes, np.int16))
        rate = chunk.sample_rate
    pcm = np.concatenate(parts) if parts else np.zeros(0, np.int16)
    return pcm.astype(np.float32) / 32768, rate


def _kokoro(text, voice, speed, models, model_file=KOKORO_MODEL):
    from kokoro_onnx import Kokoro
    key = ("kokoro", model_file)
    if key not in _cache:
        _cache[key] = Kokoro(os.path.join(models, model_file), os.path.join(models, KOKORO_VOICES))
    lang = "en-gb" if voice[:1] == "b" else "en-us"
    y, rate = _cache[key].create(text, voice=voice, speed=speed, lang=lang)
    return np.asarray(y, np.float32), rate


def have_models(engine, models=None):
    """are the default voice's files there (espeak needs none)"""
    d = models_dir(models)
    if engine == "kokoro":
        return all(os.path.exists(os.path.join(d, f)) for f in (KOKORO_MODEL, KOKORO_VOICES))
    if engine == "piper":
        try:
            return bool(_piper_file(DEFAULT_VOICE["piper"], d))
        except FileNotFoundError:
            return False
    return True


def synth(text, engine=DEFAULT_ENGINE, voice=None, speed=1.0, models=None, **kw):
    """text -> (float32 samples in -1..1, sample rate) at the engine's own rate"""
    voice = voice or DEFAULT_VOICE[engine]
    if engine == "espeak":
        return _espeak(text, voice, speed)
    if engine == "piper":
        return _piper(text, voice, speed, models_dir(models), kw.get("speaker"))
    if engine == "kokoro":
        return _kokoro(text, voice, speed, models_dir(models), kw.get("model_file", KOKORO_MODEL))
    raise ValueError(f"engine {engine}: one of {ENGINES}")


def resample(y, fs_in, fs_out):
    if fs_in == fs_out:
        return y
    from scipy.signal import resample_poly
    g = gcd(int(fs_in), int(fs_out))
    return resample_poly(y, fs_out // g, fs_in // g).astype(np.float32)


def to_int16(y, peak_dbfs=-1.0):
    """scale to a fixed peak (the engines' levels differ by 10 dB) and convert"""
    pk = float(np.max(np.abs(y))) if len(y) else 0.0
    if pk > 0:
        y = y * (10 ** (peak_dbfs / 20) / pk)
    return np.clip(np.round(y * 32767), -32768, 32767).astype(np.int16)


def write_wav(path, pcm, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())


def say(text, path, engine=DEFAULT_ENGINE, voice=None, speed=1.0, rate=None, models=None, **kw):
    """text -> 16-bit mono wav at path; returns (rate, samples)"""
    y, fs = synth(text, engine, voice, speed, models, **kw)
    if rate:
        y, fs = resample(y, fs, rate), rate
    pcm = to_int16(y)
    write_wav(path, pcm, fs)
    return fs, len(pcm)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("out")
    ap.add_argument("wpm", nargs="?", type=int, help="old form: espeak-ng words a minute (140)")
    ap.add_argument("--engine", choices=ENGINES, help=f"default {DEFAULT_ENGINE}, or espeak when "
                                                       "its model files are missing")
    ap.add_argument("--voice", help="kokoro af_heart, piper en_US-lessac-high, espeak en-us")
    ap.add_argument("--speed", type=float, default=1.0, help="1.0 = the voice's own pace")
    ap.add_argument("--rate", type=int, help="resample to this rate (8000 for sotto 1200)")
    ap.add_argument("--models", help="model folder (default $BLOWFANG_TTS_MODELS or "
                                     "~/blowfang-work/tts/models)")
    a = ap.parse_args(argv)
    engine, speed = a.engine or DEFAULT_ENGINE, a.speed
    if not a.engine and not a.voice and not have_models(engine, a.models):
        print(f"tts_say: no {engine} model under {models_dir(a.models)}, using espeak",
              file=sys.stderr)
        engine = "espeak"
    if a.wpm:  # tools/tts_say.py "text" out.wav 140, as the drive echo calls it
        engine, speed = "espeak", a.wpm / 140
    r, n = say(a.text, a.out, engine, a.voice, speed, a.rate, a.models)
    print(f"{a.out}: {n / r:.2f} s at {r} Hz ({engine} {a.voice or DEFAULT_VOICE[engine]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
